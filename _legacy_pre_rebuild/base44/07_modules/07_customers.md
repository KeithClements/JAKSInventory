# Module: Customers

Single primary screen: **Customers Hub**.

**Existing code:** `jaks_inventory/ui/customers_hub_screen.py`,
`customer_detail_dialog.py`, `customer_360_dialog.py`,
`customer_credits_screen.py`

---

## KPI strip
- Active customers
- New this month
- Top customer (this year by $)
- AR outstanding ($)
- Credit balances ($)

## Attention chips
- `N customers over credit limit`
- `N quotes >7d no contact`
- `N customers with no order in 90d`
- `N missing tax info`

## Filter row
`[Tier ▾] [Type (retail/dealer/fleet/wholesale) ▾] [Tag ▾] [Active ▾] [×]`

## Table columns
Name, Type pill, Tier, City/State, Phone, Last order, Lifetime $, AR, Credit.

## Header actions
`[ + New Customer ] [ Import CSV ] [ Merge Duplicates ] [ Export ]`

## Detail dialog (Customer 360)

Tabbed layout:

| Tab | Contents |
|-----|----------|
| **Profile** | name, dba, type, tier, billing & shipping addresses, phones, emails, tax_exempt + cert#, default ship method, default warehouse, opt-in flags |
| **Contacts** | `customer_employees` list (name, role, phone, email, primary toggle) |
| **Addresses** | additional ship-to addresses |
| **Orders** | unified timeline: quotes, SOs, invoices, returns. Filter by type. |
| **AR** | open invoices, payment history, statements (print). Aging buckets summary. |
| **Cores** | outstanding `customer_cores`, history of returned cores |
| **Credits** | `customer_credits` ledger: issued, applied, balance |
| **Warranties** | open + closed warranty claims |
| **ESN History** | every engine serial number this customer ever sent us, parts purchased for it |
| **Notes** | thread of `customer_notes`, pin important ones |
| **Messaging** | SMS thread, last campaigns sent |

### ESN History tab — special

Show one row per ESN: serial, manufacturer, model, first seen date,
parts purchased (count), $ spent. Click ESN → list every quote/invoice
line that referenced it.

This is a major sales tool: when a customer calls about an engine, sales
opens the ESN tab and sees every prior part they've bought for that engine.

### Credit limit enforcement
On quote / SO / invoice save, if `customer.balance + new_total > credit_limit`:
- Show warning modal.
- If user role is `sales`, require manager override (PIN dialog → audit).
- Admin can override silently.

### Merge duplicates
Tool: select 2-5 customer rows → **Merge**. Picks a primary, moves all
references (quotes, SOs, invoices, notes, cores, etc.) to primary,
archives the rest with `merged_into_id`.
