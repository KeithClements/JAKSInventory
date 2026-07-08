# Unified Activity Log — Contract
*Backend · Drafted 2026-05-31 · Owner: Backend lane · Audience: UI-Builder, UI-Architect, QA*

Replaces the **three** overlapping call-logging surfaces (nav "Log Call" modal, customer "Call Log"
tab form, and the separate `/customers/{id}/communications` page) with **one** activity model, **one**
log affordance, and **one** per-customer **timeline**. Frozen from the owner interview (2026-05-31):
**structured type + outcome + note · follow-ups surfaced on the dashboard · log globally AND from a
quote/invoice/PO · link the note to that document.**

> Today's split is two tables under three UIs: manual notes land in `customer_call_logs`
> (`CustomerCallLog`) via `CRMService.log_call`, while the Communications page reads
> `communication_log` (`Communication`, the R12 immutable *sent-message* audit). A phone call lands
> in either store depending on which button was clicked. This contract makes the **manual** log one
> thing and *merges* the system-sent `communication_log` into the same timeline read-only.

---

## 1. Data model — extend `customer_call_logs` (don't add a new table)

`CustomerCallLog` already has: `customer_id`, `logged_by_id`, `logged_at`, `call_type`, `outcome`,
`quote_id`, `notes`. **Add five columns** (inline migration in `database.py` `_PENDING_COLUMN_ADDITIONS`;
jaks.db is disposable so a reseed also picks them up):

| New column | Type | Null | Purpose |
|---|---|---|---|
| `activity_type` | `VARCHAR(20)` | NOT NULL `DEFAULT 'call'` | kind of interaction (`ActivityType` below) |
| `follow_up_date` | `DATE` | NULL | "follow up by" — drives the dashboard widget |
| `follow_up_done_at` | `DATETIME` | NULL | stamp when the follow-up is marked done (NULL = still open) |
| `related_entity_type` | `VARCHAR(30)` | NULL | polymorphic doc link: `quote` / `invoice` / `purchase_order` |
| `related_entity_id` | `INTEGER` | NULL | the linked document's id |

- Keep `call_type` (inbound/outbound) as an optional **direction** field; `activity_type` is the new
  primary "kind". Keep `quote_id` as a back-compat alias — when `related_entity_type='quote'`, set both.
- Index `(follow_up_date)` where `follow_up_done_at IS NULL` (dashboard query) and
  `(related_entity_type, related_entity_id)` (doc-side panel).
- Consider aliasing the class as `Activity = CustomerCallLog` for readable new code; table name unchanged.

### Enums (`app/constants.py`)
```python
class ActivityType(StrEnum):     # NEW
    CALL          = "call"
    TEXT          = "text"
    COUNTER_VISIT = "counter_visit"
    EMAIL         = "email"
    NOTE          = "note"
```
Reuse `CallOutcome` for `outcome` (extend with `REACHED`, `VOICEMAIL` if not present; `NO_ANSWER`
exists). `outcome` is optional (a NOTE has no outcome).

---

## 2. Service API (`CRMService`)

```python
def log_activity(self, customer_id, activity_type, outcome=None, notes="",
                 follow_up_date=None, related_entity_type=None, related_entity_id=None,
                 direction=None) -> CustomerCallLog
    # The one write path. log_call() becomes a thin alias (activity_type='call').

def follow_ups_due(self, *, through=None) -> list[CustomerCallLog]
    # follow_up_date <= through (default today) AND follow_up_done_at IS NULL,
    # ordered by follow_up_date asc. Powers the dashboard widget.

def mark_follow_up_done(self, activity_id) -> None        # stamp follow_up_done_at = now

def get_timeline(self, customer_id, limit=100) -> list[dict]
    # MERGE customer_call_logs (manual) + communication_log (system-sent) for this customer,
    # newest first. Each item: {kind:'activity'|'comm', when, type, outcome, note/subject,
    # follow_up_date, related: {type,id,label}|None, logged_by}. Read-only — both stores stay
    # append-only.

def activities_for_entity(self, entity_type, entity_id) -> list[CustomerCallLog]
    # doc-side panel: every activity linked to a given quote/invoice/PO.
```

**Attribution:** `logged_by_id` = signed-in user via the O2 `get_current_user_id` dependency (Activity
Rule #4 — who logged it).

---

## 3. Routes (`app/routers/` — Backend owns the view fns)

| Route | Body | Returns | Notes |
|---|---|---|---|
| `POST /activities` | `customer_id, activity_type, outcome?, notes, follow_up_date?, related_entity_type?, related_entity_id?` | `204` + `HX-Trigger: activityLogged` (or the refreshed timeline partial) | the ONE log endpoint — global AND doc-context (doc passes the related_* fields pre-filled) |
| `POST /activities/{id}/follow-up-done` | — | refreshed widget row / `204` | mark-done |
| `GET /customers/{id}/timeline` | — | `customers/_timeline.html` partial | the Activity tab body |
| `GET /activities/follow-ups` | — | `dashboard/_followups_widget.html` partial | dashboard widget data |

Validation: `customer_id` required; if `related_entity_type` is set, `related_entity_id` must resolve;
`follow_up_date` not in the past on create is a warning, not a block.

---

## 4. What the UI builds (UI-Builder · to plan §8x, Architect governs)

1. **One "+ Log Activity" modal** (repurpose the nav "Log Call" modal). Fields: customer (pre-filled
   when opened from a customer/doc), activity_type, outcome, note, optional follow-up date, optional
   document link. Posts to `POST /activities`. Reused in three places, same component:
   - **Global** — header button (a call comes in while you're anywhere).
   - **Customer page** — "+ Log" on the Activity tab, customer pre-filled.
   - **Document workspace** — "Log Activity" button on Quote/Invoice/PO, `related_entity_*` pre-filled.
2. **Customer "Activity" timeline tab** — renders `GET /customers/{id}/timeline`: one chronological feed
   (manual activities + sent comms), each row with a type/outcome chip, the note, any linked-doc chip,
   and the logged-by user. **Replaces** the old "Call Log" tab AND the "Communications" page.
3. **Dashboard "Follow-ups due" widget** — `GET /activities/follow-ups`: today + overdue, each with the
   customer, note, and a "Done" action (`POST /activities/{id}/follow-up-done`).
4. **Doc-side activity panel** — on Quote/Invoice/PO workspaces, show `activities_for_entity(...)` (the
   call history about this document) + the "Log Activity" button.

**Retire** (after the timeline + modal land): the inline Call Log tab form, and the
`/customers/{id}/communications` page + its tab. `communication_log` stays as the **system-sent** store
that feeds the timeline — do not delete it.

**New UI primitive:** the **Timeline/feed** is a new archetype (not a list, not a queue) — Architect
ratifies its pattern (row format, chips, empty state) before UI-Builder builds it.

---

## 5. Verification (Backend writes; QA extends)

`tests/test_activity_log.py`: log each `activity_type`; `follow_ups_due` returns only open + due/overdue;
`mark_follow_up_done` stamps and drops it from the due list; `get_timeline` merges both stores newest-first;
`activities_for_entity` returns the doc's activities; `logged_by_id` = signed-in user. Plus a route smoke:
`POST /activities` from global, from a customer, and from a doc (related_* set) all persist.

---

*Migration order: Backend lands the columns + service + routes + contract, then UI-Builder builds the
modal/timeline/widget against it (Backend-leads seam). Architect ratifies the Timeline primitive first.*
