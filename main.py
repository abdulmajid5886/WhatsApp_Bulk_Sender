#!/usr/bin/env python3
"""CLI: Google Contacts fetch, WhatsApp scrape, merge, send.

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
    sd.add_argument("--delay-min", type=float, default=8.0)
    sd.add_argument("--delay-max", type=float, default=20.0)
    sd.add_argument("--max-messages", type=int, default=None)
    sd.add_argument("--log", type=Path, default=Path("send.log"))
    sd.add_argument("--allow", type=Path, default=None)
    sd.add_argument("--block", type=Path, default=None)
    sd.set_defaults(func=cmd_send)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "default_region", None) == "":
        args.default_region = None
    args.func(args)


if __name__ == "__main__":
    main()
