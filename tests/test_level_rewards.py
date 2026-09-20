"""Level- und Einladungs-Belohnungen (Teil 1, Plan 2)."""
from __future__ import annotations

import asyncio

import pytest

import level_reward_config as cfg
from karten import karten
from services import db as db_modul
from services import designs
from services import level_rewards as lr

KARTENNAMEN = {k["name"] for k in karten}


def _alle_belohnungen():
    for quelle in (cfg.LEVEL_BELOHNUNGEN, cfg.EINLADUNG_STUFEN):
        for stufe, liste in quelle.items():
            for belohnung in liste:
                yield stufe, belohnung


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


# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
def test_jede_karte_existiert_und_nummern_sind_gueltig():
    for stufe, belohnung in _alle_belohnungen():
        if isinstance(belohnung, cfg.Design):
            assert belohnung.karte in KARTENNAMEN, f"Stufe {stufe}: Karte {belohnung.karte!r} fehlt"
            assert 2 <= belohnung.nummer <= designs.MAX_DESIGNS
        else:
            assert isinstance(belohnung, cfg.Staub)
            assert belohnung.menge > 0


def test_stufen_sind_aufsteigend_und_passen_zusammen():
    for tabelle in (cfg.LEVEL_STUFEN, cfg.LEVEL_BELOHNUNGEN, cfg.LEVEL_HINWEISE, cfg.EINLADUNG_STUFEN):
        stufen = list(tabelle)
        assert stufen == sorted(stufen) and len(set(stufen)) == len(stufen)
        assert all(isinstance(s, int) and s > 0 for s in stufen)
    assert set(cfg.LEVEL_BELOHNUNGEN) <= set(cfg.LEVEL_STUFEN)
    assert set(cfg.LEVEL_HINWEISE) <= set(cfg.LEVEL_STUFEN)
    assert cfg.EINLADUNG_STAUB_SONST > 0 and cfg.EINGELADENER_STAUB > 0


def test_rollennamen_sind_eindeutig():
    normiert = [" ".join(t.lower().split()) for t in cfg.LEVEL_STUFEN.values()]
    assert len(set(normiert)) == len(normiert)


def test_listen_des_nutzers():
    """Genau die Tabellen aus dem Plan (Abschnitt 4)."""
    level = {s: [(b.karte, b.nummer) for b in liste] for s, liste in cfg.LEVEL_BELOHNUNGEN.items()}
    assert level == {5: [("Black Widow", 2)], 15: [("Rocket", 2)], 20: [("The Thing", 2)],
                     35: [("Doctor Strange", 2)], 40: [("Groot", 2)],
                     45: [("Captain Marvel", 2)], 50: [("Loki", 2)]}
    assert sorted(cfg.LEVEL_STUFEN) == [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
    assert cfg.EINLADUNG_STUFEN == {
        1: [cfg.Design("Captain America", 2)],
        5: [cfg.Design("Spider-Man", 2), cfg.Staub(5)],
        10: [cfg.Design("Scarlet Witch", 2), cfg.Design("Namor", 2), cfg.Staub(10)],
    }
    assert (cfg.EINLADUNG_STAUB_SONST, cfg.EINGELADENER_STAUB) == (5, 5)


# --------------------------------------------------------------------------
# Tabellen
# --------------------------------------------------------------------------
def test_init_db_legt_die_tabellen_an(testdb):
    async def ablauf():
        await db_modul.init_db()
        async with db_modul.db_context() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('level_rollen', 'belohnungs_protokoll', 'level_rueckfragen')")
            return {z[0] for z in await cursor.fetchall()}

    assert _lauf(ablauf()) == {"level_rollen", "belohnungs_protokoll", "level_rueckfragen"}


# --------------------------------------------------------------------------
# Stufe aus Rollen
# --------------------------------------------------------------------------
ZUORDNUNG = {1: 101, 5: 105, 10: 110, 15: 115, 50: 150}


def test_stufe_aus_rollen_stapelnd_und_ersetzend():
    # MEE6 lässt alte Rollen stehen: mehrere Level-Rollen, die höchste zählt.
    assert lr.stufe_aus_rollen([101, 105, 110], ZUORDNUNG) == 10
    # MEE6 entfernt alte Rollen: nur eine Rolle, dieselbe Stufe.
    assert lr.stufe_aus_rollen([110], ZUORDNUNG) == 10
    # Fremde Rollen stören nicht.
    assert lr.stufe_aus_rollen([999, 115, 42], ZUORDNUNG) == 15
    assert lr.stufe_aus_rollen([999], ZUORDNUNG) == 0
    assert lr.stufe_aus_rollen([], ZUORDNUNG) == 0
    assert lr.stufe_aus_rollen([105], {}) == 0


def test_verlust_nur_wenn_die_stufe_sinkt():
    assert lr.verloren_haben(5, 10) is False, "5 -> 10 ist ein Aufstieg"
    assert lr.verloren_haben(10, 10) is False
    assert lr.verloren_haben(10, 0) is True
    assert lr.verloren_haben(50, 45) is True


# --------------------------------------------------------------------------
# Was steht zu
# --------------------------------------------------------------------------
def test_faellige_belohnungen_sind_kumulativ():
    karten_bis_20 = [f.belohnung.karte for f in lr.faellige_belohnungen(20, [])]
    assert karten_bis_20 == ["Black Widow", "Rocket", "The Thing"]
    assert lr.faellige_belohnungen(4, []) == []
    assert [f.stufe for f in lr.faellige_belohnungen(50, [])] == [5, 15, 20, 35, 40, 45, 50]


def test_schon_erledigtes_faellt_weg():
    alle = lr.faellige_belohnungen(20, [])
    schon = {alle[0].schluessel}
    rest = lr.faellige_belohnungen(20, schon)
    assert [f.belohnung.karte for f in rest] == ["Rocket", "The Thing"]
    assert alle[0].schluessel == "level:5:design:Black Widow:2"


def test_belohnungen_zwischen_zwei_stufen():
    zwischen = lr.belohnungen_zwischen(20, 5)
    assert [f.belohnung.karte for f in zwischen] == ["Rocket", "The Thing"]
    assert lr.belohnungen_zwischen(5, 5) == []


# Entscheidung des Nutzers (weicht vom Plan ab): Jede Einladung ohne eigene
# Stufe bringt 5 Staub - also auch 2, 3, 4, 6, 7, 8 und 9.
@pytest.mark.parametrize("anzahl,erwartet", [
    (1, ["Design 2 von Captain America"]),
    (2, ["5 Infinitydust"]),
    (4, ["5 Infinitydust"]),
    (5, ["Design 2 von Spider-Man", "5 Infinitydust"]),
    (9, ["5 Infinitydust"]),
    (10, ["Design 2 von Scarlet Witch", "Design 2 von Namor", "10 Infinitydust"]),
    (11, ["5 Infinitydust"]),
    (12, ["5 Infinitydust"]),
])
def test_einladungsstufen(anzahl, erwartet):
    assert [f.text() for f in lr.einladung_belohnungen(anzahl)] == erwartet


def test_einladung_nachholen():
    faellig = lr.einladung_faellige(6, [])
    assert [f.schluessel for f in faellig] == [
        "einladung:1:design:Captain America:2",
        "einladung:2:staub",
        "einladung:3:staub",
        "einladung:4:staub",
        "einladung:5:design:Spider-Man:2",
        "einladung:5:staub",
        "einladung:6:staub",
    ]
    ohne_staub = lr.einladung_faellige(6, [], mit_staub=False)
    assert [f.schluessel for f in ohne_staub] == [
        "einladung:1:design:Captain America:2",
        "einladung:5:design:Spider-Man:2",
    ]
    schon = {"einladung:2:staub", "einladung:1:design:Captain America:2"}
    assert [f.schluessel for f in lr.einladung_faellige(3, schon)] == ["einladung:3:staub"]
    assert lr.einladung_faellige(0, []) == []


# --------------------------------------------------------------------------
# Vergabe: höchstens einmal
# --------------------------------------------------------------------------
SPIELER = 999_000_601


async def _staub(user_id=SPIELER) -> int:
    from services.user_data import get_infinitydust
    return await get_infinitydust(user_id)


def test_zweimal_vergeben_wirkt_nur_einmal(testdb):
    async def ablauf():
        await db_modul.init_db()
        faellig = lr.faellige_belohnungen(5, [])
        erste = await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        zweite = await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        return erste, zweite, await lr.erledigte_schluessel(SPIELER)

    erste, zweite, erledigt = _lauf(ablauf())
    assert erste.designs == 1 and not erste.uebersprungen
    assert zweite.vergeben == [] and len(zweite.uebersprungen) == 1
    assert erledigt == {"level:5:design:Black Widow:2"}


def test_verkauftes_design_kommt_nicht_zurueck(testdb):
    """Wer sein Design loswird, bekommt es nicht automatisch erneut."""
    async def ablauf():
        await db_modul.init_db()
        faellig = lr.faellige_belohnungen(5, [])
        await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        await designs.entziehen(SPIELER, "Black Widow", 2)      # z. B. durch einen Admin
        nochmal = await lr.vergebe(SPIELER, lr.faellige_belohnungen(5, await lr.erledigte_schluessel(SPIELER)),
                                   lr.QUELLE_LEVEL)
        return nochmal, await designs.freigeschaltet(SPIELER, "Black Widow")

    nochmal, frei = _lauf(ablauf())
    assert nochmal.vergeben == [] and nochmal.uebersprungen == []
    assert frei == {1}, "das Design bleibt weg"


def test_entzogenes_design_wird_bei_erneutem_aufstieg_wieder_vergeben(testdb):
    async def ablauf():
        await db_modul.init_db()
        faellig = lr.faellige_belohnungen(5, [])
        await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        await lr.entziehe(SPIELER, faellig)                      # Rolle verloren -> entzogen
        nach_entzug = await designs.freigeschaltet(SPIELER, "Black Widow")
        offen = lr.faellige_belohnungen(5, await lr.erledigte_schluessel(SPIELER))
        wieder = await lr.vergebe(SPIELER, offen, lr.QUELLE_LEVEL)
        return nach_entzug, wieder, await designs.freigeschaltet(SPIELER, "Black Widow")

    nach_entzug, wieder, jetzt = _lauf(ablauf())
    assert nach_entzug == {1}
    assert wieder.designs == 1
    assert jetzt == {1, 2}


def test_behalten_wird_nie_wieder_vergeben(testdb):
    async def ablauf():
        await db_modul.init_db()
        faellig = lr.faellige_belohnungen(5, [])
        await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        await lr.behalte(SPIELER, faellig)
        return await lr.erledigte_schluessel(SPIELER), lr.faellige_belohnungen(5, await lr.erledigte_schluessel(SPIELER))

    erledigt, offen = _lauf(ablauf())
    assert erledigt == {"level:5:design:Black Widow:2"}
    assert offen == []


def test_staub_wird_gutgeschrieben(testdb):
    async def ablauf():
        await db_modul.init_db()
        vorher = await _staub()
        erste = await lr.vergebe(SPIELER, lr.einladung_belohnungen(11), lr.QUELLE_EINLADUNG)
        zweite = await lr.vergebe(SPIELER, lr.einladung_belohnungen(11), lr.QUELLE_EINLADUNG)
        return vorher, erste, zweite, await _staub()

    vorher, erste, zweite, nachher = _lauf(ablauf())
    assert erste.staub == 5 and nachher == vorher + 5
    assert zweite.vergeben == [], "derselbe Schlüssel zählt nur einmal"


def test_fehlende_karte_wird_gemeldet_aber_bricht_nicht_ab(testdb):
    async def ablauf():
        await db_modul.init_db()
        kaputt = lr.Faellig("level:99:design:Gibt es nicht:2", 99,
                            cfg.Design("Gibt es nicht", 2), lr.QUELLE_LEVEL)
        gut = lr.faellige_belohnungen(5, [])[0]
        ergebnis = await lr.vergebe(SPIELER, [kaputt, gut], lr.QUELLE_LEVEL)
        return ergebnis, await lr.erledigte_schluessel(SPIELER)

    ergebnis, erledigt = _lauf(ablauf())
    assert len(ergebnis.fehler) == 1 and "gibt es nicht" in ergebnis.fehler[0][1].lower()
    assert ergebnis.designs == 1, "die gute Belohnung kommt trotzdem an"
    assert erledigt == {"level:5:design:Black Widow:2"}, "die kaputte steht nicht im Protokoll"


def test_fehlgeschlagene_vergabe_gibt_den_platz_wieder_frei(testdb, monkeypatch):
    async def ablauf():
        await db_modul.init_db()

        async def kaputt(*_a, **_k):
            raise RuntimeError("Datenbank weg")

        monkeypatch.setattr(designs, "freischalten", kaputt)
        faellig = lr.faellige_belohnungen(5, [])
        ergebnis = await lr.vergebe(SPIELER, faellig, lr.QUELLE_LEVEL)
        return ergebnis, await lr.erledigte_schluessel(SPIELER)

    ergebnis, erledigt = _lauf(ablauf())
    assert len(ergebnis.fehler) == 1 and ergebnis.vergeben == []
    assert erledigt == set(), "kein Eintrag, also spaeter erneut versuchbar"
