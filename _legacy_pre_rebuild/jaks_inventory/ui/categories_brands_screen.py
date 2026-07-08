"""Categories & Brands — taxonomy management for the Inventory module.

Lists product categories and brands/vendors as two side-by-side tables.
Phase 1: read-only listing with counts. Edit/Add/Rename are stubbed for
follow-up work (the underlying tables already exist in db.inventory).
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from db import inventory as inv_db  # type: ignore
except Exception:  # pragma: no cover
    inv_db = None  # type: ignore

log = logging.getLogger(__name__)

# Match the dark palette used elsewhere
_BG       = "#0f1419"
_PANEL    = "#1a2128"
_PANEL2   = "#232c35"
_BORDER   = "#2d3a45"
_TEXT     = "#e6edf3"
_TEXT_DIM = "#8b98a5"
_ACCENT   = "#ff8c42"


class _TaxonomyTable(QFrame):
    """Generic two-column taxonomy panel (name + product count)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{_PANEL}; border:1px solid {_BORDER}; "
            f"border-radius:8px; }}"
        )
        self._title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        lbl = QLabel(title.upper())
        lbl.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;font-weight:700;"
            f"letter-spacing:1px;background:transparent;border:none;"
        )
        head.addWidget(lbl)
        head.addStretch()
        self._count_lbl = QLabel("0 items")
        self._count_lbl.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;background:transparent;border:none;"
        )
        head.addWidget(self._count_lbl)
        v.addLayout(head)

        self._search = QLineEdit()
        self._search.setPlaceholderText(f"Search {title.lower()}…")
        self._search.setStyleSheet(
            f"QLineEdit {{ background:{_PANEL2}; color:{_TEXT}; "
            f"border:1px solid {_BORDER}; border-radius:6px; padding:6px 10px; }}"
        )
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Products"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{_PANEL2}; color:{_TEXT}; "
            f"gridline-color:{_BORDER}; border:1px solid {_BORDER}; "
            f"border-radius:6px; }}"
            f"QHeaderView::section {{ background:{_PANEL}; color:{_TEXT_DIM}; "
            f"border:none; border-bottom:1px solid {_BORDER}; "
            f"padding:6px 8px; font-weight:600; }}"
        )
        v.addWidget(self._table, 1)

        actions = QHBoxLayout()
        add_btn = QPushButton(f"+ Add {title[:-1] if title.endswith('s') else title}")
        add_btn.setStyleSheet(
            f"QPushButton {{ background:{_ACCENT}; color:#0f1419; "
            f"border:none; border-radius:6px; padding:6px 12px; font-weight:600; }}"
            f"QPushButton:hover {{ background:#ffa05a; }}"
        )
        add_btn.clicked.connect(lambda: self._not_implemented("Add"))
        actions.addWidget(add_btn)
        rename_btn = QPushButton("Rename")
        rename_btn.setStyleSheet(
            f"QPushButton {{ background:{_PANEL2}; color:{_TEXT}; "
            f"border:1px solid {_BORDER}; border-radius:6px; padding:6px 12px; }}"
            f"QPushButton:hover {{ background:#2d3a45; }}"
        )
        rename_btn.clicked.connect(lambda: self._not_implemented("Rename"))
        actions.addWidget(rename_btn)
        actions.addStretch()
        v.addLayout(actions)

        self._rows: list[tuple[str, int]] = []

    def set_rows(self, rows: list[tuple[str, int]]) -> None:
        self._rows = rows
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self._search.text().strip().lower()
        items = [r for r in self._rows if not q or q in r[0].lower()]
        self._table.setRowCount(len(items))
        for r, (name, count) in enumerate(items):
            name_item = QTableWidgetItem(name)
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(r, 0, name_item)
            self._table.setItem(r, 1, count_item)
        self._count_lbl.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")

    def _not_implemented(self, verb: str) -> None:
        QMessageBox.information(
            self,
            f"{verb} {self._title}",
            f"{verb} is not yet wired up. The taxonomy data lives in the database "
            "and can be edited via the per-product dialogs for now.",
        )


class CategoriesBrandsScreen(QWidget):
    """Inventory > Categories & Brands tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background:{_BG}; color:{_TEXT}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Categories & Brands")
        title.setStyleSheet(
            f"color:{_TEXT};font-size:18px;font-weight:700;background:transparent;"
        )
        sub = QLabel("Taxonomy management — used across Products, POs, and reports")
        sub.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;background:transparent;"
        )
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()
        refresh_btn = QPushButton("⟳ Refresh")
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background:{_PANEL2}; color:{_TEXT}; "
            f"border:1px solid {_BORDER}; border-radius:6px; padding:6px 12px; }}"
            f"QPushButton:hover {{ background:#2d3a45; }}"
        )
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Two columns
        body = QHBoxLayout()
        body.setSpacing(12)
        self._cat_panel = _TaxonomyTable("Categories", self)
        self._brand_panel = _TaxonomyTable("Brands", self)
        body.addWidget(self._cat_panel, 1)
        body.addWidget(self._brand_panel, 1)
        root.addLayout(body, 1)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        cats = self._load_counts("category")
        brands = self._load_counts("supplier")
        self._cat_panel.set_rows(cats)
        self._brand_panel.set_rows(brands)

    def _load_counts(self, column: str) -> list[tuple[str, int]]:
        """Return (name, product_count) rows from products grouped by ``column``."""
        if inv_db is None or not hasattr(inv_db, "get_connection"):
            return []
        try:
            conn = inv_db.get_connection()
            try:
                cur = conn.execute(
                    f"SELECT COALESCE(NULLIF(TRIM({column}), ''), '(unassigned)') AS name, "
                    f"COUNT(*) AS n FROM products GROUP BY name "
                    f"ORDER BY name COLLATE NOCASE"
                )
                return [(row[0], int(row[1])) for row in cur.fetchall()]
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as e:  # pragma: no cover
            log.warning("CategoriesBrandsScreen: failed to load %s counts: %s", column, e)
            return []

    # Refresh hook used by main_window auto-refresh
    def refresh(self) -> None:
        self._refresh()
