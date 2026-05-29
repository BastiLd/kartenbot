# UI-Audit: `/dust` (+ `/lödust`) gegen `/karte-geben`

Stand: v2.3.0-Branch, vor Task 4.2.
Ziel: Dokumentation der UI-Lücke zwischen dem Karten-Vergabeflow und dem Infinitydust-Vergabeflow als Grundlage für Task 4.2 (Multi-Modus angleichen) und Task 4.3 (Lödust verifizieren).

## Code-Anker

| Komponente | Datei | Zeile |
|---|---|---|
| `/karte-geben` Slash-Handler (`give`) | `botcommands/admin_commands.py` | 249–455 |
| `/dust` Slash-Handler (`dust`) | `botcommands/admin_commands.py` | 456–475 |
| `/lödust` Slash-Handler (`loedust`) | `botcommands/admin_commands.py` | 476–495 |
| `run_dust_command_flow(...)` | `bot.py` | 14948–15050 |
| `_post_dust_result_message(...)` | `bot.py` | 14909–14946 |
| `_select_number(...)` Helper | `bot.py` | 14842–14846 |
| `NumberInputModal` (Custom-Betrag) | `bot.py` | 14854–14878 |
| `NumberSelectView` (Quick-Pick Dropdown) | `bot.py` | 14880–14903 |
| `DUST_MENU_AMOUNTS` Konstante | `bot.py` | 191 |
| `AdminUserSelectView` | `bot.py` | 7226 |
| `DustMultiUserSelectView` | `bot.py` | 7295–7437 |
| `MultiCardSelectView` | `bot.py` | 15180 |
| `GiveCardConfirmView` | `bot.py` | 15446 |
| Dev-Panel `give_dust` Action | `bot.py` | 16125–16151 |

`DUST_MENU_AMOUNTS` ist heute:

```python
DUST_MENU_AMOUNTS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 70, 100]   # bot.py:191
```

## Strukturelle Gleichheiten

1. **Slash-Command-Parameter `modus`** mit identischer `app_commands.Choice`-Liste `[single, multi]` in beiden Handlern (`admin_commands.py:252–256` für `/karte-geben`, `:459–463` für `/dust`, `:479–483` für `/lödust`).
2. **Initiales `defer(ephemeral=True)`** vor jeder UI-Stufe (`admin_commands.py:271`, `:474`, `:494`).
3. **Single-Pfad-Nutzerauswahl**: beide Flows verwenden `module.AdminUserSelectView(interaction.user.id, interaction.guild)` (`admin_commands.py:286` und `bot.py:14971`).
4. **Multi-Pfad-Nutzerauswahl**: beide Flows verwenden `module.DustMultiUserSelectView(...)` mit `bind_message`-Pattern, identischem Embed-Header („💎 Multi-Auswahl für …" wenn `item_label == "Infinitydust"`, sonst nur „Multi-Auswahl für …", `bot.py:7344–7358`).
5. **Timeout-Verhalten**: beide brechen mit ⏰-Meldung „Keine Auswahl getroffen. Abgebrochen." ab, wenn `view.value` leer bleibt (`admin_commands.py:294–298`, `:316–318` und `bot.py:14981–14988`, `:15000–15004`).
6. **Audit-Logging**: beide schreiben in derselben Reihenfolge `log_admin_dust_action` / DB-Writes pro Ziel-User (Karten direkt in `add_exact_card_variant_once`, Dust via `add_infinitydust` / `remove_infinitydust` und expliziten `log_admin_dust_action`-Aufruf in `bot.py:15022–15029`).

Bis einschließlich der Nutzer-Auswahl sind die beiden Flows damit funktional identisch. Erst danach driften sie auseinander.

## Abweichungen (sollten angepasst werden)

### A. Mengen-/Inhalt-Auswahl (zentraler Unterschied für Task 4.2)

| Aspekt | `/karte-geben` | `/dust` (`run_dust_command_flow`) |
|---|---|---|
| View | `MultiCardSelectView` mit Status/Fertig/Neustart-Buttons (`bot.py:15180`) | `NumberSelectView` als Dropdown (`bot.py:14880`), aufgerufen über `_select_number` (`bot.py:15003–15007`) |
| Auswahl-Werte | dynamisch (Karten-Liste, paginiert) | Fixliste `DUST_MENU_AMOUNTS = [5,10,15,20,25,30,35,40,45,50,70,100]` (`bot.py:191`) |
| Custom-Eingabe | nicht zutreffend | erste Dropdown-Option `Eigener Wert...` (`bot.py:14887`) öffnet `NumberInputModal` (`bot.py:14854–14878`); akzeptiert `raw_value.isdigit() and int(raw_value) > 0`, `max_length=9` |
| UI-Form | Multi-Step, Buttons + Select | Single-Select-Dropdown |

Implikationen für Task 4.2:
- Die Quick-Pick-Werte stimmen **nicht** mit der Soll-Liste `{5,10,15,20,25,30}` aus Req. 4.3/5.2 überein — `35,40,45,50,70,100` müssen entfernt werden.
- Es gibt schon **einen** Custom-Amount-Modal (`NumberInputModal`), aber er ist als Dropdown-Option erreichbar, nicht als prominente Top-Schaltfläche, und prüft die Obergrenze `1.000.000` aus Req. 5.4 nicht (`max_length=9` erlaubt bis 999.999.999).
- Heutiges UI ist ein **Select**, kein Button-Grid; der Spec verlangt für den Multi-Modus „Schnellauswahl-**Buttons**".
- Validierung „Eingabe `0` + aktiver Schnellauswahl-Button → Schnellauswahl gewinnt" lässt sich im aktuellen Dropdown-Modell gar nicht ausdrücken — dafür braucht es das Button-Grid + parallel öffenbares Modal.

### B. Bestätigungs-Schritt fehlt bei Dust

| Aspekt | `/karte-geben` | `/dust` |
|---|---|---|
| Confirm-View | `GiveCardConfirmView` (`bot.py:15446`) wird im Multi-Modus zwingend gezeigt (`admin_commands.py:340–369`) | keine Bestätigung; nach Mengenwahl wird sofort ausgezahlt (`bot.py:15013–15029`) |
| Bestätigungs-Embed | Titel „📝 Bestätigung: Karten verteilen", Color `0xF1C40F`, Footer „Mit ✅ jetzt verteilen, ❌ abbrechen." | nicht vorhanden |
| Timeout-Pfad | „⏰ Zeit abgelaufen. Vergabe abgebrochen." (`admin_commands.py:367`) | nicht vorhanden |

Da Req. 4.1 explizit fordert, dass „Dust geben" sich UI-seitig wie `/karte-geben` verhält, müsste der Dust-Multi-Modus ebenfalls eine `GiveCardConfirmView`-äquivalente Confirm-Stufe bekommen. Im Single-Modus zeigt auch `/karte-geben` keine Confirm-View (Parität gegeben).

### C. Ergebnis-Embed-Struktur

| Aspekt | `/karte-geben` | `/dust` (`_post_dust_result_message`) |
|---|---|---|
| Titel | `🎁 Karten vergeben` (`admin_commands.py:413`) | `💎 Infinitydust vergeben` bzw. `💎 Infinitydust entfernt` (`bot.py:14921`) |
| Color | dynamisch: `0xE74C3C` (rot) bei `failed>0`, `0x2ECC71` (grün) bei `added>0`, sonst `0xE67E22` (orange) (`admin_commands.py:404–410`); bei Single+1 Karte zusätzlich Rarity-Override | statisch: `0x2ECC71` (grün) für Give, `0xD64B4B` (rot) für Remove (`bot.py:14935`) — keine Drei-Bucket-Logik |
| Description | „{actor} hat **N Karte(n)** an **M Nutzer** verteilt." (`admin_commands.py:415`) | Aktor-Zeile + „Modus: **{mode}**" + Liste „• {target}: **{n}x** erhalten/entfernt" (`bot.py:14924–14933`) |
| Pro-User-Buckets | drei Buckets `✅ hinzugefügt:`, `⚠️ bereits vorhanden:`, `❌ fehlgeschlagen:` (`admin_commands.py:431–434`); Übersichts-Field-Name enthält Zähler `(✅ N · ⚠️ M · ❌ K)` | nur ein einzelner Wert pro User (`applied_amount`); kein Erfolg/Teilfehler/Skip-Bucket sichtbar |
| Single-Karte-Bonus | bei genau 1 User + 1 Karte: `set_image(card.bild)` + Rarity-Color (`admin_commands.py:447–454`) | nicht vorhanden; immer Thumbnail des Infinitydust-Items |
| Versand | `_send_with_visibility(interaction, visibility_key, embed=...)` (`admin_commands.py:455`) — respektiert die Sichtbarkeits-Konfiguration | `_send_channel_message(interaction.channel, embed=...)` (`bot.py:14941`) — **kein** `visibility_key`, kein `command_visibility_key_for_interaction(...)` |

Der fehlende `visibility_key`-Pfad ist eine zusätzliche Drift-Quelle: `/karte-geben` postet das Ergebnis über die zentrale Visibility-Steuerung, `/dust` postet hart in den Channel.

### D. `item_label`-Detail im Multi-User-Picker

`/karte-geben` ruft `DustMultiUserSelectView(..., item_label="Karten")` auf (`admin_commands.py:300–303`), während `run_dust_command_flow` keinen `item_label`-Override setzt (`bot.py:14985`) — der Default `"Infinitydust"` greift. Der Embed-Titel des User-Pickers ist nur dann „💎 Multi-Auswahl für Infinitydust", wenn `item_label == "Infinitydust"` (`bot.py:7350`). Praktisch unproblematisch, der Pfad ist symmetrisch und korrekt.

### E. Dev-Panel `give_dust` ist eine dritte Implementierung

Im Dev-Panel (`bot.py:16125–16151`) existiert ein eigener Mini-Flow:
- nutzt `_select_user` (Single-User-Picker), nicht `AdminUserSelectView` plus Multi
- nutzt `_select_number(... [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000])` mit anderer Quick-Pick-Liste
- baut ein eigenes Embed `Infinitydust vergeben` (Color `0x2ECC71`, ohne Modus-Zeile, mit Visibility-Key `give_dust`)
- ruft kein `log_admin_dust_action`, sondern nur `_log_event_safe("admin_dust_action", ...)`

Für Task 4.2 außerhalb des unmittelbaren Scopes (Dev-Panel != Slash-Command), aber relevant als Drift-Hinweis: nach Vereinheitlichung sollte auch der Dev-Panel-Pfad auf `run_dust_command_flow` umgeleitet werden, damit es nur eine Quelle der Wahrheit gibt.

## Notwendige Code-Änderungen für Task 4.2

Auf Basis dieses Audits sind in Task 4.2 folgende konkrete Änderungen am Dust-Flow nötig, damit `/dust` und `/lödust` UI-seitig zu `/karte-geben` parallel laufen:

1. **`DUST_MENU_AMOUNTS` umstellen** (`bot.py:191`) auf `[5, 10, 15, 20, 25, 30]` — die Werte `35, 40, 45, 50, 70, 100` werden im Multi-Modus nicht mehr benötigt (Req. 5.2). Falls ein Single-Modus weiterhin größere Schritte will, lokale Konstante einführen oder Parameter durchreichen.
2. **Quick-Pick-UI auf Buttons umstellen** (Multi-Modus): neue Mini-View `DustQuickAmountView` mit sechs Buttons `[5][10][15][20][25][30]` plus separatem Button „Eigener Betrag…", der `NumberInputModal` öffnet. `_select_number(...)` als Helper bleibt bestehen, wird aber für den Multi-Pfad durch die neue View ersetzt; Single-Pfad kann den heutigen `NumberSelectView` weiterverwenden, sollte aber auf dieselbe Liste zugreifen.
3. **Custom-Amount-Validierung erweitern** (`NumberInputModal.on_submit`, `bot.py:14868`): zusätzlich zur heutigen `isdigit() and > 0`-Prüfung obere Grenze `<= 1_000_000` aus Req. 5.4 aufnehmen, klare Fehlermeldung statt stiller Annahme. `max_length=9` auf `7` reduzieren (Req. 5.4: 1.000.000 → 7 Zeichen).
4. **Konflikt-Auflösung „0 + aktiver Quick-Pick"** (Req. 5.5): die neue Button-View merkt sich pro Klick den letzten gültigen Quick-Pick-Wert; öffnet der User danach das Modal und gibt `0` ein, bleibt der vorherige Quick-Pick gewinner; Modal-Eingabe `0` darf den State **nicht** überschreiben.
5. **Confirm-Schritt im Multi-Modus** (Req. 4.1, Parität zu `admin_commands.py:340–369`): vor `_post_dust_result_message(...)` (`bot.py:15039`) eine `DustGiveConfirmView` analog zu `GiveCardConfirmView` einziehen, mit Embed-Titel „📝 Bestätigung: Infinitydust verteilen", Color `0xF1C40F`, Footer-Text „Mit ✅ jetzt verteilen, ❌ abbrechen." und Timeout-Meldung „⏰ Zeit abgelaufen. Vergabe abgebrochen.". Single-Modus bleibt confirm-frei.
6. **Ergebnis-Embed angleichen** (`_post_dust_result_message`, `bot.py:14909–14946`):
   - Color-Logik: bei `remove=True` Bucket „angefordert vs. tatsächlich entfernt" auswerten — `applied_amount < requested_amount` führt zur orange-Warn-Color `0xE67E22`, `applied_amount == 0` für mindestens einen User zur roten `0xE74C3C`. `give`-Pfad bleibt grün, weil hier per Definition kein Teilfehler entsteht.
   - Field-Layout: einen `Übersicht`-Field mit den drei Bucket-Zählern wie bei `/karte-geben` einführen, auch wenn bei `give` immer alles in den ✅-Bucket fällt — das hält die UI-Form parallel.
   - Versand: auf `_send_with_visibility(interaction, visibility_key, embed=...)` umstellen (`visibility_key` aus `command_visibility_key_for_interaction(interaction)` ableiten, analog `admin_commands.py:281`); damit fällt `_send_channel_message`-Direktversand weg.
7. **Single+1-User-Bonus optional**: bei Single + Custom-Betrag könnte das Embed das Item-Bild groß zeigen (Pendant zur „Single+1-Karte"-Sonderbehandlung). Nicht zwingend für Task 4.2, aber als Folge-Cleanup notierenswert.
8. **`/lödust`-Pfad** (Req. 5.6, Task 4.3): keine separaten Änderungen nötig, da `loedust` denselben `run_dust_command_flow(remove=True)` nutzt; Verifikations-Tests laut Task 4.3 reichen.

Dev-Panel `give_dust` (`bot.py:16125–16151`) bleibt in Task 4.2 unverändert, sollte aber als Folgearbeit auf `run_dust_command_flow` umgeleitet werden, damit künftiger Drift verhindert wird.
