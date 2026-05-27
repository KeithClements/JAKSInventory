"""
LogPanel - Collapsible log viewer widget.

Provides a tabbed log interface with Log, Errors, Enrich Failures, and Summary tabs.
Includes toggle button for collapse/expand and copy errors button.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogPanel(QWidget):
    """Collapsible log panel with tabbed log views."""

    # Signals
    visibility_changed = Signal(bool)  # Emitted when panel is shown/hidden
    copy_errors_clicked = Signal()  # Emitted when copy errors button clicked

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Create and arrange UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toggle button row
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)

        self.toggle_btn = QPushButton("Show Log")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setToolTip("Show/hide the log output")

        self.copy_errors_btn = QPushButton("📋 Copy Errors")

        toggle_row.addWidget(self.toggle_btn)
        toggle_row.addStretch(1)
        toggle_row.addWidget(self.copy_errors_btn)

        layout.addLayout(toggle_row)

        # Log tabs container (collapsible)
        self.log_container = QWidget()
        container_layout = QVBoxLayout(self.log_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create log text widgets
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)

        self.errors_text = QTextEdit()
        self.errors_text.setReadOnly(True)

        self.enrich_fail_text = QTextEdit()
        self.enrich_fail_text.setReadOnly(True)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)

        # Tab widget
        self.log_tabs = QTabWidget()
        self.log_tabs.addTab(self.log_text, "Log")
        self.log_tabs.addTab(self.errors_text, "Errors only")
        self.log_tabs.addTab(self.enrich_fail_text, "Enrich Failures")
        self.log_tabs.addTab(self.summary_text, "Summary")

        container_layout.addWidget(self.log_tabs)
        self.log_container.setVisible(False)

        layout.addWidget(self.log_container)

    def _connect_signals(self) -> None:
        """Wire up internal signal connections."""
        self.toggle_btn.clicked.connect(self._on_toggle)
        self.copy_errors_btn.clicked.connect(self.copy_errors_clicked.emit)

    def _on_toggle(self) -> None:
        """Handle toggle button click."""
        try:
            expanded = bool(self.toggle_btn.isChecked())
        except Exception:
            expanded = False
        try:
            self.log_container.setVisible(expanded)
            self.toggle_btn.setText("Hide Log" if expanded else "Show Log")
            self.visibility_changed.emit(expanded)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def append_log(self, text: str) -> None:
        """Append text to the main log tab."""
        self.log_text.append(text)

    def append_error(self, text: str) -> None:
        """Append text to the errors tab."""
        self.errors_text.append(text)

    def append_enrich_failure(self, text: str) -> None:
        """Append text to the enrich failures tab."""
        self.enrich_fail_text.append(text)

    def append_summary(self, text: str) -> None:
        """Append text to the summary tab."""
        self.summary_text.append(text)

    def clear_all(self) -> None:
        """Clear all log tabs."""
        self.log_text.clear()
        self.errors_text.clear()
        self.enrich_fail_text.clear()
        self.summary_text.clear()

    def clear_log(self) -> None:
        """Clear only the main log tab."""
        self.log_text.clear()

    def get_errors(self) -> str:
        """Return the content of the errors tab."""
        return self.errors_text.toPlainText()

    def get_enrich_failures(self) -> str:
        """Return the content of the enrich failures tab."""
        return self.enrich_fail_text.toPlainText()

    def set_visible(self, visible: bool) -> None:
        """Programmatically show/hide the log container."""
        self.toggle_btn.setChecked(visible)
        self._on_toggle()

    def is_expanded(self) -> bool:
        """Return True if the log container is visible."""
        return bool(self.toggle_btn.isChecked())

    def show_tab(self, index: int) -> None:
        """Switch to the specified tab index (0=Log, 1=Errors, 2=Enrich, 3=Summary)."""
        if 0 <= index < self.log_tabs.count():
            self.log_tabs.setCurrentIndex(index)

    def show_errors_tab(self) -> None:
        """Show the Errors tab."""
        self.show_tab(1)

    def show_enrich_failures_tab(self) -> None:
        """Show the Enrich Failures tab."""
        self.show_tab(2)

    def show_summary_tab(self) -> None:
        """Show the Summary tab."""
        self.show_tab(3)

    def toggle(self) -> None:
        """Toggle the log panel visibility (simulates button click)."""
        self.toggle_btn.click()
