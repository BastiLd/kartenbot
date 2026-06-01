# 🗂️ Update v2.3.14 — Kampf-Views aufgeräumt & testbar gemacht (D4)

Ein rein internes Update: keine Änderung am Spielgefühl, aber der Kampf-Code ist jetzt
deutlich klarer strukturiert und erstmals durch echte Tests abgesichert. Das macht
künftige Änderungen an Kämpfen sicherer und schneller.

## 🧪 Erstmals: Tests für die Kampf-Views
- Neues Test-Harness, das die beiden großen Kampf-Klassen (PvP `BattleView` und
  PvE `MissionBattleView`) **offline** baut und komplette Züge durchspielt — ohne
  laufenden Bot oder Discord.
- Charakterisierungs-Tests fixieren das aktuelle Kampf-Verhalten (Schaden, Zugwechsel,
  Cooldown, Speichern/Wiederherstellen) als Sicherheitsnetz.

## 🧹 Klarere Struktur (Audit D4)
- Neue gemeinsame Basisklasse **`BaseBattleView`**: der zuvor in beiden Views doppelt
  aufgebaute Kampf-State (HP-/Karten-Daten, Effekt-/Modifier-Maps) lebt jetzt an
  **einer** Stelle.
- 3-Schichten-Aufbau mit Wegweiser-Kommentar: `BattleMechanicsMixin` (geteilte
  Mechanik) → `BaseBattleView` (geteilter State) → PvP-/PvE-Subklassen (das genuin
  Unterschiedliche, inkl. Boss-Mechaniken).
- Bewusste Entscheidung: die ~700-zeilige `execute_attack`-Logik wurde **nicht**
  zwangsvereinheitlicht (PvP und PvE sind dort nur ~33 % gleich) — Klarheit vor
  Entdopplung.

## 🛡️ Kleinigkeit am Rande
- `.gitignore` deckt jetzt die SQLite-WAL-Sidecar-Dateien (`*.db-wal`/`*.db-shm`) ab,
  die seit der WAL-Umstellung in v2.3.13 entstehen.

## ✅ Qualität
- **391 Tests grün** (vorher 384) — die neuen View-Tests (`tests/view_harness.py`,
  `tests/test_battle_view_smoke.py`, `tests/test_mission_view_smoke.py`) inklusive.
- Karten weiterhin valide (34), `bot.py` importiert sauber.
