"""
Utility functions for WhatsApp Bulk Sender.
Includes logging, error handling, phone sanitization, and CSV import/export.
"""

import csv
import logging
import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")

logger = logging.getLogger("WhatsAppBulkSender")
logger.setLevel(logging.DEBUG)

# File handler — append with timestamps
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

# Console handler
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

logger.addHandler(_fh)
logger.addHandler(_ch)


def log_event(event: str, level: str = "info"):
    """Log an event at the given level (info, warning, error, debug)."""
    fn = getattr(logger, level, logger.info)
    fn(event)


def handle_error(error: Exception, context: str = "") -> str:
    """Log an exception and return a user-friendly message."""
    msg = f"{context}: {error}" if context else str(error)
    logger.error(msg, exc_info=True)
    return f"Error — {msg}"


# Default country code for local numbers (Pakistan = 92)
DEFAULT_COUNTRY_CODE = "92"


def sanitize_phone(phone: str) -> str:
    """
    Normalize a phone number string:
    - Strip whitespace, dashes, parentheses, dots
    - ONLY for Pakistani local mobile numbers (03XXXXXXXXX, 11 digits): convert to +923XXXXXXXXX
    - Fix double-prefixed: +9203XXXXXXX → +923XXXXXXX (strip extra 0)
    - Leave all international numbers (UK +44, Malaysia +60, etc.) untouched
    Returns the cleaned number or empty string if invalid.
    """
    if not phone:
        return ""
    cleaned = re.sub(r"[\s\-\(\)\.]", "", phone.strip())
    if not cleaned:
        return ""

    # Ensure leading '+'
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    # Fix: +9203XXXXXXX → +923XXXXXXX (strip extra 0 after +92, only if followed by 3)
    # This catches Pakistan mobile numbers stored as +920 3XX XXXXXXX
    if cleaned.startswith("+920") and len(cleaned) > 5 and cleaned[4] == "3":
        cleaned = "+92" + cleaned[4:]

    # Local Pakistani mobile: +03XXXXXXXXX → +923XXXXXXXXX
    # Only 03XX numbers (Pakistani mobile), exactly 11 digits after removing leading 0
    if cleaned.startswith("+0") and cleaned[2:3] == "3" and len(cleaned) == 12:
        cleaned = "+" + DEFAULT_COUNTRY_CODE + cleaned[2:]

    # Must contain only digits after the leading '+'
    if not re.match(r"^\+\d{7,15}$", cleaned):
        return ""
    return cleaned


# ---------------------------------------------------------------------------
# CSV import / export
# ---------------------------------------------------------------------------

def export_contacts_csv(contacts: list[dict], filepath: str) -> str:
    """
    Export a list of contact dicts (keys: name, phone) to a CSV file.
    Returns the output file path.
    """
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "phone"])
        writer.writeheader()
        for c in contacts:
            writer.writerow({"name": c.get("name", ""), "phone": c.get("phone", "")})
    log_event(f"Exported {len(contacts)} contacts to {filepath}")
    return filepath


def import_contacts_csv(filepath: str) -> list[dict]:
    """
    Import contacts from a CSV file.  Expected columns: name, phone.
    Returns list of dicts.
    """
    contacts = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = sanitize_phone(row.get("phone", ""))
            if phone:
                contacts.append({"name": row.get("name", "").strip(), "phone": phone})
    log_event(f"Imported {len(contacts)} contacts from {filepath}")
    return contacts


def read_log_file() -> str:
    """Read and return the full contents of the application log file."""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""
