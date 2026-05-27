"""Pack & Ship dialog \u2014 Phase G hard gate for vendor core returns.

Workflow:
  1. User picks (or auto-creates) the RGA grouping for this vendor.
  2. Enter weight + box count + optional carrier hint.
  3. Click "Generate Packing Slip" to render the CRP-XXXX PDF.
  4. Click "Generate Freight Label" (depends on slip) to render the 4x6 label.
  5. "Mark Shipped" stays disabled until BOTH PDFs exist; once shipped,
     all attached obligations transition to ``shipped`` and the dialog
     closes.

No carrier API is consulted at any point \u2014 labels are generic and the
real carrier sticker is applied by hand. Tracking number entry is
optional after shipment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db.core_handling import (
    shipping_gate_status,
    transition_to_shipped,
    update_vendor_core_obligation,
)
from db.inventory import (
    attach_vendor_cores_to_rga,
    create_vendor_return,
    get_vendor_return,
    list_vendor_returns,
    update_vendor_return,
)
from jaks_inventory.utils.pdf_core_return import (
    generate_core_freight_label,
    generate_core_packing_slip,
)


class PackAndShipDialog(QDialog):
    """Hard-gates the Pending Ship to Vendor \u2192 Shipped transition.

    Inputs:
      * ``obligation`` \u2014 the seed VCO row the user invoked the dialog from.
        We use its vendor_id to scope the RGA picker and pre-select the
        seed obligation. Other obligations on the same RGA are also
        shown so user can confirm grouping.
    """

    def __init__(self, parent: Optional[QWidget], *, obligation: dict):
        super().__init__(parent)
        self._seed_obl = obligation
        self._vendor_id = int(obligation.get("vendor_id") or 0)
        self._vendor_return_id: Optional[int] = None

        self.setWindowTitle("Pack & Ship Vendor Cores")
        self.resize(560, 540)

        root = QVBoxLayout(self)

        # ── RGA selector ────────────────────────────────────────
        rga_row = QHBoxLayout()
        rga_row.addWidget(QLabel("Group under RGA:"))
        self._rga_combo = QComboBox()
        rga_row.addWidget(self._rga_combo, 1)
        new_btn = QPushButton("+ New RGA")
        new_btn.clicked.connect(self._on_new_rga)
        rga_row.addWidget(new_btn)
        root.addLayout(rga_row)

        # ── Shipment details ────────────────────────────────────
        form = QFormLayout()
        self._weight = QDoubleSpinBox()
        self._weight.setRange(0, 10_000)
        self._weight.setDecimals(1)
        self._weight.setSuffix(" lb")
        form.addRow("Weight:", self._weight)

        self._boxes = QSpinBox()
        self._boxes.setRange(1, 99)
        self._boxes.setValue(1)
        form.addRow("Box count:", self._boxes)

        self._carrier = QLineEdit()
        self._carrier.setPlaceholderText(
            "UPS / FedEx / etc. (informational only \u2014 label is generic)"
        )
        form.addRow("Carrier hint:", self._carrier)

        self._notes = QTextEdit()
        self._notes.setMaximumHeight(60)
        self._notes.setPlaceholderText("Optional notes for the packing slip")
        form.addRow("Notes:", self._notes)
        root.addLayout(form)

        # ── Generate buttons ────────────────────────────────────
        gen_row = QHBoxLayout()
        self._slip_btn = QPushButton("\U0001F4C4  Generate Packing Slip")
        self._slip_btn.clicked.connect(self._on_generate_slip)
        gen_row.addWidget(self._slip_btn)
        self._label_btn = QPushButton("\U0001F3F7\uFE0F  Generate Freight Label")
        self._label_btn.setEnabled(False)
        self._label_btn.clicked.connect(self._on_generate_label)
        gen_row.addWidget(self._label_btn)
        root.addLayout(gen_row)

        # ── Gate status banner ──────────────────────────────────
        self._gate_lbl = QLabel("")
        self._gate_lbl.setWordWrap(True)
        self._gate_lbl.setStyleSheet(
            "background: #fff3cd; color: #6b5500; padding: 6px; border-radius: 4px;"
        )
        root.addWidget(self._gate_lbl)

        # ── Tracking entry (optional, post-ship) ───────────────
        track_row = QHBoxLayout()
        track_row.addWidget(QLabel("Tracking #:"))
        self._tracking = QLineEdit()
        self._tracking.setPlaceholderText("Optional \u2014 enter after carrier label is applied")
        track_row.addWidget(self._tracking, 1)
        root.addLayout(track_row)

        # ── Buttons ─────────────────────────────────────────────
        self._buttons = QDialogButtonBox()
        self._ship_btn = self._buttons.addButton(
            "\U0001F69A  Mark Shipped",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._ship_btn.setEnabled(False)
        self._ship_btn.clicked.connect(self._on_mark_shipped)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        # Initial population
        self._load_rgas()
        self._update_gate()

    # ------------------------------------------------------------------
    # RGA picker
    # ------------------------------------------------------------------
    def _load_rgas(self) -> None:
        """Populate dropdown with open vendor_returns for this vendor."""
        self._rga_combo.blockSignals(True)
        self._rga_combo.clear()
        self._rga_combo.addItem("\u2014 Create new RGA on Generate \u2014", None)
        try:
            rgas = list_vendor_returns(vendor_id=self._vendor_id, limit=50)
        except Exception:
            rgas = []
        for r in rgas:
            status = str(r.get("status") or "")
            if status in ("credit_received", "closed", "rejected"):
                continue
            self._rga_combo.addItem(
                f"{r.get('rga_number')} \u00b7 {status or 'open'}",
                int(r.get("id") or 0),
            )
        self._rga_combo.blockSignals(False)
        # Pre-select if the seed obligation already has an RGA.
        seed_rga_id = int(self._seed_obl.get("rga_id") or 0)
        if seed_rga_id:
            idx = self._rga_combo.findData(seed_rga_id)
            if idx >= 0:
                self._rga_combo.setCurrentIndex(idx)
                self._vendor_return_id = seed_rga_id

    def _on_new_rga(self) -> None:
        try:
            result = create_vendor_return(
                vendor_id=self._vendor_id,
                reason="Vendor core return",
                notes="Created from Pack & Ship dialog",
            )
            new_id = int(result.get("id") if isinstance(result, dict) else result or 0)
        except Exception as e:
            QMessageBox.warning(self, "Create RGA failed", str(e))
            return
        try:
            update_vendor_return(new_id, return_type="vendor_core")
        except Exception:
            pass
        self._vendor_return_id = new_id
        self._load_rgas()
        idx = self._rga_combo.findData(new_id)
        if idx >= 0:
            self._rga_combo.setCurrentIndex(idx)

    def _resolve_or_create_rga(self) -> Optional[int]:
        """Return the vendor_return_id to use, creating one if needed and
        attaching the seed obligation. Returns ``None`` on failure.
        """
        rga_id = self._rga_combo.currentData()
        if rga_id is None:
            # Create one inline.
            self._on_new_rga()
            rga_id = self._vendor_return_id
        if not rga_id:
            return None
        self._vendor_return_id = int(rga_id)

        # Attach seed obligation if not already attached.
        seed_id = int(self._seed_obl.get("id") or 0)
        seed_rga = int(self._seed_obl.get("rga_id") or 0)
        if seed_id and seed_rga != self._vendor_return_id:
            try:
                attach_vendor_cores_to_rga(self._vendor_return_id, [seed_id])
                # The legacy attach helper bumps status to 'rga_created';
                # restore to 'pending_vendor_ship' until we actually ship.
                update_vendor_core_obligation(
                    seed_id, status="pending_vendor_ship",
                )
            except Exception as e:
                QMessageBox.warning(self, "Attach failed", str(e))
                return None
        return self._vendor_return_id

    # ------------------------------------------------------------------
    # PDF generation
    # ------------------------------------------------------------------
    def _on_generate_slip(self) -> None:
        rga_id = self._resolve_or_create_rga()
        if not rga_id:
            return
        # Persist weight / box count on the vendor_return row.
        try:
            update_vendor_return(
                rga_id,
                weight_lbs=float(self._weight.value() or 0),
                box_count=int(self._boxes.value() or 1),
                notes=self._notes.toPlainText().strip() or None,
            )
        except Exception:
            pass
        try:
            path = generate_core_packing_slip(rga_id)
        except Exception as e:
            QMessageBox.warning(self, "Packing slip failed", str(e))
            return
        self._label_btn.setEnabled(True)
        QMessageBox.information(
            self, "Packing slip generated",
            f"Saved to:\n{path}",
        )
        self._update_gate()

    def _on_generate_label(self) -> None:
        rga_id = self._vendor_return_id
        if not rga_id:
            QMessageBox.warning(self, "No RGA", "Generate the packing slip first.")
            return
        try:
            path = generate_core_freight_label(
                rga_id,
                weight_lbs=float(self._weight.value() or 0),
                box_count=int(self._boxes.value() or 1),
                carrier_hint=self._carrier.text().strip() or None,
            )
        except Exception as e:
            QMessageBox.warning(self, "Freight label failed", str(e))
            return
        QMessageBox.information(
            self, "Freight label generated",
            f"Saved to:\n{path}\n\n"
            "Apply the actual carrier sticker to the highlighted region "
            "before shipping.",
        )
        self._update_gate()

    # ------------------------------------------------------------------
    # Mark Shipped (gated)
    # ------------------------------------------------------------------
    def _update_gate(self) -> None:
        if not self._vendor_return_id:
            self._gate_lbl.setText(
                "\u26A0  Generate a packing slip and freight label to "
                "enable Mark Shipped."
            )
            self._ship_btn.setEnabled(False)
            self._ship_btn.setToolTip("Generate a packing slip first.")
            return
        gate = shipping_gate_status(self._vendor_return_id)
        if gate.get("ready_to_ship"):
            self._gate_lbl.setText(
                "\u2705  Packing slip and freight label generated. "
                "Ready to mark shipped."
            )
            self._gate_lbl.setStyleSheet(
                "background: #d4edda; color: #155724; padding: 6px; "
                "border-radius: 4px;"
            )
            self._ship_btn.setEnabled(True)
            self._ship_btn.setToolTip("")
        else:
            missing = "; ".join(gate.get("missing") or ["unknown"])
            self._gate_lbl.setText(f"\u26A0  Missing: {missing}")
            self._gate_lbl.setStyleSheet(
                "background: #fff3cd; color: #6b5500; padding: 6px; "
                "border-radius: 4px;"
            )
            self._ship_btn.setEnabled(False)
            self._ship_btn.setToolTip(f"Missing: {missing}")

    def _on_mark_shipped(self) -> None:
        rga_id = self._vendor_return_id
        if not rga_id:
            return
        gate = shipping_gate_status(rga_id)
        if not gate.get("ready_to_ship"):
            QMessageBox.warning(
                self, "Not ready to ship",
                "Generate both the packing slip and the freight label first.",
            )
            return
        tracking = self._tracking.text().strip() or None
        try:
            transition_to_shipped(rga_id, tracking_number=tracking)
            # Stamp shipped_at on vendor_returns too.
            update_vendor_return(rga_id, status="shipped")
        except Exception as e:
            QMessageBox.warning(self, "Ship failed", str(e))
            return
        QMessageBox.information(
            self, "Shipped",
            f"Marked RGA #{rga_id} shipped. Awaiting vendor credit.",
        )
        self.accept()
