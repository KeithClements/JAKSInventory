# 3-Way Match Resolution — UI Contract
*Backend Workflow Series 5 · Published 2026-05-29*
*Owner: Backend lane · Audience: UI Builder (lane/ui-builder), UI Architect (lane/ui-architect)*

---

## What changed on the backend

The 3-way match workflow now has a write side. Previously AP could **view** flagged lines (over-billed, cost-variance) but had no way to act on them. That gap is closed.

New surface area:
- **2 POST endpoints** (resolve line, create vendor credit)
- **7 new keys** in the `_match_panel.html` row dict (all additive — existing keys unchanged)
- **5 new `state` values** for the badge map
- **`VendorBillStatus.PENDING`** — new transitional status between DISCREPANCY and APPROVED

The existing `_match_panel.html` still renders correctly. Nothing broke.

---

## Endpoints

### 1. Resolve a flagged line

```
POST /purchase-orders/{po_id}/bills/{bill_id}/lines/{line_id}/resolve
```

| Form field | Required | Values |
|---|---|---|
| `decision` | yes | `accepted` · `rejected` · `on_hold` · `cleared` |
| `reason` | required for `rejected` and `cleared` | free text |

**Success** → `303 ?ok=match_resolved`  
**Error** → `303 ?error=<human-readable message>`

Permission required: `APPROVE_VENDOR_BILL` (ADMIN + BOOKKEEPING).

---

### 2. Create a vendor credit memo for a flagged line

```
POST /purchase-orders/{po_id}/bills/{bill_id}/create-credit
```

| Form field | Required | Notes |
|---|---|---|
| `po_line_id` | yes | integer — the specific PO line with the variance |
| `amount` | no | float; omit to auto-compute from the variance |
| `reason` | no | free text |
| `apply_now` | no | `"1"` to immediately allocate VCM against the bill; `"0"` default |

**Success** → `303 ?ok=Vendor credit VCM-YYYY-NNNN created`  
**Error** → `303 ?error=<message>`

Permission required: `APPROVE_VENDOR_BILL` + `ISSUE_CREDIT_MEMO` (both; ADMIN + BOOKKEEPING).

Auto-computed amounts (when `amount` is omitted):
- `over_billed` → `(qty_billed − qty_received) × billed_unit_cost`
- `cost_variance` (vendor overcharged) → `(billed_cost − ordered_cost) × qty_billed`
- Matched line with no variance → **raises** (pass explicit amount or don't show the button)

---

### 3. Approve a vendor bill (existing, now gated)

```
POST /purchase-orders/{po_id}/bills/{bill_id}/approve
```

No form fields. Now **blocks with an error** if `bill.status == 'discrepancy'` (unresolved flags remain). Only works when `bill.status == 'pending'` (all flags resolved).

Permission required: `APPROVE_VENDOR_BILL`.

---

## New row-dict keys in `compute_match_line`

These are available in every `r` in `match.rows` passed to `_match_panel.html`. All existing keys (`line`, `ordered_qty`, `ordered_cost`, `received_qty`, `billed_qty`, `billed_cost`, `qty_var`, `cost_var`, `state`, `is_flag`) are **unchanged**.

| Key | Type | Meaning |
|---|---|---|
| `r.resolution` | `str` | Current AP decision. Default: `"unresolved"`. See values below. |
| `r.resolution_reason` | `str \| None` | AP's typed reason (for rejected/cleared). |
| `r.resolved_by_id` | `int \| None` | ID of user who resolved. |
| `r.resolved_at` | `datetime \| None` | UTC timestamp of resolution. |
| `r.resolution_vcm_id` | `int \| None` | Linked VCM id when `resolution == 'credited'`. |
| `r.suggested_credit` | `float \| None` | Auto-computed overage amount for the Create-Credit button prefill. `None` when line is matched or vendor undercharged. |
| `r.can_resolve` | `bool` | `True` only when the raw variance state is `over_billed` or `cost_variance`. Use to gate whether action buttons render at all. |

---

## Full badge/state map

Add these to the `state_meta` dict in `_match_panel.html`:

```jinja
{# existing #}
'matched':          ('badge-green',  'Matched'),
'awaiting_receipt': ('badge-gray',   'Awaiting receipt'),
'awaiting_bill':    ('badge-blue',   'Awaiting bill'),
'over_billed':      ('badge-red',    'Over-billed'),
'cost_variance':    ('badge-amber',  'Cost variance'),

{# NEW — add these #}
'rejected':           ('badge-red',   'Rejected'),
'on_hold':            ('badge-gray',  'On hold'),
'resolved_accepted':  ('badge-green', 'Accepted'),
'resolved_credited':  ('badge-green', 'Credited'),
'resolved_cleared':   ('badge-green', 'Cleared'),
```

`is_flag` truth table (controls the "N need review" counter and row highlight):

| `r.state` | `r.is_flag` |
|---|---|
| `over_billed` | `True` |
| `cost_variance` | `True` |
| `rejected` | `True` (AP still disputing — bill stays DISCREPANCY) |
| `on_hold` | `False` (de-prioritised, excluded from count) |
| `resolved_accepted` | `False` |
| `resolved_credited` | `False` |
| `resolved_cleared` | `False` |
| `matched`, `awaiting_*` | `False` |

---

## Expected button behavior

Show action affordances only on rows where **`r.can_resolve` is `True`**.

### Per-line action buttons

| Button | Form action | When visible | Notes |
|---|---|---|---|
| **Accept** | `decision=accepted` | `r.is_flag` or `r.state == 'rejected'` | No reason field needed |
| **Reject** | `decision=rejected` | `r.is_flag` | Requires inline reason input before submit |
| **Hold** | `decision=on_hold` | `r.is_flag` | No reason field needed |
| **Clear** | `decision=cleared` | `r.is_flag` or `r.state == 'on_hold'` | Requires inline reason input |
| **Create Credit** | create-credit endpoint | `r.is_flag` and `r.suggested_credit is not None` | Prefill `amount` from `r.suggested_credit`; pass `po_line_id=r.line.id` |

AP may re-decide from `on_hold` or `rejected` — the same buttons apply, same routes, service allows re-deciding.

### Resolved row display

When `r.state` starts with `resolved_` or is `on_hold`:

- Replace action buttons with a read-only attribution pill:
  `Resolved — Accepted · by [user] · [date]`
- If `r.resolution_vcm_id`, add a link: `→ VCM #[number]`
- If `r.state == 'on_hold'` or `r.state == 'rejected'`, still show the Re-decide options (those aren't terminal).

### Bill-level Approve button

Show when `bill.status == 'pending'` (all flags resolved — gate open).  
**Do not show** (or disable) when `bill.status == 'discrepancy'` — the service will reject it anyway with a clear error.  
The Approve button posts to the existing `/{po_id}/bills/{bill_id}/approve` route.

---

## VendorBillStatus flow (updated)

```
DISCREPANCY  →  (all flagged lines resolved)  →  PENDING  →  (AP clicks Approve)  →  APPROVED
     ↑                                                                                    ↓
   (rejected line keeps it here)                                                        PAID
```

`PENDING` is new. The existing `approved` and `paid` states are unchanged.

---

## Match queue page (`/purchase-orders/match`)

The cross-PO match queue lists all POs with `flag_count > 0`. After AP resolves lines:
- Lines that are terminal (`resolved_*`) drop out of `flag_count`
- `on_hold` lines also drop out of `flag_count` (but remain visible with the on_hold badge)
- `rejected` lines stay in `flag_count`
- A PO with `flag_count == 0` disappears from the queue on next page load

The queue page can use the same per-line action buttons as the workspace panel, pointing to the same routes (the `po_id` and `bill_id` are available from `po.bills`).

---

## No template changes required from backend lane

Backend has not touched any template file for this feature. The `_match_panel.html` and `match_queue.html` continue to render with the old state_meta dict (missing new badge states just fall back to `undefined` — add them before wiring buttons).

Suggested implementation order for UI lane:
1. Add the 5 new badge states to `state_meta` in `_match_panel.html` — zero-risk, just labels.
2. Add the `r.suggested_credit`, `r.resolution`, `r.can_resolve` template vars to resolved/flagged rows.
3. Wire the Resolve form (inline expand or slide-over) on flagged rows in the workspace panel.
4. Wire the Create-Credit form.
5. Add the Approve button gated on `bill.status == 'pending'`.
6. Apply same actions to `match_queue.html`.

---

*Questions or blockers → Backend support mode, respond on request.*
