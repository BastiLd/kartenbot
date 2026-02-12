import asyncio
import json
import logging
import sys
import random
import sqlite3
import time
import os
from collections import deque
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, ui, SelectOption
from discord.ext import commands

from config import get_bot_token
from db import DB_PATH, close_db, db_context, init_db
from karten import karten
from services.battle import (
    STATUS_CIRCLE_MAP,
    STATUS_PRIORITY_MAP,
    _presence_to_color,
    apply_outgoing_attack_modifier,
    calculate_damage,
    create_battle_embed,
    create_battle_log_embed,
    resolve_multi_hit_damage,
    update_battle_log,
)
import secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

LOG_PATH = Path("bot.log")
ERROR_COUNT = 0

class ErrorCounter(logging.Handler):
    def emit(self, record):
        global ERROR_COUNT
        if record.levelno >= logging.ERROR:
            ERROR_COUNT += 1

error_counter = ErrorCounter()
logging.getLogger().addHandler(error_counter)

file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(file_handler)

BOT_START_TIME = time.time()
KATABUMP_MAX_INTERACTIONS_PER_MIN = 200
KATABUMP_INTERACTION_WINDOW_SEC = 60
_interaction_timestamps = deque()
_persistent_views_registered = False

class KatabumpCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.channel_id is None:
            return False
        command_name = ""
        if interaction.command:
            command_name = getattr(interaction.command, "qualified_name", interaction.command.name)
        channel_allowed = await is_channel_allowed_ids(
            interaction.guild_id,
            interaction.channel_id,
            getattr(interaction.channel, "parent_id", None),
        )
        allow_channel_bypass = False
        if command_name == "ad":
            allow_channel_bypass = await is_owner_or_dev(interaction)
        elif command_name == "configure add":
            allow_channel_bypass = await is_config_admin(interaction)
        if interaction.type == discord.InteractionType.autocomplete:
            return channel_allowed or allow_channel_bypass
        if not channel_allowed and not allow_channel_bypass:
            return False
        if await is_maintenance_enabled(interaction.guild_id):
            if not await is_owner_or_dev(interaction):
                if channel_allowed:
                    message = "⛔ Der Bot ist gerade im Wartungsmodus. Bitte später erneut versuchen."
                    if not interaction.response.is_done():
                        await interaction.response.send_message(message, ephemeral=True)
                    else:
                        await interaction.followup.send(message, ephemeral=True)
                return False
        now = time.monotonic()
        while _interaction_timestamps and now - _interaction_timestamps[0] > KATABUMP_INTERACTION_WINDOW_SEC:
            _interaction_timestamps.popleft()
        if len(_interaction_timestamps) >= KATABUMP_MAX_INTERACTIONS_PER_MIN:
            if channel_allowed:
                message = "⏳ Zu viele Anfragen. Bitte in einer Minute erneut versuchen (Katabump-Limit)."
                if not interaction.response.is_done():
                    await interaction.response.send_message(message, ephemeral=True)
                else:
                    await interaction.followup.send(message, ephemeral=True)
            return False
        _interaction_timestamps.append(now)
        return True


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=KatabumpCommandTree)

def create_bot() -> commands.Bot:
    return bot

ADMIN_SLASH_COMMANDS = {
    "configure",
    "reset-intro",
    "vaultlook",
    "test-bericht",
    "give",
    "bot_status",
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


# Rollen-IDs für Admin/Owner (vom Nutzer bestätigt)
BASTI_USER_ID = 965593518745731152
DEV_ROLE_ID = 1463304167421513961  # Bot_Developer/Tester role ID

MFU_ADMIN_ROLE_ID = 889559991437119498
OWNER_ROLE_ROLE_ID = 1272827906032402464

BUG_REPORT_TALLY_URL = os.getenv("BUG_REPORT_TALLY_URL", "https://tally.so/r/7RNo8z")
BOT_STATUS_KEY = "presence_status"
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

def _format_amount_for_label(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) == 2:
        try:
            return f"{int(value[0])}-{int(value[1])}"
        except Exception:
            return None
    try:
        return str(int(value))
    except Exception:
        return None

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
            try:
                turns = max(1, int(effect.get("turns", 1) or 1))
            except Exception:
                turns = 1
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


def _damage_text_for_attack(attack: dict) -> str:
    dmg = attack.get("damage")
    if isinstance(dmg, list) and len(dmg) == 2:
        return f"{int(dmg[0])}-{int(dmg[1])}"
    try:
        return str(int(dmg or 0))
    except Exception:
        return "0"


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
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer()
        return True
    except discord.NotFound:
        logging.warning("Interaction expired before defer; continuing with message-edit fallback.")
        return False
    except discord.HTTPException:
        logging.exception("Failed to defer interaction")
        return False


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
    await restore_bot_presence_status()
    logging.info("Bot ist online als %s", bot.user)
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
    # Prüfe, ob User in diesem Kanal das Intro schon gesehen hat
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT 1 FROM user_seen_channels WHERE user_id = ? AND guild_id = ? AND channel_id = ?",
            (message.author.id, message.guild.id, message.channel.id),
        )
        seen = await cursor.fetchone()
        if not seen:
            # Speichere, dass der User es gesehen hat
            await db.execute(
                "INSERT OR REPLACE INTO user_seen_channels (user_id, guild_id, channel_id) VALUES (?, ?, ?)",
                (message.author.id, message.guild.id, message.channel.id),
            )
            await db.commit()
            # Poste Prompt im Kanal; Button öffnet das Intro ephemer (nur für den Nutzer sichtbar)
            try:
                prompt = f"{message.author.mention} Willkommen! Klicke unten, um das Intro nur für dich zu sehen."
                view = IntroEphemeralPromptView(message.author.id)
                await message.channel.send(
                    content=prompt,
                    view=view,
                    silent=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except Exception:
                logging.exception("Unexpected error")

    # Commands weiter verarbeiten lassen
    await bot.process_commands(message)

# Kanal-Restriktion: Only respond in configured channel
async def is_channel_allowed(interaction: discord.Interaction, *, bypass_maintenance: bool = False) -> bool:
    if interaction.guild is None or interaction.channel_id is None:
        return False
    parent_id = getattr(interaction.channel, "parent_id", None)
    if not await is_channel_allowed_ids(interaction.guild_id, interaction.channel_id, parent_id):
        return False
    if not bypass_maintenance and await is_maintenance_enabled(interaction.guild_id):
        if not await is_owner_or_dev(interaction):
            message = "⛔ Der Bot ist gerade im Wartungsmodus. Bitte später erneut versuchen."
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

class RestrictedView(ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await is_channel_allowed(interaction)

class RestrictedModal(ui.Modal):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await is_channel_allowed(interaction)

# Infinitydust-System
async def add_infinitydust(user_id, amount=1):
    """Fügt Infinitydust zu einem User hinzu"""
    async with db_context() as db:
        # Prüfe ob User bereits Infinitydust hat
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        
        if row:
            # User existiert bereits - aktualisiere Infinitydust
            current_dust = row[0] or 0
            new_dust = current_dust + amount
            await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_dust, user_id))
        else:
            # User existiert nicht - erstelle neuen Eintrag
            await db.execute("INSERT INTO user_infinitydust (user_id, amount) VALUES (?, ?)", (user_id, amount))
        await db.commit()

async def get_infinitydust(user_id):
    """Holt die Infinitydust-Menge eines Users"""
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0

async def spend_infinitydust(user_id, amount):
    """Verbraucht Infinitydust eines Users"""
    async with db_context() as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current_dust = row[0] if row and row[0] else 0
        if current_dust < amount:
            return False  # Nicht genug Dust

        new_amount = current_dust - amount
        await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_amount, user_id))
        await db.commit()
        return True  # Erfolgreich verbraucht

async def add_card_buff(user_id, card_name, buff_type, attack_number, buff_amount):
    """Fügt einen Buff zu einer Karte hinzu"""
    async with db_context() as db:
        await db.execute("""
            INSERT OR REPLACE INTO user_card_buffs 
            (user_id, card_name, buff_type, attack_number, buff_amount) 
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, card_name, buff_type, attack_number, buff_amount))
        await db.commit()

async def get_card_buffs(user_id, card_name):
    """Holt alle Buffs für eine spezifische Karte eines Users"""
    async with db_context() as db:
        cursor = await db.execute("""
            SELECT buff_type, attack_number, buff_amount 
            FROM user_card_buffs 
            WHERE user_id = ? AND card_name = ?
        """, (user_id, card_name))
        return await cursor.fetchall()

async def check_and_add_karte(user_id, karte):
    """Prüft ob User die Karte schon hat und fügt sie hinzu oder wandelt zu Infinitydust um"""
    async with db_context() as db:
        # Prüfe ob User die Karte schon hat
        cursor = await db.execute("SELECT COUNT(*) FROM user_karten WHERE user_id = ? AND karten_name = ?", (user_id, karte['name']))
        row = await cursor.fetchone()
        
        if row[0] > 0:
            # Karte existiert bereits - wandle zu Infinitydust um
            await add_infinitydust(user_id, 1)
            return False  # Keine neue Karte hinzugefügt
        else:
            # Neue Karte hinzufügen
            await add_karte(user_id, karte['name'])
            return True  # Neue Karte hinzugefügt

# Hilfsfunktion: Karte zum Nutzer hinzufügen
async def add_karte(user_id, karten_name):
    async with db_context() as db:
        await db.execute(
            "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, 1) ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + 1",
            (user_id, karten_name)
        )
        await db.commit()

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

# Hilfsfunktion: Missionsfortschritt speichern
async def add_mission_reward(user_id):
    karte = random.choice(karten)
    is_new_card = await check_and_add_karte(user_id, karte)
    return karte, is_new_card

# Hilfsfunktion: Missionen pro Tag prüfen
async def get_mission_count(user_id):
    # Tagesbeginn in Europe/Berlin
    today_start = berlin_midnight_epoch()
    
    async with db_context() as db:
        cursor = await db.execute("SELECT mission_count, last_mission_reset FROM user_daily WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        
        # Falls es keinen Eintrag gibt ODER last_mission_reset NULL ist ODER vor heutigem Tagesbeginn liegt → zurücksetzen
        if not row or row[1] is None or row[1] < today_start:
            # Neuer Tag oder kein Eintrag
            await db.execute("INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, 0, ?)", (user_id, today_start))
            await db.commit()
            return 0
        else:
            return (row[0] or 0)

# Hilfsfunktion: Missionen pro Tag erhöhen
async def increment_mission_count(user_id):
    today_start = berlin_midnight_epoch()
    
    async with db_context() as db:
        await db.execute("INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, COALESCE((SELECT mission_count FROM user_daily WHERE user_id = ?), 0) + 1, ?)", 
                        (user_id, user_id, today_start))
        await db.commit()

def _build_mission_embed(mission_data: dict) -> discord.Embed:
    title = mission_data.get("title") or "Mission"
    description = mission_data.get("description") or "Hier kommt später die Story. Hier kommt später die Story."
    reward_card = mission_data.get("reward_card") or {}
    waves = mission_data.get("waves", 0)
    embed = discord.Embed(title=title, description=description)
    embed.add_field(name="Wellen", value=f"{waves}", inline=True)
    if reward_card:
        embed.add_field(name="🎁 Belohnung", value=f"**{reward_card.get('name', '?')}**", inline=True)
        if reward_card.get("bild"):
            embed.set_thumbnail(url=reward_card["bild"])
    return embed

# Hilfsfunktionen für Team-Management
async def get_team(user_id):
    async with db_context() as db:
        cursor = await db.execute("SELECT team FROM user_teams WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return []

async def set_team(user_id, team):
    async with db_context() as db:
        await db.execute("INSERT OR REPLACE INTO user_teams (user_id, team) VALUES (?, ?)", (user_id, json.dumps(team)))
        await db.commit()

# Hilfsfunktion: Karte nach Namen finden
async def get_karte_by_name(name):
    for karte in karten:
        if karte["name"].lower() == name.lower():
            return karte
    return None

# Hilfsfunktion: Karten des Nutzers abrufen
async def get_user_karten(user_id):
    async with db_context() as db:
        cursor = await db.execute("SELECT karten_name, anzahl FROM user_karten WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()

# Hilfsfunktion: Letzte Karte des Nutzers
async def get_last_karte(user_id):
    async with db_context() as db:
        cursor = await db.execute("SELECT karten_name FROM user_karten WHERE user_id = ? ORDER BY rowid DESC LIMIT 1", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

# Offene Kampf-/Missions-Requests (persistiert)
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
        return int(cursor.lastrowid)

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
        cursor = await db.execute(
            "SELECT * FROM fight_requests WHERE status = 'pending'"
        )
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
        return int(cursor.lastrowid)

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
        cursor = await db.execute(
            "SELECT * FROM mission_requests WHERE status = 'pending'"
        )
        return await cursor.fetchall()

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
        embed = discord.Embed(title=karte["name"], description=karte["beschreibung"])
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
            embed = discord.Embed(title="Mission abgeschlossen!", description=f"Du hast **{karte['name']}** erhalten!")
            embed.set_image(url=karte["bild"])
            await interaction.response.send_message(embed=embed, view=MissionView(self.user_id))
        else:
            # Karte wurde zu Infinitydust umgewandelt
            embed = discord.Embed(title="💎 Mission abgeschlossen - Infinitydust!", description=f"Du hattest **{karte['name']}** bereits!")
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
class BattleView(RestrictedView):
    def __init__(self, player1_card, player2_card, player1_id, player2_id, hp_view):
        super().__init__(timeout=120)
        self.player1_card = player1_card
        self.player2_card = player2_card
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.current_turn = player1_id

        # NEUES BUFF-SYSTEM: Health-Buffs hinzufügen
        base_hp1 = player1_card.get("hp", 100)
        base_hp2 = player2_card.get("hp", 100)

        # Diese werden später async geladen - erstmal Base-Werte setzen
        self.player1_hp = base_hp1
        self.player2_hp = base_hp2
        self.player1_max_hp = base_hp1
        self.player2_max_hp = base_hp2
        self.hp_view = hp_view

        # COOLDOWN-SYSTEM: Tracking für starke Attacken (min>90 und max>99 Schaden inkl. Buffs)
        # Format: {player_id: {attack_index: turns_remaining}}
        self.attack_cooldowns = {player1_id: {}, player2_id: {}}

        # KAMPF-LOG SYSTEM: Tracking für Log-Nachrichten
        self.battle_log_message = None
        self.round_counter = 0
        self._last_log_edit_ts = 0.0

        # SIDE EFFECTS SYSTEM: Tracking für aktive Effekte
        # Format: {player_id: [{'type': 'burning', 'duration': 3, 'damage': 15, 'applier': player_id}]}
        self.active_effects = {player1_id: [], player2_id: []}
        # Confusion flags: if a player is confused, their next turn is forced-random
        self.confused_next_turn = {player1_id: False, player2_id: False}
        # Manual reload tracking (e.g. Praezisionsschuss -> Nachladen)
        self.manual_reload_needed = {player1_id: {}, player2_id: {}}
        self.stunned_next_turn = {player1_id: False, player2_id: False}
        self.special_lock_next_turn = {player1_id: False, player2_id: False}
        self.blind_next_attack = {player1_id: 0.0, player2_id: 0.0}
        self.pending_flat_bonus = {player1_id: 0, player2_id: 0}
        self.pending_flat_bonus_uses = {player1_id: 0, player2_id: 0}
        self.pending_multiplier = {player1_id: 1.0, player2_id: 1.0}
        self.pending_multiplier_uses = {player1_id: 0, player2_id: 0}
        self.force_max_next = {player1_id: 0, player2_id: 0}
        self.guaranteed_hit_next = {player1_id: 0, player2_id: 0}
        self.incoming_modifiers = {player1_id: [], player2_id: []}
        self.outgoing_attack_modifiers = {player1_id: [], player2_id: []}
        self.absorbed_damage = {player1_id: 0, player2_id: 0}
        self.delayed_defense_queue = {player1_id: [], player2_id: []}
        self.airborne_pending_landing = {player1_id: None, player2_id: None}
        self._last_damage_roll_meta: dict | None = None

    def set_confusion(self, player_id: int, applier_id: int) -> None:
        """Mark player as confused for next turn and reflect it in active_effects for UI."""
        self.confused_next_turn[player_id] = True
        try:
            # Remove existing confusion markers to avoid duplicates
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get('type') != 'confusion']
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({'type': 'confusion', 'duration': 1, 'applier': applier_id})

    def consume_confusion_if_any(self, player_id: int) -> None:
        """Consume confusion (one turn), clear UI marker."""
        if self.confused_next_turn.get(player_id, False):
            self.confused_next_turn[player_id] = False
            try:
                self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get('type') != 'confusion']
            except Exception:
                logging.exception("Unexpected error")

    def is_reload_needed(self, player_id: int, attack_index: int) -> bool:
        return bool(self.manual_reload_needed.get(player_id, {}).get(attack_index, False))

    def set_reload_needed(self, player_id: int, attack_index: int, needed: bool) -> None:
        bucket = self.manual_reload_needed.setdefault(player_id, {})
        if needed:
            bucket[attack_index] = True
        else:
            bucket.pop(attack_index, None)

    def _find_effect(self, player_id: int, effect_type: str):
        for effect in self.active_effects.get(player_id, []):
            if effect.get("type") == effect_type:
                return effect
        return None

    def has_stealth(self, player_id: int) -> bool:
        return self._find_effect(player_id, "stealth") is not None

    def consume_stealth(self, player_id: int) -> bool:
        effect = self._find_effect(player_id, "stealth")
        if not effect:
            return False
        try:
            self.active_effects[player_id].remove(effect)
        except ValueError:
            pass
        return True

    def grant_stealth(self, player_id: int) -> None:
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "stealth"]
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({"type": "stealth", "duration": 1, "applier": player_id})

    def _append_effect_event(self, events: list[str], text: str) -> None:
        msg = str(text).strip()
        if msg:
            events.append(msg)

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
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "airborne"]
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({"type": "airborne", "duration": 1, "applier": player_id})

    def _clear_airborne(self, player_id: int) -> None:
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "airborne"]
        except Exception:
            logging.exception("Unexpected error")

    def queue_delayed_defense(self, player_id: int, defense: str, counter: int = 0) -> None:
        defense_mode = str(defense or "").strip().lower()
        if defense_mode not in {"evade", "stealth"}:
            return
        self.delayed_defense_queue[player_id].append(
            {
                "defense": defense_mode,
                "counter": max(0, int(counter)),
            }
        )

    def activate_delayed_defense_after_attack(self, player_id: int, effect_events: list[str]) -> None:
        queued = list(self.delayed_defense_queue.get(player_id, []))
        if not queued:
            return
        self.delayed_defense_queue[player_id] = []
        for entry in queued:
            defense_mode = entry.get("defense")
            if defense_mode == "evade":
                counter = int(entry.get("counter", 0) or 0)
                self.queue_incoming_modifier(player_id, evade=True, counter=counter, turns=1)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird ausgewichen.")
            elif defense_mode == "stealth":
                self.grant_stealth(player_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird vollständig geblockt.")

    def start_airborne_two_phase(
        self,
        player_id: int,
        landing_damage,
        effect_events: list[str],
        *,
        source_attack_index: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        if isinstance(landing_damage, list) and len(landing_damage) == 2:
            min_dmg = int(landing_damage[0])
            max_dmg = int(landing_damage[1])
        else:
            min_dmg = 20
            max_dmg = 40
        min_dmg = max(0, min_dmg)
        max_dmg = max(min_dmg, max_dmg)
        self.airborne_pending_landing[player_id] = {
            "damage": [min_dmg, max_dmg],
            "name": "Landungsschlag",
            "cooldown_attack_index": int(source_attack_index) if source_attack_index is not None else None,
            "cooldown_turns": max(0, int(cooldown_turns or 0)),
        }
        self.queue_incoming_modifier(player_id, evade=True, counter=0, turns=1)
        self._grant_airborne(player_id)
        self._append_effect_event(effect_events, "Flugphase aktiv: Der nächste gegnerische Angriff verfehlt.")

    def resolve_forced_landing_if_due(self, player_id: int, effect_events: list[str]) -> dict | None:
        pending = self.airborne_pending_landing.get(player_id)
        if not pending:
            return None
        self.airborne_pending_landing[player_id] = None
        self._clear_airborne(player_id)
        self._append_effect_event(effect_events, "Landungsschlag wurde automatisch ausgelöst.")
        damage = pending.get("damage", [20, 40])
        if isinstance(damage, list) and len(damage) == 2:
            damage_data = [int(damage[0]), int(damage[1])]
        else:
            damage_data = [20, 40]
        return {
            "name": str(pending.get("name") or "Landungsschlag"),
            "damage": damage_data,
            "info": "Automatischer Folgetreffer aus der Flugphase.",
            "cooldown_attack_index": pending.get("cooldown_attack_index"),
            "cooldown_turns": int(pending.get("cooldown_turns", 0) or 0),
        }

    def _max_hp_for(self, player_id: int) -> int:
        return self.player1_max_hp if player_id == self.player1_id else self.player2_max_hp

    def _hp_for(self, player_id: int) -> int:
        return self.player1_hp if player_id == self.player1_id else self.player2_hp

    def _set_hp_for(self, player_id: int, value: int) -> None:
        if player_id == self.player1_id:
            self.player1_hp = max(0, value)
        else:
            self.player2_hp = max(0, value)

    def heal_player(self, player_id: int, amount: int) -> int:
        if amount <= 0:
            return 0
        before = self._hp_for(player_id)
        after = min(self._max_hp_for(player_id), before + amount)
        self._set_hp_for(player_id, after)
        return after - before

    def queue_incoming_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        reflect: float = 0.0,
        store_ratio: float = 0.0,
        cap: int | None = None,
        evade: bool = False,
        counter: int = 0,
        turns: int = 1,
    ) -> None:
        if turns <= 0:
            turns = 1
        for _ in range(turns):
            self.incoming_modifiers[player_id].append(
                {
                    "percent": max(0.0, float(percent)),
                    "flat": max(0, int(flat)),
                    "reflect": max(0.0, float(reflect)),
                    "store_ratio": max(0.0, float(store_ratio)),
                    "cap": int(cap) if cap is not None else None,
                    "evade": bool(evade),
                    "counter": max(0, int(counter)),
                }
            )

    def queue_outgoing_attack_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        turns: int = 1,
    ) -> None:
        if turns <= 0:
            turns = 1
        for _ in range(turns):
            self.outgoing_attack_modifiers[player_id].append(
                {
                    "percent": max(0.0, float(percent)),
                    "flat": max(0, int(flat)),
                }
            )

    def apply_outgoing_attack_modifiers(self, attacker_id: int, raw_damage: int) -> tuple[int, int]:
        if raw_damage <= 0 or not self.outgoing_attack_modifiers.get(attacker_id):
            return max(0, int(raw_damage)), 0
        mod = self.outgoing_attack_modifiers[attacker_id].pop(0)
        return apply_outgoing_attack_modifier(
            raw_damage,
            percent=float(mod.get("percent", 0.0) or 0.0),
            flat=int(mod.get("flat", 0) or 0),
        )

    def consume_guaranteed_hit(self, player_id: int) -> bool:
        if self.guaranteed_hit_next.get(player_id, 0) <= 0:
            return False
        self.guaranteed_hit_next[player_id] -= 1
        if self.guaranteed_hit_next[player_id] < 0:
            self.guaranteed_hit_next[player_id] = 0
        return True

    def roll_attack_damage(
        self,
        attack: dict,
        base_damage,
        damage_buff: int,
        attack_multiplier: float,
        force_max_damage: bool,
        guaranteed_hit: bool,
    ) -> tuple[int, bool, int, int]:
        multi_hit = attack.get("multi_hit")
        if isinstance(multi_hit, dict):
            actual_damage, min_damage, max_damage, details = resolve_multi_hit_damage(
                multi_hit,
                buff_amount=damage_buff,
                attack_multiplier=attack_multiplier,
                force_max=force_max_damage,
                guaranteed_hit=guaranteed_hit,
                return_details=True,
            )
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
            is_critical = True
        return actual_damage, is_critical, min_damage, max_damage

    def resolve_incoming_modifiers(self, defender_id: int, raw_damage: int, ignore_evade: bool = False) -> tuple[int, int, bool, int]:
        if raw_damage <= 0 or not self.incoming_modifiers.get(defender_id):
            return raw_damage, 0, False, 0
        mod = self.incoming_modifiers[defender_id].pop(0)
        if mod.get("evade") and not ignore_evade:
            return 0, 0, True, int(mod.get("counter", 0) or 0)

        damage = max(0, int(raw_damage))
        prevented = 0

        percent = float(mod.get("percent", 0.0) or 0.0)
        if percent > 0:
            cut = int(round(damage * percent))
            damage -= cut
            prevented += cut

        flat = int(mod.get("flat", 0) or 0)
        if flat > 0:
            cut = min(flat, damage)
            damage -= cut
            prevented += cut

        cap = mod.get("cap")
        if cap is not None and damage > int(cap):
            cut = damage - int(cap)
            damage = int(cap)
            prevented += cut

        reflect_ratio = float(mod.get("reflect", 0.0) or 0.0)
        reflected = int(round(prevented * reflect_ratio)) if reflect_ratio > 0 else 0

        store_ratio = float(mod.get("store_ratio", 0.0) or 0.0)
        if store_ratio > 0 and prevented > 0:
            self.absorbed_damage[defender_id] += int(round(prevented * store_ratio))

        return max(0, damage), max(0, reflected), False, 0

    def apply_regen_tick(self, player_id: int) -> int:
        total = 0
        remove: list[dict] = []
        for effect in self.active_effects.get(player_id, []):
            if effect.get("type") != "regen":
                continue
            heal = int(effect.get("heal", 0) or 0)
            total += self.heal_player(player_id, heal)
            effect["duration"] = int(effect.get("duration", 0) or 0) - 1
            if effect["duration"] <= 0:
                remove.append(effect)
        for effect in remove:
            try:
                self.active_effects[player_id].remove(effect)
            except ValueError:
                pass
        return total

    def _status_icons(self, player_id: int) -> str:
        effects = self.active_effects.get(player_id, [])
        icons = []
        if any(e.get("type") == "burning" for e in effects):
            icons.append("\U0001f525")
        if any(e.get("type") == "confusion" for e in effects):
            icons.append("\U0001f300")
        if any(e.get("type") == "stealth" for e in effects):
            icons.append("\U0001f977")
        if any(e.get("type") == "airborne" for e in effects):
            icons.append("\u2708\ufe0f")
        return f" {' '.join(icons)}" if icons else ""

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
                self._last_log_edit_ts = time.monotonic()
                return
            except Exception as e:
                if getattr(e, "status", None) == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                logging.exception("Failed to edit battle log")
                return
    
    def is_attack_on_cooldown(self, player_id, attack_index):
        """Prüft ob eine Attacke auf Cooldown ist"""
        return self.attack_cooldowns[player_id].get(attack_index, 0) > 0
    
    def get_attack_max_damage(self, attack_damage, damage_buff=0):
        """Berechnet den maximalen Schaden einer Attacke"""
        if isinstance(attack_damage, list) and len(attack_damage) == 2:
            return attack_damage[1] + damage_buff
        else:
            return attack_damage + damage_buff

    def get_attack_min_damage(self, attack_damage, damage_buff=0):
        """Return min damage for cooldown checks."""
        if isinstance(attack_damage, list) and len(attack_damage) == 2:
            return attack_damage[0] + damage_buff
        return attack_damage + damage_buff

    def is_strong_attack(self, attack_damage, damage_buff=0):
        """Return True when attack should use cooldown."""
        min_damage = self.get_attack_min_damage(attack_damage, damage_buff)
        max_damage = self.get_attack_max_damage(attack_damage, damage_buff)
        return min_damage > 90 and max_damage > 99
    def start_attack_cooldown(self, player_id, attack_index):
        """Startet Cooldown für eine starke Attacke (2 Züge)"""
        self.attack_cooldowns[player_id][attack_index] = 2
    
    def reduce_cooldowns(self, player_id):
        """Reduziert alle Cooldowns für einen Spieler um 1"""
        for attack_index in list(self.attack_cooldowns[player_id].keys()):
            self.attack_cooldowns[player_id][attack_index] -= 1
            if self.attack_cooldowns[player_id][attack_index] <= 0:
                del self.attack_cooldowns[player_id][attack_index]
        
    async def init_with_buffs(self):
        """Lädt Health-Buffs nach der Initialisierung"""
        # Player 1 Health-Buffs
        player1_buffs = await get_card_buffs(self.player1_id, self.player1_card["name"])
        health_buff1 = 0
        for buff_type, attack_number, buff_amount in player1_buffs:
            if buff_type == "health" and attack_number == 0:  # Health hat attack_number 0
                health_buff1 += buff_amount
        
        # Player 2 Health-Buffs  
        player2_buffs = await get_card_buffs(self.player2_id, self.player2_card["name"])
        health_buff2 = 0
        for buff_type, attack_number, buff_amount in player2_buffs:
            if buff_type == "health" and attack_number == 0:
                health_buff2 += buff_amount
                
        # Finale HP-Werte mit Buffs
        self.player1_hp += health_buff1
        self.player2_hp += health_buff2
        self.player1_max_hp = self.player1_hp
        self.player2_max_hp = self.player2_hp
        
        # HP-View updaten falls vorhanden
        if self.hp_view:
            self.hp_view.update_hp(self.player1_hp)
        
        # Dynamische Attacken basierend auf aktueller Karte
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
                        btn.label = f"{blocked_name} (Cooldown: {cooldown_turns})"
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
                damage_buff = 0
                
                # Berechne Buff für diese Attacke
                for buff_type, attack_number, buff_amount in card_buffs:
                    if buff_type == "damage" and attack_number == (i + 1):
                        damage_buff += buff_amount
                
                # Berechne Schadenbereich mit Buffs
                if isinstance(base_damage, list) and len(base_damage) == 2:
                    min_dmg, max_dmg = base_damage
                    min_dmg += damage_buff
                    max_dmg += damage_buff
                    damage_text = f"{min_dmg}-{max_dmg}"
                else:
                    # Rückwärtskompatibilität
                    total_damage = base_damage + damage_buff
                    damage_text = str(total_damage)
                
                buff_text = f" (+{damage_buff})" if damage_buff > 0 else ""
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
                    button.label = f"{attack['name']} (Cooldown: {cooldown_turns})"
                    button.disabled = True
                elif is_reload_action:
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    if heal_label is not None:
                        button.style = discord.ButtonStyle.success
                        button.label = f"{attack['name']} (+{heal_label}){effects_label}"
                    else:
                        # Rot für normale Attacken
                        button.style = discord.ButtonStyle.danger
                        button.label = f"{attack['name']} ({damage_text}{buff_text}){effects_label}"
                    button.disabled = False

        # Deaktiviere restliche Buttons, falls die aktuelle Karte weniger als 4 Attacken hat
        if len(attacks) < len(attack_buttons):
            for j in range(len(attacks), len(attack_buttons)):
                btn = attack_buttons[j]
                btn.style = discord.ButtonStyle.secondary
                btn.label = "—"
                btn.disabled = True

    # Angriffs-Buttons (rot, 2x2 Grid)
    @ui.button(label="Angriff 1", style=discord.ButtonStyle.danger, row=0)
    async def attack1(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 0)

    @ui.button(label="Angriff 2", style=discord.ButtonStyle.danger, row=0)
    async def attack2(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 1)

    @ui.button(label="Angriff 3", style=discord.ButtonStyle.danger, row=1)
    async def attack3(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 2)

    @ui.button(label="Angriff 4", style=discord.ButtonStyle.danger, row=1)
    async def attack4(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 3)

    # Blaue Buttons unten
    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id in [self.player1_id, self.player2_id]:
            embed = discord.Embed(title="⚔️ Kampf abgebrochen", description="Der Kampf wurde abgebrochen.")
            await interaction.response.edit_message(embed=embed, view=None)
            try:
                allowed = {self.player1_id, self.player2_id}
                view = FightFeedbackView(interaction.channel, interaction.guild, allowed)
                await interaction.channel.send("Gab es einen Bug/Fehler?", view=view)
            except Exception:
                logging.exception("Unexpected error")
            self.stop()
        else:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)

    # Entfernt: Platzhalter-Button

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
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

        if self.stunned_next_turn.get(self.current_turn, False):
            self.stunned_next_turn[self.current_turn] = False
            await _safe_defer_interaction(interaction)
            self.current_turn = self.player2_id if self.current_turn == self.player1_id else self.player1_id
            self.reduce_cooldowns(self.current_turn)
            await self.update_attack_buttons()
            user1 = interaction.guild.get_member(self.player1_id)
            user2 = interaction.guild.get_member(self.player2_id)
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
            )
            battle_embed.description = (battle_embed.description or "") + "\n\n🛑 Der Gegner war betäubt und hat seinen Zug ausgesetzt."
            try:
                await interaction.message.edit(embed=battle_embed, view=self)
            except Exception:
                await interaction.channel.send(embed=battle_embed, view=self)
            if self.current_turn == 0:
                await self.execute_bot_attack(interaction.message)
            return

        effect_events: list[str] = []
        forced_landing_attack = self.resolve_forced_landing_if_due(self.current_turn, effect_events)
        is_forced_landing = forced_landing_attack is not None

        # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist
        if not is_forced_landing and self.is_attack_on_cooldown(self.current_turn, attack_index):
            await interaction.response.send_message("Diese Attacke ist noch auf Cooldown!", ephemeral=True)
            return

        if not is_forced_landing and self.special_lock_next_turn.get(self.current_turn, False) and attack_index != 0:
            await interaction.response.send_message(
                "Diese Runde sind nur Standard-Angriffe erlaubt (Attacke 1).",
                ephemeral=True,
            )
            return

        # Bestimme Angreifer und Verteidiger zuerst
        if self.current_turn == self.player1_id:
            attacker_card = self.player1_card["name"]
            defender_card = self.player2_card["name"]
            attacker_user = interaction.guild.get_member(self.player1_id)
            defender_user = interaction.guild.get_member(self.player2_id)
            defender_id = self.player2_id
        else:
            attacker_card = self.player2_card["name"]
            defender_card = self.player1_card["name"]
            attacker_user = interaction.guild.get_member(self.player2_id)
            defender_user = interaction.guild.get_member(self.player1_id)
            defender_id = self.player1_id

        # Regeneration tickt beim Start des eigenen Zuges
        regen_heal = self.apply_regen_tick(self.current_turn)
        if regen_heal > 0:
            self._append_effect_event(effect_events, f"Regeneration heilt {regen_heal} HP.")

        # SIDE EFFECTS: Apply effects on defender before attack
        effects_to_remove = []
        pre_burn_total = 0
        for effect in self.active_effects[defender_id]:
            if effect['applier'] == self.current_turn and effect['type'] == 'burning':
                damage = effect['damage']
                if defender_id == self.player1_id:
                    self.player1_hp -= damage
                else:
                    self.player2_hp -= damage
                self.player1_hp = max(0, self.player1_hp)
                self.player2_hp = max(0, self.player2_hp)
                pre_burn_total += damage

                # Decrease duration
                effect['duration'] -= 1
                if effect['duration'] <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        # Hole aktuelle Karte und Angriff
        current_card = self.player1_card if self.current_turn == self.player1_id else self.player2_card
        attacks = current_card.get("attacks", [{"name": "Punch", "damage": [15, 25]}])
        if (not is_forced_landing) and attack_index >= len(attacks):
            await interaction.response.send_message("Ungültiger Angriff!", ephemeral=True)
            return
        # Vor DB/weiterer Logik früh defern, damit Interaction nicht abläuft.
        await _safe_defer_interaction(interaction)
        damage_buff = 0
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
                    damage_buff += buff_amount

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
            if isinstance(damage_if_condition, list) and len(damage_if_condition) == 2:
                base_damage = [int(damage_if_condition[0]), int(damage_if_condition[1])]
            elif isinstance(damage_if_condition, int):
                base_damage = [damage_if_condition, damage_if_condition]

        if attack.get("add_absorbed_damage"):
            damage_buff += int(self.absorbed_damage.get(self.current_turn, 0))
            self.absorbed_damage[self.current_turn] = 0

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
        if is_reload_action:
            actual_damage = 0
            is_critical = False
            attack_hits_enemy = False
            self.set_reload_needed(self.current_turn, attack_index, False)
        else:
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
                    if self.current_turn == self.player1_id:
                        self.player1_hp -= self_damage
                    else:
                        self.player2_hp -= self_damage
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
                    elif self.current_turn == self.player1_id:
                        self.player2_hp -= actual_damage
                    else:
                        self.player1_hp -= actual_damage
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
                elif self.current_turn == self.player1_id:
                    self.player2_hp -= actual_damage
                else:
                    self.player1_hp -= actual_damage

            if attack_hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                reduced_damage, overflow_self_damage = self.apply_outgoing_attack_modifiers(self.current_turn, actual_damage)
                if reduced_damage != actual_damage:
                    delta_out = actual_damage - reduced_damage
                    if delta_out > 0:
                        if self.current_turn == self.player1_id:
                            self.player2_hp += delta_out
                        else:
                            self.player1_hp += delta_out
                    actual_damage = reduced_damage
                    self._append_effect_event(effect_events, f"Ausgehender Schaden wurde um {delta_out} reduziert.")
                if overflow_self_damage > 0:
                    if self.current_turn == self.player1_id:
                        self.player1_hp -= overflow_self_damage
                    else:
                        self.player2_hp -= overflow_self_damage
                    self._append_effect_event(effect_events, f"Überlauf-Rückstoß: {overflow_self_damage} Selbstschaden.")

                final_damage, reflected_damage, dodged, counter_damage = self.resolve_incoming_modifiers(
                    defender_id,
                    actual_damage,
                    ignore_evade=guaranteed_hit,
                )
                if dodged:
                    if self.current_turn == self.player1_id:
                        self.player2_hp += actual_damage
                    else:
                        self.player1_hp += actual_damage
                    actual_damage = 0
                    attack_hits_enemy = False
                elif final_damage != actual_damage:
                    delta = actual_damage - final_damage
                    if delta > 0:
                        if self.current_turn == self.player1_id:
                            self.player2_hp += delta
                        else:
                            self.player1_hp += delta
                    actual_damage = final_damage
                if reflected_damage > 0:
                    if self.current_turn == self.player1_id:
                        self.player1_hp -= reflected_damage
                    else:
                        self.player2_hp -= reflected_damage
                if counter_damage > 0:
                    if self.current_turn == self.player1_id:
                        self.player1_hp -= counter_damage
                    else:
                        self.player2_hp -= counter_damage

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            if self.current_turn == self.player1_id:
                self.player1_hp -= self_damage_value
            else:
                self.player2_hp -= self_damage_value
            self._append_effect_event(effect_events, f"Rückstoß: {self_damage_value} Selbstschaden.")

        heal_data = attack.get("heal")
        if heal_data is not None:
            if isinstance(heal_data, list) and len(heal_data) == 2:
                heal_amount = random.randint(int(heal_data[0]), int(heal_data[1]))
            else:
                heal_amount = int(heal_data)
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
            self.activate_delayed_defense_after_attack(self.current_turn, effect_events)

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
                duration = random.randint(effect["duration"][0], effect["duration"][1])
                new_effect = {
                    'type': 'burning',
                    'duration': duration,
                    'damage': effect['damage'],
                    'applier': self.current_turn
                }
                self.active_effects[target_id].append(new_effect)
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {effect['damage']} Schaden für {duration} Runden.")
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
                self._append_effect_event(effect_events, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e).")
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, f"Nächster Angriff macht +{pct}% Schaden.")
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff trifft garantiert.")
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n)).")
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, f"Block-Sequenz vorbereitet: {seq_text}.")
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n)).")
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden.")
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß).")
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1)
                self._append_effect_event(effect_events, "Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen.")
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1)
                self._append_effect_event(effect_events, "Absorption aktiv: Verhinderter Schaden wird gespeichert.")
            elif eff_type == "cap_damage":
                max_damage = int(effect.get("max_damage", 0) or 0)
                self.queue_incoming_modifier(target_id, cap=max_damage, turns=1)
                self._append_effect_event(effect_events, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer.")
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1)
                self._append_effect_event(effect_events, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt.")
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
                if isinstance(heal_data_effect, list) and len(heal_data_effect) == 2:
                    heal_amount = random.randint(int(heal_data_effect[0]), int(heal_data_effect[1]))
                else:
                    heal_amount = int(heal_data_effect or 0)
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
                self.queue_delayed_defense(target_id, defense_mode, counter=counter)
                self._append_effect_event(effect_events, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv.")
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

        # Prüfen ob Kampf vorbei
        if self.player1_hp <= 0 or self.player2_hp <= 0:
            if self.player2_hp <= 0:
                winner_id = self.player1_id
                winner_user = interaction.guild.get_member(self.player1_id)
                winner_card = self.player1_card["name"]
            else:
                winner_id = self.player2_id
                winner_user = interaction.guild.get_member(self.player2_id)
                winner_card = self.player2_card["name"]
            if winner_user:
                winner_mention = winner_user.mention
            else:
                winner_mention = "Bot" if winner_id == 0 else f"<@{winner_id}>"
            winner_embed = discord.Embed(title="🏆 Sieger!", description=f"**{winner_mention} mit {winner_card}** hat gewonnen!")
            
            # Aktualisiere nur die Kampf-Nachricht zum Sieger-Embed
            try:
                await interaction.message.edit(embed=winner_embed, view=None)
            except Exception:
                await interaction.channel.send(embed=winner_embed)
            # Feedback nach jedem Kampf anbieten
            try:
                allowed = {self.player1_id, self.player2_id}
                view = FightFeedbackView(interaction.channel, interaction.guild, allowed)
                await interaction.channel.send("Gab es einen Bug/Fehler?", view=view)
            except Exception:
                logging.exception("Unexpected error")
            self.stop()
            return
        
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
        if self.battle_log_message:
            defender_remaining_hp = self.player2_hp if self.current_turn == self.player1_id else self.player1_hp
            log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
            log_embed = update_battle_log(
                log_embed,
                attacker_card,
                defender_card,
                attack_name,
                actual_damage,
                is_critical,
                attacker_user,
                defender_user,
                self.round_counter,
                defender_remaining_hp,
                pre_effect_damage=pre_burn_total,
                confusion_applied=confusion_applied,
                self_hit_damage=(self_damage if not attack_hits_enemy and 'self_damage' in locals() else 0),
                attacker_status_icons=self._status_icons(self.current_turn),
                defender_status_icons=self._status_icons(defender_id),
                effect_events=effect_events,
            )
            await self._safe_edit_battle_log(log_embed)

        # Nächster Spieler
        previous_turn = self.current_turn
        self.current_turn = self.player2_id if self.current_turn == self.player1_id else self.player1_id
        
        # COOLDOWN-SYSTEM: Reduziere Cooldowns am START des neuen Zugs
        self.reduce_cooldowns(self.current_turn)
        
        # Attacken-Buttons für den neuen Spieler aktualisieren
        await self.update_attack_buttons()
        
        # Neues Kampf-Embed erstellen
        user1 = interaction.guild.get_member(self.player1_id)
        user2 = interaction.guild.get_member(self.player2_id)
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
        )
        
        # Aktualisiere Kampf-UI (Kampf-Log wurde bereits oben behandelt)
        try:
            await interaction.message.edit(embed=battle_embed, view=self)
        except Exception:
            await interaction.channel.send(embed=battle_embed, view=self)
        
        # BOT-ANGRIFF: Wenn der Bot an der Reihe ist, führe automatischen Angriff aus
        if self.current_turn == 0:  # Bot ist an der Reihe
            await self.execute_bot_attack(interaction.message)

    async def execute_bot_attack(self, message):
        """Führt einen automatischen Bot-Angriff aus"""
        # SIDE EFFECTS: Apply effects on player before bot attack
        effect_events: list[str] = []
        defender_id = self.player1_id
        effects_to_remove = []
        pre_burn_total = 0
        for effect in self.active_effects[defender_id]:
            if effect['applier'] == 0 and effect['type'] == 'burning':  # Bot applier is 0
                damage = effect['damage']
                self.player1_hp -= damage
                self.player1_hp = max(0, self.player1_hp)
                pre_burn_total += damage

                # Kein separater Burn-Log – wird inline in der folgenden Attacke gezeigt

                # Decrease duration
                effect['duration'] -= 1
                if effect['duration'] <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        if self.stunned_next_turn.get(0, False):
            self.stunned_next_turn[0] = False
            self.current_turn = self.player1_id
            self.reduce_cooldowns(self.player1_id)
            await self.update_attack_buttons()
            player_user = message.guild.get_member(self.player1_id)

            class BotUser:
                def __init__(self):
                    self.id = 0
                    self.display_name = "Bot"
                    self.mention = "**Bot**"

            bot_user = BotUser()
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
            # Wähle die stärkste verfügbare Attacke (nicht auf Cooldown)
            available_attacks = []
            attack_damages = []

            for i, attack in enumerate(attacks[:4]):
                if self.special_lock_next_turn.get(0, False) and i != 0:
                    continue
                if not self.is_attack_on_cooldown(0, i):  # Bot ID ist 0
                    if attack.get("requires_reload") and self.is_reload_needed(0, i):
                        max_damage = 0
                    else:
                        base_damage = attack["damage"]
                        if isinstance(base_damage, list) and len(base_damage) == 2:
                            max_damage = base_damage[1]  # Höchster Schaden
                        else:
                            max_damage = base_damage

                    available_attacks.append(i)
                    attack_damages.append(max_damage)

            if not available_attacks:
                # Alle Attacken auf Cooldown - wähle trotzdem die stärkste
                attack_damages = []
                for i, attack in enumerate(attacks[:4]):
                    if self.special_lock_next_turn.get(0, False) and i != 0:
                        attack_damages.append(-1)
                        continue
                    if attack.get("requires_reload") and self.is_reload_needed(0, i):
                        max_damage = 0
                    else:
                        base_damage = attack["damage"]
                        if isinstance(base_damage, list) and len(base_damage) == 2:
                            max_damage = base_damage[1]
                        else:
                            max_damage = base_damage
                    attack_damages.append(max_damage)

                # Wähle die stärkste Attacke
                max_damage_index = attack_damages.index(max(attack_damages))
                attack_index = max_damage_index
            else:
                # Wähle die stärkste verfügbare Attacke
                max_damage_index = attack_damages.index(max(attack_damages))
                attack_index = available_attacks[max_damage_index]

            # Führe Bot-Angriff aus (simuliere execute_attack Logik)
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
            if isinstance(damage_if_condition, list) and len(damage_if_condition) == 2:
                base_damage = [int(damage_if_condition[0]), int(damage_if_condition[1])]
            elif isinstance(damage_if_condition, int):
                base_damage = [damage_if_condition, damage_if_condition]
        if attack.get("add_absorbed_damage"):
            damage_buff += int(self.absorbed_damage.get(0, 0))
            self.absorbed_damage[0] = 0
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
        if is_reload_action:
            actual_damage, is_critical = 0, False
            bot_hits_enemy = False
            self.set_reload_needed(0, attack_index, False)
        else:
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
                    self.player2_hp -= self_damage
                    self.player2_hp = max(0, self.player2_hp)
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
                    else:
                        self.player1_hp -= actual_damage
                        self.player1_hp = max(0, self.player1_hp)
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
                else:
                    # Wende Schaden an
                    self.player1_hp -= actual_damage
                    self.player1_hp = max(0, self.player1_hp)

            if bot_hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                reduced_damage, overflow_self_damage = self.apply_outgoing_attack_modifiers(0, actual_damage)
                if reduced_damage != actual_damage:
                    delta_out = actual_damage - reduced_damage
                    if delta_out > 0:
                        self.player1_hp += delta_out
                    actual_damage = reduced_damage
                    self._append_effect_event(effect_events, f"Ausgehender Schaden wurde um {delta_out} reduziert.")
                if overflow_self_damage > 0:
                    self.player2_hp -= overflow_self_damage
                    self._append_effect_event(effect_events, f"Überlauf-Rückstoß: {overflow_self_damage} Selbstschaden.")

                final_damage, reflected_damage, dodged, counter_damage = self.resolve_incoming_modifiers(
                    self.player1_id,
                    actual_damage,
                    ignore_evade=guaranteed_hit,
                )
                if dodged:
                    self.player1_hp += actual_damage
                    actual_damage = 0
                    bot_hits_enemy = False
                elif final_damage != actual_damage:
                    delta = actual_damage - final_damage
                    if delta > 0:
                        self.player1_hp += delta
                    actual_damage = final_damage
                if reflected_damage > 0:
                    self.player2_hp -= reflected_damage
                if counter_damage > 0:
                    self.player2_hp -= counter_damage

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            self.player2_hp -= self_damage_value
            self._append_effect_event(effect_events, f"Rückstoß: {self_damage_value} Selbstschaden.")

        heal_data = attack.get("heal")
        if heal_data is not None:
            if isinstance(heal_data, list) and len(heal_data) == 2:
                heal_amount = random.randint(int(heal_data[0]), int(heal_data[1]))
            else:
                heal_amount = int(heal_data)
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
        class BotUser:
            def __init__(self):
                self.display_name = "Bot"
                self.mention = "**Bot**"

        bot_user = BotUser()
        player_user = message.guild.get_member(self.player1_id)

        if not is_reload_action:
            self.activate_delayed_defense_after_attack(0, effect_events)

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
                duration = random.randint(effect["duration"][0], effect["duration"][1])
                new_effect = {
                    'type': 'burning',
                    'duration': duration,
                    'damage': effect['damage'],
                    'applier': 0
                }
                self.active_effects[target_id].append(new_effect)
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {effect['damage']} Schaden für {duration} Runden.")
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
                self._append_effect_event(effect_events, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e).")
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, f"Nächster Angriff macht +{pct}% Schaden.")
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff trifft garantiert.")
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n)).")
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, f"Block-Sequenz vorbereitet: {seq_text}.")
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n)).")
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden.")
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß).")
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1)
                self._append_effect_event(effect_events, "Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen.")
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1)
                self._append_effect_event(effect_events, "Absorption aktiv: Verhinderter Schaden wird gespeichert.")
            elif eff_type == "cap_damage":
                max_damage = int(effect.get("max_damage", 0) or 0)
                self.queue_incoming_modifier(target_id, cap=max_damage, turns=1)
                self._append_effect_event(effect_events, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer.")
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1)
                self._append_effect_event(effect_events, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt.")
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
                if isinstance(heal_data_effect, list) and len(heal_data_effect) == 2:
                    heal_amount = random.randint(int(heal_data_effect[0]), int(heal_data_effect[1]))
                else:
                    heal_amount = int(heal_data_effect or 0)
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
                self.queue_delayed_defense(target_id, defense_mode, counter=counter)
                self._append_effect_event(effect_events, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv.")
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(
                    target_id,
                    effect.get("landing_damage", [20, 40]),
                    effect_events,
                    source_attack_index=attack_index if not is_forced_landing else None,
                    cooldown_turns=int(attack.get("cooldown_turns", 0) or 0),
                )
        # Kein separater Log-Eintrag – Effekte werden inline in der Angriffszeile angezeigt

        if self.battle_log_message:
            log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
            log_embed = update_battle_log(
                log_embed,
                bot_card["name"],
                self.player1_card["name"],
                attack_name,
                actual_damage,
                is_critical,
                bot_user,
                player_user,
                self.round_counter,
                self.player1_hp,
                pre_effect_damage=pre_burn_total,
                confusion_applied=False,
                self_hit_damage=(self_damage if not bot_hits_enemy and 'self_damage' in locals() else 0),
                attacker_status_icons=self._status_icons(0),
                defender_status_icons=self._status_icons(self.player1_id),
                effect_events=effect_events,
            )
            await self._safe_edit_battle_log(log_embed)

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
                winner_user = message.guild.get_member(self.player1_id)
                winner_card = self.player1_card["name"]
            else:
                winner_id = self.player2_id
                winner_user = message.guild.get_member(self.player2_id)
                winner_card = self.player2_card["name"]
            if winner_user:
                winner_mention = winner_user.mention
            else:
                winner_mention = "Bot" if winner_id == 0 else f"<@{winner_id}>"
            winner_embed = discord.Embed(title="🏆 Sieger!", description=f"**{winner_mention} mit {winner_card}** hat gewonnen!")
            await message.edit(embed=winner_embed, view=None)
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

# Neue Suchfunktion-Klassen
class UserSearchModal(RestrictedModal):
    def __init__(self, guild, challenger, parent_view: ui.View | None = None, include_bot_option: bool = True):
        super().__init__(title="🔍 User suchen")
        self.guild = guild
        self.challenger = challenger
        self.parent_view = parent_view
        self.include_bot_option = include_bot_option
    
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
            if (not member.bot and 
                member != self.challenger and 
                (search_term in member.display_name.lower() or 
                 search_term in member.name.lower())):
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
                    label=f"{status_emoji} {member.display_name}",
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
    def __init__(self, challenger, options, parent_view: ui.View | None = None):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.value = None
        self.parent_view = parent_view
        
        self.select = ui.Select(placeholder="Wähle einen Gegner aus den Suchergebnissen...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user != self.challenger:
            await interaction.response.send_message("Nur der Herausforderer kann den Gegner wählen!", ephemeral=True)
            return
        
        self.value = self.select.values[0]
        # Übergib die Auswahl zurück an die Eltern-View (z. B. OpponentSelectView/AdminUserSelectView) und beende sie
        if self.parent_view is not None:
            try:
                self.parent_view.value = self.value
                self.parent_view.stop()
            except Exception:
                logging.exception("Unexpected error")
class ShowAllMembersPager(ui.View):
    def __init__(self, requester, members: list[discord.Member], parent_view: ui.View | None = None, include_bot_option: bool = False):
        super().__init__(timeout=120)
        self.requester = requester
        self.parent_view = parent_view
        self.include_bot_option = include_bot_option
        # Nur Nicht-Bots
        self.members = [m for m in members if not m.bot]

        # Präsenz-Sortierung: grün > orange > rot > schwarz
        def presence_priority(m: discord.Member) -> int:
            s = m.status
            if s == discord.Status.online:
                return 0
            if s == discord.Status.idle:
                return 1
            if s == discord.Status.dnd:
                return 2
            return 3

        self.sorted_members = sorted(self.members, key=presence_priority)

        # Seiten vorbereiten (erste Seite ggf. 1 Slot für Bot reservieren)
        self.pages: list[list[discord.Member]] = []
        remaining = list(self.sorted_members)
        first_cap = 24 if self.include_bot_option else 25
        if remaining or self.include_bot_option:
            self.pages.append(remaining[:first_cap])
            remaining = remaining[first_cap:]
        while remaining:
            self.pages.append(remaining[:25])
            remaining = remaining[25:]
        if not self.pages:
            self.pages = [[]]
        self.page_index = 0

        # Select
        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options_for_current_page()
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        # Navigation
        self.prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=True)
        self.next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=(len(self.pages) <= 1))
        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def _status_circle(self, member: discord.Member) -> str:
        s = member.status
        if s == discord.Status.online:
            return "🟢"
        if s == discord.Status.idle:
            return "🟡"
        if s == discord.Status.dnd:
            return "🔴"
        return "⚫"

    def _placeholder(self) -> str:
        return f"Seite {self.page_index+1}/{len(self.pages)} – Nutzer wählen..."

    def _build_options_for_current_page(self) -> list[SelectOption]:
        options: list[SelectOption] = []
        if self.include_bot_option and self.page_index == 0:
            options.append(SelectOption(label="🤖 Bot", value="bot"))
        for m in self.pages[self.page_index]:
            options.append(SelectOption(label=f"{self._status_circle(m)} {m.display_name[:100]}", value=str(m.id)))
        if not options:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="none"))
        return options

    async def _on_select(self, interaction: discord.Interaction):
        # Nur ursprünglicher Nutzer darf auswählen
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return

        choice = self.select.values[0]
        if choice == "none":
            await interaction.response.send_message("❌ Keine Nutzer verfügbar!", ephemeral=True)
            return

        if self.parent_view is not None:
            try:
                self.parent_view.value = choice
                self.parent_view.stop()
            except Exception:
                logging.exception("Unexpected error")
        self.stop()
        await interaction.response.defer()

    async def _on_prev(self, interaction: discord.Interaction):
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
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
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
            await interaction.response.edit_message(view=self)
        self.stop()
        await interaction.response.defer()

class ShowAllMembersPager(ui.View):
    def __init__(self, requester, members: list[discord.Member], parent_view: ui.View | None = None, include_bot_option: bool = False):
        super().__init__(timeout=120)
        self.requester = requester
        self.parent_view = parent_view
        self.include_bot_option = include_bot_option
        # Nur Nicht-Bots
        self.members = [m for m in members if not m.bot]
        # Präsenz-Sortierung: grün > orange > rot > schwarz
        def presence_priority(m: discord.Member) -> int:
            s = m.status
            if s == discord.Status.online:
                return 0
            if s == discord.Status.idle:
                return 1
            if s == discord.Status.dnd:
                return 2
            return 3
        self.sorted_members = sorted(self.members, key=presence_priority)
        # Seiten vorbereiten (erste Seite ggf. 1 Slot für Bot reservieren)
        self.pages: list[list[discord.Member]] = []
        remaining = list(self.sorted_members)
        first_cap = 24 if self.include_bot_option else 25
        if remaining or self.include_bot_option:
            self.pages.append(remaining[:first_cap])
            remaining = remaining[first_cap:]
        while remaining:
            self.pages.append(remaining[:25])
            remaining = remaining[25:]
        if not self.pages:
            self.pages = [[]]
        self.page_index = 0

        # Select
        self.select = ui.Select(placeholder=self._placeholder(), min_values=1, max_values=1, options=self._build_options_for_current_page())
        self.select.callback = self._on_select
        self.add_item(self.select)

        # Navigation
        self.prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=True)
        self.next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=(len(self.pages) <= 1))
        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def _status_circle(self, member: discord.Member) -> str:
        s = member.status
        if s == discord.Status.online:
            return "🟢"
        if s == discord.Status.idle:
            return "🟡"
        if s == discord.Status.dnd:
            return "🔴"
        return "⚫"

    def _placeholder(self) -> str:
        return f"Seite {self.page_index+1}/{len(self.pages)} – Nutzer wählen..."

    def _build_options_for_current_page(self) -> list[SelectOption]:
        options: list[SelectOption] = []
        if self.include_bot_option and self.page_index == 0:
            options.append(SelectOption(label="🤖 Bot", value="bot"))
        for m in self.pages[self.page_index]:
            options.append(SelectOption(label=f"{self._status_circle(m)} {m.display_name[:100]}", value=str(m.id)))
        if not options:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="none"))
        return options

    async def _on_select(self, interaction: discord.Interaction):
        # Nur ursprünglicher Nutzer darf auswählen
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return

        choice = self.select.values[0]
        if choice == "none":
            await interaction.response.send_message("❌ Keine Nutzer verfügbar!", ephemeral=True)
            return

        if self.parent_view is not None:
            try:
                self.parent_view.value = choice
                self.parent_view.stop()
            except Exception:
                logging.exception("Unexpected error")
        self.stop()
        await interaction.response.defer()

    async def _on_prev(self, interaction: discord.Interaction):
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
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
        try:
            req_id = self.requester.id
        except AttributeError:
            req_id = int(self.requester)
        if interaction.user.id != req_id:
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = (self.page_index == 0)
            self.next_btn.disabled = (self.page_index == len(self.pages) - 1)
            await interaction.response.edit_message(view=self)

class OpponentSelectView(RestrictedView):
    def __init__(self, challenger: discord.Member, guild: discord.Guild):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.guild = guild
        self.value = None
        self.all_members = []
        
        # Sammle alle verfügbaren Mitglieder
        for member in guild.members:
            if not member.bot and member != challenger:
                self.all_members.append(member)
        
        # Zeige intelligente Auswahl
        self.show_smart_options()
    
    def show_smart_options(self):
        """Zeigt intelligente Optionen basierend auf Server-Größe (mit Status-Kreisen und Präsenz-Sortierung)"""
        def presence_priority(m: discord.Member) -> int:
            s = m.status
            if s == discord.Status.online:
                return 0
            if s == discord.Status.idle:
                return 1
            if s == discord.Status.dnd:
                return 2
            return 3  # offline/unknown
        
        def label_with_circle(m: discord.Member) -> str:
            # identische Kreise wie in der Suche: 🟢 🟡 🔴 ⚫
            if m.status == discord.Status.online:
                return f"🟢 {m.display_name}"
            if m.status == discord.Status.idle:
                return f"🟡 {m.display_name}"
            if m.status == discord.Status.dnd:
                return f"🔴 {m.display_name}"
            return f"⚫ {m.display_name}"
        
        options = [SelectOption(label="🤖 Bot", value="bot")]
        
        if len(self.all_members) <= 24:
            # Kleiner Server: Zeige alle nach Präsenz sortiert
            for member in sorted(self.all_members, key=presence_priority):
                options.append(SelectOption(label=label_with_circle(member), value=str(member.id)))
        else:
            # Großer Server: Zeige nach Präsenz sortierte Online/Idle/DnD-User
            online_like = [m for m in self.all_members if m.status != discord.Status.offline]
            for member in sorted(online_like, key=presence_priority)[:22]:
                options.append(SelectOption(label=label_with_circle(member), value=str(member.id)))
            # Steuer-Optionen
            options.append(SelectOption(label="🔍 Nach Name suchen", value="search"))
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
            modal = UserSearchModal(self.guild, self.challenger, parent_view=self)
            await interaction.response.send_modal(modal)
            return
        
        elif selected_value == "show_all":
            # Zeige alle User (mit Paginierung falls nötig)
            if len(self.all_members) <= 25:
                def presence_priority(m: discord.Member) -> int:
                    s = m.status
                    if s == discord.Status.online:
                        return 0
                    if s == discord.Status.idle:
                        return 1
                    if s == discord.Status.dnd:
                        return 2
                    return 3  # offline/unknown
                options = [SelectOption(label="🤖 Bot", value="bot")]
                for member in sorted(self.all_members, key=presence_priority):
                    status_emoji = self.get_status_emoji(member)
                    options.append(SelectOption(
                        label=f"{status_emoji} {member.display_name}",
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
        # Präsenz-Priorität für Sortierung: grün > orange > rot > schwarz
        def presence_priority(m: discord.Member) -> int:
            s = m.status
            if s == discord.Status.online:
                return 0
            if s == discord.Status.idle:
                return 1
            if s == discord.Status.dnd:
                return 2
            return 3

        def status_circle(m: discord.Member) -> str:
            s = m.status
            if s == discord.Status.online:
                return "🟢"
            if s == discord.Status.idle:
                return "🟡"
            if s == discord.Status.dnd:
                return "🔴"
            return "⚫"

        options: list[SelectOption] = []
        members_sorted = sorted(self.all_members, key=presence_priority)

        if not members_sorted:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="none"))
        elif len(members_sorted) <= 24:
            # Bis 24 User: alle anzeigen + Suchoption (max. 25 Optionen)
            for member in members_sorted:
                circle = status_circle(member)
                label = f"{circle} {member.display_name[:100]}"
                options.append(SelectOption(label=label, value=str(member.id)))
            options.append(SelectOption(label="🔍 Nach Name suchen", value="search"))
        else:
            # Größerer Server: kompakte Liste + Such-/Alle-Optionen (max. 25)
            for member in members_sorted[:23]:
                circle = status_circle(member)
                label = f"{circle} {member.display_name[:100]}"
                options.append(SelectOption(label=label, value=str(member.id)))
            options.append(SelectOption(label="🔍 Nach Name suchen", value="search"))
            options.append(SelectOption(label="📋 Alle User anzeigen", value="show_all"))

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
                # Präsenz-sorted und mit Status-Kreis
                def presence_priority(m: discord.Member) -> int:
                    s = m.status
                    if s == discord.Status.online:
                        return 0
                    if s == discord.Status.idle:
                        return 1
                    if s == discord.Status.dnd:
                        return 2
                    return 3
                def status_circle(m: discord.Member) -> str:
                    s = m.status
                    if s == discord.Status.online:
                        return "🟢"
                    if s == discord.Status.idle:
                        return "🟡"
                    if s == discord.Status.dnd:
                        return "🔴"
                    return "⚫"
                members_sorted = sorted(self.all_members, key=presence_priority)
                options = [
                    SelectOption(label=f"{status_circle(m)} {m.display_name[:100]}", value=str(m.id))
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

class FightVisibilityView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value: bool | None = None  # True=privat, False=öffentlich, None=abgebrochen

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

async def _maybe_delete_fight_thread(thread_id: int | None, thread_created: bool) -> None:
    if not thread_created or not thread_id:
        return
    try:
        channel = bot.get_channel(thread_id)
        if channel is None:
            channel = await bot.fetch_channel(thread_id)
        if isinstance(channel, discord.Thread):
            await channel.delete()
    except Exception:
        logging.exception("Unexpected error")

async def _start_fight_from_challenge(
    interaction: discord.Interaction,
    *,
    challenger_id: int,
    challenged_id: int,
    challenger_card_name: str,
    thread_id: int | None,
    thread_created: bool,
) -> None:
    if interaction.guild is None:
        await interaction.followup.send("❌ Nur in Servern verfügbar.", ephemeral=True)
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
    gegner_karten_liste = await get_user_karten(challenged.id)
    if not gegner_karten_liste:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"❌ {challenged.mention} hat keine Karten! Kampf abgebrochen.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    gegner_card_select_view = CardSelectView(challenged.id, gegner_karten_liste, 1)
    if await _safe_send_channel(
        interaction,
        interaction.channel,
        content=f"{challenged.mention}, wähle deine Karte für den 1v1 Kampf:",
        view=gegner_card_select_view,
    ) is None:
        return
    await gegner_card_select_view.wait()
    if not gegner_card_select_view.value:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"{challenged.mention} hat keine Karte gewählt. Kampf abgebrochen.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    gegner_selected_names = gegner_card_select_view.value
    gegner_selected_cards = [await get_karte_by_name(name) for name in gegner_selected_names]
    if not gegner_selected_cards or not gegner_selected_cards[0]:
        await _safe_send_channel(
            interaction,
            interaction.channel,
            content=f"❌ Karte von {challenged.mention} nicht gefunden. Kampf abgebrochen.",
        )
        await _maybe_delete_fight_thread(thread_id, thread_created)
        return
    battle_view = BattleView(challenger_card, gegner_selected_cards[0], challenger.id, challenged.id, None)
    await battle_view.init_with_buffs()
    log_embed = create_battle_log_embed()
    battle_log_message = await _safe_send_channel(interaction, interaction.channel, embed=log_embed)
    if battle_log_message is None:
        return
    battle_view.battle_log_message = battle_log_message
    embed = create_battle_embed(
        challenger_card,
        gegner_selected_cards[0],
        battle_view.player1_hp,
        battle_view.player2_hp,
        challenger.id,
        challenger,
        challenged,
        current_attack_infos=_build_attack_info_lines(challenger_card),
    )
    await _safe_send_channel(interaction, interaction.channel, embed=embed, view=battle_view)

class ChallengeResponseView(RestrictedView):
    def __init__(
        self,
        challenger_id: int,
        challenged_id: int,
        challenger_card_name: str,
        *,
        request_id: int,
        thread_id: int | None,
        thread_created: bool,
    ):
        super().__init__(timeout=None)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.challenger_card_name = challenger_card_name
        self.request_id = request_id
        self.thread_id = thread_id
        self.thread_created = thread_created
    @ui.button(label="Kämpfen", style=discord.ButtonStyle.success)
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
        await _start_fight_from_challenge(
            interaction,
            challenger_id=self.challenger_id,
            challenged_id=self.challenged_id,
            challenger_card_name=self.challenger_card_name,
            thread_id=self.thread_id,
            thread_created=self.thread_created,
        )
        self.stop()
    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Nur der Herausgeforderte kann ablehnen!", ephemeral=True)
            return
        if not await claim_fight_request(self.request_id, "declined"):
            await interaction.response.send_message("❌ Diese Kampf-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        await interaction.response.send_message("Kampf abgelehnt.", ephemeral=True)
        try:
            await interaction.channel.send(
                content=f"<@{self.challenger_id}>, {interaction.user.mention} hat den Kampf abgelehnt."
            )
        except Exception:
            logging.exception("Unexpected error")
        await _maybe_delete_fight_thread(self.thread_id, self.thread_created)
        self.stop()

class AdminCloseView(RestrictedView):
    def __init__(self, thread: discord.Thread):
        super().__init__(timeout=3600)
        self.thread = thread

    @ui.button(label="Thread schließen (Admin/Owner)", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await is_admin(interaction):
            await interaction.response.send_message("❌ Keine Berechtigung zum Schließen.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Thread wird geschlossen...", ephemeral=True)
        self.stop()
        try:
            await self.thread.delete()
        except Exception:
            logging.exception("Unexpected error")

class BugReportLinkView(RestrictedView):
    def __init__(self):
        super().__init__(timeout=300)
        if BUG_REPORT_TALLY_URL:
            self.add_item(ui.Button(label="Formular öffnen", style=discord.ButtonStyle.link, url=BUG_REPORT_TALLY_URL))

class FightFeedbackView(RestrictedView):
    def __init__(self, channel, guild: discord.Guild, allowed_user_ids: set[int]):
        super().__init__(timeout=600)  # 10 minutes timeout
        self.channel = channel
        self.guild = guild
        self.allowed_user_ids = allowed_user_ids

    @ui.button(label="Es gab einen Bug", style=discord.ButtonStyle.success)
    async def yes_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in self.allowed_user_ids and not await is_admin(interaction):
            await interaction.response.send_message("Nur Teilnehmer oder Admins können antworten.", ephemeral=True)
            return
        if not BUG_REPORT_TALLY_URL or "REPLACE_ME" in BUG_REPORT_TALLY_URL:
            await interaction.response.send_message("❌ Bug-Formular ist noch nicht konfiguriert.", ephemeral=True)
            return

        await interaction.response.send_message(
            content="🐞 Danke! Bitte fülle dieses Formular aus:",
            view=BugReportLinkView(),
            ephemeral=True,
        )

        try:
            await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Unexpected error")

        try:
            if isinstance(self.channel, discord.Thread):
                await self.channel.send("Ein Admin/Owner kann den Thread jetzt schließen.", view=AdminCloseView(self.channel))
        except Exception:
            logging.exception("Unexpected error")

        self.stop()

    @ui.button(label="Nein", style=discord.ButtonStyle.danger)
    async def no_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in self.allowed_user_ids and not await is_admin(interaction):
            await interaction.response.send_message("Nur Teilnehmer oder Admins können antworten.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Danke!", ephemeral=True)

        try:
            await interaction.message.edit(view=None)
        except Exception:
            logging.exception("Unexpected error")

        self.stop()
        try:
            if isinstance(self.channel, discord.Thread):
                await self.channel.delete()
        except Exception:
            logging.exception("Unexpected error")

# Helper: Check Admin (Admins oder Owner/Dev)
async def is_admin(interaction):
    # Bot-Owner/Dev dürfen Admin-Commands nutzen (auch ohne Serverrechte)
    if await is_owner_or_dev(interaction):
        return True
    # Prüfe ob User Admin-Berechtigung hat ODER Server-Owner ist ODER spezielle Rollen hat
    if interaction.user.id == (interaction.guild.owner_id if interaction.guild else 0):
        return True
    if interaction.user.guild_permissions.administrator:
        return True
    try:
        role_ids = {role.id for role in (interaction.user.roles or [])}
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
    perms = interaction.user.guild_permissions
    return perms.manage_guild or perms.manage_channels

def _has_dev_role(member: discord.Member) -> bool:
    if DEV_ROLE_ID == 0:
        return False
    try:
        role_ids = {role.id for role in (member.roles or [])}
        return DEV_ROLE_ID in role_ids
    except Exception:
        logging.exception("Failed to read member roles")
        return False

def is_owner_or_dev_member(member) -> bool:
    if member.id == BASTI_USER_ID:
        return True
    return _has_dev_role(member)

async def is_owner_or_dev(interaction: discord.Interaction) -> bool:
    if interaction.user.id == BASTI_USER_ID:
        return True
    if interaction.guild is None:
        return False
    return _has_dev_role(interaction.user)

async def require_owner_or_dev(interaction: discord.Interaction) -> bool:
    if not await is_owner_or_dev(interaction):
        await interaction.response.send_message(
            "⛔ Nur Basti oder die Developer-Rolle dürfen diesen Command nutzen.",
            ephemeral=True,
        )
        return False
    return True

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

# Slash-Command: Tägliche Belohnung
@bot.tree.command(name="täglich", description="Hole deine tägliche Belohnung ab")
async def täglich(interaction: discord.Interaction):
    visibility_key = command_visibility_key_for_interaction(interaction)
    now = int(time.time())
    async with db_context() as db:
        cursor = await db.execute("SELECT last_daily FROM user_daily WHERE user_id = ?", (interaction.user.id,))
        row = await cursor.fetchone()
        if row and row[0] and now - row[0] < 86400:
            stunden = int((86400 - (now - row[0])) / 3600)
            await _send_ephemeral(interaction, content=f"Du kannst deine tägliche Belohnung erst in {stunden} Stunden abholen.")
            return
        await db.execute("INSERT OR REPLACE INTO user_daily (user_id, last_daily) VALUES (?, ?)", (interaction.user.id, now))
        await db.commit()
    
    user_id = interaction.user.id
    karte = random.choice(karten)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(user_id, karte)
    
    if is_new_card:
        await _send_with_visibility(interaction, visibility_key, content=f"Du hast eine tägliche Belohnung erhalten: **{karte['name']}**!")
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Tägliche Belohnung - Infinitydust!", description=f"Du hattest **{karte['name']}** bereits!")
        embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await _send_with_visibility(interaction, visibility_key, embed=embed)

# Slash-Command: Mission starten
@bot.tree.command(name="mission", description="Schicke dein Team auf eine Mission und erhalte eine Belohnung")
async def mission(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
    ephemeral = visibility != VISIBILITY_PUBLIC
    # Prüfe Admin-Berechtigung
    is_admin_user = await is_admin(interaction)
    
    mission_count = 0
    if not is_admin_user:
        # Prüfe tägliche Mission-Limits für normale Nutzer
        mission_count = await get_mission_count(interaction.user.id)
        if mission_count >= 2:
            await _send_ephemeral(interaction, content="❌ Du hast heute bereits deine 2 Missionen aufgebraucht! Komme morgen wieder.")
            return
    
    # Generiere Mission-Daten
    waves = random.randint(2, 6)
    reward_card = random.choice(karten)
    
    # Erstelle Mission-Embed
    # Anzeige angepasst auf 2/Tag für Nicht-Admins
    mission_title = f"Mission {mission_count + 1}/2" if not is_admin_user else "Mission (Admin)"
    mission_description = "Hier kommt später die Story. Hier kommt später die Story."
    
    # Zeige Mission-Auswahl
    mission_data = {
        "waves": waves,
        "reward_card": reward_card,
        "current_wave": 0,
        "player_card": None,
        "title": mission_title,
        "description": mission_description,
    }
    embed = _build_mission_embed(mission_data)

    request_id = await create_mission_request(
        guild_id=interaction.guild_id or 0,
        channel_id=interaction.channel_id or 0,
        user_id=interaction.user.id,
        mission_data=mission_data,
        visibility=visibility,
        is_admin=is_admin_user,
    )
    mission_view = MissionAcceptView(
        interaction.user.id,
        mission_data,
        request_id=request_id,
        visibility=visibility,
        is_admin=is_admin_user,
    )
    message = await _send_with_visibility(interaction, visibility_key, embed=embed, view=mission_view)
    if isinstance(message, discord.Message):
        await update_mission_request_message(request_id, message.id, message.channel.id)
    else:
        # Falls keine Message zurückkommt (ephemeral), bleibt Request trotzdem offen.
        pass

async def start_mission_waves(interaction, mission_data, is_admin, ephemeral: bool):
    """Startet das Wellen-System für die Mission"""
    waves = mission_data["waves"]
    reward_card = mission_data["reward_card"]
    
    # Nutzer wählt seine Karte für die Mission
    user_karten = await get_user_karten(interaction.user.id)
    if not user_karten:
        await interaction.followup.send("❌ Du hast keine Karten für die Mission!", ephemeral=ephemeral)
        return
    
    card_select_view = CardSelectView(interaction.user.id, user_karten, 1)
    await interaction.followup.send("Wähle deine Karte für die Mission:", view=card_select_view, ephemeral=ephemeral)
    await card_select_view.wait()
    
    if not card_select_view.value:
        await interaction.followup.send("❌ Keine Karte gewählt. Mission abgebrochen.", ephemeral=ephemeral)
        return
    
    selected_card_name = card_select_view.value[0]
    player_card = await get_karte_by_name(selected_card_name)
    mission_data["player_card"] = player_card
    
    # Starte Wellen
    current_wave = 1
    while current_wave <= waves:
        # Prüfe Pause bei >4 Wellen nach der 3. Welle
        if waves > 4 and current_wave == 4:
            await interaction.followup.send("⏸️ **Pause nach der 3. Welle!** Möchtest du deine Karte wechseln?", ephemeral=ephemeral)
            
            pause_view = MissionCardSelectView(interaction.user.id, selected_card_name)
            await interaction.followup.send("Was möchtest du tun?", view=pause_view, ephemeral=ephemeral)
            await pause_view.wait()
            
            if pause_view.value == "change":
                # Neue Karte wählen
                new_card_view = MissionNewCardSelectView(interaction.user.id, user_karten)
                await interaction.followup.send("Wähle eine neue Karte:", view=new_card_view, ephemeral=ephemeral)
                await new_card_view.wait()
                
                if new_card_view.value:
                    selected_card_name = new_card_view.value
                    player_card = await get_karte_by_name(selected_card_name)
                    mission_data["player_card"] = player_card
        
        # Starte Welle mit konsistenter Karte
        wave_result = await execute_mission_wave(interaction, current_wave, waves, player_card, reward_card, ephemeral)
        
        if not wave_result:  # Niederlage
            await interaction.followup.send(f"❌ **Mission fehlgeschlagen!** Du hast in Welle {current_wave} verloren.", ephemeral=ephemeral)
            return

        await interaction.followup.send(
            f"🏆 Welle {current_wave} gewonnen! Starte Welle {current_wave + 1}...",
            ephemeral=ephemeral,
        )
        current_wave += 1
    
    # Mission erfolgreich abgeschlossen (Zähler wurde bereits beim Start erhöht)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(interaction.user.id, reward_card)
    
    if is_new_card:
        success_embed = discord.Embed(title="🏆 Mission erfolgreich!", 
                                     description=f"Du hast alle {waves} Wellen überstanden und **{reward_card['name']}** erhalten!")
        success_embed.set_image(url=reward_card["bild"])
        await interaction.followup.send(embed=success_embed, ephemeral=ephemeral)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        success_embed = discord.Embed(title="💎 Mission erfolgreich - Infinitydust!", 
                                     description=f"Du hast alle {waves} Wellen überstanden!")
        success_embed.add_field(name="Belohnung", value=f"Du hattest **{reward_card['name']}** bereits - wurde zu **Infinitydust** umgewandelt!", inline=False)
        success_embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.followup.send(embed=success_embed, ephemeral=ephemeral)

async def execute_mission_wave(interaction, wave_num, total_waves, player_card, reward_card, ephemeral: bool):
    """Führt eine einzelne Mission-Welle aus"""
    # Bot-Karte für diese Welle
    bot_card = random.choice(karten)
    
    # Erstelle interaktive Mission-BattleView
    mission_battle_view = MissionBattleView(player_card, bot_card, interaction.user.id, wave_num, total_waves)
    await mission_battle_view.init_with_buffs()

    # Erstelle Kampf-Embed nach Anwendung der Buffs
    embed = discord.Embed(title=f"⚔️ Welle {wave_num}/{total_waves}", 
                         description=f"Du kämpfst gegen **{bot_card['name']}**!")
    player_label = f"🟥 Deine Karte{mission_battle_view._status_icons(interaction.user.id)}"
    bot_label = f"🟦 Bot Karte{mission_battle_view._status_icons(0)}"
    embed.add_field(name=player_label, value=f"{player_card['name']}\nHP: {mission_battle_view.player_hp}", inline=True)
    embed.add_field(name=bot_label, value=f"{bot_card['name']}\nHP: {mission_battle_view.bot_hp}", inline=True)
    embed.set_image(url=player_card["bild"])
    embed.set_thumbnail(url=bot_card["bild"])
    _add_attack_info_field(embed, player_card)
    
    # Erstelle Kampf-Log ZUERST (über dem Kampf)
    log_embed = create_battle_log_embed()
    log_message = await interaction.followup.send(embed=log_embed, ephemeral=ephemeral)
    mission_battle_view.battle_log_message = log_message
    
    # Dann den Kampf (unter dem Log)
    battle_message = await interaction.followup.send(embed=embed, view=mission_battle_view, ephemeral=ephemeral)
    
    # Warte auf Kampf-Ende
    await mission_battle_view.wait()
    
    return mission_battle_view.result

# Entfernt: /team Command (auf Wunsch des Nutzers)

# Slash-Command: Story spielen
@bot.tree.command(name="story", description="Starte eine interaktive Story")
async def story(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
    ephemeral = visibility != VISIBILITY_PUBLIC
    # Auswahl der Story (aktuell nur "text")
    view = StorySelectView(interaction.user.id)
    embed = discord.Embed(title="📖 Story auswählen", description="Wähle eine Story aus der Liste. Aktuell verfügbar: **text**")
    await _send_with_visibility(interaction, visibility_key, embed=embed, view=view)
    await view.wait()
    if not view.value:
        await interaction.followup.send("⏰ Keine Story gewählt. Abgebrochen.", ephemeral=ephemeral)
        return

    # Starte den Story-Player (Schritt 0)
    story_view = StoryPlayerView(interaction.user.id, view.value)
    start_embed = story_view.render_step_embed()
    await interaction.followup.send(embed=start_embed, view=story_view, ephemeral=ephemeral)


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

# Slash-Command-Group: Konfiguration
configure_group = app_commands.Group(name="configure", description="Bot-Konfiguration (Nur für Admins)")

@bot.tree.command(name="ad", description="Fügt den aktuellen Kanal zur Liste erlaubter Bot-Kanäle hinzu")
async def add_channel_shortcut(interaction: discord.Interaction):
    if not await require_owner_or_dev(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    if not interaction.guild_id or not interaction.channel_id:
        await _send_ephemeral(interaction, content="❌ Dieser Command funktioniert nur in einem Server-Kanal.")
        return
    async with db_context() as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
            (interaction.guild_id, interaction.channel_id),
        )
        await db.commit()
    await _send_with_visibility(interaction, visibility_key, content=f"✅ Hinzugefügt: {interaction.channel.mention}")

@configure_group.command(name="add", description="Fügt den aktuellen Kanal zur Liste erlaubter Bot-Kanäle hinzu")
async def configure_add(interaction: discord.Interaction):
    if not await is_config_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    async with db_context() as db:
        await db.execute("INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)", (interaction.guild_id, interaction.channel_id))
        await db.commit()
    await _send_with_visibility(interaction, visibility_key, content=f"✅ Hinzugefügt: {interaction.channel.mention}")

@configure_group.command(name="remove", description="Entfernt den aktuellen Kanal aus der Liste erlaubter Bot-Kanäle")
async def configure_remove(interaction: discord.Interaction):
    if not await is_config_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    async with db_context() as db:
        await db.execute("DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?", (interaction.guild_id, interaction.channel_id))
        await db.commit()
    await _send_with_visibility(interaction, visibility_key, content=f"🗑️ Entfernt: {interaction.channel.mention}")

@configure_group.command(name="list", description="Zeigt alle erlaubten Bot-Kanäle an")
async def configure_list(interaction: discord.Interaction):
    if not await is_config_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    async with db_context() as db:
        cursor = await db.execute("SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?", (interaction.guild_id,))
        rows = await cursor.fetchall()
    if not rows:
        await _send_with_visibility(interaction, visibility_key, content="ℹ️ Es sind noch keine Kanäle erlaubt. Nutze `/configure add` im gewünschten Kanal.")
        return
    mentions = "\n".join(f"• <#{r[0]}>" for r in rows)
    await _send_with_visibility(interaction, visibility_key, content=f"✅ Erlaubte Kanäle:\n{mentions}")

# Registriere die Gruppe
bot.tree.add_command(configure_group)

# Admin-Hilfscommand: Reset „gesehenes Intro" (zum Testen)
@bot.tree.command(name="reset-intro", description="Setzt das Intro-Flag für diesen Kanal/Guild/Nutzer zurück (Nur Admins)")
async def reset_intro(interaction: discord.Interaction):
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    async with db_context() as db:
        await db.execute(
            "DELETE FROM user_seen_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild.id, interaction.channel.id),
        )
        await db.commit()
    await _send_with_visibility(
        interaction,
        visibility_key,
        content="✅ Intro-Status für ALLE in diesem Kanal zurückgesetzt. Schreibe eine Nachricht, um den Prompt erneut zu sehen.",
    )

# Select Menu Views für das neue Fuse-System
class DustAmountSelect(ui.Select):
    def __init__(self, user_dust):
        options = []
        if user_dust >= 10:
            options.append(SelectOption(label="10 Infinitydust verwenden", value="10", description="Leben/Damage +20", emoji="💎"))
        if user_dust >= 20:
            options.append(SelectOption(label="20 Infinitydust verwenden", value="20", description="Leben/Damage +40", emoji="💎"))
        if user_dust >= 30:
            options.append(SelectOption(label="30 Infinitydust verwenden", value="30", description="Leben/Damage +60", emoji="💎"))
        
        super().__init__(placeholder="Wähle die Infinitydust-Menge...", options=options)
        
    async def callback(self, interaction: discord.Interaction):
        dust_amount = int(self.values[0])
        buff_amount = dust_amount * 2  # 10=20, 20=40, 30=60
        
        # Hole User-Karten
        user_karten = await get_user_karten(interaction.user.id)
        if not user_karten:
            await interaction.response.send_message("❌ Du hast keine Karten zum Verstärken!", ephemeral=True)
            return
            
        view = FuseCardSelectView(dust_amount, buff_amount, user_karten)
        embed = discord.Embed(
            title="🎯 Karte auswählen", 
            description=f"Du verwendest **{dust_amount} Infinitydust** für **+{buff_amount}** Bonus!\n\nWähle die Karte, die du verstärken möchtest:",
            color=0x9d4edd
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=view)

class FuseCardSelectView(RestrictedView):
    def __init__(self, dust_amount, buff_amount, user_karten):
        super().__init__(timeout=60)
        self.dust_amount = dust_amount
        self.buff_amount = buff_amount
        self.add_item(CardSelect(user_karten, dust_amount, buff_amount))

class CardSelect(ui.Select):
    def __init__(self, user_karten, dust_amount, buff_amount):
        self.dust_amount = dust_amount
        self.buff_amount = buff_amount
        
        options = []
        for kartenname, anzahl in user_karten[:25]:  # Max 25 Optionen
            options.append(SelectOption(label=f"{kartenname} (x{anzahl})", value=kartenname))
        
        super().__init__(placeholder="Wähle eine Karte zum Verstärken...", options=options)
        
    async def callback(self, interaction: discord.Interaction):
        selected_card = self.values[0]
        
        # Hole Karten-Info für Attacken
        karte_data = await get_karte_by_name(selected_card)
        if not karte_data:
            await interaction.response.send_message("❌ Karte nicht gefunden!", ephemeral=True)
            return
            
        view = BuffTypeSelectView(self.dust_amount, self.buff_amount, selected_card, karte_data)
        embed = discord.Embed(
            title="⚡ Verstärkung wählen", 
            description=f"Karte: **{selected_card}**\nBonus: **+{self.buff_amount}**\n\nWas möchtest du verstärken?",
            color=0x9d4edd
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=view)

class BuffTypeSelectView(RestrictedView):
    def __init__(self, dust_amount, buff_amount, selected_card, karte_data):
        super().__init__(timeout=60)
        self.dust_amount = dust_amount
        self.buff_amount = buff_amount
        self.selected_card = selected_card
        self.add_item(BuffTypeSelect(dust_amount, buff_amount, selected_card, karte_data))

class BuffTypeSelect(ui.Select):
    def __init__(self, dust_amount, buff_amount, selected_card, karte_data):
        self.dust_amount = dust_amount
        self.buff_amount = buff_amount
        self.selected_card = selected_card
        
        options = [SelectOption(label="Leben verstärken", value="health_0", description=f"+{buff_amount} Lebenspunkte", emoji="❤️")]
        
        # Attacken hinzufügen - aber nur solche unter 200 Damage Cap
        attacks = karte_data.get("attacks", [])
        for i, attack in enumerate(attacks[:4]):  # Max 4 Attacken
            attack_name = attack.get("name", f"Attacke {i+1}")
            attack_damage = attack.get("damage", [0, 0])
            
            # Berechne maximalen Schaden (Base-Wert)
            if isinstance(attack_damage, list) and len(attack_damage) == 2:
                max_damage = attack_damage[1]
            else:
                max_damage = attack_damage
            
            # Schätze potentiellen maximalen Schaden (ohne vorhandene Buffs zu prüfen)
            # Da wir hier keinen User-Kontext haben, nehmen wir den Base-Wert + neuen Buff
            potential_max_damage = max_damage + buff_amount
            
            # Nur hinzufügen wenn potentiell unter 200 Damage Cap
            # (Genaue Prüfung erfolgt im callback)
            if potential_max_damage <= 200:
                options.append(SelectOption(
                    label=f"{attack_name} verstärken", 
                    value=f"damage_{i+1}", 
                    description=f"+{buff_amount} Damage",
                    emoji="⚔️"
                ))
        
        super().__init__(placeholder="Wähle was verstärkt werden soll...", options=options)
        
    async def callback(self, interaction: discord.Interaction):
        buff_choice = self.values[0]
        buff_type, attack_num = buff_choice.split("_")
        attack_number = int(attack_num)
        
        # 200 DAMAGE CAP: Finale Prüfung für Damage-Buffs
        if buff_type == "damage":
            # Hole Karten-Daten
            karte_data = await get_karte_by_name(self.selected_card)
            attacks = karte_data.get("attacks", [])
            if attack_number <= len(attacks):
                attack_damage = attacks[attack_number - 1].get("damage", [0, 0])
                
                # Berechne maximalen Base-Schaden
                if isinstance(attack_damage, list) and len(attack_damage) == 2:
                    max_base_damage = attack_damage[1]
                else:
                    max_base_damage = attack_damage
                
                # Prüfe vorhandene Buffs
                existing_buffs = 0
                user_buffs = await get_card_buffs(interaction.user.id, self.selected_card)
                for buff_type_check, attack_num_check, buff_amount_check in user_buffs:
                    if buff_type_check == "damage" and attack_num_check == attack_number:
                        existing_buffs += buff_amount_check
                
                # Berechne finalen maximalen Schaden
                total_max_damage = max_base_damage + existing_buffs + self.buff_amount
                
                # Prüfe 200 Damage Cap
                if total_max_damage > 200:
                    await interaction.response.send_message(
                        f"❌ **200 Damage Cap erreicht!**\n\n"
                        f"Diese Attacke würde **{total_max_damage} Schaden** erreichen.\n"
                        f"Das Maximum liegt bei **200 Schaden**.\n\n"
                        f"Aktuell: **{max_base_damage + existing_buffs}** Schaden\n"
                        f"Buff: **+{self.buff_amount}** Schaden", 
                        ephemeral=True
                    )
                    return
        else:  # health
            # 200 HP Cap check
            karte_data = await get_karte_by_name(self.selected_card)
            base_hp = karte_data.get("hp", 100)
            existing_health = 0
            user_buffs = await get_card_buffs(interaction.user.id, self.selected_card)
            for buff_type_check, attack_num_check, buff_amount_check in user_buffs:
                if buff_type_check == "health" and attack_num_check == 0:
                    existing_health += buff_amount_check
            total_hp = base_hp + existing_health + self.buff_amount
            
            # Wenn HP Cap überschritten wird, passe Buff an statt abzulehnen
            if total_hp > 200:
                # Berechne den maximalen Buff, der noch erlaubt ist
                allowed_buff = 200 - (base_hp + existing_health)
                if allowed_buff <= 0:
                    await interaction.response.send_message(
                        f"❌ **200 HP Cap bereits erreicht!**\n\n"
                        f"Diese Karte hat bereits **{base_hp + existing_health}** HP und kann nicht weiter verstärkt werden.",
                        ephemeral=True
                    )
                    return
                
                # Passe Buff-Menge an
                self.buff_amount = allowed_buff
                
                # Verbrauche Infinitydust für angepassten Buff
                success = await spend_infinitydust(interaction.user.id, self.dust_amount)
                if not success:
                    await interaction.response.send_message("❌ Nicht genug Infinitydust!", ephemeral=True)
                    return
                
                # Füge Buff hinzu
                await add_card_buff(
                    interaction.user.id, 
                    self.selected_card, 
                    buff_type, 
                    attack_number, 
                    self.buff_amount
                )
                
                # Erfolgs-Nachricht für angepassten Buff
                embed = discord.Embed(
                    title="✅ Verstärkung erfolgreich!", 
                    description=f"🃏 **{self.selected_card}**\n❤️ **Leben +{self.buff_amount}**\n\n💎 **{self.dust_amount} Infinitydust** verbraucht",
                    color=0x00ff00
                )
                embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        
        # Verbrauche Infinitydust
        success = await spend_infinitydust(interaction.user.id, self.dust_amount)
        if not success:
            await interaction.response.send_message("❌ Nicht genug Infinitydust!", ephemeral=True)
            return
        
        # Füge Buff hinzu
        await add_card_buff(
            interaction.user.id, 
            self.selected_card, 
            buff_type, 
            attack_number, 
            self.buff_amount
        )
        
        # Erfolgs-Nachricht
        if buff_type == "health":
            buff_text = f"**Leben +{self.buff_amount}**"
            emoji = "❤️"
        else:
            karte_data = await get_karte_by_name(self.selected_card)
            attack_name = karte_data["attacks"][attack_number-1]["name"]
            buff_text = f"**{attack_name} +{self.buff_amount} Damage**"
            emoji = "⚔️"
        
        embed = discord.Embed(
            title="✅ Verstärkung erfolgreich!", 
            description=f"🃏 **{self.selected_card}**\n{emoji} {buff_text}\n\n💎 **{self.dust_amount} Infinitydust** verbraucht",
            color=0x00ff00
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
                    display_name = f"{user.display_name} ({user.name})"
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
        print(f"[INVITED] selected_inviter_id={invited_user_id} by user={interaction.user.id}")

        # Prüfe nochmal ob der Einlader den Command schon mal genutzt hat (nur für Nicht-Admins)
        is_admin_user = await is_admin(interaction)
        if not is_admin_user:
            async with db_context() as db:
                cursor = await db.execute(
                    "SELECT used_invite FROM user_daily WHERE user_id = ?",
                    (self.inviter_id,),
                )
                row = await cursor.fetchone()
                print(f"[INVITED] used_invite check inviter={self.inviter_id} row={row}")
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
        print(f"[INVITED] awarded infinitydust to inviter={self.inviter_id} and invited={invited_user_id}")

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
        await interaction.channel.send(embed=embed)

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

# Slash-Command: Eingeladen (Einmalig pro User - 1x Infinitydust für beide)
@bot.tree.command(name="eingeladen", description="Wähle wer dich eingeladen hat - beide erhalten 1x Infinitydust [Einmalig]")
async def eingeladen(interaction: discord.Interaction):
    try:
        print(f"[INVITED] /eingeladen invoked by user={interaction.user.id} guild={interaction.guild_id} channel={interaction.channel_id}")
        visibility_key = command_visibility_key_for_interaction(interaction)
        visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
        ephemeral = visibility != VISIBILITY_PUBLIC
        await interaction.response.defer(ephemeral=ephemeral)
        
        user_id = interaction.user.id
        is_admin_user = await is_admin(interaction)
        print(f"[INVITED] is_admin_user={is_admin_user} for user={interaction.user.id}")
        
        # Hole alle User die den Bot schon mal genutzt haben
        async with db_context() as db:
            # Prüfe ob User den Command schon mal genutzt hat (nur für Nicht-Admins)
            if not is_admin_user:
                cursor = await db.execute("SELECT used_invite FROM user_daily WHERE user_id = ?", (user_id,))
                row = await cursor.fetchone()
                if row and row[0] == 1:
                    await interaction.followup.send("❌ Du hast den `/eingeladen` Command bereits verwendet! Nur Admins können ihn mehrfach nutzen.", ephemeral=True)
                    return
            
            # Hole alle User die den Bot schon mal genutzt haben
            cursor = await db.execute("SELECT DISTINCT user_id FROM user_karten")
            user_rows = await cursor.fetchall()
            
            # Zusätzlich User aus anderen Tabellen
            cursor = await db.execute("SELECT DISTINCT user_id FROM user_daily")
            daily_rows = await cursor.fetchall()
            
            cursor = await db.execute("SELECT DISTINCT user_id FROM user_infinitydust")
            dust_rows = await cursor.fetchall()
            
            # Alle User IDs sammeln
            all_user_ids = set()
            for row in user_rows + daily_rows + dust_rows:
                all_user_ids.add(row[0])
            
            # Eigene User ID entfernen
            all_user_ids.discard(user_id)
            print(f"[INVITED] candidates_found={len(all_user_ids)} for user={user_id}")
            
            if not all_user_ids:
                await interaction.followup.send("❌ Keine anderen Spieler gefunden! Es müssen andere Spieler den Bot bereits genutzt haben.", ephemeral=True)
                return
        
        # Erstelle User-Auswahl
        view = InviteUserSelectView(user_id, list(all_user_ids))
        # Beschreibung basierend auf Admin-Status
        if is_admin_user:
            description = "Wähle aus, wer dich eingeladen hat!\n\n**Beide erhaltet ihr 1x Infinitydust** 💎\n\n👑 **Du bist Admin - kannst unendlich oft einladen!**"
        else:
            description = "Wähle aus, wer dich eingeladen hat!\n\n**Beide erhaltet ihr 1x Infinitydust** 💎\n\n⚠️ **Dieser Command kann nur einmal verwendet werden!**"
        
        embed = discord.Embed(
            title="🎁 Wer hat dich eingeladen?",
            description=description,
            color=0x9d4edd
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        
    except discord.NotFound:
        logging.info("Invite interaction message no longer exists")
    except Exception as e:
        print(f"Fehler in eingeladen command: {e}")
        try:
            await interaction.followup.send("❌ Ein Fehler ist aufgetreten. Bitte versuche es erneut.", ephemeral=True)
        except:
            logging.exception("Unexpected error")

# Slash-Command: Karten mit Infinitydust verstärken
@bot.tree.command(name="fuse", description="Verstärke deine Karten mit Infinitydust")
async def fuse(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    user_id = interaction.user.id
    user_dust = await get_infinitydust(user_id)
    
    if user_dust < 10:
        embed = discord.Embed(
            title="❌ Nicht genug Infinitydust", 
            description=f"Du hast nur **{user_dust} Infinitydust**.\nDu brauchst mindestens **10 Infinitydust** zum Verstärken!",
            color=0xff0000
        )
        await _send_ephemeral(interaction, embed=embed)
        return
    
    view = DustAmountView(user_dust)
    embed = discord.Embed(
        title="💎 Karten-Verstärkung", 
        description=f"Du hast **{user_dust} Infinitydust**\n\nWähle die Menge für die Verstärkung:\n\n💎 **10 Dust** = +20 Leben/Damage\n💎 **20 Dust** = +40 Leben/Damage\n💎 **30 Dust** = +60 Leben/Damage",
        color=0x9d4edd
    )
    embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    await _send_with_visibility(interaction, visibility_key, embed=embed, view=view)

# Slash-Command: Vault anzeigen
@bot.tree.command(name="vault", description="Zeige deine Karten-Sammlung")
async def vault(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    user_id = interaction.user.id
    user_karten = await get_user_karten(user_id)
    infinitydust = await get_infinitydust(user_id)
    
    if not user_karten and infinitydust == 0:
        await _send_ephemeral(interaction, content="Du hast noch keine Karten in deiner Sammlung.")
        return
    
    embed = discord.Embed(title="🗄️ Deine Karten-Sammlung", description=f"Du besitzt **{len(user_karten)}** verschiedene Karten:")
    
    # Füge Infinitydust hinzu (falls vorhanden)
    if infinitydust > 0:
        embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    
    # Füge normale Karten hinzu
    for kartenname, anzahl in user_karten[:10]:  # Zeige nur die ersten 10
        karte = await get_karte_by_name(kartenname)
        if karte:
            embed.add_field(name=f"{karte['name']} (x{anzahl})", value=karte['beschreibung'][:100] + "...", inline=False)
    
    if len(user_karten) > 10:
        embed.set_footer(text=f"Und {len(user_karten) - 10} weitere Karten...")
    
    view = VaultView(interaction.user.id, user_karten)
    await _send_with_visibility(interaction, visibility_key, embed=embed, view=view)

# Admin-Command: Vault anderer User anzeigen
@bot.tree.command(name="vaultlook", description="Schau in den Vault eines anderen Users (Nur für Admins)")
async def vaultlook(interaction: discord.Interaction):
    visibility_key = command_visibility_key_for_interaction(interaction)
    visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
    ephemeral = visibility != VISIBILITY_PUBLIC
    # Schnell antworten, dann prüfen
    await interaction.response.defer(ephemeral=ephemeral)
    if not await is_admin(interaction):
        await interaction.followup.send("❌ Du hast keine Berechtigung für diesen Command! Nur Admins können in andere Vaults schauen.", ephemeral=True)
        return

    # Nutzer-Auswahl mit Suche und Statuskreisen (wie in /fight)
    view = AdminUserSelectView(interaction.user.id, interaction.guild)
    await interaction.followup.send("Wähle einen User, dessen Vault du ansehen möchtest:", view=view, ephemeral=ephemeral)
    await view.wait()
    
    if not view.value:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
        return
    
    target_user_id = int(view.value)
    target_user = interaction.guild.get_member(target_user_id)
    
    if not target_user:
        await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
        return

    await send_vaultlook(interaction, target_user_id, target_user.display_name, visibility_key=visibility_key)

@bot.tree.command(name="fight", description="Kämpfe gegen einen anderen Spieler im 1v1!")
async def fight(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    visibility_override = await get_command_visibility_override(interaction)
    # Schritt 0: Sichtbarkeit wählen (Privat/Öffentlich)
    # Defer sofort, um Interaktions-Timeouts/Unknown interaction zu vermeiden
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        logging.exception("Unexpected error")
    if visibility_override is None:
        visibility_view = FightVisibilityView(interaction.user.id)
        await interaction.followup.send("Wie soll der Kampf sichtbar sein?", view=visibility_view, ephemeral=True)
        await visibility_view.wait()
        if visibility_view.value is None:
            await interaction.followup.send("⏰ Keine Auswahl getroffen. Kampf abgebrochen.", ephemeral=True)
            return
        is_private = visibility_view.value
    else:
        is_private = visibility_override != VISIBILITY_PUBLIC
    fight_thread: discord.Thread | None = None
    me = _get_bot_member(interaction)

    # Schritt 1: Karten-Auswahl (nur 1 Karte)
    user_karten = await get_user_karten(interaction.user.id)
    if not user_karten:
        # Erste Antwort ist bereits erfolgt -> followup verwenden
        await interaction.followup.send("Du brauchst mindestens 1 Karte für den Kampf!", ephemeral=True)
        return
    
    card_select_view = CardSelectView(interaction.user.id, user_karten, 1)
    await interaction.followup.send("Wähle deine Karte für den 1v1 Kampf:", view=card_select_view, ephemeral=True)
    await card_select_view.wait()
    if not card_select_view.value:
        await interaction.followup.send("⏰ Keine Karte gewählt. Kampf abgebrochen.", ephemeral=True)
        return
    
    selected_names = card_select_view.value
    selected_cards = [await get_karte_by_name(name) for name in selected_names]
    
    # Schritt 2: Gegner-Auswahl
    view = OpponentSelectView(interaction.user, interaction.guild)
    await interaction.followup.send("Wähle einen Gegner (User oder Bot):", view=view, ephemeral=True)
    await view.wait()
    if not view.value:
        await interaction.followup.send("⏰ Kein Gegner gewählt. Kampf abgebrochen.", ephemeral=True)
        return
    
    opponent_id = view.value
    if opponent_id == "bot":
        # Bot als Gegner - verwende zufällige Karte
        bot_card = random.choice(karten)
        battle_view = BattleView(selected_cards[0], bot_card, interaction.user.id, 0, None)  # Bot hat ID 0
        await battle_view.init_with_buffs()
        
        # Erstelle Bot-User-Objekt für das Embed
        class BotUser:
            def __init__(self):
                self.id = 0
                self.display_name = "Bot"
                self.mention = "**Bot**"
        
        bot_user = BotUser()
        # Route in privaten Thread, falls privat
        target_channel: discord.abc.Messageable = interaction.channel
        if is_private and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            try:
                if isinstance(interaction.channel, discord.TextChannel) and me is not None:
                    perms = interaction.channel.permissions_for(me)
                    if not perms.create_private_threads or not perms.send_messages_in_threads:
                        await interaction.followup.send(
                            "⚠️ Privater Kampf nicht möglich (fehlende Thread-Rechte). Ich poste öffentlich.",
                            ephemeral=True,
                        )
                    else:
                        # Erzeuge privaten Thread und füge Herausforderer hinzu
                        thread_name = f"Privater Kampf von {interaction.user.display_name}"
                        fight_thread = await interaction.channel.create_thread(
                            name=thread_name,
                            type=discord.ChannelType.private_thread,
                            invitable=False,
                        )
                        await fight_thread.add_user(interaction.user)
                        target_channel = fight_thread
                elif isinstance(interaction.channel, discord.Thread):
                    if me is not None and not _can_send_in_channel(interaction.channel, me):
                        await interaction.followup.send(
                            "⚠️ Kein Schreibzugriff im Thread. Ich poste öffentlich.",
                            ephemeral=True,
                        )
                    else:
                        target_channel = interaction.channel
            except Exception:
                # Fallback: öffentlich
                fight_thread = None
                target_channel = interaction.channel

        if me is not None and isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            if not _can_send_in_channel(target_channel, me):
                await _send_ephemeral(
                    interaction,
                    content="❌ Mir fehlen Rechte, um den Kampf hier zu posten. Bitte gib mir Zugriff.",
                )
                return

        # KAMPF-LOG ZUERST senden (wird über der Kampf-Nachricht angezeigt)
        log_embed = create_battle_log_embed()
        battle_log_message = await _safe_send_channel(interaction, target_channel, embed=log_embed)
        if battle_log_message is None:
            return
        battle_view.battle_log_message = battle_log_message
        
        # DANN Kampf-Nachricht senden (erscheint unter dem Log)
        embed = create_battle_embed(
            selected_cards[0],
            bot_card,
            battle_view.player1_hp,
            battle_view.player2_hp,
            interaction.user.id,
            interaction.user,
            bot_user,
            current_attack_infos=_build_attack_info_lines(selected_cards[0]),
        )
        if await _safe_send_channel(interaction, target_channel, embed=embed, view=battle_view) is None:
            return
        return
    
    # User als Gegner
    challenged = interaction.guild.get_member(int(opponent_id))
    if not challenged:
        await interaction.followup.send("❌ Gegner nicht gefunden!", ephemeral=True)
        return
    # Privater Thread ggf. erstellen und beide hinzufügen
    target_channel: discord.abc.Messageable = interaction.channel
    thread_created = False
    if is_private and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        try:
            if isinstance(interaction.channel, discord.TextChannel) and me is not None:
                perms = interaction.channel.permissions_for(me)
                if not perms.create_private_threads or not perms.send_messages_in_threads:
                    await interaction.followup.send(
                        "⚠️ Privater Kampf nicht möglich (fehlende Thread-Rechte). Ich poste öffentlich.",
                        ephemeral=True,
                    )
                else:
                    thread_name = f"Privater Kampf: {interaction.user.display_name} vs {challenged.display_name}"
                    fight_thread = await interaction.channel.create_thread(
                        name=thread_name,
                        type=discord.ChannelType.private_thread,
                        invitable=False,
                    )
                    await fight_thread.add_user(interaction.user)
                    await fight_thread.add_user(challenged)
                    target_channel = fight_thread
                    thread_created = True
            elif isinstance(interaction.channel, discord.Thread):
                if me is not None and not _can_send_in_channel(interaction.channel, me):
                    await interaction.followup.send(
                        "⚠️ Kein Schreibzugriff im Thread. Ich poste öffentlich.",
                        ephemeral=True,
                    )
                else:
                    target_channel = interaction.channel
        except Exception:
            fight_thread = None
            target_channel = interaction.channel

    # Nachricht an Herausgeforderten + Request speichern
    request_id = await create_fight_request(
        guild_id=interaction.guild_id or 0,
        origin_channel_id=interaction.channel_id or 0,
        message_channel_id=getattr(target_channel, "id", 0) or 0,
        thread_id=fight_thread.id if thread_created and fight_thread else None,
        thread_created=thread_created,
        challenger_id=interaction.user.id,
        challenged_id=challenged.id,
        challenger_card=selected_cards[0]["name"],
    )
    challenge_view = ChallengeResponseView(
        interaction.user.id,
        challenged.id,
        selected_cards[0]["name"],
        request_id=request_id,
        thread_id=fight_thread.id if thread_created and fight_thread else None,
        thread_created=thread_created,
    )
    message = await _safe_send_channel(
        interaction,
        target_channel,
        content=f"{challenged.mention}, du wurdest zu einem 1v1 Kartenkampf herausgefordert!",
        view=challenge_view,
    )
    if message is None:
        await claim_fight_request(request_id, "failed")
        await _maybe_delete_fight_thread(fight_thread.id if fight_thread else None, thread_created)
        return
    await update_fight_request_message(request_id, message.id, getattr(message.channel, "id", None))
    await interaction.followup.send(f"Warte auf Antwort von {challenged.mention}...", ephemeral=True)
    return



# Slash-Command: Anfang (Hauptmenü)
class AnfangView(RestrictedView):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="tägliche Karte", style=discord.ButtonStyle.success, row=0, custom_id="anfang:daily")
    async def btn_daily(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum täglichen Belohnungs-Flow weiter
        await täglich.callback(interaction)

    @ui.button(label="Verbessern", style=discord.ButtonStyle.primary, row=0, custom_id="anfang:fuse")
    async def btn_fuse(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fuse-Flow weiter
        await fuse.callback(interaction)

    @ui.button(label="Kämpfe", style=discord.ButtonStyle.danger, row=0, custom_id="anfang:fight")
    async def btn_fight(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fight-Flow weiter
        await fight.callback(interaction)

    @ui.button(label="Mission", style=discord.ButtonStyle.secondary, row=0, custom_id="anfang:mission")
    async def btn_mission(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Missions-Flow weiter
        await mission.callback(interaction)

    @ui.button(label="Story", style=discord.ButtonStyle.secondary, row=0, custom_id="anfang:story")
    async def btn_story(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Story-Flow weiter
        await story.callback(interaction)

class IntroEphemeralPromptView(RestrictedView):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    @ui.button(label="Intro anzeigen (nur für dich)", style=discord.ButtonStyle.primary)
    async def show_intro(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht für dich gedacht.", ephemeral=True)
            return
        view = AnfangView()
        text = (
            "# **Rekrut.**\n\n"
            "Hör gut zu. Ich bin Nick Fury, und wenn du Teil von etwas Größerem sein willst, bist du hier richtig. Willkommen auf dem Helicarrier. Wir haben alle Hände voll zu tun, und ich hoffe, du bist bereit, dir die Hände schmutzig zu machen.\n\n"
            "Du willst wissen, wie du an die guten Sachen kommst? Täglich hast du die Chance, eine zufällige Karte aus dem Pool zu ziehen `[/täglich im Chat schreiben]`. Und wenn du eine doppelte Karte ziehst, verschwindet sie nicht einfach. Sie wird zu Staub umgewandelt. Sammle genug davon, um deine Karten zu verbessern und sie so noch mächtiger zu machen `[/fuse im Chat schreiben]`.\n\n"
            "Du bist neu hier und brauchst Training? Auf dem Helicarrier kannst du dich mit anderen anlegen und üben, bis deine Strategien sitzen `[/fight im Chat schreiben]`.\n\n"
            "Wenn du bereit für den echten Einsatz bist, stehen dir jeden Tag zwei Missionen zur Verfügung. Schließe sie ab und ich garantiere dir, du bekommst jeweils eine Karte als Belohnung `[/mission im Chat schreiben]`.\n\n"
            "Für die Verrückten da draußen, die meinen, sie wären unschlagbar: Es gibt den Story-Modus. Du hast drei Leben, um die gesamte Geschichte zu überleben. Schaffst du das, wartet eine mysteriöse Belohnung auf dich `[/story im Chat schreiben]`.\n\n"
            "**Also los jetzt. Sag mir, was du tun willst. Wir haben keine Zeit zu verlieren.**"
        )
        await interaction.response.send_message(content=text, view=view, ephemeral=True)


@bot.tree.command(name="anfang", description="Zeigt das Startmenü mit Schnellzugriff auf wichtige Funktionen")
@app_commands.describe(action="Optional: /anfang aktualisieren oder /anfang lastaktu")
@app_commands.choices(
    action=[
        app_commands.Choice(name="aktualisieren", value="aktualisieren"),
        app_commands.Choice(name="lastaktu", value="lastaktu"),
    ]
)
async def anfang(interaction: discord.Interaction, action: str | None = None):
    if not await is_channel_allowed(interaction):
        return

    text = (
        "# **Rekrut.**\n\n"
        "Hör gut zu. Ich bin Nick Fury, und wenn du Teil von etwas Größerem sein willst, bist du hier richtig. Willkommen auf dem Helicarrier. Wir haben alle Hände voll zu tun, und ich hoffe, du bist bereit, dir die Hände schmutzig zu machen.\n\n"
        "Du willst wissen, wie du an die guten Sachen kommst? Täglich hast du die Chance, eine zufällige Karte aus dem Pool zu ziehen `[/täglich im Chat schreiben]`. Und wenn du eine doppelte Karte ziehst, verschwindet sie nicht einfach. Sie wird zu Staub umgewandelt. Sammle genug davon, um deine Karten zu verbessern und sie so noch mächtiger zu machen `[/fuse im Chat schreiben]`.\n\n"
        "Du bist neu hier und brauchst Training? Auf dem Helicarrier kannst du dich mit anderen anlegen und üben, bis deine Strategien sitzen `[/fight im Chat schreiben]`.\n\n"
        "Wenn du bereit für den echten Einsatz bist, stehen dir jeden Tag zwei Missionen zur Verfügung. Schließe sie ab und ich garantiere dir, du bekommst jeweils eine Karte als Belohnung `[/mission im Chat schreiben]`.\n\n"
        "Für die Verrückten da draußen, die meinen, sie wären unschlagbar: Es gibt den Story-Modus. Du hast drei Leben, um die gesamte Geschichte zu überleben. Schaffst du das, wartet eine mysteriöse Belohnung auf dich `[/story im Chat schreiben]`.\n\n"
        "**Also los jetzt. Sag mir, was du tun willst. Wir haben keine Zeit zu verlieren.**"
    )

    view = AnfangView()
    if interaction.guild is None:
        await interaction.response.send_message(content=text, view=view)
        return

    visibility_key = command_visibility_key_for_interaction(interaction)
    visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
    is_admin_user = await is_admin(interaction)

    if action:
        if not is_admin_user:
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        if action == "lastaktu":
            existing = await get_latest_anfang_message(interaction.guild_id)
            if not existing:
                await interaction.response.send_message("ℹ️ Es gibt noch keine gespeicherte /anfang-Nachricht.", ephemeral=True)
                return
            channel_id, message_id = existing
            link = f"https://discord.com/channels/{interaction.guild_id}/{channel_id}/{message_id}"
            await interaction.response.send_message(f"🔗 Letzte /anfang-Nachricht: {link}", ephemeral=True)
            return
        if action == "aktualisieren":
            existing = await get_latest_anfang_message(interaction.guild_id)
            if not existing:
                await interaction.response.send_message("ℹ️ Keine gespeicherte /anfang-Nachricht gefunden. Nutze zuerst `/anfang`.", ephemeral=True)
                return
            old_channel_id, old_message_id = existing
            try:
                old_channel = interaction.guild.get_channel(old_channel_id) or await interaction.guild.fetch_channel(old_channel_id)
                if not isinstance(old_channel, (discord.TextChannel, discord.Thread)):
                    await interaction.response.send_message("❌ Kanal der gespeicherten Nachricht nicht gefunden.", ephemeral=True)
                    return
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.edit(content=text, view=view)
                await set_latest_anfang_message(
                    interaction.guild_id,
                    old_channel_id,
                    old_message_id,
                    interaction.user.id,
                )
                await interaction.response.send_message("✅ /anfang aktualisiert.", ephemeral=True)
            except Exception:
                logging.exception("Failed to edit latest /anfang message")
                await interaction.response.send_message("❌ Konnte die gespeicherte /anfang-Nachricht nicht aktualisieren.", ephemeral=True)
            return

    if is_admin_user:
        existing = await get_latest_anfang_message(interaction.guild_id)

        # Alte Nachricht deaktivieren (Buttons entfernen), damit nur die neueste erkannt wird
        if existing:
            old_channel_id, old_message_id = existing
            try:
                old_channel = interaction.guild.get_channel(old_channel_id) or await interaction.guild.fetch_channel(old_channel_id)
                if isinstance(old_channel, (discord.TextChannel, discord.Thread)):
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.edit(view=None)
            except Exception:
                pass

        sent_message = None
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(content=text, view=view)
                sent_message = await interaction.original_response()
            else:
                sent_message = await interaction.followup.send(content=text, view=view)
        except Exception:
            logging.exception("Failed to send /anfang message")
            return

        await set_latest_anfang_message(
            interaction.guild_id,
            sent_message.channel.id,
            sent_message.id,
            interaction.user.id,
        )
        return

    # Nicht-Admins: Sichtbarkeit über Panel-Einstellung
    if visibility == VISIBILITY_PUBLIC:
        await _send_with_visibility(interaction, visibility_key, content=text, view=view)
    else:
        await _send_ephemeral(interaction, content=text, view=view)

# Admin-Command: Test-Bericht
@bot.tree.command(name="test-bericht", description="Listet alle verfügbaren Commands und deren Status (Nur für Admins)")
async def test_bericht(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Prüfe Admin-Berechtigung
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Du hast keine Berechtigung.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    await _send_ephemeral(interaction, content="🔍 Sammle verfügbare Commands...")
    await send_test_report(interaction, visibility_key=visibility_key)

class UserSelectView(RestrictedView):
    def __init__(self, user_id, guild):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.guild = guild
        self.value = None
        self.members = sorted(
            [member for member in guild.members if not member.bot],
            key=lambda m: m.display_name.lower(),
        )
        self.pages = [self.members[i:i + 25] for i in range(0, len(self.members), 25)] or [[]]
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

        if not self.members:
            self.select.disabled = True

    def _placeholder(self) -> str:
        if not self.members:
            return "Keine Nutzer verfügbar"
        return f"Wähle einen Nutzer... (Seite {self.page_index + 1}/{len(self.pages)})"

    def _build_options_for_current_page(self) -> list[SelectOption]:
        if not self.members:
            return [SelectOption(label="Keine Nutzer verfügbar", value="__none__")]
        page_members = self.pages[self.page_index]
        return [
            SelectOption(label=member.display_name[:100], value=str(member.id))
            for member in page_members
        ]

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann den Nutzer wählen!", ephemeral=True)
            return
        selected_value = self.select.values[0]
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
            async def handle_select(inter: discord.Interaction):
                if inter.user.id != self.user_id:
                    await inter.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                    return
                card_name = select.values[0]
                karte = await get_karte_by_name(card_name)
                if not karte:
                    await inter.response.send_message("Karte nicht gefunden.", ephemeral=True)
                    return
                embed = discord.Embed(title=karte["name"], description=karte["beschreibung"])
                embed.set_image(url=karte["bild"])
                
                # Attacken + Schaden unter der Karte anzeigen (inkl. /fuse Buffs des Users)
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
                        if isinstance(dmg, list) and len(dmg) == 2:
                            min_b = dmg[0] + buff
                            max_b = dmg[1] + buff
                            dmg_text = f"{min_b}-{max_b}"
                        else:
                            dmg_text = str((dmg or 0) + buff)
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
                    if isinstance(dmg, list) and len(dmg) == 2:
                        dmg_text = f"{dmg[0] + buff}-{dmg[1] + buff}"
                    else:
                        dmg_text = str((dmg or 0) + buff)
                    btn = ui.Button(
                        label=f"{atk.get('name', f'Attacke {i+1}')} ({dmg_text})",
                        style=discord.ButtonStyle.danger,
                        disabled=True,
                        row=0 if i < 2 else 1
                    )
                    view_buttons.add_item(btn)
                
                await inter.response.send_message(embed=embed, view=view_buttons, ephemeral=True)

            select.callback = handle_select
            view = RestrictedView(timeout=90)
            view.add_item(select)
            await interaction.response.send_message("Wähle eine Karte:", view=view, ephemeral=True)
        else:
            # Paginierung
            pages = [options[i:i+25] for i in range(0, len(options), 25)]
            current_index = 0

            async def send_page(inter: discord.Interaction, page_index: int):
                sel = ui.Select(placeholder=f"Seite {page_index+1}/{len(pages)} – Karte wählen...", min_values=1, max_values=1, options=pages[page_index])
                async def handle_sel(ii: discord.Interaction):
                    if ii.user.id != self.user_id:
                        await ii.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
                        return
                    card_name = sel.values[0]
                    karte = await get_karte_by_name(card_name)
                    if not karte:
                        await ii.response.send_message("Karte nicht gefunden.", ephemeral=True)
                        return
                    embed = discord.Embed(title=karte["name"], description=karte["beschreibung"])
                    embed.set_image(url=karte["bild"])
                    
                    # Attacken + Schaden unter der Karte anzeigen (inkl. /fuse Buffs des Users)
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
                            if isinstance(dmg, list) and len(dmg) == 2:
                                min_b = dmg[0] + buff
                                max_b = dmg[1] + buff
                                dmg_text = f"{min_b}-{max_b}"
                            else:
                                dmg_text = str((dmg or 0) + buff)
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
                        if isinstance(dmg, list) and len(dmg) == 2:
                            dmg_text = f"{dmg[0] + buff}-{dmg[1] + buff}"
                        else:
                            dmg_text = str((dmg or 0) + buff)
                        btn = ui.Button(
                            label=f"{atk.get('name', f'Attacke {i+1}')} ({dmg_text})",
                            style=discord.ButtonStyle.danger,
                            disabled=True,
                            row=0 if i < 2 else 1
                        )
                        view_buttons.add_item(btn)
                    
                    await ii.response.send_message(embed=embed, view=view_buttons, ephemeral=True)
                sel.callback = handle_sel

                prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=page_index==0)
                next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=page_index==len(pages)-1)

                async def on_prev(ii: discord.Interaction):
                    if ii.user.id != self.user_id:
                        await ii.response.send_message("Nicht dein Menü!", ephemeral=True)
                        return
                    await send_page(ii, page_index-1)

                async def on_next(ii: discord.Interaction):
                    if ii.user.id != self.user_id:
                        await ii.response.send_message("Nicht dein Menü!", ephemeral=True)
                        return
                    await send_page(ii, page_index+1)

                prev_btn.callback = on_prev
                next_btn.callback = on_next

                v = RestrictedView(timeout=120)
                v.add_item(sel)
                v.add_item(prev_btn)
                v.add_item(next_btn)

                # Falls dies eine Folgeaktion ist, verwende followup, sonst response
                try:
                    await inter.response.send_message("Wähle eine Karte:", view=v, ephemeral=True)
                except discord.InteractionResponded:
                    await inter.followup.send("Wähle eine Karte:", view=v, ephemeral=True)

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

# Slash-Command: Karte geben
@bot.tree.command(name="give", description="Gib einem Nutzer eine Karte (Admin)")
async def give(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Prüfe Admin-Berechtigung
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Command! Nur Admins/Owner können Karten geben.", ephemeral=True)
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    visibility = await get_message_visibility(interaction.guild_id, visibility_key) if visibility_key else VISIBILITY_PRIVATE
    ephemeral = visibility != VISIBILITY_PUBLIC
    
    # Schritt 1: Nutzer-Auswahl
    user_select_view = AdminUserSelectView(interaction.user.id, interaction.guild)
    await _send_with_visibility(interaction, visibility_key, content="Wähle einen Nutzer, dem du eine Karte geben möchtest:", view=user_select_view)
    await user_select_view.wait()
    
    if not user_select_view.value:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
        return
    
    target_user_id = int(user_select_view.value)
    target_user = interaction.guild.get_member(target_user_id)
    
    if not target_user:
        await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
        return
    
    # Schritt 2: Karten-Auswahl
    card_select_view = GiveCardSelectView(interaction.user.id, target_user_id)
    await interaction.followup.send(f"Wähle eine Karte für {target_user.mention}:", view=card_select_view, ephemeral=ephemeral)
    await card_select_view.wait()
    
    if not card_select_view.value:
        await interaction.followup.send("⏰ Keine Karte gewählt. Abgebrochen.", ephemeral=ephemeral)
        return
    
    selected_card_name = card_select_view.value
    
    # Prüfe ob Infinitydust ausgewählt wurde
    if selected_card_name == "infinitydust":
        # Infinitydust-Mengen-Auswahl
        amount_view = InfinitydustAmountView(interaction.user.id, target_user_id)
        await interaction.followup.send(f"Wähle die Menge Infinitydust für {target_user.mention}:", view=amount_view, ephemeral=ephemeral)
        await amount_view.wait()
        
        if not amount_view.value:
            await interaction.followup.send("⏰ Keine Menge gewählt. Abgebrochen.", ephemeral=ephemeral)
            return
        
        amount = amount_view.value
        
        # Infinitydust dem Nutzer geben
        await add_infinitydust(target_user_id, amount)
        
        # Erfolgsnachricht für Infinitydust (öffentlich)
        embed = discord.Embed(title="💎 Infinitydust verschenkt!", description=f"{interaction.user.mention} hat **{amount}x Infinitydust** an {target_user.mention} gegeben!")
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await _send_with_visibility(interaction, visibility_key, embed=embed)
        return
    
    # Normale Karte dem Nutzer geben
    selected_card = await get_karte_by_name(selected_card_name)
    is_new_card = await check_and_add_karte(target_user_id, selected_card)
    
    # Erfolgsnachricht (öffentlich)
    if is_new_card:
        embed = discord.Embed(title="🎁 Karte verschenkt!", description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!")
        if selected_card:
            embed.set_image(url=selected_card["bild"])
        await _send_with_visibility(interaction, visibility_key, embed=embed)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Karte verschenkt - Infinitydust!", description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!")
        embed.add_field(name="Umwandlung", value=f"{target_user.mention} hatte die Karte bereits - wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await _send_with_visibility(interaction, visibility_key, embed=embed)

# View für Mission-Auswahl
class MissionAcceptView(RestrictedView):
    def __init__(self, user_id, mission_data, *, request_id: int, visibility: str, is_admin: bool):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.mission_data = mission_data
        self.request_id = request_id
        self.visibility = visibility
        self.is_admin = is_admin

    @ui.button(label="Annehmen", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann annehmen!", ephemeral=True)
            return
        if not await claim_mission_request(self.request_id, "accepted"):
            await interaction.response.send_message("❌ Diese Missions-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        ephemeral = self.visibility != VISIBILITY_PUBLIC and interaction.guild is not None
        await interaction.response.defer(ephemeral=ephemeral)
        if not self.is_admin:
            await increment_mission_count(self.user_id)
        await start_mission_waves(interaction, self.mission_data, self.is_admin, ephemeral)
        self.stop()

    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann ablehnen!", ephemeral=True)
            return
        if not await claim_mission_request(self.request_id, "declined"):
            await interaction.response.send_message("❌ Diese Missions-Anfrage ist nicht mehr offen.", ephemeral=True)
            return
        ephemeral = self.visibility != VISIBILITY_PUBLIC and interaction.guild is not None
        await interaction.response.send_message("Mission abgelehnt.", ephemeral=ephemeral)
        self.stop()

# View für Karten-Auswahl bei Pause
class MissionCardSelectView(RestrictedView):
    def __init__(self, user_id, current_card_name):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.current_card_name = current_card_name
        self.value = None
        
        # Optionen: Beibehalten oder neue Karte wählen
        options = [
            SelectOption(label=f"Beibehalten: {current_card_name}", value="keep"),
            SelectOption(label="Neue Karte wählen", value="change")
        ]
        self.select = ui.Select(placeholder="Was möchtest du tun?", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

# View für neue Karten-Auswahl
class MissionNewCardSelectView(RestrictedView):
    def __init__(self, user_id, user_karten):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value = None
        
        options = [SelectOption(label=karte_name, value=karte_name) for karte_name, _ in user_karten]
        self.select = ui.Select(placeholder="Wähle eine neue Karte...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()



# View für Mission-Kämpfe (interaktiv)
class MissionBattleView(RestrictedView):
    def __init__(self, player_card, bot_card, user_id, wave_num, total_waves):
        super().__init__(timeout=120)
        self.player_card = player_card
        self.bot_card = bot_card
        self.user_id = user_id
        self.wave_num = wave_num
        self.total_waves = total_waves
        self.result = None
        self.player_hp = player_card.get("hp", 100)
        self.bot_hp = bot_card.get("hp", 100)
        self.player_max_hp = self.player_hp
        self.bot_max_hp = self.bot_hp
        self.current_turn = user_id  # Spieler beginnt
        self.attacks = player_card.get("attacks", [
            {"name": "Punch", "damage": 20},
            {"name": "Kick", "damage": 25},
            {"name": "Special", "damage": 30},
            {"name": "Ultimate", "damage": 40}
        ])
        self.round_counter = 0
        self.battle_log_message = None
        self._last_log_edit_ts = 0.0

        # Buff-Speicher
        self.health_bonus = 0
        # Map: attack_number (1..4) -> total damage bonus
        self.damage_bonuses = {}

        # SIDE EFFECTS SYSTEM
        self.active_effects = {self.user_id: [], 0: []}
        # CONFUSION flags for mission mode
        self.confused_next_turn = {self.user_id: False, 0: False}

        # COOLDOWN-SYSTEM für Missionen
        # Separate Cooldowns für Spieler und Bot
        # Format: {attack_index: turns_remaining}
        self.user_attack_cooldowns = {}
        self.bot_attack_cooldowns = {}
        # Manual reload tracking for player and bot
        self.manual_reload_needed = {self.user_id: {}, 0: {}}
        self.stunned_next_turn = {self.user_id: False, 0: False}
        self.special_lock_next_turn = {self.user_id: False, 0: False}
        self.blind_next_attack = {self.user_id: 0.0, 0: 0.0}
        self.pending_flat_bonus = {self.user_id: 0, 0: 0}
        self.pending_flat_bonus_uses = {self.user_id: 0, 0: 0}
        self.pending_multiplier = {self.user_id: 1.0, 0: 1.0}
        self.pending_multiplier_uses = {self.user_id: 0, 0: 0}
        self.force_max_next = {self.user_id: 0, 0: 0}
        self.guaranteed_hit_next = {self.user_id: 0, 0: 0}
        self.incoming_modifiers = {self.user_id: [], 0: []}
        self.outgoing_attack_modifiers = {self.user_id: [], 0: []}
        self.absorbed_damage = {self.user_id: 0, 0: 0}
        self.delayed_defense_queue = {self.user_id: [], 0: []}
        self.airborne_pending_landing = {self.user_id: None, 0: None}
        self._last_damage_roll_meta: dict | None = None
        
        # Setze Button-Labels (evtl. nach init_with_buffs erneut aufrufen)
        self.update_attack_buttons_mission()

    async def init_with_buffs(self) -> None:
        """Lädt Health- und Damage-Buffs für die Spielerkarte und aktualisiert HP/Buttons."""
        buffs = await get_card_buffs(self.user_id, self.player_card["name"])
        total_health = 0
        damage_map = {}
        for buff_type, attack_number, buff_amount in buffs:
            if buff_type == "health" and attack_number == 0:
                total_health += buff_amount
            elif buff_type == "damage" and 1 <= attack_number <= 4:
                damage_map[attack_number] = damage_map.get(attack_number, 0) + buff_amount
        self.health_bonus = total_health
        self.damage_bonuses = damage_map
        self.player_hp += self.health_bonus
        self.player_max_hp = self.player_hp
        # Buttons mit Buff-Labeln aktualisieren
        self.update_attack_buttons_mission()

    def mission_get_attack_max_damage(self, attack_damage, damage_buff: int = 0):
        if isinstance(attack_damage, list) and len(attack_damage) == 2:
            return attack_damage[1] + damage_buff
        return attack_damage + damage_buff

    def mission_get_attack_min_damage(self, attack_damage, damage_buff: int = 0):
        if isinstance(attack_damage, list) and len(attack_damage) == 2:
            return attack_damage[0] + damage_buff
        return attack_damage + damage_buff

    def mission_is_strong_attack(self, attack_damage, damage_buff: int = 0) -> bool:
        min_damage = self.mission_get_attack_min_damage(attack_damage, damage_buff)
        max_damage = self.mission_get_attack_max_damage(attack_damage, damage_buff)
        return min_damage > 90 and max_damage > 99

    def _status_icons(self, target_id: int) -> str:
        effects = self.active_effects.get(target_id, [])
        icons = []
        if any(e.get("type") == "burning" for e in effects):
            icons.append("\U0001f525")
        if any(e.get("type") == "confusion" for e in effects):
            icons.append("\U0001f300")
        if any(e.get("type") == "stealth" for e in effects):
            icons.append("\U0001f977")
        if any(e.get("type") == "airborne" for e in effects):
            icons.append("\u2708\ufe0f")
        return f" {' '.join(icons)}" if icons else ""

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
        return self.user_attack_cooldowns.get(attack_index, 0) > 0

    def is_attack_on_cooldown_bot(self, attack_index: int) -> bool:
        return self.bot_attack_cooldowns.get(attack_index, 0) > 0

    def start_attack_cooldown_user(self, attack_index: int, turns: int = 1) -> None:
        self.user_attack_cooldowns[attack_index] = turns

    def start_attack_cooldown_bot(self, attack_index: int, turns: int = 1) -> None:
        self.bot_attack_cooldowns[attack_index] = turns

    def is_reload_needed(self, player_id: int, attack_index: int) -> bool:
        return bool(self.manual_reload_needed.get(player_id, {}).get(attack_index, False))

    def set_reload_needed(self, player_id: int, attack_index: int, needed: bool) -> None:
        bucket = self.manual_reload_needed.setdefault(player_id, {})
        if needed:
            bucket[attack_index] = True
        else:
            bucket.pop(attack_index, None)

    def set_confusion(self, player_id: int, applier_id: int) -> None:
        self.confused_next_turn[player_id] = True
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "confusion"]
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({"type": "confusion", "duration": 1, "applier": applier_id})

    def consume_confusion_if_any(self, player_id: int) -> None:
        if self.confused_next_turn.get(player_id, False):
            self.confused_next_turn[player_id] = False
            try:
                self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "confusion"]
            except Exception:
                logging.exception("Unexpected error")

    def _find_effect(self, player_id: int, effect_type: str):
        for effect in self.active_effects.get(player_id, []):
            if effect.get("type") == effect_type:
                return effect
        return None

    def has_stealth(self, player_id: int) -> bool:
        return self._find_effect(player_id, "stealth") is not None

    def consume_stealth(self, player_id: int) -> bool:
        effect = self._find_effect(player_id, "stealth")
        if not effect:
            return False
        try:
            self.active_effects[player_id].remove(effect)
        except ValueError:
            pass
        return True

    def grant_stealth(self, player_id: int) -> None:
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "stealth"]
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({"type": "stealth", "duration": 1, "applier": player_id})

    def _append_effect_event(self, events: list[str], text: str) -> None:
        msg = str(text).strip()
        if msg:
            events.append(msg)

    def _grant_airborne(self, player_id: int) -> None:
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "airborne"]
        except Exception:
            self.active_effects[player_id] = []
        self.active_effects[player_id].append({"type": "airborne", "duration": 1, "applier": player_id})

    def _clear_airborne(self, player_id: int) -> None:
        try:
            self.active_effects[player_id] = [e for e in self.active_effects.get(player_id, []) if e.get("type") != "airborne"]
        except Exception:
            logging.exception("Unexpected error")

    def queue_delayed_defense(self, player_id: int, defense: str, counter: int = 0) -> None:
        defense_mode = str(defense or "").strip().lower()
        if defense_mode not in {"evade", "stealth"}:
            return
        self.delayed_defense_queue[player_id].append(
            {
                "defense": defense_mode,
                "counter": max(0, int(counter)),
            }
        )

    def activate_delayed_defense_after_attack(self, player_id: int, effect_events: list[str]) -> None:
        queued = list(self.delayed_defense_queue.get(player_id, []))
        if not queued:
            return
        self.delayed_defense_queue[player_id] = []
        for entry in queued:
            defense_mode = entry.get("defense")
            if defense_mode == "evade":
                counter = int(entry.get("counter", 0) or 0)
                self.queue_incoming_modifier(player_id, evade=True, counter=counter, turns=1)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird ausgewichen.")
            elif defense_mode == "stealth":
                self.grant_stealth(player_id)
                self._append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird vollständig geblockt.")

    def start_airborne_two_phase(
        self,
        player_id: int,
        landing_damage,
        effect_events: list[str],
        *,
        source_attack_index: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        if isinstance(landing_damage, list) and len(landing_damage) == 2:
            min_dmg = int(landing_damage[0])
            max_dmg = int(landing_damage[1])
        else:
            min_dmg = 20
            max_dmg = 40
        min_dmg = max(0, min_dmg)
        max_dmg = max(min_dmg, max_dmg)
        self.airborne_pending_landing[player_id] = {
            "damage": [min_dmg, max_dmg],
            "name": "Landungsschlag",
            "cooldown_attack_index": int(source_attack_index) if source_attack_index is not None else None,
            "cooldown_turns": max(0, int(cooldown_turns or 0)),
        }
        self.queue_incoming_modifier(player_id, evade=True, counter=0, turns=1)
        self._grant_airborne(player_id)
        self._append_effect_event(effect_events, "Flugphase aktiv: Der nächste gegnerische Angriff verfehlt.")

    def resolve_forced_landing_if_due(self, player_id: int, effect_events: list[str]) -> dict | None:
        pending = self.airborne_pending_landing.get(player_id)
        if not pending:
            return None
        self.airborne_pending_landing[player_id] = None
        self._clear_airborne(player_id)
        self._append_effect_event(effect_events, "Landungsschlag wurde automatisch ausgelöst.")
        damage = pending.get("damage", [20, 40])
        if isinstance(damage, list) and len(damage) == 2:
            damage_data = [int(damage[0]), int(damage[1])]
        else:
            damage_data = [20, 40]
        return {
            "name": str(pending.get("name") or "Landungsschlag"),
            "damage": damage_data,
            "info": "Automatischer Folgetreffer aus der Flugphase.",
            "cooldown_attack_index": pending.get("cooldown_attack_index"),
            "cooldown_turns": int(pending.get("cooldown_turns", 0) or 0),
        }

    def _max_hp_for(self, player_id: int) -> int:
        return self.player_max_hp if player_id == self.user_id else self.bot_max_hp

    def _hp_for(self, player_id: int) -> int:
        return self.player_hp if player_id == self.user_id else self.bot_hp

    def _set_hp_for(self, player_id: int, value: int) -> None:
        if player_id == self.user_id:
            self.player_hp = max(0, value)
        else:
            self.bot_hp = max(0, value)

    def heal_player(self, player_id: int, amount: int) -> int:
        if amount <= 0:
            return 0
        before = self._hp_for(player_id)
        after = min(self._max_hp_for(player_id), before + amount)
        self._set_hp_for(player_id, after)
        return after - before

    def queue_incoming_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        reflect: float = 0.0,
        store_ratio: float = 0.0,
        cap: int | None = None,
        evade: bool = False,
        counter: int = 0,
        turns: int = 1,
    ) -> None:
        if turns <= 0:
            turns = 1
        for _ in range(turns):
            self.incoming_modifiers[player_id].append(
                {
                    "percent": max(0.0, float(percent)),
                    "flat": max(0, int(flat)),
                    "reflect": max(0.0, float(reflect)),
                    "store_ratio": max(0.0, float(store_ratio)),
                    "cap": int(cap) if cap is not None else None,
                    "evade": bool(evade),
                    "counter": max(0, int(counter)),
                }
            )

    def queue_outgoing_attack_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        turns: int = 1,
    ) -> None:
        if turns <= 0:
            turns = 1
        for _ in range(turns):
            self.outgoing_attack_modifiers[player_id].append(
                {
                    "percent": max(0.0, float(percent)),
                    "flat": max(0, int(flat)),
                }
            )

    def apply_outgoing_attack_modifiers(self, attacker_id: int, raw_damage: int) -> tuple[int, int]:
        if raw_damage <= 0 or not self.outgoing_attack_modifiers.get(attacker_id):
            return max(0, int(raw_damage)), 0
        mod = self.outgoing_attack_modifiers[attacker_id].pop(0)
        return apply_outgoing_attack_modifier(
            raw_damage,
            percent=float(mod.get("percent", 0.0) or 0.0),
            flat=int(mod.get("flat", 0) or 0),
        )

    def consume_guaranteed_hit(self, player_id: int) -> bool:
        if self.guaranteed_hit_next.get(player_id, 0) <= 0:
            return False
        self.guaranteed_hit_next[player_id] -= 1
        if self.guaranteed_hit_next[player_id] < 0:
            self.guaranteed_hit_next[player_id] = 0
        return True

    def roll_attack_damage(
        self,
        attack: dict,
        base_damage,
        damage_buff: int,
        attack_multiplier: float,
        force_max_damage: bool,
        guaranteed_hit: bool,
    ) -> tuple[int, bool, int, int]:
        multi_hit = attack.get("multi_hit")
        if isinstance(multi_hit, dict):
            actual_damage, min_damage, max_damage, details = resolve_multi_hit_damage(
                multi_hit,
                buff_amount=damage_buff,
                attack_multiplier=attack_multiplier,
                force_max=force_max_damage,
                guaranteed_hit=guaranteed_hit,
                return_details=True,
            )
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
            is_critical = True
        return actual_damage, is_critical, min_damage, max_damage

    def resolve_incoming_modifiers(self, defender_id: int, raw_damage: int, ignore_evade: bool = False) -> tuple[int, int, bool, int]:
        if raw_damage <= 0 or not self.incoming_modifiers.get(defender_id):
            return raw_damage, 0, False, 0
        mod = self.incoming_modifiers[defender_id].pop(0)
        if mod.get("evade") and not ignore_evade:
            return 0, 0, True, int(mod.get("counter", 0) or 0)

        damage = max(0, int(raw_damage))
        prevented = 0

        percent = float(mod.get("percent", 0.0) or 0.0)
        if percent > 0:
            cut = int(round(damage * percent))
            damage -= cut
            prevented += cut

        flat = int(mod.get("flat", 0) or 0)
        if flat > 0:
            cut = min(flat, damage)
            damage -= cut
            prevented += cut

        cap = mod.get("cap")
        if cap is not None and damage > int(cap):
            cut = damage - int(cap)
            damage = int(cap)
            prevented += cut

        reflect_ratio = float(mod.get("reflect", 0.0) or 0.0)
        reflected = int(round(prevented * reflect_ratio)) if reflect_ratio > 0 else 0

        store_ratio = float(mod.get("store_ratio", 0.0) or 0.0)
        if store_ratio > 0 and prevented > 0:
            self.absorbed_damage[defender_id] += int(round(prevented * store_ratio))

        return max(0, damage), max(0, reflected), False, 0

    def apply_regen_tick(self, player_id: int) -> int:
        total = 0
        remove: list[dict] = []
        for effect in self.active_effects.get(player_id, []):
            if effect.get("type") != "regen":
                continue
            heal = int(effect.get("heal", 0) or 0)
            total += self.heal_player(player_id, heal)
            effect["duration"] = int(effect.get("duration", 0) or 0) - 1
            if effect["duration"] <= 0:
                remove.append(effect)
        for effect in remove:
            try:
                self.active_effects[player_id].remove(effect)
            except ValueError:
                pass
        return total

    def reduce_cooldowns_user(self) -> None:
        for idx in list(self.user_attack_cooldowns.keys()):
            self.user_attack_cooldowns[idx] -= 1
            if self.user_attack_cooldowns[idx] <= 0:
                del self.user_attack_cooldowns[idx]

    def reduce_cooldowns_bot(self) -> None:
        for idx in list(self.bot_attack_cooldowns.keys()):
            self.bot_attack_cooldowns[idx] -= 1
            if self.bot_attack_cooldowns[idx] <= 0:
                del self.bot_attack_cooldowns[idx]

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
                        btn.label = f"{blocked_name} (Cooldown: {cooldown_turns})"
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
                dmg_buff = self.damage_bonuses.get(i + 1, 0)
                
                # Berechne Damage-Text
                if isinstance(base_damage, list) and len(base_damage) == 2:
                    min_dmg, max_dmg = base_damage[0] + dmg_buff, base_damage[1] + dmg_buff
                    damage_text = f"[{min_dmg}, {max_dmg}]"
                else:
                    damage_text = str(base_damage + dmg_buff)
                
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
                    button.label = f"{attack['name']} (Cooldown: {cooldown_turns})"
                    button.disabled = True
                elif is_reload_action:
                    button.style = discord.ButtonStyle.primary
                    button.label = str(attack.get("reload_name") or "Nachladen")
                    button.disabled = False
                else:
                    if heal_label is not None:
                        button.style = discord.ButtonStyle.success
                        button.label = f"{attack['name']} (+{heal_label}){effects_label}"
                    else:
                        # Rot für normale Attacken
                        button.style = discord.ButtonStyle.danger
                        button.label = f"{attack['name']} ({damage_text}){effects_label}"
                    button.disabled = False
            else:
                button.label = f"Angriff {i+1}"

    # Angriffs-Buttons (rot, 2x2 Grid)
    @ui.button(label="Angriff 1", style=discord.ButtonStyle.danger, row=0)
    async def attack1(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 0)

    @ui.button(label="Angriff 2", style=discord.ButtonStyle.danger, row=0)
    async def attack2(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 1)

    @ui.button(label="Angriff 3", style=discord.ButtonStyle.danger, row=1)
    async def attack3(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 2)

    @ui.button(label="Angriff 4", style=discord.ButtonStyle.danger, row=1)
    async def attack4(self, interaction: discord.Interaction, button: ui.Button):
        await self.execute_attack(interaction, 3)

    # Blaue Buttons unten
    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)
            return
        self.result = False  # Mission abgebrochen
        self.stop()
        await interaction.response.edit_message(content="Mission abgebrochen.", view=None)

    # Entfernt: Platzhalter-Button

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
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
            if effect['applier'] == self.user_id and effect['type'] == 'burning':
                damage = effect['damage']
                self.bot_hp -= damage
                self.bot_hp = max(0, self.bot_hp)
                pre_burn_total += damage

                # Decrease duration
                effect['duration'] -= 1
                if effect['duration'] <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        # Hole Angriff
        if attack_index >= len(self.attacks):
            await interaction.response.send_message("Ungültiger Angriff!", ephemeral=True)
            return

        # COOLDOWN prüfen (Spieler)
        if (not is_forced_landing) and self.is_attack_on_cooldown_user(attack_index):
            await interaction.response.send_message("Diese Attacke ist noch auf Cooldown!", ephemeral=True)
            return
        if (not is_forced_landing) and self.special_lock_next_turn.get(self.user_id, False) and attack_index != 0:
            await interaction.response.send_message(
                "Diese Runde sind nur Standard-Angriffe erlaubt (Attacke 1).",
                ephemeral=True,
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
        dmg_buff = self.damage_bonuses.get(attack_index + 1, 0)
        if is_forced_landing:
            dmg_buff = 0

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
            if isinstance(damage_if_condition, list) and len(damage_if_condition) == 2:
                damage = [int(damage_if_condition[0]), int(damage_if_condition[1])]
            elif isinstance(damage_if_condition, int):
                damage = [damage_if_condition, damage_if_condition]
        if attack.get("add_absorbed_damage"):
            dmg_buff += int(self.absorbed_damage.get(self.user_id, 0))
            self.absorbed_damage[self.user_id] = 0

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
        if is_reload_action:
            actual_damage, is_critical = 0, False
            hits_enemy = False
            self.set_reload_needed(self.user_id, attack_index, False)
        else:
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
                    self.player_hp -= self_damage
                    self.player_hp = max(0, self.player_hp)
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
                    else:
                        self.bot_hp -= actual_damage
                        self.bot_hp = max(0, self.bot_hp)
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
                else:
                    self.bot_hp -= actual_damage
                    self.bot_hp = max(0, self.bot_hp)

            if hits_enemy and actual_damage > 0:
                boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                reduced_damage, overflow_self_damage = self.apply_outgoing_attack_modifiers(self.user_id, actual_damage)
                if reduced_damage != actual_damage:
                    delta_out = actual_damage - reduced_damage
                    if delta_out > 0:
                        self.bot_hp += delta_out
                    actual_damage = reduced_damage
                    self._append_effect_event(effect_events, f"Ausgehender Schaden wurde um {delta_out} reduziert.")
                if overflow_self_damage > 0:
                    self.player_hp -= overflow_self_damage
                    self._append_effect_event(effect_events, f"Überlauf-Rückstoß: {overflow_self_damage} Selbstschaden.")

                final_damage, reflected_damage, dodged, counter_damage = self.resolve_incoming_modifiers(
                    0,
                    actual_damage,
                    ignore_evade=guaranteed_hit,
                )
                if dodged:
                    self.bot_hp += actual_damage
                    actual_damage = 0
                    hits_enemy = False
                elif final_damage != actual_damage:
                    delta = actual_damage - final_damage
                    if delta > 0:
                        self.bot_hp += delta
                    actual_damage = final_damage
                if reflected_damage > 0:
                    self.player_hp -= reflected_damage
                if counter_damage > 0:
                    self.player_hp -= counter_damage

        self_damage_value = int(attack.get("self_damage", 0) or 0)
        if self_damage_value > 0:
            self.player_hp -= self_damage_value
            self._append_effect_event(effect_events, f"Rückstoß: {self_damage_value} Selbstschaden.")

        heal_data = attack.get("heal")
        if heal_data is not None:
            if isinstance(heal_data, list) and len(heal_data) == 2:
                heal_amount = random.randint(int(heal_data[0]), int(heal_data[1]))
            else:
                heal_amount = int(heal_data)
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
            self.activate_delayed_defense_after_attack(self.user_id, effect_events)

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
                duration = random.randint(effect["duration"][0], effect["duration"][1])
                self.active_effects[target_id].append({
                    'type': 'burning',
                    'duration': duration,
                    'damage': effect['damage'],
                    'applier': self.user_id
                })
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                self._append_effect_event(effect_events, f"Verbrennung aktiv: {effect['damage']} Schaden für {duration} Runden.")
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
                self._append_effect_event(effect_events, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e).")
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                pct = int(round((mult - 1.0) * 100))
                if pct > 0:
                    self._append_effect_event(effect_events, f"Nächster Angriff macht +{pct}% Schaden.")
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff verursacht Maximalschaden.")
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                self._append_effect_event(effect_events, "Nächster Angriff trifft garantiert.")
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n)).")
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1)
                    if sequence:
                        seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                        self._append_effect_event(effect_events, f"Block-Sequenz vorbereitet: {seq_text}.")
            elif eff_type == "damage_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n)).")
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden.")
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = int(effect.get("amount", 0) or 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns)
                self._append_effect_event(effect_events, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß).")
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1)
                self._append_effect_event(effect_events, "Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen.")
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1)
                self._append_effect_event(effect_events, "Absorption aktiv: Verhinderter Schaden wird gespeichert.")
            elif eff_type == "cap_damage":
                max_damage = int(effect.get("max_damage", 0) or 0)
                self.queue_incoming_modifier(target_id, cap=max_damage, turns=1)
                self._append_effect_event(effect_events, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer.")
            elif eff_type == "evade":
                counter = int(effect.get("counter", 0) or 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1)
                self._append_effect_event(effect_events, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt.")
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
                if isinstance(heal_data_effect, list) and len(heal_data_effect) == 2:
                    heal_amount = random.randint(int(heal_data_effect[0]), int(heal_data_effect[1]))
                else:
                    heal_amount = int(heal_data_effect or 0)
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
                self.queue_delayed_defense(target_id, defense_mode, counter=counter)
                self._append_effect_event(effect_events, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv.")
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
                pre_effect_damage=pre_burn_total,
                confusion_applied=confusion_applied,
                self_hit_damage=(self_damage if not hits_enemy and 'self_damage' in locals() else 0),
                attacker_status_icons=self._status_icons(self.user_id),
                defender_status_icons=self._status_icons(0),
                effect_events=effect_events,
            )
            await self._safe_edit_battle_log(log_embed)

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
            await interaction.response.edit_message(content=f"🏆 **Welle {self.wave_num} gewonnen!** Du hast **{self.bot_card['name']}** besiegt!", view=None)
            self.stop()
            return
        if self.player_hp <= 0:
            self.result = False
            await interaction.response.edit_message(content=f"❌ **Welle {self.wave_num} verloren!** Du hast dich selbst besiegt.", view=None)
            self.stop()
            return
        
        # Bot-Zug nach kurzer Pause
        await interaction.response.edit_message(content=f"🎯 Du hast **{attack_name}** verwendet! **{self.bot_card['name']}** ist an der Reihe...", view=None)

        # SIDE EFFECTS: Apply effects on player before bot attack
        defender_id = self.user_id
        effects_to_remove = []
        pre_burn_total_player = 0
        for effect in self.active_effects[defender_id]:
            if effect['applier'] == 0 and effect['type'] == 'burning':
                damage = effect['damage']
                self.player_hp -= damage
                self.player_hp = max(0, self.player_hp)
                pre_burn_total_player += damage

                # Decrease duration
                effect['duration'] -= 1
                if effect['duration'] <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        self.apply_regen_tick(0)

        if self.stunned_next_turn.get(0, False):
            self.stunned_next_turn[0] = False
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
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
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
                if isinstance(damage_if_condition, list) and len(damage_if_condition) == 2:
                    damage = [int(damage_if_condition[0]), int(damage_if_condition[1])]
                elif isinstance(damage_if_condition, int):
                    damage = [damage_if_condition, damage_if_condition]
            if attack.get("add_absorbed_damage"):
                dmg_buff_bot += int(self.absorbed_damage.get(0, 0))
                self.absorbed_damage[0] = 0
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
                        self.bot_hp -= self_damage
                        self.bot_hp = max(0, self.bot_hp)
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
                        else:
                            self.player_hp -= actual_damage
                            self.player_hp = max(0, self.player_hp)
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
                    else:
                        self.player_hp -= actual_damage
                        self.player_hp = max(0, self.player_hp)

                if bot_hits_enemy and actual_damage > 0:
                    boost_text = _boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                    if boost_text:
                        self._append_effect_event(bot_effect_events, boost_text)
                    reduced_damage, overflow_self_damage = self.apply_outgoing_attack_modifiers(0, actual_damage)
                    if reduced_damage != actual_damage:
                        delta_out = actual_damage - reduced_damage
                        if delta_out > 0:
                            self.player_hp += delta_out
                        actual_damage = reduced_damage
                        self._append_effect_event(bot_effect_events, f"Ausgehender Schaden wurde um {delta_out} reduziert.")
                    if overflow_self_damage > 0:
                        self.bot_hp -= overflow_self_damage
                        self._append_effect_event(bot_effect_events, f"Überlauf-Rückstoß: {overflow_self_damage} Selbstschaden.")

                    final_damage, reflected_damage, dodged, counter_damage = self.resolve_incoming_modifiers(
                        self.user_id,
                        actual_damage,
                        ignore_evade=guaranteed_hit,
                    )
                    if dodged:
                        self.player_hp += actual_damage
                        actual_damage = 0
                        bot_hits_enemy = False
                    elif final_damage != actual_damage:
                        delta = actual_damage - final_damage
                        if delta > 0:
                            self.player_hp += delta
                        actual_damage = final_damage
                    if reflected_damage > 0:
                        self.bot_hp -= reflected_damage
                    if counter_damage > 0:
                        self.bot_hp -= counter_damage

            self_damage_value = int(attack.get("self_damage", 0) or 0)
            if self_damage_value > 0:
                self.bot_hp -= self_damage_value
                self._append_effect_event(bot_effect_events, f"Rückstoß: {self_damage_value} Selbstschaden.")

            heal_data = attack.get("heal")
            if heal_data is not None:
                if isinstance(heal_data, list) and len(heal_data) == 2:
                    heal_amount = random.randint(int(heal_data[0]), int(heal_data[1]))
                else:
                    heal_amount = int(heal_data)
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
                self.activate_delayed_defense_after_attack(0, bot_effect_events)

            # SIDE EFFECTS: Apply new effects from bot attack
            effects = attack.get("effects", [])
            burning_duration_for_dynamic_cooldown: int | None = None
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
                    duration = random.randint(effect["duration"][0], effect["duration"][1])
                    new_effect = {
                        'type': 'burning',
                        'duration': duration,
                        'damage': effect['damage'],
                        'applier': 0
                    }
                    self.active_effects[target_id].append(new_effect)
                    if attack.get("cooldown_from_burning_plus") is not None:
                        prev_duration = burning_duration_for_dynamic_cooldown or 0
                        burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
                    self._append_effect_event(bot_effect_events, f"Verbrennung aktiv: {effect['damage']} Schaden für {duration} Runden.")
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
                    self._append_effect_event(bot_effect_events, f"Schadensbonus aktiv: +{amount} für {uses} Angriff(e).")
                elif eff_type == "damage_multiplier":
                    mult = float(effect.get("multiplier", 1.0) or 1.0)
                    uses = int(effect.get("uses", 1) or 1)
                    self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                    self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
                    pct = int(round((mult - 1.0) * 100))
                    if pct > 0:
                        self._append_effect_event(bot_effect_events, f"Nächster Angriff macht +{pct}% Schaden.")
                elif eff_type == "force_max":
                    uses = int(effect.get("uses", 1) or 1)
                    self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
                    self._append_effect_event(bot_effect_events, "Nächster Angriff verursacht Maximalschaden.")
                elif eff_type == "guaranteed_hit":
                    uses = int(effect.get("uses", 1) or 1)
                    self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
                    self._append_effect_event(bot_effect_events, "Nächster Angriff trifft garantiert.")
                elif eff_type == "damage_reduction":
                    percent = float(effect.get("percent", 0.0) or 0.0)
                    turns = int(effect.get("turns", 1) or 1)
                    self.queue_incoming_modifier(target_id, percent=percent, turns=turns)
                    self._append_effect_event(bot_effect_events, f"Eingehender Schaden reduziert um {int(round(percent * 100))}% ({turns} Runde(n)).")
                elif eff_type == "damage_reduction_sequence":
                    sequence = effect.get("sequence", [])
                    if isinstance(sequence, list):
                        for pct in sequence:
                            self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1)
                        if sequence:
                            seq_text = " -> ".join(f"{int(round(float(p) * 100))}%" for p in sequence)
                            self._append_effect_event(bot_effect_events, f"Block-Sequenz vorbereitet: {seq_text}.")
                elif eff_type == "damage_reduction_flat":
                    amount = int(effect.get("amount", 0) or 0)
                    turns = int(effect.get("turns", 1) or 1)
                    self.queue_incoming_modifier(target_id, flat=amount, turns=turns)
                    self._append_effect_event(bot_effect_events, f"Eingehender Schaden reduziert um {amount} ({turns} Runde(n)).")
                elif eff_type == "enemy_next_attack_reduction_percent":
                    percent = float(effect.get("percent", 0.0) or 0.0)
                    turns = int(effect.get("turns", 1) or 1)
                    self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns)
                    self._append_effect_event(bot_effect_events, f"Nächster gegnerischer Angriff: -{int(round(percent * 100))}% Schaden.")
                elif eff_type == "enemy_next_attack_reduction_flat":
                    amount = int(effect.get("amount", 0) or 0)
                    turns = int(effect.get("turns", 1) or 1)
                    self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns)
                    self._append_effect_event(bot_effect_events, f"Nächster gegnerischer Angriff: -{amount} Schaden (mit Überlauf-Rückstoß).")
                elif eff_type == "reflect":
                    reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                    reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                    self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, turns=1)
                    self._append_effect_event(bot_effect_events, "Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen.")
                elif eff_type == "absorb_store":
                    percent = float(effect.get("percent", 0.0) or 0.0)
                    self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, turns=1)
                    self._append_effect_event(bot_effect_events, "Absorption aktiv: Verhinderter Schaden wird gespeichert.")
                elif eff_type == "cap_damage":
                    max_damage = int(effect.get("max_damage", 0) or 0)
                    self.queue_incoming_modifier(target_id, cap=max_damage, turns=1)
                    self._append_effect_event(bot_effect_events, f"Schadenslimit aktiv: Maximal {max_damage} Schaden beim nächsten Treffer.")
                elif eff_type == "evade":
                    counter = int(effect.get("counter", 0) or 0)
                    self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1)
                    self._append_effect_event(bot_effect_events, "Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt.")
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
                    if isinstance(heal_data_effect, list) and len(heal_data_effect) == 2:
                        heal_amount = random.randint(int(heal_data_effect[0]), int(heal_data_effect[1]))
                    else:
                        heal_amount = int(heal_data_effect or 0)
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
                    self.queue_delayed_defense(target_id, defense_mode, counter=counter)
                    self._append_effect_event(bot_effect_events, "Schutz vorbereitet: Wird nach dem nächsten eigenen Angriff aktiv.")
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
                    pre_effect_damage=pre_burn_total_player,
                    attacker_status_icons=self._status_icons(0),
                    defender_status_icons=self._status_icons(self.user_id),
                    effect_events=bot_effect_events,
                )
                await self._safe_edit_battle_log(log_embed)

            if (not is_forced_bot_landing) and (not is_bot_reload_action) and attack.get("requires_reload"):
                self.set_reload_needed(0, best_index, True)

            if self.special_lock_next_turn.get(0, False):
                self.special_lock_next_turn[0] = False

            if self.player_hp <= 0:
                self.result = False
                await interaction.followup.send(f"❌ **Welle {self.wave_num} verloren!** **{self.bot_card['name']}** hat dich besiegt!", ephemeral=True)
                self.stop()
                return

            if not is_forced_bot_landing:
                # Cooldown für Bot (kartenspezifisch oder stark)
                dynamic_cooldown_turns = _resolve_dynamic_cooldown_from_burning(
                    attack,
                    burning_duration_for_dynamic_cooldown,
                )
                custom_cooldown_turns = attack.get("cooldown_turns")
                starts_after_landing = _starts_cooldown_after_landing(attack)
                if dynamic_cooldown_turns > 0:
                    current_cd = self.bot_attack_cooldowns.get(best_index, 0)
                    self.bot_attack_cooldowns[best_index] = max(current_cd, dynamic_cooldown_turns)
                    bonus_for_dynamic_cd = max(0, int(attack.get("cooldown_from_burning_plus", 0) or 0))
                    self._append_effect_event(
                        bot_effect_events,
                        f"Gammastrahl-Abklingzeit: {dynamic_cooldown_turns} (Effektdauer {burning_duration_for_dynamic_cooldown} + {bonus_for_dynamic_cd}).",
                    )
                    self.reduce_cooldowns_bot()
                elif (not starts_after_landing) and isinstance(custom_cooldown_turns, int) and custom_cooldown_turns > 0:
                    current_cd = self.bot_attack_cooldowns.get(best_index, 0)
                    self.bot_attack_cooldowns[best_index] = max(current_cd, custom_cooldown_turns)
                    self.reduce_cooldowns_bot()
                elif self.mission_is_strong_attack(damage, dmg_buff_bot):
                    self.start_attack_cooldown_bot(best_index, 2)
                    # Reduziere Cooldowns für den Bot direkt nach seinem Zug (entspricht /fight)
                    self.reduce_cooldowns_bot()
            else:
                landing_cd_index = forced_bot_landing_attack.get("cooldown_attack_index")
                landing_cd_turns = int(forced_bot_landing_attack.get("cooldown_turns", 0) or 0)
                if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
                    current_cd = self.bot_attack_cooldowns.get(landing_cd_index, 0)
                    self.bot_attack_cooldowns[landing_cd_index] = max(current_cd, landing_cd_turns)
                    # Reduziere Cooldowns für den Bot direkt nach seinem Zug (entspricht /fight)
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

            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
        else:
            # Bot hat keine Attacken verfügbar (alle auf Cooldown) - überspringe Bot-Zug
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
            
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)

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
    kwargs = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs)
    return await interaction.response.send_message(**kwargs)

def _get_bot_member(interaction: discord.Interaction) -> discord.Member | None:
    if interaction.guild is None or interaction.client.user is None:
        return None
    return interaction.guild.get_member(interaction.client.user.id) or interaction.guild.me

def _can_send_in_channel(channel: discord.abc.GuildChannel | discord.Thread, member: discord.Member | None) -> bool:
    if member is None:
        return True
    perms = channel.permissions_for(member)
    if not perms.view_channel:
        return False
    if isinstance(channel, discord.Thread):
        return perms.send_messages_in_threads
    return perms.send_messages

async def _safe_send_channel(
    interaction: discord.Interaction,
    channel: discord.abc.Messageable,
    *,
    content: str | None = None,
    embed=None,
    view=None,
) -> discord.Message | None:
    guild_id = getattr(channel, "guild", None).id if getattr(channel, "guild", None) else None
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if not await is_channel_allowed_ids(guild_id, channel_id, parent_id):
        return None
    try:
        return await channel.send(content=content, embed=embed, view=view)
    except discord.Forbidden:
        await _send_ephemeral(
            interaction,
            content="❌ Mir fehlen Rechte in diesem Kanal/Thread (View/Send/Thread-Rechte). Bitte gib mir Zugriff.",
        )
        return None

async def _fetch_channel_safe(channel_id: int | None):
    if not channel_id:
        return None
    try:
        return await bot.fetch_channel(channel_id)
    except Exception:
        return None

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
            view = ChallengeResponseView(
                int(row["challenger_id"]),
                int(row["challenged_id"]),
                row["challenger_card"],
                request_id=int(row["id"]),
                thread_id=int(row["thread_id"]) if row["thread_id"] else None,
                thread_created=bool(row["thread_created"]),
            )
            msg = await channel.send(
                content=f"<@{row['challenged_id']}>, du wurdest zu einem 1v1 Kartenkampf herausgefordert!",
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            await update_fight_request_message(int(row["id"]), msg.id, msg.channel.id)
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
            try:
                mission_data = json.loads(row["mission_data"]) if row["mission_data"] else {}
            except Exception:
                mission_data = {}
            embed = _build_mission_embed(mission_data)
            view = MissionAcceptView(
                int(row["user_id"]),
                mission_data,
                request_id=int(row["id"]),
                visibility=row["visibility"] or VISIBILITY_PRIVATE,
                is_admin=bool(row["is_admin"]),
            )
            msg = await channel.send(embed=embed, view=view)
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

def _visibility_label(value: str) -> str:
    return "öffentlich" if value == VISIBILITY_PUBLIC else "nur sichtbar"

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
    try:
        all_cmds = bot.tree.get_commands()
    except Exception:
        all_cmds = []
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

async def get_message_visibility(guild_id: int | None, message_key: str) -> str:
    if not guild_id:
        return VISIBILITY_PRIVATE
    override = await get_visibility_override(guild_id, message_key)
    if override:
        return override
    legacy_key = LEGACY_COMMAND_VISIBILITY_KEYS.get(message_key)
    if legacy_key:
        legacy_override = await get_visibility_override(guild_id, legacy_key)
        if legacy_override:
            return legacy_override
    return VISIBILITY_PRIVATE

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
    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs)
    return await interaction.response.send_message(**kwargs)

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
    try:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
    except discord.InteractionResponded:
        await interaction.followup.edit_message(interaction.message.id, content=content, embed=embed, view=view)

async def _select_user(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await _send_ephemeral(interaction, content="Nur in Servern verfügbar.")
        return None, None
    view = AdminUserSelectView(interaction.user.id, interaction.guild)
    await _send_ephemeral(interaction, content=prompt, view=view)
    await view.wait()
    if not view.value:
        return None, None
    user_id = int(view.value)
    member = interaction.guild.get_member(user_id)
    if member:
        return user_id, member.display_name
    try:
        user = await interaction.client.fetch_user(user_id)
        return user_id, user.display_name
    except Exception:
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
    embed.add_field(name="Error Count", value=str(ERROR_COUNT), inline=True)
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
    issues = []
    for idx, card in enumerate(karten, start=1):
        name = card.get("name")
        beschreibung = card.get("beschreibung")
        bild = card.get("bild")
        seltenheit = card.get("seltenheit")
        if not name:
            issues.append(f"{idx}: fehlt name")
        if not beschreibung:
            issues.append(f"{idx}: fehlt beschreibung")
        if not bild or not isinstance(bild, str) or not bild.startswith("http"):
            issues.append(f"{idx}: bild ist ungueltig")
        if not seltenheit:
            issues.append(f"{idx}: fehlt seltenheit")
        hp = card.get("hp")
        if hp is not None and not isinstance(hp, int):
            issues.append(f"{idx}: hp ist kein int")
        attacks = card.get("attacks")
        if attacks is not None:
            if not isinstance(attacks, list):
                issues.append(f"{idx}: attacks ist keine Liste")
            else:
                for a_i, atk in enumerate(attacks, start=1):
                    if not isinstance(atk, dict):
                        issues.append(f"{idx}.{a_i}: attack ist kein dict")
                        continue
                    if "name" not in atk or "damage" not in atk:
                        issues.append(f"{idx}.{a_i}: attack fehlt name/damage")
                        continue
                    dmg = atk.get("damage")
                    if isinstance(dmg, list):
                        if len(dmg) != 2 or not all(isinstance(x, int) for x in dmg):
                            issues.append(f"{idx}.{a_i}: damage list ungueltig")
                    elif not isinstance(dmg, int):
                        issues.append(f"{idx}.{a_i}: damage ist kein int")
                    info_text = atk.get("info")
                    if info_text is not None and not isinstance(info_text, str):
                        issues.append(f"{idx}.{a_i}: info ist kein string")
                    guaranteed_cond = atk.get("guaranteed_hit_if_condition")
                    if guaranteed_cond is not None and not isinstance(guaranteed_cond, bool):
                        issues.append(f"{idx}.{a_i}: guaranteed_hit_if_condition ist kein bool")
                    multi_hit = atk.get("multi_hit")
                    if multi_hit is not None:
                        if not isinstance(multi_hit, dict):
                            issues.append(f"{idx}.{a_i}: multi_hit ist kein dict")
                        else:
                            hits = multi_hit.get("hits")
                            hit_chance = multi_hit.get("hit_chance")
                            per_hit_damage = multi_hit.get("per_hit_damage")
                            if not isinstance(hits, int) or hits <= 0:
                                issues.append(f"{idx}.{a_i}: multi_hit.hits ungueltig")
                            if not isinstance(hit_chance, (int, float)):
                                issues.append(f"{idx}.{a_i}: multi_hit.hit_chance ungueltig")
                            if not (isinstance(per_hit_damage, list) and len(per_hit_damage) == 2 and all(isinstance(x, int) for x in per_hit_damage)):
                                issues.append(f"{idx}.{a_i}: multi_hit.per_hit_damage ungueltig")
    if not issues:
        await _send_with_visibility(interaction, visibility_key, content="karten.py ist valide.")
        return
    preview = "\n".join(issues[:20])
    more = len(issues) - 20
    if more > 0:
        preview += f"\n... +{more} weitere"
    await _send_with_visibility(interaction, visibility_key, content=f"Probleme gefunden:\n{preview}")

async def send_configure_add(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content="Nur in Servern verfügbar.")
        return
    async with db_context() as db:
        await db.execute(
            "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
            (interaction.guild_id, interaction.channel_id),
        )
        await db.commit()
    logging.info("Configure add channel: actor=%s guild=%s channel=%s", interaction.user.id, interaction.guild_id, interaction.channel_id)
    await _send_with_visibility(interaction, visibility_key, content=f"✅ Hinzugefügt: {interaction.channel.mention}")

async def send_configure_remove(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content="Nur in Servern verfügbar.")
        return
    async with db_context() as db:
        await db.execute(
            "DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild_id, interaction.channel_id),
        )
        await db.commit()
    logging.info("Configure remove channel: actor=%s guild=%s channel=%s", interaction.user.id, interaction.guild_id, interaction.channel_id)
    await _send_with_visibility(interaction, visibility_key, content=f"🗑️ Entfernt: {interaction.channel.mention}")

async def send_configure_list(interaction: discord.Interaction, visibility_key: str | None = None):
    if interaction.guild is None:
        await _send_with_visibility(interaction, visibility_key, content="Nur in Servern verfügbar.")
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
        await _send_with_visibility(interaction, visibility_key, content="Nur in Servern verfügbar.")
        return
    async with db_context() as db:
        await db.execute(
            "DELETE FROM user_seen_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild.id, interaction.channel.id),
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
        await _send_with_visibility(interaction, visibility_key, content="Nur in Servern verfügbar.")
        return
    target_user = interaction.guild.get_member(user_id)
    mention = target_user.mention if target_user else f"<@{user_id}>"
    user_karten = await get_user_karten(user_id)
    infinitydust = await get_infinitydust(user_id)
    if not user_karten and infinitydust == 0:
        await _send_with_visibility(interaction, visibility_key, content=f"❌ {mention} hat noch keine Karten in seiner Sammlung.")
        return
    embed = discord.Embed(
        title=f"🔍 Vault von {user_name}",
        description=f"**{mention}** besitzt **{len(user_karten)}** verschiedene Karten:",
    )
    if infinitydust > 0:
        embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    for kartenname, anzahl in user_karten:
        karte = await get_karte_by_name(kartenname)
        if karte:
            embed.add_field(name=f"{karte['name']} (x{anzahl})", value=karte['beschreibung'][:100] + "...", inline=False)
    embed.set_footer(text=f"Vault-Lookup durch {interaction.user.display_name}")
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

    try:
        all_cmds = bot.tree.get_commands()
    except Exception:
        all_cmds = []
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
    embed.set_footer(text=f"Angefordert von {interaction.user.display_name} | {time.strftime('%d.%m.%Y %H:%M:%S')}")
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
            await _send_with_visibility(interaction, "maintenance", content="Nur in Servern verfügbar.")
            return
        await set_maintenance_mode(interaction.guild_id, True)
        logging.info("Maintenance ON by %s in guild %s", interaction.user.id, interaction.guild_id)
        await _send_with_visibility(interaction, "maintenance", content="Wartungsmodus aktiviert.")
        return
    if action == "maintenance_off":
        if interaction.guild is None:
            await _send_with_visibility(interaction, "maintenance", content="Nur in Servern verfügbar.")
            return
        await set_maintenance_mode(interaction.guild_id, False)
        logging.info("Maintenance OFF by %s in guild %s", interaction.user.id, interaction.guild_id)
        await _send_with_visibility(interaction, "maintenance", content="Wartungsmodus deaktiviert.")
        return
    if action == "delete_user":
        user_id, user_name = await _select_user(interaction, "Wähle den Nutzer für Löschen:")
        if not user_id:
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
        if not user_id:
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
        if not user_id:
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

@bot.tree.command(name="panel", description="Dev-Panel (nur Basti/Dev)")
async def panel(interaction: discord.Interaction):
    if not await require_owner_or_dev(interaction):
        return
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    embed = discord.Embed(title="Panel", description="Hauptmenü")
    await _send_with_visibility(interaction, visibility_key, embed=embed, view=PanelHomeView(interaction.user.id))

# /balance stats (oeffentlich)
BALANCE_GROUP = app_commands.Group(name="balance", description="Balance-Statistiken")

@BALANCE_GROUP.command(name="stats", description="Zeigt Balance-Statistiken")
async def balance_stats(interaction: discord.Interaction):
    visibility_key = command_visibility_key_for_interaction(interaction)
    await send_balance_stats(interaction, visibility_key=visibility_key)

bot.tree.add_command(BALANCE_GROUP)
# =========================
# Präsenz-Status Kreise + Live-User-Picker (wiederverwendbar für /fight und /vaultlook)
# =========================

# Mapping: Discord Presence -> Farbe/Circle + Sort-Priorität
class StatusUserPickerView(RestrictedView):
    """
    Wiederverwendbarer Nutzer-Picker mit:
    - farbigen Status-Kreisen vor dem Namen (grün/orange/rot/schwarz)
    - Sortierung: grün, orange, rot, schwarz; innerhalb Gruppe stabile Reihenfolge
    - Live-Update (Polling) ohne Flackern; identischer Mechanismus für /fight und /vaultlook
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
        opts: list[SelectOption] = []
        if self.include_bot_option:
            # Bot-Option unverändert wie in /fight
            opts.append(SelectOption(label="🤖 Bot", value="bot"))

        # Maximal 25 Optionen insgesamt
        max_user_opts = 25 - len(opts)

        for m in members_sorted[:max_user_opts]:
            color = _presence_to_color(m)
            circle = STATUS_CIRCLE_MAP.get(color, "⚫")
            label = f"{circle} {m.display_name}"
            try:
                # Beschreibungen optional; Konsistenz mit /fight gewünscht -> weglassen
                opts.append(SelectOption(label=label, value=str(m.id)))
            except Exception:
                # Fallback ohne Sonderzeichen
                opts.append(SelectOption(label=m.display_name, value=str(m.id)))

        if not opts:
            opts.append(SelectOption(label="Keine Nutzer gefunden", value="none"))

        return opts

# Starte den Bot
# =========================
# /bot_status – Bot-Präsenz via Auswahlmenü setzen
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
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        choice = self.values[0]
        new_status = BOT_STATUS_MAP.get(choice, discord.Status.online)
        try:
            await interaction.client.change_presence(status=new_status)
            await save_bot_presence_status(choice)
            embed = discord.Embed(
                title="✅ Bot-Status geändert",
                description=f"Neuer Status: {BOT_STATUS_LABELS.get(choice, 'Online')}",
                color=0x2b90ff
            )
            try:
                await interaction.response.edit_message(embed=embed, view=None)
            except discord.InteractionResponded:
                await interaction.followup.edit_message(interaction.message.id, embed=embed, view=None)
        except Exception as e:
            try:
                await interaction.response.send_message(f"❌ Fehler beim Setzen des Status: {e}", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(f"❌ Fehler beim Setzen des Status: {e}", ephemeral=True)

class BotStatusView(RestrictedView):
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.add_item(BotStatusSelect(requester_id))

@bot.tree.command(name="bot_status", description="Setze den Status des Bots über ein Auswahlmenü")
async def bot_status(interaction: discord.Interaction):
    # Reagiere nur in erlaubten Kanälen (konsistent mit anderen Commands)
    if not await is_channel_allowed(interaction):
        return
    visibility_key = command_visibility_key_for_interaction(interaction)
    await send_bot_status(interaction, visibility_key=visibility_key)
if __name__ == "__main__":
    token = get_bot_token()
    try:
        bot.run(token)
    finally:
        asyncio.run(close_db())
