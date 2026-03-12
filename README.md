# Deutscher Discord Sammelkarten-Bot

Ein lokaler Discord-Bot fuer Sammelkartenkaempfe, Sammlung, Rewards und Admin-Tools. Der Bot nutzt Slash-Commands, Discord-Views und eine SQLite-Datenbank.

## Aktueller Stand

- Sammlung, Daily, Vault, Kaempfe und Admin-Panel sind aktiv.
- Mission und Story sind aktuell standardmaessig hinter der Alpha-Phase verborgen.
- Karten, Kampfregeln und Persistenz sind inzwischen modular aufgeteilt und testbar gemacht.

## Kernfunktionen

- Karten sammeln und verwalten
- Daily-Reward und weitere Reward-Pfade
- 1v1-Kaempfe mit Effekten, Cooldowns und Statusregeln
- Admin-Panel fuer Wartung, Sync, Sichtbarkeit und Debugging
- SQLite-Persistenz fuer Sammlung, Teams, Rewards und Anfragen
- Kartenvalidierung und Tests direkt im Repo

## Voraussetzungen

- Windows mit PowerShell
- Python 3.14 empfohlen
- Ein Discord-Bot-Token

## Einrichtung

1. `.env.example` nach `.env` kopieren oder `BOT_TOKEN` direkt als Umgebungsvariable setzen.
2. Optional `KARTENBOT_DB_PATH` setzen, wenn die Datenbank nicht `kartenbot.db` heissen soll.
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
- `ALPHA_PHASE`
- `BUG_REPORT_TALLY_URL`

Hinweis zu `ALPHA_PHASE`:
- Standard ist `true`.
- Bei `ALPHA_PHASE=0` oder `ALPHA_PHASE=false` werden die ausgeblendeten Alpha-Features wieder aktiviert.

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

Vor einem Commit sollte jede Karten-Aenderung validiert werden:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_cards.py
```

Der Validator prueft unter anderem:

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

Bei jedem Push auf `main` laeuft zusaetzlich GitHub Actions:

- Setup der Python-Umgebung
- `py_compile`
- Alpha-Smoke-Check
- Kartenvalidierung
- komplette Test-Suite

## Alpha-Freigabe

Fuer die geschlossene Alpha liegt ein kompaktes Runbook in [ALPHA_RUNBOOK.md](ALPHA_RUNBOOK.md). Dort stehen:

- lokale Pflichtchecks
- Live-Test auf Discord
- Rollback-Hinweise

## Hinweise

- Voice-Support ist nicht das Ziel dieses Projekts. Eine optionale `discord.py`-Warnung zu `davey` kann weiterhin auftauchen.
- Lokale Hilfsdateien, Logs oder Temp-Ordner sollten nicht mit committed werden.
