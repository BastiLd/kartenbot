Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$requirements = Join-Path $root "requirements.txt"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' wurde nicht gefunden. Installiere Python mit aktiviertem PATH/Launcher."
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Erstelle .venv ..."
    & py -m venv (Join-Path $root ".venv")
}

Write-Host "Installiere Abhängigkeiten ..."
& $venvPython -m pip install -r $requirements

Write-Host "Setup abgeschlossen."
