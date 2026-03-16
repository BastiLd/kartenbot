import json
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from db import db_context
from karten import karten


def _berlin_midnight_epoch() -> int:
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


async def add_infinitydust(user_id, amount=1):
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

        if row:
            current_dust = row[0] or 0
            new_dust = current_dust + amount
            await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_dust, user_id))
        else:
            await db.execute("INSERT INTO user_infinitydust (user_id, amount) VALUES (?, ?)", (user_id, amount))
        await db.commit()


async def get_infinitydust(user_id):
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0


async def spend_infinitydust(user_id, amount):
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current_dust = row[0] if row and row[0] else 0
        if current_dust < amount:
            return False

        new_amount = current_dust - amount
        await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_amount, user_id))
        await db.commit()
        return True


async def remove_infinitydust(user_id, amount):
    if amount <= 0:
        return 0
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current_dust = row[0] if row and row[0] else 0
        removed = min(int(current_dust), int(amount))
        new_amount = max(0, int(current_dust) - removed)
        if row:
            await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_amount, user_id))
        else:
            await db.execute("INSERT INTO user_infinitydust (user_id, amount) VALUES (?, 0)", (user_id,))
        await db.commit()
        return removed


async def log_admin_dust_action(
    actor_id: int,
    target_id: int,
    *,
    guild_id: int = 0,
    channel_id: int = 0,
    action: str,
    mode: str,
    requested_amount: int,
    applied_amount: int,
) -> None:
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO admin_dust_audit (
                actor_id,
                target_id,
                guild_id,
                channel_id,
                action,
                mode,
                requested_amount,
                applied_amount,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(actor_id or 0),
                int(target_id or 0),
                int(guild_id or 0),
                int(channel_id or 0),
                str(action or "give"),
                str(mode or "single"),
                int(requested_amount or 0),
                int(applied_amount or 0),
                int(time.time()),
            ),
        )
        await db.commit()


async def add_card_buff(user_id, card_name, buff_type, attack_number, buff_amount):
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO user_card_buffs
            (user_id, card_name, buff_type, attack_number, buff_amount)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, card_name, buff_type, attack_number)
            DO UPDATE SET
                buff_amount = user_card_buffs.buff_amount + excluded.buff_amount,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, card_name, buff_type, attack_number, buff_amount),
        )
        await db.commit()


async def get_card_buffs(user_id: int, card_name: str) -> list[tuple[str, int, int]]:
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT buff_type, attack_number, buff_amount
            FROM user_card_buffs
            WHERE user_id = ? AND card_name = ?
            """,
            (user_id, card_name),
        )
        return await cursor.fetchall()


async def add_karte(user_id, karten_name):
    async with db_context() as db:
        await db.execute(
            "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + 1",
            (user_id, karten_name),
        )
        await db.commit()


async def check_and_add_karte(user_id, karte):
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_karten WHERE user_id = ? AND karten_name = ?",
            (user_id, karte["name"]),
        )
        row = await cursor.fetchone()

    if row[0] > 0:
        await add_infinitydust(user_id, 1)
        return False

    await add_karte(user_id, karte["name"])
    return True


async def add_karte_amount(user_id, karten_name, amount: int):
    if amount <= 0:
        return
    async with db_context() as db:
        await db.execute(
            "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + excluded.anzahl",
            (user_id, karten_name, amount),
        )
        await db.commit()


async def remove_karte_amount(user_id, karten_name, amount: int) -> int:
    if amount <= 0:
        return 0
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT anzahl FROM user_karten WHERE user_id = ? AND karten_name = ?",
            (user_id, karten_name),
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        current = row[0] or 0
        new_amount = current - amount
        if new_amount <= 0:
            await db.execute(
                "DELETE FROM user_karten WHERE user_id = ? AND karten_name = ?",
                (user_id, karten_name),
            )
            await db.commit()
            return 0
        await db.execute(
            "UPDATE user_karten SET anzahl = ? WHERE user_id = ? AND karten_name = ?",
            (new_amount, user_id, karten_name),
        )
        await db.commit()
        return new_amount


async def add_mission_reward(user_id):
    karte = random.choice(karten)
    is_new_card = await check_and_add_karte(user_id, karte)
    return karte, is_new_card


async def get_mission_count(user_id):
    today_start = _berlin_midnight_epoch()

    async with db_context() as db:
        cursor = await db.execute(
            "SELECT mission_count, last_mission_reset FROM user_daily WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

        if not row or row[1] is None or row[1] < today_start:
            await db.execute(
                "INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, 0, ?)",
                (user_id, today_start),
            )
            await db.commit()
            return 0
        return row[0] or 0


async def increment_mission_count(user_id):
    today_start = _berlin_midnight_epoch()

    async with db_context() as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES "
            "(?, COALESCE((SELECT mission_count FROM user_daily WHERE user_id = ?), 0) + 1, ?)",
            (user_id, user_id, today_start),
        )
        await db.commit()


async def get_team(user_id: int) -> list[int]:
    async with db_context() as db:
        cursor = await db.execute("SELECT team FROM user_teams WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return []


async def set_team(user_id: int, team: list[int]) -> None:
    async with db_context() as db:
        await db.execute("INSERT OR REPLACE INTO user_teams (user_id, team) VALUES (?, ?)", (user_id, json.dumps(team)))
        await db.commit()


async def get_user_karten(user_id: int) -> list[tuple[str, int]]:
    async with db_context() as db:
        cursor = await db.execute("SELECT karten_name, anzahl FROM user_karten WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()


async def get_last_karte(user_id):
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT karten_name FROM user_karten WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def delete_user_data(user_id: int) -> None:
    async with db_context() as db:
        await db.execute("DELETE FROM user_karten WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_daily WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_infinitydust WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_card_buffs WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_seen_channels WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM tradingpost WHERE seller_id = ?", (user_id,))
        await db.commit()
