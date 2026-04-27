"""Local web app: browse a folder of BatteryML .pkl files and plot (Plotly)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webapp.inspect_pkl import compute_data_meta, summarize_batteryml_pkl
from webapp.plotly_charts import (
    build_figures_for_kind,
    capacity_fade_summary,
    compute_cell_metrics,
    extract_temperature_from_name,
    load_batteryml_pickle,
    make_folder_cycle_bar,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "DATA" / "BatteryML"
CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_FILE = CACHE_DIR / "folder_cycle_cache.json"
CACHE_VERSION = 3
DATA_INFO_DIR = PROJECT_ROOT / "DATA_info"

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

_sessions: dict[str, dict[str, Any]] = {}


def _allow_root() -> Path | None:
    raw = os.environ.get("BATTERYMIL_PKL_ROOT")
    return Path(raw).resolve() if raw else None


def _check_under_root(p: Path) -> None:
    root = _allow_root()
    if root is None:
        return
    try:
        p.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"Path must be under BATTERYMIL_PKL_ROOT ({root})"
        ) from exc


def _safe_resolve_pkl(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Not a file: {p}")
    if p.suffix.lower() != ".pkl":
        raise ValueError("Path must be a .pkl file")
    _check_under_root(p)
    return p


def _safe_resolve_dir(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {p}")
    _check_under_root(p)
    return p


class LoadPathBody(BaseModel):
    path: str = Field(..., description="Path to a .pkl on the server machine.")


class FolderPathBody(BaseModel):
    path: str = Field(..., description="Path to a folder on the server machine.")


class PlotBody(BaseModel):
    kind: str
    cycles: str | None = None
    min_dv: float = 1e-4
    min_dq: float = 1e-5
    filter_outliers: bool = False


def _get_session(session_id: str) -> dict[str, Any]:
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return s


def _plotly_response(figures: list[Any]) -> JSONResponse:
    payload = {
        "figures": [
            _figure_payload(fig)
            for fig in figures
        ]
    }
    return JSONResponse(payload)


def _figure_payload(fig: Any) -> dict[str, Any]:
    return json.loads(fig.to_json())


def _data_meta(obj: dict[str, Any]) -> dict[str, Any]:
    meta = compute_data_meta(obj)
    meta.update(capacity_fade_summary(obj))
    return meta


def _load_cycle_cache() -> dict[str, Any]:
    if not CACHE_FILE.is_file():
        return {"version": CACHE_VERSION, "folders": {}}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "folders": {}}
    if data.get("version") != CACHE_VERSION or not isinstance(data.get("folders"), dict):
        return {"version": CACHE_VERSION, "folders": {}}
    return data


def _save_cycle_cache(cache: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CACHE_FILE.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2)
    tmp_path.replace(CACHE_FILE)


def _file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _cache_file_valid(path: Path, cached_file: dict[str, Any] | None) -> bool:
    if not isinstance(cached_file, dict):
        return False
    try:
        signature = _file_signature(path)
    except OSError:
        return False
    return (
        cached_file.get("size") == signature["size"]
        and cached_file.get("mtime_ns") == signature["mtime_ns"]
        and isinstance(cached_file.get("row"), dict)
    )


def _pkl_cycle_row(path: Path) -> dict[str, Any]:
    signature = _file_signature(path)
    row: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "size": signature["size"],
        "mtime_ns": signature["mtime_ns"],
        "cycle_count": None,
        "max_cycle": None,
        "max_charge_current": None,
        "max_discharge_current": None,
        "qd_max": None, "qd_min": None, "qd_fade_pct": None,
        "qc_max": None, "qc_min": None, "qc_fade_pct": None,
        "error": None,
    }
    try:
        obj = load_batteryml_pickle(path)
        summary = summarize_batteryml_pkl(obj)
        cycle_count = int(summary.get("cycle_count") or 0)
        cycle_data = obj.get("cycle_data")
        cycle_numbers: list[int] = []
        if isinstance(cycle_data, list):
            for cycle in cycle_data:
                if not isinstance(cycle, dict):
                    continue
                raw = cycle.get("cycle_number")
                try:
                    if raw is not None:
                        cycle_numbers.append(int(raw))
                except (TypeError, ValueError):
                    continue
        row["cycle_count"] = cycle_count
        row["max_cycle"] = max(cycle_numbers) if cycle_numbers else (cycle_count if cycle_count > 0 else None)
        row.update(compute_cell_metrics(obj))
    except Exception as exc:
        row["error"] = str(exc)
    row["temperature_c"] = extract_temperature_from_name(path.name)
    return row


def _list_pkl_files(folder: Path) -> list[Path]:
    return [
        entry for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower())
        if entry.is_file() and entry.suffix.lower() == ".pkl" and not entry.name.startswith("._")
    ]


def _folder_rows_from_cache(folder: Path, *, refresh: bool = False) -> tuple[list[dict[str, Any]], bool]:
    cache = _load_cycle_cache()
    folders = cache.setdefault("folders", {})
    folder_key = str(folder)
    folder_cache = folders.get(folder_key) if isinstance(folders.get(folder_key), dict) else {}
    cached_files = folder_cache.get("files") if isinstance(folder_cache.get("files"), dict) else {}

    rows: list[dict[str, Any]] = []
    next_files: dict[str, Any] = {}
    changed = refresh
    used_cache = True

    for path in _list_pkl_files(folder):
        file_key = str(path)
        cached_file = cached_files.get(file_key)
        if not refresh and _cache_file_valid(path, cached_file):
            row = dict(cached_file["row"])
            row["temperature_c"] = extract_temperature_from_name(path.name)
        else:
            row = _pkl_cycle_row(path)
            used_cache = False
            changed = True
        rows.append(row)
        next_files[file_key] = {
            "size": row.get("size"),
            "mtime_ns": row.get("mtime_ns"),
            "row": row,
        }

    if set(cached_files.keys()) != set(next_files.keys()):
        changed = True

    if changed:
        folders[folder_key] = {"path": folder_key, "files": next_files}
        _save_cycle_cache(cache)

    return rows, used_cache


def _folder_payload(folder: Path, *, refresh: bool = False) -> dict[str, Any]:
    rows, used_cache = _folder_rows_from_cache(folder, refresh=refresh)
    valid_rows = [row for row in rows if row["max_cycle"] is not None]
    figure = _figure_payload(make_folder_cycle_bar(rows, folder.name)) if valid_rows else None
    return {
        "path": str(folder),
        "name": folder.name,
        "file_count": len(rows),
        "valid_file_count": len(valid_rows),
        "failed_file_count": len(rows) - len(valid_rows),
        "rows": rows,
        "figure": figure,
        "from_cache": used_cache,
    }


def _folders_with_pkl_files(root: Path) -> list[Path]:
    folders: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        if any(name.lower().endswith(".pkl") and not name.startswith("._") for name in filenames):
            folders.append(Path(current))
    return sorted(folders, key=lambda p: str(p).lower())


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
        d = _safe_resolve_dir(dir)
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
    root = _allow_root()
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
        d = _safe_resolve_dir(dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    return await asyncio.to_thread(_folder_payload, d)


@app.get("/api/folder-inspect-stream")
async def folder_inspect_stream(dir: str) -> StreamingResponse:
    try:
        d = _safe_resolve_dir(dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    async def generate():
        files = _list_pkl_files(d)
        total = len(files)

        if total == 0:
            payload: dict[str, Any] = {
                "done": True, "path": str(d), "name": d.name,
                "file_count": 0, "valid_file_count": 0, "failed_file_count": 0,
                "rows": [], "figure": None, "from_cache": True,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

        cache = _load_cycle_cache()
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
            if _cache_file_valid(path, cached_file):
                row = dict(cached_file["row"])
            else:
                row = await asyncio.to_thread(_pkl_cycle_row, path)
                changed = True
            rows.append(row)
            next_files[file_key] = {"size": row.get("size"), "mtime_ns": row.get("mtime_ns"), "row": row}

            pct = round((i + 1) / total * 100)
            yield f"data: {json.dumps({'progress': pct, 'current': i + 1, 'total': total, 'file': path.name})}\n\n"

        if set(cached_files.keys()) != set(next_files.keys()) or changed:
            folders[folder_key] = {"path": folder_key, "files": next_files}
            await asyncio.to_thread(_save_cycle_cache, cache)

        valid_rows = [r for r in rows if r["max_cycle"] is not None]
        try:
            figure = _figure_payload(make_folder_cycle_bar(rows, d.name)) if valid_rows else None
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
        root = _safe_resolve_dir(body.path.strip().strip('"\''))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    folders = _folders_with_pkl_files(root)
    files_seen = 0
    cache_hits = 0
    errors = 0
    for folder in folders:
        payload = _folder_payload(folder)
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
        path = _safe_resolve_pkl(body.path.strip().strip('"\''))
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

    sid = str(uuid.uuid4())
    _sessions[sid] = {"path": path, "name": path.name, "obj": obj}
    return {
        "session_id": sid,
        "name": path.name,
        "path": str(path),
        "summary": summarize_batteryml_pkl(obj),
        "meta": _data_meta(obj),
    }


@app.post("/api/session/{session_id}/plot")
def plot_session(session_id: str, body: PlotBody) -> JSONResponse:
    s = _get_session(session_id)
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


@app.delete("/api/folder-cache")
async def clear_folder_cache(dir: str) -> dict[str, Any]:
    """Remove disk-cache entries for a folder so its files get re-read on next inspect."""
    cache = _load_cycle_cache()
    folders = cache.get("folders", {})
    removed = 0
    if dir in folders:
        removed = len(folders[dir].get("files", {}))
        del folders[dir]
        await asyncio.to_thread(_save_cycle_cache, cache)
    return {"cleared": True, "dir": dir, "files_removed": removed}


@app.get("/api/dataset-info")
def api_dataset_info() -> dict[str, Any]:
    """First-paragraph description for each dataset folder from DATA_info/*.md."""
    result: dict[str, str] = {}
    if not DATA_INFO_DIR.is_dir():
        return {"info": result}
    for readme in sorted(DATA_INFO_DIR.glob("*_README.md")):
        if readme.name.startswith("."):
            continue
        key = readme.stem.replace("_README", "").replace("-", "_")
        try:
            text = readme.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "|", "-", "*", ">")):
                    result[key] = line
                    break
        except Exception:
            continue
    return {"info": result}


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


class ScreenshotBody(BaseModel):
    filename: str
    data: str  # base64-encoded PNG

@app.post("/api/save-screenshot")
async def save_screenshot(body: ScreenshotBody) -> dict[str, Any]:
    import base64, re
    docs_dir = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
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
