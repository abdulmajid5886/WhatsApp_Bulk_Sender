# WhatsApp sender (Web automation + Google Contacts)

Python CLI that:

1. Loads phone numbers from **Google Contacts** (People API).
2. **Scrapes** the **WhatsApp Web** chat list (what the UI exposes).
3. **Merges and deduplicates** numbers (E.164).
4. **Optionally verifies** numbers against WhatsApp (**HTTP** to `api.whatsapp.com` / `wa.me` + optional **Playwright** on Web).
5. **Sends** text or media with throttling and logging.

**Important:** WhatsApp Web automation is **not** an official API. It can break when the site changes; bulk use can trigger **rate limits or account restrictions**. Use **dry-run**, **`--allow`**, and **`--max-numbers`** for tests. You are responsible for **WhatsApp’s terms**, consent, and anti-spam rules.

---

## Prerequisites

- **Python 3.10+** (tested with 3.12)
- **Google Cloud**: [People API](https://developers.google.com/people) enabled, OAuth consent configured, OAuth **Desktop** client → `credentials.json` (or `WA_SENDER_CREDENTIALS`)
- **Playwright Chromium**: `playwright install chromium` after `pip install`
- Dependencies include **Playwright**, **Google API clients**, **phonenumbers**, **httpx** (for verify HTTP probes)

---

## Installation

```bash
cd /path/to/whatsApp_sender
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

**Use this environment for every command.** If `python` is not the venv interpreter, you may see `ModuleNotFoundError` (e.g. missing `google` or `phonenumbers`).

```bash
source .venv/bin/activate
python main.py --help
```

Or without activating:

```bash
.venv/bin/python main.py --help
```

Always install with the **same** interpreter you use to run the app:

```bash
python -m pip install -r requirements.txt
```

---

## Commands overview

| Command | Purpose |
|---------|---------|
| `google-auth` | Google OAuth; writes `token.pickle` |
| `fetch-google` | People API → `recipients_google.json` (and optional CSV) |
| `scrape-whatsapp` | WhatsApp Web sidebar → `recipients_whatsapp.json` |
| `merge` | Merge JSON inputs → `recipients_merged.json` (dedupe E.164) |
| `verify-whatsapp` | Heuristic on-WA checks → `recipients_verified.json` |
| `send` | Open chats, send text/media → `send.log` |

Global options are on **each** subcommand that needs them (e.g. `--default-region`), not before the subcommand name.

---

## Environment variables (optional)

| Variable | Purpose |
|----------|---------|
| `WA_SENDER_CREDENTIALS` | Google OAuth client JSON (default: `credentials.json`) |
| `WA_SENDER_TOKEN` | Saved OAuth token (default: `token.pickle`) |
| `WA_SENDER_SESSION` | Playwright profile for WhatsApp (default: `.wa_session`) |
| `WA_DEFAULT_REGION` | Region for numbers without `+` (e.g. `US`); override with `--default-region` |

---

## Implementation checklist

| # | Feature | Notes |
|---|---------|--------|
| 1 | Skeleton | `requirements.txt`, `main.py`, Playwright Chromium |
| 2 | Google Contacts | OAuth + `fetch-google`, E.164 via `phonenumbers` |
| 3 | WhatsApp scrape | Persistent `.wa_session/`, `scrape-whatsapp` |
| 4 | Merge / dedupe | `--allow` / `--block` line files |
| 5 | Send | `--dry-run`, delays, `--max-messages`, media |
| 6 | Verify | HTTP + optional DOM; see below |

---

## Typical workflow

Run from the project root with the venv active. Use `--default-region` when numbers lack a country code.

### 1. Google OAuth (once)

```bash
python main.py google-auth
```

### 2. Export Google phones

```bash
python main.py fetch-google --default-region US
```

Writes **`recipients_google.json`**. Each row is **one phone field** that passed validation, not necessarily one contact card. The People API uses **`connections.list`** only (not “Other contacts”). Contacts **without** a valid phone, or numbers **invalid** per `phonenumbers`, are omitted—so the row count is often **lower** than the total contacts shown in Google Contacts (~3k cards vs ~1.5k rows is normal).

### 3. Scrape WhatsApp Web

```bash
python main.py scrape-whatsapp --default-region US
```

QR login the first time; session in **`.wa_session/`**. Output: **`recipients_whatsapp.json`**.

### 4. Merge

```bash
python main.py merge --default-region US
```

Output: **`recipients_merged.json`**. Optional: `--out-csv`, `--allow`, `--block`.

### 5. Verify (optional)

Heuristic only—not a guarantee.

- **HTTP**: Follows redirects from `api.whatsapp.com/send?phone=…` and `wa.me/…`. **`not_on_wa`** if error-style phrases appear in the HTML. **`on_wa`** if the **final URL** contains `type=phone_number` and `phone=` (Meta’s usual shape for a resolvable chat link). Check **`http_heuristic_version`** in output when comparing old runs.
- **Playwright** (default): if HTTP stays **`unknown`**, opens **WhatsApp Web** (same session as send) and inspects `/send?phone=…` (composer vs “not on WhatsApp” / invite copy).

```bash
# HTTP only (no browser; many unknown if URL signal missing)
python main.py verify-whatsapp --http-only --max-numbers 50 --out recipients_verified.json

# Default: HTTP for all; DOM only where HTTP is unknown (needs logged-in Web)
python main.py verify-whatsapp --recipients recipients_merged.json --delay-min 5 --delay-max 12 --log verify.log

# DOM on every row (slow)
python main.py verify-whatsapp --dom-all --max-numbers 30
```

Output **`recipients_verified.json`** fields (per row): `e164`, `name`, `http_verdict`, `http_meta`, `http_heuristic_version`, `dom_verdict` (or `null` if skipped), `dom_meta`, `combined_verdict` (`on_wa` \| `not_on_wa` \| `unknown`). **`combined_verdict`** prefers DOM when that step ran and was conclusive; otherwise HTTP.

Tune strings in **`whatsapp_verify.py`** if WhatsApp changes pages.

### 6. Send

```bash
python main.py send --dry-run --message "Hello" --max-numbers 3
python main.py send --message "Hello" --log send.log
```

Default delay between messages is **random 10–20 seconds**. After every **5 successful** sends, an extra **25 seconds** is added to that wait (see `SEND_COOLDOWN_EVERY_N` / `SEND_COOLDOWN_EXTRA_S` in `whatsapp_automation.py`). Override with `--delay-min` / `--delay-max`.

```bash
python main.py send --message "Hello" --delay-min 12 --delay-max 18 --log send.log
python main.py send --media ./photo.jpg --message "Caption"
```

Optional: `--headless`, `--recipients`, `--allow`, `--block`. Prefer **`--allow`** with one number for the first real send.

---

## How to test

**Smoke**

```bash
python main.py --help
python main.py merge --help
python main.py send --help
python main.py verify-whatsapp --help
```

**Merge** (two small JSON files, e.g. `a.json` / `b.json`):

```json
[{"e164": "+15551234567", "name": "Alice", "source": "test"}]
```

```json
[
  {"e164": "+15551234567", "name": "Alice Longer", "source": "t2"},
  {"e164": "+447911123456", "name": "Bob", "source": "t2"}
]
```

```bash
python main.py merge --google a.json --whatsapp b.json --out merged_test.json
```

**Send dry-run** (no browser)

```bash
python main.py send --dry-run --recipients merged_test.json --message "test"
```

**Verify** (small list, HTTP-only)

```bash
python main.py verify-whatsapp --http-only --recipients merged_test.json --max-numbers 5 --out test_verified.json
```

---

## Local files (do not commit secrets)

| Path | Purpose |
|------|---------|
| `credentials.json` | Google OAuth client |
| `token.pickle` | Google token |
| `.wa_session/` | WhatsApp browser profile |
| `recipients_google.json` / `recipients_whatsapp.json` / `recipients_merged.json` | Lists |
| `recipients_verified.json` | Verify verdicts |
| `send.log` / `verify.log` | Run logs |

Your `.gitignore` may ignore `*.json` and logs—keep tokens and session dirs private regardless.

---

## Troubleshooting

- **`ModuleNotFoundError`** (`google`, `phonenumbers`, `httpx`, …): run **`python -m pip install -r requirements.txt`** with the **same** `python` as `main.py`, or **`.venv/bin/python -m pip install -r requirements.txt`**.
- **Wrong interpreter despite `(.venv)` prompt**: run `python -c "import sys; print(sys.executable)"` and fix PATH / use `.venv/bin/python` explicitly.
- **Playwright browser missing**: `playwright install chromium`.
- **Google OAuth / consent**: enable People API; add test users if the app is in Testing.
- **WhatsApp send/scrape breaks**: update **`SELECTORS`** in `whatsapp_automation.py`.
- **Verify always `unknown`**: re-run after pulling changes; check **`http_heuristic_version`**; try **`--dom-all`** on a tiny `--allow` list; adjust **`whatsapp_verify.py`** hints if Meta changed URLs or Web UI strings.
- **Wrong country / parsing**: use **`--default-region`** or full E.164 (`+…`).
