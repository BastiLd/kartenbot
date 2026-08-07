"""Zugang zum Sprachmodell — die Bot-Hälfte.

Gegenstück zu ``web/app/ollama.py``. Zwei getrennte Zugänge, weil zwei
getrennte Umgebungen: Das Backend im Container hat ``httpx``, der Bot nicht —
dafür bringt discord.py ``aiohttp`` mit. Ein gemeinsames Modul müsste eine
der beiden Abhängigkeiten zusätzlich installieren, und beide Umgebungen sind
absichtlich schlank.

Gelesen werden die Einstellungen aus derselben Tabelle wie überall
(``web_settings``), damit die Website den Zugang einstellt und der Bot ihn nur
benutzt. Kein zweiter Ort, an dem eine Adresse gepflegt werden müsste.
"""
from __future__ import annotations

import logging

from db import db_context

# Voreinstellungen, falls in der Datenbank nichts steht. Dieselben wie auf der
# Website - sonst faende der Bot ein anderes Ollama als die Einstellungsseite.
STANDARD_URL = "http://192.168.68.10:11434"
STANDARD_TIMEOUT = 120


class OllamaFehler(Exception):
    """Das Modell ist nicht erreichbar oder antwortet nicht — Text für Menschen."""


async def _einstellung(schluessel: str, ersatz: str = "") -> str:
    try:
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT value FROM web_settings WHERE key = ?", (schluessel,))
            zeile = await cursor.fetchone()
        return str(zeile[0]).strip() if zeile and zeile[0] is not None else ersatz
    except Exception:
        # Fehlt die Tabelle (die Website lief noch nie), gilt die Voreinstellung.
        return ersatz


async def zugang() -> dict:
    """Adresse, Modell und Zeitlimit — so, wie die Website sie eingestellt hat.

    Fürs Kämpfen gilt ``ollama.model_kampf``, sonst das allgemeine Modell.
    Ein Modell, das Texte gut zusammenfasst, muss im Kampf nichts taugen —
    deshalb gibt es beide getrennt.
    """
    url = (await _einstellung("ollama.url", STANDARD_URL)).rstrip("/")
    modell = (await _einstellung("ollama.model_kampf")
              or await _einstellung("ollama.model"))
    try:
        zeitlimit = max(5, int(await _einstellung("ollama.timeout", "") or STANDARD_TIMEOUT))
    except (TypeError, ValueError):
        zeitlimit = STANDARD_TIMEOUT
    return {"url": url or STANDARD_URL, "modell": modell, "zeitlimit": zeitlimit}


async def frage(prompt: str, *, url: str, modell: str, zeitlimit: int) -> str:
    """Einmal fragen und die Antwort im Wortlaut zurückgeben."""
    import aiohttp

    if not modell:
        raise OllamaFehler("Es ist kein Modell für den Kampf ausgewählt.")
    nutzlast = {"model": modell, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.2}}
    grenze = aiohttp.ClientTimeout(total=zeitlimit)
    try:
        async with aiohttp.ClientSession(timeout=grenze) as sitzung:
            async with sitzung.post(f"{url}/api/generate", json=nutzlast) as antwort:
                if antwort.status >= 400:
                    text = (await antwort.text())[:300]
                    raise OllamaFehler(f"Ollama meldet {antwort.status}: {text}")
                daten = await antwort.json()
        return str((daten or {}).get("response", "")).strip()
    except OllamaFehler:
        raise
    except Exception as fehler:                                # noqa: BLE001
        logging.exception("Ollama-Anfrage fehlgeschlagen")
        raise OllamaFehler(f"Ollama nicht erreichbar: {fehler}") from fehler


async def erreichbar() -> tuple[bool, str]:
    """Kurzer Griff nach der Version — für eine Prüfung vor dem Kampf."""
    import aiohttp

    daten = await zugang()
    grenze = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=grenze) as sitzung:
            async with sitzung.get(f"{daten['url']}/api/version") as antwort:
                if antwort.status >= 400:
                    return False, f"Ollama meldet {antwort.status}"
                return True, ""
    except Exception as fehler:                                # noqa: BLE001
        return False, f"Ollama unter {daten['url']} nicht erreichbar: {fehler}"
