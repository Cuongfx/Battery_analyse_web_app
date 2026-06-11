"""RUL prediction runner — loads a checkpoint, runs the vendored classifier on a
BatteryML cell (.pkl or .npz), and writes plots + CSVs under ``result/RUL/``.

Two prediction modes (the caller decides which, via ``has_full_history``):

* **Full history** — slide the 16-cycle input across the whole cell life,
  producing a predicted-class trajectory and (when 80 % EOL is known) the true
  class + accuracy. The "query" cycle just marks which window to headline.
* **Partial** — the file only holds a recent stretch (>= 16 cycles). We feed the
  first 8 + last 8 of what is available as a single window and report just that
  one prediction, with a "may not be 100 % correct" warning.

Inference only — no training code or the ``cma`` dependency is imported.
"""

from __future__ import annotations

import glob
import threading
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import torch

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402

from webapp.config import PROJECT_ROOT  # noqa: E402
from webapp.data_processing import rul_features as rf  # noqa: E402
from webapp.data_processing.rul_model import (  # noqa: E402
    CLASS_COLORS,
    CLASS_NAMES,
    N_CLASSES,
    BatteryRULClassifier,
)

OUTPUT_ROOT = PROJECT_ROOT / "result" / "RUL"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
INPUT_SUFFIXES = (".pkl", ".npz")

# Cache loaded models so a folder batch (or repeated single runs) doesn't reload
# the checkpoint for every file. Keyed by (resolved dir, weight mtime_ns).
_MODEL_CACHE: dict[tuple[str, int], tuple[Any, Any, dict]] = {}
_MODEL_LOCK = threading.Lock()


def output_dir_for(stem: str) -> Path:
    out = OUTPUT_ROOT / stem
    out.mkdir(parents=True, exist_ok=True)
    return out


def find_input_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(folder.rglob("*")):
        if p.name.startswith("._"):
            continue
        if p.is_file() and p.suffix.lower() in INPUT_SUFFIXES:
            files.append(p)
    return files


# ─────────────────────────────────────────────────────────────────────────── #
# Checkpoint loading
# ─────────────────────────────────────────────────────────────────────────── #
def _one(ckpt_dir: Path, pattern: str, label: str) -> Path:
    hits = sorted(glob.glob(str(ckpt_dir / pattern)))
    hits = [h for h in hits if not Path(h).name.startswith("._")]
    if not hits:
        raise FileNotFoundError(f"No {label} ({pattern}) found in {ckpt_dir}")
    return Path(hits[0])


def checkpoint_summary(ckpt_dir: Path) -> dict[str, Any]:
    """Validate a checkpoint folder and report which files were found."""
    weights = _one(ckpt_dir, "best_clf*.pt", "model weights")
    dq = _one(ckpt_dir, "dq_scaler*.pkl", "dQ scaler")
    summ = _one(ckpt_dir, "summary_scaler*.pkl", "summary scaler")
    return {
        "dir": str(ckpt_dir),
        "weights": weights.name,
        "dq_scaler": dq.name,
        "summary_scaler": summ.name,
    }


def load_model(ckpt_dir: Path):
    """Return ``(model, scalers, info)``; cached per checkpoint folder."""
    ckpt_dir = ckpt_dir.resolve()
    weights = _one(ckpt_dir, "best_clf*.pt", "model weights")
    key = (str(ckpt_dir), weights.stat().st_mtime_ns)

    with _MODEL_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        dq_path = _one(ckpt_dir, "dq_scaler*.pkl", "dQ scaler")
        summ_path = _one(ckpt_dir, "summary_scaler*.pkl", "summary scaler")
        dq_scaler = joblib.load(dq_path)
        summary_scaler = joblib.load(summ_path)

        summary_feats = int(getattr(summary_scaler, "n_features_in_", rf._N_SUMMARY))
        model = BatteryRULClassifier(
            summary_feats=summary_feats, gru_layers=2, n_classes=N_CLASSES
        ).to(DEVICE)
        state = torch.load(str(weights), map_location=DEVICE, weights_only=True)
        model.load_state_dict(state)
        model.eval()

        info = {
            "dir": str(ckpt_dir),
            "weights": weights.name,
            "summary_feats": summary_feats,
            "device": DEVICE,
            "n_params": int(sum(p.numel() for p in model.parameters())),
        }
        result = (model, (dq_scaler, summary_scaler), info)
        _MODEL_CACHE[key] = result
        return result


# ─────────────────────────────────────────────────────────────────────────── #
# Inference
# ─────────────────────────────────────────────────────────────────────────── #
@torch.no_grad()
def _predict_start(model, dq_all, summ_all, start, scalers) -> tuple[int, np.ndarray]:
    dq_t, sum_t = rf.make_sample(dq_all, summ_all, start, scalers, DEVICE)
    logits = model(dq_t, sum_t).squeeze(0)
    probs = torch.softmax(logits, dim=-1).cpu().numpy()
    return int(probs.argmax()), probs


def _valid_starts(n_cyc: int) -> range:
    """Window starts s such that [0..N_EARLY) and [s..s+N_RANDOM) both fit."""
    return range(rf.N_EARLY, n_cyc - rf.N_RANDOM + 1)


def _start_for_query(cell: dict, query_end_cycle: int | None) -> int:
    """Map a requested *end cycle number* to the window start array index."""
    n_cyc = cell["n_cyc"]
    last_start = n_cyc - rf.N_RANDOM
    if query_end_cycle is None:
        return last_start
    ci = np.asarray(cell["cycle_index"], dtype=np.int64)
    # The window ends at cycle_index[start + N_RANDOM - 1]; pick the start whose
    # window end is closest to the requested cycle.
    ends = ci[np.array(list(_valid_starts(n_cyc))) + rf.N_RANDOM - 1]
    starts = np.array(list(_valid_starts(n_cyc)))
    j = int(np.argmin(np.abs(ends - int(query_end_cycle))))
    return int(starts[j])


def _window_end_cycle(cell: dict, start: int) -> int:
    ci = np.asarray(cell["cycle_index"], dtype=np.int64)
    return int(ci[start + rf.N_RANDOM - 1])


# ─────────────────────────────────────────────────────────────────────────── #
# Plotting
# ─────────────────────────────────────────────────────────────────────────── #
def _save_vectors(fig, png_path: Path) -> dict[str, str]:
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(png_path.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(png_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return {
        "png": str(png_path),
        "svg": str(png_path.with_suffix(".svg")),
        "pdf": str(png_path.with_suffix(".pdf")),
    }


def _plot_query(out_dir: Path, stem: str, query: dict) -> dict[str, str]:
    probs = np.asarray(query["probs"], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    colors = [CLASS_COLORS[i] if i == query["pred_class"] else "#c4c9d4" for i in range(N_CLASSES)]
    bars = ax.bar(range(N_CLASSES), probs, color=colors, edgecolor="#333", linewidth=0.5)
    for i, b in enumerate(bars):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.01,
                f"{probs[i]:.0%}", ha="center", va="bottom", fontsize=8)
    if query.get("true_class") is not None:
        tc = query["true_class"]
        ax.add_patch(plt.Rectangle((tc - 0.5, 0), 1, 1.0, fill=False,
                                   edgecolor="#111", linewidth=2.0, linestyle="--"))
        ax.text(tc, 1.02, "true", ha="center", va="bottom", fontsize=8, fontweight="bold")
    ax.set_xticks(range(N_CLASSES))
    ax.set_xticklabels(CLASS_NAMES, rotation=15, ha="right", fontsize=8.5)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Probability")
    ax.set_title(f"RUL prediction at cycle {query['end_cycle']} — "
                 f"{query['pred_name']} ({query['confidence']:.0%} confidence)",
                 fontsize=11, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.25)
    return _save_vectors(fig, out_dir / f"{stem}_rul_query.png")


def _plot_trajectory(out_dir: Path, stem: str, traj: dict, query: dict,
                     cell_id: str, reached_eol: bool) -> dict[str, str]:
    cycles = np.asarray(traj["end_cycles"])
    pred = np.asarray(traj["pred_classes"])
    true = np.asarray(traj["true_classes"])
    probs = np.asarray(traj["pred_probs"])

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), gridspec_kw={"height_ratios": [2, 1.6]})
    acc = traj.get("accuracy")
    title = f"RUL trajectory — {cell_id}"
    if reached_eol and acc is not None:
        title += f"  ·  accuracy {acc:.1%}"
    fig.suptitle(title, fontsize=13, fontweight="bold")

    ax = axes[0]
    ax.step(cycles, true, where="post", color="#1565C0", lw=2.4, label="True class", zorder=3)
    ax.step(cycles, pred, where="post", color="#E53935", lw=1.8, ls="--", label="Predicted class", zorder=4)
    correct = pred == true
    ax.fill_between(cycles, -0.4, N_CLASSES - 0.6, where=correct, alpha=0.08, color="green", step="post")
    ax.fill_between(cycles, -0.4, N_CLASSES - 0.6, where=~correct, alpha=0.12, color="red", step="post")
    ax.axvline(query["end_cycle"], color="#6A1B9A", lw=1.8, ls=":", label="Query cycle")
    ax.set_yticks(range(N_CLASSES))
    ax.set_yticklabels(CLASS_NAMES, fontsize=8.5)
    ax.set_ylim(-0.4, N_CLASSES - 0.6)
    ax.set_ylabel("RUL class")
    ax.set_title("Predicted vs true RUL class over cycle life" if reached_eol
                 else "Predicted RUL class over cycle life (true class is provisional — 80% EOL not reached)",
                 fontsize=10)
    ax.legend(fontsize=8.5, loc="upper right")
    ax.grid(True, alpha=0.25)

    ax = axes[1]
    for c in range(N_CLASSES):
        ax.plot(cycles, probs[:, c], color=CLASS_COLORS[c], lw=1.5, label=CLASS_NAMES[c], alpha=0.85)
    ax.axvline(query["end_cycle"], color="#6A1B9A", lw=1.6, ls=":")
    ax.set_ylabel("Softmax probability")
    ax.set_xlabel("Window end cycle")
    ax.set_title("Predicted class probabilities", fontsize=10)
    ax.legend(fontsize=8, ncol=N_CLASSES, loc="upper right")
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, alpha=0.25)

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return _save_vectors(fig, out_dir / f"{stem}_rul_trajectory.png")


# ─────────────────────────────────────────────────────────────────────────── #
# CSV
# ─────────────────────────────────────────────────────────────────────────── #
def _save_csv(out_dir: Path, stem: str, traj: dict | None, query: dict) -> dict[str, str]:
    import csv

    paths: dict[str, str] = {}
    if traj is not None:
        traj_csv = out_dir / f"{stem}_rul_trajectory.csv"
        with traj_csv.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["end_cycle", "pred_class", "pred_name", "true_class"]
                       + [f"prob_{i}" for i in range(N_CLASSES)])
            for i in range(len(traj["end_cycles"])):
                w.writerow([
                    traj["end_cycles"][i], traj["pred_classes"][i],
                    CLASS_NAMES[traj["pred_classes"][i]], traj["true_classes"][i],
                    *[f"{p:.6f}" for p in traj["pred_probs"][i]],
                ])
        paths["trajectory_csv"] = str(traj_csv)

    q_csv = out_dir / f"{stem}_rul_query.csv"
    with q_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["end_cycle", "pred_class", "pred_name", "confidence",
                    "true_class", "true_name", "rul_estimate"]
                   + [f"prob_{i}" for i in range(N_CLASSES)])
        w.writerow([
            query["end_cycle"], query["pred_class"], query["pred_name"],
            f"{query['confidence']:.6f}", query.get("true_class"),
            query.get("true_name"), query.get("rul_estimate"),
            *[f"{p:.6f}" for p in query["probs"]],
        ])
    paths["query_csv"] = str(q_csv)
    return paths


# ─────────────────────────────────────────────────────────────────────────── #
# Single-file pipeline
# ─────────────────────────────────────────────────────────────────────────── #
def inspect_file(path: Path) -> dict[str, Any]:
    """Lightweight load to report cycle count / EOL so the UI can set defaults."""
    cell = rf.load_cell(path)
    enough = cell["n_cyc"] >= rf.N_INPUT
    return {
        "name": path.name,
        "cell_id": cell["cell_id"],
        "n_cyc": cell["n_cyc"],
        "max_cycle": cell["max_cycle"],
        "cycle_life": cell["cycle_life"],
        "reached_eol": cell["reached_eol"],
        "q_retained_at_eol": round(float(cell["q_retained_at_eol"]), 4),
        "min_cycles_required": rf.N_INPUT,
        "enough_cycles": enough,
    }


def process_file(
    path: Path,
    ckpt_dir: Path,
    has_full_history: bool = True,
    query_cycle: int | None = None,
    model=None,
    scalers=None,
) -> dict[str, Any]:
    if model is None or scalers is None:
        model, scalers, _ = load_model(ckpt_dir)

    cell = rf.load_cell(path)
    n_cyc = cell["n_cyc"]
    if n_cyc < rf.N_INPUT:
        raise ValueError(
            f"Only {n_cyc} usable cycles found; the model needs at least "
            f"{rf.N_INPUT} (8 early + 8 recent)."
        )

    dq_all, summ_all = rf.build_cell_arrays(cell)
    warnings: list[str] = []
    cycle_life = cell["cycle_life"]
    reached_eol = cell["reached_eol"]

    # ── Query window ────────────────────────────────────────────────────── #
    if has_full_history:
        start = _start_for_query(cell, query_cycle)
    else:
        start = n_cyc - rf.N_RANDOM  # last available window
        warnings.append(
            "Prediction uses a recent-only window (no verified early-life "
            "cycles). The prediction may not be 100% correct."
        )

    pred_c, probs = _predict_start(model, dq_all, summ_all, start, scalers)
    end_cycle = _window_end_cycle(cell, start)

    true_class = rul_estimate = None
    if reached_eol:
        rul = max(0, cycle_life - end_cycle)
        true_class = rf.rul_to_class(rul)
        rul_estimate = int(rul)
    elif has_full_history:
        warnings.append(
            "This cell has not reached 80% capacity (EOL) yet — the 'true class' "
            "shown is provisional and based only on the available cycles."
        )

    query = {
        "end_cycle": end_cycle,
        "start_index": start,
        "pred_class": pred_c,
        "pred_name": CLASS_NAMES[pred_c],
        "confidence": float(probs[pred_c]),
        "probs": [float(x) for x in probs],
        "true_class": true_class,
        "true_name": CLASS_NAMES[true_class] if true_class is not None else None,
        "rul_estimate": rul_estimate,
        "correct": (bool(true_class == pred_c) if true_class is not None else None),
    }

    # ── Full-life trajectory (full-history mode only) ───────────────────── #
    traj = None
    if has_full_history:
        end_cycles, preds, trues, prob_rows = [], [], [], []
        for s in _valid_starts(n_cyc):
            pc, pr = _predict_start(model, dq_all, summ_all, s, scalers)
            ec = _window_end_cycle(cell, s)
            end_cycles.append(ec)
            preds.append(pc)
            trues.append(rf.rul_to_class(max(0, cycle_life - ec)))
            prob_rows.append(pr)
        preds_a = np.asarray(preds)
        trues_a = np.asarray(trues)
        traj = {
            "end_cycles": [int(x) for x in end_cycles],
            "pred_classes": [int(x) for x in preds_a],
            "true_classes": [int(x) for x in trues_a],
            "pred_probs": np.asarray(prob_rows),
            "accuracy": (float((preds_a == trues_a).mean()) if reached_eol and len(preds_a) else None),
        }

    # ── Outputs ─────────────────────────────────────────────────────────── #
    stem = path.stem
    out_dir = output_dir_for(stem)
    images: dict[str, Any] = {"query": _plot_query(out_dir, stem, query)}
    if traj is not None:
        images["trajectory"] = _plot_trajectory(
            out_dir, stem, traj, query, cell["cell_id"], reached_eol
        )
    csvs = _save_csv(out_dir, stem, traj, query)

    traj_payload = None
    if traj is not None:
        traj_payload = {
            "end_cycles": traj["end_cycles"],
            "pred_classes": traj["pred_classes"],
            "true_classes": traj["true_classes"],
            "accuracy": traj["accuracy"],
        }

    return {
        "name": path.name,
        "stem": stem,
        "out_dir": str(out_dir),
        "cell_id": cell["cell_id"],
        "n_cyc": n_cyc,
        "max_cycle": cell["max_cycle"],
        "cycle_life": cycle_life,
        "reached_eol": reached_eol,
        "q_retained_at_eol": round(float(cell["q_retained_at_eol"]), 4),
        "has_full_history": has_full_history,
        "query": query,
        "trajectory": traj_payload,
        "images": images,
        "csv": csvs,
        "warnings": warnings,
    }
