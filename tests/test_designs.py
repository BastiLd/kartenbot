"""Alternative Karten-Designs (services/designs.py).

Die Grundregel: Ohne Link **und** Freischaltung sieht jeder Spieler das
normale Bild — genau wie vor den Designs.
"""
from __future__ import annotations

import asyncio

import pytest

from karten import karten
from services import db as db_modul
from services import designs
from services.card_variants import build_runtime_card

SPIELER = 999_000_201
ANDERER = 999_000_202
ZWEI = "https://i.imgur.com/zwei.png"
DREI = "https://i.imgur.com/drei.png"


def _karte(name: str) -> dict:
    return next(k for k in karten if k.get("name") == name)


# Eine gewöhnliche Karte ohne Varianten, und Iron-Man mit seinen zwei Varianten.
NORMAL = next(k["name"] for k in karten if not k.get("variants"))
IRON = "Iron-Man"


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


@pytest.fixture
def mit_links(monkeypatch):
    """Design 2 und 3 an der normalen Karte und an Iron-Man eintragen."""
    for name in (NORMAL, IRON):
        monkeypatch.setitem(_karte(name), "bild_2", ZWEI)
        monkeypatch.setitem(_karte(name), "bild_3", DREI)


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


# --------------------------------------------------------------------------
# Reine Logik
# --------------------------------------------------------------------------
def test_ohne_links_gibt_es_nur_design_1():
    for karte in karten:
        if not karte.get("bild_2"):
            assert designs.verfuegbare_designs(karte) == [1]


def test_links_ergeben_designs(mit_links):
    assert designs.verfuegbare_designs(_karte(NORMAL)) == [1, 2, 3]
    assert designs.verfuegbare_designs(NORMAL) == [1, 2, 3]


def test_bild_3_ohne_bild_2_ergibt_keine_auswahl(monkeypatch):
    monkeypatch.setitem(_karte(NORMAL), "bild_3", DREI)
    assert designs.verfuegbare_designs(_karte(NORMAL)) == [1]
    monkeypatch.setitem(_karte(NORMAL), "bild_2", "   ")
    assert designs.verfuegbare_designs(_karte(NORMAL)) == [1]
    assert designs.bild_link(_karte(NORMAL), 3) == ""


def test_gueltige_nummern():
    assert designs.MAX_DESIGNS == 3
    assert [n for n in range(-1, 6) if designs.gueltige_nummer(n)] == [2, 3]
    assert not designs.gueltige_nummer(True)
    assert not designs.gueltige_nummer("2")


def test_iron_man_varianten_haben_denselben_grundnamen(mit_links):
    namen = [v["variant_id"] for v in _karte(IRON)["variants"]]
    assert len(namen) == 2
    for name in namen:
        assert designs.grundname(name) == IRON
        laufkarte = build_runtime_card(name)
        assert designs.grundname(laufkarte) == IRON
        # Design 1 ist das Bild der jeweiligen Variante, Design 2 teilen sie.
        assert designs.bild_link(laufkarte, 1) == laufkarte["bild"]
        assert designs.bild_link(laufkarte, 2) == ZWEI


def test_bild_aus_wahl_faellt_auf_standard_zurueck(monkeypatch):
    karte = _karte(NORMAL)
    assert designs.bild_aus_wahl(karte, {NORMAL: 2}) == karte["bild"]
    monkeypatch.setitem(karte, "bild_2", ZWEI)
    assert designs.bild_aus_wahl(karte, {NORMAL: 2}) == ZWEI
    assert designs.bild_aus_wahl(karte, {}) == karte["bild"]
    assert designs.bild_aus_wahl(karte, None) == karte["bild"]


# --------------------------------------------------------------------------
# Mit Datenbank
# --------------------------------------------------------------------------
def test_init_db_legt_die_tabellen_an(testdb):
    async def ablauf():
        await db_modul.init_db()
        async with db_modul.db_context() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('user_designs', 'user_design_wahl')")
            return {z[0] for z in await cursor.fetchall()}

    assert _lauf(ablauf()) == {"user_designs", "user_design_wahl"}


def test_doppelt_freischalten_ist_harmlos(testdb):
    async def ablauf():
        erstes = await designs.freischalten(SPIELER, NORMAL, 2, "admin")
        zweites = await designs.freischalten(SPIELER, NORMAL, 2, "admin")
        return erstes, zweites, await designs.freigeschaltet(SPIELER, NORMAL)

    assert _lauf(ablauf()) == (True, False, {1, 2})


def test_ungueltige_nummern_und_karten_werden_abgelehnt(testdb):
    async def ablauf():
        return [
            await designs.freischalten(SPIELER, NORMAL, 1),
            await designs.freischalten(SPIELER, NORMAL, designs.MAX_DESIGNS + 1),
            await designs.freischalten(SPIELER, NORMAL, 0),
            await designs.freischalten(SPIELER, "Gibt es nicht", 2),
            await designs.freigeschaltet(SPIELER, NORMAL),
        ]

    assert _lauf(ablauf()) == [False, False, False, False, {1}]


def test_freischalten_ohne_link_geht_waehlen_nicht(testdb):
    async def ablauf():
        frei = await designs.freischalten(SPIELER, NORMAL, 2)
        gewaehlt = await designs.waehle(SPIELER, NORMAL, 2)
        return frei, gewaehlt, await designs.bild_fuer(SPIELER, _karte(NORMAL))

    assert _lauf(ablauf()) == (True, False, _karte(NORMAL)["bild"])


def test_nicht_freigeschaltet_laesst_sich_nicht_waehlen(testdb, mit_links):
    async def ablauf():
        return (await designs.waehle(SPIELER, NORMAL, 2),
                await designs.waehle(SPIELER, NORMAL, 3),
                await designs.waehlbare_designs(SPIELER, NORMAL))

    assert _lauf(ablauf()) == (False, False, [1])


def test_waehlen_und_anzeigen(testdb, mit_links):
    async def ablauf():
        await designs.freischalten(SPIELER, NORMAL, 3)
        await designs.freischalten(SPIELER, NORMAL, 2)
        ok = await designs.waehle(SPIELER, NORMAL, 3)
        return (ok,
                await designs.aktives_design(SPIELER, NORMAL),
                await designs.bild_fuer(SPIELER, _karte(NORMAL)),
                await designs.bild_fuer(ANDERER, _karte(NORMAL)),
                await designs.aktive_designs_von(SPIELER))

    ok, aktiv, bild, bild_anderer, alle = _lauf(ablauf())
    assert ok and aktiv == 3 and bild == DREI
    assert bild_anderer == _karte(NORMAL)["bild"], "andere Spieler bleiben beim Standard"
    assert alle == {NORMAL: 3}


def test_fallback_wenn_link_entfernt_wird(testdb, mit_links, monkeypatch):
    async def ablauf():
        await designs.freischalten(SPIELER, NORMAL, 2)
        await designs.waehle(SPIELER, NORMAL, 2)
        vorher = await designs.bild_fuer(SPIELER, _karte(NORMAL))
        monkeypatch.setitem(_karte(NORMAL), "bild_2", "")
        monkeypatch.setitem(_karte(NORMAL), "bild_3", "")
        return (vorher, await designs.bild_fuer(SPIELER, _karte(NORMAL)),
                await designs.aktives_design(SPIELER, NORMAL))

    assert _lauf(ablauf()) == (ZWEI, _karte(NORMAL)["bild"], 1)


def test_entziehen_setzt_auf_standard_zurueck(testdb, mit_links):
    async def ablauf():
        await designs.freischalten(SPIELER, NORMAL, 2)
        await designs.waehle(SPIELER, NORMAL, 2)
        weg = await designs.entziehen(SPIELER, NORMAL, 2)
        nochmal = await designs.entziehen(SPIELER, NORMAL, 2)
        return (weg, nochmal, await designs.bild_fuer(SPIELER, _karte(NORMAL)),
                await designs.aktive_designs_von(SPIELER))

    assert _lauf(ablauf()) == (True, False, _karte(NORMAL)["bild"], {})


def test_design_1_waehlen_und_alle_zuruecksetzen(testdb, mit_links):
    async def ablauf():
        for name in (NORMAL, IRON):
            await designs.freischalten(SPIELER, name, 2)
            await designs.waehle(SPIELER, name, 2)
        zurueck = await designs.waehle(SPIELER, NORMAL, 1)
        nach_einzeln = await designs.aktive_designs_von(SPIELER)
        anzahl = await designs.alle_zuruecksetzen(SPIELER)
        return zurueck, nach_einzeln, anzahl, await designs.aktive_designs_von(SPIELER)

    assert _lauf(ablauf()) == (True, {IRON: 2}, 1, {})


def test_iron_man_varianten_teilen_die_freischaltung(testdb, mit_links):
    standard, alpha = [v["variant_id"] for v in _karte(IRON)["variants"]]

    async def ablauf():
        await designs.freischalten(SPIELER, alpha, 2)
        schon_da = await designs.freischalten(SPIELER, standard, 2)
        await designs.waehle(SPIELER, standard, 2)
        return (schon_da,
                await designs.bild_fuer(SPIELER, build_runtime_card(alpha)),
                await designs.bild_fuer(SPIELER, build_runtime_card(standard)))

    assert _lauf(ablauf()) == (False, ZWEI, ZWEI)


def test_ohne_spieler_immer_standard(mit_links):
    assert asyncio.run(designs.bild_fuer(None, _karte(NORMAL))) == _karte(NORMAL)["bild"]
