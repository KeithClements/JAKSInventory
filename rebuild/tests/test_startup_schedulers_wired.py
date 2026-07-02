"""
tests/test_startup_schedulers_wired.py
======================================
Source-level pins for the background schedulers registered in the startup hook.

Each scheduler is a daemon thread started once at boot; reverting a single
registration line leaves the feature silently dead (no thread, nothing fails at
runtime). These pins fail loudly if a registration is dropped, and confirm each
guard-checked helper still exists. Cheap AST/source checks — no app boot, no
thread start (the schedulers self-skip the in-memory test engine anyway).
"""
import ast
import pathlib

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"

# Helper name → the startup hook MUST call it exactly once.
_REQUIRED_SCHEDULERS = [
    "_start_shopify_scheduler",
    "_start_overdue_core_scheduler",
    "_start_qbo_retry_scheduler",
    "_start_inventory_resync_scheduler",
]


def _startup_call_names() -> set[str]:
    """Every bare function call made inside app.main's @on_event('startup') hook."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # The startup hook is decorated with @app.on_event("startup").
        is_startup = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "on_event"
            and d.args
            and getattr(d.args[0], "value", None) == "startup"
            for d in node.decorator_list
        )
        if not is_startup:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                called.add(sub.func.id)
    return called


def test_all_schedulers_registered_in_startup_hook():
    called = _startup_call_names()
    missing = [name for name in _REQUIRED_SCHEDULERS if name not in called]
    assert not missing, (
        f"Startup hook no longer calls: {missing}. A dropped scheduler "
        "registration leaves that background job silently dead."
    )


def test_scheduler_helpers_still_defined():
    import app.main as main
    for name in _REQUIRED_SCHEDULERS:
        assert hasattr(main, name), f"app.main.{name} is missing"


def test_inventory_resync_scheduler_calls_the_contract_method():
    """The scheduler must invoke InventoryService(...).resync_all_products() —
    the exact cross-lane contract name the service implements."""
    src = _MAIN.read_text(encoding="utf-8")
    assert "resync_all_products" in src
    from app.services.inventory_service import InventoryService
    assert callable(getattr(InventoryService, "resync_all_products", None))
