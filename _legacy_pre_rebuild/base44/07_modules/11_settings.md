# Module: Settings

**Existing code:** `jaks_inventory/ui/settings_screen.py`

Settings is a single screen with 8 sub-tabs.

---

## Tab 1: Company

- Legal name, DBA
- Phone, email, website
- Logo upload (used on PDFs)
- Address (HQ + warehouses)
- Time zone
- Fiscal year start month
- Default currency (USD)

Persists into `company_settings` (single-row).

---

## Tab 2: Users & Roles

Table: name, email, role (admin / sales / purchasing / warehouse / viewer),
last login, active toggle.

Actions: **Invite user**, **Reset password**, **Deactivate**.

Role permission matrix is displayed read-only — to change permissions, edit
the matrix in `03_business_rules.md#permissions` and redeploy.

Each user has:
- PIN (4-digit) for in-screen manager override prompts
- Default warehouse
- Notification preferences (email/SMS)

---

## Tab 3: Tax

Table of `tax_rates`: name, jurisdiction, rate %, default toggle, active toggle.

Per-customer tax overrides happen on Customer record (Profile tab → tax fields).

Tax-exempt customers require certificate upload.

---

## Tab 4: Shipping

Table of `shipping_rates`:
- Carrier
- Service level (Ground / 2-Day / etc.)
- Rate basis (flat / per lb / by zone)
- Surcharges
- Free-shipping threshold ($)

---

## Tab 5: Documents

Branding for outbound PDFs:
- Logo upload
- Header/footer text
- Terms text per doc type (quote, SO, invoice, PO, packing slip)
- Email subject + body templates for sending each doc type
- Numbering prefixes (Q-, SO-, INV-, PO-, RGA-, RCV-, ADJ-, CM-) and current
  sequence values (read-only display).

---

## Tab 6: QuickBooks Online

- **Connection:** Connect / Reconnect (OAuth flow), realm ID, expires_at.
- **Mode:** Mock / Read-only / Read-write (radio).
- **Account mapping:**
  - Income account
  - Inventory asset account
  - COGS account
  - Tax payable accounts (per tax rate)
  - Sales discounts account
  - Restocking fee account
  - Core liability account
- **Item creation policy:** auto-create on first push? Or require manual mapping?
- **Customer creation policy:** same.
- **Sync schedule:** queue worker interval (default 1 min).
- **Webhook URL:** display + verifier secret.
- **Open Sync Center** button.

---

## Tab 7: Shopify

- **Connection:** store URL, API access token.
- **Mode:** Disabled / Read-only / Read-write.
- **Publish rules:**
  - Auto-publish when `publish_shopify=1 AND qty>0` (toggle)
  - Auto-unpublish when qty=0 (toggle)
- **Inventory sync direction:**
  - Push local → Shopify (always)
  - Pull Shopify → local (off by default)
- **Order import:**
  - Frequency (default 10 min)
  - Status to import (`paid`, `paid+pending`, etc.)
  - Default customer to attach if no match
- **Tags & collections:** mapping `products.category` → collection.

---

## Tab 8: SMS

- **Provider:** Twilio / vendor of choice, API keys.
- **Send-from number:** display + test send button.
- **Quiet hours:** start/end (local time). Outside these hours, automation
  rules queue messages for the next allowed window.
- **Per-customer opt-out keyword:** `STOP` (default).
- **Daily caps:** total outbound, per-customer.
- **Templates:** CRUD for `sms_templates`.

---

## Save model

- All settings tabs autosave on field blur (except destructive changes).
- A footer banner shows "Saved 2s ago" or "Unsaved changes".
- Admin-only access to most tabs; Tax/Shipping/Documents allow purchasing role.
