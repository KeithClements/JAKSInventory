"""
app/services/ai_description_service.py
======================================
AI-generated product copy: title + description + meta_description for a single
product, via Claude (Anthropic Messages API).

DESIGN — mirrors the QBO push pattern (encrypted credential + lazy client +
fail-soft errors). NEVER touches a money path; never logs the API key.

  * The API key is stored ENCRYPTED at rest in the ``settings`` KV under
    ``anthropic_api_key_encrypted`` — same Fernet scheme (``JAKS_FERNET_KEY``)
    used by ``qbo_client``. ``enc:`` prefix on the stored value means Fernet
    payload; anything else is treated as legacy plaintext (so an unconfigured
    box never breaks).
  * The default model is ``claude-sonnet-4-6`` (best instruction-following
    for product copy). Overridable via the ``ai_description_model`` setting.
  * STRUCTURED OUTPUT is enforced via Anthropic's tool-use API: the model is
    forced to call the single ``save_description`` tool whose input schema
    pins the three required fields. No JSON parsing of free-form text.
  * Errors are NEVER raised to the caller. Any failure (no key, network,
    auth, rate limit, malformed tool call) is wrapped as
    ``{"error": "...", "message": "..."}`` so the endpoint can render a
    friendly hint instead of a 500.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from app.services.base import BaseService
from app.settings_utils import get_setting_value_db, set_setting_value_db

log = logging.getLogger(__name__)

# ── Fernet encryption at rest ─────────────────────────────────────────────────
# Mirrors qbo_client._encrypt / _decrypt — same env var, same enc: prefix.
# We re-implement (rather than import the QBO helpers) so this module stays
# independently testable and the QBO file isn't expanded with a new public API.
_FERNET_ENV = "JAKS_FERNET_KEY"
_ENC_PREFIX = "enc:"
_warned_no_key = False


def _fernet():
    """Return a Fernet instance or None when ``JAKS_FERNET_KEY`` is unset / invalid.
    A bad key degrades to plaintext with a one-line warning rather than raising —
    a bad key must never take down the AI suggest endpoint."""
    key = os.environ.get(_FERNET_ENV, "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception:
        log.warning(
            "%s is set but is not a valid Fernet key (urlsafe-base64, 32 bytes) — "
            "Anthropic key will be stored in PLAINTEXT.", _FERNET_ENV
        )
        return None


def _encrypt(value: str) -> str:
    """Encrypt a secret for storage when a key is configured; else return it
    unchanged (one-time warning). Idempotent: already-``enc:`` values pass through."""
    global _warned_no_key
    if not value or value.startswith(_ENC_PREFIX):
        return value
    f = _fernet()
    if f is None:
        if not _warned_no_key:
            log.warning(
                "%s is not set — Anthropic API key is stored in PLAINTEXT in the "
                "settings table. Set it to encrypt secrets at rest.", _FERNET_ENV
            )
            _warned_no_key = True
        return value
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    """Resolve a stored secret to plaintext. ``enc:``-prefixed values are
    Fernet-decrypted; everything else is treated as legacy plaintext. A decrypt
    failure falls back to the raw stored string instead of raising."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # legacy plaintext — deterministic, no exception path
    f = _fernet()
    if f is None:
        log.warning(
            "Found an encrypted Anthropic key but %s is not set — cannot decrypt.",
            _FERNET_ENV,
        )
        return value
    try:
        from cryptography.fernet import InvalidToken
        try:
            return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
        except InvalidToken:
            log.warning(
                "The Anthropic key failed Fernet decryption — treating it as legacy plaintext."
            )
            return value
    except Exception:
        return value


# ── Public key helpers ────────────────────────────────────────────────────────
# These are the ONLY way callers should touch the API key — they encrypt on
# write and decrypt on read so plaintext never lands in the DB or a log.
_KEY_SETTING = "anthropic_api_key_encrypted"
_MODEL_SETTING = "ai_description_model"
DEFAULT_MODEL = "claude-sonnet-4-6"


def set_anthropic_api_key(db: Session, plaintext_key: str) -> None:
    """Store the API key, Fernet-encrypted at rest. Caller commits.

    Pass an empty string to CLEAR the key (no encryption applied to ""), which
    disables the AI suggest endpoint until a new key is set.
    """
    raw = (plaintext_key or "").strip()
    if not raw:
        # Clear: store empty string (un-encrypted), so is_configured() flips false.
        set_setting_value_db(db, _KEY_SETTING, "", "Anthropic API Key (Fernet-encrypted)")
        return
    set_setting_value_db(
        db, _KEY_SETTING, _encrypt(raw),
        "Anthropic API Key (Fernet-encrypted)",
    )


def get_anthropic_api_key(db: Session) -> str | None:
    """Read + decrypt the API key. Returns None when unset.

    NEVER call this to log/return the key — it's only for the API client.
    """
    raw = get_setting_value_db(db, _KEY_SETTING, "").strip()
    if not raw:
        return None
    decrypted = _decrypt(raw).strip()
    return decrypted or None


def get_ai_model(db: Session) -> str:
    """Resolved model id (overridable via settings). Falls back to the default."""
    return (
        get_setting_value_db(db, _MODEL_SETTING, "").strip()
        or DEFAULT_MODEL
    )


def set_ai_model(db: Session, model: str) -> None:
    """Persist the model id. Empty string ⇒ revert to default on next read."""
    set_setting_value_db(
        db, _MODEL_SETTING, (model or "").strip(),
        "Claude model used for product description generator",
    )


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You write product copy for a B2B heavy-duty diesel engine parts dealer "
    "(Cummins, Detroit, Cat, International, etc.). Voice: plain English, terse, "
    "technical when it matters, never marketing fluff. Diesel mechanics and "
    "fleet buyers read this — they want fitment, function, and OEM cross-refs, "
    "not adjectives. Never invent specs you don't have. "
    "Title: <= 70 chars, lead with the part type then the engine fitment. "
    "Description: 2-4 plain sentences covering what it is, where it fits, and "
    "(if known) what it replaces. "
    "Meta description: <= 155 chars, search-friendly, includes the engine "
    "family if relevant."
)

# Tool schema — Anthropic forces the model to call this tool, so its input
# arrives as a parsed dict matching the schema (no free-form JSON to parse).
_SAVE_DESCRIPTION_TOOL = {
    "name": "save_description",
    "description": "Save the generated product description fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Product title, <= 70 chars, lead with part type then engine fitment.",
            },
            "description": {
                "type": "string",
                "description": "2-4 plain sentences: what it is, where it fits, what it replaces.",
            },
            "meta_description": {
                "type": "string",
                "description": "Search-friendly summary, <= 155 chars, includes engine family if relevant.",
            },
        },
        "required": ["title", "description", "meta_description"],
    },
}

_MAX_TOKENS = 1024  # plenty for title + 4-sentence body + meta description


def _build_user_prompt(
    *,
    vendor_name: str | None,
    vendor_part_number: str | None,
    manufacturer: str | None,
    brand: str | None,
    category_path: str | None,
    engine_make: str | None,
    engine_model: str | None,
    current_title: str | None,
    current_description: str | None,
) -> str:
    """Compose the user message from whatever facts we have. Blank fields are
    OMITTED entirely so the model isn't tempted to invent values to fill them.
    """
    bits: list[str] = []
    if (current_title or "").strip():
        bits.append(f"Current title (improve or replace): {current_title.strip()}")
    if (current_description or "").strip():
        bits.append(
            f"Current description (improve or replace): {current_description.strip()[:600]}"
        )
    if (manufacturer or "").strip():
        bits.append(f"Manufacturer: {manufacturer.strip()}")
    if (brand or "").strip():
        bits.append(f"Brand: {brand.strip()}")
    if (category_path or "").strip():
        bits.append(f"Category: {category_path.strip()}")
    if (engine_make or "").strip():
        bits.append(f"Engine make: {engine_make.strip()}")
    if (engine_model or "").strip():
        bits.append(f"Engine model: {engine_model.strip()}")
    if (vendor_name or "").strip():
        bits.append(f"Vendor / source: {vendor_name.strip()}")
    if (vendor_part_number or "").strip():
        bits.append(f"Vendor part #: {vendor_part_number.strip()}")
    if not bits:
        bits.append("No product facts supplied.")
    return (
        "Generate product copy for this part. Use ONLY the facts below — do not "
        "invent specs, dimensions, OEM cross-refs, or fitment.\n\n"
        + "\n".join(bits)
        + "\n\nCall the save_description tool with your result."
    )


# ── Service ───────────────────────────────────────────────────────────────────
class AIDescriptionService(BaseService):
    """Generate title / description / meta_description for a product via Claude.

    Construction lazily resolves the API key from settings — checking
    ``is_configured()`` before calling ``suggest_for_product()`` is RECOMMENDED
    but not required (the call returns a friendly error dict either way).
    """

    def is_configured(self) -> bool:
        """True when an Anthropic key is set. Cheap settings read, no network."""
        return bool(get_anthropic_api_key(self.db))

    def model(self) -> str:
        """Resolved model id used for the next call."""
        return get_ai_model(self.db)

    def suggest_for_product(
        self,
        *,
        vendor_name: str | None = None,
        vendor_part_number: str | None = None,
        manufacturer: str | None = None,
        brand: str | None = None,
        category_path: str | None = None,
        engine_make: str | None = None,
        engine_model: str | None = None,
        current_title: str | None = None,
        current_description: str | None = None,
    ) -> dict[str, Any]:
        """Ask Claude to suggest copy. Returns either:

            {"title": str, "description": str, "meta_description": str,
             "model": str, "tokens_in": int, "tokens_out": int}

        OR a friendly error envelope (never raises):

            {"error": "no_api_key" | "api_error" | "bad_response",
             "message": "human-readable hint"}
        """
        key = get_anthropic_api_key(self.db)
        if not key:
            return {
                "error": "no_api_key",
                "message": "Configure your Anthropic API key in Settings → AI.",
            }

        model_id = self.model()
        user_prompt = _build_user_prompt(
            vendor_name=vendor_name,
            vendor_part_number=vendor_part_number,
            manufacturer=manufacturer,
            brand=brand,
            category_path=category_path,
            engine_make=engine_make,
            engine_model=engine_model,
            current_title=current_title,
            current_description=current_description,
        )

        # Import inside the call so test environments can monkeypatch the SDK
        # without paying the import cost when the feature is unused.
        try:
            import anthropic
        except ImportError as exc:  # SDK missing in this env
            log.warning("anthropic SDK not installed: %s", exc)
            return {
                "error": "api_error",
                "message": "Anthropic SDK is not installed on the server.",
            }

        try:
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model=model_id,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[_SAVE_DESCRIPTION_TOOL],
                tool_choice={"type": "tool", "name": "save_description"},
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # noqa: BLE001 — any SDK error → friendly dict
            # We deliberately catch broadly: APIError, AuthenticationError,
            # RateLimitError, APIConnectionError, BadRequestError, etc. all
            # land here. Log the type but NOT the key.
            log.warning(
                "AIDescriptionService call failed (%s): %s",
                type(exc).__name__, exc,
            )
            # Friendlier surface for the common cases.
            name = type(exc).__name__.lower()
            if "auth" in name or "permission" in name:
                msg = "Anthropic rejected the API key. Check it in Settings → AI."
            elif "rate" in name:
                msg = "Hit Anthropic's rate limit. Try again in a minute."
            elif "connect" in name or "timeout" in name:
                msg = "Could not reach Anthropic. Check your internet connection."
            else:
                msg = f"AI request failed: {exc}"
            return {"error": "api_error", "message": msg}

        # Find the tool_use block — Anthropic returns content blocks; with
        # tool_choice forcing our tool, there should be exactly one of type
        # ``tool_use`` whose ``input`` is the parsed schema-conforming dict.
        tool_input: dict | None = None
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if btype == "tool_use":
                tool_input = (
                    getattr(block, "input", None)
                    if not isinstance(block, dict)
                    else block.get("input")
                )
                if isinstance(tool_input, dict):
                    break

        if not isinstance(tool_input, dict):
            log.warning("AI response had no tool_use block: %r", resp)
            return {
                "error": "bad_response",
                "message": "Claude did not return a structured suggestion. Try again.",
            }

        title = str(tool_input.get("title", "") or "").strip()
        description = str(tool_input.get("description", "") or "").strip()
        meta_description = str(tool_input.get("meta_description", "") or "").strip()
        if not (title or description or meta_description):
            return {
                "error": "bad_response",
                "message": "Claude returned empty fields. Try again.",
            }

        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0

        return {
            "title": title,
            "description": description,
            "meta_description": meta_description,
            "model": model_id,
            "tokens_in": int(tokens_in or 0),
            "tokens_out": int(tokens_out or 0),
        }
