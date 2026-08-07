"""KI als Gegner (Stufe 5, Schritt 10).

Statt nach festen Regeln zu bewerten, wird vor jedem Zug das Sprachmodell
gefragt: Hier ist die Lage, hier sind die möglichen Angriffe — welcher?

**Das ist absichtlich nichts für den Alltag.** Jeder Zug ist eine Anfrage.
Ein Kampf, den die Engine in Millisekunden rechnet, dauert damit Minuten. Ein
Testlauf über alle Karten wären zehntausende Anfragen und liefe Tage. Gedacht
ist das für **einzelne Kontrollkämpfe**: einmal zusehen, ob das Modell eine
Lage überhaupt sinnvoll liest — und woran es scheitert, wenn nicht.

**Drei Regeln, die hier über allem stehen:**

1. *Der Kampf endet immer.* Antwortet das Modell nicht, zu langsam oder
   unsinnig, entscheidet die eingebaute Bewertung. Ein Kontrollkampf, der
   hängen bleibt, wäre wertlos — und jedes Ausweichen steht hinterher im
   Protokoll, damit niemand ein Ergebnis für die Leistung des Modells hält,
   das in Wahrheit die Regeln erzeugt haben.
2. *Gefragt wird nur, wenn es etwas zu entscheiden gibt.* Bei einem einzigen
   möglichen Angriff wird keine Anfrage verschwendet.
3. *Alles wird protokolliert.* Zu jedem Zug: die Lage, die Antwort im
   Wortlaut, die Wahl und wie lange es gedauert hat. Ohne das ließe sich
   nicht sagen, warum ein Kampf so ausging.

Die Anfrage selbst wird **hereingereicht** (``frage``) und nicht hier
gebaut. So bleibt dieses Modul ohne Netz und ohne Ollama prüfbar — und die
Website kann ihren vorhandenen Zugang benutzen, statt einen zweiten zu haben.
"""
from __future__ import annotations

import logging
import random
import time

from simulation.strategy import OptimalStrategy

# Antworten kleiner Modelle sind selten sauber. Mehr als das lesen wir nicht:
# Was danach kommt, ist Begründung, nicht Entscheidung.
MAX_ANTWORT = 400

# So lange darf ein Kontrollkampf höchstens dauern, in Zügen. Ein Modell, das
# jeden Zug ausweicht, könnte sonst eine Endlosschleife erzeugen.
MAX_ZUEGE = 120


def _wirkungen(angriff: dict) -> list[str]:
    return [str(e.get("type") or "") for e in (angriff.get("effects") or [])
            if isinstance(e, dict) and e.get("type")]


def lage_fuer_ki(runner, player_id: int, erlaubt: list[int]) -> dict:
    """Die Lage in der Form, in der sie dem Modell vorgelegt wird.

    Bewusst nur das, was für die Entscheidung zählt — Lebenspunkte, laufende
    Effekte und die Angriffe zur Wahl. Wer alles hineinschreibt, bekommt von
    kleinen Modellen schlechtere Antworten, nicht bessere.
    """
    gegner_id = runner.other_player(player_id)
    angriffe = runner.attacks_for(player_id)

    moeglich = []
    for nummer, index in enumerate(erlaubt, start=1):
        angriff = angriffe[index] if index < len(angriffe) else {}
        von, bis = runner.estimate_attack_range(player_id, index)
        moeglich.append({
            "nummer": nummer,
            "index": index,
            "name": str(angriff.get("name") or f"Angriff {index + 1}"),
            "schaden_von": von,
            "schaden_bis": bis,
            "wirkungen": _wirkungen(angriff),
        })

    return {
        "eigene_karte": str(runner.card_for(player_id).get("name") or ""),
        "gegner_karte": str(runner.card_for(gegner_id).get("name") or ""),
        "eigene_hp": runner._hp_for(player_id),
        "eigene_max_hp": runner._max_hp_for(player_id),
        "gegner_hp": runner._hp_for(gegner_id),
        "gegner_max_hp": runner._max_hp_for(gegner_id),
        "gegner_unsichtbar": bool(runner.has_stealth(gegner_id)),
        "angriffe": moeglich,
    }


def frage_bauen(lage: dict) -> str:
    """Die Kampflage als Text.

    Die Angriffe sind von 1 an durchnummeriert und nicht mit ihrem internen
    Index benannt: Eine Liste, die bei 0 anfängt, verwirrt kleine Modelle
    zuverlässig. Zurückgerechnet wird beim Lesen der Antwort.
    """
    zeilen = [
        "Du spielst ein Kartenspiel und bist am Zug.",
        "",
        f"Du: {lage['eigene_karte']} — {lage['eigene_hp']} von "
        f"{lage['eigene_max_hp']} Lebenspunkten.",
        f"Gegner: {lage['gegner_karte']} — {lage['gegner_hp']} von "
        f"{lage['gegner_max_hp']} Lebenspunkten.",
    ]
    if lage["gegner_unsichtbar"]:
        zeilen.append("Der Gegner ist unsichtbar — normale Angriffe gehen daneben.")
    zeilen += ["", "Diese Angriffe stehen zur Wahl:"]
    for a in lage["angriffe"]:
        teile = [f"{a['nummer']}. {a['name']}"]
        if a["schaden_bis"] > 0:
            teile.append(f"Schaden {a['schaden_von']} bis {a['schaden_bis']}")
        else:
            teile.append("kein Schaden")
        if a["wirkungen"]:
            teile.append("Wirkung: " + ", ".join(a["wirkungen"]))
        zeilen.append("   " + " — ".join(teile))
    hoechste = len(lage["angriffe"])
    zeilen += [
        "",
        f"Welchen Angriff wählst du? Antworte nur mit der Nummer (1 bis {hoechste}).",
    ]
    return "\n".join(zeilen)


def antwort_lesen(text: str, anzahl: int) -> int | None:
    """Die gewählte Nummer aus der Antwort holen — 1-basiert.

    Kleine Modelle halten sich selten an „nur die Nummer"; sie schreiben
    „Ich nehme 2)" oder „Antwort: 3". Die Zahl herauszulesen ist fairer, als
    sie am Format scheitern zu lassen — gefragt ist die Entscheidung.

    Gelesen wird die **erste** Zahl im gültigen Bereich. Eine spätere wäre
    meist Teil der Begründung („… weil 2 mehr Schaden macht als 1").
    """
    ziffern = ""
    for zeichen in str(text or "")[:MAX_ANTWORT]:
        if zeichen.isdigit():
            ziffern += zeichen
            continue
        if ziffern:
            zahl = int(ziffern)
            if 1 <= zahl <= anzahl:
                return zahl
            ziffern = ""
    if ziffern:
        zahl = int(ziffern)
        if 1 <= zahl <= anzahl:
            return zahl
    return None


class KIGegner:
    """Eine Strategie, die vor jedem Zug das Sprachmodell fragt.

    ``frage`` ist ein Aufruf ``frage(prompt) -> str``. Er wird hereingereicht
    und nicht hier gebaut: So läuft dieses Modul im Test ohne Netz, und die
    Website benutzt ihren vorhandenen Ollama-Zugang statt eines zweiten.

    Passt die Antwort nicht, entscheidet ``rueckfall`` — voreingestellt die
    eingebaute Bewertung. Der Kampf läuft also in jedem Fall zu Ende.
    """

    def __init__(self, frage, *, rueckfall=None, protokoll: list | None = None,
                 gewichte: dict | None = None) -> None:
        self.frage = frage
        self.rueckfall = rueckfall or OptimalStrategy(gewichte=gewichte)
        self.protokoll = protokoll if protokoll is not None else []
        self.gefragt = 0
        self.ausgewichen = 0

    def select_attack_index(self, runner, player_id: int) -> int:
        erlaubt = runner.legal_attack_indices(player_id)
        if not erlaubt:
            return 0
        if len(erlaubt) == 1:
            # Nichts zu entscheiden - dafuer keine Anfrage verschwenden.
            return erlaubt[0]

        lage = lage_fuer_ki(runner, player_id, erlaubt)
        prompt = frage_bauen(lage)
        begonnen = time.monotonic()
        antwort = ""
        grund = ""
        try:
            self.gefragt += 1
            antwort = str(self.frage(prompt) or "")
            nummer = antwort_lesen(antwort, len(erlaubt))
        except Exception as fehler:                            # noqa: BLE001
            logging.exception("KI-Gegner: Anfrage fehlgeschlagen")
            nummer, grund = None, f"Anfrage fehlgeschlagen: {fehler}"

        if nummer is None:
            self.ausgewichen += 1
            gewaehlt = self.rueckfall.select_attack_index(runner, player_id)
            grund = grund or "keine brauchbare Nummer in der Antwort"
        else:
            gewaehlt = erlaubt[nummer - 1]

        self.protokoll.append({
            "runde": len(self.protokoll) + 1,
            "lage": lage,
            "antwort": antwort[:MAX_ANTWORT],
            "gewaehlt_index": gewaehlt,
            "gewaehlt_name": next(
                (a["name"] for a in lage["angriffe"] if a["index"] == gewaehlt), ""),
            "ausgewichen": nummer is None,
            "grund": grund,
            "sekunden": round(time.monotonic() - begonnen, 2),
        })
        return gewaehlt


def kontrollkampf(karte_a: dict, karte_b: dict, *, frage, seed: int | None = None,
                  gegenspieler: str = "optimal", fehlerquote: float = 0.35,
                  gewichte: dict | None = None) -> dict:
    """Ein einzelner Kampf: die KI gegen die eingebaute Bewertung.

    Gibt neben dem Ergebnis das vollständige Protokoll zurück — jeden Zug mit
    der Lage, der Antwort im Wortlaut und der Zeit. Genau das ist der Zweck:
    nicht wer gewinnt, sondern **wie** entschieden wurde.
    """
    from simulation.engine import PLAYER_ONE_ID, simulate_duel

    if seed is None:
        seed = random.randrange(0, 2**31)
    protokoll: list[dict] = []
    ki = KIGegner(frage, protokoll=protokoll, gewichte=gewichte)

    begonnen = time.monotonic()
    ergebnis = simulate_duel(
        karte_a, karte_b,
        starter_id=PLAYER_ONE_ID,
        duel_seed=int(seed),
        strategy_a_name=gegenspieler,
        strategy_b_name=gegenspieler,
        average_mistake_rate=fehlerquote,
        gewichte=gewichte,
        strategie_a=ki,
        max_runden=MAX_ZUEGE,
    )

    return {
        "karte": str(karte_a.get("name") or ""),
        "gegner": str(karte_b.get("name") or ""),
        "gewonnen": ergebnis.winner == karte_a.get("name"),
        "unentschieden": ergebnis.draw,
        "runden": ergebnis.rounds,
        "gefragt": ki.gefragt,
        "ausgewichen": ki.ausgewichen,
        "gegenspieler": gegenspieler,
        "seed": int(seed),
        "dauer_s": round(time.monotonic() - begonnen, 1),
        "protokoll": protokoll,
        "einordnung": einordnen(ki, ergebnis),
    }


def einordnen(ki: KIGegner, ergebnis) -> str:
    """Was der Kampf über das Modell sagt — in einem Satz."""
    if not ki.gefragt:
        return ("In diesem Kampf gab es keine einzige echte Wahl — das Modell "
                "wurde nie gefragt. Das Ergebnis sagt nichts über es aus.")
    quote = ki.ausgewichen * 100 / ki.gefragt
    if quote >= 50:
        return (f"Bei {ki.ausgewichen} von {ki.gefragt} Zügen kam keine brauchbare "
                f"Antwort — mehr als die Hälfte hat die eingebaute Bewertung "
                f"entschieden. Dieses Modell taugt als Gegner nicht.")
    if quote > 0:
        return (f"{ki.ausgewichen} von {ki.gefragt} Zügen musste die eingebaute "
                f"Bewertung übernehmen. Das Modell entscheidet meistens, aber "
                f"nicht verlässlich.")
    return (f"Alle {ki.gefragt} Entscheidungen kamen vom Modell — es hat die Lage "
            f"jedes Mal gelesen und sich festgelegt.")
