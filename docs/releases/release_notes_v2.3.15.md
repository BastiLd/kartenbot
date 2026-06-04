# 🐛 Update v2.3.15 — Kampf-Crashes & Mechanik-Fixes (Community-Bugs)

Mehrere von der Community gemeldete Bugs sind behoben: zwei harte Kampf-Abbrüche, ein
unbenutzbarer Sammlungs-Befehl und zwei falsch laufende Kampf-Mechaniken.

## 💥 Behobene Abstürze
- **Giant-Man & Co. brechen den Kampf nicht mehr ab.** Angriffe mit „Nächster
  Standardangriff macht X Schaden" (z. B. Ant-Mans *Giant-Man*) liefen in einen
  internen Fehler (`mult`-Variable). Läuft jetzt sauber.
- **Venom Blast crasht nicht mehr.** Fähigkeiten mit einem Schadens-Bereich als
  Zusatzeffekt (z. B. Miles' *Venom Blast* mit 8–12 Selbstschaden) lösten einen
  `int()`-Fehler aus. Bereiche werden jetzt korrekt ausgewürfelt.
- **`/sammlung ansehen` funktioniert wieder.** Bei großen Sammlungen sprengte die
  Karten-Liste Discords Limit von 25 Embed-Feldern. Die Liste wird jetzt begrenzt und
  zeigt einen Hinweis „… und N weitere Helden".

## ⚔️ Mechanik-Fixes
- **Sternenflug weicht jetzt zuverlässig aus.** War gleichzeitig ein Schutz wie
  Captain Marvels *Energie-Absorber* aktiv, „verbrauchte" dieser das Ausweichen und der
  Angriff traf trotzdem. **Ausweichen gewinnt jetzt immer** — der nächste gegnerische
  Angriff verfehlt, der Schutz bleibt für später erhalten.
- **Mega Venom Blast sperrt jetzt wirklich.** Die Selbst-Sperre („Miles' Spezialangriffe
  1 Runde lang gesperrt") wurde zuvor im selben Zug sofort wieder aufgehoben und griff
  nie. Außerdem behauptete die Meldung fälschlich, die **gegnerischen** Fähigkeiten seien
  gesperrt. Die Sperre wirkt nun korrekt und die Meldung nennt die tatsächlich
  betroffene Karte.

## ✅ Qualität
- **396 Tests grün** (+ 428 Subtests) — inkl. neuer Regressionstests für Giant-Man,
  Venom Blast, die Mega-Venom-Sperre und die Ausweichen-Priorität.
