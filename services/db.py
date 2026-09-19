import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

from karten import karten
from services.card_variants import default_variant_name_for_base, iter_card_variants

DB_PATH = os.getenv("KARTENBOT_DB_PATH", "kartenbot.db")

# Alternative Karten-Designs. karten_name ist immer der GRUNDname der Karte
# (bei Iron-Man teilen sich beide Varianten einen Eintrag); Design 1 ist immer
# frei und steht deshalb nie in user_designs.
DESIGN_TABLES_SQL = (
    """
    CREATE TABLE IF NOT EXISTS user_designs (
        user_id           INTEGER NOT NULL,
        karten_name       TEXT    NOT NULL,
        design            INTEGER NOT NULL,
        quelle            TEXT    NOT NULL DEFAULT '',
        freigeschaltet_am TEXT    NOT NULL,
        PRIMARY KEY (user_id, karten_name, design)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_design_wahl (
        user_id     INTEGER NOT NULL,
        karten_name TEXT    NOT NULL,
        design      INTEGER NOT NULL,
        PRIMARY KEY (user_id, karten_name)
    )
    """,
)

# Level- und Einladungs-Belohnungen (v2.5.0). Logik in services/level_rewards.py.
# - level_rollen: welche Discord-Rolle welches MEE6-Level bedeutet (je Server).
# - belohnungs_protokoll: jede Vergabe genau einmal. Der Schlüssel beschreibt
#   die Belohnung (z. B. 'level:5:design:Black Widow:2'); steht er drin, wird
#   nie wieder vergeben — außer der Status ist 'entzogen'.
# - level_rueckfragen: Rolle verloren -> Owner entscheidet Behalten/Entziehen.
LEVEL_TABLES_SQL = (
    """
    CREATE TABLE IF NOT EXISTS level_rollen (
        guild_id INTEGER NOT NULL,
        level    INTEGER NOT NULL,
        role_id  INTEGER NOT NULL,
        PRIMARY KEY (guild_id, level)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS belohnungs_protokoll (
        user_id      INTEGER NOT NULL,
        schluessel   TEXT    NOT NULL,
        art          TEXT    NOT NULL,
        quelle       TEXT    NOT NULL,
        details_json TEXT    NOT NULL DEFAULT '',
        status       TEXT    NOT NULL DEFAULT 'vergeben',
        am           TEXT    NOT NULL,
        PRIMARY KEY (user_id, schluessel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS level_rueckfragen (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id     INTEGER NOT NULL,
        user_id      INTEGER NOT NULL,
        von_stufe    INTEGER NOT NULL,
        auf_stufe    INTEGER NOT NULL,
        grund        TEXT    NOT NULL DEFAULT '',
        status       TEXT    NOT NULL DEFAULT 'offen',
        nachricht_id INTEGER,
        erstellt_am  TEXT    NOT NULL
    )
    """,
)

_db = None


async def connect_db():
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA foreign_keys = ON")
        # WAL + synchronous=NORMAL: schützt vor DB-Korruption/Datenverlust, wenn der
        # Bot mitten in einem Schreibvorgang abstürzt, und erlaubt gleichzeitiges Lesen.
        await _db.execute("PRAGMA journal_mode = WAL")
        await _db.execute("PRAGMA synchronous = NORMAL")
    return _db


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


@asynccontextmanager
async def db_context():
    db = await connect_db()
    try:
        yield db
    except Exception as exc:
        if isinstance(exc, aiosqlite.OperationalError) and "no such table: active_sessions" in str(exc).lower():
            logging.debug("DB table active_sessions is not available yet")
        else:
            logging.exception("DB operation failed")
        raise


async def _table_exists(db, table_name: str) -> bool:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    row = await cursor.fetchone()
    return row is not None


async def _column_exists(db, table_name: str, column_name: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return any(row[1] == column_name for row in rows)


async def _ensure_column(db, table: str, column: str, definition: str) -> None:
    if not await _column_exists(db, table, column):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def _migrate_default_variant_card_names(db) -> None:
    for card in karten:
        base_name = str(card.get("name") or "").strip()
        variants = iter_card_variants(card)
        if len(variants) <= 1:
            continue
        default_variant_name = default_variant_name_for_base(base_name, cards=karten)
        if not default_variant_name or default_variant_name == base_name:
            continue
        cursor = await db.execute(
            "SELECT user_id, anzahl FROM user_karten WHERE karten_name = ?",
            (base_name,),
        )
        rows = await cursor.fetchall()
        for user_id, amount in rows:
            await db.execute(
                "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + excluded.anzahl",
                (user_id, default_variant_name, int(amount or 0)),
            )
        await db.execute("DELETE FROM user_karten WHERE karten_name = ?", (base_name,))


async def init_db():
    db = await connect_db()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_karten (
            user_id INTEGER,
            karten_name TEXT,
            anzahl INTEGER,
            PRIMARY KEY (user_id, karten_name)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_teams (
            user_id INTEGER PRIMARY KEY,
            team TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily (
            user_id INTEGER PRIMARY KEY,
            last_daily INTEGER,
            challenges TEXT,
            last_vote INTEGER,
            mission_count INTEGER DEFAULT 0,
            last_mission_reset INTEGER,
            used_invite INTEGER DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS tradingpost (
            code TEXT PRIMARY KEY,
            seller_id INTEGER,
            card_name TEXT,
            preis INTEGER,
            timestamp INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            mission_channel_id INTEGER,
            ignored_channels TEXT,
            maintenance_mode INTEGER DEFAULT 0,
            beta_enabled INTEGER DEFAULT 0,
            alpha_enabled INTEGER DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_allowed_channels (
            guild_id INTEGER,
            channel_id INTEGER,
            PRIMARY KEY (guild_id, channel_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_message_visibility (
            guild_id INTEGER,
            message_key TEXT,
            visibility TEXT,
            PRIMARY KEY (guild_id, message_key)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_anfang_message (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER,
            author_id INTEGER,
            updated_at INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_seen_channels (
            user_id INTEGER,
            guild_id INTEGER,
            channel_id INTEGER,
            PRIMARY KEY (user_id, guild_id, channel_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_infinitydust (
            user_id INTEGER PRIMARY KEY,
            amount INTEGER DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_units (
            user_id INTEGER PRIMARY KEY,
            amount INTEGER DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_card_buffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            card_name TEXT,
            buff_type TEXT,
            attack_number INTEGER,
            buff_amount INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, card_name, buff_type, attack_number)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS fight_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            origin_channel_id INTEGER,
            message_channel_id INTEGER,
            thread_id INTEGER,
            thread_created INTEGER DEFAULT 0,
            challenger_id INTEGER,
            challenged_id INTEGER,
            challenger_card TEXT,
            created_at INTEGER,
            status TEXT,
            message_id INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS mission_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            channel_id INTEGER,
            user_id INTEGER,
            mission_data TEXT,
            visibility TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at INTEGER,
            status TEXT,
            message_id INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS durable_view_registry (
            guild_id INTEGER,
            channel_id INTEGER,
            message_id INTEGER,
            view_kind TEXT,
            payload_json TEXT,
            updated_at INTEGER,
            PRIMARY KEY (guild_id, channel_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS active_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            guild_id INTEGER,
            channel_id INTEGER,
            thread_id INTEGER,
            battle_message_id INTEGER,
            log_message_id INTEGER,
            status TEXT,
            payload_json TEXT,
            updated_at INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_threads (
            thread_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            kind TEXT,
            status TEXT,
            updated_at INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_give_op_users (
            guild_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_give_op_roles (
            guild_id INTEGER,
            role_id INTEGER,
            PRIMARY KEY (guild_id, role_id)
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_dust_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            target_id INTEGER,
            guild_id INTEGER,
            channel_id INTEGER,
            action TEXT,
            mode TEXT,
            requested_amount INTEGER,
            applied_amount INTEGER,
            created_at INTEGER
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_stats (
            user_id INTEGER PRIMARY KEY,
            completed_invites INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            created_by_id INTEGER,
            mode TEXT,
            inviter_id INTEGER NOT NULL,
            invitee_id INTEGER NOT NULL,
            pair_key TEXT,
            invitee_is_admin INTEGER NOT NULL DEFAULT 0,
            need_admin INTEGER NOT NULL DEFAULT 0,
            inviter_ok INTEGER NOT NULL DEFAULT 0,
            invitee_ok INTEGER NOT NULL DEFAULT 0,
            admin_ok INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            completed_at INTEGER,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS invite_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pending_id INTEGER,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            created_by_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            inviter_id INTEGER NOT NULL,
            invitee_id INTEGER NOT NULL,
            pair_key TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            completed_at INTEGER,
            status TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER,
            event_type TEXT NOT NULL,
            guild_id INTEGER,
            channel_id INTEGER,
            thread_id INTEGER,
            session_id INTEGER,
            session_kind TEXT,
            actor_user_id INTEGER,
            target_user_id INTEGER,
            command_name TEXT,
            hero_name TEXT,
            attack_name TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analytics_events_created_at
        ON analytics_events (created_at)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analytics_events_event_type
        ON analytics_events (event_type)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_analytics_events_session_id
        ON analytics_events (session_id)
        """
    )

    await _ensure_column(db, "user_daily", "mission_count", "INTEGER DEFAULT 0")
    await _ensure_column(db, "user_daily", "last_mission_reset", "INTEGER")
    await _ensure_column(db, "user_daily", "used_invite", "INTEGER DEFAULT 0")
    await _ensure_column(db, "guild_config", "maintenance_mode", "INTEGER DEFAULT 0")
    await _ensure_column(db, "guild_config", "beta_enabled", "INTEGER DEFAULT 0")
    await _ensure_column(db, "guild_config", "alpha_enabled", "INTEGER DEFAULT 0")
    await _ensure_column(db, "guild_config", "public_channel_id", "INTEGER")
    await _ensure_column(db, "invite_pending", "created_by_id", "INTEGER")
    await _ensure_column(db, "invite_pending", "mode", "TEXT")
    await _ensure_column(db, "invite_pending", "pair_key", "TEXT")
    await _ensure_column(db, "invite_pending", "completed_at", "INTEGER")

    await db.execute(
        """
        UPDATE invite_pending
        SET pair_key =
            CASE
                WHEN inviter_id <= invitee_id THEN guild_id || ':' || inviter_id || ':' || invitee_id
                ELSE guild_id || ':' || invitee_id || ':' || inviter_id
            END
        WHERE pair_key IS NULL OR pair_key = ''
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_invite_pending_pair_final
        ON invite_pending(pair_key)
        WHERE status IN ('pending', 'completed') AND pair_key IS NOT NULL
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invite_history_pair
        ON invite_history(guild_id, pair_key, status)
        """
    )

    # Migrate legacy infinitydust column if present.
    if await _column_exists(db, "user_karten", "infinitydust"):
        cursor = await db.execute(
            "SELECT user_id, SUM(infinitydust) FROM user_karten WHERE infinitydust > 0 GROUP BY user_id"
        )
        rows = await cursor.fetchall()
        for user_id, amount in rows:
            await db.execute(
                "INSERT INTO user_infinitydust (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
                (user_id, amount),
            )
        await db.execute("UPDATE user_karten SET infinitydust = 0")

    await _migrate_default_variant_card_names(db)

    # AFK-Markierungssystem (v2.3.0, Req. 13.8/13.9): persistente Timer für offene
    # Challenges und laufende Kämpfe, damit Pings auch nach Bot-Neustart weiterlaufen.
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS afk_timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            battle_id TEXT NOT NULL UNIQUE,
            thread_id INTEGER,
            challenger_id INTEGER NOT NULL,
            acceptor_id INTEGER NOT NULL,
            active_player_id INTEGER,
            round_number INTEGER NOT NULL DEFAULT 0,
            round_started_at INTEGER NOT NULL,
            last_action_at INTEGER NOT NULL,
            pings_sent_mask INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_afk_battle_id ON afk_timers(battle_id)"
    )

    # Alternative Karten-Designs (v2.4.0): welche ein Spieler freigeschaltet
    # und welches er gewählt hat. Logik in services/designs.py.
    for sql in DESIGN_TABLES_SQL:
        await db.execute(sql)

    # Level- und Einladungs-Belohnungen (v2.5.0), siehe LEVEL_TABLES_SQL.
    for sql in LEVEL_TABLES_SQL:
        await db.execute(sql)

    await db.commit()
