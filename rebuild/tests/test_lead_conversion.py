"""
tests/test_lead_conversion.py
=============================
LeadConversionService — the ERP (system-of-record) half of the JAK's Lead
Finder integration. Covers the CONTRACT:

  • CREATE from a full packet (usdot, mapped customer_type, primary contact,
    import activity, type-defaults applied).
  • IDEMPOTENT — same packet twice → 2nd links, exactly ONE customer per DOT.
  • auto-mode fuzzy phone / name match → needs_review with NO write.
  • mode=create with an existing exact-USDOT customer → links (no dup).
  • mode=link to a given id transfers notes / contacts.
  • business_type → customer_type mapping (government → municipality).
"""
from __future__ import annotations

import pytest

import app.database as _appdb
from app.constants import ActivityType, CustomerType, PaymentTerms, PricingTier
from app.models.customer import Customer, CustomerCallLog, CustomerContact
from app.services.lead_conversion_service import LeadConversionService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def SessionLocal():
    """Module-isolated in-memory engine, pointed at the app globals."""
    engine = fresh_engine()
    return activate(engine)


@pytest.fixture()
def db(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _full_packet(**lead_overrides) -> dict:
    lead = {
        "lead_id": 101,
        "company_name": "Big Rig Hauling Co",
        "legal_name": "Big Rig Hauling Company LLC",
        "dba_name": "Big Rig Hauling",
        "usdot_number": 1234567,
        "mc_number": "MC-998877",
        "phone": "(555) 867-5309",
        "email": "dispatch@bigrig.example",
        "website": "https://bigrig.example",
        "address": {
            "street": "100 Depot Rd",
            "city": "Dallas",
            "state": "TX",
            "zip": "75201",
        },
        "business_type": "fleet",
        "power_units": 42,
        "driver_count": 50,
        "engine_platforms": "Cummins X15, DD15",
        "known_engines": "X15",
        "lead_score": 88,
        "origin": "fmcsa",
        "contacts": [
            {
                "name": "Jane Operator",
                "role": "Owner",
                "title": None,
                "phone": "555-111-2222",
                "mobile": "555-333-4444",
                "email": "jane@bigrig.example",
                "is_primary": True,
            },
            {
                "name": "Bob Mechanic",
                "role": "Shop",
                "title": "Lead Tech",
                "phone": "555-555-5555",
                "mobile": None,
                "email": "bob@bigrig.example",
                "is_primary": False,
            },
        ],
        "notes": [
            {"type": "research", "body": "Runs 40+ trucks out of Dallas.",
             "created_at": "2026-06-10"},
        ],
        "activities": [
            {"type": "call", "outcome": "reached", "subject": "Intro call",
             "body": "Talked to Jane about turbos.", "date": "2026-06-11"},
        ],
    }
    lead.update(lead_overrides)
    return lead


# ── CREATE ───────────────────────────────────────────────────────────────────

def test_create_from_full_packet(db):
    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({"lead": _full_packet(), "mode": "auto"})

    assert result["action"] == "created"
    cid = result["customer_id"]
    assert result["customer_url"] == f"/customers/{cid}"

    cust = db.query(Customer).filter(Customer.id == cid).first()
    assert cust is not None
    # dba_name wins for the company name.
    assert cust.company_name == "Big Rig Hauling"
    assert cust.usdot_number == 1234567
    # business_type fleet → CustomerType.FLEET, with FLEET type-defaults applied.
    assert cust.customer_type == CustomerType.FLEET
    assert cust.payment_terms == PaymentTerms.NET_30
    assert cust.pricing_tier == PricingTier.FLEET
    # Address + primary contact carried over.
    assert cust.city == "Dallas"
    assert cust.state == "TX"
    assert cust.contact_name == "Jane Operator"

    # Two CustomerContact rows, primary preserved; role used as title fallback.
    contacts = db.query(CustomerContact).filter(
        CustomerContact.customer_id == cid).all()
    assert len(contacts) == 2
    jane = next(c for c in contacts if c.name == "Jane Operator")
    assert jane.is_primary is True
    assert jane.title == "Owner"          # role → title (title was None)
    bob = next(c for c in contacts if c.name == "Bob Mechanic")
    assert bob.title == "Lead Tech"       # explicit title wins over role
    assert bob.is_primary is False

    # Import activity + the packet activity.
    logs = db.query(CustomerCallLog).filter(
        CustomerCallLog.customer_id == cid).all()
    assert any("Imported from JAK's Lead Finder" in (l.notes or "") for l in logs)
    assert any("lead #101" in (l.notes or "") for l in logs)
    assert any(l.activity_type == ActivityType.CALL for l in logs)

    # The Lead Finder summary line lands in internal_notes.
    assert "Lead Finder: USDOT 1234567" in cust.internal_notes
    assert "Runs 40+ trucks" in cust.internal_notes


# ── IDEMPOTENT ───────────────────────────────────────────────────────────────

def test_idempotent_same_packet_links_second_time(db):
    svc = LeadConversionService(db, current_user_id=None)
    packet = {"lead": _full_packet(), "mode": "auto"}

    first = svc.convert(packet)
    assert first["action"] == "created"

    second = svc.convert(packet)
    assert second["action"] == "linked"
    assert second["customer_id"] == first["customer_id"]

    # Exactly ONE customer for that DOT.
    dot_customers = db.query(Customer).filter(
        Customer.usdot_number == 1234567).all()
    assert len(dot_customers) == 1

    # Linking did not duplicate the two contacts.
    contacts = db.query(CustomerContact).filter(
        CustomerContact.customer_id == first["customer_id"]).all()
    assert len(contacts) == 2


# ── auto-mode fuzzy → needs_review (NO write) ───────────────────────────────

def test_auto_fuzzy_phone_match_needs_review(db):
    # Seed an existing customer with the same phone but NO usdot.
    existing = Customer(
        company_name="Totally Different Name",
        contact_name="",
        phone="555-867-5309",
        city="Dallas", state="TX",
    )
    db.add(existing)
    db.commit()
    before = db.query(Customer).count()

    svc = LeadConversionService(db, current_user_id=None)
    # Drop usdot so the exact-DOT short-circuit doesn't fire.
    result = svc.convert({"lead": _full_packet(usdot_number=None), "mode": "auto"})

    assert result["action"] == "needs_review"
    assert result["candidates"]
    assert any(c["match_reason"] == "phone" for c in result["candidates"])
    # NO write.
    assert db.query(Customer).count() == before


def test_auto_fuzzy_name_match_needs_review(db):
    existing = Customer(
        company_name="Big Rig Hauling Inc",   # normalizes to same key
        contact_name="",
        phone="555-000-0000",
        city="Dallas", state="TX",
    )
    db.add(existing)
    db.commit()
    before = db.query(Customer).count()

    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({
        "lead": _full_packet(usdot_number=None, phone="555-999-0000"),
        "mode": "auto",
    })

    assert result["action"] == "needs_review"
    assert any(c["match_reason"] == "name" for c in result["candidates"])
    assert db.query(Customer).count() == before


# ── mode=create with existing exact-USDOT → links (no dup) ───────────────────

def test_create_mode_links_existing_dot(db):
    existing = Customer(
        company_name="Pre-existing Carrier",
        contact_name="",
        usdot_number=1234567,
        phone="555-000-1111",
    )
    db.add(existing)
    db.commit()

    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({"lead": _full_packet(), "mode": "create"})

    assert result["action"] == "linked"
    assert result["customer_id"] == existing.id
    # Still exactly one customer for the DOT — no duplicate created.
    assert db.query(Customer).filter(Customer.usdot_number == 1234567).count() == 1


def test_create_mode_creates_when_no_dot_match(db):
    before = db.query(Customer).count()
    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({"lead": _full_packet(), "mode": "create"})
    assert result["action"] == "created"
    assert db.query(Customer).count() == before + 1


# ── mode=link transfers notes / contacts ─────────────────────────────────────

def test_link_mode_transfers_notes_and_contacts(db):
    target = Customer(
        company_name="Manual Link Target",
        contact_name="",
        phone="555-222-3333",
        internal_notes="Existing note.",
    )
    db.add(target)
    db.commit()
    tid = target.id

    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({
        "lead": _full_packet(),
        "mode": "link",
        "link_customer_id": tid,
    })

    assert result["action"] == "linked"
    assert result["customer_id"] == tid

    refreshed = db.query(Customer).filter(Customer.id == tid).first()
    # USDOT filled in (was blank).
    assert refreshed.usdot_number == 1234567
    # Notes appended, original preserved.
    assert "Existing note." in refreshed.internal_notes
    assert "Lead Finder: USDOT 1234567" in refreshed.internal_notes
    # Contacts transferred.
    names = {c.name for c in db.query(CustomerContact).filter(
        CustomerContact.customer_id == tid).all()}
    assert {"Jane Operator", "Bob Mechanic"} <= names
    # Import activity logged.
    assert any(
        "Imported from JAK's Lead Finder" in (l.notes or "")
        for l in db.query(CustomerCallLog).filter(
            CustomerCallLog.customer_id == tid).all()
    )


def test_link_mode_missing_target_raises(db):
    svc = LeadConversionService(db, current_user_id=None)
    with pytest.raises(ValueError):
        svc.convert({"lead": _full_packet(), "mode": "link",
                     "link_customer_id": 999999})
    with pytest.raises(ValueError):
        svc.convert({"lead": _full_packet(), "mode": "link",
                     "link_customer_id": None})


# ── business_type → customer_type mapping ────────────────────────────────────

def test_business_type_government_maps_to_municipality(db):
    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({
        "lead": _full_packet(usdot_number=2222222, business_type="government"),
        "mode": "create",
    })
    cust = db.query(Customer).filter(Customer.id == result["customer_id"]).first()
    assert cust.customer_type == CustomerType.MUNICIPALITY
    assert cust.is_tax_exempt is True   # municipality type-default


def test_business_type_other_with_power_units_maps_to_fleet(db):
    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({
        "lead": _full_packet(usdot_number=3333333, business_type="other",
                             power_units=5),
        "mode": "create",
    })
    cust = db.query(Customer).filter(Customer.id == result["customer_id"]).first()
    assert cust.customer_type == CustomerType.FLEET


def test_business_type_other_no_power_units_maps_to_other(db):
    svc = LeadConversionService(db, current_user_id=None)
    result = svc.convert({
        "lead": _full_packet(usdot_number=4444444, business_type="other",
                             power_units=0),
        "mode": "create",
    })
    cust = db.query(Customer).filter(Customer.id == result["customer_id"]).first()
    assert cust.customer_type == CustomerType.OTHER


# ── find_matches preview (no writes) ─────────────────────────────────────────

def test_find_matches_exact_usdot(db):
    existing = Customer(
        company_name="DOT Carrier",
        contact_name="",
        usdot_number=1234567,
        phone="555-777-8888",
        city="Houston", state="TX",
    )
    db.add(existing)
    db.commit()
    before = db.query(Customer).count()

    svc = LeadConversionService(db, current_user_id=None)
    matches = svc.find_matches(_full_packet())

    assert matches["exact_usdot"] is not None
    assert matches["exact_usdot"]["match_reason"] == "usdot"
    assert matches["exact_usdot"]["confidence"] == "high"
    assert matches["exact_usdot"]["customer_id"] == existing.id
    # Pure preview — no write.
    assert db.query(Customer).count() == before
