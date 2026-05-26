from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.constants import UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.SALES
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list[UserSession]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )

    # ── convenience ──────────────────────────────────────────────────────────
    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def can_delete(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def can_void(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.BOOKKEEPING)

    @property
    def can_edit_financials(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.BOOKKEEPING)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        __import__("sqlalchemy").ForeignKey("users.id"), nullable=False
    )
    session_token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6-safe

    user: Mapped[User] = relationship("User", back_populates="sessions")
