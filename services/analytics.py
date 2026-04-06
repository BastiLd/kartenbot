from __future__ import annotations

import json
import time
from typing import Any

from db import db_context, init_db


_analytics_schema_ready = False


async def _ensure_analytics_schema() -> None:
    global _analytics_schema_ready
    if _analytics_schema_ready:
        return
    await init_db()
    _analytics_schema_ready = True


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip() or "0")
        except ValueError:
            return 0
    try:
        return int(str(value or "0").strip() or "0")
    except (TypeError, ValueError):
        return 0


def _as_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


async def log_event(
    event_type: str,
    *,
    guild_id: int | None = None,
    channel_id: int | None = None,
    thread_id: int | None = None,
    session_id: int | None = None,
    session_kind: str | None = None,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    command_name: str | None = None,
    hero_name: str | None = None,
    attack_name: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    await _ensure_analytics_schema()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    async with db_context() as db:
        cursor = await db.execute(
            """
            INSERT INTO analytics_events (
                created_at,
                event_type,
                guild_id,
                channel_id,
                thread_id,
                session_id,
                session_kind,
                actor_user_id,
                target_user_id,
                command_name,
                hero_name,
                attack_name,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                str(event_type or "unknown"),
                _as_int(guild_id),
                _as_int(channel_id),
                _as_int(thread_id),
                _as_int(session_id),
                _as_text(session_kind),
                _as_int(actor_user_id),
                _as_int(target_user_id),
                _as_text(command_name),
                _as_text(hero_name),
                _as_text(attack_name),
                payload_json,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def fetch_events() -> list[dict[str, Any]]:
    await _ensure_analytics_schema()
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT
                id,
                created_at,
                event_type,
                guild_id,
                channel_id,
                thread_id,
                session_id,
                session_kind,
                actor_user_id,
                target_user_id,
                command_name,
                hero_name,
                attack_name,
                payload_json
            FROM analytics_events
            ORDER BY created_at ASC, id ASC
            """
        )
        rows = await cursor.fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        payload_raw = row["payload_json"] if "payload_json" in row.keys() else "{}"
        try:
            payload = json.loads(payload_raw or "{}")
        except (TypeError, ValueError):
            payload = {}
        events.append(
            {
                "id": _as_int(row["id"]),
                "created_at": _as_int(row["created_at"]),
                "event_type": _as_text(row["event_type"]) or "unknown",
                "guild_id": _as_int(row["guild_id"]),
                "channel_id": _as_int(row["channel_id"]),
                "thread_id": _as_int(row["thread_id"]),
                "session_id": _as_int(row["session_id"]),
                "session_kind": _as_text(row["session_kind"]) or "",
                "actor_user_id": _as_int(row["actor_user_id"]),
                "target_user_id": _as_int(row["target_user_id"]),
                "command_name": _as_text(row["command_name"]) or "",
                "hero_name": _as_text(row["hero_name"]) or "",
                "attack_name": _as_text(row["attack_name"]) or "",
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return events
