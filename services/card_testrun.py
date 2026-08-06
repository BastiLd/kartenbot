"""Testlauf: eine Karte gegen alle anderen.

Beantwortet die Frage, die man einer Karte nicht ansieht — ist sie zu stark,
zu schwach oder rund? Dazu tritt sie gegen jede andere Karte an, ein paar
hundert Mal je Paarung, und heraus kommen Siegquote, Rundenzahl und die
Paarungen, die aus der Reihe fallen.

Gerechnet wird mit der vorhandenen Engine unter ``simulation/``. Hier steht
nur die Klammer darum: welche Karten antreten, wie oft, und wie das Ergebnis
zusammengefasst wird.

**Warum die Duelle einzeln aufgerufen werden** statt über
``simulate_matchup``: Ein Testlauf über alle Karten rechnet Minuten. Der Bot
hat aber nur einen Faden — würde hier am Stück gerechnet, stünde in dieser
Zeit das ganze Spiel still. Deshalb wird nach jedem Duell geprüft, ob es Zeit
für einen Atemzug ist (``asyncio.sleep``). Dann kommen Discord-Ereignisse
wieder durch, und Fortschritt und Abbruch greifen mitten in einer Paarung.

Nebenbei löst das ein zweites Problem: ``simulate_duel`` setzt den globalen
Zufallsgenerator auf einen festen Startwert und stellt ihn danach wieder her.
Solange nur *zwischen* zwei Duellen abgegeben wird, bekommt ein echter Kampf
im Spiel nie einen manipulierten Zufall zu sehen. Würde die Simulation
dagegen in einem eigenen Faden laufen, wäre genau das möglich.

Angefasst wird nichts: ``simulate_duel`` arbeitet auf tiefen Kopien der
Karten, und geschrieben wird hier gar nichts.
"""
from __future__ import annotations

import asyncio
import random
import time

from simulation.config import DEFAULT_AVERAGE_MISTAKE_RATE
from simulation.engine import PLAYER_ONE_ID, PLAYER_TWO_ID, simulate_duel
from simulation.loader import canonical_hero_name, load_base_runtime_cards

# Wie gespielt wird. Die Namen sind die der Engine, der Text ist für die
# Oberfläche.
SPIELWEISEN = {
    "optimal": "Bestmöglich — beide spielen jeden Zug so gut es geht",
    "average": "Wie Menschen — mit Fehlern, wie sie im Spiel vorkommen",
}

# Auswahl für die Oberfläche. Alle Werte sind gerade, damit beide Seiten
# gleich oft anfangen (wer beginnt, hat einen Vorteil).
KAMPFZAHLEN = (100, 200, 500)
STANDARD_KAEMPFE = 200

# Nach so vielen Millisekunden ununterbrochenem Rechnen wird abgegeben, damit
# der Bot bedienbar bleibt. An der Zeit ausgerichtet und nicht an der Anzahl
# der Duelle: Auf dem Server dauert ein Duell länger als auf einem schnellen
# Rechner, die Blockade soll aber überall gleich kurz sein.
HAEPPCHEN_MS = 250

# Wie lange die Pause dann dauert. Kurz genug, um kaum ins Gewicht zu fallen
# (rund 2 % Aufschlag), lang genug, damit wartende Aufgaben drankommen.
PAUSE_MS = 5

# Nur alle so vielen Duellen wird nachgesehen, ob abgebrochen werden soll —
# das ist eine Datenbankabfrage und muss nicht hundertmal je Sekunde laufen.
ABBRUCH_ALLE = 100


class TestlaufFehler(Exception):
    """Der Testlauf kann so nicht laufen — Text ist für den Menschen gedacht."""


def _quote(treffer: int, gesamt: int) -> float:
    return 0.0 if gesamt <= 0 else round(treffer * 100 / gesamt, 1)


def einordnen(siegquote: float, paarungen: list[dict]) -> dict:
    """Eine Faustregel in Worten — ohne KI, nur aus den Zahlen.

    Bezugspunkt ist 50 %: Über alle Karten gemittelt gewinnt jede Karte
    definitionsgemäß die Hälfte ihrer Kämpfe. Wer deutlich darüber liegt,
    ist stärker als das Feld, wer darunter liegt, schwächer.

    Die Beurteilung durch das Sprachmodell kommt später und ersetzt das hier
    nicht — sie kann sagen *woran* es liegt, diese Einordnung nur *dass*.
    """
    if siegquote >= 65:
        stufe, art = "zu stark", "bad"
        text = ("Diese Karte gewinnt deutlich mehr, als sie sollte. "
                "Wer sie hat, ist im Vorteil.")
    elif siegquote >= 57:
        stufe, art = "etwas stark", "warn"
        text = "Etwas über dem Feld — auffällig, aber noch kein Ausreißer."
    elif siegquote >= 43:
        stufe, art = "rund", "ok"
        text = "Liegt im Rahmen. Gewinnt ungefähr so oft, wie sie verliert."
    elif siegquote >= 35:
        stufe, art = "etwas schwach", "warn"
        text = "Etwas unter dem Feld — spielbar, aber im Nachteil."
    else:
        stufe, art = "zu schwach", "bad"
        text = ("Diese Karte verliert deutlich öfter, als sie sollte. "
                "Wer sie zieht, hat Pech.")

    # Eine Karte kann im Schnitt rund sein und trotzdem taugen, weil sie
    # gegen die Hälfte des Feldes chancenlos ist und die andere Hälfte
    # überrennt. Das steht in der Siegquote nicht drin, deshalb extra.
    klar = [p for p in paarungen if p["siegquote"] >= 80 or p["siegquote"] <= 20]
    if len(paarungen) >= 5 and len(klar) * 100 / len(paarungen) >= 30:
        text += (f" Auffällig: {len(klar)} von {len(paarungen)} Paarungen sind "
                 f"so gut wie entschieden, bevor sie beginnen.")

    return {"stufe": stufe, "art": art, "text": text}


def _finde_karte(karten: list[dict], name: str) -> dict:
    gesucht = str(name or "").strip()
    if not gesucht:
        raise TestlaufFehler("Es wurde keine Karte angegeben.")
    for karte in karten:
        if canonical_hero_name(karte) == gesucht:
            return karte
    raise TestlaufFehler(
        f"Die Karte „{gesucht}“ ist dem Spiel nicht bekannt. Der Testlauf geht "
        f"nur mit Grundkarten, nicht mit Varianten.")


async def laufen(karten_name: str, *, kaempfe_je_paarung: int = STANDARD_KAEMPFE,
                 spielweise: str = "optimal",
                 fehlerquote: float = DEFAULT_AVERAGE_MISTAKE_RATE,
                 seed: int | None = None, karten: list[dict] | None = None,
                 progress=None, cancelled=None) -> dict:
    """Lässt eine Karte gegen alle anderen antreten.

    ``progress(erledigt, gesamt, stufe)`` und ``cancelled() -> bool`` sind
    dieselben Rückrufe wie beim Verlaufs-Scan; beide dürfen fehlen.

    ``karten`` ist nur für Tests da — normalerweise kommt die Liste aus dem
    Spiel, mit allen Änderungen, die über die Website gemacht wurden.
    """
    if spielweise not in SPIELWEISEN:
        raise TestlaufFehler(f"Die Spielweise „{spielweise}“ gibt es nicht. "
                             f"Möglich: {', '.join(SPIELWEISEN)}")
    kaempfe = int(kaempfe_je_paarung)
    if not 2 <= kaempfe <= 1000:
        raise TestlaufFehler("Die Zahl der Kämpfe je Paarung muss zwischen 2 "
                             "und 1000 liegen.")
    if kaempfe % 2:
        # Wer anfängt, hat einen Vorteil. Bei ungerader Zahl bekäme eine Seite
        # ihn öfter, und das Ergebnis wäre um ein paar Prozent daneben.
        kaempfe += 1

    alle_karten = karten if karten is not None else load_base_runtime_cards()
    if len(alle_karten) < 2:
        raise TestlaufFehler("Es gibt zu wenige Karten für einen Testlauf.")

    karte = _finde_karte(alle_karten, karten_name)
    name = canonical_hero_name(karte)
    gegner_liste = [k for k in alle_karten if canonical_hero_name(k) != name]

    if seed is None:
        seed = random.randrange(0, 2**31)
    rng = random.Random(seed)

    begonnen = time.monotonic()
    letzter_atemzug = begonnen
    paarungen: list[dict] = []
    siege = niederlagen = unentschieden = 0
    runden_gesamt = 0
    duelle_seit_pruefung = 0
    abgebrochen = False

    async def _abbruch() -> bool:
        return bool(cancelled and await cancelled())

    async def _atmen() -> None:
        """Dem Bot Luft geben, wenn lange genug am Stück gerechnet wurde."""
        nonlocal letzter_atemzug
        if (time.monotonic() - letzter_atemzug) * 1000 >= HAEPPCHEN_MS:
            await asyncio.sleep(PAUSE_MS / 1000)
            letzter_atemzug = time.monotonic()

    for nummer, gegner in enumerate(gegner_liste, start=1):
        if await _abbruch():
            abgebrochen = True
            break

        gegner_name = canonical_hero_name(gegner)
        if progress:
            await progress(nummer - 1, len(gegner_liste),
                           f"{name} gegen {gegner_name} ({nummer}/{len(gegner_liste)})")

        s = n = u = 0
        runden_hier = 0
        for lauf in range(kaempfe):
            ergebnis = simulate_duel(
                karte, gegner,
                # Abwechselnd anfangen, damit der Startvorteil beide Seiten
                # gleich oft trifft.
                starter_id=PLAYER_ONE_ID if lauf % 2 == 0 else PLAYER_TWO_ID,
                duel_seed=rng.randrange(0, 2**31),
                strategy_a_name=spielweise,
                strategy_b_name=spielweise,
                average_mistake_rate=fehlerquote,
            )
            runden_hier += ergebnis.rounds
            if ergebnis.draw:
                u += 1
            elif ergebnis.winner == name:
                s += 1
            else:
                n += 1

            await _atmen()
            duelle_seit_pruefung += 1
            if duelle_seit_pruefung >= ABBRUCH_ALLE:
                duelle_seit_pruefung = 0
                if await _abbruch():
                    abgebrochen = True
                    break

        if abgebrochen:
            # Angefangene Paarung nicht mitzählen — sie hat weniger Kämpfe als
            # die anderen und würde das Bild verzerren.
            break

        siege += s
        niederlagen += n
        unentschieden += u
        runden_gesamt += runden_hier
        paarungen.append({
            "gegner": gegner_name,
            "kaempfe": kaempfe,
            "siege": s,
            "niederlagen": n,
            "unentschieden": u,
            "siegquote": _quote(s, kaempfe),
            "runden_schnitt": round(runden_hier / kaempfe, 1),
        })

    if progress:
        await progress(len(paarungen), len(gegner_liste),
                       "abgebrochen" if abgebrochen else "fertig")

    gesamt = siege + niederlagen + unentschieden
    siegquote = _quote(siege, gesamt)
    # Stärkste Gegner zuerst: Die Paarungen, in denen es schlecht läuft,
    # sind die interessanten.
    paarungen.sort(key=lambda p: p["siegquote"])

    return {
        "karte": name,
        "spielweise": spielweise,
        "kaempfe_je_paarung": kaempfe,
        "gegner": len(paarungen),
        "kaempfe": gesamt,
        "siege": siege,
        "niederlagen": niederlagen,
        "unentschieden": unentschieden,
        "siegquote": siegquote,
        "runden_schnitt": round(runden_gesamt / gesamt, 1) if gesamt else 0.0,
        "seed": int(seed),
        "dauer_s": round(time.monotonic() - begonnen, 1),
        "abgebrochen": abgebrochen,
        "einordnung": einordnen(siegquote, paarungen),
        "paarungen": paarungen,
    }
