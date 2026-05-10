from __future__ import annotations

# Zentrale UI-/Story-Texte (ohne attack["info"] und Kartenbeschreibungen in karten.py / mission_enemies.py).

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

# Gegner-Vorschau (Lakaien / Boss)
PREVIEW_TITLE_LACKEY = "Gegner-Vorschau ({index}/{total})"
PREVIEW_TITLE_BOSS = "Boss-Vorschau"
PREVIEW_DESCRIPTION_LACKEY = "So kämpft **{name}** in dieser Mission."
PREVIEW_DESCRIPTION_BOSS = "Der finale Gegner: **{name}**."
PREVIEW_FIELD_RARITY = "Seltenheit"
PREVIEW_BTN_NEXT = "Weiter"
PREVIEW_BTN_SKIP = "Überspringen"
PREVIEW_BTN_START_MISSION = "Mission starten"
PREVIEW_BTN_START_BOSS = "Boss-Kampf starten"
PREVIEW_BTN_CHANGE_HERO = "Held wechseln"

# Bot-Zug Spotlight
BOT_SPOTLIGHT_DESCRIPTION = "🎯 **{bot_name}** ist am Zug …"

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


def operation_broken_timeline_title(*, is_admin: bool, suffix: str) -> str:
    if is_admin:
        return OPERATION_BROKEN_TIMELINE_TITLE_ADMIN
    return OPERATION_BROKEN_TIMELINE_TITLE_PLAYER.format(suffix=suffix)
