"""Einrichtung des Level-Systems: /level-einrichten, /level-rolle, /level-kanal.

Alles lässt sich per Klick in Discord einstellen — es muss nie eine ID
kopiert oder in den Code geschrieben werden.

Die Befehle liegen in admin_commands.py, hier steht, was sie tun. So lässt
es sich ohne laufenden Bot testen.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import discord
from discord import ui

from botcore.ui_common import RestrictedView
from level_reward_config import LEVEL_STUFEN
from services import level_rewards as lr
from services import role_manager

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


def _anzeigename(guild: Any, user_id: int) -> str:
    mitglied = guild.get_member(int(user_id)) if guild is not None else None
    if mitglied is not None:
        return str(getattr(mitglied, "display_name", None) or user_id)
    return f"ID {user_id}"


async def vorschau_sammeln(guild: Any) -> dict[str, Any]:
    """Wer bekäme was? Ändert **nichts** — nur lesen und rechnen.

    Enthält Level-Belohnungen (aus den Rollen) und Einladungs-Belohnungen
    (aus der Einladungs-Statistik).
    """
    zuordnung = await lr.zuordnung_von(guild.id)
    rollen_ids = set(zuordnung.values())
    eintraege: list[dict[str, Any]] = []

    if zuordnung:
        for mitglied in getattr(guild, "members", []):
            if getattr(mitglied, "bot", False):
                continue
            eigene = {r.id for r in mitglied.roles}
            if not eigene & rollen_ids:
                continue
            stufe = lr.stufe_aus_rollen(eigene, zuordnung)
            if not stufe:
                continue
            offen = lr.faellige_belohnungen(stufe, await lr.erledigte_schluessel(mitglied.id))
            if offen:
                eintraege.append({"user_id": int(mitglied.id),
                                  "name": str(getattr(mitglied, "display_name", mitglied.id)),
                                  "quelle": lr.QUELLE_LEVEL, "stufe": stufe, "offen": offen})

    for user_id, anzahl in await lr.alle_einlader():
        offen = lr.einladung_faellige(anzahl, await lr.erledigte_schluessel(user_id))
        if offen:
            eintraege.append({"user_id": int(user_id), "name": _anzeigename(guild, user_id),
                              "quelle": lr.QUELLE_EINLADUNG, "stufe": anzahl, "offen": offen})

    designs_anzahl = sum(1 for e in eintraege for f in e["offen"] if f.art == "design")
    staub = sum(f.belohnung.menge for e in eintraege for f in e["offen"] if f.art == "staub")
    return {
        "eintraege": eintraege,
        "spieler_level": sum(1 for e in eintraege if e["quelle"] == lr.QUELLE_LEVEL),
        "einlader": sum(1 for e in eintraege if e["quelle"] == lr.QUELLE_EINLADUNG),
        "designs": designs_anzahl,
        "staub": staub,
        "zuordnung": zuordnung,
    }


def vorschau_text(vorschau: dict[str, Any]) -> str:
    """Die vollständige Liste als Text — für den Anhang (Discord kürzt Nachrichten)."""
    zeilen = ["Vorschau: Wer bekäme was?", "=" * 40, ""]
    for eintrag in vorschau["eintraege"]:
        woher = ("Level " + str(eintrag["stufe"]) if eintrag["quelle"] == lr.QUELLE_LEVEL
                 else f"{eintrag['stufe']} Einladungen")
        zeilen.append(f"{eintrag['name']} ({eintrag['user_id']}) — {woher}")
        for faellig in eintrag["offen"]:
            zeilen.append(f"    • {faellig.text()}")
        zeilen.append("")
    if not vorschau["eintraege"]:
        zeilen.append("Niemand hat etwas offen.")
    return "\n".join(zeilen)


def vorschau_embed(vorschau: dict[str, Any], *, aktiv: bool) -> discord.Embed:
    zeilen = [
        f"**{vorschau['spieler_level']}** Spieler mit Level-Belohnungen, "
        f"**{vorschau['einlader']}** Einlader.",
        f"Zusammen **{vorschau['designs']}** Designs und **{vorschau['staub']}** Infinitydust.",
        "",
        "Diese Vorschau **ändert nichts**. Die vollständige Liste hängt als Datei an.",
    ]
    if not vorschau["zuordnung"]:
        zeilen.append("\n⚠️ Für diesen Server sind noch keine Level-Rollen zugeordnet — "
                      "bitte zuerst `/level-einrichten`.")
    zeilen.append("\nLevel-System ist derzeit **" + ("an" if aktiv else "aus") + "**.")
    return discord.Embed(title="🔎 Vorschau der Belohnungen", description="\n".join(zeilen), color=FARBE)


class VorschauView(RestrictedView):
    """Jetzt vergeben (mit zweiter Bestätigung) oder Abbrechen."""

    def __init__(self, user_id: int, guild: Any, vorschau: dict[str, Any], *, interaction_checker=None):
        super().__init__(timeout=600, interaction_checker=interaction_checker)
        self.user_id = int(user_id)
        self.guild = guild
        self.vorschau = vorschau
        self.bestaetigt = False

        self.vergeben_knopf = ui.Button(
            label="Jetzt vergeben und einschalten",
            style=discord.ButtonStyle.success,
            disabled=not vorschau["eintraege"] and not vorschau["zuordnung"],
        )
        self.vergeben_knopf.callback = self._vergeben
        self.add_item(self.vergeben_knopf)
        abbrechen = ui.Button(label="Abbrechen", style=discord.ButtonStyle.secondary)
        abbrechen.callback = self._abbrechen
        self.add_item(abbrechen)

    async def _nur_besteller(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
        return False

    async def _abbrechen(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besteller(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            content="Abgebrochen. Es wurde nichts vergeben, das Level-System bleibt aus.",
            embed=None, view=None, attachments=[])

    async def _vergeben(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besteller(interaction):
            return
        if not self.bestaetigt:
            # Zweite Bestätigung: Das hier lässt sich nicht zurückdrehen.
            self.bestaetigt = True
            self.vergeben_knopf.label = "Wirklich vergeben?"
            self.vergeben_knopf.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(
                content=f"⚠️ **{self.vorschau['designs']} Designs** und "
                        f"**{self.vorschau['staub']} Infinitydust** werden jetzt vergeben, "
                        f"danach ist das Level-System **an**. Noch einmal klicken zum Bestätigen.",
                view=self)
            return
        self.stop()
        await interaction.response.edit_message(
            content="⏳ Wird vergeben … das kann einen Moment dauern.",
            embed=None, view=None, attachments=[])
        ergebnis = await vergeben_ausfuehren(self.guild, self.vorschau)
        await interaction.followup.send(ergebnis, ephemeral=True)


async def vergeben_ausfuehren(guild: Any, vorschau: dict[str, Any], *, pause: float | None = None) -> str:
    """Alles nacheinander vergeben, dann das Level-System einschalten.

    Nacheinander und mit kurzer Pause, weil die Datenbank nur eine
    Schreibverbindung hat. Bricht der Lauf ab, ist er wiederholbar — das
    Protokoll verhindert doppelte Vergaben.
    """
    wartezeit = role_manager.PAUSE_BETWEEN_CALLS if pause is None else pause
    designs_anzahl = staub = fehler = 0
    spieler = set()
    for eintrag in vorschau["eintraege"]:
        ergebnis = await lr.vergebe(eintrag["user_id"], eintrag["offen"], eintrag["quelle"])
        designs_anzahl += ergebnis.designs
        staub += ergebnis.staub
        fehler += len(ergebnis.fehler)
        if ergebnis.vergeben:
            spieler.add(eintrag["user_id"])
        if wartezeit:
            await asyncio.sleep(wartezeit)
    await lr.setze_aktiv(guild.id, True)

    # Eine einzige Zusammenfassung im Meldungskanal statt vieler Einzelmeldungen.
    kanal_id = await lr.meldungs_kanal(guild.id)
    kanal = guild.get_channel(int(kanal_id)) if kanal_id else None
    if kanal is not None and spieler:
        try:
            await kanal.send(embed=discord.Embed(
                title="🎖️ Level- und Einladungs-Belohnungen sind da",
                description=(f"**{len(spieler)}** Mitglieder haben **{designs_anzahl}** Designs "
                             f"und **{staub}** Infinitydust erhalten.\n"
                             "Schau mit `/design` nach, welches Aussehen deine Karten jetzt haben können."),
                color=FARBE))
        except Exception:                                          # noqa: BLE001
            logging.exception("Zusammenfassung im Level-Kanal fehlgeschlagen")
    text = (f"✅ Fertig: **{designs_anzahl}** Designs und **{staub}** Infinitydust an "
            f"**{len(spieler)}** Mitglieder. Das Level-System ist jetzt **an**.")
    if fehler:
        text += f"\n⚠️ {fehler} Belohnung(en) konnten nicht vergeben werden (siehe Log)."
    return text


async def vorschau(interaction: discord.Interaction, *, interaction_checker=None) -> None:
    """/level-vorschau: rechnen, zeigen, nichts ändern."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    daten = await vorschau_sammeln(guild)
    aktiv = await lr.ist_aktiv(guild.id)
    datei = discord.File(io.BytesIO(vorschau_text(daten).encode("utf-8")), filename="level-vorschau.txt")
    view = VorschauView(interaction.user.id, guild, daten, interaction_checker=interaction_checker)
    await interaction.followup.send(embed=vorschau_embed(daten, aktiv=aktiv), file=datei,
                                    view=view, ephemeral=True)


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
