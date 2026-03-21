"""Google People API: OAuth and contact phone export."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ("https://www.googleapis.com/auth/contacts.readonly",)


def _load_credentials(
    credentials_path: Path, token_path: Path, force_reauth: bool
) -> Credentials:
    creds: Credentials | None = None
    if token_path.exists() and not force_reauth:
        try:
            with token_path.open("rb") as f:
                creds = pickle.load(f)
        except Exception:
            creds = None
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with token_path.open("wb") as f:
                pickle.dump(creds, f)
        else:
            creds = None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path), list(SCOPES)
        )
        creds = flow.run_local_server(port=0)
        with token_path.open("wb") as f:
            pickle.dump(creds, f)
    return creds


def run_google_auth(
    credentials_path: Path, token_path: Path, force_reauth: bool = False
) -> None:
    _load_credentials(credentials_path, token_path, force_reauth)
    print("Google OAuth OK; token saved to", token_path)


def fetch_google_contacts(
    credentials_path: Path,
    token_path: Path,
    default_region: str | None,
    force_reauth: bool = False,
) -> list[dict[str, Any]]:
    from utils import normalize_phone

    creds = _load_credentials(credentials_path, token_path, force_reauth)
    service = build("people", "v1", credentials=creds, cache_discovery=False)
    connections: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        req = (
            service.people()
            .connections()
            .list(
                resourceName="people/me",
                personFields="names,phoneNumbers",
                pageSize=1000,
                pageToken=page_token,
            )
        )
        result = req.execute()
        connections.extend(result.get("connections", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    recipients: list[dict[str, Any]] = []
    for person in connections:
        names = person.get("names") or []
        display = ""
        if names:
            display = (names[0].get("displayName") or "").strip()
        for ph in person.get("phoneNumbers") or []:
            raw = (ph.get("value") or "").strip()
            e164 = normalize_phone(raw, default_region)
            if not e164:
                continue
            recipients.append(
                {
                    "e164": e164,
                    "name": display,
                    "source": "google",
                    "raw_phone": raw,
                }
            )
    return recipients


def export_google_contacts(
    credentials_path: Path,
    token_path: Path,
    out_json: Path | None,
    out_csv: Path | None,
    default_region: str | None,
    force_reauth: bool,
) -> list[dict[str, Any]]:
    from utils import save_recipients_csv, save_recipients_json

    recs = fetch_google_contacts(
        credentials_path, token_path, default_region, force_reauth
    )
    if out_json:
        save_recipients_json(out_json, recs)
        print("Wrote", out_json, "(%d rows)" % len(recs))
    if out_csv:
        save_recipients_csv(out_csv, recs)
        print("Wrote", out_csv, "(%d rows)" % len(recs))
    return recs
