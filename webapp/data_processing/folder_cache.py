"""Folder scanning and cycle-summary cache helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from webapp.config import CACHE_DIR, CACHE_FILE, CACHE_VERSION
from webapp.data_processing.inspection import summarize_batteryml_pkl
from webapp.plot.charts import (
    compute_cell_metrics,
    extract_temperature_from_name,
    figure_payload,
    load_batteryml_pickle,
    make_folder_cycle_bar,
)


def load_cycle_cache() -> dict[str, Any]:
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


def save_cycle_cache(cache: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CACHE_FILE.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2)
    tmp_path.replace(CACHE_FILE)


def file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def cache_file_valid(path: Path, cached_file: dict[str, Any] | None) -> bool:
    if not isinstance(cached_file, dict):
        return False
    try:
        signature = file_signature(path)
    except OSError:
        return False
    return (
        cached_file.get("size") == signature["size"]
        and cached_file.get("mtime_ns") == signature["mtime_ns"]
        and isinstance(cached_file.get("row"), dict)
    )


def pkl_cycle_row(path: Path) -> dict[str, Any]:
    signature = file_signature(path)
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
        "soh_cycles": None, "soh_values": None, "soh_values_smooth": None,
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


def list_pkl_files(folder: Path) -> list[Path]:
    return [
        entry for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower())
        if entry.is_file() and entry.suffix.lower() == ".pkl" and not entry.name.startswith("._")
    ]


def _build_signature_index(folders: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    """Index every cached row by (name, size, mtime_ns) across all folders, so a
    file that reappears at a new path (remounted/moved) is reused without re-reading."""
    index: dict[tuple[str, int, int], dict[str, Any]] = {}
    for folder_cache in folders.values():
        if not isinstance(folder_cache, dict):
            continue
        files = folder_cache.get("files")
        if not isinstance(files, dict):
            continue
        for entry in files.values():
            if not isinstance(entry, dict):
                continue
            row = entry.get("row")
            size = entry.get("size")
            mtime = entry.get("mtime_ns")
            if not isinstance(row, dict) or size is None or mtime is None:
                continue
            name = row.get("name")
            if name is None:
                continue
            index.setdefault((name, int(size), int(mtime)), row)
    return index


def folder_rows_from_cache(folder: Path, *, refresh: bool = False) -> tuple[list[dict[str, Any]], bool]:
    cache = load_cycle_cache()
    folders = cache.setdefault("folders", {})
    folder_key = str(folder)
    folder_cache = folders.get(folder_key) if isinstance(folders.get(folder_key), dict) else {}
    cached_files = folder_cache.get("files") if isinstance(folder_cache.get("files"), dict) else {}

    rows: list[dict[str, Any]] = []
    next_files: dict[str, Any] = {}
    changed = refresh
    used_cache = True
    sig_index: dict[tuple[str, int, int], dict[str, Any]] | None = None

    for path in list_pkl_files(folder):
        file_key = str(path)
        cached_file = cached_files.get(file_key)
        if not refresh and cache_file_valid(path, cached_file):
            row = dict(cached_file["row"])
            row["temperature_c"] = extract_temperature_from_name(path.name)
        else:
            # No exact path hit — try reusing an identical file cached under another
            # path (same name/size/mtime) before falling back to a full read.
            reused = None
            if not refresh:
                try:
                    sig = file_signature(path)
                    if sig_index is None:
                        sig_index = _build_signature_index(folders)
                    candidate = sig_index.get((path.name, sig["size"], sig["mtime_ns"]))
                    if isinstance(candidate, dict):
                        reused = dict(candidate)
                        reused["path"] = str(path)
                        reused["size"] = sig["size"]
                        reused["mtime_ns"] = sig["mtime_ns"]
                        reused["temperature_c"] = extract_temperature_from_name(path.name)
                except OSError:
                    reused = None
            if reused is not None:
                row = reused
                changed = True  # persist the new path mapping (no file was read)
            else:
                row = pkl_cycle_row(path)
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
        save_cycle_cache(cache)

    return rows, used_cache


def folder_payload(folder: Path, *, refresh: bool = False) -> dict[str, Any]:
    rows, used_cache = folder_rows_from_cache(folder, refresh=refresh)
    valid_rows = [row for row in rows if row["max_cycle"] is not None]
    figure = figure_payload(make_folder_cycle_bar(rows, folder.name)) if valid_rows else None
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


def folders_with_pkl_files(root: Path) -> list[Path]:
    folders: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        if any(name.lower().endswith(".pkl") and not name.startswith("._") for name in filenames):
            folders.append(Path(current))
    return sorted(folders, key=lambda p: str(p).lower())
