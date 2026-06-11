"""Feature extraction for RUL prediction — vendored from the standalone
`battery_estimation` project (gen_features_bml.py + dataset_clf_bml.py).

Two input paths, both producing the same in-memory ``cell`` dict the model
consumes:

* ``.pkl`` — a raw BatteryML cell. We extract per-cycle features on the fly
  (the same maths as ``gen_features_bml.extract_pkl``), but WITHOUT the
  "reject cells that never reached 80 %" behaviour, because this tool must also
  handle still-healthy cells (partial-history prediction).
* ``.npz`` — an already-extracted MIT-style feature file
  (``dataset_clf_bml._load_npz_cell``).

The model input is a 16-cycle sequence: the first ``N_EARLY`` cycles plus an
``N_RANDOM``-cycle window. ``make_sample`` builds the scaled tensors.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np
import torch

# ── Fixed model/data constants (must match the training project) ──────────── #
V_BINS = 1000
N_EARLY = 8
N_RANDOM = 8
N_INPUT = N_EARLY + N_RANDOM      # 16 cycles fed to the network
N_CLASSES = 5
REF_CYCLE = 9                     # dQ curves are referenced to this cycle
EOL_FRACTION = 0.80              # 80 % capacity retention defines end-of-life
MIN_CUR = 1e-1                    # |I| threshold separating charge / discharge

# 11 scalar summary features + 1 positional term = 12 (matches BML checkpoints).
_SUMMARY_KEYS = (
    "Qd", "c_t", "dc_t",
    "dqdv_slope_max", "dqdv_slope_min", "dqdv_min", "dqdv_avg",
    "log_std_Qd", "log_std_Qc", "log_std_Id", "log_std_Ic",
)
_N_SUMMARY = len(_SUMMARY_KEYS) + 1


def rul_to_class(rul: float) -> int:
    if rul > 400:
        return 0
    if rul > 300:
        return 1
    if rul > 200:
        return 2
    if rul > 100:
        return 3
    return 4


# ─────────────────────────────────────────────────────────────────────────── #
# Small numeric helpers (vendored)
# ─────────────────────────────────────────────────────────────────────────── #
def _interp_nan(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32).reshape(-1).copy()
    if arr.size == 0:
        return arr
    nans = ~np.isfinite(arr)
    if not nans.any():
        return arr
    idx = np.arange(arr.size)
    valid = ~nans
    if not valid.any():
        arr[:] = 0.0
        return arr
    arr[nans] = np.interp(idx[nans], idx[valid], arr[valid])
    return arr


def _safe_array(value: Any) -> np.ndarray:
    if value is None:
        return np.zeros(0, dtype=np.float32)
    try:
        return _interp_nan(np.asarray(value, dtype=np.float32).reshape(-1))
    except Exception:
        return np.zeros(0, dtype=np.float32)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if np.isfinite(out) else default


def _log_std(arr: np.ndarray) -> float:
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return 0.0
    return float(20.0 * np.log10(max(float(np.std(arr)), 1e-9)))


def _interp(curve: np.ndarray, n: int = V_BINS) -> np.ndarray:
    curve = np.asarray(curve, dtype=np.float32).reshape(-1)
    if curve.size == n:
        return curve
    if curve.size == 0:
        return np.zeros(n, dtype=np.float32)
    x_old = np.linspace(0, 1, curve.size)
    x_new = np.linspace(0, 1, n)
    return np.interp(x_new, x_old, curve).astype(np.float32)


def _dedupe_interp(x: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if x.size < 2:
        return np.zeros(grid.size, dtype=np.float32)
    order = np.argsort(x)
    x, y = x[order], y[order]
    unique_x, inverse = np.unique(x, return_inverse=True)
    if unique_x.size < 2:
        return np.zeros(grid.size, dtype=np.float32)
    y_sum = np.zeros(unique_x.size, dtype=np.float64)
    counts = np.zeros(unique_x.size, dtype=np.float64)
    np.add.at(y_sum, inverse, y)
    np.add.at(counts, inverse, 1.0)
    unique_y = y_sum / np.maximum(counts, 1.0)
    return np.interp(grid, unique_x, unique_y).astype(np.float32)


def _find_eol_idx(qd: np.ndarray, eol_fraction: float = EOL_FRACTION) -> tuple[int, float, float]:
    """Return ``(eol_idx, q_init, retained_at_eol)``.

    Primary rule: first cycle past the reference window where Qd drops below
    ``eol_fraction * Q_init``. Fallback: the most-aged (min-Qd) cycle.
    """
    qd = np.asarray(qd, dtype=np.float32)
    valid = np.isfinite(qd) & (qd > 0)
    if valid.sum() < 2:
        return len(qd), 0.0, 1.0

    valid_idx = np.where(valid)[0]
    n_ref = min(10, len(valid_idx))
    q_init = float(np.max(qd[valid_idx[:n_ref]]))
    if q_init <= 0:
        return len(qd), 0.0, 1.0

    q_eol = eol_fraction * q_init
    ref_end = valid_idx[n_ref - 1]

    for i in valid_idx:
        if i <= ref_end:
            continue
        if qd[i] < q_eol:
            return int(i), q_init, float(qd[i] / q_init)

    post_ref = valid_idx[valid_idx > ref_end]
    if post_ref.size == 0:
        return len(qd), q_init, 1.0
    j = int(post_ref[np.argmin(qd[post_ref])])
    return j, q_init, float(qd[j] / q_init)


# ─────────────────────────────────────────────────────────────────────────── #
# .pkl extraction (full history — no EOL truncation, no rejection)
# ─────────────────────────────────────────────────────────────────────────── #
def _discharge_arrays(cycle: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    qd = _safe_array(cycle.get("discharge_capacity_in_Ah"))
    voltage = _safe_array(cycle.get("voltage_in_V"))
    current = _safe_array(cycle.get("current_in_A"))
    time_s = _safe_array(cycle.get("time_in_s"))
    n = min(voltage.size, qd.size, current.size, time_s.size)
    voltage, qd, current, time_s = voltage[:n], qd[:n], current[:n], time_s[:n]
    valid = np.isfinite(voltage) & np.isfinite(qd) & np.isfinite(current) & np.isfinite(time_s)
    discharge = valid & (current < -MIN_CUR)
    idx = np.where(discharge)
    voltage, qd, current, time_s = voltage[idx], qd[idx], current[idx], time_s[idx]
    if time_s.size == 0:
        z = np.zeros(1, dtype=np.float32)
        return z, z, z, z
    time_s = time_s - time_s[0]
    return voltage, qd, current, time_s


def _charge_arrays(cycle: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    voltage = _safe_array(cycle.get("voltage_in_V"))
    qc = _safe_array(cycle.get("charge_capacity_in_Ah"))
    current = _safe_array(cycle.get("current_in_A"))
    time_s = _safe_array(cycle.get("time_in_s"))
    n = min(voltage.size, qc.size, current.size, time_s.size)
    voltage, qc, current, time_s = voltage[:n], qc[:n], current[:n], time_s[:n]
    valid = np.isfinite(voltage) & np.isfinite(qc) & np.isfinite(current) & np.isfinite(time_s)
    charge = valid & (current > MIN_CUR)
    idx = np.where(charge)
    voltage, qc, current, time_s = voltage[idx], qc[idx], current[idx], time_s[idx]
    if time_s.size == 0:
        z = np.zeros(1, dtype=np.float32)
        return z, z, z, z
    time_s = time_s - time_s[0]
    return voltage, qc, current, time_s


def _collect_voltage_limits(cell: dict, cycles: list) -> tuple[float, float]:
    vmin = _safe_float(cell.get("min_voltage_limit_in_V"), np.nan)
    vmax = _safe_float(cell.get("max_voltage_limit_in_V"), np.nan)
    if np.isfinite(vmin) and np.isfinite(vmax) and vmax > vmin:
        return vmin, vmax
    samples = []
    for cyc in cycles[: min(len(cycles), 50)]:
        v = _safe_array(cyc.get("voltage_in_V"))
        current = _safe_array(cyc.get("current_in_A"))
        n = min(v.size, current.size)
        if n >= 2:
            discharge = np.isfinite(v[:n]) & np.isfinite(current[:n]) & (current[:n] < -MIN_CUR)
            if discharge.sum() >= 2:
                samples.append(v[:n][discharge])
                continue
        if v.size:
            samples.append(v[np.isfinite(v)])
    if not samples:
        return 0.0, 1.0
    all_v = np.concatenate(samples)
    if all_v.size == 0:
        return 0.0, 1.0
    lo = float(np.nanpercentile(all_v, 1))
    hi = float(np.nanpercentile(all_v, 99))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return 0.0, 1.0
    return lo, hi


def _cycle_qdlin(cycle: dict, voltage_grid: np.ndarray) -> np.ndarray:
    voltage, qd, _, _ = _discharge_arrays(cycle)
    if voltage.size < 2:
        return np.zeros(V_BINS, dtype=np.float32)
    usable = np.isfinite(voltage) & np.isfinite(qd)
    if usable.sum() < 2:
        return np.zeros(V_BINS, dtype=np.float32)
    if float(np.nanmax(qd[usable])) <= 0:
        return np.zeros(V_BINS, dtype=np.float32)
    return _dedupe_interp(voltage[usable], qd[usable], voltage_grid)


def _cycle_number(cycle: dict, idx: int, already_spent: int) -> int:
    raw = cycle.get("cycle_number", idx + 1)
    try:
        cycle_num = int(raw)
    except Exception:
        cycle_num = idx + 1
    if already_spent > 0 and cycle_num <= idx + 2:
        return already_spent + cycle_num
    return cycle_num


def _extract_pkl_arrays(path: Path) -> dict[str, Any]:
    """Extract per-cycle feature arrays from a raw BatteryML .pkl (all cycles)."""
    if path.stat().st_size == 0:
        raise ValueError(f"Empty file: {path}")
    with path.open("rb") as f:
        cell = pickle.load(f)
    if not isinstance(cell, dict):
        raise ValueError("Pickle is not a BatteryML cell dict")
    cycles = cell.get("cycle_data")
    if not isinstance(cycles, list) or not cycles:
        raise ValueError("No cycle_data in pickle")

    vmin, vmax = _collect_voltage_limits(cell, cycles)
    voltage_grid = np.linspace(vmin, vmax, V_BINS, dtype=np.float32)
    already_spent = int(cell.get("already_spent_cycles") or 0)

    cols: dict[str, list] = {k: [] for k in (
        "Qd", "Qc", "c_t", "dc_t", "dqdv_slope_max", "dqdv_slope_min",
        "dqdv_min", "dqdv_avg", "log_std_Qd", "log_std_Qc",
        "log_std_Id", "log_std_Ic",
    )}
    qdlin_list: list[np.ndarray] = []
    cycle_index: list[int] = []

    for idx, cycle in enumerate(cycles):
        if not isinstance(cycle, dict):
            continue
        _, dq_raw, Id, _ = _discharge_arrays(cycle)
        _, cq_raw, Ic, charge_time = _charge_arrays(cycle)
        _, _, _, discharge_time = _discharge_arrays(cycle)

        qd_curve = _cycle_qdlin(cycle, voltage_grid)
        dqdv_curve = _interp_nan(np.gradient(qd_curve, voltage_grid).astype(np.float32))

        cols["Qd"].append(float(np.nanmax(dq_raw)) if dq_raw.size else 0.0)
        cols["Qc"].append(float(np.nanmax(cq_raw)) if cq_raw.size else 0.0)
        cols["c_t"].append(float(charge_time[-1]))

        dqdv_mid = dqdv_curve[100:900]
        dqdv_mid = np.convolve(dqdv_mid, np.ones(10) / 10, mode="valid")
        dqdv_slope = np.diff(dqdv_mid) if dqdv_mid.size > 1 else np.zeros(1, dtype=np.float32)
        cols["dqdv_slope_max"].append(float(np.max(dqdv_slope)))
        cols["dqdv_slope_min"].append(float(np.min(dqdv_slope)))
        cols["dqdv_min"].append(float(np.nanmin(dqdv_curve)) if dqdv_curve.size else 0.0)
        cols["dqdv_avg"].append(float(np.nanmean(dqdv_curve)) if dqdv_curve.size else 0.0)
        cols["log_std_Qd"].append(_log_std(dq_raw))
        cols["log_std_Qc"].append(_log_std(cq_raw))
        cols["log_std_Id"].append(_log_std(Id))
        cols["log_std_Ic"].append(_log_std(Ic))
        cols["dc_t"].append(float(discharge_time[-1]))
        qdlin_list.append(_interp(qd_curve))
        cycle_index.append(_cycle_number(cycle, idx, already_spent))

    if not qdlin_list:
        raise ValueError("No usable cycles in pickle")

    arrays = {k: _interp_nan(np.asarray(v, dtype=np.float32)) for k, v in cols.items()}
    arrays["cycle_index"] = np.asarray(cycle_index, dtype=np.int32)
    arrays["qdlin"] = np.stack(qdlin_list, axis=0).astype(np.float32)
    arrays["cell_id"] = str(cell.get("cell_id") or path.stem)
    return arrays


# ─────────────────────────────────────────────────────────────────────────── #
# .npz loading
# ─────────────────────────────────────────────────────────────────────────── #
def _arr(data, key: str, dtype=np.float32) -> np.ndarray:
    if key not in data.files:
        return np.zeros(0, dtype=dtype)
    return np.asarray(data[key], dtype=dtype).reshape(-1)


def _load_npz_arrays(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=True) as d:
        qd = _arr(d, "qd")
        qdlin_raw = np.asarray(d["qdlin"], dtype=np.float32)
        arrays = {
            "Qd": qd,
            "Qc": _arr(d, "qc"),
            "c_t": _arr(d, "c_t"),
            "dc_t": _arr(d, "dc_t"),
            "dqdv_slope_max": _arr(d, "dqdv_slope_max"),
            "dqdv_slope_min": _arr(d, "dqdv_slope_min"),
            "dqdv_min": _arr(d, "dqdv_min"),
            "dqdv_avg": _arr(d, "dqdv_avg"),
            "log_std_Qd": _arr(d, "log_std_Qd"),
            "log_std_Qc": _arr(d, "log_std_Qc"),
            "log_std_Id": _arr(d, "log_std_Id"),
            "log_std_Ic": _arr(d, "log_std_Ic"),
            "cycle_index": _arr(d, "cycle_index", dtype=np.int32),
            "qdlin": qdlin_raw,
            "cell_id": str(d["cell_id"]) if "cell_id" in d.files else path.stem,
        }
    return arrays


# ─────────────────────────────────────────────────────────────────────────── #
# Unified cell builder
# ─────────────────────────────────────────────────────────────────────────── #
def load_cell(path: Path) -> dict[str, Any]:
    """Load a .pkl or .npz file into the prediction ``cell`` dict.

    The returned dict keeps ALL cycles (no EOL truncation) so partial-history
    prediction works; ``reached_eol`` / ``cycle_life`` describe where 80 % EOL
    falls when it is known.
    """
    suffix = path.suffix.lower()
    if suffix == ".pkl":
        arrays = _extract_pkl_arrays(path)
    elif suffix == ".npz":
        arrays = _load_npz_arrays(path)
    else:
        raise ValueError("Input must be a .pkl or .npz file")

    qdlin = arrays["qdlin"]
    n_cyc = int(qdlin.shape[0])
    cycle_index = np.asarray(arrays["cycle_index"], dtype=np.int32).reshape(-1)
    if cycle_index.size < n_cyc:
        cycle_index = np.arange(1, n_cyc + 1, dtype=np.int32)

    qd = _interp_nan(np.asarray(arrays["Qd"], dtype=np.float32))[:n_cyc]
    eol_idx, q_init, retained = _find_eol_idx(qd)
    reached_eol = bool(eol_idx < len(qd) and q_init > 0 and retained < EOL_FRACTION)

    if eol_idx < len(qd) and cycle_index.size > eol_idx:
        cycle_life = int(cycle_index[eol_idx])
    elif cycle_index.size:
        cycle_life = int(cycle_index[-1])
    else:
        cycle_life = n_cyc

    summary = {k: np.asarray(arrays[k], dtype=np.float32)[:n_cyc] for k in _SUMMARY_KEYS if k in arrays}

    return {
        "cell_id": arrays.get("cell_id", path.stem),
        "n_cyc": n_cyc,
        "cycle_index": cycle_index[:n_cyc],
        "summary": summary,
        "qdlin": [qdlin[c] for c in range(n_cyc)],
        "qd": qd,
        "cycle_life": cycle_life,
        "reached_eol": reached_eol,
        "q_init": q_init,
        "q_retained_at_eol": retained,
        "max_cycle": int(cycle_index[n_cyc - 1]) if cycle_index.size else n_cyc,
    }


# ─────────────────────────────────────────────────────────────────────────── #
# Feature tensors
# ─────────────────────────────────────────────────────────────────────────── #
def _summary_row(summary: dict, c: int) -> np.ndarray:
    def safe(arr: np.ndarray) -> float:
        return float(arr[c]) if c < len(arr) and np.isfinite(arr[c]) else 0.0

    scalar = np.asarray(
        [safe(np.asarray(summary.get(k, []), dtype=np.float32)) for k in _SUMMARY_KEYS],
        dtype=np.float32,
    )
    return np.concatenate([scalar, [c / 5000.0]]).astype(np.float32)


def build_cell_arrays(cell: dict) -> tuple[np.ndarray, np.ndarray]:
    """Return unscaled ``(dq_all (n,1,V_BINS), summary_all (n, _N_SUMMARY))``."""
    qdlin = cell["qdlin"]
    summary = cell["summary"]
    n_cyc = len(qdlin)
    ref_idx = min(REF_CYCLE, n_cyc - 1)
    ref = np.asarray(qdlin[ref_idx], dtype=np.float32).reshape(-1)

    dq = np.zeros((n_cyc, V_BINS), dtype=np.float32)
    summ = np.zeros((n_cyc, _N_SUMMARY), dtype=np.float32)
    for c in range(n_cyc):
        q = np.asarray(qdlin[c], dtype=np.float32).reshape(-1)
        length = min(len(q), len(ref), V_BINS)
        dq[c, :length] = q[:length] - ref[:length]
        summ[c] = _summary_row(summary, c)
    return np.expand_dims(dq, axis=1), summ


def window_indices(start: int) -> list[int]:
    """Array indices for a sample whose recent window begins at ``start``:
    the first ``N_EARLY`` cycles plus ``N_RANDOM`` cycles from ``start``."""
    return list(range(N_EARLY)) + list(range(start, start + N_RANDOM))


def make_sample(
    dq_all: np.ndarray,
    summary_all: np.ndarray,
    start: int,
    scalers,
    device: str = "cpu",
) -> tuple[torch.Tensor, torch.Tensor]:
    """Build batched, scaled (dq, summary) tensors for one sliding-window sample."""
    idx = window_indices(start)
    dq_seq = dq_all[idx]               # (T, 1, V_BINS)
    sum_seq = summary_all[idx]         # (T, _N_SUMMARY)

    dq_scaler, summary_scaler = scalers
    T, C, F = dq_seq.shape
    dq_seq = dq_scaler.transform(dq_seq.reshape(T, C * F)).reshape(T, C, F)
    sum_seq = summary_scaler.transform(sum_seq)

    dq_t = torch.tensor(dq_seq, dtype=torch.float32).unsqueeze(0).to(device)
    sum_t = torch.tensor(sum_seq, dtype=torch.float32).unsqueeze(0).to(device)
    return dq_t, sum_t
