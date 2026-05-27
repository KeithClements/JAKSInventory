"""New Customer – opens CustomerDetailDialog in create mode."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NewCustomerScreen(QWidget):
    """Placeholder widget that opens the full customer dialog in create mode."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 16)
        layout.setSpacing(16)

        title = QLabel("➕ New Customer")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        layout.addWidget(title)

        desc = QLabel(
            "Click the button below to open the full customer profile form.\n"
            "All fields from the Customer Detail screen are available."
        )
        desc.setStyleSheet("font-size: 12px; color: #555;")
        layout.addWidget(desc)

        btn = QPushButton("➕  Create New Customer…")
        btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; border: none; "
            "padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background-color: #556B2F; }"
        )
        btn.clicked.connect(self._open_dialog)
        layout.addWidget(btn)
        layout.addStretch()

    def showEvent(self, event) -> None:
        """Auto-open the dialog every time this screen is shown."""
        super().showEvent(event)
        # Use a single-shot timer so the stack switch finishes first
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._open_dialog)

    def _open_dialog(self) -> None:
        from .customer_detail_dialog import CustomerDetailDialog
        dlg = CustomerDetailDialog(customer_id=None, parent=self)
        dlg.exec()
