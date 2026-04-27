from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import RangeSlider

from batteryml_data import (
    MODERN_COLORS,
    DischargeSeries,
    MetricCurve,
    _resolve_cycle_indices_silent,
    build_charge_metric_curves,
    build_dqdv_curve,
    build_dvdq_curve,
    build_metric_curves,
    build_qd_vs_voltage_curve,
    compute_qcmax_by_cycle,
    compute_qdmax_by_cycle,
    extract_charge_series,
    extract_discharge_series,
    get_cycle_data,
    load_batteryml_pickle,
    parse_cycle_csv,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "DATA" / "BatteryML"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "axes.facecolor": "#F7F8FC",
        "figure.facecolor": "#FFFFFF",
        "axes.edgecolor": "#CCCCCC",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": "#E4E7EF",
        "grid.linestyle": "-",
        "axes.grid": True,
        "grid.alpha": 1.0,
        "axes.linewidth": 0.8,
        "lines.linewidth": 1.6,
        "lines.antialiased": True,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
    }
)


@dataclass
class AxisRange:
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None


@dataclass
class SliderBundle:
    host_ax: Any
    x_slider_ax: Any
    y_slider_ax: Any
    x_slider: Any
    y_slider: Any


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _source_color(idx: int) -> str:
    return MODERN_COLORS[idx % len(MODERN_COLORS)]


def _cycle_colormap() -> Any:
    return plt.colormaps["plasma"]


def _line_colors(curves: list[MetricCurve], *, color_by_source: bool) -> list[Any]:
    if color_by_source:
        unique = list(dict.fromkeys(c.source_label or "" for c in curves))
        src_map = {s: MODERN_COLORS[i % len(MODERN_COLORS)] for i, s in enumerate(unique)}
        return [src_map.get(c.source_label or "", MODERN_COLORS[0]) for c in curves]
    if len(curves) == 1:
        return [MODERN_COLORS[0]]
    cmap = _cycle_colormap()
    ids = [c.cycle_idx for c in curves]
    lo, hi = min(ids), max(ids)
    norm = plt.Normalize(vmin=lo, vmax=max(lo + 1e-9, hi))
    return [cmap(norm(c.cycle_idx)) for c in curves]


def _copy_axis_range(axis_range: AxisRange | None) -> AxisRange:
    axis_range = axis_range or AxisRange()
    return AxisRange(
        x_min=axis_range.x_min,
        x_max=axis_range.x_max,
        y_min=axis_range.y_min,
        y_max=axis_range.y_max,
    )


# ---------------------------------------------------------------------------
# Toggle / interactive helpers
# ---------------------------------------------------------------------------


def _attach_group_toggle_handler(
    fig: Any,
    grouped_lines: dict[str, list[Any]],
    title_updaters: list[Callable[[str | None], None]],
) -> None:
    if len(grouped_lines) <= 1:
        return

    for lines in grouped_lines.values():
        for line in lines:
            line.set_picker(5)

    fig._isolated_group_label = None

    def _apply_visibility(active_label: str | None) -> None:
        for label, lines in grouped_lines.items():
            visible = active_label is None or label == active_label
            for line in lines:
                line.set_visible(visible)
        for update_title in title_updaters:
            update_title(active_label)
        fig.canvas.draw_idle()

    def _on_pick(event: Any) -> None:
        mouse_event = getattr(event, "mouseevent", None)
        artist = getattr(event, "artist", None)
        if mouse_event is None or not getattr(mouse_event, "dblclick", False):
            return
        if artist is None:
            return

        picked_label = None
        for label, lines in grouped_lines.items():
            if artist in lines:
                picked_label = label
                break
        if picked_label is None:
            return

        current_label = getattr(fig, "_isolated_group_label", None)
        next_label = None if current_label == picked_label else picked_label
        fig._isolated_group_label = next_label
        _apply_visibility(next_label)

    fig.canvas.mpl_connect("pick_event", _on_pick)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def choose_from_list(items: list[str], title: str, *, allow_quit: bool = True) -> str:
    while True:
        print(f"\n{title}")
        for idx, item in enumerate(items, start=1):
            print(f"{idx:3d}. {item}")
        prompt = "\nEnter number"
        if allow_quit:
            prompt += " (or 'q' to quit)"
        prompt += ": "
        raw = input(prompt).strip().lower()
        if allow_quit and raw == "q":
            raise KeyboardInterrupt
        if raw.isdigit():
            picked = int(raw)
            if 1 <= picked <= len(items):
                return items[picked - 1]
        print("Invalid choice.")


def resolve_existing_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"Path not found: {resolved}")

    checked: list[Path] = []
    for root in (Path.cwd(), PROJECT_ROOT):
        resolved = (root / candidate).resolve()
        checked.append(resolved)
        if resolved.exists():
            return resolved

    checked_text = "\n".join(str(path) for path in checked)
    raise FileNotFoundError(f"Path not found. Checked:\n{checked_text}")


def list_dataset_subfolders(data_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in data_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".") and not path.name.startswith("._")
    )


def list_pkl_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix == ".pkl" and not path.name.startswith("._")
    )


def list_nonempty_subfolders(data_dir: Path) -> list[Path]:
    return [folder for folder in list_dataset_subfolders(data_dir) if list_pkl_files(folder)]


def group_files_by_subfolder(files: list[Path]) -> list[tuple[str, list[Path]]]:
    """Group a flat list of paths by their immediate parent folder name."""
    groups: dict[str, list[Path]] = {}
    for path in files:
        key = path.parent.name
        groups.setdefault(key, []).append(path)
    return [(name, sorted(paths)) for name, paths in sorted(groups.items())]


def choose_pkl_file(
    data_dir: Path,
    requested_pkl: str | None,
    requested_subfolder: str | None,
    requested_file_name: str | None,
) -> Path:
    if requested_pkl:
        path = resolve_existing_path(requested_pkl)
        if path.suffix != ".pkl":
            raise ValueError(f"Expected a .pkl file, got: {path}")
        return path

    subfolders = list_dataset_subfolders(data_dir)
    if requested_subfolder:
        subfolders = [folder for folder in subfolders if folder.name == requested_subfolder]
        if not subfolders:
            raise FileNotFoundError(f"Subfolder not found in {data_dir}: {requested_subfolder}")

    if not subfolders:
        raise FileNotFoundError(f"No subfolders found under: {data_dir}")

    chosen_subfolder = subfolders[0]
    if requested_subfolder is None and sys.stdin.isatty() and len(subfolders) > 1:
        labels = [folder.name for folder in subfolders]
        picked = choose_from_list(labels, f"Choose a subfolder in {data_dir}:")
        chosen_subfolder = data_dir / picked

    pkl_files = list_pkl_files(chosen_subfolder)
    if requested_file_name:
        for path in pkl_files:
            if path.name == requested_file_name or path.stem == requested_file_name:
                return path
        raise FileNotFoundError(f"No .pkl file named '{requested_file_name}' in {chosen_subfolder}")

    if not pkl_files:
        raise FileNotFoundError(f"No .pkl files found in {chosen_subfolder}")

    if not sys.stdin.isatty() or len(pkl_files) == 1:
        return pkl_files[0]

    labels = [path.name for path in pkl_files]
    picked_file = choose_from_list(labels, f"Choose one .pkl file in {chosen_subfolder.name}:")
    return chosen_subfolder / picked_file


def choose_plot_targets(
    data_dir: Path,
    requested_pkl: str | None,
    requested_subfolder: str | None,
    requested_file_name: str | None,
    requested_all_subfolders: bool = False,
    requested_all_files_per_subfolder: bool = False,
) -> tuple[list[Path], bool, bool]:
    """Return (files, combined_mode, all_files_per_subfolder_mode).

    combined_mode=True       → one file per subfolder dashboard
    all_files_per_subfolder  → ALL files from every subfolder; group with
                               group_files_by_subfolder() before plotting
    """
    if requested_pkl:
        path = resolve_existing_path(requested_pkl)
        if path.suffix != ".pkl":
            raise ValueError(f"Expected a .pkl file, got: {path}")
        return [path], False, False

    if requested_all_files_per_subfolder:
        subfolders = list_nonempty_subfolders(data_dir)
        if not subfolders:
            raise FileNotFoundError(f"No subfolders with .pkl files found under: {data_dir}")
        all_files: list[Path] = []
        for folder in subfolders:
            all_files.extend(list_pkl_files(folder))
        return all_files, False, True

    if requested_all_subfolders:
        subfolders = list_nonempty_subfolders(data_dir)
        if not subfolders:
            raise FileNotFoundError(f"No subfolders with .pkl files found under: {data_dir}")

        manual_pick_each = requested_file_name is not None
        if requested_file_name is None and sys.stdin.isatty():
            file_mode = choose_from_list(
                [
                    "Choose one file manually in each subfolder",
                    "Automatically use the first .pkl file in each subfolder",
                ],
                "Choose how to pick files across subfolders:",
            )
            manual_pick_each = file_mode.startswith("Choose one file manually")

        chosen_files: list[Path] = []
        for folder in subfolders:
            files = list_pkl_files(folder)
            if requested_file_name:
                matches = [p for p in files if p.name == requested_file_name or p.stem == requested_file_name]
                if not matches:
                    raise FileNotFoundError(f"No .pkl file named '{requested_file_name}' in {folder}")
                chosen_files.append(matches[0])
                continue
            if not manual_pick_each or not sys.stdin.isatty() or len(files) == 1:
                chosen_files.append(files[0])
                continue
            labels = [p.name for p in files]
            picked_file = choose_from_list(labels, f"Choose one .pkl file in {folder.name}:")
            chosen_files.append(folder / picked_file)
        return chosen_files, True, False

    if requested_subfolder is None and requested_file_name is None and sys.stdin.isatty():
        mode = choose_from_list(
            [
                "Single file — one subfolder",
                "One file per subfolder — compare across datasets",
                "All files per subfolder — full dataset overview",
            ],
            "Choose plotting mode:",
        )
        if mode.startswith("One file per subfolder"):
            return choose_plot_targets(
                data_dir=data_dir,
                requested_pkl=None,
                requested_subfolder=None,
                requested_file_name=None,
                requested_all_subfolders=True,
            )
        if mode.startswith("All files per subfolder"):
            return choose_plot_targets(
                data_dir=data_dir,
                requested_pkl=None,
                requested_subfolder=None,
                requested_file_name=None,
                requested_all_files_per_subfolder=True,
            )

    return [choose_pkl_file(data_dir, None, requested_subfolder, requested_file_name)], False, False


# ---------------------------------------------------------------------------
# Data loading / processing
# ---------------------------------------------------------------------------




def choose_cycle_indices(cycle_count: int, requested_cycles: str | None) -> list[int]:
    if cycle_count <= 0:
        raise ValueError("No cycles found in file.")

    def _resolve(raw_value: str) -> list[int]:
        if raw_value.strip().lower() in {"all", "*"}:
            return list(range(cycle_count))
        picked = parse_cycle_csv(raw_value)
        missing = [idx for idx in picked if not 0 <= idx < cycle_count]
        if missing:
            raise ValueError(f"Cycle index out of range: {missing}. Valid range: 0..{cycle_count - 1}")
        return picked

    if requested_cycles is not None:
        return _resolve(requested_cycles)

    print(f"\nCycles available: 0 to {cycle_count - 1}")
    print("Enter cycle indices like 0,1,2 or type 'all'.")
    while True:
        raw = input("Cycle selection: ").strip()
        if not raw:
            raw = "all"
        try:
            return _resolve(raw)
        except ValueError as exc:
            print(exc)





def _cycle_label(curve: MetricCurve) -> str:
    if curve.cycle_number is None:
        return f"cycle_idx {curve.cycle_idx}"
    return f"cycle_idx {curve.cycle_idx} | cycle {curve.cycle_number}"


def _finite_bounds(values: list[np.ndarray]) -> tuple[float, float] | None:
    finite_parts = [part[np.isfinite(part)] for part in values if part.size > 0]
    finite_parts = [part for part in finite_parts if part.size > 0]
    if not finite_parts:
        return None
    merged = np.concatenate(finite_parts)
    return float(np.min(merged)), float(np.max(merged))


def _normalize_range(low: float | None, high: float | None) -> tuple[float | None, float | None]:
    if low is not None and high is not None and low > high:
        return high, low
    return low, high


def _expand_if_flat(low: float, high: float) -> tuple[float, float]:
    if np.isclose(low, high):
        pad = max(1e-9, abs(low) * 0.05, 1.0)
        return low - pad, high + pad
    return low, high


def apply_axis_range(
    ax: Any,
    x_values: list[np.ndarray],
    y_values: list[np.ndarray],
    axis_range: AxisRange | None,
) -> None:
    if axis_range is None:
        axis_range = AxisRange()

    x_min, x_max = _normalize_range(axis_range.x_min, axis_range.x_max)
    y_min, y_max = _normalize_range(axis_range.y_min, axis_range.y_max)

    if x_min is None or x_max is None:
        x_bounds = _finite_bounds(x_values)
        if x_bounds is not None:
            x_min = x_bounds[0] if x_min is None else x_min
            x_max = x_bounds[1] if x_max is None else x_max
    if y_min is None or y_max is None:
        y_bounds = _finite_bounds(y_values)
        if y_bounds is not None:
            y_min = y_bounds[0] if y_min is None else y_min
            y_max = y_bounds[1] if y_max is None else y_max

    if x_min is not None and x_max is not None:
        ax.set_xlim(x_min, x_max)
    if y_min is not None and y_max is not None:
        ax.set_ylim(y_min, y_max)


def _resolved_axis_range(
    x_values: list[np.ndarray],
    y_values: list[np.ndarray],
    axis_range: AxisRange | None,
) -> tuple[tuple[float, float], tuple[float, float]]:
    axis_range = axis_range or AxisRange()
    x_min, x_max = _normalize_range(axis_range.x_min, axis_range.x_max)
    y_min, y_max = _normalize_range(axis_range.y_min, axis_range.y_max)

    x_bounds = _finite_bounds(x_values)
    y_bounds = _finite_bounds(y_values)
    if x_bounds is None or y_bounds is None:
        raise ValueError("Cannot determine axis bounds from empty or non-finite data.")

    x_min = x_bounds[0] if x_min is None else x_min
    x_max = x_bounds[1] if x_max is None else x_max
    y_min = y_bounds[0] if y_min is None else y_min
    y_max = y_bounds[1] if y_max is None else y_max
    return _expand_if_flat(x_min, x_max), _expand_if_flat(y_min, y_max)


# ---------------------------------------------------------------------------
# Range sliders
# ---------------------------------------------------------------------------


def _add_range_sliders(
    fig: Any,
    ax: Any,
    x_values: list[np.ndarray],
    y_values: list[np.ndarray],
    axis_range: AxisRange | None,
    *,
    x_label: str,
    y_label: str,
    x_pos: list[float],
    y_pos: list[float],
    line_refs: list[tuple[Any, np.ndarray, np.ndarray]] | None = None,
    on_limits_changed: Callable[[tuple[float, float], tuple[float, float]], None] | None = None,
) -> SliderBundle:
    x_bounds, y_bounds = _resolved_axis_range(x_values, y_values, axis_range)
    x_ax = fig.add_axes(x_pos)
    y_ax = fig.add_axes(y_pos)

    slider_color = MODERN_COLORS[0]

    x_slider = RangeSlider(
        ax=x_ax,
        label="",
        valmin=x_bounds[0],
        valmax=x_bounds[1],
        valinit=x_bounds,
        color=slider_color,
    )
    y_slider = RangeSlider(
        ax=y_ax,
        label="",
        valmin=y_bounds[0],
        valmax=y_bounds[1],
        valinit=y_bounds,
        color=slider_color,
    )

    # No labels or value text on sliders
    x_slider.label.set_visible(False)
    y_slider.label.set_visible(False)
    x_slider.valtext.set_visible(False)
    y_slider.valtext.set_visible(False)

    # Tiny axis captions so the user knows which slider is which
    x_ax.set_title("X range", fontsize=6.5, color="#AAAAAA", pad=1, loc="left")
    y_ax.set_title("Y range", fontsize=6.5, color="#AAAAAA", pad=1, loc="left")

    def _update(_: Any) -> None:
        x_min, x_max = x_slider.val
        y_min, y_max = y_slider.val
        if x_min > x_max:
            x_min, x_max = x_max, x_min
        if y_min > y_max:
            y_min, y_max = y_max, y_min

        if on_limits_changed is not None:
            on_limits_changed((x_min, x_max), (y_min, y_max))

        if line_refs:
            vis_x_min: float | None = None
            vis_x_max: float | None = None
            vis_y_min: float | None = None
            vis_y_max: float | None = None
            has_visible = False

            for line, x_data, y_data in line_refs:
                mask = (
                    np.isfinite(x_data)
                    & np.isfinite(y_data)
                    & (x_data >= x_min)
                    & (x_data <= x_max)
                    & (y_data >= y_min)
                    & (y_data <= y_max)
                )
                if np.any(mask):
                    sx, sy = x_data[mask], y_data[mask]
                    line.set_data(sx, sy)
                    xl, xh = float(np.min(sx)), float(np.max(sx))
                    yl, yh = float(np.min(sy)), float(np.max(sy))
                    vis_x_min = xl if vis_x_min is None else min(vis_x_min, xl)
                    vis_x_max = xh if vis_x_max is None else max(vis_x_max, xh)
                    vis_y_min = yl if vis_y_min is None else min(vis_y_min, yl)
                    vis_y_max = yh if vis_y_max is None else max(vis_y_max, yh)
                    has_visible = True
                else:
                    line.set_data([], [])

            if has_visible:
                xp = max(1e-9, (vis_x_max - vis_x_min) * 0.03)
                yp = max(1e-9, (vis_y_max - vis_y_min) * 0.05)
                ax.set_xlim(max(x_min, vis_x_min - xp), min(x_max, vis_x_max + xp))
                ax.set_ylim(max(y_min, vis_y_min - yp), min(y_max, vis_y_max + yp))
                fig.canvas.draw_idle()
                return

        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)
        fig.canvas.draw_idle()

    x_slider.on_changed(_update)
    y_slider.on_changed(_update)

    if not hasattr(fig, "_range_slider_refs"):
        fig._range_slider_refs = []
    fig._range_slider_refs.extend([x_slider, y_slider])
    bundle = SliderBundle(
        host_ax=ax,
        x_slider_ax=x_ax,
        y_slider_ax=y_ax,
        x_slider=x_slider,
        y_slider=y_slider,
    )
    if not hasattr(fig, "_range_slider_bundles"):
        fig._range_slider_bundles = []
    fig._range_slider_bundles.append(bundle)
    return bundle


def _place_sliders_below_axis(
    ax: Any,
    *,
    x_height: float = 0.015,
    y_height: float = 0.015,
    gap: float = 0.016,
    label_clearance: float = 0.020,
) -> tuple[list[float], list[float]]:
    fig = ax.figure
    renderer = fig.canvas.get_renderer()
    pos = ax.get_position()
    tight_bbox = ax.get_tightbbox(renderer)
    tight_bbox_fig = tight_bbox.transformed(fig.transFigure.inverted())
    width = pos.width
    left = pos.x0
    x_bottom = max(0.02, tight_bbox_fig.y0 - label_clearance - x_height)
    y_bottom = max(0.005, x_bottom - gap - y_height)
    return [left, x_bottom, width, x_height], [left, y_bottom, width, y_height]


def _compute_dashboard_grid(count: int, fig_width: float) -> tuple[int, int]:
    if count <= 1:
        return 1, 1
    desired_panel_width = 5.8
    cols = max(1, min(count, int(fig_width / desired_panel_width)))
    if cols == 1 and count > 1:
        cols = 2
    cols = min(cols, 3)
    rows = math.ceil(count / cols)
    return rows, cols


def _attach_slider_resize_handler(fig: Any) -> None:
    if getattr(fig, "_range_slider_resize_connected", False):
        return

    def _on_resize(_: Any) -> None:
        for bundle in getattr(fig, "_range_slider_bundles", []):
            x_pos, y_pos = _place_sliders_below_axis(bundle.host_ax)
            bundle.x_slider_ax.set_position(x_pos)
            bundle.y_slider_ax.set_position(y_pos)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("resize_event", _on_resize)
    fig._range_slider_resize_connected = True


def _attach_axis_limit_change_handler(
    ax: Any,
    on_limits_changed: Callable[[tuple[float, float], tuple[float, float]], None] | None,
) -> None:
    if on_limits_changed is None:
        return

    def _handle(_: Any) -> None:
        x_low, x_high = ax.get_xlim()
        y_low, y_high = ax.get_ylim()
        on_limits_changed((float(x_low), float(x_high)), (float(y_low), float(y_high)))

    ax.callbacks.connect("xlim_changed", _handle)
    ax.callbacks.connect("ylim_changed", _handle)


def add_three_panel_axis_args(parser: Any) -> None:
    parser.add_argument("--current-x-min", type=float, default=None)
    parser.add_argument("--current-x-max", type=float, default=None)
    parser.add_argument("--current-y-min", type=float, default=None)
    parser.add_argument("--current-y-max", type=float, default=None)
    parser.add_argument("--voltage-x-min", type=float, default=None)
    parser.add_argument("--voltage-x-max", type=float, default=None)
    parser.add_argument("--voltage-y-min", type=float, default=None)
    parser.add_argument("--voltage-y-max", type=float, default=None)
    parser.add_argument("--metric-x-min", type=float, default=None)
    parser.add_argument("--metric-x-max", type=float, default=None)
    parser.add_argument("--metric-y-min", type=float, default=None)
    parser.add_argument("--metric-y-max", type=float, default=None)


def build_three_panel_ranges(args: Any) -> tuple[AxisRange, AxisRange, AxisRange]:
    return (
        AxisRange(
            x_min=args.current_x_min,
            x_max=args.current_x_max,
            y_min=args.current_y_min,
            y_max=args.current_y_max,
        ),
        AxisRange(
            x_min=args.voltage_x_min,
            x_max=args.voltage_x_max,
            y_min=args.voltage_y_min,
            y_max=args.voltage_y_max,
        ),
        AxisRange(
            x_min=args.metric_x_min,
            x_max=args.metric_x_max,
            y_min=args.metric_y_min,
            y_max=args.metric_y_max,
        ),
    )


def _reset_interactive_figure(fig: Any) -> None:
    fig.clf()
    fig._range_slider_refs = []
    fig._range_slider_bundles = []


# ---------------------------------------------------------------------------
# Three-panel drawing
# ---------------------------------------------------------------------------


def _draw_three_panel_detail(
    fig: Any,
    curves: list[MetricCurve],
    cell_name: str,
    metric_title: str,
    metric_ylabel: str,
    current_range: AxisRange | None = None,
    voltage_range: AxisRange | None = None,
    metric_range: AxisRange | None = None,
    on_current_changed: Callable[[tuple[float, float], tuple[float, float]], None] | None = None,
    on_voltage_changed: Callable[[tuple[float, float], tuple[float, float]], None] | None = None,
    on_metric_changed: Callable[[tuple[float, float], tuple[float, float]], None] | None = None,
    color_by_source: bool = False,
) -> tuple[list[Any], list[SliderBundle]]:
    if not curves:
        raise ValueError("No valid curves available to plot.")

    _reset_interactive_figure(fig)
    fig.set_size_inches(12, 13.5, forward=True)
    axes = fig.subplots(3, 1)
    fig.subplots_adjust(left=0.08, right=0.92, top=0.95, bottom=0.09, hspace=0.72)

    colors = _line_colors(curves, color_by_source=color_by_source)
    current_lines: list[tuple[Any, np.ndarray, np.ndarray]] = []
    voltage_lines: list[tuple[Any, np.ndarray, np.ndarray]] = []
    metric_lines: list[tuple[Any, np.ndarray, np.ndarray]] = []

    # Track one representative line per source label for legend (multi-source only)
    source_line_map: dict[str, Any] = {}

    for curve, color in zip(curves, colors):
        label = curve.source_label or _cycle_label(curve)
        lw = 1.4
        alpha = 0.85
        cl, = axes[0].plot(curve.time_s, curve.current_a, color=color, linewidth=lw, alpha=alpha)
        vl, = axes[1].plot(curve.time_s, curve.voltage_v, color=color, linewidth=lw, alpha=alpha)
        ml, = axes[2].plot(curve.metric_x, curve.metric_y, color=color, linewidth=lw, alpha=alpha)
        current_lines.append((cl, curve.time_s, curve.current_a))
        voltage_lines.append((vl, curve.time_s, curve.voltage_v))
        metric_lines.append((ml, curve.metric_x, curve.metric_y))
        if color_by_source and curve.source_label and curve.source_label not in source_line_map:
            source_line_map[curve.source_label] = ml

    axes[0].set_title(f"Current vs Time  |  {cell_name}")
    axes[0].set_xlabel("Time (s)")
    axes[0].set_ylabel("Current (A)")
    axes[1].set_title(f"Voltage vs Time  |  {cell_name}")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Voltage (V)")
    axes[2].set_title(f"{metric_title}  |  {cell_name}")
    axes[2].set_xlabel("Voltage (V)")
    axes[2].set_ylabel(metric_ylabel)

    # Show legend only when colours represent distinct files
    if color_by_source and source_line_map:
        for src_label, line in source_line_map.items():
            line.set_label(src_label)
        axes[2].legend(
            loc="best",
            fontsize=7.5,
            framealpha=0.92,
            edgecolor="#CCCCCC",
            fancybox=False,
        )

    apply_axis_range(
        axes[0],
        x_values=[c.time_s for c in curves],
        y_values=[c.current_a for c in curves],
        axis_range=current_range,
    )
    apply_axis_range(
        axes[1],
        x_values=[c.time_s for c in curves],
        y_values=[c.voltage_v for c in curves],
        axis_range=voltage_range,
    )
    apply_axis_range(
        axes[2],
        x_values=[c.metric_x for c in curves],
        y_values=[c.metric_y for c in curves],
        axis_range=metric_range,
    )

    fig.canvas.draw()
    cur_x_pos, cur_y_pos = _place_sliders_below_axis(axes[0])
    volt_x_pos, volt_y_pos = _place_sliders_below_axis(axes[1])
    met_x_pos, met_y_pos = _place_sliders_below_axis(axes[2], x_height=0.02, y_height=0.02)

    current_bundle = _add_range_sliders(
        fig, axes[0],
        x_values=[c.time_s for c in curves],
        y_values=[c.current_a for c in curves],
        axis_range=current_range,
        x_label="", y_label="",
        x_pos=cur_x_pos, y_pos=cur_y_pos,
        line_refs=current_lines, on_limits_changed=on_current_changed,
    )
    voltage_bundle = _add_range_sliders(
        fig, axes[1],
        x_values=[c.time_s for c in curves],
        y_values=[c.voltage_v for c in curves],
        axis_range=voltage_range,
        x_label="", y_label="",
        x_pos=volt_x_pos, y_pos=volt_y_pos,
        line_refs=voltage_lines, on_limits_changed=on_voltage_changed,
    )
    metric_bundle = _add_range_sliders(
        fig, axes[2],
        x_values=[c.metric_x for c in curves],
        y_values=[c.metric_y for c in curves],
        axis_range=metric_range,
        x_label="", y_label="",
        x_pos=met_x_pos, y_pos=met_y_pos,
        line_refs=metric_lines, on_limits_changed=on_metric_changed,
    )
    _attach_axis_limit_change_handler(axes[0], on_current_changed)
    _attach_axis_limit_change_handler(axes[1], on_voltage_changed)
    _attach_axis_limit_change_handler(axes[2], on_metric_changed)
    _attach_slider_resize_handler(fig)

    return list(axes), [current_bundle, voltage_bundle, metric_bundle]


def _draw_three_panel_overview(
    fig: Any,
    curves_by_source: list[tuple[str, list[MetricCurve]]],
    metric_title: str,
    metric_ylabel: str,
    current_range: AxisRange | None = None,
    voltage_range: AxisRange | None = None,
    metric_range: AxisRange | None = None,
    color_by_source: bool = False,
    preserve_size: bool = False,
) -> dict[Any, tuple[str, str]]:
    if not curves_by_source:
        raise ValueError("No grouped curves available to plot.")

    _reset_interactive_figure(fig)
    count = len(curves_by_source)

    if not preserve_size:
        init_cols = 1 if count == 1 else 2 if count <= 4 else 3
        init_rows = math.ceil(count / init_cols)
        fig.set_size_inches(max(12, init_cols * 5.4), max(8, init_rows * 6.0), forward=True)

    # Reflow columns based on the actual (possibly user-resized) figure width
    fig_w = fig.get_figwidth()
    cols = max(1, min(count, int(fig_w / 5.0)))
    cols = min(cols, 5)
    if cols == 1 and count > 1:
        cols = 2
    rows = math.ceil(count / cols)
    outer = fig.add_gridspec(rows, cols, left=0.05, right=0.98, top=0.96, bottom=0.05, wspace=0.22, hspace=0.22)

    axis_to_label: dict[Any, tuple[str, str]] = {}
    for idx, (source_label, curves) in enumerate(curves_by_source):
        outer_row = idx // cols
        outer_col = idx % cols
        inner = outer[outer_row, outer_col].subgridspec(3, 1, hspace=0.20)
        axes = [fig.add_subplot(inner[row, 0]) for row in range(3)]

        colors = _line_colors(curves, color_by_source=color_by_source)
        source_line_map: dict[str, Any] = {}

        for curve, color in zip(curves, colors):
            lw, alpha = 1.2, 0.85
            axes[0].plot(curve.time_s, curve.current_a, color=color, linewidth=lw, alpha=alpha)
            axes[1].plot(curve.time_s, curve.voltage_v, color=color, linewidth=lw, alpha=alpha)
            ml, = axes[2].plot(curve.metric_x, curve.metric_y, color=color, linewidth=lw, alpha=alpha)
            if color_by_source and curve.source_label and curve.source_label not in source_line_map:
                source_line_map[curve.source_label] = ml

        axes[0].set_title(source_label, fontsize=10, pad=6)
        axes[0].set_ylabel("I (A)", fontsize=9)
        axes[1].set_ylabel("V (V)", fontsize=9)
        axes[2].set_ylabel(metric_ylabel, fontsize=9)
        axes[2].set_xlabel("Voltage (V)", fontsize=9)

        if color_by_source and source_line_map:
            for src, line in source_line_map.items():
                line.set_label(src)
            axes[2].legend(fontsize=6.5, framealpha=0.9, edgecolor="#CCCCCC", fancybox=False)

        for ax in axes:
            ax.tick_params(labelsize=8)

        axis_to_label[axes[0]] = (source_label, "current")
        axis_to_label[axes[1]] = (source_label, "voltage")
        axis_to_label[axes[2]] = (source_label, "metric")

        apply_axis_range(axes[0], [c.time_s for c in curves], [c.current_a for c in curves], current_range)
        apply_axis_range(axes[1], [c.time_s for c in curves], [c.voltage_v for c in curves], voltage_range)
        apply_axis_range(axes[2], [c.metric_x for c in curves], [c.metric_y for c in curves], metric_range)

    fig.suptitle(f"{metric_title}  |  All Selected Files", fontsize=14, y=0.995)
    return axis_to_label


# ---------------------------------------------------------------------------
# Public three-panel plot functions
# ---------------------------------------------------------------------------


def plot_three_panel_metric(
    curves: list[MetricCurve],
    cell_name: str,
    metric_title: str,
    metric_ylabel: str,
    current_range: AxisRange | None = None,
    voltage_range: AxisRange | None = None,
    metric_range: AxisRange | None = None,
    save_path: Path | None = None,
    color_by_source: bool = False,
) -> Any:
    fig = plt.figure()
    _draw_three_panel_detail(
        fig, curves, cell_name, metric_title, metric_ylabel,
        current_range, voltage_range, metric_range,
        color_by_source=color_by_source,
    )
    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to: {save_path}")
    return fig


def plot_three_panel_metric_dashboard(
    curves_by_source: list[tuple[str, list[MetricCurve]]],
    metric_title: str,
    metric_ylabel: str,
    current_range: AxisRange | None = None,
    voltage_range: AxisRange | None = None,
    metric_range: AxisRange | None = None,
    save_path: Path | None = None,
    color_by_source: bool = False,
) -> Any:
    if not curves_by_source:
        raise ValueError("No grouped curves available to plot.")

    fig = plt.figure()
    curves_lookup = {label: curves for label, curves in curves_by_source}
    detail_figures: dict[str, Any] = {}
    range_state_by_label: dict[str, tuple[AxisRange, AxisRange, AxisRange]] = {
        label: (
            _copy_axis_range(current_range),
            _copy_axis_range(voltage_range),
            _copy_axis_range(metric_range),
        )
        for label, _ in curves_by_source
    }

    def _draw_overview(*, preserve_size: bool = False) -> None:
        axis_to_label = _draw_three_panel_overview(
            fig, curves_by_source, metric_title, metric_ylabel,
            None, None, None,
            color_by_source=color_by_source,
            preserve_size=preserve_size,
        )
        axes_by_label: dict[str, dict[str, Any]] = {}
        for ax, (label, role) in axis_to_label.items():
            curves = curves_lookup[label]
            cur_s, volt_s, met_s = range_state_by_label[label]
            axes_by_label.setdefault(label, {})[role] = ax
            if role == "current":
                apply_axis_range(ax, [c.time_s for c in curves], [c.current_a for c in curves], cur_s)
            elif role == "voltage":
                apply_axis_range(ax, [c.time_s for c in curves], [c.voltage_v for c in curves], volt_s)
            else:
                apply_axis_range(ax, [c.metric_x for c in curves], [c.metric_y for c in curves], met_s)
        fig._dashboard_mode = "overview"
        fig._dashboard_axis_to_label = axis_to_label
        fig._dashboard_axes_by_label = axes_by_label
        fig.canvas.draw_idle()

    def _update_overview_for_label(label: str) -> None:
        axes_by_role = getattr(fig, "_dashboard_axes_by_label", {}).get(label)
        if not axes_by_role:
            return
        curves = curves_lookup[label]
        cur_s, volt_s, met_s = range_state_by_label[label]
        if ax := axes_by_role.get("current"):
            apply_axis_range(ax, [c.time_s for c in curves], [c.current_a for c in curves], cur_s)
        if ax := axes_by_role.get("voltage"):
            apply_axis_range(ax, [c.time_s for c in curves], [c.voltage_v for c in curves], volt_s)
        if ax := axes_by_role.get("metric"):
            apply_axis_range(ax, [c.metric_x for c in curves], [c.metric_y for c in curves], met_s)
        fig.canvas.draw_idle()

    def _open_detail_window(label: str) -> None:
        selected_curves = curves_lookup.get(label)
        if selected_curves is None:
            return
        cur_s, volt_s, met_s = range_state_by_label[label]
        if (ef := detail_figures.get(label)) is not None and plt.fignum_exists(ef.number):
            plt.close(ef)

        detail_fig = plt.figure()
        detail_figures[label] = detail_fig

        def _save_current(xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
            cur_s.x_min, cur_s.x_max = xlim
            cur_s.y_min, cur_s.y_max = ylim
            _update_overview_for_label(label)

        def _save_voltage(xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
            volt_s.x_min, volt_s.x_max = xlim
            volt_s.y_min, volt_s.y_max = ylim
            _update_overview_for_label(label)

        def _save_metric(xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
            met_s.x_min, met_s.x_max = xlim
            met_s.y_min, met_s.y_max = ylim
            _update_overview_for_label(label)

        axes, _ = _draw_three_panel_detail(
            detail_fig, selected_curves, label, metric_title, metric_ylabel,
            cur_s, volt_s, met_s,
            on_current_changed=_save_current,
            on_voltage_changed=_save_voltage,
            on_metric_changed=_save_metric,
            color_by_source=color_by_source,
        )
        detail_fig._dashboard_detail_axes = set(axes)

        def _forget(_: Any) -> None:
            if detail_figures.get(label) is detail_fig:
                detail_figures.pop(label, None)

        detail_fig.canvas.mpl_connect("close_event", _forget)
        detail_fig.canvas.draw_idle()
        try:
            detail_fig.show()
        except Exception:
            pass

    def _on_double_click(event: Any) -> None:
        if not getattr(event, "dblclick", False):
            return
        inaxes = getattr(event, "inaxes", None)
        if inaxes is None:
            return
        if getattr(fig, "_dashboard_mode", "overview") == "overview":
            info = getattr(fig, "_dashboard_axis_to_label", {}).get(inaxes)
            if info is not None:
                _open_detail_window(info[0])

    _draw_overview()
    fig.canvas.mpl_connect("button_press_event", _on_double_click)
    fig.canvas.mpl_connect("resize_event", lambda _: _draw_overview(preserve_size=True))

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to: {save_path}")

    return fig


# ---------------------------------------------------------------------------
# Qdmax plots
# ---------------------------------------------------------------------------


def plot_qdmax_vs_cycle(
    cycle_axis: np.ndarray,
    qdmax_values: np.ndarray,
    cell_name: str,
    axis_range: AxisRange | None = None,
    save_path: Path | None = None,
    ylabel: str = "Qdmax (Ah)",
) -> Any:
    if cycle_axis.size == 0 or qdmax_values.size == 0:
        raise ValueError("No valid Qdmax points to plot.")

    fig = plt.figure(figsize=(11, 8.5))
    _reset_interactive_figure(fig)
    ax = fig.subplots(1, 1)
    fig.subplots_adjust(left=0.09, right=0.96, top=0.94, bottom=0.24)

    line, = ax.plot(
        cycle_axis, qdmax_values,
        marker="o", markersize=3.5, linewidth=1.8,
        color=MODERN_COLORS[0], markerfacecolor="#FFFFFF",
        markeredgewidth=1.2, markeredgecolor=MODERN_COLORS[0],
    )
    ax.set_title(cell_name)
    ax.set_xlabel("Cycle")
    ax.set_ylabel(ylabel)
    apply_axis_range(
        ax,
        x_values=[cycle_axis.astype(np.float64, copy=False)],
        y_values=[qdmax_values.astype(np.float64, copy=False)],
        axis_range=axis_range,
    )
    fig.canvas.draw()
    x_pos, y_pos = _place_sliders_below_axis(ax, x_height=0.024, y_height=0.024, gap=0.026, label_clearance=0.026)
    _add_range_sliders(
        fig, ax,
        x_values=[cycle_axis.astype(np.float64, copy=False)],
        y_values=[qdmax_values.astype(np.float64, copy=False)],
        axis_range=axis_range,
        x_label="", y_label="",
        x_pos=x_pos, y_pos=y_pos,
        line_refs=[(line, cycle_axis.astype(np.float64, copy=False), qdmax_values.astype(np.float64, copy=False))],
    )
    _attach_slider_resize_handler(fig)

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to: {save_path}")

    return fig


def plot_qdmax_dashboard(
    datasets: list[tuple[str, np.ndarray, np.ndarray]],
    title: str,
    axis_range: AxisRange | None = None,
    save_path: Path | None = None,
    ylabel: str = "Qdmax (Ah)",
) -> Any:
    """One panel per dataset entry — used for the one-file-per-subfolder mode."""
    if not datasets:
        raise ValueError("No Qdmax datasets available to plot.")

    fig = plt.figure()
    datasets_by_label = {label: (ca, qv) for label, ca, qv in datasets}
    detail_figures: dict[str, Any] = {}
    range_state: dict[str, AxisRange] = {label: _copy_axis_range(axis_range) for label, _, _ in datasets}

    def _draw_overview(*, preserve_size: bool = False) -> None:
        _reset_interactive_figure(fig)
        count = len(datasets)
        if not preserve_size:
            initial_cols = 1 if count == 1 else 2 if count <= 4 else 3
            initial_rows = math.ceil(count / initial_cols)
            fig.set_size_inches(max(13, initial_cols * 5.4), max(9, initial_rows * 4.4), forward=True)
        rows, cols = _compute_dashboard_grid(count, fig.get_figwidth())
        outer = fig.add_gridspec(rows, cols, left=0.055, right=0.985, top=0.975, bottom=0.055, wspace=0.18, hspace=0.34)
        axis_to_label: dict[Any, str] = {}

        for idx, (label, cycle_axis, qdmax_values) in enumerate(datasets):
            ax = fig.add_subplot(outer[idx // cols, idx % cols])
            color = MODERN_COLORS[idx % len(MODERN_COLORS)]
            ax.plot(
                cycle_axis, qdmax_values,
                marker="o", markersize=2.8, linewidth=1.4,
                color=color, markerfacecolor="#FFFFFF",
                markeredgewidth=1.0, markeredgecolor=color,
            )
            ax.set_title(label, fontsize=10, pad=5)
            ax.set_xlabel("Cycle", fontsize=8, labelpad=1)
            ax.set_ylabel(ylabel, fontsize=8, labelpad=2)
            ax.tick_params(labelsize=7.5)
            apply_axis_range(
                ax,
                x_values=[cycle_axis.astype(np.float64, copy=False)],
                y_values=[qdmax_values.astype(np.float64, copy=False)],
                axis_range=range_state[label],
            )
            axis_to_label[ax] = label

        fig._dashboard_mode = "overview"
        fig._dashboard_axis_to_label = axis_to_label
        fig._dashboard_axes_by_label = {label: ax for ax, label in axis_to_label.items()}

    def _update_overview_for_label(label: str) -> None:
        ax = getattr(fig, "_dashboard_axes_by_label", {}).get(label)
        data = datasets_by_label.get(label)
        if ax is None or data is None:
            return
        ca, qv = data
        apply_axis_range(ax, [ca.astype(np.float64, copy=False)], [qv.astype(np.float64, copy=False)], range_state[label])
        fig.canvas.draw_idle()

    def _open_detail_window(label: str) -> None:
        data = datasets_by_label.get(label)
        if data is None:
            return
        ca, qv = data
        saved = range_state[label]
        if (ef := detail_figures.get(label)) is not None and plt.fignum_exists(ef.number):
            plt.close(ef)

        df = plt.figure(figsize=(11, 8.5))
        detail_figures[label] = df
        _reset_interactive_figure(df)
        df.subplots_adjust(left=0.09, right=0.96, top=0.94, bottom=0.24)
        ax = df.subplots(1, 1)
        color = MODERN_COLORS[list(datasets_by_label.keys()).index(label) % len(MODERN_COLORS)]
        line, = ax.plot(
            ca, qv, marker="o", markersize=3.5, linewidth=1.8,
            color=color, markerfacecolor="#FFFFFF",
            markeredgewidth=1.2, markeredgecolor=color,
        )
        ax.set_title(label)
        ax.set_xlabel("Cycle")
        ax.set_ylabel(ylabel)
        apply_axis_range(ax, [ca.astype(np.float64, copy=False)], [qv.astype(np.float64, copy=False)], saved)
        df.canvas.draw()
        x_pos, y_pos = _place_sliders_below_axis(ax, x_height=0.024, y_height=0.024, gap=0.026, label_clearance=0.026)

        def _save(xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
            saved.x_min, saved.x_max = xlim
            saved.y_min, saved.y_max = ylim
            _update_overview_for_label(label)

        _add_range_sliders(
            df, ax,
            x_values=[ca.astype(np.float64, copy=False)],
            y_values=[qv.astype(np.float64, copy=False)],
            axis_range=saved, x_label="", y_label="",
            x_pos=x_pos, y_pos=y_pos,
            line_refs=[(line, ca.astype(np.float64, copy=False), qv.astype(np.float64, copy=False))],
            on_limits_changed=_save,
        )
        _attach_axis_limit_change_handler(ax, _save)
        _attach_slider_resize_handler(df)
        df._dashboard_detail_axes = {ax}

        def _forget(_: Any) -> None:
            if detail_figures.get(label) is df:
                detail_figures.pop(label, None)

        df.canvas.mpl_connect("close_event", _forget)
        df.canvas.draw_idle()
        try:
            df.show()
        except Exception:
            pass

    def _on_double_click(event: Any) -> None:
        if not getattr(event, "dblclick", False):
            return
        inaxes = getattr(event, "inaxes", None)
        if inaxes is None:
            return
        if getattr(fig, "_dashboard_mode", "overview") == "overview":
            label = getattr(fig, "_dashboard_axis_to_label", {}).get(inaxes)
            if label is not None:
                _open_detail_window(label)

    _draw_overview()
    fig.canvas.mpl_connect("button_press_event", _on_double_click)
    fig.canvas.mpl_connect("resize_event", lambda _: _draw_overview(preserve_size=True))

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to: {save_path}")

    return fig


def plot_qdmax_all_files_dashboard(
    subfolder_groups: list[tuple[str, list[tuple[str, np.ndarray, np.ndarray]]]],
    title: str,
    axis_range: AxisRange | None = None,
    save_path: Path | None = None,
    ylabel: str = "Qdmax (Ah)",
) -> Any:
    """Dashboard with one panel per subfolder; all files in each subfolder overlaid.

    subfolder_groups: [(subfolder_name, [(file_stem, cycle_axis, qdmax_values), ...]), ...]
    """
    if not subfolder_groups:
        raise ValueError("No subfolder groups provided.")

    count = len(subfolder_groups)
    cols = 1 if count == 1 else 2 if count <= 4 else 3
    rows = math.ceil(count / cols)
    fig, axes_grid = plt.subplots(
        rows, cols,
        figsize=(max(13, cols * 5.6), max(9, rows * 4.6)),
        squeeze=False,
    )
    fig.subplots_adjust(left=0.06, right=0.97, top=0.94, bottom=0.06, wspace=0.22, hspace=0.40)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.98)

    all_axes = [ax for row in axes_grid for ax in row]
    for idx in range(count, len(all_axes)):
        all_axes[idx].set_visible(False)

    for idx, (subfolder_name, file_entries) in enumerate(subfolder_groups):
        ax = all_axes[idx]
        for file_idx, (file_stem, cycle_axis, qdmax_values) in enumerate(file_entries):
            color = MODERN_COLORS[file_idx % len(MODERN_COLORS)]
            ax.plot(
                cycle_axis, qdmax_values,
                marker="o", markersize=2.5, linewidth=1.4,
                color=color, markerfacecolor="#FFFFFF",
                markeredgewidth=0.9, markeredgecolor=color,
                label=file_stem, alpha=0.88,
            )
        ax.set_title(subfolder_name, fontsize=10, pad=5)
        ax.set_xlabel("Cycle", fontsize=8, labelpad=1)
        ax.set_ylabel(ylabel, fontsize=8, labelpad=2)
        ax.tick_params(labelsize=7.5)
        if len(file_entries) > 1:
            ax.legend(fontsize=6, framealpha=0.88, edgecolor="#CCCCCC", fancybox=False, ncol=2)
        apply_axis_range(
            ax,
            x_values=[ca.astype(np.float64, copy=False) for _, ca, _ in file_entries],
            y_values=[qv.astype(np.float64, copy=False) for _, _, qv in file_entries],
            axis_range=axis_range,
        )

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved plot to: {save_path}")

    return fig
