"""
app/services/backup_service.py
==============================
Automatic SQLite backup + restore (O3 — go-live gate, §11).

Why a service (not inline in a route)
-------------------------------------
The backup *engine* is pure, path-injectable, and DB-session-free so it can be
unit-tested against a throwaway temp database without touching the live
``data/jaks.db`` or the settings table.  The router (app/routers/backup.py) and
the startup hook (app/main.py) are thin callers.

Backup strategy
---------------
* **Create** uses the SQLite *online backup API* (``sqlite3.Connection.backup``)
  which produces a transactionally-consistent single-file snapshot even while
  the app holds the DB open — no need to stop the server.  We first checkpoint
  the WAL (best-effort) so the snapshot is tidy and self-contained (no
  ``-wal``/``-shm`` sidecars in the backup file).
* **Restore** copies a backup file over the live DB.  On Windows an open SQLite
  file cannot be overwritten, so the *caller* (route) must ``engine.dispose()``
  first; the service only does file ops.  Stale ``-wal``/``-shm`` sidecars of
  the live DB are removed so they can't shadow the restored bytes.  A
  pre-restore safety copy of the current DB is taken by default.

Settings consumed (seeded in routers/settings.py):
    backup_dir                 blank → <app-root>/backups
    backup_retention_count     keep N newest (default 10)
    backup_on_startup          "true"/"false"
    backup_min_interval_hours  schedule-lite throttle for startup backups
    backup_last_run            ISO timestamp, written after each success
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.database import DB_PATH
from app.settings_utils import get_setting_value

log = logging.getLogger(__name__)

# Backup filenames: jaks-YYYYMMDD_HHMMSS.db  (lexical sort == chronological sort)
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
BACKUP_PREFIX = "jaks-"
BACKUP_GLOB = f"{BACKUP_PREFIX}*.db"
DEFAULT_BACKUP_DIRNAME = "backups"
DEFAULT_RETENTION = 10


# ── Path resolution ───────────────────────────────────────────────────────────

def resolve_backup_dir(backup_dir: str | Path | None = None) -> Path:
    """Resolve (and create) the backup directory.

    Priority: explicit arg → ``backup_dir`` setting → ``<app-root>/backups``.
    ``DB_PATH`` is ``<app-root>/data/jaks.db`` so ``parent.parent`` is the app
    root (``rebuild/``); the default ``backups/`` there is already gitignored.
    """
    if backup_dir:
        target = Path(backup_dir)
    else:
        configured = (get_setting_value("backup_dir", "") or "").strip()
        target = Path(configured) if configured else (DB_PATH.parent.parent / DEFAULT_BACKUP_DIRNAME)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _sidecars(db_file: Path) -> tuple[Path, Path]:
    """The WAL and SHM sidecar paths for a SQLite db file."""
    return (
        db_file.with_name(db_file.name + "-wal"),
        db_file.with_name(db_file.name + "-shm"),
    )


# ── Create ────────────────────────────────────────────────────────────────────

def create_backup(
    db_path: str | Path | None = None,
    backup_dir: str | Path | None = None,
    *,
    ts: datetime | None = None,
) -> Path:
    """Create a consistent snapshot of the live DB and return the backup path.

    ``ts`` is injectable so tests can force distinct, deterministic filenames.
    """
    src = Path(db_path) if db_path else DB_PATH
    if not src.is_file():
        raise FileNotFoundError(f"database not found: {src}")

    dest_dir = resolve_backup_dir(backup_dir)
    stamp = (ts or datetime.now()).strftime(TIMESTAMP_FMT)
    dest = dest_dir / f"{BACKUP_PREFIX}{stamp}.db"

    src_conn = sqlite3.connect(str(src))
    try:
        # Best-effort WAL checkpoint so the snapshot is self-contained.
        try:
            src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:  # busy / not in WAL mode — snapshot is still valid
            log.debug("wal_checkpoint skipped: %s", exc)
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)  # online backup → transactionally consistent
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    log.info("DB backup created: %s (%d bytes)", dest, dest.stat().st_size)
    return dest


# ── List / prune ──────────────────────────────────────────────────────────────

def list_backups(backup_dir: str | Path | None = None) -> list[Path]:
    """Return backup files, newest first (filename timestamp sorts chronologically)."""
    return sorted(
        resolve_backup_dir(backup_dir).glob(BACKUP_GLOB),
        key=lambda p: p.name,
        reverse=True,
    )


def prune_backups(
    retention: int | None = None,
    backup_dir: str | Path | None = None,
) -> list[Path]:
    """Delete all but the ``retention`` newest backups. Returns the removed paths."""
    if retention is None:
        raw = get_setting_value("backup_retention_count", str(DEFAULT_RETENTION))
        try:
            retention = int(raw)
        except (TypeError, ValueError):
            retention = DEFAULT_RETENTION
    retention = max(1, retention)  # never prune down to zero

    removed: list[Path] = []
    for old in list_backups(backup_dir)[retention:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError as exc:
            log.warning("could not prune backup %s: %s", old, exc)
    if removed:
        log.info("pruned %d old backup(s)", len(removed))
    return removed


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_backup(
    backup_file: str | Path,
    db_path: str | Path | None = None,
    *,
    safety_copy: bool = True,
    ts: datetime | None = None,
) -> Path:
    """Restore ``backup_file`` over the live DB and return the live DB path.

    On Windows the live DB must NOT be open — the caller (route/script) is
    responsible for ``engine.dispose()`` beforehand.  The service only touches
    files: it takes a pre-restore safety copy, clears stale WAL/SHM sidecars,
    then copies the backup into place.
    """
    src = Path(backup_file)
    if not src.is_file():
        raise FileNotFoundError(f"backup not found: {src}")
    dest = Path(db_path) if db_path else DB_PATH

    # Safety: snapshot the current live DB before we overwrite it.
    if safety_copy and dest.is_file():
        stamp = (ts or datetime.now()).strftime(TIMESTAMP_FMT)
        pre = dest.with_name(f"{dest.stem}-prerestore-{stamp}{dest.suffix}")
        shutil.copy2(dest, pre)
        log.info("pre-restore safety copy: %s", pre)

    # Remove stale sidecars so they don't shadow the restored bytes.
    for sidecar in _sidecars(dest):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove sidecar %s: %s", sidecar, exc)

    shutil.copy2(src, dest)
    log.info("DB restored from %s → %s", src, dest)
    return dest
