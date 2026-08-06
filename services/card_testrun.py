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
    "optimal": "bestmöglich",
    "average": "wie Menschen, mit Fehlern",
}

# Was sich im Testlauf-Fenster auswählen lässt. „Beides" rechnet zwei
# Durchgänge hintereinander — das ist die Voreinstellung, weil erst der
# Vergleich zeigt, ob eine Karte nur bei perfektem Spiel stark ist. Eine
# Karte, die alles gewinnt, solange kein Fehler passiert, ist im Discord
# etwas ganz anderes als eine, die auch Fehler verzeiht.
AUSWAHL = {
    "beides": {
        "text": "Beides nacheinander — zeigt, ob eine Karte nur bei perfektem Spiel stark ist",
        "spielweisen": ("optimal", "average"),
    },
    "optimal": {
        "text": "Nur bestmöglich — beide spielen jeden Zug so gut es geht",
        "spielweisen": ("optimal",),
    },
    "average": {
        "text": "Nur wie Menschen — mit Fehlern, wie sie im Spiel vorkommen",
        "spielweisen": ("average",),
    },
}
STANDARD_AUSWAHL = "beides"

# Ab diesem Abstand in Prozentpunkten lohnt es, den Unterschied zwischen den
# beiden Durchgängen zu erwähnen. Darunter ist es Rauschen.
VERGLEICH_SCHWELLE = 8.0

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


def rolle_von(name: str) -> str:
    """Held, kleiner Gegner oder Boss.

    Davon hängt ab, was „gut" überhaupt heisst: Ein Boss *soll* die meisten
    Kämpfe gewinnen, ein Gegner der ersten Welle *soll* zu schaffen sein.
    Beide an 50 % zu messen wäre Unsinn.
    """
    gesucht = str(name or "").strip()
    try:
        import mission_enemies                                  # type: ignore
    except Exception:                                           # noqa: BLE001
        return "held"
    for feld in dir(mission_enemies):
        if not feld.startswith("OPERATION_") or not feld.endswith("_ENCOUNTERS"):
            continue
        liste = getattr(mission_enemies, feld, None) or []
        for position, gegner in enumerate(liste):
            if str((gegner or {}).get("name") or "").strip() == gesucht:
                return "boss" if position == len(liste) - 1 else "klein"
    return "held"


# Ab welcher Siegquote welche Stufe gilt, je Rolle:
# (etwas schwach ab, rund ab, etwas stark ab, zu stark ab)
#
# Ein Boss darf und soll die meisten Kämpfe gewinnen, ein Gegner der ersten
# Wellen soll fallen - sonst kommt niemand durch die Mission. Beide an den
# 50 % der Helden zu messen wäre Unsinn.
SCHWELLEN = {
    "held": (35.0, 43.0, 57.0, 65.0),
    "klein": (3.0, 10.0, 40.0, 55.0),
    "boss": (40.0, 55.0, 80.0, 90.0),
}


def einordnen(siegquote: float, paarungen: list[dict], rolle: str = "held") -> dict:
    """Eine Faustregel in Worten — ohne KI, nur aus den Zahlen.

    Bei Helden ist der Bezugspunkt 50 %: Über alle Karten gemittelt gewinnt
    jede definitionsgemäß die Hälfte ihrer Kämpfe. Bei Missionsgegnern ist er
    ein anderer — siehe ``SCHWELLEN``.

    Die Beurteilung durch das Sprachmodell kommt später und ersetzt das hier
    nicht — sie kann sagen *woran* es liegt, diese Einordnung nur *dass*.
    """
    schwach, unten, oben, zu_stark = SCHWELLEN.get(rolle, SCHWELLEN["held"])

    if siegquote >= zu_stark:
        stufe, art = "zu stark", "bad"
        text = {
            "held": "Diese Karte gewinnt deutlich mehr, als sie sollte. "
                    "Wer sie hat, ist im Vorteil.",
            "klein": "Für einen Gegner der ersten Wellen viel zu stark — "
                     "an dem bleiben Spieler hängen, bevor die Mission losgeht.",
            "boss": "Selbst für einen Boss zu stark. Kaum eine Karte hat "
                    "hier noch eine Chance.",
        }[rolle]
    elif siegquote >= oben:
        stufe, art = "etwas stark", "warn"
        text = ("Etwas über dem, was für diese Rolle vorgesehen ist — "
                "auffällig, aber noch kein Ausreißer.")
    elif siegquote >= unten:
        stufe, art = "rund", "ok"
        text = {
            "held": "Liegt im Rahmen. Gewinnt ungefähr so oft, wie sie verliert.",
            "klein": "Passt für einen Gegner der frühen Wellen: fordernd, "
                     "aber zu schaffen.",
            "boss": "Passt für einen Boss: gewinnt meistens, ist aber zu "
                    "besiegen.",
        }[rolle]
    elif siegquote >= schwach:
        stufe, art = "etwas schwach", "warn"
        text = "Etwas unter dem, was für diese Rolle vorgesehen ist."
    else:
        stufe, art = "zu schwach", "bad"
        text = {
            "held": "Diese Karte verliert deutlich öfter, als sie sollte. "
                    "Wer sie zieht, hat Pech.",
            "klein": "Fällt praktisch von selbst um — als Gegner kaum "
                     "spürbar.",
            "boss": "Für einen Boss zu schwach. Das Ende einer Operation "
                    "sollte mehr verlangen.",
        }[rolle]

    # Eine Karte kann im Schnitt rund sein und trotzdem taugen, weil sie
    # gegen die Hälfte des Feldes chancenlos ist und die andere Hälfte
    # überrennt. Das steht in der Siegquote nicht drin, deshalb extra.
    klar = [p for p in paarungen if p["siegquote"] >= 80 or p["siegquote"] <= 20]
    if len(paarungen) >= 5 and len(klar) * 100 / len(paarungen) >= 30:
        text += (f" Auffällig: {len(klar)} von {len(paarungen)} Paarungen sind "
                 f"so gut wie entschieden, bevor sie beginnen.")

    return {"stufe": stufe, "art": art, "text": text, "rolle": rolle,
            "erwartet_von": unten, "erwartet_bis": oben}


def missionsgegner() -> list[dict]:
    """Die Gegner aus den Missionen — Schurken statt Helden.

    Sie sind ganz normale Karten (Name, Lebenspunkte, Angriffe) und kämpfen
    mit derselben Engine. Deshalb lässt sich auch für einen Boss ausrechnen,
    wie er gegen die Spielerkarten dasteht.
    """
    try:
        import mission_enemies                                  # type: ignore
    except Exception:                                           # noqa: BLE001
        return []
    heraus: list[dict] = []
    gesehen: set[str] = set()
    for feld in dir(mission_enemies):
        if not feld.startswith("OPERATION_") or not feld.endswith("_ENCOUNTERS"):
            continue
        for gegner in getattr(mission_enemies, feld, None) or []:
            name = str((gegner or {}).get("name") or "").strip()
            if name and name not in gesehen and gegner.get("attacks"):
                gesehen.add(name)
                heraus.append(gegner)
    return heraus


def _finde_karte(karten: list[dict], name: str) -> dict:
    """Die zu prüfende Karte suchen — erst bei den Helden, dann bei den Schurken."""
    gesucht = str(name or "").strip()
    if not gesucht:
        raise TestlaufFehler("Es wurde keine Karte angegeben.")
    for karte in karten:
        if canonical_hero_name(karte) == gesucht:
            return karte
    for gegner in missionsgegner():
        if str(gegner.get("name") or "").strip() == gesucht:
            return gegner
    raise TestlaufFehler(
        f"„{gesucht}“ ist dem Spiel nicht bekannt. Der Testlauf geht mit "
        f"Grundkarten und mit Missionsgegnern, nicht mit Varianten.")


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
    rolle = rolle_von(name)
    # Angetreten wird immer gegen die Spielerkarten. Bei einem Missionsgegner
    # ist genau das die Frage: Wie steht er gegen das, was Spieler mitbringen?
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
        "rolle": rolle,
        "einordnung": einordnen(siegquote, paarungen, rolle),
        "paarungen": paarungen,
    }


def vergleiche(durchgaenge: list[dict]) -> dict | None:
    """Was der Unterschied zwischen zwei Spielweisen bedeutet.

    Das ist der eigentliche Grund, beides zu rechnen: Eine Karte, die alles
    gewinnt, solange kein Fehler passiert, ist im Spiel etwas ganz anderes
    als eine, die auch Fehler verzeiht. In einer einzelnen Siegquote steht
    das nicht drin.
    """
    nach_weise = {d["spielweise"]: d for d in durchgaenge if d.get("kaempfe")}
    if len(nach_weise) < 2:
        return None
    perfekt = nach_weise["optimal"]["siegquote"]
    menschlich = nach_weise["average"]["siegquote"]
    abstand = round(perfekt - menschlich, 1)

    if abs(abstand) < VERGLEICH_SCHWELLE:
        text = (f"Die Karte spielt sich in beiden Fällen ähnlich "
                f"({perfekt} % gegenüber {menschlich} %). Wie gut jemand spielt, "
                f"ändert an ihrer Stärke wenig.")
    elif abstand > 0:
        text = (f"Bei perfektem Spiel deutlich stärker: {perfekt} % gegenüber "
                f"{menschlich} %, wenn Fehler passieren. Diese Karte belohnt, "
                f"wer sie beherrscht — in der Hand von Anfängern bringt sie "
                f"weniger, als die Zahlen zunächst vermuten lassen.")
    else:
        text = (f"Verzeiht Fehler: {menschlich} % mit Fehlern gegenüber "
                f"{perfekt} % bei perfektem Spiel. Sie ist damit gerade für "
                f"die stark, die noch nicht jeden Zug abwägen.")

    return {"abstand": abstand, "optimal": perfekt, "average": menschlich, "text": text}


async def laufen_mehrfach(karten_name: str, *, auswahl: str = STANDARD_AUSWAHL,
                          kaempfe_je_paarung: int = STANDARD_KAEMPFE,
                          fehlerquote: float = DEFAULT_AVERAGE_MISTAKE_RATE,
                          seed: int | None = None, karten: list[dict] | None = None,
                          progress=None, cancelled=None) -> dict:
    """Einen Testlauf über eine oder mehrere Spielweisen.

    Beide Durchgänge bekommen denselben Startwert. Sie werden dadurch nicht
    identisch — die Spielweisen wählen andere Züge —, aber jeder für sich
    bleibt nachrechenbar, und es gibt nur eine Zahl zu merken.
    """
    if auswahl not in AUSWAHL:
        raise TestlaufFehler(f"Die Spielweise „{auswahl}“ gibt es nicht. "
                             f"Möglich: {', '.join(AUSWAHL)}")
    spielweisen = AUSWAHL[auswahl]["spielweisen"]
    if seed is None:
        seed = random.randrange(0, 2**31)

    begonnen = time.monotonic()
    durchgaenge: list[dict] = []

    for nummer, spielweise in enumerate(spielweisen):
        async def teilfortschritt(erledigt, gesamt, stufe=None, _n=nummer):
            # Über alle Durchgänge durchzählen, sonst spränge der Balken beim
            # zweiten wieder auf null zurück.
            if progress:
                zusatz = f" [Durchgang {_n + 1} von {len(spielweisen)}]" \
                    if len(spielweisen) > 1 else ""
                await progress(_n * gesamt + erledigt, gesamt * len(spielweisen),
                               f"{stufe}{zusatz}" if stufe else None)

        durchgaenge.append(await laufen(
            karten_name, kaempfe_je_paarung=kaempfe_je_paarung, spielweise=spielweise,
            fehlerquote=fehlerquote, seed=seed, karten=karten,
            progress=teilfortschritt if progress else None, cancelled=cancelled))
        if durchgaenge[-1]["abgebrochen"]:
            break

    kaempfe = sum(d["kaempfe"] for d in durchgaenge)
    siege = sum(d["siege"] for d in durchgaenge)
    niederlagen = sum(d["niederlagen"] for d in durchgaenge)
    unentschieden = sum(d["unentschieden"] for d in durchgaenge)
    runden = sum(d["runden_schnitt"] * d["kaempfe"] for d in durchgaenge)

    return {
        "karte": durchgaenge[0]["karte"],
        "rolle": durchgaenge[0].get("rolle", "held"),
        "auswahl": auswahl,
        "kaempfe_je_paarung": durchgaenge[0]["kaempfe_je_paarung"],
        "gegner": max(d["gegner"] for d in durchgaenge),
        "kaempfe": kaempfe,
        "siege": siege,
        "niederlagen": niederlagen,
        "unentschieden": unentschieden,
        "siegquote": _quote(siege, kaempfe),
        "runden_schnitt": round(runden / kaempfe, 1) if kaempfe else 0.0,
        "seed": int(seed),
        "dauer_s": round(time.monotonic() - begonnen, 1),
        "abgebrochen": any(d["abgebrochen"] for d in durchgaenge),
        "vergleich": vergleiche(durchgaenge),
        "durchgaenge": durchgaenge,
    }
