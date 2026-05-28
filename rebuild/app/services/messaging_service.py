"""
app/services/messaging_service.py
===================================
Phase L — Communication architecture (provider-abstracted messaging layer).

Design:
  - MessagingProvider is a Protocol — pluggable backend (Null, SMTP, Twilio).
  - NullMessagingProvider is the Phase 1 default: logs every message to
    communication_log with status=LOGGED_ONLY but never actually transmits.
  - All communication attempts (even manual copy/paste) land in communication_log
    for a complete audit trail (R12).
  - Phase 2 swaps in real providers by changing the "messaging_*_provider"
    settings keys — no code changes required in callers.

Consent rules (R12):
  - SMS requires allow_sms=True AND sms_consent_at IS NOT NULL.
  - Email requires allow_email=True.
  - do_not_contact=True blocks ALL channels unconditionally.
  - Rate limits enforced per settings keys.

Templates:
  - Stored as plain text files in app/messaging_templates/<name>.txt
  - Variables substituted with str.format_map() — unknown keys left as
    "{key}" literals (never silently blanked).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.constants import (
    CommunicationChannel,
    CommunicationDirection,
    CommunicationStatus,
)
from app.models.communication import Communication
from app.models.customer import Customer
from app.services.base import BaseService
from app.settings_utils import get_setting_value_db

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "messaging_templates"


# ── Value objects ─────────────────────────────────────────────────────────────

@dataclass
class SendResult:
    status: str                        # CommunicationStatus value
    provider_message_id: str | None = None
    error: str | None = None


@dataclass
class RenderedMessage:
    subject: str | None
    body: str
    template_name: str


# ── Provider protocol ─────────────────────────────────────────────────────────

@runtime_checkable
class MessagingProvider(Protocol):
    """Pluggable send backend. Same shape for all providers."""

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> SendResult: ...

    def send_sms(self, *, to: str, body: str) -> SendResult: ...


class NullMessagingProvider:
    """
    Phase 1 default. Logs every attempt but never transmits.
    All send_* methods return status=LOGGED_ONLY immediately.
    """

    def send_email(self, *, to: str, subject: str, body: str, attachments=None) -> SendResult:
        log.debug("NullMessagingProvider.send_email to=%r subject=%r (not sent)", to, subject)
        return SendResult(status=CommunicationStatus.LOGGED_ONLY)

    def send_sms(self, *, to: str, body: str) -> SendResult:
        log.debug("NullMessagingProvider.send_sms to=%r (not sent)", to)
        return SendResult(status=CommunicationStatus.LOGGED_ONLY)


# ── Custom errors ─────────────────────────────────────────────────────────────

class CommunicationBlockedError(RuntimeError):
    """Raised when consent or do_not_contact check prevents sending."""


class TemplateNotFoundError(FileNotFoundError):
    """Raised when a requested template file does not exist."""


# ── _SafeFormatMap: leaves unknown placeholders as-is ─────────────────────────

class _SafeFormatMap(dict):
    """str.format_map() helper — unknown keys stay as {key} literals."""
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# ── Service ───────────────────────────────────────────────────────────────────

class MessagingService(BaseService):
    """
    Central messaging service. All outbound/inbound communications route through here.
    Uses NullMessagingProvider in Phase 1 (settings: messaging_log_only_mode=true).
    """

    # ── Template rendering ────────────────────────────────────────────────────

    def render_template(self, template_name: str, variables: dict) -> RenderedMessage:
        """
        Load a .txt template from messaging_templates/ and substitute {key} placeholders.
        Unknown keys are left as {key} literals (not blanked).

        The first non-blank line becomes the subject for email sends.
        The full body is returned unchanged for SMS/copy-paste use.
        """
        path = _TEMPLATE_DIR / f"{template_name}.txt"
        if not path.exists():
            raise TemplateNotFoundError(f"Message template not found: {template_name}")

        raw = path.read_text(encoding="utf-8")
        rendered = raw.format_map(_SafeFormatMap(variables))

        # Extract subject from first non-blank line (email use)
        lines = rendered.splitlines()
        subject = next((l.strip() for l in lines if l.strip()), None)

        return RenderedMessage(subject=subject, body=rendered, template_name=template_name)

    # ── Outbound send ─────────────────────────────────────────────────────────

    def send(
        self,
        *,
        customer_id: int,
        channel: str,
        template_name: str,
        variables: dict,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
        override_address: str | None = None,
    ) -> Communication:
        """
        Render, consent-check, rate-limit, send (via provider), and log.

        Raises CommunicationBlockedError if consent or rate checks fail.
        Always writes to communication_log regardless of provider outcome.
        """
        customer = self._get_customer_or_raise(customer_id)
        self._check_consent(customer, channel)
        self._check_rate_limit(customer_id, channel)

        rendered = self.render_template(template_name, variables)
        to_address = override_address or self._resolve_address(customer, channel)
        from_address = get_setting_value_db(self.db, "company_email", "")

        provider = self._provider_for(channel)
        if channel == CommunicationChannel.EMAIL:
            result = provider.send_email(
                to=to_address,
                subject=rendered.subject or "",
                body=rendered.body,
            )
        elif channel == CommunicationChannel.SMS:
            result = provider.send_sms(to=to_address, body=rendered.body)
        else:
            result = SendResult(status=CommunicationStatus.LOGGED_ONLY)

        comm = self._write_log(
            customer_id=customer_id,
            channel=channel,
            direction=CommunicationDirection.OUTBOUND,
            status=result.status,
            to_address=to_address,
            from_address=from_address,
            subject=rendered.subject,
            body=rendered.body,
            template_used=template_name,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            provider_message_id=result.provider_message_id,
            failed_reason=result.error,
            consent_verified=True,
        )
        self.db.commit()
        return comm

    # ── Manual / copy-paste logging ───────────────────────────────────────────

    def log_manual_communication(
        self,
        *,
        customer_id: int | None = None,
        vendor_id: int | None = None,
        channel: str,
        body: str,
        subject: str | None = None,
        to_address: str = "",
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> Communication:
        """
        Log a communication that the user sent themselves (copy/paste).
        No consent check — user already handled it. No provider call.
        """
        comm = self._write_log(
            customer_id=customer_id,
            vendor_id=vendor_id,
            channel=channel,
            direction=CommunicationDirection.OUTBOUND,
            status=CommunicationStatus.LOGGED_ONLY,
            to_address=to_address,
            from_address="",
            subject=subject,
            body=body,
            template_used=None,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            provider="manual",
            consent_verified=False,
        )
        self.db.commit()
        return comm

    # ── Inbound logging ───────────────────────────────────────────────────────

    def record_inbound(
        self,
        *,
        customer_id: int | None = None,
        vendor_id: int | None = None,
        channel: str,
        body: str,
        subject: str | None = None,
        from_address: str = "",
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> Communication:
        """Log an inbound message from a customer or vendor."""
        comm = self._write_log(
            customer_id=customer_id,
            vendor_id=vendor_id,
            channel=channel,
            direction=CommunicationDirection.INBOUND,
            status=CommunicationStatus.LOGGED_ONLY,
            to_address="",
            from_address=from_address,
            subject=subject,
            body=body,
            template_used=None,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            provider="manual",
            consent_verified=False,
        )
        self.db.commit()
        return comm

    # ── Consent management ────────────────────────────────────────────────────

    def record_consent(
        self,
        customer_id: int,
        channel: str,
        method: str,
    ) -> Customer:
        """
        Record that the customer has consented to communications on this channel.
        Sets sms_consent_at / email_consent_at + sms_consent_method.
        Audit logged.
        """
        customer = self._get_customer_or_raise(customer_id)
        now = datetime.utcnow()

        if channel == CommunicationChannel.SMS:
            customer.allow_sms = True
            customer.sms_consent_at = now
            customer.sms_consent_method = method
        elif channel == CommunicationChannel.EMAIL:
            customer.allow_email = True
            customer.email_consent_at = now

        self.audit(
            entity_type="customer",
            entity_id=customer_id,
            action="consent_recorded",
            new_value={"channel": channel, "method": method},
        )
        self.db.commit()
        return customer

    def record_opt_out(self, customer_id: int, reason: str = "") -> Customer:
        """
        Record that the customer has opted out of all communications.
        Sets opt_out_at, clears allow_sms/allow_email, sets do_not_contact.
        """
        customer = self._get_customer_or_raise(customer_id)
        customer.opt_out_at = datetime.utcnow()
        customer.allow_sms = False
        customer.allow_email = False
        customer.do_not_contact = True

        self.audit(
            entity_type="customer",
            entity_id=customer_id,
            action="opt_out",
            new_value={"reason": reason},
        )
        self.db.commit()
        return customer

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_customer_or_raise(self, customer_id: int) -> Customer:
        c = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if c is None:
            raise ValueError(f"Customer {customer_id} not found")
        return c

    def _check_consent(self, customer: Customer, channel: str) -> None:
        if getattr(customer, "do_not_contact", False):
            raise CommunicationBlockedError(
                f"Customer {customer.id} is marked do_not_contact"
            )
        if channel == CommunicationChannel.SMS:
            if not getattr(customer, "allow_sms", False):
                raise CommunicationBlockedError(
                    f"Customer {customer.id} has not consented to SMS"
                )
            if not getattr(customer, "sms_consent_at", None):
                raise CommunicationBlockedError(
                    f"Customer {customer.id} is missing SMS consent timestamp"
                )
        elif channel == CommunicationChannel.EMAIL:
            if not getattr(customer, "allow_email", True):
                raise CommunicationBlockedError(
                    f"Customer {customer.id} has opted out of email"
                )

    def _check_rate_limit(self, customer_id: int, channel: str) -> None:
        """Check per-customer-per-day outbound rate limit from settings."""
        max_per_day_raw = get_setting_value_db(
            self.db, "messaging_max_outbound_per_customer_per_day", "3"
        )
        try:
            max_per_day = int(max_per_day_raw)
        except (TypeError, ValueError):
            max_per_day = 3

        from datetime import date
        today_start = datetime.combine(date.today(), datetime.min.time())
        count = (
            self.db.query(Communication)
            .filter(
                Communication.customer_id == customer_id,
                Communication.direction == CommunicationDirection.OUTBOUND,
                Communication.sent_at >= today_start,
            )
            .count()
        )
        if count >= max_per_day:
            raise CommunicationBlockedError(
                f"Rate limit reached: {count} outbound messages already sent "
                f"to customer {customer_id} today (limit={max_per_day})"
            )

    def _resolve_address(self, customer: Customer, channel: str) -> str:
        if channel == CommunicationChannel.EMAIL:
            return getattr(customer, "email", "") or ""
        if channel == CommunicationChannel.SMS:
            return re.sub(r"\D", "", getattr(customer, "phone", "") or "")
        return ""

    def _provider_for(self, channel: str) -> MessagingProvider:
        """Return the configured provider. Always NullMessagingProvider in Phase 1."""
        # Phase 2: read messaging_email_provider / messaging_sms_provider from settings
        # and instantiate real providers. For now always return Null.
        return NullMessagingProvider()

    def _write_log(
        self,
        *,
        customer_id: int | None = None,
        vendor_id: int | None = None,
        channel: str,
        direction: str,
        status: str,
        to_address: str,
        from_address: str,
        subject: str | None,
        body: str,
        template_used: str | None,
        related_entity_type: str | None,
        related_entity_id: int | None,
        provider: str | None = None,
        provider_message_id: str | None = None,
        failed_reason: str | None = None,
        consent_verified: bool = False,
    ) -> Communication:
        comm = Communication(
            customer_id=customer_id,
            vendor_id=vendor_id,
            channel=channel,
            direction=direction,
            status=status,
            provider=provider,
            provider_message_id=provider_message_id,
            to_address=to_address,
            from_address=from_address,
            subject=subject,
            body=body,
            template_used=template_used,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            sent_by_user_id=self.current_user_id,
            sent_at=datetime.utcnow(),
            failed_reason=failed_reason,
            consent_verified=consent_verified,
            opt_out_check_passed=True,
            created_at=datetime.utcnow(),
        )
        self.db.add(comm)
        self.db.flush()
        return comm
