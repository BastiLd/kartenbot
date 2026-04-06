# Server-Upload README

Damit der Bot auf einem Server 24/7 laufen kann, brauchst du nicht den ganzen Projektordner hochzuladen.

## Pflicht-Dateien und Ordner

Diese Dateien und Ordner müssen auf den Server:

- `bot.py`
- `karten.py`
- `config.py`
- `db.py`
- `requirements.txt`
- `.env`
- `botcommands/`
- `botcore/`
- `services/`
- `scripts/`
  Nur nötig, wenn du die Hilfsskripte auf dem Server auch benutzen willst.

## Optional, aber meistens sinnvoll

- `kartenbot.db`
  Nur hochladen, wenn du den aktuellen Datenstand, Karten, Dust, Missionsfortschritt und Einstellungen mitnehmen willst.
- `tests/`
  Nur nötig, wenn du direkt auf dem Server Tests laufen lassen willst.
- `.github/`, `.vscode/`, `release/`, `.tmp/`, `node_modules/`
  Nicht nötig für den Bot-Betrieb.

## Nicht auf den Server nötig

Diese Sachen brauchst du für den Live-Betrieb nicht:

- `archiv_altlasten/`
- `DEVELOPING.md`
- `README.md`
- `README_SERVER_UPLOAD.md`
- `pyrightconfig.json`
- `list_commands.py`
- `test_commands.py`

## Empfohlener Server-Ablauf

1. Projektordner auf den Server kopieren.
2. In den Projektordner wechseln.
3. Virtuelle Umgebung anlegen:

```powershell
python -m venv .venv
```

4. Abhängigkeiten installieren:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Unter Linux stattdessen:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

5. `.env` korrekt setzen. Mindestens der Bot-Token muss stimmen.
6. Bot starten:

```powershell
.venv\Scripts\python.exe bot.py
```

Unter Linux:

```bash
.venv/bin/python bot.py
```

## Für echten 24/7-Betrieb

Nutze auf dem Server einen Prozessmanager.

- Linux: `systemd`, `pm2`, `supervisor` oder `screen`/`tmux`
- Windows Server: Aufgabenplanung, NSSM oder PM2

Am saubersten ist Linux mit `systemd`.

## Kurzfassung

Wenn du nur das Nötigste hochladen willst, nimm:

- `bot.py`
- `karten.py`
- `config.py`
- `db.py`
- `requirements.txt`
- `.env`
- `botcommands/`
- `botcore/`
- `services/`
- optional `kartenbot.db`
