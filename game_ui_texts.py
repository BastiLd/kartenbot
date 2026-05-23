from __future__ import annotations

# ============================================================
# ZENTRALE UI-/STORY-TEXTE
# ============================================================
# Nur sichtbare Texte für das Spiel-UI.
# Platzhalter wie {waves}, {card}, {invitee} werden zur Laufzeit ersetzt.
# Nicht hier:
# - Kartenbeschreibungen -> karten.py
# - Gegner/Attacken      -> mission_enemies.py

OPERATION_BROKEN_TIMELINE_TITLE_ADMIN = "Operation Broken Timeline (Admin)"
# {suffix} z. B. "1/2"
OPERATION_BROKEN_TIMELINE_TITLE_PLAYER = "Operation Broken Timeline ({suffix})"

OPERATION_BROKEN_TIMELINE_DESCRIPTION = (
    "**Nick Furys Hologramm flackert auf.**\n\n"
    "Agent, wir haben ein Problem, das jede normale Bedrohung wie ein Trainingsprogramm aussehen lässt. "
    "In einer gebrochenen Zeitlinie ist Bruce Banner nicht Hulk geblieben, sondern Maestro geworden. "
    "Er hat die Helden dieser Welt besiegt, herrscht über ein radioaktives Ödland und baut einen Chronos-Anker, "
    "mit dem er unsere Realität erreichen kann.\n\n"
    "Du gehst durch die Außenwellen seiner Festung, sicherst die Route und stellst dich danach Maestro selbst."
)

INTERLUDE_TITLE_DEFAULT = "Furys Versorgungskapsel"
INTERLUDE_TEXT_DEFAULT = (
    "Beeindruckend. Die Route ist frei. Ich schicke dir jetzt eine Versorgungskapsel. "
    "Heile dich, sortiere deine Ausrüstung und mach dich bereit. Maestro wartet im Thronsaal."
)
INTERLUDE_HEAL_FIELD = "Der nächste Kampf wird ohne Cooldowns und mit vollen HP gestartet."

MISSION_PAUSE_PLACEHOLDER = "Was möchtest du tun?"
MISSION_PAUSE_KEEP_LABEL = "Beibehalten: {card_name}"
MISSION_PAUSE_CHANGE_LABEL = "Neue Karte wählen"
MISSION_SELECT_NEW_CARD_PROMPT = "Wähle eine neue Karte:"

# Gegner-Vorschau (Lakaien / Boss)
PREVIEW_TITLE_LACKEY = "Gegner-Vorschau ({index}/{total})"
PREVIEW_TITLE_BOSS = "Boss-Vorschau"
PREVIEW_DESCRIPTION_LACKEY = "So kämpft **{name}** in dieser Mission."
PREVIEW_DESCRIPTION_BOSS = "Der finale Gegner: **{name}**."
PREVIEW_FIELD_RARITY = "Seltenheit"
PREVIEW_FIELD_TACTIC = "Taktik"
PREVIEW_BTN_NEXT = "Weiter"
PREVIEW_BTN_SKIP = "Überspringen"
PREVIEW_BTN_START_MISSION = "Mission starten"
PREVIEW_BTN_START_BOSS = "Boss-Kampf starten"
PREVIEW_BTN_CHANGE_HERO = "Held wechseln"
PREVIEW_CHANGE_NOT_AVAILABLE = "Du kannst die Karte erst wieder vor dem Boss wechseln."
PREVIEW_RESELECT_CARD_PROMPT = "{mention}, wähle deine Karte für die Mission:"

# Bot-Zug Spotlight
BOT_SPOTLIGHT_DESCRIPTION = "🎯 **{bot_name}** ist am Zug …"

# Gegner-Zug Ansicht
ENEMY_TURN_DESCRIPTION = "🎯 **{enemy_name}** ist am Zug. Unten siehst du seine Attacken."

# Maestro Kampflog (nicht die Karten-Fähigkeitstexte)
MAESTRO_EXECUTE_MARKED = (
    "Gnadenschuss des Tyrannen vorbereitet: Spieler unter 50 HP, Maestro nutzt in seiner nächsten Runde 999 unblockbaren Schaden."
)
MAESTRO_EXECUTE_FIRED = "Gnadenschuss des Tyrannen löst aus: 999 unblockbarer Schaden."
MAESTRO_EXECUTE_CANCELLED = "Maestro bricht den Gnadenschuss ab: Du hast wieder mindestens 50 HP."

# Missions-Erfolg
MISSION_SUCCESS_TITLE_NEW_CARD = "🏆 Mission erfolgreich!"
MISSION_SUCCESS_DESC_NEW_CARD = "Du hast alle {waves} Wellen überstanden und **{card}** erhalten!"
MISSION_SUCCESS_TITLE_DUST = "💎 Mission erfolgreich - Infinitydust!"
MISSION_SUCCESS_DESC_DUST = "Du hast alle {waves} Wellen überstanden!"
MISSION_SUCCESS_DUST_FIELD_NAME = "Belohnung"
MISSION_SUCCESS_DUST_FIELD_VALUE = "Du hattest **{card}** bereits - wurde zu **Infinitydust** umgewandelt!"

# Einladungen
INVITE_CONFIRM_TITLE = "Einladung bestätigen"
INVITE_CONFIRM_DESCRIPTION = (
    "{invitee} gibt an, von {inviter} eingeladen worden zu sein.\n\n"
    "Beide müssen bestätigen, bevor Belohnungen vergeben werden."
    "{admin_note}"
)
INVITE_ADMIN_NOTE = "\n\n**Admin-Freigabe nötig:** Dieser Einlader hat bereits viele Einladungen abgeschlossen."
INVITE_BTN_INVITER = "Einlader bestätigt"
INVITE_BTN_INVITEE = "Eingeladener bestätigt"
INVITE_BTN_ADMIN = "Admin genehmigt"
INVITE_CONFIRM_ACK_INVITER = "✅ Einlader hat bestätigt."
INVITE_CONFIRM_ACK_INVITEE = "✅ Eingeladener hat bestätigt."
INVITE_CONFIRM_ACK_ADMIN = "✅ Admin hat freigegeben."
INVITE_SUCCESS = (
    "🎉 Einladung bestätigt!\n"
    "**{inviter}** und **{invitee}** haben die Zuordnung bestätigt."
)

# Intro-Autoprompt
INTRO_PROMPT_MESSAGE = (
    "Klicke auf den Button, um dir die Start-Anleitung anzeigen zu lassen. "
    "Danach weißt du direkt, wie `/eingeladen` und die anderen Befehle funktionieren."
)

# Dev-Tools: Wartungsmodus
MAINTENANCE_CONFIRM_ON_TITLE = "Wartungsmodus einschalten?"
MAINTENANCE_CONFIRM_OFF_TITLE = "Wartungsmodus ausschalten?"
MAINTENANCE_CONFIRM_ON_TEXT = (
    "Wenn Wartungsmodus **AN** ist, können normale Nutzer keine Commands benutzen. "
    "Nur Owner/Dev/Admin können weiterarbeiten.\n\n"
    "Willst du das wirklich einschalten?"
)
MAINTENANCE_CONFIRM_OFF_TEXT = (
    "Wenn Wartungsmodus **AUS** ist, können wieder alle Nutzer normal Commands benutzen.\n\n"
    "Willst du das wirklich ausschalten?"
)
MAINTENANCE_CONFIRM_BTN_YES = "Bestätigen"
MAINTENANCE_CONFIRM_BTN_NO = "Abbrechen"
MAINTENANCE_ENABLED = "Wartungsmodus aktiviert."
MAINTENANCE_DISABLED = "Wartungsmodus deaktiviert."
MAINTENANCE_CANCELLED = "Änderung am Wartungsmodus abgebrochen."

# Dev-Tools: Alpha-Modus
ALPHA_FEATURE_DISABLED_TEXT = "🧪 Alpha ist aktiv: Mission, Story und Einladungen sind aktuell deaktiviert."
ALPHA_CONFIRM_ON_TITLE = "Alpha-Modus einschalten?"
ALPHA_CONFIRM_OFF_TITLE = "Alpha-Modus ausschalten?"
ALPHA_CONFIRM_ON_TEXT = (
    "Wenn der Alpha-Modus **AN** ist, werden `/mission`, `/geschichte` und `/eingeladen` für diesen Server blockiert.\n"
    "`/anfang` zeigt Mission und Story dann nicht mehr als Buttons.\n\n"
    "Willst du das wirklich einschalten?"
)
ALPHA_CONFIRM_OFF_TEXT = (
    "Wenn der Alpha-Modus **AUS** ist, sind `/mission`, `/geschichte` und `/eingeladen` wieder verfügbar.\n"
    "Falls Beta aktiv ist, bleiben Story und Einladungen trotzdem blockiert.\n\n"
    "Willst du das wirklich ausschalten?"
)
ALPHA_ENABLED = "Alpha-Modus aktiviert."
ALPHA_DISABLED = "Alpha-Modus deaktiviert."
ALPHA_CANCELLED = "Änderung am Alpha-Modus abgebrochen."
FEATURE_FLAG_REFRESH_UPDATED = "Letzte /anfang-Nachricht wurde aktualisiert."
FEATURE_FLAG_REFRESH_NOT_UPDATED = "Keine gespeicherte /anfang-Nachricht aktualisiert."

# Dev-Tools: Beta-Modus
BETA_STORY_DISABLED_TEXT = "🧪 Beta ist aktiv: Story ist aktuell deaktiviert."
BETA_INVITE_DISABLED_TEXT = "🧪 Beta ist aktiv: Einladungen sind aktuell deaktiviert."
BETA_CONFIRM_ON_TITLE = "Beta-Modus einschalten?"
BETA_CONFIRM_OFF_TITLE = "Beta-Modus ausschalten?"
BETA_CONFIRM_ON_TEXT = (
    "Wenn der Beta-Modus **AN** ist, werden `/geschichte` und `/eingeladen` für diesen Server blockiert.\n"
    "`/anfang` zeigt Story dann nicht mehr als Button.\n\n"
    "Willst du das wirklich einschalten?"
)
BETA_CONFIRM_OFF_TEXT = (
    "Wenn der Beta-Modus **AUS** ist, sind `/geschichte` und `/eingeladen` wieder verfügbar.\n"
    "Falls Alpha aktiv ist, bleiben Mission, Story und Einladungen trotzdem blockiert.\n\n"
    "Willst du das wirklich ausschalten?"
)
BETA_ENABLED = "Beta-Modus aktiviert."
BETA_DISABLED = "Beta-Modus deaktiviert."
BETA_CANCELLED = "Änderung am Beta-Modus abgebrochen."

# Missionen: zentrale Operationstexte
# Neue Operationen müssen nur hier ergänzt werden.
MISSION_OPERATION_TEXTS: dict[str, dict[str, str]] = {
    "operation_broken_timeline": {
        "label": "Operation Broken Timeline (Maestro)",
        "title": "Operation Broken Timeline",
        "description": OPERATION_BROKEN_TIMELINE_DESCRIPTION,
    },
    "operation_technischer_kollaps": {
        "label": "Operation Technischer Kollaps (M.O.D.O.K.)",
        "title": "Operation Technischer Kollaps",
        "description": (
            "A.I.M. hat M.O.D.O.K. auf das globale Verteidigungsnetz losgelassen. "
            "Kämpfe dich durch Laborwachen und Mechs bis zum Kernrechner."
        ),
    },
    "operation_gruener_terror": {
        "label": "Operation Grüner Terror (Green Goblin)",
        "title": "Operation Grüner Terror",
        "description": (
            "Norman Osborn will die Stadt mit Goblin-Gas überziehen. "
            "Stoppe seine Truppen und stürze den Green Goblin vom Dach."
        ),
    },
    "operation_goldener_kaefig": {
        "label": "Operation Goldener Käfig (Kingpin)",
        "title": "Operation Goldener Käfig",
        "description": (
            "Wilson Fisk kontrolliert Tower und Unterwelt. "
            "Räume die Etagen und beende den Kampf im Penthouse."
        ),
    },
    "operation_hexenfeuer": {
        "label": "Operation Hexenfeuer (Agatha Harkness)",
        "title": "Operation Hexenfeuer",
        "description": (
            "Agatha Harkness reißt die Grenze zur dunklen Dimension auf. "
            "Besiege ihre Wächter und durchbrich den Hexenkreis."
        ),
    },
}

# Boss-spezifische Taktiktexte für die Boss-Vorschau vor dem Kampf.
MISSION_BOSS_TACTICS: dict[str, str] = {
    "maestro": (
        "Bleib beim Zugende bei mindestens 50 HP. "
        "Wenn du darunter faellst, markiert Maestro seinen Gnadenschuss "
        "und kann im naechsten Boss-Zug 999 unblockbaren Schaden ausloesen."
    ),
    "modok": (
        "Plane Spezialangriffe sauber ein: M.O.D.O.K. kann sie mit System-Hack sperren "
        "und mit Berechneter Heilung viel HP zurückholen."
    ),
    "green_goblin": (
        "Rechne mit Verfehlungen und Kontern durch Gas/Illusionseffekte. "
        "Spiele wichtige Angriffe in sicheren Fenstern."
    ),
    "kingpin": (
        "Konstant Schaden machen lohnt sich: Bei Bestechungs-Versuch heilt Kingpin "
        "nur 35 HP, wenn du im Zug davor Schaden gemacht hast. "
        "Ohne Schaden heilt er 60 HP."
    ),
    "agatha_harkness": (
        "Achte auf Heal-Block und Ausweichfenster. "
        "Wenn Agatha den Fluch oder die Illusion aktiv hat, nicht blind in wichtige Heals/Angriffe laufen."
    ),
}


def mission_operation_order() -> tuple[str, ...]:
    return tuple(MISSION_OPERATION_TEXTS.keys())


def operation_broken_timeline_title(*, is_admin: bool, suffix: str) -> str:
    if is_admin:
        return OPERATION_BROKEN_TIMELINE_TITLE_ADMIN
    return OPERATION_BROKEN_TIMELINE_TITLE_PLAYER.format(suffix=suffix)
