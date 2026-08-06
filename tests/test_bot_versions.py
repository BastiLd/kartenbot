"""Tests für die Gegner-Versionen.

Der wichtigste Punkt steht ganz oben: Bei „Standard" (Fehlerquote 0) muss
der Bot exakt wie vorher spielen. Alles Neue ist abschaltbar, und ist es
aus, verhält sich der Bot wie eh und je.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from services import bot_versions

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Die Fehlerquote — der einzige Eingriff in den laufenden Kampf
# --------------------------------------------------------------------------
def test_ohne_fehlerquote_bleibt_es_beim_besten_zug():
    """Standard heisst: nichts ändert sich, nicht einmal ein Würfelwurf."""
    class WuerfelWaechter(random.Random):
        def random(self):                     # pragma: no cover - darf nie laufen
            raise AssertionError("bei Fehlerquote 0 darf nicht gewuerfelt werden")

    for quote in (0.0, 0, None):
        assert bot_versions.waehle_mit_fehlerquote(
            [0, 1, 2, 3], 2, quote, WuerfelWaechter()) == 2


def test_bei_einem_einzigen_zug_gibt_es_nichts_zu_verfehlen():
    class WuerfelWaechter(random.Random):
        def random(self):                     # pragma: no cover
            raise AssertionError("ohne Alternative darf nicht gewuerfelt werden")

    assert bot_versions.waehle_mit_fehlerquote([1], 1, 0.9, WuerfelWaechter()) == 1


def test_volle_fehlerquote_nimmt_immer_einen_anderen_zug():
    for versuch in range(20):
        gewaehlt = bot_versions.waehle_mit_fehlerquote(
            [0, 1, 2], 1, 1.0, random.Random(versuch))
        assert gewaehlt in (0, 2), "bei Quote 1 nie der beste Zug"


def test_die_fehlerquote_trifft_ungefaehr_zu():
    daneben = sum(
        1 for i in range(600)
        if bot_versions.waehle_mit_fehlerquote([0, 1, 2], 1, 0.3, random.Random(i)) != 1
    )
    # 30 % von 600 sind 180; die Streuung darf grosszuegig sein.
    assert 130 <= daneben <= 240, f"{daneben} von 600 danebengegriffen"


@pytest.mark.parametrize("quote", [-5.0, 1.5, "0.2"])
def test_unsinnige_quoten_bringen_nichts_durcheinander(quote):
    ergebnis = bot_versions.waehle_mit_fehlerquote([0, 1, 2], 1, quote, random.Random(1))

    assert ergebnis in (0, 1, 2)


def test_standard_ist_immer_da_und_ohne_fehler():
    standard = bot_versions.standard()

    assert standard["name"] == bot_versions.STANDARD_NAME
    assert standard["fehlerquote"] == 0.0
    assert standard["ist_standard"] is True
    assert standard["beschreibung"].strip()


# --------------------------------------------------------------------------
# Verwaltung auf der Website
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


def test_neue_version_anlegen_und_wiederfinden(verwaltung):
    verwaltung.anlegen("Schwer", "Für Leute, die sich auskennen.", 0.0)
    namen = [v["name"] for v in verwaltung.alle()]

    assert namen == ["Standard", "Schwer"]


def test_standard_steht_immer_vorne_und_ist_geschuetzt(verwaltung):
    verwaltung.anlegen("Aaa-Test", "", 0.1)

    assert verwaltung.alle()[0]["name"] == "Standard"
    assert verwaltung.alle()[0]["loeschbar"] is False
    with pytest.raises(verwaltung.VersionFehler, match="nicht löschen"):
        verwaltung.loeschen(0)
    with pytest.raises(verwaltung.VersionFehler, match="nicht ändern"):
        verwaltung.aendern(0, "Neu", "", 0.0)


def test_der_name_standard_ist_belegt(verwaltung):
    with pytest.raises(verwaltung.VersionFehler, match="fest eingebaut"):
        verwaltung.anlegen("standard", "", 0.0)


def test_kein_name_zweimal(verwaltung):
    verwaltung.anlegen("Schwer", "", 0.2)
    with pytest.raises(verwaltung.VersionFehler, match="gibt es schon"):
        verwaltung.anlegen("Schwer", "", 0.3)


def test_version_ohne_namen_wird_abgelehnt(verwaltung):
    with pytest.raises(verwaltung.VersionFehler, match="Namen"):
        verwaltung.anlegen("   ", "", 0.0)


@pytest.mark.parametrize("quote", [-0.1, 0.95, "viel"])
def test_unsinnige_fehlerquote_wird_abgelehnt(verwaltung, quote):
    with pytest.raises(verwaltung.VersionFehler):
        verwaltung.anlegen("Test", "", quote)


def test_aendern_und_kopieren(verwaltung):
    version = verwaltung.anlegen("Schwer", "Hart aber fair.", 0.1)
    geaendert = verwaltung.aendern(version["id"], "Sehr schwer", "Jetzt ernst.", 0.25)

    assert geaendert["name"] == "Sehr schwer"
    assert geaendert["fehlerquote"] == 0.25

    kopie = verwaltung.kopieren(geaendert["id"], "Sehr schwer (Kopie)")
    assert kopie["fehlerquote"] == 0.25
    assert kopie["beschreibung"] == "Jetzt ernst."
    assert kopie["id"] != geaendert["id"]


def test_geloeschte_version_gilt_nirgends_mehr(verwaltung):
    """Sonst zeigte die Zuordnung auf etwas, das es nicht mehr gibt."""
    version = verwaltung.anlegen("Weg damit", "", 0.2)
    verwaltung.setze_aktiv(None, version["id"])
    assert verwaltung.aktive_zuordnung()["*"] == version["id"]

    verwaltung.loeschen(version["id"])
    assert verwaltung.aktive_zuordnung().get("*") is None


def test_aktive_version_pro_server_und_fuer_alle(verwaltung):
    a = verwaltung.anlegen("Leicht", "", 0.4)
    b = verwaltung.anlegen("Schwer", "", 0.0)
    verwaltung.setze_aktiv(None, a["id"])
    verwaltung.setze_aktiv("123456", b["id"])
    zuordnung = verwaltung.aktive_zuordnung()

    assert zuordnung["*"] == a["id"]
    assert zuordnung["123456"] == b["id"]


def test_unbekannte_version_laesst_sich_nicht_aktivieren(verwaltung):
    with pytest.raises(verwaltung.VersionFehler, match="gibt es nicht"):
        verwaltung.setze_aktiv(None, 9999)


# --------------------------------------------------------------------------
# Verdrahtung
# --------------------------------------------------------------------------
def test_tabellen_stehen_auf_beiden_seiten_gleich():
    import importlib.util

    pfad = ROOT / "web" / "app" / "schema.py"
    spec = importlib.util.spec_from_file_location("_web_schema3", pfad)
    web_schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_schema)

    def spalten(anweisungen, tabelle):
        for a in anweisungen:
            treffer = re.search(rf"CREATE TABLE IF NOT EXISTS {tabelle}\s*\((.*)\)\s*$",
                                a.strip(), re.DOTALL)
            if treffer:
                return [z.strip().split()[0] for z in treffer.group(1).strip().splitlines()
                        if z.strip()]
        return []

    for tabelle in ("bot_versions", "bot_version_aktiv"):
        web = spalten(web_schema._TABLES, tabelle)
        bot = spalten(bot_versions._SCHEMA, tabelle)
        assert web, f"{tabelle} fehlt in web/app/schema.py"
        assert bot, f"{tabelle} fehlt in services/bot_versions.py"
        assert web == bot, f"{tabelle} laeuft auseinander"


def test_der_bot_fragt_die_fehlerquote_wirklich_ab():
    """Ohne diesen Aufruf waere die ganze Verwaltung wirkungslos."""
    quelle = (ROOT / "bot.py").read_text(encoding="utf-8")

    assert "bot_versions.waehle_mit_fehlerquote(" in quelle
    assert "bot_versions.aktive(" in quelle
