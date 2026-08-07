"""Lernen aus echten Kämpfen (Stufe 5, Schritt 9).

Seit die Zug-Mitschrift läuft, steht in ``battle_moves`` nicht nur, welcher
Angriff benutzt wurde, sondern auch, **welche zur Wahl standen**. Erst damit
lässt sich ablesen, was Spieler bevorzugt gespielt haben — und genau das sind
die vier Gewichte in ``simulation/strategy.py:evaluate_move``: wie viel ein
Angriff dadurch wert ist, dass er betäubt (CONTROL), schützt (DEFENSE),
vorbereitet (SETUP) oder über Runden Schaden macht (DOT).

**Wie gemessen wird — und warum nicht einfacher.** Naheliegend wäre: zählen,
wie oft Kontrolle gespielt wurde. Das misst aber vor allem, wie viele Karten
überhaupt Kontrolle haben. Gemessen wird stattdessen der Abstand zum Zufall:
Standen vier Angriffe zur Wahl und zwei davon betäuben, dann fiele bei blindem
Raten in der Hälfte der Fälle die Wahl auf einen Betäuber. Wird tatsächlich
öfter betäubt, ist das eine Vorliebe; wird seltener betäubt, eine Abneigung.
Der Quotient aus beidem ist der Faktor, mit dem das Gewicht verschoben wird.

**Gelernt wird nur von Gewinnern** (``ausgang = 'gewonnen'``) und nur von
Menschen (``ist_bot = 0``). Von den Zügen des Bots zu lernen hiesse, ihm
seine eigenen Vorlieben noch einmal zu bestätigen — er würde nur immer
sicherer in dem, was er ohnehin tut.

**Was bewusst übersprungen wird:**

- Züge, bei denen es nichts zu wählen gab. Wer nur einen Angriff zur Verfügung
  hat, trifft keine Entscheidung. Solche Zeilen würden die Statistik mit
  Nicht-Entscheidungen auffüllen und jeden Faktor Richtung 1 ziehen.
- Kategorien, die zu selten überhaupt zur Wahl standen. Aus zehn Gelegenheiten
  lässt sich nichts ableiten; dort bleibt das eingebaute Gewicht stehen.

**Der Faktor ist gedeckelt.** Ein einzelner Abend, an dem zufällig viel
betäubt wurde, darf den Gegner nicht unkenntlich machen. Mehr als doppelt so
wichtig oder halb so wichtig wie eingebaut wird nichts.
"""
from __future__ import annotations

import json
import logging

from simulation.strategy import (CONTROL_EFFECTS, DEFENSE_EFFECTS, DOT_EFFECTS,
                                 SETUP_EFFECTS, STANDARD_GEWICHTE)

# Welcher Effekt zu welcher Kategorie zählt — dieselben Mengen, mit denen die
# Engine rechnet. Zwei getrennte Listen würden über kurz oder lang
# auseinanderlaufen.
KATEGORIEN = {
    "control": CONTROL_EFFECTS,
    "defense": DEFENSE_EFFECTS,
    "setup": SETUP_EFFECTS,
    "dot": DOT_EFFECTS,
}

# So oft muss eine Kategorie mindestens zur Wahl gestanden haben, bevor aus
# ihr gelernt wird. Darunter bleibt das eingebaute Gewicht stehen.
MINDEST_GELEGENHEITEN = 30

# Wie weit sich ein Gewicht überhaupt verschieben darf.
MIN_FAKTOR = 0.5
MAX_FAKTOR = 2.0

# So viele Züge werden höchstens gelesen. Die Auswertung läuft im Webserver
# und soll keine Anfrage minutenlang blockieren.
MAX_ZUEGE = 50_000


def kategorien_von(wirkungen) -> set[str]:
    """Welche der vier Kategorien in einem Angriff stecken."""
    typen = {str(w or "").strip().lower() for w in (wirkungen or []) if str(w or "").strip()}
    return {name for name, menge in KATEGORIEN.items() if typen & menge}


def _angriffe_aus(zug: dict) -> list[dict]:
    angriffe = zug.get("angriffe")
    return [a for a in angriffe if isinstance(a, dict)] if isinstance(angriffe, list) else []


def auswerten(zuege: list[dict], grundgewichte: dict | None = None) -> dict:
    """Aus mitgeschriebenen Zügen neue Gewichte errechnen.

    ``zuege`` sind Einträge mit ``angriff_index`` und ``angriffe`` (Liste mit
    ``index`` und ``wirkungen``) — genau die Form, in der die Mitschrift die
    Lage ablegt.

    Zurück kommt neben den Gewichten die **Grundlage**: je Kategorie, wie oft
    sie zur Wahl stand, wie oft sie genommen wurde, wie oft der Zufall sie
    genommen hätte, und ob daraus gelernt wurde. Ohne diese Zahlen wäre das
    Ergebnis nicht nachprüfbar — und ein Gegner, dessen Verhalten sich nicht
    erklären lässt, ist nicht zu gebrauchen.
    """
    grund = dict(STANDARD_GEWICHTE)
    for schluessel, wert in (grundgewichte or {}).items():
        name = str(schluessel).strip().lower()
        if name in grund:
            try:
                grund[name] = float(wert)
            except (TypeError, ValueError):
                pass

    gelegenheiten = {name: 0 for name in KATEGORIEN}
    gewaehlt = {name: 0 for name in KATEGORIEN}
    erwartet = {name: 0.0 for name in KATEGORIEN}
    verwertet = 0

    for zug in zuege or []:
        angriffe = _angriffe_aus(zug)
        if len(angriffe) < 2:
            # Keine Wahl, keine Entscheidung, nichts zu lernen.
            continue
        try:
            index = int(zug.get("angriff_index", -1))
        except (TypeError, ValueError):
            continue
        gewaehlter = next((a for a in angriffe if a.get("index") == index), None)
        if gewaehlter is None:
            # Erzwungene Landung und Ähnliches steht mit Index -1 drin.
            continue

        verwertet += 1
        genommen = kategorien_von(gewaehlter.get("wirkungen"))
        for name in KATEGORIEN:
            mit_kategorie = sum(1 for a in angriffe
                                if name in kategorien_von(a.get("wirkungen")))
            if not mit_kategorie:
                continue
            gelegenheiten[name] += 1
            erwartet[name] += mit_kategorie / len(angriffe)
            if name in genommen:
                gewaehlt[name] += 1

    gewichte: dict[str, float] = {}
    grundlage: dict[str, dict] = {}
    for name in KATEGORIEN:
        genug = gelegenheiten[name] >= MINDEST_GELEGENHEITEN and erwartet[name] > 0
        faktor = 1.0
        if genug:
            faktor = max(MIN_FAKTOR, min(MAX_FAKTOR, gewaehlt[name] / erwartet[name]))
        gewichte[name] = round(grund[name] * faktor, 1)
        grundlage[name] = {
            "gelegenheiten": gelegenheiten[name],
            "gewaehlt": gewaehlt[name],
            "erwartet": round(erwartet[name], 1),
            "faktor": round(faktor, 2),
            "gelernt": bool(genug),
            "grundgewicht": grund[name],
        }

    return {
        "gewichte": gewichte,
        "grundlage": grundlage,
        "zuege_gesamt": len(zuege or []),
        "zuege_verwertet": verwertet,
        "mindestens": MINDEST_GELEGENHEITEN,
    }


def beschreibe(ergebnis: dict) -> str:
    """Was gelernt wurde, in einem Satz je Kategorie."""
    namen = {
        "control": "Betäuben und Sperren",
        "defense": "Schützen und Ausweichen",
        "setup": "Vorbereiten",
        "dot": "Schaden über mehrere Runden",
    }
    verwertet = int(ergebnis.get("zuege_verwertet") or 0)
    if not verwertet:
        return ("Es gibt noch keinen einzigen verwertbaren Zug. Gelernt wird nur "
                "aus gewonnenen Kämpfen, in denen es wirklich etwas zu wählen gab.")

    zeilen = [f"Ausgewertet wurden {verwertet} Entscheidungen aus gewonnenen Kämpfen."]
    for name, hinweis in namen.items():
        werte = (ergebnis.get("grundlage") or {}).get(name) or {}
        if not werte.get("gelernt"):
            zeilen.append(
                f"{hinweis}: stand nur {int(werte.get('gelegenheiten') or 0)}-mal zur "
                f"Wahl — zu selten, es bleibt beim eingebauten Wert.")
            continue
        faktor = float(werte.get("faktor") or 1.0)
        gewicht = (ergebnis.get("gewichte") or {}).get(name)
        if faktor >= 1.15:
            urteil = "wurde deutlich öfter genommen, als der Zufall es täte"
        elif faktor >= 1.05:
            urteil = "wurde etwas öfter genommen als erwartet"
        elif faktor <= 0.85:
            urteil = "wurde deutlich seltener genommen, als der Zufall es täte"
        elif faktor <= 0.95:
            urteil = "wurde etwas seltener genommen als erwartet"
        else:
            urteil = "wurde ungefähr so oft genommen, wie der Zufall es täte"
        zeilen.append(f"{hinweis}: {urteil} — Gewicht {werte.get('grundgewicht')} → {gewicht}.")
    return "\n".join(zeilen)


def zuege_aus_zeilen(zeilen) -> list[dict]:
    """Aus ``battle_moves``-Zeilen die Form machen, die ``auswerten`` erwartet.

    Die Lage steckt als JSON in einer Spalte. Eine kaputte Zeile wird
    übersprungen, nicht beklagt — bei zehntausend Zügen hilft ein
    Fehlerbericht je Zeile niemandem. Wie viele es waren, steht am Ende einmal
    im Protokoll.
    """
    heraus: list[dict] = []
    uebergangen = 0
    for zeile in zeilen or []:
        try:
            lage = json.loads(zeile["lage_json"] or "{}")
            angriffe = lage.get("angriffe") if isinstance(lage, dict) else None
            if not isinstance(angriffe, list) or not angriffe:
                # Ohne die Liste der moeglichen Angriffe steht in der Zeile
                # nicht, wogegen sich der Spieler entschieden hat. Damit ist
                # sie zum Lernen wertlos - und zaehlt auch nicht als gelesen.
                uebergangen += 1
                continue
            heraus.append({"angriff_index": zeile["angriff_index"], "angriffe": angriffe})
        except (TypeError, ValueError, KeyError, IndexError):
            uebergangen += 1
    if uebergangen:
        logging.info("Lernen: %s Zeilen waren nicht lesbar und wurden uebergangen",
                     uebergangen)
    return heraus
