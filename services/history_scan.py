"""Server-Verlauf auswerten.

Liest die gewählten Kanäle über die Discord-Schnittstelle und leitet daraus
Kennzahlen und Einordnungen pro Mitglied ab.

WICHTIG — Datenschutz ist hier eingebaut, nicht nachträglich aufgesetzt:
Nachrichteninhalte werden ausschließlich im Arbeitsspeicher verarbeitet und
sofort wieder verworfen. Gespeichert werden nur Zahlen und Schlagworte
(„Bilder-Anteil 41 %", „Meme-Typ"). Es wird nie ein Nachrichtentext, nie ein
Zitat und nie ein Dateiname in die Datenbank geschrieben.

Der Scan arbeitet in kleinen Häppchen mit Pausen: so bleibt der Bot nebenbei
bedienbar und Discords Anfragebremse greift nicht.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import discord

# --- Zeiträume, die die Website anbietet -----------------------------------
RANGES = {
    "3m": 90,
    "6m": 182,
    "1y": 365,
    "all": None,
}

# --- Erkennungsmuster ------------------------------------------------------
GIF_HOSTS = ("tenor.com", "giphy.com", "gfycat.com", "imgur.com", "redgifs.com")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic")
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv")
LAUGH_EMOJI = {"😂", "🤣", "😹", "💀", "😆", "😅", "🤡", "😭", "🗿"}
HEART_EMOJI = {"❤️", "❤", "💖", "💕", "🥰", "😍", "🙏", "👍", "🫶"}
LINK = re.compile(r"https?://(?:www\.)?([^/\s]+)", re.I)
CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
UNICODE_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
GREETING = re.compile(
    r"\b(willkommen|welcome|servus|moin|hallo zusammen|schön dass du da bist|"
    r"viel spaß bei uns|herzlich willkommen)\b", re.I
)
QUESTION_WORDS = re.compile(
    r"\b(wie|was|warum|wieso|weshalb|wo|wann|wer|welche[rs]?|kann (mir|jemand)|"
    r"hilfe|hilft mir)\b", re.I
)
NIGHT_HOURS = range(0, 6)


def word_pattern(words: list[str]) -> re.Pattern | None:
    """Eigene Wortliste zu einem Suchmuster. Ganze Wörter, Groß/Klein egal."""
    clean = [re.escape(w.strip()) for w in words if w and w.strip()]
    if not clean:
        return None
    return re.compile(r"(?<!\w)(" + "|".join(clean) + r")(?!\w)", re.I)


def _blank_stats() -> dict:
    return {
        "nachrichten": 0, "zeichen": 0, "woerter": 0,
        "bilder": 0, "videos": 0, "gifs": 0, "links": 0, "dateien": 0,
        "emojis": 0, "fragen": 0, "antworten_gegeben": 0, "antworten_erhalten": 0,
        "erwaehnungen_gemacht": 0, "reaktionen_erhalten": 0, "lach_reaktionen": 0,
        "herz_reaktionen": 0, "nachts": 0, "begruessungen": 0, "hilfe_antworten": 0,
        "wortliste_treffer": 0, "kanaele": set(),
        "erste_nachricht": None, "letzte_nachricht": None,
        "stunden": [0] * 24, "wochentage": [0] * 7,
    }


def _finalize(stats: dict) -> dict:
    """Rohzahlen in Anteile und lesbare Werte umrechnen."""
    n = max(stats["nachrichten"], 1)
    fertig = {k: v for k, v in stats.items() if k not in ("kanaele",)}
    fertig["kanaele"] = len(stats["kanaele"])
    fertig["laenge_schnitt"] = round(stats["zeichen"] / n, 1)
    fertig["bild_anteil"] = round((stats["bilder"] + stats["gifs"]) * 100 / n, 1)
    fertig["gif_anteil"] = round(stats["gifs"] * 100 / n, 1)
    fertig["nacht_anteil"] = round(stats["nachts"] * 100 / n, 1)
    fertig["frage_anteil"] = round(stats["fragen"] * 100 / n, 1)
    fertig["lacher_pro_nachricht"] = round(stats["lach_reaktionen"] / n, 2)
    fertig["reaktionen_pro_nachricht"] = round(stats["reaktionen_erhalten"] / n, 2)
    return fertig


# --------------------------------------------------------------------------
# Einordnung
# --------------------------------------------------------------------------
def derive_tags(s: dict, mittel: dict) -> list[dict]:
    """Schlagworte samt Begründung — jede Einordnung ist nachvollziehbar."""
    tags: list[dict] = []
    n = s["nachrichten"]

    def add(key: str, label: str, grund: str) -> None:
        tags.append({"key": key, "label": label, "grund": grund})

    if n == 0:
        add("stiller_leser", "Stiller Mitleser",
            "In den ausgewerteten Kanälen und im gewählten Zeitraum keine einzige Nachricht.")
        return tags

    if n >= 20 and s["gif_anteil"] >= 15:
        add("meme_typ", "Meme-Typ",
            f"{s['gif_anteil']} % der Nachrichten sind GIFs oder Meme-Links "
            f"(Durchschnitt auf dem Server: {mittel['gif_anteil']} %).")
    elif n >= 20 and s["bild_anteil"] >= 30:
        add("bilder_poster", "Bilder-Poster",
            f"{s['bild_anteil']} % der Nachrichten enthalten ein Bild oder Video.")

    if n >= 15 and s["lacher_pro_nachricht"] >= max(0.25, mittel["lacher_pro_nachricht"] * 1.6):
        add("lustig", "Der Lustige",
            f"Bekommt im Schnitt {s['lacher_pro_nachricht']} Lach-Reaktionen pro Nachricht — "
            f"deutlich über dem Serverschnitt von {mittel['lacher_pro_nachricht']}.")

    if n >= 20 and s["laenge_schnitt"] >= 120 and s["antworten_gegeben"] >= n * 0.2:
        add("diskutierer", "Diskutierer",
            f"Lange Beiträge (Ø {s['laenge_schnitt']} Zeichen) und häufige Antworten "
            f"auf andere ({s['antworten_gegeben']}×).")

    if n >= 15 and s["hilfe_antworten"] >= 5:
        add("helfer", "Helfer",
            f"Hat {s['hilfe_antworten']}× direkt auf eine Frage einer anderen Person geantwortet.")

    if n >= 15 and s["nacht_anteil"] >= 25:
        add("nachteule", "Nachteule",
            f"{s['nacht_anteil']} % der Nachrichten entstehen zwischen 0 und 6 Uhr.")

    if s["begruessungen"] >= 3:
        add("begruesser", "Begrüßt Neue",
            f"Hat {s['begruessungen']}× neue Leute willkommen geheißen.")

    if n >= max(50, mittel["nachrichten"] * 3):
        add("vielposter", "Vielschreiber",
            f"{n} Nachrichten — etwa das {round(n / max(mittel['nachrichten'], 1), 1)}-fache "
            "des Serverschnitts.")
    elif n <= 3:
        add("karteileiche", "Kaum aktiv",
            f"Nur {n} Nachricht(en) im gewählten Zeitraum.")

    if s["wortliste_treffer"] > 0:
        add("wortliste", "Wortliste getroffen",
            f"{s['wortliste_treffer']} Nachricht(en) enthalten ein Wort aus deiner Liste. "
            "Es wurde nichts gelöscht und niemand bestraft — das ist nur ein Hinweis.")

    if s["emojis"] >= n * 1.5 and n >= 15:
        add("emoji_fan", "Emoji-Fan",
            f"Nutzt im Schnitt {round(s['emojis'] / n, 1)} Emojis pro Nachricht.")

    return tags


# --------------------------------------------------------------------------
# Der eigentliche Durchlauf
# --------------------------------------------------------------------------
async def scan_guild(guild: discord.Guild, channel_ids: list[str], range_key: str,
                     *, badwords: list[str] | None = None, pause_ms: int = 250,
                     max_per_channel: int = 0, progress=None, cancelled=None) -> dict:
    if range_key not in RANGES:
        raise ValueError(f"Unbekannter Zeitraum: {range_key}")

    tage = RANGES[range_key]
    nach = datetime.now(timezone.utc) - timedelta(days=tage) if tage else None
    muster = word_pattern(badwords or [])
    pause = max(0, pause_ms) / 1000.0

    pro_nutzer: dict[int, dict] = defaultdict(_blank_stats)
    kanal_zahlen: Counter = Counter()
    kanal_namen: dict[str, str] = {}
    antwort_netz: Counter = Counter()
    heatmap = [[0] * 24 for _ in range(7)]
    nachrichten_gesamt = 0
    uebersprungen: list[dict] = []
    abgebrochen = False

    kanaele = []
    for raw in channel_ids:
        kanal = guild.get_channel(int(raw))
        if kanal is None:
            uebersprungen.append({"channel_id": str(raw), "grund": "Kanal nicht gefunden."})
        elif not isinstance(kanal, (discord.TextChannel, discord.ForumChannel)):
            uebersprungen.append({"channel_id": str(raw), "grund": "Kein Textkanal."})
        elif not kanal.permissions_for(guild.me).read_message_history:
            uebersprungen.append({"channel_id": str(raw), "name": kanal.name,
                                  "grund": "Der Bot darf den Verlauf dieses Kanals nicht lesen."})
        else:
            kanaele.append(kanal)

    for index, kanal in enumerate(kanaele, start=1):
        if cancelled is not None and await cancelled():
            abgebrochen = True
            break
        kanal_namen[str(kanal.id)] = kanal.name
        gelesen = 0
        try:
            async for message in kanal.history(limit=max_per_channel or None, after=nach,
                                               oldest_first=False):
                if message.author.bot:
                    continue
                _absorb(message, pro_nutzer, antwort_netz, heatmap, muster, kanal)
                kanal_zahlen[str(kanal.id)] += 1
                nachrichten_gesamt += 1
                gelesen += 1

                # Häppchenweise: kurz Luft holen, damit der Bot bedienbar bleibt
                # und Discords Anfragebremse nicht greift.
                if gelesen % 100 == 0:
                    if cancelled is not None and await cancelled():
                        abgebrochen = True
                        break
                    if progress is not None:
                        await progress(index - 1, len(kanaele),
                                       f"{kanal.name}: {gelesen} Nachrichten")
                    if pause:
                        await asyncio.sleep(pause)
        except discord.Forbidden:
            uebersprungen.append({"channel_id": str(kanal.id), "name": kanal.name,
                                  "grund": "Discord hat den Zugriff verweigert."})
        except discord.HTTPException as exc:
            logging.warning("Verlauf von #%s nicht vollständig lesbar: %s", kanal.name, exc)
            uebersprungen.append({"channel_id": str(kanal.id), "name": kanal.name,
                                  "grund": f"Discord meldet: {exc}"})

        if progress is not None:
            await progress(index, len(kanaele), f"{kanal.name} fertig")
        if abgebrochen:
            break

    # --- Mitglieder ohne eine einzige Nachricht sind auch ein Ergebnis ---
    for member in guild.members:
        if not member.bot and member.id not in pro_nutzer:
            pro_nutzer[member.id] = _blank_stats()

    fertige = {uid: _finalize(s) for uid, s in pro_nutzer.items()}
    mittel = _averages(fertige)

    profile = []
    for uid, stats in fertige.items():
        member = guild.get_member(uid)
        tags = derive_tags(stats, mittel)
        profile.append({
            "user_id": str(uid),
            "stats": {**stats, "name": member.display_name if member else None},
            "tags": tags,
        })

    zusammenfassung = {
        "nachrichten": nachrichten_gesamt,
        "mitglieder": len(profile),
        "kanaele": [{"id": cid, "name": kanal_namen.get(cid, cid), "nachrichten": n}
                    for cid, n in kanal_zahlen.most_common()],
        "uebersprungene_kanaele": uebersprungen,
        "heatmap": heatmap,
        "antwort_netz": [{"von": a, "an": b, "anzahl": n}
                         for (a, b), n in antwort_netz.most_common(50)],
        "durchschnitt": mittel,
        "abgebrochen": abgebrochen,
        "zeitraum": range_key,
    }
    return {"profile": profile, "zusammenfassung": zusammenfassung,
            "nachrichten": nachrichten_gesamt, "abgebrochen": abgebrochen}


def _absorb(message: discord.Message, pro_nutzer: dict, antwort_netz: Counter,
            heatmap: list, muster: re.Pattern | None, kanal) -> None:
    """Eine Nachricht auswerten. Der Text verlässt diese Funktion nicht."""
    s = pro_nutzer[message.author.id]
    text = message.content or ""
    zeit = message.created_at

    s["nachrichten"] += 1
    s["zeichen"] += len(text)
    s["woerter"] += len(text.split())
    s["kanaele"].add(str(kanal.id))
    s["stunden"][zeit.hour] += 1
    s["wochentage"][zeit.weekday()] += 1
    heatmap[zeit.weekday()][zeit.hour] += 1
    if zeit.hour in NIGHT_HOURS:
        s["nachts"] += 1

    stempel = zeit.isoformat()
    if s["erste_nachricht"] is None or stempel < s["erste_nachricht"]:
        s["erste_nachricht"] = stempel
    if s["letzte_nachricht"] is None or stempel > s["letzte_nachricht"]:
        s["letzte_nachricht"] = stempel

    # Anhänge und Einbettungen
    for anhang in message.attachments:
        name = (anhang.filename or "").lower()
        if name.endswith(IMAGE_SUFFIXES):
            s["bilder"] += 1
            if name.endswith(".gif"):
                s["gifs"] += 1
        elif name.endswith(VIDEO_SUFFIXES):
            s["videos"] += 1
        else:
            s["dateien"] += 1
    for einbettung in message.embeds:
        if einbettung.type in ("gifv", "image"):
            s["gifs" if einbettung.type == "gifv" else "bilder"] += 1

    # Links
    for host in LINK.findall(text):
        s["links"] += 1
        if any(g in host.lower() for g in GIF_HOSTS):
            s["gifs"] += 1

    # Emojis
    s["emojis"] += len(CUSTOM_EMOJI.findall(text)) + len(UNICODE_EMOJI.findall(text))

    # Fragen und Antworten
    ist_frage = text.rstrip().endswith("?") or bool(QUESTION_WORDS.search(text))
    if ist_frage:
        s["fragen"] += 1
    if message.reference and message.reference.resolved is not None:
        bezug = message.reference.resolved
        if isinstance(bezug, discord.Message) and not bezug.author.bot:
            s["antworten_gegeben"] += 1
            pro_nutzer[bezug.author.id]["antworten_erhalten"] += 1
            antwort_netz[(str(message.author.id), str(bezug.author.id))] += 1
            bezugstext = bezug.content or ""
            if bezugstext.rstrip().endswith("?") or QUESTION_WORDS.search(bezugstext):
                s["hilfe_antworten"] += 1

    s["erwaehnungen_gemacht"] += len(message.mentions)
    if GREETING.search(text) and (message.mentions or "@" in text):
        s["begruessungen"] += 1

    # Reaktionen, die diese Nachricht bekommen hat
    for reaktion in message.reactions:
        anzahl = reaktion.count or 0
        s["reaktionen_erhalten"] += anzahl
        zeichen = str(reaktion.emoji)
        if zeichen in LAUGH_EMOJI:
            s["lach_reaktionen"] += anzahl
        elif zeichen in HEART_EMOJI:
            s["herz_reaktionen"] += anzahl

    if muster is not None and muster.search(text):
        s["wortliste_treffer"] += 1


def _averages(fertige: dict[int, dict]) -> dict:
    aktive = [s for s in fertige.values() if s["nachrichten"] > 0]
    if not aktive:
        return {"nachrichten": 0, "gif_anteil": 0.0, "bild_anteil": 0.0,
                "lacher_pro_nachricht": 0.0, "laenge_schnitt": 0.0}
    anzahl = len(aktive)
    return {
        "nachrichten": round(sum(s["nachrichten"] for s in aktive) / anzahl, 1),
        "gif_anteil": round(sum(s["gif_anteil"] for s in aktive) / anzahl, 1),
        "bild_anteil": round(sum(s["bild_anteil"] for s in aktive) / anzahl, 1),
        "lacher_pro_nachricht": round(sum(s["lacher_pro_nachricht"] for s in aktive) / anzahl, 2),
        "laenge_schnitt": round(sum(s["laenge_schnitt"] for s in aktive) / anzahl, 1),
        "aktive_mitglieder": anzahl,
    }
