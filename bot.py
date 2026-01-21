import asyncio
import json
import logging
import sys
import random
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands, ui, SelectOption
from discord.ext import commands

from config import get_bot_token
from db import DB_PATH, close_db, db_context, init_db
from karten import karten
from services.battle import STATUS_CIRCLE_MAP, STATUS_PRIORITY_MAP, _presence_to_color, calculate_damage, create_battle_embed, create_battle_log_embed, update_battle_log
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


intents = discord.Intents.default()
intents.message_content = False
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)

def create_bot() -> commands.Bot:
    return bot


# Rollen-IDs für Admin/Owner (vom Nutzer bestätigt)
BASTI_USER_ID = 965593518745731152
DEV_ROLE_ID = 1463304167421513961  # Bot_Developer/Tester role ID

MFU_ADMIN_ROLE_ID = 889559991437119498
OWNER_ROLE_ROLE_ID = 1272827906032402464

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

# Volltreffer-System Funktionen
@bot.event
async def on_ready():
    await init_db()
    logging.info("Bot ist online als %s", bot.user)
    try:
        synced = await bot.tree.sync()
        logging.info("Slash-Commands synchronisiert: %s", len(synced))
    except Exception:
        logging.exception("Slash-Command sync failed")

# Event: On Message – bei erster Nachricht im erlaubten Kanal Intro zeigen (ephemeral)
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
    # Nur in erlaubten Kanälen reagieren (ohne Interactions)
    if not await is_channel_allowed_ids(message.guild.id, message.channel.id):
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
    # DMs erlauben
    if interaction.guild is None:
        return True
    # Wartungsmodus: Nur Owner/Dev dürfen Commands nutzen
    if not bypass_maintenance and await is_maintenance_enabled(interaction.guild_id):
        if not await is_owner_or_dev(interaction):
            message = "⛔ Der Bot ist gerade im Wartungsmodus. Bitte später erneut versuchen."
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await interaction.followup.send(message, ephemeral=True)
            return False
    configured_channel_id = None
    allowed_channels = set()
    async with db_context() as db:
        cursor = await db.execute("SELECT mission_channel_id FROM guild_config WHERE guild_id = ?", (interaction.guild_id,))
        row = await cursor.fetchone()
        if row:
            configured_channel_id = row[0]
        # Mehrere erlaubte Kanäle lesen
        cursor = await db.execute("SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?", (interaction.guild_id,))
        allowed_channels = {r[0] for r in await cursor.fetchall()}
    # Wenn nicht konfiguriert
    if not configured_channel_id and not allowed_channels:
        message = "❌ Dieser Server ist noch nicht konfiguriert. Nutze `/configure` in dem Kanal, in dem der Bot aktiv sein soll."
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
        return False
    # Wenn anderer Kanal
    # Kanal pr?fen: erlaubt wenn gleich configured_channel_id oder in allowed_channels
    if (configured_channel_id and interaction.channel_id == configured_channel_id) or (interaction.channel_id in allowed_channels):
        return True

    # Falscher Kanal: Hinweis senden
    if configured_channel_id:
        channel_mention = f"<#{configured_channel_id}>"
    else:
        # Wenn kein Hauptkanal gesetzt, aber Liste existiert, nimm den ersten als Hinweis
        channel_mention = f"<#{next(iter(allowed_channels))}>" if allowed_channels else "(nicht gesetzt)"
    message = f"? Der Bot reagiert nur im konfigurierten Kanal {channel_mention}. Nutze die Commands bitte dort."
    if not interaction.response.is_done():
        await interaction.response.send_message(message, ephemeral=True)
    else:
        await interaction.followup.send(message, ephemeral=True)
    return False

# Kanal-Check ohne Nachrichten-Seiteneffekte (für on_message)
async def is_channel_allowed_ids(guild_id: int, channel_id: int) -> bool:
    configured_channel_id = None
    allowed_channels = set()
    async with db_context() as db:
        cursor = await db.execute("SELECT mission_channel_id FROM guild_config WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if row:
            configured_channel_id = row[0]
        cursor = await db.execute("SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?", (guild_id,))
        allowed_channels = {r[0] for r in await cursor.fetchall()}
    if not configured_channel_id and not allowed_channels:
        return False
    return (configured_channel_id and channel_id == configured_channel_id) or (channel_id in allowed_channels)

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
class ZieheKarteView(ui.View):
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
class MissionView(ui.View):
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
class HPView(ui.View):
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
class BattleView(ui.View):
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
        self.hp_view = hp_view

        # COOLDOWN-SYSTEM: Tracking für starke Attacken (min>90 und max>99 Schaden inkl. Buffs)
        # Format: {player_id: {attack_index: turns_remaining}}
        self.attack_cooldowns = {player1_id: {}, player2_id: {}}

        # KAMPF-LOG SYSTEM: Tracking für Log-Nachrichten
        self.battle_log_message = None
        self.round_counter = 0

        # SIDE EFFECTS SYSTEM: Tracking für aktive Effekte
        # Format: {player_id: [{'type': 'burning', 'duration': 3, 'damage': 15, 'applier': player_id}]}
        self.active_effects = {player1_id: [], player2_id: []}
        # Confusion flags: if a player is confused, their next turn is forced-random
        self.confused_next_turn = {player1_id: False, player2_id: False}

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
                effects_label = f" {' '.join(effect_icons)}" if effect_icons else ""
                
                # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist (nur für aktuellen Spieler)
                is_on_cooldown = self.is_attack_on_cooldown(self.current_turn, i)
                max_damage = self.get_attack_max_damage(base_damage, damage_buff)
                
                if is_on_cooldown:
                    # Grau für Cooldown beim aktuellen Spieler
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = self.attack_cooldowns[self.current_turn][i]
                    button.label = f"{attack['name']} (Cooldown: {cooldown_turns})"
                    button.disabled = True
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
                if isinstance(interaction.channel, discord.Thread):
                    allowed = {self.player1_id, self.player2_id}
                    view = FightFeedbackView(interaction.channel, interaction.guild, allowed, reporter_id=interaction.user.id)
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

        # COOLDOWN-SYSTEM: Prüfe ob Attacke auf Cooldown ist
        if self.is_attack_on_cooldown(self.current_turn, attack_index):
            await interaction.response.send_message("Diese Attacke ist noch auf Cooldown!", ephemeral=True)
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

        attacker_display = attacker_user.display_name if attacker_user else "Bot"
        defender_display = defender_user.display_name if defender_user else "Bot"

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
        if attack_index >= len(attacks):
            await interaction.response.send_message("Ungültiger Angriff!", ephemeral=True)
            return

        attack = attacks[attack_index]
        base_damage = attack["damage"]
        attack_name = attack["name"]

        # NEUES BUFF-SYSTEM: Hole User-spezifische Damage-Buffs
        card_buffs = await get_card_buffs(self.current_turn, current_card["name"])
        damage_buff = 0
        for buff_type, attack_number, buff_amount in card_buffs:
            if buff_type == "damage" and attack_number == (attack_index + 1):
                damage_buff += buff_amount

        # CONFUSION: Falls Angreifer verwirrt ist, 77% Selbstschaden, 23% normaler Treffer
        attack_hits_enemy = True
        max_damage_threshold = self.get_attack_max_damage(base_damage, damage_buff)
        if self.confused_next_turn.get(self.current_turn, False):
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
                actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, damage_buff)
                if self.current_turn == self.player1_id:
                    self.player2_hp -= actual_damage
                else:
                    self.player1_hp -= actual_damage
            # Confusion verbraucht und UI-Icon entfernen
            self.consume_confusion_if_any(self.current_turn)
        else:
            # Normaler Angriff
            actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, damage_buff)
            if self.current_turn == self.player1_id:
                self.player2_hp -= actual_damage
            else:
                self.player1_hp -= actual_damage

        # HP nicht unter 0
        self.player1_hp = max(0, self.player1_hp)
        self.player2_hp = max(0, self.player2_hp)

        # KAMPF-LOG SYSTEM: (wir loggen nach Effektanwendung, damit Verwirrung inline stehen kann)
        self.round_counter += 1

        # SIDE EFFECTS: Apply new effects from attack
        effects = attack.get("effects", [])
        confusion_applied = False
        if attack_hits_enemy:
            for effect in effects:
                # 70% Fix-Chance für Verwirrung
                chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 0)
                if random.random() < chance:
                    if effect.get('type') == 'burning':
                        duration = random.randint(effect["duration"][0], effect["duration"][1])
                        new_effect = {
                            'type': 'burning',
                            'duration': duration,
                            'damage': effect['damage'],
                            'applier': self.current_turn
                        }
                        self.active_effects[defender_id].append(new_effect)
                    elif effect.get('type') == 'confusion':
                        # Confuse defender for next turn + UI marker
                        self.set_confusion(defender_id, self.current_turn)
                        confusion_applied = True

                # Kein separater Log-Eintrag mehr – Effekt wird in der Angriffszeile signalisiert

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
            # Feedback im Thread anbieten (falls in Thread)
            try:
                if isinstance(interaction.channel, discord.Thread):
                    allowed = {self.player1_id, self.player2_id}
                    view = FightFeedbackView(interaction.channel, interaction.guild, allowed, reporter_id=interaction.user.id)
                    await interaction.channel.send("Gab es einen Bug/Fehler?", view=view)
            except Exception:
                logging.exception("Unexpected error")
            self.stop()
            return
        
        # COOLDOWN-SYSTEM: Starte Cooldown für starke Attacken (min>90 UND max>99 inkl. Buffs)
        if self.is_strong_attack(base_damage, damage_buff):
            # Starke Attacke - 2 Züge Cooldown
            previous_turn = self.current_turn
            self.start_attack_cooldown(previous_turn, attack_index)
        
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
                self_hit_damage=(self_damage if not attack_hits_enemy and 'self_damage' in locals() else 0)
            )
            await self.battle_log_message.edit(embed=log_embed)

        # Defer die Interaction für weitere Updates
        await interaction.response.defer()

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
        battle_embed = create_battle_embed(self.player1_card, self.player2_card, self.player1_hp, self.player2_hp, self.current_turn, user1, user2, self.active_effects)
        
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
        defender_id = self.player1_id
        effects_to_remove = []
        for effect in self.active_effects[defender_id]:
            if effect['applier'] == 0 and effect['type'] == 'burning':  # Bot applier is 0
                damage = effect['damage']
                self.player1_hp -= damage
                self.player1_hp = max(0, self.player1_hp)

                # Kein separater Burn-Log – wird inline in der folgenden Attacke gezeigt

                # Decrease duration
                effect['duration'] -= 1
                if effect['duration'] <= 0:
                    effects_to_remove.append(effect)

        # Remove expired effects
        for effect in effects_to_remove:
            self.active_effects[defender_id].remove(effect)

        # Hole Bot-Karte und verfügbare Attacken
        bot_card = self.player2_card
        attacks = bot_card.get("attacks", [{"name": "Punch", "damage": [15, 25]}])

        # Wähle die stärkste verfügbare Attacke (nicht auf Cooldown)
        available_attacks = []
        attack_damages = []

        for i, attack in enumerate(attacks[:4]):
            if not self.is_attack_on_cooldown(0, i):  # Bot ID ist 0
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
        attack_name = attack["name"]

        # Confusion: 77% Selbstschaden, 23% normaler Treffer
        bot_hits_enemy = True
        if self.confused_next_turn.get(0, False):
            if random.random() < 0.77:
                self_damage = random.randint(15, 20) if self.get_attack_max_damage(base_damage, 0) <= 100 else random.randint(40, 60)
                self.player2_hp -= self_damage
                self.player2_hp = max(0, self.player2_hp)
                actual_damage, is_critical = 0, False
                bot_hits_enemy = False
            else:
                actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, 0)
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
            actual_damage, is_critical, min_damage, max_damage = calculate_damage(base_damage, 0)
            # Wende Schaden an
            self.player1_hp -= actual_damage
            self.player1_hp = max(0, self.player1_hp)

        # Aktualisiere Kampf-Log
        self.round_counter += 1

        # Erstelle Bot-User-Objekt für das Log
        class BotUser:
            def __init__(self):
                self.display_name = "Bot"
                self.mention = "**Bot**"

        bot_user = BotUser()
        player_user = message.guild.get_member(self.player1_id)

        if self.battle_log_message:
            log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
            # Sammle ggf. vorherigen Burn-Schaden des Bots gegen den Spieler in dieser Bot-Phase
            pre_burn_total = 0
            for effect in list(self.active_effects.get(self.player1_id, [])):
                if effect.get('applier') == 0 and effect.get('type') == 'burning':
                    pre_burn_total += effect.get('damage', 0)
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
                self_hit_damage=(self_damage if not bot_hits_enemy and 'self_damage' in locals() else 0)
            )
            await self.battle_log_message.edit(embed=log_embed)

        # SIDE EFFECTS: Apply new effects from bot attack (nur wenn Treffer)
        effects = attack.get("effects", [])
        if bot_hits_enemy:
            for effect in effects:
                chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 0)
                if random.random() < chance:
                    if effect.get('type') == 'burning':
                        duration = random.randint(effect["duration"][0], effect["duration"][1])
                        new_effect = {
                            'type': 'burning',
                            'duration': duration,
                            'damage': effect['damage'],
                            'applier': 0
                        }
                        self.active_effects[defender_id].append(new_effect)
                    elif effect.get('type') == 'confusion':
                        # set confusion for player + UI marker
                        try:
                            self.active_effects[self.player1_id] = [e for e in self.active_effects.get(self.player1_id, []) if e.get('type') != 'confusion']
                        except Exception:
                            self.active_effects[self.player1_id] = []
                        self.active_effects[self.player1_id].append({'type': 'confusion', 'duration': 1, 'applier': 0})
                        self.confused_next_turn[self.player1_id] = True
        # Kein separater Log-Eintrag – Effekte werden inline in der Angriffszeile angezeigt

        # Cooldown für Bot-Attacke
        if self.is_strong_attack(base_damage, 0):
            self.start_attack_cooldown(0, attack_index)

        # Wechsle zu Spieler
        self.current_turn = self.player1_id

        # Reduziere Cooldowns für Spieler
        self.reduce_cooldowns(self.player1_id)

        # Aktualisiere Buttons
        await self.update_attack_buttons()

        # Erstelle neues Embed
        battle_embed = create_battle_embed(self.player1_card, self.player2_card, self.player1_hp, self.player2_hp, self.current_turn, player_user, bot_user, self.active_effects)

        # Aktualisiere Kampf-UI
        await message.edit(embed=battle_embed, view=self)

class CardSelectView(ui.View):
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
class UserSearchModal(ui.Modal):
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

class UserSearchResultView(ui.View):
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

class OpponentSelectView(ui.View):
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

class AdminUserSelectView(ui.View):
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

        options = []
        # Schlanke Liste: wenn wenige User, zeige alle mit Status-Kreis (nach Präsenz sortiert)
        if len(self.all_members) <= 25:
            members_sorted = sorted(self.all_members, key=presence_priority)
            for member in members_sorted:
                circle = status_circle(member)
                label = f"{circle} {member.display_name[:100]}"
                options.append(SelectOption(label=label, value=str(member.id)))
        else:
            # Großer Server: Zeige Online/Idle/DnD (nach Präsenz sortiert) + Steuer-Optionen
            online_like = [m for m in self.all_members if m.status != discord.Status.offline]
            online_like_sorted = sorted(online_like, key=presence_priority)
            # bis zu 23 Nutzer + 2 Steuer-Optionen
            for member in online_like_sorted[:23]:
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

class FightVisibilityView(ui.View):
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

class ChallengeResponseView(ui.View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, ctx, selected_cards, mode, thread: discord.Thread | None = None):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.ctx = ctx
        self.selected_cards = selected_cards
        self.mode = mode
        self.value = None
        self.thread = thread
    @ui.button(label="Kämpfen", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Nur der Herausgeforderte kann annehmen!", ephemeral=True)
            return
        self.value = True
        self.stop()
        # Falls privater Thread genutzt wird, stelle sicher, dass der Herausgeforderte hinzugefügt ist
        try:
            if self.thread is not None:
                await self.thread.add_user(self.challenged)
        except Exception:
            logging.exception("Unexpected error")
        await interaction.response.defer()
    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Nur der Herausgeforderte kann ablehnen!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.defer()

class AdminCloseView(ui.View):
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

class FightFeedbackView(ui.View):
    def __init__(self, thread: discord.Thread, guild: discord.Guild, allowed_user_ids: set[int], reporter_id: int = None):
        super().__init__(timeout=600)  # 10 minutes timeout
        self.thread = thread
        self.guild = guild
        self.allowed_user_ids = allowed_user_ids
        self.reporter_id = reporter_id

    @ui.button(label="Ja", style=discord.ButtonStyle.success)
    async def yes_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in self.allowed_user_ids and not await is_admin(interaction):
            await interaction.response.send_message("Nur Teilnehmer oder Admins können antworten.", ephemeral=True)
            return
        # Ping only Basti and MFU Admin role (not Montrigor)
        mentions = []
        try:
            # Always mention Basti
            mentions.append(f"<@{965593518745731152}>")

            # Mention MFU Admin role
            role_admin = self.guild.get_role(MFU_ADMIN_ROLE_ID)
            if role_admin:
                mentions.append(role_admin.mention)

        except Exception:
            logging.exception("Unexpected error")
        mention_text = " ".join(mentions) if mentions else "Admins/Owner"

        await interaction.response.send_message(f"⚠️ Bug gemeldet! {mention_text}", ephemeral=False)
        # Ersetze Buttons mit Admin-Only Close
        await self.thread.send("Ein Admin/Owner kann den Thread jetzt schließen.", view=AdminCloseView(self.thread))
        self.stop()

    @ui.button(label="Nein", style=discord.ButtonStyle.danger)
    async def no_btn(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in self.allowed_user_ids and not await is_admin(interaction):
            await interaction.response.send_message("Nur Teilnehmer oder Admins können antworten.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Danke! Thread wird geschlossen.", ephemeral=True)
        self.stop()
        try:
            await self.thread.delete()
        except Exception:
            logging.exception("Unexpected error")

# Helper: Check Admin (Admins oder Owner)
async def is_admin(interaction):
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
            "? Nur Basti oder die Developer-Rolle d?rfen diesen Command nutzen.",
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
    now = int(time.time())
    async with db_context() as db:
        cursor = await db.execute("SELECT last_daily FROM user_daily WHERE user_id = ?", (interaction.user.id,))
        row = await cursor.fetchone()
        if row and row[0] and now - row[0] < 86400:
            stunden = int((86400 - (now - row[0])) / 3600)
            await interaction.response.send_message(f"Du kannst deine tägliche Belohnung erst in {stunden} Stunden abholen.", ephemeral=True)
            return
        await db.execute("INSERT OR REPLACE INTO user_daily (user_id, last_daily) VALUES (?, ?)", (interaction.user.id, now))
        await db.commit()
    
    user_id = interaction.user.id
    karte = random.choice(karten)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(user_id, karte)
    
    if is_new_card:
        await interaction.response.send_message(f"Du hast eine tägliche Belohnung erhalten: **{karte['name']}**!", ephemeral=True)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Tägliche Belohnung - Infinitydust!", description=f"Du hattest **{karte['name']}** bereits!")
        embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Slash-Command: Mission starten
@bot.tree.command(name="mission", description="Schicke dein Team auf eine Mission und erhalte eine Belohnung")
async def mission(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Prüfe Admin-Berechtigung
    is_admin_user = await is_admin(interaction)
    
    if not is_admin_user:
        # Prüfe tägliche Mission-Limits für normale Nutzer
        mission_count = await get_mission_count(interaction.user.id)
        if mission_count >= 2:
            await interaction.response.send_message("❌ Du hast heute bereits deine 2 Missionen aufgebraucht! Komme morgen wieder.", ephemeral=True)
            return
    
    # Generiere Mission-Daten
    waves = random.randint(2, 6)
    reward_card = random.choice(karten)
    
    # Erstelle Mission-Embed
    # Anzeige angepasst auf 2/Tag für Nicht-Admins
    embed = discord.Embed(title=f"Mission {mission_count + 1}/2" if not is_admin_user else "Mission (Admin)", 
                         description="Hier kommt später die Story. Hier kommt später die Story.")
    embed.add_field(name="Wellen", value=f"{waves}", inline=True)
    embed.add_field(name="🎁 Belohnung", value=f"**{reward_card['name']}**", inline=True)
    embed.set_thumbnail(url=reward_card["bild"])
    
    # Zeige Mission-Auswahl
    mission_data = {
        "waves": waves,
        "reward_card": reward_card,
        "current_wave": 0,
        "player_card": None
    }
    
    mission_view = MissionAcceptView(interaction.user.id, mission_data)
    await interaction.response.send_message(embed=embed, view=mission_view, ephemeral=True)
    await mission_view.wait()
    
    if not mission_view.value:
        await interaction.followup.send("Mission abgelehnt.", ephemeral=True)
        return
    
    # Mission angenommen - starte Wellen-System
    # Erhöhe Zähler beim Start (nur Nicht-Admins)
    if not is_admin_user:
        await increment_mission_count(interaction.user.id)
    await start_mission_waves(interaction, mission_data, is_admin_user)

async def start_mission_waves(interaction, mission_data, is_admin):
    """Startet das Wellen-System für die Mission"""
    waves = mission_data["waves"]
    reward_card = mission_data["reward_card"]
    
    # Nutzer wählt seine Karte für die Mission
    user_karten = await get_user_karten(interaction.user.id)
    if not user_karten:
        await interaction.followup.send("❌ Du hast keine Karten für die Mission!", ephemeral=True)
        return
    
    card_select_view = CardSelectView(interaction.user.id, user_karten, 1)
    await interaction.followup.send("Wähle deine Karte für die Mission:", view=card_select_view, ephemeral=True)
    await card_select_view.wait()
    
    if not card_select_view.value:
        await interaction.followup.send("❌ Keine Karte gewählt. Mission abgebrochen.", ephemeral=True)
        return
    
    selected_card_name = card_select_view.value[0]
    player_card = await get_karte_by_name(selected_card_name)
    mission_data["player_card"] = player_card
    
    # Starte Wellen
    current_wave = 1
    while current_wave <= waves:
        # Prüfe Pause bei >4 Wellen nach der 3. Welle
        if waves > 4 and current_wave == 4:
            await interaction.followup.send("⏸️ **Pause nach der 3. Welle!** Möchtest du deine Karte wechseln?", ephemeral=True)
            
            pause_view = MissionCardSelectView(interaction.user.id, selected_card_name)
            await interaction.followup.send("Was möchtest du tun?", view=pause_view, ephemeral=True)
            await pause_view.wait()
            
            if pause_view.value == "change":
                # Neue Karte wählen
                new_card_view = MissionNewCardSelectView(interaction.user.id, user_karten)
                await interaction.followup.send("Wähle eine neue Karte:", view=new_card_view, ephemeral=True)
                await new_card_view.wait()
                
                if new_card_view.value:
                    selected_card_name = new_card_view.value
                    player_card = await get_karte_by_name(selected_card_name)
                    mission_data["player_card"] = player_card
        
        # Starte Welle mit konsistenter Karte
        wave_result = await execute_mission_wave(interaction, current_wave, waves, player_card, reward_card)
        
        if not wave_result:  # Niederlage
            await interaction.followup.send(f"❌ **Mission fehlgeschlagen!** Du hast in Welle {current_wave} verloren.", ephemeral=True)
            return
        
        await interaction.followup.send(f"🏆 Welle {current_wave} gewonnen! Starte Welle {current_wave + 1}...", ephemeral=True)
        current_wave += 1
    
    # Mission erfolgreich abgeschlossen (Zähler wurde bereits beim Start erhöht)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(interaction.user.id, reward_card)
    
    if is_new_card:
        success_embed = discord.Embed(title="🏆 Mission erfolgreich!", 
                                     description=f"Du hast alle {waves} Wellen überstanden und **{reward_card['name']}** erhalten!")
        success_embed.set_image(url=reward_card["bild"])
        await interaction.followup.send(embed=success_embed, ephemeral=True)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        success_embed = discord.Embed(title="💎 Mission erfolgreich - Infinitydust!", 
                                      description=f"Du hast alle {waves} Wellen überstanden!")
        success_embed.add_field(name="Belohnung", value=f"Du hattest **{reward_card['name']}** bereits - wurde zu **Infinitydust** umgewandelt!", inline=False)
        success_embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.followup.send(embed=success_embed, ephemeral=True)

async def execute_mission_wave(interaction, wave_num, total_waves, player_card, reward_card):
    """Führt eine einzelne Mission-Welle aus"""
    # Bot-Karte für diese Welle
    bot_card = random.choice(karten)
    
    # Erstelle interaktive Mission-BattleView
    mission_battle_view = MissionBattleView(player_card, bot_card, interaction.user.id, wave_num, total_waves)
    await mission_battle_view.init_with_buffs()

    # Erstelle Kampf-Embed nach Anwendung der Buffs
    embed = discord.Embed(title=f"⚔️ Welle {wave_num}/{total_waves}", 
                         description=f"Du kämpfst gegen **{bot_card['name']}**!")
    embed.add_field(name="🟥 Deine Karte", value=f"{player_card['name']}\nHP: {mission_battle_view.player_hp}", inline=True)
    embed.add_field(name="🟦 Bot Karte", value=f"{bot_card['name']}\nHP: {mission_battle_view.bot_hp}", inline=True)
    embed.set_image(url=player_card["bild"])
    embed.set_thumbnail(url=bot_card["bild"])
    
    # Erstelle Kampf-Log ZUERST (über dem Kampf)
    log_embed = create_battle_log_embed()
    log_message = await interaction.followup.send(embed=log_embed, ephemeral=True)
    mission_battle_view.battle_log_message = log_message
    
    # Dann den Kampf (unter dem Log)
    battle_message = await interaction.followup.send(embed=embed, view=mission_battle_view, ephemeral=True)
    
    # Warte auf Kampf-Ende
    await mission_battle_view.wait()
    
    return mission_battle_view.result

# Entfernt: /team Command (auf Wunsch des Nutzers)

# Slash-Command: Story spielen
@bot.tree.command(name="story", description="Starte eine interaktive Story (nur für dich sichtbar)")
async def story(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Auswahl der Story (aktuell nur "text")
    view = StorySelectView(interaction.user.id)
    embed = discord.Embed(title="📖 Story auswählen", description="Wähle eine Story aus der Liste. Aktuell verfügbar: **text**")
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.wait()
    if not view.value:
        await interaction.followup.send("⏰ Keine Story gewählt. Abgebrochen.", ephemeral=True)
        return

    # Starte den Story-Player (Schritt 0)
    story_view = StoryPlayerView(interaction.user.id, view.value)
    start_embed = story_view.render_step_embed()
    await interaction.followup.send(embed=start_embed, view=story_view, ephemeral=True)


class StorySelectView(ui.View):
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


class StoryPlayerView(ui.View):
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

@configure_group.command(name="add", description="Fügt den aktuellen Kanal zur Liste erlaubter Bot-Kanäle hinzu")
async def configure_add(interaction: discord.Interaction):
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    async with db_context() as db:
        await db.execute("INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)", (interaction.guild_id, interaction.channel_id))
        await db.commit()
    await interaction.response.send_message(f"✅ Hinzugefügt: {interaction.channel.mention}", ephemeral=True)

@configure_group.command(name="remove", description="Entfernt den aktuellen Kanal aus der Liste erlaubter Bot-Kanäle")
async def configure_remove(interaction: discord.Interaction):
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    async with db_context() as db:
        await db.execute("DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?", (interaction.guild_id, interaction.channel_id))
        await db.commit()
    await interaction.response.send_message(f"🗑️ Entfernt: {interaction.channel.mention}", ephemeral=True)

@configure_group.command(name="list", description="Zeigt alle erlaubten Bot-Kanäle an")
async def configure_list(interaction: discord.Interaction):
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    async with db_context() as db:
        cursor = await db.execute("SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?", (interaction.guild_id,))
        rows = await cursor.fetchall()
    if not rows:
        await interaction.response.send_message("ℹ️ Es sind noch keine Kanäle erlaubt. Nutze `/configure add` im gewünschten Kanal.", ephemeral=True)
        return
    mentions = "\n".join(f"• <#{r[0]}>" for r in rows)
    await interaction.response.send_message(f"✅ Erlaubte Kanäle:\n{mentions}", ephemeral=True)

# Registriere die Gruppe
bot.tree.add_command(configure_group)

# Admin-Hilfscommand: Reset „gesehenes Intro" (zum Testen)
@bot.tree.command(name="reset-intro", description="Setzt das Intro-Flag für diesen Kanal/Guild/Nutzer zurück (Nur Admins)")
async def reset_intro(interaction: discord.Interaction):
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
        return
    async with db_context() as db:
        await db.execute(
            "DELETE FROM user_seen_channels WHERE guild_id = ? AND channel_id = ?",
            (interaction.guild.id, interaction.channel.id),
        )
        await db.commit()
    await interaction.response.send_message("✅ Intro-Status für ALLE in diesem Kanal zurückgesetzt. Schreibe eine Nachricht, um den Prompt erneut zu sehen.", ephemeral=True)

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

class FuseCardSelectView(ui.View):
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

class BuffTypeSelectView(ui.View):
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

class InviteUserSelectView(ui.View):
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

class DustAmountView(ui.View):
    def __init__(self, user_dust):
        super().__init__(timeout=60)
        self.add_item(DustAmountSelect(user_dust))

# Slash-Command: Eingeladen (Einmalig pro User - 1x Infinitydust für beide)
@bot.tree.command(name="eingeladen", description="Wähle wer dich eingeladen hat - beide erhalten 1x Infinitydust [Einmalig]")
async def eingeladen(interaction: discord.Interaction):
    try:
        print(f"[INVITED] /eingeladen invoked by user={interaction.user.id} guild={interaction.guild_id} channel={interaction.channel_id}")
        await interaction.response.defer(ephemeral=True)
        
        # Channel-Check nach der Antwort
        if not await is_channel_allowed_ids(interaction.guild_id, interaction.channel_id):
            print(f"[INVITED] channel not allowed guild={interaction.guild_id} channel={interaction.channel_id}")
            await interaction.followup.send("❌ Der Bot reagiert nur in konfigurierten Kanälen. Nutze `/configure add` im gewünschten Kanal.", ephemeral=True)
            return
            
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
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
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
    user_id = interaction.user.id
    user_dust = await get_infinitydust(user_id)
    
    if user_dust < 10:
        embed = discord.Embed(
            title="❌ Nicht genug Infinitydust", 
            description=f"Du hast nur **{user_dust} Infinitydust**.\nDu brauchst mindestens **10 Infinitydust** zum Verstärken!",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    view = DustAmountView(user_dust)
    embed = discord.Embed(
        title="💎 Karten-Verstärkung", 
        description=f"Du hast **{user_dust} Infinitydust**\n\nWähle die Menge für die Verstärkung:\n\n💎 **10 Dust** = +20 Leben/Damage\n💎 **20 Dust** = +40 Leben/Damage\n💎 **30 Dust** = +60 Leben/Damage",
        color=0x9d4edd
    )
    embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Slash-Command: Vault anzeigen
@bot.tree.command(name="vault", description="Zeige deine Karten-Sammlung")
async def vault(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    user_id = interaction.user.id
    user_karten = await get_user_karten(user_id)
    infinitydust = await get_infinitydust(user_id)
    
    if not user_karten and infinitydust == 0:
        await interaction.response.send_message("Du hast noch keine Karten in deiner Sammlung.", ephemeral=True)
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
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Admin-Command: Vault anderer User anzeigen
@bot.tree.command(name="vaultlook", description="Schau in den Vault eines anderen Users (Nur für Admins)")
async def vaultlook(interaction: discord.Interaction):
    # Schnell antworten, dann prüfen
    await interaction.response.defer(ephemeral=True)
    if not await is_admin(interaction):
        await interaction.followup.send("❌ Du hast keine Berechtigung für diesen Command! Nur Admins können in andere Vaults schauen.", ephemeral=True)
        return

    # Nutzer-Auswahl mit Suche und Statuskreisen (wie in /fight)
    view = AdminUserSelectView(interaction.user.id, interaction.guild)
    await interaction.followup.send("Wähle einen User, dessen Vault du ansehen möchtest:", view=view, ephemeral=True)
    await view.wait()
    
    if not view.value:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=True)
        return
    
    target_user_id = int(view.value)
    target_user = interaction.guild.get_member(target_user_id)
    
    if not target_user:
        await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
        return
    
    # Hole Vault-Daten des Ziel-Users
    user_karten = await get_user_karten(target_user_id)
    infinitydust = await get_infinitydust(target_user_id)
    
    if not user_karten and infinitydust == 0:
        await interaction.followup.send(f"❌ {target_user.mention} hat noch keine Karten in seiner Sammlung.", ephemeral=True)
        return
    
    # Erstelle Vault-Embed
    embed = discord.Embed(title=f"🔍 Vault von {target_user.display_name}", description=f"**{target_user.mention}** besitzt **{len(user_karten)}** verschiedene Karten:")
    
    # Füge Infinitydust hinzu (falls vorhanden)
    if infinitydust > 0:
        embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
    
    # Füge normale Karten hinzu (alle anzeigen für Admins)
    for kartenname, anzahl in user_karten:
        karte = await get_karte_by_name(kartenname)
        if karte:
            embed.add_field(name=f"{karte['name']} (x{anzahl})", value=karte['beschreibung'][:100] + "...", inline=False)
    
    embed.set_footer(text=f"Vault-Lookup durch {interaction.user.display_name}")
    embed.color = 0xff6b6b  # Rot für Admin-Aktionen
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="fight", description="Kämpfe gegen einen anderen Spieler im 1v1!")
async def fight(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Schritt 0: Sichtbarkeit wählen (Privat/Öffentlich)
    # Defer sofort, um Interaktions-Timeouts/Unknown interaction zu vermeiden
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        logging.exception("Unexpected error")
    visibility_view = FightVisibilityView(interaction.user.id)
    await interaction.followup.send("Wie soll der Kampf sichtbar sein?", view=visibility_view, ephemeral=True)
    await visibility_view.wait()
    if visibility_view.value is None:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Kampf abgebrochen.", ephemeral=True)
        return
    is_private = visibility_view.value
    fight_thread: discord.Thread | None = None

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
        target_channel: discord.abc.MessageableChannel = interaction.channel
        if is_private and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            try:
                # Erzeuge privaten Thread und füge Herausforderer hinzu
                thread_name = f"Privater Kampf von {interaction.user.display_name}"
                fight_thread = await interaction.channel.create_thread(name=thread_name, type=discord.ChannelType.private_thread, invitable=False)
                await fight_thread.add_user(interaction.user)
                target_channel = fight_thread
            except Exception:
                # Fallback: öffentlich
                fight_thread = None
                target_channel = interaction.channel

        # KAMPF-LOG ZUERST senden (wird über der Kampf-Nachricht angezeigt)
        log_embed = create_battle_log_embed()
        battle_view.battle_log_message = await target_channel.send(embed=log_embed)
        
        # DANN Kampf-Nachricht senden (erscheint unter dem Log)
        embed = create_battle_embed(selected_cards[0], bot_card, battle_view.player1_hp, battle_view.player2_hp, interaction.user.id, interaction.user, bot_user)
        msg = await target_channel.send(embed=embed, view=battle_view)
        return
    
    # User als Gegner
    challenged = interaction.guild.get_member(int(opponent_id))
    if not challenged:
        await interaction.followup.send("❌ Gegner nicht gefunden!", ephemeral=True)
        return
    # Privater Thread ggf. erstellen und beide hinzufügen
    target_channel: discord.abc.MessageableChannel = interaction.channel
    if is_private and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        try:
            thread_name = f"Privater Kampf: {interaction.user.display_name} vs {challenged.display_name}"
            fight_thread = await interaction.channel.create_thread(name=thread_name, type=discord.ChannelType.private_thread, invitable=False)
            await fight_thread.add_user(interaction.user)
            await fight_thread.add_user(challenged)
            target_channel = fight_thread
        except Exception:
            fight_thread = None
            target_channel = interaction.channel

    # Nachricht an Herausgeforderten
    challenge_view = ChallengeResponseView(interaction.user, challenged, interaction, selected_cards, 1, fight_thread)
    await target_channel.send(f"{challenged.mention}, du wurdest zu einem 1v1 Kartenkampf herausgefordert!", view=challenge_view)
    await interaction.followup.send(f"Warte auf Antwort von {challenged.mention}...", ephemeral=True)
    await challenge_view.wait()
    
    if challenge_view.value is None:
        await interaction.followup.send(f"{challenged.mention} hat nicht rechtzeitig geantwortet. Kampf abgebrochen.", ephemeral=True)
        # Thread aufräumen, falls erstellt
        try:
            if fight_thread is not None:
                await fight_thread.delete()
        except Exception:
            logging.exception("Unexpected error")
        return
    if challenge_view.value is False:
        await interaction.followup.send(f"{challenged.mention} hat den Kampf abgelehnt.", ephemeral=True)
        try:
            if fight_thread is not None:
                await fight_thread.delete()
        except Exception:
            logging.exception("Unexpected error")
        return
    
    # Schritt 3: Gegner wählt seine Karte
    gegner_karten_liste = await get_user_karten(challenged.id)
    if not gegner_karten_liste:
        await interaction.followup.send(f"{challenged.mention} hat keine Karten! Kampf abgebrochen.", ephemeral=True)
        try:
            if fight_thread is not None:
                await fight_thread.delete()
        except Exception:
            logging.exception("Unexpected error")
        return
    
    gegner_card_select_view = CardSelectView(challenged.id, gegner_karten_liste, 1)
    await (fight_thread or interaction.channel).send(f"{challenged.mention}, wähle deine Karte für den 1v1 Kampf:", view=gegner_card_select_view)
    await gegner_card_select_view.wait()
    if not gegner_card_select_view.value:
        await interaction.followup.send(f"{challenged.mention} hat keine Karte gewählt. Kampf abgebrochen.", ephemeral=True)
        try:
            if fight_thread is not None:
                await fight_thread.delete()
        except Exception:
            logging.exception("Unexpected error")
        return
    
    gegner_selected_names = gegner_card_select_view.value
    gegner_selected_cards = [await get_karte_by_name(name) for name in gegner_selected_names]
    
    # 1v1 Kampf starten
    battle_view = BattleView(selected_cards[0], gegner_selected_cards[0], interaction.user.id, challenged.id, None)
    await battle_view.init_with_buffs()
    
    # KAMPF-LOG ZUERST senden (wird über der Kampf-Nachricht angezeigt)
    log_embed = create_battle_log_embed()
    battle_view.battle_log_message = await (fight_thread or interaction.channel).send(embed=log_embed)
    
    # DANN Kampf-Nachricht senden (erscheint unter dem Log)
    embed = create_battle_embed(selected_cards[0], gegner_selected_cards[0], battle_view.player1_hp, battle_view.player2_hp, interaction.user.id, interaction.user, challenged)
    msg = await (fight_thread or interaction.channel).send(embed=embed, view=battle_view)



# Slash-Command: Anfang (Hauptmenü)
class AnfangView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label="tägliche Karte", style=discord.ButtonStyle.success, row=0)
    async def btn_daily(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum täglichen Belohnungs-Flow weiter
        await täglich.callback(interaction)

    @ui.button(label="Verbessern", style=discord.ButtonStyle.primary, row=0)
    async def btn_fuse(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fuse-Flow weiter
        await fuse.callback(interaction)

    @ui.button(label="Kämpfe", style=discord.ButtonStyle.danger, row=0)
    async def btn_fight(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Fight-Flow weiter
        await fight.callback(interaction)

    @ui.button(label="Mission", style=discord.ButtonStyle.secondary, row=0)
    async def btn_mission(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Missions-Flow weiter
        await mission.callback(interaction)

    @ui.button(label="Story", style=discord.ButtonStyle.secondary, row=0)
    async def btn_story(self, interaction: discord.Interaction, button: ui.Button):
        # Leitet zum Story-Flow weiter
        await story.callback(interaction)

class IntroEphemeralPromptView(ui.View):
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
async def anfang(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    is_admin_user = await is_admin(interaction)

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
    await interaction.response.send_message(content=text, view=view, ephemeral=not is_admin_user)

# Admin-Command: Test-Bericht
@bot.tree.command(name="test-bericht", description="Listet alle verfügbaren Commands und deren Status (Nur für Admins)")
async def test_bericht(interaction: discord.Interaction):
    if not await is_channel_allowed(interaction):
        return
    # Prüfe Admin-Berechtigung
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Du hast keine Berechtigung.", ephemeral=True)
        return
    
    await interaction.response.send_message("🔍 Sammle verfügbare Commands...", ephemeral=True)

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

    lines = []
    for name, cmd in flat_cmds:
        lines.append(f"• /{name} — registriert")

    description = "Alle registrierten Slash-Commands (inkl. Unterbefehle):\n" + "\n".join(lines) if lines else "Keine Commands registriert."

    embed = discord.Embed(
        title="🤖 Verfügbare Commands",
        description=description,
        color=0x2b90ff
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

    await interaction.followup.send(embed=embed, ephemeral=True)

class UserSelectView(ui.View):
    def __init__(self, user_id, guild):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value = None
        options = []
        # Füge alle Guild-Mitglieder hinzu (außer Bots)
        for member in guild.members:
            if not member.bot:
                options.append(SelectOption(label=member.display_name, value=str(member.id)))
        self.select = ui.Select(placeholder="Wähle einen Nutzer...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann den Nutzer wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

class VaultView(ui.View):
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
                        lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden")
                    embed.add_field(name="Attacken", value="\n".join(lines), inline=False)
                
                # Buttons für Attacken anzeigen, aber deaktiviert (kein Effekt beim Klicken)
                view_buttons = ui.View(timeout=60)
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
            view = ui.View(timeout=90)
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
                            lines.append(f"• {atk.get('name', f'Attacke {idx}')} — {dmg_text} Schaden")
                        embed.add_field(name="Attacken", value="\n".join(lines), inline=False)
                    
                    # Buttons für Attacken anzeigen, aber deaktiviert (kein Effekt beim Klicken)
                    view_buttons = ui.View(timeout=60)
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

                v = ui.View(timeout=120)
                v.add_item(sel)
                v.add_item(prev_btn)
                v.add_item(next_btn)

                # Falls dies eine Folgeaktion ist, verwende followup, sonst response
                try:
                    await inter.response.send_message("Wähle eine Karte:", view=v, ephemeral=True)
                except discord.InteractionResponded:
                    await inter.followup.send("Wähle eine Karte:", view=v, ephemeral=True)

            await send_page(interaction, current_index)

class GiveCardSelectView(ui.View):
    def __init__(self, user_id, target_user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.target_user_id = target_user_id
        self.value = None
        # Füge alle Karten aus karten.py hinzu + Infinitydust
        options = [SelectOption(label=karte["name"], value=karte["name"]) for karte in karten]
        options.append(SelectOption(label="💎 Infinitydust", value="infinitydust"))
        self.select = ui.Select(placeholder="Wähle eine Karte oder Infinitydust...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Command-User kann die Karte wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

# View für Infinitydust-Mengen-Auswahl
class InfinitydustAmountView(ui.View):
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
    
    # Schritt 1: Nutzer-Auswahl
    user_select_view = UserSelectView(interaction.user.id, interaction.guild)
    await interaction.response.send_message("Wähle einen Nutzer, dem du eine Karte geben möchtest:", view=user_select_view, ephemeral=True)
    await user_select_view.wait()
    
    if not user_select_view.value:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=True)
        return
    
    target_user_id = int(user_select_view.value)
    target_user = interaction.guild.get_member(target_user_id)
    
    if not target_user:
        await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
        return
    
    # Schritt 2: Karten-Auswahl
    card_select_view = GiveCardSelectView(interaction.user.id, target_user_id)
    await interaction.followup.send(f"Wähle eine Karte für {target_user.mention}:", view=card_select_view, ephemeral=True)
    await card_select_view.wait()
    
    if not card_select_view.value:
        await interaction.followup.send("⏰ Keine Karte gewählt. Abgebrochen.", ephemeral=True)
        return
    
    selected_card_name = card_select_view.value
    
    # Prüfe ob Infinitydust ausgewählt wurde
    if selected_card_name == "infinitydust":
        # Infinitydust-Mengen-Auswahl
        amount_view = InfinitydustAmountView(interaction.user.id, target_user_id)
        await interaction.followup.send(f"Wähle die Menge Infinitydust für {target_user.mention}:", view=amount_view, ephemeral=True)
        await amount_view.wait()
        
        if not amount_view.value:
            await interaction.followup.send("⏰ Keine Menge gewählt. Abgebrochen.", ephemeral=True)
            return
        
        amount = amount_view.value
        
        # Infinitydust dem Nutzer geben
        await add_infinitydust(target_user_id, amount)
        
        # Erfolgsnachricht für Infinitydust (öffentlich)
        embed = discord.Embed(title="💎 Infinitydust verschenkt!", description=f"{interaction.user.mention} hat **{amount}x Infinitydust** an {target_user.mention} gegeben!")
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.channel.send(embed=embed)
        return
    
    # Normale Karte dem Nutzer geben
    selected_card = await get_karte_by_name(selected_card_name)
    is_new_card = await check_and_add_karte(target_user_id, selected_card)
    
    # Erfolgsnachricht (öffentlich)
    if is_new_card:
        embed = discord.Embed(title="🎁 Karte verschenkt!", description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!")
        if selected_card:
            embed.set_image(url=selected_card["bild"])
        await interaction.channel.send(embed=embed)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Karte verschenkt - Infinitydust!", description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!")
        embed.add_field(name="Umwandlung", value=f"{target_user.mention} hatte die Karte bereits - wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.channel.send(embed=embed)

# View für Mission-Auswahl
class MissionAcceptView(ui.View):
    def __init__(self, user_id, mission_data):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.mission_data = mission_data
        self.value = None

    @ui.button(label="Annehmen", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann annehmen!", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer()

    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Mission-User kann ablehnen!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.defer()

# View für Karten-Auswahl bei Pause
class MissionCardSelectView(ui.View):
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
class MissionNewCardSelectView(ui.View):
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
class MissionBattleView(ui.View):
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
        self.current_turn = user_id  # Spieler beginnt
        self.attacks = player_card.get("attacks", [
            {"name": "Punch", "damage": 20},
            {"name": "Kick", "damage": 25},
            {"name": "Special", "damage": 30},
            {"name": "Ultimate", "damage": 40}
        ])
        self.round_counter = 0
        self.battle_log_message = None

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

    def is_attack_on_cooldown_user(self, attack_index: int) -> bool:
        return self.user_attack_cooldowns.get(attack_index, 0) > 0

    def is_attack_on_cooldown_bot(self, attack_index: int) -> bool:
        return self.bot_attack_cooldowns.get(attack_index, 0) > 0

    def start_attack_cooldown_user(self, attack_index: int, turns: int = 1) -> None:
        self.user_attack_cooldowns[attack_index] = turns

    def start_attack_cooldown_bot(self, attack_index: int, turns: int = 1) -> None:
        self.bot_attack_cooldowns[attack_index] = turns

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
                effects_label = f" {' '.join(effect_icons)}" if effect_icons else ""

                # Prüfe Cooldown (nur für Spieler)
                is_on_cooldown = self.is_attack_on_cooldown_user(i)
                
                if is_on_cooldown:
                    # Grau für Cooldown
                    button.style = discord.ButtonStyle.secondary
                    cooldown_turns = self.user_attack_cooldowns[i]
                    button.label = f"{attack['name']} (Cooldown: {cooldown_turns})"
                    button.disabled = True
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
        if self.is_attack_on_cooldown_user(attack_index):
            await interaction.response.send_message("Diese Attacke ist noch auf Cooldown!", ephemeral=True)
            return

        attack = self.attacks[attack_index]
        damage = attack["damage"]
        dmg_buff = self.damage_bonuses.get(attack_index + 1, 0)

        # Confusion handling: 77% self-damage vs 23% normal
        hits_enemy = True
        max_dmg_threshold = self.mission_get_attack_max_damage(damage, dmg_buff)
        if self.confused_next_turn.get(self.user_id, False):
            if random.random() < 0.77:
                self_damage = random.randint(15, 20) if max_dmg_threshold <= 100 else random.randint(40, 60)
                self.player_hp -= self_damage
                self.player_hp = max(0, self.player_hp)
                actual_damage, is_critical = 0, False
                hits_enemy = False
            else:
                actual_damage, is_critical, min_damage, max_damage = calculate_damage(damage, dmg_buff)
                self.bot_hp -= actual_damage
                self.bot_hp = max(0, self.bot_hp)
            # consume confusion + clear UI icon
            try:
                self.active_effects[self.user_id] = [e for e in self.active_effects.get(self.user_id, []) if e.get('type') != 'confusion']
            except Exception:
                logging.exception("Unexpected error")
            self.confused_next_turn[self.user_id] = False
        else:
            actual_damage, is_critical, min_damage, max_damage = calculate_damage(damage, dmg_buff)
            self.bot_hp -= actual_damage
            self.bot_hp = max(0, self.bot_hp)

        self.round_counter += 1

        # Apply new effects from player's attack (only if it hit)
        confusion_applied = False
        if hits_enemy:
            effects = attack.get("effects", [])
            for effect in effects:
                chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 0)
                if random.random() < chance:
                    if effect.get('type') == 'burning':
                        duration = random.randint(effect["duration"][0], effect["duration"][1])
                        self.active_effects[0].append({
                            'type': 'burning',
                            'duration': duration,
                            'damage': effect['damage'],
                            'applier': self.user_id
                        })
                    elif effect.get('type') == 'confusion':
                        self.confused_next_turn[0] = True
                        confusion_applied = True

        # Update Kampf-Log (inkl. vorab angewandter Verbrennung im selben Eintrag)
        if self.battle_log_message:
            log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
            log_embed = update_battle_log(
                log_embed,
                self.player_card["name"],
                self.bot_card["name"],
                attack["name"],
                actual_damage,
                is_critical,
                interaction.user,
                "Bot",
                self.round_counter,
                self.bot_hp,
                pre_effect_damage=pre_burn_total,
                confusion_applied=confusion_applied,
                self_hit_damage=(self_damage if not hits_enemy and 'self_damage' in locals() else 0)
            )
            await self.battle_log_message.edit(embed=log_embed)
        
        # Starte Cooldown für starke Attacken (min>90 UND max>99 inkl. Buffs) für nächsten Zug.
        # In Missionen soll die stärkste Attacke im nächsten eigenen Zug gesperrt sein.
        # Darum KEINE sofortige Reduktion hier – die Reduktion passiert nach dem Bot-Zug.
        if self.mission_is_strong_attack(damage, dmg_buff):
            self.start_attack_cooldown_user(attack_index, 2)
        
        # Prüfen ob Kampf vorbei nach Spieler-Angriff
        if self.bot_hp <= 0:
            self.result = True
            await interaction.response.edit_message(content=f"🏆 **Welle {self.wave_num} gewonnen!** Du hast **{self.bot_card['name']}** besiegt!", view=None)
            self.stop()
            return
        
        # Bot-Zug nach kurzer Pause
        await interaction.response.edit_message(content=f"🎯 Du hast **{attack['name']}** verwendet! **{self.bot_card['name']}** ist an der Reihe...", view=None)

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

        # Bot-Angriff
        bot_attacks = self.bot_card.get("attacks", [{"name": "Punch", "damage": 20}])
        # Wähle stärkste verfügbare Bot-Attacke (unter Berücksichtigung von Cooldown)
        available_attacks = []
        attack_damages = []
        for i, atk in enumerate(bot_attacks[:4]):
            if not self.is_attack_on_cooldown_bot(i):
                damage = atk["damage"]
                max_dmg = self.mission_get_attack_max_damage(damage) if isinstance(atk, dict) else 0
                available_attacks.append(i)
                attack_damages.append(max_dmg)
        
        if available_attacks:
            # Wähle die mit max Damage
            best_index = available_attacks[attack_damages.index(max(attack_damages))]
            attack = bot_attacks[best_index]
            damage = attack["damage"]
            # Bot kann ebenfalls verwirrt sein: 77% Selbstschaden, 23% normaler Treffer
            bot_hits_enemy = True
            if hasattr(self, 'confused_next_turn') and self.confused_next_turn.get(0, False):
                if random.random() < 0.77:
                    self_damage = random.randint(15, 20) if self.mission_get_attack_max_damage(damage) <= 100 else random.randint(40, 60)
                    self.bot_hp -= self_damage
                    self.bot_hp = max(0, self.bot_hp)
                    actual_damage, is_critical = 0, False
                    bot_hits_enemy = False
                else:
                    actual_damage, is_critical, min_damage, max_damage = calculate_damage(damage, 0)
                    self.player_hp -= actual_damage
                    self.player_hp = max(0, self.player_hp)
                # Confusion verbraucht
                self.confused_next_turn[0] = False
            else:
                actual_damage, is_critical, min_damage, max_damage = calculate_damage(damage, 0)  # Bot hat keine Buffs
                self.player_hp -= actual_damage
                self.player_hp = max(0, self.player_hp)
            
            self.round_counter += 1
            
            # Update Kampf-Log für Bot-Angriff
            if self.battle_log_message:
                log_embed = self.battle_log_message.embeds[0] if self.battle_log_message.embeds else create_battle_log_embed()
                log_embed = update_battle_log(
                    log_embed,
                    self.bot_card["name"],
                    self.player_card["name"],
                    attack["name"],
                    actual_damage,
                    is_critical,
                    "Bot",
                    interaction.user,
                    self.round_counter,
                    self.player_hp,
                    pre_effect_damage=pre_burn_total_player
                )
                await self.battle_log_message.edit(embed=log_embed)
    
            # SIDE EFFECTS: Apply new effects from bot attack (nur wenn Treffer)
            effects = attack.get("effects", [])
            if bot_hits_enemy:
                for effect in effects:
                    # 70% Fix-Chance für Verwirrung
                    chance = 0.7 if effect.get('type') == 'confusion' else effect.get('chance', 0)
                    if random.random() < chance:
                        if effect['type'] == 'burning':
                            duration = random.randint(effect["duration"][0], effect["duration"][1])
                            new_effect = {
                                'type': 'burning',
                                'duration': duration,
                                'damage': effect['damage'],
                                'applier': 0
                            }
                            self.active_effects[self.user_id].append(new_effect)
                        elif effect['type'] == 'confusion':
                            # Confuse player for their next turn in mission mode
                            if not hasattr(self, 'confused_next_turn'):
                                self.confused_next_turn = {self.user_id: False, 0: False}
                            self.confused_next_turn[self.user_id] = True
            # Kein separater Log – Effekte werden inline in der Angriffszeile angezeigt

            if self.player_hp <= 0:
                self.result = False
                await interaction.followup.send(f"❌ **Welle {self.wave_num} verloren!** **{self.bot_card['name']}** hat dich besiegt!", ephemeral=True)
                self.stop()
                return

            # Cooldown für Bot wenn stark (min>90 UND max>99)
            if self.mission_is_strong_attack(damage):
                self.start_attack_cooldown_bot(best_index, 2)
                # Reduziere Cooldowns für den Bot direkt nach seinem Zug (entspricht /fight)
                self.reduce_cooldowns_bot()

            # Reduce Cooldowns for User nach Bot-Zug
            self.reduce_cooldowns_user()

            # Update UI für nächsten Spieler-Zug
            embed = discord.Embed(title=f"⚔️ Welle {self.wave_num}/{self.total_waves}",
                                  description=f"Bot hat **{attack['name']}** verwendet! Dein HP: {self.player_hp}\nDu bist wieder an der Reihe!")
            embed.add_field(name="🟥 Deine Karte", value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
            embed.add_field(name="🟦 Bot Karte", value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
            embed.set_image(url=self.player_card["bild"])
            embed.set_thumbnail(url=self.bot_card["bild"])

            # Update attack buttons für neuen Spieler-Zug
            self.update_attack_buttons_mission()

            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
        else:
            # Bot hat keine Attacken verfügbar (alle auf Cooldown) - überspringe Bot-Zug
            self.reduce_cooldowns_user()
            self.update_attack_buttons_mission()
            
            embed = discord.Embed(title=f"⚔️ Welle {self.wave_num}/{self.total_waves}", 
                                  description=f"🤖 Bot hat keine Attacken verfügbar! Du bist wieder an der Reihe!")
            embed.add_field(name="🟥 Deine Karte", value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
            embed.add_field(name="🟦 Bot Karte", value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
            embed.set_image(url=self.player_card["bild"])
            embed.set_thumbnail(url=self.bot_card["bild"])
            
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)

# =========================
# Owner/Dev Panel
# =========================

async def _send_ephemeral(interaction: discord.Interaction, *, content: str | None = None, embed=None, view=None, file=None):
    if interaction.response.is_done():
        return await interaction.followup.send(content=content, embed=embed, view=view, file=file, ephemeral=True)
    return await interaction.response.send_message(content=content, embed=embed, view=view, file=file, ephemeral=True)

async def _edit_panel_message(interaction: discord.Interaction, *, content: str | None = None, embed=None, view=None):
    try:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
    except discord.InteractionResponded:
        await interaction.followup.edit_message(interaction.message.id, content=content, embed=embed, view=view)

async def _select_user(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await _send_ephemeral(interaction, content="Nur in Servern verfuegbar.")
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

class NumberSelectView(ui.View):
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
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        self.value = int(self.select.values[0])
        self.stop()
        await interaction.response.defer()

class CardSelectPagerView(ui.View):
    def __init__(self, requester_id: int, cards: list[dict]):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.cards = cards
        self.page = 0
        self.value = None
        self.select = ui.Select(placeholder="Waehle eine Karte...", min_values=1, max_values=1, options=[])
        self.select.callback = self.select_callback
        self.prev_button = ui.Button(label="< Zurueck", style=discord.ButtonStyle.secondary)
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
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        if (self.page + 1) * 25 < len(self.cards):
            self.page += 1
        self._render()
        await interaction.response.edit_message(view=self)

    async def cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)

class ConfirmDeleteUserView(ui.View):
    def __init__(self, requester_id: int, target_id: int, target_name: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.target_id = target_id
        self.target_name = target_name

    @ui.button(label="Loeschen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Anforderer kann bestaetigen.", ephemeral=True)
            return
        await delete_user_data(self.target_id)
        self.stop()
        await interaction.response.edit_message(
            content=f"Daten von {self.target_name} geloescht.", view=None
        )

    @ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nur der Anforderer kann abbrechen.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)

async def send_health(interaction: discord.Interaction):
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
    await _send_ephemeral(interaction, embed=embed)

async def send_db_backup(interaction: discord.Interaction):
    db_path = Path(DB_PATH)
    if not db_path.exists():
        await _send_ephemeral(interaction, content="DB-Datei nicht gefunden.")
        return
    await _send_ephemeral(interaction, content="DB-Backup:", file=discord.File(str(db_path), filename=db_path.name))

async def send_db_debug(interaction: discord.Interaction):
    async with db_context() as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        tables = [row[0] for row in await cursor.fetchall()]
        cursor = await db.execute("PRAGMA integrity_check")
        integrity = await cursor.fetchone()
    embed = discord.Embed(title="DB Debug", color=0x2b90ff)
    embed.add_field(name="Tables", value=str(len(tables)), inline=True)
    embed.add_field(name="Integrity", value=str(integrity[0] if integrity else "unknown"), inline=True)
    await _send_ephemeral(interaction, embed=embed)

async def send_debug_user(interaction: discord.Interaction, user_id: int, user_name: str):
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
    await _send_ephemeral(interaction, embed=embed)

async def send_logs_last(interaction: discord.Interaction, count: int):
    if not LOG_PATH.exists():
        await _send_ephemeral(interaction, content="Log-Datei nicht gefunden.")
        return
    content = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    tail = "\n".join(content[-int(count):])
    if not tail:
        await _send_ephemeral(interaction, content="Keine Logs vorhanden.")
        return
    if len(tail) > 1900:
        tail = tail[-1900:]
    await _send_ephemeral(interaction, content=f"```text\n{tail}\n```")

async def send_karten_validate(interaction: discord.Interaction):
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
    if not issues:
        await _send_ephemeral(interaction, content="karten.py ist valide.")
        return
    preview = "\n".join(issues[:20])
    more = len(issues) - 20
    if more > 0:
        preview += f"\n... +{more} weitere"
    await _send_ephemeral(interaction, content=f"Probleme gefunden:\n{preview}")

async def send_balance_stats(interaction: discord.Interaction):
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
    await _send_ephemeral(interaction, embed=embed)

class DevActionSelect(ui.Select):
    def __init__(self, requester_id: int):
        self.requester_id = requester_id
        options = [
            SelectOption(label="Maintenance ON", value="maintenance_on"),
            SelectOption(label="Maintenance OFF", value="maintenance_off"),
            SelectOption(label="Delete user data", value="delete_user"),
            SelectOption(label="DB backup", value="db_backup"),
            SelectOption(label="Give dust", value="give_dust"),
            SelectOption(label="Grant card", value="grant_card"),
            SelectOption(label="Revoke card", value="revoke_card"),
            SelectOption(label="Set daily reset", value="set_daily"),
            SelectOption(label="Set mission reset", value="set_mission"),
            SelectOption(label="Health", value="health"),
            SelectOption(label="Debug DB", value="debug_db"),
            SelectOption(label="Debug user", value="debug_user"),
            SelectOption(label="Debug sync", value="debug_sync"),
            SelectOption(label="Logs last", value="logs_last"),
            SelectOption(label="Karten validate", value="karten_validate"),
        ]
        super().__init__(placeholder="Dev-Tools waehlen...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        if not await require_owner_or_dev(interaction):
            return
        if not await is_channel_allowed(interaction):
            return

        action = self.values[0]
        if action == "maintenance_on":
            if interaction.guild is None:
                await _send_ephemeral(interaction, content="Nur in Servern verfuegbar.")
                return
            await set_maintenance_mode(interaction.guild_id, True)
            await _send_ephemeral(interaction, content="Wartungsmodus aktiviert.")
            return
        if action == "maintenance_off":
            if interaction.guild is None:
                await _send_ephemeral(interaction, content="Nur in Servern verfuegbar.")
                return
            await set_maintenance_mode(interaction.guild_id, False)
            await _send_ephemeral(interaction, content="Wartungsmodus deaktiviert.")
            return
        if action == "delete_user":
            user_id, user_name = await _select_user(interaction, "Waehle den Nutzer fuer Loeschen:")
            if not user_id:
                return
            view = ConfirmDeleteUserView(interaction.user.id, user_id, user_name)
            await _send_ephemeral(interaction, content=f"Wirklich alle Bot-Daten von {user_name} loeschen?", view=view)
            return
        if action == "db_backup":
            await send_db_backup(interaction)
            return
        if action == "give_dust":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Dust:")
            if not user_id:
                return
            amount = await _select_number(interaction, "Menge waehlen", [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000])
            if not amount:
                return
            await add_infinitydust(user_id, int(amount))
            await _send_ephemeral(interaction, content=f"{user_name} erhaelt {amount}x Infinitydust.")
            return
        if action == "grant_card":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Karte vergeben:")
            if not user_id:
                return
            card_name = await _select_card(interaction, "Karte auswaehlen:")
            if not card_name:
                return
            amount = await _select_number(interaction, "Anzahl waehlen", [1, 2, 5, 10, 20, 50, 100])
            if not amount:
                return
            await add_karte_amount(user_id, card_name, int(amount))
            await _send_ephemeral(interaction, content=f"{user_name} erhaelt {amount}x {card_name}.")
            return
        if action == "revoke_card":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Karte abziehen:")
            if not user_id:
                return
            card_name = await _select_card(interaction, "Karte auswaehlen:")
            if not card_name:
                return
            amount = await _select_number(interaction, "Anzahl waehlen", [1, 2, 5, 10, 20, 50, 100])
            if not amount:
                return
            new_amount = await remove_karte_amount(user_id, card_name, int(amount))
            await _send_ephemeral(interaction, content=f"Neue Menge {card_name} bei {user_name}: {new_amount}.")
            return
        if action == "set_daily":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Daily-Reset:")
            if not user_id:
                return
            async with db_context() as db:
                await db.execute(
                    "INSERT INTO user_daily (user_id, last_daily) VALUES (?, 0) "
                    "ON CONFLICT(user_id) DO UPDATE SET last_daily = 0",
                    (user_id,),
                )
                await db.commit()
            await _send_ephemeral(interaction, content=f"Daily fuer {user_name} zurueckgesetzt.")
            return
        if action == "set_mission":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Mission-Reset:")
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
            await _send_ephemeral(interaction, content=f"Mission-Reset fuer {user_name} gesetzt.")
            return
        if action == "health":
            await send_health(interaction)
            return
        if action == "debug_db":
            await send_db_debug(interaction)
            return
        if action == "debug_user":
            user_id, user_name = await _select_user(interaction, "Waehle Nutzer fuer Debug:")
            if not user_id:
                return
            await send_debug_user(interaction, user_id, user_name)
            return
        if action == "debug_sync":
            synced = await bot.tree.sync()
            await _send_ephemeral(interaction, content=f"Sync abgeschlossen: {len(synced)} Commands.")
            return
        if action == "logs_last":
            count = await _select_number(interaction, "Anzahl Log-Zeilen", [10, 20, 50, 100, 200])
            if not count:
                return
            await send_logs_last(interaction, int(count))
            return
        if action == "karten_validate":
            await send_karten_validate(interaction)
            return

class DevPanelView(ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(DevActionSelect(requester_id))

    @ui.button(label="Zurueck", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        embed = discord.Embed(title="Panel", description="Hauptmenue")
        await _edit_panel_message(interaction, embed=embed, view=PanelHomeView(self.requester_id))

class StatsPanelView(ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id

    @ui.button(label="Balance Stats anzeigen", style=discord.ButtonStyle.primary)
    async def show_stats(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        await send_balance_stats(interaction)

    @ui.button(label="Zurueck", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        embed = discord.Embed(title="Panel", description="Hauptmenue")
        await _edit_panel_message(interaction, embed=embed, view=PanelHomeView(self.requester_id))

class PanelHomeView(ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id

    @ui.button(label="Dev/Tools", style=discord.ButtonStyle.primary)
    async def dev_tools(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        embed = discord.Embed(title="Dev/Tools", description="Aktionen waehlen")
        await _edit_panel_message(interaction, embed=embed, view=DevPanelView(self.requester_id))

    @ui.button(label="Stats", style=discord.ButtonStyle.secondary)
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        embed = discord.Embed(title="Stats", description="Statistik-Tools")
        await _edit_panel_message(interaction, embed=embed, view=StatsPanelView(self.requester_id))

    @ui.button(label="Schliessen", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Nicht dein Menue.", ephemeral=True)
            return
        await _edit_panel_message(interaction, content="Panel geschlossen.", embed=None, view=None)

@bot.tree.command(name="panel", description="Dev-Panel (nur Basti/Dev)")
async def panel(interaction: discord.Interaction):
    if not await require_owner_or_dev(interaction):
        return
    if not await is_channel_allowed(interaction):
        return
    embed = discord.Embed(title="Panel", description="Hauptmenue")
    await interaction.response.send_message(embed=embed, view=PanelHomeView(interaction.user.id), ephemeral=True)

# /balance stats (oeffentlich)
BALANCE_GROUP = app_commands.Group(name="balance", description="Balance-Statistiken")

@BALANCE_GROUP.command(name="stats", description="Zeigt Balance-Statistiken")
async def balance_stats(interaction: discord.Interaction):
    await send_balance_stats(interaction)

bot.tree.add_command(BALANCE_GROUP)
# =========================
# Präsenz-Status Kreise + Live-User-Picker (wiederverwendbar für /fight und /vaultlook)
# =========================

# Mapping: Discord Presence -> Farbe/Circle + Sort-Priorität
class StatusUserPickerView(ui.View):
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
        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        choice = self.values[0]
        new_status = status_map.get(choice, discord.Status.online)
        try:
            await interaction.client.change_presence(status=new_status)
            labels = {
                "online": "Online",
                "idle": "Abwesend",
                "dnd": "Bitte nicht stören",
                "invisible": "Unsichtbar",
            }
            embed = discord.Embed(
                title="✅ Bot-Status geändert",
                description=f"Neuer Status: {labels.get(choice)}",
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

class BotStatusView(ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.add_item(BotStatusSelect(requester_id))

@bot.tree.command(name="bot_status", description="Setze den Status des Bots über ein Auswahlmenü")
async def bot_status(interaction: discord.Interaction):
    # Reagiere nur in erlaubten Kanälen (konsistent mit anderen Commands)
    if not await is_channel_allowed(interaction):
        return
    view = BotStatusView(interaction.user.id)
    embed = discord.Embed(
        title="🤖 Bot-Status setzen",
        description="Wähle den gewünschten Status:\n• Online\n• Abwesend\n• Bitte nicht stören\n• Unsichtbar",
        color=0x2b90ff
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
if __name__ == "__main__":
    token = get_bot_token()
    try:
        bot.run(token)
    finally:
        asyncio.run(close_db())
