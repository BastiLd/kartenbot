from __future__ import annotations

import argparse
from pathlib import Path

from simulation.config import DEFAULT_AVERAGE_MISTAKE_RATE, DEFAULT_MATRIX_OUTPUT, DEFAULT_OUTPUT, Playstyle, modes_from_flag


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless 1v1-Simulation fuer den Kartenbot")
    parser.add_argument("--mode", default="all", choices=["original", "light", "max", "all"])
    parser.add_argument("--playstyle", default="optimal", choices=["optimal", "average", "both"])
    parser.add_argument("--fights-per-matchup", type=int, default=200)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--include-matrix", action="store_true")
    parser.add_argument("--matrix-output", default=None)
    parser.add_argument("--average-mistake-rate", type=float, default=DEFAULT_AVERAGE_MISTAKE_RATE)
    return parser.parse_args(argv)


def _derived_output_path(base_output: str | None, suffix: str | None, default_name: str) -> str:
    path = Path(base_output or default_name)
    if suffix is None:
        return str(path)
    return str(path.with_name(f"{path.stem}_{suffix}{path.suffix or '.xlsx'}"))


def run_cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("Starte Simulation...", flush=True)
    print("Importiere Karten-Loader...", flush=True)

    from simulation.loader import load_base_runtime_cards

    print("Baue Runtime-Karten...", flush=True)
    cards = load_base_runtime_cards()
    print(f"Lade {len(cards)} Helden...", flush=True)

    print("Importiere Simulations-Engine...", flush=True)
    from simulation.engine import simulate_playstyle_batch

    print("Importiere Excel-Export...", flush=True)
    from simulation.excel_export import export_main_workbook, export_matrix_workbook

    modes = modes_from_flag(args.mode)
    print("Berechne Matchups...", flush=True)
    batch = simulate_playstyle_batch(
        cards,
        modes,
        args.fights_per_matchup,
        playstyle=Playstyle(args.playstyle),
        seed=args.seed,
        average_mistake_rate=args.average_mistake_rate,
        progress_callback=lambda message: print(message, flush=True),
    )
    multi_output = len(batch.results) > 1
    for playstyle_name, result in batch.results.items():
        suffix = playstyle_name if multi_output else None
        output_path = _derived_output_path(args.output, suffix, DEFAULT_OUTPUT)
        main_output = export_main_workbook(result, output_path=output_path)
        print(f"Gespeichert: {main_output}", flush=True)
        if args.include_matrix:
            matrix_output = _derived_output_path(args.matrix_output, suffix, DEFAULT_MATRIX_OUTPUT)
            matrix_path = export_matrix_workbook(result, output_path=matrix_output)
            print(f"Gespeichert: {matrix_path}", flush=True)
    return 0
