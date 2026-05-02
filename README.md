# WhatsApp sender (Web automation + Google Contacts)

Python CLI that:

1. Loads phone numbers from **Google Contacts** (People API).
2. **Scrapes** the **WhatsApp Web** chat list (what the UI exposes).
3. **Merges and deduplicates** numbers (E.164).
4. **Optionally verifies** numbers against WhatsApp (**HTTP** + optional **Playwright** on Web).
5. **Lists group chats** (`list-whatsapp-groups`, uses the **Groups** sidebar filter) and **scrapes group participants** (`group-members`, sequential, one browser session).
6. **Sends** text or media with throttling and logging.

**Important:** WhatsApp Web automation is **not** an official API. It can break when the site changes; bulk use can trigger **rate limits or account restrictions**. Use **dry-run**, **`--allow`**, and **`--max-numbers`** for tests. You are responsible for **WhatsApp’s terms**, consent, and anti-spam rules.

---

## Run from the project folder

Almost all paths (`groups.txt`, `credentials.json`, `.wa_session/`) are **relative to your current directory**. From the repo root:

```bash
cd /path/to/whatsApp_sender
source .venv/bin/activate   # if you use a venv
python main.py <subcommand> ...
```

If a file is “not found”, either **`cd`** into the project first or pass an **absolute path** (e.g. `--groups-file /path/to/whatsApp_sender/groups.txt`).

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
| `list-whatsapp-groups` | Clicks **Groups** filter, scrolls list → `whatsapp_groups.json` (all group chats) |
| `group-members` | **`--groups-json`** (recommended) or **`--groups-file`** → `group_members.json` |
| `merge` | Merge JSON inputs → `recipients_merged.json` (dedupe E.164) |
| `verify-whatsapp` | Heuristic on-WA checks → `recipients_verified.json` |
| `send` | Open chats, send text/media → `send.log` |

Global options are on **each** subcommand that needs them (e.g. `--default-region`), not before the subcommand name.

### `list-whatsapp-groups` flags (reference)

| Flag | Meaning |
|------|---------|
| `--out` | Index JSON (default `whatsapp_groups.json`). |
| `--max-scrolls` | Sidebar scroll rounds to load older chats (default 80). |
| `--probe-out` | Write a JSON snapshot: **diagnosis**, `row_samples`, `data_icon_histogram_top`, counts. |
| `--verbose` | Print diagnosis + row / heuristic counts to stderr. |
| `--no-groups-tab` | Skip clicking the **Groups** chip (icon/heuristics only; misses many groups). |
| `--session` / `--headless` | Same as other WhatsApp commands. |

### `group-members` flags (reference)

| Flag | Meaning |
|------|---------|
| `--groups-json PATH` | **Recommended.** Output from `list-whatsapp-groups`: each item has at least **`title`**; **`data_id`** is often empty (WhatsApp Web usually hides JIDs in the DOM). Opening uses **sidebar row match** (fuzzy title, emoji-tolerant) then **global search**, clicking the **search overlay / dialog** result—not only the left list. |
| `--groups-file PATH` | One title per line (`#` comments); opens via **search** (same overlay logic as JSON fallback). |
| `--max-groups N` | Process only the first N groups (debug). |
| `--dry-run` | Print parsed group list only; no browser. |
| `--out` | Combined JSON (default `group_members.json`). |
| `--session` | WhatsApp profile dir (default `.wa_session`). |
| `--split-files` / `--split-dir` | Also write one JSON per group. |
| `--pause-min` / `--pause-max` | Delay between groups in seconds (default 2–5). |
| `--headless` | Often flaky for QR/UI; prefer visible window while testing. |

You must pass **either** `--groups-json` **or** `--groups-file` (not both).

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
| 7 | Group members | `list-whatsapp-groups` (Groups tab + scroll), `group-members`, `GROUP_SELECTORS` / open-search logic in `whatsapp_groups.py` |

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

### 3b. Group members (optional)

#### Discover: `list-whatsapp-groups`

By default the CLI **clicks the “Groups” chip** under the search box (All / Unread / Favourites / **Groups**), scrolls the list, and writes **`whatsapp_groups.json`**. That lists **all group chats**, including those with **custom profile photos** (which do not use the default “group” icon in the DOM).

The JSON may include a **`detection`** field, for example:

- **`sidebar_groups_filter`** — the Groups tab was used; rows are treated as groups.
- **`titled_row_fallback`** — the Groups tab could not be used and heuristics listed **every titled sidebar row**; your file may include **1:1 chats**—remove them before `group-members`.

WhatsApp Web often exposes **`data_id_node_count: 0`** in the DOM (no `@g.us` on list rows). That is expected; **`data_id`** in the JSON is usually empty and opening relies on **title matching**.

Debug discovery:

```bash
python main.py list-whatsapp-groups --out whatsapp_groups.json --probe-out sidebar_probe.json --verbose
```

Use **`--no-groups-tab`** only if you must skip the chip. Increase **`--max-scrolls`** if some groups are missing.

#### Scrape participants: `group-members`

```bash
python main.py list-whatsapp-groups --out whatsapp_groups.json
python main.py group-members --groups-json whatsapp_groups.json --default-region US --out group_members.json
```

**How each group is opened**

1. **Sidebar:** Clicks a **`[role="row"]`** (or listitem) in **`#pane-side`** whose title **loosely** matches the JSON title (handles emoji and small spelling differences, e.g. `Brainathon Winners` vs `Brainathon Winners 🏆`).
2. If that fails, **global search** (`Ctrl/Cmd+K` / search icon): types the title, then clicks the result in the **center search overlay** (`[role="dialog"]` …), **not** the first row of the normal left chat list (which would leave the main pane empty).
3. After the chat loads, the script **clicks the main conversation pane** to focus it (it does **not** send **Escape** here—on WhatsApp Web, **Escape** often acts as “back” and **closes the chat** you just opened). Then it opens **group info** (header or **⋮ → Group info**) and scrolls the participant list.

Use a **visible** browser while debugging (`--headless` is easy to break for search/modals).

Optional: **`--max-groups 1`** to test one group.

**People across your account (groups + 1:1):** **`group-members`** collects **group participant** rows (names; **E.164 only when WhatsApp shows a number** in that UI). **`scrape-whatsapp`** exports **sidebar chats** (1:1 + mixed) to **`recipients_whatsapp.json`**. This project does **not** mine every **message** in every thread for phone numbers.

**Alternative — `groups.txt`:** copy the template; opening is search-heavy if the sidebar does not already show that chat:

```bash
cp groups.example.txt groups.txt
python main.py group-members --groups-file groups.txt --dry-run
python main.py group-members --groups-file groups.txt --default-region US --out group_members.json
```

**Limits:** The member list is **virtualized** (only a slice of rows exists in the DOM at once). The scraper **scrolls the group-info drawer repeatedly** until no new names appear; very large groups take longer (many scroll rounds). Participant **phone numbers** are often **hidden** by WhatsApp (names / “~” privacy only)—**`e164` is null unless that row shows digits**; people are still group members. Output is **deduped by display name** (one row per person; prefers entries with a visible number and shorter text). The script **never treats the whole page as the member list** (avoids mixing **chat messages** with participants). After search, it **does not click the chat list or the center of the chat** (those can switch to another chat—e.g. “52nd Entry”—or open links in the latest message); it only **blurs** focus and **Escapes** while a search **dialog** is still visible. If scraping breaks after a WhatsApp UI update, adjust **`GROUP_SELECTORS`** and **`_MEMBER_SCROLL_*`** in **`whatsapp_groups.py`**. Between groups, the script **closes dialogs and re-selects the Groups filter** to reduce stuck search modals.

**`--dry-run`** only validates input and prints names (no browser). **`--split-files`** writes `group_members_<slug>.json` per group; **`--pause-min`** / **`--pause-max`** add delay between groups.

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
python main.py group-members --help
python main.py list-whatsapp-groups --help
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

**Group members** (discover → scrape; same **`.wa_session/`** as other WhatsApp commands)

1. **Discover:** `python main.py list-whatsapp-groups --out whatsapp_groups.json`
2. **Dry-run:** `python main.py group-members --groups-json whatsapp_groups.json --dry-run`
3. **Live:** use a **visible** browser first.  
   `python main.py group-members --groups-json whatsapp_groups.json --default-region US --out group_members.json`
4. **Inspect** `group_members.json`: `group_title`, `data_id`, `members`, `opened_header_title`, `error`. Errors like **`Could not find chat header`** usually mean the chat never opened in the main pane (search overlay not confirmed, or UI changed). Empty **`members`** with no header error → drawer/selectors; adjust **`GROUP_SELECTORS`** in **`whatsapp_groups.py`**.

---

## Local files (do not commit secrets)

| Path | Purpose |
|------|---------|
| `credentials.json` | Google OAuth client |
| `token.pickle` | Google token |
| `.wa_session/` | WhatsApp browser profile |
| `recipients_*.json` / `group_members*.json` | Exported lists (often gitignored via `*.json`) |
| `whatsapp_groups.json` | Index from `list-whatsapp-groups` (`title` per group; optional `data_id`; optional `detection`) |
| `sidebar_probe.json` | Optional output from `list-whatsapp-groups --probe-out` (DOM diagnosis for support/debug) |
| `groups.txt` | Optional titles-only list for `group-members --groups-file` |
| `groups.example.txt` | Commented template to copy → `groups.txt` |
| `send.log` / `verify.log` | Run logs |

Your **`.gitignore`** ignores `*.json` and `*.log`—keep tokens and session dirs private. Add **`groups.txt`** to `.gitignore` yourself if you do not want group names in git.

---

## Troubleshooting

- **`groups file not found`**: Create **`groups.txt`** from the template (`cp groups.example.txt groups.txt`), add real titles, and run commands from the **project directory** or pass a full path to `--groups-file`.
- **`No group names in file`**: Every line is a comment or blank—add at least one line that does **not** start with `#`.
- **`ModuleNotFoundError`** (`google`, `phonenumbers`, `httpx`, …): run **`python -m pip install -r requirements.txt`** with the **same** `python` as `main.py`, or **`.venv/bin/python -m pip install -r requirements.txt`**.
- **Wrong interpreter despite `(.venv)` prompt**: run `python -c "import sys; print(sys.executable)"` and fix PATH / use `.venv/bin/python` explicitly.
- **Playwright browser missing**: `playwright install chromium`.
- **Google OAuth / consent**: enable People API; add test users if the app is in Testing.
- **WhatsApp send/scrape breaks**: update **`SELECTORS`** in `whatsapp_automation.py`.
- **Verify always `unknown`**: re-run after pulling changes; check **`http_heuristic_version`**; try **`--dom-all`** on a tiny `--allow` list; adjust **`whatsapp_verify.py`** hints if Meta changed URLs or Web UI strings.
- **`group-members` / “Could not find chat header”** or main pane still shows **“Download WhatsApp…”**: the conversation did not open. Keep **Groups** selected in the sidebar when testing; run **without `--headless`**; ensure JSON titles roughly match the UI (emoji differences are handled with **loose** matching). If search was used, the script must click the **modal “Chats” result**, not the sidebar’s first row.
- **`group-members` / wrong chat or empty members**: prefer **`--groups-json`** from **`list-whatsapp-groups`**; use a **visible** browser; update **`GROUP_SELECTORS`** (and participant row selectors) in **`whatsapp_groups.py`** after WhatsApp UI changes.
- **`list-whatsapp-groups` finds too few groups**: ensure the **Groups** filter is clickable (UI in **English** helps the automation); do not use **`--no-groups-tab`** unless needed; increase **`--max-scrolls`**.
- **`list-whatsapp-groups` / probe shows `data_id_node_count: 0`**: normal on current WhatsApp Web—**JIDs are not in the list DOM**. Discovery uses the **Groups** tab and **`[role="row"]`**. If you see **`titled_row_fallback`** in **`detection`**, trim **1:1** chats from the JSON. Use **`--probe-out`** and **`--verbose`** for **`diagnosis`**, **`row_samples`**, and **`data_icon_histogram_top`**.
- **Wrong country / parsing**: use **`--default-region`** or full E.164 (`+…`).
