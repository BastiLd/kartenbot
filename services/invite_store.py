from __future__ import annotations

import time
from typing import Any

from db import db_context
from invite_reward_config import INVITE_FIRST_REWARD_CARD_VARIANT, INVITE_FIRST_REWARD_FALLBACK_VARIANT
from karten import karten
from services.card_variants import build_runtime_card
from services.user_data import add_infinitydust, check_and_add_karte

INVITE_MAX_MEMBER_AGE_DAYS_KEY = "invite.max_member_age_days"
DEFAULT_INVITE_MAX_MEMBER_AGE_DAYS = 7


def invite_pair_key(guild_id: int, user_a: int, user_b: int) -> str:
    low, high = sorted((int(user_a), int(user_b)))
    return f"{int(guild_id)}:{low}:{high}"


def configured_first_invite_reward_card() -> dict[str, Any]:
    for candidate in (INVITE_FIRST_REWARD_CARD_VARIANT, INVITE_FIRST_REWARD_FALLBACK_VARIANT):
        card = build_runtime_card(candidate, cards=karten)
        if card is not None:
            return card
    raise ValueError(
        "No valid invite reward card configured. "
        "Check INVITE_FIRST_REWARD_CARD_VARIANT in invite_reward_config.py."
    )


async def get_invite_max_member_age_days() -> int:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT value FROM bot_settings WHERE key = ?",
            (INVITE_MAX_MEMBER_AGE_DAYS_KEY,),
        )
        row = await cursor.fetchone()
        if row is None:
            return DEFAULT_INVITE_MAX_MEMBER_AGE_DAYS
        try:
            return max(0, int(row[0]))
        except (TypeError, ValueError):
            return DEFAULT_INVITE_MAX_MEMBER_AGE_DAYS


async def set_invite_max_member_age_days(days: int) -> int:
    normalized = max(0, int(days))
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO bot_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (INVITE_MAX_MEMBER_AGE_DAYS_KEY, str(normalized)),
        )
        await db.commit()
    return normalized


async def get_invite_completed_count(inviter_id: int) -> int:
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT completed_invites FROM invite_stats WHERE user_id = ?",
            (int(inviter_id),),
        )
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0


async def find_existing_invite_pair(guild_id: int, user_a: int, user_b: int) -> dict[str, Any] | None:
    pair_key = invite_pair_key(guild_id, user_a, user_b)
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT * FROM invite_pending
            WHERE guild_id = ?
            AND pair_key = ?
            AND status IN ('pending', 'completed')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(guild_id), pair_key),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_invite_pending(
    *,
    guild_id: int,
    channel_id: int,
    created_by_id: int,
    mode: str,
    inviter_id: int,
    invitee_id: int,
    invitee_is_admin: bool,
    need_admin: bool,
) -> tuple[int, bool]:
    now = int(time.time())
    pair_key = invite_pair_key(guild_id, inviter_id, invitee_id)
    normalized_mode = str(mode or "").strip() or "unknown"
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT id FROM invite_pending
            WHERE guild_id = ?
            AND pair_key = ?
            AND status IN ('pending', 'completed')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(guild_id), pair_key),
        )
        row = await cursor.fetchone()
        if row:
            return int(row["id"]), False

        cursor = await db.execute(
            """
            INSERT INTO invite_pending (
                guild_id, channel_id, message_id,
                created_by_id, mode,
                inviter_id, invitee_id, pair_key, invitee_is_admin,
                need_admin, inviter_ok, invitee_ok, admin_ok,
                created_at, status
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, 'pending')
            """,
            (
                int(guild_id),
                int(channel_id),
                int(created_by_id),
                normalized_mode,
                int(inviter_id),
                int(invitee_id),
                pair_key,
                1 if invitee_is_admin else 0,
                1 if need_admin else 0,
                now,
            ),
        )
        pending_id = int(cursor.lastrowid)
        await db.execute(
            """
            INSERT INTO invite_history (
                pending_id, guild_id, channel_id, message_id,
                created_by_id, mode, inviter_id, invitee_id, pair_key,
                created_at, status
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                pending_id,
                int(guild_id),
                int(channel_id),
                int(created_by_id),
                normalized_mode,
                int(inviter_id),
                int(invitee_id),
                pair_key,
                now,
            ),
        )
        await db.commit()
        return pending_id, True


async def set_invite_pending_message_id(pending_id: int, message_id: int) -> None:
    async with db_context() as db:
        await db.execute(
            "UPDATE invite_pending SET message_id = ? WHERE id = ? AND status = 'pending'",
            (int(message_id), int(pending_id)),
        )
        await db.execute(
            "UPDATE invite_history SET message_id = ? WHERE pending_id = ? AND status = 'pending'",
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
    _ = alpha_enabled
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

        completed_at = int(time.time())
        # Alle Schreibvorgänge müssen gemeinsam gelten: schlägt einer fehl, wird der
        # gesamte Block zurückgerollt, damit nicht z. B. der "completed"-Status gesetzt
        # ist, die Statistik aber nicht hochgezählt wurde (inkonsistenter Zustand).
        try:
            u = await db.execute(
                """
                UPDATE invite_pending SET status = 'completed', completed_at = ?
                WHERE id = ? AND status = 'pending'
                AND inviter_ok = 1 AND invitee_ok = 1
                AND (need_admin = 0 OR admin_ok = 1)
                """,
                (completed_at, int(pending_id)),
            )
            if u.rowcount != 1:
                await db.rollback()
                return None

            await db.execute(
                """
                UPDATE invite_history
                SET status = 'completed', completed_at = ?
                WHERE pending_id = ? AND status = 'pending'
                """,
                (completed_at, int(pending_id)),
            )

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
        except Exception:
            await db.rollback()
            raise

    if prior == 0:
        card = configured_first_invite_reward_card()
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
