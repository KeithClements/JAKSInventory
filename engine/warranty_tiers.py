"""Warranty tier definitions and policy boilerplate for JAK's Diesel.

Single source of truth for:

* The list of warranty tiers JAK's offers (used by the brochure PDF and
  by the warranty-line dialog when picking a duration).
* The policy boilerplate that prints on every certificate (Coverage,
  Duration, Resold Components, Exclusions, Claim Procedure, Disclaimers,
  Governing Law).

The structure of the policy (the section names + topics covered) is a
common industry template; the wording below is JAK's-specific. None of
this text is copied from any other vendor.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WarrantyTier:
    code: str
    label: str
    months: int
    coverage_type: str          # 'parts_only' | 'parts_labor'
    mileage_limit: int | None   # None = unlimited
    blurb: str                  # 1-2 line description for brochure


# Default ladder. Pricing is computed dynamically from the product's
# `warranty_percentage` (% of unit price per year), so we don't bake in
# fixed dollar amounts here — the brochure shows the tiers, the actual
# quote line shows the calculated price.
DEFAULT_TIERS: tuple[WarrantyTier, ...] = (
    WarrantyTier(
        code="STD-PO-6",
        label="Standard 6-Month Parts-Only",
        months=6,
        coverage_type="parts_only",
        mileage_limit=None,
        blurb="Included with every qualifying purchase. Covers replacement "
              "of the part itself if it fails due to defect.",
    ),
    WarrantyTier(
        code="EXT-PO-12",
        label="Extended 12-Month Parts-Only",
        months=12,
        coverage_type="parts_only",
        mileage_limit=None,
        blurb="Doubles your coverage window for parts replacement.",
    ),
    WarrantyTier(
        code="EXT-PL-12",
        label="Extended 12-Month Parts & Labor",
        months=12,
        coverage_type="parts_labor",
        mileage_limit=100_000,
        blurb="Adds covered labor at published flat-rate times, up to a "
              "$5,000 progressive-damage cap. 100,000 mile limit.",
    ),
    WarrantyTier(
        code="EXT-PL-24",
        label="Extended 24-Month Parts & Labor",
        months=24,
        coverage_type="parts_labor",
        mileage_limit=100_000,
        blurb="Two-year parts & labor coverage. 100,000 mile limit.",
    ),
    WarrantyTier(
        code="EXT-PL-36",
        label="Extended 36-Month Parts & Labor",
        months=36,
        coverage_type="parts_labor",
        mileage_limit=200_000,
        blurb="Best-value long-haul coverage. Three-year term, 200,000 "
              "mile limit.",
    ),
)


def tier_for_line(months: int, coverage_type: str) -> WarrantyTier | None:
    """Best-match tier for a sold warranty line. Returns None if custom."""
    if not months:
        return None
    for t in DEFAULT_TIERS:
        if t.months == int(months) and t.coverage_type == (coverage_type or "parts_only"):
            return t
    return None


# ---------------------------------------------------------------------------
# Certificate boilerplate
# ---------------------------------------------------------------------------
# Each section returns a (heading, body) tuple. The PDF generator
# renders these as Paragraphs in order.
# ---------------------------------------------------------------------------

def policy_sections(*, coverage_type: str, months: int) -> list[tuple[str, str]]:
    """Return the boilerplate sections for a certificate.

    The wording adapts subtly based on whether labor is covered.
    """
    is_pl = (coverage_type or "parts_only").lower() == "parts_labor"

    coverage_body = (
        "JAK&#39;s Diesel (&#34;JAK&#39;s&#34;) warrants the product identified on "
        "this certificate to be free from defects in material and workmanship "
        "when installed correctly, used for its intended purpose, and "
        "maintained per the engine manufacturer&#39;s recommendations. This "
        "warranty extends only to the original purchaser named above and is "
        "not transferable."
    )

    duration_body = (
        f"Coverage runs for {int(months)} month(s) from the invoice date "
        "shown on this certificate, subject to the mileage cap (if any) "
        "noted on the same line. Whichever limit is reached first ends "
        "coverage."
    )

    resold_body = (
        "For components manufactured by third parties and resold by JAK&#39;s, "
        "warranty coverage is limited to the warranty provided by the "
        "original manufacturer. The customer is responsible for shipping "
        "any allegedly defective third-party component to its manufacturer "
        "for inspection unless that manufacturer&#39;s policy provides "
        "otherwise."
    )

    if is_pl:
        remedies_body = (
            "On an approved claim within the coverage window, JAK&#39;s will "
            "either repair or replace the defective product on a like-for-"
            "like basis, and will additionally cover progressive damage "
            "directly caused by the failure up to a maximum of $5,000 "
            "(inclusive of labor reimbursed at published flat-rate times "
            "not to exceed $50.00 per hour)."
        )
    else:
        remedies_body = (
            "On an approved claim within the coverage window, JAK&#39;s will "
            "repair or replace the defective product on a like-for-like "
            "basis. Parts-only coverage does not include labor, "
            "progressive damage, or any incidental costs."
        )

    exclusions_body = (
        "Coverage is void where failure results from any of the following: "
        "abuse, misuse, neglect, accident, or improper operation; improper "
        "installation, handling, or storage; unauthorized alteration, "
        "modification, or repair; lack of timely maintenance; or unusual "
        "or extreme environmental conditions."
    )

    claim_body = (
        "To open a claim, contact JAK&#39;s within 30 days of the failure. "
        "All returns must be accompanied by this certificate, a packing "
        "list, the original invoice number, and an RMA number issued by "
        "JAK&#39;s. Approved claims become the property of JAK&#39;s; denied "
        "claims may be returned to the customer at the customer&#39;s "
        "expense if requested within 30 days of denial. The customer is "
        "responsible for shipping costs to JAK&#39;s. Any open invoice "
        "balance becomes immediately due on claim closure, and no further "
        "claims will be processed until those balances are paid."
    )

    disclaimer_body = (
        "This certificate sets out JAK&#39;s sole and exclusive warranty "
        "for the product. To the maximum extent allowed by law, all other "
        "warranties (including merchantability and fitness for a "
        "particular purpose) are disclaimed. JAK&#39;s liability is capped "
        "at the price paid for the product, and JAK&#39;s is not liable "
        "for indirect, incidental, consequential, or punitive damages of "
        "any kind, including downtime, towing, lost profits, or loss of "
        "goodwill."
    )

    return [
        ("1. Coverage", coverage_body),
        ("2. Duration", duration_body),
        ("3. Resold Components", resold_body),
        ("4. Remedies", remedies_body),
        ("5. Exclusions", exclusions_body),
        ("6. Claim Procedure", claim_body),
        ("7. Disclaimer & Limit of Liability", disclaimer_body),
    ]


def short_terms_summary(*, coverage_type: str, months: int) -> str:
    is_pl = (coverage_type or "parts_only").lower() == "parts_labor"
    if is_pl:
        return (
            f"{months}-month Parts &amp; Labor coverage from the invoice "
            "date. Covered labor at flat-rate up to $5,000. Subject to "
            "exclusions on reverse."
        )
    return (
        f"{months}-month Parts-Only coverage from the invoice date. "
        "Replacement of the warranted part on approved claim. Labor and "
        "progressive damage NOT covered. Subject to exclusions on reverse."
    )
