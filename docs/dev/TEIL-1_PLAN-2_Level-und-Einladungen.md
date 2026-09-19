# Teil 1 · Plan 2 von 2 — Level (MEE6), Einladungen, versteckte Admin-Befehle

> **Für Claude Opus.** Dieser Plan ist in sich vollständig. **Voraussetzung: Plan 1 ist umgesetzt** (`TEIL-1_PLAN-1_Designs.md`,
> Tag `teil1-plan1-fertig`). Er liefert `services/designs.py` mit `freischalten(...)`, das hier gebraucht wird.
> Lies diesen Plan ganz, dann `web/UEBERGABE.md` (Abschnitt „Fallen“). **Dieser Plan berührt nur den Bot, keine Website.**

---

## 1. Einordnung

**Teil 1 von vier** großen Vorhaben (2: Website, 3: großes Bot-Feature, 4: weitere Features — nicht Gegenstand hier,
nichts vorbauen, aber nichts verbauen).

**Ziel von Plan 2:**
1. Der Bot erkennt das **MEE6-Level** der Spieler (über die Level-Rollen) und **schaltet bei bestimmten Stufen
   alternative Designs frei** (Plan 1).
2. Die Einlade-Belohnungen („Werbt einen Freund“) werden nach der Liste des Nutzers umgebaut.
3. Alle Befehle, die nur für Admins/Owner gedacht sind, werden für normale Nutzer **unsichtbar**.
4. Zwei Komfort-Befehle für Spieler: `/level` und `/einladungen`.

---

## 2. Entscheidungen des Nutzers (verbindlich, nicht neu diskutieren)

| Thema | Entscheidung |
|---|---|
| Level erkennen | Über die **MEE6-Rollen**. Keine Rangliste, keine Web-Schnittstelle von MEE6. Der Bot **liest** Rollen nur — er vergibt und entzieht **keine** Rollen. |
| Bedeutung „Alternatives Karten Design“ | **Nur ein Aussehen** (Plan 1), keine zusätzliche Karte. Besitzt der Spieler die Karte nicht, wird die Freischaltung **gemerkt**. |
| Bereits vorhandene Level-Rollen (bis zum Release starten alle mit Level 10) | **Nachholen — aber erst nach Vorschau** und Freigabe durch den Nutzer. |
| Meldung bei Freischaltung | **Nachricht in einem Kanal.** Der Nutzer wählt den Kanal **per Befehl in Discord** (keine ID von Hand, nichts im Code). |
| Rolle verloren (Level-Reset, Server verlassen, Rolle entfernt) | Der Nutzer bekommt eine **Private Nachricht** und entscheidet: **Behalten** oder **Entziehen**. Bis dahin ändert sich nichts. |
| Einladungen | **Genau nach der Liste** (Abschnitt 4). Der **Eingeladene** bekommt bei jeder bestätigten Einladung weiter nur **5 Staub**; die Karten/Designs und Zusatz-Staub bekommt **der Einlader**. |
| Extras für Spieler | `/level` (Stufe + nächste Belohnung) und `/einladungen` (Fortschritt). Keine „Design-Übersicht“ (das leistet `/design` aus Plan 1). |
| Admin-Befehle | Für normale Nutzer **nicht sichtbar** und nicht nutzbar, „wenn möglich“. |

---

## 3. Grundregeln (gelten für jeden Schritt)

### Arbeitsstil
- **Deutsch.** Im sichtbaren Text **echte Umlaute**. Commit-Betreffzeilen ASCII. Neue Spielertexte gehören in
  `game_ui_texts.py`.
- **Keine Subagents, keine Workflows** (begrenztes Kontingent des Nutzers). **Fragen immer als Mehrfachauswahl.**
  Der Nutzer ist Anfänger: Sag nach jedem Schritt einfach, **was er selbst tun muss**.
- **Nicht raten** — im Zweifel fragen. Quelldateien **nie** über PowerShell (`Get-Content`/`Set-Content`) umschreiben
  (zerstört Umlaute), nur Edit/Write.

### Es darf nichts kaputtgehen
- **Baseline zuerst:** komplette Suite grün (Übergabe: 680 Tests, plus die aus Plan 1). Nach **jedem** Schritt wieder komplett
  grün; nie Tests löschen oder überspringen.
- **Alles Neue ist aus, bis der Nutzer es einschaltet** (Schalter `level.aktiv` je Server, Standard: **aus**). Solange er
  aus ist, ändert sich für Spieler nichts. Ausnahme: die Einlade-Umstellung (Schritt 8) und das Verstecken der Admin-Befehle
  (Schritt 1) gelten sofort — sag das dem Nutzer vorher klar.
- Datenbank nur **additiv** (`CREATE TABLE IF NOT EXISTS`, nichts umbenennen oder löschen; neue Spalten in bestehenden
  Tabellen nur mit Nachrüstung in `_NACHRUESTEN`, `web/app/schema.py` **und** `services/web_jobs.py`).
- **Nicht anfassen:** Kartenwerte, Kampfregeln, Missionen, `services/card_variants.py`, Iron-Man-Varianten.
- Vor der Auslieferung soll der Nutzer die **Datenbank sichern**.
- Alle Vergaben laufen **höchstens einmal** (Protokoll, Abschnitt 6). Wer etwas verkauft/getauscht hat, bekommt es nicht
  erneut.

### Git (Entscheidung des Nutzers)
- Du arbeitest **nur** auf `opus/teil-1-designs-und-level` (weiter, wo Plan 1 aufgehört hat). `main`,
  `feature/web-dashboard` und der Bot-Branch werden **nicht** von dir angefasst — der Nutzer übernimmt später.
- Nach **jedem** Commit `git push`. **Nie** `--force`, **nie** `--amend` nach Push, nie die Historie umschreiben.
  Vor riskanten Schritten ein Tag (z. B. `git tag teil1-vor-schritt-8`).
- **Ein Commit pro Schritt oder feiner. Bot und Website nie im selben Commit** (Präfix `bot:` / `web:` / `docs:`).
  Plan 2 braucht keine Website — es sollten also nur `bot:` und `docs:` entstehen.

### Auslieferung
Dieser Plan braucht **nur Teil 1 der drei Auslieferungsteile: den Bot** (im Bot-Manager holen, **neu starten**). Neue
Befehle erscheinen erst nach dem Neu-Anmelden der Befehle (prüfe, wie der Bot das heute macht, und sag dem Nutzer, was er
klicken muss). Frage den Nutzer zu Beginn (Mehrfachauswahl), **wie er testet** — der Bot kennt Alpha/Beta **pro Server**;
ein eigener Testserver ist am sichersten.

---

## 4. Die Listen des Nutzers und was davon der Bot tut

### 4.1 Level (MEE6-Rollen)

| Level | Rolle (Name laut Nutzer) | Der **Bot** tut | Nicht Sache des Bots (Discord/MEE6) |
|---|---|---|---|
| 1 | Einwohner von New York | nichts | Einstieg |
| 5 | Stark Industries Praktikant | **Design 2 „Black Widow“** freischalten | |
| 10 | S.H.I.E.L.D. Agenten | nichts | Bilder und Links im Chat erlauben. **Alle, die bis zum Release im Discord waren, starten mit Level 10** |
| 15 | Howling Commandos | **Design 2 „Rocket“** | |
| 20 | Defenders von Hell's Kitchen | **Design 2 „The Thing“** | |
| 25 | Meister der Mystischen Künste | nichts | Zugriff auf Kanal „Sanctum Sanctorum“ (Chat mit aktiven Nutzern) |
| 30 | Wakandische Dora Milaje | nichts | Neuer Kanal mit exklusiven Einblicken („Archiv“) |
| 35 | Asgardische Krieger | **Design 2 „Doctor Strange“** (die Liste schreibt „Dr Strange“, die Karte heißt `Doctor Strange`) | |
| 40 | Guardians of the Galaxy | **Design 2 „Groot“** | |
| 45 | Inhumans Royal Guard | **Design 2 „Captain Marvel“** | |
| 50 | Avengers Initiative | **Design 2 „Loki“** | „Höhere Gewinnspiel-Chancen?“ — mit Fragezeichen notiert, **nicht Sache des Bots** (es gibt kein Gewinnspiel im Bot). Nicht bauen. |

Die Belohnungen sind **kumulativ**: Wer Stufe 35 hat, hat Anspruch auf alles ≤ 35.

### 4.2 „Werbt einen Freund“ (zählt bestätigte Einladungen des **Einladers**)

| Bestätigte Einladungen | Einlader bekommt | Eingeladener bekommt |
|---|---|---|
| 1 | Design 2 „Captain America“ | 5 Staub |
| 2–4 | nichts extra | 5 Staub |
| 5 | Design 2 „Spider-Man“ **+ 5 Staub** | 5 Staub |
| 6–9 | nichts extra | 5 Staub |
| 10 | Design 2 „Scarlet Witch“ **+** Design 2 „Namor“ **+ 10 Staub** | 5 Staub |
| jede weitere (ab 11) | **5 Staub** | 5 Staub |

**Verhaltensänderung, ausdrücklich bestätigt („so wie es hier steht“):** Bisher gab es beim ersten Einladen Iron-Man und
danach für **jede** Einladung 5 Staub für beide Seiten. Künftig entfällt die Iron-Man-Belohnung, und der Einlader bekommt bei
2–4 und 6–9 **nichts** extra. Weise den Nutzer beim Abschluss darauf hin.

---

## 5. Ist-Zustand (im Code geprüft)

- **Kein MEE6, kein Level** irgendwo im Code. `botcore/bootstrap.py` hat die Intents `members`, `message_content`, `presences`
  schon an. In `bot.py` gibt es `on_member_update`, aber es **schreibt nur Auszeiten mit** und beendet sich sofort,
  wenn sich die Auszeit nicht geändert hat (`if vorher == nachher: return`). Dein Rollenvergleich muss **davor** laufen; die
  Auszeit-Mitschrift darf nicht kaputtgehen.
- `services/invite_store.py`, `finalize_invite_pending_if_ready(...)`: Zählt `invite_stats.completed_invites` hoch, danach
  beim **ersten** Mal Karte aus `invite_reward_config.py` (`Standard_Iron-Man`) an den Einlader + 5 Staub an den Eingeladenen,
  danach 5 Staub an **beide**. Rückgabe enthält `reward_summary`. Der Aufrufer ist `InviteConfirmationView._after_flag` in
  `bot.py` (baut das Embed, ruft `_send_private_invite_card_reward`).
- Kanal-Einstellungen: `guild_config` (u. a. `mission_channel_id`), Befehle wie `/kanal-freigeben` (Kanal in Discord
  auswählen). Einfache Einstellungen: Tabelle `bot_settings` (Schlüssel/Wert, Muster in `invite_store.py`, z. B.
  `invite.max_member_age_days`).
- Dauerhafte Knöpfe (überleben Neustart): `DurableView`, Registry `durable_view_registry`
  (`InviteConfirmationView` ist ein Beispiel).
- Admin-Befehle: `botcommands/admin_commands.py`. **Kein** Befehl ist heute per `default_permissions` versteckt; nur
  `/op-verwaltung` hat `guild_only`. Geprüft wird zur Laufzeit:

  | Befehl | Prüfung heute |
  |---|---|
  | `/entwicklerpanel`, `/bot-status` | `require_owner_or_dev` (Bot-Owner `BASTI_USER_ID` oder Dev-Rolle) |
  | `/intro-zurücksetzen`, `/sammlung-ansehen`, `/test-bericht`, `/karte-geben`, `/dust`, `/lödust`, `/invite-limit`, `/op-verwaltung`, Gruppe `/statistik` | `is_admin` |
  | `/kanal-freigeben`, Gruppe `/konfigurieren` | `is_config_admin` (Admin **oder** „Server verwalten“ **oder** „Kanäle verwalten“) |

  `is_admin` gilt für: Bot-Owner/Dev, Server-Owner, Discord-Administrator, Rolle `MFU_ADMIN_ROLE_ID` oder
  `OWNER_ROLE_ROLE_ID`. Daneben gibt es „Give-OP“-Nutzer und -Rollen (`is_give_op_authorized`, `op-verwaltung`).
- Die Bot-Datenbank hat **nur eine Schreibverbindung** — Massenläufe nacheinander, mit kurzer Pause.
- Discord bremst bei zu schnellen Änderungen aus (`services/role_manager.py` nutzt `PAUSE_BETWEEN_CALLS = 0.35`).

---

## 6. Zielbild

### 6.1 Konfiguration — `level_reward_config.py` (neu, im Stil von `invite_reward_config.py`)
Eine Datei, die ein Anfänger verstehen kann, mit Kommentaren. Inhalt:
- `LEVEL_STUFEN`: `{1: "Einwohner von New York", 5: "Stark Industries Praktikant", …, 50: "Avengers Initiative"}` (Titel = auch
  die Rollennamen für die automatische Zuordnung).
- `LEVEL_BELOHNUNGEN`: je Level eine Liste aus `Design(karte, nummer)` (Nummer immer 2). Level ohne Bot-Belohnung fehlen
  oder sind leer.
- `LEVEL_HINWEISE`: kurzer Text, was die Stufe **außerhalb des Bots** bringt (Anzeige in `/level`), z. B. Level 10:
  „Bilder und Links im Chat“, Level 25: „Kanal Sanctum Sanctorum“, Level 30: „Kanal Archiv“.
- `EINLADUNG_STUFEN`: `{1: [Design("Captain America", 2)], 5: [Design("Spider-Man", 2), Staub(5)], 10: [Design("Scarlet Witch", 2), Design("Namor", 2), Staub(10)]}`.
- `EINLADUNG_AB_STUFE = 11`, `EINLADUNG_STAUB_AB_11 = 5`, `EINGELADENER_STAUB = 5`.
- Ein Test prüft: **jede** genannte Karte existiert in `karten.py`; Nummern liegen zwischen 2 und `MAX_DESIGNS`.

### 6.2 Neue Tabellen (Bot-Datenbank, additiv)
```sql
CREATE TABLE IF NOT EXISTS level_rollen (
    guild_id INTEGER NOT NULL, level INTEGER NOT NULL, role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);
CREATE TABLE IF NOT EXISTS belohnungs_protokoll (
    user_id   INTEGER NOT NULL,
    schluessel TEXT   NOT NULL,   -- z. B. 'level:5:design:Black Widow:2', 'einladung:5:staub', 'einladung:12:staub'
    art       TEXT    NOT NULL,   -- 'design' | 'staub'
    quelle    TEXT    NOT NULL,   -- 'level' | 'einladung' | 'nachholen'
    details_json TEXT NOT NULL DEFAULT '',
    status    TEXT    NOT NULL DEFAULT 'vergeben',   -- 'vergeben' | 'behalten' | 'entzogen'
    am        TEXT    NOT NULL,
    PRIMARY KEY (user_id, schluessel)
);
CREATE TABLE IF NOT EXISTS level_rueckfragen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    von_stufe INTEGER NOT NULL, auf_stufe INTEGER NOT NULL,
    grund TEXT NOT NULL DEFAULT '',        -- 'rolle_verloren' | 'server_verlassen'
    status TEXT NOT NULL DEFAULT 'offen',  -- 'offen' | 'behalten' | 'entzogen'
    nachricht_id INTEGER, erstellt_am TEXT NOT NULL
);
```
Einstellungen in `bot_settings` (je Server): `level.aktiv.<guild_id>` (`0`/`1`, Standard `0`), `level.kanal.<guild_id>`
(Kanal-ID der Meldungen).

**Höchstens-einmal-Regel:** Wer zuerst das Protokoll schreibt und erst dann vergibt, kann nie doppelt vergeben, aber bei
einem Fehler dazwischen etwas verlieren. Baue es so: Protokolleintrag mit `INSERT OR IGNORE`; **nur wenn er neu ist**,
Belohnung ausführen; schlägt das fehl, den Eintrag wieder löschen und protokollieren. Ist es ohne Umbau der bestehenden
Funktionen möglich, beides in **einer** Transaktion zu machen, dann so. Ein Test muss belegen: zweimal aufrufen → einmal
vergeben; Design **verkauft/entzogen** und erneut aufgerufen → **nicht** erneut vergeben (Protokoll gilt).

### 6.3 Kern — `services/level_rewards.py` (neu; möglichst reine Funktionen, ohne Discord testbar)
- `stufe_aus_rollen(rollen_ids, zuordnung) -> int`: **höchste** zugeordnete Stufe, die der Spieler hat, sonst 0. Funktioniert,
  ob MEE6 „Vorherige Rollenbelohnung entfernen“ an- oder ausgeschaltet hat (nur die höchste zählt).
- `faellige_belohnungen(stufe, schon) -> list`: alle Belohnungen für Level ≤ `stufe`, die nicht im Protokoll stehen.
- `einladung_belohnungen(neue_anzahl) -> list`: Stufen bei 1/5/10, ab 11 je 5 Staub.
- `vergebe(user_id, belohnungen, quelle) -> Ergebnis` (Regel aus 6.2; Designs über `services/designs.freischalten`, Staub über
  `add_infinitydust`).
- Rollenverlust: `verloren_haben(stufe_vorher, stufe_nachher)` → **nur wenn die Stufe sinkt**. Ersetzt MEE6 „Level 5“ durch
  „Level 10“ (eine Rolle geht, eine kommt), ist das **kein** Verlust. Rechne immer mit der **Gesamtmenge** der Rollen
  vorher/nachher, nie mit einzelnen Rollen-Ereignissen.

---

## 7. Schritte

Bei jedem Schritt: Code, Tests, Suite komplett grün, Commit, Push, dem Nutzer sagen, was er tun muss.

### Schritt 0 — Vorbereitung (`docs:`)
Plan 1 fertig? (Tag `teil1-plan1-fertig`, Suite grün.) Baseline-Testlauf notieren. Lesen: `services/invite_store.py`,
`bot.py` (`on_member_update`, `InviteConfirmationView`, `_send_private_invite_card_reward`), `botcommands/admin_commands.py`,
`services/designs.py`, `services/role_manager.py` (Muster für Pausen), `services/db.py`. Den Nutzer fragen, wie er testet.

### Schritt 1 — Admin-Befehle verstecken (`bot:`)
Ziel: normale Nutzer sehen diese Befehle nicht in der Liste und können sie nicht nutzen.
- Verstecken mit `@app_commands.default_permissions(...)` (bei Gruppen `default_permissions=discord.Permissions(...)` im
  `app_commands.Group`) und `@app_commands.guild_only()`:
  - **Administrator:** `/entwicklerpanel`, `/bot-status`, `/intro-zurücksetzen`, `/sammlung-ansehen`, `/test-bericht`,
    `/karte-geben`, `/dust`, `/lödust`, `/invite-limit`, `/op-verwaltung`, Gruppe `/statistik`.
  - **„Server verwalten“ (`manage_guild`):** `/kanal-freigeben`, Gruppe `/konfigurieren` (dort erlaubt der Bot auch diese
    Rechte).
  - Die Befehle aus Plan 1 (`/design-geben`, `/design-entziehen`) und alle neuen Admin-Befehle dieses Plans ebenfalls.
- **Laufzeitprüfungen bleiben unverändert** (Verstecken ist Komfort, keine Sicherheit).
- **Fallstricke — vorher klären, ggf. Nutzer fragen:**
  1. Discord kann nur nach **Discord-Rechten** verstecken, nicht nach „nur Basti“. Wer im Bot als Admin gilt, ohne
     Discord-Administrator zu sein (Dev-Rolle, `MFU_ADMIN_ROLE_ID`, `OWNER_ROLE_ROLE_ID`, Give-OP-Nutzer), **sieht die Befehle
     danach nicht mehr**. Lösung, **einmalig und von Hand** durch den Server-Owner: Servereinstellungen → **Integrationen** →
     Bot → Befehl → Rolle/Person erlauben. Schreibe dem Nutzer diese Anleitung Schritt für Schritt.
  2. Prüfe alle Verwendungen von `is_give_op_authorized` und `op-verwaltung`. Befehle, die **Give-OP-Nutzer ohne
     Admin-Recht** nutzen sollen (vermutlich `/karte-geben`, `/dust`, `/lödust`), **nicht** verstecken, sondern den Nutzer
     fragen (Mehrfachauswahl): „Verstecken und die OP-Rolle in Integrationen freigeben“ oder „sichtbar lassen“.
  3. Nach der Änderung müssen die Befehle **neu angemeldet** werden. Prüfe, wie der Bot es macht (Synchronisierung beim
     Start / im Entwicklerpanel) und sag dem Nutzer, was er tun muss.
- **Tests:** Eine Liste aller Admin-Befehle in der Testdatei; der Test schlägt an, wenn ein Befehl aus der Liste **keine**
  `default_permissions` hat. Bestehende Tests (`test_command_api_parity.py`, `test_alpha_smoke.py`) bleiben grün.
- **Fertig, wenn:** Ein Konto ohne Admin-Recht sieht keinen der Befehle; Admins sehen und nutzen sie wie vorher.

### Schritt 2 — Konfiguration und Tabellen (`bot:`)
`level_reward_config.py` (6.1) und die Tabellen (6.2) anlegen. Tests: Konfiguration ist stimmig (Karten existieren,
Nummern gültig, Stufen aufsteigend).

### Schritt 3 — Kern `services/level_rewards.py` (`bot:`)
Funktionen aus 6.3 mit ausführlichen Tests (Rollen ersetzend/stapelnd, höchstens einmal, Verlust nur bei sinkender
Stufe, Einladungsstufen 1/2/4/5/9/10/11/12, Karte fehlt im Katalog → sauber überspringen und melden).

### Schritt 4 — Einrichtungsbefehle (alle Admin, versteckt) (`bot:`)
- `/level-einrichten`: sucht auf dem Server Rollen, deren Name (ohne Groß-/Kleinschreibung, Leerzeichen normalisiert)
  einem Titel aus `LEVEL_STUFEN` entspricht, zeigt einen Vorschlag („Level 5 → @Stark Industries Praktikant …, **nicht
  gefunden:** Level 30“) mit Knöpfen **Übernehmen** / **Abbrechen**. Speichert in `level_rollen`.
- `/level-rolle`: einzelne Zuordnung von Hand setzen/entfernen (Level auswählen, Rolle aus der Liste wählen) für
  Fälle, in denen der Name abweicht.
- `/level-kanal`: Kanal **in Discord auswählen** (Kanalauswahl-Parameter), in `bot_settings` speichern, eine Testnachricht
  dort senden („Level-Meldungen erscheinen ab jetzt hier“). Prüft vorher, ob der Bot dort senden darf, und sagt es, wenn nicht.
- **Fertig, wenn:** Der Nutzer alles nur mit Klicks in Discord einstellen kann. **Keine** ID muss kopiert oder in den Code
  geschrieben werden.

### Schritt 5 — Auslöser: Level-Aufstieg und Kanal-Meldung (`bot:`)
- In `on_member_update` **vor** der Auszeit-Prüfung: Stufe aus `before.roles` und `after.roles` berechnen (Zuordnung des Servers).
  - Steigt die Stufe **und** `level.aktiv` ist an → `faellige_belohnungen` (alles ≤ neue Stufe) → `vergebe(...)`.
  - Meldung im `level.kanal`, sinngemäß: „🎖️ @Name ist jetzt **Stark Industries Praktikant** (Stufe 5) und hat das **Design 2 von
    Black Widow** freigeschaltet!“ mit Embed. **Kein leeres Bild einbetten**, wenn für das Design noch kein Link
    eingetragen ist; dann Text „Das Bild folgt.“. Fehlt der Kanal oder darf der Bot dort nicht schreiben → still überspringen und
    ins Protokoll (`logging`) schreiben; die Vergabe selbst passiert trotzdem.
  - Sinkt die Stufe → Schritt 7. Sonst nichts.
- **Ist `level.aktiv` aus,** wird **gar nichts** vergeben.
- Der bestehende Code in `on_member_update` für Auszeiten läuft danach unverändert weiter.
- Nach einer Ausfallzeit des Bots verpasste Aufstiege: Beim Start (`on_ready`), **nur wenn `level.aktiv` an ist**, gezielt
  nachholen — aber höchstens für **20** Spieler automatisch. Sind es mehr, nichts tun und dem Nutzer per DM schreiben:
  „N Spieler warten — bitte `/level-vorschau` nutzen.“ (Schutz vor Meldungsflut.)
- **Tests:** Rollenwechsel als reine Funktion; Auszeit-Mitschrift läuft weiter; aus → keine Vergabe.

### Schritt 6 — `/level-vorschau` und Nachholen (Admin, versteckt) (`bot:`)
- Berechnet **ohne etwas zu ändern**, wer was bekäme: alle Mitglieder mit Level-Rolle → Stufe → fällige Belohnungen, die im
  Protokoll fehlen. **Und** alle Einlader mit `invite_stats.completed_invites ≥ 1` → fällige Einladungsstufen, die
  fehlen (nur die Stufen 1/5/10 und der Staub ab 11 — frühere Iron-Man-/Staub-Belohnungen bleiben unberührt).
- Antwort nur für den Admin sichtbar: Zusammenfassung („123 Spieler, 210 Designs, 4 Einlader, 35 Staub“) plus vollständige Liste
  als **Textdatei** (wegen der 2000-Zeichen-Grenze). Zwei Knöpfe: **Jetzt vergeben und einschalten** (mit einer zweiten
  Bestätigung) und **Abbrechen**.
- „Jetzt vergeben“: nacheinander, mit kurzer Pause (`PAUSE_BETWEEN_CALLS`-Muster), Fortschrittsanzeige, **keine** Einzelmeldungen im
  Kanal, sondern **eine** Zusammenfassung; danach `level.aktiv` = an. Bricht der Lauf ab, ist er wiederholbar (Protokoll).
- **Fertig, wenn:** Vorschau ändert nachweislich nichts (Test); zweiter Durchlauf vergibt nichts mehr.

### Schritt 7 — Rolle verloren → private Nachricht mit Knöpfen (`bot:`)
- Sinkt die Stufe (Reset, Rolle entfernt) **oder** verlässt jemand den Server (`on_member_remove`) **und** hat der Spieler durch
  die höheren Stufen Belohnungen im Protokoll, dann bekommt der **Owner (`BASTI_USER_ID`) eine private Nachricht**: wer (Name und
  ID), von welcher auf welche Stufe, was er bisher dadurch bekommen hat, mit Knöpfen **Behalten** und **Entziehen**.
- **Bis zur Entscheidung ändert sich nichts.** Behalten → Protokoll `behalten`, Rückfrage `behalten`, nie wieder fragen für
  diesen Fall. Entziehen → `designs.entziehen(...)` für genau diese Belohnungen, Protokoll `entzogen` (erreicht der Spieler die
  Stufe später erneut, wird wieder vergeben, weil `entzogen` als „nicht vergeben“ zählt).
- Knöpfe als **`DurableView`** (der Nutzer antwortet evtl. erst nach Tagen oder nach einem Neustart).
- **Flut vermeiden:** Bei einem MEE6-Reset fallen viele Rollen fast gleichzeitig. Fasse Ereignisse **je Mitglied** über ein
  kurzes Zeitfenster (z. B. 10 Minuten) zusammen und frage höchstens **einmal** je Fall. Wer keine betroffene Belohnung hat,
  löst **keine** Nachricht aus.
- Ist die private Nachricht nicht zustellbar (DMs zu) → Fall bleibt `offen` in `level_rueckfragen`; Befehl **`/level-offen`**
  (Admin, versteckt) listet offene Fälle und erlaubt dieselbe Entscheidung.
- **Tests:** Rollen ersetzen (5 → 10) löst **keine** Rückfrage aus; Stufe sinkt → genau eine; Zusammenfassen; Entziehen
  entfernt genau die richtigen Designs; Behalten lässt alles.

### Schritt 8 — Einladungen umbauen (`bot:`)
- `finalize_invite_pending_if_ready` in `services/invite_store.py`: Die Transaktion, die `invite_stats` hochzählt, bleibt
  **unverändert**. Danach: `neue_anzahl = prior + 1`; Belohnungen des **Einladers** aus `einladung_belohnungen(neue_anzahl)` über
  `vergebe(...)` (Schlüssel wie `einladung:5:design:Spider-Man:2`, `einladung:12:staub`); der **Eingeladene** bekommt **immer**
  5 Staub. **Keine** Iron-Man-Karte mehr. Bei 2–4 und 6–9 bekommt der Einlader **nichts** extra (Abschnitt 4.2).
- `reward_summary` neu gestalten (welche Designs, wie viel Staub für wen) und die Aufrufer anpassen:
  `InviteConfirmationView._after_flag` (Meldungstext), `_send_private_invite_card_reward` bzw. `_build_invite_reward_card_embed`
  (zeigt jetzt „Design freigeschaltet — wähle es mit `/design`“ mit dem Bild, oder „Bild folgt“), Texte in `game_ui_texts.py`
  (`INVITE_SUCCESS` und Verwandte).
- `invite_reward_config.py` **nicht löschen** (Tests/Importe), aber oben mit einem Hinweis versehen: „Wird nicht mehr genutzt —
  siehe `level_reward_config.py`“.
- Bestehende Tests, die das alte Verhalten prüfen (`tests/`: suche `invite`, `configured_first_invite_reward_card`), bewusst
  **anpassen** und im Commit benennen.
- **Fertig, wenn:** Einladungen 1, 2, 5, 10, 11 liefern genau die Tabelle aus 4.2 (Test), der Ablauf der Bestätigungsknöpfe ist
  unverändert.

### Schritt 9 — `/level` und `/einladungen` für Spieler (`bot:`)
- `/level` (nur für den Spieler sichtbar): aktuelle Stufe (Titel + „mindestens Level X“), die **nächste** Stufe mit Belohnung und
  Hinweis aus `LEVEL_HINWEISE`, Anzahl freigeschalteter Designs. Sagt ehrlich, dass das genaue Level zwischen den Stufen nicht
  bekannt ist. Ist `level.aktiv` aus oder die Zuordnung leer: freundlicher Hinweis statt Fehler.
- `/einladungen`: „Du hast N Personen eingeladen“, die nächste Stufe („Noch M bis Spider-Man-Design + 5 Staub“), alle Stufen mit
  ✓ / ○, ab 11 „je 5 Staub“.
- Beide dort eintragen, wo Befehle registriert werden müssen (Kanal-Freigabe-Liste, `botcore/command_api.py` usw. — wie in
  Plan 1, Schritt 4).

### Schritt 10 — Version, Notizen, Übergabe (`bot:` / `docs:`)
- `bot.py`: `__version__` `2.4.0` → `2.5.0`. `README.md` nachziehen.
- `docs/releases/release_notes_v2.5.0.md` (Spielertext: Level-Belohnungen, Einladungs-Stufen, `/level`, `/einladungen`; **ohne** das
  Gewinnspiel zu versprechen). `web/UEBERGABE.md` ergänzen.
- Tag `teil1-plan2-fertig`, pushen.

---

## 8. Tests (Überblick)

`tests/test_level_rewards.py` (neu): Konfiguration stimmig; Stufe aus Rollen (stapelnd/ersetzend, keine Rolle, mehrere);
fällige Belohnungen kumulativ; Protokoll → höchstens einmal, auch nach Entziehen/Verkauf; Vorschau ändert nichts;
Rollenwechsel 5→10 ist kein Verlust, 10→0 ist einer; Einladungsstufen; Admin-Befehle tragen `default_permissions`;
Auszeit-Mitschrift in `on_member_update` läuft weiter. Am Ende **gesamte** Suite, `scripts/validate_cards.py`, `scripts/alpha_smoke.py`
grün.

## 9. Was der Nutzer selbst einrichten muss (schreibe es als Checkliste für ihn)

Das erledigt **nicht** der Bot, sondern MEE6/Discord:
1. **MEE6** → Levels → Rollenbelohnungen: bei Level 1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50 je eine Rolle mit **genau** den
   Namen aus Abschnitt 4.1. (Ob „Vorherige Rollen entfernen“ an oder aus ist, ist egal.)
2. **Alle Bestandsmitglieder** in MEE6 auf **Level 10** setzen (die Regel „wer bis zum Release da war, startet mit Level 10“).
3. **Rollenrechte** in Discord: Bilder/Links (Level 10), Zugriff auf „Sanctum Sanctorum“ (25) und „Archiv“ (30).
4. Im **Discord Developer Portal** beim Bot den „Server Members Intent“ **an** lassen (der Code nutzt ihn schon).
5. Der Bot braucht im Meldungskanal die Rechte **Nachrichten senden** und **Links einbetten**.
6. **Einmalig**, falls Give-OP-/Dev-/Admin-Rollen ohne Discord-Administrator existieren: Servereinstellungen → Integrationen → Bot →
   Befehle → Rolle erlauben (siehe Schritt 1).
7. **Reihenfolge nach der Auslieferung:** `/level-einrichten` → `/level-kanal` → `/level-vorschau` → prüfen →
   **Jetzt vergeben und einschalten**.
8. Gewinnspiel-Chancen bei Level 50: von Hand, nicht Sache des Bots.

## 10. Abnahme durch den Nutzer (Anleitung für ihn schreiben)

Am besten auf einem **Testserver** mit einer Test-Rolle „Stark Industries Praktikant“:
1. Datenbank sichern, Bot aktualisieren, Befehle neu anmelden.
2. Ein Konto **ohne** Admin-Recht: sieht keine Admin-Befehle. Admin: sieht sie.
3. `/level-einrichten`, `/level-kanal`, `/level-vorschau` → Vorschau zeigt den Testspieler, ändert aber nichts.
4. „Jetzt vergeben und einschalten“ → Testspieler hat Design 2 für Black Widow (mit `/design`, wenn ein Bild eingetragen ist).
5. Zweiter Test-Aufstieg (Rolle Level 15 geben) → **Meldung im Kanal**, Rocket-Design.
6. Rolle wegnehmen (auf Level 5 zurück) → **private Nachricht** mit Behalten/Entziehen.
7. Einladung durchspielen: 1. → Cap-Design (+ 5 Staub für den Eingeladenen); später 5. → Spider-Man-Design + 5 Staub.
8. `/level` und `/einladungen` zeigen die richtigen Zahlen.

## 11. Nicht Teil dieses Plans

Website-Änderungen (Level-Zuordnung oder Rückfragen auf der Website: späterer Website-Teil). Ein Gewinnspiel-System. Rollen
vergeben oder entziehen (MEE6 macht das). Anzeige des exakten Levels (bräuchte die MEE6-Rangliste — bewusst nicht gewählt).
Alles aus `VERGLEICH_Drive-gegen-Code.md` (fehlende Missionen, Story).

## 12. Fallstricke

- **Mehrere Ereignisse pro Rollenwechsel:** MEE6 gibt/nimmt Rollen einzeln. Immer die **Gesamtmenge** vorher/nachher vergleichen.
- **`on_member_update` trifft nur zwischengespeicherte Mitglieder.** Der Bot hat die Members-Intent; für das Nachholen
  (`/level-vorschau`) die Mitgliederliste des Servers laden und bei sehr großen Servern nicht auf `guild.members` allein
  verlassen.
- **DMs können scheitern** (geschlossen) — immer abfangen, nie den Ablauf abbrechen.
- **Persistente Knöpfe** (`DurableView`) müssen beim Start wieder angemeldet werden — Muster von `InviteConfirmationView` folgen.
- **Kanal-IDs und Discord-IDs** als **Text** an die Website geben, falls je nötig (JavaScript-Rundungsfehler).
- **Neue Befehle** müssen in der Kanal-Freigabe/Befehlsliste des Bots stehen, sonst antwortet er dort nicht.
- **Zwei Wahrheiten vermeiden:** Belohnungen stehen **nur** in `level_reward_config.py`, nicht zusätzlich verstreut im Code.
- Ein Design ohne Bild-Link (im Drive fehlen Black Widow, The Thing, Captain Marvel, Captain America, Spider-Man, Namor) wird
  trotzdem freigeschaltet und gemeldet — nur eben ohne Bild. Das ist gewollt.
