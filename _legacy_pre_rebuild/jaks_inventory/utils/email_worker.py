"""Background thread wrapper for SMTP email sends — prevents UI freezing."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal


class EmailWorker(QThread):
    """Run an email-sending callable off the UI thread.

    Usage::

        worker = EmailWorker(lambda: send_quote_email(quote, addr), parent=self)
        worker.finished_result.connect(self._handle_email_result)
        worker.start()
    """

    finished_result = Signal(dict)

    def __init__(self, func: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        try:
            result = self._func()
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        self.finished_result.emit(result)
