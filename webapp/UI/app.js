const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  loadedFileName: null,
  compareSessionId: null,
  compareFileName: null,
  currentDir: null,
  rootDir: null,
  selectedFolder: null,
  charts: new Map(),
  folderCache: new Map(),
  datasetInfo: {},
  activeTempFilter: null,    // Set<number> | null  (null = no filter, all visible)
  currentRowsForFilter: [],  // rows for current folder, used when filter changes
  folderFallbackTemp: null,  // number | null — README ambient temp for files whose name has none (single-temp datasets only)
};

const LAST_ROOT_KEY = "batteryAi.lastRootDir";
const LAST_SELECTED_KEY = "batteryAi.lastSelected";
const FOLDER_CACHE_LS_KEY = "batteryAi.folders.v2";

const plotConfig = {
  responsive: true,
  displayModeBar: true,
  displaylogo: false,
  scrollZoom: true,
  modeBarButtonsToRemove: ["toImage"],
};

// ── Theme (light / dark) ───────────────────────────────────────────────────
const THEME_KEY = "batteryAi.theme";

const PLOT_THEME = {
  light: { paper: "#FFFFFF", plot: "#F6F8FB", font: "#172033", grid: "#E7ECF3", line: "#CDD6E3" },
  dark:  { paper: "#18202b", plot: "#141b24", font: "#e6edf6", grid: "#2c3848", line: "#3d4c60" },
};

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

// Re-colour one Plotly chart to match the active theme.
function themePlot(chart) {
  if (!chart || !chart.layout || typeof Plotly === "undefined") return;
  const t = PLOT_THEME[currentTheme()];
  const upd = {
    paper_bgcolor: t.paper,
    plot_bgcolor: t.plot,
    "font.color": t.font,
    "legend.font.color": t.font,
    "title.font.color": t.font,
  };
  for (const key of Object.keys(chart.layout)) {
    if (/^[xy]axis\d*$/.test(key)) {
      upd[`${key}.gridcolor`] = t.grid;
      upd[`${key}.zerolinecolor`] = t.line;
      upd[`${key}.linecolor`] = t.line;
      upd[`${key}.tickcolor`] = t.line;
    }
  }
  try { Plotly.relayout(chart, upd); } catch (_) {}
}

function themeAllPlots() {
  document.querySelectorAll(".js-plotly-plot").forEach(themePlot);
}

function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const btn = $("themeToggle");
  if (btn) {
    btn.querySelector(".theme-toggle-icon").textContent = dark ? "☀️" : "🌙";
    btn.querySelector(".theme-toggle-label").textContent = dark ? "Light" : "Dark";
  }
  themeAllPlots();
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
  if (!saved) {
    saved = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyTheme(saved);
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
  applyTheme(next);
}

// ── Data summary tab (source: Table 1, battery_data.pdf) ────────────────────
const CHEM_FAMILIES = {
  LFP:        { label: "LFP",            cls: "chem-LFP" },
  "NMC/NCA":  { label: "NMC / NCA",      cls: "chem-NMCNCA" },
  LCO:        { label: "LCO",            cls: "chem-LCO" },
  "LCO+NMC":  { label: "LCO + NMC",      cls: "chem-LCONMC" },
  "LFP+NCA+NMC": { label: "LFP + NCA + NMC", cls: "chem-LFPNCANMC" },
  "Na-ion":   { label: "Na-ion",         cls: "chem-Naion" },
  "Zn-MnO₂":  { label: "Zn–MnO₂",        cls: "chem-ZnMnO2" },
};

// Negative-electrode materials → coloured badge (same visual language as chemistry)
const NEG_FAMILIES = {
  Graphite: { label: "Graphite", cls: "neg-graphite" },
  Carbon:   { label: "Carbon",   cls: "neg-carbon" },
  Zinc:     { label: "Zinc",     cls: "neg-zinc" },
  "—":      { label: "—",        cls: "neg-none" },
};

// pos     → short cathode label shown in the badge
// posFull → full chemical formula revealed on hover
// neg     → negative-electrode material key (see NEG_FAMILIES)
const BATTERY_DATASETS = [
  { name: "HUST", format: "18650", cells: 77, pos: "LFP", posFull: "LiFePO₄", neg: "Graphite", cap: "1.1", temp: "30", fam: "LFP" },
  { name: "MATR", format: "18650", cells: 169, pos: "LFP", posFull: "LiFePO₄", neg: "Graphite", cap: "1.1", temp: "30", fam: "LFP" },
  { name: "CALB", format: "", cells: 27, pos: "NMC", posFull: "Lithium nickel manganese cobalt oxide", neg: "Graphite", cap: "58", temp: "0, 25, 35, 45", fam: "NMC/NCA" },
  { name: "ISU", format: "502030 Li-polymer", cells: 240, pos: "NMC", posFull: "Lithium nickel manganese cobalt oxide", neg: "Graphite", cap: "0.25", temp: "30", fam: "NMC/NCA" },
  { name: "MICH_EXP", format: "pouch", cells: 12, pos: "NMC", posFull: "NMC111", neg: "Graphite", cap: "5.0", temp: "−5, 25, 45", fam: "NMC/NCA" },
  { name: "MICH", format: "", cells: 40, pos: "NMC", posFull: "NMC111", neg: "Graphite", cap: "2.36", temp: "25, 45", fam: "NMC/NCA" },
  { name: "RWTH", format: "", cells: 48, pos: "NMC", posFull: "Lithium nickel manganese cobalt oxide", neg: "Carbon", cap: "3", temp: "25", fam: "NMC/NCA" },
  { name: "SDU", format: "18650", cells: 86, pos: "NMC", posFull: "Lithium nickel manganese cobalt oxide", neg: "Graphite", cap: "2.4", temp: "25", fam: "NMC/NCA" },
  { name: "STANFORD", format: "18650", cells: 41, pos: "NMC", posFull: "LiNi₀.₅Mn₀.₃Co₀.₂O₂", neg: "Graphite", cap: "0.24", temp: "30", fam: "NMC/NCA" },
  { name: "STANFORD 2", format: "18650", cells: 181, pos: "NMC", posFull: "LiNi₀.₅Mn₀.₃Co₀.₂O₂", neg: "Graphite", cap: "0.24", temp: "30", fam: "NMC/NCA" },
  { name: "XJTU", format: "", cells: 23, pos: "NMC", posFull: "LiNi₀.₅Co₀.₂Mn₀.₃O₂", neg: "Graphite", cap: "2", temp: "20", fam: "NMC/NCA" },
  { name: "TONGJI", format: "18650", cells: 108, pos: "NCA / NMC", posFull: "Li₀.₈₆Ni₀.₈₆Co₀.₁₁Al₀.₀₃O₂ / Li₀.₈₄(Ni₀.₈₃Co₀.₁₁Mn₀.₀₇)O₂", neg: "Graphite", cap: "2.5 / 3.5", temp: "multiple", fam: "NMC/NCA" },
  { name: "CALCE", format: "", cells: 13, pos: "LCO", posFull: "LiCoO₂", neg: "Graphite", cap: "1.1", temp: "25", fam: "LCO" },
  { name: "HNEI", format: "", cells: 14, pos: "LCO + NMC", posFull: "LiCoO₂ & LiNi₀.₄Co₀.₄Mn₀.₂O₂", neg: "Graphite", cap: "2.8", temp: "25", fam: "LCO+NMC" },
  { name: "UL", format: "18650", cells: 10, pos: "LCO + NMC", posFull: "LiCoO₂ & LiNi₀.₄Co₀.₄Mn₀.₂O₂", neg: "Graphite", cap: "3.4", temp: "23", fam: "LCO+NMC" },
  { name: "SNL", format: "18650", cells: 52, pos: "LFP + NCA + NMC", posFull: "LiFePO₄ / LiNi₀.₈₁Co₀.₁₄Al₀.₀₅O₂ / LiNi₀.₈₄Mn₀.₀₆Co₀.₁O₂", neg: "Graphite", cap: "1.1 / 3.2 / 3.0", temp: "15, 25, 35", fam: "LFP+NCA+NMC" },
  { name: "NA-ION", format: "18650", cells: 31, pos: "Na-ion", posFull: "Sodium-ion cathode", neg: "—", cap: "1.0", temp: "25", fam: "Na-ion" },
  { name: "ZN-COIN", format: "", cells: 95, pos: "Zn–MnO₂", posFull: "Manganese dioxide (MnO₂) cathode", neg: "Zinc", cap: "10", temp: "25", fam: "Zn-MnO₂" },
];

let _summaryRendered = false;
function renderDataSummary() {
  if (_summaryRendered) return;          // static content — build once
  _summaryRendered = true;

  const rows = BATTERY_DATASETS;
  const known = rows.filter((r) => r.cells != null);
  const totalCells = known.reduce((s, r) => s + r.cells, 0);
  const families = [...new Set(rows.map((r) => r.fam))];

  const stat = (title, value, sub) => `
    <article class="stat-card">
      <div class="stat-title">${escapeHtml(title)}</div>
      <div class="stat-value">${value}</div>
      ${sub ? `<div class="stat-sub muted">${escapeHtml(sub)}</div>` : ""}
    </article>`;

  $("summaryStats").innerHTML = [
    stat("Datasets", rows.length),
    stat("Cells (total)", totalCells.toLocaleString()),
    stat("Chemistry families", families.length),
  ].join("");

  $("summaryFamilies").innerHTML = families
    .map((f) => {
      const meta = CHEM_FAMILIES[f] || { label: f, cls: "" };
      const n = rows.filter((r) => r.fam === f).length;
      return `<span class="chem-badge fam-chip ${meta.cls}" data-fam="${escapeHtml(f)}" role="button" tabindex="0" title="Click to filter by ${escapeHtml(meta.label)}">${escapeHtml(meta.label)} · ${n}</span>`;
    })
    .join("");
  $("summaryFamilies").onclick = (e) => {
    const chip = e.target.closest(".fam-chip");
    if (chip) setSummaryFilter(chip.dataset.fam);
  };

  const head = `
    <thead><tr>
      <th>Dataset</th>
      <th class="num">Cells</th>
      <th>Positive electrode</th>
      <th>Negative electrode</th>
      <th class="num">Nominal capacity (Ah)</th>
      <th class="num">Temperature (°C)</th>
      <th>Chemistry</th>
    </tr></thead>`;

  const body = rows
    .map((r) => {
      const meta = CHEM_FAMILIES[r.fam] || { label: r.fam, cls: "" };
      const negMeta = NEG_FAMILIES[r.neg] || { label: r.neg, cls: "neg-none" };
      const posTitle = r.posFull ? ` title="${escapeHtml(r.posFull)}"` : "";
      return `<tr data-fam="${escapeHtml(r.fam)}">
        <td><span class="ds-name">${escapeHtml(r.name)}</span>${r.format ? `<span class="ds-format">${escapeHtml(r.format)}</span>` : ""}</td>
        <td class="num ds-cells">${r.cells != null ? r.cells.toLocaleString() : "—"}</td>
        <td><span class="chem-badge ${meta.cls} has-tip"${posTitle}>${escapeHtml(r.pos)}</span></td>
        <td><span class="neg-badge ${negMeta.cls}">${escapeHtml(negMeta.label)}</span></td>
        <td class="num">${escapeHtml(r.cap)}</td>
        <td class="num">${escapeHtml(r.temp)}</td>
        <td><span class="chem-badge ${meta.cls}">${escapeHtml(meta.label)}</span></td>
      </tr>`;
    })
    .join("");

  $("summaryTable").innerHTML = head + `<tbody>${body}</tbody>`;
}

// Click a family chip to filter the table to that chemistry; click it again to reset.
let _summaryActiveFam = null;
function setSummaryFilter(fam) {
  _summaryActiveFam = _summaryActiveFam === fam ? null : fam;
  const active = _summaryActiveFam;
  document.querySelectorAll("#summaryTable tbody tr").forEach((tr) => {
    tr.hidden = active != null && tr.dataset.fam !== active;
  });
  document.querySelectorAll("#summaryFamilies .fam-chip").forEach((chip) => {
    const on = active == null || chip.dataset.fam === active;
    chip.classList.toggle("chip-dim", !on);
    chip.classList.toggle("chip-selected", active != null && chip.dataset.fam === active);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showErr(id, msg) {
  const el = $(id);
  if (msg) {
    el.textContent = msg;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function setLoaded(on, name = "") {
  $("loadDot").classList.toggle("on", on);
  $("loadText").textContent = on ? `Loaded: ${name}` : "No file loaded";
}

function enablePlotControls(enabled) {
  [
    "plotKind", "cycleMode", "cycles", "cycleFrom", "cycleTo", "cycleStep",
    "genPlot", "filterOutliers",
    // Feature Analyse
    "featPlotKind", "featUseRefCycle", "featRefCycle", "featCycleMode", "featCycles",
    "featCycleFrom", "featCycleTo", "featCycleStep",
    "featGenPlot", "featFilterOutliers",
  ].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = !enabled;
  });
  // Re-evaluate folder-mode availability (it's based on plot kind, not session state).
  if (typeof syncFeatFolderMode === "function") syncFeatFolderMode();
  if (typeof syncFeatReferenceUi === "function") syncFeatReferenceUi();
}

// Show/hide cycle inputs for a given input-set prefix ("" = Analyse, "feat" = Feature).
function syncCycleModeFor(prefix) {
  const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  const id = (base) => prefix ? `${prefix}${cap(base)}` : base;
  const mode = $(id("cycleMode")).value;
  $(id("cycles")).hidden = mode !== "list";
  $(id("cycleRangeInputs")).hidden = mode !== "range";
}

function syncCycleMode() { syncCycleModeFor(""); }
function syncFeatCycleMode() { syncCycleModeFor("feat"); }

// Build the cycles spec for either input-set.
function readCycleSpecFor(prefix) {
  const cap = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  const id = (base) => prefix ? `${prefix}${cap(base)}` : base;
  const mode = $(id("cycleMode")).value;
  if (mode === "list") {
    return $(id("cycles")).value.trim() || "all";
  }
  if (mode === "range") {
    const from = $(id("cycleFrom")).value.trim();
    const to = $(id("cycleTo")).value.trim();
    const step = $(id("cycleStep")).value.trim();
    if (from === "" && to === "") return "all";
    const a = from === "" ? "0" : from;
    const b = to === "" ? a : to;
    return step !== "" ? `${a}-${b}:${step}` : `${a}-${b}`;
  }
  return "all";
}

function readCycleSpec() { return readCycleSpecFor(""); }

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  $("generalPanel").hidden = name !== "general";
  $("deepPanel").hidden = name !== "deep";
  $("featurePanel").hidden = name !== "feature";
  $("summaryPanel").hidden = name !== "summary";
  $("generalPanel").classList.toggle("active", name === "general");
  $("deepPanel").classList.toggle("active", name === "deep");
  $("featurePanel").classList.toggle("active", name === "feature");
  $("summaryPanel").classList.toggle("active", name === "summary");
  if (name === "summary") renderDataSummary();
  setTimeout(() => {
    state.charts.forEach((chart) => Plotly.Plots.resize(chart));
  }, 0);
}

function fmtSize(bytes) {
  if (bytes == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes);
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value < 10 && unitIndex ? 1 : 0)} ${units[unitIndex]}`;
}

function fmtNum(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  const x = Number(value);
  if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.01)) {
    return x.toExponential(3);
  }
  return x.toFixed(4).replace(/\.?0+$/, "");
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(2)}%`;
}

function extractTempFromName(name) {
  if (!name) return null;
  const base = String(name).replace(/\.pkl$/i, "");
  // Anchor to chemistry/format keywords to avoid false matches on cell IDs like 02C, 05C
  let m = base.match(/(?:NMC\d*|NCA\d*|NCM\d*|LFP|LCO|LiFePO4|LiCoO2|LiNiCoMnO2|pouch|cylindrical|prismatic|18650|26650|coin|polymer|502030)_(-?\d+)C(?:_|$|\.)/i);
  if (m) {
    const t = parseInt(m[1], 10);
    if (t >= -30 && t <= 80) return t;
  }
  m = base.match(/^[A-Za-z]+_(-?\d+)_/);
  if (m) {
    const t = parseInt(m[1], 10);
    if (t >= -30 && t <= 80) return t;
  }
  return null;
}

function rowTemp(row) {
  if (row == null) return null;
  if (row.temperature_c != null) return row.temperature_c;
  const fromName = extractTempFromName(row.name);
  if (fromName != null) return fromName;
  // Fall back to the dataset's README ambient temperature, but only when it's a
  // single-temperature dataset (otherwise we can't tell which cell is which).
  return state.folderFallbackTemp;
}

function uniqueTemps(rows) {
  const set = new Set();
  for (const r of rows) {
    const t = rowTemp(r);
    if (t != null) set.add(t);
  }
  return [...set].sort((a, b) => a - b);
}

// Parse test temperature from free-text dataset info (README prose).
// Handles: "30 degrees Celsius", "temperature of 25°C", "45°C", "at 0 °C", etc.
function extractTempFromInfoText(text) {
  if (!text) return [];
  const found = new Set();
  // Pattern: number followed by "degrees Celsius" / "°C" / "oC" with optional spaces
  // Trailing (?![0-9]) instead of \b so "35°Cand45°C" (no space) still yields 35.
  const re = /(-?\d+)\s*(?:degrees?\s*[Cc]elsius|°[Cc]|o[Cc])(?![0-9])/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const t = parseInt(m[1], 10);
    if (t >= -30 && t <= 80) found.add(t);
  }
  return [...found].sort((a, b) => a - b);
}

function fmtAxis(value) {
  if (value == null || !Number.isFinite(Number(value))) return "";
  return Number(value).toPrecision(6);
}


function fieldDetail(info) {
  if (!info || typeof info !== "object") return String(info);
  const parts = [];
  if (info.type) parts.push(info.type);
  if (info.length != null) parts.push(`length ${info.length}`);
  if (info.len != null) parts.push(`length ${info.len}`);
  if (info.preview != null) parts.push(`preview ${info.preview}`);
  return parts.join(", ") || JSON.stringify(info);
}

function renderMeta(meta) {
  if (!meta || !meta.cycle_count) {
    $("meta").hidden = true;
    return;
  }

  $("meta").innerHTML = [
    statValueCard("Cycles total", meta.cycle_count),
    statValueCard("Max cycle index", meta.max_cycle_index ?? meta.cycle_count - 1),
    statRangeCard("Voltage (V)", meta.voltage),
    statRangeCard("Current (A)", meta.current),
    capacityCard("Qdmax", meta.qdmax),
    capacityCard("Qcmax", meta.qcmax),
  ].join("");
  $("meta").hidden = false;
  $("cycles").placeholder = `e.g. 0,1,${Math.max(0, meta.cycle_count - 1)} or all (0..${meta.cycle_count - 1})`;
}

function renderFolderStats(data, temps = []) {
  const tempHtml = temps.length
    ? temps.map(t => `<span class="temp-pill">${t} °C</span>`).join("")
    : `<span class="temp-pill temp-pill--none">N/A</span>`;
  const tempCard = `
    <article class="stat-card stat-card--temp">
      <div class="stat-title">Ambient temperature</div>
      <div class="temp-pill-row">${tempHtml}</div>
    </article>
  `;
  $("folderStats").innerHTML = [
    statValueCard("Cells in folder", data.file_count ?? 0),
    statValueCard("Files plotted", data.valid_file_count ?? 0),
    statValueCard("Files with issues", data.failed_file_count ?? 0),
    tempCard,
  ].join("");
}

function statValueCard(title, value) {
  return `
    <article class="stat-card">
      <div class="stat-title">${escapeHtml(title)}</div>
      <div class="stat-value">${escapeHtml(value)}</div>
    </article>
  `;
}

function statRangeCard(title, range) {
  const min = range && range.min != null ? fmtNum(range.min) : "N/A";
  const max = range && range.max != null ? fmtNum(range.max) : "N/A";
  return `
    <article class="stat-card">
      <div class="stat-title">${escapeHtml(title)}</div>
      <div class="range-pair">
        <div><span>Min</span><strong>${escapeHtml(min)}</strong></div>
        <div><span>Max</span><strong>${escapeHtml(max)}</strong></div>
      </div>
    </article>
  `;
}

function capacityCard(title, summary) {
  if (!summary) {
    return `
      <article class="stat-card wide">
        <div class="stat-title">${escapeHtml(title)}</div>
        <div class="stat-value">N/A</div>
      </article>
    `;
  }
  return `
    <article class="stat-card wide">
      <div class="stat-title">${escapeHtml(title)}</div>
      <div class="capacity-pair">
        <div>
          <span>First cycle ${escapeHtml(summary.first_cycle)}</span>
          <strong>${escapeHtml(fmtNum(summary.first))} Ah</strong>
        </div>
        <div>
          <span>Last cycle ${escapeHtml(summary.last_cycle)}</span>
          <strong>${escapeHtml(fmtNum(summary.last))} Ah</strong>
        </div>
      </div>
      <div class="capacity-decrease">
        <span>Decrease</span>
        <strong>${escapeHtml(fmtPct(summary.decrease_pct))}</strong>
      </div>
    </article>
  `;
}

async function pickFolder() {
  showErr("loadErr", "");
  const button = $("pickFolder");
  button.disabled = true;
  try {
    const response = await fetch("/api/pick-folder", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showErr("loadErr", data.detail || response.statusText);
      return;
    }
    if (data.path) {
      const isNewRoot = data.path !== localStorage.getItem(LAST_ROOT_KEY);
      state.rootDir = data.path;
      localStorage.setItem(LAST_ROOT_KEY, data.path);
      await browseDir(data.path, { warmCache: isNewRoot });
    }
  } finally {
    button.disabled = false;
  }
}

async function browseDir(dir, options = {}) {
  showErr("loadErr", "");
  state.currentDir = dir;
  $("folderPath").textContent = dir;
  $("browseLoading").hidden = false;
  $("browser").hidden = true;
  try {
    const response = await fetch(`/api/browse?dir=${encodeURIComponent(dir)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showErr("loadErr", data.detail || response.statusText);
      return;
    }
    renderBrowser(data);
    refreshBrowserCacheIndicators();
    updatePreCacheButtonState();
    if (options.warmCache) {
      await warmFolderCache(data.path);
      preCacheAllFolders();
    }
    // Auto-restore last selected subfolder if it's cached and belongs to this root
    if (!options.skipRestore) {
      const lastSelected = localStorage.getItem(LAST_SELECTED_KEY);
      if (lastSelected && state.folderCache.has(lastSelected)) {
        const norm = (p) => p.replace(/\\/g, "/");
        if (norm(lastSelected).startsWith(norm(dir) + "/")) {
          await inspectFolder(lastSelected);
        }
      }
    }
  } finally {
    $("browseLoading").hidden = true;
  }
}

async function warmFolderCache(rootPath) {
  $("cacheStatus").textContent = "Caching cycle information for all folders...";
  $("cacheLoading").hidden = false;
  try {
    const response = await fetch("/api/cache-folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: rootPath }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showErr("loadErr", data.detail || response.statusText);
      return;
    }
    $("cacheStatus").textContent = `Cached ${data.files_cached} files in ${data.folders_cached} folders`;
    setTimeout(() => {
      $("cacheLoading").hidden = true;
    }, 2200);
  } catch (error) {
    showErr("loadErr", String(error));
  } finally {
    if ($("cacheStatus").textContent.startsWith("Caching")) {
      $("cacheLoading").hidden = true;
    }
  }
}

// ── Persistent localStorage cache ──────────────────────────────────────────

function getAllStoredFolders() {
  try { return JSON.parse(localStorage.getItem(FOLDER_CACHE_LS_KEY) || "{}"); }
  catch { return {}; }
}

function saveToPersistedCache(path, data) {
  try {
    const all = getAllStoredFolders();
    all[path] = {
      ts: Date.now(),
      rows: data.rows || [],
      file_count: data.file_count ?? 0,
      valid_file_count: data.valid_file_count ?? 0,
      failed_file_count: data.failed_file_count ?? 0,
      folder_name: (path || "").split(/[\\/]/).pop(),
    };
    localStorage.setItem(FOLDER_CACHE_LS_KEY, JSON.stringify(all));
    updateCachePanel();
  } catch (e) { console.warn("Cache save failed:", e); }
}

function loadPersistedFolderCache() {
  const all = getAllStoredFolders();
  for (const [path, entry] of Object.entries(all)) {
    if (!state.folderCache.has(path)) {
      state.folderCache.set(path, {
        rows: entry.rows || [],
        file_count: entry.file_count ?? 0,
        valid_file_count: entry.valid_file_count ?? 0,
        failed_file_count: entry.failed_file_count ?? 0,
        folder_name: entry.folder_name || path.split(/[\\/]/).pop(),
        figure: null,
      });
    }
  }
  updateCachePanel();
}

function updateCachePanel() {
  const label = $("cacheCountLabel");
  const btnExport = $("btnExportCache");
  const btnClear = $("btnClearCache");
  if (!label) return;
  const count = Object.keys(getAllStoredFolders()).length;
  label.textContent = count > 0 ? `${count} folder${count !== 1 ? "s" : ""} cached` : "No folders cached";
  if (btnExport) btnExport.disabled = count === 0;
  if (btnClear) btnClear.disabled = count === 0;
}

function refreshBrowserCacheIndicators() {
  const all = getAllStoredFolders();
  document.querySelectorAll("#browser .row.dir").forEach((row) => {
    const path = decodeURIComponent(row.dataset.path);
    row.classList.toggle("cached", !!all[path]);
  });
}

function updatePreCacheButtonState() {
  const hasDirs = !!document.querySelector("#browser .row.dir");
  const b1 = $("btnPreCacheAll");
  const b2 = $("btnStartLoad");
  if (b1) b1.disabled = !hasDirs;
  if (b2) b2.disabled = !hasDirs;
}

// ── Client-side Plotly figure from rows ────────────────────────────────────

function buildFolderFigure(rows, folderName) {
  const valid = rows.filter((r) => r.max_cycle != null);
  if (!valid.length) return null;

  const names = valid.map((r) => r.name);
  const maxCycles = valid.map((r) => r.max_cycle);
  // customdata: [cycle_count, eol_80_cycle]  (eol = -1 if not available)
  const customdata = valid.map((r) => [
    r.cycle_count || 0,
    (r.eol_80_cycle != null && r.eol_80_cycle > 0) ? r.eol_80_cycle : -1,
  ]);

  const minC = Math.min(...maxCycles);
  const maxC = Math.max(...maxCycles);
  const span = Math.max(maxC - minC, 1);
  const colors = maxCycles.map((v) => {
    const t = (v - minC) / span;
    return `rgb(${Math.round(189 + (23 - 189) * t)},${Math.round(215 + (71 - 215) * t)},${Math.round(247 + (166 - 247) * t)})`;
  });

  const traces = [{
    type: "bar",
    x: names,
    y: maxCycles,
    marker: { color: colors, line: { color: "rgba(23,71,166,0.35)", width: 0.6 } },
    customdata: customdata,
    hovertemplate: "File: %{x}<br>Max cycle: %{y}<extra></extra>",
    showlegend: false,
  }];

  return {
    data: traces,
    layout: {
      title: { text: `Maximum cycle per file - ${folderName}`, x: 0.02, xanchor: "left", font: { size: 15, color: "#111827" } },
      height: 500,
      margin: { t: 58, b: 58, l: 68, r: 28 },
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#F6F8FB",
      font: { family: "DM Sans, Inter, system-ui, sans-serif", size: 12, color: "#172033" },
      showlegend: false,
      hovermode: "closest",
      xaxis: { title: { text: "" }, showticklabels: false, ticks: "", gridcolor: "#E7ECF3", zerolinecolor: "#CDD6E3", linecolor: "#CDD6E3", tickcolor: "#CDD6E3" },
      yaxis: { title: { text: "Maximum cycle" }, gridcolor: "#E7ECF3", zerolinecolor: "#CDD6E3", linecolor: "#CDD6E3", ticks: "outside", tickcolor: "#CDD6E3" },
    },
  };
}

// ── Pre-caching ────────────────────────────────────────────────────────────

async function prefetchFolder(path) {
  if (state.folderCache.has(path)) return true;
  return new Promise((resolve) => {
    const es = new EventSource(`/api/folder-inspect-stream?dir=${encodeURIComponent(path)}`);
    es.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.done) {
        es.close();
        state.folderCache.set(path, msg);
        saveToPersistedCache(path, msg);
        resolve(true);
      }
    };
    es.onerror = () => { es.close(); resolve(false); };
  });
}

async function preCacheAllFolders() {
  const dirRows = Array.from(document.querySelectorAll("#browser .row.dir"));
  if (!dirRows.length) return;

  const btn = $("btnPreCacheAll");
  if (btn) btn.disabled = true;
  $("preCacheLoading").hidden = false;

  const dirs = dirRows.map((r) => decodeURIComponent(r.dataset.path));
  let done = 0;
  for (const path of dirs) {
    const name = path.split(/[\\/]/).pop();
    $("preCacheStatus").textContent = `Caching ${done + 1}/${dirs.length}: ${name}`;
    await prefetchFolder(path);
    refreshBrowserCacheIndicators();
    done++;
  }

  $("preCacheStatus").textContent = `Done — ${done} folder${done !== 1 ? "s" : ""} cached`;
  setTimeout(() => { $("preCacheLoading").hidden = true; }, 2500);
  updatePreCacheButtonState();
}

// ── Cache export / import / clear ─────────────────────────────────────────

function exportCacheJson() {
  try {
    const blob = new Blob([localStorage.getItem(FOLDER_CACHE_LS_KEY) || "{}"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "battery_ai_cache.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { showErr("loadErr", "Export failed: " + e.message); }
}

function importCacheJson(file) {
  const reader = new FileReader();
  reader.onload = (ev) => {
    try {
      const imported = JSON.parse(ev.target.result);
      const merged = { ...getAllStoredFolders(), ...imported };
      localStorage.setItem(FOLDER_CACHE_LS_KEY, JSON.stringify(merged));
      for (const [path, entry] of Object.entries(imported)) {
        if (!state.folderCache.has(path)) {
          state.folderCache.set(path, {
            rows: entry.rows || [],
            file_count: entry.file_count ?? 0,
            valid_file_count: entry.valid_file_count ?? 0,
            failed_file_count: entry.failed_file_count ?? 0,
            folder_name: entry.folder_name || path.split(/[\\/]/).pop(),
            figure: null,
          });
        }
      }
      updateCachePanel();
      refreshBrowserCacheIndicators();
    } catch (e) { showErr("loadErr", "Import failed: " + e.message); }
  };
  reader.readAsText(file);
}

function clearFolderCache() {
  localStorage.removeItem(FOLDER_CACHE_LS_KEY);
  state.folderCache.clear();
  updateCachePanel();
  refreshBrowserCacheIndicators();
}

async function reloadCurrentFolder() {
  const path = state.selectedFolder;
  if (!path) return;

  const btn = $("btnReloadFolder");
  if (btn) { btn.disabled = true; btn.textContent = "Reloading…"; }

  // Wipe JS-side caches for this folder
  state.folderCache.delete(path);
  try {
    const all = JSON.parse(localStorage.getItem(FOLDER_CACHE_LS_KEY) || "{}");
    delete all[path];
    localStorage.setItem(FOLDER_CACHE_LS_KEY, JSON.stringify(all));
  } catch (_e) {}
  updateCachePanel();
  refreshBrowserCacheIndicators();

  // Wipe server-side disk cache for this folder
  try {
    await fetch(`/api/folder-cache?dir=${encodeURIComponent(path)}`, { method: "DELETE" });
  } catch (_e) {}

  // Re-fetch via SSE
  await inspectFolder(path);

  if (btn) { btn.textContent = "↻ Reload data"; btn.disabled = false; }
}

// ── Start Load ────────────────────────────────────────────────────────────

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function showStartLoadProgress(pct, processed, total, filename, folderName) {
  const wrap = $("startLoadWrap");
  if (!wrap) return;
  wrap.hidden = false;
  wrap.innerHTML = `
    <div class="slp-bar-row">
      <div class="slp-track"><div class="slp-bar" style="width:${pct}%"></div></div>
      <span class="slp-pct">${pct}%</span>
    </div>
    <div class="slp-detail">
      <span class="slp-folder-name">${escapeHtml(folderName || "")}</span>
      ${filename ? `<span class="slp-filename">${escapeHtml(filename)}</span>` : ""}
    </div>
    <div class="slp-stats">
      <span>${processed} / ${total} files</span>
    </div>
  `;
}

function showStartLoadProgressDone(total) {
  const wrap = $("startLoadWrap");
  if (!wrap) return;
  wrap.hidden = false;
  wrap.innerHTML = `<div class="slp-done">✓ All done — ${total} files cached</div>`;
  setTimeout(() => { wrap.hidden = true; }, 3500);
}

async function startLoadAll() {
  const dirRows = Array.from(document.querySelectorAll("#browser .row.dir"));
  if (!dirRows.length) return;

  const btn = $("btnStartLoad");
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }

  const dirs = dirRows.map((r) => decodeURIComponent(r.dataset.path));

  // Fetch file counts for accurate ETA
  let folderCounts = {};
  let totalFiles = 0;
  try {
    const resp = await fetch(`/api/count-folder-files?dir=${encodeURIComponent(state.currentDir)}`);
    const data = await resp.json();
    folderCounts = data.counts || {};
    totalFiles = data.total || 0;
  } catch (_e) {
    totalFiles = dirs.length * 20; // rough fallback
  }

  let overallProcessed = 0;
  showStartLoadProgress(0, 0, totalFiles, "", "");

  for (const dirPath of dirs) {
    const folderName = dirPath.split(/[\\/]/).pop();
    const folderFileCount = folderCounts[dirPath] || 0;

    if (state.folderCache.has(dirPath)) {
      overallProcessed += folderFileCount;
      const pct = totalFiles > 0 ? Math.min(Math.round(overallProcessed / totalFiles * 100), 100) : 0;
      showStartLoadProgress(pct, overallProcessed, totalFiles, "", folderName);
      continue;
    }

    const baseCount = overallProcessed;
    await new Promise((resolve) => {
      const es = new EventSource(`/api/folder-inspect-stream?dir=${encodeURIComponent(dirPath)}`);
      es.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (msg.done) {
          es.close();
          state.folderCache.set(dirPath, msg);
          saveToPersistedCache(dirPath, msg);
          refreshBrowserCacheIndicators();
          overallProcessed = baseCount + folderFileCount;
          resolve();
        } else {
          const fileIdx = msg.current || 0;
          overallProcessed = baseCount + fileIdx;
          const pct = totalFiles > 0 ? Math.min(Math.round(overallProcessed / totalFiles * 100), 99) : 0;
          showStartLoadProgress(pct, overallProcessed, totalFiles, msg.file || "", folderName);
        }
      };
      es.onerror = () => { es.close(); resolve(); };
    });
  }

  showStartLoadProgressDone(totalFiles);
  updateCachePanel();
  if (btn) { btn.disabled = false; btn.textContent = "Start load"; }
}

// ── Dataset info ──────────────────────────────────────────────────────────

// temps is already resolved by renderFolderInspection (filename or info-text fallback)
function renderDatasetInfo(folderName, temps = []) {
  const card = $("datasetInfoCard");
  if (!card) return;
  const key = folderName.replace(/-/g, "_");
  const info = state.datasetInfo[key] || state.datasetInfo[folderName] || state.datasetInfo[key.toUpperCase()];

  if (!info) { card.hidden = true; return; }
  card.hidden = false;
  card.innerHTML = `
    <span class="dataset-info-label">Dataset info</span>
    <p class="dataset-info-text">${escapeHtml(info)}</p>
  `;
}

async function inspectFolder(path) {
  showErr("loadErr", "");
  showErr("plotErr", "");
  state.selectedFolder = path;
  localStorage.setItem(LAST_SELECTED_KEY, path);
  $("folderInspectTarget").textContent = path;
  // Highlight in sidebar
  document.querySelectorAll("#browser .row.selected").forEach(r => r.classList.remove("selected"));
  const encoded = encodeURIComponent(path);
  const row = document.querySelector(`#browser .row[data-path="${encoded}"]`);
  if (row) row.classList.add("selected");
  const reloadBtn = $("btnReloadFolder");
  if (reloadBtn) reloadBtn.disabled = false;
  // Stay on whichever tab the user is currently on — don't force a switch.
  renderDatasetInfo(path.split(/[\\/]/).pop());
  _updateLogfeatFolderBanner();

  if (state.folderCache.has(path)) {
    await renderFolderInspection(state.folderCache.get(path));
    return;
  }

  renderFolderStats({ file_count: 0, valid_file_count: 0, failed_file_count: 0 });
  $("folderTableCard").hidden = true;
  showFolderProgress(0, 0, 0, "");

  await new Promise((resolve) => {
    const es = new EventSource(`/api/folder-inspect-stream?dir=${encodeURIComponent(path)}`);

    es.onmessage = async (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.done) {
        es.close();
        state.folderCache.set(path, msg);
        saveToPersistedCache(path, msg);
        refreshBrowserCacheIndicators();
        await renderFolderInspection(msg);
        resolve();
      } else {
        showFolderProgress(msg.progress, msg.current, msg.total, msg.file);
      }
    };

    es.onerror = () => {
      es.close();
      showErr("loadErr", "Failed to load folder data");
      resetFolderInspection("Could not inspect folder", "Connection error");
      resolve();
    };
  });
}

function showFolderProgress(pct, current, total, filename) {
  $("folderPlotGrid").innerHTML = `
    <div class="folder-progress">
      <div class="folder-progress-info">
        <span class="folder-progress-label">
          ${total > 0 ? `Processing file ${current} of ${total}` : "Preparing…"}
        </span>
        ${filename ? `<span class="folder-progress-file">${escapeHtml(filename)}</span>` : ""}
      </div>
      <div class="folder-progress-row">
        <div class="folder-progress-track">
          <div class="folder-progress-bar" style="width:${pct}%"></div>
        </div>
        <span class="folder-progress-pct">${pct}%</span>
      </div>
    </div>
  `;
}

function resetFolderInspection(title, message) {
  $("folderPlotGrid").innerHTML = `
    <div class="empty-state">
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

async function renderFolderInspection(data) {
  const rows = data.rows || [];
  const folderName = data.folder_name || (state.selectedFolder || "").split(/[\\/]/).pop();

  // Resolve temperatures from the README prose so we can (a) fill the per-file
  // column when a filename carries no temperature and (b) show the stat box.
  const key = folderName.replace(/-/g, "_");
  const info = state.datasetInfo[key] || state.datasetInfo[folderName] || state.datasetInfo[key.toUpperCase()];
  const infoTemps = extractTempFromInfoText(info);
  // Per-file fallback only when the dataset has a single ambient temperature —
  // multi-temperature datasets must come from the filename or stay unknown.
  state.folderFallbackTemp = infoTemps.length === 1 ? infoTemps[0] : null;

  // Stat box: filename temps (now incl. the fallback via rowTemp), else README list
  let temps = uniqueTemps(rows);
  if (temps.length === 0) temps = infoTemps;

  renderFolderStats(data, temps);
  renderFolderRows(rows);
  renderDatasetInfo(folderName, temps);
  // Refresh the Feature Analyse compare-bar dropdowns with this folder's PKL files.
  populateFeatFileDropdowns();
  // Always rebuild client-side so the figure reflects current chart logic
  // (older cached `data.figure` payloads may still contain the old EOL scatter trace)
  const figure = buildFolderFigure(data.rows || [], folderName);

  if (!figure) {
    $("folderPlotGrid").innerHTML = `
      <div class="empty-state">
        <strong>No cycle data found</strong>
        <span>This folder has ${escapeHtml(data.file_count || 0)} PKL files, but none could be plotted.</span>
      </div>
    `;
    return;
  }

  const grid = $("folderPlotGrid");
  const title = figureTitle(figure, "Maximum cycle per file");
  const chartId = `folder_plot_${Date.now()}`;
  grid.innerHTML = plotCardMarkup(chartId, title);
  const card = grid.lastElementChild;
  const chart = $(chartId);
  await Plotly.newPlot(chart, figure.data, figure.layout, plotConfig);
  themePlot(chart);

  // Store original trace data for sort reset
  const t0 = figure.data[0];
  chart._origBarData = {
    x: Array.from(t0.x || []),
    y: Array.from(t0.y || []),
    customdata: Array.from(t0.customdata || []),
    colors: Array.from(t0.marker?.color || []),
  };

  chart._folderRows = data.rows || [];
  chart._eolBarIndex = null;   // which bar currently shows EOL marker

  state.charts.set(chartId, chart);
  wirePlotCard(card, chart, title);
  wireFolderSortControls(card, chart);
  applyFolderSort(chart, "asc");
  buildAxisEditor(chart, card.querySelector(".axis-editor"));
  chart.on("plotly_relayout", () => syncAxisEditorValues(chart));

  // Bar click → detail panel + EOL shape inside the bar
  card.insertAdjacentHTML("beforeend", `<div class="bar-detail-panel" hidden></div>`);
  chart.on("plotly_click", (event) => {
    const pt = event?.points?.[0];
    if (!pt) return;
    const row = (chart._folderRows || []).find((r) => r.name === pt.x);
    if (row) showBarDetail(card.querySelector(".bar-detail-panel"), row);

    // EOL shape: customdata[1] is eol_80_cycle (-1 = none)
    const eolCycle = pt.customdata?.[1] ?? -1;
    const idx = pt.pointIndex;

    if (eolCycle > 0) {
      if (chart._eolBarIndex === idx) {
        // Toggle off — same bar clicked again
        Plotly.relayout(chart, { shapes: [] });
        chart._eolBarIndex = null;
      } else {
        // Draw horizontal EOL line INSIDE the bar
        Plotly.relayout(chart, {
          shapes: [{
            type: "line",
            xref: "x",
            yref: "y",
            x0: idx - 0.42,
            x1: idx + 0.42,
            y0: eolCycle,
            y1: eolCycle,
            line: { color: "#dc2626", width: 3, dash: "solid" },
          }],
        });
        chart._eolBarIndex = idx;
      }
    } else {
      // Bar has no EOL data — clear any existing marker
      Plotly.relayout(chart, { shapes: [] });
      chart._eolBarIndex = null;
    }
  });
}

function showBarDetail(panel, row) {
  const fmt = (v, unit, dec = 4) =>
    v != null ? Number(v).toFixed(dec) + "\u202f" + unit : "—";
  const pct = (v) => (v != null ? Number(v).toFixed(1) + "%" : "—");

  panel.hidden = false;
  panel.innerHTML = `
    <div class="bar-detail-head">
      <span class="bar-detail-title" title="${escapeHtml(row.path || row.name)}">${escapeHtml(row.name)}</span>
      <button type="button" class="bar-detail-close" aria-label="Close">✕</button>
    </div>
    <div class="bar-detail-grid">
      <div class="detail-metric">
        <div class="detail-metric-label">Max charge current</div>
        <div class="detail-metric-value">${fmt(row.max_charge_current, "A", 4)}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Max discharge current</div>
        <div class="detail-metric-value detail-metric-neg">${fmt(row.max_discharge_current, "A", 4)}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Max Qd</div>
        <div class="detail-metric-value">${fmt(row.qd_max, "Ah")}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Min Qd</div>
        <div class="detail-metric-value">${fmt(row.qd_min, "Ah")}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Max Qc</div>
        <div class="detail-metric-value">${fmt(row.qc_max, "Ah")}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Min Qc</div>
        <div class="detail-metric-value">${fmt(row.qc_min, "Ah")}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Qd fade</div>
        <div class="detail-metric-value detail-metric-fade">${pct(row.qd_fade_pct)}</div>
      </div>
      <div class="detail-metric">
        <div class="detail-metric-label">Qc fade</div>
        <div class="detail-metric-value detail-metric-fade">${pct(row.qc_fade_pct)}</div>
      </div>
      ${row.eol_80_cycle > 0 ? `
      <div class="detail-metric detail-metric--eol">
        <div class="detail-metric-label">EOL @ 80% Qd</div>
        <div class="detail-metric-value detail-metric-eol">cycle ${row.eol_80_cycle}</div>
      </div>` : ""}
    </div>
    ${row.eol_80_cycle > 0 ? `<p class="bar-detail-eol-hint">🔴 Red line on chart = EOL cycle. Click bar again to hide.</p>` : ""}
  `;
  panel.querySelector(".bar-detail-close").addEventListener("click", () => {
    panel.hidden = true;
  });
}

function wireFolderSortControls(card, chart) {
  const head = card.querySelector(".plot-card-head");
  const sortHtml = `
    <div class="sort-group" role="group" aria-label="Sort order">
      <button type="button" class="sort-btn" data-sort="original" title="Original order">
        <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true"><rect x="1" y="1" width="11" height="2.5" rx="1.25" fill="currentColor" opacity=".55"/><rect x="1" y="5.25" width="8" height="2.5" rx="1.25" fill="currentColor" opacity=".75"/><rect x="1" y="9.5" width="5" height="2.5" rx="1.25" fill="currentColor"/></svg>
        Original
      </button>
      <button type="button" class="sort-btn active" data-sort="asc" title="Sort low to high">
        <svg width="11" height="13" viewBox="0 0 11 13" fill="none" aria-hidden="true"><path d="M5.5 1v11M2 8.5l3.5 3.5 3.5-3.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Low → High
      </button>
      <button type="button" class="sort-btn" data-sort="desc" title="Sort high to low">
        <svg width="11" height="13" viewBox="0 0 11 13" fill="none" aria-hidden="true"><path d="M5.5 12V1M2 4.5L5.5 1 9 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
        High → Low
      </button>
    </div>
  `;
  head.insertAdjacentHTML("afterbegin", sortHtml);
  head.querySelectorAll(".sort-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      head.querySelectorAll(".sort-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      applyFolderSort(chart, btn.dataset.sort);
    });
  });
}

function applyFolderSort(chart, order) {
  const orig = chart._origBarData;
  const n = orig.x.length;
  let indices = Array.from({ length: n }, (_, i) => i);
  if (order === "asc") indices.sort((a, b) => orig.y[a] - orig.y[b]);
  else if (order === "desc") indices.sort((a, b) => orig.y[b] - orig.y[a]);
  const newOrder = indices.map((i) => orig.x[i]);
  Plotly.restyle(
    chart,
    {
      x: [newOrder],
      y: [indices.map((i) => orig.y[i])],
      customdata: [indices.map((i) => orig.customdata[i])],
      "marker.color": [indices.map((i) => orig.colors[i])],
    },
    [0],
  );
  // Clear any EOL shape — positions shift after sort
  chart._eolBarIndex = null;
  Plotly.relayout(chart, {
    shapes: [],
    "xaxis.categoryorder": "array",
    "xaxis.categoryarray": newOrder,
  });
}

function renderFolderRows(rows) {
  const table = $("folderTableCard");
  const body = $("folderFileRows");
  table.hidden = !rows.length;
  state.currentRowsForFilter = rows;

  const fmt = (v, dec = 4) => (v != null ? Number(v).toFixed(dec) : "—");
  const pct = (v) => (v != null ? Number(v).toFixed(1) + "%" : "—");

  body.innerHTML = rows.map((row) => {
    const t = rowTemp(row);
    return `
    <tr data-temp="${t != null ? t : ""}">
      <td title="${escapeHtml(row.path || row.name)}">${escapeHtml(row.name)}</td>
      <td>${t != null ? t : "—"}</td>
      <td>${escapeHtml(row.cycle_count ?? "—")}</td>
      <td>${fmt(row.max_charge_current)}</td>
      <td>${fmt(row.max_discharge_current)}</td>
      <td>${fmt(row.qd_max)}</td>
      <td>${fmt(row.qd_min)}</td>
      <td>${fmt(row.qc_max)}</td>
      <td>${fmt(row.qc_min)}</td>
      <td>${pct(row.qd_fade_pct)}</td>
      <td>${pct(row.qc_fade_pct)}</td>
    </tr>`;
  }).join("");

  buildTempFilter(rows);
  applyTempFilter();
}

function buildTempFilter(rows) {
  const wrap = $("tempFilterWrap");
  const menu = $("tempFilterMenu");
  if (!wrap || !menu) return;

  const temps = uniqueTemps(rows);
  if (temps.length < 2) {
    wrap.hidden = true;
    state.activeTempFilter = null;
    return;
  }
  wrap.hidden = false;

  const counts = {};
  for (const r of rows) {
    const t = rowTemp(r);
    if (t != null) counts[t] = (counts[t] || 0) + 1;
  }

  state.activeTempFilter = new Set(temps);

  menu.innerHTML = temps.map((t) =>
    `<label><input type="checkbox" data-temp="${t}" checked> ${t} °C <span style="opacity:.55">(${counts[t]})</span></label>`
  ).join("");

  menu.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      const t = Number(cb.dataset.temp);
      if (!state.activeTempFilter) state.activeTempFilter = new Set(temps);
      if (cb.checked) state.activeTempFilter.add(t);
      else state.activeTempFilter.delete(t);
      applyTempFilter();
      updateTempFilterBtn(temps);
    });
  });

  updateTempFilterBtn(temps);
}

function updateTempFilterBtn(allTemps) {
  const btn = $("tempFilterBtn");
  if (!btn) return;
  const sel = state.activeTempFilter;
  if (!sel || sel.size === allTemps.length) {
    btn.textContent = "Temperature: All ▾";
  } else if (sel.size === 0) {
    btn.textContent = "Temperature: None ▾";
  } else {
    const list = [...sel].sort((a, b) => a - b).join(", ");
    btn.textContent = `Temperature: ${list} °C ▾`;
  }
}

function applyTempFilter() {
  const sel = state.activeTempFilter;
  document.querySelectorAll("#folderFileRows tr").forEach((tr) => {
    const raw = tr.dataset.temp;
    if (raw === "" || raw == null) {
      tr.hidden = false;
    } else if (!sel) {
      tr.hidden = false;
    } else {
      tr.hidden = !sel.has(Number(raw));
    }
  });
}

function renderBrowser(data) {
  const browser = $("browser");
  const rows = [];
  browser.hidden = false;

  if (data.parent) {
    rows.push(browserRow("up", data.parent, ".. (parent)", ""));
  }

  if (!data.entries.length) {
    rows.push(`<div class="empty-folder">${data.parent ? "No subfolders or .pkl files here" : "Empty folder"}</div>`);
  }

  for (const entry of data.entries) {
    rows.push(browserRow(entry.type, entry.path, entry.name, entry.type === "pkl" ? fmtSize(entry.size) : ""));
  }

  browser.innerHTML = rows.join("");
  browser.querySelectorAll(".row").forEach((row) => {
    let clickTimer = null;
    row.addEventListener("dblclick", () => {
      clearTimeout(clickTimer);
      const type = row.dataset.type;
      const path = decodeURIComponent(row.dataset.path);
      if (type === "dir" || type === "up") browseDir(path, { skipRestore: true });
      if (type === "dir") {
        // Register as selected folder so Feature Analyse log plots can use it immediately.
        state.selectedFolder = path;
        localStorage.setItem(LAST_SELECTED_KEY, path);
        $("folderInspectTarget").textContent = path;
        renderDatasetInfo(path.split(/[\\/]/).pop());
        syncFeatFolderMode();
        _updateLogfeatFolderBanner();   // show folder name in Plot Log feature banner
      }
      if (type === "pkl") loadPkl(path);
    });
    row.addEventListener("click", () => {
      browser.querySelectorAll(".row.selected").forEach((selected) => selected.classList.remove("selected"));
      row.classList.add("selected");
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        if (row.dataset.type === "dir") inspectFolder(decodeURIComponent(row.dataset.path));
        if (row.dataset.type === "pkl") loadPkl(decodeURIComponent(row.dataset.path));
      }, 220);
    });
  });
  // Keep the Feature Analyse compare dropdowns in sync with whatever's in the sidebar
  if (typeof populateFeatFileDropdowns === "function") populateFeatFileDropdowns();
}

function browserRow(type, path, name, size) {
  const label = type === "pkl" ? "PKL" : (type === "up" ? "UP" : "DIR");
  return `
    <div class="row ${escapeHtml(type)}" data-type="${escapeHtml(type)}" data-path="${encodeURIComponent(path)}">
      <span class="file-kind">${label}</span>
      <span class="row-name">${escapeHtml(name)}</span>
      <span class="row-size">${escapeHtml(size)}</span>
    </div>
  `;
}

async function loadPkl(path) {
  showErr("loadErr", "");
  showErr("plotErr", "");
  state.sessionId = null;
  setLoaded(false);
  $("loadText").textContent = `Loading ${path.split("/").pop()}...`;
  enablePlotControls(false);
  $("meta").hidden = true;
  resetPlotGrid();

  const response = await fetch("/api/load-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showErr("loadErr", data.detail || response.statusText);
    setLoaded(false);
    return;
  }

  state.sessionId = data.session_id;
  state.loadedFileName = data.name;
  setLoaded(true, data.name);
  renderMeta(data.meta);
  enablePlotControls(true);
  refreshFeatPinButton();
  // Stay on whichever tab the user is currently on — don't force a switch.
}

// ── Feature compare: two-file dropdowns ──────────────────────────────────

async function loadFileSession(path) {
  const response = await fetch("/api/load-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
  }
  return { sessionId: data.session_id, name: data.name };
}

function pklRowsForCurrentFolder() {
  // 1) Prefer the live sidebar listing — always matches what's shown on the left.
  const sidebarRows = pklEntriesFromSidebar();
  if (sidebarRows.length) return sidebarRows;
  // 2) Fall back to the inspected-folder cache.
  if (state.selectedFolder && state.folderCache.has(state.selectedFolder)) {
    return state.folderCache.get(state.selectedFolder).rows || [];
  }
  return [];
}

// Read every PKL row currently shown in the sidebar browser. Returns
// [{name, path, size?}] in display order.
function pklEntriesFromSidebar() {
  const browser = $("browser");
  if (!browser) return [];
  const rows = browser.querySelectorAll(".row.pkl");
  const out = [];
  rows.forEach((row) => {
    const path = decodeURIComponent(row.dataset.path || "");
    if (!path) return;
    const nameEl = row.querySelector(".row-name");
    const name = nameEl ? nameEl.textContent.trim() : path.split(/[\\/]/).pop();
    out.push({ name, path });
  });
  return out;
}

function populateFeatFileDropdowns() {
  const a = $("featFileA");
  const b = $("featFileB");
  if (!a || !b) return;
  const rows = pklRowsForCurrentFolder();
  const opts = [`<option value="">— pick a file —</option>`].concat(
    rows.map(r => `<option value="${escapeHtml(r.path || r.name)}">${escapeHtml(r.name)}</option>`)
  ).join("");
  a.innerHTML = opts;
  b.innerHTML = opts;
  a.disabled = rows.length === 0;
  b.disabled = rows.length === 0 || !$("featCompareEnable").checked;
  // Pre-select File A to match the currently loaded file if it's in this folder.
  if (state.loadedFileName) {
    const match = rows.find(r => r.name === state.loadedFileName);
    if (match) a.value = match.path || match.name;
  }
  syncFeatCompareUi();
}

function syncFeatCompareUi() {
  const enabled = $("featCompareEnable").checked;
  $("featFileBWrap").hidden = !enabled;
  const rows = pklRowsForCurrentFolder();
  $("featFileB").disabled = !enabled || rows.length === 0;
  const info = $("featCompareInfo");
  if (rows.length === 0) {
    info.textContent = "Open a folder containing .pkl files in the sidebar.";
  } else if (enabled) {
    info.textContent = state.compareSessionId
      ? `→ Plotting File A (blue/red) vs File B (orange/cyan) from ${state.currentDir || ""}`
      : "Pick a file in each dropdown above.";
  } else {
    info.textContent = "Tick the box to overlay a second file from the same folder.";
  }
}

async function onFeatFileAChange() {
  const path = $("featFileA").value;
  if (!path) return;
  showErr("featPlotErr", "");
  try {
    const { sessionId, name } = await loadFileSession(path);
    state.sessionId = sessionId;
    state.loadedFileName = name;
    setLoaded(true, name);
    enablePlotControls(true);
    syncFeatCompareUi();
  } catch (error) {
    showErr("featPlotErr", `Could not load File A: ${error.message}`);
  }
}

async function onFeatFileBChange() {
  const path = $("featFileB").value;
  if (!path) {
    state.compareSessionId = null;
    state.compareFileName = null;
    syncFeatCompareUi();
    return;
  }
  showErr("featPlotErr", "");
  try {
    const { sessionId, name } = await loadFileSession(path);
    state.compareSessionId = sessionId;
    state.compareFileName = name;
    syncFeatCompareUi();
  } catch (error) {
    showErr("featPlotErr", `Could not load File B: ${error.message}`);
  }
}

async function doPlot() {
  showErr("plotErr", "");
  if (!state.sessionId) {
    showErr("plotErr", "Load a file first.");
    return;
  }

  const kind = $("plotKind").value;
  const isCapacityPlot = kind === "qcmax" || kind === "qdmax";
  const body = {
    kind,
    cycles: isCapacityPlot ? null : readCycleSpec(),
    filter_outliers: $("filterOutliers").checked,
  };

  $("plotLoading").hidden = false;
  $("genPlot").disabled = true;
  try {
    const response = await fetch(`/api/session/${state.sessionId}/plot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join(" ")
        : (data.detail || response.statusText);
      showErr("plotErr", detail);
      return;
    }
    await renderFigures(data.figures || []);
  } catch (error) {
    showErr("plotErr", String(error));
  } finally {
    $("plotLoading").hidden = true;
    $("genPlot").disabled = false;
  }
}

function isLogavgKind(kind) {
  return /^log(avg|max|min)_/.test(kind);
}

// ── Feature Analyse sub-tabs ─────────────────────────────────────────────
// Short explanation shown under the sub-tabs for each Feature Analyse mode.
const FEAT_MODE_HELP = {
  single:
    "Plot single renders the selected feature for one cell at a time. Pick a file " +
    "from the folder, choose a plot type, and select the cycles to show. Tick " +
    "<strong>Use reference</strong> to subtract a chosen reference cycle from every " +
    "plotted cycle, so each curve shows the change relative to that baseline.",
  compare:
    "Compare two overlays the same feature from two cells on a single chart for a " +
    "side-by-side comparison. Pick <strong>File A</strong> and <strong>File B</strong> " +
    "from the folder, then choose a plot type and the cycles to show. Tick " +
    "<strong>Use reference</strong> to subtract the reference cycle from each file " +
    "before plotting, so you compare how each cell deviates from its own baseline.",
  logfeat:
    "Plot Log feature compares <strong>every cell in the selected folder</strong>. " +
    "Each plot summarises how the cells differ at one specific cycle: a point is drawn " +
    "per cell at the chosen <strong>target cycle</strong>, plotted against cycle life. " +
    "A reference is optional — when <strong>Use reference</strong> is enabled, each " +
    "cell's data has its reference-cycle data subtracted before the log feature is computed.",
};

function _updateFeatModeHelp(name) {
  const el = $("featModeHelp");
  if (el) el.innerHTML = FEAT_MODE_HELP[name] || "";
}

// "single" → standard single-file Δ plots (compare bar hidden, folder mode off)
// "compare" → compare-two-files plots (compare bar shown + auto-checked)
// "logfeat" → log⟨|Δ ...|⟩ folder scatter (compare bar hidden, folder mode on)
function switchFeatSubTab(name) {
  document.querySelectorAll("#featSubTabs .sub-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.subtab === name);
  });

  const compareBar = $("featCompareBar");
  const folderBar = $("featFolderBar");
  const cyclesField = $("featCyclesField");
  const targetField = $("featTargetCycleField");
  const compareEnable = $("featCompareEnable");
  const folderMode = $("featFolderMode");

  if (name === "single") {
    compareBar.hidden = true;
    folderBar.hidden = true;
    cyclesField.hidden = false;
    targetField.hidden = true;
    compareEnable.checked = false;
    folderMode.checked = false;
    state.compareSessionId = null;
    state.compareFileName = null;
  } else if (name === "compare") {
    compareBar.hidden = false;
    folderBar.hidden = true;
    cyclesField.hidden = false;
    targetField.hidden = true;
    compareEnable.checked = true;     // auto-on for this sub-tab
    $("featFileBWrap").hidden = false;
    folderMode.checked = false;
  } else if (name === "logfeat") {
    compareBar.hidden = true;
    folderBar.hidden = true;
    cyclesField.hidden = true;
    targetField.hidden = false;
    compareEnable.checked = false;
    folderMode.checked = true;
    state.compareSessionId = null;
    state.compareFileName = null;
  }

  // Update the mode explanation under the sub-tabs
  _updateFeatModeHelp(name);

  // Show/hide the logfeat folder indicator
  const logfeatBar = $("logfeatFolderBar");
  if (logfeatBar) logfeatBar.hidden = (name !== "logfeat");
  if (name === "logfeat") _updateLogfeatFolderBanner();

  filterFeatPlotKindOptions(name);
  syncFeatCompareUi();
  syncFeatFolderMode();
  syncFeatReferenceUi();
}

// Hide options in the plot dropdown that don't apply to the current sub-tab.
function filterFeatPlotKindOptions(subtab) {
  const select = $("featPlotKind");
  if (!select) return;
  const allowsLog = subtab === "logfeat";
  let firstVisible = null;
  Array.from(select.options).forEach(opt => {
    const isLog = isLogavgKind(opt.value);
    const show = allowsLog ? isLog : !isLog;
    opt.hidden = !show;
    opt.disabled = !show;
    if (show && firstVisible === null) firstVisible = opt.value;
  });
  // If the current selection became hidden, jump to the first visible option.
  const cur = select.options[select.selectedIndex];
  if (!cur || cur.hidden) {
    if (firstVisible !== null) select.value = firstVisible;
  }
}

function activeFeatSubTab() {
  const btn = document.querySelector("#featSubTabs .sub-tab.active");
  return btn ? btn.dataset.subtab : "single";
}

function syncFeatFolderMode() {
  const kind = $("featPlotKind").value;
  const cb = $("featFolderMode");
  const supported = isLogavgKind(kind);
  cb.disabled = !supported;
  if (!supported) cb.checked = false;
  $("featFolderTargetWrap").hidden = !cb.checked;
  $("featFolderInfo").textContent = supported
    ? (cb.checked
        ? `→ Bar chart per file in: ${state.selectedFolder || "(no folder)"}`
        : "✓ Available — tick to plot across the whole selected folder")
    : "Folder mode supports only log(avg/max/min|Δ ...|) kinds";
}

function syncFeatReferenceUi() {
  const useRef = $("featUseRefCycle").checked;
  const controlsEnabled = !$("featPlotKind").disabled;
  $("featRefCycle").disabled = !controlsEnabled || !useRef;
}

function _updateLogfeatFolderBanner() {
  const nameEl = $("logfeatFolderName");
  if (!nameEl) return;
  const folder = state.selectedFolder || state.currentDir;
  if (folder) {
    const folderShort = folder.replace(/\\/g, "/").split("/").pop();
    nameEl.textContent = folderShort;
    nameEl.title = folder;       // full path on hover
    nameEl.classList.remove("empty");
  } else {
    nameEl.textContent = "— double-click a subfolder in the sidebar —";
    nameEl.title = "";
    nameEl.classList.add("empty");
  }
}


async function doFeaturePlot() {
  showErr("featPlotErr", "");
  const subtab = activeFeatSubTab();
  const kind = $("featPlotKind").value;
  const useReferenceCycle = $("featUseRefCycle").checked;

  let referenceCycle = 0;
  if (useReferenceCycle) {
    const refRaw = $("featRefCycle").value.trim();
    if (refRaw === "" || isNaN(Number(refRaw))) {
      showErr("featPlotErr", "Enter a reference cycle (integer).");
      return;
    }
    referenceCycle = parseInt(refRaw, 10);
  }

  // ── Plot Log feature sub-tab: folder scatter
  if (subtab === "logfeat") {
    if (!isLogavgKind(kind)) {
      showErr("featPlotErr", "Pick a log(avg/max/min|Δ ...|) plot kind on this sub-tab.");
      return;
    }
    const folder = state.selectedFolder || state.currentDir;
    if (!folder) {
      showErr("featPlotErr", "Open a subfolder in the sidebar first.");
      return;
    }
    const targetRaw = $("featTargetCycleInline").value.trim();
    if (targetRaw === "" || isNaN(Number(targetRaw))) {
      showErr("featPlotErr", "Enter a target cycle.");
      return;
    }
    const targetCycle = parseInt(targetRaw, 10);
    if (useReferenceCycle && targetCycle === referenceCycle) {
      showErr("featPlotErr", "Target cycle must differ from reference cycle.");
      return;
    }
    const body = {
      folder_path: folder,
      kind,
      reference_cycle: referenceCycle,
      use_reference_cycle: useReferenceCycle,
      target_cycle: targetCycle,
      filter_outliers: $("featFilterOutliers").checked,
    };

    $("featPlotLoading").hidden = false;
    $("featGenPlot").disabled = true;
    try {
      const response = await fetch("/api/folder-feature-logavg", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map((item) => item.msg).join(" ")
          : (data.detail || response.statusText);
        showErr("featPlotErr", detail);
        return;
      }
      await renderFeatureFigures(data.figures || []);
    } catch (error) {
      showErr("featPlotErr", String(error));
    } finally {
      $("featPlotLoading").hidden = true;
      $("featGenPlot").disabled = false;
    }
    return;
  }

  // ── Plot single / Compare two
  if (isLogavgKind(kind)) {
    showErr("featPlotErr", "log(avg/max/min|Δ ...|) kinds belong to the 'Plot Log feature' sub-tab.");
    return;
  }
  if (!state.sessionId) {
    showErr("featPlotErr", "Load a file first.");
    return;
  }
  const compareEnabled = subtab === "compare";
  if (compareEnabled && !state.compareSessionId) {
    showErr("featPlotErr", "Pick File A and File B in the dropdowns above.");
    return;
  }

  const body = {
    kind,
    cycles: readCycleSpecFor("feat"),
    reference_cycle: referenceCycle,
    use_reference_cycle: useReferenceCycle,
    filter_outliers: $("featFilterOutliers").checked,
    compare_session_id: compareEnabled ? (state.compareSessionId || null) : null,
  };

  $("featPlotLoading").hidden = false;
  $("featGenPlot").disabled = true;
  try {
    const response = await fetch(`/api/session/${state.sessionId}/feature-plot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg).join(" ")
        : (data.detail || response.statusText);
      showErr("featPlotErr", detail);
      return;
    }
    await renderFeatureFigures(data.figures || []);
  } catch (error) {
    showErr("featPlotErr", String(error));
  } finally {
    $("featPlotLoading").hidden = true;
    $("featGenPlot").disabled = false;
  }
}

async function renderFeatureFigures(figures) {
  const grid = $("featPlotGrid");
  // Remove just our feature charts from state.charts to avoid leaking
  for (const id of Array.from(state.charts.keys())) {
    if (id.startsWith("feat_plot_")) state.charts.delete(id);
  }

  if (!figures.length) {
    grid.innerHTML = `
      <div class="empty-state">
        <strong>No valid plot data</strong>
        <span>Try another plot type, reference cycle, or cycle selection.</span>
      </div>
    `;
    return;
  }

  grid.innerHTML = "";
  for (let index = 0; index < figures.length; index += 1) {
    const figure = figures[index];
    const title = figureTitle(figure, `Feature plot ${index + 1}`);
    const chartId = `feat_plot_${Date.now()}_${index}`;
    grid.insertAdjacentHTML("beforeend", plotCardMarkup(chartId, title));
    const card = grid.lastElementChild;
    const chart = $(chartId);
    await Plotly.newPlot(chart, figure.data, figure.layout, plotConfig);
    themePlot(chart);
    state.charts.set(chartId, chart);
    wirePlotCard(card, chart, title);
    buildAxisEditor(chart, card.querySelector(".axis-editor"));
    chart.on("plotly_relayout", () => syncAxisEditorValues(chart));
  }
}

function resetPlotGrid() {
  state.charts.clear();
  $("plotGrid").innerHTML = `
    <div class="empty-state">
      <strong>No plot yet</strong>
      <span>Load a file, choose a plot type, then generate the plot.</span>
    </div>
  `;
}

async function renderFigures(figures) {
  const grid = $("plotGrid");
  state.charts.clear();

  if (!figures.length) {
    grid.innerHTML = `
      <div class="empty-state">
        <strong>No valid plot data</strong>
        <span>Try another plot type or cycle selection.</span>
      </div>
    `;
    return;
  }

  grid.innerHTML = "";
  for (let index = 0; index < figures.length; index += 1) {
    const figure = figures[index];
    const title = figureTitle(figure, `Plot ${index + 1}`);
    const chartId = `plot_${Date.now()}_${index}`;
    grid.insertAdjacentHTML("beforeend", plotCardMarkup(chartId, title));
    const card = grid.lastElementChild;
    const chart = $(chartId);
    await Plotly.newPlot(chart, figure.data, figure.layout, plotConfig);
    themePlot(chart);
    state.charts.set(chartId, chart);
    wirePlotCard(card, chart, title);
    buildAxisEditor(chart, card.querySelector(".axis-editor"));
    chart.on("plotly_relayout", () => syncAxisEditorValues(chart));
  }
}

function plotCardMarkup(chartId, title) {
  return `
    <article class="plot-card">
      <div class="plot-card-head">
        <h3>${escapeHtml(title)}</h3>
        <div class="plot-actions">
          <button type="button" class="secondary" data-export="svg">SVG</button>
          <button type="button" class="secondary" data-export="pdf">PDF</button>
        </div>
      </div>
      <div class="chart-host" id="${escapeHtml(chartId)}"></div>
      <div class="axis-editor">
        <div class="axis-editor-head">
          <div class="axis-heading">Range sliders</div>
          <button type="button" class="secondary" data-axis-reset>Autoscale</button>
        </div>
        <div class="axis-rows"></div>
      </div>
    </article>
  `;
}

function wirePlotCard(card, chart, title) {
  card.querySelector('[data-export="svg"]').addEventListener("click", (event) => {
    runWithButton(event.currentTarget, "Saving", () => downloadPlotSvg(chart, title))
      .catch((error) => showErr("plotErr", String(error)));
  });
  card.querySelector('[data-export="pdf"]').addEventListener("click", (event) => {
    runWithButton(event.currentTarget, "Saving", () => downloadPlotPdf(chart, title))
      .catch((error) => showErr("plotErr", String(error)));
  });
  card.querySelector("[data-axis-reset]").addEventListener("click", async () => {
    const update = {};
    axisKeys(chart.layout).forEach((key) => {
      update[`${key}.autorange`] = true;
    });
    await Plotly.relayout(chart, update);
    buildAxisEditor(chart, card.querySelector(".axis-editor"));
  });
}

async function runWithButton(button, busyText, action) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    await action();
  } finally {
    button.textContent = originalText;
    button.disabled = false;
  }
}

function figureTitle(figure, fallback) {
  const raw = figure && figure.layout && figure.layout.title;
  const text = typeof raw === "string" ? raw : raw && raw.text;
  return stripTags(text || fallback);
}

function stripTags(value) {
  return String(value).replace(/<[^>]*>/g, "");
}

function axisKeys(layout) {
  return Object.keys(layout || {})
    .filter((key) => /^[xy]axis\d*$/.test(key))
    .sort((a, b) => axisOrder(a) - axisOrder(b));
}

function axisOrder(key) {
  const suffix = key.match(/\d+$/);
  const axisIndex = key.startsWith("x") ? 0 : 1;
  return axisIndex * 1000 + (suffix ? Number(suffix[0]) : 1);
}

function axisLabel(layout, key) {
  const title = layout[key] && layout[key].title;
  const text = (title && (title.text || (typeof title === "string" ? title : ""))) || key;
  return stripTags(text);
}

function currentRange(chart, key) {
  const layoutAxis = chart.layout[key] || {};
  const fullAxis = chart._fullLayout && chart._fullLayout[key];
  const raw = Array.isArray(layoutAxis.range) ? layoutAxis.range : (fullAxis && fullAxis.range);
  if (!Array.isArray(raw) || raw.length !== 2) return [null, null];
  const lo = Number(raw[0]);
  const hi = Number(raw[1]);
  return Number.isFinite(lo) && Number.isFinite(hi) ? [lo, hi] : [null, null];
}

function buildAxisEditor(chart, editor) {
  const rows = editor.querySelector(".axis-rows");
  const keys = axisKeys(chart.layout);
  rows.innerHTML = "";
  chart._axisBounds = {};

  keys.forEach((key) => {
    const [lo, hi] = currentRange(chart, key);
    if (lo == null || hi == null) return;
    const low = Math.min(lo, hi);
    const high = Math.max(lo, hi);
    const padding = low === high ? Math.max(Math.abs(low) * 0.05, 1) : 0;
    chart._axisBounds[key] = [low - padding, high + padding];
  });

  keys.forEach((key) => {
    const [lo, hi] = currentRange(chart, key);
    if (lo == null || hi == null) return;
    const [boundLo, boundHi] = chart._axisBounds[key] || [Math.min(lo, hi), Math.max(lo, hi)];
    const span = Math.max(boundHi - boundLo, 1);
    const step = span / 1000;
    rows.insertAdjacentHTML("beforeend", axisRowMarkup(
      axisLabel(chart.layout, key),
      key,
      lo,
      hi,
      boundLo,
      boundHi,
      step,
    ));
    wireAxisRow(chart, rows.lastElementChild, key);
  });
}

function axisRowMarkup(label, key, lo, hi, boundLo, boundHi, step) {
  return `
    <div class="axis-row" data-axis-key="${escapeHtml(key)}">
      <div class="axis-label" title="${escapeHtml(label)}">${escapeHtml(label)}</div>
      <div class="axis-fields">
        <input type="text" data-role="min" aria-label="${escapeHtml(label)} minimum" value="${escapeHtml(fmtAxis(lo))}" />
        <input type="text" data-role="max" aria-label="${escapeHtml(label)} maximum" value="${escapeHtml(fmtAxis(hi))}" />
      </div>
      <div class="axis-slider">
        <input type="range" data-role="slider-min" min="${boundLo}" max="${boundHi}" step="${step}" value="${lo}" />
        <input type="range" data-role="slider-max" min="${boundLo}" max="${boundHi}" step="${step}" value="${hi}" />
      </div>
    </div>
  `;
}

function wireAxisRow(chart, row, key) {
  const minInput = row.querySelector('[data-role="min"]');
  const maxInput = row.querySelector('[data-role="max"]');
  const minSlider = row.querySelector('[data-role="slider-min"]');
  const maxSlider = row.querySelector('[data-role="slider-max"]');

  const relayout = (a, b) => {
    if (Number.isFinite(a) && Number.isFinite(b) && a < b) {
      Plotly.relayout(chart, { [`${key}.range`]: [a, b] });
    }
  };

  const applyText = () => {
    relayout(Number.parseFloat(minInput.value), Number.parseFloat(maxInput.value));
  };

  const applySliders = () => {
    let a = Number.parseFloat(minSlider.value);
    let b = Number.parseFloat(maxSlider.value);
    if (a > b) [a, b] = [b, a];
    minInput.value = fmtAxis(a);
    maxInput.value = fmtAxis(b);
    relayout(a, b);
  };

  minInput.addEventListener("change", applyText);
  maxInput.addEventListener("change", applyText);
  minInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyText();
  });
  maxInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyText();
  });
  minSlider.addEventListener("input", applySliders);
  maxSlider.addEventListener("input", applySliders);
}

function syncAxisEditorValues(chart) {
  const card = chart.closest(".plot-card");
  if (!card) return;
  axisKeys(chart.layout).forEach((key) => {
    const row = card.querySelector(`[data-axis-key="${key}"]`);
    if (!row) return;
    const [lo, hi] = currentRange(chart, key);
    if (lo == null || hi == null) return;
    syncControl(row.querySelector('[data-role="min"]'), fmtAxis(lo));
    syncControl(row.querySelector('[data-role="max"]'), fmtAxis(hi));
    syncControl(row.querySelector('[data-role="slider-min"]'), lo);
    syncControl(row.querySelector('[data-role="slider-max"]'), hi);
  });
}

function syncControl(control, value) {
  if (control && document.activeElement !== control) {
    control.value = value;
  }
}

function exportSize(chart) {
  const rect = chart.getBoundingClientRect();
  const full = chart._fullLayout || {};
  return {
    width: Math.max(640, Math.round(rect.width || full.width || 900)),
    height: Math.max(320, Math.round(full.height || rect.height || 420)),
  };
}

async function downloadPlotSvg(chart, title) {
  const size = exportSize(chart);
  await Plotly.downloadImage(chart, {
    format: "svg",
    filename: safeFilename(title),
    width: size.width,
    height: size.height,
    scale: 1,
  });
}

async function downloadPlotPdf(chart, title) {
  const size = exportSize(chart);
  const dataUrl = await Plotly.toImage(chart, {
    format: "jpeg",
    width: size.width,
    height: size.height,
    scale: 2,
  });
  const image = await loadImage(dataUrl);
  const jpegBytes = dataUrlToBytes(dataUrl);
  const pdfBytes = makeJpegPdf(jpegBytes, image.width, image.height, size.width, size.height);
  downloadBlob(pdfBytes, `${safeFilename(title)}.pdf`, "application/pdf");
}

function safeFilename(value) {
  const cleaned = String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned || "battery-plot";
}

function loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = reject;
    image.src = dataUrl;
  });
}

function dataUrlToBytes(dataUrl) {
  const base64 = dataUrl.split(",", 2)[1] || "";
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function makeJpegPdf(jpegBytes, imageWidth, imageHeight, pageWidth, pageHeight) {
  const encoder = new TextEncoder();
  const ascii = (text) => encoder.encode(text);
  const content = `q\n${pageWidth.toFixed(2)} 0 0 ${pageHeight.toFixed(2)} 0 0 cm\n/Im0 Do\nQ\n`;
  const contentBytes = ascii(content);
  const objects = [
    [ascii("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")],
    [ascii("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")],
    [ascii(`3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth.toFixed(2)} ${pageHeight.toFixed(2)}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>\nendobj\n`)],
    [
      ascii(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${imageWidth} /Height ${imageHeight} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpegBytes.length} >>\nstream\n`),
      jpegBytes,
      ascii("\nendstream\nendobj\n"),
    ],
    [
      ascii(`5 0 obj\n<< /Length ${contentBytes.length} >>\nstream\n`),
      contentBytes,
      ascii("endstream\nendobj\n"),
    ],
  ];

  const chunks = [ascii("%PDF-1.3\n")];
  const offsets = [0];
  let length = chunks[0].length;

  objects.forEach((parts) => {
    offsets.push(length);
    parts.forEach((part) => {
      chunks.push(part);
      length += part.length;
    });
  });

  const xrefOffset = length;
  const xrefRows = offsets.map((offset, index) => {
    if (index === 0) return "0000000000 65535 f \n";
    return `${String(offset).padStart(10, "0")} 00000 n \n`;
  }).join("");
  chunks.push(ascii(`xref\n0 ${objects.length + 1}\n${xrefRows}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`));

  return concatBytes(chunks);
}

function concatBytes(parts) {
  const length = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(length);
  let offset = 0;
  parts.forEach((part) => {
    out.set(part, offset);
    offset += part.length;
  });
  return out;
}

function downloadBlob(bytes, filename, type) {
  const blob = new Blob([bytes], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("pickFolder").addEventListener("click", pickFolder);
$("genPlot").addEventListener("click", doPlot);
$("cycleMode").addEventListener("change", syncCycleMode);
syncCycleMode();

$("featGenPlot").addEventListener("click", doFeaturePlot);
$("featCycleMode").addEventListener("change", syncFeatCycleMode);
syncFeatCycleMode();
$("featPlotKind").addEventListener("change", syncFeatFolderMode);
$("featUseRefCycle").addEventListener("change", syncFeatReferenceUi);
$("featFolderMode").addEventListener("change", syncFeatFolderMode);
syncFeatFolderMode();
syncFeatReferenceUi();
$("featCompareEnable").addEventListener("change", () => {
  if (!$("featCompareEnable").checked) {
    state.compareSessionId = null;
    state.compareFileName = null;
    $("featFileB").value = "";
  }
  syncFeatCompareUi();
});
$("featFileA").addEventListener("change", onFeatFileAChange);
$("featFileB").addEventListener("change", onFeatFileBChange);
syncFeatCompareUi();

// Feature Analyse sub-tabs
document.querySelectorAll("#featSubTabs .sub-tab").forEach(btn => {
  btn.addEventListener("click", () => switchFeatSubTab(btn.dataset.subtab));
});
switchFeatSubTab("single");
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});
window.addEventListener("resize", () => {
  state.charts.forEach((chart) => Plotly.Plots.resize(chart));
});

$("btnStartLoad").addEventListener("click", startLoadAll);
$("btnReloadFolder").addEventListener("click", reloadCurrentFolder);

$("tempFilterBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("tempFilterMenu");
  menu.hidden = !menu.hidden;
});
document.addEventListener("click", (e) => {
  const wrap = $("tempFilterWrap");
  const menu = $("tempFilterMenu");
  if (!menu || menu.hidden) return;
  if (!wrap.contains(e.target)) menu.hidden = true;
});
$("btnPreCacheAll").addEventListener("click", preCacheAllFolders);
$("btnExportCache").addEventListener("click", exportCacheJson);
$("btnClearCache").addEventListener("click", () => {
  if (confirm("Clear all cached folder data? You will need to re-scan folders.")) clearFolderCache();
});
$("btnImportCache").addEventListener("click", () => $("importCacheInput").click());
$("importCacheInput").addEventListener("change", (e) => {
  if (e.target.files[0]) importCacheJson(e.target.files[0]);
  e.target.value = "";
});

initTheme();
$("themeToggle").addEventListener("click", toggleTheme);

loadPersistedFolderCache();

fetch("/api/dataset-info")
  .then((r) => r.json())
  .then((d) => { state.datasetInfo = d.info || {}; })
  .catch(() => {});

fetch("/api/defaults")
  .then((response) => response.json())
  .then((data) => {
    const savedRoot = localStorage.getItem(LAST_ROOT_KEY);
    const root = savedRoot || (data.has_default_data_dir ? data.default_data_dir : null);
    if (root) {
      state.rootDir = root;
      browseDir(root).then(() => {
        refreshBrowserCacheIndicators();
        updatePreCacheButtonState();
      });
    }
  })
  .catch(() => {});
