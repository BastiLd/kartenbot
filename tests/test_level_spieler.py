"""/level und /einladungen — die zwei Anzeigen für Spieler (Plan 2, Schritt 9)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bot
from botcommands import level_player
from services import db as db_modul
from services import designs
from services import level_rewards as lr

SPIELER = 999_001_201
SERVER = 555_000_5
ROLLE_5, ROLLE_15 = 105, 115


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(lr, "_schema_fuer_pfad", None)
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


def _interaktion(rollen_ids=(), guild_id=SERVER):
    guild = SimpleNamespace(id=guild_id) if guild_id else None
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=SPIELER, roles=[SimpleNamespace(id=r) for r in rollen_ids]))


# --------------------------------------------------------------------------
# Texte (reine Logik)
# --------------------------------------------------------------------------
def test_level_text_mit_stufe():
    text = "\n".join(level_player.level_zeilen(15, 3))
    assert "Howling Commandos" in text and "mindestens **Level 15**" in text
    assert "Das genaue Level kennt nur MEE6" in text
    assert "Level 20" in text and "Design 2 von The Thing" in text
    assert "Freigeschaltete Designs: **3**" in text


def test_level_text_ohne_stufe():
    text = "\n".join(level_player.level_zeilen(0, 0))
    assert "noch keine Level-Rolle" in text
    assert "Level 1" in text and "Einwohner von New York" in text


def test_level_text_nennt_die_naechste_stufe_mit_design():
    # Level 10 bringt im Bot nichts — der Text sagt, wann es wieder etwas gibt.
    text = "\n".join(level_player.level_zeilen(5, 1))
    assert "Als Nächstes: Level 10" in text
    assert "Bilder und Links im Chat" in text
    assert "nächste Design gibt es bei **Level 15**" in text


def test_level_text_bei_hoechster_stufe():
    text = "\n".join(level_player.level_zeilen(50, 7))
    assert "höchste Stufe erreicht" in text


def test_einladungs_text():
    text = "\n".join(level_player.einladungs_zeilen(3))
    assert "**3** Person(en)" in text
    assert "Noch **2** bis: Design 2 von Spider-Man, 5 Infinitydust" in text
    assert "✅ **1**: Design 2 von Captain America" in text
    assert "⬜ **5**" in text and "⬜ **10**" in text
    assert "Jede weitere Einladung: 5 Infinitydust" in text
    assert "Der Eingeladene bekommt jedes Mal **5 Infinitydust**" in text


def test_einladungs_text_ohne_einladung():
    text = "\n".join(level_player.einladungs_zeilen(0))
    assert "noch niemanden eingeladen" in text
    assert "Noch **1** bis: Design 2 von Captain America" in text


def test_einladungs_text_ab_elf():
    text = "\n".join(level_player.einladungs_zeilen(11))
    assert all(f"✅ **{stufe}**" in text for stufe in (1, 5, 10))
    assert "Noch" not in text, "es gibt keine weitere Stufe mehr"


# --------------------------------------------------------------------------
# Mit Datenbank
# --------------------------------------------------------------------------
def test_level_zeigt_hinweis_wenn_das_system_aus_ist(testdb):
    async def ablauf():
        await db_modul.init_db()
        await lr.setze_zuordnung(SERVER, {5: ROLLE_5})
        return await level_player.level_anzeigen(_interaktion([ROLLE_5]))

    assert level_player.AUS_HINWEIS in _lauf(ablauf()).description


def test_level_zeigt_hinweis_ohne_zuordnung(testdb):
    async def ablauf():
        await db_modul.init_db()
        await lr.setze_aktiv(SERVER, True)
        return await level_player.level_anzeigen(_interaktion([ROLLE_5]))

    assert level_player.AUS_HINWEIS in _lauf(ablauf()).description


def test_level_zeigt_die_eigene_stufe(testdb):
    async def ablauf():
        await db_modul.init_db()
        await lr.setze_zuordnung(SERVER, {5: ROLLE_5, 15: ROLLE_15})
        await lr.setze_aktiv(SERVER, True)
        await designs.freischalten(SPIELER, "Black Widow", 2, "level")
        return await level_player.level_anzeigen(_interaktion([ROLLE_5, ROLLE_15]))

    beschreibung = _lauf(ablauf()).description
    assert "Howling Commandos" in beschreibung
    assert "Freigeschaltete Designs: **1**" in beschreibung


def test_einladungen_embed(testdb):
    embed = _lauf(level_player.einladungen_anzeigen(_interaktion(), 5))
    assert embed.title.endswith("Deine Einladungen")
    assert "**5** Person(en)" in embed.description


def test_befehle_sind_angemeldet_und_sichtbar():
    befehle = {c.name: c for c in bot.bot.tree.get_commands()}
    for name in ("level", "einladungen"):
        assert name in befehle
        assert befehle[name].default_permissions is None, "Spieler-Befehle bleiben sichtbar"
