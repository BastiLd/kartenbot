from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from services.analytics import fetch_events

try:
    import xlsxwriter
except ImportError as exc:  # pragma: no cover - runtime guard
    raise RuntimeError("xlsxwriter is required for stats export") from exc


TZ = ZoneInfo("Europe/Vienna")


def _dt_text(timestamp: int) -> str:
    if int(timestamp or 0) <= 0:
        return ""
    return datetime.fromtimestamp(int(timestamp), TZ).strftime("%Y-%m-%d %H:%M:%S")


def _date_text(timestamp: int) -> str:
    if int(timestamp or 0) <= 0:
        return ""
    return datetime.fromtimestamp(int(timestamp), TZ).strftime("%Y-%m-%d")


def _payload_value(payload: dict[str, Any], *keys: str, default: Any = "") -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def build_stats_workbook() -> tuple[bytes, str]:
    events = await fetch_events()
    now = datetime.now(TZ).strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"katabump_stats_{now}.xlsx"

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    header_fmt = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
    sub_header_fmt = workbook.add_format({"bold": True, "bg_color": "#EAEAEA", "border": 1})
    cell_fmt = workbook.add_format({"border": 1})
    num_fmt = workbook.add_format({"border": 1, "num_format": "0"})
    title_fmt = workbook.add_format({"bold": True, "font_size": 14})

    overview = workbook.add_worksheet("Overview")
    charts_ws = workbook.add_worksheet("Charts")
    events_ws = workbook.add_worksheet("Events")
    heroes_ws = workbook.add_worksheet("Heroes")
    attacks_ws = workbook.add_worksheet("Attacks")
    upgrades_ws = workbook.add_worksheet("Upgrades")
    admin_ws = workbook.add_worksheet("Admin_Actions")
    lifecycle_ws = workbook.add_worksheet("Lifecycle")

    overview.write("A1", "Katabump Statistik-Export", title_fmt)
    overview.write("A3", "Kennzahl", header_fmt)
    overview.write("B3", "Wert", header_fmt)

    total_events = len(events)
    command_events = [event for event in events if event["event_type"] == "command_used"]
    attack_events = [event for event in events if event["event_type"] == "attack_used"]
    hero_events = [event for event in events if event["event_type"] == "hero_selected"]
    upgrade_events = [event for event in events if event["event_type"] == "upgrade_applied"]
    admin_events = [event for event in events if event["event_type"].startswith("admin_")]
    lifecycle_events = [event for event in events if event["event_type"].startswith("lifecycle_")]
    unique_actors = {
        int(event["actor_user_id"])
        for event in events
        if int(event.get("actor_user_id", 0) or 0) > 0
    }

    overview_rows = [
        ("Gesamt-Events", total_events),
        ("Command-Aufrufe", len(command_events)),
        ("Heldenwahlen", len(hero_events)),
        ("Attacken", len(attack_events)),
        ("Upgrades", len(upgrade_events)),
        ("Admin-Aktionen", len(admin_events)),
        ("Lifecycle-Events", len(lifecycle_events)),
        ("Eindeutige Nutzer", len(unique_actors)),
    ]
    for idx, (label, value) in enumerate(overview_rows, start=4):
        overview.write(f"A{idx}", label, cell_fmt)
        overview.write(f"B{idx}", value, num_fmt)

    top_hero_counter = Counter(event["hero_name"] for event in hero_events if event["hero_name"])
    top_attack_counter = Counter(
        f"{event['hero_name']} - {event['attack_name']}"
        for event in attack_events
        if event["hero_name"] and event["attack_name"]
    )
    top_upgrade_counter = Counter(
        f"{event['hero_name']} - {str(_payload_value(event['payload'], 'upgrade_type', default=''))}"
        for event in upgrade_events
        if event["hero_name"]
    )
    top_command_counter = Counter(event["command_name"] for event in command_events if event["command_name"])
    top_day_counter = Counter(_date_text(event["created_at"]) for event in events if event["created_at"])

    overview.write("A14", "Weitere Insights", header_fmt)
    overview.write("A15", "Kennzahl", sub_header_fmt)
    overview.write("B15", "Wert", sub_header_fmt)
    extra_rows = [
        ("Meistgenutzter Held", (top_hero_counter.most_common(1)[0][0] if top_hero_counter else "-")),
        ("Meistgenutzte Attacke", (top_attack_counter.most_common(1)[0][0] if top_attack_counter else "-")),
        ("Häufigstes Upgrade", (top_upgrade_counter.most_common(1)[0][0] if top_upgrade_counter else "-")),
        ("Häufigster Command", (top_command_counter.most_common(1)[0][0] if top_command_counter else "-")),
        ("Aktivster Tag", (top_day_counter.most_common(1)[0][0] if top_day_counter else "-")),
    ]
    for idx, (label, value) in enumerate(extra_rows, start=16):
        overview.write(f"A{idx}", label, cell_fmt)
        overview.write(f"B{idx}", value, cell_fmt)
    overview.set_column("A:A", 32)
    overview.set_column("B:B", 40)

    event_headers = [
        "id",
        "created_at",
        "created_at_local",
        "event_type",
        "guild_id",
        "channel_id",
        "thread_id",
        "session_id",
        "session_kind",
        "actor_user_id",
        "target_user_id",
        "command_name",
        "hero_name",
        "attack_name",
        "payload_json",
    ]
    for col, header in enumerate(event_headers):
        events_ws.write(0, col, header, header_fmt)
    for row_idx, event in enumerate(events, start=1):
        payload = event.get("payload", {})
        values = [
            event.get("id", 0),
            event.get("created_at", 0),
            _dt_text(_safe_int(event.get("created_at"))),
            event.get("event_type", ""),
            event.get("guild_id", 0),
            event.get("channel_id", 0),
            event.get("thread_id", 0),
            event.get("session_id", 0),
            event.get("session_kind", ""),
            event.get("actor_user_id", 0),
            event.get("target_user_id", 0),
            event.get("command_name", ""),
            event.get("hero_name", ""),
            event.get("attack_name", ""),
            str(payload),
        ]
        for col, value in enumerate(values):
            events_ws.write(row_idx, col, value, cell_fmt)
    events_ws.set_column("A:O", 18)

    hero_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"selected": 0, "attacks": 0, "wins": 0, "losses": 0})
    for event in hero_events:
        hero = event["hero_name"]
        if hero:
            hero_stats[hero]["selected"] += 1
    for event in attack_events:
        hero = event["hero_name"]
        if hero:
            hero_stats[hero]["attacks"] += 1
    for event in events:
        if event["event_type"] != "fight_result":
            continue
        winner = str(_payload_value(event["payload"], "winner_hero", default="")).strip()
        loser = str(_payload_value(event["payload"], "loser_hero", default="")).strip()
        if winner:
            hero_stats[winner]["wins"] += 1
        if loser:
            hero_stats[loser]["losses"] += 1

    for col, header in enumerate(["hero_name", "selected_count", "attack_count", "wins", "losses"]):
        heroes_ws.write(0, col, header, header_fmt)
    hero_rows = sorted(hero_stats.items(), key=lambda item: (-item[1]["selected"], item[0]))
    for row_idx, (hero, stats) in enumerate(hero_rows, start=1):
        heroes_ws.write(row_idx, 0, hero, cell_fmt)
        heroes_ws.write(row_idx, 1, stats["selected"], num_fmt)
        heroes_ws.write(row_idx, 2, stats["attacks"], num_fmt)
        heroes_ws.write(row_idx, 3, stats["wins"], num_fmt)
        heroes_ws.write(row_idx, 4, stats["losses"], num_fmt)
    heroes_ws.set_column("A:A", 24)
    heroes_ws.set_column("B:E", 14)

    attack_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"count": 0, "damage": 0, "burn": 0, "bonus": 0})
    for event in attack_events:
        hero = event["hero_name"]
        attack = event["attack_name"]
        if not hero or not attack:
            continue
        bucket = attack_stats[(hero, attack)]
        bucket["count"] += 1
        bucket["damage"] += _safe_int(_payload_value(event["payload"], "damage", "final_damage", default=0))
        bucket["burn"] += _safe_int(_payload_value(event["payload"], "damage", "pre_effect_damage", default=0))
        bucket["bonus"] += _safe_int(_payload_value(event["payload"], "damage", "boost_bonus", default=0))

    for col, header in enumerate(["hero_name", "attack_name", "uses", "final_damage_total", "burn_total", "boost_bonus_total"]):
        attacks_ws.write(0, col, header, header_fmt)
    attack_rows = sorted(attack_stats.items(), key=lambda item: (-item[1]["count"], item[0][0], item[0][1]))
    for row_idx, ((hero, attack), stats) in enumerate(attack_rows, start=1):
        attacks_ws.write(row_idx, 0, hero, cell_fmt)
        attacks_ws.write(row_idx, 1, attack, cell_fmt)
        attacks_ws.write(row_idx, 2, stats["count"], num_fmt)
        attacks_ws.write(row_idx, 3, stats["damage"], num_fmt)
        attacks_ws.write(row_idx, 4, stats["burn"], num_fmt)
        attacks_ws.write(row_idx, 5, stats["bonus"], num_fmt)
    attacks_ws.set_column("A:B", 24)
    attacks_ws.set_column("C:F", 16)

    upgrade_stats: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"count": 0, "amount": 0})
    for event in upgrade_events:
        hero = event["hero_name"]
        upgrade_type = str(_payload_value(event["payload"], "upgrade_type", default="")).strip()
        attack_name = str(_payload_value(event["payload"], "upgrade_attack_name", default="")).strip()
        if not hero:
            continue
        bucket = upgrade_stats[(hero, upgrade_type or "-", attack_name or "-")]
        bucket["count"] += 1
        bucket["amount"] += _safe_int(_payload_value(event["payload"], "upgrade_amount", default=0))

    for col, header in enumerate(["hero_name", "upgrade_type", "attack_name", "count", "total_amount"]):
        upgrades_ws.write(0, col, header, header_fmt)
    upgrade_rows = sorted(upgrade_stats.items(), key=lambda item: (-item[1]["count"], item[0][0], item[0][1], item[0][2]))
    for row_idx, ((hero, upgrade_type, attack_name), stats) in enumerate(upgrade_rows, start=1):
        upgrades_ws.write(row_idx, 0, hero, cell_fmt)
        upgrades_ws.write(row_idx, 1, upgrade_type, cell_fmt)
        upgrades_ws.write(row_idx, 2, attack_name, cell_fmt)
        upgrades_ws.write(row_idx, 3, stats["count"], num_fmt)
        upgrades_ws.write(row_idx, 4, stats["amount"], num_fmt)
    upgrades_ws.set_column("A:C", 24)
    upgrades_ws.set_column("D:E", 14)

    admin_counter = Counter(event["event_type"] for event in admin_events if event["event_type"])
    for col, header in enumerate(["event_type", "count"]):
        admin_ws.write(0, col, header, header_fmt)
    for row_idx, (name, count) in enumerate(admin_counter.most_common(), start=1):
        admin_ws.write(row_idx, 0, name, cell_fmt)
        admin_ws.write(row_idx, 1, count, num_fmt)
    admin_ws.set_column("A:A", 32)
    admin_ws.set_column("B:B", 12)

    lifecycle_counter = Counter(event["event_type"] for event in lifecycle_events if event["event_type"])
    lifecycle_by_day: dict[tuple[str, str], int] = Counter(
        (_date_text(event["created_at"]), event["event_type"]) for event in lifecycle_events
    )
    for col, header in enumerate(["event_type", "count", "day", "daily_count", "event_type_day"]):
        lifecycle_ws.write(0, col, header, header_fmt)
    row_idx = 1
    for name, count in lifecycle_counter.most_common():
        lifecycle_ws.write(row_idx, 0, name, cell_fmt)
        lifecycle_ws.write(row_idx, 1, count, num_fmt)
        row_idx += 1
    day_start = row_idx + 1
    for day, name in sorted(lifecycle_by_day):
        lifecycle_ws.write(row_idx, 2, day, cell_fmt)
        lifecycle_ws.write(row_idx, 3, lifecycle_by_day[(day, name)], num_fmt)
        lifecycle_ws.write(row_idx, 4, name, cell_fmt)
        row_idx += 1
    lifecycle_ws.set_column("A:E", 20)

    charts_ws.write("A1", "Diagrammdaten", title_fmt)

    def _write_chart_table(start_col: int, title: str, rows: list[tuple[str, int]]) -> tuple[int, int]:
        charts_ws.write(1, start_col, title, sub_header_fmt)
        charts_ws.write(2, start_col, "Label", header_fmt)
        charts_ws.write(2, start_col + 1, "Wert", header_fmt)
        write_row = 3
        for label, value in rows:
            charts_ws.write(write_row, start_col, label, cell_fmt)
            charts_ws.write(write_row, start_col + 1, value, num_fmt)
            write_row += 1
        return 3, max(3, write_row - 1)

    hero_chart_range = _write_chart_table(0, "Helden-Nutzung", top_hero_counter.most_common(10))
    attack_chart_range = _write_chart_table(3, "Attacken-Nutzung", top_attack_counter.most_common(10))
    upgrade_chart_range = _write_chart_table(6, "Upgrade-Verteilung", top_upgrade_counter.most_common(10))
    admin_chart_range = _write_chart_table(9, "Admin-Aktionen", admin_counter.most_common(10))
    lifecycle_chart_range = _write_chart_table(12, "Lifecycle", lifecycle_counter.most_common(10))

    def _insert_column_chart(name: str, start_col: int, data_range: tuple[int, int], target_cell: str) -> None:
        start_row, end_row = data_range
        chart = workbook.add_chart({"type": "column"})
        chart.add_series(
            {
                "name": name,
                "categories": ["Charts", start_row, start_col, end_row, start_col],
                "values": ["Charts", start_row, start_col + 1, end_row, start_col + 1],
            }
        )
        chart.set_title({"name": name})
        chart.set_legend({"none": True})
        charts_ws.insert_chart(target_cell, chart, {"x_scale": 1.2, "y_scale": 1.2})

    _insert_column_chart("Helden-Nutzung", 0, hero_chart_range, "A20")
    _insert_column_chart("Attacken-Nutzung", 3, attack_chart_range, "J20")
    _insert_column_chart("Upgrade-Verteilung", 6, upgrade_chart_range, "A38")
    _insert_column_chart("Admin-Aktionen", 9, admin_chart_range, "J38")
    _insert_column_chart("Lifecycle", 12, lifecycle_chart_range, "A56")
    charts_ws.set_column("A:N", 22)

    workbook.close()
    return output.getvalue(), filename
