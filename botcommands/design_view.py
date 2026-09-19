"""Die Ansicht hinter /design: Designs der eigenen Karten ansehen und wählen.

Bewusst eine eigene Datei statt noch mehr bot.py: Die Ansicht braucht nur die
Design-Logik (services/designs.py) und ein paar Werkzeuge, die der Befehl
mitgibt.

Ein Design ändert nur das Aussehen. Deshalb zeigt die Ansicht auch nur Name,
Nummer und Bild — keine Werte, denn an denen ändert sich nichts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import discord
from discord import ui

from botcore.ui_common import RestrictedView
from services import designs

# Discord erlaubt höchstens 25 Einträge in einer Auswahl.
SEITENGROESSE = 25
FARBE = 0x9B59B6

HINWEIS_NUR_AUSSEHEN = "Ein Design ändert nur das Aussehen — Werte und Angriffe bleiben gleich."


@dataclass
class DesignKarte:
    grundname: str
    karte: dict                      # Laufzeitkarte; ihr Bild ist Design 1
    verfuegbar: list[int] = field(default_factory=list)


async def lade_design_karten(
    gruppen: list[dict[str, Any]],
    get_karte_by_name: Callable[[str], Awaitable[dict | None]],
) -> list[DesignKarte]:
    """Karten des Spielers, die mindestens zwei Designs haben (Link eingetragen).

    ``gruppen`` sind die nach Grundkarte zusammengefassten eigenen Karten, wie
    sie auch /sammlung benutzt.
    """
    out: list[DesignKarte] = []
    for gruppe in gruppen:
        grundname = str(gruppe.get("base_name") or "").strip()
        varianten = list(gruppe.get("variants") or [])
        name = str(varianten[0][0]) if varianten else grundname
        if not name:
            continue
        karte = await get_karte_by_name(name)
        if not isinstance(karte, dict):
            continue
        verfuegbar = designs.verfuegbare_designs(karte)
        if len(verfuegbar) >= 2:
            out.append(DesignKarte(designs.grundname(karte) or grundname, karte, verfuegbar))
    return out


class DesignView(RestrictedView):
    """Übersicht aller Karten mit Designs, und je Karte die Designs zur Wahl."""

    def __init__(self, user_id: int, karten: list[DesignKarte], *, interaction_checker=None):
        super().__init__(timeout=300, interaction_checker=interaction_checker)
        self.user_id = int(user_id)
        self.karten = karten
        self.seite = 0
        self.auswahl: DesignKarte | None = None
        self.design = 1
        self.frei: set[int] = {1}
        self.aktive: dict[str, int] = {}
        self.hinweis = ""

    # ------------------------------------------------------------------ Daten
    def _aktiv(self, eintrag: DesignKarte) -> int:
        design = self.aktive.get(eintrag.grundname, 1)
        return design if design in eintrag.verfuegbar else 1

    async def _neu_laden(self) -> None:
        self.aktive = await designs.aktive_designs_von(self.user_id)
        if self.auswahl is not None:
            self.frei = await designs.freigeschaltet(self.user_id, self.auswahl.karte)

    @property
    def _seiten(self) -> int:
        return max(1, (len(self.karten) + SEITENGROESSE - 1) // SEITENGROESSE)

    # ------------------------------------------------------------ Darstellung
    async def aufbauen(self) -> discord.Embed:
        """Daten frisch holen, Knöpfe neu setzen und das passende Embed liefern."""
        await self._neu_laden()
        self.clear_items()
        if self.auswahl is None:
            self._knoepfe_uebersicht()
            return self._embed_uebersicht()
        self._knoepfe_karte()
        return self._embed_karte()

    def _embed_uebersicht(self) -> discord.Embed:
        zeilen = ["Wähle eine Karte, um ihre Designs anzusehen.", HINWEIS_NUR_AUSSEHEN]
        if self.hinweis:
            zeilen.insert(0, self.hinweis)
        embed = discord.Embed(title="🎨 Deine Karten-Designs", description="\n\n".join(zeilen), color=FARBE)
        if self._seiten > 1:
            embed.set_footer(text=f"Seite {self.seite + 1} von {self._seiten}")
        return embed

    def _embed_karte(self) -> discord.Embed:
        eintrag = self.auswahl
        assert eintrag is not None
        gesamt = len(eintrag.verfuegbar)
        aktiv = self._aktiv(eintrag)
        if self.design == aktiv:
            status = "✅ Dieses Design ist gerade aktiv."
        elif self.design not in self.frei:
            status = "🔒 Noch gesperrt."
        else:
            status = "Freigeschaltet — mit „Übernehmen“ zeigen Sammlung und Kampf dieses Design."
        zeilen = [status, HINWEIS_NUR_AUSSEHEN]
        if self.hinweis:
            zeilen.insert(0, self.hinweis)
        embed = discord.Embed(
            title=f"Design {self.design} von {gesamt} · {eintrag.grundname}",
            description="\n\n".join(zeilen),
            color=FARBE,
        )
        bild = designs.bild_link(eintrag.karte, self.design)
        if bild:
            embed.set_image(url=bild)
        return embed

    def _knoepfe_uebersicht(self) -> None:
        start = self.seite * SEITENGROESSE
        sichtbar = self.karten[start:start + SEITENGROESSE]
        auswahl = ui.Select(
            placeholder="Karte wählen …",
            options=[
                discord.SelectOption(
                    label=eintrag.grundname[:100],
                    value=str(start + index),
                    description=f"{len(eintrag.verfuegbar)} Designs · aktiv: Design {self._aktiv(eintrag)}",
                )
                for index, eintrag in enumerate(sichtbar)
            ],
            row=0,
        )
        auswahl.callback = self._karte_gewaehlt
        self.add_item(auswahl)

        if self._seiten > 1:
            zurueck = ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                disabled=self.seite == 0, row=1)
            zurueck.callback = self._seite_zurueck
            self.add_item(zurueck)
            vor = ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                            disabled=self.seite >= self._seiten - 1, row=1)
            vor.callback = self._seite_vor
            self.add_item(vor)

        alle = ui.Button(label="Alle zurücksetzen", style=discord.ButtonStyle.danger, row=2)
        alle.callback = self._alle_zuruecksetzen
        self.add_item(alle)

    def _knoepfe_karte(self) -> None:
        eintrag = self.auswahl
        assert eintrag is not None
        aktiv = self._aktiv(eintrag)
        optionen = []
        for nummer in eintrag.verfuegbar:
            if nummer == aktiv:
                beschreibung, emoji = "gerade aktiv", "✅"
            elif nummer not in self.frei:
                beschreibung, emoji = "noch gesperrt", "🔒"
            else:
                beschreibung, emoji = "freigeschaltet", None
            optionen.append(discord.SelectOption(
                label=f"Design {nummer}" + (" (Standard)" if nummer == 1 else ""),
                value=str(nummer),
                description=beschreibung,
                emoji=emoji,
                default=nummer == self.design,
            ))
        auswahl = ui.Select(placeholder="Design wählen …", options=optionen, row=0)
        auswahl.callback = self._design_gewaehlt
        self.add_item(auswahl)

        uebernehmen = ui.Button(
            label="Übernehmen",
            style=discord.ButtonStyle.success,
            disabled=self.design == aktiv or self.design not in self.frei,
            row=1,
        )
        uebernehmen.callback = self._uebernehmen
        self.add_item(uebernehmen)
        zurueck = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, row=1)
        zurueck.callback = self._zur_uebersicht
        self.add_item(zurueck)

    # --------------------------------------------------------------- Aktionen
    async def _nur_besitzer(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Das ist nicht dein Menü!", ephemeral=True)
        return False

    async def _zeigen(self, interaction: discord.Interaction) -> None:
        try:
            embed = await self.aufbauen()
        except Exception:
            logging.exception("Design-Ansicht für %s nicht aufbaubar", self.user_id)
            await interaction.response.send_message(
                "❌ Die Designs konnten gerade nicht geladen werden. Bitte später erneut versuchen.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(embed=embed, view=self)

    def _gewaehlter_wert(self, interaction: discord.Interaction) -> str:
        werte = (interaction.data or {}).get("values") or []
        return str(werte[0]) if werte else ""

    async def _karte_gewaehlt(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        try:
            index = int(self._gewaehlter_wert(interaction))
            self.auswahl = self.karten[index]
        except (ValueError, IndexError):
            self.auswahl = None
        self.hinweis = ""
        if self.auswahl is not None:
            self.design = self._aktiv(self.auswahl)
        await self._zeigen(interaction)

    async def _design_gewaehlt(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        try:
            nummer = int(self._gewaehlter_wert(interaction))
        except ValueError:
            nummer = 1
        if self.auswahl is not None and nummer in self.auswahl.verfuegbar:
            self.design = nummer
        self.hinweis = ""
        await self._zeigen(interaction)

    async def _uebernehmen(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        if self.auswahl is None:
            await self._zeigen(interaction)
            return
        if await designs.waehle(self.user_id, self.auswahl.karte, self.design):
            self.hinweis = f"✅ Übernommen: Sammlung und Kampf zeigen jetzt Design {self.design}."
        else:
            self.hinweis = "❌ Das ging nicht — dieses Design ist (noch) nicht freigeschaltet."
        await self._zeigen(interaction)

    async def _zur_uebersicht(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        self.auswahl = None
        self.hinweis = ""
        await self._zeigen(interaction)

    async def _seite_zurueck(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        self.seite = max(0, self.seite - 1)
        await self._zeigen(interaction)

    async def _seite_vor(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        self.seite = min(self._seiten - 1, self.seite + 1)
        await self._zeigen(interaction)

    async def _alle_zuruecksetzen(self, interaction: discord.Interaction) -> None:
        if not await self._nur_besitzer(interaction):
            return
        anzahl = await designs.alle_zuruecksetzen(self.user_id)
        if anzahl:
            self.hinweis = f"↩️ Alle Karten zeigen wieder Design 1 ({anzahl} zurückgesetzt)."
        else:
            self.hinweis = "Alle Karten zeigen schon Design 1."
        await self._zeigen(interaction)
