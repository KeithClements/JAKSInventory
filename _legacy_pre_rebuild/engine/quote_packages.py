"""Package-name resolution for multi-option quotes.

A/B/C "option groups" in the data model map to customer-facing repair
tiers. Names are configurable via the `package_name_a / _b / _c` settings
so the sales team can rename them ("Good / Better / Best",
"Bronze / Silver / Gold", etc.) without code changes.

Conventions:
    Tier A → "Essential" tier (required parts)
    Tier B → "Recommended" tier (add-ons / suggested sells)
    Tier C → "Maximum Protection" tier (extended warranty)
"""
from __future__ import annotations

DEFAULT_PACKAGE_NAMES = {
    "A": "Essential Repair Package",
    "B": "Recommended Repair Package",
    "C": "Maximum Protection Package",
}

# Tier B is the visually-emphasised recommended tier.
RECOMMENDED_TIER = "B"


def package_name(tier: str, default: str | None = None) -> str:
    """Return the configured customer-facing package name for tier A/B/C.

    Falls back to the built-in default if the setting isn't set. ``tier``
    is matched case-insensitively.
    """
    t = (tier or "").strip().upper()
    if t not in DEFAULT_PACKAGE_NAMES:
        return default or ""
    try:
        from db import get_setting  # local import to avoid cycles
    except Exception:
        return DEFAULT_PACKAGE_NAMES[t]
    raw = get_setting(f"package_name_{t.lower()}", DEFAULT_PACKAGE_NAMES[t])
    return (raw or DEFAULT_PACKAGE_NAMES[t]).strip()


def package_label(tier: str) -> str:
    """Return the long ``"OPTION X — <Name>"`` label.

    Adds a ⭐ to the recommended tier (B by default).
    """
    t = (tier or "").strip().upper()
    name = package_name(t)
    label = f"Option {t} — {name}" if name else f"Option {t}"
    if t == RECOMMENDED_TIER:
        label += "  ⭐"
    return label


def short_package_label(tier: str) -> str:
    """Compact label suitable for pill buttons / totals rows."""
    t = (tier or "").strip().upper()
    name = package_name(t)
    if not name:
        return f"Option {t}"
    # Just the first word of the package name keeps pills tight.
    short = name.split(" ")[0]
    return f"Option {t} — {short}"
