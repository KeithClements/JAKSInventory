"""Vendors management screen."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QGuiApplication,
    QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from db import delete_vendor, get_vendor_stats, update_vendor, get_db, find_open_purchase_orders_for_vendor

from .vendor_dialog import VendorDialog


def _fmt_money(value: float | int | None) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${v:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%"


def _fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = date.fromisoformat(str(value)[:10])
    except Exception:
        return str(value)
    days = (date.today() - d).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 30:
        return f"{days} days ago"
    return d.strftime("%Y-%m-%d")


class _NumericItem(QTableWidgetItem):
    """Right-aligned table item that sorts by underlying numeric value."""

    def __init__(self, text: str, sort_value: float) -> None:
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole + 1, sort_value)
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other: "QTableWidgetItem") -> bool:  # type: ignore[override]
        try:
            return float(self.data(Qt.ItemDataRole.UserRole + 1) or 0) < float(
                other.data(Qt.ItemDataRole.UserRole + 1) or 0
            )
        except (TypeError, ValueError):
            return super().__lt__(other)


class VendorsScreen(QWidget):
    """Screen for managing vendors with spend / activity tracking."""

    COLUMNS = [
        "Name", "Code", "Open POs", "Last Order",
        "YTD Spend", "Last 12 mo", "Lifetime",
        "Avg Transit", "On-Time %",
    ]
    COL_NAME, COL_CODE, COL_OPEN, COL_LAST, \
        COL_YTD, COL_12MO, COL_LIFETIME, \
        COL_TRANSIT, COL_ONTIME = range(9)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_vendors()

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search vendors (name / code / contact)…")
        self._search.textChanged.connect(self._filter_table)

        self._show_inactive = QCheckBox("Show inactive")
        self._show_inactive.stateChanged.connect(self._load_vendors)

        self._add_btn = QPushButton("➕ Add Vendor")
        self._add_btn.clicked.connect(self._on_add)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)

        self._delete_btn = QPushButton("🗑️ Deactivate")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)

        self._reactivate_btn = QPushButton("♻️ Reactivate")
        self._reactivate_btn.setToolTip(
            "Reactivate the selected vendor(s). Visible when at least one inactive vendor is selected."
        )
        self._reactivate_btn.clicked.connect(self._on_reactivate)
        self._reactivate_btn.setEnabled(False)
        self._reactivate_btn.hide()

        self._refresh_btn = QPushButton("⟳ Refresh")
        self._refresh_btn.clicked.connect(self._load_vendors)

        self._export_btn = QPushButton("📤 Export CSV")
        self._export_btn.setToolTip("Export visible vendor rows to CSV.")
        self._export_btn.clicked.connect(self._on_export_csv)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._show_inactive)
        search_row.addStretch()
        search_row.addWidget(self._refresh_btn)
        search_row.addWidget(self._export_btn)
        search_row.addWidget(self._add_btn)
        search_row.addWidget(self._edit_btn)
        search_row.addWidget(self._delete_btn)
        search_row.addWidget(self._reactivate_btn)

        # Table
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.cellDoubleClicked.connect(self._on_edit)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        # Delete-key shortcut → bulk deactivate selection (Excel/QBO muscle memory).
        self._delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._table)
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_shortcut.activated.connect(self._on_delete)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        # Sort by Lifetime spend descending by default
        self._table.sortItems(self.COL_LIFETIME, Qt.SortOrder.DescendingOrder)

        self._status_label = QLabel("")
        self._totals_label = QLabel("")
        font = QFont()
        font.setBold(True)
        self._totals_label.setFont(font)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._status_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self._totals_label)

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(self._table, 1)
        layout.addLayout(bottom_row)
        self.setLayout(layout)

        self._vendors: list[dict] = []

    # -- Data --------------------------------------------------------------

    def _load_vendors(self) -> None:
        active_only = not self._show_inactive.isChecked()
        try:
            self._vendors = get_vendor_stats(active_only=active_only)
        except Exception as exc:  # pragma: no cover - defensive
            QMessageBox.critical(self, "Error", f"Failed to load vendors:\n{exc}")
            self._vendors = []
        self._populate_table()

    def _populate_table(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._vendors))

        muted = QBrush(QColor("#9aa5b1"))
        green = QBrush(QColor("#2e7d32"))
        amber = QBrush(QColor("#b26a00"))
        red = QBrush(QColor("#b00020"))

        total_ytd = 0.0
        total_12mo = 0.0
        total_lifetime = 0.0
        total_open = 0

        for row_idx, v in enumerate(self._vendors):
            def text_item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            # Name
            name_item = text_item(str(v.get("name", "") or ""))
            name_item.setData(Qt.ItemDataRole.UserRole, v.get("id"))
            tooltip_bits = []
            if v.get("contact_name"):
                tooltip_bits.append(str(v["contact_name"]))
            if v.get("contact_email"):
                tooltip_bits.append(str(v["contact_email"]))
            if v.get("contact_phone"):
                tooltip_bits.append(str(v["contact_phone"]))
            if v.get("payment_terms"):
                tooltip_bits.append(f"Terms: {v['payment_terms']}")
            if tooltip_bits:
                name_item.setToolTip("\n".join(tooltip_bits))
            self._table.setItem(row_idx, self.COL_NAME, name_item)

            # Code
            self._table.setItem(row_idx, self.COL_CODE, text_item(str(v.get("code", "") or "")))

            # Open POs
            open_count = int(v.get("po_count_open") or 0)
            total_open += open_count
            open_item = _NumericItem(str(open_count) if open_count else "—", float(open_count))
            if open_count > 0:
                f = open_item.font()
                f.setBold(True)
                open_item.setFont(f)
            self._table.setItem(row_idx, self.COL_OPEN, open_item)

            # Last order
            last_order = v.get("last_order_date")
            last_item = text_item(_fmt_date(last_order))
            last_item.setData(Qt.ItemDataRole.UserRole + 1, last_order or "")
            if not last_order:
                last_item.setForeground(muted)
                f = last_item.font()
                f.setItalic(True)
                last_item.setFont(f)
            self._table.setItem(row_idx, self.COL_LAST, last_item)

            # YTD
            ytd = float(v.get("spend_ytd") or 0)
            total_ytd += ytd
            self._table.setItem(row_idx, self.COL_YTD, _NumericItem(_fmt_money(ytd), ytd))

            # 12 mo
            mo12 = float(v.get("spend_last_12mo") or 0)
            total_12mo += mo12
            self._table.setItem(row_idx, self.COL_12MO, _NumericItem(_fmt_money(mo12), mo12))

            # Lifetime
            lifetime = float(v.get("spend_lifetime") or 0)
            total_lifetime += lifetime
            life_item = _NumericItem(_fmt_money(lifetime), lifetime)
            self._table.setItem(row_idx, self.COL_LIFETIME, life_item)

            # Avg transit
            transit = v.get("avg_transit_days")
            try:
                transit_n = int(transit) if transit not in (None, "") else 0
            except (TypeError, ValueError):
                transit_n = 0
            transit_text = f"{transit_n} d" if transit_n else "—"
            self._table.setItem(row_idx, self.COL_TRANSIT, _NumericItem(transit_text, float(transit_n)))

            # On-time %
            ontime = v.get("on_time_pct")
            ot_item = _NumericItem(_fmt_pct(ontime), float(ontime) if ontime is not None else -1.0)
            if ontime is not None:
                if ontime >= 90:
                    ot_item.setForeground(green)
                elif ontime >= 70:
                    ot_item.setForeground(amber)
                else:
                    ot_item.setForeground(red)
            self._table.setItem(row_idx, self.COL_ONTIME, ot_item)

            # Dormant: no orders in 12 mo → italic + muted name
            if mo12 == 0 and lifetime > 0:
                f = name_item.font()
                f.setItalic(True)
                name_item.setFont(f)
                name_item.setForeground(muted)

        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._vendors)} vendor(s)")
        self._totals_label.setText(
            f"Open: {total_open}   "
            f"YTD: {_fmt_money(total_ytd)}   "
            f"Last 12 mo: {_fmt_money(total_12mo)}   "
            f"Lifetime: {_fmt_money(total_lifetime)}"
        )

    def _filter_table(self) -> None:
        query = self._search.text().lower().strip()
        for row in range(self._table.rowCount()):
            if not query:
                self._table.setRowHidden(row, False)
                continue
            match = False
            for col in (self.COL_NAME, self.COL_CODE):
                item = self._table.item(row, col)
                if item and query in item.text().lower():
                    match = True
                    break
            if not match:
                # also search tooltip (contact info)
                name_item = self._table.item(row, self.COL_NAME)
                if name_item and name_item.toolTip() and query in name_item.toolTip().lower():
                    match = True
            self._table.setRowHidden(row, not match)

    # -- Actions -----------------------------------------------------------

    def _selected_vendor_id(self) -> int | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, self.COL_NAME)
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        n = len(rows)
        # Edit only makes sense for a single vendor.
        self._edit_btn.setEnabled(n == 1)

        # Split selection into active / inactive so the toolbar matches reality.
        active_count = 0
        inactive_count = 0
        for idx in rows:
            v = self._vendor_at_row(idx.row())
            if not v:
                continue
            if v.get("is_active", True):
                active_count += 1
            else:
                inactive_count += 1

        # Deactivate button: enabled when at least one active is selected.
        self._delete_btn.setEnabled(active_count >= 1)
        if active_count >= 2:
            self._delete_btn.setText(f"🗑️ Deactivate ({active_count})")
        else:
            self._delete_btn.setText("🗑️ Deactivate")

        # Reactivate button: only visible / enabled when an inactive is selected.
        if inactive_count >= 1:
            self._reactivate_btn.show()
            self._reactivate_btn.setEnabled(True)
            if inactive_count >= 2:
                self._reactivate_btn.setText(f"♻️ Reactivate ({inactive_count})")
            else:
                self._reactivate_btn.setText("♻️ Reactivate")
        else:
            self._reactivate_btn.setEnabled(False)
            self._reactivate_btn.hide()

    def _on_add(self) -> None:
        # Loop so "Save & Add Another" reopens the dialog automatically.
        keep_open = True
        while keep_open:
            keep_open = False
            dlg = VendorDialog(parent=self)
            if dlg.exec():
                self._load_vendors()
            if getattr(dlg, "_save_and_new", False):
                keep_open = True

    def _on_edit(self) -> None:
        vendor_id = self._selected_vendor_id()
        if not vendor_id:
            return
        dlg = VendorDialog(vendor_id=vendor_id, parent=self)
        if dlg.exec():
            self._load_vendors()

    def _vendor_impact(self, vendor_id: int) -> tuple[int, int]:
        """Return ``(open_po_count, preferred_product_count)`` for impact warnings.

        Returns ``(0, 0)`` when the underlying queries fail — we never want a
        broken count to block deactivation.
        """
        open_pos = 0
        try:
            rows = find_open_purchase_orders_for_vendor(int(vendor_id)) or []
            open_pos = len(rows)
        except Exception:
            open_pos = 0
        pref_products = 0
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM products WHERE preferred_vendor_id = %s",
                    (int(vendor_id),),
                ).fetchone()
                if row is not None:
                    pref_products = int(row[0] if not hasattr(row, "keys") else list(row)[0])
        except Exception:
            pref_products = 0
        return (open_pos, pref_products)

    def _on_delete(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return

        # Collect (vendor_id, name) for active vendors only. Skip already-inactive
        # rows silently — keeps the UX forgiving when a mixed set is selected.
        targets: list[tuple[int, str]] = []
        skipped_inactive = 0
        for idx in rows:
            v = self._vendor_at_row(idx.row())
            if not v:
                continue
            if not v.get("is_active", True):
                skipped_inactive += 1
                continue
            vid = v.get("id")
            if vid is None:
                continue
            targets.append((int(vid), str(v.get("name") or "")))

        if not targets:
            QMessageBox.information(
                self, "Nothing to Deactivate",
                "No active vendors are selected.",
            )
            return

        # Aggregate impact across all targets.
        total_open_pos = 0
        total_pref_products = 0
        per_vendor_impact: list[tuple[str, int, int]] = []
        for vid, name in targets:
            open_pos, pref_products = self._vendor_impact(vid)
            total_open_pos += open_pos
            total_pref_products += pref_products
            per_vendor_impact.append((name, open_pos, pref_products))

        # Build impact lines (only included when non-zero).
        impact_lines: list[str] = []
        if total_open_pos:
            impact_lines.append(
                f"• {total_open_pos} open purchase order(s) will remain open."
            )
        if total_pref_products:
            impact_lines.append(
                f"• {total_pref_products} product(s) list this vendor as their preferred vendor."
            )
        impact_block = ("\n\n⚠ Impact:\n" + "\n".join(impact_lines)) if impact_lines else ""

        # Confirm.
        if len(targets) == 1:
            name = targets[0][1]
            prompt = (
                f"Deactivate vendor '{name}'?\n\n"
                "This will hide the vendor from lists but preserve history."
                f"{impact_block}"
            )
        else:
            shown = [f"• {n}" for _, n in targets[:10]]
            extra = len(targets) - 10
            if extra > 0:
                shown.append(f"…and {extra} more")
            prompt = (
                f"Deactivate {len(targets)} vendors?\n\n"
                + "\n".join(shown)
                + "\n\nThis will hide them from lists but preserve history."
                + f"{impact_block}"
            )

        reply = QMessageBox.question(
            self, "Deactivate Vendors", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok: list[str] = []
        failed: list[tuple[str, str]] = []
        for vid, name in targets:
            result = delete_vendor(vid)
            if result.get("success"):
                ok.append(name)
            else:
                failed.append((name, str(result.get("error") or "Unknown")))

        self._load_vendors()

        if failed:
            detail = "\n".join(f"• {n}: {err}" for n, err in failed[:10])
            QMessageBox.warning(
                self, "Deactivate — Partial Success",
                f"Deactivated {len(ok)} of {len(targets)} vendor(s).\n\n"
                f"Failed:\n{detail}",
            )
        elif len(ok) == 1:
            # Match the prior single-row UX: silent success.
            return
        else:
            note = ""
            if skipped_inactive:
                note = f"\n\n({skipped_inactive} already-inactive row(s) skipped.)"
            QMessageBox.information(
                self, "Vendors Deactivated",
                f"Deactivated {len(ok)} vendor(s).{note}",
            )

    def _on_reactivate(self) -> None:
        """Reactivate selected inactive vendors. Mirrors `_on_delete` shape."""
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return

        targets: list[tuple[int, str]] = []
        for idx in rows:
            v = self._vendor_at_row(idx.row())
            if not v:
                continue
            if v.get("is_active", True):
                continue  # already active
            vid = v.get("id")
            if vid is None:
                continue
            targets.append((int(vid), str(v.get("name") or "")))

        if not targets:
            QMessageBox.information(
                self, "Nothing to Reactivate",
                "No inactive vendors are selected.",
            )
            return

        if len(targets) == 1:
            prompt = f"Reactivate vendor '{targets[0][1]}'?"
        else:
            shown = [f"• {n}" for _, n in targets[:10]]
            extra = len(targets) - 10
            if extra > 0:
                shown.append(f"…and {extra} more")
            prompt = (
                f"Reactivate {len(targets)} vendors?\n\n" + "\n".join(shown)
            )

        reply = QMessageBox.question(
            self, "Reactivate Vendors", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok: list[str] = []
        failed: list[tuple[str, str]] = []
        for vid, name in targets:
            try:
                result = update_vendor(vid, is_active=True)
                if result.get("success"):
                    ok.append(name)
                else:
                    failed.append((name, str(result.get("error") or "Unknown")))
            except Exception as exc:
                failed.append((name, str(exc)))

        self._load_vendors()

        if failed:
            detail = "\n".join(f"• {n}: {err}" for n, err in failed[:10])
            QMessageBox.warning(
                self, "Reactivate — Partial Success",
                f"Reactivated {len(ok)} of {len(targets)} vendor(s).\n\nFailed:\n{detail}",
            )
        elif len(ok) > 1:
            QMessageBox.information(
                self, "Vendors Reactivated",
                f"Reactivated {len(ok)} vendor(s).",
            )

    # -- Context menu / shortcuts -----------------------------------------

    def _vendor_at_row(self, row: int) -> dict | None:
        if row < 0:
            return None
        item = self._table.item(row, self.COL_NAME)
        if not item:
            return None
        vid = item.data(Qt.ItemDataRole.UserRole)
        for v in self._vendors:
            if v.get("id") == vid:
                return v
        return None

    def _on_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        # Only collapse the existing selection when the right-clicked row is NOT
        # already part of it — otherwise multi-row deactivate would silently
        # become single-row.
        selected_rows = {idx.row() for idx in self._table.selectionModel().selectedRows()}
        if row not in selected_rows:
            self._table.selectRow(row)
            selected_rows = {row}
        v = self._vendor_at_row(row)
        if not v:
            return
        vid = v.get("id")

        menu = QMenu(self)
        open_act = menu.addAction("Open / Edit Vendor")
        new_po_act = menu.addAction("New PO from this Vendor")
        view_pos_act = menu.addAction("View Purchase Orders")
        menu.addSeparator()

        email = (v.get("contact_email") or "").strip()
        phone = (v.get("contact_phone") or "").strip()
        email_act = menu.addAction(f"Email: {email}" if email else "Email…")
        email_act.setEnabled(bool(email))
        copy_phone_act = menu.addAction(f"Copy Phone: {phone}" if phone else "Copy Phone")
        copy_phone_act.setEnabled(bool(phone))
        website = (v.get("website") or "").strip()
        site_act = menu.addAction("Open Website" if website else "Open Website…")
        site_act.setEnabled(bool(website))

        menu.addSeparator()
        n_selected = len(selected_rows)
        is_active = bool(v.get("is_active", True))
        if is_active:
            deactivate_label = (
                f"Deactivate {n_selected} Vendors" if n_selected >= 2 else "Deactivate Vendor"
            )
            deactivate_act = menu.addAction(deactivate_label)
            deactivate_act.setEnabled(True)
            reactivate_act = None
        else:
            deactivate_act = None
            reactivate_label = (
                f"Reactivate {n_selected} Vendors" if n_selected >= 2 else "Reactivate Vendor"
            )
            reactivate_act = menu.addAction(reactivate_label)
            reactivate_act.setEnabled(True)

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is open_act:
            self._on_edit()
        elif chosen is new_po_act:
            self._open_new_po(int(vid))
        elif chosen is view_pos_act:
            self._open_vendor_pos(int(vid))
        elif chosen is email_act and email:
            QDesktopServices.openUrl(QUrl(f"mailto:{email}"))
        elif chosen is copy_phone_act and phone:
            QGuiApplication.clipboard().setText(phone)
        elif chosen is site_act and website:
            url = website if "://" in website else f"https://{website}"
            QDesktopServices.openUrl(QUrl(url))
        elif deactivate_act is not None and chosen is deactivate_act:
            self._on_delete()
        elif reactivate_act is not None and chosen is reactivate_act:
            self._on_reactivate()

    def _open_new_po(self, vendor_id: int) -> None:
        try:
            from .po_dialog import PODialog
            dlg = PODialog(parent=self, vendor_id=vendor_id)
            if dlg.exec():
                self._load_vendors()
        except Exception as exc:
            QMessageBox.warning(self, "New PO", f"Could not open PO dialog:\n{exc}")

    def _open_vendor_pos(self, vendor_id: int) -> None:
        # Best-effort: navigate to the POs screen and let the user filter.
        # Falls back silently when the parent main window isn't available.
        try:
            top = self.window()
            navigate = getattr(top, "_navigate_to_screen", None)
            if callable(navigate):
                navigate("Purchase Orders")
        except Exception:
            pass

    # -- CSV export --------------------------------------------------------

    def _on_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Vendors CSV", "vendors.csv", "CSV Files (*.csv)",
        )
        if not path:
            return

        # Export all currently-loaded (and visible) rows.
        import csv
        rows = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            v = self._vendor_at_row(row)
            if not v:
                continue
            rows.append({
                "id": v.get("id"),
                "name": v.get("name"),
                "code": v.get("code") or "",
                "contact_name": v.get("contact_name") or "",
                "contact_email": v.get("contact_email") or "",
                "contact_phone": v.get("contact_phone") or "",
                "is_active": "Y" if v.get("is_active", True) else "N",
                "is_1099": "Y" if v.get("is_1099") else "N",
                "tax_id": v.get("tax_id") or "",
                "po_count_open": v.get("po_count_open") or 0,
                "spend_ytd": float(v.get("spend_ytd") or 0),
                "spend_last_12mo": float(v.get("spend_last_12mo") or 0),
                "spend_lifetime": float(v.get("spend_lifetime") or 0),
                "avg_transit_days": v.get("avg_transit_days") or 0,
                "on_time_pct": v.get("on_time_pct") if v.get("on_time_pct") is not None else "",
                "last_order_date": v.get("last_order_date") or "",
                "payment_terms": v.get("payment_terms") or "",
                "lead_time_days": v.get("lead_time_days") or 0,
            })

        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                if rows:
                    writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                else:
                    fh.write("")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not write CSV:\n{exc}")
            return

        QMessageBox.information(
            self, "Export Complete",
            f"Exported {len(rows)} vendor(s) to:\n{path}",
        )
