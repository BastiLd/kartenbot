"""Die zwei Spieler-Befehle /level und /einladungen.

Beide antworten nur dem Spieler selbst und zeigen nur an — sie vergeben
nichts. Die Texte sind bewusst ehrlich: Der Bot kennt nur die Level-Rollen,
nicht das genaue MEE6-Level dazwischen.
"""
from __future__ import annotations

import discord

from level_reward_config import EINLADUNG_STUFEN, LEVEL_BELOHNUNGEN
from services import designs
from services import level_rewards as lr

FARBE = 0xF1C40F

AUS_HINWEIS = ("Das Level-System ist auf diesem Server noch nicht eingeschaltet. "
               "Ein Admin richtet es mit `/level-einrichten` ein.")


def _belohnungstext(stufe: int) -> str:
    eintraege = LEVEL_BELOHNUNGEN.get(int(stufe)) or []
    if not eintraege:
        return ""
    return ", ".join(f"Design {b.nummer} von {b.karte}" for b in eintraege)


def level_zeilen(stufe: int, freigeschaltet: int) -> list[str]:
    """Der Text von /level — reine Textarbeit, ohne Datenbank."""
    zeilen: list[str] = []
    if stufe:
        zeilen.append(f"Du bist **{lr.titel(stufe)}** — mindestens **Level {stufe}**.")
        zeilen.append("_Das genaue Level kennt nur MEE6; der Bot sieht nur deine Rollen._")
        eigener_hinweis = lr.hinweis(stufe)
        if eigener_hinweis:
            zeilen.append(f"Diese Stufe bringt außerdem: {eigener_hinweis}.")
    else:
        zeilen.append("Du hast noch keine Level-Rolle. Schreib im Server mit, dann kommt die erste bald.")

    naechste = lr.naechste_stufe(stufe)
    if naechste:
        belohnung = _belohnungstext(naechste)
        weiteres = lr.hinweis(naechste)
        was = " und ".join(t for t in (belohnung, weiteres) if t) or "keine Bot-Belohnung"
        zeilen.append(f"\n**Als Nächstes: Level {naechste} — {lr.titel(naechste)}**\nDafür gibt es: {was}.")
        mit_belohnung = lr.naechste_stufe_mit_belohnung(stufe)
        if mit_belohnung and mit_belohnung != naechste:
            zeilen.append(f"Das nächste Design gibt es bei **Level {mit_belohnung}**: "
                          f"{_belohnungstext(mit_belohnung)}.")
    else:
        zeilen.append("\nDu hast die höchste Stufe erreicht. 🎉")

    zeilen.append(f"\nFreigeschaltete Designs: **{freigeschaltet}** — ansehen mit `/design`.")
    return zeilen


def einladungs_zeilen(anzahl: int) -> list[str]:
    """Der Text von /einladungen."""
    zeilen = [f"Du hast **{anzahl}** Person(en) eingeladen." if anzahl
              else "Du hast noch niemanden eingeladen. Mit `/eingeladen` wird eine Einladung bestätigt."]
    naechste = lr.naechste_einladungsstufe(anzahl)
    if naechste:
        fehlt = naechste - int(anzahl or 0)
        was = ", ".join(f.text() for f in lr.einladung_belohnungen(naechste))
        zeilen.append(f"Noch **{fehlt}** bis: {was}.")
    zeilen.append("")
    for stufe in sorted(EINLADUNG_STUFEN):
        haken = "✅" if int(anzahl or 0) >= stufe else "⬜"
        was = ", ".join(f.text() for f in lr.einladung_belohnungen(stufe))
        zeilen.append(f"{haken} **{stufe}**: {was}")
    zeilen.append("⬜ Jede weitere Einladung: 5 Infinitydust")
    zeilen.append(f"\nDer Eingeladene bekommt jedes Mal **{lr.eingeladener_staub()} Infinitydust**.")
    return zeilen


async def level_anzeigen(interaction: discord.Interaction) -> discord.Embed:
    """Baut das Embed für /level."""
    guild = interaction.guild
    zuordnung = await lr.zuordnung_von(guild.id) if guild else {}
    if not guild or not zuordnung or not await lr.ist_aktiv(guild.id):
        return discord.Embed(title="🎖️ Dein Level", description=AUS_HINWEIS, color=FARBE)
    rollen = [r.id for r in getattr(interaction.user, "roles", [])]
    stufe = lr.stufe_aus_rollen(rollen, zuordnung)
    freigeschaltet = await designs.anzahl_freigeschaltet(interaction.user.id)
    return discord.Embed(title="🎖️ Dein Level",
                         description="\n".join(level_zeilen(stufe, freigeschaltet)), color=FARBE)


async def einladungen_anzeigen(interaction: discord.Interaction, anzahl: int) -> discord.Embed:
    """Baut das Embed für /einladungen."""
    return discord.Embed(title="🤝 Deine Einladungen",
                         description="\n".join(einladungs_zeilen(anzahl)), color=FARBE)
