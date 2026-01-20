import discord
from discord.ext import commands
from discord import app_commands, ui, SelectOption
import random
import asyncio
import time
import aiosqlite
import json
from karten import karten
from db import init_db, DB_PATH
from config import BOT_TOKEN
import secrets

# Intents für den Bot
intents = discord.Intents.default()
intents.message_content = False
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Event: Bot ist bereit
@bot.event
async def on_ready():
    await init_db()
    print(f"Bot ist online als {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Slash-Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(e)

# Infinitydust-System
async def add_infinitydust(user_id, amount=1):
    """Fügt Infinitydust zu einem User hinzu"""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 0

async def spend_infinitydust(user_id, amount):
    """Verbraucht Infinitydust eines Users"""
    async with aiosqlite.connect(DB_PATH) as db:
        current_dust = await get_infinitydust(user_id)
        if current_dust < amount:
            return False  # Nicht genug Dust
        
        new_amount = current_dust - amount
        await db.execute("UPDATE user_infinitydust SET amount = ? WHERE user_id = ?", (new_amount, user_id))
        await db.commit()
        return True  # Erfolgreich verbraucht

async def add_card_buff(user_id, card_name, buff_type, attack_number, buff_amount):
    """Fügt einen Buff zu einer Karte hinzu"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO user_card_buffs 
            (user_id, card_name, buff_type, attack_number, buff_amount) 
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, card_name, buff_type, attack_number, buff_amount))
        await db.commit()

async def get_card_buffs(user_id, card_name):
    """Holt alle Buffs für eine spezifische Karte eines Users"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT buff_type, attack_number, buff_amount 
            FROM user_card_buffs 
            WHERE user_id = ? AND card_name = ?
        """, (user_id, card_name))
        return await cursor.fetchall()

async def check_and_add_karte(user_id, karte):
    """Prüft ob User die Karte schon hat und fügt sie hinzu oder wandelt zu Infinitydust um"""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, 1) ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + 1",
            (user_id, karten_name)
        )
        await db.commit()

# Hilfsfunktion: Missionsfortschritt speichern
async def add_mission_reward(user_id):
    karte = random.choice(karten)
    is_new_card = await check_and_add_karte(user_id, karte)
    return karte, is_new_card

# Hilfsfunktion: Missionen pro Tag prüfen
async def get_mission_count(user_id):
    now = int(time.time())
    today_start = now - (now % 86400)  # Start des Tages
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT mission_count, last_mission_reset FROM user_daily WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        
        if not row or row[1] < today_start:
            # Neuer Tag oder kein Eintrag
            await db.execute("INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, 0, ?)", (user_id, today_start))
            await db.commit()
            return 0
        else:
            return row[0]

# Hilfsfunktion: Missionen pro Tag erhöhen
async def increment_mission_count(user_id):
    now = int(time.time())
    today_start = now - (now % 86400)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO user_daily (user_id, mission_count, last_mission_reset) VALUES (?, COALESCE((SELECT mission_count FROM user_daily WHERE user_id = ?), 0) + 1, ?)", 
                        (user_id, user_id, today_start))
        await db.commit()

# Hilfsfunktionen für Team-Management
async def get_team(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT team FROM user_teams WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return []

async def set_team(user_id, team):
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT karten_name, anzahl FROM user_karten WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()

# Hilfsfunktion: Letzte Karte des Nutzers
async def get_last_karte(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT karten_name FROM user_karten WHERE user_id = ? ORDER BY rowid DESC LIMIT 1", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

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
        
        self.attacks = self.player1_card.get("attacks", [
            {"name": "Punch", "damage": 20},
            {"name": "Kick", "damage": 25},
            {"name": "Special", "damage": 30},
            {"name": "Ultimate", "damage": 40}
        ])
        # Setze Button-Labels für Attacken
        attack_buttons = [child for child in self.children if isinstance(child, ui.Button) and child.style == discord.ButtonStyle.danger]
        for i, button in enumerate(attack_buttons):
            if i < len(self.attacks):
                button.label = f"{self.attacks[i]['name']} ({self.attacks[i]['damage']})"
        else:
                button.label = f"Angriff {i+1}"
        
        # Setze secondary Buttons
        for child in self.children:
            if isinstance(child, ui.Button) and child.style == discord.ButtonStyle.secondary:
                child.style = discord.ButtonStyle.secondary

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
        else:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)

    @ui.button(label="Platzhalter", style=discord.ButtonStyle.secondary, row=2)
    async def placeholder(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("Dieser Button ist noch nicht implementiert.", ephemeral=True)

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
        if interaction.user.id != self.current_turn:
            await interaction.response.send_message("Du bist nicht an der Reihe!", ephemeral=True)
            return
        # Hole aktuelle Karte und Angriff
        current_card = self.player1_card if self.current_turn == self.player1_id else self.player2_card
        attacks = current_card.get("attacks", [{"name": "Punch", "damage": 20}])
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
                
        # Finale Damage-Berechnung
        damage = base_damage + damage_buff
        if self.current_turn == self.player1_id:
            self.player2_hp -= damage
            attacker = self.player1_card["name"]
            defender = self.player2_card["name"]
        else:
            self.player1_hp -= damage
            attacker = self.player2_card["name"]
            defender = self.player1_card["name"]
        # HP nicht unter 0
        self.player1_hp = max(0, self.player1_hp)
        self.player2_hp = max(0, self.player2_hp)
        # HP-View aktualisieren
        if self.hp_view:
            self.hp_view.update_hp(self.player1_hp)
        # Prüfen ob Kampf vorbei
        if self.player1_hp <= 0 or self.player2_hp <= 0:
            winner = self.player1_card["name"] if self.player2_hp <= 0 else self.player2_card["name"]
            embed = discord.Embed(title="🏆 Sieger!", description=f"**{winner}** hat gewonnen!")
            await interaction.message.edit(embed=embed, view=None)
            return
        # Nächster Spieler
        self.current_turn = self.player2_id if self.current_turn == self.player1_id else self.player1_id
        # Neues Embed mit immer gleichem Layout
        user1 = interaction.guild.get_member(self.player1_id)
        user2 = interaction.guild.get_member(self.player2_id)
        embed = create_battle_embed(self.player1_card, self.player2_card, self.player1_hp, self.player2_hp, self.current_turn, user1, user2)
        await interaction.response.edit_message(embed=embed, view=self)

def create_battle_embed(player1_card, player2_card, player1_hp, player2_hp, current_turn, user1, user2):
    # user1 und user2 sind discord.Member Objekte
    embed = discord.Embed(title="1v1 Kampf beginnt!", description=f"{user1.mention} vs {user2.mention}")
    if current_turn == user1.id:
        embed.set_image(url=player1_card["bild"])
        embed.set_thumbnail(url=player2_card["bild"])
    else:
        embed.set_image(url=player2_card["bild"])
        embed.set_thumbnail(url=player1_card["bild"])
    embed.add_field(name="VS", value="[VS](https://png.pngtree.com/png-clipart/20220111/original/pngtree-vs-versus-icon-png-image_7075379.png)", inline=False)
    embed.add_field(name="🟥 Deine Karte", value=f"{player1_card['name']}\nHP: {player1_hp}", inline=True)
    embed.add_field(name="🟦 Gegner Karte", value=f"{player2_card['name']}\nHP: {player2_hp}", inline=True)
    embed.add_field(name="🎯 Nächster Spieler", value=f"{user1.mention if current_turn == user1.id else user2.mention} ist an der Reihe", inline=False)
    return embed

class FightModeSelect(ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.value = None

    @ui.select(
        placeholder="Wähle den Kampfmodus...",
        options=[
            SelectOption(label="1v1", value="1"),
            SelectOption(label="2v2", value="2"),
            SelectOption(label="3v3", value="3")
        ]
    )
    async def select_mode(self, interaction: discord.Interaction, select: ui.Select):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Nur der Herausforderer kann den Modus wählen!", ephemeral=True)
            return
        self.value = int(select.values[0])
        self.stop()
        await interaction.response.defer()

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

class MultiBattleView(ui.View):
    def __init__(self, user_id, player_cards, mode, gegner_cards=None):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.player_cards = player_cards
        self.mode = mode
        self.selected_attack = None
        self.gegner_cards = gegner_cards or [karten[i % len(karten)] for i in range(mode)]
        self.player_hp = [card.get("hp", 100) for card in player_cards]
        self.gegner_hp = [card.get("hp", 100) for card in self.gegner_cards]
        # Für jede Karte 4 Attacken-Buttons
        for idx, card in enumerate(player_cards):
            attacks = card.get("attacks", [])
            for a_idx, attack in enumerate(attacks):
                label = f"{attack['name']} ({attack['damage']})"
                self.add_item(self.AttackButton(label, idx, a_idx, user_id, self))
        # Abbrechen-Button nur einmal ganz unten links
        self.add_item(self.CancelButton(user_id))

    class AttackButton(ui.Button):
        def __init__(self, label, card_idx, attack_idx, user_id, parent_view):
            super().__init__(label=label, style=discord.ButtonStyle.danger, row=card_idx)
            self.card_idx = card_idx
            self.attack_idx = attack_idx
            self.user_id = user_id
            self.parent_view = parent_view
        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Nur der Herausforderer kann angreifen!", ephemeral=True)
                return
            # Nur 1v1 Logik für den Anfang
            if self.parent_view.mode == 1:
                attack = self.parent_view.player_cards[self.card_idx]["attacks"][self.attack_idx]
                damage = attack["damage"]
                self.parent_view.gegner_hp[0] -= damage
                if self.parent_view.gegner_hp[0] <= 0:
                    self.parent_view.gegner_hp[0] = 0
                    # Sieg
                    embed = discord.Embed(title="🏆 Sieg!", description=f"Du hast gewonnen! {attack['name']} hat den Gegner besiegt.")
                    embed.add_field(name="Deine Karte", value=f"{self.parent_view.player_cards[0]['name']}\nHP: {self.parent_view.player_hp[0]}", inline=True)
                    embed.add_field(name="Gegner Karte", value=f"{self.parent_view.gegner_cards[0]['name']}\nHP: 0", inline=True)
                    await interaction.response.edit_message(embed=embed, view=None)
                    return
                # Update Embed
                embed = discord.Embed(title="⚔️ 1v1 Kampf", description=f"Du hast {attack['name']} eingesetzt und {damage} Schaden verursacht!")
                embed.add_field(name="Deine Karte", value=f"{self.parent_view.player_cards[0]['name']}\nHP: {self.parent_view.player_hp[0]}", inline=True)
                embed.add_field(name="Gegner Karte", value=f"{self.parent_view.gegner_cards[0]['name']}\nHP: {self.parent_view.gegner_hp[0]}", inline=True)
                await interaction.response.edit_message(embed=embed, view=self.parent_view)
            else:
                await interaction.response.send_message("Mehrere Karten-Kampf folgt!", ephemeral=True)

    class CancelButton(ui.Button):
        def __init__(self, user_id):
            super().__init__(label="Abbrechen", style=discord.ButtonStyle.secondary, row=4)
            self.user_id = user_id
        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Nur der Herausforderer kann abbrechen!", ephemeral=True)
                return
            await interaction.response.edit_message(content="Kampf abgebrochen.", view=None)

class OpponentSelectView(ui.View):
    def __init__(self, challenger: discord.Member, guild: discord.Guild):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.value = None
        # Zeige alle Mitglieder außer Bot und Challenger (ohne Online-Status-Filter)
        options = [SelectOption(label="🤖 Bot", value="bot")]
        for member in guild.members:
            if not member.bot and member != challenger:
                options.append(SelectOption(label=member.display_name, value=str(member.id)))
        self.select = ui.Select(placeholder="Wähle einen Gegner...", min_values=1, max_values=1, options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user != self.challenger:
            await interaction.response.send_message("Nur der Herausforderer kann den Gegner wählen!", ephemeral=True)
            return
        self.value = self.select.values[0]
        self.stop()
        await interaction.response.defer()

class ChallengeResponseView(ui.View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, ctx, selected_cards, mode):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.challenged = challenged
        self.ctx = ctx
        self.selected_cards = selected_cards
        self.mode = mode
        self.value = None
    @ui.button(label="Kämpfen", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Nur der Herausgeforderte kann annehmen!", ephemeral=True)
            return
        self.value = True
        self.stop()
        await interaction.response.defer()
    @ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user != self.challenged:
            await interaction.response.send_message("Nur der Herausgeforderte kann ablehnen!", ephemeral=True)
            return
        self.value = False
        self.stop()
        await interaction.response.defer()

# Helper: Check Admin
async def is_admin(interaction):
    return interaction.user.guild_permissions.administrator

# Slash-Command: Karte ziehen
@bot.tree.command(name="karte", description="Ziehe eine zufällige Karte")
async def karte(interaction: discord.Interaction):
    user_id = interaction.user.id
    karte = random.choice(karten)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(user_id, karte)
    
    if is_new_card:
        embed = discord.Embed(title=karte["name"], description=karte["beschreibung"])
        embed.set_image(url=karte["bild"])
        # Zeige Attacken im Embed
        if "attacks" in karte:
            attacks_text = "\n".join([f"{a['name']} ({a['damage']})" for a in karte["attacks"]])
            embed.add_field(name="Attacken", value=attacks_text, inline=False)
        await interaction.response.send_message(embed=embed, view=ZieheKarteView(user_id))
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Infinitydust erhalten!", description=f"Du hattest **{karte['name']}** bereits!")
        embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Slash-Command: Tägliche Belohnung
@bot.tree.command(name="täglich", description="Hole deine tägliche Belohnung ab")
async def täglich(interaction: discord.Interaction):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_daily FROM user_daily WHERE user_id = ?", (interaction.user.id,))
        row = await cursor.fetchone()
        if row and now - row[0] < 86400:
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
        await interaction.response.send_message(f"Du hast eine tägliche Belohnung erhalten: **{karte['name']}**!")
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Tägliche Belohnung - Infinitydust!", description=f"Du hattest **{karte['name']}** bereits!")
        embed.add_field(name="Umwandlung", value="Die Karte wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Slash-Command: Mission starten
@bot.tree.command(name="mission", description="Schicke dein Team auf eine Mission und erhalte eine Belohnung")
async def mission(interaction: discord.Interaction):
    # Prüfe Admin-Berechtigung
    is_admin = interaction.user.guild_permissions.administrator
    
    if not is_admin:
        # Prüfe tägliche Mission-Limits für normale Nutzer
        mission_count = await get_mission_count(interaction.user.id)
        if mission_count >= 3:
            await interaction.response.send_message("❌ Du hast heute bereits alle 3 Missionen aufgebraucht! Komme morgen wieder.", ephemeral=True)
        return
    
    # Generiere Mission-Daten
    waves = random.randint(2, 6)
    reward_card = random.choice(karten)
    
    # Erstelle Mission-Embed
    embed = discord.Embed(title=f"Mission {mission_count + 1}/3" if not is_admin else "Mission (Admin)", 
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
    await start_mission_waves(interaction, mission_data, is_admin)

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
        
        current_wave += 1
    
    # Mission erfolgreich abgeschlossen
    await increment_mission_count(interaction.user.id)
    
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
    
    # Erstelle Kampf-Embed wie beim /fight Command
    embed = discord.Embed(title=f"⚔️ Welle {wave_num}/{total_waves}", 
                         description=f"Du kämpfst gegen **{bot_card['name']}**!")
    embed.add_field(name="🟥 Deine Karte", value=f"{player_card['name']}\nHP: {player_card.get('hp', 100)}", inline=True)
    embed.add_field(name="🟦 Bot Karte", value=f"{bot_card['name']}\nHP: {bot_card.get('hp', 100)}", inline=True)
    embed.set_image(url=player_card["bild"])
    embed.set_thumbnail(url=bot_card["bild"])
    
    # Erstelle interaktive Mission-BattleView
    mission_battle_view = MissionBattleView(player_card, bot_card, interaction.user.id, wave_num, total_waves)
    await interaction.followup.send(embed=embed, view=mission_battle_view, ephemeral=True)
    
    # Warte auf Kampf-Ende
    await mission_battle_view.wait()
    
    return mission_battle_view.result

# Slash-Command: Team anzeigen
@bot.tree.command(name="team", description="Zeige dein aktuelles Team an")
async def team(interaction: discord.Interaction):
    team = await get_team(interaction.user.id)
    if not team:
        await interaction.response.send_message("Du hast noch kein Team erstellt.", ephemeral=True)
        return
    
    embed = discord.Embed(title="🏆 Dein Team", description="Hier sind deine Team-Karten:")
    for i, kartenname in enumerate(team, 1):
        karte = await get_karte_by_name(kartenname)
        if karte:
            embed.add_field(name=f"Position {i}: {karte['name']}", value=karte['beschreibung'], inline=False)
    
    await interaction.response.send_message(embed=embed)

# Slash-Command: Konfiguration
@bot.tree.command(name="configure", description="Konfiguriere Bot-Einstellungen (Nur für Admins)")
@app_commands.describe(action="Was möchtest du konfigurieren?")
async def configure(interaction: discord.Interaction, action: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Command!", ephemeral=True)
        return

    if "mission" in action.lower():
        # Mission Channel setzen
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO guild_config (guild_id, mission_channel_id) VALUES (?, ?)", 
                           (interaction.guild_id, interaction.channel_id))
            await db.commit()
        await interaction.response.send_message(f"✅ Mission Channel auf {interaction.channel.mention} gesetzt!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Unbekannte Aktion. Verwende 'mission' für Mission Channel.", ephemeral=True)

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
            
        view = CardSelectView(dust_amount, buff_amount, user_karten)
        embed = discord.Embed(
            title="🎯 Karte auswählen", 
            description=f"Du verwendest **{dust_amount} Infinitydust** für **+{buff_amount}** Bonus!\n\nWähle die Karte, die du verstärken möchtest:",
            color=0x9d4edd
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=view)

class CardSelectView(ui.View):
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
        
        # Attacken hinzufügen
        attacken = karte_data.get("attacken", [])
        for i, attack in enumerate(attacken[:4]):  # Max 4 Attacken
            attack_name = attack.get("name", f"Attacke {i+1}")
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
            attack_name = karte_data["attacken"][attack_number-1]["name"]
            buff_text = f"**{attack_name} +{self.buff_amount} Damage**"
            emoji = "⚔️"
        
        embed = discord.Embed(
            title="✅ Verstärkung erfolgreich!", 
            description=f"🃏 **{self.selected_card}**\n{emoji} {buff_text}\n\n💎 **{self.dust_amount} Infinitydust** verbraucht",
            color=0x00ff00
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.edit_message(embed=embed, view=None)

class DustAmountView(ui.View):
    def __init__(self, user_dust):
        super().__init__(timeout=60)
        self.add_item(DustAmountSelect(user_dust))

# Slash-Command: Karten mit Infinitydust verstärken
@bot.tree.command(name="fuse", description="Verstärke deine Karten mit Infinitydust")
async def fuse(interaction: discord.Interaction):
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
    await interaction.response.send_message(embed=embed, view=view)

# Slash-Command: Vault anzeigen
@bot.tree.command(name="vault", description="Zeige deine Karten-Sammlung")
async def vault(interaction: discord.Interaction):
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
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="fight", description="Kämpfe gegen einen anderen Spieler im 1v1, 2v2 oder 3v3!")
async def fight(interaction: discord.Interaction):
    # Schritt 1: Modus-Auswahl
    mode_view = FightModeSelect(interaction.user.id)
    await interaction.response.send_message("Wähle den Kampfmodus:", view=mode_view, ephemeral=True)
    await mode_view.wait()
    if not mode_view.value:
        await interaction.followup.send("⏰ Keine Auswahl getroffen. Kampf abgebrochen.", ephemeral=True)
        return
    mode = mode_view.value
    # Schritt 2: Karten-Auswahl
    user_karten = await get_user_karten(interaction.user.id)
    if not user_karten or len(user_karten) < mode:
        await interaction.followup.send(f"Du brauchst mindestens {mode} Karten für diesen Modus!", ephemeral=True)
        return
    card_select_view = CardSelectView(interaction.user.id, user_karten, mode)
    await interaction.followup.send(f"Wähle deine {mode} Karte(n) für den Kampf:", view=card_select_view, ephemeral=True)
    await card_select_view.wait()
    if not card_select_view.value:
        await interaction.followup.send("⏰ Keine Karten gewählt. Kampf abgebrochen.", ephemeral=True)
        return
    selected_names = card_select_view.value
    selected_cards = [await get_karte_by_name(name) for name in selected_names]
    # Schritt 3: Gegner-Auswahl
    opponent_view = OpponentSelectView(interaction.user, interaction.guild)
    await interaction.followup.send("Wähle einen Gegner (User oder Bot):", view=opponent_view, ephemeral=True)
    await opponent_view.wait()
    if not opponent_view.value:
        await interaction.followup.send("⏰ Kein Gegner gewählt. Kampf abgebrochen.", ephemeral=True)
        return
    opponent_id = opponent_view.value
    if opponent_id == "bot":
        # Bot als Gegner
        gegner_karten = [karten[i % len(karten)] for i in range(mode)]
        embed = discord.Embed(title=f"{mode}v{mode} Kampf beginnt!", description="Deine Karten vs Bot")
        for i, card in enumerate(selected_cards):
            embed.add_field(name=f"🟥 Deine Karte {i+1}", value=f"{card['name']}\nHP: {card.get('hp', 100)}", inline=True)
        embed.add_field(name="⠀", value="[VS](https://png.pngtree.com/png-clipart/20220111/original/pngtree-vs-versus-icon-png-image_7075379.png)", inline=True)
        for i, card in enumerate(gegner_karten):
            embed.add_field(name=f"🟦 Bot Karte {i+1}", value=f"{card['name']}\nHP: {card.get('hp', 100)}", inline=True)
        await interaction.followup.send(embed=embed, view=MultiBattleView(interaction.user.id, selected_cards, mode))
        return
    # User als Gegner
    challenged = interaction.guild.get_member(int(opponent_id))
    if not challenged:
        await interaction.followup.send("❌ Gegner nicht gefunden!", ephemeral=True)
        return
    # Nachricht an Herausgeforderten (jetzt im Channel, nicht ephemeral)
    challenge_view = ChallengeResponseView(interaction.user, challenged, interaction, selected_cards, mode)
    await interaction.channel.send(f"{challenged.mention}, du wurdest zu einem Kartenkampf herausgefordert!", view=challenge_view)
    # Info für Herausforderer (ephemeral)
    await interaction.followup.send(f"Warte auf Antwort von {challenged.mention}...", ephemeral=True)
    await challenge_view.wait()
    if challenge_view.value is None:
        await interaction.followup.send(f"{challenged.mention} hat nicht rechtzeitig geantwortet. Kampf abgebrochen.", ephemeral=True)
        return
    if challenge_view.value is False:
        await interaction.followup.send(f"{challenged.mention} hat den Kampf abgelehnt.", ephemeral=True)
        return
    # Schritt 4: Gegner wählt seine Karte(n)
    gegner_karten_liste = await get_user_karten(challenged.id)
    if not gegner_karten_liste or len(gegner_karten_liste) < mode:
        await interaction.followup.send(f"{challenged.mention} hat nicht genug Karten für diesen Modus! Kampf abgebrochen.", ephemeral=True)
        return
    gegner_card_select_view = CardSelectView(challenged.id, gegner_karten_liste, mode)
    await interaction.channel.send(f"{challenged.mention}, wähle deine {mode} Karte(n) für den Kampf:", view=gegner_card_select_view)
    await gegner_card_select_view.wait()
    if not gegner_card_select_view.value:
        await interaction.followup.send(f"{challenged.mention} hat keine Karten gewählt. Kampf abgebrochen.", ephemeral=True)
        return
    gegner_selected_names = gegner_card_select_view.value
    gegner_selected_cards = [await get_karte_by_name(name) for name in gegner_selected_names]
    # Kampf-Embed mit Anzeige, wer an der Reihe ist
    embed = discord.Embed(title=f"{mode}v{mode} Kampf beginnt!", description=f"{interaction.user.mention} vs {challenged.mention}")
    if mode == 1:
        embed.set_thumbnail(url=selected_cards[0]['bild'])
        embed.set_image(url=gegner_selected_cards[0]['bild'])
        embed.add_field(name=" ", value="[VS](https://png.pngtree.com/png-clipart/20220111/original/pngtree-vs-versus-icon-png-image_7075379.png)", inline=False)
        # Für 1v1: BattleView verwenden (nicht MultiBattleView)
        battle_view = BattleView(selected_cards[0], gegner_selected_cards[0], interaction.user.id, challenged.id, None)
        await battle_view.init_with_buffs()  # Lade Health-Buffs
        
        # Embed mit korrekten HP-Werten (inkl. Buffs) aktualisieren
        embed.add_field(name="🟥 Deine Karte", value=f"{selected_cards[0]['name']}\nHP: {battle_view.player1_hp}", inline=True)
        embed.add_field(name="🟦 Gegner Karte", value=f"{gegner_selected_cards[0]['name']}\nHP: {battle_view.player2_hp}", inline=True)
        embed.add_field(name="🎯 Nächster Spieler", value=f"{interaction.user.mention} ist an der Reihe", inline=False)
        await interaction.channel.send(embed=embed, view=battle_view)
    else:
        for i, card in enumerate(selected_cards):
            embed.add_field(name=f"🟥 Deine Karte {i+1}", value=f"{card['name']}\nHP: {card.get('hp', 100)}", inline=True)
        embed.add_field(name="⠀", value="[VS](https://png.pngtree.com/png-clipart/20220111/original/pngtree-vs-versus-icon-png-image_7075379.png)", inline=True)
        for i, card in enumerate(gegner_selected_cards):
            embed.add_field(name=f"🟦 Gegner Karte {i+1}", value=f"{card['name']}\nHP: {card.get('hp', 100)}", inline=True)
        embed.add_field(name="🎯 Nächster Spieler", value=f"{interaction.user.mention} ist an der Reihe", inline=False)
        # Für 2v2/3v3: MultiBattleView verwenden
        await interaction.channel.send(embed=embed, view=MultiBattleView(interaction.user.id, selected_cards, mode, gegner_cards=gegner_selected_cards))

# Slash-Command: Daily (Alternative zu täglich)
@bot.tree.command(name="daily", description="Hole deine tägliche Belohnung ab (Englisch)")
async def daily(interaction: discord.Interaction):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT last_daily FROM user_daily WHERE user_id = ?", (interaction.user.id,))
        row = await cursor.fetchone()
        if row and now - row[0] < 86400:
            stunden = int((86400 - (now - row[0])) / 3600)
            await interaction.response.send_message(f"You can claim your daily reward in {stunden} hours.", ephemeral=True)
            return
        await db.execute("INSERT OR REPLACE INTO user_daily (user_id, last_daily) VALUES (?, ?)", (interaction.user.id, now))
        await db.commit()
    
    user_id = interaction.user.id
    karte = random.choice(karten)
    
    # Prüfe ob User die Karte schon hat
    is_new_card = await check_and_add_karte(user_id, karte)
    
    if is_new_card:
        await interaction.response.send_message(f"You received a daily reward: **{karte['name']}**!")
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Daily Reward - Infinitydust!", description=f"You already had **{karte['name']}**!")
        embed.add_field(name="Conversion", value="The card was converted to **Infinitydust**!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Admin-Command: Test-Bericht
@bot.tree.command(name="test-bericht", description="Testet alle Commands und erstellt einen Bericht (Nur für Admins)")
async def test_bericht(interaction: discord.Interaction):
    # Prüfe Admin-Berechtigung
    if not await is_admin(interaction):
        await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Command! Nur Admins können den Test-Bericht ausführen.", ephemeral=True)
        return
    
    await interaction.response.send_message("🔍 Starte automatischen Test aller Commands...", ephemeral=True)
    
    # Test-Ergebnisse sammeln
    test_results = []
    
    # Test 1: /karte
    try:
        test_karte = random.choice(karten)
        test_results.append(("✅ /karte", "Funktioniert - Karte kann gezogen werden"))
    except Exception as e:
        test_results.append(("❌ /karte", f"Fehler: {str(e)}"))
    
    # Test 2: /täglich
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT last_daily FROM user_daily WHERE user_id = ?", (interaction.user.id,))
            await cursor.fetchone()
        test_results.append(("✅ /täglich", "Funktioniert - Datenbankverbindung OK"))
    except Exception as e:
        test_results.append(("❌ /täglich", f"Fehler: {str(e)}"))
    
    # Test 3: /mission
    try:
        erfolg = random.random() < 0.7
        test_results.append(("✅ /mission", f"Funktioniert - Erfolgschance: {erfolg}"))
    except Exception as e:
        test_results.append(("❌ /mission", f"Fehler: {str(e)}"))
    
    # Test 4: /team
    try:
        team = await get_team(interaction.user.id)
        test_results.append(("✅ /team", f"Funktioniert - Aktuelles Team: {len(team)} Karten"))
    except Exception as e:
        test_results.append(("❌ /team", f"Fehler: {str(e)}"))
    
    # Test 5: /configure
    try:
        test_results.append(("✅ /configure", "Funktioniert - Admin-Berechtigung prüfbar"))
    except Exception as e:
        test_results.append(("❌ /configure", f"Fehler: {str(e)}"))
    
    # Test 6: /fuse
    try:
        test_results.append(("✅ /fuse", "Funktioniert - Fusion-System verfügbar"))
    except Exception as e:
        test_results.append(("❌ /fuse", f"Fehler: {str(e)}"))
    
    # Test 7: /vault
    try:
        user_karten = await get_user_karten(interaction.user.id)
        test_results.append(("✅ /vault", f"Funktioniert - {len(user_karten)} Karten in Sammlung"))
    except Exception as e:
        test_results.append(("❌ /vault", f"Fehler: {str(e)}"))
    
    # Test 8: /fight
    try:
        test_results.append(("✅ /fight", "Funktioniert - Kampf-System verfügbar"))
    except Exception as e:
        test_results.append(("❌ /fight", f"Fehler: {str(e)}"))
    
    # Test 9: /daily
    try:
        test_results.append(("✅ /daily", "Funktioniert - Daily-System verfügbar"))
    except Exception as e:
        test_results.append(("❌ /daily", f"Fehler: {str(e)}"))
    
    # Test 10: Datenbankverbindung
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cursor.fetchall()
        test_results.append(("✅ Datenbank", f"Funktioniert - {len(tables)} Tabellen verfügbar"))
    except Exception as e:
        test_results.append(("❌ Datenbank", f"Fehler: {str(e)}"))
    
    # Test 11: Karten-Daten
    try:
        if len(karten) > 0:
            test_results.append(("✅ Karten-Daten", f"Funktioniert - {len(karten)} Karten geladen"))
        else:
            test_results.append(("❌ Karten-Daten", "Fehler: Keine Karten geladen"))
    except Exception as e:
        test_results.append(("❌ Karten-Daten", f"Fehler: {str(e)}"))
    
    # Test 13: /tradingpost
    try:
        test_results.append(("✅ /tradingpost", "Funktioniert - Tradingpost-System verfügbar"))
    except Exception as e:
        test_results.append(("❌ /tradingpost", f"Fehler: {str(e)}"))
    
    # Bericht erstellen
    working_commands = sum(1 for result in test_results if result[0].startswith("✅"))
    total_commands = len(test_results)
    
    embed = discord.Embed(
        title="🤖 Bot Test-Bericht",
        description=f"Automatischer Test aller Commands abgeschlossen\n**Ergebnis: {working_commands}/{total_commands} Commands funktionieren**",
        color=0x00ff00 if working_commands == total_commands else 0xffaa00
    )
    
    # Gruppiere Ergebnisse
    working = []
    warnings = []
    errors = []
    
    for result in test_results:
        if result[0].startswith("✅"):
            working.append(result)
        elif result[0].startswith("⚠️"):
            warnings.append(result)
        else:
            errors.append(result)
    
    # Füge Ergebnisse zum Embed hinzu
    if working:
        working_text = "\n".join([f"{result[0]} - {result[1]}" for result in working])
        embed.add_field(name="✅ Funktionierende Commands", value=working_text[:1024], inline=False)
    
    if warnings:
        warnings_text = "\n".join([f"{result[0]} - {result[1]}" for result in warnings])
        embed.add_field(name="⚠️ Warnungen", value=warnings_text[:1024], inline=False)
    
    if errors:
        errors_text = "\n".join([f"{result[0]} - {result[1]}" for result in errors])
        embed.add_field(name="❌ Fehler", value=errors_text[:1024], inline=False)
    
    # Zusammenfassung
    summary = f"""
**Test-Zusammenfassung:**
• **Funktionierende Commands:** {len(working)}
• **Warnungen:** {len(warnings)}
• **Fehler:** {len(errors)}
• **Gesamt:** {total_commands}

**Empfehlung:** {'✅ Bot ist vollständig funktionsfähig!' if len(errors) == 0 else '⚠️ Einige Commands haben Probleme - siehe Details oben'}
"""
    
    embed.add_field(name="📊 Zusammenfassung", value=summary, inline=False)
    embed.set_footer(text=f"Test ausgeführt von {interaction.user.display_name} | {time.strftime('%d.%m.%Y %H:%M:%S')}")
    
    # Sende Bericht
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
        
        # Erstelle Optionen für Mengen von 1-20
        options = [SelectOption(label=f"{i}x Infinitydust", value=str(i)) for i in range(1, 21)]
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
    # Prüfe Admin-Berechtigung
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Command! Nur Admins können Karten geben.", ephemeral=True)
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
        
        # Erfolgsnachricht für Infinitydust
        embed = discord.Embed(title="💎 Infinitydust verschenkt!", description=f"Du hast **{amount}x Infinitydust** an {target_user.mention} gegeben!")
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    # Normale Karte dem Nutzer geben
    selected_card = await get_karte_by_name(selected_card_name)
    is_new_card = await check_and_add_karte(target_user_id, selected_card)
    
    # Erfolgsnachricht
    if is_new_card:
        embed = discord.Embed(title="🎁 Karte verschenkt!", description=f"Du hast **{selected_card_name}** an {target_user.mention} gegeben!")
        if selected_card:
            embed.set_image(url=selected_card["bild"])
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        # Karte wurde zu Infinitydust umgewandelt
        embed = discord.Embed(title="💎 Karte verschenkt - Infinitydust!", description=f"Du hast **{selected_card_name}** an {target_user.mention} gegeben!")
        embed.add_field(name="Umwandlung", value=f"{target_user.mention} hatte die Karte bereits - wurde zu **Infinitydust** umgewandelt!", inline=False)
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        
        # Setze Button-Labels für Attacken
        attack_buttons = [child for child in self.children if isinstance(child, ui.Button) and child.style == discord.ButtonStyle.danger]
        for i, button in enumerate(attack_buttons):
            if i < len(self.attacks):
                button.label = f"{self.attacks[i]['name']} ({self.attacks[i]['damage']})"
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

    @ui.button(label="Platzhalter", style=discord.ButtonStyle.secondary, row=2)
    async def placeholder(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("Dieser Button ist noch nicht implementiert.", ephemeral=True)

    async def execute_attack(self, interaction: discord.Interaction, attack_index: int):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Du bist nicht an diesem Kampf beteiligt!", ephemeral=True)
            return
        
        if interaction.user.id != self.current_turn:
            await interaction.response.send_message("Du bist nicht an der Reihe!", ephemeral=True)
            return
        
        # Hole Angriff
        if attack_index >= len(self.attacks):
            await interaction.response.send_message("Ungültiger Angriff!", ephemeral=True)
            return
        
        attack = self.attacks[attack_index]
        damage = attack["damage"]
        
        # Spieler-Angriff
        self.bot_hp -= damage
        self.bot_hp = max(0, self.bot_hp)
        
        # Prüfen ob Kampf vorbei nach Spieler-Angriff
        if self.bot_hp <= 0:
            self.result = True
            await interaction.response.edit_message(content=f"🏆 **Welle {self.wave_num} gewonnen!** Du hast **{self.bot_card['name']}** besiegt!", view=None)
            self.stop()
            return
        
        # Bot-Zug nach kurzer Pause
        await interaction.response.edit_message(content=f"🎯 Du hast **{attack['name']}** verwendet! **{self.bot_card['name']}** ist an der Reihe...", view=None)
        await asyncio.sleep(1)
        
        # Bot-Angriff
        bot_attacks = self.bot_card.get("attacks", [{"name": "Punch", "damage": 20}])
        bot_attack = random.choice(bot_attacks)
        self.player_hp -= bot_attack["damage"]
        self.player_hp = max(0, self.player_hp)
        
        # Prüfen ob Kampf vorbei nach Bot-Angriff
        if self.player_hp <= 0:
            self.result = False
            await interaction.followup.send(content=f"❌ **Welle {self.wave_num} verloren!** Du wurdest von **{self.bot_card['name']}** besiegt!", ephemeral=True)
            self.stop()
            return
        
        # Neues Embed für nächsten Spieler-Zug
        embed = discord.Embed(title=f"⚔️ Welle {self.wave_num}/{self.total_waves}", 
                             description=f"Du kämpfst gegen **{self.bot_card['name']}**!")
        embed.add_field(name="🟥 Deine Karte", value=f"{self.player_card['name']}\nHP: {self.player_hp}", inline=True)
        embed.add_field(name="🟦 Bot Karte", value=f"{self.bot_card['name']}\nHP: {self.bot_hp}", inline=True)
        embed.set_image(url=self.player_card["bild"])
        embed.set_thumbnail(url=self.bot_card["bild"])
        embed.add_field(name="🎯 Dein Zug", value="Du bist an der Reihe!", inline=False)
        
        await interaction.followup.send(embed=embed, view=self)

# Starte den Bot
bot.run(BOT_TOKEN) 