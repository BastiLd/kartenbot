# Entwicklung unter Windows

## Einrichtung

1. `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1`
2. Optional prüfen: `.\.venv\Scripts\python.exe -c "import discord; print(discord.__version__)"`

## Tests

- Einzelner Smoke-Test: `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 tests.test_smoke`
- Alle Tests im `tests/`-Ordner: `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`

## VS Code / basedpyright

- Der Workspace zeigt standardmäßig auf `.venv\Scripts\python.exe`.
- Falls `basedpyright` trotzdem noch `reportMissingImports` für `discord` meldet:
  1. VS Code neu laden.
  2. `Python: Select Interpreter` öffnen.
  3. `.venv\Scripts\python.exe` auswählen.

## Hinweis

- Das Repo nutzt den Windows-Launcher `py` nur für das initiale Setup.
- Für Tests und Skripte wird danach immer die feste Interpreter-Datei aus `.venv` verwendet.
