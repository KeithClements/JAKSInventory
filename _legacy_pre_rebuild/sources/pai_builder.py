"""PAI client builder utility."""

from __future__ import annotations
import os
from typing import TYPE_CHECKING

from sources.pai_client import PAIClient
from core.constants import ENABLE_PAI_COST

if TYPE_CHECKING:
    from workers.signals import Signals

__all__ = ["build_pai_client_for_run"]


def build_pai_client_for_run(signals: "Signals | None" = None) -> PAIClient | None:
    """Best-effort PAI login for enrichment.

    Priority:
    1) Explicit per-run UI opt-in via signals._pai_cost_enabled + signals._pai_username/_pai_password
    2) Legacy env var opt-in via PAI_ENABLE=1 + PAI_USER + PAI_PASS
    
    Returns:
        PAIClient instance if login successful, None otherwise.
    """

    # Phase-level guard: allow call sites to force-disable any PAI behavior.
    try:
        if signals is not None and bool(getattr(signals, "_force_disable_pai", False)):
            return None
    except Exception:
        pass

    explicit_enabled = bool(getattr(signals, "_pai_cost_enabled", False)) if signals is not None else False

    if explicit_enabled:
        user = str(getattr(signals, "_pai_username", "") or "").strip()
        pw = str(getattr(signals, "_pai_password", "") or "").strip()
        if not user or not pw:
            try:
                if signals is not None:
                    signals.log.emit("PAI enrichment enabled, but username/password missing")
            except Exception:
                pass
            return None
    else:
        if not ENABLE_PAI_COST:
            return None
        user = str(os.getenv("PAI_USER", "") or "").strip()
        pw = str(os.getenv("PAI_PASS", "") or "").strip()
        if not user or not pw:
            return None

    client = PAIClient(username=user, password=pw)
    if not client.login():
        try:
            if signals is not None:
                signals.log.emit("PAI login failed; continuing without PAI enrichment")
        except Exception:
            pass
        return None
    return client
