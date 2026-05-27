# Module: Marketing

Sub-screens: Text Messaging · SMS Campaigns · Automation.

**Existing code:** `jaks_inventory/ui/messaging_screen.py`,
`marketing_screen.py`, `automation_screen.py`,
`sms_inbox_screen.py`, `sms_settings_dialog.py`

---

## Text Messaging (1-to-1 inbox)

Standard SMS inbox UI:
- Left pane: conversation list (customer, last message preview, unread badge).
- Right pane: thread + composer with template picker.
- New conversation: pick customer → opens thread.
- Templates pull from `sms_templates` table.

Inbound messages are received by webhook (Twilio or vendor of choice), routed
to the matching customer by phone number, append to `messages` table.

### KPI strip
- Open conversations (unread)
- Messages sent today
- Messages received today
- Avg response time

---

## SMS Campaigns

Bulk outbound:
1. **Audience builder**: filter customers by tier/tag/last-order-age/etc.
2. **Message editor**: with variable placeholders `{first_name}`, `{esn}`,
   `{last_part}`. Live preview against a sample customer.
3. **Schedule**: send now or at a future time. Honors `Settings > SMS > quiet
   hours` and `customer.opt_out_sms`.
4. **Send** → progress bar, then results: delivered N, failed M.
5. Campaign stored in `campaigns` + per-recipient `campaign_recipients`.

### Reporting
- Open rate (link clicks)
- Reply count
- Opt-outs
- Quotes / orders attributed (when reply turns into a quote)

---

## Automation

Rule-based engine for hands-free outreach. Each rule = trigger + condition +
action.

### Triggers
- Quote sent (after N days no response)
- Invoice overdue (N days)
- Core outstanding (N days)
- Customer no order for N days
- PO received (notify customer waiting for backorder)
- Warranty registered

### Conditions
Filter by customer tier, $ threshold, has tag, opted in for SMS.

### Actions
- Send SMS from template
- Send email from template
- Create task for owner
- Notify manager

### Rule editor UI
Card-based: each rule shows trigger + condition summary + last fired + active
toggle. Click to open editor modal.

### Rate limits & safety
- Per-customer per-rule: max 1 fire / 7 days.
- Global: max 200 outbound SMS / day default (configurable).
- Always honor opt-out.
