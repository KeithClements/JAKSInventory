# Email Engine

Outbound transactional + reminder email over **SMTP** (Gmail/O365/own
server). No inbound parsing in this build. PDF attachments rendered
in-process.

## Tables

```sql
CREATE TABLE email_smtp_config (
    id              SERIAL PRIMARY KEY,
    host            TEXT NOT NULL,
    port            INT NOT NULL,
    username        TEXT NOT NULL,
    password_enc    TEXT NOT NULL,             -- encrypted
    use_tls         BOOLEAN NOT NULL DEFAULT TRUE,
    use_ssl         BOOLEAN NOT NULL DEFAULT FALSE,
    from_address    TEXT NOT NULL,
    from_name       TEXT,
    reply_to        TEXT,
    daily_cap       INT DEFAULT 500,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE email_templates (
    id              SERIAL PRIMARY KEY,
    key             TEXT UNIQUE NOT NULL,      -- 'quote_sent','invoice','past_due',...
    name            TEXT NOT NULL,
    subject         TEXT NOT NULL,             -- Jinja2
    body_html       TEXT NOT NULL,             -- Jinja2
    body_text       TEXT NOT NULL,             -- plaintext fallback
    attachment_kind TEXT,                       -- 'quote_pdf','invoice_pdf','po_pdf','statement_pdf','none'
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE email_outbound_queue (
    id              BIGSERIAL PRIMARY KEY,
    template_key    TEXT NOT NULL,
    to_addresses    TEXT[] NOT NULL,
    cc_addresses    TEXT[],
    bcc_addresses   TEXT[],
    context         JSONB NOT NULL,             -- merge vars
    related_type    TEXT,                       -- 'invoice','quote','po','rga','customer','internal'
    related_id      BIGINT,
    idempotency_key TEXT UNIQUE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/sending/sent/failed/suppressed
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error      TEXT,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ
);

CREATE TABLE email_log (
    id              BIGSERIAL PRIMARY KEY,
    queue_id        BIGINT,
    template_key    TEXT,
    to_addresses    TEXT[],
    subject         TEXT,
    related_type    TEXT,
    related_id      BIGINT,
    status          TEXT,                       -- 'sent','failed','suppressed'
    smtp_response   TEXT,
    error           TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE email_suppressions (
    address         TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,              -- 'hard_bounce','complaint','manual'
    suppressed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Outbound catalog (E2)

| key | trigger | recipient | attachment |
|-----|---------|-----------|------------|
| `quote_sent` | quote sent | customer | quote PDF |
| `shipment_notice` | SO shipped | customer | none (tracking in body) |
| `invoice` | invoice finalized | customer | invoice PDF |
| `payment_receipt` | invoice payment applied | customer | none |
| `statement_monthly` | monthly statement job | customer | statement PDF |
| `core_reminder_30` | customer core 30d before expiry | customer | none |
| `core_reminder_60` | 60d before expiry | customer | none |
| `core_reminder_90` | 90d / final | customer | none |
| `past_due` | invoice past due | customer | none |
| `rga_to_vendor` | RGA submitted/shipped | vendor | RGA PDF |
| `po_to_vendor` | PO sent | vendor | PO PDF |
| `internal_low_stock` | reorder-point breach (batched) | internal | none |
| `internal_qbo_failure` | QBO push failed permanently | internal | none |
| `internal_scrape_failure` | scrape job failed | internal | none |

**Not in scope (per E2):** order_confirmation, daily_summary.

## Template engine

Jinja2. Variables available depend on `related_type`:

- `invoice`: `{{ invoice }}`, `{{ customer }}`, `{{ lines }}`, `{{ company }}`, `{{ payment_link }}`
- `quote`: `{{ quote }}`, `{{ customer }}`, `{{ lines }}`, `{{ expires_at }}`
- `core_reminder_*`: `{{ customer }}`, `{{ event }}`, `{{ due_back_by }}`, `{{ amount }}`
- All templates have `{{ company }}` and `{{ today }}` in context.

Templates are seeded on first run via `seed_email_templates()`; admins may
edit via `PUT /email/templates/{key}`. A built-in `validate(template)`
sanity-checks the Jinja before saving.

## Send service

```python
def queue_email(template_key, to, context, *,
                cc=None, bcc=None,
                related_type=None, related_id=None,
                idempotency_key) -> int:
    if any(addr in suppressions for addr in to):
        log_suppressed()
        return
    insert email_outbound_queue row...
```

`idempotency_key` strategy:
- Transactional: `f"{template_key}:{related_type}:{related_id}"`
  (so reissuing the same invoice email is a no-op).
- Reminders: include the reminder window: `f"core_reminder_30:event:{id}"`.
- Bulk statements: `f"statement_monthly:{customer_id}:{period}"`.

## Worker

Loop every 30 s. For each `pending` row where `next_attempt_at <= now`:

1. Lock row (`status='sending'`).
2. Honor **quiet hours** if `template_key` starts with `internal_` or is a
   reminder; transactional sends ignore quiet hours.
3. Check daily cap (count `sent` today vs `email_smtp_config.daily_cap`);
   if exceeded → push `next_attempt_at` to tomorrow 00:05.
4. Render subject + body from template, merging `context`.
5. If `attachment_kind`, call PDF service for `related_type`+`related_id`.
6. Send via SMTP. Treat any non-2xx / non-250 as failure.
7. On success → status `sent`, write `email_log`, mark `sent_at`.
8. On failure → increment attempts, backoff `1m → 5m → 15m → 1h → 6h`.
9. SMTP hard bounce (5xx code in response) → add address to
   `email_suppressions`, status `failed`.

## PDF generation (E7)

In-process using **wkhtmltopdf** (preferred — small binary, easy to bundle).
Fallback to **Puppeteer** if richer rendering needed.

Pipeline:
1. Render an HTML template (separate from email body) with full styling.
2. Pipe to wkhtmltopdf → bytes.
3. Cache by `(template, related_type, related_id, payload_hash)` so
   re-sending the same invoice doesn't re-render.

```sql
CREATE TABLE pdf_cache (
    cache_key       TEXT PRIMARY KEY,
    template        TEXT NOT NULL,
    related_type    TEXT,
    related_id      BIGINT,
    bytes           BYTEA NOT NULL,
    rendered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    size_bytes      INT NOT NULL
);
```

A nightly job prunes entries older than 90d.

## Quiet hours + caps

`settings_kv['email.quiet_hours'] = '20:00-07:00'` (local time).
`settings_kv['email.daily_cap'] = 500` (per SMTP server).
`settings_kv['email.per_customer_per_day'] = 5` (sanity cap to avoid
spamming a single customer if an automation goes wrong).

## API surface

```
GET    /email/config
PUT    /email/config            -- updates SMTP (password encrypted at rest)
POST   /email/test              -- send test message

GET    /email/templates
GET    /email/templates/{key}
PUT    /email/templates/{key}
POST   /email/templates/{key}/preview -- render with sample context

POST   /email/send              -- enqueue ad-hoc send
GET    /email/queue?status=...
POST   /email/queue/{id}/retry
POST   /email/queue/{id}/cancel
GET    /email/log?related_type=...&related_id=...

GET    /email/suppressions
POST   /email/suppressions      -- add manual suppression
DELETE /email/suppressions/{address}
```
