"""UI dialogs for JAKS Scraper.

Centralizes QMessageBox wrappers, input dialogs, and confirmation flows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Callable, Any
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QComboBox,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QMessageBox,
    QInputDialog,
    QFileDialog,
    QWidget,
    QPushButton,
    QScrollArea,
    QFrame,
)

if TYPE_CHECKING:
    from models import Product

__all__ = [
    "confirm_dialog",
    "error_dialog",
    "info_dialog",
    "warning_dialog",
    "select_range_dialog",
    "set_margin_dialog",
    "adjust_cog_dialog",
    "sku_conflict_dialog",
    "preupload_diff_dialog",
    "pai_alternate_dialog",
    "ProductDetailDialog",
    "WizardDialog",
    "PAICredentialsDialog",
    "SingleURLDialog",
]


# =============================================================================
# Simple Message Boxes
# =============================================================================

def confirm_dialog(
    parent: QWidget,
    title: str,
    message: str,
    info: str = "",
    *,
    yes_text: str = "Yes",
    no_text: str = "No",
) -> bool:
    """Show a Yes/No confirmation dialog. Returns True if Yes clicked."""
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setWindowTitle(title)
    msg.setText(message)
    if info:
        msg.setInformativeText(info)
    yes_btn = msg.addButton(yes_text, QMessageBox.ButtonRole.YesRole)
    msg.addButton(no_text, QMessageBox.ButtonRole.NoRole)
    msg.setDefaultButton(yes_btn)
    msg.exec()
    return msg.clickedButton() == yes_btn


def error_dialog(parent: QWidget, title: str, message: str, details: str = "") -> None:
    """Show an error dialog."""
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Critical)
    msg.setWindowTitle(title)
    msg.setText(message)
    if details:
        msg.setDetailedText(details)
    msg.exec()


def info_dialog(parent: QWidget, title: str, message: str, details: str = "") -> None:
    """Show an information dialog."""
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setWindowTitle(title)
    msg.setText(message)
    if details:
        msg.setDetailedText(details)
    msg.exec()


def warning_dialog(parent: QWidget, title: str, message: str, info: str = "") -> None:
    """Show a warning dialog."""
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle(title)
    msg.setText(message)
    if info:
        msg.setInformativeText(info)
    msg.exec()


# =============================================================================
# Input Dialogs
# =============================================================================

def select_range_dialog(
    parent: QWidget,
    row_count: int,
) -> tuple[int, int] | None:
    """
    Open dialogs to select a range of rows.
    Returns (start, end) as 1-indexed row numbers, or None if cancelled.
    """
    if row_count <= 0:
        info_dialog(parent, "Select Range", "No rows in table.")
        return None

    start, ok1 = QInputDialog.getInt(
        parent,
        "Select Range",
        f"Start row (1 to {row_count}):",
        value=1,
        min=1,
        max=row_count,
    )
    if not ok1:
        return None

    end, ok2 = QInputDialog.getInt(
        parent,
        "Select Range",
        f"End row ({start} to {row_count}):",
        value=min(start + 24, row_count),
        min=start,
        max=row_count,
    )
    if not ok2:
        return None

    return (start, end)


def set_margin_dialog(
    parent: QWidget,
    default_value: float = 30.0,
    min_margin_warn: float = 15.0,
) -> float | None:
    """
    Open a dialog to set global margin percentage.
    Returns the margin value, or None if cancelled.
    Includes low margin warning confirmation.
    """
    val, ok = QInputDialog.getDouble(
        parent,
        "Set Global Margin",
        "Target My Margin % (applies Selling Price override):",
        default_value,
        0.0,
        95.0,
        1,
    )
    if not ok:
        return None

    target_pct = float(val)

    # Low margin confirmation
    if target_pct < min_margin_warn:
        if not confirm_dialog(
            parent,
            "Low margin",
            f"Set My Margin to {target_pct:.1f}% (<{min_margin_warn:.0f}%).",
            "Proceed anyway?",
        ):
            return None

    return target_pct


def adjust_cog_dialog(parent: QWidget) -> float | None:
    """
    Open a dialog to adjust COG by percentage.
    Returns the adjustment percentage, or None if cancelled.
    """
    val, ok = QInputDialog.getDouble(
        parent,
        "Adjust COG",
        "Adjust My cost by % (e.g., 10 = +10%, -5 = -5%):",
        0.0,
        -50.0,
        100.0,
        1,
    )
    if not ok:
        return None
    return float(val)


# =============================================================================
# Settings Dialogs
# =============================================================================

class PAICredentialsDialog(QDialog):
    """Dialog for entering PAI portal credentials."""

    def __init__(self, parent: QWidget, username: str = "", password: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("PAI Credentials")
        self.setMinimumWidth(350)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Info label
        info = QLabel("Enter your PAI portal credentials for cost enrichment.")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Username
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Email / Username")
        self.username_input.setText(username)
        layout.addWidget(QLabel("Username:"))
        layout.addWidget(self.username_input)

        # Password
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setText(password)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.password_input)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Focus username if empty, else password
        if not username:
            self.username_input.setFocus()
        else:
            self.password_input.setFocus()

    def get_credentials(self) -> tuple[str, str]:
        """Return (username, password) tuple."""
        return self.username_input.text().strip(), self.password_input.text()


class SingleURLDialog(QDialog):
    """Dialog for scraping a single URL."""

    def __init__(self, parent: QWidget, last_url: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Scrape Single URL")
        self.setMinimumWidth(500)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Info label
        info = QLabel("Enter a product URL from Highway and Heavy Parts:")
        info.setWordWrap(True)
        layout.addWidget(info)

        # URL input
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://highwayandheavyparts.com/product/...")
        self.url_input.setText(last_url)
        layout.addWidget(self.url_input)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Scrape")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.url_input.setFocus()
        self.url_input.selectAll()

    def get_url(self) -> str:
        """Return the entered URL."""
        return self.url_input.text().strip()


# =============================================================================
# Complex Dialogs
# =============================================================================

def sku_conflict_dialog(
    parent: QWidget,
    conflicts: list[dict],
) -> bool:
    """
    Show SKU conflicts and ask whether to proceed.
    Returns True if user wants to continue anyway.
    """
    if not conflicts:
        return True

    lines = []
    for c in conflicts[:50]:  # Limit display
        sku = c.get("sku", "?")
        handle = c.get("handle", "?")
        existing_id = c.get("existing_id", "?")
        lines.append(f"• {sku} ({handle}) - existing ID: {existing_id}")

    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle("SKU Conflicts Detected")
    msg.setText(f"{len(conflicts)} product(s) already exist in Shopify.")
    msg.setInformativeText("These will be UPDATED. Continue?")
    msg.setDetailedText("\n".join(lines))
    
    continue_btn = msg.addButton("Continue (Update)", QMessageBox.ButtonRole.AcceptRole)
    msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    msg.setDefaultButton(continue_btn)
    msg.exec()
    
    return msg.clickedButton() == continue_btn


def preupload_diff_dialog(
    parent: QWidget,
    diffs: list[dict],
    publish: bool,
    include_core: bool,
) -> bool:
    """
    Show pre-upload diff summary and ask for confirmation.
    Returns True if user confirms upload.
    """
    lines: list[str] = []
    updates = 0
    creates = 0

    for d in diffs:
        sku = str(d.get("sku", "") or "").strip()
        handle = str(d.get("handle", "") or "").strip()
        exists = bool(d.get("exists", False))
        
        if exists:
            updates += 1
            hdr = f"{sku} ({handle}) — UPDATE"
        else:
            creates += 1
            hdr = f"{sku} ({handle}) — CREATE"

        price_old = d.get("price_old")
        price_new = d.get("price_new")
        cost_old = d.get("cost_old")
        cost_new = d.get("cost_new")
        tmpl_old = d.get("template_old")
        tmpl_new = d.get("template_new")
        tags_added = d.get("tags_added") or []
        tags_removed = d.get("tags_removed") or []
        mf_added = d.get("metafields_added") or []
        mf_removed = d.get("metafields_removed") or []
        mf_changed = d.get("metafields_changed") or {}

        lines.append(hdr)
        lines.append(f"  Price: {price_old or '—'} → {price_new or '—'}")
        lines.append(f"  Cost:  {cost_old or '—'} → {cost_new or '—'}")
        
        if (tmpl_old or tmpl_new) and (tmpl_old != tmpl_new):
            lines.append(f"  Template: {tmpl_old or '—'} → {tmpl_new or '—'}")
        
        if tags_added or tags_removed:
            add_s = ("+" + ", +".join(tags_added)) if tags_added else ""
            rem_s = ("-" + ", -".join(tags_removed)) if tags_removed else ""
            lines.append(f"  Tags:  {', '.join([s for s in [add_s, rem_s] if s])}")
        
        if mf_added or mf_removed or mf_changed:
            if mf_added:
                lines.append("  Metafields added: " + ", ".join(mf_added))
            if mf_removed:
                lines.append("  Metafields removed: " + ", ".join(mf_removed))
            for k, ch in mf_changed.items():
                old_v = str(ch.get("old", "") or "")[:80]
                new_v = str(ch.get("new", "") or "")[:80]
                lines.append(f"  Metafield {k}: '{old_v}' → '{new_v}'")
        
        lines.append("")

    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Information)
    msg.setWindowTitle("Pre-Upload Diff")
    msg.setText("Review changes before uploading")
    msg.setInformativeText(
        f"Updates: {updates} | Creates: {creates} | Publish: {publish} | Include core: {include_core}"
    )
    msg.setDetailedText("\n".join(lines).strip() or "No diffs computed")
    
    cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    upload_btn = msg.addButton("Upload", QMessageBox.ButtonRole.AcceptRole)
    msg.setDefaultButton(cancel_btn)
    msg.exec()

    return msg.clickedButton() == upload_btn


def pai_alternate_dialog(
    parent: QWidget,
    alternates: list[str],
    current_sku: str = "",
) -> str | None:
    """
    Show PAI alternate SKUs and let user select one.
    Returns selected SKU or None if cancelled.
    """
    if not alternates:
        info_dialog(parent, "PAI Alternates", "No alternate SKUs available.")
        return None

    dialog = QDialog(parent)
    dialog.setWindowTitle("Select PAI Alternate")
    dialog.setMinimumWidth(400)
    
    layout = QVBoxLayout(dialog)
    
    layout.addWidget(QLabel(f"Current SKU: {current_sku}" if current_sku else "Select an alternate PAI SKU:"))
    
    list_widget = QListWidget()
    for alt in alternates:
        item = QListWidgetItem(alt)
        list_widget.addItem(item)
    list_widget.setCurrentRow(0)
    layout.addWidget(list_widget)
    
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    
    if dialog.exec() == QDialog.DialogCode.Accepted:
        current = list_widget.currentItem()
        if current:
            return current.text()
    
    return None


# =============================================================================
# Product Detail Dialog
# =============================================================================

class ProductDetailDialog(QDialog):
    """Dialog showing detailed product information."""
    
    # Signal emitted when cross-reference is adopted
    cross_reference_adopted = Signal(object)  # Emits the product

    def __init__(self, parent: QWidget, product: Any) -> None:
        super().__init__(parent)
        self.product = product
        self.setWindowTitle("Product Details")
        self.setMinimumSize(600, 500)
        self.adopt_btn = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        
        prod = self.product
        
        # Basic Info Group
        basic_group = QGroupBox("Basic Information")
        basic_layout = QVBoxLayout(basic_group)
        
        fields = [
            ("SKU", getattr(prod, "sku", "")),
            ("Handle", getattr(prod, "handle", "")),
            ("Title", getattr(prod, "title", "")),
            ("Manufacturer", getattr(prod, "manufacturer", "")),
            ("Category", getattr(prod, "category", "")),
            ("Brand", getattr(prod, "brand", "")),
            ("Engine", getattr(prod, "engine", "")),
            ("Condition", getattr(prod, "condition", "")),
        ]
        
        for label, value in fields:
            row = QHBoxLayout()
            lbl = QLabel(f"<b>{label}:</b>")
            lbl.setFixedWidth(100)
            row.addWidget(lbl)
            val = QLabel(str(value or "—"))
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(val, 1)
            basic_layout.addLayout(row)
        
        content_layout.addWidget(basic_group)
        
        # Part Numbers Group
        pn_group = QGroupBox("Part Numbers")
        pn_layout = QVBoxLayout(pn_group)
        
        pn_fields = [
            ("Primary PN", getattr(prod, "primary_part_number", "")),
            ("Secondary PN", getattr(prod, "secondary_part_number", "")),
            ("OEM PN", getattr(prod, "oem_part_number", "")),
            ("PAI SKU", getattr(prod, "pai_sku", "")),
            ("Supplier PN", getattr(prod, "supplier_part_number", "")),
        ]
        
        for label, value in pn_fields:
            if value:
                row = QHBoxLayout()
                lbl = QLabel(f"<b>{label}:</b>")
                lbl.setFixedWidth(100)
                row.addWidget(lbl)
                val = QLabel(str(value))
                val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                row.addWidget(val, 1)
                pn_layout.addLayout(row)
        
        content_layout.addWidget(pn_group)
        
        # Pricing Group
        pricing_group = QGroupBox("Pricing")
        pricing_layout = QVBoxLayout(pricing_group)
        
        pricing = getattr(prod, "pricing", {}) or {}
        if not isinstance(pricing, dict):
            pricing = {}
        
        price_fields = [
            ("HHP Price", pricing.get("price")),
            ("Core Charge", pricing.get("core_charge")),
            ("My Cost", getattr(prod, "cost", None)),
            ("PAI Cost", getattr(prod, "pai_cost", None)),
            ("Supplier", getattr(prod, "supplier", "HHP")),
            ("Stage", pricing.get("stage", 1)),
        ]
        
        for label, value in price_fields:
            row = QHBoxLayout()
            lbl = QLabel(f"<b>{label}:</b>")
            lbl.setFixedWidth(100)
            row.addWidget(lbl)
            if isinstance(value, (int, float)) and label not in ("Stage",):
                val = QLabel(f"${value:.2f}" if value else "—")
            else:
                val = QLabel(str(value) if value else "—")
            row.addWidget(val, 1)
            pricing_layout.addLayout(row)
        
        content_layout.addWidget(pricing_group)
        
        # PAI Info Group (if enriched)
        pai_status = getattr(prod, "pai_status", "")
        pai_cross_ref = getattr(prod, "pai_cross_reference", {}) or pricing.get("pai_cross_reference", {})
        
        if pai_status or pai_cross_ref:
            pai_group = QGroupBox("PAI Enrichment")
            pai_layout = QVBoxLayout(pai_group)
            
            pai_fields = [
                ("PAI Status", pai_status or "—"),
                ("PAI Cross", "Yes" if pricing.get("pai_cross") else "No"),
            ]
            
            # Stock info
            stock = getattr(prod, "pai_stock", None) or pricing.get("pai_stock")
            if isinstance(stock, dict) and stock:
                avail = sum(1 for v in stock.values() if (isinstance(v, int) and v > 0) or str(v).lower() in ("in_stock", "available"))
                pai_fields.append(("Stock Locations", f"{avail} / {len(stock)}"))
            
            for label, value in pai_fields:
                row = QHBoxLayout()
                lbl = QLabel(f"<b>{label}:</b>")
                lbl.setFixedWidth(100)
                row.addWidget(lbl)
                val = QLabel(str(value))
                row.addWidget(val, 1)
                pai_layout.addLayout(row)
            
            # PAI Cross-Reference Section (if available)
            if pai_cross_ref and pai_cross_ref.get("pai_sku"):
                cross_ref_group = QGroupBox("🔀 PAI Cross-Reference Available")
                cross_ref_layout = QVBoxLayout(cross_ref_group)
                
                searched = pai_cross_ref.get("searched_sku", "")
                pai_sku = pai_cross_ref.get("pai_sku", "")
                cost = pai_cross_ref.get("cost", 0)
                adopted = pai_cross_ref.get("adopted", False)
                
                info_text = f"Searched: <b>{searched}</b> → Found PAI SKU: <b>{pai_sku}</b>"
                if cost:
                    info_text += f" @ <b>${cost:.2f}</b>"
                
                info_label = QLabel(info_text)
                cross_ref_layout.addWidget(info_label)
                
                if adopted:
                    status_label = QLabel("✅ <span style='color: green;'>Cross-reference adopted</span>")
                    cross_ref_layout.addWidget(status_label)
                else:
                    hint_label = QLabel("<i>Click 'Adopt PAI SKU' to use this PAI part number and cost</i>")
                    hint_label.setStyleSheet("color: #666;")
                    cross_ref_layout.addWidget(hint_label)
                    
                    self.adopt_btn = QPushButton(f"🔀 Adopt PAI SKU: {pai_sku}")
                    self.adopt_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #4a6741;
                            color: white;
                            padding: 8px 16px;
                            border-radius: 4px;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background-color: #5a7751;
                        }
                    """)
                    self.adopt_btn.clicked.connect(self._adopt_cross_reference)
                    cross_ref_layout.addWidget(self.adopt_btn)
                
                pai_layout.addWidget(cross_ref_group)
            
            content_layout.addWidget(pai_group)
        
        # URLs Group
        url = getattr(prod, "url", "") or getattr(prod, "source_url", "")
        if url:
            url_group = QGroupBox("URLs")
            url_layout = QVBoxLayout(url_group)
            
            url_label = QLabel(f'<a href="{url}">{url}</a>')
            url_label.setOpenExternalLinks(True)
            url_label.setWordWrap(True)
            url_layout.addWidget(url_label)
            
            content_layout.addWidget(url_group)
        
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # Close button
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _adopt_cross_reference(self) -> None:
        """Adopt the PAI cross-reference SKU for this product."""
        try:
            from sources.pai_enrichment import adopt_pai_cross_reference
            
            if adopt_pai_cross_reference(self.product):
                # Update button state
                if self.adopt_btn:
                    self.adopt_btn.setText("✅ Adopted!")
                    self.adopt_btn.setEnabled(False)
                    self.adopt_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #2e7d32;
                            color: white;
                            padding: 8px 16px;
                            border-radius: 4px;
                        }
                    """)
                
                # Emit signal to notify parent
                self.cross_reference_adopted.emit(self.product)
                
                # Show success message
                QMessageBox.information(
                    self,
                    "PAI Cross-Reference Adopted",
                    f"Successfully adopted PAI SKU: {self.product.pai_sku}\n"
                    f"Cost: ${self.product.pai_cost:.2f}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Adoption Failed",
                    "Failed to adopt PAI cross-reference. No data available."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to adopt cross-reference: {e}"
            )


# =============================================================================
# Wizard Dialog
# =============================================================================

class WizardDialog(QDialog):
    """Step-by-step wizard for new users."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("JAKS Scraper Wizard")
        self.setMinimumSize(700, 500)
        self.current_step = 0
        self.steps = [
            self._step_welcome,
            self._step_select_category,
            self._step_scan,
            self._step_scrape,
            self._step_enrich,
            self._step_review,
            self._step_upload,
        ]
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        # Step indicator
        self.step_indicator = QLabel()
        self.step_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_indicator.setStyleSheet("font-size: 14px; padding: 10px;")
        layout.addWidget(self.step_indicator)
        
        # Content area
        self.content_area = QWidget()
        self.content_layout = QVBoxLayout(self.content_area)
        layout.addWidget(self.content_area, 1)
        
        # Navigation buttons
        nav_layout = QHBoxLayout()
        
        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._go_back)
        nav_layout.addWidget(self.back_btn)
        
        nav_layout.addStretch(1)
        
        self.next_btn = QPushButton("Next →")
        self.next_btn.clicked.connect(self._go_next)
        nav_layout.addWidget(self.next_btn)
        
        self.finish_btn = QPushButton("Finish")
        self.finish_btn.clicked.connect(self.accept)
        self.finish_btn.hide()
        nav_layout.addWidget(self.finish_btn)
        
        layout.addLayout(nav_layout)
        
        self._show_step(0)

    def _show_step(self, step: int) -> None:
        self.current_step = step
        
        # Clear content
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Update indicator
        step_names = ["Welcome", "Category", "Scan", "Scrape", "PAI Enrich", "Review", "Upload"]
        indicators = []
        for i, name in enumerate(step_names):
            if i < step:
                indicators.append(f"✅ {name}")
            elif i == step:
                indicators.append(f"● {name}")
            else:
                indicators.append(f"○ {name}")
        self.step_indicator.setText("  →  ".join(indicators))
        
        # Update buttons
        self.back_btn.setEnabled(step > 0)
        self.next_btn.setVisible(step < len(self.steps) - 1)
        self.finish_btn.setVisible(step == len(self.steps) - 1)
        
        # Show step content
        if 0 <= step < len(self.steps):
            self.steps[step]()

    def _go_back(self) -> None:
        if self.current_step > 0:
            self._show_step(self.current_step - 1)

    def _go_next(self) -> None:
        if self.current_step < len(self.steps) - 1:
            self._show_step(self.current_step + 1)

    def _step_welcome(self) -> None:
        title = QLabel("<h2>Welcome to JAKS Scraper</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(title)
        
        intro = QLabel(
            "<p>This wizard will guide you through the 5-phase workflow:</p>"
            "<ol>"
            "<li><b>Scan</b> - Discover products from HHP category pages</li>"
            "<li><b>Scrape</b> - Download full product details and images</li>"
            "<li><b>PAI Enrich</b> - Get supplier costs and cross-references</li>"
            "<li><b>Review</b> - Verify pricing and product data</li>"
            "<li><b>Upload</b> - Push products to Shopify</li>"
            "</ol>"
            "<p>Click <b>Next</b> to begin.</p>"
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(intro)
        
        self.content_layout.addStretch(1)

    def _step_select_category(self) -> None:
        title = QLabel("<h2>Step 1: Select Category & Manufacturer</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Choose a category and manufacturer to scrape from Highway & Heavy Parts.</p>"
            "<p>Use the dropdowns in the main window, then return here.</p>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        tip = QLabel(
            "<p><b>Tip:</b> Start with a small category to test the workflow.</p>"
        )
        tip.setStyleSheet("background: #FFF3CD; padding: 10px; border-radius: 4px;")
        self.content_layout.addWidget(tip)
        
        self.content_layout.addStretch(1)

    def _step_scan(self) -> None:
        title = QLabel("<h2>Step 2: Phase 1 - Scan</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Click <b>Phase 1: Scan</b> to discover products in the selected category.</p>"
            "<p>This is a fast operation that:</p>"
            "<ul>"
            "<li>Crawls the category listing pages</li>"
            "<li>Extracts product URLs and basic info</li>"
            "<li>Creates Stage 1 (🔍 Discovered) entries</li>"
            "</ul>"
            "<p>No images are downloaded at this stage.</p>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        self.content_layout.addStretch(1)

    def _step_scrape(self) -> None:
        title = QLabel("<h2>Step 3: Phase 2 - Scrape</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Select Stage 1 rows in the table, then click <b>Phase 2: Scrape</b>.</p>"
            "<p>This will:</p>"
            "<ul>"
            "<li>Visit each product page with Playwright browser</li>"
            "<li>Extract full specifications and descriptions</li>"
            "<li>Download and watermark product images</li>"
            "<li>Generate AI descriptions (if enabled)</li>"
            "<li>Promote to Stage 2 (🧱 Scraped)</li>"
            "</ul>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        tip = QLabel(
            "<p><b>Tip:</b> Use 'Select by Stage → Stage 1' to quickly select all discovered products.</p>"
        )
        tip.setStyleSheet("background: #FFF3CD; padding: 10px; border-radius: 4px;")
        self.content_layout.addWidget(tip)
        
        self.content_layout.addStretch(1)

    def _step_enrich(self) -> None:
        title = QLabel("<h2>Step 4: Phase 3 - PAI Enrichment</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Select Stage 2 rows, then click <b>Phase 3: PAI</b>.</p>"
            "<p>This contacts PAI (Parts Authority International) to:</p>"
            "<ul>"
            "<li>Look up supplier cost pricing</li>"
            "<li>Find cross-reference part numbers</li>"
            "<li>Check stock availability by warehouse</li>"
            "<li>Promote to Stage 3 (🧪 PAI Enriched)</li>"
            "</ul>"
            "<p><b>Note:</b> Requires PAI credentials in the Site section.</p>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        self.content_layout.addStretch(1)

    def _step_review(self) -> None:
        title = QLabel("<h2>Step 5: Phase 4 - Review</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Select Stage 3 rows, then click <b>Phase 4: Review</b>.</p>"
            "<p>Review each product's:</p>"
            "<ul>"
            "<li>Selling price and margin calculations</li>"
            "<li>Product images and descriptions</li>"
            "<li>Part number accuracy</li>"
            "<li>PAI cross-reference selections</li>"
            "</ul>"
            "<p>Products are promoted to Stage 4 (🧠 Reviewed) when approved.</p>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        tip = QLabel(
            "<p><b>Tip:</b> Double-click a row to see full product details.</p>"
        )
        tip.setStyleSheet("background: #FFF3CD; padding: 10px; border-radius: 4px;")
        self.content_layout.addWidget(tip)
        
        self.content_layout.addStretch(1)

    def _step_upload(self) -> None:
        title = QLabel("<h2>Step 6: Phase 5 - Upload</h2>")
        self.content_layout.addWidget(title)
        
        desc = QLabel(
            "<p>Select Stage 4 rows, then click <b>Phase 5: Upload</b>.</p>"
            "<p>This will:</p>"
            "<ul>"
            "<li>Create or update products in Shopify</li>"
            "<li>Upload product images</li>"
            "<li>Set metafields and tags</li>"
            "<li>Assign to collections</li>"
            "<li>Promote to Stage 5 (🚀 Uploaded)</li>"
            "</ul>"
            "<p>Use <b>Upload as Draft</b> to create unpublished products.</p>"
        )
        desc.setWordWrap(True)
        self.content_layout.addWidget(desc)
        
        success = QLabel(
            "<p>🎉 <b>Congratulations!</b> You now know the full JAKS workflow.</p>"
        )
        success.setStyleSheet("background: #D4EDDA; padding: 10px; border-radius: 4px;")
        success.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(success)
        
        self.content_layout.addStretch(1)
