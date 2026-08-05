"""Anbindung an Ollama (lokale KI).

Wird nur benutzt, wenn du die KI-Auswertung in den Einstellungen einschaltest.
Adresse und Modell sind frei einstellbar.

Der Modell-Finder prüft mehrere installierte Modelle gleichzeitig mit einer
kleinen Testaufgabe und meldet, welche davon brauchbar antworten und wie
schnell — damit du nicht raten musst, welches Modell auf deiner Maschine passt.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from . import settings

# Testaufgabe für den Modell-Finder: eindeutige, sehr kurze Antwort erwartet.
PROBE_PROMPT = (
    "Antworte NUR mit einem einzigen Wort, ohne Punkt und ohne Erklärung. "
    "Welche Farbe hat der Himmel bei wolkenlosem Wetter am Mittag?"
)
PROBE_EXPECTED = ("blau", "blue", "hellblau")


class OllamaError(Exception):
    """Ollama-Fehler, bereits verständlich formuliert."""


_HINTS = (
    ("model requires more system memory", "Zu wenig Arbeitsspeicher für dieses Modell. "
     "Nimm ein kleineres Modell oder stelle Ollama auf reinen CPU-Betrieb."),
    ("out of memory", "Der Grafikspeicher reicht nicht. Kleineres Modell wählen oder "
     "Ollama mit OLLAMA_NUM_GPU=0 auf CPU stellen."),
    ("not found", "Dieses Modell ist auf dem Server nicht installiert."),
    ("context", "Die Anfrage ist zu lang für das Modell. Weniger Text mitschicken "
     "oder num_ctx erhöhen."),
    ("connection", "Ollama ist nicht erreichbar. Läuft der Dienst, und stimmt die Adresse "
     "in den Einstellungen?"),
)


def explain(raw: str) -> str:
    low = str(raw).lower()
    for needle, text in _HINTS:
        if needle in low:
            return text
    return f"Ollama meldet: {raw}"


def base_url() -> str:
    return settings.get("ollama.url").rstrip("/")


def timeout_seconds() -> int:
    return max(5, settings.get_int("ollama.timeout"))


async def _get(path: str, timeout: float = 8.0):
    url = base_url() + path
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise OllamaError(explain(f"{exc.response.status_code} {exc.response.text[:200]}")) from exc
    except httpx.RequestError as exc:
        raise OllamaError(explain(f"connection: {exc}")) from exc


async def status() -> dict:
    try:
        version = await _get("/api/version", timeout=5.0)
    except OllamaError as exc:
        return {"ok": False, "url": base_url(), "error": str(exc)}
    return {
        "ok": True,
        "url": base_url(),
        "version": (version or {}).get("version", ""),
        "model": settings.get("ollama.model"),
        "ai_enabled": settings.get_bool("ai.enabled"),
    }


async def models() -> list[dict]:
    data = await _get("/api/tags", timeout=10.0)
    out = []
    for entry in (data or {}).get("models", []):
        details = entry.get("details") or {}
        out.append({
            "name": entry.get("name"),
            "size": entry.get("size", 0),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "family": details.get("family"),
        })
    return sorted(out, key=lambda m: m.get("size", 0))


async def generate(prompt: str, *, model: str | None = None, as_json: bool = False,
                   timeout: float | None = None) -> str:
    name = model or settings.get("ollama.model")
    if not name:
        raise OllamaError("Es ist kein Modell ausgewählt. Wähle eines in den Einstellungen "
                          "oder benutze den Modell-Finder.")
    payload = {"model": name, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.2}}
    if as_json:
        payload["format"] = "json"
    try:
        async with httpx.AsyncClient(timeout=timeout or timeout_seconds()) as client:
            response = await client.post(base_url() + "/api/generate", json=payload)
            response.raise_for_status()
            return (response.json() or {}).get("response", "").strip()
    except httpx.HTTPStatusError as exc:
        raise OllamaError(explain(exc.response.text[:300])) from exc
    except httpx.ReadTimeout as exc:
        raise OllamaError(
            "Das Modell hat nicht rechtzeitig geantwortet. Auf der CPU ist das normal — "
            "erhöhe das Zeitlimit in den Einstellungen oder nimm ein kleineres Modell."
        ) from exc
    except httpx.RequestError as exc:
        raise OllamaError(explain(f"connection: {exc}")) from exc


async def generate_json(prompt: str, *, model: str | None = None) -> dict:
    raw = await generate(prompt, model=model, as_json=True)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError("Das Modell hat keine auswertbare Antwort geliefert.") from exc
    return value if isinstance(value, dict) else {"wert": value}


# --------------------------------------------------------------------------
# Modell-Finder
# --------------------------------------------------------------------------
async def _probe(name: str, per_model_timeout: float) -> dict:
    started = time.perf_counter()
    try:
        answer = await generate(PROBE_PROMPT, model=name, timeout=per_model_timeout)
    except OllamaError as exc:
        return {"model": name, "ok": False, "error": str(exc)}
    seconds = round(time.perf_counter() - started, 2)
    normalized = answer.strip().strip(".").lower()
    hit = any(word in normalized for word in PROBE_EXPECTED)
    return {
        "model": name,
        "ok": hit,
        "seconds": seconds,
        "answer": answer[:120],
        "error": None if hit else "Antwort passt nicht zur Testaufgabe — "
                                  "das Modell ist für diese Auswertung wahrscheinlich ungeeignet.",
    }


async def find_model(candidates: list[str] | None = None, per_model_timeout: float = 90.0,
                     parallel: int = 3) -> dict:
    """Testet mehrere Modelle gleichzeitig und meldet, welches am besten passt.

    Es laufen absichtlich nicht alle auf einmal los: Ollama lädt jedes Modell in
    den Speicher, zu viele gleichzeitig bringen die Maschine ins Schwitzen.
    """
    names = candidates or [m["name"] for m in await models()]
    if not names:
        raise OllamaError("Auf dem Server ist kein einziges Modell installiert.")

    limiter = asyncio.Semaphore(max(1, parallel))

    async def run(name: str) -> dict:
        async with limiter:
            return await _probe(name, per_model_timeout)

    results = await asyncio.gather(*(run(n) for n in names), return_exceptions=True)
    clean = [r for r in results if isinstance(r, dict)]
    working = sorted([r for r in clean if r["ok"]], key=lambda r: r["seconds"])
    return {
        "tested": clean,
        "working": working,
        "recommended": working[0]["model"] if working else None,
    }
