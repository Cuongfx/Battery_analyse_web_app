"""Open-circuit-voltage (OCV) estimation from an HPPC test.

The relaxed (rested) voltage just before each discharge pulse approximates the
OCV at that SOC. We collect one anchor per SOC section plus the rested voltage
after the final discharge-to-empty (for the ~0% SOC end), build a regular-grid
table, and optionally fit an analytical OCV(SOC) polynomial.

SOC is coulomb-counted from 100% at the start of the HPPC region using the same
capacity as the fit, so the OCV-SOC curve shifts consistently with capacity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# SOC slightly outside [0, 1] (rounding in the capacity estimate) is clamped;
# anything further out is dropped.
_SOC_CLAMP_TOL = 0.05


def _region_soc(
    time_s: np.ndarray,
    current: np.ndarray,
    start_row: int,
    end_row: int,
    capacity: float,
    eta_chg: float,
    eta_dis: float,
) -> np.ndarray:
    """Coulomb-counted SOC over ``[start_row, end_row)`` starting at 1.0."""
    t = np.asarray(time_s[start_row:end_row], dtype=float)
    i = np.asarray(current[start_row:end_row], dtype=float)
    soc = np.ones(len(t))
    q_as = capacity * 3600.0
    for k in range(1, len(t)):
        eta = eta_chg if i[k] > 0 else eta_dis
        soc[k] = soc[k - 1] + eta * i[k] * (t[k] - t[k - 1]) / q_as
    return soc


def estimate_ocv(
    time_s: np.ndarray,
    current: np.ndarray,
    voltage: np.ndarray,
    blocks: list[tuple[int, int]],
    kinds: list[str],
    first_pulse: int | None,
    final_charge: int | None,
    capacity: float | None,
    eta_chg: float = 0.98,
    eta_dis: float = 1.0,
) -> dict[str, Any]:
    """Estimate OCV anchor points ``[(soc, ocv), ...]`` from the HPPC region.

    Uses the rested voltage before each discharge pulse, plus the rested voltage
    after the final discharge (the ~0% SOC anchor) when present.
    """
    warnings: list[str] = []
    if first_pulse is None or not capacity or capacity <= 0:
        return {"anchors": [], "warnings": ["OCV: HPPC region or capacity unavailable."]}

    region_end_block = final_charge if final_charge is not None else len(blocks)
    # Include the rest before the first pulse so SOC starts at a rested 100%.
    if first_pulse > 0 and kinds[first_pulse - 1] == "rest":
        region_start = blocks[first_pulse - 1][0]
    else:
        region_start = blocks[first_pulse][0]
    region_end_row = (
        blocks[final_charge][0] if final_charge is not None else blocks[-1][1] + 1
    )

    soc = _region_soc(
        time_s, current, region_start, region_end_row, capacity, eta_chg, eta_dis
    )

    def soc_at(idx: int) -> float | None:
        j = idx - region_start
        return float(soc[j]) if 0 <= j < len(soc) else None

    raw: list[tuple[float, float]] = []

    # One rested anchor before each section's discharge pulse.
    for bi in range(first_pulse, region_end_block):
        if kinds[bi] != "discharge_pulse":
            continue
        anchor = blocks[bi - 1][1] if (bi > 0 and kinds[bi - 1] == "rest") else blocks[bi][0]
        s = soc_at(anchor)
        if s is not None:
            raw.append((s, float(voltage[anchor])))

    # Final ~0% SOC anchor: the rested sample just before the closing full charge.
    if final_charge is not None and final_charge > 0 and kinds[final_charge - 1] == "rest":
        idx0 = blocks[final_charge - 1][1]
        s0 = soc_at(idx0)
        if s0 is not None:
            raw.append((s0, float(voltage[idx0])))
    else:
        warnings.append(
            "OCV: final discharge-to-0% rest not found; low-SOC coverage may be limited."
        )

    # Clamp small overshoots into [0, 1]; drop anything further out.
    cleaned: dict[float, float] = {}
    for s, v in raw:
        if s < -_SOC_CLAMP_TOL or s > 1.0 + _SOC_CLAMP_TOL:
            continue
        s = min(max(s, 0.0), 1.0)
        cleaned[round(s, 4)] = round(v, 4)  # dedupe by SOC

    anchors = sorted(cleaned.items())  # ascending SOC
    if len(anchors) < 2:
        warnings.append("OCV: not enough valid anchor points.")
    return {"anchors": [list(a) for a in anchors], "warnings": warnings}


def _anchor_arrays(anchors: list) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(anchors, dtype=float)
    return arr[:, 0], arr[:, 1]


def ocv_table(anchors: list, step: float = 0.01) -> dict[str, list[float]]:
    """Linear-interpolate OCV onto a regular SOC grid within the measured range."""
    if len(anchors) < 2:
        return {"soc": [], "ocv": []}
    soc, ocv = _anchor_arrays(anchors)
    lo = float(np.ceil(soc.min() / step) * step)
    hi = float(np.floor(soc.max() / step) * step)
    grid = np.round(np.arange(lo, hi + step / 2, step), 4)
    values = np.interp(grid, soc, ocv)
    return {"soc": grid.tolist(), "ocv": np.round(values, 5).tolist()}


def fit_ocv_polynomial(anchors: list, degree: int = 8) -> dict[str, Any] | None:
    """Fit OCV(SOC) as a polynomial; return coefficients (high->low) and RMSE."""
    if len(anchors) < 2:
        return None
    soc, ocv = _anchor_arrays(anchors)
    degree = max(1, min(degree, len(anchors) - 1))
    coeffs = np.polyfit(soc, ocv, degree)
    residual = ocv - np.polyval(coeffs, soc)
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    return {"degree": degree, "coeffs": np.round(coeffs, 8).tolist(), "rmse": round(rmse, 6)}


def ocv_endpoints(anchors: list) -> dict[str, float | None]:
    """OCV at SOC=1 and SOC=0 (nearest measured anchor; no extrapolation)."""
    if not anchors:
        return {"ocv_100": None, "ocv_0": None}
    soc, ocv = _anchor_arrays(anchors)
    return {
        "ocv_100": round(float(ocv[int(np.argmax(soc))]), 4),
        "ocv_0": round(float(ocv[int(np.argmin(soc))]), 4),
    }


def save_ocv_csv(
    out_dir: Path,
    stem: str,
    table: dict[str, list[float]],
    poly: dict[str, Any] | None,
) -> Path:
    """Write the regular-grid OCV table (+ polynomial curve if fitted)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}_ocv.csv"
    df = pd.DataFrame({"soc": table["soc"], "ocv_tabulated_v": table["ocv"]})
    if poly is not None and table["soc"]:
        df["ocv_poly_v"] = np.round(np.polyval(poly["coeffs"], df["soc"]), 5)
    df.to_csv(path, index=False)
    return path


def plot_ocv(
    anchors: list,
    table: dict[str, list[float]],
    poly: dict[str, Any] | None,
    save_path: Path,
    show: bool = False,
) -> None:
    """OCV vs SOC: measured anchors (markers) + tabulated line + polynomial."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5), tight_layout=True)
    if anchors:
        soc, ocv = _anchor_arrays(anchors)
        ax.plot(soc, ocv, "o", color="C3", label="HPPC rested anchors")
    if table["soc"]:
        ax.plot(table["soc"], table["ocv"], "-", color="C0", label="Tabulated (interp)")
    if poly is not None and table["soc"]:
        ax.plot(
            table["soc"], np.polyval(poly["coeffs"], table["soc"]),
            "--", color="k", label=f"Polynomial (deg {poly['degree']})",
        )
    ax.set_xlabel("SOC [-]")
    ax.set_ylabel("OCV [V]")
    ax.set_title("Open-Circuit Voltage vs SOC")
    ax.grid(True, color="0.9")
    ax.set_frame_on(False)
    ax.legend(loc="best")

    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=150)
    if show:
        plt.show()
    plt.close(fig)
