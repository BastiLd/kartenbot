"""Auswertung von bot.log.

Der Bot schreibt seine Meldungen in eine Textdatei. Von hinten gelesen ergibt
das ohne Datenbank eine gute Antwort auf „läuft alles rund?" — Fehler und
Warnungen der letzten 24 Stunden, plus die letzten Zeilen zum Nachlesen.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config

# Beispiel: 2026-08-05 20:26:31,123 ERROR bot Beschreibung...
LINE = re.compile(
    r"^(?P<zeit>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[.,]?\d*\s+"
    r"(?P<stufe>DEBUG|INFO|WARNING|ERROR|CRITICAL)\b\s*(?P<text>.*)$"
)
MAX_BYTES = 4 * 1024 * 1024   # nur das Ende der Datei lesen


def _tail(path: Path, limit: int = MAX_BYTES) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit:
            handle.seek(size - limit)
            handle.readline()          # angeschnittene erste Zeile verwerfen
        raw = handle.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def _parse(line: str) -> dict | None:
    match = LINE.match(line)
    if not match:
        return None
    stamp = match.group("zeit").replace("T", " ")
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {"zeit": stamp, "stufe": match.group("stufe"), "text": match.group("text"),
            "_dt": moment}


def read_lines(limit: int = 300, level: str = "", search: str = "") -> list[dict]:
    path = Path(config.LOG_PATH)
    if not path.exists():
        return []
    needle = search.strip().lower()
    out = []
    for line in _tail(path):
        entry = _parse(line)
        if not entry:
            if out and not needle and not level:
                out[-1]["text"] += "\n" + line       # Fortsetzungszeile (Traceback)
            continue
        if level and entry["stufe"] != level:
            continue
        if needle and needle not in line.lower():
            continue
        entry.pop("_dt", None)
        out.append(entry)
    return out[-limit:]


def health_from_log() -> dict:
    path = Path(config.LOG_PATH)
    if not path.exists():
        return {"verfuegbar": False, "pfad": str(path), "fehler_24h": 0, "warnungen_24h": 0,
                "letzte_fehler": []}

    grenze = datetime.now() - timedelta(hours=24)
    fehler = warnungen = 0
    letzte: list[dict] = []
    for line in _tail(path):
        entry = _parse(line)
        if not entry or entry["_dt"] < grenze:
            continue
        if entry["stufe"] in ("ERROR", "CRITICAL"):
            fehler += 1
            letzte.append({"zeit": entry["zeit"], "stufe": entry["stufe"],
                           "text": entry["text"][:400]})
        elif entry["stufe"] == "WARNING":
            warnungen += 1

    return {
        "verfuegbar": True,
        "pfad": str(path),
        "groesse": path.stat().st_size,
        "fehler_24h": fehler,
        "warnungen_24h": warnungen,
        "letzte_fehler": letzte[-5:],
    }
