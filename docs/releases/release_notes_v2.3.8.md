# 🗂️ Update v2.3.8 — Standardangriff oben links & lesbarere karten.py

## 🎯 Standardangriff immer oben links
- **Hawkeye:** Der Standardangriff **„Pfeil"** sitzt jetzt im **ersten Slot (oben links)**.
  Bisher zwang eine Sonderbehandlung in `karten.py` ihn gezielt auf den zweiten Slot –
  diese Sonderlogik wurde entfernt (auch im Generator-Skript `materialize_button_styles.py`).
- **Alle 34 Karten geprüft:** Bei jeder Karte ist die `is_standard_attack`-Attacke jetzt
  an Position 1 (oben links). Hawkeye war die einzige Ausnahme.

## 📖 `karten.py` lesbarer gemacht (für Nicht-Programmierer)
- Jede Karte ist jetzt ein klar getrennter Block mit einer Überschriftszeile
  `# ===== Name (Seltenheit) =====` und einer Leerzeile dazwischen.
- Je Karte stehen die wichtigsten Felder **oben**: `name`, `seltenheit`, `hp`,
  danach `beschreibung`, `bild`, `attacks`.
- Kurze **Anleitung** als Kommentar am Anfang der Liste.
- **Reine Formatierung – keine Werte geändert** (per Datengleichheits-Check verifiziert).
- Neues Hilfsskript `scripts/reformat_karten.py`, das diese Formatierung reproduzierbar erzeugt.

Gesamte Testsuite grün (366 passed). Die Hawkeye-Regressionstests wurden auf die neue
Slot-Reihenfolge angepasst.
