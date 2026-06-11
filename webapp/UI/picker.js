/* Shared in-browser file/folder picker.
 *
 * window.openPicker(opts) -> Promise<string|null>
 *   opts.title   : modal heading
 *   opts.select  : "folder" | "file"   (what the user ultimately picks)
 *   opts.kind    : "folder" | "ckpt" | "xlsx" | "cell"  (file filter on the server)
 *   opts.start   : optional starting directory (defaults to the project root)
 *   opts.native  : { url, body }  -> "Use system dialog" fallback (native OS picker)
 *
 * Resolves with the chosen absolute path, or null if cancelled.
 */
(function () {
  "use strict";

  // --- inline icons (home / up / new-folder) ------------------------------ //
  var ICONS = {
    home: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>',
    up: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>',
    mkdir: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v4"/><path d="M10 13h4"/></svg>',
    folder: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#5b7cfa" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    file: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#7a8699" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  };

  var overlay = null;
  var els = {};
  var cur = null; // active session state

  function h(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function build() {
    overlay = h("div", "picker-overlay");
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="picker-modal" role="dialog" aria-modal="true">' +
      '  <div class="picker-head">' +
      '    <h3 class="picker-title"></h3>' +
      '    <button type="button" class="picker-x" aria-label="Close">✕</button>' +
      "  </div>" +
      '  <div class="picker-bar">' +
      '    <button type="button" class="picker-ico" data-act="home" title="Home folder">' + ICONS.home + "</button>" +
      '    <button type="button" class="picker-ico" data-act="up" title="Up one level">' + ICONS.up + "</button>" +
      '    <span class="picker-path" title=""></span>' +
      '    <button type="button" class="picker-ico picker-ico-end" data-act="mkdir" title="New folder">' + ICONS.mkdir + "</button>" +
      "  </div>" +
      '  <div class="picker-list"></div>' +
      '  <div class="picker-foot">' +
      '    <button type="button" class="picker-native">Use system dialog</button>' +
      '    <span class="picker-hint"></span>' +
      '    <span class="picker-actions"></span>' +
      "  </div>" +
      "</div>";
    document.body.appendChild(overlay);

    els.modal = overlay.querySelector(".picker-modal");
    els.title = overlay.querySelector(".picker-title");
    els.path = overlay.querySelector(".picker-path");
    els.list = overlay.querySelector(".picker-list");
    els.hint = overlay.querySelector(".picker-hint");
    els.actions = overlay.querySelector(".picker-actions");
    els.native = overlay.querySelector(".picker-native");

    overlay.querySelector(".picker-x").addEventListener("click", function () { close(null); });
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) close(null); });
    overlay.querySelectorAll(".picker-ico").forEach(function (b) {
      b.addEventListener("click", function () { onAct(b.dataset.act); });
    });
    els.native.addEventListener("click", useNative);
    document.addEventListener("keydown", function (e) {
      if (!overlay.hidden && e.key === "Escape") close(null);
    });
  }

  function setError(msg) {
    els.hint.textContent = msg || cur.hintDefault;
    els.hint.classList.toggle("picker-err", !!msg);
  }

  async function navigate(path) {
    var url = "/api/fs/list?kind=" + encodeURIComponent(cur.kind);
    if (path) url += "&path=" + encodeURIComponent(path);
    var res, data;
    try {
      res = await fetch(url);
      data = await res.json().catch(function () { return {}; });
    } catch (e) {
      setError("Could not read folder.");
      return;
    }
    if (!res.ok) { setError(data.detail || "Could not read folder."); return; }
    cur.path = data.path;
    cur.parent = data.parent;
    cur.home = data.home;
    cur.selected = null;
    els.path.textContent = data.path;
    els.path.title = data.path;
    renderList(data.entries || []);
    updateActions();
    setError("");
  }

  function renderList(entries) {
    els.list.innerHTML = "";
    if (!entries.length) {
      els.list.appendChild(h("div", "picker-empty", "Empty folder"));
      return;
    }
    entries.forEach(function (ent) {
      var row = h("div", "picker-row picker-" + ent.type);
      row.innerHTML =
        '<span class="picker-row-ico">' + (ent.type === "dir" ? ICONS.folder : ICONS.file) + "</span>" +
        '<span class="picker-row-name"></span>';
      row.querySelector(".picker-row-name").textContent = ent.name;
      var full = cur.path.replace(/\/+$/, "") + "/" + ent.name;
      row.addEventListener("click", function () { onRowClick(ent, full, row); });
      row.addEventListener("dblclick", function () { onRowDbl(ent, full); });
      els.list.appendChild(row);
    });
  }

  function highlight(row) {
    els.list.querySelectorAll(".picker-row.sel").forEach(function (r) { r.classList.remove("sel"); });
    if (row) row.classList.add("sel");
  }

  function onRowClick(ent, full, row) {
    if (ent.type === "dir") {
      if (cur.select === "folder") {
        cur.selected = { type: "dir", path: full };
        highlight(row);
        updateActions();
      } else {
        navigate(full); // file mode: drill straight into folders
      }
    } else {
      cur.selected = { type: "file", path: full };
      highlight(row);
      updateActions();
    }
  }

  function onRowDbl(ent, full) {
    if (ent.type === "dir") navigate(full);
    else if (cur.select === "file") close(full);
  }

  function updateActions() {
    els.actions.innerHTML = "";
    if (cur.select === "folder") {
      var useThis = h("button", "picker-btn", "Use this folder");
      useThis.addEventListener("click", function () { close(cur.path); });
      var useSel = h("button", "picker-btn picker-btn-primary", "Use selected");
      useSel.disabled = !(cur.selected && cur.selected.type === "dir");
      useSel.addEventListener("click", function () {
        if (cur.selected) close(cur.selected.path);
      });
      els.actions.appendChild(useThis);
      els.actions.appendChild(useSel);
    } else {
      var open = h("button", "picker-btn picker-btn-primary", "Open");
      open.disabled = !(cur.selected && cur.selected.type === "file");
      open.addEventListener("click", function () {
        if (cur.selected) close(cur.selected.path);
      });
      els.actions.appendChild(open);
    }
  }

  function onAct(act) {
    if (act === "home") navigate(cur.home || "");
    else if (act === "up") { if (cur.parent) navigate(cur.parent); }
    else if (act === "mkdir") doMkdir();
  }

  async function doMkdir() {
    var name = window.prompt("New folder name:", "");
    if (name == null) return;
    name = name.trim();
    if (!name) return;
    var res, data;
    try {
      res = await fetch("/api/fs/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: cur.path, name: name }),
      });
      data = await res.json().catch(function () { return {}; });
    } catch (e) {
      setError("Could not create folder.");
      return;
    }
    if (!res.ok) { setError(data.detail || "Could not create folder."); return; }
    await navigate(cur.path); // refresh; the new folder appears in the list
  }

  async function useNative() {
    var n = cur.native;
    if (!n || !n.url) return;
    var resolve = cur.resolve;
    hide();
    try {
      var opt = { method: "POST" };
      if (n.body) {
        opt.headers = { "Content-Type": "application/json" };
        opt.body = JSON.stringify(n.body);
      }
      var res = await fetch(n.url, opt);
      var data = await res.json().catch(function () { return {}; });
      resolve(res.ok && data.path ? data.path : null);
    } catch (e) {
      resolve(null);
    }
  }

  function hide() {
    overlay.hidden = true;
    cur = null;
  }

  function close(value) {
    var resolve = cur && cur.resolve;
    hide();
    if (resolve) resolve(value || null);
  }

  window.openPicker = function (opts) {
    opts = opts || {};
    if (!overlay) build();
    return new Promise(function (resolve) {
      cur = {
        select: opts.select === "file" ? "file" : "folder",
        kind: opts.kind || (opts.select === "file" ? "cell" : "folder"),
        native: opts.native || null,
        resolve: resolve,
        path: "",
        parent: null,
        home: null,
        selected: null,
      };
      cur.hintDefault =
        cur.select === "folder"
          ? "Pick a sub-folder, or use the current folder →"
          : "Pick a file, then “Open” — or double-click it.";
      els.title.textContent = opts.title || (cur.select === "file" ? "Select file" : "Select folder");
      els.native.hidden = !cur.native;
      els.hint.textContent = cur.hintDefault;
      els.hint.classList.remove("picker-err");
      els.actions.innerHTML = "";
      els.list.innerHTML = "";
      overlay.hidden = false;
      navigate(opts.start || "");
    });
  };
})();
