# Battery AI Analyzer

> **BatteryML PKL browser and plot viewer** — explore, compare, and visualise battery cycle-life data entirely in your browser. All processing runs locally; your data never leaves the machine.
>
> *Design by CuongFX*

---

## Quick Start

```bash
# from the project root
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765 --reload
```

Open **http://localhost:8765** in your browser.

**Requirements:** Python 3.10+, packages: `fastapi`, `uvicorn`, `plotly`, `numpy`, `scipy` (see `requirements.txt`).

> On Windows you can double-click `run_webapp_windows.cmd`; on macOS/Linux run `./run_webapp.sh`.

---

## Interface at a Glance

The window is split into a **left sidebar** (data source + navigation + cache) and a **main workspace** with three tabs:

| Tab | Purpose |
|---|---|
| **General Inspection** | Folder-wide overview — one bar per cell, stat cards, files table |
| **Analyse** | Single-cell deep dive — dQ/dV, dV/dQ, capacity-fade curves |
| **Feature Analyse** | Feature engineering — single, two-cell comparison, and whole-folder log-feature scatter |

![Choosing a folder and browsing the sidebar](docs/screenshots/01_choose_folder1.png)

---

## Step 1 — Choose Your Data Folder

Click **Choose folder** in the sidebar and select the root directory that holds your BatteryML subfolders (e.g. `Raw_BML/`).

Once a root is selected:

- The sidebar lists every **subfolder** (`DIR` badge) and **PKL file** (`PKL` badge).
- The app remembers this folder across browser reloads — you only pick it again if you switch datasets.
- Cached subfolders show a filled dot (●) for instant access.

### Sidebar Navigation

| Action | Result |
|---|---|
| **Single-click a subfolder** | Opens it in the *General Inspection* tab |
| **Double-click a subfolder** | Navigates *into* it to reveal its PKL files — does **not** switch tabs |
| **Single-click a PKL file** | Loads that cell into the *Analyse* / *Feature Analyse* tabs |

> **Tip:** Stay on the Analyse tab and double-click subfolders to drill down without losing your current plot.

The **first** time you open a subfolder, the app reads every PKL, computes per-file metrics (max cycle, Qd/Qc capacity, current, capacity fade, EOL cycle), and caches the result both on the server (`webapp/cache/folder_cycle_cache.json`) and in the browser. Every later visit is instant.

---

## Step 2 — General Inspection

The **General Inspection** tab gives a complete picture of all cells in a folder at once.

![General inspection of the MATR dataset](docs/screenshots/02_general_inspection.png)

### Dataset Info & Stat Cards

If a matching `DATA_info/<FOLDER>_README.md` exists, its description (chemistry, format, nominal capacity, protocol) is shown in a green info card. Below it sit four stat cards:

| Card | What it tells you |
|---|---|
| **Cells in folder** | Total number of `.pkl` files found |
| **Files plotted** | Files successfully read with valid cycle data |
| **Files with issues** | Files that failed to parse (corrupt, truncated, incompatible) |
| **Ambient temperature** | Test temperatures detected from filenames or the dataset README, shown as blue pill badges (e.g. `30 °C`). Shows `N/A` if unknown. |

### Bar Chart — Maximum Cycle per File

Each bar is one cell; height = its maximum recorded cycle. Bars are coloured on a pale-blue → deep-navy gradient so the weakest and strongest cells stand out instantly.

Three header buttons control ordering: **Original** (file order), **↓ Low → High** *(default)*, and **↑ High → Low**. Sorting runs entirely in the browser.

**EOL marker (End-of-Life @ 80% Qd):** click any bar to draw a red line at the cycle where discharge capacity first drops to 80% of its initial value; click again to toggle it off. **SVG** / **PDF** buttons export the current view.

### Files Table

Below the chart, the **Files in selected folder** table lists one row per cell:

| Column | Description |
|---|---|
| File | `.pkl` filename |
| Temp (°C) | Ambient temperature (filename or README fallback) |
| Cycles | Number of cycles recorded |
| I chg / I dch (A) | Max charge / discharge current |
| Qd max / Qd min (Ah) | Discharge-capacity bounds |
| Qc max / Qc min (Ah) | Charge-capacity bounds |
| Qd fade / Qc fade | Capacity fade percentage |

When a folder mixes two or more temperatures, a **Temperature** filter dropdown appears in the table header so you can show just the subset you care about.

---

## Step 3 — Analyse Tab (Single-Cell Deep Dive)

Double-click a `.pkl` file in the sidebar (the status bar shows `Loaded: <filename>`), then open the **Analyse** tab for electrochemical analysis of that one cell.

![Single-cell analysis with summary cards and a current-vs-time curve](docs/screenshots/03_single_cell_analyse.png)

### Summary Cards

Above the plot, cards summarise the cell: **cycles total**, **max cycle index**, **voltage range**, **current range**, and first/last values with % decrease for **Qdmax** and **Qcmax**.

### Plot Types

| Plot type | What it shows |
|---|---|
| dQ/dV — discharge / charge / both | Differential capacity vs voltage |
| dV/dQ — discharge / charge / both | Differential voltage vs capacity |
| Qd vs voltage — discharge | Discharge capacity vs cell voltage |
| Qcharge vs voltage | Charge capacity vs cell voltage |
| dQ/dV & dV/dQ vs time | The above plotted against time instead of voltage |
| Qcmax vs cycle / Qdmax vs cycle | Capacity-fade curve across all cycles |

**How to use:** pick a plot type, type the cycles to compare in the **Cycles** field (`0, 50, 100` or `all`), optionally tick **Filter** to clip axes to the 1–99 percentile, then click **Generate plot**. Range sliders under the chart let you zoom; **Autoscale** resets.

---

## Step 4 — Feature Analyse

The **Feature Analyse** tab builds difference-based features that are useful for degradation studies and life-prediction models. It has three sub-tabs.

### Plot single / Compare two

**Plot single** renders a feature for one cell; **Compare two** overlays the same feature from two cells on one chart. Tick **Use reference** to subtract a chosen reference cycle from every plotted cycle, so each curve shows the change relative to that baseline.

![Compare two — Δ dQ/dV vs voltage for two MATR cells](docs/screenshots/04_feature_analyse_compare_2.png)

In the example above, File A (`MATR_b1c18`) and File B (`MATR_b1c30`) are overlaid as **Δ dQ/dV vs voltage**, each referenced to cycle 10.

### Plot Log feature

**Plot Log feature** compares **every cell in the selected folder** at once. For a chosen **target cycle**, it draws one point per cell — the log-magnitude feature (e.g. `log⟨|Δ dQ/dV|⟩`) against the cell's cycle life — so you can see how the cells separate at that cycle. A reference cycle is optional; when enabled, each cell's data has its reference-cycle data subtracted before the feature is computed.

![Plot Log feature — whole-folder scatter coloured by cycle life](docs/screenshots/05_log_features_analyse.png)

Every Feature Analyse plot supports **Filter** (outlier clipping), range sliders, and **SVG** / **PDF** export.

---

## Folder Cache Panel

The **Folder cache** section at the bottom of the sidebar manages all cached data.

| Button | Effect |
|---|---|
| **Cache all folders** | Pre-loads every visible subfolder silently — useful for bulk caching |
| **Export JSON** | Downloads the cache so you can back it up or share it |
| **Import JSON** | Restores a previously exported cache (merges with existing entries) |
| **Clear** | Wipes the entire browser cache |

> **Sharing a cache:** Export on one machine and Import on another to skip re-processing the PKL files.

### Cache Architecture

| Layer | Location | Purpose |
|---|---|---|
| Server disk cache | `webapp/cache/folder_cycle_cache.json` | Per-file metrics, keyed by path + size + mtime; auto-invalidated when files change |
| Browser localStorage | `batteryAi.folders.v2` | Row data mirrored in-browser; bar chart rebuilt client-side on reload |
| Last selected folder | `batteryAi.lastSelected` | Subfolder open at last close — auto-restored |
| Last root folder | `batteryAi.lastRootDir` | Root browsed last — auto-browsed on next load |

---

## Ambient Temperature Detection

Each cell's ambient temperature is resolved in order:

1. **Filename** — chemistry/format tokens (`NMC_25C`, `pouch_-5C`, `18650_30C`, `LFP_45C`), dataset-prefix style (`CALB_0_B182` → `0 °C`), and Tongji cycling tokens (`Tongji1_CY25-…` → `25 °C`). Cell-ID and C-rate tokens like `02C` or `1-1C` are deliberately **not** matched.
2. **README fallback** — for single-temperature datasets (e.g. MATR, HUST, RWTH at one fixed temperature), the value stated in the dataset description (*"30 degrees Celsius"*, *"25°C"*) fills in any file whose name carries no temperature.
3. **N/A** — shown when no temperature can be determined (e.g. a multi-temperature dataset whose filenames don't encode it).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Files show `—` in every column | Those PKL files are corrupt or truncated. Re-download, then click **↻ Reload data**. |
| "No cycle data found" | The PKL files lack a `cycle_data` list with `cycle_number` fields. |
| Loading is slow every time | Delete `webapp/cache/folder_cycle_cache.json` and reload to rebuild. |
| Stale UI after an update | Hard-refresh: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac). |
| Wrong / missing temperature | Click **Clear** in the cache panel and reload the folder. |
| "Connection error" | The server may have restarted — refresh the page. |

---

## Expected Folder Structure

```
Root folder/             ← select this with "Choose folder"
├── CALB/
│   ├── CALB_0_B182.pkl
│   ├── CALB_25_B247.pkl
│   └── ...
├── HUST/
│   ├── HUST_1-1.pkl
│   └── ...
├── MATR/
│   ├── MATR_b1c0.pkl
│   └── ...
└── DATA_info/           ← optional: README files for dataset descriptions
    ├── CALB_README.md
    ├── MATR_README.md
    └── ...
```

---

## Dataset Download

This app works with the **BatteryLife** dataset collection:

**https://github.com/Ruifeng-Tan/BatteryLife**

The repository provides Hugging Face / Zenodo download links covering all included datasets: `CALCE`, `MATR`, `HUST`, `HNEI`, `MICH`, `CALB`, `MICH_EXP`, `SNL`, `Tongji`, and more.

---

## Citation

### This tool

If you use the **Battery AI Analyzer** in your research, please cite it as:

> Pham, Manh Cuong. *Battery AI Analyzer* RPTU Kaiserslautern-Landau, 2026. https://github.com/Cuongfx/Battery_analyse_web_app

```bibtex
@software{pham_battery_ai_analyzer_2026,
  author  = {Pham, Manh Cuong},
  year    = {2026},
  url     = {https://github.com/Cuongfx/Battery_analyse_web_app},
  note    = {RPTU Kaiserslautern-Landau.
             https://eit.rptu.de/fgs/meas/team/m-sc-manh-cuong-pham}
}
```

### BatteryLife dataset

If you also use the BatteryLife dataset, please cite:

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

---

## Author

👨‍💻 **Manh Cuong Pham**
📧 mpham@rptu.de
💼 PhD Candidate at RPTU Kaiserslautern-Landau
🔗 [Team page](https://eit.rptu.de/fgs/meas/team/m-sc-manh-cuong-pham) · [GitHub repo](https://github.com/Cuongfx/Battery_analyse_web_app)
