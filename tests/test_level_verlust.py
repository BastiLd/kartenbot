"""Rolle verloren → private Nachricht mit Behalten/Entziehen (Plan 2, Schritt 7).

Grundregel: Bis der Besitzer entscheidet, ändert sich nichts.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
from services import db as db_modul
from services import designs
from services import level_rewards as lr

SPIELER = 999_001_001
SERVER = 555_000_3
ROLLE_5, ROLLE_10, ROLLE_15 = 105, 110, 115


@pytest.fixture
def testdb(tmp_path, monkeypatch):
    asyncio.run(db_modul.close_db())
    monkeypatch.setattr(db_modul, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(lr, "_schema_fuer_pfad", None)
    monkeypatch.setattr(designs, "_schema_fuer_pfad", None)


@pytest.fixture
def post(monkeypatch):
    """Die privaten Nachrichten an den Besitzer abfangen."""
    nachrichten = []

    async def senden(embed=None, view=None, **_k):
        nachrichten.append((embed, view))
        return SimpleNamespace(id=4242)

    monkeypatch.setattr(bot.bot, "get_user", lambda _uid: SimpleNamespace(send=senden))
    monkeypatch.setattr(bot.bot, "add_view", lambda *_a, **_k: None)
    return nachrichten


def _lauf(coro):
    async def mit_aufraeumen():
        try:
            return await coro
        finally:
            await db_modul.close_db()
    return asyncio.run(mit_aufraeumen())


def _server():
    return SimpleNamespace(id=SERVER, name="Testserver", get_channel=lambda _cid: None)


def _mitglied(rollen_ids, guild):
    return SimpleNamespace(id=SPIELER, guild=guild, mention=f"<@{SPIELER}>", bot=False,
                           display_name="Tester",
                           roles=[SimpleNamespace(id=r) for r in rollen_ids])


async def _vorbereiten(stufe_rollen=(ROLLE_15,)):
    """Level an, Zuordnung gesetzt, Spieler hat die Belohnungen bis Stufe 15."""
    await db_modul.init_db()
    await lr.setze_zuordnung(SERVER, {5: ROLLE_5, 10: ROLLE_10, 15: ROLLE_15})
    await lr.setze_aktiv(SERVER, True)
    guild = _server()
    await bot._level_rollenwechsel(_mitglied([], guild), _mitglied(list(stufe_rollen), guild))
    return guild


# --------------------------------------------------------------------------
# Wann wird gefragt?
# --------------------------------------------------------------------------
def test_rollentausch_nach_oben_fragt_nicht(testdb, post):
    async def ablauf():
        guild = await _vorbereiten((ROLLE_5,))
        post.clear()
        # MEE6 ersetzt Level 5 durch Level 10 — das ist kein Verlust.
        await bot._level_rollenwechsel(_mitglied([ROLLE_5], guild), _mitglied([ROLLE_10], guild))
        return post, await lr.offene_rueckfragen(SERVER)

    nachrichten, offen = _lauf(ablauf())
    assert nachrichten == [] and offen == []


def test_sinkende_stufe_fragt_genau_einmal(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        post.clear()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([ROLLE_5], guild))
        # Zweites Ereignis kurz danach (MEE6-Reset nimmt Rollen einzeln weg).
        await bot._level_rollenwechsel(_mitglied([ROLLE_5], guild), _mitglied([], guild))
        offen = await lr.offene_rueckfragen(SERVER)
        return post, offen, await designs.freigeschaltet(SPIELER, "Rocket")

    nachrichten, offen, rocket = _lauf(ablauf())
    assert len(nachrichten) == 1, "nur eine Nachfrage, auch bei mehreren Ereignissen"
    assert len(offen) == 1
    assert (offen[0]["von_stufe"], offen[0]["auf_stufe"]) == (15, 0), "die Rückfrage wird erweitert"
    assert rocket == {1, 2}, "bis zur Entscheidung ändert sich nichts"
    assert "Rocket" in nachrichten[0][0].description


def test_ohne_betroffene_belohnung_keine_frage(testdb, post):
    async def ablauf():
        await db_modul.init_db()
        await lr.setze_zuordnung(SERVER, {5: ROLLE_5, 10: ROLLE_10})
        await lr.setze_aktiv(SERVER, True)
        guild = _server()
        # Spieler war auf Stufe 10 (bringt nichts) und fällt auf 0.
        await bot._level_rollenwechsel(_mitglied([ROLLE_10], guild), _mitglied([], guild))
        return post, await lr.offene_rueckfragen(SERVER)

    nachrichten, offen = _lauf(ablauf())
    assert nachrichten == [] and offen == []


def test_server_verlassen_fragt_auch(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        post.clear()
        await bot.on_member_remove(_mitglied([ROLLE_15], guild))
        return post, await lr.offene_rueckfragen(SERVER)

    nachrichten, offen = _lauf(ablauf())
    assert len(nachrichten) == 1 and len(offen) == 1
    assert offen[0]["grund"] == lr.GRUND_SERVER_VERLASSEN
    assert "Server verlassen" in nachrichten[0][0].description


def test_ohne_level_system_keine_frage(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await lr.setze_aktiv(SERVER, False)
        post.clear()
        await bot.on_member_remove(_mitglied([ROLLE_15], guild))
        return post, await lr.offene_rueckfragen(SERVER)

    nachrichten, offen = _lauf(ablauf())
    assert nachrichten == [] and offen == []


def test_nicht_zustellbare_nachricht_laesst_den_fall_offen(testdb, monkeypatch):
    async def ablauf():
        async def kaputt(*_a, **_k):
            raise RuntimeError("DMs sind zu")

        monkeypatch.setattr(bot.bot, "add_view", lambda *_a, **_k: None)
        guild = await _vorbereiten()
        monkeypatch.setattr(bot.bot, "get_user", lambda _uid: SimpleNamespace(send=kaputt))
        monkeypatch.setattr(bot.bot, "fetch_user", kaputt)
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        return await lr.offene_rueckfragen(SERVER)

    offen = _lauf(ablauf())
    assert len(offen) == 1, "der Fall bleibt offen und taucht in /level-offen auf"


# --------------------------------------------------------------------------
# Entscheidung
# --------------------------------------------------------------------------
def _knopf(view, label):
    return next(k for k in view.children if k.label == label)


def _klick(user_id=bot.BASTI_USER_ID):
    it = SimpleNamespace(user=SimpleNamespace(id=user_id), guild=None, guild_id=None)
    it.response = SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock())
    return it


def test_behalten_laesst_alles_und_fragt_nie_wieder(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        view = post[-1][1]
        it = _klick()
        await _knopf(view, "Behalten").callback(it)
        # Erneuter Verlust darf nicht noch einmal fragen.
        post.clear()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        return (it, await designs.freigeschaltet(SPIELER, "Rocket"),
                await lr.offene_rueckfragen(SERVER), post)

    it, rocket, offen, nachrichten = _lauf(ablauf())
    assert rocket == {1, 2}
    assert "Behalten" in it.response.edit_message.await_args.kwargs["content"]
    assert offen == [] and nachrichten == []


def test_entziehen_nimmt_genau_die_richtigen_designs(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([ROLLE_5], guild))
        view = post[-1][1]
        await _knopf(view, "Entziehen").callback(_klick())
        return (await designs.freigeschaltet(SPIELER, "Rocket"),
                await designs.freigeschaltet(SPIELER, "Black Widow"),
                await lr.offene_rueckfragen(SERVER))

    rocket, widow, offen = _lauf(ablauf())
    assert rocket == {1}, "Rocket (Stufe 15) ist weg"
    assert widow == {1, 2}, "Black Widow (Stufe 5) bleibt"
    assert offen == []


def test_entzogenes_kommt_bei_erneutem_aufstieg_zurueck(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        await _knopf(post[-1][1], "Entziehen").callback(_klick())
        weg = await designs.freigeschaltet(SPIELER, "Rocket")
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_15], guild))
        return weg, await designs.freigeschaltet(SPIELER, "Rocket")

    weg, wieder = _lauf(ablauf())
    assert weg == {1} and wieder == {1, 2}


def test_fremde_duerfen_nicht_entscheiden(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        it = _klick(user_id=123456)
        await _knopf(post[-1][1], "Entziehen").callback(it)
        return it, await designs.freigeschaltet(SPIELER, "Rocket"), await lr.offene_rueckfragen(SERVER)

    it, rocket, offen = _lauf(ablauf())
    it.response.send_message.assert_awaited()
    assert rocket == {1, 2} and len(offen) == 1


def test_zweiter_klick_meldet_nur_noch(testdb, post):
    async def ablauf():
        guild = await _vorbereiten()
        await bot._level_rollenwechsel(_mitglied([ROLLE_15], guild), _mitglied([], guild))
        view = post[-1][1]
        await _knopf(view, "Behalten").callback(_klick())
        zweite = _klick()
        await _knopf(view, "Entziehen").callback(zweite)
        return zweite, await designs.freigeschaltet(SPIELER, "Rocket")

    zweite, rocket = _lauf(ablauf())
    assert "schon entschieden" in zweite.response.edit_message.await_args.kwargs["content"]
    assert rocket == {1, 2}
