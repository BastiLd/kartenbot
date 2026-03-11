import sqlite3
import time

from db import db_context


async def add_give_op_user(guild_id: int, user_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_give_op_users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        await db.commit()


async def remove_give_op_user(guild_id: int, user_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "DELETE FROM guild_give_op_users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await db.commit()


async def add_give_op_role(guild_id: int, role_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_give_op_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )
        await db.commit()


async def remove_give_op_role(guild_id: int, role_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "DELETE FROM guild_give_op_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await db.commit()


async def get_give_op_allowed_users(guild_id: int) -> set[int]:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT user_id FROM guild_give_op_users WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
    return {int(row[0]) for row in rows}


async def get_give_op_allowed_roles(guild_id: int) -> set[int]:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT role_id FROM guild_give_op_roles WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
    return {int(row[0]) for row in rows}


async def is_maintenance_enabled(guild_id: int) -> bool:
    if not guild_id:
        return False
    async with db_context() as db:
        cursor = await db.execute("SELECT maintenance_mode FROM guild_config WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        return bool(row[0]) if row and row[0] else False


async def set_maintenance_mode(guild_id: int, enabled: bool) -> None:
    async with db_context() as db:
        await db.execute(
            "INSERT INTO guild_config (guild_id, maintenance_mode) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET maintenance_mode = excluded.maintenance_mode",
            (guild_id, 1 if enabled else 0),
        )
        await db.commit()


async def _ensure_visibility_table(db) -> None:
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
    await db.commit()


async def _ensure_anfang_table(db) -> None:
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
    await db.commit()


async def get_latest_anfang_message(guild_id: int | None):
    if not guild_id:
        return None
    async with db_context() as db:
        try:
            cursor = await db.execute(
                "SELECT channel_id, message_id FROM guild_anfang_message WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) and "guild_anfang_message" in str(exc):
                await _ensure_anfang_table(db)
                return None
            raise
    if row:
        return int(row[0]), int(row[1])
    return None


async def set_latest_anfang_message(guild_id: int, channel_id: int, message_id: int, author_id: int) -> None:
    async with db_context() as db:
        try:
            await db.execute(
                """
                INSERT INTO guild_anfang_message (guild_id, channel_id, message_id, author_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id,
                    author_id = excluded.author_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, message_id, author_id, int(time.time())),
            )
            await db.commit()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) and "guild_anfang_message" in str(exc):
                await _ensure_anfang_table(db)
                await db.execute(
                    """
                    INSERT INTO guild_anfang_message (guild_id, channel_id, message_id, author_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        message_id = excluded.message_id,
                        author_id = excluded.author_id,
                        updated_at = excluded.updated_at
                    """,
                    (guild_id, channel_id, message_id, author_id, int(time.time())),
                )
                await db.commit()
                return
            raise


async def get_visibility_override(guild_id: int | None, message_key: str) -> str | None:
    if not guild_id:
        return None
    async with db_context() as db:
        try:
            cursor = await db.execute(
                "SELECT visibility FROM guild_message_visibility WHERE guild_id = ? AND message_key = ?",
                (guild_id, message_key),
            )
            row = await cursor.fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) and "guild_message_visibility" in str(exc):
                await _ensure_visibility_table(db)
                cursor = await db.execute(
                    "SELECT visibility FROM guild_message_visibility WHERE guild_id = ? AND message_key = ?",
                    (guild_id, message_key),
                )
                row = await cursor.fetchone()
            else:
                raise
    return row[0] if row and row[0] else None


async def get_message_visibility(
    guild_id: int | None,
    message_key: str,
    *,
    default_visibility: str,
    legacy_visibility_keys: dict[str, str] | None = None,
) -> str:
    if not guild_id:
        return default_visibility
    override = await get_visibility_override(guild_id, message_key)
    if override:
        return override
    legacy_key = (legacy_visibility_keys or {}).get(message_key)
    if legacy_key:
        legacy_override = await get_visibility_override(guild_id, legacy_key)
        if legacy_override:
            return legacy_override
    return default_visibility


async def get_visibility_map(guild_id: int | None) -> dict[str, str]:
    if not guild_id:
        return {}
    async with db_context() as db:
        try:
            cursor = await db.execute(
                "SELECT message_key, visibility FROM guild_message_visibility WHERE guild_id = ?",
                (guild_id,),
            )
            rows = await cursor.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) and "guild_message_visibility" in str(exc):
                await _ensure_visibility_table(db)
                cursor = await db.execute(
                    "SELECT message_key, visibility FROM guild_message_visibility WHERE guild_id = ?",
                    (guild_id,),
                )
                rows = await cursor.fetchall()
            else:
                raise
    return {row[0]: row[1] for row in rows}


async def set_message_visibility(guild_id: int | None, message_key: str, visibility: str) -> None:
    if not guild_id:
        return
    async with db_context() as db:
        try:
            await db.execute(
                "INSERT INTO guild_message_visibility (guild_id, message_key, visibility) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id, message_key) DO UPDATE SET visibility = excluded.visibility",
                (guild_id, message_key, visibility),
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) and "guild_message_visibility" in str(exc):
                await _ensure_visibility_table(db)
                await db.execute(
                    "INSERT INTO guild_message_visibility (guild_id, message_key, visibility) VALUES (?, ?, ?) "
                    "ON CONFLICT(guild_id, message_key) DO UPDATE SET visibility = excluded.visibility",
                    (guild_id, message_key, visibility),
                )
            else:
                raise
        await db.commit()
