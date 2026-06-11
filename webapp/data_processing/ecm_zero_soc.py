"""0% SOC RC parameters by trend extrapolation of the fitted curve.

The HPPC pulse sections only reach the lowest pulsed SOC (e.g. 5% or 10%). To
get R0/R1/C1(/R2/C2) at ~0% SOC we **extrapolate each parameter's own
100%->5% trend down to SOC=0**, rather than fitting the final discharge-to-empty
relaxation (whose dynamics differ from the pulses and produced off-trend values).

Each resistance/time-constant is extrapolated independently in **log space**
(keeps the quantity positive and captures the multiplicative upturn near 0%);
capacitances are then derived as ``C = tau / R`` so the appended row stays
internally consistent with the rest of the table.

Four selectable techniques are provided (the UI lets the user pick one):
  - ``log_poly2``      weighted least-squares deg-2 polynomial over the whole curve
  - ``weighted_local`` weighted low-order fit over the lowest-SOC points only
  - ``pchip``          shape-preserving PCHIP with a linear tail past the last point
  - ``gpr``            Gaussian-process regression with an RBF kernel (NumPy-only)

All techniques weight the low-SOC end more heavily, so the 0% value follows the
near-0% trend rather than the flat mid-range. Everything is count-independent:
it only needs the fitted SOC points, so it works for any number of pulses.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator

# Public list of techniques: value -> human label (mirrored in the UI dropdown).
EXTRAPOLATION_METHODS: dict[str, str] = {
    "log_poly2": "Log-space quadratic (whole curve)",
    "weighted_local": "Weighted local fit (low-SOC end)",
    "pchip": "Monotone spline + linear tail (PCHIP)",
    "gpr": "Gaussian process (RBF)",
}
DEFAULT_METHOD = "log_poly2"

# Low-SOC emphasis: weight ~ exp(-soc / _WEIGHT_SCALE). Smaller scale -> stronger
# emphasis on the points nearest 0% SOC.
_WEIGHT_SCALE = 0.35
# Local techniques use at most this many of the lowest-SOC points.
_LOCAL_POINTS = 4
# RBF length scale (in SOC units) for the Gaussian-process option.
_GP_LENGTH_SCALE = 0.25


def available_extrapolation_methods() -> list[dict[str, str]]:
    """Selectable extrapolation techniques as ``[{value, label}, ...]``."""
    return [{"value": k, "label": v} for k, v in EXTRAPOLATION_METHODS.items()]


def _weights(soc: np.ndarray) -> np.ndarray:
    """Per-point weights that emphasise the low-SOC end of the curve."""
    return np.exp(-np.asarray(soc, dtype=float) / _WEIGHT_SCALE)


def _poly_at_zero(soc: np.ndarray, y: np.ndarray, w: np.ndarray, degree: int) -> float:
    """Weighted least-squares polynomial fit, evaluated at SOC=0."""
    degree = max(1, min(degree, len(soc) - 1))
    coeffs = np.polyfit(soc, y, degree, w=w)
    return float(np.polyval(coeffs, 0.0))


def _local_at_zero(soc: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Weighted low-order fit over only the lowest-SOC points (soc is ascending)."""
    n = min(_LOCAL_POINTS, len(soc))
    s, yy, ww = soc[:n], y[:n], w[:n]
    return _poly_at_zero(s, yy, ww, degree=2 if n >= 3 else 1)


def _pchip_at_zero(soc: np.ndarray, y: np.ndarray) -> float:
    """PCHIP through all points, linear tail past the lowest point to SOC=0."""
    spline = PchipInterpolator(soc, y, extrapolate=True)
    slope = float(spline.derivative()(soc[0]))  # shape-preserving boundary slope
    return float(y[0] + slope * (0.0 - soc[0]))


def _gpr_at_zero(soc: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    """Gaussian-process (RBF) posterior mean at SOC=0, NumPy-only.

    A constant prior mean (the data mean) is used so the prediction reverts to
    the curve's average far from data rather than to zero. Per-point noise is
    smaller for low-SOC points, which trusts the near-0% trend more.
    """
    mean = float(np.mean(y))
    yc = y - mean
    sig_f2 = max(float(np.var(yc)), 1e-9)

    diff = soc[:, None] - soc[None, :]
    k = sig_f2 * np.exp(-0.5 * (diff / _GP_LENGTH_SCALE) ** 2)
    # Heteroscedastic noise: low-SOC points (high weight) get less noise.
    w_norm = w / w.max()
    noise = 1e-3 * sig_f2 + 1e-2 * sig_f2 * (1.0 - w_norm)
    k[np.diag_indices_from(k)] += noise

    try:
        alpha = np.linalg.solve(k, yc)
    except np.linalg.LinAlgError:
        alpha = np.linalg.lstsq(k, yc, rcond=None)[0]
    k_star = sig_f2 * np.exp(-0.5 * (soc / _GP_LENGTH_SCALE) ** 2)
    return float(mean + k_star @ alpha)


def _extrapolate_series(soc: np.ndarray, values: np.ndarray, method: str, w: np.ndarray) -> float:
    """Extrapolate one positive parameter series to SOC=0 with the chosen method.

    Works in log space when all values are positive (so the result stays
    positive and captures the multiplicative trend); falls back to linear space
    otherwise.
    """
    soc = np.asarray(soc, dtype=float)
    values = np.asarray(values, dtype=float)
    positive = bool(np.all(values > 0))
    y = np.log(values) if positive else values

    if method == "weighted_local":
        out = _local_at_zero(soc, y, w)
    elif method == "pchip":
        out = _pchip_at_zero(soc, y)
    elif method == "gpr":
        out = _gpr_at_zero(soc, y, w)
    else:  # log_poly2 (default)
        out = _poly_at_zero(soc, y, w, degree=2)

    return float(np.exp(out)) if positive else float(out)


def zero_soc_row(
    soc_points: np.ndarray,
    rctau: np.ndarray,
    rc_order: int,
    method: str = DEFAULT_METHOD,
) -> dict[str, Any]:
    """Build a 0% SOC parameter row by extrapolating the fitted curves.

    ``rctau`` columns follow ``rctau_from_coefficients``:
    1RC -> [tau1, r0, r1, c1]; 2RC -> [tau1, tau2, r0, r1, r2, c1, c2].
    Returns ``{"row": [...] | None, "soc": 0.0, "warning": str | None}`` where
    ``row`` is in that same column order. Resistances and time constants are
    extrapolated; capacitances are derived as ``C = tau / R`` for consistency.
    """
    soc = np.asarray(soc_points, dtype=float)
    rctau = np.asarray(rctau, dtype=float)
    if len(soc) < 2:
        return {"row": None, "soc": 0.0, "warning": "0% SOC: too few points to extrapolate."}
    if method not in EXTRAPOLATION_METHODS:
        return {"row": None, "soc": 0.0, "warning": f"0% SOC: unknown method '{method}'."}

    order = np.argsort(soc)  # ascending SOC, so [:n] are the lowest points
    soc = soc[order]
    rctau = rctau[order]
    w = _weights(soc)

    def col(idx: int) -> float:
        return _extrapolate_series(soc, rctau[:, idx], method, w)

    try:
        if rc_order == 1:
            tau1, r0, r1 = col(0), col(1), col(2)
            row = [tau1, r0, r1, tau1 / r1]
        else:
            tau1, tau2, r0 = col(0), col(1), col(2)
            r1, r2 = col(3), col(4)
            row = [tau1, tau2, r0, r1, r2, tau1 / r1, tau2 / r2]
    except Exception as exc:  # noqa: BLE001 - surface as a non-blocking warning
        return {"row": None, "soc": 0.0, "warning": f"0% SOC: extrapolation failed ({exc})."}

    return {"row": row, "soc": 0.0, "warning": None}
