"""Phone normalization, recipient lists, allow/block filters."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

import phonenumbers

PHONE_LIKE = re.compile(r"\+?\d[\d\s\-().]{6,}\d")


def normalize_phone(raw: str, default_region: str | None = None) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region or None)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
    except phonenumbers.NumberParseException:
        return None


def extract_phone_candidates(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0) for m in PHONE_LIKE.finditer(text)]


def load_recipients_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}")
    return data


def load_recipients_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def save_recipients_json(path: Path, recipients: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(recipients, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_recipients_csv(path: Path, recipients: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not recipients:
        path.write_text("e164,name,source\n", encoding="utf-8")
        return
    fields = sorted({k for r in recipients for k in r.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(recipients)


def load_phone_lines(path: Path) -> set[str]:
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def dedupe_by_e164(
    records: Iterable[dict[str, Any]],
    default_region: str | None = None,
) -> list[dict[str, Any]]:
    """Ensure each record has e164; merge duplicates keeping longest name."""
    by_e164: dict[str, dict[str, Any]] = {}
    for r in records:
        e164 = r.get("e164")
        if not e164:
            raw = r.get("phone") or r.get("number") or ""
            e164 = normalize_phone(str(raw), default_region)
        if not e164:
            continue
        name = (r.get("name") or "").strip()
        source = r.get("source") or "unknown"
        if e164 not in by_e164:
            by_e164[e164] = {"e164": e164, "name": name, "source": source}
        else:
            cur = by_e164[e164]
            if len(name) > len(cur.get("name") or ""):
                cur["name"] = name
            if source != cur.get("source"):
                cur["source"] = f"{cur.get('source')},{source}"
    return sorted(by_e164.values(), key=lambda x: x["e164"])


def apply_allow_block(
    recipients: list[dict[str, Any]],
    allow_path: Path | None,
    block_path: Path | None,
    default_region: str | None = None,
) -> list[dict[str, Any]]:
    blocked: set[str] = set()
    if block_path and block_path.exists():
        for line in load_phone_lines(block_path):
            n = normalize_phone(line, default_region) or line
            blocked.add(n)

    if allow_path and allow_path.exists():
        allowed_raw = load_phone_lines(allow_path)
        allowed_e164: set[str] = set()
        for line in allowed_raw:
            n = normalize_phone(line, default_region)
            if n:
                allowed_e164.add(n)
        return [r for r in recipients if r.get("e164") in allowed_e164]

    if not blocked:
        return recipients
    return [r for r in recipients if r.get("e164") not in blocked]


def merge_recipient_lists(
    *lists: list[dict[str, Any]],
    default_region: str | None = None,
) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for lst in lists:
        flat.extend(lst)
    return dedupe_by_e164(flat, default_region=default_region)
