"""
app/services/crossref_hygiene.py
================================
The ONE definition of a "garbage" cross-reference number, shared by the
importer (refuse at ingest — product_import_service) and the purge tool
(delete what already leaked in — routers/catalog_hygiene).

Why this exists: cross_references drives the counter part-number search, and
the live table carries placeholder junk parsed out of vendor feeds — one
"number" like 'NUMBER OEM NUMBER' maps to 5,000+ products, 'N/A' to 3,400+,
a bare 'G' to 1,700+. A single junk ref can pull thousands of unrelated parts
into the LIMIT-capped line-adder dropdown and crowd the real match out:
direct wrong-part-sold risk at the counter.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.product import CrossReference

# Placeholder strings vendors put in the OEM-number column instead of a real
# number. Case-insensitive; separators/whitespace are ignored when matching
# (so 'O.E.M.', 'n / a', 'see-notes' all hit). Most short entries are already
# caught by the <3-alphanumerics rule below — the denylist matters for the
# longer prose placeholders.
GARBAGE_REF_DENYLIST: frozenset[str] = frozenset({
    "N/A", "NA", "NONE", "NULL", "NIL",
    "-", "--", "---",
    "NUMBER", "NUMBER OEM NUMBER",
    "OEM", "OEM NUMBER", "OEM NUMBERS", "OEM PART NUMBER",
    "PART NUMBER", "PART NUMBERS",
    "TBD", "TBA", "UNKNOWN", "UNK", "MISC",
    "SEE NOTES", "SEE NOTE", "SEE DESCRIPTION", "SEE CATALOG",
    "CALL", "CALL FOR AVAILABILITY", "CALL FOR PRICE",
    "NO NUMBER", "NOT AVAILABLE", "NOT APPLICABLE", "VARIOUS",
})

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")

# Denylist folded to bare alphanumerics so matching survives any separator or
# spacing variant the feed used ('N/A' == 'N\\A' == 'n a').
_DENYLIST_ALNUM: frozenset[str] = frozenset(
    _NON_ALNUM_RE.sub("", entry).upper() for entry in GARBAGE_REF_DENYLIST
)


def is_garbage_ref(ref: str | None) -> bool:
    """True when ``ref`` is not a usable cross-reference number: blank,
    fewer than 3 alphanumeric characters (a real OEM/competitor part number
    is never a 1-2 character code), or a known placeholder string."""
    s = (ref or "").strip()
    if not s:
        return True
    alnum = _NON_ALNUM_RE.sub("", s).upper()
    if len(alnum) < 3:
        return True
    return alnum in _DENYLIST_ALNUM


def purge_garbage_crossrefs(db: Session, dry_run: bool = True) -> dict:
    """Scan every cross_references row through ``is_garbage_ref`` and — when
    ``dry_run=False`` — delete ONLY the garbage rows. Legit rows are never
    touched. Flushes; the CALLER commits (so the router can attach the audit
    row to the same transaction).

    Returns {total_rows, garbage_rows, by_ref_number (top offenders,
    whitespace-folded + uppercased, worst first), deleted, dry_run}.
    """
    total_rows = db.query(func.count(CrossReference.id)).scalar() or 0

    offenders: Counter[str] = Counter()
    garbage_ids: list[int] = []
    # Python-side classification (not a SQL translation of the rule) so the
    # purge and the importer share literally the same predicate.
    for rid, ref in db.query(CrossReference.id, CrossReference.ref_number).yield_per(5000):
        if is_garbage_ref(ref):
            garbage_ids.append(rid)
            offenders[" ".join((ref or "").split()).upper() or "(blank)"] += 1

    deleted = 0
    if not dry_run and garbage_ids:
        # Chunked IN-deletes: SQLite's default host-variable ceiling is 999.
        for i in range(0, len(garbage_ids), 900):
            chunk = garbage_ids[i:i + 900]
            deleted += (
                db.query(CrossReference)
                .filter(CrossReference.id.in_(chunk))
                .delete(synchronize_session=False)
            )
        db.flush()

    return {
        "dry_run": dry_run,
        "total_rows": total_rows,
        "garbage_rows": len(garbage_ids),
        "by_ref_number": offenders.most_common(20),
        "deleted": deleted,
    }
