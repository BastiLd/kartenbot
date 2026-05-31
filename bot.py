from __future__ import annotations

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
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable, Iterable, Protocol, TypedDict, cast

import discord
from discord import app_commands, ui, SelectOption
from discord.ext import commands

from botcommands import (
    register_admin_commands,
    register_gameplay_commands,
    register_player_commands,
)
from botcore.command_api import build_command_api
from botcore.feature_config import boss_switch_enabled
from services.mission_rewards import MissionRewardAccumulator, commit_on_mission_success
from services import afk_tracker
from services import mission_rewards
from botcore.facades import AdminFacade, GameplayFacade, PlayerFacade
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
from karten import (
    DIRECT_DAMAGE_CAP,
    SPECIAL_DAMAGE_UPGRADE_MAX_TIMES,
    SPECIAL_DAMAGE_UPGRADE_STEP,
    STANDARD_DAMAGE_UPGRADE_MAX_TIMES,
    STANDARD_DAMAGE_UPGRADE_STEP,
    DOT_TYPE_DEFAULTS,
    karten as RAW_KARTEN,
)
from battle_flow_config import should_carry_cooldowns, should_carry_mission_cooldowns
import game_ui_texts
from mission_enemies import (
    get_operation_broken_timeline_encounters,
    get_operation_goldener_kaefig_encounters,
    get_operation_gruener_terror_encounters,
    get_operation_hexenfeuer_encounters,
    get_operation_technischer_kollaps_encounters,
)
from services.battle import (
    STATUS_CIRCLE_MAP,
    STATUS_PRIORITY_MAP,
    _format_attack_label,
    _presence_to_color,
    build_battle_log_entry,
    calculate_damage,
    create_battle_embed,
    create_battle_log_embed,
    render_boss_special_activation,
    resolve_multi_hit_damage,
    update_battle_log,
)
from services import battle_state
from services.coercion import (
    _coerce_damage_input,
    _dict_str_any,
    _int_keyed_bool_dict,
    _int_keyed_dict,
    _int_keyed_float_dict,
    _int_keyed_int_dict,
    _json_clone,
    _list_any,
    _maybe_float,
    _maybe_int,
    _nested_int_keyed_dict,
    _nested_int_keyed_int_dict,
    _random_int_from_range,
    _range_pair,
)
from services.card_validation import summarize_validation_issues, validate_cards
from services.battle_types import CardData
from services.analytics import log_event as log_analytics_event
from services.card_variants import (
    base_card_name,
    build_runtime_card,
    card_has_multiple_variants,
    default_variant_name_for_base,
    exact_variant_names_with_amounts,
    group_owned_cards_by_base,
    normalize_owned_card_name,
    variant_names_for_base,
)
from services.card_pool import (
    ALPHA_PLAYABLE_CARD_NAMES,
    canonical_card_name,
    filter_owned_cards_for_gameplay,
    gameplay_cards,
    random_gameplay_card,
)
from services.guild_settings import (
    add_give_op_role,
    add_give_op_user,
    get_give_op_allowed_roles,
    get_give_op_allowed_users,
    get_latest_anfang_message as load_latest_anfang_message,
    get_message_visibility as resolve_message_visibility,
    get_visibility_map as load_visibility_map,
    get_visibility_override as load_visibility_override,
    is_alpha_enabled,
    is_beta_enabled,
    is_maintenance_enabled,
    remove_give_op_role,
    remove_give_op_user,
    set_alpha_enabled,
    set_beta_enabled,
    set_latest_anfang_message as store_latest_anfang_message,
    set_maintenance_mode,
    set_message_visibility,
)
from services.invite_store import (
    create_invite_pending,
    find_existing_invite_pair,
    finalize_invite_pending_if_ready,
    get_invite_completed_count,
    get_invite_max_member_age_days,
    load_invite_pending,
    mark_invite_pending_flag,
    set_invite_pending_message_id,
    set_invite_max_member_age_days,
)
from items import get_item_by_id
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
from services.stats_export import build_stats_workbook
from services.card_grant import grant_cards_to_users
from services.user_data import (
    add_exact_card_variant_once,
    add_card_buff,
    add_infinitydust as _add_infinitydust,
    add_karte,
    add_karte_amount,
    add_mission_reward,
    add_units,
    check_and_add_karte,
    delete_user_data,
    get_card_buffs,
    get_infinitydust,
    get_units,
    get_last_karte,
    get_mission_count,
    get_team,
    get_user_karten,
    has_exact_card_variant,
    increment_mission_count,
    log_admin_dust_action,
    remove_invalid_damage_card_buffs,
    remove_infinitydust,
    remove_karte_amount,
    set_team,
    spend_infinitydust,
    spend_units,
)
import secrets

configure_logging()

KATABUMP_MAX_INTERACTIONS_PER_MIN = 200
KATABUMP_INTERACTION_WINDOW_SEC = 60
DUST_MENU_AMOUNTS = [5, 10, 15, 20, 25, 30]
FIGHT_OPPONENT_ROLE_ID = 1482325886471766090
_interaction_timestamps = deque()
_persistent_views_registered = False

__version__ = "2.3.5"


class CardCatalog:
    def __init__(self, cards: list[CardData]) -> None:
        self._all_cards = list(cards)

    def _gameplay_view(self) -> list[CardData]:
        return gameplay_cards(self._all_cards, alpha_enabled=ALPHA_PHASE_ENABLED)

    def __iter__(self):
        return iter(self._all_cards)

    def __len__(self) -> int:
        return len(self._gameplay_view())

    def __getitem__(self, item):
        if isinstance(item, slice):
            return self._all_cards[item]
        return self._gameplay_view()[item]

    def all_cards(self) -> list[CardData]:
        return list(self._all_cards)


karten = CardCatalog(cast(list[CardData], RAW_KARTEN))

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
VIEW_KIND_MISSION_ENCOUNTER_PREVIEW = "mission_encounter_preview"
VIEW_KIND_MISSION_BATTLE = "mission_battle"
VIEW_KIND_INVITE_CONFIRM = "invite_confirm"

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


def _thread_id_for_channel(channel: object) -> int:
    if isinstance(channel, discord.Thread):
        return int(channel.id)
    return 0


def current_gameplay_cards() -> list[CardData]:
    return gameplay_cards(karten, alpha_enabled=ALPHA_PHASE_ENABLED)


def _filter_owned_cards_for_current_mode(user_cards: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return filter_owned_cards_for_gameplay(user_cards, alpha_enabled=ALPHA_PHASE_ENABLED)


def _group_owned_cards_for_current_mode(user_cards: list[tuple[str, int]]) -> list[dict[str, Any]]:
    return group_owned_cards_by_base(_filter_owned_cards_for_current_mode(user_cards), cards=karten)


def _owned_variant_rows_for_base(
    user_cards: list[tuple[str, int]],
    base_name: str,
) -> list[tuple[str, int]]:
    filtered_cards = _filter_owned_cards_for_current_mode(user_cards)
    return exact_variant_names_with_amounts(filtered_cards, base_name, cards=karten)


def _group_option_label(group: dict[str, Any]) -> str:
    base_name = str(group.get("base_name") or "Karte")
    total_amount = int(group.get("total_amount", 0) or 0)
    return f"{base_name} (x{total_amount})" if total_amount > 1 else base_name


def _fight_challenge_card_label(card_name: str) -> str:
    card = build_runtime_card(card_name, cards=karten)
    if card is None:
        return str(card_name or "Unbekannte Karte").strip() or "Unbekannte Karte"
    base_name = str(card.get("base_name") or card.get("name") or "").strip()
    selected_name = str(card.get("name") or base_name).strip()
    if base_name and selected_name and selected_name != base_name:
        return f"{base_name} [{selected_name}]"
    return base_name or selected_name or "Unbekannte Karte"


def _fight_challenge_prompt(challenged_mention: str, challenger_card_name: str) -> str:
    card_label = _fight_challenge_card_label(challenger_card_name)
    return (
        f"{challenged_mention}, du wurdest zu einem 1v1-Kartenkampf herausgefordert!\n"
        f"Herausforderer-Karte: **{card_label}**"
    )


async def _log_event_safe(event_type: str, **kwargs: Any) -> None:
    try:
        await log_analytics_event(event_type, **kwargs)
    except Exception:
        logging.exception("Failed to write analytics event: %s", event_type)


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


def _outgoing_reduction_effect_text(
    original_damage: int,
    final_damage: int,
    *,
    source: object | None = None,
) -> str:
    before = max(0, int(original_damage or 0))
    after = max(0, int(final_damage or 0))
    source_name = str(_effect_source_name(source)).strip() if source else ""
    if source_name:
        return (
            f"Ausgehende Reduktion: Normal wären {before} Schaden möglich gewesen, "
            f"durch {escape_display_text(source_name, fallback='Effekt')} jetzt {after} Schaden."
        )
    return f"Ausgehende Reduktion: Normal wären {before} Schaden möglich gewesen, jetzt {after} Schaden."


def _overflow_recoil_source(source: object | None = None) -> str:
    source_name = str(_effect_source_name(source)).strip() if source else ""
    if source_name:
        return f"Überlauf-Rückstoß durch {escape_display_text(source_name, fallback='Effekt')}"
    return "Überlauf-Rückstoß"


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


def _resolve_member_status(member: discord.Member) -> discord.Status:
    """Liefert den robustesten verfügbaren Online-Status eines Members.

    `member.status` kann je nach Cache und Geräte-Verteilung knapp daneben
    liegen. Wir prüfen darum erst Mobile/Desktop/Web und greifen sonst auf
    den raw_status zurück, der direkt vom Gateway gesendet wird.
    """
    candidates: list[object] = []
    for attr in ("desktop_status", "mobile_status", "web_status"):
        try:
            candidates.append(getattr(member, attr, None))
        except Exception:
            continue
    candidates.append(getattr(member, "status", None))

    priority = {
        discord.Status.online: 0,
        discord.Status.idle: 1,
        discord.Status.dnd: 2,
        discord.Status.invisible: 3,
        discord.Status.offline: 4,
    }
    best: discord.Status | None = None
    best_score: int | None = None
    for candidate in candidates:
        if isinstance(candidate, discord.Status):
            status_value = candidate
        elif isinstance(candidate, str) and candidate:
            try:
                status_value = discord.Status(candidate)
            except ValueError:
                continue
        else:
            continue
        score = priority.get(status_value, 4)
        if best_score is None or score < best_score:
            best_score = score
            best = status_value
    if best is not None:
        return best

    raw_status = getattr(member, "raw_status", None)
    if isinstance(raw_status, str) and raw_status:
        try:
            return discord.Status(raw_status)
        except ValueError:
            pass
    return discord.Status.offline


def _member_presence_priority(member: discord.Member) -> int:
    status = _resolve_member_status(member)
    if status == discord.Status.online:
        return 0
    if status == discord.Status.idle:
        return 1
    if status == discord.Status.dnd:
        return 2
    return 3


def _member_status_circle(member: discord.Member) -> str:
    status = _resolve_member_status(member)
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


def _effect_int(effect: dict[str, object], key: str, default: int = 0) -> int:
    return _maybe_int(effect.get(key, default)) or default


def _effect_amount(effect: dict[str, object], key: str, default: int = 0) -> int:
    return _random_int_from_range(effect.get(key, default), default=default)


def _effect_amount_label(value: object, default: int = 0) -> str:
    min_value, max_value = _range_pair(value, default_min=default, default_max=default)
    if min_value == max_value:
        return str(min_value)
    return f"{min_value}-{max_value}"


def _is_dot_effect_type(effect_type: object) -> bool:
    return str(effect_type or "").strip().lower() in DOT_TYPE_DEFAULTS


def _dot_label(effect_type: object) -> str:
    key = str(effect_type or "").strip().lower()
    return str(DOT_TYPE_DEFAULTS.get(key, {}).get("label") or "Effekt")


def _dot_icon(effect_type: object) -> str:
    key = str(effect_type or "").strip().lower()
    return str(DOT_TYPE_DEFAULTS.get(key, {}).get("icon") or "")


def _resolve_dot_damage(effect_type: object, raw_damage: object) -> int:
    key = str(effect_type or "").strip().lower()
    configured_cap = int(DOT_TYPE_DEFAULTS.get(key, {}).get("max_damage") or 0)
    damage = max(0, _random_int_from_range(raw_damage, default=0))
    if configured_cap > 0:
        damage = min(damage, configured_cap)
    return max(0, damage)


def _append_dot_effect(
    active_effects: dict[int, list[dict[str, object]]],
    *,
    target_id: int,
    attacker_id: int,
    effect_type: object,
    duration: object,
    damage: object,
    damage_multiplier: float = 1.0,
) -> tuple[int, int]:
    resolved_type = str(effect_type or "").strip().lower()
    resolved_duration = max(1, _random_int_from_range(duration, default=1))
    resolved_damage = _resolve_dot_damage(resolved_type, damage)
    multiplier = max(0.0, float(damage_multiplier or 1.0))
    if abs(multiplier - 1.0) > 1e-9:
        resolved_damage = max(0, int(round(resolved_damage * multiplier)))
    active_effects[target_id].append(
        {
            "type": resolved_type,
            "duration": resolved_duration,
            "damage": resolved_damage,
            "applier": attacker_id,
        }
    )
    return resolved_duration, resolved_damage


def _apply_dot_ticks_for_applier(
    active_effects: dict[int, list[dict[str, object]]],
    *,
    target_id: int,
    applier_id: int,
    damage_callback: Callable[[int], object],
) -> tuple[int, list[str]]:
    remove: list[dict[str, object]] = []
    total_damage = 0
    events: list[str] = []
    for effect in active_effects[target_id]:
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect.get("applier") != applier_id or not _is_dot_effect_type(effect_type):
            continue
        damage = _effect_int(effect, "damage")
        if damage > 0:
            damage_callback(damage)
            total_damage += damage
            events.append(f"{_dot_label(effect_type)}: {damage} Schaden.")
        remaining_duration = _effect_int(effect, "duration") - 1
        effect["duration"] = remaining_duration
        if remaining_duration <= 0:
            remove.append(effect)
    for effect in remove:
        active_effects[target_id].remove(effect)
    return total_damage, events


NEGATIVE_STATUS_EFFECT_TYPES = frozenset(
    {
        "blind",
        "bleeding",
        "burning",
        "confusion",
        "disable_enemy_evade_and_block",
        "disable_enemy_heal_if_bleeding",
        "enemy_attack_self_damage",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "heal_curse",
        "incoming_damage_bonus",
        "incoming_damage_multiplier",
        "poison",
        "special_lock",
        "standard_lock",
        "stun",
    }
)
TURN_END_DECAY_EFFECT_TYPES = frozenset(
    {
        "burn_multiplier",
        "disable_enemy_evade_and_block",
        "disable_enemy_heal_if_bleeding",
        "enemy_attack_self_damage",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "heal_curse",
        "incoming_damage_bonus",
        "next_attack_flat_penalty",
        "standard_lock",
        "status_immunity",
        "interrupt_enemy_standard_or_heal_self",
    }
)


def _active_effect_entries(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
) -> list[dict[str, object]]:
    effect_key = str(effect_type or "").strip().lower()
    return [
        effect
        for effect in active_effects.get(player_id, [])
        if str(effect.get("type") or "").strip().lower() == effect_key
    ]


def _find_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
) -> dict[str, object] | None:
    entries = _active_effect_entries(active_effects, player_id, effect_type)
    return entries[0] if entries else None


def _append_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
    applier_id: int,
    **fields: object,
) -> dict[str, object]:
    entry: dict[str, object] = {"type": str(effect_type or "").strip().lower(), "applier": applier_id}
    entry.update(fields)
    active_effects.setdefault(player_id, []).append(entry)
    return entry


def _remove_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect: dict[str, object] | None,
) -> None:
    if effect is None:
        return
    try:
        active_effects.get(player_id, []).remove(effect)
    except ValueError:
        pass


def _label_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _damage_boost_restriction(effect: dict[str, object] | None) -> tuple[list[str], bool]:
    effect = effect or {}
    names: list[str] = []
    for key in (
        "allowed_attack_name",
        "allowed_attack_names",
        "only_attack",
        "only_attacks",
        "linked_attack",
        "linked_attacks",
        "requires_attack",
        "requires_attacks",
        "bonus_attack_name",
        "bonus_attack_names",
    ):
        raw_value = effect.get(key)
        if isinstance(raw_value, str):
            parts = [part.strip() for part in re.split(r"[,|]", raw_value) if part.strip()]
            names.extend(parts or ([raw_value.strip()] if raw_value.strip() else []))
        elif isinstance(raw_value, (list, tuple, set)):
            names.extend(str(item).strip() for item in raw_value if str(item).strip())
    only_standard = bool(effect.get("only_standard") or effect.get("standard_only"))
    seen: set[str] = set()
    unique_names: list[str] = []
    for name in names:
        key = _label_key(name)
        if key and key not in seen:
            seen.add(key)
            unique_names.append(name)
    return unique_names, only_standard


def _has_damage_boost_restriction(effect: dict[str, object] | None) -> bool:
    names, only_standard = _damage_boost_restriction(effect)
    return bool(names or only_standard)


def _restricted_damage_boost_target_text(effect: dict[str, object]) -> str:
    names, only_standard = _damage_boost_restriction(effect)
    if names:
        return ", ".join(names)
    if only_standard:
        return "Standardangriff"
    return "passende Attacke"


def _attack_matches_restricted_damage_boost(
    effect: dict[str, object],
    attack: dict[str, object],
    *,
    attack_index: int,
    standard_index: int,
) -> bool:
    names, only_standard = _damage_boost_restriction(effect)
    if only_standard and int(attack_index) != int(standard_index):
        return False
    if names:
        attack_name_key = _label_key(attack.get("name"))
        allowed = {_label_key(name) for name in names}
        return bool(attack_name_key and attack_name_key in allowed)
    return bool(only_standard)


def _matching_restricted_flat_damage_bonus(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    attack: dict[str, object],
    *,
    attack_index: int,
    standard_index: int,
) -> tuple[int, dict[str, object] | None]:
    best_amount = 0
    best_effect: dict[str, object] | None = None
    for effect in _active_effect_entries(active_effects, player_id, "restricted_damage_boost"):
        if not _attack_matches_restricted_damage_boost(
            effect,
            attack,
            attack_index=attack_index,
            standard_index=standard_index,
        ):
            continue
        amount = max(0, _effect_int(effect, "amount", 0))
        uses = max(0, _effect_int(effect, "uses", 1))
        if amount > best_amount and uses > 0:
            best_amount = amount
            best_effect = effect
    return best_amount, best_effect


def _consume_restricted_flat_damage_bonus(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    attack: dict[str, object],
    *,
    attack_index: int,
    standard_index: int,
) -> tuple[int, dict[str, object] | None]:
    amount, effect = _matching_restricted_flat_damage_bonus(
        active_effects,
        player_id,
        attack,
        attack_index=attack_index,
        standard_index=standard_index,
    )
    if effect is None or amount <= 0:
        return 0, None
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return amount, effect


def _preferred_attack_index_for_restricted_bonus(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    attacks: list[dict[str, object]],
    available_indices: list[int],
    *,
    standard_index: int,
) -> int | None:
    best_idx: int | None = None
    best_amount = 0
    for idx in available_indices:
        if not (0 <= int(idx) < len(attacks)):
            continue
        attack = attacks[idx]
        if not isinstance(attack, dict):
            continue
        amount, _effect = _matching_restricted_flat_damage_bonus(
            active_effects,
            player_id,
            attack,
            attack_index=int(idx),
            standard_index=int(standard_index),
        )
        if amount > best_amount:
            best_amount = amount
            best_idx = int(idx)
    if best_amount <= 0:
        return None
    return best_idx


def _queue_flat_damage_boost(
    owner: _WordRuntimeOwner,
    effect_events: list[str],
    *,
    target_id: int,
    applier_id: int,
    attack_name: str,
    amount: int,
    uses: int,
    effect: dict[str, object] | None = None,
) -> None:
    amount = max(0, int(amount or 0))
    uses = max(1, int(uses or 1))
    effect = effect or {}
    if _has_damage_boost_restriction(effect):
        names, only_standard = _damage_boost_restriction(effect)
        _append_active_effect(
            getattr(owner, "active_effects"),
            target_id,
            "restricted_damage_boost",
            applier_id,
            amount=amount,
            uses=uses,
            allowed_attack_names=names,
            only_standard=only_standard,
            source=attack_name,
        )
        target_text = _restricted_damage_boost_target_text(effect)
        owner._append_effect_event(
            effect_events,
            _effect_source_text(attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e) auf {target_text}."),
        )
        return
    owner.pending_flat_bonus[target_id] = max(owner.pending_flat_bonus.get(target_id, 0), amount)
    owner.pending_flat_bonus_uses[target_id] = max(owner.pending_flat_bonus_uses.get(target_id, 0), uses)
    owner._append_effect_event(effect_events, _effect_source_text(attack_name, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e)."))


def _has_status_immunity(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> bool:
    return _find_active_effect(active_effects, player_id, "status_immunity") is not None


def _should_block_negative_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: object,
) -> bool:
    effect_key = str(effect_type or "").strip().lower()
    return effect_key in NEGATIVE_STATUS_EFFECT_TYPES and _has_status_immunity(active_effects, player_id)


def _consume_status_immunity(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
) -> bool:
    effect = _find_active_effect(active_effects, player_id, "status_immunity")
    if effect is None:
        return False
    turns_left = max(0, _effect_int(effect, "turns", 1) - 1)
    effect["turns"] = turns_left
    if turns_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return True


def _consume_turn_end_decay_effects(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
) -> None:
    remove: list[dict[str, object]] = []
    for effect in active_effects.get(player_id, []):
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect_type not in TURN_END_DECAY_EFFECT_TYPES:
            continue
        turns_left = _effect_int(effect, "turns", 1) - 1
        effect["turns"] = turns_left
        if turns_left <= 0:
            remove.append(effect)
    for effect in remove:
        _remove_active_effect(active_effects, player_id, effect)


def _consume_attack_penalty(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> int:
    effect = _find_active_effect(active_effects, player_id, "next_attack_flat_penalty")
    if effect is None:
        return 0
    penalty = max(0, _effect_amount(effect, "amount", 0))
    turns_left = max(0, _effect_int(effect, "turns", 1) - 1)
    effect["turns"] = turns_left
    if turns_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return penalty


def _consume_next_standard_damage_override(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    *,
    attack_index: int,
    standard_index: int,
    current_damage: int,
) -> tuple[int, dict[str, object] | None]:
    if attack_index != standard_index or current_damage <= 0:
        return current_damage, None
    effect = _find_active_effect(active_effects, player_id, "next_standard_damage_override")
    if effect is None:
        return current_damage, None
    overridden = max(0, _effect_amount(effect, "damage", current_damage))
    turns_left = max(0, _effect_int(effect, "turns", 1) - 1)
    effect["turns"] = turns_left
    if turns_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return overridden, effect


def _consume_capped_damage_multiplier(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    current_damage: int,
) -> tuple[int, int, dict[str, object] | None]:
    if current_damage <= 0:
        return current_damage, 0, None
    effect = _find_active_effect(active_effects, player_id, "capped_damage_multiplier")
    if effect is None:
        return current_damage, 0, None
    multiplier = max(1.0, float(_maybe_float(effect.get("multiplier")) or 1.0))
    max_bonus = max(0, _effect_amount(effect, "max_bonus", 0))
    raw_bonus = max(0, int(round(current_damage * (multiplier - 1.0))))
    bonus = min(raw_bonus, max_bonus) if max_bonus > 0 else raw_bonus
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return current_damage + bonus, bonus, effect


def _consume_attack_heal(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
) -> tuple[int, dict[str, object] | None]:
    effect = _find_active_effect(active_effects, player_id, "attack_heal")
    if effect is None:
        return 0, None
    heal_amount = max(0, _effect_amount(effect, "amount", 0))
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return heal_amount, effect


def _clear_negative_active_effects(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
) -> list[str]:
    removed: list[str] = []
    keep: list[dict[str, object]] = []
    for effect in active_effects.get(player_id, []):
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect_type in NEGATIVE_STATUS_EFFECT_TYPES or _is_dot_effect_type(effect_type):
            removed.append(effect_type)
        else:
            keep.append(effect)
    active_effects[player_id] = keep
    return removed


def _apply_word_runtime_effect(
    owner: _WordRuntimeOwner,
    effect_events: list[str],
    *,
    eff_type: str,
    target_id: int,
    attack_name: str,
    effect: dict[str, object] | None = None,
) -> bool:
    effect = effect or {}
    if eff_type == "clear_negative_effects":
        active_effects = owner.active_effects
        removed = _clear_negative_active_effects(active_effects, target_id)
        if hasattr(owner, "special_lock_next_turn"):
            owner.special_lock_next_turn[target_id] = 0
        if hasattr(owner, "blind_next_attack"):
            owner.blind_next_attack[target_id] = 0.0
        count = len(removed)
        message = "Negative Effekte entfernt." if count else "Keine negativen Effekte zum Entfernen."
        owner._append_effect_event(effect_events, _effect_source_text(attack_name, message))
        return True
    if eff_type == "random_pym_debuff":
        if random.random() < 0.5:
            owner.queue_outgoing_attack_modifier(target_id, flat=10, turns=1, source=attack_name)
            owner._append_effect_event(effect_events, _effect_source_text(attack_name, "Pym-Effekt: Der nächste gegnerische Angriff macht 10 Schaden weniger."))
        else:
            owner.blind_next_attack[target_id] = max(owner.blind_next_attack.get(target_id, 0.0), 1.0)
            owner._append_effect_event(effect_events, _effect_source_text(attack_name, "Pym-Effekt: Der nächste gegnerische Angriff verfehlt."))
        return True
    if eff_type == "permanent_damage_boost":
        amount = _effect_amount(effect, "amount", 0)
        _append_active_effect(
            getattr(owner, "active_effects"),
            target_id,
            "permanent_damage_boost",
            target_id,
            amount=amount,
            source=attack_name,
        )
        owner._append_effect_event(effect_events, _effect_source_text(attack_name, f"Dauerhafte Schadenssteigerung: +{amount} Schaden (stapelbar)."))
        return True
    if eff_type == "incoming_damage_multiplier":
        multiplier = max(0.0, float(_maybe_float(effect.get("multiplier")) or 1.0))
        uses = max(1, int(_maybe_int(effect.get("uses")) or 1))
        _append_active_effect(
            getattr(owner, "active_effects"),
            target_id,
            "incoming_damage_multiplier",
            target_id,
            multiplier=multiplier,
            uses=uses,
            source=attack_name,
        )
        pct = int(round((multiplier - 1.0) * 100))
        owner._append_effect_event(effect_events, _effect_source_text(attack_name, f"Systemüberhitzung: Nächster erhaltener Schaden +{pct}%."))
        return True
    if eff_type == "next_attack_damage_override":
        uses = max(1, int(effect.get("uses", 1) or 1))
        _append_active_effect(
            getattr(owner, "active_effects"),
            target_id,
            "next_attack_damage_override",
            target_id,
            damage=effect.get("damage", 0),
            uses=uses,
            source=attack_name,
        )
        owner._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff wird auf {_effect_amount_label(effect.get('damage', 0))} Schaden gesetzt."))
        return True
    if eff_type == "maestro_artifact":
        if random.random() < 0.5:
            owner.queue_incoming_modifier(target_id, flat=20, turns=1, source=attack_name)
            owner._append_effect_event(effect_events, _effect_source_text(attack_name, "Trophäe: Schild blockt beim nächsten Treffer 20 Schaden."))
        else:
            bonus_effect = dict(effect)
            bonus_amount = max(0, _effect_amount(bonus_effect, "amount", 15))
            bonus_uses = max(1, _effect_int(bonus_effect, "uses", 1))
            _queue_flat_damage_boost(
                owner,
                effect_events,
                target_id=target_id,
                applier_id=target_id,
                attack_name=attack_name,
                amount=bonus_amount,
                uses=bonus_uses,
                effect=bonus_effect,
            )
        return True
    return False


def _permanent_damage_boost_amount(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> int:
    return sum(
        max(0, _effect_int(effect, "amount", 0))
        for effect in _active_effect_entries(active_effects, player_id, "permanent_damage_boost")
    )


def _consume_next_attack_damage_override(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    current_damage: int,
) -> tuple[int, dict[str, object] | None]:
    effect = _find_active_effect(active_effects, player_id, "next_attack_damage_override")
    if effect is None:
        return max(0, int(current_damage or 0)), None
    new_damage = _random_int_from_range(effect.get("damage", 0), default=0)
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return max(0, int(new_damage)), effect


def _consume_incoming_damage_multiplier(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    current_damage: int,
) -> tuple[int, dict[str, object] | None]:
    effect = _find_active_effect(active_effects, player_id, "incoming_damage_multiplier")
    if effect is None:
        return max(0, int(current_damage or 0)), None
    multiplier = max(0.0, float(effect.get("multiplier", 1.0) or 1.0))
    new_damage = int(round(max(0, int(current_damage or 0)) * multiplier))
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return max(0, int(new_damage)), effect


def _force_min_damage_active(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> bool:
    return _find_active_effect(active_effects, player_id, "enemy_force_min_damage") is not None


def _consume_force_min_damage(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> bool:
    effect = _find_active_effect(active_effects, player_id, "enemy_force_min_damage")
    if effect is None:
        return False
    turns_left = max(0, _effect_int(effect, "turns", 1) - 1)
    effect["turns"] = turns_left
    if turns_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return True


def _incoming_damage_bonus(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> int:
    return sum(max(0, _effect_int(effect, "amount", 0)) for effect in _active_effect_entries(active_effects, player_id, "incoming_damage_bonus"))


def _shield_entry(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> dict[str, object] | None:
    return _find_active_effect(active_effects, player_id, "shield")


def _consume_shield_damage(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    damage: int,
) -> tuple[int, int]:
    shield = _shield_entry(active_effects, player_id)
    if shield is None:
        return max(0, int(damage or 0)), 0
    shield_hp = max(0, _effect_int(shield, "hp", 0))
    incoming = max(0, int(damage or 0))
    absorbed = min(shield_hp, incoming)
    shield["hp"] = max(0, shield_hp - absorbed)
    hits_left = shield.get("max_hits")
    if hits_left is not None:
        resolved_hits_left = _maybe_int(hits_left) or 0
        shield["max_hits"] = max(0, resolved_hits_left - 1)
    shield_hp_left = _effect_int(shield, "hp", 0)
    shield_hits_left = _effect_int(shield, "max_hits", 1)
    broke = shield_hp_left <= 0 or shield_hits_left <= 0
    break_counter = 0
    if broke:
        break_counter = max(0, _effect_int(shield, "break_counter", 0))
        _remove_active_effect(active_effects, player_id, shield)
    return max(0, incoming - absorbed), break_counter


def _shield_has_stun_immunity(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> bool:
    shield = _shield_entry(active_effects, player_id)
    return bool(shield and shield.get("stun_immunity"))


def _consume_burn_multiplier(active_effects: dict[int, list[dict[str, object]]], player_id: int) -> float:
    effect = _find_active_effect(active_effects, player_id, "burn_multiplier")
    if effect is None:
        return 1.0
    multiplier = max(1.0, float(_maybe_float(effect.get("multiplier")) or 1.0))
    uses_left = max(0, _effect_int(effect, "uses", 1) - 1)
    effect["uses"] = uses_left
    if uses_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return multiplier


def _sum_target_dot_damage(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    dot_type: str,
) -> int:
    effect_key = str(dot_type or "").strip().lower()
    return sum(
        max(0, _effect_int(effect, "damage", 0))
        for effect in active_effects.get(player_id, [])
        if str(effect.get("type") or "").strip().lower() == effect_key
    )


def _apply_reactive_evolution_reduction(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    damage: int,
) -> tuple[int, int]:
    effect = _find_active_effect(active_effects, player_id, "reactive_evolution")
    if effect is None:
        return max(0, int(damage or 0)), 0
    current_reduction = max(0, _effect_int(effect, "stacks", 0))
    reduced_damage = max(0, int(damage or 0) - current_reduction)
    amount = max(0, _effect_int(effect, "amount", 0))
    max_stacks = max(1, _effect_int(effect, "max_stacks", 1))
    effect["stacks"] = min(max_stacks * amount, current_reduction + amount)
    return reduced_damage, current_reduction


def _record_last_special_attack(
    last_special_attack: dict[int, dict[str, object] | None],
    *,
    actor_id: int,
    attack_index: int,
    attacks: list[dict],
    attack: dict,
    card_name: str,
    attack_name: str,
    is_reload_action: bool,
    is_forced_landing: bool,
) -> None:
    if is_reload_action or is_forced_landing:
        return
    if attack_index < 0:
        return
    if attack_index == _standard_attack_index(attacks):
        return
    last_special_attack[actor_id] = {
        "attack_index": int(attack_index),
        "card_name": str(card_name or ""),
        "attack_name": str(attack_name or ""),
        "attack": _json_clone(attack),
    }


def _copied_attack_from_history(entry: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    attack = entry.get("attack")
    if not isinstance(attack, dict):
        return None
    disallowed = {
        "copy_last_enemy_special",
        "increase_last_enemy_special_cooldown",
        "increase_random_enemy_cooldown",
        "interrupt_enemy_standard_or_heal_self",
        "reset_own_cooldown",
    }
    for effect in attack.get("effects", []):
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect_type in disallowed:
            return None
    return cast(dict[str, object], _json_clone(attack))


def _consume_attack_self_damage_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    *,
    special_attack: bool,
) -> int:
    candidates: list[dict[str, object]] = []
    candidates.extend(_active_effect_entries(active_effects, player_id, "enemy_attack_self_damage"))
    if special_attack:
        candidates.extend(_active_effect_entries(active_effects, player_id, "enemy_special_self_damage"))
        candidates.extend(_active_effect_entries(active_effects, player_id, "enemy_next_special_self_damage"))
    if not candidates:
        return 0
    effect = candidates[0]
    amount = max(0, _effect_int(effect, "amount", 0))
    turns_left = max(0, _effect_int(effect, "turns", 1) - 1)
    effect["turns"] = turns_left
    if turns_left <= 0:
        _remove_active_effect(active_effects, player_id, effect)
    return amount


async def _require_optional_attack_confirmation(
    view: object,
    interaction: discord.Interaction,
    *,
    player_id: int,
    attack_index: int,
    attack_name: str,
    reason_text: str,
) -> bool:
    confirmations = getattr(view, "_optional_attack_confirmations", None)
    if not isinstance(confirmations, dict):
        confirmations = {}
        setattr(view, "_optional_attack_confirmations", confirmations)
    current = confirmations.get(player_id)
    if isinstance(current, dict):
        same_attack = int(current.get("attack_index", -1) or -1) == int(attack_index)
        same_name = str(current.get("attack_name") or "") == str(attack_name or "")
        if same_attack and same_name:
            confirmations.pop(player_id, None)
            return True
    confirmations[player_id] = {"attack_index": int(attack_index), "attack_name": str(attack_name or "")}
    await _safe_send_interaction_ephemeral(
        interaction,
        f"{reason_text} Drücke `{attack_name}` noch einmal, um nur den Basisteil auszuführen, oder wähle eine andere Attacke.",
    )
    return False


def _pick_resettable_cooldown_index(cooldown_map: dict[int, int], *, exclude_index: int | None = None) -> int | None:
    candidates = [
        (int(idx), int(turns or 0))
        for idx, turns in cooldown_map.items()
        if int(turns or 0) > 0 and (exclude_index is None or int(idx) != int(exclude_index))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return candidates[0][0]


def _apply_random_enemy_cooldown_increase(
    attacks: list[dict],
    cooldown_map: dict[int, int],
    *,
    amount: int,
) -> tuple[int | None, int]:
    standard_idx = _standard_attack_index(attacks)
    candidates = [idx for idx in range(min(4, len(attacks))) if idx != standard_idx]
    if not candidates:
        return None, 0
    chosen_idx = random.choice(candidates)
    bonus = max(1, int(amount or 1))
    cooldown_map[chosen_idx] = max(0, int(cooldown_map.get(chosen_idx, 0) or 0)) + bonus
    return chosen_idx, cooldown_map[chosen_idx]


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


async def add_infinitydust(user_id: int, amount: int = 1) -> None:
    await _add_infinitydust(user_id, amount)


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


ALPHA_PHASE_ENABLED = False
ALPHA_HIDDEN_SLASH_COMMANDS = ("mission", "geschichte")
ALPHA_FEATURE_DISABLED_TEXT = game_ui_texts.ALPHA_FEATURE_DISABLED_TEXT
BETA_STORY_DISABLED_TEXT = game_ui_texts.BETA_STORY_DISABLED_TEXT
BETA_INVITE_DISABLED_TEXT = game_ui_texts.BETA_INVITE_DISABLED_TEXT


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
        elif command_name == "konfigurieren hinzufügen":
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
        if command_name:
            await _log_event_safe(
                "command_used",
                guild_id=guild_id,
                channel_id=interaction.channel_id,
                thread_id=_thread_id_for_channel(interaction.channel),
                actor_user_id=getattr(interaction.user, "id", 0),
                command_name=command_name,
                payload={
                    "channel_parent_id": int(getattr(interaction.channel, "parent_id", 0) or 0),
                    "alpha_phase": bool(ALPHA_PHASE_ENABLED),
                },
            )
        return True


bot = build_bot(tree_cls=KatabumpCommandTree)

def create_bot() -> commands.Bot:
    return bot

ADMIN_SLASH_COMMANDS = {
    "konfigurieren",
    "intro-zurücksetzen",
    "sammlung-ansehen",
    "test-bericht",
    "karte-geben",
    "stats_e",
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
    return []


# Rollen-IDs für Admin/Owner (vom Nutzer bestätigt)
BASTI_USER_ID = 965593518745731152
DEV_ROLE_ID = 1463304167421513961  # Bot_Developer/Tester role ID

MFU_ADMIN_ROLE_ID = 889559991437119498
OWNER_ROLE_ROLE_ID = 1272827906032402464

BUG_REPORT_TALLY_URL = os.getenv("BUG_REPORT_TALLY_URL", "https://tally.so/r/7RNo8z")
BOT_STATUS_KEY = "presence_status"
RESET_BUFFS_MIGRATION_KEY = "migration_reset_buffs_2026_02_21"
INVALID_DAMAGE_BUFFS_MIGRATION_KEY = "migration_remove_invalid_damage_buffs_2026_03_17"
MAX_ATTACK_DAMAGE_PER_HIT = DIRECT_DAMAGE_CAP
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
        "attack_heal",
        "blind",
        "burning",
        "bleeding",
        "cap_damage",
        "capped_damage_multiplier",
        "clear_negative_effects",
        "damage_boost",
        "damage_reduction",
        "damage_reduction_flat",
        "damage_multiplier",
        "damage_reduction_sequence",
        "delayed_defense_after_next_attack",
        "enemy_next_attack_reduction_flat",
        "enemy_next_attack_reduction_percent",
        "enemy_attack_self_damage",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "evade",
        "finisher_below_hp",
        "force_max",
        "guaranteed_hit",
        "heal_curse",
        "heal_from_target_dot",
        "incoming_damage_bonus",
        "increase_last_enemy_special_cooldown",
        "increase_random_enemy_cooldown",
        "interrupt_enemy_standard_or_heal_self",
        "mix_heal_or_max",
        "next_attack_flat_penalty",
        "next_attack_damage_override",
        "next_standard_damage_override",
        "poison",
        "permanent_damage_boost",
        "reflect",
        "regen",
        "random_pym_debuff",
        "maestro_artifact",
        "reset_own_cooldown",
        "shield",
        "special_lock",
        "standard_lock",
        "status_immunity",
        "stun",
        "burn_multiplier",
        "copy_last_enemy_special",
        "disable_enemy_evade_and_block",
        "disable_enemy_heal_if_bleeding",
        "reactive_evolution",
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
        await _log_event_safe(
            "lifecycle_presence_restored",
            command_name="bot_status",
            payload={"status": status_key},
        )
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


def build_anfang_intro_text(*, alpha_enabled: bool = False, beta_enabled: bool = False) -> str:
    text = (
        "# **Rekrut.**\n\n"
        "Hör gut zu. Ich bin Nick Fury, und wenn du Teil von etwas Größerem sein willst, bist du hier richtig. Willkommen auf dem Helicarrier. Wir haben alle Hände voll zu tun, und ich hoffe, du bist bereit, dir die Hände schmutzig zu machen.\n\n"
        "Du willst wissen, wie du an die guten Sachen kommst? Täglich hast du die Chance, eine zufällige Karte aus dem Pool zu ziehen `[/täglich im Chat schreiben]`. Und wenn du eine doppelte Karte ziehst, verschwindet sie nicht einfach. Sie wird zu Staub umgewandelt. Sammle genug davon, um deine Karten zu verbessern und sie so noch mächtiger zu machen `[/verbessern im Chat schreiben]`.\n\n"
        "Du bist neu hier und brauchst Training? Auf dem Helicarrier kannst du dich mit anderen anlegen und üben, bis deine Strategien sitzen `[/kampf im Chat schreiben]`.\n\n"
    )
    if not alpha_enabled:
        text += (
            "Wenn du bereit für den echten Einsatz bist, stehen dir jeden Tag zwei Missionen zur Verfügung. Schließe sie ab und ich garantiere dir, du bekommst jeweils eine Karte als Belohnung `[/mission im Chat schreiben]`.\n\n"
        )
    if not alpha_enabled and not beta_enabled:
        text += (
            "Für die Verrückten da draußen, die meinen, sie wären unschlagbar: Es gibt den Story-Modus. Du hast drei Leben, um die gesamte Geschichte zu überleben. Schaffst du das, wartet eine mysteriöse Belohnung auf dich `[/geschichte im Chat schreiben]`.\n\n"
        )
    text += (
        "Wurdest du von jemandem eingeladen? Dann bestätige das mit `[/eingeladen im Chat schreiben]`, "
        "damit Belohnungen korrekt verteilt werden.\n\n"
    )
    text += "**Also los jetzt. Sag mir, was du tun willst. Wir haben keine Zeit zu verlieren.**"
    return text


async def _send_alpha_feature_blocked(interaction: discord.Interaction) -> None:
    await _send_ephemeral(interaction, content=ALPHA_FEATURE_DISABLED_TEXT)


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


def _describe_direct_heal_amount(value: object) -> str | None:
    if isinstance(value, list) and len(value) == 2:
        min_value = _maybe_int(value[0])
        max_value = _maybe_int(value[1])
        if min_value is None or max_value is None:
            return None
        return f"Heilt {min_value}-{max_value} HP"
    parsed = _maybe_int(value)
    if parsed is None:
        return None
    return f"Heilt bis zu {parsed} HP"


def _describe_regen_amount(value: object, *, turns: int) -> str | None:
    round_label = "Runde" if turns == 1 else "Runden"
    if isinstance(value, list) and len(value) == 2:
        min_value = _maybe_int(value[0])
        max_value = _maybe_int(value[1])
        if min_value is None or max_value is None:
            return None
        return f"Heilt {min_value}-{max_value} HP für {turns} {round_label}"
    parsed = _maybe_int(value)
    if parsed is None:
        return None
    return f"Heilt {parsed} HP für {turns} {round_label}"


def _heal_label_for_attack(attack: dict) -> str | None:
    heal_data = attack.get("heal")
    heal_text = _describe_direct_heal_amount(heal_data)
    if heal_text:
        return heal_text

    lifesteal_ratio = attack.get("lifesteal_ratio")
    if lifesteal_ratio:
        try:
            pct = int(round(float(lifesteal_ratio) * 100))
        except Exception:
            pct = 0
        if pct > 0:
            return f"{pct}% Lebensraub"

    for effect in attack.get("effects", []):
        effect_type = effect.get("type")
        if effect_type == "regen":
            turns = max(1, _maybe_int(effect.get("turns", 1) or 1) or 1)
            regen_text = _describe_regen_amount(effect.get("heal"), turns=turns)
            if regen_text:
                return regen_text
        elif effect_type == "heal":
            direct_heal = _describe_direct_heal_amount(effect.get("amount"))
            if direct_heal:
                return direct_heal
        elif effect_type == "mix_heal_or_max":
            mix_heal = _describe_direct_heal_amount(effect.get("heal"))
            if mix_heal:
                return f"{mix_heal} oder Maximalschaden"
            return "Heilung oder Maximalschaden"
    return None


def _utility_label_for_attack(attack: dict) -> str | None:
    effect_types: list[str] = []
    for effect in attack.get("effects", []):
        eff_type = str(effect.get("type") or "").strip().lower()
        if eff_type:
            effect_types.append(eff_type)

    # Prefer concise, user-facing labels for non-damage actions.
    labels: list[str] = []
    for eff_type in effect_types:
        if eff_type in {"force_max", "guaranteed_hit"}:
            if "Maximalschaden" not in labels:
                labels.append("Maximalschaden")
        elif eff_type == "evade":
            if "Ausweichen" not in labels:
                labels.append("Ausweichen")
        elif eff_type == "reflect":
            if "Reflektieren" not in labels:
                labels.append("Reflektieren")
        elif eff_type == "shield":
            if "Schild" not in labels:
                labels.append("Schild")
        elif eff_type in {"clear_negative_effects", "status_immunity"}:
            if "Reinigen/Immun" not in labels:
                labels.append("Reinigen/Immun")
        elif eff_type in {"stun", "blind", "special_lock", "standard_lock"}:
            if "Kontrolle" not in labels:
                labels.append("Kontrolle")
        elif eff_type in {"increase_last_enemy_special_cooldown", "increase_random_enemy_cooldown"}:
            if "Cooldown" not in labels:
                labels.append("Cooldown")

    if labels:
        return ", ".join(labels[:2])
    if effect_types:
        return "Effekt"
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
    if _resolve_self_damage_value(attack.get("self_damage", 0)) > 0:
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


def _resolve_self_damage_value(raw_value: object) -> int:
    if isinstance(raw_value, list) and len(raw_value) == 2:
        min_damage = max(0, int(_maybe_int(raw_value[0]) or 0))
        max_damage = max(min_damage, int(_maybe_int(raw_value[1]) or 0))
        if max_damage <= min_damage:
            return min_damage
        return random.randint(min_damage, max_damage)
    return max(0, int(_maybe_int(raw_value) or 0))


class _SupportsMixHealOrMax(Protocol):
    force_max_next: dict[int, int]

    def _hp_for(self, player_id: int) -> int: ...

    def _max_hp_for(self, player_id: int) -> int: ...

    def heal_player(self, player_id: int, amount: int) -> int: ...

    def _append_effect_event(self, events: list[str], text: str) -> None: ...


class _WordRuntimeOwner(Protocol):
    active_effects: dict[int, list[dict[str, object]]]
    pending_flat_bonus: dict[int, int]
    pending_flat_bonus_uses: dict[int, int]
    special_lock_next_turn: dict[int, int]
    blind_next_attack: dict[int, float]

    def _append_effect_event(self, events: list[str], text: str) -> None: ...

    def queue_outgoing_attack_modifier(self, target_id: int, *, flat: int, turns: int, source: str | None = None) -> None: ...

    def queue_incoming_modifier(self, target_id: int, *, flat: int, turns: int, source: str | None = None) -> None: ...


def _apply_mix_heal_or_max_effect(
    owner: _SupportsMixHealOrMax,
    target_id: int,
    effect: dict,
    effect_events: list[str],
) -> None:
    heal_amount = _random_int_from_range(effect.get("heal", 0))
    can_heal = heal_amount > 0 and owner._hp_for(target_id) < owner._max_hp_for(target_id)
    if can_heal and random.random() < 0.5:
        healed_mix = owner.heal_player(target_id, heal_amount)
        owner._append_effect_event(effect_events, f"Awesome Mix: +{healed_mix} HP.")
        return
    owner.force_max_next[target_id] = max(owner.force_max_next.get(target_id, 0), 1)
    owner._append_effect_event(effect_events, "Awesome Mix: Nächster Angriff verursacht Maximalschaden.")


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


def _attack_total_damage_range(attack: dict, *, max_only_bonus: int = 0, flat_bonus: int = 0) -> tuple[int, int]:
    multi_hit = attack.get("multi_hit")
    if isinstance(multi_hit, dict):
        hits = max(0, int(_maybe_int(multi_hit.get("hits")) or 0))
        per_hit = multi_hit.get("per_hit_damage", [0, 0])
        if isinstance(per_hit, list) and len(per_hit) == 2:
            hit_min = max(0, int(_maybe_int(per_hit[0]) or 0))
            hit_max = max(hit_min, int(_maybe_int(per_hit[1]) or 0))
        else:
            hit_min = 0
            hit_max = 0
        hit_chance = float(multi_hit.get("hit_chance", 0.0) or 0.0)
        guaranteed_hits = hits if hit_chance >= 1.0 else 0
        min_damage = guaranteed_hits * hit_min + int(flat_bonus or 0)
        max_damage = hits * hit_max + int(flat_bonus or 0) + int(max_only_bonus or 0)
        min_damage = max(0, int(min_damage))
        max_damage = max(min_damage, int(max_damage))
        return min_damage, max_damage
    return _damage_range_with_max_bonus(
        attack.get("damage", [0, 0]),
        max_only_bonus=max_only_bonus,
        flat_bonus=flat_bonus,
    )


def _attack_has_direct_damage(attack: dict) -> bool:
    _min_damage, max_damage = _attack_total_damage_range(attack, max_only_bonus=0, flat_bonus=0)
    return max_damage > 0


def _attack_allowed_at_self_hp(attack: dict, hp: int, max_hp: int) -> bool:
    pct = _maybe_float(attack.get("conditional_self_hp_below_pct"))
    if pct is None:
        return True
    safe_max_hp = max(1, int(max_hp or 0))
    return int(hp or 0) <= int(safe_max_hp * pct)


def _standard_attack_index(attacks: list[dict]) -> int:
    for idx, attack in enumerate(attacks[:4]):
        if bool(attack.get("is_standard_attack")):
            return idx
    return 0


def _is_standard_attack(attacks: list[dict], attack_index: int) -> bool:
    if attack_index < 0:
        return False
    return _standard_attack_index(attacks) == int(attack_index)


def _pending_landing_slot_index(pending_landing: dict[str, object] | None) -> int:
    if not isinstance(pending_landing, dict):
        return 0
    attack_data = pending_landing.get("attack")
    if isinstance(attack_data, dict):
        raw_index = attack_data.get("cooldown_attack_index")
        parsed_index = _maybe_int(raw_index)
        if parsed_index is not None and 0 <= parsed_index < 4:
            return int(parsed_index)
    return 0


def _readable_effect_source(source: object | None) -> str:
    source_name = str(source or "").strip()
    if not source_name:
        return ""
    if source_name.lower() == "airborne":
        return "Flugphase"
    return source_name


def _attack_kind_label(
    attack: dict,
    *,
    attacks: list[dict],
    attack_index: int,
    is_reload_action: bool = False,
    is_forced_landing: bool = False,
) -> str:
    if is_reload_action:
        return "Nachladen"
    if is_forced_landing:
        return "Folgeangriff"
    if _is_standard_attack(attacks, attack_index):
        return "Standardangriff"
    if _attack_has_heal_component(attack) and not _attack_has_direct_damage(attack):
        return "Heilfähigkeit"
    return "Fähigkeit"


def _extract_heal_amount_from_events(effect_events: list[str] | None) -> int:
    total_heal = 0
    for event in effect_events or []:
        text = str(event or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith("regeneration aktiviert:"):
            continue
        if not (
            lowered.startswith("heilung:")
            or lowered.startswith("heileffekt:")
            or lowered.startswith("lebensraub:")
            or lowered.startswith("regeneration heilt")
            or lowered.startswith("awesome mix:")
        ):
            continue
        for match in re.findall(r"\+(\d+)\s*HP", text, flags=re.IGNORECASE):
            try:
                total_heal += int(match)
            except Exception:
                continue
    return max(0, total_heal)


def _prepend_action_context_events(
    effect_events: list[str],
    *,
    action_type: str,
    actual_damage: int,
    miss_reason: str | None = None,
    heal_amount: int = 0,
    is_reload_action: bool = False,
) -> None:
    action_text = str(action_type or "").strip()
    outcome_text = ""
    if miss_reason:
        outcome_text = f"verfehlt {miss_reason}"
    elif heal_amount > 0 and int(actual_damage or 0) <= 0:
        outcome_text = f"erfolgreich geheilt (+{heal_amount} HP)"
    elif is_reload_action:
        outcome_text = "ohne direkten Schaden nachgeladen"
    elif int(actual_damage or 0) > 0:
        outcome_text = "normal getroffen"
    else:
        outcome_text = "erfolgreich ohne direkten Schaden eingesetzt"
    if outcome_text:
        effect_events.insert(0, f"Ausführung: {outcome_text}.")
    if action_text:
        effect_events.insert(0, f"Aktionstyp: {action_text}.")


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


def _attack_effect_icons(attack: dict) -> list[str]:
    effect_icons: list[str] = []
    for eff in attack.get("effects", []):
        eff_type = str(eff.get("type") or "").strip().lower()
        if eff_type == "burning":
            if "🔥" not in effect_icons:
                effect_icons.append("🔥")
        elif eff_type == "poison":
            if "☠️" not in effect_icons:
                effect_icons.append("☠️")
        elif eff_type == "bleeding":
            if "🩸" not in effect_icons:
                effect_icons.append("🩸")
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
        elif eff_type in {"damage_boost", "damage_multiplier", "permanent_damage_boost"}:
            if "⬆️" not in effect_icons:
                effect_icons.append("⬆️")
        elif eff_type in {"force_max", "mix_heal_or_max", "guaranteed_hit"}:
            if "🎯" not in effect_icons:
                effect_icons.append("🎯")
        elif eff_type in {"heal", "regen"}:
            if "❤️" not in effect_icons:
                effect_icons.append("❤️")
    heal_label = _heal_label_for_attack(attack)
    if heal_label and "❤️" not in effect_icons:
        effect_icons.append("❤️")
    return effect_icons


def _attack_effects_label(attack: dict) -> str:
    effect_icons = _attack_effect_icons(attack)
    return f" {' '.join(effect_icons)}" if effect_icons else ""


def _attack_display_parts(attack: dict, *, max_only_bonus: int = 0) -> tuple[str, discord.ButtonStyle, str]:
    attack_name = str(attack.get("name") or "Attacke")
    effects_label = _attack_effects_label(attack)
    heal_label = _heal_label_for_attack(attack)
    if heal_label is not None:
        label = f"{attack_name} ({heal_label}){effects_label}"
        style = _resolve_attack_button_style(attack, discord.ButtonStyle.success)
        summary = f"{attack_name} — {heal_label}{effects_label}"
        return label, style, summary
    min_dmg, max_dmg = _attack_total_damage_range(attack, max_only_bonus=max_only_bonus, flat_bonus=0)
    if min_dmg == 0 and max_dmg == 0:
        utility = _utility_label_for_attack(attack) or "Effekt"
        label = f"{attack_name} ({utility}){effects_label}"
        style = _resolve_attack_button_style(attack, discord.ButtonStyle.secondary)
        summary = f"{attack_name} — {utility}{effects_label}"
        return label, style, summary
    damage_text = f"{min_dmg}-{max_dmg}"
    buff_text = f" (+{max_only_bonus} max)" if max_only_bonus > 0 else ""
    label = f"{attack_name} ({damage_text}{buff_text}){effects_label}"
    style = _resolve_attack_button_style(attack, discord.ButtonStyle.danger)
    summary = f"{attack_name} — {damage_text} Schaden{buff_text}{effects_label}"
    return label, style, summary


def _format_passive_preview_line(passive: dict) -> str | None:
    if not isinstance(passive, dict):
        return None
    passive_type = str(passive.get("type") or "").strip().lower()
    source = str(passive.get("source") or "").strip() or "Passive Fähigkeit"
    if passive_type == "on_hit_recoil":
        damage = int(passive.get("damage", 0) or 0)
        return f"• {source}: +{damage} Schaden bei Treffer"
    info_text = str(passive.get("info") or "").strip()
    if info_text:
        return f"• {source}: {info_text}"
    return f"• {source}"


def _build_attack_info_lines(card: dict, *, max_attacks: int = 4, include_passives: bool = False) -> list[str]:
    lines: list[str] = []
    attacks = card.get("attacks", [])
    for attack in attacks[:max_attacks]:
        _label, _style, attack_summary = _attack_display_parts(attack)
        # Req. 14.1/14.4: In der Fähigkeiten-Vorschau das Cooldown-Suffix „(<n>CD)"
        # an verfügbare Fähigkeiten mit konfiguriertem Cooldown anhängen.
        cd_suffix = _format_attack_label(attack, is_on_cooldown=False)
        if isinstance(attack, dict) and cd_suffix.endswith("CD}"):
            suffix = cd_suffix[len(str(attack.get("name") or "")):].strip()
            if suffix:
                attack_summary = f"{attack_summary} {suffix}"
        info_text = str(attack.get("info") or "").strip()
        if info_text:
            lines.append(f"• {attack_summary}: {info_text}")
        else:
            lines.append(f"• {attack_summary}")
    if include_passives:
        for passive in card.get("passives", []) or []:
            passive_line = _format_passive_preview_line(passive)
            if passive_line:
                lines.append(passive_line)
    return lines


def _add_attack_info_field(
    embed: discord.Embed,
    card: dict,
    *,
    field_name: str = "Fähigkeiten",
    include_passives: bool = False,
) -> None:
    lines = _build_attack_info_lines(card, include_passives=include_passives)
    if not lines:
        return
    value = "\n".join(lines)
    if len(value) > 1024:
        value = value[:1021] + "..."
    embed.add_field(name=field_name, value=value, inline=False)


def _build_upgrade_preview_lines(
    card: dict,
    buffs: list[tuple[str, int, int]],
    *,
    max_attacks: int = 4,
) -> list[str]:
    total_health, damage_map = battle_state.summarize_card_buffs(buffs)
    base_hp = int(card.get("hp", 100) or 100)
    lines = [f"❤️ Leben aktuell: **{base_hp + total_health} HP**"]
    attacks = card.get("attacks", [])
    for idx, attack in enumerate(attacks[:max_attacks], start=1):
        _label, _style, attack_summary = _attack_display_parts(
            attack,
            max_only_bonus=damage_map.get(idx, 0),
        )
        lines.append(f"⚔️ {attack_summary}")
    return lines


def _build_fuse_card_select_embed(
    dust_amount: int,
    *,
    title: str = "🎯 Karte auswählen",
    guidance: str = "Wähle die Karte, die du verstärken möchtest:",
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=(
            f"Du verwendest **{dust_amount} Infinitydust**.\n"
            f"❤️ Leben: **+{FUSE_HEALTH_BONUS}** (max. {FUSE_HP_CAP})\n"
            f"⚔️ Standard: **{STANDARD_DAMAGE_UPGRADE_MAX_TIMES}x +{STANDARD_DAMAGE_UPGRADE_STEP} Max-Schaden**\n"
            f"⚔️ Spezial: **{SPECIAL_DAMAGE_UPGRADE_MAX_TIMES}x +{SPECIAL_DAMAGE_UPGRADE_STEP} Max-Schaden**\n\n"
            f"{guidance}"
        ),
        color=0x9D4EDD,
    )
    _apply_item_media(embed, "infinitydust", thumbnail=True)
    return embed


def _build_fuse_buff_type_embed(
    selected_card: str,
    karte_data: dict[str, Any],
    user_buffs: list[tuple[str, int, int]],
) -> discord.Embed:
    current_values = "\n".join(_build_upgrade_preview_lines(karte_data, user_buffs))
    embed = discord.Embed(
        title="⚡ Verstärkung wählen",
        description=(
            f"Karte: **{selected_card}**\n\n"
            "Was möchtest du verstärken?"
        ),
        color=0x9D4EDD,
    )
    embed.add_field(name="Aktuelle Werte", value=current_values[:1024], inline=False)
    embed.add_field(
        name="Nächste Verstärkung",
        value=(
            f"❤️ Leben: **+{FUSE_HEALTH_BONUS}**\n"
            f"⚔️ Standard: **+{STANDARD_DAMAGE_UPGRADE_STEP} Max-Schaden** pro Upgrade "
            f"(max. {STANDARD_DAMAGE_UPGRADE_MAX_TIMES}x)\n"
            f"⚔️ Spezial: **+{SPECIAL_DAMAGE_UPGRADE_STEP} Max-Schaden** pro Upgrade "
            f"(max. {SPECIAL_DAMAGE_UPGRADE_MAX_TIMES}x)\n"
            "min bleibt gleich"
        ),
        inline=False,
    )
    _apply_item_media(embed, "infinitydust", thumbnail=True)
    return embed


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


def _resolve_final_damage_cooldown_turns(attack: dict, actual_damage: int) -> int:
    try:
        cooldown_turns = max(0, int(attack.get("cooldown_turns", 0) or 0))
    except Exception:
        return 0
    if cooldown_turns <= 0:
        return 0
    try:
        dealt_damage = max(0, int(actual_damage or 0))
    except Exception:
        dealt_damage = 0
    overrides = attack.get("cooldown_overrides_by_final_damage", [])
    if not isinstance(overrides, list):
        return cooldown_turns
    resolved_turns = cooldown_turns
    highest_matching_threshold = -1
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        try:
            threshold = int(entry.get("threshold", 0) or 0)
            turns = int(entry.get("turns", 0) or 0)
        except Exception:
            continue
        if threshold <= dealt_damage and turns > 0 and threshold >= highest_matching_threshold:
            highest_matching_threshold = threshold
            resolved_turns = turns
    return max(0, resolved_turns)


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


def _extract_boost_breakdown(effect_events: list[str] | None) -> dict[str, int]:
    for entry in effect_events or []:
        text = str(entry or "").strip()
        match = re.search(
            r"Normal:\s*(\d+)\s*\|\s*durch Verstärkung:\s*(\d+)\s*\(\+(\d+)\)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return {
                "base_damage": int(match.group(1)),
                "boosted_damage": int(match.group(2)),
                "boost_bonus": int(match.group(3)),
            }
    return {"base_damage": 0, "boosted_damage": 0, "boost_bonus": 0}


def _damage_breakdown_payload(
    *,
    actual_damage: int,
    pre_effect_damage: int,
    effect_events: list[str] | None,
    self_hit_damage: int = 0,
) -> dict[str, int]:
    boost = _extract_boost_breakdown(effect_events)
    final_damage = max(0, int(actual_damage or 0))
    burn_damage = max(0, int(pre_effect_damage or 0))
    boost_bonus = 0
    direct_damage = final_damage
    if boost["boosted_damage"] == final_damage and boost["boost_bonus"] > 0:
        direct_damage = max(0, int(boost["base_damage"]))
        boost_bonus = max(0, int(boost["boost_bonus"]))
    return {
        "direct_damage": direct_damage,
        "boost_bonus": boost_bonus,
        "pre_effect_damage": burn_damage,
        "final_damage": final_damage,
        "total_damage_to_defender": max(0, direct_damage + boost_bonus + burn_damage),
        "self_hit_damage": max(0, int(self_hit_damage or 0)),
    }

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
    await _log_event_safe(
        "lifecycle_ready",
        command_name="startup",
        payload={
            "alpha_phase": bool(ALPHA_PHASE_ENABLED),
            "guild_count": len(bot.guilds),
            "user": str(bot.user or ""),
        },
    )
    try:
        synced = await bot.tree.sync()
        logging.info("Slash-Commands synchronisiert: %s", len(synced))
    except Exception:
        logging.exception("Slash-Command sync failed")
    global _persistent_views_registered
    if not _persistent_views_registered:
        try:
            bot.add_view(AnfangView())
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
    # Req. 13: AFK-Ticker starten (idempotent; nur einmal pro Prozess).
    global _afk_loop_task
    if _afk_loop_task is None or _afk_loop_task.done():
        _afk_loop_task = asyncio.create_task(afk_tracker_loop())


_afk_loop_task: "asyncio.Task | None" = None


async def afk_tracker_loop() -> None:
    """Lädt alle 5 Minuten alle aktiven AFK-Zustände und ruft ``tick`` auf (Req. 13.1-13.5)."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            states = await afk_tracker.restore_all_states()
            now = int(time.time())
            for state in states:
                try:
                    await afk_tracker.tick(bot, state, now)
                except Exception:
                    logging.exception("AFK-Tick fehlgeschlagen (battle=%s)", getattr(state, "battle_id", "?"))
        except Exception:
            logging.exception("AFK-Tracker-Loop-Durchlauf fehlgeschlagen")
        await asyncio.sleep(300)


@bot.event
async def on_disconnect():
    await _log_event_safe("lifecycle_disconnect", command_name="gateway")


@bot.event
async def on_resumed():
    await _log_event_safe("lifecycle_resumed", command_name="gateway")

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
    _is_managed = isinstance(message.channel, discord.Thread) and await is_managed_thread(message.channel.id)
    if _is_managed:
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

    # Intro-Prompt nur in normalen Kanälen anzeigen – niemals in Threads (Mission/PVP),
    # auch wenn ein Thread (noch) nicht als "managed" registriert ist.
    if not _is_managed and not isinstance(message.channel, discord.Thread):
        try:
            async with db_context() as db:
                cursor = await db.execute(
                    "SELECT 1 FROM user_seen_channels WHERE user_id = ? AND guild_id = ? AND channel_id = ?",
                    (message.author.id, message.guild.id, message.channel.id),
                )
                already_seen = await cursor.fetchone()
                if not already_seen:
                    await db.execute(
                        "INSERT OR IGNORE INTO user_seen_channels (user_id, guild_id, channel_id) VALUES (?, ?, ?)",
                        (message.author.id, message.guild.id, message.channel.id),
                    )
                    await db.commit()
                    await message.channel.send(
                        f"{message.author.mention} {game_ui_texts.INTRO_PROMPT_MESSAGE}",
                        view=IntroEphemeralPromptView(message.author.id),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
        except Exception:
            logging.exception("Failed to handle intro prompt for first channel message")

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

    async def clear_durable_registration(self) -> None:
        guild_id = self._durable_guild_id
        channel_id = self._durable_channel_id
        self.bind_durable_message(guild_id=None, channel_id=None, message_id=None)
        if not isinstance(guild_id, int) or guild_id <= 0:
            return
        if not isinstance(channel_id, int) or channel_id <= 0:
            return
        try:
            await delete_durable_view(guild_id=guild_id, channel_id=channel_id)
        except Exception:
            logging.exception("Failed to clear durable view registry for channel %s", channel_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Any]) -> None:
        logging.exception("Durable view callback failed (%s)", self.durable_context_label(), exc_info=error)
        await _handle_durable_view_error(
            interaction,
            error,
            view=self,
            view_label=self.durable_context_label(),
            battle_log_text=self.durable_log_text(),
        )


def _build_mission_embed(mission_data: dict, *, user_already_owns_reward: bool = False) -> discord.Embed:
    title = mission_data.get("title") or "Mission"
    description = mission_data.get("description") or "Hier kommt später die Story. Hier kommt später die Story."
    reward_card = mission_data.get("reward_card") or {}
    waves = mission_data.get("waves", 0)
    embed = discord.Embed(title=title, description=description, color=_card_rarity_color(reward_card))
    embed.add_field(name="Wellen", value=f"{waves}", inline=True)
    if mission_data.get("unit_reward_after_wave"):
        embed.add_field(name="Units", value=f"+{int(mission_data.get('unit_reward_after_wave') or 0)} nach Welle 3", inline=True)
        _apply_item_media(embed, "unit", image=False, thumbnail=True)
    if reward_card:
        if user_already_owns_reward:
            embed.add_field(
                name="🎁 Belohnung",
                value=f"**{reward_card.get('name', '?')}** (bereits vorhanden – wird zu 💎 Infinitydust)",
                inline=True,
            )
            _, dust_thumbnail_url = _item_media_urls("infinitydust")
            if dust_thumbnail_url:
                embed.set_thumbnail(url=dust_thumbnail_url)
        else:
            embed.add_field(name="🎁 Belohnung", value=f"**{reward_card.get('name', '?')}**", inline=True)
            if reward_card.get("bild"):
                embed.set_image(url=reward_card["bild"])
    return embed


async def _user_already_owns_card(user_id: int, reward_card: dict | None) -> bool:
    if not reward_card:
        return False
    name = str(reward_card.get("name") or "").strip()
    if not name:
        return False
    try:
        return await has_exact_card_variant(int(user_id), name)
    except Exception:
        logging.exception("Failed to check user card ownership for mission preview")
        return False


def build_operation_broken_timeline_mission(*, mission_number: int | None = None, is_admin: bool = False) -> dict[str, Any]:
    reward_card = random_gameplay_card(
        karten,
        alpha_enabled=ALPHA_PHASE_ENABLED,
        context="mission_build_reward",
    )
    suffix = "Admin" if is_admin else f"{mission_number or 1}/2"
    return {
        "mission_id": "operation_broken_timeline",
        "title": game_ui_texts.operation_broken_timeline_title(is_admin=is_admin, suffix=suffix),
        "description": game_ui_texts.OPERATION_BROKEN_TIMELINE_DESCRIPTION,
        "waves": 4,
        "unit_reward_after_wave": 1,
        "interlude_after_wave": 3,
        "interlude_title": game_ui_texts.INTERLUDE_TITLE_DEFAULT,
        "interlude_text": game_ui_texts.INTERLUDE_TEXT_DEFAULT,
        "reward_card": reward_card,
        "current_wave": 0,
        "player_card": None,
        "encounters": get_operation_broken_timeline_encounters(),
    }


MISSION_OPERATION_DEFS: dict[str, dict[str, object]] = {
    operation_id: {
        "label": str((payload or {}).get("label") or operation_id),
        "title": str((payload or {}).get("title") or "Operation"),
        "description": str((payload or {}).get("description") or "Eine gefährliche Operation wartet auf dich."),
        "encounters_getter": {
            "operation_broken_timeline": get_operation_broken_timeline_encounters,
            "operation_technischer_kollaps": get_operation_technischer_kollaps_encounters,
            "operation_gruener_terror": get_operation_gruener_terror_encounters,
            "operation_goldener_kaefig": get_operation_goldener_kaefig_encounters,
            "operation_hexenfeuer": get_operation_hexenfeuer_encounters,
        }.get(operation_id),
    }
    for operation_id, payload in game_ui_texts.MISSION_OPERATION_TEXTS.items()
}

MISSION_OPERATION_ORDER: tuple[str, ...] = game_ui_texts.mission_operation_order()


def mission_operation_options() -> list[SelectOption]:
    options: list[SelectOption] = []
    for operation_id in MISSION_OPERATION_ORDER:
        payload = MISSION_OPERATION_DEFS.get(operation_id) or {}
        label = str(payload.get("label") or operation_id)
        options.append(SelectOption(label=label[:100], value=operation_id))
    return options


def build_mission_from_operation(
    operation_id: str,
    *,
    mission_number: int | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    if operation_id == "operation_broken_timeline":
        return build_operation_broken_timeline_mission(mission_number=mission_number, is_admin=is_admin)
    payload = MISSION_OPERATION_DEFS.get(str(operation_id or "").strip()) or {}
    encounters_getter = payload.get("encounters_getter")
    encounters: list[dict[str, Any]] = []
    if callable(encounters_getter):
        encounters = list(encounters_getter())
    reward_card = random_gameplay_card(
        karten,
        alpha_enabled=ALPHA_PHASE_ENABLED,
        context="mission_build_reward",
    )
    title = str(payload.get("title") or "Operation")
    description = str(payload.get("description") or "Eine gefährliche Operation wartet auf dich.")
    return {
        "mission_id": str(operation_id or "operation_unknown"),
        "title": title,
        "description": description,
        "waves": max(1, len(encounters)),
        "unit_reward_after_wave": 1,
        "interlude_after_wave": max(1, len(encounters) - 1),
        "interlude_title": game_ui_texts.INTERLUDE_TITLE_DEFAULT,
        "interlude_text": game_ui_texts.INTERLUDE_TEXT_DEFAULT,
        "reward_card": reward_card,
        "current_wave": 0,
        "player_card": None,
        "encounters": encounters,
        "mission_number": mission_number or 1,
        "is_admin": bool(is_admin),
    }


def _mission_encounters(mission_data: dict[str, Any]) -> list[dict[str, Any]]:
    encounters = mission_data.get("encounters")
    if not isinstance(encounters, list):
        return []
    return [_dict_str_any(item) for item in encounters if isinstance(item, dict)]


def _mission_encounter_for_wave(mission_data: dict[str, Any], wave_num: int) -> dict[str, Any] | None:
    encounters = _mission_encounters(mission_data)
    idx = int(wave_num or 1) - 1
    if 0 <= idx < len(encounters):
        return cast(dict[str, Any], _json_clone(encounters[idx]))
    return None


def _mission_preview_slides(mission_data: dict[str, Any], mode: str, *, wave_num: int | None = None) -> list[dict[str, Any]]:
    enc = _mission_encounters(mission_data)
    if not enc:
        return []
    if wave_num is not None:
        idx = int(wave_num or 1) - 1
        if 0 <= idx < len(enc):
            return [cast(dict[str, Any], _json_clone(enc[idx]))]
        return []
    if str(mode or "").strip().lower() == "boss":
        return [cast(dict[str, Any], _json_clone(enc[-1]))]
    return [cast(dict[str, Any], _json_clone(enc[0]))]


def _strip_mission_preview_keys(mission_state: dict[str, Any]) -> dict[str, Any]:
    st = dict(mission_state)
    st.pop("preview_index", None)
    return st


def _should_offer_boss_preview(mission_state: dict[str, Any]) -> bool:
    nw = int(mission_state.get("next_wave", 1) or 1)
    tw = int(mission_state.get("total_waves", 1) or 1)
    if nw != tw:
        return False
    md = _dict_str_any(mission_state.get("mission_data"))
    return bool(_mission_encounters(md))


def _preview_mode_for_next_wave(mission_state: dict[str, Any]) -> str:
    nw = int(mission_state.get("next_wave", 1) or 1)
    tw = int(mission_state.get("total_waves", 1) or 1)
    return "boss" if nw >= tw else "lackeys"


def _build_mission_enemy_preview_embed(
    enemy: dict[str, Any],
    *,
    mode: str,
    index: int,
    total: int,
) -> discord.Embed:
    name = str(enemy.get("name") or "Gegner")
    if str(mode or "").strip().lower() == "boss":
        title = game_ui_texts.PREVIEW_TITLE_BOSS
        desc = game_ui_texts.PREVIEW_DESCRIPTION_BOSS.format(name=name)
    else:
        title = game_ui_texts.PREVIEW_TITLE_LACKEY.format(index=index + 1, total=total)
        desc = game_ui_texts.PREVIEW_DESCRIPTION_LACKEY.format(name=name)
    embed = discord.Embed(title=title, description=desc, color=0xE74C3C)
    hp = enemy.get("hp", "?")
    rarity = str(enemy.get("seltenheit") or "").strip() or "—"
    embed.add_field(name="Gegner", value=f"**{name}**\nHP: {hp}", inline=True)
    embed.add_field(name=game_ui_texts.PREVIEW_FIELD_RARITY, value=rarity, inline=True)
    if enemy.get("bild"):
        embed.set_image(url=str(enemy["bild"]))
    _add_attack_info_field(embed, enemy, include_passives=True)
    if str(mode or "").strip().lower() == "boss":
        boss_key = str(enemy.get("mission_boss") or "").strip().lower()
        tactic_text = str(game_ui_texts.MISSION_BOSS_TACTICS.get(boss_key) or "").strip()
        if tactic_text:
            embed.add_field(name=game_ui_texts.PREVIEW_FIELD_TACTIC, value=tactic_text[:1024], inline=False)
    return embed


async def _launch_mission_encounter_preview_or_wave(
    interaction: discord.Interaction,
    mission_state: dict[str, Any],
    user_id: int,
    mode: str,
) -> None:
    ms = _strip_mission_preview_keys(dict(mission_state))
    slides = _mission_preview_slides(
        _dict_str_any(ms.get("mission_data")),
        mode,
        wave_num=int(ms.get("next_wave", 1) or 1),
    )
    if not slides:
        await _start_mission_wave_in_thread(interaction, mission_state=ms)
        return
    ms["preview_index"] = 0
    view = MissionEncounterPreviewView(user_id, ms, mode)
    await _safe_send_channel(interaction, interaction.channel, embed=view.build_embed(), view=view)


async def _continue_mission_after_pause_or_card_pick(
    interaction: discord.Interaction,
    mission_state: dict[str, Any],
    user_id: int,
) -> None:
    ms = _strip_mission_preview_keys(dict(mission_state))
    await _launch_mission_encounter_preview_or_wave(interaction, ms, user_id, _preview_mode_for_next_wave(ms))


def _is_operation_broken_timeline(mission_data: dict[str, Any]) -> bool:
    return str(mission_data.get("mission_id") or "") == "operation_broken_timeline"


def _cooldowns_by_attack_name(attacks: list[dict[str, Any]], cooldowns: dict[int, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, attack in enumerate(attacks[:4]):
        if not isinstance(attack, dict):
            continue
        name = str(attack.get("name") or "").strip()
        turns = _maybe_int(cooldowns.get(index))
        if name and turns is not None and turns > 0:
            result[name] = int(turns)
    return result


def _cooldowns_from_attack_names(attacks: list[dict[str, Any]], raw_cooldowns: object) -> dict[int, int]:
    if not isinstance(raw_cooldowns, dict):
        return {}
    normalized = {_label_key(name): _maybe_int(turns) for name, turns in raw_cooldowns.items()}
    result: dict[int, int] = {}
    for index, attack in enumerate(attacks[:4]):
        if not isinstance(attack, dict):
            continue
        turns = normalized.get(_label_key(attack.get("name")))
        if turns is not None and turns > 0:
            result[index] = int(turns)
    return result


_cooldown_carryover_state: dict[str, dict[int, dict[str, dict[str, int]]]] = {}


def _save_cooldown_carryover(
    mode: str,
    player_id: int,
    card_name: str,
    attacks: list[dict[str, Any]],
    cooldowns: dict[int, int],
) -> None:
    mode_key = str(mode or "").strip().lower()
    if not mode_key:
        return
    card_key = str(card_name or "").strip()
    if not card_key:
        return
    mapped = _cooldowns_by_attack_name(attacks, cooldowns)
    bucket = _cooldown_carryover_state.setdefault(mode_key, {})
    user_bucket = bucket.setdefault(int(player_id), {})
    user_bucket[card_key] = mapped


def _load_cooldown_carryover(
    mode: str,
    player_id: int,
    card_name: str,
    attacks: list[dict[str, Any]],
) -> dict[int, int]:
    mode_key = str(mode or "").strip().lower()
    card_key = str(card_name or "").strip()
    if not mode_key or not card_key:
        return {}
    raw = (
        _cooldown_carryover_state
        .get(mode_key, {})
        .get(int(player_id), {})
        .get(card_key, {})
    )
    return _cooldowns_from_attack_names(attacks, raw)


# Hilfsfunktion: Karte nach Namen finden
async def get_karte_by_name(name: str) -> dict[str, Any] | None:
    wanted_name = canonical_card_name(name)
    runtime_card = build_runtime_card(wanted_name, cards=karten)
    if runtime_card is not None:
        return runtime_card
    runtime_card = build_runtime_card(name, cards=karten)
    if runtime_card is not None:
        return runtime_card
    return None

def _sort_user_cards_like_karten(user_cards) -> list[tuple[str, int]]:
    """Sort user-owned exact cards by base-card order and variant order."""
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
        normalized.append((normalize_owned_card_name(name, cards=karten), amount))

    def _key(item: tuple[str, int]) -> tuple[int, str]:
        name = str(item[0]).strip()
        base_name = base_card_name(name, cards=karten)
        idx = order_map.get(base_name.lower(), 10**9)
        variant_rows = exact_variant_names_with_amounts([(name, 1)], base_name, cards=karten)
        variant_name = variant_rows[0][0] if variant_rows else name
        return idx, variant_name.lower()

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
        karte = random_gameplay_card(karten, alpha_enabled=ALPHA_PHASE_ENABLED, context="draw_card")
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
            # Req. 7.3/7.8: bereits besessene Karte gibt den konfigurierten Bonus-Staub (sofort).
            _dup_bonus = mission_rewards.daily_duplicate_bonus()
            if _dup_bonus > 0:
                await add_infinitydust(self.user_id, _dup_bonus)
            embed = discord.Embed(
                title="💎 Mission abgeschlossen - Infinitydust!",
                description=f"Du hattest **{karte['name']}** bereits!",
                color=_card_rarity_color(karte),
            )
            embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
            # Req. 8.2/8.3: Dust-Bild ausschließlich als Thumbnail, nie als großes Bild.
            _apply_item_media(embed, "infinitydust", image=False, thumbnail=True)
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
        self.session_kind = "fight_pvp" if player2_id != 0 else "fight_bot"

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
        self.last_special_attack = runtime_maps["last_special_attack"]
        self._last_damage_roll_meta: dict | None = None
        self._optional_attack_confirmations: dict[int, dict[str, object]] = {}

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
        attacker_id = int(getattr(attacker_user, "id", 0) or 0)
        defender_id = int(getattr(defender_user, "id", 0) or 0)
        await _log_event_safe(
            "attack_used",
            guild_id=self._durable_guild_id,
            channel_id=self._durable_channel_id,
            thread_id=self._durable_channel_id if self.session_kind in {"fight_pvp", "fight_bot", "mission"} else 0,
            session_id=self.session_id,
            session_kind=self.session_kind,
            actor_user_id=attacker_id,
            target_user_id=defender_id,
            hero_name=attacker_name,
            attack_name=attack_name,
            payload={
                "round": int(round_number),
                "is_critical": bool(effective_critical),
                "damage": _damage_breakdown_payload(
                    actual_damage=int(actual_damage),
                    pre_effect_damage=int(pre_effect_damage),
                    effect_events=effect_events,
                    self_hit_damage=int(self_hit_damage),
                ),
                "defender_remaining_hp": int(defender_remaining_hp or 0),
                "attacker_remaining_hp": int(attacker_remaining_hp or 0) if attacker_remaining_hp is not None else None,
                "effect_events": [str(item) for item in effect_events or []],
                "attacker_status_icons": attacker_status_icons,
                "defender_status_icons": defender_status_icons,
            },
        )

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
            "last_special_attack": _json_clone(self.last_special_attack),
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
        self.special_lock_next_turn = _int_keyed_int_dict(payload.get("special_lock_next_turn"))
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
        raw_last_special = _int_keyed_dict(payload.get("last_special_attack"))
        self.last_special_attack = {key: (value if isinstance(value, dict) else None) for key, value in raw_last_special.items()}
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
        self._optional_attack_confirmations = {}
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
        was_new_session = self.session_id is None
        if battle_message is not None:
            self.bind_durable_message(guild_id=guild.id, channel_id=channel_id, message_id=battle_message.id)
        self.session_id = await save_active_session(
            session_id=self.session_id,
            kind=self.session_kind,
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
        if was_new_session and status == "active":
            await _log_event_safe(
                "fight_started",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=self.player1_id,
                target_user_id=self.player2_id,
                payload={
                    "player_hero": self.player1_card.get("name"),
                    "enemy_hero": self.player2_card.get("name"),
                },
            )
            await _log_event_safe(
                "hero_selected",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=self.player1_id,
                target_user_id=self.player2_id,
                hero_name=self.player1_card.get("name"),
                payload={"side": "player1"},
            )
            await _log_event_safe(
                "hero_selected",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=self.player2_id,
                target_user_id=self.player1_id,
                hero_name=self.player2_card.get("name"),
                payload={"side": "player2" if self.player2_id != 0 else "bot"},
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
        landing_attack: dict[str, object] | None = None,
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
            landing_attack=landing_attack,
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
        max_store: int | None = None,
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
            max_store=max_store,
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
        ignore_all_defense: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int, dict[str, object] | None]:
        return battle_state.resolve_incoming_modifiers(
            self.incoming_modifiers,
            self.absorbed_damage,
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            ignore_all_defense=ignore_all_defense,
            incoming_min_damage=incoming_min_damage,
        )

    def resolve_incoming_modifiers(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        ignore_all_defense: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int]:
        final_damage, reflected_damage, dodged, counter_damage, _modifier_details = self._resolve_incoming_modifiers_with_details(
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            ignore_all_defense=ignore_all_defense,
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
            except discord.NotFound:
                # Kanal/Thread wurde gelöscht – weiteres Bearbeiten überspringen.
                self.battle_log_message = None
                return
            except discord.Forbidden:
                self.battle_log_message = None
                return
            except Exception as e:
                if getattr(e, "status", None) == 429:
                    await asyncio.sleep(_rate_limit_delay_from_error(e, attempt))
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
            "capped_damage_multiplier",
            "damage_multiplier",
            "next_standard_damage_override",
            "attack_heal",
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
            "clear_negative_effects",
            "random_pym_debuff",
        }
        for effect in attack.get("effects", []):
            if str(effect.get("type") or "").strip().lower() in setup_types:
                return True
        return False

    def _estimate_attack_max_damage_for_bot(self, attack: dict, defender_hp: int, attacker_hp: int) -> int:
        attack_for_estimate = dict(attack)
        damage_buff = 0
        attacker_max_hp = self._max_hp_for(0)
        defender_max_hp = self._max_hp_for(self.player1_id)

        conditional_self_pct = _maybe_float(attack.get("bonus_if_self_hp_below_pct"))
        conditional_self_bonus = _maybe_int(attack.get("bonus_damage_if_condition", 0)) or 0
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * conditional_self_pct):
            damage_buff += conditional_self_bonus

        conditional_enemy_pct = _maybe_float(attack.get("conditional_enemy_hp_below_pct"))
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * conditional_enemy_pct):
            damage_if_condition = attack.get("damage_if_condition")
            attack_for_estimate["damage"] = _coerce_damage_input(damage_if_condition, default=0)

        if attack.get("add_absorbed_damage"):
            damage_buff += int(self.absorbed_damage.get(0, 0) or 0)

        _min_damage, max_damage = _attack_total_damage_range(attack_for_estimate, max_only_bonus=0, flat_bonus=damage_buff)
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
            standard_idx = _standard_attack_index(attacks)
            for idx, other in enumerate(attacks[:4]):
                if idx == attack_index:
                    continue
                if self.special_lock_next_turn.get(0, 0) > 0 and idx != standard_idx:
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
        if defender_has_stealth and not guaranteed_hit_candidate and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
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
        standard_idx = _standard_attack_index(attacks)

        attacker_hp = self._hp_for(0)
        attacker_max_hp = self._max_hp_for(0)
        candidate_indices: list[int] = []
        for i, attack in enumerate(attacks[:4]):
            if self.special_lock_next_turn.get(0, 0) > 0 and i != standard_idx:
                continue
            if i == standard_idx and _find_active_effect(self.active_effects, 0, "standard_lock"):
                continue
            if not _attack_allowed_at_self_hp(attack, attacker_hp, attacker_max_hp):
                continue
            if not self.is_attack_on_cooldown(0, i):
                candidate_indices.append(i)

        if not candidate_indices:
            for i, attack in enumerate(attacks[:4]):
                if self.special_lock_next_turn.get(0, 0) > 0 and i != standard_idx:
                    continue
                if i == standard_idx and _find_active_effect(self.active_effects, 0, "standard_lock"):
                    continue
                if not _attack_allowed_at_self_hp(attack, attacker_hp, attacker_max_hp):
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
        standard_idx = _standard_attack_index(attacks)

        # Hole Buffs für diese Karte
        card_buffs = await get_card_buffs(self.current_turn, current_card["name"])

        # Finde die vier Angriffs-Buttons (Zeilen 0 und 1, unabhängig von Label/Style)
        attack_buttons = [child for child in self.children if isinstance(child, ui.Button) and child.row in (0, 1)]
        attack_buttons = attack_buttons[:4]

        pending_landing = self.airborne_pending_landing.get(self.current_turn)
        if pending_landing:
            landing_slot = _pending_landing_slot_index(pending_landing)
            raw_landing_attack = pending_landing.get("attack")
            landing_attack = raw_landing_attack if isinstance(raw_landing_attack, dict) else {}
            landing_damage = pending_landing.get("damage", [20, 40])
            if isinstance(landing_damage, list) and len(landing_damage) == 2:
                dmg_text = f"{int(landing_damage[0])}-{int(landing_damage[1])}"
            else:
                dmg_text = "20-40"
            landing_name = str(landing_attack.get("name") or "Landungsschlag")
            for i, btn in enumerate(attack_buttons):
                btn.style = discord.ButtonStyle.secondary
                if i >= len(attacks):
                    btn.label = "—"
                    btn.disabled = True
                    continue
                if i == landing_slot:
                    btn.style = discord.ButtonStyle.danger
                    btn.label = f"{landing_name} ({dmg_text}) ✈️"
                    btn.disabled = False
                    continue
                blocked_attack = attacks[i]
                blocked_name = str(blocked_attack.get("name") or f"Angriff {i+1}")
                if self.is_attack_on_cooldown(self.current_turn, i):
                    cooldown_turns = self.attack_cooldowns[self.current_turn].get(i, 0)
                    btn.label = f"{blocked_name} ({_format_cooldown_label(blocked_attack, cooldown_turns)})"
                else:
                    btn.label = f"{blocked_name} (Blockiert)"
                btn.disabled = True
            return

        if self.special_lock_next_turn.get(self.current_turn, 0) > 0:
            for i, button in enumerate(attack_buttons):
                button.style = discord.ButtonStyle.secondary
                if i >= len(attacks):
                    button.label = "—"
                    button.disabled = True
                    continue
                attack = attacks[i]
                if i == standard_idx:
                    damage_max_bonus = 0
                    for buff_type, attack_number, buff_amount in card_buffs:
                        if buff_type == "damage" and attack_number == (i + 1):
                            damage_max_bonus += buff_amount
                    display_label, display_style, _ = _attack_display_parts(
                        attack,
                        max_only_bonus=damage_max_bonus,
                    )
                    is_on_cooldown = self.is_attack_on_cooldown(self.current_turn, i)
                    is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.current_turn, i))
                    if is_on_cooldown:
                        cooldown_turns = self.attack_cooldowns[self.current_turn][i]
                        button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                        button.disabled = True
                    else:
                        if is_reload_action:
                            button.style = discord.ButtonStyle.primary
                            button.label = str(attack.get("reload_name") or "Nachladen")
                        else:
                            button.style = display_style
                            button.label = display_label
                        button.disabled = False
                    continue
                attack_name = str(attack.get("name") or f"Angriff {i+1}")
                if self.is_attack_on_cooldown(self.current_turn, i):
                    cooldown_turns = self.attack_cooldowns[self.current_turn].get(i, 0)
                    button.label = f"{attack_name} ({_format_cooldown_label(attack, cooldown_turns)})"
                else:
                    button.label = f"{attack_name} (Gesperrt)"
                button.disabled = True
            return

        for i, attack in enumerate(attacks[:4]):
            if i < len(attack_buttons):
                button = attack_buttons[i]
                damage_max_bonus = 0
                for buff_type, attack_number, buff_amount in card_buffs:
                    if buff_type == "damage" and attack_number == (i + 1):
                        damage_max_bonus += buff_amount
                display_label, display_style, _ = _attack_display_parts(
                    attack,
                    max_only_bonus=damage_max_bonus,
                )
                is_on_cooldown = self.is_attack_on_cooldown(self.current_turn, i)
                is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(self.current_turn, i))
                if is_on_cooldown:
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = self.attack_cooldowns[self.current_turn][i]
                    button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                    button.disabled = True
                elif is_reload_action:
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    button.style = display_style
                    button.label = display_label
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
        current_attacks = (
            self.player1_card.get("attacks", [])
            if self.current_turn == self.player1_id
            else self.player2_card.get("attacks", [])
        )
        standard_idx = _standard_attack_index(current_attacks)

        # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist
        if not is_forced_landing and self.is_attack_on_cooldown(self.current_turn, attack_index):
            await _safe_send_interaction_ephemeral(interaction, "Diese Attacke ist noch auf Cooldown!")
            return

        if is_forced_landing:
            landing_slot = _maybe_int(forced_landing_attack.get("cooldown_attack_index"))
            if landing_slot is not None and 0 <= int(landing_slot) < 4 and int(landing_slot) != int(attack_index):
                await _safe_send_interaction_ephemeral(
                    interaction,
                    f"Diese Runde ist nur {str(forced_landing_attack.get('name') or 'Landungsschlag')} im ursprünglichen Slot verfügbar.",
                )
                return

        if (not is_forced_landing) and self.special_lock_next_turn.get(self.current_turn, 0) > 0 and attack_index != standard_idx:
            standard_attack = current_attacks[standard_idx] if 0 <= standard_idx < len(current_attacks) else {"name": "Standardangriff"}
            await _safe_send_interaction_ephemeral(
                interaction,
                f"Diese Runde ist nur der Standardangriff {str(standard_attack.get('name') or 'Standardangriff')} erlaubt.",
            )
            return
        if (not is_forced_landing) and attack_index == standard_idx and _find_active_effect(self.active_effects, self.current_turn, "standard_lock"):
            await _safe_send_interaction_ephemeral(interaction, "Dein Standardangriff ist in dieser Runde gesperrt.")
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

        # Bereits liegende DoT-Effekte des Angreifers ticken vor dem Angriff.
        pre_burn_total, dot_tick_events = _apply_dot_ticks_for_applier(
            self.active_effects,
            target_id=defender_id,
            applier_id=self.current_turn,
            damage_callback=(lambda amount: self._apply_non_heal_damage(defender_id, amount)),
        )
        for event_text in dot_tick_events:
            self._append_effect_event(effect_events, event_text)

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

        attack_effect_types = {
            str(effect.get("type") or "").strip().lower()
            for effect in attack.get("effects", [])
            if isinstance(effect, dict)
        }
        last_enemy_special_entry = self.last_special_attack.get(defender_id)
        reset_cooldown_index: int | None = None
        if (not is_forced_landing) and (not is_reload_action):
            if "increase_last_enemy_special_cooldown" in attack_effect_types and not isinstance(last_enemy_special_entry, dict):
                confirmed = await _require_optional_attack_confirmation(
                    self,
                    interaction,
                    player_id=self.current_turn,
                    attack_index=attack_index,
                    attack_name=str(attack_name),
                    reason_text="Es gibt noch keine gegnerische Spezialfähigkeit zum Verlängern.",
                )
                if not confirmed:
                    return
            if "copy_last_enemy_special" in attack_effect_types and not isinstance(last_enemy_special_entry, dict):
                confirmed = await _require_optional_attack_confirmation(
                    self,
                    interaction,
                    player_id=self.current_turn,
                    attack_index=attack_index,
                    attack_name=str(attack_name),
                    reason_text="Es gibt noch keine gegnerische Spezialfähigkeit zum Kopieren.",
                )
                if not confirmed:
                    return
            if "reset_own_cooldown" in attack_effect_types:
                reset_cooldown_index = _pick_resettable_cooldown_index(
                    self.attack_cooldowns[self.current_turn],
                    exclude_index=attack_index,
                )
                if reset_cooldown_index is None:
                    confirmed = await _require_optional_attack_confirmation(
                        self,
                        interaction,
                        player_id=self.current_turn,
                        attack_index=attack_index,
                        attack_name=str(attack_name),
                        reason_text="Es gibt gerade keine eigene Fähigkeit zum Zurücksetzen.",
                    )
                    if not confirmed:
                        return
            if "copy_last_enemy_special" in attack_effect_types and isinstance(last_enemy_special_entry, dict):
                copied_attack = _copied_attack_from_history(last_enemy_special_entry)
                if copied_attack is not None:
                    attack = copied_attack
                    base_damage = copied_attack.get("damage", base_damage)
                    self._append_effect_event(
                        effect_events,
                        f"Gedankenkontrolle kopiert {str(last_enemy_special_entry.get('attack_name') or 'die letzte Spezialfähigkeit')}.",
                    )

        action_type = _attack_kind_label(
            attack,
            attacks=attacks,
            attack_index=attack_index,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
        )
        miss_reason: str | None = None

        attacker_hp = self._hp_for(self.current_turn)
        attacker_max_hp = self._max_hp_for(self.current_turn)
        defender_hp = self._hp_for(defender_id)
        defender_max_hp = self._max_hp_for(defender_id)

        conditional_self_pct = _maybe_float(attack.get("bonus_if_self_hp_below_pct"))
        conditional_self_bonus = _maybe_int(attack.get("bonus_damage_if_condition", 0)) or 0
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * conditional_self_pct):
            damage_buff += conditional_self_bonus

        conditional_enemy_triggered = False
        conditional_enemy_pct = _maybe_float(attack.get("conditional_enemy_hp_below_pct"))
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * conditional_enemy_pct):
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
        attack_penalty = _consume_attack_penalty(self.active_effects, self.current_turn)
        if attack_penalty > 0:
            damage_buff -= attack_penalty
            self._append_effect_event(effect_events, f"Schadensmalus aktiv: -{attack_penalty} auf diesen Angriff.")

        effective_attack = dict(attack)
        effective_attack["damage"] = base_damage
        is_damaging_attack = _attack_has_direct_damage(effective_attack)
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
            restricted_bonus_now, restricted_bonus_effect = _consume_restricted_flat_damage_bonus(
                self.active_effects,
                self.current_turn,
                attack,
                attack_index=attack_index,
                standard_index=standard_idx,
            )
            if restricted_bonus_now > 0:
                damage_buff += restricted_bonus_now
                applied_flat_bonus_now += max(0, restricted_bonus_now)
                source = str((restricted_bonus_effect or {}).get("source") or "Verstärkung")
                self._append_effect_event(effect_events, f"{source}: +{restricted_bonus_now} Schaden auf diesen Angriff.")
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
        force_min_damage = _force_min_damage_active(self.active_effects, self.current_turn)
        attack_cancelled_by_heal_curse = False
        heal_curse_effect = _find_active_effect(self.active_effects, self.current_turn, "heal_curse")
        if (not is_reload_action) and heal_curse_effect is not None and _attack_has_heal_component(attack):
            attack_cancelled_by_heal_curse = True
            curse_damage = max(0, _effect_int(heal_curse_effect, "damage", 0))
            curse_source = str(heal_curse_effect.get("source") or "Hex-Fluch")
            turns_left = max(0, _effect_int(heal_curse_effect, "turns", 1) - 1)
            heal_curse_effect["turns"] = turns_left
            if turns_left <= 0:
                _remove_active_effect(self.active_effects, self.current_turn, heal_curse_effect)
            if curse_damage > 0:
                self._apply_non_heal_damage_with_event(
                    effect_events,
                    self.current_turn,
                    curse_damage,
                    source=curse_source,
                    self_damage=True,
                )
            self._append_effect_event(effect_events, "Heilung wurde blockiert. Diese Attacke verbraucht keinen Cooldown.")

        # Manual reload action: spend turn to load the shot again.
        attack_hits_enemy = True
        self_damage = 0
        if is_reload_action:
            actual_damage = 0
            is_critical = False
            attack_hits_enemy = False
            self.set_reload_needed(self.current_turn, attack_index, False)
        elif attack_cancelled_by_heal_curse:
            actual_damage = 0
            is_critical = False
            attack_hits_enemy = False
        else:
            min_damage = 0
            max_damage = 0
            defender_has_stealth = self.has_stealth(defender_id)
            guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(self.current_turn)
            if guaranteed_hit:
                self.blind_next_attack[self.current_turn] = 0.0
                self.consume_confusion_if_any(self.current_turn)
                self._append_effect_event(effect_events, "Dieser Angriff trifft garantiert.")
            current_attack_profile = dict(attack)
            current_attack_profile["damage"] = base_damage
            _min_threshold_damage, max_damage_threshold = _attack_total_damage_range(
                current_attack_profile,
                max_only_bonus=0,
                flat_bonus=damage_buff,
            )
            blind_chance = float(self.blind_next_attack.get(self.current_turn, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[self.current_turn] = 0.0
                blind_miss = random.random() < blind_chance
            # CONFUSION: Falls Angreifer verwirrt ist, 77% Selbstschaden, 23% normaler Treffer
            if blind_miss:
                miss_reason = f"durch Blendung ({int(round(blind_chance * 100))}% Verfehlchance)"
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
                    miss_reason = "durch Verwirrung, stattdessen Selbsttreffer"
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
                    if force_min_damage:
                        actual_damage = min_damage
                        is_critical = False
                        _consume_force_min_damage(self.active_effects, self.current_turn)
                    self._append_multi_hit_roll_event(effect_events)
                    if (
                        defender_has_stealth
                        and actual_damage > 0
                        and not guaranteed_hit
                        and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    ):
                        actual_damage = 0
                        is_critical = False
                        attack_hits_enemy = False
                        miss_reason = "durch Tarnung"
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
                if force_min_damage:
                    actual_damage = min_damage
                    is_critical = False
                    _consume_force_min_damage(self.active_effects, self.current_turn)
                self._append_multi_hit_roll_event(effect_events)
                if (
                    defender_has_stealth
                    and actual_damage > 0
                    and not guaranteed_hit
                    and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                ):
                    actual_damage = 0
                    is_critical = False
                    attack_hits_enemy = False
                    miss_reason = "durch Tarnung"
                    self.consume_stealth(defender_id)

            if attack_hits_enemy and actual_damage > 0:
                before_override = int(actual_damage)
                actual_damage, override_effect = _consume_next_standard_damage_override(
                    self.active_effects,
                    self.current_turn,
                    attack_index=attack_index,
                    standard_index=standard_idx,
                    current_damage=actual_damage,
                )
                if override_effect is not None and actual_damage != before_override:
                    source = str(override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Standardangriff {before_override} -> {actual_damage} Schaden.")
                before_capped = int(actual_damage)
                actual_damage, capped_bonus, capped_effect = _consume_capped_damage_multiplier(self.active_effects, self.current_turn, actual_damage)
                if capped_effect is not None and capped_bonus > 0:
                    source = str(capped_effect.get("source") or "Geheimakte")
                    self._append_effect_event(effect_events, f"{source}: Schaden {before_capped} -> {actual_damage} (+{capped_bonus}, max. +{_effect_amount_label(capped_effect.get('max_bonus', 0))}).")
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
                        _outgoing_reduction_effect_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.current_turn,
                        overflow_self_damage,
                        source=_overflow_recoil_source(modifier_source or None),
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False
                incoming_bonus = _incoming_damage_bonus(self.active_effects, defender_id)
                if incoming_bonus > 0 and actual_damage > 0:
                    actual_damage += incoming_bonus
                    self._append_effect_event(effect_events, f"Schadensanfälligkeit: +{incoming_bonus} eingehender Schaden.")

                bypass_all_defense = bool(
                    attack.get("ignore_defense")
                    or attack.get("ignore_shield")
                    or attack.get("unblockable")
                    or _find_active_effect(self.active_effects, defender_id, "disable_enemy_evade_and_block")
                )
                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(defender_id, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    defender_id,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(defender_id)),
                    ignore_all_defense=bypass_all_defense,
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
                    readable_source = _readable_effect_source((incoming_modifier or {}).get("source"))
                    miss_reason = f"durch {readable_source}" if readable_source else "durch Ausweichen"
                    actual_damage = 0
                    attack_hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    actual_damage, reactive_reduction = _apply_reactive_evolution_reduction(
                        self.active_effects,
                        defender_id,
                        actual_damage,
                    )
                    if reactive_reduction > 0:
                        self._append_effect_event(effect_events, f"Reaktive Evolution reduziert den Treffer um {reactive_reduction}.")
                    shield_break_counter = 0
                    if actual_damage > 0 and not bypass_all_defense:
                        actual_damage, shield_break_counter = _consume_shield_damage(
                            self.active_effects,
                            defender_id,
                            actual_damage,
                        )
                        if shield_break_counter > 0:
                            self._append_effect_event(effect_events, f"Schild zerbricht und verursacht {shield_break_counter} Rückschaden.")
                    if actual_damage > 0:
                        self._apply_non_heal_damage(defender_id, actual_damage)
                    else:
                        is_critical = False
                    if shield_break_counter > 0:
                        self._apply_non_heal_damage_with_event(
                            effect_events,
                            self.current_turn,
                            shield_break_counter,
                            source="Schildbruch",
                            self_damage=False,
                        )
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
                hit_heal, heal_effect = _consume_attack_heal(self.active_effects, self.current_turn)
                if hit_heal > 0:
                    healed_now = self.heal_player(self.current_turn, hit_heal)
                    if healed_now > 0:
                        self._append_effect_event(effect_events, f"{str((heal_effect or {}).get('source') or 'Trefferheilung')}: Treffer heilt {healed_now} HP.")
            if not attack_hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = _resolve_self_damage_value(attack.get("self_damage", 0))
        if self_damage_value > 0:
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.current_turn,
                self_damage_value,
                source=f"{attack_name} / Rückstoß",
                self_damage=True,
            )

        trap_self_damage = _consume_attack_self_damage_effect(
            self.active_effects,
            self.current_turn,
            special_attack=bool((not is_forced_landing) and attack_index != standard_idx),
        )
        if trap_self_damage > 0:
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.current_turn,
                trap_self_damage,
                source="Vorbereiteter Gegeneffekt",
                self_damage=True,
            )

        heal_data = attack.get("heal")
        healing_disabled = bool(_find_active_effect(self.active_effects, self.current_turn, "disable_enemy_heal_if_bleeding")) and _sum_target_dot_damage(
            self.active_effects,
            self.current_turn,
            "bleeding",
        ) > 0
        if heal_data is not None:
            if healing_disabled:
                self._append_effect_event(effect_events, "Heilung blockiert: Blutung verhindert diesen Effekt.")
            else:
                heal_chance = _maybe_float(attack.get("heal_chance", 1.0)) or 1.0
                if random.random() <= heal_chance:
                    heal_amount = _random_int_from_range(heal_data)
                    healed_now = self.heal_player(self.current_turn, heal_amount)
                    if healed_now > 0:
                        self._append_effect_event(effect_events, f"Heilung: +{healed_now} HP.")

        lifesteal_ratio = _maybe_float(attack.get("lifesteal_ratio", 0.0)) or 0.0
        if lifesteal_ratio > 0 and attack_hits_enemy and actual_damage > 0 and not healing_disabled:
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
        raw_effects = attack.get("effects", [])
        effects = raw_effects if isinstance(raw_effects, list) else []
        confusion_applied = False
        burning_duration_for_dynamic_cooldown: int | None = None
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            # 70% Fix-Chance für Verwirrung
            chance = 0.7 if effect.get('type') == 'confusion' else (_maybe_float(effect.get('chance', 1.0)) or 1.0)
            if random.random() >= chance:
                continue
            target = effect.get("target", "enemy")
            target_id = self.current_turn if target == "self" else defender_id
            eff_type = effect.get("type")
            if target != "self" and not attack_hits_enemy and eff_type not in {"stun"}:
                continue
            if target != "self" and _should_block_negative_effect(self.active_effects, target_id, eff_type):
                if _consume_status_immunity(self.active_effects, target_id):
                    self._append_effect_event(effect_events, "Status-Immunität verhindert den negativen Effekt.")
                continue
            if eff_type == "stun" and _shield_has_stun_immunity(self.active_effects, target_id):
                self._append_effect_event(effect_events, "Betäubung abgewehrt: Schild schützt vor Stun.")
                continue
            if _apply_word_runtime_effect(self, effect_events, eff_type=str(eff_type), target_id=target_id, attack_name=attack_name, effect=effect):
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif _is_dot_effect_type(eff_type):
                dot_multiplier = _consume_burn_multiplier(self.active_effects, self.current_turn) if str(eff_type or "").strip().lower() == "burning" else 1.0
                duration, burn_damage = _append_dot_effect(
                    self.active_effects,
                    target_id=target_id,
                    attacker_id=self.current_turn,
                    effect_type=eff_type,
                    duration=effect.get("duration"),
                    damage=effect.get("damage"),
                    damage_multiplier=dot_multiplier,
                )
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"{_dot_label(eff_type)} aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == "confusion":
                # Confuse defender for next turn + UI marker
                self.set_confusion(target_id, self.current_turn)
                confusion_applied = True
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = _effect_amount(effect, "amount", 0)
                uses = int(effect.get("uses", 1) or 1)
                _queue_flat_damage_boost(
                    self,
                    effect_events,
                    target_id=target_id,
                    applier_id=self.current_turn,
                    attack_name=str(attack_name),
                    amount=amount,
                    uses=uses,
                    effect=effect,
                )
            elif eff_type == "attack_heal":
                uses = int(effect.get("uses", 1) or 1)
                _append_active_effect(self.active_effects, target_id, "attack_heal", self.current_turn, amount=effect.get("amount", 0), uses=uses, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Trefferheilung aktiv: +{_effect_amount_label(effect.get('amount', 0))} HP für {uses} eigene Treffer."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
            elif eff_type == "capped_damage_multiplier":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "capped_damage_multiplier",
                    self.current_turn,
                    multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                    max_bonus=effect.get("max_bonus", 0),
                    uses=max(1, int(effect.get("uses", 1) or 1)),
                    source=attack_name,
                )
            elif eff_type == "next_standard_damage_override":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "next_standard_damage_override",
                    self.current_turn,
                    turns=max(1, int(effect.get("turns", 1) or 1)),
                    damage=effect.get("damage", 0),
                    source=attack_name,
                )
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
            elif eff_type == "standard_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "standard_lock", self.current_turn, turns=turns, source=attack_name)
                self._append_effect_event(effect_events, "Der nächste gegnerische Standardangriff ist gesperrt.")
            elif eff_type == "status_immunity":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "status_immunity", self.current_turn, turns=turns, source=attack_name)
                self._append_effect_event(effect_events, "Status-Immunität aktiviert.")
            elif eff_type in {"enemy_attack_self_damage", "enemy_special_self_damage", "enemy_next_special_self_damage"}:
                turns = max(1, int(effect.get("turns", 1) or 1))
                amount = max(0, int(effect.get("amount", 0) or 0))
                _append_active_effect(self.active_effects, target_id, str(eff_type), self.current_turn, turns=turns, amount=amount, source=attack_name)
                self._append_effect_event(effect_events, f"Vorbereiteter Gegeneffekt: {amount} Selbstschaden beim passenden Angriff.")
            elif eff_type == "disable_enemy_evade_and_block":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "disable_enemy_evade_and_block", self.current_turn, turns=turns, source=attack_name)
                self._append_effect_event(effect_events, "Gegnerische Abwehr ist kurzzeitig deaktiviert.")
            elif eff_type == "shield":
                shield_hp = max(1, _effect_amount(effect, "hp", 1))
                existing_shield = _shield_entry(self.active_effects, target_id)
                if existing_shield is not None:
                    _remove_active_effect(self.active_effects, target_id, existing_shield)
                shield_fields: dict[str, object] = {"hp": shield_hp, "source": attack_name}
                if effect.get("break_counter") is not None:
                    shield_fields["break_counter"] = int(effect.get("break_counter", 0) or 0)
                if effect.get("stun_immunity") is not None:
                    shield_fields["stun_immunity"] = bool(effect.get("stun_immunity"))
                if effect.get("max_hits") is not None:
                    shield_fields["max_hits"] = int(effect.get("max_hits", 0) or 0)
                _append_active_effect(self.active_effects, target_id, "shield", self.current_turn, **shield_fields)
                self._append_effect_event(effect_events, f"Schild aktiv: {shield_hp} absorbierbarer Schaden.")
            elif eff_type == "increase_random_enemy_cooldown":
                target_attacks = self.player1_card.get("attacks", []) if target_id == self.player1_id else self.player2_card.get("attacks", [])
                chosen_idx, new_cd = _apply_random_enemy_cooldown_increase(
                    target_attacks,
                    self.attack_cooldowns[target_id],
                    amount=int(effect.get("amount", 1) or 1),
                )
                if chosen_idx is not None and 0 <= chosen_idx < len(target_attacks):
                    target_name = str(target_attacks[chosen_idx].get("name") or f"Angriff {chosen_idx+1}")
                    self._append_effect_event(effect_events, f"Cooldown erhöht: {target_name} ist jetzt {new_cd} Runde(n) gesperrt.")
            elif eff_type == "increase_last_enemy_special_cooldown":
                if isinstance(last_enemy_special_entry, dict):
                    last_index = _maybe_int(last_enemy_special_entry.get("attack_index", -1)) or -1
                    if last_index >= 0:
                        bonus = max(1, _maybe_int(effect.get("amount", 1)) or 1)
                        self.attack_cooldowns[defender_id][last_index] = max(0, int(self.attack_cooldowns[defender_id].get(last_index, 0) or 0)) + bonus
                        self._append_effect_event(effect_events, f"Cooldown verlängert: {str(last_enemy_special_entry.get('attack_name') or 'letzte Spezialfähigkeit')} +{bonus}.")
            elif eff_type == "incoming_damage_bonus":
                turns = max(1, int(effect.get("turns", 1) or 1))
                amount = max(0, int(effect.get("amount", 0) or 0))
                _append_active_effect(self.active_effects, target_id, "incoming_damage_bonus", self.current_turn, turns=turns, amount=amount, source=attack_name)
                self._append_effect_event(effect_events, f"Der Gegner erleidet {turns} Runde(n) lang +{amount} Schaden.")
            elif eff_type == "interrupt_enemy_standard_or_heal_self":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "interrupt_enemy_standard_or_heal_self",
                    self.current_turn,
                    turns=turns,
                    damage=int(effect.get("damage", 0) or 0),
                    heal=int(effect.get("heal", 0) or 0),
                    source=attack_name,
                )
                self._append_effect_event(effect_events, "Flammenwand ist vorbereitet.")
            elif eff_type == "burn_multiplier":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "burn_multiplier",
                    self.current_turn,
                    uses=max(1, int(effect.get("uses", 1) or 1)),
                    multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                    turns=1,
                    source=attack_name,
                )
                self._append_effect_event(effect_events, "Der nächste Brand wird verstärkt.")
            elif eff_type == "reset_own_cooldown":
                if reset_cooldown_index is not None:
                    self.attack_cooldowns[self.current_turn].pop(reset_cooldown_index, None)
                    own_attacks = self.player1_card.get("attacks", []) if self.current_turn == self.player1_id else self.player2_card.get("attacks", [])
                    if 0 <= reset_cooldown_index < len(own_attacks):
                        reset_name = str(own_attacks[reset_cooldown_index].get("name") or f"Angriff {reset_cooldown_index+1}")
                        self._append_effect_event(effect_events, f"Cooldown zurückgesetzt: {reset_name} ist wieder einsatzbereit.")
            elif eff_type == "heal_curse":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "heal_curse",
                    self.current_turn,
                    turns=turns,
                    damage=int(effect.get("damage", effect.get("amount", 0)) or 0),
                    source=attack_name,
                )
                self._append_effect_event(effect_events, "Hex-Fluch aktiv: Heilversuche verursachen Schaden.")
            elif eff_type == "next_attack_flat_penalty":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "next_attack_flat_penalty", self.current_turn, turns=turns, amount=int(effect.get("amount", 0) or 0), source=attack_name)
                self._append_effect_event(effect_events, "Der nächste eigene Angriff wird geschwächt.")
            elif eff_type == "enemy_force_min_damage":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "enemy_force_min_damage", self.current_turn, turns=turns, source=attack_name)
                self._append_effect_event(effect_events, "Der nächste gegnerische Angriff verursacht nur Mindestschaden.")
            elif eff_type == "reactive_evolution":
                if _find_active_effect(self.active_effects, target_id, "reactive_evolution") is None:
                    _append_active_effect(
                        self.active_effects,
                        target_id,
                        "reactive_evolution",
                        self.current_turn,
                        amount=int(effect.get("amount", 0) or 0),
                        max_stacks=int(effect.get("max_stacks", 1) or 1),
                        stacks=0,
                        source=attack_name,
                    )
                self._append_effect_event(effect_events, "Reaktive Evolution analysiert eingehende Treffer.")
            elif eff_type == "disable_enemy_heal_if_bleeding":
                turns = max(1, int(effect.get("turns", 1) or 1))
                _append_active_effect(self.active_effects, target_id, "disable_enemy_heal_if_bleeding", self.current_turn, turns=turns, source=attack_name)
                self._append_effect_event(effect_events, "Blutende Gegner können vorerst nicht heilen.")
            elif eff_type == "copy_last_enemy_special":
                copied_name = str((last_enemy_special_entry or {}).get("attack_name") or "").strip()
                if copied_name:
                    self._append_effect_event(effect_events, f"Gedankenkontrolle übernimmt {copied_name}.")
            elif eff_type == "heal_from_target_dot":
                dot_type = str(effect.get("dot_type") or "bleeding").strip().lower()
                heal_amount = _sum_target_dot_damage(self.active_effects, target_id, dot_type)
                healed_now = self.heal_player(self.current_turn, heal_amount)
                if healed_now > 0:
                    self._append_effect_event(effect_events, f"Symbiontenheilung: +{healed_now} HP aus { _dot_label(dot_type) }.")
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
                amount = _effect_amount(effect, "amount", 0)
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
                amount = _effect_amount(effect, "amount", 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                reflect_flat = effect.get("flat", 0)
                self.queue_incoming_modifier(
                    target_id,
                    percent=reduce_percent,
                    reflect=reflect_ratio,
                    flat=0,
                    turns=1,
                    source=attack_name,
                )
                if self.incoming_modifiers.get(target_id):
                    self.incoming_modifiers[target_id][-1]["reflect_flat"] = reflect_flat
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                flat_text = f" und {_effect_amount_label(reflect_flat)} fixer Rückschaden ausgelöst werden" if _range_pair(reflect_flat)[1] > 0 else ""
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert, {reflect_pct}% des verhinderten Schadens werden zurückgeworfen{flat_text}.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                max_store = effect.get("max_store")
                self.queue_incoming_modifier(
                    target_id,
                    percent=percent,
                    store_ratio=1.0,
                    max_store=(int(max_store) if max_store is not None else None),
                    turns=1,
                    source=attack_name,
                )
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
                    max_damage = cap_setting
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {_effect_amount_label(max_damage)} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = effect.get("counter", 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                self.special_lock_next_turn[target_id] = max(self.special_lock_next_turn.get(target_id, 0), turns)
                self._append_effect_event(effect_events, f"Spezialfähigkeiten des Gegners sind für {turns} Runde(n) gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = effect.get("heal", 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": self.current_turn})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: Heilt sich in den nächsten {turns} Runden jeweils um {heal} HP.")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                _apply_mix_heal_or_max_effect(self, target_id, effect, effect_events)
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = effect.get("counter", 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    landing_attack=(effect.get("landing_attack") if isinstance(effect.get("landing_attack"), dict) else None),
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=_maybe_int(attack.get("cooldown_turns", 0)) or 0,
                )

            # Kein separater Log-Eintrag mehr – Effekt wird in der Angriffszeile signalisiert

        if attack_hits_enemy and int(actual_damage or 0) > 0:
            for effect in effects:
                if str(effect.get("type") or "").strip().lower() == "finisher_below_hp":
                    threshold = max(0, int(effect.get("threshold", 0) or 0))
                    if self._hp_for(defender_id) <= threshold:
                        if defender_id == self.player1_id:
                            self.player1_hp = 0
                        else:
                            self.player2_hp = 0
                        self._append_effect_event(effect_events, f"Finisher: Gegner unter {threshold} HP und sofort besiegt.")
                        break

        _record_last_special_attack(
            self.last_special_attack,
            actor_id=self.current_turn,
            attack_index=attack_index,
            attacks=attacks,
            attack=attack,
            card_name=str(current_card.get("name") or attacker_card),
            attack_name=str(attack_name),
            is_reload_action=is_reload_action or attack_cancelled_by_heal_curse,
            is_forced_landing=is_forced_landing,
        )

        if self.special_lock_next_turn.get(self.current_turn, 0) > 0:
            self.special_lock_next_turn[self.current_turn] = max(0, self.special_lock_next_turn.get(self.current_turn, 0) - 1)

        if (not is_forced_landing) and (not attack_cancelled_by_heal_curse):
            if not is_reload_action and attack.get("requires_reload"):
                self.set_reload_needed(self.current_turn, attack_index, True)

            # COOLDOWN-SYSTEM: Kartenspezifisch oder für starke Attacken
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = _resolve_final_damage_cooldown_turns(attack, actual_damage)
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(attack_index, 0)
                self.attack_cooldowns[previous_turn][attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, _maybe_int(attack.get("cooldown_from_burning_plus", 0)) or 0)
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and custom_cooldown_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(attack_index, 0)
                self.attack_cooldowns[previous_turn][attack_index] = max(current_cd, custom_cooldown_turns)
            elif self.is_strong_attack(base_damage, damage_buff):
                # Starke Attacke - 2 Züge Cooldown
                previous_turn = self.current_turn
                self.start_attack_cooldown(previous_turn, attack_index)
        else:
            forced_landing = cast(dict[str, object], forced_landing_attack)
            landing_cd_index = _maybe_int(forced_landing.get("cooldown_attack_index"))
            landing_cd_turns = _maybe_int(forced_landing.get("cooldown_turns", 0)) or 0
            if landing_cd_index is not None and landing_cd_index >= 0 and landing_cd_turns > 0:
                previous_turn = self.current_turn
                current_cd = self.attack_cooldowns[previous_turn].get(landing_cd_index, 0)
                self.attack_cooldowns[previous_turn][landing_cd_index] = max(current_cd, landing_cd_turns)

        _prepend_action_context_events(
            effect_events,
            action_type=action_type,
            actual_damage=int(actual_damage or 0),
            miss_reason=miss_reason,
            heal_amount=_extract_heal_amount_from_events(effect_events),
            is_reload_action=is_reload_action,
        )

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
            await _log_event_safe(
                "fight_result",
                guild_id=self._durable_guild_id,
                channel_id=self._durable_channel_id,
                thread_id=self._durable_channel_id,
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=winner_id,
                target_user_id=loser_id,
                hero_name=winner_card,
                payload={
                    "winner_id": int(winner_id or 0),
                    "winner_hero": winner_card,
                    "loser_id": int(loser_id or 0),
                    "loser_hero": loser_card,
                    "rounds": int(self.round_counter),
                },
            )
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
            if should_carry_cooldowns("normal"):
                _save_cooldown_carryover(
                    "normal",
                    self.player1_id,
                    str(self.player1_card.get("name") or ""),
                    [atk for atk in self.player1_card.get("attacks", []) if isinstance(atk, dict)],
                    self.attack_cooldowns.get(self.player1_id, {}),
                )
                _save_cooldown_carryover(
                    "normal",
                    self.player2_id,
                    str(self.player2_card.get("name") or ""),
                    [atk for atk in self.player2_card.get("attacks", []) if isinstance(atk, dict)],
                    self.attack_cooldowns.get(self.player2_id, {}),
                )
            self.stop()
            return

        # Nächster Spieler
        previous_turn = self.current_turn
        _consume_turn_end_decay_effects(self.active_effects, previous_turn)
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
        # Bereits liegende DoT-Effekte des Bots ticken vor dessen Angriff.
        effect_events: list[str] = []
        defender_id = self.player1_id
        pre_burn_total, dot_tick_events = _apply_dot_ticks_for_applier(
            self.active_effects,
            target_id=defender_id,
            applier_id=0,
            damage_callback=(lambda amount: self._apply_non_heal_damage(defender_id, amount)),
        )
        for event_text in dot_tick_events:
            self._append_effect_event(effect_events, event_text)

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
        standard_idx = _standard_attack_index(attacks)
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
        effective_attack = dict(attack)
        effective_attack["damage"] = base_damage
        is_damaging_attack = _attack_has_direct_damage(effective_attack)
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
            restricted_bonus_now, restricted_bonus_effect = _consume_restricted_flat_damage_bonus(
                self.active_effects,
                0,
                attack,
                attack_index=attack_index,
                standard_index=standard_idx,
            )
            if restricted_bonus_now > 0:
                damage_buff += restricted_bonus_now
                applied_flat_bonus_now += max(0, restricted_bonus_now)
                source = str((restricted_bonus_effect or {}).get("source") or "Verstärkung")
                self._append_effect_event(effect_events, f"{source}: +{restricted_bonus_now} Schaden auf diesen Angriff.")
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
        is_reload_action = bool((not is_forced_landing) and attack.get("requires_reload") and self.is_reload_needed(0, attack_index))
        attack_name = str(attack.get("reload_name") or "Nachladen") if is_reload_action else attack["name"]
        guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)
        force_min_damage = _force_min_damage_active(self.active_effects, self.current_turn)
        attack_cancelled_by_heal_curse = False
        heal_curse_effect = _find_active_effect(self.active_effects, self.current_turn, "heal_curse")
        if (not is_reload_action) and heal_curse_effect is not None and _attack_has_heal_component(attack):
            attack_cancelled_by_heal_curse = True
            curse_damage = max(0, _effect_int(heal_curse_effect, "damage", 0))
            curse_source = str(heal_curse_effect.get("source") or "Hex-Fluch")
            turns_left = max(0, _effect_int(heal_curse_effect, "turns", 1) - 1)
            heal_curse_effect["turns"] = turns_left
            if turns_left <= 0:
                _remove_active_effect(self.active_effects, self.current_turn, heal_curse_effect)
            if curse_damage > 0:
                self._apply_non_heal_damage_with_event(
                    effect_events,
                    self.current_turn,
                    curse_damage,
                    source=curse_source,
                    self_damage=True,
                )
            self._append_effect_event(effect_events, "Heilung wurde blockiert. Diese Attacke verbraucht keinen Cooldown.")
        action_type = _attack_kind_label(
            attack,
            attacks=attacks,
            attack_index=attack_index,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
        )
        miss_reason: str | None = None

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
            current_attack_profile = dict(attack)
            current_attack_profile["damage"] = base_damage
            _min_threshold_damage, max_damage_threshold = _attack_total_damage_range(
                current_attack_profile,
                max_only_bonus=0,
                flat_bonus=damage_buff,
            )
            if blind_miss:
                miss_reason = f"durch Blendung ({int(round(blind_chance * 100))}% Verfehlchance)"
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
                    miss_reason = "durch Verwirrung, stattdessen Selbsttreffer"
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
                    if (
                        defender_has_stealth
                        and actual_damage > 0
                        and not guaranteed_hit
                        and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    ):
                        actual_damage = 0
                        is_critical = False
                        bot_hits_enemy = False
                        miss_reason = "durch Tarnung"
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
                if (
                    defender_has_stealth
                    and actual_damage > 0
                    and not guaranteed_hit
                    and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                ):
                    actual_damage = 0
                    is_critical = False
                    bot_hits_enemy = False
                    miss_reason = "durch Tarnung"
                    self.consume_stealth(self.player1_id)

            if bot_hits_enemy and actual_damage > 0:
                before_any_override = int(actual_damage)
                actual_damage, any_override_effect = _consume_next_attack_damage_override(
                    self.active_effects,
                    0,
                    actual_damage,
                )
                if any_override_effect is not None and actual_damage != before_any_override:
                    source = str(any_override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Angriffsschaden {before_any_override} -> {actual_damage}.")
                before_override = int(actual_damage)
                actual_damage, override_effect = _consume_next_standard_damage_override(
                    self.active_effects,
                    0,
                    attack_index=attack_index,
                    standard_index=standard_idx,
                    current_damage=actual_damage,
                )
                if override_effect is not None and actual_damage != before_override:
                    source = str(override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Standardangriff {before_override} -> {actual_damage} Schaden.")
                before_capped = int(actual_damage)
                actual_damage, capped_bonus, capped_effect = _consume_capped_damage_multiplier(self.active_effects, 0, actual_damage)
                if capped_effect is not None and capped_bonus > 0:
                    source = str(capped_effect.get("source") or "Geheimakte")
                    self._append_effect_event(effect_events, f"{source}: Schaden {before_capped} -> {actual_damage} (+{capped_bonus}, max. +{_effect_amount_label(capped_effect.get('max_bonus', 0))}).")
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
                        _outgoing_reduction_effect_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        0,
                        overflow_self_damage,
                        source=_overflow_recoil_source(modifier_source or None),
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False

                bypass_all_defense = bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(self.player1_id, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    self.player1_id,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(self.player1_id)),
                    ignore_all_defense=bypass_all_defense,
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
                    readable_source = _readable_effect_source((incoming_modifier or {}).get("source"))
                    miss_reason = f"durch {readable_source}" if readable_source else "durch Ausweichen"
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
                hit_heal, heal_effect = _consume_attack_heal(self.active_effects, 0)
                if hit_heal > 0:
                    healed_now = self.heal_player(0, hit_heal)
                    if healed_now > 0:
                        self._append_effect_event(effect_events, f"{str((heal_effect or {}).get('source') or 'Trefferheilung')}: Treffer heilt {healed_now} HP.")
            if not bot_hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = _resolve_self_damage_value(attack.get("self_damage", 0))
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
            heal_chance = float(attack.get("heal_chance", 1.0) or 1.0)
            if random.random() <= heal_chance:
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
            if _apply_word_runtime_effect(self, effect_events, eff_type=str(eff_type), target_id=target_id, attack_name=attack_name):
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif _is_dot_effect_type(eff_type):
                duration, burn_damage = _append_dot_effect(
                    self.active_effects,
                    target_id=target_id,
                    attacker_id=0,
                    effect_type=eff_type,
                    duration=effect.get("duration"),
                    damage=effect.get("damage"),
                )
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"{_dot_label(eff_type)} aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == 'confusion':
                self.set_confusion(target_id, 0)
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = _effect_amount(effect, "amount", 0)
                uses = int(effect.get("uses", 1) or 1)
                _queue_flat_damage_boost(
                    self,
                    effect_events,
                    target_id=target_id,
                    applier_id=0,
                    attack_name=str(attack_name),
                    amount=amount,
                    uses=uses,
                    effect=effect,
                )
            elif eff_type == "attack_heal":
                uses = int(effect.get("uses", 1) or 1)
                _append_active_effect(self.active_effects, target_id, "attack_heal", 0, amount=effect.get("amount", 0), uses=uses, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Trefferheilung aktiv: +{_effect_amount_label(effect.get('amount', 0))} HP für {uses} eigene Treffer."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
            elif eff_type == "capped_damage_multiplier":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "capped_damage_multiplier",
                    0,
                    multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                    max_bonus=effect.get("max_bonus", 0),
                    uses=max(1, int(effect.get("uses", 1) or 1)),
                    source=attack_name,
                )
            elif eff_type == "next_standard_damage_override":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "next_standard_damage_override",
                    0,
                    turns=max(1, int(effect.get("turns", 1) or 1)),
                    damage=effect.get("damage", 0),
                    source=attack_name,
                )
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
                amount = _effect_amount(effect, "amount", 0)
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
                amount = _effect_amount(effect, "amount", 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                reflect_flat = effect.get("flat", 0)
                self.queue_incoming_modifier(
                    target_id,
                    percent=reduce_percent,
                    reflect=reflect_ratio,
                    flat=0,
                    turns=1,
                    source=attack_name,
                )
                if self.incoming_modifiers.get(target_id):
                    self.incoming_modifiers[target_id][-1]["reflect_flat"] = reflect_flat
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                flat_text = f" und {_effect_amount_label(reflect_flat)} fixer Rückschaden ausgelöst werden" if _range_pair(reflect_flat)[1] > 0 else ""
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert, {reflect_pct}% des verhinderten Schadens werden zurückgeworfen{flat_text}.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                max_store = effect.get("max_store")
                self.queue_incoming_modifier(
                    target_id,
                    percent=percent,
                    store_ratio=1.0,
                    max_store=(int(max_store) if max_store is not None else None),
                    turns=1,
                    source=attack_name,
                )
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
                    max_damage = cap_setting
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {_effect_amount_label(max_damage)} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = effect.get("counter", 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                self.special_lock_next_turn[target_id] = max(self.special_lock_next_turn.get(target_id, 0), turns)
                self._append_effect_event(effect_events, f"Spezialfähigkeiten des Gegners sind für {turns} Runde(n) gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = effect.get("heal", 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": 0})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: Heilt sich in den nächsten {turns} Runden jeweils um {heal} HP.")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                _apply_mix_heal_or_max_effect(self, target_id, effect, effect_events)
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = effect.get("counter", 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    landing_attack=(effect.get("landing_attack") if isinstance(effect.get("landing_attack"), dict) else None),
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )
        # Kein separater Log-Eintrag – Effekte werden inline in der Angriffszeile angezeigt

        _prepend_action_context_events(
            effect_events,
            action_type=action_type,
            actual_damage=int(actual_damage or 0),
            miss_reason=miss_reason,
            heal_amount=_extract_heal_amount_from_events(effect_events),
            is_reload_action=is_reload_action,
        )

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

        if self.special_lock_next_turn.get(0, 0) > 0:
            self.special_lock_next_turn[0] = max(0, self.special_lock_next_turn.get(0, 0) - 1)

        if not is_forced_landing:
            # Cooldown für Bot-Attacke
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = _resolve_final_damage_cooldown_turns(attack, actual_damage)
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                current_cd = self.attack_cooldowns[0].get(attack_index, 0)
                self.attack_cooldowns[0][attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and custom_cooldown_turns > 0:
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
            await _log_event_safe(
                "fight_result",
                guild_id=self._durable_guild_id,
                channel_id=self._durable_channel_id,
                thread_id=self._durable_channel_id,
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=winner_id,
                target_user_id=loser_id,
                hero_name=winner_card,
                payload={
                    "winner_id": int(winner_id or 0),
                    "winner_hero": winner_card,
                    "loser_id": int(loser_id or 0),
                    "loser_hero": loser_card,
                    "rounds": int(self.round_counter),
                },
            )
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
        try:
            await message.edit(embed=battle_embed, view=self)
        except (discord.NotFound, discord.Forbidden):
            # Channel/Thread wurde gelöscht – Kampf still beenden.
            return

class CardSelectView(RestrictedView):
    def __init__(self, user_id, karten_liste, anzahl):
        super().__init__(timeout=90)
        self.user_id = user_id
        self.value = None
        self.anzahl = int(anzahl)
        self.user_cards = list(karten_liste)
        self.base_groups = _group_owned_cards_for_current_mode(self.user_cards)
        self.selected_base_name: str | None = None
        options = [SelectOption(label=_group_option_label(group)[:100], value=str(group.get("base_name") or "")) for group in self.base_groups[:25]]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(placeholder=f"Wähle {anzahl} Karte(n)...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    def _variant_rows_for_selected_base(self) -> list[tuple[str, int]]:
        if not self.selected_base_name:
            return []
        return _owned_variant_rows_for_base(self.user_cards, self.selected_base_name)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann Karten wählen!", ephemeral=True)
            return
        selected_value = str(self.select.values[0] or "").strip()
        if selected_value == "__none__":
            await interaction.response.send_message("❌ Es sind aktuell keine nutzbaren Karten verfügbar.", ephemeral=True)
            return
        if self.selected_base_name is None:
            self.selected_base_name = selected_value
            variant_rows = self._variant_rows_for_selected_base()
            if len(variant_rows) <= 1:
                if not variant_rows:
                    await interaction.response.send_message("❌ Für diese Karte wurde kein nutzbarer Style gefunden.", ephemeral=True)
                    return
                self.value = [variant_rows[0][0]]
                self.stop()
                await interaction.response.defer()
                return
            self.select.placeholder = f"Wähle den Style für {self.selected_base_name}..."
            self.select.options = [
                SelectOption(
                    label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                    value=variant_name,
                )
                for variant_name, amount in variant_rows[:25]
            ]
            await interaction.response.edit_message(
                content=f"Wähle den Style für **{self.selected_base_name}**:",
                view=self,
            )
            return
        self.value = [selected_value]
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
        selected_base_name: str | None = None,
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
        self.selected_base_name = str(selected_base_name or "").strip() or None
        grouped_cards = group_owned_cards_by_base([(str(name), 1) for name in self.challenged_card_options], cards=karten)
        self._grouped_cards = grouped_cards
        if self.selected_base_name:
            variant_rows = exact_variant_names_with_amounts(
                [(str(name), 1) for name in self.challenged_card_options],
                self.selected_base_name,
                cards=karten,
            )
            options = [
                SelectOption(
                    label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                    value=variant_name,
                )
                for variant_name, amount in variant_rows[:25]
            ]
        else:
            options = [
                SelectOption(label=_group_option_label(group)[:100], value=str(group.get("base_name") or ""))
                for group in grouped_cards[:25]
            ]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder=(
                f"Wähle den Style für {self.selected_base_name}..."
                if self.selected_base_name
                else "Wähle deine Karte für den 1v1 Kampf..."
            ),
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
            "selected_base_name": self.selected_base_name,
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
        if self.selected_base_name is None:
            self.selected_base_name = selected_name
            variant_rows = exact_variant_names_with_amounts(
                [(str(name), 1) for name in self.challenged_card_options],
                self.selected_base_name,
                cards=karten,
            )
            if len(variant_rows) > 1:
                self.select.placeholder = f"Wähle den Style für {self.selected_base_name}..."
                self.select.options = [
                    SelectOption(
                        label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                        value=variant_name,
                    )
                    for variant_name, amount in variant_rows[:25]
                ]
                await interaction.response.edit_message(
                    content=f"Wähle den Style für **{self.selected_base_name}**:",
                    view=self,
                )
                return
            if variant_rows:
                selected_name = variant_rows[0][0]
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
            return "?"

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
            return "?"

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
    def __init__(self, requester_id: int, guild: discord.Guild, *, item_label: str = "Infinitydust"):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.guild = guild
        self.item_label = str(item_label or "Auswahl")
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
        return f"Wähle mehrere Nutzer für {self.item_label}. Die Suche steht immer ganz oben."

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
            names.append(safe_display_name(member or f"<@{user_id}>", fallback="Unbekannt"))
        return ", ".join(names)

    def _summary_embed(self) -> discord.Embed:
        available_count = len(self._available_members())
        selected_count = len(self.selected_user_ids)
        title = f"💎 Multi-Auswahl für {self.item_label}" if self.item_label == "Infinitydust" else f"Multi-Auswahl für {self.item_label}"
        embed = discord.Embed(
            title=title,
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
            SelectOption(label="👥 Ganze Rolle wählen", value="role"),
            SelectOption(label="✅ Fertig", value="done"),
        ]
        for member in self._available_members()[:22]:
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
            f"✅ {safe_display_name(member, fallback=f'<@{user_id}>')} hinzugefügt.",
            ephemeral=True,
        )

    async def _handle_role_selection(self, interaction: discord.Interaction) -> None:
        """Entweder-Oder: gewählte Rolle ersetzt die Auswahl mit allen ihren Mitgliedern."""
        role_view = GiveOpRoleSelectView(self.requester_id)
        await interaction.response.send_message(
            "Wähle eine Rolle – alle Mitglieder erhalten dann die Auswahl.",
            view=role_view,
            ephemeral=True,
        )
        await role_view.wait()
        if role_view.value is None:
            return
        role = self.guild.get_role(int(role_view.value))
        if role is None:
            await interaction.followup.send("❌ Rolle nicht gefunden.", ephemeral=True)
            return
        member_ids = [m.id for m in role.members if not m.bot]
        if not member_ids:
            await interaction.followup.send(
                f"❌ Die Rolle **{role.name}** hat keine (Nicht-Bot-)Mitglieder.",
                ephemeral=True,
            )
            return
        self.selected_user_ids = member_ids
        self.value = list(self.selected_user_ids)
        self.stop()
        if self._message is not None:
            try:
                await self._message.edit(
                    content=f"👥 Rolle gewählt: **{role.name}** ({len(member_ids)} Mitglieder).",
                    embed=None,
                    view=None,
                )
            except discord.HTTPException:
                pass

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
        if selected == "role":
            await self._handle_role_selection(interaction)
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
                    await channel.send(
                        f"\u2139\ufe0f Dieser Thread wird in {int(delay)} Sekunden geschlossen."
                    )
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
        thread_name = safe_thread_name("Privater Kampf:", safe_display_name(interaction.user, fallback="Nutzer"))
        if challenged is not None:
            thread_name = safe_thread_name(
                "Privater Kampf:",
                safe_display_name(interaction.user, fallback="Nutzer"),
                "vs",
                safe_display_name(challenged, fallback="Nutzer"),
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
        thread_name = safe_thread_name("Mission:", safe_display_name(interaction.user, fallback="Nutzer"))
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
    send_channel = await _resolve_thread_channel_or_fallback(thread_id, interaction.channel)
    if thread_id and _thread_id_for_channel(send_channel) != thread_id:
        await interaction.followup.send("❌ Der private Kampf-Thread ist nicht mehr verfügbar. Bitte erneut herausfordern.", ephemeral=True)
        await _maybe_delete_fight_thread(thread_id, thread_created)
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
            send_channel,
            content=f"❌ Karte von {challenger.mention} nicht gefunden. Bitte erneut herausfordern.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    gegner_karten_liste = _sort_user_cards_like_karten(
        _filter_owned_cards_for_current_mode(await get_user_karten(challenged.id))
    )
    if not gegner_karten_liste:
        await _safe_send_channel(
            interaction,
            send_channel,
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
        send_channel,
        content=(
            f"{challenged.mention}, wähle deine Karte für den 1v1 Kampf:\n"
            f"Herausforderer-Karte: **{_fight_challenge_card_label(challenger_card_name)}**"
        ),
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
    send_channel = await _resolve_thread_channel_or_fallback(thread_id, interaction.channel)
    if thread_id and _thread_id_for_channel(send_channel) != thread_id:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content="❌ Der private Kampf-Thread ist nicht mehr verfügbar. Bitte erneut herausfordern.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    challenger = await _get_member_safe(interaction.guild, challenger_id)
    challenged = await _get_member_safe(interaction.guild, challenged_id)
    if not challenger or not challenged:
        await _safe_send_channel(
            interaction,
            send_channel,
            content="❌ Nutzer nicht gefunden. Bitte erneut herausfordern.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    challenger_card = await get_karte_by_name(challenger_card_name)
    challenged_card = await get_karte_by_name(challenged_card_name)
    if not challenger_card or not challenged_card:
        await _safe_send_channel(
            interaction,
            send_channel,
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
    if should_carry_cooldowns("normal"):
        p1_carried = _load_cooldown_carryover(
            "normal",
            challenger.id,
            str(challenger_card.get("name") or ""),
            [atk for atk in challenger_card.get("attacks", []) if isinstance(atk, dict)],
        )
        if p1_carried:
            battle_view.attack_cooldowns[challenger.id].update(p1_carried)
        p2_carried = _load_cooldown_carryover(
            "normal",
            challenged.id,
            str(challenged_card.get("name") or ""),
            [atk for atk in challenged_card.get("attacks", []) if isinstance(atk, dict)],
        )
        if p2_carried:
            battle_view.attack_cooldowns[challenged.id].update(p2_carried)
    log_message = await _safe_send_channel(
        interaction,
        send_channel,
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
    battle_message = await _safe_send_channel(interaction, send_channel, embed=embed, view=battle_view)
    if battle_message is not None:
        await battle_view.persist_session(send_channel, status="active", battle_message=battle_message)


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
    card_name = str(reward_card.get("name") or "?")
    if is_new_card:
        embed = discord.Embed(
            title=game_ui_texts.MISSION_SUCCESS_TITLE_NEW_CARD,
            description=game_ui_texts.MISSION_SUCCESS_DESC_NEW_CARD.format(waves=total_waves, card=card_name),
            color=reward_color,
        )
        if reward_card.get("bild"):
            embed.set_image(url=str(reward_card["bild"]))
        _add_attack_info_field(embed, reward_card)
        return embed
    embed = discord.Embed(
        title=game_ui_texts.MISSION_SUCCESS_TITLE_DUST,
        description=game_ui_texts.MISSION_SUCCESS_DESC_DUST.format(waves=total_waves),
        color=reward_color,
    )
    embed.add_field(
        name=game_ui_texts.MISSION_SUCCESS_DUST_FIELD_NAME,
        value=game_ui_texts.MISSION_SUCCESS_DUST_FIELD_VALUE.format(card=card_name),
        inline=False,
    )
    # Req. 8.2/8.3: Bei Dust-Belohnung (Karte bereits besessen) erscheint das Dust-Bild
    # ausschließlich als Thumbnail; kein großes Bild.
    dust_image_url, dust_thumbnail_url = _item_media_urls("infinitydust")
    thumb = dust_thumbnail_url or dust_image_url
    if thumb:
        embed.set_thumbnail(url=thumb)
    _add_attack_info_field(embed, reward_card)
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
    user_karten = _sort_user_cards_like_karten(
        _filter_owned_cards_for_current_mode(await get_user_karten(interaction.user.id))
    )
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
    intro_embed = _build_mission_embed(
        _dict_str_any(mission_data),
        user_already_owns_reward=await _user_already_owns_card(
            interaction.user.id,
            _dict_str_any(mission_data).get("reward_card"),
        ),
    )
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
    encounters = _mission_encounters(mission_data)
    total_waves = max(wave_num, len(encounters), int(mission_state.get("total_waves", mission_data.get("waves", 1)) or 1))
    mission_data["waves"] = total_waves
    if wave_num == 1 and not bool(mission_state.get("mission_counted")) and not bool(mission_state.get("is_admin", False)):
        await increment_mission_count(interaction.user.id)
        mission_state["mission_counted"] = True
        mission_data["mission_counted"] = True
    mission_enemy = _mission_encounter_for_wave(mission_data, wave_num)
    bot_card = cast(
        CardData,
        mission_enemy or random_gameplay_card(karten, alpha_enabled=ALPHA_PHASE_ENABLED, context="default"),
    )
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
    carry_hp = _maybe_int(mission_state.get("player_hp"))
    carry_max_hp = _maybe_int(mission_state.get("player_max_hp"))
    if bool(mission_state.get("full_heal")):
        mission_view.player_hp = mission_view.player_max_hp
    elif carry_hp is not None:
        if carry_max_hp is not None and carry_max_hp > 0:
            mission_view.player_max_hp = max(mission_view.player_max_hp, carry_max_hp)
        mission_view.player_hp = min(mission_view.player_max_hp, max(1, int(carry_hp)))
    carry_bot_hp = _maybe_int(mission_state.get("bot_hp"))
    carry_bot_max_hp = _maybe_int(mission_state.get("bot_max_hp"))
    if carry_bot_max_hp is not None and carry_bot_max_hp > 0:
        mission_view.bot_max_hp = max(mission_view.bot_max_hp, carry_bot_max_hp)
    if carry_bot_hp is not None:
        mission_view.bot_hp = min(mission_view.bot_max_hp, max(1, int(carry_bot_hp)))
    is_boss_wave = bool(wave_num >= total_waves)
    if should_carry_cooldowns("mission") and should_carry_mission_cooldowns(is_boss_wave=is_boss_wave):
        carried_cooldowns = _cooldowns_from_attack_names(
            [atk for atk in mission_view.attacks if isinstance(atk, dict)],
            mission_state.get("player_attack_cooldowns_by_name"),
        )
        if carried_cooldowns:
            mission_view.user_attack_cooldowns.update(carried_cooldowns)
            mission_view.update_attack_buttons_mission()
    if mission_enemy is not None:
        wave_intro = discord.Embed(
            title=f"Welle {wave_num}/{total_waves}: {bot_card.get('name', 'Gegner')}",
            description=str(bot_card.get("beschreibung") or "Der nächste Gegner stellt sich dir in den Weg."),
            color=0x8E44AD if str(bot_card.get("mission_boss") or "") == "maestro" else 0x2F3136,
        )
        wave_intro.add_field(name="HP", value=str(bot_card.get("hp", "?")), inline=True)
        attack_names = [str(atk.get("name") or "?") for atk in bot_card.get("attacks", []) if isinstance(atk, dict)]
        if attack_names:
            wave_intro.add_field(name="Attacken", value=", ".join(attack_names[:4]), inline=False)
        await _safe_send_channel(interaction, interaction.channel, embed=wave_intro)
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
        # Req. 13: Challenge-AFK-Timer endet mit der Annahme.
        try:
            await afk_tracker.delete_state(f"challenge:{self.request_id}")
        except Exception:
            logging.exception("Failed to delete challenge AFK state on accept")
        await interaction.response.defer()
        thread: discord.Thread | None = None
        try:
            if self.thread_id:
                if isinstance(interaction.channel, discord.Thread) and interaction.channel.id == self.thread_id:
                    thread = interaction.channel
                else:
                    cached_thread = bot.get_channel(self.thread_id)
                    if isinstance(cached_thread, discord.Thread):
                        thread = cached_thread
                    else:
                        fetched_thread = await _fetch_channel_safe(self.thread_id)
                        if isinstance(fetched_thread, discord.Thread):
                            thread = fetched_thread
        except Exception:
            logging.exception("Unexpected error")
        if thread is None:
            challenged_member = interaction.guild.get_member(self.challenged_id) if interaction.guild is not None else None
            replacement_thread = await _create_required_private_fight_thread(interaction, challenged=challenged_member)
            if replacement_thread is None:
                await interaction.followup.send("❌ Der private Kampf-Thread konnte nicht wiederhergestellt werden. Bitte erneut herausfordern.", ephemeral=True)
                return
            thread = replacement_thread
            self.thread_id = replacement_thread.id
            self.thread_created = True
        try:
            await thread.add_user(interaction.user)
        except Exception:
            logging.exception("Failed to add challenged user to fight thread")
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
        try:
            await afk_tracker.delete_state(f"challenge:{self.request_id}")
        except Exception:
            logging.exception("Failed to delete challenge AFK state on decline")
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

    @ui.button(label="Abbrechen", style=discord.ButtonStyle.danger, custom_id="fight_challenge:cancel")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        # Req. 12.1/12.3: sowohl Herausforderer als auch Herausgeforderter dürfen abbrechen.
        if interaction.user.id not in (self.challenger_id, self.challenged_id):
            await interaction.response.send_message("Nur Herausforderer oder Herausgeforderter können abbrechen!", ephemeral=True)
            return
        if not await claim_fight_request(self.request_id, "cancelled"):
            await interaction.response.send_message("❌ Diese Kampf-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        # Req. 12.5: AFK-Pings sofort stoppen, bevor weitere Cleanup-Verarbeitung läuft.
        try:
            await afk_tracker.delete_state(f"challenge:{self.request_id}")
        except Exception:
            logging.exception("Failed to delete AFK state on challenge cancel")
        await interaction.response.send_message("Challenge abgebrochen.", ephemeral=True)
        try:
            await _safe_send_channel(
                interaction,
                interaction.channel,
                content=(
                    f"<@{self.challenger_id}> <@{self.challenged_id}> — "
                    f"die Challenge wurde von {interaction.user.mention} abgebrochen."
                ),
            )
        except Exception:
            logging.exception("Failed to notify challenge cancellation")
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
            await interaction.response.send_message(CLOSE_PERMISSION_DENIED, ephemeral=False)
            return
        await interaction.response.send_message(THREAD_CLOSING, ephemeral=False)
        self.stop()
        try:
            await update_managed_thread_status(self.thread.id, "deleted")
            await self.thread.delete()
            await self.clear_durable_registration()
        except discord.NotFound:
            await self.clear_durable_registration()
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
            await self.clear_durable_registration()
        except discord.NotFound:
            await self.clear_durable_registration()
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
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=False)
            return
        if interaction.user.id in self._bug_reported_by:
            await interaction.response.send_message("Du hast bereits gemeldet, dass es einen Bug gab.", ephemeral=False)
            return
        self._bug_reported_by.add(interaction.user.id)
        if not BUG_REPORT_TALLY_URL or "REPLACE_ME" in BUG_REPORT_TALLY_URL:
            await interaction.response.send_message(BUG_FORM_NOT_CONFIGURED, ephemeral=False)
            return

        await _send_basti_log_dm(
            self.battle_log_text,
            context_lines=[
                "Bug-Button wurde geklickt.",
                f"Guild: {self.guild.name if self.guild else 'Unbekannt'}",
                f"Kanal/Thread: {_channel_mention_or_fallback(self.channel)}",
                f"Gemeldet von: {safe_display_name(interaction.user, fallback='Unbekannt')} ({interaction.user.id})",
                f"View: {self.durable_context_label()}",
            ],
        )
        actor_name = safe_display_name(interaction.user, fallback="Unbekannt")
        await _log_event_safe(
            "fight_feedback_bug",
            guild_id=getattr(self.guild, "id", 0),
            channel_id=getattr(self.channel, "id", 0),
            thread_id=_thread_id_for_channel(self.channel),
            actor_user_id=interaction.user.id,
            payload={
                "action": "bug",
                "actor_name": actor_name,
                "view_kind": self.durable_context_label(),
            },
        )
        await interaction.response.send_message(
            content=f"🐞 {actor_name} hat **Es gab einen Bug** gewählt. Bitte fülle dieses Formular aus:",
            view=BugReportLinkView(),
            ephemeral=False,
        )
        if self.keep_open_after_bug and self._auto_close_task and not self._auto_close_task.done():
            self._auto_close_task.cancel()
        await self._maybe_post_admin_close_view()

    @ui.button(label="Kampf-Log per DM", style=discord.ButtonStyle.primary, custom_id="fight_feedback:log")
    async def log_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=False)
            return
        if interaction.user.id in self._log_sent_to:
            await interaction.response.send_message("Ich habe dir den Kampf-Log bereits per DM geschickt.", ephemeral=False)
            return
        if not self.battle_log_text:
            await interaction.response.send_message("Für diesen Kampf ist kein Log verfügbar.", ephemeral=False)
            return
        chunks = self._split_log_for_dm(self.battle_log_text)
        if not chunks:
            await interaction.response.send_message("Für diesen Kampf ist kein Log verfügbar.", ephemeral=False)
            return
        try:
            for idx, chunk in enumerate(chunks, start=1):
                title = "Vollständiger Kampf-Log" if len(chunks) == 1 else f"Vollständiger Kampf-Log ({idx}/{len(chunks)})"
                dm_embed = discord.Embed(title=title, description=chunk, color=0x2F3136)
                await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            await interaction.response.send_message(DM_DISABLED, ephemeral=False)
            return
        except Exception:
            logging.exception("Unexpected error")
            await interaction.response.send_message(DM_LOG_SEND_FAILED, ephemeral=False)
            return
        self._log_sent_to.add(interaction.user.id)
        actor_name = safe_display_name(interaction.user, fallback="Unbekannt")
        await _log_event_safe(
            "fight_feedback_log_dm",
            guild_id=getattr(self.guild, "id", 0),
            channel_id=getattr(self.channel, "id", 0),
            thread_id=_thread_id_for_channel(self.channel),
            actor_user_id=interaction.user.id,
            payload={
                "action": "log_dm",
                "actor_name": actor_name,
                "chunk_count": len(chunks),
            },
        )
        await interaction.response.send_message(
            f"📩 {actor_name} hat **Kampf-Log per DM** gewählt. Der vollständige Log wurde per DM gesendet.",
            ephemeral=False,
        )

    @ui.button(label="Es gab keinen Bug", style=discord.ButtonStyle.danger, row=2, custom_id="fight_feedback:no_bug")
    async def no_bug_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(PARTICIPANTS_OR_ADMINS_ONLY, ephemeral=False)
            return
        if interaction.user.id in self._opted_out_by:
            await interaction.response.send_message("Du hast bereits geantwortet.", ephemeral=False)
            return
        self._opted_out_by.add(interaction.user.id)
        if self.close_after_no_bug and isinstance(self.channel, discord.Thread) and not self._auto_close_blocked():
            self.close_on_idle = True
            if not self.auto_close_delay:
                self.auto_close_delay = _thread_auto_close_delay(DEFAULT_THREAD_AUTO_CLOSE_POLICY)
            if self.auto_close_delay and not self.auto_close_started_at:
                self.auto_close_started_at = int(time.time())
            self._ensure_auto_close_task()
        actor_name = safe_display_name(interaction.user, fallback="Unbekannt")
        await _log_event_safe(
            "fight_feedback_no_bug",
            guild_id=getattr(self.guild, "id", 0),
            channel_id=getattr(self.channel, "id", 0),
            thread_id=_thread_id_for_channel(self.channel),
            actor_user_id=interaction.user.id,
            payload={"action": "no_bug", "actor_name": actor_name},
        )
        await interaction.response.send_message(
            f"✅ {actor_name} hat **Es gab keinen Bug** gewählt. Danke für das Feedback!",
            ephemeral=False,
        )

    @ui.button(
        label="Thread schließen (Admin/Owner)",
        style=discord.ButtonStyle.secondary,
        custom_id="fight_feedback:close_thread",
    )
    async def close_thread_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not isinstance(self.channel, discord.Thread):
            await interaction.response.send_message("Dieser Button ist nur in Threads verfügbar.", ephemeral=False)
            return
        if not await is_admin(interaction):
            await interaction.response.send_message(CLOSE_PERMISSION_DENIED, ephemeral=False)
            return
        await _log_event_safe(
            "fight_feedback_thread_closed",
            guild_id=getattr(self.guild, "id", 0),
            channel_id=getattr(self.channel, "id", 0),
            thread_id=_thread_id_for_channel(self.channel),
            actor_user_id=interaction.user.id,
            payload={"action": "close_thread"},
        )
        await interaction.response.send_message(THREAD_CLOSING, ephemeral=False)
        self.stop()
        try:
            await update_managed_thread_status(self.channel.id, "deleted")
            await self.channel.delete()
            await self.clear_durable_registration()
        except discord.NotFound:
            await self.clear_durable_registration()
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
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    alias_map = {
        "common": "common",
        "normal": "common",
        "gewöhnlich": "common",
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
    card = build_runtime_card(name or "", cards=karten)
    if card is not None:
        return card
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


def _item_media_urls(item_id: str) -> tuple[str, str]:
    item = get_item_by_id(item_id)
    image_url = str((item or {}).get("bild") or "").strip()
    thumbnail_url = str((item or {}).get("thumbnail") or "").strip()
    return image_url, thumbnail_url


def _apply_item_media(embed: discord.Embed, item_id: str, *, image: bool = False, thumbnail: bool = True) -> None:
    image_url, thumbnail_url = _item_media_urls(item_id)
    if image and image_url:
        embed.set_image(url=image_url)
    if thumbnail and thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)


def _build_units_collection_field_value(units: int) -> str | None:
    # In /sammlung nur die Anzahl anzeigen – exakt wie bei Staub ("Anzahl: Nx").
    if units <= 0:
        return None
    return f"Anzahl: {units}x"


def _unit_boss_revive_config() -> tuple[int, str]:
    unit_item = get_item_by_id("unit") or {}
    for effect in unit_item.get("effects", []):
        if not isinstance(effect, dict):
            continue
        if str(effect.get("kind") or "").strip().lower() != "boss_revive":
            continue
        cost = max(0, int(effect.get("cost", 2) or 2))
        mode = str(effect.get("mode") or "revive_continue").strip().lower()
        if mode not in {"revive_continue", "restart_boss"}:
            mode = "revive_continue"
        return cost, mode
    return 2, "revive_continue"

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
# v2.3.5: Günstigste Aufwertung kostet 5 Dust (deckungsgleich mit FUSE_MULTIPLIER_DUST_PER_STEP).
FUSE_DUST_COST = 5
FUSE_HEALTH_BONUS = 10
FUSE_DAMAGE_MAX_BONUS = f"{STANDARD_DAMAGE_UPGRADE_STEP} Standard / {SPECIAL_DAMAGE_UPGRADE_STEP} Spezial"
FUSE_HP_CAP = 200
FUSE_CARD_ACTION_SEARCH = "search"
FUSE_CARD_ACTION_BROWSE_ALL = "browse_all"
FUSE_CARD_ACTION_BACK = "back"
FUSE_CARD_EMPTY = "__none__"
FUSE_OWNER_LOCKED_TEXT = 'Nur die Person, die "/verbessern" gestartet hat, kann dieses Men? benutzen.'


def _attack_upgrade_step(attack: dict) -> int:
    if bool(attack.get("is_standard_attack")):
        return int(STANDARD_DAMAGE_UPGRADE_STEP)
    return int(SPECIAL_DAMAGE_UPGRADE_STEP)


def _attack_upgrade_max_bonus(attack: dict) -> int:
    if bool(attack.get("is_standard_attack")):
        return int(STANDARD_DAMAGE_UPGRADE_STEP) * int(STANDARD_DAMAGE_UPGRADE_MAX_TIMES)
    return int(SPECIAL_DAMAGE_UPGRADE_STEP) * int(SPECIAL_DAMAGE_UPGRADE_MAX_TIMES)


def _normalize_fuse_search_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _search_fuse_card_groups(user_karten: list[tuple[str, int]], query: str) -> list[dict[str, Any]]:
    normalized_query = _normalize_fuse_search_text(query)
    if not normalized_query:
        return []

    grouped_cards = _group_owned_cards_for_current_mode(list(user_karten))
    query_tokens = normalized_query.split()
    scored_matches: list[tuple[int, int, dict[str, Any]]] = []

    for index, group in enumerate(grouped_cards):
        base_name = str(group.get("base_name") or "")
        group_label = _group_option_label(group)
        searchable_values = [
            _normalize_fuse_search_text(base_name),
            _normalize_fuse_search_text(group_label),
            _normalize_fuse_search_text(f"{base_name} {group_label}"),
        ]

        best_score: int | None = None
        for value in searchable_values:
            if not value:
                continue

            score: int | None = None
            if value == normalized_query:
                score = 1000
            elif value.startswith(normalized_query):
                score = 900
            elif normalized_query in value:
                score = 800
            elif query_tokens and all(token in value for token in query_tokens):
                score = 700
            else:
                ratio = SequenceMatcher(None, normalized_query, value).ratio()
                if ratio >= 0.55:
                    score = int(round(ratio * 100))

            if score is not None:
                best_score = score if best_score is None else max(best_score, score)

        if best_score is not None:
            scored_matches.append((best_score, index, group))

    return [group for _score, _index, group in sorted(scored_matches, key=lambda item: (-item[0], item[1]))]


class FuseFlowView(RestrictedView):
    def __init__(self, requester_id: int, *args, **kwargs):
        self.requester_id = int(requester_id)
        super().__init__(*args, **kwargs)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.requester_id:
            await send_interaction_response(interaction, content=FUSE_OWNER_LOCKED_TEXT, ephemeral=True)
            return False
        return True


class FuseFlowModal(RestrictedModal):
    def __init__(self, requester_id: int, *args, **kwargs):
        self.requester_id = int(requester_id)
        super().__init__(*args, **kwargs)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.requester_id:
            await send_interaction_response(interaction, content=FUSE_OWNER_LOCKED_TEXT, ephemeral=True)
            return False
        return True


class DustAmountSelect(ui.Select):
    def __init__(self, user_dust):
        options = []
        if user_dust >= FUSE_DUST_COST:
            options.append(
                SelectOption(
                    label=f"{FUSE_DUST_COST} Infinitydust verwenden",
                    value=str(FUSE_DUST_COST),
                    description=(
                        f"Leben +{FUSE_HEALTH_BONUS} oder Schaden: "
                        f"Std +{STANDARD_DAMAGE_UPGRADE_STEP} / Spez +{SPECIAL_DAMAGE_UPGRADE_STEP}"
                    ),
                    emoji="💎",
                )
            )
        super().__init__(placeholder="Wähle die Infinitydust-Menge...", options=options)

    async def callback(self, interaction: discord.Interaction):
        # Respond quickly to avoid Discord's 3s interaction timeout.
        await defer_interaction(interaction, ephemeral=True)
        try:
            dust_amount = int(self.values[0])
        except Exception:
            await send_interaction_response(interaction, content="❌ Ungültige Auswahl.", ephemeral=True)
            return

        user_karten = await get_user_karten(interaction.user.id)
        if not user_karten:
            await send_interaction_response(interaction, content="❌ Du hast keine Karten zum Verstärken!", ephemeral=True)
            return

        next_view = FuseCardSelectView(interaction.user.id, dust_amount, user_karten)
        if self.view is not None:
            self.view.stop()
        await edit_interaction_message(interaction, embed=next_view.build_embed(), view=next_view)


class FuseCancelButton(ui.Button):
    def __init__(self):
        super().__init__(label="Abbrechen", style=discord.ButtonStyle.danger, row=2)

    async def callback(self, interaction: discord.Interaction):
        await defer_interaction(interaction, ephemeral=True)
        if self.view is not None:
            self.view.stop()
        await edit_interaction_message(
            interaction,
            content="❌ Verstärkung abgebrochen.",
            embed=None,
            view=None,
        )


# DEPRECATED v2.3.0: FuseCardActionSelect is no longer added to the rendered
# FuseCardSelectView (the top action menu was removed in /verbessern). The class
# is kept defined so existing tests in tests/test_combat_rules.py that import it
# continue to load. Do not add it back to the active UI.
class FuseCardActionSelect(ui.Select):
    def __init__(self, parent_view: "FuseCardSelectView"):
        self.parent_view = parent_view
        super().__init__(
            placeholder=parent_view.action_placeholder(),
            min_values=1,
            max_values=1,
            options=parent_view.action_options(),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.handle_action_selection(interaction, str(self.values[0] or ""))


class CardSelect(ui.Select):
    def __init__(self, parent_view: "FuseCardSelectView"):
        self.parent_view = parent_view
        super().__init__(
            placeholder=parent_view.card_placeholder(),
            min_values=1,
            max_values=1,
            options=parent_view.card_options(),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.handle_card_selection(interaction, str(self.values[0] or ""))


# DEPRECATED v2.3.0: FuseCardSearchModal is no longer reachable from the
# rendered /verbessern UI because the top action menu (which contained the
# "Suchen" entry) was removed. Kept defined for backward compatibility with
# existing tests that instantiate it directly.
class FuseCardSearchModal(FuseFlowModal):
    def __init__(
        self,
        requester_id: int,
        dust_amount: int,
        user_karten: list[tuple[str, int]],
        *,
        source_message: discord.Message | None,
        parent_view: "FuseCardSelectView",
    ):
        super().__init__(requester_id, title="🔍 Held suchen")
        self.dust_amount = int(dust_amount)
        self.user_karten = list(user_karten)
        self.source_message = source_message
        self.parent_view = parent_view
        self.search_input = ui.TextInput(
            label="Suchbegriff",
            placeholder="z. B. Iron, Spider oder Captain",
            required=True,
            max_length=50,
        )
        self.add_item(self.search_input)

    async def on_submit(self, interaction: discord.Interaction):
        search_query = str(self.search_input.value or "").strip()
        if not search_query:
            await interaction.response.send_message("❌ Bitte gib einen Suchbegriff ein.", ephemeral=True)
            return

        matches = _search_fuse_card_groups(self.user_karten, search_query)
        if not matches:
            await interaction.response.send_message(
                f'? Keine passenden Helden f?r "{search_query}" gefunden.',
                ephemeral=True,
            )
            return
        if self.source_message is None:
            await interaction.response.send_message(
                "❌ Die Suchergebnisse konnten nicht geladen werden.",
                ephemeral=True,
            )
            return

        next_view = FuseCardSelectView(
            self.requester_id,
            self.dust_amount,
            self.user_karten,
            mode="search",
            grouped_cards=matches,
            search_query=search_query,
            page=0,
        )
        self.parent_view.stop()
        await interaction.response.defer()
        try:
            await self.source_message.edit(embed=next_view.build_embed(), view=next_view)
        except Exception:
            logging.exception("Failed to update fuse search results")
            next_view.stop()
            await send_interaction_response(
                interaction,
                content="❌ Die Suchergebnisse konnten nicht geladen werden.",
                ephemeral=True,
            )


class FuseCardSelectView(FuseFlowView):
    def __init__(
        self,
        requester_id: int,
        dust_amount: int,
        user_karten: list[tuple[str, int]],
        *,
        mode: str = "browse",
        grouped_cards: list[dict[str, Any]] | None = None,
        search_query: str | None = None,
        page: int = 0,
    ):
        super().__init__(requester_id, timeout=600)
        self.dust_amount = int(dust_amount)
        self.user_karten = list(user_karten)
        # v2.3.0: the top action menu was removed; the legacy "root" mode is
        # aliased to "browse" so older call sites and tests keep working
        # without forcing them to pass mode="browse" explicitly.
        normalized_mode = str(mode or "browse")
        if normalized_mode == "root":
            normalized_mode = "browse"
        self.mode = normalized_mode
        self.search_query = str(search_query or "").strip()
        self.grouped_cards = list(grouped_cards) if grouped_cards is not None else _group_owned_cards_for_current_mode(list(self.user_karten))
        self.page = max(0, int(page))

        self.action_select = FuseCardActionSelect(self)
        self.card_select = CardSelect(self)
        self.prev_button = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, row=2)
        self.next_button = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, row=2)
        self.prev_button.callback = self._on_prev_page
        self.next_button.callback = self._on_next_page
        self.cancel_button = FuseCancelButton()
        self._render()

    def _page_count(self) -> int:
        if self.mode == "root":
            return 1
        return max(1, (len(self.grouped_cards) + 24) // 25)

    def _visible_groups(self) -> list[dict[str, Any]]:
        if self.mode == "root":
            return list(self.grouped_cards[:25])
        start = self.page * 25
        return list(self.grouped_cards[start:start + 25])

    def action_placeholder(self) -> str:
        if self.mode == "search":
            return "Suchoptionen"
        if self.mode == "browse":
            return "Browseroptionen"
        return "Aktion wählen..."

    def action_options(self) -> list[SelectOption]:
        if self.mode == "root":
            return [
                SelectOption(
                    label="Suchen",
                    value=FUSE_CARD_ACTION_SEARCH,
                    description="Held per Suchbegriff finden",
                    emoji="🔍",
                ),
                SelectOption(
                    label="Alle durchsuchen",
                    value=FUSE_CARD_ACTION_BROWSE_ALL,
                    description="Alle verfügbaren Helden anzeigen",
                    emoji="📚",
                ),
            ]
        return [
            SelectOption(
                label="Zurück",
                value=FUSE_CARD_ACTION_BACK,
                description="Zur normalen Held-Auswahl zurückkehren",
                emoji="↩️",
            )
        ]

    def card_placeholder(self) -> str:
        if self.mode == "search":
            if self._page_count() > 1:
                return f"Suchergebnisse auswählen... (Seite {self.page + 1}/{self._page_count()})"
            return "Suchergebnis auswählen..."
        if self.mode == "browse":
            if self._page_count() > 1:
                return f"Helden auswählen... (Seite {self.page + 1}/{self._page_count()})"
            return "Helden auswählen..."
        return "Wähle eine Karte zum Verstärken..."

    def card_options(self) -> list[SelectOption]:
        visible_groups = self._visible_groups()
        if not visible_groups:
            return [SelectOption(label="Keine Karten verfügbar", value=FUSE_CARD_EMPTY)]
        return [
            SelectOption(
                label=_group_option_label(group)[:100],
                value=str(group.get("base_name") or ""),
            )
            for group in visible_groups
        ]

    def build_embed(self) -> discord.Embed:
        if self.mode == "browse":
            return _build_fuse_card_select_embed(
                self.dust_amount,
                title="📚 Alle Helden durchsuchen",
                guidance=(
                    f"Seite **{self.page + 1}** von **{self._page_count()}**.\n"
                    'W?hle einen Helden oder nutze oben "Zur?ck".'
                ),
            )
        if self.mode == "search":
            guidance = (
                f"Suchbegriff: **{self.search_query}**\n"
                f"Treffer: **{len(self.grouped_cards)}**"
            )
            if self._page_count() > 1:
                guidance += f"\nSeite **{self.page + 1}** von **{self._page_count()}**"
            return _build_fuse_card_select_embed(
                self.dust_amount,
                title="🔍 Suchergebnisse",
                guidance=guidance,
            )
        return _build_fuse_card_select_embed(self.dust_amount)

    def _render(self) -> None:
        self.clear_items()
        # v2.3.0: action_select is intentionally NOT added to the view. The top
        # action menu ("Suchen" / "Alle durchsuchen" / "Zurück") was removed
        # from /verbessern; only the card list and pagination remain. We still
        # refresh its options/placeholder so existing tests that read
        # view.action_select.options keep seeing up-to-date values.
        self.action_select.options = self.action_options()
        self.action_select.placeholder = self.action_placeholder()
        self.card_select.options = self.card_options()
        self.card_select.placeholder = self.card_placeholder()
        self.add_item(self.card_select)
        if self.mode in {"browse", "search"} and self._page_count() > 1:
            self.prev_button.disabled = self.page <= 0
            self.next_button.disabled = self.page >= (self._page_count() - 1)
            self.add_item(self.prev_button)
            self.add_item(self.next_button)
        self.add_item(self.cancel_button)

    async def handle_action_selection(self, interaction: discord.Interaction, selected_value: str) -> None:
        if selected_value == FUSE_CARD_ACTION_SEARCH:
            modal = FuseCardSearchModal(
                self.requester_id,
                self.dust_amount,
                self.user_karten,
                source_message=_interaction_message_or_none(interaction),
                parent_view=self,
            )
            # Modals must be sent as the initial response (no defer beforehand).
            try:
                await interaction.response.send_modal(modal)
            except Exception:
                await send_interaction_response(
                    interaction,
                    content="❌ Konnte das Suchfenster nicht öffnen.",
                    ephemeral=True,
                )
            return
        await defer_interaction(interaction, ephemeral=True)
        if selected_value == FUSE_CARD_ACTION_BROWSE_ALL:
            next_view = FuseCardSelectView(
                self.requester_id,
                self.dust_amount,
                self.user_karten,
                mode="browse",
                grouped_cards=_group_owned_cards_for_current_mode(list(self.user_karten)),
                page=0,
            )
            self.stop()
            await edit_interaction_message(interaction, embed=next_view.build_embed(), view=next_view)
            return
        if selected_value == FUSE_CARD_ACTION_BACK:
            next_view = FuseCardSelectView(self.requester_id, self.dust_amount, self.user_karten)
            self.stop()
            await edit_interaction_message(interaction, embed=next_view.build_embed(), view=next_view)

    async def handle_card_selection(self, interaction: discord.Interaction, selected_card: str) -> None:
        await defer_interaction(interaction, ephemeral=True)
        if selected_card == FUSE_CARD_EMPTY:
            await send_interaction_response(interaction, content="❌ Du hast aktuell keine Karten zum Verstärken.", ephemeral=True)
            return

        karte_data = await get_karte_by_name(selected_card)
        if not karte_data:
            await send_interaction_response(interaction, content="❌ Karte nicht gefunden!", ephemeral=True)
            return

        user_buffs = await get_card_buffs(interaction.user.id, selected_card)
        next_view = BuffTypeSelectView(
            self.requester_id,
            self.dust_amount,
            selected_card,
            karte_data,
            user_buffs,
        )
        self.stop()
        await edit_interaction_message(
            interaction,
            embed=_build_fuse_buff_type_embed(selected_card, karte_data, user_buffs),
            view=next_view,
        )

    async def _on_prev_page(self, interaction: discord.Interaction) -> None:
        await defer_interaction(interaction, ephemeral=True)
        if self.page > 0:
            self.page -= 1
        self._render()
        await edit_interaction_message(interaction, embed=self.build_embed(), view=self)

    async def _on_next_page(self, interaction: discord.Interaction) -> None:
        await defer_interaction(interaction, ephemeral=True)
        if self.page < (self._page_count() - 1):
            self.page += 1
        self._render()
        await edit_interaction_message(interaction, embed=self.build_embed(), view=self)


class BuffTypeSelectView(FuseFlowView):
    def __init__(self, requester_id, dust_amount, selected_card, karte_data, user_buffs):
        super().__init__(requester_id, timeout=600)
        self.dust_amount = int(dust_amount)
        self.selected_card = selected_card
        self.add_item(BuffTypeSelect(self.dust_amount, selected_card, karte_data, user_buffs))
        self.add_item(FuseCancelButton())


class BuffTypeSelect(ui.Select):
    def __init__(self, dust_amount, selected_card, karte_data, user_buffs):
        self.dust_amount = dust_amount
        self.selected_card = selected_card
        total_health, damage_map = battle_state.summarize_card_buffs(user_buffs)
        base_hp = int(karte_data.get("hp", 100) or 100)
        current_hp = base_hp + total_health

        options = [
            SelectOption(
                label="Held wechseln",
                value="change_card",
                description="Zurück zur Held-Auswahl",
                emoji="🔁",
            ),
            SelectOption(
                label="Leben verstärken",
                value="health_0",
                description=f"Aktuell {current_hp} HP, +{FUSE_HEALTH_BONUS} Lebenspunkte",
                emoji="❤️",
            )
        ]

        attacks = karte_data.get("attacks", [])
        for i, attack in enumerate(attacks[:4]):
            if not _attack_is_damage_upgradeable(attack):
                continue
            attack_name = str(attack.get("name") or f"Attacke {i + 1}")
            current_bonus = damage_map.get(i + 1, 0)
            upgrade_step = _attack_upgrade_step(attack)
            upgrade_cap = _attack_upgrade_max_bonus(attack)
            min_dmg, max_dmg = _attack_total_damage_range(attack, max_only_bonus=current_bonus, flat_bonus=0)
            if current_bonus >= upgrade_cap:
                continue
            if max_dmg + upgrade_step > MAX_ATTACK_DAMAGE_PER_HIT:
                continue
            options.append(
                SelectOption(
                    label=f"{attack_name} verstärken",
                    value=f"damage_{i + 1}",
                    description=f"Aktuell {min_dmg}-{max_dmg} Schaden, +{upgrade_step} Max-Schaden",
                    emoji="⚔️",
                )
            )

        super().__init__(placeholder="Wähle was verstärkt werden soll...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await defer_interaction(interaction, ephemeral=True)
        buff_choice = str(self.values[0] or "")
        if buff_choice == "change_card":
            user_karten = await get_user_karten(interaction.user.id)
            if not user_karten:
                await send_interaction_response(interaction, content="❌ Du hast keine Karten zum Verstärken!", ephemeral=True)
                return
            view = FuseCardSelectView(interaction.user.id, self.dust_amount, user_karten)
            if self.view is not None:
                self.view.stop()
            await edit_interaction_message(interaction, embed=view.build_embed(), view=view)
            return

        buff_type, attack_num = buff_choice.split("_")
        attack_number = int(attack_num)

        karte_data = await get_karte_by_name(self.selected_card)
        if not karte_data:
            await send_interaction_response(interaction, content="❌ Karte nicht gefunden!", ephemeral=True)
            return
        user_buffs = await get_card_buffs(interaction.user.id, self.selected_card)

        applied_buff_amount = 0
        buff_text = ""
        emoji = "⚔️"

        if buff_type == "damage":
            attacks = karte_data.get("attacks", [])
            if attack_number <= 0 or attack_number > len(attacks):
                await send_interaction_response(interaction, content="❌ Ungültige Attacke.", ephemeral=True)
                return
            selected_attack = attacks[attack_number - 1]
            if not _attack_is_damage_upgradeable(selected_attack):
                await send_interaction_response(
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

            upgrade_step = _attack_upgrade_step(selected_attack)
            upgrade_cap = _attack_upgrade_max_bonus(selected_attack)
            current_max_damage = int(max_base_damage) + int(existing_buffs)
            if existing_buffs >= upgrade_cap:
                await send_interaction_response(
                    "❌ Dieses Upgrade hat bereits sein Maximum erreicht.",
                    ephemeral=True,
                )
                return

            next_max_damage = current_max_damage + upgrade_step
            if next_max_damage > MAX_ATTACK_DAMAGE_PER_HIT:
                await send_interaction_response(
                    (
                        f"❌ **Maximal {MAX_ATTACK_DAMAGE_PER_HIT} Schaden pro Angriff erlaubt!**\n\n"
                        f"Aktuell: **{current_max_damage}**\n"
                        f"Nächste Verbesserung wäre: **{next_max_damage}**"
                    ),
                    ephemeral=True,
                )
                return

            applied_buff_amount = upgrade_step
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

        try:
            await add_card_buff(
                interaction.user.id,
                self.selected_card,
                buff_type,
                attack_number,
                applied_buff_amount,
            )
        except Exception:
            logging.exception("Failed to apply card buff for %s", self.selected_card)
            await add_infinitydust(interaction.user.id, self.dust_amount)
            await send_interaction_response(
                interaction,
                content="❌ Die Verstärkung ist fehlgeschlagen. Dein Infinitydust wurde zurückerstattet.",
                ephemeral=True,
            )
            return
        await _log_event_safe(
            "upgrade_applied",
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            hero_name=self.selected_card,
            attack_name=(str(selected_attack.get("name") or "") if buff_type == "damage" else ""),
            payload={
                "upgrade_type": buff_type,
                "upgrade_attack_number": int(attack_number),
                "upgrade_attack_name": (str(selected_attack.get("name") or "") if buff_type == "damage" else ""),
                "upgrade_amount": int(applied_buff_amount),
                "dust_cost": int(self.dust_amount),
            },
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
        _apply_item_media(embed, "infinitydust", thumbnail=True)
        if self.view is not None:
            self.view.stop()
        await edit_interaction_message(interaction, embed=embed, view=None)


class FuseStatSelectView(FuseFlowView):
    """Stat-Auswahl im /verbessern-Flow (Schritt B nach Karten-Auswahl).

    Zeigt Optionen für HP und jede aufwertungsfähige Attacke. Optionen werden
    ausgeblendet, wenn der Stat bereits am Cap ist. Bei keiner möglichen
    Aufwertung wird ein Hinweis angezeigt.

    Validates: Requirements 6.5, 6.12
    """

    def __init__(self, requester_id, selected_card, karte_data, user_buffs):
        super().__init__(requester_id, timeout=600)
        self.selected_card = selected_card
        self.karte_data = karte_data
        self.user_buffs = list(user_buffs)
        self.stat_select = FuseStatSelect(selected_card, karte_data, list(user_buffs))
        self.add_item(self.stat_select)
        self.add_item(FuseCancelButton())

    def has_upgradeable_stats(self) -> bool:
        return self._upgradeable_count() > 0

    def _upgradeable_count(self) -> int:
        # Counts HP-upgrade option + each non-capped damage upgrade option.
        # Mirrors the option-building logic in `FuseStatSelect.__init__` but
        # without instantiating the Select itself.
        count = 0
        total_health, damage_map = battle_state.summarize_card_buffs(self.user_buffs)
        base_hp = int(self.karte_data.get("hp", 100) or 100)
        current_hp = base_hp + total_health
        if current_hp < FUSE_HP_CAP:
            count += 1
        attacks = self.karte_data.get("attacks", [])
        for i, attack in enumerate(attacks[:4]):
            if not _attack_is_damage_upgradeable(attack):
                continue
            current_bonus = damage_map.get(i + 1, 0)
            if current_bonus >= _attack_upgrade_max_bonus(attack):
                continue
            _min_dmg, max_dmg = _attack_total_damage_range(
                attack, max_only_bonus=current_bonus, flat_bonus=0
            )
            if max_dmg + _attack_upgrade_step(attack) > MAX_ATTACK_DAMAGE_PER_HIT:
                continue
            count += 1
        return count


class FuseStatSelect(ui.Select):
    """Select-Menu für die Stat-Auswahl im neuen /verbessern-Flow.

    Optionen: ``health_0`` für HP, ``damage_<n>`` für Attacke n. Wenn keine
    Stats aufwertbar sind, wird eine Hinweis-Option mit value ``__none__``
    angezeigt (Req. 6.12).
    """

    def __init__(self, selected_card, karte_data, user_buffs):
        self.selected_card = selected_card
        self.karte_data = karte_data
        self.user_buffs = list(user_buffs)
        total_health, damage_map = battle_state.summarize_card_buffs(self.user_buffs)
        base_hp = int(karte_data.get("hp", 100) or 100)
        current_hp = base_hp + total_health
        options: list[SelectOption] = []
        if current_hp < FUSE_HP_CAP:
            options.append(
                SelectOption(
                    label="Leben verstärken",
                    value="health_0",
                    description=(
                        f"Aktuell {current_hp} HP, "
                        f"+{FUSE_HEALTH_BONUS} Lebenspunkte pro 5 Dust"
                    ),
                    emoji="❤️",
                )
            )
        attacks = karte_data.get("attacks", [])
        for i, attack in enumerate(attacks[:4]):
            if not _attack_is_damage_upgradeable(attack):
                continue
            attack_name = str(attack.get("name") or f"Attacke {i + 1}")
            current_bonus = damage_map.get(i + 1, 0)
            if current_bonus >= _attack_upgrade_max_bonus(attack):
                continue
            min_dmg, max_dmg = _attack_total_damage_range(
                attack, max_only_bonus=current_bonus, flat_bonus=0
            )
            upgrade_step = _attack_upgrade_step(attack)
            if max_dmg + upgrade_step > MAX_ATTACK_DAMAGE_PER_HIT:
                continue
            options.append(
                SelectOption(
                    label=f"{attack_name} verstärken",
                    value=f"damage_{i + 1}",
                    description=(
                        f"Aktuell {min_dmg}-{max_dmg} Schaden, "
                        f"+{upgrade_step} Max-Schaden pro 5 Dust"
                    ),
                    emoji="⚔️",
                )
            )
        if not options:
            options.append(
                SelectOption(
                    label="Keine Aufwertung möglich",
                    value="__none__",
                    description="Alle Stats sind am Cap.",
                )
            )
        super().__init__(
            placeholder="Wähle was verstärkt werden soll...",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await defer_interaction(interaction, ephemeral=True)
        choice = str(self.values[0] or "")
        if choice == "__none__":
            await send_interaction_response(
                interaction,
                content="❌ Alle aufwertbaren Stats dieser Karte sind am Cap.",
                ephemeral=True,
            )
            return
        # Schritt C: Multiplikator-Auswahl. Ab v2.3.0 (Task 6.4) ersetzt
        # `FuseMultiplierView` die alte `DustAmountView` und filtert die
        # Multiplikator-Optionen dynamisch nach Stat-Cap und Dust-Saldo.
        user_dust = await get_infinitydust(interaction.user.id)
        requester_id = self.view.requester_id if self.view is not None else interaction.user.id
        next_view = FuseMultiplierView(
            requester_id,
            selected_card=self.selected_card,
            karte_data=self.karte_data,
            user_buffs=self.user_buffs,
            stat_choice=choice,
            dust_balance=user_dust,
        )
        if self.view is not None:
            self.view.stop()
        embed = discord.Embed(
            title="💎 Karten-Verstärkung",
            description=(
                f"Du hast **{user_dust} Infinitydust**\n\n"
                "Wähle einen Multiplikator (1× = 5 Dust, 2× = 10 Dust, …, 6× = 30 Dust)."
            ),
            color=0x9D4EDD,
        )
        await edit_interaction_message(interaction, embed=embed, view=next_view)


# ---------------------------------------------------------------------------
# /verbessern Schritt C: Multiplikator-Auswahl (Task 6.4)
# ---------------------------------------------------------------------------
# Multiplier-Beträge im /verbessern-Flow ab v2.3.0.
# 1× = 5 Dust (entspricht der alten "normalen" Aufwertung).
FUSE_MULTIPLIER_DUST_PER_STEP = 5
FUSE_MULTIPLIER_VALUES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)


def _fuse_available_multipliers(
    *,
    base_step: int,
    cap_remaining: int,
    dust_balance: int,
) -> tuple[list[int], list[int]]:
    """Liefert (visible, affordable) für den Multiplikator-Filter.

    - visible: Multiplikatoren 1..min(6, cap_remaining // base_step) — alle
      Optionen, die laut Stat-Cap noch zulässig sind (Req. 6.7).
    - affordable: Teilmenge von ``visible``, die mit ``dust_balance`` bezahlt
      werden können. Nicht-bezahlbare Multiplikatoren bleiben sichtbar, werden
      aber als nicht wählbar markiert (Req. 6.9).
    """
    if base_step <= 0:
        return [], []
    max_by_cap = max(0, cap_remaining // base_step)
    visible_max = min(6, max_by_cap)
    visible = [m for m in FUSE_MULTIPLIER_VALUES if m <= visible_max]
    affordable = [m for m in visible if m * FUSE_MULTIPLIER_DUST_PER_STEP <= dust_balance]
    return visible, affordable


def _fuse_resolve_stat_context(
    *,
    karte_data: dict,
    user_buffs: list,
    stat_choice: str,
) -> tuple[int, int]:
    """Bestimmt ``(base_step, cap_remaining)`` für die gewählte Stat-Option.

    HP nutzt ``FUSE_HEALTH_BONUS`` und den globalen ``FUSE_HP_CAP`` von 200.
    Für Damage-Optionen (``damage_<n>``) wird sowohl der attack-spezifische
    Upgrade-Cap (``_attack_upgrade_max_bonus``) als auch der globale Schadens-
    Hartcap (``MAX_ATTACK_DAMAGE_PER_HIT``) berücksichtigt.

    Bei ungültiger Auswahl wird ``(0, 0)`` zurückgegeben.
    """
    total_health, damage_map = battle_state.summarize_card_buffs(user_buffs)
    if stat_choice == "health_0":
        base_step = int(FUSE_HEALTH_BONUS)
        base_hp = int(karte_data.get("hp", 100) or 100)
        current_hp = base_hp + total_health
        cap_remaining = max(0, FUSE_HP_CAP - current_hp)
        return base_step, cap_remaining
    if not stat_choice.startswith("damage_"):
        return 0, 0
    try:
        attack_number = int(stat_choice.split("_", 1)[1])
    except (IndexError, ValueError):
        return 0, 0
    attacks = karte_data.get("attacks", []) or []
    if attack_number <= 0 or attack_number > len(attacks):
        return 0, 0
    attack = attacks[attack_number - 1]
    base_step = int(_attack_upgrade_step(attack))
    upgrade_cap = int(_attack_upgrade_max_bonus(attack))
    current_bonus = int(damage_map.get(attack_number, 0))
    cap_remaining = max(0, upgrade_cap - current_bonus)
    # Zusätzlicher Hartcap: globaler Per-Hit-Schadens-Cap.
    _min_dmg, max_dmg = _attack_total_damage_range(
        attack, max_only_bonus=current_bonus, flat_bonus=0
    )
    hard_cap_remaining = max(0, MAX_ATTACK_DAMAGE_PER_HIT - max_dmg)
    cap_remaining = min(cap_remaining, hard_cap_remaining)
    return base_step, cap_remaining


class FuseMultiplierView(FuseFlowView):
    """Multiplikator-Auswahl im /verbessern-Flow (Schritt C).

    Zeigt 1×–6× je nach verbleibendem Cap-Abstand des gewählten Stats. Optionen,
    die zu teuer sind, bleiben sichtbar, werden aber als nicht wählbar markiert.

    Validates: Requirements 6.1, 6.2, 6.6, 6.7, 6.8, 6.9, 6.10
    """

    def __init__(
        self,
        requester_id: int,
        *,
        selected_card: str,
        karte_data: dict,
        user_buffs: list,
        stat_choice: str,
        dust_balance: int,
    ):
        super().__init__(requester_id, timeout=600)
        self.selected_card = selected_card
        self.karte_data = karte_data
        self.user_buffs = list(user_buffs)
        self.stat_choice = str(stat_choice or "")
        self.dust_balance = int(dust_balance)
        self.multiplier_select = FuseMultiplierSelect(
            selected_card=selected_card,
            karte_data=karte_data,
            user_buffs=user_buffs,
            stat_choice=stat_choice,
            dust_balance=dust_balance,
        )
        self.add_item(self.multiplier_select)
        self.add_item(FuseCancelButton())


class FuseMultiplierSelect(ui.Select):
    """Select-Menu für Multiplikator-Auswahl mit dynamischer Cap-/Dust-Filterung."""

    def __init__(
        self,
        *,
        selected_card: str,
        karte_data: dict,
        user_buffs: list,
        stat_choice: str,
        dust_balance: int,
    ):
        self.selected_card = selected_card
        self.karte_data = karte_data
        self.user_buffs = list(user_buffs)
        self.stat_choice = str(stat_choice or "")
        self.dust_balance = int(dust_balance)

        base_step, cap_remaining = _fuse_resolve_stat_context(
            karte_data=karte_data,
            user_buffs=user_buffs,
            stat_choice=stat_choice,
        )
        self.base_step = int(base_step)
        self.cap_remaining = int(cap_remaining)

        visible, affordable = _fuse_available_multipliers(
            base_step=self.base_step,
            cap_remaining=self.cap_remaining,
            dust_balance=self.dust_balance,
        )
        self._visible = list(visible)
        self._affordable = set(affordable)

        options: list[SelectOption] = []
        for m in visible:
            cost = m * FUSE_MULTIPLIER_DUST_PER_STEP
            stat_gain = m * self.base_step
            if m in self._affordable:
                label = f"{m}× ({cost} Dust → +{stat_gain})"
                description = f"Kosten: {cost} Dust • Gewinn: +{stat_gain}"
            else:
                label = f"{m}× ({cost} Dust → +{stat_gain}) ⚠️ zu teuer"
                description = f"Du hast nur {self.dust_balance} Dust."
            options.append(
                SelectOption(
                    label=label[:100],
                    value=str(m),
                    description=description[:100],
                )
            )

        if not options:
            options.append(
                SelectOption(
                    label="Keine Aufwertung möglich",
                    value="__none__",
                    description="Stat ist am Cap oder Basiswert ungültig.",
                )
            )

        super().__init__(
            placeholder=f"Multiplikator wählen (du hast {self.dust_balance} Dust)...",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await defer_interaction(interaction, ephemeral=True)
        choice = str(self.values[0] or "")
        if choice == "__none__":
            await send_interaction_response(
                interaction,
                content=(
                    "❌ Diese Karte kann mit deinen aktuellen Stats und deinem "
                    "Dust-Vorrat nicht weiter aufgewertet werden."
                ),
                ephemeral=True,
            )
            return
        try:
            multiplier = int(choice)
        except ValueError:
            multiplier = 0
        if multiplier <= 0 or multiplier not in self._affordable:
            await send_interaction_response(
                interaction,
                content=(
                    f"❌ Du hast nicht genug Dust für **{multiplier}×**. "
                    f"Wähle einen niedrigeren Multiplikator."
                ),
                ephemeral=True,
            )
            return
        # Apply path: vorerst über die bestehende Step-by-Step-Logik. Tasks 6.6
        # und 6.7 ersetzen das durch eine atomare Transaktion mit Vorher/Nachher-
        # Bestätigungs-Embed.
        await _apply_fuse_multi_upgrade_legacy(
            interaction=interaction,
            view=self.view,
            selected_card=self.selected_card,
            karte_data=self.karte_data,
            stat_choice=self.stat_choice,
            multiplier=multiplier,
            cost_per_step=FUSE_MULTIPLIER_DUST_PER_STEP,
            base_step=self.base_step,
        )


async def _apply_fuse_multi_upgrade_legacy(
    *,
    interaction: discord.Interaction,
    view: ui.View | None,
    selected_card: str,
    karte_data: dict,
    stat_choice: str,
    multiplier: int,
    cost_per_step: int,
    base_step: int,
) -> None:
    """Apply-Logik für Multi-Multiplikator-Aufwertung (Tasks 6.4 + 6.6 + 6.7).

    Wendet ``multiplier`` Aufwertungs-Schritte als **eine atomare Operation** an
    (Req. 6.13): ``total_dust_cost = multiplier × cost_per_step`` wird in einem
    einzigen ``spend_infinitydust``-Aufruf abgebucht und ``total_gain =
    multiplier × base_step`` in einem einzigen ``add_card_buff``-Aufruf gewährt.
    Schlägt der Stat-Apply-Schritt fehl, wird der gesamte Dust-Betrag in einem
    Zug refundiert — der Spieler bleibt also entweder im vollständig
    aufgewerteten Zustand oder unverändert. Anschließend wird ein
    Bestätigungs-Embed mit Vorher/Nachher-Werten, verbrauchtem Dust und
    verbleibendem Saldo gepostet (Req. 6.11).
    """
    user_id = interaction.user.id
    is_hp = (stat_choice == "health_0")
    attacks = karte_data.get("attacks", []) or []
    if is_hp:
        buff_type = "health"
        attack_number = 0
        emoji = "❤️"
        attack_name = ""
        attack_for_stat: dict | None = None
    else:
        try:
            attack_number = int(stat_choice.split("_", 1)[1])
        except (IndexError, ValueError):
            await send_interaction_response(
                interaction,
                content="❌ Ungültige Stat-Auswahl.",
                ephemeral=True,
            )
            return
        if attack_number <= 0 or attack_number > len(attacks):
            await send_interaction_response(
                interaction,
                content="❌ Ungültige Attacke.",
                ephemeral=True,
            )
            return
        buff_type = "damage"
        emoji = "⚔️"
        attack_for_stat = attacks[attack_number - 1]
        attack_name = str(attack_for_stat.get("name") or f"Attacke {attack_number}")

    # Vorher-Werte (Req. 6.11): Buffs vor der Aufwertung laden, um prev_value
    # exakt zu kennen. Bei DB-Fehler nehmen wir 0 als Defaults und loggen.
    try:
        prior_user_buffs = await get_card_buffs(user_id, selected_card)
    except Exception:
        logging.exception(
            "Failed to load prior buffs for %s before multi-upgrade", selected_card
        )
        prior_user_buffs = []
    prior_total_health, prior_damage_map = battle_state.summarize_card_buffs(
        prior_user_buffs
    )

    # Atomares Persistieren (Req. 6.13, Task 6.7): statt N Einzel-Schritten
    # buchen wir die gesamten Dust-Kosten in EINEM `spend_infinitydust`-Aufruf
    # ab und gewähren den gesamten Stat-Gain in EINEM `add_card_buff`-Aufruf.
    # Schlägt das Apply nach dem Spend fehl, refundieren wir den vollen Betrag
    # — der Spieler bleibt damit immer entweder vollständig aufgewertet oder
    # unverändert.
    multiplier_int = int(multiplier)
    total_dust_cost = multiplier_int * int(cost_per_step)
    total_gain = multiplier_int * int(base_step)

    spend_ok = False
    try:
        spend_ok = await spend_infinitydust(user_id, total_dust_cost)
    except Exception:
        logging.exception(
            "spend_infinitydust raised during multi-upgrade for user %s", user_id
        )
        spend_ok = False

    if not spend_ok:
        await send_interaction_response(
            interaction,
            content=(
                f"❌ Nicht genug Infinitydust für **{multiplier_int}× "
                f"({total_dust_cost} Dust)**."
            ),
            ephemeral=True,
        )
        return

    try:
        await add_card_buff(
            user_id,
            selected_card,
            buff_type,
            attack_number,
            total_gain,
        )
    except Exception:
        logging.exception(
            "Failed to apply card buff during multi-upgrade for %s; "
            "refunding %d dust",
            selected_card,
            total_dust_cost,
        )
        try:
            await add_infinitydust(user_id, total_dust_cost)
        except Exception:
            logging.exception(
                "Failed to refund %d dust after apply failure for user %s",
                total_dust_cost,
                user_id,
            )
        await send_interaction_response(
            interaction,
            content=(
                "❌ Die Verstärkung ist fehlgeschlagen. Dein Infinitydust "
                "wurde zurückerstattet."
            ),
            ephemeral=True,
        )
        return

    applied_steps = multiplier_int
    spent_dust = total_dust_cost

    if applied_steps <= 0:
        await send_interaction_response(
            interaction,
            content="❌ Die Verstärkung ist fehlgeschlagen. Es wurde kein Dust verbraucht.",
            ephemeral=True,
        )
        return

    total_gain = applied_steps * int(base_step)

    # Vorher/Nachher-Werte für das Bestätigungs-Embed (Req. 6.11).
    if is_hp:
        base_hp = int(karte_data.get("hp", 100) or 100)
        prev_value = base_hp + int(prior_total_health)
        new_value = prev_value + total_gain
        stat_label = "Leben"
        prev_display = f"{prev_value} HP"
        new_display = f"{new_value} HP"
    else:
        existing_bonus = int(prior_damage_map.get(int(attack_number), 0))
        prev_min, prev_max = _attack_total_damage_range(
            attack_for_stat or {},
            max_only_bonus=existing_bonus,
            flat_bonus=0,
        )
        new_max = prev_max + total_gain
        new_min = prev_min
        stat_label = attack_name
        prev_display = f"{prev_min}–{prev_max} Schaden"
        new_display = f"{new_min}–{new_max} Schaden"

    # Verbleibender Dust-Saldo nach allen Spend-Operationen (Req. 6.11).
    try:
        remaining_dust = int(await get_infinitydust(user_id) or 0)
    except Exception:
        logging.exception(
            "Failed to load remaining infinitydust after multi-upgrade for user %s",
            user_id,
        )
        remaining_dust = 0

    await _log_event_safe(
        "upgrade_applied",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        thread_id=_thread_id_for_channel(interaction.channel),
        actor_user_id=user_id,
        hero_name=selected_card,
        attack_name=attack_name,
        payload={
            "upgrade_type": buff_type,
            "upgrade_attack_number": int(attack_number),
            "upgrade_attack_name": attack_name,
            "upgrade_amount": int(total_gain),
            "upgrade_multiplier": int(multiplier),
            "applied_steps": int(applied_steps),
            "dust_cost": int(spent_dust),
        },
    )

    embed = discord.Embed(
        title="✅ Verstärkung erfolgreich!",
        description=(
            f"🃏 **{selected_card}**\n"
            f"{emoji} **{stat_label}**: **{prev_display}** → **{new_display}**"
        ),
        color=0x00FF00,
    )
    embed.add_field(
        name="💎 Dust verbraucht",
        value=f"**{spent_dust}**",
        inline=True,
    )
    embed.add_field(
        name="💎 Dust verbleibend",
        value=f"**{remaining_dust}**",
        inline=True,
    )
    if applied_steps < int(multiplier):
        embed.add_field(
            name="⚠️ Hinweis",
            value=(
                f"Es wurden nur **{applied_steps}** Schritte angewendet "
                f"(statt {int(multiplier)})."
            ),
            inline=False,
        )
    _apply_item_media(embed, "infinitydust", thumbnail=True)
    if view is not None:
        view.stop()
    await edit_interaction_message(interaction, embed=embed, view=None)


class InviteConfirmationView(DurableView):
    durable_view_kind = VIEW_KIND_INVITE_CONFIRM

    def __init__(self, pending_id: int, *, need_admin_gate: bool):
        super().__init__(timeout=None)
        self.pending_id = int(pending_id)
        self.need_admin_gate = bool(need_admin_gate)
        inv_b = ui.Button(
            label=game_ui_texts.INVITE_BTN_INVITER,
            style=discord.ButtonStyle.success,
            custom_id="invite_conf:inv",
            row=0,
        )
        inv_b.callback = self._cb_inviter
        self.add_item(inv_b)
        invtee_b = ui.Button(
            label=game_ui_texts.INVITE_BTN_INVITEE,
            style=discord.ButtonStyle.success,
            custom_id="invite_conf:invtee",
            row=0,
        )
        invtee_b.callback = self._cb_invitee
        self.add_item(invtee_b)
        if self.need_admin_gate:
            adm_b = ui.Button(
                label=game_ui_texts.INVITE_BTN_ADMIN,
                style=discord.ButtonStyle.primary,
                custom_id="invite_conf:adm",
                row=1,
            )
            adm_b.callback = self._cb_admin
            self.add_item(adm_b)

    def durable_payload(self) -> dict[str, Any]:
        return {"pending_id": self.pending_id, "need_admin_gate": self.need_admin_gate}

    def _status_embed(self, row: dict[str, Any]) -> discord.Embed:
        inviter_id = int(row["inviter_id"])
        invitee_id = int(row["invitee_id"])
        need = bool(int(row["need_admin"] or 0))
        inviter_u = bot.get_user(inviter_id)
        invitee_u = bot.get_user(invitee_id)
        im = inviter_u.mention if inviter_u else f"<@{inviter_id}>"
        em = invitee_u.mention if invitee_u else f"<@{invitee_id}>"
        admin_note = game_ui_texts.INVITE_ADMIN_NOTE if need else ""
        desc = game_ui_texts.INVITE_CONFIRM_DESCRIPTION.format(
            invitee=em,
            inviter=im,
            admin_note=admin_note,
        )
        lines: list[str] = []
        if int(row["inviter_ok"] or 0):
            lines.append("✅ " + game_ui_texts.INVITE_CONFIRM_ACK_INVITER)
        if int(row["invitee_ok"] or 0):
            lines.append("✅ " + game_ui_texts.INVITE_CONFIRM_ACK_INVITEE)
        if need and int(row["admin_ok"] or 0):
            lines.append("✅ " + game_ui_texts.INVITE_CONFIRM_ACK_ADMIN)
        if lines:
            desc += "\n\n" + "\n".join(lines)
        embed = discord.Embed(title=game_ui_texts.INVITE_CONFIRM_TITLE, description=desc, color=0x9D4EDD)
        _apply_item_media(embed, "infinitydust", thumbnail=True)
        return embed

    async def _cb_inviter(self, interaction: discord.Interaction):
        row = await load_invite_pending(self.pending_id)
        if not row or str(row.get("status") or "") != "pending":
            await interaction.response.send_message("❌ Diese Anfrage ist nicht mehr aktiv.", ephemeral=True)
            return
        if interaction.user.id != int(row["inviter_id"]):
            await interaction.response.send_message("❌ Nur der ausgewählte Einlader kann hier klicken.", ephemeral=True)
            return
        await mark_invite_pending_flag(self.pending_id, inviter=True)
        await interaction.response.defer()
        await self._after_flag(interaction)

    async def _cb_invitee(self, interaction: discord.Interaction):
        row = await load_invite_pending(self.pending_id)
        if not row or str(row.get("status") or "") != "pending":
            await interaction.response.send_message("❌ Diese Anfrage ist nicht mehr aktiv.", ephemeral=True)
            return
        if interaction.user.id != int(row["invitee_id"]):
            await interaction.response.send_message("❌ Nur der Eingeladene kann hier klicken.", ephemeral=True)
            return
        await mark_invite_pending_flag(self.pending_id, invitee=True)
        await interaction.response.defer()
        await self._after_flag(interaction)

    async def _cb_admin(self, interaction: discord.Interaction):
        row = await load_invite_pending(self.pending_id)
        if not row or str(row.get("status") or "") != "pending":
            await interaction.response.send_message("❌ Diese Anfrage ist nicht mehr aktiv.", ephemeral=True)
            return
        if not bool(int(row["need_admin"] or 0)):
            await interaction.response.send_message("❌ Keine Admin-Freigabe nötig.", ephemeral=True)
            return
        if not await is_admin(interaction):
            await interaction.response.send_message("❌ Nur ein Admin kann hier klicken.", ephemeral=True)
            return
        await mark_invite_pending_flag(self.pending_id, admin=True)
        await interaction.response.defer()
        await self._after_flag(interaction)

    async def _after_flag(self, interaction: discord.Interaction):
        result = await finalize_invite_pending_if_ready(self.pending_id, alpha_enabled=ALPHA_PHASE_ENABLED)
        msg = interaction.message
        if result:
            await self.clear_durable_registration()
            inv_id = int(result["inviter_id"])
            exp_id = int(result["invitee_id"])
            inv_u = bot.get_user(inv_id)
            exp_u = bot.get_user(exp_id)
            im = inv_u.mention if inv_u else f"<@{inv_id}>"
            em = exp_u.mention if exp_u else f"<@{exp_id}>"
            summary = result["reward_summary"]
            if summary.get("kind") == "first":
                extra = (
                    f"\n\n🃏 {im} hat die Karte **{summary.get('card_name', '?')}** erhalten.\n"
                    f"💎 {em} hat **5 Infinitydust** erhalten."
                )
            else:
                extra = f"\n\n💎 {im} und {em} haben je **5 Infinitydust** erhalten."
            if summary.get("kind") == "first":
                await _send_private_invite_card_reward(interaction, inv_id, str(summary.get("card_name") or ""))
            done = discord.Embed(
                title="🎉 Einladung abgeschlossen",
                description=game_ui_texts.INVITE_SUCCESS.format(inviter=im, invitee=em) + extra,
                color=0x00FF00,
            )
            self.stop()
            try:
                if msg is not None:
                    await msg.edit(embed=done, view=None)
            except Exception:
                logging.exception("Failed to finalize invite message")
            return
        row = await load_invite_pending(self.pending_id)
        if row and str(row.get("status") or "") == "pending":
            try:
                if msg is not None:
                    await msg.edit(embed=self._status_embed(row), view=self)
                    await _maybe_register_durable_message(msg, self)
            except Exception:
                logging.exception("Failed to refresh invite confirmation view")


def _build_invite_reward_card_embed(card_name: str) -> discord.Embed:
    card = _card_by_name_local(card_name) or {"name": card_name}
    resolved_name = str(card.get("name") or card_name or "Unbekannte Karte")
    embed = discord.Embed(
        title=f"Einladungs-Belohnung: {resolved_name}",
        description=str(card.get("beschreibung") or "Du hast diese Karte für deine erste bestätigte Einladung erhalten."),
        color=_card_rarity_color(card) or 0x00FF00,
    )
    rarity = str(card.get("seltenheit") or "").strip()
    if rarity:
        embed.add_field(name="Seltenheit", value=rarity, inline=True)
    if card.get("hp") is not None:
        embed.add_field(name="HP", value=str(card.get("hp")), inline=True)
    attacks = card.get("attacks") if isinstance(card, dict) else None
    if isinstance(attacks, list) and attacks:
        lines: list[str] = []
        for idx, attack in enumerate(attacks[:4], start=1):
            if not isinstance(attack, dict):
                continue
            damage = attack.get("damage")
            if isinstance(damage, list) and len(damage) >= 2:
                damage_text = f"{damage[0]}-{damage[1]}"
            elif damage is not None:
                damage_text = str(damage)
            else:
                damage_text = "-"
            info = str(attack.get("info") or "").strip()
            suffix = f" - {info}" if info else ""
            lines.append(f"{idx}. {attack.get('name', 'Attacke')} ({damage_text}){suffix}")
        if lines:
            embed.add_field(name="Attacken", value="\n".join(lines)[:1024], inline=False)
    image_url = str(card.get("bild") or "").strip()
    if image_url:
        embed.set_image(url=image_url)
    return embed


async def _send_private_invite_card_reward(
    interaction: discord.Interaction,
    inviter_id: int,
    card_name: str,
) -> None:
    embed = _build_invite_reward_card_embed(card_name)
    if interaction.user.id == int(inviter_id):
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        except Exception:
            logging.exception("Failed to send invite reward as ephemeral followup")
    try:
        user = bot.get_user(int(inviter_id)) or await bot.fetch_user(int(inviter_id))
        await user.send(embed=embed)
    except Exception:
        logging.exception("Failed to DM invite card reward")


async def _fetch_guild_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(int(user_id))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


async def _invitee_age_error(guild: discord.Guild, invitee_id: int) -> str | None:
    max_days = await get_invite_max_member_age_days()
    member = await _fetch_guild_member(guild, invitee_id)
    if member is None:
        return "Der Eingeladene wurde auf diesem Server nicht gefunden."
    joined_at = getattr(member, "joined_at", None)
    if joined_at is None:
        return "Das Beitrittsdatum des Eingeladenen konnte nicht geprüft werden."
    if joined_at.tzinfo is None:
        joined_at = joined_at.replace(tzinfo=timezone.utc)
    age_seconds = datetime.now(timezone.utc).timestamp() - joined_at.timestamp()
    if age_seconds > max_days * 86400:
        age_days = int(age_seconds // 86400)
        return (
            f"Der Eingeladene ist schon {age_days} Tage auf dem Server. "
            f"Erlaubt sind maximal {max_days} Tage."
        )
    return None


class InviteUserSelectView(RestrictedView):
    def __init__(self, requester_id: int, mode: str, available_user_ids: list[int]):
        super().__init__(timeout=None)
        self.requester_id = int(requester_id)
        self.mode = str(mode or "invitee")
        self.add_item(InviteUserSelect(self.requester_id, self.mode, available_user_ids))


class InviteUserSelect(ui.Select):
    def __init__(self, requester_id: int, mode: str, available_user_ids: list[int]):
        self.requester_id = int(requester_id)
        self.mode = str(mode or "invitee")
        options: list[SelectOption] = []
        for user_id in available_user_ids[:25]:
            user = bot.get_user(int(user_id))
            if user:
                primary_name = safe_display_name(user, fallback=f"<@{user_id}>")
                username = escape_display_text(getattr(user, "name", ""), fallback=primary_name)
                display_name = f"{primary_name} ({username})"
            else:
                display_name = f"Unbekannt (<@{user_id}>)"
            options.append(
                SelectOption(
                    label=display_name[:100],
                    value=str(user_id),
                    description="Einladung bestätigen",
                    emoji="🎁",
                )
            )
        if not options:
            options.append(SelectOption(label="Keine Spieler verfügbar", value="none"))
        placeholder = "Wähle den Eingeladenen" if self.mode == "inviter" else "Wähle deinen Einlader"
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Nur du kannst deine Auswahl treffen.", ephemeral=True)
            return
        if self.values[0] == "none":
            await interaction.response.send_message("❌ Keine Spieler verfügbar!", ephemeral=True)
            return

        selected_user_id = int(self.values[0])
        if selected_user_id == self.requester_id:
            await interaction.response.send_message("❌ Du kannst dich nicht selbst auswählen.", ephemeral=True)
            return

        if self.mode == "inviter":
            inviter_id = self.requester_id
            invitee_id = selected_user_id
        else:
            inviter_id = selected_user_id
            invitee_id = self.requester_id

        logging.info("[INVITED] mode=%s inviter_id=%s invitee_id=%s", self.mode, inviter_id, invitee_id)

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Das funktioniert nur auf einem Server.", ephemeral=True)
            return
        if interaction.channel is None:
            await interaction.response.send_message("❌ Kanal nicht gefunden.", ephemeral=True)
            return

        age_error = await _invitee_age_error(guild, invitee_id)
        if age_error:
            await interaction.response.send_message(f"❌ {age_error}", ephemeral=True)
            return

        existing = await find_existing_invite_pair(guild.id, inviter_id, invitee_id)
        if existing:
            status = str(existing.get("status") or "pending")
            await interaction.response.send_message(
                f"❌ Für diese zwei Personen gibt es bereits eine Einladung mit Status **{status}**.",
                ephemeral=True,
            )
            return

        invitee_member = await _fetch_guild_member(guild, invitee_id)
        invitee_is_admin = bool(invitee_member and any(role.permissions.administrator for role in invitee_member.roles))
        prior = await get_invite_completed_count(inviter_id)
        need_admin_gate = prior >= 5

        pending_id, created = await create_invite_pending(
            guild_id=guild.id,
            channel_id=interaction.channel.id,
            created_by_id=interaction.user.id,
            mode=self.mode,
            inviter_id=inviter_id,
            invitee_id=invitee_id,
            invitee_is_admin=invitee_is_admin,
            need_admin=need_admin_gate,
        )
        if not created:
            await interaction.response.send_message(
                "❌ Für diese zwei Personen gibt es bereits eine offene oder abgeschlossene Einladung.",
                ephemeral=True,
            )
            return

        inviter_u = bot.get_user(inviter_id)
        invitee_u = bot.get_user(invitee_id)
        im = inviter_u.mention if inviter_u else f"<@{inviter_id}>"
        em = invitee_u.mention if invitee_u else f"<@{invitee_id}>"
        admin_note = game_ui_texts.INVITE_ADMIN_NOTE if need_admin_gate else ""
        embed = discord.Embed(
            title=game_ui_texts.INVITE_CONFIRM_TITLE,
            description=game_ui_texts.INVITE_CONFIRM_DESCRIPTION.format(
                invitee=em,
                inviter=im,
                admin_note=admin_note,
            ),
            color=0x9D4EDD,
        )
        _apply_item_media(embed, "infinitydust", thumbnail=True)

        confirm_view = InviteConfirmationView(pending_id, need_admin_gate=need_admin_gate)
        sent = await _safe_send_channel(interaction, interaction.channel, embed=embed, view=confirm_view)
        if sent is not None:
            await set_invite_pending_message_id(pending_id, sent.id)

        try:
            await interaction.response.edit_message(
                content="✅ Öffentliche Bestätigung wurde gepostet. Bitte die Buttons dort nutzen.",
                embed=None,
                view=None,
            )
        except Exception:
            logging.exception("Failed to edit invite select message")


class DustAmountView(FuseFlowView):
    def __init__(self, requester_id: int, user_dust: int):
        super().__init__(requester_id, timeout=600)
        # Optional pre-bindings for the new card->stat->multiplier flow
        # introduced in v2.3.0 Task 6.3. The legacy DustAmountSelect path does
        # not consume these yet — Task 6.4 wires them through to the
        # multiplier-aware apply step.
        self.preselected_card: str | None = None
        self.preselected_stat_choice: str | None = None
        self.add_item(DustAmountSelect(int(user_dust)))
        self.add_item(FuseCancelButton())

# Slash-Command: Anfang (Hauptmenü)
class AnfangView(RestrictedView):
    def __init__(self, *, alpha_enabled: bool = False, beta_enabled: bool = False):
        super().__init__(timeout=None)
        self.alpha_enabled = bool(alpha_enabled)
        self.beta_enabled = bool(beta_enabled)
        if self.alpha_enabled:
            for child in list(self.children):
                if getattr(child, "custom_id", None) in {"anfang:mission", "anfang:story"}:
                    self.remove_item(child)
        elif self.beta_enabled:
            for child in list(self.children):
                if getattr(child, "custom_id", None) == "anfang:story":
                    self.remove_item(child)

    @ui.button(label="tägliche Karte", style=discord.ButtonStyle.success, row=0, custom_id="anfang:daily")
    async def btn_daily(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum täglichen Belohnungs-Flow weiter
        await _invoke_command_callback(daily_command, interaction)

    @ui.button(label="Verbessern", style=discord.ButtonStyle.primary, row=0, custom_id="anfang:fuse")
    async def btn_fuse(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fuse-Flow weiter
        await _invoke_command_callback(fuse, interaction)

    @ui.button(label="Kämpfe", style=discord.ButtonStyle.danger, row=0, custom_id="anfang:fight")
    async def btn_fight(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fight-Flow weiter
        await _invoke_command_callback(fight, interaction)

    @ui.button(label="Mission", style=discord.ButtonStyle.danger, row=0, custom_id="anfang:mission")
    async def btn_mission(self, interaction: discord.Interaction, button: ui.Button):
        if await is_alpha_enabled(interaction.guild_id):
            await _send_alpha_feature_blocked(interaction)
            return
        # Leitet zum Missions-Flow weiter
        await _invoke_command_callback(mission, interaction)

    @ui.button(label="Story", style=discord.ButtonStyle.danger, row=0, custom_id="anfang:story")
    async def btn_story(self, interaction: discord.Interaction, button: ui.Button):
        if await is_alpha_enabled(interaction.guild_id):
            await _send_alpha_feature_blocked(interaction)
            return
        if await is_beta_enabled(interaction.guild_id):
            await _send_ephemeral(interaction, content=BETA_STORY_DISABLED_TEXT)
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
        alpha_enabled = await is_alpha_enabled(interaction.guild_id)
        beta_enabled = await is_beta_enabled(interaction.guild_id)
        view = AnfangView(alpha_enabled=alpha_enabled, beta_enabled=beta_enabled)
        text = build_anfang_intro_text(alpha_enabled=alpha_enabled, beta_enabled=beta_enabled)
        await interaction.response.send_message(content=text, view=view, ephemeral=True)


class MaintenanceConfirmView(RestrictedView):
    def __init__(self, requester_id: int, *, enable: bool):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.enable = bool(enable)

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_YES, style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if interaction.guild is None:
            await _send_with_visibility(interaction, "maintenance", content=SERVER_ONLY)
            return
        await set_maintenance_mode(interaction.guild.id, self.enable)
        event_name = "admin_maintenance_on" if self.enable else "admin_maintenance_off"
        await _log_event_safe(
            event_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            command_name="entwicklerpanel",
        )
        await interaction.response.edit_message(
            content=(game_ui_texts.MAINTENANCE_ENABLED if self.enable else game_ui_texts.MAINTENANCE_DISABLED),
            view=None,
            embed=None,
        )

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_NO, style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await interaction.response.edit_message(content=game_ui_texts.MAINTENANCE_CANCELLED, view=None, embed=None)

class AlphaConfirmView(RestrictedView):
    def __init__(self, requester_id: int, *, enable: bool):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.enable = bool(enable)

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_YES, style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if interaction.guild is None:
            await _send_with_visibility(interaction, "feature_flags", content=SERVER_ONLY)
            return
        await set_alpha_enabled(interaction.guild.id, self.enable)
        refreshed = await refresh_latest_anfang_message_for_guild(interaction)
        event_name = "admin_alpha_on" if self.enable else "admin_alpha_off"
        await _log_event_safe(
            event_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            command_name="entwicklerpanel",
        )
        refresh_text = (
            game_ui_texts.FEATURE_FLAG_REFRESH_UPDATED
            if refreshed
            else game_ui_texts.FEATURE_FLAG_REFRESH_NOT_UPDATED
        )
        await interaction.response.edit_message(
            content=(game_ui_texts.ALPHA_ENABLED if self.enable else game_ui_texts.ALPHA_DISABLED) + f" {refresh_text}",
            view=None,
            embed=None,
        )

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_NO, style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await interaction.response.edit_message(content=game_ui_texts.ALPHA_CANCELLED, view=None, embed=None)


class BetaConfirmView(RestrictedView):
    def __init__(self, requester_id: int, *, enable: bool):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.enable = bool(enable)

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_YES, style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if interaction.guild is None:
            await _send_with_visibility(interaction, "feature_flags", content=SERVER_ONLY)
            return
        await set_beta_enabled(interaction.guild.id, self.enable)
        refreshed = await refresh_latest_anfang_message_for_guild(interaction)
        event_name = "admin_beta_on" if self.enable else "admin_beta_off"
        await _log_event_safe(
            event_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            command_name="entwicklerpanel",
        )
        refresh_text = (
            game_ui_texts.FEATURE_FLAG_REFRESH_UPDATED
            if refreshed
            else game_ui_texts.FEATURE_FLAG_REFRESH_NOT_UPDATED
        )
        await interaction.response.edit_message(
            content=(game_ui_texts.BETA_ENABLED if self.enable else game_ui_texts.BETA_DISABLED) + f" {refresh_text}",
            view=None,
            embed=None,
        )

    @ui.button(label=game_ui_texts.MAINTENANCE_CONFIRM_BTN_NO, style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await interaction.response.edit_message(content=game_ui_texts.BETA_CANCELLED, view=None, embed=None)

class UserSelectView(RestrictedView):
    def __init__(self, user_id, guild):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.guild = guild
        self.value = None
        self.members = sorted(
            [member for member in guild.members if not member.bot],
            key=lambda m: safe_display_name(m, fallback="Unbekannt").lower(),
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

async def _build_owned_card_detail(
    *,
    user_id: int,
    selected_name: str,
    variant_rows: list[tuple[str, int]] | None = None,
) -> tuple[discord.Embed, RestrictedView] | None:
    karte = await get_karte_by_name(selected_name)
    if not karte:
        return None
    embed = discord.Embed(
        title=str(karte.get("name") or selected_name),
        description=str(karte.get("beschreibung") or ""),
        color=_card_rarity_color(karte),
    )
    if karte.get("bild"):
        embed.set_image(url=str(karte.get("bild")))

    attacks = karte.get("attacks", [])
    user_buffs = await get_card_buffs(user_id, str(karte.get("base_name") or karte.get("name") or selected_name))
    damage_buff_map: dict[int, int] = {}
    for buff_type, attack_number, buff_amount in user_buffs:
        if buff_type == "damage" and 1 <= attack_number <= 4:
            damage_buff_map[int(attack_number)] = damage_buff_map.get(int(attack_number), 0) + int(buff_amount or 0)

    # v2.3.5: Leben/Stats ganz oben anzeigen (inkl. Aufwertungen aus /verbessern).
    total_health, _damage_map = battle_state.summarize_card_buffs(user_buffs)
    base_hp = int(karte.get("hp", 100) or 100)
    stat_lines = [f"❤️ Leben: **{base_hp + total_health} HP**"]
    rarity = str(karte.get("seltenheit") or "").strip()
    if rarity:
        stat_lines.append(f"✨ Seltenheit: **{rarity}**")
    embed.add_field(name="Werte", value="\n".join(stat_lines), inline=False)

    if attacks:
        lines = []
        for idx, atk in enumerate(attacks, start=1):
            buff = damage_buff_map.get(idx, 0)
            _button_label, _button_style, attack_summary = _attack_display_parts(atk, max_only_bonus=buff)
            # v2.3.5: Cooldown nach dem Schaden im Format {nCD} anhängen.
            cd_suffix = _format_attack_label(atk, is_on_cooldown=False)
            if isinstance(atk, dict) and cd_suffix.endswith("CD}"):
                suffix = cd_suffix[len(str(atk.get("name") or "")):].strip()
                if suffix:
                    attack_summary = f"{attack_summary} {suffix}"
            info_text = str(atk.get("info") or "").strip()
            if info_text:
                lines.append(f"• {attack_summary}\n  ↳ {info_text}")
            else:
                lines.append(f"• {attack_summary}")
        embed.add_field(name="Attacken", value="\n".join(lines), inline=False)

    variant_rows = list(variant_rows or [])
    if len(variant_rows) > 1:
        embed.add_field(
            name="Varianten",
            value="\n".join(f"• {variant_name} (x{amount})" for variant_name, amount in variant_rows),
            inline=False,
        )

    view_buttons = RestrictedView(timeout=60)
    for i, atk in enumerate(attacks[:4]):
        buff = damage_buff_map.get(i + 1, 0)
        button_label, button_style, _attack_summary = _attack_display_parts(atk, max_only_bonus=buff)
        btn = ui.Button(
            label=button_label,
            style=button_style,
            disabled=True,
            row=0 if i < 2 else 1,
        )
        view_buttons.add_item(btn)
    return embed, view_buttons


class VaultView(RestrictedView):
    def __init__(self, user_id: int, user_karten):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.user_karten = user_karten  # Liste (kartenname, anzahl)

        anzeigen_button = ui.Button(label="Anzeige", style=discord.ButtonStyle.primary)
        anzeigen_button.callback = self.on_anzeige
        self.add_item(anzeigen_button)

    async def _show_card_detail(self, interaction: discord.Interaction, base_name: str) -> None:
        variant_rows = _owned_variant_rows_for_base(self.user_karten, base_name)
        if not variant_rows:
            await interaction.response.send_message("Karte nicht gefunden.", ephemeral=True)
            return
        if len(variant_rows) == 1:
            detail_payload = await _build_owned_card_detail(
                user_id=self.user_id,
                selected_name=variant_rows[0][0],
                variant_rows=variant_rows,
            )
            if detail_payload is None:
                await interaction.response.send_message("Karte nicht gefunden.", ephemeral=True)
                return
            embed, view_buttons = detail_payload
            await interaction.response.send_message(embed=embed, view=view_buttons, ephemeral=True)
            return
        variant_view = CardVariantSelectView(self.user_id, base_name, variant_rows)
        await interaction.response.send_message(
            f"Wähle den Style für **{base_name}**:",
            view=variant_view,
            ephemeral=True,
        )
        await variant_view.wait()
        if not variant_view.value:
            return
        detail_payload = await _build_owned_card_detail(
            user_id=self.user_id,
            selected_name=variant_view.value,
            variant_rows=variant_rows,
        )
        if detail_payload is None:
            return
        embed, view_buttons = detail_payload
        await interaction.followup.send(embed=embed, view=view_buttons, ephemeral=True)

    async def on_anzeige(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Button!", ephemeral=True)
            return

        grouped_cards = _group_owned_cards_for_current_mode(list(self.user_karten))
        options = [
            SelectOption(label=_group_option_label(group)[:100], value=str(group.get("base_name") or ""))
            for group in grouped_cards
        ]

        if len(options) <= 25:
            select = ui.Select(placeholder="Wähle eine Karte zur Anzeige...", min_values=1, max_values=1, options=options)

            async def handle_select(interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                    return
                await self._show_card_detail(interaction, str(select.values[0] or "").strip())

            select.callback = handle_select
            view = RestrictedView(timeout=90)
            view.add_item(select)
            await interaction.response.send_message("Wähle eine Karte:", view=view, ephemeral=True)
            return

        pages = [options[i:i + 25] for i in range(0, len(options), 25)]
        current_index = 0

        async def send_page(interaction: discord.Interaction, page_index: int):
            sel = ui.Select(
                placeholder=f"Seite {page_index + 1}/{len(pages)} – Karte wählen...",
                min_values=1,
                max_values=1,
                options=pages[page_index],
            )

            async def handle_sel(interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                    return
                await self._show_card_detail(interaction, str(sel.values[0] or "").strip())

            sel.callback = handle_sel

            prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=page_index == 0)
            next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=page_index == len(pages) - 1)

            async def on_prev(interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
                    return
                await send_page(interaction, page_index - 1)

            async def on_next(interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
                    return
                await send_page(interaction, page_index + 1)

            prev_btn.callback = on_prev
            next_btn.callback = on_next

            v = RestrictedView(timeout=120)
            v.add_item(sel)
            v.add_item(prev_btn)
            v.add_item(next_btn)

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

    def __init__(
        self,
        user_id: int,
        mission_data: dict,
        *,
        is_admin: bool,
        user_karten: list[str] | None = None,
        selected_base_name: str | None = None,
    ):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_data = mission_data
        self.is_admin = is_admin
        self.user_karten = [str(name) for name in (user_karten or []) if str(name).strip()]
        self.selected_base_name = str(selected_base_name or "").strip() or None
        if self.selected_base_name:
            variant_rows = exact_variant_names_with_amounts(
                [(name, 1) for name in self.user_karten],
                self.selected_base_name,
                cards=karten,
            )
            options = [
                SelectOption(
                    label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                    value=variant_name,
                )
                for variant_name, amount in variant_rows[:25]
            ]
        else:
            grouped_cards = group_owned_cards_by_base([(name, 1) for name in self.user_karten], cards=karten)
            options = [SelectOption(label=_group_option_label(group)[:100], value=str(group.get("base_name") or "")) for group in grouped_cards[:25]]
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder=(
                f"Wähle den Style für {self.selected_base_name}..."
                if self.selected_base_name
                else "Wähle deine Karte für die Mission..."
            ),
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
            "selected_base_name": self.selected_base_name,
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        selected_name = str(self.select.values[0] or "").strip()
        if not selected_name or selected_name == "__none__":
            await interaction.response.send_message("❌ Keine gültige Karte verfügbar.", ephemeral=True)
            return
        if self.selected_base_name is None:
            self.selected_base_name = selected_name
            variant_rows = exact_variant_names_with_amounts(
                [(name, 1) for name in self.user_karten],
                self.selected_base_name,
                cards=karten,
            )
            if len(variant_rows) > 1:
                self.select.placeholder = f"Wähle den Style für {self.selected_base_name}..."
                self.select.options = [
                    SelectOption(
                        label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                        value=variant_name,
                    )
                    for variant_name, amount in variant_rows[:25]
                ]
                await interaction.response.edit_message(
                    content=f"Wähle den Style für **{self.selected_base_name}**:",
                    view=self,
                )
                return
            if variant_rows:
                selected_name = variant_rows[0][0]
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission start card select view")
        initial_state = {
            "mission_data": _json_clone(self.mission_data),
            "is_admin": self.is_admin,
            "selected_card_name": selected_name,
            "next_wave": 1,
            "total_waves": int(self.mission_data.get("waves", 1) or 1),
        }
        await _launch_mission_encounter_preview_or_wave(
            interaction,
            initial_state,
            self.user_id,
            _preview_mode_for_next_wave(initial_state),
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
            SelectOption(
                label=game_ui_texts.MISSION_PAUSE_KEEP_LABEL.format(card_name=current_card_name)[:100],
                value="keep",
            ),
        ]
        self.select = ui.Select(
            placeholder=game_ui_texts.MISSION_PAUSE_PLACEHOLDER,
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
            user_karten = _sort_user_cards_like_karten(
                _filter_owned_cards_for_current_mode(await get_user_karten(self.user_id))
            )
            next_view = MissionNewCardSelectView(self.user_id, user_karten, mission_state=self.mission_state)
            await _safe_send_channel(interaction, interaction.channel, content="Wähle eine neue Karte:", view=next_view)
        else:
            await _continue_mission_after_pause_or_card_pick(interaction, self.mission_state, self.user_id)
        self.stop()


class MissionNewCardSelectView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_NEW_CARD_SELECT

    def __init__(self, user_id, user_karten, *, mission_state: dict[str, Any], selected_base_name: str | None = None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_state = mission_state
        self.user_karten = [normalize_owned_card_name(karte_name, cards=karten) for karte_name, _amount in user_karten]
        self.selected_base_name = str(selected_base_name or "").strip() or None
        if self.selected_base_name:
            variant_rows = exact_variant_names_with_amounts(
                [(name, 1) for name in self.user_karten],
                self.selected_base_name,
                cards=karten,
            )
            options = [
                SelectOption(
                    label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                    value=variant_name,
                )
                for variant_name, amount in variant_rows[:25]
            ]
        else:
            grouped_cards = group_owned_cards_by_base([(name, 1) for name in self.user_karten], cards=karten)
            # Req. 1.2: ursprünglich gewählte Karte mit Marker "(aktuell)" als erste Option.
            raw_selected = str(self.mission_state.get("selected_card_name") or "")
            current_base = base_card_name(raw_selected, cards=karten) if raw_selected else ""
            ordered = sorted(
                grouped_cards,
                key=lambda g: 0 if str(g.get("base_name") or "") == current_base else 1,
            )
            options = []
            for group in ordered[:25]:
                base_name = str(group.get("base_name") or "")
                label = _group_option_label(group)
                if base_name and base_name == current_base:
                    label = f"{label} (aktuell)"
                options.append(SelectOption(label=label[:100], value=base_name))
        if not options:
            options = [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder=(
                f"Wähle den Style für {self.selected_base_name}..."
                if self.selected_base_name
                else "Wähle eine neue Karte..."
            ),
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
            "selected_base_name": self.selected_base_name,
        }

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        selected_name = str(self.select.values[0] or "").strip()
        if not selected_name or selected_name == "__none__":
            await interaction.response.send_message("❌ Keine gültige Karte verfügbar.", ephemeral=True)
            return
        if self.selected_base_name is None:
            self.selected_base_name = selected_name
            variant_rows = exact_variant_names_with_amounts(
                [(name, 1) for name in self.user_karten],
                self.selected_base_name,
                cards=karten,
            )
            if len(variant_rows) > 1:
                self.select.placeholder = f"Wähle den Style für {self.selected_base_name}..."
                self.select.options = [
                    SelectOption(
                        label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                        value=variant_name,
                    )
                    for variant_name, amount in variant_rows[:25]
                ]
                await interaction.response.edit_message(
                    content=f"Wähle den Style für **{self.selected_base_name}**:",
                    view=self,
                )
                return
            if variant_rows:
                selected_name = variant_rows[0][0]
        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission new-card select view")
        next_state = dict(self.mission_state)
        next_state["selected_card_name"] = selected_name
        await _continue_mission_after_pause_or_card_pick(interaction, next_state, self.user_id)
        self.stop()


class MissionBossReviveView(RestrictedView):
    def __init__(self, user_id: int, mission_state: dict[str, Any], *, cost: int, mode: str):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.mission_state = _json_clone(mission_state)
        self.cost = max(0, int(cost))
        self.mode = str(mode or "revive_continue").strip().lower()
        if self.mode not in {"revive_continue", "restart_boss"}:
            self.mode = "revive_continue"

        label = f"Für {self.cost} Unit wiederbeleben"
        self.revive_button = ui.Button(label=label[:80], style=discord.ButtonStyle.success)
        self.revive_button.callback = self.revive
        self.add_item(self.revive_button)

        self.cancel_button = ui.Button(label="Mission beenden", style=discord.ButtonStyle.danger)
        self.cancel_button.callback = self.cancel
        self.add_item(self.cancel_button)

    async def revive(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann das nutzen!", ephemeral=True)
            return
        if not await spend_units(self.user_id, self.cost):
            await interaction.response.send_message(f"❌ Du brauchst **{self.cost} Unit** für die Wiederbelebung.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission boss revive view")

        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"✅ {self.cost} Unit ausgegeben. Bosskampf wird fortgesetzt.",
        )
        await _start_mission_wave_in_thread(interaction, mission_state=_dict_str_any(self.mission_state))
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann das nutzen!", ephemeral=True)
            return
        await interaction.response.edit_message(content="Mission beendet.", embed=None, view=None)
        await _send_mission_feedback_prompt(
            interaction.channel,
            interaction.guild,
            allowed_user_id=self.user_id,
            battle_log_text="",
        )
        self.stop()


class MissionEncounterPreviewView(DurableView):
    durable_view_kind = VIEW_KIND_MISSION_ENCOUNTER_PREVIEW

    def __init__(self, user_id: int, mission_state: dict[str, Any], mode: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mode = str(mode or "lackeys").strip().lower()
        if self.mode not in {"lackeys", "boss"}:
            self.mode = "lackeys"
        self.mission_state = mission_state
        self._clamp_preview_index()
        self._build_button_rows()

    def _clamp_preview_index(self) -> None:
        slides = self._slides()
        if not slides:
            self.mission_state["preview_index"] = 0
            return
        idx = int(self.mission_state.get("preview_index", 0) or 0)
        self.mission_state["preview_index"] = max(0, min(idx, len(slides) - 1))

    def _slides(self) -> list[dict[str, Any]]:
        return _mission_preview_slides(
            _dict_str_any(self.mission_state.get("mission_data")),
            self.mode,
            wave_num=int(self.mission_state.get("next_wave", 1) or 1),
        )

    def build_embed(self) -> discord.Embed:
        slides = self._slides()
        idx = int(self.mission_state.get("preview_index", 0) or 0)
        if not slides:
            return discord.Embed(title="Vorschau", description="Keine Gegnerdaten.", color=0x95A5A6)
        wave_num = int(self.mission_state.get("next_wave", 1) or 1)
        total_waves = int(self.mission_state.get("total_waves", len(slides)) or len(slides))
        return _build_mission_enemy_preview_embed(
            slides[idx],
            mode=self.mode,
            index=max(0, wave_num - 1),
            total=max(1, total_waves),
        )

    def durable_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "mission_state": _json_clone(self.mission_state),
            "mode": self.mode,
        }

    def _build_button_rows(self) -> None:
        self.clear_items()
        slides = self._slides()
        n = len(slides)
        idx = int(self.mission_state.get("preview_index", 0) or 0)
        if self.mode == "boss":
            self.add_item(self._btn_start_boss())
            # Req. 1.6: Wechsel-Button nur anbieten, wenn boss_switch_enabled True ist.
            if boss_switch_enabled():
                self.add_item(self._btn_hero())
            return
        if n == 0:
            return
        if n == 1:
            self.add_item(self._btn_start_mission())
            if self._allows_card_change():
                self.add_item(self._btn_hero())
            return
        if idx < n - 1:
            self.add_item(self._btn_next())
        else:
            self.add_item(self._btn_start_mission())
            if self._allows_card_change():
                self.add_item(self._btn_hero())

    def _allows_card_change(self) -> bool:
        if self.mode == "boss":
            return boss_switch_enabled()
        return int(self.mission_state.get("next_wave", 1) or 1) <= 1

    def _btn_next(self) -> ui.Button:
        b = ui.Button(
            label=game_ui_texts.PREVIEW_BTN_NEXT,
            style=discord.ButtonStyle.primary,
            custom_id="mission_enc_prv:next",
            row=0,
        )
        b.callback = self._cb_next
        return b

    def _btn_start_mission(self) -> ui.Button:
        b = ui.Button(
            label=game_ui_texts.PREVIEW_BTN_START_MISSION,
            style=discord.ButtonStyle.success,
            custom_id="mission_enc_prv:start_m",
            row=1,
        )
        b.callback = self._cb_start
        return b

    def _btn_start_boss(self) -> ui.Button:
        b = ui.Button(
            label=game_ui_texts.PREVIEW_BTN_START_BOSS,
            style=discord.ButtonStyle.success,
            custom_id="mission_enc_prv:start_b",
            row=1,
        )
        b.callback = self._cb_start
        return b

    def _btn_hero(self) -> ui.Button:
        b = ui.Button(
            label=game_ui_texts.PREVIEW_BTN_CHANGE_HERO,
            style=discord.ButtonStyle.secondary,
            custom_id="mission_enc_prv:hero",
            row=1,
        )
        b.callback = self._cb_hero
        return b

    async def _touch_durable(self, interaction: discord.Interaction) -> None:
        msg = interaction.message
        if msg is not None and isinstance(getattr(msg, "guild", None), discord.Guild):
            await _maybe_register_durable_message(msg, self)

    async def _cb_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann das steuern!", ephemeral=True)
            return
        slides = self._slides()
        idx = int(self.mission_state.get("preview_index", 0) or 0)
        if idx < len(slides) - 1:
            self.mission_state["preview_index"] = idx + 1
        self._clamp_preview_index()
        self._build_button_rows()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await self._touch_durable(interaction)

    async def _cb_start(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann das steuern!", ephemeral=True)
            return
        await self._finalize_wave_start(interaction)

    async def _cb_hero(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann das steuern!", ephemeral=True)
            return
        if not self._allows_card_change():
            await interaction.response.send_message(game_ui_texts.PREVIEW_CHANGE_NOT_AVAILABLE, ephemeral=True)
            return
        msg = interaction.message
        await interaction.response.defer()
        await self.clear_durable_registration()
        try:
            if msg is not None:
                await msg.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission encounter preview (hero)")
        ms = _strip_mission_preview_keys(self.mission_state)
        md = _dict_str_any(ms.get("mission_data"))
        user_karten = _sort_user_cards_like_karten(
            _filter_owned_cards_for_current_mode(await get_user_karten(self.user_id))
        )
        if int(ms.get("next_wave", 1) or 1) <= 1 and self.mode != "boss":
            select_view = MissionStartCardSelectView(
                self.user_id,
                md,
                is_admin=bool(ms.get("is_admin", False)),
                user_karten=[name for name, _amount in user_karten],
            )
            content = game_ui_texts.PREVIEW_RESELECT_CARD_PROMPT.format(mention=interaction.user.mention)
            embed = _build_mission_embed(
                md,
                user_already_owns_reward=await _user_already_owns_card(
                    self.user_id,
                    md.get("reward_card"),
                ),
            )
        else:
            select_view = MissionNewCardSelectView(self.user_id, user_karten, mission_state=ms)
            content = game_ui_texts.MISSION_SELECT_NEW_CARD_PROMPT
            embed = None
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=content,
            embed=embed,
            view=select_view,
        )
        self.stop()

    async def _finalize_wave_start(self, interaction: discord.Interaction):
        msg = interaction.message
        await interaction.response.defer()
        await self.clear_durable_registration()
        try:
            if msg is not None:
                await msg.edit(view=None)
        except Exception:
            logging.exception("Failed to clear mission encounter preview")
        ms = _strip_mission_preview_keys(self.mission_state)
        await _start_mission_wave_in_thread(interaction, mission_state=ms)
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
        self.session_kind = "mission"
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
        self.last_special_attack = runtime_maps["last_special_attack"]
        self._last_damage_roll_meta: dict | None = None
        self._optional_attack_confirmations: dict[int, dict[str, object]] = {}
        self.maestro_execute_pending = bool(self.mission_data.get("maestro_execute_pending", False))
        self._last_player_damage_dealt = int(self.mission_data.get("last_player_damage_dealt", 0) or 0)
        self._mission_actor_turn = "player"

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
            "last_special_attack": _json_clone(self.last_special_attack),
            "maestro_execute_pending": bool(self.maestro_execute_pending),
            "last_player_damage_dealt": int(self._last_player_damage_dealt),
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
        self.special_lock_next_turn = _int_keyed_int_dict(payload.get("special_lock_next_turn"))
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
        raw_last_special = _int_keyed_dict(payload.get("last_special_attack"))
        self.last_special_attack = {key: (value if isinstance(value, dict) else None) for key, value in raw_last_special.items()}
        self.maestro_execute_pending = bool(payload.get("maestro_execute_pending", self.maestro_execute_pending))
        self._last_player_damage_dealt = int(payload.get("last_player_damage_dealt", 0) or 0)
        self.round_counter = int(payload.get("round_counter", 0) or 0)
        self._battle_log_text_cache = str(payload.get("battle_log_text") or "")
        self.attacks = list(self.player_card.get("attacks", self.attacks))
        self._optional_attack_confirmations = {}
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
        was_new_session = self.session_id is None
        if battle_message is not None:
            self.bind_durable_message(guild_id=guild.id, channel_id=channel_id, message_id=battle_message.id)
        self.session_id = await save_active_session(
            session_id=self.session_id,
            kind=self.session_kind,
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
        # v2.3.5: AFK-Markierung auch in Missions-Threads. Bei jeder aktiven Persistierung
        # (Wellenstart + nach jedem Spielerzug) wird der Timer neu gesetzt; bleibt der Spieler
        # zu lange inaktiv, pingt der AFK-Loop ihn im Thread an.
        if status == "active" and isinstance(channel, discord.Thread):
            try:
                await afk_tracker.create_mission_state(
                    battle_id=f"mission:{channel_id}:{self.user_id}",
                    thread_id=channel_id,
                    user_id=self.user_id,
                )
            except Exception:
                logging.exception("Failed to upsert mission AFK state")
        if was_new_session and status == "active":
            await _log_event_safe(
                "mission_wave_started",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=self.user_id,
                hero_name=self.player_card.get("name"),
                payload={
                    "wave_num": int(self.wave_num),
                    "total_waves": int(self.total_waves),
                    "bot_hero": self.bot_card.get("name"),
                },
            )
            await _log_event_safe(
                "hero_selected",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=self.user_id,
                hero_name=self.player_card.get("name"),
                payload={"side": "mission_player"},
            )
            await _log_event_safe(
                "hero_selected",
                guild_id=guild.id,
                channel_id=channel_id,
                thread_id=_thread_id_for_channel(channel),
                session_id=self.session_id,
                session_kind=self.session_kind,
                actor_user_id=0,
                target_user_id=self.user_id,
                hero_name=self.bot_card.get("name"),
                payload={"side": "mission_bot"},
            )

    def _enemy_cooldown_lines(self) -> list[str]:
        """Zeigt im eigenen Zug, welche Spezialfähigkeiten des Gegners bereit/auf Cooldown sind."""
        lines: list[str] = []
        attacks = self.bot_card.get("attacks", []) or []
        for i, atk in enumerate(attacks[:4]):
            if not isinstance(atk, dict):
                continue
            try:
                cd = int(atk.get("cooldown_turns") or 0)
            except (TypeError, ValueError):
                cd = 0
            if cd <= 0:
                continue  # Standardangriffe haben keinen Cooldown – nicht auflisten
            name = str(atk.get("name") or f"Angriff {i+1}")
            remaining = int(self.bot_attack_cooldowns.get(i, 0) or 0)
            if remaining > 0:
                lines.append(f"• {name}: 🔒 noch {remaining} {'Zug' if remaining == 1 else 'Züge'}")
            else:
                lines.append(f"• {name}: ✅ bereit")
        return lines

    def _add_enemy_cooldown_field(self, embed: discord.Embed) -> None:
        lines = self._enemy_cooldown_lines()
        if not lines:
            return
        value = "\n".join(lines)
        if len(value) > 1024:
            value = value[:1021] + "..."
        embed.add_field(name="🛡️ Gegner-Cooldowns", value=value, inline=False)

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
        self._add_enemy_cooldown_field(embed)
        return embed

    def create_bot_spotlight_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"⚔️ Welle {self.wave_num}/{self.total_waves}",
            description=game_ui_texts.ENEMY_TURN_DESCRIPTION.format(enemy_name=self.bot_card["name"]),
        )
        player_label = f"🟥 Deine Karte{self._status_icons(self.user_id)}"
        bot_label = f"🟦 Gegner{self._status_icons(0)}"
        embed.add_field(name=player_label, value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
        embed.add_field(name=bot_label, value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
        if self.bot_card.get("bild"):
            embed.set_image(url=str(self.bot_card["bild"]))
        if self.player_card.get("bild"):
            embed.set_thumbnail(url=str(self.player_card["bild"]))
        _add_attack_info_field(embed, self.bot_card, include_passives=True)
        return embed

    async def _log_mission_attack_event(
        self,
        *,
        attacker_user_id: int,
        defender_user_id: int,
        attacker_name: str,
        attack_name: str,
        actual_damage: int,
        is_critical: bool,
        round_number: int,
        defender_remaining_hp: int,
        attacker_remaining_hp: int | None,
        pre_effect_damage: int,
        self_hit_damage: int,
        effect_events: list[str],
    ) -> None:
        await _log_event_safe(
            "attack_used",
            guild_id=self._durable_guild_id,
            channel_id=self._durable_channel_id,
            thread_id=self._durable_channel_id,
            session_id=self.session_id,
            session_kind=self.session_kind,
            actor_user_id=attacker_user_id,
            target_user_id=defender_user_id,
            hero_name=attacker_name,
            attack_name=attack_name,
            payload={
                "round": int(round_number),
                "is_critical": bool(is_critical),
                "damage": _damage_breakdown_payload(
                    actual_damage=int(actual_damage),
                    pre_effect_damage=int(pre_effect_damage),
                    effect_events=effect_events,
                    self_hit_damage=int(self_hit_damage),
                ),
                "defender_remaining_hp": int(defender_remaining_hp or 0),
                "attacker_remaining_hp": int(attacker_remaining_hp or 0) if attacker_remaining_hp is not None else None,
                "effect_events": [str(item) for item in effect_events or []],
                "wave_num": int(self.wave_num),
                "total_waves": int(self.total_waves),
            },
        )

    async def _complete_wave(
        self,
        interaction: discord.Interaction,
        message: discord.Message | None,
        *,
        won: bool,
        cancel_actor: discord.abc.User | None = None,
        detail_text: str | None = None,
    ) -> None:
        # v2.3.5: AFK-Timer dieser Mission entfernen (Welle endet / Übergang / Abbruch).
        # Bei einem Welle-Übergang legt die persist_session der nächsten Welle ihn neu an.
        try:
            _afk_channel_id = getattr(interaction.channel, "id", None) or getattr(self, "_durable_channel_id", None)
            if _afk_channel_id:
                await afk_tracker.delete_state(f"mission:{int(_afk_channel_id)}:{self.user_id}")
        except Exception:
            logging.exception("Failed to delete mission AFK state")
        if message is not None:
            try:
                summary_embed = self.create_current_embed(description=detail_text or ("Welle gewonnen." if won else "Welle verloren."))
                await message.edit(embed=summary_embed, view=None)
            except Exception:
                logging.exception("Failed to update mission battle message at wave completion")
        if not won:
            is_boss_wave = bool(self.wave_num >= self.total_waves)
            if cancel_actor is None and is_boss_wave:
                cost, revive_mode = _unit_boss_revive_config()
                revive_state: dict[str, Any] = {
                    "mission_data": _json_clone(self.mission_data),
                    "is_admin": self.is_admin,
                    "selected_card_name": self.selected_card_name,
                    "next_wave": self.wave_num,
                    "total_waves": self.total_waves,
                    "mission_counted": bool(self.mission_data.get("mission_counted")),
                    "unit_awarded": bool(self.mission_data.get("unit_awarded")),
                    "full_heal": True,
                }
                if revive_mode == "revive_continue":
                    revive_state["bot_hp"] = max(1, int(self.bot_hp))
                    revive_state["bot_max_hp"] = int(self.bot_max_hp)
                current_units = await get_units(self.user_id)
                after_units = max(0, current_units - cost)
                embed = discord.Embed(
                    title="Bosskampf verloren",
                    description=(
                        f"Wiederbeleben kostet **{cost} Unit**.\n"
                        f"Aktuell hast du **{current_units} Unit**.\n"
                        f"Nach dem Wiederbeleben hättest du **{after_units} Unit**."
                    ),
                    color=0xE67E22,
                )
                _apply_item_media(embed, "unit", thumbnail=True)
                await _safe_send_channel(
                    interaction,
                    interaction.channel,
                    embed=embed,
                    view=MissionBossReviveView(self.user_id, revive_state, cost=cost, mode=revive_mode),
                )
                return

            status = "cancelled" if cancel_actor is not None else "failed"
            await self.persist_session(interaction.channel, status=status)
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

        await self.persist_session(interaction.channel, status="completed")

        # Req. 7.1/7.2: pro gewonnener Welle den in mission_dust_config.py konfigurierten
        # Staub-Betrag aufaddieren (Welle 1-3 = Lakeien, Welle 4 = Boss). Ausgezahlt wird
        # erst beim Mission-Erfolg (Req. 7.5).
        self.mission_data["reward_infinitydust"] = (
            int(self.mission_data.get("reward_infinitydust", 0) or 0)
            + mission_rewards.wave_dust_reward(int(self.wave_num))
        )

        next_wave = self.wave_num + 1
        if next_wave > self.total_waves:
            reward_card = _dict_str_any(self.mission_data.get("reward_card"))
            is_new_card = True
            if reward_card:
                is_new_card = await check_and_add_karte(self.user_id, reward_card)
                await _safe_send_channel(
                    interaction,
                    interaction.channel,
                    embed=_mission_success_embed(reward_card, self.total_waves, is_new_card=is_new_card),
                )
            # Req. 7.3/7.4/7.5/7.7: akkumulierte Belohnung auszahlen (Lakeien + Boss),
            # plus +1, falls die verknüpfte Reward-Karte bereits im Besitz war. Cap 5.
            acc = MissionRewardAccumulator(
                user_id=self.user_id,
                mission_id=str(self.mission_data.get("mission_id") or ""),
            )
            acc.infinitydust = int(self.mission_data.get("reward_infinitydust", 0) or 0)
            if reward_card and not is_new_card:
                acc.on_daily_card_already_owned()
            try:
                await commit_on_mission_success(acc, add_infinitydust=add_infinitydust)
            except Exception:
                logging.exception("Infinitydust-Mission-Auszahlung fehlgeschlagen")
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
            "mission_counted": bool(self.mission_data.get("mission_counted")),
            "unit_awarded": bool(self.mission_data.get("unit_awarded")),
            "player_hp": int(self.player_hp),
            "player_max_hp": int(self.player_max_hp),
        }
        next_is_boss_wave = bool(next_wave >= self.total_waves)
        if should_carry_cooldowns("mission") and should_carry_mission_cooldowns(is_boss_wave=next_is_boss_wave):
            next_state["player_attack_cooldowns_by_name"] = _cooldowns_by_attack_name(
                [atk for atk in self.attacks if isinstance(atk, dict)],
                self.user_attack_cooldowns,
            )
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"🏆 Welle {self.wave_num} gewonnen!",
        )
        interlude_after_wave = int(self.mission_data.get("interlude_after_wave", 0) or 0)
        if interlude_after_wave == self.wave_num and next_wave == interlude_after_wave + 1:
            if not bool(next_state.get("unit_awarded")):
                unit_reward = max(0, int(self.mission_data.get("unit_reward_after_wave", 0) or 0))
                if unit_reward > 0:
                    await add_units(self.user_id, unit_reward)
                    next_state["unit_awarded"] = True
                    next_state["mission_data"]["unit_awarded"] = True
            # v2.3.5 (2026-05-31): Den akkumulierten Wellen-Staub (Welle 1-3) sofort in der
            # Pause auszahlen – "alle 3 Wellen geschafft = 1 Staub + 1 Unit" gilt damit auch,
            # wenn man danach am Boss verliert. Danach reward_infinitydust nullen, damit der
            # Mission-Erfolg-Akkumulator (Boss) ihn nicht erneut auszahlt.
            interlude_dust = 0
            if not bool(next_state.get("wave_dust_awarded")):
                pending_dust = int(self.mission_data.get("reward_infinitydust", 0) or 0)
                if pending_dust > 0:
                    try:
                        await add_infinitydust(self.user_id, pending_dust)
                        interlude_dust = pending_dust
                    except Exception:
                        logging.exception("Wellen-Dust-Auszahlung in der Pause fehlgeschlagen")
                next_state["wave_dust_awarded"] = True
                next_state["mission_data"]["reward_infinitydust"] = 0
            next_state.pop("player_hp", None)
            next_state.pop("player_max_hp", None)
            next_state["full_heal"] = True
            interlude_embed = discord.Embed(
                title=str(self.mission_data.get("interlude_title") or "Missionspause"),
                description=str(self.mission_data.get("interlude_text") or "Bereite dich auf den nächsten Kampf vor."),
                color=0x2F80ED,
            )
            reward_parts: list[str] = []
            if self.mission_data.get("unit_reward_after_wave"):
                reward_parts.append(f"+{int(self.mission_data.get('unit_reward_after_wave') or 0)} Unit")
            if interlude_dust > 0:
                reward_parts.append(f"+{interlude_dust} Infinitydust")
            if reward_parts:
                interlude_embed.add_field(name="Belohnung", value="\n".join(reward_parts), inline=True)
                _apply_item_media(interlude_embed, "unit", image=False, thumbnail=True)
            interlude_embed.add_field(name="Heilung", value=game_ui_texts.INTERLUDE_HEAL_FIELD, inline=True)
            pause_view = MissionPauseView(self.user_id, self.selected_card_name, mission_state=next_state)
            await _safe_send_channel(
                interaction,
                interaction.channel,
                embed=interlude_embed,
                view=pause_view,
            )
            return
        await _launch_mission_encounter_preview_or_wave(
            interaction,
            next_state,
            self.user_id,
            "boss" if next_is_boss_wave else "lackeys",
        )

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

    def _mission_boss_key(self) -> str:
        return str(self.bot_card.get("mission_boss") or "").strip().lower()

    def _is_maestro_boss(self) -> bool:
        return _is_operation_broken_timeline(self.mission_data) and self._mission_boss_key() == "maestro"

    def _is_modok_boss(self) -> bool:
        return self._mission_boss_key() == "modok"

    def _is_green_goblin_boss(self) -> bool:
        return self._mission_boss_key() == "green_goblin"

    def _is_kingpin_boss(self) -> bool:
        return self._mission_boss_key() == "kingpin"

    def _is_agatha_boss(self) -> bool:
        return self._mission_boss_key() == "agatha_harkness"

    def _sync_maestro_execute_for_current_hp(self, effect_events: list[str]) -> None:
        if not self._is_maestro_boss() or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        if self.player_hp >= 35 and self.maestro_execute_pending:
            self.maestro_execute_pending = False
            self.mission_data["maestro_execute_pending"] = False
            self._append_effect_event(effect_events, game_ui_texts.MAESTRO_EXECUTE_CANCELLED)

    def _mark_maestro_execute_if_needed(self, effect_events: list[str]) -> None:
        if not self._is_maestro_boss() or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        if self.player_hp < 35 and not self.maestro_execute_pending:
            self.maestro_execute_pending = True
            self.mission_data["maestro_execute_pending"] = True
            self._append_effect_event(effect_events, game_ui_texts.MAESTRO_EXECUTE_MARKED)

    def _forced_maestro_execute_attack(self, effect_events: list[str]) -> dict[str, Any] | None:
        self._sync_maestro_execute_for_current_hp(effect_events)
        if not self._is_maestro_boss() or not self.maestro_execute_pending or self.player_hp <= 0 or self.bot_hp <= 0:
            return None
        self.maestro_execute_pending = False
        self.mission_data["maestro_execute_pending"] = False
        self._append_effect_event(effect_events, game_ui_texts.MAESTRO_EXECUTE_FIRED)
        return {
            "name": "Gnadenschuss des Tyrannen",
            "damage": [999, 999],
            "ignore_defense": True,
            "ignore_shield": True,
            "unblockable": True,
            "info": "Automatische Spezial-Aktion, wenn der Spieler unter 35 HP gefallen ist.",
        }

    def _attack_cooldown_turns(self, attack: dict[str, Any]) -> int:
        try:
            return max(0, int(attack.get("cooldown_turns", 0) or 0))
        except Exception:
            return 0

    def _player_action_pattern_type(
        self,
        attack: dict[str, Any],
        *,
        attack_index: int,
        is_reload_action: bool,
        is_forced_landing: bool,
    ) -> str | None:
        if is_reload_action or is_forced_landing:
            return None
        if _is_standard_attack(self.attacks, attack_index):
            return "standard"
        if self._attack_cooldown_turns(attack) > 0:
            return "special"
        return None

    def _apply_agatha_action_pattern(self, effect_events: list[str], action_type: str | None) -> None:
        if not self._is_agatha_boss() or not action_type or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        previous_type = str(self.mission_data.get("agatha_last_player_action_type") or "")
        if previous_type == action_type == "special":
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.user_id,
                25,
                source="Magisches Feedback",
                self_damage=True,
            )
            self._append_effect_event(effect_events, game_ui_texts.AGATHA_SPECIAL_FEEDBACK)
        elif previous_type == action_type == "standard":
            healed = self.heal_player(0, 25)
            if healed > 0:
                self._append_effect_event(effect_events, game_ui_texts.AGATHA_STANDARD_HEAL.format(amount=healed))
        self.mission_data["agatha_last_player_action_type"] = action_type

    def _apply_modok_neural_feedback(self, effect_events: list[str], attack: dict[str, Any], *, is_reload_action: bool) -> None:
        if not self._is_modok_boss() or is_reload_action or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        if self._attack_cooldown_turns(attack) < 5:
            return
        self._apply_non_heal_damage_with_event(
            effect_events,
            self.user_id,
            15,
            source="Neuronales Feedback",
            self_damage=True,
        )
        self._append_effect_event(effect_events, game_ui_texts.MODOK_NEURAL_FEEDBACK)

    def _prepare_kingpin_information_for_player_action(self, effect_events: list[str], *, is_reload_action: bool) -> None:
        if not self._is_kingpin_boss() or is_reload_action or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        turn_count = int(self.mission_data.get("kingpin_player_turn_count", 0) or 0) + 1
        self.mission_data["kingpin_player_turn_count"] = turn_count
        if turn_count % 4 == 0 and not bool(self.mission_data.get("kingpin_information_pending", False)):
            self.mission_data["kingpin_information_pending"] = True
            self._append_effect_event(effect_events, game_ui_texts.KINGPIN_INFORMATION_READY)

    def _consume_kingpin_information(self, effect_events: list[str], damage: int) -> int:
        if not self._is_kingpin_boss() or not bool(self.mission_data.get("kingpin_information_pending", False)):
            return max(0, int(damage or 0))
        self.mission_data["kingpin_information_pending"] = False
        prevented_damage = max(0, int(damage or 0))
        healed = self.heal_player(0, prevented_damage) if prevented_damage > 0 else 0
        self._append_effect_event(
            effect_events,
            game_ui_texts.KINGPIN_INFORMATION_CONSUMED.format(damage=prevented_damage, healed=healed),
        )
        return 0

    def _prepare_green_goblin_bomb_for_player_action(self, effect_events: list[str], *, is_reload_action: bool) -> None:
        if not self._is_green_goblin_boss() or is_reload_action or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        turn_count = int(self.mission_data.get("green_goblin_player_turn_count", 0) or 0) + 1
        self.mission_data["green_goblin_player_turn_count"] = turn_count
        active_bomb = self.mission_data.get("green_goblin_mega_bomb")
        if turn_count % 3 == 0 and not isinstance(active_bomb, dict):
            self.mission_data["green_goblin_mega_bomb"] = {"turns_left": 2, "damage_done": 0}
            self._append_effect_event(effect_events, game_ui_texts.GREEN_GOBLIN_BOMB_ARMED)

    def _resolve_green_goblin_bomb_after_player_attack(self, effect_events: list[str], *, damage_dealt: int) -> None:
        if not self._is_green_goblin_boss() or self.player_hp <= 0 or self.bot_hp <= 0:
            return
        active_bomb = self.mission_data.get("green_goblin_mega_bomb")
        if not isinstance(active_bomb, dict):
            return
        progress = max(0, int(active_bomb.get("damage_done", 0) or 0)) + max(0, int(damage_dealt or 0))
        turns_left = max(0, int(active_bomb.get("turns_left", 0) or 0)) - 1
        if progress >= 30:
            self.mission_data.pop("green_goblin_mega_bomb", None)
            self._append_effect_event(effect_events, game_ui_texts.GREEN_GOBLIN_BOMB_DEFUSED.format(progress=progress))
            return
        if turns_left <= 0:
            self.mission_data.pop("green_goblin_mega_bomb", None)
            self._apply_non_heal_damage_with_event(
                effect_events,
                self.user_id,
                50,
                source="Mega-Kürbisbombe",
                self_damage=False,
            )
            self._append_effect_event(effect_events, game_ui_texts.GREEN_GOBLIN_BOMB_EXPLODED)
            return
        self.mission_data["green_goblin_mega_bomb"] = {"turns_left": turns_left, "damage_done": progress}
        self._append_effect_event(
            effect_events,
            game_ui_texts.GREEN_GOBLIN_BOMB_PROGRESS.format(progress=progress, turns=turns_left),
        )

    def _apply_on_hit_passives(self, effect_events: list[str], *, damage_dealt: int) -> None:
        if damage_dealt <= 0:
            return
        for passive in self.bot_card.get("passives", []) or []:
            if not isinstance(passive, dict):
                continue
            if str(passive.get("type") or "").strip().lower() != "on_hit_recoil":
                continue
            amount = _random_int_from_range(passive.get("damage", 0), default=0)
            if amount <= 0:
                continue
            source = str(passive.get("source") or "Passiver Effekt")
            self._apply_non_heal_damage_with_event(effect_events, self.user_id, amount, source=source, self_damage=True)

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
            except discord.NotFound:
                self.battle_log_message = None
                return
            except discord.Forbidden:
                self.battle_log_message = None
                return
            except Exception as e:
                if getattr(e, "status", None) == 429:
                    await asyncio.sleep(_rate_limit_delay_from_error(e, attempt))
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
        landing_attack: dict[str, object] | None = None,
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
            landing_attack=landing_attack,
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
        max_store: int | None = None,
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
            max_store=max_store,
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
        ignore_all_defense: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int, dict[str, object] | None]:
        return battle_state.resolve_incoming_modifiers(
            self.incoming_modifiers,
            self.absorbed_damage,
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            ignore_all_defense=ignore_all_defense,
            incoming_min_damage=incoming_min_damage,
        )

    def resolve_incoming_modifiers(
        self,
        defender_id: int,
        raw_damage: int,
        ignore_evade: bool = False,
        ignore_all_defense: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int]:
        final_damage, reflected_damage, dodged, counter_damage, _modifier_details = self._resolve_incoming_modifiers_with_details(
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            ignore_all_defense=ignore_all_defense,
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
        is_bot_turn = str(getattr(self, "_mission_actor_turn", "player")) == "bot"
        current_attacks = list(self.bot_card.get("attacks", [])) if is_bot_turn else list(self.attacks)
        standard_idx = _standard_attack_index(current_attacks)

        pending_landing = self.airborne_pending_landing.get(self.user_id if not is_bot_turn else 0)
        if pending_landing:
            landing_slot = _pending_landing_slot_index(pending_landing)
            raw_landing_attack = pending_landing.get("attack")
            landing_attack = raw_landing_attack if isinstance(raw_landing_attack, dict) else {}
            landing_damage = pending_landing.get("damage", [20, 40])
            if isinstance(landing_damage, list) and len(landing_damage) == 2:
                dmg_text = f"{int(landing_damage[0])}-{int(landing_damage[1])}"
            else:
                dmg_text = "20-40"
            landing_name = str(landing_attack.get("name") or "Landungsschlag")
            for i, btn in enumerate(attack_buttons):
                btn.style = discord.ButtonStyle.secondary
                if i >= len(current_attacks):
                    btn.label = "—"
                    btn.disabled = True
                    continue
                if i == landing_slot:
                    btn.style = discord.ButtonStyle.danger
                    btn.label = f"{landing_name} ({dmg_text}) ✈️"
                    btn.disabled = bool(is_bot_turn)
                    continue
                blocked_attack = current_attacks[i]
                blocked_name = str(blocked_attack.get("name") or f"Angriff {i+1}")
                if (self.is_attack_on_cooldown_bot(i) if is_bot_turn else self.is_attack_on_cooldown_user(i)):
                    cooldown_turns = (self.bot_attack_cooldowns if is_bot_turn else self.user_attack_cooldowns).get(i, 0)
                    btn.label = f"{blocked_name} ({_format_cooldown_label(blocked_attack, cooldown_turns)})"
                else:
                    btn.label = f"{blocked_name} (Blockiert)"
                btn.disabled = True
            return

        lock_target_id = 0 if is_bot_turn else self.user_id
        if self.special_lock_next_turn.get(lock_target_id, 0) > 0:
            for i, button in enumerate(attack_buttons):
                button.style = discord.ButtonStyle.secondary
                if i >= len(current_attacks):
                    button.label = "—"
                    button.disabled = True
                    continue
                attack = current_attacks[i]
                if i == standard_idx:
                    dmg_max_bonus = 0 if is_bot_turn else self.damage_bonuses.get(i + 1, 0)
                    display_label, display_style, _ = _attack_display_parts(
                        attack,
                        max_only_bonus=dmg_max_bonus,
                    )
                    is_on_cooldown = self.is_attack_on_cooldown_bot(i) if is_bot_turn else self.is_attack_on_cooldown_user(i)
                    is_reload_action = False if is_bot_turn else bool(attack.get("requires_reload") and self.is_reload_needed(self.user_id, i))
                    if is_on_cooldown:
                        cooldown_turns = (self.bot_attack_cooldowns if is_bot_turn else self.user_attack_cooldowns)[i]
                        button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                        button.disabled = True
                    elif is_reload_action and (not is_bot_turn):
                        button.style = discord.ButtonStyle.primary
                        button.label = str(attack.get("reload_name") or "Nachladen")
                        button.disabled = False
                    else:
                        button.style = display_style
                        button.label = display_label
                        button.disabled = bool(is_bot_turn)
                    continue
                attack_name = str(attack.get("name") or f"Angriff {i+1}")
                if (self.is_attack_on_cooldown_bot(i) if is_bot_turn else self.is_attack_on_cooldown_user(i)):
                    cooldown_turns = (self.bot_attack_cooldowns if is_bot_turn else self.user_attack_cooldowns).get(i, 0)
                    button.label = f"{attack_name} ({_format_cooldown_label(attack, cooldown_turns)})"
                else:
                    button.label = f"{attack_name} (Gesperrt)"
                button.disabled = True
            return

        for i, button in enumerate(attack_buttons):
            if i < len(current_attacks):
                attack = current_attacks[i]
                dmg_max_bonus = 0 if is_bot_turn else self.damage_bonuses.get(i + 1, 0)
                display_label, display_style, _ = _attack_display_parts(
                    attack,
                    max_only_bonus=dmg_max_bonus,
                )
                is_on_cooldown = self.is_attack_on_cooldown_bot(i) if is_bot_turn else self.is_attack_on_cooldown_user(i)
                is_reload_action = False if is_bot_turn else bool(attack.get("requires_reload") and self.is_reload_needed(self.user_id, i))
                if is_on_cooldown:
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = (self.bot_attack_cooldowns if is_bot_turn else self.user_attack_cooldowns)[i]
                    button.label = f"{attack['name']} ({_format_cooldown_label(attack, cooldown_turns)})"
                    button.disabled = True
                elif is_reload_action and (not is_bot_turn):
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    button.style = display_style
                    button.label = display_label
                    button.disabled = bool(is_bot_turn)
            else:
                button.label = f"Angriff {i+1}"
                button.disabled = True

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
        # v2.3.5 Fix: In Missionen bleibt self.current_turn immer der Spieler – der Gegnerzug
        # wird über _mission_actor_turn getrackt. Ohne diese Prüfung konnte man während der
        # Bot-Spotlight-Phase (Bot-Karte + Bot-Attacken sichtbar) einen Button klicken und
        # damit fälschlich die EIGENE Attacke auslösen. Jetzt sind Aktionen nur im eigenen Zug erlaubt.
        if str(getattr(self, "_mission_actor_turn", "player")) != "player":
            await interaction.response.send_message(
                "⏳ Der Gegner ist gerade am Zug – warte einen Moment.", ephemeral=True
            )
            return
        await _safe_defer_interaction(interaction)

        effect_events: list[str] = []
        forced_landing_attack = self.resolve_forced_landing_if_due(self.user_id, effect_events)
        is_forced_landing = forced_landing_attack is not None

        regen_heal = self.apply_regen_tick(self.user_id)
        if regen_heal > 0:
            self._append_effect_event(effect_events, f"Regeneration heilt {regen_heal} HP.")

        defender_id = 0
        pre_burn_total, dot_tick_events = _apply_dot_ticks_for_applier(
            self.active_effects,
            target_id=defender_id,
            applier_id=self.user_id,
            damage_callback=(lambda amount: self._apply_non_heal_damage(defender_id, amount)),
        )
        for event_text in dot_tick_events:
            self._append_effect_event(effect_events, event_text)

        # Hole Angriff
        if attack_index >= len(self.attacks):
            await _safe_send_interaction_ephemeral(interaction, "Ungültiger Angriff!")
            return
        standard_idx = _standard_attack_index(self.attacks)

        # COOLDOWN prüfen (Spieler)
        if (not is_forced_landing) and self.is_attack_on_cooldown_user(attack_index):
            await _safe_send_interaction_ephemeral(interaction, "Diese Attacke ist noch auf Cooldown!")
            return
        if is_forced_landing:
            landing_slot = _maybe_int(forced_landing_attack.get("cooldown_attack_index"))
            if landing_slot is not None and 0 <= int(landing_slot) < 4 and int(landing_slot) != int(attack_index):
                await _safe_send_interaction_ephemeral(
                    interaction,
                    f"Diese Runde ist nur {str(forced_landing_attack.get('name') or 'Landungsschlag')} im ursprünglichen Slot verfügbar.",
                )
                return
        if (not is_forced_landing) and self.special_lock_next_turn.get(self.user_id, 0) > 0 and attack_index != standard_idx:
            standard_attack = self.attacks[standard_idx] if 0 <= standard_idx < len(self.attacks) else {"name": "Standardangriff"}
            await _safe_send_interaction_ephemeral(
                interaction,
                f"Diese Runde ist nur der Standardangriff {str(standard_attack.get('name') or 'Standardangriff')} erlaubt.",
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
        action_type = _attack_kind_label(
            attack,
            attacks=self.attacks,
            attack_index=attack_index,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
        )
        player_pattern_type = self._player_action_pattern_type(
            attack,
            attack_index=attack_index,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
        )
        if not is_forced_landing:
            self._prepare_kingpin_information_for_player_action(effect_events, is_reload_action=is_reload_action)
            self._prepare_green_goblin_bomb_for_player_action(effect_events, is_reload_action=is_reload_action)
        player_miss_reason: str | None = None
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
        permanent_boost = _permanent_damage_boost_amount(self.active_effects, self.user_id)
        if permanent_boost > 0:
            dmg_buff += permanent_boost
            self._append_effect_event(effect_events, f"Dauerhafte Schadenssteigerung aktiv: +{permanent_boost} Schaden.")
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

        effective_attack = dict(attack)
        effective_attack["damage"] = damage
        is_damaging_attack = _attack_has_direct_damage(effective_attack)
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
            restricted_bonus_now, restricted_bonus_effect = _consume_restricted_flat_damage_bonus(
                self.active_effects,
                self.user_id,
                attack,
                attack_index=attack_index,
                standard_index=standard_idx,
            )
            if restricted_bonus_now > 0:
                dmg_buff += restricted_bonus_now
                applied_flat_bonus_now += max(0, restricted_bonus_now)
                source = str((restricted_bonus_effect or {}).get("source") or "Verstärkung")
                self._append_effect_event(effect_events, f"{source}: +{restricted_bonus_now} Schaden auf diesen Angriff.")
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
            current_attack_profile = dict(attack)
            current_attack_profile["damage"] = damage
            _min_threshold_damage, max_dmg_threshold = _attack_total_damage_range(
                current_attack_profile,
                max_only_bonus=0,
                flat_bonus=dmg_buff,
            )
            blind_chance = float(self.blind_next_attack.get(self.user_id, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[self.user_id] = 0.0
                blind_miss = random.random() < blind_chance
            if blind_miss:
                player_miss_reason = f"durch Blendung ({int(round(blind_chance * 100))}% Verfehlchance)"
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
                    player_miss_reason = "durch Verwirrung, stattdessen Selbsttreffer"
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
                    if (
                        defender_has_stealth
                        and actual_damage > 0
                        and not guaranteed_hit
                        and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    ):
                        actual_damage = 0
                        is_critical = False
                        hits_enemy = False
                        player_miss_reason = "durch Tarnung"
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
                if (
                    defender_has_stealth
                    and actual_damage > 0
                    and not guaranteed_hit
                    and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                ):
                    actual_damage = 0
                    is_critical = False
                    hits_enemy = False
                    player_miss_reason = "durch Tarnung"
                    self.consume_stealth(0)

            if hits_enemy and actual_damage > 0:
                before_any_override = int(actual_damage)
                actual_damage, any_override_effect = _consume_next_attack_damage_override(
                    self.active_effects,
                    self.user_id,
                    actual_damage,
                )
                if any_override_effect is not None and actual_damage != before_any_override:
                    source = str(any_override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Angriffsschaden {before_any_override} -> {actual_damage}.")
                before_override = int(actual_damage)
                actual_damage, override_effect = _consume_next_standard_damage_override(
                    self.active_effects,
                    self.user_id,
                    attack_index=attack_index,
                    standard_index=standard_idx,
                    current_damage=actual_damage,
                )
                if override_effect is not None and actual_damage != before_override:
                    source = str(override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Standardangriff {before_override} -> {actual_damage} Schaden.")
                before_capped = int(actual_damage)
                actual_damage, capped_bonus, capped_effect = _consume_capped_damage_multiplier(self.active_effects, self.user_id, actual_damage)
                if capped_effect is not None and capped_bonus > 0:
                    source = str(capped_effect.get("source") or "Geheimakte")
                    self._append_effect_event(effect_events, f"{source}: Schaden {before_capped} -> {actual_damage} (+{capped_bonus}, max. +{_effect_amount_label(capped_effect.get('max_bonus', 0))}).")
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
                        _outgoing_reduction_effect_text(
                            int(actual_damage),
                            int(reduced_damage),
                            source=modifier_source or None,
                        ),
                    )
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._apply_non_heal_damage_with_event(
                        effect_events,
                        self.user_id,
                        overflow_self_damage,
                        source=_overflow_recoil_source(modifier_source or None),
                        self_damage=True,
                    )
                if actual_damage <= 0:
                    is_critical = False

                bypass_all_defense = bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                before_incoming_multiplier = int(actual_damage)
                actual_damage, incoming_multiplier_effect = _consume_incoming_damage_multiplier(
                    self.active_effects,
                    0,
                    actual_damage,
                )
                if incoming_multiplier_effect is not None and actual_damage != before_incoming_multiplier:
                    source = str(incoming_multiplier_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Eingehender Schaden {before_incoming_multiplier} -> {actual_damage}.")
                incoming_raw_damage = int(actual_damage)
                absorbed_before = int(self.absorbed_damage.get(0, 0) or 0)
                final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                    0,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(0)),
                    ignore_all_defense=bypass_all_defense,
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
                    readable_source = _readable_effect_source((incoming_modifier or {}).get("source"))
                    player_miss_reason = f"durch {readable_source}" if readable_source else "durch Ausweichen"
                    actual_damage = 0
                    hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    actual_damage = self._consume_kingpin_information(effect_events, actual_damage)
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
                hit_heal, heal_effect = _consume_attack_heal(self.active_effects, self.user_id)
                if hit_heal > 0:
                    healed_now = self.heal_player(self.user_id, hit_heal)
                    if healed_now > 0:
                        self._append_effect_event(effect_events, f"{str((heal_effect or {}).get('source') or 'Trefferheilung')}: Treffer heilt {healed_now} HP.")
                self._apply_on_hit_passives(effect_events, damage_dealt=int(actual_damage or 0))
            if not hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = _resolve_self_damage_value(attack.get("self_damage", 0))
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
            heal_chance = float(attack.get("heal_chance", 1.0) or 1.0)
            if random.random() <= heal_chance:
                heal_amount = _random_int_from_range(heal_data)
                # Req. 20.4 (Agatha „Darkhold-Fluch"): die nächste heilende Spielerfähigkeit
                # heilt 0 HP; der Effekt läuft mit dieser Auflösung ab.
                if heal_amount > 0 and bool(self.mission_data.get("player_heal_negation_pending")):
                    self.mission_data["player_heal_negation_pending"] = False
                    heal_amount = 0
                    self._append_effect_event(effect_events, "Darkhold-Fluch: Heilung wird negiert (0 HP).")
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
        if bool(self.mission_data.get("kingpin_information_pending", False)):
            actual_damage = self._consume_kingpin_information(effect_events, int(actual_damage or 0))
        self._apply_agatha_action_pattern(effect_events, player_pattern_type)
        self._apply_modok_neural_feedback(effect_events, attack, is_reload_action=is_reload_action)
        self._resolve_green_goblin_bomb_after_player_attack(effect_events, damage_dealt=int(actual_damage or 0))
        self._last_player_damage_dealt = int(actual_damage or 0)
        self.mission_data["last_player_damage_dealt"] = int(self._last_player_damage_dealt)
        # Req. 17.7: merken, ob der Spieler in dieser Runde eine Cooldown-Fähigkeit
        # (Nicht-Standardangriff mit cooldown_turns > 0) eingesetzt hat.
        if not is_reload_action:
            used_cd_ability = (
                isinstance(attack, dict)
                and not bool(attack.get("is_standard_attack"))
                and int(attack.get("cooldown_turns", 0) or 0) > 0
            )
            self.mission_data["player_used_cd_last_round"] = bool(used_cd_ability)
        self._mark_maestro_execute_if_needed(effect_events)
        self._sync_maestro_execute_for_current_hp(effect_events)

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
            if _apply_word_runtime_effect(self, effect_events, eff_type=str(eff_type), target_id=target_id, attack_name=attack_name):
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
            elif _is_dot_effect_type(eff_type):
                duration, burn_damage = _append_dot_effect(
                    self.active_effects,
                    target_id=target_id,
                    attacker_id=self.user_id,
                    effect_type=eff_type,
                    duration=effect.get("duration"),
                    damage=effect.get("damage"),
                )
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"{_dot_label(eff_type)} aktiv: {burn_damage} Schaden für {duration} Runden.")
            elif eff_type == 'confusion':
                self.set_confusion(target_id, self.user_id)
                confusion_applied = True
                self._append_effect_event(effect_events, "Verwirrung wurde angewendet.")
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
                self._append_effect_event(effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
            elif eff_type == "damage_boost":
                amount = _effect_amount(effect, "amount", 0)
                uses = int(effect.get("uses", 1) or 1)
                _queue_flat_damage_boost(
                    self,
                    effect_events,
                    target_id=target_id,
                    applier_id=self.user_id,
                    attack_name=str(attack_name),
                    amount=amount,
                    uses=uses,
                    effect=effect,
                )
            elif eff_type == "attack_heal":
                uses = int(effect.get("uses", 1) or 1)
                _append_active_effect(self.active_effects, target_id, "attack_heal", self.user_id, amount=effect.get("amount", 0), uses=uses, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Trefferheilung aktiv: +{_effect_amount_label(effect.get('amount', 0))} HP für {uses} eigene Treffer."))
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Angriff macht +{pct}% Schaden."))
            elif eff_type == "capped_damage_multiplier":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "capped_damage_multiplier",
                    self.user_id,
                    multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                    max_bonus=effect.get("max_bonus", 0),
                    uses=max(1, int(effect.get("uses", 1) or 1)),
                    source=attack_name,
                )
            elif eff_type == "next_standard_damage_override":
                _append_active_effect(
                    self.active_effects,
                    target_id,
                    "next_standard_damage_override",
                    self.user_id,
                    turns=max(1, int(effect.get("turns", 1) or 1)),
                    damage=effect.get("damage", 0),
                    source=attack_name,
                )
                self._append_effect_event(effect_events, _effect_source_text(attack_name, f"Nächster Standardangriff wird auf {_effect_amount_label(effect.get('damage', 0))} Schaden gesetzt."))
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
                amount = _effect_amount(effect, "amount", 0)
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
                amount = _effect_amount(effect, "amount", 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                )
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                reflect_flat = effect.get("flat", 0)
                self.queue_incoming_modifier(
                    target_id,
                    percent=reduce_percent,
                    reflect=reflect_ratio,
                    flat=0,
                    turns=1,
                    source=attack_name,
                )
                if self.incoming_modifiers.get(target_id):
                    self.incoming_modifiers[target_id][-1]["reflect_flat"] = reflect_flat
                reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                flat_text = f" und {_effect_amount_label(reflect_flat)} fixer Rückschaden ausgelöst werden" if _range_pair(reflect_flat)[1] > 0 else ""
                self._append_effect_event(
                    effect_events,
                    _effect_source_text(
                        attack_name,
                        f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert, {reflect_pct}% des verhinderten Schadens werden zurückgeworfen{flat_text}.",
                    ),
                )
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                max_store = effect.get("max_store")
                self.queue_incoming_modifier(
                    target_id,
                    percent=percent,
                    store_ratio=1.0,
                    max_store=(int(max_store) if max_store is not None else None),
                    turns=1,
                    source=attack_name,
                )
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
                    max_damage = cap_setting
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=attack_name)
                    self._append_effect_event(
                        effect_events,
                        _effect_source_text(attack_name, f"Schadenslimit aktiv: Maximal {_effect_amount_label(max_damage)} Schaden beim nächsten Treffer."),
                    )
            elif eff_type == "evade":
                counter = effect.get("counter", 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
            elif eff_type == "special_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                self.special_lock_next_turn[target_id] = max(self.special_lock_next_turn.get(target_id, 0), turns)
                self._append_effect_event(effect_events, f"Spezialfähigkeiten des Gegners sind für {turns} Runde(n) gesperrt.")
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                self._append_effect_event(effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = effect.get("heal", 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": self.user_id})
                self._append_effect_event(effect_events, f"Regeneration aktiviert: Heilt sich in den nächsten {turns} Runden jeweils um {heal} HP.")
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = _random_int_from_range(heal_data_effect)
                healed_effect = self.heal_player(target_id, heal_amount)
                if healed_effect > 0:
                    self._append_effect_event(effect_events, f"Heileffekt: +{healed_effect} HP.")
            elif eff_type == "mix_heal_or_max":
                _apply_mix_heal_or_max_effect(self, target_id, effect, effect_events)
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = effect.get("counter", 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
                self._append_effect_event(effect_events, _effect_source_text(attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    landing_attack=(effect.get("landing_attack") if isinstance(effect.get("landing_attack"), dict) else None),
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )

        _prepend_action_context_events(
            effect_events,
            action_type=action_type,
            actual_damage=int(actual_damage or 0),
            miss_reason=player_miss_reason,
            heal_amount=_extract_heal_amount_from_events(effect_events),
            is_reload_action=is_reload_action,
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
        await self._log_mission_attack_event(
            attacker_user_id=self.user_id,
            defender_user_id=0,
            attacker_name=self.player_card["name"],
            attack_name=attack_name,
            actual_damage=int(actual_damage or 0),
            is_critical=bool(is_critical),
            round_number=int(self.round_counter),
            defender_remaining_hp=int(self.bot_hp),
            attacker_remaining_hp=int(self.player_hp),
            pre_effect_damage=int(pre_burn_total or 0),
            self_hit_damage=int(self_damage if not hits_enemy and 'self_damage' in locals() else 0),
            effect_events=effect_events,
        )
        if self.airborne_pending_landing.get(0):
            self._consume_airborne_evade_marker(0)

        if (not is_forced_landing) and (not is_reload_action) and attack.get("requires_reload"):
            self.set_reload_needed(self.user_id, attack_index, True)

        if self.special_lock_next_turn.get(self.user_id, 0) > 0:
            self.special_lock_next_turn[self.user_id] = max(0, self.special_lock_next_turn.get(self.user_id, 0) - 1)

        # Starte Cooldown (kartenspezifisch oder für starke Attacken) für den nächsten Zug.
        # In Missionen soll die stärkste Attacke im nächsten eigenen Zug gesperrt sein.
        # Darum KEINE sofortige Reduktion hier – die Reduktion passiert nach dem Bot-Zug.
        if not is_forced_landing:
            dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                attack,
                burning_duration_for_dynamic_cooldown,
            )
            custom_cooldown_turns = _resolve_final_damage_cooldown_turns(attack, actual_damage)
            starts_after_landing = _starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                current_cd = self.user_attack_cooldowns.get(attack_index, 0)
                self.user_attack_cooldowns[attack_index] = max(current_cd, dynamic_cooldown_turns)
                bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                self._append_effect_event(
                    effect_events,
                    f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                )
            elif (not starts_after_landing) and custom_cooldown_turns > 0:
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

        # Bot-Zug: kurz große Gegnerkarte, dann Ablauf
        if message is not None:
            try:
                self._mission_actor_turn = "bot"
                self.update_attack_buttons_mission()
                await message.edit(
                    embed=self.create_bot_spotlight_embed(),
                    view=self,
                )
            except Exception:
                logging.exception("Failed to update mission battle before bot turn")
            await asyncio.sleep(random.uniform(2.0, 5.0))

        defender_id = self.user_id
        pre_burn_total_player, pre_bot_turn_events = _apply_dot_ticks_for_applier(
            self.active_effects,
            target_id=defender_id,
            applier_id=0,
            damage_callback=(lambda amount: self._apply_non_heal_damage(defender_id, amount)),
        )

        self.apply_regen_tick(0)

        if self.stunned_next_turn.get(0, False):
            self.stunned_next_turn[0] = False
            if self.airborne_pending_landing.get(self.user_id):
                self._consume_airborne_evade_marker(self.user_id)
            self.reduce_cooldowns_user()
            self._mission_actor_turn = "player"
            self.update_attack_buttons_mission()
            embed = self.create_current_embed(
                description="🛑 Bot war betäubt und setzt den Zug aus! Du bist wieder an der Reihe!",
            )
            if message is not None:
                await interaction.followup.edit_message(message.id, embed=embed, view=self)
            else:
                await interaction.followup.send(embed=embed, view=self, ephemeral=True)
            if message is not None:
                await self.persist_session(interaction.channel, status="active", battle_message=message)
            return

        # Bot-Angriff
        bot_attacks = self.bot_card.get("attacks", [{"name": "Punch", "damage": 20}])
        standard_idx = _standard_attack_index(bot_attacks)
        bot_effect_events: list[str] = []
        for event_text in pre_bot_turn_events:
            self._append_effect_event(bot_effect_events, event_text)
        forced_bot_landing_attack = self.resolve_forced_landing_if_due(0, bot_effect_events)
        is_forced_bot_landing = forced_bot_landing_attack is not None
        forced_maestro_attack = self._forced_maestro_execute_attack(bot_effect_events)
        # Wähle stärkste verfügbare Bot-Attacke (unter Berücksichtigung von Cooldown)
        available_attacks = []
        attack_damages = []
        attack_scores = []
        bot_hp_gate = self._hp_for(0)
        bot_max_hp_gate = self._max_hp_for(0)
        for i, atk in enumerate(bot_attacks[:4]):
            if self.special_lock_next_turn.get(0, 0) > 0 and i != standard_idx:
                continue
            if not _attack_allowed_at_self_hp(atk, bot_hp_gate, bot_max_hp_gate):
                continue
            if not self.is_attack_on_cooldown_bot(i):
                if atk.get("requires_reload") and self.is_reload_needed(0, i):
                    max_dmg = 0
                else:
                    _min_dmg, max_dmg = _attack_total_damage_range(atk, max_only_bonus=0, flat_bonus=0) if isinstance(atk, dict) else (0, 0)
                score = max_dmg
                if _is_operation_broken_timeline(self.mission_data):
                    score = max(score, int(atk.get("bot_priority", 0) or 0))
                    if _attack_has_heal_component(atk) and self.bot_hp >= self.bot_max_hp:
                        score = min(score, max_dmg)
                available_attacks.append(i)
                attack_damages.append(max_dmg)
                attack_scores.append(score)

        if available_attacks or is_forced_bot_landing or forced_maestro_attack is not None:
            if forced_maestro_attack is not None:
                best_index = -1
                attack = forced_maestro_attack
                damage = attack["damage"]
            elif is_forced_bot_landing:
                best_index = -1
                attack = forced_bot_landing_attack
                damage = attack["damage"]
            else:
                # Wähle die mit max Damage
                preferred_idx = _preferred_attack_index_for_restricted_bonus(
                    self.active_effects,
                    0,
                    [atk for atk in bot_attacks if isinstance(atk, dict)],
                    available_attacks,
                    standard_index=standard_idx,
                )
                best_index = preferred_idx if preferred_idx is not None else available_attacks[attack_scores.index(max(attack_scores))]
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
            # Req. 19.6/19.7 (Kingpin „Zermalmender Griff"): reduzierter Schaden, solange das
            # Ziel noch über einem HP-Schwellwert liegt.
            reduced_hp_cfg = attack.get("reduced_damage_if_player_hp_at_least")
            if isinstance(reduced_hp_cfg, dict) and defender_hp >= int(reduced_hp_cfg.get("hp", 0) or 0):
                damage = _coerce_damage_input(reduced_hp_cfg.get("damage"), default=damage)
            permanent_boost_bot = _permanent_damage_boost_amount(self.active_effects, 0)
            if permanent_boost_bot > 0:
                dmg_buff_bot += permanent_boost_bot
                self._append_effect_event(bot_effect_events, f"Dauerhafte Schadenssteigerung aktiv: +{permanent_boost_bot} Schaden.")
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
            effective_attack = dict(attack)
            effective_attack["damage"] = damage
            is_damaging_attack = _attack_has_direct_damage(effective_attack)
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
                restricted_bonus_now, restricted_bonus_effect = _consume_restricted_flat_damage_bonus(
                    self.active_effects,
                    0,
                    attack,
                    attack_index=best_index,
                    standard_index=standard_idx,
                )
                if restricted_bonus_now > 0:
                    dmg_buff_bot += restricted_bonus_now
                    applied_flat_bonus_now += max(0, restricted_bonus_now)
                    source = str((restricted_bonus_effect or {}).get("source") or "Verstärkung")
                    self._append_effect_event(bot_effect_events, f"{source}: +{restricted_bonus_now} Schaden auf diesen Angriff.")
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
            action_type = _attack_kind_label(
                attack,
                attacks=bot_attacks,
                attack_index=best_index,
                is_reload_action=is_bot_reload_action,
                is_forced_landing=is_forced_bot_landing,
            )
            miss_reason: str | None = None
            # Bot kann ebenfalls verwirrt sein: 77% Selbstschaden, 23% normaler Treffer
            bot_hits_enemy = True
            self_damage = 0
            if is_bot_reload_action:
                actual_damage, is_critical = 0, False
                bot_hits_enemy = False
                self.set_reload_needed(0, best_index, False)
            else:
                min_damage = 0
                max_damage = 0
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
                current_attack_profile = dict(attack)
                current_attack_profile["damage"] = damage
                _min_threshold_damage, max_dmg_threshold = _attack_total_damage_range(
                    current_attack_profile,
                    max_only_bonus=0,
                    flat_bonus=dmg_buff_bot,
                )
                if blind_miss:
                    miss_reason = f"durch Blendung ({int(round(blind_chance * 100))}% Verfehlchance)"
                    actual_damage, is_critical = 0, False
                    bot_hits_enemy = False
                    self.confused_next_turn[0] = False
                elif hasattr(self, 'confused_next_turn') and self.confused_next_turn.get(0, False):
                    if random.random() < 0.77:
                        self_damage = random.randint(15, 20) if max_dmg_threshold <= 100 else random.randint(40, 60)
                        self._apply_non_heal_damage_with_event(
                            bot_effect_events,
                            0,
                            self_damage,
                            source="Verwirrung",
                            self_damage=True,
                        )
                        miss_reason = "durch Verwirrung, stattdessen Selbsttreffer"
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
                        if (
                        defender_has_stealth
                        and actual_damage > 0
                        and not guaranteed_hit
                        and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    ):
                            actual_damage = 0
                            is_critical = False
                            bot_hits_enemy = False
                            miss_reason = "durch Tarnung"
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
                    if (
                        defender_has_stealth
                        and actual_damage > 0
                        and not guaranteed_hit
                        and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    ):
                        actual_damage = 0
                        is_critical = False
                        bot_hits_enemy = False
                        miss_reason = "durch Tarnung"
                        self.consume_stealth(self.user_id)

            if bot_hits_enemy and actual_damage > 0:
                before_override = int(actual_damage)
                actual_damage, override_effect = _consume_next_standard_damage_override(
                    self.active_effects,
                    0,
                    attack_index=best_index,
                    standard_index=standard_idx,
                    current_damage=actual_damage,
                )
                if override_effect is not None and actual_damage != before_override:
                    source = str(override_effect.get("source") or "Effekt")
                    self._append_effect_event(bot_effect_events, f"{source}: Standardangriff {before_override} -> {actual_damage} Schaden.")
                before_capped = int(actual_damage)
                actual_damage, capped_bonus, capped_effect = _consume_capped_damage_multiplier(self.active_effects, 0, actual_damage)
                if capped_effect is not None and capped_bonus > 0:
                    source = str(capped_effect.get("source") or "Geheimakte")
                    self._append_effect_event(bot_effect_events, f"{source}: Schaden {before_capped} -> {actual_damage} (+{capped_bonus}, max. +{_effect_amount_label(capped_effect.get('max_bonus', 0))}).")
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if True:
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
                            _outgoing_reduction_effect_text(
                                int(actual_damage),
                                int(reduced_damage),
                                source=modifier_source or None,
                            ),
                        )
                        actual_damage = reduced_damage
                    if overflow_self_damage > 0:
                        modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                        self._apply_non_heal_damage_with_event(
                            bot_effect_events,
                            0,
                            overflow_self_damage,
                            source=_overflow_recoil_source(modifier_source or None),
                            self_damage=True,
                        )
                    if actual_damage <= 0:
                        is_critical = False

                    bypass_all_defense = bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable"))
                    before_incoming_multiplier = int(actual_damage)
                    actual_damage, incoming_multiplier_effect = _consume_incoming_damage_multiplier(
                        self.active_effects,
                        self.user_id,
                        actual_damage,
                    )
                    if incoming_multiplier_effect is not None and actual_damage != before_incoming_multiplier:
                        source = str(incoming_multiplier_effect.get("source") or "Effekt")
                        self._append_effect_event(bot_effect_events, f"{source}: Eingehender Schaden {before_incoming_multiplier} -> {actual_damage}.")
                    incoming_raw_damage = int(actual_damage)
                    absorbed_before = int(self.absorbed_damage.get(self.user_id, 0) or 0)
                    final_damage, reflected_damage, dodged, counter_damage, incoming_modifier = self._resolve_incoming_modifiers_with_details(
                        self.user_id,
                        actual_damage,
                        ignore_evade=(guaranteed_hit and not self.has_airborne(self.user_id)),
                        ignore_all_defense=bypass_all_defense,
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
                        readable_source = _readable_effect_source((incoming_modifier or {}).get("source"))
                        miss_reason = f"durch {readable_source}" if readable_source else "durch Ausweichen"
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
                    hit_heal, heal_effect = _consume_attack_heal(self.active_effects, 0)
                    if hit_heal > 0:
                        healed_now = self.heal_player(0, hit_heal)
                        if healed_now > 0:
                            self._append_effect_event(bot_effect_events, f"{str((heal_effect or {}).get('source') or 'Trefferheilung')}: Treffer heilt {healed_now} HP.")
                if not bot_hits_enemy or int(actual_damage or 0) <= 0:
                    is_critical = False

            self_damage_value = _resolve_self_damage_value(attack.get("self_damage", 0))
            if self_damage_value > 0:
                self._apply_non_heal_damage_with_event(
                    bot_effect_events,
                    0,
                    self_damage_value,
                    source=f"{bot_attack_name} / Rückstoß",
                    self_damage=True,
                )

            heal_data = attack.get("heal")
            if (
                str(self.bot_card.get("name") or "").strip().lower() == "kingpin"
                and str(bot_attack_name or "").strip() == "Bestechungs-Versuch"
            ):
                # v2.3.5 (2026-05-31, Balance-Notiz): deutlich abgeschwächt – 20 HP wenn der
                # Spieler in der Vorrunde 0 Schaden gemacht hat, sonst 15 HP (vorher 30/35).
                heal_data = [20, 20] if int(self._last_player_damage_dealt or 0) <= 0 else [15, 15]
            # Req. 17.6/17.7: MODOK „Berechnete Heilung" heilt 30 statt 15, wenn der Spieler
            # in der vorherigen Runde eine Cooldown-Fähigkeit eingesetzt hat.
            boosted_heal = attack.get("heal_if_player_used_cd_last_round")
            if boosted_heal is not None and bool(self.mission_data.get("player_used_cd_last_round")):
                heal_data = [int(boosted_heal), int(boosted_heal)]
            if heal_data is not None:
                heal_chance = float(attack.get("heal_chance", 1.0) or 1.0)
                if random.random() <= heal_chance:
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
            self._mark_maestro_execute_if_needed(bot_effect_events)
            self._sync_maestro_execute_for_current_hp(bot_effect_events)

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
                if _apply_word_runtime_effect(self, bot_effect_events, eff_type=str(eff_type), target_id=target_id, attack_name=bot_attack_name, effect=effect):
                    continue
                if eff_type == "stealth":
                    self.grant_stealth(target_id)
                    self._append_effect_event(bot_effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird geblockt.")
                elif _is_dot_effect_type(eff_type):
                    duration, burn_damage = _append_dot_effect(
                        self.active_effects,
                        target_id=target_id,
                        attacker_id=0,
                        effect_type=eff_type,
                        duration=effect.get("duration"),
                        damage=effect.get("damage"),
                    )
                    if attack.get("cooldown_from_burning_plus") is not None:
                        prev_duration = bot_burning_duration_for_dynamic_cooldown or 0
                        bot_burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                    self._append_effect_event(bot_effect_events, f"{_dot_label(eff_type)} aktiv: {burn_damage} Schaden für {duration} Runden.")
                elif eff_type == 'confusion':
                    self.set_confusion(target_id, 0)
                    self._append_effect_event(bot_effect_events, "Verwirrung wurde angewendet.")
                elif eff_type == "stun":
                    self.stunned_next_turn[target_id] = True
                    self._append_effect_event(bot_effect_events, "Betäubung: Der Gegner setzt den nächsten Zug aus.")
                elif eff_type == "damage_boost":
                    amount = _effect_amount(effect, "amount", 0)
                    uses = int(effect.get("uses", 1) or 1)
                    _queue_flat_damage_boost(
                        self,
                        bot_effect_events,
                        target_id=target_id,
                        applier_id=0,
                        attack_name=str(bot_attack_name),
                        amount=amount,
                        uses=uses,
                        effect=effect,
                    )
                elif eff_type == "attack_heal":
                    uses = int(effect.get("uses", 1) or 1)
                    _append_active_effect(self.active_effects, target_id, "attack_heal", 0, amount=effect.get("amount", 0), uses=uses, source=bot_attack_name)
                    self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, f"Trefferheilung aktiv: +{_effect_amount_label(effect.get('amount', 0))} HP für {uses} eigene Treffer."))
                elif eff_type == "damage_multiplier":
                    mult = float(effect.get("multiplier", 1.0) or 1.0)
                    uses = int(effect.get("uses", 1) or 1)
                    self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                    self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                elif eff_type == "capped_damage_multiplier":
                    _append_active_effect(
                        self.active_effects,
                        target_id,
                        "capped_damage_multiplier",
                        0,
                        multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                        max_bonus=effect.get("max_bonus", 0),
                        uses=max(1, int(effect.get("uses", 1) or 1)),
                        source=bot_attack_name,
                    )
                elif eff_type == "next_standard_damage_override":
                    _append_active_effect(
                        self.active_effects,
                        target_id,
                        "next_standard_damage_override",
                        0,
                        turns=max(1, int(effect.get("turns", 1) or 1)),
                        damage=effect.get("damage", 0),
                        source=bot_attack_name,
                    )
                    self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, f"Nächster Standardangriff wird auf {_effect_amount_label(effect.get('damage', 0))} Schaden gesetzt."))
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
                    amount = effect.get("amount", 0)
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
                    amount = effect.get("amount", 0)
                    turns = int(effect.get("turns", 1) or 1)
                    self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=bot_attack_name)
                    self._append_effect_event(
                        bot_effect_events,
                        _effect_source_text(bot_attack_name, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß)."),
                    )
                elif eff_type == "reflect":
                    reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                    reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                    reflect_flat = effect.get("flat", 0)
                    self.queue_incoming_modifier(
                        target_id,
                        percent=reduce_percent,
                        reflect=reflect_ratio,
                        flat=0,
                        turns=1,
                        source=bot_attack_name,
                    )
                    if self.incoming_modifiers.get(target_id):
                        self.incoming_modifiers[target_id][-1]["reflect_flat"] = reflect_flat
                    reduce_pct = int(round(max(0.0, reduce_percent) * 100))
                    reflect_pct = int(round(max(0.0, reflect_ratio) * 100))
                    flat_text = f" und {_effect_amount_label(reflect_flat)} fixer Rückschaden ausgelöst werden" if _range_pair(reflect_flat)[1] > 0 else ""
                    self._append_effect_event(
                        bot_effect_events,
                        _effect_source_text(
                            bot_attack_name,
                            f"Reflexion aktiv: Nächster eingehender Angriff wird um {reduce_pct}% reduziert, {reflect_pct}% des verhinderten Schadens werden zurückgeworfen{flat_text}.",
                        ),
                    )
                elif eff_type == "absorb_store":
                    percent = float(effect.get("percent", 0.0) or 0.0)
                    max_store = effect.get("max_store")
                    self.queue_incoming_modifier(
                        target_id,
                        percent=percent,
                        store_ratio=1.0,
                        max_store=(int(max_store) if max_store is not None else None),
                        turns=1,
                        source=bot_attack_name,
                    )
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
                        max_damage = cap_setting
                        self.queue_incoming_modifier(target_id, cap=max_damage, turns=1, source=bot_attack_name)
                        self._append_effect_event(
                            bot_effect_events,
                            _effect_source_text(bot_attack_name, f"Schadenslimit aktiv: Maximal {_effect_amount_label(max_damage)} Schaden beim nächsten Treffer."),
                        )
                elif eff_type == "evade":
                    counter = effect.get("counter", 0)
                    self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=bot_attack_name)
                    self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."))
                elif eff_type == "special_lock":
                    turns = max(1, int(effect.get("turns", 1) or 1))
                    self.special_lock_next_turn[target_id] = max(self.special_lock_next_turn.get(target_id, 0), turns)
                    self._append_effect_event(bot_effect_events, f"Spezialfähigkeiten des Gegners sind für {turns} Runde(n) gesperrt.")
                elif eff_type == "next_player_heal_negation":
                    # Req. 20.4 (Agatha „Darkhold-Fluch"): die nächste heilende Spielerfähigkeit
                    # heilt 0 HP. Konsumiert wird der Marker beim nächsten Spieler-Heal.
                    self.mission_data["player_heal_negation_pending"] = True
                    self._append_effect_event(bot_effect_events, "Darkhold-Fluch: Die nächste heilende Fähigkeit des Spielers heilt 0 HP.")
                elif eff_type == "blind":
                    miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                    self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
                    self._append_effect_event(bot_effect_events, f"Blendung aktiv: {int(round(miss_chance * 100))}% Verfehlchance beim nächsten Angriff.")
                elif eff_type == "regen":
                    turns = int(effect.get("turns", 1) or 1)
                    heal = effect.get("heal", 0)
                    self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": 0})
                    self._append_effect_event(bot_effect_events, f"Regeneration aktiviert: +{heal} HP für {turns} Runde(n).")
                elif eff_type == "heal":
                    heal_data_effect = effect.get("amount", 0)
                    heal_amount = _random_int_from_range(heal_data_effect)
                    healed_effect = self.heal_player(target_id, heal_amount)
                    if healed_effect > 0:
                        self._append_effect_event(bot_effect_events, f"Heileffekt: +{healed_effect} HP.")
                elif eff_type == "mix_heal_or_max":
                    _apply_mix_heal_or_max_effect(self, target_id, effect, bot_effect_events)
                elif eff_type == "delayed_defense_after_next_attack":
                    defense_mode = str(effect.get("defense", "")).strip().lower()
                    counter = effect.get("counter", 0)
                    self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=bot_attack_name)
                    self._append_effect_event(bot_effect_events, _effect_source_text(bot_attack_name, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv."))
                elif eff_type == "airborne_two_phase":
                    self.start_airborne_two_phase(
                        target_id,
                        effect.get("landing_damage", [20, 40]),
                        bot_effect_events,
                        landing_attack=(effect.get("landing_attack") if isinstance(effect.get("landing_attack"), dict) else None),
                        source_attack_index=best_index if not is_forced_bot_landing else None,
                        cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                    )
            # Kein separater Log – Effekte werden inline in der Angriffszeile angezeigt

            _prepend_action_context_events(
                bot_effect_events,
                action_type=action_type,
                actual_damage=int(actual_damage or 0),
                miss_reason=miss_reason,
                heal_amount=_extract_heal_amount_from_events(bot_effect_events),
                is_reload_action=is_bot_reload_action,
            )

            # v2.3.5 (Req. 15): Spezialfähigkeit des Gegners/Bosses als eigene, fett markierte
            # Zeile ganz oben hervorheben, wenn eine Nicht-Standard-Attacke mit Effekt/Heilung
            # ausgelöst wurde – damit klar ist, WAS gerade passiert ist.
            if (
                isinstance(attack, dict)
                and not is_bot_reload_action
                and not bool(attack.get("is_standard_attack"))
                and (bool(attack.get("effects")) or attack.get("heal") is not None)
            ):
                special_line = render_boss_special_activation(
                    str(self.bot_card.get("name") or ""),
                    str(bot_attack_name or ""),
                    str(attack.get("info") or "").strip(),
                )
                if special_line:
                    bot_effect_events.insert(0, special_line)

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
            await self._log_mission_attack_event(
                attacker_user_id=0,
                defender_user_id=self.user_id,
                attacker_name=self.bot_card["name"],
                attack_name=bot_attack_name,
                actual_damage=int(actual_damage or 0),
                is_critical=bool(is_critical),
                round_number=int(self.round_counter),
                defender_remaining_hp=int(self.player_hp),
                attacker_remaining_hp=int(self.bot_hp),
                pre_effect_damage=int(pre_burn_total_player or 0),
                self_hit_damage=int(self_damage if not bot_hits_enemy and 'self_damage' in locals() else 0),
                effect_events=bot_effect_events,
            )
            if self.airborne_pending_landing.get(self.user_id):
                self._consume_airborne_evade_marker(self.user_id)

            if (not is_forced_bot_landing) and (not is_bot_reload_action) and attack.get("requires_reload"):
                self.set_reload_needed(0, best_index, True)

            if self.special_lock_next_turn.get(0, 0) > 0:
                self.special_lock_next_turn[0] = max(0, self.special_lock_next_turn.get(0, 0) - 1)

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
                custom_cooldown_turns = _resolve_final_damage_cooldown_turns(attack, actual_damage)
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
                elif best_index >= 0 and (not starts_after_landing) and custom_cooldown_turns > 0:
                    current_cd = self.bot_attack_cooldowns.get(best_index, 0)
                    self.bot_attack_cooldowns[best_index] = max(current_cd, custom_cooldown_turns)
                    self.reduce_cooldowns_bot()
                elif best_index >= 0 and self.mission_is_strong_attack(damage, dmg_buff_bot):
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

            # Falls der Bot sich selbst/über Effekte besiegt hat, Welle sofort beenden.
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
                    detail_text=f"❌ **Welle {self.wave_num} verloren!** Der Bot hat dich besiegt.",
                )
                return

            # Update UI für nächsten Spieler-Zug
            self._mission_actor_turn = "player"
            self.update_attack_buttons_mission()
            embed = self.create_current_embed(
                description=f"Bot hat **{bot_attack_name}** verwendet! Dein HP: {self.player_hp}\nDu bist wieder an der Reihe!",
            )

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
            self._mission_actor_turn = "player"
            self.update_attack_buttons_mission()

            # Safety: falls Bot/Spieler schon 0 HP hat, Welle beenden statt UI weiterlaufen zu lassen.
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
                    detail_text=f"❌ **Welle {self.wave_num} verloren!** Der Bot hat dich besiegt.",
                )
                return

            embed = self.create_current_embed(
                description="🤖 Bot hat keine Attacken verfügbar! Du bist wieder an der Reihe!",
            )

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


def _rate_limit_delay_from_error(exc: Exception, attempt: int) -> float:
    retry_after_raw = getattr(exc, "retry_after", None)
    retry_after = _maybe_float(retry_after_raw)
    if retry_after is not None and retry_after > 0:
        return max(0.5, min(10.0, float(retry_after)))
    return max(0.5, min(10.0, 1.2 * float(attempt + 1)))

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
    for attempt in range(3):
        try:
            sent_message = await sendable_channel.send(content=content, embed=embed, view=view)
            await _maybe_register_durable_message(sent_message, view)
            return sent_message
        except discord.NotFound:
            logging.warning("Channel %s no longer exists for send", channel_id)
            return None
        except discord.Forbidden:
            logging.warning("Missing send permissions in channel %s", channel_id)
            return None
        except discord.HTTPException as exc:
            if int(getattr(exc, "status", 0) or 0) == 429 and attempt < 2:
                await asyncio.sleep(_rate_limit_delay_from_error(exc, attempt))
                continue
            logging.exception("Failed to send message to channel %s", channel_id)
            return None
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
            content=(
                "\u274c Mir fehlen Rechte in diesem Kanal/Thread "
                "(View/Send/Thread-Rechte). Bitte gib mir Zugriff."
            ),
        )
    except Exception:
        try:
            await _send_ephemeral(
                interaction,
                content="\u274c Nachricht konnte in diesem Kanal/Thread gerade nicht gesendet werden.",
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


def _is_unknown_channel_error(error: Exception) -> bool:
    return isinstance(error, discord.NotFound) and int(getattr(error, "code", 0) or 0) == 10003


async def _cleanup_unavailable_durable_view(view: DurableView) -> None:
    await view.clear_durable_registration()
    stale_thread_id = None
    for candidate in (getattr(view, "channel", None), getattr(view, "thread", None)):
        if isinstance(candidate, discord.Thread):
            stale_thread_id = candidate.id
            break
    if stale_thread_id:
        try:
            await update_managed_thread_status(stale_thread_id, "deleted")
        except Exception:
            logging.exception("Failed to mark stale thread %s as deleted", stale_thread_id)
    view.stop()


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


async def _resolve_thread_channel_or_fallback(thread_id: int | None, fallback_channel: object) -> object:
    if thread_id:
        thread_channel = bot.get_channel(int(thread_id))
        if thread_channel is None:
            thread_channel = await _fetch_channel_safe(int(thread_id))
        if _coerce_sendable_channel(thread_channel) is not None:
            return thread_channel
    return fallback_channel


async def _handle_durable_view_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    view: DurableView,
    view_label: str,
    battle_log_text: str,
) -> None:
    channel = interaction.channel
    missing_channel = _is_unknown_channel_error(error)
    if missing_channel:
        await _cleanup_unavailable_durable_view(view)
    guild_name = interaction.guild.name if interaction.guild else "DM"
    channel_text = _channel_mention_or_fallback(channel)
    user_text = getattr(interaction.user, "mention", None) or f"<@{interaction.user.id}>"
    await _send_basti_log_dm(
        battle_log_text,
        context_lines=[
            f"Fehler in View: {view_label}",
            f"Guild: {guild_name}",
            f"Kanal/Thread: {channel_text}",
            f"User: {safe_display_name(interaction.user, fallback='Unbekannt')} ({interaction.user.id})",
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
    if missing_channel:
        return
    try:
        if channel is not None:
            # Kurze, verständliche Fehlererklärung direkt in den Thread schreiben
            # (technische Details gehen zusätzlich per DM an Basti).
            error_summary = f"{type(error).__name__}: {error}"
            if len(error_summary) > 400:
                error_summary = error_summary[:397] + "..."
            await _send_channel_message(
                channel,
                content=(
                    f"{user_text} ⚠️ **Der Kampf wurde durch einen Fehler unterbrochen.**\n"
                    f"Was passiert ist (technisch):\n```\n{error_summary}\n```\n"
                    "Basti wurde mit den vollständigen Log-Details informiert. "
                    "Wenn du willst, nutze das Formular unten."
                ),
                view=BugReportLinkView(),
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
            selected_base_name=str(payload.get("selected_base_name") or ""),
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
            selected_base_name=str(payload.get("selected_base_name") or ""),
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
            selected_base_name=str(payload.get("selected_base_name") or ""),
        )
    if view_kind == VIEW_KIND_MISSION_ENCOUNTER_PREVIEW:
        return MissionEncounterPreviewView(
            int(payload.get("user_id", 0) or 0),
            _dict_str_any(payload.get("mission_state")),
            str(payload.get("mode") or "lackeys"),
        )
    if view_kind == VIEW_KIND_INVITE_CONFIRM:
        pending_id = int(payload.get("pending_id", 0) or 0)
        row = await load_invite_pending(pending_id)
        if not row or str(row.get("status") or "") != "pending":
            return None
        return InviteConfirmationView(
            pending_id,
            need_admin_gate=bool(int(row.get("need_admin") or 0)),
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
            request_id = int(row["id"])
            guild_id = int(row["guild_id"]) if row["guild_id"] else 0
            guild = bot.get_guild(guild_id) if guild_id else None
            if guild is None:
                continue
            channel = None
            thread_id = row["thread_id"]
            if thread_id:
                channel = guild.get_channel(int(thread_id)) or await _fetch_channel_safe(int(thread_id))
            elif row["message_channel_id"]:
                channel = guild.get_channel(int(row["message_channel_id"])) or await _fetch_channel_safe(int(row["message_channel_id"]))
            elif row["origin_channel_id"]:
                channel = guild.get_channel(int(row["origin_channel_id"])) or await _fetch_channel_safe(int(row["origin_channel_id"]))
            if channel is None:
                await claim_fight_request(request_id, "expired")
                continue
            if not await is_channel_allowed_ids(guild.id, getattr(channel, "id", None), getattr(channel, "parent_id", None)):
                await claim_fight_request(request_id, "expired")
                continue
            sendable_channel = _coerce_sendable_channel(channel)
            if sendable_channel is None:
                await claim_fight_request(request_id, "expired")
                continue
            view = ChallengeResponseView(
                int(row["challenger_id"]),
                int(row["challenged_id"]),
                row["challenger_card"],
                request_id=request_id,
                origin_channel_id=int(row["origin_channel_id"]) if row["origin_channel_id"] else None,
                thread_id=int(row["thread_id"]) if row["thread_id"] else None,
                thread_created=bool(row["thread_created"]),
            )
            existing_message = await _fetch_message_safe(channel, int(row["message_id"])) if row["message_id"] else None
            if existing_message is not None:
                await _maybe_register_durable_message(existing_message, view)
                bot.add_view(view, message_id=existing_message.id)
                continue
            await claim_fight_request(request_id, "expired")
        except Exception:
            logging.exception("Failed to resend fight request")

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
            embed = _build_mission_embed(
                mission_data,
                user_already_owns_reward=await _user_already_owns_card(
                    int(row["user_id"]),
                    _dict_str_any(mission_data).get("reward_card"),
                ),
            )
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
    ("feature_flags", "Feature Flags", "Beta/Alpha-Schalter"),
]

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
        return user_id, safe_display_name(member, fallback=f"<@{user_id}>")
    try:
        user = await interaction.client.fetch_user(user_id)
        return user_id, safe_display_name(user, fallback=f"<@{user_id}>")
    except discord.NotFound:
        return user_id, f"<@{user_id}>"
    except discord.HTTPException:
        logging.exception("Failed to fetch user %s for admin selector", user_id)
        return user_id, f"<@{user_id}>"

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

class NumberInputModal(RestrictedModal):
    def __init__(self, requester_id: int, parent_view: "NumberSelectView | DustQuickAmountView"):
        super().__init__(title="Eigene Menge eingeben")
        self.requester_id = requester_id
        self.parent_view = parent_view
        self.amount = ui.TextInput(
            label="Menge",
            placeholder="Gib eine positive Zahl ein (1-1.000.000)",
            required=True,
            max_length=7,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        raw_value = str(self.amount.value or "").strip()
        if not raw_value.isdigit():
            await interaction.response.send_message(
                "❌ Bitte einen Betrag zwischen 1 und 1.000.000 eingeben.",
                ephemeral=True,
            )
            return
        parsed_amount = int(raw_value)
        if parsed_amount <= 0 or parsed_amount > 1_000_000:
            await interaction.response.send_message(
                "❌ Bitte einen Betrag zwischen 1 und 1.000.000 eingeben.",
                ephemeral=True,
            )
            return
        # Memo Change 4: only update value if input is valid AND non-zero.
        # If the parent view already has an active quick-pick value, a 0/invalid
        # input must NOT overwrite it — the early returns above ensure that.
        self.parent_view.value = parsed_amount
        self.parent_view.stop()
        await interaction.response.send_message(
            f"✅ Eigene Menge gewählt: **{parsed_amount}**", ephemeral=True
        )


class NumberSelectView(RestrictedView):
    def __init__(self, requester_id: int, options: list[int], placeholder: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.value = None
        select_options = [SelectOption(label="Eigener Wert...", value="__custom__", description="Gib selbst eine Menge ein.")]
        select_options.extend(SelectOption(label=str(n), value=str(n)) for n in options[:24])
        self.select = ui.Select(placeholder=placeholder, min_values=1, max_values=1, options=select_options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        selected = str(self.select.values[0] or "").strip()
        if selected == "__custom__":
            await interaction.response.send_modal(NumberInputModal(self.requester_id, self))
            return
        self.value = int(selected)
        self.stop()
        await interaction.response.defer()


class DustQuickAmountView(RestrictedView):
    """Multi-Modus Schnellauswahl für Infinitydust-Beträge.

    Sechs Schnellauswahl-Buttons {5,10,15,20,25,30} (auf zwei Zeilen verteilt,
    da Discord max. 5 Buttons pro Reihe erlaubt) plus ein primärer Button
    „Eigener Betrag…", der das bestehende `NumberInputModal` öffnet.
    Vergleichbar mit der Multi-Karten-Auswahl in `/karte-geben`.
    """

    def __init__(self, requester_id: int, *, amounts: list[int] | None = None):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.value: int | None = None
        quick_amounts = list(amounts) if amounts is not None else list(DUST_MENU_AMOUNTS)
        # Discord erlaubt max. 5 Buttons pro Reihe → wir brechen die sechs
        # Quick-Pick-Buttons in 3+3 auf, Custom-Amount-Button kommt eine
        # Zeile darunter.
        for index, amount in enumerate(quick_amounts[:6]):
            row = 0 if index < 3 else 1
            self.add_item(_DustQuickAmountButton(amount, row=row))
        self.add_item(_DustCustomAmountButton(row=2))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


class _DustQuickAmountButton(ui.Button):
    def __init__(self, amount: int, *, row: int = 0):
        super().__init__(
            label=str(int(amount)),
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.amount = int(amount)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: DustQuickAmountView = self.view  # type: ignore[assignment]
        view.value = self.amount
        view.stop()
        await interaction.response.defer()


class _DustCustomAmountButton(ui.Button):
    def __init__(self, *, row: int = 1):
        super().__init__(
            label="Eigener Betrag…",
            style=discord.ButtonStyle.primary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: DustQuickAmountView = self.view  # type: ignore[assignment]
        await interaction.response.send_modal(NumberInputModal(view.requester_id, view))


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
    visibility_key: str | None = None,
) -> bool:
    action_title = "Infinitydust entfernt" if remove else "Infinitydust vergeben"
    actor_mention = getattr(interaction.user, "mention", None) or f"<@{interaction.user.id}>"

    # Bucket-Klassifikation analog zu /karte-geben:
    # - applied: vollständig angewendet (== requested)
    # - partial: teilweise angewendet (0 < applied < requested) — nur bei remove möglich
    # - failed:  gar nicht angewendet (applied == 0 trotz requested > 0) — nur bei remove möglich
    requested_amount = int(amount)
    applied_count = 0
    partial_count = 0
    failed_count = 0
    for _, applied_amount in results:
        applied_int = int(applied_amount)
        if applied_int >= requested_amount:
            applied_count += 1
        elif applied_int <= 0:
            failed_count += 1
        else:
            partial_count += 1

    # Color-Logik:
    # - Give-Pfad: immer grün (per Definition kein Teilfehler)
    # - Remove-Pfad: rot, wenn ≥ 1 User komplett fehlgeschlagen; orange bei
    #   Teilfehlern; sonst grün.
    if remove:
        if failed_count > 0:
            embed_color = 0xE74C3C
        elif partial_count > 0:
            embed_color = 0xE67E22
        else:
            embed_color = 0x2ECC71
    else:
        embed_color = 0x2ECC71

    lines = [
        f"{actor_mention} hat {'Infinitydust entfernt' if remove else 'Infinitydust vergeben'}.",
        f"Modus: **{escape_display_text(mode, fallback='single')}**",
        "",
    ]
    for user_id, applied_amount in results:
        target = _member_mention_or_fallback(interaction.guild, user_id)
        applied_int = int(applied_amount)
        if remove:
            if applied_int <= 0:
                lines.append(f"❌ {target}: **0x** entfernt (von **{requested_amount}x** angefordert)")
            elif applied_int < requested_amount:
                lines.append(
                    f"⚠️ {target}: **{applied_int}x** entfernt "
                    f"(von **{requested_amount}x** angefordert)"
                )
            else:
                lines.append(f"✅ {target}: **{applied_int}x** entfernt")
        else:
            lines.append(f"✅ {target}: **{applied_int}x** erhalten")
    if remove:
        lines.append("")
        lines.append(f"Angeforderte Menge pro Nutzer: **{requested_amount}x**")

    embed = discord.Embed(
        title=f"💎 {action_title}",
        description="\n".join(lines),
        color=embed_color,
    )
    # Übersicht-Field analog zu /karte-geben — visuell parallel halten,
    # auch wenn Give-Pfad immer alles im ✅-Bucket hat.
    embed.add_field(
        name=(
            f"\u00dcbersicht (\u2705 {applied_count} angewendet \u00b7 "
            f"\u26a0\ufe0f {partial_count} unvollst\u00e4ndig \u00b7 "
            f"\u274c {failed_count} fehlgeschlagen)"
        ),
        value="_keine weiteren Details_" if not results else " ",
        inline=False,
    )
    _apply_item_media(embed, "infinitydust", thumbnail=True)
    embed.set_footer(text=f"Ausgeführt von {safe_display_name(interaction.user, fallback='Unbekannt')}")
    sent_message = await _send_with_visibility(
        interaction,
        visibility_key,
        embed=embed,
    )
    return sent_message is not None


class DustGiveConfirmView(RestrictedView):
    """Final-Bestätigung im Multi-Mode bevor Infinitydust verteilt wird.

    Analog zu `GiveCardConfirmView` für `/karte-geben`. Single-Mode nutzt
    keine Confirm-View — Parität mit `/karte-geben`.
    """

    def __init__(self, requester_id: int, *, remove: bool = False):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.remove = bool(remove)
        self.value: bool | None = None
        confirm_label = "✅ Jetzt entfernen" if self.remove else "✅ Jetzt verteilen"
        self.confirm_button = ui.Button(label=confirm_label, style=discord.ButtonStyle.success)
        self.confirm_button.callback = self._confirm
        self.add_item(self.confirm_button)
        self.cancel_button = ui.Button(label="❌ Abbrechen", style=discord.ButtonStyle.danger)
        self.cancel_button.callback = self._cancel
        self.add_item(self.cancel_button)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = True
        self.stop()
        followup = "✅ Bestätigt – entferne Infinitydust..." if self.remove else "✅ Bestätigt – verteile Infinitydust..."
        await interaction.response.edit_message(content=followup, view=None)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = False
        self.stop()
        cancelled = "❌ Entfernen abgebrochen." if self.remove else "❌ Vergabe abgebrochen."
        await interaction.response.edit_message(content=cancelled, view=None)


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
        await interaction.followup.send(
            "\u274c Ung\u00fcltiger Modus. Nutze `single` oder `multi`.",
            ephemeral=True,
        )
        return

    visibility_key = command_visibility_key_for_interaction(interaction)
    action_phrase = "entfernen" if remove else "geben"
    target_user_ids: list[int] = []

    if mode_value == "single":
        user_select_view = AdminUserSelectView(interaction.user.id, interaction.guild)
        await interaction.followup.send(
            content=f"W\u00e4hle den Nutzer, dem du Infinitydust {action_phrase} m\u00f6chtest:",
            view=user_select_view,
            ephemeral=True,
        )
        await user_select_view.wait()
        if not user_select_view.value:
            await interaction.followup.send(
                "\u23f0 Keine Auswahl getroffen. Abgebrochen.",
                ephemeral=True,
            )
            return
        target_user_ids = [int(user_select_view.value)]
    else:
        multi_view = DustMultiUserSelectView(
            interaction.user.id, interaction.guild, item_label="Infinitydust"
        )
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
            await interaction.followup.send(
                "\u23f0 Keine Nutzer gew\u00e4hlt. Abgebrochen.",
                ephemeral=True,
            )
            return
        target_user_ids = [int(user_id) for user_id in multi_view.value]

    if mode_value == "multi":
        amount_view = DustQuickAmountView(interaction.user.id)
        await interaction.followup.send(
            content="W\u00e4hle die Menge Infinitydust:",
            view=amount_view,
            ephemeral=True,
        )
        await amount_view.wait()
        amount = amount_view.value
    else:
        amount = await _select_number(
            interaction,
            "W\u00e4hle die Menge Infinitydust:",
            DUST_MENU_AMOUNTS,
        )
    if not amount:
        await interaction.followup.send(
            "\u23f0 Keine Menge gew\u00e4hlt. Abgebrochen.",
            ephemeral=True,
        )
        return

    requested_amount = int(amount)

    # Confirm-Schritt im Multi-Mode (Parität zu /karte-geben).
    if mode_value == "multi":
        confirm_view = DustGiveConfirmView(interaction.user.id, remove=remove)
        target_lines: list[str] = []
        for user_id in target_user_ids[:25]:
            target_lines.append(_member_mention_or_fallback(interaction.guild, user_id))
        if len(target_user_ids) > 25:
            target_lines.append(f"... und {len(target_user_ids) - 25} weitere")
        action_word = "entfernen" if remove else "verteilen"
        confirm_embed = discord.Embed(
            title=f"\U0001F4DD Bestätigung: Infinitydust {action_word}",
            description=(
                f"**Empf\u00e4nger ({len(target_user_ids)}):**\n"
                + "\n".join(target_lines)
            ),
            color=0xF1C40F,
        )
        confirm_embed.add_field(
            name="Menge pro Nutzer",
            value=f"**{requested_amount}x** Infinitydust",
            inline=False,
        )
        if remove:
            confirm_embed.set_footer(text="Mit ✅ jetzt entfernen, ❌ abbrechen.")
        else:
            confirm_embed.set_footer(text="Mit ✅ jetzt verteilen, ❌ abbrechen.")
        await interaction.followup.send(
            embed=confirm_embed,
            view=confirm_view,
            ephemeral=True,
        )
        await confirm_view.wait()
        if confirm_view.value is not True:
            if confirm_view.value is None:
                await interaction.followup.send(
                    "\u23f0 Zeit abgelaufen. Vergabe abgebrochen.",
                    ephemeral=True,
                )
            return

    channel_id = int(getattr(interaction.channel, "id", 0) or 0)
    guild_id = int(getattr(interaction, "guild_id", 0) or getattr(interaction.guild, "id", 0) or 0)
    action_key = "remove" if remove else "give"
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
        visibility_key=visibility_key,
    )
    if result_sent:
        return
    await interaction.followup.send(
        "❌ Die öffentliche Ergebnisnachricht konnte nicht gesendet werden.",
        ephemeral=True,
    )

class CardSelectPagerView(RestrictedView):
    def __init__(self, requester_id: int, cards: Iterable[CardData]):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.cards: list[CardData] = list(cards)
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


class CardVariantSelectView(RestrictedView):
    def __init__(self, requester_id: int, base_name: str, variant_rows: list[tuple[str, int]], *, placeholder: str | None = None):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.base_name = base_name
        self.variant_rows = list(variant_rows)
        self.value: str | None = None
        options = [
            SelectOption(
                label=(f"{variant_name} (x{amount})" if amount > 1 else variant_name)[:100],
                value=variant_name,
            )
            for variant_name, amount in self.variant_rows[:25]
        ]
        if not options:
            options = [SelectOption(label="Keine Varianten verfügbar", value="__none__")]
        self.select = ui.Select(
            placeholder=placeholder or f"Wähle den Style für {base_name}...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        selected_value = str(self.select.values[0] or "").strip()
        if not selected_value or selected_value == "__none__":
            await interaction.response.send_message("❌ Keine gültige Variante verfügbar.", ephemeral=True)
            return
        self.value = selected_value
        self.stop()
        await interaction.response.defer()


class SingleMultiModeView(RestrictedView):
    def __init__(self, requester_id: int, *, placeholder: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.value: str | None = None
        self.select = ui.Select(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=[
                SelectOption(label="Single", value="single", description="Einen Nutzer auswählen"),
                SelectOption(label="Multi", value="multi", description="Mehrere Nutzer auswählen"),
            ],
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = str(self.select.values[0] or "").strip() or None
        self.stop()
        await interaction.response.defer()


class MultiCardSelectView(RestrictedView):
    """Sammelt mehrere Karten-Auswahlen mit Status/Fertig/Neustart-Buttons."""

    def __init__(self, requester_id: int, target_user_ids: list[int], guild: discord.Guild | None):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.target_user_ids = list(target_user_ids)
        self.guild = guild
        # Liste der gewählten Karten (Reihenfolge bleibt erhalten)
        self.selected_cards: list[str] = []
        self.value: list[str] | None = None
        self.cancelled: bool = False
        self.page = 0
        # Karten als Liste der Namen (gameplay catalogue)
        self._all_cards: list[CardData] = list(karten)
        self._message: discord.Message | None = None

        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=[],
            row=0,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.prev_button = ui.Button(label="< Zurück", style=discord.ButtonStyle.secondary, row=1)
        self.prev_button.callback = self._on_prev
        self.add_item(self.prev_button)

        self.next_button = ui.Button(label="Weiter >", style=discord.ButtonStyle.secondary, row=1)
        self.next_button.callback = self._on_next
        self.add_item(self.next_button)

        self.status_button = ui.Button(label="📋 Status", style=discord.ButtonStyle.primary, row=2)
        self.status_button.callback = self._on_status
        self.add_item(self.status_button)

        self.finish_button = ui.Button(label="✅ Fertig", style=discord.ButtonStyle.success, row=2)
        self.finish_button.callback = self._on_finish
        self.add_item(self.finish_button)

        self.cancel_button = ui.Button(label="Abbrechen", style=discord.ButtonStyle.danger, row=2)
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.cancel_button)

        self._render()

    def bind_message(self, message: discord.Message | None) -> None:
        self._message = message

    @property
    def _total_pages(self) -> int:
        return max(1, (len(self._all_cards) + 24) // 25)

    def _placeholder(self) -> str:
        count = len(self.selected_cards)
        suffix = f" ({count} gewählt)" if count else ""
        return f"Wähle eine Karte... Seite {self.page + 1}/{self._total_pages}{suffix}"

    def _render(self) -> None:
        start = self.page * 25
        subset = self._all_cards[start:start + 25]
        self.select.options = [
            SelectOption(label=str(c.get("name") or "?")[:100], value=str(c.get("name") or ""))
            for c in subset
            if c.get("name")
        ] or [SelectOption(label="Keine Karten verfügbar", value="__none__")]
        self.select.placeholder = self._placeholder()
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = start + 25 >= len(self._all_cards)
        self.finish_button.disabled = not self.selected_cards

    def content_text(self) -> str:
        target_count = len(self.target_user_ids)
        target_word = "Nutzer" if target_count == 1 else "Nutzern"
        lines = [
            f"Wähle eine oder mehrere Karten für **{target_count} {target_word}**.",
            "Wähle Karten nacheinander aus, drücke **📋 Status** für eine Übersicht oder **✅ Fertig**, wenn du alle ausgewählt hast.",
        ]
        if self.selected_cards:
            lines.append("")
            lines.append(f"Aktuell ausgewählt: {len(self.selected_cards)} Karte(n)")
        return "\n".join(lines)

    def _summary_embed(self) -> discord.Embed:
        target_lines: list[str] = []
        for user_id in self.target_user_ids[:25]:
            member = self.guild.get_member(user_id) if self.guild is not None else None
            if member is not None:
                target_lines.append(f"- {member.mention}")
            else:
                target_lines.append(f"- <@{user_id}>")
        if len(self.target_user_ids) > 25:
            target_lines.append(f"... und {len(self.target_user_ids) - 25} weitere")

        if self.selected_cards:
            cards_text = "\n".join(f"- **{name}**" for name in self.selected_cards)
        else:
            cards_text = "_Noch keine Karte gewählt._"

        embed = discord.Embed(
            title="📋 Status: Karten-Vergabe",
            description=(
                f"**Empfänger ({len(self.target_user_ids)}):**\n"
                + ("\n".join(target_lines) or "_keine_")
            ),
            color=0x3498DB,
        )
        embed.add_field(
            name=f"Karten ({len(self.selected_cards)})",
            value=cards_text[:1024],
            inline=False,
        )
        embed.set_footer(text="Mit ✅ Fertig bestätigst du die Vergabe.")
        return embed

    async def _refresh_origin(self) -> None:
        self._render()
        if self._message is not None:
            try:
                await self._message.edit(content=self.content_text(), view=self)
            except Exception:
                logging.exception("Failed to refresh MultiCardSelectView message")

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        chosen = str(self.select.values[0] or "").strip()
        if not chosen or chosen == "__none__":
            await interaction.response.send_message("❌ Ungültige Auswahl.", ephemeral=True)
            return
        # Variante auflösen
        if card_has_multiple_variants(chosen, cards=karten):
            variant_rows = [
                (variant_name, 1)
                for variant_name in variant_names_for_base(chosen, cards=karten)
            ]
            variant_view = CardVariantSelectView(
                self.requester_id,
                chosen,
                variant_rows,
                placeholder=f"Wähle den Style für {chosen}...",
            )
            await interaction.response.send_message(
                f"Wähle den Style für **{chosen}**:",
                view=variant_view,
                ephemeral=True,
            )
            await variant_view.wait()
            resolved = str(variant_view.value or "").strip()
            if not resolved:
                return
        else:
            resolved = default_variant_name_for_base(chosen, cards=karten) or chosen
            await interaction.response.defer()
        self.selected_cards.append(resolved)
        await self._refresh_origin()

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        self._render()
        await interaction.response.edit_message(content=self.content_text(), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if (self.page + 1) * 25 < len(self._all_cards):
            self.page += 1
        self._render()
        await interaction.response.edit_message(content=self.content_text(), view=self)

    async def _on_status(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        status_view = MultiCardStatusView(self)
        await interaction.response.send_message(
            embed=self._summary_embed(),
            view=status_view,
            ephemeral=True,
        )

    async def _on_finish(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if not self.selected_cards:
            await interaction.response.send_message("Wähle erst mindestens eine Karte.", ephemeral=True)
            return
        self.value = list(self.selected_cards)
        await interaction.response.edit_message(content="✅ Karten-Auswahl abgeschlossen.", view=None)
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.cancelled = True
        self.value = None
        self.stop()
        await interaction.response.edit_message(content="⏰ Auswahl abgebrochen.", view=None)

    async def restart_selection(self) -> None:
        self.selected_cards = []
        self.page = 0
        await self._refresh_origin()


class MultiCardStatusView(RestrictedView):
    """Status-Antwort mit Buttons Neustart / Weiter / Fertig."""

    def __init__(self, parent: MultiCardSelectView):
        super().__init__(timeout=120)
        self.parent_view = parent

    @ui.button(label="🔄 Neustart der Auswahl", style=discord.ButtonStyle.danger)
    async def restart(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        await self.parent_view.restart_selection()
        self.stop()
        await interaction.response.edit_message(
            content="🔄 Auswahl wurde geleert. Wähle erneut Karten oben aus.",
            embed=None,
            view=None,
        )

    @ui.button(label="↩️ Weiter machen", style=discord.ButtonStyle.secondary)
    async def keep_going(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(
            content="Wähle weitere Karten oben aus oder drücke ✅ Fertig.",
            embed=None,
            view=None,
        )

    @ui.button(label="✅ Fertig", style=discord.ButtonStyle.success)
    async def finish(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        if not self.parent_view.selected_cards:
            await interaction.response.send_message("Wähle erst mindestens eine Karte.", ephemeral=True)
            return
        self.parent_view.value = list(self.parent_view.selected_cards)
        await interaction.response.edit_message(
            content="✅ Karten-Auswahl abgeschlossen.",
            embed=None,
            view=None,
        )
        self.parent_view.stop()
        self.stop()


class GiveCardConfirmView(RestrictedView):
    """Final-Bestätigung im Multi-Mode bevor die Karten verteilt werden."""

    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.value: bool | None = None

    @ui.button(label="✅ Karten jetzt vergeben", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="✅ Bestätigt – verteile Karten...", view=None)

    @ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="❌ Vergabe abgebrochen.", view=None)


class GiveConfirmView(RestrictedView):
    """Generische Ja/Nein-Bestätigung mit anpassbarem Bestätigungs-Label."""

    def __init__(self, requester_id: int, *, confirm_label: str = "✅ Jetzt vergeben"):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.value: bool | None = None
        self.confirm_button.label = confirm_label

    @ui.button(label="✅ Jetzt vergeben", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="✅ Bestätigt – verteile...", view=None)

    @ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="❌ Abgebrochen.", view=None)


class ConfirmDeleteUserView(RestrictedView):
    """Bestätigungs-Dialog für das Löschen aller Bot-Daten eines Nutzers."""

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


async def send_stats_excel_export(interaction: discord.Interaction) -> None:
    try:
        workbook_bytes, filename = await build_stats_workbook()
    except Exception:
        logging.exception("Failed to build stats workbook")
        await interaction.followup.send("❌ Der Excel-Export konnte nicht erstellt werden.", ephemeral=True)
        return

    try:
        await interaction.user.send(
            content="📊 Hier ist dein aktueller Statistik-Export.",
            file=discord.File(BytesIO(workbook_bytes), filename=filename),
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ Ich konnte dir keine DM senden. Bitte aktiviere Direktnachrichten.", ephemeral=True)
        return
    except Exception:
        logging.exception("Failed to deliver stats workbook via DM")
        await interaction.followup.send("❌ Der Excel-Export wurde erstellt, konnte aber nicht per DM gesendet werden.", ephemeral=True)
        return

    await _log_event_safe(
        "admin_stats_export",
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        thread_id=_thread_id_for_channel(interaction.channel),
        actor_user_id=interaction.user.id,
        command_name="stats_e",
        payload={"filename": filename, "bytes": len(workbook_bytes)},
    )
    await interaction.followup.send("✅ Die Excel-Datei wurde dir per DM geschickt.", ephemeral=True)

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
    if not await is_alpha_enabled(interaction.guild_id):
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
        await _send_with_visibility(interaction, visibility_key, content=f"karten.py ist valide ({len(list(karten))} Karten).")
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
    grouped_cards = _group_owned_cards_for_current_mode(user_karten)
    infinitydust = await get_infinitydust(user_id)
    units = await get_units(user_id)
    if not user_karten and infinitydust == 0:
        await _send_with_visibility(interaction, visibility_key, content=f"❌ {mention} hat noch keine Karten in seiner Sammlung.")
        return
    embed = discord.Embed(
        title=f"🔍 Vault von {escape_display_text(user_name, fallback=mention)}",
        description=f"**{mention}** besitzt **{len(grouped_cards)}** verschiedene Helden:",
    )
    if infinitydust > 0:
        embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
        _apply_item_media(embed, "infinitydust", thumbnail=True)
    units_value = _build_units_collection_field_value(units)
    if units_value:
        unit_item = get_item_by_id("unit") or {}
        unit_label = str(unit_item.get("display_name") or "Unit").strip() or "Unit"
        embed.add_field(name=f"🪙 {unit_label}", value=units_value, inline=True)
        if infinitydust <= 0:
            _apply_item_media(embed, "unit", thumbnail=True)
    for group in grouped_cards:
        base_name = str(group.get("base_name") or "")
        karte = await get_karte_by_name(base_name)
        if karte:
            variant_rows = list(group.get("variants") or [])
            variant_text = ", ".join(f"{variant_name} x{amount}" for variant_name, amount in variant_rows)
            embed.add_field(
                name=_group_option_label(group),
                value=f"{str(karte['beschreibung'])[:80]}...\nVarianten: {variant_text}",
                inline=False,
            )
    embed.set_footer(text=f"Vault-Lookup durch {safe_display_name(interaction.user, fallback='Unbekannt')}")
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
    embed.set_footer(text=f"Angefordert von {safe_display_name(interaction.user, fallback='Unbekannt')} | {time.strftime('%d.%m.%Y %H:%M:%S')}")
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
    total_cards = len(list(karten))
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
    ("Alpha ON", "alpha_on"),
    ("Alpha OFF", "alpha_off"),
    ("Beta ON", "beta_on"),
    ("Beta OFF", "beta_off"),
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

async def refresh_latest_anfang_message_for_guild(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or interaction.guild_id is None:
        return False
    existing = await get_latest_anfang_message(interaction.guild_id)
    if not existing:
        return False
    channel_id, message_id = existing
    try:
        channel = interaction.guild.get_channel(channel_id) or await interaction.guild.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False
        message = await channel.fetch_message(message_id)
        alpha_enabled = await is_alpha_enabled(interaction.guild_id)
        beta_enabled = await is_beta_enabled(interaction.guild_id)
        await message.edit(
            content=build_anfang_intro_text(alpha_enabled=alpha_enabled, beta_enabled=beta_enabled),
            view=AnfangView(alpha_enabled=alpha_enabled, beta_enabled=beta_enabled),
        )
        await set_latest_anfang_message(interaction.guild_id, channel_id, message_id, interaction.user.id)
        return True
    except Exception:
        logging.exception("Failed to refresh latest /anfang message after feature flag change")
        return False


async def handle_dev_action(interaction: discord.Interaction, requester_id: int, action: str):
    if interaction.user.id != requester_id:
        await interaction.response.send_message("Nicht dein Menü.", ephemeral=True)
        return
    if not await require_owner_or_dev(interaction):
        return
    if not await is_channel_allowed(interaction):
        return

    if action in {"alpha_on", "alpha_off", "beta_on", "beta_off"}:
        if interaction.guild is None:
            await _send_with_visibility(interaction, "feature_flags", content=SERVER_ONLY)
            return
        is_alpha_action = action.startswith("alpha_")
        enabled = action.endswith("_on")
        if is_alpha_action:
            view = AlphaConfirmView(interaction.user.id, enable=enabled)
            title = game_ui_texts.ALPHA_CONFIRM_ON_TITLE if enabled else game_ui_texts.ALPHA_CONFIRM_OFF_TITLE
            text = game_ui_texts.ALPHA_CONFIRM_ON_TEXT if enabled else game_ui_texts.ALPHA_CONFIRM_OFF_TEXT
            # Req. 10.1/10.2: aktuellen Status im Dialog anzeigen.
            status_line = game_ui_texts.render_mode_confirm(
                "Alpha", await is_alpha_enabled(interaction.guild_id)
            )
        else:
            view = BetaConfirmView(interaction.user.id, enable=enabled)
            title = game_ui_texts.BETA_CONFIRM_ON_TITLE if enabled else game_ui_texts.BETA_CONFIRM_OFF_TITLE
            text = game_ui_texts.BETA_CONFIRM_ON_TEXT if enabled else game_ui_texts.BETA_CONFIRM_OFF_TEXT
            status_line = game_ui_texts.render_mode_confirm(
                "Beta", await is_beta_enabled(interaction.guild_id)
            )

        await _send_with_visibility(
            interaction,
            "feature_flags",
            content=f"**{title}**\n\n{status_line}\n\n{text}",
            view=view,
        )
        return

    if action == "maintenance_on":
        view = MaintenanceConfirmView(interaction.user.id, enable=True)
        status_line = game_ui_texts.render_mode_confirm(
            "Maintenance", await is_maintenance_enabled(interaction.guild_id)
        )
        await _send_with_visibility(
            interaction,
            "maintenance",
            content=f"**{game_ui_texts.MAINTENANCE_CONFIRM_ON_TITLE}**\n\n{status_line}\n\n{game_ui_texts.MAINTENANCE_CONFIRM_ON_TEXT}",
            view=view,
        )
        return
    if action == "maintenance_off":
        view = MaintenanceConfirmView(interaction.user.id, enable=False)
        status_line = game_ui_texts.render_mode_confirm(
            "Maintenance", await is_maintenance_enabled(interaction.guild_id)
        )
        await _send_with_visibility(
            interaction,
            "maintenance",
            content=f"**{game_ui_texts.MAINTENANCE_CONFIRM_OFF_TITLE}**\n\n{status_line}\n\n{game_ui_texts.MAINTENANCE_CONFIRM_OFF_TEXT}",
            view=view,
        )
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
        # Wie grant_card: Multi-User (oder ganze Rolle) -> eine Menge -> Bestätigung -> verteilen
        if interaction.guild is None:
            await _send_ephemeral(interaction, content=SERVER_ONLY)
            return
        multi_user_view = DustMultiUserSelectView(
            interaction.user.id, interaction.guild, item_label="Infinitydust"
        )
        multi_user_message = await interaction.followup.send(
            content=multi_user_view._content(),
            embed=multi_user_view._summary_embed(),
            view=multi_user_view,
            ephemeral=True,
            wait=True,
        )
        multi_user_view.bind_message(multi_user_message)
        await multi_user_view.wait()
        if not multi_user_view.value:
            await interaction.followup.send("⏰ Keine Nutzer gewählt. Abgebrochen.", ephemeral=True)
            return
        target_user_ids = [int(uid) for uid in multi_user_view.value]

        amount = await _select_number(interaction, "Menge wählen", [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000])
        if not amount:
            return
        amount = int(amount)

        confirm_view = GiveConfirmView(interaction.user.id, confirm_label="✅ Dust jetzt verteilen")
        target_lines: list[str] = []
        for uid in target_user_ids[:25]:
            member = interaction.guild.get_member(uid)
            target_lines.append(member.mention if member else f"<@{uid}>")
        if len(target_user_ids) > 25:
            target_lines.append(f"... und {len(target_user_ids) - 25} weitere")
        confirm_embed = discord.Embed(
            title="📝 Bestätigung: Infinitydust verteilen",
            description=(
                f"**Empfänger ({len(target_user_ids)}):**\n" + "\n".join(target_lines)
            ),
            color=0xF1C40F,
        )
        confirm_embed.add_field(
            name="Menge",
            value=f"**{amount}x Infinitydust** pro Nutzer",
            inline=False,
        )
        confirm_embed.set_footer(text="Mit ✅ jetzt verteilen, ❌ abbrechen.")
        await interaction.followup.send(embed=confirm_embed, view=confirm_view, ephemeral=True)
        await confirm_view.wait()
        if confirm_view.value is not True:
            if confirm_view.value is None:
                await interaction.followup.send("⏰ Zeit abgelaufen. Vergabe abgebrochen.", ephemeral=True)
            return

        for uid in target_user_ids:
            await add_infinitydust(uid, amount)
            await _log_event_safe(
                "admin_dust_action",
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                thread_id=_thread_id_for_channel(interaction.channel),
                actor_user_id=interaction.user.id,
                target_user_id=uid,
                command_name="entwicklerpanel",
                payload={"action": "give", "requested_amount": amount, "applied_amount": amount, "mode": "multi"},
            )
        logging.info(
            "Give dust: actor=%s targets=%s amount=%s",
            interaction.user.id, len(target_user_ids), amount,
        )

        result_lines: list[str] = []
        for uid in target_user_ids[:25]:
            member = interaction.guild.get_member(uid)
            result_lines.append(member.mention if member else f"<@{uid}>")
        if len(target_user_ids) > 25:
            result_lines.append(f"... und {len(target_user_ids) - 25} weitere")
        embed = discord.Embed(
            title="Infinitydust vergeben",
            description=(
                f"{interaction.user.mention} hat **{amount}x Infinitydust** an "
                f"**{len(target_user_ids)} Nutzer** verteilt.\n\n" + "\n".join(result_lines)
            ),
            color=0x2ECC71,
        )
        _apply_item_media(embed, "infinitydust", thumbnail=True)
        await _send_with_visibility(interaction, "give_dust", embed=embed)
        return
    if action == "grant_card":
        # 1:1 wie /karte-geben multi: Multi-User -> Multi-Karten -> Bestätigung -> Verteilen
        if interaction.guild is None:
            await _send_ephemeral(interaction, content=SERVER_ONLY)
            return
        multi_user_view = DustMultiUserSelectView(
            interaction.user.id, interaction.guild, item_label="Karten"
        )
        multi_user_message = await interaction.followup.send(
            content=multi_user_view._content(),
            embed=multi_user_view._summary_embed(),
            view=multi_user_view,
            ephemeral=True,
            wait=True,
        )
        multi_user_view.bind_message(multi_user_message)
        await multi_user_view.wait()
        if not multi_user_view.value:
            await interaction.followup.send("⏰ Keine Nutzer gewählt. Abgebrochen.", ephemeral=True)
            return
        target_user_ids: list[int] = [int(uid) for uid in multi_user_view.value]

        multi_card_view = MultiCardSelectView(
            interaction.user.id, target_user_ids, interaction.guild
        )
        card_message = await interaction.followup.send(
            content=multi_card_view.content_text(),
            view=multi_card_view,
            ephemeral=True,
            wait=True,
        )
        multi_card_view.bind_message(card_message)
        await multi_card_view.wait()
        if not multi_card_view.value:
            await interaction.followup.send("⏰ Keine Karten gewählt. Abgebrochen.", ephemeral=True)
            return
        selected_card_names: list[str] = list(multi_card_view.value)

        confirm_view = GiveCardConfirmView(interaction.user.id)
        target_lines: list[str] = []
        for uid in target_user_ids[:25]:
            member = interaction.guild.get_member(uid)
            target_lines.append(member.mention if member else f"<@{uid}>")
        if len(target_user_ids) > 25:
            target_lines.append(f"... und {len(target_user_ids) - 25} weitere")
        cards_text = "\n".join(f"- **{n}**" for n in selected_card_names)
        confirm_embed = discord.Embed(
            title="📝 Bestätigung: Karten verteilen",
            description=(
                f"**Empfänger ({len(target_user_ids)}):**\n"
                + "\n".join(target_lines)
            ),
            color=0xF1C40F,
        )
        confirm_embed.add_field(
            name=f"Karten ({len(selected_card_names)})",
            value=cards_text[:1024],
            inline=False,
        )
        confirm_embed.set_footer(text="Mit ✅ jetzt verteilen, ❌ abbrechen.")
        await interaction.followup.send(embed=confirm_embed, view=confirm_view, ephemeral=True)
        await confirm_view.wait()
        if confirm_view.value is not True:
            if confirm_view.value is None:
                await interaction.followup.send("⏰ Zeit abgelaufen. Vergabe abgebrochen.", ephemeral=True)
            return

        async def _audit_grant_outcome(uid: int, card_name: str, outcome: str) -> None:
            logging.info(
                "Grant card via panel: actor=%s target=%s card=%s outcome=%s",
                interaction.user.id, uid, card_name, outcome,
            )
            await _log_event_safe(
                "admin_card_grant",
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                thread_id=_thread_id_for_channel(interaction.channel),
                actor_user_id=interaction.user.id,
                target_user_id=uid,
                command_name="entwicklerpanel",
                hero_name=card_name,
                payload={
                    "amount": 1,
                    "added": outcome == "added",
                    "outcome": outcome,
                },
            )

        summary = await grant_cards_to_users(
            target_user_ids=target_user_ids,
            card_names=selected_card_names,
            add_card=add_exact_card_variant_once,
            is_card_known=lambda name: _card_by_name_local(name) is not None,
            on_outcome=_audit_grant_outcome,
        )
        per_user_added: dict[int, list[str]] = {
            uid: summary.per_user_added(uid) for uid in target_user_ids
        }
        per_user_skipped: dict[int, list[str]] = {
            uid: summary.per_user_skipped(uid) for uid in target_user_ids
        }
        per_user_failed: dict[int, list[str]] = {
            uid: summary.per_user_failed(uid) for uid in target_user_ids
        }

        total_added = summary.total_added
        total_skipped = summary.total_skipped
        total_failed = summary.total_failed
        if total_failed > 0:
            embed_color = 0xE74C3C
        elif total_added > 0:
            embed_color = 0x2ECC71
        else:
            embed_color = 0xE67E22
        result_embed = discord.Embed(
            title="🎁 Karten vergeben",
            description=(
                f"{interaction.user.mention} hat **{len(selected_card_names)} Karte(n)** "
                f"an **{len(target_user_ids)} Nutzer** verteilt."
            ),
            color=embed_color,
        )
        result_lines: list[str] = []
        for uid in target_user_ids:
            member = interaction.guild.get_member(uid)
            mention = member.mention if member else f"<@{uid}>"
            added_names = per_user_added.get(uid, [])
            skipped_names = per_user_skipped.get(uid, [])
            failed_names = per_user_failed.get(uid, [])
            parts: list[str] = []
            if added_names:
                parts.append("✅ hinzugefügt: " + ", ".join(added_names))
            if skipped_names:
                parts.append("⚠️ bereits vorhanden: " + ", ".join(skipped_names))
            if failed_names:
                parts.append("❌ fehlgeschlagen: " + ", ".join(failed_names))
            line = f"{mention} — " + (" | ".join(parts) if parts else "_keine Änderung_")
            result_lines.append(line)
        joined_lines = "\n".join(result_lines)
        if len(joined_lines) > 4000:
            joined_lines = joined_lines[:3990] + "\n…"
        result_embed.add_field(
            name=(
                f"Übersicht (✅ {total_added} · "
                f"⚠️ {total_skipped} · ❌ {total_failed})"
            ),
            value=joined_lines or "_keine_",
            inline=False,
        )
        await _send_with_visibility(interaction, "grant_card", embed=result_embed)
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
        await _log_event_safe(
            "admin_card_revoke",
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            target_user_id=user_id,
            command_name="entwicklerpanel",
            hero_name=card_name,
            payload={"amount": int(amount), "new_total": int(new_amount)},
        )
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
        if await is_alpha_enabled(interaction.guild_id):
            await _send_with_visibility(interaction, "set_mission", content=ALPHA_FEATURE_DISABLED_TEXT)
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
            circle = STATUS_CIRCLE_MAP.get(color, "?")
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
            SelectOption(label="? Unsichtbar", value="invisible"),
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
        await _log_event_safe(
            "admin_bot_status_changed",
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            thread_id=_thread_id_for_channel(interaction.channel),
            actor_user_id=interaction.user.id,
            command_name="bot_status",
            payload={"status": choice},
        )

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

_command_api = build_command_api(globals())
_player_commands = register_player_commands(bot, PlayerFacade(_command_api))
daily_command = _player_commands["täglich"]
eingeladen = _player_commands["eingeladen"]
fuse = _player_commands["fuse"]
vault = _player_commands["vault"]
anfang = _player_commands["anfang"]

_gameplay_commands = register_gameplay_commands(bot, GameplayFacade(_command_api))
mission = _gameplay_commands["mission"]
story = _gameplay_commands["story"]
fight = _gameplay_commands["fight"]

_admin_commands = register_admin_commands(bot, AdminFacade(_command_api))


@bot.tree.command(name="stats_e", description="Nur für Admins!!!")
async def stats_e(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Du hast keine Berechtigung für diesen Command! Nur Admins/Owner können den Excel-Export anfordern.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    await send_stats_excel_export(interaction)


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


