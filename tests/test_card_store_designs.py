"""Design-Bilder (bild_2, bild_3) über die Kartenänderungen aus der Datenbank.

Die Website legt die Links in card_overrides ab; der Bot legt sie mit
card_store.anwenden() auf die laufenden Karten. Geprüft wird hier, dass sie
ankommen — und dass sie wieder verschwinden, wenn die Karte zurückgesetzt
wird, ohne dass der Bot neu starten muss.
"""
from __future__ import annotations

import asyncio

import pytest

from services import card_store
from services import db as db_modul


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))


def _lauf(coro):
    """Ausführen und die Verbindung in derselben Schleife wieder schließen."""
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


def _karte(name="Testheld"):
    return {"name": name, "bild": "https://example.com/standard.png", "hp": 100}


def test_design_felder_sind_aenderbar():
    assert card_store.AENDERBAR[-2:] == ("bild_2", "bild_3")
    for feld in ("seltenheit", "hp", "beschreibung", "bild", "attacks"):
        assert feld in card_store.AENDERBAR


def test_karte_mit_bild_2_laesst_sich_laden(testdb):
    async def ablauf():
        await card_store.setze("Testheld", {"bild_2": "https://i.imgur.com/zwei.png"})
        liste = [_karte(), _karte("Andere")]
        angepasst = await card_store.anwenden(liste)
        return angepasst, liste

    angepasst, liste = _lauf(ablauf())
    assert angepasst == 1
    assert liste[0]["bild_2"] == "https://i.imgur.com/zwei.png"
    assert liste[0]["bild"] == "https://example.com/standard.png"
    assert "bild_2" not in liste[1]


def test_geleerter_link_kommt_als_leer_an(testdb):
    async def ablauf():
        liste = [_karte()]
        await card_store.setze("Testheld", {"bild_2": "https://i.imgur.com/zwei.png"})
        await card_store.anwenden(liste)
        await card_store.setze("Testheld", {"bild_2": ""})
        await card_store.anwenden(liste)
        return liste

    assert _lauf(ablauf())[0]["bild_2"] == ""


def test_zuruecksetzen_entfernt_design_ohne_neustart(testdb):
    async def ablauf():
        liste = [_karte()]
        await card_store.setze("Testheld", {"bild_2": "https://i.imgur.com/zwei.png",
                                            "bild_3": "https://i.imgur.com/drei.png"})
        await card_store.anwenden(liste)
        await card_store.zuruecksetzen("Testheld")
        await card_store.anwenden(liste)
        return liste

    karte = _lauf(ablauf())[0]
    assert "bild_2" not in karte
    assert "bild_3" not in karte
    assert karte["bild"] == "https://example.com/standard.png"


def test_ohne_aenderungen_bleibt_alles_wie_es_ist(testdb):
    liste = [_karte()]
    vorher = [dict(k) for k in liste]
    assert _lauf(card_store.anwenden(liste)) == 0
    assert liste == vorher
