"""Migration 039 – Marketing Phase 2: sms_templates, saved_audiences,
campaigns, campaign_recipients, automation_rules, automation_log."""
from __future__ import annotations


def migrate(conn) -> None:
    stmts = [
        # ── SMS Templates ──
        """CREATE TABLE IF NOT EXISTS sms_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            body TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── Saved audiences (filter sets) ──
        """CREATE TABLE IF NOT EXISTS saved_audiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            filters_json TEXT NOT NULL DEFAULT '[]',
            customer_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── Campaigns (audit trail) ──
        """CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            channel TEXT NOT NULL DEFAULT 'sms',
            audience_id INTEGER REFERENCES saved_audiences(id),
            template_id INTEGER REFERENCES sms_templates(id),
            message_body TEXT,
            subject TEXT,
            status TEXT DEFAULT 'draft',
            scheduled_at TIMESTAMP,
            sent_at TIMESTAMP,
            total_recipients INTEGER DEFAULT 0,
            total_sent INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            total_replied INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── Campaign recipients ──
        """CREATE TABLE IF NOT EXISTS campaign_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            phone TEXT,
            email TEXT,
            status TEXT DEFAULT 'pending',
            sent_at TIMESTAMP,
            error TEXT,
            replied INTEGER DEFAULT 0,
            reply_body TEXT,
            replied_at TIMESTAMP
        )""",

        # ── Automation rules ──
        """CREATE TABLE IF NOT EXISTS automation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            config_json TEXT NOT NULL DEFAULT '{}',
            template_id INTEGER REFERENCES sms_templates(id),
            last_run_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── Automation log ──
        """CREATE TABLE IF NOT EXISTS automation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL REFERENCES automation_rules(id),
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            action TEXT,
            message TEXT,
            status TEXT DEFAULT 'suggested',
            acted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── Indexes ──
        "CREATE INDEX IF NOT EXISTS idx_sms_tpl_category ON sms_templates(category)",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status)",
        "CREATE INDEX IF NOT EXISTS idx_camp_recip_campaign ON campaign_recipients(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_camp_recip_customer ON campaign_recipients(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_auto_rules_type ON automation_rules(rule_type)",
        "CREATE INDEX IF NOT EXISTS idx_auto_log_rule ON automation_log(rule_id)",
        "CREATE INDEX IF NOT EXISTS idx_auto_log_customer ON automation_log(customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_auto_log_status ON automation_log(status)",
    ]

    for sql in stmts:
        try:
            conn.execute(sql)
        except Exception:
            pass  # table/index already exists

    # ── Seed default SMS templates ──
    existing = conn.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0]
    if existing == 0:
        templates = [
            # Quick Sales
            ("Back in Stock", "sales",
             "Hey {name} — just got {part} back in stock. Want me to set one aside for you?"),
            ("Hot Item", "sales",
             "Got some {part} available right now. These usually move quick — need one?"),
            ("One Left", "urgency",
             "I've got one left on the shelf — want me to hold it for you?"),
            # Quote Follow-up
            ("Quote Follow-Up", "follow_up",
             "Hey {name}, just checking if you want me to lock that quote in for you."),
            ("Quote Nudge", "follow_up",
             "Still need that {part}? I can get it moving today if you want."),
            # Core Reminders
            ("Core Reminder", "core",
             "Hey {name}, still need that core back on the {part}. Let me know if you need pickup arranged."),
            ("Core Credit", "core",
             "Quick reminder — core is still open on your last order. Once we get it back I'll get that credit issued."),
            # Payment
            ("Payment Reminder", "payment",
             "Hey {name}, just a heads up — we've got an open balance. Let me know if you want me to run a payment."),
            # Reactivation
            ("Reactivation", "reactivation",
             "Hey {name}, haven't heard from you in a bit — need anything right now?"),
            # Upsell
            ("Upsell Bundle", "upsell",
             "If you're doing that {part}, guys usually grab the kit with bolts + gasket. Want me to add that?"),
        ]
        for name, cat, body in templates:
            conn.execute(
                "INSERT INTO sms_templates (name, category, body) VALUES (?, ?, ?)",
                (name, cat, body),
            )

    # ── Seed default automation rules ──
    existing_rules = conn.execute("SELECT COUNT(*) FROM automation_rules").fetchone()[0]
    if existing_rules == 0:
        import json
        rules = [
            ("Quote Follow-Up", "quote_followup",
             json.dumps({"days_threshold": 2})),
            ("Core Overdue", "core_overdue",
             json.dumps({"days_threshold": 30})),
            ("Inactive Customer", "inactive_customer",
             json.dumps({"days_threshold": 60})),
            ("Restock Notification", "restock_notification",
             json.dumps({})),
            ("Credit Warning", "credit_warning",
             json.dumps({"threshold_percent": 90})),
            ("Open Core Warning", "open_core_warning",
             json.dumps({})),
        ]
        for name, rtype, config in rules:
            conn.execute(
                "INSERT INTO automation_rules (name, rule_type, config_json) VALUES (?, ?, ?)",
                (name, rtype, config),
            )

    conn.commit()
