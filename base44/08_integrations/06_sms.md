# Integration: SMS

**Existing code:** `jaks_inventory/messaging/*`, screens in
`jaks_inventory/ui/messaging_screen.py`, `sms_inbox_screen.py`,
`marketing_screen.py`, `automation_screen.py`.

SMS supports three distinct workflows:

1. **Transactional** — single-recipient messages tied to a document
   (quote sent, invoice ready, core reminder, RGA approved).
2. **1-to-1 conversations** — sales reps texting customers from inside the app.
3. **Campaigns** — bulk outbound to an audience built from filters.

---

## Provider

Twilio is the reference implementation. Vendor-agnostic interface:

```python
class SmsProvider(Protocol):
    def send(self, to: str, body: str, media_url: list[str] | None = None) -> SmsSendResult: ...
    def verify_webhook(self, headers, body) -> bool: ...
    def parse_inbound(self, payload) -> InboundSms: ...
```

Settings → SMS holds the provider credentials, from-number, and quiet hours.

---

## Outbound queue

`sms_outbound_queue` table:
- `id, customer_id, to_phone, body, media_urls, template_key, related_type, related_id, queued_at, send_after, status, attempts, last_error, sent_at, provider_message_id`

A worker (every 30 s):
1. Selects `pending` rows where `send_after <= now`.
2. Checks **quiet hours** and **per-customer caps** and **daily caps**.
3. Calls provider `send`.
4. Updates row with provider id + sent_at.
5. On failure, increments attempts and applies exponential backoff (1m / 5m / 15m / 1h).

Templates merge `{{first_name}}`, `{{quote_number}}`, `{{balance}}`, etc.
from the related entity.

---

## Inbound webhook

Provider posts to our webhook URL when an SMS comes in:
1. Verify signature.
2. Match `from_phone` against customer phones; if multiple match, pick most
   recent contact.
3. Insert `messages` row (direction=`inbound`).
4. Mark conversation unread, increment dashboard unread count.
5. If the body matches an opt-out keyword (`STOP`, `UNSUBSCRIBE`), set
   `customer.opt_out_sms = 1` and send a confirmation.
6. If an active automation rule is awaiting reply (e.g. "reply YES to confirm
   delivery"), route there.

---

## Templates

`sms_templates` table:
- `key` (unique, e.g. `quote_sent`)
- `name`
- `body` (with placeholders)
- `media_urls` (optional JSON array)
- `created_by`, `updated_at`

Standard templates seeded on first run:
- `quote_sent` — "Hi {{first_name}}, your quote #{{quote_number}} is ready: {{link}}"
- `invoice_ready` — "Invoice #{{invoice_number}} for ${{total}} is ready"
- `core_reminder` — "Reminder: ${{core_amount}} core return due by {{due_date}}"
- `rga_approved` — "Your return #{{rga_number}} has been approved"
- `delivery_eta` — "Your delivery is scheduled for {{eta}}"

---

## Quiet hours

Settings → SMS configures local-time start/end. Outside these hours:
- Transactional messages enqueue with `send_after = next_allowed_window_start`.
- Conversational messages from a logged-in user override quiet hours (rep
  is on the phone with the customer).

---

## Caps & opt-outs

- Global daily cap (default 200 outbound). Excess overflows to next day.
- Per-customer per-day cap (default 3).
- Hard opt-out via `customer.opt_out_sms` blocks ALL outbound.
- Soft opt-out per-campaign via `campaign_recipients.opted_out`.

---

## Audit trail

`sms_log` table records every outbound + inbound. Surfaced in
**Customer 360 → Messaging** and **Reports → SMS Activity**.

---

## For a Base44 implementation

- Use Base44 server-side actions to call Twilio's API directly.
- Webhook endpoint as a public Base44 action with HMAC verification.
- Schedule a 30-s action for the queue worker.
- `sms_outbound_queue` and `messages` as standard Base44 collections.
