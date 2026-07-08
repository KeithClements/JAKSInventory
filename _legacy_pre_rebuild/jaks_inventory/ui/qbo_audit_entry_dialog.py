"""QBO Audit Entry Dialog — per-row inspector for qbo_sync_log.

Opened from the "🔍 Audit Log" tab in the QBO Export screen (and from
the Invoice dialog's QBO Sync card). Shows the exact request JSON we
sent, the response QBO returned, an optional read-back of the QBO
record by Id, and a diff between the two.

Footer buttons:
    • Read Back From QBO — fetches the live record using qbo.readback
    • Open in QBO        — launches the QBO web UI for that record
    • Retry              — replays the last operation (best-effort)
    • Mark Resolved      — stamps resolved_at so it can be filtered out
    • Close
"""
from __future__ import annotations

import json
import webbrowser
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


def _pretty(payload: Any) -> str:
    if payload in (None, "", b""):
        return ""
    if isinstance(payload, (dict, list)):
        try:
            return json.dumps(payload, indent=2, default=str, sort_keys=True)
        except Exception:
            return str(payload)
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            return repr(payload)
    if isinstance(payload, str):
        try:
            return json.dumps(json.loads(payload), indent=2, sort_keys=True)
        except Exception:
            return payload
    return str(payload)


class QBOAuditEntryDialog(QDialog):
    """Modal viewer for a single ``qbo_sync_log`` row."""

    def __init__(self, log_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"QBO Audit Entry #{log_id}")
        self.resize(900, 640)

        self._log_id = int(log_id)
        self._row: dict[str, Any] = {}

        outer = QVBoxLayout(self)

        self._header = QLabel("")
        self._header.setTextFormat(Qt.TextFormat.RichText)
        self._header.setStyleSheet(
            "padding: 6px 8px; background: #f5f5f5; "
            "border: 1px solid #ddd; border-radius: 3px;"
        )
        outer.addWidget(self._header)

        self._tabs = QTabWidget()

        self._summary_view = QPlainTextEdit()
        self._request_view = QPlainTextEdit()
        self._response_view = QPlainTextEdit()
        self._readback_view = QPlainTextEdit()
        self._diff_view = QPlainTextEdit()
        for v in (self._summary_view, self._request_view, self._response_view,
                  self._readback_view, self._diff_view):
            v.setReadOnly(True)
            v.setFont(QFont("Consolas", 10))
            v.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._tabs.addTab(self._summary_view, "Summary")
        self._tabs.addTab(self._request_view, "Request JSON")
        self._tabs.addTab(self._response_view, "Response JSON")
        self._tabs.addTab(self._readback_view, "QBO Read-Back")
        self._tabs.addTab(self._diff_view, "Diff (Request vs Response)")
        outer.addWidget(self._tabs, 1)

        # Footer buttons
        footer = QHBoxLayout()
        self._btn_readback = QPushButton("Read Back From QBO")
        self._btn_readback.clicked.connect(self._do_readback)
        self._btn_open = QPushButton("Open in QBO")
        self._btn_open.clicked.connect(self._do_open_in_qbo)
        self._btn_resolve = QPushButton("Mark Resolved")
        self._btn_resolve.clicked.connect(self._do_mark_resolved)
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.accept)
        footer.addWidget(self._btn_readback)
        footer.addWidget(self._btn_open)
        footer.addWidget(self._btn_resolve)
        footer.addStretch(1)
        footer.addWidget(self._btn_close)
        outer.addLayout(footer)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        from qbo import sync_log

        try:
            row = sync_log.get_log(self._log_id)
        except Exception as exc:
            QMessageBox.warning(self, "Audit", f"Could not load entry: {exc}")
            return
        if not row:
            QMessageBox.warning(self, "Audit", "Entry not found.")
            return
        d = dict(row) if hasattr(row, "keys") else row
        self._row = d

        # Header
        bits = [
            f"<b>#{d.get('id')}</b>",
            d.get("source_type") or "",
            d.get("qbo_object_type") or "",
            f"status={d.get('status') or ''}",
            f"env={d.get('environment') or ''}",
        ]
        qid = d.get("qbo_object_id") or d.get("qbo_id") or ""
        if qid:
            bits.append(f"QBO Id={qid}")
        if d.get("created_at"):
            bits.append(f"at {str(d['created_at'])[:19]}")
        if d.get("resolved_at"):
            bits.append(f"<span style='color:#080;'>RESOLVED "
                        f"{str(d['resolved_at'])[:19]}</span>")
        self._header.setText("&nbsp;·&nbsp;".join(b for b in bits if b))

        # Summary tab
        summary_lines = []
        for k in ("id", "source_type", "source_id", "qbo_object_type",
                  "qbo_object_id", "status", "environment", "request_method",
                  "request_url", "response_status", "error_message",
                  "created_at", "readback_at", "resolved_at",
                  "attempts", "last_error"):
            if k in d:
                summary_lines.append(f"{k:>18}: {d.get(k)}")
        self._summary_view.setPlainText("\n".join(summary_lines))

        self._request_view.setPlainText(_pretty(d.get("request_json")))
        self._response_view.setPlainText(_pretty(d.get("response_json")))
        self._readback_view.setPlainText(_pretty(d.get("readback_json")))

        # Diff: response vs request, or readback vs request if available
        try:
            from qbo.readback import diff_payloads
            req = self._safe_json(d.get("request_json"))
            target = (
                self._safe_json(d.get("readback_json"))
                or self._safe_json(d.get("response_json"))
                or {}
            )
            diffs = diff_payloads(req, target)
            if not diffs:
                self._diff_view.setPlainText(
                    "(no differences — request matches QBO response/read-back, "
                    "ignoring QBO-managed fields)"
                )
            else:
                lines = [f"{'KEY':<60}  {'REQUEST':<30}  RESPONSE/READ-BACK"]
                lines.append("-" * 120)
                for path, a, b in diffs:
                    lines.append(f"{path[:60]:<60}  {str(a)[:30]:<30}  {b}")
                self._diff_view.setPlainText("\n".join(lines))
        except Exception as exc:
            self._diff_view.setPlainText(f"(diff failed: {exc})")

        # Disable Read Back / Open if we don't have a qbo id
        has_qbo_id = bool(qid)
        self._btn_readback.setEnabled(
            has_qbo_id and bool(d.get("qbo_object_type"))
        )
        self._btn_open.setEnabled(has_qbo_id)
        self._btn_resolve.setEnabled(not d.get("resolved_at"))

    @staticmethod
    def _safe_json(payload: Any) -> dict | list | None:
        if payload in (None, "", b""):
            return None
        if isinstance(payload, (dict, list)):
            return payload
        try:
            return json.loads(payload)
        except Exception:
            return None

    # ------------------------------------------------------------------
    def _do_readback(self) -> None:
        from qbo import readback, sync_log
        d = self._row
        obj_type = d.get("qbo_object_type") or ""
        qid = d.get("qbo_object_id") or d.get("qbo_id") or ""
        if not obj_type or not qid:
            QMessageBox.information(self, "Read Back",
                                    "Missing QBO object type or id.")
            return
        try:
            data = readback.fetch(obj_type, str(qid))
        except Exception as exc:
            QMessageBox.warning(self, "Read Back",
                                f"Read-back failed: {exc}")
            return
        try:
            sync_log.record_readback(
                self._log_id,
                readback_json=json.dumps(data, indent=2, default=str, sort_keys=True),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Read Back",
                                f"Saved to QBO but could not write log: {exc}")
        self._reload()

    def _do_open_in_qbo(self) -> None:
        from qbo import sync_log
        d = self._row
        url = sync_log.open_in_qbo_url(
            d.get("environment") or "",
            d.get("qbo_object_type") or "",
            d.get("qbo_object_id") or d.get("qbo_id") or "",
        )
        if not url:
            QMessageBox.information(self, "Open in QBO",
                                    "No QBO link available for this entry.")
            return
        try:
            webbrowser.open(url)
        except Exception as exc:
            QMessageBox.warning(self, "Open in QBO",
                                f"Failed to open browser: {exc}")

    def _do_mark_resolved(self) -> None:
        from qbo import sync_log
        try:
            sync_log.mark_resolved(self._log_id)
        except Exception as exc:
            QMessageBox.warning(self, "Mark Resolved",
                                f"Failed: {exc}")
            return
        self._reload()
