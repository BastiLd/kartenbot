"""Tests für die Zug-Mitschrift.

Zwei Dinge stehen hier über allem und werden entsprechend hart geprüft:
Ohne den Schalter passiert gar nichts, und ein Fehler beim Mitschreiben darf
einen laufenden Kampf niemals stören.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sqlite3
import time
from pathlib import Path

import pytest

from services import db as services_db
from services import move_log

ROOT = Path(__file__).resolve().parents[1]


class FakeView:
    """Ein Kampf, wie ihn die Mitschrift sieht — PvP-Benennung."""

    def __init__(self, spieler=111, gegner=222, runde=3):
        self.player1_id = spieler
        self.player2_id = gegner
        self.player1_card = {
            "name": "Iron-Man",
            "attacks": [
                {"name": "Repulsor", "damage": [12, 17], "is_standard_attack": True},
                {"name": "Unibeam", "damage": [30, 40], "cooldown_turns": 3,
                 "effects": [{"type": "stun"}]},
            ],
        }
        self.player2_card = {"name": "Hulk", "attacks": [{"name": "Smash", "damage": 20}]}
        self._hp_by_player = {spieler: 80, gegner: 45}
        self._max_hp_by_player = {spieler: 140, gegner: 200}
        self._card_names_by_player = {spieler: "Iron-Man", gegner: "Hulk"}
        self.active_effects = {spieler: [{"type": "burning", "turns": 2}], gegner: []}
        self.attack_cooldowns = {spieler: {1: 2}, gegner: {}}
        self.stunned_next_turn = {spieler: False, gegner: False}
        self.confused_next_turn = {spieler: False, gegner: False}
        self.round_counter = runde
        self.session_id = 4242
        self.session_kind = "fight_pvp"
        self._zug_begonnen_ts = time.monotonic() - 1.5


class FakeMissionView:
    """Dieselbe Sache in Missions-Benennung — dort heisst alles anders."""

    def __init__(self, spieler=333):
        self.user_id = spieler
        self.player_card = {"name": "Thor", "attacks": [{"name": "Hammer", "damage": 25}]}
        self.bot_card = {"name": "Maestro", "attacks": [{"name": "Wut", "damage": 40}]}
        self._hp_by_player = {spieler: 100, 0: 300}
        self._max_hp_by_player = {spieler: 140, 0: 300}
        self._card_names_by_player = {spieler: "Thor", 0: "Maestro"}
        self.active_effects = {spieler: [], 0: []}
        self._cooldowns_by_player = {spieler: {0: 1}, 0: {}}
        self.stunned_next_turn = {spieler: False, 0: False}
        self.confused_next_turn = {spieler: False, 0: False}
        self.round_counter = 1
        self.session_id = 77
        self.session_kind = "mission"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Eine Wegwerf-Datenbank, damit kein Test die echte anfasst."""
    pfad = tmp_path / "test.db"
    sqlite3.connect(str(pfad)).close()
    monkeypatch.setattr(services_db, "DB_PATH", str(pfad))
    monkeypatch.setattr(services_db, "_db", None)
    monkeypatch.setattr(move_log, "_schema_ready", False)
    yield pfad
    try:
        asyncio.run(services_db.close_db())
    except Exception:
        pass                      # Der Loop des Tests ist schon zu - egal
    services_db._db = None


@pytest.fixture
def db_wie_echt(db):
    """Wegwerf-Datenbank mit der Struktur der echten — aber ohne einen Datensatz.

    Ein echter Zug fasst nebenbei andere Tabellen an (Karten-Buffs zum
    Beispiel). Statt sie einzeln nachzubauen, wird die Struktur der echten
    Datenbank übernommen: nur die CREATE-Anweisungen, keine Daten, und die
    echte Datei wird ausschliesslich lesend geöffnet.
    """
    echte = ROOT / "kartenbot.db"
    if not echte.exists():
        pytest.skip("kartenbot.db nicht vorhanden - echter Kampf nicht nachstellbar")
    quelle = sqlite3.connect(f"file:{echte.as_posix()}?mode=ro", uri=True)
    ziel = sqlite3.connect(str(db))
    try:
        for (anweisung,) in quelle.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
            try:
                ziel.execute(anweisung)
            except sqlite3.OperationalError:
                pass                    # gibt es schon oder brauchen wir nicht
        ziel.commit()
    finally:
        quelle.close()
        ziel.close()
    return db


@pytest.fixture
def an(monkeypatch):
    """Schalter an, ohne dafür die Datenbank zu brauchen."""
    monkeypatch.setattr(move_log, "_schalter_wert", True)
    monkeypatch.setattr(move_log, "_schalter_geprueft", time.monotonic())


@pytest.fixture
def aus(monkeypatch):
    monkeypatch.setattr(move_log, "_schalter_wert", False)
    monkeypatch.setattr(move_log, "_schalter_geprueft", time.monotonic())


def _zeilen(pfad) -> list[dict]:
    con = sqlite3.connect(str(pfad))
    con.row_factory = sqlite3.Row
    try:
        return [dict(z) for z in con.execute("SELECT * FROM battle_moves ORDER BY id")]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# --------------------------------------------------------------------------
# Ohne Schalter passiert nichts
# --------------------------------------------------------------------------
def test_ohne_schalter_wird_nichts_gesammelt(aus):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)

    assert view._mitschrift_zug is None


def test_ohne_schalter_wird_nichts_geschrieben(db, aus):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    assert _zeilen(db) == []


def test_schalter_ist_im_zweifel_aus(db, monkeypatch):
    # Frischer Zustand, nichts in web_settings eingetragen.
    monkeypatch.setattr(move_log, "_schalter_geprueft", 0.0)

    assert asyncio.run(move_log.ist_an()) is False


def test_schalter_wird_aus_den_einstellungen_gelesen(db, monkeypatch):
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE web_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO web_settings VALUES (?, '1', '2026-08-06')", (move_log.SCHALTER,))
    con.commit()
    con.close()
    monkeypatch.setattr(move_log, "_schalter_geprueft", 0.0)

    assert asyncio.run(move_log.ist_an()) is True


# --------------------------------------------------------------------------
# Was mitgeschrieben wird
# --------------------------------------------------------------------------
def test_zug_landet_vollstaendig_in_der_datenbank(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 1)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    zeilen = _zeilen(db)
    assert len(zeilen) == 1
    z = zeilen[0]
    assert z["spieler_id"] == "111"
    assert z["ist_bot"] == 0
    assert z["karte"] == "Iron-Man"
    assert z["gegner_karte"] == "Hulk"
    assert (z["eigene_hp"], z["eigene_max_hp"]) == (80, 140)
    assert (z["gegner_hp"], z["gegner_max_hp"]) == (45, 200)
    assert z["angriff_index"] == 1
    assert z["angriff_name"] == "Unibeam"
    assert z["runde"] == 3
    assert z["session_id"] == 4242
    assert z["kampf_art"] == "fight_pvp"
    assert z["ausgang"] is None          # wird erst am Kampfende nachgetragen


def test_die_lage_enthaelt_was_zum_lernen_noetig_ist(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    lage = json.loads(_zeilen(db)[0]["lage_json"])
    assert lage["eigene_effekte"] == [{"typ": "burning", "turns": 2}]
    assert lage["gegner_effekte"] == []
    assert lage["abklingzeiten"] == {"1": 2}
    # Alle Angriffe, die zur Wahl standen - nicht nur der gewaehlte.
    assert [a["name"] for a in lage["angriffe"]] == ["Repulsor", "Unibeam"]
    unibeam = lage["angriffe"][1]
    assert unibeam["wartet_noch"] == 2          # war gesperrt
    assert unibeam["wirkungen"] == ["stun"]
    assert lage["angriffe"][0]["standard"] is True


def test_bedenkzeit_wird_gemessen(db, an):
    view = FakeView()
    view._zug_begonnen_ts = time.monotonic() - 2.0
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    bedenkzeit = _zeilen(db)[0]["bedenkzeit_ms"]
    assert 1800 <= bedenkzeit <= 2600


def test_ueber_nacht_offene_kaempfe_verfaelschen_die_bedenkzeit_nicht(db, an):
    view = FakeView()
    view._zug_begonnen_ts = time.monotonic() - 60 * 60 * 5      # fünf Stunden
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    assert _zeilen(db)[0]["bedenkzeit_ms"] is None


def test_missionen_werden_genauso_erfasst(db, an):
    """Dort heissen Karten und Abklingzeiten anders — das darf nichts ändern."""
    view = FakeMissionView()
    move_log.merke_lage(view, view.user_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    z = _zeilen(db)[0]
    assert z["karte"] == "Thor"
    assert z["gegner_karte"] == "Maestro"
    assert z["angriff_name"] == "Hammer"
    assert z["kampf_art"] == "mission"
    assert json.loads(z["lage_json"])["angriffe"][0]["name"] == "Hammer"


def test_bot_zuege_werden_als_solche_erkannt(db, an):
    view = FakeView(spieler=111, gegner=0)
    move_log.merke_lage(view, 0, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    assert _zeilen(db)[0]["ist_bot"] == 1


# --------------------------------------------------------------------------
# Nur echte Züge, und jeder nur einmal
# --------------------------------------------------------------------------
def test_abgelehnter_zug_wird_nie_geschrieben(db, an):
    """Der Sinn der Trennung: Was das Spiel ablehnt, ist keine Entscheidung."""
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 1)
    # ... hier bricht execute_attack ab (Abklingzeit), es wird nie geschrieben.
    move_log.merke_lage(view, view.player1_id, 0)          # zweiter Versuch
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    zeilen = _zeilen(db)
    assert len(zeilen) == 1
    assert zeilen[0]["angriff_index"] == 0


def test_derselbe_zug_wird_nicht_zweimal_geschrieben(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    assert len(_zeilen(db)) == 1


def test_ohne_gemerkten_zug_passiert_nichts(db, an):
    asyncio.run(move_log.schreibe_gemerkten_zug(FakeView()))

    assert _zeilen(db) == []


# --------------------------------------------------------------------------
# Der Ausgang wird nachgetragen
# --------------------------------------------------------------------------
def test_ausgang_wird_pro_seite_richtig_nachgetragen(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))
    move_log.merke_lage(view, view.player2_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    asyncio.run(move_log.setze_ausgang(4242, 111))

    ausgaenge = {z["spieler_id"]: z["ausgang"] for z in _zeilen(db)}
    assert ausgaenge == {"111": "gewonnen", "222": "verloren"}


def test_unentschieden_wird_festgehalten(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    asyncio.run(move_log.setze_ausgang(4242, None))

    assert _zeilen(db)[0]["ausgang"] == "unentschieden"


def test_ausgang_eines_anderen_kampfes_bleibt_unberuehrt(db, an):
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    asyncio.run(move_log.setze_ausgang(9999, 111))          # anderer Kampf

    assert _zeilen(db)[0]["ausgang"] is None


def test_abgebrochene_kaempfe_bleiben_ohne_ausgang(db, an):
    """Ein Kampf, der nie zu Ende gespielt wurde, darf das Lernen nicht verfälschen."""
    view = FakeView()
    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))

    zaehlung = asyncio.run(move_log.zaehle())
    assert zaehlung == {"zuege": 1, "kaempfe": 1, "verwertbar": 0}

    asyncio.run(move_log.setze_ausgang(4242, 111))
    assert asyncio.run(move_log.zaehle())["verwertbar"] == 1


# --------------------------------------------------------------------------
# Der Kampf darf davon nichts merken
# --------------------------------------------------------------------------
def test_kaputter_kampf_bringt_das_mitschreiben_nicht_zum_absturz(an):
    class Kaputt:
        @property
        def _hp_by_player(self):
            raise RuntimeError("kaputt")

    kaputt = Kaputt()
    move_log.merke_lage(kaputt, 1, 0)      # darf nicht werfen
    assert getattr(kaputt, "_mitschrift_zug", None) is None


def test_fehlende_datenbank_stoert_den_kampf_nicht(an, monkeypatch, tmp_path):
    monkeypatch.setattr(services_db, "DB_PATH", str(tmp_path / "gibt-es-nicht" / "x.db"))
    monkeypatch.setattr(services_db, "_db", None)
    monkeypatch.setattr(move_log, "_schema_ready", False)
    view = FakeView()

    move_log.merke_lage(view, view.player1_id, 0)
    asyncio.run(move_log.schreibe_gemerkten_zug(view))      # darf nicht werfen
    asyncio.run(move_log.setze_ausgang(4242, 111))          # ebenso wenig


def test_ohne_sitzung_kein_ausgang(db, an):
    asyncio.run(move_log.setze_ausgang(None, 111))          # darf nicht werfen

    assert _zeilen(db) == []


# --------------------------------------------------------------------------
# Am echten Kampf, nicht nur an Attrappen
# --------------------------------------------------------------------------
def test_echter_pvp_zug_landet_in_der_mitschrift(db_wie_echt, an):
    """Der eigentliche Beweis: ein echter Zug durch ``execute_attack``.

    Die Attrappen oben prüfen die Logik, aber nicht, ob die Felder im echten
    ``BattleView`` auch so heissen. Genau daran wäre es sonst gescheitert.
    """
    import random as _random

    from tests.view_harness import make_battle_view, make_interaction, run_view_coro

    view = make_battle_view(p1_id=111, p2_id=222)
    view.session_id = 5150
    _random.seed(42)
    run_view_coro(lambda: view.execute_attack(make_interaction(111), 0))

    zeilen = _zeilen(db_wie_echt)
    assert len(zeilen) == 1, "genau ein Zug muss mitgeschrieben sein"
    z = zeilen[0]
    assert z["spieler_id"] == "111"
    assert z["karte"], "der Kartenname muss ankommen"
    assert z["gegner_karte"]
    assert z["angriff_index"] == 0
    assert z["angriff_name"], "der Name des gewaehlten Angriffs muss ankommen"
    assert z["eigene_hp"] > 0 and z["gegner_max_hp"] > 0
    assert z["session_id"] == 5150
    # Die Lage muss die Angriffe enthalten, die zur Wahl standen.
    assert json.loads(z["lage_json"])["angriffe"]


def test_abgelehnter_zug_im_echten_kampf_wird_nicht_mitgeschrieben(db_wie_echt, an):
    """Wer nicht am Zug ist, trifft keine Entscheidung — also auch keine Zeile."""
    import random as _random

    from tests.view_harness import make_battle_view, make_interaction, run_view_coro

    view = make_battle_view(p1_id=111, p2_id=222)
    _random.seed(1)
    run_view_coro(lambda: view.execute_attack(make_interaction(222), 0))

    assert _zeilen(db_wie_echt) == []


def test_echter_zug_misst_die_bedenkzeit(db_wie_echt, an):
    import random as _random

    from tests.view_harness import make_battle_view, make_interaction, run_view_coro

    view = make_battle_view(p1_id=111, p2_id=222)
    # So, als waere der Spieler vor einer Sekunde an die Reihe gekommen.
    view._zug_begonnen_ts = time.monotonic() - 1.0
    _random.seed(42)
    run_view_coro(lambda: view.execute_attack(make_interaction(111), 0))

    assert _zeilen(db_wie_echt)[0]["bedenkzeit_ms"] >= 900


def test_zugwechsel_stellt_die_uhr_fuer_den_naechsten(db_wie_echt, an):
    """Der Property-Setter ist die Quelle der Bedenkzeit im PvP."""
    import random as _random

    from tests.view_harness import make_battle_view, make_interaction, run_view_coro

    view = make_battle_view(p1_id=111, p2_id=222)
    _random.seed(42)
    run_view_coro(lambda: view.execute_attack(make_interaction(111), 0))

    assert view.current_turn == 222, "der Zug muss gewechselt haben"
    # Nach dem Wechsel laeuft die Uhr des naechsten Spielers frisch.
    assert time.monotonic() - view._zug_begonnen_ts < 5


def test_ohne_schalter_bleibt_der_echte_kampf_unberuehrt(db_wie_echt, aus):
    import random as _random

    from tests.view_harness import make_battle_view, make_interaction, run_view_coro

    view = make_battle_view(p1_id=111, p2_id=222)
    vorher = view.player2_hp
    _random.seed(42)
    run_view_coro(lambda: view.execute_attack(make_interaction(111), 0))

    assert view.player2_hp < vorher, "der Kampf muss ganz normal laufen"
    assert view.current_turn == 222
    assert _zeilen(db_wie_echt) == []


# --------------------------------------------------------------------------
# Verdrahtung im Bot
# --------------------------------------------------------------------------
def _bot_quelle() -> str:
    return (ROOT / "bot.py").read_text(encoding="utf-8")


def test_current_turn_merkt_sich_den_zeitpunkt():
    """Ohne diesen Zeitstempel gäbe es keine Bedenkzeit.

    Der Setter ist der Grund, warum kein einziger der vielen Zugwechsel im
    Bot angefasst werden musste.
    """
    quelle = _bot_quelle()
    assert "@current_turn.setter" in quelle
    assert "_zug_begonnen_ts = time.monotonic()" in quelle


def test_alle_drei_kampfarten_schreiben_mit():
    quelle = _bot_quelle()
    assert quelle.count("move_log.merke_lage(") == 3, \
        "PvP, Bot-Zug und Mission muessen alle drei merken"
    assert quelle.count("move_log.schreibe_gemerkten_zug(") == 3
    assert quelle.count("move_log.setze_ausgang(") == 3


def test_gemerkt_wird_vor_geschrieben():
    """Sonst stünde in der Zeile die Lage nach dem Zug statt davor."""
    quelle = _bot_quelle()
    assert quelle.index("move_log.merke_lage(") < quelle.index("move_log.schreibe_gemerkten_zug(")


def test_tabelle_steht_auf_beiden_seiten_gleich():
    """Die Website legt die Tabellen an, der Bot notfalls auch."""
    pfad = ROOT / "web" / "app" / "schema.py"
    spec = importlib.util.spec_from_file_location("_web_schema2", pfad)
    web_schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_schema)

    def spalten(anweisungen):
        for a in anweisungen:
            treffer = re.search(r"CREATE TABLE IF NOT EXISTS battle_moves\s*\((.*)\)\s*$",
                                a.strip(), re.DOTALL)
            if treffer:
                return [z.strip().split()[0] for z in treffer.group(1).strip().splitlines()
                        if z.strip()]
        return []

    web = spalten(web_schema._TABLES)
    bot = spalten(move_log._SCHEMA)

    assert web, "battle_moves fehlt in web/app/schema.py"
    assert bot, "battle_moves fehlt in services/move_log.py"
    assert web == bot


def test_schalter_ist_in_den_einstellungen_beschrieben():
    """Nichts wird still gesammelt — der Schalter muss auf der Seite auftauchen."""
    quelle = (ROOT / "web" / "app" / "settings.py").read_text(encoding="utf-8")

    assert f'"{move_log.SCHALTER}"' in quelle
    # Voreinstellung aus: erst einschalten, dann wird gesammelt.
    block = re.search(rf'"{move_log.SCHALTER}":\s*\(\s*"([^"]*)"', quelle)
    assert block and block.group(1) == "0"
