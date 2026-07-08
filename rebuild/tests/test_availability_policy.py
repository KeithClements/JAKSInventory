"""
tests/test_availability_policy.py
=================================
Pure unit tests for the sold-out-model storefront decision
(app/services/availability_policy.desired_state). No DB, no network — this is the
single source of truth for buyability, so every branch is pinned here.

Buyability rule = own shelf stock OR any vendor in stock; an UNKNOWN vendor status
('') is treated as "can still supply" so we never silently stop selling a part we
simply lack a reading for. Only an EXPLICIT vendor out_of_stock with zero own stock
produces a DENY / "Sold out".
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.constants import ProductStatus, VendorAvailability
from app.services.availability_policy import (
    POLICY_CONTINUE, POLICY_DENY, STATE_AVAILABLE_TO_ORDER, STATE_HIDDEN,
    STATE_IN_STOCK, STATE_SOLD_OUT, desired_state,
)


def _p(*, own=0, vendor="", is_active=True, status=ProductStatus.ACTIVE, pack=1):
    """A minimal duck-typed product (desired_state uses getattr only)."""
    return SimpleNamespace(
        qty_available=own, vendor_availability=vendor, is_active=is_active,
        status=status, pack_qty=pack, sku="JAKS-TEST",
    )


def test_own_stock_and_vendor_in_stock_is_in_stock_continue():
    ds = desired_state(_p(own=5, vendor=VendorAvailability.IN_STOCK))
    assert ds.state == STATE_IN_STOCK
    assert ds.shopify_status == "ACTIVE"
    assert ds.inventory_policy == POLICY_CONTINUE   # keep selling past our 5 (drop-ship)
    assert ds.qty == 5 and ds.buyable is True


def test_own_stock_but_vendor_out_sells_own_then_blocks():
    # We hold 3; vendor explicitly out → sell the 3 then go sold out (DENY at 0).
    ds = desired_state(_p(own=3, vendor=VendorAvailability.OUT_OF_STOCK))
    assert ds.state == STATE_IN_STOCK
    assert ds.inventory_policy == POLICY_DENY
    assert ds.qty == 3 and ds.buyable is True


def test_no_own_stock_vendor_in_stock_is_available_to_order():
    ds = desired_state(_p(own=0, vendor=VendorAvailability.IN_STOCK))
    assert ds.state == STATE_AVAILABLE_TO_ORDER
    assert ds.shopify_status == "ACTIVE"
    assert ds.inventory_policy == POLICY_CONTINUE   # buyable at qty 0 (drop-ship)
    assert ds.qty == 0 and ds.buyable is True


def test_no_own_stock_unknown_vendor_stays_buyable():
    # THE SAFETY CHOICE: '' (never imported an availability reading) must NOT block
    # the sale — treat unknown as can-supply. This protects the ~22k unknown parts.
    ds = desired_state(_p(own=0, vendor=""))
    assert ds.state == STATE_AVAILABLE_TO_ORDER
    assert ds.inventory_policy == POLICY_CONTINUE
    assert ds.buyable is True


def test_out_everywhere_is_sold_out_deny():
    ds = desired_state(_p(own=0, vendor=VendorAvailability.OUT_OF_STOCK))
    assert ds.state == STATE_SOLD_OUT
    assert ds.shopify_status == "ACTIVE"            # LIVE page, not a 404
    assert ds.inventory_policy == POLICY_DENY        # can't oversell
    assert ds.qty == 0 and ds.buyable is False


def test_vendor_discontinued_is_hidden():
    ds = desired_state(_p(own=0, vendor=VendorAvailability.DISCONTINUED))
    assert ds.state == STATE_HIDDEN and ds.hidden is True
    assert ds.shopify_status == "DRAFT" and ds.buyable is False


def test_deactivated_product_is_hidden_even_with_stock():
    ds = desired_state(_p(own=9, vendor=VendorAvailability.IN_STOCK, is_active=False))
    assert ds.state == STATE_HIDDEN and ds.shopify_status == "DRAFT"


def test_discontinued_status_is_hidden():
    ds = desired_state(_p(own=0, vendor="", status=ProductStatus.DISCONTINUED))
    assert ds.state == STATE_HIDDEN


def test_pack_qty_floors_to_whole_packs():
    # 12 pieces of a 5-pack part = 2 sellable packs (never 12).
    ds = desired_state(_p(own=12, vendor=VendorAvailability.IN_STOCK, pack=5))
    assert ds.qty == 2 and ds.state == STATE_IN_STOCK


def test_negative_own_stock_clamped_to_zero():
    # on-hand can go negative on admin corrections; never publish a negative.
    ds = desired_state(_p(own=-4, vendor=VendorAvailability.OUT_OF_STOCK))
    assert ds.qty == 0 and ds.state == STATE_SOLD_OUT
