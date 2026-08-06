"""Die Gegner aus den Missionen — Schurken statt Helden.

Der Aufbau im Bot ist immer derselbe: Jede Operation hat genau **3 kleine
Gegner und 1 Boss**, in dieser Reihenfolge. Die Gegner sind ganz normale
Karten (Name, Lebenspunkte, Angriffe) und kämpfen mit derselben Engine —
deshalb geht auch der Testlauf mit ihnen.

**Die Bilder werden über die Nummer zugeordnet, nicht über den Namen.** Die
Namen in den Dateien sind teilweise falsch (aus „Fisks Enforcer" wurde
„Korrupte SWAT-Einheit"), die Nummer stimmt aber immer: 1 bis 3 sind die
kleinen Gegner in der Reihenfolge des Codes, 4 ist der Boss. Wer nach Namen
zuordnet, bekommt Unsinn; wer nach Nummer zuordnet, liegt richtig.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache

from . import config

if str(config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(config.PROJECT_ROOT))

BILDER_ORDNER = config.WEB_DIR / "static" / "missionen"

# Operation -> (Anzeigename, Bildordner, Name der Liste in mission_enemies.py)
OPERATIONEN = {
    "broken_timeline": ("Broken Timeline", "broken_timeline",
                        "OPERATION_BROKEN_TIMELINE_ENCOUNTERS"),
    "goldener_kaefig": ("Goldener Käfig", "goldener_kaefig",
                        "OPERATION_GOLDENER_KAEFIG_ENCOUNTERS"),
    "gruener_terror": ("Grüner Terror", "gruener_terror",
                       "OPERATION_GRUENER_TERROR_ENCOUNTERS"),
    "hexenfeuer": ("Hexenfeuer", "hexenfeuer",
                   "OPERATION_HEXENFEUER_ENCOUNTERS"),
    "technischer_kollaps": ("Technischer Kollaps", "technischer_kollaps",
                            "OPERATION_TECHNISCHER_KOLLAPS_ENCOUNTERS"),
}


@lru_cache(maxsize=1)
def _bilder() -> dict[str, dict[int, str]]:
    """Bildpfade je Ordner, nach der Nummer im Dateinamen sortiert."""
    gefunden: dict[str, dict[int, str]] = {}
    if not BILDER_ORDNER.exists():
        return gefunden
    for ordner in BILDER_ORDNER.iterdir():
        if not ordner.is_dir():
            continue
        nach_nummer: dict[int, str] = {}
        for datei in sorted(ordner.iterdir()):
            treffer = re.match(r"^(\d+)", datei.name)
            if treffer and datei.is_file():
                # Relativ, damit es hinter WebHafen genauso funktioniert wie
                # direkt am Backend - dort liegt static im Wurzelverzeichnis.
                nach_nummer[int(treffer.group(1))] = f"missionen/{ordner.name}/{datei.name}"
        gefunden[ordner.name] = nach_nummer
    return gefunden


@lru_cache(maxsize=1)
def _roh() -> dict[str, list[dict]]:
    try:
        import mission_enemies                                  # type: ignore
    except Exception:                                           # noqa: BLE001
        return {}
    heraus = {}
    for schluessel, (_, _, listenname) in OPERATIONEN.items():
        liste = getattr(mission_enemies, listenname, None)
        if isinstance(liste, list) and liste:
            heraus[schluessel] = liste
    return heraus


def verfuegbar() -> bool:
    return bool(_roh())


def katalog() -> list[dict]:
    """Alle Missionsgegner, aufbereitet für die Oberfläche.

    Ein Eintrag je Gegner, mit seiner Operation, seiner Rolle (Boss oder
    kleiner Gegner) und dem zugeordneten Bild.
    """
    bilder = _bilder()
    heraus: list[dict] = []
    for schluessel, gegnerliste in _roh().items():
        anzeigename, bildordner, _ = OPERATIONEN[schluessel]
        ordnerbilder = bilder.get(bildordner, {})
        letzter = len(gegnerliste) - 1
        for position, gegner in enumerate(gegnerliste):
            ist_boss = position == letzter
            heraus.append({
                "name": gegner.get("name"),
                "operation": schluessel,
                "operation_name": anzeigename,
                "rolle": "boss" if ist_boss else "klein",
                "position": position + 1,
                "hp": gegner.get("hp"),
                "beschreibung": gegner.get("beschreibung"),
                # Das Bild aus dem Ordner gewinnt: Es ist das, was im Spiel
                # auf der Karte steht. Die Adresse im Code zeigt teils noch
                # auf alte Imgur-Bilder.
                "bild": ordnerbilder.get(position + 1) or gegner.get("bild"),
                "bild_aus_datei": bool(ordnerbilder.get(position + 1)),
                "angriffe": [
                    {"name": a.get("name"),
                     "schaden": a.get("damage"),
                     "info": a.get("info"),
                     "abklingzeit": a.get("cooldown_turns"),
                     "heilung": a.get("heal"),
                     "standard": bool(a.get("is_standard_attack")),
                     "wirkungen": [e.get("type") for e in (a.get("effects") or [])
                                   if isinstance(e, dict) and e.get("type")]}
                    for a in (gegner.get("attacks") or []) if isinstance(a, dict)
                ],
                "passiv": [str(p.get("type")) for p in (gegner.get("passives") or [])
                           if isinstance(p, dict) and p.get("type")],
            })
    return heraus


def operationen() -> list[dict]:
    """Die Operationen als Filterliste, mit der Zahl ihrer Gegner."""
    alle = katalog()
    heraus = []
    for schluessel, (anzeigename, _, _) in OPERATIONEN.items():
        gegner = [g for g in alle if g["operation"] == schluessel]
        if not gegner:
            continue
        boss = next((g["name"] for g in gegner if g["rolle"] == "boss"), None)
        heraus.append({"schluessel": schluessel, "name": anzeigename,
                       "boss": boss, "anzahl": len(gegner)})
    return heraus


def namen() -> set[str]:
    return {str(g["name"]).strip() for g in katalog() if g.get("name")}
