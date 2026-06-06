"""Equivalent-Circuit-Model (ECM) backend.

Wraps the CLI scripts in ``equiv-circ-model`` so the web app can:
  1. auto-detect cell capacity (Qd from the HPPC test, Qc from the final CCCV),
  2. extract the HPPC pulse section from a Neware ``.xlsx`` into a pipeline CSV,
  3. fit a 1-RC or 2-RC ECM and estimate R0, R1, C1, ... per SOC.

All outputs are written under
``equiv-circ-model/Equivalent-Circuit/<xlsx-stem>/`` (one subfolder per file).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import matplotlib

# Headless rendering — the fitting/plotting code opens no GUI windows.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import numpy as np  # noqa: E402

from webapp.config import PROJECT_ROOT  # noqa: E402

# Make the standalone ``equiv-circ-model`` package importable.
EQUIV_DIR = PROJECT_ROOT / "equiv-circ-model"
if str(EQUIV_DIR) not in sys.path:
    sys.path.insert(0, str(EQUIV_DIR))

import extract_hppc  # noqa: E402
from ecm import (  # noqa: E402
    CellHppcData,
    EcmConfig,
    available_algorithms,
    fit_ecm_from_hppc,
    mean_absolute_error,
    plot_hppc_fit,
    plot_rc_params,
    root_mean_square_error,
    save_rctau_csv,
)

OUTPUT_ROOT = EQUIV_DIR / "Equivalent-Circuit"
DEFAULT_SHEET = "Record List1"
DEFAULT_PULSE_MAX_SECONDS = extract_hppc.DEFAULT_PULSE_MAX_SECONDS


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def algorithms() -> tuple[str, ...]:
    return available_algorithms()


def output_dir_for(stem: str) -> Path:
    return OUTPUT_ROOT / stem


def _block_ah(data: dict[str, Any], block: tuple[int, int]) -> float:
    """Signed charge throughput (Ah) for one block via trapezoid integration."""
    start, end = block
    t = data["time_s"][start : end + 1]
    i = data["current"][start : end + 1]
    if len(t) < 2:
        return 0.0
    return float(np.trapz(i, t) / 3600.0)


def detect_capacity(
    xlsx_path: Path,
    sheet: str = DEFAULT_SHEET,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
) -> dict[str, Any]:
    """Estimate cell capacity from a Neware HPPC + final-CCCV file.

    Returns ``{"qd": Ah, "qc": Ah, "capacity": Ah}`` where ``qd`` is the total
    discharge throughput across the HPPC region (full -> empty) and ``qc`` is the
    charge throughput of the trailing full CCCV charge. ``capacity`` prefers
    ``qd`` and falls back to ``qc``.
    """
    data = extract_hppc.load_records(xlsx_path, sheet)
    blocks = extract_hppc.build_blocks(data["command"])
    kinds = [extract_hppc.classify_block(data, b, pulse_max_seconds) for b in blocks]

    # Qc: trailing run of full-charge blocks (the final CCCV), skipping rests.
    qc = 0.0
    for idx in range(len(blocks) - 1, -1, -1):
        if kinds[idx] == "full_charge":
            qc += abs(_block_ah(data, blocks[idx]))
        elif kinds[idx] == "rest":
            continue
        elif qc > 0:
            break

    # Locate the HPPC region: first discharge pulse .. start of the final charge.
    first_pulse = next((i for i, k in enumerate(kinds) if k == "discharge_pulse"), None)
    final_charge = None
    if first_pulse is not None:
        for i in range(len(kinds) - 1, first_pulse, -1):
            if kinds[i] == "full_charge":
                final_charge = i
            elif final_charge is not None and kinds[i] not in ("full_charge", "rest"):
                break
    region_end = final_charge if final_charge is not None else len(blocks)

    # Qd: discharge throughput (pulses + constant discharges) over the HPPC region.
    qd = 0.0
    if first_pulse is not None:
        for i in range(first_pulse, region_end):
            if kinds[i] in ("discharge_pulse", "constant_discharge"):
                qd += abs(_block_ah(data, blocks[i]))

    capacity = qd if qd > 0 else qc
    return {
        "qd": round(qd, 4),
        "qc": round(qc, 4),
        "capacity": round(capacity, 4) if capacity else None,
    }


def extract_pulses(
    xlsx_path: Path,
    out_csv: Path,
    sheet: str = DEFAULT_SHEET,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
    save_plot: Path | None = None,
) -> dict[str, Any]:
    """Extract the HPPC pulse section into ``out_csv`` (and an optional PNG)."""
    data = extract_hppc.load_records(xlsx_path, sheet)
    blocks = extract_hppc.build_blocks(data["command"])
    end_row, section_flags = extract_hppc.detect_hppc_region(
        data, blocks, pulse_max_seconds
    )
    time, current, voltage, flags = extract_hppc.build_output(
        data, end_row, section_flags
    )
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    extract_hppc.write_csv(out_csv, time, current, voltage, flags)

    if save_plot is not None:
        save_plot.parent.mkdir(parents=True, exist_ok=True)
        extract_hppc.plot_region(time, current, voltage, save_path=save_plot, show=False)
        plt.close("all")

    return {
        "n_sections": len(section_flags),
        "n_rows": int(len(time)),
        "duration_s": float(time[-1]) if len(time) else 0.0,
        "v_min": float(voltage.min()) if len(voltage) else None,
        "v_max": float(voltage.max()) if len(voltage) else None,
        "i_min": float(current.min()) if len(current) else None,
        "i_max": float(current.max()) if len(current) else None,
        "pulse_csv": str(out_csv),
        "pulse_png": str(save_plot) if save_plot else None,
    }


def fit_pulses(
    pulse_csv: Path,
    out_dir: Path,
    stem: str,
    rc_order: int = 1,
    algorithm: str = "curve_fit",
    capacity: float | None = None,
    source: str = "pulse",
    save_plots: bool = True,
) -> dict[str, Any]:
    """Fit an ECM from an extracted pulse CSV and save params + plots."""
    config = EcmConfig(q_cell=capacity) if capacity else EcmConfig()
    hppc_data = CellHppcData(pulse_csv)

    _, fit_result = fit_ecm_from_hppc(
        hppc_data,
        config,
        rc_order=rc_order,
        algorithm=algorithm,
        source=source,
    )

    mae = mean_absolute_error(hppc_data.voltage, fit_result.vt)
    rmse = root_mean_square_error(hppc_data.voltage, fit_result.vt)

    out_dir.mkdir(parents=True, exist_ok=True)
    params_csv = out_dir / f"{stem}_{rc_order}rc_parameters.csv"
    param_df = save_rctau_csv(
        fit_result.rctau,
        params_csv,
        rc_order=rc_order,
        soc_values=fit_result.soc_points,
    )

    fit_png = out_dir / f"{stem}_{rc_order}rc_fit.png"
    params_png = out_dir / f"{stem}_{rc_order}rc_params.png"
    if save_plots:
        plot_hppc_fit(
            hppc_data.time,
            hppc_data.voltage,
            fit_result.vt,
            rc_order=rc_order,
            save_path=fit_png,
            show=False,
        )
        plot_rc_params(param_df, save_path=params_png, show=False)
        plt.close("all")

    # Round numeric table values for a tidy JSON payload.
    rounded = param_df.copy()
    for col in rounded.columns:
        rounded[col] = rounded[col].map(lambda x: round(float(x), 6))

    return {
        "rc_order": rc_order,
        "algorithm": algorithm,
        "capacity": capacity,
        "source": source,
        "mae": float(mae),
        "rmse": float(rmse),
        "columns": list(param_df.columns),
        "rows": rounded.to_dict(orient="records"),
        "params_csv": str(params_csv),
        "fit_png": str(fit_png) if save_plots else None,
        "params_png": str(params_png) if save_plots else None,
    }


def process_file(
    xlsx_path: Path,
    rc_order: int = 1,
    algorithm: str = "curve_fit",
    sheet: str = DEFAULT_SHEET,
    capacity_override: float | None = None,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
) -> dict[str, Any]:
    """Full pipeline for one file: detect capacity, extract, fit, save."""
    stem = xlsx_path.stem
    out_dir = output_dir_for(stem)

    cap = detect_capacity(xlsx_path, sheet, pulse_max_seconds)
    capacity = capacity_override or cap.get("capacity") or EcmConfig().q_cell

    pulse_csv = out_dir / f"{stem}_pulses.csv"
    pulse_png = out_dir / f"{stem}_pulses.png"
    extract = extract_pulses(
        xlsx_path, pulse_csv, sheet, pulse_max_seconds, save_plot=pulse_png
    )

    fit = fit_pulses(
        pulse_csv, out_dir, stem,
        rc_order=rc_order, algorithm=algorithm, capacity=capacity,
    )

    return {
        "name": xlsx_path.name,
        "stem": stem,
        "xlsx_path": str(xlsx_path),
        "out_dir": str(out_dir),
        "capacity_detected": cap,
        "capacity_used": round(float(capacity), 4),
        "extract": extract,
        "fit": fit,
    }


def find_xlsx_files(folder: Path) -> list[Path]:
    """Recursively list real ``.xlsx`` files (skipping Excel temp files)."""
    return sorted(
        p
        for p in folder.rglob("*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    )
