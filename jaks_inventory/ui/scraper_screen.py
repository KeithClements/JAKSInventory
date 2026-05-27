"""Scraper Admin — configuration, run history, pending review, maintenance."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QFormLayout,
)

from db import (
    get_product_by_sku,
    get_scrape_runs,
    get_scraped_xref_counts,
    get_competitor_price_counts,
    get_setting,
    set_setting,
)

from .scraper_review_dialog import ScraperReviewDialog
from ..scraper import ScraperWorker
from ..utils.email_worker import EmailWorker


class ScraperScreen(QWidget):
    """Scraper admin & maintenance dashboard (Tools area)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scraper_worker: ScraperWorker | None = None
        self._build_ui()
        self._load_settings()
        self._refresh_history()
        self._refresh_pending()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        header = QLabel(
            "<b>Scraper Administration</b><br>"
            "<span style='color:#666; font-size:9pt;'>"
            "For day-to-day scraping, use the Products screen (bulk) or "
            "Edit Product dialog (single part).  This page is for configuration, "
            "logs, and maintenance.</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # ── Scraper Settings ─────────────────────────────────
        settings_box = QGroupBox("⚙️  Scraper Settings")
        settings_form = QFormLayout()

        self._delay_spin = QLineEdit()
        self._delay_spin.setPlaceholderText("1.5")
        self._delay_spin.setMaximumWidth(80)
        settings_form.addRow("Request Delay (s):", self._delay_spin)

        self._pai_user_edit = QLineEdit()
        self._pai_user_edit.setPlaceholderText("PAI username")
        self._pai_user_edit.setMaximumWidth(220)
        settings_form.addRow("PAI Username:", self._pai_user_edit)

        self._pai_pass_edit = QLineEdit()
        self._pai_pass_edit.setPlaceholderText("PAI password")
        self._pai_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pai_pass_edit.setMaximumWidth(220)
        settings_form.addRow("PAI Password:", self._pai_pass_edit)

        source_label = QLabel(
            "<b>Active Sources:</b>  DPD  ·  ATL Diesel  ·  FleetPride  ·  HHP"
        )
        source_label.setStyleSheet("color: #333; font-size: 9pt; padding: 4px 0;")
        settings_form.addRow("Sources:", source_label)

        save_settings_btn = QPushButton("💾 Save Settings")
        save_settings_btn.setMaximumWidth(140)
        save_settings_btn.clicked.connect(self._save_settings)

        test_login_btn = QPushButton("🔐 Test PAI Login")
        test_login_btn.setToolTip(
            "Open PAI in a Chrome window so you can complete login manually "
            "(handles CAPTCHA / 2FA). Cookies persist for later headless searches."
        )
        test_login_btn.setMaximumWidth(180)
        test_login_btn.clicked.connect(self._test_pai_login)

        btn_row = QHBoxLayout()
        btn_row.addWidget(save_settings_btn)
        btn_row.addWidget(test_login_btn)
        btn_row.addStretch()
        settings_form.addRow(btn_row)

        settings_box.setLayout(settings_form)
        layout.addWidget(settings_box)

        # ── Pending Review ───────────────────────────────────
        pending_box = QGroupBox("📊  Pending Review")
        pending_layout = QHBoxLayout()
        self._xref_pending_label = QLabel("Cross-Refs: —")
        self._xref_pending_label.setStyleSheet("font-size: 11pt; font-weight: bold;")
        self._price_pending_label = QLabel("Prices: —")
        self._price_pending_label.setStyleSheet("font-size: 11pt; font-weight: bold;")
        review_btn = QPushButton("📋 Review All Pending")
        review_btn.clicked.connect(self._open_review)
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh_pending)
        pending_layout.addWidget(self._xref_pending_label)
        pending_layout.addSpacing(30)
        pending_layout.addWidget(self._price_pending_label)
        pending_layout.addStretch()
        pending_layout.addWidget(review_btn)
        pending_layout.addWidget(refresh_btn)
        pending_box.setLayout(pending_layout)
        layout.addWidget(pending_box)

        # ── Maintenance ──────────────────────────────────────
        maint_box = QGroupBox("🔧  Maintenance")
        maint_layout = QHBoxLayout()

        retry_btn = QPushButton("🔁 Retry Failed Jobs")
        retry_btn.setToolTip("Re-run the most recent failed scrape run")
        retry_btn.clicked.connect(self._retry_failed)
        maint_layout.addWidget(retry_btn)

        self._maint_sku_input = QLineEdit()
        self._maint_sku_input.setPlaceholderText("SKU for manual test scrape…")
        self._maint_sku_input.setMaximumWidth(200)
        maint_layout.addWidget(self._maint_sku_input)

        test_btn = QPushButton("🕷️ Test Scrape")
        test_btn.setToolTip("Run a single test scrape from the admin panel")
        test_btn.clicked.connect(self._run_test_scrape)
        maint_layout.addWidget(test_btn)

        self._maint_progress = QProgressBar()
        self._maint_progress.setMaximumHeight(16)
        self._maint_progress.setMaximumWidth(180)
        self._maint_progress.hide()
        maint_layout.addWidget(self._maint_progress)

        self._maint_status = QLabel("")
        self._maint_status.setStyleSheet("color: #666; font-size: 9pt;")
        maint_layout.addWidget(self._maint_status)
        maint_layout.addStretch()

        maint_box.setLayout(maint_layout)
        layout.addWidget(maint_box)

        # ── Run History ──────────────────────────────────────
        history_box = QGroupBox("📜  Scrape Run History")
        history_layout = QVBoxLayout()

        self._history_table = QTableWidget(0, 6)
        self._history_table.setHorizontalHeaderLabels([
            "ID", "Type", "Sources", "Status", "Found", "Date"
        ])
        self._history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        history_layout.addWidget(self._history_table)

        history_btns = QHBoxLayout()
        history_refresh_btn = QPushButton("🔄 Refresh History")
        history_refresh_btn.clicked.connect(self._refresh_history)
        history_btns.addStretch()
        history_btns.addWidget(history_refresh_btn)
        history_layout.addLayout(history_btns)

        history_box.setLayout(history_layout)
        layout.addWidget(history_box, 1)

        self.setLayout(layout)

    # ── Settings helpers ─────────────────────────────────────

    def _load_settings(self) -> None:
        try:
            self._delay_spin.setText(str(get_setting("scraper_delay", "1.5")))
            self._pai_user_edit.setText(str(get_setting("pai_username", "")))
            self._pai_pass_edit.setText(str(get_setting("pai_password", "")))
        except Exception:
            pass

    def _save_settings(self) -> None:
        try:
            set_setting("scraper_delay", self._delay_spin.text().strip())
            set_setting("pai_username", self._pai_user_edit.text().strip())
            set_setting("pai_password", self._pai_pass_edit.text().strip())
            QMessageBox.information(self, "Saved", "Scraper settings saved.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings:\n{e}")

    def _test_pai_login(self) -> None:
        """Open headed PAI login window so user can complete login manually."""
        # Save current credentials first so the headed window can pre-fill.
        try:
            set_setting("pai_username", self._pai_user_edit.text().strip())
            set_setting("pai_password", self._pai_pass_edit.text().strip())
        except Exception:
            pass
        try:
            from ..scraper.pai_scraper import pai_login_interactive
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not load PAI login helper:\n{e}")
            return
        QMessageBox.information(
            self,
            "Test PAI Login",
            "A Chrome window will open to the PAI login page.\n\n"
            "Credentials will be pre-filled. Click the Login button in the browser, "
            "complete any CAPTCHA / 2FA, and wait for the dashboard to load.\n\n"
            "The window closes automatically once login is detected (up to 2 minutes).",
        )
        self._pai_login_worker = EmailWorker(
            lambda: {
                "ok": (result := pai_login_interactive(timeout_sec=120))[0],
                "message": result[1],
            },
            parent=self,
        )

        def _on_pai_login_result(result: dict) -> None:
            ok = bool(result.get("ok"))
            msg = str(result.get("message", "Unknown PAI login result."))
            if ok:
                QMessageBox.information(self, "PAI Login", msg)
            else:
                QMessageBox.warning(self, "PAI Login", msg)

        self._pai_login_worker.finished_result.connect(_on_pai_login_result)
        self._pai_login_worker.start()

    # ── Refresh helpers ──────────────────────────────────────
    def _refresh_pending(self) -> None:
        try:
            xref_counts = get_scraped_xref_counts()
            pending_x = xref_counts.get("pending", 0) if xref_counts else 0
            self._xref_pending_label.setText(f"Cross-Refs: {pending_x} pending")
        except Exception:
            self._xref_pending_label.setText("Cross-Refs: —")
        try:
            price_counts = get_competitor_price_counts()
            pending_p = price_counts.get("pending", 0) if price_counts else 0
            self._price_pending_label.setText(f"Prices: {pending_p} pending")
        except Exception:
            self._price_pending_label.setText("Prices: —")

    def _refresh_history(self) -> None:
        try:
            runs = get_scrape_runs(limit=50)
        except Exception:
            runs = []
        self._history_table.setRowCount(len(runs))
        for i, r in enumerate(runs):
            def _item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._history_table.setItem(i, 0, _item(r.get("id", "")))
            self._history_table.setItem(i, 1, _item(r.get("job_type", "")))
            self._history_table.setItem(i, 2, _item(r.get("sources", "")))
            self._history_table.setItem(i, 3, _item(r.get("status", "")))
            self._history_table.setItem(i, 4, _item(r.get("total_found", 0)))
            self._history_table.setItem(i, 5, _item(r.get("created_at", "")))
        self._history_table.resizeColumnsToContents()

    # ── Maintenance actions ──────────────────────────────────

    def _retry_failed(self) -> None:
        """Find the most recent failed run and retry it."""
        try:
            runs = get_scrape_runs(limit=20)
        except Exception:
            runs = []
        failed = [r for r in runs if r.get("status") == "error"]
        if not failed:
            QMessageBox.information(self, "No Failures", "No failed scrape runs found.")
            return
        last_fail = failed[0]
        sku = last_fail.get("sku_searched", "")
        if not sku:
            QMessageBox.warning(self, "No SKU", "Failed run has no SKU recorded.")
            return
        self._maint_sku_input.setText(sku)
        self._run_test_scrape()

    def _run_test_scrape(self) -> None:
        """Run a single test scrape from the maintenance panel."""
        sku = self._maint_sku_input.text().strip()
        if not sku:
            QMessageBox.warning(self, "No SKU", "Enter a SKU to test scrape.")
            return
        if self._scraper_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        product = get_product_by_sku(sku)
        product_id = product["id"] if product else None

        self._maint_progress.setValue(0)
        self._maint_progress.setMaximum(0)
        self._maint_progress.show()
        self._maint_status.setText(f"Test scraping {sku}…")

        self._scraper_worker = ScraperWorker("both", sku, product_id=product_id)
        self._scraper_worker.progress.connect(self._on_progress)
        self._scraper_worker.finished.connect(self._on_done)
        self._scraper_worker.error.connect(self._on_error)
        self._scraper_worker.start()

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self._maint_progress.setMaximum(total)
            self._maint_progress.setValue(current)
        self._maint_status.setText(message)

    def _on_done(self, result: dict) -> None:
        self._scraper_worker = None
        self._maint_progress.hide()
        found = result.get("found", 0)
        xref_r = result.get("xref", {})
        pricing_r = result.get("pricing", {})
        xref_found = xref_r.get("found", 0) if isinstance(xref_r, dict) else 0
        pricing_found = pricing_r.get("found", 0) if isinstance(pricing_r, dict) else 0
        self._maint_status.setText(
            f"✅ Done — xrefs: {xref_found}, prices: {pricing_found}"
        )
        self._refresh_history()
        self._refresh_pending()

    def _on_error(self, msg: str) -> None:
        self._scraper_worker = None
        self._maint_progress.hide()
        self._maint_status.setText(f"❌ Error: {msg}")
        QMessageBox.warning(self, "Scrape Error", msg)

    def _open_review(self) -> None:
        dlg = ScraperReviewDialog(product_id=None, parent=self)
        dlg.exec()
        self._refresh_pending()
