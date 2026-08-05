"""Tests für die Bot-Seite von Kartenbot Web.

Prüft die drei neuen Bausteine: Auftragswarteschlange, Rollen-Rangordnung und
den Verlaufs-Scanner. Discord wird dabei durch schlanke Attrappen ersetzt —
es geht um die Entscheidungslogik, nicht um die Bibliothek.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from services import history_scan


# --------------------------------------------------------------------------
# Wortliste
# --------------------------------------------------------------------------
def test_wortliste_trifft_nur_ganze_woerter():
    muster = history_scan.word_pattern(["mist", "bloed"])
    assert muster.search("das ist Mist")
    assert muster.search("BLOED!")
    # Teilwörter dürfen nicht anschlagen, sonst gäbe es dauernd Fehlalarme.
    assert not muster.search("Mistel")
    assert not muster.search("verbloedet")


def test_leere_wortliste_ergibt_kein_muster():
    assert history_scan.word_pattern([]) is None
    assert history_scan.word_pattern(["  ", ""]) is None


def test_wortliste_behandelt_sonderzeichen_als_text():
    # Ein Punkt darf nicht als "beliebiges Zeichen" wirken.
    muster = history_scan.word_pattern(["a.b"])
    assert muster.search("a.b")
    assert not muster.search("axb")


# --------------------------------------------------------------------------
# Einordnung der Mitglieder
# --------------------------------------------------------------------------
def _stats(**werte):
    basis = history_scan._finalize(history_scan._blank_stats())
    basis.update(werte)
    return basis


MITTEL = {"nachrichten": 20, "gif_anteil": 5.0, "bild_anteil": 10.0,
          "lacher_pro_nachricht": 0.1, "laenge_schnitt": 60.0}


def _keys(tags):
    return {t["key"] for t in tags}


def test_ohne_nachrichten_ist_man_stiller_mitleser():
    tags = history_scan.derive_tags(_stats(nachrichten=0), MITTEL)
    assert _keys(tags) == {"stiller_leser"}
    assert tags[0]["grund"]


def test_meme_typ_bei_hohem_gif_anteil():
    tags = history_scan.derive_tags(
        _stats(nachrichten=50, gif_anteil=40.0, bild_anteil=45.0), MITTEL)
    assert "meme_typ" in _keys(tags)


def test_lustig_braucht_ueberdurchschnittliche_lacher():
    knapp_drunter = history_scan.derive_tags(
        _stats(nachrichten=30, lacher_pro_nachricht=0.15), MITTEL)
    deutlich_drueber = history_scan.derive_tags(
        _stats(nachrichten=30, lacher_pro_nachricht=0.9), MITTEL)
    assert "lustig" not in _keys(knapp_drunter)
    assert "lustig" in _keys(deutlich_drueber)


def test_nachteule_bei_hohem_nachtanteil():
    tags = history_scan.derive_tags(_stats(nachrichten=40, nacht_anteil=45.0), MITTEL)
    assert "nachteule" in _keys(tags)


def test_wortlisten_treffer_wird_als_hinweis_markiert():
    tags = history_scan.derive_tags(_stats(nachrichten=10, wortliste_treffer=3), MITTEL)
    treffer = next(t for t in tags if t["key"] == "wortliste")
    # Der Text muss klarstellen, dass nichts bestraft wird.
    assert "bestraft" in treffer["grund"]


def test_jede_einordnung_hat_eine_begruendung():
    tags = history_scan.derive_tags(
        _stats(nachrichten=200, gif_anteil=40.0, bild_anteil=50.0, nacht_anteil=40.0,
               lacher_pro_nachricht=1.2, laenge_schnitt=200.0, antworten_gegeben=80,
               hilfe_antworten=20, begruessungen=5, emojis=400, wortliste_treffer=1),
        MITTEL)
    assert tags, "es sollte mindestens eine Einordnung geben"
    for tag in tags:
        assert tag["grund"].strip(), f"{tag['key']} hat keine Begründung"
        assert tag["label"].strip()


# --------------------------------------------------------------------------
# Auswertung einer einzelnen Nachricht
# --------------------------------------------------------------------------
class _Autor:
    def __init__(self, uid, bot=False):
        self.id = uid
        self.bot = bot


def _nachricht(uid=1, text="", *, anhaenge=(), reaktionen=(), bezug=None, erwaehnungen=(),
               stunde=12, wochentag=0):
    # Ein Montag zur gewünschten Stunde.
    zeit = datetime(2026, 8, 3, stunde, 0, tzinfo=timezone.utc) + timedelta(days=wochentag)
    return SimpleNamespace(
        author=_Autor(uid), content=text, created_at=zeit,
        attachments=list(anhaenge), embeds=[], reactions=list(reaktionen),
        reference=bezug, mentions=list(erwaehnungen),
    )


def _anhang(name):
    return SimpleNamespace(filename=name)


def _reaktion(emoji, anzahl):
    return SimpleNamespace(emoji=emoji, count=anzahl)


def _leer():
    from collections import Counter, defaultdict
    return defaultdict(history_scan._blank_stats), Counter(), [[0] * 24 for _ in range(7)]


def test_bilder_und_gifs_werden_getrennt_gezaehlt():
    nutzer, netz, heat = _leer()
    kanal = SimpleNamespace(id=99, name="allgemein")
    history_scan._absorb(_nachricht(1, anhaenge=[_anhang("katze.png")]), nutzer, netz, heat, None, kanal)
    history_scan._absorb(_nachricht(1, anhaenge=[_anhang("lol.gif")]), nutzer, netz, heat, None, kanal)
    history_scan._absorb(_nachricht(1, "schau mal https://tenor.com/view/abc"), nutzer, netz, heat, None, kanal)
    s = nutzer[1]
    assert s["bilder"] == 2          # png + gif zaehlen beide als Bild
    assert s["gifs"] == 2            # gif-Anhang + tenor-Link
    assert s["links"] == 1


def test_nachtstunden_werden_erkannt():
    nutzer, netz, heat = _leer()
    kanal = SimpleNamespace(id=1, name="nacht")
    history_scan._absorb(_nachricht(1, "hi", stunde=3), nutzer, netz, heat, None, kanal)
    history_scan._absorb(_nachricht(1, "hi", stunde=15), nutzer, netz, heat, None, kanal)
    assert nutzer[1]["nachts"] == 1
    assert heat[0][3] == 1 and heat[0][15] == 1


def test_lach_reaktionen_zaehlen_separat():
    nutzer, netz, heat = _leer()
    kanal = SimpleNamespace(id=1, name="spass")
    history_scan._absorb(
        _nachricht(1, "witz", reaktionen=[_reaktion("😂", 5), _reaktion("👍", 2)]),
        nutzer, netz, heat, None, kanal)
    s = nutzer[1]
    assert s["reaktionen_erhalten"] == 7
    assert s["lach_reaktionen"] == 5
    assert s["herz_reaktionen"] == 2


class _BezugsNachricht(discord.Message):
    """Attrappe, die den isinstance-Test in _absorb besteht.

    Die Unterklasse bekommt ein eigenes __dict__, deshalb lassen sich die
    beiden Felder setzen, die ausgewertet werden — ohne dass discord.py
    tatsächlich eine Nachricht aufbauen müsste.
    """

    def __init__(self, uid, text):          # noqa: D107 - bewusst ohne super()
        self.author = _Autor(uid)
        self.content = text


def test_antwort_auf_frage_zaehlt_als_hilfe():
    nutzer, netz, heat = _leer()
    kanal = SimpleNamespace(id=1, name="hilfe")
    bezug = SimpleNamespace(resolved=_BezugsNachricht(2, "Wie geht das denn?"))
    history_scan._absorb(_nachricht(1, "so und so", bezug=bezug), nutzer, netz, heat, None, kanal)
    assert nutzer[1]["antworten_gegeben"] == 1
    assert nutzer[1]["hilfe_antworten"] == 1
    assert nutzer[2]["antworten_erhalten"] == 1
    assert netz[("1", "2")] == 1


def test_antwort_ohne_frage_zaehlt_nicht_als_hilfe():
    nutzer, netz, heat = _leer()
    kanal = SimpleNamespace(id=1, name="plausch")
    bezug = SimpleNamespace(resolved=_BezugsNachricht(2, "ich geh jetzt schlafen"))
    history_scan._absorb(_nachricht(1, "gute nacht"), nutzer, netz, heat, None, kanal)
    history_scan._absorb(_nachricht(1, "bis morgen", bezug=bezug), nutzer, netz, heat, None, kanal)
    assert nutzer[1]["antworten_gegeben"] == 1
    assert nutzer[1]["hilfe_antworten"] == 0


def test_bot_nachrichten_werden_im_scan_ignoriert():
    # Der Filter sitzt in scan_guild; hier wird nur festgehalten, dass ein Bot
    # als solcher erkennbar ist — sonst würden Bot-Posts die Statistik verzerren.
    assert _Autor(1, bot=True).bot is True
    assert _Autor(1).bot is False


def test_finalize_rechnet_anteile_korrekt():
    roh = history_scan._blank_stats()
    roh["nachrichten"] = 10
    roh["gifs"] = 4
    roh["bilder"] = 1
    roh["nachts"] = 2
    roh["zeichen"] = 500
    fertig = history_scan._finalize(roh)
    assert fertig["gif_anteil"] == 40.0
    assert fertig["bild_anteil"] == 50.0       # (bilder + gifs) / nachrichten
    assert fertig["nacht_anteil"] == 20.0
    assert fertig["laenge_schnitt"] == 50.0
    assert isinstance(fertig["kanaele"], int)  # Menge wird zu Anzahl


def test_finalize_teilt_nie_durch_null():
    fertig = history_scan._finalize(history_scan._blank_stats())
    assert fertig["gif_anteil"] == 0.0
    assert fertig["laenge_schnitt"] == 0.0


# --------------------------------------------------------------------------
# Foren: haben selbst keinen Verlauf, nur ihre Beitraege
# --------------------------------------------------------------------------
def test_forumkanal_hat_wirklich_kein_history():
    """Grundlage des Fehlers: discord.py bietet fuer Foren kein history() an.
    Faellt dieser Test, hat sich die Bibliothek geaendert."""
    assert not hasattr(discord.ForumChannel, "history")
    assert hasattr(discord.TextChannel, "history")
    assert hasattr(discord.Thread, "history")


class _FakeForum(discord.ForumChannel):
    # threads ist in discord.py eine schreibgeschuetzte Eigenschaft, deshalb
    # wird sie hier ueberschrieben statt zugewiesen.
    def __init__(self, threads, archiviert=()):
        self.id = 42
        self.name = "forum"
        self._threads = list(threads)
        self._archiviert = list(archiviert)

    @property
    def threads(self):
        return list(self._threads)

    def archived_threads(self, limit=None):
        eintraege = self._archiviert

        class _Iter:
            def __aiter__(self_inner):
                self_inner._i = iter(eintraege)
                return self_inner

            async def __anext__(self_inner):
                try:
                    return next(self_inner._i)
                except StopIteration:
                    raise StopAsyncIteration
        return _Iter()


def _lauf(coro):
    import asyncio
    return asyncio.run(coro)


def test_forum_wird_in_seine_beitraege_aufgeloest():
    forum = _FakeForum(threads=['laufend1', 'laufend2'], archiviert=['alt1'])
    quellen = _lauf(history_scan._lesbare_quellen(forum))
    # Das Forum selbst darf NICHT dabei sein - es hat keinen Verlauf.
    assert forum not in quellen
    assert quellen == ['laufend1', 'laufend2', 'alt1']


def test_forum_ohne_zugriff_auf_archiv_liefert_wenigstens_die_laufenden():
    class _Gesperrt(_FakeForum):
        def archived_threads(self, limit=None):
            class _Iter:
                def __aiter__(self_inner): return self_inner
                async def __anext__(self_inner):
                    raise discord.Forbidden(_Antwort(403), "keine Rechte")
            return _Iter()

    forum = _Gesperrt(threads=['laufend1'])
    assert _lauf(history_scan._lesbare_quellen(forum)) == ['laufend1']


class _Antwort:
    """Minimale Attrappe fuer die HTTP-Antwort, die discord.Forbidden erwartet."""
    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"


def test_textkanal_ist_selbst_die_quelle_und_nimmt_threads_mit():
    kanal = SimpleNamespace(id=1, name="allgemein", threads=['thread-a', 'thread-b'])
    quellen = _lauf(history_scan._lesbare_quellen(kanal))
    assert quellen[0] is kanal
    assert quellen[1:] == ['thread-a', 'thread-b']


def test_textkanal_ohne_threads_funktioniert_auch():
    kanal = SimpleNamespace(id=1, name="still")
    assert _lauf(history_scan._lesbare_quellen(kanal)) == [kanal]


# --------------------------------------------------------------------------
# Rollen-Rangordnung
# --------------------------------------------------------------------------
class _Rolle:
    def __init__(self, name, position, managed=False, default=False):
        self.name = name
        self.position = position
        self.managed = managed
        self._default = default

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


class _Rechte:
    def __init__(self, manage_roles=True):
        self.manage_roles = manage_roles


class _Guild:
    def __init__(self, me):
        self.me = me


def _bot_member(top_position, darf=True):
    return SimpleNamespace(top_role=_Rolle("Bot", top_position),
                           guild_permissions=_Rechte(darf))


def test_rolle_ueber_dem_bot_wird_abgelehnt():
    from services import role_manager
    guild = _Guild(_bot_member(10))
    grund = role_manager.role_blocked_reason(guild, _Rolle("Admin", 20))
    assert grund is not None and "über" in grund


def test_rolle_auf_gleicher_hoehe_wird_abgelehnt():
    from services import role_manager
    guild = _Guild(_bot_member(10))
    # Discord erlaubt auch die gleiche Position nicht.
    assert role_manager.role_blocked_reason(guild, _Rolle("Gleich", 10)) is not None


def test_rolle_unter_dem_bot_ist_erlaubt():
    from services import role_manager
    guild = _Guild(_bot_member(10))
    assert role_manager.role_blocked_reason(guild, _Rolle("Mitglied", 5)) is None


def test_ohne_recht_geht_gar_nichts():
    from services import role_manager
    guild = _Guild(_bot_member(10, darf=False))
    grund = role_manager.role_blocked_reason(guild, _Rolle("Mitglied", 5))
    assert grund is not None and "Rollen verwalten" in grund


def test_everyone_und_app_rollen_werden_abgelehnt():
    from services import role_manager
    guild = _Guild(_bot_member(10))
    assert role_manager.role_blocked_reason(guild, _Rolle("@everyone", 0, default=True))
    assert role_manager.role_blocked_reason(guild, _Rolle("Nitro", 1, managed=True))


def test_ohne_bot_mitglied_wird_abgelehnt():
    from services import role_manager
    assert role_manager.role_blocked_reason(_Guild(None), _Rolle("X", 1)) is not None
