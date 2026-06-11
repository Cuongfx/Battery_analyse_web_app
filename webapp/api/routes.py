"""Local web app: browse a folder of BatteryML .pkl files and plot (Plotly)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from webapp.api.models import (
    EcmCapacityBody,
    EcmExtractBody,
    EcmFitBody,
    EcmPickBody,
    FeaturePlotBody,
    FsMkdirBody,
    FolderLogavgBody,
    FolderPathBody,
    LoadPathBody,
    OcvComputeBody,
    PlotBody,
    RulInspectBody,
    RulPickBody,
    RulPredictBody,
    ScreenshotBody,
)
from webapp.config import (
    CACHE_FILE,
    DATA_INFO_DIR,
    DEFAULT_DATA_DIR,
    PROJECT_ROOT,
    STATIC_DIR,
)
from webapp.data_processing.folder_cache import (
    cache_file_valid,
    folder_payload,
    folders_with_pkl_files,
    list_pkl_files,
    load_cycle_cache,
    pkl_cycle_row,
    save_cycle_cache,
)
from webapp.data_processing.inspection import compute_data_meta, summarize_batteryml_pkl
from webapp.data_processing.dataset_info import read_dataset_info
from webapp.data_processing.paths import allow_root, safe_resolve_dir, safe_resolve_pkl
from webapp.data_processing.sessions import create_session, get_session
from webapp.plot.charts import (
    build_feature_compare_figures,
    build_feature_figures_for_kind,
    build_figures_for_kind,
    build_folder_logavg_figure,
    compute_logavg_and_lifetime_for_file,
    capacity_fade_summary,
    figure_payload,
    load_batteryml_pickle,
    make_folder_cycle_bar,
)

app = FastAPI(title="Battery AI Analyzer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

def _plotly_response(figures: list[Any]) -> JSONResponse:
    payload = {
        "figures": [
            figure_payload(fig)
            for fig in figures
        ]
    }
    return JSONResponse(payload)


def _data_meta(obj: dict[str, Any]) -> dict[str, Any]:
    meta = compute_data_meta(obj)
    meta.update(capacity_fade_summary(obj))
    return meta


def _run_tk_dialog(kind: str) -> str:
    """kind: 'folder', 'file' (.pkl) or 'xlsx'. Returns selected path or ''."""
    if kind == "folder":
        call = "filedialog.askdirectory(title='Select data folder')"
    elif kind == "xlsx":
        call = (
            "filedialog.askopenfilename(title='Select .xlsx file',"
            "filetypes=[('Excel files','*.xlsx'),('All files','*.*')])"
        )
    elif kind == "cell":
        call = (
            "filedialog.askopenfilename(title='Select a cell file (.pkl or .npz)',"
            "filetypes=[('Cell files','*.pkl *.npz'),('Pickle','*.pkl'),"
            "('NumPy npz','*.npz'),('All files','*.*')])"
        )
    else:
        call = (
            "filedialog.askopenfilename(title='Select .pkl file',"
            "filetypes=[('Pickle files','*.pkl'),('All files','*.*')])"
        )
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"p = {call}\n"
        "r.destroy()\n"
        "import sys; sys.stdout.write(p or '')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=600,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "Picker failed")
    return res.stdout.strip()


@app.post("/api/pick-folder")
def pick_folder() -> dict[str, Any]:
    try:
        path = _run_tk_dialog("folder")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"path": path}


# --------------------------------------------------------------------------- #
# In-browser file/folder picker (server-side filesystem browse)
# --------------------------------------------------------------------------- #
# Extensions shown per picker kind. Folder/checkpoint pickers list folders only.
_FS_FILE_EXTS: dict[str, set[str]] = {
    "folder": set(),
    "ckpt": set(),
    "xlsx": {".xlsx"},
    "cell": {".pkl", ".npz"},
}


@app.get("/api/fs/list")
def fs_list(path: str = "", kind: str = "folder") -> dict[str, Any]:
    """List sub-folders (and, for file pickers, matching files) of `path`.

    Empty `path` defaults to the project root. Dot-entries are hidden. This
    browses the real filesystem (same reach as the native dialog it replaces);
    actual file serving still goes through the existing per-feature path jails.
    """
    base = Path(path).expanduser() if path.strip() else PROJECT_ROOT
    try:
        d = base.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not d.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a folder: {d}")

    exts = _FS_FILE_EXTS.get(kind, set())
    dirs: list[dict[str, str]] = []
    files: list[dict[str, str]] = []
    try:
        with os.scandir(d) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": entry.name, "type": "dir"})
                    elif exts and Path(entry.name).suffix.lower() in exts:
                        files.append({"name": entry.name, "type": "file"})
                except OSError:
                    continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied: {d}") from exc

    dirs.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())
    parent = str(d.parent) if d.parent != d else None
    return {
        "path": str(d),
        "parent": parent,
        "home": str(Path.home()),
        "entries": dirs + files,
    }


@app.post("/api/fs/mkdir")
def fs_mkdir(body: FsMkdirBody) -> dict[str, Any]:
    """Create a new folder named `name` inside `path`."""
    name = body.name.strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    try:
        parent = Path(body.path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not parent.is_dir():
        raise HTTPException(status_code=404, detail="Parent folder not found")
    target = parent / name
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="A folder with that name already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"path": str(target)}


@app.get("/api/browse")
def browse(dir: str) -> dict[str, Any]:
    try:
        d = safe_resolve_dir(dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    entries: list[dict[str, Any]] = []
    try:
        for entry in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                entries.append({"name": entry.name, "type": "dir", "path": str(entry)})
            elif entry.is_file() and entry.suffix.lower() == ".pkl":
                entries.append({
                    "name": entry.name,
                    "type": "pkl",
                    "path": str(entry),
                    "size": entry.stat().st_size,
                })
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    parent = str(d.parent) if d.parent != d else None
    root = allow_root()
    if root is not None:
        try:
            d.relative_to(root)
            if d == root:
                parent = None
        except ValueError:
            parent = None

    return {"path": str(d), "parent": parent, "entries": entries}


@app.get("/api/folder-inspect")
async def folder_inspect(dir: str) -> dict[str, Any]:
    try:
        d = safe_resolve_dir(dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    return await asyncio.to_thread(folder_payload, d)


@app.get("/api/folder-inspect-stream")
async def folder_inspect_stream(dir: str) -> StreamingResponse:
    try:
        d = safe_resolve_dir(dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    async def generate():
        files = list_pkl_files(d)
        total = len(files)

        if total == 0:
            payload: dict[str, Any] = {
                "done": True, "path": str(d), "name": d.name,
                "file_count": 0, "valid_file_count": 0, "failed_file_count": 0,
                "rows": [], "figure": None, "from_cache": True,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

        cache = load_cycle_cache()
        folders = cache.setdefault("folders", {})
        folder_key = str(d)
        folder_cache = folders.get(folder_key) if isinstance(folders.get(folder_key), dict) else {}
        cached_files = folder_cache.get("files") if isinstance(folder_cache.get("files"), dict) else {}

        rows: list[dict[str, Any]] = []
        next_files: dict[str, Any] = {}
        changed = False

        for i, path in enumerate(files):
            file_key = str(path)
            cached_file = cached_files.get(file_key)
            if cache_file_valid(path, cached_file):
                row = dict(cached_file["row"])
            else:
                row = await asyncio.to_thread(pkl_cycle_row, path)
                changed = True
            rows.append(row)
            next_files[file_key] = {"size": row.get("size"), "mtime_ns": row.get("mtime_ns"), "row": row}

            pct = round((i + 1) / total * 100)
            yield f"data: {json.dumps({'progress': pct, 'current': i + 1, 'total': total, 'file': path.name})}\n\n"

        if set(cached_files.keys()) != set(next_files.keys()) or changed:
            folders[folder_key] = {"path": folder_key, "files": next_files}
            await asyncio.to_thread(save_cycle_cache, cache)

        valid_rows = [r for r in rows if r["max_cycle"] is not None]
        try:
            figure = figure_payload(make_folder_cycle_bar(rows, d.name)) if valid_rows else None
        except Exception:
            figure = None

        done_payload: dict[str, Any] = {
            "done": True, "path": str(d), "name": d.name,
            "file_count": len(rows),
            "valid_file_count": len(valid_rows),
            "failed_file_count": len(rows) - len(valid_rows),
            "rows": rows, "figure": figure, "from_cache": not changed,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cache-folders")
def cache_folders(body: FolderPathBody) -> dict[str, Any]:
    try:
        root = safe_resolve_dir(body.path.strip().strip('"\''))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    folders = folders_with_pkl_files(root)
    files_seen = 0
    cache_hits = 0
    errors = 0
    for folder in folders:
        payload = folder_payload(folder)
        files_seen += payload["file_count"]
        if payload["from_cache"]:
            cache_hits += payload["file_count"]
        errors += payload["failed_file_count"]

    return {
        "root": str(root),
        "folders_cached": len(folders),
        "files_cached": files_seen,
        "cache_hits": cache_hits,
        "files_with_issues": errors,
        "cache_file": str(CACHE_FILE),
    }


@app.post("/api/load-path")
def load_path(body: LoadPathBody) -> dict[str, Any]:
    try:
        path = safe_resolve_pkl(body.path.strip().strip('"\''))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        obj = load_batteryml_pickle(path)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not load pickle: {exc}"
        ) from exc

    sid = create_session(path, obj)
    return {
        "session_id": sid,
        "name": path.name,
        "path": str(path),
        "summary": summarize_batteryml_pkl(obj),
        "meta": _data_meta(obj),
    }


@app.post("/api/session/{session_id}/plot")
def plot_session(session_id: str, body: PlotBody) -> JSONResponse:
    s = get_session(session_id)
    obj = s["obj"]
    cell_name = s["path"].stem if isinstance(s.get("path"), Path) else s["name"]

    try:
        figures = build_figures_for_kind(
            obj,
            cell_name=cell_name,
            kind=body.kind,
            cycles=body.cycles,
            min_dv=body.min_dv,
            min_dq=body.min_dq,
            filter_outliers=body.filter_outliers,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _plotly_response(figures)


@app.post("/api/session/{session_id}/feature-plot")
def feature_plot_session(session_id: str, body: FeaturePlotBody) -> JSONResponse:
    s = get_session(session_id)
    obj = s["obj"]
    cell_name = s["path"].stem if isinstance(s.get("path"), Path) else s["name"]

    try:
        if body.compare_session_id:
            sb = get_session(body.compare_session_id)
            obj_b = sb["obj"]
            cell_name_b = sb["path"].stem if isinstance(sb.get("path"), Path) else sb["name"]
            figures = build_feature_compare_figures(
                obj, obj_b,
                cell_name_a=cell_name,
                cell_name_b=cell_name_b,
                kind=body.kind,
                cycles=body.cycles,
                reference_cycle=body.reference_cycle,
                use_reference_cycle=body.use_reference_cycle,
                min_dv=body.min_dv,
                min_dq=body.min_dq,
                filter_outliers=body.filter_outliers,
            )
        else:
            figures = build_feature_figures_for_kind(
                obj,
                cell_name=cell_name,
                kind=body.kind,
                cycles=body.cycles,
                reference_cycle=body.reference_cycle,
                use_reference_cycle=body.use_reference_cycle,
                min_dv=body.min_dv,
                min_dq=body.min_dq,
                filter_outliers=body.filter_outliers,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _plotly_response(figures)


@app.post("/api/folder-feature-logavg")
async def folder_feature_logavg(body: FolderLogavgBody) -> JSONResponse:
    """For each .pkl in a folder, compute log⟨|Δ metric|⟩ at one target cycle (vs reference)."""
    folder = Path(body.folder_path)
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")

    pkl_files = list_pkl_files(folder)
    if not pkl_files:
        raise HTTPException(status_code=400, detail=f"No .pkl files in {folder}")

    file_results: list[tuple[str, float, int | None, int | None]] = []
    skipped = 0

    def _one_file(path: Path) -> tuple[str, float, int | None, int | None] | None:
        try:
            obj = load_batteryml_pickle(path)
            triple = compute_logavg_and_lifetime_for_file(
                obj,
                kind=body.kind,
                reference_cycle=body.reference_cycle,
                target_cycle=body.target_cycle,
                use_reference_cycle=body.use_reference_cycle,
                min_dv=body.min_dv,
                min_dq=body.min_dq,
            )
            if triple is None:
                return None
            log_val, eol, max_c = triple
            return (path.name, log_val, eol, max_c)
        except Exception:
            return None

    # Run sequentially in a thread to avoid blocking the event loop.
    for path in pkl_files:
        result = await asyncio.to_thread(_one_file, path)
        if result is None:
            skipped += 1
            continue
        file_results.append(result)

    if not file_results:
        cycle_detail = (
            f"cycle {body.target_cycle} (ref {body.reference_cycle})"
            if body.use_reference_cycle
            else f"cycle {body.target_cycle}"
        )
        raise HTTPException(
            status_code=400,
            detail=(f"No files in {folder.name} produced a valid log-avg "
                    f"value at {cycle_detail}."),
        )

    try:
        figures = build_folder_logavg_figure(
            file_results,
            folder.name,
            kind=body.kind,
            reference_cycle=body.reference_cycle,
            target_cycle=body.target_cycle,
            use_reference_cycle=body.use_reference_cycle,
            filter_outliers=body.filter_outliers,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return _plotly_response(figures)


@app.delete("/api/folder-cache")
async def clear_folder_cache(dir: str) -> dict[str, Any]:
    """Remove disk-cache entries for a folder so its files get re-read on next inspect."""
    cache = load_cycle_cache()
    folders = cache.get("folders", {})
    removed = 0
    if dir in folders:
        removed = len(folders[dir].get("files", {}))
        del folders[dir]
        await asyncio.to_thread(save_cycle_cache, cache)
    return {"cleared": True, "dir": dir, "files_removed": removed}


@app.get("/api/dataset-info")
def api_dataset_info() -> dict[str, Any]:
    """First-paragraph description for each dataset folder from DATA_info/*.md."""
    return {"info": read_dataset_info(DATA_INFO_DIR)}


@app.get("/api/count-folder-files")
async def count_folder_files(dir: str) -> dict[str, Any]:
    """Count PKL files in each immediate subfolder for ETA calculation."""
    d = Path(dir)
    if not d.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    counts: dict[str, int] = {}
    try:
        for sub in sorted(d.iterdir()):
            if sub.is_dir():
                counts[str(sub)] = sum(1 for _ in sub.glob("*.pkl"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"counts": counts, "total": sum(counts.values())}


@app.get("/api/defaults")
def api_defaults() -> dict[str, Any]:
    return {
        "default_data_dir": str(DEFAULT_DATA_DIR),
        "has_default_data_dir": DEFAULT_DATA_DIR.is_dir(),
    }


@app.post("/api/save-screenshot")
async def save_screenshot(body: ScreenshotBody) -> dict[str, Any]:
    import base64, re
    docs_dir = PROJECT_ROOT / "docs" / "screenshots"
    docs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", body.filename)
    if not safe_name.endswith(".png"):
        safe_name += ".png"
    raw = re.sub(r"^data:image/[^;]+;base64,", "", body.data)
    out_path = docs_dir / safe_name
    out_path.write_bytes(base64.b64decode(raw))
    return {"saved": str(out_path)}


# --------------------------------------------------------------------------- #
# ECM (Equivalent Circuit Model) tab
# --------------------------------------------------------------------------- #
from webapp.data_processing import ecm_runner  # noqa: E402
from webapp.data_processing import ocv_runner  # noqa: E402


@app.get("/api/ecm/algorithms")
def ecm_algorithms() -> dict[str, Any]:
    return {
        "algorithms": list(ecm_runner.algorithms()),
        "default_sheet": ecm_runner.DEFAULT_SHEET,
        "extrapolation_methods": ecm_runner.extrapolation_methods(),
    }


@app.post("/api/ecm/pick")
def ecm_pick(body: EcmPickBody) -> dict[str, Any]:
    kind = "folder" if body.kind == "folder" else "xlsx"
    try:
        path = _run_tk_dialog(kind)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"path": path}


@app.get("/api/ecm/scan-folder")
def ecm_scan_folder(dir: str) -> dict[str, Any]:
    folder = Path(dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    files = ecm_runner.find_xlsx_files(folder)
    return {
        "folder": str(folder),
        "count": len(files),
        "files": [{"name": p.name, "path": str(p)} for p in files],
    }


@app.post("/api/ecm/detect-capacity")
async def ecm_detect_capacity(body: EcmCapacityBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    try:
        cap = await asyncio.to_thread(ecm_runner.detect_capacity, path, body.sheet)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Capacity detection failed: {exc}") from exc
    return cap


@app.post("/api/ecm/extract")
async def ecm_extract(body: EcmExtractBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")

    stem = path.stem
    out_dir = ecm_runner.output_dir_for(stem)
    pulse_csv = out_dir / f"{stem}_pulses.csv"
    pulse_png = out_dir / f"{stem}_pulses.png"

    def _work() -> dict[str, Any]:
        cap = ecm_runner.detect_capacity(path, body.sheet, body.pulse_max_seconds)
        extract = ecm_runner.extract_pulses(
            path, pulse_csv, body.sheet, body.pulse_max_seconds, save_plot=pulse_png
        )
        return {"capacity_detected": cap, "extract": extract}

    try:
        result = await asyncio.to_thread(_work)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {exc}") from exc

    result["stem"] = stem
    result["out_dir"] = str(out_dir)
    return result


@app.post("/api/ecm/fit")
async def ecm_fit(body: EcmFitBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    if body.rc_order not in (1, 2):
        raise HTTPException(status_code=400, detail="rc_order must be 1 or 2")

    stem = path.stem
    out_dir = ecm_runner.output_dir_for(stem)
    pulse_csv = out_dir / f"{stem}_pulses.csv"

    def _work() -> dict[str, Any]:
        # Extract on demand if the pulse CSV is missing (reuses Step-2 output).
        if not pulse_csv.exists():
            ecm_runner.extract_pulses(
                path, pulse_csv, body.sheet, body.pulse_max_seconds,
                save_plot=out_dir / f"{stem}_pulses.png",
            )
        # One load -> capacity, ranges and OCV anchors (SOC axis uses the same
        # capacity as the fit). A user value overrides only the SOC capacity.
        capacity = body.capacity
        analysis = ecm_runner.analyze_hppc(
            path, body.sheet, body.pulse_max_seconds, capacity
        )
        cap = analysis["capacity"]
        capacity = capacity or cap.get("capacity")
        capacity_used = round(float(capacity), 4) if capacity else None

        v_limits = ecm_runner.effective_v_limits(cap, body.v_min, body.v_max)
        warnings = ecm_runner.validate_against_bounds(
            cap, v_max=body.v_max, v_min=body.v_min,
            i_chg_max=body.i_chg_max, i_dch_max=body.i_dch_max,
        )
        warnings = warnings + list(analysis.get("ocv_warnings") or [])

        fit = ecm_runner.fit_pulses(
            pulse_csv, out_dir, stem,
            rc_order=body.rc_order, algorithm=body.algorithm, capacity=capacity,
            v_limits=v_limits, zero_soc_method=body.zero_soc_method,
        )
        if fit.get("zero_soc_warning"):
            warnings = warnings + [fit["zero_soc_warning"]]

        ocv = ecm_runner.build_ocv_outputs(
            out_dir, stem, analysis["ocv_anchors"], body.ocv_mode, body.ocv_poly_degree
        )

        summary = ecm_runner.build_summary(
            cap, capacity_used, v_limits, body.nominal_capacity, warnings, ocv
        )
        ecm_runner.save_summary_csv(out_dir, stem, summary)

        return {
            "capacity_detected": cap, "capacity_used": capacity_used,
            "nominal_capacity": body.nominal_capacity,
            "v_limits_used": list(v_limits), "warnings": warnings,
            "fit": fit, "ocv": ocv,
        }

    try:
        result = await asyncio.to_thread(_work)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Fit failed: {exc}") from exc

    return {
        "name": path.name, "stem": stem, "out_dir": str(out_dir),
        "capacity_detected": result["capacity_detected"],
        "capacity_used": result["capacity_used"],
        "nominal_capacity": result["nominal_capacity"],
        "v_limits_used": result["v_limits_used"],
        "warnings": result["warnings"],
        "fit": result["fit"],
        "ocv": result["ocv"],
    }


@app.get("/api/ecm/batch-stream")
async def ecm_batch_stream(
    dir: str,
    rc_order: int = 1,
    algorithm: str = "curve_fit",
    sheet: str = "Record List1",
    capacity: float | None = None,
    pulse_max_seconds: float = 60.0,
    v_max: float | None = None,
    v_min: float | None = None,
    i_chg_max: float | None = None,
    i_dch_max: float | None = None,
    nominal_capacity: float | None = None,
    ocv_mode: str = "both",
    ocv_poly_degree: int = 8,
    zero_soc_method: str = "log_poly2",
) -> StreamingResponse:
    folder = Path(dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    if rc_order not in (1, 2):
        raise HTTPException(status_code=400, detail="rc_order must be 1 or 2")

    files = ecm_runner.find_xlsx_files(folder)

    async def generate():
        total = len(files)
        if total == 0:
            yield f"data: {json.dumps({'done': True, 'total': 0, 'results': [], 'errors': []})}\n\n"
            return

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        full_results: list[dict[str, Any]] = []

        for i, path in enumerate(files):
            yield f"data: {json.dumps({'progress': round(i / total * 100), 'current': i + 1, 'total': total, 'file': path.name})}\n\n"
            try:
                res = await asyncio.to_thread(
                    ecm_runner.process_file,
                    path, rc_order, algorithm, sheet, capacity, pulse_max_seconds,
                    v_max, v_min, i_chg_max, i_dch_max, nominal_capacity,
                    ocv_mode, ocv_poly_degree, zero_soc_method,
                )
                full_results.append(res)
                results.append({
                    "name": res["name"], "stem": res["stem"],
                    "out_dir": res["out_dir"], "warnings": res["warnings"],
                    "capacity_used": res["capacity_used"],
                    "mae": res["fit"]["mae"], "rmse": res["fit"]["rmse"],
                })
            except Exception as exc:
                errors.append({"name": path.name, "error": str(exc)})

        # Pick one already-processed file at random to preview on screen.
        import random
        preview = random.choice(full_results) if full_results else None

        done = {
            "done": True, "total": total,
            "ok_count": len(results), "error_count": len(errors),
            "results": results, "errors": errors,
            "output_root": str(ecm_runner.OUTPUT_ROOT),
            "preview": preview,
        }
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_ECM_MEDIA_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


@app.get("/api/ecm/image")
def ecm_image(path: str, download: int = 0) -> FileResponse:
    """Serve a generated ECM plot (png/svg/pdf), jailed under OUTPUT_ROOT.

    ``download=1`` returns the file as an attachment (for the SVG/PDF buttons).
    """
    p = Path(path).resolve()
    try:
        p.relative_to(ecm_runner.OUTPUT_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Image path outside output folder")
    media_type = _ECM_MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    filename = p.name if download else None
    return FileResponse(str(p), media_type=media_type, filename=filename)


# --------------------------------------------------------------------------- #
# OCV-test tab (separate pipeline; shares the file picker + image jail)
# --------------------------------------------------------------------------- #
@app.post("/api/ocv/detect-capacity")
async def ocv_detect_capacity(body: EcmCapacityBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    try:
        return await asyncio.to_thread(ocv_runner.detect_capacity, path, body.sheet)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OCV detection failed: {exc}") from exc


@app.post("/api/ocv/compute")
async def ocv_compute(body: OcvComputeBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    try:
        return await asyncio.to_thread(
            ocv_runner.compute_ocv, path, body.sheet, body.capacity,
            body.ocv_mode, body.ocv_poly_degree,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OCV computation failed: {exc}") from exc


@app.get("/api/ocv/batch-stream")
async def ocv_batch_stream(
    dir: str,
    sheet: str = "Record List1",
    capacity: float | None = None,
    ocv_mode: str = "both",
    ocv_poly_degree: int = 8,
) -> StreamingResponse:
    folder = Path(dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")

    files = ecm_runner.find_xlsx_files(folder)

    async def generate():
        total = len(files)
        if total == 0:
            yield f"data: {json.dumps({'done': True, 'total': 0, 'results': [], 'errors': []})}\n\n"
            return

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        full_results: list[dict[str, Any]] = []

        for i, path in enumerate(files):
            yield f"data: {json.dumps({'progress': round(i / total * 100), 'current': i + 1, 'total': total, 'file': path.name})}\n\n"
            try:
                res = await asyncio.to_thread(
                    ocv_runner.compute_ocv, path, sheet, capacity, ocv_mode, ocv_poly_degree,
                )
                full_results.append(res)
                ep = res.get("endpoints") or {}
                results.append({
                    "name": res["name"], "stem": res["stem"], "out_dir": res["out_dir"],
                    "capacity_used": res["capacity_used"],
                    "n_discharge_steps": res["n_discharge_steps"],
                    "ocv_100_discharge": ep.get("ocv_100_discharge"),
                    "ocv_0_discharge": ep.get("ocv_0_discharge"),
                    "max_hysteresis_v": ep.get("max_hysteresis_v"),
                    "warnings": res["warnings"],
                })
            except Exception as exc:
                errors.append({"name": path.name, "error": str(exc)})

        import random
        preview = random.choice(full_results) if full_results else None

        done = {
            "done": True, "total": total,
            "ok_count": len(results), "error_count": len(errors),
            "results": results, "errors": errors,
            "output_root": str(ocv_runner.OUTPUT_ROOT),
            "preview": preview,
        }
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# Battery Life Prediction — RUL classification tab
# --------------------------------------------------------------------------- #
from webapp.data_processing import rul_runner  # noqa: E402


@app.post("/api/rul/pick")
def rul_pick(body: RulPickBody) -> dict[str, Any]:
    kind = {"folder": "folder", "ckpt": "folder"}.get(body.kind, "cell")
    try:
        path = _run_tk_dialog(kind)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"path": path}


@app.get("/api/rul/scan-folder")
def rul_scan_folder(dir: str) -> dict[str, Any]:
    folder = Path(dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    files = rul_runner.find_input_files(folder)
    return {
        "folder": str(folder),
        "count": len(files),
        "files": [{"name": p.name, "path": str(p)} for p in files],
    }


@app.get("/api/rul/checkpoint-info")
def rul_checkpoint_info(dir: str) -> dict[str, Any]:
    folder = Path(dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    try:
        return rul_runner.checkpoint_summary(folder)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/rul/inspect")
async def rul_inspect(body: RulInspectBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    try:
        return await asyncio.to_thread(rul_runner.inspect_file, path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc


@app.post("/api/rul/predict")
async def rul_predict(body: RulPredictBody) -> dict[str, Any]:
    path = Path(body.path.strip().strip('"\''))
    ckpt = Path(body.ckpt_dir.strip().strip('"\''))
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    if not ckpt.is_dir():
        raise HTTPException(status_code=400, detail=f"Checkpoint folder not found: {ckpt}")
    try:
        return await asyncio.to_thread(
            rul_runner.process_file, path, ckpt,
            body.has_full_history, body.query_cycle,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Prediction failed: {exc}") from exc


@app.get("/api/rul/batch-stream")
async def rul_batch_stream(
    dir: str,
    ckpt_dir: str,
    has_full_history: bool = True,
    query_cycle: int | None = None,
) -> StreamingResponse:
    folder = Path(dir.strip().strip('"\''))
    ckpt = Path(ckpt_dir.strip().strip('"\''))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    if not ckpt.is_dir():
        raise HTTPException(status_code=400, detail=f"Checkpoint folder not found: {ckpt}")

    files = rul_runner.find_input_files(folder)

    async def generate():
        total = len(files)
        if total == 0:
            yield f"data: {json.dumps({'done': True, 'total': 0, 'results': [], 'errors': []})}\n\n"
            return

        # Load the model once for the whole batch.
        try:
            model, scalers, _ = await asyncio.to_thread(rul_runner.load_model, ckpt)
        except Exception as exc:
            yield f"data: {json.dumps({'done': True, 'total': total, 'fatal': str(exc), 'results': [], 'errors': []})}\n\n"
            return

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        full_results: list[dict[str, Any]] = []

        for i, path in enumerate(files):
            yield f"data: {json.dumps({'progress': round(i / total * 100), 'current': i + 1, 'total': total, 'file': path.name})}\n\n"
            try:
                res = await asyncio.to_thread(
                    rul_runner.process_file, path, ckpt,
                    has_full_history, query_cycle, model, scalers,
                )
                full_results.append(res)
                q = res["query"]
                tr = res.get("trajectory") or {}
                results.append({
                    "name": res["name"], "stem": res["stem"], "out_dir": res["out_dir"],
                    "max_cycle": res["max_cycle"], "query_cycle": q["end_cycle"],
                    "pred_name": q["pred_name"], "confidence": q["confidence"],
                    "true_name": q.get("true_name"),
                    "reached_eol": res["reached_eol"],
                    "accuracy": tr.get("accuracy"),
                })
            except Exception as exc:
                errors.append({"name": path.name, "error": str(exc)})

        import random
        preview = random.choice(full_results) if full_results else None

        done = {
            "done": True, "total": total,
            "ok_count": len(results), "error_count": len(errors),
            "results": results, "errors": errors,
            "output_root": str(rul_runner.OUTPUT_ROOT),
            "preview": preview,
        }
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/rul/image")
def rul_image(path: str, download: int = 0) -> FileResponse:
    """Serve a generated RUL plot (png/svg/pdf), jailed under result/RUL/."""
    p = Path(path).resolve()
    try:
        p.relative_to(rul_runner.OUTPUT_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Image path outside output folder")
    media_type = _ECM_MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    filename = p.name if download else None
    return FileResponse(str(p), media_type=media_type, filename=filename)


if STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(STATIC_DIR), html=True),
        name="static",
    )
