# R4 Go-Live Trial — Owner's Weekly Agenda

Companion to `TESTING_FEEDBACK_R4_GOLIVE_TRIAL.md`. That sheet is WHAT to test;
this is WHEN, and what "pass" actually looks like.

**The one question for every row:** *Can I finish the business task, and is the
money + inventory correct afterward?* Not "does the screen look right."

**Marks:** ✅ works · ⚠️ works-but-quirky (note the quirk) · ❌ broken (stop, report
same day) · — not tested · N/A. **Write a note on every ⚠️ and ❌** — "core credit
showed twice on the balance" is fixable same-day; "cores broken" is not.

**What counts as BLOCKING:** wrong money, wrong inventory, or a task you cannot
finish at all. Everything else (labels, layout, extra clicks) goes in the
non-blocking list — it will NOT hold up go-live.

---

## WEEK 1 — Prove it (the trial itself)

### Day 1 — Pre-flight + the two spines (~1–2 hrs)
- Part 0 pre-flight (0.1–0.6). Log in as BOTH admin and bookkeeper — confirm
  invoices/payments show the right user's name, not always "admin".
- **Lifecycle A** (PO → receive → inventory). Watch hardest at A.4/A.5: receive
  4 of 10, then 6 — inventory must go +4 then +6, and the moving-average cost
  must update. This is the historical "looks done, isn't" trap.
- **Lifecycle B** (quote → invoice → finalize → pay). At B.3 inventory must go
  DOWN by qty sold. At B.4 invoice hits PAID, balance exactly 0.
- If A or B fails: STOP. Mark it, note it, report it. Nothing else matters until
  the spines pass.

### Day 2 — The hard lifecycles (~1–2 hrs)
- **Lifecycle C** (backorder + deposit). The money trap is C.5: the deposit must
  be PRE-APPLIED to the invoice — deposit + final payment = total, never more.
- **Lifecycle D** (cores). D.1: core charge child line appears automatically.
  D.5: credit issued ONCE. Then push it: return a core, have the "vendor" deny
  part of it — the chargeback should claw back only the shortfall.
- **Lifecycle E** (A/R + statements). E.3/E.4: the aging bucket on the report,
  the customer detail bar, and the statement must all agree.
- Part 3 data-integrity checks DI.1–DI.6 against the records you just created.
  These must be EXACT, not "close".

### Day 3 — QBO sandbox connect + push pass (~1 hr)
Do this AFTER the lifecycles so you have real finalized documents to push.
- Set `JAKS_FERNET_KEY` BEFORE connecting (practice the real cutover flow —
  tokens encrypt at rest from the first connect).
- Sheet section 18 (18.1–18.7), plus the R2/R3 legs the sheet predates —
  see the QBO watch-list below.

### Day 4–5 — Screen-by-screen sweep (Part 2, ~30–45 min/day)
- Day 4: sections 1–8 (Dashboard → Payments). Test Smart Import HARD (section 4
  says so for a reason — it was just rebuilt; try a big file AND a junk file).
- Day 5: sections 9–20 (Vendors → Security). Receiving 11.2 is the canonical
  trap: verify inventory actually moves UP on each receive click.

### Weekend — Operational cutover (only if A–E all passed)
- [ ] Strong admin + bookkeeper passwords at `/account`
- [ ] One REAL backup → restore drill (break it on purpose, restore, verify)
- [ ] `JAKS_FERNET_KEY` set permanently in the environment
- [ ] Full Import the real catalog (vendor digits first: PAI=9, IMB=3 set;
      HHP/ATL get digits via the new auto-assign)
- [ ] Sign Part 4: GO / NO-GO

---

## WEEKS 2–3 — Earn it (the 2-week clean-use window)

Run the business on it daily. The last readiness points can only be earned here.

**Daily (5 min, end of day):**
- Today's invoices: totals right? Right user attributed?
- Any inventory count that surprised you? Note the SKU immediately.
- Payments recorded = money actually taken (card surcharge collected, not just shown).

**Weekly (30 min, Friday):**
- Pick 3 customers: ERP balance = Σ invoices − payments − credits, exactly.
- Pick 3 products: QOH = receipts − sales − returns, exactly.
- QBO reconcile: pick 5 pushed invoices — ERP total vs QBO total, and AR in QBO
  went to zero where payments were pushed.
- Cores: open core liability list matches reality on the shelf.

**Rule for the window:** any data-integrity surprise (money or inventory wrong
with no explanation) RESETS the 2-week clock after it's fixed. Quirks and polish
don't.

**End of week 3:** if the clock ran clean → go-live is done; flip QBO from
sandbox to the real company and keep the same weekly reconcile habit.

---

## QBO SANDBOX WATCH-LIST (what to report back)

All four push legs exist now — the sheet's section 18 only covers invoices, so add these:

| Push | Do | Verify IN QBO |
|---|---|---|
| Invoice (18.3) | Push a finalized invoice | Total matches the accounting-summary strategy: cc_surcharge + tax EXCLUDED; customer resolved/created correctly |
| Payment (R2) | Push the payment for that invoice | Payment applies to the right invoice; QBO AR for that customer → $0 |
| Vendor bill (R3) | Approve a bill in 3-way match, push | Bill appears with COGS expense lines + freight line; only APPROVED/PAID bills pushable |
| Credit memo (R3) | Issue + push a CM | CM lands against the right customer/invoice items |

**Cross-cutting checks:**
- Fail-soft: kill your network mid-push or push something invalid — the ERP
  document must stay intact (marked sync-failed), money path untouched.
- Two customers with the same name in QBO → push should REFUSE to auto-bind
  (multi-DisplayName guard), not pick one silently.
- Every push (success or fail) should leave an AuditLog row with YOUR user.
- Week-end drift report: any customer where ERP balance ≠ QBO balance — that's
  the #1 thing to report back, with the customer + documents involved.

**Known-not-built (don't report as bugs):** automatic sales-tax (AST) detection
and a background retry worker — both wait on the Phase-3 scheduler. Failed
pushes are retried manually for now.
