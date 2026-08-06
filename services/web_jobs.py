"""Auftragswarteschlange der Website — Bot-Seite.

Die Website legt Aufträge in der Tabelle ``web_jobs`` ab, der Bot holt sie hier
ab und meldet Fortschritt und Ergebnis zurück. So laufen Discord-Aktionen im
Bot-Prozess, der die Rechte, die Rangordnung der Rollen und die Wiederholung
bei Discord-Sperren ohnehin schon beherrscht.

Die Tabellen legt normalerweise die Website an. Damit der Bot auch dann
funktioniert, wenn die Website noch nie lief, werden sie hier notfalls selbst
erzeugt — dieselben Anweisungen wie in web/app/schema.py.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from db import db_context

FINAL_STATES = ("done", "failed", "cancelled")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS web_jobs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        started_at       TEXT,
        finished_at      TEXT,
        kind             TEXT NOT NULL,
        guild_id         TEXT,
        payload_json     TEXT NOT NULL DEFAULT '{}',
        status           TEXT NOT NULL DEFAULT 'pending',
        progress         INTEGER NOT NULL DEFAULT 0,
        total            INTEGER NOT NULL DEFAULT 0,
        stage            TEXT,
        result_json      TEXT,
        error            TEXT,
        requested_by     TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_web_jobs_status ON web_jobs(status, id)",
    """
    CREATE TABLE IF NOT EXISTS role_grants (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        guild_id   TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        role_id    TEXT NOT NULL,
        action     TEXT NOT NULL,
        actor      TEXT,
        job_id     INTEGER,
        expires_at TEXT,
        undone_at  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_role_grants_expiry ON role_grants(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS mod_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        guild_id   TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        kind       TEXT NOT NULL,
        actor_id   TEXT,
        reason     TEXT,
        until_at   TEXT,
        source     TEXT NOT NULL DEFAULT 'live'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mod_events_user ON mod_events(guild_id, user_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS scan_runs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id       TEXT NOT NULL,
        job_id         INTEGER,
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        status         TEXT NOT NULL DEFAULT 'running',
        range_key      TEXT,
        after_ts       TEXT,
        channels_json  TEXT,
        messages_seen  INTEGER NOT NULL DEFAULT 0,
        members_seen   INTEGER NOT NULL DEFAULT 0,
        summary_json   TEXT,
        error          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS member_profiles (
        guild_id    TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        scan_run_id INTEGER,
        updated_at  TEXT NOT NULL,
        stats_json  TEXT NOT NULL DEFAULT '{}',
        tags_json   TEXT NOT NULL DEFAULT '[]',
        ai_summary  TEXT,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_testruns (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        karten_name        TEXT NOT NULL,
        job_id             INTEGER,
        started_at         TEXT NOT NULL,
        finished_at        TEXT,
        status             TEXT NOT NULL DEFAULT 'running',
        spielweise         TEXT NOT NULL DEFAULT 'optimal',
        kaempfe_je_paarung INTEGER NOT NULL DEFAULT 0,
        seed               INTEGER,
        kaempfe_gesamt     INTEGER NOT NULL DEFAULT 0,
        siege              INTEGER NOT NULL DEFAULT 0,
        niederlagen        INTEGER NOT NULL DEFAULT 0,
        unentschieden      INTEGER NOT NULL DEFAULT 0,
        siegquote          REAL,
        runden_schnitt     REAL,
        ergebnis_json      TEXT,
        error              TEXT,
        ki_text            TEXT,
        ki_modell          TEXT,
        ki_am              TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_card_testruns_karte ON card_testruns(karten_name, id)",
)

_schema_ready = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Spalten, die spaeter zu einer bereits bestehenden Tabelle dazugekommen sind.
# CREATE TABLE IF NOT EXISTS legt nichts an, wenn die Tabelle schon steht -
# muss zur Liste in web/app/schema.py passen.
_NACHRUESTEN = {
    "card_testruns": (("ki_text", "TEXT"), ("ki_modell", "TEXT"), ("ki_am", "TEXT")),
}


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with db_context() as db:
        for statement in _SCHEMA:
            await db.execute(statement)
        for tabelle, spalten in _NACHRUESTEN.items():
            cursor = await db.execute(f"PRAGMA table_info({tabelle})")
            vorhanden = {zeile[1] for zeile in await cursor.fetchall()}
            for name, typ in spalten:
                if vorhanden and name not in vorhanden:
                    await db.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {typ}")
        await db.commit()
    _schema_ready = True


async def claim_next() -> dict | None:
    """Holt den ältesten offenen Auftrag und markiert ihn als laufend.

    Das Markieren passiert mit ``WHERE status = 'pending'``, damit ein Auftrag
    auch dann nur einmal genommen wird, wenn zwei Bot-Prozesse laufen.
    """
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT * FROM web_jobs WHERE status = 'pending' ORDER BY id LIMIT 1")
        row = await cursor.fetchone()
        if not row:
            return None
        job = _as_dict(row, cursor)
        cursor = await db.execute(
            "UPDATE web_jobs SET status = 'running', started_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now(), _now(), job["id"]))
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return job


async def reset_orphaned() -> int:
    """Räumt Aufträge auf, die ein Neustart mitten in der Arbeit erwischt hat.

    Aufträge werden ausschließlich vom Bot bearbeitet. Steht beim Start also
    etwas auf "läuft", kann das nur ein abgebrochener Rest von vorher sein —
    es gibt niemanden, der daran noch arbeitet.

    Ohne dieses Aufräumen bliebe so ein Auftrag für immer auf "läuft" stehen.
    Die Website liesse dann für diesen Server nie wieder einen neuen Scan zu,
    weil sie denkt, es liefe schon einer.
    """
    await ensure_schema()
    hinweis = ("Der Bot wurde neu gestartet, während dieser Auftrag lief. "
               "Er wurde deshalb abgebrochen — du kannst ihn einfach neu starten.")
    async with db_context() as db:
        cursor = await db.execute(
            "UPDATE web_jobs SET status = 'failed', error = ?, finished_at = ?, updated_at = ? "
            "WHERE status = 'running'", (hinweis, _now(), _now()))
        anzahl = cursor.rowcount or 0
        # Angefangene Analyse-Läufe genauso schliessen, sonst zeigt die
        # Oberfläche dauerhaft einen Lauf an, der nie zu Ende geht.
        await db.execute(
            "UPDATE scan_runs SET status = 'failed', finished_at = ?, error = ? "
            "WHERE status = 'running'", (_now(), hinweis))
        # Dasselbe fuer Testlaeufe - sie dauern Minuten und werden von einem
        # Neustart entsprechend haeufig mittendrin erwischt.
        await db.execute(
            "UPDATE card_testruns SET status = 'failed', finished_at = ?, error = ? "
            "WHERE status = 'running'", (_now(), hinweis))
        await db.commit()
    if anzahl:
        logging.info("%s haengengebliebene Web-Auftraege nach Neustart aufgeraeumt", anzahl)
    return anzahl


async def update_progress(job_id: int, progress: int, total: int | None = None,
                          stage: str | None = None) -> None:
    async with db_context() as db:
        if total is None:
            await db.execute(
                "UPDATE web_jobs SET progress = ?, stage = COALESCE(?, stage), updated_at = ? "
                "WHERE id = ?", (int(progress), stage, _now(), int(job_id)))
        else:
            await db.execute(
                "UPDATE web_jobs SET progress = ?, total = ?, stage = COALESCE(?, stage), "
                "updated_at = ? WHERE id = ?",
                (int(progress), int(total), stage, _now(), int(job_id)))
        await db.commit()


async def is_cancelled(job_id: int) -> bool:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT cancel_requested FROM web_jobs WHERE id = ?", (int(job_id),))
        row = await cursor.fetchone()
    return bool(row and row[0])


async def finish(job_id: int, status: str, result: dict | None = None,
                 error: str | None = None) -> None:
    if status not in FINAL_STATES:
        raise ValueError(f"Ungültiger Endzustand: {status}")
    async with db_context() as db:
        await db.execute(
            "UPDATE web_jobs SET status = ?, result_json = ?, error = ?, finished_at = ?, "
            "updated_at = ? WHERE id = ?",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, _now(), _now(), int(job_id)))
        await db.commit()


async def record_role_grants(guild_id, action: str, changes: list[dict], actor: str | None,
                             job_id: int | None, expires_at: str | None) -> None:
    if not changes:
        return
    now = _now()
    async with db_context() as db:
        await db.executemany(
            "INSERT INTO role_grants (created_at, guild_id, user_id, role_id, action, actor, "
            "job_id, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(now, str(guild_id), str(c["user_id"]), str(c["role_id"]), action, actor,
              job_id, expires_at) for c in changes])
        await db.commit()


async def due_expirations(limit: int = 200) -> list[dict]:
    """Rollen auf Zeit, deren Frist abgelaufen ist."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT id, guild_id, user_id, role_id FROM role_grants "
            "WHERE action = 'add' AND expires_at IS NOT NULL AND expires_at <= ? "
            "AND undone_at IS NULL ORDER BY expires_at LIMIT ?",
            (_now(), int(limit)))
        rows = await cursor.fetchall()
        return [{"id": r[0], "guild_id": r[1], "user_id": r[2], "role_id": r[3]} for r in rows]


async def mark_undone(grant_id: int) -> None:
    async with db_context() as db:
        await db.execute("UPDATE role_grants SET undone_at = ? WHERE id = ?",
                         (_now(), int(grant_id)))
        await db.commit()


async def log_mod_event(guild_id, user_id, kind: str, *, actor_id=None, reason: str | None = None,
                        until_at: str | None = None, source: str = "live") -> None:
    """Moderationsereignis mitschreiben.

    Discord selbst hebt sein Prüfprotokoll nur rund 45 Tage auf. Alles, was der
    Bot ab jetzt hier festhält, bleibt dauerhaft nachvollziehbar.
    """
    try:
        await ensure_schema()
        async with db_context() as db:
            await db.execute(
                "INSERT INTO mod_events (created_at, guild_id, user_id, kind, actor_id, reason, "
                "until_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), str(guild_id), str(user_id), kind,
                 str(actor_id) if actor_id else None, reason, until_at, source))
            await db.commit()
    except Exception:
        logging.exception("Moderationsereignis konnte nicht gespeichert werden")


async def start_scan_run(guild_id, job_id: int, range_key: str, after_ts: int | None,
                         channel_ids: list[str]) -> int:
    async with db_context() as db:
        cursor = await db.execute(
            "INSERT INTO scan_runs (guild_id, job_id, started_at, status, range_key, after_ts, "
            "channels_json) VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (str(guild_id), int(job_id), _now(), range_key,
             str(after_ts) if after_ts else None,
             json.dumps([str(c) for c in channel_ids])))
        await db.commit()
        return cursor.lastrowid


async def finish_scan_run(run_id: int, status: str, *, messages: int = 0, members: int = 0,
                          summary: dict | None = None, error: str | None = None) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE scan_runs SET status = ?, finished_at = ?, messages_seen = ?, "
            "members_seen = ?, summary_json = ?, error = ? WHERE id = ?",
            (status, _now(), int(messages), int(members),
             json.dumps(summary, ensure_ascii=False) if summary else None, error, int(run_id)))
        await db.commit()


async def save_member_profiles(guild_id, run_id: int, profiles: list[dict]) -> None:
    """Speichert die Auswertung pro Mitglied.

    Hier landen ausschließlich Kennzahlen und Einordnungen — niemals der
    Inhalt von Nachrichten.
    """
    if not profiles:
        return
    now = _now()
    async with db_context() as db:
        await db.executemany(
            "INSERT INTO member_profiles (guild_id, user_id, scan_run_id, updated_at, "
            "stats_json, tags_json, ai_summary) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET scan_run_id = excluded.scan_run_id, "
            "updated_at = excluded.updated_at, stats_json = excluded.stats_json, "
            "tags_json = excluded.tags_json, "
            "ai_summary = COALESCE(excluded.ai_summary, member_profiles.ai_summary)",
            [(str(guild_id), str(p["user_id"]), int(run_id), now,
              json.dumps(p.get("stats", {}), ensure_ascii=False),
              json.dumps(p.get("tags", []), ensure_ascii=False),
              p.get("ai_summary")) for p in profiles])
        await db.commit()


async def start_card_testrun(karten_name: str, job_id: int, spielweise: str,
                             kaempfe_je_paarung: int) -> int:
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "INSERT INTO card_testruns (karten_name, job_id, started_at, status, "
            "spielweise, kaempfe_je_paarung) VALUES (?, ?, ?, 'running', ?, ?)",
            (str(karten_name), int(job_id), _now(), str(spielweise),
             int(kaempfe_je_paarung)))
        await db.commit()
        return cursor.lastrowid


async def finish_card_testrun(run_id: int, status: str, *, ergebnis: dict | None = None,
                              error: str | None = None) -> None:
    """Ergebnis eines Testlaufs festhalten.

    Die Kennzahlen stehen doppelt: einmal als eigene Spalten, damit sich eine
    Liste ohne Auspacken sortieren laesst, und einmal vollstaendig in
    ``ergebnis_json`` — dort stecken auch die einzelnen Paarungen.
    """
    daten = ergebnis or {}
    async with db_context() as db:
        await db.execute(
            "UPDATE card_testruns SET status = ?, finished_at = ?, seed = ?, "
            "kaempfe_gesamt = ?, siege = ?, niederlagen = ?, unentschieden = ?, "
            "siegquote = ?, runden_schnitt = ?, ergebnis_json = ?, error = ? WHERE id = ?",
            (status, _now(), daten.get("seed"), int(daten.get("kaempfe") or 0),
             int(daten.get("siege") or 0), int(daten.get("niederlagen") or 0),
             int(daten.get("unentschieden") or 0), daten.get("siegquote"),
             daten.get("runden_schnitt"),
             json.dumps(daten, ensure_ascii=False) if ergebnis is not None else None,
             error, int(run_id)))
        await db.commit()


def _as_dict(row, cursor) -> dict:
    columns = [c[0] for c in cursor.description]
    data = dict(zip(columns, row))
    try:
        data["payload"] = json.loads(data.get("payload_json") or "{}")
    except (TypeError, ValueError):
        data["payload"] = {}
    return data


def unix_now() -> int:
    return int(time.time())
