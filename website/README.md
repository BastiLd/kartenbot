# Kartenbot Dashboard 🃏

Monitoring- & Admin-Dashboard für den Kartenbot im „AgentOS“-Stil: dunkles Theme,
Live-Kacheln, Charts und Admin-Aktionen — läuft komplett getrennt vom Bot und liest
dessen SQLite-Datenbank (`kartenbot.db`) sowie `bot.log`.

## Stack & Begründung

- **Backend: FastAPI + Uvicorn (Python)** — bleibt in der Sprache des Bots, kann die
  Spieldaten-Module (`karten.py`, `services/card_variants.py`) direkt importieren
  (Kartennamen-Validierung) und liest die DB über eine **eigene read-only-Verbindung**
  (`mode=ro`, WAL-sicher). Kein ORM, nur parametrisierte SQL-Queries.
- **Frontend: statisches HTML/CSS/Vanilla-JS + Chart.js (CDN)** — kein Node/Build-Schritt
  nötig, ein einziger Startbefehl, trotzdem modernes animiertes Dashboard. Fonts & Chart.js
  kommen per CDN (bei komplett offline gewünschtem Betrieb lokal ablegen).

## Starten (lokal)

```powershell
# einmalig (aus dem Projekt-Root, nutzt die bestehende .venv des Bots):
.venv\Scripts\pip.exe install -r website\requirements.txt
copy website\.env.example website\.env   # dann DASHBOARD_PASSWORD eintragen

# Start (der eine Befehl):
cd website
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8080
```

Dann <http://localhost:8080> öffnen. Das Dashboard startet **nicht** automatisch mit dem
Bot — es ist bewusst separat.

## Versionierung & Docker-Image

Die Datei `website/VERSION` enthält die aktuelle Dashboard-Version (z.B. `1.1.0`) und
wird unten links im Sidebar-Footer angezeigt (`v1.1.0`) — so lässt sich prüfen, ob ein
Container tatsächlich die neue Version fährt. Bei jeder inhaltlichen Änderung am
Dashboard `website/VERSION` hochzählen.

Die GitHub Action (`.github/workflows/dashboard-image.yml`) baut bei jedem Push auf
`main`, der `website/**` betrifft, automatisch ein neues Image und pusht es als
`ghcr.io/bastild/kartenbot-dashboard:latest` **und** zusätzlich mit dem konkreten
Versions-Tag aus `website/VERSION` (z.B. `:1.1.0`).

**Wichtig für ZimaOS/Docker-Update:** Ein Update auf denselben Tag `:latest` wird von
vielen Auto-Update-Mechanismen nur erkannt, wenn sie den Image-**Digest** vergleichen,
nicht nur den Tag-Namen. Falls ZimaOS nach einem Push nicht automatisch aktualisiert:

- Sicherstellen, dass die "Always pull latest image" / Digest-Vergleich-Option beim
  Auto-Update aktiv ist (nicht nur "Tag geändert?").
- Alternativ den konkreten Versions-Tag (z.B. `:1.1.0`) im Compose/ZimaOS-App
  eintragen und bei jedem Update manuell auf die neue Versionsnummer setzen — das
  erzwingt garantiert einen Pull.
- Zur Kontrolle: die im Dashboard angezeigte Versionsnummer mit dem `website/VERSION`
  im aktuellsten `main`-Commit vergleichen.

Das Dashboard prüft zusätzlich selbst alle 30 min gegen GitHub, ob eine neuere
Version existiert — dann erscheint unten links ein gelber „⬆ Update verfügbar“-Badge
mit einer Schritt-für-Schritt-Anleitung (auch per Klick auf die Versionsnummer).

## Online-Status & Uptime

Der Bot schreibt jede Minute einen Heartbeat in `bot_settings` (Keys
`heartbeat_at`/`started_at`) — daraus liest das Dashboard präzisen Online-Status
und Uptime. **Dafür muss der Bot mindestens auf dem Stand dieses Commits laufen**
(Bot neu deployen!). Für ältere Bot-Versionen fällt das Dashboard auf die
Log-Heuristik zurück (letzter Log-Eintrag/Analytics-Event < 15 min).

## Docker / ZimaOS

```bash
# aus dem Projekt-Root:
docker compose -f website/docker-compose.yml up -d --build
```

Der Container mountet `kartenbot.db` (+ WAL/SHM) read-write (für Admin-Aktionen) und
`bot.log` read-only nach `/data/`. Auf ZimaOS die Volume-Pfade in
`website/docker-compose.yml` an den Datenordner des Bots anpassen.

## Env-Variablen

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `KARTENBOT_DB_PATH` | nein (Default `kartenbot.db`) | Pfad zur Bot-DB |
| `KARTENBOT_LOG_PATH` | nein (Default `bot.log`) | Pfad zur Logdatei |
| `DASHBOARD_PASSWORD` | **ja für Admin-Aktionen** | ohne Passwort ist das Dashboard read-only |
| `DASHBOARD_SESSION_SECRET` | nein | festes Cookie-Secret (sonst zufällig pro Start) |
| `DASHBOARD_SESSION_TTL` | nein (43200 s) | Login-Gültigkeit |
| `DASHBOARD_HOST` / `DASHBOARD_PORT` | nein (127.0.0.1 / 8080) | Bind-Adresse |
| `DASHBOARD_TZ` | nein (Europe/Vienna) | Zeitzone für Tages-/Stundenstatistik |
| `DASHBOARD_UPDATE_URL` | nein | URL für den Update-Check (Default: `website/VERSION` auf GitHub main) |
| `BOT_TOKEN` | nein | Discord-Bot-Token (nur lesend): zeigt echte User-/Gilden-Namen statt IDs; Namen werden in `dashboard_name_cache` in der DB gecacht und sind dann auch per Name suchbar. Lokal wird das Token automatisch aus der Projekt-`.env` bzw. `bot_token.txt` übernommen; im Docker/ZimaOS als Env-Variable setzen. Ohne Token bleiben IDs sichtbar. |

## Features

**Ansicht** (Zeitraum-Filter Heute/7 Tage/30 Tage/Alles, Auto-Refresh alle 15 s):

1. **Health & Logs** — Online-Heuristik (letztes Log < 15 min), Uptime/letzter Neustart,
   Fehler & Warnungen (24 h), DB-Größe, aktive Sessions/Threads, AFK-Timer,
   Log-Viewer mit Level-Filter und Volltextsuche (inkl. Tracebacks).
2. **Spieler & Economy** — aktive Spieler, Top-Listen (Dust/Units/Sammler), Karten-
   Verteilung, Team-Größen, Daily-Nutzung, Trading-Post, Spieler-Detailsuche per User-ID.
3. **Battles & Missionen** — Kämpfe, beliebteste Helden/Attacken, Win-Rates
   (aus `fight_result`-Payloads), Kampf-Modi, Kampf-/Missions-Anfragen, AFK-Timer,
   Bug-Feedback-Zähler.
4. **Commands & Invites** — meistgenutzte Commands, Events pro Tag/Uhrzeit,
   Invite-Tracking (Top-Inviter, offene Invites, Status), Admin-Dust-Audit.

**Admin-Aktionen** (nur nach Login):

- InfinityDust / Units geben & abziehen (identische Buchungslogik wie
  `services/user_data.py`: atomare Upserts, Abziehen maximal bis 0)
- Karten geben/entfernen (validiert gegen `karten.py` inkl. Varianten)
- Trading-Post-Einträge löschen
- Guild-Flags umschalten (`maintenance_mode`, `beta_enabled`, `alpha_enabled`)
- Aufräumen: beendete/24 h alte Sessions, Threads, AFK-Timer

## API-Endpunkte

| Methode | Pfad | Auth |
|---|---|---|
| GET | `/api/health`, `/api/logs`, `/api/overview`, `/api/players`, `/api/battles`, `/api/analytics`, `/api/user/{id}`, `/api/meta` | – |
| POST | `/api/admin/login`, `/api/admin/logout` | Passwort |
| GET | `/api/admin/status` | – |
| POST | `/api/admin/currency`, `/api/admin/card`, `/api/admin/tradingpost/delete`, `/api/admin/guild-flag`, `/api/admin/cleanup` | Login |
| GET | `/api/admin/guilds`, `/api/admin/audit` | Login |

## Sicherheit

- **Bot-Logik unangetastet**: `bot.py` und `services/` werden nur gelesen; Schreibzugriffe
  gehen ausschließlich in die DB-Tabellen und spiegeln die bestehenden Buchungsmuster.
- **Jede** Schreib-Aktion landet im Audit-Log (`dashboard_audit`-Tabelle, gleiches Muster
  wie `admin_dust_audit`); Dust-Aktionen werden zusätzlich in `admin_dust_audit` gespiegelt.
- Alle Queries parametrisiert; Guild-Flag-Spalten sind whitelisted; Beträge gedeckelt;
  Bestätigungs-Dialog vor jeder Aktion im UI.
- Login: Passwort aus `.env`, HMAC-signiertes HttpOnly-Cookie (SameSite=strict),
  konstantzeitiger Vergleich. Ohne `DASHBOARD_PASSWORD` sind alle Schreib-Endpunkte
  deaktiviert (HTTP 503).
- Standard-Bind ist `127.0.0.1` — nur lokal erreichbar. Wer es ins LAN stellt
  (ZimaOS), sollte zwingend ein starkes Passwort setzen; die Lese-Ansichten sind ohne
  Login sichtbar.
- Secrets/DB werden nicht committet (`.env`, `*.db`, `bot.log` stehen in `.gitignore`).
