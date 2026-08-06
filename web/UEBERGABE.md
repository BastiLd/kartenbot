# Übergabe — Kartenbot Web

Stand: 6. August 2026, Version 1.2.0. Diese Datei ist für eine neue Sitzung
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

**Stufe 5, Schritte 1–3** ([PLAN-STUFE-5.md](PLAN-STUFE-5.md)):

- Angriffe bearbeiten mit echter Prüfung durch `card_validation.py`
- Kacheln, Vollbild mit Zurück-Pfeil, Discord-Vorschau
- Gegnernamen an die Kartenbilder angeglichen, Bilder aufbereitet unter
  `web/static/missionen/`
- **Testlauf**: eine Karte gegen alle anderen, mit Siegquote, Rundenzahl,
  jeder einzelnen Paarung und einer Einordnung in Worten. Voreingestellt
  laufen **zwei Durchgänge** — einmal bestmöglich gespielt, einmal mit
  Fehlern. Erst der Vergleich zeigt, ob eine Karte nur bei perfektem Spiel
  stark ist. (Iron-Man etwa: 10,9 % bestmöglich, 21,2 % mit Fehlern — er
  lebt davon, dass Gegner Fehler machen.)

**Zug-Mitschrift** (Stufe 5, Schritt 6 — vorgezogen):

- Bei jedem Zug wird festgehalten, wie die Lage war: Lebenspunkte beider
  Seiten, aktive Effekte, Abklingzeiten, welche Angriffe zur Wahl standen,
  die getroffene Wahl und die **Bedenkzeit**.
- Gilt für alle drei Kampfarten: gegen Mitspieler, gegen den Bot (dessen
  eigene Züge inklusive, markiert mit `ist_bot`) und Missionen.
- **Voreingestellt aus.** Der Schalter steht in den Einstellungen unter
  „Kämpfe"; darüber zeigt die Seite, wie viel schon zusammengekommen ist.

532 Tests grün: `.venv/Scripts/python.exe -m pytest -q`

### Wie die Zug-Mitschrift gebaut ist

| Teil | Wo |
|---|---|
| Erfassen und Speichern | `services/move_log.py` |
| Tabelle | `battle_moves` — Kennzahlen als Spalten, die Lage in `lage_json` |
| Schalter | Einstellung `mitschrift.aktiv`, Voreinstellung aus |
| Zähler | `/api/mitschrift`, angezeigt über dem Schalter |

**Die Falle, die sonst Tage kostet:** `CombatRunner` wird **nur von der
Simulation** benutzt, nicht vom echten Kampf. Wer dort mitschreibt, bekommt
kein einziges echtes Spiel zu fassen. Im echten Spiel fällt ein Zug an drei
Stellen in `bot.py`: `BattleView.execute_attack` (gegen Mitspieler und gegen
den Bot), `execute_bot_attack` (der Zug des Bots) und
`MissionBattleView.execute_attack`.

**Warum in zwei Schritten** (`merke_lage` … `schreibe_gemerkten_zug`):
Zwischen dem Klick und der Ausführung liegen Prüfungen, die den Zug noch
ablehnen können — Abklingzeit, Sperre, erzwungene Landung. Wer beim Klick
schreibt, sammelt Züge ein, die nie stattfanden. Deshalb wird erst gemerkt
und nur geschrieben, wenn der Zug wirklich durch ist. Der Aufruf steht
jeweils **vor** der Kampfende-Prüfung, sonst fehlte ausgerechnet der
entscheidende letzte Zug.

**Die Bedenkzeit** kommt aus einem Zeitstempel, den der Setter von
`current_turn` in `BaseBattleView` setzt — deshalb ist das eine Property
und kein einfaches Attribut. So musste keiner der vielen Zugwechsel
angefasst werden. In Missionen bleibt `current_turn` allerdings durchgehend
beim Spieler; dort zählt stattdessen die Zeit seit dem letzten
mitgeschriebenen Zug.

**Was bewusst nicht gespeichert wird**, weil es später ableitbar ist:
Wiederholung nach einem Fehlschlag (steht im vorherigen Zug desselben
Spielers) und ob die Karte kurz vorher geändert wurde (ergibt sich aus
`erstellt_am` und `card_override_history`).

**Bekannte Lücke:** Die Züge der Bosse in Missionen werden noch nicht
mitgeschrieben — ihre Auswahl steckt in den Boss-Hooks und ist an jeder
Stelle anders. Die Spielerzüge in Missionen sind vollständig da.

### Wie der Testlauf gebaut ist

| Teil | Wo |
|---|---|
| Rechnen | `services/card_testrun.py` — `laufen()` je Spielweise, `laufen_mehrfach()` klammert die Durchgänge; nutzt `simulate_duel` aus `simulation/` |
| Auftrag | Art `cards.testlauf`, in `web/app/jobs.py` und `bot.py` (`_run_card_testrun`) |
| Ergebnis | Tabelle `card_testruns` — Kennzahlen als Spalten, Paarungen in `ergebnis_json` |
| Prüfung der Eingaben | `web/app/karteneditor.py` (`pruefe_testlauf`) |
| Oberfläche | `web/static/app.js` — Panel „Testlauf" in der Einzelkartenansicht |

**Warum die Duelle einzeln aufgerufen werden** statt über `simulate_matchup`:
Der Bot hat nur einen Faden. Zwischen zwei Duellen wird kurz abgegeben, sonst
stünde das Spiel minutenlang still. Das schützt nebenbei den globalen
Zufallsgenerator — `simulate_duel` setzt ihn auf einen festen Startwert und
stellt ihn danach wieder her, und dazwischen wird nie abgegeben. Ein Test
belegt, dass dabei Zahl für Zahl dasselbe herauskommt wie bei
`simulate_matchup`.

---

## Was als Nächstes ansteht

### Schritt 4: Zweites KI-Modell für den Testlauf

Eines fürs **Prüfen** (Server-Analyse, wie bisher), eines für den
**Testlauf** — mit eigenem Modell-Finder. Der vorhandene testet auf eine
Verständnisaufgabe; der neue muss eine Kampflage lesen und eine sinnvolle
Entscheidung treffen können. Also eigene Testaufgabe, eigene Bewertung.
Anzusetzen bei `web/app/ollama.py` (`find_model`) und den Einstellungen.

Die Engine dafür steht bereit:

```
simulation/engine.py    simulate_duel, simulate_matchup,
                        simulate_full_round_robin, aggregate_hero_results
simulation/strategy.py  Strategy-Protokoll, OptimalStrategy, AverageStrategy,
                        build_strategy(name, rng, average_mistake_rate),
                        evaluate_move  <- hier setzt später das Lernen an
simulation/modes.py     apply_mode_to_cards
simulation/loader.py    Karten laden
```

### Danach

5. KI beurteilt die Testlauf-Ergebnisse in Worten (braucht 4). Die Zahlen
   liegen fertig in `card_testruns.ergebnis_json`; die regelbasierte
   Einordnung (`card_testrun.einordnen`) bleibt als Rückfall daneben stehen.
6. **Zug-Mitschrift** — sollte früh kommen, siehe unten
7. Gegner-Versionen („Standard", „Schwer") auf der Website
8. Auswahl im Discord beim Kampfstart
9. Lernen aus echten Kämpfen
10. KI als Gegner (zuletzt, sehr langsam)

Beim Testlauf selbst wäre als Nächstes sinnvoll: die **Modi** `light` und
`max` aus `simulation/modes.py` zuschaltbar machen (heute rechnet er immer
mit dem echten Spielstand), und eine **Gesamtübersicht** über alle Karten.
`queries.card_testruns()` kann dafür schon ohne Kartennamen abgefragt werden.

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

**Aufträge laufen einer nach dem anderen.** `web_job_loop` holt immer nur
einen. Ein Testlauf rechnet Minuten, ein Verlaufs-Scan Stunden — solange
bleibt alles andere (Rollen vergeben, Kartenänderung übernehmen) in der
Warteschlange stehen. Das sieht aus, als täte die Website nichts. Ein Blick
in die Auftragsliste zeigt, woran es liegt.

**Lange Shell-Zeichenketten mit Umlauten und Anführungszeichen** scheitern am
Zitieren. Für größere Textblöcke die Datei-Werkzeuge nehmen, nicht `bash`
mit Heredoc.

**`Get-Content` + `Set-Content` zerstören Umlaute in Quelldateien.**
PowerShell 5.1 liest ohne `-Encoding` in ANSI: Aus „für" wird „fÃ¼r", und
`Set-Content -Encoding utf8` friert das mitsamt einem BOM ein. Quelldateien
deshalb **nie** über die Shell umschreiben. Passiert es doch, ist der
Rückweg: BOM abschneiden, als Windows-1252 kodieren, als UTF-8 lesen.

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
