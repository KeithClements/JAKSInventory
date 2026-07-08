"""HHP Scraper Pipeline — 5-phase workflow screen for jaks_inventory.

Phases:
  1. Scan  — paginate HHP category listings (HTTP-only, fast)
  2. Scrape — full product parse + images + AI description (Playwright)
  3. PAI   — cost enrichment (optional)
  4. Review — validate pricing / preview Shopify diff
  5. Upload — push to Shopify

Products discovered here are persisted to inventory.db and appear on
the Products screen immediately.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QSpinBox,
)

from workers.signals import Signals

from ..scraper.hhp_bridge import HHP_CATEGORIES, HHP_MANUFACTURERS
from ..scraper.hhp_workers import HHPScanWorker, HHPScrapeWorker, HHPSingleScrapeWorker


class HHPScraperScreen(QWidget):
    """Full HHP scraper pipeline integrated into the inventory app."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._scan_worker: HHPScanWorker | None = None
        self._scrape_worker: HHPScrapeWorker | None = None
        self._single_worker: HHPSingleScrapeWorker | None = None
        self._scan_result: dict | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout()

        # Header
        header = QLabel(
            "<b>🕷️ HHP Scraper Pipeline</b><br>"
            "<span style='color:#666; font-size:9pt;'>"
            "Scan, scrape, enrich, review and upload products from "
            "Highway &amp; Heavy Parts into your inventory.</span>"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        # ── Top bar: category / manufacturer selectors + action buttons ──
        controls_box = QGroupBox("🔍 Phase 1 — Scan / Scrape")
        controls_layout = QVBoxLayout()

        # Row 1: selectors
        sel_row = QHBoxLayout()

        sel_row.addWidget(QLabel("Category:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItems(HHP_CATEGORIES)
        self._cat_combo.setMinimumWidth(180)
        sel_row.addWidget(self._cat_combo)

        sel_row.addWidget(QLabel("Manufacturer:"))
        self._man_combo = QComboBox()
        self._man_combo.addItems(HHP_MANUFACTURERS)
        self._man_combo.setMinimumWidth(140)
        sel_row.addWidget(self._man_combo)

        self._new_only_chk = QCheckBox("New only")
        self._new_only_chk.setToolTip("Exclude remanufactured items")
        sel_row.addWidget(self._new_only_chk)

        sel_row.addWidget(QLabel("Limit:"))
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(0, 9999)
        self._limit_spin.setSpecialValueText("No limit")
        self._limit_spin.setToolTip("0 = scrape all found products")
        self._limit_spin.setMaximumWidth(80)
        sel_row.addWidget(self._limit_spin)

        sel_row.addStretch()
        controls_layout.addLayout(sel_row)

        # Row 2: action buttons
        btn_row = QHBoxLayout()

        self._scan_btn = QPushButton("🔍 Scan Listings")
        self._scan_btn.setToolTip("Phase 1: Quick HTTP scan to estimate product count")
        self._scan_btn.clicked.connect(self._start_scan)
        btn_row.addWidget(self._scan_btn)

        self._scrape_btn = QPushButton("🧱 Scrape All")
        self._scrape_btn.setToolTip("Phase 2: Full scrape with images & descriptions")
        self._scrape_btn.clicked.connect(self._start_scrape)
        btn_row.addWidget(self._scrape_btn)

        self._stop_btn = QPushButton("⏹ Stop")
        self._stop_btn.setToolTip("Cancel the current operation")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_current)
        btn_row.addWidget(self._stop_btn)

        btn_row.addSpacing(20)

        # Single URL scrape
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("Paste a single HHP product URL…")
        self._url_input.setMinimumWidth(300)
        btn_row.addWidget(self._url_input, 1)

        self._single_btn = QPushButton("🧱 Scrape URL")
        self._single_btn.setToolTip("Scrape a single product by URL")
        self._single_btn.clicked.connect(self._start_single_scrape)
        btn_row.addWidget(self._single_btn)

        controls_layout.addLayout(btn_row)
        controls_box.setLayout(controls_layout)
        root.addWidget(controls_box)

        # ── Progress area ──
        prog_row = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        prog_row.addWidget(self._progress_bar, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666; font-size: 9pt;")
        prog_row.addWidget(self._status_label)
        root.addLayout(prog_row)

        # ── Splitter: results table (top) + log (bottom) ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Results table
        results_box = QGroupBox("📦 Scan / Scrape Results")
        results_layout = QVBoxLayout()

        self._results_table = QTableWidget(0, 7)
        self._results_table.setHorizontalHeaderLabels([
            "Part #", "Title", "Price", "Manufacturer", "Stage", "Images", "URL",
        ])
        self._results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results_table.setAlternatingRowColors(True)
        hh = self._results_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        results_layout.addWidget(self._results_table)

        # Summary row under table
        summary_row = QHBoxLayout()
        self._count_label = QLabel("Products: 0")
        self._count_label.setStyleSheet("font-weight: bold;")
        summary_row.addWidget(self._count_label)
        summary_row.addStretch()
        results_layout.addLayout(summary_row)

        results_box.setLayout(results_layout)
        splitter.addWidget(results_box)

        # Log panel
        log_box = QGroupBox("📜 Log")
        log_layout = QVBoxLayout()
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(200)
        self._log_area.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        log_layout.addWidget(self._log_area)

        log_btns = QHBoxLayout()
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.clicked.connect(self._log_area.clear)
        log_btns.addStretch()
        log_btns.addWidget(clear_log_btn)
        log_layout.addLayout(log_btns)

        log_box.setLayout(log_layout)
        splitter.addWidget(log_box)

        splitter.setSizes([400, 150])
        root.addWidget(splitter, 1)

        self.setLayout(root)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        self._log_area.append(msg)

    def _set_busy(self, busy: bool) -> None:
        self._scan_btn.setEnabled(not busy)
        self._scrape_btn.setEnabled(not busy)
        self._single_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        if busy:
            self._progress_bar.setMaximum(0)
            self._progress_bar.show()
        else:
            self._progress_bar.hide()

    def _ro_item(self, text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(str(text))
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    # ------------------------------------------------------------------
    # Phase 1 — Scan
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        if self._scan_worker is not None:
            return
        cat = self._cat_combo.currentText()
        man = self._man_combo.currentText()
        self._log(f"Scanning {cat} / {man}…")
        self._set_busy(True)
        self._status_label.setText(f"Scanning {cat} / {man}…")

        # Create Signals in the MAIN thread so Qt signal delivery works
        signals = Signals()
        signals.log.connect(self._log)

        self._scan_worker = HHPScanWorker(
            cat, man, new_only=self._new_only_chk.isChecked(), signals=signals,
        )
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_scan_done(self, result: dict) -> None:
        self._scan_worker = None
        self._scan_result = result
        items = result.get("items", [])
        count = len(items)
        self._log(f"Scan complete: {count} product(s) found")
        self._status_label.setText(f"Scan complete: {count} product(s)")
        self._count_label.setText(f"Products: {count}")
        self._set_busy(False)

        # Populate table with scan results
        self._results_table.setRowCount(count)
        for i, item in enumerate(items):
            self._results_table.setItem(i, 0, self._ro_item(item.get("part", "")))
            self._results_table.setItem(i, 1, self._ro_item(item.get("title", "")))
            price = item.get("price")
            self._results_table.setItem(i, 2, self._ro_item(f"${price:,.2f}" if price else "—"))
            self._results_table.setItem(i, 3, self._ro_item(result.get("manufacturer", "")))
            self._results_table.setItem(i, 4, self._ro_item("🔍 Discovered"))
            self._results_table.setItem(i, 5, self._ro_item("—"))
            self._results_table.setItem(i, 6, self._ro_item(item.get("url", "")))
        self._results_table.resizeColumnsToContents()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_worker = None
        self._set_busy(False)
        self._status_label.setText(f"Scan failed")
        self._log(f"ERROR: {msg}")
        QMessageBox.warning(self, "Scan Failed", msg)

    # ------------------------------------------------------------------
    # Phase 2 — Scrape Category
    # ------------------------------------------------------------------

    def _start_scrape(self) -> None:
        if self._scrape_worker is not None:
            return
        cat = self._cat_combo.currentText()
        man = self._man_combo.currentText()
        limit = self._limit_spin.value() or None
        self._log(f"Scraping {cat} / {man} (limit={limit or 'all'})…")
        self._set_busy(True)
        self._status_label.setText(f"Scraping {cat} / {man}…")
        self._results_table.setRowCount(0)
        self._scrape_row_count = 0

        # Create Signals in the MAIN thread so Qt signal delivery works
        signals = Signals()
        signals.log.connect(self._log)
        signals.stats.connect(self._on_scrape_stats)
        signals.row_ready.connect(self._on_row_ready)

        self._scrape_worker = HHPScrapeWorker(
            cat, man, new_only=self._new_only_chk.isChecked(), limit=limit,
            signals=signals,
        )
        self._scrape_worker.finished.connect(self._on_scrape_done)
        self._scrape_worker.error.connect(self._on_scrape_error)
        self._scrape_worker.start()

    def _on_scrape_stats(self, d: dict) -> None:
        total = d.get("total") or 0
        attempted = d.get("attempted", 0)
        added = d.get("added", 0)
        failed = d.get("failed", 0)
        phase = d.get("phase", "")
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(attempted)
        self._status_label.setText(
            f"{phase} — {attempted}/{total}  |  ✅ {added}  ❌ {failed}"
        )

    def _on_row_ready(self, d: dict) -> None:
        """Add a single product row to the table as it comes in."""
        kind = d.get("kind", "")
        if kind == "skipped":
            return  # don't show skipped rows

        prod = d.get("product")
        if prod is None:
            return

        row = self._results_table.rowCount()
        self._results_table.insertRow(row)
        sku = str(getattr(prod, "sku", "") or "")
        title = str(getattr(prod, "title", "") or "")
        price = ""
        try:
            p = getattr(prod, "pricing", {}) or {}
            pv = p.get("price") or p.get("selling_price") or 0
            if pv:
                price = f"${float(pv):,.2f}"
        except Exception:
            pass
        man = str(getattr(prod, "manufacturer", "") or "")
        stage_num = 2
        try:
            stage_num = int((getattr(prod, "pricing", {}) or {}).get("stage", 2))
        except Exception:
            pass
        stage_map = {1: "🔍 Discovered", 2: "🧱 Scraped", 3: "🧪 PAI", 4: "🧠 Reviewed", 5: "🚀 Uploaded"}
        stage_txt = stage_map.get(stage_num, str(stage_num))
        img_count = str(getattr(prod, "image_count_watermarked", 0) or getattr(prod, "image_count", 0) or 0)
        url = str(getattr(prod, "url", "") or "")

        self._results_table.setItem(row, 0, self._ro_item(sku))
        self._results_table.setItem(row, 1, self._ro_item(title))
        self._results_table.setItem(row, 2, self._ro_item(price or "—"))
        self._results_table.setItem(row, 3, self._ro_item(man))
        self._results_table.setItem(row, 4, self._ro_item(stage_txt))
        self._results_table.setItem(row, 5, self._ro_item(img_count))
        self._results_table.setItem(row, 6, self._ro_item(url))
        self._count_label.setText(f"Products: {row + 1}")

    def _on_scrape_done(self, products: dict) -> None:
        self._scrape_worker = None
        self._set_busy(False)
        count = len(products) if products else 0
        self._status_label.setText(f"Scrape complete: {count} product(s) saved to inventory")
        self._count_label.setText(f"Products: {count}")
        self._log(f"✅ Scrape complete — {count} product(s) saved to inventory.db")

    def _on_scrape_error(self, msg: str) -> None:
        self._scrape_worker = None
        self._set_busy(False)
        self._status_label.setText("Scrape failed")
        self._log(f"ERROR: {msg}")
        QMessageBox.warning(self, "Scrape Failed", msg)

    # ------------------------------------------------------------------
    # Single URL Scrape
    # ------------------------------------------------------------------

    def _start_single_scrape(self) -> None:
        url = self._url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "No URL", "Paste an HHP product URL first.")
            return
        if self._single_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        self._log(f"Scraping single URL: {url}")
        self._set_busy(True)
        self._status_label.setText("Scraping single product…")

        # Create Signals in the MAIN thread so Qt signal delivery works
        signals = Signals()
        signals.log.connect(self._log)

        self._single_worker = HHPSingleScrapeWorker(url, signals=signals)
        self._single_worker.finished.connect(self._on_single_done)
        self._single_worker.error.connect(self._on_single_error)
        self._single_worker.start()

    def _on_single_done(self, prod: object) -> None:
        self._single_worker = None
        self._set_busy(False)
        if prod is None:
            self._status_label.setText("No product returned")
            return

        sku = str(getattr(prod, "sku", "") or "")
        self._status_label.setText(f"✅ Scraped and saved: {sku}")
        self._log(f"✅ Product saved to inventory: {sku}")

        # Add to table
        row = self._results_table.rowCount()
        self._results_table.insertRow(row)
        title = str(getattr(prod, "title", "") or "")
        price = ""
        try:
            p = getattr(prod, "pricing", {}) or {}
            pv = p.get("price") or p.get("selling_price") or 0
            if pv:
                price = f"${float(pv):,.2f}"
        except Exception:
            pass
        man = str(getattr(prod, "manufacturer", "") or "")
        img = str(getattr(prod, "image_count_watermarked", 0) or 0)
        url_str = str(getattr(prod, "url", "") or "")

        self._results_table.setItem(row, 0, self._ro_item(sku))
        self._results_table.setItem(row, 1, self._ro_item(title))
        self._results_table.setItem(row, 2, self._ro_item(price or "—"))
        self._results_table.setItem(row, 3, self._ro_item(man))
        self._results_table.setItem(row, 4, self._ro_item("🧱 Scraped"))
        self._results_table.setItem(row, 5, self._ro_item(img))
        self._results_table.setItem(row, 6, self._ro_item(url_str))
        self._count_label.setText(f"Products: {row + 1}")

    def _on_single_error(self, msg: str) -> None:
        self._single_worker = None
        self._set_busy(False)
        self._status_label.setText("Single scrape failed")
        self._log(f"ERROR: {msg}")
        QMessageBox.warning(self, "Scrape Failed", msg)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop_current(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel()
            self._log("Cancelling scan…")
        if self._scrape_worker is not None:
            self._scrape_worker.cancel()
            self._log("Cancelling scrape…")
