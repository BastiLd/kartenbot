"""/design-geben und /design-entziehen: versteckt, geprüft, harmlos doppelt."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
from botcommands import design_admin
from karten import karten as RAW
from services import db as db_modul
from services import designs
from tests.view_harness import make_interaction

ADMIN = 999_000_501
SPIELER = 999_000_502
ZWEI = "https://i.imgur.com/zwei.png"
NORMAL = next(k["name"] for k in RAW if not k.get("variants"))


def _roh(name: str) -> dict:
    return next(k for k in RAW if k.get("name") == name)


def _befehl(name: str):
    return next(c for c in bot.bot.tree.get_commands() if c.name == name)


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


def _modul(admin: bool = True, besitz=((NORMAL, 1),)):
    return SimpleNamespace(is_admin=AsyncMock(return_value=admin),
                           get_user_karten=AsyncMock(return_value=list(besitz)))


def _mitglied():
    return SimpleNamespace(id=SPIELER, mention=f"<@{SPIELER}>")


async def _rufen(modul, karte=NORMAL, design=2, *, entziehen=False):
    it = make_interaction(ADMIN)
    await design_admin.ausfuehren(it, modul, _mitglied(), karte, design, entziehen=entziehen)
    kwargs = it.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    return kwargs


# --------------------------------------------------------------------------
# Anmeldung
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["design-geben", "design-entziehen"])
def test_befehle_sind_angemeldet_und_versteckt(name):
    befehl = _befehl(name)
    assert befehl.default_permissions is not None
    assert befehl.default_permissions.administrator is True
    assert befehl.guild_only is True
    assert befehl.get_parameter("karte").autocomplete is not None
    assert [c.value for c in befehl.get_parameter("design").choices] == [2, 3]


def test_vorschlaege_zeigen_alle_karten():
    alle = design_admin.karten_vorschlaege("")
    assert len(alle) == min(25, len(RAW))
    assert all(not _roh(c.value).get("bild_2") for c in alle), "auch Karten ohne Bild"
    assert [c.value for c in design_admin.karten_vorschlaege("iron")] == ["Iron-Man"]


def test_finde_karte():
    assert designs.finde_karte(NORMAL.upper())["name"] == NORMAL
    assert designs.finde_karte("Alpha_Iron-Man")["name"] == "Iron-Man"
    assert designs.finde_karte("Gibt es nicht") is None


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------
def test_nicht_admin_wird_abgewiesen(testdb):
    async def ablauf():
        antwort = await _rufen(_modul(admin=False))
        return antwort, await designs.freigeschaltet(SPIELER, NORMAL)

    antwort, frei = _lauf(ablauf())
    assert "Keine Berechtigung" in antwort["content"]
    assert frei == {1}


def test_nicht_admin_kann_auch_nicht_entziehen(testdb):
    async def ablauf():
        await designs.freischalten(SPIELER, NORMAL, 2)
        await _rufen(_modul(admin=False), entziehen=True)
        return await designs.freigeschaltet(SPIELER, NORMAL)

    assert _lauf(ablauf()) == {1, 2}


def test_vergeben_ohne_link_mit_hinweis_und_doppelt_harmlos(testdb):
    async def ablauf():
        return await _rufen(_modul()), await _rufen(_modul()), await designs.freigeschaltet(SPIELER, NORMAL)

    erste, zweite, frei = _lauf(ablauf())
    assert "neu freigeschaltet" in erste["content"]
    assert design_admin.KEIN_LINK_HINWEIS in erste["content"]
    assert "embed" not in erste
    assert "schon" in zweite["content"]
    assert frei == {1, 2}


def test_vergeben_mit_link_zeigt_das_design(testdb, monkeypatch):
    monkeypatch.setitem(_roh(NORMAL), "bild_2", ZWEI)
    antwort = _lauf(_rufen(_modul()))
    assert design_admin.KEIN_LINK_HINWEIS not in antwort["content"]
    assert antwort["embed"].image.url == ZWEI


def test_hinweis_wenn_karte_noch_fehlt(testdb):
    antwort = _lauf(_rufen(_modul(besitz=())))
    assert "neu freigeschaltet" in antwort["content"]
    assert "fehlt noch" in antwort["content"]


def test_unbekannte_karte(testdb):
    antwort = _lauf(_rufen(_modul(), karte="Gibt es nicht"))
    assert "gibt es nicht" in antwort["content"]


def test_entziehen(testdb):
    async def ablauf():
        await designs.freischalten(SPIELER, NORMAL, 2)
        erste = await _rufen(_modul(), entziehen=True)
        zweite = await _rufen(_modul(), entziehen=True)
        return erste, zweite, await designs.freigeschaltet(SPIELER, NORMAL)

    erste, zweite, frei = _lauf(ablauf())
    assert "entzogen" in erste["content"]
    assert "gar nicht" in zweite["content"]
    assert frei == {1}
