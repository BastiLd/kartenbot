"""Zusätzliche Tabellen für Kartenbot Web.

Es wird nur angelegt, was noch nicht da ist — bestehende Tabellen des Bots
werden nicht angefasst. Gleiche Vorgehensweise wie services/db.py im Bot:
CREATE TABLE IF NOT EXISTS, damit ein Neustart nie etwas kaputt macht.

Die Tabellen sind bewusst auch für den Bot lesbar gehalten (gleiche DB-Datei),
denn der Bot arbeitet die Aufträge aus web_jobs ab.
"""
from __future__ import annotations

import sqlite3

# Ein Auftrag ist erst fertig, wenn er einen dieser Zustände hat.
JOB_FINAL_STATES = ("done", "failed", "cancelled")

_TABLES = (
    # --- Einstellungen, die du in der Oberfläche änderst ---
    """
    CREATE TABLE IF NOT EXISTS web_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # --- Zugang: wer ist Besitzer der Seite ---
    """
    CREATE TABLE IF NOT EXISTS web_auth (
        id                INTEGER PRIMARY KEY CHECK (id = 1),
        owner_discord_id  TEXT,
        owner_name        TEXT,
        claimed_at        TEXT
    )
    """,
    # --- Auftragswarteschlange: Website legt ab, Bot arbeitet ab ---
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
    "CREATE INDEX IF NOT EXISTS idx_web_jobs_guild ON web_jobs(guild_id, created_at)",
    # --- Protokoll jeder Aktion, die über die Website ausgelöst wurde ---
    """
    CREATE TABLE IF NOT EXISTS web_audit (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        actor      TEXT,
        action     TEXT NOT NULL,
        guild_id   TEXT,
        target     TEXT,
        detail     TEXT,
        client_ip  TEXT,
        ok         INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_web_audit_created ON web_audit(created_at)",
    # --- Generische An/Aus-Schalter pro Server (Vorbild: guild_message_visibility) ---
    """
    CREATE TABLE IF NOT EXISTS guild_feature_toggles (
        guild_id    TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        enabled     INTEGER NOT NULL DEFAULT 1,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (guild_id, feature_key)
    )
    """,
    # --- Verlaufs-Analyse: ein Lauf ---
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
    "CREATE INDEX IF NOT EXISTS idx_scan_runs_guild ON scan_runs(guild_id, started_at)",
    # --- Verlaufs-Analyse: Ergebnis pro Mitglied.
    #     WICHTIG: hier stehen ausschließlich Auswertungen, niemals Nachrichtentexte. ---
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
    # --- Moderationsereignisse, ab jetzt fortlaufend mitgeschrieben ---
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
    # --- Rollen-Verlauf: für Nachvollziehbarkeit, Rückgängig und Rollen auf Zeit ---
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
    "CREATE INDEX IF NOT EXISTS idx_role_grants_guild ON role_grants(guild_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_role_grants_expiry ON role_grants(expires_at)",
    # --- Testlauf: eine Karte gegen alle anderen.
    #     Gerechnet wird im Bot, hier steht nur das Ergebnis. Die Kennzahlen
    #     stehen als eigene Spalten, die Paarungen in ergebnis_json. ---
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
        error              TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_card_testruns_karte ON card_testruns(karten_name, id)",
    # --- Zwischenspeicher für Namen von Servern, Kanälen, Rollen, Mitgliedern ---
    """
    CREATE TABLE IF NOT EXISTS web_discord_cache (
        kind       TEXT NOT NULL,
        guild_id   TEXT NOT NULL DEFAULT '',
        object_id  TEXT NOT NULL,
        data_json  TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, guild_id, object_id)
    )
    """,
)


def init_schema(con: sqlite3.Connection) -> None:
    for statement in _TABLES:
        con.execute(statement)
    con.execute("INSERT OR IGNORE INTO web_auth (id) VALUES (1)")
