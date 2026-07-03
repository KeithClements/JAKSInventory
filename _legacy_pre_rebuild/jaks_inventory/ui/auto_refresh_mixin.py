"""Auto-refresh mixin. Polls a snapshot on the main thread via QTimer.

Earlier versions ran the snapshot on a worker QThread, which crashed Qt
when SQLite (not thread-safe by default) was queried off-thread and the
thread had to be torn down while still mid-call. The snapshot calls are
cheap (a single row read), so polling on the GUI thread is both simpler
and safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from PySide6.QtCore import QTimer

log = logging.getLogger(__name__)


class AutoRefreshMixin:
    """Mixin that polls a snapshot on the main thread and calls
    ``_refresh`` on change."""

    def _init_auto_refresh(self, refresh_interval_ms: int = 2000) -> None:
        self._ar_last_hash: str | None = None
        self._ar_timer = QTimer(self)
        self._ar_timer.setInterval(int(refresh_interval_ms))
        self._ar_timer.timeout.connect(self._ar_tick)
        self._ar_timer.start()

    def _ar_tick(self) -> None:
        try:
            snapshot = self._get_state_snapshot()
            payload = json.dumps(snapshot, sort_keys=True, default=str).encode()
            h = hashlib.md5(payload).hexdigest()
        except Exception as exc:
            log.debug("auto-refresh snapshot failed: %s", exc)
            return
        if self._ar_last_hash is None:
            self._ar_last_hash = h
            return
        if h != self._ar_last_hash:
            self._ar_last_hash = h
            if hasattr(self, "_refresh") and callable(self._refresh):
                try:
                    self._refresh()
                except Exception as exc:
                    log.warning("auto-refresh _refresh() failed: %s", exc)

    def _stop_auto_refresh(self) -> None:
        timer = getattr(self, "_ar_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            try:
                timer.deleteLater()
            except Exception:
                pass
        self._ar_timer = None

    def _get_state_snapshot(self) -> dict[str, Any]:
        return {}

    def closeEvent(self, event) -> None:
        self._stop_auto_refresh()
        super().closeEvent(event)
