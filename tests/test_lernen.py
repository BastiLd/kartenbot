"""Tests für das Lernen aus echten Kämpfen (Stufe 5, Schritt 9).

Zwei Dinge stehen über allem:

1. *Ohne Gewichte ändert sich nichts.* Die Engine muss ohne Angabe Zahl für
   Zahl dasselbe rechnen wie vorher — sonst wären alle bisherigen Testläufe
   still ungültig geworden.
2. *Gemessen wird der Abstand zum Zufall*, nicht die nackte Häufigkeit. Eine
   Kategorie, die auf jeder Karte steht, darf nicht allein deshalb als
   beliebt gelten.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services import lernen
from simulation import strategy

ROOT = Path(__file__).resolve().parents[1]


def zug(gewaehlt: int, *angriffe: list[str]) -> dict:
    """Ein Zug: welche Angriffe zur Wahl standen, welcher genommen wurde."""
    return {
        "angriff_index": gewaehlt,
        "angriffe": [{"index": i, "wirkungen": w} for i, w in enumerate(angriffe)],
    }


# --------------------------------------------------------------------------
# Die Engine ohne Gewichte — hier darf sich nichts verschoben haben
# --------------------------------------------------------------------------
def test_die_eingebauten_gewichte_sind_die_alten_zahlen():
    assert strategy.STANDARD_GEWICHTE == {
        "control": 140.0, "defense": 110.0, "setup": 85.0, "dot": 90.0}


def test_ohne_angabe_gelten_die_eingebauten_werte():
    assert strategy.normalisiere_gewichte() == strategy.STANDARD_GEWICHTE
    assert strategy.normalisiere_gewichte({}) == strategy.STANDARD_GEWICHTE
    assert strategy.normalisiere_gewichte(None) == strategy.STANDARD_GEWICHTE


def test_eine_teilangabe_laesst_den_rest_stehen():
    werte = strategy.normalisiere_gewichte({"control": 200})

    assert werte["control"] == 200.0
    assert werte["defense"] == 110.0
    assert werte["setup"] == 85.0
    assert werte["dot"] == 90.0


@pytest.mark.parametrize("kaputt", [
    {"control": "viel"}, {"control": None}, {"unbekannt": 5},
    {"CONTROL": 200}, {"  defense  ": 7},
])
def test_krumme_gewichte_bringen_die_engine_nicht_zu_fall(kaputt):
    """Sie kommen aus der Datenbank — heil sind sie da nicht immer."""
    werte = strategy.normalisiere_gewichte(kaputt)

    assert set(werte) == set(strategy.STANDARD_GEWICHTE)
    assert all(isinstance(w, float) for w in werte.values())


def test_gross_und_kleinschreibung_und_leerzeichen_zaehlen_nicht():
    assert strategy.normalisiere_gewichte({"CONTROL": 200})["control"] == 200.0
    assert strategy.normalisiere_gewichte({" Defense ": 7})["defense"] == 7.0


# --------------------------------------------------------------------------
# Was gezählt wird — und was nicht
# --------------------------------------------------------------------------
def test_ohne_wahl_wird_nichts_gelernt():
    """Wer nur einen Angriff hat, trifft keine Entscheidung."""
    ergebnis = lernen.auswerten([zug(0, ["stun"])] * 100)

    assert ergebnis["zuege_verwertet"] == 0
    assert ergebnis["gewichte"] == strategy.STANDARD_GEWICHTE


def test_ein_zug_auf_einen_angriff_der_gar_nicht_zur_wahl_stand_zaehlt_nicht():
    """Erzwungene Landungen stehen mit Index -1 in der Mitschrift."""
    ergebnis = lernen.auswerten([zug(-1, ["stun"], [])] * 100)

    assert ergebnis["zuege_verwertet"] == 0


def test_zu_wenig_gelegenheiten_aendern_nichts():
    ergebnis = lernen.auswerten([zug(0, ["stun"], [])] * 5)

    assert ergebnis["zuege_verwertet"] == 5
    assert ergebnis["gewichte"]["control"] == strategy.STANDARD_GEWICHTE["control"]
    assert ergebnis["grundlage"]["control"]["gelernt"] is False


def test_ab_der_schwelle_wird_gelernt():
    ergebnis = lernen.auswerten([zug(0, ["stun"], [])] * lernen.MINDEST_GELEGENHEITEN)

    assert ergebnis["grundlage"]["control"]["gelernt"] is True
    assert ergebnis["gewichte"]["control"] > strategy.STANDARD_GEWICHTE["control"]


# --------------------------------------------------------------------------
# Der Kern: gemessen wird der Abstand zum Zufall
# --------------------------------------------------------------------------
def test_wer_immer_kontrolle_nimmt_hebt_ihr_gewicht():
    ergebnis = lernen.auswerten([zug(0, ["stun"], [])] * 100)

    assert ergebnis["grundlage"]["control"]["faktor"] == 2.0
    assert ergebnis["gewichte"]["control"] == 280.0


def test_wer_kontrolle_meidet_senkt_ihr_gewicht():
    ergebnis = lernen.auswerten([zug(1, ["stun"], [])] * 100)

    assert ergebnis["grundlage"]["control"]["faktor"] == 0.5
    assert ergebnis["gewichte"]["control"] == 70.0


def test_wer_genau_nach_zufall_waehlt_aendert_nichts():
    """Zwei Angriffe, einer mit Kontrolle, jeder gleich oft genommen."""
    ergebnis = lernen.auswerten(
        [zug(0, ["stun"], []) for _ in range(50)] +
        [zug(1, ["stun"], []) for _ in range(50)])

    assert ergebnis["grundlage"]["control"]["faktor"] == 1.0
    assert ergebnis["gewichte"]["control"] == strategy.STANDARD_GEWICHTE["control"]


def test_eine_kategorie_auf_jedem_angriff_gilt_nicht_als_beliebt():
    """Der springende Punkt: sonst gewaenne, was am haeufigsten vorkommt.

    Hier steht auf *beiden* Angriffen Kontrolle. Sie wird also in 100 % der
    Zuege gewaehlt - der Zufall haette sie aber auch in 100 % gewaehlt.
    """
    ergebnis = lernen.auswerten([zug(0, ["stun"], ["confusion"])] * 100)

    assert ergebnis["grundlage"]["control"]["gewaehlt"] == 100
    assert ergebnis["grundlage"]["control"]["faktor"] == 1.0
    assert ergebnis["gewichte"]["control"] == strategy.STANDARD_GEWICHTE["control"]


def test_der_faktor_ist_nach_oben_und_unten_gedeckelt():
    """Ein einzelner Abend darf den Gegner nicht unkenntlich machen."""
    viel = lernen.auswerten([zug(0, ["stun"], [], [], [])] * 100)
    wenig = lernen.auswerten([zug(1, ["stun"], [], [], [])] * 100)

    assert viel["grundlage"]["control"]["faktor"] == lernen.MAX_FAKTOR
    assert wenig["grundlage"]["control"]["faktor"] == lernen.MIN_FAKTOR


def test_kategorien_ohne_gelegenheit_bleiben_unangetastet():
    ergebnis = lernen.auswerten([zug(0, ["stun"], [])] * 100)

    for name in ("defense", "setup", "dot"):
        assert ergebnis["grundlage"][name]["gelegenheiten"] == 0
        assert ergebnis["grundlage"][name]["gelernt"] is False
        assert ergebnis["gewichte"][name] == strategy.STANDARD_GEWICHTE[name]


def test_alle_vier_kategorien_werden_erkannt():
    proben = {"control": "stun", "defense": "shield",
              "setup": "damage_boost", "dot": "burning"}
    for name, wirkung in proben.items():
        ergebnis = lernen.auswerten([zug(0, [wirkung], [])] * 100)
        assert ergebnis["grundlage"][name]["gelernt"] is True, name
        assert ergebnis["gewichte"][name] > strategy.STANDARD_GEWICHTE[name], name


def test_unbekannte_wirkungen_zaehlen_zu_keiner_kategorie():
    assert lernen.kategorien_von(["gibtesnicht", ""]) == set()
    assert lernen.kategorien_von(None) == set()
    assert lernen.kategorien_von(["STUN"]) == {"control"}


# --------------------------------------------------------------------------
# Die Zeilen aus der Datenbank
# --------------------------------------------------------------------------
def test_zeilen_werden_in_zuege_uebersetzt():
    lage = {"angriffe": [{"index": 0, "wirkungen": ["stun"]}, {"index": 1, "wirkungen": []}]}
    zeilen = [{"angriff_index": 0, "lage_json": json.dumps(lage)}]

    assert lernen.zuege_aus_zeilen(zeilen) == [
        {"angriff_index": 0, "angriffe": lage["angriffe"]}]


@pytest.mark.parametrize("kaputt", [
    {"angriff_index": 0, "lage_json": "kein json"},
    {"angriff_index": 0, "lage_json": "[]"},
    {"angriff_index": 0, "lage_json": None},
    {"angriff_index": 0},
])
def test_kaputte_zeilen_werden_uebergangen_statt_zu_stoeren(kaputt):
    heil = {"angriff_index": 0,
            "lage_json": json.dumps({"angriffe": [{"index": 0, "wirkungen": ["stun"]}]})}

    assert len(lernen.zuege_aus_zeilen([kaputt, heil])) == 1


# --------------------------------------------------------------------------
# Der Text zum Ergebnis
# --------------------------------------------------------------------------
def test_ohne_material_sagt_der_text_das_auch():
    text = lernen.beschreibe(lernen.auswerten([]))

    assert "keinen einzigen verwertbaren Zug" in text


def test_der_text_nennt_zahl_und_richtung():
    text = lernen.beschreibe(lernen.auswerten([zug(0, ["stun"], [])] * 100))

    assert "100 Entscheidungen" in text
    assert "öfter genommen" in text
    assert "140.0 → 280.0" in text


def test_zu_seltene_kategorien_werden_im_text_erklaert():
    text = lernen.beschreibe(lernen.auswerten([zug(0, ["shield"], [])] * 3))

    assert "zu selten" in text
    assert "eingebauten Wert" in text


# --------------------------------------------------------------------------
# Die Website-Hälfte: lesen, rechnen, in die Version schreiben
# --------------------------------------------------------------------------
@pytest.fixture
def verwaltung(tmp_path, monkeypatch):
    import sqlite3
    import sys

    if str(ROOT / "web") not in sys.path:
        sys.path.insert(0, str(ROOT / "web"))
    try:
        from app import database, gegnerversionen, schema
    except Exception as fehler:                                # noqa: BLE001
        pytest.skip(f"web/app nicht importierbar: {fehler}")

    pfad = tmp_path / "test.db"
    sqlite3.connect(str(pfad)).close()
    monkeypatch.setattr(database.config, "DB_PATH", str(pfad))
    with database.write_connection() as con:
        schema.init_schema(con)
    return gegnerversionen


def _schreibe_zuege(verwaltung, anzahl: int, *, ausgang="gewonnen", ist_bot=0,
                    gewaehlt=0, wirkungen=("stun",)):
    from app import database

    lage = json.dumps({"angriffe": [
        {"index": 0, "wirkungen": list(wirkungen)}, {"index": 1, "wirkungen": []}]})
    with database.write_connection() as con:
        for i in range(anzahl):
            con.execute(
                "INSERT INTO battle_moves (erstellt_am, session_id, ist_bot, "
                "angriff_index, lage_json, ausgang) VALUES (?, ?, ?, ?, ?, ?)",
                ("2026-08-07T08:00:00+00:00", i // 10, ist_bot, gewaehlt, lage, ausgang))


def test_ohne_mitschrift_sagt_der_fehler_wo_der_schalter_steht(verwaltung):
    version = verwaltung.anlegen("Lernt", "", 0.0)

    with pytest.raises(verwaltung.VersionFehler, match="Schalter"):
        verwaltung.lerne(version["id"])


def test_ohne_gewonnene_kaempfe_sagt_der_fehler_etwas_anderes(verwaltung):
    version = verwaltung.anlegen("Lernt", "", 0.0)
    _schreibe_zuege(verwaltung, 40, ausgang="verloren")

    with pytest.raises(verwaltung.VersionFehler, match="gewonnen"):
        verwaltung.lerne(version["id"])


def test_die_zuege_des_bots_werden_nicht_gelernt(verwaltung):
    """Sonst bestaetigte er sich nur seine eigenen Vorlieben."""
    version = verwaltung.anlegen("Lernt", "", 0.0)
    _schreibe_zuege(verwaltung, 40, ist_bot=1)

    with pytest.raises(verwaltung.VersionFehler, match="gewonnen"):
        verwaltung.lerne(version["id"])


def test_gelernte_gewichte_landen_in_der_version(verwaltung):
    version = verwaltung.anlegen("Lernt", "", 0.0)
    _schreibe_zuege(verwaltung, 60)

    gelernt = verwaltung.lerne(version["id"])

    assert gelernt["gewichte"]["control"] == 280.0
    assert gelernt["lernstand"]["zuege_verwertet"] == 60
    assert gelernt["lernstand"]["text"]
    assert gelernt["lernstand"]["stand_am"]


def test_standard_lernt_nichts_dazu(verwaltung):
    """Es muss immer einen Weg zurueck zu "spielt wie immer" geben."""
    _schreibe_zuege(verwaltung, 60)

    with pytest.raises(verwaltung.VersionFehler, match="Kopie"):
        verwaltung.lerne(0)


def test_gelerntes_laesst_sich_wieder_wegwerfen(verwaltung):
    version = verwaltung.anlegen("Lernt", "", 0.0)
    _schreibe_zuege(verwaltung, 60)
    verwaltung.lerne(version["id"])

    zurueck = verwaltung.gewichte_vergessen(version["id"])

    assert zurueck["gewichte"] == {}
    assert zurueck["lernstand"] == {}


def test_der_lernstoff_zaehlt_nur_das_verwertbare(verwaltung):
    _schreibe_zuege(verwaltung, 30)
    _schreibe_zuege(verwaltung, 10, ausgang="verloren")
    _schreibe_zuege(verwaltung, 5, ist_bot=1)

    stoff = verwaltung.lernstoff()

    assert stoff["zuege"] == 30
    assert stoff["mitgeschrieben"] == 45


def test_ohne_tabelle_zaehlt_der_lernstoff_null(verwaltung):
    """Auf einer Datenbank, auf der die Mitschrift nie lief, gibt es sie nicht."""
    from app import database

    with database.write_connection() as con:
        con.execute("DROP TABLE battle_moves")

    assert verwaltung.lernstoff() == {"zuege": 0, "kaempfe": 0, "mitgeschrieben": 0}
    version = verwaltung.anlegen("Lernt", "", 0.0)
    with pytest.raises(verwaltung.VersionFehler, match="Schalter"):
        verwaltung.lerne(version["id"])


# --------------------------------------------------------------------------
# Verdrahtung
# --------------------------------------------------------------------------
def test_die_engine_reicht_die_gewichte_bis_zur_bewertung_durch():
    quelle = (ROOT / "simulation" / "engine.py").read_text(encoding="utf-8")
    strat = (ROOT / "simulation" / "strategy.py").read_text(encoding="utf-8")

    assert "gewichte=gewichte" in quelle
    assert "def build_strategy" in strat and "gewichte" in strat
    assert 'werte["control"]' in strat
    # Die vier Zahlen duerfen nicht mehr fest im Rechenweg stehen.
    assert not re.search(r"CONTROL_EFFECTS\)\s*\*\s*140", strat)


def test_der_testlauf_reicht_die_gewichte_bis_zur_engine_durch():
    """Ohne diese Kette waere das Gelernte eine Zahl ohne Folgen."""
    lauf = (ROOT / "services" / "card_testrun.py").read_text(encoding="utf-8")
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")

    assert "gewichte=gewichte" in lauf
    assert "gewichte: dict | None = None" in lauf
    assert "bot_versions.hole(version_id)" in bot
    assert "auswahl=spielweise, gewichte=gewichte" in bot


def test_der_testlauf_haelt_fest_womit_er_gerechnet_hat():
    """Sonst liessen sich zwei Ergebnisse derselben Karte nicht auseinanderhalten."""
    lauf = (ROOT / "services" / "card_testrun.py").read_text(encoding="utf-8")

    assert lauf.count('"gewichte": dict(gewichte) if gewichte else {}') == 2


def test_eine_version_ohne_gelerntes_wird_im_testlauf_abgelehnt(verwaltung):
    """Sie wuerde auf exakt dieselben Zahlen kommen - das waere nur Verwirrung."""
    import sys

    if str(ROOT / "web") not in sys.path:
        sys.path.insert(0, str(ROOT / "web"))
    from app import karteneditor

    version = verwaltung.anlegen("Ohne alles", "", 0.0)
    with pytest.raises(karteneditor.EditorFehler, match="nichts gelernt"):
        karteneditor.pruefe_testlauf("Hulk", "beides", 200, version["id"])


def test_nur_versionen_mit_gelerntem_stehen_zur_wahl(verwaltung):
    import sys

    if str(ROOT / "web") not in sys.path:
        sys.path.insert(0, str(ROOT / "web"))
    from app import karteneditor

    verwaltung.anlegen("Ohne alles", "", 0.0)
    gelernt = verwaltung.anlegen("Mit Wissen", "", 0.0)
    _schreibe_zuege(verwaltung, 60)
    verwaltung.lerne(gelernt["id"])

    namen = [v["name"] for v in karteneditor.testlauf_moeglichkeiten()["gelernte_versionen"]]

    assert namen == ["Mit Wissen"]


def test_die_kategorien_kommen_aus_der_engine_und_sind_nicht_abgeschrieben():
    assert lernen.KATEGORIEN["control"] is strategy.CONTROL_EFFECTS
    assert lernen.KATEGORIEN["defense"] is strategy.DEFENSE_EFFECTS
    assert lernen.KATEGORIEN["setup"] is strategy.SETUP_EFFECTS
    assert lernen.KATEGORIEN["dot"] is strategy.DOT_EFFECTS
