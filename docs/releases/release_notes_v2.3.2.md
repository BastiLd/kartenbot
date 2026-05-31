## ⚔️ Kämpfe
- Schadens-Anzeige korrigiert: Bei aktiver Verstärkung steht jetzt der echte Ausgangsschaden da (z. B. Schaden: 30, Verstärkung: 15, Zusammen: 45). Eine Schutz-Reduktion erscheint weiterhin separat als „Schutzwirkung: 45 → 23".
- Tarnung löst nur noch aus, wenn wirklich Schaden gemacht wird – Hochfliegen (Phase 1), Buffs, Heilungen und Schilde verbrauchen sie nicht mehr.
- Exo-Suit bringt sich nicht mehr selbst am Kampfanfang um.
- Bricht ein Kampf durch einen Fehler ab, schreibt der Bot jetzt direkt in den Thread, was schiefgelaufen ist.

## 📜 Missionen & Belohnungen
- Staub pro Welle (1, 2, 3 und Boss) ist jetzt frei einstellbar (`mission_dust_config.py`) – an/aus und Menge, alles auskommentiert erklärt.
- Gamma-Mutant zeigt in der Vorschau wieder alle 4 Fähigkeiten.

## 🗃️ Sammlung & Units
- In `/sammlung` werden Units genau wie Staub angezeigt (nur die Anzahl).
- Beim Boss-K.o. steht in der Wiederbeleben-Frage, wie viele Units du aktuell hast und wie viele du danach noch hättest.

## 🛠️ Tools & Threads
- `/karte-geben` und „Grant Card" laufen wieder zuverlässig in Single und Multi (inkl. „Fertig"-Button).
- In Missions- und Kampf-Threads kommt nicht mehr die Frage „Willst du das Intro anzeigen?".

## Server-Update
Anhaken (bleiben erhalten): `kartenbot.db`, `.env`, `bot_token.txt`, `bot.log`, `Simulation Files/`. Code-Dateien nicht anhaken.
