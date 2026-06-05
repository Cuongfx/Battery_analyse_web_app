"""Read short descriptions from dataset README files."""

from __future__ import annotations

from pathlib import Path


def read_dataset_info(data_info_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not data_info_dir.is_dir():
        return result
    for readme in sorted(data_info_dir.glob("*_README.md")):
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
    return result
