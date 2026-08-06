"""Datenbank-Zugriff von Kartenbot Web.

Lesen läuft über eine eigene read-only-Verbindung — dem Bot kann dabei nichts
passieren, WAL erlaubt gleichzeitiges Lesen. Geschrieben wird über eine zweite
Verbindung, serialisiert über ein Lock. Alle Abfragen sind parametrisiert.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config


class WebDBError(Exception):
    """DB-Fehler, wird als saubere Fehlermeldung an die Oberfläche gereicht."""


def _connect(readonly: bool) -> sqlite3.Connection:
    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        raise WebDBError(f"Datenbank nicht gefunden: {db_path}")
    if readonly:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    else:
        con = sqlite3.connect(str(db_path), timeout=10.0)
        con.execute("PRAGMA busy_timeout = 10000")
        con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


_write_lock = threading.Lock()


@contextmanager
def read_connection():
    try:
        con = _connect(readonly=True)
    except sqlite3.Error as exc:
        raise WebDBError(f"DB-Verbindung fehlgeschlagen: {exc}") from exc
    try:
        yield con
    finally:
        con.close()


@contextmanager
def write_connection():
    with _write_lock:
        try:
            con = _connect(readonly=False)
        except sqlite3.Error as exc:
            raise WebDBError(f"DB-Verbindung fehlgeschlagen: {exc}") from exc
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


# Spalten, deren Inhalt eine Discord-ID ist. Sie muessen als Text zum Browser,
# niemals als Zahl - siehe _ids_als_text().
_ID_SPALTEN = ("user_id", "guild_id", "channel_id", "thread_id", "role_id",
               "actor_id", "target_id", "actor_user_id", "target_user_id",
               "message_id", "inviter_id", "invitee_id", "session_id")


def _ids_als_text(zeile: dict) -> dict:
    """Wandelt Discord-IDs in Text um, bevor sie den Browser erreichen.

    Der Bot legt IDs als Zahl ab. JavaScript rechnet aber nur mit 53 Bit genau,
    und eine Discord-ID braucht mehr. Aus 965593518745731152 wird im Browser
    stillschweigend 965593518745731200 - eine falsche ID, mit der weder ein
    Name gefunden noch eine Aktion ausgefuehrt werden kann.

    Deshalb hier zentral: was nach einer ID aussieht, geht als Text raus.
    """
    for spalte in _ID_SPALTEN:
        wert = zeile.get(spalte)
        if isinstance(wert, int):
            zeile[spalte] = str(wert)
    return zeile


def fetch_all(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [_ids_als_text(dict(r)) for r in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
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
