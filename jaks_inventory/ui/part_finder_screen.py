"""Part Finder screen — standalone search page available from main navigation."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget, QMessageBox

from .part_finder_dialog import PartFinderDialog


class PartFinderScreen(QWidget):
    """Thin wrapper that hosts PartFinderDialog contents inline.

    Uses the same Part Finder search UI but embedded as a navigation screen
    instead of a modal dialog.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Embed the finder as a child widget (non-dialog mode)
        self._finder = _EmbeddedPartFinder(parent=self)
        lay.addWidget(self._finder)


class _EmbeddedPartFinder(PartFinderDialog):
    """PartFinderDialog reused as an embedded widget (not modal)."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # Remove dialog frame so it behaves as an embedded widget
        self.setWindowFlags(Qt.WindowType.Widget)

    def _on_add_to_quote(self, result):
        """Create a new quote and open it with this product."""
        try:
            from db import create_quote
            from .quote_dialog import QuoteDialog

            r = create_quote(lines=[{
                "product_id": result.product_id,
                "description": result.title,
                "supplier": result.vendor,
                "qty": 1,
                "unit_price": result.price,
                "unit_cost": result.cost,
            }])
            if r and r.get("id"):
                dlg = QuoteDialog(quote_id=r["id"], parent=self)
                dlg.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))

    def _on_import(self, result):
        """Open vendor import dialog."""
        from .vendor_import_dialog import VendorImportDialog

        data = {
            "vendor": result.vendor or result.source.value,
            "vendor_part_number": result.vendor_part_number,
            "oem_part_number": result.oem_part_number,
            "title": result.title,
            "description": result.description,
            "cost": result.cost,
            "price": result.price,
            "manufacturer": result.manufacturer,
            "category": result.category,
            "raw": result.raw,
        }
        dlg = VendorImportDialog(data, parent=self)
        dlg.exec()
