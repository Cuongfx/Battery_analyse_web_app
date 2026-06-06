/* ECM tab — Equivalent Circuit Model workflow (extract HPPC pulses -> fit R/C). */
(function () {
  "use strict";

  const eg = (id) => document.getElementById(id);

  const state = {
    initialized: false,
    mode: "single",
    path: "",
    fileCount: 0,
    sheet: "Record List1",
    detected: null, // {qd, qc, capacity}
    rcOrder: 1,
    algorithm: "curve_fit",
    extract: null, // single-file extract result
  };

  const COL_LABELS = {
    soc: "SOC",
    tau1_s: "τ1 (s)",
    tau2_s: "τ2 (s)",
    r0_ohm: "R0 (Ω)",
    r1_ohm: "R1 (Ω)",
    r2_ohm: "R2 (Ω)",
    c1_f: "C1 (F)",
    c2_f: "C2 (F)",
  };

  function fmt(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const x = Number(value);
    if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.01)) {
      return x.toExponential(3);
    }
    return Number(x.toFixed(4)).toString();
  }

  function imgUrl(absPath) {
    return "/api/ecm/image?path=" + encodeURIComponent(absPath);
  }

  function showErr(msg) {
    const el = eg("ecmErr");
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
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

  // ---- stepper / stage navigation ---------------------------------------- //
  function setStep(n) {
    document.querySelectorAll("#ecmStepper .ecm-step").forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("active", s === n);
      li.classList.toggle("done", s < n);
      // Step 2 (extract) is only part of the single-file flow.
      li.classList.toggle("skip", s === 2 && state.mode === "folder");
    });
    for (let i = 1; i <= 4; i += 1) {
      eg("ecmStage" + i).hidden = i !== n;
    }
    showErr("");
  }

  // ---- step 1: select ---------------------------------------------------- //
  function updateModeUI() {
    const single = state.mode === "single";
    eg("ecmPick").textContent = single ? "Choose file" : "Choose folder";
    eg("ecmPath").textContent = single ? "No file selected" : "No folder selected";
    eg("ecmFolderInfo").hidden = true;
    eg("ecmDetectCap").style.display = single ? "" : "none";
    eg("ecmCapNote").textContent = single
      ? ""
      : "Capacity is auto-detected per file. A value entered here overrides all files.";
    state.path = "";
    state.detected = null;
    eg("ecmDetected").hidden = true;
    eg("ecmStep1Next").disabled = true;
    eg("ecmDetectCap").disabled = true;
  }

  async function pick() {
    showErr("");
    try {
      const { path } = await postJSON("/api/ecm/pick", {
        kind: state.mode === "folder" ? "folder" : "xlsx",
      });
      if (!path) return; // dialog cancelled
      state.path = path;
      eg("ecmPath").textContent = path;
      eg("ecmStep1Next").disabled = false;

      if (state.mode === "folder") {
        const scan = await getJSON("/api/ecm/scan-folder?dir=" + encodeURIComponent(path));
        state.fileCount = scan.count;
        const info = eg("ecmFolderInfo");
        info.hidden = false;
        info.textContent =
          scan.count > 0
            ? `${scan.count} .xlsx file${scan.count === 1 ? "" : "s"} found (recursive).`
            : "No .xlsx files found in this folder.";
        eg("ecmStep1Next").disabled = scan.count === 0;
      } else {
        eg("ecmDetectCap").disabled = false;
        detectCapacity(); // auto-run on pick
      }
    } catch (err) {
      showErr(String(err.message || err));
    }
  }

  async function detectCapacity() {
    if (state.mode !== "single" || !state.path) return;
    const note = eg("ecmCapNote");
    note.textContent = "Detecting capacity…";
    try {
      const cap = await postJSON("/api/ecm/detect-capacity", {
        path: state.path,
        sheet: eg("ecmSheet").value.trim() || "Record List1",
      });
      state.detected = cap;
      const capEl = eg("ecmCapacity");
      if (cap.capacity != null) capEl.placeholder = `${cap.capacity} (auto)`;
      note.textContent =
        `Qd ≈ ${fmt(cap.qd)} Ah (HPPC),  Qc ≈ ${fmt(cap.qc)} Ah (CCCV)` +
        (cap.capacity != null ? `  →  using ${fmt(cap.capacity)} Ah` : "");
      renderDetected(cap);
    } catch (err) {
      note.textContent = "Capacity auto-detect failed: " + (err.message || err);
    }
  }

  function renderDetected(cap) {
    const box = eg("ecmDetected");
    if (cap.v_min == null && cap.i_chg_max == null) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML =
      `<span class="ecm-detected-label">Detected (HPPC window):</span>` +
      `<span>V ${fmt(cap.v_min)}–${fmt(cap.v_max)} V</span>` +
      `<span>I charge max ${fmt(cap.i_chg_max)} A</span>` +
      `<span>I discharge max ${fmt(cap.i_dch_max)} A</span>` +
      `<span class="ecm-detected-note">Blank limits below use these.</span>`;
  }

  function currentCapacity() {
    const raw = eg("ecmCapacity").value.trim();
    if (raw !== "") return Number(raw);
    return state.detected ? state.detected.capacity : null;
  }

  // Optional limits from the advanced panel; "" means "use detected".
  function currentBounds() {
    const num = (id) => {
      const raw = eg(id).value.trim();
      return raw === "" ? null : Number(raw);
    };
    return {
      v_max: num("ecmVMax"),
      v_min: num("ecmVMin"),
      i_chg_max: num("ecmIChg"),
      i_dch_max: num("ecmIDch"),
      nominal_capacity: num("ecmNominal"),
    };
  }

  // ---- step 2: extract (single) ------------------------------------------ //
  async function runExtract() {
    showErr("");
    eg("ecmExtractLoading").hidden = false;
    eg("ecmRunExtract").disabled = true;
    try {
      const data = await postJSON("/api/ecm/extract", {
        path: state.path,
        sheet: eg("ecmSheet").value.trim() || "Record List1",
      });
      state.extract = data;
      const x = data.extract;
      const box = eg("ecmExtractResult");
      box.hidden = false;
      box.innerHTML = `
        <div class="ecm-extract-stats">
          <span><b>${x.n_sections}</b> SOC sections</span>
          <span><b>${x.n_rows.toLocaleString()}</b> rows</span>
          <span><b>${fmt(x.duration_s)}</b> s</span>
          <span>V ${fmt(x.v_min)}–${fmt(x.v_max)} V</span>
          <span>I ${fmt(x.i_min)}–${fmt(x.i_max)} A</span>
        </div>
        ${x.pulse_png ? `<img class="ecm-plot-img" src="${imgUrl(x.pulse_png)}" alt="Extracted HPPC pulses" />` : ""}
        <p class="ecm-saved-note">Pulse data saved to <code>${data.out_dir}</code></p>
      `;
      eg("ecmStep2Next").disabled = false;
    } catch (err) {
      showErr(String(err.message || err));
    } finally {
      eg("ecmExtractLoading").hidden = true;
      eg("ecmRunExtract").disabled = false;
    }
  }

  // ---- step 3: fit ------------------------------------------------------- //
  async function runFit() {
    showErr("");
    state.rcOrder = Number(document.querySelector('input[name="ecmRcOrder"]:checked').value);
    state.algorithm = eg("ecmAlgo").value;

    if (state.mode === "folder") {
      runBatch();
      return;
    }

    eg("ecmFitLoading").hidden = false;
    eg("ecmRunFit").disabled = true;
    try {
      const data = await postJSON("/api/ecm/fit", {
        path: state.path,
        sheet: eg("ecmSheet").value.trim() || "Record List1",
        rc_order: state.rcOrder,
        algorithm: state.algorithm,
        capacity: currentCapacity(),
        ocv_mode: eg("ecmOcvMode").value,
        ...currentBounds(),
      });
      const cap = data.capacity_detected || {};
      renderResults(data.fit, {
        name: data.name, out_dir: data.out_dir, qd: cap.qd, qc: cap.qc,
        nominal: data.nominal_capacity, warnings: data.warnings, ocv: data.ocv,
      });
      setStep(4);
    } catch (err) {
      showErr(String(err.message || err));
    } finally {
      eg("ecmFitLoading").hidden = true;
      eg("ecmRunFit").disabled = false;
    }
  }

  function runBatch() {
    const params = new URLSearchParams({
      dir: state.path,
      rc_order: String(state.rcOrder),
      algorithm: state.algorithm,
      sheet: eg("ecmSheet").value.trim() || "Record List1",
      pulse_max_seconds: "60",
      ocv_mode: eg("ecmOcvMode").value,
    });
    const cap = currentCapacity();
    if (cap != null && !Number.isNaN(cap)) params.set("capacity", String(cap));
    const bounds = currentBounds();
    Object.keys(bounds).forEach((k) => {
      if (bounds[k] != null && !Number.isNaN(bounds[k])) params.set(k, String(bounds[k]));
    });

    const prog = eg("ecmBatchProgress");
    prog.hidden = false;
    eg("ecmRunFit").disabled = true;
    eg("ecmBatchFill").style.width = "0%";
    eg("ecmBatchText").textContent = "Starting…";

    const es = new EventSource("/api/ecm/batch-stream?" + params.toString());
    es.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (!msg.done) {
        eg("ecmBatchFill").style.width = (msg.progress || 0) + "%";
        eg("ecmBatchText").textContent = `Processing ${msg.current}/${msg.total}: ${msg.file}`;
        return;
      }
      es.close();
      eg("ecmBatchFill").style.width = "100%";
      eg("ecmRunFit").disabled = false;
      prog.hidden = true;
      renderBatchResults(msg);
      setStep(4);
    };
    es.onerror = () => {
      es.close();
      eg("ecmRunFit").disabled = false;
      prog.hidden = true;
      showErr("Batch processing connection failed.");
    };
  }

  // ---- step 4: results --------------------------------------------------- //
  function renderResults(fit, meta) {
    eg("ecmResultsHead").innerHTML =
      `<h3>${meta.name}</h3>` +
      `<p class="muted">${fit.rc_order}RC · ${fit.algorithm} · capacity ${fmt(fit.capacity)} Ah</p>`;

    const card = (label, val) =>
      `<div class="ecm-metric"><span class="ecm-metric-label">${label}</span><span class="ecm-metric-val">${val}</span></div>`;
    const ep = (meta.ocv && meta.ocv.endpoints) || {};
    const capMetrics =
      (meta.qd != null ? card("Qd (HPPC)", `${fmt(meta.qd)} Ah`) : "") +
      (meta.qc != null ? card("Qc (CCCV)", `${fmt(meta.qc)} Ah`) : "") +
      (meta.nominal != null ? card("Nominal (ref)", `${fmt(meta.nominal)} Ah`) : "") +
      (ep.ocv_100 != null ? card("OCV @100%", `${fmt(ep.ocv_100)} V`) : "") +
      (ep.ocv_0 != null ? card("OCV @0%", `${fmt(ep.ocv_0)} V`) : "");
    eg("ecmMetrics").innerHTML =
      card("MAE", `${fmt(fit.mae)} V`) + card("RMSE", `${fmt(fit.rmse)} V`) + capMetrics;

    const warnEl = eg("ecmWarnings");
    if (meta.warnings && meta.warnings.length) {
      warnEl.hidden = false;
      warnEl.innerHTML =
        `<b>⚠ ${meta.warnings.length} warning${meta.warnings.length === 1 ? "" : "s"}:</b><ul>` +
        meta.warnings.map((w) => `<li>${w}</li>`).join("") +
        `</ul>`;
    } else {
      warnEl.hidden = true;
      warnEl.innerHTML = "";
    }

    eg("ecmResultTable").innerHTML = renderTable(fit.columns, fit.rows);

    const plots = [];
    if (fit.fit_png) plots.push(`<figure><figcaption>Measured vs fitted voltage</figcaption><img class="ecm-plot-img" src="${imgUrl(fit.fit_png)}" alt="HPPC fit" /></figure>`);
    if (fit.params_png) plots.push(`<figure><figcaption>R / C / τ vs SOC</figcaption><img class="ecm-plot-img" src="${imgUrl(fit.params_png)}" alt="RC parameters" /></figure>`);
    eg("ecmResultPlots").innerHTML = plots.join("");

    renderOcv(meta.ocv);

    eg("ecmBatchSummary").hidden = true;
    eg("ecmBatchSummary").innerHTML = "";
    if (meta.out_dir) {
      eg("ecmResultsHead").innerHTML += `<p class="ecm-saved-note">Saved to <code>${meta.out_dir}</code></p>`;
    }
  }

  // Estimated OCV vs SOC: plot + collapsible table.
  function renderOcv(ocv) {
    const box = eg("ecmOcv");
    if (!ocv || !ocv.png) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    let tableRows = "";
    const t = ocv.table || {};
    if (t.soc && t.soc.length) {
      tableRows = t.soc
        .map((s, i) => `<tr><td>${fmt(s)}</td><td>${fmt(t.ocv[i])}</td></tr>`)
        .join("");
    }
    const polyNote = ocv.poly
      ? `<p class="ecm-saved-note">Polynomial fit: degree ${ocv.poly.degree}, RMSE ${fmt(ocv.poly.rmse)} V (coefficients in <code>${"<stem>_ocv.csv"}</code> / summary).</p>`
      : "";
    box.innerHTML = `
      <h4>Estimated OCV vs SOC</h4>
      <figure><img class="ecm-plot-img" src="${imgUrl(ocv.png)}" alt="OCV vs SOC" /></figure>
      ${polyNote}
      ${tableRows ? `<details class="ecm-ocv-table"><summary>OCV table (tabulated grid)</summary>
        <div class="ecm-result-table"><table><thead><tr><th>SOC</th><th>OCV (V)</th></tr></thead><tbody>${tableRows}</tbody></table></div>
      </details>` : ""}
    `;
  }

  function renderBatchResults(msg) {
    const rows = (msg.results || [])
      .map((r) => `<tr><td>${r.name}</td><td>${fmt(r.capacity_used)}</td><td>${fmt(r.mae)}</td><td>${fmt(r.rmse)}</td></tr>`)
      .join("");
    const errRows = (msg.errors || [])
      .map((e) => `<li><b>${e.name}</b>: ${e.error}</li>`)
      .join("");

    if (msg.preview) {
      const cap = msg.preview.capacity_detected || {};
      renderResults(msg.preview.fit, {
        name: msg.preview.name, out_dir: null, qd: cap.qd, qc: cap.qc,
        nominal: msg.preview.nominal_capacity, warnings: msg.preview.warnings,
        ocv: msg.preview.ocv,
      });
      eg("ecmResultsHead").innerHTML =
        `<h3>Batch complete — previewing a random file: ${msg.preview.name}</h3>` +
        `<p class="muted">${msg.ok_count} ok · ${msg.error_count} failed · ${msg.total} files</p>`;
    } else {
      eg("ecmResultsHead").innerHTML = `<h3>Batch complete</h3>`;
      eg("ecmMetrics").innerHTML = "";
      eg("ecmResultTable").innerHTML = "";
      eg("ecmResultPlots").innerHTML = "";
      eg("ecmOcv").hidden = true;
      eg("ecmOcv").innerHTML = "";
    }

    const summary = eg("ecmBatchSummary");
    summary.hidden = false;
    summary.innerHTML = `
      <h4>All files</h4>
      <p class="ecm-saved-note">Results saved under <code>${msg.output_root}</code></p>
      <div class="ecm-result-table"><table><thead><tr><th>File</th><th>Capacity (Ah)</th><th>MAE (V)</th><th>RMSE (V)</th></tr></thead><tbody>${rows || '<tr><td colspan="4">No files processed.</td></tr>'}</tbody></table></div>
      ${errRows ? `<div class="ecm-errors"><b>${msg.error_count} failed:</b><ul>${errRows}</ul></div>` : ""}
    `;
  }

  function renderTable(columns, rows) {
    if (!columns || !rows) return "";
    const head = columns.map((c) => `<th>${COL_LABELS[c] || c}</th>`).join("");
    const body = rows
      .map((row) => "<tr>" + columns.map((c) => `<td>${fmt(row[c])}</td>`).join("") + "</tr>")
      .join("");
    return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  // ---- wiring ------------------------------------------------------------ //
  async function loadAlgorithms() {
    try {
      const data = await getJSON("/api/ecm/algorithms");
      const sel = eg("ecmAlgo");
      sel.innerHTML = data.algorithms
        .map((a) => `<option value="${a}"${a === "curve_fit" ? " selected" : ""}>${a}</option>`)
        .join("");
    } catch (err) {
      /* leave empty; surfaced on fit */
    }
  }

  window.ecmInit = function ecmInit() {
    if (state.initialized) return;
    state.initialized = true;

    loadAlgorithms();
    updateModeUI();

    document.querySelectorAll('input[name="ecmMode"]').forEach((r) => {
      r.addEventListener("change", () => {
        state.mode = document.querySelector('input[name="ecmMode"]:checked').value;
        updateModeUI();
      });
    });

    eg("ecmPick").addEventListener("click", pick);
    eg("ecmDetectCap").addEventListener("click", detectCapacity);
    eg("ecmSheet").addEventListener("change", () => {
      if (state.mode === "single" && state.path) detectCapacity();
    });

    eg("ecmStep1Next").addEventListener("click", () => {
      if (state.mode === "single") {
        eg("ecmStage2File").textContent = state.path.split("/").pop();
        setStep(2);
      } else {
        setStep(3);
      }
    });

    eg("ecmRunExtract").addEventListener("click", runExtract);
    eg("ecmStep2Back").addEventListener("click", () => setStep(1));
    eg("ecmStep2Next").addEventListener("click", () => setStep(3));

    eg("ecmStep3Back").addEventListener("click", () => setStep(state.mode === "single" ? 2 : 1));
    eg("ecmRunFit").addEventListener("click", runFit);

    eg("ecmRestart").addEventListener("click", () => {
      state.extract = null;
      eg("ecmExtractResult").hidden = true;
      eg("ecmExtractResult").innerHTML = "";
      eg("ecmStep2Next").disabled = true;
      setStep(1);
    });
  };
})();
