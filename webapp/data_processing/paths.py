"""Filesystem safety helpers for user-selected BatteryML data paths."""

from __future__ import annotations

import os
from pathlib import Path


def allow_root() -> Path | None:
    raw = os.environ.get("BATTERYMIL_PKL_ROOT")
    return Path(raw).resolve() if raw else None


def check_under_root(path: Path) -> None:
    root = allow_root()
    if root is None:
        return
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"Path must be under BATTERYMIL_PKL_ROOT ({root})"
        ) from exc


def safe_resolve_pkl(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    if path.suffix.lower() != ".pkl":
        raise ValueError("Path must be a .pkl file")
    check_under_root(path)
    return path


def safe_resolve_dir(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Not a directory: {path}")
    check_under_root(path)
    return path
