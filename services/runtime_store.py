import json
import time
from typing import Any

from db import db_context


def _decode_payload(raw: object) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def upsert_durable_view(
    *,
    guild_id: int,
    channel_id: int,
    message_id: int,
    view_kind: str,
    payload: dict[str, Any] | None = None,
) -> None:
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO durable_view_registry (guild_id, channel_id, message_id, view_kind, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                message_id = excluded.message_id,
                view_kind = excluded.view_kind,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                channel_id,
                message_id,
                view_kind,
                json.dumps(payload or {}, ensure_ascii=True),
                int(time.time()),
            ),
        )
        await db.commit()


async def delete_durable_view(*, guild_id: int, channel_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "DELETE FROM durable_view_registry WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await db.commit()


async def list_durable_views() -> list[dict[str, Any]]:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT guild_id, channel_id, message_id, view_kind, payload_json, updated_at FROM durable_view_registry"
        )
        rows = await cursor.fetchall()
    return [
        {
            "guild_id": int(row["guild_id"]),
            "channel_id": int(row["channel_id"]),
            "message_id": int(row["message_id"]),
            "view_kind": str(row["view_kind"] or ""),
            "payload": _decode_payload(row["payload_json"]),
            "updated_at": int(row["updated_at"] or 0),
        }
        for row in rows
    ]


async def save_active_session(
    *,
    kind: str,
    guild_id: int,
    channel_id: int,
    thread_id: int | None,
    battle_message_id: int | None,
    log_message_id: int | None,
    status: str,
    payload: dict[str, Any],
    session_id: int | None = None,
) -> int:
    now = int(time.time())
    async with db_context() as db:
        if session_id is None:
            cursor = await db.execute(
                """
                INSERT INTO active_sessions (
                    kind, guild_id, channel_id, thread_id, battle_message_id, log_message_id, status, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    guild_id,
                    channel_id,
                    thread_id,
                    battle_message_id,
                    log_message_id,
                    status,
                    json.dumps(payload, ensure_ascii=True),
                    now,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

        await db.execute(
            """
            UPDATE active_sessions
            SET kind = ?, guild_id = ?, channel_id = ?, thread_id = ?, battle_message_id = ?, log_message_id = ?,
                status = ?, payload_json = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                kind,
                guild_id,
                channel_id,
                thread_id,
                battle_message_id,
                log_message_id,
                status,
                json.dumps(payload, ensure_ascii=True),
                now,
                session_id,
            ),
        )
        await db.commit()
    return int(session_id)


async def get_active_session(session_id: int) -> dict[str, Any] | None:
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT session_id, kind, guild_id, channel_id, thread_id, battle_message_id, log_message_id, status, payload_json, updated_at
            FROM active_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "session_id": int(row["session_id"]),
        "kind": str(row["kind"] or ""),
        "guild_id": int(row["guild_id"] or 0),
        "channel_id": int(row["channel_id"] or 0),
        "thread_id": int(row["thread_id"]) if row["thread_id"] else None,
        "battle_message_id": int(row["battle_message_id"]) if row["battle_message_id"] else None,
        "log_message_id": int(row["log_message_id"]) if row["log_message_id"] else None,
        "status": str(row["status"] or ""),
        "payload": _decode_payload(row["payload_json"]),
        "updated_at": int(row["updated_at"] or 0),
    }


async def list_active_sessions(*, statuses: tuple[str, ...] = ("active",)) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in statuses)
    query = (
        "SELECT session_id, kind, guild_id, channel_id, thread_id, battle_message_id, log_message_id, status, payload_json, updated_at "
        f"FROM active_sessions WHERE status IN ({placeholders})"
    )
    async with db_context() as db:
        cursor = await db.execute(query, statuses)
        rows = await cursor.fetchall()
    return [
        {
            "session_id": int(row["session_id"]),
            "kind": str(row["kind"] or ""),
            "guild_id": int(row["guild_id"] or 0),
            "channel_id": int(row["channel_id"] or 0),
            "thread_id": int(row["thread_id"]) if row["thread_id"] else None,
            "battle_message_id": int(row["battle_message_id"]) if row["battle_message_id"] else None,
            "log_message_id": int(row["log_message_id"]) if row["log_message_id"] else None,
            "status": str(row["status"] or ""),
            "payload": _decode_payload(row["payload_json"]),
            "updated_at": int(row["updated_at"] or 0),
        }
        for row in rows
    ]


async def update_session_status(session_id: int, status: str) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE active_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, int(time.time()), session_id),
        )
        await db.commit()


async def save_managed_thread(*, thread_id: int, guild_id: int, kind: str, status: str = "active") -> None:
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO managed_threads (thread_id, guild_id, kind, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                kind = excluded.kind,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (thread_id, guild_id, kind, status, int(time.time())),
        )
        await db.commit()


async def update_managed_thread_status(thread_id: int, status: str) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE managed_threads SET status = ?, updated_at = ? WHERE thread_id = ?",
            (status, int(time.time()), thread_id),
        )
        await db.commit()


async def is_managed_thread(thread_id: int) -> bool:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT 1 FROM managed_threads WHERE thread_id = ? AND status != 'deleted'",
            (thread_id,),
        )
        row = await cursor.fetchone()
    return row is not None
