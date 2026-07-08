"""ESN Lookup Dialog — engine serial number lookup with Cummins scraper + Grok AI.

Supports Cummins (live scraper), Caterpillar, Detroit (placeholder).
Results can be added to Pre-Inventory, searched on PAI/SAMPA, or
sent to Part Finder.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
    QApplication,
    QWidget,
    QProgressBar,
)

logger = logging.getLogger(__name__)

_MANUFACTURERS = ["Cummins", "Caterpillar", "Detroit"]


# ── Worker thread for Cummins scraping ──────────────────────────────


class _ScrapeSignals(QObject):
    status = Signal(str)
    finished = Signal(list)    # list[dict]
    error = Signal(str)


class _CumminsWorker(QThread):
    """Background thread that runs the Cummins Playwright scraper."""

    def __init__(self, esn: str, keyword: str, parent=None):
        super().__init__(parent)
        self.esn = esn
        self.keyword = keyword
        self.signals = _ScrapeSignals()

    def run(self):
        try:
            from jaks_inventory.scraper.cummins_scraper import search_cummins_esn
            results = search_cummins_esn(
                self.esn,
                self.keyword,
                headless=True,
                on_status=lambda msg: self.signals.status.emit(msg),
            )
            self.signals.finished.emit([asdict(r) for r in results])
        except Exception as exc:
            logger.exception("Cummins scrape failed")
            self.signals.error.emit(str(exc))


class _GrokWorker(QThread):
    """Background thread for Grok AI analysis."""

    def __init__(self, esn, keyword, manufacturer, results, parent=None):
        super().__init__(parent)
        self.esn = esn
        self.keyword = keyword
        self.manufacturer = manufacturer
        self.results = results
        self.signals = _ScrapeSignals()

    def run(self):
        try:
            from jaks_inventory.services.grok_part_advisor import analyse_esn_results
            recs = analyse_esn_results(
                self.esn, self.keyword, self.manufacturer, self.results,
            )
            self.signals.finished.emit([asdict(r) for r in recs])
        except Exception as exc:
            logger.exception("Grok analysis failed")
            self.signals.error.emit(str(exc))


# ── Main dialog ─────────────────────────────────────────────────────


class ESNLookupDialog(QDialog):
    """ESN Lookup — retrieves OEM parts via live scraping + Grok AI.

    Signals:
      part_selected(str)  — OEM part number selected, ready for Part Finder.
    """

    part_selected = Signal(str)

    def __init__(self, *, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔧 ESN Lookup")
        self.setMinimumSize(900, 620)
        self.resize(1000, 700)

        self._results_data: list[dict] = []
        self._grok_recs: list[dict] = []
        self._current_worker = None
        self._grok_worker = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        title = QLabel("🔧 ESN Lookup")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1F2937;")
        root.addWidget(title)

        # ── Input section ──
        input_group = QGroupBox("Engine Information")
        input_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding-top: 18px; margin-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        form = QFormLayout(input_group)
        form.setSpacing(8)

        self._mfr_combo = QComboBox()
        self._mfr_combo.addItems(_MANUFACTURERS)
        form.addRow("Manufacturer:", self._mfr_combo)

        self._esn_edit = QLineEdit()
        self._esn_edit.setPlaceholderText("Enter Engine Serial Number")
        self._esn_edit.setStyleSheet(
            "QLineEdit { padding: 8px; border: 2px solid #EA580C; "
            "border-radius: 6px; font-size: 14px; }"
            "QLineEdit:focus { border-color: #C2410C; background: #FFF7ED; }"
        )
        self._esn_edit.returnPressed.connect(self._do_lookup)
        form.addRow("ESN:", self._esn_edit)

        self._keyword_edit = QLineEdit()
        self._keyword_edit.setPlaceholderText("e.g. Overhaul Kit, Oil Pump, Turbo")
        self._keyword_edit.returnPressed.connect(self._do_lookup)
        form.addRow("Search For:", self._keyword_edit)

        # Buttons row
        btn_row = QHBoxLayout()
        self._lookup_btn = QPushButton("🔍 Look Up ESN")
        self._lookup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lookup_btn.setStyleSheet(
            "QPushButton { background: #EA580C; color: white; font-weight: 600; "
            "padding: 10px 24px; border-radius: 8px; font-size: 14px; }"
            "QPushButton:hover { background: #C2410C; }"
        )
        self._lookup_btn.clicked.connect(self._do_lookup)
        btn_row.addWidget(self._lookup_btn)

        self._grok_btn = QPushButton("🤖 Ask Grok AI")
        self._grok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._grok_btn.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; font-weight: 600; "
            "padding: 10px 16px; border-radius: 8px; font-size: 13px; }"
            "QPushButton:hover { background: #6D28D9; }"
        )
        self._grok_btn.setEnabled(False)
        self._grok_btn.setToolTip("Run Grok AI analysis on results")
        self._grok_btn.clicked.connect(self._run_grok)
        btn_row.addWidget(self._grok_btn)

        btn_row.addStretch()

        self._dealer_btn = QPushButton("📧 Dealer Request Email")
        self._dealer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dealer_btn.setStyleSheet(
            "QPushButton { background: #1E40AF; color: white; font-weight: 600; "
            "padding: 10px 16px; border-radius: 8px; font-size: 12px; }"
            "QPushButton:hover { background: #1E3A8A; }"
        )
        self._dealer_btn.clicked.connect(self._create_dealer_request)
        btn_row.addWidget(self._dealer_btn)

        form.addRow("", btn_row)
        root.addWidget(input_group)

        # ── Progress ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.setStyleSheet(
            "QProgressBar { border: none; background: #E5E7EB; border-radius: 3px; }"
            "QProgressBar::chunk { background: #EA580C; border-radius: 3px; }"
        )
        root.addWidget(self._progress)

        # ── Status ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        root.addWidget(self._status_label)

        # ── Results table ──
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "Part #", "Description", "Kit #", "Kit Description",
            "🤖 AI", "Source", "",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 110)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(4, 60)
        self._table.setColumnWidth(5, 90)
        self._table.setColumnWidth(6, 280)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table, 1)

        # ── Grok recommendation banner (hidden initially) ──
        self._grok_banner = QLabel("")
        self._grok_banner.setVisible(False)
        self._grok_banner.setWordWrap(True)
        self._grok_banner.setStyleSheet(
            "QLabel { background: #EDE9FE; border: 1px solid #C4B5FD; "
            "border-radius: 6px; padding: 8px 12px; color: #5B21B6; font-size: 12px; }"
        )
        root.addWidget(self._grok_banner)

        # ── Bottom ──
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { padding: 8px 20px; border: 1px solid #D1D5DB; "
            "border-radius: 6px; } QPushButton:hover { background: #F3F4F6; }"
        )
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # Lookup dispatcher
    # ------------------------------------------------------------------
    def _do_lookup(self):
        esn = self._esn_edit.text().strip()
        if not esn:
            QMessageBox.warning(self, "ESN Required", "Please enter an Engine Serial Number.")
            return

        mfr = self._mfr_combo.currentText()
        keyword = self._keyword_edit.text().strip()
        if not keyword:
            QMessageBox.warning(self, "Keyword Required",
                                "Please enter a search term (e.g. Overhaul Kit).")
            return

        self._results_data.clear()
        self._grok_recs.clear()
        self._grok_banner.setVisible(False)
        self._grok_btn.setEnabled(False)
        self._table.setRowCount(0)

        if mfr == "Cummins":
            self._lookup_cummins(esn, keyword)
        elif mfr == "Caterpillar":
            self._lookup_caterpillar(esn, keyword)
        elif mfr == "Detroit":
            self._lookup_detroit(esn, keyword)

    # ------------------------------------------------------------------
    # Cummins — live Playwright scraper
    # ------------------------------------------------------------------
    def _lookup_cummins(self, esn: str, keyword: str):
        self._set_busy(True, f"Launching Cummins scraper for ESN {esn}…")

        self._current_worker = _CumminsWorker(esn, keyword, parent=self)
        self._current_worker.signals.status.connect(self._on_status)
        self._current_worker.signals.finished.connect(self._on_cummins_done)
        self._current_worker.signals.error.connect(self._on_scrape_error)
        self._current_worker.start()

    def _on_cummins_done(self, results: list[dict]):
        self._set_busy(False)
        self._results_data = results

        if not results:
            self._status_label.setText(
                "No results from Cummins. Try different keywords or use Dealer Request Email."
            )
            # Try fallback internal search
            esn = self._esn_edit.text().strip()
            keyword = self._keyword_edit.text().strip()
            fallback = self._fallback_internal_search(esn, "Cummins", keyword)
            if fallback:
                self._results_data = fallback
                self._populate_results(fallback, source="Internal Cache")
                self._status_label.setText(
                    f"No live results — showing {len(fallback)} cached result(s)."
                )
            return

        # Cache ESN for future lookups
        self._cache_esn(esn=self._esn_edit.text().strip(), mfr="Cummins")

        self._populate_results(results, source="Cummins")
        self._status_label.setText(
            f"✅ {len(results)} part(s) found on Cummins for ESN {self._esn_edit.text().strip()}"
        )
        self._grok_btn.setEnabled(True)

    def _on_scrape_error(self, error: str):
        self._set_busy(False)
        self._status_label.setText(f"Scrape error: {error}")

        # Fallback to internal cache
        esn = self._esn_edit.text().strip()
        keyword = self._keyword_edit.text().strip()
        mfr = self._mfr_combo.currentText()
        fallback = self._fallback_internal_search(esn, mfr, keyword)
        if fallback:
            self._results_data = fallback
            self._populate_results(fallback, source=f"{mfr} (cache)")
            self._status_label.setText(
                f"Live lookup failed — showing {len(fallback)} cached result(s). Error: {error}"
            )

    # ------------------------------------------------------------------
    # Caterpillar / Detroit — stubs with fallback
    # ------------------------------------------------------------------
    def _lookup_caterpillar(self, esn: str, keyword: str):
        self._status_label.setText("Caterpillar SIS integration coming soon — checking cache…")
        QApplication.processEvents()
        results = self._fallback_internal_search(esn, "Caterpillar", keyword)
        self._results_data = results
        if results:
            self._populate_results(results, source="Caterpillar (cache)")
            self._status_label.setText(f"{len(results)} cached result(s) for Caterpillar ESN.")
            self._grok_btn.setEnabled(True)
        else:
            self._status_label.setText(
                "No cached results. Use Dealer Request Email to request parts from CAT dealer."
            )

    def _lookup_detroit(self, esn: str, keyword: str):
        self._status_label.setText("Detroit DemandDetroit integration coming soon — checking cache…")
        QApplication.processEvents()
        results = self._fallback_internal_search(esn, "Detroit", keyword)
        self._results_data = results
        if results:
            self._populate_results(results, source="Detroit (cache)")
            self._status_label.setText(f"{len(results)} cached result(s) for Detroit ESN.")
            self._grok_btn.setEnabled(True)
        else:
            self._status_label.setText(
                "No cached results. Use Dealer Request Email to request parts from Detroit dealer."
            )

    # ------------------------------------------------------------------
    # Grok AI analysis
    # ------------------------------------------------------------------
    def _run_grok(self):
        if not self._results_data:
            return

        from jaks_inventory.services.grok_part_advisor import get_grok_available
        if not get_grok_available():
            QMessageBox.information(
                self, "Grok Unavailable",
                "Set XAI_API_KEY in .env to enable Grok AI recommendations."
            )
            return

        self._set_busy(True, "🤖 Asking Grok AI to analyse results…")
        self._grok_btn.setEnabled(False)

        self._grok_worker = _GrokWorker(
            esn=self._esn_edit.text().strip(),
            keyword=self._keyword_edit.text().strip(),
            manufacturer=self._mfr_combo.currentText(),
            results=self._results_data,
            parent=self,
        )
        self._grok_worker.signals.status.connect(self._on_status)
        self._grok_worker.signals.finished.connect(self._on_grok_done)
        self._grok_worker.signals.error.connect(self._on_grok_error)
        self._grok_worker.start()

    def _on_grok_done(self, recs: list[dict]):
        self._set_busy(False)
        self._grok_recs = recs

        # Update table AI column + highlight recommended row
        recommended_pn = ""
        recommended_reason = ""
        for i, rec in enumerate(recs):
            if i >= self._table.rowCount():
                break
            confidence = rec.get("confidence", "")
            is_rec = rec.get("recommended", False)
            reason = rec.get("reason", "")
            warnings = rec.get("warnings", "")

            # AI badge
            badge = ""
            if is_rec:
                badge = "⭐ REC"
                recommended_pn = rec.get("part_number", "")
                recommended_reason = reason
            elif confidence == "high":
                badge = "🟢"
            elif confidence == "medium":
                badge = "🟡"
            else:
                badge = "🔴"

            ai_item = QTableWidgetItem(badge)
            ai_item.setToolTip(f"{reason}\n{warnings}" if warnings else reason)
            ai_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 4, ai_item)

            # Highlight recommended row
            if is_rec:
                for col in range(self._table.columnCount()):
                    existing = self._table.item(i, col)
                    if existing:
                        existing.setBackground(Qt.GlobalColor.yellow)

        # Show banner
        if recommended_pn:
            self._grok_banner.setText(
                f"🤖 <b>Grok Recommends:</b> {recommended_pn} — {recommended_reason}"
            )
            self._grok_banner.setVisible(True)

        self._status_label.setText("🤖 Grok analysis complete.")
        self._grok_btn.setEnabled(True)

    def _on_grok_error(self, error: str):
        self._set_busy(False)
        self._grok_btn.setEnabled(True)
        self._status_label.setText(f"Grok error: {error}")

    # ------------------------------------------------------------------
    # Results table
    # ------------------------------------------------------------------
    def _populate_results(self, results: list[dict], source: str = ""):
        self._table.setRowCount(len(results))
        for row, r in enumerate(results):
            pn = r.get("part_number", "") or r.get("oem_part_number", "")
            desc = r.get("description", "")
            kit_num = r.get("kit_number", "")
            kit_desc = r.get("kit_description", "")
            src = source or r.get("source", "")

            self._table.setItem(row, 0, QTableWidgetItem(pn))
            self._table.setItem(row, 1, QTableWidgetItem(desc))
            self._table.setItem(row, 2, QTableWidgetItem(kit_num))
            self._table.setItem(row, 3, QTableWidgetItem(kit_desc))
            self._table.setItem(row, 4, QTableWidgetItem(""))  # AI column
            self._table.setItem(row, 5, QTableWidgetItem(src))

            # Action buttons
            actions = QWidget()
            actions_lay = QHBoxLayout(actions)
            actions_lay.setContentsMargins(2, 2, 2, 2)
            actions_lay.setSpacing(4)

            # Add to Pre-Inventory
            add_btn = QPushButton("📦 Pre-Inv")
            add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            add_btn.setStyleSheet(
                "QPushButton { background: #DC2626; color: white; font-size: 11px; "
                "font-weight: 600; border: none; border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #B91C1C; }"
            )
            add_btn.setToolTip("Add to Pre-Inventory for review")
            add_btn.clicked.connect(lambda _, r=r, s=src: self._add_to_pre_inventory(r, s))
            actions_lay.addWidget(add_btn)

            # Search PAI
            pai_btn = QPushButton("PAI")
            pai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pai_btn.setStyleSheet(
                "QPushButton { background: #2563EB; color: white; font-size: 11px; "
                "font-weight: 600; border: none; border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #1D4ED8; }"
            )
            pai_btn.setToolTip("Search PAI for cross-reference")
            pai_btn.clicked.connect(lambda _, pn_=pn: self._search_pai(pn_))
            actions_lay.addWidget(pai_btn)

            # Find in Part Finder
            pf_btn = QPushButton("🔍 Finder")
            pf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pf_btn.setStyleSheet(
                "QPushButton { background: #6B8E23; color: white; font-size: 11px; "
                "font-weight: 600; border: none; border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #556B2F; }"
            )
            pf_btn.clicked.connect(lambda _, pn_=pn: self.part_selected.emit(pn_))
            actions_lay.addWidget(pf_btn)

            self._table.setCellWidget(row, 6, actions)
            self._table.setRowHeight(row, 40)

    # ------------------------------------------------------------------
    # Pre-Inventory actions
    # ------------------------------------------------------------------
    def _add_to_pre_inventory(self, result: dict, source: str):
        """Add a result to the pre_inventory staging table."""
        from jaks_inventory.services.pre_inventory_service import create_pre_inventory

        pn = result.get("part_number", "") or result.get("oem_part_number", "")
        data = {
            "source": source.lower().split()[0] if source else "manual",
            "esn": self._esn_edit.text().strip(),
            "manufacturer": self._mfr_combo.currentText(),
            "oem_part_number": pn,
            "description": result.get("description", ""),
            "kit_number": result.get("kit_number", ""),
            "kit_description": result.get("kit_description", ""),
            "image_url": result.get("image_url", ""),
        }

        # Attach Grok recommendation if available
        for rec in self._grok_recs:
            if rec.get("part_number") == pn:
                data["grok_recommendation"] = (
                    f"{'⭐ RECOMMENDED — ' if rec.get('recommended') else ''}"
                    f"{rec.get('reason', '')}"
                    f"{' ⚠️ ' + rec.get('warnings') if rec.get('warnings') else ''}"
                )
                break

        r = create_pre_inventory(data)
        if r.get("success"):
            QMessageBox.information(
                self, "Added to Pre-Inventory",
                f"Part {pn} added to Pre-Inventory for review.\n"
                f"(ID: {r['id']})"
            )
        else:
            QMessageBox.warning(self, "Error", r.get("error", "Unknown error"))

    def _search_pai(self, part_number: str):
        """Search PAI for a part number and add result to pre-inventory."""
        self._status_label.setText(f"Searching PAI for {part_number}…")
        QApplication.processEvents()

        try:
            from sources.pai_enrichment import enrich_pai_advisory
            pai_result = enrich_pai_advisory(part_number)
            if pai_result and pai_result.get("pai_sku"):
                from jaks_inventory.services.pre_inventory_service import create_pre_inventory
                data = {
                    "source": "pai",
                    "esn": self._esn_edit.text().strip(),
                    "manufacturer": self._mfr_combo.currentText(),
                    "oem_part_number": part_number,
                    "pai_part_number": pai_result.get("pai_sku", ""),
                    "description": pai_result.get("description", ""),
                    "cost": pai_result.get("cost", 0),
                    "selling_price": pai_result.get("selling_price", 0),
                }
                r = create_pre_inventory(data)
                self._status_label.setText(
                    f"✅ PAI found: {pai_result.get('pai_sku')} — added to Pre-Inventory (ID: {r.get('id')})"
                )
            else:
                self._status_label.setText(f"No PAI match for {part_number}.")
        except Exception as exc:
            self._status_label.setText(f"PAI search failed: {exc}")

    # ------------------------------------------------------------------
    # Fallback internal search
    # ------------------------------------------------------------------
    def _fallback_internal_search(self, esn: str, mfr: str, keyword: str) -> list[dict]:
        results = []
        try:
            from db import get_engine_compatibility
            compat = get_engine_compatibility(esn)
            if compat:
                for entry in (compat if isinstance(compat, list) else [compat]):
                    results.append({
                        "part_number": entry.get("oem_part_number", ""),
                        "description": entry.get("description", ""),
                        "kit_number": "",
                        "kit_description": "",
                        "source": f"{mfr} (cached)",
                    })
        except Exception:
            pass

        try:
            from db import get_esn_cache
            cached = get_esn_cache(esn)
            if cached and cached.get("oem_part_number"):
                results.append({
                    "part_number": cached["oem_part_number"],
                    "description": cached.get("engine_model", ""),
                    "kit_number": "",
                    "kit_description": "",
                    "source": f"{mfr} (cache)",
                })
        except Exception:
            pass

        return results

    def _cache_esn(self, esn: str, mfr: str):
        try:
            from db import save_esn_cache
            save_esn_cache(esn, manufacturer=mfr)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dealer Request Email
    # ------------------------------------------------------------------
    def _create_dealer_request(self):
        mfr = self._mfr_combo.currentText()
        esn = self._esn_edit.text().strip()
        keyword = self._keyword_edit.text().strip()

        email_body = (
            f"Subject: Parts Request — {mfr} ESN {esn}\n\n"
            f"Hello,\n\n"
            f"We are requesting parts information for the following engine:\n\n"
            f"  Manufacturer: {mfr}\n"
            f"  Engine Serial Number: {esn}\n"
            f"  Parts Needed: {keyword}\n"
            f"  VIN: (if available)\n\n"
            f"Please provide part numbers, pricing, and availability.\n\n"
            f"Thank you,\n"
            f"JAKS Equipment\n"
        )
        QApplication.clipboard().setText(email_body)
        QMessageBox.information(
            self, "Email Copied",
            "Dealer request email template copied to clipboard."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool, msg: str = ""):
        self._progress.setVisible(busy)
        self._lookup_btn.setEnabled(not busy)
        if msg:
            self._status_label.setText(msg)

    def _on_status(self, msg: str):
        self._status_label.setText(msg)
        QApplication.processEvents()
