from __future__ import annotations

from pathlib import Path

try:
    import xlsxwriter
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("xlsxwriter is required for simulation export") from exc

from simulation.config import DEFAULT_MATRIX_OUTPUT, DEFAULT_OUTPUT, MODE_SHEET_NAMES
from simulation.reporting import build_mode_report


def _write_ranking_sheet(workbook, sheet, rows) -> None:
    header_fmt = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1})
    cell_fmt = workbook.add_format({"border": 1})
    percent_fmt = workbook.add_format({"border": 1, "num_format": "0.00%"})
    headers = ["Rang", "Held", "Wins", "Losses", "Winrate %"]
    for column, header in enumerate(headers):
        sheet.write(0, column, header, header_fmt)
    for row_index, row in enumerate(rows, start=1):
        sheet.write(row_index, 0, row.rank, cell_fmt)
        sheet.write(row_index, 1, row.hero, cell_fmt)
        sheet.write(row_index, 2, row.wins, cell_fmt)
        sheet.write(row_index, 3, row.losses, cell_fmt)
        sheet.write(row_index, 4, row.winrate, percent_fmt)
    sheet.freeze_panes(1, 0)
    sheet.set_column("A:A", 10)
    sheet.set_column("B:B", 24)
    sheet.set_column("C:D", 12)
    sheet.set_column("E:E", 14)


def export_main_workbook(playstyle_result, output_path: str | None = None) -> str:
    target = Path(output_path or DEFAULT_OUTPUT)
    workbook = xlsxwriter.Workbook(str(target))
    try:
        for mode in playstyle_result.mode_results:
            report = build_mode_report(playstyle_result.mode_results[mode])
            sheet = workbook.add_worksheet(MODE_SHEET_NAMES[mode])
            _write_ranking_sheet(workbook, sheet, report.rows)
    finally:
        workbook.close()
    return str(target)


def export_matrix_workbook(playstyle_result, output_path: str | None = None) -> str:
    target = Path(output_path or DEFAULT_MATRIX_OUTPUT)
    workbook = xlsxwriter.Workbook(str(target))
    try:
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#EAEAEA", "border": 1})
        percent_fmt = workbook.add_format({"border": 1, "num_format": "0.00%"})
        cell_fmt = workbook.add_format({"border": 1})
        for mode in playstyle_result.mode_results:
            report = build_mode_report(playstyle_result.mode_results[mode])
            sheet = workbook.add_worksheet(MODE_SHEET_NAMES[mode])
            heroes = list(report.matrix.keys())
            sheet.write(0, 0, "Held", header_fmt)
            for column, hero in enumerate(heroes, start=1):
                sheet.write(0, column, hero, header_fmt)
            for row_index, hero in enumerate(heroes, start=1):
                sheet.write(row_index, 0, hero, header_fmt)
                for column_index, opponent in enumerate(heroes, start=1):
                    value = report.matrix[hero][opponent]
                    if value is None:
                        sheet.write(row_index, column_index, "", cell_fmt)
                    else:
                        sheet.write(row_index, column_index, value, percent_fmt)
            sheet.freeze_panes(1, 1)
            sheet.set_column(0, 0, 24)
            sheet.set_column(1, len(heroes), 14)
    finally:
        workbook.close()
    return str(target)
