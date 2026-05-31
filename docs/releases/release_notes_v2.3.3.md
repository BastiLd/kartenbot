# Release v2.3.3 – Wartung & Aufräumen

Dieses Release enthält keine Spielinhalte-Änderungen. Es geht um Code-Qualität,
Aufräumen und einen ersten Schritt, den großen `bot.py`-Monolithen zu entkoppeln.
Verhalten im Spiel bleibt unverändert.

## 🧹 Aufräumen
- Toten Doppelcode entfernt: Die `Einladen`-Auswahl (`InviteUserSelectView` /
  `InviteUserSelect`) war doppelt definiert; die erste, fehlerhafte Variante
  (ein nie gesetztes Feld hätte beim Benutzen einen Fehler ausgelöst) ist raus.
- Repo aufgeräumt: große Binärdateien (`MISSIONEN.pdf`, `simulation_results.xlsx`)
  und die leere `bot_token.txt` werden nicht mehr mitgetrackt.
- Doku sortiert: Release Notes und Update-Texte liegen jetzt unter
  `docs/releases/`, Entwickler-Notizen unter `docs/dev/`.

## 🧱 Technik
- Neuer Baustein `services/coercion.py`: die reinen Typ-/Wert-Helfer
  (Zahlen-/Listen-/Dict-Umwandlung, Schadensbereiche) sind aus `bot.py`
  ausgelagert. `bot.py` und `services/combat_runner.py` teilen sie sich jetzt
  ohne Umweg über das Bot-Modul.
- Erster Schritt, um die großen Kampf-Views später sauber aus `bot.py`
  herausziehen zu können.
- `bot.py` ist dadurch um ~250 Zeilen kleiner.

## ✅ Qualität
- Neuer Unit-Test `tests/test_coercion.py`.
- Komplette Test-Suite grün: 365 passed, 428 subtests.

## Server-Update
Anhaken (bleiben erhalten): `kartenbot.db`, `.env`, `bot_token.txt`, `bot.log`,
`Simulation Files/`. Code-Dateien nicht anhaken.
