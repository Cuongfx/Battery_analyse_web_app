"""Summarize a BatteryML-style pickle (dict with cycle_data) for API responses."""

from __future__ import annotations

from typing import Any


def _len_or_none(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return len(v)
    try:
        import numpy as np

        if isinstance(v, np.ndarray):
            return int(v.size)
    except ImportError:
        pass
    return None


def _type_label(v: Any) -> str:
    if v is None:
        return "null"
    t = type(v).__name__
    if t == "ndarray":
        return "ndarray"
    return t


def _sample_cycle_keys(cycle: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(cycle.keys()):
        val = cycle[key]
        n = _len_or_none(val)
        out[key] = {
            "type": _type_label(val),
            "length": n,
        }
    return out


def summarize_batteryml_pkl(obj: dict[str, Any]) -> dict[str, Any]:
    top_keys = sorted(obj.keys())
    cycle_data = obj.get("cycle_data")
    cycle_count = len(cycle_data) if isinstance(cycle_data, list) else 0

    first_summary: dict[str, Any] | None = None
    last_summary: dict[str, Any] | None = None
    if isinstance(cycle_data, list) and cycle_data:
        c0 = cycle_data[0]
        if isinstance(c0, dict):
            first_summary = _sample_cycle_keys(c0)
        if len(cycle_data) > 1:
            c1 = cycle_data[-1]
            if isinstance(c1, dict):
                last_summary = _sample_cycle_keys(c1)

    other_top: dict[str, Any] = {}
    for k in top_keys:
        if k == "cycle_data":
            continue
        v = obj[k]
        if isinstance(v, (str, int, float, bool)) or v is None:
            other_top[k] = {"type": _type_label(v), "preview": str(v)[:200]}
        elif isinstance(v, (list, tuple)) and len(v) <= 20:
            other_top[k] = {"type": "list", "len": len(v), "preview": str(v)[:200]}
        else:
            n = _len_or_none(v)
            other_top[k] = {"type": _type_label(v), "length": n}

    return {
        "root_type": "dict",
        "top_level_keys": top_keys,
        "cycle_count": cycle_count,
        "other_top_level": other_top,
        "first_cycle_field_summary": first_summary,
        "last_cycle_field_summary": last_summary if cycle_count > 1 else None,
    }


_VOLTAGE_KEYS = ("voltage_in_V", "voltage", "V")
_CURRENT_KEYS = ("current_in_A", "current", "I")
_TIME_KEYS = ("time_in_s", "time", "t")
_QD_KEYS = ("discharge_capacity_in_Ah", "Qd", "discharge_capacity")
_QC_KEYS = ("charge_capacity_in_Ah", "Qc", "charge_capacity")


def _array_range(cycle_data: list, keys: tuple[str, ...]) -> tuple[float | None, float | None]:
    try:
        import numpy as np
    except ImportError:
        return (None, None)
    lo: float | None = None
    hi: float | None = None
    for c in cycle_data:
        if not isinstance(c, dict):
            continue
        for k in keys:
            v = c.get(k)
            if v is None:
                continue
            try:
                arr = np.asarray(v, dtype=float)
                if arr.size == 0:
                    break
                a = float(np.nanmin(arr))
                b = float(np.nanmax(arr))
                if np.isnan(a) or np.isnan(b):
                    break
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
            except Exception:
                pass
            break
    return (lo, hi)


def compute_data_meta(obj: dict[str, Any]) -> dict[str, Any]:
    cd = obj.get("cycle_data")
    if not isinstance(cd, list) or not cd:
        return {"cycle_count": 0}
    v_lo, v_hi = _array_range(cd, _VOLTAGE_KEYS)
    i_lo, i_hi = _array_range(cd, _CURRENT_KEYS)
    t_lo, t_hi = _array_range(cd, _TIME_KEYS)
    qd_lo, qd_hi = _array_range(cd, _QD_KEYS)
    qc_lo, qc_hi = _array_range(cd, _QC_KEYS)
    return {
        "cycle_count": len(cd),
        "max_cycle_index": len(cd) - 1,
        "voltage": {"min": v_lo, "max": v_hi},
        "current": {"min": i_lo, "max": i_hi},
        "time": {"min": t_lo, "max": t_hi},
        "qd": {"min": qd_lo, "max": qd_hi},
        "qc": {"min": qc_lo, "max": qc_hi},
    }
