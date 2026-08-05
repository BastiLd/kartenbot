"""Auftragswarteschlange.

Die Website legt hier einen Auftrag ab, der Bot holt ihn ab und führt ihn aus.
Das hat zwei Vorteile: der Bot kennt seine Rechte und die Rangordnung der
Rollen ohnehin, und lange Arbeiten (ein Verlaufs-Scan über Monate) blockieren
keine Web-Anfrage.

Der Bot schreibt Fortschritt und Ergebnis in dieselbe Zeile zurück, die
Oberfläche fragt sie regelmäßig ab.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import database, schema

# Auftragsarten, die der Bot kennt. Alles andere wird abgelehnt, damit über die
# Schnittstelle nichts Unerwartetes in die Warteschlange gelangt.
KINDS = {
    "role.apply":     "Rollen vergeben oder entziehen",
    "role.create":    "Rolle anlegen",
    "role.modify":    "Rolle bearbeiten",
    "member.modify":  "Mitglied bearbeiten (Spitzname, Timeout)",
    "member.kick":    "Mitglied entfernen",
    "member.ban":     "Mitglied sperren",
    "scan.history":   "Server-Verlauf auswerten",
    "guild.sync":     "Serverdaten neu einlesen",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(kind: str, guild_id: str | None, payload: dict, requested_by: str,
           total: int = 0) -> dict:
    if kind not in KINDS:
        raise ValueError(f"Unbekannte Auftragsart: {kind}")
    now = _now()
    with database.write_connection() as con:
        schema.init_schema(con)
        cursor = con.execute(
            "INSERT INTO web_jobs (created_at, updated_at, kind, guild_id, payload_json, "
            "status, total, requested_by) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (now, now, kind, str(guild_id) if guild_id else None,
             json.dumps(payload, ensure_ascii=False), int(total), requested_by),
        )
        job_id = cursor.lastrowid
    return get(job_id)


def get(job_id: int) -> dict | None:
    with database.read_connection() as con:
        row = database.fetch_one(con, "SELECT * FROM web_jobs WHERE id = ?", (int(job_id),))
    return _shape(row) if row else None


def recent(guild_id: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM web_jobs"
    params: tuple = ()
    if guild_id:
        sql += " WHERE guild_id = ?"
        params = (str(guild_id),)
    sql += " ORDER BY id DESC LIMIT ?"
    params = params + (int(limit),)
    with database.read_connection() as con:
        return [_shape(r) for r in database.fetch_all(con, sql, params)]


def active(guild_id: str | None = None) -> list[dict]:
    placeholders = ",".join("?" for _ in schema.JOB_FINAL_STATES)
    sql = f"SELECT * FROM web_jobs WHERE status NOT IN ({placeholders})"
    params: tuple = tuple(schema.JOB_FINAL_STATES)
    if guild_id:
        sql += " AND guild_id = ?"
        params = params + (str(guild_id),)
    sql += " ORDER BY id DESC"
    with database.read_connection() as con:
        return [_shape(r) for r in database.fetch_all(con, sql, params)]


def request_cancel(job_id: int) -> bool:
    placeholders = ",".join("?" for _ in schema.JOB_FINAL_STATES)
    with database.write_connection() as con:
        cursor = con.execute(
            f"UPDATE web_jobs SET cancel_requested = 1, updated_at = ? "
            f"WHERE id = ? AND status NOT IN ({placeholders})",
            (_now(), int(job_id), *schema.JOB_FINAL_STATES),
        )
        return cursor.rowcount > 0


def _shape(row: dict) -> dict:
    def _load(raw, fallback):
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback

    total = row.get("total") or 0
    done = row.get("progress") or 0
    return {
        "id": row["id"],
        "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]),
        "guild_id": row.get("guild_id"),
        "status": row.get("status"),
        "stage": row.get("stage"),
        "progress": done,
        "total": total,
        "percent": round(done * 100 / total) if total else None,
        "payload": _load(row.get("payload_json"), {}),
        "result": _load(row.get("result_json"), None),
        "error": row.get("error"),
        "requested_by": row.get("requested_by"),
        "cancel_requested": bool(row.get("cancel_requested")),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }
