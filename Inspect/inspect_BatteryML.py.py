"""
How to run:

python inspect_content_raw_npz_choose_export_cycle.py --content-dir "F:\\Working\\Battery_AI\\content\\content_discharge"

This version lets you choose a .npz file and export one or more cycles to XLSX.
"""

import argparse
from pathlib import Path
import re

import numpy as np
import pandas as pd

MATRIX_KEYS = ("time_s", "current", "qd", "temperature", "voltage")
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def format_scalar(value) -> str:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def choose_from_list(items: list[str], title: str) -> str:
    while True:
        print(f"\n{title}")
        for idx, item in enumerate(items, start=1):
            print(f"{idx:3d}. {item}")
        raw = input("\nEnter number (or 'q' to quit): ").strip().lower()
        if raw == "q":
            raise KeyboardInterrupt("User quit.")
        if raw.isdigit():
            picked = int(raw)
            if 1 <= picked <= len(items):
                return items[picked - 1]
        print("Invalid choice.")


def list_subfolders(content_dir: Path) -> list[Path]:
    return sorted(
        p for p in content_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("._")
    )


def list_npz_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.glob("*.npz")
        if p.is_file() and not p.name.startswith("._")
    )


def list_pkl_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.glob("*.pkl")
        if p.is_file() and not p.name.startswith("._")
    )


def resolve_existing_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"Path not found: {resolved}")

    search_roots = [Path.cwd(), PROJECT_ROOT]
    checked: list[Path] = []
    for root in search_roots:
        resolved = (root / candidate).resolve()
        checked.append(resolved)
        if resolved.exists():
            return resolved

    checked_text = "\n".join(str(path) for path in checked)
    raise FileNotFoundError(f"Path not found. Checked:\n{checked_text}")


def resolve_root_folder(args: argparse.Namespace) -> Path:
    if args.content_dir:
        content_dir = resolve_existing_path(args.content_dir)
    else:
        raw = input("Enter root folder path: ").strip().strip('"')
        content_dir = resolve_existing_path(raw)

    if not content_dir.is_dir():
        raise FileNotFoundError(f"Root folder not found: {content_dir}")
    return content_dir


def choose_subfolder(content_dir: Path) -> Path:
    subfolders = list_subfolders(content_dir)
    if not subfolders:
        raise FileNotFoundError(f"No subfolders found in: {content_dir}")

    labels = [p.name for p in subfolders]
    chosen_name = choose_from_list(labels, f"Choose a subfolder in {content_dir}:")
    return content_dir / chosen_name


def choose_npz_file(folder: Path) -> Path:
    files = list_npz_files(folder)
    if not files:
        pkl_files = list_pkl_files(folder)
        if pkl_files:
            raise FileNotFoundError(
                f"No .npz files found in: {folder}\n"
                f"Found {len(pkl_files)} .pkl files instead. "
                "This folder looks like a pickle dataset, so use "
                "'inspect_pkl_dataset_choose_export_cycle.py' for it."
            )
        raise FileNotFoundError(f"No .npz files found in: {folder}")

    labels = [p.name for p in files]
    chosen_name = choose_from_list(labels, f"Choose one .npz file in {folder.name}:")
    return folder / chosen_name


def get_cycle_count(data: np.lib.npyio.NpzFile) -> int:
    if "seq_len" not in data.files:
        raise KeyError("'seq_len' not found in file")

    seq_len = np.asarray(data["seq_len"], dtype=np.int32).reshape(-1)
    n_cycles = seq_len.size
    if n_cycles == 0:
        raise ValueError("No cycles found in file")
    return n_cycles


def parse_cycle_indices(raw: str, n_cycles: int) -> list[int]:
    normalized = re.sub(r"[.;\s]+", ",", raw.strip())
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if not parts:
        raise ValueError("No cycle indices were provided.")

    values: list[int] = []
    seen: set[int] = set()
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"Invalid cycle index: {part}")
        idx = int(part)
        if not 0 <= idx < n_cycles:
            raise IndexError(f"Cycle out of range: {idx} (valid: 0..{n_cycles - 1})")
        if idx not in seen:
            values.append(idx)
            seen.add(idx)
    return values


def resolve_export_cycle_indices(
    data: np.lib.npyio.NpzFile,
    requested_export_cycles: str | None,
    requested_export_cycle: int | None,
) -> list[int]:
    n_cycles = get_cycle_count(data)

    if requested_export_cycles:
        return parse_cycle_indices(requested_export_cycles, n_cycles)

    if requested_export_cycle is not None:
        if 0 <= requested_export_cycle < n_cycles:
            return [requested_export_cycle]
        raise IndexError(
            f"--export-cycle out of range: {requested_export_cycle} (valid: 0..{n_cycles - 1})"
        )

    while True:
        raw = input(
            "Choose cycle index/indices to export to XLSX "
            "(example: 1,4,5,10 | '.' ';' and spaces also work): "
        ).strip()
        try:
            return parse_cycle_indices(raw, n_cycles)
        except (ValueError, IndexError) as exc:
            print(exc)


def build_cycle_dataframe(data: np.lib.npyio.NpzFile, cycle_idx: int) -> pd.DataFrame:
    seq_len = np.asarray(data["seq_len"], dtype=np.int32).reshape(-1)
    valid_len = int(seq_len[cycle_idx])

    arrays: dict[str, np.ndarray] = {}
    for key in MATRIX_KEYS:
        if key not in data.files:
            arrays[key] = np.full(valid_len, np.nan, dtype=np.float32)
            continue

        matrix = np.asarray(data[key], dtype=np.float32)
        if matrix.ndim != 2 or cycle_idx >= matrix.shape[0]:
            arrays[key] = np.full(valid_len, np.nan, dtype=np.float32)
            continue

        row = np.asarray(matrix[cycle_idx], dtype=np.float32)
        if row.ndim != 1:
            row = row.reshape(-1)
        arr = row[: min(valid_len, row.size)]
        if arr.size < valid_len:
            padded = np.full(valid_len, np.nan, dtype=np.float32)
            padded[:arr.size] = arr
            arr = padded
        arrays[key] = arr

    df = pd.DataFrame({
        "index": np.arange(valid_len, dtype=np.int32),
        "time_s": arrays["time_s"],
        "current": arrays["current"],
        "voltage": arrays["voltage"],
        "qd": arrays["qd"],
        "temperature": arrays["temperature"],
    })

    if "cycle_index" in data.files:
        ci = np.asarray(data["cycle_index"]).reshape(-1)
        if cycle_idx < ci.size:
            df.insert(1, "cycle_number", ci[cycle_idx])

    df.insert(1, "cycle_idx", cycle_idx)
    return df


def export_cycle_to_xlsx(data: np.lib.npyio.NpzFile, npz_path: Path, cycle_idx: int) -> Path:
    df = build_cycle_dataframe(data, cycle_idx)

    script_dir = Path(__file__).resolve().parent
    output_name = f"{npz_path.stem}_cycle_{cycle_idx}.xlsx"
    output_path = script_dir / output_name

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="cycle_data", index=False)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Choose a .npz file from a root folder and export one or more cycles to XLSX."
        )
    )
    parser.add_argument("--content-dir", default=None, help="Root folder containing subfolders of .npz files")
    parser.add_argument("--export-cycle", type=int, default=None, help="Optional cycle index to export to XLSX")
    parser.add_argument(
        "--export-cycles",
        default=None,
        help="Optional comma-separated cycle indices to export, for example: 1,4,5,10",
    )
    args = parser.parse_args()

    root_folder = resolve_root_folder(args)
    chosen_subfolder = choose_subfolder(root_folder)
    npz_path = choose_npz_file(chosen_subfolder)

    with np.load(npz_path, allow_pickle=True) as data:
        print(f"\nLoaded file: {npz_path}")
        print(f"Cycles available: 0 to {get_cycle_count(data) - 1}")

        export_cycle_indices = resolve_export_cycle_indices(
            data=data,
            requested_export_cycles=args.export_cycles,
            requested_export_cycle=args.export_cycle,
        )
        exported_paths: list[Path] = []
        for export_cycle_idx in export_cycle_indices:
            exported_paths.append(export_cycle_to_xlsx(data, npz_path, export_cycle_idx))

        print("\nXLSX export completed:")
        for export_cycle_idx, output_path in zip(export_cycle_indices, exported_paths):
            print(f"  cycle {export_cycle_idx}: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
