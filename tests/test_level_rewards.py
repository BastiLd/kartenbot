"""Level- und Einladungs-Belohnungen (Teil 1, Plan 2)."""
from __future__ import annotations

import asyncio

import pytest

import level_reward_config as cfg
from karten import karten
from services import db as db_modul
from services import designs

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
    assert max(cfg.EINLADUNG_STUFEN) < cfg.EINLADUNG_AB_STUFE
    assert cfg.EINLADUNG_STAUB_AB_11 > 0 and cfg.EINGELADENER_STAUB > 0


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
    assert (cfg.EINLADUNG_AB_STUFE, cfg.EINLADUNG_STAUB_AB_11, cfg.EINGELADENER_STAUB) == (11, 5, 5)


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
