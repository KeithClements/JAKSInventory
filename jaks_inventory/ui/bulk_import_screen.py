"""5-Step Bulk Import / Smart Import Wizard.

Step 1: Upload (CSV / Excel / Paste)
Step 2: Preview Table (editable, validation highlights)
Step 3: AI Processing Review (SKU gen, description cleanup, category suggestion,
        duplicate detection, cross-reference identification)
Step 4: Bulk Actions (apply vendor, category, margin, replace text, remove rows,
        scan duplicates, merge cells)
Step 5: Confirm & Import (summary)
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    bulk_import_products,
    create_product,
    find_similar_products,
    generate_next_sku,
    get_all_categories,
    get_all_subcategories,
    get_all_vendors,
    get_sku_prefix_for_vendor,
    list_sku_prefixes,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAPPED_FIELDS = [
    "(skip)", "sku", "title", "manufacturer", "category", "subcategory",
    "cost", "selling_price", "qty_on_hand", "oem_part_number",
    "xref_part_number", "engine", "condition", "supplier",
    "description_html", "core_charge",
]

_AUTO_MAP: dict[str, str] = {
    "sku": "sku", "part": "sku", "part number": "sku", "part_number": "sku",
    "item": "sku", "item number": "sku",
    "title": "title", "name": "title", "product": "title",
    "description": "title", "desc": "title",
    "manufacturer": "manufacturer", "brand": "manufacturer", "mfg": "manufacturer",
    "category": "category", "type": "category", "cat": "category",
    "subcategory": "subcategory", "sub category": "subcategory", "subcat": "subcategory",
    "cost": "cost", "wholesale": "cost", "unit cost": "cost",
    "price": "selling_price", "sell": "selling_price", "retail": "selling_price",
    "selling_price": "selling_price", "sell price": "selling_price",
    "qty": "qty_on_hand", "quantity": "qty_on_hand", "qty_on_hand": "qty_on_hand",
    "on hand": "qty_on_hand", "stock": "qty_on_hand",
    "oem": "oem_part_number", "oem part": "oem_part_number",
    "xref": "xref_part_number", "cross ref": "xref_part_number",
    "interchange": "xref_part_number",
    "engine": "engine", "engine model": "engine",
    "condition": "condition",
    "supplier": "supplier", "vendor": "supplier",
    "core": "core_charge", "core charge": "core_charge",
}

# Validation colours
_CLR_ERROR = QColor("#FFCCCC")      # red-tint  — missing required
_CLR_WARN = QColor("#FFF3CD")       # yellow    — possible duplicate / suspicious
_CLR_AI = QColor("#D4EDDA")         # green     — AI-enriched
_CLR_OK = QColor("#FFFFFF")

# Step indices
STEP_UPLOAD = 0
STEP_PREVIEW = 1
STEP_AI = 2
STEP_BULK = 3
STEP_CONFIRM = 4

STEP_LABELS = [
    "① Upload",
    "② Preview & Map",
    "③ AI Processing",
    "④ Bulk Actions",
    "⑤ Confirm & Import",
]


# ═══════════════════════════════════════════════════════════════════════════
# AI Worker Thread
# ═══════════════════════════════════════════════════════════════════════════

class _AIWorker(QThread):
    """Run AI enrichment in a background thread."""

    progress = Signal(int, int)        # (current_row, total)
    row_done = Signal(int, dict)       # (row_index, enriched_fields)
    finished_all = Signal(list)        # list of enrichment dicts
    error = Signal(str)

    def __init__(
        self,
        rows: list[dict],
        categories: list[str],
        subcategories: list[str],
        vendor_prefix_map: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self._rows = rows
        self._categories = categories
        self._subcategories = subcategories
        self._vendor_prefix_map = vendor_prefix_map  # vendor_name_lower -> prefix
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # -----------------------------------------------------------------
    def run(self):  # noqa: C901  — complex but self-contained
        try:
            from openai import OpenAI
        except ImportError:
            self.error.emit("openai package not installed — pip install openai")
            return

        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            # Fallback to deterministic enrichment only
            results = self._deterministic_only()
            self.finished_all.emit(results)
            return

        client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        total = len(self._rows)
        results: list[dict] = []

        # Process in batches of up to 10 rows for efficiency
        batch_size = 10
        for start in range(0, total, batch_size):
            if self._cancelled:
                break
            batch = self._rows[start : start + batch_size]
            batch_results = self._process_batch(client, batch, start)
            results.extend(batch_results)
            for i, r in enumerate(batch_results):
                self.row_done.emit(start + i, r)
            self.progress.emit(min(start + batch_size, total), total)

        self.finished_all.emit(results)

    # -----------------------------------------------------------------
    def _process_batch(self, client, batch: list[dict], offset: int) -> list[dict]:
        """Process a batch of rows via Grok."""
        cats_str = ", ".join(self._categories[:40]) if self._categories else "General"
        subcats_str = ", ".join(self._subcategories[:40]) if self._subcategories else ""

        rows_text = ""
        for i, row in enumerate(batch):
            rows_text += (
                f"Row {offset + i + 1}:\n"
                f"  Title: {row.get('title', '')}\n"
                f"  SKU: {row.get('sku', '')}\n"
                f"  Manufacturer: {row.get('manufacturer', '')}\n"
                f"  Category: {row.get('category', '')}\n"
                f"  OEM Part: {row.get('oem_part_number', '')}\n"
                f"  Engine: {row.get('engine', '')}\n"
                f"  Description: {row.get('description_html', '')}\n\n"
            )

        system = (
            "You are a diesel engine parts inventory specialist. "
            "You clean up imported product data for a diesel parts store called JAK's Diesel. "
            "Respond ONLY with a JSON array — no markdown fences, no commentary."
        )

        prompt = f"""Clean and enrich these imported diesel parts. For each row return a JSON object.

Existing categories in our system: {cats_str}
Existing subcategories: {subcats_str}

{rows_text}

For each row, return:
{{
  "row": <row_number>,
  "cleaned_title": "<cleaned product title — remove garbage chars, fix capitalisation>",
  "suggested_category": "<best matching category from our list, or new if none fit>",
  "suggested_subcategory": "<best matching subcategory, or empty>",
  "is_garbage": <true if this row looks like junk/header data that should be removed>,
  "possible_xrefs": ["<any cross-reference part numbers found in title or description>"],
  "notes": "<brief note about changes made>"
}}

Return a JSON array of objects, one per row. No extra text."""

        try:
            response = client.chat.completions.create(
                model="grok-3",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.choices[0].message.content.strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                content = re.sub(r"^```[a-z]*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)
            parsed = json.loads(content)
            if not isinstance(parsed, list):
                parsed = [parsed]
            # Pad if fewer results than batch
            while len(parsed) < len(batch):
                parsed.append({})
            return parsed[: len(batch)]
        except Exception:
            # Return empty enrichment on API failure
            return [{} for _ in batch]

    def _deterministic_only(self) -> list[dict]:
        """Fallback enrichment without AI — just SKU generation hints."""
        results = []
        total = len(self._rows)
        for i, row in enumerate(self._rows):
            if self._cancelled:
                break
            results.append({"row": i + 1, "cleaned_title": row.get("title", "")})
            self.row_done.emit(i, results[-1])
            self.progress.emit(i + 1, total)
        return results


# ═══════════════════════════════════════════════════════════════════════════
# Main Screen
# ═══════════════════════════════════════════════════════════════════════════

class BulkImportScreen(QWidget):
    """5-step Bulk Import / Smart Import wizard."""

    navigate_to = Signal(str)   # for cross-screen nav if needed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Internal state
        self._raw_rows: list[dict] = []
        self._headers: list[str] = []
        self._mapped_rows: list[dict] = []      # after column mapping
        self._ai_results: list[dict] = []       # AI enrichment per row
        self._ai_worker: _AIWorker | None = None
        self._validation_issues: list[list[str]] = []  # per-row issue list

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════
    # UI Construction
    # ═══════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Step indicator bar ──
        self._step_bar = QHBoxLayout()
        self._step_labels: list[QLabel] = []
        for txt in STEP_LABELS:
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "padding: 6px 10px; font-size: 10pt; border-bottom: 3px solid transparent;"
            )
            self._step_labels.append(lbl)
            self._step_bar.addWidget(lbl)

        step_bar_w = QWidget()
        step_bar_w.setLayout(self._step_bar)
        step_bar_w.setStyleSheet("background-color: #3d4a38;")
        root.addWidget(step_bar_w)

        # ── Stacked pages ──
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_upload_page())       # 0
        self._pages.addWidget(self._build_preview_page())      # 1
        self._pages.addWidget(self._build_ai_page())           # 2
        self._pages.addWidget(self._build_bulk_page())         # 3
        self._pages.addWidget(self._build_confirm_page())      # 4
        root.addWidget(self._pages, 1)

        # ── Bottom nav bar ──
        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(10, 6, 10, 6)
        self._btn_back = QPushButton("← Back")
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next = QPushButton("Next →")
        self._btn_next.clicked.connect(self._go_next)
        self._btn_next.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; "
            "padding: 6px 20px; font-size: 11pt;"
        )
        self._btn_reset = QPushButton("🔄 Start Over")
        self._btn_reset.clicked.connect(self._reset_wizard)
        nav_bar.addWidget(self._btn_reset)
        nav_bar.addStretch()
        nav_bar.addWidget(self._btn_back)
        nav_bar.addWidget(self._btn_next)

        nav_w = QWidget()
        nav_w.setLayout(nav_bar)
        nav_w.setStyleSheet("background-color: #f0f0f0; border-top: 1px solid #ccc;")
        root.addWidget(nav_w)

        self._go_to_step(STEP_UPLOAD)

    # ------------------------------------------------------------------
    # Page 1: Upload
    # ------------------------------------------------------------------
    def _build_upload_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Step 1 — Upload Your Data")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; margin-bottom: 10px;")
        lay.addWidget(title)

        desc = QLabel(
            "Load a CSV or Excel file, or paste tab-separated data directly. "
            "We'll auto-detect columns in the next step."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 10pt; color: #555; margin-bottom: 16px;")
        lay.addWidget(desc)

        # File picker
        file_group = QGroupBox("📂 Import from File")
        fg_lay = QHBoxLayout(file_group)
        self._upload_file_label = QLabel("No file selected")
        self._upload_file_label.setStyleSheet("font-size: 10pt;")
        browse_btn = QPushButton("Browse CSV / Excel…")
        browse_btn.setStyleSheet("padding: 6px 16px;")
        browse_btn.clicked.connect(self._browse_file)
        self._has_header_cb = QCheckBox("First row is header")
        self._has_header_cb.setChecked(True)
        fg_lay.addWidget(self._upload_file_label, 1)
        fg_lay.addWidget(self._has_header_cb)
        fg_lay.addWidget(browse_btn)
        lay.addWidget(file_group)

        # Paste area
        paste_group = QGroupBox("📋 Paste from Clipboard  (tab-separated)")
        pg_lay = QVBoxLayout(paste_group)
        self._paste_edit = QPlainTextEdit()
        self._paste_edit.setPlaceholderText(
            "Paste rows here (Tab-separated).\n"
            "Example:\n"
            "SKU\tTitle\tManufacturer\tCategory\tCost\tPrice\tQty\n"
            "PAI-123\tPiston Kit\tPAI Industries\tEngine Parts\t125.00\t249.99\t5"
        )
        self._paste_edit.setMaximumHeight(200)
        pg_lay.addWidget(self._paste_edit)

        paste_btn = QPushButton("📋 Use Pasted Data")
        paste_btn.clicked.connect(self._load_pasted)
        pg_lay.addWidget(paste_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(paste_group)

        # Row count label
        self._upload_status = QLabel("")
        self._upload_status.setStyleSheet("font-size: 11pt; font-weight: bold; color: #2e7d32;")
        lay.addWidget(self._upload_status)

        lay.addStretch()
        return page

    # ------------------------------------------------------------------
    # Page 2: Preview & Map
    # ------------------------------------------------------------------
    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)

        title = QLabel("Step 2 — Preview & Column Mapping")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(title)

        desc = QLabel(
            "Map each file column to a product field. "
            "Cells highlighted ❌ red = missing required data, ⚠️ yellow = possible issue. "
            "Double-click any cell to edit."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 9pt; color: #555; margin-bottom: 8px;")
        lay.addWidget(desc)

        # Column mapping row
        mapping_wrapper = QWidget()
        self._mapping_row = QHBoxLayout(mapping_wrapper)
        self._mapping_row.setContentsMargins(0, 0, 0, 0)
        self._mapping_combos: list[QComboBox] = []
        lay.addWidget(QLabel("Column Mapping:"))
        lay.addWidget(mapping_wrapper)

        # Apply mapping button
        apply_btn = QPushButton("🔄 Re-apply Mapping")
        apply_btn.clicked.connect(self._apply_mapping)
        lay.addWidget(apply_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        # Preview table
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._preview_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self._preview_table, 1)

        # Validation summary
        self._validation_label = QLabel("")
        self._validation_label.setStyleSheet("font-size: 10pt; margin-top: 4px;")
        lay.addWidget(self._validation_label)

        return page

    # ------------------------------------------------------------------
    # Page 3: AI Processing
    # ------------------------------------------------------------------
    def _build_ai_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Step 3 — AI Processing Review")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(title)

        desc = QLabel(
            "AI will analyse your data to:\n"
            "  • Generate SKUs (JAKS-[VendorCode]-[PartNumber])\n"
            "  • Clean up titles & remove garbage data\n"
            "  • Suggest category / subcategory\n"
            "  • Detect duplicates in your current inventory\n"
            "  • Identify interchange / cross-reference part numbers"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 10pt; color: #555;")
        lay.addWidget(desc)

        # Controls
        ctrl_row = QHBoxLayout()
        self._ai_run_btn = QPushButton("🤖 Run AI Processing")
        self._ai_run_btn.setStyleSheet(
            "background-color: #1565c0; color: white; font-weight: bold; "
            "padding: 8px 20px; font-size: 11pt;"
        )
        self._ai_run_btn.clicked.connect(self._run_ai_processing)
        self._ai_cancel_btn = QPushButton("Cancel")
        self._ai_cancel_btn.setEnabled(False)
        self._ai_cancel_btn.clicked.connect(self._cancel_ai)
        self._ai_skip_btn = QPushButton("Skip AI → Use As-Is")
        self._ai_skip_btn.clicked.connect(lambda: self._go_to_step(STEP_BULK))
        ctrl_row.addWidget(self._ai_run_btn)
        ctrl_row.addWidget(self._ai_cancel_btn)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._ai_skip_btn)
        lay.addLayout(ctrl_row)

        # Progress
        self._ai_progress = QProgressBar()
        self._ai_progress.setValue(0)
        lay.addWidget(self._ai_progress)
        self._ai_status_label = QLabel("")
        self._ai_status_label.setStyleSheet("font-size: 10pt;")
        lay.addWidget(self._ai_status_label)

        # AI results table
        self._ai_table = QTableWidget()
        self._ai_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        self._ai_table.setAlternatingRowColors(True)
        self._ai_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self._ai_table, 1)

        return page

    # ------------------------------------------------------------------
    # Page 4: Bulk Actions
    # ------------------------------------------------------------------
    def _build_bulk_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)

        title = QLabel("Step 4 — Bulk Actions")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(title)

        desc = QLabel(
            "Apply changes to ALL rows at once. Edit individual cells by double-clicking."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 9pt; color: #555; margin-bottom: 6px;")
        lay.addWidget(desc)

        # Toolbar
        tb = QHBoxLayout()

        # Apply Vendor
        self._bulk_vendor_combo = QComboBox()
        self._bulk_vendor_combo.setMinimumWidth(140)
        self._bulk_vendor_combo.addItem("(select vendor)")
        btn_vendor = QPushButton("Apply Vendor")
        btn_vendor.clicked.connect(self._bulk_apply_vendor)
        tb.addWidget(QLabel("Vendor:"))
        tb.addWidget(self._bulk_vendor_combo)
        tb.addWidget(btn_vendor)

        tb.addWidget(self._vsep())

        # Apply Category
        self._bulk_cat_combo = QComboBox()
        self._bulk_cat_combo.setMinimumWidth(120)
        self._bulk_cat_combo.setEditable(True)
        btn_cat = QPushButton("Apply Category")
        btn_cat.clicked.connect(self._bulk_apply_category)
        tb.addWidget(QLabel("Category:"))
        tb.addWidget(self._bulk_cat_combo)
        tb.addWidget(btn_cat)

        tb.addWidget(self._vsep())

        # Apply Margin %
        self._bulk_margin_spin = QSpinBox()
        self._bulk_margin_spin.setRange(0, 500)
        self._bulk_margin_spin.setValue(40)
        self._bulk_margin_spin.setSuffix("%")
        btn_margin = QPushButton("Apply Margin")
        btn_margin.clicked.connect(self._bulk_apply_margin)
        tb.addWidget(QLabel("Margin:"))
        tb.addWidget(self._bulk_margin_spin)
        tb.addWidget(btn_margin)

        tb.addStretch()
        lay.addLayout(tb)

        # Toolbar row 2
        tb2 = QHBoxLayout()
        btn_replace = QPushButton("🔄 Find & Replace")
        btn_replace.clicked.connect(self._bulk_find_replace)
        btn_remove = QPushButton("🗑 Remove Selected Rows")
        btn_remove.clicked.connect(self._bulk_remove_rows)
        btn_dupes = QPushButton("🔍 Scan for Duplicates")
        btn_dupes.clicked.connect(self._bulk_scan_duplicates)
        btn_gen_sku = QPushButton("🏷 Generate Missing SKUs")
        btn_gen_sku.clicked.connect(self._bulk_generate_skus)
        tb2.addWidget(btn_replace)
        tb2.addWidget(btn_remove)
        tb2.addWidget(btn_dupes)
        tb2.addWidget(btn_gen_sku)
        tb2.addStretch()
        lay.addLayout(tb2)

        # Editable table
        self._bulk_table = QTableWidget()
        self._bulk_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._bulk_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._bulk_table.setAlternatingRowColors(True)
        self._bulk_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self._bulk_table, 1)

        # Summary line
        self._bulk_summary = QLabel("")
        self._bulk_summary.setStyleSheet("font-size: 10pt; margin-top: 4px;")
        lay.addWidget(self._bulk_summary)

        return page

    # ------------------------------------------------------------------
    # Page 5: Confirm & Import
    # ------------------------------------------------------------------
    def _build_confirm_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Step 5 — Confirm & Import")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(title)

        self._confirm_summary = QTextEdit()
        self._confirm_summary.setReadOnly(True)
        self._confirm_summary.setStyleSheet("font-size: 10pt;")
        lay.addWidget(self._confirm_summary, 1)

        # Options
        opts = QHBoxLayout()
        self._confirm_update_cb = QCheckBox("Update existing SKUs if found")
        self._confirm_auto_sku_combo = QComboBox()
        self._confirm_auto_sku_combo.addItem("(no auto-SKU for blanks)", "")
        self._confirm_auto_sku_combo.setMinimumWidth(200)
        opts.addWidget(self._confirm_update_cb)
        opts.addWidget(QLabel("  Auto-SKU prefix:"))
        opts.addWidget(self._confirm_auto_sku_combo)
        opts.addStretch()
        lay.addLayout(opts)

        # Import button (large + green)
        self._import_btn = QPushButton("🚀 Import Now")
        self._import_btn.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; "
            "padding: 12px 40px; font-size: 14pt;"
        )
        self._import_btn.clicked.connect(self._run_import)
        lay.addWidget(self._import_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Result label
        self._import_result_label = QLabel("")
        self._import_result_label.setWordWrap(True)
        self._import_result_label.setStyleSheet("font-size: 11pt; margin-top: 12px;")
        lay.addWidget(self._import_result_label)

        return page

    # ═══════════════════════════════════════════════════════════════
    # Navigation Logic
    # ═══════════════════════════════════════════════════════════════

    def _go_to_step(self, step: int):
        step = max(STEP_UPLOAD, min(STEP_CONFIRM, step))
        self._pages.setCurrentIndex(step)
        self._btn_back.setEnabled(step > STEP_UPLOAD)
        self._btn_next.setVisible(step < STEP_CONFIRM)

        # Update step bar styling
        for i, lbl in enumerate(self._step_labels):
            if i < step:
                lbl.setStyleSheet(
                    "padding: 6px 10px; font-size: 10pt; color: #8fbc8f; "
                    "border-bottom: 3px solid #8fbc8f;"
                )
            elif i == step:
                lbl.setStyleSheet(
                    "padding: 6px 10px; font-size: 10pt; font-weight: bold; "
                    "color: white; border-bottom: 3px solid #6B8E23;"
                )
            else:
                lbl.setStyleSheet(
                    "padding: 6px 10px; font-size: 10pt; color: #aaa; "
                    "border-bottom: 3px solid transparent;"
                )

        # Populate step content when entering
        if step == STEP_PREVIEW:
            self._enter_preview_step()
        elif step == STEP_AI:
            self._enter_ai_step()
        elif step == STEP_BULK:
            self._enter_bulk_step()
        elif step == STEP_CONFIRM:
            self._enter_confirm_step()

    def _go_next(self):
        cur = self._pages.currentIndex()
        if cur == STEP_UPLOAD and not self._raw_rows:
            QMessageBox.warning(self, "No Data", "Please load a file or paste data first.")
            return
        if cur == STEP_PREVIEW:
            self._apply_mapping()
            if not self._mapped_rows:
                QMessageBox.warning(self, "No Data", "No mapped rows to process.")
                return
        self._go_to_step(cur + 1)

    def _go_back(self):
        cur = self._pages.currentIndex()
        self._go_to_step(cur - 1)

    def _reset_wizard(self):
        if self._raw_rows:
            reply = QMessageBox.question(
                self, "Start Over",
                "Discard current import data and start over?"
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._raw_rows.clear()
        self._headers.clear()
        self._mapped_rows.clear()
        self._ai_results.clear()
        self._validation_issues.clear()
        self._upload_file_label.setText("No file selected")
        self._upload_status.setText("")
        self._paste_edit.clear()
        self._preview_table.setRowCount(0)
        self._preview_table.setColumnCount(0)
        self._ai_table.setRowCount(0)
        self._ai_table.setColumnCount(0)
        self._bulk_table.setRowCount(0)
        self._bulk_table.setColumnCount(0)
        self._ai_progress.setValue(0)
        self._ai_status_label.setText("")
        self._import_result_label.setText("")
        self._go_to_step(STEP_UPLOAD)

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Upload Logic
    # ═══════════════════════════════════════════════════════════════

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Import File", "",
            "Spreadsheets (*.csv *.xlsx *.xls *.tsv);;All Files (*)"
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".xlsx", ".xls"):
                self._load_excel(path)
            elif ext == ".tsv":
                self._load_csv(path, delimiter="\t")
            else:
                self._load_csv(path)
            self._upload_file_label.setText(f"✅ {os.path.basename(path)}")
            self._upload_status.setText(f"✅ {len(self._raw_rows)} rows loaded, {len(self._headers)} columns detected")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _load_csv(self, path: str, delimiter: str = ","):
        with open(path, "r", encoding="utf-8-sig") as f:
            if self._has_header_cb.isChecked():
                reader = csv.DictReader(f, delimiter=delimiter)
                self._headers = list(reader.fieldnames or [])
                self._raw_rows = list(reader)
            else:
                reader = csv.reader(f, delimiter=delimiter)
                all_rows = list(reader)
                if not all_rows:
                    self._raw_rows = []
                    self._headers = []
                    return
                ncols = max(len(r) for r in all_rows)
                self._headers = [f"Column {i+1}" for i in range(ncols)]
                self._raw_rows = [
                    {self._headers[j]: (r[j] if j < len(r) else "") for j in range(ncols)}
                    for r in all_rows
                ]

    def _load_excel(self, path: str):
        try:
            import openpyxl
        except ImportError:
            QMessageBox.warning(
                self, "Missing Package",
                "Install openpyxl to import Excel files:\n\npip install openpyxl"
            )
            return
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        if self._has_header_cb.isChecked():
            header_row = next(rows_iter, None)
            if not header_row:
                self._raw_rows, self._headers = [], []
                return
            self._headers = [str(h or f"Column {i+1}") for i, h in enumerate(header_row)]
        else:
            first = next(rows_iter, None)
            if not first:
                self._raw_rows, self._headers = [], []
                return
            self._headers = [f"Column {i+1}" for i in range(len(first))]
            self._raw_rows = [{self._headers[j]: str(v or "") for j, v in enumerate(first)}]

        for row_vals in rows_iter:
            self._raw_rows.append({
                self._headers[j]: str(row_vals[j] if j < len(row_vals) and row_vals[j] is not None else "")
                for j in range(len(self._headers))
            })
        wb.close()
        self._upload_file_label.setText(f"✅ {os.path.basename(path)}")
        self._upload_status.setText(f"✅ {len(self._raw_rows)} rows loaded, {len(self._headers)} columns detected")

    def _load_pasted(self):
        text = self._paste_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty", "Please paste some data first.")
            return
        lines = text.split("\n")
        if not lines:
            return
        # Detect delimiter: tab preferred, then comma
        delim = "\t" if "\t" in lines[0] else ","
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        all_rows = list(reader)
        if not all_rows:
            return

        if self._has_header_cb.isChecked() and len(all_rows) > 1:
            self._headers = [h.strip() for h in all_rows[0]]
            self._raw_rows = [
                {self._headers[j]: (r[j].strip() if j < len(r) else "") for j in range(len(self._headers))}
                for r in all_rows[1:]
            ]
        else:
            ncols = max(len(r) for r in all_rows)
            self._headers = [f"Column {i+1}" for i in range(ncols)]
            self._raw_rows = [
                {self._headers[j]: (r[j].strip() if j < len(r) else "") for j in range(ncols)}
                for r in all_rows
            ]

        self._upload_file_label.setText("📋 Pasted data")
        self._upload_status.setText(f"✅ {len(self._raw_rows)} rows loaded, {len(self._headers)} columns detected")

    # ═══════════════════════════════════════════════════════════════
    # Step 2: Preview & Map
    # ═══════════════════════════════════════════════════════════════

    def _enter_preview_step(self):
        self._build_column_mapping()
        self._populate_preview_table()

    def _build_column_mapping(self):
        # Clear old combos
        for combo in self._mapping_combos:
            combo.setParent(None)
            combo.deleteLater()
        self._mapping_combos.clear()
        while self._mapping_row.count():
            item = self._mapping_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            child_lay = item.layout()
            if child_lay:
                while child_lay.count():
                    ci = child_lay.takeAt(0)
                    cw = ci.widget()
                    if cw:
                        cw.setParent(None)
                        cw.deleteLater()

        for header in self._headers:
            col_lay = QVBoxLayout()
            lbl = QLabel(header)
            lbl.setStyleSheet("font-size: 8pt; font-weight: bold;")
            combo = QComboBox()
            for field in MAPPED_FIELDS:
                combo.addItem(field)
            # Auto-detect
            h_lower = header.lower().strip()
            mapped = _AUTO_MAP.get(h_lower, "(skip)")
            idx = MAPPED_FIELDS.index(mapped) if mapped in MAPPED_FIELDS else 0
            combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self._on_mapping_changed)
            col_lay.addWidget(lbl)
            col_lay.addWidget(combo)
            self._mapping_row.addLayout(col_lay)
            self._mapping_combos.append(combo)
        self._mapping_row.addStretch()

    def _on_mapping_changed(self):
        self._populate_preview_table()

    def _apply_mapping(self):
        """Build mapped_rows from raw_rows + current mapping combos."""
        self._mapped_rows.clear()
        for raw in self._raw_rows:
            row: dict[str, str] = {}
            for i, header in enumerate(self._headers):
                if i < len(self._mapping_combos):
                    field = self._mapping_combos[i].currentText()
                    if field != "(skip)":
                        row[field] = str(raw.get(header, "")).strip()
            self._mapped_rows.append(row)
        self._validate_mapped_rows()

    def _populate_preview_table(self):
        """Show raw data with colour-coded validation."""
        self._apply_mapping()
        n_rows = len(self._raw_rows)
        n_cols = len(self._headers)
        self._preview_table.setRowCount(n_rows)
        self._preview_table.setColumnCount(n_cols)
        self._preview_table.setHorizontalHeaderLabels(self._headers)

        for i, raw in enumerate(self._raw_rows):
            mapped = self._mapped_rows[i] if i < len(self._mapped_rows) else {}
            issues = self._validation_issues[i] if i < len(self._validation_issues) else []
            for j, header in enumerate(self._headers):
                val = str(raw.get(header, ""))
                it = QTableWidgetItem(val)
                # Determine the mapped field for this column
                field = "(skip)"
                if j < len(self._mapping_combos):
                    field = self._mapping_combos[j].currentText()
                # Color coding
                bg = _CLR_OK
                if field == "sku" and not val.strip():
                    bg = _CLR_ERROR
                elif field == "title" and not val.strip():
                    bg = _CLR_ERROR
                elif field in ("cost", "selling_price"):
                    try:
                        float(val.replace("$", "").replace(",", "").strip() or "0")
                    except ValueError:
                        bg = _CLR_ERROR
                if issues:
                    for iss in issues:
                        if "duplicate" in iss.lower():
                            bg = _CLR_WARN
                            break
                it.setBackground(QBrush(bg))
                self._preview_table.setItem(i, j, it)

        self._preview_table.resizeColumnsToContents()

        # Validation summary
        n_errors = sum(1 for iss in self._validation_issues if any("❌" in s for s in iss))
        n_warns = sum(1 for iss in self._validation_issues if any("⚠" in s for s in iss))
        self._validation_label.setText(
            f"Rows: {n_rows}  |  "
            f"<span style='color:red;'>❌ Errors: {n_errors}</span>  |  "
            f"<span style='color:#b8860b;'>⚠️ Warnings: {n_warns}</span>"
        )

    def _validate_mapped_rows(self):
        """Run validation on each mapped row."""
        self._validation_issues.clear()
        for row in self._mapped_rows:
            issues: list[str] = []
            if not row.get("sku", "").strip() and not row.get("title", "").strip():
                issues.append("❌ Missing both SKU and Title")
            elif not row.get("title", "").strip():
                issues.append("⚠ Missing Title")
            # Check price/cost are numbers
            for fld in ("cost", "selling_price"):
                v = row.get(fld, "").strip()
                if v:
                    try:
                        float(v.replace("$", "").replace(",", ""))
                    except ValueError:
                        issues.append(f"❌ Invalid number in {fld}: {v}")
            self._validation_issues.append(issues)

    # ═══════════════════════════════════════════════════════════════
    # Step 3: AI Processing
    # ═══════════════════════════════════════════════════════════════

    def _enter_ai_step(self):
        if not self._mapped_rows:
            self._apply_mapping()
        self._ai_progress.setValue(0)
        self._ai_status_label.setText(
            f"{len(self._mapped_rows)} rows ready for AI processing.  "
            f"Press 'Run AI Processing' or 'Skip'."
        )

    def _run_ai_processing(self):
        if not self._mapped_rows:
            return
        self._ai_run_btn.setEnabled(False)
        self._ai_cancel_btn.setEnabled(True)
        self._ai_status_label.setText("Starting AI processing…")
        self._ai_progress.setMaximum(len(self._mapped_rows))
        self._ai_progress.setValue(0)
        self._ai_results.clear()

        # Load reference data
        try:
            cats = get_all_categories()
        except Exception:
            cats = []
        try:
            subcats = get_all_subcategories()
        except Exception:
            subcats = []

        # Vendor -> prefix map
        vendor_prefix: dict[str, str] = {}
        try:
            for p in list_sku_prefixes():
                mfg = (p.get("manufacturer") or "").lower()
                if mfg:
                    vendor_prefix[mfg] = p.get("prefix", "")
        except Exception:
            pass

        self._ai_worker = _AIWorker(
            self._mapped_rows, cats, subcats, vendor_prefix, parent=self
        )
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.row_done.connect(self._on_ai_row_done)
        self._ai_worker.finished_all.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _cancel_ai(self):
        if self._ai_worker:
            self._ai_worker.cancel()
            self._ai_status_label.setText("Cancelling…")

    def _on_ai_progress(self, current: int, total: int):
        self._ai_progress.setValue(current)
        self._ai_status_label.setText(f"Processing… {current}/{total} rows")

    def _on_ai_row_done(self, idx: int, enrichment: dict):
        pass  # Could update table in real time

    def _on_ai_error(self, msg: str):
        self._ai_run_btn.setEnabled(True)
        self._ai_cancel_btn.setEnabled(False)
        QMessageBox.warning(self, "AI Error", msg)

    def _on_ai_finished(self, results: list[dict]):
        self._ai_results = results
        self._ai_run_btn.setEnabled(True)
        self._ai_cancel_btn.setEnabled(False)
        self._ai_progress.setValue(self._ai_progress.maximum())
        self._ai_status_label.setText(
            f"✅ AI processing complete — {len(results)} rows enriched.  "
            "Review changes below, then proceed to Bulk Actions."
        )

        # Apply AI results to mapped rows
        for i, enr in enumerate(results):
            if i >= len(self._mapped_rows):
                break
            row = self._mapped_rows[i]
            if enr.get("cleaned_title"):
                row["_ai_title"] = enr["cleaned_title"]
            if enr.get("suggested_category"):
                row["_ai_category"] = enr["suggested_category"]
            if enr.get("suggested_subcategory"):
                row["_ai_subcategory"] = enr["suggested_subcategory"]
            if enr.get("is_garbage"):
                row["_ai_garbage"] = True
            if enr.get("possible_xrefs"):
                row["_ai_xrefs"] = enr["possible_xrefs"]
            if enr.get("notes"):
                row["_ai_notes"] = enr["notes"]

        self._populate_ai_table()

    def _populate_ai_table(self):
        """Show AI analysis results in a review table."""
        cols = [
            "Row", "Original Title", "AI Cleaned Title", "Category → Suggested",
            "Garbage?", "Cross-Refs Found", "AI Notes", "Accept",
        ]
        self._ai_table.setColumnCount(len(cols))
        self._ai_table.setHorizontalHeaderLabels(cols)
        self._ai_table.setRowCount(len(self._mapped_rows))

        for i, row in enumerate(self._mapped_rows):
            self._ai_table.setItem(i, 0, self._ro_item(str(i + 1)))
            self._ai_table.setItem(i, 1, self._ro_item(row.get("title", "")))
            self._ai_table.setItem(i, 2, QTableWidgetItem(row.get("_ai_title", "")))
            cat_text = f"{row.get('category', '')} → {row.get('_ai_category', '')}"
            self._ai_table.setItem(i, 3, QTableWidgetItem(cat_text))

            is_garbage = row.get("_ai_garbage", False)
            garb_item = QTableWidgetItem("🗑 YES" if is_garbage else "")
            if is_garbage:
                garb_item.setBackground(QBrush(_CLR_ERROR))
            self._ai_table.setItem(i, 4, garb_item)

            xrefs = row.get("_ai_xrefs", [])
            self._ai_table.setItem(i, 5, self._ro_item(", ".join(xrefs) if xrefs else ""))
            self._ai_table.setItem(i, 6, self._ro_item(row.get("_ai_notes", "")))

            # Accept checkbox (as editable text — "✅" or "")
            accept = QTableWidgetItem("✅")
            if is_garbage:
                accept = QTableWidgetItem("")
            self._ai_table.setItem(i, 7, accept)

        self._ai_table.resizeColumnsToContents()
        # Stretch the notes column
        header = self._ai_table.horizontalHeader()
        if header.count() > 6:
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Bulk Actions
    # ═══════════════════════════════════════════════════════════════

    # Canonical column order for bulk table
    _BULK_COLS = [
        "sku", "title", "manufacturer", "category", "subcategory",
        "cost", "selling_price", "qty_on_hand", "oem_part_number",
        "xref_part_number", "engine", "condition", "supplier",
    ]

    def _enter_bulk_step(self):
        # Apply accepted AI changes into mapped_rows
        self._apply_ai_acceptances()
        self._populate_bulk_table()
        self._load_bulk_combos()

    def _apply_ai_acceptances(self):
        """Apply AI suggestions that the user 'accepted' in the AI table."""
        if self._ai_table.rowCount() == 0:
            return
        rows_to_remove: list[int] = []
        for i in range(self._ai_table.rowCount()):
            if i >= len(self._mapped_rows):
                break
            accept_item = self._ai_table.item(i, 7)
            accepted = accept_item and accept_item.text().strip() == "✅"
            row = self._mapped_rows[i]

            if accepted:
                # Apply cleaned title
                ai_title = self._ai_table.item(i, 2)
                if ai_title and ai_title.text().strip():
                    row["title"] = ai_title.text().strip()
                # Apply suggested category
                ai_cat = row.get("_ai_category", "")
                if ai_cat:
                    row["category"] = ai_cat
                ai_subcat = row.get("_ai_subcategory", "")
                if ai_subcat:
                    row["subcategory"] = ai_subcat
                # Apply xrefs
                ai_xrefs = row.get("_ai_xrefs", [])
                if ai_xrefs and not row.get("xref_part_number"):
                    row["xref_part_number"] = ", ".join(ai_xrefs)
            # Remove garbage rows (whether accepted or not — garbage is garbage)
            if row.get("_ai_garbage") and not accepted:
                rows_to_remove.append(i)

        # Remove garbage rows (reverse to keep indices valid)
        for idx in reversed(rows_to_remove):
            self._mapped_rows.pop(idx)

        # Clean up AI temp keys
        for row in self._mapped_rows:
            for k in list(row.keys()):
                if k.startswith("_ai_"):
                    del row[k]

    def _populate_bulk_table(self):
        cols = self._BULK_COLS
        n_rows = len(self._mapped_rows)
        self._bulk_table.setRowCount(n_rows)
        self._bulk_table.setColumnCount(len(cols))
        self._bulk_table.setHorizontalHeaderLabels(
            [c.replace("_", " ").title() for c in cols]
        )

        for i, row in enumerate(self._mapped_rows):
            for j, col in enumerate(cols):
                val = str(row.get(col, ""))
                it = QTableWidgetItem(val)
                # Highlight missing SKU
                if col == "sku" and not val.strip():
                    it.setBackground(QBrush(_CLR_WARN))
                self._bulk_table.setItem(i, j, it)

        self._bulk_table.resizeColumnsToContents()
        self._bulk_summary.setText(f"Total rows: {n_rows}")

    def _load_bulk_combos(self):
        """Populate bulk-action combo boxes."""
        # Vendors
        self._bulk_vendor_combo.clear()
        self._bulk_vendor_combo.addItem("(select vendor)")
        try:
            for v in get_all_vendors():
                self._bulk_vendor_combo.addItem(str(v.get("name", "")), v.get("id"))
        except Exception:
            pass
        # Categories
        self._bulk_cat_combo.clear()
        try:
            for c in get_all_categories():
                self._bulk_cat_combo.addItem(c)
        except Exception:
            pass

    # ── Bulk action handlers ──

    def _bulk_apply_vendor(self):
        vendor = self._bulk_vendor_combo.currentText()
        if vendor == "(select vendor)":
            return
        col_idx = self._BULK_COLS.index("supplier") if "supplier" in self._BULK_COLS else -1
        mfg_idx = self._BULK_COLS.index("manufacturer") if "manufacturer" in self._BULK_COLS else -1
        for i in range(self._bulk_table.rowCount()):
            if col_idx >= 0:
                self._bulk_table.setItem(i, col_idx, QTableWidgetItem(vendor))
            if mfg_idx >= 0 and not (self._bulk_table.item(i, mfg_idx) and
                                      self._bulk_table.item(i, mfg_idx).text().strip()):
                self._bulk_table.setItem(i, mfg_idx, QTableWidgetItem(vendor))

    def _bulk_apply_category(self):
        cat = self._bulk_cat_combo.currentText().strip()
        if not cat:
            return
        col_idx = self._BULK_COLS.index("category") if "category" in self._BULK_COLS else -1
        if col_idx < 0:
            return
        for i in range(self._bulk_table.rowCount()):
            self._bulk_table.setItem(i, col_idx, QTableWidgetItem(cat))

    def _bulk_apply_margin(self):
        """Calculate selling_price from cost using the margin %."""
        margin_pct = self._bulk_margin_spin.value()
        if margin_pct <= 0:
            return
        cost_idx = self._BULK_COLS.index("cost") if "cost" in self._BULK_COLS else -1
        price_idx = self._BULK_COLS.index("selling_price") if "selling_price" in self._BULK_COLS else -1
        if cost_idx < 0 or price_idx < 0:
            return
        count = 0
        for i in range(self._bulk_table.rowCount()):
            cost_item = self._bulk_table.item(i, cost_idx)
            cost_val = 0.0
            if cost_item:
                try:
                    cost_val = float(cost_item.text().replace("$", "").replace(",", "").strip() or "0")
                except ValueError:
                    continue
            if cost_val > 0:
                price = cost_val / (1 - margin_pct / 100.0)
                self._bulk_table.setItem(i, price_idx, QTableWidgetItem(f"{price:.2f}"))
                count += 1
        self._bulk_summary.setText(f"Applied {margin_pct}% margin to {count} rows")

    def _bulk_find_replace(self):
        find_text, ok = QInputDialog.getText(self, "Find & Replace", "Find text:")
        if not ok or not find_text:
            return
        replace_text, ok = QInputDialog.getText(
            self, "Find & Replace", f"Replace '{find_text}' with:"
        )
        if not ok:
            return
        count = 0
        for i in range(self._bulk_table.rowCount()):
            for j in range(self._bulk_table.columnCount()):
                item = self._bulk_table.item(i, j)
                if item:
                    old = item.text()
                    if find_text in old:
                        item.setText(old.replace(find_text, replace_text))
                        count += 1
        self._bulk_summary.setText(f"Replaced {count} occurrences")

    def _bulk_remove_rows(self):
        selected = set()
        for item in self._bulk_table.selectedItems():
            selected.add(item.row())
        if not selected:
            QMessageBox.information(self, "No Selection", "Select rows to remove first.")
            return
        reply = QMessageBox.question(
            self, "Remove Rows", f"Remove {len(selected)} selected rows?"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for idx in sorted(selected, reverse=True):
            self._bulk_table.removeRow(idx)
            if idx < len(self._mapped_rows):
                self._mapped_rows.pop(idx)
        self._bulk_summary.setText(f"Removed {len(selected)} rows — {self._bulk_table.rowCount()} remaining")

    def _bulk_scan_duplicates(self):
        """Check each row's SKU/title against existing inventory."""
        dup_count = 0
        for i in range(self._bulk_table.rowCount()):
            sku_item = self._bulk_table.item(i, self._BULK_COLS.index("sku"))
            title_item = self._bulk_table.item(i, self._BULK_COLS.index("title"))
            sku = sku_item.text().strip() if sku_item else ""
            title = title_item.text().strip() if title_item else ""
            try:
                dupes = find_similar_products(sku=sku, title=title)
            except Exception:
                dupes = []
            if dupes:
                dup_count += 1
                for j in range(self._bulk_table.columnCount()):
                    item = self._bulk_table.item(i, j)
                    if item:
                        item.setBackground(QBrush(_CLR_WARN))
                # Add tooltip with duplicate info
                tip_lines = [f"⚠ Possible duplicate of: {d.get('sku', '')} — {d.get('title', '')}"
                             for d in dupes[:3]]
                if sku_item:
                    sku_item.setToolTip("\n".join(tip_lines))

        self._bulk_summary.setText(
            f"Scan complete — {dup_count} potential duplicate(s) highlighted in yellow"
        )

    def _bulk_generate_skus(self):
        """Generate SKUs for rows that don't have one."""
        sku_idx = self._BULK_COLS.index("sku")
        mfg_idx = self._BULK_COLS.index("manufacturer")
        supplier_idx = self._BULK_COLS.index("supplier")
        count = 0
        for i in range(self._bulk_table.rowCount()):
            sku_item = self._bulk_table.item(i, sku_idx)
            if sku_item and sku_item.text().strip():
                continue  # Already has SKU
            # Determine prefix from manufacturer or supplier
            mfg_item = self._bulk_table.item(i, mfg_idx)
            sup_item = self._bulk_table.item(i, supplier_idx)
            prefix = "JAKS"
            mfg_text = (mfg_item.text().strip() if mfg_item else "").upper()
            sup_text = (sup_item.text().strip() if sup_item else "").upper()
            # Try to find a prefix for this vendor
            if mfg_text:
                try:
                    prefixes = list_sku_prefixes()
                    for p in prefixes:
                        if (p.get("manufacturer") or "").upper() == mfg_text:
                            prefix = p["prefix"]
                            break
                except Exception:
                    pass
            elif sup_text:
                prefix = sup_text[:4]  # Fallback: first 4 chars

            try:
                new_sku = generate_next_sku(prefix)
                self._bulk_table.setItem(i, sku_idx, QTableWidgetItem(new_sku))
                self._bulk_table.item(i, sku_idx).setBackground(QBrush(_CLR_AI))
                count += 1
            except Exception:
                pass
        self._bulk_summary.setText(f"Generated {count} new SKU(s)")

    # ═══════════════════════════════════════════════════════════════
    # Step 5: Confirm & Import
    # ═══════════════════════════════════════════════════════════════

    def _enter_confirm_step(self):
        # Sync bulk table edits back to mapped_rows
        self._sync_bulk_table_to_mapped()

        # Load prefix combo
        self._confirm_auto_sku_combo.clear()
        self._confirm_auto_sku_combo.addItem("(no auto-SKU for blanks)", "")
        try:
            for p in list_sku_prefixes():
                self._confirm_auto_sku_combo.addItem(
                    f"JAKS-{p.get('prefix', '')}…", p.get("prefix", "")
                )
        except Exception:
            pass

        # Build summary
        total = len(self._mapped_rows)
        has_sku = sum(1 for r in self._mapped_rows if r.get("sku", "").strip())
        no_sku = total - has_sku
        has_title = sum(1 for r in self._mapped_rows if r.get("title", "").strip())
        has_cost = sum(1 for r in self._mapped_rows if self._parse_float(r.get("cost", "")) > 0)
        has_price = sum(1 for r in self._mapped_rows if self._parse_float(r.get("selling_price", "")) > 0)
        cats = set(r.get("category", "") for r in self._mapped_rows if r.get("category"))
        mfgs = set(r.get("manufacturer", "") for r in self._mapped_rows if r.get("manufacturer"))

        html = (
            f"<h2>Import Summary</h2>"
            f"<table cellpadding='4'>"
            f"<tr><td><b>Total rows:</b></td><td>{total}</td></tr>"
            f"<tr><td><b>With SKU:</b></td><td>{has_sku}</td></tr>"
            f"<tr><td><b>Without SKU:</b></td><td style='color:{'red' if no_sku else 'green'};'>"
            f"{no_sku}{'  (will need auto-SKU prefix)' if no_sku else ''}</td></tr>"
            f"<tr><td><b>With Title:</b></td><td>{has_title}</td></tr>"
            f"<tr><td><b>With Cost:</b></td><td>{has_cost}</td></tr>"
            f"<tr><td><b>With Price:</b></td><td>{has_price}</td></tr>"
            f"<tr><td><b>Categories:</b></td><td>{', '.join(sorted(cats)) or '(none)'}</td></tr>"
            f"<tr><td><b>Manufacturers:</b></td><td>{', '.join(sorted(mfgs)) or '(none)'}</td></tr>"
            f"</table>"
        )

        # Sample first 5 rows
        html += "<h3>Sample Rows</h3><table border='1' cellpadding='3' style='border-collapse:collapse;'>"
        html += "<tr>"
        for col in ["sku", "title", "manufacturer", "category", "cost", "selling_price"]:
            html += f"<th style='background:#ddd;'>{col.replace('_', ' ').title()}</th>"
        html += "</tr>"
        for row in self._mapped_rows[:5]:
            html += "<tr>"
            for col in ["sku", "title", "manufacturer", "category", "cost", "selling_price"]:
                html += f"<td>{row.get(col, '')}</td>"
            html += "</tr>"
        if total > 5:
            html += f"<tr><td colspan='6' style='text-align:center;'>… and {total - 5} more rows</td></tr>"
        html += "</table>"

        self._confirm_summary.setHtml(html)
        self._import_result_label.setText("")

    def _sync_bulk_table_to_mapped(self):
        """Read edits from bulk table back into mapped_rows."""
        cols = self._BULK_COLS
        new_mapped: list[dict] = []
        for i in range(self._bulk_table.rowCount()):
            row: dict[str, str] = {}
            for j, col in enumerate(cols):
                item = self._bulk_table.item(i, j)
                row[col] = item.text().strip() if item else ""
            # Preserve any extra fields from original mapped row
            if i < len(self._mapped_rows):
                for k, v in self._mapped_rows[i].items():
                    if k not in row and not k.startswith("_ai_"):
                        row[k] = v
            new_mapped.append(row)
        self._mapped_rows = new_mapped

    def _run_import(self):
        if not self._mapped_rows:
            return

        auto_prefix = self._confirm_auto_sku_combo.currentData() or None
        update = self._confirm_update_cb.isChecked()

        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Import {len(self._mapped_rows)} products?\n\n"
            f"Auto-SKU prefix: {auto_prefix or '(none)'}\n"
            f"Update existing: {'Yes' if update else 'No'}"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = bulk_import_products(
                self._mapped_rows,
                auto_sku_prefix=auto_prefix,
                update_existing=update,
                filename="bulk_wizard",
            )
            msg = (
                f"<h2>✅ Import Complete!</h2>"
                f"<p style='font-size:12pt;'>"
                f"<b>New products added:</b> {result['imported']}<br>"
                f"<b>Updated:</b> {result['updated']}<br>"
                f"<b>Skipped:</b> {result['skipped']}<br>"
                f"<b>Errors:</b> {len(result['errors'])}"
                f"</p>"
            )
            dup_skus = result.get("duplicate_skus") or []
            if dup_skus:
                msg += (
                    f"<h3 style='color:#D97706;'>⚠ Duplicate SKUs "
                    f"({len(dup_skus)}):</h3><p>"
                    + ", ".join(dup_skus[:30])
                    + ("…" if len(dup_skus) > 30 else "")
                    + "</p>"
                )
            if result["errors"]:
                msg += "<h3>Errors:</h3><ul>"
                for e in result["errors"][:20]:
                    msg += f"<li>{e}</li>"
                msg += "</ul>"
            self._import_result_label.setText(msg)
            self._import_btn.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _ro_item(text: str) -> QTableWidgetItem:
        """Create a read-only table item."""
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    @staticmethod
    def _parse_float(val: str | None) -> float:
        if not val:
            return 0.0
        try:
            return float(str(val).replace("$", "").replace(",", "").strip())
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _vsep() -> QWidget:
        """Vertical separator for toolbars."""
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setFixedHeight(24)
        sep.setStyleSheet("background-color: #ccc;")
        return sep
