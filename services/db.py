import logging
import os
from contextlib import asynccontextmanager

import aiosqlite

DB_PATH = os.getenv("KARTENBOT_DB_PATH", "kartenbot.db")

_db = None


async def connect_db():
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA foreign_keys = ON")
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
    except Exception:
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
            maintenance_mode INTEGER DEFAULT 0
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

    await _ensure_column(db, "user_daily", "mission_count", "INTEGER DEFAULT 0")
    await _ensure_column(db, "user_daily", "last_mission_reset", "INTEGER")
    await _ensure_column(db, "user_daily", "used_invite", "INTEGER DEFAULT 0")
    await _ensure_column(db, "guild_config", "maintenance_mode", "INTEGER DEFAULT 0")

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

    await db.commit()
