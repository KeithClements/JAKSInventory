# Vendor Contacts — Route Contract (O4)

Backend seam for the **Contacts card** on `vendors/detail.html`. Backend owns the
routes + business rules below; the **UI lane owns the card template**. These are
plain form-POST endpoints that 303-redirect back to the vendor detail page — no
Backend-owned partial, so the UI is free to design the card however it likes.

Shipped: routes live in `app/routers/vendors.py`; model `VendorContact` and the
`vendor_contacts` table already exist (no migration). Tests:
`tests/test_vendor_contacts.py`.

## Data the detail route already exposes
`GET /vendors/{id}` passes `vendor` to the template. From it:
- `vendor.contacts` — **all** contacts (active + inactive). The card should show
  only active ones: `{% for c in vendor.contacts if c.is_active %}`.
- `vendor.primary_contact` — the one `VendorContact` with `is_primary` (or `None`).

`VendorContact` fields: `id, name, role, phone, email, is_primary,
is_sales_contact, is_warranty_contact, is_returns_contact,
is_accounting_contact, is_active, notes`.

`role` ∈ `sales | warranty | returns | accounting | general`
(`app.constants.VendorContactRole`).

## Routes

| Method & path | Form fields | Effect |
|---|---|---|
| `POST /vendors/{vendor_id}/contacts` | `name` (required), `role`, `phone`, `email`, `is_primary` (checkbox), `is_sales_contact`, `is_warranty_contact`, `is_returns_contact`, `is_accounting_contact`, `notes` | Create. **First contact auto-becomes primary**; `is_primary=on` steals primary from any other. |
| `POST /vendors/{vendor_id}/contacts/{contact_id}` | same as create **except** `is_primary` | Update fields. **Does not** change primary (use make-primary) — avoids checkbox ambiguity. |
| `POST /vendors/{vendor_id}/contacts/{contact_id}/make-primary` | — | Set this contact primary; clears `is_primary` on all others (re-activates if needed). |
| `POST /vendors/{vendor_id}/contacts/{contact_id}/delete` | — | Soft-delete (`is_active=False`, clears primary). History preserved. |

## Rules Backend guarantees
- **At most one primary** per vendor — enforced server-side on create / make-primary.
- Checkboxes use standard HTML semantics: omit when unchecked, send `on` (or
  `true`/`1`) when checked.
- All routes **303-redirect** to `/vendors/{id}?saved=1#contacts` on success.
  Create with a blank name redirects with `?error=...#contacts` (show it near the form).
- Unknown vendor → `/vendors/`; unknown contact → `/vendors/{id}` (no 500).

## UI card — suggested shape (UI lane owns the markup)
- List active contacts; badge the primary; show role + a chip per `is_*_contact` flag.
- Per row: **Edit** (form → update route), **Make primary** (only if not primary),
  **Delete**.
- An **Add contact** form posting to the create route (name required; role select;
  phone/email; the flag checkboxes; `is_primary` checkbox).
- This is a §2A detail-page card — no filter tabs / bulk / dock.
