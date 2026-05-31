"""Formatiert den `karten = [...]`-Block in karten.py menschenfreundlich um.

- Pro Karte ein Trenner-Kommentar ``# ===== Name (Seltenheit) =====`` + Leerzeile.
- Schluesselreihenfolge je Karte: name, seltenheit, hp, beschreibung, bild, attacks, Rest.
- AENDERT KEINE DATEN: laedt die echte `karten`-Liste und schreibt sie nur neu formatiert.

Header (Konstanten) und der Normalisierungs-Loop am Ende bleiben unveraendert.
Aufruf:  python scripts/reformat_karten.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from pprint import pformat

ROOT = Path(__file__).resolve().parents[1]
KARTEN = ROOT / "karten.py"

PREFERRED_KEY_ORDER = ["name", "seltenheit", "hp", "beschreibung", "bild", "attacks"]

GUIDE = (
    "# ============================================================\n"
    "#  KARTEN-LISTE\n"
    "#  ------------------------------------------------------------\n"
    "#  Jede Karte ist ein Block, getrennt durch eine Kommentarzeile\n"
    "#    # ===== Name (Seltenheit) =====\n"
    "#  Reihenfolge je Karte: name, seltenheit, hp, beschreibung,\n"
    "#  bild, attacks. Die ERSTE Attacke (is_standard_attack) ist im\n"
    "#  Kampf immer der Button oben links.\n"
    "#  Zum Aendern einfach die Werte anpassen - Kommas/Klammern so\n"
    "#  lassen wie sie sind.\n"
    "# ============================================================\n"
)


def _reorder(card: dict) -> dict:
    out: dict = {}
    for key in PREFERRED_KEY_ORDER:
        if key in card:
            out[key] = card[key]
    for key, value in card.items():
        if key not in out:
            out[key] = value
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import karten as karten_module  # noqa: E402

    cards = karten_module.karten
    text = KARTEN.read_text(encoding="utf-8")

    start = text.index("karten = [")
    footer_idx = text.index("for card in karten:")
    pre = text[:start].rstrip("\n") + "\n\n\n"
    footer = text[footer_idx:]

    buf = io.StringIO()
    buf.write(pre)
    buf.write(GUIDE)
    buf.write("karten = [\n")
    for card in cards:
        name = str(card.get("name") or "Unbenannt")
        rarity = str(card.get("seltenheit") or "?")
        buf.write(f"    # ===== {name} ({rarity}) =====\n")
        body = pformat(_reorder(card), width=120, sort_dicts=False)
        for line in body.splitlines():
            buf.write("    " + line + "\n")
        buf.write("    ,\n\n")
    buf.write("]\n\n\n")
    buf.write(footer)

    KARTEN.write_text(buf.getvalue(), encoding="utf-8", newline="\n")
    print(f"karten.py neu formatiert ({len(cards)} Karten).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
