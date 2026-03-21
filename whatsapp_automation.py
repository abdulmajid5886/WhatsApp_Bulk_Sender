"""WhatsApp Web: persistent session, chat scrape, send text/media."""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from utils import extract_phone_candidates, normalize_phone

WA_URL = "https://web.whatsapp.com"

# Centralized selectors; update when WhatsApp Web DOM changes.
SELECTORS = {
    "pane_side": "#pane-side",
    "chat_rows": [
        "#pane-side div[role='listitem']",
        "#pane-side div[data-testid='cell-frame-container']",
        "#pane-side [role='row']",
    ],
    "message_box": "div[contenteditable='true'][data-tab='10']",
    "message_box_alt": "footer div[contenteditable='true']",
    "send_button": "span[data-icon='send']",
    "attach_trigger": "span[data-icon='plus-rounded']",
    "attach_trigger_alt": "span[data-icon='attach-menu-plus']",
    "file_input": "input[type='file'][accept]",
}


def digits_only_e164(e164: str) -> str:
    return re.sub(r"\D", "", e164 or "")


def launch_context(
    playwright: Playwright,
    user_data_dir: Path,
    headless: bool = False,
) -> BrowserContext:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
    )


def wait_for_chat_list(page: Page, timeout_ms: int = 180_000) -> None:
    page.goto(WA_URL, wait_until="domcontentloaded")
    page.wait_for_selector(SELECTORS["pane_side"], timeout=timeout_ms)


def scrape_chat_list(
    page: Page,
    default_region: str | None,
    max_scrolls: int = 40,
) -> list[dict[str, Any]]:
    wait_for_chat_list(page)
    pane = page.locator(SELECTORS["pane_side"]).first
    for _ in range(max_scrolls):
        try:
            pane.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        except Exception:
            break
        page.wait_for_timeout(400)

    rows: list = []
    for sel in SELECTORS["chat_rows"]:
        rows = page.locator(sel).all()
        if rows:
            break
    seen_text: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            text = (row.inner_text() or "").strip()
        except Exception:
            continue
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        title = lines[0] if lines else ""
        subtitle = lines[1] if len(lines) > 1 else ""
        candidates = extract_phone_candidates(text) + extract_phone_candidates(
            subtitle
        )
        e164 = None
        for c in candidates:
            e164 = normalize_phone(c, default_region)
            if e164:
                break
        if not e164:
            e164 = normalize_phone(title, default_region)
        out.append(
            {
                "e164": e164,
                "name": title,
                "source": "whatsapp",
                "raw_row": text[:500],
            }
        )
    return out


def export_scrape_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _focus_message_box(page: Page, timeout_ms: int = 30_000) -> None:
    box = page.locator(SELECTORS["message_box"]).first
    if box.count() == 0:
        box = page.locator(SELECTORS["message_box_alt"]).first
    box.wait_for(state="visible", timeout=timeout_ms)
    box.click()


def open_chat_by_phone(page: Page, e164: str, timeout_ms: int = 60_000) -> None:
    digits = digits_only_e164(e164)
    if not digits:
        raise ValueError("Invalid e164")
    page.goto(
        f"{WA_URL}/send?phone={digits}",
        wait_until="domcontentloaded",
    )
    _focus_message_box(page, timeout_ms=timeout_ms)


def send_text_message(page: Page, text: str) -> None:
    _focus_message_box(page)
    page.keyboard.type(text, delay=15)
    page.locator(SELECTORS["send_button"]).first.click()


def send_media_message(page: Page, media_path: Path, caption: str | None = None) -> None:
    path = media_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    _focus_message_box(page)
    # Open attach menu
    for sel in (SELECTORS["attach_trigger"], SELECTORS["attach_trigger_alt"]):
        loc = page.locator(sel).first
        if loc.count():
            loc.click()
            break
    page.wait_for_timeout(300)
    inp = page.locator(SELECTORS["file_input"]).first
    inp.set_input_files(str(path))
    page.wait_for_timeout(800)
    if caption:
        _focus_message_box(page)
        page.keyboard.type(caption, delay=15)
    page.locator(SELECTORS["send_button"]).first.click()


def run_scrape(
    user_data_dir: Path,
    out_json: Path,
    headless: bool,
    default_region: str | None,
) -> list[dict[str, Any]]:
    with sync_playwright() as p:
        ctx = launch_context(p, user_data_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            rows = scrape_chat_list(page, default_region=default_region)
        finally:
            ctx.close()
    export_scrape_json(out_json, rows)
    print("Wrote", out_json, "(%d rows)" % len(rows))
    return rows


def run_send_batch(
    recipients: list[dict[str, Any]],
    message: str,
    media_path: Path | None,
    user_data_dir: Path,
    headless: bool,
    dry_run: bool,
    delay_min_s: float,
    delay_max_s: float,
    max_messages: int | None,
    log_path: Path | None,
) -> None:
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    if dry_run:
        n = 0
        for r in recipients:
            if max_messages is not None and n >= max_messages:
                log("Stopped: max_messages reached")
                break
            e164 = r.get("e164")
            if not e164:
                log("skip (no e164): %r" % (r,))
                continue
            log("dry-run: would send to %s (%s)" % (e164, r.get("name", "")))
            n += 1
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("Log:", log_path)
        return

    sent = 0
    with sync_playwright() as p:
        ctx = launch_context(p, user_data_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            wait_for_chat_list(page)
            for r in recipients:
                if max_messages is not None and sent >= max_messages:
                    log("Stopped: max_messages reached")
                    break
                e164 = r.get("e164")
                if not e164:
                    log("skip (no e164): %r" % (r,))
                    continue
                if dry_run:
                    log("dry-run: would send to %s (%s)" % (e164, r.get("name", "")))
                    sent += 1
                    continue
                try:
                    open_chat_by_phone(page, e164)
                    page.wait_for_timeout(1200)
                    if media_path:
                        send_media_message(page, media_path, caption=message or None)
                    else:
                        send_text_message(page, message)
                    log("sent ok: %s" % e164)
                    sent += 1
                except Exception as ex:
                    log("FAIL %s: %s" % (e164, ex))
                jitter = random.uniform(delay_min_s, delay_max_s)
                log("sleep %.1fs" % jitter)
                time.sleep(jitter)
        finally:
            ctx.close()

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Log:", log_path)
