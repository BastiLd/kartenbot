param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TestArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw ".venv fehlt. Führe zuerst scripts/setup_windows.ps1 aus."
}

if ($TestArgs.Count -eq 0) {
    & $venvPython -m unittest discover -s (Join-Path $root "tests")
    exit $LASTEXITCODE
}

& $venvPython -m unittest @TestArgs
exit $LASTEXITCODE
