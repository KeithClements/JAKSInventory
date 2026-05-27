"""Reusable searchable type-ahead combo box widget."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal, QSortFilterProxyModel, QStringListModel
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QPushButton,
    QWidget,
)


class SearchableComboBox(QWidget):
    """Drop-in searchable combo with inline '+' button.

    Signals
    -------
    currentChanged(text, data)
        Emitted when the selected item changes.
    addRequested()
        Emitted when the '+' button is clicked.
    """

    currentChanged = Signal(str, object)  # (display_text, user_data)
    addRequested = Signal()

    def __init__(
        self,
        *,
        placeholder: str = "Type to search...",
        add_label: str = "+ Add",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, Any]] = []  # (display_text, user_data)
        self._selected_data: Any = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # --- Combo box ---
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._combo.setMinimumContentsLength(14)

        # Use a proxy model for live case-insensitive filtering
        self._source_model = QStringListModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._source_model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self._combo.setModel(self._proxy)

        # Configure the popup list view
        popup = QListView()
        popup.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._combo.setView(popup)

        # Line edit placeholder
        self._combo.lineEdit().setPlaceholderText(placeholder)
        self._combo.lineEdit().setClearButtonEnabled(True)
        self._combo.setCurrentIndex(-1)

        # Connect signals
        self._combo.lineEdit().textEdited.connect(self._on_text_edited)
        self._combo.activated.connect(self._on_activated)
        self._combo.lineEdit().installEventFilter(self)

        layout.addWidget(self._combo, 1)

        # --- Add button ---
        self._add_btn = QPushButton(add_label)
        self._add_btn.setFixedWidth(max(80, self._add_btn.fontMetrics().horizontalAdvance(add_label) + 24))
        self._add_btn.setToolTip(add_label)
        self._add_btn.clicked.connect(self.addRequested.emit)
        layout.addWidget(self._add_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setItems(self, items: list[tuple[str, Any]]) -> None:
        """Set items as list of (display_text, user_data) tuples."""
        self._items = list(items)
        labels = [t[0] for t in self._items]
        self._source_model.setStringList(labels)
        self._proxy.setFilterFixedString("")
        self._combo.setCurrentIndex(-1)
        self._combo.lineEdit().clear()
        self._selected_data = None

    def addItem(self, text: str, data: Any = None) -> None:
        self._items.append((text, data))
        labels = [t[0] for t in self._items]
        self._source_model.setStringList(labels)
        self._proxy.setFilterFixedString("")

    def currentText(self) -> str:
        return self._combo.currentText()

    def currentData(self) -> Any:
        return self._selected_data

    def setCurrentByText(self, text: str) -> None:
        """Select an item by its display text."""
        self._proxy.setFilterFixedString("")
        for i, (label, data) in enumerate(self._items):
            if label.lower() == text.lower():
                source_idx = i
                proxy_idx = self._proxy.mapFromSource(
                    self._source_model.index(source_idx, 0)
                )
                if proxy_idx.isValid():
                    self._combo.setCurrentIndex(proxy_idx.row())
                    self._selected_data = data
                    return
        # Fallback: set text directly
        self._combo.setCurrentText(text)

    def setCurrentByData(self, data: Any) -> None:
        """Select an item by its user data."""
        self._proxy.setFilterFixedString("")
        for i, (label, d) in enumerate(self._items):
            if d == data:
                proxy_idx = self._proxy.mapFromSource(
                    self._source_model.index(i, 0)
                )
                if proxy_idx.isValid():
                    self._combo.setCurrentIndex(proxy_idx.row())
                    self._selected_data = d
                    return

    def clear(self) -> None:
        self._proxy.setFilterFixedString("")
        self._combo.setCurrentIndex(-1)
        self._combo.lineEdit().clear()
        self._selected_data = None

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._combo.setEnabled(enabled)
        self._add_btn.setEnabled(enabled)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason) -> None:
        self._combo.lineEdit().setFocus(reason)

    def setAddVisible(self, visible: bool) -> None:
        self._add_btn.setVisible(visible)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_text_edited(self, text: str) -> None:
        """Filter the dropdown as user types."""
        self._proxy.setFilterFixedString(text)
        self._selected_data = None
        if text:
            self._combo.showPopup()

    def _on_activated(self, proxy_row: int) -> None:
        """User picked an item from the dropdown."""
        source_idx = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        if source_idx.isValid():
            row = source_idx.row()
            if 0 <= row < len(self._items):
                label, data = self._items[row]
                self._selected_data = data
                self._combo.lineEdit().setText(label)
                # Reset filter so full list is available next time
                self._proxy.setFilterFixedString("")
                self.currentChanged.emit(label, data)

    def eventFilter(self, obj, event) -> bool:
        """Handle keyboard navigation in the line edit."""
        if obj is self._combo.lineEdit() and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Escape and self._combo.view().isVisible():
                self._combo.hidePopup()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._combo.view().isVisible():
                    idx = self._combo.view().currentIndex()
                    if idx.isValid():
                        self._combo.setCurrentIndex(idx.row())
                        self._on_activated(idx.row())
                        self._combo.hidePopup()
                        return True
        return super().eventFilter(obj, event)
