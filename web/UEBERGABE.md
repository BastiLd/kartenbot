# Übergabe — Kartenbot Web

Stand: 7. August 2026, Version 1.4.0. Diese Datei ist für eine neue Sitzung
gedacht: Sie sagt, wo alles liegt, was fertig ist, was als Nächstes ansteht
und welche Fallen es gibt.

**Stufe 5 ist damit vollständig** — Schritte 1 bis 10 sind erledigt.

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

**Missionsbereich** (Stufe 5, Punkt G):

- Umschalter oben: grün **Helden** (Karten der Spieler), rot **Schurken**
  (Missionen und Bosse). Die Wahl bleibt gespeichert.
- 20 Gegner aus 5 Operationen, filterbar nach Operation und danach, ob Boss
  oder kleiner Gegner. Bilder werden **über die Nummer im Dateinamen**
  zugeordnet, nicht über den Namen — die Namen stimmen teils nicht.
- Missionsgegner lassen sich **auch testen**: Sie sind ganz normale Karten
  und treten gegen alle Heldenkarten an. Bearbeiten geht nicht, sie stehen
  fest im Bot.
- Die Einordnung richtet sich nach der **Rolle**: Ein Boss soll die meisten
  Kämpfe gewinnen (55–80 %), ein Gegner der frühen Wellen soll fallen
  (10–40 %). Beide an den 50 % der Helden zu messen wäre Unsinn — Maestro
  kommt auf 75 % und ist damit genau richtig, nicht „zu stark".

**Statistikseite aufgeräumt**: Oben vier Kennzahlen, dann der Tagesverlauf
(der fehlte ganz, obwohl er längst berechnet wurde), Helden und Siegquoten,
Uhrzeit. Alles Weitere steckt unter „Mehr Zahlen". Wer es genauer braucht,
kommt über einen Knopf zum alten Dashboard — die Adresse steht in den
Einstellungen (`dashboard.url`, derzeit `http://192.168.68.10:7859/`).

**Zweites KI-Modell und KI-Beurteilung** (Stufe 5, Schritte 4–5):

- Eigene Einstellung `ollama.model_kampf` mit **eigenem Modell-Finder**. Der
  prüft etwas anderes als der alte: drei Kampflagen, bei denen jeweils genau
  eine Antwort richtig ist. Wer zwei davon trifft, gilt als brauchbar — bei
  einer einzigen Frage käme Raten zu oft durch.
- Knopf **„Von der KI beurteilen lassen"** unter jedem Testlauf-Ergebnis.
  Das Ergebnis steht in `card_testruns.ki_text`.

**Gegner-Versionen** (Stufe 5, Schritt 7):

- Anlegen, bearbeiten, kopieren, löschen unter Einstellungen → Gegner-Versionen.
- „Standard" ist fest eingebaut, hat Fehlerquote 0 und lässt sich nicht
  löschen. Solange er gilt, spielt der Bot **exakt** wie vorher — bei
  Fehlerquote 0 wird nicht einmal gewürfelt.
- Die **Fehlerquote** wirkt schon: Mit ihr greift der Bot absichtlich daneben.
  Angesetzt in `_choose_bot_attack_index`, an genau einer Stelle.
- Gewichte und Lernstand sind in der Tabelle vorbereitet, aber noch ohne
  Wirkung — dort landet später das Gelernte.

**Auswahl der Gegner-Version im Discord** (Stufe 5, Schritt 8):

- Wer im `/kampf` auf „Bot" geht, wird gefragt, wie der Gegner spielen soll —
  eine Auswahlliste mit der Beschreibung als zweite Zeile. Keine Knöpfe:
  Discord kann nur unter einer Option eine Erklärung setzen.
- **Gefragt wird nur, wenn es etwas zu entscheiden gibt.** Solange es allein
  „Standard" gibt, läuft der Kampfstart Klick für Klick wie vorher.
- Die Wahl wird über `setze_gegner_version` **vor** `init_with_buffs` gesetzt;
  dort wird nur nachgeladen, was fehlt. Damit gilt jetzt auch die
  Einstellung **pro Server** — beim Kampfstart ist die Gilde bekannt.
- Die Version steht in der Sitzung. Ohne das spielte der Bot nach einem
  Neustart mitten im Kampf plötzlich wieder „Standard".

**Lernen aus echten Kämpfen** (Stufe 5, Schritt 9):

- `services/lernen.py` bestimmt die vier Gewichte aus `battle_moves`.
- Gemessen wird der **Abstand zum Zufall**, nicht die Häufigkeit: Standen
  vier Angriffe zur Wahl und zwei betäuben, fiele bei blindem Raten in der
  Hälfte der Fälle die Wahl auf einen Betäuber. Wird öfter betäubt, ist das
  eine Vorliebe. Sonst gewänne schlicht, was auf den meisten Karten steht.
- Nur gewonnene Kämpfe, nur Menschen (`ist_bot = 0`). Von den Zügen des Bots
  zu lernen hiesse, ihm seine eigenen Vorlieben zu bestätigen.
- Übersprungen: Züge ohne Wahl, Kategorien unter 30 Gelegenheiten. Der
  Faktor ist auf halb bis doppelt gedeckelt.
- **Wirksam wird das im Testlauf, nicht im Discord.** Der echte Kampf
  benutzt `_score_bot_attack_choice` in `bot.py` — eine andere Bewertung.
  Der Testlauf-Dialog hat dafür eine Auswahl; Versionen ohne Gelerntes
  stehen gar nicht erst drin.

**KI als Gegner** (Stufe 5, Schritt 10):

- `services/ki_gegner.py` fragt vor jedem Zug das Sprachmodell. Ein
  **einzelner** Kontrollkampf, kein Testlauf: Jeder Zug ist eine Anfrage.
- Der Kampf endet immer. Schweigt das Modell, schreibt es Unsinn oder
  stürzt die Anfrage ab, entscheidet die eingebaute Bewertung — und das
  steht im Protokoll, damit niemand ein Regelergebnis für eine Leistung
  des Modells hält.
- Läuft auf der **Website** (`web/app/kikampf.py`) in einem eigenen Faden.
  Anders als beim Testlauf gibt es keinen Grund, im Bot zu rechnen: Es ist
  ein Kampf, nicht zehntausend — und der Zugang zum Modell liegt hier.

680 Tests grün: `.venv/Scripts/python.exe -m pytest -q`

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

### Wie die Gegner-Versionen gebaut sind

| Teil | Wo |
|---|---|
| Bot-Seite | `services/bot_versions.py` — `aktive()`, `waehle_mit_fehlerquote()` |
| Website-Seite | `web/app/gegnerversionen.py` — anlegen, ändern, kopieren, löschen |
| Tabellen | `bot_versions`, `bot_version_aktiv` |
| Wirkung im Kampf | `bot.py`, Ende von `_choose_bot_attack_index` |

**Neue Spalten in bestehenden Tabellen brauchen eine Nachrüstung.**
`CREATE TABLE IF NOT EXISTS` legt nichts an, wenn die Tabelle schon steht —
die Spalte fehlt dann bei jedem, der die Seite vorher benutzt hat. Dafür
gibt es `_NACHRUESTEN` in `web/app/schema.py` **und** in
`services/web_jobs.py`; beide Listen müssen zusammenpassen.

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

Stufe 5 ist abgeschlossen. Offen sind nur noch die Punkte, die schon vorher
als „wäre als Nächstes sinnvoll" notiert waren:

### Die Züge der Bosse in Missionen mitschreiben

Die einzige bekannte Lücke der Zug-Mitschrift. Ihre Auswahl steckt in den
Boss-Hooks und ist an jeder Stelle anders. Die Spielerzüge in Missionen sind
vollständig da.

### Gelerntes auch im echten Kampf wirksam machen

Heute wirken die gelernten Gewichte nur im Testlauf, weil der echte Kampf mit
`_score_bot_attack_choice` in `bot.py` eine **andere** Bewertung benutzt als
`evaluate_move` in der Simulation. Beide zusammenzuführen wäre der saubere
Weg — das ist aber ein Eingriff mitten in den laufenden Kampf und gehört
sorgfältig gemacht, mit einem Testlauf davor und danach.

### Modi und Gesamtübersicht beim Testlauf

Die **Modi** `light` und `max` aus `simulation/modes.py` zuschaltbar machen
(heute rechnet er immer mit dem echten Spielstand), und eine
**Gesamtübersicht** über alle Karten. `queries.card_testruns()` kann dafür
schon ohne Kartennamen abgefragt werden.

### Weniger Anfragen im Kontrollkampf

Heute wird jeder Zug einzeln gefragt. Wer das billiger will: gleiche Lagen
zwischenspeichern, oder das Modell mehrere Züge im Voraus planen lassen.

---

## Die Engine

```
simulation/engine.py    simulate_duel, simulate_matchup,
                        simulate_full_round_robin, aggregate_hero_results
                        simulate_duel nimmt jetzt gewichte= und strategie_a=
simulation/strategy.py  Strategy-Protokoll, OptimalStrategy, AverageStrategy,
                        build_strategy(name, rng, average_mistake_rate, gewichte),
                        evaluate_move, STANDARD_GEWICHTE, normalisiere_gewichte
simulation/modes.py     apply_mode_to_cards
simulation/loader.py    Karten laden
```

### Wie das Lernen gebaut ist

| Teil | Wo |
|---|---|
| Auswertung | `services/lernen.py` — `auswerten()`, `beschreibe()` |
| Website-Seite | `web/app/gegnerversionen.py` — `lerne()`, `lernstoff()`, `gewichte_vergessen()` |
| Ablage | `bot_versions.gewichte_json` und `lernstand_json` |
| Wirkung | `simulation/strategy.py:evaluate_move` über `gewichte` |
| Oberfläche | `web/static/app.js` — Block unter jeder Version, Auswahl im Testlauf |

### Wie der KI-Gegner gebaut ist

| Teil | Wo |
|---|---|
| Entscheiden | `services/ki_gegner.py` — `KIGegner`, `frage_bauen`, `antwort_lesen` |
| Kampf | `services/ki_gegner.py:kontrollkampf` |
| Website-Seite | `web/app/kikampf.py`, Endpunkt `/api/karten/kikampf` |
| Modellzugang | `web/app/ollama.py:generate_sync` (synchron, für den eigenen Faden) |

**Warum die Anfrage hereingereicht wird** (`frage`) statt fest verdrahtet:
So läuft das Modul im Test ohne Netz und ohne Ollama — jeder Test dort gibt
seine eigene Antwort vor, auch eine abstürzende.

Der Missionsbereich (Punkt G) ist fertig — siehe oben.

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

Erwartung: sauberer Stand, 680 Tests grün, `main` und
`feature/web-dashboard` auf demselben Commit.

**Und das Wichtigste vor jeder Fehlersuche:** Steht in der Seitenleiste
„Oberfläche vX · Backend vY" oder ein Balken oben auf der Seite, ist nichts
kaputt — dann wurde nur einer der drei Teile aktualisiert. Genau dieser Fall
hat schon einen halben Tag gekostet: Das Backend meldete die neue Version,
die alte `app.js` schrieb sie ungeprüft in die Seitenleiste, und es sah aus,
als fehlten Knöpfe im Programm. Seit 1.3.0 trägt `app.js` ihre eigene Nummer
und vergleicht.
