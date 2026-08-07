"""Tests für die Auswahl der Gegner-Version beim Kampfstart (Stufe 5, Schritt 8).

Der Eingriff sitzt in der Kampf-Oberfläche des Bots, und dort darf nichts
schlechter werden. Entsprechend prüfen die Tests vor allem, was **nicht**
passiert: Ohne gespeicherte Version wird nicht gefragt, eine gewählte Version
wird beim Laden nicht wieder überschrieben, und ein Neustart mitten im Kampf
tauscht den Gegner nicht heimlich aus.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services import bot_versions

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Wann überhaupt gefragt wird
# --------------------------------------------------------------------------
def test_ohne_eigene_version_wird_nicht_gefragt():
    """Nur „Standard" heisst: nichts zu entscheiden, also keine Rueckfrage."""
    assert bot_versions.braucht_auswahl([bot_versions.standard()]) is False
    assert bot_versions.braucht_auswahl([]) is False
    assert bot_versions.braucht_auswahl(None) is False


def test_ab_der_ersten_eigenen_version_wird_gefragt():
    versionen = [bot_versions.standard(), {"id": 1, "name": "Schwer"}]

    assert bot_versions.braucht_auswahl(versionen) is True


# --------------------------------------------------------------------------
# Was in der Liste steht
# --------------------------------------------------------------------------
def test_standard_steht_mit_seiner_beschreibung_in_der_liste():
    optionen = bot_versions.auswahl_optionen([bot_versions.standard()])

    assert optionen[0]["name"] == bot_versions.STANDARD_NAME
    assert optionen[0]["wert"] == "0"
    assert optionen[0]["hinweis"].strip()


def test_die_fehlerquote_steht_als_klartext_dabei():
    """Ohne sie saehen zwei Versionen mit gleicher Beschreibung gleich aus."""
    optionen = bot_versions.auswahl_optionen(
        [{"id": 3, "name": "Übungsgegner", "beschreibung": "Für den Anfang.",
          "fehlerquote": 0.35}])

    assert "Für den Anfang." in optionen[0]["hinweis"]
    assert "35 %" in optionen[0]["hinweis"]


def test_ohne_beschreibung_steht_wenigstens_die_quote_da():
    optionen = bot_versions.auswahl_optionen(
        [{"id": 4, "name": "Schwach", "beschreibung": "", "fehlerquote": 0.5}])

    assert optionen[0]["hinweis"] == "greift in 50 % der Züge daneben"


def test_ohne_quote_kein_leerer_zusatz():
    optionen = bot_versions.auswahl_optionen(
        [{"id": 5, "name": "Hart", "beschreibung": "Spielt sauber.", "fehlerquote": 0.0}])

    assert optionen[0]["hinweis"] == "Spielt sauber."


def test_die_aktive_version_ist_vorgewaehlt():
    versionen = [bot_versions.standard(), {"id": 7, "name": "Schwer"}]
    optionen = bot_versions.auswahl_optionen(versionen, aktive_id=7)

    assert [o["vorgewaehlt"] for o in optionen] == [False, True]


def test_ohne_zuordnung_ist_standard_vorgewaehlt():
    versionen = [bot_versions.standard(), {"id": 7, "name": "Schwer"}]
    optionen = bot_versions.auswahl_optionen(versionen)

    assert optionen[0]["vorgewaehlt"] is True


# --------------------------------------------------------------------------
# Discords Grenzen — sie werden kommentarlos durchgesetzt
# --------------------------------------------------------------------------
def test_mehr_als_fuenfundzwanzig_versionen_sprengen_die_liste_nicht():
    versionen = [{"id": i, "name": f"V{i}"} for i in range(40)]

    assert len(bot_versions.auswahl_optionen(versionen)) == bot_versions.AUSWAHL_MAX


def test_lange_namen_und_beschreibungen_werden_gekuerzt():
    optionen = bot_versions.auswahl_optionen(
        [{"id": 1, "name": "N" * 300, "beschreibung": "B" * 300, "fehlerquote": 0.1}])

    assert len(optionen[0]["name"]) == bot_versions.AUSWAHL_TEXT_MAX
    assert len(optionen[0]["hinweis"]) == bot_versions.AUSWAHL_TEXT_MAX


@pytest.mark.parametrize("kaputt", [
    {"id": None, "name": None},
    {"id": "x", "name": "", "fehlerquote": "viel"},
    {},
])
def test_unvollstaendige_versionen_bringen_die_liste_nicht_zu_fall(kaputt):
    """Was aus der Datenbank kommt, muss nicht heil sein."""
    try:
        optionen = bot_versions.auswahl_optionen([kaputt])
    except (TypeError, ValueError):
        pytest.fail("eine kaputte Zeile darf den Kampfstart nicht aufhalten")

    assert optionen[0]["name"]
    assert optionen[0]["wert"] == "0"


# --------------------------------------------------------------------------
# Verdrahtung im Bot — ohne sie ist die ganze Auswahl wirkungslos
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bot_quelle() -> str:
    return (ROOT / "bot.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kampf_quelle() -> str:
    return (ROOT / "botcommands" / "gameplay_commands.py").read_text(encoding="utf-8")


def test_der_kampf_gegen_den_bot_fragt_nach_der_version(kampf_quelle):
    assert "frage_gegner_version(interaction)" in kampf_quelle
    assert "setze_gegner_version(" in kampf_quelle


def test_gesetzt_wird_vor_dem_laden_der_buffs(kampf_quelle):
    """Andersherum wuerde init_with_buffs die Wahl wieder ueberschreiben."""
    setzen = kampf_quelle.index("setze_gegner_version(")
    laden = kampf_quelle.index("battle_view.init_with_buffs()")

    assert setzen < laden


def test_init_ueberschreibt_eine_gewaehlte_version_nicht(bot_quelle):
    assert 'if self.player2_id == 0 and not getattr(self, "_bot_version", None):' in bot_quelle


def test_die_servereinstellung_kennt_jetzt_ihren_server(bot_quelle):
    """Vorher stand dort fest None - es galt immer "fuer alle Server"."""
    assert 'bot_versions.aktive(getattr(self, "_bot_guild_id", None))' in bot_quelle
    assert "bot_versions.aktive(None)" not in bot_quelle


def test_die_version_ueberlebt_einen_neustart_mitten_im_kampf(bot_quelle):
    """Sonst spielte der Bot nach dem Neustart ploetzlich wieder Standard."""
    assert '"bot_version": _json_clone(getattr(self, "_bot_version", None))' in bot_quelle
    assert '"bot_fehlerquote": float(getattr(self, "_bot_fehlerquote", 0.0) or 0.0)' in bot_quelle
    assert 'payload.get("bot_version")' in bot_quelle
    assert 'payload.get("bot_fehlerquote", 0.0)' in bot_quelle


def test_die_abfrage_bricht_den_kampf_nie_ab(bot_quelle):
    """Jeder Weg aus frage_gegner_version muss eine Version liefern."""
    treffer = re.search(r"async def frage_gegner_version\(.*?\n(?=\n\nclass |\n\nasync def |\n\ndef )",
                        bot_quelle, re.DOTALL)
    assert treffer, "frage_gegner_version nicht gefunden"
    rumpf = treffer.group(0)

    assert "return None" not in rumpf
    assert rumpf.count("return voreinstellung") >= 3
    assert "return view.value or voreinstellung" in rumpf
