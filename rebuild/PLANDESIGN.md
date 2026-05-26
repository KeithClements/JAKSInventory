# JAKS Inventory — Design & Build Plan Synthesis
*Generated: 2026-05-25 | Source: All interview docs, DESIGN.md, PHASE_1_PLAN.md, researchdesign.md, FIGMA_DESIGN_BRIEF.md, MOCKUP_PLAN.md, SCHEMA_INTERVIEW.md*

---

## 1. Project Snapshot

**What this is:** A FastAPI + HTMX + Alpine.js + Tailwind CSS local web app for a small B2B diesel parts dealer. Two users (Keith: operations; wife: bookkeeping). SQLite database.

**Build philosophy locked:**
- Boring works. Density over whitespace. Predictability over surprise.
- "Bloomberg Terminal meets QuickBooks" aesthetic.
- Every screen is an operational console — designed for 6–8 hours/day use, not first-time discovery.

---

## 2. What Is Complete

### Foundation
- [x] Full SQLAlchemy schema (all 40+ tables from SCHEMA_INTERVIEW.md)
- [x] Service layer skeleton (13 services)
- [x] Settings + number sequences (INV, Q, SO, PO, RA, WC, CORE, VCR, RI)
- [x] Navigation: SALES / PURCHASING / INVENTORY / CORES / REPORTS / SYSTEM

### Templates — Design System Pass (all 15 DESIGN.md screens)
- [x] Base design system: `.card`, `.btn-*`, `.form-*`, `.tbl-*`, `.badge-*`, `.tab-*`
- [x] Dashboard
- [x] Customers (list + detail)
- [x] Quotes (list + workspace)
- [x] Quote workspace: chips, warranty tier picker, upgrade options, context menu
- [x] Sales Orders (list + detail)
- [x] Invoices (list + detail)
- [x] Payments (list + detail)
- [x] Purchase Orders (list + detail)
- [x] Vendors (list + detail)
- [x] Products (list + detail with 6-tab layout)
- [x] Core Charges (list + detail)
- [x] Warranty Claims (list + detail)
- [x] Print templates: quotes/print.html, invoices/print.html (brand colors corrected)

### Features Built
- [x] Quote PDF (weasyprint)
- [x] Invoice PDF
- [x] Payment workflow (record, allocate, reverse)
- [x] Suggested sells chips + inline chip row
- [x] Warranty tier picker (inline)
- [x] Upgrade option system (Economy/Recommended/Premium grouping)
- [x] Product enrichment panel (PAI/HHP/ATL scraper buttons)
- [x] Cross-reference confidence status (7 states)
- [x] Image management tab on product detail

---

## 3. What Is NOT Yet Built (Phase 1 Remaining)

### Priority queue (from PHASE_1_PLAN.md Part 8, Session 7):

| Priority | Feature | Gate Condition |
|---|---|---|
| NEXT-1 | DB recreate (add `line_role`, `is_included`, `option_label` columns) | Run before testing quote workspace |
| **NEXT-2** | **Quote PDF** | **#1 blocker per Keith** |
| **NEXT-3** | **Invoice PDF** | **#2 blocker** |
| **NEXT-4** | **Payment workflow** | **#3 blocker** |
| NEXT-5 | Customer balance panel on quote screen | After PDFs + payments solid |
| NEXT-6 | Research status on quote lines | After NEXT-5 |

### Remaining Phase 1 build steps (from PHASE_1_PLAN.md):
| Step | Module | Status |
|---|---|---|
| 8 | Purchase Orders 3-way match (po_receipts, vendor_bills) | Not built |
| 9 | Global search (Ctrl+K overlay) | Not built |
| 14 | Core Charges full lifecycle | Partial (models + template only) |
| 15 | Returns & Warranty Claims | Partial (models + template only) |
| 16 | Dashboard operational widgets | Partial |
| 17 | QBO integration | Not built |
| 18 | PDF for PO + core documents | Not built |
| 19 | Reports suite | Not built |

### UX features not yet built:
- Inline creation slide-overs (Quick Create: Customer, Product, Vendor) — #1 UX problem
- Global "Log Call" slide-over (from any screen)
- Quote pop-out window
- Quote auto-save with indicator
- Recently Viewed sidebar panel

---

## 4. The Most Important Screen: Quote Builder

Per every interview session and every planning document, the **Quote Builder workspace** is the most critical screen in the entire application. Keith's exact criteria:

> "I never touch the mouse. I can build a quote in 45 seconds. I can see all vendor options instantly."

### Required elements (non-negotiable):

**Header zone:**
- Customer field with `+` inline creation button
- Customer status mini-panel: Terms | Open | ⚠ Overdue | ✓ Credit | Cores Owed
- Quote number, date, expiry, validity
- Follow-up date field
- Discount % field
- Notes / Internal Notes

**Action buttons:**
- [⬡ Pop Out] — opens quote in separate window
- [Send] — marks sent
- [Convert ▼] — dropdown: to SO / to Invoice
- Auto-save indicator: "✓ Saved 5s ago"
- [Mark Lost]

**Part search bar:**
- Full-width, auto-focused on load
- Ctrl+K / `/` shortcut to focus
- Searches: SKU, OEM#, cross-refs, description, ESN, vendor SKU
- Dropdown shows: product name, QOH (color-coded), vendor availability, last sold to this customer
- Arrow keys navigate, Enter adds line, Esc closes

**Line items table columns (in priority order):**
1. # (row number)
2. Description (SKU bold + name below, source badge)
3. QOH (colored dot: green≥2, amber=1, red=0)
4. Source / Availability (vendor pills: PAI:In / PAI:12 / HHP:5 / ATL:—)
5. ETA (amber if >1d, red if >5d)
6. Sell $ (editable inline)
7. Disc % (editable)
8. Margin % (green/yellow/red by threshold)
9. Total
10. Actions (⋮ context menu)

**Chips row (below each product line):**
- `Add: [+SKU] [+SKU★] [+Warranty ▼]`
- Only appears for product lines with configured suggestions or warrantable products

**Follow-up bar (always visible at bottom):**
- `[ Follow Up Tomorrow ] [ Waiting Customer ] [ Waiting Vendor ] [ Truck Down ] [ No Follow Up ]`

---

## 5. Design System Status

All utility classes are defined in `base.html`:

```
.btn-primary, .btn-secondary, .btn-danger, .btn-ghost, .btn-sm, .btn-xs
.form-input, .form-select, .form-textarea, .form-label, .form-checkbox
.card, .card-header, .card-body, .card-footer, .card-title
.tbl, .tbl-head, .tbl-th, .tbl-th-r, .tbl-td, .tbl-td-r, .tbl-row, .tbl-empty
.tab-bar, .tab, .tab-active, .tab-inactive
.badge, .badge-green, .badge-red, .badge-amber, .badge-blue, .badge-gray, .badge-purple, .badge-brand
.link, .link-subtle
.stat-card, .stat-label, .stat-value, .stat-sub
.section-title
```

**Brand colors:**
- `brand-700` = `#4b5320` (olive) — buttons, links, active states
- `brand-600` = `#5e6928` — hover
- `brand-300` = `#b0be71` — sidebar accents (on dark bg)
- Sidebar background: `bg-slate-900`

**Semantic color rule (never override):**
- Red = errors, overdue, cancelled, 0-stock
- Amber = pending, partial, draft, waiting
- Green = paid, active, in-stock, confirmed
- Blue = informational, sent, in-progress

---

## 6. Three Visual Design Options for the Quote Builder

Three rendered HTML mockups have been created to show different visual approaches
for the most important screen. Files:

| File | Approach | Best For |
|---|---|---|
| `render_a.html` | Dense Ops Console | Users who want maximum data visible at once; ERP-classic feel |
| `render_b.html` | Balanced Operator *(RECOMMENDED)* | Matches DESIGN.md spec exactly; optimal density+readability balance |
| `render_c.html` | Command Line Focused | Power users who live on keyboard; emphasizes search bar + shortcuts |

**Recommendation:** `render_b.html` — it exactly implements what was designed and agreed in all sessions.

---

## 7. Key Design Decisions (All Locked)

| Decision | Value | Source |
|---|---|---|
| Nav structure | SALES / PURCHASING / INVENTORY / CORES / REPORTS / SYSTEM | Session 4 |
| Sales Orders placement | Own nav item under SALES | Session 4 |
| Cores | Own CORES section | Session 4 |
| Row height | 36px (`tbl-td` = `py-1.5`) | researchdesign.md |
| Monospace for | SKU, invoice#, quote#, ESN, check#, VIN | researchdesign.md |
| Currency | Right-aligned, `tabular-nums` | researchdesign.md |
| Table headers | `text-xs uppercase tracking-wide text-gray-500` | researchdesign.md |
| Zero-stock invoicing | Warn + prompt SO, allow invoice anyway | Session 2 (D2) |
| Invoice lock trigger | End of day OR QBO sync OR fully paid | Session 2 (D5) |
| Core credit method | Account credit default, override to check | Session 3 (D6) |
| Quote follow-up | Quick Follow-Up Bar, 5 one-click options | Session 4 |
| Research Queue | Own dashboard widget (separate from follow-ups) | Session 5 (R-D) |
| Vendor bill approval | Auto-approve if exact match, manual if discrepancy | Session 5 (D9) |
| Warranty chip behavior | Inline tier picker (not a slide-over) | Session 6 |
| Wife's access | Full owner-level, same as Keith | Session 4 |
| PDF generation | WeasyPrint, Jinja2 templates | PLAN.md |
| QBO sync | Push only: invoices, payments, vendor bills | Session 1 |

---

## 8. Open Questions Remaining (All Answered — For Reference)

All R-series decisions are locked. All D-series decisions are locked. No blocking unknowns remain before Phase 1 can complete. (See PHASE_1_PLAN.md Part 6 for full status.)

---

## 9. Next Recommended Build Sprint

Based on Keith's explicit priority answer ("biggest blocker is not being able to produce/send
professional quote PDFs and then smoothly convert that into invoice/payment workflow"):

1. **Approve quote builder visual design** (from 3 renders in this document)
2. **Implement Quote PDF** (weasyprint, includes is_included lines + alternatives section)
3. **Implement Invoice PDF** (same architecture, adds payment info)
4. **Complete payment workflow** (record payment slide-over, allocation logic)
5. **Customer balance panel** on quote workspace header
6. **Inline creation slide-overs** (Quick Create Customer, Product, Vendor)

Once those 6 items are solid in daily use, move to:
7. Global search (Ctrl+K)
8. PO receipt workflow (3-way match)
9. Core lifecycle management full implementation
10. QBO integration

---

## 10. Architecture Rules (Non-Negotiable Standing Rules)

1. **No transactional route may ever:** silently fail / directly mutate DB / bypass service layer / swallow exceptions.
2. **Every financial event requires:** visible error banners / audit logging / rollback behavior / centralized transaction handling.
3. **All monetary calculations are server-side.** Frontend displays; never trusts.
4. **Inventory changes only through controlled events:** PO receipt, invoice save, manual adjustment.
5. **Payment cannot exceed invoice total** — service layer enforces, not just a UI warning.
6. **Credit memos are the only way to modify a locked invoice.** No backdating.
7. **DESIGN.md is the authoritative visual spec.** All screen designs defer to it.
8. **Semantic colors are never replaced with brand:** red=errors, amber=warnings, green=success.

---

*This document synthesizes all planning materials as of 2026-05-25.*
*Update it after major sprint completions or design decisions.*
