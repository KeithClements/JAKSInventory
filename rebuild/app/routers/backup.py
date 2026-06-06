"""
app/routers/backup.py
=====================
Admin HTTP surface for the SQLite backup/restore service (O3 — go-live gate).

Kept deliberately separate from app/routers/admin.py (which the QA lane owns for
the smoke-test dashboard).  Both mount under ``/admin`` — paths don't collide
(``/admin/smoke-tests*`` vs ``/admin/backup*``).

Routes (thin wrappers over app/services/backup_service.py):
    GET  /admin/backup           → JSON status (config + existing backups)
    POST /admin/backup/run       → create a backup now, prune, stamp last-run
    POST /admin/backup/restore   → restore a named backup over the live DB

Responses are JSON so this stays entirely in the Backend lane (no admin
templates, which the QA/UI lanes own).  A friendlier page can be layered on top
later by the UI lane.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import app.database as _appdb
from app.deps import get_db, require_admin
from app.services import backup_service
from app.settings_utils import get_setting_value_db, set_setting_value_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backup", tags=["admin", "backup"])


def _status_payload(db: Session) -> dict:
    backups = backup_service.list_backups()
    return {
        "backup_dir": str(backup_service.resolve_backup_dir()),
        "retention": get_setting_value_db(db, "backup_retention_count", "10"),
        "on_startup": get_setting_value_db(db, "backup_on_startup", "true"),
        "min_interval_hours": get_setting_value_db(db, "backup_min_interval_hours", "12"),
        "last_run": get_setting_value_db(db, "backup_last_run", ""),
        "count": len(backups),
        "backups": [
            {"name": p.name, "size_bytes": p.stat().st_size}
            for p in backups
        ],
    }


@router.get("")
def backup_status(db: Session = Depends(get_db)):
    """Current backup configuration and the list of existing backups."""
    return JSONResponse(_status_payload(db))


@router.post("/run")
def backup_run(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Create a backup now, prune to retention, and record last-run."""
    try:
        path = backup_service.create_backup()
    except Exception as exc:  # surface the failure rather than 500 opaquely
        log.exception("manual backup failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    removed = backup_service.prune_backups()
    set_setting_value_db(db, "backup_last_run", datetime.now().isoformat(timespec="seconds"))
    db.commit()
    return JSONResponse({
        "ok": True,
        "created": path.name,
        "pruned": [p.name for p in removed],
    })


@router.post("/restore")
def backup_restore(
    filename: str = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Restore a named backup over the live DB.

    Security: ADMIN role required (``require_admin``) — restoring overwrites the
    live database, so a non-admin (e.g. the bookkeeping user) must never reach
    here. Only a bare filename inside the configured backup dir is accepted (no
    path traversal). The SQLAlchemy engine is disposed first so the live file is
    not locked (required on Windows), then the file is copied into place.
    """
    backup_dir = backup_service.resolve_backup_dir()
    # Reject any path component — only a plain filename in the backup dir is valid.
    candidate = (backup_dir / filename).resolve()
    if candidate.parent != backup_dir.resolve() or not candidate.is_file():
        return JSONResponse({"ok": False, "error": "backup not found"}, status_code=404)

    # Release the live DB handle so the restore copy can overwrite it (Windows lock).
    _appdb.engine.dispose()
    try:
        backup_service.restore_backup(candidate)
    except Exception as exc:
        log.exception("restore failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse({"ok": True, "restored": candidate.name})
