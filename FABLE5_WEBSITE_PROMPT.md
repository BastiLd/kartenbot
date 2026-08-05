# Auftrag an Fable 5 — Dashboard-Webseite für den Kartenbot + Gesamt-Check

> **So benutzt du das (an den Nutzer):** Neuen Chat mit **Fable 5** öffnen, im Projektordner
> `c:\Users\basti\Documents\BOT\kartenbot`, und den kompletten Text unterhalb der Linie als
> ersten Prompt einfügen.

---

## ⚠️ Reihenfolge (WICHTIG)

1. **ZUERST**: Die Dashboard-Webseite bauen (Teil A).
2. **DANACH**: Den Gesamt-Check / Audit des Bots machen (Teil B).

Grund: Falls das Nutzungslimit erreicht wird, ist die Webseite dann schon fertig. Baue die
Webseite **vollständig zu Ende**, bevor du mit dem Audit anfängst.

---

## Kontext: Was ist dieses Projekt?

Das ist **kartenbot** — ein deutscher **Discord-Karten-/Kampf-Spielbot** in **Python**
(`discord.py`, `aiosqlite`). Es ist **KEIN Node-Projekt** und **kein LLM/AI-Bot** — „Tokens"
bedeutet hier den Discord-Bot-Token, nicht AI-Tokens. Das Ziel ist ein **Monitoring- &
Admin-Dashboard** im Stil von „AgentOS": schicke Übersicht über den Bot-Zustand, Spieler,
Economy, Kämpfe und Nutzung — mit coolem Design und dezenten Animationen.

**Wichtige Dateien/Ordner (bitte selbst genauer anschauen):**
- `bot.py` — Haupt-Bot (SEHR groß, ~838 KB). **Logik NICHT verändern.**
- `kartenbot.db` — SQLite-Datenbank (Quelle für fast alle Dashboard-Daten). Läuft im **WAL-Modus**,
  gleichzeitiges Lesen ist also sicher, solange du eine **eigene, read-only Verbindung** benutzt.
- `bot.log` — Laufzeit-Logs (Quelle für Health/Fehler/letzter Neustart).
- `services/` — Kernlogik: `analytics.py`, `stats_export.py`, `db.py`, `battle.py`,
  `user_data.py`, `guild_settings.py`, `invite_store.py`, `card_grant.py` u.a.
- `botcommands/` — `admin_commands.py`, `gameplay_commands.py`, `player_commands.py`.
- `karten.py`, `items.py`, `mission_enemies.py`, `Missionen.txt` — Spieldaten.
- `config.py` / `.env.example` — Token/DB-Pfad-Konfiguration (`BOT_TOKEN`, `KARTENBOT_DB_PATH`).
- `docs/`, `DEVELOPING.md`, `.kiro/specs/`, `archiv_altlasten/` — Doku & alte Specs. **Bitte lesen**,
  um den Bot zu verstehen (siehe „Erst erkunden").

**Datenbank-Tabellen** (bereits vorhanden — das ist dein Daten-Fundus):
`user_karten`, `user_teams`, `user_daily`, `tradingpost`, `user_infinitydust`, `user_units`,
`user_card_buffs`, `fight_requests`, `mission_requests`, `active_sessions`, `managed_threads`,
`invite_stats`, `invite_pending`, `invite_history`, `admin_dust_audit`, `analytics_events`
(inkl. Indizes auf `created_at`, `event_type`, `session_id`), `afk_timers`, `guild_config`,
`guild_allowed_channels`, `guild_message_visibility`, `bot_settings`, u.a.

`analytics_events` ist besonders wertvoll: Spalten u.a. `created_at`, `event_type`, `guild_id`,
`actor_user_id`, `command_name`, `hero_name`, `attack_name`, `payload_json`.
`services/stats_export.py` zeigt bereits, wie man daraus Statistiken/Excel baut — als Vorlage nutzen.

---

# TEIL A — Baue das Dashboard

## Erst erkunden (bevor du Code schreibst)
Schau dir so viel an, wie du kannst, damit das Dashboard wirklich passt:
- Lies `services/analytics.py` (welche `event_type`s werden geloggt) und `services/stats_export.py`
  (welche Auswertungen es schon gibt) komplett.
- Sieh dir das echte Schema an: öffne `kartenbot.db` read-only und lies `sqlite_master`, dann ein
  paar Beispielzeilen je Tabelle (ohne etwas zu ändern).
- Überflieg `docs/`, `DEVELOPING.md`, `.kiro/specs/` und `archiv_altlasten/`, um Features/Begriffe
  (InfinityDust, Units, Missionen, Trading-Post, Invites, OP-Rechte) zu verstehen.
- Falls frühere Chat-/Notiz-Dateien im Projekt liegen, wirf einen Blick hinein.

## Wo
Alles in einen **neuen Ordner `website/`** im Projekt-Root. **Keine bestehenden Bot-Dateien
verändern** (Ausnahme: eine kurze Ergänzung in `README.md` und ggf. ein Eintrag in `.gitignore`
für Website-Build-Artefakte/Secrets ist ok).

## Tech-Stack — deine Wahl
Du entscheidest den besten Stack selbst und begründest ihn kurz in `website/README.md`. Empfehlung
(nur als Startpunkt, nicht bindend): ein **Python-Backend** (FastAPI o.ä.), das nah am Bot ist und
`kartenbot.db` direkt liest, plus ein **modernes, animiertes Frontend**. Wenn du ein
JS-Frontend (Vite/React o.ä.) nimmst, ist das ok — dann bitte **einen einzigen, klar
dokumentierten Start-Befehl** bereitstellen (z. B. `npm run dev` bzw. ein `start`-Skript).

## Design — viel Freiheit
- **„AgentOS"-Feeling**: modernes Dashboard, dunkles Theme, Karten/Panels, Live-Kacheln, Charts,
  dezente Animationen & Übergänge (nicht überladen, performant).
- Übersichtliche Navigation zwischen den Bereichen. Responsiv. Schnell.
- Nutze gern ein Icon-Set und Charts (deine Wahl).

## Inhalte (alle vier Bereiche + Extras)
1. **Bot-Health & Logs**: Online/Offline-Status, Uptime, letzter Neustart, Latenz (falls
   ermittelbar), Fehlerrate & letzte Fehler aus `bot.log` (Log-Viewer mit Filter/Level),
   DB-Größe, Anzahl aktiver Sessions (`active_sessions`), offene Threads (`managed_threads`).
2. **Spieler & Economy**: aktive Spieler (aus `analytics_events` nach Zeitraum), Karten-Besitz
   (`user_karten`), Team-Setups (`user_teams`), Daily-Nutzung (`user_daily`),
   InfinityDust- & Units-Wirtschaft (`user_infinitydust`, `user_units`) inkl. Top-Listen &
   Verteilungen, Trading-Post-Aktivität (`tradingpost`).
3. **Battles & Missionen**: Kampf-/Missions-Statistiken (`fight_requests`, `mission_requests`,
   `analytics_events`), beliebteste Helden/Karten & Angriffe (`hero_name`, `attack_name`),
   Win-Rates falls ableitbar, AFK-Timeouts (`afk_timers`), laufende Sessions.
4. **Command- & Invite-Analytics**: meistgenutzte Commands (`command_name` in `analytics_events`),
   Nutzung über Zeit (Tages-/Stundenverlauf), Invite-Tracking & Wachstum
   (`invite_stats`, `invite_pending`, `invite_history`), Admin-Dust-Audit (`admin_dust_audit`).
- **Zeitraum-Filter** (heute / 7 Tage / 30 Tage / alles) global fürs Dashboard.
- Auto-Refresh (Polling reicht; WebSockets optional).

## Ansehen **und** Aktionen (Admin)
Das Dashboard soll nicht nur anzeigen, sondern auch **Admin-Aktionen** erlauben (der Nutzer will
Kontrolle, „wie ein AgentOS mit Skills"). Beispiele: InfinityDust/Units vergeben oder abziehen,
Karten geben, Trading-Post-Einträge entfernen, `guild_config`-Einstellungen (z. B.
`maintenance_mode`, `beta_enabled`, `alpha_enabled`) umschalten, Sessions/Threads aufräumen.

**Sicherheits-Regeln für Schreib-Aktionen (strikt einhalten):**
- **Login/Auth-Schutz** vor allen schreibenden Endpunkten (einfacher Passwort-/Token-Login,
  Passwort aus `.env`, nicht hartkodiert). Read-Ansichten dürfen ohne Login sein, wenn nur lokal.
- Jede Schreib-Aktion in ein **Audit-Log** schreiben (nutze/erweitere das Muster von
  `admin_dust_audit`), mit Zeit, Aktion, Ziel, Betrag.
- Schreibzugriffe **parametrisiert** (keine SQL-Injection), mit Bestätigungs-Dialog im UI.
- **Niemals** die Bot-Logik in `bot.py` verändern; schreibe nur in die DB-Tabellen, konsistent
  zu den bestehenden `services/`-Funktionen (schau dort ab, wie Dust/Karten korrekt gebucht werden).
- Robust gegen kaputte/teilweise Daten — Fehler sauber abfangen und anzeigen.

## Betrieb: lokal jetzt, ZimaOS/Docker später
- **Jetzt**: läuft lokal (z. B. `http://localhost:8080`). Ein **einziger, dokumentierter
  Start-Befehl** in `website/README.md`.
- **Startet NICHT automatisch mit dem Bot** — separat startbar, damit der Bot unberührt bleibt.
- **Später ZimaOS**: bitte ein **`Dockerfile`** (+ optional `docker-compose.yml`) beilegen, damit
  es später als Container auf ZimaOS laufen kann. DB-Pfad & Login-Passwort über **Env-Variablen**
  konfigurierbar (`KARTENBOT_DB_PATH`, eigenes `DASHBOARD_PASSWORD` o.ä.).
- **Secrets & DB niemals committen**: `.env`, `kartenbot.db`, Build-Artefakte, `node_modules`
  gehören in `.gitignore`. Lege eine `website/.env.example` mit den nötigen Variablen an.

## Doku
Schreibe `website/README.md`: gewählter Stack + kurze Begründung, wie starten (lokal), wie Docker,
welche Env-Variablen, welche Endpunkte/Features es gibt, Sicherheitshinweise.

---

# TEIL B — Gesamt-Check / Audit des Bots (ERST NACH der Webseite)

Wenn die Webseite fertig ist, mach einen **so umfassenden Check wie möglich** vom Bot-Code
(nicht von der neuen Webseite — die hast du gerade gebaut).

**Vorgaben (bitte genau so):**
- **Nur finden & berichten — noch NICHT fixen.** Der Nutzer meldet danach in einer Folgenachricht
  die von echten Nutzern gefundenen Bugs; erst dann wird priorisiert gefixt.
- **Antworte auf Deutsch.**
- **Baseline zuerst aufsetzen und laufen lassen:** `.venv` benutzen bzw. anlegen,
  `pip install -r requirements.txt`, dann Tests:
  `.venv/Scripts/python.exe -m unittest discover -s tests` (≈ 400+ Tests) und
  `python scripts/validate_cards.py`. Ergebnisse im Bericht festhalten.
- **Umfang „ALLES", mehrere Blickwinkel:** Bugs, async/Race-Conditions (z. B. Doppelklick-Locks
  in Kampf-Interaktionen), Kampf-/Balance-Logik, Datenkonsistenz, Sicherheit, Code-Qualität,
  Fehlerbehandlung.
- **Bekannter, wiederkehrender Prüfpunkt:** Die **Farbe der linken Leiste** von Kampf-Embeds soll
  zu dem passen, der **gerade am Zug** ist — in **BEIDEN** Ansichten (PvP **und** Mission).
- Sieh dir zum Verständnis auch `docs/`, `.kiro/specs/`, `DEVELOPING.md` und ältere Notizen an.
- **Ergebnis:** ein strukturierter Bericht (gruppiert nach Schweregrad/Bereich, mit
  `datei:zeile`-Verweisen). **Keine Code-Änderungen am Bot**, bis der Nutzer die Nutzer-Bugs meldet.

---

**Zusammenfassung der Reihenfolge:** 1) `website/` komplett bauen (Ansicht + Admin, cooles Design,
lokal jetzt / Docker für ZimaOS, Secrets nie committen). 2) Danach den vollständigen Bot-Audit als
reinen Bericht auf Deutsch.
