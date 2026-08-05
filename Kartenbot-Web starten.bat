@echo off
setlocal
chcp 65001 >nul
title Kartenbot Web

rem ===========================================================================
rem  Doppelklick genuegt: startet das Backend und oeffnet die Seite im Browser.
rem  Beim ersten Mal wird eine Python-Umgebung angelegt, das dauert kurz.
rem ===========================================================================

cd /d "%~dp0web"

echo.
echo   Kartenbot Web wird gestartet ...
echo.

if not exist ".env" (
    echo   [!] Es fehlt die Datei web\.env mit deinem Passwort.
    echo.
    echo       Kopiere web\.env.example nach web\.env und trage
    echo       bei WEB_PASSWORD dein Wunschpasswort ein.
    echo.
    if exist ".env.example" (
        choice /C JN /M "   Soll ich die Datei jetzt anlegen und oeffnen"
        if errorlevel 2 goto :ende
        copy /y ".env.example" ".env" >nul
        notepad ".env"
        echo.
        echo   Bitte speichern und diese Datei erneut starten.
    )
    goto :ende
)

if not exist ".venv\Scripts\python.exe" (
    echo   Erste Einrichtung - das passiert nur einmal ...
    python -m venv .venv || goto :keinpython
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fehler
    echo   Fertig eingerichtet.
    echo.
)

rem Port aus der .env lesen, sonst 8090.
set "PORT=8090"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="WEB_PORT" set "PORT=%%B"
)
set "PORT=%PORT: =%"

echo   Adresse: http://127.0.0.1:%PORT%
echo   Zum Beenden dieses Fenster schliessen oder Strg+C druecken.
echo.

start "" "http://127.0.0.1:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
goto :ende

:keinpython
echo.
echo   [!] Python wurde nicht gefunden.
echo       Installiere es von https://www.python.org/downloads/
echo       und setze dabei den Haken bei "Add Python to PATH".
goto :ende

:fehler
echo.
echo   [!] Die Einrichtung ist fehlgeschlagen - siehe Meldungen oben.

:ende
echo.
pause
endlocal
