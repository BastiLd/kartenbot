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

# --------------------------------------------------------------------------
# Zweite Testaufgabe: Kann das Modell eine Kampflage lesen?
# --------------------------------------------------------------------------
# Das Modell für den Testlauf muss etwas anderes können als das für die
# Auswertung: nicht Text verstehen, sondern aus Zahlen die richtige
# Entscheidung ableiten. Deshalb eine eigene Aufgabe mit eigener Bewertung.
#
# Drei Lagen, damit Raten auffliegt: Bei einer einzigen Frage läge ein Modell
# schon mit reinem Würfeln in einem von drei Fällen richtig.
KAMPF_AUFGABEN = (
    {
        "frage": "Du hast 12 von 140 Lebenspunkten, der Gegner hat noch 8.\n"
                 "1) Schlag — 9 bis 11 Schaden\n"
                 "2) Aufbau — kein Schaden, erhöht deinen Schaden ab der nächsten Runde\n"
                 "3) Rückzug — kein Schaden, du heilst 20",
        "richtig": 1,
        "warum": "Der Gegner stirbt sofort — alles andere verschenkt den Sieg.",
    },
    {
        "frage": "Du hast 6 von 140 Lebenspunkten, der Gegner hat noch 95.\n"
                 "Der Gegner macht pro Runde etwa 15 Schaden.\n"
                 "1) Schlag — 9 bis 11 Schaden\n"
                 "2) Heiltrank — du heilst 40\n"
                 "3) Wuchtschlag — 20 Schaden, danach 3 Runden nicht verfügbar",
        "richtig": 2,
        "warum": "Ohne Heilung ist der nächste Treffer tödlich, und 95 Leben "
                 "sind mit einem Schlag nicht wegzubekommen.",
    },
    {
        "frage": "Du hast 100 von 140 Lebenspunkten, der Gegner hat 60.\n"
                 "Der Gegner ist getarnt: Angriffe verfehlen ihn, außer sie sind "
                 "unblockbar.\n"
                 "1) Schlag — 12 Schaden\n"
                 "2) Wuchtschlag — 25 Schaden\n"
                 "3) Zielsucher — 8 Schaden, unblockbar",
        "richtig": 3,
        "warum": "Gegen Tarnung trifft nur der unblockbare Angriff — die "
                 "anderen machen null Schaden.",
    },
)

KAMPF_PROBE_HINWEIS = (
    "Du spielst ein Kartenspiel und bist am Zug. Wähle den besten Angriff.\n"
    "Antworte NUR mit der Nummer (1, 2 oder 3). Keine Erklärung, kein Punkt.\n\n"
)

# Ab so vielen richtigen Antworten gilt ein Modell als brauchbar. Zwei von
# drei: Ein Ausrutscher ist verzeihlich, Raten reicht nicht.
KAMPF_MINDESTENS = 2


class OllamaError(Exception):
    """Ollama-Fehler, bereits verständlich formuliert."""


def testlauf_prompt(lauf: dict) -> str:
    """Aus den Zahlen eines Testlaufs eine Frage ans Sprachmodell.

    Bewusst knapp gehalten: Alle 33 Paarungen mitzuschicken bläht die Anfrage
    auf und bringt nichts — die Extreme sagen das Wesentliche. Auf einer CPU
    entscheidet die Länge der Anfrage maßgeblich über die Wartezeit.
    """
    ergebnis = lauf.get("ergebnis") or {}
    durchgaenge = ergebnis.get("durchgaenge") or ([ergebnis] if ergebnis.get("paarungen") else [])
    rolle = {"boss": "ein Boss aus einer Mission",
             "klein": "ein kleiner Gegner aus den frühen Wellen einer Mission"}.get(
                 ergebnis.get("rolle"), "eine Spielerkarte")

    zeilen = [
        f"Karte: {ergebnis.get('karte') or lauf.get('karten_name')}",
        f"Das ist {rolle}.",
        "",
    ]
    for d in durchgaenge:
        weise = {"optimal": "bei perfektem Spiel",
                 "average": "wenn beide Seiten Fehler machen"}.get(d.get("spielweise"), "")
        paarungen = d.get("paarungen") or []
        schwer = ", ".join(f"{p['gegner']} ({p['siegquote']} %)" for p in paarungen[:4])
        leicht = ", ".join(f"{p['gegner']} ({p['siegquote']} %)" for p in paarungen[-4:])
        zeilen += [
            f"Siegquote {weise}: {d.get('siegquote')} % aus {d.get('kaempfe')} Kämpfen "
            f"gegen {len(paarungen)} Gegner.",
            f"Durchschnittlich {d.get('runden_schnitt')} Runden je Kampf.",
            f"Verliert am deutlichsten gegen: {schwer or '—'}",
            f"Gewinnt am deutlichsten gegen: {leicht or '—'}",
            "",
        ]
    if ergebnis.get("vergleich"):
        zeilen.append(f"Beobachtung: {ergebnis['vergleich'].get('text')}")

    return (
        "Du bist Spieldesigner und beurteilst die Ausgewogenheit einer Karte in "
        "einem Discord-Kartenspiel. Unten stehen die Ergebnisse einer Simulation.\n\n"
        "Antworte auf Deutsch, in höchstens fünf Sätzen, ohne Aufzählung und ohne "
        "Überschrift. Sage: Ist die Karte zu stark, zu schwach oder stimmig? "
        "Woran liegt es — an den Lebenspunkten, am Schaden, an bestimmten "
        "Nebenwirkungen? Und was würdest du ändern? Wenn alles passt, sage das "
        "klar und schlage nichts vor.\n\n"
        + "\n".join(zeilen)
    )


async def beurteile_testlauf(lauf: dict) -> tuple[str, str]:
    """Lässt das Sprachmodell den Testlauf in Worten beurteilen.

    Gibt (Text, benutztes Modell) zurück. Genommen wird das Modell für den
    Testlauf; ist keines gesetzt, das allgemeine.
    """
    modell = settings.get("ollama.model_kampf").strip() or settings.get("ollama.model").strip()
    if not modell:
        raise OllamaError("Es ist kein Modell ausgewählt. Wähle eines in den "
                          "Einstellungen oder benutze den Modell-Finder.")
    text = await generate(testlauf_prompt(lauf), model=modell)
    if not text.strip():
        raise OllamaError("Das Modell hat nichts zurückgegeben.")
    return text.strip(), modell


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


def generate_sync(prompt: str, *, model: str | None = None,
                  timeout: float | None = None) -> str:
    """Dasselbe wie ``generate``, aber synchron.

    Gebraucht vom KI-Gegner: Die Kampf-Engine ist synchron, und ein Zug
    entsteht mitten in ihrer Schleife. Der ganze Kontrollkampf läuft deshalb
    in einem eigenen Faden (``asyncio.to_thread``) — dort wäre ein ``await``
    weder möglich noch nötig.
    """
    name = model or settings.get("ollama.model_kampf") or settings.get("ollama.model")
    if not name:
        raise OllamaError("Es ist kein Modell ausgewählt. Wähle eines in den Einstellungen "
                          "oder benutze den Modell-Finder.")
    payload = {"model": name, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.2}}
    try:
        with httpx.Client(timeout=timeout or timeout_seconds()) as client:
            response = client.post(base_url() + "/api/generate", json=payload)
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


def _erste_ziffer(text: str) -> int | None:
    """Die erste 1, 2 oder 3 in der Antwort.

    Kleine Modelle halten sich selten an „nur die Nummer" — sie schreiben
    „Antwort: 2" oder „Ich wähle 2)". Die Zahl herauszulesen ist fairer, als
    sie dafür durchfallen zu lassen: Gefragt ist die Entscheidung, nicht die
    Fähigkeit, das Format einzuhalten.
    """
    for zeichen in str(text):
        if zeichen in "123":
            return int(zeichen)
    return None


async def _probe_kampf(name: str, per_model_timeout: float) -> dict:
    """Prüft, ob ein Modell eine Kampflage lesen und richtig entscheiden kann."""
    started = time.perf_counter()
    richtig = 0
    einzeln = []
    for nummer, aufgabe in enumerate(KAMPF_AUFGABEN, start=1):
        try:
            antwort = await generate(KAMPF_PROBE_HINWEIS + aufgabe["frage"],
                                     model=name, timeout=per_model_timeout)
        except OllamaError as exc:
            return {"model": name, "ok": False, "error": str(exc),
                    "treffer": richtig, "von": len(KAMPF_AUFGABEN), "aufgaben": einzeln}
        gewaehlt = _erste_ziffer(antwort)
        stimmt = gewaehlt == aufgabe["richtig"]
        richtig += 1 if stimmt else 0
        einzeln.append({"nummer": nummer, "gewaehlt": gewaehlt,
                        "richtig": aufgabe["richtig"], "stimmt": stimmt,
                        "warum": aufgabe["warum"]})

    seconds = round(time.perf_counter() - started, 2)
    ok = richtig >= KAMPF_MINDESTENS
    return {
        "model": name,
        "ok": ok,
        "seconds": seconds,
        "treffer": richtig,
        "von": len(KAMPF_AUFGABEN),
        "aufgaben": einzeln,
        "answer": f"{richtig} von {len(KAMPF_AUFGABEN)} Lagen richtig eingeschätzt",
        "error": None if ok else
        f"Nur {richtig} von {len(KAMPF_AUFGABEN)} Lagen richtig — dieses Modell "
        f"trifft im Kampf keine verlässlichen Entscheidungen.",
    }


async def find_model(candidates: list[str] | None = None, per_model_timeout: float = 90.0,
                     parallel: int = 3, art: str = "verstaendnis") -> dict:
    """Testet mehrere Modelle gleichzeitig und meldet, welches am besten passt.

    ``art`` entscheidet über die Aufgabe: ``verstaendnis`` für die Auswertung
    von Texten, ``kampf`` für den Testlauf. Beides sind verschiedene
    Fähigkeiten — ein Modell, das Texte gut zusammenfasst, kann trotzdem an
    einer Kampflage scheitern.

    Es laufen absichtlich nicht alle auf einmal los: Ollama lädt jedes Modell in
    den Speicher, zu viele gleichzeitig bringen die Maschine ins Schwitzen.
    """
    names = candidates or [m["name"] for m in await models()]
    if not names:
        raise OllamaError("Auf dem Server ist kein einziges Modell installiert.")

    pruefung = _probe_kampf if art == "kampf" else _probe
    limiter = asyncio.Semaphore(max(1, parallel))

    async def run(name: str) -> dict:
        async with limiter:
            return await pruefung(name, per_model_timeout)

    results = await asyncio.gather(*(run(n) for n in names), return_exceptions=True)
    clean = [r for r in results if isinstance(r, dict)]
    # Bei der Kampfaufgabe zaehlt erst die Trefferzahl, dann das Tempo: Ein
    # schnelles Modell, das falsch entscheidet, nuetzt nichts.
    working = sorted([r for r in clean if r["ok"]],
                     key=lambda r: (-r.get("treffer", 0), r["seconds"]))
    return {
        "art": art,
        "tested": clean,
        "working": working,
        "recommended": working[0]["model"] if working else None,
    }
