# 🃏 Update v2.3.5 — Mission-Belohnungen, Kampf-Fix, Cooldown-Anzeige & AFK

Dieses Update bringt neue Mission-Belohnungsregeln, behebt einen Kampf-Anzeige-Bug, macht Cooldowns klarer sichtbar und erweitert die AFK-Markierung auf Missionen.

## 💎 Mission-Belohnungen neu geregelt
- **Nach Welle 1–3** (alle drei Wellen vor dem Boss geschafft) bekommst du jetzt **sofort 1 Infinitydust + 1 Unit** – direkt in der Pause nach Welle 3, **auch wenn du danach am Boss verlierst**.
- **Boss:** Es gibt **nur dann 1 Infinitydust**, wenn die Belohnungs-Karte **bereits in deinem Besitz** war (Duplikat). Ist die Karte neu, bekommst du die Karte – aber keinen zusätzlichen Boss-Dust.
- Einstellbar bleibt alles über `mission_dust_config.py` (pro Welle an/aus + Menge). Die doppelte Verrechnung des Duplikat-Bonus wurde dabei entfernt.

## 🐛 Kampf-Anzeige-Bug behoben (Missionen)
- Während der kurzen **Gegner-Spotlight-Phase** (Bot-Karte groß, Bot-Attacken sichtbar) konnte man fälschlich einen Button drücken und damit die **eigene** Attacke auslösen. Aktionen sind jetzt **nur noch im eigenen Zug** möglich; die Buttons des Gegnerzugs sind zuverlässig gesperrt.
- Die Spieler-Zug-Ansicht zeigt jetzt konsequent **deine** Karte + **deine** Fähigkeiten.

## 🕒 Cooldown-Anzeige verbessert
- Cooldowns stehen jetzt **nach dem Schaden** im Format `{nCD}`, z. B. `Gamma-Eruption — 26-35 Schaden {6CD}` – für **deine** Fähigkeiten **und** die von Gegnern/Bossen/Wellen.
- **Neu:** Im eigenen Zug siehst du ein Feld **„🛡️ Gegner-Cooldowns"**, das zeigt, welche Spezialfähigkeiten des Gegners gerade **bereit** sind oder **noch X Züge** brauchen.

## ⚡ Boss-Spezialfähigkeiten im Log hervorgehoben
- Wenn ein Gegner/Boss eine Spezialfähigkeit (Heilung, Sperre, Schild, Konter …) einsetzt, erscheint jetzt eine **eigene, fett markierte Zeile** im Kampf-Log: `⚡ **Name** — was passiert ist`.

## 🖼️ Karten-Detail (`/sammlung` & `/sammlung-ansehen`)
- Beim Ansehen einer Karte stehen jetzt **oben die Werte**: ❤️ **Leben** (inkl. Aufwertungen aus `/verbessern`) und ✨ **Seltenheit**. Attacken zeigen zusätzlich ihren Cooldown im `{nCD}`-Format.

## ⚔️ Boss- & Wellen-Balance (Feinschliff)
- **Green Goblin – Gleiter-Ramme:** Schaden **20 → 14**.
- **Green-Goblin-Welle 3 – Hitzesuchende Rakete:** **30 → 24–30**.
- **Agatha-Welle 3 – Höllenfeuer-Stoß:** **30 → 24**.
- **Kingpin – Bestechungs-Versuch:** Heilung deutlich abgeschwächt: **20 HP** (wenn du zuvor 0 Schaden gemacht hast) bzw. **15 HP** sonst (vorher 30/35).
- *Hinweis:* Der Großteil deiner Test-Notizen (Maestro komplett, M.O.D.O.K., Kingpin „Zermalmender Griff" 26/38, die 14–18/20–24-Ranges der dritten Lakeien) war **bereits in v2.3.0** umgesetzt und ist unverändert geblieben.

## 🔨 `/verbessern`
- Die **günstigste Aufwertung kostet 5 Infinitydust** (1×-Stufe). Die veraltete „10er"-Konstante wurde vereinheitlicht.

## ⏰ AFK-Markierung jetzt auch in Missionen
- Bleibst du in einem **Missions-Thread** zu lange inaktiv, wirst du jetzt – wie bei PvP-Kämpfen – per Ping erinnert. Der Timer wird bei jeder Aktion zurückgesetzt und beim Mission-Ende/Abbruch entfernt.

---

### 🔧 Technisch
- `mission_dust_config.py`: neue Standardwerte (Welle 3 = 1 Dust, Rest 0; Duplikat-Akkumulator-Bonus aus, da der Duplikat-Dust bereits in `check_and_add_karte` vergeben wird).
- `services/battle.py`: `_format_attack_label` nutzt `{nCD}`. `render_boss_special_activation` ist jetzt im Bot-Zug **verdrahtet** (war zuvor nur definiert).
- `services/afk_tracker.py`: neuer `kind="mission"` + `create_mission_state`, Empfänger-Dedupe.
- `bot.py`: Turn-Guard in `execute_attack` (`_mission_actor_turn`), vereinheitlichte Spieler-Zug-Embeds via `create_current_embed`, Gegner-Cooldown-Feld, Interlude-Dust-Auszahlung.
- Tests aktualisiert/erweitert: `test_mission_rewards`, `test_cooldown_display`, `test_combat_rules`. Gesamtsuite grün (366 passed).
