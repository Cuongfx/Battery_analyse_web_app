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
import pandas as pd  # noqa: E402

from webapp.config import PROJECT_ROOT  # noqa: E402
from webapp.data_processing import ecm_ocv  # noqa: E402
from webapp.data_processing import ecm_zero_soc  # noqa: E402

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


def extrapolation_methods() -> list[dict[str, str]]:
    """Selectable 0% SOC extrapolation techniques as ``[{value, label}, ...]``."""
    return ecm_zero_soc.available_extrapolation_methods()


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


def _classify(xlsx_path, sheet, pulse_max_seconds):
    """Load the workbook once and classify every step block."""
    data = extract_hppc.load_records(xlsx_path, sheet)
    blocks = extract_hppc.build_blocks(data["command"])
    kinds = [extract_hppc.classify_block(data, b, pulse_max_seconds) for b in blocks]
    return data, blocks, kinds


def _region_indices(kinds):
    """Return ``(first_pulse, final_charge)`` block indices for the HPPC region."""
    first_pulse = next((i for i, k in enumerate(kinds) if k == "discharge_pulse"), None)
    final_charge = None
    if first_pulse is not None:
        for i in range(len(kinds) - 1, first_pulse, -1):
            if kinds[i] == "full_charge":
                final_charge = i
            elif final_charge is not None and kinds[i] not in ("full_charge", "rest"):
                break
    return first_pulse, final_charge


def _capacity_and_ranges(data, blocks, kinds, first_pulse, final_charge):
    """Qd/Qc capacity plus HPPC-window voltage/current ranges (one pass)."""
    region_end = final_charge if final_charge is not None else len(blocks)

    # Qc: trailing run of full-charge blocks (the final CCCV), skipping rests.
    qc = 0.0
    for idx in range(len(blocks) - 1, -1, -1):
        if kinds[idx] == "full_charge":
            qc += abs(_block_ah(data, blocks[idx]))
        elif kinds[idx] == "rest":
            continue
        elif qc > 0:
            break

    # Qd: discharge capacity = throughput of the constant-discharge (SOC-stepping)
    # steps only. The short HPPC pulses are excluded because each discharge pulse
    # is offset by a charge pulse and nets ~zero SOC change.
    qd = 0.0
    if first_pulse is not None:
        for i in range(first_pulse, region_end):
            if kinds[i] == "constant_discharge":
                qd += abs(_block_ah(data, blocks[i]))

    capacity = qd if qd > 0 else qc

    v_min = v_max = i_chg_max = i_dch_max = None
    if first_pulse is not None and region_end > first_pulse:
        start_row = blocks[first_pulse][0]
        end_row = blocks[region_end - 1][1]
        v_region = data["voltage"][start_row : end_row + 1]
        i_region = data["current"][start_row : end_row + 1]
        if len(v_region):
            v_min = round(float(np.min(v_region)), 4)
            v_max = round(float(np.max(v_region)), 4)
            i_chg_max = round(float(np.max(i_region)), 4)   # most positive (charge)
            i_dch_max = round(float(np.min(i_region)), 4)   # most negative (discharge)

    return {
        "qd": round(qd, 4),
        "qc": round(qc, 4),
        "capacity": round(capacity, 4) if capacity else None,
        "v_min": v_min,
        "v_max": v_max,
        "i_chg_max": i_chg_max,
        "i_dch_max": i_dch_max,
    }


def detect_capacity(
    xlsx_path: Path,
    sheet: str = DEFAULT_SHEET,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
) -> dict[str, Any]:
    """Estimate cell capacity (Qd/Qc) and HPPC-window V/I ranges from one file.

    ``qd`` is the constant-discharge throughput (full -> empty), ``qc`` the
    trailing full CCCV charge; ``capacity`` prefers ``qd`` and falls back to ``qc``.
    """
    data, blocks, kinds = _classify(xlsx_path, sheet, pulse_max_seconds)
    first_pulse, final_charge = _region_indices(kinds)
    return _capacity_and_ranges(data, blocks, kinds, first_pulse, final_charge)


def analyze_hppc(
    xlsx_path: Path,
    sheet: str = DEFAULT_SHEET,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
    capacity_override: float | None = None,
) -> dict[str, Any]:
    """Single-load analysis: capacity + ranges + OCV anchors.

    ``capacity_override`` (if given) is the capacity used for the OCV SOC axis,
    matching the value used for the fit; otherwise the detected capacity is used.
    (The ~0% SOC RC row is extrapolated later in ``fit_pulses`` from the fitted
    parameter curves, so it needs nothing from here.)
    """
    data, blocks, kinds = _classify(xlsx_path, sheet, pulse_max_seconds)
    first_pulse, final_charge = _region_indices(kinds)
    cap = _capacity_and_ranges(data, blocks, kinds, first_pulse, final_charge)

    soc_capacity = capacity_override or cap.get("capacity")
    ocv = ecm_ocv.estimate_ocv(
        data["time_s"], data["current"], data["voltage"],
        blocks, kinds, first_pulse, final_charge, soc_capacity,
    )
    return {
        "capacity": cap,
        "ocv_anchors": ocv["anchors"],
        "ocv_warnings": ocv["warnings"],
    }


def build_ocv_outputs(
    out_dir: Path,
    stem: str,
    anchors: list,
    ocv_mode: str = "both",
    poly_degree: int = 8,
    save_plot: bool = True,
) -> dict[str, Any]:
    """Build the OCV table/polynomial, save the CSV + PNG, return a payload."""
    table = ecm_ocv.ocv_table(anchors)
    poly = (
        ecm_ocv.fit_ocv_polynomial(anchors, poly_degree)
        if ocv_mode in ("analytical", "both")
        else None
    )
    endpoints = ecm_ocv.ocv_endpoints(anchors)

    ocv_csv = ecm_ocv.save_ocv_csv(out_dir, stem, table, poly) if anchors else None
    ocv_png = out_dir / f"{stem}_ocv.png"
    ocv_svg = ocv_pdf = None
    if save_plot and anchors:
        ecm_ocv.plot_ocv(anchors, table, poly, ocv_png, show=False)
        ocv_svg = str(ocv_png.with_suffix(".svg"))
        ocv_pdf = str(ocv_png.with_suffix(".pdf"))
    else:
        ocv_png = None

    return {
        "mode": ocv_mode,
        "anchors": anchors,
        "table": table,
        "poly": poly,
        "endpoints": endpoints,
        "csv": str(ocv_csv) if ocv_csv else None,
        "png": str(ocv_png) if ocv_png else None,
        "svg": ocv_svg,
        "pdf": ocv_pdf,
    }


def save_summary_csv(out_dir: Path, stem: str, summary: dict[str, Any]) -> Path:
    """Write the capacity, detected ranges, applied limits and any warnings."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}_summary.csv"
    row = dict(summary)
    # Flatten the warnings list so it fits in one CSV cell.
    if isinstance(row.get("warnings"), list):
        row["warnings"] = " | ".join(row["warnings"])
    pd.DataFrame([row]).to_csv(path, index=False)
    return path


def validate_against_bounds(
    detected: dict[str, Any],
    *,
    v_max: float | None = None,
    v_min: float | None = None,
    i_chg_max: float | None = None,
    i_dch_max: float | None = None,
) -> list[str]:
    """Warn (non-blocking) when measured ranges fall outside user-entered bounds.

    Compares the detected HPPC-window ranges in ``detected`` against optional
    user limits. The measured signal is never modified.
    """
    warnings: list[str] = []
    mv_min, mv_max = detected.get("v_min"), detected.get("v_max")
    mi_chg, mi_dch = detected.get("i_chg_max"), detected.get("i_dch_max")

    if v_max is not None and mv_max is not None and mv_max > v_max:
        warnings.append(f"Measured Vmax {mv_max} V exceeds the entered max {v_max} V.")
    if v_min is not None and mv_min is not None and mv_min < v_min:
        warnings.append(f"Measured Vmin {mv_min} V is below the entered min {v_min} V.")
    if i_chg_max is not None and mi_chg is not None and mi_chg > i_chg_max:
        warnings.append(f"Measured charge current {mi_chg} A exceeds the entered max {i_chg_max} A.")
    if i_dch_max is not None and mi_dch is not None and mi_dch < i_dch_max:
        warnings.append(f"Measured discharge current {mi_dch} A exceeds the entered max {i_dch_max} A.")
    return warnings


def effective_v_limits(
    detected: dict[str, Any],
    v_min: float | None = None,
    v_max: float | None = None,
) -> tuple[float | None, float | None]:
    """Voltage window to clip/plot with: user value if given, else detected."""
    return (
        v_min if v_min is not None else detected.get("v_min"),
        v_max if v_max is not None else detected.get("v_max"),
    )


def build_summary(
    detected: dict[str, Any],
    capacity_used: float | None,
    v_limits: tuple[float | None, float | None],
    nominal_capacity: float | None,
    warnings: list[str],
    ocv: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the per-file summary written to ``<stem>_summary.csv``."""
    summary = {
        "qd_ah": detected.get("qd"),
        "qc_ah": detected.get("qc"),
        "capacity_used_ah": capacity_used,
        "nominal_capacity_ah": nominal_capacity,
        "v_min_detected": detected.get("v_min"),
        "v_max_detected": detected.get("v_max"),
        "i_chg_max_detected": detected.get("i_chg_max"),
        "i_dch_max_detected": detected.get("i_dch_max"),
        "v_min_used": v_limits[0],
        "v_max_used": v_limits[1],
        "warnings": warnings,
    }
    if ocv is not None:
        ep = ocv.get("endpoints") or {}
        poly = ocv.get("poly")
        summary["ocv_100_v"] = ep.get("ocv_100")
        summary["ocv_0_v"] = ep.get("ocv_0")
        summary["ocv_poly_degree"] = poly["degree"] if poly else None
        summary["ocv_poly_rmse"] = poly["rmse"] if poly else None
        summary["ocv_poly_coeffs"] = ";".join(str(c) for c in poly["coeffs"]) if poly else None
    return summary


def save_vector_formats(fig, png_path: Path) -> tuple[str, str]:
    """Save a matplotlib figure as vector ``.svg`` and ``.pdf`` next to its PNG."""
    svg_path = png_path.with_suffix(".svg")
    pdf_path = png_path.with_suffix(".pdf")
    fig.savefig(svg_path)
    fig.savefig(pdf_path)
    return str(svg_path), str(pdf_path)


def _clip_to_last_marker(hppc_data) -> None:
    """Trim the loaded data to the last HPPC ``S`` marker (drops the recharge tail).

    The pulse CSV now spans the whole process; everything after the last HPPC
    marker is the trailing full charge, which must not enter the fit or the
    MAE/RMSE. All fitted relaxation windows end at or before that marker.
    """
    s = hppc_data.get_indices_s()
    if len(s) == 0:
        return
    last = int(s[-1]) + 1
    hppc_data.time = hppc_data.time[:last]
    hppc_data.current = hppc_data.current[:last]
    hppc_data.voltage = hppc_data.voltage[:last]
    hppc_data.flags = hppc_data.flags[:last]


def _append_zero_soc_row(rctau, soc_points, rc_order, method):
    """Append a ~0% SOC row extrapolated from the fitted parameter curves.

    Returns ``(rctau, soc_points, warning)``. The row is built by
    ``ecm_zero_soc.zero_soc_row`` (each R/tau extrapolated to SOC=0, C derived);
    on failure the table is returned unchanged with a non-blocking warning.
    """
    result = ecm_zero_soc.zero_soc_row(soc_points, rctau, rc_order, method)
    if result["row"] is None:
        return rctau, soc_points, result["warning"]
    rctau = np.vstack([rctau, np.asarray(result["row"], dtype=float)])
    soc_points = np.append(soc_points, float(result["soc"]))
    return rctau, soc_points, None


def extract_pulses(
    xlsx_path: Path,
    out_csv: Path,
    sheet: str = DEFAULT_SHEET,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
    save_plot: Path | None = None,
) -> dict[str, Any]:
    """Capture the whole test process into ``out_csv`` (and an optional PNG/SVG/PDF).

    The CSV/plot span the complete process (initial full charge -> rest -> HPPC
    -> final rest -> recharge); HPPC section markers are preserved so the fit
    still operates only on the pulse relaxations.
    """
    data = extract_hppc.load_records(xlsx_path, sheet)
    blocks = extract_hppc.build_blocks(data["command"])
    _, section_flags = extract_hppc.detect_hppc_region(
        data, blocks, pulse_max_seconds
    )
    time, current, voltage, flags = extract_hppc.build_full_output(
        data, section_flags
    )
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    extract_hppc.write_csv(out_csv, time, current, voltage, flags)

    pulse_svg = pulse_pdf = None
    if save_plot is not None:
        save_plot.parent.mkdir(parents=True, exist_ok=True)
        fig = extract_hppc.plot_region(
            time, current, voltage, save_path=save_plot, show=False,
            title="Battery test — full process (charge · rest · HPPC · rest · recharge)",
        )
        pulse_svg, pulse_pdf = save_vector_formats(fig, save_plot)
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
        "pulse_svg": pulse_svg,
        "pulse_pdf": pulse_pdf,
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
    v_limits: tuple[float | None, float | None] | None = None,
    zero_soc_method: str | None = ecm_zero_soc.DEFAULT_METHOD,
) -> dict[str, Any]:
    """Fit an ECM from an extracted pulse CSV and save params + plots.

    ``v_limits`` is an optional ``(vmin, vmax)`` window. When given, the simulated
    terminal voltage is clipped to it (a real cell cannot leave its voltage
    window) before computing MAE/RMSE and plotting, and the fit-plot y-axis is
    locked to it. The estimated R/C parameters are unaffected by clipping.

    ``zero_soc_method`` selects the technique used to **extrapolate** a ~0% SOC
    row from the fitted 100%->5% parameter curves (see ``ecm_zero_soc``); it is
    appended to the R/C-vs-SOC table/plot. Pass ``None``/``"none"`` to skip it.
    It enriches the parameter outputs only; the fit/MAE stay HPPC-based.
    """
    config = EcmConfig(q_cell=capacity) if capacity else EcmConfig()
    hppc_data = CellHppcData(pulse_csv)
    # The pulse CSV spans the whole process; drop the trailing recharge (every
    # row past the last HPPC marker) so the fit and MAE/RMSE stay HPPC-only.
    _clip_to_last_marker(hppc_data)

    _, fit_result = fit_ecm_from_hppc(
        hppc_data,
        config,
        rc_order=rc_order,
        algorithm=algorithm,
        source=source,
    )

    vt = np.asarray(fit_result.vt, dtype=float)
    ylim = None
    if v_limits is not None:
        vmin, vmax = v_limits
        if vmin is not None or vmax is not None:
            vt = np.clip(vt, vmin, vmax)
        if vmin is not None and vmax is not None:
            pad = max((vmax - vmin) * 0.05, 0.02)
            ylim = (vmin - pad, vmax + pad)

    mae = mean_absolute_error(hppc_data.voltage, vt)
    rmse = root_mean_square_error(hppc_data.voltage, vt)

    rctau = np.asarray(fit_result.rctau, dtype=float)
    soc_points = np.asarray(fit_result.soc_points, dtype=float)
    zero_soc_warning = None
    if zero_soc_method and zero_soc_method != "none":
        rctau, soc_points, zero_soc_warning = _append_zero_soc_row(
            rctau, soc_points, rc_order, zero_soc_method
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    params_csv = out_dir / f"{stem}_{rc_order}rc_parameters.csv"
    param_df = save_rctau_csv(
        rctau,
        params_csv,
        rc_order=rc_order,
        soc_values=soc_points,
    )

    fit_png = out_dir / f"{stem}_{rc_order}rc_fit.png"
    params_png = out_dir / f"{stem}_{rc_order}rc_params.png"
    fit_svg = fit_pdf = params_svg = params_pdf = None
    if save_plots:
        fig_fit = plot_hppc_fit(
            hppc_data.time,
            hppc_data.voltage,
            vt,
            rc_order=rc_order,
            ylim=ylim,
            save_path=fit_png,
            show=False,
        )
        fit_svg, fit_pdf = save_vector_formats(fig_fit, fit_png)
        fig_par = plot_rc_params(param_df, save_path=params_png, show=False)
        params_svg, params_pdf = save_vector_formats(fig_par, params_png)
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
        "zero_soc_method": zero_soc_method,
        "zero_soc_warning": zero_soc_warning,
        "columns": list(param_df.columns),
        "rows": rounded.to_dict(orient="records"),
        "params_csv": str(params_csv),
        "fit_png": str(fit_png) if save_plots else None,
        "fit_svg": fit_svg,
        "fit_pdf": fit_pdf,
        "params_png": str(params_png) if save_plots else None,
        "params_svg": params_svg,
        "params_pdf": params_pdf,
    }


def process_file(
    xlsx_path: Path,
    rc_order: int = 1,
    algorithm: str = "curve_fit",
    sheet: str = DEFAULT_SHEET,
    capacity_override: float | None = None,
    pulse_max_seconds: float = DEFAULT_PULSE_MAX_SECONDS,
    v_max: float | None = None,
    v_min: float | None = None,
    i_chg_max: float | None = None,
    i_dch_max: float | None = None,
    nominal_capacity: float | None = None,
    ocv_mode: str = "both",
    ocv_poly_degree: int = 8,
    zero_soc_method: str | None = ecm_zero_soc.DEFAULT_METHOD,
) -> dict[str, Any]:
    """Full pipeline for one file: analyze, validate, extract, fit, OCV, save.

    ``v_max``/``v_min``/``i_chg_max``/``i_dch_max`` are optional limits (else the
    detected HPPC-window ranges are used). ``nominal_capacity`` is reference-only
    and does not affect SOC. ``zero_soc_method`` selects the 0% SOC extrapolation
    technique (``None``/``"none"`` to skip it).
    """
    stem = xlsx_path.stem
    out_dir = output_dir_for(stem)

    # One workbook load -> capacity, ranges and OCV anchors (SOC axis uses the
    # same capacity as the fit).
    capacity_hint = capacity_override
    analysis = analyze_hppc(xlsx_path, sheet, pulse_max_seconds, capacity_hint)
    cap = analysis["capacity"]
    capacity = capacity_override or cap.get("capacity") or EcmConfig().q_cell
    capacity_used = round(float(capacity), 4)

    v_limits = effective_v_limits(cap, v_min, v_max)
    warnings = validate_against_bounds(
        cap, v_max=v_max, v_min=v_min, i_chg_max=i_chg_max, i_dch_max=i_dch_max
    )
    warnings = warnings + list(analysis.get("ocv_warnings") or [])

    pulse_csv = out_dir / f"{stem}_pulses.csv"
    pulse_png = out_dir / f"{stem}_pulses.png"
    extract = extract_pulses(
        xlsx_path, pulse_csv, sheet, pulse_max_seconds, save_plot=pulse_png
    )

    fit = fit_pulses(
        pulse_csv, out_dir, stem,
        rc_order=rc_order, algorithm=algorithm, capacity=capacity,
        v_limits=v_limits, zero_soc_method=zero_soc_method,
    )
    if fit.get("zero_soc_warning"):
        warnings = warnings + [fit["zero_soc_warning"]]

    ocv = build_ocv_outputs(
        out_dir, stem, analysis["ocv_anchors"], ocv_mode, ocv_poly_degree
    )

    summary = build_summary(cap, capacity_used, v_limits, nominal_capacity, warnings, ocv)
    save_summary_csv(out_dir, stem, summary)

    return {
        "name": xlsx_path.name,
        "stem": stem,
        "xlsx_path": str(xlsx_path),
        "out_dir": str(out_dir),
        "capacity_detected": cap,
        "capacity_used": capacity_used,
        "nominal_capacity": nominal_capacity,
        "v_limits_used": list(v_limits),
        "warnings": warnings,
        "extract": extract,
        "fit": fit,
        "ocv": ocv,
    }


def find_xlsx_files(folder: Path) -> list[Path]:
    """Recursively list real ``.xlsx`` files (skipping Excel temp files)."""
    return sorted(
        p
        for p in folder.rglob("*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    )
