"""
tests/test_backup_restore.py
============================
Backend unit tests for the O3 SQLite backup/restore service.

These exercise app/services/backup_service.py directly against throwaway temp
databases (explicit path injection) — they never touch the live data/jaks.db or
the settings table, so they're isolated and order-independent.  The QA lane adds
the acceptance-level "restore actually brings the app back" test on top of these
to close the §11 go-live gate.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from app.services import backup_service as bk


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db(path, value: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _set_value(path, value: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM t")
        conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_values(path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT v FROM t").fetchall()]
    finally:
        conn.close()


# ── create ────────────────────────────────────────────────────────────────────

def test_create_backup_makes_a_file(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "original")

    dest = bk.create_backup(db_path=db, backup_dir=backups)

    assert dest.is_file()
    assert dest.parent == backups
    assert dest.name.startswith("jaks-") and dest.suffix == ".db"
    # The snapshot contains the same data.
    assert _read_values(dest) == ["original"]


def test_create_backup_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.create_backup(db_path=tmp_path / "nope.db", backup_dir=tmp_path / "b")


# ── restore ───────────────────────────────────────────────────────────────────

def test_restore_roundtrips_data(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "v1")

    snapshot = bk.create_backup(db_path=db, backup_dir=backups)

    # Mutate the live DB after the snapshot.
    _set_value(db, "v2-corrupted")
    assert _read_values(db) == ["v2-corrupted"]

    # Restoring brings v1 back...
    bk.restore_backup(snapshot, db_path=db)
    assert _read_values(db) == ["v1"]

    # ...and a pre-restore safety copy of the v2 state was written next to the DB.
    pre = list(tmp_path.glob("jaks-prerestore-*.db"))
    assert len(pre) == 1
    assert _read_values(pre[0]) == ["v2-corrupted"]


def test_restore_missing_backup_raises(tmp_path):
    db = tmp_path / "jaks.db"
    _make_db(db, "x")
    with pytest.raises(FileNotFoundError):
        bk.restore_backup(tmp_path / "ghost.db", db_path=db)


def test_restore_clears_stale_wal_sidecar(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "clean")
    snapshot = bk.create_backup(db_path=db, backup_dir=backups)

    # Simulate a stale WAL sidecar that must not shadow the restored file.
    stale = db.with_name(db.name + "-wal")
    stale.write_bytes(b"garbage")
    assert stale.is_file()

    bk.restore_backup(snapshot, db_path=db, safety_copy=False)
    assert not stale.is_file()
    assert _read_values(db) == ["clean"]


# ── prune / list ──────────────────────────────────────────────────────────────

def test_prune_keeps_n_newest(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "data")

    # Four backups with distinct, increasing timestamps.
    for day in range(1, 5):
        bk.create_backup(db_path=db, backup_dir=backups, ts=datetime(2026, 1, day, 12, 0, 0))

    assert len(bk.list_backups(backup_dir=backups)) == 4

    removed = bk.prune_backups(retention=2, backup_dir=backups)
    remaining = bk.list_backups(backup_dir=backups)

    assert len(removed) == 2
    assert len(remaining) == 2
    # Newest two survive (Jan 4 and Jan 3); oldest two removed.
    assert [p.name for p in remaining] == [
        "jaks-20260104_120000.db",
        "jaks-20260103_120000.db",
    ]


def test_prune_retention_never_zero(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "data")
    for day in range(1, 4):
        bk.create_backup(db_path=db, backup_dir=backups, ts=datetime(2026, 2, day, 9, 0, 0))

    # Retention clamps to >= 1 so a misconfigured 0 can't wipe every backup.
    bk.prune_backups(retention=0, backup_dir=backups)
    assert len(bk.list_backups(backup_dir=backups)) == 1


def test_list_backups_newest_first(tmp_path):
    db = tmp_path / "jaks.db"
    backups = tmp_path / "backups"
    _make_db(db, "data")
    for day in (2, 1, 3):  # created out of order
        bk.create_backup(db_path=db, backup_dir=backups, ts=datetime(2026, 3, day, 8, 0, 0))

    names = [p.name for p in bk.list_backups(backup_dir=backups)]
    assert names == [
        "jaks-20260303_080000.db",
        "jaks-20260302_080000.db",
        "jaks-20260301_080000.db",
    ]
