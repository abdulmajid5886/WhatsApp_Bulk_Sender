#!/usr/bin/env python3
"""CLI: Google Contacts fetch, WhatsApp scrape, group members, merge, verify, send.

Setup: python -m venv .venv && source .venv/bin/activate
        pip install -r requirements.txt && playwright install chromium
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


def _default_credentials() -> Path:
    return Path(os.environ.get("WA_SENDER_CREDENTIALS", "credentials.json"))


def _default_token() -> Path:
    return Path(os.environ.get("WA_SENDER_TOKEN", "token.pickle"))


def _default_wa_session() -> Path:
    return Path(os.environ.get("WA_SENDER_SESSION", ".wa_session"))


def _deps_import_error_hint() -> None:
    exe = sys.executable
    print(
        "A required Python package is missing for this interpreter:\n"
        f"  {exe}\n\n"
        "Install everything from the project directory:\n"
        "  python -m pip install -r requirements.txt\n\n"
        "If you use a venv, `python` must be that venv’s binary:\n"
        "  which python\n"
        "  python -c \"import sys; print(sys.executable)\"\n"
        "Or run:  .venv/bin/python -m pip install -r requirements.txt\n",
        file=sys.stderr,
    )


def cmd_google_auth(args: argparse.Namespace) -> None:
    try:
        from google_contacts import run_google_auth
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e
    run_google_auth(args.credentials, args.token, force_reauth=args.force_reauth)


def cmd_fetch_google(args: argparse.Namespace) -> None:
    try:
        from google_contacts import export_google_contacts

        export_google_contacts(
            args.credentials,
            args.token,
            out_json=args.out_json,
            out_csv=args.out_csv,
            default_region=args.default_region,
            force_reauth=args.force_reauth,
        )
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e


def cmd_scrape_whatsapp(args: argparse.Namespace) -> None:
    try:
        from whatsapp_automation import run_scrape

        run_scrape(
            user_data_dir=args.session,
            out_json=args.out,
            headless=args.headless,
            default_region=args.default_region,
        )
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e


def cmd_group_members(args: argparse.Namespace) -> None:
    try:
        from whatsapp_groups import load_group_names, load_groups_spec, run_group_members_batch
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e

    if args.groups_json and args.groups_file:
        print("Use only one of --groups-file or --groups-json.", file=sys.stderr)
        sys.exit(1)
    if not args.groups_json and not args.groups_file:
        print(
            "Provide --groups-json (recommended) or --groups-file. "
            "Discover groups first:  python main.py list-whatsapp-groups --out whatsapp_groups.json",
            file=sys.stderr,
        )
        sys.exit(1)

    groups: list[dict[str, Any]] = []
    if args.groups_json:
        if not args.groups_json.exists():
            print("groups JSON not found:", args.groups_json.resolve(), file=sys.stderr)
            sys.exit(1)
        groups = load_groups_spec(args.groups_json)
        if not groups:
            print("No groups in JSON (need title per item).", file=sys.stderr)
            sys.exit(1)
    else:
        assert args.groups_file is not None
        if not args.groups_file.exists():
            p = args.groups_file.resolve()
            print("groups file not found:", p, file=sys.stderr)
            print(
                "Create it in the project folder with one WhatsApp group title per line, e.g.:",
                file=sys.stderr,
            )
            print("  cp groups.example.txt groups.txt", file=sys.stderr)
            print(
                "  # or: python main.py list-whatsapp-groups --out whatsapp_groups.json",
                file=sys.stderr,
            )
            sys.exit(1)
        names = load_group_names(args.groups_file)
        if not names:
            print("No group names in file (non-empty, non-# lines).", file=sys.stderr)
            sys.exit(1)
        groups = [{"title": n, "data_id": None} for n in names]

    if args.dry_run:
        print("%d group(s) would be scraped (in order):" % len(groups))
        for i, g in enumerate(groups, 1):
            did = g.get("data_id")
            extra = ("  [%s]" % did) if did else ""
            print("  %d. %s%s" % (i, g.get("title", ""), extra))
        return
    run_group_members_batch(
        groups=groups,
        user_data_dir=args.session,
        headless=args.headless,
        default_region=args.default_region,
        out_path=args.out,
        split_files=args.split_files,
        split_dir=args.split_dir,
        pause_min_s=args.pause_min,
        pause_max_s=args.pause_max,
        max_groups=args.max_groups,
    )


def cmd_list_whatsapp_groups(args: argparse.Namespace) -> None:
    try:
        from whatsapp_groups import run_discover_groups
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e

    run_discover_groups(
        user_data_dir=args.session,
        headless=args.headless,
        out_path=args.out,
        max_scrolls=args.max_scrolls,
        probe_out=args.probe_out,
        verbose=args.verbose,
        use_groups_filter=not args.no_groups_tab,
    )


def cmd_merge(args: argparse.Namespace) -> None:
    try:
        from utils import (
            apply_allow_block,
            load_recipients_json,
            merge_recipient_lists,
            save_recipients_csv,
            save_recipients_json,
        )
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e

    inputs: list[list[dict[str, Any]]] = []
    if args.google.exists():
        inputs.append(load_recipients_json(args.google))
    if args.whatsapp.exists():
        inputs.append(load_recipients_json(args.whatsapp))
    if not inputs:
        print("No inputs; provide --google and/or --whatsapp", file=sys.stderr)
        sys.exit(1)
    merged = merge_recipient_lists(*inputs, default_region=args.default_region)
    merged = apply_allow_block(
        merged,
        allow_path=args.allow,
        block_path=args.block,
        default_region=args.default_region,
    )
    save_recipients_json(args.out, merged)
    print("Wrote", args.out, "(%d recipients)" % len(merged))
    if args.out_csv:
        save_recipients_csv(args.out_csv, merged)
        print("Wrote", args.out_csv)


def cmd_send(args: argparse.Namespace) -> None:
    try:
        from utils import apply_allow_block, load_recipients_json
        from whatsapp_automation import run_send_batch
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e

    recs = load_recipients_json(args.recipients)
    recs = apply_allow_block(
        recs,
        allow_path=args.allow,
        block_path=args.block,
        default_region=args.default_region,
    )
    if not args.message and not args.media:
        print("Provide --message and/or --media", file=sys.stderr)
        sys.exit(1)
    msg = args.message or ""
    media = Path(args.media) if args.media else None
    run_send_batch(
        recipients=recs,
        message=msg,
        media_path=media,
        user_data_dir=args.session,
        headless=args.headless,
        dry_run=args.dry_run,
        delay_min_s=args.delay_min,
        delay_max_s=args.delay_max,
        max_messages=args.max_messages,
        log_path=args.log,
    )


def cmd_verify_whatsapp(args: argparse.Namespace) -> None:
    try:
        from utils import apply_allow_block, load_recipients_json
        from whatsapp_verify import run_verify_batch
    except ImportError as e:
        _deps_import_error_hint()
        raise SystemExit(1) from e

    recs = load_recipients_json(args.recipients)
    recs = apply_allow_block(
        recs,
        allow_path=args.allow,
        block_path=args.block,
        default_region=args.default_region,
    )
    run_verify_batch(
        recipients=recs,
        user_data_dir=args.session,
        headless=args.headless,
        delay_min_s=args.delay_min,
        delay_max_s=args.delay_max,
        http_only=args.http_only,
        dom_all=args.dom_all,
        out_path=args.out,
        log_path=args.log,
        max_numbers=args.max_numbers,
    )


def _region_parent() -> argparse.ArgumentParser:
    """Shared --default-region (must live on subparsers; parent-only flags must appear before the subcommand)."""
    rp = argparse.ArgumentParser(add_help=False)
    rp.add_argument(
        "--default-region",
        default=os.environ.get("WA_DEFAULT_REGION"),
        help="Default region for parsing numbers without +country (e.g. US, GB)",
    )
    return rp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WhatsApp Web helper + Google Contacts")
    region_p = _region_parent()
    sub = p.add_subparsers(dest="command", required=True)

    ga = sub.add_parser("google-auth", help="Run Google OAuth; save token.pickle")
    ga.add_argument("--credentials", type=Path, default=_default_credentials())
    ga.add_argument("--token", type=Path, default=_default_token())
    ga.add_argument("--force-reauth", action="store_true")
    ga.set_defaults(func=cmd_google_auth)

    fg = sub.add_parser(
        "fetch-google",
        parents=[region_p],
        help="Export Google contact phones to JSON/CSV",
    )
    fg.add_argument("--credentials", type=Path, default=_default_credentials())
    fg.add_argument("--token", type=Path, default=_default_token())
    fg.add_argument("--out-json", type=Path, default=Path("recipients_google.json"))
    fg.add_argument("--out-csv", type=Path, default=None)
    fg.add_argument("--force-reauth", action="store_true")
    fg.set_defaults(func=cmd_fetch_google)

    sw = sub.add_parser(
        "scrape-whatsapp",
        parents=[region_p],
        help="Scrape sidebar chats; save JSON",
    )
    sw.add_argument("--session", type=Path, default=_default_wa_session())
    sw.add_argument("--out", type=Path, default=Path("recipients_whatsapp.json"))
    sw.add_argument("--headless", action="store_true")
    sw.set_defaults(func=cmd_scrape_whatsapp)

    gm = sub.add_parser(
        "group-members",
        parents=[region_p],
        help="Open each group, scrape participant list to JSON (prefer --groups-json from list-whatsapp-groups)",
    )
    gm.add_argument(
        "--groups-json",
        type=Path,
        default=None,
        help="From list-whatsapp-groups: titles + data_id per group (opens correct sidebar row)",
    )
    gm.add_argument(
        "--groups-file",
        type=Path,
        default=None,
        help="One group chat title per line (# comments ok); no data_id (search-only open)",
    )
    gm.add_argument("--session", type=Path, default=_default_wa_session())
    gm.add_argument("--headless", action="store_true")
    gm.add_argument("--out", type=Path, default=Path("group_members.json"))
    gm.add_argument(
        "--split-files",
        action="store_true",
        help="Also write group_members_<slug>.json per group",
    )
    gm.add_argument(
        "--split-dir",
        type=Path,
        default=None,
        help="Directory for --split-files (default: same as --out parent)",
    )
    gm.add_argument(
        "--pause-min",
        type=float,
        default=2.0,
        help="Seconds between groups (min)",
    )
    gm.add_argument(
        "--pause-max",
        type=float,
        default=5.0,
        help="Seconds between groups (max)",
    )
    gm.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print group names from file; do not open browser",
    )
    gm.add_argument(
        "--max-groups",
        type=int,
        default=None,
        help="Scrape only the first N groups from the list (debug)",
    )
    gm.set_defaults(func=cmd_group_members)

    lg = sub.add_parser(
        "list-whatsapp-groups",
        parents=[region_p],
        help="Scroll chat list and save all group chats (@g.us) to JSON",
    )
    lg.add_argument("--session", type=Path, default=_default_wa_session())
    lg.add_argument("--headless", action="store_true")
    lg.add_argument(
        "--out",
        type=Path,
        default=Path("whatsapp_groups.json"),
        help="Written as { groups: [ { title, data_id } ], ... }",
    )
    lg.add_argument(
        "--max-scrolls",
        type=int,
        default=80,
        help="How many times to scroll the sidebar to load more chats",
    )
    lg.add_argument(
        "--probe-out",
        type=Path,
        default=None,
        help="Write sidebar DOM summary (data_id_samples, row counts) for debugging 0 groups",
    )
    lg.add_argument(
        "--verbose",
        action="store_true",
        help="Print diagnosis and row/heuristic counts to stderr",
    )
    lg.add_argument(
        "--no-groups-tab",
        action="store_true",
        help="Do not click the sidebar Groups chip (heuristics / fallback only)",
    )
    lg.set_defaults(func=cmd_list_whatsapp_groups)

    mg = sub.add_parser(
        "merge",
        parents=[region_p],
        help="Merge Google + WhatsApp JSON; dedupe E.164",
    )
    mg.add_argument("--google", type=Path, default=Path("recipients_google.json"))
    mg.add_argument("--whatsapp", type=Path, default=Path("recipients_whatsapp.json"))
    mg.add_argument("--out", type=Path, default=Path("recipients_merged.json"))
    mg.add_argument("--out-csv", type=Path, default=None)
    mg.add_argument("--allow", type=Path, default=None, help="Only these numbers (one per line)")
    mg.add_argument("--block", type=Path, default=None, help="Skip these numbers (one per line)")
    mg.set_defaults(func=cmd_merge)

    sd = sub.add_parser(
        "send",
        parents=[region_p],
        help="Send text/media to each recipient via WhatsApp Web",
    )
    sd.add_argument("--recipients", type=Path, default=Path("recipients_merged.json"))
    sd.add_argument("--message", type=str, default="", help="Text body or media caption")
    sd.add_argument("--media", type=Path, default=None)
    sd.add_argument("--session", type=Path, default=_default_wa_session())
    sd.add_argument("--headless", action="store_true")
    sd.add_argument("--dry-run", action="store_true")
    sd.add_argument("--delay-min", type=float, default=10.0)
    sd.add_argument("--delay-max", type=float, default=20.0)
    sd.add_argument("--max-messages", type=int, default=None)
    sd.add_argument("--log", type=Path, default=Path("send.log"))
    sd.add_argument("--allow", type=Path, default=None)
    sd.add_argument("--block", type=Path, default=None)
    sd.set_defaults(func=cmd_send)

    vw = sub.add_parser(
        "verify-whatsapp",
        parents=[region_p],
        help="Heuristic check: HTTP (api.whatsapp.com / wa.me) + optional Web DOM",
    )
    vw.add_argument("--recipients", type=Path, default=Path("recipients_merged.json"))
    vw.add_argument("--out", type=Path, default=Path("recipients_verified.json"))
    vw.add_argument("--session", type=Path, default=_default_wa_session())
    vw.add_argument("--headless", action="store_true")
    vw.add_argument("--delay-min", type=float, default=8.0)
    vw.add_argument("--delay-max", type=float, default=20.0)
    vw.add_argument(
        "--http-only",
        action="store_true",
        help="Skip Playwright; HTTP heuristics only (many unknown)",
    )
    vw.add_argument(
        "--dom-all",
        action="store_true",
        help="Run DOM check for every number (slow)",
    )
    vw.add_argument("--log", type=Path, default=Path("verify.log"))
    vw.add_argument("--allow", type=Path, default=None)
    vw.add_argument("--block", type=Path, default=None)
    vw.add_argument("--max-numbers", type=int, default=None)
    vw.set_defaults(func=cmd_verify_whatsapp)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "default_region", None) == "":
        args.default_region = None
    args.func(args)


if __name__ == "__main__":
    main()
