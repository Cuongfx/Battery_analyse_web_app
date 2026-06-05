from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from _batteryml_plot_common import (
    AxisRange,
    DEFAULT_DATA_DIR,
    choose_plot_targets,
    compute_qdmax_by_cycle,
    group_files_by_subfolder,
    load_batteryml_pickle,
    plot_qdmax_all_files_dashboard,
    plot_qdmax_dashboard,
    plot_qdmax_vs_cycle,
    resolve_existing_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot maximum discharge capacity (Qdmax) vs cycle from a BatteryML .pkl file."
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="BatteryML dataset root")
    parser.add_argument("--pkl-file", default=None, help="Direct path to one .pkl file")
    parser.add_argument("--subfolder", default=None, help="Optional subfolder under --data-dir")
    parser.add_argument("--file-name", default=None, help="Optional .pkl filename or stem")
    parser.add_argument(
        "--all-subfolders-together",
        action="store_true",
        help="One file per subfolder, each in its own panel",
    )
    parser.add_argument(
        "--all-subfolders-all-files",
        action="store_true",
        help="ALL files from every subfolder — each subfolder panel overlays all its files",
    )
    parser.add_argument("--save", default=None, help="Optional output image path")
    parser.add_argument("--x-min", type=float, default=None)
    parser.add_argument("--x-max", type=float, default=None)
    parser.add_argument("--y-min", type=float, default=None)
    parser.add_argument("--y-max", type=float, default=None)
    args = parser.parse_args()

    data_dir = resolve_existing_path(args.data_dir)
    save_path = Path(args.save).expanduser() if args.save else None
    axis_range = AxisRange(x_min=args.x_min, x_max=args.x_max, y_min=args.y_min, y_max=args.y_max)

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
        subfolder_groups_raw = group_files_by_subfolder(chosen_files)
        subfolder_groups = []
        for subfolder_name, paths in subfolder_groups_raw:
            file_entries = []
            for path in paths:
                try:
                    obj = load_batteryml_pickle(path)
                    cycle_axis, qdmax_values = compute_qdmax_by_cycle(obj)
                    file_entries.append((path.stem, cycle_axis, qdmax_values))
                except Exception as exc:
                    print(f"  Skipping {path.name}: {exc}")
            if file_entries:
                subfolder_groups.append((subfolder_name, file_entries))

        plot_qdmax_all_files_dashboard(
            subfolder_groups,
            title="Qdmax vs Cycle  |  All Subfolders — All Files",
            axis_range=axis_range,
            save_path=save_path,
        )
        plt.show()
        return

    # ── Mode 2: one file per subfolder dashboard ──────────────────────────────
    if combined_mode:
        datasets = []
        for path in chosen_files:
            obj = load_batteryml_pickle(path)
            cycle_axis, qdmax_values = compute_qdmax_by_cycle(obj)
            datasets.append((path.parent.name, cycle_axis, qdmax_values))
        plot_qdmax_dashboard(
            datasets=datasets,
            title="Qdmax vs Cycle  |  All Selected Subfolders",
            axis_range=axis_range,
            save_path=save_path,
        )
        plt.show()
        return

    # ── Mode 1: single file ───────────────────────────────────────────────────
    chosen_path = chosen_files[0]
    obj = load_batteryml_pickle(chosen_path)
    cycle_axis, qdmax_values = compute_qdmax_by_cycle(obj)
    plot_qdmax_vs_cycle(
        cycle_axis=cycle_axis,
        qdmax_values=qdmax_values,
        cell_name=chosen_path.parent.name,
        axis_range=axis_range,
        save_path=save_path,
    )
    plt.show()


if __name__ == "__main__":
    main()
