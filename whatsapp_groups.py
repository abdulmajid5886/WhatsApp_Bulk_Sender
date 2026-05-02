"""WhatsApp Web: scrape group participant list (sequential, multi-group).

GROUP_SELECTORS and DOM assumptions break when WhatsApp updates the web app.
"""

from __future__ import annotations

import json
import platform
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from utils import extract_phone_candidates, normalize_phone
from whatsapp_automation import SELECTORS, launch_context, wait_for_chat_list

# Update when WhatsApp Web changes (drawer / search / header).
GROUP_SELECTORS = {
    "search_icon": [
        '[data-icon="search"]',
        '[data-icon="search-light"]',
        'span[data-icon="search"]',
        'button[aria-label="Search"]',
        'div[aria-label="Search"]',
    ],
    "header_title": [
        '[data-testid="conversation-info-header-chat-title"]',
        '[data-testid="conversation-header"] span[dir="auto"]',
        '#main [data-testid="conversation-header"] span[dir="auto"]',
        "#main [role='banner'] span[dir='auto']",
        'header[data-testid="chat-header"] span[dir="auto"]',
        "#main header span[dir=\"auto\"]",
        'header[data-testid="conversation-header"] span[dir="auto"]',
    ],
    "header_click": [
        '#main [data-testid="conversation-header"]',
        '[data-testid="conversation-header"]',
        "#main [role='banner']",
        '[data-testid="conversation-info-header"]',
        "header[data-testid='chat-header']",
        "header[data-testid='conversation-header']",
        "#main header",
    ],
    "drawer_root": [
        "div[data-animate-drawer-sidebar]",
        "[data-testid='drawer-right']",
        "[data-testid='drawer']",
        "div[role='complementary']",
        "aside[role='complementary']",
        "aside",
        "[data-testid*='drawer']",
    ],
    "member_rows": [
        "div[role='listitem']",
        "[role='row']",
        "div[data-testid='cell-frame-container']",
    ],
}

# Participant list in group info is virtualized; need many scroll rounds + stable-end detection.
_MEMBER_SCROLL_MAX_ROUNDS = 360
_MEMBER_SCROLL_STAGNANT = 22


def load_group_names(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def load_groups_spec(path: Path) -> list[dict[str, Any]]:
    """Load group scrape targets from JSON: { \"groups\": [...] } or a bare list.

    Each item: {\"title\": \"...\", \"data_id\": \"...@g.us\" (optional)} or a string title.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "groups" in raw:
        raw = raw["groups"]
    if not isinstance(raw, list):
        raise ValueError("groups JSON must be a list or {\"groups\": [...]}")
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            t = item.strip()
            if t:
                out.append({"title": t, "data_id": None})
            continue
        if isinstance(item, dict):
            t = (item.get("title") or item.get("name") or "").strip()
            if not t:
                continue
            did = item.get("data_id")
            if did is not None:
                did = str(did).strip() or None
            out.append({"title": t, "data_id": did})
    return out


def _click_sidebar_groups_filter(page: Page) -> bool:
    """Click the **Groups** chip under the search box so the list shows only group chats."""
    if page.locator("#pane-side").count() == 0:
        pane = page.locator("#side").first
    else:
        pane = page.locator("#pane-side").first
    try:
        tab = pane.get_by_role("tab", name=re.compile(r"^\s*Groups\s*$", re.I))
        if tab.count() > 0:
            tab.first.click(timeout=8000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    try:
        btn = pane.get_by_role("button", name=re.compile(r"^\s*Groups\s*$", re.I))
        if btn.count() > 0:
            btn.first.click(timeout=8000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    try:
        g = pane.get_by_text("Groups", exact=True)
        if g.count() > 0:
            g.first.click(timeout=8000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


def _scroll_pane_side(page: Page, max_rounds: int) -> None:
    for side_sel in (
        SELECTORS["pane_side"],
        "#side",
        '[data-testid="chat-list"]',
        '[data-testid="chatlist"]',
    ):
        root = page.locator(side_sel)
        if root.count() == 0:
            continue
        pane = root.first
        try:
            pane.wait_for(state="visible", timeout=15_000)
        except Exception:
            continue
        for _ in range(max_rounds):
            try:
                pane.evaluate(
                    """el => {
                      el.scrollTop = el.scrollHeight;
                      const inner = el.querySelector('[tabindex="0"]') || el.firstElementChild;
                      if (inner && inner.scrollHeight > inner.clientHeight + 40) {
                        inner.scrollTop = inner.scrollHeight;
                      }
                    }"""
                )
            except Exception:
                break
            page.wait_for_timeout(350)
        return


def discover_groups_sidebar(
    page: Page,
    max_scrolls: int = 60,
    use_groups_filter: bool = True,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Collect group chats from the left list.

    Default: click the **Groups** filter tab first (matches your WhatsApp Web UI) so every
    ``[role="row"]`` is a group—even with custom group photos (no ``default-group`` icon).

    Without that, we use heuristics (``@g.us``, ``N members``, ``default-group-refreshed``, …).
    """
    for sel in (
        "#pane-side [role='listitem']",
        "#pane-side [role='row']",
        "#pane-side [data-testid='cell-frame-container']",
        "#side [role='listitem']",
        "#side [role='row']",
    ):
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=120_000)
            break
        except Exception:
            continue
    page.wait_for_timeout(600)
    groups_tab_clicked = False
    if use_groups_filter:
        groups_tab_clicked = _click_sidebar_groups_filter(page)
        page.wait_for_timeout(500)
    _scroll_pane_side(page, max_scrolls)
    data = page.evaluate(
        """({ allGroupsMode }) => {
          function rowTitle(row) {
            const sp = row.querySelector('span[dir="auto"]');
            if (sp) {
              const t = (sp.innerText || '').split('\\n')[0].trim();
              if (t) return t;
            }
            const lines = (row.innerText || '').split('\\n').map(s => s.trim())
              .filter(Boolean);
            for (const line of lines) {
              if (/^\\d{1,2}:\\d{2}/.test(line)) continue;
              if (line.length >= 1 && line.length <= 200) return line;
            }
            return lines[0] || '';
          }
          function rowIcons(row) {
            return [...row.querySelectorAll('[data-icon]')].map(
              e => (e.getAttribute('data-icon') || '').toLowerCase()
            ).filter(Boolean);
          }
          function gidFromNode(node) {
            if (!node || !node.getAttribute) return null;
            for (const attr of ['data-id', 'data-jid', 'id']) {
              const v = node.getAttribute(attr);
              if (v && v.toLowerCase().includes('@g.us')) return v;
            }
            if (node.attributes) {
              for (const a of node.attributes) {
                const v = a.value || '';
                if (v.toLowerCase().includes('@g.us')) return v;
              }
            }
            return null;
          }
          function findGroupJidInRow(row) {
            for (const n of row.querySelectorAll('[data-id], [data-jid]')) {
              const g = gidFromNode(n);
              if (g) return g;
            }
            let p = row;
            for (let i = 0; i < 12 && p; i++) {
              const g = gidFromNode(p);
              if (g) return g;
              p = p.parentElement;
            }
            return null;
          }
          function textLooksLikeGroup(row) {
            const blob = (row.innerText || '').toLowerCase();
            if (/\\d+\\s+members?\\b/.test(blob)) return true;
            if (/\\d+\\s+participants?\\b/.test(blob)) return true;
            if (/\\bparticipants?:\\s*\\d+/.test(blob)) return true;
            if (/\\bsubgroup\\b/.test(blob)) return true;
            if (/\\bcommunity\\b/.test(blob) && /\\d/.test(blob)) return true;
            return false;
          }
          function iconsLookLikeGroup(row) {
            for (const ic of rowIcons(row)) {
              if (ic.includes('community')) return true;
              if (ic.includes('default-group')) return true;
              if (ic.includes('group') && !ic.includes('ungroup')) return true;
            }
            return false;
          }
          function ariaLooksLikeGroup(row) {
            const a = ((row.getAttribute('aria-label') || '') + ' ' +
              (row.getAttribute('title') || '')).toLowerCase();
            return a.includes('group') || a.includes('community');
          }
          function isExcludedNavTitle(t) {
            const x = (t || '').toLowerCase().trim();
            const blocked = new Set([
              'chats', 'search', 'archived', 'settings', 'status', 'channels',
              'business', 'newsletter', 'tools', 'profile', 'starred', 'labels',
              'whatsapp', 'marketplace', 'updates', 'unread', 'favourites', 'favorites'
            ]);
            return !x || blocked.has(x);
          }
          const paneSelectors = [
            '#pane-side', '#side', '[data-testid="chat-list"]',
            '[data-testid="chatlist"]', '[data-testid="chat-list-search"]'
          ];
          let pane = null;
          for (const s of paneSelectors) {
            pane = document.querySelector(s);
            if (pane) break;
          }
          if (!pane) {
            return {
              items: [],
              used_fallback: false,
              groups_filter_mode: !!allGroupsMode,
            };
          }
          const rowList = [...pane.querySelectorAll(
            '[role="row"], [role="listitem"], [data-testid="cell-frame-container"]'
          )];
          if (allGroupsMode) {
            const seen = new Set();
            const out = [];
            for (const row of rowList) {
              const title = rowTitle(row);
              if (isExcludedNavTitle(title)) continue;
              const jid = findGroupJidInRow(row);
              const key = jid
                ? ('j:' + jid)
                : ('t:' + title.toLowerCase().replace(/\\s+/g, ' ').trim());
              if (seen.has(key)) continue;
              seen.add(key);
              out.push({ title: title || jid || key, data_id: jid || '' });
            }
            return {
              items: out,
              used_fallback: false,
              groups_filter_mode: true,
            };
          }
          const seen = new Set();
          const out = [];
          for (const row of rowList) {
            const title = rowTitle(row);
            if (isExcludedNavTitle(title)) continue;
            const jid = findGroupJidInRow(row);
            if (jid) {
              if (seen.has(jid)) continue;
              seen.add(jid);
              out.push({ title: title || jid, data_id: jid });
              continue;
            }
            if (!textLooksLikeGroup(row) && !iconsLookLikeGroup(row)
                && !ariaLooksLikeGroup(row)) continue;
            const key = 't:' + title.toLowerCase().replace(/\\s+/g, ' ').trim();
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({ title, data_id: '' });
          }
          let usedFallback = false;
          if (out.length === 0 && rowList.length > 0) {
            usedFallback = true;
            for (const row of rowList) {
              const title = rowTitle(row);
              if (isExcludedNavTitle(title)) continue;
              const key = 't:' + title.toLowerCase().replace(/\\s+/g, ' ').trim();
              if (seen.has(key)) continue;
              seen.add(key);
              out.push({ title, data_id: '', row_fallback: true });
            }
          }
          return {
            items: out,
            used_fallback: usedFallback,
            groups_filter_mode: false,
          };
        }""",
        {"allGroupsMode": groups_tab_clicked},
    )
    meta: dict[str, Any] = {
        "used_titled_rows_fallback": False,
        "used_groups_filter_tab": False,
        "groups_tab_click_ok": groups_tab_clicked,
    }
    used_fallback = False
    if isinstance(data, dict):
        used_fallback = bool(data.get("used_fallback"))
        meta["used_groups_filter_tab"] = bool(data.get("groups_filter_mode"))
        data = data.get("items")
    if not isinstance(data, list):
        return [], meta
    out: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        t = str(row.get("title") or "").strip()
        d = str(row.get("data_id") or "").strip()
        if d and "@g.us" in d.lower():
            out.append({"title": t or d, "data_id": d})
        elif t and not d:
            out.append({"title": t, "data_id": ""})
    meta["used_titled_rows_fallback"] = used_fallback
    return out, meta


def export_groups_index_json(
    path: Path,
    groups: list[dict[str, str]],
    exported_epoch: float | None = None,
    used_titled_rows_fallback: bool = False,
    used_groups_filter_tab: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "exported_epoch": exported_epoch if exported_epoch is not None else time.time(),
        "group_count": len(groups),
        "groups": groups,
    }
    if used_groups_filter_tab:
        payload["detection"] = (
            "sidebar_groups_filter: clicked the Groups tab; rows are treated as group chats "
            "(use group-members --groups-json to scrape participants)."
        )
    elif used_titled_rows_fallback:
        payload["detection"] = (
            "titled_row_fallback: WhatsApp hid chat JIDs in DOM; file may include 1:1 chats—"
            "delete non-groups before group-members."
        )
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def discover_sidebar_probe(page: Page) -> dict[str, Any]:
    """Summarize chat-list DOM: explains why JID discovery fails and samples rows/icons."""
    data = page.evaluate(
        """() => {
          const panes = ['#pane-side', '#side', '[data-testid="chat-list"]'];
          let pane = null;
          let sel = '';
          for (const s of panes) {
            pane = document.querySelector(s);
            if (pane) { sel = s; break; }
          }
          if (!pane) {
            return { error: 'no_pane', diagnosis: 'No #pane-side or #side element found.' };
          }
          const ids = [];
          for (const n of pane.querySelectorAll('[data-id]')) {
            const v = n.getAttribute('data-id');
            if (v) ids.push(v);
          }
          const uniq = [...new Set(ids)];
          const rows = [...pane.querySelectorAll('[role="row"]')];
          const hist = {};
          for (const row of rows) {
            for (const el of row.querySelectorAll('[data-icon]')) {
              const ic = el.getAttribute('data-icon') || '';
              if (ic) hist[ic] = (hist[ic] || 0) + 1;
            }
          }
          const histSorted = Object.entries(hist).sort((a, b) => b[1] - a[1]).slice(0, 50);
          let heuristicGroupLike = 0;
          for (const row of rows) {
            const t = row.innerText || '';
            const icons = [...row.querySelectorAll('[data-icon]')].map(
              e => (e.getAttribute('data-icon') || '').toLowerCase()
            );
            const textHit = /\\d+\\s+members?\\b/i.test(t)
              || /\\d+\\s+participants?\\b/i.test(t);
            const iconHit = icons.some(
              ic => ic.includes('group') && !ic.includes('ungroup')
            );
            if (textHit || iconHit) heuristicGroupLike++;
          }
          const rowSamples = [];
          for (let i = 0; i < Math.min(20, rows.length); i++) {
            const row = rows[i];
            const icons = [...row.querySelectorAll('[data-icon]')].map(
              e => e.getAttribute('data-icon')
            ).filter(Boolean);
            const title = (row.querySelector('span[dir="auto"]')?.innerText || '')
              .split('\\n')[0].trim().slice(0, 120);
            rowSamples.push({
              index: i,
              title,
              icons: icons.slice(0, 14),
              text_snippet: (row.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
            });
          }
          const nDataId = pane.querySelectorAll('[data-id]').length;
          return {
            pane_selector: sel,
            listitem_count: pane.querySelectorAll('[role="listitem"]').length,
            row_count: rows.length,
            cell_frame_count: pane.querySelectorAll(
              '[data-testid="cell-frame-container"]'
            ).length,
            data_id_node_count: nDataId,
            data_id_samples: uniq.slice(0, 40),
            data_icon_histogram_top: Object.fromEntries(histSorted),
            heuristic_group_like_row_count: heuristicGroupLike,
            row_samples: rowSamples,
            diagnosis: nDataId === 0
              ? 'Chat list has no data-id nodes (WhatsApp Web change). Old logic required @g.us in DOM; discovery now uses [role=row] + subtitle/icons heuristics.'
              : 'data-id nodes exist; if groups still missing, check data_id_samples for @g.us format.',
          };
        }"""
    )
    return data if isinstance(data, dict) else {"error": "probe_failed"}


def run_discover_groups(
    user_data_dir: Path,
    headless: bool,
    out_path: Path,
    max_scrolls: int,
    probe_out: Path | None = None,
    verbose: bool = False,
    use_groups_filter: bool = True,
) -> list[dict[str, str]]:
    probe: dict[str, Any] = {}
    used_fb = False
    g_filter = False
    groups: list[dict[str, str]] = []
    with sync_playwright() as p:
        ctx = launch_context(p, user_data_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            wait_for_chat_list(page)
            groups, meta = discover_groups_sidebar(
                page,
                max_scrolls=max_scrolls,
                use_groups_filter=use_groups_filter,
            )
            used_fb = bool(meta.get("used_titled_rows_fallback"))
            g_filter = bool(meta.get("used_groups_filter_tab"))
            tab_ok = bool(meta.get("groups_tab_click_ok"))
            if use_groups_filter and not tab_ok:
                print(
                    "[list-whatsapp-groups] Groups tab not clicked (wrong role/name or UI). "
                    "Heuristic list may miss groups with custom photos. "
                    "Use a visible window; UI language should be English for the Groups chip.",
                    file=sys.stderr,
                )
            if used_fb and groups and not g_filter:
                print(
                    "[list-whatsapp-groups] No group-specific DOM signals; exported every titled "
                    "sidebar row (likely includes 1:1 chats)—trim JSON or check probe icons.",
                    file=sys.stderr,
                )
            if probe_out or verbose:
                probe = discover_sidebar_probe(page)
            if probe_out:
                probe_out.parent.mkdir(parents=True, exist_ok=True)
                probe_out.write_text(
                    json.dumps(probe, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                print("Wrote sidebar probe:", probe_out)
            if verbose and probe:
                print("[list-whatsapp-groups] diagnosis:", probe.get("diagnosis"), file=sys.stderr)
                print(
                    "[list-whatsapp-groups] rows=%s data_id_nodes=%s heuristic_group_like=%s "
                    "discovered=%s groups_filter_mode=%s"
                    % (
                        probe.get("row_count"),
                        probe.get("data_id_node_count"),
                        probe.get("heuristic_group_like_row_count"),
                        len(groups),
                        g_filter,
                    ),
                    file=sys.stderr,
                )
        finally:
            ctx.close()
    export_groups_index_json(
        out_path,
        groups,
        used_titled_rows_fallback=used_fb,
        used_groups_filter_tab=g_filter,
    )
    print("Wrote", out_path, "(%d groups)" % len(groups))
    if not groups:
        print(
            "No groups in list. Click Chats, ensure Groups filter or use --probe-out.",
            file=sys.stderr,
        )
    return groups


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _strip_for_title_match(s: str) -> str:
    """Lowercase collapse; drop most punctuation/emoji so JSON title matches UI variants."""
    s = re.sub(r"\s+", " ", (s or "").strip().lower())
    return re.sub(r"[^\w\s\u0600-\u06FF\u0750-\u077F]", "", s, flags=re.UNICODE).strip()


def _titles_match_loose(ui_title: str, wanted: str) -> bool:
    a = _strip_for_title_match(ui_title)
    b = _strip_for_title_match(wanted)
    if not a or not b:
        return False
    return (
        a == b
        or a.startswith(b)
        or b.startswith(a)
        or a in b
        or b in a
    )


def _select_all_shortcut(page: Page) -> None:
    mod = "Meta" if platform.system() == "Darwin" else "Control"
    page.keyboard.press(f"{mod}+a")


def _open_search(page: Page) -> None:
    for sel in GROUP_SELECTORS["search_icon"]:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.click(timeout=3000)
                page.wait_for_timeout(400)
                return
            except Exception:
                continue
    mod = "Meta" if platform.system() == "Darwin" else "Control"
    page.keyboard.press(f"{mod}+k")
    page.wait_for_timeout(400)


def _focus_search_input(page: Page) -> None:
    for sel in (
        '#side div[contenteditable="true"]',
        '#pane-side div[contenteditable="true"]',
        'div[contenteditable="true"][data-tab="3"]',
        '[role="textbox"][contenteditable="true"]',
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.click(timeout=3000)
                return
            except Exception:
                continue


def _visible_conversation_panel(page: Page):
    for sel in (
        "#main",
        '[data-testid="conversation-panel-wrapper"]',
        '[data-testid="conversation-panel-messages"]',
    ):
        loc = page.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            if loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _wait_conversation_panel_visible(page: Page, timeout_ms: int = 30000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if _visible_conversation_panel(page) is not None:
            return
        page.wait_for_timeout(120)
    raise RuntimeError(
        "Conversation panel did not become visible. "
        "Run list-whatsapp-groups and use --groups-json (data_id opens the correct row)."
    )


def _conversation_panel(page: Page):
    v = _visible_conversation_panel(page)
    if v is not None:
        return v
    for sel in (
        "#main",
        '[data-testid="conversation-panel-wrapper"]',
        '[data-testid="conversation-panel-messages"]',
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            return loc
    return page.locator("#main").first


def _sidebar_click_group_row(page: Page, title: str, data_id: str | None) -> str:
    """Programmatic click on a sidebar row; returns reason token."""
    tid = _norm_title(title)
    tstrip = _strip_for_title_match(title)
    result = page.evaluate(
        """({ dataId, titleNorm, titleStrip }) => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const strip = (s) => norm(s).replace(/[^\\w\\s\\u0600-\\u06FF\\u0750-\\u077F]/gu, '').trim();
          function loose(ui, want) {
            const a = strip(ui);
            const b = strip(want);
            if (!a || !b) return false;
            return a === b || a.startsWith(b) || b.startsWith(a) || a.includes(b) || b.includes(a);
          }
          const pane = document.querySelector('#pane-side');
          if (!pane) return 'no_pane';
          if (dataId) {
            const hit = pane.querySelector('[data-id="' + CSS.escape(dataId) + '"]');
            if (hit) {
              const row = hit.closest('[role="listitem"]')
                || hit.closest('[data-testid="cell-frame-container"]')
                || hit.closest('[role="row"]')
                || hit;
              row.click();
              return 'click_data_id';
            }
          }
          const candidates = pane.querySelectorAll(
            '[role="row"], [role="listitem"], [data-testid="cell-frame-container"]'
          );
          for (const row of candidates) {
            const sp = row.querySelector('span[dir="auto"]');
            const raw = (sp && sp.innerText) || (row.innerText || '').split('\\n')[0] || '';
            const t = norm(raw);
            if (t && (t === titleNorm || loose(raw, titleStrip))) {
              row.click();
              return 'click_title';
            }
          }
          return 'not_found';
        }""",
        {"dataId": data_id or "", "titleNorm": tid, "titleStrip": tstrip},
    )
    return str(result) if result is not None else "not_found"


def _reset_search_and_dialogs(page: Page) -> None:
    """Close stuck global search / modals so the next group open does not reuse a half-open UI."""
    try:
        page.evaluate(
            """() => {
              const a = document.activeElement;
              if (a && a.blur) a.blur();
            }"""
        )
    except Exception:
        pass
    page.wait_for_timeout(120)
    for _ in range(8):
        dlg = page.locator('[role="dialog"]')
        try:
            if dlg.count() == 0:
                break
            if not dlg.first.is_visible():
                break
        except Exception:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(260)


def open_group_chat_smart(page: Page, title: str, data_id: str | None) -> None:
    """Open a group chat: prefer exact sidebar row (data_id), then title match, then search."""
    _reset_search_and_dialogs(page)
    close_panels(page)
    page.wait_for_timeout(200)
    _click_sidebar_groups_filter(page)
    page.wait_for_timeout(350)
    r = _sidebar_click_group_row(page, title, data_id)
    if r in ("click_data_id", "click_title"):
        page.wait_for_timeout(500)
        try:
            _wait_conversation_panel_visible(page, timeout_ms=30000)
            page.wait_for_timeout(400)
            return
        except Exception:
            pass
    search_and_open_group_chat(page, title)


def _read_main_header_title(page: Page) -> str:
    for sel in GROUP_SELECTORS["header_title"]:
        loc = page.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            t = (loc.inner_text() or "").strip()
            if t:
                return t
            t = (loc.get_attribute("title") or "").strip()
            if t:
                return t
        except Exception:
            continue
    return ""


def _open_group_info_via_menu(page: Page) -> bool:
    panel = _visible_conversation_panel(page)
    root = page.locator("#main").first if panel is None else panel
    for sel in (
        "span[data-icon='menu']",
        "span[data-icon='down']",
        "button[aria-label='Menu']",
        "div[aria-label='Menu']",
        "[data-testid='conversation-menu-bar'] span[data-icon='menu']",
    ):
        loc = root.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            if not loc.is_visible():
                continue
            loc.click(timeout=4000, force=True)
            page.wait_for_timeout(450)
            for label in (
                "Group info",
                "Group details",
                "Group information",
                "View group info",
            ):
                opt = page.get_by_text(label, exact=True).first
                if opt.count() == 0:
                    continue
                try:
                    if opt.is_visible():
                        opt.click(timeout=4000)
                        page.wait_for_timeout(900)
                        return True
                except Exception:
                    continue
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            continue
    return False


def _click_chat_header(page: Page) -> None:
    try:
        _wait_conversation_panel_visible(page, timeout_ms=8000)
    except Exception:
        pass
    panel = _visible_conversation_panel(page)
    if panel is None:
        panel = _conversation_panel(page)
    try:
        panel.wait_for(state="attached", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(400)
    for sel in GROUP_SELECTORS["header_click"]:
        loc = page.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            if not loc.is_visible():
                continue
            title_sp = loc.locator('span[dir="auto"]').first
            if title_sp.count() > 0:
                try:
                    if title_sp.is_visible():
                        title_sp.click(timeout=6000)
                        page.wait_for_timeout(900)
                        return
                except Exception:
                    pass
            loc.click(timeout=8000, force=True)
            page.wait_for_timeout(800)
            return
        except Exception:
            continue
    try:
        clicked = panel.evaluate(
            """m => {
              if (!m) return false;
              const pick = (s) => m.querySelector(s);
              for (const s of [
                '[data-testid="conversation-header"]',
                '[data-testid="conversation-info-header"]',
                '[role="banner"]',
                'header'
              ]) {
                const el = pick(s);
                if (el && el.offsetParent !== null) {
                  el.click();
                  return true;
                }
              }
              const rect = m.getBoundingClientRect();
              const x = rect.left + Math.min(rect.width * 0.45, 280);
              const y = rect.top + 36;
              const stack = document.elementsFromPoint(x, y);
              for (const el of stack) {
                if (!m.contains(el)) continue;
                const r = el.getBoundingClientRect();
                if (r.height > 8 && r.height < 120 && r.width > 40) {
                  el.click();
                  return true;
                }
              }
              return false;
            }"""
        )
        if clicked:
            page.wait_for_timeout(800)
            return
    except Exception:
        pass
    if _open_group_info_via_menu(page):
        return
    raise RuntimeError("Could not find chat header to open group info")


def _viewport_width(page: Page) -> int:
    try:
        vs = page.viewport_size
        if vs and vs.get("width"):
            return int(vs["width"])
    except Exception:
        pass
    return 1280


def _resolve_group_info_drawer(page: Page):
    """Find the right-hand group-info panel. WhatsApp DOM varies; use position + text hints."""
    vw = _viewport_width(page)
    hint = re.compile(
        r"participant|members|mute notifications|group description|"
        r"add members|report group|exit group|invite link|group admin|you.{0,4}admin",
        re.I,
    )
    for sel in GROUP_SELECTORS["drawer_root"]:
        loc = page.locator(sel)
        n = loc.count()
        for i in range(min(n, 20)):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if not box:
                    continue
                w, h, x = box["width"], box["height"], box["x"]
                if w < 120 or h < 100:
                    continue
                snippet = (el.inner_text() or "")[:1500]
                if not hint.search(snippet):
                    continue
                if w >= vw * 0.85:
                    return el
                if x + w < vw * 0.34:
                    continue
                if x < vw * 0.38 and w > vw * 0.45:
                    continue
                return el
            except Exception:
                continue
    return None


def _wait_group_info_drawer(page: Page, timeout_ms: int = 25000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if _resolve_group_info_drawer(page) is not None:
            return True
        page.wait_for_timeout(140)
    return False


def _scroll_all_scrollables_to_bottom(root) -> None:
    try:
        root.evaluate(
            """el => {
              const all = [];
              const walk = n => {
                if (!n || n.nodeType !== 1) return;
                try {
                  const sh = n.scrollHeight, ch = n.clientHeight;
                  if (sh > ch + 18 && ch > 40) all.push(n);
                } catch (e) {}
                for (const c of n.children || []) walk(c);
              };
              walk(el);
              for (const n of all) {
                n.scrollTop = n.scrollHeight;
              }
            }"""
        )
    except Exception:
        pass


def _wheel_inside_locator(page: Page, root) -> None:
    try:
        box = root.bounding_box()
        if not box or box["height"] < 20:
            return
        x = box["x"] + min(box["width"] * 0.55, 220)
        y = box["y"] + box["height"] * 0.5
        page.mouse.move(x, y)
        for _ in range(5):
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(45)
    except Exception:
        pass


def _click_view_all_members_in_drawer(page: Page, root) -> None:
    try:
        if root.evaluate("el => el === document.body"):
            return
    except Exception:
        return
    for pattern in (
        re.compile(r"view\s+all", re.I),
        re.compile(r"see\s+all", re.I),
        re.compile(r"show\s+all", re.I),
    ):
        try:
            hit = root.get_by_text(pattern).first
            if hit.count() == 0:
                continue
            if hit.is_visible():
                hit.click(timeout=5000)
                page.wait_for_timeout(700)
                return
        except Exception:
            continue


def _row_text_looks_like_chat_snippet(text: str) -> bool:
    """Participant rows rarely contain multiple link lines; message list does."""
    if not text:
        return False
    low = text.lower()
    n_http = low.count("http://") + low.count("https://")
    if n_http >= 2:
        return True
    if "meet.google.com" in low and text.count("\n") >= 2:
        return True
    if "wa.me/" in low or "chat.whatsapp.com/" in low:
        return True
    if "messages and calls are end-to-end encrypted" in low:
        return True
    return False


def _parse_member_row(
    text: str, default_region: str | None
) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text or len(text) < 1:
        return None
    if _row_text_looks_like_chat_snippet(text):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0] if lines else text
    low = title.lower()
    if low in (
        "search",
        "chats",
        "add new contact",
        "new group",
        "communities",
        "channels",
    ):
        return None
    if "participant" in low and len(text) < 40:
        return None
    if re.match(r"^\d{1,2}:\d{2}(\s*[ap]m)?$", title, re.I):
        return None
    if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", title):
        return None
    if re.match(r"^\d+\s+participants?\s*$", low):
        return None
    candidates = extract_phone_candidates(text)
    e164 = None
    for c in candidates:
        e164 = normalize_phone(c, default_region)
        if e164:
            break
    if not e164:
        e164 = normalize_phone(title, default_region)
    return {
        "name": title,
        "e164": e164,
        "raw_row": text[:400],
        "source": "whatsapp_group",
    }


def _collect_members_after_drawer(
    page: Page, default_region: str | None
) -> list[dict[str, Any]]:
    root = _resolve_group_info_drawer(page)
    if root is None:
        return []
    _click_view_all_members_in_drawer(page, root)
    raw = _collect_members_virtualized_scroll(page, root, default_region)
    return _dedupe_members_by_display_name(raw)


def _dedupe_members_by_display_name(
    members: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One entry per person; prefer row with e164 and shorter raw_row (real list row)."""
    best: dict[str, dict[str, Any]] = {}

    def score(m: dict[str, Any]) -> tuple[int, int]:
        e = m.get("e164")
        raw = str(m.get("raw_row") or "")
        return (1 if e else 0, -len(raw))

    for m in members:
        name = (m.get("name") or "").strip()
        key = _strip_for_title_match(name)
        if not key:
            continue
        if key not in best or score(m) > score(best[key]):
            best[key] = m
    return list(best.values())


def _collect_members_virtualized_scroll(
    page: Page, root, default_region: str | None
) -> list[dict[str, Any]]:
    """Scroll the drawer participant list to the end many times; collect new rows each pass."""
    seen: set[str] = set()
    members: list[dict[str, Any]] = []
    stagnant = 0
    row_sel = (
        '[role="listitem"], [role="row"], [data-testid="cell-frame-container"]'
    )
    for _ in range(_MEMBER_SCROLL_MAX_ROUNDS):
        _scroll_all_scrollables_to_bottom(root)
        _wheel_inside_locator(page, root)
        page.wait_for_timeout(220)
        new_this_round = 0
        try:
            rows = root.locator(row_sel).all()
        except Exception:
            rows = []
        for row in rows:
            try:
                if not row.is_visible():
                    continue
                text = (row.inner_text() or "").strip()
            except Exception:
                continue
            key = text[:220]
            if not key or key in seen:
                continue
            parsed = _parse_member_row(text, default_region)
            if not parsed:
                continue
            seen.add(key)
            members.append(parsed)
            new_this_round += 1
        if new_this_round == 0:
            stagnant += 1
            if stagnant >= _MEMBER_SCROLL_STAGNANT:
                break
        else:
            stagnant = 0
    return members


def close_panels(page: Page) -> None:
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)


def _click_global_search_result_row(page: Page, group_title: str) -> bool:
    """WhatsApp opens a centered search UI; results are often NOT under #pane-side. Click that row."""
    for sel in (
        '[role="dialog"] [role="listitem"]',
        '[role="dialog"] [role="row"]',
        '[role="alertdialog"] [role="listitem"]',
        '[data-testid="search-result"]',
    ):
        items = page.locator(sel)
        n = items.count()
        for i in range(min(n, 25)):
            it = items.nth(i)
            try:
                if not it.is_visible():
                    continue
                line = (it.inner_text() or "").splitlines()[0].strip()
                if line and _titles_match_loose(line, group_title):
                    it.click(timeout=8000)
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
    for sel in ('[role="dialog"] [role="listitem"]', '[role="dialog"] [role="row"]'):
        it = page.locator(sel).first
        if it.count() == 0:
            continue
        try:
            if it.is_visible():
                it.click(timeout=8000)
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    clicked = page.evaluate(
        """({ titleNorm, titleStrip }) => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const strip = (s) => norm(s).replace(/[^\\w\\s\\u0600-\\u06FF\\u0750-\\u077F]/gu, '').trim();
          function loose(raw, want) {
            const a = strip(raw);
            const b = strip(want);
            if (!a || !b) return false;
            return a === b || a.startsWith(b) || b.startsWith(a) || a.includes(b) || b.includes(a);
          }
          const pane = document.querySelector('#pane-side');
          const pool = [];
          for (const el of document.querySelectorAll('[role="listitem"], [role="row"]')) {
            if (!el.offsetParent) continue;
            if (pane && pane.contains(el)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 40 || r.height < 8) continue;
            pool.push(el);
          }
          for (const el of pool) {
            const sp = el.querySelector('span[dir="auto"]');
            const raw = (sp && sp.innerText) || (el.innerText || '').split('\\n')[0] || '';
            const t = norm(raw);
            if (t && (t === titleNorm || loose(raw, titleStrip))) {
              el.click();
              return true;
            }
          }
          if (pool.length > 0) {
            pool[0].click();
            return true;
          }
          return false;
        }""",
        {
            "titleNorm": _norm_title(group_title),
            "titleStrip": _strip_for_title_match(group_title),
        },
    )
    if clicked:
        page.wait_for_timeout(500)
    return bool(clicked)


def search_and_open_group_chat(page: Page, group_title: str) -> None:
    close_panels(page)
    _open_search(page)
    _focus_search_input(page)
    page.wait_for_timeout(300)
    _select_all_shortcut(page)
    page.wait_for_timeout(150)
    page.keyboard.type(group_title, delay=25)
    page.wait_for_timeout(1800)
    if _click_global_search_result_row(page, group_title):
        pass
    else:
        loc = page.locator("#pane-side [role='row']").first
        if loc.count() == 0:
            loc = page.locator("#pane-side [role='listitem']").first
        if loc.count() == 0:
            loc = page.locator("#pane-side [data-testid='cell-frame-container']").first
        if loc.count() > 0:
            try:
                loc.click(timeout=10000)
            except Exception:
                page.keyboard.press("Enter")
        else:
            page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    try:
        _wait_conversation_panel_visible(page, timeout_ms=30000)
    except Exception:
        page.wait_for_timeout(800)
    page.wait_for_timeout(500)
    _nudge_focus_away_from_search_without_hitting_messages(page)


def _nudge_focus_away_from_search_without_hitting_messages(page: Page) -> None:
    """Never click #pane-side or the message area — that switches the open chat or hits links.

    A fixed click on the chat list was selecting whatever row sits at that Y (e.g. "52nd Entry").
    Only blur focus and, if a search dialog is still visible, Escape to dismiss it.
    """
    try:
        page.evaluate(
            """() => {
              const a = document.activeElement;
              if (a && a.blur) a.blur();
            }"""
        )
    except Exception:
        pass
    page.wait_for_timeout(120)
    for _ in range(8):
        dlg = page.locator('[role="dialog"]')
        try:
            if dlg.count() == 0:
                break
            if not dlg.first.is_visible():
                break
        except Exception:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(220)


def scrape_one_group(
    page: Page, spec: dict[str, Any], default_region: str | None
) -> dict[str, Any]:
    group_title = (spec.get("title") or "").strip()
    data_id = spec.get("data_id")
    if data_id is not None:
        data_id = str(data_id).strip() or None
    err: str | None = None
    members: list[dict[str, Any]] = []
    header_seen = ""
    try:
        open_group_chat_smart(page, group_title, data_id)
        header_seen = _read_main_header_title(page)
        if header_seen and not _titles_match_loose(header_seen, group_title):
            err = (
                "header_title_mismatch: expected %r opened %r"
                % (group_title, header_seen)
            )
        _click_chat_header(page)
        page.wait_for_timeout(1400)
        if not _wait_group_info_drawer(page, timeout_ms=25000):
            err = (err + "; " if err else "") + "group_info_drawer_not_visible"
        else:
            page.wait_for_timeout(400)
        members = _collect_members_after_drawer(page, default_region)
        if not members:
            err = (err + "; " if err else "") + "no_member_rows_found"
    except Exception as ex:
        err = str(ex)
    close_panels(page)
    _reset_search_and_dialogs(page)
    return {
        "group_title": group_title,
        "data_id": data_id,
        "opened_header_title": header_seen,
        "members": members,
        "error": err,
    }


def export_group_batch_json(path: Path, batch: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(batch, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    return (s or "group")[:80]


def run_group_members_batch(
    groups: list[dict[str, Any]],
    user_data_dir: Path,
    headless: bool,
    default_region: str | None,
    out_path: Path,
    split_files: bool,
    split_dir: Path | None,
    pause_min_s: float,
    pause_max_s: float,
    max_groups: int | None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    todo = groups if max_groups is None else groups[: max(0, max_groups)]
    with sync_playwright() as p:
        ctx = launch_context(p, user_data_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            wait_for_chat_list(page)
            for spec in todo:
                title = spec.get("title") or ""
                print("Group:", title)
                one = scrape_one_group(page, spec, default_region)
                results.append(one)
                print(
                    "  members=%d error=%s"
                    % (len(one["members"]), one.get("error") or "none")
                )
                time.sleep(random.uniform(pause_min_s, pause_max_s))
        finally:
            ctx.close()

    export_group_batch_json(out_path, results)
    print("Wrote", out_path)

    if split_files and results:
        d = split_dir or out_path.parent
        d.mkdir(parents=True, exist_ok=True)
        for one in results:
            slug = _slug(one["group_title"])
            fp = d / ("group_members_%s.json" % slug)
            export_group_batch_json(fp, [one])
            print("Wrote", fp)

    return results
