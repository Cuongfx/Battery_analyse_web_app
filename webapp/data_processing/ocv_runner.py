"""OCV-test pipeline — separate from the HPPC/ECM tab.

A slow GITT-style open-circuit-voltage test (Neware ``.xlsx``, same reader as
HPPC but a completely different sequence):

    full charge -> rest -> [ slow discharge step -> long rest ] x N
                 -> rest -> one continuous slow (~C/20) charge back to full
                 -> (optional) long final rest

From it we estimate, all on **one continuous SOC axis** coulomb-counted from the
initial 100% rested point:

* **Discharge OCV** — both the rested equilibrium voltage after each discharge
  step (GITT anchors) *and* the raw low-rate discharge curve.
* **Charge OCV** — the low-rate charge terminal voltage as a pseudo-OCV (plus the
  relaxed point from a closing rest when one is present).
* **Mean OCV** ``(charge + discharge)/2`` and **hysteresis** ``charge - discharge``
  between the two low-rate curves, on their overlapping SOC range.

Outputs (PNG/SVG/PDF + CSV) are written under the shared ECM ``OUTPUT_ROOT`` so
the existing ``/api/ecm/image`` jail serves them; filenames are ``*_ocvtest_*``
to stay distinct from HPPC outputs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.interpolate import PchipInterpolator  # noqa: E402

from webapp.config import PROJECT_ROOT  # noqa: E402
from webapp.data_processing import ecm_ocv  # noqa: E402

EQUIV_DIR = PROJECT_ROOT / "equiv-circ-model"
if str(EQUIV_DIR) not in sys.path:
    sys.path.insert(0, str(EQUIV_DIR))

import extract_hppc  # noqa: E402

OUTPUT_ROOT = EQUIV_DIR / "Equivalent-Circuit"
DEFAULT_SHEET = "Record List1"
PULSE_MAX_SECONDS = extract_hppc.DEFAULT_PULSE_MAX_SECONDS
ETA_CHG = 0.98
ETA_DIS = 1.0

# Keep the dense low-rate curves light for JSON/CSV/plotting.
_MAX_CURVE_POINTS = 400
# A "real" rest (for the relaxed charge anchor) must be at least this long.
_MIN_REST_SECONDS = 600.0


# --------------------------------------------------------------------------- #
# Loading / structure
# --------------------------------------------------------------------------- #
def output_dir_for(stem: str) -> Path:
    return OUTPUT_ROOT / stem


def _classify(xlsx_path, sheet, pulse_max_seconds=PULSE_MAX_SECONDS):
    data = extract_hppc.load_records(xlsx_path, sheet)
    blocks = extract_hppc.build_blocks(data["command"])
    kinds = [extract_hppc.classify_block(data, b, pulse_max_seconds) for b in blocks]
    return data, blocks, kinds


def _block_mean_current(data, block):
    s, e = block
    return float(np.mean(data["current"][s:e + 1]))


def _block_ah(data, block):
    s, e = block
    t = data["time_s"][s:e + 1]
    i = data["current"][s:e + 1]
    if len(t) < 2:
        return 0.0
    return float(np.trapz(i, t) / 3600.0)


def _structure(data, blocks, kinds):
    """Locate the OCV-test landmarks.

    Returns a dict with: ``discharge`` (block indices of the slow-discharge
    steps), ``charge`` (block indices of the trailing slow charge), ``ref_row``
    (the rested 100% SOC sample), ``final_rest`` (block index of a closing rest
    after the charge, or None).
    """
    discharge = [
        i for i, k in enumerate(kinds)
        if k == "constant_discharge" and _block_mean_current(data, blocks[i]) < 0
    ]
    if not discharge:
        raise ValueError("No slow-discharge steps found — is this an OCV test file?")

    first_dis, last_dis = discharge[0], discharge[-1]

    # Trailing slow charge: charge blocks after the last discharge step.
    charge = [
        i for i in range(last_dis + 1, len(blocks))
        if kinds[i] in ("full_charge", "charge_pulse")
        and _block_mean_current(data, blocks[i]) > 0
    ]

    # 100% reference: the rested sample just before the first discharge step.
    if first_dis > 0 and kinds[first_dis - 1] == "rest":
        ref_row = blocks[first_dis - 1][1]
    else:
        ref_row = blocks[first_dis][0]

    # A closing rest after the charge gives a relaxed charge-OCV anchor.
    final_rest = None
    if charge:
        after = charge[-1] + 1
        if after < len(blocks) and kinds[after] == "rest":
            s, e = blocks[after]
            if data["time_s"][e] - data["time_s"][s] >= _MIN_REST_SECONDS:
                final_rest = after

    return {
        "discharge": discharge,
        "charge": charge,
        "first_dis": first_dis,
        "last_dis": last_dis,
        "ref_row": ref_row,
        "final_rest": final_rest,
    }


def _continuous_soc(time_s, current, ref_row, capacity):
    """Coulomb-counted SOC for every row, anchored to 1.0 at ``ref_row``.

    Rows before ``ref_row`` (the initial full charge) are left as NaN — the OCV
    estimate only uses the discharge sweep and the trailing charge.
    """
    n = len(time_s)
    soc = np.full(n, np.nan)
    soc[ref_row] = 1.0
    q_as = capacity * 3600.0
    for k in range(ref_row + 1, n):
        eta = ETA_CHG if current[k] > 0 else ETA_DIS
        soc[k] = soc[k - 1] + eta * current[k] * (time_s[k] - time_s[k - 1]) / q_as
    return soc


# --------------------------------------------------------------------------- #
# Capacity detection (Step-1 preview)
# --------------------------------------------------------------------------- #
def detect_capacity(xlsx_path: Path, sheet: str = DEFAULT_SHEET) -> dict[str, Any]:
    """Detected discharge/charge throughput + voltage range for the OCV test."""
    data, blocks, kinds = _classify(xlsx_path, sheet)
    st = _structure(data, blocks, kinds)

    qd = sum(abs(_block_ah(data, blocks[i])) for i in st["discharge"])
    qc = sum(abs(_block_ah(data, blocks[i])) for i in st["charge"])

    start = blocks[st["first_dis"]][0]
    end = blocks[st["charge"][-1]][1] if st["charge"] else blocks[st["last_dis"]][1]
    v = data["voltage"][start:end + 1]
    return {
        "qd": round(qd, 4),
        "qc": round(qc, 4),
        "capacity": round(qd, 4) if qd > 0 else (round(qc, 4) if qc > 0 else None),
        "v_min": round(float(np.min(v)), 4) if len(v) else None,
        "v_max": round(float(np.max(v)), 4) if len(v) else None,
        "n_discharge_steps": len(st["discharge"]),
        "has_charge": bool(st["charge"]),
        "has_final_rest": st["final_rest"] is not None,
    }


# --------------------------------------------------------------------------- #
# Curve helpers
# --------------------------------------------------------------------------- #
def _downsample(soc, volt, n_max=_MAX_CURVE_POINTS):
    """Even row decimation; keeps endpoints. Returns SOC-ascending arrays."""
    soc = np.asarray(soc, dtype=float)
    volt = np.asarray(volt, dtype=float)
    keep = np.isfinite(soc) & np.isfinite(volt)
    soc, volt = soc[keep], volt[keep]
    if len(soc) > n_max:
        idx = np.linspace(0, len(soc) - 1, n_max).round().astype(int)
        soc, volt = soc[idx], volt[idx]
    order = np.argsort(soc)
    return soc[order], volt[order]


def _rows_for_blocks(blocks, idxs):
    """Concatenated row indices for a list of block indices."""
    rows = []
    for i in idxs:
        s, e = blocks[i]
        rows.extend(range(s, e + 1))
    return np.asarray(rows, dtype=int)


# --------------------------------------------------------------------------- #
# Main computation
# --------------------------------------------------------------------------- #
def compute_ocv(
    xlsx_path: Path,
    sheet: str = DEFAULT_SHEET,
    capacity_override: float | None = None,
    ocv_mode: str = "both",
    poly_degree: int = 8,
    save_plots: bool = True,
) -> dict[str, Any]:
    """Full OCV-test pipeline for one file; writes plots/CSVs, returns a payload."""
    stem = xlsx_path.stem
    out_dir = output_dir_for(stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    data, blocks, kinds = _classify(xlsx_path, sheet)
    st = _structure(data, blocks, kinds)
    time_s, current, voltage = data["time_s"], data["current"], data["voltage"]

    qd = sum(abs(_block_ah(data, blocks[i])) for i in st["discharge"])
    qc = sum(abs(_block_ah(data, blocks[i])) for i in st["charge"])
    capacity = capacity_override or (qd if qd > 0 else qc) or None
    if not capacity or capacity <= 0:
        raise ValueError("Could not determine a capacity for the SOC axis.")
    capacity_used = round(float(capacity), 4)

    soc = _continuous_soc(time_s, current, st["ref_row"], capacity)
    warnings: list[str] = []

    # -- discharge: rested equilibrium anchors (incl. the 100% point) --------- #
    rested: list[tuple[float, float]] = [(float(soc[st["ref_row"]]), float(voltage[st["ref_row"]]))]
    for di in st["discharge"]:
        nxt = di + 1
        if nxt < len(blocks) and kinds[nxt] == "rest":
            r_end = blocks[nxt][1]
            rested.append((float(soc[r_end]), float(voltage[r_end])))
    # Clamp tiny coulomb-count overshoots (e.g. -0.004) into [0, 1].
    rested = [(min(max(s, 0.0), 1.0), v) for s, v in rested if np.isfinite(s)]
    rested.sort()
    rested_soc = np.array([s for s, _ in rested])
    rested_v = np.array([v for _, v in rested])

    # -- discharge: smooth OCV via monotone PCHIP through the rested anchors --- #
    # The rested equilibrium points are the true OCV; PCHIP gives a smooth,
    # overshoot-free curve through them (the raw loaded curve below is the GITT
    # sawtooth and is kept only as a reference trace).
    smooth_soc = smooth_v = np.array([])
    if len(rested_soc) >= 2:
        u_soc, u_idx = np.unique(rested_soc, return_index=True)
        if len(u_soc) >= 2:
            pchip = PchipInterpolator(u_soc, rested_v[u_idx])
            smooth_soc = np.round(np.arange(u_soc.min(), u_soc.max() + 1e-9, 0.005), 4)
            smooth_v = pchip(smooth_soc)

    # -- discharge: raw low-rate curve (discharge steps only) ----------------- #
    d_rows = _rows_for_blocks(blocks, st["discharge"])
    dis_soc, dis_v = _downsample(soc[d_rows], voltage[d_rows])

    # -- charge: pseudo-OCV from the slow charge ------------------------------ #
    chg_soc = chg_v = np.array([])
    charge_rested = None
    if st["charge"]:
        c_rows = _rows_for_blocks(blocks, st["charge"])
        chg_soc, chg_v = _downsample(soc[c_rows], voltage[c_rows])
        if st["final_rest"] is not None:
            r_end = blocks[st["final_rest"]][1]
            charge_rested = (float(soc[r_end]), float(voltage[r_end]))
    else:
        warnings.append("OCV: no trailing slow-charge step found; charge OCV unavailable.")

    # -- mean + hysteresis on the overlapping SOC range ----------------------- #
    grid = mean_v = hyst_v = chg_on_grid = dis_on_grid = np.array([])
    if len(dis_soc) >= 2 and len(chg_soc) >= 2:
        lo = max(dis_soc.min(), chg_soc.min())
        hi = min(dis_soc.max(), chg_soc.max())
        if hi > lo:
            grid = np.round(np.arange(np.ceil(lo * 100) / 100, hi + 1e-9, 0.01), 4)
            dis_on_grid = np.interp(grid, dis_soc, dis_v)
            chg_on_grid = np.interp(grid, chg_soc, chg_v)
            mean_v = (chg_on_grid + dis_on_grid) / 2.0
            hyst_v = chg_on_grid - dis_on_grid
    if not len(grid):
        warnings.append("OCV: discharge and charge SOC ranges do not overlap; no mean/hysteresis.")

    # -- polynomial fits (optional) ------------------------------------------- #
    poly = {}
    if ocv_mode in ("analytical", "both"):
        if len(rested) >= 2:
            poly["discharge_rested"] = ecm_ocv.fit_ocv_polynomial(
                [[s, v] for s, v in rested], poly_degree
            )
        if len(chg_soc) >= 2:
            poly["charge"] = ecm_ocv.fit_ocv_polynomial(
                list(zip(chg_soc.tolist(), chg_v.tolist())), poly_degree
            )

    # -- save CSVs ------------------------------------------------------------ #
    paths = _save_csvs(
        out_dir, stem, rested_soc, rested_v, smooth_soc, smooth_v,
        dis_soc, dis_v, chg_soc, chg_v, grid, dis_on_grid, chg_on_grid, mean_v, hyst_v,
    )

    # -- plots ---------------------------------------------------------------- #
    images = {}
    if save_plots:
        images = _plot(
            out_dir, stem, rested_soc, rested_v, smooth_soc, smooth_v,
            dis_soc, dis_v, chg_soc, chg_v, charge_rested, grid, mean_v, hyst_v,
        )

    endpoints = {
        "ocv_100_discharge": round(float(rested_v[np.argmax(rested_soc)]), 4) if len(rested_v) else None,
        "ocv_0_discharge": round(float(dis_v[np.argmin(dis_soc)]), 4) if len(dis_v) else None,
        "ocv_100_charge": round(float(chg_v[np.argmax(chg_soc)]), 4) if len(chg_v) else None,
        "ocv_0_charge": round(float(chg_v[np.argmin(chg_soc)]), 4) if len(chg_v) else None,
        "max_hysteresis_v": round(float(np.max(np.abs(hyst_v))), 4) if len(hyst_v) else None,
        "mean_hysteresis_v": round(float(np.mean(np.abs(hyst_v))), 4) if len(hyst_v) else None,
    }

    summary = {
        "qd_ah": round(qd, 4), "qc_ah": round(qc, 4),
        "capacity_used_ah": capacity_used,
        "n_discharge_steps": len(st["discharge"]),
        **endpoints,
        "warnings": " | ".join(warnings),
    }
    pd.DataFrame([summary]).to_csv(out_dir / f"{stem}_ocvtest_summary.csv", index=False)

    return {
        "name": xlsx_path.name,
        "stem": stem,
        "out_dir": str(out_dir),
        "capacity_used": capacity_used,
        "qd": round(qd, 4),
        "qc": round(qc, 4),
        "n_discharge_steps": len(st["discharge"]),
        "endpoints": endpoints,
        "warnings": warnings,
        "poly": poly,
        "rested_anchors": [[round(s, 4), round(v, 4)] for s, v in rested],
        "curves": {
            "discharge_rested": {"soc": rested_soc.round(4).tolist(), "ocv": rested_v.round(5).tolist()},
            "discharge_smooth": {"soc": smooth_soc.round(4).tolist(), "ocv": np.round(smooth_v, 5).tolist()},
            "discharge_raw": {"soc": dis_soc.round(4).tolist(), "ocv": dis_v.round(5).tolist()},
            "charge": {"soc": chg_soc.round(4).tolist(), "ocv": chg_v.round(5).tolist()},
            "mean": {"soc": grid.round(4).tolist(), "ocv": np.round(mean_v, 5).tolist()},
            "hysteresis": {"soc": grid.round(4).tolist(), "v": np.round(hyst_v, 5).tolist()},
        },
        "images": images,
        "csv": paths,
    }


def _save_csvs(out_dir, stem, r_soc, r_v, sm_soc, sm_v, d_soc, d_v, c_soc, c_v, grid, d_grid, c_grid, mean_v, hyst_v):
    rested_csv = out_dir / f"{stem}_ocvtest_discharge_rested.csv"
    pd.DataFrame({"soc": r_soc, "ocv_v": r_v}).to_csv(rested_csv, index=False)

    if len(sm_soc):
        pd.DataFrame({"soc": sm_soc, "ocv_v": sm_v}).to_csv(
            out_dir / f"{stem}_ocvtest_discharge_smooth.csv", index=False
        )

    curves_csv = out_dir / f"{stem}_ocvtest_curves.csv"
    if len(grid):
        pd.DataFrame({
            "soc": grid, "discharge_ocv_v": d_grid, "charge_ocv_v": c_grid,
            "mean_ocv_v": mean_v, "hysteresis_v": hyst_v,
        }).to_csv(curves_csv, index=False)
    else:
        # Fall back to the raw curves when there is no common grid.
        pd.DataFrame({"soc": d_soc, "discharge_ocv_v": d_v}).to_csv(curves_csv, index=False)

    return {"discharge_rested_csv": str(rested_csv), "curves_csv": str(curves_csv)}


def _save_vectors(fig, png_path: Path) -> dict[str, str]:
    fig.savefig(png_path, dpi=150)
    fig.savefig(png_path.with_suffix(".svg"))
    fig.savefig(png_path.with_suffix(".pdf"))
    return {
        "png": str(png_path),
        "svg": str(png_path.with_suffix(".svg")),
        "pdf": str(png_path.with_suffix(".pdf")),
    }


def _plot(out_dir, stem, r_soc, r_v, sm_soc, sm_v, d_soc, d_v, c_soc, c_v, charge_rested, grid, mean_v, hyst_v):
    images = {}

    # 1) OCV curves: smooth discharge OCV + raw (GITT sawtooth) + charge pseudo.
    fig, ax = plt.subplots(figsize=(8, 5), tight_layout=True)
    if len(d_soc):
        ax.plot(d_soc, d_v, "-", color="C0", lw=1, alpha=0.45, label="Discharge (loaded, raw)")
    if len(c_soc):
        ax.plot(c_soc, c_v, "-", color="C3", label="Charge (low-rate)")
    if len(sm_soc):
        ax.plot(sm_soc, sm_v, "-", color="C2", lw=2, label="Discharge OCV (smooth)")
    if len(r_soc):
        ax.plot(r_soc, r_v, "o", color="C2", ms=5, label="Discharge rested (OCV)")
    if charge_rested is not None:
        ax.plot([charge_rested[0]], [charge_rested[1]], "s", color="C1", ms=7, label="Charge rested (OCV)")
    ax.set_xlabel("SOC [-]")
    ax.set_ylabel("Voltage [V]")
    ax.set_title("OCV vs SOC — discharge & charge")
    ax.grid(True, color="0.9")
    ax.set_frame_on(False)
    ax.legend(loc="best")
    images["curves"] = _save_vectors(fig, out_dir / f"{stem}_ocvtest.png")
    plt.close(fig)

    # 2) Mean OCV + hysteresis.
    if len(grid):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True, tight_layout=True)
        ax1.plot(grid, mean_v, "-", color="C4")
        ax1.set_ylabel("Mean OCV [V]")
        ax1.set_title("Mean OCV and hysteresis vs SOC")
        ax1.grid(True, color="0.9")
        ax1.set_frame_on(False)
        ax2.plot(grid, hyst_v * 1000.0, "-", color="C5")
        ax2.axhline(0.0, color="0.7", lw=0.8)
        ax2.set_xlabel("SOC [-]")
        ax2.set_ylabel("Hysteresis [mV]")
        ax2.grid(True, color="0.9")
        ax2.set_frame_on(False)
        images["mean_hyst"] = _save_vectors(fig, out_dir / f"{stem}_ocvtest_mean_hyst.png")
        plt.close(fig)

    return images
