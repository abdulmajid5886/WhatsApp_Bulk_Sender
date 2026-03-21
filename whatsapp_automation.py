"""
WhatsApp Web automation module using Playwright.
Handles login, contact extraction, group member scraping, number validation,
and message sending via browser automation.
"""

import asyncio
import os
import time

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

    async def wait_for_login(self, timeout: int = 120):
        """
        Wait until the user scans the QR code and WhatsApp Web is ready.
        Returns True on success, False on timeout.
        """
        log_event("Waiting for WhatsApp Web login (scan QR code)…")
        try:
            # The side panel / search bar appears once logged in
            await self.page.wait_for_selector(
                'div[contenteditable="true"][data-tab="3"]',
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
            el = await self.page.query_selector(
                'div[contenteditable="true"][data-tab="3"]'
            )
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

    async def get_all_contacts(self) -> list[dict]:
        """
        Open the 'New Chat' pane and scrape the visible contact list.
        Returns list of dicts with 'name' and optionally 'phone'.
        """
        contacts = []
        try:
            log_event("Extracting WhatsApp contacts…")
            # Click the "New Chat" button
            new_chat_btn = await self.page.wait_for_selector(
                'div[title="New chat"]', timeout=10000
            )
            if new_chat_btn:
                await new_chat_btn.click()
                await self.page.wait_for_timeout(2000)

            # Scroll the contact list to load more
            contact_list = await self.page.query_selector(
                'div[aria-label="Contact list"]'
            )
            if not contact_list:
                # Fallback selector
                contact_list = await self.page.query_selector(
                    'div[data-tab="4"]'
                )

            if contact_list:
                # Scroll multiple times to load contacts
                for _ in range(20):
                    await contact_list.evaluate("el => el.scrollTop = el.scrollHeight")
                    await self.page.wait_for_timeout(500)

            # Scrape contact entries
            items = await self.page.query_selector_all(
                'div[class*="contact"] span[title]'
            )
            seen = set()
            for item in items:
                name = await item.get_attribute("title")
                if name and name not in seen:
                    seen.add(name)
                    contacts.append({"name": name, "phone": ""})

            # Close the new-chat pane
            await self.page.keyboard.press("Escape")
            log_event(f"Extracted {len(contacts)} WhatsApp contacts.")
        except Exception as exc:
            handle_error(exc, "Failed to extract WhatsApp contacts")
        return contacts

    async def get_group_members(self, group_name: str) -> list[dict]:
        """
        Navigate to a group chat and scrape the member list.
        Returns list of dicts with 'name' and optionally 'phone'.
        """
        members = []
        try:
            log_event(f"Extracting members from group: {group_name}")
            # Search for the group
            search_box = await self.page.wait_for_selector(
                'div[contenteditable="true"][data-tab="3"]', timeout=10000
            )
            await search_box.click()
            await search_box.fill("")
            await self.page.keyboard.type(group_name, delay=50)
            await self.page.wait_for_timeout(2000)

            # Click the group in search results
            group_el = await self.page.query_selector(
                f'span[title="{group_name}"]'
            )
            if not group_el:
                log_event(f"Group '{group_name}' not found.", "warning")
                await self.page.keyboard.press("Escape")
                return members
            await group_el.click()
            await self.page.wait_for_timeout(2000)

            # Click group header to open info pane
            header = await self.page.query_selector(
                'header span[title]'
            )
            if header:
                await header.click()
                await self.page.wait_for_timeout(2000)

            # Scrape members
            member_els = await self.page.query_selector_all(
                'div[data-testid="cell-frame-container"] span[title]'
            )
            seen = set()
            for el in member_els:
                name = await el.get_attribute("title")
                if name and name not in seen and name != group_name:
                    seen.add(name)
                    # If the title looks like a phone number, use it
                    phone = sanitize_phone(name) if name.startswith("+") else ""
                    members.append({"name": name, "phone": phone})

            # Close info pane
            await self.page.keyboard.press("Escape")
            await self.page.keyboard.press("Escape")
            log_event(f"Extracted {len(members)} members from group '{group_name}'.")
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
            # If the number is invalid, WhatsApp shows an "invalid" popup
            invalid = await self.page.query_selector(
                'div[data-testid="popup-contents"]'
            )
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
            invalid = await self.page.query_selector(
                'div[data-testid="popup-contents"]'
            )
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
                    'div[contenteditable="true"][data-tab="10"]',
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
