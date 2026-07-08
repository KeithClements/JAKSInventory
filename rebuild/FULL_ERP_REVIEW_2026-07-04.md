# Axle / JAK's Diesel ERP — Full Multi-Persona Review (2026-07-04)

> **STATUS AS OF 2026-07-05: all 5 CRITICALs + 7 of the original HIGH findings are fixed,
> committed, and pushed to `backend/workflow-series-3`** (tags `v0.1.0-pretrial` →
> `v0.1.1-pretrial`). CI now actually runs the suite on every push (previously 0 runs ever).
> See the inline `✅ FIXED` / `[x]` markers throughout this document for exactly what changed
> and where. Still genuinely open: Ctrl+K price/availability, the New Return 19k-option picker,
> QBO auto-push-on-finalize + dead-token banner, overpayment/refund QBO push, credit-memo tax,
> PO-receive concurrency, session-key-at-rest, login credentials rotation + HTTPS-on-LAN (owner
> action), and a real (non-hash-order) test-isolation leak under active investigation on a
> separate branch. Full accounting in the session handoff.

**Method:** 12 persona reviewers read the real code and drove real HTTP through the TestClient
harness against isolated copies of the live schema. Every Critical/High was then re-read by an
adversarial verifier whose job was to *refute* it. 45 critical/high claims were verified: **31
CONFIRMED, 14 PARTIAL (real but rescoped), 0 fully refuted.** Two load-bearing facts were then
re-checked by hand against the working tree. Grades are per-persona and brutal by request.

**Overall grade: C+ today** — a B+/A- money-and-inventory *engine* wrapped in C-/D operational
readiness. The prior 07-01 review landed at the same C+/70; the engine has improved, but the
operational floor (backups, DR, onboarding, deployment) has not, and one new class of bug
(payment bypasses the invoice state machine) was found. **~1–2 weeks of targeted fixes gets this
to a real go-live; it is not there this morning.**

| Persona | Grade | One-line |
|---|---|---|
| Staff Engineer (CTO lens) | C+ | Good bones; a draft invoice can be paid to PAID with stock never moving. |
| Security Engineer | C+ | Plumbing is solid; the front door (default creds, plain HTTP, key-in-DB) is open. |
| UX / Product Designer | C+ | Core 7 screens are strong; the *paper you hand customers* is broken. |
| Daily Power User (counter) | B- | Faster than a dealer parts desk — with three silent oversell/negative-money traps. |
| New User (week one) | C- | Front door literally nailed shut: forced-rotation page 403s; onboarding doc is fiction. |
| Bookkeeper | C- | Engine is honest; the QBO handoff will book cash you never received. |
| Warehouse / Receiver | C+ | Best-in-class cores/receiving; the shelf, ERP, and website are three different stories. |
| Owner-Buyer (would I bet my shop?) | C- | No, not today — one disk, self-deleting backups, bus factor of one. |
| QA / Release Manager | B- | Best test suite I've seen at this tier; zero release engineering around it. |
| DevOps / IT | D+ | Great app, no seatbelt: no working backups, nothing restarts it, no logs, no monitoring. |
| Integrations Engineer | C+ | Excellent push buttons; not yet an integration architecture — every lane is one-way and blind. |
| ERP Industry Analyst | B- | Beats any buyable bundle on features for *this* shop; carries risks no vendor could ship. |

---

## What is genuinely better than the commercial alternatives (verified in code)

These are real, and most are things you cannot buy at this price:

1. **Core / reman lifecycle as a first-class dual-ledger system** — bidirectional core charges
   (customer-owes / vendor-owes), inspection outcomes, 6 physical core locations with an immutable
   movement trail, VCR batches with expected-vs-actual credit reconciliation, ESN warranty gating,
   idempotent denial chargebacks. **No benchmarked competitor (Fishbowl, inFlow, Katana, Fullbay,
   MAM, Vision) does vendor-side reman ledger accounting at all.** This is the moat.
2. **QBO sync-not-replace** — invoice/payment/vendor-bill/credit-memo push, fail-soft, off the
   money path, TxnDate on the real transaction date, encrypted tokens with the key stored *outside*
   the DB. MAM Autopart — the flagship vertical system — has no QuickBooks integration at all;
   adopting it means abandoning QBO. Axle never asks that.
3. **Real-time Shopify control on *standard* (non-Plus) Shopify** — auto-hide on vendor OOS,
   auto-relist (only listings the ERP hid), oversell guards, sell-pack enforcement. Acumatica needs
   scheduled cycles and Shopify Plus (~$2,300/mo) for the B2B equivalent.
4. **Per-customer cost-plus pricing waterfall** with volume breaks, date windows, and
   self-correction when receipt cost changes — B2B pricing depth that otherwise needs Shopify Plus
   or enterprise ERP.
5. **One shared money engine** (`app/invoice_totals.py`) consumed by model, workspace, and print —
   the three surfaces literally cannot disagree. Many commercial ERPs render three divergent totals.
6. **A real inventory ledger** (`InventoryService.apply_stock_delta` + append-only
   `InventoryTransaction` + nightly cache-vs-ledger resync) — not just a mutable counter. QuickBooks
   has nothing like it.
7. **A test suite better than most funded startups ship** — ~3,040 fast, isolated, genuinely
   assertive tests; a mechanical whole-app CSRF sweep with a floor so it can't silently shrink; a
   schema-drift roundtrip gate; a full quote→SO→PO→receive→invoice→payment→core-return E2E asserting
   money to the cent.
8. **Cost:** ~$115/mo (QBO Plus) vs $4,400–$24,000+/yr for anything that gets close on features.

The analyst's bottom line: **if this app vanished, the honest replacement is QBO Plus + inFlow +
a Shopify order connector (~$5k/yr) — and it would still have no core lifecycle, no pricing
waterfall, no cross-reference data, and no real-time storefront control. Axle beats the buyable
bundle today, and it isn't close.** The problems below are *why you still can't lean on it yet* —
not evidence that you should buy something else.

---

## CRITICAL — must fix before a single real invoice enters the trial

> **UPDATE 2026-07-04 (later same day): all 5 criticals are FIXED and merged into
> `backend/workflow-series-3`.** C1/C2/C3 landed via the spun-off worktree branches
> (verified + merged, the C2↔C3 overlap in the credit-memo/payment services reconciled
> so both guards coexist); C4 and C5 were implemented directly. New regression tests:
> `test_c2_draft_payment_gate.py` (10), `test_c3_account_credit_qbo.py` (4),
> `test_backup_restore.py`/`test_backup_acceptance.py` (17), `test_c4_first_login_recoverable.py`
> (4), `test_c5_ops_hardening.py` (8). Each item below is annotated with its resolution.

Every item here was verified against current code (several reproduced live).

### C1. The automatic backup silently deletes itself — you have no working backups right now
`prune_backups()` sorts backup filenames descending and keeps the top 10 matching `jaks-*.db`. The
pre-migration snapshots are named `jaks-pre*`, and in ASCII `'p'` sorts above every digit — so the
10 `jaks-pre*` files permanently occupy the entire retention quota and **every fresh dated backup
is deleted seconds after it is written, in the same startup call.** Verified live this morning:
`backup_last_run = 2026-07-04T11:06` (reports success) yet `backups/` contains only 10 `jaks-pre*`
files, newest data **July 2**. Compounding it: all backups sit on the **same disk** as the live
82 MB DB (no offsite copy), and restore is a JSON-only POST with **no admin UI** — the Settings
page points at an "Admin tools" page that does not exist. So today: no surviving daily backup, no
offsite copy, and no button you or a non-engineer could press to recover.
*Evidence:* `backup_service.py:49,123-127,144`; `database.py:436`; live `backups/` listing.
**This is the single scariest thing in the product.** Fix is ~1 line (change the sort/glob) plus a
nightly offsite copy (robocopy to cloud) plus one restore drill.
> **✅ FIXED** — `list_backups` now matches the dated pattern *exactly* (`jaks-YYYYMMDD_HHMMSS.db`);
> `jaks-pre*` snapshots are a separate pool (`list_snapshots`) that prune never touches. Added an
> offsite copy (`backup_offsite_dir` → OneDrive) that also ships the Fernet keyfile, a restore
> affordance, and `docs/BACKUP_RESTORE_RUNBOOK.md`. Restore drill passed; 17 backup tests green.

### C2. A DRAFT invoice can be paid straight to PAID with no stock decrement, no tax, no ledger row
`POST /invoices/{id}/payment` has no status guard; `record_payment`/`allocate` never check invoice
status; `refresh_payment_status` flips *any* balance-zero invoice to PAID and locks it. Reproduced
end-to-end over real HTTP: created a draft, added a $3,261 line, POSTed a payment → invoice became
`paid`/`locked`, **`qty_on_hand` never decremented, line tax stayed $0.00**, and `qbo_sync_status`
went `pending` (so the retry worker will push a never-finalized invoice to QBO). Recovery is hard
because `void_invoice` rejects PAID. The UI hides the button on drafts, but the server is the
contract — a replayed form, a stale tab, or any LAN client hits it. Every *other* money action got
atomic-claim hardening; this path was missed. *Evidence:* `payment_service.py:111-125`,
`invoice_service.py:1024-1041`, reproduced. Fix is a one-day draft guard.
> **✅ FIXED** — `PaymentService._assert_invoice_payable` gates `record_payment`/`allocate`/
> `apply_account_credit` (rejects DRAFT + VOID); `apply_credit_memo` rejects DRAFT; and
> `refresh_payment_status` raises on DRAFT and never resurrects VOID as a backstop. 10 tests green.

### C3. Applying a credit memo double-posts to QuickBooks (phantom cash)
`apply_credit_memo()` mints a synthetic `account_credit` Payment to reduce the invoice balance.
`push_payment()` has **no method filter** (zero references to `ACCOUNT_CREDIT` in the whole QBO
service), so that synthetic payment pushes to QBO as **real cash into Undeposited Funds** — while
the credit memo *also* pushes as a QBO CreditMemo. Net per applied CM: invoice shows paid with
money that never existed, the customer carries a duplicate credit, and Undeposited Funds never ties
to the bank. `apply_account_credit()` (customer credit balance) has the identical flaw. This is a
books-corrupting seam in the exact feature the 30-day trial exists to validate.
*Evidence:* `credit_memo_service.py:250-268`, `qbo_service.py:358-448` (no gate), reproduced.
> **✅ FIXED** — synthetic `ACCOUNT_CREDIT` payments are now created `qbo_sync_status=SKIPPED`;
> `push_payment` hard-refuses `method=account_credit` *before* any QBO call (covers legacy PENDING
> rows) and the retry lane excludes them. The CreditMemo document remains the sole QBO record of the
> credit. ERP balance math unchanged; 4 tests green.

### C4. First login is unrecoverable — you cannot onboard a human today
Logging in with a default credential force-redirects every page to `/account` until the password is
changed — but `auth/account.html` is a standalone page with **no `_csrf` field and no scripts**
(the CSRF-stamping JS lives only in `base.html`, which this page doesn't extend), and
`/account/password` is **not** CSRF-exempt. Submitting the form exactly as rendered returns a bare
`403 CSRF token missing or invalid`. Reloading can't fix it; the rotation gate blocks navigation
everywhere else. **There is no in-app escape** — a new hire (or you on a fresh box) is fully
trapped without a developer. Separately, a Sales-role hire's *first screen after a successful
login* is a raw JSON 403, because `/` is gated to admin/bookkeeper and non-404 errors render as
unstyled JSON. *Evidence:* `auth/account.html`, `security.py:50`, `main.py:113`, reproduced.
> **✅ FIXED** — `account.html` now carries a `_csrf` field + a minimal cookie-reading stamper
> (mirrors base.html), so the native form submits cleanly; a contextual banner explains the gate and
> the dead-loop "Back to dashboard" link is hidden while gated. Role-denied browser navigations now
> render a branded `errors/403.html` (extends base.html → full nav = a way forward) instead of raw
> JSON. 4 tests green, incl. the exact form-field submit that used to 403.

### C5. Nothing keeps the app running, and if it dies you're blind
No Windows service is installed and `service_install.py` is broken two ways (references a pywin32
attribute that doesn't exist → `install` crashes; and would `sys.exit(0)` instead of starting even
if installed). The real run model is a console window from a `.bat` ("keep this window open"). There
is **zero persistent logging** (no file handler anywhere — INFO is dropped, WARNING+ dies with the
console), **no `/health` endpoint, no watchdog, no monitoring**, and no disk-space check (data/ +
backups/ are ~1.3 GB and growing on C:). A 2 p.m. bluescreen or a Windows-Update reboot stops the
shop until someone physically revives it, and a background sync that dies on a Tuesday is
undiscoverable. *Evidence:* `service_install.py:75-89`, `main.py:126-655`, verified `/health`→404.
> **✅ FIXED** — rotating file logging (`logs/axle.log`, INFO+) configured at startup; a `GET /health`
> endpoint (auth-exempt) returns a DB check + backup/sync freshness (200 up / 503 degraded) for a
> watchdog to poll; `service_install.py` rewritten to register a Task Scheduler auto-start pointed at
> a new **supervised** headless runner (`scripts/axle_service_run.bat`) that auto-restarts uvicorn on
> crash; the landmine `START JAKS.bat` is now a safe redirect and `run.py`'s Windows-stalling
> `--reload` is opt-in. 8 tests green. *(Push alerting on sync failure and a disk-space check remain
> as HIGH follow-ups — a monitor can now be pointed at `/health` to cover the gap in the meantime.)*

---

## HIGH — will bite during the 30-day trial

> **UPDATE 2026-07-05: a second fix wave landed** (committed on `backend/workflow-series-3`,
> full suite green). Resolved from the list below: **A/R aging now excludes drafts**; **sales-tax /
> sales reports bucket by `locked_at`** (= QBO TxnDate) instead of `created_at`; **quote lines floor
> qty at 1**; **login lockout** (5 fails/username+IP → 60s); **physical-count loader** built
> (`InventoryService.apply_physical_count` + `scripts/load_physical_count.py`); and **CI now runs the
> full suite** from a root `.github/workflows/ci.yml`. New tests: `test_h_report_date_and_drafts.py`,
> `test_h_quote_qty_floor.py`, `test_login_throttle.py`, `test_physical_count_loader.py`. Still open
> below: Shopify order-feed proving, QBO manual-push/dead-token banner, price-lock on full import,
> credit-memo tax, overpayment/refund push, session key at rest, HTTPS-on-LAN, PO receive concurrency,
> printed-doc phone/commas.

**Integrations (every lane is one-way and blind):**
- **Shopify order feed** is now **committed** (`shopify_order_sync.py` @ `73bb0b7`, wired at
  `main.py` startup with a `ShopifyProcessedOrder` idempotency ledger) — the "untracked code crashes
  a clone" release blocker is resolved. Still treat it as **unproven until watched in the trial**: it
  should have a committed regression test and monitoring. The nightly stock sync is still a **blind
  "ERP-is-master" absolute overwrite** — confirm the order feed decrements before the overwrite can
  re-arm oversell on own-shelf items.
- **QBO "Connected" only means "tokens exist."** A dead refresh token shows a green connection while
  every push fails and the retry worker burns each document's 5-retry ceiling on the doomed refresh.
  On day 20 this looks like random per-invoice errors, not the one systemic failure it is.
- **QBO push is a manual chore** — nothing auto-pushes on finalize; payments/CMs/bills are
  per-document button clicks; the worker retries ERROR only (PENDING sits forever). Forgetting the
  button for a week silently invalidates the trial comparison. Failures surface only if you go look.
- **Shopify GraphQL is throttle-blind** (no 429/THROTTLED handling, unlike the QBO client), and
  **live-store drift detection (`refresh_live_status`) is manual-only** — the exact pattern behind
  the 2,611-listing incident stays open between hand-run audits.
- **Full-catalog re-import bypasses the price lock** — the `full_import` refresh branch writes
  `price_override` with no locked check and no threshold rail, silently clobbering hand-locked deal
  prices (the money-losing class the lock was built to prevent).

**Books / reconciliation (bookkeeper cannot trust it yet):**
- **A/R aging and the dashboard include unposted DRAFT invoices** (filter is only `status != VOID`).
  A $999.99 draft inflates A/R; the aging you'd hand the accountant is wrong by the sum of every
  open draft — and the app's *own* statements filter correctly, so surfaces disagree.
- **Period-basis mismatch:** ERP reports bucket by `created_at`; QBO gets `locked_at` as TxnDate.
  Every draft that straddles a month boundary lands in different periods in ERP vs QBO, by design.
- **Credit memos can never carry sales tax** → tax liability overstated after crediting any taxable
  sale. **Overpayments, unapplied cash, and refund checks structurally cannot reach QBO.**

**Data / go-live gap:**
- **No physical-count loader** — the go-live plan's *own gating task*. The bulk quantity importer
  doesn't exist, `receive_without_po`/`transfer_inventory` are dead code with zero callers, and the
  only ledger-backed entry is the single-product Adjust form. The live DB currently holds **4 SKUs /
  6 units** vs ~$68.6k of real stock. Even a flawless QBO trial leaves the scariest cutover — loading
  real inventory and flipping QBO perpetual→periodic — completely unrehearsed.

**Counter traps (silent wrong money / oversell):**
- **Ctrl+K global search reports on-hand, not available** — it'll tell you "3 ea" when all 3 are
  committed, making you promise parts on the phone that are already sold. It also shows **no price**,
  forcing a page load on the single most common call.
- **The qty box accepts `-3` and `0`** on quote lines → a negative quote/invoice with no pushback,
  on the busiest input in the app. (The invoice workspace clamps qty; the quote path does not, and a
  negative quote converts straight into an SO.)
- **The New Return picker ships the entire 19k-product catalog as `<option>` tags** (~1.9 MB, and it
  re-stamps per line) — it will freeze a real browser. It's the one screen that didn't get the nice
  typeahead every other line-adder uses.
- **Rejected inline edits fail silently** — HTMX doesn't swap 4xx bodies and there's no global error
  handler, so a bad value shows no message while the cell keeps *displaying* the rejected number:
  screen and DB now disagree until refresh.

**Security / deployment posture (before you bind it to shop Wi-Fi):**
- **`bookkeeper/bookkeeper` is still a valid live login** (verified against the DB), there's **no
  login lockout or rate-limiting** anywhere, and it's **plain HTTP on 0.0.0.0** — password and
  session cookie are sniffable on the network. The **session-signing key is stored in plaintext in
  the DB** (not under the Fernet scheme) and also signs public document links, so anyone who copies
  `jaks.db` or a backup can forge an admin session. Sessions can't be revoked.

**Release engineering (QA can't sign the go/no-go):**
- **No CI runs the 3,040-test suite** — and the two workflow files that exist sit at
  `rebuild/.github/` while the git root is one level up, so GitHub never even registered them (0 runs
  ever). **No git tags, no CHANGELOG, no version string**, and **untracked app code the app imports**
  (`availability_policy.py`, now `shopify_order_sync.py`) → a fresh clone crashes on startup. The
  **Playwright smoke suite hasn't run since June 1** (33 days, ~228 commits, all six change waves).
  You cannot answer "what build produced this invoice?" or roll back cleanly.
- **PO receiving has no concurrency guard** — the one money path left out of the atomic-claim
  hardening. A double-submitted receive (or two people processing one delivery) double-increments
  stock and writes two ledger rows the nightly resync can't heal.

**Customer-facing paper (what they judge you by):**
- ~~Every printed quote and invoice prints the customer's phone number twice, back to back, and
  shows money with no thousands separator.~~ **✅ FIXED 2026-07-05** — invoices.py/quotes.py's
  duplicate inline address-line builders consolidated onto the shared `customer_address_lines()`
  helper (document_render.py), and the redundant separate phone line was removed from all 6
  affected templates (invoice/quote/SO/warranty/RA/PO). All 52 money cells across all 5 documents
  (quote/invoice/SO/PO/customer statement) now use `"{:,.2f}".format(x)`, matching the convention
  dashboard.html already used. Verified live: phone renders exactly once, `$12,500.00` renders with
  a comma. Regression: `test_h_print_doc_money_and_phone.py` (5 tests); all 48 pre-existing
  print/PDF tests still green.

---

## MEDIUM / LOW — friction, drift, and debt (not trial-blocking)

- **Design-system drift:** five different filter-tab systems and four status-chip systems coexist
  across sibling screens; **Credit Memos is an orphaned money screen** (no nav, no Ctrl+K, no inbound
  link — your bookkeeper literally cannot find it); ~40 destructive actions still use native
  `window.confirm()` despite the design system's own mandate; **1,294 lines of dead `detail.html`
  templates** are being co-maintained alongside their live `workspace.html` twins; date/format
  anarchy (6+ display formats).
- **No barcode scanning at receiving** (only at the sales counter), **no cycle-count workflow** (two
  dropdown reasons, no count sheets/variance), **locations/bins are decorative for parts** (the cores
  side has real location tracking; your $2,000 turbos are one big pile).
- **Reporting is flat:** ~20 well-chosen point-in-time HTML+CSV reports, **zero charts, zero trend
  lines, zero scheduled delivery**. Even QBO alone gives trend graphs.
- **Messaging is still log-only** — the engine is complete but defaults to `NullMessagingProvider`;
  the app cannot actually email an invoice until SMTP/Twilio are configured. "One config step from
  parity" since the 06-17 plan.
- **No in-app help/glossary/tour** across ~180 templates — the UI assumes you know ESN/VCR/xref/core.
- **Non-404 errors render as raw JSON** in the browser; blank customers can be created server-side
  and then linger as nameless `—` rows with no delete affordance.
- **Money is float end-to-end with Python banker's rounding** — rescoped to medium by verification
  (QBO records the ERP's numbers verbatim, so it's draft-vs-finalize penny drift and float-accumulation
  risk, not a guaranteed QBO mismatch), but it's a foundation an accountant flags on principle.
- **SQLite is untuned:** no `busy_timeout`, WAL is a historical accident (a restore silently
  downgrades to rollback-journal mode), and `with_for_update()` on the document-number counter is a
  silent no-op that can 500 on a concurrent create.
- **~90 ms of PBKDF2 runs on every authenticated request** (the rotation middleware re-hashes the
  default password for `admin`/`bookkeeper` forever, even post-rotation) on the single async event
  loop — felt latency that serializes all LAN users. Move it off the hot path.
- **Schema truth lives in four hand-mirrored places** (model, inline `_PENDING_COLUMN_ADDITIONS`,
  Alembic revision, index name-match) and Alembic failures are swallowed at startup.

---

## Competitive position (analyst teardown)

| Tier | Example (2025-26 price) | Beats Axle on | Axle beats it on |
|---|---|---|---|
| Status quo | QBO Plus + spreadsheets (~$115/mo) | nothing operational | everything parts-specific |
| SMB inventory | inFlow ($186–439/mo), Katana ($299+/mo), Fishbowl ($4,395+/seat +~$10k/yr) | **barcode/mobile, hosted reliability, support org, onboarding** | core lifecycle, QBO-sync, real-time Shopify, pricing waterfall, cost |
| Auto/HD vertical | Fullbay ($188+/mo), MAM Autopart, Epicor Vision/Eagle | **~22M-row PartExpert interchange catalog, EDI, implementation** | MAM forces abandoning QBO; Fullbay is a repair-shop DMS, wrong shape |
| Enterprise | NetSuite, Acumatica, D365 BC | hosting/DR/SOC2, reporting depth, multi-entity | cost, diesel-real depth, customization speed |

**The 8 capabilities Axle lacks, ranked by impact for this shop, and who does each best:**
1. **Interchange/fitment data at scale** — Epicor PartExpert (~22M xrefs). *The one thing you cannot
   code around — it's data licensing.* Axle's xref table is thin scraped data (~35% coverage, 10,705-row
   purge pending). **Go price an ACES/PIES or PartExpert/LaserCat3 license — this wins the counter.**
2. **Hosted reliability + disaster recovery** — every SaaS competitor. Your one-disk / self-deleting-
   backup / bus-factor-of-one posture is the risk no vendor could legally ship.
3. **Barcode receiving/picking/counting** — inFlow (native mobile apps included), Fishbowl.
4. **Automatic web-order ingestion** — even QBO's free Shopify connector. (In-flight but uncommitted.)
5. **Automated replenishment / cross-vendor best-buy + lead-time math** — MAM Autopart. Cheap to build
   on your existing `ProductVendorSource` data.
6. **Dashboards with trends + scheduled report delivery** — Odoo, Katana, even QBO.
7. **Bidirectional accounting sync** — Fishbowl/Odoo (QBO edits/bank-feed deposits never flow back).
8. **EDI with PAI/IMB** — MAM (GCommerce/Corcentric). Defer at this scale, but it's a dependency with
   no SLA on your scrape/portal lanes.

---

## Pre-trial go/no-go checklist (entry criteria this fails today)

**Blockers — do before day 1:**
- [x] ~~Fix the backup prune bug + verify a real dated backup survives; add a nightly offsite copy
      (incl. the Fernet keyfile); do one restore drill.~~ **DONE (C1)** — two retention pools, offsite→OneDrive, restore drill passed.
- [x] ~~Guard the payment path: reject payment against a DRAFT invoice.~~ **DONE (C2)** — `_assert_invoice_payable` on all money entry points.
- [x] ~~Stop credit-memo application from double-posting cash to QBO.~~ **DONE (C3)** — `ACCOUNT_CREDIT` payments SKIPPED + refused pre-QBO.
- [x] ~~Fix the forced-password-rotation 403.~~ **DONE (C4)** — CSRF stamper on `account.html` + branded 403 page.
- [x] ~~Auto-start/auto-restart wrapper, rotating file logs, `/health` check.~~ **DONE (C5)** — Task Scheduler + supervised runner + `logs/axle.log` + `/health`.
- [~] **Login lockout DONE** (5 fails/username+IP → 60s cooldown). **Still owner action:** rotate the live
      `bookkeeper/bookkeeper` password (the rotation gate forces it on first login) and put HTTPS on the LAN
      (a local reverse proxy / self-signed cert — `JAKS_SECURE_COOKIES=1` is already wired for it).
- [x] ~~Wire the full suite into CI (move `.github/` to the git root — it's currently ignored).~~ **DONE** —
      root `.github/workflows/ci.yml` runs the full suite on `windows-latest`. *(Still to do: `git tag` the
      release, a rollback runbook, and relocating the stale ui-lint/visual workflows once baselines are refreshed.)*
- [x] ~~Build the physical-count loader so the trial tests real inventory.~~ **DONE** —
      `InventoryService.apply_physical_count` (ledger-backed, dry-run default) + `scripts/load_physical_count.py` CSV loader.
- [x] ~~Keep drafts out of A/R aging; pick one date basis; drop the qty floor.~~ **DONE** — aging filters to
      finalized statuses; sales-tax/sales reports bucket by `locked_at` (= QBO TxnDate); quote lines floor qty at 1.

**Should-fix in week 1:**
- [x] ~~Fix the printed-doc phone duplication + thousands separators (customers see this first).~~ **DONE**
- [ ] Ctrl+K: show *available* stock and price. Replace the New Return 19k-`<option>` picker with the typeahead.
- [ ] Auto-push invoices on finalize; add a loud "QBO token dead — reconnect" dashboard banner.
- [ ] Add a batch cap / circuit breaker on mass availability flips (one bad scraper run can hide the whole store).
- [ ] Put Credit Memos in the nav; add a global HTMX error handler; enforce WAL + `busy_timeout` in code.

---

## Bottom line

You have built something genuinely more capable than "an internal tool," and for a heavy-duty diesel
parts reseller the **feature combination is unmatched at any buyable price** — the core lifecycle
alone beats systems that cost $500–$2,000/month. The money-and-inventory *engine* is real, tested,
and in places better than the commercial stuff.

But the honest grade for **betting your operations on it today is C+**, and every persona that
lives outside the code — the bookkeeper, the new hire, the owner-buyer, the IT consultant — graded
it C-/D+. The reasons are boring and fixable and exactly the reasons a business fails on custom
software: **your backups are silently deleting themselves, a draft invoice can be paid without
moving stock, applying a credit memo books phantom cash into QuickBooks, nobody but you can operate
or resurrect it, and a new person can't even finish logging in.** None of these are architectural.
They're a punch list of maybe 1–2 weeks. Clear the blocker checklist above and this is a solid **B /
real go-live** — the engine already earned it.
