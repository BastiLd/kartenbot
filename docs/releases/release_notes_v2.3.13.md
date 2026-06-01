# 🗂️ Update v2.3.13 — Code-Audit: Datensicherheit, Balance & Aufräumen

Ein größeres internes Audit über den gesamten Code. Spürbar für Spieler vor allem bei
der Balance; der Rest macht den Bot stabiler und wartbarer.

## 🛡️ Datenverlust & Bugs behoben
- **Guthaben kann nicht mehr doppelt/negativ werden:** Infinitydust- und Units-Abbuchung
  (z. B. bei schnellem Doppel-Klick in `/verbessern`) läuft jetzt atomar in der Datenbank.
- **DB-Schutz bei Absturz:** SQLite läuft im WAL-Modus – ein Crash mitten im Schreiben
  kann die Datenbank nicht mehr beschädigen.
- **Einladungs-Belohnungen konsistent:** Die Finalisierung einer Einladung läuft in einer
  Transaktion mit Rollback – keine halb gebuchten Zustände mehr.
- **Stille Fehler werden geloggt:** Beschädigte Sitzungsdaten und fehlerhafte Spawn-Config
  werden nicht mehr unbemerkt verschluckt.

## ⚔️ Balance & Attacken
- **Deadpool:** Spezial „Hex-Fluch" → **„Letzter Witz"** umbenannt (Name war doppelt mit
  Scarlet Witch). Finisher greift jetzt erst **unter 15 HP** (vorher 20).
- **Rocket „Das dicke Ding":** Schaden **42–52 → 38–48** (Rückstoß bleibt 15).
- **Wolverine „Berserkerwut":** Beschreibung korrigiert (zeigt jetzt die echten 22–28).
- **Thor „Ruf des Donners" 14 → 12–16** und **Human Torch „Feuerball" 12 → 10–14**
  (etwas Streuung statt starrem Wert).
- **Schaden-über-Zeit (Blutung/Brand/Gift):** Pro-Tick-Obergrenze von 999 auf 60 gesenkt
  (am aktuellen Spiel ändert sich nichts, schützt aber vor Ausreißern).

## 🧹 Aufräumen unter der Haube
- **34 identische Kampf-Methoden** aus PvP- und Missions-Kampf in einen gemeinsamen
  `BattleMechanicsMixin` zusammengeführt – **−568 doppelte Zeilen**.
- **Effekt-Helfer** (Status/DoT) in ein eigenes, getestetes Modul `services/effect_handler.py`
  ausgelagert.
- Mehrfach kopierte Kanal-Freigabe-Checks und Sende-Helfer zusammengefasst.

## ✅ Qualität
- **384 Tests grün** (vorher 373) – u. a. neue Tests für atomare Guthaben-Buchung
  (`tests/test_dust_atomicity.py`), die Effekt-Helfer (`tests/test_effect_handler.py`)
  und die Mixin-Auslagerung (`tests/test_battle_view_mixin.py`).
- Karten weiterhin valide (34), `bot.py` importiert sauber, **~760 Zeilen** Code netto entfernt.
