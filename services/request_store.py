import json
import time

from db import db_context


async def create_fight_request(
    *,
    guild_id: int,
    origin_channel_id: int,
    message_channel_id: int,
    thread_id: int | None,
    thread_created: bool,
    challenger_id: int,
    challenged_id: int,
    challenger_card: str,
    message_id: int | None = None,
) -> int:
    async with db_context() as db:
        cursor = await db.execute(
            """
            INSERT INTO fight_requests (
                guild_id, origin_channel_id, message_channel_id, thread_id, thread_created,
                challenger_id, challenged_id, challenger_card, created_at, status, message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                origin_channel_id,
                message_channel_id,
                thread_id,
                1 if thread_created else 0,
                challenger_id,
                challenged_id,
                challenger_card,
                int(time.time()),
                "pending",
                message_id,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def update_fight_request_message(request_id: int, message_id: int | None, message_channel_id: int | None = None) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE fight_requests SET message_id = ?, message_channel_id = COALESCE(?, message_channel_id) WHERE id = ?",
            (message_id, message_channel_id, request_id),
        )
        await db.commit()


async def claim_fight_request(request_id: int, status: str) -> bool:
    async with db_context() as db:
        cursor = await db.execute(
            "UPDATE fight_requests SET status = ? WHERE id = ? AND status = 'pending'",
            (status, request_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_pending_fight_requests():
    async with db_context() as db:
        cursor = await db.execute("SELECT * FROM fight_requests WHERE status = 'pending'")
        return await cursor.fetchall()


async def create_mission_request(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    mission_data: dict,
    visibility: str,
    is_admin: bool,
    message_id: int | None = None,
) -> int:
    async with db_context() as db:
        cursor = await db.execute(
            """
            INSERT INTO mission_requests (
                guild_id, channel_id, user_id, mission_data, visibility, is_admin, created_at, status, message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                user_id,
                json.dumps(mission_data),
                visibility,
                1 if is_admin else 0,
                int(time.time()),
                "pending",
                message_id,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def update_mission_request_message(request_id: int, message_id: int | None, channel_id: int | None = None) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE mission_requests SET message_id = ?, channel_id = COALESCE(?, channel_id) WHERE id = ?",
            (message_id, channel_id, request_id),
        )
        await db.commit()


async def claim_mission_request(request_id: int, status: str) -> bool:
    async with db_context() as db:
        cursor = await db.execute(
            "UPDATE mission_requests SET status = ? WHERE id = ? AND status = 'pending'",
            (status, request_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_pending_mission_requests():
    async with db_context() as db:
        cursor = await db.execute("SELECT * FROM mission_requests WHERE status = 'pending'")
        return await cursor.fetchall()
