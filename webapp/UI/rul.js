/* Battery Life Prediction — RUL tab.
   Loads a cell file (.pkl/.npz) + a model checkpoint folder, predicts a
   Remaining-Useful-Life class. Single file -> full result; folder -> batch
   table + random preview. Flexible on whether the file has full history. */
(function () {
  "use strict";

  const eg = (id) => document.getElementById(id);

  const CLASS_COLORS = ["#1565C0", "#2E7D32", "#F9A825", "#E65100", "#B71C1C"];

  const state = {
    initialized: false,
    mode: "single",   // "single" | "folder"
    history: "full",  // "full" | "partial"
    path: "",
    ckptDir: "",
    fileCount: 0,
    inspected: null,
    result: null,
  };

  function fmt(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const x = Number(value);
    if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 0.01)) return x.toExponential(3);
    return Number(x.toFixed(4)).toString();
  }
  const pct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);

  const imgUrl = (p) => "/api/rul/image?path=" + encodeURIComponent(p);
  const dlUrl = (p) => "/api/rul/image?path=" + encodeURIComponent(p) + "&download=1";

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
    const el = eg("rulErr");
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
    document.querySelectorAll("#rulStepper .ecm-step").forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("active", s === n);
      li.classList.toggle("done", s < n);
    });
    eg("rulStage1").hidden = n !== 1;
    eg("rulStage2").hidden = n !== 2;
    showErr("");
  }

  // ---- step 1: select + configure ---------------------------------------- //
  function updateModeUI() {
    const single = state.mode === "single";
    eg("rulPick").textContent = single ? "Choose file" : "Choose folder";
    eg("rulPath").textContent = single ? "No file selected" : "No folder selected";
    eg("rulFolderInfo").hidden = true;
    eg("rulDetected").hidden = true;
    state.path = "";
    state.inspected = null;
    updateHistoryUI();
    refreshNext();
  }

  function updateHistoryUI() {
    state.history = (document.querySelector('input[name="rulHistory"]:checked') || {}).value || "full";
    const full = state.history === "full";
    // Query cycle only makes sense for a single full-history file.
    eg("rulQueryWrap").hidden = !(full && state.mode === "single");
    eg("rulHistoryHelp").textContent = full
      ? "Full mode uses the first 8 early-life cycles + 8 recent cycles. Best accuracy; also draws the full life trajectory and (if the cell reached 80% EOL) the true class."
      : "Recent-only mode feeds the latest 16 cycles directly to predict the future. A reminder will be shown that the prediction may not be 100% correct.";
  }

  function refreshNext() {
    let ok = !!state.ckptDir;
    if (state.mode === "single") ok = ok && !!state.path && !!(state.inspected && state.inspected.enough_cycles);
    else ok = ok && state.fileCount > 0;
    eg("rulStep1Next").disabled = !ok;
  }

  async function pick() {
    showErr("");
    try {
      const { path } = await postJSON("/api/rul/pick", { kind: state.mode === "folder" ? "folder" : "file" });
      if (!path) return;
      state.path = path;
      eg("rulPath").textContent = path;
      if (state.mode === "single") {
        await inspectFile();
      } else {
        const scan = await getJSON("/api/rul/scan-folder?dir=" + encodeURIComponent(path));
        state.fileCount = scan.count;
        const info = eg("rulFolderInfo");
        info.hidden = false;
        info.innerHTML = `<b>${scan.count}</b> cell file${scan.count === 1 ? "" : "s"} (<code>.pkl</code>/<code>.npz</code>) found.`;
      }
      refreshNext();
    } catch (err) {
      showErr(String(err.message || err));
    }
  }

  async function pickCkpt() {
    showErr("");
    try {
      const { path } = await postJSON("/api/rul/pick", { kind: "ckpt" });
      if (!path) return;
      state.ckptDir = path;
      eg("rulCkptPath").textContent = path;
      const note = eg("rulCkptNote");
      try {
        const info = await getJSON("/api/rul/checkpoint-info?dir=" + encodeURIComponent(path));
        note.textContent = `Weights: ${info.weights} · scalers: ${info.dq_scaler}, ${info.summary_scaler}`;
        note.classList.remove("rul-incorrect");
      } catch (e) {
        state.ckptDir = "";
        note.textContent = "Not a valid checkpoint folder (need best_clf*.pt + dq_scaler*.pkl + summary_scaler*.pkl).";
        note.classList.add("rul-incorrect");
      }
      refreshNext();
    } catch (err) {
      showErr(String(err.message || err));
    }
  }

  async function inspectFile() {
    const box = eg("rulDetected");
    box.hidden = false;
    box.innerHTML = `<span>Reading file…</span>`;
    try {
      const info = await postJSON("/api/rul/inspect", { path: state.path });
      state.inspected = info;
      const eolTxt = info.reached_eol
        ? `reached 80% EOL at cycle ${info.cycle_life}`
        : `not yet at 80% EOL (min ${pct(info.q_retained_at_eol)} retained)`;
      box.innerHTML =
        `<span class="ecm-detected-label">Detected:</span>` +
        `<span>${info.n_cyc} cycles</span>` +
        `<span>max cycle ${info.max_cycle}</span>` +
        `<span>${eolTxt}</span>` +
        (info.enough_cycles ? "" : `<span class="rul-incorrect">needs ≥ ${info.min_cycles_required} cycles</span>`);
      // Default the query cycle to the latest available.
      const q = eg("rulQueryCycle");
      q.max = info.max_cycle;
      q.placeholder = `latest (${info.max_cycle})`;
      eg("rulQueryNote").textContent = `Pick any cycle from ${info.min_cycles_required} to ${info.max_cycle}.`;
    } catch (err) {
      state.inspected = null;
      box.innerHTML = `<span class="rul-incorrect">${String(err.message || err)}</span>`;
    }
  }

  // ---- step 2: predict / batch + results --------------------------------- //
  function clearResults() {
    eg("rulResultsHead").innerHTML = "";
    eg("rulMetrics").innerHTML = "";
    eg("rulPlots").innerHTML = "";
    eg("rulTable").innerHTML = "";
    eg("rulWarnings").hidden = true;
    eg("rulWarnings").innerHTML = "";
    eg("rulBatchSummary").hidden = true;
    eg("rulBatchSummary").innerHTML = "";
  }

  function runPredict() {
    if (state.mode === "folder") runBatch();
    else runSingle();
  }

  function queryCycleValue() {
    if (state.history !== "full" || state.mode !== "single") return null;
    const raw = eg("rulQueryCycle").value.trim();
    if (raw === "") return null;
    return Number(raw);
  }

  async function runSingle() {
    showErr("");
    clearResults();
    eg("rulLoading").hidden = false;
    eg("rulRunPredict").disabled = true;
    try {
      const res = await postJSON("/api/rul/predict", {
        path: state.path,
        ckpt_dir: state.ckptDir,
        has_full_history: state.history === "full",
        query_cycle: queryCycleValue(),
      });
      state.result = res;
      renderResults(res);
    } catch (err) {
      showErr(String(err.message || err));
    } finally {
      eg("rulLoading").hidden = true;
      eg("rulRunPredict").disabled = false;
    }
  }

  function runBatch() {
    showErr("");
    clearResults();
    const params = new URLSearchParams({
      dir: state.path,
      ckpt_dir: state.ckptDir,
      has_full_history: String(state.history === "full"),
    });
    const prog = eg("rulBatchProgress");
    prog.hidden = false;
    eg("rulRunPredict").disabled = true;
    eg("rulBatchFill").style.width = "0%";
    eg("rulBatchText").textContent = "Starting…";

    const es = new EventSource("/api/rul/batch-stream?" + params.toString());
    es.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (!msg.done) {
        eg("rulBatchFill").style.width = (msg.progress || 0) + "%";
        eg("rulBatchText").textContent = `Processing ${msg.current}/${msg.total}: ${msg.file}`;
        return;
      }
      es.close();
      eg("rulRunPredict").disabled = false;
      prog.hidden = true;
      if (msg.fatal) { showErr(msg.fatal); return; }
      renderBatchResults(msg);
    };
    es.onerror = () => {
      es.close();
      eg("rulRunPredict").disabled = false;
      prog.hidden = true;
      showErr("Batch processing connection failed.");
    };
  }

  function badge(cls, name) {
    const color = CLASS_COLORS[cls] || "#555";
    return `<span class="rul-pred-badge" style="background:${color}">${name}</span>`;
  }

  function renderResults(res) {
    const q = res.query || {};
    const tr = res.trajectory;
    eg("rulResultsHead").innerHTML =
      `<h3>${res.name}</h3>` +
      `<p class="muted">${res.cell_id} · ${res.n_cyc} cycles · ` +
      `${res.has_full_history ? "full-history" : "recent-only"} prediction</p>`;

    const card = (label, val) =>
      `<div class="ecm-metric"><span class="ecm-metric-label">${label}</span><span class="ecm-metric-val">${val}</span></div>`;

    let cards =
      card("Predicted RUL", badge(q.pred_class, q.pred_name)) +
      card("Confidence", pct(q.confidence)) +
      card("Query cycle", `${q.end_cycle} / ${res.max_cycle}`);
    if (q.true_name != null) {
      const mark = q.correct ? `<span class="rul-correct">✓ match</span>` : `<span class="rul-incorrect">✗ off</span>`;
      cards += card("True class", `${q.true_name} ${mark}`);
    }
    if (q.rul_estimate != null) cards += card("RUL (true)", `${q.rul_estimate} cycles`);
    if (tr && tr.accuracy != null) cards += card("Trajectory accuracy", pct(tr.accuracy));
    eg("rulMetrics").innerHTML = cards;

    const warnEl = eg("rulWarnings");
    if (res.warnings && res.warnings.length) {
      warnEl.hidden = false;
      warnEl.innerHTML =
        `<b>⚠ ${res.warnings.length} note${res.warnings.length === 1 ? "" : "s"}:</b><ul>` +
        res.warnings.map((w) => `<li>${w}</li>`).join("") + `</ul>`;
    } else {
      warnEl.hidden = true;
      warnEl.innerHTML = "";
    }

    const imgs = res.images || {};
    eg("rulPlots").innerHTML = [
      plotFigure("RUL prediction at query cycle", imgs.query, "RUL query prediction"),
      plotFigure("Predicted RUL class over cycle life", imgs.trajectory, "RUL trajectory"),
    ].join("");

    eg("rulTable").innerHTML = renderProbTable(q.probs, q.pred_class, q.true_class);

    if (res.out_dir) {
      eg("rulResultsHead").innerHTML += `<p class="ecm-saved-note">Saved to <code>${res.out_dir}</code></p>`;
    }
  }

  function renderProbTable(probs, predClass, trueClass) {
    if (!probs || !probs.length) return "";
    const names = ["RUL>400", "RUL 300-400", "RUL 200-300", "RUL 100-200", "RUL<100"];
    const rows = probs.map((p, i) => {
      const tags = [];
      if (i === predClass) tags.push("predicted");
      if (i === trueClass) tags.push("true");
      const tag = tags.length ? ` <em>(${tags.join(" / ")})</em>` : "";
      return `<tr><td>${names[i] || i}${tag}</td><td>${pct(p)}</td></tr>`;
    }).join("");
    return `<details class="ecm-ocv-table" open>
      <summary>Class probabilities</summary>
      <table class="ecm-table"><thead><tr><th>RUL class</th><th>Probability</th></tr></thead><tbody>${rows}</tbody></table>
    </details>`;
  }

  function renderBatchResults(msg) {
    const rows = (msg.results || []).map((r) => {
      const tname = r.true_name != null ? r.true_name : "—";
      const acc = r.accuracy != null ? pct(r.accuracy) : "—";
      return `<tr><td>${r.name}</td><td>${r.max_cycle}</td><td>${r.query_cycle}</td>` +
        `<td>${r.pred_name}</td><td>${pct(r.confidence)}</td><td>${tname}</td><td>${acc}</td></tr>`;
    }).join("");
    const errRows = (msg.errors || []).map((e) => `<li><b>${e.name}</b>: ${e.error}</li>`).join("");

    if (msg.preview) {
      renderResults(msg.preview);
      eg("rulResultsHead").innerHTML =
        `<h3>Batch complete — previewing a random file: ${msg.preview.name}</h3>` +
        `<p class="muted">${msg.ok_count} ok · ${msg.error_count} failed · ${msg.total} files</p>`;
    } else {
      eg("rulResultsHead").innerHTML = `<h3>Batch complete</h3>`;
      eg("rulMetrics").innerHTML = "";
      eg("rulPlots").innerHTML = "";
      eg("rulTable").innerHTML = "";
    }

    const summary = eg("rulBatchSummary");
    summary.hidden = false;
    summary.innerHTML = `
      <h4>All files</h4>
      <p class="ecm-saved-note">Results saved under <code>${msg.output_root}</code></p>
      <div class="ecm-result-table"><table><thead><tr><th>File</th><th>Max cycle</th><th>Query cycle</th><th>Predicted</th><th>Confidence</th><th>True class</th><th>Accuracy</th></tr></thead><tbody>${rows || '<tr><td colspan="7">No files processed.</td></tr>'}</tbody></table></div>
      ${errRows ? `<div class="ecm-errors"><b>${msg.error_count} failed:</b><ul>${errRows}</ul></div>` : ""}
    `;
  }

  // Reuse the shared lightbox (#ecmLightbox) for click-to-zoom on RUL plots.
  function wireLightbox() {
    const box = eg("ecmLightbox");
    if (!box) return;
    const img = box.querySelector("img");
    const open = (src) => { img.src = src; box.hidden = false; };
    const close = () => { box.hidden = true; img.src = ""; };
    eg("rulPanel").addEventListener("click", (e) => {
      const target = e.target.closest(".ecm-plot-img");
      if (target) open(target.src);
    });
    box.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }

  // ---- wiring ------------------------------------------------------------ //
  window.rulInit = function rulInit() {
    if (state.initialized) return;
    state.initialized = true;

    updateModeUI();
    wireLightbox();

    document.querySelectorAll('input[name="rulFileMode"]').forEach((r) => {
      r.addEventListener("change", () => {
        state.mode = document.querySelector('input[name="rulFileMode"]:checked').value;
        updateModeUI();
      });
    });
    document.querySelectorAll('input[name="rulHistory"]').forEach((r) => {
      r.addEventListener("change", () => { updateHistoryUI(); refreshNext(); });
    });

    eg("rulPick").addEventListener("click", pick);
    eg("rulPickCkpt").addEventListener("click", pickCkpt);
    eg("rulStep1Next").addEventListener("click", () => setStep(2));
    eg("rulRunPredict").addEventListener("click", runPredict);
    eg("rulStartOver").addEventListener("click", () => { clearResults(); setStep(1); });
  };
})();
