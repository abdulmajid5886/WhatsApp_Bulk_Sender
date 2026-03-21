# WhatsApp sender (Web automation + Google Contacts)

Python CLI that (1) loads phone numbers from **Google Contacts** via the People API, (2) **scrapes** what it can from **WhatsApp Web**’s chat list, (3) **merges and deduplicates** numbers (E.164), and (4) sends **text or media** with delays and logging.

**Important:** WhatsApp Web automation is **not** an official API. It can break when the site changes, and bulk messaging can trigger **rate limits or account restrictions**. Use **dry-run** and small tests first. You are responsible for **WhatsApp’s terms**, consent, and anti-spam rules.

---

## Prerequisites

- **Python 3.10+** (tested with 3.12)
- **Google Cloud**: [People API](https://developers.google.com/people) enabled, OAuth consent configured, OAuth **Desktop** client → download as `credentials.json` in the project root (or set `WA_SENDER_CREDENTIALS`)
- **Playwright Chromium**: installed after `pip` (see below)

---

## Installation

```bash
cd /path/to/whatsApp_sender
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

**Use this environment for every command.** If you skip activation, `python` is your system interpreter and you will get `ModuleNotFoundError: No module named 'google'`.

```bash
source .venv/bin/activate   # each new terminal session
python main.py google-auth
```

Or call the venv’s Python directly (no activation needed):

```bash
.venv/bin/python main.py google-auth
```

Verify the CLI:

```bash
python main.py --help
python main.py fetch-google --help
```

---

## Environment variables (optional)

| Variable | Purpose |
|----------|---------|
| `WA_SENDER_CREDENTIALS` | Path to Google OAuth client JSON (default: `credentials.json`) |
| `WA_SENDER_TOKEN` | Path to saved OAuth token (default: `token.pickle`) |
| `WA_SENDER_SESSION` | Playwright user-data dir for WhatsApp (default: `.wa_session`) |
| `WA_DEFAULT_REGION` | Default region for numbers without `+` (e.g. `US`, `GB`); can override with `--default-region` |

---

## Implementation checklist (from project plan)

Use this as a build/verification list. All items are implemented in this repo.

| # | Item | What to verify |
|---|------|----------------|
| 1 | **Project skeleton** | `requirements.txt`, `main.py` subcommands, Playwright Chromium installed |
| 2 | **Google Contacts** | `google-auth` works; `fetch-google` writes JSON/CSV with E.164 numbers |
| 3 | **WhatsApp session + scrape** | `.wa_session/` persists login; `scrape-whatsapp` writes `recipients_whatsapp.json` |
| 4 | **Merge + dedupe** | `merge` combines Google + WhatsApp JSON, dedupes by E.164; `--allow` / `--block` work |
| 5 | **Send** | `send` opens chats, sends text and/or media; `--dry-run`, delays, `--max-messages`, `send.log` |

---

## How to run (typical flow)

Run from the project root with the venv activated. Use `--default-region` when your contacts omit country codes (e.g. `--default-region US`).

### 1. Google OAuth (once)

```bash
python main.py google-auth
```

Completes in the browser; saves **`token.pickle`** (do not commit).

### 2. Export Google contact phones

```bash
python main.py fetch-google --default-region US
```

Writes **`recipients_google.json`** (optional: `--out-csv path.csv`).

### 3. Scrape WhatsApp Web chat list

Opens a browser; scan QR the first time. Session is stored under **`.wa_session/`**.

```bash
python main.py scrape-whatsapp --default-region US
```

Writes **`recipients_whatsapp.json`**. (Many chats show names only; numbers appear only when the UI exposes them.)

### 4. Merge lists

Uses whichever of these files exist: `recipients_google.json`, `recipients_whatsapp.json`.

```bash
python main.py merge --default-region US
```

Output: **`recipients_merged.json`**. Optional: `--out-csv merged.csv`, `--allow allow.txt`, `--block block.txt` (one phone per line, `#` comments allowed).

### 5. Send messages

**Always test with dry-run first:**

```bash
python main.py send --dry-run --message "Hello" --max-messages 3
```

Real send (after you trust the list):

```bash
python main.py send --message "Hello" --delay-min 10 --delay-max 25 --log send.log
```

With an image and caption:

```bash
python main.py send --media ./photo.jpg --message "Caption here"
```

Optional: `--headless` (WhatsApp Web often needs a visible window for QR and stability), `--recipients other.json`, `--allow allow.txt`, `--block block.txt`.

---

## How to test

### A. Smoke tests (no Google / no WhatsApp)

```bash
python main.py --help
python main.py merge --help
python main.py send --help
```

### B. Test `merge` without Google

Create two small JSON files locally (your `.gitignore` may ignore `*.json`; that’s fine for local secrets/output).

`a.json`:

```json
[
  {"e164": "+15551234567", "name": "Alice", "source": "test"}
]
```

`b.json`:

```json
[
  {"e164": "+15551234567", "name": "Alice Longer Name", "source": "test2"},
  {"e164": "+447911123456", "name": "Bob", "source": "test2"}
]
```

```bash
python main.py merge --google a.json --whatsapp b.json --out merged_test.json
```

Expect one row for `+15551234567` (longer name kept) plus `+447911123456`.

### C. Test `send` without messaging anyone

Use a JSON list with **your own** number as the only `e164`, then:

```bash
python main.py send --dry-run --recipients merged_test.json --message "test"
```

Dry-run does **not** open the browser and writes lines to **`send.log`** (default).

### D. End-to-end (real)

1. `python main.py google-auth`
2. `python main.py fetch-google --default-region XX`
3. `python main.py scrape-whatsapp` (log in, wait for chat list)
4. `python main.py merge`
5. `python main.py send --dry-run --message "Hi" --max-messages 1`
6. `python main.py send --message "Hi" --allow allow.txt` where `allow.txt` contains **one** test number

---

## Files you will see locally

| File / dir | Purpose |
|------------|---------|
| `credentials.json` | Google OAuth client (keep private) |
| `token.pickle` | Google refresh token (keep private) |
| `.wa_session/` | WhatsApp Web browser profile (keep private) |
| `recipients_*.json` | Exported / merged lists |
| `send.log` | Send run log |

---

## Troubleshooting

- **`ModuleNotFoundError: No module named 'phonenumbers'`** (or any other package from `requirements.txt`): same fix — run **`python -m pip install -r requirements.txt`** with the same `python` you use for `main.py`, or use **`.venv/bin/python -m pip install -r requirements.txt`**.
- **`ModuleNotFoundError: No module named 'google'`** (even with `(.venv)` in your prompt): the `python` you run is **not** the one where packages were installed—common with **conda**, **pyenv**, or activating a venv from the wrong folder. Fix:
  1. `cd` to the project root (where `main.py` and `.venv` live).
  2. Run **`python -m pip install -r requirements.txt`** (using the *same* `python` you use for `main.py`). That guarantees installs match the interpreter.
  3. Check: `python -c "import sys; print(sys.executable)"` — it should end with **`whatsApp_sender/.venv/bin/python`** (or your chosen venv).
  4. If not, use the full path: **`.venv/bin/python -m pip install -r requirements.txt`** then **`.venv/bin/python main.py fetch-google …`**.
- **Other `ModuleNotFoundError`**: same as above — always use `python -m pip install -r requirements.txt` with the interpreter you run the app with.
- **Playwright browser missing**: run `playwright install chromium`.
- **Google `access_denied` / consent**: add your Google account as a test user in the OAuth consent screen (if the app is in Testing).
- **WhatsApp selectors fail**: WhatsApp Web DOM changes; update `SELECTORS` in `whatsapp_automation.py`.
- **Invalid phone / wrong country**: set `--default-region` or use full E.164 (`+…`) in your source data.
