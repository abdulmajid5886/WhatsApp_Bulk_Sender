"""
WhatsApp Web automation module using Playwright.
Handles login, contact extraction, group member scraping, number validation,
and message sending via browser automation.
"""

import asyncio
import os
import time
import shutil
import re
from playwright.async_api import async_playwright, Browser, Page, BrowserContext, ElementHandle

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
        self._running: bool = False

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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1440,900"
            ],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
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
        if not self.page:
            return False
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
        if not self.page:
            return False
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
        Scrape WhatsApp contacts from two sources:
        1. Main chat list (contains recent chats, including unsaved numbers)
        2. 'New Chat' pane (contains all saved contacts)
        progress_callback: optional callable(str) to report progress messages.
        Returns list of dicts with 'name' and optionally 'phone'.
        """
        contacts: list[dict] = []
        seen_names: set[str] = set()

        def _report(msg):
            log_event(msg)
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass

        def _add_contact(name: str):
            if name and name not in seen_names and name not in ["New group", "New community", "New contact", "Archived", "Starred messages"]:
                seen_names.add(name)
                # If name looks like a phone number, extract it.
                phone = ""
                # WhatsApp often displays unsaved numbers with a leading '+' or just as formatted digits
                if re.search(r'[\+\d]', name):
                    cleaned_phone = sanitize_phone(name)
                    if cleaned_phone:
                        phone = cleaned_phone
                contacts.append({"name": name, "phone": phone})
                return True
            return False

        if not self.page:
            _report("WhatsApp is not initialized.")
            return contacts

        try:
            # --- PHASE 1: Main Chat List ---
            _report("DEBUG: Starting PHASE 1")
            _report("Extracting contacts from recent chats (includes unsaved numbers)…")
            
            # Ensure we are on the main view by pressing Escape
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
            
            _report("DEBUG: Waiting for pane-side selector")
            pane = await self.page.wait_for_selector('div#pane-side', timeout=15000)
            _report("DEBUG: pane-side selector found")
            
            if pane:
                last_count = 0
                scrolls_without_new = 0
                max_scrolls_without_new = 30
                
                # Infinite scroll for older chats. Will break when we reach the very bottom.
                for _ in range(2000): 
                    _report(f"DEBUG: Scroll iteration {_}")
                    # Use more robust selectors for chat items
                    chat_items: list[ElementHandle] = await pane.query_selector_all(
                        'div[role="listitem"], div[role="gridcell"], div[data-testid="cell-frame-container"]'
                    )
                    _report(f"DEBUG: Found {len(chat_items)} chat items in current view")
                    for item in chat_items:
                        # Skip groups: check several indicators
                        is_group = await item.query_selector('[data-testid*="group"], [data-icon="default-group"], [data-icon="business-group"]')
                        if is_group:
                            continue
                        
                        name_el = await item.query_selector('span[title]')
                        if name_el:
                            name = await name_el.get_attribute("title")
                            _add_contact(str(name))
                    
                    current_count = len(seen_names)
                    if current_count > last_count:
                        # Report more frequently initially for better feedback
                        if current_count < 20 or (current_count - last_count) >= 5:
                            _report(f"Found {current_count} contacts so far…")
                        last_count = current_count
                        scrolls_without_new = 0
                    else:
                        scrolls_without_new += 1
                        
                    if scrolls_without_new >= max_scrolls_without_new:
                        _report("Reached the bottom of the chat list or no new chats found recently.")
                        break
                        
                    if chat_items:
                        try:
                            await chat_items[-1].scroll_into_view_if_needed()
                        except Exception:
                            pass
                    
                    await self.page.wait_for_timeout(1500)

            # --- PHASE 2: New Chat Pane (Saved Contacts) ---
            _report("DEBUG: Starting PHASE 2")
            _report("Extracting saved contacts from address book…")
            
            _report("DEBUG: Waiting for New chat button")
            new_chat_btn = await self.page.wait_for_selector('button[aria-label="New chat"]', timeout=10000)
            
            if new_chat_btn:
                _report("DEBUG: Clicking New chat button")
                await new_chat_btn.click()
                await self.page.wait_for_timeout(2000)
                
                _report("DEBUG: Waiting for contact list container")
                container = await self.page.wait_for_selector(
                    'div[aria-label="Contact list"], div[data-tab="4"], [role="grid"], div#pane-side',
                    timeout=10000
                )
                _report("DEBUG: Contact list container found")
                
                if container:
                    last_count = len(seen_names)
                    max_scrolls_without_new = 8
                    scrolls_without_new = 0

                    for i in range(250):
                        _report(f"DEBUG: New Chat scroll iteration {i}")
                        items: list[ElementHandle] = await self.page.query_selector_all(
                            'div[role="listitem"], div[role="gridcell"], div[data-testid="cell-frame-container"]'
                        )
                        for item in items:
                            # FILTER: Skip groups
                            is_group = await item.query_selector('[data-testid*="group"]')
                            if is_group:
                                continue

                            name_el = await item.query_selector('span[title]')
                            if name_el:
                                name = await name_el.get_attribute("title")
                                _add_contact(str(name))

                        current_count = len(seen_names)
                        if current_count > last_count:
                            if (current_count - last_count) >= 10:
                                _report(f"Found {current_count} total contacts…")
                            last_count = current_count
                            scrolls_without_new = 0
                        else:
                            scrolls_without_new += 1

                        if scrolls_without_new >= max_scrolls_without_new:
                            break

                        if items:
                            try:
                                await items[-1].scroll_into_view_if_needed()
                            except Exception:
                                pass
                        await self.page.wait_for_timeout(1500)

                await self.page.keyboard.press("Escape")
            
            _report(f"✅ Done! Extracted {len(contacts)} unique contacts.")

        except BaseException as exc:
            _report(f"DEBUG Exception occurred: {repr(exc)}")
            handle_error(Exception(str(exc)), f"Failed to extract WhatsApp contacts. Details: {repr(exc)}")
        finally:
            _report("DEBUG: Exiting get_all_contacts method")
        return contacts

    async def get_all_groups(self, progress_callback=None) -> list[str]:
        """
        Scroll through the main chat sidebar and identify group chats.
        Returns a list of group names.
        """
        groups: list[str] = []
        seen: set[str] = set()

        def _report(msg):
            log_event(msg)
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass

        if not self.page:
            _report("WhatsApp is not initialized.")
            return groups

        try:
            _report("Scanning sidebar for groups…")
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
            
            await self.page.wait_for_selector('div#pane-side', timeout=15000)
            await self.page.wait_for_timeout(2000)

            pane = await self.page.query_selector('div#pane-side')
            if not pane:
                _report("Chat list pane not found.")
                return groups

            # Try to click the "Groups" filter button to isolate only groups
            try:
                groups_btn = await self.page.query_selector('button:has-text("Groups"), span:has-text("Groups")')
                if groups_btn:
                    await groups_btn.click()
                    await self.page.wait_for_timeout(1500)
            except Exception:
                pass

            last_count = 0
            scrolls_without_new = 0

            for _ in range(100):
                chat_items: list[ElementHandle] = await pane.query_selector_all(
                    'div[role="listitem"], div[role="gridcell"], div[data-testid="cell-frame-container"]'
                )
                for item in chat_items:
                    # In Groups tab, virtually all items are groups or broadcasts.
                    name_el = await item.query_selector('span[title]')
                    if name_el:
                        name = await name_el.get_attribute("title")
                        if name and name not in seen and name not in ["Archived", "New group"]:
                            seen.add(str(name))
                            groups.append(str(name))

                if len(seen) > last_count:
                    _report(f"Found {len(seen)} groups so far…")
                    last_count = len(seen)
                    scrolls_without_new = 0
                else:
                    scrolls_without_new += 1

                if scrolls_without_new >= 6:
                    break

                box = await pane.bounding_box()
                if box:
                    await self.page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    await self.page.mouse.wheel(0, 600)
                    await self.page.wait_for_timeout(1000)

            # Click "All" filter to reset sidebar
            try:
                all_btn = await self.page.query_selector('button:has-text("All"), span:has-text("All")')
                if all_btn:
                    await all_btn.click()
            except Exception:
                pass

            _report(f"Done! Found {len(groups)} groups.")
        except Exception as exc:
            handle_error(exc, "Failed to scan groups")
        return groups

    async def get_group_members(self, group_name: str) -> list[dict]:
        """
        Navigate to a group chat and scrape the member list from the info drawer.
        """
        members: list[dict] = []
        if not self.page:
            return members

        try:
            log_event(f"Extracting members from group: {group_name}")
            
            # Reset UI state completely
            for _ in range(4):
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(200)

            # Search for the group using varied selectors, as WhatsApp changes aria labels occasionally
            search_box = await self.page.wait_for_selector(
                'input[aria-label="Search or start a new chat"], input[aria-label*="Search"], div[title="Search input textbox"]',
                timeout=10000
            )
            if not search_box:
                return members

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

            # Click group header to open info pane. Must use 'div#main header' to avoid clicking the user profile header.
            header = await self.page.query_selector(f'div#main header, header:has-text("{group_name}")')
            if header:
                await header.click()
                await self.page.wait_for_timeout(3000)
                
                # DEBUG: Take a screenshot to see if the drawer actually opened correctly
                try:
                    await self.page.screenshot(path='/tmp/whatsapp_drawer_debug.png')
                    html_content = await self.page.content()
                    with open('/tmp/whatsapp_drawer_debug.html', 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    log_event("DEBUG: Saved screenshot to /tmp/whatsapp_drawer_debug.png", "info")
                except Exception:
                    pass
                
            # 1. Look for the "Group · X members" header or button and click it to reveal/expand
            # We add extra wait because of the "micro-refresh" or layout shift described by user
            await self.page.wait_for_timeout(3000)
            try:
                # Look for the members count header (e.g., "119 members")
                members_header = await self.page.query_selector('span:has-text("members"), div[aria-label*="members"][role="button"], button:has-text("members"), div:has-text("Group · ")')
                if members_header is not None:
                    log_event("Clicking members header to reveal list...")
                    await members_header.click()
                    await self.page.wait_for_timeout(3000) # Settle after header click
            except Exception:
                pass

            # 2. Look for "View all" button which may now be visible
            # It might appear right away or after a small delay
            target_btn = None
            for _ in range(10): # Poll for 10 seconds
                try:
                    target_btn = await self.page.query_selector('div[role="button"]:has-text("View all"), span:has-text("View all"), div:has-text("View all")[role="button"], div[aria-label*="View all"], span:has-text("more ")')
                    if target_btn is not None:
                        break
                except Exception:
                    pass
                await self.page.wait_for_timeout(1000)

            if target_btn is not None:
                try:
                    btn_text = await target_btn.inner_text()
                    log_event(f"Clicking 'View all' button: {btn_text}...")
                    await target_btn.click()
                    await self.page.wait_for_timeout(5000) # Give modal plenty of time to hydrate
                except Exception:
                    pass

            # 3. Native PageDown scrolling to trigger virtualization within the resulting modal/drawer
            # We use PageDown because it is unconditionally human and triggers the scroll triggers.
            js_scroll_drawer = """
            () => {
                const targets = [
                    document.querySelector('div[role="dialog"]'),
                    document.querySelector('div[data-testid="group-info-drawer"]'),
                    document.querySelector('[role="region"]')
                ];
                for (const target of targets) {
                    if (target) {
                        const divs = target.querySelectorAll('div');
                        for (const div of divs) {
                            const style = window.getComputedStyle(div);
                            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && div.scrollHeight > div.clientHeight) {
                                div.scrollTop += 1500;
                                return true;
                            }
                        }
                    }
                }
                return false;
            }
            """
            for _ in range(5):
                await self.page.evaluate(js_scroll_drawer)
                await self.page.wait_for_timeout(1000)

            # The full list is either in a dialog popup or in the right-hand panel
            dialog = await self.page.query_selector('div[role="dialog"]')
            if dialog:
                container = dialog
            else:
                # Target the drawer directly so we don't accidentally scrape the Left chat histories
                container = await self.page.query_selector('div[data-testid="group-info-drawer"]') or \
                            await self.page.query_selector('[role="region"]') or self.page

            seen_names = set()
            last_count = 0
            scrolls_without_new = 0

            # Incremental collection for group members. Loops until no new members are found.
            for i in range(500):
                # Search for all contact cells
                member_els: list[ElementHandle] = await container.query_selector_all(
                    'div[role="listitem"], div[role="gridcell"], div[data-testid="cell-frame-container"], div[role="row"], div[role="button"]'
                )
                
                if i == 0 and not member_els:
                    log_event("DEBUG: No member elements found in container. Retrying with loose selectors...", "info")
                    member_els = await container.query_selector_all('div[role="button"]')
                
                for item in member_els:
                    # Advanced name finding: check title, then aria-label, then first span text
                    name = ""
                    name_el = await item.query_selector('span[title], span[dir="auto"], div[title]')
                    if name_el:
                        name = await name_el.get_attribute("title") or await name_el.inner_text()
                    
                    if not name:
                        name = await item.get_attribute("aria-label") or ""
                        # If aria-label is just "Contact info", skip
                        if name == "Contact info": name = ""
                        if name and name not in seen_names and name != group_name and not name.startswith("View all"):
                            name_str = str(name)
                            seen_names.add(name_str)
                            
                            phone = ""
                            # Ultimate fallback: search the entire row's text for a phone number
                            try:
                                full_text = await item.inner_text()
                                # WhatsApp secretly injects invisible Unicode BiDi control chars. We MUST strip them!
                                clean_text = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', full_text)
                                # Matches +123 456 7890, 0300-1234567, etc.
                                phone_match = re.search(r'\+?\d[\d\s\-\(\)]{7,20}\d', clean_text)
                                if phone_match:
                                    potential_phone = phone_match.group(0)
                                    # Ensure it has enough digits to be a real phone number
                                    if len(re.sub(r'\D', '', potential_phone)) >= 8:
                                        phone = sanitize_phone(potential_phone)
                            except Exception:
                                pass
                                
                            # If regex failed, check if the Name itself is a phone number
                            if not phone and ("+" in name_str or re.search(r'\d{5}', name_str)):
                                phone = sanitize_phone(name_str)
                            
                            members.append({"name": name_str, "phone": phone})

                if len(seen_names) > last_count:
                    if len(seen_names) % 10 == 0 or len(seen_names) - last_count > 5:
                        log_event(f"Extracted {len(seen_names)} members so far…")
                    last_count = len(seen_names)
                    scrolls_without_new = 0
                else:
                    scrolls_without_new += 1
                
                if scrolls_without_new >= 15:
                    break

                # Reliably scroll the active container using our dynamic JS script
                await self.page.evaluate(js_scroll_drawer)
                await self.page.wait_for_timeout(800)

            if len(members) == 0:
                log_event("Structural extraction failed. Attempting global text-based scraping...", "info")
                try:
                    all_text = await container.inner_text()
                    # Strip BiDi characters
                    clean_all = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', all_text)
                    # Find all phone number patterns
                    potential_phones = re.findall(r'\+?\d[\d\s\-\(\)]{8,20}\d', clean_all)
                    for p in set(potential_phones):
                        digits = re.sub(r'\D', '', p)
                        if len(digits) >= 8:
                            members.append({"name": p, "phone": sanitize_phone(p)})
                except Exception:
                    pass

            # Close info panes by hitting Escape
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(500)
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
        if not self.page:
            return False
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
                ok_btn = await self.page.query_selector('div[data-testid="popup-controls"] button')
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

    async def send_message(self, phone: str, text: str, media_path: str | None = None) -> bool:
        """
        Send a text (and optionally a media file) to a phone number.
        Uses the direct URL approach: web.whatsapp.com/send?phone=...
        Returns True on success.
        """
        if not self.page:
            return False
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
                ok_btn = await self.page.query_selector('div[data-testid="popup-controls"] button')
                if ok_btn:
                    await ok_btn.click()
                return False

            # Attach media if provided
            if media_path and os.path.isfile(media_path):
                await self._attach_media(media_path)

            # Type and send text message
            if text:
                msg_box = await self.page.wait_for_selector('div[contenteditable="true"][role="textbox"]', timeout=15000)
                if msg_box:
                    await msg_box.click()
                    await msg_box.fill("")
                    await self.page.keyboard.type(text, delay=20)
                    await self.page.wait_for_timeout(500)

            # Press Send
            send_btn = await self.page.query_selector('button[data-testid="compose-btn-send"]')
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
        if not self.page:
            return
        try:
            attach_btn = await self.page.wait_for_selector('div[title="Attach"], button[data-testid="clip"]', timeout=10000)
            if attach_btn:
                await attach_btn.click()
                await self.page.wait_for_timeout(1000)

            file_input = await self.page.query_selector('input[accept*="image"], input[type="file"]')
            if file_input:
                await file_input.set_input_files(media_path)
                await self.page.wait_for_timeout(3000)
                log_event(f"Media attached: {media_path}")
                
                send_btn = await self.page.query_selector('div[data-testid="media-caption-send-btn"], span[data-testid="send"]')
                if send_btn:
                    await send_btn.click()
                    await self.page.wait_for_timeout(2000)
        except Exception as exc:
            handle_error(exc, "Failed to attach media")
