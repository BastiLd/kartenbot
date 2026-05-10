from __future__ import annotations

import time
from typing import Any

from db import db_context
from karten import karten
from services.card_pool import random_gameplay_card
from services.user_data import add_infinitydust, check_and_add_karte


async def get_invite_completed_count(inviter_id: int) -> int:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT completed_invites FROM invite_stats WHERE user_id = ?",
            (int(inviter_id),),
        )
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0


async def create_invite_pending(
    *,
    guild_id: int,
    channel_id: int,
    inviter_id: int,
    invitee_id: int,
    invitee_is_admin: bool,
    need_admin: bool,
) -> int:
    now = int(time.time())
    async with db_context() as db:
        cursor = await db.execute(
            """
            INSERT INTO invite_pending (
                guild_id, channel_id, message_id,
                inviter_id, invitee_id, invitee_is_admin,
                need_admin, inviter_ok, invitee_ok, admin_ok,
                created_at, status
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, 0, 0, 0, ?, 'pending')
            """,
            (
                int(guild_id),
                int(channel_id),
                int(inviter_id),
                int(invitee_id),
                1 if invitee_is_admin else 0,
                1 if need_admin else 0,
                now,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def set_invite_pending_message_id(pending_id: int, message_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE invite_pending SET message_id = ? WHERE id = ? AND status = 'pending'",
            (int(message_id), int(pending_id)),
        )
        await db.commit()


async def load_invite_pending(pending_id: int) -> dict[str, Any] | None:
    async with db_context() as db:
        cursor = await db.execute("SELECT * FROM invite_pending WHERE id = ?", (int(pending_id),))
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)


async def mark_invite_pending_flag(
    pending_id: int,
    *,
    inviter: bool = False,
    invitee: bool = False,
    admin: bool = False,
) -> dict[str, Any] | None:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT * FROM invite_pending WHERE id = ? AND status = 'pending'",
            (int(pending_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        inviter_ok = int(row["inviter_ok"] or 0)
        invitee_ok = int(row["invitee_ok"] or 0)
        admin_ok = int(row["admin_ok"] or 0)
        if inviter:
            inviter_ok = 1
        if invitee:
            invitee_ok = 1
        if admin:
            admin_ok = 1
        await db.execute(
            """
            UPDATE invite_pending
            SET inviter_ok = ?, invitee_ok = ?, admin_ok = ?
            WHERE id = ? AND status = 'pending'
            """,
            (inviter_ok, invitee_ok, admin_ok, int(pending_id)),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM invite_pending WHERE id = ?", (int(pending_id),))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def finalize_invite_pending_if_ready(pending_id: int, *, alpha_enabled: bool) -> dict[str, Any] | None:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT * FROM invite_pending WHERE id = ? AND status = 'pending'",
            (int(pending_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if not int(row["inviter_ok"] or 0) or not int(row["invitee_ok"] or 0):
            return None
        if int(row["need_admin"] or 0) and not int(row["admin_ok"] or 0):
            return None

        inviter_id = int(row["inviter_id"])
        invitee_id = int(row["invitee_id"])
        invitee_is_admin = bool(int(row["invitee_is_admin"] or 0))

        cursor = await db.execute(
            "SELECT completed_invites FROM invite_stats WHERE user_id = ?",
            (inviter_id,),
        )
        stat_row = await cursor.fetchone()
        prior = int(stat_row[0] or 0) if stat_row else 0

        u = await db.execute(
            """
            UPDATE invite_pending SET status = 'completed'
            WHERE id = ? AND status = 'pending'
            AND inviter_ok = 1 AND invitee_ok = 1
            AND (need_admin = 0 OR admin_ok = 1)
            """,
            (int(pending_id),),
        )
        if u.rowcount != 1:
            await db.rollback()
            return None

        await db.execute(
            """
            INSERT INTO invite_stats (user_id, completed_invites)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                completed_invites = completed_invites + 1
            """,
            (inviter_id,),
        )
        if not invitee_is_admin:
            await db.execute(
                """
                INSERT OR REPLACE INTO user_daily (user_id, last_daily, used_invite)
                VALUES (
                    ?,
                    COALESCE((SELECT last_daily FROM user_daily WHERE user_id = ?), 0),
                    1
                )
                """,
                (invitee_id, invitee_id),
            )
        await db.commit()

    if prior == 0:
        card = random_gameplay_card(karten, alpha_enabled=alpha_enabled, context="invite_reward")
        await check_and_add_karte(inviter_id, card)
        await add_infinitydust(invitee_id, 5)
        reward_summary: dict[str, Any] = {"kind": "first", "card_name": str(card.get("name") or "")}
    else:
        await add_infinitydust(inviter_id, 5)
        await add_infinitydust(invitee_id, 5)
        reward_summary = {"kind": "repeat"}

    return {
        "inviter_id": inviter_id,
        "invitee_id": invitee_id,
        "reward_summary": reward_summary,
    }
