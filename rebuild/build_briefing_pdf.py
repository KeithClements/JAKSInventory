"""Generate the JAKS Inventory parallel-coder briefing PDF."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    HRFlowable, ListFlowable, ListItem,
)

OUT = "JAKS_Parallel_Coder_Briefing.pdf"

# ── Palette (army-olive brand to match the app) ──────────────────────────────
BRAND = colors.HexColor("#4d5320")     # army olive
BRAND_LT = colors.HexColor("#eef0e2")
INK = colors.HexColor("#1f2328")
MUTE = colors.HexColor("#6b7280")
RED = colors.HexColor("#b91c1c")
RED_BG = colors.HexColor("#fdecec")
AMBER = colors.HexColor("#b45309")
AMBER_BG = colors.HexColor("#fdf4e3")
GREEN = colors.HexColor("#15803d")
BLUE = colors.HexColor("#1d4ed8")
LINE = colors.HexColor("#d8dbc8")
ZEBRA = colors.HexColor("#f6f7f0")

styles = getSampleStyleSheet()


def S(name, **kw):
    base = kw.pop("parent", styles["Normal"])
    return ParagraphStyle(name, parent=base, **kw)


body = S("body", fontSize=9.5, leading=14, textColor=INK, spaceAfter=6)
small = S("small", fontSize=8, leading=11, textColor=MUTE)
h1 = S("h1", fontSize=17, leading=21, textColor=BRAND, spaceBefore=4,
       spaceAfter=8, fontName="Helvetica-Bold")
h2 = S("h2", fontSize=12.5, leading=16, textColor=BRAND, spaceBefore=12,
       spaceAfter=5, fontName="Helvetica-Bold")
h3 = S("h3", fontSize=10.5, leading=14, textColor=INK, spaceBefore=8,
       spaceAfter=3, fontName="Helvetica-Bold")
cell = S("cell", fontSize=8.3, leading=11, textColor=INK)
cellb = S("cellb", fontSize=8.3, leading=11, textColor=INK,
          fontName="Helvetica-Bold")
cellw = S("cellw", fontSize=8.3, leading=11, textColor=colors.white,
          fontName="Helvetica-Bold")
mono = S("mono", fontSize=8, leading=11, textColor=INK, fontName="Courier")
callt = S("callt", fontSize=9.3, leading=13, textColor=INK)

story = []


def hr(c=LINE, w=0.8, sb=2, sa=8):
    story.append(HRFlowable(width="100%", thickness=w, color=c,
                            spaceBefore=sb, spaceAfter=sa))


def bullets(items, st=body, bullet="•", indent=14):
    return ListFlowable(
        [ListItem(Paragraph(t, st), value=bullet) for t in items],
        bulletType="bullet", start=bullet, leftIndent=indent,
        bulletFontSize=7, spaceBefore=0, spaceAfter=0,
    )


def callout(title, lines, accent=BRAND, bg=BRAND_LT):
    inner = [Paragraph(f"<b>{title}</b>", S("ct", fontSize=9.5, leading=13,
                                            textColor=accent))]
    for ln in lines:
        inner.append(Paragraph(ln, callt))
    t = Table([[inner]], colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))


def table(data, col_w, header=True, zebra=True, font=8.3):
    rows = []
    for r in data:
        rows.append([c if hasattr(c, "wrap") else
                     Paragraph(str(c), cellw if (header and data.index(r) == 0)
                               else cell) for c in r])
    t = Table(rows, colWidths=col_w, repeatRows=1 if header else 0)
    ts = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
    ]
    if header:
        ts += [("BACKGROUND", (0, 0), (-1, 0), BRAND),
               ("TOPPADDING", (0, 0), (-1, 0), 6),
               ("BOTTOMPADDING", (0, 0), (-1, 0), 6)]
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if (i - (1 if header else 0)) % 2 == 1:
                ts.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 8))


# ════════════════════════════════════════════════════════════════════════════
# COVER
# ════════════════════════════════════════════════════════════════════════════
story.append(Spacer(1, 1.4 * inch))
story.append(Paragraph("JAKS Inventory ERP", S("cv1", fontSize=30, leading=34,
             textColor=BRAND, alignment=TA_CENTER, fontName="Helvetica-Bold")))
story.append(Spacer(1, 6))
story.append(Paragraph("Parallel Coder Briefing &amp; Status Report",
             S("cv2", fontSize=15, leading=20, textColor=INK,
               alignment=TA_CENTER)))
story.append(Spacer(1, 14))
story.append(HRFlowable(width="42%", thickness=2, color=BRAND,
                        hAlign="CENTER"))
story.append(Spacer(1, 14))
story.append(Paragraph(
    "Three independent work lanes, runnable as parallel Claude Code sessions, "
    "with strict file ownership to guarantee zero merge collisions.",
    S("cv3", fontSize=10.5, leading=16, textColor=MUTE, alignment=TA_CENTER)))
story.append(Spacer(1, 1.6 * inch))
meta = Table([
    ["Prepared", "2026-05-29"],
    ["Repo", "C:\\Users\\keith\\JAKSInventory\\rebuild"],
    ["Branch baseline", "backend/workflow-series-3 (4 commits ahead of main)"],
    ["Model config", "3 lanes · fix blockers first · own-branch-per-lane"],
], colWidths=[1.5 * inch, 4.6 * inch])
meta.setStyle(TableStyle([
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("TEXTCOLOR", (0, 0), (0, -1), BRAND),
    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ("TEXTCOLOR", (1, 0), (1, -1), INK),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
]))
story.append(meta)
story.append(PageBreak())

# ════════════════════════════════════════════════════════════════════════════
# 1. STATUS
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("1 &nbsp; Where the Project Is", h1))
hr(BRAND, 1.2)
story.append(Paragraph(
    "The rebuild runs as three coordinated lanes. The active branch "
    "<font face='Courier'>backend/workflow-series-3</font> is a clean superset "
    "of <font face='Courier'>main</font> (4 commits ahead, 0 behind) and mixes "
    "backend Workflow Series 3&ndash;4 with UI-lane commits. The "
    "<font face='Courier'>feat/document-engine-series</font> branch is merged to "
    "main via PR&nbsp;#4.", body))

callout("Decisions locked — 2026-05-29 (Keith)", [
    "1. <font face='Courier'>data/jaks.db</font> is disposable dev data &mdash; "
    "<b>drop &amp; reseed</b> for Blocker 1; do not preserve.",
    "2. <font face='Courier'>purchase_orders/detail.html</font> is "
    "<b>confirmed unused</b> by routes/templates/tests &mdash; Backend deletes it.",
    "3. <b>QBO (Phase M) stays gated</b> &mdash; not started until the UI rollout "
    "and core operational flows (PO, invoice, receiving, match, lock, core) are "
    "stable.",
    "4. The 13 backend Open Assumptions are <b>confirmed as written</b>; do not "
    "pause to interview unless one blocks implementation or risks "
    "accounting/legal correctness.",
    "5. Macro deviations are the <b>Architect's call</b> &mdash; default: no new "
    "inline-script approvals unless explicitly accepted; restore "
    "<font face='Courier'>preserve_q</font> if it affects filter behavior; "
    "<font face='Courier'>empty_state</font> adoption may wait.",
    "6. Workspace drawers &rarr; slide-overs is <b>deferred to the L3 governance "
    "pass</b>; no midstream redesign unless drawers break usability.",
    "7. <b>Run concurrently, Backend leads slightly</b> &mdash; Backend lands "
    "blockers + route contracts first; UI must never build against guessed "
    "endpoints.",
], BRAND, BRAND_LT)

story.append(Paragraph("Lane 1 — Backend (Phases A–O)", h3))
table([
    ["Phase", "Area", "Status"],
    ["A", "Schema foundation — inline migrations, all new models", "Complete"],
    ["B", "Inventory + moving-average cost (inventory_service)", "Complete"],
    ["C", "Sales-Order fulfillment sources", "Partial"],
    ["D", "Tax + locked-invoice enforcement (Series 1)", "Complete"],
    ["E", "Payment rewrite (Series 2)", "Complete"],
    ["F", "Credit memos (credit_memo_service)", "Complete"],
    ["G", "Vendor credits + returns (Series 3)", "Complete"],
    ["H", "Cores + locations (Series 2)", "Complete"],
    ["I", "Statements + reports (8 report templates live)", "Complete"],
    ["J", "Search + optimistic locking", "Partial"],
    ["K", "Notifications + audit (notification_service + router)", "Complete"],
    ["L", "Communication foundation (messaging_service + 10 templates)", "Complete"],
    ["M", "QuickBooks Online push", "Deferred — fields exist, no push"],
    ["N / O", "Real messaging providers / serial + kits + merge", "Not started"],
], [0.55 * inch, 4.0 * inch, 2.0 * inch])

story.append(Paragraph("Lane 2 — UI (17-screen rollout + governance)", h3))
story.append(bullets([
    "<b>L2 reference lists complete:</b> Products, Purchase Orders, Invoices.",
    "<b>Primitives extracted</b> — 6 Jinja2 macros in "
    "<font face='Courier'>app/templates/macros/</font>; 3 lists ported. "
    "<font color='#b45309'><b>Macro library still pending an Architect "
    "governance pass.</b></font>",
    "<b>Queue Boards complete (QB2):</b> PO Receiving Queue + 3-Way Match Queue.",
    "<b>Workspaces:</b> PO + Invoice are L3-candidates; need a formal L3 "
    "governance pass.",
    "<b>Not started:</b> Customer List (L1.5&rarr;L2), Quotes alignment, Sales "
    "Orders, Vendors, Returns, Payments, Product Detail; Warranty / Cores / "
    "Returns queues.",
]))
story.append(Spacer(1, 4))
story.append(Paragraph("Lane 3 — Document Engine (merged to main)", h3))
story.append(Paragraph(
    "PO / SO / Core Slip / VCR / Warranty / RA print + PDF generation "
    "(<font face='Courier'>document_render.py</font>). Stable; not an active "
    "lane unless new document types are requested.", body))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════════════════
# 2. BLOCKERS
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("2 &nbsp; Pre-Flight: Fix These Blockers First", h1))
hr(BRAND, 1.2)
story.append(Paragraph(
    "Per your direction, all three lanes start from a green baseline. These are "
    "owned by <b>Lane B (Backend)</b> and should land before parallel feature "
    "work begins — they take well under a session combined.", body))

callout("BLOCKER 1 — Sales-Order list page returns HTTP 500", [
    "<font face='Courier'>GET /sales-orders/</font> throws "
    "<font face='Courier'>OperationalError: no such column: "
    "sales_orders.qbo_so_id</font>.",
    "<b>Cause:</b> the <font face='Courier'>SalesOrder</font> model "
    "(app/models/quote.py) declares <font face='Courier'>qbo_so_id</font>, the "
    "QBOSyncMixin columns, <font face='Courier'>payment_mode</font>, "
    "<font face='Courier'>deposit_amount</font>, "
    "<font face='Courier'>customer_job_number</font>, engine fields, "
    "<font face='Courier'>ship_to_address_id</font> and "
    "<font face='Courier'>updated_at</font> — but "
    "<font face='Courier'>_PENDING_COLUMN_ADDITIONS</font> in "
    "app/database.py has <b>no sales_orders entries</b>, so existing DBs never "
    "get them.",
    "<b>Fix (decided):</b> "
    "<font face='Courier'>data/jaks.db</font> is disposable &mdash; <b>drop &amp; "
    "recreate + reseed per Phase A</b> (fastest; do not preserve the file). "
    "Optionally also add the missing sales_orders columns to "
    "<font face='Courier'>_PENDING_COLUMN_ADDITIONS</font> so any other stale DB "
    "self-heals.",
    "<b>Done when:</b> the SO-list smoke test passes — "
    "<font face='Courier'>pytest \"tests/test_smoke.py::test_page_200[SO "
    "list]\"</font>.",
], RED, RED_BG)

callout("BLOCKER 2 — No test isolation (misleading red suite)", [
    "There is no <font face='Courier'>tests/conftest.py</font>; tests share one "
    "<font face='Courier'>data/jaks.db</font>. Files pass individually "
    "(series-3 is 20/20) but the full suite cascades to "
    "<b>38 failed / 24 errored</b> from shared state.",
    "<b>Fix:</b> add a <font face='Courier'>conftest.py</font> with a "
    "function/module-scoped fixture that builds a fresh in-memory (or "
    "temp-file) SQLite DB per test and overrides the FastAPI DB dependency.",
    "<b>Done when:</b> <font face='Courier'>pytest -q</font> is green end-to-end "
    "with no ordering dependence.",
], RED, RED_BG)

callout("HOUSEKEEPING (Backend lane / you)", [
    "<b>Delete</b> <font face='Courier'>app/templates/purchase_orders/detail.html</font> "
    "&mdash; verified unused (the <font face='Courier'>/{po_id}</font> route "
    "renders <font face='Courier'>workspace.html</font>; no route, include, or "
    "test references it).",
    "Prune 3 stale locked worktrees that point at deleted dirs: "
    "<font face='Courier'>git worktree prune</font> (then "
    "<font face='Courier'>git worktree list</font> to confirm).",
    "Commit or stash the 2 uncommitted edits "
    "(<font face='Courier'>JAKS_UI_Change_Plan.md</font>, "
    "<font face='Courier'>app/templates/invoices/list.html</font>) before "
    "branching so lanes fork from a clean tree.",
], AMBER, AMBER_BG)

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════════════════
# 3. BRANCHING
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("3 &nbsp; Branching &amp; Coordination", h1))
hr(BRAND, 1.2)
story.append(Paragraph("Setup (do once, before launching the lanes):", h3))
story.append(bullets([
    "Commit current work on <font face='Courier'>backend/workflow-series-3</font>, "
    "then merge it into <font face='Courier'>main</font> so every lane forks "
    "from one clean baseline.",
    "Land the two blockers (Section 2) on main.",
    "Create one branch per lane off main: "
    "<font face='Courier'>lane/ui-builder</font>, "
    "<font face='Courier'>lane/backend</font>, "
    "<font face='Courier'>lane/ui-architect</font>.",
]))
story.append(Paragraph("The golden rule of parallel work:", h3))
callout("No two lanes may edit the same file in the same session.", [
    "Collisions are prevented by <b>file ownership</b>, not by luck. Section 4 "
    "assigns every file to exactly one lane. The one structural seam — list "
    "<b>routes</b> (Backend) vs list <b>templates</b> (UI) — is handled the way "
    "the Invoice List already was: Backend lands the route (tabs, counts, "
    "preview endpoint) first; UI builds the template against it.",
    "Each lane commits only inside its ownership set. Rebase on main daily; "
    "integrate via PR. The Architect lane reviews before UI screens are marked "
    "complete.",
], BRAND, BRAND_LT)

# ════════════════════════════════════════════════════════════════════════════
# 4. LANES
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("4 &nbsp; The Three Parallel Lanes", h1))
hr(BRAND, 1.2)
table([
    ["Lane", "Role", "Branch", "Owns"],
    ["A", "UI Builder", "lane/ui-builder",
     "Screen templates (lists, queues, workspaces)"],
    ["B", "Backend", "lane/backend",
     "Services, models, list routes, DB, tests"],
    ["C", "UI Architect", "lane/ui-architect",
     "Macros, design system, governance, plan doc"],
], [0.5 * inch, 1.2 * inch, 1.5 * inch, 3.35 * inch])

# ---- LANE A ----
story.append(Paragraph("Lane A — UI Builder", h2))
story.append(Paragraph("Branch: <font face='Courier'>lane/ui-builder</font>",
             small))
story.append(Paragraph("Read first (in order):", h3))
story.append(bullets([
    "Invoke the <b>jaks-ui-governance</b> skill — it is the design system and is "
    "mandatory before writing any template code.",
    "<font face='Courier'>JAKS_UI_Change_Plan.md</font> — all sections, "
    "especially &sect;2 (Operational List), &sect;2A (Queue Board), the "
    "Governance Pass Checklist, and the Rollout Order.",
    "Reference screens: <font face='Courier'>app/templates/products/list.html</font> "
    "(L2 ref), <font face='Courier'>purchase_orders/receiving_queue.html</font> "
    "(QB2 ref).",
]))
story.append(Paragraph("Tasks, in rollout order:", h3))
story.append(bullets([
    "<b>Customer List</b> L1.5&rarr;L2 — build from the new macros from the "
    "start (dock, bulk toolbar, tabs, stripe). Depends on Lane B providing the "
    "tab/counts route + <font face='Courier'>/customers/preview/{id}</font>.",
    "<b>Warranty Queue, Cores Queue, Returns Queue</b> — QB2; copy "
    "<font face='Courier'>receiving_queue.html</font>, adapt state_meta + "
    "group-by + metrics. Depends on Lane B queue routes.",
    "<b>Quotes List</b> alignment pass — add border-l-4 stripe, preview dock, "
    "pb-52.",
    "<b>Sales Orders / Vendors / Payments / Returns lists</b> — L1&rarr;L2 full "
    "upgrades (remove tbl-* classes).",
    "<b>Product Detail</b> — section-based card layout L1&rarr;L2.",
]))
callout("Lane A — file ownership (edit ONLY these)", [
    "<font face='Courier'>app/templates/{customers,warranty,cores,returns,"
    "quotes,sales_orders,vendors,payments,products}/*.html</font> (screen "
    "templates + _preview_panel partials)",
    "<b>Do NOT</b> edit router .py, service .py, models, "
    "<font face='Courier'>macros/</font>, or "
    "<font face='Courier'>JAKS_UI_Change_Plan.md</font>. Need a route shape or "
    "a new macro? Request it from Lane B / Lane C.",
], BLUE, colors.HexColor("#eaf0fd"))
callout("Lane A — done criteria (per screen)", [
    "All 11 &sect;2 elements (or 8 &sect;2A elements for queues) present; no "
    "<font face='Courier'>tbl-*</font> classes; renders without error; submitted "
    "to Lane C for governance — <b>not</b> self-marked complete.",
], GREEN, colors.HexColor("#e9f6ee"))

story.append(PageBreak())

# ---- LANE B ----
story.append(Paragraph("Lane B — Backend", h2))
story.append(Paragraph("Branch: <font face='Courier'>lane/backend</font>", small))
story.append(Paragraph("Read first:", h3))
story.append(bullets([
    "<font face='Courier'>BACKEND_IMPLEMENTATION_PLAN.md</font> — the source of "
    "truth; match every task to its priority phase, do not skip ahead.",
    "<font face='Courier'>app/database.py</font> (inline migration mechanism), "
    "<font face='Courier'>app/services/base.py</font> (permission + optimistic "
    "lock helpers).",
]))
story.append(Paragraph("Tasks:", h3))
story.append(bullets([
    "<b>FIRST — both Section 2 blockers</b> (SO-list columns, conftest "
    "isolation). Gate the other lanes on these.",
    "<b>Finish Phase C</b> — SO fulfillment-source state machine + linked-PO "
    "FIFO allocation on receipt.",
    "<b>Finish Phase J</b> — wire the optimistic-lock version check into all "
    "financial save paths; SearchService ranking + phone normalization + "
    "combined OEM result.",
    "<b>Provide list routes for Lane A screens</b> — for each new L2 list, add "
    "the <font face='Courier'>tab</font> param + unfiltered counts + "
    "<font face='Courier'>/{resource}/preview/{id}</font> endpoint (registered "
    "<i>before</i> <font face='Courier'>/{id}</font>), mirroring "
    "<font face='Courier'>invoices.py</font>.",
    "<b>Phase M (QBO) — DO NOT START.</b> Gated until the UI rollout + core "
    "operational flows (PO, invoice, receiving, match, lock, core) are stable. "
    "Leave the existing <font face='Courier'>qbo_*</font> fields dormant.",
]))
callout("Lane B — file ownership", [
    "<font face='Courier'>app/services/*.py</font>, "
    "<font face='Courier'>app/models/*.py</font>, "
    "<font face='Courier'>app/database.py</font>, "
    "<font face='Courier'>app/constants.py</font>, "
    "<font face='Courier'>tests/*</font>, and the <b>route logic</b> (Python "
    "view functions) in <font face='Courier'>app/routers/*.py</font>.",
    "<b>Seam rule:</b> you may add/modify the route function, but do not "
    "restyle its template — that is Lane A. Touch only the .py side.",
], BLUE, colors.HexColor("#eaf0fd"))
callout("Lane B — done criteria", [
    "Each phase passes its plan-defined Gate; new/changed code has a test in "
    "<font face='Courier'>tests/</font>; full suite green; no model column "
    "exists without a matching migration entry.",
], GREEN, colors.HexColor("#e9f6ee"))

# ---- LANE C ----
story.append(Paragraph("Lane C — UI Architect (Pattern Owner / Governance)", h2))
story.append(Paragraph("Branch: <font face='Courier'>lane/ui-architect</font>",
             small))
story.append(Paragraph("Read first:", h3))
story.append(bullets([
    "The <b>jaks-ui-governance</b> skill and the full "
    "<font face='Courier'>JAKS_UI_Change_Plan.md</font> (you own this document).",
]))
story.append(Paragraph("Tasks:", h3))
story.append(bullets([
    "<b>Clear the pending governance backlog:</b> (1) macro-library pass &mdash; "
    "rule on the 3 flagged deviations per owner default: <i>do not</i> approve "
    "the inline-script P2 pattern unless you explicitly accept it; <b>restore "
    "preserve_q</b> if it affects expected filter behavior; "
    "<font face='Courier'>empty_state</font> adoption may wait if not blocking. "
    "(2) Invoice Workspace L3 pass; (3) PO Workspace L3 pass &mdash; at this "
    "pass, decide the drawers&rarr;slide-over question (deferred until here; no "
    "midstream redesign).",
    "<b>Own the macro library</b> — when Lane A needs a new primitive, you build "
    "/ approve it in <font face='Courier'>app/templates/macros/</font>.",
    "<b>Run a governance pass</b> on every Lane A screen before it is marked "
    "complete; punch specific issues, do not redesign.",
    "<b>Keep the plan current</b> — update the maturity table + Rollout Order "
    "and As-Built records as screens land.",
]))
callout("Lane C — file ownership", [
    "<font face='Courier'>app/templates/macros/*.html</font>, "
    "<font face='Courier'>JAKS_UI_Change_Plan.md</font>, and base/shared shells "
    "(<font face='Courier'>app/templates/base.html</font> slide-over / modal / "
    "Ctrl+K).",
    "Reviews Lane A output but edits screens only to correct an architectural "
    "defect (per the governance role).",
], BLUE, colors.HexColor("#eaf0fd"))
callout("Lane C — done criteria", [
    "Pending governance items closed; macro library approved; every "
    "completed-marked screen has a recorded governance pass in the plan.",
], GREEN, colors.HexColor("#e9f6ee"))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════════════════
# 5. COLLISION MATRIX
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("5 &nbsp; Collision Matrix (Who Owns What)", h1))
hr(BRAND, 1.2)
story.append(Paragraph(
    "If a path is not listed for a lane, that lane must not edit it. The only "
    "shared seam is router files, split by language: Python view logic (B) vs "
    "the template it renders (A).", body))
table([
    ["Path", "Owner"],
    ["app/templates/{screens}/*.html (lists, queues, workspaces)", "Lane A"],
    ["app/templates/macros/*.html", "Lane C"],
    ["app/templates/base.html (shared shells)", "Lane C"],
    ["JAKS_UI_Change_Plan.md", "Lane C"],
    ["app/routers/*.py — Python view functions / route logic", "Lane B"],
    ["app/services/*.py", "Lane B"],
    ["app/models/*.py · app/database.py · app/constants.py", "Lane B"],
    ["tests/*", "Lane B"],
    ["BACKEND_IMPLEMENTATION_PLAN.md", "Lane B"],
], [5.0 * inch, 1.55 * inch])
callout("Dependency handshakes (sequence these)", [
    "<b>New L2 list:</b> Lane B lands route (tab+counts+preview) &rarr; Lane A "
    "builds template &rarr; Lane C governance pass.",
    "<b>New queue board:</b> Lane B lands queue route &rarr; Lane A copies "
    "receiving_queue.html &rarr; Lane C pass.",
    "<b>New macro needed:</b> Lane A requests &rarr; Lane C builds/approves in "
    "macros/ &rarr; Lane A consumes.",
], BRAND, BRAND_LT)

# ════════════════════════════════════════════════════════════════════════════
# 6. LAUNCH
# ════════════════════════════════════════════════════════════════════════════
story.append(Paragraph("6 &nbsp; How to Launch Each Lane", h1))
hr(BRAND, 1.2)
story.append(Paragraph(
    "Open three Claude Code sessions in the repo, each checked out to its lane "
    "branch. Paste the matching kickoff prompt. Each prompt is self-contained. "
    "<b>Run concurrently, but stagger the start: launch Lane B first</b> and let "
    "it land the two blockers + the route contracts each UI screen needs; then "
    "bring up Lanes A and C, which follow the dependency handshakes in &sect;5. "
    "UI must never build against guessed endpoints.", body))

story.append(Paragraph("Lane A kickoff prompt", h3))
story.append(Paragraph(
    "You are the UI Builder for JAKS Inventory. First invoke the "
    "jaks-ui-governance skill and read JAKS_UI_Change_Plan.md in full. You own "
    "ONLY screen templates under app/templates/&lt;screen&gt;/ — never edit "
    "routers, services, models, macros, or the plan doc. Build the next Rollout "
    "Order screen (start: Customer List L1.5&rarr;L2) using the macros in "
    "app/templates/macros/. Confirm the list route + preview endpoint exist "
    "(ask Lane B if not). Submit each screen for governance; do not self-mark "
    "complete. Branch: lane/ui-builder.", mono))
story.append(Spacer(1, 6))
story.append(Paragraph("Lane B kickoff prompt", h3))
story.append(Paragraph(
    "You are the Backend coder for JAKS Inventory. Read "
    "BACKEND_IMPLEMENTATION_PLAN.md. FIRST fix two blockers: (1) add the missing "
    "sales_orders columns (qbo_so_id, QBOSyncMixin fields, payment_mode, "
    "deposit_amount, engine/job/ship fields, updated_at) to "
    "_PENDING_COLUMN_ADDITIONS in app/database.py so GET /sales-orders/ stops "
    "500ing; (2) add tests/conftest.py giving each test an isolated SQLite DB so "
    "the full suite is green. Then continue Phases C and J and provide L2 list "
    "routes on request. You own services, models, db, tests, and router view "
    "logic only — not templates. Branch: lane/backend.", mono))
story.append(Spacer(1, 6))
story.append(Paragraph("Lane C kickoff prompt", h3))
story.append(Paragraph(
    "You are the UI Architect for JAKS Inventory. Invoke the jaks-ui-governance "
    "skill; you own JAKS_UI_Change_Plan.md and app/templates/macros/. Clear the "
    "pending governance backlog: (1) macro-library pass incl. the 3 flagged "
    "deviations; (2) Invoice Workspace L3 pass; (3) PO Workspace L3 pass. Then "
    "review each Lane A screen before it is marked complete and keep the plan's "
    "maturity table current. Punch specific issues; do not redesign. Branch: "
    "lane/ui-architect.", mono))

story.append(Spacer(1, 14))
hr(BRAND, 1.0)
story.append(Paragraph(
    "Open questions for Keith are listed in the chat alongside this document. "
    "This PDF is generated from JAKS_UI_Change_Plan.md, "
    "BACKEND_IMPLEMENTATION_PLAN.md, the git history, and a live test run on "
    "2026-05-29.", small))

doc = SimpleDocTemplate(OUT, pagesize=letter,
                        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
                        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                        title="JAKS Inventory — Parallel Coder Briefing",
                        author="Claude Code")


def footer(canvas, d):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTE)
    if d.page > 1:
        canvas.drawString(0.85 * inch, 0.45 * inch,
                          "JAKS Inventory — Parallel Coder Briefing")
        canvas.drawRightString(7.65 * inch, 0.45 * inch, f"Page {d.page}")
    canvas.restoreState()


doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("WROTE", OUT)
