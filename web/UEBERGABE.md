# Übergabe — Kartenbot Web

Stand: 7. August 2026, Version 1.4.1 — ergänzt am 19./20. September 2026 um
Teil 1, Plan 1 (Designs) und Plan 2 (Level, Einladungen): Web 1.5.0,
Bot 2.5.0, beides noch nicht in `main`.
Diese Datei ist für eine neue Sitzung
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

**Schritt 0, sonst ist alles Weitere wirkungslos: `git push`.** Bot-Manager
und Portainer ziehen aus GitHub, nicht vom Entwicklungsrechner. Ein Commit,
der nur lokal liegt, kommt nirgends an — und im Bot-Manager steht dann
„Repository ist aktuell", weil es das ja auch ist. Beide Branches mitziehen:
`main` und `feature/web-dashboard` werden gleich gehalten.

Danach drei getrennte Teile — **wer nur einen aktualisiert, bekommt Fehler,
die wie Programmfehler aussehen, aber keine sind.**

1. **Bot** — im Bot-Manager von GitHub holen, **neu starten**.
2. **Backend** — Portainer → Stacks → `kartenbot-web` → *Update the stack*.
3. **Oberfläche** — `web/static/` als ZIP packen, in WebHafen hochladen
   („Ordner vorher leeren"), dann Strg+F5.

Zum Nachsehen, ob der Push angekommen ist: Im Bot-Manager steht unter
*Settings* der **Remote commit**. Steht dort nicht der neueste Commit, hilft
kein einziger Klick auf „Update".

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
- Läuft als **Auftrag im Bot** (`cards.kikampf`), genau wie der Testlauf.
- Der Kampf führt seine Schleife **selbst** über `CombatRunner` statt über
  `simulate_duel`. Zwei Gründe, beide zwingend: Zwischen zwei Zügen wird auf
  das Modell gewartet, das geht nur mit `await`. Und `simulate_duel` setzt
  den globalen Zufall auf einen festen Startwert — wer dazwischen abgibt,
  lässt einen echten Kampf im Spiel auf einem bekannten Startwert laufen.

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

## Teil 1, Plan 1: Alternative Karten-Designs (Bot 2.4.0, Web 1.5.0)

Stand 19. September 2026, gebaut nach `docs/dev/TEIL-1_PLAN-1_Designs.md` auf
dem Branch `opus/teil-1-designs-und-level` (Tags `teil1-start`,
`teil1-vor-schritt-3`, `teil1-vor-schritt-7`, `teil1-plan1-fertig`). Noch
**nicht** in `main`, `feature/bot` oder `feature/web-dashboard` übernommen.

Jeder Held kann bis zu drei Designs haben: `bild` (Design 1, immer frei),
`bild_2`, `bild_3`. Ein Design ist **nur ein Bild** — keine eigene
Sammlungskarte, keine anderen Werte. Mit den Iron-Man-Varianten
(`services/card_variants.py`) hat das nichts zu tun; beide Varianten teilen
sich Designs über den Grundnamen „Iron-Man".

| Teil | Wo |
|---|---|
| Bild-Links | `bild_2`/`bild_3` in `card_overrides` (Website), `AENDERBAR` in `services/card_store.py` **und** `web/app/karteneditor.py` — gleiche Liste, gleiche Reihenfolge |
| Prüfung | `services/card_validation.py` (Bot), `karteneditor.pruefe()` (Website): http(s), ≤ 500 Zeichen, Design 3 nur mit Design 2 |
| Freischaltung / Wahl | Tabellen `user_designs`, `user_design_wahl` (SQL in `services/db.py`, `DESIGN_TABLES_SQL`) |
| Logik | `services/designs.py` — `MAX_DESIGNS = 3` steht nur dort |
| Anzeige | `bot.py`: `_design_bild`, `_karte_mit_design`, `_design_hinweis`; Sammlung in `_build_owned_card_detail` |
| `/design` | `botcommands/design_view.py`, angemeldet in `player_commands.py` |
| `/design-geben`, `/design-entziehen` | `botcommands/design_admin.py`, angemeldet in `admin_commands.py` (versteckt per `default_permissions(administrator=True)`, geprüft per `is_admin`) |
| Website | Felder „Design 2/3" im Editor, Kennzeichen „N Designs", Umschalter in der Discord-Vorschau (`web/static/app.js`) |

**Regeln, die der Code einhält:**
- Freischalten geht **ohne** Bild-Link (Belohnungen gehen nicht verloren,
  solange Bilder fehlen). Sichtbar und wählbar wird ein Design erst mit Link
  **und** Freischaltung. Fehlt eins davon, gilt still Design 1.
- Im Kampf liegt das Design nur auf der **eigenen Kopie** der Kampfkarte
  (`_karte_mit_design`), nie auf `RAW_KARTEN`. Die Karte wird mit der
  Sitzung gespeichert, das Bild übersteht also einen Neustart. Bot- und
  Missionsgegner zeigen immer Design 1.
- Belohnungen zeigen, was man bekommt: eine Karte → normales Bild, ein
  Design (`/design-geben`, später Level/Einladung) → das Bild des Designs.
- Vor Kampf und Mission gibt es **keinen** Wechsel-Knopf: Die Auswahl-Views
  sind DurableViews, an denen nichts geändert werden sollte. Stattdessen die
  Textzeile „🎨 Design ändern: /design" — nur für Spieler mit wählbarem Design.

**Nebenbei behoben:**
- Der Karten-Editor zeigte immer karten.py statt des gespeicherten Stands
  (`/api/cards` → jetzt `cards.catalog_aktuell()`). Wer eine Karte zweimal
  speicherte, überschrieb still die erste Änderung.
- Nach „Wieder wie im Bot" verschwinden `bild_2`/`bild_3` im Bot sofort
  (`card_store._design_felder_zurueckholen`). **Für alle anderen Felder gilt
  weiterhin:** Zurücksetzen wirkt im Bot erst nach einem Neustart.

---

## Teil 1, Plan 2: Level (MEE6) und Einladungen (Bot 2.5.0)

Stand 20. September 2026, gebaut nach `docs/dev/TEIL-1_PLAN-2_Level-und-Einladungen.md`,
weiterhin auf `opus/teil-1-designs-und-level` (Tags `teil1-plan2-start`,
`teil1-plan2-fertig`). **Nur Bot, keine Website.** Noch nicht in `main` oder
`feature/bot`.

| Teil | Wo |
|---|---|
| Belohnungen (einzige Quelle) | `level_reward_config.py` — LEVEL_STUFEN (Titel = MEE6-Rollennamen), LEVEL_BELOHNUNGEN, LEVEL_HINWEISE, EINLADUNG_STUFEN, EINLADUNG_STAUB_SONST, EINGELADENER_STAUB |
| Logik | `services/level_rewards.py` — Stufe aus Rollen, fällige Belohnungen, Protokoll, Rückfragen, Einstellungen je Server |
| Tabellen | `level_rollen`, `belohnungs_protokoll`, `level_rueckfragen` (`LEVEL_TABLES_SQL` in `services/db.py`); Einstellungen `level.aktiv.<gid>` / `level.kanal.<gid>` in `bot_settings` |
| Ereignisse | `bot.py`: `_level_rollenwechsel` (in `on_member_update`, **vor** der Auszeit-Prüfung), `on_member_remove`, `_level_nachholen_beim_start` (höchstens 20) |
| Rückfragen | `bot.py`: `LevelRueckfrageView`, `_level_verlust`, `_level_rueckfragen_anmelden`, `send_level_offen` |
| Admin-Befehle | `botcommands/level_admin.py` (+ `admin_commands.py`): `/level-einrichten`, `/level-rolle`, `/level-kanal`, `/level-vorschau`, `/level-offen` |
| Spieler-Befehle | `botcommands/level_player.py` (+ `player_commands.py`): `/level`, `/einladungen` |
| Einladungen | `services/invite_store.py` → `level_rewards.einladung_belohnungen`; `invite_reward_config.py` wird nicht mehr genutzt |

**Regeln, die der Code einhält:**
- **Alles Level-bezogene ist aus**, bis jemand `/level-vorschau` → „Jetzt vergeben und
  einschalten“ drückt (`level.aktiv.<guild>`, Standard 0).
- **Höchstens einmal:** erst Platz im `belohnungs_protokoll` belegen, dann vergeben;
  scheitert die Vergabe, wird der Platz wieder frei. `entzogen` zählt als nicht vergeben,
  `behalten` als erledigt.
- Gerechnet wird immer mit der **gesamten Rollenmenge** vorher/nachher — MEE6 gibt und
  nimmt Rollen einzeln. 5 → 10 ist deshalb kein Verlust.
- Der Bot **liest** Rollen nur; er vergibt und entfernt keine.

**Zwei Abweichungen vom Plan (beide mit dem Nutzer abgestimmt):**
1. **Einladungen:** Der Einlader bekommt bei **jeder** Einladung ohne eigene Stufe 5 Staub
   (also auch 2, 3, 4, 6, 7, 8, 9), nicht erst ab der 11. Beim einmaligen Nachholen wird
   dieser Staub **rückwirkend** vergeben.
2. **Rückfrage-Knöpfe:** `durable_view_registry` braucht Guild und Kanal und funktioniert
   deshalb in DMs nicht. Die Knöpfe tragen stattdessen die Nummer der Rückfrage in ihrer
   `custom_id`, und `_level_rueckfragen_anmelden()` meldet beim Start alle offenen Fälle
   wieder an.

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
| Entscheiden | `services/ki_gegner.py` — `KIGegner.waehle`, `frage_bauen`, `antwort_lesen` |
| Kampf | `services/ki_gegner.py:kontrollkampf` (async, eigene Schleife) |
| Auftrag | Art `cards.kikampf`, in `web/app/jobs.py` und `bot.py` (`_run_ki_kontrollkampf`) |
| Modellzugang im Bot | `services/ollama_bot.py` (aiohttp) |
| Prüfung der Eingaben | `web/app/kikampf.py` — legt nur den Auftrag an |

**Warum die Anfrage hereingereicht wird** (`frage`) statt fest verdrahtet:
So läuft das Modul im Test ohne Netz und ohne Ollama — jeder Test dort gibt
seine eigene Antwort vor, auch eine abstürzende.

**Warum es zwei Ollama-Zugänge gibt.** Das Backend-Abbild hat `httpx`, der
Bot nicht — dafür bringt discord.py `aiohttp` mit. Ein gemeinsames Modul
müsste eine der beiden Abhängigkeiten zusätzlich installieren; beide
Umgebungen sind absichtlich schlank. Die *Einstellungen* teilen sie sich
(`web_settings`), es gibt also nur einen Ort, an dem die Adresse gepflegt wird.

### Was das Backend-Abbild NICHT kann

`web/Dockerfile` kopiert nur wenige Bot-Module hinein und installiert weder
discord.py noch aiosqlite. Die Kampf-Engine ist dort deshalb **nicht
lauffähig**: `simulation/engine.py` holt sich `services/combat_runner.py`,
und das importiert das ganze `bot.py`.

Der Import fällt nicht auf — `simulate_duel` lädt `combat_runner` erst beim
Aufruf. Es stürzt also nicht beim Start ab, sondern beim ersten Kampf. Alles,
was wirklich rechnet, gehört deshalb in den Bot.

Aus derselben Ecke kam ein zweiter Fehler: `mission_enemies.py` fehlte im
Abbild, und der Bereich „Schurken" meldete daraufhin, der Bot-Ordner sei
nicht eingebunden. Er war es — eingebunden wird er für die *Datenbank*, nicht
für den *Programmcode*. Wer dort ein Bot-Modul braucht, muss es in den
`COPY`-Zeilen des Dockerfiles nachtragen.

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

**Das Formular schickt immer alle Felder.** `karteneditor.setze()` ersetzt
den gespeicherten Stand komplett. Zeigt der Editor also einen veralteten
Wert, wird genau der beim nächsten Speichern zurückgeschrieben. Deshalb muss
`/api/cards` den gespeicherten Stand liefern (`catalog_aktuell`), nicht
`catalog()`.

**`anwenden()` legt nur auf, was in einer Änderung steht.** Verschwindet
eine Änderung (Zurücksetzen), bleibt der alte Wert an der laufenden Karte
hängen, bis der Bot neu startet. Für die Design-Felder ist das abgefangen,
für die übrigen Felder nicht.

**Neue Admin-Befehle mit `default_permissions`** sieht nur, wer in Discord
das Recht *Administrator* hat — nicht automatisch die MFU-Admin- oder
Dev-Rolle. Freischalten geht ohne Code: Servereinstellungen → Integrationen
→ Bot → Befehl → Rolle hinzufügen.

**Kartenbilder brauchen direkte, dauerhafte Links** (`https://i.imgur.com/….png`).
Google-Drive-Freigaben und Discord-Anhang-Links zeigt Discord nicht bzw. nur
eine Weile.

**`on_member_update` steigt sofort aus, wenn sich die Auszeit nicht geändert
hat** (`if vorher == nachher: return`). Alles, was auf Rollenwechsel reagieren
soll, muss **davor** stehen — und gekapselt, damit ein Fehler dort die
Auszeit-Mitschrift nicht verhindert.

**Persistente Knöpfe in privaten Nachrichten** kann `durable_view_registry`
nicht: Sie speichert Guild, Kanal und Nachricht. Für DMs die Kennung in die
`custom_id` legen und die Views beim Start mit `bot.add_view(...)` neu
anmelden (Muster: `LevelRueckfrageView`).

**`bot.guilds` lässt sich in Tests nicht ersetzen** (Property ohne Setter).
Funktionen, die darüber laufen, nehmen die Liste deshalb als Parameter
entgegen (`_level_nachholen_beim_start(guilds=None)`).

**`default_permissions` ist keine reine Anzeige:** Discord lässt Mitglieder
ohne das Recht den Befehl auch nicht mehr aufrufen. Wer im Bot als Admin gilt,
ohne Discord-Administrator zu sein, braucht eine Freigabe unter
Servereinstellungen → Integrationen.

**`git push` scheitert auf dem Entwicklungsrechner** an einem veralteten
Credential-Helper in `~/.gitconfig` (zeigt auf eine gelöschte `gh.exe`).
Einmal-Umweg ohne die Einstellung zu ändern:
`git -c 'credential.https://github.com.helper=' -c 'credential.https://github.com.helper=!gh auth git-credential' push`

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
`feature/web-dashboard` auf demselben Commit. Auf dem Branch
`opus/teil-1-designs-und-level`: nach Plan 1 **765 Tests**, nach Plan 2
**885 Tests** grün. Die `.venv` braucht dafür zusätzlich `pytest` und
`web/requirements.txt`.

**Und das Wichtigste vor jeder Fehlersuche:** Steht in der Seitenleiste
„Oberfläche vX · Backend vY" oder ein Balken oben auf der Seite, ist nichts
kaputt — dann wurde nur einer der drei Teile aktualisiert. Genau dieser Fall
hat schon einen halben Tag gekostet: Das Backend meldete die neue Version,
die alte `app.js` schrieb sie ungeprüft in die Seitenleiste, und es sah aus,
als fehlten Knöpfe im Programm. Seit 1.3.0 trägt `app.js` ihre eigene Nummer
und vergleicht.
