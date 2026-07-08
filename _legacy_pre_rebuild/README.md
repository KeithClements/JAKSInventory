# Legacy pre-rebuild app (ARCHIVED — do not develop here)

This folder holds the **original JAKS Inventory app** that predates the current
rebuild. It was frozen at the initial project commit (`1cba292`, 2026-05-27) and
has not been modified since. The **active app lives entirely under `rebuild/`**
and does not import anything from here.

Archived in-place on 2026-07-02 to declutter the repo root. Everything is still
tracked in git history — this is a move, not a deletion, so nothing was lost.

## What's in here
- **Code:** `base44/`, `core/`, `engine/`, `engine_rebuild/`, `jaks_inventory/`,
  `qbo/`, `sources/`, `ui/`, `utils/`, `workers/`, `db/`, `mockups/`, `phases/`,
  `models.py`
- **Old CLI / launchers:** `jaks_inventory_cli.py`, `jaks_inventory_cli.spec`,
  `run.bat`, `run.ps1` (these launched `python -m jaks_inventory`, the OLD app)
- **Early scratch/diagnostic scripts:** `_backfill_recv.py`, `_check_cols.py`,
  `_check_db2.py`, `_db_path.py`, `_diag_recv.py`, `_h1_smoke.py`,
  `_test_add_line.py`
- **Legacy deps:** `requirements_full.txt` (the active app uses
  `rebuild/requirements.txt`)

## If you need the old app
`git log --follow _legacy_pre_rebuild/jaks_inventory/` still shows its full
history. To run it you'd restore the root launchers, but there is no reason to —
`rebuild/` is the system of record.
