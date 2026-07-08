# Cores Engine

> **Important context from user, captured verbatim:**
>
> *We need to track cores from the moment we purchase/receive a part from a
> vendor, not only after the customer returns a core.*

The engine treats cores as **three independent concerns** that link to each
other but are tracked separately. Do **not** collapse them into a single
"core" row.

---

## The 3 concepts

### 1. Customer Core Event

What the **customer owes us** (or what we owe the customer back).

States:
- `charged` — customer was charged a core on an invoice
- `returned` — customer brought the core back
- `credited` — we issued credit / refund
- `expired` — return window passed without a return

Lifecycle: `charged → returned → credited` (happy path) or
`charged → expired`.

### 2. Vendor Core Obligation

What the **vendor owes us** (vendor will refund us when we ship the used
core back to them).

States:
- `open` — created when we received a part with a refundable vendor core
- `eligible_for_rga` — a customer has returned a matching core to us
- `shipped` — included on an RGA we sent to the vendor
- `credited` — vendor issued credit / credit memo
- `closed` — fully reconciled
- `rejected` — vendor refused the core (damaged / wrong part)

Lifecycle: `open → eligible_for_rga → shipped → credited → closed`
(happy path), with `rejected` as an off-ramp at any point after shipped.

### 3. Physical Core Unit (quantity-tracked)

Where the **actual physical core** is. This is quantity-tracked, **not**
serialized at MVP. Each row represents N units of a given product SKU in
a given state.

States:
- `on_shelf` — sitting in our warehouse waiting to be sold
- `awaiting_customer_core` — we sold the part, customer hasn't returned
  their old one yet
- `ready_to_ship` — customer returned their old one; we hold it for vendor
- `shipped` — left our warehouse on an RGA
- `closed` — vendor credited us
- `rejected` — vendor refused; we still have it / wrote it off
- `written_off` — given up on, removed from books

---

## Tables

```sql
-- 1. Customer core event
CREATE TABLE customer_core_events (
    id              BIGSERIAL PRIMARY KEY,
    invoice_id      BIGINT REFERENCES invoices(id),
    invoice_line_id BIGINT REFERENCES invoice_lines(id),
    customer_id     BIGINT REFERENCES customers(id),
    product_id      BIGINT REFERENCES products(id),
    qty             INT NOT NULL,
    core_amount     NUMERIC(12,2) NOT NULL,         -- charged per unit
    status          TEXT NOT NULL,                  -- charged/returned/credited/expired
    charged_at      TIMESTAMPTZ NOT NULL,
    due_back_by     TIMESTAMPTZ NOT NULL,           -- charged_at + return window
    returned_at     TIMESTAMPTZ,
    credited_at     TIMESTAMPTZ,
    credit_method   TEXT,                           -- store_credit / cash / original_method
    credit_amount   NUMERIC(12,2),
    notes           TEXT
);

-- 2. Vendor core obligation
CREATE TABLE vendor_core_obligations (
    id                 BIGSERIAL PRIMARY KEY,
    vendor_id          BIGINT REFERENCES vendors(id) NOT NULL,
    product_id         BIGINT REFERENCES products(id) NOT NULL,
    po_id              BIGINT REFERENCES purchase_orders(id),   -- where it came from
    po_receipt_line_id BIGINT REFERENCES purchase_receipt_lines(id),
    qty                INT NOT NULL,
    expected_credit    NUMERIC(12,2) NOT NULL,
    status             TEXT NOT NULL,               -- open/eligible/shipped/credited/closed/rejected
    rga_id             BIGINT REFERENCES vendor_returns(id),
    actual_credit      NUMERIC(12,2),
    opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    eligible_at        TIMESTAMPTZ,
    shipped_at         TIMESTAMPTZ,
    credited_at        TIMESTAMPTZ,
    closed_at          TIMESTAMPTZ,
    notes              TEXT
);

-- 3. Physical core unit
CREATE TABLE core_units (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          BIGINT REFERENCES products(id) NOT NULL,
    qty                 INT NOT NULL,
    status              TEXT NOT NULL,
    vendor_obligation_id BIGINT REFERENCES vendor_core_obligations(id),
    customer_event_id   BIGINT REFERENCES customer_core_events(id),
    rga_id              BIGINT REFERENCES vendor_returns(id),
    location_notes      TEXT,                       -- "bin C-12", free text
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_status_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes               TEXT
);

-- Append-only state-transition log
CREATE TABLE core_unit_audit (
    id              BIGSERIAL PRIMARY KEY,
    core_unit_id    BIGINT REFERENCES core_units(id) NOT NULL,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    reason          TEXT,
    actor_user_id   BIGINT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Workflows

### W1. PO receiving — part with refundable vendor core

When a PO line is received for a product where `products.has_vendor_core = 1`:

```python
def on_po_line_received(line, qty_received):
    product = get_product(line.product_id)
    if not product.has_vendor_core:
        return

    # 1. Create vendor obligation
    obligation = create_vendor_core_obligation(
        vendor_id=line.po.vendor_id,
        product_id=product.id,
        po_id=line.po_id,
        po_receipt_line_id=line.receipt_line_id,
        qty=qty_received,
        expected_credit=product.vendor_core_amount * qty_received,
        status="open",
    )

    # 2. Create N core units (or one row with qty=N) on the shelf
    create_core_unit(
        product_id=product.id,
        qty=qty_received,
        status="on_shelf",
        vendor_obligation_id=obligation.id,
    )

    emit("core.received_from_vendor", {...})
```

### W2. Sale — customer is charged a core

When an invoice line is finalized for a product with `has_core = 1`:

```python
def on_invoice_line_finalized(line):
    product = get_product(line.product_id)
    if not product.has_core:
        return

    # 1. Open a customer core event
    event = create_customer_core_event(
        invoice_id=line.invoice_id,
        invoice_line_id=line.id,
        customer_id=line.invoice.customer_id,
        product_id=product.id,
        qty=line.qty,
        core_amount=product.core_sell_price,
        status="charged",
        charged_at=now(),
        due_back_by=now() + days(product.core_return_days or 90),
    )

    # 2. Move N core units from on_shelf -> awaiting_customer_core
    transition_core_units(
        product_id=product.id,
        qty=line.qty,
        from_status="on_shelf",
        to_status="awaiting_customer_core",
        customer_event_id=event.id,
    )

    emit("core.sold", {...})
```

### W3. Customer returns a core

```python
def on_customer_core_return(event_id, condition, credit_method):
    event = get_customer_core_event(event_id)
    assert event.status == "charged"

    # 1. Mark event
    update_customer_core_event(event_id,
        status="returned" if condition == "ok" else "rejected",
        returned_at=now(),
    )

    if condition != "ok":
        emit("core.customer_rejected", {...})
        return

    # 2. Issue credit
    credit = issue_customer_credit(
        customer_id=event.customer_id,
        amount=event.core_amount * event.qty,
        method=credit_method,
        source_event_id=event_id,
    )
    update_customer_core_event(event_id,
        status="credited",
        credited_at=now(),
        credit_method=credit_method,
        credit_amount=credit.amount,
    )

    # 3. Move core units awaiting_customer_core -> ready_to_ship
    transition_core_units(
        customer_event_id=event_id,
        from_status="awaiting_customer_core",
        to_status="ready_to_ship",
    )

    # 4. Flag matching vendor obligation as eligible
    obligation = find_matching_obligation(event.product_id, qty=event.qty)
    update_vendor_core_obligation(obligation.id,
        status="eligible_for_rga",
        eligible_at=now(),
    )

    emit("core.returned_by_customer", {...})
```

### W4. Ship cores to vendor (RGA)

```python
def create_rga(vendor_id, obligation_ids, carrier, tracking):
    rga = create_vendor_return(vendor_id=vendor_id, ...)

    for ob_id in obligation_ids:
        attach_obligation_to_rga(rga.id, ob_id)
        update_vendor_core_obligation(ob_id,
            status="shipped",
            shipped_at=now(),
            rga_id=rga.id,
        )
        transition_core_units(
            vendor_obligation_id=ob_id,
            from_status="ready_to_ship",
            to_status="shipped",
            rga_id=rga.id,
        )

    record_rga_shipment(rga.id, carrier=carrier, tracking=tracking)
    emit("core.shipped_to_vendor", {...})
```

### W5. Vendor credit applied

```python
def apply_vendor_credit(rga_id, credit_memo_number, line_credits):
    """line_credits = [{obligation_id, actual_credit}, ...]"""
    for lc in line_credits:
        update_vendor_core_obligation(lc.obligation_id,
            status="credited",
            credited_at=now(),
            actual_credit=lc.actual_credit,
        )
        transition_core_units(
            vendor_obligation_id=lc.obligation_id,
            from_status="shipped",
            to_status="closed",
        )

    record_vendor_credit(rga_id, credit_memo_number, sum(lc.actual_credit))

    # Each obligation -> closed once reconciled
    for lc in line_credits:
        update_vendor_core_obligation(lc.obligation_id,
            status="closed",
            closed_at=now(),
        )

    emit("core.credited_by_vendor", {...})
```

### W6. Vendor rejection (off-ramp from W5)

```python
def reject_vendor_core(obligation_id, reason):
    update_vendor_core_obligation(obligation_id,
        status="rejected",
    )
    transition_core_units(
        vendor_obligation_id=obligation_id,
        to_status="rejected",
        reason=reason,
    )
    emit("core.rejected_by_vendor", {...})
    # Operator decides: write off, scrap, attempt resale, etc.
```

### W7. Customer return window expiry (scheduled job)

Nightly: any `customer_core_events` with `status='charged'` and
`due_back_by < now()` → `status='expired'`. Optionally emit
`core.customer_expired` to drive an email reminder *N* days before expiry.

---

## Rules / invariants

1. **A `core_unit` row is bound to one and only one of:**
   `vendor_obligation_id`, `customer_event_id`, `rga_id` at any moment, plus
   `product_id` always.
2. **Status transitions are append-only via `core_unit_audit`.** Direct
   updates to `core_units.status` MUST go through `transition_core_units()`
   which writes the audit row.
3. **Cores are NEVER part of `products.qty_on_hand`** or invoiced as
   sellable inventory. They are a parallel ledger.
4. **Quantity-based at MVP.** Add serial/photo/inspection later by adding
   a `core_unit_serials` child table; don't bake serials into `core_units`
   schema yet.
5. **Customer side and vendor side stay independent.** The link is by
   `product_id` + matching when a customer returns a core. A vendor
   obligation may sit `open` indefinitely if no customer ever returns the
   matching core (we still owe nothing until we want our deposit back).
6. **Accounting sync is separate.** Each transition emits an event; the QBO
   layer subscribes and decides what JE/credit memo to push. The cores
   engine itself doesn't know about QBO.

---

## Open follow-ups (need user confirmation)

- **C1.** Default `core_return_days` if a product doesn't set one? (30 / 60 /
  90 / 180)
- **C3.** When a customer core expires, what should the engine do
  automatically?
- **C5.** RGA grouping — auto-create draft RGA at N obligations, $ threshold,
  time-based, or manual only?
- **C6.** Vendor credit variance threshold for auto-accept vs flag for review?
- **W6 follow-up.** When vendor rejects, default disposition: write_off /
  manual decide / try to sell as-is?

These are in `01_open_questions.md` — answer when ready.
