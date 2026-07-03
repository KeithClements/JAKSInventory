"""CLI: recompute open-quote / open-SO line ETAs.

Usage::

    python -m jaks_inventory.scripts.refresh_etas

Schedule via Windows Task Scheduler nightly to keep customer-facing
delivery dates fresh.
"""
from __future__ import annotations

import sys


def main() -> int:
    from engine.eta_refresh import refresh_all
    summary = refresh_all()
    print(
        "ETA refresh complete: "
        f"{summary['quotes_updated']}/{summary['quotes_scanned']} quote lines, "
        f"{summary['sos_updated']}/{summary['sos_scanned']} SO lines."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
