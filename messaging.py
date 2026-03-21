"""
Message sending and delay logic module.
BulkSender runs in a QThread to keep the GUI responsive and emits
progress/status signals back to the main window.
"""

import random
import time
import asyncio

from PySide6.QtCore import QThread, Signal

from utils import log_event, handle_error
from whatsapp_automation import WhatsAppBot


def random_delay(min_sec: int = 10, max_sec: int = 30) -> int:
    """Sleep for a random duration between min_sec and max_sec. Returns the delay used."""
    delay = random.randint(min_sec, max_sec)
    time.sleep(delay)
    return delay


class BulkSender(QThread):
    """
    Background thread that iterates over a contact list and sends
    messages via WhatsApp Web automation.

    Signals:
        progress(int, int)          — (current_index, total)
        status(str, str, str)       — (phone, status_emoji, detail)
        finished_all(int, int)      — (success_count, fail_count)
        log_message(str)            — free-form log line
        error(str)                  — critical error
    """

    progress = Signal(int, int)
    status = Signal(str, str, str)
    finished_all = Signal(int, int)
    log_message = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        bot: WhatsAppBot,
        contacts: list[dict],
        message: str,
        media_path: str | None = None,
        min_delay: int = 10,
        max_delay: int = 30,
        parent=None,
    ):
        super().__init__(parent)
        self.bot = bot
        self.contacts = contacts
        self.message = message
        self.media_path = media_path
        self.min_delay = min_delay
        self.max_delay = max_delay

        self._paused = False
        self._stopped = False

    # ----- lifecycle controls -----

    def pause(self):
        self._paused = True
        self.log_message.emit("⏸  Sending paused.")
        log_event("Bulk sending paused.")

    def resume(self):
        self._paused = False
        self.log_message.emit("▶  Sending resumed.")
        log_event("Bulk sending resumed.")

    def stop(self):
        self._stopped = True
        self.log_message.emit("⏹  Sending stopped by user.")
        log_event("Bulk sending stopped by user.")

    # ----- main thread body -----

    def run(self):  # noqa: C901
        total = len(self.contacts)
        success = 0
        fail = 0
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            for idx, contact in enumerate(self.contacts):
                if self._stopped:
                    break

                # Pause loop
                while self._paused and not self._stopped:
                    time.sleep(0.5)
                if self._stopped:
                    break

                phone = contact.get("phone", "")
                name = contact.get("name", phone)
                self.progress.emit(idx + 1, total)
                self.status.emit(phone, "⏳", f"Sending to {name}…")
                self.log_message.emit(f"[{idx + 1}/{total}] Sending to {name} ({phone})…")

                try:
                    ok = loop.run_until_complete(
                        self.bot.send_message(phone, self.message, self.media_path)
                    )
                    if ok:
                        success += 1
                        self.status.emit(phone, "✅", f"Sent to {name}")
                        self.log_message.emit(f"  ✅ Sent to {name}")
                    else:
                        fail += 1
                        self.status.emit(phone, "❌", f"Failed: {name}")
                        self.log_message.emit(f"  ❌ Failed to send to {name}")
                except Exception as exc:
                    fail += 1
                    err_msg = handle_error(exc, f"Error sending to {name}")
                    self.status.emit(phone, "❌", err_msg)
                    self.log_message.emit(f"  ❌ {err_msg}")

                # Random delay between sends (skip after last contact)
                if idx < total - 1 and not self._stopped:
                    delay = random.randint(self.min_delay, self.max_delay)
                    self.log_message.emit(f"  ⏱  Waiting {delay}s before next send…")
                    for _ in range(delay * 2):
                        if self._stopped:
                            break
                        while self._paused and not self._stopped:
                            time.sleep(0.5)
                        time.sleep(0.5)

        except Exception as exc:
            err_msg = handle_error(exc, "Bulk send critical error")
            self.error.emit(err_msg)
        finally:
            loop.close()

        self.finished_all.emit(success, fail)
        self.log_message.emit(
            f"\n{'='*40}\nFinished — ✅ {success}  ❌ {fail}  Total: {total}"
        )
        log_event(f"Bulk send complete: {success} sent, {fail} failed, {total} total.")
