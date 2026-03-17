import asyncio
import json
import logging
import aiosqlite
import sys
import random
import re
import time
import os
from collections import deque
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable, Protocol, TypedDict, cast

import discord
from discord import app_commands, ui, SelectOption
from discord.ext import commands

from botcommands import (
    register_admin_commands,
    register_gameplay_commands,
    register_player_commands,
)
from botcore.bootstrap import BOT_START_TIME, build_bot, run_bot
from botcore.interaction_utils import defer_interaction, edit_interaction_message, send_interaction_response
from botcore.logging_utils import LOG_PATH, configure_logging, get_error_count
from botcore.messages import (
    BUG_FORM_NOT_CONFIGURED,
    CLOSE_PERMISSION_DENIED,
    DM_DISABLED,
    DM_LOG_SEND_FAILED,
    MAINTENANCE_ACTIVE,
    PARTICIPANTS_OR_ADMINS_ONLY,
    SERVER_ONLY,
    THREAD_CLOSING,
)
from botcore.name_utils import escape_display_text, safe_display_name, safe_thread_name, safe_user_option_label
from botcore.ui_common import (
    RestrictedModal as BaseRestrictedModal,
    RestrictedView as BaseRestrictedView,
    ShowAllMembersPager,
)
from db import DB_PATH, close_db, db_context, init_db
from karten import karten as RAW_KARTEN
from services.battle import (
    STATUS_CIRCLE_MAP,
    STATUS_PRIORITY_MAP,
    _presence_to_color,
    build_battle_log_entry,
    calculate_damage,
    create_battle_embed,
    create_battle_log_embed,
    resolve_multi_hit_damage,
    update_battle_log,
)
from services import battle_state
from services.card_validation import summarize_validation_issues, validate_cards
from services.battle_types import CardData
from services.guild_settings import (
    add_give_op_role,
    add_give_op_user,
    get_give_op_allowed_roles,
    get_give_op_allowed_users,
    get_latest_anfang_message as load_latest_anfang_message,
    get_message_visibility as resolve_message_visibility,
    get_visibility_map as load_visibility_map,
    get_visibility_override as load_visibility_override,
    is_maintenance_enabled,
    remove_give_op_role,
    remove_give_op_user,
    set_latest_anfang_message as store_latest_anfang_message,
    set_maintenance_mode,
    set_message_visibility,
)
from services.request_store import (
    claim_fight_request,
    claim_mission_request,
    create_fight_request,
    create_mission_request,
    get_pending_fight_requests,
    get_pending_mission_requests,
    update_fight_request_message,
    update_mission_request_message,
)
from services.runtime_store import (
    delete_durable_view,
    get_active_session,
    get_active_session_for_channel,
    is_managed_thread,
    list_active_sessions,
    list_durable_views,
    patch_active_session_payload,
    save_active_session,
    save_managed_thread,
    update_managed_thread_status,
    update_session_status,
    upsert_durable_view,
)
from services.user_data import (
    add_card_buff,
    add_infinitydust,
    add_karte,
    add_karte_amount,
    add_mission_reward,
    check_and_add_karte,
    delete_user_data,
    get_card_buffs,
    get_infinitydust,
    get_last_karte,
    get_mission_count,
    get_team,
    get_user_karten,
    increment_mission_count,
    log_admin_dust_action,
    remove_invalid_damage_card_buffs,
    remove_infinitydust,
    remove_karte_amount,
    set_team,
    spend_infinitydust,
)
import secrets

configure_logging()

KATABUMP_MAX_INTERACTIONS_PER_MIN = 200
KATABUMP_INTERACTION_WINDOW_SEC = 60
DUST_MENU_AMOUNTS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 70, 100]
FIGHT_OPPONENT_ROLE_ID = 1482325886471766090
_interaction_timestamps = deque()
_persistent_views_registered = False
karten: list[CardData] = cast(list[CardData], RAW_KARTEN)

VIEW_KIND_INTRO_PROMPT = "intro_prompt"
VIEW_KIND_FIGHT_CHALLENGE = "fight_challenge"
VIEW_KIND_FIGHT_CARD_SELECT = "fight_card_select"
VIEW_KIND_BATTLE = "battle"
VIEW_KIND_FIGHT_FEEDBACK = "fight_feedback"
VIEW_KIND_THREAD_CLOSE = "thread_close"
VIEW_KIND_MISSION_ACCEPT = "mission_accept"
VIEW_KIND_MISSION_CARD_SELECT = "mission_card_select"
VIEW_KIND_MISSION_PAUSE = "mission_pause"
VIEW_KIND_MISSION_NEW_CARD_SELECT = "mission_new_card_select"
VIEW_KIND_MISSION_BATTLE = "mission_battle"

THREAD_KIND_FIGHT = "fight"
THREAD_KIND_MISSION = "mission"


class ThreadAutoClosePolicy(TypedDict):
    delay: int | None
    close_on_idle: bool
    close_after_no_bug: bool
    keep_open_after_bug: bool


DEFAULT_THREAD_AUTO_CLOSE_POLICY: ThreadAutoClosePolicy = {
    "delay": 18,
    "close_on_idle": True,
    "close_after_no_bug": True,
    "keep_open_after_bug": True,
}
CANCELLED_THREAD_AUTO_CLOSE_POLICY: ThreadAutoClosePolicy = {
    "delay": 10,
    "close_on_idle": True,
    "close_after_no_bug": True,
    "keep_open_after_bug": True,
}
MISSION_THREAD_AUTO_CLOSE_POLICY: ThreadAutoClosePolicy = {
    "delay": DEFAULT_THREAD_AUTO_CLOSE_POLICY["delay"],
    "close_on_idle": DEFAULT_THREAD_AUTO_CLOSE_POLICY["close_on_idle"],
    "close_after_no_bug": DEFAULT_THREAD_AUTO_CLOSE_POLICY["close_after_no_bug"],
    "keep_open_after_bug": DEFAULT_THREAD_AUTO_CLOSE_POLICY["keep_open_after_bug"],
}


def _copy_thread_auto_close_policy(policy: ThreadAutoClosePolicy | None) -> ThreadAutoClosePolicy | None:
    if policy is None:
        return None
    return {
        "delay": int(policy.get("delay") or 0) or None,
        "close_on_idle": bool(policy.get("close_on_idle", False)),
        "close_after_no_bug": bool(policy.get("close_after_no_bug", True)),
        "keep_open_after_bug": bool(policy.get("keep_open_after_bug", True)),
    }


def _thread_auto_close_delay(policy: ThreadAutoClosePolicy | None) -> int | None:
    copied = _copy_thread_auto_close_policy(policy)
    if copied is None:
        return None
    return copied.get("delay")


def _thread_auto_close_hint(policy: ThreadAutoClosePolicy | None) -> str:
    delay = _thread_auto_close_delay(policy)
    if not delay:
        return ""
    return f"Der Thread schlie\u00dft automatisch in {int(delay)} Sekunden, wenn kein Bug gemeldet wird."


class SendableChannel(Protocol):
    async def send(self, *args: Any, **kwargs: Any) -> discord.Message: ...


class SupportsUpdateHp(Protocol):
    def update_hp(self, new_hp: int) -> None: ...


def _coerce_sendable_channel(channel: object) -> SendableChannel | None:
    if channel is None or not hasattr(channel, "send"):
        return None
    return cast(SendableChannel, channel)


def _channel_mention_or_fallback(channel: object) -> str:
    mention = getattr(channel, "mention", None)
    if isinstance(mention, str) and mention:
        return mention
    channel_id = getattr(channel, "id", None)
    if isinstance(channel_id, int):
        return f"<#{channel_id}>"
    return "dieser Kanal"


def _effect_source_name(source: object) -> str:
    text = str(source or "").strip()
    return text or "Effekt"


def _damage_transition_text(
    original_damage: int,
    final_damage: int,
    *,
    source: object | None = None,
    context: str = "Schaden",
) -> str:
    before = max(0, int(original_damage or 0))
    after = max(0, int(final_damage or 0))
    source_name = str(_effect_source_name(source)).strip() if source else ""
    if source_name:
        return f"{context}: {before} -> {after} durch {escape_display_text(source_name, fallback='Effekt')}."
    return f"{context}: {before} -> {after}."


async def _delete_message_quietly(message: discord.Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except discord.NotFound:
        return
    except Exception:
        logging.exception("Failed to delete message %s", getattr(message, "id", None))


def _member_has_role(member: discord.Member, role_id: int) -> bool:
    return any(getattr(role, "id", None) == role_id for role in getattr(member, "roles", ()))


def _get_fight_opponent_candidates(guild: discord.Guild, challenger: discord.Member) -> list[discord.Member]:
    return [
        member
        for member in guild.members
        if not member.bot and member != challenger and _member_has_role(member, FIGHT_OPPONENT_ROLE_ID)
    ]


def _member_presence_priority(member: discord.Member) -> int:
    status = member.status
    if status == discord.Status.online:
        return 0
    if status == discord.Status.idle:
        return 1
    if status == discord.Status.dnd:
        return 2
    return 3


def _member_status_circle(member: discord.Member) -> str:
    status = member.status
    if status == discord.Status.online:
        return "🟢"
    if status == discord.Status.idle:
        return "🟡"
    if status == discord.Status.dnd:
        return "🔴"
    return "⚫"


def _effect_source_text(source: object, message: str) -> str:
    return f"{_effect_source_name(source)}: {message}"


class SimpleBotUser:
    def __init__(self, *, bot_id: int = 0, display_name: str = "Bot", mention: str = "**Bot**") -> None:
        self.id = bot_id
        self.display_name = display_name
        self.mention = mention


def _get_member_if_available(guild: discord.Guild | None, user_id: int) -> discord.Member | None:
    if guild is None:
        return None
    return guild.get_member(user_id)


def _interaction_member_or_none(interaction: discord.Interaction) -> discord.Member | None:
    user = interaction.user
    if isinstance(user, discord.Member):
        return user
    return None


def _interaction_message_or_none(interaction: discord.Interaction) -> discord.Message | None:
    message = interaction.message
    if isinstance(message, discord.Message):
        return message
    return None


def _member_role_ids(member: discord.Member | None) -> set[int]:
    if member is None:
        return set()
    return {role.id for role in member.roles}


def _json_clone(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError):
        return value


def _dict_str_any(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_any(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_keyed_dict(value: object) -> dict[int, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, Any] = {}
    for key, item in value.items():
        parsed_key = _maybe_int(key)
        if parsed_key is None:
            continue
        result[parsed_key] = item
    return result


def _nested_int_keyed_dict(value: object) -> dict[int, dict[int, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, dict[int, Any]] = {}
    for key, item in value.items():
        outer_key = _maybe_int(key)
        if outer_key is None:
            continue
        if isinstance(item, dict):
            inner: dict[int, Any] = {}
            for inner_key, inner_value in item.items():
                parsed_inner_key = _maybe_int(inner_key)
                if parsed_inner_key is None:
                    continue
                inner[parsed_inner_key] = inner_value
            result[outer_key] = inner
        else:
            result[outer_key] = {}
    return result


def _nested_int_keyed_int_dict(value: object) -> dict[int, dict[int, int]]:
    source = _nested_int_keyed_dict(value)
    result: dict[int, dict[int, int]] = {}
    for outer_key, item in source.items():
        inner_result: dict[int, int] = {}
        for inner_key, inner_value in item.items():
            parsed = _maybe_int(inner_value)
            inner_result[inner_key] = 0 if parsed is None else parsed
        result[outer_key] = inner_result
    return result


def _int_keyed_bool_dict(value: object) -> dict[int, bool]:
    source = _int_keyed_dict(value)
    return {key: bool(item) for key, item in source.items()}


def _int_keyed_int_dict(value: object) -> dict[int, int]:
    source = _int_keyed_dict(value)
    result: dict[int, int] = {}
    for key, item in source.items():
        parsed = _maybe_int(item)
        if parsed is None:
            result[key] = 0
        else:
            result[key] = parsed
    return result


def _int_keyed_float_dict(value: object) -> dict[int, float]:
    source = _int_keyed_dict(value)
    result: dict[int, float] = {}
    for key, item in source.items():
        parsed = _maybe_float(item)
        if parsed is None:
            result[key] = 0.0
        else:
            result[key] = parsed
    return result


def _range_pair(value: object, *, default_min: int = 0, default_max: int = 0) -> tuple[int, int]:
    if isinstance(value, list) and len(value) == 2:
        first = _maybe_int(value[0])
        second = _maybe_int(value[1])
        if first is not None and second is not None:
            return first, second
    parsed = _maybe_int(value)
    if parsed is None:
        return default_min, default_max
    return parsed, parsed


def _coerce_damage_input(value: object, *, default: int = 0) -> int | list[int]:
    if isinstance(value, list) and len(value) == 2:
        min_value, max_value = _range_pair(value, default_min=default, default_max=default)
        return [min_value, max_value]
    parsed = _maybe_int(value)
    if parsed is None:
        return default
    return parsed


def _random_int_from_range(value: object, *, default: int = 0) -> int:
    min_value, max_value = _range_pair(value, default_min=default, default_max=default)
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    return random.randint(min_value, max_value)


def _effect_int(effect: dict[str, object], key: str, default: int = 0) -> int:
    return _maybe_int(effect.get(key, default)) or default


def _resolve_multi_hit_damage_details(
    multi_hit: dict[str, object],
    *,
    buff_amount: int,
    attack_multiplier: float,
    force_max: bool,
    guaranteed_hit: bool,
) -> tuple[int, int, int, dict[str, object]]:
    actual_damage, min_damage, max_damage, details = resolve_multi_hit_damage(
        multi_hit,
        buff_amount=buff_amount,
        attack_multiplier=attack_multiplier,
        force_max=force_max,
        guaranteed_hit=guaranteed_hit,
        return_details=True,
    )
    typed_details = details if isinstance(details, dict) else {}
    return actual_damage, min_damage, max_damage, typed_details


async def _invoke_command_callback(
    command: object,
    interaction: discord.Interaction,
) -> None:
    callback = getattr(command, "callback", None)
    if not callable(callback):
        raise TypeError("Command callback is not callable")
    typed_callback = cast(Callable[[discord.Interaction], Awaitable[object]], callback)
    await typed_callback(interaction)


def _env_flag_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "on", "yes", "y", "ja"}:
        return True
    if value in {"0", "false", "off", "no", "n", "nein"}:
        return False
    logging.warning("Invalid boolean env %s=%r, fallback default=%s", name, raw, default)
    return default


ALPHA_PHASE_ENABLED = _env_flag_enabled("ALPHA_PHASE", default=True)
ALPHA_HIDDEN_SLASH_COMMANDS = ("mission", "geschichte")
ALPHA_FEATURE_DISABLED_TEXT = "🧪 Alpha-Phase: Mission und Story sind aktuell deaktiviert."


class KatabumpCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.channel_id is None:
            return False
        guild_id = interaction.guild_id
        if guild_id is None:
            return False
        command_name = ""
        if interaction.command:
            command_name = getattr(interaction.command, "qualified_name", interaction.command.name)
        channel_allowed = await is_channel_allowed_ids(
            guild_id,
            interaction.channel_id,
            getattr(interaction.channel, "parent_id", None),
        )
        allow_channel_bypass = False
        if command_name == "kanal-freigeben":
            allow_channel_bypass = await is_config_admin(interaction)
        elif command_name == "konfigurieren hinzufuegen":
            allow_channel_bypass = await is_config_admin(interaction)
        if interaction.type == discord.InteractionType.autocomplete:
            return channel_allowed or allow_channel_bypass
        if not channel_allowed and not allow_channel_bypass:
            return False
        if await is_maintenance_enabled(guild_id):
            if not await is_owner_or_dev(interaction):
                if channel_allowed:
                    message = MAINTENANCE_ACTIVE
                    await send_interaction_response(interaction, content=message, ephemeral=True)
                return False
        now = time.monotonic()
        while _interaction_timestamps and now - _interaction_timestamps[0] > KATABUMP_INTERACTION_WINDOW_SEC:
            _interaction_timestamps.popleft()
        if len(_interaction_timestamps) >= KATABUMP_MAX_INTERACTIONS_PER_MIN:
            if channel_allowed:
                message = "⏳ Zu viele Anfragen. Bitte in einer Minute erneut versuchen (Katabump-Limit)."
                await send_interaction_response(interaction, content=message, ephemeral=True)
            return False
        _interaction_timestamps.append(now)
        return True


bot = build_bot(tree_cls=KatabumpCommandTree)

def create_bot() -> commands.Bot:
    return bot

ADMIN_SLASH_COMMANDS = {
    "konfigurieren",
    "intro-zuruecksetzen",
    "sammlung-ansehen",
    "test-bericht",
    "karte-geben",
    "op-verwaltung",
    "bot-status",
    "kanal-freigeben",
    "entwicklerpanel",
    "bot_log",
}

def prune_admin_slash_commands() -> None:
    removed = []
    for name in sorted(ADMIN_SLASH_COMMANDS):
        cmd = bot.tree.remove_command(name)
        if cmd is not None:
            removed.append(name)
    if removed:
        logging.info("Pruned admin/dev slash commands: %s", ", ".join(removed))


def prune_alpha_slash_commands() -> list[str]:
    if not ALPHA_PHASE_ENABLED:
        return []
    removed = []
    for name in ALPHA_HIDDEN_SLASH_COMMANDS:
        cmd = bot.tree.remove_command(name)
        if cmd is not None:
            removed.append(name)
    logging.info(
        "Alpha phase active, hidden slash commands: %s",
        ", ".join(removed) if removed else "none (already removed)",
    )
    return removed


# Rollen-IDs für Admin/Owner (vom Nutzer bestätigt)
BASTI_USER_ID = 965593518745731152
DEV_ROLE_ID = 1463304167421513961  # Bot_Developer/Tester role ID

MFU_ADMIN_ROLE_ID = 889559991437119498
OWNER_ROLE_ROLE_ID = 1272827906032402464

BUG_REPORT_TALLY_URL = os.getenv("BUG_REPORT_TALLY_URL", "https://tally.so/r/7RNo8z")
BOT_STATUS_KEY = "presence_status"
RESET_BUFFS_MIGRATION_KEY = "migration_reset_buffs_2026_02_21"
INVALID_DAMAGE_BUFFS_MIGRATION_KEY = "migration_remove_invalid_damage_buffs_2026_03_17"
MAX_ATTACK_DAMAGE_PER_HIT = 50
BOT_STATUS_MAP: dict[str, discord.Status] = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}
BOT_STATUS_LABELS: dict[str, str] = {
    "online": "Online",
    "idle": "Abwesend",
    "dnd": "Bitte nicht stören",
    "invisible": "Unsichtbar",
}


class EffectBestMoment(TypedDict):
    tag: str
    round: int
    actor: str
    event: str
    score: int


EFFECT_TYPES_WITH_EFFECT_LOGS = frozenset(
    {
        "absorb_store",
        "airborne_two_phase",
        "blind",
        "burning",
        "cap_damage",
        "damage_boost",
        "damage_multiplier",
        "damage_reduction_sequence",
        "delayed_defense_after_next_attack",
        "enemy_next_attack_reduction_flat",
        "enemy_next_attack_reduction_percent",
        "evade",
        "force_max",
        "guaranteed_hit",
        "mix_heal_or_max",
        "reflect",
        "regen",
        "special_lock",
        "stun",
    }
)


def berlin_midnight_epoch() -> int:
    """Gibt den Unix-Timestamp für den heutigen Tagesbeginn in Europe/Berlin zurück."""
    try:
        tz = ZoneInfo("Europe/Berlin")
        now = datetime.now(tz)
        today_start = datetime(now.year, now.month, now.day, tzinfo=tz)
        return int(today_start.timestamp())
    except Exception:
        # Fallback: lokales Mitternacht basierend auf Systemzeitzone
        tm = time.localtime()
        midnight_ts = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, tm.tm_wday, tm.tm_yday, tm.tm_isdst))
        return int(midnight_ts)

async def save_bot_presence_status(status_key: str) -> None:
    if status_key not in BOT_STATUS_MAP:
        return
    async with db_context() as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (BOT_STATUS_KEY, status_key),
        )
        await db.commit()

async def load_bot_presence_status() -> str | None:
    async with db_context() as db:
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (BOT_STATUS_KEY,))
        row = await cursor.fetchone()
    if not row:
        return None
    status_key = str(row[0]).strip().lower()
    if status_key not in BOT_STATUS_MAP:
        return None
    return status_key

async def restore_bot_presence_status() -> None:
    status_key = await load_bot_presence_status()
    if not status_key:
        return
    status = BOT_STATUS_MAP.get(status_key)
    if status is None:
        return
    try:
        await bot.change_presence(status=status)
        logging.info("Bot status restored from DB: %s", status_key)
    except Exception:
        logging.exception("Failed to restore bot status from DB")


async def run_one_time_migrations() -> None:
    reset_applied = False
    cleanup_pending = False
    async with db_context() as db:
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (RESET_BUFFS_MIGRATION_KEY,))
        row = await cursor.fetchone()
        if not row:
            await db.execute("DELETE FROM user_card_buffs")
            await db.execute(
                "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (RESET_BUFFS_MIGRATION_KEY, str(int(time.time()))),
            )
            reset_applied = True
        cursor = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (INVALID_DAMAGE_BUFFS_MIGRATION_KEY,))
        row = await cursor.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (INVALID_DAMAGE_BUFFS_MIGRATION_KEY, str(int(time.time()))),
            )
            cleanup_pending = True
        await db.commit()
    if reset_applied:
        logging.info("Applied migration: reset user_card_buffs and stored %s", RESET_BUFFS_MIGRATION_KEY)
    if cleanup_pending:
        removed_count = await remove_invalid_damage_card_buffs()
        logging.info(
            "Applied migration: removed %s invalid damage buffs and stored %s",
            removed_count,
            INVALID_DAMAGE_BUFFS_MIGRATION_KEY,
        )


def build_anfang_intro_text() -> str:
    text = (
        "# **Rekrut.**\n\n"
        "Hör gut zu. Ich bin Nick Fury, und wenn du Teil von etwas Größerem sein willst, bist du hier richtig. Willkommen auf dem Helicarrier. Wir haben alle Hände voll zu tun, und ich hoffe, du bist bereit, dir die Hände schmutzig zu machen.\n\n"
        "Du willst wissen, wie du an die guten Sachen kommst? Täglich hast du die Chance, eine zufällige Karte aus dem Pool zu ziehen `[/täglich im Chat schreiben]`. Und wenn du eine doppelte Karte ziehst, verschwindet sie nicht einfach. Sie wird zu Staub umgewandelt. Sammle genug davon, um deine Karten zu verbessern und sie so noch mächtiger zu machen `[/verbessern im Chat schreiben]`.\n\n"
        "Du bist neu hier und brauchst Training? Auf dem Helicarrier kannst du dich mit anderen anlegen und üben, bis deine Strategien sitzen `[/kampf im Chat schreiben]`.\n\n"
    )
    text += (
        "Wenn du bereit für den echten Einsatz bist, stehen dir jeden Tag zwei Missionen zur Verfügung. Schließe sie ab und ich garantiere dir, du bekommst jeweils eine Karte als Belohnung `[/mission im Chat schreiben]`.\n\n"
        "Für die Verrückten da draußen, die meinen, sie wären unschlagbar: Es gibt den Story-Modus. Du hast drei Leben, um die gesamte Geschichte zu überleben. Schaffst du das, wartet eine mysteriöse Belohnung auf dich `[/geschichte im Chat schreiben]`.\n\n"
    )
    text += "**Also los jetzt. Sag mir, was du tun willst. Wir haben keine Zeit zu verlieren.**"
    return text


async def _send_alpha_feature_blocked(interaction: discord.Interaction) -> None:
    await _send_ephemeral(interaction, content=ALPHA_FEATURE_DISABLED_TEXT)


def _maybe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _maybe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _format_amount_for_label(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) == 2:
        min_value = _maybe_int(value[0])
        max_value = _maybe_int(value[1])
        if min_value is None or max_value is None:
            return None
        return f"{min_value}-{max_value}"
    parsed = _maybe_int(value)
    if parsed is None:
        return None
    return str(parsed)

def _heal_label_for_attack(attack: dict) -> str | None:
    heal_data = attack.get("heal")
    heal_text = _format_amount_for_label(heal_data)
    if heal_text:
        return heal_text

    lifesteal_ratio = attack.get("lifesteal_ratio")
    if lifesteal_ratio:
        try:
            pct = int(round(float(lifesteal_ratio) * 100))
        except Exception:
            pct = 0
        if pct > 0:
            return f"{pct}% LS"

    for effect in attack.get("effects", []):
        effect_type = effect.get("type")
        if effect_type == "regen":
            regen_text = _format_amount_for_label(effect.get("heal"))
            turns = max(1, _maybe_int(effect.get("turns", 1) or 1) or 1)
            if regen_text:
                return f"{regen_text}x{turns}" if turns > 1 else regen_text
        elif effect_type == "heal":
            direct_heal = _format_amount_for_label(effect.get("amount"))
            if direct_heal:
                return direct_heal
        elif effect_type == "mix_heal_or_max":
            mix_heal = _format_amount_for_label(effect.get("heal"))
            if mix_heal:
                return f"{mix_heal}/MAX"
            return "MAX"
    return None


def _attack_has_heal_component(attack: dict) -> bool:
    heal_data = attack.get("heal")
    if isinstance(heal_data, list) and len(heal_data) == 2:
        if (_maybe_int(heal_data[1]) or 0) > 0:
            return True
    elif isinstance(heal_data, (int, float)) and int(heal_data) > 0:
        return True
    if float(attack.get("lifesteal_ratio", 0.0) or 0.0) > 0:
        return True
    for effect in attack.get("effects", []):
        eff_type = str(effect.get("type") or "").strip().lower()
        if eff_type in {"regen", "heal", "mix_heal_or_max"}:
            return True
    return False


def _attack_is_damage_upgradeable(attack: dict) -> bool:
    if _attack_has_heal_component(attack):
        return False
    if attack.get("effects"):
        return False
    if int(attack.get("self_damage", 0) or 0) > 0:
        return False
    max_damage = 0
    multi_hit = attack.get("multi_hit")
    if isinstance(multi_hit, dict):
        per_hit = multi_hit.get("per_hit_damage")
        if isinstance(per_hit, list) and len(per_hit) == 2:
            max_damage = max(max_damage, int(_maybe_int(per_hit[1]) or 0))
    _base_min_damage, base_max_damage = _damage_range_with_max_bonus(
        attack.get("damage", [0, 0]),
        max_only_bonus=0,
        flat_bonus=0,
    )
    max_damage = max(max_damage, int(base_max_damage))
    return max_damage > 0


def _damage_range_with_max_bonus(base_damage, *, max_only_bonus: int = 0, flat_bonus: int = 0) -> tuple[int, int]:
    if isinstance(base_damage, list) and len(base_damage) == 2:
        base_min = _maybe_int(base_damage[0]) or 0
        base_max = _maybe_int(base_damage[1]) or 0
    else:
        base_value = _maybe_int(base_damage or 0) or 0
        base_min = base_value
        base_max = base_value
    base_min = max(0, base_min + int(flat_bonus))
    base_max = max(base_min, int(base_max) + int(flat_bonus) + max(0, int(max_only_bonus)))
    return base_min, base_max


def _apply_max_only_damage_bonus(base_damage, max_only_bonus: int):
    bonus = max(0, int(max_only_bonus or 0))
    if bonus <= 0:
        return base_damage
    min_dmg, max_dmg = _damage_range_with_max_bonus(base_damage, max_only_bonus=bonus, flat_bonus=0)
    return [min_dmg, max_dmg]


def _damage_text_for_attack(attack: dict) -> str:
    dmg = attack.get("damage")
    if isinstance(dmg, list) and len(dmg) == 2:
        min_damage = _maybe_int(dmg[0])
        max_damage = _maybe_int(dmg[1])
        if min_damage is not None and max_damage is not None:
            return f"{min_damage}-{max_damage}"
        return "0"
    parsed = _maybe_int(dmg or 0)
    if parsed is None:
        return "0"
    return str(parsed)


def _format_cooldown_label(attack: dict, remaining_turns: int) -> str:
    remaining = max(0, int(remaining_turns or 0))
    try:
        base = int(attack.get("cooldown_turns", 0) or 0)
    except Exception:
        base = 0
    if base > 0:
        return f"Cooldown: {remaining}/{base}"
    return f"Cooldown: {remaining}"


_ATTACK_BUTTON_STYLE_MAP: dict[str, discord.ButtonStyle] = {
    "danger": discord.ButtonStyle.danger,
    "red": discord.ButtonStyle.danger,
    "rot": discord.ButtonStyle.danger,
    "success": discord.ButtonStyle.success,
    "green": discord.ButtonStyle.success,
    "gruen": discord.ButtonStyle.success,
    "grün": discord.ButtonStyle.success,
    "primary": discord.ButtonStyle.primary,
    "blue": discord.ButtonStyle.primary,
    "blau": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "gray": discord.ButtonStyle.secondary,
    "grey": discord.ButtonStyle.secondary,
    "grau": discord.ButtonStyle.secondary,
}


def _resolve_attack_button_style(attack: dict, default_style: discord.ButtonStyle) -> discord.ButtonStyle:
    raw_value = attack.get("button_style")
    if raw_value is None:
        raw_value = attack.get("button_color")
    key = str(raw_value or "").strip().lower()
    if not key:
        return default_style
    return _ATTACK_BUTTON_STYLE_MAP.get(key, default_style)


def _build_attack_info_lines(card: dict, *, max_attacks: int = 4) -> list[str]:
    lines: list[str] = []
    attacks = card.get("attacks", [])
    for attack in attacks[:max_attacks]:
        attack_name = str(attack.get("name", "Attacke"))
        damage_text = _damage_text_for_attack(attack)
        info_text = str(attack.get("info") or "").strip()
        if info_text:
            lines.append(f"• {attack_name} ({damage_text}): {info_text}")
        else:
            lines.append(f"• {attack_name} ({damage_text})")
    return lines


def _add_attack_info_field(embed: discord.Embed, card: dict, *, field_name: str = "Fähigkeiten") -> None:
    lines = _build_attack_info_lines(card)
    if not lines:
        return
    value = "\n".join(lines)
    if len(value) > 1024:
        value = value[:1021] + "..."
    embed.add_field(name=field_name, value=value, inline=False)


def _starts_cooldown_after_landing(attack: dict) -> bool:
    for effect in attack.get("effects", []):
        if str(effect.get("type") or "").strip().lower() == "airborne_two_phase":
            return True
    return False


def _resolve_dynamic_cooldown_from_burning(attack: dict, applied_burning_duration: int | None) -> int:
    if applied_burning_duration is None:
        return 0
    bonus_raw = attack.get("cooldown_from_burning_plus")
    if bonus_raw is None:
        return 0
    try:
        bonus = max(0, int(bonus_raw))
    except Exception:
        return 0
    duration = max(0, int(applied_burning_duration))
    if duration <= 0:
        return 0
    return duration + bonus


async def _safe_defer_interaction(interaction: discord.Interaction) -> bool:
    return await defer_interaction(interaction)


async def _safe_send_interaction_ephemeral(interaction: discord.Interaction, content: str) -> object | None:
    if interaction.response.is_done() and not hasattr(interaction, "followup"):
        try:
            return await interaction.response.send_message(content, ephemeral=True)
        except Exception:
            logging.exception("Failed to send deferred interaction message without followup")
            return None
    return await send_interaction_response(interaction, content=content, ephemeral=True)


def _boosted_damage_effect_text(boosted_damage: int, attack_multiplier: float, flat_bonus: int) -> str | None:
    boosted = int(boosted_damage or 0)
    if boosted <= 0:
        return None
    multiplier = float(attack_multiplier or 1.0)
    flat = max(0, int(flat_bonus or 0))
    if flat <= 0 and abs(multiplier - 1.0) < 1e-9:
        return None
    if multiplier <= 0:
        multiplier = 1.0
    approx_after_flat = int(round(boosted / multiplier))
    normal_damage = max(0, approx_after_flat - flat)
    bonus = boosted - normal_damage
    if bonus <= 0:
        return None
    return f"Normal: {normal_damage} | durch Verstärkung: {boosted} (+{bonus})"

# Volltreffer-System Funktionen
@bot.event
async def on_ready():
    if bot.user is not None and not bot.user.bot:
        logging.error("Self-bot erkannt. Bot-Tokens sind erforderlich. Shutdown.")
        await bot.close()
        return
    await init_db()
    await run_one_time_migrations()
    await restore_bot_presence_status()
    logging.info("Bot ist online als %s", bot.user)
    prune_alpha_slash_commands()
    try:
        synced = await bot.tree.sync()
        logging.info("Slash-Commands synchronisiert: %s", len(synced))
    except Exception:
        logging.exception("Slash-Command sync failed")
    global _persistent_views_registered
    if not _persistent_views_registered:
        try:
            bot.add_view(AnfangView())
            if ALPHA_PHASE_ENABLED:
                bot.add_view(AlphaPhaseLegacyAnfangView())
            _persistent_views_registered = True
        except Exception:
            logging.exception("Failed to register persistent views")
    try:
        await _restore_durable_views()
    except Exception:
        logging.exception("Failed to restore durable views on startup")
    try:
        await resend_pending_requests()
    except Exception:
        logging.exception("Failed to resend pending requests on startup")

# Event: On Message – bei erster Nachricht im Kanal Intro zeigen (ephemeral)
@bot.event
async def on_message(message: discord.Message):
    # Ignoriere Bot-Nachrichten
    if message.author.bot:
        return
    # Nur in Guilds relevant
    if not message.guild:
        return
    # Wartungsmodus: Nur Owner/Dev reagieren lassen
    if await is_maintenance_enabled(message.guild.id):
        if not is_owner_or_dev_member(message.author):
            return
    if not await is_channel_allowed_ids(message.guild.id, message.channel.id, getattr(message.channel, "parent_id", None)):
        return
    if isinstance(message.channel, discord.Thread) and await is_managed_thread(message.channel.id):
        try:
            session = await get_active_session_for_channel(
                message.channel.id,
                kinds=("fight_pvp", "fight_bot"),
            )
        except Exception:
            logging.exception("Failed to load active fight session for managed thread %s", message.channel.id)
            session = None
        if session is not None and str(session.get("status") or "") == "active":
            payload = _dict_str_any(session.get("payload"))
            if not bool(payload.get("ui_needs_resend")):
                try:
                    await patch_active_session_payload(
                        int(session.get("session_id") or 0),
                        {
                            "ui_needs_resend": True,
                            "ui_needs_resend_message_id": int(message.id),
                        },
                    )
                except Exception:
                    logging.exception("Failed to patch managed fight session %s", session.get("session_id"))

    # Commands weiter verarbeiten lassen
    await bot.process_commands(message)

# Kanal-Restriktion: Only respond in configured channel
async def is_channel_allowed(interaction: discord.Interaction, *, bypass_maintenance: bool = False) -> bool:
    if interaction.guild is None or interaction.channel_id is None:
        return False
    guild_id = interaction.guild_id
    if guild_id is None:
        return False
    parent_id = getattr(interaction.channel, "parent_id", None)
    if not await is_channel_allowed_ids(guild_id, interaction.channel_id, parent_id):
        return False
    if not bypass_maintenance and await is_maintenance_enabled(guild_id):
        if not await is_owner_or_dev(interaction):
            message = MAINTENANCE_ACTIVE
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await interaction.followup.send(message, ephemeral=True)
            return False
    return True

# Kanal-Check ohne Nachrichten-Seiteneffekte (für on_message)
async def is_channel_allowed_ids(
    guild_id: int | None,
    channel_id: int | None,
    parent_channel_id: int | None = None,
) -> bool:
    if not guild_id or not channel_id:
        return False
    async with db_context() as db:
        cursor = await db.execute("SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?", (guild_id,))
        allowed_channels = {r[0] for r in await cursor.fetchall()}
    if not allowed_channels:
        return False
    if channel_id in allowed_channels:
        return True
    if parent_channel_id and parent_channel_id in allowed_channels:
        return True
    return False

class RestrictedView(BaseRestrictedView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, interaction_checker=is_channel_allowed, **kwargs)


class RestrictedModal(BaseRestrictedModal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, interaction_checker=is_channel_allowed, **kwargs)


class DurableView(RestrictedView):
    durable_view_kind = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._durable_guild_id: int | None = None
        self._durable_channel_id: int | None = None
        self._durable_message_id: int | None = None

    def durable_payload(self) -> dict[str, Any]:
        return {}

    def durable_log_text(self) -> str:
        return ""

    def durable_context_label(self) -> str:
        return self.durable_view_kind or self.__class__.__name__

    def bind_durable_message(self, *, guild_id: int | None, channel_id: int | None, message_id: int | None) -> None:
        self._durable_guild_id = guild_id
        self._durable_channel_id = channel_id
        self._durable_message_id = message_id

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Any]) -> None:
        logging.exception("Durable view callback failed (%s)", self.durable_context_label(), exc_info=error)
        await _handle_durable_view_error(
            interaction,
            error,
            view=self,
            view_label=self.durable_context_label(),
            battle_log_text=self.durable_log_text(),
        )


def _build_mission_embed(mission_data: dict) -> discord.Embed:
    title = mission_data.get("title") or "Mission"
    description = mission_data.get("description") or "Hier kommt später die Story. Hier kommt später die Story."
    reward_card = mission_data.get("reward_card") or {}
    waves = mission_data.get("waves", 0)
    embed = discord.Embed(title=title, description=description, color=_card_rarity_color(reward_card))
    embed.add_field(name="Wellen", value=f"{waves}", inline=True)
    if reward_card:
        embed.add_field(name="🎁 Belohnung", value=f"**{reward_card.get('name', '?')}**", inline=True)
        if reward_card.get("bild"):
            embed.set_thumbnail(url=reward_card["bild"])
    return embed

# Hilfsfunktion: Karte nach Namen finden
async def get_karte_by_name(name: str) -> dict[str, Any] | None:
    for karte in karten:
        card_name = str(karte.get("name") or "").lower()
        if card_name == name.lower():
            return karte
    return None

def _sort_user_cards_like_karten(user_cards) -> list[tuple[str, int]]:
    """Sort user-owned cards by the order in karten.py, unknown cards last."""
    order_map = {
        str(card.get("name", "")).strip().lower(): idx
        for idx, card in enumerate(karten)
    }
    normalized: list[tuple[str, int]] = []
    for row in user_cards or []:
        try:
            name = str(row[0])
            amount = int(row[1])
        except Exception:
            continue
        normalized.append((name, amount))

    def _key(item: tuple[str, int]) -> tuple[int, str]:
        name = str(item[0]).strip()
        idx = order_map.get(name.lower(), 10**9)
        return idx, name.lower()

    return sorted(normalized, key=_key)

# View für Buttons beim Kartenziehen
class ZieheKarteView(RestrictedView):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id

    @ui.button(label="Noch eine Karte ziehen", style=discord.ButtonStyle.primary)
    async def ziehe_karte(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Button!", ephemeral=True)
            return
        karte = random.choice(karten)
        await add_karte(interaction.user.id, karte["name"])
        embed = discord.Embed(
            title=karte["name"],
            description=karte["beschreibung"],
            color=_card_rarity_color(karte),
        )
        embed.set_image(url=karte["bild"])
        await interaction.response.send_message(embed=embed, view=ZieheKarteView(self.user_id))

# View für Missions-Buttons
class MissionView(RestrictedView):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id

    @ui.button(label="Noch eine Mission starten", style=discord.ButtonStyle.success)
    async def neue_mission(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Button!", ephemeral=True)
            return
        karte, is_new_card = await add_mission_reward(self.user_id)
        
        if is_new_card:
            embed = discord.Embed(
                title="Mission abgeschlossen!",
                description=f"Du hast **{karte['name']}** erhalten!",
                color=_card_rarity_color(karte),
            )
            embed.set_image(url=karte["bild"])
            await interaction.response.send_message(embed=embed, view=MissionView(self.user_id))
        else:
            # Karte wurde zu Infinitydust umgewandelt
            embed = discord.Embed(
                title="💎 Mission abgeschlossen - Infinitydust!",
                description=f"Du hattest **{karte['name']}** bereits!",
                color=_card_rarity_color(karte),
            )
            embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
            embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
            await interaction.response.send_message(embed=embed, ephemeral=True)

# View für HP-Button (über der Karte)
class HPView(RestrictedView):
    def __init__(self, player_card, player_hp):
        super().__init__(timeout=120)
        self.player_card = player_card
        self.player_hp = player_hp
        self.hp_hearts = "❤️" * (self.player_hp // 20) + "🖤" * (5 - self.player_hp // 20)

    @ui.button(label="❤️❤️❤️❤️❤️", style=discord.ButtonStyle.success)
    async def hp_display(self, interaction: discord.Interaction, button: ui.Button):
        # HP-Button zeigt nur HP an, keine Aktion
        await interaction.response.send_message(f"**{self.player_card['name']}** HP: {self.player_hp}/100", ephemeral=True)

    def update_hp(self, new_hp):
        """Aktualisiert die HP-Anzeige"""
        self.player_hp = new_hp
        self.hp_hearts = "❤️" * (self.player_hp // 20) + "🖤" * (5 - self.player_hp // 20)
        for child in self.children:
            if isinstance(child, ui.Button):
                child.label = self.hp_hearts
                break

# View für Kampf-Buttons (unter der Karte)
class BattleView(DurableView):
    durable_view_kind = VIEW_KIND_BATTLE

    def __init__(
        self,
        player1_card: CardData,
        player2_card: CardData,
        player1_id: int,
        player2_id: int,
        hp_view: SupportsUpdateHp | None,
        public_result_channel_id: int | None = None,
    ):
        super().__init__(timeout=None)
        self.player1_card = player1_card
        self.player2_card = player2_card
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.current_turn = player1_id
        self.public_result_channel_id = int(public_result_channel_id) if public_result_channel_id else None
        self.session_id: int | None = None

        base_hp1 = player1_card.get("hp", 100)
        base_hp2 = player2_card.get("hp", 100)
        self._hp_by_player = {
            self.player1_id: int(base_hp1),
            self.player2_id: int(base_hp2),
        }
        self._max_hp_by_player = {
            self.player1_id: int(base_hp1),
            self.player2_id: int(base_hp2),
        }
        self._card_names_by_player = {
            self.player1_id: str(player1_card.get("name") or "Spieler"),
            self.player2_id: str(player2_card.get("name") or ("Bot" if player2_id == 0 else "Gegner")),
        }
        self.hp_view = hp_view
        runtime_maps = battle_state.build_battle_runtime_maps((player1_id, player2_id))

        self.attack_cooldowns = runtime_maps["cooldowns_by_player"]
        self.battle_log_message: discord.Message | None = None
        self._full_battle_log_embed = create_battle_log_embed()
        self._all_battle_log_entries: list[str] = []
        self._all_battle_log_summaries: list[str] = []
        self._recent_log_lines: list[str] = []
        self._last_highlight_tone: str = "hit"
        self._biggest_hit = 0
        self._critical_hits = 0
        self._effect_tag_counts: dict[str, int] = {}
        self._effect_best_moments: dict[str, EffectBestMoment] = {}
        self._effect_event_history: list[EffectBestMoment] = []
        self.round_counter = 0
        self._last_log_edit_ts = 0.0
        self.ui_needs_resend = False

        self.active_effects = runtime_maps["active_effects"]
        self.confused_next_turn = runtime_maps["confused_next_turn"]
        self.manual_reload_needed = runtime_maps["manual_reload_needed"]
        self.stunned_next_turn = runtime_maps["stunned_next_turn"]
        self.special_lock_next_turn = runtime_maps["special_lock_next_turn"]
        self.blind_next_attack = runtime_maps["blind_next_attack"]
        self.pending_flat_bonus = runtime_maps["pending_flat_bonus"]
        self.pending_flat_bonus_uses = runtime_maps["pending_flat_bonus_uses"]
        self.pending_multiplier = runtime_maps["pending_multiplier"]
        self.pending_multiplier_uses = runtime_maps["pending_multiplier_uses"]
        self.force_max_next = runtime_maps["force_max_next"]
        self.guaranteed_hit_next = runtime_maps["guaranteed_hit_next"]
        self.incoming_modifiers = runtime_maps["incoming_modifiers"]
        self.outgoing_attack_modifiers = runtime_maps["outgoing_attack_modifiers"]
        self.absorbed_damage = runtime_maps["absorbed_damage"]
        self.delayed_defense_queue = runtime_maps["delayed_defense_queue"]
        self.airborne_pending_landing = runtime_maps["airborne_pending_landing"]
        self._last_damage_roll_meta: dict | None = None

    def durable_payload(self) -> dict[str, Any]:
        return {"session_id": self.session_id} if self.session_id else {}

    def durable_log_text(self) -> str:
        return self._full_battle_log_text()

    @property
    def player1_hp(self) -> int:
        return int(self._hp_by_player[self.player1_id])

    @player1_hp.setter
    def player1_hp(self, value: int) -> None:
        self._hp_by_player[self.player1_id] = max(0, int(value))

    @property
    def player2_hp(self) -> int:
        return int(self._hp_by_player[self.player2_id])

    @player2_hp.setter
    def player2_hp(self, value: int) -> None:
        self._hp_by_player[self.player2_id] = max(0, int(value))

    @property
    def player1_max_hp(self) -> int:
        return int(self._max_hp_by_player[self.player1_id])

    @player1_max_hp.setter
    def player1_max_hp(self, value: int) -> None:
        self._max_hp_by_player[self.player1_id] = max(0, int(value))

    @property
    def player2_max_hp(self) -> int:
        return int(self._max_hp_by_player[self.player2_id])

    @player2_max_hp.setter
    def player2_max_hp(self, value: int) -> None:
        self._max_hp_by_player[self.player2_id] = max(0, int(value))

    def set_confusion(self, player_id: int, applier_id: int) -> None:
        battle_state.set_confusion(self.active_effects, self.confused_next_turn, player_id, applier_id)

    def consume_confusion_if_any(self, player_id: int) -> None:
        battle_state.consume_confusion_if_any(self.active_effects, self.confused_next_turn, player_id)

    def is_reload_needed(self, player_id: int, attack_index: int) -> bool:
        return battle_state.is_reload_needed(self.manual_reload_needed, player_id, attack_index)

    def set_reload_needed(self, player_id: int, attack_index: int, needed: bool) -> None:
        battle_state.set_reload_needed(self.manual_reload_needed, player_id, attack_index, needed)

    def _find_effect(self, player_id: int, effect_type: str):
        return battle_state.find_effect(self.active_effects, player_id, effect_type)

    def has_stealth(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "stealth")

    def has_airborne(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "airborne")

    def consume_stealth(self, player_id: int) -> bool:
        return battle_state.consume_effect(self.active_effects, player_id, "stealth")

    def grant_stealth(self, player_id: int) -> None:
        battle_state.grant_unique_effect(self.active_effects, player_id, "stealth", player_id, duration=1)

    def _append_effect_event(self, events: list[str], text: str) -> None:
        battle_state.append_effect_event(events, text)

    def _effect_tag_for_event(self, text: str) -> str | None:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return None
        if "heil" in normalized or "regeneration" in normalized or "lebensraub" in normalized:
            return "Heilung"
        if "verbrennung" in normalized or "brennen" in normalized:
            return "Verbrennung"
        if "betäub" in normalized or "stun" in normalized:
            return "Betäubung"
        if (
            "ausweich" in normalized
            or "tarn" in normalized
            or "schutz" in normalized
            or "reflexion" in normalized
            or "spiegeldimension" in normalized
            or "absorption" in normalized
            or "konter" in normalized
        ):
            return "Schutz"
        if "bonus" in normalized or "verstärkung" in normalized or "maximalschaden" in normalized or "garantiert" in normalized:
            return "Buffs"
        return None

    @staticmethod
    def _effect_score_for_event(text: str) -> int:
        numbers = [int(m) for m in re.findall(r"\d+", str(text or ""))]
        if numbers:
            return max(numbers)
        return 1

    @staticmethod
    def _effect_actor_for_event(default_actor: str, event_text: str) -> str:
        text = str(event_text or "").strip()
        match = re.search(r"\bdurch\s+([^:,.|]+)", text, flags=re.IGNORECASE)
        if match:
            actor = str(match.group(1) or "").strip()
            if actor:
                return actor
        actor = str(default_actor or "").strip()
        return actor or "Bot"

    def _update_highlight_stats(
        self,
        actual_damage: int,
        is_critical: bool,
        round_number: int,
        attacker_display: str,
        effect_events: list[str] | None = None,
    ) -> None:
        self._biggest_hit = max(self._biggest_hit, max(0, int(actual_damage or 0)))
        if is_critical:
            self._critical_hits += 1
        for entry in effect_events or []:
            text = str(entry or "").strip()
            tag = self._effect_tag_for_event(text)
            if not tag:
                continue
            self._effect_tag_counts[tag] = int(self._effect_tag_counts.get(tag, 0) or 0) + 1
            score = self._effect_score_for_event(text)
            actor = self._effect_actor_for_event(attacker_display, text)
            record: EffectBestMoment = {
                "tag": tag,
                "round": int(round_number),
                "actor": actor,
                "event": text,
                "score": int(score),
            }
            self._effect_event_history.append(record)
            prev = self._effect_best_moments.get(tag)
            if (
                prev is None
                or int(record["score"]) > int(prev["score"])
                or (int(record["score"]) == int(prev["score"]) and int(record["round"]) < int(prev["round"]))
            ):
                self._effect_best_moments[tag] = record

    def _resolve_highlight_tone(self, is_critical: bool, effect_events: list[str] | None = None) -> str:
        if is_critical:
            return "crit"
        for entry in effect_events or []:
            text = str(entry or "").lower()
            if "heil" in text or "regeneration" in text or "lebensraub" in text:
                return "heal"
        for entry in effect_events or []:
            text = str(entry or "").lower()
            if "bonus" in text or "verstärkung" in text or "maximalschaden" in text or "garantiert" in text:
                return "buff"
        return "hit"

    def _recent_log_preview_from_embed(self) -> list[str]:
        return list(self._all_battle_log_summaries[-2:])

    async def _record_battle_log(
        self,
        attacker_name,
        defender_name,
        attack_name,
        actual_damage,
        is_critical,
        attacker_user,
        defender_user,
        round_number,
        defender_remaining_hp,
        attacker_remaining_hp: int | None = None,
        *,
        pre_effect_damage: int = 0,
        confusion_applied: bool = False,
        self_hit_damage: int = 0,
        attacker_status_icons: str = "",
        defender_status_icons: str = "",
        effect_events: list[str] | None = None,
    ) -> None:
        effective_critical = bool(is_critical and int(actual_damage or 0) > 0)
        entry_text, summary_line = build_battle_log_entry(
            attacker_name,
            defender_name,
            attack_name,
            actual_damage,
            effective_critical,
            attacker_user,
            defender_user,
            round_number,
            defender_remaining_hp,
            attacker_remaining_hp=attacker_remaining_hp,
            pre_effect_damage=pre_effect_damage,
            confusion_applied=confusion_applied,
            self_hit_damage=self_hit_damage,
            attacker_status_icons=attacker_status_icons,
            defender_status_icons=defender_status_icons,
            effect_events=effect_events,
        )
        self._all_battle_log_entries.append(entry_text)
        self._all_battle_log_summaries.append(summary_line)
        self._full_battle_log_embed = update_battle_log(
            self._full_battle_log_embed,
            attacker_name,
            defender_name,
            attack_name,
            actual_damage,
            effective_critical,
            attacker_user,
            defender_user,
            round_number,
            defender_remaining_hp,
            attacker_remaining_hp=attacker_remaining_hp,
            pre_effect_damage=pre_effect_damage,
            confusion_applied=confusion_applied,
            self_hit_damage=self_hit_damage,
            attacker_status_icons=attacker_status_icons,
            defender_status_icons=defender_status_icons,
            effect_events=effect_events,
            max_rounds=4,
        )
        self._recent_log_lines = self._recent_log_preview_from_embed()
        self._last_highlight_tone = self._resolve_highlight_tone(effective_critical, effect_events)
        attacker_display = safe_display_name(attacker_user, fallback="Bot")
        self._update_highlight_stats(
            actual_damage,
            effective_critical,
            round_number,
            attacker_display,
            effect_events,
        )
        if self.battle_log_message and not self.ui_needs_resend:
            await self._safe_edit_battle_log(self._full_battle_log_embed)

    def _winner_embed(
        self,
        winner_mention: str,
        winner_card: str,
        loser_mention: str | None = None,
        loser_card: str | None = None,
    ) -> discord.Embed:
        result_sections = [
            f"\U0001f3c6 **Gewinner**\n{winner_mention} hat mit {winner_card} gewonnen.",
        ]
        if loser_mention and loser_card:
            result_sections.append(f"\U0001f4a5 **Verlierer**\n{loser_mention} hat mit {loser_card} verloren.")
        embed = discord.Embed(
            title="\u2694\ufe0f Kampfergebnis",
            description="\n\n".join(result_sections),
        )
        stats_lines = [
            f"Runden: {self.round_counter}",
            f"Größter Treffer: {self._biggest_hit}",
            f"Kritische Treffer: {self._critical_hits}",
        ]
        embed.add_field(name="Kampfstatistik", value="\n".join(stats_lines), inline=False)
        if self._effect_tag_counts:
            top = sorted(self._effect_tag_counts.items(), key=lambda x: (-x[1], x[0]))[:3]
            lines: list[str] = []
            for name, count in top:
                base = f"{name}: {count}x"
                best = self._effect_best_moments.get(name)
                if best:
                    event_text = str(best.get("event", "") or "").strip()
                    if len(event_text) > 80:
                        event_text = event_text[:77] + "..."
                    base += (
                        f" | Runde {int(best.get('round', 0) or 0)}"
                        f" · {str(best.get('actor', 'Unbekannt') or 'Unbekannt')}"
                    )
                    if event_text:
                        base += f" · {event_text}"
                lines.append(base)
            top_text = "\n".join(lines)
        else:
            top_text = "Keine markanten Effekte."
        if len(top_text) > 1024:
            top_text = top_text[:1021] + "..."
        embed.add_field(name="Top-Effekte", value=top_text, inline=False)
        return embed

    def _full_battle_log_text(self) -> str:
        if not self._all_battle_log_entries:
            return "*Der Kampf beginnt...*"
        return "*Der Kampf beginnt...*" + "".join(self._all_battle_log_entries)

    def serialize_session_payload(self) -> dict[str, Any]:
        return {
            "player1_card": _json_clone(self.player1_card),
            "player2_card": _json_clone(self.player2_card),
            "player1_id": self.player1_id,
            "player2_id": self.player2_id,
            "public_result_channel_id": self.public_result_channel_id,
            "current_turn": self.current_turn,
            "hp_by_player": _json_clone(self._hp_by_player),
            "max_hp_by_player": _json_clone(self._max_hp_by_player),
            "card_names_by_player": _json_clone(self._card_names_by_player),
            "attack_cooldowns": _json_clone(self.attack_cooldowns),
            "active_effects": _json_clone(self.active_effects),
            "confused_next_turn": _json_clone(self.confused_next_turn),
            "manual_reload_needed": _json_clone(self.manual_reload_needed),
            "stunned_next_turn": _json_clone(self.stunned_next_turn),
            "special_lock_next_turn": _json_clone(self.special_lock_next_turn),
            "blind_next_attack": _json_clone(self.blind_next_attack),
            "pending_flat_bonus": _json_clone(self.pending_flat_bonus),
            "pending_flat_bonus_uses": _json_clone(self.pending_flat_bonus_uses),
            "pending_multiplier": _json_clone(self.pending_multiplier),
            "pending_multiplier_uses": _json_clone(self.pending_multiplier_uses),
            "force_max_next": _json_clone(self.force_max_next),
            "guaranteed_hit_next": _json_clone(self.guaranteed_hit_next),
            "incoming_modifiers": _json_clone(self.incoming_modifiers),
            "outgoing_attack_modifiers": _json_clone(self.outgoing_attack_modifiers),
            "absorbed_damage": _json_clone(self.absorbed_damage),
            "delayed_defense_queue": _json_clone(self.delayed_defense_queue),
            "airborne_pending_landing": _json_clone(self.airborne_pending_landing),
            "all_battle_log_entries": _json_clone(self._all_battle_log_entries),
            "all_battle_log_summaries": _json_clone(self._all_battle_log_summaries),
            "recent_log_lines": _json_clone(self._recent_log_lines),
            "last_highlight_tone": self._last_highlight_tone,
            "biggest_hit": self._biggest_hit,
            "critical_hits": self._critical_hits,
            "effect_tag_counts": _json_clone(self._effect_tag_counts),
            "effect_best_moments": _json_clone(self._effect_best_moments),
            "effect_event_history": _json_clone(self._effect_event_history),
            "round_counter": self.round_counter,
            "ui_needs_resend": self.ui_needs_resend,
        }

    def restore_from_session_payload(self, payload: dict[str, Any]) -> None:
        player1_card = _dict_str_any(payload.get("player1_card"))
        player2_card = _dict_str_any(payload.get("player2_card"))
        if player1_card:
            self.player1_card = cast(CardData, player1_card)
        if player2_card:
            self.player2_card = cast(CardData, player2_card)
        self.current_turn = int(payload.get("current_turn", self.current_turn) or self.current_turn)
        self.public_result_channel_id = int(payload.get("public_result_channel_id", 0) or 0) or None
        self._hp_by_player = _int_keyed_int_dict(payload.get("hp_by_player"))
        self._max_hp_by_player = _int_keyed_int_dict(payload.get("max_hp_by_player"))
        raw_card_names = _int_keyed_dict(payload.get("card_names_by_player"))
        self._card_names_by_player = {key: str(value or "") for key, value in raw_card_names.items()}
        self.attack_cooldowns = _nested_int_keyed_int_dict(payload.get("attack_cooldowns"))
        self.active_effects = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("active_effects")).items()}
        self.confused_next_turn = _int_keyed_bool_dict(payload.get("confused_next_turn"))
        self.manual_reload_needed = {
            key: {inner_key: bool(inner_value) for inner_key, inner_value in value.items()}
            for key, value in _nested_int_keyed_dict(payload.get("manual_reload_needed")).items()
        }
        self.stunned_next_turn = _int_keyed_bool_dict(payload.get("stunned_next_turn"))
        self.special_lock_next_turn = _int_keyed_bool_dict(payload.get("special_lock_next_turn"))
        self.blind_next_attack = _int_keyed_float_dict(payload.get("blind_next_attack"))
        self.pending_flat_bonus = _int_keyed_int_dict(payload.get("pending_flat_bonus"))
        self.pending_flat_bonus_uses = _int_keyed_int_dict(payload.get("pending_flat_bonus_uses"))
        self.pending_multiplier = _int_keyed_float_dict(payload.get("pending_multiplier"))
        self.pending_multiplier_uses = _int_keyed_int_dict(payload.get("pending_multiplier_uses"))
        self.force_max_next = _int_keyed_int_dict(payload.get("force_max_next"))
        self.guaranteed_hit_next = _int_keyed_int_dict(payload.get("guaranteed_hit_next"))
        self.incoming_modifiers = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("incoming_modifiers")).items()}
        self.outgoing_attack_modifiers = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("outgoing_attack_modifiers")).items()}
        self.absorbed_damage = _int_keyed_int_dict(payload.get("absorbed_damage"))
        self.delayed_defense_queue = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("delayed_defense_queue")).items()}
        raw_airborne = _int_keyed_dict(payload.get("airborne_pending_landing"))
        self.airborne_pending_landing = {key: (value if isinstance(value, dict) else None) for key, value in raw_airborne.items()}
        self._all_battle_log_entries = [str(item) for item in _list_any(payload.get("all_battle_log_entries"))]
        self._all_battle_log_summaries = [str(item) for item in _list_any(payload.get("all_battle_log_summaries"))]
        self._recent_log_lines = [str(item) for item in _list_any(payload.get("recent_log_lines"))]
        self._last_highlight_tone = str(payload.get("last_highlight_tone") or "hit")
        self._biggest_hit = int(payload.get("biggest_hit", 0) or 0)
        self._critical_hits = int(payload.get("critical_hits", 0) or 0)
        self._effect_tag_counts = {
            str(key): int(value or 0)
            for key, value in _dict_str_any(payload.get("effect_tag_counts")).items()
        }
        self._effect_best_moments = {
            str(key): cast(EffectBestMoment, value)
            for key, value in _dict_str_any(payload.get("effect_best_moments")).items()
            if isinstance(value, dict)
        }
        self._effect_event_history = [
            cast(EffectBestMoment, item)
            for item in _list_any(payload.get("effect_event_history"))
            if isinstance(item, dict)
        ]
        self.round_counter = int(payload.get("round_counter", 0) or 0)
        self.ui_needs_resend = bool(payload.get("ui_needs_resend", False))
        self._full_battle_log_embed = create_battle_log_embed()
        self._full_battle_log_embed.description = self._full_battle_log_text()

    async def persist_session(
        self,
        channel: object,
        *,
        status: str = "active",
        battle_message: discord.Message | None = None,
    ) -> None:
        guild = getattr(channel, "guild", None)
        channel_id = getattr(channel, "id", None)
        if not isinstance(guild, discord.Guild) or not isinstance(channel_id, int):
            return
        if battle_message is not None:
            self.bind_durable_message(guild_id=guild.id, channel_id=channel_id, message_id=battle_message.id)
        self.session_id = await save_active_session(
            session_id=self.session_id,
            kind="fight_pvp" if self.player2_id != 0 else "fight_bot",
            guild_id=guild.id,
            channel_id=channel_id,
            thread_id=channel_id if isinstance(channel, discord.Thread) else None,
            battle_message_id=self._durable_message_id,
            log_message_id=self.battle_log_message.id if self.battle_log_message else None,
            status=status,
            payload=self.serialize_session_payload(),
        )
        if self._durable_message_id is not None:
            await upsert_durable_view(
                guild_id=guild.id,
                channel_id=channel_id,
                message_id=self._durable_message_id,
                view_kind=self.durable_view_kind,
                payload=self.durable_payload(),
            )

    async def _repost_battle_ui_if_needed(
        self,
        channel: object,
        *,
        interaction: discord.Interaction | None,
        current_message: discord.Message | None,
        battle_embed: discord.Embed,
        view: ui.View | None = None,
        status: str = "active",
    ) -> discord.Message | None:
        if not self.ui_needs_resend:
            return current_message
        old_battle_message = current_message
        old_log_message = self.battle_log_message
        if interaction is not None:
            new_log_message = await _safe_send_channel(
                interaction,
                channel,
                embed=self._full_battle_log_embed,
            )
        else:
            new_log_message = await _send_channel_message(
                channel,
                embed=self._full_battle_log_embed,
            )
        if new_log_message is None:
            return current_message
        self.battle_log_message = new_log_message
        if interaction is not None:
            new_battle_message = await _safe_send_channel(
                interaction,
                channel,
                embed=battle_embed,
                view=view,
            )
        else:
            new_battle_message = await _send_channel_message(
                channel,
                embed=battle_embed,
                view=view,
            )
        if new_battle_message is None:
            self.battle_log_message = old_log_message
            await _delete_message_quietly(new_log_message)
            return current_message
        self.ui_needs_resend = False
        await self.persist_session(channel, status=status, battle_message=new_battle_message)
        if old_log_message is not None and old_log_message.id != new_log_message.id:
            await _delete_message_quietly(old_log_message)
        if old_battle_message is not None and old_battle_message.id != new_battle_message.id:
            await _delete_message_quietly(old_battle_message)
        return new_battle_message

    async def _sync_runtime_flags_from_session(self) -> None:
        if not self.session_id:
            return
        session = await get_active_session(int(self.session_id))
        if session is None:
            return
        payload = _dict_str_any(session.get("payload"))
        self.ui_needs_resend = bool(payload.get("ui_needs_resend", self.ui_needs_resend))

    @staticmethod
    def _thread_finished_embed() -> discord.Embed:
        return discord.Embed(
            title="⚔️ Kampf beendet",
            description="Die Sieger-Nachricht wurde im öffentlichen Kanal gesendet.",
        )

    async def _resolve_public_result_channel(self, guild: discord.Guild | None):
        if guild is None or not self.public_result_channel_id:
            return None
        channel = guild.get_channel(int(self.public_result_channel_id))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(self.public_result_channel_id))
            except Exception:
                channel = None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _post_winner_public(
        self,
        guild: discord.Guild | None,
        fallback_channel: object,
        winner_embed: discord.Embed,
    ) -> None:
        target_channel = await self._resolve_public_result_channel(guild)
        if target_channel is not None:
            try:
                await target_channel.send(embed=winner_embed)
                return
            except Exception:
                logging.exception("Failed to send winner embed to public result channel")
        sendable_fallback = _coerce_sendable_channel(fallback_channel)
        if sendable_fallback is None:
            return
        try:
            await sendable_fallback.send(embed=winner_embed)
        except Exception:
            logging.exception("Failed to send winner embed to fallback channel")

    def _feedback_player_ids(self) -> list[int]:
        ids: list[int] = []
        for pid in (self.player1_id, self.player2_id):
            if isinstance(pid, int) and pid > 0 and pid not in ids:
                ids.append(pid)
        return ids

    def _feedback_prompt_text(self, guild: discord.Guild | None) -> str:
        mentions: list[str] = []
        for pid in self._feedback_player_ids():
            member = guild.get_member(pid) if guild else None
            mentions.append(member.mention if member else f"<@{pid}>")
        prompt = (
            "Gab es einen Bug/Fehler?\n"
            "Wenn du willst, klicke auf **Kampf-Log per DM** und ich schicke dir den vollständigen Log privat."
        )
        if mentions:
            return f"{' '.join(mentions)} {prompt}"
        return prompt

    async def _send_feedback_prompt(
        self,
        channel: object,
        guild: discord.Guild | None,
        *,
        auto_close_policy: ThreadAutoClosePolicy | None = DEFAULT_THREAD_AUTO_CLOSE_POLICY,
    ) -> None:
        allowed = set(self._feedback_player_ids())
        if not allowed:
            return
        sendable_channel = _coerce_sendable_channel(channel)
        if sendable_channel is None:
            return
        policy = _copy_thread_auto_close_policy(auto_close_policy)
        view = FightFeedbackView(
            sendable_channel,
            guild,
            allowed,
            battle_log_text=self._full_battle_log_text(),
            auto_close_delay=_thread_auto_close_delay(policy),
            close_on_idle=bool(policy and policy.get("close_on_idle", False)),
            close_after_no_bug=bool(policy and policy.get("close_after_no_bug", True)),
            keep_open_after_bug=bool(policy and policy.get("keep_open_after_bug", True)),
        )
        auto_close_hint = ""
        if isinstance(sendable_channel, discord.Thread):
            hint = _thread_auto_close_hint(policy)
            if hint:
                auto_close_hint = f"\n\n{hint}"
        message_text = (
            f"{self._feedback_prompt_text(guild)}{auto_close_hint}\n\n"
            "Buttons unten: **Es gab einen Bug** | **Kampf-Log per DM** | **Es gab keinen Bug**"
        )
        try:
            message = await sendable_channel.send(message_text, view=view)
            await _maybe_register_durable_message(message, view)
        except Exception:
            logging.exception("Failed to send fight feedback prompt with buttons")
            # Fallback, damit immer zumindest die Info im Kanal ankommt.
            await sendable_channel.send(self._feedback_prompt_text(guild))

    def _append_multi_hit_roll_event(self, effect_events: list[str]) -> None:
        meta = self._last_damage_roll_meta or {}
        if meta.get("kind") != "multi_hit":
            return
        details = meta.get("details")
        if not isinstance(details, dict):
            return
        hits = int(details.get("hits", 0) or 0)
        landed = int(details.get("landed_hits", 0) or 0)
        per_hit = details.get("per_hit_damages", [])
        per_hit_numbers: list[int] = []
        if isinstance(per_hit, list):
            for value in per_hit:
                try:
                    per_hit_numbers.append(int(value))
                except Exception:
                    continue
        per_hit_text = ", ".join(str(v) for v in per_hit_numbers) if per_hit_numbers else "-"
        total_damage = int(details.get("total_damage", 0) or 0)
        self._append_effect_event(
            effect_events,
            f"Treffer: {landed}/{hits} | Schaden pro Treffer: {per_hit_text} | Gesamt: {total_damage}.",
        )

    def _grant_airborne(self, player_id: int) -> None:
        battle_state.grant_unique_effect(self.active_effects, player_id, "airborne", player_id, duration=1)

    def _clear_airborne(self, player_id: int) -> None:
        battle_state.consume_effect(self.active_effects, player_id, "airborne")

    def queue_delayed_defense(
        self,
        player_id: int,
        defense: str,
        counter: int = 0,
        source: str | None = None,
    ) -> None:
        battle_state.queue_delayed_defense(
            self.delayed_defense_queue,
            player_id,
            defense,
            counter=counter,
            source=source,
        )

    def activate_delayed_defense_after_attack(
        self,
        player_id: int,
        effect_events: list[str],
        *,
        attack_landed: bool,
    ) -> None:
        battle_state.activate_delayed_defense_after_attack(
            self.delayed_defense_queue,
            self.active_effects,
            self.incoming_modifiers,
            player_id,
            effect_events,
            attack_landed=attack_landed,
        )

    def start_airborne_two_phase(
        self,
        player_id: int,
        landing_damage,
        effect_events: list[str],
        *,
        source_attack_index: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        battle_state.start_airborne_two_phase(
            self.active_effects,
            self.airborne_pending_landing,
            self.incoming_modifiers,
            player_id,
            landing_damage,
            effect_events,
            source_attack_index=source_attack_index,
            cooldown_turns=cooldown_turns,
        )

    def resolve_forced_landing_if_due(self, player_id: int, effect_events: list[str]) -> dict | None:
        return battle_state.resolve_forced_landing_if_due(
            self.active_effects,
            self.airborne_pending_landing,
            player_id,
            effect_events,
        )

    def _max_hp_for(self, player_id: int) -> int:
        return battle_state.max_hp_for(self._max_hp_by_player, player_id)

    def _hp_for(self, player_id: int) -> int:
        return battle_state.hp_for(self._hp_by_player, player_id)

    def _set_hp_for(self, player_id: int, value: int) -> None:
        battle_state.set_hp_for(self._hp_by_player, player_id, value)

    def heal_player(self, player_id: int, amount: int) -> int:
        return battle_state.heal_player(self._hp_by_player, self._max_hp_by_player, player_id, amount)

    def _apply_non_heal_damage(self, player_id: int, amount: int) -> int:
        return battle_state.apply_non_heal_damage(self._hp_by_player, player_id, amount)

    def _card_name_for(self, player_id: int) -> str:
        fallback = "Bot" if player_id == 0 else "Spieler"
        return battle_state.card_name_for(self._card_names_by_player, player_id, fallback=fallback)

    def _apply_non_heal_damage_with_event(
        self,
        events: list[str],
        player_id: int,
        amount: int,
        *,
        source: str,
        self_damage: bool,
    ) -> int:
        return battle_state.apply_non_heal_damage_with_event(
            self._hp_by_player,
            self._card_names_by_player,
            events,
            player_id,
            amount,
            source=source,
            self_damage=self_damage,
        )

    def _guard_non_heal_damage_result(self, defender_id: int, defender_hp_before: int, context: str) -> None:
        battle_state.guard_non_heal_damage_result(self._hp_by_player, defender_id, defender_hp_before, context)

    def queue_incoming_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        reflect: float = 0.0,
        store_ratio: float = 0.0,
        cap: int | str | None = None,
        evade: bool = False,
        counter: int = 0,
        turns: int = 1,
        source: str | None = None,
    ) -> None:
        battle_state.queue_incoming_modifier(
            self.incoming_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            reflect=reflect,
            store_ratio=store_ratio,
            cap=cap,
            evade=evade,
            counter=counter,
            turns=turns,
            source=source,
        )

    def _consume_airborne_evade_marker(self, player_id: int) -> bool:
        modifiers = self.incoming_modifiers.get(player_id) or []
        for idx, mod in enumerate(modifiers):
            if not isinstance(mod, dict):
                continue
            if not bool(mod.get("evade")):
                continue
            if str(mod.get("source") or "").strip().lower() != "airborne":
                continue
            try:
                modifiers.pop(idx)
            except Exception:
                logging.exception("Unexpected error")
                return False
            return True
        return False

    def queue_outgoing_attack_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        turns: int = 1,
        source: str | None = None,
    ) -> None:
        battle_state.queue_outgoing_attack_modifier(
            self.outgoing_attack_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            turns=turns,
            source=source,
        )

    def apply_outgoing_attack_modifiers(self, attacker_id: int, raw_damage: int) -> tuple[int, int, dict[str, object] | None]:
        reduced_damage, overflow_self_damage, modifier_details = battle_state.apply_outgoing_attack_modifiers(
            self.outgoing_attack_modifiers,
            attacker_id,
            raw_damage,
        )
        return reduced_damage, overflow_self_damage, modifier_details

    def consume_guaranteed_hit(self, player_id: int) -> bool:
        return battle_state.consume_guaranteed_hit(self.guaranteed_hit_next, player_id)

    def roll_attack_damage(
        self,
        attack: dict,
        base_damage,
        damage_buff: int,
        attack_multiplier: float,
        force_max_damage: bool,
        guaranteed_hit: bool,
    ) -> tuple[int, bool, int, int]:
        cap = MAX_ATTACK_DAMAGE_PER_HIT
        multi_hit = attack.get("multi_hit")
        if isinstance(multi_hit, dict):
            actual_damage, min_damage, max_damage, details = _resolve_multi_hit_damage_details(
                multi_hit,
                buff_amount=damage_buff,
                attack_multiplier=attack_multiplier,
                force_max=force_max_damage,
                guaranteed_hit=guaranteed_hit,
            )
            actual_damage = min(cap, max(0, int(actual_damage)))
            min_damage = min(cap, max(0, int(min_damage)))
            max_damage = min(cap, max(min_damage, int(max_damage)))
            if isinstance(details, dict):
                details["total_damage"] = actual_damage
            self._last_damage_roll_meta = {"kind": "multi_hit", "details": details}
            is_critical = bool(force_max_damage and actual_damage >= max_damage and max_damage > 0)
            return actual_damage, is_critical, min_damage, max_damage

        self._last_damage_roll_meta = {"kind": "single_hit"}
        actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, damage_buff)
        if attack_multiplier != 1.0:
            actual_damage = int(round(actual_damage * attack_multiplier))
            max_damage = int(round(max_damage * attack_multiplier))
            min_damage = int(round(min_damage * attack_multiplier))
        if force_max_damage:
            actual_damage = max_damage
            is_critical = max_damage > 0
        min_damage = min(cap, max(0, int(min_damage)))
        max_damage = min(cap, max(min_damage, int(max_damage)))
        actual_damage = min(cap, max(0, int(actual_damage)))
        return actual_damage, is_critical, min_damage, max_damage

    def _resolve_incoming_modifiers_with_details(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int, dict[str, object] | None]:
        return battle_state.resolve_incoming_modifiers(
            self.incoming_modifiers,
            self.absorbed_damage,
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            incoming_min_damage=incoming_min_damage,
        )

    def resolve_incoming_modifiers(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int]:
        final_damage, reflected_damage, dodged, counter_damage, _modifier_details = self._resolve_incoming_modifiers_with_details(
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            incoming_min_damage=incoming_min_damage,
        )
        return final_damage, reflected_damage, dodged, counter_damage

    def _append_incoming_resolution_events(
        self,
        effect_events: list[str],
        *,
        defender_name: str,
        raw_damage: int,
        final_damage: int,
        reflected_damage: int,
        dodged: bool,
        counter_damage: int,
        modifier_details: dict[str, object] | None = None,
        absorbed_before: int | None = None,
        absorbed_after: int | None = None,
    ) -> None:
        defender = str(defender_name or "Verteidiger").strip() or "Verteidiger"
        modifier_source = str((modifier_details or {}).get("source") or "").strip()
        source_suffix = f" durch {_effect_source_name(modifier_source)}" if modifier_source else ""
        if dodged:
            self._append_effect_event(effect_events, f"Ausweichen{source_suffix}: Angriff vollständig verfehlt.")
        elif final_damage < raw_damage:
            self._append_effect_event(
                effect_events,
                _damage_transition_text(
                    int(raw_damage),
                    int(final_damage),
                    source=modifier_source or None,
                    context="Schutzwirkung",
                ),
            )

        if reflected_damage > 0:
            reflect_prefix = "Spiegeldimension/Reflexion" if not modifier_source else f"Reflexion{source_suffix}"
            if not modifier_source:
                self._append_effect_event(
                    effect_events,
                    f"{reflect_prefix} durch {defender}: {int(reflected_damage)} Schaden zurückgeworfen.",
                )
            else:
                self._append_effect_event(
                    effect_events,
                    f"Reflexion{source_suffix} durch {defender}: {int(reflected_damage)} Schaden zurückgeworfen.",
                )
        if counter_damage > 0:
            self._append_effect_event(effect_events, f"Konter{source_suffix} durch {defender}: {int(counter_damage)} Schaden.")

        if (
            absorbed_before is not None
            and absorbed_after is not None
            and int(absorbed_after) > int(absorbed_before)
        ):
            gained = int(absorbed_after) - int(absorbed_before)
            self._append_effect_event(effect_events, f"Absorption{source_suffix} durch {defender}: {gained} Schaden gespeichert.")

    def apply_regen_tick(self, player_id: int) -> int:
        return battle_state.apply_regen_tick(
            self.active_effects,
            self._hp_by_player,
            self._max_hp_by_player,
            player_id,
        )

    def _status_icons(self, player_id: int) -> str:
        return battle_state.status_icons(self.active_effects, player_id)

    def _current_attack_infos(self) -> list[str]:
        current_card = self.player1_card if self.current_turn == self.player1_id else self.player2_card
        return _build_attack_info_lines(current_card)

    async def _safe_edit_battle_log(self, embed) -> None:
        if not self.battle_log_message:
            return
        try:
            last_ts = float(getattr(self, "_last_log_edit_ts", 0.0) or 0.0)
        except Exception:
            last_ts = 0.0
        now = time.monotonic()
        if now - last_ts < 0.9:
            await asyncio.sleep(0.9 - (now - last_ts))
        for attempt in range(2):
            try:
                await self.battle_log_message.edit(embed=embed)
                self._battle_log_text_cache = str(embed.description or "")
                self._last_log_edit_ts = time.monotonic()
                return
            except Exception as e:
                if getattr(e, "status", None) == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                logging.exception("Failed to edit battle log")
                return
    
    def is_attack_on_cooldown(self, player_id, attack_index):
        return battle_state.is_attack_on_cooldown(self.attack_cooldowns[player_id], attack_index)

    @staticmethod
    def _attack_has_heal(attack: dict) -> bool:
        return _attack_has_heal_component(attack)

    @staticmethod
    def _attack_has_setup(attack: dict) -> bool:
        setup_types = {
            "damage_boost",
            "damage_multiplier",
            "force_max",
            "guaranteed_hit",
            "enemy_next_attack_reduction_flat",
            "enemy_next_attack_reduction_percent",
            "delayed_defense_after_next_attack",
            "damage_reduction",
            "damage_reduction_sequence",
            "damage_reduction_flat",
            "evade",
            "stealth",
            "reflect",
            "absorb_store",
            "cap_damage",
            "special_lock",
            "stun",
            "blind",
            "mix_heal_or_max",
        }
        for effect in attack.get("effects", []):
            if str(effect.get("type") or "").strip().lower() in setup_types:
                return True
        return False

    def _estimate_attack_max_damage_for_bot(self, attack: dict, defender_hp: int, attacker_hp: int) -> int:
        damage = attack.get("damage", [0, 0])
        damage_buff = 0
        damage_max_bonus = 0
        attacker_max_hp = self._max_hp_for(0)
        defender_max_hp = self._max_hp_for(self.player1_id)

        conditional_self_pct = attack.get("bonus_if_self_hp_below_pct")
        conditional_self_bonus = int(attack.get("bonus_damage_if_condition", 0) or 0)
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * float(conditional_self_pct)):
            damage_buff += conditional_self_bonus

        conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
            damage_if_condition = attack.get("damage_if_condition")
            damage = _coerce_damage_input(damage_if_condition, default=0)

        if attack.get("add_absorbed_damage"):
            damage_buff += int(self.absorbed_damage.get(0, 0) or 0)

        max_damage = self.get_attack_max_damage(damage, damage_buff)
        if max_damage > 0 and self.pending_flat_bonus_uses.get(0, 0) > 0:
            max_damage += int(self.pending_flat_bonus.get(0, 0) or 0)
        if max_damage > 0 and self.pending_multiplier_uses.get(0, 0) > 0:
            multiplier = float(self.pending_multiplier.get(0, 1.0) or 1.0)
            max_damage = int(round(max_damage * multiplier))
        return max(0, int(max_damage))

    def _score_bot_attack_choice(
        self,
        *,
        attack: dict,
        attack_index: int,
        attacks: list[dict],
        defender_hp: int,
        attacker_hp: int,
        attacker_max_hp: int,
        guaranteed_hit_candidate: bool,
    ) -> int:
        max_damage = self._estimate_attack_max_damage_for_bot(attack, defender_hp, attacker_hp)
        score = max_damage * 20

        if max_damage > 0 and max_damage >= defender_hp:
            score += 20000

        has_heal = self._attack_has_heal(attack)
        has_setup = self._attack_has_setup(attack)

        hp_ratio = (attacker_hp / attacker_max_hp) if attacker_max_hp > 0 else 1.0
        if has_heal:
            if hp_ratio <= 0.35:
                score += 9000
            elif hp_ratio <= 0.55:
                score += 4500
            elif hp_ratio <= 0.70:
                score += 1000
            else:
                score += 100

        bot_buff_active = (
            self.pending_flat_bonus_uses.get(0, 0) > 0
            or self.pending_multiplier_uses.get(0, 0) > 0
            or self.force_max_next.get(0, 0) > 0
        )
        if has_setup:
            strong_followup_exists = False
            for idx, other in enumerate(attacks[:4]):
                if idx == attack_index:
                    continue
                if self.special_lock_next_turn.get(0, False) and idx != 0:
                    continue
                if self.is_attack_on_cooldown(0, idx):
                    continue
                if other.get("requires_reload") and self.is_reload_needed(0, idx):
                    continue
                other_max = self._estimate_attack_max_damage_for_bot(other, defender_hp, attacker_hp)
                if other_max >= max(35, int(defender_hp * 0.3)):
                    strong_followup_exists = True
                    break
            if not bot_buff_active and strong_followup_exists:
                score += 3500
            elif not bot_buff_active:
                score += 1800
            else:
                score += 400

        outgoing_reduced = bool(self.outgoing_attack_modifiers.get(0))
        if outgoing_reduced:
            if has_heal or has_setup:
                score += 2200
            if max_damage > 0:
                score -= 1400

        defender_has_stealth = self.has_stealth(self.player1_id)
        if defender_has_stealth and not guaranteed_hit_candidate:
            if max_damage > 0:
                score -= 2000
            if has_heal or has_setup:
                score += 1200

        if attack.get("requires_reload") and self.is_reload_needed(0, attack_index):
            score -= 500

        return int(score)

    def _choose_bot_attack_index(self, attacks: list[dict]) -> int:
        attacker_hp = self._hp_for(0)
        attacker_max_hp = self._max_hp_for(0)
        defender_hp = self._hp_for(self.player1_id)
        defender_max_hp = self._max_hp_for(self.player1_id)

        candidate_indices: list[int] = []
        for i, attack in enumerate(attacks[:4]):
            if self.special_lock_next_turn.get(0, False) and i != 0:
                continue
            if not self.is_attack_on_cooldown(0, i):
                candidate_indices.append(i)

        if not candidate_indices:
            for i, _attack in enumerate(attacks[:4]):
                if self.special_lock_next_turn.get(0, False) and i != 0:
                    continue
                candidate_indices.append(i)

        if not candidate_indices:
            return 0

        def _candidate_key(idx: int) -> tuple[int, int, int, int]:
            attack = attacks[idx]
            conditional_enemy_triggered = False
            conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
            if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
                conditional_enemy_triggered = True
            guaranteed_hit_candidate = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)
            guaranteed_hit_candidate = guaranteed_hit_candidate or (self.guaranteed_hit_next.get(0, 0) > 0)

            score = self._score_bot_attack_choice(
                attack=attack,
                attack_index=idx,
                attacks=attacks,
                defender_hp=defender_hp,
                attacker_hp=attacker_hp,
                attacker_max_hp=attacker_max_hp,
                guaranteed_hit_candidate=guaranteed_hit_candidate,
            )
            max_damage = self._estimate_attack_max_damage_for_bot(attack, defender_hp, attacker_hp)
            cooldown_turns = int(attack.get("cooldown_turns", 0) or 0)
            return score, max_damage, -cooldown_turns, -idx

        return max(candidate_indices, key=_candidate_key)
    
    def get_attack_max_damage(self, attack_damage, damage_buff=0):
        return battle_state.get_attack_max_damage(attack_damage, damage_buff)

    def get_attack_min_damage(self, attack_damage, damage_buff=0):
        return battle_state.get_attack_min_damage(attack_damage, damage_buff)

    def is_strong_attack(self, attack_damage, damage_buff=0):
        return battle_state.is_strong_attack(attack_damage, damage_buff)

    def start_attack_cooldown(self, player_id, attack_index):
        battle_state.start_attack_cooldown(self.attack_cooldowns[player_id], attack_index, turns=2)
    
    def reduce_cooldowns(self, player_id):
        battle_state.reduce_cooldowns(self.attack_cooldowns[player_id])
        
    async def init_with_buffs(self):
        player1_buffs = await get_card_buffs(self.player1_id, self.player1_card["name"])
        player2_buffs = await get_card_buffs(self.player2_id, self.player2_card["name"])
        health_buff1, _damage_map1 = battle_state.summarize_card_buffs(player1_buffs)
        health_buff2, _damage_map2 = battle_state.summarize_card_buffs(player2_buffs)
        self.player1_hp += health_buff1
        self.player2_hp += health_buff2
        self.player1_max_hp = self.player1_hp
        self.player2_max_hp = self.player2_hp
        if self.hp_view is not None:
            self.hp_view.update_hp(self.player1_hp)
        await self.update_attack_buttons()
        
    async def update_attack_buttons(self):
        """Aktualisiert die Attacken-Buttons basierend auf der aktuellen Karte mit Buffs"""
        # Hole aktuelle Karte
        current_card = self.player1_card if self.current_turn == self.player1_id else self.player2_card
        attacks = current_card.get("attacks", [{"name": "Punch", "damage": [15, 25]}])
        
        # Hole Buffs für diese Karte
        card_buffs = await get_card_buffs(self.current_turn, current_card["name"])
        
        # Finde die vier Angriffs-Buttons (Zeilen 0 und 1, unabhängig von Label/Style)
        attack_buttons = [child for child in self.children if isinstance(child, ui.Button) and child.row in (0, 1)]
        attack_buttons = attack_buttons[:4]

        pending_landing = self.airborne_pending_landing.get(self.current_turn)
        if pending_landing:
            landing_damage = pending_landing.get("damage", [20, 40])
            if isinstance(landing_damage, list) and len(landing_damage) == 2:
                dmg_text = f"{int(landing_damage[0])}-{int(landing_damage[1])}"
            else:
                dmg_text = "20-40"
            if attack_buttons:
                first = attack_buttons[0]
                first.style = discord.ButtonStyle.danger
                first.label = f"Landungsschlag ({dmg_text}) ✈️"
                first.disabled = False
            for i, btn in enumerate(attack_buttons[1:], start=1):
                btn.style = discord.ButtonStyle.secondary
                if i < len(attacks):
                    blocked_attack = attacks[i]
                    blocked_name = str(blocked_attack.get("name") or f"Angriff {i+1}")
                    if self.is_attack_on_cooldown(self.current_turn, i):
                        cooldown_turns = self.attack_cooldowns[self.current_turn].get(i, 0)
                        btn.label = f"{blocked_name} ({_format_cooldown_label(blocked_attack, cooldown_turns)})"
                    else:
                        btn.label = f"{blocked_name} (Blockiert)"
                else:
                    btn.label = "—"
                btn.disabled = True
            return
        
        for i, attack in enumerate(attacks[:4]):
            if i < len(attack_buttons):
                # Weisen wir die Buttons strikt in Reihenfolge zu
                button = attack_buttons[i]
                base_damage = attack["damage"]
                damage_max_bonus = 0
                
                # Berechne Buff für diese Attacke
                for buff_type, attack_number, buff_amount in card_buffs:
                    if buff_type == "damage" and attack_number == (i + 1):
                        damage_max_bonus += buff_amount
                
                # Berechne Schadenbereich mit Buffs
                min_dmg, max_dmg = _damage_range_with_max_bonus(base_damage, max_only_bonus=damage_max_bonus, flat_bonus=0)
                damage_text = f"{min_dmg}-{max_dmg}"
                
                buff_text = f" (+{damage_max_bonus} max)" if damage_max_bonus > 0 else ""
                # Effekte-Label (🔥 Verbrennung, 🌀 Verwirrung)
                effects = attack.get("effects", [])
                effect_icons = []
                for eff in effects:
                    eff_type = eff.get("type")
                    if eff_type == "burning":
                        if "🔥" not in effect_icons:
                            effect_icons.append("🔥")
                    elif eff_type == "confusion":
                        if "🌀" not in effect_icons:
                            effect_icons.append("🌀")
                    elif eff_type == "stealth":
                        if "🥷" not in effect_icons:
                            effect_icons.append("🥷")
                    elif eff_type == "stun":
                        if "🛑" not in effect_icons:
                            effect_icons.append("🛑")
                    elif eff_type in {
                        "damage_reduction",
                        "damage_reduction_flat",
                        "enemy_next_attack_reduction_percent",
                        "enemy_next_attack_reduction_flat",
                        "reflect",
                        "absorb_store",
                        "cap_damage",
                        "delayed_defense_after_next_attack",
                    }:
                        if "🛡️" not in effect_icons:
                            effect_icons.append("🛡️")
                    elif eff_type == "airborne_two_phase":
                        if "✈️" not in effect_icons:
                            effect_icons.append("✈️")
                    elif eff_type in {"damage_boost", "damage_multiplier"}:
                        if "⚡" not in effect_icons:
                            effect_icons.append("⚡")
                    elif eff_type in {"force_max", "mix_heal_or_max", "guaranteed_hit"}:
                        if "🎯" not in effect_icons:
                            effect_icons.append("🎯")
                    elif eff_type in {"heal", "regen"}:
                        if "❤️" not in effect_icons:
                            effect_icons.append("❤️")
                heal_label = _heal_label_for_attack(attack)
                if heal_label and "❤️" not in effect_icons:
                    effect_icons.append("❤️")
                effects_label = f" {' '.join(effect_icons)}" if effect_icons else ""
                
                # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist (nur für aktuellen Spieler)
                is_on_cooldown = self.is_attack_on_cooldown(self.current_turn, i)
                is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.current_turn, i))
                
                if is_on_cooldown:
                    # Grau für Cooldown beim aktuellen Spieler
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = self.attack_cooldowns[self.current_turn][i]
                    button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                    button.disabled = True
                elif is_reload_action:
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    if heal_label is not None:
                        default_style = discord.ButtonStyle.success
                        button.label = f"{attack['name']} (+{heal_label}){effects_label}"
                    else:
                        # Rot für normale Attacken
                        default_style = discord.ButtonStyle.danger
                        button.label = f"{attack['name']} ({damage_text}{buff_text}){effects_label}"
                    button.style = _resolve_attack_button_style(attack, default_style)
                    button.disabled = False

        # Deaktiviere restliche Buttons, falls die aktuelle Karte weniger als 4 Attacken hat
        if len(attacks) < len(attack_buttons):
            for j in range(len(attacks), len(attack_buttons)):
                btn = attack_buttons[j]
                btn.style = discord.ButtonStyle.secondary
                btn.label = "—"
                btn.disabled = True

    # Angriffs-Buttons (rot, 2x2 Grid)
    @ui.button(label="Angriff 1", style=discord.ButtonStyle.danger, row=0, custom_id="battle:attack1")
    async def attack1(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 0)

    @ui.button(label="Angriff 2", style=discord.ButtonStyle.danger, row=0, custom_id="battle:attack2")
    async def attack2(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 1)

    @ui.button(label="Angriff 3", style=discord.ButtonStyle.danger, row=1, custom_id="battle:attack3")
    async def attack3(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 2)

    @ui.button(label="Angriff 4", style=discord.ButtonStyle.danger, row=1, custom_id="battle:attack4")
    async def attack4(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 3)

    # Blaue Buttons unten
    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, row=2, custom_id="battle:cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id in [self.player1_id, self.player2_id]:
            embed = discord.Embed(
                title="⚔️ Kampf abgebrochen",
                description=f"Der Kampf wurde von {interaction.user.mention} abgebrochen.",
            )
            await interaction.response.edit_message(embed=embed, view=None)
            try:
                await self._send_feedback_prompt(
                    interaction.channel,
                    interaction.guild,
                    auto_close_policy=CANCELLED_THREAD_AUTO_CLOSE_POLICY,
                )
            except Exception:
                logging.exception("Unexpected error")
            try:
                await self.persist_session(interaction.channel, status="cancelled")
            except Exception:
                logging.exception("Failed to persist cancelled fight session")
            self.stop()
        else:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)

    # Entfernt: Platzhalter-Button

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
        guild = interaction.guild
        message = _interaction_message_or_none(interaction)
        # Block actions if fight already ended (HP <= 0)
        if self.player1_hp <= 0 or self.player2_hp <= 0:
            try:
                await interaction.response.send_message("❌ Der Kampf ist bereits vorbei.", ephemeral=True)
            except Exception:
                logging.exception("Unexpected error")
            return
        if interaction.user.id != self.current_turn:
            await interaction.response.send_message("Du bist nicht an der Reihe!", ephemeral=True)
            return
        await _safe_defer_interaction(interaction)
        await self._sync_runtime_flags_from_session()

        if self.stunned_next_turn.get(self.current_turn, False):
            self.stunned_next_turn[self.current_turn] = False
            skipped_player_id = self.current_turn
            airborne_owner_id = self.player2_id if skipped_player_id == self.player1_id else self.player1_id
            if self.airborne_pending_landing.get(airborne_owner_id):
                self._consume_airborne_evade_marker(airborne_owner_id)
            self.current_turn = self.player2_id if self.current_turn == self.player1_id else self.player1_id
            self.reduce_cooldowns(self.current_turn)
            await self.update_attack_buttons()
            user1 = _get_member_if_available(guild, self.player1_id)
            user2 = _get_member_if_available(guild, self.player2_id)
            battle_embed = create_battle_embed(
                self.player1_card,
                self.player2_card,
                self.player1_hp,
                self.player2_hp,
                self.current_turn,
                user1,
                user2,
                self.active_effects,
                current_attack_infos=self._current_attack_infos(),
                recent_log_lines=self._recent_log_lines,
                highlight_tone=self._last_highlight_tone,
            )
            battle_embed.description = (battle_embed.description or "") + "\n\n🛑 Der Gegner war betäubt und hat seinen Zug ausgesetzt."
            if message is not None:
                try:
                    await message.edit(embed=battle_embed, view=self)
                except Exception:
                    await _safe_send_channel(interaction, interaction.channel, embed=battle_embed, view=self)
            else:
                await _safe_send_channel(interaction, interaction.channel, embed=battle_embed, view=self)
            if self.current_turn == 0 and message is not None:
                await self.execute_bot_attack(message)
            return

        effect_events: list[str] = []
        forced_landing_attack = self.resolve_forced_landing_if_due(self.current_turn, effect_events)
        is_forced_landing = forced_landing_attack is not None

        # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist
        if not is_forced_landing and self.is_attack_on_cooldown(self.current_turn, attack_index):
            await _safe_send_interaction_ephemeral(interaction, "Diese Attacke ist noch auf Cooldown!")
            return

        if not is_forced_landing and self.special_lock_next_turn.get(self.current_turn, False) and attack_index != 0:
            await _safe_send_interaction_ephemeral(
                interaction,
                "Diese Runde sind nur Standard-Angriffe erlaubt (Attacke 1).",
            )
            return

        # Bestimme Angreifer und Verteidiger zuerst
        if self.current_turn == self.player1_id:
            attacker_card = self.player1_card["name"]
            defender_card = self.player2_card["name"]
            attacker_user = _get_member_if_available(guild, self.player1_id)
            defender_user = _get_member_if_available(guild, self.player2_id)
            defender_id = self.player2_id
        else:
            attacker_card = self.player2_card["name"]
            defender_card = self.player1_card["name"]
            attacker_user = _get_member_if_available(guild, self.player2_id)
            defender_user = _get_member_if_available(guild, self.player1_id)
            defender_id = self.player1_id

        # Regeneration tickt beim Start des eigenen Zuges
        regen_heal = self.apply_regen_tick(self.current_turn)
        if regen_heal > 0:
            self._append_effect_event(effect_events, f"Regeneration heilt {regen_heal} HP.")

        # SIDE EFFECTS: Apply effects on defender before attack
        effects_to_remove = []
        pre_burn_total = 0
        for effect in self.active_effects[defender_id]:
            if effect.get('applier') == self.current_turn and effect.get('type') == 'burning':
                damage = _effect_int(effect, 'damage')
                if defender_id == self.player1_id:
                    self.player1_hp -= damage
                else:
                    self.player2_hp -= damage
                self.player1_hp = max(0, self.player1_hp)
                self.player2_hp = max(0, self.player2_hp)
                pre_burn_total += damage

                # Decrease duration
                remaining_duration = _effect_int(effect, 'duration') - 1
                effect['duration'] = remaining_duration
                if remaining_duration <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        # Hole aktuelle Karte und Angriff
        current_card = self.player1_card if self.current_turn == self.player1_id else self.player2_card
        attacks = current_card.get("attacks", [{"name": "Punch", "damage": [15, 25]}])
        if (not is_forced_landing) and attack_index >= len(attacks):
            await _safe_send_interaction_ephemeral(interaction, "Ungültiger Angriff!")
            return
        # Vor DB/weiterer Logik früh defern, damit Interaction nicht abläuft.
        await _safe_defer_interaction(interaction)
        damage_buff = 0
        damage_max_bonus = 0
        if is_forced_landing:
            attack = forced_landing_attack
            base_damage = attack["damage"]
            is_reload_action = False
            attack_name = attack["name"]
        else:
            attack = attacks[attack_index]
            base_damage = attack["damage"]
            is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.current_turn, attack_index))
            attack_name = str(attack.get("reload_name") or "Nachladen") if is_reload_action else attack["name"]

            # NEUES BUFF-SYSTEM: Hole User-spezifische Damage-Buffs
            card_buffs = await get_card_buffs(self.current_turn, current_card["name"])
            for buff_type, attack_number, buff_amount in card_buffs:
                if buff_type == "damage" and attack_number == (attack_index + 1):
                    damage_max_bonus += buff_amount

        attacker_hp = self._hp_for(self.current_turn)
        attacker_max_hp = self._max_hp_for(self.current_turn)
        defender_hp = self._hp_for(defender_id)
        defender_max_hp = self._max_hp_for(defender_id)

        conditional_self_pct = attack.get("bonus_if_self_hp_below_pct")
        conditional_self_bonus = int(attack.get("bonus_damage_if_condition", 0) or 0)
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * float(conditional_self_pct)):
            damage_buff += conditional_self_bonus

        conditional_enemy_triggered = False
        conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
            conditional_enemy_triggered = True
            damage_if_condition = attack.get("damage_if_condition")
            base_damage = _coerce_damage_input(damage_if_condition, default=0)
        if damage_max_bonus > 0:
            base_damage = _apply_max_only_damage_bonus(base_damage, damage_max_bonus)

        if attack.get("add_absorbed_damage"):
            absorbed_bonus = int(self.absorbed_damage.get(self.current_turn, 0) or 0)
            damage_buff += absorbed_bonus
            self.absorbed_damage[self.current_turn] = 0
            base_min, base_max = _range_pair(base_damage)
            base_text = str(base_min) if base_min == base_max else f"{base_min}-{base_max}"
            self._append_effect_event(
                effect_events,
                f"Kinetische Entladung: Grundschaden {base_text}, durch Absorption +{absorbed_bonus}.",
            )

        is_damaging_attack = self.get_attack_max_damage(base_damage, 0) > 0
        attack_multiplier = 1.0
        applied_flat_bonus_now = 0
        force_max_damage = False
        if is_damaging_attack:
            if self.pending_flat_bonus_uses.get(self.current_turn, 0) > 0:
                flat_bonus_now = int(self.pending_flat_bonus.get(self.current_turn, 0))
                damage_buff += flat_bonus_now
                applied_flat_bonus_now = max(0, flat_bonus_now)
                self.pending_flat_bonus_uses[self.current_turn] -= 1
                if self.pending_flat_bonus_uses[self.current_turn] <= 0:
                    self.pending_flat_bonus[self.current_turn] = 0
                if flat_bonus_now > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{flat_bonus_now} Schaden auf diesen Angriff.")
            if self.pending_multiplier_uses.get(self.current_turn, 0) > 0:
                attack_multiplier = float(self.pending_multiplier.get(self.current_turn, 1.0) or 1.0)
                self.pending_multiplier_uses[self.current_turn] -= 1
                if self.pending_multiplier_uses[self.current_turn] <= 0:
                    self.pending_multiplier[self.current_turn] = 1.0
                multiplier_pct = int(round((attack_multiplier - 1.0) * 100))
                if multiplier_pct > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{multiplier_pct}% Schaden auf diesen Angriff.")
            if self.force_max_next.get(self.current_turn, 0) > 0:
                force_max_damage = True
                self.force_max_next[self.current_turn] -= 1

        guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)

        # Manual reload action: spend turn to load the shot again.
        attack_hits_enemy = True
        self_damage = 0
        if is_reload_action:
            actual_damage = 0
            is_critical = False
            attack_hits_enemy = False
            self.set_reload_needed(self.current_turn, attack_index, False)
        else:
            min_damage = 0
            max_damage = 0
            defender_has_stealth = self.has_stealth(defender_id)
            guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(self.current_turn)
            if guaranteed_hit:
                self.blind_next_attack[self.current_turn] = 0.0
                self.consume_confusion_if_any(self.current_turn)
                self._append_effect_event(effect_events, "Dieser Angriff trifft garantiert.")
            max_damage_threshold = self.get_attack_max_damage(base_damage, damage_buff)
            blind_chance = float(self.blind_next_attack.get(self.current_turn, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[self.current_turn] = 0.0
                blind_miss = random.random() < blind_chance
            # CONFUSION: Falls Angreifer verwirrt ist, 77% Selbstschaden, 23% normaler Treffer
            if blind_miss:
                actual_damage = 0
                is_critical = False
                attack_hits_enemy = False
                if self.confused_next_turn.get(self.current_turn, False):
                    self.consume_confusion_if_any(self.current_turn)
            elif self.confused_next_turn.get(self.current_turn, False):
                if random.random() < 0.77:
                    # Selbstschaden anstatt Gegner-Schaden
                    self_damage = random.randint(15, 20) if max_damage_threshold <= 100 else random.randint(40, 60)
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.current_turn,
                        self_damage,
                        source="Verwirrung",
                        self_damage=True,
                    )
                    actual_damage = 0
                    is_critical = False
                    attack_hits_enemy = False
                else:
                    # Angriff geht normal durch
                    actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                        attack,
                        base_damage,
                        damage_buff,
                        attack_multiplier,
                        force_max_damage,
                        guaranteed_hit,
                    )
                    self._append_multi_hit_roll_event(effect_events)
                    if defender_has_stealth and not guaranteed_hit:
                        actual_damage = 0
                        is_critical = False
                        attack_hits_enemy = False
                        self.consume_stealth(defender_id)
                    elif defender_has_stealth:
                        self.consume_stealth(defender_id)
                # Confusion verbraucht und UI-Icon entfernen
                self.consume_confusion_if_any(self.current_turn)
            else:
                # Normaler Angriff
                actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                    attack,
                    base_damage,
                    damage_buff,
                    attack_multiplier,
                    force_max_damage,
                    guaranteed_hit,
                )
                self._append_multi_hit_roll_event(effect_events)
                if defender_has_stealth and not guaranteed_hit:
                    actual_damage = 0
                    is_critical = False
                    attack_hits_enemy = False
                    self.consume_stealth(defender_id)
                elif defender_has_stealth:
                    self.consume_stealth(defender_id)

            if attack_hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                defender_hp_before = self._hp_for(defender_id)
                reduced_damage, overflow_self_damage, outgoing_modifier = self.apply_outgoing_attack_modifiers(
                    self.current_turn,
                    actual_damage,
                )
                if reduced_damage != actual_damage:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._append_effect_event(
                        effect_events,
                        _damage_transition_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                            context="Ausgehende Reduktion",
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.current_turn,
                        overflow_self_damage,
                        source="Überlauf-Rückstoß",
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False

                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(defender_id, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    defender_id,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(defender_id)),
                    incoming_min_damage=min_damage,
                )
                absorbed_after = int(self.absorbed_damage.get(defender_id, 0) or 0)
                self._append_incoming_resolution_events(
                    effect_events,
                    defender_name=defender_card,
                    raw_damage=incoming_raw_damage,
                    final_damage=int(final_damage),
                    reflected_damage=int(reflected_damage),
                    dodged=bool(dodged),
                    counter_damage=int(counter_damage),
                    modifier_details=incoming_modifier,
                    absorbed_before=absorbed_before,
                    absorbed_after=absorbed_after,
                )
                if dodged:
                    actual_damage = 0
                    attack_hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    if actual_damage > 0:
                        self._apply_non_heal_damage(defender_id, actual_damage)
                    else:
                        is_critical = False
                if reflected_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.current_turn,
                        reflected_damage,
                        source="Reflexions-Rückschaden",
                        self_damage=False,
                    )
                if counter_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.current_turn,
                        counter_damage,
                        source="Konter-Rückschaden",
                        self_damage=False,
                    )
                self._guard_non_heal_damage_result(defender_id, defender_hp_before, "pvp_player_attack")
            if not attack_hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.current_turn,
                self_damage_value,
                source=f"{attack_name} / Rückstoß",
                self_damage=True,
            )

        heal_data = attack.get("heal")
        if heal_data is not None:
            heal_amount = _random_int_from_range(heal_data)
            healed_now = self.heal_player(self.current_turn, heal_amount)
            if healed_now > 0:
                self._append_effect_event(effect_events, f"Heilung: +{healed_now} HP.")

        lifesteal_ratio = float(attack.get("lifesteal_ratio", 0.0) or 0.0)
        if lifesteal_ratio > 0 and attack_hits_enemy and actual_damage > 0:
            lifesteal_heal = self.heal_player(self.current_turn, int(round(actual_damage * lifesteal_ratio)))
            if lifesteal_heal > 0:
                self._append_effect_event(effect_events, f"Lebensraub: +{lifesteal_heal} HP.")

        # HP nicht unter 0
        self.player1_hp = max(0, self.player1_hp)
        self.player2_hp = max(0, self.player2_hp)

        # KAMPF-LOG SYSTEM: (wir loggen nach Effektanwendung, damit Verwirrung inline stehen kann)
        self.round_counter += 1

        if not is_reload_action:
            self.activate_delayed_defense_after_attack(
                self.current_turn,
                effect_events,
                attack_landed=bool(attack_hits_enemy and int(actual_damage or 0) > 0),
            )

        # SIDE EFFECTS: Apply new effects from attack
        effects = attack.get("effects", [])
        confusion_applied = False
        burning_duration_for_dynamic_cooldown: int | None = None
        for effect in effects:
            # 70% Fix-Chance für Verwirrung
            chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 1.0)
            if random.random() >= chance:
                continue
            target = effect.get("target", "enemy")
            target_id = self.current_turn if target == "self" else defender_id
            eff_type = effect.get("type")
            if target != "self" and not attack_hits_enemy and eff_type not in {"stun"}:
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif eff_type == "burning":
                duration = _random_int_from_range(effect.get("duration"), default=1)
                burn_damage = _effect_int(effect, "damage")
                new_effect: dict[str, object] = {
                    'type': 'burning',
                    'duration': duration,
                    'damage': burn_damage,
                    'applier': self.current_turn
                }
                self.active_effects[target_id].append(new_effect)
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == "confusion":
                # Confuse defender for next turn + UI marker
                self.set_confusion(target_id, self.current_turn)
                confusion_applied = True
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = int(effect.get("amount", 0) or 0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_flat_bonus[target_id] = max(self.pending_flat_bonus.get(target_id, 0), amount)
                self.pending_flat_bonus_uses[target_id] = max(self.pending_flat_bonus_uses.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e)."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff verursacht Maximalschaden."))
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff trifft garantiert."))
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n))."),
                )
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1, source=attack_name)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Block-Sequenz vorbereitet: {seq_text}."))
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n))."),
                )
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden."),
                )
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1, source=attack_name)
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert und {reflect_pct}% des verhinderten Schadens werden zurückgeworfen.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Absorption aktiv: Verhinderter Schaden wird gespeichert."))
            elif eff_type == "cap_damage":
                cap_setting = effect.get("max_damage", 0)
                if str(cap_setting).strip().lower() == "attack_min":
                    self.queue_incoming_modifier(target_id, cap="attack_min", turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, "Schadenslimit aktiv: Nächster Treffer wird auf dessen Mindestschaden begrenzt."),
                    )
                else:
                    max_damage = int(cap_setting or 0)
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                self.special_lock_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Spezialfähigkeiten des Gegners sind nächste Runde gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = int(effect.get("heal", 0) or 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": self.current_turn})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: +{heal} HP für {turns} Runde(n).")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                heal_amount = int(effect.get("heal", 0) or 0)
                if random.random() < 0.5:
                    healed_mix = self.heal_player(target_id, heal_amount)
                    if healed_mix > 0:
                        self._append_effect_event(effect_events, f"Awesome Mix: +{healed_mix} HP.")
                else:
                    self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), 1)
                    self._append_effect_event(effect_events, "Awesome Mix: Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = int(effect.get("counter", 0) or 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )

            # Kein separater Log-Eintrag mehr – Effekt wird in der Angriffszeile signalisiert

        if self.special_lock_next_turn.get(self.current_turn, False):
            self.special_lock_next_turn[self.current_turn] = False

        if not is_forced_landing:
            if not is_reload_action and attack.get("requires_reload"):
                self.set_reload_needed(self.current_turn, attack_index, True)

            # COOLDOWN-SYSTEM: Kartenspezifisch oder für starke Attacken
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = attack.get("cooldown_turns")
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(attack_index, 0)
                self.attack_cooldowns[previous_turn][attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and isinstance(custom_cooldown_turns, int) and custom_cooldown_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(attack_index, 0)
                self.attack_cooldowns[previous_turn][attack_index] = max(current_cd, custom_cooldown_turns)
            elif self.is_strong_attack(base_damage, damage_buff):
                # Starke Attacke - 2 Züge Cooldown
                previous_turn = self.current_turn
                self.start_attack_cooldown(previous_turn, attack_index)
        else:
            landing_cd_index = forced_landing_attack.get("cooldown_attack_index")
            landing_cd_turns = int(forced_landing_attack.get("cooldown_turns", 0) or 0)
            if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(landing_cd_index, 0)
                self.attack_cooldowns[previous_turn][landing_cd_index] = max(current_cd, landing_cd_turns)
        
        # Log now including confusion/self-hit if it applied
        attacker_remaining_hp = self._hp_for(self.current_turn)
        defender_remaining_hp = self.player2_hp if self.current_turn == self.player1_id else self.player1_hp
        await self._record_battle_log(
            attacker_card,
            defender_card,
            attack_name,
            actual_damage,
            is_critical,
            attacker_user,
            defender_user,
            self.round_counter,
            defender_remaining_hp,
            attacker_remaining_hp=attacker_remaining_hp,
            pre_effect_damage=pre_burn_total,
            confusion_applied=confusion_applied,
            self_hit_damage=(self_damage if not attack_hits_enemy and 'self_damage' in locals() else 0),
            attacker_status_icons=self._status_icons(self.current_turn),
            defender_status_icons=self._status_icons(defender_id),
            effect_events=effect_events,
        )
        if self.airborne_pending_landing.get(defender_id):
            self._consume_airborne_evade_marker(defender_id)

        # Nach dem Log-Eintrag auf Kampfende prüfen, damit der finale Treffer immer im Log landet.
        if self.player1_hp <= 0 or self.player2_hp <= 0:
            if self.player2_hp <= 0:
                winner_id = self.player1_id
                winner_user = _get_member_if_available(guild, self.player1_id)
                winner_card = self.player1_card["name"]
                loser_id = self.player2_id
                loser_user = _get_member_if_available(guild, self.player2_id)
                loser_card = self.player2_card["name"]
            else:
                winner_id = self.player2_id
                winner_user = _get_member_if_available(guild, self.player2_id)
                winner_card = self.player2_card["name"]
                loser_id = self.player1_id
                loser_user = _get_member_if_available(guild, self.player1_id)
                loser_card = self.player1_card["name"]
            if winner_user:
                winner_mention = winner_user.mention
            else:
                winner_mention = "Bot" if winner_id == 0 else f"<@{winner_id}>"
            if loser_user:
                loser_mention = loser_user.mention
            else:
                loser_mention = "Bot" if loser_id == 0 else f"<@{loser_id}>"
            winner_embed = self._winner_embed(winner_mention, winner_card, loser_mention, loser_card)
            final_battle_message = message
            if self.ui_needs_resend:
                final_battle_message = await self._repost_battle_ui_if_needed(
                    interaction.channel,
                    interaction=interaction,
                    current_message=message,
                    battle_embed=self._thread_finished_embed(),
                    view=None,
                    status="completed",
                )
            elif message is not None:
                try:
                    await message.edit(embed=self._thread_finished_embed(), view=None)
                except Exception:
                    logging.exception("Failed to update fight thread end-state")
            await self._post_winner_public(guild, interaction.channel, winner_embed)
            try:
                await self._send_feedback_prompt(interaction.channel, guild)
            except Exception:
                logging.exception("Unexpected error")
            try:
                await self.persist_session(
                    interaction.channel,
                    status="completed",
                    battle_message=final_battle_message,
                )
            except Exception:
                logging.exception("Failed to persist completed fight session")
            self.stop()
            return

        # Nächster Spieler
        previous_turn = self.current_turn
        self.current_turn = self.player2_id if self.current_turn == self.player1_id else self.player1_id
        
        # COOLDOWN-SYSTEM: Reduziere Cooldowns am START des neuen Zugs
        self.reduce_cooldowns(self.current_turn)
        
        # Attacken-Buttons für den neuen Spieler aktualisieren
        await self.update_attack_buttons()
        
        # Neues Kampf-Embed erstellen
        user1 = _get_member_if_available(guild, self.player1_id)
        user2 = _get_member_if_available(guild, self.player2_id)
        battle_embed = create_battle_embed(
            self.player1_card,
            self.player2_card,
            self.player1_hp,
            self.player2_hp,
            self.current_turn,
            user1,
            user2,
            self.active_effects,
            current_attack_infos=self._current_attack_infos(),
            recent_log_lines=self._recent_log_lines,
            highlight_tone=self._last_highlight_tone,
        )
        
        # Aktualisiere Kampf-UI (Kampf-Log wurde bereits oben behandelt)
        if self.ui_needs_resend:
            message = await self._repost_battle_ui_if_needed(
                interaction.channel,
                interaction=interaction,
                current_message=message,
                battle_embed=battle_embed,
                view=self,
                status="active",
            )
        elif message is not None:
            try:
                await message.edit(embed=battle_embed, view=self)
                await self.persist_session(interaction.channel, status="active", battle_message=message)
            except Exception:
                replacement = await _safe_send_channel(interaction, interaction.channel, embed=battle_embed, view=self)
                if replacement is not None:
                    await self.persist_session(interaction.channel, status="active", battle_message=replacement)
        else:
            replacement = await _safe_send_channel(interaction, interaction.channel, embed=battle_embed, view=self)
            if replacement is not None:
                await self.persist_session(interaction.channel, status="active", battle_message=replacement)
        
        # BOT-ANGRIFF: Wenn der Bot an der Reihe ist, führe automatischen Angriff aus
        if self.current_turn == 0:  # Bot ist an der Reihe
            if message is not None:
                await self.execute_bot_attack(message)

    async def execute_bot_attack(self, message):
        """Führt einen automatischen Bot-Angriff aus"""
        # SIDE EFFECTS: Apply effects on player before bot attack
        effect_events: list[str] = []
        defender_id = self.player1_id
        effects_to_remove = []
        pre_burn_total = 0
        for effect in self.active_effects[defender_id]:
            if effect.get('applier') == 0 and effect.get('type') == 'burning':  # Bot applier is 0
                damage = _effect_int(effect, 'damage')
                self.player1_hp -= damage
                self.player1_hp = max(0, self.player1_hp)
                pre_burn_total += damage

                # Kein separater Burn-Log – wird inline in der folgenden Attacke gezeigt

                # Decrease duration
                remaining_duration = _effect_int(effect, 'duration') - 1
                effect['duration'] = remaining_duration
                if remaining_duration <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        if self.stunned_next_turn.get(0, False):
            self.stunned_next_turn[0] = False
            if self.airborne_pending_landing.get(self.player1_id):
                self._consume_airborne_evade_marker(self.player1_id)
            self.current_turn = self.player1_id
            self.reduce_cooldowns(self.player1_id)
            await self.update_attack_buttons()
            player_user = _get_member_if_available(message.guild, self.player1_id)
            bot_user = SimpleBotUser()
            battle_embed = create_battle_embed(
                self.player1_card,
                self.player2_card,
                self.player1_hp,
                self.player2_hp,
                self.current_turn,
                player_user,
                bot_user,
                self.active_effects,
                current_attack_infos=self._current_attack_infos(),
                recent_log_lines=self._recent_log_lines,
                highlight_tone=self._last_highlight_tone,
            )
            battle_embed.description = (battle_embed.description or "") + "\n\n🛑 Bot war betäubt und hat seinen Zug ausgesetzt."
            await message.edit(embed=battle_embed, view=self)
            return

        # Hole Bot-Karte und verfügbare Attacken
        bot_card = self.player2_card
        attacks = bot_card.get("attacks", [{"name": "Punch", "damage": [15, 25]}])
        forced_landing_attack = self.resolve_forced_landing_if_due(0, effect_events)
        is_forced_landing = forced_landing_attack is not None
        if is_forced_landing:
            attack_index = -1
            attack = forced_landing_attack
        else:
            # Regelbasierte KI-Auswahl statt reinem Max-Schaden.
            attack_index = self._choose_bot_attack_index(attacks)
            attack = attacks[attack_index]
        base_damage = attack["damage"]
        damage_buff = 0
        attacker_hp = self._hp_for(0)
        attacker_max_hp = self._max_hp_for(0)
        defender_hp = self._hp_for(self.player1_id)
        defender_max_hp = self._max_hp_for(self.player1_id)
        conditional_self_pct = attack.get("bonus_if_self_hp_below_pct")
        conditional_self_bonus = int(attack.get("bonus_damage_if_condition", 0) or 0)
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * float(conditional_self_pct)):
            damage_buff += conditional_self_bonus
        conditional_enemy_triggered = False
        conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
            conditional_enemy_triggered = True
            damage_if_condition = attack.get("damage_if_condition")
            base_damage = _coerce_damage_input(damage_if_condition, default=0)
        if attack.get("add_absorbed_damage"):
            absorbed_bonus = int(self.absorbed_damage.get(0, 0) or 0)
            damage_buff += absorbed_bonus
            self.absorbed_damage[0] = 0
            base_min, base_max = _range_pair(base_damage)
            base_text = str(base_min) if base_min == base_max else f"{base_min}-{base_max}"
            self._append_effect_event(
                effect_events,
                f"Kinetische Entladung: Grundschaden {base_text}, durch Absorption +{absorbed_bonus}.",
            )
        is_damaging_attack = self.get_attack_max_damage(base_damage, 0) > 0
        attack_multiplier = 1.0
        applied_flat_bonus_now = 0
        force_max_damage = False
        if is_damaging_attack:
            if self.pending_flat_bonus_uses.get(0, 0) > 0:
                flat_bonus_now = int(self.pending_flat_bonus.get(0, 0))
                damage_buff += flat_bonus_now
                applied_flat_bonus_now = max(0, flat_bonus_now)
                self.pending_flat_bonus_uses[0] -= 1
                if self.pending_flat_bonus_uses[0] <= 0:
                    self.pending_flat_bonus[0] = 0
                if flat_bonus_now > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{flat_bonus_now} Schaden auf diesen Angriff.")
            if self.pending_multiplier_uses.get(0, 0) > 0:
                attack_multiplier = float(self.pending_multiplier.get(0, 1.0) or 1.0)
                self.pending_multiplier_uses[0] -= 1
                if self.pending_multiplier_uses[0] <= 0:
                    self.pending_multiplier[0] = 1.0
                multiplier_pct = int(round((attack_multiplier - 1.0) * 100))
                if multiplier_pct > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{multiplier_pct}% Schaden auf diesen Angriff.")
            if self.force_max_next.get(0, 0) > 0:
                force_max_damage = True
                self.force_max_next[0] -= 1
        guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)
        is_reload_action = bool((not is_forced_landing) and attack.get("requires_reload") and self.is_reload_needed(0, attack_index))
        attack_name = str(attack.get("reload_name") or "Nachladen") if is_reload_action else attack["name"]

        # Confusion: 77% Selbstschaden, 23% normaler Treffer
        bot_hits_enemy = True
        self_damage = 0
        if is_reload_action:
            actual_damage, is_critical = 0, False
            bot_hits_enemy = False
            self.set_reload_needed(0, attack_index, False)
        else:
            min_damage = 0
            max_damage = 0
            defender_has_stealth = self.has_stealth(self.player1_id)
            guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(0)
            if guaranteed_hit:
                self.blind_next_attack[0] = 0.0
                self.consume_confusion_if_any(0)
                self._append_effect_event(effect_events, "Dieser Angriff trifft garantiert.")
            blind_chance = float(self.blind_next_attack.get(0, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[0] = 0.0
                blind_miss = random.random() < blind_chance
            max_damage_threshold = self.get_attack_max_damage(base_damage, damage_buff)
            if blind_miss:
                actual_damage, is_critical = 0, False
                bot_hits_enemy = False
                if self.confused_next_turn.get(0, False):
                    try:
                        self.active_effects[0] = [e for e in self.active_effects.get(0, []) if e.get('type') != 'confusion']
                    except Exception:
                        logging.exception("Unexpected error")
                    self.confused_next_turn[0] = False
            elif self.confused_next_turn.get(0, False):
                if random.random() < 0.77:
                    self_damage = random.randint(15, 20) if max_damage_threshold <= 100 else random.randint(40, 60)
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        0,
                        self_damage,
                        source="Verwirrung",
                        self_damage=True,
                    )
                    actual_damage, is_critical = 0, False
                    bot_hits_enemy = False
                else:
                    actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                        attack,
                        base_damage,
                        damage_buff,
                        attack_multiplier,
                        force_max_damage,
                        guaranteed_hit,
                    )
                    self._append_multi_hit_roll_event(effect_events)
                    if defender_has_stealth and not guaranteed_hit:
                        actual_damage = 0
                        is_critical = False
                        bot_hits_enemy = False
                        self.consume_stealth(self.player1_id)
                    elif defender_has_stealth:
                        self.consume_stealth(self.player1_id)
                # Confusion verbrauchen + UI Icon entfernen
                try:
                    self.active_effects[0] = [e for e in self.active_effects.get(0, []) if e.get('type') != 'confusion']
                except Exception:
                    logging.exception("Unexpected error")
                self.confused_next_turn[0] = False
            else:
                # Berechne Schaden normal
                actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                    attack,
                    base_damage,
                    damage_buff,
                    attack_multiplier,
                    force_max_damage,
                    guaranteed_hit,
                )
                self._append_multi_hit_roll_event(effect_events)
                if defender_has_stealth and not guaranteed_hit:
                    actual_damage = 0
                    is_critical = False
                    bot_hits_enemy = False
                    self.consume_stealth(self.player1_id)
                elif defender_has_stealth:
                    self.consume_stealth(self.player1_id)

            if bot_hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                defender_hp_before = self._hp_for(self.player1_id)
                reduced_damage, overflow_self_damage, outgoing_modifier = self.apply_outgoing_attack_modifiers(
                    0,
                    actual_damage,
                )
                if reduced_damage != actual_damage:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._append_effect_event(
                        effect_events,
                        _damage_transition_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                            context="Ausgehende Reduktion",
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        0,
                        overflow_self_damage,
                        source="Überlauf-Rückstoß",
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False

                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(self.player1_id, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    self.player1_id,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(self.player1_id)),
                    incoming_min_damage=min_damage,
                )
                absorbed_after = int(self.absorbed_damage.get(self.player1_id, 0) or 0)
                self._append_incoming_resolution_events(
                    effect_events,
                    defender_name=self.player1_card["name"],
                    raw_damage=incoming_raw_damage,
                    final_damage=int(final_damage),
                    reflected_damage=int(reflected_damage),
                    dodged=bool(dodged),
                    counter_damage=int(counter_damage),
                    modifier_details=incoming_modifier,
                    absorbed_before=absorbed_before,
                    absorbed_after=absorbed_after,
                )
                if dodged:
                    actual_damage = 0
                    bot_hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    if actual_damage > 0:
                        self._apply_non_heal_damage(self.player1_id, actual_damage)
                    else:
                        is_critical = False
                if reflected_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        0,
                        reflected_damage,
                        source="Reflexions-Rückschaden",
                        self_damage=False,
                    )
                if counter_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        0,
                        counter_damage,
                        source="Konter-Rückschaden",
                        self_damage=False,
                    )
                self._guard_non_heal_damage_result(self.player1_id, defender_hp_before, "pvp_bot_attack")
            if not bot_hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            self._apply_non_heal_damage_with_event(
                effect_events,
                0,
                self_damage_value,
                source=f"{attack_name} / Rückstoß",
                self_damage=True,
            )

        heal_data = attack.get("heal")
        if heal_data is not None:
            heal_amount = _random_int_from_range(heal_data)
            healed_now = self.heal_player(0, heal_amount)
            if healed_now > 0:
                self._append_effect_event(effect_events, f"Heilung: +{healed_now} HP.")

        lifesteal_ratio = float(attack.get("lifesteal_ratio", 0.0) or 0.0)
        if lifesteal_ratio > 0 and bot_hits_enemy and actual_damage > 0:
            lifesteal_heal = self.heal_player(0, int(round(actual_damage * lifesteal_ratio)))
            if lifesteal_heal > 0:
                self._append_effect_event(effect_events, f"Lebensraub: +{lifesteal_heal} HP.")

        self.player1_hp = max(0, self.player1_hp)
        self.player2_hp = max(0, self.player2_hp)

        # Aktualisiere Kampf-Log
        self.round_counter += 1

        # Erstelle Bot-User-Objekt für das Log
        bot_user = SimpleBotUser()
        player_user = _get_member_if_available(message.guild, self.player1_id)

        if not is_reload_action:
            self.activate_delayed_defense_after_attack(
                0,
                effect_events,
                attack_landed=bool(bot_hits_enemy and int(actual_damage or 0) > 0),
            )

        # SIDE EFFECTS: Apply new effects from bot attack (nur wenn Treffer)
        effects = attack.get("effects", [])
        burning_duration_for_dynamic_cooldown: int | None = None
        for effect in effects:
            chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 1.0)
            if random.random() >= chance:
                continue
            target = effect.get("target", "enemy")
            target_id = 0 if target == "self" else self.player1_id
            eff_type = effect.get("type")
            if target != "self" and not bot_hits_enemy and eff_type not in {"stun"}:
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif eff_type == 'burning':
                duration = _random_int_from_range(effect.get("duration"), default=1)
                burn_damage = _effect_int(effect, "damage")
                new_effect: dict[str, object] = {
                    'type': 'burning',
                    'duration': duration,
                    'damage': burn_damage,
                    'applier': 0
                }
                self.active_effects[target_id].append(new_effect)
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == 'confusion':
                self.set_confusion(target_id, 0)
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = int(effect.get("amount", 0) or 0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_flat_bonus[target_id] = max(self.pending_flat_bonus.get(target_id, 0), amount)
                self.pending_flat_bonus_uses[target_id] = max(self.pending_flat_bonus_uses.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e)."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff verursacht Maximalschaden."))
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff trifft garantiert."))
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n))."),
                )
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1, source=attack_name)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Block-Sequenz vorbereitet: {seq_text}."))
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n))."),
                )
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden."),
                )
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1, source=attack_name)
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert und {reflect_pct}% des verhinderten Schadens werden zurückgeworfen.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Absorption aktiv: Verhinderter Schaden wird gespeichert."))
            elif eff_type == "cap_damage":
                cap_setting = effect.get("max_damage", 0)
                if str(cap_setting).strip().lower() == "attack_min":
                    self.queue_incoming_modifier(target_id, cap="attack_min", turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, "Schadenslimit aktiv: Nächster Treffer wird auf dessen Mindestschaden begrenzt."),
                    )
                else:
                    max_damage = int(cap_setting or 0)
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                self.special_lock_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Spezialfähigkeiten des Gegners sind nächste Runde gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = int(effect.get("heal", 0) or 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": 0})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: +{heal} HP für {turns} Runde(n).")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                heal_amount = int(effect.get("heal", 0) or 0)
                if random.random() < 0.5:
                    healed_mix = self.heal_player(target_id, heal_amount)
                    if healed_mix > 0:
                        self._append_effect_event(effect_events, f"Awesome Mix: +{healed_mix} HP.")
                else:
                    self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), 1)
                    self._append_effect_event(effect_events, "Awesome Mix: Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = int(effect.get("counter", 0) or 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )
        # Kein separater Log-Eintrag – Effekte werden inline in der Angriffszeile angezeigt

        await self._record_battle_log(
            bot_card["name"],
            self.player1_card["name"],
            attack_name,
            actual_damage,
            is_critical,
            bot_user,
            player_user,
            self.round_counter,
            self.player1_hp,
            attacker_remaining_hp=self._hp_for(0),
            pre_effect_damage=pre_burn_total,
            confusion_applied=False,
            self_hit_damage=(self_damage if not bot_hits_enemy and 'self_damage' in locals() else 0),
            attacker_status_icons=self._status_icons(0),
            defender_status_icons=self._status_icons(self.player1_id),
            effect_events=effect_events,
        )
        if self.airborne_pending_landing.get(self.player1_id):
            self._consume_airborne_evade_marker(self.player1_id)

        if (not is_forced_landing) and (not is_reload_action) and attack.get("requires_reload"):
            self.set_reload_needed(0, attack_index, True)

        if self.special_lock_next_turn.get(0, False):
            self.special_lock_next_turn[0] = False

        if not is_forced_landing:
            # Cooldown für Bot-Attacke
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = attack.get("cooldown_turns")
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                current_cd = self.attack_cooldowns[0].get(attack_index, 0)
                self.attack_cooldowns[0][attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and isinstance(custom_cooldown_turns, int) and custom_cooldown_turns > 0:
                current_cd = self.attack_cooldowns[0].get(attack_index, 0)
                self.attack_cooldowns[0][attack_index] = max(current_cd, custom_cooldown_turns)
            elif self.is_strong_attack(base_damage, damage_buff):
                self.start_attack_cooldown(0, attack_index)
        else:
            landing_cd_index = forced_landing_attack.get("cooldown_attack_index")
            landing_cd_turns = int(forced_landing_attack.get("cooldown_turns", 0) or 0)
            if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
                current_cd = self.attack_cooldowns[0].get(landing_cd_index, 0)
                self.attack_cooldowns[0][landing_cd_index] = max(current_cd, landing_cd_turns)

        if self.player1_hp <= 0 or self.player2_hp <= 0:
            if self.player2_hp <= 0:
                winner_id = self.player1_id
                winner_user = _get_member_if_available(message.guild, self.player1_id)
                winner_card = self.player1_card["name"]
                loser_id = self.player2_id
                loser_user = _get_member_if_available(message.guild, self.player2_id)
                loser_card = self.player2_card["name"]
            else:
                winner_id = self.player2_id
                winner_user = _get_member_if_available(message.guild, self.player2_id)
                winner_card = self.player2_card["name"]
                loser_id = self.player1_id
                loser_user = _get_member_if_available(message.guild, self.player1_id)
                loser_card = self.player1_card["name"]
            if winner_user:
                winner_mention = winner_user.mention
            else:
                winner_mention = "Bot" if winner_id == 0 else f"<@{winner_id}>"
            if loser_user:
                loser_mention = loser_user.mention
            else:
                loser_mention = "Bot" if loser_id == 0 else f"<@{loser_id}>"
            winner_embed = self._winner_embed(winner_mention, winner_card, loser_mention, loser_card)
            final_battle_message = message
            if self.ui_needs_resend:
                final_battle_message = await self._repost_battle_ui_if_needed(
                    message.channel,
                    interaction=None,
                    current_message=message,
                    battle_embed=self._thread_finished_embed(),
                    view=None,
                    status="completed",
                )
            else:
                try:
                    await message.edit(embed=self._thread_finished_embed(), view=None)
                except Exception:
                    logging.exception("Failed to update fight thread end-state")
            await self._post_winner_public(message.guild, message.channel, winner_embed)
            try:
                await self._send_feedback_prompt(message.channel, message.guild)
            except Exception:
                logging.exception("Unexpected error")
            try:
                await self.persist_session(
                    message.channel,
                    status="completed",
                    battle_message=final_battle_message,
                )
            except Exception:
                logging.exception("Failed to persist completed fight session")
            self.stop()
            return

        # Wechsle zu Spieler
        self.current_turn = self.player1_id

        # Reduziere Cooldowns für Spieler
        self.reduce_cooldowns(self.player1_id)

        # Aktualisiere Buttons
        await self.update_attack_buttons()

        # Erstelle neues Embed
        battle_embed = create_battle_embed(
            self.player1_card,
            self.player2_card,
            self.player1_hp,
            self.player2_hp,
            self.current_turn,
            player_user,
            bot_user,
            self.active_effects,
            current_attack_infos=self._current_attack_infos(),
            recent_log_lines=self._recent_log_lines,
            highlight_tone=self._last_highlight_tone,
        )

        # Aktualisiere Kampf-UI
        await message.edit(embed=battle_embed, view=self)

class CardSelectView(RestrictedView):
    def __init__(self, user_id, karten_liste, anzahl):
        super().__init__(timeout=90)
        self.user_id = user_id
        self.value = None
        options = [SelectOption(label=k[0], value=k[0]) for k in karten_liste]
        self.select = ui.Select(placeholder=f"Wähle {anzahl} Karte(n)...", min_values=anzahl, max_values=anzahl, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann Karten wählen!", ephemeral=True)
            return
        self.value = self.select.values
        self.stop()
        await interaction.response.defer()


class FightCardSelectView(DurableView):
    durable_view_kind = VIEW_KIND_FIGHT_CARD_SELECT

    def __init__(
        self,
        challenger_id: int,
        challenged_id: int,
        challenger_card_name: str,
        challenged_card_options,
        *,
        origin_channel_id: int | None,
        thread_id: int | None,
        thread_created: bool,
    ):
        super().__init__(timeout=None)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.challenger_card_name = challenger_card_name
        self.origin_channel_id = origin_channel_id
        self.thread_id = thread_id
        self.thread_created = thread_created
        self.challenged_card_options = list(challenged_card_options or [])
        options = [
            SelectOption(label=str(name), value=str(name))
            for name in self.challenged_card_options
            if str(name).strip()
        ][:25]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder="Wähle deine Karte für den 1v1 Kampf...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="fight_card_select:pick",
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def durable_payload(self) -> dict[str, Any]:
        return {
            "challenger_id": self.challenger_id,
            "challenged_id": self.challenged_id,
            "challenger_card_name": self.challenger_card_name,
            "challenged_card_options": list(self.challenged_card_options),
            "origin_channel_id": self.origin_channel_id,
            "thread_id": self.thread_id,
            "thread_created": self.thread_created,
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Nur der Herausgeforderte kann die Karte wählen!", ephemeral=True)
            return
        selected_name = str(self.select.values[0] or "").strip()
        if not selected_name or selected_name == "__none__":
            await interaction.response.send_message("❌ Keine gültige Karte verfügbar.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear fight card select view")
        await _start_fight_battle_from_card_selection(
            interaction,
            challenger_id=self.challenger_id,
            challenged_id=self.challenged_id,
            challenger_card_name=self.challenger_card_name,
            challenged_card_name=selected_name,
            origin_channel_id=self.origin_channel_id,
            thread_id=self.thread_id,
            thread_created=self.thread_created,
        )
        self.stop()


# Neue Suchfunktion-Klassen
class UserSearchModal(RestrictedModal):
    def __init__(
        self,
        guild,
        challenger,
        parent_view: object | None = None,
        include_bot_option: bool = True,
        required_role_id: int | None = None,
        exclude_user_id: int | None = None,
        exclude_user_ids: set[int] | None = None,
    ):
        super().__init__(title="🔍 User suchen")
        self.guild = guild
        self.challenger = challenger
        self.requester_id = int(getattr(challenger, "id", challenger))
        self.parent_view = parent_view
        self.include_bot_option = include_bot_option
        self.required_role_id = required_role_id
        self.exclude_user_ids = {
            int(user_id)
            for user_id in (exclude_user_ids or set())
            if str(user_id).strip()
        }
        self.exclude_user_ids.add(int(exclude_user_id or self.requester_id or 0))
    
    search_input = ui.TextInput(
        label="Name eingeben:",
        placeholder="z.B. John, Jane, etc...",
        max_length=50
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        search_term = self.search_input.value.lower().strip()
        
        if not search_term:
            await interaction.response.send_message("❌ Bitte gib einen Namen ein!", ephemeral=True)
            return
        
        # Finde passende User
        matches = []
        for member in self.guild.members:
            if (
                not member.bot
                and member.id not in self.exclude_user_ids
                and (
                    self.required_role_id is None
                    or _member_has_role(member, self.required_role_id)
                )
                and (
                    search_term in member.display_name.lower()
                    or search_term in member.name.lower()
                )
            ):
                matches.append(member)
        
        if not matches:
            await interaction.response.send_message(
                f"❌ Keine User mit '{search_term}' gefunden! Versuche es mit einem anderen Namen.", 
                ephemeral=True
            )
            return
        
        # Zeige Ergebnisse (max 25)
        if len(matches) <= 25:
            options = []
            if self.include_bot_option:
                options.append(SelectOption(label="🤖 Bot", value="bot"))
            for member in matches:
                status_emoji = self.get_status_emoji(member)
                options.append(SelectOption(
                    label=safe_user_option_label(member, prefix=f"{status_emoji} "),
                    value=str(member.id)
                ))
            
            view = UserSearchResultView(self.challenger, options, parent_view=self.parent_view)
            await interaction.response.send_message(
                f"🔍 **Suchergebnisse für '{search_term}':**",
                view=view, ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Zu viele Ergebnisse ({len(matches)}). Bitte spezifischer suchen!",
                ephemeral=True
            )
    
    def get_status_emoji(self, member):
        """Gibt Emoji für Online-Status zurück"""
        if member.status == discord.Status.online:
            return "🟢"
        elif member.status == discord.Status.idle:
            return "🟡"
        elif member.status == discord.Status.dnd:
            return "🔴"
        else:
            return "⚫"

class UserSearchResultView(RestrictedView):
    def __init__(self, challenger, options, parent_view: object | None = None):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.requester_id = int(getattr(challenger, "id", challenger))
        self.value = None
        self.parent_view = parent_view
        
        self.select = ui.Select(placeholder="Wähle einen Gegner aus den Suchergebnissen...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Herausforderer kann den Gegner wählen!", ephemeral=True)
            return
        
        self.value = self.select.values[0]
        # Übergib die Auswahl zurück an die Eltern-View (z. B. OpponentSelectView/AdminUserSelectView) und beende sie
        if self.parent_view is not None:
            try:
                search_handler = getattr(self.parent_view, "handle_search_selection", None)
                if callable(search_handler):
                    handled = search_handler(self.value, interaction)
                    if asyncio.iscoroutine(handled):
                        await handled
                else:
                    setattr(self.parent_view, "value", self.value)
                    stop_callback = getattr(self.parent_view, "stop", None)
                    if callable(stop_callback):
                        stop_callback()
            except Exception:
                logging.exception("Unexpected error")
        self.stop()
        if not interaction.response.is_done():
            await interaction.response.defer()
class OpponentSelectView(RestrictedView):
    def __init__(self, challenger: discord.Member, guild: discord.Guild):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.guild = guild
        self.value = None
        self.all_members = _get_fight_opponent_candidates(guild, challenger)
        
        # Zeige intelligente Auswahl
        self.show_smart_options()
    
    def show_smart_options(self):
        """Zeigt intelligente Optionen basierend auf Server-Größe (mit Status-Kreisen und Präsenz-Sortierung)"""
        def label_with_circle(m: discord.Member) -> str:
            # identische Kreise wie in der Suche: 🟢 🟡 🔴 ⚫
            return safe_user_option_label(m, prefix=f"{_member_status_circle(m)} ")
        
        options = [
            SelectOption(label="🔍 Nach Name suchen", value="search"),
            SelectOption(label="🤖 Bot", value="bot"),
        ]

        if len(self.all_members) <= 23:
            # Kompakte Liste: Suche zuerst, dann Bot, dann alle gültigen Nutzer
            for member in sorted(self.all_members, key=_member_presence_priority):
                options.append(SelectOption(label=label_with_circle(member), value=str(member.id)))
        else:
            # Größere Liste: Suche zuerst, dann Bot, dann häufig sichtbare Nutzer und Vollansicht
            online_like = [m for m in self.all_members if m.status != discord.Status.offline]
            for member in sorted(online_like, key=_member_presence_priority)[:22]:
                options.append(SelectOption(label=label_with_circle(member), value=str(member.id)))
            options.append(SelectOption(label="📋 Alle User anzeigen", value="show_all"))
        
        self.select = ui.Select(placeholder="Wähle einen Gegner...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    
    def get_status_emoji(self, member):
        """Gibt Emoji für Online-Status zurück"""
        if member.status == discord.Status.online:
            return "🟢"
        elif member.status == discord.Status.idle:
            return "🟡"
        elif member.status == discord.Status.dnd:
            return "🔴"
        else:
            return "⚫"
    
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user != self.challenger:
            await interaction.response.send_message("Nur der Herausforderer kann den Gegner wählen!", ephemeral=True)
            return
        
        selected_value = self.select.values[0]
        
        if selected_value == "search":
            # Öffne Suchmodal und verknüpfe mit Parent-View
            modal = UserSearchModal(
                self.guild,
                self.challenger,
                parent_view=self,
                required_role_id=FIGHT_OPPONENT_ROLE_ID,
            )
            await interaction.response.send_modal(modal)
            return
        
        elif selected_value == "show_all":
            # Zeige alle User (mit Paginierung falls nötig)
            if len(self.all_members) <= 25:
                options = [SelectOption(label="🤖 Bot", value="bot")]
                for member in sorted(self.all_members, key=_member_presence_priority):
                    status_emoji = self.get_status_emoji(member)
                    options.append(SelectOption(
                        label=safe_user_option_label(member, prefix=f"{status_emoji} "),
                        value=str(member.id)
                    ))
                
                view = UserSearchResultView(self.challenger, options, parent_view=self)
                await interaction.response.send_message(
                    "📋 **Alle User:**",
                    view=view, ephemeral=True
                )
            else:
                pager = ShowAllMembersPager(self.challenger, self.all_members, parent_view=self, include_bot_option=True)
                await interaction.response.send_message("📋 **Alle User (Seitenweise):**", view=pager, ephemeral=True)
            return
        
        self.value = selected_value
        self.stop()
        await interaction.response.defer()

class AdminUserSelectView(RestrictedView):
    def __init__(self, admin_user_id: int, guild: discord.Guild):
        super().__init__(timeout=60)
        self.admin_user_id = admin_user_id
        self.guild = guild
        self.value = None
        self.all_members = [m for m in guild.members if not m.bot]

        self.show_smart_options()

    def show_smart_options(self):
        options: list[SelectOption] = []
        members_sorted = sorted(self.all_members, key=_member_presence_priority)

        if not members_sorted:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="none"))
        elif len(members_sorted) <= 24:
            # Bis 24 User: alle anzeigen + Suchoption (max. 25 Optionen)
            for member in members_sorted:
                circle = _member_status_circle(member)
                label = safe_user_option_label(member, prefix=f"{circle} ")
                options.append(SelectOption(label=label, value=str(member.id)))
            options.insert(0, SelectOption(label="🔍 Nach Name suchen", value="search"))
        else:
            # Größerer Server: kompakte Liste + Such-/Alle-Optionen (max. 25)
            options.append(SelectOption(label="🔍 Nach Name suchen", value="search"))
            options.append(SelectOption(label="📋 Alle User anzeigen", value="show_all"))
            for member in members_sorted[:23]:
                circle = _member_status_circle(member)
                label = safe_user_option_label(member, prefix=f"{circle} ")
                options.append(SelectOption(label=label, value=str(member.id)))

        self.select = ui.Select(placeholder="Wähle einen Nutzer...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_user_id:
            await interaction.response.send_message("Nur der Admin kann wählen!", ephemeral=True)
            return
        selected = self.select.values[0]
        if selected == "none":
            await interaction.response.send_message("❌ Keine Nutzer verfügbar.", ephemeral=True)
            return
        if selected == "search":
            modal = UserSearchModal(self.guild, interaction.user, parent_view=self, include_bot_option=False)
            await interaction.response.send_modal(modal)
            return
        if selected == "show_all":
            if len(self.all_members) <= 25:
                members_sorted = sorted(self.all_members, key=_member_presence_priority)
                options = [
                    SelectOption(
                        label=safe_user_option_label(m, prefix=f"{_member_status_circle(m)} "),
                        value=str(m.id),
                    )
                    for m in members_sorted
                ]
                view = UserSearchResultView(interaction.user, options, parent_view=self)
                await interaction.response.send_message("📋 Alle User:", view=view, ephemeral=True)
            else:
                pager = ShowAllMembersPager(interaction.user, self.all_members, parent_view=self, include_bot_option=False)
                await interaction.response.send_message("📋 Alle User (Seitenweise):", view=pager, ephemeral=True)
            return
        self.value = selected
        self.stop()
        await interaction.response.defer()


class DustMultiUserSelectView(RestrictedView):
    def __init__(self, requester_id: int, guild: discord.Guild):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.guild = guild
        self.selected_user_ids: list[int] = []
        self.value: list[int] | None = None
        self._message: discord.Message | None = None

        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options(),
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def bind_message(self, message: discord.Message | None) -> None:
        self._message = message

    def _content(self) -> str:
        return "Wähle mehrere Nutzer für Infinitydust. Die Suche steht immer ganz oben."

    def _available_members(self) -> list[discord.Member]:
        chosen = set(self.selected_user_ids)
        members = [
            member
            for member in self.guild.members
            if not member.bot and member.id not in chosen
        ]
        return sorted(members, key=_member_presence_priority)

    def _placeholder(self) -> str:
        selected_count = len(self.selected_user_ids)
        if selected_count <= 0:
            return "Wähle Nutzer oder suche oben..."
        return f"Wähle weitere Nutzer... ({selected_count} ausgewählt)"

    def _selected_summary(self) -> str:
        if not self.selected_user_ids:
            return "Noch niemand ausgewählt."
        names: list[str] = []
        for user_id in self.selected_user_ids:
            member = self.guild.get_member(user_id)
            names.append(safe_display_name(member or str(user_id), fallback=str(user_id)))
        return ", ".join(names)

    def _summary_embed(self) -> discord.Embed:
        available_count = len(self._available_members())
        selected_count = len(self.selected_user_ids)
        embed = discord.Embed(
            title="💎 Multi-Auswahl für Infinitydust",
            description="Speichere mehrere Nutzer und drücke danach auf **Fertig**.",
            color=0x3498DB,
        )
        embed.add_field(name="Ausgewählt", value=str(selected_count), inline=True)
        embed.add_field(name="Noch verfügbar", value=str(available_count), inline=True)
        embed.add_field(name="Gewählte Nutzer", value=self._selected_summary(), inline=False)
        return embed

    def _build_options(self) -> list[SelectOption]:
        options: list[SelectOption] = [
            SelectOption(label="🔍 Nach Name suchen", value="search"),
            SelectOption(label="✅ Fertig", value="done"),
        ]
        for member in self._available_members()[:23]:
            options.append(
                SelectOption(
                    label=safe_user_option_label(member, prefix=f"{_member_status_circle(member)} "),
                    value=str(member.id),
                )
            )
        return options

    async def _refresh_origin_message(self) -> None:
        self.select.options = self._build_options()
        self.select.placeholder = self._placeholder()
        if self._message is not None:
            await self._message.edit(content=self._content(), embed=self._summary_embed(), view=self)

    async def _append_user(
        self,
        interaction: discord.Interaction,
        raw_value: str,
        *,
        edit_origin_with_response: bool,
    ) -> None:
        try:
            user_id = int(raw_value)
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ Ungültiger Nutzer.", ephemeral=True)
            return
        if user_id in self.selected_user_ids:
            await interaction.response.send_message("Dieser Nutzer ist bereits ausgewählt.", ephemeral=True)
            return
        member = self.guild.get_member(user_id)
        if member is None or member.bot:
            await interaction.response.send_message("❌ Nutzer nicht gefunden.", ephemeral=True)
            return
        self.selected_user_ids.append(user_id)
        self.select.options = self._build_options()
        self.select.placeholder = self._placeholder()
        if edit_origin_with_response:
            await interaction.response.edit_message(content=self._content(), embed=self._summary_embed(), view=self)
            return
        await self._refresh_origin_message()
        await interaction.response.send_message(
            f"✅ {safe_display_name(member, fallback=str(user_id))} hinzugefügt.",
            ephemeral=True,
        )

    async def handle_search_selection(self, raw_value: str, interaction: discord.Interaction) -> None:
        await self._append_user(interaction, raw_value, edit_origin_with_response=False)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Command-User kann wählen!", ephemeral=True)
            return

        selected = str(self.select.values[0] or "").strip()
        if selected == "search":
            modal = UserSearchModal(
                self.guild,
                interaction.user,
                parent_view=self,
                include_bot_option=False,
                exclude_user_ids=set(self.selected_user_ids),
            )
            await interaction.response.send_modal(modal)
            return
        if selected == "done":
            if not self.selected_user_ids:
                await interaction.response.send_message("❌ Du musst erst mindestens einen Nutzer auswählen.", ephemeral=True)
                return
            self.value = list(self.selected_user_ids)
            self.stop()
            await interaction.response.defer()
            return
        await self._append_user(interaction, selected, edit_origin_with_response=True)

class FightVisibilityView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value: bool | None = None  # True=privat, False=öffentlich, None=abgebrochen
        self.cancelled: bool = False

    @ui.button(label="Privat", style=discord.ButtonStyle.primary, row=0)
    async def private_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann die Sichtbarkeit wählen!", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer()

    @ui.button(label="Öffentlich", style=discord.ButtonStyle.success, row=0)
    async def public_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann die Sichtbarkeit wählen!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.defer()

    @ui.button(label="Abbrechen", style=discord.ButtonStyle.danger, row=0)
    async def cancel_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann den Kampf abbrechen!", ephemeral=True)
            return
        self.value = None  # Abgebrochen
        self.cancelled = True
        self.stop()
        await interaction.response.defer()

async def _get_member_safe(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None

async def _delete_managed_thread_after_delay(thread: discord.Thread, delay_seconds: int) -> None:
    await asyncio.sleep(max(0, int(delay_seconds or 0)))
    try:
        await update_managed_thread_status(thread.id, "deleted")
        await thread.delete()
    except discord.NotFound:
        return
    except Exception:
        logging.exception("Unexpected error")

async def _maybe_delete_fight_thread(thread_id: int | None, thread_created: bool) -> None:
    if not thread_created or not thread_id:
        return
    try:
        channel = bot.get_channel(thread_id)
        if channel is None:
            channel = await bot.fetch_channel(thread_id)
        if isinstance(channel, discord.Thread):
            delay = _thread_auto_close_delay(CANCELLED_THREAD_AUTO_CLOSE_POLICY)
            if delay:
                try:
                    await channel.send(f"?? Dieser Thread wird in {int(delay)} Sekunden geschlossen.")
                except Exception:
                    logging.exception("Failed to send delayed-close notice for thread %s", channel.id)
                asyncio.create_task(_delete_managed_thread_after_delay(channel, int(delay)))
    except Exception:
        logging.exception("Unexpected error")


async def _create_required_private_fight_thread(
    interaction: discord.Interaction,
    *,
    challenged: discord.Member | None = None,
) -> discord.Thread | None:
    me = _get_bot_member(interaction)
    base_channel = interaction.channel
    parent_text: discord.TextChannel | None = None
    if isinstance(base_channel, discord.TextChannel):
        parent_text = base_channel
    elif isinstance(base_channel, discord.Thread) and isinstance(base_channel.parent, discord.TextChannel):
        parent_text = base_channel.parent
    if parent_text is None:
        await interaction.followup.send(
            f"❌ Privater Kampf konnte nicht gestartet werden. Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren.",
            ephemeral=True,
        )
        return None
    if me is None:
        await interaction.followup.send(
            f"❌ Privater Kampf konnte nicht gestartet werden. Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren.",
            ephemeral=True,
        )
        return None
    perms = parent_text.permissions_for(me)
    if not perms.create_private_threads or not perms.send_messages_in_threads:
        await interaction.followup.send(
            (
                "❌ Ich kann hier keinen privaten Kampf-Thread erstellen "
                f"(fehlende Thread-Rechte). Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren."
            ),
            ephemeral=True,
        )
        return None
    try:
        thread_name = safe_thread_name("Privater Kampf:", safe_display_name(interaction.user, fallback=str(interaction.user.id)))
        if challenged is not None:
            thread_name = safe_thread_name(
                "Privater Kampf:",
                safe_display_name(interaction.user, fallback=str(interaction.user.id)),
                "vs",
                safe_display_name(challenged, fallback=str(challenged.id)),
            )
        fight_thread = await parent_text.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=True,
        )
        await fight_thread.add_user(interaction.user)
        if challenged is not None:
            try:
                await fight_thread.add_user(challenged)
            except discord.Forbidden:
                logging.warning(
                    "Failed to add challenged user %s to private fight thread %s; falling back to public channel.",
                    challenged.id,
                    fight_thread.id,
                )
                await fight_thread.delete()
                await interaction.followup.send(
                    (
                        "❌ Ich konnte den privaten Thread nicht für beide Nutzer öffnen. "
                        "Bitte prüfe Kanalrechte für den Gegner oder starte den Kampf im normalen Kanal."
                    ),
                    ephemeral=True,
                )
                return None
        await save_managed_thread(
            thread_id=fight_thread.id,
            guild_id=interaction.guild.id if interaction.guild else 0,
            kind=THREAD_KIND_FIGHT,
        )
        return fight_thread
    except Exception:
        logging.exception("Failed to create private fight thread")
        await interaction.followup.send(
            f"❌ Privater Kampf konnte nicht gestartet werden. Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren.",
            ephemeral=True,
        )
        return None


async def _mission_thread_admin_members(guild: discord.Guild | None) -> list[discord.Member]:
    if guild is None:
        return []
    members_by_id: dict[int, discord.Member] = {}
    owner_id = guild.owner_id
    if owner_id is not None:
        owner = await _get_member_safe(guild, owner_id)
        if owner is not None:
            members_by_id[owner.id] = owner
    for member in guild.members:
        if member.bot:
            continue
        if member.id == BASTI_USER_ID:
            members_by_id[member.id] = member
            continue
        if member.guild_permissions.administrator:
            members_by_id[member.id] = member
            continue
        role_ids = _member_role_ids(member)
        if MFU_ADMIN_ROLE_ID in role_ids or OWNER_ROLE_ROLE_ID in role_ids or DEV_ROLE_ID in role_ids:
            members_by_id[member.id] = member
    return list(members_by_id.values())


async def _create_required_private_mission_thread(interaction: discord.Interaction) -> discord.Thread | None:
    me = _get_bot_member(interaction)
    base_channel = interaction.channel
    parent_text: discord.TextChannel | None = None
    if isinstance(base_channel, discord.TextChannel):
        parent_text = base_channel
    elif isinstance(base_channel, discord.Thread) and isinstance(base_channel.parent, discord.TextChannel):
        parent_text = base_channel.parent
    if parent_text is None or me is None:
        await interaction.followup.send(
            f"❌ Privater Missions-Thread konnte nicht erstellt werden. Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren.",
            ephemeral=True,
        )
        return None
    perms = parent_text.permissions_for(me)
    if not perms.create_private_threads or not perms.send_messages_in_threads:
        await interaction.followup.send(
            (
                "❌ Ich kann hier keinen privaten Missions-Thread erstellen "
                f"(fehlende Thread-Rechte). Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren."
            ),
            ephemeral=True,
        )
        return None
    try:
        thread_name = safe_thread_name("Mission:", safe_display_name(interaction.user, fallback=str(interaction.user.id)))
        mission_thread = await parent_text.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=True,
        )
        await mission_thread.add_user(interaction.user)
        for admin_member in await _mission_thread_admin_members(interaction.guild):
            if admin_member.id == interaction.user.id:
                continue
            try:
                await mission_thread.add_user(admin_member)
            except discord.Forbidden:
                logging.warning(
                    "Failed to add admin member %s to mission thread %s",
                    admin_member.id,
                    mission_thread.id,
                )
        await save_managed_thread(
            thread_id=mission_thread.id,
            guild_id=interaction.guild.id if interaction.guild else 0,
            kind=THREAD_KIND_MISSION,
        )
        return mission_thread
    except Exception:
        logging.exception("Failed to create private mission thread")
        await interaction.followup.send(
            f"❌ Privater Missions-Thread konnte nicht gestartet werden. Bitte {interaction.user.mention} und <@{BASTI_USER_ID}> informieren.",
            ephemeral=True,
        )
        return None

async def _start_fight_card_selection_from_challenge(
    interaction: discord.Interaction,
    *,
    challenger_id: int,
    challenged_id: int,
    challenger_card_name: str,
    origin_channel_id: int | None,
    thread_id: int | None,
    thread_created: bool,
) -> None:
    if interaction.guild is None:
        await interaction.followup.send(SERVER_ONLY, ephemeral=True)
        return
    challenger = await _get_member_safe(interaction.guild, challenger_id)
    challenged = await _get_member_safe(interaction.guild, challenged_id)
    if not challenger or not challenged:
        await interaction.followup.send("❌ Nutzer nicht gefunden. Bitte erneut herausfordern.", ephemeral=True)
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    challenger_card = await get_karte_by_name(challenger_card_name)
    if not challenger_card:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"❌ Karte von {challenger.mention} nicht gefunden. Bitte erneut herausfordern.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    gegner_karten_liste = _sort_user_cards_like_karten(await get_user_karten(challenged.id))
    if not gegner_karten_liste:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"❌ {challenged.mention} hat keine Karten! Kampf abgebrochen.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    option_names = [name for name, _amount in gegner_karten_liste]
    gegner_card_select_view = FightCardSelectView(
        challenger.id,
        challenged.id,
        challenger_card_name,
        option_names,
        origin_channel_id=origin_channel_id,
        thread_id=thread_id,
        thread_created=thread_created,
    )
    if await _safe_send_channel(
        interaction,
        interaction.channel,
        content=f"{challenged.mention}, wähle deine Karte für den 1v1 Kampf:",
        view=gegner_card_select_view,
    ) is None:
        return


async def _start_fight_battle_from_card_selection(
    interaction: discord.Interaction,
    *,
    challenger_id: int,
    challenged_id: int,
    challenger_card_name: str,
    challenged_card_name: str,
    origin_channel_id: int | None,
    thread_id: int | None,
    thread_created: bool,
) -> None:
    if interaction.guild is None:
        return
    challenger = await _get_member_safe(interaction.guild, challenger_id)
    challenged = await _get_member_safe(interaction.guild, challenged_id)
    if not challenger or not challenged:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content="❌ Nutzer nicht gefunden. Bitte erneut herausfordern.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    challenger_card = await get_karte_by_name(challenger_card_name)
    challenged_card = await get_karte_by_name(challenged_card_name)
    if not challenger_card or not challenged_card:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content="❌ Eine der ausgewählten Karten konnte nicht gefunden werden. Kampf abgebrochen.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    battle_view = BattleView(
        challenger_card,
        challenged_card,
        challenger.id,
        challenged.id,
        None,
        public_result_channel_id=origin_channel_id,
    )
    await battle_view.init_with_buffs()
    log_message = await _safe_send_channel(
        interaction,
        interaction.channel,
        embed=create_battle_log_embed(),
    )
    if isinstance(log_message, discord.Message):
        battle_view.battle_log_message = log_message

    embed = create_battle_embed(
        challenger_card,
        challenged_card,
        battle_view.player1_hp,
        battle_view.player2_hp,
        challenger.id,
        challenger,
        challenged,
        current_attack_infos=_build_attack_info_lines(challenger_card),
        recent_log_lines=battle_view._recent_log_lines,
        highlight_tone=battle_view._last_highlight_tone,
    )
    battle_message = await _safe_send_channel(interaction, interaction.channel, embed=embed, view=battle_view)
    if battle_message is not None:
        await battle_view.persist_session(interaction.channel, status="active", battle_message=battle_message)


async def _send_mission_feedback_prompt(
    channel: object,
    guild: discord.Guild | None,
    *,
    allowed_user_id: int,
    battle_log_text: str,
    auto_close_policy: ThreadAutoClosePolicy | None = MISSION_THREAD_AUTO_CLOSE_POLICY,
) -> None:
    sendable_channel = _coerce_sendable_channel(channel)
    if sendable_channel is None:
        return
    policy = _copy_thread_auto_close_policy(auto_close_policy)
    view = FightFeedbackView(
        sendable_channel,
        guild,
        {allowed_user_id},
        battle_log_text=battle_log_text,
        auto_close_delay=_thread_auto_close_delay(policy),
        close_on_idle=bool(policy and policy.get("close_on_idle", False)),
        close_after_no_bug=bool(policy and policy.get("close_after_no_bug", True)),
        keep_open_after_bug=bool(policy and policy.get("keep_open_after_bug", True)),
    )
    auto_close_hint = ""
    if isinstance(sendable_channel, discord.Thread):
        hint = _thread_auto_close_hint(policy)
        if hint:
            auto_close_hint = f"\n\n{hint}"
    message_text = (
        f"<@{allowed_user_id}> Gab es einen Bug/Fehler?{auto_close_hint}\n\n"
        "Buttons unten: **Es gab einen Bug** | **Kampf-Log per DM** | **Es gab keinen Bug**"
    )
    try:
        message = await sendable_channel.send(
            message_text,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await _maybe_register_durable_message(message, view)
    except Exception:
        logging.exception("Failed to send mission feedback prompt")


def _mission_success_embed(reward_card: dict[str, Any], total_waves: int, *, is_new_card: bool) -> discord.Embed:
    reward_color = _card_rarity_color(cast(CardData, reward_card))
    if is_new_card:
        embed = discord.Embed(
            title="🏆 Mission erfolgreich!",
            description=f"Du hast alle {total_waves} Wellen überstanden und **{reward_card['name']}** erhalten!",
            color=reward_color,
        )
        if reward_card.get("bild"):
            embed.set_image(url=str(reward_card["bild"]))
        return embed
    embed = discord.Embed(
        title="💎 Mission erfolgreich - Infinitydust!",
        description=f"Du hast alle {total_waves} Wellen überstanden!",
        color=reward_color,
    )
    embed.add_field(
        name="Belohnung",
        value=f"Du hattest **{reward_card['name']}** bereits - wurde zu **Infinitydust** umgewandelt!",
        inline=False,
    )
    embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    return embed


async def _begin_mission_thread_flow(interaction: discord.Interaction, mission_data: dict[str, Any], is_admin: bool) -> None:
    thread: discord.Thread | None = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
    if thread is None:
        thread = await _create_required_private_mission_thread(interaction)
        if thread is None:
            return
        await interaction.followup.send(f"Mission-Thread erstellt: {thread.mention}", ephemeral=True)
    elif interaction.guild is not None:
        await save_managed_thread(thread_id=thread.id, guild_id=interaction.guild.id, kind=THREAD_KIND_MISSION)
    user_karten = _sort_user_cards_like_karten(await get_user_karten(interaction.user.id))
    if not user_karten:
        await _safe_send_channel(
            interaction,
            thread,
            content=f"{interaction.user.mention} ❌ Du hast keine Karten für die Mission!",
        )
        return
    select_view = MissionStartCardSelectView(
        interaction.user.id,
        _dict_str_any(mission_data),
        is_admin=is_admin,
        user_karten=[name for name, _amount in user_karten],
    )
    intro_embed = _build_mission_embed(_dict_str_any(mission_data))
    intro_embed.description = (
        f"{intro_embed.description or ''}\n\n"
        f"{interaction.user.mention}, wähle jetzt deine Karte für diese Mission."
    ).strip()
    await _safe_send_channel(interaction, thread, embed=intro_embed, view=select_view)


async def _start_mission_wave_in_thread(
    interaction: discord.Interaction,
    *,
    mission_state: dict[str, Any],
) -> MissionBattleView | None:
    mission_data = _dict_str_any(mission_state.get("mission_data"))
    selected_card_name = str(mission_state.get("selected_card_name") or "").strip()
    if not selected_card_name:
        await _safe_send_channel(interaction, interaction.channel, content="❌ Keine Missions-Karte ausgewählt.")
        return None
    player_card = await get_karte_by_name(selected_card_name)
    if not player_card:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"❌ Die Karte **{selected_card_name}** konnte nicht gefunden werden.",
        )
        return None
    wave_num = max(1, int(mission_state.get("next_wave", 1) or 1))
    total_waves = max(wave_num, int(mission_state.get("total_waves", mission_data.get("waves", 1)) or 1))
    bot_card = cast(CardData, random.choice(karten))
    mission_view = MissionBattleView(
        cast(CardData, player_card),
        bot_card,
        interaction.user.id,
        wave_num,
        total_waves,
        mission_data=mission_data,
        is_admin=bool(mission_state.get("is_admin", False)),
        selected_card_name=selected_card_name,
    )
    await mission_view.init_with_buffs()
    log_message = await _safe_send_channel(interaction, interaction.channel, embed=create_battle_log_embed())
    if isinstance(log_message, discord.Message):
        mission_view.battle_log_message = log_message
    battle_message = await _safe_send_channel(
        interaction,
        interaction.channel,
        embed=mission_view.create_current_embed(),
        view=mission_view,
    )
    if isinstance(battle_message, discord.Message):
        await mission_view.persist_session(interaction.channel, status="active", battle_message=battle_message)
    return mission_view

class ChallengeResponseView(DurableView):
    durable_view_kind = VIEW_KIND_FIGHT_CHALLENGE

    def __init__(
        self,
        challenger_id: int,
        challenged_id: int,
        challenger_card_name: str,
        *,
        request_id: int,
        origin_channel_id: int | None,
        thread_id: int | None,
        thread_created: bool,
    ):
        super().__init__(timeout=None)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.challenger_card_name = challenger_card_name
        self.request_id = request_id
        self.origin_channel_id = origin_channel_id
        self.thread_id = thread_id
        self.thread_created = thread_created

    def durable_payload(self) -> dict[str, Any]:
        return {
            "challenger_id": self.challenger_id,
            "challenged_id": self.challenged_id,
            "challenger_card_name": self.challenger_card_name,
            "request_id": self.request_id,
            "origin_channel_id": self.origin_channel_id,
            "thread_id": self.thread_id,
            "thread_created": self.thread_created,
        }

    @ui.button(label="Kämpfen", style=discord.ButtonStyle.success, custom_id="fight_challenge:accept")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Nur der Herausgeforderte kann annehmen!", ephemeral=True)
            return
        if not await claim_fight_request(self.request_id, "accepted"):
            await interaction.response.send_message("❌ Diese Kampf-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        await interaction.response.defer()
        # Falls privater Thread genutzt wird, stelle sicher, dass der Herausgeforderte hinzugefügt ist
        try:
            if self.thread_id:
                thread = bot.get_channel(self.thread_id)
                if thread is None:
                    thread = await bot.fetch_channel(self.thread_id)
                if isinstance(thread, discord.Thread):
                    await thread.add_user(interaction.user)
        except Exception:
            logging.exception("Unexpected error")
        await _start_fight_card_selection_from_challenge(
            interaction,
            challenger_id=self.challenger_id,
            challenged_id=self.challenged_id,
            challenger_card_name=self.challenger_card_name,
            origin_channel_id=self.origin_channel_id,
            thread_id=self.thread_id,
            thread_created=self.thread_created,
        )
        self.stop()

    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="fight_challenge:decline")
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Nur der Herausgeforderte kann ablehnen!", ephemeral=True)
            return
        if not await claim_fight_request(self.request_id, "declined"):
            await interaction.response.send_message("❌ Diese Kampf-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        await interaction.response.send_message("Kampf abgelehnt.", ephemeral=True)
        try:
            await _safe_send_channel(
                interaction,
                interaction.channel,
                content=f"<@{self.challenger_id}>, {interaction.user.mention} hat den Kampf abgelehnt.",
            )
        except Exception:
            logging.exception("Unexpected error")
        await _maybe_delete_fight_thread(self.thread_id, self.thread_created)
        self.stop()

class AdminCloseView(DurableView):
    durable_view_kind = VIEW_KIND_THREAD_CLOSE

    def __init__(self, thread: discord.Thread):
        super().__init__(timeout=None)
        self.thread = thread

    def durable_payload(self) -> dict[str, Any]:
        return {"thread_id": self.thread.id}

    @ui.button(
        label="Thread schließen (Admin/Owner)",
        style=discord.ButtonStyle.danger,
        custom_id="thread_close:admin",
    )
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await is_admin(interaction):
            await interaction.response.send_message(CLOSE_PERMISSION_DENIED, ephemeral=True)
            return
        await interaction.response.send_message(THREAD_CLOSING, ephemeral=True)
        self.stop()
        try:
            await update_managed_thread_status(self.thread.id, "deleted")
            await self.thread.delete()
        except Exception:
            logging.exception("Unexpected error")

class BugReportLinkView(RestrictedView):
    def __init__(self):
        super().__init__(timeout=300)
        if BUG_REPORT_TALLY_URL:
            self.add_item(ui.Button(label="Formular öffnen", style=discord.ButtonStyle.link, url=BUG_REPORT_TALLY_URL))

class FightFeedbackView(DurableView):
    durable_view_kind = VIEW_KIND_FIGHT_FEEDBACK

    def __init__(
        self,
        channel,
        guild: discord.Guild | None,
        allowed_user_ids: set[int],
        battle_log_text: str | None = None,
        *,
        bug_reported_by: set[int] | None = None,
        log_sent_to: set[int] | None = None,
        opted_out_by: set[int] | None = None,
        auto_close_delay: int | None = None,
        auto_close_started_at: int | None = None,
        close_on_idle: bool = True,
        close_after_no_bug: bool = True,
        keep_open_after_bug: bool = True,
    ):
        super().__init__(timeout=None)
        self.channel = channel
        self.guild = guild
        self.allowed_user_ids = allowed_user_ids
        self.battle_log_text = str(battle_log_text or "").strip()
        self._bug_reported_by: set[int] = set(bug_reported_by or set())
        self._log_sent_to: set[int] = set(log_sent_to or set())
        self._opted_out_by: set[int] = set(opted_out_by or set())
        self._admin_close_posted = False
        self.auto_close_delay = max(0, int(auto_close_delay or 0)) or None
        self.auto_close_started_at = (
            int(auto_close_started_at or time.time())
            if self.auto_close_delay
            else None
        )
        self.close_on_idle = bool(close_on_idle)
        self.close_after_no_bug = bool(close_after_no_bug)
        self.keep_open_after_bug = bool(keep_open_after_bug)
        self._auto_close_task: asyncio.Task | None = None
        if not isinstance(self.channel, discord.Thread):
            try:
                self.remove_item(self.close_thread_btn)
            except Exception:
                logging.exception("Unexpected error")
        else:
            self._ensure_auto_close_task()

    async def _is_allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_user_ids:
            return True
        return await is_admin(interaction)

    @staticmethod
    def _split_log_for_dm(text: str, chunk_size: int = 3800) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        chunks: list[str] = []
        current = ""
        for line in raw.splitlines():
            line_to_add = (line + "\n") if line else "\n"
            if len(current) + len(line_to_add) > chunk_size:
                if current.strip():
                    chunks.append(current.rstrip())
                current = line_to_add
            else:
                current += line_to_add
        if current.strip():
            chunks.append(current.rstrip())
        return chunks

    def durable_payload(self) -> dict[str, Any]:
        return {
            "allowed_user_ids": sorted(self.allowed_user_ids),
            "battle_log_text": self.battle_log_text,
            "bug_reported_by": sorted(self._bug_reported_by),
            "log_sent_to": sorted(self._log_sent_to),
            "opted_out_by": sorted(self._opted_out_by),
            "auto_close_delay": self.auto_close_delay,
            "auto_close_started_at": self.auto_close_started_at,
            "close_on_idle": self.close_on_idle,
            "close_after_no_bug": self.close_after_no_bug,
            "keep_open_after_bug": self.keep_open_after_bug,
        }

    def durable_log_text(self) -> str:
        return self.battle_log_text

    def stop(self) -> None:
        super().stop()
        if self._auto_close_task and not self._auto_close_task.done():
            self._auto_close_task.cancel()

    def _auto_close_blocked(self) -> bool:
        return bool(self.keep_open_after_bug and self._bug_reported_by)

    def _remaining_auto_close_delay(self) -> int | None:
        if not isinstance(self.channel, discord.Thread):
            return None
        if not self.auto_close_delay or not self.auto_close_started_at:
            return None
        elapsed = max(0, int(time.time()) - int(self.auto_close_started_at))
        return max(0, int(self.auto_close_delay) - elapsed)

    async def _close_thread_automatically(self) -> None:
        if not isinstance(self.channel, discord.Thread):
            return
        if self._auto_close_blocked():
            return
        try:
            await update_managed_thread_status(self.channel.id, "deleted")
            await self.channel.delete()
        except discord.NotFound:
            return
        except Exception:
            logging.exception("Failed to auto-close thread %s", self.channel.id)

    async def _auto_close_loop(self) -> None:
        remaining = self._remaining_auto_close_delay()
        if remaining is None:
            return
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
            if self._auto_close_blocked():
                return
            await self._close_thread_automatically()
        except asyncio.CancelledError:
            return

    def _ensure_auto_close_task(self) -> None:
        if not isinstance(self.channel, discord.Thread):
            return
        if not self.close_on_idle or not self.auto_close_delay:
            return
        if self._auto_close_blocked():
            return
        if self._auto_close_task and not self._auto_close_task.done():
            return
        self._auto_close_task = asyncio.create_task(self._auto_close_loop())

    async def _maybe_post_admin_close_view(self) -> None:
        if self._admin_close_posted:
            return
        if not isinstance(self.channel, discord.Thread):
            return
        self._admin_close_posted = True
        try:
            view = AdminCloseView(self.channel)
            message = await self.channel.send("Ein Admin/Owner kann den Thread jetzt schließen.", view=view)
            await _maybe_register_durable_message(message, view)
        except Exception:
            logging.exception("Unexpected error")

    @ui.button(label="Es gab einen Bug", style=discord.ButtonStyle.success, custom_id="fight_feedback:bug")
    async def yes_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=True)
            return
        if interaction.user.id in self._bug_reported_by:
            await interaction.response.send_message("Du hast bereits gemeldet, dass es einen Bug gab.", ephemeral=True)
            return
        self._bug_reported_by.add(interaction.user.id)
        if not BUG_REPORT_TALLY_URL or "REPLACE_ME" in BUG_REPORT_TALLY_URL:
            await interaction.response.send_message(BUG_FORM_NOT_CONFIGURED, ephemeral=True)
            return

        await _send_basti_log_dm(
            self.battle_log_text,
            context_lines=[
                "Bug-Button wurde geklickt.",
                f"Guild: {self.guild.name if self.guild else 'Unbekannt'}",
                f"Kanal/Thread: {_channel_mention_or_fallback(self.channel)}",
                f"Gemeldet von: {safe_display_name(interaction.user, fallback=str(interaction.user.id))} ({interaction.user.id})",
                f"View: {self.durable_context_label()}",
            ],
        )
        await interaction.response.send_message(
            content="🐞 Danke! Bitte fülle dieses Formular aus:",
            view=BugReportLinkView(),
            ephemeral=True,
        )
        if self.keep_open_after_bug and self._auto_close_task and not self._auto_close_task.done():
            self._auto_close_task.cancel()
        await self._maybe_post_admin_close_view()

    @ui.button(label="Kampf-Log per DM", style=discord.ButtonStyle.primary, custom_id="fight_feedback:log")
    async def log_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=True)
            return
        if interaction.user.id in self._log_sent_to:
            await interaction.response.send_message("Ich habe dir den Kampf-Log bereits per DM geschickt.", ephemeral=True)
            return
        if not self.battle_log_text:
            await interaction.response.send_message("Für diesen Kampf ist kein Log verfügbar.", ephemeral=True)
            return
        chunks = self._split_log_for_dm(self.battle_log_text)
        if not chunks:
            await interaction.response.send_message("Für diesen Kampf ist kein Log verfügbar.", ephemeral=True)
            return
        try:
            for idx, chunk in enumerate(chunks, start=1):
                title = "Vollständiger Kampf-Log" if len(chunks) == 1 else f"Vollständiger Kampf-Log ({idx}/{len(chunks)})"
                dm_embed = discord.Embed(title=title, description=chunk, color=0x2F3136)
                await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            await interaction.response.send_message(DM_DISABLED, ephemeral=True)
            return
        except Exception:
            logging.exception("Unexpected error")
            await interaction.response.send_message(DM_LOG_SEND_FAILED, ephemeral=True)
            return
        self._log_sent_to.add(interaction.user.id)
        await interaction.response.send_message("📩 Vollständiger Kampf-Log wurde dir per DM gesendet.", ephemeral=True)

    @ui.button(label="Es gab keinen Bug", style=discord.ButtonStyle.danger, row=2, custom_id="fight_feedback:no_bug")
    async def no_bug_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=True)
            return
        if interaction.user.id in self._opted_out_by:
            await interaction.response.send_message("Du hast bereits geantwortet.", ephemeral=True)
            return
        self._opted_out_by.add(interaction.user.id)
        if self.close_after_no_bug and isinstance(self.channel, discord.Thread) and not self._auto_close_blocked():
            self.close_on_idle = True
            if not self.auto_close_delay:
                self.auto_close_delay = _thread_auto_close_delay(DEFAULT_THREAD_AUTO_CLOSE_POLICY)
            if self.auto_close_delay and not self.auto_close_started_at:
                self.auto_close_started_at = int(time.time())
            self._ensure_auto_close_task()
        await interaction.response.send_message("\u2705 Danke f\u00fcr dein Feedback!", ephemeral=True)

    @ui.button(
        label="Thread schließen (Admin/Owner)",
        style=discord.ButtonStyle.secondary,
        custom_id="fight_feedback:close_thread",
    )
    async def close_thread_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not isinstance(self.channel, discord.Thread):
            await interaction.response.send_message("Dieser Button ist nur in Threads verfügbar.", ephemeral=True)
            return
        if not await is_admin(interaction):
            await interaction.response.send_message(CLOSE_PERMISSION_DENIED, ephemeral=True)
            return
        await interaction.response.send_message(THREAD_CLOSING, ephemeral=True)
        self.stop()
        try:
            await update_managed_thread_status(self.channel.id, "deleted")
            await self.channel.delete()
        except Exception:
            logging.exception("Unexpected error")

# Helper: Check Admin (Admins oder Owner/Dev)
async def is_admin(interaction):
    # Bot-Owner/Dev dürfen Admin-Commands nutzen (auch ohne Serverrechte)
    if await is_owner_or_dev(interaction):
        return True
    member = _interaction_member_or_none(interaction)
    # Prüfe ob User Admin-Berechtigung hat ODER Server-Owner ist ODER spezielle Rollen hat
    if interaction.user.id == (interaction.guild.owner_id if interaction.guild else 0):
        return True
    if member is not None and member.guild_permissions.administrator:
        return True
    try:
        role_ids = _member_role_ids(member)
        if MFU_ADMIN_ROLE_ID in role_ids or OWNER_ROLE_ROLE_ID in role_ids:
            return True
    except Exception:
        logging.exception("Unexpected error")
    return False

async def is_config_admin(interaction: discord.Interaction) -> bool:
    if await is_admin(interaction):
        return True
    if interaction.guild is None:
        return False
    member = _interaction_member_or_none(interaction)
    if member is None:
        return False
    perms = member.guild_permissions
    return perms.manage_guild or perms.manage_channels

def _has_dev_role(member: discord.Member | discord.User | None) -> bool:
    if DEV_ROLE_ID == 0:
        return False
    if not isinstance(member, discord.Member):
        return False
    try:
        role_ids = _member_role_ids(member)
        return DEV_ROLE_ID in role_ids
    except Exception:
        logging.exception("Failed to read member roles")
        return False

def is_owner_or_dev_member(member: discord.Member | discord.User | None) -> bool:
    if member is None:
        return False
    if member.id == BASTI_USER_ID:
        return True
    return _has_dev_role(member)

async def is_owner_or_dev(interaction: discord.Interaction) -> bool:
    if interaction.user.id == BASTI_USER_ID:
        return True
    if interaction.guild is None:
        return False
    return _has_dev_role(_interaction_member_or_none(interaction))

async def require_owner_or_dev(interaction: discord.Interaction) -> bool:
    if not await is_owner_or_dev(interaction):
        await interaction.response.send_message(
            "⛔ Nur Basti oder die Developer-Rolle dürfen diesen Command nutzen.",
            ephemeral=True,
        )
        return False
    return True

def _normalize_rarity_key(raw_value: str | None) -> str:
    value = str(raw_value or "").strip().lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "Ã¤": "ae",
        "Ã¶": "oe",
        "Ã¼": "ue",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    alias_map = {
        "common": "common",
        "normal": "common",
        "gewoehnlich": "common",
        "gewohnlich": "common",
        "rare": "rare",
        "selten": "rare",
        "epic": "epic",
        "episch": "epic",
        "legendary": "legendary",
        "legendaer": "legendary",
        "legendar": "legendary",
    }
    return alias_map.get(value, value or "unknown")

def _rarity_label_from_key(rarity_key: str) -> str:
    labels = {
        "common": "Gewöhnlich / Common",
        "rare": "Selten / Rare",
        "epic": "Episch / Epic",
        "legendary": "Legendär / Legendary",
    }
    return labels.get(rarity_key, rarity_key.title())

def _cards_by_rarity_group() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for card in karten:
        key = _normalize_rarity_key(card.get("seltenheit"))
        grouped.setdefault(key, []).append(card)
    return grouped

def _card_by_name_local(name: str | None) -> dict | None:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return None
    for card in karten:
        card_name = str(card.get("name") or "").strip().lower()
        if card_name == normalized:
            return card
    return None

def _card_rarity_color(card: dict | None) -> int | None:
    if not isinstance(card, dict):
        return None
    rarity_key = _normalize_rarity_key(card.get("seltenheit"))
    color_map = {
        "common": 0x13EB2B,      # Gewöhnlich
        "rare": 0x2E86FF,        # Selten
        "epic": 0xC84DFF,        # Episch
        "legendary": 0xFFB020,   # Legendär
    }
    return color_map.get(rarity_key)


def _card_name_ansi_block(card_name: str, card: dict | None) -> str:
    rarity_key = _normalize_rarity_key((card or {}).get("seltenheit"))
    ansi_color_map = {
        "common": "32",      # green
        "rare": "34",        # blue
        "epic": "35",        # magenta
        "legendary": "33",   # yellow/orange-like
    }
    color_code = ansi_color_map.get(rarity_key)
    safe_name = str(card_name or "Unbekannte Karte")
    if not color_code:
        return f"**{safe_name}**"
    return f"```ansi\n\u001b[1;{color_code}m{safe_name}\u001b[0m\n```"

async def is_give_op_authorized(interaction: discord.Interaction) -> bool:
    if await is_owner_or_dev(interaction):
        return True
    if interaction.guild is None:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    guild_id = interaction.guild.id
    allowed_users = await get_give_op_allowed_users(guild_id)
    if interaction.user.id in allowed_users:
        return True
    allowed_roles = await get_give_op_allowed_roles(guild_id)
    if not allowed_roles:
        return False
    member = _interaction_member_or_none(interaction)
    if member is None:
        return False
    try:
        member_role_ids = _member_role_ids(member)
    except Exception:
        logging.exception("Failed reading member roles for give_op authorization")
        return False
    return bool(member_role_ids.intersection(allowed_roles))

async def start_mission_waves(interaction, mission_data, is_admin, ephemeral: bool):
    del ephemeral
    await _begin_mission_thread_flow(interaction, _dict_str_any(mission_data), bool(is_admin))

async def execute_mission_wave(interaction, wave_num, total_waves, player_card, reward_card, ephemeral: bool):
    del reward_card, ephemeral
    state: dict[str, Any] = {
        "mission_data": {"waves": int(total_waves or 1)},
        "is_admin": False,
        "selected_card_name": str(_dict_str_any(player_card).get("name") or ""),
        "next_wave": int(wave_num or 1),
        "total_waves": int(total_waves or 1),
    }
    view = await _start_mission_wave_in_thread(interaction, mission_state=state)
    return None if view is None else view.result

# Entfernt: /team Command (auf Wunsch des Nutzers)

class StorySelectView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value: str | None = None
        options = [SelectOption(label="text", value="text", description="Test-Story")]  # Platzhalter
        self.select = ui.Select(placeholder="Wähle eine Story...", min_values=1, max_values=1, options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann die Story wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()


class StoryPlayerView(RestrictedView):
    def __init__(self, user_id: int, story_id: str):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.story_id = story_id
        self.step = 0  # 0=Video-Hinweis, 1=Bild-Hinweis, 2=Final

    def render_step_embed(self) -> discord.Embed:
        if self.step == 0:
            embed = discord.Embed(title="🎬 Story: text", description="Schau dir dieses Video an: https://youtu.be/cpXOkN6T2OU")
        elif self.step == 1:
            embed = discord.Embed(title="🗺️ Story: text", description="Weg Beschreibung noch nicht vorhanden.")
        else:
            embed = discord.Embed(title="🚧 Realize Feature", description="Dies ist ein Realize Feature und deshalb noch nicht erreichbar.")
        return embed

    def update_buttons_for_step(self):
        # Buttons existieren immer, aber Zustände variieren
        # Back ist nur ab Schritt 1 aktiv
        self.button_back.disabled = (self.step == 0)
        # Weiter ist in Schritt 2 deaktiviert
        self.button_next.disabled = (self.step >= 2)

    @ui.button(label="Weiter", style=discord.ButtonStyle.success)
    async def button_next(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Story-Dialog!", ephemeral=True)
            return
        if self.step < 2:
            self.step += 1
        self.update_buttons_for_step()
        await interaction.response.edit_message(embed=self.render_step_embed(), view=self if self.step < 2 else None)
        if self.step >= 2:
            self.stop()

    @ui.button(label="Abbrechen", style=discord.ButtonStyle.danger)
    async def button_cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Story-Dialog!", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="❌ Story abgebrochen.", embed=None, view=None)

    @ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def button_back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Story-Dialog!", ephemeral=True)
            return
        if self.step > 0:
            self.step -= 1
        self.update_buttons_for_step()
        await interaction.response.edit_message(embed=self.render_step_embed(), view=self)

# Select Menu Views für das Verstärken-System
FUSE_DUST_COST = 10
FUSE_HEALTH_BONUS = 10
FUSE_DAMAGE_MAX_BONUS = 5
FUSE_HP_CAP = 200


class DustAmountSelect(ui.Select):
    def __init__(self, user_dust):
        options = []
        if user_dust >= FUSE_DUST_COST:
            options.append(
                SelectOption(
                    label=f"{FUSE_DUST_COST} Infinitydust verwenden",
                    value=str(FUSE_DUST_COST),
                    description=f"Leben +{FUSE_HEALTH_BONUS} oder Max-Schaden +{FUSE_DAMAGE_MAX_BONUS}",
                    emoji="💎",
                )
            )
        super().__init__(placeholder="Wähle die Infinitydust-Menge...", options=options)

    async def callback(self, interaction: discord.Interaction):
        dust_amount = int(self.values[0])
        user_karten = await get_user_karten(interaction.user.id)
        if not user_karten:
            await interaction.response.send_message("❌ Du hast keine Karten zum Verstärken!", ephemeral=True)
            return

        view = FuseCardSelectView(dust_amount, user_karten)
        embed = discord.Embed(
            title="🎯 Karte auswählen",
            description=(
                f"Du verwendest **{dust_amount} Infinitydust**.\n"
                f"❤️ Leben: **+{FUSE_HEALTH_BONUS}** (max. {FUSE_HP_CAP})\n"
                f"⚔️ Max-Schaden: **+{FUSE_DAMAGE_MAX_BONUS}**\n\n"
                "Wähle die Karte, die du verstärken möchtest:"
            ),
            color=0x9D4EDD,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=view)


class FuseCardSelectView(RestrictedView):
    def __init__(self, dust_amount, user_karten):
        super().__init__(timeout=60)
        self.dust_amount = dust_amount
        self.add_item(CardSelect(user_karten, dust_amount))


class CardSelect(ui.Select):
    def __init__(self, user_karten, dust_amount):
        self.dust_amount = dust_amount
        options = []
        for kartenname, anzahl in user_karten[:25]:
            options.append(SelectOption(label=f"{kartenname} (x{anzahl})", value=kartenname))
        super().__init__(placeholder="Wähle eine Karte zum Verstärken...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_card = self.values[0]
        karte_data = await get_karte_by_name(selected_card)
        if not karte_data:
            await interaction.response.send_message("❌ Karte nicht gefunden!", ephemeral=True)
            return

        view = BuffTypeSelectView(self.dust_amount, selected_card, karte_data)
        embed = discord.Embed(
            title="⚡ Verstärkung wählen",
            description=(
                f"Karte: **{selected_card}**\n\n"
                f"❤️ Leben: **+{FUSE_HEALTH_BONUS}**\n"
                f"⚔️ Max-Schaden: **+{FUSE_DAMAGE_MAX_BONUS}** (min bleibt gleich)\n\n"
                "Was möchtest du verstärken?"
            ),
            color=0x9D4EDD,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=view)


class BuffTypeSelectView(RestrictedView):
    def __init__(self, dust_amount, selected_card, karte_data):
        super().__init__(timeout=60)
        self.dust_amount = dust_amount
        self.selected_card = selected_card
        self.add_item(BuffTypeSelect(dust_amount, selected_card, karte_data))


class BuffTypeSelect(ui.Select):
    def __init__(self, dust_amount, selected_card, karte_data):
        self.dust_amount = dust_amount
        self.selected_card = selected_card

        options = [
            SelectOption(
                label="Leben verstärken",
                value="health_0",
                description=f"+{FUSE_HEALTH_BONUS} Lebenspunkte (bis {FUSE_HP_CAP})",
                emoji="❤️",
            )
        ]

        attacks = karte_data.get("attacks", [])
        for i, attack in enumerate(attacks[:4]):
            if not _attack_is_damage_upgradeable(attack):
                continue
            attack_name = str(attack.get("name") or f"Attacke {i + 1}")
            attack_damage = attack.get("damage", [0, 0])
            _min_dmg, max_dmg = _damage_range_with_max_bonus(attack_damage, max_only_bonus=0, flat_bonus=0)
            if max_dmg + FUSE_DAMAGE_MAX_BONUS > MAX_ATTACK_DAMAGE_PER_HIT:
                continue
            options.append(
                SelectOption(
                    label=f"{attack_name} verstärken",
                    value=f"damage_{i + 1}",
                    description=f"+{FUSE_DAMAGE_MAX_BONUS} Max-Schaden (bis {MAX_ATTACK_DAMAGE_PER_HIT})",
                    emoji="⚔️",
                )
            )

        super().__init__(placeholder="Wähle was verstärkt werden soll...", options=options)

    async def callback(self, interaction: discord.Interaction):
        buff_choice = self.values[0]
        buff_type, attack_num = buff_choice.split("_")
        attack_number = int(attack_num)

        karte_data = await get_karte_by_name(self.selected_card)
        if not karte_data:
            await interaction.response.send_message("❌ Karte nicht gefunden!", ephemeral=True)
            return
        user_buffs = await get_card_buffs(interaction.user.id, self.selected_card)

        applied_buff_amount = 0
        buff_text = ""
        emoji = "⚔️"

        if buff_type == "damage":
            attacks = karte_data.get("attacks", [])
            if attack_number <= 0 or attack_number > len(attacks):
                await interaction.response.send_message("❌ Ungültige Attacke.", ephemeral=True)
                return
            selected_attack = attacks[attack_number - 1]
            if not _attack_is_damage_upgradeable(selected_attack):
                await interaction.response.send_message(
                    "❌ Nur reine Schadens-Attacken ohne Zusatzeffekte können aktuell verbessert werden.",
                    ephemeral=True,
                )
                return

            _base_min, max_base_damage = _damage_range_with_max_bonus(
                selected_attack.get("damage", [0, 0]),
                max_only_bonus=0,
                flat_bonus=0,
            )
            existing_buffs = 0
            for buff_type_check, attack_num_check, buff_amount_check in user_buffs:
                if buff_type_check == "damage" and attack_num_check == attack_number:
                    existing_buffs += int(buff_amount_check or 0)

            current_max_damage = int(max_base_damage) + int(existing_buffs)
            next_max_damage = current_max_damage + FUSE_DAMAGE_MAX_BONUS
            if next_max_damage > MAX_ATTACK_DAMAGE_PER_HIT:
                await interaction.response.send_message(
                    (
                        f"❌ **Maximal {MAX_ATTACK_DAMAGE_PER_HIT} Schaden pro Angriff erlaubt!**\n\n"
                        f"Aktuell: **{current_max_damage}**\n"
                        f"Nächste Verbesserung wäre: **{next_max_damage}**"
                    ),
                    ephemeral=True,
                )
                return

            applied_buff_amount = FUSE_DAMAGE_MAX_BONUS
            attack_name = str(selected_attack.get("name") or f"Attacke {attack_number}")
            buff_text = f"**{attack_name} Max-Schaden +{applied_buff_amount}**"
            emoji = "⚔️"
        else:
            base_hp = int(karte_data.get("hp", 100) or 100)
            existing_health = 0
            for buff_type_check, attack_num_check, buff_amount_check in user_buffs:
                if buff_type_check == "health" and int(attack_num_check or 0) == 0:
                    existing_health += int(buff_amount_check or 0)
            current_hp = base_hp + existing_health
            allowed_buff = min(FUSE_HEALTH_BONUS, max(0, FUSE_HP_CAP - current_hp))
            if allowed_buff <= 0:
                await interaction.response.send_message(
                    f"❌ **HP-Cap erreicht!** Diese Karte hat bereits **{current_hp} HP**.",
                    ephemeral=True,
                )
                return
            applied_buff_amount = allowed_buff
            buff_text = f"**Leben +{applied_buff_amount}**"
            emoji = "❤️"

        success = await spend_infinitydust(interaction.user.id, self.dust_amount)
        if not success:
            await interaction.response.send_message("❌ Nicht genug Infinitydust!", ephemeral=True)
            return

        await add_card_buff(
            interaction.user.id,
            self.selected_card,
            buff_type,
            attack_number,
            applied_buff_amount,
        )

        embed = discord.Embed(
            title="✅ Verstärkung erfolgreich!",
            description=(
                f"🃏 **{self.selected_card}**\n"
                f"{emoji} {buff_text}\n\n"
                f"💎 **{self.dust_amount} Infinitydust** verbraucht"
            ),
            color=0x00FF00,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=None)

class InviteUserSelectView(RestrictedView):
    def __init__(self, inviter_id, available_user_ids):
        super().__init__(timeout=60)
        self.inviter_id = inviter_id
        self.add_item(InviteUserSelect(inviter_id, available_user_ids))

class InviteUserSelect(ui.Select):
    def __init__(self, inviter_id, available_user_ids):
        self.inviter_id = inviter_id
        
        options = []
        for user_id in available_user_ids[:25]:  # Max 25 Optionen
            try:
                # Versuche User-Objekt zu bekommen (funktioniert nur wenn Bot den User sehen kann)
                user = bot.get_user(user_id)
                if user:
                    primary_name = safe_display_name(user, fallback=f"User {user_id}")
                    username = escape_display_text(getattr(user, "name", ""), fallback=str(user_id))
                    display_name = f"{primary_name} ({username})"
                else:
                    display_name = f"User {user_id}"
                
                options.append(SelectOption(
                    label=display_name[:100],  # Discord Limit
                    value=str(user_id),
                    description="Beide erhalten 1x Infinitydust",
                    emoji="🎁"
                ))
            except:
                # Fallback für unbekannte User
                options.append(SelectOption(
                    label=f"User {user_id}",
                    value=str(user_id),
                    description="Beide erhalten 1x Infinitydust",
                    emoji="🎁"
                ))
        
        if not options:
            options.append(SelectOption(label="Keine Spieler verfügbar", value="none"))
        
        super().__init__(placeholder="Wähle den Spieler der dich eingeladen hat! :)", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ Keine Spieler verfügbar!", ephemeral=True)
            return

        invited_user_id = int(self.values[0])
        logging.info("[INVITED] selected_inviter_id=%s user=%s", invited_user_id, interaction.user.id)

        # Prüfe nochmal ob der Einlader den Command schon mal genutzt hat (nur für Nicht-Admins)
        is_admin_user = await is_admin(interaction)
        if not is_admin_user:
            async with db_context() as db:
                cursor = await db.execute(
                    "SELECT used_invite FROM user_daily WHERE user_id = ?",
                    (self.inviter_id,),
                )
                row = await cursor.fetchone()
                logging.info("[INVITED] used_invite check inviter=%s row=%s", self.inviter_id, row)
                if row and row[0] == 1:
                    await interaction.response.send_message("❌ Du hast den `/eingeladen` Command bereits verwendet! Nur Admins können ihn mehrfach nutzen.", ephemeral=True)
                    return
                # Markiere als verwendet (nur für Nicht-Admins)
                await db.execute(
                    """
                    INSERT OR REPLACE INTO user_daily (user_id, last_daily, used_invite)
                    VALUES (
                        ?,
                        COALESCE((SELECT last_daily FROM user_daily WHERE user_id = ?), 0),
                        1
                    )
                    """,
                    (self.inviter_id, self.inviter_id),
                )
                await db.commit()

        # Gib beiden Usern 1x Infinitydust
        await add_infinitydust(self.inviter_id, 1)
        await add_infinitydust(invited_user_id, 1)
        logging.info(
            "[INVITED] awarded infinitydust inviter=%s invited=%s",
            self.inviter_id,
            invited_user_id,
        )

        # Hole User-Namen für die Nachricht
        inviter = interaction.user
        invited_user = None
        try:
            invited_user = bot.get_user(invited_user_id)
            if invited_user is None:
                invited_user = await interaction.client.fetch_user(invited_user_id)
        except Exception:
            invited_user = None
        invited_mention = invited_user.mention if invited_user else f"<@{invited_user_id}>"

        # Erfolgs-Nachricht (öffentlich für beide)
        embed = discord.Embed(
            title="🎉 Einladung erfolgreich!",
            description=(
                f"**{inviter.mention}** wurde von **{invited_mention}** eingeladen!\n\n"
                f"💎 **Beide haben 1x Infinitydust erhalten!**"
            ),
            color=0x00ff00,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")

        # Sende öffentlich in den Kanal
        await _safe_send_channel(interaction, interaction.channel, embed=embed)

        # Bestätige dem Initiator
        await interaction.response.edit_message(
            content="✅ Einladung erfolgreich gesendet!",
            embed=None,
            view=None,
        )

class DustAmountView(RestrictedView):
    def __init__(self, user_dust):
        super().__init__(timeout=60)
        self.add_item(DustAmountSelect(user_dust))

# Slash-Command: Anfang (Hauptmenü)
class AnfangView(RestrictedView):
    def __init__(self):
        super().__init__(timeout=None)
        self.remove_item(self.btn_mission)
        self.remove_item(self.btn_story)

    @ui.button(label="tägliche Karte", style=discord.ButtonStyle.success, row=0, custom_id="anfang:daily")
    async def btn_daily(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum täglichen Belohnungs-Flow weiter
        await _invoke_command_callback(täglich, interaction)

    @ui.button(label="Verbessern", style=discord.ButtonStyle.primary, row=0, custom_id="anfang:fuse")
    async def btn_fuse(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fuse-Flow weiter
        await _invoke_command_callback(fuse, interaction)

    @ui.button(label="Kämpfe", style=discord.ButtonStyle.danger, row=0, custom_id="anfang:fight")
    async def btn_fight(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fight-Flow weiter
        await _invoke_command_callback(fight, interaction)

    @ui.button(label="Mission", style=discord.ButtonStyle.secondary, row=0, custom_id="anfang:mission")
    async def btn_mission(self, interaction: discord.Interaction, button: ui.Button):
        if ALPHA_PHASE_ENABLED:
            await _send_alpha_feature_blocked(interaction)
            return
        # Leitet zum Missions-Flow weiter
        await _invoke_command_callback(mission, interaction)

    @ui.button(label="Story", style=discord.ButtonStyle.secondary, row=0, custom_id="anfang:story")
    async def btn_story(self, interaction: discord.Interaction, button: ui.Button):
        if ALPHA_PHASE_ENABLED:
            await _send_alpha_feature_blocked(interaction)
            return
        # Leitet zum Story-Flow weiter
        await _invoke_command_callback(story, interaction)


class AlphaPhaseLegacyAnfangView(RestrictedView):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Mission", style=discord.ButtonStyle.secondary, custom_id="anfang:mission")
    async def legacy_block_mission(self, interaction: discord.Interaction, button: ui.Button):
        await _send_alpha_feature_blocked(interaction)

    @ui.button(label="Story", style=discord.ButtonStyle.secondary, custom_id="anfang:story")
    async def legacy_block_story(self, interaction: discord.Interaction, button: ui.Button):
        await _send_alpha_feature_blocked(interaction)


class IntroEphemeralPromptView(DurableView):
    durable_view_kind = VIEW_KIND_INTRO_PROMPT

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    def durable_payload(self) -> dict[str, object]:
        return {"user_id": self.user_id}

    @ui.button(
        label="Intro anzeigen (nur für dich)",
        style=discord.ButtonStyle.primary,
        custom_id="intro_prompt:show_intro",
    )
    async def show_intro(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht für dich gedacht.", ephemeral=True)
            return
        view = AnfangView()
        text = build_anfang_intro_text()
        await interaction.response.send_message(content=text, view=view, ephemeral=True)

class UserSelectView(RestrictedView):
    def __init__(self, user_id, guild):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.guild = guild
        self.value = None
        self.members = sorted(
            [member for member in guild.members if not member.bot],
            key=lambda m: safe_display_name(m, fallback=str(m.id)).lower(),
        )
        self.pages = [self.members[i:i + 24] for i in range(0, len(self.members), 24)] or [[]]
        self.page_index = 0

        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options_for_current_page(),
            row=0,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        self.prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        self.next_btn = ui.Button(
            label="Weiter",
            style=discord.ButtonStyle.secondary,
            disabled=(len(self.pages) <= 1),
            row=1,
        )
        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def _placeholder(self) -> str:
        if not self.members:
            return "Wähle einen Nutzer oder suche oben..."
        return f"Wähle einen Nutzer... (Seite {self.page_index + 1}/{len(self.pages)})"

    def _build_options_for_current_page(self) -> list[SelectOption]:
        options = [SelectOption(label="🔍 Nach Name suchen", value="search")]
        if not self.members:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="__none__"))
            return options
        page_members = self.pages[self.page_index]
        for member in page_members:
            options.append(SelectOption(label=safe_user_option_label(member), value=str(member.id)))
        return options

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann den Nutzer wählen!", ephemeral=True)
            return
        selected_value = self.select.values[0]
        if selected_value == "search":
            modal = UserSearchModal(
                self.guild,
                interaction.user,
                parent_view=self,
                include_bot_option=False,
                exclude_user_id=None,
            )
            await interaction.response.send_modal(modal)
            return
        if selected_value == "__none__":
            await interaction.response.send_message("❌ Keine Nutzer verfügbar.", ephemeral=True)
            return
        self.value = selected_value
        self.stop()
        await interaction.response.defer()

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index > 0:
            self.page_index -= 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
        await interaction.response.edit_message(view=self)

class VaultView(RestrictedView):
    def __init__(self, user_id: int, user_karten):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.user_karten = user_karten  # Liste (kartenname, anzahl)

        anzeigen_button = ui.Button(label="Anzeige", style=discord.ButtonStyle.primary)
        anzeigen_button.callback = self.on_anzeige
        self.add_item(anzeigen_button)

    async def on_anzeige(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Button!", ephemeral=True)
            return

        # Baue Optionsliste aus dem Besitz des Users
        options = []
        for kartenname, anzahl in self.user_karten:
            options.append(SelectOption(label=f"{kartenname} (x{anzahl})", value=kartenname))

        # Discord erlaubt höchstens 25 Optionen; bei mehr paginieren
        if len(options) <= 25:
            select = ui.Select(placeholder="Wähle eine Karte zur Anzeige...", min_values=1, max_values=1, options=options)
            async def handle_select(interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                    return
                card_name = select.values[0]
                karte = await get_karte_by_name(card_name)
                if not karte:
                    await interaction.response.send_message("Karte nicht gefunden.", ephemeral=True)
                    return
                embed = discord.Embed(
                    title=karte["name"],
                    description=karte["beschreibung"],
                    color=_card_rarity_color(karte),
                )
                embed.set_image(url=karte["bild"])
                
                # Attacken + Schaden unter der Karte anzeigen (inkl. /verbessern-Buffs des Users)
                attacks = karte.get("attacks", [])
                # Mappe Damage-Buffs je Attacke (1..4) für diesen User
                user_buffs = await get_card_buffs(self.user_id, karte["name"])
                damage_buff_map = {}
                for buff_type, attack_number, buff_amount in user_buffs:
                    if buff_type == "damage" and 1 <= attack_number <= 4:
                        damage_buff_map[attack_number] = damage_buff_map.get(attack_number, 0) + buff_amount
                
                if attacks:
                    lines = []
                    for idx, atk in enumerate(attacks, start=1):
                        dmg = atk.get("damage")
                        buff = damage_buff_map.get(idx, 0)
                        min_b, max_b = _damage_range_with_max_bonus(dmg, max_only_bonus=buff, flat_bonus=0)
                        dmg_text = f"{min_b}-{max_b}"
                        info_text = str(atk.get("info") or "").strip()
                        if info_text:
                            lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden\n  ↳ {info_text}")
                        else:
                            lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden")
                    embed.add_field(name="Attacken", value="\n".join(lines), inline=False)
                
                # Buttons für Attacken anzeigen, aber deaktiviert (kein Effekt beim Klicken)
                view_buttons = RestrictedView(timeout=60)
                for i, atk in enumerate(attacks[:4]):
                    dmg = atk.get("damage")
                    buff = damage_buff_map.get(i + 1, 0)
                    min_b, max_b = _damage_range_with_max_bonus(dmg, max_only_bonus=buff, flat_bonus=0)
                    dmg_text = f"{min_b}-{max_b}"
                    btn = ui.Button(
                        label=f"{atk.get('name', f'Attacke {i+1}')} ({dmg_text})",
                        style=discord.ButtonStyle.danger,
                        disabled=True,
                        row=0 if i < 2 else 1
                    )
                    view_buttons.add_item(btn)
                
                await interaction.response.send_message(embed=embed, view=view_buttons, ephemeral=True)

            select.callback = handle_select
            view = RestrictedView(timeout=90)
            view.add_item(select)
            await interaction.response.send_message("Wähle eine Karte:", view=view, ephemeral=True)
        else:
            # Paginierung
            pages = [options[i:i+25] for i in range(0, len(options), 25)]
            current_index = 0

            async def send_page(interaction: discord.Interaction, page_index: int):
                sel = ui.Select(placeholder=f"Seite {page_index+1}/{len(pages)} – Karte wählen...", min_values=1, max_values=1, options=pages[page_index])
                async def handle_sel(interaction: discord.Interaction):
                    if interaction.user.id != self.user_id:
                        await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                        return
                    card_name = sel.values[0]
                    karte = await get_karte_by_name(card_name)
                    if not karte:
                        await interaction.response.send_message("Karte nicht gefunden.", ephemeral=True)
                        return
                    embed = discord.Embed(
                        title=karte["name"],
                        description=karte["beschreibung"],
                        color=_card_rarity_color(karte),
                    )
                    embed.set_image(url=karte["bild"])
                    
                    # Attacken + Schaden unter der Karte anzeigen (inkl. /verbessern-Buffs des Users)
                    attacks = karte.get("attacks", [])
                    # Mappe Damage-Buffs je Attacke (1..4) für diesen User
                    user_buffs = await get_card_buffs(self.user_id, karte["name"])
                    damage_buff_map = {}
                    for buff_type, attack_number, buff_amount in user_buffs:
                        if buff_type == "damage" and 1 <= attack_number <= 4:
                            damage_buff_map[attack_number] = damage_buff_map.get(attack_number, 0) + buff_amount
                    
                    if attacks:
                        lines = []
                        for idx, atk in enumerate(attacks, start=1):
                            dmg = atk.get("damage")
                            buff = damage_buff_map.get(idx, 0)
                            min_b, max_b = _damage_range_with_max_bonus(dmg, max_only_bonus=buff, flat_bonus=0)
                            dmg_text = f"{min_b}-{max_b}"
                            info_text = str(atk.get("info") or "").strip()
                            if info_text:
                                lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden\n  ↳ {info_text}")
                            else:
                                lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden")
                        embed.add_field(name="Attacken", value="\n".join(lines), inline=False)
                    
                    # Buttons für Attacken anzeigen, aber deaktiviert (kein Effekt beim Klicken)
                    view_buttons = RestrictedView(timeout=60)
                    for i, atk in enumerate(attacks[:4]):
                        dmg = atk.get("damage")
                        buff = damage_buff_map.get(i + 1, 0)
                        min_b, max_b = _damage_range_with_max_bonus(dmg, max_only_bonus=buff, flat_bonus=0)
                        dmg_text = f"{min_b}-{max_b}"
                        btn = ui.Button(
                            label=f"{atk.get('name', f'Attacke {i+1}')} ({dmg_text})",
                            style=discord.ButtonStyle.danger,
                            disabled=True,
                            row=0 if i < 2 else 1
                        )
                        view_buttons.add_item(btn)
                    
                    await interaction.response.send_message(embed=embed, view=view_buttons, ephemeral=True)
                sel.callback = handle_sel

                prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=page_index==0)
                next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=page_index==len(pages)-1)

                async def on_prev(interaction: discord.Interaction):
                    if interaction.user.id != self.user_id:
                        await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
                        return
                    await send_page(interaction, page_index-1)

                async def on_next(interaction: discord.Interaction):
                    if interaction.user.id != self.user_id:
                        await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
                        return
                    await send_page(interaction, page_index+1)

                prev_btn.callback = on_prev
                next_btn.callback = on_next

                v = RestrictedView(timeout=120)
                v.add_item(sel)
                v.add_item(prev_btn)
                v.add_item(next_btn)

                # Falls dies eine Folgeaktion ist, verwende followup, sonst response
                try:
                    await interaction.response.send_message("Wähle eine Karte:", view=v, ephemeral=True)
                except discord.InteractionResponded:
                    await interaction.followup.send("Wähle eine Karte:", view=v, ephemeral=True)

            await send_page(interaction, current_index)

class GiveCardSelectView(RestrictedView):
    def __init__(self, user_id, target_user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.target_user_id = target_user_id
        self.value = None
        # Füge alle Karten aus karten.py hinzu + Infinitydust
        self.options = [SelectOption(label=karte["name"], value=karte["name"]) for karte in karten]
        self.options.append(SelectOption(label="💎 Infinitydust", value="infinitydust"))
        self.pages = [self.options[i:i + 25] for i in range(0, len(self.options), 25)] or [[]]
        self.page_index = 0

        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options_for_current_page(),
            row=0,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        self.prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        self.next_btn = ui.Button(
            label="Weiter",
            style=discord.ButtonStyle.secondary,
            disabled=(len(self.pages) <= 1),
            row=1,
        )
        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def _placeholder(self) -> str:
        return f"Wähle eine Karte oder Infinitydust... (Seite {self.page_index + 1}/{len(self.pages)})"

    def _build_options_for_current_page(self) -> list[SelectOption]:
        return self.pages[self.page_index]

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann die Karte wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index > 0:
            self.page_index -= 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
        await interaction.response.edit_message(view=self)

class GiveOpActionView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.value: str | None = None
        options = [
            SelectOption(label="card give", value="card_give", description="Eine Karte geben"),
            SelectOption(label="card remove", value="card_remove", description="Eine Karte wegnehmen"),
            SelectOption(label="card give-group", value="group_give", description="Kartengruppe geben"),
            SelectOption(label="card remove-group", value="group_remove", description="Kartengruppe wegnehmen"),
            SelectOption(label="ad user", value="add_user", description="Nutzer für /op-verwaltung freischalten"),
            SelectOption(label="remove user", value="remove_user", description="Nutzer für /op-verwaltung entfernen"),
            SelectOption(label="ad role", value="add_role", description="Rolle für /op-verwaltung freischalten"),
            SelectOption(label="remove role", value="remove_role", description="Rolle für /op-verwaltung entfernen"),
        ]
        self.select = ui.Select(
            placeholder="Wähle eine Aktion...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

class GiveOpRaritySelectView(RestrictedView):
    def __init__(self, user_id: int, rarity_keys: list[str]):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value: str | None = None
        options = [
            SelectOption(label=_rarity_label_from_key(key)[:100], value=key)
            for key in rarity_keys[:25]
        ]
        if not options:
            options = [SelectOption(label="Keine Gruppen verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder="Wähle eine Karten-Gruppe...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann wählen!", ephemeral=True)
            return
        selected = self.select.values[0]
        if selected == "__none__":
            await interaction.response.send_message("❌ Keine Gruppen verfügbar.", ephemeral=True)
            return
        self.value = selected
        self.stop()
        await interaction.response.defer()

class GiveOpRolePicker(ui.RoleSelect):
    def __init__(self, parent_view: "GiveOpRoleSelectView"):
        super().__init__(placeholder="Wähle eine Rolle...", min_values=1, max_values=1)
        self.parent_view: GiveOpRoleSelectView = parent_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.user_id:
            await interaction.response.send_message("Nur der Command-User kann wählen!", ephemeral=True)
            return
        if not self.values:
            await interaction.response.send_message("❌ Keine Rolle gewählt.", ephemeral=True)
            return
        selected_role = self.values[0]
        self.parent_view.value = selected_role.id
        stop_callback = getattr(self.parent_view, "stop", None)
        if callable(stop_callback):
            stop_callback()
        await interaction.response.defer()

class GiveOpRoleSelectView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value: int | None = None
        self.add_item(GiveOpRolePicker(self))

# View für Infinitydust-Mengen-Auswahl
class InfinitydustAmountView(RestrictedView):
    def __init__(self, user_id, target_user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.target_user_id = target_user_id
        self.value = None
        
        # Erstelle Optionen für Mengen: 1-20, dann 25, 30, 40, 50, 70 (25 Optionen total)
        amounts = list(range(1, 21)) + [25, 30, 40, 50, 70]
        options = [SelectOption(label=f"{i}x Infinitydust", value=str(i)) for i in amounts]
        self.select = ui.Select(placeholder="Wähle die Menge...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann die Menge wählen!", ephemeral=True)
            return
        self.value = int(self.select.values[0])
        self.stop()
        await interaction.response.defer()

# View für Mission-Auswahl
class MissionAcceptView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_ACCEPT

    def __init__(self, user_id, mission_data, *, request_id: int, visibility: str, is_admin: bool):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_data = mission_data
        self.request_id = request_id
        self.visibility = visibility
        self.is_admin = is_admin

    def durable_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "mission_data": _json_clone(self.mission_data),
            "request_id": self.request_id,
            "visibility": self.visibility,
            "is_admin": self.is_admin,
        }

    @ui.button(label="Annehmen", style=discord.ButtonStyle.success, custom_id="mission_accept:accept")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann annehmen!", ephemeral=True)
            return
        if not await claim_mission_request(self.request_id, "accepted"):
            await interaction.response.send_message("❌ Diese Missions-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not self.is_admin:
            await increment_mission_count(self.user_id)
        await _begin_mission_thread_flow(interaction, self.mission_data, self.is_admin)
        self.stop()

    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="mission_accept:decline")
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann ablehnen!", ephemeral=True)
            return
        if not await claim_mission_request(self.request_id, "declined"):
            await interaction.response.send_message("❌ Diese Missions-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        await interaction.response.send_message("Mission abgelehnt.", ephemeral=True)
        self.stop()

# View für initiale Missions-Kartenwahl
class MissionStartCardSelectView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_CARD_SELECT

    def __init__(self, user_id: int, mission_data: dict, *, is_admin: bool, user_karten: list[str] | None = None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_data = mission_data
        self.is_admin = is_admin
        self.user_karten = [str(name) for name in (user_karten or []) if str(name).strip()]
        options = [SelectOption(label=name, value=name) for name in self.user_karten[:25]]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder="Wähle deine Karte für die Mission...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="mission_card_select:start",
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def durable_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "mission_data": _json_clone(self.mission_data),
            "is_admin": self.is_admin,
            "user_karten": list(self.user_karten),
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        selected_name = str(self.select.values[0] or "").strip()
        if not selected_name or selected_name == "__none__":
            await interaction.response.send_message("❌ Keine gültige Karte verfügbar.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission start card select view")
        await _start_mission_wave_in_thread(
            interaction,
            mission_state={
                "mission_data": _json_clone(self.mission_data),
                "is_admin": self.is_admin,
                "selected_card_name": selected_name,
                "next_wave": 1,
                "total_waves": int(self.mission_data.get("waves", 1) or 1),
            },
        )
        self.stop()


class MissionPauseView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_PAUSE

    def __init__(self, user_id, current_card_name, mission_state: dict[str, Any]):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.current_card_name = current_card_name
        self.mission_state = mission_state
        options = [
            SelectOption(label=f"Beibehalten: {current_card_name}", value="keep"),
            SelectOption(label="Neue Karte wählen", value="change"),
        ]
        self.select = ui.Select(
            placeholder="Was möchtest du tun?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="mission_pause:choice",
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def durable_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "current_card_name": self.current_card_name,
            "mission_state": _json_clone(self.mission_state),
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        choice = str(self.select.values[0] or "").strip()
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission pause view")
        if choice == "change":
            user_karten = _sort_user_cards_like_karten(await get_user_karten(self.user_id))
            next_view = MissionNewCardSelectView(self.user_id, user_karten, mission_state=self.mission_state)
            await _safe_send_channel(interaction, interaction.channel, content="Wähle eine neue Karte:", view=next_view)
        else:
            await _start_mission_wave_in_thread(interaction, mission_state=self.mission_state)
        self.stop()


class MissionNewCardSelectView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_NEW_CARD_SELECT

    def __init__(self, user_id, user_karten, *, mission_state: dict[str, Any]):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_state = mission_state
        self.user_karten = [str(karte_name) for karte_name, _amount in user_karten]
        options = [SelectOption(label=karte_name, value=karte_name) for karte_name in self.user_karten[:25]]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder="Wähle eine neue Karte...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="mission_new_card_select:pick",
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def durable_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "user_karten": [[name, 1] for name in self.user_karten],
            "mission_state": _json_clone(self.mission_state),
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        selected_name = str(self.select.values[0] or "").strip()
        if not selected_name or selected_name == "__none__":
            await interaction.response.send_message("❌ Keine gültige Karte verfügbar.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission new-card select view")
        next_state = dict(self.mission_state)
        next_state["selected_card_name"] = selected_name
        await _start_mission_wave_in_thread(interaction, mission_state=next_state)
        self.stop()



# View für Mission-Kämpfe (interaktiv)
class MissionBattleView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_BATTLE

    def __init__(
        self,
        player_card: CardData,
        bot_card: CardData,
        user_id: int,
        wave_num: int,
        total_waves: int,
        *,
        mission_data: dict[str, Any] | None = None,
        is_admin: bool = False,
        selected_card_name: str | None = None,
    ):
        super().__init__(timeout=None)
        self.player_card = player_card
        self.bot_card = bot_card
        self.user_id = user_id
        self.wave_num = wave_num
        self.total_waves = total_waves
        self.mission_data = mission_data or {}
        self.is_admin = is_admin
        self.selected_card_name = selected_card_name or str(player_card.get("name") or "")
        self.result = None
        self.session_id: int | None = None
        base_player_hp = int(player_card.get("hp", 100) or 100)
        base_bot_hp = int(bot_card.get("hp", 100) or 100)
        self._hp_by_player = {self.user_id: base_player_hp, 0: base_bot_hp}
        self._max_hp_by_player = {self.user_id: base_player_hp, 0: base_bot_hp}
        self._card_names_by_player = {
            self.user_id: str(player_card.get("name") or "Spieler"),
            0: str(bot_card.get("name") or "Bot"),
        }
        self.current_turn = user_id  # Spieler beginnt
        self.attacks = player_card.get("attacks", [
            {"name": "Punch", "damage": 20},
            {"name": "Kick", "damage": 25},
            {"name": "Special", "damage": 30},
            {"name": "Ultimate", "damage": 40}
        ])
        self.round_counter = 0
        self.battle_log_message: discord.Message | None = None
        self._battle_log_text_cache = ""
        self._last_log_edit_ts = 0.0

        # Buff-Speicher
        self.health_bonus = 0
        # Map: attack_number (1..4) -> total damage bonus
        self.damage_bonuses = {}
        runtime_maps = battle_state.build_battle_runtime_maps((self.user_id, 0))
        self.active_effects = runtime_maps["active_effects"]
        self.confused_next_turn = runtime_maps["confused_next_turn"]
        self._cooldowns_by_player = runtime_maps["cooldowns_by_player"]
        self.user_attack_cooldowns = self._cooldowns_by_player[self.user_id]
        self.bot_attack_cooldowns = self._cooldowns_by_player[0]
        self.manual_reload_needed = runtime_maps["manual_reload_needed"]
        self.stunned_next_turn = runtime_maps["stunned_next_turn"]
        self.special_lock_next_turn = runtime_maps["special_lock_next_turn"]
        self.blind_next_attack = runtime_maps["blind_next_attack"]
        self.pending_flat_bonus = runtime_maps["pending_flat_bonus"]
        self.pending_flat_bonus_uses = runtime_maps["pending_flat_bonus_uses"]
        self.pending_multiplier = runtime_maps["pending_multiplier"]
        self.pending_multiplier_uses = runtime_maps["pending_multiplier_uses"]
        self.force_max_next = runtime_maps["force_max_next"]
        self.guaranteed_hit_next = runtime_maps["guaranteed_hit_next"]
        self.incoming_modifiers = runtime_maps["incoming_modifiers"]
        self.outgoing_attack_modifiers = runtime_maps["outgoing_attack_modifiers"]
        self.absorbed_damage = runtime_maps["absorbed_damage"]
        self.delayed_defense_queue = runtime_maps["delayed_defense_queue"]
        self.airborne_pending_landing = runtime_maps["airborne_pending_landing"]
        self._last_damage_roll_meta: dict | None = None
        
        # Setze Button-Labels (evtl. nach init_with_buffs erneut aufrufen)
        self.update_attack_buttons_mission()

    def durable_payload(self) -> dict[str, Any]:
        return {"session_id": self.session_id} if self.session_id else {}

    def durable_log_text(self) -> str:
        if not self.battle_log_message or not self.battle_log_message.embeds:
            return str(getattr(self, "_battle_log_text_cache", "") or "")
        return str(self.battle_log_message.embeds[0].description or "")

    def serialize_session_payload(self) -> dict[str, Any]:
        return {
            "player_card": _json_clone(self.player_card),
            "bot_card": _json_clone(self.bot_card),
            "user_id": self.user_id,
            "wave_num": self.wave_num,
            "total_waves": self.total_waves,
            "mission_data": _json_clone(self.mission_data),
            "is_admin": self.is_admin,
            "selected_card_name": self.selected_card_name,
            "current_turn": self.current_turn,
            "hp_by_player": _json_clone(self._hp_by_player),
            "max_hp_by_player": _json_clone(self._max_hp_by_player),
            "card_names_by_player": _json_clone(self._card_names_by_player),
            "health_bonus": self.health_bonus,
            "damage_bonuses": _json_clone(self.damage_bonuses),
            "active_effects": _json_clone(self.active_effects),
            "confused_next_turn": _json_clone(self.confused_next_turn),
            "cooldowns_by_player": _json_clone(self._cooldowns_by_player),
            "manual_reload_needed": _json_clone(self.manual_reload_needed),
            "stunned_next_turn": _json_clone(self.stunned_next_turn),
            "special_lock_next_turn": _json_clone(self.special_lock_next_turn),
            "blind_next_attack": _json_clone(self.blind_next_attack),
            "pending_flat_bonus": _json_clone(self.pending_flat_bonus),
            "pending_flat_bonus_uses": _json_clone(self.pending_flat_bonus_uses),
            "pending_multiplier": _json_clone(self.pending_multiplier),
            "pending_multiplier_uses": _json_clone(self.pending_multiplier_uses),
            "force_max_next": _json_clone(self.force_max_next),
            "guaranteed_hit_next": _json_clone(self.guaranteed_hit_next),
            "incoming_modifiers": _json_clone(self.incoming_modifiers),
            "outgoing_attack_modifiers": _json_clone(self.outgoing_attack_modifiers),
            "absorbed_damage": _json_clone(self.absorbed_damage),
            "delayed_defense_queue": _json_clone(self.delayed_defense_queue),
            "airborne_pending_landing": _json_clone(self.airborne_pending_landing),
            "round_counter": self.round_counter,
            "battle_log_text": self.durable_log_text(),
        }

    def restore_from_session_payload(self, payload: dict[str, Any]) -> None:
        player_card = _dict_str_any(payload.get("player_card"))
        bot_card = _dict_str_any(payload.get("bot_card"))
        if player_card:
            self.player_card = cast(CardData, player_card)
        if bot_card:
            self.bot_card = cast(CardData, bot_card)
        self.user_id = int(payload.get("user_id", self.user_id) or self.user_id)
        self.wave_num = int(payload.get("wave_num", self.wave_num) or self.wave_num)
        self.total_waves = int(payload.get("total_waves", self.total_waves) or self.total_waves)
        self.mission_data = _dict_str_any(payload.get("mission_data")) or self.mission_data
        self.is_admin = bool(payload.get("is_admin", self.is_admin))
        self.selected_card_name = str(payload.get("selected_card_name") or self.selected_card_name)
        self.current_turn = int(payload.get("current_turn", self.current_turn) or self.current_turn)
        self._hp_by_player = _int_keyed_int_dict(payload.get("hp_by_player"))
        self._max_hp_by_player = _int_keyed_int_dict(payload.get("max_hp_by_player"))
        raw_card_names = _int_keyed_dict(payload.get("card_names_by_player"))
        self._card_names_by_player = {key: str(value or "") for key, value in raw_card_names.items()}
        self.health_bonus = int(payload.get("health_bonus", 0) or 0)
        self.damage_bonuses = _int_keyed_int_dict(payload.get("damage_bonuses"))
        self.active_effects = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("active_effects")).items()}
        self.confused_next_turn = _int_keyed_bool_dict(payload.get("confused_next_turn"))
        self._cooldowns_by_player = _nested_int_keyed_int_dict(payload.get("cooldowns_by_player"))
        self.user_attack_cooldowns = self._cooldowns_by_player.setdefault(self.user_id, {})
        self.bot_attack_cooldowns = self._cooldowns_by_player.setdefault(0, {})
        self.manual_reload_needed = {
            key: {inner_key: bool(inner_value) for inner_key, inner_value in value.items()}
            for key, value in _nested_int_keyed_dict(payload.get("manual_reload_needed")).items()
        }
        self.stunned_next_turn = _int_keyed_bool_dict(payload.get("stunned_next_turn"))
        self.special_lock_next_turn = _int_keyed_bool_dict(payload.get("special_lock_next_turn"))
        self.blind_next_attack = _int_keyed_float_dict(payload.get("blind_next_attack"))
        self.pending_flat_bonus = _int_keyed_int_dict(payload.get("pending_flat_bonus"))
        self.pending_flat_bonus_uses = _int_keyed_int_dict(payload.get("pending_flat_bonus_uses"))
        self.pending_multiplier = _int_keyed_float_dict(payload.get("pending_multiplier"))
        self.pending_multiplier_uses = _int_keyed_int_dict(payload.get("pending_multiplier_uses"))
        self.force_max_next = _int_keyed_int_dict(payload.get("force_max_next"))
        self.guaranteed_hit_next = _int_keyed_int_dict(payload.get("guaranteed_hit_next"))
        self.incoming_modifiers = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("incoming_modifiers")).items()}
        self.outgoing_attack_modifiers = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("outgoing_attack_modifiers")).items()}
        self.absorbed_damage = _int_keyed_int_dict(payload.get("absorbed_damage"))
        self.delayed_defense_queue = {key: list(value) if isinstance(value, list) else [] for key, value in _int_keyed_dict(payload.get("delayed_defense_queue")).items()}
        raw_airborne = _int_keyed_dict(payload.get("airborne_pending_landing"))
        self.airborne_pending_landing = {key: (value if isinstance(value, dict) else None) for key, value in raw_airborne.items()}
        self.round_counter = int(payload.get("round_counter", 0) or 0)
        self._battle_log_text_cache = str(payload.get("battle_log_text") or "")
        self.attacks = list(self.player_card.get("attacks", self.attacks))
        self.update_attack_buttons_mission()

    async def persist_session(
        self,
        channel: object,
        *,
        status: str = "active",
        battle_message: discord.Message | None = None,
    ) -> None:
        guild = getattr(channel, "guild", None)
        channel_id = getattr(channel, "id", None)
        if not isinstance(guild, discord.Guild) or not isinstance(channel_id, int):
            return
        if battle_message is not None:
            self.bind_durable_message(guild_id=guild.id, channel_id=channel_id, message_id=battle_message.id)
        self.session_id = await save_active_session(
            session_id=self.session_id,
            kind="mission",
            guild_id=guild.id,
            channel_id=channel_id,
            thread_id=channel_id if isinstance(channel, discord.Thread) else None,
            battle_message_id=self._durable_message_id,
            log_message_id=self.battle_log_message.id if self.battle_log_message else None,
            status=status,
            payload=self.serialize_session_payload(),
        )
        if self._durable_message_id is not None:
            await upsert_durable_view(
                guild_id=guild.id,
                channel_id=channel_id,
                message_id=self._durable_message_id,
                view_kind=self.durable_view_kind,
                payload=self.durable_payload(),
            )

    def create_current_embed(self, *, description: str | None = None) -> discord.Embed:
        embed = discord.Embed(
            title=f"⚔️ Welle {self.wave_num}/{self.total_waves}",
            description=description or f"Du kämpfst gegen **{self.bot_card['name']}**!",
        )
        player_label = f"🟥 Deine Karte{self._status_icons(self.user_id)}"
        bot_label = f"🟦 Bot Karte{self._status_icons(0)}"
        embed.add_field(name=player_label, value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
        embed.add_field(name=bot_label, value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
        if self.player_card.get("bild"):
            embed.set_image(url=str(self.player_card["bild"]))
        if self.bot_card.get("bild"):
            embed.set_thumbnail(url=str(self.bot_card["bild"]))
        _add_attack_info_field(embed, self.player_card)
        return embed

    async def _complete_wave(
        self,
        interaction: discord.Interaction,
        message: discord.Message | None,
        *,
        won: bool,
        cancel_actor: discord.abc.User | None = None,
        detail_text: str | None = None,
    ) -> None:
        if message is not None:
            try:
                summary_embed = self.create_current_embed(description=detail_text or ("Welle gewonnen." if won else "Welle verloren."))
                await message.edit(embed=summary_embed, view=None)
            except Exception:
                logging.exception("Failed to update mission battle message at wave completion")
        status = "completed" if won else ("cancelled" if cancel_actor is not None else "failed")
        await self.persist_session(interaction.channel, status=status)
        if not won:
            if cancel_actor is not None:
                await _safe_send_channel(
                    interaction,
                    interaction.channel,
                    content=(
                        f"⚔️ Mission abgebrochen von {cancel_actor.mention}.\n\n"
                        "Gab es einen Bug/Fehler? Nutze die Buttons unten."
                    ),
                )
            await _send_mission_feedback_prompt(
                interaction.channel,
                interaction.guild,
                allowed_user_id=self.user_id,
                battle_log_text=self.durable_log_text(),
            )
            return

        next_wave = self.wave_num + 1
        if next_wave > self.total_waves:
            reward_card = _dict_str_any(self.mission_data.get("reward_card"))
            if reward_card:
                is_new_card = await check_and_add_karte(self.user_id, reward_card)
                await _safe_send_channel(
                    interaction,
                    interaction.channel,
                    embed=_mission_success_embed(reward_card, self.total_waves, is_new_card=is_new_card),
                )
            await _send_mission_feedback_prompt(
                interaction.channel,
                interaction.guild,
                allowed_user_id=self.user_id,
                battle_log_text=self.durable_log_text(),
            )
            return

        next_state: dict[str, Any] = {
            "mission_data": _json_clone(self.mission_data),
            "is_admin": self.is_admin,
            "selected_card_name": self.selected_card_name,
            "next_wave": next_wave,
            "total_waves": self.total_waves,
        }
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"🏆 Welle {self.wave_num} gewonnen! Welle {next_wave} startet jetzt.",
        )
        if self.total_waves > 4 and next_wave == 4:
            pause_view = MissionPauseView(self.user_id, self.selected_card_name, mission_state=next_state)
            await _safe_send_channel(
                interaction,
                interaction.channel,
                content="⏸️ Pause nach der 3. Welle. Möchtest du deine Karte wechseln?",
                view=pause_view,
            )
            return
        await _start_mission_wave_in_thread(interaction, mission_state=next_state)

    @property
    def player_hp(self) -> int:
        return int(self._hp_by_player[self.user_id])

    @player_hp.setter
    def player_hp(self, value: int) -> None:
        self._hp_by_player[self.user_id] = max(0, int(value))

    @property
    def bot_hp(self) -> int:
        return int(self._hp_by_player[0])

    @bot_hp.setter
    def bot_hp(self, value: int) -> None:
        self._hp_by_player[0] = max(0, int(value))

    @property
    def player_max_hp(self) -> int:
        return int(self._max_hp_by_player[self.user_id])

    @player_max_hp.setter
    def player_max_hp(self, value: int) -> None:
        self._max_hp_by_player[self.user_id] = max(0, int(value))

    @property
    def bot_max_hp(self) -> int:
        return int(self._max_hp_by_player[0])

    @bot_max_hp.setter
    def bot_max_hp(self, value: int) -> None:
        self._max_hp_by_player[0] = max(0, int(value))

    async def init_with_buffs(self) -> None:
        buffs = await get_card_buffs(self.user_id, self.player_card["name"])
        total_health, damage_map = battle_state.summarize_card_buffs(buffs)
        self.health_bonus = total_health
        self.damage_bonuses = damage_map
        self.player_hp += self.health_bonus
        self.player_max_hp = self.player_hp
        self.update_attack_buttons_mission()

    def mission_get_attack_max_damage(self, attack_damage, damage_buff: int = 0):
        return battle_state.get_attack_max_damage(attack_damage, damage_buff)

    def mission_get_attack_min_damage(self, attack_damage, damage_buff: int = 0):
        return battle_state.get_attack_min_damage(attack_damage, damage_buff)

    def mission_is_strong_attack(self, attack_damage, damage_buff: int = 0) -> bool:
        return battle_state.is_strong_attack(attack_damage, damage_buff)

    def _status_icons(self, target_id: int) -> str:
        return battle_state.status_icons(self.active_effects, target_id)

    async def _safe_edit_battle_log(self, embed) -> None:
        if not self.battle_log_message:
            return
        try:
            last_ts = float(getattr(self, "_last_log_edit_ts", 0.0) or 0.0)
        except Exception:
            last_ts = 0.0
        now = time.monotonic()
        if now - last_ts < 0.9:
            await asyncio.sleep(0.9 - (now - last_ts))
        for attempt in range(2):
            try:
                await self.battle_log_message.edit(embed=embed)
                self._last_log_edit_ts = time.monotonic()
                return
            except Exception as e:
                if getattr(e, "status", None) == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                logging.exception("Failed to edit battle log")
                return

    def is_attack_on_cooldown_user(self, attack_index: int) -> bool:
        return battle_state.is_attack_on_cooldown(self.user_attack_cooldowns, attack_index)

    def is_attack_on_cooldown_bot(self, attack_index: int) -> bool:
        return battle_state.is_attack_on_cooldown(self.bot_attack_cooldowns, attack_index)

    def start_attack_cooldown_user(self, attack_index: int, turns: int = 1) -> None:
        battle_state.start_attack_cooldown(self.user_attack_cooldowns, attack_index, turns=turns)

    def start_attack_cooldown_bot(self, attack_index: int, turns: int = 1) -> None:
        battle_state.start_attack_cooldown(self.bot_attack_cooldowns, attack_index, turns=turns)

    def is_reload_needed(self, player_id: int, attack_index: int) -> bool:
        return battle_state.is_reload_needed(self.manual_reload_needed, player_id, attack_index)

    def set_reload_needed(self, player_id: int, attack_index: int, needed: bool) -> None:
        battle_state.set_reload_needed(self.manual_reload_needed, player_id, attack_index, needed)

    def set_confusion(self, player_id: int, applier_id: int) -> None:
        battle_state.set_confusion(self.active_effects, self.confused_next_turn, player_id, applier_id)

    def consume_confusion_if_any(self, player_id: int) -> None:
        battle_state.consume_confusion_if_any(self.active_effects, self.confused_next_turn, player_id)

    def _find_effect(self, player_id: int, effect_type: str):
        return battle_state.find_effect(self.active_effects, player_id, effect_type)

    def has_stealth(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "stealth")

    def has_airborne(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "airborne")

    def consume_stealth(self, player_id: int) -> bool:
        return battle_state.consume_effect(self.active_effects, player_id, "stealth")

    def grant_stealth(self, player_id: int) -> None:
        battle_state.grant_unique_effect(self.active_effects, player_id, "stealth", player_id, duration=1)

    def _append_effect_event(self, events: list[str], text: str) -> None:
        battle_state.append_effect_event(events, text)

    def _append_multi_hit_roll_event(self, effect_events: list[str]) -> None:
        meta = self._last_damage_roll_meta or {}
        if meta.get("kind") != "multi_hit":
            return
        details = meta.get("details")
        if not isinstance(details, dict):
            return
        hits = int(details.get("hits", 0) or 0)
        landed = int(details.get("landed_hits", 0) or 0)
        per_hit = details.get("per_hit_damages", [])
        per_hit_numbers: list[int] = []
        if isinstance(per_hit, list):
            for value in per_hit:
                try:
                    per_hit_numbers.append(int(value))
                except Exception:
                    continue
        per_hit_text = ", ".join(str(v) for v in per_hit_numbers) if per_hit_numbers else "-"
        total_damage = int(details.get("total_damage", 0) or 0)
        self._append_effect_event(
            effect_events,
            f"Treffer: {landed}/{hits} | Schaden pro Treffer: {per_hit_text} | Gesamt: {total_damage}.",
        )

    def _grant_airborne(self, player_id: int) -> None:
        battle_state.grant_unique_effect(self.active_effects, player_id, "airborne", player_id, duration=1)

    def _clear_airborne(self, player_id: int) -> None:
        battle_state.consume_effect(self.active_effects, player_id, "airborne")

    def queue_delayed_defense(
        self,
        player_id: int,
        defense: str,
        counter: int = 0,
        source: str | None = None,
    ) -> None:
        battle_state.queue_delayed_defense(
            self.delayed_defense_queue,
            player_id,
            defense,
            counter=counter,
            source=source,
        )

    def activate_delayed_defense_after_attack(
        self,
        player_id: int,
        effect_events: list[str],
        *,
        attack_landed: bool,
    ) -> None:
        battle_state.activate_delayed_defense_after_attack(
            self.delayed_defense_queue,
            self.active_effects,
            self.incoming_modifiers,
            player_id,
            effect_events,
            attack_landed=attack_landed,
        )

    def start_airborne_two_phase(
        self,
        player_id: int,
        landing_damage,
        effect_events: list[str],
        *,
        source_attack_index: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        battle_state.start_airborne_two_phase(
            self.active_effects,
            self.airborne_pending_landing,
            self.incoming_modifiers,
            player_id,
            landing_damage,
            effect_events,
            source_attack_index=source_attack_index,
            cooldown_turns=cooldown_turns,
        )

    def resolve_forced_landing_if_due(self, player_id: int, effect_events: list[str]) -> dict | None:
        return battle_state.resolve_forced_landing_if_due(
            self.active_effects,
            self.airborne_pending_landing,
            player_id,
            effect_events,
        )

    def _max_hp_for(self, player_id: int) -> int:
        return battle_state.max_hp_for(self._max_hp_by_player, player_id)

    def _hp_for(self, player_id: int) -> int:
        return battle_state.hp_for(self._hp_by_player, player_id)

    def _set_hp_for(self, player_id: int, value: int) -> None:
        battle_state.set_hp_for(self._hp_by_player, player_id, value)

    def heal_player(self, player_id: int, amount: int) -> int:
        return battle_state.heal_player(self._hp_by_player, self._max_hp_by_player, player_id, amount)

    def _apply_non_heal_damage(self, player_id: int, amount: int) -> int:
        return battle_state.apply_non_heal_damage(self._hp_by_player, player_id, amount)

    def _card_name_for(self, player_id: int) -> str:
        fallback = "Bot" if player_id == 0 else "Spieler"
        return battle_state.card_name_for(self._card_names_by_player, player_id, fallback=fallback)

    def _apply_non_heal_damage_with_event(
        self,
        events: list[str],
        player_id: int,
        amount: int,
        *,
        source: str,
        self_damage: bool,
    ) -> int:
        return battle_state.apply_non_heal_damage_with_event(
            self._hp_by_player,
            self._card_names_by_player,
            events,
            player_id,
            amount,
            source=source,
            self_damage=self_damage,
        )

    def _guard_non_heal_damage_result(self, defender_id: int, defender_hp_before: int, context: str) -> None:
        battle_state.guard_non_heal_damage_result(self._hp_by_player, defender_id, defender_hp_before, context)

    def queue_incoming_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        reflect: float = 0.0,
        store_ratio: float = 0.0,
        cap: int | str | None = None,
        evade: bool = False,
        counter: int = 0,
        turns: int = 1,
        source: str | None = None,
    ) -> None:
        battle_state.queue_incoming_modifier(
            self.incoming_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            reflect=reflect,
            store_ratio=store_ratio,
            cap=cap,
            evade=evade,
            counter=counter,
            turns=turns,
            source=source,
        )

    def _consume_airborne_evade_marker(self, player_id: int) -> bool:
        modifiers = self.incoming_modifiers.get(player_id) or []
        for idx, mod in enumerate(modifiers):
            if not isinstance(mod, dict):
                continue
            if not bool(mod.get("evade")):
                continue
            if str(mod.get("source") or "").strip().lower() != "airborne":
                continue
            try:
                modifiers.pop(idx)
            except Exception:
                logging.exception("Unexpected error")
                return False
            return True
        return False

    def queue_outgoing_attack_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        turns: int = 1,
        source: str | None = None,
    ) -> None:
        battle_state.queue_outgoing_attack_modifier(
            self.outgoing_attack_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            turns=turns,
            source=source,
        )

    def _apply_outgoing_attack_modifiers_with_details(
        self,
        attacker_id: int,
        raw_damage: int,
    ) -> tuple[int, int, dict[str, object] | None]:
        reduced_damage, overflow_self_damage, modifier_details = battle_state.apply_outgoing_attack_modifiers(
            self.outgoing_attack_modifiers,
            attacker_id,
            raw_damage,
        )
        return reduced_damage, overflow_self_damage, modifier_details

    def apply_outgoing_attack_modifiers(self, attacker_id: int, raw_damage: int) -> tuple[int, int]:
        reduced_damage, overflow_self_damage, _modifier_details = self._apply_outgoing_attack_modifiers_with_details(
            attacker_id,
            raw_damage,
        )
        return reduced_damage, overflow_self_damage

    def consume_guaranteed_hit(self, player_id: int) -> bool:
        return battle_state.consume_guaranteed_hit(self.guaranteed_hit_next, player_id)

    def roll_attack_damage(
        self,
        attack: dict,
        base_damage,
        damage_buff: int,
        attack_multiplier: float,
        force_max_damage: bool,
        guaranteed_hit: bool,
    ) -> tuple[int, bool, int, int]:
        cap = MAX_ATTACK_DAMAGE_PER_HIT
        multi_hit = attack.get("multi_hit")
        if isinstance(multi_hit, dict):
            actual_damage, min_damage, max_damage, details = _resolve_multi_hit_damage_details(
                multi_hit,
                buff_amount=damage_buff,
                attack_multiplier=attack_multiplier,
                force_max=force_max_damage,
                guaranteed_hit=guaranteed_hit,
            )
            actual_damage = min(cap, max(0, int(actual_damage)))
            min_damage = min(cap, max(0, int(min_damage)))
            max_damage = min(cap, max(min_damage, int(max_damage)))
            if isinstance(details, dict):
                details["total_damage"] = actual_damage
            self._last_damage_roll_meta = {"kind": "multi_hit", "details": details}
            is_critical = bool(force_max_damage and actual_damage >= max_damage and max_damage > 0)
            return actual_damage, is_critical, min_damage, max_damage

        self._last_damage_roll_meta = {"kind": "single_hit"}
        actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, damage_buff)
        if attack_multiplier != 1.0:
            actual_damage = int(round(actual_damage * attack_multiplier))
            max_damage = int(round(max_damage * attack_multiplier))
            min_damage = int(round(min_damage * attack_multiplier))
        if force_max_damage:
            actual_damage = max_damage
            is_critical = max_damage > 0
        min_damage = min(cap, max(0, int(min_damage)))
        max_damage = min(cap, max(min_damage, int(max_damage)))
        actual_damage = min(cap, max(0, int(actual_damage)))
        return actual_damage, is_critical, min_damage, max_damage

    def _resolve_incoming_modifiers_with_details(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int, dict[str, object] | None]:
        return battle_state.resolve_incoming_modifiers(
            self.incoming_modifiers,
            self.absorbed_damage,
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            incoming_min_damage=incoming_min_damage,
        )

    def resolve_incoming_modifiers(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int]:
        final_damage, reflected_damage, dodged, counter_damage, _modifier_details = self._resolve_incoming_modifiers_with_details(
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            incoming_min_damage=incoming_min_damage,
        )
        return final_damage, reflected_damage, dodged, counter_damage

    def _append_incoming_resolution_events(
        self,
        effect_events: list[str],
        *,
        defender_name: str,
        raw_damage: int,
        final_damage: int,
        reflected_damage: int,
        dodged: bool,
        counter_damage: int,
        modifier_details: dict[str, object] | None = None,
        absorbed_before: int | None = None,
        absorbed_after: int | None = None,
    ) -> None:
        defender = str(defender_name or "Verteidiger").strip() or "Verteidiger"
        modifier_source = str((modifier_details or {}).get("source") or "").strip()
        source_suffix = f" durch {_effect_source_name(modifier_source)}" if modifier_source else ""
        if dodged:
            self._append_effect_event(effect_events, f"Ausweichen{source_suffix}: Angriff vollständig verfehlt.")
        elif final_damage < raw_damage:
            self._append_effect_event(
                effect_events,
                _damage_transition_text(
                    int(raw_damage),
                    int(final_damage),
                    source=modifier_source or None,
                    context="Schutzwirkung",
                ),
            )

        if reflected_damage > 0:
            if modifier_source:
                self._append_effect_event(
                    effect_events,
                    f"Reflexion{source_suffix} durch {defender}: {int(reflected_damage)} Schaden zurückgeworfen.",
                )
            else:
                self._append_effect_event(
                    effect_events,
                    f"Spiegeldimension/Reflexion durch {defender}: {int(reflected_damage)} Schaden zurückgeworfen.",
                )
        if counter_damage > 0:
            self._append_effect_event(effect_events, f"Konter{source_suffix} durch {defender}: {int(counter_damage)} Schaden.")

        if (
            absorbed_before is not None
            and absorbed_after is not None
            and int(absorbed_after) > int(absorbed_before)
        ):
            gained = int(absorbed_after) - int(absorbed_before)
            self._append_effect_event(effect_events, f"Absorption{source_suffix} durch {defender}: {gained} Schaden gespeichert.")

    def apply_regen_tick(self, player_id: int) -> int:
        return battle_state.apply_regen_tick(
            self.active_effects,
            self._hp_by_player,
            self._max_hp_by_player,
            player_id,
        )

    def reduce_cooldowns_user(self) -> None:
        battle_state.reduce_cooldowns(self.user_attack_cooldowns)

    def reduce_cooldowns_bot(self) -> None:
        battle_state.reduce_cooldowns(self.bot_attack_cooldowns)

    def update_attack_buttons_mission(self) -> None:
        # Finde alle vier Angriffs-Buttons in den Zeilen 0 und 1
        attack_buttons = [child for child in self.children if isinstance(child, ui.Button) and child.row in (0, 1)]
        attack_buttons = attack_buttons[:4]

        pending_landing = self.airborne_pending_landing.get(self.user_id)
        if pending_landing:
            landing_damage = pending_landing.get("damage", [20, 40])
            if isinstance(landing_damage, list) and len(landing_damage) == 2:
                dmg_text = f"{int(landing_damage[0])}-{int(landing_damage[1])}"
            else:
                dmg_text = "20-40"
            if attack_buttons:
                first = attack_buttons[0]
                first.style = discord.ButtonStyle.danger
                first.label = f"Landungsschlag ({dmg_text}) ✈️"
                first.disabled = False
            for i, btn in enumerate(attack_buttons[1:], start=1):
                btn.style = discord.ButtonStyle.secondary
                if i < len(self.attacks):
                    blocked_attack = self.attacks[i]
                    blocked_name = str(blocked_attack.get("name") or f"Angriff {i+1}")
                    if self.is_attack_on_cooldown_user(i):
                        cooldown_turns = self.user_attack_cooldowns.get(i, 0)
                        btn.label = f"{blocked_name} ({_format_cooldown_label(blocked_attack, cooldown_turns)})"
                    else:
                        btn.label = f"{blocked_name} (Blockiert)"
                else:
                    btn.label = "—"
                btn.disabled = True
            return
        
        for i, button in enumerate(attack_buttons):
            if i < len(self.attacks):
                attack = self.attacks[i]
                base_damage = attack["damage"]
                dmg_max_bonus = self.damage_bonuses.get(i + 1, 0)
                
                # Berechne Damage-Text
                min_dmg, max_dmg = _damage_range_with_max_bonus(base_damage, max_only_bonus=dmg_max_bonus, flat_bonus=0)
                damage_text = f"[{min_dmg}, {max_dmg}]"
                
                # Effekte-Label (🔥/🌀)
                effects = attack.get("effects", [])
                effect_icons: list[str] = []
                for eff in effects:
                    t = eff.get("type")
                    if t == "burning" and "🔥" not in effect_icons:
                        effect_icons.append("🔥")
                    elif t == "confusion" and "🌀" not in effect_icons:
                        effect_icons.append("🌀")
                    elif t == "stealth" and "🥷" not in effect_icons:
                        effect_icons.append("🥷")
                    elif t == "stun" and "🛑" not in effect_icons:
                        effect_icons.append("🛑")
                    elif t in {
                        "damage_reduction",
                        "damage_reduction_flat",
                        "enemy_next_attack_reduction_percent",
                        "enemy_next_attack_reduction_flat",
                        "reflect",
                        "absorb_store",
                        "cap_damage",
                        "delayed_defense_after_next_attack",
                    } and "🛡️" not in effect_icons:
                        effect_icons.append("🛡️")
                    elif t == "airborne_two_phase" and "✈️" not in effect_icons:
                        effect_icons.append("✈️")
                    elif t in {"damage_boost", "damage_multiplier"} and "⚡" not in effect_icons:
                        effect_icons.append("⚡")
                    elif t in {"force_max", "mix_heal_or_max", "guaranteed_hit"} and "🎯" not in effect_icons:
                        effect_icons.append("🎯")
                    elif t in {"heal", "regen"} and "❤️" not in effect_icons:
                        effect_icons.append("❤️")
                heal_label = _heal_label_for_attack(attack)
                if heal_label and "❤️" not in effect_icons:
                    effect_icons.append("❤️")
                effects_label = f" {' '.join(effect_icons)}" if effect_icons else ""

                # Prüfe Cooldown (nur für Spieler)
                is_on_cooldown = self.is_attack_on_cooldown_user(i)
                is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.user_id, i))
                
                if is_on_cooldown:
                    # Grau für Cooldown
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = self.user_attack_cooldowns[i]
                    button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                    button.disabled = True
                elif is_reload_action:
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    if heal_label is not None:
                        default_style = discord.ButtonStyle.success
                        button.label = f"{attack['name']} (+{heal_label}){effects_label}"
                    else:
                        # Rot für normale Attacken
                        default_style = discord.ButtonStyle.danger
                        button.label = f"{attack['name']} ({damage_text}){effects_label}"
                    button.style = _resolve_attack_button_style(attack, default_style)
                    button.disabled = False
            else:
                button.label = f"Angriff {i+1}"

    # Angriffs-Buttons (rot, 2x2 Grid)
    @ui.button(label="Angriff 1", style=discord.ButtonStyle.danger, row=0, custom_id="mission_battle:attack1")
    async def attack1(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 0)

    @ui.button(label="Angriff 2", style=discord.ButtonStyle.danger, row=0, custom_id="mission_battle:attack2")
    async def attack2(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 1)

    @ui.button(label="Angriff 3", style=discord.ButtonStyle.danger, row=1, custom_id="mission_battle:attack3")
    async def attack3(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 2)

    @ui.button(label="Angriff 4", style=discord.ButtonStyle.danger, row=1, custom_id="mission_battle:attack4")
    async def attack4(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 3)

    # Blaue Buttons unten
    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, row=2, custom_id="mission_battle:cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)
            return
        await interaction.response.defer()
        self.result = False
        self.stop()
        message = _interaction_message_or_none(interaction)
        if message is not None:
            try:
                await message.edit(
                    embed=self.create_current_embed(
                        description=f"⚔️ Mission abgebrochen von {interaction.user.mention}.",
                    ),
                    view=None,
                )
            except Exception:
                logging.exception("Failed to update cancelled mission message")
        await self._complete_wave(
            interaction,
            message,
            won=False,
            cancel_actor=interaction.user,
            detail_text=f"⚔️ Mission abgebrochen von {interaction.user.mention}.",
        )

    # Entfernt: Platzhalter-Button

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
        message = _interaction_message_or_none(interaction)
        # Block if fight already ended
        if self.player_hp <= 0 or self.bot_hp <= 0:
            try:
                await interaction.response.send_message("❌ Die Welle ist bereits beendet.", ephemeral=True)
            except Exception:
                logging.exception("Unexpected error")
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)
            return

        if interaction.user.id != self.current_turn:
            await interaction.response.send_message("Du bist nicht an der Reihe!", ephemeral=True)
            return
        await _safe_defer_interaction(interaction)

        effect_events: list[str] = []
        forced_landing_attack = self.resolve_forced_landing_if_due(self.user_id, effect_events)
        is_forced_landing = forced_landing_attack is not None

        regen_heal = self.apply_regen_tick(self.user_id)
        if regen_heal > 0:
            self._append_effect_event(effect_events, f"Regeneration heilt {regen_heal} HP.")

        # SIDE EFFECTS: Apply effects on bot before attack
        defender_id = 0
        effects_to_remove = []
        pre_burn_total = 0
        for effect in self.active_effects[defender_id]:
            if effect.get('applier') == self.user_id and effect.get('type') == 'burning':
                damage = _effect_int(effect, 'damage')
                self.bot_hp -= damage
                self.bot_hp = max(0, self.bot_hp)
                pre_burn_total += damage

                # Decrease duration
                remaining_duration = _effect_int(effect, 'duration') - 1
                effect['duration'] = remaining_duration
                if remaining_duration <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        # Hole Angriff
        if attack_index >= len(self.attacks):
            await _safe_send_interaction_ephemeral(interaction, "Ungültiger Angriff!")
            return

        # COOLDOWN prüfen (Spieler)
        if (not is_forced_landing) and self.is_attack_on_cooldown_user(attack_index):
            await _safe_send_interaction_ephemeral(interaction, "Diese Attacke ist noch auf Cooldown!")
            return
        if (not is_forced_landing) and self.special_lock_next_turn.get(self.user_id, False) and attack_index != 0:
            await _safe_send_interaction_ephemeral(
                interaction,
                "Diese Runde sind nur Standard-Angriffe erlaubt (Attacke 1).",
            )
            return

        if is_forced_landing:
            attack = forced_landing_attack
            damage = attack["damage"]
            is_reload_action = False
            attack_name = attack["name"]
        else:
            attack = self.attacks[attack_index]
            damage = attack["damage"]
            is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.user_id, attack_index))
            attack_name = str(attack.get("reload_name") or "Nachladen") if is_reload_action else attack["name"]
        dmg_buff = 0
        damage_max_bonus = self.damage_bonuses.get(attack_index + 1, 0)
        if is_forced_landing:
            damage_max_bonus = 0

        attacker_hp = self._hp_for(self.user_id)
        attacker_max_hp = self._max_hp_for(self.user_id)
        defender_hp = self._hp_for(0)
        defender_max_hp = self._max_hp_for(0)
        conditional_self_pct = attack.get("bonus_if_self_hp_below_pct")
        conditional_self_bonus = int(attack.get("bonus_damage_if_condition", 0) or 0)
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * float(conditional_self_pct)):
            dmg_buff += conditional_self_bonus
        conditional_enemy_triggered = False
        conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
            conditional_enemy_triggered = True
            damage_if_condition = attack.get("damage_if_condition")
            damage = _coerce_damage_input(damage_if_condition, default=0)
        if damage_max_bonus > 0:
            damage = _apply_max_only_damage_bonus(damage, damage_max_bonus)
        if attack.get("add_absorbed_damage"):
            absorbed_bonus = int(self.absorbed_damage.get(self.user_id, 0) or 0)
            dmg_buff += absorbed_bonus
            self.absorbed_damage[self.user_id] = 0
            base_min, base_max = _range_pair(damage)
            base_text = str(base_min) if base_min == base_max else f"{base_min}-{base_max}"
            self._append_effect_event(
                effect_events,
                f"Kinetische Entladung: Grundschaden {base_text}, durch Absorption +{absorbed_bonus}.",
            )

        is_damaging_attack = self.mission_get_attack_max_damage(damage, 0) > 0
        attack_multiplier = 1.0
        applied_flat_bonus_now = 0
        force_max_damage = False
        if is_damaging_attack:
            if self.pending_flat_bonus_uses.get(self.user_id, 0) > 0:
                flat_bonus_now = int(self.pending_flat_bonus.get(self.user_id, 0))
                dmg_buff += flat_bonus_now
                applied_flat_bonus_now = max(0, flat_bonus_now)
                self.pending_flat_bonus_uses[self.user_id] -= 1
                if self.pending_flat_bonus_uses[self.user_id] <= 0:
                    self.pending_flat_bonus[self.user_id] = 0
                if flat_bonus_now > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{flat_bonus_now} Schaden auf diesen Angriff.")
            if self.pending_multiplier_uses.get(self.user_id, 0) > 0:
                attack_multiplier = float(self.pending_multiplier.get(self.user_id, 1.0) or 1.0)
                self.pending_multiplier_uses[self.user_id] -= 1
                if self.pending_multiplier_uses[self.user_id] <= 0:
                    self.pending_multiplier[self.user_id] = 1.0
                multiplier_pct = int(round((attack_multiplier - 1.0) * 100))
                if multiplier_pct > 0:
                    self._append_effect_event(effect_events, f"Verstärkung aktiv: +{multiplier_pct}% Schaden auf diesen Angriff.")
            if self.force_max_next.get(self.user_id, 0) > 0:
                force_max_damage = True
                self.force_max_next[self.user_id] -= 1

        guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)

        # Confusion handling: 77% self-damage vs 23% normal
        hits_enemy = True
        self_damage = 0
        if is_reload_action:
            actual_damage, is_critical = 0, False
            hits_enemy = False
            self.set_reload_needed(self.user_id, attack_index, False)
        else:
            min_damage = 0
            max_damage = 0
            defender_has_stealth = self.has_stealth(0)
            guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(self.user_id)
            if guaranteed_hit:
                self.blind_next_attack[self.user_id] = 0.0
                self.consume_confusion_if_any(self.user_id)
                self._append_effect_event(effect_events, "Dieser Angriff trifft garantiert.")
            max_dmg_threshold = self.mission_get_attack_max_damage(damage, dmg_buff)
            blind_chance = float(self.blind_next_attack.get(self.user_id, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[self.user_id] = 0.0
                blind_miss = random.random() < blind_chance
            if blind_miss:
                actual_damage, is_critical = 0, False
                hits_enemy = False
                if self.confused_next_turn.get(self.user_id, False):
                    try:
                        self.active_effects[self.user_id] = [e for e in self.active_effects.get(self.user_id, []) if e.get('type') != 'confusion']
                    except Exception:
                        logging.exception("Unexpected error")
                    self.confused_next_turn[self.user_id] = False
            elif self.confused_next_turn.get(self.user_id, False):
                if random.random() < 0.77:
                    self_damage = random.randint(15, 20) if max_dmg_threshold <= 100 else random.randint(40, 60)
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.user_id,
                        self_damage,
                        source="Verwirrung",
                        self_damage=True,
                    )
                    actual_damage, is_critical = 0, False
                    hits_enemy = False
                else:
                    actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                        attack,
                        damage,
                        dmg_buff,
                        attack_multiplier,
                        force_max_damage,
                        guaranteed_hit,
                    )
                    self._append_multi_hit_roll_event(effect_events)
                    if defender_has_stealth and not guaranteed_hit:
                        actual_damage = 0
                        is_critical = False
                        hits_enemy = False
                        self.consume_stealth(0)
                    elif defender_has_stealth:
                        self.consume_stealth(0)
                # consume confusion + clear UI icon
                try:
                    self.active_effects[self.user_id] = [e for e in self.active_effects.get(self.user_id, []) if e.get('type') != 'confusion']
                except Exception:
                    logging.exception("Unexpected error")
                self.confused_next_turn[self.user_id] = False
            else:
                actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                    attack,
                    damage,
                    dmg_buff,
                    attack_multiplier,
                    force_max_damage,
                    guaranteed_hit,
                )
                self._append_multi_hit_roll_event(effect_events)
                if defender_has_stealth and not guaranteed_hit:
                    actual_damage = 0
                    is_critical = False
                    hits_enemy = False
                    self.consume_stealth(0)
                elif defender_has_stealth:
                    self.consume_stealth(0)

            if hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                defender_hp_before = self._hp_for(0)
                reduced_damage, overflow_self_damage, outgoing_modifier = self._apply_outgoing_attack_modifiers_with_details(
                    self.user_id,
                    actual_damage,
                )
                if reduced_damage != actual_damage:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._append_effect_event(
                        effect_events,
                        _damage_transition_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                            context="Ausgehende Reduktion",
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.user_id,
                        overflow_self_damage,
                        source="Überlauf-Rückstoß",
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False

                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(0, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    0,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(0)),
                    incoming_min_damage=min_damage,
                )
                absorbed_after = int(self.absorbed_damage.get(0, 0) or 0)
                self._append_incoming_resolution_events(
                    effect_events,
                    defender_name=self.bot_card["name"],
                    raw_damage=incoming_raw_damage,
                    final_damage=int(final_damage),
                    reflected_damage=int(reflected_damage),
                    dodged=bool(dodged),
                    counter_damage=int(counter_damage),
                    modifier_details=incoming_modifier,
                    absorbed_before=absorbed_before,
                    absorbed_after=absorbed_after,
                )
                if dodged:
                    actual_damage = 0
                    hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    if actual_damage > 0:
                        self._apply_non_heal_damage(0, actual_damage)
                    else:
                        is_critical = False
                if reflected_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.user_id,
                        reflected_damage,
                        source="Reflexions-Rückschaden",
                        self_damage=False,
                    )
                if counter_damage > 0:
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.user_id,
                        counter_damage,
                        source="Konter-Rückschaden",
                        self_damage=False,
                    )
                self._guard_non_heal_damage_result(0, defender_hp_before, "mission_player_attack")
            if not hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.user_id,
                self_damage_value,
                source=f"{attack_name} / Rückstoß",
                self_damage=True,
            )

        heal_data = attack.get("heal")
        if heal_data is not None:
            heal_amount = _random_int_from_range(heal_data)
            healed_now = self.heal_player(self.user_id, heal_amount)
            if healed_now > 0:
                self._append_effect_event(effect_events, f"Heilung: +{healed_now} HP.")

        lifesteal_ratio = float(attack.get("lifesteal_ratio", 0.0) or 0.0)
        if lifesteal_ratio > 0 and hits_enemy and actual_damage > 0:
            lifesteal_heal = self.heal_player(self.user_id, int(round(actual_damage * lifesteal_ratio)))
            if lifesteal_heal > 0:
                self._append_effect_event(effect_events, f"Lebensraub: +{lifesteal_heal} HP.")

        self.player_hp = max(0, self.player_hp)
        self.bot_hp = max(0, self.bot_hp)

        self.round_counter += 1

        if not is_reload_action:
            self.activate_delayed_defense_after_attack(
                self.user_id,
                effect_events,
                attack_landed=bool(hits_enemy and int(actual_damage or 0) > 0),
            )

        # Apply new effects from player's attack
        confusion_applied = False
        effects = attack.get("effects", [])
        burning_duration_for_dynamic_cooldown: int | None = None
        for effect in effects:
            chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 1.0)
            if random.random() >= chance:
                continue
            target = effect.get("target", "enemy")
            target_id = self.user_id if target == "self" else 0
            eff_type = effect.get("type")
            if target != "self" and not hits_enemy and eff_type not in {"stun"}:
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif eff_type == 'burning':
                duration = _random_int_from_range(effect.get("duration"), default=1)
                burn_damage = _effect_int(effect, "damage")
                self.active_effects[target_id].append({
                    'type': 'burning',
                    'duration': duration,
                    'damage': burn_damage,
                    'applier': self.user_id
                })
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == 'confusion':
                self.set_confusion(target_id, self.user_id)
                confusion_applied = True
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = int(effect.get("amount", 0) or 0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_flat_bonus[target_id] = max(self.pending_flat_bonus.get(target_id, 0), amount)
                self.pending_flat_bonus_uses[target_id] = max(self.pending_flat_bonus_uses.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e)."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff verursacht Maximalschaden."))
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Nächster Angriff trifft garantiert."))
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n))."),
                )
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1, source=attack_name)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Block-Sequenz vorbereitet: {seq_text}."))
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n))."),
                )
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden."),
                )
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1, source=attack_name)
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert und {reflect_pct}% des verhinderten Schadens werden zurückgeworfen.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Absorption aktiv: Verhinderter Schaden wird gespeichert."))
            elif eff_type == "cap_damage":
                cap_setting = effect.get("max_damage", 0)
                if str(cap_setting).strip().lower() == "attack_min":
                    self.queue_incoming_modifier(target_id, cap="attack_min", turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, "Schadenslimit aktiv: Nächster Treffer wird auf dessen Mindestschaden begrenzt."),
                    )
                else:
                    max_damage = int(cap_setting or 0)
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                self.special_lock_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Spezialfähigkeiten des Gegners sind nächste Runde gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = int(effect.get("heal", 0) or 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": self.user_id})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: +{heal} HP für {turns} Runde(n).")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                heal_amount = int(effect.get("heal", 0) or 0)
                if random.random() < 0.5:
                    healed_mix = self.heal_player(target_id, heal_amount)
                    if healed_mix > 0:
                        self._append_effect_event(effect_events, f"Awesome Mix: +{healed_mix} HP.")
                else:
                    self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), 1)
                    self._append_effect_event(effect_events, "Awesome Mix: Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = int(effect.get("counter", 0) or 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )

        # Update Kampf-Log (inkl. vorab angewandter Verbrennung im selben Eintrag)
        if self.battle_log_message:
            log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
            log_embed = update_battle_log(
                log_embed,
                self.player_card["name"],
                self.bot_card["name"],
                attack_name,
                actual_damage,
                is_critical,
                interaction.user,
                "Bot",
                self.round_counter,
                self.bot_hp,
                attacker_remaining_hp=self.player_hp,
                pre_effect_damage=pre_burn_total,
                confusion_applied=confusion_applied,
                self_hit_damage=(self_damage if not hits_enemy and 'self_damage' in locals() else 0),
                attacker_status_icons=self._status_icons(self.user_id),
                defender_status_icons=self._status_icons(0),
                effect_events=effect_events,
            )
            await self._safe_edit_battle_log(log_embed)
        if self.airborne_pending_landing.get(0):
            self._consume_airborne_evade_marker(0)

        if (not is_forced_landing) and (not is_reload_action) and attack.get("requires_reload"):
            self.set_reload_needed(self.user_id, attack_index, True)

        if self.special_lock_next_turn.get(self.user_id, False):
            self.special_lock_next_turn[self.user_id] = False
        
        # Starte Cooldown (kartenspezifisch oder für starke Attacken) für den nächsten Zug.
        # In Missionen soll die stärkste Attacke im nächsten eigenen Zug gesperrt sein.
        # Darum KEINE sofortige Reduktion hier – die Reduktion passiert nach dem Bot-Zug.
        if not is_forced_landing:
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = attack.get("cooldown_turns")
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                current_cd = self.user_attack_cooldowns.get(attack_index, 0)
                self.user_attack_cooldowns[attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and isinstance(custom_cooldown_turns, int) and custom_cooldown_turns > 0:
                current_cd = self.user_attack_cooldowns.get(attack_index, 0)
                self.user_attack_cooldowns[attack_index] = max(current_cd, custom_cooldown_turns)
            elif self.mission_is_strong_attack(damage, dmg_buff):
                self.start_attack_cooldown_user(attack_index, 2)
        else:
            landing_cd_index = forced_landing_attack.get("cooldown_attack_index")
            landing_cd_turns = int(forced_landing_attack.get("cooldown_turns", 0) or 0)
            if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
                current_cd = self.user_attack_cooldowns.get(landing_cd_index, 0)
                self.user_attack_cooldowns[landing_cd_index] = max(current_cd, landing_cd_turns)
        
        # Prüfen ob Kampf vorbei nach Spieler-Angriff
        if self.bot_hp <= 0:
            self.result = True
            self.stop()
            await self._complete_wave(
                interaction,
                message,
                won=True,
                detail_text=f"🏆 **Welle {self.wave_num} gewonnen!** Du hast **{self.bot_card['name']}** besiegt!",
            )
            return
        if self.player_hp <= 0:
            self.result = False
            self.stop()
            await self._complete_wave(
                interaction,
                message,
                won=False,
                detail_text=f"❌ **Welle {self.wave_num} verloren!** Du hast dich selbst besiegt.",
            )
            return
        
        # Bot-Zug nach kurzer Pause
        if message is not None:
            try:
                await message.edit(
                    embed=self.create_current_embed(
                        description=f"🎯 Du hast **{attack_name}** verwendet! **{self.bot_card['name']}** ist an der Reihe...",
                    ),
                    view=None,
                )
            except Exception:
                logging.exception("Failed to update mission battle before bot turn")

        # SIDE EFFECTS: Apply effects on player before bot attack
        defender_id = self.user_id
        effects_to_remove = []
        pre_burn_total_player = 0
        for effect in self.active_effects[defender_id]:
            if effect.get('applier') == 0 and effect.get('type') == 'burning':
                damage = _effect_int(effect, 'damage')
                self.player_hp -= damage
                self.player_hp = max(0, self.player_hp)
                pre_burn_total_player += damage

                # Decrease duration
                remaining_duration = _effect_int(effect, 'duration') - 1
                effect['duration'] = remaining_duration
                if remaining_duration <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        self.apply_regen_tick(0)

        if self.stunned_next_turn.get(0, False):
            self.stunned_next_turn[0] = False
            if self.airborne_pending_landing.get(self.user_id):
                self._consume_airborne_evade_marker(self.user_id)
            self.reduce_cooldowns_user()
            self.update_attack_buttons_mission()
            embed = discord.Embed(
                title=f"⚔️ Welle {self.wave_num}/{self.total_waves}",
                description="🛑 Bot war betäubt und setzt den Zug aus! Du bist wieder an der Reihe!",
            )
            player_label = f"🟥 Deine Karte{self._status_icons(self.user_id)}"
            bot_label = f"🟦 Bot Karte{self._status_icons(0)}"
            embed.add_field(name=player_label, value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
            embed.add_field(name=bot_label, value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
            embed.set_image(url=self.player_card["bild"])
            embed.set_thumbnail(url=self.bot_card["bild"])
            _add_attack_info_field(embed, self.player_card)
            if message is not None:
                await interaction.followup.edit_message(message.id, embed=embed, view=self)
            else:
                await interaction.followup.send(embed=embed, view=self, ephemeral=True)
            if message is not None:
                await self.persist_session(interaction.channel, status="active", battle_message=message)
            return

        # Bot-Angriff
        bot_attacks = self.bot_card.get("attacks", [{"name": "Punch", "damage": 20}])
        bot_effect_events: list[str] = []
        forced_bot_landing_attack = self.resolve_forced_landing_if_due(0, bot_effect_events)
        is_forced_bot_landing = forced_bot_landing_attack is not None
        # Wähle stärkste verfügbare Bot-Attacke (unter Berücksichtigung von Cooldown)
        available_attacks = []
        attack_damages = []
        for i, atk in enumerate(bot_attacks[:4]):
            if self.special_lock_next_turn.get(0, False) and i != 0:
                continue
            if not self.is_attack_on_cooldown_bot(i):
                if atk.get("requires_reload") and self.is_reload_needed(0, i):
                    max_dmg = 0
                else:
                    damage = atk["damage"]
                    max_dmg = self.mission_get_attack_max_damage(damage) if isinstance(atk, dict) else 0
                available_attacks.append(i)
                attack_damages.append(max_dmg)
        
        if available_attacks or is_forced_bot_landing:
            if is_forced_bot_landing:
                best_index = -1
                attack = forced_bot_landing_attack
                damage = attack["damage"]
            else:
                # Wähle die mit max Damage
                best_index = available_attacks[attack_damages.index(max(attack_damages))]
                attack = bot_attacks[best_index]
                damage = attack["damage"]
            dmg_buff_bot = 0
            attacker_hp = self._hp_for(0)
            attacker_max_hp = self._max_hp_for(0)
            defender_hp = self._hp_for(self.user_id)
            defender_max_hp = self._max_hp_for(self.user_id)
            conditional_self_pct = attack.get("bonus_if_self_hp_below_pct")
            conditional_self_bonus = int(attack.get("bonus_damage_if_condition", 0) or 0)
            if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * float(conditional_self_pct)):
                dmg_buff_bot += conditional_self_bonus
            conditional_enemy_triggered = False
            conditional_enemy_pct = attack.get("conditional_enemy_hp_below_pct")
            if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * float(conditional_enemy_pct)):
                conditional_enemy_triggered = True
                damage_if_condition = attack.get("damage_if_condition")
                damage = _coerce_damage_input(damage_if_condition, default=0)
            if attack.get("add_absorbed_damage"):
                absorbed_bonus = int(self.absorbed_damage.get(0, 0) or 0)
                dmg_buff_bot += absorbed_bonus
                self.absorbed_damage[0] = 0
                base_min, base_max = _range_pair(damage)
                base_text = str(base_min) if base_min == base_max else f"{base_min}-{base_max}"
                self._append_effect_event(
                    bot_effect_events,
                    f"Kinetische Entladung: Grundschaden {base_text}, durch Absorption +{absorbed_bonus}.",
                )
            is_damaging_attack = self.mission_get_attack_max_damage(damage, 0) > 0
            attack_multiplier = 1.0
            applied_flat_bonus_now = 0
            force_max_damage = False
            if is_damaging_attack:
                if self.pending_flat_bonus_uses.get(0, 0) > 0:
                    flat_bonus_now = int(self.pending_flat_bonus.get(0, 0))
                    dmg_buff_bot += flat_bonus_now
                    applied_flat_bonus_now = max(0, flat_bonus_now)
                    self.pending_flat_bonus_uses[0] -= 1
                    if self.pending_flat_bonus_uses[0] <= 0:
                        self.pending_flat_bonus[0] = 0
                    if flat_bonus_now > 0:
                        self._append_effect_event(bot_effect_events, f"Verstärkung aktiv: +{flat_bonus_now} Schaden auf diesen Angriff.")
                if self.pending_multiplier_uses.get(0, 0) > 0:
                    attack_multiplier = float(self.pending_multiplier.get(0, 1.0) or 1.0)
                    self.pending_multiplier_uses[0] -= 1
                    if self.pending_multiplier_uses[0] <= 0:
                        self.pending_multiplier[0] = 1.0
                    multiplier_pct = int(round((attack_multiplier - 1.0) * 100))
                    if multiplier_pct > 0:
                        self._append_effect_event(bot_effect_events, f"Verstärkung aktiv: +{multiplier_pct}% Schaden auf diesen Angriff.")
                if self.force_max_next.get(0, 0) > 0:
                    force_max_damage = True
                    self.force_max_next[0] -= 1
            guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)
            is_bot_reload_action = bool((not is_forced_bot_landing) and attack.get("requires_reload") and self.is_reload_needed(0, best_index))
            bot_attack_name = str(attack.get("reload_name") or "Nachladen") if is_bot_reload_action else attack["name"]
            # Bot kann ebenfalls verwirrt sein: 77% Selbstschaden, 23% normaler Treffer
            bot_hits_enemy = True
            if is_bot_reload_action:
                actual_damage, is_critical = 0, False
                bot_hits_enemy = False
                self.set_reload_needed(0, best_index, False)
            else:
                min_damage = 0
                max_damage = 0
                self_damage = 0
                defender_has_stealth = self.has_stealth(self.user_id)
                guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(0)
                if guaranteed_hit:
                    self.blind_next_attack[0] = 0.0
                    self.consume_confusion_if_any(0)
                    self._append_effect_event(bot_effect_events, "Dieser Angriff trifft garantiert.")
                blind_chance = float(self.blind_next_attack.get(0, 0.0) or 0.0)
                blind_miss = False
                if blind_chance > 0:
                    self.blind_next_attack[0] = 0.0
                    blind_miss = random.random() < blind_chance
                if blind_miss:
                    actual_damage, is_critical = 0, False
                    bot_hits_enemy = False
                    self.confused_next_turn[0] = False
                elif hasattr(self, 'confused_next_turn') and self.confused_next_turn.get(0, False):
                    if random.random() < 0.77:
                        self_damage = random.randint(15, 20) if self.mission_get_attack_max_damage(damage, dmg_buff_bot) <= 100 else random.randint(40, 60)
                        self._apply_non_heal_damage_with_event(
                            bot_effect_events,
                            0,
                            self_damage,
                            source="Verwirrung",
                            self_damage=True,
                        )
                        actual_damage, is_critical = 0, False
                        bot_hits_enemy = False
                    else:
                        actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                            attack,
                            damage,
                            dmg_buff_bot,
                            attack_multiplier,
                            force_max_damage,
                            guaranteed_hit,
                        )
                        self._append_multi_hit_roll_event(bot_effect_events)
                        if defender_has_stealth and not guaranteed_hit:
                            actual_damage = 0
                            is_critical = False
                            bot_hits_enemy = False
                            self.consume_stealth(self.user_id)
                        elif defender_has_stealth:
                            self.consume_stealth(self.user_id)
                    # Confusion verbraucht
                    self.confused_next_turn[0] = False
                else:
                    actual_damage, is_critical, min_damage, max_damage = self.roll_attack_damage(
                        attack,
                        damage,
                        dmg_buff_bot,
                        attack_multiplier,
                        force_max_damage,
                        guaranteed_hit,
                    )
                    self._append_multi_hit_roll_event(bot_effect_events)
                    if defender_has_stealth and not guaranteed_hit:
                        actual_damage = 0
                        is_critical = False
                        bot_hits_enemy = False
                        self.consume_stealth(self.user_id)
                    elif defender_has_stealth:
                        self.consume_stealth(self.user_id)
    
                    if bot_hits_enemy and actual_damage > 0:
                        boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                        if boost_text:
                            self._append_effect_event(bot_effect_events, boost_text)
                        defender_hp_before = self._hp_for(self.user_id)
                        reduced_damage, overflow_self_damage, outgoing_modifier = self._apply_outgoing_attack_modifiers_with_details(
                            0,
                            actual_damage,
                        )
                        if reduced_damage != actual_damage:
                            modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                            self._append_effect_event(
                                bot_effect_events,
                                _damage_transition_text(
                                    int(actual_damage),
                                    int(reduced_damage),
                                    source=modifier_source or None,
                                    context="Ausgehende Reduktion",
                                ),
                            )
                            actual_damage = reduced_damage
                        if overflow_self_damage > 0:
                            self._apply_non_heal_damage_with_event(
                                bot_effect_events,
                                0,
                                overflow_self_damage,
                                source="Überlauf-Rückstoß",
                                self_damage=True,
                            )
                        if actual_damage <= 0:
                            is_critical = False
    
                        incoming_raw_damage = int(actual_damage)
                        absorbed_before = int(self.absorbed_damage.get(self.user_id, 0) or 0)
                        final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                            self.user_id,
                            actual_damage,
                            ignore_evade=(guaranteed_hit and not self.has_airborne(self.user_id)),
                            incoming_min_damage=min_damage,
                        )
                        absorbed_after = int(self.absorbed_damage.get(self.user_id, 0) or 0)
                        self._append_incoming_resolution_events(
                            bot_effect_events,
                            defender_name=self.player_card["name"],
                            raw_damage=incoming_raw_damage,
                            final_damage=int(final_damage),
                            reflected_damage=int(reflected_damage),
                            dodged=bool(dodged),
                            counter_damage=int(counter_damage),
                            modifier_details=incoming_modifier,
                            absorbed_before=absorbed_before,
                            absorbed_after=absorbed_after,
                        )
                        if dodged:
                            actual_damage = 0
                            bot_hits_enemy = False
                            is_critical = False
                        else:
                            actual_damage = max(0, int(final_damage))
                            if actual_damage > 0:
                                self._apply_non_heal_damage(self.user_id, actual_damage)
                            else:
                                is_critical = False
                        if reflected_damage > 0:
                            self._apply_non_heal_damage_with_event(
                                bot_effect_events,
                                0,
                                reflected_damage,
                                source="Reflexions-Rückschaden",
                                self_damage=False,
                            )
                        if counter_damage > 0:
                            self._apply_non_heal_damage_with_event(
                                bot_effect_events,
                                0,
                                counter_damage,
                                source="Konter-Rückschaden",
                                self_damage=False,
                            )
                        self._guard_non_heal_damage_result(self.user_id, defender_hp_before, "mission_bot_attack")
                    if not bot_hits_enemy or int(actual_damage or 0) <= 0:
                        is_critical = False
    
                self_damage_value = int(attack.get("self_damage", 0) or 0)
                if self_damage_value > 0:
                    self._apply_non_heal_damage_with_event(
                        bot_effect_events,
                        0,
                        self_damage_value,
                        source=f"{bot_attack_name} / Rückstoß",
                        self_damage=True,
                    )
    
                heal_data = attack.get("heal")
                if heal_data is not None:
                    heal_amount = _random_int_from_range(heal_data)
                    healed_now = self.heal_player(0, heal_amount)
                    if healed_now > 0:
                        self._append_effect_event(bot_effect_events, f"Heilung: +{healed_now} HP.")
    
                lifesteal_ratio = float(attack.get("lifesteal_ratio", 0.0) or 0.0)
                if lifesteal_ratio > 0 and bot_hits_enemy and actual_damage > 0:
                    lifesteal_heal = self.heal_player(0, int(round(actual_damage * lifesteal_ratio)))
                    if lifesteal_heal > 0:
                        self._append_effect_event(bot_effect_events, f"Lebensraub: +{lifesteal_heal} HP.")
    
                self.player_hp = max(0, self.player_hp)
                self.bot_hp = max(0, self.bot_hp)
                
                self.round_counter += 1
    
                if not is_bot_reload_action:
                    self.activate_delayed_defense_after_attack(
                        0,
                        bot_effect_events,
                        attack_landed=bool(bot_hits_enemy and int(actual_damage or 0) > 0),
                    )
    
                # SIDE EFFECTS: Apply new effects from bot attack
                effects = attack.get("effects", [])
                bot_burning_duration_for_dynamic_cooldown: int | None = None
                for effect in effects:
                    # 70% Fix-Chance für Verwirrung
                    chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 1.0)
                    if random.random() >= chance:
                        continue
                    target = effect.get("target", "enemy")
                    target_id = 0 if target == "self" else self.user_id
                    eff_type = effect.get("type")
                    if target != "self" and not bot_hits_enemy and eff_type not in {"stun"}:
                        continue
                    if eff_type == "stealth":
                        self.grant_stealth(target_id)
                        self._append_effect_event(bot_effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
                    elif eff_type == 'burning':
                        duration = _random_int_from_range(effect.get("duration"), default=1)
                        burn_damage = _effect_int(effect, "damage")
                        new_effect: dict[str, object] = {
                            'type': 'burning',
                            'duration': duration,
                            'damage': burn_damage,
                            'applier': 0
                        }
                        self.active_effects[target_id].append(new_effect)
                        if attack.get("cooldown_from_burning_plus") is not None:
                            prev_duration = bot_burning_duration_for_dynamic_cooldown or 0
                            bot_burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                        self._append_effect_event(bot_effect_events, f"Verbrennung aktiv: {burn_damage} Schaden für {duration} Runden.")
                    elif eff_type == 'confusion':
                        self.set_confusion(target_id, 0)
                        self._append_effect_event(bot_effect_events, "Verwirrung wurde angewendet.")
                    elif eff_type == "stun":
                        self.stunned_next_turn[target_id] = True
                        self._append_effect_event(bot_effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
                    elif eff_type == "damage_boost":
                        amount = int(effect.get("amount", 0) or 0)
                        uses = int(effect.get("uses", 1) or 1)
                        self.pending_flat_bonus[target_id] = max(self.pending_flat_bonus.get(target_id, 0), amount)
                        self.pending_flat_bonus_uses[target_id] = max(self.pending_flat_bonus_uses.get(target_id, 0), uses)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e)."))
                    elif eff_type == "damage_multiplier":
                        mult = float(effect.get("multiplier", 1.0) or 1.0)
                        uses = int(effect.get("uses", 1) or 1)
                        self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                        self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                        pct = int(round((mult - 1.0) * 100))
                        if pct > 0:
                            self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
                    elif eff_type == "force_max":
                        uses = int(effect.get("uses", 1) or 1)
                        self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Nächster Angriff verursacht Maximalschaden."))
                    elif eff_type == "guaranteed_hit":
                        uses = int(effect.get("uses", 1) or 1)
                        self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Nächster Angriff trifft garantiert."))
                    elif eff_type == "damage_reduction":
                        percent = float(effect.get("percent", 0.0) or 0.0)
                        turns = int(effect.get("turns", 1) or 1)
                        self.queue_incoming_modifier(target_id, percent=percent, turns=turns, source=bot_attack_name)
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(bot_attack_name, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n))."),
                        )
                    elif eff_type == "damage_reduction_sequence":
                        sequence = effect.get("sequence", [])
                        if isinstance(sequence, list):
                            for pct in sequence:
                                self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1, source=bot_attack_name)
                            if sequence:
                                seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                                self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, f"Block-Sequenz vorbereitet: {seq_text}."))
                    elif eff_type == "damage_reduction_flat":
                        amount = int(effect.get("amount", 0) or 0)
                        turns = int(effect.get("turns", 1) or 1)
                        self.queue_incoming_modifier(target_id, flat=amount, turns=turns, source=bot_attack_name)
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(bot_attack_name, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n))."),
                        )
                    elif eff_type == "enemy_next_attack_reduction_percent":
                        percent = float(effect.get("percent", 0.0) or 0.0)
                        turns = int(effect.get("turns", 1) or 1)
                        self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns, source=bot_attack_name)
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(bot_attack_name, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden."),
                        )
                    elif eff_type == "enemy_next_attack_reduction_flat":
                        amount = int(effect.get("amount", 0) or 0)
                        turns = int(effect.get("turns", 1) or 1)
                        self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=bot_attack_name)
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(bot_attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                        )
                    elif eff_type == "reflect":
                        reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                        reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                        self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1, source=bot_attack_name)
                        reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                        reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(
                                bot_attack_name,
                                f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert und {reflect_pct}% des verhinderten Schadens werden zurückgeworfen.",
                            ),
                        )
                    elif eff_type == "absorb_store":
                        percent = float(effect.get("percent", 0.0) or 0.0)
                        self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1, source=bot_attack_name)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Absorption aktiv: Verhinderter Schaden wird gespeichert."))
                    elif eff_type == "cap_damage":
                        cap_setting = effect.get("max_damage", 0)
                        if str(cap_setting).strip().lower() == "attack_min":
                            self.queue_incoming_modifier(target_id, cap="attack_min", turns=1, source=bot_attack_name)
                            self._append_effect_event(
                                bot_effect_events,
                                _effect_source_text(bot_attack_name, "Schadenslimit aktiv: Nächster Treffer wird auf dessen Mindestschaden begrenzt."),
                            )
                        else:
                            max_damage = int(cap_setting or 0)
                            self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=bot_attack_name)
                            self._append_effect_event(
                                bot_effect_events,
                                _effect_source_text(bot_attack_name, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer."),
                            )
                    elif eff_type == "evade":
                        counter = int(effect.get("counter", 0) or 0)
                        self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=bot_attack_name)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
                    elif eff_type == "special_lock":
                        self.special_lock_next_turn[target_id] = True
                        self._append_effect_event(bot_effect_events, "Spezialfähigkeiten des Gegners sind nächste Runde gesperrt.")
                    elif eff_type == "blind":
                        miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                        self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                        self._append_effect_event(bot_effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
                    elif eff_type == "regen":
                        turns = int(effect.get("turns", 1) or 1)
                        heal = int(effect.get("heal", 0) or 0)
                        self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": 0})
                        self._append_effect_event(bot_effect_events, f"Regeneration aktiviert: +{heal} HP für {turns} Runde(n).")
                    elif eff_type == "heal":
                        heal_data_effect = effect.get("amount", 0)
                        heal_amount = _random_int_from_range(heal_data_effect)
                        healed_effect = self.heal_player(target_id, heal_amount)
                        if healed_effect > 0:
                            self._append_effect_event(bot_effect_events, f"Heileffekt: +{healed_effect} HP.")
                    elif eff_type == "mix_heal_or_max":
                        heal_amount = int(effect.get("heal", 0) or 0)
                        if random.random() < 0.5:
                            healed_mix = self.heal_player(target_id, heal_amount)
                            if healed_mix > 0:
                                self._append_effect_event(bot_effect_events, f"Awesome Mix: +{healed_mix} HP.")
                        else:
                            self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), 1)
                            self._append_effect_event(bot_effect_events, "Awesome Mix: Nächster Angriff verursacht Maximalschaden.")
                    elif eff_type == "delayed_defense_after_next_attack":
                        defense_mode = str(effect.get("defense", "")).strip().lower()
                        counter = int(effect.get("counter", 0) or 0)
                        self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=bot_attack_name)
                        self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
                    elif eff_type == "airborne_two_phase":
                        self.start_airborne_two_phase(
                            target_id,
                            effect.get("landing_damage", [20, 40]),
                            bot_effect_events,
                            source_attack_index=best_index if not is_forced_bot_landing else None,
                            cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                        )
                # Kein separater Log – Effekte werden inline in der Angriffszeile angezeigt
    
                if self.battle_log_message:
                    log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
                    log_embed = update_battle_log(
                        log_embed,
                        self.bot_card["name"],
                        self.player_card["name"],
                        bot_attack_name,
                        actual_damage,
                        is_critical,
                        "Bot",
                        interaction.user,
                        self.round_counter,
                        self.player_hp,
                        attacker_remaining_hp=self.bot_hp,
                        pre_effect_damage=pre_burn_total_player,
                        attacker_status_icons=self._status_icons(0),
                        defender_status_icons=self._status_icons(self.user_id),
                        effect_events=bot_effect_events,
                    )
                    await self._safe_edit_battle_log(log_embed)
                if self.airborne_pending_landing.get(self.user_id):
                    self._consume_airborne_evade_marker(self.user_id)
    
                if (not is_forced_bot_landing) and (not is_bot_reload_action) and attack.get("requires_reload"):
                    self.set_reload_needed(0, best_index, True)
    
                if self.special_lock_next_turn.get(0, False):
                    self.special_lock_next_turn[0] = False
    
                if self.player_hp <= 0:
                    self.result = False
                    self.stop()
                    await self._complete_wave(
                        interaction,
                        message,
                        won=False,
                        detail_text=f"❌ **Welle {self.wave_num} verloren!** **{self.bot_card['name']}** hat dich besiegt!",
                    )
                    return
    
                if not is_forced_bot_landing:
                    # Cooldown für Bot (kartenspezifisch oder stark)
                    dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                        attack,
                        bot_burning_duration_for_dynamic_cooldown,
                    )
                    custom_cooldown_turns = attack.get("cooldown_turns")
                    starts_after_landing = _starts_cooldown_after_landing(attack)
                    if dynamic_cooldown_turns > 0:
                        current_cd = self.bot_attack_cooldowns.get(best_index, 0)
                        self.bot_attack_cooldowns[best_index] = max(current_cd, dynamic_cooldown_turns)
                        bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                        self._append_effect_event(
                            bot_effect_events,
                            f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {bot_burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                        )
                        self.reduce_cooldowns_bot()
                    elif (not starts_after_landing) and isinstance(custom_cooldown_turns, int) and custom_cooldown_turns > 0:
                        current_cd = self.bot_attack_cooldowns.get(best_index, 0)
                        self.bot_attack_cooldowns[best_index] = max(current_cd, custom_cooldown_turns)
                        self.reduce_cooldowns_bot()
                    elif self.mission_is_strong_attack(damage, dmg_buff_bot):
                        self.start_attack_cooldown_bot(best_index, 2)
                        # Reduziere Cooldowns für den Bot direkt nach seinem Zug (entspricht /kampf)
                        self.reduce_cooldowns_bot()
                else:
                    landing_cd_index = forced_bot_landing_attack.get("cooldown_attack_index")
                    landing_cd_turns = int(forced_bot_landing_attack.get("cooldown_turns", 0) or 0)
                    if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
                        current_cd = self.bot_attack_cooldowns.get(landing_cd_index, 0)
                        self.bot_attack_cooldowns[landing_cd_index] = max(current_cd, landing_cd_turns)
                        # Reduziere Cooldowns für den Bot direkt nach seinem Zug (entspricht /kampf)
                        self.reduce_cooldowns_bot()
    
                # Reduce Cooldowns for User nach Bot-Zug
                self.reduce_cooldowns_user()
    
                # Update UI für nächsten Spieler-Zug
                embed = discord.Embed(title=f"⚔️ Welle {self.wave_num}/{self.total_waves}",
                                      description=f"Bot hat **{bot_attack_name}** verwendet! Dein HP: {self.player_hp}\nDu bist wieder an der Reihe!")
                player_label = f"🟥 Deine Karte{self._status_icons(self.user_id)}"
                bot_label = f"🟦 Bot Karte{self._status_icons(0)}"
                embed.add_field(name=player_label, value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
                embed.add_field(name=bot_label, value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
                embed.set_image(url=self.player_card["bild"])
                embed.set_thumbnail(url=self.bot_card["bild"])
                _add_attack_info_field(embed, self.player_card)
    
                # Update attack buttons für neuen Spieler-Zug
                self.update_attack_buttons_mission()
    
                if message is not None:
                    await interaction.followup.edit_message(message.id, embed=embed, view=self)
                    await self.persist_session(interaction.channel, status="active", battle_message=message)
                else:
                    await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            # Bot hat keine Attacken verfügbar (alle auf Cooldown) - überspringe Bot-Zug
            if self.airborne_pending_landing.get(self.user_id):
                self._consume_airborne_evade_marker(self.user_id)
            self.reduce_cooldowns_user()
            self.update_attack_buttons_mission()
            
            embed = discord.Embed(title=f"⚔️ Welle {self.wave_num}/{self.total_waves}", 
                                  description=f"🤖 Bot hat keine Attacken verfügbar! Du bist wieder an der Reihe!")
            player_label = f"🟥 Deine Karte{self._status_icons(self.user_id)}"
            bot_label = f"🟦 Bot Karte{self._status_icons(0)}"
            embed.add_field(name=player_label, value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
            embed.add_field(name=bot_label, value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
            embed.set_image(url=self.player_card["bild"])
            embed.set_thumbnail(url=self.bot_card["bild"])
            _add_attack_info_field(embed, self.player_card)
            
            if message is not None:
                await interaction.followup.edit_message(message.id, embed=embed, view=self)
                await self.persist_session(interaction.channel, status="active", battle_message=message)
            else:
                await interaction.followup.send(embed=embed, view=self, ephemeral=True)

# =========================
# Owner/Dev Panel
# =========================

async def _send_ephemeral(interaction: discord.Interaction, *, content: str | None = None, embed=None, view=None, file=None):
    if not await is_channel_allowed_ids(
        interaction.guild_id,
        interaction.channel_id,
        getattr(interaction.channel, "parent_id", None),
    ):
        return None
    kwargs: dict[str, object] = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    return await send_interaction_response(interaction, **kwargs)

def _get_bot_member(interaction: discord.Interaction) -> discord.Member | None:
    guild = interaction.guild
    client_user = interaction.client.user
    if guild is None or client_user is None:
        return None
    return guild.get_member(client_user.id) or guild.me

def _can_send_in_channel(channel: discord.abc.GuildChannel | discord.Thread, member: discord.Member | None) -> bool:
    if member is None:
        return True
    perms = channel.permissions_for(member)
    if not perms.view_channel:
        return False
    if isinstance(channel, discord.Thread):
        return perms.send_messages_in_threads
    return perms.send_messages

async def _send_channel_message(
    channel: object,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: ui.View | None = None,
) -> discord.Message | None:
    sendable_channel = _coerce_sendable_channel(channel)
    if sendable_channel is None:
        return None
    guild_obj = getattr(channel, "guild", None)
    guild_id = guild_obj.id if isinstance(guild_obj, discord.Guild) else None
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if not await is_channel_allowed_ids(guild_id, channel_id, parent_id):
        return None
    try:
        sent_message = await sendable_channel.send(content=content, embed=embed, view=view)
        await _maybe_register_durable_message(sent_message, view)
        return sent_message
    except discord.Forbidden:
        logging.warning("Missing send permissions in channel %s", channel_id)
        return None
    except discord.HTTPException:
        logging.exception("Failed to send message to channel %s", channel_id)
        return None


async def _safe_send_channel(
    interaction: discord.Interaction,
    channel: object,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: ui.View | None = None,
) -> discord.Message | None:
    if _coerce_sendable_channel(channel) is None:
        return None
    guild_obj = getattr(channel, "guild", None)
    guild_id = guild_obj.id if isinstance(guild_obj, discord.Guild) else None
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if not await is_channel_allowed_ids(guild_id, channel_id, parent_id):
        return None
    sent_message = await _send_channel_message(
        channel,
        content=content,
        embed=embed,
        view=view,
    )
    if sent_message is not None:
        return sent_message
    if not hasattr(interaction, "guild_id") or not hasattr(interaction, "channel_id"):
        return None
    try:
        await _send_ephemeral(
            interaction,
            content="? Mir fehlen Rechte in diesem Kanal/Thread (View/Send/Thread-Rechte). Bitte gib mir Zugriff.",
        )
    except Exception:
        try:
            await _send_ephemeral(
                interaction,
                content="? Nachricht konnte in diesem Kanal/Thread gerade nicht gesendet werden.",
            )
        except Exception:
            return None
    return None

async def _fetch_channel_safe(channel_id: int | None):
    if not channel_id:
        return None
    try:
        return await bot.fetch_channel(channel_id)
    except discord.NotFound:
        return None
    except discord.Forbidden:
        logging.warning("Missing access while fetching channel %s", channel_id)
        return None
    except discord.HTTPException:
        logging.exception("Failed to fetch channel %s", channel_id)
        return None


async def _fetch_message_safe(channel: object, message_id: int | None) -> discord.Message | None:
    if not message_id or channel is None or not hasattr(channel, "fetch_message"):
        return None
    try:
        fetch_message = getattr(channel, "fetch_message")
        return await fetch_message(int(message_id))
    except discord.NotFound:
        return None
    except discord.Forbidden:
        logging.warning("Missing access while fetching message %s", message_id)
        return None
    except discord.HTTPException:
        logging.exception("Failed to fetch message %s", message_id)
        return None


def _split_text_chunks(text: str, chunk_size: int = 3800) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    chunks: list[str] = []
    current = ""
    for line in raw.splitlines():
        line_to_add = f"{line}\n" if line else "\n"
        if len(current) + len(line_to_add) > chunk_size:
            if current.strip():
                chunks.append(current.rstrip())
            current = line_to_add
        else:
            current += line_to_add
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def _send_basti_log_dm(log_text: str, *, context_lines: list[str]) -> None:
    if not log_text.strip():
        return
    user = bot.get_user(BASTI_USER_ID)
    if user is None:
        try:
            user = await bot.fetch_user(BASTI_USER_ID)
        except discord.HTTPException:
            logging.exception("Failed to fetch Basti user for bug log DM")
            return
    try:
        intro = "\n".join(line for line in context_lines if line.strip())
        if intro:
            await user.send(intro[:1900])
        for idx, chunk in enumerate(_split_text_chunks(log_text), start=1):
            title = "Kampf-/Missionslog" if idx == 1 else f"Kampf-/Missionslog ({idx})"
            embed = discord.Embed(title=title, description=chunk, color=0x2F3136)
            await user.send(embed=embed)
    except discord.HTTPException:
        logging.exception("Failed to DM Basti with battle log")


async def _handle_durable_view_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    view: DurableView,
    view_label: str,
    battle_log_text: str,
) -> None:
    channel = interaction.channel
    guild_name = interaction.guild.name if interaction.guild else "DM"
    channel_text = _channel_mention_or_fallback(channel)
    user_text = getattr(interaction.user, "mention", None) or f"<@{interaction.user.id}>"
    await _send_basti_log_dm(
        battle_log_text,
        context_lines=[
            f"Fehler in View: {view_label}",
            f"Guild: {guild_name}",
            f"Kanal/Thread: {channel_text}",
            f"User: {safe_display_name(interaction.user, fallback=str(interaction.user.id))} ({interaction.user.id})",
            f"Exception: {error!r}",
        ],
    )
    try:
        await send_interaction_response(
            interaction,
            content="❌ Da ist vermutlich ein Bug passiert. Ich habe Basti informiert. Wenn du willst, nutze das Formular unten.",
            view=BugReportLinkView(),
            ephemeral=True,
        )
    except Exception:
        logging.exception("Failed to send durable-view error response")
    try:
        if channel is not None:
            sendable_channel = _coerce_sendable_channel(channel)
            if sendable_channel is not None:
                await sendable_channel.send(
                    content=f"{user_text} Es gab vermutlich einen Bug. Wenn du willst, nutze das Formular unten.",
                    view=BugReportLinkView(),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
    except Exception:
        logging.exception("Failed to send durable-view error channel fallback")


async def _maybe_register_durable_message(message: discord.Message | None, view: ui.View | None) -> None:
    if message is None or not isinstance(view, DurableView):
        return
    guild = getattr(message.channel, "guild", None)
    if not isinstance(guild, discord.Guild):
        return
    channel_id = getattr(message.channel, "id", None)
    if not isinstance(channel_id, int):
        return
    await upsert_durable_view(
        guild_id=guild.id,
        channel_id=channel_id,
        message_id=message.id,
        view_kind=view.durable_view_kind,
        payload=view.durable_payload(),
    )
    view.bind_durable_message(guild_id=guild.id, channel_id=channel_id, message_id=message.id)


async def _restore_fight_battle_view_from_session(session_id: int) -> DurableView | None:
    session = await get_active_session(session_id)
    if not session or session.get("status") != "active":
        return None
    payload = _dict_str_any(session.get("payload"))
    if not payload:
        return None
    view = BattleView(
        cast(CardData, _dict_str_any(payload.get("player1_card"))),
        cast(CardData, _dict_str_any(payload.get("player2_card"))),
        int(payload.get("player1_id", 0) or 0),
        int(payload.get("player2_id", 0) or 0),
        None,
        public_result_channel_id=int(payload.get("public_result_channel_id", 0) or 0) or None,
    )
    view.restore_from_session_payload(payload)
    view.session_id = session_id
    channel = bot.get_channel(int(session.get("channel_id", 0) or 0)) or await _fetch_channel_safe(int(session.get("channel_id", 0) or 0))
    log_message = await _fetch_message_safe(channel, session.get("log_message_id"))
    if log_message is not None:
        view.battle_log_message = log_message
    return view


async def _restore_mission_battle_view_from_session(session_id: int) -> DurableView | None:
    session = await get_active_session(session_id)
    if not session or session.get("status") != "active":
        return None
    payload = _dict_str_any(session.get("payload"))
    if not payload:
        return None
    view = MissionBattleView(
        cast(CardData, _dict_str_any(payload.get("player_card"))),
        cast(CardData, _dict_str_any(payload.get("bot_card"))),
        int(payload.get("user_id", 0) or 0),
        int(payload.get("wave_num", 1) or 1),
        int(payload.get("total_waves", 1) or 1),
        mission_data=_dict_str_any(payload.get("mission_data")),
        is_admin=bool(payload.get("is_admin", False)),
        selected_card_name=str(payload.get("selected_card_name") or ""),
    )
    view.restore_from_session_payload(payload)
    view.session_id = session_id
    channel = bot.get_channel(int(session.get("channel_id", 0) or 0)) or await _fetch_channel_safe(int(session.get("channel_id", 0) or 0))
    log_message = await _fetch_message_safe(channel, session.get("log_message_id"))
    if log_message is not None:
        view.battle_log_message = log_message
    return view


async def _restore_durable_view_instance(
    *,
    guild_id: int,
    channel: object,
    view_kind: str,
    payload: dict[str, Any],
) -> DurableView | None:
    guild = bot.get_guild(guild_id)
    if view_kind == VIEW_KIND_INTRO_PROMPT:
        return IntroEphemeralPromptView(int(payload.get("user_id", 0) or 0))
    if view_kind == VIEW_KIND_FIGHT_CHALLENGE:
        return ChallengeResponseView(
            int(payload.get("challenger_id", 0) or 0),
            int(payload.get("challenged_id", 0) or 0),
            str(payload.get("challenger_card_name") or ""),
            request_id=int(payload.get("request_id", 0) or 0),
            origin_channel_id=int(payload.get("origin_channel_id", 0) or 0) or None,
            thread_id=int(payload.get("thread_id", 0) or 0) or None,
            thread_created=bool(payload.get("thread_created", False)),
        )
    if view_kind == VIEW_KIND_FIGHT_CARD_SELECT:
        return FightCardSelectView(
            int(payload.get("challenger_id", 0) or 0),
            int(payload.get("challenged_id", 0) or 0),
            str(payload.get("challenger_card_name") or ""),
            payload.get("challenged_card_options", []),
            origin_channel_id=int(payload.get("origin_channel_id", 0) or 0) or None,
            thread_id=int(payload.get("thread_id", 0) or 0) or None,
            thread_created=bool(payload.get("thread_created", False)),
        )
    if view_kind == VIEW_KIND_BATTLE:
        return await _restore_fight_battle_view_from_session(int(payload.get("session_id", 0) or 0))
    if view_kind == VIEW_KIND_FIGHT_FEEDBACK:
        return FightFeedbackView(
            channel,
            guild,
            {int(value) for value in _list_any(payload.get("allowed_user_ids")) if str(value).strip()},
            battle_log_text=str(payload.get("battle_log_text") or ""),
            bug_reported_by={int(value) for value in _list_any(payload.get("bug_reported_by")) if str(value).strip()},
            log_sent_to={int(value) for value in _list_any(payload.get("log_sent_to")) if str(value).strip()},
            opted_out_by={int(value) for value in _list_any(payload.get("opted_out_by")) if str(value).strip()},
            auto_close_delay=int(payload.get("auto_close_delay", 0) or 0) or None,
            auto_close_started_at=int(payload.get("auto_close_started_at", 0) or 0) or None,
            close_on_idle=bool(payload.get("close_on_idle", True)),
            close_after_no_bug=bool(payload.get("close_after_no_bug", True)),
            keep_open_after_bug=bool(payload.get("keep_open_after_bug", True)),
        )
    if view_kind == VIEW_KIND_THREAD_CLOSE:
        if isinstance(channel, discord.Thread):
            return AdminCloseView(channel)
        return None
    if view_kind == VIEW_KIND_MISSION_ACCEPT:
        return MissionAcceptView(
            int(payload.get("user_id", 0) or 0),
            _dict_str_any(payload.get("mission_data")),
            request_id=int(payload.get("request_id", 0) or 0),
            visibility=str(payload.get("visibility") or VISIBILITY_PRIVATE),
            is_admin=bool(payload.get("is_admin", False)),
        )
    if view_kind == VIEW_KIND_MISSION_CARD_SELECT:
        return MissionStartCardSelectView(
            int(payload.get("user_id", 0) or 0),
            _dict_str_any(payload.get("mission_data")),
            is_admin=bool(payload.get("is_admin", False)),
            user_karten=[str(item) for item in _list_any(payload.get("user_karten"))],
        )
    if view_kind == VIEW_KIND_MISSION_PAUSE:
        return MissionPauseView(
            int(payload.get("user_id", 0) or 0),
            str(payload.get("current_card_name") or ""),
            _dict_str_any(payload.get("mission_state")),
        )
    if view_kind == VIEW_KIND_MISSION_NEW_CARD_SELECT:
        return MissionNewCardSelectView(
            int(payload.get("user_id", 0) or 0),
            [tuple(item) for item in _list_any(payload.get("user_karten")) if isinstance(item, list) and len(item) == 2],
            mission_state=_dict_str_any(payload.get("mission_state")),
        )
    if view_kind == VIEW_KIND_MISSION_BATTLE:
        return await _restore_mission_battle_view_from_session(int(payload.get("session_id", 0) or 0))
    return None


async def _restore_durable_views() -> None:
    rows = await list_durable_views()
    for row in rows:
        guild_id = int(row.get("guild_id", 0) or 0)
        channel_id = int(row.get("channel_id", 0) or 0)
        channel = bot.get_channel(channel_id) or await _fetch_channel_safe(channel_id)
        if channel is None:
            await delete_durable_view(guild_id=guild_id, channel_id=channel_id)
            continue
        message = await _fetch_message_safe(channel, int(row.get("message_id", 0) or 0))
        if message is None:
            await delete_durable_view(guild_id=guild_id, channel_id=channel_id)
            continue
        payload = _dict_str_any(row.get("payload"))
        view = await _restore_durable_view_instance(
            guild_id=guild_id,
            channel=channel,
            view_kind=str(row.get("view_kind") or ""),
            payload=payload,
        )
        if view is None:
            await delete_durable_view(guild_id=guild_id, channel_id=channel_id)
            continue
        view.bind_durable_message(guild_id=guild_id, channel_id=channel_id, message_id=message.id)
        bot.add_view(view, message_id=message.id)

async def resend_pending_requests() -> None:
    try:
        fight_rows = await get_pending_fight_requests()
    except Exception:
        logging.exception("Failed to load pending fight requests")
        fight_rows = []
    for row in fight_rows:
        try:
            guild_id = int(row["guild_id"]) if row["guild_id"] else 0
            guild = bot.get_guild(guild_id) if guild_id else None
            if guild is None:
                continue
            channel = None
            thread_id = row["thread_id"]
            if thread_id:
                channel = guild.get_channel(int(thread_id)) or await _fetch_channel_safe(int(thread_id))
            if channel is None and row["message_channel_id"]:
                channel = guild.get_channel(int(row["message_channel_id"])) or await _fetch_channel_safe(int(row["message_channel_id"]))
            if channel is None and row["origin_channel_id"]:
                channel = guild.get_channel(int(row["origin_channel_id"])) or await _fetch_channel_safe(int(row["origin_channel_id"]))
            if channel is None:
                continue
            if not await is_channel_allowed_ids(guild.id, getattr(channel, "id", None), getattr(channel, "parent_id", None)):
                continue
            sendable_channel = _coerce_sendable_channel(channel)
            if sendable_channel is None:
                continue
            view = ChallengeResponseView(
                int(row["challenger_id"]),
                int(row["challenged_id"]),
                row["challenger_card"],
                request_id=int(row["id"]),
                origin_channel_id=int(row["origin_channel_id"]) if row["origin_channel_id"] else None,
                thread_id=int(row["thread_id"]) if row["thread_id"] else None,
                thread_created=bool(row["thread_created"]),
            )
            existing_message = await _fetch_message_safe(channel, int(row["message_id"])) if row["message_id"] else None
            if existing_message is not None:
                await _maybe_register_durable_message(existing_message, view)
                bot.add_view(view, message_id=existing_message.id)
                continue
            msg = await sendable_channel.send(
                content=f"<@{row['challenged_id']}>, du wurdest zu einem 1v1 Kartenkampf herausgefordert!",
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            await update_fight_request_message(int(row["id"]), msg.id, msg.channel.id)
        except Exception:
            logging.exception("Failed to resend fight request")

    if ALPHA_PHASE_ENABLED:
        logging.info("Alpha phase active: skipping mission request resend.")
        return

    try:
        mission_rows = await get_pending_mission_requests()
    except Exception:
        logging.exception("Failed to load pending mission requests")
        mission_rows = []
    for row in mission_rows:
        try:
            guild_id = int(row["guild_id"]) if row["guild_id"] else 0
            guild = bot.get_guild(guild_id) if guild_id else None
            if guild is None:
                continue
            channel = None
            if row["channel_id"]:
                channel = guild.get_channel(int(row["channel_id"])) or await _fetch_channel_safe(int(row["channel_id"]))
            if channel is None:
                continue
            if not await is_channel_allowed_ids(guild.id, getattr(channel, "id", None), getattr(channel, "parent_id", None)):
                continue
            sendable_channel = _coerce_sendable_channel(channel)
            if sendable_channel is None:
                continue
            try:
                mission_data = json.loads(row["mission_data"]) if row["mission_data"] else {}
            except (json.JSONDecodeError, TypeError):
                mission_data = {}
            embed = _build_mission_embed(mission_data)
            view = MissionAcceptView(
                int(row["user_id"]),
                mission_data,
                request_id=int(row["id"]),
                visibility=row["visibility"] or VISIBILITY_PRIVATE,
                is_admin=bool(row["is_admin"]),
            )
            existing_message = await _fetch_message_safe(channel, int(row["message_id"])) if row["message_id"] else None
            if existing_message is not None:
                await _maybe_register_durable_message(existing_message, view)
                bot.add_view(view, message_id=existing_message.id)
                continue
            msg = await sendable_channel.send(embed=embed, view=view)
            await update_mission_request_message(int(row["id"]), msg.id, msg.channel.id)
        except Exception:
            logging.exception("Failed to resend mission request")

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"

def command_visibility_key(qualified_name: str) -> str:
    return f"cmd:{qualified_name.replace(' ', '.')}"

def command_visibility_key_for_interaction(interaction: discord.Interaction) -> str | None:
    if not interaction.command:
        return None
    name = getattr(interaction.command, "qualified_name", interaction.command.name)
    return command_visibility_key(name)

LEGACY_COMMAND_VISIBILITY_KEYS = {
    command_visibility_key("anfang"): "anfang",
}

PANEL_STATIC_VISIBILITY_ITEMS: list[tuple[str, str, str]] = [
    ("maintenance", "Wartungsmodus", "Bestätigungen für Wartungsmodus an/aus"),
    ("delete_user", "User löschen", "Lösch-Dialog und Ergebnis"),
    ("db_backup", "DB-Backup", "DB-Datei als Attachment"),
    ("give_dust", "Give Dust", "Bestätigung für Dust-Vergabe"),
    ("grant_card", "Grant Card", "Bestätigung für Karten-Vergabe"),
    ("revoke_card", "Revoke Card", "Bestätigung für Karten-Abzug"),
    ("set_daily", "Daily Reset", "Bestätigung für Daily-Reset"),
    ("set_mission", "Mission Reset", "Bestätigung für Mission-Reset"),
    ("health", "Health", "Health-Report"),
    ("debug_db", "Debug DB", "DB-Checks/Integrity"),
    ("debug_user", "Debug User", "User-Übersicht"),
    ("debug_sync", "Debug Sync", "Sync-Ergebnis"),
    ("logs_last", "Logs Last", "Letzte Log-Zeilen"),
    ("karten_validate", "Karten Validate", "Prüfung karten.py"),
    ("channel_config", "Kanal-Config", "Kanal erlauben/entfernen/listen"),
    ("reset_intro", "Intro Reset", "Intro-Reset Bestätigung"),
    ("vault_look", "Vault Look", "Vault-Ansicht"),
    ("bot_status", "Bot-Status", "Status-Menü"),
    ("test_report", "Command-Report", "Slash-Command Bericht"),
]
if ALPHA_PHASE_ENABLED:
    PANEL_STATIC_VISIBILITY_ITEMS = [item for item in PANEL_STATIC_VISIBILITY_ITEMS if item[0] != "set_mission"]

def _visibility_label(value: str) -> str:
    return "öffentlich" if value == VISIBILITY_PUBLIC else "nur sichtbar"

async def get_latest_anfang_message(guild_id: int | None):
    return await load_latest_anfang_message(guild_id)

async def set_latest_anfang_message(guild_id: int, channel_id: int, message_id: int, author_id: int) -> None:
    await store_latest_anfang_message(guild_id, channel_id, message_id, author_id)

def _flatten_app_commands(commands_list, prefix: str = "") -> list[tuple[str, app_commands.Command]]:
    flat: list[tuple[str, app_commands.Command]] = []
    for cmd in commands_list:
        if isinstance(cmd, app_commands.Group):
            new_prefix = f"{prefix}{cmd.name} "
            flat.extend(_flatten_app_commands(cmd.commands, new_prefix))
        else:
            flat.append((f"{prefix}{cmd.name}", cmd))
    return flat

def get_panel_visibility_items() -> list[tuple[str, str, str]]:
    all_cmds = bot.tree.get_commands()
    command_items: list[tuple[str, str, str]] = []
    for name, cmd in _flatten_app_commands(all_cmds):
        key = command_visibility_key(name)
        label = f"/{name}"[:100]
        desc = (cmd.description or "Slash-Command")[:100]
        command_items.append((key, label, desc))
    command_items.sort(key=lambda item: item[1].lower())
    return PANEL_STATIC_VISIBILITY_ITEMS + command_items

def _visibility_value_for_key(message_key: str, visibility_map: dict[str, str]) -> str:
    if message_key in visibility_map:
        return visibility_map[message_key]
    legacy_key = LEGACY_COMMAND_VISIBILITY_KEYS.get(message_key)
    if legacy_key and legacy_key in visibility_map:
        return visibility_map[legacy_key]
    return VISIBILITY_PRIVATE

async def get_visibility_override(guild_id: int | None, message_key: str) -> str | None:
    return await load_visibility_override(guild_id, message_key)

async def get_message_visibility(guild_id: int | None, message_key: str) -> str:
    return await resolve_message_visibility(
        guild_id,
        message_key,
        default_visibility=VISIBILITY_PRIVATE,
        legacy_visibility_keys=LEGACY_COMMAND_VISIBILITY_KEYS,
    )

async def get_command_visibility(interaction: discord.Interaction) -> str:
    key = command_visibility_key_for_interaction(interaction)
    if not key:
        return VISIBILITY_PRIVATE
    return await get_message_visibility(interaction.guild_id, key)

async def get_command_visibility_override(interaction: discord.Interaction) -> str | None:
    key = command_visibility_key_for_interaction(interaction)
    if not key:
        return None
    override = await get_visibility_override(interaction.guild_id, key)
    if override:
        return override
    legacy_key = LEGACY_COMMAND_VISIBILITY_KEYS.get(key)
    if legacy_key:
        return await get_visibility_override(interaction.guild_id, legacy_key)
    return None

async def get_visibility_map(guild_id: int | None) -> dict[str, str]:
    return await load_visibility_map(guild_id)

async def _send_panel_message(
    interaction: discord.Interaction,
    message_key: str,
    *,
    content: str | None = None,
    embed=None,
    view=None,
    file=None,
):
    if not await is_channel_allowed_ids(
        interaction.guild_id,
        interaction.channel_id,
        getattr(interaction.channel, "parent_id", None),
    ):
        return None
    visibility = await get_message_visibility(interaction.guild_id, message_key)
    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if visibility != VISIBILITY_PUBLIC:
        if message_key.startswith("cmd:"):
            if not await is_admin(interaction):
                kwargs["ephemeral"] = True
        else:
            kwargs["ephemeral"] = True
    return await send_interaction_response(interaction, **kwargs)

async def _send_with_visibility(
    interaction: discord.Interaction,
    visibility_key: str | None,
    *,
    content: str | None = None,
    embed=None,
    view=None,
    file=None,
):
    if visibility_key:
        return await _send_panel_message(interaction, visibility_key, content=content, embed=embed, view=view, file=file)
    return await _send_ephemeral(interaction, content=content, embed=embed, view=view, file=file)

async def _edit_panel_message(interaction: discord.Interaction, *, content: str | None = None, embed=None, view=None):
    await edit_interaction_message(interaction, content=content, embed=embed, view=view)

async def _select_user(interaction: discord.Interaction, prompt: str) -> tuple[int | None, str | None]:
    if interaction.guild is None:
        await _send_ephemeral(interaction, content=SERVER_ONLY)
        return None, None
    view = AdminUserSelectView(interaction.user.id, interaction.guild)
    await _send_ephemeral(interaction, content=prompt, view=view)
    await view.wait()
    if not view.value:
        return None, None
    user_id = int(view.value)
    member = _get_member_if_available(interaction.guild, user_id)
    if member:
        return user_id, safe_display_name(member, fallback=str(user_id))
    try:
        user = await interaction.client.fetch_user(user_id)
        return user_id, safe_display_name(user, fallback=str(user_id))
    except discord.NotFound:
        return user_id, str(user_id)
    except discord.HTTPException:
        logging.exception("Failed to fetch user %s for admin selector", user_id)
        return user_id, str(user_id)

async def _select_number(interaction: discord.Interaction, prompt: str, options: list[int]):
    view = NumberSelectView(interaction.user.id, options, prompt)
    await _send_ephemeral(interaction, content=prompt, view=view)
    await view.wait()
    return view.value

async def _select_card(interaction: discord.Interaction, prompt: str):
    view = CardSelectPagerView(interaction.user.id, karten)
    await _send_ephemeral(interaction, content=prompt, view=view)
    await view.wait()
    return view.value

class NumberSelectView(RestrictedView):
    def __init__(self, requester_id: int, options: list[int], placeholder: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.value = None
        select_options = [SelectOption(label=str(n), value=str(n)) for n in options][:25]
        self.select = ui.Select(placeholder=placeholder, min_values=1, max_values=1, options=select_options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = int(self.select.values[0])
        self.stop()
        await interaction.response.defer()


def _member_mention_or_fallback(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return member.mention if member is not None else f"<@{user_id}>"


async def _post_dust_result_message(
    interaction: discord.Interaction,
    *,
    mode: str,
    remove: bool,
    amount: int,
    results: list[tuple[int, int]],
) -> bool:
    action_title = "Infinitydust entfernt" if remove else "Infinitydust vergeben"
    actor_mention = getattr(interaction.user, "mention", None) or f"<@{interaction.user.id}>"
    lines = [
        f"{actor_mention} hat {'Infinitydust entfernt' if remove else 'Infinitydust vergeben'}.",
        f"Modus: **{escape_display_text(mode, fallback='single')}**",
        "",
    ]
    for user_id, applied_amount in results:
        target = _member_mention_or_fallback(interaction.guild, user_id)
        if remove:
            lines.append(f"• {target}: **{applied_amount}x** entfernt")
        else:
            lines.append(f"• {target}: **{applied_amount}x** erhalten")
    if remove:
        lines.append("")
        lines.append(f"Angeforderte Menge pro Nutzer: **{amount}x**")

    embed = discord.Embed(
        title=f"💎 {action_title}",
        description="\n".join(lines),
        color=0xD64B4B if remove else 0x2ECC71,
    )
    embed.set_footer(text=f"Ausgeführt von {safe_display_name(interaction.user, fallback=str(interaction.user.id))}")
    sent_message = await _send_channel_message(
        interaction.channel,
        embed=embed,
    )
    return sent_message is not None


async def run_dust_command_flow(
    interaction: discord.Interaction,
    *,
    mode: str,
    remove: bool,
) -> None:
    if interaction.guild is None:
        await interaction.followup.send(SERVER_ONLY, ephemeral=True)
        return

    mode_value = str(mode or "").strip().lower()
    if mode_value not in {"single", "multi"}:
        await interaction.followup.send("? Ung?ltiger Modus. Nutze `single` oder `multi`.", ephemeral=True)
        return

    action_phrase = "entfernen" if remove else "geben"
    target_user_ids: list[int] = []

    if mode_value == "single":
        user_select_view = AdminUserSelectView(interaction.user.id, interaction.guild)
        await interaction.followup.send(
            content=f"W?hle den Nutzer, dem du Infinitydust {action_phrase} m?chtest:",
            view=user_select_view,
            ephemeral=True,
        )
        await user_select_view.wait()
        if not user_select_view.value:
            await interaction.followup.send("? Keine Auswahl getroffen. Abgebrochen.", ephemeral=True)
            return
        target_user_ids = [int(user_select_view.value)]
    else:
        multi_view = DustMultiUserSelectView(interaction.user.id, interaction.guild)
        multi_message = await interaction.followup.send(
            content=multi_view._content(),
            embed=multi_view._summary_embed(),
            view=multi_view,
            ephemeral=True,
            wait=True,
        )
        multi_view.bind_message(multi_message)
        await multi_view.wait()
        if not multi_view.value:
            await interaction.followup.send("? Keine Nutzer gew?hlt. Abgebrochen.", ephemeral=True)
            return
        target_user_ids = [int(user_id) for user_id in multi_view.value]

    amount = await _select_number(
        interaction,
        "W?hle die Menge Infinitydust:",
        DUST_MENU_AMOUNTS,
    )
    if not amount:
        await interaction.followup.send("? Keine Menge gew?hlt. Abgebrochen.", ephemeral=True)
        return

    channel_id = int(getattr(interaction.channel, "id", 0) or 0)
    guild_id = int(getattr(interaction, "guild_id", 0) or getattr(interaction.guild, "id", 0) or 0)
    action_key = "remove" if remove else "give"
    requested_amount = int(amount)
    results: list[tuple[int, int]] = []
    for user_id in target_user_ids:
        if remove:
            applied_amount = await remove_infinitydust(user_id, requested_amount)
        else:
            await add_infinitydust(user_id, requested_amount)
            applied_amount = requested_amount
        applied_amount = int(applied_amount)
        results.append((user_id, applied_amount))
        await log_admin_dust_action(
            interaction.user.id,
            user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            action=action_key,
            mode=mode_value,
            requested_amount=requested_amount,
            applied_amount=applied_amount,
        )

    result_sent = await _post_dust_result_message(
        interaction,
        mode=mode_value,
        remove=remove,
        amount=requested_amount,
        results=results,
    )
    if result_sent:
        await interaction.followup.send("? Vorgang abgeschlossen. Die ?ffentliche Ergebnisnachricht wurde gesendet.", ephemeral=True)
        return
    await interaction.followup.send(
        "? Vorgang abgeschlossen, aber ich konnte die ?ffentliche Ergebnisnachricht nicht senden.",
        ephemeral=True,
    )

class CardSelectPagerView(RestrictedView):
    def __init__(self, requester_id: int, cards: list[dict]):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.cards = cards
        self.page = 0
        self.value = None
        self.select = ui.Select(placeholder="Wähle eine Karte...", min_values=1, max_values=1, options=[])
        self.select.callback = self.select_callback
        self.prev_button = ui.Button(label="< Zurück", style=discord.ButtonStyle.secondary)
        self.prev_button.callback = self.prev_page
        self.next_button = ui.Button(label="Weiter >", style=discord.ButtonStyle.secondary)
        self.next_button.callback = self.next_page
        self.cancel_button = ui.Button(label="Abbrechen", style=discord.ButtonStyle.danger)
        self.cancel_button.callback = self.cancel
        self._render()

    def _render(self):
        self.clear_items()
        start = self.page * 25
        subset = self.cards[start:start + 25]
        self.select.options = [SelectOption(label=c.get("name", "?")[:100], value=c.get("name", "")) for c in subset]
        self.add_item(self.select)
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = start + 25 >= len(self.cards)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.cancel_button)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if (self.page + 1) * 25 < len(self.cards):
            self.page += 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)

class ConfirmDeleteUserView(RestrictedView):
    def __init__(self, requester_id: int, target_id: int, target_name: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.target_id = target_id
        self.target_name = target_name

    @ui.button(label="Löschen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Anforderer kann bestätigen.", ephemeral=True)
            return
        await delete_user_data(self.target_id)
        logging.info("Delete user data: actor=%s target=%s", interaction.user.id, self.target_id)
        self.stop()
        await interaction.response.edit_message(
            content=f"Daten von {self.target_name} gelöscht.", view=None
        )

    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Anforderer kann abbrechen.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)

async def send_health(interaction: discord.Interaction, visibility_key: str | None = None):
    uptime = timedelta(seconds=int(time.time() - BOT_START_TIME))
    latency_ms = int(bot.latency * 1000)
    guild_count = len(bot.guilds)
    embed = discord.Embed(title="Bot Health", color=0x2b90ff)
    embed.add_field(name="Uptime", value=str(uptime), inline=True)
    embed.add_field(name="Latency", value=f"{latency_ms} ms", inline=True)
    embed.add_field(name="Guilds", value=str(guild_count), inline=True)
    embed.add_field(name="Python", value=sys.version.split()[0], inline=True)
    embed.add_field(name="DB Path", value=str(DB_PATH), inline=True)
    embed.add_field(name="Error Count", value=str(get_error_count()), inline=True)
    await _send_with_visibility(interaction, visibility_key, embed=embed)

async def send_db_backup(interaction: discord.Interaction, visibility_key: str | None = None):
    db_path = Path(DB_PATH)
    if not db_path.exists():
        await _send_with_visibility(interaction, visibility_key, content="DB-Datei nicht gefunden.")
        return
    await _send_with_visibility(
        interaction,
        visibility_key,
        content="DB-Backup:",
        file=discord.File(str(db_path), filename=db_path.name),
    )

async def send_db_debug(interaction: discord.Interaction, visibility_key: str | None = None):
    async with db_context() as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        tables = [row[0] for row in await cursor.fetchall()]
        cursor = await db.execute("PRAGMA integrity_check")
        integrity = await cursor.fetchone()
    embed = discord.Embed(title="DB Debug", color=0x2b90ff)
    embed.add_field(name="Tables", value=str(len(tables)), inline=True)
    embed.add_field(name="Integrity", value=str(integrity[0] if integrity else "unknown"), inline=True)
    await _send_with_visibility(interaction, visibility_key, embed=embed)

async def send_debug_user(interaction: discord.Interaction, user_id: int, user_name: str, visibility_key: str | None = None):
    async with db_context() as db:
        cursor = await db.execute("SELECT team FROM user_teams WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        team_raw = row[0] if row and row[0] else "[]"
        try:
            team = json.loads(team_raw)
        except Exception:
            team = team_raw

        cursor = await db.execute("SELECT COUNT(*), SUM(anzahl) FROM user_karten WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        unique_cards = row[0] if row and row[0] else 0
        total_cards = row[1] if row and row[1] else 0

        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        infinitydust = row[0] if row and row[0] else 0

        cursor = await db.execute("SELECT COUNT(*) FROM user_card_buffs WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        buffs = row[0] if row and row[0] else 0

        cursor = await db.execute("SELECT last_daily, mission_count, last_mission_reset FROM user_daily WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        last_daily = row[0] if row else None
        mission_count = row[1] if row and row[1] else 0
        last_mission_reset = row[2] if row else None

    team_text = str(team)
    if len(team_text) > 900:
        team_text = team_text[:900] + "..."

    embed = discord.Embed(title=f"Debug User: {user_name}", color=0x2b90ff)
    embed.add_field(name="Team", value=team_text, inline=False)
    embed.add_field(name="Karten", value=f"Unique: {unique_cards} | Total: {total_cards}", inline=True)
    embed.add_field(name="Infinitydust", value=str(infinitydust), inline=True)
    embed.add_field(name="Buffs", value=str(buffs), inline=True)
    embed.add_field(name="Daily", value=str(last_daily), inline=True)
    if not ALPHA_PHASE_ENABLED:
        embed.add_field(name="Mission Count", value=str(mission_count), inline=True)
        embed.add_field(name="Mission Reset", value=str(last_mission_reset), inline=True)
    await _send_with_visibility(interaction, visibility_key, embed=embed)

async def send_logs_last(interaction: discord.Interaction, count: int, visibility_key: str | None = None):
    if not LOG_PATH.exists():
        await _send_with_visibility(interaction, visibility_key, content="Log-Datei nicht gefunden.")
        return
    content = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    tail = "\n".join(content[-int(count):])
    if not tail:
        await _send_with_visibility(interaction, visibility_key, content="Keine Logs vorhanden.")
        return
    if len(tail) > 1900:
        tail = tail[-1900:]
    await _send_with_visibility(interaction, visibility_key, content=f"```text\n{tail}\n```")

async def send_karten_validate(interaction: discord.Interaction, visibility_key: str | None = None):
    issues = validate_cards(karten)
    if not issues:
        await _send_with_visibility(interaction, visibility_key, content=f"karten.py ist valide ({len(karten)} Karten).")
        return
    preview = summarize_validation_issues(issues, max_items=20)
    await _send_with_visibility(interaction, visibility_key, content=f"Probleme gefunden:\n{preview}")

async def send_configure_add(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content=SERVER_ONLY)
        return
    async with db_context() as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
            (interaction.guild_id, interaction.channel_id),
        )
        await db.commit()
    logging.info("Configure add channel: actor=%s guild=%s channel=%s", interaction.user.id, interaction.guild_id, interaction.channel_id)
    await _send_with_visibility(
        interaction,
        visibility_key,
        content=f"✅ Hinzugefügt: {_channel_mention_or_fallback(interaction.channel)}",
    )

async def send_configure_remove(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content=SERVER_ONLY)
        return
    async with db_context() as db:
        await db.execute(
            "DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild_id, interaction.channel_id),
        )
        await db.commit()
    logging.info("Configure remove channel: actor=%s guild=%s channel=%s", interaction.user.id, interaction.guild_id, interaction.channel_id)
    await _send_with_visibility(
        interaction,
        visibility_key,
        content=f"🗑️ Entfernt: {_channel_mention_or_fallback(interaction.channel)}",
    )

async def send_configure_list(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content=SERVER_ONLY)
        return
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        rows = await cursor.fetchall()
    if not rows:
        await _send_with_visibility(interaction, visibility_key, content="ℹ️ Es sind noch keine Kanäle erlaubt.")
        return
    mentions = "\n".join(f"• <#{r[0]}>" for r in rows)
    await _send_with_visibility(interaction, visibility_key, content=f"✅ Erlaubte Kanäle:\n{mentions}")

async def send_reset_intro(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content=SERVER_ONLY)
        return
    channel_id = interaction.channel_id
    if channel_id is None:
        await _send_with_visibility(interaction, visibility_key, content="Kanal konnte nicht erkannt werden.")
        return
    async with db_context() as db:
        await db.execute(
            "DELETE FROM user_seen_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild.id, channel_id),
        )
        await db.commit()
    logging.info("Reset intro: actor=%s guild=%s channel=%s", interaction.user.id, interaction.guild_id, interaction.channel_id)
    await _send_with_visibility(
        interaction,
        visibility_key,
        content="✅ Intro-Status für ALLE in diesem Kanal zurückgesetzt. Schreibe eine Nachricht, um den Prompt erneut zu sehen.",
    )

async def send_vaultlook(interaction: discord.Interaction, user_id: int, user_name: str, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content=SERVER_ONLY)
        return
    target_user = _get_member_if_available(interaction.guild, user_id)
    mention = target_user.mention if target_user else f"<@{user_id}>"
    user_karten = await get_user_karten(user_id)
    infinitydust = await get_infinitydust(user_id)
    if not user_karten and infinitydust == 0:
        await _send_with_visibility(interaction, visibility_key, content=f"❌ {mention} hat noch keine Karten in seiner Sammlung.")
        return
    embed = discord.Embed(
        title=f"🔍 Vault von {escape_display_text(user_name, fallback=str(user_id))}",
        description=f"**{mention}** besitzt **{len(user_karten)}** verschiedene Karten:",
    )
    if infinitydust > 0:
        embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    for kartenname, anzahl in user_karten:
        karte = await get_karte_by_name(kartenname)
        if karte:
            embed.add_field(name=f"{karte['name']} (x{anzahl})", value=karte['beschreibung'][:100] + "...", inline=False)
    embed.set_footer(text=f"Vault-Lookup durch {safe_display_name(interaction.user, fallback=str(interaction.user.id))}")
    embed.color = 0xff6b6b
    logging.info("Vault look: actor=%s target=%s", interaction.user.id, user_id)
    await _send_with_visibility(interaction, visibility_key, embed=embed)

async def send_test_report(interaction: discord.Interaction, visibility_key: str | None = None):
    def flatten_commands(cmds, prefix=""):
        flat = []
        for c in cmds:
            if isinstance(c, app_commands.Group):
                new_prefix = f"{prefix}{c.name} "
                flat.extend(flatten_commands(c.commands, new_prefix))
            else:
                flat.append((f"{prefix}{c.name}", c))
        return flat

    all_cmds = bot.tree.get_commands()
    flat_cmds = flatten_commands(all_cmds)
    lines = [f"• /{name} — registriert" for name, _ in flat_cmds]
    description = "Alle registrierten Slash-Commands (inkl. Unterbefehle):\n" + "\n".join(lines) if lines else "Keine Commands registriert."
    embed = discord.Embed(
        title="🤖 Verfügbare Commands",
        description=description,
        color=0x2b90ff,
    )
    embed.add_field(
        name="Hinweis",
        value=(
            "Dieser Bericht ist nur für dich sichtbar. Ein automatisches Ausführen einzelner Slash-Commands "
            "ist nicht möglich, daher wird hier die Registrierung angezeigt."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Angefordert von {safe_display_name(interaction.user, fallback=str(interaction.user.id))} | {time.strftime('%d.%m.%Y %H:%M:%S')}")
    logging.info("Test report requested by %s", interaction.user.id)
    await _send_with_visibility(interaction, visibility_key, embed=embed)

async def send_bot_status(interaction: discord.Interaction, visibility_key: str | None = None):
    view = BotStatusView(interaction.user.id)
    embed = discord.Embed(
        title="🤖 Bot-Status setzen",
        description="Wähle den gewünschten Status:\n• Online\n• Abwesend\n• Bitte nicht stören\n• Unsichtbar",
        color=0x2b90ff,
    )
    logging.info("Bot status requested by %s", interaction.user.id)
    await _send_with_visibility(interaction, visibility_key, embed=embed, view=view)

async def send_balance_stats(interaction: discord.Interaction, visibility_key: str | None = None):
    if not await is_channel_allowed(interaction, bypass_maintenance=True):
        return
    total_cards = len(karten)
    rarity_counts = {}
    hp_values = []
    attack_max_values = []
    for card in karten:
        rarity = (card.get("seltenheit") or "unbekannt").lower()
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
        hp_values.append(card.get("hp", 100))
        attacks = card.get("attacks", [])
        for atk in attacks:
            if not isinstance(atk, dict):
                continue
            dmg = atk.get("damage")
            if isinstance(dmg, list) and len(dmg) == 2:
                attack_max_values.append(max(dmg))
            elif isinstance(dmg, int):
                attack_max_values.append(dmg)

    avg_hp = sum(hp_values) / len(hp_values) if hp_values else 0
    avg_atk = sum(attack_max_values) / len(attack_max_values) if attack_max_values else 0

    rarity_lines = []
    for rarity, count in sorted(rarity_counts.items(), key=lambda x: x[1], reverse=True):
        pct = (count / total_cards * 100) if total_cards else 0
        rarity_lines.append(f"{rarity}: {count} ({pct:.1f}%)")

    top_cards = []
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT karten_name, SUM(anzahl) as total FROM user_karten "
            "GROUP BY karten_name ORDER BY total DESC LIMIT 5"
        )
        rows = await cursor.fetchall()
        for row in rows:
            top_cards.append(f"{row[0]} ({row[1]})")

    embed = discord.Embed(title="Balance Stats", color=0x2b90ff)
    embed.add_field(name="Rarity", value="\n".join(rarity_lines) or "-", inline=False)
    embed.add_field(name="Avg HP", value=f"{avg_hp:.1f}", inline=True)
    embed.add_field(name="Avg Max Damage", value=f"{avg_atk:.1f}", inline=True)
    embed.add_field(name="Top Karten (DB)", value="\n".join(top_cards) or "Keine Daten", inline=False)
    if visibility_key:
        await _send_with_visibility(interaction, visibility_key, embed=embed)
    else:
        await _send_ephemeral(interaction, embed=embed)

DEV_ACTION_OPTIONS: list[tuple[str, str]] = [
    ("Maintenance ON", "maintenance_on"),
    ("Maintenance OFF", "maintenance_off"),
    ("Delete user data", "delete_user"),
    ("DB backup", "db_backup"),
    ("Give dust", "give_dust"),
    ("Grant card", "grant_card"),
    ("Revoke card", "revoke_card"),
    ("Set daily reset", "set_daily"),
    ("Set mission reset", "set_mission"),
    ("Health", "health"),
    ("Debug DB", "debug_db"),
    ("Debug user", "debug_user"),
    ("Debug sync", "debug_sync"),
    ("Logs last", "logs_last"),
    ("Karten validate", "karten_validate"),
    ("Kanal erlauben (hier)", "cfg_add"),
    ("Kanal entfernen (hier)", "cfg_remove"),
    ("Erlaubte Kanäle anzeigen", "cfg_list"),
    ("Intro zurücksetzen (Kanal)", "reset_intro"),
    ("Vault ansehen", "vault_look"),
    ("Bot-Status setzen", "bot_status"),
    ("Command-Report", "test_report"),
    ("Nachrichten-Sichtbarkeit", "visibility_settings"),
]
if ALPHA_PHASE_ENABLED:
    DEV_ACTION_OPTIONS = [item for item in DEV_ACTION_OPTIONS if item[1] != "set_mission"]

class DevActionSelect(ui.Select):
    def __init__(
        self,
        requester_id: int,
        options_list: list[tuple[str, str]] | None = None,
        placeholder: str = "Dev-Tools wählen...",
    ):
        self.requester_id = requester_id
        options_src = DEV_ACTION_OPTIONS if options_list is None else options_list
        options = [SelectOption(label=label, value=value) for label, value in options_src]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        await handle_dev_action(interaction, self.requester_id, action)

class DevSearchView(RestrictedView):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(DevActionSelect(requester_id, placeholder="Tippe zum Suchen..."))

    @ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Dev/Tools", description="Aktionen wählen")
        await _edit_panel_message(interaction, embed=embed, view=DevPanelView(self.requester_id))

class VisibilitySelectPagerView(RestrictedView):
    def __init__(self, requester_id: int, visibility_map: dict[str, str], page: int = 0):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.page = page
        self.visibility_map = visibility_map
        self.items = get_panel_visibility_items()
        self.select = ui.Select(placeholder="Kategorie wählen...", min_values=1, max_values=1, options=[])
        self.select.callback = self.select_callback
        self.prev_button = ui.Button(label="Vorige Seite", style=discord.ButtonStyle.secondary, row=4)
        self.prev_button.callback = self.prev_page
        self.next_button = ui.Button(label="Nächste Seite", style=discord.ButtonStyle.secondary, row=4)
        self.next_button.callback = self.next_page
        self.back_button = ui.Button(label="Zurück zum Panel", style=discord.ButtonStyle.danger, row=4)
        self.back_button.callback = self.back_to_panel
        self._render()

    def _render(self):
        self.clear_items()
        start = self.page * 25
        subset = self.items[start:start + 25]
        options = []
        for key, label, desc in subset:
            current_value = _visibility_value_for_key(key, self.visibility_map)
            current = _visibility_label(current_value)
            options.append(SelectOption(label=f"{label} ({current})", value=key, description=desc[:100]))
        if not options:
            options = [SelectOption(label="Keine Einträge", value="__none__")]
        self.select.options = options
        self.add_item(self.select)
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = start + 25 >= len(self.items)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.back_button)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        key = self.select.values[0]
        if key == "__none__":
            await interaction.response.defer()
            return
        entry = next((item for item in self.items if item[0] == key), None)
        if not entry:
            await interaction.response.send_message("Unbekannte Option.", ephemeral=True)
            return
        label = entry[1]
        current_value = _visibility_value_for_key(key, self.visibility_map)
        current = _visibility_label(current_value)
        embed = discord.Embed(
            title="Sichtbarkeit einstellen",
            description=f"**{label}**\nAktuell: **{current}**\n\nWähle die Sichtbarkeit:",
        )
        await _edit_panel_message(
            interaction,
            embed=embed,
            view=VisibilityToggleView(self.requester_id, key, self.page),
        )

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if (self.page + 1) * 25 < len(self.items):
            self.page += 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def back_to_panel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Dev/Tools", description="Aktionen wählen")
        await _edit_panel_message(interaction, embed=embed, view=DevPanelView(self.requester_id))

class VisibilityToggleView(RestrictedView):
    def __init__(self, requester_id: int, message_key: str, page: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.message_key = message_key
        self.page = page

    async def _back_to_list(self, interaction: discord.Interaction):
        visibility_map = await get_visibility_map(interaction.guild_id)
        embed = discord.Embed(
            title="Sichtbarkeit",
            description="Wähle eine Kategorie, um die Sichtbarkeit zu ändern.",
        )
        view = VisibilitySelectPagerView(self.requester_id, visibility_map, page=self.page)
        await _edit_panel_message(interaction, embed=embed, view=view)

    @ui.button(label="Öffentlich", style=discord.ButtonStyle.success)
    async def set_public(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await set_message_visibility(interaction.guild_id, self.message_key, VISIBILITY_PUBLIC)
        await self._back_to_list(interaction)

    @ui.button(label="Nur sichtbar", style=discord.ButtonStyle.secondary)
    async def set_private(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await set_message_visibility(interaction.guild_id, self.message_key, VISIBILITY_PRIVATE)
        await self._back_to_list(interaction)

    @ui.button(label="Zurück", style=discord.ButtonStyle.danger)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await self._back_to_list(interaction)

async def show_visibility_settings(interaction: discord.Interaction, requester_id: int, page: int = 0):
    visibility_map = await get_visibility_map(interaction.guild_id)
    embed = discord.Embed(
        title="Sichtbarkeit",
        description="Wähle eine Kategorie, um die Sichtbarkeit zu ändern.",
    )
    view = VisibilitySelectPagerView(requester_id, visibility_map, page=page)
    await _edit_panel_message(interaction, embed=embed, view=view)

async def handle_dev_action(interaction: discord.Interaction, requester_id: int, action: str):
    if interaction.user.id != requester_id:
        await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
        return
    if not await require_owner_or_dev(interaction):
        return
    if not await is_channel_allowed(interaction):
        return

    if action == "maintenance_on":
        if interaction.guild is None:
            await _send_with_visibility(interaction, "maintenance", content=SERVER_ONLY)
            return
        await set_maintenance_mode(interaction.guild.id, True)
        logging.info("Maintenance ON by %s in guild %s", interaction.user.id, interaction.guild_id)
        await _send_with_visibility(interaction, "maintenance", content="Wartungsmodus aktiviert.")
        return
    if action == "maintenance_off":
        if interaction.guild is None:
            await _send_with_visibility(interaction, "maintenance", content=SERVER_ONLY)
            return
        await set_maintenance_mode(interaction.guild.id, False)
        logging.info("Maintenance OFF by %s in guild %s", interaction.user.id, interaction.guild_id)
        await _send_with_visibility(interaction, "maintenance", content="Wartungsmodus deaktiviert.")
        return
    if action == "delete_user":
        user_id, user_name = await _select_user(interaction, "Wähle den Nutzer für Löschen:")
        if not user_id or user_name is None:
            return
        view = ConfirmDeleteUserView(interaction.user.id, user_id, user_name)
        await _send_with_visibility(
            interaction,
            "delete_user",
            content=f"Wirklich alle Bot-Daten von {user_name} löschen?",
            view=view,
        )
        return
    if action == "db_backup":
        logging.info("DB backup requested by %s", interaction.user.id)
        await send_db_backup(interaction, visibility_key="db_backup")
        return
    if action == "give_dust":
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Dust:")
        if not user_id:
            return
        amount = await _select_number(interaction, "Menge wählen", [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000])
        if not amount:
            return
        await add_infinitydust(user_id, int(amount))
        logging.info("Give dust: actor=%s target=%s amount=%s", interaction.user.id, user_id, amount)
        await _send_with_visibility(interaction, "give_dust", content=f"{user_name} erhält {amount}x Infinitydust.")
        return
    if action == "grant_card":
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Karte vergeben:")
        if not user_id:
            return
        card_name = await _select_card(interaction, "Karte auswählen:")
        if not card_name:
            return
        amount = await _select_number(interaction, "Anzahl wählen", [1, 2, 5, 10, 20, 50, 100])
        if not amount:
            return
        await add_karte_amount(user_id, card_name, int(amount))
        logging.info("Grant card: actor=%s target=%s card=%s amount=%s", interaction.user.id, user_id, card_name, amount)
        await _send_with_visibility(interaction, "grant_card", content=f"{user_name} erhält {amount}x {card_name}.")
        return
    if action == "revoke_card":
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Karte abziehen:")
        if not user_id:
            return
        card_name = await _select_card(interaction, "Karte auswählen:")
        if not card_name:
            return
        amount = await _select_number(interaction, "Anzahl wählen", [1, 2, 5, 10, 20, 50, 100])
        if not amount:
            return
        new_amount = await remove_karte_amount(user_id, card_name, int(amount))
        logging.info("Revoke card: actor=%s target=%s card=%s amount=%s new_total=%s", interaction.user.id, user_id, card_name, amount, new_amount)
        await _send_with_visibility(interaction, "revoke_card", content=f"Neue Menge {card_name} bei {user_name}: {new_amount}.")
        return
    if action == "set_daily":
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Daily-Reset:")
        if not user_id:
            return
        async with db_context() as db:
            await db.execute(
                "INSERT INTO user_daily (user_id, last_daily) VALUES (?, 0) "
                "ON CONFLICT(user_id) DO UPDATE SET last_daily = 0",
                (user_id,),
            )
            await db.commit()
        logging.info("Daily reset: actor=%s target=%s", interaction.user.id, user_id)
        await _send_with_visibility(interaction, "set_daily", content=f"Daily für {user_name} zurückgesetzt.")
        return
    if action == "set_mission":
        if ALPHA_PHASE_ENABLED:
            await _send_with_visibility(
                interaction,
                "set_mission",
                content="🧪 Alpha-Phase: Mission-Reset ist aktuell deaktiviert.",
            )
            return
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Mission-Reset:")
        if not user_id:
            return
        today_start = berlin_midnight_epoch()
        async with db_context() as db:
            await db.execute(
                "INSERT INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, 0, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET mission_count = 0, last_mission_reset = ?",
                (user_id, today_start, today_start),
            )
            await db.commit()
        logging.info("Mission reset: actor=%s target=%s", interaction.user.id, user_id)
        await _send_with_visibility(interaction, "set_mission", content=f"Mission-Reset für {user_name} gesetzt.")
        return
    if action == "health":
        logging.info("Health requested by %s", interaction.user.id)
        await send_health(interaction, visibility_key="health")
        return
    if action == "debug_db":
        logging.info("Debug DB requested by %s", interaction.user.id)
        await send_db_debug(interaction, visibility_key="debug_db")
        return
    if action == "debug_user":
        user_id, user_name = await _select_user(interaction, "Wähle Nutzer für Debug:")
        if not user_id or user_name is None:
            return
        logging.info("Debug user requested by %s target=%s", interaction.user.id, user_id)
        await send_debug_user(interaction, user_id, user_name, visibility_key="debug_user")
        return
    if action == "debug_sync":
        prune_alpha_slash_commands()
        synced = await bot.tree.sync()
        logging.info("Debug sync by %s; synced=%s", interaction.user.id, len(synced))
        await _send_with_visibility(interaction, "debug_sync", content=f"Sync abgeschlossen: {len(synced)} Commands.")
        return
    if action == "logs_last":
        count = await _select_number(interaction, "Anzahl Log-Zeilen", [10, 20, 50, 100, 200])
        if not count:
            return
        await send_logs_last(interaction, int(count), visibility_key="logs_last")
        logging.info("Logs last requested by %s count=%s", interaction.user.id, count)
        return
    if action == "karten_validate":
        await send_karten_validate(interaction, visibility_key="karten_validate")
        logging.info("Karten validate requested by %s", interaction.user.id)
        return
    if action == "cfg_add":
        await send_configure_add(interaction, visibility_key="channel_config")
        return
    if action == "cfg_remove":
        await send_configure_remove(interaction, visibility_key="channel_config")
        return
    if action == "cfg_list":
        await send_configure_list(interaction, visibility_key="channel_config")
        return
    if action == "reset_intro":
        await send_reset_intro(interaction, visibility_key="reset_intro")
        return
    if action == "vault_look":
        user_id, user_name = await _select_user(interaction, "Wähle einen User für Vault-Look:")
        if not user_id or user_name is None:
            return
        await send_vaultlook(interaction, user_id, user_name, visibility_key="vault_look")
        return
    if action == "bot_status":
        await send_bot_status(interaction, visibility_key="bot_status")
        return
    if action == "test_report":
        await send_test_report(interaction, visibility_key="test_report")
        return
    if action == "visibility_settings":
        await show_visibility_settings(interaction, requester_id)
        return

class DevPanelView(RestrictedView):
    def __init__(self, requester_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.page = page

        self.select = DevActionSelect(requester_id, options_list=[])
        self.add_item(self.select)

        self.prev_button = ui.Button(label="Vorige Seite", style=discord.ButtonStyle.secondary, row=4)
        self.prev_button.callback = self.prev_page
        self.add_item(self.prev_button)

        self.next_button = ui.Button(label="Nächste Seite", style=discord.ButtonStyle.secondary, row=4)
        self.next_button.callback = self.next_page
        self.add_item(self.next_button)

        self._render()

    def _render(self):
        start = self.page * 25
        subset = DEV_ACTION_OPTIONS[start:start + 25]
        self.select.options = [SelectOption(label=label, value=value) for label, value in subset]
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = start + 25 >= len(DEV_ACTION_OPTIONS)

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if (self.page + 1) * 25 < len(DEV_ACTION_OPTIONS):
            self.page += 1
        self._render()
        await interaction.response.edit_message(view=self)

    @ui.button(label="Suche", style=discord.ButtonStyle.secondary, row=3)
    async def search(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if len(DEV_ACTION_OPTIONS) > 25:
            await interaction.response.send_message("Zu viele Optionen für die Suche. Nutze die Seiten.", ephemeral=True)
            return
        embed = discord.Embed(title="Dev-Tools Suche", description="Tippe im Auswahlfeld, um zu filtern.")
        await _edit_panel_message(interaction, embed=embed, view=DevSearchView(self.requester_id))

    @ui.button(label="Zurück", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Panel", description="Hauptmenü")
        await _edit_panel_message(interaction, embed=embed, view=PanelHomeView(self.requester_id))

class StatsPanelView(RestrictedView):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id

    @ui.button(label="Balance Stats anzeigen", style=discord.ButtonStyle.primary)
    async def show_stats(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await send_balance_stats(interaction)

    @ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Panel", description="Hauptmenü")
        await _edit_panel_message(interaction, embed=embed, view=PanelHomeView(self.requester_id))

class PanelHomeView(RestrictedView):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id

    @ui.button(label="Dev/Tools", style=discord.ButtonStyle.primary)
    async def dev_tools(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Dev/Tools", description="Aktionen wählen")
        await _edit_panel_message(interaction, embed=embed, view=DevPanelView(self.requester_id))

    @ui.button(label="Stats", style=discord.ButtonStyle.secondary)
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        embed = discord.Embed(title="Stats", description="Statistik-Tools")
        await _edit_panel_message(interaction, embed=embed, view=StatsPanelView(self.requester_id))

    @ui.button(label="Schliessen", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await _edit_panel_message(interaction, content="Panel geschlossen.", embed=None, view=None)
# =========================
# Präsenz-Status Kreise + Live-User-Picker (wiederverwendbar für /kampf und /sammlung-ansehen)
# =========================

# Mapping: Discord Presence -> Farbe/Circle + Sort-Priorität
class StatusUserPickerView(RestrictedView):
    """
    Wiederverwendbarer Nutzer-Picker mit:
    - farbigen Status-Kreisen vor dem Namen (grün/orange/rot/schwarz)
    - Sortierung: grün, orange, rot, schwarz; innerhalb Gruppe stabile Reihenfolge
    - Live-Update (Polling) ohne Flackern; identischer Mechanismus für /kampf und /sammlung-ansehen
    """
    def __init__(
        self,
        requester_id: int,
        guild: discord.Guild,
        include_bot_option: bool = False,
        exclude_user_id: int | None = None,
        refresh_interval_sec: int = 5,
    ):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.guild = guild
        self.include_bot_option = include_bot_option
        self.exclude_user_id = exclude_user_id
        self.refresh_interval_sec = refresh_interval_sec

        # Auswahl-Element
        self.value: str | None = None
        self.select = ui.Select(
            placeholder="Wähle einen Nutzer...",
            min_values=1,
            max_values=1,
            options=[SelectOption(label="Lade Nutzer...", value="__loading__")]
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        # Interne Felder für Live-Update
        self._message: discord.Message | None = None
        self._task: asyncio.Task | None = None
        self._baseline_index: dict[int, int] = {}  # stabile Reihenfolge pro User-ID
        self._last_signature: list[tuple[str, str]] = []  # [(value,label)] zur Änderungs-Erkennung

    async def start_auto_refresh(self, message: discord.Message):
        """Startet das periodische Aktualisieren der Optionsliste."""
        self._message = message
        # Erste Füllung sofort
        await self._refresh_options(force=True)
        # Hintergrund-Task starten
        if self._task is None:
            self._task = asyncio.create_task(self._auto_loop())

    def stop(self) -> None:
        super().stop()
        try:
            if self._task and not self._task.done():
                self._task.cancel()
        except Exception:
            logging.exception("Unexpected error")

    async def _auto_loop(self):
        try:
            while not self.is_finished():
                await asyncio.sleep(self.refresh_interval_sec)
                await self._refresh_options()
        except asyncio.CancelledError:
            pass
        except Exception:
            # Keine Exceptions nach außen leaken
            pass

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der anfragende Nutzer kann wählen!", ephemeral=True)
            return

        choice = self.select.values[0]
        if choice == "__loading__":
            await interaction.response.send_message("Liste wird noch geladen...", ephemeral=True)
            return
        if choice == "search":
            modal = UserSearchModal(
                self.guild,
                interaction.user,
                parent_view=self,
                include_bot_option=self.include_bot_option,
                exclude_user_id=self.exclude_user_id,
            )
            await interaction.response.send_modal(modal)
            return
        if choice == "none":
            await interaction.response.send_message("❌ Keine Nutzer gefunden.", ephemeral=True)
            return

        # Auswahl übernehmen und View schließen
        self.value = choice
        self.stop()
        await interaction.response.defer()

    async def _refresh_options(self, force: bool = False):
        """Baut die Optionsliste neu und editiert die Nachricht nur bei Änderungen."""
        options = self._build_options()

        # Signatur zum Vergleich
        signature = [(opt.value, opt.label) for opt in options]
        if not force and signature == self._last_signature:
            return  # Keine Änderungen -> kein Edit (vermeidet Flackern)

        self._last_signature = signature
        self.select.options = options

        # Nachricht aktualisieren
        if self._message:
            try:
                await self._message.edit(view=self)
            except Exception:
                logging.exception("Unexpected error")

    def _build_options(self) -> list[SelectOption]:
        # Baseline-Reihenfolge initialisieren (einmalig) aus aktueller Gildeliste
        if not self._baseline_index:
            baseline = []
            for idx, member in enumerate(self.guild.members):
                if member.bot:
                    continue
                baseline.append(member.id)
            self._baseline_index = {uid: i for i, uid in enumerate(baseline)}

        # Kandidaten sammeln (keine Bots), optional eigenen User ausschließen
        members: list[discord.Member] = []
        for m in self.guild.members:
            if m.bot:
                continue
            if self.exclude_user_id and m.id == self.exclude_user_id:
                continue
            members.append(m)

        # Sortieren nach Status (grün, orange, rot, schwarz) und dann Baseline
        def sort_key(m: discord.Member):
            color = _presence_to_color(m)
            pri = STATUS_PRIORITY_MAP.get(color, 3)
            base = self._baseline_index.get(m.id, 10_000_000)
            return (pri, base)

        members_sorted = sorted(members, key=sort_key)

        # Optionen aufbauen
        opts: list[SelectOption] = [SelectOption(label="🔍 Nach Name suchen", value="search")]
        if self.include_bot_option:
            # Bot-Option unverändert wie in /kampf
            opts.append(SelectOption(label="🤖 Bot", value="bot"))

        # Maximal 25 Optionen insgesamt
        max_user_opts = 25 - len(opts)

        for m in members_sorted[:max_user_opts]:
            color = _presence_to_color(m)
            circle = STATUS_CIRCLE_MAP.get(color, "⚫")
            opts.append(
                SelectOption(
                    label=safe_user_option_label(m, prefix=f"{circle} "),
                    value=str(m.id),
                )
            )

        if len(opts) == 1 or (self.include_bot_option and len(opts) == 2):
            opts.append(SelectOption(label="Keine Nutzer gefunden", value="none"))

        return opts

# Starte den Bot
# =========================
# /bot-status – Bot-Präsenz via Auswahlmenü setzen
# =========================

class BotStatusSelect(ui.Select):
    def __init__(self, requester_id: int):
        self.requester_id = requester_id
        options = [
            SelectOption(label="🟢 Online", value="online"),
            SelectOption(label="🟡 Abwesend", value="idle"),
            SelectOption(label="🔴 Bitte nicht stören", value="dnd"),
            SelectOption(label="⚫ Unsichtbar", value="invisible"),
        ]
        super().__init__(placeholder="Wähle den neuen Bot-Status ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await send_interaction_response(interaction, content="Nicht dein Menü!", ephemeral=True)
            return
        choice = self.values[0]
        new_status = BOT_STATUS_MAP.get(choice, discord.Status.online)
        try:
            await interaction.client.change_presence(status=new_status)
        except discord.HTTPException as exc:
            logging.exception("Failed to change bot presence to %s", choice)
            await send_interaction_response(interaction, content=f"❌ Fehler beim Setzen des Status: {exc}", ephemeral=True)
            return

        try:
            await save_bot_presence_status(choice)
        except aiosqlite.Error:
            logging.exception("Failed to persist bot status %s", choice)
            await send_interaction_response(
                interaction,
                content="❌ Status wurde gesetzt, aber konnte nicht in der Datenbank gespeichert werden.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ Bot-Status geändert",
            description=f"Neuer Status: {BOT_STATUS_LABELS.get(choice, 'Online')}",
            color=0x2b90ff
        )
        await edit_interaction_message(interaction, embed=embed, view=None)

class BotStatusView(RestrictedView):
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.add_item(BotStatusSelect(requester_id))

_player_commands = register_player_commands(bot, sys.modules[__name__])
täglich = _player_commands["täglich"]
eingeladen = _player_commands["eingeladen"]
fuse = _player_commands["fuse"]
vault = _player_commands["vault"]
anfang = _player_commands["anfang"]

_gameplay_commands = register_gameplay_commands(bot, sys.modules[__name__])
mission = _gameplay_commands["mission"]
story = _gameplay_commands["story"]
fight = _gameplay_commands["fight"]

_admin_commands = register_admin_commands(bot, sys.modules[__name__])
configure_group = _admin_commands["configure_group"]
add_channel_shortcut = _admin_commands["add_channel_shortcut"]
configure_add = _admin_commands["configure_add"]
configure_remove = _admin_commands["configure_remove"]
configure_list = _admin_commands["configure_list"]
reset_intro = _admin_commands["reset_intro"]
vaultlook = _admin_commands["vaultlook"]
test_bericht = _admin_commands["test_bericht"]
give = _admin_commands["give"]
give_op = _admin_commands["give_op"]
panel = _admin_commands["panel"]
BALANCE_GROUP = _admin_commands["BALANCE_GROUP"]
balance_stats = _admin_commands["balance_stats"]
bot_status = _admin_commands["bot_status"]

if __name__ == "__main__":
    run_bot(bot, close_db=close_db)

