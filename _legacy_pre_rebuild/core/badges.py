"""Shared status badge colours for quotes, sales orders, invoices, and POs.

Single source of truth so the workflow Quote -> SO -> Invoice (and PO)
looks uniform across screens. Each entry is `(background_hex, foreground_hex)`.

Use :func:`badge_colors` to look up a (bg, fg) tuple with a safe fallback.
"""
from __future__ import annotations

# Neutral fallback used when a status is unknown.
DEFAULT_BADGE: tuple[str, str] = ("#E5E7EB", "#374151")

# Quote lifecycle
QUOTE_BADGES: dict[str, tuple[str, str]] = {
    "draft":           ("#F3F4F6", "#6B7280"),
    "sent":            ("#DBEAFE", "#1E40AF"),
    "needs_follow_up": ("#FFF7ED", "#C2410C"),
    "accepted":        ("#D1FAE5", "#065F46"),
    "invoiced":        ("#EDE9FE", "#5B21B6"),
    "void":            ("#FEE2E2", "#991B1B"),
    "expired":         ("#F3F4F6", "#6B7280"),
    "lost":            ("#FEE2E2", "#7F1D1D"),
}

# Sales-order lifecycle
SO_BADGES: dict[str, tuple[str, str]] = {
    "quote":     ("#E5E7EB", "#374151"),
    "draft":     ("#E5E7EB", "#374151"),
    "confirmed": ("#DBEAFE", "#1E40AF"),
    "picking":   ("#FEF3C7", "#92400E"),
    "ready":     ("#D1FAE5", "#065F46"),
    "shipped":   ("#CFFAFE", "#155E75"),
    "invoiced":  ("#EDE9FE", "#5B21B6"),
    "cancelled": ("#FEE2E2", "#991B1B"),
}

# Invoice lifecycle
INVOICE_BADGES: dict[str, tuple[str, str]] = {
    "draft":   ("#F3F4F6", "#6B7280"),
    "sent":    ("#DBEAFE", "#1E40AF"),
    "partial": ("#FEF3C7", "#92400E"),
    "paid":    ("#D1FAE5", "#065F46"),
    "void":    ("#FEE2E2", "#991B1B"),
    "overdue": ("#FEE2E2", "#7F1D1D"),
}

# Purchase-order lifecycle
PO_BADGES: dict[str, tuple[str, str]] = {
    "draft":            ("#F3F4F6", "#6B7280"),
    "sent":             ("#EDE9FE", "#5B21B6"),
    "confirmed":        ("#FEF9C3", "#713F12"),
    "ordered":          ("#FEF9C3", "#713F12"),
    "partial":          ("#FFEDD5", "#9A3412"),
    "partial received": ("#FFEDD5", "#9A3412"),
    "received":         ("#D1FAE5", "#065F46"),
    "closed":           ("#E5E7EB", "#4B5563"),
    "cancelled":        ("#FEE2E2", "#991B1B"),
}

# Priority badges (quotes & SOs)
PRIORITY_BADGES: dict[str, tuple[str, str]] = {
    "high":                ("#FEE2E2", "#DC2626"),
    "rush":                ("#FEE2E2", "#DC2626"),
    "medium":              ("#FEF3C7", "#F59E0B"),
    "normal":              ("#FEF3C7", "#F59E0B"),
    "low":                 ("#F3F4F6", "#6B7280"),
    "waiting_on_customer": ("#EDE9FE", "#7C3AED"),
    "waiting_on_vendor":   ("#DBEAFE", "#2563EB"),
}


def badge_colors(
    status: str | None,
    table: dict[str, tuple[str, str]],
    fallback: tuple[str, str] = DEFAULT_BADGE,
) -> tuple[str, str]:
    """Return (bg, fg) for ``status`` in ``table``, case-insensitive."""
    if not status:
        return fallback
    return table.get(str(status).strip().lower(), fallback)
