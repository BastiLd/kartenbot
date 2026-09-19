"""Was hinter /design-geben und /design-entziehen passiert.

Getrennt von admin_commands.py, damit es sich ohne laufenden Discord-Bot
testen lässt: Die Befehle dort rufen nur `ausfuehren()` auf.

Die Befehle sind in Discord versteckt (nur für Administratoren sichtbar).
Das ist aber nur Komfort — geprüft wird trotzdem hier, mit `is_admin`.
"""
from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from services import designs

FARBE = 0x9B59B6

KEIN_LINK_HINWEIS = (
    "⚠️ Achtung: für dieses Design ist noch kein Bild eingetragen — es wird erst "
    "sichtbar, wenn du den Link in der Website ergänzt."
)


def design_auswahl() -> list[app_commands.Choice[int]]:
    """Die Nummern, die sich vergeben lassen (2 bis MAX_DESIGNS)."""
    return [app_commands.Choice(name=f"Design {n}", value=n) for n in range(2, designs.MAX_DESIGNS + 1)]


def karten_vorschlaege(eingabe: str) -> list[app_commands.Choice[str]]:
    """Autovervollständigung: alle Karten, nicht nur die mit eingetragenem Bild."""
    text = str(eingabe or "").strip().lower()
    treffer = [n for n in designs.alle_grundnamen() if text in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in treffer[:25]]


async def _besitzt(module: Any, user_id: int, grundname: str) -> bool:
    try:
        eigene = await module.get_user_karten(int(user_id))
    except Exception:
        logging.exception("Sammlung von %s nicht lesbar", user_id)
        return True          # im Zweifel keinen falschen Hinweis geben
    return any(designs.grundname(name) == grundname and int(anzahl or 0) > 0 for name, anzahl in eigene)


async def ausfuehren(
    interaction: discord.Interaction,
    module: Any,
    mitglied: discord.abc.User,
    karte: str,
    design: int,
    *,
    entziehen: bool = False,
) -> None:
    """Ein Design vergeben oder wegnehmen. Antwortet immer nur dem Admin (ephemeral)."""
    async def antworten(text: str, embed: discord.Embed | None = None) -> None:
        kwargs: dict[str, Any] = {"content": text, "ephemeral": True}
        if embed is not None:
            kwargs["embed"] = embed
        await interaction.response.send_message(**kwargs)

    if not await module.is_admin(interaction):
        await antworten("❌ Keine Berechtigung.")
        return
    grundkarte = designs.finde_karte(karte)
    if grundkarte is None:
        await antworten(f"❌ Die Karte „{karte}“ gibt es nicht. Bitte aus der Vorschlagsliste wählen.")
        return
    name = str(grundkarte.get("name"))
    if not designs.gueltige_nummer(design):
        await antworten(f"❌ Design {design} gibt es nicht. Möglich: 2 bis {designs.MAX_DESIGNS}.")
        return
    wer = getattr(mitglied, "mention", None) or f"`{getattr(mitglied, 'id', '?')}`"

    if entziehen:
        if await designs.entziehen(mitglied.id, name, design):
            await antworten(f"✅ Design {design} für **{name}** wurde {wer} entzogen. "
                            f"Falls es gewählt war, zeigt die Karte wieder Design 1.")
        else:
            await antworten(f"ℹ️ {wer} hatte Design {design} für **{name}** gar nicht.")
        return

    neu = await designs.freischalten(mitglied.id, name, design, "admin")
    if neu:
        zeilen = [f"✅ Design {design} für **{name}** an {wer} neu freigeschaltet."]
    else:
        zeilen = [f"ℹ️ {wer} hatte Design {design} für **{name}** schon."]

    link = designs.bild_link(grundkarte, design)
    if not link:
        zeilen.append(KEIN_LINK_HINWEIS)
    if not await _besitzt(module, mitglied.id, name):
        zeilen.append("ℹ️ Die Karte selbst fehlt noch in der Sammlung — die Freischaltung "
                      "wirkt, sobald sie da ist. Eine Karte wird dabei nicht vergeben.")

    embed = None
    if link:
        # Wer ein Design bekommt, soll das Design sehen (nicht das normale Bild).
        embed = discord.Embed(title=f"Design {design} · {name}", color=FARBE)
        embed.set_image(url=link)
    await antworten("\n".join(zeilen), embed)
