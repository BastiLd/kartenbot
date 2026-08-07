"""Tests rund um die Auslieferung der drei Teile.

Kartenbot Web besteht aus Bot, Backend und Oberfläche, die getrennt
hochgeladen werden. Wer nur eines aktualisiert, bekommt Fehler, die wie
Programmfehler aussehen und keine sind: Knöpfe fehlen, Bereiche sind nicht
da. Genau das ist hier schon passiert.

Damit es auffällt, zeigt die Oberfläche beide Versionen und warnt, wenn sie
auseinanderlaufen. Das funktioniert aber nur, wenn die Nummer in ``app.js``
auch wirklich mit ``web/VERSION`` mitgezogen wird — dafür sind diese Tests
da.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _oberflaechen_version() -> str:
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    treffer = re.search(r"OBERFLAECHE_VERSION\s*=\s*'([^']+)'", js)
    assert treffer, "OBERFLAECHE_VERSION fehlt in app.js"
    return treffer.group(1)


def test_oberflaeche_und_backend_tragen_dieselbe_version():
    """Sonst warnt die Seite grundlos — oder schlimmer: gar nicht."""
    datei = (ROOT / "web" / "VERSION").read_text(encoding="utf-8").strip()

    assert _oberflaechen_version() == datei, (
        f"app.js sagt {_oberflaechen_version()}, web/VERSION sagt {datei}. "
        f"Beim Ausliefern muessen beide mitgezogen werden."
    )


def test_die_version_ist_eine_richtige_nummer():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _oberflaechen_version())


def test_die_oberflaeche_warnt_bei_ungleichen_versionen():
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert "version-warnung" in js, "ohne Warnbalken merkt es wieder niemand"
    assert "versionKleiner(" in js, "die Anleitung muss zur Richtung passen"


def test_versionsvergleich_zaehlt_zahlen_und_nicht_zeichen():
    """1.10 ist neuer als 1.9 — als Text verglichen waere es andersherum."""
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    block = re.search(r"function versionKleiner\(a, b\) \{(.*?)\n\}", js, re.DOTALL)

    assert block, "versionKleiner fehlt"
    assert "Number(" in block.group(1), "es muss zahlenweise verglichen werden"
