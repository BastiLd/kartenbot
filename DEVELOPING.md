# Entwicklung unter Windows

## Setup

Projektumgebung aufsetzen:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Optionaler Import-Check:

```powershell
.\.venv\Scripts\python.exe -c "import discord; import bot; print('ok')"
```

## Starten

```powershell
.\.venv\Scripts\python.exe .\bot.py
```

Konfiguration kommt aus:

1. Umgebungsvariablen
2. `.env`
3. `bot_token.txt` oder `token.txt` fuer `BOT_TOKEN`

## Tests

Volle Suite:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1
```

Einzelne Module:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 tests.test_smoke
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 tests.test_card_validation
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 tests.test_interaction_utils
```

Direkte Kartenvalidierung:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_cards.py
```

## Projektaufbau

- `bot.py`
  Startpunkt und zentrale Orchestrierung.
- `botcommands/player_commands.py`
  Sammlung, Daily, Vault und spielernahe Commands.
- `botcommands/gameplay_commands.py`
  Kampf, Mission, Story und Gameplay-Registrierung.
- `botcommands/admin_commands.py`
  Admin- und Panel-Commands.
- `botcore/bootstrap.py`
  Bot-Erzeugung und Run-Entry.
- `botcore/logging_utils.py`
  Logging und Basis-Metriken.
- `botcore/ui_common.py`
  Gemeinsame Views und Pager.
- `botcore/interaction_utils.py`
  Sichere Discord-Antwortpfade fuer `send`, `defer` und `edit`.
- `services/battle.py`
  Battle-Berechnungen und Embed-Helfer.
- `services/battle_state.py`
  Reine Zustandslogik fuer Effekte, Cooldowns und Modifier.
- `services/card_validation.py`
  Regeln fuer die Struktur von `karten.py`.
- `services/user_data.py`
  Nutzerkarten, Dust, Buffs, Teams und Rewards.
- `services/request_store.py`
  Fight- und Mission-Anfragen.
- `services/guild_settings.py`
  Sichtbarkeit, Wartung und Server-bezogene Einstellungen.
- `services/db.py`
  SQLite-Verbindung und Schema-Initialisierung.

## Kartenpflege

Pflege-Regeln fuer `karten.py`:

- jeder Kartenname muss eindeutig sein
- jeder Attackenname muss innerhalb einer Karte eindeutig sein
- jede Attacke braucht `name`, `damage` und `info`
- Seltenheiten muessen auf die bekannten Gruppen abbildbar sein
- Effekt-Typen muessen im Validator bekannt sein
- neue Datenformen zuerst im Validator und dann in den Tests absichern

Wenn du neue Effekte oder Attack-Felder einfuehrst:

1. Logik in `services/` oder `bot.py` erweitern.
2. `services/card_validation.py` anpassen.
3. passende Tests in `tests/` ergaenzen.
4. `scripts/validate_cards.py` und die relevante Test-Suite laufen lassen.

## VS Code / basedpyright

- Der Workspace ist auf `.venv\Scripts\python.exe` ausgelegt.
- Falls `discord` trotzdem als fehlender Import markiert wird:

```text
1. Reload Window
2. Python: Select Interpreter
3. .venv\Scripts\python.exe waehlen
```

## Commit- und Push-Ablauf

Empfohlener Ablauf fuer jede Aenderung:

1. relevante Tests ausfuehren
2. bei Karten-Aenderungen immer `scripts/validate_cards.py` ausfuehren
3. nur die betroffenen Dateien stagen
4. klaren Commit mit Thema der Aenderung erstellen
5. direkt nach `origin/main` pushen
6. GitHub Actions abwarten

Beispiel:

```powershell
git add bot.py tests/test_smoke.py
git commit -m "Beispielhafte Aenderung"
git push origin main
```

## CI

Der Workflow `.github/workflows/main-checks.yml` laeuft auf jedem Push nach `main` und prueft:

- Setup der Windows-Python-Umgebung
- `py_compile` fuer Kernmodule
- Kartenvalidierung
- komplette Test-Suite

Wenn CI rot ist, sollte die lokale Reproduktion zuerst ueber dieselben Repo-Skripte laufen.
