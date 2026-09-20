"""Level-Aufstieg erkennen, vergeben und melden (Plan 2, Schritt 5).

Der wichtigste Punkt steht unten: Die Auszeit-Mitschrift in on_member_update
muss unverändert weiterlaufen.
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

SPIELER = 999_000_801
SERVER = 555_000_2
ROLLE_5, ROLLE_10, ROLLE_15 = 105, 110, 115
ZWEI = "https://i.imgur.com/zwei.png"


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


def _kanal():
    return SimpleNamespace(id=777, send=AsyncMock())


def _server(kanal=None):
    return SimpleNamespace(id=SERVER, name="Testserver", get_channel=lambda _cid: kanal)


def _mitglied(rollen_ids, guild):
    return SimpleNamespace(id=SPIELER, guild=guild, mention=f"<@{SPIELER}>", bot=False,
                           roles=[SimpleNamespace(id=r) for r in rollen_ids])


async def _vorbereiten(*, aktiv=True, kanal=None):
    await db_modul.init_db()
    await lr.setze_zuordnung(SERVER, {5: ROLLE_5, 10: ROLLE_10, 15: ROLLE_15})
    await lr.setze_aktiv(SERVER, aktiv)
    if kanal is not None:
        await lr.setze_meldungs_kanal(SERVER, kanal.id)


# --------------------------------------------------------------------------
# Aufstieg
# --------------------------------------------------------------------------
def test_aufstieg_vergibt_und_meldet(testdb):
    async def ablauf():
        kanal = _kanal()
        await _vorbereiten(kanal=kanal)
        guild = _server(kanal)
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_5], guild))
        return kanal, await designs.freigeschaltet(SPIELER, "Black Widow")

    kanal, frei = _lauf(ablauf())
    assert frei == {1, 2}
    kanal.send.assert_awaited_once()
    embed = kanal.send.await_args.kwargs["embed"]
    assert "Stark Industries Praktikant" in embed.description
    assert "Design 2 von Black Widow" in embed.description
    assert "Das Bild folgt." in embed.description, "ohne Link kein leeres Bild"
    assert embed.image.url is None


def test_meldung_zeigt_das_bild_wenn_der_link_da_ist(testdb, monkeypatch):
    async def ablauf():
        monkeypatch.setitem(next(k for k in __import__("karten").karten
                                 if k["name"] == "Black Widow"), "bild_2", ZWEI)
        kanal = _kanal()
        await _vorbereiten(kanal=kanal)
        guild = _server(kanal)
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_5], guild))
        return kanal

    embed = _lauf(ablauf()).send.await_args.kwargs["embed"]
    assert embed.image.url == ZWEI
    assert "Das Bild folgt." not in embed.description


def test_aufstieg_ist_kumulativ(testdb):
    async def ablauf():
        kanal = _kanal()
        await _vorbereiten(kanal=kanal)
        guild = _server(kanal)
        # Direkt von 0 auf 15: Black Widow (5) und Rocket (15) zusammen.
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_15], guild))
        return kanal, await designs.freigeschaltet(SPIELER, "Rocket"), await designs.freigeschaltet(SPIELER, "Black Widow")

    kanal, rocket, widow = _lauf(ablauf())
    assert rocket == {1, 2} and widow == {1, 2}
    assert kanal.send.await_count == 1, "eine Meldung, nicht zwei"


def test_ohne_neue_belohnung_keine_meldung(testdb):
    async def ablauf():
        kanal = _kanal()
        await _vorbereiten(kanal=kanal)
        guild = _server(kanal)
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_5], guild))
        kanal.send.reset_mock()
        # 5 -> 10: MEE6 ersetzt die Rolle, Level 10 bringt im Bot nichts.
        await bot._level_rollenwechsel(_mitglied([ROLLE_5], guild), _mitglied([ROLLE_10], guild))
        return kanal

    assert _lauf(ablauf()).send.await_count == 0


def test_abgeschaltet_vergibt_nichts(testdb):
    async def ablauf():
        kanal = _kanal()
        await _vorbereiten(aktiv=False, kanal=kanal)
        guild = _server(kanal)
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_15], guild))
        return kanal, await lr.erledigte_schluessel(SPIELER)

    kanal, erledigt = _lauf(ablauf())
    assert erledigt == set()
    kanal.send.assert_not_awaited()


def test_ohne_rollenwechsel_passiert_nichts(testdb, monkeypatch):
    async def ablauf():
        await _vorbereiten()

        async def darf_nicht(*_a, **_k):                          # pragma: no cover
            raise AssertionError("ohne Rollenwechsel darf nichts nachgesehen werden")

        monkeypatch.setattr(lr, "zuordnung_von", darf_nicht)
        guild = _server(_kanal())
        await bot._level_rollenwechsel(_mitglied([ROLLE_5], guild), _mitglied([ROLLE_5], guild))
        return await lr.erledigte_schluessel(SPIELER)

    assert _lauf(ablauf()) == set()


def test_fehlender_kanal_verhindert_die_vergabe_nicht(testdb):
    async def ablauf():
        await _vorbereiten()                                       # kein Kanal eingestellt
        guild = _server(None)
        await bot._level_rollenwechsel(_mitglied([], guild), _mitglied([ROLLE_5], guild))
        return await designs.freigeschaltet(SPIELER, "Black Widow")

    assert _lauf(ablauf()) == {1, 2}


# --------------------------------------------------------------------------
# on_member_update: die Auszeit-Mitschrift muss weiterlaufen
# --------------------------------------------------------------------------
def test_auszeit_wird_weiter_mitgeschrieben(testdb, monkeypatch):
    from services import web_jobs

    async def ablauf():
        await _vorbereiten()
        notiert = []

        async def merke(*args, **kwargs):
            notiert.append((args, kwargs))

        monkeypatch.setattr(web_jobs, "log_mod_event", merke)
        guild = _server(_kanal())
        vorher = _mitglied([], guild)
        nachher = _mitglied([ROLLE_5], guild)
        vorher.timed_out_until = None
        nachher.timed_out_until = SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00")
        await bot.on_member_update(vorher, nachher)
        return notiert, await designs.freigeschaltet(SPIELER, "Black Widow")

    notiert, frei = _lauf(ablauf())
    assert len(notiert) == 1 and notiert[0][0][2] == "timeout"
    assert frei == {1, 2}, "beides passiert: Level-Belohnung und Auszeit-Mitschrift"


def test_fehler_in_der_levelpruefung_stoppt_die_auszeit_nicht(testdb, monkeypatch):
    from services import web_jobs

    async def ablauf():
        await _vorbereiten()
        notiert = []

        async def merke(*args, **kwargs):
            notiert.append(args)

        async def kaputt(*_a, **_k):
            raise RuntimeError("Datenbank weg")

        monkeypatch.setattr(web_jobs, "log_mod_event", merke)
        monkeypatch.setattr(bot, "_level_rollenwechsel", kaputt)
        guild = _server(_kanal())
        vorher = _mitglied([], guild)
        nachher = _mitglied([ROLLE_5], guild)
        vorher.timed_out_until = SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00")
        nachher.timed_out_until = None
        await bot.on_member_update(vorher, nachher)
        return notiert

    notiert = _lauf(ablauf())
    assert len(notiert) == 1 and notiert[0][2] == "timeout_aufgehoben"


# --------------------------------------------------------------------------
# Nachholen beim Start
# --------------------------------------------------------------------------
def test_offene_aufstiege_finden(testdb):
    async def ablauf():
        await _vorbereiten()
        guild = _server(None)
        guild.members = [_mitglied([ROLLE_5], guild),
                         SimpleNamespace(id=2, guild=guild, bot=True, roles=[SimpleNamespace(id=ROLLE_15)]),
                         SimpleNamespace(id=3, guild=guild, bot=False, roles=[SimpleNamespace(id=999)])]
        offen_vorher = await bot._level_offene_aufstiege(guild)
        await lr.vergebe(SPIELER, lr.faellige_belohnungen(5, []), lr.QUELLE_LEVEL)
        return offen_vorher, await bot._level_offene_aufstiege(guild)

    offen_vorher, offen_nachher = _lauf(ablauf())
    assert [(m.id, stufe) for m, stufe in offen_vorher] == [(SPIELER, 5)], "Bots und Leute ohne Level-Rolle zaehlen nicht"
    assert offen_nachher == [], "wer schon alles hat, steht nicht mehr drin"


def test_zu_viele_offene_werden_nur_gemeldet(testdb, monkeypatch):
    async def ablauf():
        await _vorbereiten()
        kanal = _kanal()
        await lr.setze_meldungs_kanal(SERVER, kanal.id)
        guild = _server(kanal)
        guild.members = []
        for nummer in range(bot.LEVEL_NACHHOLEN_MAX + 1):
            guild.members.append(SimpleNamespace(
                id=900_000 + nummer, guild=guild, bot=False, mention=f"<@{900_000 + nummer}>",
                roles=[SimpleNamespace(id=ROLLE_5)]))
        dms = []

        async def merke_dm(text, **kwargs):
            dms.append((text, kwargs))

        monkeypatch.setattr(bot, "_send_basti_log_dm", merke_dm)
        await bot._level_nachholen_beim_start([guild])
        return dms, kanal, await lr.erledigte_schluessel(900_000)

    dms, kanal, erledigt = _lauf(ablauf())
    assert len(dms) == 1 and "level-vorschau" in dms[0][0].lower()
    assert erledigt == set(), "nichts vergeben"
    kanal.send.assert_not_awaited()


def test_wenige_offene_werden_nachgeholt(testdb, monkeypatch):
    async def ablauf():
        kanal = _kanal()
        await _vorbereiten(kanal=kanal)
        guild = _server(kanal)
        guild.members = [_mitglied([ROLLE_5], guild)]
        monkeypatch.setattr(bot.role_manager, "PAUSE_BETWEEN_CALLS", 0)
        await bot._level_nachholen_beim_start([guild])
        return kanal, await designs.freigeschaltet(SPIELER, "Black Widow")

    kanal, frei = _lauf(ablauf())
    assert frei == {1, 2}
    kanal.send.assert_awaited_once()
