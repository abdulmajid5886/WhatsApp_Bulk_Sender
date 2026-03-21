# WhatsApp Bulk Sender — Full Implementation

## Phase 1: Setup & Core Modules
- [ ] Set up project dependencies (`requirements.txt`, virtual env)
- [ ] Implement [utils.py](file:///Volumes/Personal/Github/whatsApp_sender/utils.py) — logging, error handling, config helpers
- [ ] Implement [google_contacts.py](file:///Volumes/Personal/Github/whatsApp_sender/google_contacts.py) — OAuth2 + fetch/parse contacts
- [ ] Implement [whatsapp_automation.py](file:///Volumes/Personal/Github/whatsApp_sender/whatsapp_automation.py) — Playwright-based WhatsApp Web automation
  - [ ] Login (QR code scan)
  - [ ] Extract all WhatsApp contacts
  - [ ] Extract group members (saved + unsaved)

## Phase 2: Number Validation & Messaging
- [ ] WhatsApp number validation (check if number is on WhatsApp)
- [ ] Implement [messaging.py](file:///Volumes/Personal/Github/whatsApp_sender/messaging.py) — send text/media messages with random delay

## Phase 3: GUI & User Experience
- [ ] Implement [main.py](file:///Volumes/Personal/Github/whatsApp_sender/main.py) — PyQt5 GUI
  - [ ] Auth panel (Google OAuth, WhatsApp QR)
  - [ ] Contact/group selection & review
  - [ ] Message composition (text + media)
  - [ ] Progress/status display with logs
  - [ ] Error notifications
- [ ] Export/import contact lists
- [ ] Logging and error handling throughout

## Verification
- [ ] Manual test: launch GUI, verify all tabs render
- [ ] Manual test: Google Contacts OAuth flow
- [ ] Manual test: WhatsApp Web QR login + contact extraction
- [ ] Manual test: send test message with delay
