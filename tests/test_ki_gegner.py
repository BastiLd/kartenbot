"""Tests für die KI als Gegner (Stufe 5, Schritt 10).

Das Wichtigste ist nicht, dass das Modell gut spielt — das kann es oder
nicht. Das Wichtigste ist, dass **ein Kontrollkampf immer endet**: Egal ob
das Modell schweigt, Unsinn schreibt, abstürzt oder ewig braucht, der Kampf
läuft zu Ende und im Protokoll steht, was passiert ist.

Kein Test hier spricht mit Ollama. Die Anfrage wird hereingereicht — genau
dafür ist sie ein Parameter und kein fest verdrahteter Aufruf.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import ki_gegner
from simulation.loader import load_base_runtime_cards

ROOT = Path(__file__).resolve().parents[1]


def _antwort(was):
    """Ein Modell, das immer dasselbe sagt — oder das abstürzt."""
    async def frage(prompt):
        return was(prompt) if callable(was) else was
    return frage


def _kampf(a, b, antwort, **rest):
    """Einen Kontrollkampf laufen lassen. ``antwort`` ist die Modellantwort."""
    return asyncio.run(ki_gegner.kontrollkampf(
        a, b, frage=_antwort(antwort), seed=7, **rest))


# --------------------------------------------------------------------------
# Die Antwort lesen — kleine Modelle halten sich selten ans Format
# --------------------------------------------------------------------------
@pytest.mark.parametrize("antwort,erwartet", [
    ("2", 2),
    ("Antwort: 3", 3),
    ("Ich wähle 1)", 1),
    ("  4  ", 4),
    ("Nummer 2 — sie macht mehr Schaden als 3.", 2),
])
def test_die_nummer_wird_aus_der_antwort_gelesen(antwort, erwartet):
    assert ki_gegner.antwort_lesen(antwort, 4) == erwartet


@pytest.mark.parametrize("antwort", [
    "", "   ", None, "keine Ahnung", "9", "0",
    "Ich kann diese Frage nicht beantworten.",
])
def test_unbrauchbare_antworten_geben_nichts_zurueck(antwort):
    assert ki_gegner.antwort_lesen(antwort, 4) is None


def test_eine_zahl_ausserhalb_des_bereichs_wird_uebergangen():
    """"Angriff 7 gibt es nicht" - dann zaehlt die naechste gueltige Zahl."""
    assert ki_gegner.antwort_lesen("7 geht nicht, also 2", 4) == 2


def test_sehr_lange_antworten_werden_nicht_ganz_gelesen():
    text = "x" * (ki_gegner.MAX_ANTWORT + 50) + "3"

    assert ki_gegner.antwort_lesen(text, 4) is None


# --------------------------------------------------------------------------
# Die Frage
# --------------------------------------------------------------------------
def _lage(anzahl: int = 3) -> dict:
    return {
        "eigene_karte": "Hulk", "gegner_karte": "Loki",
        "eigene_hp": 80, "eigene_max_hp": 140,
        "gegner_hp": 30, "gegner_max_hp": 140,
        "gegner_unsichtbar": False,
        "angriffe": [
            {"nummer": i + 1, "index": i, "name": f"Angriff {i}",
             "schaden_von": 10 * i, "schaden_bis": 20 * i,
             "wirkungen": ["stun"] if i == 1 else []}
            for i in range(anzahl)
        ],
    }


def test_die_frage_nennt_lage_angriffe_und_das_gewuenschte_format():
    text = ki_gegner.frage_bauen(_lage())

    assert "Hulk" in text and "Loki" in text
    assert "80 von 140" in text
    assert "1. Angriff 0" in text
    assert "nur mit der Nummer (1 bis 3)" in text


def test_die_angriffe_sind_ab_eins_nummeriert():
    """Eine Liste, die bei 0 anfaengt, verwirrt kleine Modelle zuverlaessig."""
    text = ki_gegner.frage_bauen(_lage())

    assert "\n   1. " in text
    assert "\n   0. " not in text


def test_unsichtbarkeit_steht_in_der_frage():
    lage = _lage()
    lage["gegner_unsichtbar"] = True

    assert "unsichtbar" in ki_gegner.frage_bauen(lage)


def test_angriffe_ohne_schaden_werden_als_solche_benannt():
    lage = _lage(1)
    lage["angriffe"][0].update({"schaden_von": 0, "schaden_bis": 0})

    assert "kein Schaden" in ki_gegner.frage_bauen(lage)


# --------------------------------------------------------------------------
# Der Kampf endet immer — das ist die eigentliche Zusage
# --------------------------------------------------------------------------
def _zwei_karten() -> tuple[dict, dict]:
    karten = load_base_runtime_cards()
    if len(karten) < 2:
        pytest.skip("zu wenige Karten fuer einen Kontrollkampf")
    return karten[0], karten[1]


def test_ein_kampf_mit_brauchbaren_antworten_geht_zu_ende():
    a, b = _zwei_karten()

    ergebnis = _kampf(a, b, "1")

    assert ergebnis["runden"] > 0
    assert ergebnis["gefragt"] >= 0
    assert ergebnis["ausgewichen"] == 0
    assert len(ergebnis["protokoll"]) == ergebnis["gefragt"]


def test_ein_schweigendes_modell_haelt_den_kampf_nicht_auf():
    a, b = _zwei_karten()

    ergebnis = _kampf(a, b, "")

    assert ergebnis["runden"] > 0
    assert ergebnis["ausgewichen"] == ergebnis["gefragt"]
    assert all(z["ausgewichen"] for z in ergebnis["protokoll"])


def test_ein_abstuerzendes_modell_haelt_den_kampf_nicht_auf():
    a, b = _zwei_karten()

    def kaputt(_):
        raise RuntimeError("Ollama antwortet nicht")

    ergebnis = _kampf(a, b, kaputt)

    assert ergebnis["runden"] > 0
    assert ergebnis["ausgewichen"] == ergebnis["gefragt"]
    if ergebnis["protokoll"]:
        assert "Ollama antwortet nicht" in ergebnis["protokoll"][0]["grund"]


def test_ein_kampf_ohne_ergebnis_bricht_nach_der_hoechstzahl_ab():
    """Sonst koennte ein ausweichendes Modell eine Endlosschleife erzeugen."""
    a, b = _zwei_karten()

    ergebnis = _kampf(a, b, "1")

    assert ergebnis["runden"] <= ki_gegner.MAX_ZUEGE


def test_ein_abbruch_beendet_den_kampf_sofort():
    """Ein Kampf dauert Minuten - er muss sich anhalten lassen."""
    a, b = _zwei_karten()

    async def sofort():
        return True

    ergebnis = asyncio.run(ki_gegner.kontrollkampf(
        a, b, frage=_antwort("1"), seed=7, abbruch=sofort))

    assert ergebnis["abgebrochen"] is True
    assert ergebnis["runden"] == 0


def test_der_fortschritt_wird_gemeldet():
    a, b = _zwei_karten()
    meldungen = []

    async def fortschritt(erledigt, gesamt, stufe=None):
        meldungen.append((erledigt, gesamt, stufe))

    ergebnis = asyncio.run(ki_gegner.kontrollkampf(
        a, b, frage=_antwort("1"), seed=7, fortschritt=fortschritt))

    assert len(meldungen) == ergebnis["runden"]
    assert meldungen[0][0] == 1


def test_das_protokoll_haelt_jeden_zug_fest():
    a, b = _zwei_karten()

    ergebnis = _kampf(a, b, "1")

    for zug in ergebnis["protokoll"]:
        assert zug["lage"]["angriffe"]
        assert zug["gewaehlt_index"] >= 0
        assert zug["gewaehlt_name"]
        assert zug["sekunden"] >= 0


def test_bei_nur_einem_moeglichen_zug_wird_nicht_gefragt():
    """Jede Anfrage kostet Sekunden - fuer eine Nicht-Wahl waere das Verschwendung."""
    gefragt = []

    class EinZug:
        def legal_attack_indices(self, player_id):
            return [2]

    async def frage(prompt):
        gefragt.append(prompt)
        return "1"

    ki = ki_gegner.KIGegner(frage)

    assert asyncio.run(ki.waehle(EinZug(), 1)) == 2
    assert gefragt == []
    assert ki.gefragt == 0
    assert ki.protokoll == []


def test_ohne_moeglichen_zug_faellt_die_wahl_auf_null():
    class KeinZug:
        def legal_attack_indices(self, player_id):
            return []

    ki = ki_gegner.KIGegner(_antwort("1"))

    assert asyncio.run(ki.waehle(KeinZug(), 1)) == 0


# --------------------------------------------------------------------------
# Die Einordnung in Worten
# --------------------------------------------------------------------------
def test_ohne_eine_einzige_frage_sagt_die_einordnung_das_auch():
    ki = ki_gegner.KIGegner(_antwort("1"))

    assert "nie gefragt" in ki_gegner.einordnen(ki)


def test_viel_ausweichen_wird_deutlich_benannt():
    ki = ki_gegner.KIGegner(_antwort("1"))
    ki.gefragt, ki.ausgewichen = 10, 8

    assert "taugt als Gegner nicht" in ki_gegner.einordnen(ki)


def test_lueckenloses_entscheiden_wird_gelobt():
    ki = ki_gegner.KIGegner(_antwort("1"))
    ki.gefragt, ki.ausgewichen = 10, 0

    assert "Alle 10 Entscheidungen" in ki_gegner.einordnen(ki)


def test_gelegentliches_ausweichen_bekommt_einen_eigenen_satz():
    ki = ki_gegner.KIGegner(_antwort("1"))
    ki.gefragt, ki.ausgewichen = 10, 2

    assert "nicht verlässlich" in ki_gegner.einordnen(ki)


# --------------------------------------------------------------------------
# Verdrahtung
# --------------------------------------------------------------------------
def test_der_kampf_setzt_den_globalen_zufall_nie_neu(monkeypatch):
    """Der springende Punkt gegenueber simulate_duel.

    simulate_duel setzt random.seed() auf einen festen Wert und stellt den
    Zustand danach wieder her. Sicher ist das nur, solange dazwischen nicht
    abgegeben wird - hier wird aber bei jedem Zug auf das Modell gewartet.
    Ein echter Kampf im Spiel saehe in dieser Zeit einen Zufall, der auf einem
    bekannten Startwert steht, und waere damit vorhersagbar.

    Der Kontrollkampf verbraucht Zufallszahlen wie jeder andere Kampf auch -
    das ist harmlos. Er darf nur nie am Startwert drehen.
    """
    import random as zufall

    a, b = _zwei_karten()

    def verboten(*_a, **_k):                                   # pragma: no cover
        raise AssertionError("random.seed() darf im Kontrollkampf nicht vorkommen")

    monkeypatch.setattr(zufall, "seed", verboten)
    monkeypatch.setattr(zufall, "setstate", verboten)

    ergebnis = _kampf(a, b, "1")

    assert ergebnis["runden"] > 0


def test_die_engine_bleibt_unveraendert_bei_fuenfhundert_runden():
    """Der Kontrollkampf fuehrt seine Schleife selbst - simulate_duel wurde
    dafuer nicht angefasst, sonst fiele jedes bisherige Ergebnis anders aus."""
    quelle = (ROOT / "simulation" / "engine.py").read_text(encoding="utf-8")

    assert "rounds < 500" in quelle
    assert "strategie_a" not in quelle


def test_gekaempft_wird_im_bot_und_nicht_im_backend():
    """Die Engine braucht ueber combat_runner das ganze bot.py - discord und
    aiosqlite. Beides ist im Backend-Abbild nicht drin."""
    web = (ROOT / "web" / "app" / "kikampf.py").read_text(encoding="utf-8")
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")

    assert "simulate_duel" not in web
    assert "ki_gegner" not in web
    assert 'art == "cards.kikampf"' in bot
    assert "ki_gegner.kontrollkampf(" in bot


def test_der_bot_benutzt_aiohttp_und_nicht_httpx():
    """httpx ist nur im Backend-Abbild, aiohttp bringt discord.py mit."""
    quelle = (ROOT / "services" / "ollama_bot.py").read_text(encoding="utf-8")

    assert "import aiohttp" in quelle
    assert "import httpx" not in quelle


def test_der_bot_liest_die_einstellungen_der_website():
    """Kein zweiter Ort, an dem eine Adresse gepflegt werden muesste."""
    quelle = (ROOT / "services" / "ollama_bot.py").read_text(encoding="utf-8")

    assert "web_settings" in quelle
    assert "ollama.model_kampf" in quelle
    assert "ollama.url" in quelle
