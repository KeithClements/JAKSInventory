"""
tests/test_qbo_sandbox_smoke_gate.py
====================================
Guards the ONE thing that must never break in scripts/qbo_sandbox_smoke.py: the
production/sandbox safety gate. This test makes NO real QBO network calls — it
exercises the pure gate function and the preflight ordering with stubs.

The contract:
  * assert_sandbox() raises for anything but "sandbox" (esp. "production"), and
    accepts "sandbox" case/space-insensitively.
  * _preflight() refuses (returns ok=False) the moment it sees a non-sandbox
    environment, and does so BEFORE it ever calls list_accounts() or _resolve_items
    — i.e. before any read that could touch a production realm.
  * _self_check() returns exit code 0 (gate wired correctly).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_qbo_sandbox_smoke_gate.py -q
"""
from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

smoke = importlib.import_module("scripts.qbo_sandbox_smoke")


# ── the pure gate ────────────────────────────────────────────────────────────

def test_sandbox_allowed():
    smoke.assert_sandbox("sandbox")            # must not raise


@pytest.mark.parametrize("value", ["  SANDBOX  ", "Sandbox", "sandbox"])
def test_sandbox_case_and_space_tolerant(value):
    smoke.assert_sandbox(value)                # must not raise


@pytest.mark.parametrize("value", ["production", "prod", "live", "", None])
def test_non_sandbox_blocked(value):
    with pytest.raises(smoke.ProductionEnvironmentError):
        smoke.assert_sandbox(value)


def test_self_check_returns_zero():
    assert smoke._self_check() == 0


# ── preflight refuses production BEFORE any account/item read ──────────────────

class _TrapClient:
    """A QBOClient stand-in whose construction would signal 'we got past the gate'.
    _preflight must never construct it when the environment is production."""
    def __init__(self, db):  # pragma: no cover - must not be reached in the prod test
        raise AssertionError(
            "_preflight built the QBO client for a PRODUCTION environment — "
            "the safety gate did not stop first")


class _ProdService:
    """Minimal QBOSyncService stand-in reporting a PRODUCTION connection. Its
    list_accounts / _resolve_items blow up if called — the gate must short-circuit
    before either runs."""
    def connection_summary(self):
        return {"connected": True, "environment": "production", "realm_id": "REALM-PROD"}

    def list_accounts(self):  # pragma: no cover - must not be reached
        raise AssertionError("list_accounts() ran against a production realm")

    def _resolve_items(self, client):  # pragma: no cover - must not be reached
        raise AssertionError("_resolve_items() ran against a production realm")


def test_preflight_blocks_production_before_touching_the_api():
    ok, ctx = smoke._preflight(db=object(), svc=_ProdService(), client_cls=_TrapClient)
    assert ok is False
    assert ctx == {}


class _DisconnectedService:
    def connection_summary(self):
        return {"connected": False}


def test_preflight_blocks_when_not_connected():
    ok, ctx = smoke._preflight(db=object(), svc=_DisconnectedService(),
                               client_cls=_TrapClient)
    assert ok is False
    assert ctx == {}
