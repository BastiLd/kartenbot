# Übergabe — Kartenbot Web

Stand: 6. August 2026, Version 1.1.0. Diese Datei ist für eine neue Sitzung
gedacht: Sie sagt, wo alles liegt, was fertig ist, was als Nächstes ansteht
und welche Fallen es gibt.

---

## Wo was liegt

| Was | Wo |
|---|---|
| Arbeitsverzeichnis | `C:\Users\basti\Documents\kartenbot-web` (Branch `feature/web-dashboard`) |
| Haupt-Repo | `C:\Users\basti\Documents\BOT\kartenbot` (Branch `main`) |
| GitHub | `BastiLd/kartenbot` — `main` und `feature/web-dashboard` sind gleich |
| WebHafen-Repo | `BastiLd/MFU-TEST` (nicht der Ordner `webhafen`!) |
| Python | `C:/Users/basti/Documents/BOT/kartenbot/.venv/Scripts/python.exe` |

**Auf ZimaOS (192.168.68.10):**

| Dienst | Adresse |
|---|---|
| Website | `http://192.168.68.10:8012` |
| WebHafen-Verwaltung | `:8010` |
| Portainer | `:9000` |
| Backend (nur intern) | `:8090` |
| Ollama | `:11434` |
| Bot-Arbeitsordner | `/DATA/AppData/homelab-discord-bot-manager/config/servers/local-d2a651fa/workspace` |

---

## Wie ausgeliefert wird

Drei getrennte Teile — **wer nur einen aktualisiert, bekommt Fehler, die wie
Programmfehler aussehen, aber keine sind.**

1. **Bot** — im Bot-Manager von GitHub holen, **neu starten**.
2. **Backend** — Portainer → Stacks → `kartenbot-web` → *Update the stack*.
3. **Oberfläche** — `web/static/` als ZIP packen, in WebHafen hochladen
   („Ordner vorher leeren"), dann Strg+F5.

Faustregel: Änderungen unter `web/app/` brauchen 2, unter `web/static/`
brauchen 3, in `bot.py` oder `services/` brauchen 1.

---

## Was fertig ist

**Stufen 1–4 des Ausbauplans** ([PLAN-AUSBAU.md](PLAN-AUSBAU.md)):

- Kanal-Freigaben, Units-Protokoll, Einladungen
- Mehrere Personen auf einmal, Rückgängig überall, Handy-Ansicht
- Papierkorb (30 Tage), Datenbank-Sicherung, CSV-Bericht, Selbsttest
- Karten-Editor inklusive Angriffe, Kachelansicht, Vollbild, Vorschau

**Stufe 5, Schritte 1–2** ([PLAN-STUFE-5.md](PLAN-STUFE-5.md)):

- Angriffe bearbeiten mit echter Prüfung durch `card_validation.py`
- Kacheln, Vollbild mit Zurück-Pfeil, Discord-Vorschau
- Gegnernamen an die Kartenbilder angeglichen, Bilder aufbereitet unter
  `web/static/missionen/`

454 Tests grün: `.venv/Scripts/python.exe -m pytest -q`

---

## Was als Nächstes ansteht

### Schritt 3: Testlauf (das ist der Auftrag)

Wie stark ist eine Karte wirklich? Die Simulation lässt sie gegen alle
anderen antreten und liefert Siegquote, Rundenzahl und die Paarungen, die
auffallen.

**Die halbe Arbeit ist schon da.** Unter `simulation/` liegt eine fertige
Engine (797 Zeilen):

```
simulation/engine.py    simulate_duel, simulate_matchup,
                        simulate_full_round_robin, aggregate_hero_results
simulation/strategy.py  Strategy-Protokoll, OptimalStrategy, AverageStrategy,
                        build_strategy(name, rng, average_mistake_rate),
                        evaluate_move  <- hier setzt später das Lernen an
simulation/modes.py     apply_mode_to_cards
simulation/loader.py    Karten laden
```

Zu bauen:
1. Auftragsart `cards.testlauf` in `web/app/jobs.py` (KINDS) **und** in
   `bot.py` in `_run_web_job` — der Bot rechnet, nicht die Website.
2. Fortschritt melden über `web_jobs.update_progress`, Abbruch über
   `is_cancelled` — beides gibt es schon, siehe `scan.history`.
3. Ergebnis in einer neuen Tabelle ablegen, Muster: `scan_runs`.
4. In der Kartenansicht ein Knopf „Testlauf" mit Fortschritt und Ergebnis.

### Danach

4. Zweites KI-Modell für den Testlauf, mit eigenem Modell-Finder
5. KI beurteilt die Ergebnisse in Worten
6. **Zug-Mitschrift** — sollte früh kommen, siehe unten
7. Gegner-Versionen („Standard", „Schwer") auf der Website
8. Auswahl im Discord beim Kampfstart
9. Lernen aus echten Kämpfen
10. KI als Gegner (zuletzt, sehr langsam)

Dazu offen: **Missionsbereich** mit Umschalter oben (grün = Karten, rot =
Missionen), Filter nach Missionen, Bossen, kleinen Gegnern und den
Dreiergruppen. Bilder liegen bereit.

---

## Fallen, die schon Zeit gekostet haben

**Discord-IDs sind zu groß für JavaScript.** Aus `965593518745731152` wird im
Browser stillschweigend `...731200`. IDs müssen als **Text** aus dem Backend
kommen. `database.fetch_all()` wandelt sie zentral um (`_ID_SPALTEN`); wer
neue Antworten baut, muss `str(uid)` nicht vergessen.

**Kartenänderungen wirken ohne Neustart** — aber nur, weil die Karten-*Objekte*
an Ort und Stelle geändert werden. `bot.py` legt einen `CardCatalog` darum,
und der macht eine **flache Kopie**: Die Liste zu *ersetzen* käme dort nicht
an, die Objekte zu *ändern* schon. Im Bot heißt die Liste `RAW_KARTEN`, nicht
`karten`.

**Foren haben kein `history()`.** `discord.ForumChannel` kennt die Methode
nicht — seine Beiträge sind Threads. Siehe `_lesbare_quellen()` in
`services/history_scan.py`.

**Mitglieder von Discord sind `{user: {id, ...}, nick}`** — nicht `{id, name}`.
Das hat schon einmal „undefined" in ein Suchfeld geschrieben.

**Aufträge bleiben nach einem Neustart hängen**, wenn niemand aufräumt.
`web_jobs.reset_orphaned()` läuft beim Start; bei neuen Auftragsarten daran
denken.

**Die Bot-Datenbank hat nur eine Schreibverbindung.** Massenaktionen laufen
deshalb der Reihe nach, nicht gleichzeitig.

**Lange Shell-Zeichenketten mit Umlauten und Anführungszeichen** scheitern am
Zitieren. Für größere Textblöcke die Datei-Werkzeuge nehmen, nicht `bash`
mit Heredoc.

---

## Wie der Nutzer arbeitet

- Antwortet auf Deutsch, mag es direkt und ohne Umschweife.
- **Fragen bitte immer als Mehrfachauswahl** stellen, auch bei Ja/Nein.
- Will Umlaute im sichtbaren Text — kein „ae", „oe", „ue".
- Keine Subagents und keine Workflows: Er hat begrenzte Nutzung und hat
  ausdrücklich darum gebeten, solo zu arbeiten.
- Schickt gern Screenshots als PDF. Auslesen mit `pypdf` + `PIL`, dann in
  Abschnitte zerschneiden — als Ganzes ist es unlesbar.
- Er testet sofort und meldet zurück. Nach jedem Schritt eine ZIP mit
  `web/static/` schicken und klar sagen, welche der drei Teile neu geladen
  werden müssen.

---

## Vor dem Loslegen prüfen

```
cd C:/Users/basti/Documents/kartenbot-web
git status
git log --oneline -5
.venv-Python -m pytest -q
```

Erwartung: sauberer Stand, 454 Tests grün, `main` und
`feature/web-dashboard` auf demselben Commit.
