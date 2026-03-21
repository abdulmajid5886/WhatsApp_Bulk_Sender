"""Best-effort WhatsApp registration hints: HTTP (wa.me / api.whatsapp.com) + Web DOM.

HTTP_HEURISTICS_VERSION / VERIFY_* below are the single place to adjust when Meta
changes landing pages or WhatsApp Web copy. Treat all verdicts as heuristic.
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any, Literal

import httpx

from playwright.sync_api import Page, sync_playwright

from whatsapp_automation import (
    WA_URL,
    SELECTORS,
    digits_only_e164,
    launch_context,
    wait_for_chat_list,
)

# Bump when changing HTTP body/URL rules so exported JSON stays interpretable.
HTTP_HEURISTICS_VERSION = 1
DOM_HEURISTICS_VERSION = 1

# ---------------------------------------------------------------------------
# HTTP: substrings in response body (lowercased). Conservative: only mark
# not_on_wa when matched; otherwise unknown unless a positive hint matches.
# Meta changes these pages often — update here.
# ---------------------------------------------------------------------------
HTTP_NOT_ON_WA_BODY_HINTS: tuple[str, ...] = (
    "phone number shared via url is not on whatsapp",
    "phone number shared via url isn't on whatsapp",
    "isn't on whatsapp",
    "is not on whatsapp",
    "not on whatsapp",
    "invalid phone number",
    "invalid number",
    "couldn't look up this phone number",
    "could not look up this phone number",
)

HTTP_ON_WA_BODY_HINTS: tuple[str, ...] = (
    "continue to chat",
    "continue to whatsapp",
    "message on whatsapp",
    "chat on whatsapp",
)

# ---------------------------------------------------------------------------
# DOM: WhatsApp Web after /send?phone= — visible copy hints (lowercased body).
# ---------------------------------------------------------------------------
DOM_NOT_ON_WA_TEXT_HINTS: tuple[str, ...] = (
    "isn't on whatsapp",
    "is not on whatsapp",
    "not on whatsapp",
    "invalid phone number",
    "phone number invalid",
    "couldn't find",
    "could not find",
    "invite to whatsapp",
)

Verdict = Literal["on_wa", "not_on_wa", "unknown"]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _http_verdict_from_body(body: str, final_url: str) -> Verdict:
    low = body.lower()
    for hint in HTTP_NOT_ON_WA_BODY_HINTS:
        if hint in low:
            return "not_on_wa"
    for hint in HTTP_ON_WA_BODY_HINTS:
        if hint in low:
            return "on_wa"
    # Final URL sometimes stays on api.whatsapp.com/send with generic shell — unknown.
    if "web.whatsapp.com/send" in final_url.lower():
        return "unknown"
    return "unknown"


def http_probe_phone(digits: str, timeout_s: float = 20.0) -> dict[str, Any]:
    """GET api.whatsapp.com then optionally wa.me; returns verdict + metadata (no full body)."""
    d = re.sub(r"\D", "", digits or "")
    if not d:
        return {
            "http_verdict": "unknown",
            "http_meta": {"error": "empty_digits"},
            "http_heuristic_version": HTTP_HEURISTICS_VERSION,
        }
    urls = (
        f"https://api.whatsapp.com/send?phone={d}",
        f"https://wa.me/{d}",
    )
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            merged = ""
            last_status = 0
            last_url = ""
            for url in urls:
                r = client.get(url)
                merged += "\n" + (r.text or "")
                last_status = r.status_code
                last_url = str(r.url)
                verdict = _http_verdict_from_body(merged, last_url)
                if verdict != "unknown":
                    break
            verdict = _http_verdict_from_body(merged, last_url)
            snippet = merged[:8000]
            return {
                "http_verdict": verdict,
                "http_meta": {
                    "status_code": last_status,
                    "final_url": last_url,
                    "body_length": len(merged),
                    "urls_fetched": list(urls[: merged.count("\n") + 1]) if False else urls,
                    "snippet_sha256": __import__("hashlib")
                    .sha256(snippet.encode("utf-8", errors="replace"))
                    .hexdigest()[:16],
                },
                "http_heuristic_version": HTTP_HEURISTICS_VERSION,
            }
    except Exception as ex:
        return {
            "http_verdict": "unknown",
            "http_meta": {"error": str(ex)},
            "http_heuristic_version": HTTP_HEURISTICS_VERSION,
        }


def dom_probe_phone(page: Page, e164: str, timeout_ms: int = 60_000) -> dict[str, Any]:
    """Inspect WhatsApp Web /send UI; requires logged-in session."""
    digits = digits_only_e164(e164)
    if not digits:
        return {
            "dom_verdict": "unknown",
            "dom_meta": {"error": "invalid_e164"},
            "dom_heuristic_version": DOM_HEURISTICS_VERSION,
        }
    try:
        page.goto(
            f"{WA_URL}/send?phone={digits}",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        page.wait_for_timeout(2200)
        body_low = ""
        try:
            body_low = (page.inner_text("body") or "")[:80000].lower()
        except Exception:
            pass
        for hint in DOM_NOT_ON_WA_TEXT_HINTS:
            if hint in body_low:
                return {
                    "dom_verdict": "not_on_wa",
                    "dom_meta": {"matched_text_hint": hint},
                    "dom_heuristic_version": DOM_HEURISTICS_VERSION,
                }
        box = page.locator(SELECTORS["message_box"]).first
        if box.count() > 0:
            try:
                if box.is_visible():
                    return {
                        "dom_verdict": "on_wa",
                        "dom_meta": {"signal": "message_box_data_tab"},
                        "dom_heuristic_version": DOM_HEURISTICS_VERSION,
                    }
            except Exception:
                pass
        box2 = page.locator(SELECTORS["message_box_alt"]).first
        if box2.count() > 0:
            try:
                if box2.is_visible():
                    return {
                        "dom_verdict": "on_wa",
                        "dom_meta": {"signal": "footer_contenteditable"},
                        "dom_heuristic_version": DOM_HEURISTICS_VERSION,
                    }
            except Exception:
                pass
        send_vis = False
        try:
            sb = page.locator(SELECTORS["send_button"]).first
            send_vis = sb.count() > 0 and sb.is_visible()
        except Exception:
            pass
        if send_vis:
            return {
                "dom_verdict": "on_wa",
                "dom_meta": {"signal": "send_button_visible"},
                "dom_heuristic_version": DOM_HEURISTICS_VERSION,
            }
        return {
            "dom_verdict": "unknown",
            "dom_meta": {"signal": "no_composer_no_known_banner"},
            "dom_heuristic_version": DOM_HEURISTICS_VERSION,
        }
    except Exception as ex:
        return {
            "dom_verdict": "unknown",
            "dom_meta": {"error": str(ex)},
            "dom_heuristic_version": DOM_HEURISTICS_VERSION,
        }


def combined_verdict(
    http_v: Verdict, dom_v: Verdict | None, dom_ran: bool
) -> Verdict:
    if dom_ran and dom_v is not None and dom_v != "unknown":
        return dom_v
    return http_v


def run_verify_batch(
    recipients: list[dict[str, Any]],
    user_data_dir: Path,
    headless: bool,
    delay_min_s: float,
    delay_max_s: float,
    http_only: bool,
    dom_all: bool,
    out_path: Path,
    log_path: Path | None,
    max_numbers: int | None,
) -> list[dict[str, Any]]:
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    out_rows: list[dict[str, Any]] = []
    n_done = 0

    for r in recipients:
        if max_numbers is not None and n_done >= max_numbers:
            log("Stopped: max_numbers reached")
            break
        e164 = r.get("e164")
        name = (r.get("name") or "").strip()
        if not e164:
            log("skip (no e164): %r" % (r,))
            continue

        http_result = http_probe_phone(digits_only_e164(e164))
        http_v: Verdict = http_result.get("http_verdict", "unknown")  # type: ignore[assignment]

        need_dom = not http_only and (dom_all or http_v == "unknown")
        dom_v: Verdict | None = None
        dom_meta: dict[str, Any] = {}

        if need_dom:
            # Defer browser until first DOM-needed row
            pass

        row_base = {
            "e164": e164,
            "name": name,
            "http_verdict": http_v,
            "http_meta": http_result.get("http_meta"),
            "http_heuristic_version": http_result.get("http_heuristic_version"),
        }
        out_rows.append(row_base)
        n_done += 1

    # Second pass: DOM in one browser session for rows that need it
    if http_only:
        for i, r in enumerate(out_rows):
            r["dom_verdict"] = None
            r["dom_meta"] = None
            r["dom_heuristic_version"] = None
            r["combined_verdict"] = combined_verdict(
                r["http_verdict"], None, False
            )
        _write_out(out_path, out_rows)
        if log_path:
            _write_log(log_path, lines)
        return out_rows

    dom_indices = [
        i
        for i, row in enumerate(out_rows)
        if dom_all or row["http_verdict"] == "unknown"
    ]

    if not dom_indices:
        for row in out_rows:
            row["dom_verdict"] = None
            row["dom_meta"] = None
            row["dom_heuristic_version"] = None
            row["combined_verdict"] = combined_verdict(
                row["http_verdict"], None, False
            )
        _write_out(out_path, out_rows)
        if log_path:
            _write_log(log_path, lines)
        return out_rows

    with sync_playwright() as p:
        ctx = launch_context(p, user_data_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            wait_for_chat_list(page)
            for idx in dom_indices:
                row = out_rows[idx]
                e164 = row["e164"]
                log("dom probe: %s" % e164)
                dom_result = dom_probe_phone(page, e164)
                dom_v = dom_result.get("dom_verdict", "unknown")  # type: ignore[assignment]
                row["dom_verdict"] = dom_v
                row["dom_meta"] = dom_result.get("dom_meta")
                row["dom_heuristic_version"] = dom_result.get("dom_heuristic_version")
                row["combined_verdict"] = combined_verdict(
                    row["http_verdict"], dom_v, True
                )
                log(
                    "  http=%s dom=%s -> %s"
                    % (
                        row["http_verdict"],
                        row["dom_verdict"],
                        row["combined_verdict"],
                    )
                )
                jitter = random.uniform(delay_min_s, delay_max_s)
                time.sleep(jitter)
            # Rows that skipped DOM
            for i, row in enumerate(out_rows):
                if i not in dom_indices:
                    row["dom_verdict"] = None
                    row["dom_meta"] = None
                    row["dom_heuristic_version"] = None
                    row["combined_verdict"] = combined_verdict(
                        row["http_verdict"], None, False
                    )
        finally:
            ctx.close()

    _write_out(out_path, out_rows)
    if log_path:
        _write_log(log_path, lines)
    print("Wrote", out_path, "(%d rows)" % len(out_rows))
    return out_rows


def _write_out(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Log:", path)
