# Alpha Runbook

## Vor dem Start
- `.venv` anlegen: `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1`
- Tests ausführen: `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`
- Smoke-Checks ausführen: `.\.venv\Scripts\python.exe .\scripts\alpha_smoke.py`
- Karten validieren: `.\.venv\Scripts\python.exe .\scripts\validate_cards.py`

## Live-Check auf Discord
- Bot mit echtem Token starten
- Slash-Commands synchronisiert prüfen
- `/täglich`, `/sammlung`, `/kampf`, `/mission`, `/entwicklerpanel` je einmal testen
- Einen PvP-Kampf und einen Missionskampf komplett durchspielen
- Logs beobachten: keine unerwarteten Exceptions, keine hängen gebliebenen Views

## Rollback
- Letzten stabilen Commit auf `main` notieren
- Wenn nötig: neuen Hotfix-Branch vom aktuellen `main` schneiden
- Kritischen Fehler zuerst auf Staging-/Testserver reproduzieren

## Nach der Alpha
- Häufige User-Fehler sammeln
- Kampf-Embeds und Admin-Feedback anhand echter Nutzungsdaten anpassen
