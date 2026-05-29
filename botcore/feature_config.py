"""Loader für globale Feature-Toggles aus ``namenconfig.py``.

Dieses Modul kapselt das defensive Laden der zentralen Feature-Schalter
für v2.3.0. Die Werte werden einmalig beim Import aus dem Modul
``namenconfig`` (Repo-Root) gelesen. Fehlt die Datei oder enthält sie
ungültige Einträge, wird auf Defaults zurückgegriffen und eine Warnung
geloggt - der Bot soll niemals wegen eines Konfigurationsfehlers hart
abbrechen.

Helper-Funktionen :func:`boss_switch_enabled` und
:func:`name_normalization_enabled` liefern die aktuell geltenden Werte
und werden von Aufrufern statt direktem Modul-Zugriff verwendet, damit
Tests den Modul-Zustand bequem patchen können.
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Defaults werden verwendet, wenn ``namenconfig.py`` fehlt oder einen
# Eintrag mit ungültigem Typ liefert.
_DEFAULTS: Dict[str, bool] = {
    "boss_switch_enabled": True,
    "name_normalization_enabled": True,
}


def _load_namenconfig() -> Dict[str, bool]:
    """Lädt ``namenconfig.py`` defensiv und liefert immer ein vollständiges Dict.

    Verhalten:
        * Fehlt das Modul (``ImportError``), werden Defaults zurückgegeben
          und eine Warnung geloggt.
        * Pro Default-Key wird geprüft, ob im Modul ein gleichnamiges
          Attribut vom Typ ``bool`` existiert. Falls ja, wird es
          übernommen. Andernfalls (fehlt / falscher Typ) wird der
          Default verwendet und eine Warnung geloggt.
        * Es werden keine Exceptions weitergereicht; das zurückgegebene
          Dict enthält garantiert alle Default-Keys.
    """
    result: Dict[str, bool] = dict(_DEFAULTS)
    try:
        import namenconfig as _cfg  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("namenconfig.py nicht gefunden - Defaults aktiv")
        return result

    for key, default_value in _DEFAULTS.items():
        if not hasattr(_cfg, key):
            logger.warning(
                "namenconfig.%s nicht gesetzt - Default %s aktiv",
                key,
                default_value,
            )
            continue
        value = getattr(_cfg, key)
        if isinstance(value, bool):
            result[key] = value
        else:
            logger.warning(
                "namenconfig.%s ungültig (%r) - Default %s aktiv",
                key,
                value,
                default_value,
            )
    return result


# Modul-Konstante: einmalig beim Import berechnet. Tests können diesen
# Wert direkt patchen, um andere Toggle-Konfigurationen zu simulieren.
NAMENCONFIG: Dict[str, bool] = _load_namenconfig()


def boss_switch_enabled() -> bool:
    """Gibt zurück, ob der Boss-Karten-Wechsel-Dialog aktiv ist."""
    return NAMENCONFIG["boss_switch_enabled"]


def name_normalization_enabled() -> bool:
    """Gibt zurück, ob Benutzernamens-Normalisierung aktiv ist."""
    return NAMENCONFIG["name_normalization_enabled"]
