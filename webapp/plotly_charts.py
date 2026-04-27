"""Plotly figures built from the same data pipeline as the CLI plot scripts."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import plotly.graph_objects as go


_TEMP_PATTERNS = (
    # Anchored to chemistry/format keyword: e.g. "NMC_25C_", "pouch_-5C_", "18650_30C_"
    re.compile(
        r"(?:NMC\d*|NCA\d*|NCM\d*|LFP|LCO|LiFePO4|LiCoO2|LiNiCoMnO2|"
        r"pouch|cylindrical|prismatic|18650|26650|coin|polymer|502030)_(-?\d+)C(?:_|$|\.)",
        re.IGNORECASE,
    ),
    # CALB-style: dataset prefix then a bare number then underscore  (CALB_0_B182, CALB_35_B247)
    re.compile(r"^[A-Za-z]+_(-?\d+)_"),
)


def extract_temperature_from_name(name: str | None) -> int | None:
    """Extract test ambient temperature in °C from a battery .pkl filename.

    Examples:
        CALB_0_B182.pkl                                           -> 0
        CALB_35_B247.pkl                                          -> 35
        MICH_01R_pouch_NMC_25C_0-100_0.2-0.2C.pkl                 -> 25
        MICH_02C_pouch_NMC_-5C_0-100_0.2-0.2C.pkl                 -> -5  (cell id "02C" ignored)
        MICH_MCForm39_pouch_NMC_45C_0-100_1-1C_x.pkl              -> 45  (C-rate "1-1C" ignored)
        MATR_b1c0.pkl                                             -> None
        CALCE_CS2_33.pkl                                          -> None
    """
    if not name:
        return None
    base = re.sub(r"\.[Pp][Kk][Ll]$", "", str(name))
    for pat in _TEMP_PATTERNS:
        m = pat.search(base)
        if not m:
            continue
        try:
            t = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if -30 <= t <= 80:
            return t
    return None

_PLOT_DIR = Path(__file__).resolve().parent.parent / "Plot"
if str(_PLOT_DIR) not in sys.path:
    sys.path.insert(0, str(_PLOT_DIR))

from batteryml_data import (  # noqa: E402
    MODERN_COLORS,
    MetricCurve,
    build_charge_metric_curves,
    build_dqdv_curve,
    build_dvdq_curve,
    build_metric_curves,
    build_qd_vs_voltage_curve,
    compute_qcmax_by_cycle,
    compute_qdmax_by_cycle,
    get_cycle_data,
    load_batteryml_pickle,
    parse_cycle_csv,
)

_FONT = dict(family="DM Sans, Inter, system-ui, sans-serif", size=12, color="#172033")


def resolve_cycles(cycle_count: int, cycles_raw: str | None) -> list[int]:
    if cycle_count <= 0:
        return []
    if cycles_raw is None or str(cycles_raw).strip().lower() in {"", "all", "*"}:
        return list(range(cycle_count))
    picked = parse_cycle_csv(str(cycles_raw))
    bad = [i for i in picked if not 0 <= i < cycle_count]
    if bad:
        raise ValueError(f"Cycle index out of range: {bad}. Valid: 0..{cycle_count - 1}")
    return picked


def _robust_range(values: np.ndarray, lo_pct: float = 1.0, hi_pct: float = 99.0, pad: float = 0.05) -> tuple[float, float] | None:
    arr = values[np.isfinite(values)]
    if arr.size == 0:
        return None
    lo = float(np.percentile(arr, lo_pct))
    hi = float(np.percentile(arr, hi_pct))
    if lo == hi:
        return None
    span = hi - lo
    return (lo - span * pad, hi + span * pad)


def _apply_layout(fig: go.Figure, title: str, *, height: int = 360) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0.02, xanchor="left", font=dict(size=15, color="#111827")),
        height=height,
        margin=dict(t=58, b=58, l=68, r=28),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#F6F8FB",
        font=_FONT,
        showlegend=False,
        hovermode="closest",
    )
    fig.update_xaxes(
        gridcolor="#E7ECF3",
        zerolinecolor="#CDD6E3",
        linecolor="#CDD6E3",
        ticks="outside",
        tickcolor="#CDD6E3",
    )
    fig.update_yaxes(
        gridcolor="#E7ECF3",
        zerolinecolor="#CDD6E3",
        linecolor="#CDD6E3",
        ticks="outside",
        tickcolor="#CDD6E3",
    )
    return fig


def _multi_line_panel(
    title: str,
    xlabel: str,
    ylabel: str,
    curves: list[MetricCurve],
    x_attr: str,
    y_attr: str,
    *,
    filter_outliers: bool,
) -> go.Figure:
    fig = go.Figure()
    for i, c in enumerate(curves):
        label = _cycle_label(c)
        fig.add_trace(go.Scatter(
            x=getattr(c, x_attr),
            y=getattr(c, y_attr),
            mode="lines",
            line=dict(color=MODERN_COLORS[i % len(MODERN_COLORS)], width=1.4),
            name=label,
            hovertemplate=f"{label}<br>{xlabel}: %{{x:.5g}}<br>{ylabel}: %{{y:.5g}}<extra></extra>",
            showlegend=False,
        ))
    fig.update_xaxes(title_text=xlabel)
    fig.update_yaxes(title_text=ylabel)
    if filter_outliers and curves:
        xs = np.concatenate([np.asarray(getattr(c, x_attr), dtype=float) for c in curves])
        ys = np.concatenate([np.asarray(getattr(c, y_attr), dtype=float) for c in curves])
        xr = _robust_range(xs)
        yr = _robust_range(ys)
        if xr is not None:
            fig.layout.xaxis.range = list(xr)
        if yr is not None:
            fig.layout.yaxis.range = list(yr)
    return _apply_layout(fig, title)


def make_three_panels(
    curves: list[MetricCurve],
    metric_title: str,
    metric_x_label: str,
    metric_ylabel: str,
    *,
    filter_outliers: bool = False,
) -> list[go.Figure]:
    if not curves:
        raise ValueError("No curves to plot.")
    return [
        _multi_line_panel("Current vs time", "Time (s)", "Current (A)",
                          curves, "time_s", "current_a", filter_outliers=filter_outliers),
        _multi_line_panel("Voltage vs time", "Time (s)", "Voltage (V)",
                          curves, "time_s", "voltage_v", filter_outliers=filter_outliers),
        _multi_line_panel(metric_title, metric_x_label, metric_ylabel,
                          curves, "metric_x", "metric_y", filter_outliers=filter_outliers),
    ]


def make_qmax_panel(
    cycle_axis: np.ndarray,
    values: np.ndarray,
    title: str,
    ylabel: str,
    *,
    filter_outliers: bool = False,
) -> list[go.Figure]:
    fig = go.Figure(data=[go.Scatter(
        x=cycle_axis,
        y=values,
        mode="lines+markers",
        line=dict(color=MODERN_COLORS[0], width=1.8),
        marker=dict(size=5, color="#FFFFFF", line=dict(color=MODERN_COLORS[0], width=1.2)),
        hovertemplate="Cycle: %{x}<br>" + ylabel + ": %{y:.5g}<extra></extra>",
        showlegend=False,
    )])
    fig.update_xaxes(title_text="Cycle")
    fig.update_yaxes(title_text=ylabel)
    if filter_outliers:
        r = _robust_range(np.asarray(values, dtype=float))
        if r is not None:
            fig.layout.yaxis.range = list(r)
    return [_apply_layout(fig, title, height=440)]


def _heatmap_bar_colors(values: list[int]) -> list[str]:
    """Interpolate bar colors from pale blue (low) to deep blue (high)."""
    low_rgb = (189, 215, 247)   # #bdd7f7
    high_rgb = (23, 71, 166)    # #1747A6
    min_v, max_v = min(values), max(values)
    span = max(max_v - min_v, 1)
    colors = []
    for v in values:
        t = (v - min_v) / span
        r = int(low_rgb[0] + (high_rgb[0] - low_rgb[0]) * t)
        g = int(low_rgb[1] + (high_rgb[1] - low_rgb[1]) * t)
        b = int(low_rgb[2] + (high_rgb[2] - low_rgb[2]) * t)
        colors.append(f"rgb({r},{g},{b})")
    return colors


def _eol_cycle_from_qd(cycle_axis: Any, qd_values: Any, threshold: float = 0.8) -> int | None:
    """Return the first cycle at which Qd drops to (threshold * initial_Qd) or below."""
    arr = np.asarray(qd_values, dtype=float)
    cyc = np.asarray(cycle_axis, dtype=float)
    mask = np.isfinite(arr) & (arr > 0) & np.isfinite(cyc)
    if mask.sum() < 2:
        return None
    arr = arr[mask]
    cyc = cyc[mask]
    initial = float(arr[0])
    if initial <= 0:
        return None
    target = initial * threshold
    below = np.where(arr <= target)[0]
    if len(below) == 0:
        return None
    return int(cyc[below[0]])


def compute_cell_metrics(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract Qd/Qc capacity and current metrics from a BatteryML object."""
    metrics: dict[str, Any] = {
        "qd_max": None, "qd_min": None, "qd_fade_pct": None,
        "qc_max": None, "qc_min": None, "qc_fade_pct": None,
        "max_charge_current": None, "max_discharge_current": None,
        "eol_80_cycle": None,
    }

    try:
        cx_d, qd_vals = compute_qdmax_by_cycle(obj)
        if len(qd_vals) > 0:
            metrics["qd_max"] = round(float(np.nanmax(qd_vals)), 5)
            metrics["qd_min"] = round(float(np.nanmin(qd_vals)), 5)
            if len(qd_vals) >= 2 and qd_vals[0] > 0:
                metrics["qd_fade_pct"] = round(float((qd_vals[0] - qd_vals[-1]) / qd_vals[0] * 100), 2)
            metrics["eol_80_cycle"] = _eol_cycle_from_qd(cx_d, qd_vals, threshold=0.8)
    except Exception:
        pass

    try:
        _, qc_vals = compute_qcmax_by_cycle(obj)
        if len(qc_vals) > 0:
            metrics["qc_max"] = round(float(np.nanmax(qc_vals)), 5)
            metrics["qc_min"] = round(float(np.nanmin(qc_vals)), 5)
            if len(qc_vals) >= 2 and qc_vals[0] > 0:
                metrics["qc_fade_pct"] = round(float((qc_vals[0] - qc_vals[-1]) / qc_vals[0] * 100), 2)
    except Exception:
        pass

    cycle_data = obj.get("cycle_data")
    if isinstance(cycle_data, list):
        per_cycle_chg: list[float] = []
        per_cycle_dch: list[float] = []
        for cycle in cycle_data:
            if not isinstance(cycle, dict):
                continue
            curr = cycle.get("current_in_A") or cycle.get("current") or cycle.get("I")
            if curr is None:
                continue
            try:
                arr = np.asarray(curr, dtype=float)
                arr = arr[np.isfinite(arr)]
                pos = arr[arr > 0.001]
                neg = arr[arr < -0.001]
                if len(pos):
                    per_cycle_chg.append(float(np.max(pos)))
                if len(neg):
                    per_cycle_dch.append(float(np.min(neg)))
            except Exception:
                continue

        def _iqr_filter_max(values: list[float]) -> float | None:
            if not values:
                return None
            a = np.array(values)
            q1, q3 = np.percentile(a, [25, 75])
            iqr = q3 - q1
            keep = a[a <= q3 + 3.0 * iqr] if iqr > 0 else a
            return round(float(np.max(keep if len(keep) else a)), 4)

        def _iqr_filter_min(values: list[float]) -> float | None:
            if not values:
                return None
            a = np.array(values)
            q1, q3 = np.percentile(a, [25, 75])
            iqr = q3 - q1
            keep = a[a >= q1 - 3.0 * iqr] if iqr > 0 else a
            return round(float(np.min(keep if len(keep) else a)), 4)

        metrics["max_charge_current"] = _iqr_filter_max(per_cycle_chg)
        metrics["max_discharge_current"] = _iqr_filter_min(per_cycle_dch)

    return metrics


def make_folder_cycle_bar(file_rows: list[dict[str, Any]], folder_name: str) -> go.Figure:
    valid_rows = [
        row for row in file_rows
        if isinstance(row.get("max_cycle"), int)
    ]
    if not valid_rows:
        raise ValueError("No files with valid cycle data found in folder.")

    names = [str(row["name"]) for row in valid_rows]
    max_cycles = [int(row["max_cycle"]) for row in valid_rows]
    cycle_counts = [int(row.get("cycle_count") or 0) for row in valid_rows]
    colors = _heatmap_bar_colors(max_cycles)

    fig = go.Figure(data=[go.Bar(
        x=names,
        y=max_cycles,
        marker=dict(
            color=colors,
            line=dict(color="rgba(23,71,166,0.35)", width=0.6),
        ),
        customdata=np.asarray(cycle_counts, dtype=np.int32),
        hovertemplate="File: %{x}<br>Max cycle: %{y}<extra></extra>",
    )])

    # Red line marker at the cycle where Qd drops to 80% of initial
    eol_x: list[str] = []
    eol_y: list[int] = []
    for row in valid_rows:
        eol = row.get("eol_80_cycle")
        if isinstance(eol, (int, float)) and eol > 0:
            eol_x.append(str(row["name"]))
            eol_y.append(int(eol))
    if eol_x:
        fig.add_trace(go.Scatter(
            x=eol_x,
            y=eol_y,
            mode="markers",
            marker=dict(
                symbol="line-ew",
                size=24,
                line=dict(color="#dc2626", width=4),
            ),
            hovertemplate="%{x}<br>EOL @ 80%%: cycle %{y}<extra></extra>",
            showlegend=False,
        ))

    fig.update_xaxes(title_text="", showticklabels=False, ticks="")
    fig.update_yaxes(title_text="Maximum cycle")
    return _apply_layout(fig, f"Maximum cycle per file - {folder_name}", height=500)


_CurveFn = Callable[[Any], MetricCurve]

_MULTI_SPECS: dict[str, tuple[bool, str, str, str, Callable[[float, float], _CurveFn]]] = {
    "dqdv_discharge": (False, "dQ/dV vs voltage",          "Voltage (V)", "dQ/dV (Ah/V)",
                       lambda min_dv, _: lambda s: build_dqdv_curve(s, min_dv=min_dv)),
    "dvdq_discharge": (False, "dV/dQ vs voltage",          "Voltage (V)", "dV/dQ (V/Ah)",
                       lambda _, min_dq: lambda s: build_dvdq_curve(s, min_dq=min_dq)),
    "qd_vs_v":        (False, "Discharge Q vs voltage",    "Voltage (V)", "Qd (Ah)",
                       lambda *_: build_qd_vs_voltage_curve),
    "qc_vs_v":        (True,  "Charge Q vs voltage",       "Voltage (V)", "Qcharge (Ah)",
                       lambda *_: build_qd_vs_voltage_curve),
    "dqdv_charge":    (True,  "dQ/dV (charge) vs voltage", "Voltage (V)", "dQ/dV (Ah/V)",
                       lambda min_dv, _: lambda s: build_dqdv_curve(s, min_dv=min_dv)),
    "dvdq_charge":    (True,  "dV/dQ (charge) vs voltage", "Voltage (V)", "dV/dQ (V/Ah)",
                       lambda _, min_dq: lambda s: build_dvdq_curve(s, min_dq=min_dq)),
}


def build_figures_for_kind(
    obj: dict[str, Any],
    *,
    cell_name: str,
    kind: str,
    cycles: str | None,
    min_dv: float = 1e-4,
    min_dq: float = 1e-5,
    filter_outliers: bool = False,
) -> list[go.Figure]:
    cycle_data = get_cycle_data(obj)
    indices = resolve_cycles(len(cycle_data), cycles)

    if kind in _MULTI_SPECS:
        charge, title, xlabel, ylabel, make_fn = _MULTI_SPECS[kind]
        curve_fn = make_fn(min_dv, min_dq)
        builder = build_charge_metric_curves if charge else build_metric_curves
        curves = builder(obj, indices, curve_fn)
        return make_three_panels(curves, title, xlabel, ylabel, filter_outliers=filter_outliers)

    if kind == "qcmax":
        cx, qv = compute_qcmax_by_cycle(obj)
        return make_qmax_panel(cx, qv, f"Qcmax vs cycle - {cell_name}", "Qcmax (Ah)",
                               filter_outliers=filter_outliers)
    if kind == "qdmax":
        cx, qv = compute_qdmax_by_cycle(obj)
        return make_qmax_panel(cx, qv, f"Qdmax vs cycle - {cell_name}", "Qdmax (Ah)",
                               filter_outliers=filter_outliers)

    raise ValueError(f"Unknown plot kind: {kind}")


def _cycle_label(curve: MetricCurve) -> str:
    if curve.cycle_number is None:
        return f"cycle_idx {curve.cycle_idx}"
    return f"cycle_idx {curve.cycle_idx} | cycle {curve.cycle_number}"


def capacity_fade_summary(obj: dict[str, Any]) -> dict[str, dict[str, float | int | None] | None]:
    """Return Qdmax/Qcmax first-to-last summaries for the metadata cards."""
    out: dict[str, dict[str, float | int | None] | None] = {"qdmax": None, "qcmax": None}
    for key, fn in (("qdmax", compute_qdmax_by_cycle), ("qcmax", compute_qcmax_by_cycle)):
        try:
            cycles, vals = fn(obj)
            cycle_arr = np.asarray(cycles, dtype=float)
            value_arr = np.asarray(vals, dtype=float)
            mask = np.isfinite(cycle_arr) & np.isfinite(value_arr)
            cycle_arr = cycle_arr[mask]
            value_arr = value_arr[mask]
            if value_arr.size < 1:
                continue
            first = float(value_arr[0])
            last = float(value_arr[-1])
            dec = (first - last) / first * 100.0 if first != 0 else None
            out[key] = {
                "first": first,
                "last": last,
                "first_cycle": int(cycle_arr[0]),
                "last_cycle": int(cycle_arr[-1]),
                "decrease_pct": dec,
            }
        except Exception:
            continue
    return out


__all__ = [
    "build_figures_for_kind",
    "capacity_fade_summary",
    "compute_cell_metrics",
    "extract_temperature_from_name",
    "load_batteryml_pickle",
    "make_folder_cycle_bar",
    "resolve_cycles",
]
