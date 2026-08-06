"""Einstellungen, die du in der Oberfläche änderst.

Liegen in der Tabelle web_settings. Für jeden Schlüssel gibt es hier einen
Standardwert und eine kurze Beschreibung — die Oberfläche baut ihre
Einstellungsseite direkt daraus, damit Code und Anzeige nie auseinanderlaufen.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config, database

# key -> (Standardwert, Typ, Gruppe, Beschriftung, Hilfetext)
DEFINITIONS: dict[str, tuple] = {
    "ollama.url": (
        config.OLLAMA_URL_DEFAULT, "text", "KI", "Ollama-Adresse",
        "Wo läuft Ollama? Beispiel: http://192.168.68.10:11434",
    ),
    "ollama.model": (
        "", "text", "KI", "Modell für die Auswertung",
        "Leer lassen und den Modell-Finder benutzen, wenn du unsicher bist.",
    ),
    "ollama.model_kampf": (
        "", "text", "KI", "Modell für den Testlauf",
        "Beurteilt die Ergebnisse eines Testlaufs in Worten. Das braucht etwas "
        "anderes als die Auswertung: Das Modell muss eine Kampflage lesen und "
        "eine sinnvolle Entscheidung ableiten können. Der Modell-Finder daneben "
        "prüft genau das. Leer lassen = es wird das Modell für die Auswertung "
        "benutzt.",
    ),
    "ollama.timeout": (
        "120", "int", "KI", "Zeitlimit pro Anfrage (Sekunden)",
        "Auf einer CPU dauern Antworten länger — dann höher stellen.",
    ),
    "ai.enabled": (
        "0", "bool", "KI", "KI-Auswertung erlauben",
        "Aus = die Analyse nutzt nur die schnellen Statistik-Regeln. "
        "An = zusätzlich eine Einschätzung durch das Sprachmodell.",
    ),
    "exec.mode": (
        "job", "choice:job,direct", "Ausführung", "Wie Discord-Aktionen laufen",
        "Auftragstabelle = der Bot führt aus (empfohlen, nutzt seine Rechteprüfung). "
        "Direkt = die Website spricht selbst mit Discord (schneller, weniger Absicherung).",
    ),
    "scan.chunk_pause_ms": (
        "250", "int", "Analyse", "Pause zwischen den Häppchen (Millisekunden)",
        "Höher = schonender für den Bot und für Discords Sperren, aber langsamer.",
    ),
    "scan.max_messages_per_channel": (
        "0", "int", "Analyse", "Höchstzahl Nachrichten je Kanal",
        "0 = unbegrenzt. Nur setzen, wenn ein Scan zu lange dauert.",
    ),
    "scan.badwords": (
        "", "textarea", "Analyse", "Eigene Wortliste",
        "Ein Wort pro Zeile. Der Bot hat von Haus aus keinen Wortfilter — "
        "diese Liste gilt nur für die Auswertung, es wird nichts gelöscht oder bestraft.",
    ),
    "net.lan": (
        ",".join(config.TRUSTED_LAN), "text", "Zugang", "Heimnetz-Bereiche",
        "Nur von hier aus sind kritische Aktionen erlaubt (Rollen, Kick, Bann). "
        "Mehrere durch Komma trennen.",
    ),
    "net.vpn": (
        ",".join(config.TRUSTED_VPN), "text", "Zugang", "VPN-Bereiche (Tailscale)",
        "Von hier aus ist alles außer den kritischen Aktionen erlaubt.",
    ),
    "dashboard.url": (
        "http://192.168.68.10:7859/", "text", "Darstellung", "Adresse der ausführlichen Statistik",
        "Das alte Kartenbot-Dashboard mit den ausführlichen Auswertungen. "
        "Steht hier eine Adresse, erscheint auf der Statistikseite ein Knopf dorthin. "
        "Leer lassen, wenn es die Seite nicht mehr gibt.",
    ),
    "mitschrift.aktiv": (
        "0", "bool", "Kämpfe", "Züge mitschreiben",
        "Aus = es wird nichts aufgezeichnet. An = der Bot hält bei jedem Zug fest, "
        "wie die Lage war und wie lange jemand überlegt hat. Daraus lernt später "
        "der Gegner. Am Spiel selbst ändert das nichts — es wird nur mitgeschrieben. "
        "Gelernt wird nur aus Kämpfen ab dem Einschalten; je früher es an ist, "
        "desto eher ist genug beisammen.",
    ),
    "ui.theme": (
        "dark", "choice:dark,light,auto", "Darstellung", "Design",
        "Automatisch richtet sich nach der Einstellung deines Systems.",
    ),
    "ui.confirm_seconds": (
        "10", "int", "Darstellung", "Zeitfenster zum Rückgängigmachen (Sekunden)",
        "So lange kannst du eine ausgeführte Aktion noch zurücknehmen.",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_all() -> dict[str, str]:
    values = {key: spec[0] for key, spec in DEFINITIONS.items()}
    with database.read_connection() as con:
        for row in database.fetch_all(con, "SELECT key, value FROM web_settings"):
            if row["key"] in values:
                values[row["key"]] = row["value"]
    return values


def get(key: str) -> str:
    spec = DEFINITIONS.get(key)
    default = spec[0] if spec else ""
    with database.read_connection() as con:
        row = database.fetch_one(con, "SELECT value FROM web_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def get_int(key: str) -> int:
    try:
        return int(str(get(key)).strip() or "0")
    except ValueError:
        return int(DEFINITIONS.get(key, ("0",))[0] or 0)


def get_bool(key: str) -> bool:
    return str(get(key)).strip().lower() in ("1", "true", "ja", "an", "yes", "on")


def get_list(key: str) -> list[str]:
    raw = get(key)
    parts = raw.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def set_many(changes: dict[str, str]) -> dict[str, str]:
    unknown = [k for k in changes if k not in DEFINITIONS]
    if unknown:
        raise ValueError("Unbekannte Einstellung: " + ", ".join(sorted(unknown)))
    now = _now()
    with database.write_connection() as con:
        for key, value in changes.items():
            con.execute(
                "INSERT INTO web_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, str(value), now),
            )
    return get_all()


def describe() -> list[dict]:
    """Beschreibung aller Einstellungen für die Oberfläche."""
    values = get_all()
    out = []
    for key, (default, kind, group, label, help_text) in DEFINITIONS.items():
        entry = {
            "key": key, "value": values[key], "default": default,
            "type": kind, "group": group, "label": label, "help": help_text,
        }
        if kind.startswith("choice:"):
            entry["type"] = "choice"
            entry["choices"] = kind.split(":", 1)[1].split(",")
        out.append(entry)
    return out


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
