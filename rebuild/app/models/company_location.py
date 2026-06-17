from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CompanyLocation(Base):
    """Our own shipping/billing addresses — the 'ship to me' book.

    Distinct from InventoryLocation (stock-ledger bins) and CoreLocation (core
    storage): this is purely the set of postal addresses a PO can bill-to or
    ship-to. One row is flagged ``is_primary`` (the default "ship to me" / HQ),
    seeded from the company_* settings on first run.
    """
    __tablename__ = "company_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    address_line2: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # ── Display helpers (mirror document_render address formatting) ────────────
    @property
    def address_lines(self) -> list[str]:
        lines: list[str] = []
        for ln in (self.address_line1, self.address_line2):
            if (ln or "").strip():
                lines.append(ln.strip())
        city_state = ", ".join(p for p in ((self.city or "").strip(),
                                           (self.state or "").strip()) if p)
        zip_code = (self.zip_code or "").strip()
        if city_state and zip_code:
            lines.append(f"{city_state} {zip_code}")
        elif city_state:
            lines.append(city_state)
        elif zip_code:
            lines.append(zip_code)
        if (self.country or "").strip():
            lines.append(self.country.strip())
        return lines

    @property
    def one_line(self) -> str:
        parts = [(self.name or "").strip()] + self.address_lines
        return " · ".join(p for p in parts if p)
