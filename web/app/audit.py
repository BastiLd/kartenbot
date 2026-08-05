"""Protokoll aller Aktionen, die über die Website ausgelöst wurden.

Jede schreibende Aktion landet hier — auch die fehlgeschlagenen. Das ist die
Antwort auf die Frage „wer hat wann was gemacht?" und die Grundlage für die
Rückgängig-Funktion.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import database, schema


def record(*, actor: str, action: str, guild_id: str | None = None,
           target: str | None = None, detail: str | None = None,
           client_ip: str | None = None, ok: bool = True) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with database.write_connection() as con:
            schema.init_schema(con)
            con.execute(
                "INSERT INTO web_audit (created_at, actor, action, guild_id, target, detail, "
                "client_ip, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, actor, action, str(guild_id) if guild_id else None,
                 str(target) if target else None, detail, client_ip, 1 if ok else 0),
            )
    except database.WebDBError:
        # Ein kaputtes Protokoll darf die eigentliche Aktion nicht verhindern.
        pass


def recent(limit: int = 100, guild_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM web_audit"
    params: tuple = ()
    if guild_id:
        sql += " WHERE guild_id = ?"
        params = (str(guild_id),)
    sql += " ORDER BY id DESC LIMIT ?"
    with database.read_connection() as con:
        return database.fetch_all(con, sql, params + (int(limit),))
