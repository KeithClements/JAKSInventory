"""QThread workers for running the HHP 5-phase scraper pipeline in the background.

Each worker emits Qt signals so the UI can update safely from the main thread.

IMPORTANT: The ``Signals`` QObject must be created in the **main thread** and
passed into each worker via the constructor.  Creating it inside ``run()``
violates Qt thread-affinity rules and causes silent signal delivery failure.
"""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QThread, Signal

from workers.signals import Signals

from .hhp_bridge import (
    hhp_scan,
    hhp_scrape_category,
    hhp_scrape_single,
)

__all__ = [
    "HHPScanWorker",
    "HHPScrapeWorker",
    "HHPSingleScrapeWorker",
]


class HHPScanWorker(QThread):
    """Phase 1 — scan HHP category listings (HTTP-only, fast)."""

    progress = Signal(str)        # log message
    finished = Signal(dict)       # scan result dict
    error = Signal(str)           # error message

    def __init__(
        self,
        category: str,
        manufacturer: str,
        *,
        new_only: bool = False,
        signals: Signals | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._category = category
        self._manufacturer = manufacturer
        self._new_only = new_only
        self._signals = signals or Signals()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = hhp_scan(
                category=self._category,
                manufacturer=self._manufacturer,
                new_only=self._new_only,
                cancel_event=self._cancel,
                signals=self._signals,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


class HHPScrapeWorker(QThread):
    """Phase 2 — full scrape of an HHP category (Playwright + images + description)."""

    log = Signal(str)            # log message
    stats = Signal(dict)         # {attempted, added, skipped, failed, total, current_url}
    row_ready = Signal(object)   # individual product dict for live table update
    finished = Signal(dict)      # {key: Product} dict
    error = Signal(str)          # error message

    def __init__(
        self,
        category: str,
        manufacturer: str,
        *,
        new_only: bool = False,
        limit: int | None = None,
        signals: Signals | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._category = category
        self._manufacturer = manufacturer
        self._new_only = new_only
        self._limit = limit
        self._signals = signals or Signals()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            products = hhp_scrape_category(
                category=self._category,
                manufacturer=self._manufacturer,
                new_only=self._new_only,
                limit=self._limit,
                cancel_event=self._cancel,
                signals=self._signals,
            )
            self.finished.emit(products)
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


class HHPSingleScrapeWorker(QThread):
    """Scrape a single HHP product by URL."""

    log = Signal(str)
    finished = Signal(object)    # Product object
    error = Signal(str)

    def __init__(self, url: str, *, signals: Signals | None = None, parent=None):
        super().__init__(parent)
        self._url = url
        self._signals = signals or Signals()

    def run(self) -> None:
        try:
            prod = hhp_scrape_single(url=self._url, signals=self._signals)
            self.finished.emit(prod)
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")
