"""Rollen vergeben und entziehen.

Discord erlaubt einem Bot nur Rollen **unterhalb** seiner eigenen höchsten
Rolle, und nur mit dem Recht „Rollen verwalten". Beides wird hier geprüft,
bevor irgendetwas passiert — mit einer klaren Begründung pro Rolle statt einer
kryptischen Fehlermeldung von Discord.

Jeder Aufruf kann als Trockenlauf laufen (`dry_run`): dann bekommst du die
vollständige Vorschau, ohne dass etwas verändert wird.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import audit, database, discordapi, jobs, schema, settings

MAX_TARGETS = 500          # Schutz vor versehentlichen Massenaktionen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _member_name(member: dict) -> str:
    user = member.get("user") or {}
    return member.get("nick") or user.get("global_name") or user.get("username") or user.get("id", "?")


async def _resolve_targets(guild_id: str, user_ids: list[str], from_role_id: str | None) -> list[dict]:
    """Zielpersonen bestimmen — entweder direkt genannt oder alle Mitglieder
    einer bestehenden Rolle."""
    if from_role_id:
        alle = await discordapi.all_members(guild_id)
        return [m for m in alle if str(from_role_id) in {str(r) for r in m.get("roles", [])}]

    gesucht = {str(u).strip() for u in user_ids if str(u).strip()}
    if not gesucht:
        return []
    alle = await discordapi.all_members(guild_id)
    nach_id = {str((m.get("user") or {}).get("id")): m for m in alle}
    return [nach_id[uid] for uid in gesucht if uid in nach_id]


async def apply(body, caller, client_ip: str | None) -> dict:
    if body.action not in ("add", "remove"):
        raise HTTPException(400, "Die Aktion muss 'add' oder 'remove' sein.")
    if not body.role_ids:
        raise HTTPException(400, "Bitte mindestens eine Rolle auswählen.")

    info = await discordapi.manageable_roles(body.guild_id)
    nach_id = {r["id"]: r for r in info["roles"]}

    gewaehlt, abgelehnt = [], []
    for role_id in body.role_ids:
        role = nach_id.get(str(role_id))
        if not role:
            abgelehnt.append({"role_id": str(role_id), "name": None,
                              "grund": "Diese Rolle gibt es auf dem Server nicht."})
        elif not role["manageable"]:
            abgelehnt.append({"role_id": role["id"], "name": role["name"],
                              "grund": role["blocked_reason"]})
        else:
            gewaehlt.append(role)

    ziele = await _resolve_targets(body.guild_id, body.user_ids, body.from_role_id)
    if not ziele:
        raise HTTPException(400, "Es wurde niemand gefunden, auf den die Aktion passt.")
    if len(ziele) * max(len(gewaehlt), 1) > MAX_TARGETS:
        raise HTTPException(400, f"Das wären {len(ziele) * len(gewaehlt)} Änderungen auf einmal. "
                                 f"Zur Sicherheit sind höchstens {MAX_TARGETS} erlaubt — "
                                 "bitte in kleineren Gruppen.")

    # --- Vorschau: was würde tatsächlich passieren? ---
    vorschau = []
    for member in ziele:
        user_id = str((member.get("user") or {}).get("id"))
        hat = {str(r) for r in member.get("roles", [])}
        for role in gewaehlt:
            if body.action == "add":
                zustand = "hatte_schon" if role["id"] in hat else "wird_gesetzt"
            else:
                zustand = "wird_entfernt" if role["id"] in hat else "hatte_nicht"
            vorschau.append({"user_id": user_id, "name": _member_name(member),
                             "role_id": role["id"], "role_name": role["name"],
                             "zustand": zustand})

    zu_tun = [v for v in vorschau if v["zustand"] in ("wird_gesetzt", "wird_entfernt")]
    zusammenfassung = {
        "mitglieder": len(ziele),
        "rollen": len(gewaehlt),
        "aenderungen": len(zu_tun),
        "ohne_wirkung": len(vorschau) - len(zu_tun),
        "abgelehnte_rollen": abgelehnt,
    }

    if body.dry_run:
        return {"trockenlauf": True, "zusammenfassung": zusammenfassung, "vorschau": vorschau}
    if not zu_tun:
        return {"trockenlauf": False, "zusammenfassung": zusammenfassung, "vorschau": vorschau,
                "hinweis": "Es gab nichts zu tun — alle Beteiligten sind schon im Zielzustand."}

    ablauf = None
    if body.expires_in_minutes and body.action == "add":
        minuten = max(1, min(int(body.expires_in_minutes), 60 * 24 * 365))
        ablauf = (datetime.now(timezone.utc) + timedelta(minutes=minuten)).isoformat(timespec="seconds")

    nutzlast = {
        "action": body.action,
        "reason": body.reason,
        "expires_at": ablauf,
        "changes": [{"user_id": v["user_id"], "role_id": v["role_id"]} for v in zu_tun],
    }

    modus = settings.get("exec.mode")
    if modus == "direct":
        ergebnis = await _apply_direct(body.guild_id, nutzlast, caller.actor, ablauf)
    else:
        job = jobs.create("role.apply", body.guild_id, nutzlast,
                          requested_by=caller.actor, total=len(zu_tun))
        ergebnis = {"auftrag": job,
                    "hinweis": "Der Bot arbeitet das gleich ab — der Fortschritt wird angezeigt."}

    audit.record(actor=caller.actor,
                 action="rollen.gesetzt" if body.action == "add" else "rollen.entfernt",
                 guild_id=body.guild_id, target=f"{len(ziele)} Mitglieder",
                 detail=", ".join(r["name"] for r in gewaehlt)[:400], client_ip=client_ip)

    return {"trockenlauf": False, "zusammenfassung": zusammenfassung,
            "vorschau": vorschau, **ergebnis}


async def _apply_direct(guild_id: str, payload: dict, actor: str, expires_at: str | None) -> dict:
    """Modus 'Direkt': die Website spricht selbst mit Discord."""
    gesetzt, fehlgeschlagen = [], []
    for change in payload["changes"]:
        try:
            if payload["action"] == "add":
                await discordapi.add_role(guild_id, change["user_id"], change["role_id"],
                                          payload["reason"])
            else:
                await discordapi.remove_role(guild_id, change["user_id"], change["role_id"],
                                             payload["reason"])
            gesetzt.append(change)
        except discordapi.DiscordError as exc:
            fehlgeschlagen.append({**change, "grund": str(exc)})

    _record_grants(guild_id, payload["action"], gesetzt, actor, None, expires_at)
    return {"ausgefuehrt": len(gesetzt), "fehlgeschlagen": fehlgeschlagen,
            "hinweis": "Direkt an Discord geschickt (Einstellung 'Direkt')."}


def _record_grants(guild_id: str, action: str, changes: list[dict], actor: str,
                   job_id: int | None, expires_at: str | None) -> None:
    if not changes:
        return
    now = _now()
    with database.write_connection() as con:
        schema.init_schema(con)
        con.executemany(
            "INSERT INTO role_grants (created_at, guild_id, user_id, role_id, action, actor, "
            "job_id, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(now, str(guild_id), str(c["user_id"]), str(c["role_id"]), action, actor,
              job_id, expires_at) for c in changes])


# --------------------------------------------------------------------------
# Auswertungen
# --------------------------------------------------------------------------
PERMISSION_NAMES = {
    0: "Einladung erstellen", 1: "Mitglieder sperren", 2: "Mitglieder entfernen",
    3: "Administrator", 4: "Kanäle verwalten", 5: "Server verwalten",
    6: "Reaktionen hinzufügen", 7: "Prüfprotokoll ansehen", 8: "Laut sprechen",
    10: "Kanäle sehen", 11: "Nachrichten senden", 13: "Nachrichten löschen",
    16: "Verlauf lesen", 17: "@everyone erwähnen", 18: "Externe Emojis",
    20: "Sprachkanal betreten", 22: "Andere stummschalten", 23: "Andere taub schalten",
    24: "Mitglieder verschieben", 26: "Spitznamen ändern", 27: "Spitznamen verwalten",
    28: "Rollen verwalten", 29: "Webhooks verwalten", 30: "Emojis verwalten",
    34: "Mitglieder in Auszeit schicken", 40: "Threads verwalten",
}


async def explain_permissions(guild_id: str, user_id: str) -> dict:
    """Was darf diese Person auf diesem Server wirklich?"""
    member = await discordapi.member(guild_id, user_id)
    alle_rollen = await discordapi.roles(guild_id)
    hat = {str(r) for r in member.get("roles", [])}

    maske = 0
    eigene = []
    for role in alle_rollen:
        if str(role.get("id")) in hat or str(role.get("id")) == str(guild_id):
            maske |= int(role.get("permissions", 0))
            eigene.append({"id": str(role.get("id")), "name": role.get("name"),
                           "position": role.get("position", 0), "color": role.get("color", 0)})

    ist_admin = bool(maske & (1 << 3))
    rechte = []
    for bit, name in sorted(PERMISSION_NAMES.items()):
        aktiv = ist_admin or bool(maske & (1 << bit))
        rechte.append({"name": name, "aktiv": aktiv,
                       "grund": "über Administrator" if ist_admin and not (maske & (1 << bit)) else None})

    return {
        "user_id": str(user_id),
        "name": _member_name(member),
        "beigetreten_am": member.get("joined_at"),
        "in_auszeit_bis": member.get("communication_disabled_until"),
        "rollen": sorted(eigene, key=lambda r: r["position"], reverse=True),
        "ist_administrator": ist_admin,
        "rechte": rechte,
    }


async def orphan_roles(guild_id: str) -> dict:
    """Rollen, die niemand hat — Aufräum-Kandidaten."""
    rollen = await discordapi.roles(guild_id)
    mitglieder = await discordapi.all_members(guild_id)
    belegt: dict[str, int] = {}
    for member in mitglieder:
        for role_id in member.get("roles", []):
            belegt[str(role_id)] = belegt.get(str(role_id), 0) + 1

    out = []
    for role in rollen:
        rid = str(role.get("id"))
        if rid == str(guild_id):
            continue
        out.append({"id": rid, "name": role.get("name"), "anzahl": belegt.get(rid, 0),
                    "verwaltet": bool(role.get("managed")),
                    "position": role.get("position", 0)})
    return {"rollen": sorted(out, key=lambda r: (r["anzahl"], -r["position"])),
            "ohne_mitglieder": [r for r in out if r["anzahl"] == 0 and not r["verwaltet"]]}


def load_json(raw, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
