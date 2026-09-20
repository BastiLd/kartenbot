"""Einrichtung des Level-Systems: /level-einrichten, /level-rolle, /level-kanal."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from botcommands import level_admin
from level_reward_config import LEVEL_STUFEN
from services import db as db_modul
from services import designs
from services import level_rewards as lr
from tests.view_harness import make_interaction

ADMIN = 999_000_701
SERVER = 555_000_1


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


def _antwort(it) -> str:
    """Der Text der Antwort - egal ob als Argument oder als content=."""
    aufruf = it.response.send_message.await_args
    return str(aufruf.kwargs.get("content") or (aufruf.args[0] if aufruf.args else ""))


def _rolle(rid, name):
    return SimpleNamespace(id=rid, name=name, mention=f"<@&{rid}>")


def _interaktion(guild=None):
    it = make_interaction(ADMIN)
    it.guild = guild
    return it


def _server(rollen):
    return SimpleNamespace(id=SERVER, roles=rollen, me=SimpleNamespace(id=1))


def _kanal(server, *, senden=True, einbetten=True, sendefehler=False):
    kanal = SimpleNamespace(id=777, mention="#level", guild=server)
    kanal.permissions_for = lambda _m: SimpleNamespace(send_messages=senden, embed_links=einbetten)
    kanal.send = AsyncMock(side_effect=RuntimeError("keine Rechte") if sendefehler else None)
    return kanal


# --------------------------------------------------------------------------
# Rollen finden
# --------------------------------------------------------------------------
def test_rollen_werden_ueber_den_namen_gefunden():
    rollen = [
        (11, "  stark industries   praktikant "),   # Groß/klein und Leerzeichen egal
        (12, "Howling Commandos"),
        (13, "Irgendeine andere Rolle"),
    ]
    assert lr.passende_rollen(rollen) == {5: 11, 15: 12}


def test_ohne_passende_rollen_gibt_es_nichts():
    assert lr.passende_rollen([(1, "Mitglied"), (2, "Moderator")]) == {}
    assert lr.passende_rollen([]) == {}


def test_vorschlag_zeigt_gefundene_und_fehlende():
    zeilen = level_admin.vorschlag_zeilen({5: 11}, {11: "Stark Industries Praktikant"})
    assert len(zeilen) == len(LEVEL_STUFEN)
    assert zeilen[1].startswith("✅ Level 5 →")
    assert "❌ Level 10" in zeilen[2] and "nicht gefunden" in zeilen[2]


def test_stufen_auswahl_passt_zur_konfiguration():
    auswahl = level_admin.stufen_auswahl()
    assert [w for _t, w in auswahl] == sorted(LEVEL_STUFEN)
    assert all(len(t) <= 100 for t, _w in auswahl)


# --------------------------------------------------------------------------
# /level-einrichten
# --------------------------------------------------------------------------
def test_uebernehmen_speichert_die_zuordnung(testdb):
    async def ablauf():
        server = _server([_rolle(11, "Stark Industries Praktikant"), _rolle(12, "Howling Commandos")])
        it = _interaktion(server)
        await level_admin.einrichten(it)
        view = it.response.send_message.await_args.kwargs["view"]
        knopf = next(k for k in view.children if k.label == "Übernehmen")
        assert not knopf.disabled
        await knopf.callback(_interaktion(server))
        return await lr.zuordnung_von(SERVER)

    assert _lauf(ablauf()) == {5: 11, 15: 12}


def test_abbrechen_speichert_nichts(testdb):
    async def ablauf():
        server = _server([_rolle(11, "Stark Industries Praktikant")])
        it = _interaktion(server)
        await level_admin.einrichten(it)
        view = it.response.send_message.await_args.kwargs["view"]
        abbrechen = next(k for k in view.children if k.label == "Abbrechen")
        await abbrechen.callback(_interaktion(server))
        return await lr.zuordnung_von(SERVER)

    assert _lauf(ablauf()) == {}


def test_ohne_treffer_ist_uebernehmen_gesperrt(testdb):
    async def ablauf():
        it = _interaktion(_server([_rolle(9, "Mitglied")]))
        await level_admin.einrichten(it)
        return it.response.send_message.await_args.kwargs["view"]

    view = _lauf(ablauf())
    assert next(k for k in view.children if k.label == "Übernehmen").disabled


def test_fremde_duerfen_nicht_uebernehmen(testdb):
    async def ablauf():
        server = _server([_rolle(11, "Stark Industries Praktikant")])
        it = _interaktion(server)
        await level_admin.einrichten(it)
        view = it.response.send_message.await_args.kwargs["view"]
        fremd = make_interaction(ADMIN + 1)
        fremd.guild = server
        await next(k for k in view.children if k.label == "Übernehmen").callback(fremd)
        return fremd, await lr.zuordnung_von(SERVER)

    fremd, zuordnung = _lauf(ablauf())
    fremd.response.send_message.assert_awaited()
    assert zuordnung == {}


# --------------------------------------------------------------------------
# /level-rolle
# --------------------------------------------------------------------------
def test_einzelne_zuordnung_setzen_und_entfernen(testdb):
    async def ablauf():
        server = _server([])
        await level_admin.rolle_setzen(_interaktion(server), 30, _rolle(30_1, "Dora"))
        gesetzt = await lr.zuordnung_von(SERVER)
        await level_admin.rolle_setzen(_interaktion(server), 30, None)
        return gesetzt, await lr.zuordnung_von(SERVER)

    gesetzt, entfernt = _lauf(ablauf())
    assert gesetzt == {30: 301}
    assert entfernt == {}


def test_unbekannte_stufe_wird_abgelehnt(testdb):
    async def ablauf():
        it = _interaktion(_server([]))
        await level_admin.rolle_setzen(it, 7, _rolle(1, "X"))
        return _antwort(it), await lr.zuordnung_von(SERVER)

    text, zuordnung = _lauf(ablauf())
    assert "gibt es in der Liste nicht" in text
    assert zuordnung == {}


# --------------------------------------------------------------------------
# /level-kanal
# --------------------------------------------------------------------------
def test_kanal_ohne_rechte_wird_abgelehnt(testdb):
    async def ablauf():
        await db_modul.init_db()
        server = _server([])
        it = _interaktion(server)
        await level_admin.kanal_setzen(it, _kanal(server, senden=False))
        it2 = _interaktion(server)
        await level_admin.kanal_setzen(it2, _kanal(server, einbetten=False))
        return (_antwort(it),
                _antwort(it2),
                await lr.meldungs_kanal(SERVER))

    ohne_senden, ohne_einbetten, kanal = _lauf(ablauf())
    assert "keine Nachrichten senden" in ohne_senden
    assert "Links einbetten" in ohne_einbetten
    assert kanal == 0, "nichts gespeichert"


def test_kanal_wird_gespeichert_und_bekommt_eine_testnachricht(testdb):
    async def ablauf():
        await db_modul.init_db()
        server = _server([])
        kanal = _kanal(server)
        it = _interaktion(server)
        await level_admin.kanal_setzen(it, kanal)
        return kanal, _antwort(it), await lr.meldungs_kanal(SERVER)

    kanal, text, gespeichert = _lauf(ablauf())
    kanal.send.assert_awaited()
    assert gespeichert == 777
    assert "#level" in text


def test_fehlgeschlagene_testnachricht_wird_gemeldet(testdb):
    async def ablauf():
        await db_modul.init_db()
        server = _server([])
        it = _interaktion(server)
        await level_admin.kanal_setzen(it, _kanal(server, sendefehler=True))
        return _antwort(it), await lr.meldungs_kanal(SERVER)

    text, gespeichert = _lauf(ablauf())
    assert "Testnachricht ging nicht durch" in text
    assert gespeichert == 777


# --------------------------------------------------------------------------
# Schalter
# --------------------------------------------------------------------------
def test_level_ist_standardmaessig_aus(testdb):
    async def ablauf():
        await db_modul.init_db()
        aus = await lr.ist_aktiv(SERVER)
        await lr.setze_aktiv(SERVER, True)
        an = await lr.ist_aktiv(SERVER)
        await lr.setze_aktiv(SERVER, False)
        return aus, an, await lr.ist_aktiv(SERVER)

    assert _lauf(ablauf()) == (False, True, False)


# --------------------------------------------------------------------------
# /level-vorschau
# --------------------------------------------------------------------------
def _meldekanal():
    """Kanal fuer die Meldungen - hier zaehlt nur, dass gesendet wird."""
    return SimpleNamespace(id=777, send=AsyncMock())


SPIELER = 999_000_901
EINLADER = 999_000_902


def _mitglied(uid, rollen_ids, name="Tester"):
    return SimpleNamespace(id=uid, bot=False, display_name=name,
                           roles=[SimpleNamespace(id=r) for r in rollen_ids])


def _server_mit_mitgliedern(mitglieder, kanal=None):
    server = SimpleNamespace(id=SERVER, name="Testserver", roles=[], me=SimpleNamespace(id=1),
                             members=mitglieder)
    server.get_channel = lambda _cid: kanal
    server.get_member = lambda uid: next((m for m in mitglieder if m.id == uid), None)
    return server


async def _einladungen_eintragen(user_id, anzahl):
    async with db_modul.db_context() as db:
        await db.execute("INSERT INTO invite_stats (user_id, completed_invites) VALUES (?, ?) "
                         "ON CONFLICT(user_id) DO UPDATE SET completed_invites = excluded.completed_invites",
                         (int(user_id), int(anzahl)))
        await db.commit()


async def _vorbereiten(kanal=None):
    await db_modul.init_db()
    await lr.setze_zuordnung(SERVER, {5: 105, 15: 115})
    await _einladungen_eintragen(EINLADER, 6)
    server = _server_mit_mitgliedern(
        [_mitglied(SPIELER, [115], "Spieler"), _mitglied(EINLADER, [], "Einlader"),
         SimpleNamespace(id=3, bot=True, display_name="Bot", roles=[SimpleNamespace(id=115)])],
        kanal)
    if kanal is not None:
        await lr.setze_meldungs_kanal(SERVER, kanal.id)
    return server


def test_vorschau_rechnet_aber_aendert_nichts(testdb):
    async def ablauf():
        server = await _vorbereiten()
        daten = await level_admin.vorschau_sammeln(server)
        return (daten, await lr.erledigte_schluessel(SPIELER), await lr.erledigte_schluessel(EINLADER),
                await lr.ist_aktiv(SERVER))

    daten, spieler_erledigt, einlader_erledigt, aktiv = _lauf(ablauf())
    assert daten["spieler_level"] == 1 and daten["einlader"] == 1
    # Spieler: Black Widow (5) + Rocket (15). Einlader (6): Cap + Spider-Man + 4x Staub.
    assert daten["designs"] == 4
    assert daten["staub"] == 5 + 5 * 4          # Stufe 5 plus die Einladungen 2, 3, 4, 6
    assert spieler_erledigt == set() and einlader_erledigt == set(), "Vorschau vergibt nichts"
    assert aktiv is False, "und schaltet nichts ein"


def test_vorschau_text_nennt_jeden_eintrag(testdb):
    async def ablauf():
        server = await _vorbereiten()
        return level_admin.vorschau_text(await level_admin.vorschau_sammeln(server))

    text = _lauf(ablauf())
    assert "Spieler (999000901) — Level 15" in text
    assert "Einlader (999000902) — 6 Einladungen" in text
    assert "Design 2 von Rocket" in text


def test_vergeben_schaltet_ein_und_meldet_einmal(testdb):
    async def ablauf():
        kanal = _meldekanal()
        server = await _vorbereiten(kanal)
        daten = await level_admin.vorschau_sammeln(server)
        text = await level_admin.vergeben_ausfuehren(server, daten, pause=0)
        zweite = await level_admin.vergeben_ausfuehren(
            server, await level_admin.vorschau_sammeln(server), pause=0)
        return (kanal, text, zweite, await lr.ist_aktiv(SERVER),
                await designs.freigeschaltet(SPIELER, "Rocket"),
                await designs.freigeschaltet(EINLADER, "Captain America"))

    kanal, text, zweite, aktiv, rocket, cap = _lauf(ablauf())
    assert aktiv is True
    assert rocket == {1, 2} and cap == {1, 2}
    assert "**4** Designs" in text and "Level-System ist jetzt **an**" in text
    assert kanal.send.await_count == 1, "eine Zusammenfassung, keine Einzelmeldungen"
    assert "**0** Designs und **0** Infinitydust" in zweite, "beim zweiten Lauf gibt es nichts mehr"


def test_knopf_fragt_zweimal(testdb):
    async def ablauf():
        kanal = _meldekanal()
        server = await _vorbereiten(kanal)
        daten = await level_admin.vorschau_sammeln(server)
        view = level_admin.VorschauView(ADMIN, server, daten)
        knopf = next(k for k in view.children if "vergeben" in (k.label or "").lower())
        erste = _interaktion(server)
        await knopf.callback(erste)
        nach_erstem = await lr.ist_aktiv(SERVER)
        zweite = _interaktion(server)
        await knopf.callback(zweite)
        return erste, nach_erstem, await lr.ist_aktiv(SERVER)

    erste, nach_erstem, aktiv = _lauf(ablauf())
    assert "Noch einmal klicken" in erste.response.edit_message.await_args.kwargs["content"]
    assert nach_erstem is False, "der erste Klick vergibt noch nichts"
    assert aktiv is True


def test_abbrechen_laesst_alles_aus(testdb):
    async def ablauf():
        server = await _vorbereiten()
        daten = await level_admin.vorschau_sammeln(server)
        view = level_admin.VorschauView(ADMIN, server, daten)
        abbrechen = next(k for k in view.children if k.label == "Abbrechen")
        await abbrechen.callback(_interaktion(server))
        return await lr.ist_aktiv(SERVER), await lr.erledigte_schluessel(SPIELER)

    aktiv, erledigt = _lauf(ablauf())
    assert aktiv is False and erledigt == set()
