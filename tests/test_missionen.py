"""Tests für den Schurken-Bereich: Missionsgegner und ihr Testlauf.

Missionsgegner sind ganz normale Karten und kämpfen mit derselben Engine.
Der Unterschied liegt in der Erwartung: Ein Boss *soll* gewinnen, ein Gegner
der ersten Welle *soll* fallen.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from services import card_testrun

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "web") not in sys.path:
    sys.path.insert(0, str(ROOT / "web"))


# --------------------------------------------------------------------------
# Die Gegner selbst
# --------------------------------------------------------------------------
def test_alle_missionsgegner_werden_gefunden():
    gegner = card_testrun.missionsgegner()

    # Fuenf Operationen mit je drei kleinen Gegnern und einem Boss.
    assert len(gegner) == 20
    assert all(g.get("attacks") for g in gegner), "jeder Gegner braucht Angriffe"
    assert all(g.get("hp") for g in gegner)


def test_gegner_kommen_nur_einmal_vor():
    namen = [g["name"] for g in card_testrun.missionsgegner()]

    assert len(namen) == len(set(namen))


@pytest.mark.parametrize("name,rolle", [
    ("Maestro", "boss"),
    ("Kingpin", "boss"),
    ("M.O.D.O.K.", "boss"),
    ("Ödland-Plünderer", "klein"),
    ("Gamma-Mutant", "klein"),
    ("Iron-Man", "held"),
    ("Gibt es nicht", "held"),
])
def test_rolle_wird_richtig_erkannt(name, rolle):
    """Der Boss ist immer der letzte seiner Operation — nicht der stärkste."""
    assert card_testrun.rolle_von(name) == rolle


# --------------------------------------------------------------------------
# Die Erwartung hängt an der Rolle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rolle,quote,stufe", [
    # Ein Boss mit 75 % ist genau richtig - derselbe Wert waere fuer einen
    # Helden viel zu stark und fuer einen kleinen Gegner absurd.
    ("boss", 75.0, "rund"),
    ("held", 75.0, "zu stark"),
    ("klein", 75.0, "zu stark"),
    # Und andersherum: 25 % sind fuer einen kleinen Gegner in Ordnung.
    ("klein", 25.0, "rund"),
    ("held", 25.0, "zu schwach"),
    ("boss", 25.0, "zu schwach"),
    # Wer nie gewinnt, ist auch als kleiner Gegner zu schwach.
    ("klein", 0.0, "zu schwach"),
    # Ein Boss, den niemand schlaegt, ist zu stark.
    ("boss", 95.0, "zu stark"),
])
def test_einordnung_richtet_sich_nach_der_rolle(rolle, quote, stufe):
    assert card_testrun.einordnen(quote, [], rolle)["stufe"] == stufe


def test_einordnung_nennt_die_erwartung():
    ordnung = card_testrun.einordnen(75.0, [], "boss")

    assert ordnung["rolle"] == "boss"
    assert ordnung["erwartet_von"] == 55.0
    assert ordnung["erwartet_bis"] == 80.0


def test_texte_sprechen_die_rolle_an():
    boss = card_testrun.einordnen(20.0, [], "boss")["text"]
    klein = card_testrun.einordnen(90.0, [], "klein")["text"]

    assert "Boss" in boss
    assert "Wellen" in klein or "Mission" in klein


def test_ohne_rollenangabe_gilt_die_heldenerwartung():
    """Damit bestehende Aufrufe unverändert weiterlaufen."""
    assert card_testrun.einordnen(50.0, []) == card_testrun.einordnen(50.0, [], "held")


# --------------------------------------------------------------------------
# Der Testlauf eines Schurken
# --------------------------------------------------------------------------
def test_schurke_tritt_gegen_alle_helden_an():
    """Ein Missionsgegner ist selbst keine Heldenkarte — also fällt keine weg."""
    from simulation.loader import load_base_runtime_cards

    helden = load_base_runtime_cards()
    ergebnis = asyncio.run(card_testrun.laufen(
        "Maestro", kaempfe_je_paarung=2, seed=3, karten=helden))

    assert ergebnis["karte"] == "Maestro"
    assert ergebnis["rolle"] == "boss"
    assert ergebnis["gegner"] == len(helden), "gegen jeden Helden, keiner faellt weg"
    assert "Maestro" not in {p["gegner"] for p in ergebnis["paarungen"]}


def test_held_tritt_gegen_alle_anderen_an():
    """Zur Gegenprobe: Bei einem Helden fällt er selbst aus der Liste."""
    from simulation.loader import load_base_runtime_cards

    helden = load_base_runtime_cards()
    ergebnis = asyncio.run(card_testrun.laufen(
        "Iron-Man", kaempfe_je_paarung=2, seed=3, karten=helden))

    assert ergebnis["rolle"] == "held"
    assert ergebnis["gegner"] == len(helden) - 1


def test_der_boss_wird_nach_bossmassstab_beurteilt():
    ergebnis = asyncio.run(card_testrun.laufen_mehrfach(
        "Maestro", kaempfe_je_paarung=2, auswahl="optimal", seed=3))

    assert ergebnis["rolle"] == "boss"
    assert ergebnis["durchgaenge"][0]["einordnung"]["rolle"] == "boss"


# --------------------------------------------------------------------------
# Die Aufbereitung für die Oberfläche
# --------------------------------------------------------------------------
@pytest.fixture
def missionen():
    try:
        from app import missionen as modul
    except Exception as fehler:                                # noqa: BLE001
        pytest.skip(f"web/app nicht importierbar: {fehler}")
    return modul


def test_katalog_hat_jede_operation_mit_drei_kleinen_und_einem_boss(missionen):
    katalog = missionen.katalog()

    assert len(katalog) == 20
    for schluessel in missionen.OPERATIONEN:
        gegner = [g for g in katalog if g["operation"] == schluessel]
        assert len(gegner) == 4, f"{schluessel} muss 4 Gegner haben"
        assert sum(1 for g in gegner if g["rolle"] == "boss") == 1
        assert sum(1 for g in gegner if g["rolle"] == "klein") == 3


def test_der_boss_ist_der_letzte_der_operation(missionen):
    for gegner in missionen.katalog():
        if gegner["rolle"] == "boss":
            assert gegner["position"] == 4


def test_bilder_werden_ueber_die_nummer_zugeordnet(missionen):
    """Die Namen in den Dateien stimmen teils nicht — die Nummer schon.

    „Korrupte SWAT-Einheit (King Pin 3)" zeigt in Wahrheit Fisks rechte Hand.
    Wer nach Namen zuordnet, bekommt Unsinn.
    """
    katalog = missionen.katalog()

    ohne_bild = [g["name"] for g in katalog if not g["bild_aus_datei"]]
    assert not ohne_bild, f"ohne zugeordnetes Bild: {ohne_bild}"
    for gegner in katalog:
        # Die Nummer im Pfad muss zur Position im Code passen.
        assert f"/{gegner['position']}-" in gegner["bild"], gegner["bild"]
        assert gegner["bild"].startswith("missionen/"), "Pfad muss relativ sein"


def test_operationen_nennen_ihren_boss(missionen):
    bosse = {o["name"]: o["boss"] for o in missionen.operationen()}

    assert bosse["Broken Timeline"] == "Maestro"
    assert bosse["Goldener Käfig"] == "Kingpin"
    assert bosse["Technischer Kollaps"] == "M.O.D.O.K."
    assert all(o["anzahl"] == 4 for o in missionen.operationen())


def test_namen_deckt_sich_mit_dem_katalog(missionen):
    """Die Website prüft damit, ob ein Testlauf zulässig ist."""
    assert missionen.namen() == {g["name"] for g in missionen.katalog()}
    assert "Maestro" in missionen.namen()
