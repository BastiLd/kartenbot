"""Einrichtung des Level-Systems: /level-einrichten, /level-rolle, /level-kanal.

Alles lässt sich per Klick in Discord einstellen — es muss nie eine ID
kopiert oder in den Code geschrieben werden.

Die Befehle liegen in admin_commands.py, hier steht, was sie tun. So lässt
es sich ohne laufenden Bot testen.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import discord
from discord import ui

from botcore.ui_common import RestrictedView
from level_reward_config import LEVEL_STUFEN
from services import level_rewards as lr

FARBE = 0xF1C40F


def stufen_auswahl() -> list[tuple[str, int]]:
    """(Anzeigetext, Stufe) für die Auswahl in /level-rolle."""
    return [(f"Level {stufe} · {titel}"[:100], int(stufe)) for stufe, titel in LEVEL_STUFEN.items()]


def vorschlag_zeilen(gefunden: dict[int, int], rollennamen: dict[int, str]) -> list[str]:
    """Eine Zeile je Stufe: gefundene Rolle oder „nicht gefunden“."""
    zeilen = []
    for stufe, titel in LEVEL_STUFEN.items():
        rolle_id = gefunden.get(int(stufe))
        if rolle_id:
            zeilen.append(f"✅ Level {stufe} → <@&{rolle_id}> ({rollennamen.get(rolle_id, titel)})")
        else:
            zeilen.append(f"❌ Level {stufe} → nicht gefunden (Rolle „{titel}“)")
    return zeilen


def vorschlag_embed(gefunden: dict[int, int], rollennamen: dict[int, str],
                    bisher: dict[int, int]) -> discord.Embed:
    fehlen = len(LEVEL_STUFEN) - len(gefunden)
    beschreibung = [
        "Ich habe die Rollen dieses Servers mit den Titeln aus der Liste verglichen.",
        "",
        *vorschlag_zeilen(gefunden, rollennamen),
    ]
    if fehlen:
        beschreibung += [
            "",
            f"**{fehlen} Rolle(n) fehlen.** Entweder heißen sie anders, oder es gibt sie noch nicht. "
            "Du kannst sie einzeln mit `/level-rolle` zuordnen — der Rest funktioniert trotzdem.",
        ]
    if bisher:
        beschreibung += ["", f"Bisher gespeichert: {len(bisher)} Zuordnung(en). "
                             "„Übernehmen“ überschreibt die gefundenen."]
    embed = discord.Embed(title="🎖️ Level-Rollen zuordnen", description="\n".join(beschreibung), color=FARBE)
    embed.set_footer(text="Es werden keine Rollen vergeben oder entfernt — der Bot liest sie nur.")
    return embed


class VorschlagView(RestrictedView):
    """Übernehmen oder Abbrechen für /level-einrichten."""

    def __init__(self, user_id: int, guild_id: int, gefunden: dict[int, int], *, interaction_checker=None):
        super().__init__(timeout=300, interaction_checker=interaction_checker)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.gefunden = dict(gefunden)

        uebernehmen = ui.Button(label="Übernehmen", style=discord.ButtonStyle.success,
                                disabled=not self.gefunden)
        uebernehmen.callback = self._uebernehmen
        self.add_item(uebernehmen)
        abbrechen = ui.Button(label="Abbrechen", style=discord.ButtonStyle.secondary)
        abbrechen.callback = self._abbrechen
        self.add_item(abbrechen)

    async def _nur_besteller(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
        return False

    async def _uebernehmen(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besteller(interaction):
            return
        anzahl = await lr.setze_zuordnung(self.guild_id, self.gefunden)
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ {anzahl} Zuordnung(en) gespeichert. Weiter mit `/level-kanal`, "
                    f"danach `/level-vorschau`.",
            embed=None, view=None)

    async def _abbrechen(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besteller(interaction):
            return
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen. Es wurde nichts gespeichert.",
                                                embed=None, view=None)


async def einrichten(interaction: discord.Interaction, *, interaction_checker=None) -> None:
    """Rollen des Servers mit den Titeln vergleichen und Vorschlag zeigen."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    rollen = [(r.id, r.name) for r in guild.roles]
    gefunden = lr.passende_rollen(rollen)
    bisher = await lr.zuordnung_von(guild.id)
    namen = {r.id: r.name for r in guild.roles}
    view = VorschlagView(interaction.user.id, guild.id, gefunden, interaction_checker=interaction_checker)
    await interaction.response.send_message(
        embed=vorschlag_embed(gefunden, namen, bisher), view=view, ephemeral=True)


async def rolle_setzen(interaction: discord.Interaction, stufe: int, rolle: discord.Role | None) -> None:
    """Eine einzelne Zuordnung von Hand setzen oder entfernen."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if int(stufe) not in LEVEL_STUFEN:
        await interaction.response.send_message(
            f"❌ Level {stufe} gibt es in der Liste nicht.", ephemeral=True)
        return
    await lr.setze_rolle(guild.id, int(stufe), rolle.id if rolle else None)
    if rolle:
        text = (f"✅ Level {stufe} („{LEVEL_STUFEN[int(stufe)]}“) ist jetzt die Rolle {rolle.mention}.")
    else:
        text = f"🗑️ Die Zuordnung für Level {stufe} wurde entfernt."
    await interaction.response.send_message(text, ephemeral=True)


def kanal_problem(kanal: Any, bot_mitglied: Any) -> str:
    """Kann der Bot dort schreiben? Leerer Text heißt: alles in Ordnung."""
    try:
        rechte = kanal.permissions_for(bot_mitglied)
    except Exception:                                              # noqa: BLE001
        logging.exception("Rechte im Kanal nicht lesbar")
        return "Die Rechte in diesem Kanal konnten nicht geprüft werden."
    if not getattr(rechte, "send_messages", False):
        return "Der Bot darf in diesem Kanal keine Nachrichten senden."
    if not getattr(rechte, "embed_links", False):
        return "Der Bot darf in diesem Kanal keine Links einbetten (Rechte „Links einbetten“)."
    return ""


async def kanal_setzen(interaction: discord.Interaction, kanal: Any) -> None:
    """Meldungskanal speichern und dort eine Testnachricht senden."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    problem = kanal_problem(kanal, guild.me)
    if problem:
        await interaction.response.send_message(
            f"❌ {problem}\nBitte die Rechte anpassen oder einen anderen Kanal wählen.", ephemeral=True)
        return
    await lr.setze_meldungs_kanal(guild.id, kanal.id)
    hinweis = ""
    try:
        await kanal.send(embed=discord.Embed(
            title="🎖️ Level-Meldungen",
            description="Level-Meldungen erscheinen ab jetzt hier.",
            color=FARBE))
    except Exception:                                              # noqa: BLE001
        logging.exception("Testnachricht im Level-Kanal fehlgeschlagen")
        hinweis = "\n⚠️ Die Testnachricht ging nicht durch. Bitte die Rechte des Bots dort prüfen."
    await interaction.response.send_message(
        f"✅ Meldungen erscheinen jetzt in {kanal.mention}.{hinweis}", ephemeral=True)
