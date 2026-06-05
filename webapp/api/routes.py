"""Local web app: browse a folder of BatteryML .pkl files and plot (Plotly)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from webapp.api.models import (
    FeaturePlotBody,
    FolderLogavgBody,
    FolderPathBody,
    LoadPathBody,
    PlotBody,
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
    """kind: 'folder' or 'file'. Returns selected path or ''."""
    if kind == "folder":
        call = "filedialog.askdirectory(title='Select data folder')"
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


if STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(STATIC_DIR), html=True),
        name="static",
    )
