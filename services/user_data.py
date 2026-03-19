import json
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from db import db_context
from karten import karten
from services.card_pool import random_gameplay_card


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


def _card_data_by_name(card_name: str) -> dict | None:
    card_name_normalized = str(card_name or "").strip()
    if not card_name_normalized:
        return None
    for card in karten:
        if str(card.get("name") or "").strip() == card_name_normalized:
            return card
    return None


def _attack_allows_damage_buff(attack: dict) -> bool:
    heal_data = attack.get("heal")
    if isinstance(heal_data, list) and len(heal_data) == 2:
        if int(heal_data[1] or 0) > 0:
            return False
    elif isinstance(heal_data, (int, float)) and int(heal_data) > 0:
        return False
    if float(attack.get("lifesteal_ratio", 0.0) or 0.0) > 0:
        return False
    if attack.get("effects"):
        return False
    self_damage = attack.get("self_damage", 0)
    if isinstance(self_damage, list) and len(self_damage) == 2:
        if max(int(self_damage[0] or 0), int(self_damage[1] or 0)) > 0:
            return False
    elif isinstance(self_damage, (int, float)):
        if int(self_damage) > 0:
            return False
    elif isinstance(self_damage, str):
        if int(self_damage or 0) > 0:
            return False
    else:
        return False
    max_damage = 0
    raw_damage = attack.get("damage", [0, 0])
    if isinstance(raw_damage, list) and len(raw_damage) == 2:
        max_damage = max(max_damage, int(raw_damage[1] or 0))
    elif isinstance(raw_damage, (int, float)):
        max_damage = max(max_damage, int(raw_damage))
    multi_hit = attack.get("multi_hit")
    if isinstance(multi_hit, dict):
        per_hit_damage = multi_hit.get("per_hit_damage")
        if isinstance(per_hit_damage, list) and len(per_hit_damage) == 2:
            max_damage = max(max_damage, int(per_hit_damage[1] or 0))
    return max_damage > 0


async def remove_invalid_damage_card_buffs(*, user_id: int | None = None, card_name: str | None = None) -> int:
    query = (
        "SELECT user_id, card_name, attack_number "
        "FROM user_card_buffs WHERE buff_type = 'damage'"
    )
    params: list[object] = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(int(user_id))
    if card_name is not None:
        query += " AND card_name = ?"
        params.append(str(card_name))

    async with db_context() as db:
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        invalid_rows: list[tuple[int, str, int]] = []
        for row in rows:
            current_card_name = str(row["card_name"] or "")
            attack_number = int(row["attack_number"] or 0)
            card_data = _card_data_by_name(current_card_name)
            attacks = list(card_data.get("attacks", [])) if isinstance(card_data, dict) else []
            attack_index = attack_number - 1
            if attack_index < 0 or attack_index >= len(attacks):
                invalid_rows.append((int(row["user_id"] or 0), current_card_name, attack_number))
                continue
            if not _attack_allows_damage_buff(attacks[attack_index]):
                invalid_rows.append((int(row["user_id"] or 0), current_card_name, attack_number))
        if invalid_rows:
            await db.executemany(
                """
                DELETE FROM user_card_buffs
                WHERE user_id = ? AND card_name = ? AND buff_type = 'damage' AND attack_number = ?
                """,
                invalid_rows,
            )
            await db.commit()
        return len(invalid_rows)


async def get_card_buffs(user_id: int, card_name: str) -> list[tuple[str, int, int]]:
    await remove_invalid_damage_card_buffs(user_id=user_id, card_name=card_name)
    async with db_context() as db:
        cursor = await db.execute(
            """
            SELECT buff_type, attack_number, buff_amount
            FROM user_card_buffs
            WHERE user_id = ? AND card_name = ?
            """,
            (user_id, card_name),
        )
        rows = await cursor.fetchall()
        return [
            (
                str(row[0] or ""),
                int(row[1] or 0),
                int(row[2] or 0),
            )
            for row in rows
        ]


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

    if row and int(row[0] or 0) > 0:
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


async def add_mission_reward(user_id, *, alpha_enabled: bool = True):
    karte = random_gameplay_card(karten, alpha_enabled=alpha_enabled)
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
        rows = await cursor.fetchall()
        return [(str(row[0] or ""), int(row[1] or 0)) for row in rows]


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
