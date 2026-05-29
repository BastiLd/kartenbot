# Manuelle Test-Checkliste v2.3.0

Visuelle / Verhaltens-Prüfungen, die nicht durch automatische Tests abgedeckt sind.

## Thumbnail-Verhalten (Req. 8)
- [ ] **Daily** mit bereits besessener Karte → Dust-Bild erscheint nur als Thumbnail (oben rechts), kein großes Bild.
- [ ] **Mission-Reward** (Karte bereits besessen) → Dust nur als Thumbnail.
- [ ] **Mission-Reward** (neue Karte) → Karten-Bild groß ist weiterhin gewollt.
- [ ] **/karte-geben** Bestätigung → kein großes Dust-/Unit-Bild.
- [ ] **Dev-Tools** (Grant Card / Dust geben) → Dust/Unit nur als Thumbnail.
- [ ] **Lödust** → Dust nur als Thumbnail.
- [ ] **Kampf** → Spieler-Kartenbild bleibt groß, Gegner-Karte als Thumbnail (unverändert).

## Cooldown-Anzeige (Req. 14)
- [ ] Fähigkeit ohne Cooldown → kein Suffix.
- [ ] Verfügbare Fähigkeit mit Cooldown → Suffix `(<n>CD)`.
- [ ] Fähigkeit auf Cooldown → ausgegraut, kein Suffix.

## Boss-Spezial-Hervorhebung (Req. 15)
- [ ] Jede Boss-Spezialfähigkeit erscheint als `⚡ **Name** — Effekt`.

## Mode-Confirm (Req. 10)
- [ ] Maintenance/Beta/Alpha aktivieren → Dialog zeigt „… ist aktuell **NICHT AKTIV** → wird **AKTIVIERT**".
- [ ] Deaktivieren → Dialog zeigt „… ist aktuell **AKTIV** → wird **DEAKTIVIERT**".
- [ ] Abbrechen → Status bleibt unverändert.

## Boss-Balance Stichproben (Req. 16–20)
- [ ] Maestro Gamma-Eruption verursacht 26–35.
- [ ] Kingpin Zermalmender Griff: 26 bei Ziel ≥ 60 HP, 38 darunter.
- [ ] Kingpin Bestechungs-Versuch: 30 nach 0-Schaden-Runde, sonst 35.
- [ ] MODOK Berechnete Heilung: 15 normal, 30 nach Spezial-Nutzung.
- [ ] Agatha Darkhold-Fluch: nächste Heilung des Spielers = 0 HP.

## Noch offen (separat zu implementieren, siehe tasks.md)
- [ ] Boss-Karten-Wechsel: Frisch-Start (HP voll / Cooldowns leer) + `(aktuell)`-Marker + Toggle-Gate.
- [ ] Infinitydust-Akkumulator in den Mission-Flow eingehängt (Lakei/Boss/Daily, commit/discard).
- [ ] Cancel-Buttons (Challenge + Kampf).
- [ ] AFK-Ticker-Loop + Lifecycle-Verdrahtung (create/on_action/delete).
