"""Visual workflow stepper widget for JAKS Scraper.

Shows the 5-phase workflow progress with clickable steps.
"""

from __future__ import annotations

from typing import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSizePolicy,
)

__all__ = [
    "WorkflowPhase",
    "WORKFLOW_PHASES",
    "WorkflowStepper",
    "PhaseButton",
]


@dataclass
class WorkflowPhase:
    """Configuration for a workflow phase."""
    number: int
    name: str
    emoji: str
    description: str
    requires_stage: int  # Minimum stage required to enter this phase
    produces_stage: int  # Stage produced after completing this phase


# The 5-phase workflow
WORKFLOW_PHASES: list[WorkflowPhase] = [
    WorkflowPhase(1, "Scan", "🔍", "Discover products from category listing", 0, 1),
    WorkflowPhase(2, "Scrape", "🧱", "Download product details and images", 1, 2),
    WorkflowPhase(3, "PAI", "🧪", "Enrich with supplier cost and cross-refs", 2, 3),
    WorkflowPhase(4, "Review", "🧠", "Verify pricing and product data", 3, 4),
    WorkflowPhase(5, "Upload", "🚀", "Push products to Shopify", 4, 5),
]


class PhaseButton(QPushButton):
    """A styled button representing a workflow phase."""

    def __init__(
        self,
        phase: WorkflowPhase,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.phase = phase
        self._state = "inactive"  # inactive, available, active, complete
        
        self.setText(f"{phase.emoji} {phase.name}")
        self.setToolTip(f"Phase {phase.number}: {phase.description}")
        self.setMinimumHeight(40)
        self.setMinimumWidth(90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self._update_style()

    def set_state(self, state: str) -> None:
        """Set button state: inactive, available, active, complete."""
        self._state = state
        self._update_style()

    def _update_style(self) -> None:
        """Update button appearance based on state."""
        base_style = """
            QPushButton {
                font-weight: bold;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }
        """
        
        if self._state == "complete":
            self.setStyleSheet(base_style + """
                QPushButton {
                    background-color: #4A7A4A;
                    color: white;
                    border: 2px solid #3A6A3A;
                }
                QPushButton:hover {
                    background-color: #5A8A5A;
                }
            """)
        elif self._state == "active":
            self.setStyleSheet(base_style + """
                QPushButton {
                    background-color: #6B7A4A;
                    color: white;
                    border: 2px solid #4B5A2A;
                }
                QPushButton:hover {
                    background-color: #7B8A5A;
                }
            """)
        elif self._state == "available":
            self.setStyleSheet(base_style + """
                QPushButton {
                    background-color: #FFFFFF;
                    color: #6B7A4A;
                    border: 2px solid #6B7A4A;
                }
                QPushButton:hover {
                    background-color: #F0F5E8;
                }
            """)
        else:  # inactive
            self.setStyleSheet(base_style + """
                QPushButton {
                    background-color: #E8E8E0;
                    color: #A0A090;
                    border: 2px solid #C0C0B0;
                }
                QPushButton:hover {
                    background-color: #E8E8E0;
                }
            """)
            self.setCursor(Qt.CursorShape.ForbiddenCursor)


class WorkflowStepper(QWidget):
    """
    A visual stepper showing the 5-phase workflow.
    
    Shows progress through: Scan → Scrape → PAI → Review → Upload
    """

    phase_clicked = Signal(int)  # Emits phase number when clicked

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_phase = 0
        self._enabled_phases: set[int] = {1}  # Phase 1 always available
        self._completed_phases: set[int] = set()
        self._phase_buttons: dict[int, PhaseButton] = {}
        
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        
        for i, phase in enumerate(WORKFLOW_PHASES):
            # Phase button
            btn = PhaseButton(phase)
            btn.clicked.connect(lambda checked, p=phase.number: self._on_phase_clicked(p))
            self._phase_buttons[phase.number] = btn
            layout.addWidget(btn)
            
            # Arrow between phases (except after last)
            if i < len(WORKFLOW_PHASES) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("""
                    QLabel {
                        color: #A0A090;
                        font-size: 18px;
                        padding: 0 8px;
                    }
                """)
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(arrow)
        
        layout.addStretch(1)
        
        self._update_button_states()

    def _on_phase_clicked(self, phase_number: int) -> None:
        """Handle phase button click."""
        if phase_number in self._enabled_phases:
            self.phase_clicked.emit(phase_number)

    def set_current_phase(self, phase: int) -> None:
        """Set the currently active phase."""
        self._current_phase = phase
        self._update_button_states()

    def set_enabled_phases(self, phases: set[int]) -> None:
        """Set which phases are currently available based on selection."""
        self._enabled_phases = phases
        self._update_button_states()

    def mark_phase_complete(self, phase: int) -> None:
        """Mark a phase as completed."""
        self._completed_phases.add(phase)
        self._update_button_states()

    def clear_completed(self) -> None:
        """Clear all completed phase markers."""
        self._completed_phases.clear()
        self._update_button_states()

    def update_from_selection(
        self,
        selected_stages: set[int],
        *,
        has_products: bool = False,
    ) -> None:
        """
        Update enabled phases based on selected product stages.
        
        Phase requirements:
        - Phase 1 (Scan): Always available
        - Phase 2 (Scrape): Requires Stage 1 selection
        - Phase 3 (PAI): Requires Stage 2 selection
        - Phase 4 (Review): Requires Stage 3 selection
        - Phase 5 (Upload): Requires Stage 4 selection
        """
        enabled = {1}  # Scan always available
        
        if has_products and selected_stages:
            if 1 in selected_stages:
                enabled.add(2)
            if 2 in selected_stages:
                enabled.add(3)
            if 3 in selected_stages:
                enabled.add(4)
            if 4 in selected_stages:
                enabled.add(5)
        
        self._enabled_phases = enabled
        self._update_button_states()

    def _update_button_states(self) -> None:
        """Update all button states based on current state."""
        for phase_num, btn in self._phase_buttons.items():
            if phase_num in self._completed_phases:
                btn.set_state("complete")
                btn.setEnabled(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
            elif phase_num == self._current_phase:
                btn.set_state("active")
                btn.setEnabled(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
            elif phase_num in self._enabled_phases:
                btn.set_state("available")
                btn.setEnabled(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                btn.set_state("inactive")
                btn.setEnabled(False)
                btn.setCursor(Qt.CursorShape.ForbiddenCursor)
                
                # Update tooltip to show why disabled
                phase = WORKFLOW_PHASES[phase_num - 1]
                if phase.requires_stage > 0:
                    stage_emoji = {1: "🔍", 2: "🧱", 3: "🧪", 4: "🧠"}.get(phase.requires_stage, "")
                    btn.setToolTip(
                        f"Phase {phase.number}: {phase.description}\n"
                        f"⚠ Requires Stage {phase.requires_stage} {stage_emoji} selection"
                    )


class WorkflowStatusBar(QWidget):
    """
    A compact status bar showing workflow progress and row counts.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(16)
        
        # Stage counts
        self.stage_labels: dict[int, QLabel] = {}
        stage_info = [
            (1, "🔍", "Discovered"),
            (2, "🧱", "Scraped"),
            (3, "🧪", "Enriched"),
            (4, "🧠", "Reviewed"),
            (5, "🚀", "Uploaded"),
        ]
        
        for stage, emoji, name in stage_info:
            lbl = QLabel(f"{emoji} 0")
            lbl.setToolTip(f"Stage {stage}: {name}")
            lbl.setStyleSheet("color: #606050; font-size: 12px;")
            self.stage_labels[stage] = lbl
            layout.addWidget(lbl)
        
        layout.addStretch(1)
        
        # Filter info
        self.filter_label = QLabel("Showing all")
        self.filter_label.setStyleSheet("color: #606050; font-size: 12px;")
        layout.addWidget(self.filter_label)
        
        # Total count
        self.total_label = QLabel("0 products")
        self.total_label.setStyleSheet("color: #2D2D2D; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.total_label)

    def update_counts(
        self,
        stage_counts: dict[int, int],
        total: int,
        visible: int | None = None,
    ) -> None:
        """Update the displayed counts."""
        for stage, label in self.stage_labels.items():
            count = stage_counts.get(stage, 0)
            emoji = {1: "🔍", 2: "🧱", 3: "🧪", 4: "🧠", 5: "🚀"}.get(stage, "")
            label.setText(f"{emoji} {count}")
        
        self.total_label.setText(f"{total} products")
        
        if visible is not None and visible != total:
            self.filter_label.setText(f"Showing {visible} of {total}")
            self.filter_label.setVisible(True)
        else:
            self.filter_label.setText("Showing all")
            self.filter_label.setVisible(False)

    def set_filter_text(self, text: str) -> None:
        """Set custom filter description."""
        self.filter_label.setText(text)
        self.filter_label.setVisible(bool(text))
