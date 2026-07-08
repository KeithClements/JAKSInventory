"""
tests/smoke/
============
Playwright end-to-end smoke suite for the core JAKS business workflow.

This package drives the REAL app in a real browser against an isolated, freshly
seeded SQLite database, then asserts that the 12 business-critical steps actually
work end to end:

    1.  Create customer
    2.  Create quote from customer
    3.  Add product to quote (via product search)
    4.  Save quote
    5.  Convert quote to sales order
    6.  Add product to sales order
    7.  Convert sales order to invoice
    8.  Finalize invoice
    9.  Record payment
    10. Create purchase order
    11. Receive purchase order
    12. Confirm inventory quantity changes

It is NOT a visual / cosmetic suite — no pixel diffing, no styling assertions.
It only answers: do the workflows function?

Entry points
------------
- ``python -m tests.smoke.runner``  → run headless, write results JSON, exit non-zero on failure.
- ``tests/test_smoke_workflow.py``  → pytest wrapper (skips if Playwright/browser missing).
- ``GET /admin/smoke-tests``        → dashboard that renders the latest results JSON.

Design notes live in the module docstrings of ``server.py``, ``factories.py``,
and ``runner.py``.
"""
