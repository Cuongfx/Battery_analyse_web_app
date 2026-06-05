from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

# Modern colour palette — categorical (files / subfolders)
MODERN_COLORS: list[str] = [
    "#3A86FF",
    "#FF6B6B",
    "#06D6A0",
    "#FFD60A",
    "#8338EC",
    "#FF9F1C",
    "#2EC4B6",
    "#F72585",
    "#48CAE4",
    "#E63946",
    "#90BE6D",
    "#457B9D",
    "#FB5607",
    "#FFBE0B",
    "#3A0CA3",
]


@dataclass
class DischargeSeries:
    cycle_idx: int
    cycle_number: int | None
    time_s: np.ndarray
    current_a: np.ndarray
    voltage_v: np.ndarray
    qd_ah: np.ndarray


@dataclass
class MetricCurve:
    cycle_idx: int
    cycle_number: int | None
    time_s: np.ndarray
    current_a: np.ndarray
    voltage_v: np.ndarray
    metric_x: np.ndarray
    metric_y: np.ndarray
    source_label: str | None = None


def load_batteryml_pickle(path: Path) -> dict[str, Any]:
    with open(path, "rb") as handle:
        obj = pickle.load(handle)
    if not isinstance(obj, dict):
        raise TypeError(f"Expected root object to be dict, got {type(obj).__name__}")
    return obj


def get_cycle_data(obj: dict[str, Any]) -> list[dict[str, Any]]:
    cycle_data = obj.get("cycle_data")
    if not isinstance(cycle_data, list):
        raise TypeError("'cycle_data' must be a list.")
    return [cycle for cycle in cycle_data if isinstance(cycle, dict)]


def parse_cycle_csv(raw: str) -> list[int]:
    normalized = raw.replace(";", ",").replace(".", ",").replace(" ", ",")
    tokens = [token.strip() for token in normalized.split(",") if token.strip()]
    if not tokens:
        raise ValueError("No cycle indices provided.")

    picked: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        if not token.isdigit():
            raise ValueError(f"Invalid cycle index: {token}")
        value = int(token)
        if value not in seen:
            picked.append(value)
            seen.add(value)
    return picked
def _resolve_cycle_indices_silent(cycle_count: int, requested: str | None) -> list[int]:
    """Non-interactive cycle resolution — clamps indices to the file's range."""
    if cycle_count <= 0:
        return []
    if requested is None or requested.strip().lower() in {"all", "*"}:
        return list(range(cycle_count))
    try:
        indices = parse_cycle_csv(requested)
        return [i for i in indices if 0 <= i < cycle_count]
    except ValueError:
        return list(range(min(5, cycle_count)))

def _as_float_array(value: Any) -> np.ndarray:
    if value is None or not isinstance(value, (list, tuple, np.ndarray)):
        return np.array([], dtype=np.float64)
    try:
        return np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        arr = np.asarray(value).reshape(-1)
        converted = np.full(arr.shape, np.nan, dtype=np.float64)
        for idx, item in enumerate(arr):
            try:
                converted[idx] = float(item)
            except (TypeError, ValueError):
                continue
        return converted


def _extract_cycle_number(cycle: dict[str, Any]) -> int | None:
    value = cycle.get("cycle_number")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_discharge_series(cycle: dict[str, Any], cycle_idx: int) -> DischargeSeries | None:
    time_s = _as_float_array(cycle.get("time_in_s"))
    current_a = _as_float_array(cycle.get("current_in_A"))
    voltage_v = _as_float_array(cycle.get("voltage_in_V"))
    qd_ah = _as_float_array(cycle.get("discharge_capacity_in_Ah"))

    required = [time_s, current_a, voltage_v, qd_ah]
    if any(arr.size == 0 for arr in required):
        return None

    n = min(arr.size for arr in required)
    time_s = time_s[:n]
    current_a = current_a[:n]
    voltage_v = voltage_v[:n]
    qd_ah = qd_ah[:n]

    finite_mask = (
        np.isfinite(time_s)
        & np.isfinite(current_a)
        & np.isfinite(voltage_v)
        & np.isfinite(qd_ah)
    )
    if int(np.count_nonzero(finite_mask)) < 3:
        return None

    time_s = time_s[finite_mask]
    current_a = current_a[finite_mask]
    voltage_v = voltage_v[finite_mask]
    qd_ah = qd_ah[finite_mask]

    qd_shifted = qd_ah - float(np.min(qd_ah))
    qd_range = float(np.max(qd_shifted))
    if qd_range <= 0.0:
        return None

    grow_tol = max(1e-8, qd_range * 1e-6)
    dq = np.diff(qd_shifted)
    growing = dq > grow_tol
    if np.any(growing):
        first = max(0, int(np.flatnonzero(growing)[0]))
        last = min(qd_shifted.size - 1, int(np.flatnonzero(growing)[-1]) + 1)
    else:
        active = qd_shifted > grow_tol
        if not np.any(active):
            return None
        first = max(0, int(np.flatnonzero(active)[0]) - 1)
        last = int(np.flatnonzero(active)[-1])

    if last - first + 1 < 3:
        return None

    time_s = time_s[first : last + 1] - time_s[first]
    current_a = current_a[first : last + 1]
    voltage_v = voltage_v[first : last + 1]
    qd_ah = qd_shifted[first : last + 1]

    return DischargeSeries(
        cycle_idx=cycle_idx,
        cycle_number=_extract_cycle_number(cycle),
        time_s=time_s,
        current_a=current_a,
        voltage_v=voltage_v,
        qd_ah=qd_ah,
    )


def build_dqdv_curve(series: DischargeSeries, min_dv: float = 1e-4) -> MetricCurve | None:
    dq = np.diff(series.qd_ah)
    dv = np.diff(series.voltage_v)
    x = 0.5 * (series.voltage_v[:-1] + series.voltage_v[1:])
    mask = np.isfinite(dq) & np.isfinite(dv) & np.isfinite(x) & (np.abs(dv) >= max(min_dv, 1e-12))
    if int(np.count_nonzero(mask)) < 2:
        return None
    y = dq[mask] / dv[mask]
    x = x[mask]
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


def build_dvdq_curve(series: DischargeSeries, min_dq: float = 1e-5) -> MetricCurve | None:
    dq = np.diff(series.qd_ah)
    dv = np.diff(series.voltage_v)
    x = 0.5 * (series.voltage_v[:-1] + series.voltage_v[1:])
    mask = np.isfinite(dq) & np.isfinite(dv) & np.isfinite(x) & (np.abs(dq) >= max(min_dq, 1e-12))
    if int(np.count_nonzero(mask)) < 2:
        return None
    y = dv[mask] / dq[mask]
    x = x[mask]
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


def build_qd_vs_voltage_curve(series: DischargeSeries) -> MetricCurve | None:
    mask = np.isfinite(series.voltage_v) & np.isfinite(series.qd_ah)
    if int(np.count_nonzero(mask)) < 2:
        return None
    return MetricCurve(
        cycle_idx=series.cycle_idx,
        cycle_number=series.cycle_number,
        time_s=series.time_s,
        current_a=series.current_a,
        voltage_v=series.voltage_v,
        metric_x=series.voltage_v[mask],
        metric_y=series.qd_ah[mask],
    )


def build_metric_curves(
    obj: dict[str, Any],
    cycle_indices: list[int],
    builder: Callable[[DischargeSeries], MetricCurve | None],
    source_label: str | None = None,
) -> list[MetricCurve]:
    cycle_data = get_cycle_data(obj)
    curves: list[MetricCurve] = []
    for cycle_idx in cycle_indices:
        if not 0 <= cycle_idx < len(cycle_data):
            continue
        series = extract_discharge_series(cycle_data[cycle_idx], cycle_idx)
        if series is None:
            continue
        curve = builder(series)
        if curve is not None:
            if source_label is not None:
                curve.source_label = source_label
            curves.append(curve)
    return curves


# ---------------------------------------------------------------------------
# Charge-period extraction
# ---------------------------------------------------------------------------


def extract_charge_series(cycle: dict[str, Any], cycle_idx: int) -> DischargeSeries | None:
    """Extract the charging phase of a cycle.

    Tries ``charge_capacity_in_Ah`` first.  If that key is absent, integrates
    positive current over time to reconstruct charge capacity.  Returns a
    ``DischargeSeries`` whose ``qd_ah`` field holds charge capacity so that all
    existing builder functions (dQ/dV, dV/dQ, Qc vs V) work unchanged.
    """
    time_s = _as_float_array(cycle.get("time_in_s"))
    current_a = _as_float_array(cycle.get("current_in_A"))
    voltage_v = _as_float_array(cycle.get("voltage_in_V"))

    required = [time_s, current_a, voltage_v]
    if any(arr.size == 0 for arr in required):
        return None

    n = min(arr.size for arr in required)
    time_s = time_s[:n]
    current_a = current_a[:n]
    voltage_v = voltage_v[:n]

    qc_raw = _as_float_array(cycle.get("charge_capacity_in_Ah"))
    if qc_raw.size >= n:
        qc_ah = qc_raw[:n]
    else:
        # Integrate positive current (A) × Δt (s) → Ah
        dt = np.zeros(n, dtype=np.float64)
        dt[1:] = np.diff(time_s)
        dq = np.where(np.isfinite(current_a) & (current_a > 0), current_a * dt / 3600.0, 0.0)
        qc_ah = np.cumsum(dq)

    finite_mask = (
        np.isfinite(time_s)
        & np.isfinite(current_a)
        & np.isfinite(voltage_v)
        & np.isfinite(qc_ah)
    )
    if int(np.count_nonzero(finite_mask)) < 3:
        return None

    time_s = time_s[finite_mask]
    current_a = current_a[finite_mask]
    voltage_v = voltage_v[finite_mask]
    qc_ah = qc_ah[finite_mask]

    qc_shifted = qc_ah - float(np.min(qc_ah))
    qc_range = float(np.max(qc_shifted))
    if qc_range <= 0.0:
        return None

    grow_tol = max(1e-8, qc_range * 1e-6)
    dq = np.diff(qc_shifted)

    # Only detect qc growth during positive-current periods so that noise /
    # rounding in the discharge region does not corrupt the window boundaries.
    # A threshold of 0.001 A accommodates CV-taper current without picking up
    # discharge or rest periods.
    charging_mask = current_a > 0.001
    dq_constrained = np.where(charging_mask[:-1], dq, 0.0)

    growing = dq_constrained > grow_tol
    if np.any(growing):
        # Group growing indices into contiguous runs, allowing small gaps so
        # CC→rest→CV transitions are bridged while spurious post-discharge
        # tail increments (separated by a long gap) form their own run.
        MAX_GAP = 20
        growing_idx = np.flatnonzero(growing)
        run_start = int(growing_idx[0])
        run_end = int(growing_idx[0])
        runs: list[tuple[int, int]] = []
        for gi in growing_idx[1:]:
            gi = int(gi)
            if gi - run_end <= MAX_GAP:
                run_end = gi
            else:
                runs.append((run_start, run_end))
                run_start = gi
                run_end = gi
        runs.append((run_start, run_end))
        # Use the longest run — the primary CC+CV charge phase
        best = max(runs, key=lambda r: r[1] - r[0])
        first = max(0, best[0])
        last = min(qc_shifted.size - 1, best[1] + 1)
    else:
        active = (qc_shifted > grow_tol) & charging_mask
        if not np.any(active):
            return None
        first = max(0, int(np.flatnonzero(active)[0]) - 1)
        last = int(np.flatnonzero(active)[-1])

    # Trim any trailing non-positive-current points from the window boundary.
    while last > first and not charging_mask[last]:
        last -= 1

    if last - first + 1 < 3:
        return None

    time_s = time_s[first : last + 1] - time_s[first]
    current_a = current_a[first : last + 1]
    voltage_v = voltage_v[first : last + 1]
    qc_ah = qc_shifted[first : last + 1]

    return DischargeSeries(
        cycle_idx=cycle_idx,
        cycle_number=_extract_cycle_number(cycle),
        time_s=time_s,
        current_a=current_a,
        voltage_v=voltage_v,
        qd_ah=qc_ah,   # charge capacity stored in qd_ah so builders are reusable
    )


def build_charge_metric_curves(
    obj: dict[str, Any],
    cycle_indices: list[int],
    builder: Callable[[DischargeSeries], MetricCurve | None],
    source_label: str | None = None,
) -> list[MetricCurve]:
    """Like ``build_metric_curves`` but extracts the charging phase."""
    cycle_data = get_cycle_data(obj)
    curves: list[MetricCurve] = []
    for cycle_idx in cycle_indices:
        if not 0 <= cycle_idx < len(cycle_data):
            continue
        series = extract_charge_series(cycle_data[cycle_idx], cycle_idx)
        if series is None:
            continue
        curve = builder(series)
        if curve is not None:
            if source_label is not None:
                curve.source_label = source_label
            curves.append(curve)
    return curves


def compute_qcmax_by_cycle(obj: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Maximum charge capacity per cycle."""
    cycle_data = get_cycle_data(obj)
    cycle_axis: list[int] = []
    qcmax_values: list[float] = []
    for cycle_idx, cycle in enumerate(cycle_data):
        series = extract_charge_series(cycle, cycle_idx)
        if series is None or series.qd_ah.size == 0:
            continue
        cycle_axis.append(series.cycle_number if series.cycle_number is not None else cycle_idx)
        qcmax_values.append(float(np.max(series.qd_ah)))

    if not cycle_axis:
        raise ValueError("No valid charge-capacity data found in file.")

    return (
        np.asarray(cycle_axis, dtype=np.int32),
        np.asarray(qcmax_values, dtype=np.float64),
    )


def compute_qdmax_by_cycle(obj: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    cycle_data = get_cycle_data(obj)
    cycle_axis: list[int] = []
    qdmax_values: list[float] = []
    for cycle_idx, cycle in enumerate(cycle_data):
        series = extract_discharge_series(cycle, cycle_idx)
        if series is None or series.qd_ah.size == 0:
            continue
        cycle_axis.append(series.cycle_number if series.cycle_number is not None else cycle_idx)
        qdmax_values.append(float(np.max(series.qd_ah)))

    if not cycle_axis:
        raise ValueError("No valid discharge-capacity data found in file.")

    return (
        np.asarray(cycle_axis, dtype=np.int32),
        np.asarray(qdmax_values, dtype=np.float64),
    )
