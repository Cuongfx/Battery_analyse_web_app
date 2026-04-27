const $ = (id) => document.getElementById(id);

const state = {
  sessionId: null,
  currentDir: null,
  rootDir: null,
  selectedFolder: null,
  charts: new Map(),
  folderCache: new Map(),
  datasetInfo: {},
  activeTempFilter: null,    // Set<number> | null  (null = no filter, all visible)
  currentRowsForFilter: [],  // rows for current folder, used when filter changes
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
  ["plotKind", "cycles", "genPlot", "filterOutliers"].forEach((id) => {
    $(id).disabled = !enabled;
  });
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  $("generalPanel").hidden = name !== "general";
  $("deepPanel").hidden = name !== "deep";
  $("generalPanel").classList.toggle("active", name === "general");
  $("deepPanel").classList.toggle("active", name === "deep");
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
  return extractTempFromName(row.name);
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
  const re = /(-?\d+)\s*(?:degrees?\s*[Cc]elsius|°[Cc]|o[Cc])\b/g;
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
      <div class="stat-title">Test temperature</div>
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
  const cycleCounts = valid.map((r) => r.cycle_count || 0);

  const minC = Math.min(...maxCycles);
  const maxC = Math.max(...maxCycles);
  const span = Math.max(maxC - minC, 1);
  const colors = maxCycles.map((v) => {
    const t = (v - minC) / span;
    return `rgb(${Math.round(189 + (23 - 189) * t)},${Math.round(215 + (71 - 215) * t)},${Math.round(247 + (166 - 247) * t)})`;
  });

  // EOL @ 80% red line markers
  const eolX = [];
  const eolY = [];
  for (const r of valid) {
    if (r.eol_80_cycle != null && r.eol_80_cycle > 0) {
      eolX.push(r.name);
      eolY.push(r.eol_80_cycle);
    }
  }

  const traces = [{
    type: "bar",
    x: names,
    y: maxCycles,
    marker: { color: colors, line: { color: "rgba(23,71,166,0.35)", width: 0.6 } },
    customdata: cycleCounts,
    hovertemplate: "File: %{x}<br>Max cycle: %{y}<extra></extra>",
    showlegend: false,
  }];
  if (eolX.length) {
    traces.push({
      type: "scatter",
      mode: "markers",
      x: eolX,
      y: eolY,
      marker: { symbol: "line-ew", size: 24, line: { color: "#dc2626", width: 4 } },
      hovertemplate: "%{x}<br>EOL @ 80%: cycle %{y}<extra></extra>",
      showlegend: false,
    });
  }

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
  switchTab("general");
  renderDatasetInfo(path.split(/[\\/]/).pop());

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

  // Resolve temperatures: from filenames first, fall back to README info text
  let temps = uniqueTemps(rows);
  if (temps.length === 0) {
    const key = folderName.replace(/-/g, "_");
    const info = state.datasetInfo[key] || state.datasetInfo[folderName] || state.datasetInfo[key.toUpperCase()];
    temps = extractTempFromInfoText(info);
  }

  renderFolderStats(data, temps);
  renderFolderRows(rows);
  renderDatasetInfo(folderName, temps);
  const figure = data.figure || buildFolderFigure(data.rows || [], folderName);

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

  // Store original trace data for sort reset
  const t0 = figure.data[0];
  chart._origBarData = {
    x: Array.from(t0.x || []),
    y: Array.from(t0.y || []),
    customdata: Array.from(t0.customdata || []),
    colors: Array.from(t0.marker?.color || []),
  };

  chart._folderRows = data.rows || [];

  state.charts.set(chartId, chart);
  wirePlotCard(card, chart, title);
  wireFolderSortControls(card, chart);
  applyFolderSort(chart, "asc");
  buildAxisEditor(chart, card.querySelector(".axis-editor"));
  chart.on("plotly_relayout", () => syncAxisEditorValues(chart));

  // Bar click → detail panel
  card.insertAdjacentHTML("beforeend", `<div class="bar-detail-panel" hidden></div>`);
  chart.on("plotly_click", (event) => {
    const pt = event?.points?.[0];
    if (!pt) return;
    const row = (chart._folderRows || []).find((r) => r.name === pt.x);
    if (row) showBarDetail(card.querySelector(".bar-detail-panel"), row);
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
    </div>
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
  // Keep the EOL marker trace aligned to the same categorical order
  Plotly.relayout(chart, {
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
  setLoaded(true, data.name);
  renderMeta(data.meta);
  enablePlotControls(true);
  switchTab("deep");
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
    cycles: isCapacityPlot ? null : ($("cycles").value.trim() || "all"),
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
