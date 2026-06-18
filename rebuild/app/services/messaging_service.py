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


def _to_e164(phone: str) -> str:
    """Best-effort US E.164 for SMS (Twilio requires +country code). 10 digits →
    +1XXXXXXXXXX; 11 starting with 1 → +1…; an existing +… is kept; anything else
    is prefixed with + as a fallback. '' for blank."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


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


class SmtpProvider:
    """Real email via SMTP (Google Workspace / Microsoft 365 / any SMTP host).
    STARTTLS on 587 (default) or implicit TLS on 465. Pure stdlib (smtplib)."""

    def __init__(self, *, host: str, port: int, username: str, password: str,
                 from_address: str, from_name: str = "", use_tls: bool = True):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.from_address, self.from_name = from_address, from_name
        self.use_tls = use_tls

    def send_email(self, *, to: str, subject: str, body: str, attachments=None) -> SendResult:
        import smtplib
        import ssl
        from email.message import EmailMessage
        if not to:
            return SendResult(status=CommunicationStatus.FAILED, error="No recipient email")
        msg = EmailMessage()
        msg["Subject"] = subject or ""
        msg["From"] = (f"{self.from_name} <{self.from_address}>"
                       if self.from_name else self.from_address)
        msg["To"] = to
        msg.set_content(body)
        # Attach files (quote/invoice/SO PDFs). Best-effort: a bad path is logged
        # and skipped rather than failing the whole send.
        for path in (attachments or []):
            try:
                p = Path(path)
                msg.add_attachment(
                    p.read_bytes(), maintype="application", subtype="pdf",
                    filename=p.name,
                )
            except Exception as exc:
                log.warning("SMTP attachment failed (%s): %s", path, exc)
        # EHLO/HELO name: smtplib defaults to socket.getfqdn(), which on a Windows
        # box is often a bare NetBIOS name → Microsoft 365 rejects it with
        # "501 5.5.4 Invalid domain name". Send the sender's own domain instead
        # (always a valid FQDN); fall back to smtplib's default when unknown.
        helo = (self.from_address.split("@", 1)[-1].strip()
                if "@" in (self.from_address or "") else "")
        kw = {"local_hostname": helo} if helo else {}
        try:
            if int(self.port) == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=20,
                                      context=ssl.create_default_context(), **kw) as s:
                    if self.username:
                        s.login(self.username, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20, **kw) as s:
                    if self.use_tls:
                        s.starttls(context=ssl.create_default_context())
                    if self.username:
                        s.login(self.username, self.password)
                    s.send_message(msg)
            return SendResult(status=CommunicationStatus.SENT)
        except Exception as exc:  # never raise into the caller — log + record FAILED
            log.warning("SMTP send failed to=%r: %s", to, exc)
            return SendResult(status=CommunicationStatus.FAILED, error=str(exc)[:300])

    def send_sms(self, *, to: str, body: str) -> SendResult:
        return SendResult(status=CommunicationStatus.FAILED,
                          error="SMTP provider cannot send SMS")


class TwilioProvider:
    """Real SMS via the Twilio REST API (httpx — no extra SDK dependency).
    Requires an A2P 10DLC-registered sending number for US delivery."""

    def __init__(self, *, account_sid: str, auth_token: str, from_number: str):
        self.account_sid, self.auth_token = account_sid, auth_token
        self.from_number = from_number

    def send_sms(self, *, to: str, body: str) -> SendResult:
        import httpx
        if not to:
            return SendResult(status=CommunicationStatus.FAILED, error="No recipient phone")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        try:
            r = httpx.post(
                url, auth=(self.account_sid, self.auth_token),
                data={"From": self.from_number, "To": to, "Body": body}, timeout=20,
            )
            if r.status_code in (200, 201):
                return SendResult(status=CommunicationStatus.SENT,
                                  provider_message_id=r.json().get("sid"))
            # Twilio returns a JSON error body with a 'message'
            try:
                detail = r.json().get("message", r.text)
            except Exception:
                detail = r.text
            return SendResult(status=CommunicationStatus.FAILED,
                              error=f"Twilio {r.status_code}: {str(detail)[:250]}")
        except Exception as exc:
            log.warning("Twilio send failed to=%r: %s", to, exc)
            return SendResult(status=CommunicationStatus.FAILED, error=str(exc)[:300])

    def send_email(self, *, to: str, subject: str, body: str, attachments=None) -> SendResult:
        return SendResult(status=CommunicationStatus.FAILED,
                          error="Twilio provider cannot send email")


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

    # ── Free-form send (the document "Send" dialog) ───────────────────────────

    def send_message(
        self,
        *,
        customer_id: int,
        channel: str,
        body: str,
        subject: str | None = None,
        attachments: list[Path] | None = None,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
        override_address: str | None = None,
    ) -> Communication:
        """Send an already-composed (and possibly rep-edited) message — no template
        rendering. Consent-checked, rate-limited, sent via the live provider (email
        forwards ``attachments`` such as the document PDF), and always logged.

        Raises CommunicationBlockedError on consent / rate failure (the caller
        decides how to surface per-channel blocks). §22 Function A.
        """
        customer = self._get_customer_or_raise(customer_id)
        self._check_consent(customer, channel)
        self._check_rate_limit(customer_id, channel)

        to_address = override_address or self._resolve_address(customer, channel)
        if channel == CommunicationChannel.SMS and override_address:
            to_address = _to_e164(override_address)
        from_address = get_setting_value_db(self.db, "company_email", "")

        provider = self._provider_for(channel)
        if channel == CommunicationChannel.EMAIL:
            result = provider.send_email(
                to=to_address, subject=subject or "", body=body,
                attachments=attachments,
            )
        elif channel == CommunicationChannel.SMS:
            result = provider.send_sms(to=to_address, body=body)
        else:
            result = SendResult(status=CommunicationStatus.LOGGED_ONLY)

        comm = self._write_log(
            customer_id=customer_id, channel=channel,
            direction=CommunicationDirection.OUTBOUND, status=result.status,
            to_address=to_address, from_address=from_address,
            subject=subject, body=body, template_used=None,
            related_entity_type=related_entity_type, related_entity_id=related_entity_id,
            provider_message_id=result.provider_message_id,
            failed_reason=result.error, consent_verified=True,
        )
        self.db.commit()
        return comm

    # ── Connection test (Settings → Messaging) ────────────────────────────────

    def send_test(
        self,
        *,
        channel: str,
        to_address: str,
        subject: str | None = None,
        body: str = "",
    ) -> SendResult:
        """Send a TEST message to an arbitrary address via the REAL provider so the
        owner can verify SMTP / Twilio from Settings. Honors the log-only
        kill-switch (returns LOGGED_ONLY then). No customer / no consent check.
        Logged with customer_id=None + provider='test' for the audit trail. §22.5
        """
        to = _to_e164(to_address) if channel == CommunicationChannel.SMS else (to_address or "").strip()
        provider = self._provider_for(channel)
        if channel == CommunicationChannel.EMAIL:
            result = provider.send_email(
                to=to, subject=subject or "Axle ERP test email",
                body=body or "This is a test email from Axle ERP. If you received it, "
                             "your email sending is configured correctly.",
            )
        elif channel == CommunicationChannel.SMS:
            result = provider.send_sms(
                to=to,
                body=body or "Test message from Axle ERP — your SMS sending is configured correctly.",
            )
        else:
            result = SendResult(status=CommunicationStatus.FAILED, error=f"Unknown channel {channel!r}")

        self._write_log(
            customer_id=None, channel=channel,
            direction=CommunicationDirection.OUTBOUND, status=result.status,
            to_address=to, from_address=get_setting_value_db(self.db, "company_email", ""),
            subject=subject, body=body or "(connection test)", template_used=None,
            related_entity_type=None, related_entity_id=None, provider="test",
            provider_message_id=result.provider_message_id, failed_reason=result.error,
            consent_verified=False,
        )
        self.db.commit()
        return result

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
            return _to_e164(getattr(customer, "phone", "") or "")
        return ""

    def _setting(self, key: str, default: str = "") -> str:
        return (get_setting_value_db(self.db, key, default) or "").strip()

    def _provider_for(self, channel: str) -> MessagingProvider:
        """Pick the live provider from Settings, defaulting to the safe Null
        provider (log-only). messaging_log_only_mode=true forces Null on every
        channel — the global kill-switch. A misconfigured provider also falls
        back to Null so a missing credential never raises into a send."""
        # Global kill-switch: log everything, transmit nothing.
        if self._setting("messaging_log_only_mode", "true").lower() == "true":
            return NullMessagingProvider()

        from app.services.qbo_client import _decrypt as _secret_decrypt

        if channel == CommunicationChannel.EMAIL:
            if self._setting("messaging_email_provider", "null").lower() in ("smtp", "m365", "gmail"):
                host = self._setting("smtp_host")
                frm = self._setting("smtp_from_address") or self._setting("company_email")
                if host and frm:
                    try:
                        port = int(self._setting("smtp_port", "587") or 587)
                    except ValueError:
                        port = 587
                    return SmtpProvider(
                        host=host, port=port,
                        username=self._setting("smtp_username"),
                        password=_secret_decrypt(self._setting("smtp_password_encrypted")),
                        from_address=frm, from_name=self._setting("smtp_from_name"),
                        use_tls=self._setting("smtp_use_tls", "true").lower() == "true",
                    )
            return NullMessagingProvider()

        if channel == CommunicationChannel.SMS:
            if self._setting("messaging_sms_provider", "null").lower() == "twilio":
                sid = self._setting("twilio_account_sid")
                token = _secret_decrypt(self._setting("twilio_auth_token_encrypted"))
                frm = self._setting("twilio_from_number")
                if sid and token and frm:
                    return TwilioProvider(account_sid=sid, auth_token=token, from_number=frm)
            return NullMessagingProvider()

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
