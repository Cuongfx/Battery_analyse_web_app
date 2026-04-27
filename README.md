# Battery AI Analyzer — Web UI Guide

A browser-based tool for exploring and visualising **BatteryML `.pkl` datasets**.
All processing runs locally — your data never leaves the machine.

*Design by CuongFX*

---

## UI Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SIDEBAR                  │  MAIN WORKSPACE                             │
│                           │                                             │
│  ┌─────────────────────┐  │  [ General inspection ] [ Analyse ]         │
│  │  Battery AI Analyzer│  │                                             │
│  │  BatteryML PKL      │  │  Folder overview > General inspection       │
│  │  browser & viewer   │  │  ─────────────────────────────────────────  │
│  └─────────────────────┘  │  DATASET INFO  ←── from README.md           │
│                           │  ─────────────────────────────────────────  │
│  DATA SOURCE              │  Cells │ Plotted │ Issues │ Temp            │
│  [Choose folder][Start ▶] │  ─────────────────────────────────────────  │
│  /path/to/root            │  ╔══════════════════════════════════════╗   │
│  ┌──────────────────────┐ │  ║  Maximum cycle per file – HUST       ║   │
│  │ DIR  SubfolderA      │ │  ║  (bar chart)                         ║   │
│  │ DIR  SubfolderB  ●   │ │  ╚══════════════════════════════════════╝   │
│  │ PKL  cell_001.pkl    │ │  ─────────────────────────────────────────  │
│  └──────────────────────┘ │  Files table with temperature filter        │
│                           │                                             │
│  FOLDER CACHE             │                                             │
│  3 folders cached         │                                             │
│  [Cache all] [Export]...  │                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# from the project root
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765 --reload
```

Then open **http://localhost:8765** in your browser.

**Requirements:** Python 3.10+, `fastapi`, `uvicorn`, `plotly`, `numpy`, `scipy`, `pandas` (see `requirements.txt`)

---

## 1 · Sidebar — Data Source

### Choose Folder + Start Load

Two buttons sit side-by-side at the top of the sidebar:

| Button | Action |
|---|---|
| **Choose folder** | Open a system folder-picker dialog to select the root directory that contains your BatteryML `.pkl` subfolders (e.g. `Raw_BML/` or `BatteryLife/`). The folder path is remembered across browser reloads. |
| **Start load** | Pre-loads **all** subfolders under the selected root. A progress bar shows `% complete` and `N / total files`. Once finished, everything is cached for instant access. |

> **Tip:** After the first "Start load", you never need to click it again for the same folder. The app auto-restores the last folder and the last selected subfolder on every reload.

### Browser Tree

After choosing a folder, the sidebar lists subfolders and `.pkl` files:

- **Blue `DIR` badge** — subfolder (not yet cached or has no data)
- **Blue `DIR` badge with a filled dot (●)** — subfolder is already cached (instant load)
- **`PKL` badge** — individual battery file (load into Analyse tab)

#### Navigation gestures

| Gesture | Effect |
|---|---|
| **Single click on folder** | Open it in *General Inspection* (switches to the General tab) |
| **Double click on folder** | Navigate **into** the folder to see its PKL files — stays on the current tab |
| **Single click on PKL** | Load that file into the *Analyse* tab |

---

## 2 · Sidebar — Folder Cache

The cache panel lets you manage scanned folder data:

| Element | Behaviour |
|---|---|
| **N folders cached** | Live count of folders stored in browser localStorage |
| **Cache all folders** | Pre-loads every subfolder silently in the background |
| **Export JSON** | Downloads `battery_ai_cache.json` — back-up or share your cache |
| **Import JSON** | Restores a previously exported cache file (merges with existing) |
| **Clear** | Wipes the entire localStorage cache |

When you reload the page, all previously cached folders open **instantly** — the bar chart is rebuilt client-side from saved row data with no server round-trip.

---

## 3 · General Inspection Tab

### Toolbar

- **Selected subfolder path** shown top-right
- **↻ Reload data** — clears both the in-memory and disk caches for this folder, then re-reads every PKL file fresh from disk. Use this when files have been updated.

### Dataset Info Card

If a matching `DATA_info/<FOLDER>_README.md` file exists in the project, its first paragraph is shown in a card above the stats — describing the battery chemistry, format, nominal capacity, cycling protocol, etc.

### Stat Cards

Four cards summarise the selected folder at a glance:

| Card | Value |
|---|---|
| **Cells in folder** | Total `.pkl` files found |
| **Files plotted** | Files with valid cycle data |
| **Files with issues** | Files that failed to parse (corrupted, truncated, etc.) |
| **Test temperature** | One blue pill badge per unique temperature (e.g. `−5 °C` `25 °C` `45 °C`). Extracted from filenames first; falls back to the Dataset Info text if no temperature is in filenames. Shown as `N/A` if unknown. |

### Bar Chart — Maximum Cycle per File

Each bar represents one `.pkl` file:

- **Height** = the maximum cycle number recorded
- **Colour** = a heatmap gradient from pale blue (few cycles) to deep navy (many cycles)
- **Default sort** = Low → High (ascending)

#### Sort Controls

Three pill buttons in the chart header control the sort order:

| Button | Effect |
|---|---|
| **Original** | Alphabetical / file order |
| **↓ Low → High** | Ascending by max cycle (default) |
| **↑ High → Low** | Descending by max cycle |

Sorting is instant and client-side.

#### EOL Marker (End-of-Life @ 80% Qd)

The cycle at which a cell's discharge capacity drops to **80% of its initial value** is computed automatically. To see it:

1. **Click any bar** — a red horizontal line appears **inside** that bar at the EOL cycle height.
2. The cell detail panel (below the chart) also shows `EOL @ 80% Qd: cycle N`.
3. **Click the same bar again** to hide the marker (toggle).
4. Clicking a different bar moves the marker there.
5. Changing the sort order clears the marker.

Cells that never reached 80% capacity fade have no EOL data and show no marker.

#### Bar Click → Cell Detail Panel

Clicking any bar opens a panel with eight metrics for that cell:

| Metric | Notes |
|---|---|
| Max charge current (A) | Per-cycle max, IQR-filtered to remove spike outliers |
| Max discharge current (A) | Per-cycle min, IQR-filtered (negative value, shown in red) |
| Max Qd / Min Qd (Ah) | Discharge capacity bounds across all cycles |
| Max Qc / Min Qc (Ah) | Charge capacity bounds across all cycles |
| Qd fade (%) | `(Qd₀ − Qd_last) / Qd₀ × 100` |
| Qc fade (%) | Same for charge capacity |
| EOL @ 80% Qd | The first cycle where Qd ≤ 80% of initial (if reached) |

#### Axis Editor + Export

- **Range sliders** below the chart let you zoom each axis manually.
- **Autoscale** button resets both axes.
- **SVG** and **PDF** buttons in the chart header export the current view.

### Files Table

A table below the chart lists every file with the following columns:

`File` · `Temp (°C)` · `Cycles` · `I chg (A)` · `I dch (A)` · `Qd max` · `Qd min` · `Qc max` · `Qc min` · `Qd fade` · `Qc fade`

#### Temperature Filter

When the folder contains cells tested at **two or more temperatures**, a **Temperature** dropdown appears in the table header. Tick/untick temperatures to show only the rows you want. This is useful for datasets like MICH-EXP (−5 °C / 25 °C / 45 °C).

---

## 4 · Analyse Tab

For detailed electrochemical analysis of a single cell:

1. **Double-click a `.pkl` file** in the sidebar (or single-click to load it).  
   The status indicator at the bottom of the sidebar shows `Loaded: <filename>`.
2. Switch to the **Analyse** tab (or stay there — double-clicking a PKL won't switch tabs).
3. Choose a **Plot** type from the dropdown:

| Plot type | Description |
|---|---|
| dQ/dV – discharge (3 panels) | Differential capacity vs voltage, discharge half-cycle |
| dV/dQ – discharge (3 panels) | Differential voltage vs capacity, discharge |
| Qd vs voltage (3 panels) | Discharge capacity vs cell voltage |
| Qcharge vs voltage (3 panels) | Charge capacity vs cell voltage |
| dQ/dV – charge (3 panels) | dQ/dV on the charge half-cycle |
| dV/dQ – charge (3 panels) | dV/dQ on the charge half-cycle |
| Qcmax vs cycle | Max charge capacity fade curve over all cycles |
| Qdmax vs cycle | Max discharge capacity fade curve over all cycles |

4. For 3-panel plots, enter the **Cycles** to compare — e.g. `0,1,2` or `all`.
5. Tick **Filter** to clip axes to the 1–99 percentile (removes extreme outliers).
6. Click **Generate plot**.

### Stats Cards (Analyse tab)

Above the plot, summary cards show: total cycles, max cycle index, voltage range, current range, and first/last capacity for Qdmax and Qcmax.

---

## 5 · Plotly Toolbar Shortcuts

| Action | How |
|---|---|
| Pan | Click and drag |
| Zoom box | Drag while in zoom-box mode |
| Zoom scroll | Mouse wheel |
| Reset view | Double-click chart, or **Autoscale** |
| Export image | **SVG** / **PDF** buttons in the card header |
| Download PNG | Camera icon in the Plotly toolbar |

---

## 6 · Cache Architecture

| Layer | Where | What |
|---|---|---|
| **Server disk cache** | `webapp/cache/folder_cycle_cache.json` | Full per-file metrics keyed by path + file size + mtime. Version-stamped — adding new metrics auto-invalidates. |
| **Browser localStorage** | Key `batteryAi.folders.v2` | Row data mirrored in-browser. Bar charts rebuilt client-side on reload — zero server calls. |
| **Last selected folder** | Key `batteryAi.lastSelected` | The subfolder that was open when you left. Auto-restored on next load. |
| **Last root folder** | Key `batteryAi.lastRootDir` | Which root folder was browsed. Auto-browsed on next load. |
| **In-session cache** | JS `Map` | Switching folders in the same session uses the in-memory copy — no network. |

---

## 7 · Expected Folder Structure

```
Root folder/             ← choose this with "Choose folder"
├── HUST/
│   ├── HUST_cell_001.pkl
│   ├── HUST_cell_002.pkl
│   └── ...
├── MATR/
│   └── ...
├── CALB/
│   └── ...
└── ...
```

Each `.pkl` must be a BatteryML-format pickle with at minimum a `cycle_data` list whose entries contain a `cycle_number` field.

If a subfolder name matches a `*_README.md` file under `DATA_info/` (e.g. `HUST/` ↔ `DATA_info/HUST_README.md`), the dataset description card appears automatically.

---

## 8 · Temperature Detection

The app automatically determines the **test ambient temperature** of each cell:

1. **Filename patterns** — searches for chemistry/format keywords followed by a temperature:
   - `NMC_25C`, `pouch_-5C`, `18650_30C`, `LFP_45C` → extracts `25`, `−5`, `30`, `45 °C`
   - Dataset-prefix style: `CALB_0_B182` → `0 °C`, `CALB_35_B247` → `35 °C`
   - Cell-ID tokens like `02C`, `05C` are **not** matched (avoids false positives)

2. **Dataset info text fallback** — if no temperature is in the filename, the README text is parsed for phrases like *"30 degrees Celsius"*, *"temperature of 25°C"*, etc.

3. **N/A** — shown when no temperature can be determined.

---

## 9 · Troubleshooting

| Symptom | Fix |
|---|---|
| Some files show all `—` in the table | Those PKL files are corrupted or truncated. Re-download them, then click **↻ Reload data**. |
| "No cycle data found" | The PKL files lack a `cycle_data` list with `cycle_number` fields. |
| Chart is empty / 0 files plotted | Check the Files table — every row probably failed to parse. |
| "Connection error" during loading | The server may have restarted. Refresh the page. |
| Stale UI / missing buttons | Hard-refresh (`Ctrl+Shift+R` / `Cmd+Shift+R`) to bypass the JS cache. |
| Temperature shows wrong value | Clear the localStorage cache (Folder cache → **Clear**) and reload to force re-detection. |
| Folder loads slowly every time | Delete `webapp/cache/folder_cycle_cache.json` and click **Start load** to rebuild. |
| Cache works on one machine but not another | Use **Export JSON** to save your cache, then **Import JSON** on the other machine. |

---

## 10 · Dataset Download

This app is designed to work with the **BatteryLife** dataset collection.  
For download instructions see the official repository:

**https://github.com/Ruifeng-Tan/BatteryLife**

The repository covers:
- Hugging Face / Zenodo download links
- Folder layout (`CALCE/`, `MATR/`, `HUST/`, `HNEI/`, `MICH/`, `CALB/`, …)
- Per-source README files (mirrored under this project's `DATA_info/`)

Once downloaded, point **Choose folder** at the root containing those subfolders.

---

## 11 · Citation

If you use this tool with the BatteryLife dataset in your research, please cite:

```bibtex
@inproceedings{10.1145/3711896.3737372,
  author    = {Tan, Ruifeng and Hong, Weixiang and Tang, Jiayue and Lu, Xibin
               and Ma, Ruijun and Zheng, Xiang and Li, Jia and Huang, Jiaqiang
               and Zhang, Tong-Yi},
  title     = {BatteryLife: A Comprehensive Dataset and Benchmark for Battery Life Prediction},
  year      = {2025},
  isbn      = {9798400714542},
  publisher = {Association for Computing Machinery},
  address   = {New York, NY, USA},
  url       = {https://doi.org/10.1145/3711896.3737372},
  doi       = {10.1145/3711896.3737372},
  booktitle = {Proceedings of the 31st ACM SIGKDD Conference on Knowledge Discovery
               and Data Mining V.2},
  pages     = {5789--5800},
  numpages  = {12},
  location  = {Toronto ON, Canada},
  series    = {KDD '25}
}
```

Tan et al., *BatteryLife: A Comprehensive Dataset and Benchmark for Battery Life Prediction*, KDD '25, Toronto, Canada.
