# 🎯 Update v2.3.1

Bugfixes und ein neues Einstell-System für Mission-Belohnungen.

## Behoben
- **`/karte-geben` & Grant Card**: Der Absturz beim Multi-Modus (`MultiCardSelectView` nicht gefunden) ist behoben — Single und Multi laufen wieder, inklusive „Fertig".
- **Schadens-Anzeige im Kampf**: Bei aktiver Verstärkung wird jetzt der korrekte Ausgangsschaden angezeigt (z. B. `Schaden: 30`, `Verstärkung: 15`, `Zusammen: 45`). Eine Schutz-Reduktion erscheint weiterhin separat in der `Schutzwirkung: 45 → 23`-Zeile.
- **Threads**: In Missions- und PVP-Threads erscheint nicht mehr die Frage „Willst du das Intro für dich anzeigen?".
- **Kampf-Abbruch durch Fehler**: Wenn ein Kampf wegen eines Fehlers abbricht, schreibt der Bot jetzt direkt eine Erklärung in den Thread (mit kurzer technischer Ursache); die vollständigen Logs gehen weiterhin an Basti.
- **/sammlung**: Units zeigen jetzt „Aktuell: X" und – falls möglich – „Danach: X-… (nach einer Wiederbelebung)".

## Neu
- **Mission-Staub einstellbar pro Welle** (`mission_dust_config.py`): Für Welle 1, 2, 3 und Boss (Welle 4) lässt sich getrennt einstellen, ob es Staub gibt (`enabled`) und wie viel (`amount`). Alles gut auskommentiert erklärt. Plus separat schaltbarer Bonus für bereits besessene Belohnungskarten.

## Bereits in v2.3.0 enthalten (beim Update aktiv)
- Gamma-Mutant zeigt alle 4 Fähigkeiten in der Vorschau.
- Exo-Suit (Schwerer Kampf-Mech) begeht keinen Selbstmord mehr zu Kampfbeginn.
- Tarnung wird nur noch bei tatsächlichem Schaden (> 0) verbraucht.

## Server-Update
Beim Aktualisieren **anhaken** (bleiben erhalten): `kartenbot.db`, `.env`, `bot_token.txt`, `bot.log`, `Simulation Files/`. **`mission_dust_config.py` NICHT anhaken**, wenn du die neuen Standardwerte willst — bzw. anhaken, wenn du deine eigenen Werte behalten möchtest. Code-Dateien (`bot.py`, `services/`, `botcommands/` …) **nicht** anhaken.
