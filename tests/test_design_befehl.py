"""/design: Befehl angemeldet, Ansicht baut sich, Wählen wirkt."""
from __future__ import annotations

import asyncio

import discord
import pytest

import bot
from botcommands.design_view import DesignView, lade_design_karten
from karten import karten as RAW
from services import db as db_modul
from services import designs
from services.card_variants import group_owned_cards_by_base
from tests.view_harness import make_interaction

SPIELER = 999_000_401
ZWEI = "https://i.imgur.com/zwei.png"
DREI = "https://i.imgur.com/drei.png"
OHNE_VARIANTEN = [k["name"] for k in RAW if not k.get("variants")]
MIT_DESIGN, OHNE_DESIGN = OHNE_VARIANTEN[0], OHNE_VARIANTEN[1]


def _roh(name: str) -> dict:
    return next(k for k in RAW if k.get("name") == name)


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


@pytest.fixture
def mit_links(monkeypatch):
    monkeypatch.setitem(_roh(MIT_DESIGN), "bild_2", ZWEI)
    monkeypatch.setitem(_roh(MIT_DESIGN), "bild_3", DREI)


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


def _klick(view: DesignView, typ, label: str | None = None):
    for item in view.children:
        if isinstance(item, typ) and (label is None or getattr(item, "label", None) == label):
            return item
    raise AssertionError(f"{typ.__name__} {label!r} fehlt")


def _interaktion(werte=None):
    it = make_interaction(SPIELER)
    it.data = {"values": list(werte or [])}
    return it


async def _ansicht() -> DesignView:
    gruppen = group_owned_cards_by_base([(MIT_DESIGN, 1), (OHNE_DESIGN, 2)])
    liste = await lade_design_karten(gruppen, bot.get_karte_by_name)
    view = DesignView(SPIELER, liste)
    await view.aufbauen()
    return view


def test_befehl_ist_angemeldet():
    namen = {c.name for c in bot.bot.tree.get_commands()}
    assert "design" in namen


def test_ohne_links_keine_karten(testdb):
    async def ablauf():
        gruppen = group_owned_cards_by_base([(MIT_DESIGN, 1)])
        return await lade_design_karten(gruppen, bot.get_karte_by_name)

    assert _lauf(ablauf()) == []


def test_nur_eigene_karten_mit_designs(testdb, mit_links):
    view = _lauf(_ansicht())
    assert [k.grundname for k in view.karten] == [MIT_DESIGN]
    auswahl = _klick(view, discord.ui.Select)
    assert [o.label for o in auswahl.options] == [MIT_DESIGN]
    assert "3 Designs" in auswahl.options[0].description


def test_gesperrtes_design_ist_sichtbar_aber_nicht_waehlbar(testdb, mit_links):
    async def ablauf():
        view = await _ansicht()
        await _klick(view, discord.ui.Select).callback(_interaktion(["0"]))
        await _klick(view, discord.ui.Select).callback(_interaktion(["2"]))
        return view

    view = _lauf(ablauf())
    optionen = _klick(view, discord.ui.Select).options
    assert [o.label for o in optionen] == ["Design 1 (Standard)", "Design 2", "Design 3"]
    assert str(optionen[1].emoji) == "🔒" and optionen[1].description == "noch gesperrt"
    assert _klick(view, discord.ui.Button, "Übernehmen").disabled
    embed = view._embed_karte()
    assert embed.title == f"Design 2 von 3 · {MIT_DESIGN}"
    assert embed.image.url == ZWEI
    assert "Noch gesperrt" in embed.description


def test_freigeschaltetes_design_uebernehmen(testdb, mit_links):
    async def ablauf():
        await designs.freischalten(SPIELER, MIT_DESIGN, 3)
        view = await _ansicht()
        await _klick(view, discord.ui.Select).callback(_interaktion(["0"]))
        await _klick(view, discord.ui.Select).callback(_interaktion(["3"]))
        knopf = _klick(view, discord.ui.Button, "Übernehmen")
        assert not knopf.disabled
        await knopf.callback(_interaktion())
        return view, await designs.bild_fuer(SPIELER, _roh(MIT_DESIGN))

    view, bild = _lauf(ablauf())
    assert bild == DREI
    assert "Übernommen" in view.hinweis
    assert _klick(view, discord.ui.Button, "Übernehmen").disabled, "aktives Design nicht nochmal"


def test_alle_zuruecksetzen(testdb, mit_links):
    async def ablauf():
        await designs.freischalten(SPIELER, MIT_DESIGN, 2)
        await designs.waehle(SPIELER, MIT_DESIGN, 2)
        view = await _ansicht()
        await _klick(view, discord.ui.Button, "Alle zurücksetzen").callback(_interaktion())
        return view, await designs.aktive_designs_von(SPIELER)

    view, aktive = _lauf(ablauf())
    assert aktive == {}
    assert "Design 1" in view.hinweis


def test_fremde_klicks_werden_abgewiesen(testdb, mit_links):
    async def ablauf():
        view = await _ansicht()
        fremd = make_interaction(SPIELER + 1)
        fremd.data = {"values": ["0"]}
        await _klick(view, discord.ui.Select).callback(fremd)
        return view, fremd

    view, fremd = _lauf(ablauf())
    assert view.auswahl is None
    fremd.response.send_message.assert_awaited()
