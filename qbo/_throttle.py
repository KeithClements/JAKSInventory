"""Simple token-bucket rate limiter for QBO API calls.

QBO's documented limits:
- 500 requests/minute per realm (≈ 8.3 req/s)
- 40 requests/minute per app across all realms (much harder to hit
  unless you're a public app)

This module exposes a single :func:`throttle` call that blocks just
long enough to stay under a conservative per-realm cap. Thread-safe so
sync loops and background workers share the budget.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# Conservative cap: 6 req/sec sustained (well under 8.3 r/s ceiling),
# with bursts of up to 12 calls before throttling kicks in.
_MAX_PER_SECOND = 6
_BURST = 12

_lock = threading.Lock()
_recent: deque[float] = deque(maxlen=_BURST)


def throttle() -> None:
    """Block until it's safe to make another QBO API call.

    Sliding-window over the last second. When the burst window is full,
    sleeps until the oldest call ages out.
    """
    with _lock:
        now = time.monotonic()
        # Drop calls older than 1 second.
        while _recent and now - _recent[0] > 1.0:
            _recent.popleft()
        if len(_recent) >= _MAX_PER_SECOND:
            wait = 1.0 - (now - _recent[0]) + 0.01
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
                while _recent and now - _recent[0] > 1.0:
                    _recent.popleft()
        _recent.append(time.monotonic())


def reset() -> None:
    """Clear the sliding window. Used in tests."""
    with _lock:
        _recent.clear()
