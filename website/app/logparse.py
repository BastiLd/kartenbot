"""Parser für bot.log.

Format: ``YYYY-MM-DD HH:MM:SS,mmm LEVEL Nachricht`` — Folgezeilen ohne
Zeitstempel (Tracebacks) gehören zum vorherigen Eintrag.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

TZ = ZoneInfo(config.TIMEZONE)

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} (?P<level>[A-Z]+) (?P<msg>.*)$"
)

# Marker, an denen ein echter Bot-(Neu)start erkennbar ist. WICHTIG: kein
# generisches "shard id" — "Shard ID None has successfully RESUMED" ist nur
# ein Gateway-Reconnect, KEIN Neustart (führte zu falscher Mini-Uptime).
_STARTUP_MARKERS = (
    "logging in using static token",
    "davey is not installed",
    "has sent the identify payload",
)

MAX_BYTES = 2 * 1024 * 1024  # nur die letzten 2 MB lesen — reicht für den Viewer


def _read_tail(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > MAX_BYTES:
            fh.seek(size - MAX_BYTES)
            fh.readline()  # angeschnittene Zeile verwerfen
        return fh.read().decode("utf-8", errors="replace")


def parse_log(limit: int = 300, level: str | None = None, query: str | None = None) -> dict:
    path = Path(config.LOG_PATH)
    result: dict = {
        "available": path.exists(),
        "entries": [],
        "counts": {},
        "last_timestamp": None,
        "last_startup": None,
        "file_size": 0,
    }
    if not path.exists():
        return result
    result["file_size"] = path.stat().st_size

    entries: list[dict] = []
    current: dict | None = None
    for line in _read_tail(path).splitlines():
        match = _LINE_RE.match(line)
        if match:
            if current:
                entries.append(current)
            ts_text = match.group("ts")
            try:
                # Log-Zeitstempel sind lokale Bot-Zeit (DASHBOARD_TZ) — NICHT
                # Container-Lokalzeit (UTC), sonst ist die Uptime um Stunden
                # verschoben (negative Uptime-Anzeige).
                epoch = int(datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp())
            except ValueError:
                epoch = 0
            current = {
                "timestamp": ts_text,
                "epoch": epoch,
                "level": match.group("level"),
                "message": match.group("msg"),
                "detail": "",
            }
        elif current is not None:
            current["detail"] += line + "\n"
    if current:
        entries.append(current)

    # Auto-Kalibrierung: Die Datei-mtime entspricht (fast) exakt der Echtzeit
    # des letzten Log-Eintrags. Weicht der geparste Zeitstempel davon ab,
    # loggt der Bot in einer anderen Zeitzone als DASHBOARD_TZ — dann alle
    # Epochen um den (auf 15 min gerundeten) Offset korrigieren. Verhindert
    # negative/verschobene Uptime unabhängig von Host-Zeitzonen.
    if entries and entries[-1]["epoch"]:
        delta = int(path.stat().st_mtime) - entries[-1]["epoch"]
        correction = round(delta / 900) * 900
        if correction:
            for entry in entries:
                if entry["epoch"]:
                    entry["epoch"] += correction

    counts: dict[str, int] = {}
    last_startup = None
    for entry in entries:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
        lowered = entry["message"].lower()
        if any(marker in lowered for marker in _STARTUP_MARKERS):
            last_startup = entry
    result["counts"] = counts
    if entries:
        result["last_timestamp"] = entries[-1]["epoch"]
    if last_startup:
        result["last_startup"] = {"timestamp": last_startup["timestamp"], "epoch": last_startup["epoch"]}

    filtered = entries
    if level:
        wanted = {part.strip().upper() for part in level.split(",") if part.strip()}
        filtered = [e for e in filtered if e["level"] in wanted]
    if query:
        needle = query.lower()
        filtered = [e for e in filtered if needle in e["message"].lower() or needle in e["detail"].lower()]

    filtered = list(reversed(filtered))[: max(1, min(limit, 1000))]
    result["entries"] = filtered
    return result


def health_from_log() -> dict:
    """Heuristik: Der Bot gilt als online, wenn in den letzten 15 Minuten
    etwas geloggt wurde ODER die Log-Datei kürzlich geändert wurde."""
    path = Path(config.LOG_PATH)
    info = {
        "log_available": path.exists(),
        "online_guess": False,
        "last_log_epoch": None,
        "last_startup_epoch": None,
        "errors_24h": 0,
        "warnings_24h": 0,
        "last_errors": [],
    }
    if not path.exists():
        return info
    parsed = parse_log(limit=1000)
    now = int(time.time())
    mtime = int(path.stat().st_mtime)
    last_epoch = parsed["last_timestamp"] or 0
    info["last_log_epoch"] = last_epoch or None
    info["online_guess"] = (now - max(last_epoch, mtime)) < 15 * 60
    if parsed["last_startup"]:
        info["last_startup_epoch"] = parsed["last_startup"]["epoch"]
    day_ago = now - 86400
    errors = [e for e in parsed["entries"] if e["level"] == "ERROR" and e["epoch"] >= day_ago]
    warnings = [e for e in parsed["entries"] if e["level"] == "WARNING" and e["epoch"] >= day_ago]
    info["errors_24h"] = len(errors)
    info["warnings_24h"] = len(warnings)
    info["last_errors"] = errors[:5]
    return info
