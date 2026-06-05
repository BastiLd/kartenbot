# ⚖️ Update v2.3.17 — Kampf-Werte an Kartentexte angeglichen (Community-Bugs)

Vier von der Community gemeldete Ungereimtheiten zwischen Kartentext und tatsächlichem Verhalten
sind behoben — vor allem im Missions-/Bot-Kampf.

## 💥 Venom Blast wirkt jetzt auch in Missionen
- Miles' **Venom Blast** ließ den Gegner laut Text „beim nächsten Angriff 8-12 Selbstschaden"
  erleiden — das funktionierte bisher **nur im PvP-Spielerzug**. In Missionen und im Bot-Zug wurde der
  Effekt weder gesetzt noch ausgelöst. Jetzt greift er in **allen** Kampf-Varianten korrekt.

## 🔢 Feste Werte sind jetzt wirklich fest
- **She-Hulk „Einspruch!"** („14 feste Verfahrenskosten") und **Shang-Chi „Fünf-Finger-Explosion"**
  („Der Gegner macht 14 Schaden weniger") hatten intern einen Zufallsbereich (12-16) hinterlegt, der
  mal 15 oder 16 ergab. Beide sind jetzt **fest 14**, wie im Text beschrieben.

## 🎯 Shang-Chi „Zehn-Ringe-Wucht" trifft verlässlich 3-5 Mal
- Der Text sagt „trifft 3-5 Mal", technisch waren aber 0-5 Treffer möglich (jeder Versuch nur 80 %) —
  daher kamen teils nur 2 Treffer. Jetzt sind **mindestens 3 Treffer garantiert** (3-5), und die
  angezeigte Schadensspanne stimmt mit **21-45** überein.

## ✅ Qualität
- **401 Tests grün** (+ 428 Subtests) — inkl. neuer Regressionstests für den Missions-Venom-Blast,
  die festen 14er-Werte und die garantierten 3-5 Treffer.
