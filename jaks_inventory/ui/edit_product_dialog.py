"""Edit Product Dialog - Comprehensive product editor for JAKS Inventory."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, QDate
from PySide6.QtGui import QColor, QIcon, QKeySequence, QImage, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QComboBox,
    QCheckBox,
    QFrame,
    QWidget,
    QGroupBox,
    QCompleter,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
)

from db import (
    get_product_by_sku,
    get_product_by_id,
    update_product,
    create_product,
    get_all_vendors,
    get_all_categories,
    get_all_manufacturers,
    get_all_engine_manufacturers,
    get_all_engine_models,
    get_setting,
    adjust_product_quantity,
    generate_next_sku,
    get_sku_prefix_for_vendor,
    find_similar_products,
    get_interchanges,
    add_interchange,
    delete_interchange,
    get_latest_competitor_prices,
    get_pending_scraped_xrefs,
    accept_scraped_xref,
    reject_scraped_xref,
    get_product_images,
    add_product_image,
    delete_product_image,
    browse_products,
    search_by_part_number,
    add_suggested_sell,
    get_suggested_sells,
    delete_suggested_sell,
)

from .scraper_review_dialog import ScraperReviewDialog
from ..scraper import ScraperWorker

# Common diesel engine manufacturers for initial dropdown
_DEFAULT_ENGINE_MFRS = [
    "Caterpillar", "Cummins", "Detroit Diesel", "International / Navistar",
    "Mack", "PACCAR", "Volvo",
]

# Engine manufacturer abbreviations for invoice line
_ENGINE_MFR_ABBREV: dict[str, str] = {
    "Caterpillar": "CAT",
    "Cummins": "CUM",
    "Detroit Diesel": "DD",
    "International / Navistar": "INT",
    "International": "INT",
    "Navistar": "NAV",
    "Mack": "MACK",
    "PACCAR": "PAC",
    "Volvo": "VOL",
}

# Common engine models by manufacturer
_DEFAULT_ENGINE_MODELS = [
    "ISX 15", "ISX 12", "ISB 6.7", "ISC 8.3", "ISL 8.9", "ISM 11",
    "N14", "M11", "L10", "855",
    "C15", "C13", "C12", "C9", "C7", "3406E", "3406B", "3126",
    "DD15", "DD13", "DD16", "Series 60 14L", "Series 60 12.7",
    "DT466", "DT530", "MaxxForce 13", "A26",
    "MX-13", "MX-11", "D13", "D11",
]

# Canonical aliases for manufacturer normalization.
_ENGINE_MFR_ALIASES: dict[str, str] = {
    "cat": "Caterpillar",
    "caterpillar": "Caterpillar",
    "cummins": "Cummins",
    "detroit": "Detroit Diesel",
    "detroit diesel": "Detroit Diesel",
    "dd": "Detroit Diesel",
    "international": "International / Navistar",
    "navistar": "International / Navistar",
    "international / navistar": "International / Navistar",
    "mack": "Mack",
    "paccar": "PACCAR",
    "volvo": "Volvo",
}

# Model token -> likely manufacturer (guardrail inference, not hard enforcement).
_ENGINE_MODEL_HINTS: list[tuple[str, str]] = [
    ("C15", "Caterpillar"),
    ("C13", "Caterpillar"),
    ("C12", "Caterpillar"),
    ("C9", "Caterpillar"),
    ("C7", "Caterpillar"),
    ("3406", "Caterpillar"),
    ("3126", "Caterpillar"),
    ("ISX", "Cummins"),
    ("X15", "Cummins"),
    ("X12", "Cummins"),
    ("X10", "Cummins"),
    ("ISB", "Cummins"),
    ("ISC", "Cummins"),
    ("ISL", "Cummins"),
    ("ISM", "Cummins"),
    ("N14", "Cummins"),
    ("M11", "Cummins"),
    ("L10", "Cummins"),
    ("855", "Cummins"),
    ("DD13", "Detroit Diesel"),
    ("DD15", "Detroit Diesel"),
    ("DD16", "Detroit Diesel"),
    ("SERIES 60", "Detroit Diesel"),
    ("DT466", "International / Navistar"),
    ("DT530", "International / Navistar"),
    ("MAXXFORCE", "International / Navistar"),
    ("A26", "International / Navistar"),
    ("MX-11", "PACCAR"),
    ("MX-13", "PACCAR"),
    ("D13", "Volvo"),
    ("D11", "Volvo"),
]

_ENGINE_MODELS_BY_MFR: dict[str, list[str]] = {
    "Caterpillar": ["C15", "C13", "C12", "C9", "C7", "3406E", "3406B", "3126"],
    "Cummins": ["ISX 15", "ISX 12", "ISB 6.7", "ISC 8.3", "ISL 8.9", "ISM 11", "N14", "M11", "L10", "855", "X15", "X12", "X10"],
    "Detroit Diesel": ["DD15", "DD13", "DD16", "Series 60 14L", "Series 60 12.7"],
    "International / Navistar": ["DT466", "DT530", "MaxxForce 13", "A26"],
    "PACCAR": ["MX-13", "MX-11"],
    "Volvo": ["D13", "D11", "D13TC"],
    "Mack": ["MP7", "MP8", "MP10"],
}

# Common truck manufacturers
_DEFAULT_TRUCK_MFRS = [
    "Kenworth", "Peterbilt", "Freightliner", "Western Star",
    "Volvo", "Mack", "International / Navistar",
]

_TRUCK_MODEL_HINTS: list[tuple[str, str]] = [
    ("T680", "Kenworth"),
    ("T880", "Kenworth"),
    ("W900", "Kenworth"),
    ("W990", "Kenworth"),
    ("579", "Peterbilt"),
    ("589", "Peterbilt"),
    ("567", "Peterbilt"),
    ("CASCADIA", "Freightliner"),
    ("M2", "Freightliner"),
    ("47X", "Western Star"),
    ("49X", "Western Star"),
    ("57X", "Western Star"),
    ("VNL", "Volvo"),
    ("VNR", "Volvo"),
    ("VNX", "Volvo"),
    ("VHD", "Volvo"),
    ("VAH", "Volvo"),
    ("ANTHEM", "Mack"),
    ("GRANITE", "Mack"),
    ("LR", "Mack"),
    ("MD", "Mack"),
    ("PINNACLE", "Mack"),
    ("LT", "International / Navistar"),
    ("RH", "International / Navistar"),
    ("HV", "International / Navistar"),
    ("HX", "International / Navistar"),
    ("MV", "International / Navistar"),
]

_TRUCK_MODELS_BY_MFR: dict[str, list[str]] = {
    "Kenworth": ["T680", "T680E", "T880", "T880E", "T800", "W990", "W900L", "T280", "T380", "T480"],
    "Peterbilt": ["579", "589", "567", "520", "548", "537", "536"],
    "Freightliner": ["Cascadia", "M2 106", "M2 112", "SD", "eCascadia", "eM2"],
    "Western Star": ["47X", "49X", "57X", "6900XD"],
    "Volvo": ["VNL", "VNR", "VNR Electric", "VNX", "VHD", "VAH"],
    "Mack": ["Anthem", "Granite", "Pioneer", "LR", "LR Electric", "MD", "MD Electric", "Pinnacle", "TerraPro"],
    "International / Navistar": ["LT", "RH", "HV", "HX", "MV", "eMV"],
}

# Common truck systems
_DEFAULT_TRUCK_SYSTEMS = [
    "Suspension", "Brakes", "Exhaust / Aftertreatment", "Cooling",
    "Electrical", "Fuel System", "Air System", "Steering",
    "Drivetrain", "HVAC", "Body / Cab",
]


class _PAILookupWorker(QThread):
    """Background worker for PAI lookup — Playwright scraper with Grok AI selection."""
    finished = Signal(dict)       # Selected result dict (with stock, pricing, reasoning)
    multi_match = Signal(list)    # List of PAIPartResult dicts (for manual pick)
    error = Signal(str)
    status_update = Signal(str)   # Progress messages

    def __init__(self, part_number: str, product_name: str = "", parent=None):
        super().__init__(parent)
        self._part_number = part_number
        self._product_name = product_name

    def run(self):
        try:
            from jaks_inventory.scraper.pai_scraper import (
                search_pai_portal,
                grok_select_best_match,
            )

            last_status = ""

            def _status(msg):
                nonlocal last_status
                last_status = msg
                self.status_update.emit(msg)

            results = search_pai_portal(
                self._part_number,
                headless=True,
                on_status=_status,
                fetch_detail=True,
            )

            if not results:
                # Snapshot the real PAI status BEFORE the fallback _status() overwrites it.
                pre_fallback_status = last_status
                # Fallback: try requests-based PAI client
                _status("Playwright found nothing — trying HTTP fallback…")
                fallback = self._try_requests_fallback()
                if fallback:
                    self.finished.emit(fallback)
                else:
                    real_status = pre_fallback_status or last_status
                    ls = real_status.lower()
                    # Always surface a real PAI status verbatim if we have one.
                    if real_status and (
                        "pai login" in ls
                        or "login failed" in ls
                        or "credentials" in ls
                        or "did not complete" in ls
                        or "captcha" in ls
                        or "search error" in ls
                        or "error" in ls
                        or "failed" in ls
                    ):
                        self.error.emit(
                            f"PAI lookup failed for '{self._part_number}':\n\n{real_status}"
                        )
                    else:
                        self.error.emit(
                            f"No PAI results for '{self._part_number}'.\n\n"
                            f"Last status: {real_status or '(none)'}\n\n"
                            "If this looks wrong, open Settings > Scraper and click "
                            "“Test PAI Login” to log in interactively."
                        )
                return

            if len(results) == 1:
                r = results[0]
                self.finished.emit(self._result_to_dict(r, "Only one result"))
                return

            # Multiple results — use Grok to pick best
            _status(f"Grok AI selecting best from {len(results)} results…")
            best, reasoning = grok_select_best_match(
                results,
                product_name=self._product_name,
                search_term=self._part_number,
            )

            if best:
                result_dict = self._result_to_dict(best, reasoning)
                # Include all results so UI can show alternatives
                result_dict["all_results"] = [self._result_to_dict(r, "") for r in results]
                result_dict["grok_reasoning"] = reasoning
                self.finished.emit(result_dict)
            else:
                # Emit multi-match for manual selection
                self.multi_match.emit([self._result_to_dict(r, "") for r in results])

        except Exception as exc:
            self.error.emit(str(exc))

    def _result_to_dict(self, r, reasoning: str = "") -> dict:
        """Convert PAIPartResult to dict for signal emission."""
        image_urls = list(getattr(r, "image_urls", []) or [])
        if not image_urls and getattr(r, "image_url", ""):
            image_urls = [r.image_url]
        return {
            "pai_sku": r.sku,
            "sku": r.sku,
            "cost": r.your_price,
            "list_price": r.list_price,
            "url": r.detail_url,
            "source": r.detail_url,
            "stock": r.stock,
            "in_stock": r.in_stock,
            "total_available": r.total_available,
            "not_available": r.not_available,
            "description": r.description,
            "oem_number": r.oem_number,
            "product_group": r.product_group,
            "warranty": r.warranty,
            "weight": r.weight,
            "sell_pack": r.sell_pack,
            "image_url": r.image_url,
            "image_urls": image_urls,
            "searched_sku": self._part_number,
            "is_cross_reference": False,
            "grok_reasoning": reasoning,
        }

    def _try_requests_fallback(self) -> dict | None:
        """Fallback to requests-based PAI client if Playwright fails."""
        try:
            from sources.pai_client import PAIClient
            username = get_setting("pai_username") or ""
            password = get_setting("pai_password") or ""
            if not username or not password:
                return None
            client = PAIClient(username=username, password=password)
            result = client.lookup_cost(self._part_number)
            if result:
                return dict(result)
        except Exception:
            pass
        return None


class _PAIMultiResultDialog(QDialog):
    """Dialog to let the user pick from multiple PAI cross-reference matches."""

    def __init__(self, matches: list[dict], searched: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"PAI: Multiple Matches for '{searched}'")
        self.setMinimumSize(650, 350)
        self._selected: dict | None = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"<b>{len(matches)}</b> PAI results matched OEM '<b>{searched}</b>'."
            " Select one to apply:"
        ))

        self._table = QTableWidget(len(matches), 5)
        self._table.setHorizontalHeaderLabels(["SKU", "Cost", "In Stock", "Stock Detail", "URL"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)

        for row, m in enumerate(matches):
            self._table.setItem(row, 0, QTableWidgetItem(str(m.get("sku", ""))))
            cost = m.get("cost")
            self._table.setItem(row, 1, QTableWidgetItem(f"${cost:,.2f}" if cost else "N/A"))
            in_stock = m.get("in_stock", False)
            item = QTableWidgetItem("✔ Yes" if in_stock else "✖ No")
            self._table.setItem(row, 2, item)
            stock = m.get("stock") or {}
            stock_str = ", ".join(f"{k}: {v}" for k, v in stock.items()) if stock else ""
            self._table.setItem(row, 3, QTableWidgetItem(stock_str))
            self._table.setItem(row, 4, QTableWidgetItem(str(m.get("url", ""))))

        self._table.resizeColumnsToContents()
        self._table.doubleClicked.connect(self._on_double_click)
        lay.addWidget(self._table)

        self._matches = matches

        btn_row = QHBoxLayout()
        select_btn = QPushButton("Select")
        select_btn.clicked.connect(self._on_select)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(select_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        # Auto-select first row
        if matches:
            self._table.selectRow(0)

    def _on_select(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._matches):
            self._selected = self._matches[row]
            self.accept()

    def _on_double_click(self, index):
        row = index.row()
        if 0 <= row < len(self._matches):
            self._selected = self._matches[row]
            self.accept()

    def selected_match(self) -> dict | None:
        return self._selected


class _AIDescriptionWorker(QThread):
    """Background worker to generate brief description via Grok AI."""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, api_key: str, title: str, category: str,
                 oem_pn: str, eng_mfr: str, eng_model: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._title = title
        self._category = category
        self._oem_pn = oem_pn
        self._eng_mfr = eng_mfr
        self._eng_model = eng_model

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=self._api_key,
                base_url="https://api.x.ai/v1",
            )

            context_parts = [f"Product: {self._title}"]
            if self._category:
                context_parts.append(f"Category: {self._category}")
            if self._oem_pn:
                context_parts.append(f"OEM Part #: {self._oem_pn}")
            if self._eng_mfr:
                context_parts.append(f"Engine: {self._eng_mfr}")
            if self._eng_model:
                context_parts.append(f"Model: {self._eng_model}")
            context = "\n".join(context_parts)

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=60,
                messages=[
                    {"role": "system", "content": (
                        "You are a diesel parts specialist. Given a product listing, "
                        "return ONLY a brief 3-6 word description of what the part is "
                        "(e.g. 'Turbocharger', 'Water Pump', 'Fuel Injector', "
                        "'EGR Cooler', 'Oil Pan Gasket'). No punctuation, no explanation."
                    )},
                    {"role": "user", "content": context},
                ],
            )
            desc = response.choices[0].message.content.strip()
            self.finished.emit(desc)
        except Exception as exc:
            self.error.emit(str(exc))


class _AIEnrichWorker(QThread):
    """Background worker that uses Grok to enrich multiple product fields at once.

    Given a product title and OEM part number, uses AI to determine:
    - brief_description (what the part is)
    - category (product category)
    - subcategory (product subcategory)
    - engine_manufacturer (e.g. Caterpillar, Cummins)
    - engine_model (e.g. C15, ISX 15)
    """
    finished = Signal(dict)   # dict with keys: brief_description, category, subcategory, engine_manufacturer, engine_model
    error = Signal(str)

    def __init__(self, api_key: str, title: str, oem_pn: str,
                 categories: list[str], subcategories: list[str], parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._title = title
        self._oem_pn = oem_pn
        self._categories = categories
        self._subcategories = subcategories

    def run(self):
        try:
            import json as _json
            from openai import OpenAI
            client = OpenAI(
                api_key=self._api_key,
                base_url="https://api.x.ai/v1",
            )

            cat_list = ", ".join(self._categories[:30]) if self._categories else "Engine Parts, Cylinder Heads, Turbos, Fuel System, Cooling, Exhaust, Electrical, Air Intake"
            subcat_list = ", ".join(self._subcategories[:30]) if self._subcategories else ""

            context_parts = [f"Product title: {self._title}"]
            if self._oem_pn:
                context_parts.append(f"OEM Part Number: {self._oem_pn}")
            context = "\n".join(context_parts)

            system_prompt = (
                "You are an expert diesel engine parts specialist. "
                "Given a product listing, classify it and return JSON ONLY with these fields:\n"
                '- "brief_description": 2-5 word description of what the part IS (e.g. "Turbocharger", "Water Pump", "Cylinder Head")\n'
                f'- "category": best matching category from: [{cat_list}]. Use existing if possible, or suggest a new one.\n'
            )
            if subcat_list:
                system_prompt += f'- "subcategory": best matching from: [{subcat_list}], or suggest a new one.\n'
            else:
                system_prompt += '- "subcategory": a relevant subcategory for the part.\n'
            system_prompt += (
                '- "engine_manufacturer": the engine OEM (Caterpillar, Cummins, Detroit Diesel, International / Navistar, Volvo, Mack, PACCAR) or "" if unclear.\n'
                '- "engine_model": specific engine model (e.g. C15, C15 Acert, ISX 15, DD15, 3406E, Series 60 14L) or "" if unclear.\n'
                "\nReturn ONLY valid JSON, no markdown, no explanation."
            )

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=200,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context},
                ],
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = _json.loads(raw)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class _AIOemLookupWorker(QThread):
    """Background worker to identify an OEM part number via Grok."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, api_key: str, oem_pn: str, title: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._oem_pn = oem_pn
        self._title = title

    def run(self):
        try:
            import json as _json
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key, base_url="https://api.x.ai/v1")

            context = f"OEM Part Number: {self._oem_pn}"
            if self._title:
                context += f"\nProduct title (may be inaccurate): {self._title}"

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=200,
                messages=[
                    {"role": "system", "content": (
                        "You are a diesel engine parts expert. Given an OEM part number, "
                        "identify what the part is. Return JSON ONLY with:\n"
                        '- "description": brief part type (e.g. "Turbocharger", "Water Pump")\n'
                        '- "engine_manufacturer": the OEM (Caterpillar, Cummins, etc.) or ""\n'
                        '- "engine_model": engine model (C15, ISX 15, etc.) or ""\n'
                        '- "category": part category or ""\n'
                        "No markdown, no explanation, JSON only."
                    )},
                    {"role": "user", "content": context},
                ],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            self.finished.emit(_json.loads(raw))
        except Exception as exc:
            self.error.emit(str(exc))


class _AIPriceSuggestionWorker(QThread):
    """Background worker to suggest pricing based on competitor data."""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, api_key: str, title: str, comp_prices: dict,
                 cost: float, current_sell: float, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._title = title
        self._comp_prices = comp_prices
        self._cost = cost
        self._current_sell = current_sell

    def run(self):
        try:
            import json as _json
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key, base_url="https://api.x.ai/v1")

            comp_text = "\n".join(f"  {k}: ${v:,.2f}" for k, v in self._comp_prices.items())
            context = f"Product: {self._title}\n"
            if self._cost > 0:
                context += f"Our cost: ${self._cost:,.2f}\n"
            if self._current_sell > 0:
                context += f"Current sell price: ${self._current_sell:,.2f}\n"
            if comp_text:
                context += f"Competitor prices:\n{comp_text}\n"

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=300,
                messages=[
                    {"role": "system", "content": (
                        "You are a diesel parts pricing expert. Analyze the competitor "
                        "prices and cost to suggest an optimal selling price. "
                        "We typically target 30-40% margin while staying competitive. "
                        "Return JSON ONLY with:\n"
                        '- "suggested_price": number (the suggested sell price)\n'
                        '- "margin_percent": number (estimated margin % if cost is known)\n'
                        '- "reasoning": brief 1-2 sentence explanation\n'
                        "No markdown, JSON only."
                    )},
                    {"role": "user", "content": context},
                ],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            self.finished.emit(_json.loads(raw))
        except Exception as exc:
            self.error.emit(str(exc))


class _AITagsWorker(QThread):
    """Background worker to generate Shopify tags."""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, api_key: str, context: dict, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._context = context

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key, base_url="https://api.x.ai/v1")

            info = "\n".join(f"{k}: {v}" for k, v in self._context.items() if v)

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=150,
                messages=[
                    {"role": "system", "content": (
                        "You are a Shopify SEO specialist for diesel truck parts. "
                        "Generate 8-15 comma-separated tags for this product. "
                        "Include: part type, engine manufacturer, engine model, "
                        "condition, category, common search terms. "
                        "Tags should be lowercase, no hashtags. "
                        "Return ONLY the comma-separated tags, nothing else."
                    )},
                    {"role": "user", "content": info},
                ],
            )
            tags = response.choices[0].message.content.strip()
            self.finished.emit(tags)
        except Exception as exc:
            self.error.emit(str(exc))


class _AIShopifyDescWorker(QThread):
    """Background worker to generate Shopify product description HTML."""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, api_key: str, context: dict, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._context = context

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key, base_url="https://api.x.ai/v1")

            info = "\n".join(f"{k}: {v}" for k, v in self._context.items() if v)

            response = client.chat.completions.create(
                model="grok-3",
                max_tokens=800,
                messages=[
                    {"role": "system", "content": (
                        "You are a product copywriter for a diesel truck parts retailer. "
                        "Write a professional Shopify product description in clean HTML. "
                        "Structure:\n"
                        "1. Brief intro paragraph about the part\n"
                        "2. Key features/specs as a <ul> list\n"
                        "3. Compatibility info (engine/truck)\n"
                        "4. Brief warranty/quality note\n\n"
                        "Use <h3>, <p>, <ul><li> tags. NO <html>/<body>/<head> wrapper. "
                        "Keep it concise (150-250 words). Professional tone. "
                        "Return ONLY the HTML, no markdown fences."
                    )},
                    {"role": "user", "content": info},
                ],
            )
            html = response.choices[0].message.content.strip()
            if html.startswith("```"):
                html = html.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            self.finished.emit(html)
        except Exception as exc:
            self.error.emit(str(exc))


class EditProductDialog(QDialog):
    """Dialog for editing / creating product information."""

    # Class-level storage for Save & New pre-fill
    _prefill_values: dict = {}
    # Track open modeless dialogs to prevent garbage collection
    _open_dialogs: list = []

    def __init__(
        self,
        *,
        sku: str | None = None,
        create_mode: bool = False,
        initial_section: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._create_mode = create_mode
        self._initial_section_key = str(initial_section or "").strip().upper()
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(1100, 800)
        self.showMaximized()

        self._sku = sku or ""
        self._product: dict | None = None
        self._product_id: int | None = None
        self._modified = False
        self._sku_manually_edited = False
        # Vendor-driven auto-pricing override flags. Set when the user hand-
        # edits Selling Price or Price Category so vendor/cost changes don't
        # silently clobber their value. Cleared by the "Reset to vendor
        # price" button or by Save.
        self._selling_price_user_override = False
        self._price_category_user_override = False
        self._auto_pricing_active = False  # reentrancy guard

        if create_mode:
            self.setWindowTitle("Add New Product")
            # Apply prefill values from previous Save & New
            if EditProductDialog._prefill_values:
                self._product = dict(EditProductDialog._prefill_values)
                EditProductDialog._prefill_values = {}
        else:
            self.setWindowTitle(f"Edit Product: {sku}")
            self._load_product()
        self._build_ui()
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Drag & Drop support for images
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile().lower()
                if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                self._import_image_from_path(path)
                break

    def _load_product(self) -> None:
        """Load product data from database."""
        try:
            self._product = get_product_by_sku(self._sku)
            if self._product:
                self._product_id = self._product.get("id")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load product: {e}")
            self._product = {}
    
    # ==================================================================
    # BUILD UI
    # ==================================================================

    # Style for collapsible section headers
    _SECTION_STYLE = """
        QGroupBox {
            font-weight: bold;
            font-size: 10pt;
            border: 2px solid #4a6741;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 18px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
        }
        QGroupBox::indicator {
            width: 0px; height: 0px;
        }
    """

    def _make_section(self, title: str, *, expanded: bool = True) -> QGroupBox:
        """Create a checkable (collapsible) QGroupBox section."""
        box = QGroupBox()
        box.setCheckable(True)
        box.setChecked(expanded)
        box.setStyleSheet(self._SECTION_STYLE)
        box.setProperty("_base_title", title)
        box.setProperty("_summary_fn", None)
        self._update_section_title(box, expanded)
        box.toggled.connect(lambda on, b=box: self._toggle_section(b, on))
        return box

    def _set_section_summary(self, box: QGroupBox, fn) -> None:
        """Attach a summary function to a section (called when collapsed)."""
        box.setProperty("_summary_fn", fn)
        if not box.isChecked():
            self._update_section_title(box, False)

    @staticmethod
    def _update_section_title(box: QGroupBox, expanded: bool) -> None:
        base = box.property("_base_title") or ""
        arrow = "▼" if expanded else "▶"
        summary = ""
        if not expanded:
            fn = box.property("_summary_fn")
            if callable(fn):
                try:
                    summary = fn()
                except Exception:
                    summary = ""
        if summary:
            box.setTitle(f"{arrow}  {base}    {summary}")
        else:
            box.setTitle(f"{arrow}  {base}")

    @staticmethod
    def _toggle_section(box: QGroupBox, on: bool) -> None:
        """Show / hide the contents of a collapsible section."""
        EditProductDialog._update_section_title(box, on)
        layout = box.layout()
        if layout is None:
            return

        def _set_visible_recursive(lo) -> None:
            for i in range(lo.count()):
                item = lo.itemAt(i)
                w = item.widget()
                if w:
                    w.setVisible(on)
                child_lo = item.layout()
                if child_lo:
                    _set_visible_recursive(child_lo)

        _set_visible_recursive(layout)

    def _build_ui(self) -> None:
        """Build the dialog UI with collapsible sections."""
        outer = QVBoxLayout(self)

        product = self._product or {}

        # ── Shared button styles for this dialog ──
        self._BTN_UTIL = (
            "QPushButton { padding: 2px 8px; border: 1px solid #aaa; "
            "border-radius: 3px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #ececec; }"
        )
        self._BTN_ACTION = (
            "QPushButton { background-color: #6B7A4A; color: white; "
            "font-weight: bold; padding: 3px 8px; border-radius: 3px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #5A6A3A; }"
        )

        # ── Header bar (olive green themed) ──
        header_bar = QFrame()
        header_bar.setStyleSheet(
            "QFrame#header_bar { background-color: #4a6741; border-radius: 6px; }"
            "QLabel { color: white; background: transparent; }"
        )
        header_bar.setObjectName("header_bar")
        header_bar.setMinimumHeight(52)
        header_bar.setMaximumHeight(56)
        header_lay = QHBoxLayout(header_bar)
        header_lay.setContentsMargins(12, 6, 12, 6)

        # Pinned button
        self._pinned_btn = QPushButton("Pinned")
        self._pinned_btn.setCheckable(True)
        self._pinned_btn.setStyleSheet(
            "QPushButton { background-color: #5A6A3A; color: white; "
            "font-weight: bold; padding: 4px 14px; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background-color: #6B7A4A; }"
            "QPushButton:checked { background-color: #3d5a2e; }"
        )
        self._pinned_btn.setToolTip("Pin this product for quick access")
        header_lay.addWidget(self._pinned_btn)
        header_lay.addSpacing(12)

        if self._create_mode:
            title_text = "Add New Product"
        else:
            title_text = f"Edit Product: {self._sku}"
        header_title = QLabel(title_text)
        header_title.setStyleSheet(
            "color: white; font-size: 14pt; font-weight: bold; background: transparent;"
        )
        header_lay.addWidget(header_title)
        header_lay.addStretch()

        # Core charge badge (only shown when product has a core)
        self._core_badge_label = QLabel("")
        self._core_badge_label.setStyleSheet(
            "QLabel { background-color: #B8860B; color: white; font-weight: bold; "
            "font-size: 9pt; padding: 3px 10px; border-radius: 10px; }"
        )
        self._core_badge_label.setToolTip(
            "This product carries a returnable core charge.\n"
            "See the CORES section in the right-side nav."
        )
        self._core_badge_label.hide()
        header_lay.addWidget(self._core_badge_label)
        header_lay.addSpacing(8)

        # AI Auto-Fill button — kept secondary; sits alongside Scrape/Generate tools
        self._ai_autofill_btn = QPushButton("🤖 Auto-Fill")
        self._ai_autofill_btn.setStyleSheet(
            "QPushButton { background-color: rgba(255,255,255,0.18); color: white; "
            "padding: 2px 8px; border-radius: 3px; font-size: 8pt; "
            "border: 1px solid rgba(255,255,255,0.35); }"
            "QPushButton:hover { background-color: rgba(255,255,255,0.30); }"
        )
        self._ai_autofill_btn.setToolTip(
            "Use AI to auto-fill description, category, engine, and platform from product name"
        )
        self._ai_autofill_btn.clicked.connect(self._on_ai_enrich)
        header_lay.addWidget(self._ai_autofill_btn)
        header_lay.addSpacing(8)

        # Search & Scrape controls in header
        search_lbl = QLabel("Search #:")
        search_lbl.setStyleSheet("color: #ccd; font-size: 9pt; background: transparent;")
        header_lay.addWidget(search_lbl)
        self._quick_scrape_edit = QLineEdit()
        self._quick_scrape_edit.setPlaceholderText("Part number…")
        self._quick_scrape_edit.setMinimumWidth(120)
        self._quick_scrape_edit.setMaximumWidth(180)
        self._quick_scrape_edit.setStyleSheet(
            "QLineEdit { background: #fff; color: #333; border: 1px solid #aaa; "
            "border-radius: 3px; padding: 3px 6px; font-size: 9pt; }"
        )
        header_lay.addWidget(self._quick_scrape_edit)
        header_lay.addSpacing(4)

        self._quick_scrape_all_btn = QPushButton("Scrape All")
        self._quick_scrape_all_btn.setStyleSheet(
            "QPushButton { background-color: #6B7A4A; color: white; "
            "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #5A6A3A; }"
        )
        self._quick_scrape_all_btn.setToolTip("Scrape competitor pricing + cross-references + PAI images")
        self._quick_scrape_all_btn.clicked.connect(
            lambda: self._run_edit_scrape("both")
        )
        header_lay.addWidget(self._quick_scrape_all_btn)

        # Header progress indicator (visible during scrape)
        self._header_scrape_progress = QProgressBar()
        self._header_scrape_progress.setFixedWidth(120)
        self._header_scrape_progress.setFixedHeight(18)
        self._header_scrape_progress.setTextVisible(False)
        self._header_scrape_progress.setStyleSheet(
            "QProgressBar { background-color: #3d5a2e; border: 1px solid #5A6A3A; "
            "border-radius: 3px; } "
            "QProgressBar::chunk { background-color: #8FBC5A; border-radius: 2px; }"
        )
        self._header_scrape_progress.hide()
        header_lay.addWidget(self._header_scrape_progress)

        self._header_scrape_label = QLabel("")
        self._header_scrape_label.setStyleSheet(
            "color: #d4e4c0; font-size: 8pt; background: transparent;"
        )
        self._header_scrape_label.hide()
        header_lay.addWidget(self._header_scrape_label)

        outer.addWidget(header_bar)

        # === THREE-COLUMN MASTER-DETAIL LAYOUT ===
        content_split = QHBoxLayout()
        content_split.setSpacing(8)

        # --- LEFT PANEL: Basic Info (always visible) + Pricing Summary ---
        left_panel = QWidget()
        left_panel.setMinimumWidth(360)
        left_panel.setMaximumWidth(460)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # --- CENTER PANEL: Stacked widget for sections ---
        self._section_stack = QStackedWidget()

        # ==============================================================
        #  🔵 BASIC — SKU, Title, Category, Condition
        # ==============================================================
        basic_box = self._make_section("🔵  BASIC", expanded=True)
        basic_box.setCheckable(False)
        basic_form = QFormLayout()
        # Stack labels above their fields so each input uses the full panel width.
        basic_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        basic_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        basic_form.setHorizontalSpacing(0)
        basic_form.setVerticalSpacing(10)
        basic_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # SKU
        sku_prefix = get_setting("sku_prefix") or ""
        if self._create_mode:
            sku_wrap_w = QWidget()
            sku_wrap = QVBoxLayout(sku_wrap_w)
            sku_wrap.setContentsMargins(0, 0, 0, 0)
            sku_wrap.setSpacing(2)
            self._sku_edit = QLineEdit(sku_prefix)
            self._sku_edit.setPlaceholderText(f"{sku_prefix}PART-NUMBER")
            self._sku_edit.textEdited.connect(self._on_sku_manual_edit)
            sku_wrap.addWidget(self._sku_edit)
            sku_btn_row = QHBoxLayout()
            sku_btn_row.setContentsMargins(0, 0, 0, 0)
            sku_btn_row.addStretch()
            auto_sku_btn = QPushButton("⚡ Auto-SKU")
            auto_sku_btn.setToolTip("Generate SKU from vendor prefix")
            auto_sku_btn.setStyleSheet(self._BTN_UTIL)
            auto_sku_btn.clicked.connect(self._on_auto_sku)
            sku_btn_row.addWidget(auto_sku_btn)
            sku_wrap.addLayout(sku_btn_row)
            basic_form.addRow("SKU:", sku_wrap_w)
        else:
            sku_row_w = QWidget()
            sku_row = QHBoxLayout(sku_row_w)
            sku_row.setContentsMargins(0, 0, 0, 0)
            self._sku_edit = QLineEdit(str(product.get("sku", self._sku)))
            self._sku_edit.setReadOnly(True)
            self._sku_edit.setStyleSheet("background-color: #e0e0e0; color: #666;")
            sku_row.addWidget(self._sku_edit, 1)
            self._sku_unlock_btn = QPushButton("✏️ Edit")
            self._sku_unlock_btn.setToolTip("Unlock SKU for editing")
            self._sku_unlock_btn.setStyleSheet(self._BTN_UTIL)
            self._sku_unlock_btn.setMaximumWidth(60)
            self._sku_unlock_btn.clicked.connect(self._on_unlock_sku)
            sku_row.addWidget(self._sku_unlock_btn)
            basic_form.addRow("SKU:", sku_row_w)

        # Description (merged Product Name + Brief Description)
        desc_wrap_w = QWidget()
        desc_wrap = QVBoxLayout(desc_wrap_w)
        desc_wrap.setContentsMargins(0, 0, 0, 0)
        desc_wrap.setSpacing(2)
        self._title_edit = QLineEdit(
            str(product.get("brief_description", "") or product.get("title", "") or "")
        )
        self._title_edit.setPlaceholderText("e.g. Water Pump, Fuel Injector, Turbocharger")
        self._title_edit.setMinimumWidth(180)
        self._title_edit.textChanged.connect(self._on_field_changed)
        self._title_edit.textChanged.connect(self._update_invoice_preview)
        desc_wrap.addWidget(self._title_edit)
        desc_btn_row = QHBoxLayout()
        desc_btn_row.setContentsMargins(0, 0, 0, 0)
        desc_btn_row.addStretch()
        self._ai_desc_btn = QPushButton("🤖 Generate")
        self._ai_desc_btn.setToolTip("Generate description using Grok AI")
        self._ai_desc_btn.setStyleSheet(self._BTN_UTIL)
        self._ai_desc_btn.clicked.connect(self._on_ai_generate_description)
        desc_btn_row.addWidget(self._ai_desc_btn)
        self._ai_auto_cb = QCheckBox("Auto")
        self._ai_auto_cb.setToolTip("Auto-generate description when OEM part # changes")
        desc_btn_row.addWidget(self._ai_auto_cb)
        desc_wrap.addLayout(desc_btn_row)
        basic_form.addRow("Description:", desc_wrap_w)

        # Condition
        self._condition_combo = QComboBox()
        self._condition_combo.addItems([
            "", "New", "Remanufactured", "Used", "Core",
        ])
        current_condition = str(product.get("condition", "") or "")
        if self._create_mode and not current_condition:
            current_condition = "New"
        idx = self._condition_combo.findText(current_condition, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._condition_combo.setCurrentIndex(idx)
        else:
            self._condition_combo.setCurrentText(current_condition)
        self._condition_combo.currentIndexChanged.connect(self._on_field_changed)
        basic_form.addRow("Condition:", self._condition_combo)

        # Product Description HTML widget (created here, displayed in bottom preview bar)
        self._desc_html_edit = QPlainTextEdit(str(product.get("description_html", "") or ""))
        self._desc_html_edit.setPlaceholderText(
            "HTML product description for Shopify listing...\n"
            "Use the Generate button to create from product details."
        )
        self._desc_html_edit.textChanged.connect(self._on_field_changed)
        self._desc_html_edit.textChanged.connect(self._update_desc_preview)
        self._ai_shopify_desc_btn = QPushButton("🤖 Generate Description")
        self._ai_shopify_desc_btn.setToolTip("Generate full Shopify product description using AI")
        self._ai_shopify_desc_btn.setStyleSheet(self._BTN_UTIL)
        self._ai_shopify_desc_btn.clicked.connect(self._on_ai_generate_shopify_desc)

        # OEM Part #
        self._oem_pn_edit = QLineEdit(str(product.get("oem_part_number", "") or ""))
        self._oem_pn_edit.setPlaceholderText("OEM part number")
        self._oem_pn_edit.setMinimumWidth(180)
        self._oem_pn_edit.textChanged.connect(self._on_field_changed)
        self._oem_pn_edit.textChanged.connect(self._update_invoice_preview)
        oem_wrap_w = QWidget()
        oem_wrap = QVBoxLayout(oem_wrap_w)
        oem_wrap.setContentsMargins(0, 0, 0, 0)
        oem_wrap.setSpacing(2)
        oem_wrap.addWidget(self._oem_pn_edit)
        oem_btn_row = QHBoxLayout()
        oem_btn_row.setContentsMargins(0, 0, 0, 0)
        oem_btn_row.addStretch()
        self._oem_lookup_btn = QPushButton("🔍 Lookup")
        self._oem_lookup_btn.setToolTip("Use AI to identify this part number")
        self._oem_lookup_btn.setStyleSheet(self._BTN_UTIL)
        self._oem_lookup_btn.clicked.connect(self._on_oem_lookup)
        oem_btn_row.addWidget(self._oem_lookup_btn)
        oem_wrap.addLayout(oem_btn_row)
        basic_form.addRow("OEM Part #:", oem_wrap_w)

        # Serial Tracking widget is created here so save logic always sees it,
        # but it is displayed inside the INVENTORY section (see _build_ui below).
        self._requires_serial_cb = QCheckBox("Require Serial Number when invoicing")
        self._requires_serial_cb.setToolTip(
            "Serial required when invoicing (not receiving)"
        )
        self._requires_serial_cb.setChecked(bool(product.get("requires_serial", 0)))
        self._requires_serial_cb.toggled.connect(self._on_field_changed)

        self._requires_serial_receive_cb = QCheckBox("Require Serial Number when receiving")
        self._requires_serial_receive_cb.setToolTip(
            "Serial captured during PO receipt"
        )
        self._requires_serial_receive_cb.setChecked(bool(product.get("requires_serial_receive", 0)))
        self._requires_serial_receive_cb.toggled.connect(self._on_field_changed)

        self._requires_serial_warranty_cb = QCheckBox("Require Serial Number on warranty claim")
        self._requires_serial_warranty_cb.setToolTip(
            "Serial required when filing a warranty claim"
        )
        self._requires_serial_warranty_cb.setChecked(bool(product.get("requires_serial_warranty", 0)))
        self._requires_serial_warranty_cb.toggled.connect(self._on_field_changed)

        # Read-only rendered description preview at bottom of BASIC
        desc_view_lbl = QLabel("Product Description:")
        desc_view_lbl.setStyleSheet(
            "font-weight: bold; font-size: 8pt; color: #555; background: transparent; margin-top: 4px;"
        )
        basic_form.addRow(desc_view_lbl)
        self._basic_desc_preview = QTextBrowser()
        self._basic_desc_preview.setOpenExternalLinks(False)
        self._basic_desc_preview.setStyleSheet(
            "QTextBrowser { background-color: #fffff0; border: 1px solid #ddd; "
            "border-radius: 3px; padding: 4px; font-size: 9pt; }"
        )
        self._basic_desc_preview.setMinimumHeight(50)
        self._basic_desc_preview.setMaximumHeight(100)
        basic_form.addRow(self._basic_desc_preview)

        basic_box.setLayout(basic_form)
        # Basic Info goes to the LEFT panel (always visible)
        left_layout.addWidget(basic_box)

        # ==============================================================
        #  🔄 CORE CHARGE — prominent left-panel card
        #     Collapsed by default ([ ] checkbox only). Expands when the
        #     user enables a core. Drives quotes/SO/invoice/PO/QBO downstream.
        # ==============================================================
        core_box = QFrame()
        core_box.setObjectName("core_box")
        core_box.setStyleSheet(
            "QFrame#core_box { background-color: #FFFBEA; "
            "border: 2px solid #B8860B; border-radius: 6px; }"
            "QLabel { background: transparent; }"
        )
        core_outer = QVBoxLayout(core_box)
        core_outer.setContentsMargins(10, 6, 10, 8)
        core_outer.setSpacing(4)

        core_title = QLabel("🔄  CORE CHARGE")
        core_title.setStyleSheet(
            "font-weight: bold; font-size: 10pt; color: #8B6914; "
            "border-bottom: 1px solid #B8860B; padding-bottom: 3px;"
        )
        core_outer.addWidget(core_title)

        _initial_core_cost = float(product.get("core_charge", 0) or 0)
        _initial_core_sell = float(product.get("core_sell_price", 0) or 0)
        _has_core_initially = bool(product.get("has_core", 0)) or (
            _initial_core_cost > 0 or _initial_core_sell > 0
        )

        self._has_core_cb = QCheckBox("This product has a refundable core")
        self._has_core_cb.setToolTip(
            "Enable when this part requires a core deposit/return.\n"
            "Vendor deposit = what you owe back to the vendor.\n"
            "Customer charge = deposit added to customer invoices."
        )
        self._has_core_cb.setChecked(_has_core_initially)
        self._has_core_cb.setStyleSheet("font-weight: bold; color: #5a4a10;")
        self._has_core_cb.toggled.connect(self._on_field_changed)
        self._has_core_cb.toggled.connect(self._on_has_core_toggled)
        core_outer.addWidget(self._has_core_cb)

        # All detail fields live inside this container, hidden when unchecked.
        self._core_details_widget = QWidget()
        core_form = QFormLayout(self._core_details_widget)
        core_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        core_form.setContentsMargins(4, 4, 0, 0)
        core_form.setVerticalSpacing(5)
        core_form.setHorizontalSpacing(8)

        # ── Customer side ──
        _cust_hdr = QLabel("Customer Side")
        _cust_hdr.setStyleSheet("font-weight: bold; font-size: 9pt; color: #4a6741;")
        core_form.addRow(_cust_hdr)

        self._core_sell_spin = QDoubleSpinBox()
        self._core_sell_spin.setRange(0, 999999.99)
        self._core_sell_spin.setDecimals(2)
        self._core_sell_spin.setPrefix("$")
        self._core_sell_spin.setValue(_initial_core_sell)
        self._core_sell_spin.setToolTip(
            "Customer core charge — deposit added to the customer's invoice,\n"
            "refunded when their old core unit is returned."
        )
        self._core_sell_spin.valueChanged.connect(self._on_field_changed)
        self._core_sell_spin.valueChanged.connect(self._update_core_margin)
        self._core_sell_spin.valueChanged.connect(self._refresh_core_badge)
        self._core_sell_spin.valueChanged.connect(self._update_invoice_preview)

        core_sell_row = QHBoxLayout()
        core_sell_row.addWidget(self._core_sell_spin)
        self._core_margin_label = QLabel("")
        self._core_margin_label.setStyleSheet(
            "color: #666; font-size: 9pt; background: transparent;"
        )
        core_sell_row.addWidget(self._core_margin_label)
        core_sell_row.addStretch()
        core_form.addRow("Customer Core Charge:", core_sell_row)

        self._core_return_window_spin = QSpinBox()
        self._core_return_window_spin.setRange(0, 3650)
        self._core_return_window_spin.setSuffix(" days")
        _rw = int(product.get("core_return_days", 0) or product.get("core_return_window_days", 0) or 30)
        if _rw <= 0:
            _rw = 30
        self._core_return_window_spin.setValue(_rw)
        self._core_return_window_spin.setToolTip(
            "How long after sale a customer can return the old core for a refund.\n"
            "Defaults to 30 days."
        )
        self._core_return_window_spin.valueChanged.connect(self._on_field_changed)
        core_form.addRow("Core Return Window:", self._core_return_window_spin)

        self._track_customer_core_return_cb = QCheckBox("Track customer core return")
        self._track_customer_core_return_cb.setChecked(
            bool(product.get("track_customer_core_return", 1))
        )
        # Deprecated in the May 2026 Core Handling Revitalization — when
        # ``has_core`` is set the system always tracks both sides. Widget kept
        # alive (hidden) so the existing save handler can still write to the
        # underlying ``track_customer_core_return`` column without crashing.
        self._track_customer_core_return_cb.setVisible(False)

        # ── Vendor side ──
        _vend_hdr = QLabel("Vendor Side")
        _vend_hdr.setStyleSheet(
            "font-weight: bold; font-size: 9pt; color: #4a6741; padding-top: 4px;"
        )
        core_form.addRow(_vend_hdr)

        self._core_charge_spin = QDoubleSpinBox()
        self._core_charge_spin.setRange(0, 999999.99)
        self._core_charge_spin.setDecimals(2)
        self._core_charge_spin.setPrefix("$")
        self._core_charge_spin.setValue(_initial_core_cost)
        self._core_charge_spin.setToolTip(
            "Vendor deposit — the amount the vendor charges for the new unit's core.\n"
            "You recover this when the old core is shipped back to the vendor.\n"
            "Can be 0 if unknown."
        )
        self._core_charge_spin.valueChanged.connect(self._on_field_changed)
        self._core_charge_spin.valueChanged.connect(self._update_core_margin)
        self._core_charge_spin.valueChanged.connect(self._refresh_core_badge)
        core_form.addRow("Vendor Core Deposit:", self._core_charge_spin)

        self._track_vendor_core_credit_cb = QCheckBox("Track vendor core credit")
        self._track_vendor_core_credit_cb.setChecked(
            bool(product.get("track_vendor_core_credit", 1))
        )
        # Deprecated (see _track_customer_core_return_cb above). Hidden, kept
        # alive for the save handler.
        self._track_vendor_core_credit_cb.setVisible(False)

        # ── Options ──
        # Entire Options block deprecated in the May 2026 plan. Widgets stay
        # in memory so save() doesn't crash, but none are added to the form.
        self._core_type_combo = QComboBox()
        for value, label in (
            ("none", "No core handling"),
            ("refundable_deposit", "Refundable deposit"),
            ("exchange_required", "Exchange required"),
            ("included_in_price", "Core baked into price"),
        ):
            self._core_type_combo.addItem(label, value)
        _current_core_type = str(product.get("core_type") or "none").lower()
        if _current_core_type == "none" and (
            float(product.get("core_charge", 0) or 0) > 0
            or int(product.get("has_core", 0) or 0) == 1
        ):
            _current_core_type = "refundable_deposit"
        _ct_idx = self._core_type_combo.findData(_current_core_type)
        if _ct_idx >= 0:
            self._core_type_combo.setCurrentIndex(_ct_idx)
        self._core_type_combo.setVisible(False)

        self._core_include_in_price_cb = QCheckBox("Include core in advertised price")
        self._core_include_in_price_cb.setChecked(
            bool(product.get("include_core_in_advertised_price", 0))
        )
        self._core_include_in_price_cb.setVisible(False)

        # Core condition notes stay visible — useful for "Complete rebuildable
        # core required" / "No cracks" warnings on POs and invoices.
        self._core_notes_edit = QPlainTextEdit(str(product.get("core_notes", "") or ""))
        self._core_notes_edit.setPlaceholderText(
            'Example: "Complete, rebuildable core required. No cracks or broken castings."'
        )
        self._core_notes_edit.setMaximumHeight(54)
        self._core_notes_edit.textChanged.connect(self._on_field_changed)
        core_form.addRow("Core Condition Notes:", self._core_notes_edit)

        core_outer.addWidget(self._core_details_widget)

        # Apply initial collapsed/expanded state
        self._core_details_widget.setVisible(_has_core_initially)

        left_layout.addWidget(core_box)

        # ==============================================================
        #  �🟢 PLATFORM — Engine OR Truck fields
        # ==============================================================
        platform_box = self._make_section("🟢  PLATFORM — Engine / Truck", expanded=False)
        plat_form = QFormLayout()
        plat_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Manufacturer combo (hidden — kept for data compat but not shown)
        self._manufacturer_combo = QComboBox()
        self._manufacturer_combo.setEditable(True)
        self._manufacturer_combo.setMinimumWidth(250)
        self._manufacturer_combo.addItem("")
        try:
            db_mfrs = get_all_manufacturers()
        except Exception:
            db_mfrs = []
        for m in sorted(set(db_mfrs)):
            self._manufacturer_combo.addItem(m)
        current_mfr = str(product.get("manufacturer", "") or "")
        idx = self._manufacturer_combo.findText(current_mfr, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._manufacturer_combo.setCurrentIndex(idx)
        else:
            self._manufacturer_combo.setCurrentText(current_mfr)
        self._manufacturer_combo.currentTextChanged.connect(self._on_field_changed)
        self._manufacturer_combo.currentTextChanged.connect(self._update_invoice_preview)
        self._manufacturer_combo.currentTextChanged.connect(self._on_mfr_or_supplier_changed)
        # Not added to layout — hidden

        # Engine/Truck radios (hidden — kept for _on_platform_toggle compat)
        self._engine_radio = QRadioButton("Engine")
        self._truck_radio = QRadioButton("Truck")
        self._engine_radio.setChecked(True)

        # ── ENGINE APPLICATION header (primary) ──
        _engine_hdr = QLabel("ENGINE APPLICATION")
        _engine_hdr.setStyleSheet(
            "color: #2D2D2D; font-size: 10pt; font-weight: bold; "
            "padding: 6px 0 2px 0; border-bottom: 2px solid #6B7A4A; background: transparent;"
        )
        plat_form.addRow(_engine_hdr)

        # ── Engine fields (always visible) ──
        self._engine_mfr_combo = QComboBox()
        self._engine_mfr_combo.setEditable(True)
        self._engine_mfr_combo.setMinimumWidth(250)
        self._engine_mfr_combo.addItem("")
        try:
            db_eng_mfrs = get_all_engine_manufacturers()
        except Exception:
            db_eng_mfrs = []
        for em in sorted(set(_DEFAULT_ENGINE_MFRS + db_eng_mfrs)):
            self._engine_mfr_combo.addItem(em)
        current_eng_mfr = str(product.get("oem_manufacturer", "") or "")
        idx = self._engine_mfr_combo.findText(current_eng_mfr, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._engine_mfr_combo.setCurrentIndex(idx)
        else:
            self._engine_mfr_combo.setCurrentText(current_eng_mfr)
        self._engine_mfr_combo.currentTextChanged.connect(self._on_field_changed)
        self._engine_mfr_combo.currentTextChanged.connect(self._update_invoice_preview)
        self._engine_mfr_label = QLabel("Engine Manufacturer:")
        plat_form.addRow("Engine Manufacturer:", self._engine_mfr_combo)

        self._engine_model_combo = QComboBox()
        self._engine_model_combo.setEditable(True)
        self._engine_model_combo.setMinimumWidth(250)
        self._engine_model_combo.addItem("")
        try:
            db_eng_models = get_all_engine_models()
        except Exception:
            db_eng_models = []
        self._all_engine_models = sorted(set(_DEFAULT_ENGINE_MODELS + db_eng_models))
        for em in self._all_engine_models:
            self._engine_model_combo.addItem(em)
        current_eng_model = str(product.get("engine", "") or "")
        idx = self._engine_model_combo.findText(current_eng_model, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._engine_model_combo.setCurrentIndex(idx)
        else:
            self._engine_model_combo.setCurrentText(current_eng_model)
        self._engine_model_combo.currentTextChanged.connect(self._on_field_changed)
        self._engine_model_combo.currentTextChanged.connect(self._update_invoice_preview)
        self._engine_model_label = QLabel("Engine Model:")
        plat_form.addRow("Engine Model:", self._engine_model_combo)

        self._engine_platform_hint_label = QLabel("")
        self._engine_platform_hint_label.setStyleSheet(
            "color: #8a4b00; font-size: 9pt; background: transparent;"
        )
        self._engine_platform_hint_label.setWordWrap(True)
        plat_form.addRow("", self._engine_platform_hint_label)

        # Separator between engine and truck
        plat_sep = QFrame()
        plat_sep.setFrameShape(QFrame.Shape.HLine)
        plat_sep.setStyleSheet("color: #ccc; margin-top: 4px;")
        plat_form.addRow(plat_sep)

        # ── TRUCK FITMENT header (secondary, muted) ──
        _truck_hdr = QLabel("TRUCK FITMENT")
        _truck_hdr.setStyleSheet(
            "color: #555; font-size: 9pt; font-weight: bold; "
            "padding: 4px 0 2px 0; border-bottom: 1px dashed #aaa; background: transparent;"
        )
        plat_form.addRow(_truck_hdr)

        # ── Truck fields (always visible) ──
        self._truck_mfr_combo = QComboBox()
        self._truck_mfr_combo.setEditable(True)
        self._truck_mfr_combo.setMinimumWidth(250)
        self._truck_mfr_combo.addItem("")
        for tm in _DEFAULT_TRUCK_MFRS:
            self._truck_mfr_combo.addItem(tm)
        current_truck_mfr = str(product.get("truck_manufacturer", "") or "")
        if current_truck_mfr:
            idx = self._truck_mfr_combo.findText(current_truck_mfr, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._truck_mfr_combo.setCurrentIndex(idx)
            else:
                self._truck_mfr_combo.setCurrentText(current_truck_mfr)
        self._truck_mfr_combo.currentTextChanged.connect(self._on_field_changed)
        self._truck_mfr_combo.currentTextChanged.connect(self._update_invoice_preview)
        self._truck_mfr_label = QLabel("Truck Manufacturer:")
        plat_form.addRow("Truck Manufacturer:", self._truck_mfr_combo)

        self._truck_model_edit = QComboBox()
        self._truck_model_edit.setEditable(True)
        self._truck_model_edit.setMinimumWidth(250)
        self._truck_model_edit.addItem("")
        self._all_truck_models = sorted(
            {
                model
                for models in _TRUCK_MODELS_BY_MFR.values()
                for model in models
            }
        )
        for tm in self._all_truck_models:
            self._truck_model_edit.addItem(tm)
        current_truck_model = str(product.get("truck_model", "") or "")
        idx = self._truck_model_edit.findText(current_truck_model, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._truck_model_edit.setCurrentIndex(idx)
        else:
            self._truck_model_edit.setCurrentText(current_truck_model)
        self._truck_model_edit.currentTextChanged.connect(self._on_field_changed)
        self._truck_model_edit.currentTextChanged.connect(self._update_invoice_preview)
        self._truck_model_label = QLabel("Truck Model:")
        plat_form.addRow("Truck Model:", self._truck_model_edit)

        self._truck_platform_hint_label = QLabel("")
        self._truck_platform_hint_label.setStyleSheet(
            "color: #8a4b00; font-size: 9pt; background: transparent;"
        )
        self._truck_platform_hint_label.setWordWrap(True)
        plat_form.addRow("", self._truck_platform_hint_label)

        self._truck_system_combo = QComboBox()
        self._truck_system_combo.setEditable(True)
        self._truck_system_combo.setMinimumWidth(250)
        self._truck_system_combo.addItem("")
        for ts in _DEFAULT_TRUCK_SYSTEMS:
            self._truck_system_combo.addItem(ts)
        current_truck_sys = str(product.get("truck_system", "") or "")
        if current_truck_sys:
            idx = self._truck_system_combo.findText(current_truck_sys, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._truck_system_combo.setCurrentIndex(idx)
            else:
                self._truck_system_combo.setCurrentText(current_truck_sys)
        self._truck_system_combo.currentTextChanged.connect(self._on_field_changed)
        self._truck_system_combo.currentTextChanged.connect(self._update_invoice_preview)
        self._truck_system_label = QLabel("Truck System:")
        plat_form.addRow("Truck System:", self._truck_system_combo)

        self._engine_mfr_combo.currentTextChanged.connect(self._on_engine_mfr_changed_smart)
        self._engine_model_combo.currentTextChanged.connect(self._on_engine_model_changed_smart)
        self._truck_mfr_combo.currentTextChanged.connect(self._on_truck_mfr_changed_smart)
        self._truck_model_edit.currentTextChanged.connect(self._on_truck_model_changed_smart)

        platform_box.setLayout(plat_form)

        # Invoice Preview Box (always visible — not in a collapsible section)
        preview_box = QGroupBox("Invoice Line Preview")
        preview_box.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #4a6741; "
            "border-radius: 4px; margin-top: 8px; padding-top: 16px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        preview_layout = QVBoxLayout(preview_box)
        self._invoice_preview = QLabel("")
        self._invoice_preview.setWordWrap(True)
        self._invoice_preview.setStyleSheet(
            "font-size: 11pt; padding: 8px; background-color: #fffff0; "
            "border: 1px solid #ddd; border-radius: 3px;"
        )
        self._invoice_preview.setMinimumHeight(40)
        preview_layout.addWidget(self._invoice_preview)

        # ==============================================================
        #  🟡 SUPPLIER / VENDOR
        # ==============================================================
        vendor_box = self._make_section("🟡  SUPPLIER / VENDOR", expanded=True)
        vendor_form = QFormLayout()
        vendor_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Supplier dropdown + Add button
        supplier_row = QHBoxLayout()
        self._supplier_combo = QComboBox()
        self._supplier_combo.setMinimumWidth(250)
        self._supplier_combo.addItem("-- Select Vendor --", None)
        self._vendors = []
        try:
            self._vendors = get_all_vendors()
            for v in self._vendors:
                self._supplier_combo.addItem(v.get("name", ""), v.get("id"))
        except Exception:
            pass
        current_supplier = str(product.get("supplier", "") or "")
        if current_supplier:
            idx = self._supplier_combo.findText(current_supplier, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._supplier_combo.setCurrentIndex(idx)
        self._supplier_combo.currentIndexChanged.connect(self._on_field_changed)
        self._supplier_combo.currentIndexChanged.connect(
            lambda: self._on_mfr_or_supplier_changed()
        )
        self._supplier_combo.currentTextChanged.connect(self._update_pai_button_visibility)
        self._supplier_combo.currentIndexChanged.connect(self._update_invoice_preview)
        self._supplier_combo.currentTextChanged.connect(self._on_supplier_changed_warranty)
        self._supplier_combo.currentIndexChanged.connect(self._on_vendor_changed_pricing)
        supplier_row.addWidget(self._supplier_combo, 1)

        add_vendor_btn = QPushButton("+ Vendor")
        add_vendor_btn.setToolTip("Create new supplier")
        add_vendor_btn.setStyleSheet(self._BTN_UTIL)
        add_vendor_btn.setMaximumWidth(80)
        add_vendor_btn.clicked.connect(self._on_add_vendor)
        supplier_row.addWidget(add_vendor_btn)
        vendor_form.addRow("Supplier (required):", supplier_row)

        # Private Label + custom part number override
        private_label_part_default = str(product.get("private_label_part_number", "") or "").strip()
        if not private_label_part_default and bool(product.get("private_label", 0)):
            sku_text = str(product.get("sku", "") or "").strip()
            if sku_text.upper().startswith("JAKS-"):
                private_label_part_default = sku_text[5:]

        private_label_row = QHBoxLayout()
        self._private_label_cb = QCheckBox("Private Label")
        self._private_label_cb.setToolTip(
            "SKU uses JAKS-XXXXXX format, no vendor SKU shown, "
            "invoice shows (JAKS) instead of supplier"
        )
        self._private_label_cb.setChecked(bool(product.get("private_label", 0)))
        self._private_label_cb.toggled.connect(self._on_field_changed)
        self._private_label_cb.toggled.connect(self._on_private_label_toggled)
        private_label_row.addWidget(self._private_label_cb)

        self._private_label_part_label = QLabel("Part #:")
        self._private_label_part_edit = QLineEdit(private_label_part_default)
        self._private_label_part_edit.setPlaceholderText("Private label part number")
        self._private_label_part_edit.textChanged.connect(self._on_field_changed)
        self._private_label_part_edit.textChanged.connect(self._auto_update_sku)
        self._private_label_part_edit.setMinimumWidth(160)
        private_label_row.addSpacing(8)
        private_label_row.addWidget(self._private_label_part_label)
        private_label_row.addWidget(self._private_label_part_edit, 1)
        vendor_form.addRow("", private_label_row)

        # Vendor SKU + PAI Lookup
        vendor_sku_row = QHBoxLayout()
        self._pai_sku_edit = QLineEdit(str(product.get("pai_sku", "") or ""))
        self._pai_sku_edit.textChanged.connect(self._on_field_changed)
        self._pai_sku_edit.textChanged.connect(self._auto_update_sku)
        vendor_sku_row.addWidget(self._pai_sku_edit, 1)
        self._pai_lookup_btn = QPushButton("🔍 PAI Lookup")
        self._pai_lookup_btn.setToolTip("Look up this part on PAI and auto-fill fields")
        self._pai_lookup_btn.setStyleSheet(self._BTN_UTIL)
        self._pai_lookup_btn.setMaximumWidth(100)
        self._pai_lookup_btn.clicked.connect(self._on_pai_lookup)
        vendor_sku_row.addWidget(self._pai_lookup_btn)
        self._vendor_sku_label = QLabel("Vendor SKU:")
        vendor_form.addRow(self._vendor_sku_label, vendor_sku_row)
        self._pai_lookup_worker: _PAILookupWorker | None = None

        # Show PAI Lookup only when supplier is PAI
        self._update_pai_button_visibility(current_supplier)

        # Vendor Description (new field)
        self._vendor_desc_edit = QLineEdit(str(product.get("vendor_description", "") or ""))
        self._vendor_desc_edit.setPlaceholderText("Vendor's description of this part")
        self._vendor_desc_edit.textChanged.connect(self._on_field_changed)
        vendor_form.addRow("Vendor Description:", self._vendor_desc_edit)

        # Vendor Link
        self._pai_link_edit = QLineEdit(str(product.get("pai_link", "") or ""))
        self._pai_link_edit.textChanged.connect(self._on_field_changed)
        vendor_form.addRow("Vendor Link:", self._pai_link_edit)

        # ── Vendor Availability (per-warehouse stock) ──
        avail_sep = QFrame()
        avail_sep.setFrameShape(QFrame.Shape.HLine)
        avail_sep.setStyleSheet("color: #ccc;")
        vendor_form.addRow(avail_sep)

        avail_hdr = QLabel("Availability")
        avail_hdr.setStyleSheet(
            "font-weight:bold; font-size:10pt; color:#4a6741; margin-top:2px;"
        )
        vendor_form.addRow(avail_hdr)

        self._vendor_avail_edit = QLineEdit(
            str(product.get("vendor_availability", "") or "")
        )
        self._vendor_avail_edit.setPlaceholderText(
            "e.g. ATL: 5, DFW: 3, LAX: Out — auto-filled by PAI Lookup"
        )
        self._vendor_avail_edit.setReadOnly(False)
        self._vendor_avail_edit.textChanged.connect(self._on_field_changed)
        vendor_form.addRow("Stock by Location:", self._vendor_avail_edit)

        # Overall availability status
        self._vendor_avail_status = QLabel("")
        self._vendor_avail_status.setStyleSheet("font-size: 9pt;")
        vendor_form.addRow("Status:", self._vendor_avail_status)
        self._update_vendor_avail_status(product)

        # ── Vendor Alternate Part Numbers ──
        alt_sep = QFrame()
        alt_sep.setFrameShape(QFrame.Shape.HLine)
        alt_sep.setStyleSheet("color: #ccc;")
        vendor_form.addRow(alt_sep)

        self._vendor_alternates_edit = QLineEdit(
            str(product.get("vendor_alternates", "") or "")
        )
        self._vendor_alternates_edit.setPlaceholderText(
            "Alternate part numbers offered by vendor (comma-separated)"
        )
        self._vendor_alternates_edit.textChanged.connect(self._on_field_changed)

        alt_row = QHBoxLayout()
        alt_row.addWidget(self._vendor_alternates_edit, 1)
        self._add_alt_product_btn = QPushButton("➕ Add as Product")
        self._add_alt_product_btn.setToolTip(
            "Open a new product entry pre-filled with the selected alternate's PAI data"
        )
        self._add_alt_product_btn.setStyleSheet(self._BTN_UTIL)
        self._add_alt_product_btn.setMaximumWidth(130)
        self._add_alt_product_btn.setVisible(False)  # shown after PAI lookup
        self._add_alt_product_btn.clicked.connect(self._on_add_alternate_product)
        alt_row.addWidget(self._add_alt_product_btn)
        vendor_form.addRow("Vendor Alternates:", alt_row)

        # Storage for PAI results (populated by PAI lookup)
        self._pai_all_results: list[dict] = []
        self._pai_selected_sku: str = ""

        # Apply initial private label visibility
        if self._private_label_cb.isChecked():
            self._vendor_sku_label.setVisible(False)
            self._pai_sku_edit.setVisible(False)
            self._pai_lookup_btn.setVisible(False)
        else:
            self._private_label_part_label.setVisible(False)
            self._private_label_part_edit.setVisible(False)

        vendor_box.setLayout(vendor_form)

        # ==============================================================
        #  🛡  WARRANTY — Supplier + JAKS Additional (own section tab)
        # ==============================================================
        warranty_box = self._make_section("🛡  WARRANTY", expanded=False)
        warranty_form = QFormLayout()
        warranty_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Supplier Warranty ──────────────────────────────────────
        sw_hdr = QLabel("Supplier Warranty")
        sw_hdr.setStyleSheet("font-weight:bold; font-size:10pt; color:#4a6741; margin-top:2px;")
        warranty_form.addRow(sw_hdr)

        self._supplier_warranty_cb = QCheckBox("Supplier provides warranty")
        self._supplier_warranty_cb.setChecked(bool(product.get("supplier_warranty_enabled", 0)))
        self._supplier_warranty_cb.toggled.connect(self._on_field_changed)
        self._supplier_warranty_cb.toggled.connect(self._on_supplier_warranty_toggled)
        warranty_form.addRow("", self._supplier_warranty_cb)

        # Quick-select buttons
        self._sw_quick_frame = QFrame()
        sw_quick_lay = QHBoxLayout(self._sw_quick_frame)
        sw_quick_lay.setContentsMargins(0, 0, 0, 0)
        sw_quick_lay.setSpacing(3)
        sw_quick_lay.addWidget(QLabel("Quick:"))
        for label, val, unit in [
            ("30d", 30, "days"), ("60d", 60, "days"), ("90d", 90, "days"),
            ("1yr", 1, "years"), ("2yr", 2, "years"), ("3yr", 3, "years"),
        ]:
            btn = QPushButton(label)
            btn.setFixedWidth(38)
            btn.setStyleSheet(
                "QPushButton { padding: 2px 4px; border: 1px solid #aaa; "
                "border-radius: 3px; font-size: 8pt; }"
                "QPushButton:hover { background: #e3f2fd; }"
            )
            btn.clicked.connect(
                lambda checked, v=val, u=unit: self._set_supplier_warranty(v, u)
            )
            sw_quick_lay.addWidget(btn)
        sw_quick_lay.addStretch()
        warranty_form.addRow("", self._sw_quick_frame)

        # Custom entry: value + unit dropdown
        sw_custom_row = QHBoxLayout()
        self._sw_value_spin = QSpinBox()
        self._sw_value_spin.setRange(0, 9999)
        self._sw_value_spin.setValue(int(product.get("supplier_warranty_value", 0) or 0))
        self._sw_value_spin.setMaximumWidth(70)
        self._sw_value_spin.valueChanged.connect(self._on_field_changed)
        sw_custom_row.addWidget(self._sw_value_spin)

        self._sw_unit_combo = QComboBox()
        self._sw_unit_combo.addItems(["days", "months", "years"])
        sw_unit = str(product.get("supplier_warranty_unit", "days") or "days")
        idx = self._sw_unit_combo.findText(sw_unit)
        if idx >= 0:
            self._sw_unit_combo.setCurrentIndex(idx)
        self._sw_unit_combo.setMaximumWidth(90)
        self._sw_unit_combo.currentIndexChanged.connect(self._on_field_changed)
        sw_custom_row.addWidget(self._sw_unit_combo)

        self._sw_display_label = QLabel("")
        self._sw_display_label.setStyleSheet("color: #4a6741; font-weight: bold; font-size: 9pt;")
        sw_custom_row.addWidget(self._sw_display_label)
        sw_custom_row.addStretch()
        warranty_form.addRow("Length:", sw_custom_row)

        # Initial visibility
        sw_on = self._supplier_warranty_cb.isChecked()
        self._sw_quick_frame.setVisible(sw_on)
        self._sw_value_spin.setVisible(sw_on)
        self._sw_unit_combo.setVisible(sw_on)
        self._sw_display_label.setVisible(sw_on)
        self._update_sw_display()

        # ── JAKS Extended Warranty ────────────────────────────────
        # Single knob: % of sell price charged per year. Year selection
        # (1/2/3) happens later on the quote/SO line, not on the product.
        jw_sep = QFrame()
        jw_sep.setFrameShape(QFrame.Shape.HLine)
        jw_sep.setStyleSheet("color: #ccc; margin-top: 8px;")
        warranty_form.addRow(jw_sep)

        self._jaks_warranty_cb = QCheckBox("Offer extended warranty")
        self._jaks_warranty_cb.setChecked(bool(product.get("jaks_warranty_enabled", 0)))
        self._jaks_warranty_cb.toggled.connect(self._on_field_changed)
        self._jaks_warranty_cb.toggled.connect(self._on_jaks_warranty_toggled)
        warranty_form.addRow("", self._jaks_warranty_cb)

        # Hidden legacy widgets — kept so summary/preview/_get_changes still
        # work and DB columns jaks_warranty_value / jaks_warranty_unit /
        # jaks_warranty_charge stay populated. Length is hardcoded to 1 year
        # at the product level (quote line items pick the actual term).
        self._jw_value_spin = QSpinBox()
        self._jw_value_spin.setRange(0, 9999)
        self._jw_value_spin.setValue(1)
        self._jw_value_spin.hide()

        self._jw_unit_combo = QComboBox()
        self._jw_unit_combo.addItems(["days", "months", "years"])
        self._jw_unit_combo.setCurrentIndex(self._jw_unit_combo.findText("years"))
        self._jw_unit_combo.hide()

        self._jw_display_label = QLabel("")
        self._jw_display_label.hide()

        # Quick buttons removed — keep empty list so legacy
        # _update_jw_quick_highlight() is a no-op.
        self._jw_quick_buttons: list[tuple[QPushButton, int, str, float]] = []
        self._jw_quick_frame = QFrame()
        self._jw_quick_frame.hide()
        self._jw_btn_style = ""
        self._jw_btn_selected = ""

        # Warranty charge — internal/derived ($ amount = % × sell price).
        # Hidden helper so legacy code paths see a value.
        self._jw_charge_spin = QDoubleSpinBox()
        self._jw_charge_spin.setRange(0, 999999.99)
        self._jw_charge_spin.setDecimals(2)
        self._jw_charge_spin.setPrefix("$")
        self._jw_charge_spin.setValue(float(product.get("jaks_warranty_charge", 0) or 0))
        self._jw_charge_spin.valueChanged.connect(self._on_field_changed)
        self._jw_charge_spin.hide()

        # The single user-facing knob: rate per year.
        jw_pct_row = QHBoxLayout()
        self._jw_pct_spin = QDoubleSpinBox()
        self._jw_pct_spin.setRange(0, 100)
        self._jw_pct_spin.setDecimals(1)
        self._jw_pct_spin.setSuffix("%")
        # Default to 10% on a brand-new product (no value stored yet).
        _default_pct = float(product.get("warranty_percentage") or 10.0)
        self._jw_pct_spin.setValue(_default_pct)
        self._jw_pct_spin.setMaximumWidth(100)
        self._jw_pct_spin.setToolTip(
            "% of sell price charged per year for extended warranty on quotes"
        )
        self._jw_pct_spin.valueChanged.connect(self._on_field_changed)
        self._jw_pct_spin.valueChanged.connect(self._recompute_warranty_charge)
        jw_pct_row.addWidget(self._jw_pct_spin)
        pct_hint = QLabel("of sell price per year")
        pct_hint.setStyleSheet("color: #888; font-size: 9pt;")
        jw_pct_row.addWidget(pct_hint)
        jw_pct_row.addStretch()
        warranty_form.addRow("Charge per year:", jw_pct_row)

        # Live preview: 1 yr / 2 yr / 3 yr at current rate × current sell price.
        self._jw_preview_label = QLabel("")
        self._jw_preview_label.setStyleSheet(
            "color: #4a6741; font-size: 9pt; padding: 2px 0;"
        )
        warranty_form.addRow("", self._jw_preview_label)

        # Initial visibility — only the single rate row + preview show.
        jw_on = self._jaks_warranty_cb.isChecked()
        self._jw_pct_spin.setVisible(jw_on)
        pct_hint.setVisible(jw_on)
        self._jw_preview_label.setVisible(jw_on)
        self._jw_pct_hint_label = pct_hint  # keep ref for toggled handler
        self._update_jw_preview()

        warranty_box.setLayout(warranty_form)

        # ==============================================================
        #  🟣 PRICING
        # ==============================================================
        pricing_box = self._make_section("🟣  PRICING", expanded=False)
        price_form = QFormLayout()
        price_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        _sub_hdr = "font-weight:bold; font-size:9pt; color:#555; margin-top:2px;"

        # ── Last pricing edit ──
        pricing_ts = product.get("pricing_updated_at")
        if pricing_ts:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(pricing_ts))
                ts_text = dt.strftime("%b %d, %Y  %I:%M %p")
            except Exception:
                ts_text = str(pricing_ts)[:16]
            self._pricing_ts_label = QLabel(f"Last edited: {ts_text}")
            self._pricing_ts_label.setStyleSheet("color: #888; font-size: 8pt; font-style: italic;")
        else:
            self._pricing_ts_label = QLabel("No pricing edits yet")
            self._pricing_ts_label.setStyleSheet("color: #bbb; font-size: 8pt; font-style: italic;")
        price_form.addRow(self._pricing_ts_label)

        # ── Price Category (tiered pricing v2) ──
        try:
            from db import list_price_categories as _lpc
            _cats = _lpc(active_only=True)
        except Exception:
            _cats = []
        self._price_category_combo = QComboBox()
        self._price_category_combo.addItem("— inherit from vendor —", None)
        for _c in _cats:
            self._price_category_combo.addItem(f"{_c.get('code','')} — {_c.get('name','')}", _c.get("id"))
        _cur_cat = product.get("price_category_id")
        if _cur_cat is not None:
            _ix = self._price_category_combo.findData(_cur_cat)
            if _ix >= 0:
                self._price_category_combo.setCurrentIndex(_ix)
        self._price_category_combo.setToolTip(
            "Override the vendor's default price category for this product. "
            "Categories are managed under Settings → Pricing → Tiered Pricing."
        )
        self._price_category_combo.currentIndexChanged.connect(self._on_field_changed)
        self._price_category_combo.activated.connect(self._on_price_category_user_changed)
        # Wrap combo with a small "Resolved: …" status label
        pc_row = QHBoxLayout()
        pc_row.setContentsMargins(0, 0, 0, 0)
        pc_row.addWidget(self._price_category_combo, 1)
        self._resolved_cat_label = QLabel("")
        self._resolved_cat_label.setStyleSheet("color:#888; font-size:8pt; font-style:italic;")
        pc_row.addWidget(self._resolved_cat_label)
        price_form.addRow("Price Category:", pc_row)

        # ── Selling ──
        sell_hdr = QLabel("Selling")
        sell_hdr.setStyleSheet(_sub_hdr)
        price_form.addRow(sell_hdr)

        # Selling Price + margin display
        sell_row = QHBoxLayout()
        self._selling_price_spin = QDoubleSpinBox()
        self._selling_price_spin.setRange(0, 999999.99)
        self._selling_price_spin.setDecimals(2)
        self._selling_price_spin.setPrefix("$")
        self._selling_price_spin.setValue(float(product.get("selling_price", 0) or 0))
        self._selling_price_spin.valueChanged.connect(self._on_field_changed)
        self._selling_price_spin.valueChanged.connect(self._on_price_changed)
        self._selling_price_spin.valueChanged.connect(self._recompute_warranty_charge)
        # Track whether the user hand-edited Selling Price so vendor/cost
        # changes don't silently overwrite their value.
        self._selling_price_spin.valueChanged.connect(self._on_selling_price_user_edited)
        sell_row.addWidget(self._selling_price_spin)
        self._margin_label = QLabel("")
        self._margin_label.setStyleSheet("color: #228B22; font-weight: bold; font-size: 9pt;")
        sell_row.addWidget(self._margin_label)
        self._reset_vendor_price_btn = QPushButton("Reset to vendor price")
        self._reset_vendor_price_btn.setToolTip(
            "Discard the manual price and recompute from the vendor's price "
            "category × cost band."
        )
        self._reset_vendor_price_btn.clicked.connect(self._on_reset_to_vendor_price)
        sell_row.addWidget(self._reset_vendor_price_btn)
        sell_row.addStretch()
        price_form.addRow("Selling Price:", sell_row)

        # Margin editor
        margin_row = QHBoxLayout()
        self._margin_spin = QDoubleSpinBox()
        self._margin_spin.setRange(-999, 99.9)
        self._margin_spin.setDecimals(1)
        self._margin_spin.setSuffix(" %")
        self._margin_spin.setToolTip("Edit margin to auto-calculate selling price")
        self._margin_spin.valueChanged.connect(self._on_margin_edited)
        margin_row.addWidget(self._margin_spin)
        tier_btn = QPushButton("View Tier Pricing")
        tier_btn.setToolTip("View and manage tiered pricing for this product")
        tier_btn.clicked.connect(self._on_view_tier_pricing)
        margin_row.addWidget(tier_btn)
        margin_row.addStretch()
        price_form.addRow("Target Margin:", margin_row)

        # Compare At Price
        self._compare_price_spin = QDoubleSpinBox()
        self._compare_price_spin.setRange(0, 999999.99)
        self._compare_price_spin.setDecimals(2)
        self._compare_price_spin.setPrefix("$")
        self._compare_price_spin.setValue(float(product.get("compare_at_price", 0) or 0))
        self._compare_price_spin.valueChanged.connect(self._on_field_changed)
        price_form.addRow("Compare At Price:", self._compare_price_spin)

        # ── Cost ──
        cost_sep = QFrame()
        cost_sep.setFrameShape(QFrame.Shape.HLine)
        cost_sep.setStyleSheet("color: #ccc;")
        price_form.addRow(cost_sep)
        cost_hdr = QLabel("Cost")
        cost_hdr.setStyleSheet(_sub_hdr)
        price_form.addRow(cost_hdr)

        # Cost (Landed)
        cost_row = QHBoxLayout()
        self._cost_spin = QDoubleSpinBox()
        self._cost_spin.setRange(0, 999999.99)
        self._cost_spin.setDecimals(2)
        self._cost_spin.setPrefix("$")
        self._cost_spin.setValue(float(product.get("cost", 0) or 0))
        self._cost_spin.setToolTip("Landed cost (Vendor Cost + Shipping). Used for margin.")
        self._cost_spin.valueChanged.connect(self._on_field_changed)
        self._cost_spin.valueChanged.connect(self._on_price_changed)
        cost_row.addWidget(self._cost_spin)
        cost_info = QLabel("  ← Landed cost (used for margin)")
        cost_info.setStyleSheet("color: #888; font-size: 8pt;")
        cost_row.addWidget(cost_info)
        cost_row.addStretch()
        price_form.addRow("Cost (Landed):", cost_row)

        # Vendor Cost
        vendor_cost_row = QHBoxLayout()
        self._pai_cost_spin = QDoubleSpinBox()
        self._pai_cost_spin.setRange(0, 999999.99)
        self._pai_cost_spin.setDecimals(2)
        self._pai_cost_spin.setPrefix("$")
        self._pai_cost_spin.setValue(float(product.get("pai_cost", 0) or 0))
        self._pai_cost_spin.setToolTip("Raw supplier cost before shipping")
        self._pai_cost_spin.valueChanged.connect(self._on_field_changed)
        self._pai_cost_spin.valueChanged.connect(self._recalc_landed_cost)
        vendor_cost_row.addWidget(self._pai_cost_spin)
        cost_updated = product.get("cost_updated_at")
        if cost_updated:
            cost_date_label = QLabel(f"  Updated {str(cost_updated)[:10]}")
            cost_date_label.setStyleSheet("color: #888; font-size: 9pt;")
        else:
            cost_date_label = QLabel("  No price date")
            cost_date_label.setStyleSheet("color: #ccc; font-size: 9pt; font-style: italic;")
        vendor_cost_row.addWidget(cost_date_label)
        vendor_cost_row.addStretch()
        price_form.addRow("Vendor Cost:", vendor_cost_row)

        # Shipping Cost
        self._shipping_cost_spin = QDoubleSpinBox()
        self._shipping_cost_spin.setRange(0, 99999.99)
        self._shipping_cost_spin.setDecimals(2)
        self._shipping_cost_spin.setPrefix("$")
        self._shipping_cost_spin.setValue(float(product.get("shipping_cost", 0) or 0))
        self._shipping_cost_spin.setToolTip("Estimated shipping per unit")
        self._shipping_cost_spin.valueChanged.connect(self._on_field_changed)
        self._shipping_cost_spin.valueChanged.connect(self._recalc_landed_cost)
        price_form.addRow("Shipping Cost:", self._shipping_cost_spin)

        # Core fields live in the dedicated 🔄 CORE CHARGE section on the left panel.
        _core_hint = QLabel("🔄 Core Cost / Customer Core → see CORES section in right-side nav")
        _core_hint.setStyleSheet(
            "color: #8B6914; font-size: 8pt; font-style: italic; "
            "background-color: #FFFBEA; border: 1px dashed #DAA520; "
            "border-radius: 3px; padding: 3px 6px;"
        )
        _core_hint.setWordWrap(True)
        price_form.addRow(_core_hint)

        # ── Competitor ──
        comp_sep = QFrame()
        comp_sep.setFrameShape(QFrame.Shape.HLine)
        comp_sep.setStyleSheet("color: #ccc;")
        price_form.addRow(comp_sep)
        comp_hdr = QLabel("Competitor")
        comp_hdr.setStyleSheet(_sub_hdr)
        price_form.addRow(comp_hdr)

        _COMP_LABEL_STYLE = "color: #666; font-size: 9pt;"
        _COMP_VALUE_STYLE = "font-weight: bold; font-size: 9pt;"
        _COMP_STALE_STYLE = "color: #FF6600; font-weight: bold; font-size: 9pt;"

        self._comp_labels: dict[str, QLabel] = {}
        for abbr in ("DPD", "FLT", "ATL", "HHP"):
            val_label = QLabel("—")
            val_label.setStyleSheet(_COMP_VALUE_STYLE)
            self._comp_labels[abbr] = val_label
            row_label = QLabel(f"{abbr} Price:")
            row_label.setStyleSheet(_COMP_LABEL_STYLE)
            price_form.addRow(row_label, val_label)

        self._comp_scrape_date_label = QLabel("—")
        self._comp_scrape_date_label.setStyleSheet(_COMP_LABEL_STYLE)
        price_form.addRow("Last Scrape:", self._comp_scrape_date_label)

        if not self._create_mode and self._product_id:
            self._load_competitor_prices()

        pricing_box.setLayout(price_form)

        # ── Check Competitor Pricing button (below pricing section) ──
        pricing_scrape_row = QHBoxLayout()
        self._check_pricing_btn = QPushButton("💲 Check Pricing")
        self._check_pricing_btn.setToolTip(
            "Scrape competitor sites for pricing on this product"
        )
        self._check_pricing_btn.setStyleSheet(self._BTN_ACTION)
        self._check_pricing_btn.clicked.connect(self._on_check_pricing)
        pricing_scrape_row.addWidget(self._check_pricing_btn)

        self._ai_price_btn = QPushButton("🤖 Suggest Price")
        self._ai_price_btn.setToolTip(
            "Use AI to analyze competitor prices and suggest optimal selling price"
        )
        self._ai_price_btn.setStyleSheet(self._BTN_UTIL)
        self._ai_price_btn.clicked.connect(self._on_ai_suggest_price)
        pricing_scrape_row.addWidget(self._ai_price_btn)
        pricing_scrape_row.addStretch()
        price_form.addRow(pricing_scrape_row)

        # ==============================================================
        #  🟠 INVENTORY — Category, Qty, Audit, Sales
        # ==============================================================
        inv_box = self._make_section("🟠  INVENTORY", expanded=False)
        inv_form = QFormLayout()
        inv_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Product Type ──────────────────────────────────────────────
        self._product_type_combo = QComboBox()
        self._product_type_combo.addItem("Inventoried", "inventoried")
        self._product_type_combo.addItem("Non-Inventoried (warranty, service, fee)", "non_inventoried")
        self._product_type_combo.addItem("Service / Labour", "service")
        self._product_type_combo.setToolTip(
            "Inventoried: stock levels tracked; qty_on_hand decremented on sale.\n"
            "Non-Inventoried: sold at any quantity without affecting stock — "
            "use for extended warranties, freight, misc. fees.\n"
            "Service / Labour: same as Non-Inventoried; reserved for labour items."
        )
        current_pt = str(product.get("product_type") or "inventoried")
        pt_idx = self._product_type_combo.findData(current_pt)
        if pt_idx >= 0:
            self._product_type_combo.setCurrentIndex(pt_idx)
        self._product_type_combo.currentIndexChanged.connect(self._on_product_type_changed)
        self._product_type_combo.currentIndexChanged.connect(self._on_field_changed)
        inv_form.addRow("Product Type:", self._product_type_combo)

        # Category — editable dropdown with autocomplete + Add button (moved from Basic)
        cat_row = QHBoxLayout()
        self._category_combo = QComboBox()
        self._category_combo.setEditable(True)
        self._category_combo.addItem("")
        self._all_categories: list[str] = []
        try:
            self._all_categories = list(get_all_categories())
            for cat in self._all_categories:
                self._category_combo.addItem(cat)
        except Exception:
            pass
        completer = QCompleter(self._all_categories)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._category_combo.setCompleter(completer)
        current_cat = str(product.get("category", "") or "")
        idx = self._category_combo.findText(current_cat, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._category_combo.setCurrentIndex(idx)
        else:
            self._category_combo.setCurrentText(current_cat)
        self._category_combo.currentTextChanged.connect(self._on_field_changed)
        self._category_combo.currentTextChanged.connect(self._update_invoice_preview)
        cat_row.addWidget(self._category_combo, 1)

        add_cat_btn = QPushButton("+ Category")
        add_cat_btn.setToolTip("Add a new category")
        add_cat_btn.setStyleSheet(self._BTN_UTIL)
        add_cat_btn.setMaximumWidth(80)
        add_cat_btn.clicked.connect(self._on_add_category)
        cat_row.addWidget(add_cat_btn)
        inv_form.addRow("Category:", cat_row)

        # Subcategory — editable dropdown with autocomplete + Add button (moved from Basic)
        subcat_row = QHBoxLayout()
        self._subcategory_combo = QComboBox()
        self._subcategory_combo.setEditable(True)
        self._subcategory_combo.addItem("")
        self._all_subcategories: list[str] = []
        try:
            from db import get_all_subcategories
            self._all_subcategories = list(get_all_subcategories())
            for sc in self._all_subcategories:
                self._subcategory_combo.addItem(sc)
        except Exception:
            pass
        subcat_completer = QCompleter(self._all_subcategories)
        subcat_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        subcat_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._subcategory_combo.setCompleter(subcat_completer)
        current_subcat = str(product.get("subcategory", "") or "")
        idx = self._subcategory_combo.findText(current_subcat, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self._subcategory_combo.setCurrentIndex(idx)
        else:
            self._subcategory_combo.setCurrentText(current_subcat)
        self._subcategory_combo.currentTextChanged.connect(self._on_field_changed)
        self._subcategory_combo.currentTextChanged.connect(self._update_invoice_preview)
        subcat_row.addWidget(self._subcategory_combo, 1)

        add_subcat_btn = QPushButton("+ Subcat")
        add_subcat_btn.setToolTip("Add a new subcategory")
        add_subcat_btn.setStyleSheet(self._BTN_UTIL)
        add_subcat_btn.setMaximumWidth(80)
        add_subcat_btn.clicked.connect(self._on_add_subcategory)
        subcat_row.addWidget(add_subcat_btn)
        inv_form.addRow("Subcategory:", subcat_row)

        inv_sep1 = QFrame()
        inv_sep1.setFrameShape(QFrame.Shape.HLine)
        inv_sep1.setStyleSheet("color: #ccc; margin-top: 4px;")
        inv_form.addRow(inv_sep1)

        self._min_qty_spin = QSpinBox()
        self._min_qty_spin.setRange(0, 99999)
        self._min_qty_spin.setValue(int(product.get("min_qty", 0) or 0))
        self._min_qty_spin.valueChanged.connect(self._on_field_changed)
        inv_form.addRow("Min Qty:", self._min_qty_spin)

        self._max_qty_spin = QSpinBox()
        self._max_qty_spin.setRange(0, 99999)
        self._max_qty_spin.setValue(int(product.get("max_qty", 0) or 0))
        self._max_qty_spin.valueChanged.connect(self._on_field_changed)
        inv_form.addRow("Max Qty:", self._max_qty_spin)

        qoh_val = int(product.get("quantity", 0) or product.get("qty_on_hand", 0) or 0)
        qoh_note = " (set after first receipt)" if self._create_mode else ""
        self._qoh_label = QLabel(f"{qoh_val:,}{qoh_note}")
        self._qoh_label.setStyleSheet("font-weight: 600; color: #374151;")
        inv_form.addRow("QOH:", self._qoh_label)

        # ── Live stock breakdown (Phase 6) ──
        _reserved_val = int(product.get("reserved_qty", 0) or 0)
        _incoming_val = int(product.get("incoming_po_qty", 0) or 0)
        _available_val = max(0, qoh_val - _reserved_val)
        self._reserved_qty_label = QLabel(f"{_reserved_val:,}")
        self._reserved_qty_label.setStyleSheet("color: #b26a00; font-size: 9pt;")
        self._reserved_qty_label.setToolTip("Quantity reserved by open sales orders / quotes")
        inv_form.addRow("Reserved:", self._reserved_qty_label)

        self._available_qty_label = QLabel(f"{_available_val:,}")
        self._available_qty_label.setStyleSheet(
            "color: #1b5e20; font-weight: bold; font-size: 9pt;"
        )
        self._available_qty_label.setToolTip("Available = QOH − Reserved")
        inv_form.addRow("Available:", self._available_qty_label)

        self._incoming_po_label = QLabel(f"{_incoming_val:,}")
        self._incoming_po_label.setStyleSheet("color: #1565c0; font-size: 9pt;")
        self._incoming_po_label.setToolTip("Quantity on open purchase orders")
        inv_form.addRow("Incoming PO:", self._incoming_po_label)

        self._bin_location_edit = QLineEdit(str(product.get("bin_location", "") or ""))
        self._bin_location_edit.setPlaceholderText("Aisle / Shelf / Bin")
        self._bin_location_edit.setMaximumWidth(220)
        self._bin_location_edit.textChanged.connect(self._on_field_changed)
        inv_form.addRow("Bin / Location:", self._bin_location_edit)

        self._last_received_label = QLabel(str(product.get("last_received_date", "") or "—"))
        self._last_received_label.setStyleSheet("color: #555; font-size: 9pt;")
        inv_form.addRow("Last Received:", self._last_received_label)

        self._last_sold_label = QLabel(str(product.get("last_sold_date", "") or "—"))
        self._last_sold_label.setStyleSheet("color: #555; font-size: 9pt;")
        inv_form.addRow("Last Sold:", self._last_sold_label)

        # Days since sale (computed if last_sold_date present)
        _dss_text = "—"
        try:
            _ls = product.get("last_sold_date")
            if _ls:
                from datetime import date, datetime
                if isinstance(_ls, str):
                    _ls_dt = datetime.fromisoformat(_ls[:10]).date()
                else:
                    _ls_dt = _ls
                _dss_text = f"{(date.today() - _ls_dt).days} days"
        except Exception:
            pass
        self._days_since_sale_label = QLabel(_dss_text)
        self._days_since_sale_label.setStyleSheet("color: #555; font-size: 9pt;")
        inv_form.addRow("Days Since Sale:", self._days_since_sale_label)

        # Direct inventory adjustment controls (applies immediately with transaction log)
        self._inv_set_qoh_spin = QSpinBox()
        self._inv_set_qoh_spin.setRange(0, 999999)
        self._inv_set_qoh_spin.setValue(qoh_val)
        inv_form.addRow("Set On Hand:", self._inv_set_qoh_spin)

        self._inv_effective_date = QDateEdit()
        self._inv_effective_date.setCalendarPopup(True)
        self._inv_effective_date.setDisplayFormat("yyyy-MM-dd")
        self._inv_effective_date.setDate(QDate.currentDate())
        inv_form.addRow("Starting Date:", self._inv_effective_date)

        self._inv_apply_btn = QPushButton("Apply Inventory Adjustment")
        self._inv_apply_btn.setStyleSheet(self._BTN_UTIL)
        self._inv_apply_btn.clicked.connect(self._on_apply_inventory_adjustment)
        inv_form.addRow("", self._inv_apply_btn)

        self._inv_adjust_hint = QLabel(
            "Sets exact on-hand quantity and logs an Adjustment transaction."
        )
        self._inv_adjust_hint.setStyleSheet("color: #666; font-size: 9pt;")
        inv_form.addRow("", self._inv_adjust_hint)

        if not self._product_id:
            self._inv_set_qoh_spin.setEnabled(False)
            self._inv_effective_date.setEnabled(False)
            self._inv_apply_btn.setEnabled(False)
            self._inv_apply_btn.setToolTip("Save product first, then apply inventory adjustment")

        # ── Audit ──────────────────────────────────────────────────
        inv_sep2 = QFrame()
        inv_sep2.setFrameShape(QFrame.Shape.HLine)
        inv_sep2.setStyleSheet("color: #ccc; margin-top: 4px;")
        inv_form.addRow(inv_sep2)

        audit_hdr = QLabel("Audit")
        audit_hdr.setStyleSheet("font-weight:bold; font-size:9pt; color:#555; margin-top:2px;")
        inv_form.addRow(audit_hdr)

        self._needs_audit_cb = QCheckBox("Needs Audit Review")
        self._needs_audit_cb.setToolTip("Flag this product for inventory audit")
        self._needs_audit_cb.setChecked(bool(product.get("needs_audit", 0)))
        self._needs_audit_cb.toggled.connect(self._on_field_changed)
        inv_form.addRow("Audit:", self._needs_audit_cb)

        self._last_audited_label = QLabel(str(product.get("last_audited_date", "") or "Never"))
        self._last_audited_label.setStyleSheet("color: #666; font-size: 9pt;")
        inv_form.addRow("Last Audited:", self._last_audited_label)

        # ── Sales Statistics ──────────────────────────────────────
        inv_sep3 = QFrame()
        inv_sep3.setFrameShape(QFrame.Shape.HLine)
        inv_sep3.setStyleSheet("color: #ccc; margin-top: 4px;")
        inv_form.addRow(inv_sep3)

        sales_hdr = QLabel("Sales Statistics")
        sales_hdr.setStyleSheet("font-weight:bold; font-size:9pt; color:#555; margin-top:2px;")
        inv_form.addRow(sales_hdr)

        self._sales_90d_label = QLabel("—")
        self._sales_90d_label.setStyleSheet("color: #2D2D2D; font-size: 9pt;")
        inv_form.addRow("Last 90 Days:", self._sales_90d_label)

        self._sales_1y_label = QLabel("—")
        self._sales_1y_label.setStyleSheet("color: #2D2D2D; font-size: 9pt;")
        inv_form.addRow("Last 1 Year:", self._sales_1y_label)

        # Load sales stats
        if not self._create_mode and self._product_id:
            try:
                from db import get_product_sales_stats
                stats = get_product_sales_stats(self._product_id)
                self._sales_90d_label.setText(
                    f"{stats['qty_90d']} sold  •  ${stats['revenue_90d']:,.2f} revenue"
                )
                self._sales_1y_label.setText(
                    f"{stats['qty_1y']} sold  •  ${stats['revenue_1y']:,.2f} revenue"
                )
            except Exception:
                pass

        # ── Serial Tracking (Phase 3) ──────────────────────────────
        inv_sep4 = QFrame()
        inv_sep4.setFrameShape(QFrame.Shape.HLine)
        inv_sep4.setStyleSheet("color: #ccc; margin-top: 4px;")
        inv_form.addRow(inv_sep4)

        serial_hdr = QLabel("Serial Tracking")
        serial_hdr.setStyleSheet(
            "font-weight: bold; font-size: 9pt; color: #555; margin-top: 2px;"
        )
        inv_form.addRow(serial_hdr)
        inv_form.addRow("", self._requires_serial_cb)
        inv_form.addRow("", self._requires_serial_receive_cb)
        inv_form.addRow("", self._requires_serial_warranty_cb)

        inv_box.setLayout(inv_form)

        # ==============================================================
        #  📝 NOTES — Customer Notes + Internal Notes
        # ==============================================================
        notes_box = self._make_section("📝  NOTES", expanded=False)
        notes_form = QFormLayout()
        notes_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._notes_public_edit = QPlainTextEdit(str(product.get("notes_public", "") or ""))
        self._notes_public_edit.setPlaceholderText("Customer-visible notes (appears on invoices/quotes)")
        self._notes_public_edit.setMaximumHeight(54)
        self._notes_public_edit.textChanged.connect(self._on_field_changed)
        notes_form.addRow("Customer Notes:", self._notes_public_edit)

        self._notes_internal_edit = QPlainTextEdit(str(product.get("notes_internal", "") or ""))
        self._notes_internal_edit.setPlaceholderText("Internal only — never shown to customer")
        self._notes_internal_edit.setMaximumHeight(54)
        self._notes_internal_edit.textChanged.connect(self._on_field_changed)
        notes_form.addRow("Internal Notes:", self._notes_internal_edit)

        notes_box.setLayout(notes_form)
        # NOTE: notes_box is added AFTER interchanges (below)

        # ==============================================================
        #  📦 SHIPPING — Weight, Dimensions, Freight info
        # ==============================================================
        shipping_box = self._make_section("📦  SHIPPING", expanded=False)
        shipping_form = QFormLayout()
        shipping_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Weight
        weight_row = QHBoxLayout()
        self._weight_spin = QDoubleSpinBox()
        self._weight_spin.setRange(0, 99999.99)
        self._weight_spin.setDecimals(2)
        self._weight_spin.setValue(float(product.get("weight", 0) or 0))
        self._weight_spin.setToolTip("Product weight for shipping calculations")
        self._weight_spin.valueChanged.connect(self._on_field_changed)
        weight_row.addWidget(self._weight_spin)
        self._weight_unit_combo = QComboBox()
        self._weight_unit_combo.addItems(["LBS", "KG", "OZ"])
        self._weight_unit_combo.setCurrentText(
            str(product.get("weight_unit", "LBS") or "LBS")
        )
        self._weight_unit_combo.setMaximumWidth(60)
        self._weight_unit_combo.currentTextChanged.connect(self._on_field_changed)
        weight_row.addWidget(self._weight_unit_combo)
        weight_row.addStretch()
        shipping_form.addRow("Weight:", weight_row)

        # Dimensions header
        dim_hdr = QLabel("Dimensions")
        dim_hdr.setStyleSheet(
            "font-weight:bold; font-size:10pt; color:#4a6741; margin-top:4px;"
        )
        shipping_form.addRow(dim_hdr)

        # Length / Width / Height
        dim_row = QHBoxLayout()
        self._length_spin = QDoubleSpinBox()
        self._length_spin.setRange(0, 9999.99)
        self._length_spin.setDecimals(2)
        self._length_spin.setValue(float(product.get("length", 0) or 0))
        self._length_spin.setToolTip("Length")
        self._length_spin.valueChanged.connect(self._on_field_changed)
        dim_row.addWidget(QLabel("L:"))
        dim_row.addWidget(self._length_spin)

        self._width_spin = QDoubleSpinBox()
        self._width_spin.setRange(0, 9999.99)
        self._width_spin.setDecimals(2)
        self._width_spin.setValue(float(product.get("width", 0) or 0))
        self._width_spin.setToolTip("Width")
        self._width_spin.valueChanged.connect(self._on_field_changed)
        dim_row.addWidget(QLabel("W:"))
        dim_row.addWidget(self._width_spin)

        self._height_spin = QDoubleSpinBox()
        self._height_spin.setRange(0, 9999.99)
        self._height_spin.setDecimals(2)
        self._height_spin.setValue(float(product.get("height", 0) or 0))
        self._height_spin.setToolTip("Height")
        self._height_spin.valueChanged.connect(self._on_field_changed)
        dim_row.addWidget(QLabel("H:"))
        dim_row.addWidget(self._height_spin)

        self._dim_unit_combo = QComboBox()
        self._dim_unit_combo.addItems(["IN", "CM", "FT"])
        self._dim_unit_combo.setCurrentText(
            str(product.get("dimension_unit", "IN") or "IN")
        )
        self._dim_unit_combo.setMaximumWidth(50)
        self._dim_unit_combo.currentTextChanged.connect(self._on_field_changed)
        dim_row.addWidget(self._dim_unit_combo)
        dim_row.addStretch()
        shipping_form.addRow("L × W × H:", dim_row)

        # Shipping Cost (moved reference — actual spin is in PRICING)
        ship_note = QLabel(
            "Shipping Cost is set in the PRICING section"
        )
        ship_note.setStyleSheet("color: #888; font-size: 8pt; font-style: italic;")
        shipping_form.addRow("", ship_note)

        shipping_box.setLayout(shipping_form)

        # ==============================================================
        #  ⚫ CROSS-REFERENCES — XRef table + add row
        # ==============================================================
        xref_box = self._make_section("⚫  CROSS-REFERENCES", expanded=False)
        xref_layout = QVBoxLayout()

        self._xref_table = QTableWidget(0, 4)
        self._xref_table.setHorizontalHeaderLabels(["Part Number", "Type", "Supplier", ""])
        self._xref_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._xref_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._xref_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._xref_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._xref_table.horizontalHeader().resizeSection(1, 110)
        self._xref_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._xref_table.horizontalHeader().resizeSection(2, 140)
        self._xref_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._xref_table.horizontalHeader().resizeSection(3, 30)
        self._xref_table.horizontalHeader().setStretchLastSection(False)
        self._xref_table.setMaximumHeight(200)
        xref_layout.addWidget(self._xref_table)

        # Add row inputs
        xref_add_row = QHBoxLayout()
        self._xref_pn_input = QLineEdit()
        self._xref_pn_input.setPlaceholderText("Part number…")
        self._xref_pn_input.returnPressed.connect(self._on_add_xref)
        xref_add_row.addWidget(self._xref_pn_input, 2)

        self._xref_type_combo = QComboBox()
        self._xref_type_combo.addItems(["OEM", "Aftermarket", "Supplier", "Competitor", "Internal"])
        xref_add_row.addWidget(self._xref_type_combo)

        self._xref_supplier_input = QLineEdit()
        self._xref_supplier_input.setPlaceholderText("Mfr / Supplier")
        xref_add_row.addWidget(self._xref_supplier_input, 1)

        add_xref_btn = QPushButton("Add")
        add_xref_btn.setMaximumWidth(50)
        add_xref_btn.clicked.connect(self._on_add_xref)
        xref_add_row.addWidget(add_xref_btn)
        xref_layout.addLayout(xref_add_row)

        # ── Scrape cross-references button + progress ──
        scrape_xref_row = QHBoxLayout()
        self._find_xref_btn = QPushButton("🔗 Find Crosses")
        self._find_xref_btn.setToolTip(
            "Scrape competitor sites for cross-reference data"
        )
        self._find_xref_btn.setStyleSheet(self._BTN_ACTION)
        self._find_xref_btn.clicked.connect(self._on_find_xrefs)
        scrape_xref_row.addWidget(self._find_xref_btn)
        self._edit_scrape_progress = QProgressBar()
        self._edit_scrape_progress.setMaximumHeight(14)
        self._edit_scrape_progress.setMaximumWidth(180)
        self._edit_scrape_progress.hide()
        scrape_xref_row.addWidget(self._edit_scrape_progress)
        self._edit_scrape_label = QLabel("")
        self._edit_scrape_label.setStyleSheet("color: #6a1b9a; font-size: 9pt;")
        scrape_xref_row.addWidget(self._edit_scrape_label)
        scrape_xref_row.addStretch()
        xref_layout.addLayout(scrape_xref_row)

        # ── Manual search override + debug ──
        override_row = QHBoxLayout()
        override_row.addWidget(QLabel("Manual Search:"))
        self._scrape_override_edit = QLineEdit()
        self._scrape_override_edit.setPlaceholderText(
            "Override search value (part #, OEM, or paste URL)"
        )
        self._scrape_override_edit.setMinimumWidth(200)
        override_row.addWidget(self._scrape_override_edit, 1)

        self._debug_toggle = QCheckBox("Debug")
        self._debug_toggle.setToolTip("Show scraper debug info")
        self._debug_toggle.setChecked(False)
        self._debug_toggle.toggled.connect(
            lambda on: self._scrape_debug_label.setVisible(on and bool(self._scrape_debug_label.text()))
        )
        override_row.addWidget(self._debug_toggle)
        xref_layout.addLayout(override_row)

        # Scrape info labels (always visible when populated)
        self._scrape_search_info = QLabel("")
        self._scrape_search_info.setStyleSheet(
            "font-size: 9pt; color: #1565c0; padding: 2px 0;"
        )
        self._scrape_search_info.setWordWrap(True)
        xref_layout.addWidget(self._scrape_search_info)

        self._scrape_debug_label = QLabel("")
        self._scrape_debug_label.setStyleSheet(
            "font-size: 8pt; color: #888; font-family: Consolas, monospace; padding: 2px 0;"
        )
        self._scrape_debug_label.setWordWrap(True)
        self._scrape_debug_label.hide()
        xref_layout.addWidget(self._scrape_debug_label)

        self._edit_scrape_worker: ScraperWorker | None = None

        # ── Pending Cross References (from scraper) ──
        pending_sep = QLabel("Pending Cross References")
        pending_sep.setStyleSheet(
            "font-weight: bold; font-size: 9pt; color: #6a1b9a; "
            "margin-top: 4px; padding: 2px 0;"
        )
        xref_layout.addWidget(pending_sep)

        self._pending_xref_table = QTableWidget(0, 4)
        self._pending_xref_table.setHorizontalHeaderLabels(["✓", "Part Number", "Source", "Type"])
        self._pending_xref_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pending_xref_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pending_xref_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._pending_xref_table.horizontalHeader().resizeSection(0, 36)
        self._pending_xref_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._pending_xref_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._pending_xref_table.horizontalHeader().resizeSection(2, 80)
        self._pending_xref_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._pending_xref_table.horizontalHeader().resizeSection(3, 100)
        self._pending_xref_table.setMaximumHeight(150)
        xref_layout.addWidget(self._pending_xref_table)

        pending_btn_row = QHBoxLayout()
        self._accept_pending_btn = QPushButton("✅ Add Selected")
        self._accept_pending_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 3px 8px; border-radius: 3px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self._accept_pending_btn.clicked.connect(self._on_accept_pending_xrefs)
        pending_btn_row.addWidget(self._accept_pending_btn)

        self._discard_pending_btn = QPushButton("❌ Discard")
        self._discard_pending_btn.setStyleSheet(self._BTN_UTIL)
        self._discard_pending_btn.clicked.connect(self._on_discard_pending_xrefs)
        pending_btn_row.addWidget(self._discard_pending_btn)

        self._select_all_pending_btn = QPushButton("Select All")
        self._select_all_pending_btn.setStyleSheet(self._BTN_UTIL)
        self._select_all_pending_btn.clicked.connect(
            lambda: self._set_all_pending_xref_checks(Qt.CheckState.Checked)
        )
        pending_btn_row.addWidget(self._select_all_pending_btn)

        self._clear_pending_btn = QPushButton("Clear")
        self._clear_pending_btn.setStyleSheet(self._BTN_UTIL)
        self._clear_pending_btn.clicked.connect(
            lambda: self._set_all_pending_xref_checks(Qt.CheckState.Unchecked)
        )
        pending_btn_row.addWidget(self._clear_pending_btn)
        pending_btn_row.addStretch()
        xref_layout.addLayout(pending_btn_row)

        self._pending_xref_items: list[dict] = []
        self._load_pending_xrefs()

        # Populate existing interchanges
        self._xref_items: list[dict] = []
        self._load_interchanges(product)

        xref_box.setLayout(xref_layout)

        # ==============================================================
        #  💡 SUGGESTED SELLS — products commonly bought together
        # ==============================================================
        suggest_box = self._make_section("💡  SUGGESTED SELLS", expanded=False)
        suggest_layout = QVBoxLayout()

        # ── Current suggested sells table ──
        self._suggest_table = QTableWidget(0, 6)
        self._suggest_table.setHorizontalHeaderLabels([
            "SKU", "Description", "Supplier", "Price", "Type", "",
        ])
        self._suggest_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._suggest_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._suggest_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._suggest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._suggest_table.horizontalHeader().resizeSection(0, 140)
        self._suggest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._suggest_table.horizontalHeader().resizeSection(1, 420)
        self._suggest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._suggest_table.horizontalHeader().resizeSection(2, 110)
        self._suggest_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._suggest_table.horizontalHeader().resizeSection(3, 90)
        self._suggest_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._suggest_table.horizontalHeader().resizeSection(4, 90)
        self._suggest_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._suggest_table.horizontalHeader().resizeSection(5, 30)
        self._suggest_table.horizontalHeader().setStretchLastSection(False)
        self._suggest_table.setMaximumHeight(180)
        self._suggest_table.doubleClicked.connect(self._on_open_suggested_sell)
        self._suggest_table.installEventFilter(self)
        suggest_layout.addWidget(self._suggest_table)

        # ── Search row ──
        suggest_search_row = QHBoxLayout()
        self._suggest_search_input = QLineEdit()
        self._suggest_search_input.setPlaceholderText("Search by SKU, description, or part number…")
        self._suggest_search_input.returnPressed.connect(self._on_search_suggested_sell)
        suggest_search_row.addWidget(self._suggest_search_input, 2)

        search_suggest_btn = QPushButton("🔍 Search")
        search_suggest_btn.setMaximumWidth(80)
        search_suggest_btn.clicked.connect(self._on_search_suggested_sell)
        suggest_search_row.addWidget(search_suggest_btn)
        suggest_layout.addLayout(suggest_search_row)

        # ── Search results table (hidden until search) ──
        self._suggest_search_table = QTableWidget(0, 7)
        self._suggest_search_table.setHorizontalHeaderLabels([
            "ID", "SKU", "Description", "Supplier", "Price", "Margin%", "Engine",
        ])
        self._suggest_search_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._suggest_search_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._suggest_search_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._suggest_search_table.setColumnHidden(0, True)  # Hide ID column
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._suggest_search_table.horizontalHeader().resizeSection(1, 140)
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._suggest_search_table.horizontalHeader().resizeSection(3, 100)
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._suggest_search_table.horizontalHeader().resizeSection(4, 80)
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self._suggest_search_table.horizontalHeader().resizeSection(5, 65)
        self._suggest_search_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self._suggest_search_table.horizontalHeader().resizeSection(6, 100)
        self._suggest_search_table.setMaximumHeight(160)
        self._suggest_search_table.doubleClicked.connect(self._on_add_from_suggest_search)
        self._suggest_search_table.hide()
        suggest_layout.addWidget(self._suggest_search_table)

        # ── Search result count + Add button row ──
        suggest_result_row = QHBoxLayout()
        self._suggest_search_count = QLabel("")
        self._suggest_search_count.setStyleSheet("font-size: 8pt; color: #888;")
        self._suggest_search_count.hide()
        suggest_result_row.addWidget(self._suggest_search_count)
        suggest_result_row.addStretch()
        self._suggest_add_btn = QPushButton("➕ Add Selected")
        self._suggest_add_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 3px 10px; border-radius: 3px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self._suggest_add_btn.clicked.connect(self._on_add_from_suggest_search)
        self._suggest_add_btn.hide()
        suggest_result_row.addWidget(self._suggest_add_btn)
        suggest_layout.addLayout(suggest_result_row)

        self._suggest_search_results: list[dict] = []
        self._suggest_items: list[dict] = []
        self._load_suggested_sells(product)

        suggest_box.setLayout(suggest_layout)

        # ==============================================================
        #  📷 PRODUCT IMAGES
        # ==============================================================
        img_box = self._make_section("📷  PRODUCT IMAGES", expanded=True)
        img_layout = QVBoxLayout()
        img_layout.setSpacing(6)

        # Image preview area (supports drag & drop) — generous size so the
        # whole image always fits without cropping.
        self._img_preview = QLabel("Drop image here\nor click Import")
        self._img_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_preview.setMinimumHeight(420)
        self._img_preview.setMaximumHeight(720)
        self._img_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._img_preview.setScaledContents(False)
        self._img_preview.setAcceptDrops(True)
        self._img_preview.setStyleSheet(
            "QLabel { background-color: #ffffff; border: 1px solid #c8c8c8; "
            "border-radius: 6px; color: #888; font-size: 10pt; padding: 8px; }"
        )
        self._img_preview_empty_qss = (
            "QLabel { background-color: #fafafa; border: 2px dashed #cccccc; "
            "border-radius: 6px; color: #888; font-size: 10pt; padding: 8px; }"
        )
        self._img_preview_filled_qss = (
            "QLabel { background-color: #ffffff; border: 1px solid #c8c8c8; "
            "border-radius: 6px; padding: 8px; }"
        )
        img_layout.addWidget(self._img_preview, 1)

        # Thumbnail strip — horizontal scroller showing all images at once.
        self._img_thumb_scroll = QScrollArea()
        self._img_thumb_scroll.setWidgetResizable(True)
        self._img_thumb_scroll.setFixedHeight(96)
        self._img_thumb_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._img_thumb_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._img_thumb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._img_thumb_scroll.setStyleSheet(
            "QScrollArea { background-color: #f4f4f4; border: 1px solid #d8d8d8; "
            "border-radius: 4px; }"
        )
        self._img_thumb_container = QWidget()
        self._img_thumb_layout = QHBoxLayout(self._img_thumb_container)
        self._img_thumb_layout.setContentsMargins(6, 6, 6, 6)
        self._img_thumb_layout.setSpacing(6)
        self._img_thumb_layout.addStretch()
        self._img_thumb_scroll.setWidget(self._img_thumb_container)
        self._img_thumb_buttons: list[QPushButton] = []
        img_layout.addWidget(self._img_thumb_scroll)

        # Combined navigation + caption row (compact, centered)
        img_nav_row = QHBoxLayout()
        img_nav_row.setSpacing(8)
        img_nav_row.addStretch()
        self._img_prev_btn = QPushButton("◀")
        self._img_prev_btn.setFixedSize(28, 24)
        self._img_prev_btn.setToolTip("Previous image")
        self._img_prev_btn.clicked.connect(lambda: self._navigate_image(-1))
        img_nav_row.addWidget(self._img_prev_btn)
        self._img_info_label = QLabel("0 / 0")
        self._img_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_info_label.setMinimumWidth(60)
        self._img_info_label.setStyleSheet(
            "color: #555; font-size: 9pt; font-weight: 600;"
        )
        img_nav_row.addWidget(self._img_info_label)
        self._img_next_btn = QPushButton("▶")
        self._img_next_btn.setFixedSize(28, 24)
        self._img_next_btn.setToolTip("Next image")
        self._img_next_btn.clicked.connect(lambda: self._navigate_image(1))
        img_nav_row.addWidget(self._img_next_btn)
        img_nav_row.addStretch()
        img_layout.addLayout(img_nav_row)

        # Buttons row
        img_btn_row = QHBoxLayout()
        self._img_import_btn = QPushButton("📁 Import Image")
        self._img_import_btn.setToolTip("Import an image from disk")
        self._img_import_btn.clicked.connect(self._on_import_image)
        if self._create_mode:
            self._img_import_btn.setEnabled(False)
            self._img_import_btn.setToolTip("Save the product first before importing images")
        img_btn_row.addWidget(self._img_import_btn)
        self._img_paste_btn = QPushButton("📋 Paste (Ctrl+V)")
        self._img_paste_btn.setToolTip("Paste image from clipboard")
        self._img_paste_btn.clicked.connect(self._on_paste_image)
        if self._create_mode:
            self._img_paste_btn.setEnabled(False)
        img_btn_row.addWidget(self._img_paste_btn)
        self._img_delete_btn = QPushButton("🗑 Delete Image")
        self._img_delete_btn.setToolTip("Delete the current image")
        self._img_delete_btn.clicked.connect(self._on_delete_image)
        if self._create_mode:
            self._img_delete_btn.setEnabled(False)
        img_btn_row.addWidget(self._img_delete_btn)
        self._img_pai_btn = QPushButton("🌐 Find PAI Images")
        self._img_pai_btn.setToolTip(
            "Search the PAI portal for product images and add them.\n"
            "Requires PAI_ENABLE=1 + PAI_USER/PAI_PASS in .env."
        )
        self._img_pai_btn.setStyleSheet(
            "QPushButton { background-color: #4a7ba7; color: white; "
            "font-weight: 600; padding: 5px 12px; border: none; "
            "border-radius: 4px; } "
            "QPushButton:hover { background-color: #3d6b94; } "
            "QPushButton:disabled { background-color: #a8b8c8; color: #eee; }"
        )
        self._img_pai_btn.clicked.connect(lambda: self._run_edit_scrape("images"))
        # Keep enabled in create mode — clicking will prompt the user to
        # save the product first (handled in _run_edit_scrape) so the
        # button is discoverable rather than greyed-out.
        img_btn_row.addWidget(self._img_pai_btn)
        img_btn_row.addStretch()
        img_layout.addLayout(img_btn_row)

        self._image_list: list[dict] = []
        self._image_index: int = 0
        if not self._create_mode and self._product_id:
            self._load_product_images()
        else:
            self._update_image_display()

        img_box.setLayout(img_layout)

        # ==============================================================
        #  🛒 SHOPIFY — Tags + Description HTML
        # ==============================================================
        shopify_box = self._make_section("🛒  SHOPIFY", expanded=False)
        shopify_form = QFormLayout()
        shopify_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Tags
        tags_row = QHBoxLayout()
        self._tags_edit = QLineEdit(str(product.get("tags", "") or ""))
        self._tags_edit.setPlaceholderText("Comma-separated tags: turbo, caterpillar, C15, remanufactured")
        self._tags_edit.textChanged.connect(self._on_field_changed)
        tags_row.addWidget(self._tags_edit, 1)
        self._ai_tags_btn = QPushButton("🤖 Tags")
        self._ai_tags_btn.setToolTip("Generate Shopify tags using AI")
        self._ai_tags_btn.setStyleSheet(self._BTN_UTIL)
        self._ai_tags_btn.setMaximumWidth(70)
        self._ai_tags_btn.clicked.connect(self._on_ai_generate_tags)
        tags_row.addWidget(self._ai_tags_btn)
        shopify_form.addRow("Tags:", tags_row)

        shopify_box.setLayout(shopify_form)

        # ── Section summaries (shown when collapsed) ──
        def _pricing_summary() -> str:
            sell = self._selling_price_spin.value()
            cost = self._cost_spin.value()
            parts = []
            if sell > 0:
                parts.append(f"Sell: ${sell:,.2f}")
            if cost > 0:
                parts.append(f"Cost: ${cost:,.2f}")
            if sell > 0 and cost > 0:
                margin = ((sell - cost) / sell) * 100
                parts.append(f"Margin: {margin:.0f}%")
            scrape = self._comp_scrape_date_label.text()
            if scrape and scrape != "—":
                parts.append(f"Scrape: {scrape}")
            return "  |  ".join(parts) if parts else ""

        def _inventory_summary() -> str:
            mn = self._min_qty_spin.value()
            mx = self._max_qty_spin.value()
            cat = self._category_combo.currentText().strip()
            parts = [f"Min: {mn}", f"Max: {mx}"]
            if cat:
                parts.insert(0, cat)
            if self._needs_audit_cb.isChecked():
                parts.append("⚠ Audit")
            return "  |  ".join(parts)

        def _notes_summary() -> str:
            cust = bool(self._notes_public_edit.toPlainText().strip())
            intl = bool(self._notes_internal_edit.toPlainText().strip())
            parts = []
            if cust:
                parts.append("Customer: Yes")
            if intl:
                parts.append("Internal: Yes")
            return "  |  ".join(parts) if parts else "No notes"

        def _images_summary() -> str:
            count = len(self._image_list)
            return f"Images: {count}" if count else "No images"

        def _vendor_summary() -> str:
            sup = self._supplier_combo.currentText()
            if sup == "-- Select Vendor --":
                sup = ""
            parts = []
            if sup:
                parts.append(sup)
            if self._supplier_warranty_cb.isChecked():
                v = self._sw_value_spin.value()
                u = self._sw_unit_combo.currentText()
                parts.append(f"Supplier Warranty: {v} {u}" if v else "Supplier Warranty: ✓")
            if self._jaks_warranty_cb.isChecked():
                v = self._jw_value_spin.value()
                u = self._jw_unit_combo.currentText()
                pct = self._jw_pct_spin.value()
                tag = f"JAKS Warranty: {v} {u}" if v else "JAKS Warranty: ✓"
                if pct > 0:
                    tag += f" ({pct:g}%)"
                parts.append(tag)
            return "  |  ".join(parts) if parts else ""

        self._set_section_summary(vendor_box, _vendor_summary)

        def _warranty_summary() -> str:
            parts = []
            if self._supplier_warranty_cb.isChecked():
                sv = self._sw_value_spin.value()
                su = self._sw_unit_combo.currentText()
                if sv > 0:
                    parts.append(f"Supplier: {sv} {su}")
            if self._jaks_warranty_cb.isChecked():
                jv = self._jw_value_spin.value()
                ju = self._jw_unit_combo.currentText()
                if jv > 0:
                    parts.append(f"JAKS: {jv} {ju}")
                    pct = self._jw_pct_spin.value()
                    if pct > 0:
                        parts.append(f"{pct:g}% / yr")
            return "  |  ".join(parts) if parts else "No warranty"

        self._set_section_summary(warranty_box, _warranty_summary)
        self._set_section_summary(pricing_box, _pricing_summary)
        self._set_section_summary(inv_box, _inventory_summary)
        self._set_section_summary(notes_box, _notes_summary)
        self._set_section_summary(img_box, _images_summary)

        def _shopify_summary() -> str:
            tags = self._tags_edit.text().strip()
            parts = []
            if tags:
                tag_count = len([t for t in tags.split(",") if t.strip()])
                parts.append(f"Tags: {tag_count}")
            return "  |  ".join(parts) if parts else "Not configured"

        self._set_section_summary(shopify_box, _shopify_summary)

        # ==============================================================
        # BUILD STACKED WIDGET PAGES (one per section, each scrollable)
        # ==============================================================
        # Section definitions: (name, icon, box_widget(s))
        # Platform includes the preview_box below it
        self._section_boxes = [
            ("PLATFORM", "🔧", platform_box),
            ("SUPPLIER / VENDOR", "📦", vendor_box),
            ("WARRANTY", "🛡", warranty_box),
            ("PRICING", "💰", pricing_box),
            ("INVENTORY", "📋", inv_box),
            ("NOTES", "📝", notes_box),
            ("SHIPPING", "🚚", shipping_box),
            ("CROSS-REFERENCES", "⚙", xref_box),
            ("SUGGESTED SELLS", "💡", suggest_box),
            ("IMAGES", "📷", img_box),
            ("SHOPIFY", "🛒", shopify_box),
        ]

        # Build a scrollable page for each section
        self._section_pages: list[QWidget] = []
        for i, (name, icon, box) in enumerate(self._section_boxes):
            page_scroll = QScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setFrameShape(QFrame.Shape.NoFrame)
            page_widget = QWidget()
            page_layout = QVBoxLayout(page_widget)
            page_layout.setContentsMargins(4, 4, 4, 4)
            page_layout.setSpacing(4)
            # Always expand sections in stacked view (no collapsing)
            box.setCheckable(False)
            box.setChecked(True)
            self._toggle_section(box, True)
            page_layout.addWidget(box)
            page_layout.addStretch()
            page_scroll.setWidget(page_widget)
            self._section_stack.addWidget(page_scroll)
            self._section_pages.append(page_scroll)

        # --- LEFT PANEL: finish with pricing summary ---
        # Pricing summary panel in the left column
        pricing_panel = QFrame()
        pricing_panel.setStyleSheet(
            "QFrame#pricing_panel { background-color: #F5F5F0; "
            "border: 1px solid #A0A090; border-radius: 5px; }"
            "QLabel { background: transparent; }"
        )
        pricing_panel.setObjectName("pricing_panel")
        pp_layout = QVBoxLayout(pricing_panel)
        pp_layout.setContentsMargins(12, 8, 12, 8)
        pp_layout.setSpacing(4)

        pp_title = QLabel("Quick Stats")
        pp_title.setStyleSheet(
            "font-weight: bold; font-size: 10pt; color: #4a6741; "
            "border-bottom: 1px solid #A0A090; padding-bottom: 4px; background: transparent;"
        )
        pp_layout.addWidget(pp_title)

        _SUMMARY_LABEL = "color: #606050; font-size: 9pt; background: transparent;"
        _SUMMARY_VALUE = "color: #2D2D2D; font-weight: bold; font-size: 11pt; background: transparent;"

        for label_text, attr_name in [
            ("Selling Price", "_sidebar_sell"),
            ("Margin", "_sidebar_margin"),
            ("Target Margin", "_sidebar_target"),
            ("Cost", "_sidebar_cost"),
            ("Compare At Price", "_sidebar_compare"),
        ]:
            lbl = QLabel(label_text)
            lbl.setStyleSheet(_SUMMARY_LABEL)
            val = QLabel("—")
            val.setStyleSheet(_SUMMARY_VALUE)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            row = QHBoxLayout()
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val)
            pp_layout.addLayout(row)
            setattr(self, attr_name, val)

        # Last pricing edit date
        pricing_ts = product.get("pricing_updated_at")
        if pricing_ts:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(pricing_ts))
                ts_text = dt.strftime("%b %d  %I:%M %p")
            except Exception:
                ts_text = str(pricing_ts)[:16]
            pe_lbl = QLabel("Last Price Edit")
            pe_lbl.setStyleSheet(_SUMMARY_LABEL)
            pe_val = QLabel(ts_text)
            pe_val.setStyleSheet("color: #888; font-size: 9pt; font-style: italic; background: transparent;")
            pe_val.setAlignment(Qt.AlignmentFlag.AlignRight)
            pe_row = QHBoxLayout()
            pe_row.addWidget(pe_lbl)
            pe_row.addStretch()
            pe_row.addWidget(pe_val)
            pp_layout.addLayout(pe_row)

        left_layout.addWidget(pricing_panel)

        # ── Sales Activity panel (below Quick Stats) ──────────────
        sales_panel = QFrame()
        sales_panel.setStyleSheet(
            "QFrame#sales_panel { background-color: #F5F5F0; "
            "border: 1px solid #A0A090; border-radius: 5px; }"
            "QLabel { background: transparent; }"
        )
        sales_panel.setObjectName("sales_panel")
        sp_layout = QVBoxLayout(sales_panel)
        sp_layout.setContentsMargins(12, 8, 12, 8)
        sp_layout.setSpacing(4)

        sp_title = QLabel("Sales Activity")
        sp_title.setStyleSheet(
            "font-weight: bold; font-size: 10pt; color: #4a6741; "
            "border-bottom: 1px solid #A0A090; padding-bottom: 4px; background: transparent;"
        )
        sp_layout.addWidget(sp_title)

        self._sidebar_sales_90d = QLabel("—")
        self._sidebar_sales_90d.setStyleSheet(_SUMMARY_VALUE)
        self._sidebar_sales_90d.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_90d = QLabel("Last 90 Days")
        lbl_90d.setStyleSheet(_SUMMARY_LABEL)
        row_90d = QHBoxLayout()
        row_90d.addWidget(lbl_90d)
        row_90d.addStretch()
        row_90d.addWidget(self._sidebar_sales_90d)
        sp_layout.addLayout(row_90d)

        self._sidebar_sales_1y = QLabel("—")
        self._sidebar_sales_1y.setStyleSheet(_SUMMARY_VALUE)
        self._sidebar_sales_1y.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_1y = QLabel("Last 1 Year")
        lbl_1y.setStyleSheet(_SUMMARY_LABEL)
        row_1y = QHBoxLayout()
        row_1y.addWidget(lbl_1y)
        row_1y.addStretch()
        row_1y.addWidget(self._sidebar_sales_1y)
        sp_layout.addLayout(row_1y)

        # Profit rows (revenue − cost×qty using current product cost)
        self._sidebar_profit_90d = QLabel("—")
        self._sidebar_profit_90d.setStyleSheet(_SUMMARY_VALUE)
        self._sidebar_profit_90d.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_p90 = QLabel("Profit — 90 Days")
        lbl_p90.setStyleSheet(_SUMMARY_LABEL)
        row_p90 = QHBoxLayout()
        row_p90.addWidget(lbl_p90)
        row_p90.addStretch()
        row_p90.addWidget(self._sidebar_profit_90d)
        sp_layout.addLayout(row_p90)

        self._sidebar_profit_1y = QLabel("—")
        self._sidebar_profit_1y.setStyleSheet(_SUMMARY_VALUE)
        self._sidebar_profit_1y.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl_p1y = QLabel("Profit — 1 Year")
        lbl_p1y.setStyleSheet(_SUMMARY_LABEL)
        row_p1y = QHBoxLayout()
        row_p1y.addWidget(lbl_p1y)
        row_p1y.addStretch()
        row_p1y.addWidget(self._sidebar_profit_1y)
        sp_layout.addLayout(row_p1y)

        left_layout.addWidget(sales_panel)

        # Load sidebar sales stats
        if not self._create_mode and self._product_id:
            try:
                from db import get_product_sales_stats
                stats = get_product_sales_stats(self._product_id)
                self._sidebar_sales_90d.setText(
                    f"{stats['qty_90d']} sold  •  ${stats['revenue_90d']:,.2f}"
                )
                self._sidebar_sales_1y.setText(
                    f"{stats['qty_1y']} sold  •  ${stats['revenue_1y']:,.2f}"
                )
                p90 = float(stats.get("profit_90d") or 0)
                p1y = float(stats.get("profit_1y") or 0)
                pos = "color: #15803D;"
                neg = "color: #B91C1C;"
                self._sidebar_profit_90d.setText(f"${p90:,.2f}")
                self._sidebar_profit_90d.setStyleSheet(
                    _SUMMARY_VALUE + (pos if p90 >= 0 else neg)
                )
                self._sidebar_profit_1y.setText(f"${p1y:,.2f}")
                self._sidebar_profit_1y.setStyleSheet(
                    _SUMMARY_VALUE + (pos if p1y >= 0 else neg)
                )
            except Exception:
                pass

        left_layout.addStretch()

        # Wrap the left panel in a scroll area so BASIC, CORE, Quick Stats and
        # Sales Activity all stay reachable even when the dialog is short.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_panel)
        left_scroll.setMinimumWidth(380)
        left_scroll.setMaximumWidth(480)
        content_split.addWidget(left_scroll)

        # --- CENTER PANEL: Section stack ---
        content_split.addWidget(self._section_stack, 1)

        # --- RIGHT PANEL: Nav buttons ---
        sidebar = QWidget()
        sidebar.setMaximumWidth(190)
        sidebar.setMinimumWidth(160)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(2)

        nav_title = QLabel("SECTIONS")
        nav_title.setStyleSheet(
            "font-weight: bold; font-size: 9pt; color: #606050; "
            "padding: 4px 8px; background: transparent;"
        )
        sidebar_layout.addWidget(nav_title)

        self._nav_buttons: list[QPushButton] = []
        _NAV_BTN_STYLE = (
            "QPushButton { background-color: #E8E8E0; color: #2D2D2D; "
            "font-weight: 600; padding: 5px 10px; border-radius: 4px; "
            "font-size: 9pt; text-align: left; border: 1px solid #A0A090; }"
            "QPushButton:hover { background-color: #d8d8d0; }"
        )
        _NAV_BTN_ACTIVE = (
            "QPushButton { background-color: #6B7A4A; color: white; "
            "font-weight: bold; padding: 5px 10px; border-radius: 4px; "
            "font-size: 9pt; text-align: left; border: 1px solid #5A6A3A; }"
            "QPushButton:hover { background-color: #5A6A3A; }"
        )
        self._nav_btn_style = _NAV_BTN_STYLE
        self._nav_btn_active = _NAV_BTN_ACTIVE

        for i, (name, icon, box) in enumerate(self._section_boxes):
            btn = QPushButton(f"  {icon}  {name}")
            btn.setStyleSheet(_NAV_BTN_STYLE)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda checked, idx=i: self._nav_to_section(idx)
            )
            sidebar_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sidebar_layout.addStretch()

        content_split.addWidget(sidebar)
        outer.addLayout(content_split, 1)

        # Save / Cancel buttons — full width above preview bar
        _SAVE_ENABLED = (
            "QPushButton { background-color: #4A7A4A; color: white; "
            "font-weight: bold; padding: 8px 18px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background-color: #3D6A3D; }"
        )
        _SAVE_DISABLED = (
            "QPushButton { background-color: #bdbdbd; color: #757575; "
            "padding: 8px 18px; border-radius: 4px; font-size: 10pt; }"
        )
        self._save_enabled_style = _SAVE_ENABLED
        self._save_disabled_style = _SAVE_DISABLED

        self._unsaved_label = QLabel("")
        self._unsaved_label.setStyleSheet("color: #B8860B; font-weight: bold; font-size: 9pt;")
        outer.addWidget(self._unsaved_label)

        button_layout = QHBoxLayout()
        if self._create_mode:
            self._save_btn = QPushButton("Save")
            self._save_btn.setEnabled(True)
            self._save_btn.setStyleSheet(_SAVE_ENABLED)
            self._save_btn.setAutoDefault(False)
            self._save_btn.setDefault(False)
            self._save_new_btn = QPushButton("Save && New")
            self._save_new_btn.setToolTip("Create this product and open a fresh dialog (Ctrl+Enter)")
            self._save_new_btn.setStyleSheet(
                "QPushButton { background-color: #6B7A4A; color: white; "
                "font-weight: bold; padding: 8px 14px; border-radius: 4px; font-size: 9pt; }"
                "QPushButton:hover { background-color: #5A6A3A; }"
            )
            self._save_new_btn.setAutoDefault(False)
            self._save_new_btn.setDefault(False)
            self._save_new_btn.clicked.connect(self._on_save_and_new)
            button_layout.addWidget(self._save_new_btn)
        else:
            self._save_btn = QPushButton("Save Changes")
            self._save_btn.setEnabled(False)
            self._save_btn.setStyleSheet(_SAVE_DISABLED)
            self._save_btn.setAutoDefault(False)
            self._save_btn.setDefault(False)
        self._save_btn.clicked.connect(self._on_save)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; "
            "padding: 8px 18px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background-color: #7b8290; }"
        )
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self._save_btn)
        button_layout.addWidget(cancel_btn)
        button_layout.addStretch()
        outer.addLayout(button_layout)

        # ── Bottom Preview Bar ──
        preview_bar = QFrame()
        preview_bar.setObjectName("preview_bar")
        preview_bar.setStyleSheet(
            "QFrame#preview_bar { background-color: #f5f5ef; "
            "border: 1px solid #A0A090; border-radius: 5px; }"
        )
        pb_layout = QVBoxLayout(preview_bar)
        pb_layout.setContentsMargins(10, 6, 10, 6)
        pb_layout.setSpacing(4)

        # -- Invoice line preview (moved from Platform section) --
        inv_title = QLabel("Preview — How this product appears on quotes, sales orders & invoices:")
        inv_title.setStyleSheet(
            "font-weight: bold; font-size: 9pt; color: #4a6741; background: transparent;"
        )
        pb_layout.addWidget(inv_title)

        self._invoice_preview.setParent(None)  # detach from old Platform page
        self._invoice_preview.setStyleSheet(
            "font-size: 11pt; padding: 6px 8px; background-color: #fffff0; "
            "border: 1px solid #ddd; border-radius: 3px;"
        )
        self._invoice_preview.setMinimumHeight(30)
        self._invoice_preview.setMaximumHeight(60)
        pb_layout.addWidget(self._invoice_preview)

        # -- Collapsible "Web / Marketplace Description" drawer (Phase 4) --
        self._desc_drawer_btn = QToolButton()
        self._desc_drawer_btn.setText("Web / Marketplace Description")
        self._desc_drawer_btn.setCheckable(True)
        self._desc_drawer_btn.setChecked(False)
        self._desc_drawer_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._desc_drawer_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._desc_drawer_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 2px 0; "
            "font-weight: bold; font-size: 9pt; color: #4a6741; }"
            "QToolButton:hover { color: #2d4a25; }"
        )
        pb_layout.addWidget(self._desc_drawer_btn)

        self._desc_drawer_body = QWidget()
        _drawer_lay = QVBoxLayout(self._desc_drawer_body)
        _drawer_lay.setContentsMargins(0, 4, 0, 0)
        _drawer_lay.setSpacing(4)

        # -- Product description: editor + rendered HTML preview side-by-side --
        desc_split = QHBoxLayout()
        desc_split.setSpacing(8)

        # Left: raw HTML editor
        desc_edit_col = QVBoxLayout()
        desc_edit_header = QHBoxLayout()
        desc_edit_lbl = QLabel("Product Description (HTML source):")
        desc_edit_lbl.setStyleSheet(
            "font-weight: bold; font-size: 8pt; color: #555; background: transparent;"
        )
        desc_edit_header.addWidget(desc_edit_lbl)
        desc_edit_header.addStretch()
        desc_edit_header.addWidget(self._ai_shopify_desc_btn)
        desc_edit_col.addLayout(desc_edit_header)
        self._desc_html_edit.setMinimumHeight(80)
        self._desc_html_edit.setMaximumHeight(140)
        desc_edit_col.addWidget(self._desc_html_edit)
        desc_split.addLayout(desc_edit_col, 1)

        # Right: rendered HTML preview
        desc_preview_col = QVBoxLayout()
        desc_preview_lbl = QLabel("Rendered Preview:")
        desc_preview_lbl.setStyleSheet(
            "font-weight: bold; font-size: 8pt; color: #555; background: transparent;"
        )
        desc_preview_col.addWidget(desc_preview_lbl)
        self._desc_preview_browser = QTextBrowser()
        self._desc_preview_browser.setOpenExternalLinks(False)
        self._desc_preview_browser.setStyleSheet(
            "QTextBrowser { background-color: #ffffff; border: 1px solid #ddd; "
            "border-radius: 3px; padding: 6px; font-size: 10pt; }"
        )
        self._desc_preview_browser.setMinimumHeight(80)
        self._desc_preview_browser.setMaximumHeight(140)
        desc_preview_col.addWidget(self._desc_preview_browser)
        desc_split.addLayout(desc_preview_col, 1)

        _drawer_lay.addLayout(desc_split)
        self._desc_drawer_body.setVisible(False)
        pb_layout.addWidget(self._desc_drawer_body)

        def _toggle_desc_drawer(checked: bool) -> None:
            self._desc_drawer_body.setVisible(checked)
            self._desc_drawer_btn.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )

        self._desc_drawer_btn.toggled.connect(_toggle_desc_drawer)

        outer.addWidget(preview_bar)

        # ── Keyboard shortcuts ──
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._on_save)

        if self._create_mode:
            save_new_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
            save_new_shortcut.activated.connect(self._on_save_and_new)

        # Initial state
        self._updating_price = False
        self._platform_syncing = False
        self._initialize_smart_platform_state()
        self._update_margin()
        self._update_core_margin()
        self._refresh_core_badge()
        self._update_invoice_preview()
        self._update_desc_preview()
        self._sync_margin_spin()
        self._update_pricing_summary()
        # Initialize the "Resolved: …" status label next to Price Category.
        # We do NOT auto-fill prices on load — only on user actions.
        try:
            self._update_resolved_category_label()
        except Exception:
            pass

        # Show requested section if provided, otherwise default to Platform.
        start_idx = 0
        if self._initial_section_key:
            for i, (name, _icon, _box) in enumerate(self._section_boxes):
                if str(name).strip().upper() == self._initial_section_key:
                    start_idx = i
                    break
        self._nav_to_section(start_idx)

        # Default the quick-scrape search field to best available PN
        if self._quick_scrape_edit is not None:
            sku_text = self._sku_edit.text().strip()
            # Don't use JAKS- prefixed SKUs as search terms (internal, not searchable)
            sku_for_search = "" if sku_text.upper().startswith("JAKS-") else sku_text
            default_pn = (
                self._pai_sku_edit.text().strip()
                or self._oem_pn_edit.text().strip()
                or sku_for_search
            )
            self._quick_scrape_edit.setText(default_pn)

    # ==================================================================
    # VENDOR / MANUFACTURER CHANGE → SKU WARNING
    # ==================================================================
    def _on_sku_manual_edit(self) -> None:
        """User typed in SKU field — lock it from auto-overwrite."""
        self._sku_manually_edited = True
        self._on_field_changed()

    def _build_auto_sku(self) -> str:
        """Build SKU as JAKS-{VENDOR_CODE}-{PART_NUMBER}.

        When Private Label is checked, uses JAKS-{PART_NUMBER} (no vendor code).

        Part number resolution order:
        1. Private Label Part # override (private-label only)
        2. Vendor SKU field
        3. OEM Part # field
        4. Part number extracted from current SKU (strip existing JAKS- prefix)
        """
        is_private_label = (
            hasattr(self, "_private_label_cb") and self._private_label_cb.isChecked()
        )

        vendor_code = ""
        if not is_private_label:
            vendor_id = self._supplier_combo.currentData()
            if vendor_id:
                for v in self._vendors:
                    if v.get("id") == vendor_id:
                        vendor_code = (v.get("code") or "").strip()
                        break
            if not vendor_code:
                return ""

        # Resolve part number: private-label override → vendor SKU → OEM → extract from current SKU
        part_number = ""
        if is_private_label and hasattr(self, "_private_label_part_edit"):
            part_number = self._private_label_part_edit.text().strip()
        if not part_number:
            part_number = self._pai_sku_edit.text().strip()
        if not part_number:
            part_number = self._oem_pn_edit.text().strip()
        if not part_number:
            # Extract from current SKU: strip "JAKS-" prefix and any existing vendor code
            current = self._sku_edit.text().strip()
            if current.upper().startswith("JAKS-"):
                remainder = current[5:]  # after "JAKS-"
                # If remainder has a code prefix like "DON-P551100", strip it
                parts = remainder.split("-", 1)
                if len(parts) == 2 and parts[0].isalpha() and len(parts[0]) <= 6:
                    part_number = parts[1]
                else:
                    part_number = remainder
        if not part_number:
            return ""

        if is_private_label:
            return f"JAKS-{part_number}"
        return f"JAKS-{vendor_code}-{part_number}"

    def _auto_update_sku(self) -> None:
        """Auto-update SKU from vendor code + vendor SKU (if not manually edited)."""
        if self._sku_manually_edited:
            return
        # In edit mode, only update if the SKU field has been unlocked
        if not self._create_mode and self._sku_edit.isReadOnly():
            return
        new_sku = self._build_auto_sku()
        if new_sku:
            self._sku_edit.setText(new_sku)

    def _on_mfr_or_supplier_changed(self) -> None:
        """Warn user when manufacturer or supplier changes in edit mode."""
        if self._create_mode or not self._product_id:
            # In create mode, just auto-update
            self._auto_update_sku()
            return
        if not self._modified:
            return  # initial population, not a user action
        new_sku = self._build_auto_sku()
        msg = "Changing supplier/manufacturer may affect SKU.\n\n"
        if new_sku:
            msg += f"Update SKU to '{new_sku}'?"
        else:
            msg += "Do you want to unlock the SKU for editing?"
        reply = QMessageBox.question(
            self, "SKU Update", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sku_edit.setReadOnly(False)
            self._sku_edit.setStyleSheet("")
            self._sku_manually_edited = False
            if new_sku:
                self._sku_edit.setText(new_sku)
            else:
                self._sku_edit.setFocus()

    def _update_pai_button_visibility(self, text: str = "") -> None:
        """Show PAI Lookup button only when supplier contains 'PAI'."""
        supplier = text or self._supplier_combo.currentText()
        is_pai = "pai" in supplier.lower()
        self._pai_lookup_btn.setVisible(is_pai)

    def _update_vendor_avail_status(self, data: dict) -> None:
        """Update the vendor availability status label from product data."""
        avail_text = str(data.get("vendor_availability", "") or "")
        if not avail_text:
            self._vendor_avail_status.setText("")
            self._vendor_avail_status.setStyleSheet("font-size: 9pt;")
            return
        upper = avail_text.upper()
        if "N/A" in upper or "NOT AVAILABLE" in upper:
            self._vendor_avail_status.setText("❌ Not Available")
            self._vendor_avail_status.setStyleSheet(
                "font-size: 9pt; font-weight: bold; color: #c62828;"
            )
        elif "OUT" in upper and "IN STOCK" not in upper:
            self._vendor_avail_status.setText("⚠ Out of Stock (all locations)")
            self._vendor_avail_status.setStyleSheet(
                "font-size: 9pt; font-weight: bold; color: #e65100;"
            )
        else:
            self._vendor_avail_status.setText("✔ Available")
            self._vendor_avail_status.setStyleSheet(
                "font-size: 9pt; font-weight: bold; color: #2e7d32;"
            )

    # ==================================================================
    # ADD ALTERNATE AS NEW PRODUCT
    # ==================================================================
    def _on_add_alternate_product(self) -> None:
        """Open a new product dialog pre-filled with an alternate's PAI data."""
        alts_text = self._vendor_alternates_edit.text().strip()
        if not alts_text:
            QMessageBox.information(self, "No Alternates", "No vendor alternates to add.")
            return

        alt_list = [s.strip() for s in alts_text.split(",") if s.strip()]
        if not alt_list:
            return

        # If only one alternate, use it directly; otherwise let user pick
        if len(alt_list) == 1:
            chosen_sku = alt_list[0]
        else:
            from PySide6.QtWidgets import QInputDialog
            chosen_sku, ok = QInputDialog.getItem(
                self,
                "Select Alternate",
                "Choose which alternate to add as a new product:",
                alt_list,
                0,
                False,
            )
            if not ok or not chosen_sku:
                return

        # Find the PAI result for this alternate SKU
        alt_data = None
        for r in self._pai_all_results:
            if r.get("sku", "") == chosen_sku or r.get("pai_sku", "") == chosen_sku:
                alt_data = r
                break

        # Build pre-fill from the current product + alternate PAI data
        prefill = {}

        # Carry over structural fields from the current product
        prefill["supplier"] = self._supplier_combo.currentText()
        if prefill["supplier"] == "-- Select Vendor --":
            prefill["supplier"] = ""
        prefill["category"] = self._category_combo.currentText().strip()
        prefill["subcategory"] = self._subcategory_combo.currentText().strip()
        prefill["manufacturer"] = self._manufacturer_combo.currentText().strip()
        prefill["condition"] = self._condition_combo.currentText()
        prefill["oem_manufacturer"] = self._engine_mfr_combo.currentText().strip()
        prefill["engine"] = self._engine_model_combo.currentText().strip()
        prefill["truck_manufacturer"] = self._truck_mfr_combo.currentText().strip()
        prefill["truck_model"] = self._truck_model_edit.currentText().strip()
        prefill["truck_system"] = self._truck_system_combo.currentText().strip()

        # PAI-specific data from the alternate
        prefill["pai_sku"] = chosen_sku
        if alt_data:
            cost = alt_data.get("cost", 0)
            if cost and float(cost) > 0:
                prefill["pai_cost"] = float(cost)
                prefill["cost"] = float(cost)
            lp = alt_data.get("list_price", 0)
            if lp and float(lp) > 0:
                prefill["compare_at_price"] = float(lp)
            prefill["pai_link"] = alt_data.get("url", "") or alt_data.get("source", "")
            prefill["vendor_description"] = alt_data.get("description", "")

            # Stock / availability
            stock = alt_data.get("stock") or {}
            not_available = alt_data.get("not_available", False)
            if not_available:
                prefill["vendor_availability"] = "N/A — Not Available"
            elif stock:
                parts = []
                for wh, info in stock.items():
                    if wh.startswith("_"):
                        continue
                    if isinstance(info, int):
                        parts.append(f"{wh}: {info}")
                    elif info == "in_stock":
                        parts.append(f"{wh}: In Stock")
                    elif info == "out_of_stock":
                        parts.append(f"{wh}: Out")
                    else:
                        parts.append(f"{wh}: {info}")
                prefill["vendor_availability"] = ", ".join(parts)

            # Weight
            import re as _re_wt
            weight_str = alt_data.get("weight", "")
            if weight_str:
                wm = _re_wt.search(r"([\d.]+)\s*(LBS?|KG|OZ)", weight_str, _re_wt.IGNORECASE)
                if wm:
                    prefill["weight"] = float(wm.group(1))
                    unit_raw = wm.group(2).upper()
                    prefill["weight_unit"] = "LBS" if unit_raw in ("LB", "LBS") else unit_raw

            # Warranty
            warranty = alt_data.get("warranty", "")
            if warranty:
                wm2 = _re_wt.search(
                    r"(\d+)\s*(day|month|year)s?", warranty, _re_wt.IGNORECASE
                )
                if wm2:
                    prefill["supplier_warranty_enabled"] = 1
                    prefill["supplier_warranty_value"] = int(wm2.group(1))
                    prefill["supplier_warranty_unit"] = wm2.group(2).lower() + "s"

            # Product group → title hint
            pg = alt_data.get("product_group", "")
            if pg:
                prefill["title"] = pg.title()
                prefill["brief_description"] = pg.title()

        # Set pre-fill and open a new modeless dialog
        EditProductDialog._prefill_values = prefill
        dlg = EditProductDialog(create_mode=True, parent=None)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda: self._remove_open_dialog(dlg))
        EditProductDialog._open_dialogs.append(dlg)
        dlg.show()

    @staticmethod
    def _remove_open_dialog(dlg) -> None:
        """Remove a closed dialog from the open dialogs tracker."""
        try:
            EditProductDialog._open_dialogs.remove(dlg)
        except ValueError:
            pass

    # ==================================================================
    # ADD CATEGORY / SUBCATEGORY
    # ==================================================================
    def _on_add_category(self) -> None:
        """Prompt user to add a new category."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Add Category", "New category name:"
        )
        if ok and text.strip():
            cat = text.strip()
            if self._category_combo.findText(cat, Qt.MatchFlag.MatchFixedString) < 0:
                self._category_combo.addItem(cat)
            self._category_combo.setCurrentText(cat)

    def _on_add_subcategory(self) -> None:
        """Prompt user to add a new subcategory."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Add Subcategory", "New subcategory name:"
        )
        if ok and text.strip():
            sc = text.strip()
            if self._subcategory_combo.findText(sc, Qt.MatchFlag.MatchFixedString) < 0:
                self._subcategory_combo.addItem(sc)
            self._subcategory_combo.setCurrentText(sc)

    # ==================================================================
    # ADD VENDOR
    # ==================================================================
    def _on_add_vendor(self) -> None:
        """Open vendor dialog to add a new vendor, then refresh the dropdown."""
        from .vendor_dialog import VendorDialog
        dlg = VendorDialog(parent=self)
        if dlg.exec():
            current_text = self._supplier_combo.currentText()
            self._supplier_combo.blockSignals(True)
            self._supplier_combo.clear()
            self._supplier_combo.addItem("-- Select Vendor --", None)
            try:
                self._vendors = get_all_vendors()
                for v in self._vendors:
                    self._supplier_combo.addItem(v.get("name", ""), v.get("id"))
            except Exception:
                pass
            if self._supplier_combo.count() > 1:
                self._supplier_combo.setCurrentIndex(self._supplier_combo.count() - 1)
            self._supplier_combo.blockSignals(False)
            self._on_field_changed()

    # ==================================================================
    # PAI LOOKUP
    # ==================================================================
    def _on_pai_lookup(self) -> None:
        """Look up on PAI using OEM Part # first, then Vendor SKU as fallback."""
        # Prefer OEM Part # for PAI search (PAI cross-references OEM → their SKU)
        oem_pn = self._oem_pn_edit.text().strip()
        vendor_sku = self._pai_sku_edit.text().strip()
        pn = oem_pn or vendor_sku
        if not pn:
            QMessageBox.warning(
                self, "No Part Number",
                "Enter an OEM Part # or Vendor SKU to look up.",
            )
            return
        if self._pai_lookup_worker is not None:
            QMessageBox.information(self, "Busy", "A PAI lookup is already running.")
            return

        product_name = self._title_edit.text().strip() if hasattr(self, "_title_edit") else ""
        self._pai_lookup_btn.setEnabled(False)
        self._pai_lookup_btn.setText("⏳ Searching PAI…")
        self._pai_lookup_worker = _PAILookupWorker(pn, product_name=product_name, parent=self)
        self._pai_lookup_worker.finished.connect(self._on_pai_lookup_done)
        self._pai_lookup_worker.multi_match.connect(self._on_pai_multi_match)
        self._pai_lookup_worker.error.connect(self._on_pai_lookup_error)
        self._pai_lookup_worker.status_update.connect(self._on_pai_status)
        self._pai_lookup_worker.start()

    def _on_pai_status(self, msg: str) -> None:
        """Update PAI lookup button text with progress."""
        self._pai_lookup_btn.setText(f"⏳ {msg[:25]}…" if len(msg) > 25 else f"⏳ {msg}")

    def _on_pai_lookup_done(self, result: dict) -> None:
        self._pai_lookup_worker = None
        self._pai_lookup_btn.setEnabled(True)
        self._pai_lookup_btn.setText("🔍 PAI Lookup")

        # ── Vendor SKU (PAI's own SKU — the primary goal of lookup) ──
        pai_sku = result.get("pai_sku") or result.get("sku") or ""
        if pai_sku:
            self._pai_sku_edit.setText(pai_sku)

        # ── Vendor Cost (YOUR PRICE from PAI — always store, even if 0) ──
        cost = result.get("cost")
        if cost and float(cost) > 0:
            self._pai_cost_spin.setValue(float(cost))
        # Always record the cost-check date
        from datetime import datetime as _dt
        self._pai_cost_date = _dt.now().isoformat()

        # ── Compare At Price (LIST PRICE from PAI = MSRP) ──
        list_price = result.get("list_price", 0)
        if list_price and float(list_price) > 0:
            self._compare_price_spin.setValue(float(list_price))

        # PAI just refreshed cost → re-derive Selling Price from the
        # vendor's price-category band (respects user override flags).
        try:
            self._apply_vendor_auto_price()
        except Exception:
            pass

        # ── Vendor Link ──
        url = result.get("url") or result.get("source") or ""
        if url and url not in ("CACHE", "DETAIL_PAGE"):
            self._pai_link_edit.setText(url)

        # ── Stock / Availability ──
        not_available = result.get("not_available", False)
        stock = result.get("stock") or {}
        total_avail = result.get("total_available", 0)
        in_stock = result.get("in_stock", False)

        if not_available:
            # PAI says "Not Available" — product not stocked
            stock_text = "PAI: N/A — Not Available"
        elif stock:
            parts = []
            for wh, info in stock.items():
                if wh.startswith("_"):
                    continue
                if isinstance(info, int):
                    parts.append(f"{wh}: {info} In Stock")
                elif isinstance(info, dict):
                    qty = info.get("qty") or info.get("quantity") or info.get("available")
                    if qty is not None:
                        parts.append(f"{wh}: {qty}")
                    else:
                        parts.append(f"{wh}: {info}")
                elif info == "in_stock":
                    parts.append(f"{wh}: ✔ In Stock")
                elif info == "out_of_stock":
                    parts.append(f"{wh}: ✖ Out")
                elif info == "low_stock":
                    parts.append(f"{wh}: ⚠ Low")
                else:
                    parts.append(f"{wh}: {info}")
            if parts:
                stock_text = "PAI Stock — " + ", ".join(parts)
                if total_avail > 0:
                    stock_text += f" (Total: {total_avail})"
            else:
                stock_text = ""
        else:
            stock_text = ""

        # ── Populate vendor availability field (structured per-warehouse) ──
        if not_available:
            self._vendor_avail_edit.setText("N/A — Not Available")
        elif stock:
            avail_parts = []
            for wh, info in stock.items():
                if wh.startswith("_"):
                    continue
                if isinstance(info, int):
                    avail_parts.append(f"{wh}: {info}")
                elif info == "in_stock":
                    avail_parts.append(f"{wh}: In Stock")
                elif info == "out_of_stock":
                    avail_parts.append(f"{wh}: Out")
                elif info == "low_stock":
                    avail_parts.append(f"{wh}: Low")
                else:
                    avail_parts.append(f"{wh}: {info}")
            if avail_parts:
                self._vendor_avail_edit.setText(", ".join(avail_parts))
        self._update_vendor_avail_status({
            "vendor_availability": self._vendor_avail_edit.text(),
            "not_available": not_available,
            "in_stock": in_stock,
            "total_available": total_avail,
        })

        # ── PAI Description → Vendor Description ──
        pai_desc = result.get("description", "")
        if pai_desc:
            self._vendor_desc_edit.setText(pai_desc)
        # Also populate the product Description (title) if empty
        if pai_desc and not self._title_edit.text().strip():
            self._title_edit.setText(pai_desc.title())

        # ── Warranty → supplier warranty fields ──
        warranty = result.get("warranty", "")
        if warranty and hasattr(self, "_supplier_warranty_cb"):
            self._supplier_warranty_cb.setChecked(True)
            # Parse warranty string like "24 Months", "2 Year", "90 Days"
            import re as _re
            m = _re.search(r"(\d+)\s*(day|month|year)s?", warranty, _re.IGNORECASE)
            if m:
                val = int(m.group(1))
                unit = m.group(2).lower() + "s"  # days/months/years
                self._set_supplier_warranty(val, unit)

        # ── Product Group → Category (only if empty) ──
        product_group = result.get("product_group", "")
        if product_group and not self._category_combo.currentText().strip():
            # Map common PAI product groups to app categories
            pg_upper = product_group.upper()
            if "ENGINE" in pg_upper:
                self._category_combo.setCurrentText("Engine Parts")
            elif "BRAKE" in pg_upper:
                self._category_combo.setCurrentText("Brake")
            elif "EXHAUST" in pg_upper:
                self._category_combo.setCurrentText("Exhaust")
            else:
                self._category_combo.setCurrentText(product_group.title())

        # ── OEM number from PAI — do NOT write to OEM Part # field ──
        # PAI's "OEM" field is unreliable; leave OEM Part # for manual entry only.

        # ── High-confidence SKU override ──
        # When Grok says [high] confidence and the PAI SKU is a close variant
        # of the existing vendor SKU, override it with the canonical PAI SKU
        grok_reason = result.get("grok_reasoning", "")
        if pai_sku and "[high]" in grok_reason.lower():
            import re as _re_sku
            current_vsku = self._pai_sku_edit.text().strip()
            # Normalize: strip dashes/spaces to compare core digits
            norm_pai = _re_sku.sub(r"[\-\s]", "", pai_sku).upper()
            norm_cur = _re_sku.sub(r"[\-\s]", "", current_vsku).upper()
            if norm_pai != norm_cur and norm_cur:
                # Close match — override with PAI's canonical format
                self._pai_sku_edit.setText(pai_sku)

        # ── Weight → Shipping section ──
        weight_str = result.get("weight", "")
        if weight_str:
            import re as _re2
            wm = _re2.search(r"([\d.]+)\s*(LBS?|KG|OZ)", weight_str, _re2.IGNORECASE)
            if wm:
                try:
                    self._weight_spin.setValue(float(wm.group(1)))
                except Exception:
                    pass
                unit_raw = wm.group(2).upper()
                if unit_raw in ("LB", "LBS"):
                    self._weight_unit_combo.setCurrentText("LBS")
                elif unit_raw == "KG":
                    self._weight_unit_combo.setCurrentText("KG")
                elif unit_raw == "OZ":
                    self._weight_unit_combo.setCurrentText("OZ")

        # ── Vendor Alternates (from multi-result PAI search) ──
        all_results = result.get("all_results") or []
        if len(all_results) > 1:
            alt_skus = [
                r.get("sku", "") for r in all_results
                if r.get("sku") and r.get("sku") != pai_sku
            ]
            if alt_skus:
                existing = self._vendor_alternates_edit.text().strip()
                new_alts = ", ".join(alt_skus)
                if existing:
                    # Merge without duplicates
                    existing_set = {s.strip() for s in existing.split(",")}
                    for a in alt_skus:
                        if a not in existing_set:
                            existing += f", {a}"
                    self._vendor_alternates_edit.setText(existing)
                else:
                    self._vendor_alternates_edit.setText(new_alts)

        # Store PAI results for "Add as Product" button
        self._pai_selected_sku = pai_sku
        if all_results:
            self._pai_all_results = all_results
        else:
            self._pai_all_results = [result]
        # Show button when alternates exist
        if self._vendor_alternates_edit.text().strip():
            self._add_alt_product_btn.setVisible(True)

        # ── Download product images from PAI ──
        image_urls = list(result.get("image_urls") or [])
        if not image_urls:
            single_image = str(result.get("image_url", "") or "").strip()
            if single_image:
                image_urls = [single_image]
        images_downloaded = 0
        images_pending_save = False
        if image_urls and self._product_id:
            for img_url in image_urls:
                try:
                    self._download_and_store_image(img_url, source="pai")
                    images_downloaded += 1
                except Exception:
                    pass
            if images_downloaded:
                self._load_product_images()
                # Auto-expand the PRODUCT IMAGES section so user can see them
                try:
                    if hasattr(self, "_img_preview"):
                        box = self._img_preview.parentWidget()
                        while box is not None and not (hasattr(box, "isCheckable") and box.isCheckable()):
                            box = box.parentWidget()
                        if box is not None and not box.isChecked():
                            box.setChecked(True)
                except Exception:
                    pass
        elif image_urls and not self._product_id:
            images_pending_save = True

        self._modified = True

        # ── Summary message ──
        grok_reason = result.get("grok_reasoning", "")
        msg_parts = [
            f"PAI SKU: {pai_sku}",
            f"Your Price (Cost): ${float(cost):,.2f}" if cost else "Cost: N/A",
            f"List Price: ${float(list_price):,.2f}" if list_price else "",
        ]
        if not_available:
            msg_parts.append("Availability: ❌ N/A — Not Available")
        elif in_stock and stock:
            stock_detail = []
            for wh, info in stock.items():
                if wh.startswith("_"):
                    continue
                if isinstance(info, int) and info > 0:
                    stock_detail.append(f"{wh}: {info}")
                elif info == "in_stock":
                    stock_detail.append(f"{wh}: ✔")
                elif info == "low_stock":
                    stock_detail.append(f"{wh}: ⚠")
                elif info == "out_of_stock":
                    stock_detail.append(f"{wh}: ✖")
            avail_line = f"Availability: ✔ In Stock"
            if total_avail:
                avail_line += f" ({total_avail} total)"
            msg_parts.append(avail_line)
            if stock_detail:
                msg_parts.append("  " + ", ".join(stock_detail))
        elif in_stock:
            msg_parts.append(f"Availability: ✔ In Stock ({total_avail} total)" if total_avail else "Availability: ✔ Available")
        elif stock:
            msg_parts.append("Availability: ⚠ Available (out of stock at all warehouses)")
        else:
            msg_parts.append("Availability: Unknown")
        if warranty:
            msg_parts.append(f"Warranty: {warranty}")
        if product_group:
            msg_parts.append(f"Product Group: {product_group}")
        weight = result.get("weight", "")
        if weight:
            msg_parts.append(f"Weight: {weight}")
        if images_downloaded:
            msg_parts.append(f"Images downloaded: {images_downloaded}")
        elif images_pending_save:
            msg_parts.append("Images found: save product first, then run PAI Lookup again to import images.")
        if grok_reason:
            msg_parts.append(f"\n🤖 AI Selection: {grok_reason}")
        # Show alternatives
        all_results = result.get("all_results") or []
        if len(all_results) > 1:
            alt_skus = [r.get("sku", "") for r in all_results if r.get("sku") != pai_sku]
            if alt_skus:
                msg_parts.append(f"\nAlternatives: {', '.join(alt_skus)}")

        QMessageBox.information(
            self, "PAI Lookup Complete", "\n".join(p for p in msg_parts if p)
        )

    def _on_pai_lookup_error(self, msg: str) -> None:
        self._pai_lookup_worker = None
        self._pai_lookup_btn.setEnabled(True)
        self._pai_lookup_btn.setText("🔍 PAI Lookup")
        # Check if debug HTML was saved
        import os as _os
        debug_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
            "pai_portal_session", "last_search_debug.html",
        )
        extra = ""
        if _os.path.exists(debug_path):
            extra = f"\n\nDebug HTML saved to:\n{debug_path}"
        QMessageBox.warning(self, "PAI Lookup Failed", msg + extra)

    def _on_pai_multi_match(self, matches: list) -> None:
        """Handle multiple PAI cross-reference matches — let user pick."""
        self._pai_lookup_worker = None
        self._pai_lookup_btn.setEnabled(True)
        self._pai_lookup_btn.setText("🔍 PAI Lookup")

        searched = self._pai_sku_edit.text().strip()
        dlg = _PAIMultiResultDialog(matches, searched, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        m = dlg.selected_match()
        if not m:
            return

        # Apply selected match the same way _on_pai_lookup_done does
        pai_sku = str(m.get("sku", ""))
        if pai_sku:
            self._pai_sku_edit.setText(pai_sku)

        cost = m.get("cost")
        if cost and float(cost) > 0:
            self._pai_cost_spin.setValue(float(cost))

        url = m.get("url", "")
        if url:
            self._pai_link_edit.setText(url)

        stock = m.get("stock") or {}
        not_available = m.get("not_available", False)
        in_stock = m.get("in_stock", False)
        total_avail = m.get("total_available", 0)
        if stock:
            parts = []
            avail_parts = []
            for wh, info in stock.items():
                if isinstance(info, dict):
                    qty = info.get("qty") or info.get("quantity") or info.get("available")
                    if qty is not None:
                        parts.append(f"{wh}: {qty}")
                        avail_parts.append(f"{wh}: {qty}")
                elif isinstance(info, int):
                    parts.append(f"{wh}: {info} In Stock")
                    avail_parts.append(f"{wh}: {info}")
                elif info == "in_stock":
                    parts.append(f"{wh}: ✔ In Stock")
                    avail_parts.append(f"{wh}: In Stock")
                elif info == "out_of_stock":
                    parts.append(f"{wh}: ✖ Out")
                    avail_parts.append(f"{wh}: Out")
                else:
                    parts.append(f"{wh}: {info}")
                    avail_parts.append(f"{wh}: {info}")
            if avail_parts:
                self._vendor_avail_edit.setText(", ".join(avail_parts))
        elif not_available:
            self._vendor_avail_edit.setText("N/A — Not Available")
        self._update_vendor_avail_status({
            "vendor_availability": self._vendor_avail_edit.text(),
        })

        # Description → product title if empty
        pai_desc = m.get("description", "")
        if pai_desc and not self._title_edit.text().strip():
            self._title_edit.setText(pai_desc.title())

        # Warranty → supplier warranty fields (same as single-match path)
        warranty = m.get("warranty", "")
        if warranty and hasattr(self, "_supplier_warranty_cb"):
            self._supplier_warranty_cb.setChecked(True)
            import re as _re_w
            wm_match = _re_w.search(r"(\d+)\s*(day|month|year)s?", warranty, _re_w.IGNORECASE)
            if wm_match:
                val = int(wm_match.group(1))
                unit = wm_match.group(2).lower() + "s"  # days/months/years
                self._set_supplier_warranty(val, unit)

        # Weight
        weight_str = m.get("weight", "")
        if weight_str:
            import re as _re3
            wm = _re3.search(r"([\d.]+)\s*(LBS?|KG|OZ)", weight_str, _re3.IGNORECASE)
            if wm:
                try:
                    self._weight_spin.setValue(float(wm.group(1)))
                except Exception:
                    pass
                unit_raw = wm.group(2).upper()
                if unit_raw in ("LB", "LBS"):
                    self._weight_unit_combo.setCurrentText("LBS")
                elif unit_raw == "KG":
                    self._weight_unit_combo.setCurrentText("KG")

        # Vendor alternates from all matches
        all_match_skus = [
            r.get("sku", "") for r in matches
            if r.get("sku") and r.get("sku") != pai_sku
        ]
        if all_match_skus:
            self._vendor_alternates_edit.setText(", ".join(all_match_skus))

        # Store all PAI results for "Add as Product" button
        self._pai_selected_sku = pai_sku
        self._pai_all_results = matches
        if self._vendor_alternates_edit.text().strip():
            self._add_alt_product_btn.setVisible(True)

        # Cost date
        from datetime import datetime as _dt2
        self._pai_cost_date = _dt2.now().isoformat()

        # Download images
        image_urls = list(m.get("image_urls") or [])
        if not image_urls:
            single_image = str(m.get("image_url", "") or "").strip()
            if single_image:
                image_urls = [single_image]
        images_downloaded = 0
        images_pending_save = False
        if image_urls and self._product_id:
            for img_url in image_urls:
                try:
                    self._download_and_store_image(img_url, source="pai")
                    images_downloaded += 1
                except Exception:
                    pass
            if images_downloaded:
                self._load_product_images()
                try:
                    if hasattr(self, "_img_preview"):
                        box = self._img_preview.parentWidget()
                        while box is not None and not (hasattr(box, "isCheckable") and box.isCheckable()):
                            box = box.parentWidget()
                        if box is not None and not box.isChecked():
                            box.setChecked(True)
                except Exception:
                    pass
        elif image_urls and not self._product_id:
            images_pending_save = True

        self._modified = True
        in_stock = m.get("in_stock", False)
        msg = f"Applied PAI SKU: {pai_sku}\n"
        if cost:
            msg += f"Cost: ${cost:,.2f}\n"
        msg += f"In Stock: {'Yes' if in_stock else 'No'}\n"
        if images_pending_save:
            msg += "Images: Found (save product first, then run PAI Lookup again)"
        else:
            msg += f"Images: {images_downloaded}"
        QMessageBox.information(self, "PAI Match Selected", msg)

    # ==================================================================
    # AUTO-SKU
    # ==================================================================
    def _on_apply_inventory_adjustment(self) -> None:
        """Set exact on-hand qty from Inventory tab and record an adjustment transaction."""
        if not self._product_id:
            QMessageBox.information(
                self,
                "Save Product First",
                "Save this product before applying inventory adjustments.",
            )
            return

        sku = self._sku_edit.text().strip() if hasattr(self, "_sku_edit") else ""
        if not sku:
            QMessageBox.warning(self, "Inventory", "SKU is missing; cannot adjust inventory.")
            return

        try:
            fresh = get_product_by_id(int(self._product_id)) or {}
            current_qty = int(fresh.get("qty_on_hand", 0) or 0)
        except Exception:
            current_qty = int(getattr(self._product, "qty_on_hand", 0) or 0) if isinstance(self._product, dict) else 0

        target_qty = int(self._inv_set_qoh_spin.value())
        delta = target_qty - current_qty
        effective_date = self._inv_effective_date.date().toString("yyyy-MM-dd")

        if delta == 0:
            QMessageBox.information(self, "Inventory", "On-hand quantity is already set to that value.")
            return

        result = adjust_product_quantity(
            sku=sku,
            delta=delta,
            txn_type="adjust",
            reference=f"Edit Product ({effective_date})",
            notes=f"Set on-hand to {target_qty} from Edit Product Inventory tab",
            user="ui.edit_product",
            txn_datetime=f"{effective_date}T00:00:00",
        )
        if not result.get("success"):
            QMessageBox.warning(self, "Inventory", result.get("error", "Inventory adjustment failed."))
            return

        qty_after = int(result.get("qty_after", target_qty) or target_qty)
        self._qoh_label.setText(f"{qty_after:,}")
        self._inv_set_qoh_spin.setValue(qty_after)
        if isinstance(self._product, dict):
            self._product["qty_on_hand"] = qty_after

        QMessageBox.information(
            self,
            "Inventory Updated",
            f"On-hand updated for {sku}: {current_qty} -> {qty_after}\n"
            f"Effective date: {effective_date}",
        )

    def _on_auto_sku(self) -> None:
        """Generate a SKU from vendor code + vendor SKU."""
        if self._sku_manually_edited:
            reply = QMessageBox.question(
                self, "Overwrite SKU?",
                "You've manually edited the SKU. Overwrite with auto-generated SKU?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        is_private_label = (
            hasattr(self, "_private_label_cb") and self._private_label_cb.isChecked()
        )
        vendor_id = self._supplier_combo.currentData()
        if not is_private_label and not vendor_id:
            QMessageBox.warning(
                self, "Vendor Required",
                "Please select a vendor first so the correct SKU prefix can be used."
            )
            return
        new_sku = self._build_auto_sku()
        if new_sku:
            self._sku_manually_edited = False
            self._sku_edit.setText(new_sku)
        else:
            if is_private_label:
                QMessageBox.information(
                    self, "Part Number Required",
                    "Enter a Private Label Part # (or Vendor SKU/OEM Part #) first so the SKU can be generated as\n"
                    "JAKS-{PART_NUMBER}."
                )
                return

            vendor_sku = self._pai_sku_edit.text().strip()
            if not vendor_sku:
                QMessageBox.information(
                    self, "Vendor SKU Required",
                    "Enter a Vendor SKU first so the SKU can be generated as\n"
                    "JAKS-{VENDOR_CODE}-{VENDOR_SKU}."
                )
            else:
                # Fallback to old prefix-based generation
                try:
                    prefix_row = get_sku_prefix_for_vendor(vendor_id)
                    if not prefix_row:
                        vendor_name = self._supplier_combo.currentText()
                        QMessageBox.information(
                            self, "No Prefix",
                            f"No SKU prefix or vendor code configured for '{vendor_name}'.\n\n"
                            "Set a vendor code in Settings → Vendors,\n"
                            "or set a SKU prefix in Settings → SKU Prefixes."
                        )
                        return
                    prefix = prefix_row["prefix"]
                    result = generate_next_sku(prefix)
                    if result.get("success"):
                        self._sku_manually_edited = False
                        self._sku_edit.setText(result["sku"])
                    else:
                        QMessageBox.warning(self, "Error", result.get("error", "Failed to generate SKU"))
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Auto-SKU failed: {e}")

    # ==================================================================
    # SKU UNLOCK
    # ==================================================================
    def _on_unlock_sku(self) -> None:
        """Allow editing the SKU field (edit mode only)."""
        reply = QMessageBox.question(
            self, "Unlock SKU?",
            "Changing the SKU can break Shopify sync and cross-references.\n\n"
            "Are you sure you want to edit it?",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sku_edit.setReadOnly(False)
            self._sku_edit.setStyleSheet("")
            self._sku_edit.setFocus()
            self._sku_unlock_btn.setEnabled(False)

    # ==================================================================
    # AI BRIEF DESCRIPTION (Grok)
    # ==================================================================
    def _on_title_changed_for_ai(self, text: str) -> None:
        """If Auto checkbox is on, trigger AI enrichment after title changes."""
        if self._ai_auto_cb.isChecked() and text.strip():
            self._on_ai_enrich()

    def _on_ai_generate_description(self) -> None:
        """Generate button clicked — if Auto is on, do full enrich; otherwise just description."""
        if self._ai_auto_cb.isChecked():
            self._on_ai_enrich()
        else:
            self._on_ai_desc_only()

    def _on_ai_desc_only(self) -> None:
        """Generate a brief product description only using Grok AI."""
        import os
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(
                self, "No API Key",
                "Set XAI_API_KEY in your .env file to use AI description generation."
            )
            return

        title = self._title_edit.text().strip()
        category = self._category_combo.currentText().strip()
        oem_pn = self._oem_pn_edit.text().strip()
        eng_mfr = self._engine_mfr_combo.currentText().strip()
        eng_model = self._engine_model_combo.currentText().strip()

        if not title and not oem_pn:
            QMessageBox.information(self, "Missing Info", "Enter a Description or OEM Part # first.")
            return

        self._ai_desc_btn.setEnabled(False)
        self._ai_desc_btn.setText("⏳ ...")

        self._ai_worker = _AIDescriptionWorker(
            api_key, title, category, oem_pn, eng_mfr, eng_model, self
        )
        self._ai_worker.finished.connect(self._on_ai_desc_result)
        self._ai_worker.error.connect(self._on_ai_desc_error)
        self._ai_worker.start()

    def _on_ai_enrich(self) -> None:
        """Full AI enrichment — fills description, category, subcategory, engine fields."""
        import os
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(
                self, "No API Key",
                "Set XAI_API_KEY in your .env file to use AI."
            )
            return

        title = self._title_edit.text().strip()
        oem_pn = self._oem_pn_edit.text().strip()

        if not title and not oem_pn:
            QMessageBox.information(self, "Missing Info", "Enter a Description or OEM Part # first.")
            return

        self._ai_desc_btn.setEnabled(False)
        self._ai_desc_btn.setText("⏳ AI...")

        self._ai_enrich_worker = _AIEnrichWorker(
            api_key, title, oem_pn,
            self._all_categories, self._all_subcategories, self
        )
        self._ai_enrich_worker.finished.connect(self._on_ai_enrich_result)
        self._ai_enrich_worker.error.connect(self._on_ai_desc_error)
        self._ai_enrich_worker.start()

    def _on_ai_enrich_result(self, data: dict) -> None:
        """Apply AI enrichment results to form fields (only fill empty fields)."""
        self._ai_desc_btn.setEnabled(True)
        self._ai_desc_btn.setText("🤖 Generate")

        # Brief description — always update (that's what the button is for)
        brief = data.get("brief_description", "")
        if brief:
            self._title_edit.setText(brief)

        # Category — only fill if currently empty
        cat = data.get("category", "")
        if cat and not self._category_combo.currentText().strip():
            idx = self._category_combo.findText(cat, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._category_combo.setCurrentIndex(idx)
            else:
                self._category_combo.setCurrentText(cat)

        # Subcategory — only fill if currently empty
        subcat = data.get("subcategory", "")
        if subcat and not self._subcategory_combo.currentText().strip():
            idx = self._subcategory_combo.findText(subcat, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._subcategory_combo.setCurrentIndex(idx)
            else:
                self._subcategory_combo.setCurrentText(subcat)

        # Engine manufacturer — only fill if currently empty
        eng_mfr = data.get("engine_manufacturer", "")
        if eng_mfr and not self._engine_mfr_combo.currentText().strip():
            idx = self._engine_mfr_combo.findText(eng_mfr, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._engine_mfr_combo.setCurrentIndex(idx)
            else:
                self._engine_mfr_combo.setCurrentText(eng_mfr)

        # Engine model — only fill if currently empty
        eng_model = data.get("engine_model", "")
        if eng_model and not self._engine_model_combo.currentText().strip():
            idx = self._engine_model_combo.findText(eng_model, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self._engine_model_combo.setCurrentIndex(idx)
            else:
                self._engine_model_combo.setCurrentText(eng_model)

    def _on_ai_desc_result(self, description: str) -> None:
        self._title_edit.setText(description)
        self._ai_desc_btn.setEnabled(True)
        self._ai_desc_btn.setText("🤖 Generate")

    def _on_ai_desc_error(self, err: str) -> None:
        self._ai_desc_btn.setEnabled(True)
        self._ai_desc_btn.setText("🤖 Generate")
        QMessageBox.warning(self, "AI Error", f"Failed to generate description:\n{err}")

    # ==================================================================
    # OEM PART NUMBER LOOKUP (AI)
    # ==================================================================
    def _on_oem_lookup(self) -> None:
        """Use AI to identify what an OEM part number is."""
        import os
        oem_pn = self._oem_pn_edit.text().strip()
        if not oem_pn:
            QMessageBox.information(self, "No Part #", "Enter an OEM part number first.")
            return
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Set XAI_API_KEY in .env.")
            return

        self._oem_lookup_btn.setEnabled(False)
        self._oem_lookup_btn.setText("⏳ ...")

        title = self._title_edit.text().strip()
        self._oem_lookup_worker = _AIOemLookupWorker(api_key, oem_pn, title, self)
        self._oem_lookup_worker.finished.connect(self._on_oem_lookup_result)
        self._oem_lookup_worker.error.connect(self._on_oem_lookup_error)
        self._oem_lookup_worker.start()

    def _on_oem_lookup_result(self, data: dict) -> None:
        self._oem_lookup_btn.setEnabled(True)
        self._oem_lookup_btn.setText("🔍 Lookup")

        info_parts = []
        desc = data.get("description", "")
        eng = data.get("engine_manufacturer", "")
        model = data.get("engine_model", "")
        cat = data.get("category", "")
        if desc:
            info_parts.append(f"Part: {desc}")
        if eng:
            info_parts.append(f"Engine: {eng}")
        if model:
            info_parts.append(f"Model: {model}")
        if cat:
            info_parts.append(f"Category: {cat}")

        msg = "\n".join(info_parts) if info_parts else "Could not identify this part number."

        reply = QMessageBox.question(
            self, "OEM Lookup Result",
            f"{msg}\n\nApply these to empty fields?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if desc and not self._title_edit.text().strip():
                self._title_edit.setText(desc)
            if eng and not self._engine_mfr_combo.currentText().strip():
                idx = self._engine_mfr_combo.findText(eng, Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    self._engine_mfr_combo.setCurrentIndex(idx)
                else:
                    self._engine_mfr_combo.setCurrentText(eng)
            if model and not self._engine_model_combo.currentText().strip():
                idx = self._engine_model_combo.findText(model, Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    self._engine_model_combo.setCurrentIndex(idx)
                else:
                    self._engine_model_combo.setCurrentText(model)
            if cat and not self._category_combo.currentText().strip():
                idx = self._category_combo.findText(cat, Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    self._category_combo.setCurrentIndex(idx)
                else:
                    self._category_combo.setCurrentText(cat)

    def _on_oem_lookup_error(self, err: str) -> None:
        self._oem_lookup_btn.setEnabled(True)
        self._oem_lookup_btn.setText("🔍 Lookup")
        QMessageBox.warning(self, "Lookup Error", f"OEM lookup failed:\n{err}")

    # ==================================================================
    # AI PRICE SUGGESTION
    # ==================================================================
    def _on_ai_suggest_price(self) -> None:
        """Analyze competitor prices and suggest optimal selling price."""
        import os
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Set XAI_API_KEY in .env.")
            return

        # Gather competitor prices
        comp_prices = {}
        for abbr, label in self._comp_labels.items():
            text = label.text().strip()
            if text and text != "—":
                # Extract price from formatted text like "$1,234.56 (In Stock)"
                import re
                m = re.search(r"\$([0-9,]+(?:\.\d{2})?)", text)
                if m:
                    comp_prices[abbr] = float(m.group(1).replace(",", ""))

        cost = self._cost_spin.value()
        pai_cost = self._pai_cost_spin.value()
        current_sell = self._selling_price_spin.value()
        title = self._title_edit.text().strip()

        if not comp_prices and cost == 0:
            QMessageBox.information(
                self, "Insufficient Data",
                "No competitor prices or cost data available.\n"
                "Run Check Pricing first, or set the cost."
            )
            return

        self._ai_price_btn.setEnabled(False)
        self._ai_price_btn.setText("⏳ ...")

        self._ai_price_worker = _AIPriceSuggestionWorker(
            api_key, title, comp_prices, cost or pai_cost, current_sell, self
        )
        self._ai_price_worker.finished.connect(self._on_ai_price_result)
        self._ai_price_worker.error.connect(self._on_ai_price_error)
        self._ai_price_worker.start()

    def _on_ai_price_result(self, data: dict) -> None:
        self._ai_price_btn.setEnabled(True)
        self._ai_price_btn.setText("🤖 Suggest Price")

        suggested = data.get("suggested_price", 0)
        reasoning = data.get("reasoning", "")
        margin_pct = data.get("margin_percent", 0)

        msg = f"Suggested Sell Price: ${suggested:,.2f}"
        if margin_pct:
            msg += f"\nEstimated Margin: {margin_pct:.0f}%"
        if reasoning:
            msg += f"\n\nReasoning: {reasoning}"

        reply = QMessageBox.question(
            self, "AI Price Suggestion",
            f"{msg}\n\nApply this price?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and suggested > 0:
            self._selling_price_spin.setValue(suggested)

    def _on_ai_price_error(self, err: str) -> None:
        self._ai_price_btn.setEnabled(True)
        self._ai_price_btn.setText("🤖 Suggest Price")
        QMessageBox.warning(self, "Price Error", f"AI price suggestion failed:\n{err}")

    # ==================================================================
    # AI SHOPIFY TAGS
    # ==================================================================
    def _on_ai_generate_tags(self) -> None:
        """Generate Shopify tags from product info."""
        import os
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Set XAI_API_KEY in .env.")
            return
        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.information(self, "Missing Info", "Enter a Description first.")
            return

        self._ai_tags_btn.setEnabled(False)
        self._ai_tags_btn.setText("⏳")

        context = {
            "title": title,
            "category": self._category_combo.currentText().strip(),
            "subcategory": self._subcategory_combo.currentText().strip(),
            "engine_mfr": self._engine_mfr_combo.currentText().strip(),
            "engine_model": self._engine_model_combo.currentText().strip(),
            "brief_desc": self._title_edit.text().strip(),
            "oem_pn": self._oem_pn_edit.text().strip(),
            "condition": self._condition_combo.currentText().strip(),
        }

        self._ai_tags_worker = _AITagsWorker(api_key, context, self)
        self._ai_tags_worker.finished.connect(self._on_ai_tags_result)
        self._ai_tags_worker.error.connect(self._on_ai_tags_error)
        self._ai_tags_worker.start()

    def _on_ai_tags_result(self, tags: str) -> None:
        self._ai_tags_btn.setEnabled(True)
        self._ai_tags_btn.setText("🤖 Tags")
        existing = self._tags_edit.text().strip()
        if existing:
            # Merge, dedup
            all_tags = [t.strip() for t in (existing + ", " + tags).split(",") if t.strip()]
            seen = set()
            unique = []
            for t in all_tags:
                key = t.lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(t)
            self._tags_edit.setText(", ".join(unique))
        else:
            self._tags_edit.setText(tags)

    def _on_ai_tags_error(self, err: str) -> None:
        self._ai_tags_btn.setEnabled(True)
        self._ai_tags_btn.setText("🤖 Tags")
        QMessageBox.warning(self, "Tags Error", f"AI tag generation failed:\n{err}")

    # ==================================================================
    # AI SHOPIFY DESCRIPTION
    # ==================================================================
    def _on_ai_generate_shopify_desc(self) -> None:
        """Generate full HTML product description for Shopify."""
        import os
        api_key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Set XAI_API_KEY in .env.")
            return
        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.information(self, "Missing Info", "Enter a Description first.")
            return

        self._ai_shopify_desc_btn.setEnabled(False)
        self._ai_shopify_desc_btn.setText("⏳ Generating...")

        context = {
            "title": title,
            "category": self._category_combo.currentText().strip(),
            "subcategory": self._subcategory_combo.currentText().strip(),
            "engine_mfr": self._engine_mfr_combo.currentText().strip(),
            "engine_model": self._engine_model_combo.currentText().strip(),
            "brief_desc": self._title_edit.text().strip(),
            "oem_pn": self._oem_pn_edit.text().strip(),
            "condition": self._condition_combo.currentText().strip(),
            "manufacturer": self._manufacturer_combo.currentText().strip(),
        }

        self._ai_desc_html_worker = _AIShopifyDescWorker(api_key, context, self)
        self._ai_desc_html_worker.finished.connect(self._on_ai_shopify_desc_result)
        self._ai_desc_html_worker.error.connect(self._on_ai_shopify_desc_error)
        self._ai_desc_html_worker.start()

    def _on_ai_shopify_desc_result(self, html: str) -> None:
        self._ai_shopify_desc_btn.setEnabled(True)
        self._ai_shopify_desc_btn.setText("🤖 Generate Description")
        self._desc_html_edit.setPlainText(html)

    def _on_ai_shopify_desc_error(self, err: str) -> None:
        self._ai_shopify_desc_btn.setEnabled(True)
        self._ai_shopify_desc_btn.setText("🤖 Generate Description")
        QMessageBox.warning(self, "Description Error", f"AI description generation failed:\n{err}")

    # ==================================================================
    # PRICING — DUAL EDIT (Price ↔ Margin)
    # ==================================================================
    def _on_price_changed(self) -> None:
        """Selling price or cost changed — update margin display and spin."""
        if self._updating_price:
            return
        self._update_margin()
        self._sync_margin_spin()
        self._update_pricing_summary()

    def _sync_margin_spin(self) -> None:
        """Sync the margin spin box to current sell/cost values."""
        sell = self._selling_price_spin.value()
        cost = self._cost_spin.value()
        self._margin_spin.blockSignals(True)
        if sell > 0 and cost > 0:
            pct = ((sell - cost) / sell) * 100
            self._margin_spin.setValue(round(pct, 1))
        else:
            self._margin_spin.setValue(0)
        self._margin_spin.blockSignals(False)

    def _on_margin_edited(self, margin_pct: float) -> None:
        """User edited the margin% spin — recalculate selling price from cost."""
        cost = self._cost_spin.value()
        if cost <= 0 or margin_pct >= 100:
            return
        self._updating_price = True
        new_sell = cost / (1 - margin_pct / 100)
        self._selling_price_spin.setValue(round(new_sell, 2))
        self._updating_price = False
        self._update_margin()
        self._update_pricing_summary()
        self._on_field_changed()

    def _update_margin(self) -> None:
        """Update margin display label."""
        sell = self._selling_price_spin.value()
        cost = self._cost_spin.value()
        if sell > 0 and cost > 0:
            margin_dollars = sell - cost
            margin_pct = (margin_dollars / sell) * 100
            color = "#228B22" if margin_dollars > 0 else "#dc3545"
            self._margin_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 9pt;")
            self._margin_label.setText(f"  Margin: ${margin_dollars:,.2f} ({margin_pct:.1f}%)")
        elif sell > 0:
            self._margin_label.setText("  Margin: N/A (no cost)")
            self._margin_label.setStyleSheet("color: #888; font-size: 9pt;")
        else:
            self._margin_label.setText("")

    def _recalc_landed_cost(self) -> None:
        """Auto-calculate landed cost = vendor cost + shipping."""
        vendor_cost = self._pai_cost_spin.value()
        shipping = self._shipping_cost_spin.value()
        if vendor_cost > 0:
            self._cost_spin.setValue(round(vendor_cost + shipping, 2))
        # Cost just changed — re-derive selling price from the vendor's
        # category band (subject to override flags).
        self._apply_vendor_auto_price()

    # ------------------------------------------------------------------
    # Vendor-driven auto pricing (Phase 1)
    # ------------------------------------------------------------------
    def _resolve_vendor_category(self) -> dict | None:
        """Return the price category dict for the currently selected vendor.

        Order: explicit Price Category combo override → vendor mapping →
        manufacturer mapping (legacy) → default category. Returns None if
        nothing is configured.
        """
        try:
            from db import (
                get_price_category,
                get_vendor_category,
                get_manufacturer_category,
                get_default_price_category,
            )
        except Exception:
            return None

        # 1) Explicit override on the product/category combo
        try:
            override = self._price_category_combo.currentData()
        except Exception:
            override = None
        if override:
            cat = get_price_category(int(override))
            if cat:
                return cat

        # 2) Vendor mapping
        try:
            vid = self._supplier_combo.currentData()
        except Exception:
            vid = None
        if vid:
            cid = get_vendor_category(int(vid))
            if cid:
                cat = get_price_category(int(cid))
                if cat:
                    return cat

        # 3) Legacy manufacturer mapping
        try:
            mfr = self._mfr_combo.currentText().strip() if hasattr(self, "_mfr_combo") else ""
        except Exception:
            mfr = ""
        if mfr:
            cid = get_manufacturer_category(mfr)
            if cid:
                cat = get_price_category(int(cid))
                if cat:
                    return cat

        # 4) Default
        return get_default_price_category()

    def _apply_vendor_auto_price(self, *, force: bool = False) -> None:
        """Recompute Selling Price from the resolved category × cost band.

        Skips silently when:
          - the user has hand-edited Selling Price (unless force=True),
          - cost is zero,
          - no category band matches the cost.
        Always refreshes the "Resolved category" status label.
        """
        if self._auto_pricing_active:
            return
        if not hasattr(self, "_selling_price_spin") or not hasattr(self, "_cost_spin"):
            return

        category = self._resolve_vendor_category()
        # Refresh the category combo so it shows the resolved category
        # whenever the user has not overridden it.
        if category and not self._price_category_user_override and hasattr(self, "_price_category_combo"):
            try:
                ix = self._price_category_combo.findData(int(category["id"]))
                if ix >= 0 and self._price_category_combo.currentIndex() != ix:
                    self._auto_pricing_active = True
                    self._price_category_combo.blockSignals(True)
                    self._price_category_combo.setCurrentIndex(ix)
                    self._price_category_combo.blockSignals(False)
                    self._auto_pricing_active = False
            except Exception:
                self._auto_pricing_active = False

        # Update the resolved-category status label
        self._update_resolved_category_label(category)

        if self._selling_price_user_override and not force:
            return
        if not category:
            return

        cost = float(self._cost_spin.value() or 0) or float(self._pai_cost_spin.value() or 0)
        if cost <= 0:
            return

        try:
            from db import find_band_for_cost
        except Exception:
            return
        try:
            band = find_band_for_cost(int(category["id"]), cost)
        except Exception:
            band = None
        if not band:
            return
        margin_pct = float(band.get("margin_pct") or 0)
        if margin_pct >= 100:
            return
        # Gross-margin formula: price = cost / (1 - m). Matches the
        # 'Target Margin' label so a 40% band displays as 40%.
        new_price = round(cost / (1 - margin_pct / 100.0), 2)
        if abs(new_price - float(self._selling_price_spin.value() or 0)) < 0.005:
            return

        self._auto_pricing_active = True
        self._updating_price = True
        self._selling_price_spin.blockSignals(True)
        self._selling_price_spin.setValue(new_price)
        self._selling_price_spin.blockSignals(False)
        self._updating_price = False
        self._auto_pricing_active = False
        self._update_margin()
        self._sync_margin_spin()
        self._update_pricing_summary()

    def _update_resolved_category_label(self, category: dict | None = None) -> None:
        """Refresh the small status label next to the Price Category combo."""
        if not hasattr(self, "_resolved_cat_label"):
            return
        if category is None:
            category = self._resolve_vendor_category()
        if not category:
            self._resolved_cat_label.setText("Resolved: (none)")
            self._resolved_cat_label.setStyleSheet("color:#888; font-size:8pt; font-style:italic;")
            return
        # Determine source
        try:
            override = self._price_category_combo.currentData()
        except Exception:
            override = None
        try:
            vid = self._supplier_combo.currentData()
        except Exception:
            vid = None
        if override and not self._price_category_user_override:
            # Combo holds the auto-resolved value (vendor / mfr / default)
            src = "vendor" if vid else "default"
        elif self._price_category_user_override and override:
            src = "manual override"
        elif vid:
            src = "vendor"
        else:
            src = "default"
        code = category.get("code", "")
        self._resolved_cat_label.setText(f"Resolved: {code} ({src})")
        self._resolved_cat_label.setStyleSheet("color:#2e7d32; font-size:8pt;")

    def _on_vendor_changed_pricing(self) -> None:
        """Slot fired when supplier combo changes — auto-fill pricing."""
        self._apply_vendor_auto_price()

    def _on_price_category_user_changed(self, _index: int) -> None:
        """User picked a Price Category by hand — mark override and reapply."""
        if self._auto_pricing_active:
            return
        self._price_category_user_override = True
        # Re-apply pricing using the newly-chosen category, but respect any
        # selling-price override the user has already set.
        self._apply_vendor_auto_price()

    def _on_selling_price_user_edited(self, _value: float) -> None:
        """Mark Selling Price as user-overridden (unless we're driving it)."""
        if self._auto_pricing_active or self._updating_price:
            return
        self._selling_price_user_override = True
        self._update_resolved_category_label()

    def _on_reset_to_vendor_price(self) -> None:
        """User clicked 'Reset to vendor price' — clear override and reapply."""
        self._selling_price_user_override = False
        # Clearing the category override also makes sense when the user
        # explicitly asks to follow the vendor again.
        self._price_category_user_override = False
        # Reset the combo to the vendor-resolved value
        self._auto_pricing_active = True
        self._price_category_combo.blockSignals(True)
        self._price_category_combo.setCurrentIndex(0)  # "— inherit from vendor —"
        self._price_category_combo.blockSignals(False)
        self._auto_pricing_active = False
        self._apply_vendor_auto_price(force=True)

    def _update_core_margin(self) -> None:
        """Update core margin label."""
        core_cost = self._core_charge_spin.value()
        core_sell = self._core_sell_spin.value()
        if core_sell > 0 and core_cost > 0:
            margin = core_sell - core_cost
            self._core_margin_label.setText(f"  Margin: ${margin:,.2f}")
        else:
            self._core_margin_label.setText("")

    def _on_has_core_toggled(self, checked: bool) -> None:
        """Expand/collapse the core details panel; clear values when disabling."""
        if hasattr(self, "_core_details_widget"):
            self._core_details_widget.setVisible(checked)
        if not checked:
            self._core_charge_spin.setValue(0.0)
            self._core_sell_spin.setValue(0.0)
        self._refresh_core_badge()
        # Refresh invoice preview line for immediate feedback
        try:
            self._update_invoice_preview()
        except Exception:
            pass

    def _refresh_core_badge(self) -> None:
        """Show/hide the header CORE badge based on current values."""
        if not hasattr(self, "_core_badge_label"):
            return
        cost = self._core_charge_spin.value()
        sell = self._core_sell_spin.value()
        if cost > 0 or sell > 0:
            amt = sell if sell > 0 else cost
            self._core_badge_label.setText(f"🔄 CORE  ·  ${amt:,.2f}")
            self._core_badge_label.show()
        else:
            self._core_badge_label.hide()

    # ==================================================================
    # TIER PRICING
    # ==================================================================
    def _on_view_tier_pricing(self) -> None:
        """Preview effective category pricing bands and tier discounts for this product."""
        try:
            from db import (
                list_bands,
                list_customer_tiers,
                get_tier_category_discount,
                resolve_product_category,
                get_price_category,
            )

            selected_category_id = self._price_category_combo.currentData()
            selected_category = (
                get_price_category(int(selected_category_id))
                if selected_category_id
                else None
            )

            effective_category = None
            if self._product_id:
                effective_category = resolve_product_category(self._product_id)
            category = selected_category or effective_category

            if not category:
                QMessageBox.information(
                    self,
                    "Tier Pricing",
                    "No price category is assigned yet. Choose a Price Category and save.",
                )
                return

            cost_basis = float(self._cost_spin.value() or self._pai_cost_spin.value() or 0)
            bands = list_bands(int(category["id"]))

            lines: list[str] = []
            lines.append(f"SKU: {self._sku_edit.text().strip() or '(new product)'}")
            lines.append(f"Category: {category.get('code', '')} - {category.get('name', '')}")

            if selected_category and effective_category and selected_category.get("id") != effective_category.get("id"):
                lines.append(
                    f"Pending change: will move from {effective_category.get('code', '')} to {selected_category.get('code', '')} on save."
                )

            if cost_basis > 0 and bands:
                matched_band = None
                for band in bands:
                    cmin = float(band.get("cost_min") or 0)
                    cmax = band.get("cost_max")
                    cmax_val = float(cmax) if cmax is not None else None
                    if cost_basis >= cmin and (cmax_val is None or cost_basis < cmax_val):
                        matched_band = band
                if matched_band is not None:
                    margin_pct = float(matched_band.get("margin_pct") or 0)
                    if margin_pct >= 100:
                        list_price = 0.0
                    else:
                        list_price = round(cost_basis / (1 - margin_pct / 100.0), 2)
                    cmax = matched_band.get("cost_max")
                    band_label = (
                        f"${float(matched_band.get('cost_min') or 0):,.2f}+"
                        if cmax is None
                        else f"${float(matched_band.get('cost_min') or 0):,.2f}-${float(cmax):,.2f}"
                    )
                    lines.append("")
                    lines.append(f"Cost basis: ${cost_basis:,.2f}")
                    lines.append(f"Matched band: {band_label} @ {margin_pct:.1f}% margin")
                    lines.append(f"List price: ${list_price:,.2f}")

                    tiers = list_customer_tiers(active_only=True)
                    if tiers:
                        lines.append("")
                        lines.append("Tier pricing preview:")
                        for tier in tiers:
                            discount_pct = float(
                                get_tier_category_discount(int(tier["id"]), int(category["id"]))
                            )
                            net = round(list_price * (1 - discount_pct / 100.0), 2)
                            lines.append(
                                f"  • {tier.get('name', 'Tier')}: ${net:,.2f} ({discount_pct:.1f}% off)"
                            )
                    else:
                        lines.append("")
                        lines.append("No active customer tiers found.")
                else:
                    lines.append("")
                    lines.append(
                        "No pricing band matched this cost. Update Price Categories > Bands."
                    )
            else:
                lines.append("")
                lines.append(
                    "Enter cost/PAI cost to preview tier prices for this category."
                )

            lines.append("")
            lines.append("Manage categories, bands, and tier matrix in Price Categories / Price Tiers screens.")
            QMessageBox.information(self, "Tier Pricing", "\n".join(lines))
        except Exception:
            QMessageBox.information(
                self,
                "Tier Pricing",
                "Tier pricing preview is currently unavailable. Open Price Tiers to configure pricing.",
            )

    # ==================================================================
    # INVOICE DESCRIPTION — ENGINE / TRUCK / CATEGORY
    # ==================================================================
    def _build_invoice_description(self) -> str:
        """Build invoice description from component fields.

        Format: [Subcategory|Category] Abbrev (Full) - Engine Model - Brief Desc - Supplier (code) - PartNumber
        """
        category = self._category_combo.currentText().strip()
        subcategory = self._subcategory_combo.currentText().strip()
        eng_mfr = self._engine_mfr_combo.currentText().strip()
        eng_model = self._engine_model_combo.currentText().strip()
        truck_mfr = self._truck_mfr_combo.currentText().strip()
        truck_model = self._truck_model_edit.currentText().strip()
        truck_system = self._truck_system_combo.currentText().strip()
        brief = self._title_edit.text().strip()
        oem_pn = self._oem_pn_edit.text().strip()

        # Supplier name and code
        supplier_name = ""
        supplier_code = ""
        idx = self._supplier_combo.currentIndex()
        if idx > 0:
            supplier_name = self._supplier_combo.currentText().strip()
            vendor_id = self._supplier_combo.currentData()
            if vendor_id and self._vendors:
                for v in self._vendors:
                    if v.get("id") == vendor_id:
                        supplier_code = v.get("code", "") or ""
                        break

        # Prefix: prefer subcategory, fall back to category
        prefix = subcategory or category

        parts: list[str] = []

        # Engine manufacturer abbreviated (abbreviation only)
        if eng_mfr:
            abbrev = _ENGINE_MFR_ABBREV.get(eng_mfr, "")
            parts.append(abbrev if abbrev else eng_mfr)
        elif truck_mfr:
            parts.append(truck_mfr)

        # Engine model or truck model
        if eng_model:
            parts.append(eng_model)
        elif truck_model:
            parts.append(truck_model)

        if truck_system and (truck_mfr or truck_model):
            parts.append(truck_system)

        if brief:
            parts.append(brief)

        # Supplier — private label shows "(JAKS)", otherwise code in parentheses
        is_private_label = (
            hasattr(self, "_private_label_cb") and self._private_label_cb.isChecked()
        )
        if is_private_label:
            parts.append("(JAKS)")
        elif supplier_name:
            if supplier_code:
                parts.append(f"({supplier_code})")
            else:
                parts.append(supplier_name)

        if oem_pn:
            parts.append(oem_pn)

        desc = " - ".join(parts)

        if prefix and desc:
            return f"[{prefix}] {desc}"
        return desc or prefix

    def _update_invoice_preview(self, *_args) -> None:
        """Update the invoice line preview label with warranty if applicable."""
        desc = self._build_invoice_description()
        if not desc:
            self._invoice_preview.setText(
                '<span style="color: #999; font-style: italic;">'
                'Fill in fields above to see invoice description</span>'
            )
            return

        # Build warranty line if enabled. Supplier coverage is the included
        # standard warranty; JAKS coverage remains the extended-warranty upsell.
        warranty_parts = []
        if hasattr(self, "_supplier_warranty_cb") and self._supplier_warranty_cb.isChecked():
            sw_val = self._sw_value_spin.value()
            sw_unit = self._sw_unit_combo.currentText()
            if sw_val > 0:
                try:
                    from engine.warranty import format_standard_warranty, supplier_standard_warranty_for_product
                    std_months, _, _ = supplier_standard_warranty_for_product({
                        "supplier_warranty_enabled": 1,
                        "supplier_warranty_value": sw_val,
                        "supplier_warranty_unit": sw_unit,
                    })
                    warranty_parts.append(format_standard_warranty(std_months))
                except Exception:
                    warranty_parts.append(f"{sw_val} {sw_unit} Standard Warranty")
        if hasattr(self, "_jaks_warranty_cb") and self._jaks_warranty_cb.isChecked():
            jw_val = self._jw_value_spin.value()
            jw_unit = self._jw_unit_combo.currentText()
            jw_pct = self._jw_pct_spin.value()
            if jw_val > 0:
                w_text = "Extended Warranty Option"
                if jw_pct > 0:
                    w_text += f" ({jw_pct:g}% / yr)"
                warranty_parts.append(w_text)

        if warranty_parts:
            warranty_line = f'<br><span style="font-size: 8pt; color: #2e7d32; font-weight: bold;">🛡️ {" + ".join(warranty_parts)}</span>'
            self._invoice_preview.setText(f'{desc}{warranty_line}')
        else:
            self._invoice_preview.setText(desc)

    def _update_desc_preview(self) -> None:
        """Render the HTML description in the preview browser and BASIC panel."""
        html = self._desc_html_edit.toPlainText().strip()
        if html:
            self._desc_preview_browser.setHtml(html)
            self._basic_desc_preview.setHtml(html)
        else:
            placeholder = (
                '<p style="color: #999; font-style: italic;">'
                'No description yet — use Generate or type HTML above.</p>'
            )
            self._desc_preview_browser.setHtml(placeholder)
            self._basic_desc_preview.setHtml(placeholder)

    # ==================================================================
    # CROSS-REFERENCES / INTERCHANGES
    # ==================================================================
    @staticmethod
    def _classify_part_type(pn: str) -> str:
        """Classify a part number as OEM, Aftermarket, or Cross-Reference.

        Known aftermarket manufacturer prefixes:
          MCIF / MCBMCIF = Interstate McBee
          PAI / EIS / 3-letter + digits starting with PAI pattern
          FP = Federal Power Products
          HP = Highway & Heavy Parts
        OEM patterns: Caterpillar (digits only, 7+ chars), Cummins (digits, 7+ chars)
        """
        import re as _re_cls
        up = pn.strip().upper()

        # --- Aftermarket prefixes ---
        _AFTERMARKET_PREFIXES = (
            "MCIF", "MCBMCIF", "MCB",       # Interstate McBee
            "PAI ",                           # PAI Industries (with space)
            "681",                            # PAI 681-series
            "FP",                             # Federal Power Products
        )
        for pfx in _AFTERMARKET_PREFIXES:
            if up.startswith(pfx):
                return "Aftermarket"

        # PAI-style: letter(s) + digits with optional dash (e.g. C15103-010)
        # But only if it contains a dash — pure digits could be OEM
        if _re_cls.match(r"^[A-Z]{1,3}\d{3,}", up) and "-" in up:
            return "Aftermarket"

        # HHP-specific suffixes: part number ending with HP (e.g. C15103010HP)
        if _re_cls.match(r"^.+HP$", up) and len(up) > 4:
            return "Aftermarket"

        # Pure long-digit strings (7+) are likely OEM (CAT, Cummins)
        if _re_cls.match(r"^\d{7,}$", up):
            return "OEM"

        return "Cross-Reference"

    def _load_interchanges(self, product: dict) -> None:
        """Load existing interchanges into the xref table."""
        self._xref_items = []
        if self._product_id:
            try:
                rows = get_interchanges(self._product_id)
                for r in rows:
                    pn = r.get("interchange_number", "")
                    db_type = r.get("interchange_type", "OEM")
                    # Re-classify if DB says OEM but pattern says otherwise
                    if db_type == "OEM":
                        db_type = self._classify_part_type(pn)
                    self._xref_items.append({
                        "id": r.get("id"),
                        "number": pn,
                        "type": db_type,
                        "manufacturer": r.get("manufacturer", "") or "",
                        "new": False,
                    })
            except Exception:
                pass

        # Also auto-add OEM Part# and Vendor SKU as display rows
        oem = str(product.get("oem_part_number", "") or "")
        pai_sku = str(product.get("pai_sku", "") or "")
        existing_numbers = {x["number"].upper() for x in self._xref_items}
        if oem and oem.upper() not in existing_numbers:
            self._xref_items.insert(0, {
                "id": None, "number": oem, "type": self._classify_part_type(oem),
                "manufacturer": "", "new": True,
            })
        if pai_sku and pai_sku.upper() not in existing_numbers and pai_sku.upper() != oem.upper():
            self._xref_items.insert(0, {
                "id": None, "number": pai_sku, "type": "Supplier",
                "manufacturer": str(product.get("supplier", "") or ""),
                "new": True,
            })

        self._refresh_xref_table()

    def _refresh_xref_table(self) -> None:
        """Refresh the xref table widget from _xref_items."""
        self._xref_table.setRowCount(len(self._xref_items))
        for i, x in enumerate(self._xref_items):
            self._xref_table.setItem(i, 0, QTableWidgetItem(x["number"]))
            self._xref_table.setItem(i, 1, QTableWidgetItem(x["type"]))
            self._xref_table.setItem(i, 2, QTableWidgetItem(x["manufacturer"]))
            del_btn = QPushButton("✕")
            del_btn.setMaximumWidth(28)
            del_btn.setToolTip("Remove")
            del_btn.clicked.connect(lambda _, idx=i: self._on_remove_xref(idx))
            self._xref_table.setCellWidget(i, 3, del_btn)

    @staticmethod
    def _is_valid_part_number(pn: str) -> str | None:
        """Return cleaned part number or None if invalid."""
        pn = pn.strip()
        if not pn or pn in ("-", "—", "N/A", "n/a", "NA", "none", "None"):
            return None
        if len(pn) <= 1 and pn.isdigit():
            return None
        return pn

    def _on_add_xref(self) -> None:
        """Add a new cross-reference from the input fields."""
        raw = self._xref_pn_input.text()
        pn = self._is_valid_part_number(raw)
        if pn is None:
            if raw.strip():
                QMessageBox.warning(
                    self, "Invalid",
                    f"'{raw.strip()}' is not a valid part number.",
                )
            return
        xtype = self._xref_type_combo.currentText()
        supplier = self._xref_supplier_input.text().strip()

        # Check duplicates in current list
        for x in self._xref_items:
            if x["number"].upper() == pn.upper():
                QMessageBox.information(self, "Duplicate", f"'{pn}' is already in the list.")
                return

        item = {"id": None, "number": pn, "type": xtype, "manufacturer": supplier, "new": True}
        self._xref_items.append(item)
        self._refresh_xref_table()
        self._xref_pn_input.clear()
        self._xref_supplier_input.clear()
        self._on_field_changed()

    def _on_remove_xref(self, idx: int) -> None:
        """Remove a cross-reference row."""
        if idx < 0 or idx >= len(self._xref_items):
            return
        removed = self._xref_items.pop(idx)
        # If it was persisted, delete from DB
        if removed.get("id") and self._product_id:
            try:
                delete_interchange(removed["id"])
            except Exception:
                pass
        self._refresh_xref_table()
        self._on_field_changed()

    def _save_new_interchanges(self) -> None:
        """Save any new xref items to the database."""
        if not self._product_id:
            return
        for x in self._xref_items:
            if x.get("new") and x["number"]:
                try:
                    add_interchange(
                        self._product_id,
                        x["number"],
                        interchange_type=x["type"],
                        manufacturer=x["manufacturer"] or None,
                    )
                except Exception:
                    pass  # Ignore duplicates

    # ==================================================================
    # SUGGESTED SELLS
    # ==================================================================

    def _load_suggested_sells(self, product: dict) -> None:
        """Load existing suggested-sell links into the table."""
        self._suggest_items = []
        if self._product_id:
            try:
                self._suggest_items = get_suggested_sells(self._product_id)
            except Exception:
                pass
        self._refresh_suggest_table()

    def _refresh_suggest_table(self) -> None:
        """Refresh the suggested-sells table widget."""
        self._suggest_table.setRowCount(len(self._suggest_items))
        for i, s in enumerate(self._suggest_items):
            def _mk(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._suggest_table.setItem(i, 0, _mk(s.get("sku", "")))
            self._suggest_table.setItem(i, 1, _mk(s.get("title", "")))
            self._suggest_table.setItem(i, 2, _mk(s.get("supplier", "")))
            price = float(s.get("selling_price") or 0)
            self._suggest_table.setItem(i, 3, _mk(f"${price:,.2f}" if price else ""))
            rel_type = (s.get("relationship_type") or "optional").title()
            type_item = _mk(rel_type)
            if rel_type == "Required":
                type_item.setForeground(QColor("#c62828"))
            elif rel_type == "Recommended":
                type_item.setForeground(QColor("#1565c0"))
            else:
                type_item.setForeground(QColor("#666666"))
            self._suggest_table.setItem(i, 4, type_item)
            del_btn = QPushButton("✕")
            del_btn.setMaximumWidth(28)
            del_btn.setStyleSheet(
                "QPushButton { color: white; background-color: #c62828; "
                "font-weight: bold; border: none; border-radius: 3px; "
                "padding: 2px; font-size: 9pt; }"
                "QPushButton:hover { background-color: #e53935; }"
            )
            del_btn.setToolTip("Remove")
            row_id = s.get("id")
            del_btn.clicked.connect(lambda _, rid=row_id: self._on_remove_suggested_sell(rid))
            self._suggest_table.setCellWidget(i, 5, del_btn)

    def _on_search_suggested_sell(self) -> None:
        """Search for products to add as suggested sells (quote-style search)."""
        query = self._suggest_search_input.text().strip()
        if not query:
            return

        results = search_by_part_number(query)
        browse_results = browse_products(search=query, limit=50)
        seen_ids = {r.get("id") or r.get("product_id") for r in results}
        for br in browse_results:
            if br.get("id") not in seen_ids:
                results.append(br)

        # Remove self and already-suggested products
        existing_ids = {s.get("suggested_product_id") for s in self._suggest_items}
        if self._product_id:
            existing_ids.add(self._product_id)
        results = [r for r in results if r.get("id") not in existing_ids]
        self._suggest_search_results = results[:50]

        self._suggest_search_table.setRowCount(len(self._suggest_search_results))
        for i, p in enumerate(self._suggest_search_results):
            def _mk(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            pid = p.get("id") or p.get("product_id", "")
            self._suggest_search_table.setItem(i, 0, _mk(pid))
            self._suggest_search_table.setItem(i, 1, _mk(p.get("sku", "")))
            self._suggest_search_table.setItem(i, 2, _mk(p.get("title", "")))
            self._suggest_search_table.setItem(i, 3, _mk(p.get("supplier", "")))

            price = float(p.get("selling_price") or 0)
            self._suggest_search_table.setItem(i, 4, _mk(f"${price:,.2f}" if price else ""))

            cost = float(p.get("cost") or 0)
            margin = ((price - cost) / price * 100) if price > 0 and cost > 0 else 0
            margin_item = _mk(f"{margin:.1f}%")
            if margin < 10:
                margin_item.setForeground(QColor("#c62828"))
            elif margin < 30:
                margin_item.setForeground(QColor("#f59e0b"))
            else:
                margin_item.setForeground(QColor("#2e7d32"))
            self._suggest_search_table.setItem(i, 5, margin_item)

            engine = p.get("engine_manufacturer", "") or ""
            if p.get("engine_model"):
                engine = f"{engine} {p['engine_model']}".strip()
            self._suggest_search_table.setItem(i, 6, _mk(engine))

        self._suggest_search_table.show()
        count = len(self._suggest_search_results)
        self._suggest_search_count.setText(
            f"Found {count} result{'s' if count != 1 else ''}"
        )
        self._suggest_search_count.show()
        self._suggest_add_btn.show()

    def _on_add_from_suggest_search(self) -> None:
        """Add the selected search result as a suggested sell."""
        if not self._product_id:
            QMessageBox.information(self, "Save First", "Save the product before adding suggestions.")
            return
        row = self._suggest_search_table.currentRow()
        if row < 0 or row >= len(self._suggest_search_results):
            QMessageBox.information(self, "No Selection", "Select a product from the search results.")
            return

        target = self._suggest_search_results[row]
        target_id = target.get("id")
        if target_id == self._product_id:
            return

        # Check duplicate
        if any(s.get("suggested_product_id") == target_id for s in self._suggest_items):
            QMessageBox.information(self, "Duplicate", "This product is already in the list.")
            return

        rel_type = "optional"
        new_id = add_suggested_sell(self._product_id, target_id, relationship_type=rel_type)
        if new_id is None:
            QMessageBox.warning(self, "Error", "Could not add suggestion (possibly duplicate).")
            return

        self._suggest_items.append({
            "id": new_id,
            "suggested_product_id": target_id,
            "sku": target.get("sku", ""),
            "title": target.get("title", ""),
            "supplier": target.get("supplier", ""),
            "selling_price": target.get("selling_price", 0),
            "relationship_type": rel_type,
        })
        self._refresh_suggest_table()

        # Remove from search results so it can't be double-added
        self._suggest_search_results.pop(row)
        self._suggest_search_table.removeRow(row)
        count = len(self._suggest_search_results)
        self._suggest_search_count.setText(
            f"Found {count} result{'s' if count != 1 else ''}"
        )
        self._on_field_changed()

    def _on_open_suggested_sell(self) -> None:
        """Open the selected suggested-sell product in a new dialog."""
        row = self._suggest_table.currentRow()
        if row < 0 or row >= len(self._suggest_items):
            return
        target_id = self._suggest_items[row].get("suggested_product_id")
        if not target_id:
            return
        target = get_product_by_id(target_id)
        if not target:
            QMessageBox.warning(self, "Not Found", "Product no longer exists.")
            return
        target_sku = target.get("sku", "")
        if not target_sku:
            QMessageBox.warning(self, "No SKU", "Product has no SKU.")
            return
        dlg = EditProductDialog(sku=target_sku, parent=None)
        EditProductDialog._open_dialogs.append(dlg)
        dlg.finished.connect(lambda: EditProductDialog._remove_open_dialog(dlg))
        dlg.show()

    def _on_remove_suggested_sell(self, row_id: int) -> None:
        """Remove a suggested-sell link with confirmation."""
        item = next((s for s in self._suggest_items if s.get("id") == row_id), None)
        sku = item.get("sku", "?") if item else "?"
        reply = QMessageBox.question(
            self, "Remove Suggested Sell",
            f"Remove '{sku}' from suggested sells?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        delete_suggested_sell(row_id)
        self._suggest_items = [s for s in self._suggest_items if s.get("id") != row_id]
        self._refresh_suggest_table()
        self._on_field_changed()

    def eventFilter(self, obj, event) -> bool:
        """Handle DEL key on suggested-sells table."""
        if obj is self._suggest_table and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Delete:
                row = self._suggest_table.currentRow()
                if 0 <= row < len(self._suggest_items):
                    row_id = self._suggest_items[row].get("id")
                    if row_id:
                        self._on_remove_suggested_sell(row_id)
                return True
        return super().eventFilter(obj, event)

    # ==================================================================
    # PENDING CROSS REFERENCES
    # ==================================================================

    def _load_pending_xrefs(self) -> None:
        """Load pending scraped cross-references for this product."""
        self._pending_xref_items.clear()
        if not self._product_id:
            self._refresh_pending_xref_table()
            return
        try:
            rows = get_pending_scraped_xrefs(self._product_id)
            for r in rows:
                pn = (r.get("found_alternate_number") or "").strip()
                if not self._is_valid_part_number(pn):
                    continue
                db_type = r.get("match_type", "Cross-Reference")
                if db_type == "OEM":
                    db_type = self._classify_part_type(pn)
                self._pending_xref_items.append({
                    "id": r["id"],
                    "number": pn,
                    "source": r.get("source", ""),
                    "type": db_type,
                })
        except Exception:
            pass
        self._refresh_pending_xref_table()

    def _refresh_pending_xref_table(self) -> None:
        """Refresh the pending xref table widget."""
        self._pending_xref_table.setRowCount(len(self._pending_xref_items))
        for i, x in enumerate(self._pending_xref_items):
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb.setCheckState(Qt.CheckState.Checked)
            self._pending_xref_table.setItem(i, 0, cb)
            self._pending_xref_table.setItem(i, 1, QTableWidgetItem(x["number"]))
            self._pending_xref_table.setItem(i, 2, QTableWidgetItem(x["source"]))
            self._pending_xref_table.setItem(i, 3, QTableWidgetItem(x["type"]))
        # Hide section if nothing pending
        has_pending = len(self._pending_xref_items) > 0
        self._pending_xref_table.setVisible(has_pending)
        self._accept_pending_btn.setVisible(has_pending)
        self._discard_pending_btn.setVisible(has_pending)
        if hasattr(self, "_select_all_pending_btn"):
            self._select_all_pending_btn.setVisible(has_pending)
            self._clear_pending_btn.setVisible(has_pending)

    def _set_all_pending_xref_checks(self, state: Qt.CheckState) -> None:
        """Set every pending xref row's checkbox to the given state."""
        for i in range(self._pending_xref_table.rowCount()):
            cb = self._pending_xref_table.item(i, 0)
            if cb is not None:
                cb.setCheckState(state)

    def _on_accept_pending_xrefs(self) -> None:
        """Accept checked pending cross-references → move to live table."""
        accepted = 0
        for i in range(len(self._pending_xref_items) - 1, -1, -1):
            cb = self._pending_xref_table.item(i, 0)
            if cb and cb.checkState() == Qt.CheckState.Checked:
                xref = self._pending_xref_items[i]
                try:
                    accept_scraped_xref(xref["id"])
                    accepted += 1
                except Exception:
                    pass
                self._pending_xref_items.pop(i)
        if accepted:
            self._refresh_pending_xref_table()
            if self._product:
                self._load_interchanges(self._product)

    def _on_discard_pending_xrefs(self) -> None:
        """Reject checked pending cross-references."""
        discarded = 0
        for i in range(len(self._pending_xref_items) - 1, -1, -1):
            cb = self._pending_xref_table.item(i, 0)
            if cb and cb.checkState() == Qt.CheckState.Checked:
                xref = self._pending_xref_items[i]
                try:
                    reject_scraped_xref(xref["id"])
                    discarded += 1
                except Exception:
                    pass
                self._pending_xref_items.pop(i)
        if discarded:
            self._refresh_pending_xref_table()

    # ==================================================================
    # FIELD CHANGE TRACKING
    # ==================================================================
    def _canonical_engine_mfr(self, value: str) -> str:
        key = str(value or "").strip().lower()
        if not key:
            return ""
        return _ENGINE_MFR_ALIASES.get(key, str(value or "").strip())

    def _infer_engine_mfr_from_model(self, model: str) -> str:
        token = str(model or "").strip().upper()
        if not token:
            return ""
        for hint, mfr in _ENGINE_MODEL_HINTS:
            if hint in token:
                return mfr
        return ""

    def _infer_truck_mfr_from_model(self, model: str) -> str:
        token = str(model or "").strip().upper()
        if not token:
            return ""
        for hint, mfr in _TRUCK_MODEL_HINTS:
            if hint in token:
                return mfr
        return ""

    def _models_for_engine_mfr(self, mfr: str) -> list[str]:
        canonical = self._canonical_engine_mfr(mfr)
        curated = list(_ENGINE_MODELS_BY_MFR.get(canonical, []))
        all_models = list(getattr(self, "_all_engine_models", []))
        if not canonical:
            return sorted(set(curated + all_models))
        filtered = [m for m in all_models if self._infer_engine_mfr_from_model(m) == canonical]
        return sorted(set(curated + filtered))

    def _models_for_truck_mfr(self, mfr: str) -> list[str]:
        canonical = str(mfr or "").strip()
        curated = list(_TRUCK_MODELS_BY_MFR.get(canonical, []))
        all_models = list(getattr(self, "_all_truck_models", []))
        if not canonical:
            return sorted(set(curated + all_models))
        filtered = [m for m in all_models if self._infer_truck_mfr_from_model(m) == canonical]
        return sorted(set(curated + filtered))

    def _rebuild_engine_model_options(self, preferred_text: str) -> None:
        mfr = self._engine_mfr_combo.currentText().strip()
        options = self._models_for_engine_mfr(mfr)
        if preferred_text and preferred_text not in options:
            options.append(preferred_text)
            options = sorted(set(options))
        self._engine_model_combo.blockSignals(True)
        try:
            self._engine_model_combo.clear()
            self._engine_model_combo.addItem("")
            for opt in options:
                self._engine_model_combo.addItem(opt)
            self._engine_model_combo.setCurrentText(preferred_text)
        finally:
            self._engine_model_combo.blockSignals(False)

    def _rebuild_truck_model_options(self, preferred_text: str) -> None:
        mfr = self._truck_mfr_combo.currentText().strip()
        options = self._models_for_truck_mfr(mfr)
        if preferred_text and preferred_text not in options:
            options.append(preferred_text)
            options = sorted(set(options))
        self._truck_model_edit.blockSignals(True)
        try:
            self._truck_model_edit.clear()
            self._truck_model_edit.addItem("")
            for opt in options:
                self._truck_model_edit.addItem(opt)
            self._truck_model_edit.setCurrentText(preferred_text)
        finally:
            self._truck_model_edit.blockSignals(False)

    def _initialize_smart_platform_state(self) -> None:
        self._platform_syncing = True
        try:
            self._rebuild_engine_model_options(self._engine_model_combo.currentText().strip())
            self._rebuild_truck_model_options(self._truck_model_edit.currentText().strip())
            self._on_engine_model_changed_smart(self._engine_model_combo.currentText())
            self._on_truck_model_changed_smart(self._truck_model_edit.currentText())
        finally:
            self._platform_syncing = False

    def _on_platform_toggle(self, checked: bool) -> None:
        """No-op — all platform fields are always visible now."""
        pass

    def _on_engine_mfr_changed_smart(self, text: str) -> None:
        if getattr(self, "_platform_syncing", False):
            return
        self._platform_syncing = True
        try:
            current_model = self._engine_model_combo.currentText().strip()
            self._rebuild_engine_model_options(current_model)
            self._on_engine_model_changed_smart(current_model)
        finally:
            self._platform_syncing = False

    def _on_engine_model_changed_smart(self, text: str) -> None:
        if getattr(self, "_platform_syncing", False):
            return
        inferred = self._infer_engine_mfr_from_model(text)
        selected = self._canonical_engine_mfr(self._engine_mfr_combo.currentText())
        hint = ""

        self._platform_syncing = True
        try:
            if inferred and not selected:
                self._engine_mfr_combo.setCurrentText(inferred)
                selected = inferred
                hint = f"Auto-selected engine manufacturer: {inferred}"
            elif inferred and selected and inferred != selected:
                hint = (
                    f"Model '{text.strip()}' usually maps to {inferred}. "
                    f"Current override: {selected}."
                )
            self._rebuild_engine_model_options(text.strip())
        finally:
            self._platform_syncing = False
        self._engine_platform_hint_label.setText(hint)

    def _on_truck_mfr_changed_smart(self, text: str) -> None:
        if getattr(self, "_platform_syncing", False):
            return
        self._platform_syncing = True
        try:
            current_model = self._truck_model_edit.currentText().strip()
            self._rebuild_truck_model_options(current_model)
            self._on_truck_model_changed_smart(current_model)
        finally:
            self._platform_syncing = False

    def _on_truck_model_changed_smart(self, text: str) -> None:
        if getattr(self, "_platform_syncing", False):
            return
        inferred = self._infer_truck_mfr_from_model(text)
        selected = self._truck_mfr_combo.currentText().strip()
        hint = ""

        self._platform_syncing = True
        try:
            if inferred and not selected:
                self._truck_mfr_combo.setCurrentText(inferred)
                selected = inferred
                hint = f"Auto-selected truck manufacturer: {inferred}"
            elif inferred and selected and inferred != selected:
                hint = (
                    f"Model '{text.strip()}' usually maps to {inferred}. "
                    f"Current override: {selected}."
                )
            self._rebuild_truck_model_options(text.strip())
        finally:
            self._platform_syncing = False
        self._truck_platform_hint_label.setText(hint)

    # ── Warranty helpers ──────────────────────────────────────────
    def _on_private_label_toggled(self, on: bool) -> None:
        """Hide vendor SKU fields and update SKU when Private Label is toggled."""
        self._vendor_sku_label.setVisible(not on)
        self._pai_sku_edit.setVisible(not on)
        self._pai_lookup_btn.setVisible(not on)
        self._private_label_part_label.setVisible(on)
        self._private_label_part_edit.setVisible(on)
        if on and not self._private_label_part_edit.text().strip():
            seed = (
                self._pai_sku_edit.text().strip()
                or self._oem_pn_edit.text().strip()
            )
            if not seed:
                sku_text = self._sku_edit.text().strip()
                if sku_text.upper().startswith("JAKS-"):
                    seed = sku_text[5:]
            if seed:
                self._private_label_part_edit.setText(seed)
        self._auto_update_sku()
        self._update_invoice_preview()

    def _on_supplier_changed_warranty(self, supplier_text: str) -> None:
        """Auto-enable JAKS warranty when supplier is JAKS."""
        if not hasattr(self, "_jaks_warranty_cb"):
            return
        is_jaks = "jaks" in supplier_text.lower()
        if is_jaks and not self._jaks_warranty_cb.isChecked():
            self._jaks_warranty_cb.setChecked(True)

    def _on_supplier_warranty_toggled(self, on: bool) -> None:
        self._sw_quick_frame.setVisible(on)
        self._sw_value_spin.setVisible(on)
        self._sw_unit_combo.setVisible(on)
        self._sw_display_label.setVisible(on)
        if not on:
            self._sw_value_spin.setValue(0)
        self._update_invoice_preview()

    def _set_supplier_warranty(self, value: int, unit: str) -> None:
        self._sw_value_spin.setValue(value)
        idx = self._sw_unit_combo.findText(unit)
        if idx >= 0:
            self._sw_unit_combo.setCurrentIndex(idx)
        self._update_sw_display()
        self._on_field_changed()
        self._update_invoice_preview()

    def _update_sw_display(self) -> None:
        v = self._sw_value_spin.value()
        u = self._sw_unit_combo.currentText()
        if v > 0:
            self._sw_display_label.setText(f"✓ {v} {u}")
        else:
            self._sw_display_label.setText("")

    def _on_jaks_warranty_toggled(self, on: bool) -> None:
        # Only the visible widgets are toggled; the hidden helpers always
        # carry value=1 / unit='years' so legacy code paths see something.
        self._jw_pct_spin.setVisible(on)
        if hasattr(self, "_jw_pct_hint_label"):
            self._jw_pct_hint_label.setVisible(on)
        if hasattr(self, "_jw_preview_label"):
            self._jw_preview_label.setVisible(on)
        if not on:
            self._jw_charge_spin.setValue(0)
        else:
            self._recompute_warranty_charge()
        self._update_jw_preview()
        self._update_invoice_preview()

    def _recompute_warranty_charge(self, *_args) -> None:
        """Derive `_jw_charge_spin` from % rate × current selling price.

        Single source of truth is the percentage; the $ charge is kept as a
        hidden helper so legacy DB/code paths still see a value.
        """
        if not hasattr(self, "_jw_pct_spin") or not hasattr(self, "_selling_price_spin"):
            return
        pct = float(self._jw_pct_spin.value() or 0)
        sell = float(self._selling_price_spin.value() or 0)
        charge = round(sell * pct / 100.0, 2) if (pct and sell) else 0.0
        # Avoid feedback loops — block signals while updating derived value.
        self._jw_charge_spin.blockSignals(True)
        try:
            self._jw_charge_spin.setValue(charge)
        finally:
            self._jw_charge_spin.blockSignals(False)
        self._update_jw_preview()

    def _update_jw_preview(self) -> None:
        """Refresh the live '1 yr / 2 yr / 3 yr' preview line."""
        if not hasattr(self, "_jw_preview_label"):
            return
        if not self._jaks_warranty_cb.isChecked():
            self._jw_preview_label.setText("")
            return
        pct = float(self._jw_pct_spin.value() or 0)
        sell = float(self._selling_price_spin.value() or 0) if hasattr(self, "_selling_price_spin") else 0
        if pct <= 0 or sell <= 0:
            self._jw_preview_label.setText(
                "<i style='color:#888;'>Set a sell price and rate to see preview.</i>"
            )
            return
        per_yr = sell * pct / 100.0
        self._jw_preview_label.setText(
            f"<b>Preview:</b>  1 yr → ${per_yr:,.2f}  ·  "
            f"2 yr → ${per_yr * 2:,.2f}  ·  "
            f"3 yr → ${per_yr * 3:,.2f}"
        )

    def _set_jaks_warranty(self, value: int, unit: str) -> None:
        # Legacy helper — duration is fixed (1/years) on product now.
        self._jw_value_spin.setValue(value)
        idx = self._jw_unit_combo.findText(unit)
        if idx >= 0:
            self._jw_unit_combo.setCurrentIndex(idx)
        self._on_field_changed()
        self._update_invoice_preview()

    def _update_jw_display(self) -> None:
        # Legacy no-op — display label is hidden in simplified UI.
        if hasattr(self, "_jw_display_label"):
            self._jw_display_label.setText("")

    def _set_jaks_warranty_with_pct(self, value: int, unit: str, pct: float) -> None:
        """Legacy helper retained for any external callers."""
        self._jw_value_spin.setValue(value)
        idx = self._jw_unit_combo.findText(unit)
        if idx >= 0:
            self._jw_unit_combo.setCurrentIndex(idx)
        self._jw_pct_spin.setValue(pct)
        self._recompute_warranty_charge()
        self._on_field_changed()

    def _update_jw_quick_highlight(self) -> None:
        # Legacy no-op — quick buttons removed in simplified UI.
        return

    def _on_field_changed(self) -> None:
        """Handle field value change."""
        self._modified = True
        self._save_btn.setEnabled(True)
        self._save_btn.setStyleSheet(self._save_enabled_style)
        self._unsaved_label.setText("● Unsaved changes")

    def _on_product_type_changed(self) -> None:
        """Show/hide inventory-specific controls based on product type."""
        is_inventoried = (self._product_type_combo.currentData() == "inventoried")
        for widget in (
            getattr(self, "_min_qty_spin", None),
            getattr(self, "_max_qty_spin", None),
            getattr(self, "_qoh_label", None),
            getattr(self, "_inv_set_qoh_spin", None),
            getattr(self, "_inv_effective_date", None),
            getattr(self, "_inv_apply_btn", None),
            getattr(self, "_inv_adjust_hint", None),
            getattr(self, "_needs_audit_cb", None),
        ):
            if widget is not None:
                widget.setEnabled(is_inventoried)

    # ==================================================================
    # SIDEBAR NAVIGATION & PRICING SUMMARY
    # ==================================================================

    def _nav_to_section(self, nav_idx: int) -> None:
        """Navigate to a section: switch the stacked widget page."""
        self._section_stack.setCurrentIndex(nav_idx)
        for i, btn in enumerate(self._nav_buttons):
            if i == nav_idx:
                btn.setStyleSheet(self._nav_btn_active)
            else:
                btn.setStyleSheet(self._nav_btn_style)

    def _update_pricing_summary(self) -> None:
        """Update the sidebar pricing summary panel."""
        sell = self._selling_price_spin.value()
        cost = self._cost_spin.value()
        compare = self._compare_price_spin.value()
        margin_val = self._margin_spin.value()

        self._sidebar_sell.setText(f"${sell:,.2f}" if sell > 0 else "—")
        self._sidebar_cost.setText(f"${cost:,.2f}" if cost > 0 else "—")
        self._sidebar_compare.setText(f"${compare:,.2f}" if compare > 0 else "—")
        self._sidebar_target.setText(f"{margin_val:.0f}%")

        if sell > 0 and cost > 0:
            margin_pct = ((sell - cost) / sell) * 100
            self._sidebar_margin.setText(f"{margin_pct:.0f}%")
            color = "#228B22" if margin_pct > 0 else "#dc3545"
            self._sidebar_margin.setStyleSheet(
                f"color: {color}; font-weight: bold; font-size: 11pt; background: transparent;"
            )
        else:
            self._sidebar_margin.setText("0%")

    def _get_changes(self) -> dict:
        """Get dictionary of changed fields."""
        product = self._product or {}
        changes = {}

        new_title = self._title_edit.text().strip()
        if new_title != str(product.get("title", "") or ""):
            changes["title"] = new_title
        # Description field stores to both title and brief_description
        if new_title != str(product.get("brief_description", "") or ""):
            changes["brief_description"] = new_title

        new_category = self._category_combo.currentText().strip()
        if new_category != str(product.get("category", "") or ""):
            changes["category"] = new_category

        new_subcategory = self._subcategory_combo.currentText().strip()
        if new_subcategory != str(product.get("subcategory", "") or ""):
            changes["subcategory"] = new_subcategory

        new_manufacturer = self._manufacturer_combo.currentText().strip()
        if new_manufacturer != str(product.get("manufacturer", "") or ""):
            changes["manufacturer"] = new_manufacturer

        # Supplier
        new_supplier = self._supplier_combo.currentText()
        if new_supplier == "-- Select Vendor --":
            new_supplier = ""
        if new_supplier != str(product.get("supplier", "") or ""):
            changes["supplier"] = new_supplier
            vendor_id = self._supplier_combo.currentData()
            changes["preferred_vendor_id"] = vendor_id

        new_condition = self._condition_combo.currentText()
        if new_condition != str(product.get("condition", "") or ""):
            changes["condition"] = new_condition

        # SKU (if unlocked for editing)
        if not self._sku_edit.isReadOnly():
            new_sku = self._sku_edit.text().strip()
            if new_sku and new_sku != str(product.get("sku", "") or ""):
                changes["sku"] = new_sku

        # Engine / Invoice description fields
        new_eng_mfr = self._engine_mfr_combo.currentText().strip()
        if new_eng_mfr != str(product.get("oem_manufacturer", "") or ""):
            changes["oem_manufacturer"] = new_eng_mfr

        new_eng_model = self._engine_model_combo.currentText().strip()
        if new_eng_model != str(product.get("engine", "") or ""):
            changes["engine"] = new_eng_model

        new_oem_pn = self._oem_pn_edit.text().strip()
        if new_oem_pn != str(product.get("oem_part_number", "") or ""):
            changes["oem_part_number"] = new_oem_pn

        # Truck fields
        new_truck_mfr = self._truck_mfr_combo.currentText().strip()
        if new_truck_mfr != str(product.get("truck_manufacturer", "") or ""):
            changes["truck_manufacturer"] = new_truck_mfr

        new_truck_model = self._truck_model_edit.currentText().strip()
        if new_truck_model != str(product.get("truck_model", "") or ""):
            changes["truck_model"] = new_truck_model

        new_truck_system = self._truck_system_combo.currentText().strip()
        if new_truck_system != str(product.get("truck_system", "") or ""):
            changes["truck_system"] = new_truck_system

        # Always update invoice_description if any invoice field changed
        new_inv_desc = self._build_invoice_description()
        if new_inv_desc != str(product.get("invoice_description", "") or ""):
            changes["invoice_description"] = new_inv_desc

        # Pricing
        _pricing_changed = False

        # Price Category override (tiered pricing v2)
        if hasattr(self, "_price_category_combo"):
            new_pc = self._price_category_combo.currentData()
            old_pc = product.get("price_category_id")
            # Normalise: treat 0/empty as None for comparison
            old_pc_norm = old_pc if old_pc not in (0, "", "0") else None
            if new_pc != old_pc_norm:
                changes["price_category_id"] = new_pc
                _pricing_changed = True

        new_selling_price = self._selling_price_spin.value()
        if abs(new_selling_price - float(product.get("selling_price", 0) or 0)) > 0.001:
            changes["selling_price"] = new_selling_price
            _pricing_changed = True

        new_compare_price = self._compare_price_spin.value()
        if abs(new_compare_price - float(product.get("compare_at_price", 0) or 0)) > 0.001:
            changes["compare_at_price"] = new_compare_price
            _pricing_changed = True

        new_cost = self._cost_spin.value()
        if abs(new_cost - float(product.get("cost", 0) or 0)) > 0.001:
            changes["cost"] = new_cost
            _pricing_changed = True

        new_pai_cost = self._pai_cost_spin.value()
        if abs(new_pai_cost - float(product.get("pai_cost", 0) or 0)) > 0.001:
            changes["pai_cost"] = new_pai_cost
            from datetime import datetime
            changes["cost_updated_at"] = datetime.now().isoformat()
            _pricing_changed = True

        new_core_charge = self._core_charge_spin.value()
        if abs(new_core_charge - float(product.get("core_charge", 0) or 0)) > 0.001:
            changes["core_charge"] = new_core_charge
            _pricing_changed = True

        new_core_sell = self._core_sell_spin.value()
        if abs(new_core_sell - float(product.get("core_sell_price", 0) or 0)) > 0.001:
            changes["core_sell_price"] = new_core_sell
            _pricing_changed = True

        # ── Core flags & metadata (Phase: Core Charge Card) ──
        new_has_core = 1 if self._has_core_cb.isChecked() else 0
        if new_has_core != int(product.get("has_core", 0) or 0):
            changes["has_core"] = new_has_core

        new_return_days = int(self._core_return_window_spin.value())
        if new_has_core and new_return_days <= 0:
            new_return_days = 30
        if new_return_days != int(
            product.get("core_return_days", 0) or product.get("core_return_window_days", 0) or 0
        ):
            changes["core_return_days"] = new_return_days

        new_track_cust = 1 if self._track_customer_core_return_cb.isChecked() else 0
        if new_track_cust != int(product.get("track_customer_core_return", 1) or 0):
            changes["track_customer_core_return"] = new_track_cust

        new_track_vend = 1 if self._track_vendor_core_credit_cb.isChecked() else 0
        if new_track_vend != int(product.get("track_vendor_core_credit", 1) or 0):
            changes["track_vendor_core_credit"] = new_track_vend

        new_inc_price = 1 if self._core_include_in_price_cb.isChecked() else 0
        if new_inc_price != int(product.get("include_core_in_advertised_price", 0) or 0):
            changes["include_core_in_advertised_price"] = new_inc_price

        new_core_notes = self._core_notes_edit.toPlainText().strip()
        if new_core_notes != str(product.get("core_notes", "") or "").strip():
            changes["core_notes"] = new_core_notes

        # Core handling overhaul (migration 108): persist the product-level
        # default core_type so downstream resolvers honor it.
        try:
            new_core_type = self._core_type_combo.currentData() or "none"
        except Exception:
            new_core_type = "none"
        if str(new_core_type) != str(product.get("core_type") or "none"):
            changes["core_type"] = new_core_type

        new_shipping = self._shipping_cost_spin.value()
        if abs(new_shipping - float(product.get("shipping_cost", 0) or 0)) > 0.001:
            changes["shipping_cost"] = new_shipping
            _pricing_changed = True

        if _pricing_changed:
            from datetime import datetime
            changes["pricing_updated_at"] = datetime.now().isoformat()

        new_pai_sku = self._pai_sku_edit.text().strip()
        if new_pai_sku != str(product.get("pai_sku", "") or ""):
            changes["pai_sku"] = new_pai_sku

        new_pai_link = self._pai_link_edit.text().strip()
        if new_pai_link != str(product.get("pai_link", "") or ""):
            changes["pai_link"] = new_pai_link

        new_vendor_desc = self._vendor_desc_edit.text().strip()
        if new_vendor_desc != str(product.get("vendor_description", "") or ""):
            changes["vendor_description"] = new_vendor_desc

        # Vendor Availability
        new_vendor_avail = self._vendor_avail_edit.text().strip()
        if new_vendor_avail != str(product.get("vendor_availability", "") or ""):
            changes["vendor_availability"] = new_vendor_avail

        # Vendor Alternates
        new_vendor_alts = self._vendor_alternates_edit.text().strip()
        if new_vendor_alts != str(product.get("vendor_alternates", "") or ""):
            changes["vendor_alternates"] = new_vendor_alts

        # Shipping fields
        new_weight = self._weight_spin.value()
        if abs(new_weight - float(product.get("weight", 0) or 0)) > 0.001:
            changes["weight"] = new_weight

        new_weight_unit = self._weight_unit_combo.currentText()
        if new_weight_unit != str(product.get("weight_unit", "LBS") or "LBS"):
            changes["weight_unit"] = new_weight_unit

        new_length = self._length_spin.value()
        if abs(new_length - float(product.get("length", 0) or 0)) > 0.001:
            changes["length"] = new_length

        new_width = self._width_spin.value()
        if abs(new_width - float(product.get("width", 0) or 0)) > 0.001:
            changes["width"] = new_width

        new_height = self._height_spin.value()
        if abs(new_height - float(product.get("height", 0) or 0)) > 0.001:
            changes["height"] = new_height

        new_dim_unit = self._dim_unit_combo.currentText()
        if new_dim_unit != str(product.get("dimension_unit", "IN") or "IN"):
            changes["dimension_unit"] = new_dim_unit

        # Cost date (set by PAI lookup)
        if hasattr(self, "_pai_cost_date") and self._pai_cost_date:
            changes["cost_updated_at"] = self._pai_cost_date

        # Supplier Warranty
        new_sw_enabled = 1 if self._supplier_warranty_cb.isChecked() else 0
        if new_sw_enabled != int(product.get("supplier_warranty_enabled", 0) or 0):
            changes["supplier_warranty_enabled"] = new_sw_enabled

        new_sw_value = self._sw_value_spin.value()
        if new_sw_value != int(product.get("supplier_warranty_value", 0) or 0):
            changes["supplier_warranty_value"] = new_sw_value

        new_sw_unit = self._sw_unit_combo.currentText()
        if new_sw_unit != str(product.get("supplier_warranty_unit", "days") or "days"):
            changes["supplier_warranty_unit"] = new_sw_unit

        # JAKS Additional Warranty
        new_jw_enabled = 1 if self._jaks_warranty_cb.isChecked() else 0
        if new_jw_enabled != int(product.get("jaks_warranty_enabled", 0) or 0):
            changes["jaks_warranty_enabled"] = new_jw_enabled

        new_jw_value = self._jw_value_spin.value()
        if new_jw_value != int(product.get("jaks_warranty_value", 0) or 0):
            changes["jaks_warranty_value"] = new_jw_value

        new_jw_unit = self._jw_unit_combo.currentText()
        if new_jw_unit != str(product.get("jaks_warranty_unit", "days") or "days"):
            changes["jaks_warranty_unit"] = new_jw_unit

        new_jw_charge = self._jw_charge_spin.value()
        if abs(new_jw_charge - float(product.get("jaks_warranty_charge", 0) or 0)) > 0.001:
            changes["jaks_warranty_charge"] = new_jw_charge

        new_jw_pct = self._jw_pct_spin.value()
        if abs(new_jw_pct - float(product.get("warranty_percentage", 10.0) or 10.0)) > 0.01:
            changes["warranty_percentage"] = new_jw_pct

        # Also sync is_warrantable flag (used by quote warranty dialog)
        changes["is_warrantable"] = 1 if new_jw_enabled else 0

        new_requires_serial = self._requires_serial_cb.isChecked()
        if new_requires_serial != bool(product.get("requires_serial", 0)):
            changes["requires_serial"] = 1 if new_requires_serial else 0

        new_needs_audit = self._needs_audit_cb.isChecked()
        if new_needs_audit != bool(product.get("needs_audit", 0)):
            changes["needs_audit"] = 1 if new_needs_audit else 0

        new_min = self._min_qty_spin.value()
        if new_min != int(product.get("min_qty", 0) or 0):
            changes["min_qty"] = new_min

        new_max = self._max_qty_spin.value()
        if new_max != int(product.get("max_qty", 0) or 0):
            changes["max_qty"] = new_max

        new_notes_public = self._notes_public_edit.toPlainText().strip()
        if new_notes_public != str(product.get("notes_public", "") or ""):
            changes["notes_public"] = new_notes_public

        new_notes_internal = self._notes_internal_edit.toPlainText().strip()
        if new_notes_internal != str(product.get("notes_internal", "") or ""):
            changes["notes_internal"] = new_notes_internal

        # Private Label
        new_private_label = 1 if (
            hasattr(self, "_private_label_cb") and self._private_label_cb.isChecked()
        ) else 0
        if new_private_label != int(product.get("private_label", 0) or 0):
            changes["private_label"] = new_private_label

        new_pl_part = ""
        if hasattr(self, "_private_label_part_edit"):
            new_pl_part = self._private_label_part_edit.text().strip()
        if new_pl_part != str(product.get("private_label_part_number", "") or ""):
            changes["private_label_part_number"] = new_pl_part

        # Shopify fields
        new_tags = self._tags_edit.text().strip()
        if new_tags != str(product.get("tags", "") or ""):
            changes["tags"] = new_tags

        new_desc_html = self._desc_html_edit.toPlainText().strip()
        if new_desc_html != str(product.get("description_html", "") or ""):
            changes["description_html"] = new_desc_html

        # Product Type
        new_product_type = self._product_type_combo.currentData() or "inventoried"
        if new_product_type != str(product.get("product_type") or "inventoried"):
            changes["product_type"] = new_product_type

        return changes

    # ==================================================================
    # SAVE
    # ==================================================================
    _save_and_new = False

    def _on_save_and_new(self) -> None:
        """Save current product and signal that a new dialog should open."""
        self._save_and_new = True
        self._on_save()

    @property
    def should_open_new(self) -> bool:
        """Return True if the caller should open another Add Product dialog."""
        return self._save_and_new and self.result() == QDialog.DialogCode.Accepted

    def _validate_core_fields(self) -> bool:
        """Validate Core Charge card. Returns False if save should be blocked."""
        if not self._has_core_cb.isChecked():
            return True
        cust = float(self._core_sell_spin.value())
        vend = float(self._core_charge_spin.value())
        if cust <= 0:
            QMessageBox.warning(
                self, "Core Charge Required",
                "This product is marked as having a refundable core, but the\n"
                "Customer Core Charge is $0.00.\n\n"
                "Set a customer core charge greater than zero, or uncheck\n"
                "\"This product has a refundable core\".",
            )
            return False
        if vend > cust:
            ans = QMessageBox.question(
                self, "Vendor Deposit Exceeds Customer Charge",
                f"Vendor Core Deposit (${vend:,.2f}) is higher than the\n"
                f"Customer Core Charge (${cust:,.2f}).\n\n"
                "This means each unit sold loses money on the core return.\n\n"
                "Save anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return False
        # Auto-default the return window if blank
        if self._core_return_window_spin.value() <= 0:
            self._core_return_window_spin.setValue(30)
        return True

    def _on_save(self) -> None:
        """Save changes to database (create or update)."""
        # Validate vendor is selected
        supplier = self._supplier_combo.currentText()
        if supplier == "-- Select Vendor --" or not supplier.strip():
            QMessageBox.warning(
                self, "Vendor Required",
                "Please select a valid vendor/supplier before saving."
            )
            return

        # Validate core charge card
        if not self._validate_core_fields():
            return

        if self._create_mode:
            self._do_create()
            return

        if not self._product_id:
            QMessageBox.warning(self, "Error", "Product not found in database")
            return

        changes = self._get_changes()
        if not changes and not any(x.get("new") for x in self._xref_items):
            QMessageBox.information(self, "No Changes", "No changes to save.")
            return

        try:
            from datetime import datetime
            changes["updated_at"] = datetime.now().isoformat()

            success = update_product(self._product_id, **changes)

            if success:
                self._save_new_interchanges()
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Product updated successfully.\n\nChanged fields: {', '.join(changes.keys())}"
                )
                self.accept()
            else:
                QMessageBox.warning(self, "Error", "Failed to update product")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")

    def _do_create(self) -> None:
        """Create a new product."""
        sku = self._sku_edit.text().strip()
        if not sku:
            QMessageBox.warning(self, "Error", "SKU is required.")
            return

        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "Error", "Description is required.")
            return

        supplier = self._supplier_combo.currentText()
        if supplier == "-- Select Vendor --":
            supplier = ""
        vendor_id = self._supplier_combo.currentData()

        kwargs = dict(
            sku=sku,
            title=title,
            category=self._category_combo.currentText().strip(),
            subcategory=self._subcategory_combo.currentText().strip(),
            manufacturer=self._manufacturer_combo.currentText().strip(),
            supplier=supplier,
            condition=self._condition_combo.currentText(),
            cost=self._cost_spin.value(),
            pai_cost=self._pai_cost_spin.value(),
            selling_price=self._selling_price_spin.value(),
            compare_at_price=self._compare_price_spin.value(),
            core_charge=self._core_charge_spin.value(),
            pai_sku=self._pai_sku_edit.text().strip(),
            pai_link=self._pai_link_edit.text().strip(),
            preferred_vendor_id=vendor_id,
            requires_serial=1 if self._requires_serial_cb.isChecked() else 0,
            oem_manufacturer=self._engine_mfr_combo.currentText().strip(),
            engine=self._engine_model_combo.currentText().strip(),
            brief_description=self._title_edit.text().strip(),
            oem_part_number=self._oem_pn_edit.text().strip(),
            invoice_description=self._build_invoice_description(),
            truck_manufacturer=self._truck_mfr_combo.currentText().strip(),
            truck_model=self._truck_model_edit.currentText().strip(),
            truck_system=self._truck_system_combo.currentText().strip(),
            vendor_description=self._vendor_desc_edit.text().strip(),
            min_qty=self._min_qty_spin.value(),
            max_qty=self._max_qty_spin.value(),
            shipping_cost=self._shipping_cost_spin.value(),
            core_sell_price=self._core_sell_spin.value(),
            has_core=1 if self._has_core_cb.isChecked() else 0,
            core_return_days=int(self._core_return_window_spin.value()),
            track_customer_core_return=1 if self._track_customer_core_return_cb.isChecked() else 0,
            track_vendor_core_credit=1 if self._track_vendor_core_credit_cb.isChecked() else 0,
            include_core_in_advertised_price=1 if self._core_include_in_price_cb.isChecked() else 0,
            core_notes=self._core_notes_edit.toPlainText().strip(),
            notes_public=self._notes_public_edit.toPlainText().strip(),
            notes_internal=self._notes_internal_edit.toPlainText().strip(),
            private_label=1 if (
                hasattr(self, "_private_label_cb") and self._private_label_cb.isChecked()
            ) else 0,
            private_label_part_number=self._private_label_part_edit.text().strip() if hasattr(self, "_private_label_part_edit") else "",
            vendor_availability=self._vendor_avail_edit.text().strip(),
            vendor_alternates=self._vendor_alternates_edit.text().strip(),
            weight=self._weight_spin.value(),
            weight_unit=self._weight_unit_combo.currentText(),
            length=self._length_spin.value(),
            width=self._width_spin.value(),
            height=self._height_spin.value(),
            dimension_unit=self._dim_unit_combo.currentText(),
            supplier_warranty_enabled=1 if self._supplier_warranty_cb.isChecked() else 0,
            supplier_warranty_value=self._sw_value_spin.value(),
            supplier_warranty_unit=self._sw_unit_combo.currentText(),
            jaks_warranty_enabled=1 if self._jaks_warranty_cb.isChecked() else 0,
            jaks_warranty_value=self._jw_value_spin.value(),
            jaks_warranty_unit=self._jw_unit_combo.currentText(),
            jaks_warranty_charge=self._jw_charge_spin.value(),
            is_warrantable=1 if self._jaks_warranty_cb.isChecked() else 0,
            product_type=self._product_type_combo.currentData() or "inventoried",
        )

        if hasattr(self, "_price_category_combo"):
            kwargs["price_category_id"] = self._price_category_combo.currentData()

        try:
            # Check for potential duplicates before creating
            oem = self._oem_pn_edit.text().strip()
            dupes = find_similar_products(sku=sku, title=title, oem_part_number=oem)
            if dupes:
                lines = [f"• {d['sku']}  —  {d['title']}  (match: {d.get('match_reason', 'similar')})" for d in dupes[:10]]
                msg = "Potential duplicates found:\n\n" + "\n".join(lines) + "\n\nCreate anyway?"
                if QMessageBox.question(self, "Duplicate Warning", msg) != QMessageBox.StandardButton.Yes:
                    return

            result = create_product(**kwargs)
            if result.get("success"):
                # Save interchanges for new product
                new_prod = get_product_by_sku(sku)
                if new_prod:
                    self._product_id = new_prod.get("id")
                    self._save_new_interchanges()
                # Stash values for Save & New pre-fill
                if self._save_and_new:
                    EditProductDialog._prefill_values = {
                        "category": self._category_combo.currentText().strip(),
                        "subcategory": self._subcategory_combo.currentText().strip(),
                        "supplier": self._supplier_combo.currentText(),
                        "manufacturer": self._manufacturer_combo.currentText().strip(),
                        "condition": self._condition_combo.currentText(),
                        "cost": self._cost_spin.value(),
                        "pai_cost": self._pai_cost_spin.value(),
                        "selling_price": self._selling_price_spin.value(),
                        "compare_at_price": self._compare_price_spin.value(),
                        "shipping_cost": self._shipping_cost_spin.value(),
                        "min_qty": self._min_qty_spin.value(),
                        "max_qty": self._max_qty_spin.value(),
                        "private_label": 1 if self._private_label_cb.isChecked() else 0,
                        "private_label_part_number": self._private_label_part_edit.text().strip(),
                        "oem_manufacturer": self._engine_mfr_combo.currentText().strip(),
                        "engine": self._engine_model_combo.currentText().strip(),
                        "truck_manufacturer": self._truck_mfr_combo.currentText().strip(),
                        "truck_model": self._truck_model_edit.currentText().strip(),
                        "truck_system": self._truck_system_combo.currentText().strip(),
                    }
                QMessageBox.information(self, "Created", f"Product '{sku}' created successfully.")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", result.get("error", "Failed to create product"))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Create failed: {e}")

    # ==================================================================
    # COMPETITOR PRICES — Load & Display
    # ==================================================================

    _COMP_NAME_MAP = {
        "Diesel Parts Direct": "DPD",
        "ATL Diesel": "ATL",
        "FleetPride": "FLT",
        "Highway & Heavy Parts": "HHP",
    }

    def _load_competitor_prices(self) -> None:
        """Populate the competitor price labels from the DB."""
        try:
            if self._product_id:
                rows = get_latest_competitor_prices(self._product_id)
            else:
                # In create mode, look up by search term used
                search_term = (
                    self._pai_sku_edit.text().strip()
                    or self._oem_pn_edit.text().strip()
                    or self._scrape_override_edit.text().strip()
                )
                if not search_term:
                    return
                rows = get_latest_competitor_prices(sku_searched=search_term)
        except Exception:
            return

        latest_date = None
        for row in rows:
            name = row.get("competitor_name", "")
            abbr = self._COMP_NAME_MAP.get(name)
            if not abbr or abbr not in self._comp_labels:
                continue
            price = row.get("competitor_price")
            avail = row.get("availability", "")
            scraped = row.get("scraped_at", "")

            if price is not None:
                txt = f"${price:,.2f}"
                if avail:
                    txt += f"  ({avail})"
                self._comp_labels[abbr].setText(txt)
            else:
                self._comp_labels[abbr].setText("N/A")

            if scraped and (latest_date is None or str(scraped) > str(latest_date)):
                latest_date = scraped

        if latest_date:
            self._comp_scrape_date_label.setText(str(latest_date)[:10])

    # ==================================================================
    # PRODUCT IMAGES — Load / Display / Import / Delete
    # ==================================================================

    def _images_dir_for_product(self) -> str:
        """Return `<db_dir>/images/<product_id>/` (matches scraper_service)."""
        import os
        try:
            from db.database import get_database_url
            db_url = get_database_url() or ""
        except Exception:
            db_url = os.environ.get("DATABASE_URL", "") or ""
        db_path = db_url.replace("sqlite:///", "") if db_url.startswith("sqlite") else ""
        base = os.path.dirname(db_path) if db_path else os.getcwd()
        if not base:
            base = os.getcwd()
        return os.path.join(base, "images", str(self._product_id))

    def _load_product_images(self) -> None:
        """Load images from DB and show the first one."""
        if not self._product_id:
            self._image_list = []
            self._image_index = 0
            self._update_image_display()
            return
        try:
            self._image_list = list(get_product_images(self._product_id))
        except Exception:
            self._image_list = []
        self._image_index = 0
        self._update_image_display()

    def _update_image_display(self) -> None:
        """Refresh the preview label and navigation for current image."""
        count = len(self._image_list)
        # Always rebuild the thumbnail strip so it reflects the latest list.
        self._rebuild_thumbnail_strip()
        if count == 0:
            self._img_preview.setText("Drop image here, click Import,\nor use Find PAI Images")
            self._img_preview.setPixmap(QPixmap())
            if hasattr(self, "_img_preview_empty_qss"):
                self._img_preview.setStyleSheet(self._img_preview_empty_qss)
            self._img_info_label.setText("0 / 0")
            self._img_prev_btn.setEnabled(False)
            self._img_next_btn.setEnabled(False)
            self._img_delete_btn.setEnabled(False)
            return

        self._image_index = max(0, min(self._image_index, count - 1))
        img = self._image_list[self._image_index]
        path = img.get("path", "")

        import os
        if os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                # Fit the entire image inside the preview (no cropping). Use
                # the current label size minus padding so resizing the dialog
                # rescales the image too (see resizeEvent).
                avail_w = max(200, self._img_preview.width() - 24)
                avail_h = max(200, self._img_preview.height() - 24)
                scaled = pix.scaled(
                    avail_w, avail_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._img_preview.setPixmap(scaled)
                if hasattr(self, "_img_preview_filled_qss"):
                    self._img_preview.setStyleSheet(self._img_preview_filled_qss)
            else:
                self._img_preview.setText("(Cannot load image)")
                if hasattr(self, "_img_preview_empty_qss"):
                    self._img_preview.setStyleSheet(self._img_preview_empty_qss)
        else:
            self._img_preview.setText(f"(File not found: {path})")
            if hasattr(self, "_img_preview_empty_qss"):
                self._img_preview.setStyleSheet(self._img_preview_empty_qss)

        # Caption: filename + dims + source badge
        source = (img.get("source") or "").strip()
        filename = img.get("filename") or ""
        w = img.get("width") or 0
        h = img.get("height") or 0
        dim_part = f"  ·  {w}×{h}" if w and h else ""
        src_part = f"  ·  {source.upper()}" if source else ""
        self._img_info_label.setText(
            f"{self._image_index + 1} / {count}{dim_part}{src_part}"
        )
        self._img_info_label.setToolTip(filename)
        self._img_prev_btn.setEnabled(self._image_index > 0)
        self._img_next_btn.setEnabled(self._image_index < count - 1)
        self._img_delete_btn.setEnabled(True)

    def _navigate_image(self, delta: int) -> None:
        self._image_index += delta
        self._update_image_display()

    def _rebuild_thumbnail_strip(self) -> None:
        """Recreate the horizontal thumbnail strip from `self._image_list`."""
        if not hasattr(self, "_img_thumb_layout"):
            return
        # Remove existing thumbnail buttons (keep the trailing stretch).
        for btn in getattr(self, "_img_thumb_buttons", []):
            try:
                self._img_thumb_layout.removeWidget(btn)
                btn.deleteLater()
            except Exception:
                pass
        self._img_thumb_buttons = []

        import os
        thumb_size = 72
        for i, img in enumerate(self._image_list):
            btn = QPushButton()
            btn.setFixedSize(thumb_size + 8, thumb_size + 8)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.setChecked(i == self._image_index)
            path = img.get("path", "")
            tip_bits = [img.get("filename") or ""]
            if img.get("source"):
                tip_bits.append(f"source: {img['source']}")
            btn.setToolTip("\n".join(t for t in tip_bits if t))
            if path and os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    scaled = pix.scaled(
                        thumb_size, thumb_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    btn.setIcon(QIcon(scaled))
                    btn.setIconSize(scaled.size())
            btn.setStyleSheet(
                "QPushButton { background-color: #ffffff; border: 1px solid #c8c8c8; "
                "border-radius: 4px; padding: 2px; } "
                "QPushButton:hover { border: 1px solid #4a7ba7; } "
                "QPushButton:checked { border: 2px solid #4a7ba7; "
                "background-color: #eaf2f9; }"
            )
            btn.clicked.connect(lambda _checked=False, idx=i: self._select_image(idx))
            # Insert before the trailing stretch (last item).
            self._img_thumb_layout.insertWidget(
                self._img_thumb_layout.count() - 1, btn
            )
            self._img_thumb_buttons.append(btn)

    def _select_image(self, idx: int) -> None:
        if 0 <= idx < len(self._image_list):
            self._image_index = idx
            self._update_image_display()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        # Re-scale the current preview pixmap to fit the new size.
        if getattr(self, "_image_list", None):
            try:
                self._update_image_display()
            except Exception:
                pass

    def _on_import_image(self) -> None:
        """Import an image file from disk."""
        if not self._product_id:
            QMessageBox.information(self, "Save First", "Save the product before adding images.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Product Image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if not path:
            return
        self._store_image_file(path)

    def _on_paste_image(self) -> None:
        """Paste an image from the clipboard."""
        if not self._product_id:
            QMessageBox.information(self, "Save First", "Save the product before adding images.")
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        image = clipboard.image()
        if image.isNull():
            QMessageBox.information(self, "No Image", "No image found on clipboard.")
            return
        import os
        import tempfile
        # Save clipboard image to a temp file then import it
        images_dir = self._images_dir_for_product()
        os.makedirs(images_dir, exist_ok=True)
        counter = len(self._image_list) + 1
        filename = f"pasted_{counter}.png"
        dest = os.path.join(images_dir, filename)
        while os.path.exists(dest):
            counter += 1
            filename = f"pasted_{counter}.png"
            dest = os.path.join(images_dir, filename)
        pixmap = QPixmap.fromImage(image)
        pixmap.save(dest, "PNG")
        file_size = os.path.getsize(dest)
        position = len(self._image_list)
        add_product_image(
            self._product_id,
            filename=filename,
            path=dest,
            position=position,
            width=pixmap.width(),
            height=pixmap.height(),
            file_size=file_size,
            source="clipboard",
        )
        self._load_product_images()
        self._image_index = len(self._image_list) - 1
        self._update_image_display()

    def _import_image_from_path(self, path: str) -> None:
        """Import an image via drag & drop."""
        if not self._product_id:
            QMessageBox.information(self, "Save First", "Save the product before adding images.")
            return
        self._store_image_file(path)

    def _store_image_file(self, source_path: str) -> None:
        """Copy an image into the images folder and register in DB."""
        import os
        import shutil

        images_dir = self._images_dir_for_product()
        os.makedirs(images_dir, exist_ok=True)

        filename = os.path.basename(source_path)
        dest_path = os.path.join(images_dir, filename)

        # Avoid overwriting by appending counter
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            filename = f"{base}_{counter}{ext}"
            dest_path = os.path.join(images_dir, filename)
            counter += 1

        shutil.copy2(source_path, dest_path)

        # Get file info
        file_size = os.path.getsize(dest_path)
        pix = QPixmap(dest_path)
        width = pix.width() if not pix.isNull() else 0
        height = pix.height() if not pix.isNull() else 0

        position = len(self._image_list)
        add_product_image(
            self._product_id,
            filename=filename,
            path=dest_path,
            position=position,
            width=width,
            height=height,
            file_size=file_size,
            source="import",
        )

        self._load_product_images()
        self._image_index = len(self._image_list) - 1
        self._update_image_display()

    def _on_delete_image(self) -> None:
        """Delete the currently displayed image."""
        if not self._image_list:
            return
        img = self._image_list[self._image_index]
        reply = QMessageBox.question(
            self, "Delete Image",
            f"Delete image '{img.get('filename', '')}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_product_image(img["id"])
            # Also remove file from disk
            import os
            path = img.get("path", "")
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to delete image: {e}")
            return

        self._load_product_images()

    def _download_and_store_image(self, url: str, source: str = "pai") -> None:
        """Download an image from a URL and store it."""
        if not self._product_id:
            return
        import os
        import hashlib
        try:
            import requests
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except Exception:
            return

        # Determine filename from URL or content type
        from urllib.parse import urlparse
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or "product_image.jpg"
        if "." not in filename:
            ct = resp.headers.get("content-type", "")
            ext = ".jpg"
            if "png" in ct:
                ext = ".png"
            elif "webp" in ct:
                ext = ".webp"
            filename += ext

        images_dir = self._images_dir_for_product()
        os.makedirs(images_dir, exist_ok=True)
        dest_path = os.path.join(images_dir, filename)

        # Avoid overwriting
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            filename = f"{base}_{counter}{ext}"
            dest_path = os.path.join(images_dir, filename)
            counter += 1

        with open(dest_path, "wb") as f:
            f.write(resp.content)

        file_size = os.path.getsize(dest_path)
        pix = QPixmap(dest_path)
        width = pix.width() if not pix.isNull() else 0
        height = pix.height() if not pix.isNull() else 0
        img_hash = hashlib.md5(resp.content).hexdigest()

        position = len(self._image_list) if self._image_list else 0
        add_product_image(
            self._product_id,
            filename=filename,
            path=dest_path,
            position=position,
            width=width,
            height=height,
            file_size=file_size,
            hash_val=img_hash,
            source=source,
        )

    # ==================================================================
    # SCRAPER — Find Cross References / Check Competitor Pricing
    # ==================================================================

    def _on_find_xrefs(self) -> None:
        """Scrape cross-references for this product."""
        self._run_edit_scrape("xref")

    def _on_check_pricing(self) -> None:
        """Scrape competitor pricing for this product."""
        self._run_edit_scrape("pricing")

    def _run_edit_scrape(self, job_type: str) -> None:
        if self._edit_scrape_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        if self._create_mode and not self._product_id and job_type in ("xref", "both", "images"):
            QMessageBox.information(
                self,
                "Save Product First",
                "Save this new product before running PAI / cross-reference scrapes so results can be attached to it.",
            )
            return

        # Resolve search value: quick-scrape bar → manual override → vendor SKU → OEM part# → block
        quick = self._quick_scrape_edit.text().strip() if self._quick_scrape_edit else ""
        # Ignore JAKS- prefixed SKUs — they're internal, not searchable on external sites
        if quick.upper().startswith("JAKS-"):
            quick = ""
        manual = self._scrape_override_edit.text().strip()
        vendor_sku = self._pai_sku_edit.text().strip()
        oem_pn = self._oem_pn_edit.text().strip()
        search_value = quick or manual or vendor_sku or oem_pn

        # Collect ALL non-empty alternate search terms for fallback
        alt_terms: list[str] = []
        for candidate in (oem_pn, vendor_sku, manual, quick):
            if candidate and candidate != search_value and candidate not in alt_terms:
                alt_terms.append(candidate)
        # Also try extracting part number from JAKS SKU as last resort
        jaks_sku = self._sku_edit.text().strip()
        if jaks_sku.upper().startswith("JAKS-"):
            remainder = jaks_sku[5:]  # strip "JAKS-"
            parts = remainder.split("-", 1)
            # If "JAKS-PAI-2486744", extract "2486744"
            if len(parts) == 2 and parts[0].isalpha() and len(parts[0]) <= 6:
                extracted = parts[1]
            else:
                extracted = remainder
            if extracted and extracted != search_value and extracted not in alt_terms:
                alt_terms.append(extracted)

        # Determine which source was used for display
        if quick:
            source_used = "Quick Search"
        elif manual:
            source_used = "Manual Override"
        elif vendor_sku:
            source_used = "Vendor SKU"
        elif oem_pn:
            source_used = "OEM Part Number"
        else:
            source_used = None

        if not search_value:
            QMessageBox.warning(
                self, "No Search Value",
                "No Vendor SKU or OEM Part Number available for search.\n\n"
                "Enter a value in the Manual Search field, or fill in\n"
                "Vendor SKU / OEM Part Number first.",
            )
            return

        # Show pre-scrape info
        label_map = {"xref": "crosses", "pricing": "pricing", "images": "PAI images", "both": "pricing + crosses + images"}
        label = label_map.get(job_type, job_type)
        # No longer auto-trigger PAI — remove PAI from source list
        sources = "PAI" if job_type == "images" else ("DPD+ATL+FP+HHP+PAI" if job_type == "both" else "DPD+ATL+FP+HHP")
        self._scrape_search_info.setText(
            f"Searching {sources} using: {search_value}  (Source: {source_used})"
            + (f"  +alt: {', '.join(alt_terms)}" if alt_terms else "")
        )

        # Debug info
        if self._debug_toggle.isChecked():
            debug = (
                f"job={job_type}  value={search_value!r}  "
                f"alt_terms={alt_terms!r}  "
                f"vendor_sku={vendor_sku!r}  oem={oem_pn!r}  "
                f"manual={manual!r}  product_id={self._product_id}"
            )
            self._scrape_debug_label.setText(debug)
            self._scrape_debug_label.show()
        else:
            self._scrape_debug_label.hide()

        self._find_xref_btn.setEnabled(False)
        self._check_pricing_btn.setEnabled(False)
        if hasattr(self, "_img_pai_btn"):
            self._img_pai_btn.setEnabled(False)
        self._edit_scrape_progress.setValue(0)
        self._edit_scrape_progress.setMaximum(0)
        self._edit_scrape_progress.show()
        self._edit_scrape_label.setText(f"Scraping {label} for {search_value}…")

        # Header progress
        self._quick_scrape_all_btn.setEnabled(False)
        self._header_scrape_progress.setValue(0)
        self._header_scrape_progress.setMaximum(0)
        self._header_scrape_progress.show()
        self._header_scrape_label.setText(f"Scraping {label}…")
        self._header_scrape_label.show()

        self._edit_scrape_worker = ScraperWorker(
            job_type, search_value,
            alt_terms=alt_terms or None,
            product_id=self._product_id,
        )
        self._last_scrape_job_type = job_type
        self._edit_scrape_worker.progress.connect(self._on_edit_scrape_progress)
        self._edit_scrape_worker.finished.connect(self._on_edit_scrape_done)
        self._edit_scrape_worker.error.connect(self._on_edit_scrape_error)
        self._edit_scrape_worker.start()

    def _on_edit_scrape_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self._edit_scrape_progress.setMaximum(total)
            self._edit_scrape_progress.setValue(current)
            self._header_scrape_progress.setMaximum(total)
            self._header_scrape_progress.setValue(current)
        self._edit_scrape_label.setText(message)
        # Shorten for header
        short = message if len(message) < 30 else message[:27] + "…"
        self._header_scrape_label.setText(short)

    def _on_edit_scrape_done(self, result: dict) -> None:
        self._edit_scrape_worker = None
        self._find_xref_btn.setEnabled(True)
        self._check_pricing_btn.setEnabled(True)
        if hasattr(self, "_img_pai_btn") and not self._create_mode:
            self._img_pai_btn.setEnabled(True)
        self._edit_scrape_progress.hide()
        self._quick_scrape_all_btn.setEnabled(True)
        self._header_scrape_progress.hide()

        # Count total results across sub-results
        total_found = 0
        errors = []
        for key in ("xref", "pricing", "images"):
            sub = result.get(key, {})
            if isinstance(sub, dict):
                total_found += int(sub.get("found", 0))
                errors.extend(sub.get("errors", []))
        # Track images downloaded separately so we can show it in status
        img_sub = result.get("images") or {}
        images_downloaded = int(img_sub.get("downloaded", 0)) if isinstance(img_sub, dict) else 0
        # Fallback for flat result
        if not total_found and "found" in result:
            total_found = int(result.get("found", 0))
            errors = result.get("errors", [])

        search_value = self._scrape_override_edit.text().strip() \
            or self._pai_sku_edit.text().strip() \
            or self._oem_pn_edit.text().strip() or "?"

        if total_found > 0:
            extra = f", {images_downloaded} image(s)" if images_downloaded else ""
            self._edit_scrape_label.setText(
                f"✔ Results found ({total_found} items{extra})"
            )
            self._edit_scrape_label.setStyleSheet("color: #2e7d32; font-size: 9pt;")
            self._header_scrape_label.setText(f"✔ {total_found} found")
            self._header_scrape_label.setStyleSheet(
                "color: #a0e080; font-size: 8pt; background: transparent;"
            )
        else:
            self._edit_scrape_label.setText(
                f"✖ No results found for: {search_value}"
            )
            self._edit_scrape_label.setStyleSheet("color: #c62828; font-size: 9pt;")
            self._header_scrape_label.setText("✖ No results")
            self._header_scrape_label.setStyleSheet(
                "color: #ff9999; font-size: 8pt; background: transparent;"
            )

        if errors:
            err_text = f"Errors: {'; '.join(str(e) for e in errors)}"
            self._scrape_debug_label.setText(err_text)
            self._scrape_debug_label.show()
            # Also show errors in status line if no results found
            if total_found == 0:
                self._edit_scrape_label.setText(
                    f"✖ No results — {len(errors)} source(s) had errors"
                )

        # If the user explicitly ran an images-only scrape and PAI is not
        # configured, pop a dialog so they understand why nothing happened.
        img_errors = []
        if isinstance(img_sub, dict):
            img_errors = list(img_sub.get("errors") or [])
        pai_not_configured = any(
            "PAI not configured" in str(e) or "PAI images: PAI not configured" in str(e)
            for e in img_errors
        )
        if pai_not_configured and images_downloaded == 0:
            # Only nag for the explicit "images" job, or for "both" when PAI was the only failure
            should_nag = (
                getattr(self, "_last_scrape_job_type", None) == "images"
                or (getattr(self, "_last_scrape_job_type", None) == "both" and total_found == 0)
            )
            if should_nag:
                QMessageBox.information(
                    self,
                    "PAI Not Configured",
                    "PAI image scraping is disabled.\n\n"
                    "To enable it, edit your .env file and set:\n"
                    "    PAI_ENABLE=1\n"
                    "    PAI_USER=<your PAI email>\n"
                    "    PAI_PASS=<your PAI password>\n\n"
                    "Then restart the app.",
                )

        # Refresh pending cross-references (scraper results land here)
        self._load_pending_xrefs()

        # Auto-open review dialog when xref/pricing results were found
        # (images-only jobs skip the review dialog and instead refresh the
        # images section below)
        xref_or_pricing_found = 0
        for key in ("xref", "pricing"):
            sub = result.get(key, {})
            if isinstance(sub, dict):
                xref_or_pricing_found += int(sub.get("found", 0))
        if xref_or_pricing_found > 0 and self._product_id:
            dlg = ScraperReviewDialog(product_id=self._product_id, parent=self)
            dlg.exec()
            # After dialog closes, refresh inline tables
            self._load_pending_xrefs()

        # Navigate to CROSS-REFERENCES section so user sees pending items
        xref_section_idx = next(
            (i for i, (name, *_) in enumerate(self._section_boxes)
             if "CROSS" in name),
            None,
        )
        if xref_section_idx is not None and xref_or_pricing_found > 0:
            self._nav_to_section(xref_section_idx)

        # Refresh interchanges table in case any were accepted
        if self._product:
            self._load_interchanges(self._product)
        # Refresh competitor prices in case any were accepted
        self._load_competitor_prices()

        # Refresh product images if any were downloaded from PAI
        if images_downloaded > 0 and self._product_id:
            try:
                self._load_product_images()
                self._image_index = max(0, len(self._image_list) - 1)
                self._update_image_display()
            except Exception:
                pass

    def _on_edit_scrape_error(self, error_msg: str) -> None:
        self._edit_scrape_worker = None
        self._find_xref_btn.setEnabled(True)
        self._check_pricing_btn.setEnabled(True)
        if hasattr(self, "_img_pai_btn") and not self._create_mode:
            self._img_pai_btn.setEnabled(True)
        self._edit_scrape_progress.hide()
        self._quick_scrape_all_btn.setEnabled(True)
        self._header_scrape_progress.hide()
        self._header_scrape_label.setText("✖ Failed")
        self._header_scrape_label.setStyleSheet(
            "color: #ff9999; font-size: 8pt; background: transparent;"
        )

        search_value = self._scrape_override_edit.text().strip() \
            or self._pai_sku_edit.text().strip() \
            or self._oem_pn_edit.text().strip() or "?"
        self._edit_scrape_label.setText(
            f"✖ Scrape failed for: {search_value}"
        )
        self._edit_scrape_label.setStyleSheet("color: #c62828; font-size: 9pt;")
        self._scrape_search_info.setText("")

        if self._debug_toggle.isChecked():
            self._scrape_debug_label.setText(f"Error: {error_msg}")
            self._scrape_debug_label.show()

        QMessageBox.warning(self, "Scrape Error", f"Scrape failed:\n{error_msg}")
