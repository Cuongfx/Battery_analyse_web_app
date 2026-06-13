"""Read short descriptions from dataset README files."""

from __future__ import annotations

from pathlib import Path


def _first_paragraph(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "|", "-", "*", ">")):
            return line
    return None


def read_dataset_info(*data_info_dirs: Path) -> dict[str, str]:
    """Map dataset key -> first-paragraph description from each *_README.md.

    Accepts several directories; earlier directories take precedence so a curated
    Data_Info/ entry wins over a Raw/READMEs/ fallback for the same key.
    """
    result: dict[str, str] = {}
    for data_info_dir in data_info_dirs:
        if not isinstance(data_info_dir, Path) or not data_info_dir.is_dir():
            continue
        for readme in sorted(data_info_dir.glob("*_README.md")):
            if readme.name.startswith("."):
                continue
            key = readme.stem.replace("_README", "").replace("-", "_")
            if key in result:
                continue  # earlier dir already provided this key
            try:
                para = _first_paragraph(readme.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if para:
                result[key] = para
    return result
