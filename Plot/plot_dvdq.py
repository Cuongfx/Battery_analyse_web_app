from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from _batteryml_plot_common import (
    DEFAULT_DATA_DIR,
    add_three_panel_axis_args,
    build_dvdq_curve,
    build_metric_curves,
    build_three_panel_ranges,
    choose_cycle_indices,
    choose_plot_targets,
    group_files_by_subfolder,
    load_batteryml_pickle,
    plot_three_panel_metric,
    plot_three_panel_metric_dashboard,
    resolve_existing_path,
    _resolve_cycle_indices_silent,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot current vs time, voltage vs time, and dV/dQ vs voltage from a BatteryML .pkl file."
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="BatteryML dataset root")
    parser.add_argument("--pkl-file", default=None, help="Direct path to one .pkl file")
    parser.add_argument("--subfolder", default=None, help="Optional subfolder under --data-dir")
    parser.add_argument("--file-name", default=None, help="Optional .pkl filename or stem")
    parser.add_argument(
        "--all-subfolders-together",
        action="store_true",
        help="One file per subfolder, plotted together in one dashboard",
    )
    parser.add_argument(
        "--all-subfolders-all-files",
        action="store_true",
        help="ALL files from every subfolder — each subfolder gets its own panel with all its files overlaid",
    )
    parser.add_argument("--cycles", default=None, help="Cycle indices like 0,1,2 or 'all'")
    parser.add_argument("--min-dq", type=float, default=1e-5, help="Minimum |delta Q| for dV/dQ")
    parser.add_argument("--save", default=None, help="Optional output image path")
    add_three_panel_axis_args(parser)
    args = parser.parse_args()

    data_dir = resolve_existing_path(args.data_dir)
    save_path = Path(args.save).expanduser() if args.save else None
    current_range, voltage_range, metric_range = build_three_panel_ranges(args)

    chosen_files, combined_mode, all_files_mode = choose_plot_targets(
        data_dir,
        args.pkl_file,
        args.subfolder,
        args.file_name,
        args.all_subfolders_together,
        args.all_subfolders_all_files,
    )
    for path in chosen_files:
        print(f"\nSelected file: {path}")

    # ── Mode 3: all files per subfolder ──────────────────────────────────────
    if all_files_mode:
        subfolder_groups = group_files_by_subfolder(chosen_files)
        curves_by_subfolder: list[tuple[str, list]] = []
        for subfolder_name, paths in subfolder_groups:
            all_curves = []
            for path in paths:
                obj = load_batteryml_pickle(path)
                indices = _resolve_cycle_indices_silent(len(obj.get("cycle_data", [])), args.cycles)
                curves = build_metric_curves(
                    obj, indices,
                    builder=lambda series: build_dvdq_curve(series, min_dq=args.min_dq),
                    source_label=path.stem,
                )
                all_curves.extend(curves)
            if all_curves:
                curves_by_subfolder.append((subfolder_name, all_curves))

        plot_three_panel_metric_dashboard(
            curves_by_subfolder,
            metric_title="dV/dQ vs Voltage",
            metric_ylabel="dV/dQ (V/Ah)",
            current_range=current_range,
            voltage_range=voltage_range,
            metric_range=metric_range,
            save_path=save_path,
            color_by_source=True,
        )
        plt.show()
        return

    objects = [(path, load_batteryml_pickle(path)) for path in chosen_files]
    cycle_count = min(len(obj.get("cycle_data", [])) for _, obj in objects)
    cycle_indices = choose_cycle_indices(cycle_count, args.cycles)

    # ── Mode 2: one file per subfolder dashboard ──────────────────────────────
    if combined_mode:
        curves_by_source = []
        for path, obj in objects:
            curves = build_metric_curves(
                obj, cycle_indices,
                builder=lambda series: build_dvdq_curve(series, min_dq=args.min_dq),
            )
            if curves:
                curves_by_source.append((path.parent.name, curves))

        plot_three_panel_metric_dashboard(
            curves_by_source,
            metric_title="dV/dQ vs Voltage",
            metric_ylabel="dV/dQ (V/Ah)",
            current_range=current_range,
            voltage_range=voltage_range,
            metric_range=metric_range,
            save_path=save_path,
        )
        plt.show()
        return

    # ── Mode 1: single file ───────────────────────────────────────────────────
    chosen_path, chosen_obj = objects[0]
    curves = build_metric_curves(
        chosen_obj, cycle_indices,
        builder=lambda series: build_dvdq_curve(series, min_dq=args.min_dq),
    )
    plot_three_panel_metric(
        curves,
        cell_name=chosen_path.stem,
        metric_title="dV/dQ vs Voltage",
        metric_ylabel="dV/dQ (V/Ah)",
        current_range=current_range,
        voltage_range=voltage_range,
        metric_range=metric_range,
        save_path=save_path,
    )
    plt.show()


if __name__ == "__main__":
    main()
