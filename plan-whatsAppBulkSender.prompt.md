## Plan: WhatsApp Bulk Sender App (Python, Browser, GUI)

**TL;DR:**
Build a Python GUI app that fetches phone numbers from WhatsApp group members, all WhatsApp contacts, and Google Contacts using browser automation, checks if each number is on WhatsApp, and sends a message (text/media) to each with a random delay (10–30 seconds) to avoid spam/blocking.

**Steps**

### Phase 1: Setup & Core Modules
1. Set up Python project structure for a GUI app (recommend PyQt5, Tkinter, or similar).
2. Integrate Google Contacts API:
   - Implement OAuth2 authentication flow.
   - Fetch and parse phone numbers from Google Contacts.
3. Implement WhatsApp Web automation (using Selenium or Playwright):
   - Automate login to WhatsApp Web (QR code scan).
   - Extract all WhatsApp contacts.
   - Extract group members’ numbers (saved and unsaved).

### Phase 2: Number Validation & Messaging
4. For each number, check if it is registered on WhatsApp (via WhatsApp Web search/automation).
5. Build message sending logic:
   - Allow user to compose text or select media file in GUI.
   - Send message to each valid number using browser automation.
   - Add random delay (10–30 seconds) between sends.

### Phase 3: GUI & User Experience
6. Design GUI for:
   - Authentication (Google, WhatsApp Web QR scan)
   - Contact/group selection and review
   - Message composition (text/media)
   - Progress/status display and error reporting
7. Implement error handling, logging, and user notifications.

**Relevant files**
- `main.py` — App entry point, GUI setup
- `google_contacts.py` — Google Contacts integration
- `whatsapp_automation.py` — WhatsApp Web automation (Selenium/Playwright)
- `messaging.py` — Message sending and delay logic
- `utils.py` — Helper functions (random delay, logging, etc.)

**Verification**
1. Test Google Contacts fetch and display in GUI.
2. Test WhatsApp Web login and contact/group scraping.
3. Validate WhatsApp number existence via automation.
4. Send test messages with random delays, monitor for errors/blocks.
5. User acceptance: GUI usability and reliability.

**Decisions**
- Python selected for implementation.
- Browser automation (Selenium/Playwright) for WhatsApp integration.
- GUI-based app (PyQt5 or Tkinter recommended).
- Unofficial automation may violate WhatsApp’s terms; user accepts risk.

**Further Considerations**
1. Recommend Playwright for more robust browser automation (supports QR login, stealth, better handling of WhatsApp Web updates).
2. Consider modular design for easy updates if WhatsApp Web changes.
3. Add export/import for contact lists and logs for transparency.
