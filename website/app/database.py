"""Datenbank-Zugriff des Dashboards.

Lesen: eigene read-only-Verbindung (URI mode=ro) — kann dem Bot nichts
kaputt machen, WAL erlaubt gleichzeitiges Lesen.
Schreiben: separate Verbindung mit busy_timeout, nur für die Admin-Aktionen
in actions.py. Alle Queries sind parametrisiert.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config


class DashboardDBError(Exception):
    """Fehler beim DB-Zugriff, wird als saubere API-Fehlermeldung gerendert."""


def _connect(readonly: bool) -> sqlite3.Connection:
    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        raise DashboardDBError(f"Datenbank nicht gefunden: {db_path}")
    if readonly:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


_write_lock = threading.Lock()


@contextmanager
def read_connection():
    try:
        con = _connect(readonly=True)
    except sqlite3.Error as exc:
        raise DashboardDBError(f"DB-Verbindung fehlgeschlagen: {exc}") from exc
    try:
        yield con
    finally:
        con.close()


@contextmanager
def write_connection():
    """Schreibende Verbindung; serialisiert über ein Lock, damit sich
    Dashboard-Aktionen nicht gegenseitig blockieren."""
    with _write_lock:
        try:
            con = _connect(readonly=False)
        except sqlite3.Error as exc:
            raise DashboardDBError(f"DB-Verbindung fehlgeschlagen: {exc}") from exc
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def fetch_all(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        # Robust gegen fehlende Tabellen/Spalten in älteren DB-Ständen.
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg:
            return []
        raise


def fetch_one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    rows = fetch_all(con, sql, params)
    return rows[0] if rows else None


def scalar(con: sqlite3.Connection, sql: str, params: tuple = (), default=0):
    row = fetch_one(con, sql, params)
    if not row:
        return default
    value = next(iter(row.values()), default)
    return default if value is None else value
