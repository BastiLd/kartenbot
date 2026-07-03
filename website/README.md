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
