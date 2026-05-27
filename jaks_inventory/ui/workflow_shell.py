"""Shared workflow shell widgets and styling for operational detail screens."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)


def build_header_bar() -> tuple[QFrame, QHBoxLayout]:
    """Create the standard dark workflow header bar."""
    bar = QFrame()
    bar.setStyleSheet("QFrame { background: #1F2937; padding: 8px 16px; }")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(16, 8, 16, 8)
    lay.setSpacing(16)
    return bar, lay


def create_status_badge(text: str = "DRAFT") -> QLabel:
    badge = QLabel(text)
    badge.setStyleSheet(
        "font-weight: 600; font-size: 12px; padding: 4px 12px; "
        "border-radius: 4px; background: #E5E7EB; color: #374151;"
    )
    return badge


def build_progress_strip(stages: list[str]) -> tuple[QFrame, list[QLabel]]:
    strip = QFrame()
    strip.setStyleSheet("QFrame { background: #F9FAFB; border-bottom: 1px solid #E5E7EB; }")
    lay = QHBoxLayout(strip)
    lay.setContentsMargins(16, 6, 16, 6)
    lay.setSpacing(4)

    labels: list[QLabel] = []
    for i, stage in enumerate(stages):
        lbl = QLabel(stage)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            "font-size: 11px; padding: 4px 12px; border-radius: 3px; "
            "color: #9CA3AF; background: transparent;"
        )
        labels.append(lbl)
        lay.addWidget(lbl)
        if i < len(stages) - 1:
            arrow = QLabel("->")
            arrow.setStyleSheet("color: #D1D5DB; font-size: 12px;")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(arrow)
    lay.addStretch()
    return strip, labels


def create_metric_card(
    title: str,
    value: str,
    color: str,
    min_width: int = 100,
) -> tuple[QFrame, QLabel, QLabel]:
    card = QFrame()
    card.setMinimumHeight(58)
    card.setMinimumWidth(min_width)
    card.setStyleSheet(
        "QFrame { background: white; border: 1px solid #E5E7EB; "
        "border-radius: 6px; padding: 4px 10px; }"
    )
    vlay = QVBoxLayout(card)
    vlay.setContentsMargins(8, 2, 8, 2)
    vlay.setSpacing(0)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"font-size: 10px; color: {color}; font-weight: 600;")
    title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_lbl.setWordWrap(True)
    vlay.addWidget(title_lbl)

    value_lbl = QLabel(value)
    value_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")
    value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    value_lbl.setWordWrap(True)
    vlay.addWidget(value_lbl)
    return card, title_lbl, value_lbl


def build_summary_cards(
    cards: list[tuple[str, str, str]],
    min_width: int = 100,
) -> tuple[QFrame, list[tuple[QFrame, QLabel, QLabel]]]:
    frame = QFrame()
    frame.setStyleSheet("QFrame { background: white; border-bottom: 1px solid #E5E7EB; }")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(16, 10, 16, 10)
    lay.setSpacing(10)

    built: list[tuple[QFrame, QLabel, QLabel]] = []
    for title, value, color in cards:
        card = create_metric_card(title, value, color, min_width=min_width)
        built.append(card)
        lay.addWidget(card[0], 1)
    return frame, built


def create_sidebar_container(min_width: int = 280, max_width: int = 360) -> tuple[QWidget, QVBoxLayout]:
    """Create sidebar container with scrollbar support."""
    # Create inner panel for content
    inner_panel = QWidget()
    inner_panel.setObjectName("workflowSidebarInner")
    inner_panel.setStyleSheet(
        "QWidget#workflowSidebarInner { background: #F8FAFC; }"
    )
    lay = QVBoxLayout(inner_panel)
    lay.setContentsMargins(10, 10, 10, 10)
    lay.setSpacing(12)
    
    # Wrap in QScrollArea
    scroll = QScrollArea()
    scroll.setObjectName("workflowSidebar")
    scroll.setMaximumWidth(max_width)
    scroll.setMinimumWidth(min_width)
    scroll.setStyleSheet(
        "QScrollArea#workflowSidebar { background: #F8FAFC; border-left: 1px solid #E5E7EB; border: none; }"
        "QScrollArea#workflowSidebar QScrollBar:vertical { width: 8px; background: #F3F4F6; }"
        "QScrollArea#workflowSidebar QScrollBar::handle:vertical { background: #D1D5DB; border-radius: 4px; }"
        "QScrollArea#workflowSidebar QScrollBar::handle:vertical:hover { background: #9CA3AF; }"
    )
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner_panel)
    return scroll, lay


def create_sidebar_section_title(text: str) -> QLabel:
    hdr = QLabel(text)
    hdr.setStyleSheet("font-weight: bold; font-size: 12px; color: #1F2937;")
    return hdr


def create_link_card(title: str, value: str, button_text: str) -> tuple[QFrame, QLabel, QPushButton]:
    card = QFrame()
    card.setStyleSheet(
        "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
    )
    cla = QVBoxLayout(card)
    cla.setContentsMargins(8, 4, 8, 4)
    cla.setSpacing(4)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
    cla.addWidget(title_lbl)

    value_lbl = QLabel(value)
    value_lbl.setStyleSheet("font-size: 11px; color: #1F2937; font-weight: 500;")
    value_lbl.setWordWrap(True)
    cla.addWidget(value_lbl)

    btn = QPushButton(button_text)
    btn.setFixedHeight(26)
    btn.setStyleSheet(style_action_button("subtle"))
    cla.addWidget(btn)
    return card, value_lbl, btn


def create_activity_text_area(read_only: bool = True) -> QTextEdit:
    notes = QTextEdit()
    notes.setReadOnly(read_only)
    notes.setStyleSheet(
        "QTextEdit { background: white; border: 1px solid #E5E7EB; border-radius: 4px; "
        "font-size: 11px; padding: 6px; }"
    )
    return notes


def build_bottom_action_bar() -> tuple[QFrame, QHBoxLayout]:
    bar = QFrame()
    bar.setStyleSheet("QFrame { background: #F9FAFB; border-top: 1px solid #E5E7EB; }")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(16, 8, 16, 8)
    lay.setSpacing(8)
    return bar, lay


def style_action_button(kind: str = "neutral") -> str:
    styles = {
        "primary": (
            "QPushButton { background: #2563EB; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #1D4ED8; }"
        ),
        "success": (
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #047857; }"
        ),
        "warning": (
            "QPushButton { background: #D97706; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #B45309; }"
        ),
        "purple": (
            "QPushButton { background: #7C3AED; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #6D28D9; }"
        ),
        "teal": (
            "QPushButton { background: #0F766E; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #115E59; }"
        ),
        "danger": (
            "QPushButton { background: white; color: #DC2626; font-weight: 600; "
            "padding: 6px 14px; border-radius: 4px; border: 1px solid #FCA5A5; }"
            "QPushButton:hover { background: #FEE2E2; }"
        ),
        "neutral": "QPushButton { padding: 6px 14px; border-radius: 4px; }",
        "subtle": (
            "QPushButton { background: #F3F4F6; color: #374151; font-size: 11px; "
            "font-weight: 600; border: 1px solid #D1D5DB; border-radius: 3px; padding: 2px 8px; }"
            "QPushButton:hover { background: #E5E7EB; }"
        ),
    }
    return styles.get(kind, styles["neutral"])
