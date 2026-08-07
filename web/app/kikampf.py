"""Kontrollkampf gegen die KI — die Website-Hälfte (Stufe 5, Schritt 10).

Ein einzelner Kampf, bei dem vor jedem Zug das Sprachmodell gefragt wird.
Die Entscheidungslogik steht in ``services/ki_gegner.py``; hier steht nur die
Klammer: welche Karten antreten, welches Modell gefragt wird, und dass das
Ganze nicht den Webserver blockiert.

**Warum auf der Website und nicht im Bot** — anders als beim Testlauf: Hier
läuft genau *ein* Kampf, nicht zehntausend. Der Grund, im Bot zu rechnen (die
Rechnung portionieren, damit das Spiel weiterläuft), entfällt damit. Dafür
liegt hier der fertige Zugang zum Sprachmodell.

**Warum in einem eigenen Faden:** Die Kampf-Engine ist synchron, und die
Anfragen ans Modell dauern Sekunden bis Minuten. Liefe das im Ereignis-Faden,
stünde die ganze Website still.

Gerechnet wird mit den Karten des Spiels **einschließlich** der Änderungen,
die über diese Seite gemacht wurden — sonst prüfte der Kontrollkampf einen
Zustand, den es im Discord gar nicht gibt.
"""
from __future__ import annotations

import asyncio
import sys

from . import config, karteneditor, ollama, settings

if str(config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(config.PROJECT_ROOT))

# Was der Kampf höchstens kosten darf. Jeder Zug ist eine Anfrage; bei einem
# langsamen Modell sind 120 Züge schon eine halbe Stunde.
MAX_RUNDEN_HINWEIS = 120


class KampfFehler(Exception):
    """Der Kontrollkampf kann so nicht laufen — Text ist für den Menschen."""


def _karten() -> list[dict]:
    """Die Karten des Spiels mit den Änderungen von dieser Seite."""
    try:
        from simulation.loader import load_base_runtime_cards
    except Exception as fehler:                                # noqa: BLE001
        raise KampfFehler(f"Die Kampf-Engine ist nicht erreichbar: {fehler}") from fehler

    karten = load_base_runtime_cards()
    try:
        from services.card_store import AENDERBAR
    except Exception:                                          # noqa: BLE001
        AENDERBAR = ()                                         # noqa: N806

    abweichungen = karteneditor.alle()
    for karte in karten:
        eintrag = abweichungen.get(karte.get("name"))
        if not eintrag:
            continue
        for feld, wert in (eintrag.get("aenderungen") or {}).items():
            if not AENDERBAR or feld in AENDERBAR:
                karte[feld] = wert
    return karten


def _finde(karten: list[dict], name: str) -> dict:
    gesucht = str(name or "").strip()
    try:
        from simulation.loader import canonical_hero_name
    except Exception:                                          # noqa: BLE001
        def canonical_hero_name(k):                            # type: ignore
            return str(k.get("name") or "")

    for karte in karten:
        if canonical_hero_name(karte) == gesucht:
            return karte
    try:
        from services.card_testrun import missionsgegner
        for gegner in missionsgegner():
            if str(gegner.get("name") or "").strip() == gesucht:
                return gegner
    except Exception:                                          # noqa: BLE001
        pass
    raise KampfFehler(f"„{gesucht}“ ist dem Spiel nicht bekannt.")


def pruefe(karte: str, gegner: str, gegenspieler: str) -> dict:
    """Eingaben abnehmen, bevor der Kampf losgeht."""
    if not settings.get_bool("ai.enabled"):
        raise KampfFehler("Die KI-Auswertung ist ausgeschaltet. Der Schalter steht "
                          "in den Einstellungen unter „KI“.")
    modell = settings.get("ollama.model_kampf") or settings.get("ollama.model")
    if not modell:
        raise KampfFehler("Es ist kein Modell für den Kampf ausgewählt. Der "
                          "Modell-Finder in den Einstellungen sucht eines.")
    if gegenspieler not in ("optimal", "average"):
        raise KampfFehler("Als Gegenspieler geht „optimal“ oder „average“.")

    karten = _karten()
    _finde(karten, karte)
    if gegner:
        _finde(karten, gegner)
    return {"karte": str(karte).strip(), "gegner": str(gegner or "").strip(),
            "gegenspieler": gegenspieler, "modell": modell}


async def laufen(karte: str, gegner: str = "", *, gegenspieler: str = "optimal",
                 seed: int | None = None) -> dict:
    """Einen Kontrollkampf rechnen — im eigenen Faden, damit nichts stillsteht."""
    sauber = pruefe(karte, gegner, gegenspieler)
    karten = _karten()
    karte_a = _finde(karten, sauber["karte"])
    if sauber["gegner"]:
        karte_b = _finde(karten, sauber["gegner"])
    else:
        karte_b = next((k for k in karten if k is not karte_a), None)
        if karte_b is None:
            raise KampfFehler("Es gibt keine zweite Karte, gegen die angetreten werden könnte.")

    from services import ki_gegner

    def frage(prompt: str) -> str:
        return ollama.generate_sync(prompt, model=sauber["modell"])

    ergebnis = await asyncio.to_thread(
        ki_gegner.kontrollkampf, karte_a, karte_b,
        frage=frage, seed=seed, gegenspieler=gegenspieler)
    ergebnis["modell"] = sauber["modell"]
    return ergebnis
