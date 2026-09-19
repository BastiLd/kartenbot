# Teil 1 · Plan 1 von 2 — Designs (Aussehen der Karten)

> **Für Claude Opus.** Dieser Plan ist in sich vollständig. Lies ihn ganz, dann `web/UEBERGABE.md` (Abschnitt
> „Fallen“), dann fang mit Schritt 0 an. Plan 2 (`TEIL-1_PLAN-2_Level-und-Einladungen.md`) baut auf diesem Plan auf und
> wird **erst danach** umgesetzt.

---

## 1. Einordnung

Das hier ist **Teil 1 von vier** großen Vorhaben. Teil 2 betrifft die Website, Teil 3 ein großes Bot-Feature, Teil 4 weitere
Features. Nichts davon ist Gegenstand dieses Plans — **baue nichts vor**, aber baue so, dass es später nicht im Weg steht
(z. B. keine festen Spalten „design2“ in der Datenbank, sondern Nummern).

**Ziel von Plan 1:** Jeder Held bekommt bis zu **drei Designs** (Standard + zwei alternative Bilder). Ein Spieler kann
ein freigeschaltetes Design für seine Karte wählen; es wird in der Sammlung und im Kampf angezeigt. Ein Design ist
**nur ein anderes Aussehen** — Werte, Angriffe und Name der Karte bleiben gleich. In der Website lassen sich die zwei
zusätzlichen Bild-Links pro Held eintragen.

Plan 2 hängt später die Freischaltung an MEE6-Level und Einladungen. In Plan 1 gibt es dafür nur den Admin-Weg.

---

## 2. Entscheidungen des Nutzers (verbindlich, nicht neu diskutieren)

| Thema | Entscheidung |
|---|---|
| Bedeutung von „Alternatives Karten Design“ | **Nur ein Aussehen** für eine Karte, die der Spieler ohnehin besitzt — **keine** zusätzliche Karte in der Sammlung. |
| Spieler hat die Karte nicht, bekommt aber die Freischaltung | Freischaltung wird **gemerkt**. Sie wirkt, sobald er die Karte besitzt. Es wird keine Karte dazugegeben. |
| Anzahl Designs | Bis zu **3** pro Held (Standard + 2). Mehr gibt es nicht. |
| Designs, die nicht in der Level-/Einladungsliste stehen (ca. 23 Helden) | Bild-Link darf eingetragen werden, das Design ist für Spieler aber **gesperrt**. **Nur ein Admin kann es vergeben.** |
| Wechseln | Über **alle drei** Wege: Befehl, Knopf in der Sammlung, vor dem Kampf. Der **Befehl** legt fest, welches Design **standardmäßig** in Sammlung **und** Kampf angezeigt wird. |
| Admin-Befehle | Sollen für normale Nutzer **nicht sichtbar** und nicht nutzbar sein (dazu Plan 2, Schritt 1; neue Befehle in diesem Plan bekommen es sofort). |
| Bild-Links | Trägt der Nutzer selbst in der Website ein. |

**Zur Frage „Lohnt sich ein dritter Platz?“ (der Nutzer wollte eine Empfehlung im Plan):** Ja, **drei Plätze, technisch
allgemein gebaut.** Die Datenbank speichert eine *Nummer* (1–3) statt fester Spalten, und die Obergrenze steht an genau
einer Stelle (`MAX_DESIGNS = 3`). Der dritte Platz kostet dadurch fast nichts extra und ein späteres „vier“ wäre eine
Ein-Zeilen-Änderung. Es gibt keinen Grund, nur zwei zu bauen.

---

## 3. Grundregeln (gelten für jeden Schritt)

### Arbeitsstil
- Antworte und schreibe auf **Deutsch**. Im sichtbaren Text (Embeds, Website) **echte Umlaute** (ä, ö, ü, ß), kein „ae“.
  Commit-Betreffzeilen bleiben ASCII (so macht es das Repo).
- **Keine Subagents, keine Workflows.** Der Nutzer hat begrenztes Kontingent und wünscht ausdrücklich Solo-Arbeit.
- **Fragen an den Nutzer immer als Mehrfachauswahl** (auch Ja/Nein). Er ist Anfänger: Sag ihm nach jedem Schritt in
  einfachen Worten, **was er selbst tun muss** (welcher Knopf, welcher Befehl, welcher Teil neu geladen werden muss).
- **Nicht raten.** Wenn ein Schritt von einer Annahme abhängt, die du nicht im Code bestätigen kannst: fragen.
- Quelldateien **nie** über PowerShell (`Get-Content`/`Set-Content`) umschreiben — das zerstört Umlaute. Nur
  Edit/Write-Werkzeuge. Für lange Texte mit Anführungszeichen keine Shell-Heredocs.

### Es darf nichts kaputtgehen
- **Baseline zuerst:** komplette Testsuite grün (die Übergabe nennt 680 Tests). Ergebnis notieren. Nach **jedem** Schritt
  wieder komplett grün. Rote Tests werden repariert, nie gelöscht oder übersprungen.
- **Ohne Bild-Links und ohne Freischaltung ändert sich für Spieler nichts.** Prüfe das ausdrücklich (Test + Handprobe).
- Datenbank nur **additiv:** neue Tabellen mit `CREATE TABLE IF NOT EXISTS`, nichts umbenennen, nichts löschen. Neue
  Spalten in *bestehenden* Tabellen nur mit Nachrüstung (`_NACHRUESTEN` in `web/app/schema.py` **und**
  `services/web_jobs.py`; beide Listen müssen zusammenpassen).
- **Nicht anfassen:** Kartenwerte, Angriffe, Missionsgegner, Kampfregeln, die Varianten `Standard_Iron-Man` /
  `Alpha_Iron-Man` und `services/card_variants.py`. Designs sind ein **eigenes, zusätzliches** System.
- Vor der ersten Auslieferung soll der Nutzer die **Datenbank sichern** (Website → Datenbank sichern). Sag es ihm.

### Git (Entscheidung des Nutzers)

| Branch | Zweck |
|---|---|
| `main` | Gemeinsamer Endstand. **Du fasst ihn nicht an.** Er bekommt etwas erst, wenn **alle vier Teile** fertig sind und alles zusammenpasst — das entscheidet der Nutzer. |
| `opus/teil-1-designs-und-level` | **Dein Arbeitsbranch** zum Erstellen (Teil 1, beide Pläne). Ist bereits angelegt, Stand wie `claude/helden-verwaltung-plan-afdd7d` — dort liegen diese Pläne. |
| `feature/bot` | Bot-Branch, angelegt (Stand wie `main`). Sammelt die **Bot-Commits** aus den Opus-Branches, sobald der Nutzer sie übernimmt. Erst wenn beim Bot alles passt, geht es weiter nach `main`. |
| `feature/web-dashboard` | Website-Branch, existiert. Sammelt entsprechend die **Website-Commits**. |

- Du committest und pushst **nur auf deinen Arbeitsbranch.** Nach **jedem** Commit sofort `git push`.
- **Nie** `--force`, **nie** `--amend` nach einem Push, **nie** die Historie umschreiben. Alles muss zurückgehen können.
- **Ein Commit pro Schritt oder feiner.** Vor riskanten Schritten ein Tag setzen (`git tag teil1-vor-schritt-4`, pushen).
- **Bot und Website nie im selben Commit.** Ein Commit berührt entweder nur `web/…` oder nur den Rest. Betreff mit Präfix:
  `bot:` / `web:` / `docs:`. Grund: Der Nutzer übernimmt den Bot-Teil später in `feature/bot` und den Website-Teil in den
  Website-Branch `feature/web-dashboard` (per Cherry-Pick). Ein gemischter Commit wäre dabei unbrauchbar.
- Wo Bot und Website zusammenpassen müssen (`services/card_store.py` ↔ `web/app/karteneditor.py`), kommt der **Bot-Commit
  zuerst**.
- Gemergt wird nichts von dir. Wenn der Nutzer testen und übernehmen will, sagt er es.

### Auslieferung — drei getrennte Teile (aus der Übergabe)
Wer nur einen aktualisiert, sieht Fehler, die wie Programmfehler aussehen.

1. **Bot** — im Bot-Manager von GitHub holen, **neu starten**. (Änderungen in `bot.py`, `services/`, `botcommands/`.)
2. **Backend** — Portainer → Stacks → `kartenbot-web` → *Update the stack*. (Änderungen unter `web/app/`.)
3. **Oberfläche** — `web/static/` als ZIP packen, in WebHafen hochladen („Ordner vorher leeren“), dann Strg+F5.

Bot-Manager und Portainer ziehen laut Übergabe aus GitHub (dort werden `main` und `feature/web-dashboard` gleich gehalten).
**Frage den Nutzer zu Beginn**, wie er einen Branch testen will (Bot-Manager auf den Branch umstellen, oder lokal), und
richte dich danach. Sage nach jedem Schritt genau, welche der
drei Teile neu geladen werden müssen.

---

## 4. Ist-Zustand (im Code geprüft)

- `karten.py` — 34 Karten. Felder: `name, seltenheit, hp, beschreibung, bild, attacks`. **Nur Iron-Man** hat `variants`
  (`Standard_Iron-Man`, `Alpha_Iron-Man`). Diese Varianten sind **eigene Sammlungskarten** (`user_karten.karten_name`) — das
  ist **nicht** das, was hier gebaut wird.
- `services/card_store.py` — Änderungen aus der Website liegen in `card_overrides` (nur Abweichungen zu `karten.py`).
  `AENDERBAR = ("seltenheit", "hp", "beschreibung", "bild", "attacks")`. `anwenden()` ändert die Kartenobjekte an Ort und
  Stelle (alle Module teilen dieselbe Liste; **die Liste ersetzen** käme nicht an, die Objekte ändern schon).
- `web/app/karteneditor.py` — Gegenstück auf der Website. **Eigene** `AENDERBAR` und `GRENZEN`, muss zur Liste im Bot
  passen. `pruefe()` prüft z. B. `bild` auf `http://`/`https://`.
- `web/static/app.js` — Formular „Grunddaten“ mit `<input data-feld="bild">` (Feld „Bildadresse“), Kachelansicht,
  Vollbild, Discord-Vorschau. `app.js` trägt seit 1.3.0 **eine eigene Versionsnummer** und vergleicht sie mit dem Backend.
- `services/card_validation.py` — prüft `karten.py`. Stelle bei `bild` (Pflichtfeld, URL-Prüfung).
- Kartenbilder erscheinen im Bot an diesen Stellen (`bot.py`, suche `"bild"`): Tagesbelohnung/Karte ziehen
  (`ZieheKarteView`), Missions-Belohnung, Vault/Sammlung (`VaultView` und das Karten-Embed dazu), Einladungs-Belohnung,
  Kampf (`BattleView`, `MissionBattleView`, jeweils Spieler- und Gegnerkarte, Bild und Thumbnail). Gegner und Items
  (`enemy`, `items.py`) haben **keine** Designs.
- Kartenauswahl vor dem Kampf: `CardSelectView`, `FightCardSelectView` (DurableView), `MissionStartCardSelectView`,
  `MissionNewCardSelectView`.
- Befehle liegen in `botcommands/` (`player_commands.py`, `admin_commands.py`, `gameplay_commands.py`). Ob ein neuer
  Befehl zusätzlich irgendwo eingetragen werden muss (Kanal-Freigabe-Liste in `bot.py` um den Eintrag
  `"kanal-freigeben"`, `botcore/command_api.py`, `list_commands.py`, Tests `test_command_api_parity.py`,
  `test_alpha_smoke.py`), prüfst du **vor** dem Bauen.
- Admin-Prüfung: `is_admin(interaction)` in `bot.py` (Bot-Owner `BASTI_USER_ID`, Dev-Rolle, Server-Owner,
  Discord-Administrator, `MFU_ADMIN_ROLE_ID` / `OWNER_ROLE_ROLE_ID`). Bisher ist **kein** Befehl per
  `default_permissions` versteckt.
- Die Bot-Datenbank hat **nur eine Schreibverbindung** — Massenaktionen laufen nacheinander.

---

## 5. Zielbild

**Karte (Daten):** zwei neue optionale Felder, `bild_2` und `bild_3` (Text, leer erlaubt). Design 1 = `bild` (Standard).
Design 2/3 existieren für Spieler nur, wenn das Feld gefüllt **und** freigeschaltet ist. `bild_3` ohne `bild_2` ist
unzulässig (keine Lücken).

**Spieler (neue Tabellen, Bot-Datenbank):**
```sql
CREATE TABLE IF NOT EXISTS user_designs (
    user_id           INTEGER NOT NULL,
    karten_name       TEXT    NOT NULL,   -- GRUNDname der Karte (bei Iron-Man beide Varianten -> "Iron-Man")
    design            INTEGER NOT NULL,   -- 2 oder 3; Design 1 ist immer frei
    quelle            TEXT    NOT NULL DEFAULT '',  -- 'admin' | 'level' | 'einladung' (die letzten beiden: Plan 2)
    freigeschaltet_am TEXT    NOT NULL,
    PRIMARY KEY (user_id, karten_name, design)
);
CREATE TABLE IF NOT EXISTS user_design_wahl (
    user_id     INTEGER NOT NULL,
    karten_name TEXT    NOT NULL,
    design      INTEGER NOT NULL,
    PRIMARY KEY (user_id, karten_name)
);
```
Den Grundnamen liefert `base_card_name()` aus `services/card_variants.py`.

**Service `services/designs.py` (neu, reine Logik wo möglich):**
`MAX_DESIGNS = 3`; `verfuegbare_designs(karte) -> list[int]`; `freigeschaltet(user_id, karten_name) -> set[int]`;
`freischalten(user_id, karten_name, design, quelle) -> bool` (idempotent; `False`, wenn schon vorhanden oder die Nummer
ungültig ist, also nicht 2 … `MAX_DESIGNS`). **Die Freischaltung hängt nicht davon ab, dass der Bild-Link schon eingetragen
ist** — im Drive fehlen noch Bilder, und Belohnungen (Plan 2) dürfen deshalb nicht verloren gehen. Sichtbar/wählbar wird
ein Design erst, wenn Link **und** Freischaltung da sind; `entziehen(...)`;
`waehle(user_id, karten_name, design) -> bool` (nur wenn freigeschaltet **und** Link vorhanden);
`aktives_design(...)`; `bild_fuer(user_id, karte) -> str` (fällt **still** auf `bild` zurück, wenn das gewählte Design
gesperrt, entfernt oder der Link leer ist); `aktive_designs_von(user_id) -> dict` für Sammelabfragen (kein Datenbankzugriff
pro Karte in Schleifen).

**Anzeige:** Wo das Bild einer **Spielerkarte** gezeigt wird, kommt `bild_fuer(...)` zum Einsatz. Im Kampf sieht jeder
Spieler sein **gewähltes** Design, und der Gegner sieht es auch. Der Bot-Gegner (KI) nutzt Standard. Beim Kampfstart wird die
Wahl einmal gelesen und in der Ansicht gehalten; nach einem Neustart mitten im Kampf wird sie einfach neu aus der
Datenbank gelesen (kein Eintrag in der Sitzung nötig).

---

## 6. Schritte

Bei jedem Schritt: Code, Tests, Testsuite komplett grün, Commit, Push, dem Nutzer sagen, was er tun muss.

### Schritt 0 — Vorbereitung (`docs:`)
1. `git fetch`; auf den Branch `opus/teil-1-designs-und-level` wechseln. Er wurde bei der Planung bereits angelegt (gleicher
   Stand wie `claude/helden-verwaltung-plan-afdd7d`, dort liegen diese Pläne). Fehlt er, von dort abzweigen und mit
   `git push -u origin …` veröffentlichen. Tag `teil1-start` setzen und pushen.
2. Umgebung wie in `DEVELOPING.md` / `scripts/setup_windows.ps1` aufsetzen (in diesem Arbeitsordner gibt es noch kein
   `.venv`). **Baseline-Testlauf**, Ergebnis notieren.
3. Lesen: `web/UEBERGABE.md`, `services/card_store.py`, `services/card_variants.py`, `services/card_validation.py`,
   `web/app/karteneditor.py`, `web/app/cards.py`, `botcommands/player_commands.py`, `botcore/command_api.py`, die
   Stellen mit `"bild"` in `bot.py`.
4. Den Nutzer fragen (Mehrfachauswahl), wie er einen Branch testet (siehe Auslieferung).

### Schritt 1 — Kartenfelder `bild_2`, `bild_3` (Bot-Seite) (`bot:`)
- `services/card_store.py`: `AENDERBAR` um `"bild_2"`, `"bild_3"` erweitern (Reihenfolge und Inhalt **identisch** zur
  Website-Liste, sie wird in Schritt 8 nachgezogen).
- `services/card_validation.py`: Felder optional; wenn gesetzt, gilt dieselbe URL-Regel wie bei `bild`
  (`http://`/`https://`, ≤ 500 Zeichen); `bild_3` ohne `bild_2` → Fehlermeldung.
- `karten.py` bleibt unverändert (Felder sind optional, Zugriff nur mit `.get`).
- **Fertig, wenn:** `scripts/validate_cards.py` grün, Tests grün, eine Karte mit `bild_2` lässt sich laden.

### Schritt 2 — Tabellen und `services/designs.py` (`bot:`)
- Tabellen aus Abschnitt 5 in der Schema-Initialisierung des Bots anlegen (`services/db.py`, wie die anderen Tabellen).
- `services/designs.py` mit den Funktionen aus Abschnitt 5, plus Tests (`tests/test_designs.py`):
  Freischalten doppelt → zweites Mal `False`; Freischalten **ohne** eingetragenen Link ist möglich, Wählen dann nicht;
  Wählen nicht freigeschalteter Designs → `False`; Fallback von `bild_fuer`; Iron-Man-Varianten teilen sich eine
  Freischaltung; `bild_3` ohne `bild_2` ergibt keine Auswahl; Nummer 1 und Nummern über `MAX_DESIGNS` sind ungültig.
- **Fertig, wenn:** Tests grün, keine bestehende Tabelle verändert.

### Schritt 3 — Anzeige überall (`bot:`)
- An **allen** Stellen, die das Bild einer Spielerkarte zeigen, `bild_fuer(...)` einsetzen (Liste in Abschnitt 4; suche
  `"bild"` in `bot.py`, `services/`, `botcommands/` — der Bot ist sehr groß, prüfe **alle** Treffer einzeln).
- Kampf: Wahl beim Start lesen und in der Ansicht halten (siehe Abschnitt 5). Werte, Namen und Logik bleiben unberührt.
- **Fertig, wenn:** Ohne Freischaltung sind alle Ausgaben **Byte für Byte** wie vorher (Test gegen die bestehenden Embed-Tests);
  mit Freischaltung erscheint das gewählte Design in Sammlung und Kampf (Handprobe).

### Schritt 4 — Befehl `/design` (`bot:`)
- Ephemerale Ansicht für Spieler: Auswahl der Karte (nur Karten, die der Spieler **besitzt** und die ≥ 2 verfügbare Designs
  haben), Auswahl des Designs (gesperrte sind sichtbar, aber mit 🔒 und Hinweis „noch gesperrt“, nicht wählbar), Vorschau
  mit Bild, Knopf „Übernehmen“. Zusätzlich „Alle zurücksetzen“ (alle Karten auf Design 1).
- Wortlaut Deutsch, Beispiel: „Design 2 von 3 · Black Widow“. Nichts an Werten anzeigen, was sich ändert — es ändert sich
  nichts.
- Eintragen, wo Befehle registriert werden müssen (siehe Abschnitt 4). Tests: Befehl ist angemeldet, die Ansicht
  baut sich (Muster: `tests/test_battle_view_smoke.py`, `tests/view_harness.py`).
- **Fertig, wenn:** Ein Spieler kann sein freigeschaltetes Design wählen, danach zeigen Sammlung und Kampf es an.

### Schritt 5 — Knopf „Design wechseln“ in der Sammlung (`bot:`)
- Bei der einzelnen Karte in der Sammlung (Vault/Sammlung-Ansicht) ein Knopf, der durch die **freigeschalteten** Designs
  schaltet (nur sichtbar, wenn ≥ 2 Designs freigeschaltet sind). Setzt dieselbe Wahl wie `/design`.
- **Fertig, wenn:** Knopf wechselt das Bild sofort; Ansichten ohne freigeschaltete Designs sehen aus wie vorher.

### Schritt 6 — Admin-Befehle `/design-geben` und `/design-entziehen` (`bot:`)
- Parameter: Mitglied, Karte (Auswahl mit Autovervollständigung aus Karten mit `bild_2`), Design (2 oder 3). Ruft
  `freischalten(..., quelle="admin")` bzw. `entziehen` auf. Antwort ephemeral mit Ergebnis („neu freigeschaltet“ /
  „hatte er schon“). Ist für die Karte **noch kein Bild-Link** eingetragen, wird trotzdem freigeschaltet, aber die Antwort
  weist darauf hin („Achtung: für dieses Design ist noch kein Bild eingetragen — es wird erst sichtbar, wenn du den Link in
  der Website ergänzt“). Die Autovervollständigung der Karte zeigt **alle** Karten, nicht nur die mit `bild_2`.
- Prüfung mit `is_admin(interaction)`. **Zusätzlich verstecken:** `@app_commands.default_permissions(administrator=True)`
  und `@app_commands.guild_only()`. Die Laufzeitprüfung bleibt (das Verstecken ist Komfort, keine Sicherheit).
- Tests: Nicht-Admin wird abgewiesen; doppelt vergeben ist harmlos.

### Schritt 7 — Design vor dem Kampf wechseln (`bot:`)
- Ziel: In der Kartenauswahl vor dem Kampf (`FightCardSelectView`, `MissionStartCardSelectView`,
  `MissionNewCardSelectView`) das gewählte Design zeigen und einen schnellen Wechsel anbieten.
- **Stopp-Regel:** Das sind `DurableView`s (überleben Neustarts). Ändere **nichts** an bestehenden `custom_id`s oder am
  Registry-Format. Wenn der Wechsel nur mit solchen Eingriffen geht: **nicht bauen.** Stattdessen im Vorschaubild das
  gewählte Design zeigen und den Hinweis „Design ändern: /design“ einblenden — und den Nutzer (Mehrfachauswahl) fragen, ob
  das reicht. Lieber weniger als ein kaputter Kampfstart.
- **Fertig, wenn:** Kampf- und Missionsstart funktionieren wie vorher (bestehende Tests grün, Handprobe eines
  Kampfes und einer Mission).

### Schritt 8 — Website: Felder „Design 2“ und „Design 3“ (`web:`)
- `web/app/karteneditor.py`: `AENDERBAR` um `bild_2`, `bild_3` (**gleich** wie im Bot, gleiche Reihenfolge), `GRENZEN`,
  `pruefe()`: URL-Regel wie bei `bild`; `bild_3` nur mit `bild_2`; verständliche deutsche Fehlermeldungen.
- `web/app/cards.py`: die zwei Felder an die Oberfläche durchreichen (sie müssen in den Kartendaten ankommen, die
  `app.js` bekommt).
- `web/static/app.js` (+ CSS): im Formular „Grunddaten“ unter „Bildadresse“ zwei Eingabefelder „Design 2 (alternatives
  Bild)“ und „Design 3“, mit kleiner Vorschau des Bildes und Hinweis „Direktlink von imgur (`https://i.imgur.com/….png`)“.
  In Kacheln/Vollbild ein kleines Kennzeichen „2 Designs“ / „3 Designs“. In der Discord-Vorschau Umschalter
  Design 1/2/3. Rücknahme über den bestehenden Verlauf prüfen (`card_override_history`): muss die neuen Felder
  mit zurückholen.
- Versionsnummern erhöhen: `web/VERSION` (1.4.1 → 1.5.0) **und** die eigene Nummer in `app.js` (so ist es seit 1.3.0
  gebaut; finde die Stelle, sonst zeigt die Seite das Warnband „Oberfläche passt nicht zum Backend“).
- Tests: `tests/test_web_dashboard.py` erweitern (Eingabeprüfung).
- **Fertig, wenn:** Link eintragen → speichern → im Bot (nach Bot-Update) ist `bild_2` an der Karte da. Ohne Eintrag
  sieht die Seite aus wie vorher.
- **Auslieferung dieses Schritts:** Backend **und** Oberfläche (Teil 2 und 3). Bot-Update (Teil 1) muss **vorher** laufen.

### Schritt 9 — Version, Notizen, Übergabe (`bot:` / `docs:`)
- `bot.py`: `__version__` von `2.3.20` auf `2.4.0`. `README.md` (Versionszeile) nachziehen.
- `docs/releases/release_notes_v2.4.0.md` im Stil der bisherigen (kurz, für Spieler lesbar: „Neu: Alternative Designs für
  Karten …“). **Keine** Level-/Einladungs-Versprechen — das kommt mit Plan 2.
- `web/UEBERGABE.md` ergänzen: Was Plan 1 gebaut hat, wo es liegt, welche Fallen es gab.
- Tag `teil1-plan1-fertig`, pushen.

---

## 7. Tests (Überblick)

Neue Datei `tests/test_designs.py` und Ergänzungen: Freischalten/Wählen/Fallback, keine Lücken (`bild_3` ohne `bild_2`),
Iron-Man-Varianten teilen Freischaltung, Ausgabe ohne Designs unverändert, Befehle angemeldet, Admin-Befehle
abgewiesen für Nicht-Admins und mit `default_permissions` versehen, Website-Eingabeprüfung. Am Ende: **gesamte** Suite grün,
`scripts/validate_cards.py` grün, `scripts/alpha_smoke.py` grün.

## 8. Abnahme durch den Nutzer (Anleitung für ihn schreiben)

1. Datenbank sichern.
2. Bot aktualisieren und neu starten. Website-Backend und Oberfläche aktualisieren.
3. In der Website bei einem Helden **Design 2** eintragen, speichern. (Zum Testen z. B. eines der fünf vorhandenen Bilder:
   Rocket, Doctor Strange, Groot, Loki, Scarlet Witch — erst nach imgur hochladen.)
4. In Discord: `/design` → das Design ist **gesperrt**.
5. `/design-geben` (als Admin) → `/design` zeigt es freigeschaltet → wählen → Sammlung zeigt das neue Bild → ein Kampf
   zeigt es auch.
6. Ein normaler Nutzer sieht `/design-geben` und `/design-entziehen` **nicht** in der Befehlsliste.
7. Design in der Website wieder entfernen → Spieler sieht still wieder das Standardbild, nichts bricht.

## 9. Nicht Teil dieses Plans

Freischaltung über Level und Einladungen, `/level`, `/einladungen`, Verstecken der **bestehenden** Admin-Befehle
(alles Plan 2). Website-Oberfläche zum Vergeben von Designs (späterer Website-Teil). Die vier fehlenden Missions-
Operationen und die Story (siehe `VERGLEICH_Drive-gegen-Code.md`). Änderungen an Kartenwerten.

## 10. Fallstricke, die schon Zeit gekostet haben

- **Discord-IDs** sind für JavaScript zu groß: aus dem Backend immer als **Text** liefern (`str(uid)`).
- **Kartenänderungen ohne Neustart** wirken nur, weil die Kartenobjekte an Ort und Stelle geändert werden.
- **Neue Auftragsarten** brauchen Aufräumen in `web_jobs.reset_orphaned()` (hier voraussichtlich nicht nötig).
- **Bilder:** Discord zeigt nur direkte, dauerhafte Adressen. Google-Drive-Freigabelinks und Discord-Anhang-Links (laufen
  ab) taugen nicht — imgur nutzen, wie bisher. Sage das dem Nutzer bei Schritt 8.
- `docs/dev/VERGLEICH_Drive-gegen-Code.md` beschreibt, welche Bilder im Drive **fehlen** (Black Widow, The Thing, Captain
  Marvel, Captain America, Spider-Man, Namor) — nicht darauf warten, Plan 1 braucht keine echten Bilder.
