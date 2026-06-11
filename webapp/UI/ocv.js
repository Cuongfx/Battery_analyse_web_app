/* OCV tab — slow GITT discharge + low-rate charge -> discharge/charge OCV,
   mean OCV and hysteresis. Separate pipeline from the HPPC/ECM subtab.
   Supports a single file or a whole folder (batch). */
(function () {
  "use strict";

  const eg = (id) => document.getElementById(id);

  const state = {
    initialized: false,
    mode: "single", // "single" | "folder"
    path: "",
    fileCount: 0,
    detected: null,
    result: null,
  };

  function fmt(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const x = Number(value);
    if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.01)) {
      return x.toExponential(3);
    }
    return Number(x.toFixed(4)).toString();
  }

  const mv = (v) => (v == null ? "—" : `${fmt(v * 1000)} mV`);
  const imgUrl = (p) => "/api/ecm/image?path=" + encodeURIComponent(p);
  const dlUrl = (p) => "/api/ecm/image?path=" + encodeURIComponent(p) + "&download=1";

  function plotFigure(caption, files, alt) {
    if (!files || !files.png) return "";
    const dl = [];
    if (files.svg) dl.push(`<a class="ecm-dl" href="${dlUrl(files.svg)}" download>SVG</a>`);
    if (files.pdf) dl.push(`<a class="ecm-dl" href="${dlUrl(files.pdf)}" download>PDF</a>`);
    return `<figure class="ecm-figure">
      <div class="ecm-figure-head">
        <figcaption>${caption}</figcaption>
        <div class="ecm-figure-actions">${dl.join("")}</div>
      </div>
      <img class="ecm-plot-img" src="${imgUrl(files.png)}" alt="${alt || caption}" title="Click to enlarge" />
    </figure>`;
  }

  function showErr(msg) {
    const el = eg("ocvErr");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  async function getJSON(url) {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  // ---- stepper ----------------------------------------------------------- //
  function setStep(n) {
    document.querySelectorAll("#ocvStepper .ecm-step").forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("active", s === n);
      li.classList.toggle("done", s < n);
    });
    eg("ocvStage1").hidden = n !== 1;
    eg("ocvStage2").hidden = n !== 2;
    showErr("");
  }

  // ---- step 1: select + detect ------------------------------------------- //
  function updateModeUI() {
    const single = state.mode === "single";
    eg("ocvPick").textContent = single ? "Choose file" : "Choose folder";
    eg("ocvPath").textContent = single ? "No file selected" : "No folder selected";
    eg("ocvDetectCap").hidden = !single;
    eg("ocvFolderInfo").hidden = true;
    eg("ocvStep1Next").disabled = true;
    state.path = "";
  }

  async function pick() {
    showErr("");
    try {
      const { path } = await postJSON("/api/ecm/pick", { kind: state.mode === "folder" ? "folder" : "xlsx" });
      if (!path) return;
      state.path = path;
      eg("ocvPath").textContent = path;
      if (state.mode === "single") {
        eg("ocvStep1Next").disabled = false;
        eg("ocvDetectCap").disabled = false;
        detectCapacity();
      } else {
        const scan = await getJSON("/api/ecm/scan-folder?dir=" + encodeURIComponent(path));
        state.fileCount = scan.count;
        const info = eg("ocvFolderInfo");
        info.hidden = false;
        info.innerHTML = `<b>${scan.count}</b> <code>.xlsx</code> file${scan.count === 1 ? "" : "s"} found.`;
        eg("ocvStep1Next").disabled = scan.count === 0;
      }
    } catch (err) {
      showErr(String(err.message || err));
    }
  }

  function currentCapacity() {
    const raw = eg("ocvCapacity").value.trim();
    if (raw !== "") return Number(raw);
    return state.detected ? state.detected.capacity : null;
  }

  async function detectCapacity() {
    if (state.mode !== "single" || !state.path) return;
    const note = eg("ocvCapNote");
    note.textContent = "Detecting capacity…";
    try {
      const cap = await postJSON("/api/ocv/detect-capacity", {
        path: state.path,
        sheet: eg("ocvSheet").value.trim() || "Record List1",
      });
      state.detected = cap;
      if (cap.capacity != null) eg("ocvCapacity").placeholder = `${cap.capacity} (auto)`;
      note.textContent =
        `Qd ≈ ${fmt(cap.qd)} Ah (discharge),  Qc ≈ ${fmt(cap.qc)} Ah (charge)` +
        (cap.capacity != null ? `  →  using ${fmt(cap.capacity)} Ah` : "");
      renderDetected(cap);
    } catch (err) {
      note.textContent = "Capacity auto-detect failed: " + (err.message || err);
    }
  }

  function renderDetected(cap) {
    const box = eg("ocvDetected");
    box.hidden = false;
    box.innerHTML =
      `<span class="ecm-detected-label">Detected:</span>` +
      `<span>${cap.n_discharge_steps} discharge steps</span>` +
      `<span>V ${fmt(cap.v_min)}–${fmt(cap.v_max)} V</span>` +
      `<span>charge ${cap.has_charge ? "present" : "missing"}</span>` +
      `<span>final rest ${cap.has_final_rest ? "present" : "missing"}</span>`;
  }

  // ---- step 2: compute / batch + results --------------------------------- //
  function clearResults() {
    eg("ocvResultsHead").innerHTML = "";
    eg("ocvMetrics").innerHTML = "";
    eg("ocvPlots").innerHTML = "";
    eg("ocvTable").innerHTML = "";
    eg("ocvWarnings").hidden = true;
    eg("ocvWarnings").innerHTML = "";
    eg("ocvBatchSummary").hidden = true;
    eg("ocvBatchSummary").innerHTML = "";
  }

  function runCompute() {
    if (state.mode === "folder") {
      runBatch();
      return;
    }
    runSingle();
  }

  async function runSingle() {
    showErr("");
    clearResults();
    eg("ocvLoading").hidden = false;
    eg("ocvRunCompute").disabled = true;
    try {
      const res = await postJSON("/api/ocv/compute", {
        path: state.path,
        sheet: eg("ocvSheet").value.trim() || "Record List1",
        capacity: currentCapacity(),
        ocv_mode: eg("ocvMode").value,
      });
      state.result = res;
      renderResults(res);
    } catch (err) {
      showErr(String(err.message || err));
    } finally {
      eg("ocvLoading").hidden = true;
      eg("ocvRunCompute").disabled = false;
    }
  }

  function runBatch() {
    showErr("");
    clearResults();
    const params = new URLSearchParams({
      dir: state.path,
      sheet: eg("ocvSheet").value.trim() || "Record List1",
      ocv_mode: eg("ocvMode").value,
    });
    const cap = currentCapacity();
    if (cap != null && !Number.isNaN(cap)) params.set("capacity", String(cap));

    const prog = eg("ocvBatchProgress");
    prog.hidden = false;
    eg("ocvRunCompute").disabled = true;
    eg("ocvBatchFill").style.width = "0%";
    eg("ocvBatchText").textContent = "Starting…";

    const es = new EventSource("/api/ocv/batch-stream?" + params.toString());
    es.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (!msg.done) {
        eg("ocvBatchFill").style.width = (msg.progress || 0) + "%";
        eg("ocvBatchText").textContent = `Processing ${msg.current}/${msg.total}: ${msg.file}`;
        return;
      }
      es.close();
      eg("ocvRunCompute").disabled = false;
      prog.hidden = true;
      renderBatchResults(msg);
    };
    es.onerror = () => {
      es.close();
      eg("ocvRunCompute").disabled = false;
      prog.hidden = true;
      showErr("Batch processing connection failed.");
    };
  }

  function renderResults(res) {
    const ep = res.endpoints || {};
    eg("ocvResultsHead").innerHTML =
      `<h3>${res.name}</h3>` +
      `<p class="muted">${res.n_discharge_steps} discharge steps · capacity ${fmt(res.capacity_used)} Ah</p>`;

    const card = (label, val) =>
      `<div class="ecm-metric"><span class="ecm-metric-label">${label}</span><span class="ecm-metric-val">${val}</span></div>`;
    eg("ocvMetrics").innerHTML =
      card("Qd (discharge)", `${fmt(res.qd)} Ah`) +
      card("Qc (charge)", `${fmt(res.qc)} Ah`) +
      card("Capacity used", `${fmt(res.capacity_used)} Ah`) +
      card("OCV @100% (dis)", `${fmt(ep.ocv_100_discharge)} V`) +
      card("OCV @0% (dis)", `${fmt(ep.ocv_0_discharge)} V`) +
      card("Max hysteresis", mv(ep.max_hysteresis_v)) +
      card("Mean hysteresis", mv(ep.mean_hysteresis_v));

    const warnEl = eg("ocvWarnings");
    if (res.warnings && res.warnings.length) {
      warnEl.hidden = false;
      warnEl.innerHTML =
        `<b>⚠ ${res.warnings.length} warning${res.warnings.length === 1 ? "" : "s"}:</b><ul>` +
        res.warnings.map((w) => `<li>${w}</li>`).join("") +
        `</ul>`;
    } else {
      warnEl.hidden = true;
      warnEl.innerHTML = "";
    }

    const imgs = res.images || {};
    eg("ocvPlots").innerHTML = [
      plotFigure("OCV vs SOC — discharge & charge", imgs.curves, "OCV curves"),
      plotFigure("Mean OCV & hysteresis vs SOC", imgs.mean_hyst, "Mean OCV and hysteresis"),
    ].join("");

    eg("ocvTable").innerHTML = renderRestedTable(res.rested_anchors);

    if (res.out_dir) {
      eg("ocvResultsHead").innerHTML += `<p class="ecm-saved-note">Saved to <code>${res.out_dir}</code></p>`;
    }
  }

  function renderRestedTable(anchors) {
    if (!anchors || !anchors.length) return "";
    const rows = anchors
      .slice()
      .sort((a, b) => b[0] - a[0])
      .map(([s, v]) => `<tr><td>${fmt(s)}</td><td>${fmt(v)}</td></tr>`)
      .join("");
    return `<details class="ecm-ocv-table" open>
      <summary>Discharge rested OCV anchors (${anchors.length})</summary>
      <table class="ecm-table"><thead><tr><th>SOC</th><th>OCV (V)</th></tr></thead><tbody>${rows}</tbody></table>
    </details>`;
  }

  function renderBatchResults(msg) {
    const rows = (msg.results || [])
      .map((r) =>
        `<tr><td>${r.name}</td><td>${fmt(r.capacity_used)}</td><td>${r.n_discharge_steps}</td>` +
        `<td>${fmt(r.ocv_100_discharge)}</td><td>${fmt(r.ocv_0_discharge)}</td><td>${mv(r.max_hysteresis_v)}</td></tr>`)
      .join("");
    const errRows = (msg.errors || [])
      .map((e) => `<li><b>${e.name}</b>: ${e.error}</li>`)
      .join("");

    if (msg.preview) {
      renderResults(msg.preview);
      eg("ocvResultsHead").innerHTML =
        `<h3>Batch complete — previewing a random file: ${msg.preview.name}</h3>` +
        `<p class="muted">${msg.ok_count} ok · ${msg.error_count} failed · ${msg.total} files</p>`;
    } else {
      eg("ocvResultsHead").innerHTML = `<h3>Batch complete</h3>`;
      eg("ocvMetrics").innerHTML = "";
      eg("ocvPlots").innerHTML = "";
      eg("ocvTable").innerHTML = "";
    }

    const summary = eg("ocvBatchSummary");
    summary.hidden = false;
    summary.innerHTML = `
      <h4>All files</h4>
      <p class="ecm-saved-note">Results saved under <code>${msg.output_root}</code></p>
      <div class="ecm-result-table"><table><thead><tr><th>File</th><th>Capacity (Ah)</th><th># steps</th><th>OCV @100%</th><th>OCV @0%</th><th>Max hyst.</th></tr></thead><tbody>${rows || '<tr><td colspan="6">No files processed.</td></tr>'}</tbody></table></div>
      ${errRows ? `<div class="ecm-errors"><b>${msg.error_count} failed:</b><ul>${errRows}</ul></div>` : ""}
    `;
  }

  // ---- wiring ------------------------------------------------------------ //
  window.ocvInit = function ocvInit() {
    if (state.initialized) return;
    state.initialized = true;

    updateModeUI();

    document.querySelectorAll('input[name="ocvFileMode"]').forEach((r) => {
      r.addEventListener("change", () => {
        state.mode = document.querySelector('input[name="ocvFileMode"]:checked').value;
        updateModeUI();
      });
    });

    eg("ocvPick").addEventListener("click", pick);
    eg("ocvDetectCap").addEventListener("click", detectCapacity);
    eg("ocvSheet").addEventListener("change", () => { if (state.mode === "single" && state.path) detectCapacity(); });
    eg("ocvStep1Next").addEventListener("click", () => setStep(2));
    eg("ocvRunCompute").addEventListener("click", runCompute);
    eg("ocvStartOver").addEventListener("click", () => {
      clearResults();
      setStep(1);
    });
  };
})();
