"""Qt signals and logging utilities for cross-thread communication."""

from __future__ import annotations
import sys
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

__all__ = [
    "Signals",
    "SignalWriter",
    "QtLogHandler",
    "redirect_stdout_to_signal",
]


class Signals(QObject):
    """Qt signals for thread-safe UI updates."""
    
    log = Signal(str)
    stats = Signal(dict)
    progress = Signal(int, str)  # (percentage, message)
    products_ready = Signal(object)
    row_ready = Signal(object)
    estimate_ready = Signal(object)
    family_analysis_ready = Signal(object)  # Phase 1.5 family grouping results
    scan_ready = Signal(object)
    sku_conflicts_ready = Signal(object)
    preupload_diff_ready = Signal(object)
    simulate_diff_ready = Signal(object)
    sync_status_ready = Signal(object)  # Shopify sync results
    done = Signal(str)
    failed = Signal(str)
    exported = Signal(str)
    scrape_success = Signal(str, object, str)  # (product_key, scraped_product, url)
    scrape_failure = Signal(str, str)  # (product_key, url)


class SignalWriter:
    """File-like object that writes to a Qt signal."""
    
    def __init__(self, signals: Signals) -> None:
        self._signals = signals

    def write(self, s: str) -> None:
        if s.strip():
            self._signals.log.emit(s.rstrip())

    def flush(self) -> None:
        pass


class QtLogHandler(logging.Handler):
    """Logging handler that emits to a Qt signal."""
    
    def __init__(self, signals: Signals) -> None:
        super().__init__()
        self._signals = signals

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._signals.log.emit(msg)
        except Exception:
            pass


@contextmanager
def redirect_stdout_to_signal(signals: Signals):
    """Context manager to redirect stdout/stderr to a Qt signal."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        writer = SignalWriter(signals)
        sys.stdout = writer  # type: ignore
        sys.stderr = writer  # type: ignore
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
