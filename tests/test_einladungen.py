"""Einladungs-Belohnungen nach level_reward_config (Plan 2, Schritt 8).

Die Iron-Man-Karte bei der ersten Einladung gibt es nicht mehr; stattdessen
Designs bei 1, 5 und 10 und sonst Staub für den Einlader. Der Eingeladene
bekommt immer 5 Staub.
"""
from __future__ import annotations

import asyncio

import pytest

from services import db as db_modul
from services import designs
from services import level_rewards as lr
from services.invite_store import (create_invite_pending, finalize_invite_pending_if_ready,
                                   mark_invite_pending_flag)
from services.user_data import get_infinitydust, get_user_karten

EINLADER = 999_001_101
SERVER = 555_000_4


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


async def _einladung(nummer: int) -> dict:
    """Eine vollständige, bestätigte Einladung mit einem neuen Eingeladenen."""
    eingeladener = 700_000 + nummer
    pending_id, _neu = await create_invite_pending(
        guild_id=SERVER, channel_id=1, created_by_id=EINLADER, mode="invitee",
        inviter_id=EINLADER, invitee_id=eingeladener, invitee_is_admin=False, need_admin=False)
    await mark_invite_pending_flag(pending_id, inviter=True)
    await mark_invite_pending_flag(pending_id, invitee=True)
    ergebnis = await finalize_invite_pending_if_ready(pending_id, alpha_enabled=False)
    return {"ergebnis": ergebnis, "eingeladener": eingeladener}


def test_erste_einladung_gibt_design_statt_karte(testdb):
    async def ablauf():
        await db_modul.init_db()
        lauf = await _einladung(1)
        return (lauf["ergebnis"]["reward_summary"],
                await designs.freigeschaltet(EINLADER, "Captain America"),
                await get_infinitydust(EINLADER),
                await get_infinitydust(lauf["eingeladener"]),
                await get_user_karten(EINLADER))

    summary, cap, staub_einlader, staub_eingeladener, karten = _lauf(ablauf())
    assert summary["anzahl"] == 1
    assert summary["designs"] == [{"karte": "Captain America", "nummer": 2}]
    assert cap == {1, 2}
    assert staub_einlader == 0, "bei der ersten Einladung nur das Design"
    assert staub_eingeladener == 5
    assert karten == [], "keine Iron-Man-Karte mehr"


def test_zweite_einladung_gibt_staub(testdb):
    async def ablauf():
        await db_modul.init_db()
        await _einladung(1)
        lauf = await _einladung(2)
        return lauf["ergebnis"]["reward_summary"], await get_infinitydust(EINLADER)

    summary, staub = _lauf(ablauf())
    assert summary == {"anzahl": 2, "designs": [], "staub_einlader": 5, "staub_eingeladener": 5}
    assert staub == 5


def test_fuenfte_einladung_gibt_design_und_staub(testdb):
    async def ablauf():
        await db_modul.init_db()
        for nummer in range(1, 6):
            lauf = await _einladung(nummer)
        return (lauf["ergebnis"]["reward_summary"],
                await designs.freigeschaltet(EINLADER, "Spider-Man"),
                await get_infinitydust(EINLADER))

    summary, spidey, staub = _lauf(ablauf())
    assert summary["designs"] == [{"karte": "Spider-Man", "nummer": 2}]
    assert summary["staub_einlader"] == 5
    assert spidey == {1, 2}
    # 2., 3., 4. je 5 Staub, dazu 5 aus der Stufe 5.
    assert staub == 20


def test_zehnte_einladung_gibt_zwei_designs(testdb):
    async def ablauf():
        await db_modul.init_db()
        for nummer in range(1, 11):
            lauf = await _einladung(nummer)
        return (lauf["ergebnis"]["reward_summary"],
                await designs.freigeschaltet(EINLADER, "Scarlet Witch"),
                await designs.freigeschaltet(EINLADER, "Namor"),
                await get_infinitydust(EINLADER))

    summary, witch, namor, staub = _lauf(ablauf())
    assert summary["designs"] == [{"karte": "Scarlet Witch", "nummer": 2},
                                  {"karte": "Namor", "nummer": 2}]
    assert summary["staub_einlader"] == 10
    assert witch == {1, 2} and namor == {1, 2}
    # Staub: 2,3,4,6,7,8,9 (7x5) + Stufe 5 (5) + Stufe 10 (10) = 50
    assert staub == 50


def test_elfte_einladung_gibt_weiter_staub(testdb):
    async def ablauf():
        await db_modul.init_db()
        for nummer in range(1, 12):
            lauf = await _einladung(nummer)
        return lauf["ergebnis"]["reward_summary"], await get_infinitydust(EINLADER)

    summary, staub = _lauf(ablauf())
    assert summary == {"anzahl": 11, "designs": [], "staub_einlader": 5, "staub_eingeladener": 5}
    assert staub == 55


def test_eingeladener_bekommt_immer_staub(testdb):
    async def ablauf():
        await db_modul.init_db()
        staende = []
        for nummer in range(1, 4):
            lauf = await _einladung(nummer)
            staende.append(await get_infinitydust(lauf["eingeladener"]))
        return staende

    assert _lauf(ablauf()) == [5, 5, 5]


def test_jede_stufe_wird_nur_einmal_vergeben(testdb):
    """Der Schlüssel im Protokoll verhindert doppelte Vergaben."""
    async def ablauf():
        await db_modul.init_db()
        await _einladung(1)
        vorher = await lr.erledigte_schluessel(EINLADER)
        # Zweiter Aufruf derselben Einladung: nichts Neues.
        zweite = await lr.vergebe(EINLADER, lr.einladung_belohnungen(1), lr.QUELLE_EINLADUNG)
        return vorher, zweite, await lr.erledigte_schluessel(EINLADER)

    vorher, zweite, nachher = _lauf(ablauf())
    assert vorher == nachher == {"einladung:1:design:Captain America:2"}
    assert zweite.vergeben == []
