Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$requirements = Join-Path $root "requirements.txt"
$pythonLauncher = $null

if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonLauncher = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonLauncher = "python"
} else {
    throw "Weder 'py' noch 'python' wurde gefunden. Installiere Python mit aktiviertem PATH/Launcher."
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Erstelle .venv ..."
    & $pythonLauncher -m venv (Join-Path $root ".venv")
}

Write-Host "Installiere Abhängigkeiten ..."
& $venvPython -m pip install -r $requirements

Write-Host "Setup abgeschlossen."
