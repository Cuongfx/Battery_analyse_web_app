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
    # Tongji-style cycling-temperature token: "_CY25-", "_CY35_", "_CY45-" (the trailing -05/-025 is a C-rate)
    re.compile(r"(?:^|_)CY(-?\d+)[-_]", re.IGNORECASE),
)


def extract_temperature_from_name(name: str | None) -> int | None:
    """Extract test ambient temperature in °C from a battery .pkl filename.

    Examples:
        CALB_0_B182.pkl                                           -> 0
        CALB_35_B247.pkl                                          -> 35
        MICH_01R_pouch_NMC_25C_0-100_0.2-0.2C.pkl                 -> 25
        MICH_02C_pouch_NMC_-5C_0-100_0.2-0.2C.pkl                 -> -5  (cell id "02C" ignored)
        MICH_MCForm39_pouch_NMC_45C_0-100_1-1C_x.pkl              -> 45  (C-rate "1-1C" ignored)
        Tongji1_CY25-025_1--1.pkl                                 -> 25  (C-rate "-025" ignored)
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


_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)(?:\s*:\s*(\d+))?\s*$")


def resolve_cycles(cycle_count: int, cycles_raw: str | None) -> list[int]:
    if cycle_count <= 0:
        return []
    raw = "" if cycles_raw is None else str(cycles_raw).strip()
    if raw.lower() in {"", "all", "*"}:
        return list(range(cycle_count))

    # Range syntax: "a-b" or "a-b:step"  (e.g. "0-100", "0-100:5")
    m = _RANGE_RE.match(raw)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        step = int(m.group(3)) if m.group(3) else 1
        if step <= 0:
            raise ValueError("Step must be a positive integer.")
        lo, hi = (a, b) if a <= b else (b, a)
        picked = list(range(lo, hi + 1, step))
    else:
        picked = parse_cycle_csv(raw)

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


def _gradient_palette(dark_rgb: tuple[int, int, int], light_rgb: tuple[int, int, int], n: int) -> list[str]:
    """Build n colours stepping from dark (smallest cycle) to light (largest)."""
    if n <= 0:
        return []
    if n == 1:
        return [f"rgb({dark_rgb[0]},{dark_rgb[1]},{dark_rgb[2]})"]
    out = []
    for i in range(n):
        t = i / (n - 1)
        r = round(dark_rgb[0] + (light_rgb[0] - dark_rgb[0]) * t)
        g = round(dark_rgb[1] + (light_rgb[1] - dark_rgb[1]) * t)
        b = round(dark_rgb[2] + (light_rgb[2] - dark_rgb[2]) * t)
        out.append(f"rgb({r},{g},{b})")
    return out


# Named gradient stops used by dQ/dV and dV/dQ plots.
# File A uses blue (discharge) / red (charge).
# File B uses the *opposite* tones on the colour wheel: orange (↔ blue) / cyan (↔ red).
_GRADIENT_BLUE = ((8, 48, 107), (158, 202, 225))      # dark navy → pale blue
_GRADIENT_RED = ((103, 0, 13), (252, 146, 124))       # dark crimson → soft coral
_GRADIENT_ORANGE = ((124, 45, 18), (253, 186, 116))   # burnt amber → soft peach (opposite of blue)
_GRADIENT_CYAN = ((19, 78, 74), (94, 234, 212))       # deep teal → light cyan (opposite of red)
_GRADIENT_GREEN = ((6, 64, 43), (160, 217, 180))      # kept for back-compat
_GRADIENT_PURPLE = ((63, 14, 110), (200, 168, 232))   # kept for back-compat

_GRADIENT_STOPS = {
    "blue": _GRADIENT_BLUE,
    "red": _GRADIENT_RED,
    "orange": _GRADIENT_ORANGE,
    "cyan": _GRADIENT_CYAN,
    "green": _GRADIENT_GREEN,
    "purple": _GRADIENT_PURPLE,
}


def _palette_for(curves: list[MetricCurve], palette: str | None) -> list[str]:
    """Return per-curve colours; if a named palette is given, gradient by ascending cycle index."""
    n = len(curves)
    stops = _GRADIENT_STOPS.get(palette) if palette else None
    if stops is None:
        return [MODERN_COLORS[i % len(MODERN_COLORS)] for i in range(n)]

    # Map sorted-by-cycle index → colour, then reproject onto the original curve order
    order = sorted(range(n), key=lambda i: curves[i].cycle_idx)
    grad = _gradient_palette(stops[0], stops[1], n)
    colors = [None] * n
    for rank, idx in enumerate(order):
        colors[idx] = grad[rank]
    return colors


def _multi_line_panel(
    title: str,
    xlabel: str,
    ylabel: str,
    curves: list[MetricCurve],
    x_attr: str,
    y_attr: str,
    *,
    filter_outliers: bool,
    palette: str | None = None,
) -> go.Figure:
    fig = go.Figure()
    colors = _palette_for(curves, palette)
    for i, c in enumerate(curves):
        label = _cycle_label(c)
        fig.add_trace(go.Scatter(
            x=getattr(c, x_attr),
            y=getattr(c, y_attr),
            mode="lines",
            line=dict(color=colors[i], width=1.4),
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
    palette: str | None = None,
) -> list[go.Figure]:
    if not curves:
        raise ValueError("No curves to plot.")
    return [
        _multi_line_panel("Current vs time", "Time (s)", "Current (A)",
                          curves, "time_s", "current_a",
                          filter_outliers=filter_outliers, palette=palette),
        _multi_line_panel("Voltage vs time", "Time (s)", "Voltage (V)",
                          curves, "time_s", "voltage_v",
                          filter_outliers=filter_outliers, palette=palette),
        _multi_line_panel(metric_title, metric_x_label, metric_ylabel,
                          curves, "metric_x", "metric_y",
                          filter_outliers=filter_outliers, palette=palette),
    ]


def make_charge_discharge_overlay(
    discharge_curves: list[MetricCurve],
    charge_curves: list[MetricCurve],
    metric_title: str,
    xlabel: str,
    ylabel: str,
    *,
    filter_outliers: bool = False,
    palette_pair: tuple[str, str] = ("blue", "red"),
) -> list[go.Figure]:
    """Single-panel plot overlaying discharge and charge curves vs voltage with distinct colour themes."""
    if not discharge_curves and not charge_curves:
        raise ValueError("No curves to plot.")
    fig = go.Figure()

    # Sort by cycle index so the darkest colour maps to the lowest cycle.
    discharge_sorted = sorted(discharge_curves, key=lambda c: c.cycle_idx)
    charge_sorted = sorted(charge_curves, key=lambda c: c.cycle_idx)

    pal_dis, pal_chg = palette_pair
    discharge_palette = _gradient_palette(*_GRADIENT_STOPS.get(pal_dis, _GRADIENT_BLUE), len(discharge_sorted))
    charge_palette = _gradient_palette(*_GRADIENT_STOPS.get(pal_chg, _GRADIENT_RED), len(charge_sorted))

    # Discharge: solid lines, dark → light blue
    for i, c in enumerate(discharge_sorted):
        color = discharge_palette[i]
        label = f"discharge - {_cycle_label(c)}"
        fig.add_trace(go.Scatter(
            x=c.metric_x,
            y=c.metric_y,
            mode="lines",
            line=dict(color=color, width=1.4, dash="solid"),
            name=label,
            hovertemplate=f"{label}<br>{xlabel}: %{{x:.5g}}<br>{ylabel}: %{{y:.5g}}<extra></extra>",
            showlegend=False,
        ))

    # Charge: dashed lines, dark → light red
    for i, c in enumerate(charge_sorted):
        color = charge_palette[i]
        label = f"charge - {_cycle_label(c)}"
        fig.add_trace(go.Scatter(
            x=c.metric_x,
            y=c.metric_y,
            mode="lines",
            line=dict(color=color, width=1.4, dash="dash"),
            name=label,
            hovertemplate=f"{label}<br>{xlabel}: %{{x:.5g}}<br>{ylabel}: %{{y:.5g}}<extra></extra>",
            showlegend=False,
        ))

    fig.update_xaxes(title_text=xlabel)
    fig.update_yaxes(title_text=ylabel)

    if filter_outliers:
        all_x = []
        all_y = []
        for c in (*discharge_curves, *charge_curves):
            all_x.append(np.asarray(c.metric_x, dtype=float))
            all_y.append(np.asarray(c.metric_y, dtype=float))
        if all_x:
            xr = _robust_range(np.concatenate(all_x))
            yr = _robust_range(np.concatenate(all_y))
            if xr is not None:
                fig.layout.xaxis.range = list(xr)
            if yr is not None:
                fig.layout.yaxis.range = list(yr)

    fig = _apply_layout(fig, metric_title, height=480)
    return [fig]


def make_qmax_panel(
    cycle_axis: np.ndarray,
    values: np.ndarray,
    title: str,
    ylabel: str,
    *,
    filter_outliers: bool = False,
    palette: str | None = None,
) -> list[go.Figure]:
    if palette == "red":
        line_color = f"rgb{_GRADIENT_RED[0]}"
    elif palette == "blue":
        line_color = f"rgb{_GRADIENT_BLUE[0]}"
    else:
        line_color = MODERN_COLORS[0]
    fig = go.Figure(data=[go.Scatter(
        x=cycle_axis,
        y=values,
        mode="lines+markers",
        line=dict(color=line_color, width=1.8),
        marker=dict(size=5, color="#FFFFFF", line=dict(color=line_color, width=1.2)),
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
    # customdata columns: [cycle_count, eol_80_cycle]  (eol = -1 if not available)
    customdata = np.array([
        [int(row.get("cycle_count") or 0),
         int(row["eol_80_cycle"]) if isinstance(row.get("eol_80_cycle"), (int, float)) and row["eol_80_cycle"] > 0 else -1]
        for row in valid_rows
    ], dtype=np.int32)
    colors = _heatmap_bar_colors(max_cycles)

    fig = go.Figure(data=[go.Bar(
        x=names,
        y=max_cycles,
        marker=dict(
            color=colors,
            line=dict(color="rgba(23,71,166,0.35)", width=0.6),
        ),
        customdata=customdata,
        hovertemplate="File: %{x}<br>Max cycle: %{y}<extra></extra>",
    )])

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


def _build_dqdv_time_curve(series: Any, min_dv: float = 1e-4) -> MetricCurve | None:
    dq = np.diff(series.qd_ah)
    dv = np.diff(series.voltage_v)
    t = 0.5 * (series.time_s[:-1] + series.time_s[1:])
    mask = np.isfinite(dq) & np.isfinite(dv) & np.isfinite(t) & (np.abs(dv) >= max(min_dv, 1e-12))
    if int(np.count_nonzero(mask)) < 2:
        return None
    y = dq[mask] / dv[mask]
    x = t[mask]
    finite = np.isfinite(y) & np.isfinite(x)
    if int(np.count_nonzero(finite)) < 2:
        return None
    return MetricCurve(
        cycle_idx=series.cycle_idx,
        cycle_number=series.cycle_number,
        time_s=series.time_s,
        current_a=series.current_a,
        voltage_v=series.voltage_v,
        metric_x=x[finite],
        metric_y=y[finite],
    )


def _build_dvdq_time_curve(series: Any, min_dq: float = 1e-5) -> MetricCurve | None:
    dq = np.diff(series.qd_ah)
    dv = np.diff(series.voltage_v)
    t = 0.5 * (series.time_s[:-1] + series.time_s[1:])
    mask = np.isfinite(dq) & np.isfinite(dv) & np.isfinite(t) & (np.abs(dq) >= max(min_dq, 1e-12))
    if int(np.count_nonzero(mask)) < 2:
        return None
    y = dv[mask] / dq[mask]
    x = t[mask]
    finite = np.isfinite(y) & np.isfinite(x)
    if int(np.count_nonzero(finite)) < 2:
        return None
    return MetricCurve(
        cycle_idx=series.cycle_idx,
        cycle_number=series.cycle_number,
        time_s=series.time_s,
        current_a=series.current_a,
        voltage_v=series.voltage_v,
        metric_x=x[finite],
        metric_y=y[finite],
    )


_FEATURE_EXTRA_SPECS: dict[str, tuple[bool, str, str, str, Callable[[float, float], _CurveFn]]] = {
    "dqdv_dis_vs_time": (False, "dQ/dV vs time",          "Time (s)", "dQ/dV (Ah/V)",
                         lambda min_dv, _: lambda s: _build_dqdv_time_curve(s, min_dv=min_dv)),
    "dqdv_chg_vs_time": (True,  "dQ/dV (charge) vs time", "Time (s)", "dQ/dV (Ah/V)",
                         lambda min_dv, _: lambda s: _build_dqdv_time_curve(s, min_dv=min_dv)),
    "dvdq_dis_vs_time": (False, "dV/dQ vs time",          "Time (s)", "dV/dQ (V/Ah)",
                         lambda _, min_dq: lambda s: _build_dvdq_time_curve(s, min_dq=min_dq)),
    "dvdq_chg_vs_time": (True,  "dV/dQ (charge) vs time", "Time (s)", "dV/dQ (V/Ah)",
                         lambda _, min_dq: lambda s: _build_dvdq_time_curve(s, min_dq=min_dq)),
}


_FEATURE_SPECS = {**_MULTI_SPECS, **_FEATURE_EXTRA_SPECS}


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

    if kind in _FEATURE_SPECS:
        charge, title, xlabel, ylabel, make_fn = _FEATURE_SPECS[kind]
        curve_fn = make_fn(min_dv, min_dq)
        builder = build_charge_metric_curves if charge else build_metric_curves
        curves = builder(obj, indices, curve_fn)
        # All discharge-side plots → blue gradient; all charge-side plots → red gradient
        palette = "red" if charge else "blue"
        return make_three_panels(curves, title, xlabel, ylabel,
                                 filter_outliers=filter_outliers, palette=palette)

    if kind in ("dqdv_both", "dvdq_both", "dqdv_time_both", "dvdq_time_both"):
        if kind == "dqdv_both":
            curve_fn = lambda s: build_dqdv_curve(s, min_dv=min_dv)
            title = f"dQ/dV vs voltage (charge + discharge) - {cell_name}"
            xlabel = "Voltage (V)"
            ylabel = "dQ/dV (Ah/V)"
        elif kind == "dvdq_both":
            curve_fn = lambda s: build_dvdq_curve(s, min_dq=min_dq)
            title = f"dV/dQ vs voltage (charge + discharge) - {cell_name}"
            xlabel = "Voltage (V)"
            ylabel = "dV/dQ (V/Ah)"
        elif kind == "dqdv_time_both":
            curve_fn = lambda s: _build_dqdv_time_curve(s, min_dv=min_dv)
            title = f"dQ/dV vs time (charge + discharge) - {cell_name}"
            xlabel = "Time (s)"
            ylabel = "dQ/dV (Ah/V)"
        else:
            curve_fn = lambda s: _build_dvdq_time_curve(s, min_dq=min_dq)
            title = f"dV/dQ vs time (charge + discharge) - {cell_name}"
            xlabel = "Time (s)"
            ylabel = "dV/dQ (V/Ah)"
        discharge_curves = build_metric_curves(obj, indices, curve_fn)
        charge_curves = build_charge_metric_curves(obj, indices, curve_fn)
        return make_charge_discharge_overlay(
            discharge_curves, charge_curves, title, xlabel, ylabel,
            filter_outliers=filter_outliers,
        )

    if kind == "qcmax":
        cx, qv = compute_qcmax_by_cycle(obj)
        return make_qmax_panel(cx, qv, f"Qcmax vs cycle - {cell_name}", "Qcmax (Ah)",
                               filter_outliers=filter_outliers, palette="red")
    if kind == "qdmax":
        cx, qv = compute_qdmax_by_cycle(obj)
        return make_qmax_panel(cx, qv, f"Qdmax vs cycle - {cell_name}", "Qdmax (Ah)",
                               filter_outliers=filter_outliers, palette="blue")

    raise ValueError(f"Unknown plot kind: {kind}")


# ── Feature Analyse: difference vs reference cycle ───────────────────────────

def _diff_curve(ref: MetricCurve, target: MetricCurve) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate target.metric_y onto ref.metric_x, return (x_ref, target_y - ref_y)."""
    rx = np.asarray(ref.metric_x, dtype=float)
    ry = np.asarray(ref.metric_y, dtype=float)
    tx = np.asarray(target.metric_x, dtype=float)
    ty = np.asarray(target.metric_y, dtype=float)
    rmask = np.isfinite(rx) & np.isfinite(ry)
    tmask = np.isfinite(tx) & np.isfinite(ty)
    rx, ry = rx[rmask], ry[rmask]
    tx, ty = tx[tmask], ty[tmask]
    if rx.size < 2 or tx.size < 2:
        return rx, np.full_like(rx, np.nan)
    # np.interp needs strictly increasing x. Sort if not.
    if np.any(np.diff(tx) < 0):
        order = np.argsort(tx)
        tx, ty = tx[order], ty[order]
    # Restrict to overlap to avoid linear-extrapolation artifacts.
    lo = max(rx.min(), tx.min())
    hi = min(rx.max(), tx.max())
    grid_mask = (rx >= lo) & (rx <= hi)
    grid = rx[grid_mask]
    if grid.size < 2:
        return rx, np.full_like(rx, np.nan)
    interp = np.interp(grid, tx, ty)
    diff = interp - ry[grid_mask]
    return grid, diff


def _make_diff_panel(
    ref: MetricCurve,
    targets: list[MetricCurve],
    title: str,
    xlabel: str,
    ylabel: str,
    *,
    filter_outliers: bool,
    palette: str | None = None,
) -> go.Figure:
    """Plot Δy = y_target - y_ref vs ref.metric_x for each target cycle."""
    fig = go.Figure()
    sorted_targets = sorted(targets, key=lambda c: c.cycle_idx)
    grad = _palette_for(sorted_targets, palette)

    all_x: list[np.ndarray] = []
    all_y: list[np.ndarray] = []
    for i, t in enumerate(sorted_targets):
        if t.cycle_idx == ref.cycle_idx:
            continue  # skip self-difference
        x_diff, y_diff = _diff_curve(ref, t)
        if x_diff.size == 0:
            continue
        label = f"Δ {_cycle_label(t)}"
        fig.add_trace(go.Scatter(
            x=x_diff, y=y_diff, mode="lines",
            line=dict(color=grad[i], width=1.4),
            name=label,
            hovertemplate=f"{label}<br>{xlabel}: %{{x:.5g}}<br>{ylabel}: %{{y:.5g}}<extra></extra>",
            showlegend=False,
        ))
        all_x.append(x_diff)
        all_y.append(y_diff)

    fig.add_hline(y=0, line=dict(color="#94a3b8", width=1, dash="dot"))
    fig.update_xaxes(title_text=xlabel)
    fig.update_yaxes(title_text=ylabel)
    if filter_outliers and all_x:
        xr = _robust_range(np.concatenate(all_x))
        yr = _robust_range(np.concatenate(all_y))
        if xr is not None:
            fig.layout.xaxis.range = list(xr)
        if yr is not None:
            fig.layout.yaxis.range = list(yr)
    return _apply_layout(fig, title, height=480)


def _make_v_vs_time_diff(
    obj: dict[str, Any],
    indices: list[int],
    ref_idx: int,
    *,
    charge: bool,
    title: str,
    palette: str | None,
    filter_outliers: bool,
) -> list[go.Figure]:
    """For each target cycle, plot V_target(t) - V_ref(t) vs time within the same half-cycle."""
    builder = build_charge_metric_curves if charge else build_metric_curves
    # We just need the raw voltage_v / time_s arrays; metric_x/y are unused here.
    curve_fn = lambda s: build_qd_vs_voltage_curve(s)
    all_indices = sorted(set(indices) | {ref_idx})
    curves = builder(obj, all_indices, curve_fn)
    if not curves:
        raise ValueError("No cycle data available for V vs time.")

    ref_curve = next((c for c in curves if c.cycle_idx == ref_idx), None)
    if ref_curve is None:
        raise ValueError(f"Reference cycle {ref_idx} has no voltage data for this half-cycle.")

    ref_t = np.asarray(ref_curve.time_s, dtype=float)
    ref_v = np.asarray(ref_curve.voltage_v, dtype=float)
    rmask = np.isfinite(ref_t) & np.isfinite(ref_v)
    ref_t, ref_v = ref_t[rmask], ref_v[rmask]
    if ref_t.size < 2:
        raise ValueError("Reference cycle has insufficient data points.")
    # Normalise time to start at 0 so cycles overlap on the same axis.
    ref_t = ref_t - ref_t[0]

    targets = sorted([c for c in curves if c.cycle_idx != ref_idx], key=lambda c: c.cycle_idx)
    grad = _palette_for(targets, palette)

    fig = go.Figure()
    all_y: list[np.ndarray] = []
    for i, t in enumerate(targets):
        tt = np.asarray(t.time_s, dtype=float)
        tv = np.asarray(t.voltage_v, dtype=float)
        m = np.isfinite(tt) & np.isfinite(tv)
        tt, tv = tt[m], tv[m]
        if tt.size < 2:
            continue
        tt = tt - tt[0]
        # Interpolate target voltage onto reference time grid (overlap range only).
        lo = max(ref_t.min(), tt.min())
        hi = min(ref_t.max(), tt.max())
        grid_mask = (ref_t >= lo) & (ref_t <= hi)
        grid = ref_t[grid_mask]
        if grid.size < 2:
            continue
        # Ensure monotonic time for np.interp
        if np.any(np.diff(tt) < 0):
            order = np.argsort(tt)
            tt, tv = tt[order], tv[order]
        v_interp = np.interp(grid, tt, tv)
        diff = v_interp - ref_v[grid_mask]
        label = f"Δ {_cycle_label(t)}"
        fig.add_trace(go.Scatter(
            x=grid, y=diff, mode="lines",
            line=dict(color=grad[i], width=1.4),
            name=label, showlegend=False,
            hovertemplate=f"{label}<br>Time: %{{x:.4g}} s<br>ΔV: %{{y:.5g}} V<extra></extra>",
        ))
        all_y.append(diff)

    fig.add_hline(y=0, line=dict(color="#94a3b8", width=1, dash="dot"))
    fig.update_xaxes(title_text="Time (s)")
    ylabel = "ΔVcharge (V)" if charge else "ΔVdischarge (V)"
    fig.update_yaxes(title_text=ylabel)
    if filter_outliers and all_y:
        yr = _robust_range(np.concatenate(all_y))
        if yr is not None:
            fig.layout.yaxis.range = list(yr)
    return [_apply_layout(fig, title, height=480)]


# ── log⟨|Δ metric|⟩ feature scalars ──────────────────────────────────────────

# kind → (charge_side, mode, curve_fn_factory, short_ylabel, metric_label)
#   mode: "metric" → use metric_x/metric_y diff vs voltage
#         "vtime"  → use voltage_v vs time_s diff
#   agg:  "avg" | "max" | "min"  — how to aggregate |Δ| values before log10
_LOGAVG_SPECS: dict[str, tuple[bool, str, Callable[[float, float], _CurveFn], str, str, str]] = {
    # ── avg ──────────────────────────────────────────────────────────────────
    "logavg_dqdv_dis_vs_cycle": (False, "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log⟨|Δ dQ/dV|⟩", "dQ/dV - discharge", "avg"),
    "logavg_dqdv_chg_vs_cycle": (True,  "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log⟨|Δ dQ/dV|⟩", "dQ/dV - charge", "avg"),
    "logavg_dvdq_dis_vs_cycle": (False, "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log⟨|Δ dV/dQ|⟩", "dV/dQ - discharge", "avg"),
    "logavg_dvdq_chg_vs_cycle": (True,  "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log⟨|Δ dV/dQ|⟩", "dV/dQ - charge", "avg"),
    "logavg_q_dis_vs_cycle":    (False, "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log⟨|Δ Q|⟩", "Q - discharge", "avg"),
    "logavg_q_chg_vs_cycle":    (True,  "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log⟨|Δ Q|⟩", "Q - charge", "avg"),
    "logavg_v_dis_vs_cycle":    (False, "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log⟨|Δ V|⟩", "V - discharge", "avg"),
    "logavg_v_chg_vs_cycle":    (True,  "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log⟨|Δ V|⟩", "V - charge", "avg"),
    # ── max ──────────────────────────────────────────────────────────────────
    "logmax_dqdv_dis_vs_cycle": (False, "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log(max|Δ dQ/dV|)", "dQ/dV - discharge", "max"),
    "logmax_dqdv_chg_vs_cycle": (True,  "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log(max|Δ dQ/dV|)", "dQ/dV - charge", "max"),
    "logmax_dvdq_dis_vs_cycle": (False, "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log(max|Δ dV/dQ|)", "dV/dQ - discharge", "max"),
    "logmax_dvdq_chg_vs_cycle": (True,  "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log(max|Δ dV/dQ|)", "dV/dQ - charge", "max"),
    "logmax_q_dis_vs_cycle":    (False, "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(max|Δ Q|)", "Q - discharge", "max"),
    "logmax_q_chg_vs_cycle":    (True,  "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(max|Δ Q|)", "Q - charge", "max"),
    "logmax_v_dis_vs_cycle":    (False, "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(max|Δ V|)", "V - discharge", "max"),
    "logmax_v_chg_vs_cycle":    (True,  "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(max|Δ V|)", "V - charge", "max"),
    # ── min ──────────────────────────────────────────────────────────────────
    "logmin_dqdv_dis_vs_cycle": (False, "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log(min|Δ dQ/dV|)", "dQ/dV - discharge", "min"),
    "logmin_dqdv_chg_vs_cycle": (True,  "metric",
                                  lambda mdv, _: lambda s: build_dqdv_curve(s, min_dv=mdv),
                                  "log(min|Δ dQ/dV|)", "dQ/dV - charge", "min"),
    "logmin_dvdq_dis_vs_cycle": (False, "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log(min|Δ dV/dQ|)", "dV/dQ - discharge", "min"),
    "logmin_dvdq_chg_vs_cycle": (True,  "metric",
                                  lambda _, mdq: lambda s: build_dvdq_curve(s, min_dq=mdq),
                                  "log(min|Δ dV/dQ|)", "dV/dQ - charge", "min"),
    "logmin_q_dis_vs_cycle":    (False, "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(min|Δ Q|)", "Q - discharge", "min"),
    "logmin_q_chg_vs_cycle":    (True,  "metric",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(min|Δ Q|)", "Q - charge", "min"),
    "logmin_v_dis_vs_cycle":    (False, "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(min|Δ V|)", "V - discharge", "min"),
    "logmin_v_chg_vs_cycle":    (True,  "vtime",
                                  lambda *_: build_qd_vs_voltage_curve,
                                  "log(min|Δ V|)", "V - charge", "min"),
}


def _log_avg_abs(arr: np.ndarray) -> float | None:
    """log10(mean(|values|)) over finite entries; None if empty or mean ≤ 0."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    avg = float(np.mean(np.abs(a)))
    if avg <= 0:
        return None
    return float(np.log10(avg))


def _logavg_metric_per_cycle(
    obj: dict[str, Any],
    indices: list[int],
    ref_idx: int,
    *,
    charge: bool,
    curve_fn: _CurveFn,
) -> dict[int, tuple[float, int | None]]:
    """For each cycle in `indices`, return log10(mean(|y_target_interp − y_ref|)) on the metric (vs voltage)."""
    builder = build_charge_metric_curves if charge else build_metric_curves
    all_indices = sorted(set(indices) | {ref_idx})
    curves = builder(obj, all_indices, curve_fn)
    ref = next((c for c in curves if c.cycle_idx == ref_idx), None)
    if ref is None:
        raise ValueError(f"Reference cycle {ref_idx} missing for this metric.")
    out: dict[int, tuple[float, int | None]] = {}
    for c in curves:
        if c.cycle_idx == ref_idx:
            continue
        _x, y_diff = _diff_curve(ref, c)
        v = _log_avg_abs(y_diff)
        if v is not None:
            out[c.cycle_idx] = (v, c.cycle_number)
    return out


def _logavg_v_vs_time_per_cycle(
    obj: dict[str, Any],
    indices: list[int],
    ref_idx: int,
    *,
    charge: bool,
) -> dict[int, tuple[float, int | None]]:
    """For each cycle, return log10(mean(|V_target(t) − V_ref(t)|)) on the time-aligned half-cycle."""
    builder = build_charge_metric_curves if charge else build_metric_curves
    curve_fn = lambda s: build_qd_vs_voltage_curve(s)
    all_indices = sorted(set(indices) | {ref_idx})
    curves = builder(obj, all_indices, curve_fn)
    ref = next((c for c in curves if c.cycle_idx == ref_idx), None)
    if ref is None:
        raise ValueError(f"Reference cycle {ref_idx} missing for V-vs-time.")
    ref_t = np.asarray(ref.time_s, dtype=float)
    ref_v = np.asarray(ref.voltage_v, dtype=float)
    rmask = np.isfinite(ref_t) & np.isfinite(ref_v)
    ref_t, ref_v = ref_t[rmask], ref_v[rmask]
    if ref_t.size < 2:
        raise ValueError("Reference cycle has insufficient V/t data.")
    ref_t = ref_t - ref_t[0]

    out: dict[int, tuple[float, int | None]] = {}
    for c in curves:
        if c.cycle_idx == ref_idx:
            continue
        tt = np.asarray(c.time_s, dtype=float)
        tv = np.asarray(c.voltage_v, dtype=float)
        m = np.isfinite(tt) & np.isfinite(tv)
        tt, tv = tt[m], tv[m]
        if tt.size < 2:
            continue
        tt = tt - tt[0]
        if np.any(np.diff(tt) < 0):
            order = np.argsort(tt)
            tt, tv = tt[order], tv[order]
        lo = max(ref_t.min(), tt.min())
        hi = min(ref_t.max(), tt.max())
        gm = (ref_t >= lo) & (ref_t <= hi)
        grid = ref_t[gm]
        if grid.size < 2:
            continue
        v_interp = np.interp(grid, tt, tv)
        diff = v_interp - ref_v[gm]
        v = _log_avg_abs(diff)
        if v is not None:
            out[c.cycle_idx] = (v, c.cycle_number)
    return out


def _lograw_metric_per_cycle(
    obj: dict[str, Any],
    indices: list[int],
    *,
    charge: bool,
    curve_fn: _CurveFn,
) -> dict[int, tuple[float, int | None]]:
    """For each cycle, return log10(mean(|metric_y|)) without reference subtraction."""
    builder = build_charge_metric_curves if charge else build_metric_curves
    curves = builder(obj, indices, curve_fn)
    out: dict[int, tuple[float, int | None]] = {}
    for c in curves:
        v = _log_avg_abs(np.asarray(c.metric_y, dtype=float))
        if v is not None:
            out[c.cycle_idx] = (v, c.cycle_number)
    return out


def _lograw_v_vs_time_per_cycle(
    obj: dict[str, Any],
    indices: list[int],
    *,
    charge: bool,
) -> dict[int, tuple[float, int | None]]:
    """For each cycle, return log10(mean(|V(t)|)) without reference subtraction."""
    builder = build_charge_metric_curves if charge else build_metric_curves
    curve_fn = lambda s: build_qd_vs_voltage_curve(s)
    curves = builder(obj, indices, curve_fn)
    out: dict[int, tuple[float, int | None]] = {}
    for c in curves:
        v = _log_avg_abs(np.asarray(c.voltage_v, dtype=float))
        if v is not None:
            out[c.cycle_idx] = (v, c.cycle_number)
    return out


def _log_ylabel(ylabel: str, *, use_reference_cycle: bool) -> str:
    if use_reference_cycle:
        return ylabel
    return ylabel.replace("|Δ ", "|").replace("|Δ", "|")


def _make_logavg_vs_cycle_figure(
    results: dict[int, tuple[float, int | None]],
    title: str,
    ylabel: str,
    *,
    palette: str,
    filter_outliers: bool,
) -> go.Figure:
    """Plot the per-cycle log-avg-Δ scalar vs cycle (line + markers)."""
    if not results:
        raise ValueError("No cycles produced a valid log-avg value.")
    items = sorted(results.items(), key=lambda kv: kv[0])
    xs = [(num if num is not None else idx) for idx, (_v, num) in items]
    ys = [v for _idx, (v, _num) in items]
    line_color = f"rgb{_GRADIENT_STOPS.get(palette, _GRADIENT_BLUE)[0]}"
    fig = go.Figure(data=[go.Scatter(
        x=xs, y=ys, mode="lines+markers",
        line=dict(color=line_color, width=1.8),
        marker=dict(size=6, color=line_color),
        hovertemplate=f"Cycle: %{{x}}<br>{ylabel}: %{{y:.4g}}<extra></extra>",
        showlegend=False,
    )])
    fig.update_xaxes(title_text="Cycle")
    fig.update_yaxes(title_text=ylabel)
    if filter_outliers:
        r = _robust_range(np.asarray(ys, dtype=float))
        if r is not None:
            fig.layout.yaxis.range = list(r)
    return _apply_layout(fig, title, height=460)


def compute_logavg_for_file(
    obj: dict[str, Any],
    *,
    kind: str,
    reference_cycle: int,
    target_cycle: int,
    use_reference_cycle: bool = True,
    min_dv: float = 1e-4,
    min_dq: float = 1e-5,
) -> float | None:
    """For one PKL: compute log-feature (vs reference) up to target_cycle.

    agg="avg" (logavg_*): evaluate at the single target_cycle index.
    agg="max" (logmax_*): compute log⟨|Δ|⟩ for every cycle from reference+1 to target_cycle,
                          return the maximum of those per-cycle values.
    agg="min" (logmin_*): same but return the minimum.

    target_cycle is a cycle-data index (0-based).
    """
    if kind not in _LOGAVG_SPECS:
        raise ValueError(f"Unsupported logavg kind: {kind}")
    cycle_data = get_cycle_data(obj)
    n = len(cycle_data)
    if use_reference_cycle and not (0 <= reference_cycle < n):
        return None
    if not (0 <= target_cycle < n):
        return None
    if use_reference_cycle and reference_cycle == target_cycle:
        return None  # Δ to self = 0 → log undefined

    charge, mode, fn_factory, _ylabel, _, agg = _LOGAVG_SPECS[kind]

    if agg == "avg":
        # Single target cycle only
        indices = [target_cycle]
        if mode == "vtime":
            if use_reference_cycle:
                d = _logavg_v_vs_time_per_cycle(obj, indices, reference_cycle, charge=charge)
            else:
                d = _lograw_v_vs_time_per_cycle(obj, indices, charge=charge)
        else:
            curve_fn = fn_factory(min_dv, min_dq)
            if use_reference_cycle:
                d = _logavg_metric_per_cycle(obj, indices, reference_cycle, charge=charge, curve_fn=curve_fn)
            else:
                d = _lograw_metric_per_cycle(obj, indices, charge=charge, curve_fn=curve_fn)
        pair = d.get(target_cycle)
        return pair[0] if pair else None
    else:
        # With a reference, aggregate from reference to target. Without one,
        # aggregate from cycle index 0 through target.
        lo = min(reference_cycle, target_cycle) if use_reference_cycle else 0
        hi = max(reference_cycle, target_cycle) if use_reference_cycle else target_cycle
        indices = list(range(lo, hi + 1))
        if mode == "vtime":
            if use_reference_cycle:
                d = _logavg_v_vs_time_per_cycle(obj, indices, reference_cycle, charge=charge)
            else:
                d = _lograw_v_vs_time_per_cycle(obj, indices, charge=charge)
        else:
            curve_fn = fn_factory(min_dv, min_dq)
            if use_reference_cycle:
                d = _logavg_metric_per_cycle(obj, indices, reference_cycle, charge=charge, curve_fn=curve_fn)
            else:
                d = _lograw_metric_per_cycle(obj, indices, charge=charge, curve_fn=curve_fn)
        if not d:
            return None
        vals = [v for (v, _) in d.values()]
        if agg == "max":
            return float(max(vals))
        else:  # "min"
            return float(min(vals))


def compute_logavg_and_lifetime_for_file(
    obj: dict[str, Any],
    *,
    kind: str,
    reference_cycle: int,
    target_cycle: int,
    use_reference_cycle: bool = True,
    min_dv: float = 1e-4,
    min_dq: float = 1e-5,
) -> tuple[float, int | None, int | None] | None:
    """Return (log_value, eol_cycle_or_None, max_cycle_or_None) for one PKL, or None on failure.

    eol_cycle: cycle number where Qd first drops to 80% of initial. None if cell never reached EOL.
    max_cycle: highest cycle number in the file (proxy for total cycles the cell ran).
    """
    log_val = compute_logavg_for_file(
        obj, kind=kind,
        reference_cycle=reference_cycle, target_cycle=target_cycle,
        use_reference_cycle=use_reference_cycle,
        min_dv=min_dv, min_dq=min_dq,
    )
    if log_val is None:
        return None
    eol: int | None = None
    max_cycle: int | None = None
    try:
        cx, qv = compute_qdmax_by_cycle(obj)
        cx_arr = np.asarray(cx, dtype=float)
        if cx_arr.size:
            mask = np.isfinite(cx_arr)
            if mask.any():
                max_cycle = int(np.max(cx_arr[mask]))
        eol = _eol_cycle_from_qd(cx, qv, threshold=0.8)
    except Exception:
        pass
    return (log_val, eol, max_cycle)


def build_folder_logavg_figure(
    file_results: list[tuple[str, float, int | None, int | None]],
    folder_name: str,
    *,
    kind: str,
    reference_cycle: int,
    target_cycle: int,
    use_reference_cycle: bool = True,
    filter_outliers: bool = False,
) -> list[go.Figure]:
    """Scatter plot: x = log⟨|Δ metric|⟩ at target cycle, y = cell cycle-life (EOL @ 80% Qd).

    One point per `.pkl` in the folder. Files that never reached EOL fall back to max cycle
    and are drawn as open diamonds so they're distinguishable.
    """
    if not file_results:
        raise ValueError("No files produced a valid log-avg value.")
    if kind not in _LOGAVG_SPECS:
        raise ValueError(f"Unsupported logavg kind: {kind}")
    _charge, _mode, _fn, base_ylabel, metric_label, _agg = _LOGAVG_SPECS[kind]
    ylabel = _log_ylabel(base_ylabel, use_reference_cycle=use_reference_cycle)

    eol_xs: list[float] = []
    eol_ys: list[int] = []
    eol_names: list[str] = []
    nool_xs: list[float] = []
    nool_ys: list[int] = []
    nool_names: list[str] = []

    for name, log_val, eol, max_c in file_results:
        if eol is not None:
            eol_xs.append(log_val)
            eol_ys.append(int(eol))
            eol_names.append(name)
        elif max_c is not None:
            nool_xs.append(log_val)
            nool_ys.append(int(max_c))
            nool_names.append(name)

    if not eol_xs and not nool_xs:
        raise ValueError("No files have valid Qd cycle data to determine cycle life.")

    fig = go.Figure()

    # Files that reached EOL — solid coloured circles with viridis gradient by cycle life
    if eol_xs:
        fig.add_trace(go.Scatter(
            x=eol_xs, y=eol_ys, mode="markers",
            marker=dict(
                size=10,
                color=eol_ys,
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(
                    title=dict(text="Cycle life", side="right"),
                    thickness=14,
                    len=0.85,
                    outlinewidth=0,
                ),
                line=dict(color="rgba(0,0,0,0.35)", width=0.6),
            ),
            text=eol_names,
            name="reached EOL",
            hovertemplate=(
                f"%{{text}}<br>{ylabel}: %{{x:.4g}}"
                f"<br>Cycle life (EOL @80%): %{{y}}<extra></extra>"
            ),
            showlegend=False,
        ))

    # Files that never reached EOL — open diamonds, x = log, y = max cycle
    if nool_xs:
        fig.add_trace(go.Scatter(
            x=nool_xs, y=nool_ys, mode="markers",
            marker=dict(
                symbol="diamond-open",
                size=10,
                color="#94a3b8",
                line=dict(color="#475569", width=1.2),
            ),
            text=nool_names,
            name="not reached EOL (max cycle shown)",
            hovertemplate=(
                f"%{{text}}<br>{ylabel}: %{{x:.4g}}"
                f"<br>Max cycle (no EOL): %{{y}}<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.update_xaxes(title_text=ylabel)
    fig.update_yaxes(title_text="Cycle life (EOL @ 80% Qd)")
    if filter_outliers:
        all_x = np.array(eol_xs + nool_xs, dtype=float)
        all_y = np.array(eol_ys + nool_ys, dtype=float)
        xr = _robust_range(all_x)
        yr = _robust_range(all_y)
        if xr is not None:
            fig.layout.xaxis.range = list(xr)
        if yr is not None:
            fig.layout.yaxis.range = list(yr)

    if not use_reference_cycle:
        if _agg == "avg":
            cycle_desc = f"cycle {target_cycle}"
        else:
            cycle_desc = f"cycle 0 → cycle {target_cycle}"
    elif _agg == "avg":
        cycle_desc = f"cycle {target_cycle} vs ref {reference_cycle}"
    else:
        cycle_desc = f"ref {reference_cycle} → cycle {target_cycle}"
    title = (f"{ylabel} of {metric_label} ({cycle_desc}) "
             f"vs cycle life — {folder_name}")
    return [_apply_layout(fig, title, height=520)]


def build_feature_figures_for_kind(
    obj: dict[str, Any],
    *,
    cell_name: str,
    kind: str,
    cycles: str | None,
    reference_cycle: int,
    use_reference_cycle: bool = True,
    min_dv: float = 1e-4,
    min_dq: float = 1e-5,
    filter_outliers: bool = False,
    palette_pair: tuple[str, str] = ("blue", "red"),
) -> list[go.Figure]:
    """Build difference-from-reference plots used by the Feature Analyse tab.

    palette_pair: (discharge_palette, charge_palette) — used to override colours
    when overlaying multiple files (e.g. file A=("blue","red"), file B=("green","purple")).
    """
    cycle_data = get_cycle_data(obj)
    if use_reference_cycle and not (0 <= reference_cycle < len(cycle_data)):
        raise ValueError(f"Reference cycle {reference_cycle} out of range "
                         f"(valid: 0..{len(cycle_data) - 1}).")
    indices = resolve_cycles(len(cycle_data), cycles)
    if use_reference_cycle and reference_cycle not in indices:
        indices = sorted(set(indices) | {reference_cycle})

    pal_dis, pal_chg = palette_pair

    # Per-cycle log⟨|Δ metric|⟩ scalars plotted vs cycle
    if kind in _LOGAVG_SPECS:
        charge, mode, fn_factory, base_ylabel, metric_label, _agg = _LOGAVG_SPECS[kind]
        ylabel = _log_ylabel(base_ylabel, use_reference_cycle=use_reference_cycle)
        palette = pal_chg if charge else pal_dis
        if mode == "vtime":
            if use_reference_cycle:
                results = _logavg_v_vs_time_per_cycle(obj, indices, reference_cycle, charge=charge)
            else:
                results = _lograw_v_vs_time_per_cycle(obj, indices, charge=charge)
        else:
            curve_fn = fn_factory(min_dv, min_dq)
            if use_reference_cycle:
                results = _logavg_metric_per_cycle(obj, indices, reference_cycle,
                                                   charge=charge, curve_fn=curve_fn)
            else:
                results = _lograw_metric_per_cycle(obj, indices, charge=charge, curve_fn=curve_fn)
        if use_reference_cycle:
            title = f"{ylabel} of {metric_label} vs cycle (ref = cycle_idx {reference_cycle}) - {cell_name}"
        else:
            title = f"{ylabel} of {metric_label} vs cycle - {cell_name}"
        return [_make_logavg_vs_cycle_figure(results, title, ylabel,
                                             palette=palette, filter_outliers=filter_outliers)]

    if not use_reference_cycle:
        if kind == "vdis_vs_time":
            curves = build_metric_curves(obj, indices, build_qd_vs_voltage_curve)
            fig = _multi_line_panel(
                f"Vdischarge vs time - {cell_name}", "Time (s)", "Vdischarge (V)",
                curves, "time_s", "voltage_v",
                filter_outliers=filter_outliers, palette=pal_dis,
            )
            return [fig]
        if kind == "vchg_vs_time":
            curves = build_charge_metric_curves(obj, indices, build_qd_vs_voltage_curve)
            fig = _multi_line_panel(
                f"Vcharge vs time - {cell_name}", "Time (s)", "Vcharge (V)",
                curves, "time_s", "voltage_v",
                filter_outliers=filter_outliers, palette=pal_chg,
            )
            return [fig]

        if kind in ("dqdv_both", "dvdq_both", "dqdv_time_both", "dvdq_time_both"):
            if kind == "dqdv_both":
                curve_fn = lambda s: build_dqdv_curve(s, min_dv=min_dv)
                title = f"dQ/dV vs voltage (charge + discharge) - {cell_name}"
                xlabel = "Voltage (V)"
                ylabel = "dQ/dV (Ah/V)"
            elif kind == "dvdq_both":
                curve_fn = lambda s: build_dvdq_curve(s, min_dq=min_dq)
                title = f"dV/dQ vs voltage (charge + discharge) - {cell_name}"
                xlabel = "Voltage (V)"
                ylabel = "dV/dQ (V/Ah)"
            elif kind == "dqdv_time_both":
                curve_fn = lambda s: _build_dqdv_time_curve(s, min_dv=min_dv)
                title = f"dQ/dV vs time (charge + discharge) - {cell_name}"
                xlabel = "Time (s)"
                ylabel = "dQ/dV (Ah/V)"
            else:
                curve_fn = lambda s: _build_dvdq_time_curve(s, min_dq=min_dq)
                title = f"dV/dQ vs time (charge + discharge) - {cell_name}"
                xlabel = "Time (s)"
                ylabel = "dV/dQ (V/Ah)"
            discharge_curves = build_metric_curves(obj, indices, curve_fn)
            charge_curves = build_charge_metric_curves(obj, indices, curve_fn)
            return make_charge_discharge_overlay(
                discharge_curves, charge_curves, title, xlabel, ylabel,
                filter_outliers=filter_outliers,
                palette_pair=palette_pair,
            )

        if kind in _FEATURE_SPECS:
            charge, base_title, xlabel, ylabel, make_fn = _FEATURE_SPECS[kind]
            curve_fn = make_fn(min_dv, min_dq)
            builder = build_charge_metric_curves if charge else build_metric_curves
            curves = builder(obj, indices, curve_fn)
            palette = pal_chg if charge else pal_dis
            fig = _multi_line_panel(
                f"{base_title} - {cell_name}", xlabel, ylabel,
                curves, "metric_x", "metric_y",
                filter_outliers=filter_outliers, palette=palette,
            )
            return [fig]

    # Voltage difference vs time within a half-cycle
    if kind == "vdis_vs_time":
        return _make_v_vs_time_diff(
            obj, indices, reference_cycle,
            charge=False,
            title=f"ΔVdischarge vs time (ref = cycle_idx {reference_cycle}) - {cell_name}",
            palette=pal_dis, filter_outliers=filter_outliers,
        )
    if kind == "vchg_vs_time":
        return _make_v_vs_time_diff(
            obj, indices, reference_cycle,
            charge=True,
            title=f"ΔVcharge vs time (ref = cycle_idx {reference_cycle}) - {cell_name}",
            palette=pal_chg, filter_outliers=filter_outliers,
        )

    # Combined charge + discharge difference plots
    if kind in ("dqdv_both", "dvdq_both", "dqdv_time_both", "dvdq_time_both"):
        if kind == "dqdv_both":
            curve_fn = lambda s: build_dqdv_curve(s, min_dv=min_dv)
            title = f"Δ dQ/dV vs voltage (charge + discharge, ref = cycle_idx {reference_cycle}) - {cell_name}"
            xlabel = "Voltage (V)"
            ylabel = "Δ dQ/dV (Ah/V)"
        elif kind == "dvdq_both":
            curve_fn = lambda s: build_dvdq_curve(s, min_dq=min_dq)
            title = f"Δ dV/dQ vs voltage (charge + discharge, ref = cycle_idx {reference_cycle}) - {cell_name}"
            xlabel = "Voltage (V)"
            ylabel = "Δ dV/dQ (V/Ah)"
        elif kind == "dqdv_time_both":
            curve_fn = lambda s: _build_dqdv_time_curve(s, min_dv=min_dv)
            title = f"Δ dQ/dV vs time (charge + discharge, ref = cycle_idx {reference_cycle}) - {cell_name}"
            xlabel = "Time (s)"
            ylabel = "Δ dQ/dV (Ah/V)"
        else:
            curve_fn = lambda s: _build_dvdq_time_curve(s, min_dq=min_dq)
            title = f"Δ dV/dQ vs time (charge + discharge, ref = cycle_idx {reference_cycle}) - {cell_name}"
            xlabel = "Time (s)"
            ylabel = "Δ dV/dQ (V/Ah)"
        d_curves = build_metric_curves(obj, indices, curve_fn)
        c_curves = build_charge_metric_curves(obj, indices, curve_fn)
        d_ref = next((c for c in d_curves if c.cycle_idx == reference_cycle), None)
        c_ref = next((c for c in c_curves if c.cycle_idx == reference_cycle), None)
        if d_ref is None or c_ref is None:
            raise ValueError(f"Reference cycle {reference_cycle} missing charge/discharge data.")

        fig = go.Figure()
        d_targets = sorted([c for c in d_curves if c.cycle_idx != reference_cycle], key=lambda c: c.cycle_idx)
        c_targets = sorted([c for c in c_curves if c.cycle_idx != reference_cycle], key=lambda c: c.cycle_idx)
        d_grad = _gradient_palette(*_GRADIENT_STOPS[pal_dis], len(d_targets))
        c_grad = _gradient_palette(*_GRADIENT_STOPS[pal_chg], len(c_targets))

        all_x: list[np.ndarray] = []
        all_y: list[np.ndarray] = []
        for i, t in enumerate(d_targets):
            x, y = _diff_curve(d_ref, t)
            if x.size == 0:
                continue
            lab = f"discharge Δ {_cycle_label(t)}"
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines",
                                     line=dict(color=d_grad[i], width=1.4, dash="solid"),
                                     name=lab, showlegend=False,
                                     hovertemplate=f"{lab}<br>{xlabel}: %{{x:.5g}}<br>Δ: %{{y:.5g}}<extra></extra>"))
            all_x.append(x); all_y.append(y)
        for i, t in enumerate(c_targets):
            x, y = _diff_curve(c_ref, t)
            if x.size == 0:
                continue
            lab = f"charge Δ {_cycle_label(t)}"
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines",
                                     line=dict(color=c_grad[i], width=1.4, dash="dash"),
                                     name=lab, showlegend=False,
                                     hovertemplate=f"{lab}<br>{xlabel}: %{{x:.5g}}<br>Δ: %{{y:.5g}}<extra></extra>"))
            all_x.append(x); all_y.append(y)

        fig.add_hline(y=0, line=dict(color="#94a3b8", width=1, dash="dot"))
        fig.update_xaxes(title_text=xlabel)
        fig.update_yaxes(title_text=ylabel)
        if filter_outliers and all_x:
            xr = _robust_range(np.concatenate(all_x))
            yr = _robust_range(np.concatenate(all_y))
            if xr is not None:
                fig.layout.xaxis.range = list(xr)
            if yr is not None:
                fig.layout.yaxis.range = list(yr)
        return [_apply_layout(fig, title, height=480)]

    # Single-side dQ/dV, dV/dQ, Qd vs V, Qc vs V difference plots
    if kind in _FEATURE_SPECS:
        charge, base_title, xlabel, ylabel, make_fn = _FEATURE_SPECS[kind]
        curve_fn = make_fn(min_dv, min_dq)
        builder = build_charge_metric_curves if charge else build_metric_curves
        curves = builder(obj, indices, curve_fn)
        ref_curve = next((c for c in curves if c.cycle_idx == reference_cycle), None)
        if ref_curve is None:
            raise ValueError(f"Reference cycle {reference_cycle} missing data for this plot kind.")
        targets = [c for c in curves if c.cycle_idx != reference_cycle]
        title = f"Δ {base_title} (ref = cycle_idx {reference_cycle}) - {cell_name}"
        ylabel_d = f"Δ {ylabel}"
        palette = pal_chg if charge else pal_dis
        fig = _make_diff_panel(
            ref_curve, targets, title, xlabel, ylabel_d,
            filter_outliers=filter_outliers, palette=palette,
        )
        return [fig]

    raise ValueError(f"Unknown feature plot kind: {kind}")


def build_feature_compare_figures(
    obj_a: dict[str, Any],
    obj_b: dict[str, Any],
    *,
    cell_name_a: str,
    cell_name_b: str,
    kind: str,
    cycles: str | None,
    reference_cycle: int,
    use_reference_cycle: bool = True,
    min_dv: float = 1e-4,
    min_dq: float = 1e-5,
    filter_outliers: bool = False,
) -> list[go.Figure]:
    """Build feature plots for two files overlaid on the same axes with distinct palettes."""
    figs_a = build_feature_figures_for_kind(
        obj_a, cell_name=cell_name_a, kind=kind, cycles=cycles,
        reference_cycle=reference_cycle, use_reference_cycle=use_reference_cycle,
        min_dv=min_dv, min_dq=min_dq,
        filter_outliers=filter_outliers, palette_pair=("blue", "red"),
    )
    figs_b = build_feature_figures_for_kind(
        obj_b, cell_name=cell_name_b, kind=kind, cycles=cycles,
        reference_cycle=reference_cycle, use_reference_cycle=use_reference_cycle,
        min_dv=min_dv, min_dq=min_dq,
        filter_outliers=filter_outliers, palette_pair=("orange", "cyan"),
    )

    merged: list[go.Figure] = []
    n = max(len(figs_a), len(figs_b))
    for i in range(n):
        fa = figs_a[i] if i < len(figs_a) else None
        fb = figs_b[i] if i < len(figs_b) else None
        if fa is None:
            merged.append(fb)
            continue
        if fb is None:
            merged.append(fa)
            continue
        # Append B's traces onto A's figure; tag trace names with file label.
        for tr in fa.data:
            tr.name = f"[{cell_name_a}] {tr.name}" if tr.name else cell_name_a
        for tr in fb.data:
            tr.name = f"[{cell_name_b}] {tr.name}" if tr.name else cell_name_b
            fa.add_trace(tr)
        # Update title to show the comparison
        old_title = fa.layout.title.text or ""
        fa.update_layout(title=dict(
            text=f"{old_title}  vs  {cell_name_b}",
            x=0.02, xanchor="left",
        ))
        merged.append(fa)
    return merged


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
    "build_feature_compare_figures",
    "build_feature_figures_for_kind",
    "build_figures_for_kind",
    "build_folder_logavg_figure",
    "capacity_fade_summary",
    "compute_logavg_and_lifetime_for_file",
    "compute_logavg_for_file",
    "compute_cell_metrics",
    "extract_temperature_from_name",
    "load_batteryml_pickle",
    "make_folder_cycle_bar",
    "resolve_cycles",
]
