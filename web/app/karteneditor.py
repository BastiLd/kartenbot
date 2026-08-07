"""Karten von der Website aus ändern.

Das Gegenstück zu services/card_store.py auf der Bot-Seite. Beide arbeiten
auf denselben Tabellen; hier steht die schreibende Hälfte, dort die, die die
Änderungen auf die laufenden Karten legt.

Bewusst eigener Code statt Wiederverwendung: Der Bot arbeitet mit aiosqlite,
die Website mit dem eingebauten sqlite3. Die paar Zeilen doppelt zu haben ist
weniger Ärger, als eine weitere Abhängigkeit in den Container zu ziehen.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import cards, database

# Muss zur Liste in services/card_store.py passen.
AENDERBAR = ("seltenheit", "hp", "beschreibung", "bild", "attacks")

# Felder eines Angriffs, die sich bearbeiten lassen. Bewusst nur die
# haeufigen: Es gibt 26 verschiedene Angriffsfelder, viele davon kommen genau
# einmal im ganzen Spiel vor (etwa bonus_damage_if_condition). Die blind
# bearbeitbar zu machen waere gefaehrlich - sie werden unveraendert
# durchgereicht, damit nichts davon verlorengeht.
ANGRIFF_FELDER = ("name", "info", "damage", "cooldown_turns", "heal",
                  "self_damage", "multi_hit", "button_style", "lifesteal_ratio",
                  "is_standard_attack", "effects")

KNOEPFE = ("red", "blurple", "green", "grey", "gray")

GRENZEN = {
    "hp": (1, 10000),
    "beschreibung": (0, 500),
    "bild": (0, 500),
}


class EditorFehler(Exception):
    """Eingabe nicht brauchbar — Text ist für den Menschen gedacht."""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS card_overrides (
            karten_name TEXT PRIMARY KEY,
            daten_json  TEXT NOT NULL,
            geaendert_am TEXT NOT NULL,
            geaendert_von TEXT NOT NULL DEFAULT ''
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS card_override_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            karten_name  TEXT NOT NULL,
            daten_json   TEXT NOT NULL,
            geaendert_am TEXT NOT NULL,
            geaendert_von TEXT NOT NULL DEFAULT ''
        )
    """)


def _zahl_oder_bereich(wert, feld: str):
    """Schaden steht mal als Zahl da, mal als Bereich [von, bis].

    Beides muss durchkommen — die Kartendatei nutzt beide Formen, und wer
    einen festen Schaden will, soll nicht [7, 7] tippen müssen.
    """
    if isinstance(wert, (list, tuple)):
        if len(wert) != 2:
            raise EditorFehler(f"„{feld}“ als Bereich braucht genau zwei Werte: von und bis.")
        try:
            von, bis = int(wert[0]), int(wert[1])
        except (TypeError, ValueError):
            raise EditorFehler(f"„{feld}“ muss aus ganzen Zahlen bestehen.") from None
        if von > bis:
            raise EditorFehler(f"Bei „{feld}“ ist der untere Wert größer als der obere.")
        if von < 0 or bis > 9999:
            raise EditorFehler(f"„{feld}“ muss zwischen 0 und 9999 liegen.")
        return [von, bis]
    try:
        zahl = int(wert)
    except (TypeError, ValueError):
        raise EditorFehler(f"„{feld}“ muss eine ganze Zahl oder ein Bereich sein.") from None
    if not 0 <= zahl <= 9999:
        raise EditorFehler(f"„{feld}“ muss zwischen 0 und 9999 liegen.")
    return zahl


def _pruefe_angriffe(name: str, neue_angriffe: list) -> list:
    """Angriffe zusammenbauen und von der Kartenprüfung des Bots abnehmen lassen.

    Der Aufbau: Die unveränderte Karte wird geholt, die bearbeitbaren Felder
    werden überschrieben, alles andere bleibt wie es war. Dann läuft die
    echte Prüfung darüber — dieselbe, die auch der Bot benutzt. Was sie
    beanstandet, wird gar nicht erst gespeichert.
    """
    original = next((k for k in _rohkarten() if k.get("name") == name), None)
    if original is None:
        raise EditorFehler(f"Die Karte „{name}“ gibt es nicht.")
    vorhandene = original.get("attacks") or []
    if len(neue_angriffe) != len(vorhandene):
        raise EditorFehler("Die Anzahl der Angriffe lässt sich nicht ändern — "
                           "nur die vorhandenen bearbeiten.")

    zusammengebaut = []
    for index, (alt, neu) in enumerate(zip(vorhandene, neue_angriffe), start=1):
        # Mit dem Original anfangen, damit seltene Felder erhalten bleiben.
        angriff = dict(alt)
        for feld, wert in (neu or {}).items():
            if feld not in ANGRIFF_FELDER:
                raise EditorFehler(f"Angriff {index}: „{feld}“ lässt sich nicht ändern.")
            if feld in ("damage", "heal", "self_damage", "cooldown_turns", "multi_hit"):
                if wert in ("", None):
                    angriff.pop(feld, None)      # leer heisst: gibt es hier nicht
                    continue
                angriff[feld] = _zahl_oder_bereich(wert, feld)
            elif feld == "lifesteal_ratio":
                try:
                    anteil = float(wert)
                except (TypeError, ValueError):
                    raise EditorFehler(f"Angriff {index}: Lebensraub muss eine Zahl sein.") from None
                if not 0 <= anteil <= 1:
                    raise EditorFehler(f"Angriff {index}: Lebensraub liegt zwischen 0 und 1 "
                                       "(0,5 = die Hälfte des Schadens).")
                angriff[feld] = anteil
            elif feld == "is_standard_attack":
                angriff[feld] = bool(wert)
            elif feld == "button_style":
                if wert not in KNOEPFE:
                    raise EditorFehler(f"Angriff {index}: Knopffarbe „{wert}“ gibt es nicht. "
                                       f"Möglich: {', '.join(KNOEPFE)}")
                angriff[feld] = wert
            elif feld == "effects":
                if not isinstance(wert, list):
                    raise EditorFehler(f"Angriff {index}: Nebenwirkungen müssen eine Liste sein.")
                angriff[feld] = wert
            else:
                text = str(wert or "").strip()
                if feld == "name" and not text:
                    raise EditorFehler(f"Angriff {index} braucht einen Namen.")
                if len(text) > 200:
                    raise EditorFehler(f"Angriff {index}: „{feld}“ ist zu lang (höchstens 200).")
                angriff[feld] = text
        zusammengebaut.append(angriff)

    # Genau ein Standardangriff — der Knopf oben links im Kampf.
    standard = [a for a in zusammengebaut if a.get("is_standard_attack")]
    if len(standard) != 1:
        raise EditorFehler(f"Genau ein Angriff muss der Standardangriff sein, "
                           f"gerade sind es {len(standard)}.")

    _abnehmen(original, zusammengebaut)
    return zusammengebaut


def _abnehmen(original: dict, angriffe: list) -> None:
    """Die Kartenprüfung des Bots über die geänderte Karte laufen lassen."""
    try:
        from services import card_validation
    except ImportError:
        return          # Ohne die Prüfung lieber weitermachen als blockieren
    probe = dict(original)
    probe["attacks"] = angriffe
    fehler = card_validation.validate_cards([probe])
    if fehler:
        # Der erste Fehler reicht — mehr überfordert nur.
        raise EditorFehler(f"Die Kartenprüfung beanstandet: {fehler[0]}")


def _rohkarten() -> list:
    try:
        from karten import karten as roh
        return roh
    except ImportError:
        return []


def pruefe(aenderungen: dict) -> dict:
    """Eingaben prüfen, bevor irgendetwas gespeichert wird.

    Lieber hier ablehnen als spaeter im Kampf eine Karte mit -5 Lebenspunkten
    zu haben.
    """
    sauber = {}
    for feld, wert in (aenderungen or {}).items():
        if feld not in AENDERBAR:
            raise EditorFehler(f"Das Feld „{feld}“ lässt sich nicht ändern.")
        if feld == "hp":
            try:
                zahl = int(wert)
            except (TypeError, ValueError):
                raise EditorFehler("Die Lebenspunkte müssen eine ganze Zahl sein.") from None
            unten, oben = GRENZEN["hp"]
            if not unten <= zahl <= oben:
                raise EditorFehler(f"Lebenspunkte müssen zwischen {unten} und {oben} liegen.")
            sauber[feld] = zahl
            continue
        text = str(wert or "").strip()
        oben = GRENZEN.get(feld, (0, 500))[1]
        if len(text) > oben:
            raise EditorFehler(f"„{feld}“ darf höchstens {oben} Zeichen haben.")
        if feld == "bild" and text and not text.startswith(("http://", "https://")):
            raise EditorFehler("Die Bildadresse muss mit http:// oder https:// beginnen.")
        if feld == "seltenheit" and text and text not in _seltenheiten():
            raise EditorFehler(f"Die Seltenheit „{text}“ gibt es nicht. "
                               f"Möglich: {', '.join(sorted(_seltenheiten()))}")
        sauber[feld] = text
    return sauber


def _seltenheiten() -> set:
    return {k.get("seltenheit") for k in cards.catalog() if k.get("seltenheit")}


def alle() -> dict:
    with database.read_connection() as con:
        try:
            zeilen = database.fetch_all(
                con, "SELECT karten_name, daten_json, geaendert_am, geaendert_von "
                     "FROM card_overrides")
        except sqlite3.OperationalError:
            return {}
    out = {}
    for z in zeilen:
        try:
            out[z["karten_name"]] = {
                "aenderungen": json.loads(z["daten_json"]),
                "geaendert_am": z["geaendert_am"],
                "geaendert_von": z["geaendert_von"],
            }
        except (ValueError, TypeError):
            continue
    return out


def setze(name: str, aenderungen: dict, *, von: str = "") -> dict:
    if not any(k.get("name") == name for k in cards.catalog()):
        raise EditorFehler(f"Die Karte „{name}“ gibt es nicht.")

    # Angriffe brauchen den Kartennamen, um gegen das Original geprüft zu
    # werden — deshalb getrennt von den einfachen Feldern.
    aenderungen = dict(aenderungen or {})
    angriffe = aenderungen.pop("attacks", None)
    sauber = pruefe(aenderungen) if aenderungen else {}
    if angriffe is not None:
        sauber["attacks"] = _pruefe_angriffe(name, angriffe)
    if not sauber:
        raise EditorFehler("Es wurde nichts angegeben, was sich ändern ließe.")
    with database.write_connection() as con:
        _schema(con)
        vorher = con.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,)).fetchone()
        if vorher:
            con.execute(
                "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
                "geaendert_von) VALUES (?, ?, ?, ?)",
                (name, vorher["daten_json"], _jetzt(), von))
        con.execute(
            "INSERT INTO card_overrides (karten_name, daten_json, geaendert_am, geaendert_von) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(karten_name) DO UPDATE SET "
            "daten_json = excluded.daten_json, geaendert_am = excluded.geaendert_am, "
            "geaendert_von = excluded.geaendert_von",
            (name, json.dumps(sauber, ensure_ascii=False), _jetzt(), von))
    return sauber


def zuruecksetzen(name: str, *, von: str = "") -> bool:
    with database.write_connection() as con:
        _schema(con)
        vorher = con.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,)).fetchone()
        if not vorher:
            return False
        con.execute(
            "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
            "geaendert_von) VALUES (?, ?, ?, ?)", (name, vorher["daten_json"], _jetzt(), von))
        con.execute("DELETE FROM card_overrides WHERE karten_name = ?", (name,))
    return True


# --------------------------------------------------------------------------
# Testlauf
# --------------------------------------------------------------------------
def _testlauf_modul():
    """Die Testlauf-Logik des Bots.

    Die erlaubten Werte stehen dort und werden von dort geholt, damit es nur
    eine Wahrheit gibt. Gerechnet wird ohnehin im Bot — ohne dessen Ordner
    gäbe es gar keine Karten, deshalb hier ein klarer Abbruch statt einer
    zweiten Liste, die irgendwann auseinanderläuft.
    """
    try:
        from services import card_testrun
    except ImportError:
        raise EditorFehler("Für den Testlauf fehlt der Kartenordner des Bots. "
                           "Stimmt KARTENBOT_DIR?") from None
    return card_testrun


def testlauf_moeglichkeiten() -> dict:
    """Was sich einstellen lässt — für die Oberfläche."""
    modul = _testlauf_modul()
    return {
        "spielweisen": [{"wert": w, "text": d["text"],
                         "durchgaenge": len(d["spielweisen"])}
                        for w, d in modul.AUSWAHL.items()],
        "standard_spielweise": modul.STANDARD_AUSWAHL,
        "kampfzahlen": list(modul.KAMPFZAHLEN),
        "standard_kaempfe": modul.STANDARD_KAEMPFE,
        # Nur Versionen, die wirklich etwas gelernt haben. Eine ohne Gewichte
        # zur Wahl zu stellen hiesse, eine Wahl anzubieten, die nichts ändert.
        "gelernte_versionen": _gelernte_versionen(),
    }


def _gelernte_versionen() -> list[dict]:
    try:
        from . import gegnerversionen
        return [{"id": v["id"], "name": v["name"],
                 "zuege": int((v.get("lernstand") or {}).get("zuege_verwertet") or 0)}
                for v in gegnerversionen.alle() if v.get("gewichte")]
    except Exception:                                          # noqa: BLE001
        # Der Testlauf muss auch dann einstellbar bleiben, wenn es mit den
        # Versionen gerade hakt.
        return []


def pruefe_testlauf(name: str, spielweise: str, kaempfe_je_paarung: int,
                    version_id: int = 0) -> dict:
    """Eingaben abnehmen, bevor ein Auftrag angelegt wird.

    Der Bot prüft dasselbe noch einmal — er muss das, weil er Aufträge auch
    von anderswo bekommen könnte. Hier geht es darum, einen Fehler sofort zu
    melden statt erst, wenn der Auftrag drankommt.

    ``version_id`` sagt, mit wessen gelernten Gewichten gerechnet wird. 0 ist
    „Standard": die eingebauten Werte, also der Testlauf wie bisher.
    """
    modul = _testlauf_modul()
    karte = str(name or "").strip()
    # Helden und Schurken: Missionsgegner sind ganz normale Karten und
    # kaempfen mit derselben Engine.
    if not any(k.get("name") == karte for k in cards.catalog()):
        from . import missionen
        if karte not in missionen.namen():
            raise EditorFehler(f"„{karte}“ gibt es weder bei den Helden "
                               f"noch bei den Schurken.")
    if spielweise not in modul.AUSWAHL:
        raise EditorFehler(f"Die Spielweise „{spielweise}“ gibt es nicht. "
                           f"Möglich: {', '.join(modul.AUSWAHL)}")
    try:
        zahl = int(kaempfe_je_paarung)
    except (TypeError, ValueError):
        raise EditorFehler("Die Zahl der Kämpfe muss eine ganze Zahl sein.") from None
    if zahl not in modul.KAMPFZAHLEN:
        raise EditorFehler(f"So viele Kämpfe sind nicht vorgesehen. Möglich: "
                           f"{', '.join(str(z) for z in modul.KAMPFZAHLEN)}")
    try:
        version = int(version_id or 0)
    except (TypeError, ValueError):
        raise EditorFehler("Die Gegner-Version ist nicht lesbar.") from None
    if version:
        from . import gegnerversionen
        gewaehlt = next((v for v in gegnerversionen.alle() if v["id"] == version), None)
        if gewaehlt is None:
            raise EditorFehler("Diese Gegner-Version gibt es nicht (mehr).")
        if not gewaehlt.get("gewichte"):
            raise EditorFehler(
                f"„{gewaehlt['name']}“ hat noch nichts gelernt — der Testlauf "
                f"käme damit auf genau dieselben Zahlen wie mit "
                f"„{gegnerversionen.STANDARD_NAME}“.")
    return {"karte": karte, "spielweise": spielweise, "kaempfe_je_paarung": zahl,
            "version_id": version}


def verlauf(name: str, limit: int = 20) -> list[dict]:
    with database.read_connection() as con:
        try:
            zeilen = database.fetch_all(
                con, "SELECT daten_json, geaendert_am, geaendert_von "
                     "FROM card_override_history WHERE karten_name = ? "
                     "ORDER BY id DESC LIMIT ?", (name, limit))
        except sqlite3.OperationalError:
            return []
    out = []
    for z in zeilen:
        try:
            out.append({"aenderungen": json.loads(z["daten_json"]),
                        "geaendert_am": z["geaendert_am"],
                        "geaendert_von": z["geaendert_von"]})
        except (ValueError, TypeError):
            continue
    return out
