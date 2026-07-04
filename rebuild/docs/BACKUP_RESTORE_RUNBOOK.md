# Backup & Restore Runbook

*Owner-facing. If the database is ever lost or broken, this page gets it back.*
*Last restore drill: **2026-07-04, PASSED** (see the drill record at the bottom).*

---

## How backups work

| What | Where | When | Kept |
|---|---|---|---|
| Routine backup `jaks-YYYYMMDD_HHMMSS.db` | `rebuild/backups/` | On app startup (at most once per `backup_min_interval_hours`, default 12h) and on **Settings → System → Back Up Now** | Newest 10 (`backup_retention_count`) |
| Offsite copy of the newest backup **+ encryption keyfile** | `backup_offsite_dir` (default `%OneDrive%\JAKS Backups`) | After every successful backup run; also nightly via `scripts/offsite_backup.bat` if registered in Task Scheduler | Newest 10 |
| Pre-migration snapshot `jaks-premigration-*.db` | `rebuild/backups/` | Automatically before any schema migration | Forever (never auto-pruned; delete by hand when huge) |
| Pre-restore safety copy `jaks-prerestore-*.db` | `rebuild/data/` | Automatically every time a restore runs | Forever (manual cleanup) |

**Two separate retention pools (the C1 fix, 2026-07-04).** Routine dated backups
and `jaks-pre*` snapshots share a folder but are matched by exact pattern and
pruned independently. Before this fix, ten `jaks-pre*` files sorted above every
dated backup and permanently filled the retention quota — every fresh backup was
deleted seconds after it was written, while `backup_last_run` still reported
success. Regression tests: `tests/test_backup_restore.py`
(`test_fresh_backup_survives_prune_with_ten_presnapshots` and friends).

**The keyfile matters.** `%USERPROFILE%\.jaks_fernet.key` decrypts the secrets
stored in the database (QuickBooks tokens, API keys). A backup restored onto a
new machine **without this file** runs fine but every stored secret is
unrecoverable — you would have to reconnect QuickBooks/Shopify/Anthropic. That
is why the offsite copy always ships the keyfile next to the newest backup.

## Offsite copies

Two lanes, both landing in the same cloud-synced folder:

1. **In-app (automatic):** after every backup run the newest backup + keyfile are
   copied to **Settings → System → "Offsite backup folder"**. Default is
   `%OneDrive%\JAKS Backups`; env vars expand at run time; blank disables; a
   value that doesn't expand on this machine safely disables it too.
2. **Task Scheduler (optional, for machines that stay up for days):**

   ```bat
   schtasks /Create /SC DAILY /ST 21:30 /TN "JAKS Offsite Backup" ^
     /TR "\"C:\Users\keith\JAKSInventory\rebuild\scripts\offsite_backup.bat\""
   ```

   The script copies the newest dated backup + keyfile via robocopy. Pass a
   folder argument to override the OneDrive default.

## Restoring — the easy way (app is running)

1. **Settings → System → Backups & Restore.**
2. Pick the backup to go back to — routine dated backups on top; the
   "Pre-migration & script snapshots" list underneath also works as restore
   points. **Everything entered after that backup's timestamp will be gone.**
3. Click **Restore** and confirm. A safety copy of the current database is
   written to `rebuild/data/` first, so a wrong pick is itself reversible.
4. **Close and restart JAKS** (`START JAKS.bat`).
5. Spot-check: open Customers and Invoices, confirm recent records match the
   backup date you chose.

Restore is admin-only (the bookkeeper login gets a 403 by design).

## Restoring — the hard way (app won't start / new machine)

1. Stop the app (close the JAKS window; check Task Manager for stray
   `python.exe` on port 8000).
2. In `rebuild/data/`, rename the broken `jaks.db` to `jaks-broken-<date>.db`
   (keep it — never delete evidence). Delete `jaks.db-wal` / `jaks.db-shm` if
   present.
3. Copy the chosen backup from `rebuild/backups/` — or from the offsite folder
   if the machine is lost — into `rebuild/data/` and rename it `jaks.db`.
4. New machine only: also copy `.jaks_fernet.key` from the offsite folder to
   `C:\Users\<user>\.jaks_fernet.key`.
5. Start JAKS. Migrations re-apply automatically if the backup predates a
   schema change (a premigration snapshot of the backup is taken first).

## Restore drill — run one every few months

Never trust a backup that has never been restored. The scripted drill stages a
**copy** of a real snapshot in a temp folder, backs it up, wipes the customers
table, restores, and verifies row counts + `PRAGMA integrity_check` — the live
database is never touched. Either re-run the scripted drill or do a manual one
(restore yesterday's backup on a spare copy and open it with the app).

### Drill record

| Date | Method | Source data | Result |
|---|---|---|---|
| 2026-07-04 | Scripted (create → wipe customers → restore → verify) | Copy of `jaks-premigration-20260702-193825.db` (real data: 43 customers, 30,935 products) | **PASSED** — counts restored exactly, `integrity_check` ok on backup and restored DB, pre-restore safety copy written, offsite copy + keyfile verified |
