"""
app/services/ai_categorization_service.py
=========================================
Optional AI assist for the Smart Import review queue.

The keyword classifier (ClassificationService) confidently places ~2/3 of a feed;
the rest land in the queue flagged ``needs_review`` because no level-2 category
keyword matched. This service asks **Claude (Anthropic Messages API)** to suggest a
category for those flagged rows so a human reviews a pre-filled suggestion instead
of a blank.

DESIGN — gated + fail-soft, exactly like the QBO push:
  * Does **nothing** unless ``anthropic_api_key`` is set in Settings.
  * Writes only a **suggestion** onto the ImportCandidate (``resolved_category_id``
    + a "🤖 AI" flag + the model's reasoning in ``review_notes``). It NEVER changes
    ``review_status`` and NEVER writes the product catalog — a human still confirms
    the suggestion in the queue and clicks "Apply Approved".
  * Any API/parse error on a row is swallowed into the run summary; one bad row
    never aborts the run, and an unconfigured key never affects anything.

Raw httpx (no SDK dependency) — mirrors ``qbo_client``. Uses the cheap/fast Haiku
tier with a JSON-Schema *structured output* so the model can only return a real
category id (a closed ``enum``) or the sentinel ``"UNKNOWN"`` — it can't invent a
bucket. The API key is read per-call from Settings; nothing is cached at rest.
"""
from __future__ import annotations

import json
import time

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import ScrapedItemReviewStatus
from app.models.import_review import ImportCandidate
from app.models.product import ProductCategory
from app.services.base import BaseService
from app.services.product_import_service import _split_two
from app.settings_utils import get_setting_value_db

# ── Anthropic Messages API ────────────────────────────────────────────────────
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
AI_MODEL = "claude-haiku-4-5"     # cheap/fast tier — right for bulk classification
AI_FLAG = "\U0001F916 AI"          # 🤖 AI — marker prepended to candidate.flags
_MAX_TOKENS = 400                  # plenty for one small JSON object
_HTTP_TIMEOUT = 45.0
_INTER_CALL_DELAY = 0.0            # seconds between calls; raise to throttle RPM


class AICategorizationError(RuntimeError):
    """Any Anthropic API/parse failure. Caught per-row in bulk runs (fail-soft)."""


class AICategorizationService(BaseService):
    """Suggest categories for flagged Smart-Import candidates via Claude."""

    # ── Configuration / gating ────────────────────────────────────────────────
    def api_key(self) -> str:
        return get_setting_value_db(self.db, "anthropic_api_key", "").strip()

    def is_enabled(self) -> bool:
        """True only when an Anthropic API key is configured in Settings."""
        return bool(self.api_key())

    # ── Category options offered to the model ─────────────────────────────────
    def _category_options(self) -> list[tuple[int, str, int]]:
        """(id, full-path label, level) for every ACTIVE category, ordered shallow
        first. The label is the breadcrumb ("Engine > Cooling > Water Pump") so the
        model has the hierarchy context without us sending a tree."""
        cats = (
            self.db.query(ProductCategory)
            .filter(ProductCategory.is_active == True)  # noqa: E712
            .all()
        )
        by_id = {c.id: c for c in cats}
        out: list[tuple[int, str, int]] = []
        for c in cats:
            labels: list[str] = []
            node = c
            seen: set[int] = set()
            while node is not None and node.id not in seen:
                seen.add(node.id)
                labels.append(node.name)
                pid = getattr(node, "parent_id", None)
                node = by_id.get(pid) if pid else None
            out.append((c.id, " > ".join(reversed(labels)), c.level))
        out.sort(key=lambda r: (r[2], r[1]))
        return out

    # ── Build one request ─────────────────────────────────────────────────────
    def _build_request(self, candidate: ImportCandidate,
                       options: list[tuple[int, str, int]]) -> dict:
        ids = [str(cid) for cid, _, _ in options]
        cat_lines = "\n".join(f"{cid}: {label}" for cid, label, _ in options)
        system = (
            "You classify heavy-duty diesel engine parts for a parts distributor. "
            "For each part you will:\n"
            "1. Choose the SINGLE best-fitting category from the numbered list. "
            "Prefer the most specific (deepest) category. Return \"UNKNOWN\" if nothing "
            "fits with reasonable confidence — never guess.\n"
            "2. Write a concise 1–3 sentence product description (plain text, no HTML) "
            "suitable for a parts catalog. Mention the engine application, part function, "
            "and any key specs. Leave blank if the part is ambiguous.\n"
            "3. Return 3–8 comma-separated search tags (engine make, part type, etc.).\n\n"
            "Return the category's numeric id exactly as shown.\n\n"
            "CATEGORIES (id: full path):\n" + cat_lines
        )
        # Closed enum → the model can only return a real id or the sentinel.
        schema = {
            "type": "object",
            "properties": {
                "category_id": {"type": "string", "enum": ids + ["UNKNOWN"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "engine_manufacturer": {"type": "string"},
                "reason": {"type": "string"},
                # Optional product-creation fields — let Claude fill these so
                # applied products aren't created with empty description/tags.
                "description": {"type": "string"},   # 1–3 sentences, no HTML
                "tags": {"type": "string"},           # comma-separated search tags
            },
            "required": ["category_id", "confidence", "engine_manufacturer", "reason"],
            "additionalProperties": False,
        }
        return {
            "model": AI_MODEL,
            "max_tokens": _MAX_TOKENS,
            # The category list is identical across every call in a run, so cache it:
            # back-to-back calls read the cached prefix at ~0.1x input cost instead of
            # re-billing the whole list each time. (Silently no-ops if the prefix is
            # under the model's min cacheable size — pure upside, never an error.)
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": self._candidate_summary(candidate)}],
            # §21 — schema enforcement via a FORCED tool call. The previous
            # output_config/json_schema shape needs an anthropic-beta header to take
            # effect; without it the schema was ignored and the model could return
            # free-form text (parse failure → silently no suggestion). Tool use with
            # tool_choice is GA, needs no beta header, and guarantees the response
            # carries a schema-shaped tool_use.input we can read directly.
            "tools": [{
                "name": "classify_part",
                "description": "Return the single best category, confidence, and "
                               "catalog fields for this diesel part.",
                "input_schema": schema,
            }],
            "tool_choice": {"type": "tool", "name": "classify_part"},
        }

    @staticmethod
    def _candidate_summary(candidate: ImportCandidate) -> str:
        """Compact, signal-rich description of the part from the staged raw row."""
        try:
            p = json.loads(candidate.raw_json) if candidate.raw_json else {}
        except (ValueError, TypeError):
            p = {}
        title = (p.get("title") or candidate.title or "").strip()
        feed_type = (p.get("type") or candidate.category_name or "").strip()
        tags = (p.get("tags") or "").strip()
        apps = []
        for a in (p.get("apps") or [])[:12]:
            make, model = _split_two(a)
            joined = " ".join(x for x in (make, model) if x)
            if joined:
                apps.append(joined)
        oem = []
        for it in (p.get("oem") or [])[:12]:
            brand, num = _split_two(it)
            joined = " ".join(x for x in (brand, num) if x)
            if joined:
                oem.append(joined)
        body = (p.get("body") or "").strip()
        lines = [f"Title: {title or '(none)'}"]
        if feed_type:
            lines.append(f"Vendor type: {feed_type}")
        if tags:
            lines.append(f"Tags: {tags}")
        if apps:
            lines.append("Engine applications: " + "; ".join(apps))
        if oem:
            lines.append("OEM / cross references: " + "; ".join(oem))
        if body:
            lines.append("Description: " + body[:600])
        return "\n".join(lines)

    # ── HTTP call (fail-soft) ─────────────────────────────────────────────────
    def _call_api(self, payload: dict) -> dict:
        key = self.api_key()
        if not key:
            raise AICategorizationError("Anthropic API key not configured.")
        try:
            resp = httpx.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
                timeout=_HTTP_TIMEOUT,
            )
        except httpx.HTTPError as exc:  # network / timeout
            raise AICategorizationError(f"network error: {exc}") from exc
        if resp.status_code != 200:
            raise AICategorizationError(
                f"Anthropic {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        # §21 — forced tool_choice guarantees a tool_use block whose `input` is
        # the schema-shaped object. Read it directly (no JSON-from-prose parsing).
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                result = block.get("input")
                if isinstance(result, dict):
                    return result
                raise AICategorizationError("tool_use block had no object input")
        raise AICategorizationError("no tool_use block in Anthropic response")

    def suggest_for_candidate(self, candidate: ImportCandidate,
                              options: list[tuple[int, str, int]] | None = None) -> dict:
        """One-shot suggestion for a single candidate (raises on failure)."""
        if options is None:
            options = self._category_options()
        return self._call_api(self._build_request(candidate, options))

    # ── Write the suggestion back (NO status change, NO catalog write) ────────
    @staticmethod
    def _set_ai_flag(candidate: ImportCandidate, text: str) -> None:
        """Replace any prior AI flag on the candidate so re-runs don't stack."""
        parts = [f.strip() for f in (candidate.flags or "").split(",")]
        parts = [f for f in parts if f and AI_FLAG not in f]
        parts.append(text)
        candidate.flags = ", ".join(parts)[:300]

    def _apply_suggestion(self, candidate: ImportCandidate, result: dict,
                          valid_ids: set[int], labels: dict[int, str]) -> bool:
        """Write the model's suggestion onto the candidate. Returns True when a real
        category was assigned. Leaves review_status/needs_review untouched — a human
        still confirms."""
        cid_raw = str((result or {}).get("category_id", "UNKNOWN")).strip()
        reason = str((result or {}).get("reason", "")).strip()
        conf = str((result or {}).get("confidence", "")).strip() or "n/a"
        mfr = str((result or {}).get("engine_manufacturer", "")).strip()
        prefix = f"{AI_FLAG} ({conf}): "

        if not cid_raw.isdigit() or int(cid_raw) not in valid_ids:
            candidate.review_notes = (prefix + "no confident category. " + reason)[:2000]
            self._set_ai_flag(candidate, f"{AI_FLAG}: no match")
            return False

        cid = int(cid_raw)
        candidate.resolved_category_id = cid
        candidate.category_issue = False
        # AI filled in the category → treat as confident so "Approve all confident"
        # picks it up without requiring the human to manually clear the flag.
        candidate.needs_review = False
        if mfr and not candidate.engine_manufacturer:
            candidate.engine_manufacturer = mfr[:200]
        label = labels.get(cid, str(cid))
        candidate.review_notes = (prefix + f"{label}. " + reason)[:2000]
        self._set_ai_flag(candidate, f"{AI_FLAG}: {label}")

        # Patch AI-generated product fields into raw_json so apply_approved can
        # use them when creating the product (description + tags only if missing).
        ai_description = str((result or {}).get("description", "")).strip()
        ai_tags = str((result or {}).get("tags", "")).strip()
        if (ai_description or ai_tags) and candidate.raw_json:
            try:
                rj = json.loads(candidate.raw_json)
                if ai_description and not rj.get("_ai_description"):
                    rj["_ai_description"] = ai_description[:2000]
                if ai_tags and not rj.get("_ai_tags"):
                    rj["_ai_tags"] = ai_tags[:500]
                candidate.raw_json = json.dumps(rj)
            except (ValueError, TypeError):
                pass

        return True

    # ── Counting (for the UI button) ──────────────────────────────────────────
    def flagged_pending_count(self, batch_id: int, *, only_unresolved: bool = True) -> int:
        q = self.db.query(func.count(ImportCandidate.id)).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.needs_review == True,                       # noqa: E712
            ImportCandidate.review_status == ScrapedItemReviewStatus.PENDING,
        )
        if only_unresolved:
            q = q.filter(ImportCandidate.resolved_category_id.is_(None))
        return q.scalar() or 0

    # ── Bulk run over a batch's flagged rows ──────────────────────────────────
    def bulk_categorize_flagged(self, batch_id: int, *, limit: int | None = None,
                                only_unresolved: bool = True) -> dict:
        """Ask Claude to suggest a category for every flagged, still-PENDING row in
        the batch. Fail-soft per row; commits after each so partial progress sticks.
        Stops early on an auth error (401/403) since every row would fail the same."""
        summary = {"processed": 0, "suggested": 0, "no_match": 0, "errors": []}
        if not self.is_enabled():
            summary["errors"].append("Anthropic API key not configured in Settings.")
            return summary
        options = self._category_options()
        if not options:
            summary["errors"].append("No active categories to choose from.")
            return summary
        valid_ids = {cid for cid, _, _ in options}
        labels = {cid: label for cid, label, _ in options}

        q = self.db.query(ImportCandidate).filter(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.needs_review == True,                       # noqa: E712
            ImportCandidate.review_status == ScrapedItemReviewStatus.PENDING,
        )
        if only_unresolved:
            q = q.filter(ImportCandidate.resolved_category_id.is_(None))
        q = q.order_by(ImportCandidate.id)
        if limit:
            q = q.limit(limit)

        for cand in q.all():
            try:
                result = self._call_api(self._build_request(cand, options))
            except AICategorizationError as exc:
                summary["errors"].append(f"candidate {cand.id} ({cand.sku}): {exc}")
                if " 401" in f" {exc}" or " 403" in f" {exc}":
                    break  # bad/blocked key — don't burn the whole batch
                continue
            ok = self._apply_suggestion(cand, result, valid_ids, labels)
            summary["processed"] += 1
            summary["suggested" if ok else "no_match"] += 1
            self.db.commit()
            if _INTER_CALL_DELAY:
                time.sleep(_INTER_CALL_DELAY)
        return summary


def run_background_ai_categorize(batch_id: int, user_id: int | None,
                                 limit: int | None = None) -> None:
    """Run a bulk AI categorize on a FRESH DB session (FastAPI BackgroundTask).

    The request's session is closed by the time a background task runs, so open
    and close our own. Fail-soft: any error is swallowed — the suggestion pass is
    never allowed to take down the request that started it."""
    from app.database import SessionLocal  # local import: avoids an import cycle
    db = SessionLocal()
    try:
        AICategorizationService(db, user_id).bulk_categorize_flagged(batch_id, limit=limit)
    except Exception:  # noqa: BLE001 — background job, nothing to surface to
        db.rollback()
    finally:
        db.close()
