"""Designs in Sammlung und Kampf (bot.py).

Wichtigster Punkt: Ohne gewähltes Design ist jede Ausgabe genau wie vorher.
Und ein Design ändert nur das Bild — nie Werte, Namen oder die gemeinsamen
Kartenobjekte, die alle Module teilen.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

import bot
from karten import karten as RAW
from services import db as db_modul
from services import designs

SPIELER = 999_000_301
GEGNER = 999_000_302
ZWEI = "https://i.imgur.com/zwei.png"
NORMAL = next(k["name"] for k in RAW if not k.get("variants"))


def _roh(name: str) -> dict:
    return next(k for k in RAW if k.get("name") == name)


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


@pytest.fixture
def mit_link(monkeypatch):
    monkeypatch.setitem(_roh(NORMAL), "bild_2", ZWEI)


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


async def _design_2_waehlen(user_id: int = SPIELER) -> None:
    await designs.freischalten(user_id, NORMAL, 2)
    assert await designs.waehle(user_id, NORMAL, 2)


# --------------------------------------------------------------------------
# Ohne Design: alles wie vorher
# --------------------------------------------------------------------------
def test_ohne_design_bleibt_die_kampfkarte_unangetastet(testdb, mit_link):
    async def ablauf():
        karte = await bot.get_karte_by_name(NORMAL)
        vorher = copy.deepcopy(karte)
        danach = await bot._karte_mit_design(SPIELER, karte)
        return karte, vorher, danach, await bot._design_bild(SPIELER, karte)

    karte, vorher, danach, bild = _lauf(ablauf())
    assert danach is karte
    assert danach == vorher
    assert bild is None


def test_ohne_spieler_oder_karte_nichts(testdb):
    assert _lauf(bot._design_bild(None, _roh(NORMAL))) is None
    assert _lauf(bot._design_bild(SPIELER, None)) is None
    assert _lauf(bot._karte_mit_design(SPIELER, None)) is None


def test_fehler_beim_nachsehen_ergibt_standard(testdb, mit_link, monkeypatch):
    async def kaputt(*_a, **_k):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(designs, "bild_fuer", kaputt)
    karte = _lauf(bot.get_karte_by_name(NORMAL))
    assert _lauf(bot._design_bild(SPIELER, karte)) is None
    assert _lauf(bot._karte_mit_design(SPIELER, karte))["bild"] == _roh(NORMAL)["bild"]


def test_sammlung_ohne_design_zeigt_normales_bild(testdb, mit_link):
    async def ablauf():
        await db_modul.init_db()
        embed, _view = await bot._build_owned_card_detail(user_id=SPIELER, selected_name=NORMAL)
        return embed

    assert _lauf(ablauf()).image.url == _roh(NORMAL)["bild"]


# --------------------------------------------------------------------------
# Mit Design
# --------------------------------------------------------------------------
def test_kampfkarte_bekommt_nur_das_design_bild(testdb, mit_link):
    async def ablauf():
        await _design_2_waehlen()
        karte = await bot.get_karte_by_name(NORMAL)
        vorher = copy.deepcopy(karte)
        return vorher, await bot._karte_mit_design(SPIELER, karte)

    vorher, danach = _lauf(ablauf())
    assert danach["bild"] == ZWEI
    ohne_bild = {k: v for k, v in danach.items() if k != "bild"}
    assert ohne_bild == {k: v for k, v in vorher.items() if k != "bild"}, "nur das Bild ändert sich"
    assert _roh(NORMAL)["bild"] != ZWEI, "die gemeinsame Karte bleibt unberührt"


def test_gemeinsame_karte_wird_nie_veraendert(testdb, mit_link):
    async def ablauf():
        await _design_2_waehlen()
        return await bot._karte_mit_design(SPIELER, _roh(NORMAL))

    original_bild = _roh(NORMAL)["bild"]
    danach = _lauf(ablauf())
    assert danach is not _roh(NORMAL)
    assert danach["bild"] == ZWEI
    assert _roh(NORMAL)["bild"] == original_bild


def test_gegner_sieht_das_design_im_kampfbild(testdb, mit_link):
    """Das Kampfbild zeigt beide Karten — auch das Design des anderen."""
    class Nutzer:
        def __init__(self, uid):
            self.id = uid
            self.display_name = f"U{uid}"
            self.mention = f"<@{uid}>"

    async def ablauf():
        await _design_2_waehlen(SPIELER)
        eigene = await bot._karte_mit_design(SPIELER, await bot.get_karte_by_name(NORMAL))
        fremde = await bot._karte_mit_design(GEGNER, await bot.get_karte_by_name(NORMAL))
        return eigene, fremde

    eigene, fremde = _lauf(ablauf())
    a, b = Nutzer(SPIELER), Nutzer(GEGNER)
    am_zug_spieler = bot.create_battle_embed(eigene, fremde, 100, 100, SPIELER, a, b)
    am_zug_gegner = bot.create_battle_embed(eigene, fremde, 100, 100, GEGNER, a, b)
    assert am_zug_spieler.image.url == ZWEI
    assert am_zug_spieler.thumbnail.url == _roh(NORMAL)["bild"]
    assert am_zug_gegner.thumbnail.url == ZWEI


def test_sammlung_zeigt_gewaehltes_design(testdb, mit_link):
    async def ablauf():
        await db_modul.init_db()
        await _design_2_waehlen()
        eigen, _ = await bot._build_owned_card_detail(user_id=SPIELER, selected_name=NORMAL)
        fremd, _ = await bot._build_owned_card_detail(user_id=GEGNER, selected_name=NORMAL)
        return eigen, fremd

    eigen, fremd = _lauf(ablauf())
    assert eigen.image.url == ZWEI
    assert fremd.image.url == _roh(NORMAL)["bild"]


def test_link_entfernt_faellt_still_zurueck(testdb, mit_link, monkeypatch):
    async def ablauf():
        await _design_2_waehlen()
        monkeypatch.setitem(_roh(NORMAL), "bild_2", "")
        karte = await bot.get_karte_by_name(NORMAL)
        return karte, await bot._karte_mit_design(SPIELER, karte)

    karte, danach = _lauf(ablauf())
    assert danach is karte and danach["bild"] == _roh(NORMAL)["bild"]
