"""
App entry point and GUI setup for WhatsApp Bulk Sender.
PyQt5 tabbed interface with 4 tabs:
  1. Authentication & Setup
  2. Contacts
  3. Compose & Send
  4. Logs
"""

import sys
import os
import asyncio
import threading

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit, QComboBox, QFileDialog,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QSpinBox, QGroupBox, QMessageBox, QSplitter, QAbstractItemView,
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QFont, QColor, QIcon, QPixmap

from utils import log_event, handle_error, export_contacts_csv, import_contacts_csv, read_log_file
from google_contacts import authenticate_google, fetch_google_contacts
from whatsapp_automation import WhatsAppBot
from messaging import BulkSender


# ---------------------------------------------------------------------------
# Colour palette & style
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
}
QTabWidget::pane {
    border: 1px solid #16213e;
    background: #1a1a2e;
    border-radius: 8px;
}
QTabBar::tab {
    background: #16213e;
    color: #a8b2d1;
    padding: 10px 22px;
    margin: 2px;
    border-radius: 6px 6px 0 0;
    font-weight: bold;
    font-size: 13px;
}
QTabBar::tab:selected {
    background: #0f3460;
    color: #e94560;
}
QLabel {
    color: #ccd6f6;
    font-size: 13px;
}
QPushButton {
    background-color: #0f3460;
    color: #e6e6e6;
    border: none;
    padding: 9px 20px;
    border-radius: 6px;
    font-weight: bold;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #e94560;
    color: #fff;
}
QPushButton:disabled {
    background-color: #2a2a4a;
    color: #555;
}
QLineEdit, QTextEdit, QSpinBox, QComboBox {
    background-color: #16213e;
    color: #ccd6f6;
    border: 1px solid #0f3460;
    border-radius: 5px;
    padding: 6px;
    font-size: 13px;
}
QTableWidget {
    background-color: #16213e;
    color: #ccd6f6;
    gridline-color: #0f3460;
    border: 1px solid #0f3460;
    border-radius: 5px;
    font-size: 12px;
}
QTableWidget::item:selected {
    background-color: #0f3460;
}
QHeaderView::section {
    background-color: #0f3460;
    color: #e94560;
    padding: 6px;
    border: none;
    font-weight: bold;
}
QProgressBar {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 5px;
    text-align: center;
    color: #ccd6f6;
    height: 22px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #e94560, stop:1 #0f3460);
    border-radius: 4px;
}
QGroupBox {
    border: 1px solid #0f3460;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 18px;
    color: #e94560;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}
QCheckBox {
    color: #ccd6f6;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
}
"""


# ---------------------------------------------------------------------------
# Async helper thread for WhatsApp bot operations
# ---------------------------------------------------------------------------

class AsyncWorker(QThread):
    """Generic worker that runs an async coroutine and emits result / error."""
    result_ready = Signal(object)
    error_occurred = Signal(str)
    progress_update = Signal(str)

    def __init__(self, coro_func, *args, parent=None):
        super().__init__(parent)
        self._coro_func = coro_func
        self._args = args

    def _progress_callback(self, msg: str):
        """Called from the async coroutine to relay progress to the GUI thread."""
        self.progress_update.emit(msg)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._coro_func(*self._args))
            self.result_ready.emit(result)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WhatsApp Bulk Sender")
        self.setMinimumSize(920, 640)
        self.setStyleSheet(STYLESHEET)

        # State
        self.google_creds = None
        self.wa_bot: WhatsAppBot | None = None
        self.contacts: list[dict] = []
        self.bulk_sender: BulkSender | None = None
        self._workers: list[AsyncWorker] = []
        self._bot_busy = False  # Prevent concurrent bot operations

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self._build_auth_tab(), "🔐 Auth")
        tabs.addTab(self._build_contacts_tab(), "📇 Contacts")
        tabs.addTab(self._build_compose_tab(), "✉️ Compose & Send")
        tabs.addTab(self._build_logs_tab(), "📋 Logs")
        self.setCentralWidget(tabs)
        self._tabs = tabs
        tabs.currentChanged.connect(self._on_tab_changed)

    # ==================================================================
    # Tab 1 — Authentication
    # ==================================================================
    def _build_auth_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        # --- Google ---
        g_group = QGroupBox("Google Contacts")
        g_layout = QVBoxLayout(g_group)
        self.google_status = QLabel("Status: Not connected")
        self.google_btn = QPushButton("Connect Google Account")
        self.google_btn.clicked.connect(self._connect_google)
        g_layout.addWidget(self.google_status)
        g_layout.addWidget(self.google_btn)
        layout.addWidget(g_group)

        # --- WhatsApp ---
        wa_group = QGroupBox("WhatsApp Web")
        wa_layout = QVBoxLayout(wa_group)
        self.wa_status = QLabel("Status: Not connected")
        self.wa_open_btn = QPushButton("Open WhatsApp Web")
        self.wa_open_btn.clicked.connect(self._open_whatsapp)
        self.wa_check_btn = QPushButton("Check Connection")
        self.wa_check_btn.clicked.connect(self._check_wa_connection)
        self.wa_check_btn.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.wa_open_btn)
        row.addWidget(self.wa_check_btn)
        wa_layout.addWidget(self.wa_status)
        wa_layout.addLayout(row)
        layout.addWidget(wa_group)

        layout.addStretch()
        return w

    def _connect_google(self):
        self.google_status.setText("Status: Authenticating…")
        self.google_btn.setEnabled(False)
        try:
            creds = authenticate_google()
            if creds:
                self.google_creds = creds
                self.google_status.setText("Status: ✅ Connected")
                log_event("Google account connected.")
            else:
                self.google_status.setText("Status: ❌ Failed (see logs)")
        except Exception as exc:
            self.google_status.setText(f"Status: ❌ {exc}")
            handle_error(exc, "Google auth")
        self.google_btn.setEnabled(True)

    def _open_whatsapp(self):
        self.wa_status.setText("Status: Launching browser…")
        self.wa_open_btn.setEnabled(False)
        self.wa_bot = WhatsAppBot()

        worker = AsyncWorker(self._wa_start_sequence)
        worker.result_ready.connect(self._on_wa_started)
        worker.error_occurred.connect(self._on_wa_error)
        self._workers.append(worker)
        worker.start()

    async def _wa_start_sequence(self):
        await self.wa_bot.start(headless=False)
        logged_in = await self.wa_bot.wait_for_login(timeout=120)
        return logged_in

    @Slot(object)
    def _on_wa_started(self, logged_in):
        if logged_in:
            self.wa_status.setText("Status: ✅ Connected")
            log_event("WhatsApp Web connected.")
        else:
            self.wa_status.setText("Status: ⏳ Scan QR code, then click 'Check Connection'")
        self.wa_open_btn.setEnabled(True)
        self.wa_check_btn.setEnabled(True)

    @Slot(str)
    def _on_wa_error(self, err):
        self.wa_status.setText(f"Status: ❌ {err}")
        self.wa_open_btn.setEnabled(True)
        handle_error(Exception(err), "WhatsApp Web launch")

    def _check_wa_connection(self):
        if not self.wa_bot:
            self.wa_status.setText("Status: ❌ Bot not started")
            return
        worker = AsyncWorker(self.wa_bot.is_logged_in)
        worker.result_ready.connect(
            lambda ok: self.wa_status.setText(
                "Status: ✅ Connected" if ok else "Status: ❌ Not logged in"
            )
        )
        self._workers.append(worker)
        worker.start()

    # ==================================================================
    # Tab 2 — Contacts
    # ==================================================================
    def _build_contacts_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Source selector
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Google Contacts", "WhatsApp Contacts", "WhatsApp Group"])
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        src_row.addWidget(self.source_combo)

        # Group selection: combo + load button (visible when source = WhatsApp Group)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(200)
        self.group_combo.setPlaceholderText("Select a group…")
        self.group_combo.setVisible(False)
        src_row.addWidget(self.group_combo)

        self.load_groups_btn = QPushButton("Load Groups")
        self.load_groups_btn.setVisible(False)
        self.load_groups_btn.clicked.connect(self._load_groups)
        src_row.addWidget(self.load_groups_btn)

        self.fetch_btn = QPushButton("Fetch Contacts")
        self.fetch_btn.clicked.connect(self._fetch_contacts)
        src_row.addWidget(self.fetch_btn)
        layout.addLayout(src_row)

        # Status label for progress feedback
        self.fetch_status_lbl = QLabel("")
        self.fetch_status_lbl.setStyleSheet("color: #64ffda; font-size: 12px; padding: 2px 4px;")
        layout.addWidget(self.fetch_status_lbl)

        # Select all + count
        ctrl_row = QHBoxLayout()
        self.select_all_cb = QCheckBox("Select All")
        self.select_all_cb.stateChanged.connect(self._toggle_select_all)
        ctrl_row.addWidget(self.select_all_cb)
        self.contact_count_lbl = QLabel("0 contacts")
        ctrl_row.addWidget(self.contact_count_lbl)
        ctrl_row.addStretch()

        self.import_btn = QPushButton("Import CSV")
        self.import_btn.clicked.connect(self._import_csv)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._export_csv)
        ctrl_row.addWidget(self.import_btn)
        ctrl_row.addWidget(self.export_btn)
        layout.addLayout(ctrl_row)

        # Table
        self.contact_table = QTableWidget(0, 3)
        self.contact_table.setHorizontalHeaderLabels(["✓", "Name", "Phone"])
        self.contact_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.contact_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.contact_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.contact_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.contact_table)

        return w

    def _on_source_changed(self, idx):
        is_group = (idx == 2)
        self.group_combo.setVisible(is_group)
        self.load_groups_btn.setVisible(is_group)

    def _populate_table(self, contacts: list[dict]):
        """Populate the contact table, removing duplicates by phone number."""
        # Deduplicate by phone number (keep first occurrence)
        seen_phones = set()
        unique_contacts = []
        for c in contacts:
            phone = c.get("phone", "")
            name = c.get("name", "")
            key = phone if phone else name  # Use name as key if no phone
            if key and key not in seen_phones:
                seen_phones.add(key)
                unique_contacts.append(c)

        self.contacts = unique_contacts
        self.contact_table.setRowCount(len(unique_contacts))
        for i, c in enumerate(unique_contacts):
            cb = QCheckBox()
            cb.setChecked(True)
            self.contact_table.setCellWidget(i, 0, cb)
            self.contact_table.setItem(i, 1, QTableWidgetItem(c.get("name", "")))
            self.contact_table.setItem(i, 2, QTableWidgetItem(c.get("phone", "")))
        self.contact_count_lbl.setText(f"{len(unique_contacts)} contacts")
        self.select_all_cb.setChecked(True)

    def _toggle_select_all(self, state):
        checked = (state == Qt.CheckState.Checked)
        for i in range(self.contact_table.rowCount()):
            cb = self.contact_table.cellWidget(i, 0)
            if cb:
                cb.setChecked(checked)

    def _get_selected_contacts(self) -> list[dict]:
        selected = []
        for i in range(self.contact_table.rowCount()):
            cb = self.contact_table.cellWidget(i, 0)
            if cb and cb.isChecked():
                name = self.contact_table.item(i, 1).text() if self.contact_table.item(i, 1) else ""
                phone = self.contact_table.item(i, 2).text() if self.contact_table.item(i, 2) else ""
                if phone:
                    selected.append({"name": name, "phone": phone})
        return selected

    def _fetch_contacts(self):
        source = self.source_combo.currentIndex()
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching…")
        self.fetch_status_lbl.setText("⏳ Starting fetch…")

        if source == 0:  # Google
            self._fetch_google()
        elif source == 1:  # WhatsApp contacts
            self._fetch_wa_contacts()
        elif source == 2:  # WhatsApp group
            self._fetch_wa_group()

    def _fetch_google(self):
        try:
            contacts = fetch_google_contacts(self.google_creds)
            self._populate_table(contacts)
        except Exception as exc:
            handle_error(exc, "Fetch Google Contacts")
            QMessageBox.warning(self, "Error", str(exc))
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Contacts")

    def _fetch_wa_contacts(self):
        if not self.wa_bot:
            QMessageBox.warning(self, "Error", "WhatsApp Web not connected. Go to Auth tab first.")
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Fetch Contacts")
            return
        if self._bot_busy:
            QMessageBox.warning(self, "Busy", "Another WhatsApp operation is in progress. Please wait.")
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Fetch Contacts")
            return

        self._bot_busy = True
        worker = AsyncWorker(self.wa_bot.get_all_contacts)
        worker._args = (worker._progress_callback,)
        worker.progress_update.connect(self._on_fetch_progress)
        worker.result_ready.connect(self._on_contacts_fetched)
        worker.error_occurred.connect(self._on_fetch_error)
        self._workers.append(worker)
        worker.start()

    def _fetch_wa_group(self):
        group_name = self.group_combo.currentText().strip()
        if not group_name:
            QMessageBox.warning(self, "Error", "Select a group first. Click 'Load Groups' to populate the list.")
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Fetch Contacts")
            return
        if not self.wa_bot:
            QMessageBox.warning(self, "Error", "WhatsApp Web not connected.")
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Fetch Contacts")
            return
        if self._bot_busy:
            QMessageBox.warning(self, "Busy", "Another WhatsApp operation is in progress. Please wait.")
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("Fetch Contacts")
            return

        self._bot_busy = True
        worker = AsyncWorker(self.wa_bot.get_group_members, group_name)
        worker.result_ready.connect(self._on_contacts_fetched)
        worker.error_occurred.connect(self._on_fetch_error)
        self._workers.append(worker)
        worker.start()

    def _load_groups(self):
        """Load list of WhatsApp groups into the group dropdown."""
        if not self.wa_bot:
            QMessageBox.warning(self, "Error", "WhatsApp Web not connected. Go to Auth tab first.")
            return
        if self._bot_busy:
            QMessageBox.warning(self, "Busy", "Another WhatsApp operation is in progress. Please wait.")
            return

        self._bot_busy = True
        self.load_groups_btn.setEnabled(False)
        self.load_groups_btn.setText("Loading…")

        worker = AsyncWorker(self.wa_bot.get_all_groups)
        worker._args = (worker._progress_callback,)
        worker.progress_update.connect(self._on_load_groups_progress)
        worker.result_ready.connect(self._on_groups_loaded)
        worker.error_occurred.connect(self._on_fetch_error)
        self._workers.append(worker)
        worker.start()

    @Slot(str)
    def _on_fetch_progress(self, msg):
        """Show progress on the status label."""
        self.fetch_status_lbl.setText(msg)

    @Slot(str)
    def _on_load_groups_progress(self, msg):
        """Show progress on the status label and Load Groups button."""
        self.fetch_status_lbl.setText(msg)
        self.load_groups_btn.setText(msg[:25])

    @Slot(object)
    def _on_groups_loaded(self, groups):
        """Populate the group dropdown with the fetched group names."""
        self._bot_busy = False
        self.group_combo.clear()
        if groups:
            self.group_combo.addItems(groups)
            self.group_combo.setCurrentIndex(0)
            self.fetch_status_lbl.setText(f"Loaded {len(groups)} groups. Select one and click Fetch.")
        else:
            self.group_combo.setPlaceholderText("No groups found")
            self.fetch_status_lbl.setText("No groups found in your chat list.")
        self.load_groups_btn.setEnabled(True)
        self.load_groups_btn.setText("Load Groups")

    @Slot(object)
    def _on_contacts_fetched(self, contacts):
        self._bot_busy = False
        self._populate_table(contacts or [])
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Contacts")
        count = len(contacts) if contacts else 0
        self.fetch_status_lbl.setText(f"✅ Fetched {count} contacts.")

    @Slot(str)
    def _on_fetch_error(self, err):
        self._bot_busy = False
        QMessageBox.warning(self, "Fetch Error", err)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Contacts")
        self.load_groups_btn.setEnabled(True)
        self.load_groups_btn.setText("Load Groups")
        self.fetch_status_lbl.setText(f"❌ Error: {err[:60]}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV Files (*.csv)")
        if path:
            try:
                contacts = import_contacts_csv(path)
                self._populate_table(contacts)
            except Exception as exc:
                QMessageBox.warning(self, "Import Error", str(exc))

    def _export_csv(self):
        if not self.contacts:
            QMessageBox.information(self, "Export", "No contacts to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "contacts.csv", "CSV Files (*.csv)")
        if path:
            try:
                export_contacts_csv(self.contacts, path)
                QMessageBox.information(self, "Export", f"Exported to {path}")
            except Exception as exc:
                QMessageBox.warning(self, "Export Error", str(exc))

    # ==================================================================
    # Tab 3 — Compose & Send
    # ==================================================================
    def _build_compose_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # Message editor
        msg_group = QGroupBox("Message")
        msg_layout = QVBoxLayout(msg_group)
        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("Type your message here…")
        self.message_edit.setMaximumHeight(120)
        msg_layout.addWidget(self.message_edit)

        # Media
        media_row = QHBoxLayout()
        self.media_path_lbl = QLabel("No media attached")
        self.media_btn = QPushButton("Attach Media")
        self.media_btn.clicked.connect(self._attach_media)
        self.media_clear_btn = QPushButton("Clear")
        self.media_clear_btn.clicked.connect(self._clear_media)
        media_row.addWidget(self.media_path_lbl)
        media_row.addStretch()
        media_row.addWidget(self.media_btn)
        media_row.addWidget(self.media_clear_btn)
        msg_layout.addLayout(media_row)
        layout.addWidget(msg_group)

        # Delay settings
        delay_group = QGroupBox("Delay Settings")
        delay_layout = QHBoxLayout(delay_group)
        delay_layout.addWidget(QLabel("Min (s):"))
        self.min_delay_spin = QSpinBox()
        self.min_delay_spin.setRange(1, 300)
        self.min_delay_spin.setValue(10)
        delay_layout.addWidget(self.min_delay_spin)
        delay_layout.addWidget(QLabel("Max (s):"))
        self.max_delay_spin = QSpinBox()
        self.max_delay_spin.setRange(1, 300)
        self.max_delay_spin.setValue(30)
        delay_layout.addWidget(self.max_delay_spin)
        delay_layout.addStretch()
        layout.addWidget(delay_group)

        # Controls
        ctrl_row = QHBoxLayout()
        self.send_btn = QPushButton("▶ Start Sending")
        self.send_btn.clicked.connect(self._start_sending)
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self._pause_sending)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.clicked.connect(self._stop_sending)
        self.stop_btn.setEnabled(False)
        ctrl_row.addWidget(self.send_btn)
        ctrl_row.addWidget(self.pause_btn)
        ctrl_row.addWidget(self.stop_btn)
        layout.addLayout(ctrl_row)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Status log
        self.send_log = QTextEdit()
        self.send_log.setReadOnly(True)
        self.send_log.setPlaceholderText("Send log will appear here…")
        layout.addWidget(self.send_log)

        self._media_path = None
        return w

    def _attach_media(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Media", "",
            "Media Files (*.png *.jpg *.jpeg *.gif *.mp4 *.pdf *.doc *.docx);;All Files (*)"
        )
        if path:
            self._media_path = path
            self.media_path_lbl.setText(f"📎 {os.path.basename(path)}")

    def _clear_media(self):
        self._media_path = None
        self.media_path_lbl.setText("No media attached")

    def _start_sending(self):
        if not self.wa_bot:
            QMessageBox.warning(self, "Error", "WhatsApp Web not connected. Go to Auth tab.")
            return

        selected = self._get_selected_contacts()
        if not selected:
            QMessageBox.warning(self, "Error", "No contacts selected. Go to Contacts tab.")
            return

        msg = self.message_edit.toPlainText().strip()
        if not msg and not self._media_path:
            QMessageBox.warning(self, "Error", "Enter a message or attach media.")
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Confirm",
            f"Send to {len(selected)} contacts?\n\nMessage: {msg[:80]}{'…' if len(msg) > 80 else ''}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.send_log.clear()
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)

        self.bulk_sender = BulkSender(
            bot=self.wa_bot,
            contacts=selected,
            message=msg,
            media_path=self._media_path,
            min_delay=self.min_delay_spin.value(),
            max_delay=self.max_delay_spin.value(),
        )
        self.bulk_sender.progress.connect(self._on_send_progress)
        self.bulk_sender.log_message.connect(self._on_send_log)
        self.bulk_sender.status.connect(self._on_send_status)
        self.bulk_sender.finished_all.connect(self._on_send_finished)
        self.bulk_sender.error.connect(self._on_send_error)

        self.send_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.bulk_sender.start()

    def _pause_sending(self):
        if self.bulk_sender:
            if self.bulk_sender._paused:
                self.bulk_sender.resume()
                self.pause_btn.setText("⏸ Pause")
            else:
                self.bulk_sender.pause()
                self.pause_btn.setText("▶ Resume")

    def _stop_sending(self):
        if self.bulk_sender:
            self.bulk_sender.stop()

    @Slot(int, int)
    def _on_send_progress(self, current, total):
        self.progress_bar.setValue(current)

    @Slot(str)
    def _on_send_log(self, msg):
        self.send_log.append(msg)

    @Slot(str, str, str)
    def _on_send_status(self, phone, emoji, detail):
        pass  # Logged via log_message signal

    @Slot(int, int)
    def _on_send_finished(self, success, fail):
        self.send_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("⏸ Pause")
        QMessageBox.information(
            self, "Done",
            f"Sending complete!\n✅ Sent: {success}\n❌ Failed: {fail}"
        )

    @Slot(str)
    def _on_send_error(self, err):
        self.send_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        QMessageBox.critical(self, "Error", err)

    # ==================================================================
    # Tab 4 — Logs
    # ==================================================================
    def _build_logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Courier", 11))
        layout.addWidget(self.log_view)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Logs")
        refresh_btn.clicked.connect(self._refresh_logs)
        export_log_btn = QPushButton("Export Log")
        export_log_btn.clicked.connect(self._export_log)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(export_log_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return w

    def _refresh_logs(self):
        self.log_view.setPlainText(read_log_file())
        # Scroll to bottom
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Log", "app_log.txt", "Text Files (*.txt)")
        if path:
            try:
                with open(path, "w") as f:
                    f.write(self.log_view.toPlainText())
                QMessageBox.information(self, "Export", f"Log exported to {path}")
            except Exception as exc:
                QMessageBox.warning(self, "Error", str(exc))

    def _on_tab_changed(self, idx):
        # Auto-refresh logs when switching to Logs tab
        if idx == 3:
            self._refresh_logs()

    # ==================================================================
    # Cleanup
    # ==================================================================
    def closeEvent(self, event):
        if self.bulk_sender and self.bulk_sender.isRunning():
            self.bulk_sender.stop()
            self.bulk_sender.wait(5000)
        if self.wa_bot:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self.wa_bot.close())
            except Exception:
                pass
            finally:
                loop.close()
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("WhatsApp Bulk Sender")
    window = MainWindow()
    window.show()
    log_event("Application started.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
