# WhatsApp Bulk Sender — Full Implementation Plan

Build a fully functional Python GUI application that automates WhatsApp bulk messaging. The current codebase has 5 stub files with no real implementation — every function is a `TODO` placeholder.

## User Review Required

> [!CAUTION]
> WhatsApp Web automation is unofficial and may violate WhatsApp's Terms of Service. The user accepts this risk per the plan.

> [!IMPORTANT]
> **Google Contacts OAuth** requires a `credentials.json` file from the Google Cloud Console with the People API enabled. You must provide this file in the project root before that feature works.

> [!IMPORTANT]
> **Playwright** is chosen over Selenium for robustness. On first run, `playwright install chromium` must be executed to download the browser binary.

## Proposed Changes

### Dependency Setup

#### [NEW] [requirements.txt](file:///Volumes/Personal/Github/whatsApp_sender/requirements.txt)
Pin all required packages:
- `PyQt5` — GUI framework
- `playwright` — browser automation for WhatsApp Web
- `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib` — Google Contacts API
- `Pillow` — image/media handling

---

### Utilities Module

#### [MODIFY] [utils.py](file:///Volumes/Personal/Github/whatsApp_sender/utils.py)
- File-based + console logging with `logging` module
- [log_event()](file:///Volumes/Personal/Github/whatsApp_sender/utils.py#8-11) — log info/warning/error with timestamps to `app.log`
- [handle_error()](file:///Volumes/Personal/Github/whatsApp_sender/utils.py#12-15) — log exception + return user-friendly message
- `sanitize_phone()` — normalize phone numbers (strip spaces, dashes, ensure `+` prefix)
- `export_contacts_csv()` / `import_contacts_csv()` — CSV import/export for contact lists

---

### Google Contacts Module

#### [MODIFY] [google_contacts.py](file:///Volumes/Personal/Github/whatsApp_sender/google_contacts.py)
- Keep the existing [authenticate_google()](file:///Volumes/Personal/Github/whatsApp_sender/google_contacts.py#8-39) OAuth2 flow (already mostly complete)
- Implement [fetch_google_contacts()](file:///Volumes/Personal/Github/whatsApp_sender/google_contacts.py#40-43):
  - Uses the People API to list all contacts with phone numbers
  - Returns a list of dicts `[{"name": "...", "phone": "+1234567890"}, ...]`
  - Calls `sanitize_phone()` from utils

---

### WhatsApp Automation Module

#### [MODIFY] [whatsapp_automation.py](file:///Volumes/Personal/Github/whatsApp_sender/whatsapp_automation.py)
Full rewrite using **Playwright (async)**:
- `WhatsAppBot` class with lifecycle management:
  - `start()` — launch Chromium with persistent user-data dir (so QR scan is remembered)
  - `wait_for_login()` — wait until the main WhatsApp Web interface loads (search bar visible)
  - `is_logged_in()` — check session status
  - `get_all_contacts()` — open "New Chat" pane → scroll and scrape contact list
  - `get_group_members(group_name)` — navigate to group → open info → scrape member list
  - `check_number_on_whatsapp(phone)` — search for number in new-chat search → check if a chat opens
  - [send_message(phone, text, media_path=None)](file:///Volumes/Personal/Github/whatsApp_sender/messaging.py#11-14) — open chat with phone via `https://web.whatsapp.com/send?phone=...`, type text, optionally attach media, send
  - `close()` — shutdown browser

---

### Messaging Module

#### [MODIFY] [messaging.py](file:///Volumes/Personal/Github/whatsApp_sender/messaging.py)
- `BulkSender` class:
  - Accepts a `WhatsAppBot` instance, a contact list, message text, optional media path
  - Iterates contacts → validates → sends → random delay (10–30s)
  - Emits `PyQt5` signals for progress updates (current contact, success/fail, ETA)
  - Runs in a `QThread` to keep GUI responsive
  - `pause()` / `resume()` / `stop()` lifecycle controls
- Keep existing [random_delay()](file:///Volumes/Personal/Github/whatsApp_sender/messaging.py#15-19) function

---

### Main GUI Application

#### [MODIFY] [main.py](file:///Volumes/Personal/Github/whatsApp_sender/main.py)
Full PyQt5 GUI with a tabbed interface:

**Tab 1 — Authentication & Setup**
- "Connect Google" button → triggers OAuth2 flow → shows connected status
- WhatsApp Web status indicator (connected/disconnected)
- "Open WhatsApp Web" button → launches Playwright browser for QR scan
- Status labels for both services

**Tab 2 — Contacts**
- Source selector: Google Contacts / WhatsApp Contacts / WhatsApp Group
- Group name input field (visible when "WhatsApp Group" selected)
- "Fetch Contacts" button → populates a table/list
- Checkbox-based contact selection (select all / deselect all)
- Import/Export CSV buttons
- Contact count display

**Tab 3 — Compose & Send**
- Text message editor (multi-line)
- "Attach media" button with file dialog (images, videos, PDFs)
- Media preview
- Delay range selector (min/max seconds, default 10–30)
- "Start Sending" / "Pause" / "Stop" buttons
- Progress bar + status log (scrolling text area)
- Per-contact status: ✅ sent, ❌ failed, ⏳ pending

**Tab 4 — Logs**
- Read-only text area showing `app.log` contents
- Auto-refresh on tab switch
- "Export Log" button

## Verification Plan

### Manual Verification

Since this app requires live browser interaction (WhatsApp Web QR login) and Google OAuth, it cannot be fully tested automatically. Manual verification steps:

1. **Launch the app:**
   ```bash
   cd /Volumes/Personal/Github/whatsApp_sender
   pip install -r requirements.txt
   playwright install chromium
   python main.py
   ```
   **Expected:** PyQt5 window opens with 4 tabs. No crashes.

2. **Google Contacts (requires `credentials.json`):**
   - Click "Connect Google" → browser opens for OAuth
   - After auth, status shows "Connected"
   - Switch to Contacts tab → select "Google Contacts" → click Fetch
   - **Expected:** contact list populates with names + phone numbers

3. **WhatsApp Web login:**
   - Click "Open WhatsApp Web" → Chromium window opens to `web.whatsapp.com`
   - Scan QR code with phone
   - **Expected:** status shows "Connected" in the app

4. **Contact extraction:**
   - Select "WhatsApp Contacts" source → Fetch → contacts appear
   - Select "WhatsApp Group" → enter group name → Fetch → members appear

5. **Send a test message:**
   - Select 1–2 contacts → Compose tab → type a short message
   - Click "Start Sending"
   - **Expected:** messages sent with 10–30s delay, progress bar updates, log shows status

> [!TIP]
> For initial testing, use your own number or a test group to avoid spamming real contacts.
