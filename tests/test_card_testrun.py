"""Tests für den Testlauf — eine Karte gegen alle anderen.

Gerechnet wird mit echten Karten und der echten Engine, aber mit sehr wenigen
Kämpfen: Es geht um die Klammer drumherum (Zählen, Fortschritt, Abbruch,
Reproduzierbarkeit), nicht um die Kampfregeln — die haben ihre eigenen Tests.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import inspect
import random
import re
from pathlib import Path

import pytest

from services import card_testrun, web_jobs
from simulation.config import DEFAULT_AVERAGE_MISTAKE_RATE
from simulation.engine import simulate_matchup
from simulation.loader import canonical_hero_name, load_base_runtime_cards

ROOT = Path(__file__).resolve().parents[1]


def _karten(anzahl: int = 3) -> list[dict]:
    return load_base_runtime_cards()[:anzahl]


def _lauf(name: str, **kwargs):
    """Einen Testlauf durchrechnen. Wenige Kämpfe, damit es schnell geht."""
    kwargs.setdefault("kaempfe_je_paarung", 2)
    kwargs.setdefault("seed", 7)
    if "karten" not in kwargs:
        kwargs["karten"] = _karten()
    return asyncio.run(card_testrun.laufen(name, **kwargs))


# --------------------------------------------------------------------------
# Das Ergebnis muss in sich stimmen
# --------------------------------------------------------------------------
def test_karte_tritt_gegen_alle_anderen_an():
    karten = _karten()
    name = canonical_hero_name(karten[0])
    ergebnis = _lauf(name, karten=karten)

    assert ergebnis["karte"] == name
    assert ergebnis["gegner"] == len(karten) - 1
    # Gegen sich selbst tritt niemand an.
    assert name not in {p["gegner"] for p in ergebnis["paarungen"]}


def test_zahlen_gehen_auf():
    ergebnis = _lauf(canonical_hero_name(_karten()[0]))

    assert ergebnis["siege"] + ergebnis["niederlagen"] + ergebnis["unentschieden"] \
        == ergebnis["kaempfe"]
    assert ergebnis["kaempfe"] == ergebnis["gegner"] * ergebnis["kaempfe_je_paarung"]
    for paarung in ergebnis["paarungen"]:
        assert paarung["siege"] + paarung["niederlagen"] + paarung["unentschieden"] \
            == paarung["kaempfe"]
    # Die Summe der Einzelpaarungen muss die Gesamtzahl ergeben.
    assert sum(p["siege"] for p in ergebnis["paarungen"]) == ergebnis["siege"]


def test_paarungen_stehen_schwerste_zuerst():
    karten = _karten(5)
    ergebnis = _lauf(canonical_hero_name(karten[0]), karten=karten, kaempfe_je_paarung=4)
    quoten = [p["siegquote"] for p in ergebnis["paarungen"]]

    assert quoten == sorted(quoten)


def test_ungerade_kampfzahl_wird_aufgerundet():
    # Wer anfaengt, hat einen Vorteil. Bei ungerader Zahl bekaeme ihn eine
    # Seite oefter - deshalb wird auf gerade aufgerundet.
    ergebnis = _lauf(canonical_hero_name(_karten()[0]), kaempfe_je_paarung=3)

    assert ergebnis["kaempfe_je_paarung"] == 4


def test_gleicher_startwert_gibt_gleiches_ergebnis():
    name = canonical_hero_name(_karten()[0])

    assert _lauf(name, seed=42)["paarungen"] == _lauf(name, seed=42)["paarungen"]


def test_ohne_startwert_wird_einer_gewuerfelt_und_gemerkt():
    ergebnis = _lauf(canonical_hero_name(_karten()[0]), seed=None)

    assert isinstance(ergebnis["seed"], int)


def test_ergibt_dasselbe_wie_die_engine_selbst():
    """Beweist, dass die Engine hier genauso benutzt wird wie in ihr selbst.

    ``simulate_matchup`` rechnet eine Paarung am Stück. Hier werden die Duelle
    einzeln aufgerufen, damit zwischendurch abgegeben werden kann — das darf
    am Ergebnis nichts ändern. Bei gleichem Startwert muss deshalb Zahl für
    Zahl dasselbe herauskommen.
    """
    karten = _karten(2)
    ergebnis = _lauf(canonical_hero_name(karten[0]), karten=karten,
                     kaempfe_je_paarung=10, seed=99)
    vergleich = simulate_matchup(karten[0], karten[1], 10, rng=random.Random(99),
                                 playstyle="optimal",
                                 average_mistake_rate=DEFAULT_AVERAGE_MISTAKE_RATE)
    paarung = ergebnis["paarungen"][0]

    assert (paarung["siege"], paarung["niederlagen"], paarung["unentschieden"]) \
        == (vergleich.wins_a, vergleich.wins_b, vergleich.draws)


# --------------------------------------------------------------------------
# Der laufende Kampf im Spiel darf nichts davon merken
# --------------------------------------------------------------------------
def test_globaler_zufall_bleibt_unberuehrt():
    """Die wichtigste Zusicherung des Moduls.

    ``simulate_duel`` setzt den globalen Zufallsgenerator auf einen festen
    Startwert. Bliebe der stehen, bekaeme ein echter Kampf im Spiel waehrend
    des Testlaufs vorhersagbare Wuerfel zu sehen.
    """
    random.seed(12345)
    vorher = random.getstate()

    _lauf(canonical_hero_name(_karten()[0]))

    assert random.getstate() == vorher


def test_karten_werden_nicht_veraendert():
    """Testläufe rechnen auf Kopien — die Karten selbst bleiben, wie sie sind."""
    karten = _karten()
    vorher = copy.deepcopy(karten)

    _lauf(canonical_hero_name(karten[0]), karten=karten)

    assert karten == vorher


# --------------------------------------------------------------------------
# Fortschritt und Abbruch
# --------------------------------------------------------------------------
def test_fortschritt_wird_gemeldet():
    gemeldet = []

    async def fortschritt(erledigt, gesamt, stufe=None):
        gemeldet.append((erledigt, gesamt, stufe))

    karten = _karten()
    _lauf(canonical_hero_name(karten[0]), karten=karten, progress=fortschritt)

    assert gemeldet, "es muss mindestens einmal Fortschritt gemeldet werden"
    # Gesamtzahl ist immer die Zahl der Gegner, und am Ende ist alles erledigt.
    assert all(gesamt == len(karten) - 1 for _, gesamt, _ in gemeldet)
    assert gemeldet[-1][0] == len(karten) - 1
    assert gemeldet[-1][2] == "fertig"


def test_abbruch_beendet_den_lauf_vorzeitig():
    aufrufe = []

    async def abbrechen():
        aufrufe.append(1)
        return len(aufrufe) > 1        # die erste Paarung noch, dann Schluss

    karten = _karten(4)
    ergebnis = _lauf(canonical_hero_name(karten[0]), karten=karten, cancelled=abbrechen)

    assert ergebnis["abgebrochen"] is True
    assert ergebnis["gegner"] == 1
    assert len(ergebnis["paarungen"]) == 1


def test_abbruch_zaehlt_nur_fertige_paarungen():
    """Eine angefangene Paarung hat weniger Kämpfe und würde das Bild verzerren."""
    async def sofort():
        return True

    ergebnis = _lauf(canonical_hero_name(_karten()[0]), cancelled=sofort)

    assert ergebnis["abgebrochen"] is True
    assert ergebnis["paarungen"] == []
    assert ergebnis["kaempfe"] == 0
    assert ergebnis["siegquote"] == 0.0


def test_abbruch_greift_auch_mitten_in_einer_paarung(monkeypatch):
    # Ohne das liefe eine einzelne Paarung bis zum Ende weiter - bei 500
    # Kaempfen sind das etliche Sekunden, in denen nichts auf "Abbrechen"
    # reagiert.
    monkeypatch.setattr(card_testrun, "ABBRUCH_ALLE", 2)
    aufrufe = []

    async def abbrechen():
        aufrufe.append(1)
        return len(aufrufe) > 1

    ergebnis = _lauf(canonical_hero_name(_karten()[0]), kaempfe_je_paarung=10,
                     cancelled=abbrechen)

    assert ergebnis["abgebrochen"] is True
    # Die Pruefung mitten in der Paarung hat gegriffen, nicht erst die vor dem
    # naechsten Gegner - sonst waere die erste Paarung fertig geworden.
    assert ergebnis["paarungen"] == []


# --------------------------------------------------------------------------
# Eingaben, die nicht gehen
# --------------------------------------------------------------------------
def test_unbekannte_karte_wird_abgelehnt():
    with pytest.raises(card_testrun.TestlaufFehler, match="nicht bekannt"):
        _lauf("Gibt es nicht")


def test_leerer_name_wird_abgelehnt():
    with pytest.raises(card_testrun.TestlaufFehler):
        _lauf("   ")


def test_unbekannte_spielweise_wird_abgelehnt():
    with pytest.raises(card_testrun.TestlaufFehler, match="Spielweise"):
        _lauf(canonical_hero_name(_karten()[0]), spielweise="zufaellig")


@pytest.mark.parametrize("kaempfe", [0, 1, 1001])
def test_unsinnige_kampfzahl_wird_abgelehnt(kaempfe):
    with pytest.raises(card_testrun.TestlaufFehler, match="Kämpfe"):
        _lauf(canonical_hero_name(_karten()[0]), kaempfe_je_paarung=kaempfe)


def test_zu_wenige_karten_werden_abgelehnt():
    with pytest.raises(card_testrun.TestlaufFehler, match="zu wenige"):
        _lauf("egal", karten=_karten(1))


# --------------------------------------------------------------------------
# Einordnung in Worten
# --------------------------------------------------------------------------
@pytest.mark.parametrize("quote,stufe", [
    (95.0, "zu stark"), (65.0, "zu stark"),
    (60.0, "etwas stark"), (57.0, "etwas stark"),
    (50.0, "rund"), (43.0, "rund"),
    (40.0, "etwas schwach"), (35.0, "etwas schwach"),
    (10.0, "zu schwach"),
])
def test_einordnung_trifft_die_stufen(quote, stufe):
    assert card_testrun.einordnen(quote, [])["stufe"] == stufe


def test_einordnung_hat_immer_eine_begruendung():
    for quote in (0.0, 25.0, 50.0, 75.0, 100.0):
        ordnung = card_testrun.einordnen(quote, [])
        assert ordnung["text"].strip()
        assert ordnung["art"] in ("ok", "warn", "bad")


def test_polarisierende_karte_wird_erwaehnt():
    # Im Schnitt rund, aber jede Paarung ist vorher entschieden.
    paarungen = [{"siegquote": 100.0}] * 5 + [{"siegquote": 0.0}] * 5
    ordnung = card_testrun.einordnen(50.0, paarungen)

    assert ordnung["stufe"] == "rund"
    assert "entschieden" in ordnung["text"]


def test_ausgeglichenes_feld_wird_nicht_als_auffaellig_gemeldet():
    paarungen = [{"siegquote": 50.0}] * 10

    assert "entschieden" not in card_testrun.einordnen(50.0, paarungen)["text"]


# --------------------------------------------------------------------------
# Verdrahtung: was auf beiden Seiten zusammenpassen muss
# --------------------------------------------------------------------------
def _quelle(*teile: str) -> str:
    return (ROOT.joinpath(*teile)).read_text(encoding="utf-8")


def _spalten(anweisungen, tabelle: str) -> list[str]:
    """Die Spaltennamen aus einer CREATE-TABLE-Anweisung ziehen."""
    for anweisung in anweisungen:
        treffer = re.search(rf"CREATE TABLE IF NOT EXISTS {tabelle}\s*\((.*)\)\s*$",
                            anweisung.strip(), re.DOTALL)
        if treffer:
            return [zeile.strip().split()[0]
                    for zeile in treffer.group(1).strip().splitlines()
                    if zeile.strip()]
    return []


def test_auftragsart_ist_der_website_bekannt():
    block = re.search(r"^KINDS = \{.*?^\}", _quelle("web", "app", "jobs.py"),
                      re.DOTALL | re.MULTILINE)

    assert block, "KINDS wurde in web/app/jobs.py nicht gefunden"
    assert "cards.testlauf" in block.group(0)


def test_bot_arbeitet_die_auftragsart_auch_ab():
    """Sonst legt die Website Aufträge an, die der Bot als unbekannt ablehnt."""
    assert 'art == "cards.testlauf"' in _quelle("bot.py")


def test_tabelle_steht_auf_beiden_seiten_gleich():
    """Die klassische Falle: Tabelle nur in einer der beiden Dateien geändert.

    Die Website legt die Tabellen an, der Bot notfalls auch. Laufen die
    Anweisungen auseinander, fehlt je nach Startreihenfolge eine Spalte.
    """
    pfad = ROOT / "web" / "app" / "schema.py"
    spec = importlib.util.spec_from_file_location("_web_schema", pfad)
    web_schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_schema)

    web = _spalten(web_schema._TABLES, "card_testruns")
    bot = _spalten(web_jobs._SCHEMA, "card_testruns")

    assert web, "card_testruns fehlt in web/app/schema.py"
    assert bot, "card_testruns fehlt in services/web_jobs.py"
    assert web == bot


def test_bot_raeumt_haengengebliebene_testlaeufe_auf():
    """Nach einem Neustart darf kein Lauf für immer auf „läuft“ stehen."""
    assert "card_testruns" in inspect.getsource(web_jobs.reset_orphaned)
