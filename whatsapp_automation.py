"""
WhatsApp Web automation module using Playwright.
Handles login, contact extraction, group member scraping, number validation,
and message sending via browser automation.
"""

import asyncio
import os
import time

import shutil
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from utils import log_event, handle_error, sanitize_phone


# Persistent user-data directory so the QR session is remembered
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wa_session")


class WhatsAppBot:
    """Playwright-based WhatsApp Web automation."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, headless: bool = False):
        """Launch Chromium and navigate to WhatsApp Web."""
        log_event("Starting WhatsApp Web bot…")
        self._clear_stale_lock()
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=headless,
            channel="chromium",
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self.page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        self._running = True
        log_event("WhatsApp Web page loaded.")

    def _clear_stale_lock(self):
        """
        Playwright/Chromium may leave a 'SingletonLock' file if it crashes.
        This prevents subsequent launches. We try to remove it proactively.
        """
        lock_file = os.path.join(USER_DATA_DIR, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                log_event("Removing stale WhatsApp session lock file…")
                # On macOS/Linux, SingletonLock is often a symlink
                if os.path.islink(lock_file):
                    os.unlink(lock_file)
                else:
                    os.remove(lock_file)
            except Exception as exc:
                log_event(f"Could not remove SingletonLock: {exc}", "warning")

    async def wait_for_login(self, timeout: int = 120):
        """
        Wait until the user scans the QR code and WhatsApp Web is ready.
        Returns True on success, False on timeout.
        """
        log_event("Waiting for WhatsApp Web login (scan QR code)…")
        try:
            # After login, the chat list pane or search bar appears
            await self.page.wait_for_selector(
                'div#pane-side, input[aria-label="Search or start a new chat"]',
                timeout=timeout * 1000,
            )
            log_event("WhatsApp Web login successful.")
            return True
        except Exception:
            log_event("WhatsApp Web login timed out.", "warning")
            return False

    async def is_logged_in(self) -> bool:
        """Check if WhatsApp Web session is active."""
        try:
            el = await self.page.query_selector('div#pane-side')
            return el is not None
        except Exception:
            return False

    async def close(self):
        """Shut down the browser."""
        self._running = False
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            handle_error(exc, "Error closing WhatsApp bot")
        log_event("WhatsApp bot closed.")

    # ------------------------------------------------------------------
    # Contact extraction
    # ------------------------------------------------------------------

    async def get_all_contacts(self, progress_callback=None) -> list[dict]:
        """
        Open the 'New Chat' pane and scrape the visible contact list incrementally.
        progress_callback: optional callable(str) to report progress messages.
        Returns list of dicts with 'name' and optionally 'phone'.
        """
        contacts = []
        def _report(msg):
            log_event(msg)
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass

        try:
            _report("Extracting WhatsApp contacts…")
            # Click the "New chat" button
            new_chat_btn = await self.page.wait_for_selector(
                'button[aria-label="New chat"]', timeout=15000
            )
            if not new_chat_btn:
                _report("New chat button not found.")
                return contacts

            await new_chat_btn.click()
            await self.page.wait_for_timeout(2000)

            # Find the scrollable container in the side pane
            container = await self.page.wait_for_selector(
                'div[aria-label="Contact list"], div[data-tab="4"], [role="grid"]',
                timeout=10000
            )

            if not container:
                _report("Contact list container not found.")
                await self.page.keyboard.press("Escape")
                return contacts

            seen_names = set()
            last_count = 0
            max_scrolls_without_new = 8
            scrolls_without_new = 0

            _report("Scrolling through contact list…")

            # Incremental collection loop
            for i in range(200):  # Safety limit
                items = await self.page.query_selector_all('div[role="listitem"]')

                for item in items:
                    # FILTER: Skip groups
                    is_group = await item.query_selector('[data-testid*="group"]')
                    if is_group:
                        continue

                    name_el = await item.query_selector('span[title]')
                    if name_el:
                        name = await name_el.get_attribute("title")
                        if name and name not in seen_names and name not in ["New group", "New community", "New contact"]:
                            seen_names.add(name)
                            contacts.append({"name": name, "phone": ""})

                # Report progress
                current_count = len(seen_names)
                if current_count > last_count:
                    _report(f"Found {current_count} contacts so far…")
                    last_count = current_count
                    scrolls_without_new = 0
                else:
                    scrolls_without_new += 1

                if scrolls_without_new >= max_scrolls_without_new:
                    _report("Reached end of contact list.")
                    break

                # Scroll using mouse wheel to trigger lazy-loading
                box = await container.bounding_box()
                if box:
                    await self.page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    await self.page.mouse.wheel(0, 800)
                    await self.page.wait_for_timeout(1200)

            await self.page.keyboard.press("Escape")
            _report(f"Done! Extracted {len(contacts)} unique contacts.")

        except Exception as exc:
            handle_error(exc, "Failed to extract WhatsApp contacts")
        return contacts

    async def get_all_groups(self, progress_callback=None) -> list[str]:
        """
        Scroll through the main chat sidebar and identify group chats.
        Returns a list of group names.
        """
        groups = []
        def _report(msg):
            log_event(msg)
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass

        try:
            _report("Scanning sidebar for groups…")
            # Make sure we're on the main page
            await self.page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
            await self.page.wait_for_selector('div#pane-side', timeout=15000)
            await self.page.wait_for_timeout(2000)

            pane = await self.page.query_selector('div#pane-side')
            if not pane:
                _report("Chat list pane not found.")
                return groups

            seen = set()
            last_count = 0
            scrolls_without_new = 0

            for _ in range(100):
                # Get all chat items currently visible
                chat_items = await pane.query_selector_all('div[role="listitem"]')
                for item in chat_items:
                    # Groups have a group icon
                    is_group = await item.query_selector('[data-testid*="group"], [data-icon="default-group"]')
                    if is_group:
                        name_el = await item.query_selector('span[title]')
                        if name_el:
                            name = await name_el.get_attribute("title")
                            if name and name not in seen:
                                seen.add(name)
                                groups.append(name)

                if len(seen) > last_count:
                    _report(f"Found {len(seen)} groups so far…")
                    last_count = len(seen)
                    scrolls_without_new = 0
                else:
                    scrolls_without_new += 1

                if scrolls_without_new >= 5:
                    break

                box = await pane.bounding_box()
                if box:
                    await self.page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    await self.page.mouse.wheel(0, 600)
                    await self.page.wait_for_timeout(1000)

            _report(f"Done! Found {len(groups)} groups.")
        except Exception as exc:
            handle_error(exc, "Failed to scan groups")
        return groups

    async def get_group_members(self, group_name: str) -> list[dict]:
        """
        Navigate to a group chat and scrape the member list from the info drawer.
        """
        members = []
        try:
            log_event(f"Extracting members from group: {group_name}")
            # Search for the group
            search_box = await self.page.wait_for_selector(
                'input[aria-label="Search or start a new chat"]', timeout=10000
            )
            await search_box.click()
            await search_box.fill("")
            await self.page.keyboard.type(group_name, delay=50)
            await self.page.wait_for_timeout(2000)

            # Click the group in search results
            group_el = await self.page.query_selector(f'span[title="{group_name}"]')
            if not group_el:
                log_event(f"Group '{group_name}' not found.", "warning")
                await self.page.keyboard.press("Escape")
                return members
            await group_el.click()
            await self.page.wait_for_timeout(2000)

            # Click group header to open info pane
            header = await self.page.query_selector('header span[title]')
            if header:
                await header.click()
                await self.page.wait_for_timeout(2000)

            # Find the scrollable member list in the info drawer
            drawer = await self.page.wait_for_selector('div[data-testid="group-info-drawer"], [role="region"]', timeout=10000)
            if not drawer:
                log_event("Group info drawer not found.", "error")
                return members

            seen_names = set()
            last_count = 0
            scrolls_without_new = 0

            # Incremental collection for group members
            for i in range(100):
                # Search for member cells specifically in the drawer
                member_els = await drawer.query_selector_all('div[data-testid="cell-frame-container"] span[title]')
                
                for el in member_els:
                    name = await el.get_attribute("title")
                    if name and name not in seen_names and name != group_name:
                        seen_names.add(name)
                        # Identify phone number if it's in the title
                        phone = sanitize_phone(name) if name.startswith("+") else ""
                        members.append({"name": name, "phone": phone})

                if len(seen_names) > last_count:
                    last_count = len(seen_names)
                    scrolls_without_new = 0
                else:
                    scrolls_without_new += 1
                
                if scrolls_without_new >= 5:
                    break

                # Scroll the drawer
                await drawer.evaluate("el => el.querySelector('div[style*=\"overflow-y: auto\"]')?.scrollBy(0, 500)")
                await self.page.wait_for_timeout(1000)

            # Close info pane
            await self.page.keyboard.press("Escape")
            await self.page.keyboard.press("Escape")
            log_event(f"Extracted {len(members)} unique members from group '{group_name}'.")
        except Exception as exc:
            handle_error(exc, f"Failed to extract members from group '{group_name}'")
        return members

    # ------------------------------------------------------------------
    # Number validation
    # ------------------------------------------------------------------

    async def check_number_on_whatsapp(self, phone: str) -> bool:
        """
        Check if a phone number is registered on WhatsApp by navigating
        to the direct chat URL.  Returns True if the chat opens.
        """
        clean = sanitize_phone(phone)
        if not clean:
            return False
        digits = clean.lstrip("+")
        url = f"https://web.whatsapp.com/send?phone={digits}"
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(5000)
            # If the number is invalid, WhatsApp shows a "Phone number shared via url is invalid" popup
            invalid = await self.page.query_selector('div[role="dialog"]')
            if invalid:
                log_event(f"Number {clean} is NOT on WhatsApp.", "info")
                ok_btn = await self.page.query_selector(
                    'div[data-testid="popup-controls"] button'
                )
                if ok_btn:
                    await ok_btn.click()
                return False
            log_event(f"Number {clean} appears to be on WhatsApp.", "info")
            return True
        except Exception as exc:
            handle_error(exc, f"Error checking number {clean}")
            return False

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    async def send_message(
        self, phone: str, text: str, media_path: str | None = None
    ) -> bool:
        """
        Send a text (and optionally a media file) to a phone number.
        Uses the direct URL approach: web.whatsapp.com/send?phone=...
        Returns True on success.
        """
        clean = sanitize_phone(phone)
        if not clean:
            log_event(f"Invalid phone number: {phone}", "warning")
            return False

        digits = clean.lstrip("+")
        url = f"https://web.whatsapp.com/send?phone={digits}"
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(5000)

            # Check for invalid number popup
            invalid = await self.page.query_selector('div[role="dialog"]')
            if invalid:
                log_event(f"Cannot send to {clean} — not on WhatsApp.", "warning")
                ok_btn = await self.page.query_selector(
                    'div[data-testid="popup-controls"] button'
                )
                if ok_btn:
                    await ok_btn.click()
                return False

            # Attach media if provided
            if media_path and os.path.isfile(media_path):
                await self._attach_media(media_path)

            # Type and send text message
            if text:
                msg_box = await self.page.wait_for_selector(
                    'div[contenteditable="true"][role="textbox"]',
                    timeout=15000,
                )
                await msg_box.click()
                await msg_box.fill("")
                # Type with slight delay to seem human-like
                await self.page.keyboard.type(text, delay=20)
                await self.page.wait_for_timeout(500)

            # Press Send
            send_btn = await self.page.query_selector(
                'button[data-testid="compose-btn-send"]'
            )
            if send_btn:
                await send_btn.click()
            else:
                await self.page.keyboard.press("Enter")

            await self.page.wait_for_timeout(2000)
            log_event(f"Message sent to {clean}.")
            return True

        except Exception as exc:
            handle_error(exc, f"Failed to send message to {clean}")
            return False

    async def _attach_media(self, media_path: str):
        """Attach a media file to the current chat."""
        try:
            # Click the attachment (paperclip) button
            attach_btn = await self.page.wait_for_selector(
                'div[title="Attach"]', timeout=10000
            )
            if not attach_btn:
                attach_btn = await self.page.query_selector(
                    'button[data-testid="clip"]'
                )
            if attach_btn:
                await attach_btn.click()
                await self.page.wait_for_timeout(1000)

            # Use the file input for images/videos/documents
            file_input = await self.page.query_selector(
                'input[accept*="image"]'
            )
            if not file_input:
                file_input = await self.page.query_selector(
                    'input[type="file"]'
                )
            if file_input:
                await file_input.set_input_files(media_path)
                await self.page.wait_for_timeout(3000)
                log_event(f"Media attached: {media_path}")
                # Click send on the media preview
                send_btn = await self.page.query_selector(
                    'div[data-testid="media-caption-send-btn"]'
                )
                if not send_btn:
                    send_btn = await self.page.query_selector(
                        'span[data-testid="send"]'
                    )
                if send_btn:
                    await send_btn.click()
                    await self.page.wait_for_timeout(2000)
        except Exception as exc:
            handle_error(exc, "Failed to attach media")


# ---------------------------------------------------------------------------
# Synchronous helpers (for use from non-async code / GUI threads)
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine from synchronous code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
