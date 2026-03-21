"""
Google Contacts integration module.
Handles OAuth2 authentication and fetching phone numbers via the People API.
"""

import os
import pickle

from utils import log_event, handle_error, sanitize_phone


# ---------------------------------------------------------------------------
# OAuth2 Authentication
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.pickle")
CREDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")


def authenticate_google():
    """
    Handle OAuth2 authentication for Google Contacts API.
    Returns a credentials object, or None on failure.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None

    # Load cached credentials
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "rb") as token:
                creds = pickle.load(token)
        except Exception as exc:
            log_event(f"Failed to load cached token: {exc}", "warning")

    # Refresh or start new OAuth flow
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(CREDS_PATH):
                    log_event(
                        "credentials.json not found. Download it from Google Cloud Console.",
                        "error",
                    )
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
            # Persist for next run
            with open(TOKEN_PATH, "wb") as token:
                pickle.dump(creds, token)
            log_event("Google OAuth2 authentication successful.")
        except Exception as exc:
            handle_error(exc, "Google OAuth2 authentication failed")
            return None

    return creds


# ---------------------------------------------------------------------------
# Fetch Contacts
# ---------------------------------------------------------------------------

def fetch_google_contacts(creds=None) -> list[dict]:
    """
    Fetch all contacts with phone numbers from Google Contacts.

    Args:
        creds: google.oauth2.credentials.Credentials (if None, will authenticate)

    Returns:
        List of dicts: [{"name": "...", "phone": "+1234567890"}, ...]
    """
    from googleapiclient.discovery import build

    if creds is None:
        creds = authenticate_google()
    if creds is None:
        return []

    try:
        service = build("people", "v1", credentials=creds)
        contacts: list[dict] = []
        next_page_token = None

        while True:
            results = (
                service.people()
                .connections()
                .list(
                    resourceName="people/me",
                    pageSize=1000,
                    personFields="names,phoneNumbers",
                    pageToken=next_page_token,
                )
                .execute()
            )

            connections = results.get("connections", [])
            for person in connections:
                names = person.get("names", [])
                phones = person.get("phoneNumbers", [])
                name = names[0].get("displayName", "Unknown") if names else "Unknown"

                for phone_entry in phones:
                    raw = phone_entry.get("value", "")
                    cleaned = sanitize_phone(raw)
                    if cleaned:
                        contacts.append({"name": name, "phone": cleaned})

            next_page_token = results.get("nextPageToken")
            if not next_page_token:
                break

        log_event(f"Fetched {len(contacts)} contacts from Google.")
        return contacts

    except Exception as exc:
        handle_error(exc, "Failed to fetch Google Contacts")
        return []
