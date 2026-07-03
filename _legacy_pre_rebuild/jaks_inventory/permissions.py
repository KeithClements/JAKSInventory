"""Role-based access scaffold for JAKS Inventory screens.

This is intentionally lightweight — it lets us *tag* screens and actions
with a required role today, even before a real auth system exists. The
``current_role()`` function returns ``Role.ADMIN`` by default so nothing
is blocked until a host application wires in real user identity.

Future plan
-----------
A user/session manager will set ``set_current_role(...)`` at login. UI
screens read ``current_role()`` and either hide / disable controls or
short-circuit slot methods that fail ``has_permission(required)``.

Roles (escalating)
------------------
``VIEWER``      — read-only dashboards, audit logs.
``OPERATIONS``  — daily sync ops: push/pull, retry failures, drain
                  webhook queue. The bread-and-butter accounting role.
``ADVANCED``    — reconciliation tooling: compare inventory, repair
                  broken sync links, dedupe mappings. Riskier than
                  routine ops, requires more care.
``ADMIN``       — credentials, account mappings, sync authority
                  settings. Also implicitly grants every lower role.

Mapping (for QBO restructure plan §5)
-------------------------------------
* Settings        → ``ADMIN``
* Sync Center     → ``OPERATIONS``
* Reconciliation  → ``ADVANCED``
"""
from __future__ import annotations

from enum import IntEnum
from functools import wraps
from typing import Callable, TypeVar


class Role(IntEnum):
    """Application roles in ascending privilege order."""

    VIEWER = 10
    OPERATIONS = 20
    ADVANCED = 30
    ADMIN = 40


# Default role until a real auth system is wired up. ADMIN means every
# screen and action is reachable; this preserves today's behavior.
_current_role: Role = Role.ADMIN


def current_role() -> Role:
    """Return the currently-active role (defaults to ADMIN)."""
    return _current_role


def set_current_role(role: Role) -> None:
    """Set the active role. Intended for the future login flow."""
    global _current_role
    if not isinstance(role, Role):
        raise TypeError(f"role must be Role enum, got {type(role).__name__}")
    _current_role = role


def has_permission(required: Role) -> bool:
    """True when the current role meets or exceeds ``required``."""
    return current_role() >= required


F = TypeVar("F", bound=Callable[..., object])


def require_role(required: Role) -> Callable[[F], F]:
    """Decorator that no-ops the wrapped callable when the current role
    is below ``required``. UI handlers can use this to silently ignore
    clicks from unauthorized users; combine with disabling the control
    for a fully-gated experience.

    The decorator does not raise — it simply returns ``None`` when
    blocked. This keeps Qt slot connections safe.
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_permission(required):
                return None
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# Screen-level role tags (looked up by class name). Used by the main
# window's navigation builder to optionally hide entries from users
# whose role is too low. Today every entry resolves to ADMIN so nothing
# is hidden.
SCREEN_ROLE_MAP: dict[str, Role] = {
    "QBOSettingsTab": Role.ADMIN,
    "QBOSyncCenterScreen": Role.OPERATIONS,
    "QBOScreen": Role.ADVANCED,  # renamed display: "QBO Reconciliation"
}


def screen_role(screen_class_name: str) -> Role:
    """Return the required role for a screen class. Unknown screens
    default to ``VIEWER`` so the navigation isn't accidentally hidden.
    """
    return SCREEN_ROLE_MAP.get(screen_class_name, Role.VIEWER)


__all__ = [
    "Role",
    "current_role",
    "set_current_role",
    "has_permission",
    "require_role",
    "screen_role",
    "SCREEN_ROLE_MAP",
]
