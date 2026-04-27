# Battery AI Analyzer — Web UI Guide

A browser-based tool for exploring and visualising BatteryML `.pkl` datasets.
All processing runs locally — your data never leaves the machine.

*Design by CuongFX*

---

## Quick start

```bash
# from the project root
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765 --reload
```

Then open **http://localhost:8765** in your browser.

---

## Interface overview

The app is split into two areas:

| Area | Purpose |
|---|---|
| **Left sidebar** | Pick a root folder, browse subfolders, manage cached data |
| **Main workspace** | Two tabs — *General inspection* and *Analyse* |

---

## 1 · Sidebar — Data source

![Sidebar — choose folder](docs/screenshots/01_choose_folder.png)

The top sidebar panel has two buttons side-by-side:

| Button | Action |
|---|---|
| **Choose folder** | Open a system dialog to select the root directory containing your BatteryML `.pkl` subfolders (e.g. `Raw_BML/`). |
| **Start load** | Pre-loads every subfolder under the selected root. A progress bar shows `% complete` and `N / total files`. Once finished, all folders are cached for instant access. |

Below the buttons:

- **Folder path** — current root path
- **Browser tree** — list of subfolders and `.pkl` files. Folders that are already cached show a **blue `DIR`** badge.

### Single-click vs. double-click

| Gesture | Effect on a subfolder |
|---|---|
| **Single click** | Open it in *General inspection* (switches to the General tab) |
| **Double click** | Navigate **into** the folder — stays on the current tab |

This means you can stay in the *Analyse* tab while drilling into another subfolder.

---

## 2 · Sidebar — Folder cache

The cache panel lets you persist scanned folder data across reloads:

| Element | Behaviour |
|---|---|
| **N folders cached** | Live count of folders stored in browser localStorage |
| **Cache all folders** | Walks every visible subfolder and caches it silently |
| **Export JSON** | Downloads `battery_ai_cache.json` — share or back-up your cache |
| **Import JSON** | Loads a previously exported cache file |
| **Clear** | Wipes the localStorage cache (asks for confirmation) |

When you reload the page, cached folders open **instantly** — the bar chart is rebuilt client-side from the saved row data, no server round-trip.

---

## 3 · General Inspection tab

![General inspection](docs/screenshots/02_general_inspection.png)

### Toolbar

- **Folder path** of the selected subfolder (top-right)
- **↻ Reload data** — clears both the JS and disk caches for this folder, then re-reads every PKL fresh from disk

### Dataset info card

Below the toolbar, a card automatically shows the first paragraph of the matching `DATA_info/<FOLDER>_README.md` file — chemistry, format, capacity, temperatures, protocols, etc. Updates whenever you select a different subfolder.

### Stat cards

| Card | Value |
|---|---|
| **Cells in folder** | Total `.pkl` files found |
| **Files plotted** | Files with valid cycle data |
| **Files with issues** | Files that failed to parse (corrupted, truncated, etc.) |

### Loading progress

![Loading progress bar](docs/screenshots/03_progress_bar.png)

When a folder is inspected for the first time, a progress bar shows the current file being processed and `%` complete. After that, the in-session and localStorage caches make repeat visits instant.

### The bar chart — Maximum cycle per file

![Sort controls](docs/screenshots/04_sort_controls.png)

Each bar represents one `.pkl` file:
- **Height** = the maximum cycle number recorded
- **Colour** is a heatmap gradient — pale blue (low) to deep navy (high)
- **Red horizontal line** on each bar marks the cycle at which `Qd` dropped to **80% of its initial value** (industry-standard End-of-Life). Hover the line to read the exact cycle. Cells that never reached 80% have no red mark.

#### Sort controls

Three pill-style buttons in the chart header:

| Button | Effect |
|---|---|
| **Original** | Alphabetical / file order |
| **↓ Low → High** | Ascending by max cycle (default) |
| **↑ High → Low** | Descending by max cycle |

Sorting is instant and client-side. The red EOL markers move with their bars.

#### Bar click → cell detail panel

Click any bar to slide open a panel with eight metrics for that cell:

| Metric | Notes |
|---|---|
| Max charge current (A) | Per-cycle max, IQR-filtered to drop spikes |
| Max discharge current (A) | Per-cycle min, IQR-filtered (negative value, shown red) |
| Max Qd / Min Qd (Ah) | Discharge-capacity bounds across cycles |
| Max Qc / Min Qc (Ah) | Charge-capacity bounds across cycles |
| Qd fade (%) | `(Qd₀ − Qd_last) / Qd₀ × 100` (shown amber) |
| Qc fade (%) | Same for charge capacity (shown amber) |

#### Range sliders + autoscale

Sliders below the chart let you zoom each axis without using the Plotly toolbar. Click **Autoscale** to reset.

#### Export

**SVG** and **PDF** buttons in the card header export the current view.

### Files table

A table at the bottom lists every file with 10 columns:

| File | Cycles | I chg (A) | I dch (A) | Qd max | Qd min | Qc max | Qc min | Qd fade | Qc fade |

Files that failed to parse show `—` in every metric column.

---

## 4 · Analyse tab

![Analyse tab](docs/screenshots/05_deep_dive.png)

For detailed electrochemical analysis of a single cell:

1. Click a **`.pkl` file** (not a folder) in the sidebar to load it. The status indicator at the bottom of the sidebar shows `Loaded: <filename>`.
2. The Analyse tab opens automatically.
3. Choose a **Plot** type:

| Plot type | Description |
|---|---|
| dQ/dV – discharge (3 panels) | Differential capacity vs voltage |
| dV/dQ – discharge (3 panels) | Differential voltage vs charge |
| Qd vs voltage (3 panels) | Discharge capacity vs voltage |
| Qcharge vs voltage (3 panels) | Charge capacity vs voltage |
| dQ/dV – charge (3 panels) | Charge differential capacity |
| dV/dQ – charge (3 panels) | Charge differential voltage |
| Qcmax vs cycle | Max charge capacity fade curve |
| Qdmax vs cycle | Max discharge capacity fade curve |

4. For 3-panel plots, enter the **Cycles** to compare — e.g. `0,1,2` or `all`.
5. Tick **Filter** to clip the axes to the 1–99 percentile (removes extreme outliers).
6. Click **Generate plot**.

### Stats cards

Above the plot, summary cards show total cycles, max cycle index, voltage range, current range, and capacity fade metrics (Qdmax, Qcmax).

---

## 5 · Plotly toolbar shortcuts

| Action | How |
|---|---|
| Pan | Click and drag |
| Zoom box | Drag in zoom mode |
| Zoom scroll | Mouse wheel |
| Reset view | Double-click chart, or use **Autoscale** |
| Export image | **SVG** / **PDF** buttons in the card header |

---

## 6 · Cache & performance

| Layer | Detail |
|---|---|
| **Disk cache** | `webapp/cache/folder_cycle_cache.json` — keyed by file size + mtime so stale entries refresh automatically. Versioned (`CACHE_VERSION`) so adding new metrics invalidates and recomputes. |
| **localStorage cache** | The browser stores `{rows}` per folder. On reload, folders open instantly — the bar chart is rebuilt client-side. |
| **In-session cache** | Switching to a folder you already viewed in the current session uses the in-memory copy. |
| **Streamed progress** | First-time folder loads stream progress over Server-Sent Events file-by-file. |
| **Reload button** | The `↻ Reload data` button next to the folder path nukes both caches for that one folder and re-reads its PKLs fresh. |

---

## 7 · Folder structure expected

```
Root folder/
├── SubfolderA/          ← click this in the sidebar
│   ├── cell_001.pkl
│   ├── cell_002.pkl
│   └── ...
├── SubfolderB/
│   └── ...
└── ...
```

Each `.pkl` must be a BatteryML-format pickle with at minimum a `cycle_data` list whose entries contain `cycle_number`.

If the folder name matches a `*_README.md` file in the project's `DATA_info/` directory (e.g. subfolder `MATR/` ↔ `DATA_info/MATR_README.md`), the dataset description card appears automatically.

---

## 8 · Troubleshooting

| Symptom | Fix |
|---|---|
| Some files show all `—` in the table | Those PKL files are corrupted or 0-byte on disk (e.g. truncated downloads). The web app skips them gracefully. Re-download the offending files, then click **↻ Reload data**. |
| "No cycle data found" | The folder's `.pkl` files don't contain `cycle_data` with numeric `cycle_number` fields. |
| Chart is empty / 0 files plotted | Check the Files table — every row probably failed to parse. |
| "Connection error" during loading | The server may have restarted. Refresh the page. |
| Stale UI / missing buttons | Hard-refresh (`Ctrl+Shift+R` / `Cmd+Shift+R`) to bypass the JS cache. |
| Folder loads slowly every time | Delete `webapp/cache/folder_cycle_cache.json` and click **Start load** to rebuild from scratch. |
| Cache survives across machines | Use **Export JSON** to save your localStorage cache, then **Import JSON** on another machine. |

---

## 9 · Dataset download

This app is designed to work with the **BatteryLife** dataset collection.
For full instructions on how to download the raw `.pkl` files, see the official repository:

**https://github.com/Ruifeng-Tan/BatteryLife**

The dataset README there covers:
- Hugging Face / Zenodo download links
- Folder layout (one subfolder per source: `CALCE/`, `MATR/`, `HUST/`, `HNEI/`, …)
- Per-source README files (mirrored under this project's `DATA_info/`)

Once downloaded, point the **Choose folder** button at the root that contains those subfolders (e.g. `BatteryLife_Raw/`).

---

## 10 · Citation

If you use this tool with the BatteryLife dataset in your research, please cite the original paper:

```bibtex
@inproceedings{10.1145/3711896.3737372,
  author    = {Tan, Ruifeng and Hong, Weixiang and Tang, Jiayue and Lu, Xibin and Ma, Ruijun and Zheng, Xiang and Li, Jia and Huang, Jiaqiang and Zhang, Tong-Yi},
  title     = {BatteryLife: A Comprehensive Dataset and Benchmark for Battery Life Prediction},
  year      = {2025},
  isbn      = {9798400714542},
  publisher = {Association for Computing Machinery},
  address   = {New York, NY, USA},
  url       = {https://doi.org/10.1145/3711896.3737372},
  doi       = {10.1145/3711896.3737372},
  booktitle = {Proceedings of the 31st ACM SIGKDD Conference on Knowledge Discovery and Data Mining V.2},
  pages     = {5789--5800},
  numpages  = {12},
  location  = {Toronto ON, Canada},
  series    = {KDD '25}
}
```

Tan et al., *BatteryLife: A Comprehensive Dataset and Benchmark for Battery Life Prediction*, KDD '25, Toronto, Canada.
#   B a t t e r y _ a n a l y s e _ w e b _ a p p  
 #   B a t t e r y _ a n a l y s e _ w e b _ a p p  
 