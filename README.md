# Deutscher Discord Sammelkarten-Bot

**Version: v2.3.21**

Ein lokaler Discord-Bot für Sammelkartenkämpfe, Sammlung, Rewards und Admin-Tools. Der Bot nutzt Slash-Commands, Discord-Views und eine SQLite-Datenbank.

## Aktueller Stand

- Sammlung, Daily, Vault, Kämpfe und Admin-Panel sind aktiv.
- Alpha und Beta können pro Server im Entwicklerpanel ein- und ausgeschaltet werden.
- Karten, Kampfregeln und Persistenz sind inzwischen modular aufgeteilt und testbar gemacht.

## Kernfunktionen

- Karten sammeln und verwalten
- Daily-Reward und weitere Reward-Pfade
- 1v1-Kämpfe mit Effekten, Cooldowns und Statusregeln
- Admin-Panel für Wartung, Sync, Sichtbarkeit und Debugging
- SQLite-Persistenz für Sammlung, Teams, Rewards und Anfragen
- Kartenvalidierung und Tests direkt im Repo

## Voraussetzungen

- Windows mit PowerShell
- Python 3.14 empfohlen
- Ein Discord-Bot-Token

## Einrichtung

1. `.env.example` nach `.env` kopieren oder `BOT_TOKEN` direkt als Umgebungsvariable setzen.
2. Optional `KARTENBOT_DB_PATH` setzen, wenn die Datenbank nicht `kartenbot.db` heißen soll.
3. Abhaengigkeiten und `.venv` anlegen:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

4. Bot starten:

```powershell
.\.venv\Scripts\python.exe .\bot.py
```

Alternativ liest der Bot das Token auch aus `bot_token.txt` oder `token.txt`, falls keine `.env` genutzt wird.

## Wichtige Umgebungsvariablen

- `BOT_TOKEN`
- `KARTENBOT_DB_PATH`
- `BUG_REPORT_TALLY_URL`

Nicht in GitHub hochladen:
- `.env`
- `bot_token.txt` oder `token.txt`
- `kartenbot.db`
- `bot.log`
- lokale Archive wie `.git.zip` oder andere `*.zip`

Die `.env` muss nach einem frischen Clone manuell erstellt werden. Mindestens erforderlich:

```env
BOT_TOKEN=dein_discord_bot_token
KARTENBOT_DB_PATH=kartenbot.db
```

Alpha und Beta werden nicht mehr über `.env` gesetzt, sondern pro Server im Entwicklerpanel ein- und ausgeschaltet. Neue Server starten mit Alpha aus.

## Dashboard-Webseite

Unter `website/` liegt ein separates Monitoring- & Admin-Dashboard (FastAPI, liest
`kartenbot.db` read-only, Admin-Aktionen mit Passwort-Login und Audit-Log). Es startet
unabhängig vom Bot — Anleitung, Docker-Setup und Env-Variablen: siehe `website/README.md`.

## Wichtige Befehle

- `/anfang`
- `/täglich`
- `/kampf`
- `/sammlung`
- `/vault`
- `/verbessern`
- `/konfigurieren ...`
- `/entwicklerpanel`
- `/bot-status`

Die interne Command-Registrierung liegt inzwischen in den Modulen unter `botcommands/`.

## Projektstruktur

- `bot.py`
  Startpunkt, zentrale Laufzeitobjekte und Glue-Code.
- `botcommands/`
  Slash-Command-Registrierung nach Bereichen.
- `botcore/`
  Bootstrap, Logging, gemeinsame UI-Bausteine und Interaction-Helper.
- `services/`
  Battle-Logik, Battle-State, Persistenz, Kartenvalidierung und Settings.
- `scripts/`
  Setup, Test-Runner, Alpha-Smoke-Check und Kartenvalidierung.
- `tests/`
  Unit- und Regressionstests.
- `karten.py`
  Aktive Kartendaten.
- `karten_legacy_backup.py`
  Altbestand nur als Referenz, nicht Teil der aktiven Laufzeit.

## Karten pflegen

Neue Karten werden in `karten.py` gepflegt. Jede Karte braucht mindestens:

```python
{
    "name": "Test Hero",
    "beschreibung": "Kurzbeschreibung.",
    "bild": "https://example.com/test-hero.png",
    "seltenheit": "Legendary",
    "hp": 140,
    "attacks": [
        {
            "name": "Testschlag",
            "damage": [10, 20],
            "info": "Beschreibt kurz den Angriff.",
        }
    ],
}
```

Vor einem Commit sollte jede Karten-Änderung validiert werden:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_cards.py
```

Der Validator prüft unter anderem:

- Pflichtfelder
- bekannte Seltenheiten
- doppelte Karten- und Attackennamen
- gueltige Attacken-Struktur
- bekannte Effekt-Typen und deren Pflichtwerte

## Tests und Qualitaet

Alle Tests:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1
```

Einzelne Tests:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 tests.test_smoke
```

Alpha-Smoke-Check:

```powershell
.\.venv\Scripts\python.exe .\scripts\alpha_smoke.py
```

Bei jedem Push auf `main` läuft zusätzlich GitHub Actions:

- Setup der Python-Umgebung
- `py_compile`
- Alpha-Smoke-Check
- Kartenvalidierung
- komplette Test-Suite

## Alpha-Freigabe

Für die geschlossene Alpha liegt ein kompaktes Runbook in [ALPHA_RUNBOOK.md](ALPHA_RUNBOOK.md). Dort stehen:

- lokale Pflichtchecks
- Live-Test auf Discord
- Rollback-Hinweise

## Hinweise

- Voice-Support ist nicht das Ziel dieses Projekts. Eine optionale `discord.py`-Warnung zu `davey` kann weiterhin auftauchen.
- Lokale Hilfsdateien, Logs oder Temp-Ordner sollten nicht mit committed werden.
