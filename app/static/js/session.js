// Session workspace: drag-and-drop batch upload with progress, and live analysis status.
(function () {
  "use strict";

  var root = document.querySelector("[data-session]");
  if (!root) return;

  var ds = root.dataset;
  var maxFiles = parseInt(ds.maxFiles, 10);
  var maxBytes = parseInt(ds.maxBytes, 10);
  var ALLOWED = /\.(pdf|docx|txt)$/i;
  var BADGES = { done: "success", processing: "info", pending: "plain", parse_failed: "danger", analysis_failed: "danger" };

  var dropzone = root.querySelector("[data-dropzone]");
  var input = root.querySelector("[data-file-input]");
  var progressBox = root.querySelector("[data-upload-progress]");
  var progressBar = root.querySelector("[data-upload-bar]");
  var progressPct = root.querySelector("[data-upload-pct]");
  var report = root.querySelector("[data-upload-report]");
  var rejectedList = root.querySelector("[data-rejected-list]");
  var resumeList = root.querySelector("[data-resume-list]");
  var emptyNote = root.querySelector("[data-resumes-empty]");
  var analysisState = root.querySelector("[data-analysis-state]");
  var retryButton = root.querySelector("[data-retry]");
  var pollTimer = null;
  var wasBusy = ds.running === "true";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function format(template, values) {
    return template.replace(/\{(\w+)\}/g, function (_, key) { return values[key] != null ? values[key] : ""; });
  }

  // Upload ----------------------------------------------------------------------------

  function addRejected(items) {
    items.forEach(function (item) {
      var li = el("li", "file-item");
      var body = el("div", "file-item__name");
      body.appendChild(el("div", null, item.filename));
      body.appendChild(el("div", "file-item__error", item.message));
      li.appendChild(body);
      rejectedList.appendChild(li);
    });
    report.hidden = rejectedList.children.length === 0;
  }

  function upload(fileList) {
    var files = Array.prototype.slice.call(fileList);
    if (!files.length) return;
    rejectedList.textContent = "";
    report.hidden = true;

    if (files.length > maxFiles) {
      App.toast(ds.msgTooMany, "danger");
      return;
    }
    var local = [];
    var valid = files.filter(function (file) {
      if (!ALLOWED.test(file.name)) { local.push({ filename: file.name, message: ds.msgBadType }); return false; }
      if (file.size > maxBytes) { local.push({ filename: file.name, message: ds.msgTooLarge }); return false; }
      return true;
    });
    addRejected(local);
    if (!valid.length) return;

    var form = new FormData();
    valid.forEach(function (file) { form.append("files", file, file.name); });

    var xhr = new XMLHttpRequest();
    xhr.open("POST", ds.uploadUrl);
    xhr.setRequestHeader("X-CSRF-Token", App.csrfToken);
    xhr.setRequestHeader("Accept", "application/json");
    progressBox.hidden = false;
    dropzone.setAttribute("aria-busy", "true");

    xhr.upload.addEventListener("progress", function (event) {
      if (!event.lengthComputable) return;
      var pct = Math.round((100 * event.loaded) / event.total);
      progressBar.style.width = pct + "%";
      progressPct.textContent = pct + "%";
    });
    xhr.addEventListener("loadend", function () {
      progressBox.hidden = true;
      progressBar.style.width = "0";
      dropzone.removeAttribute("aria-busy");
      input.value = "";
      var data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (e) { /* non-JSON error page */ }
      if (xhr.status < 200 || xhr.status >= 300) {
        App.toast(data.message || ds.msgFailed, "danger");
        return;
      }
      addRejected(data.rejected || []);
      App.toast(format(ds.msgUploaded, { accepted: (data.accepted || []).length, rejected: (data.rejected || []).length + local.length }));
      if ((data.accepted || []).length && !data.started) App.toast(ds.msgNotStarted, "danger");
      refresh();
    });
    xhr.send(form);
  }

  input.addEventListener("change", function () { upload(input.files); });
  ["dragenter", "dragover"].forEach(function (type) {
    dropzone.addEventListener(type, function (event) { event.preventDefault(); dropzone.classList.add("is-dragover"); });
  });
  ["dragleave", "drop"].forEach(function (type) {
    dropzone.addEventListener(type, function () { dropzone.classList.remove("is-dragover"); });
  });
  dropzone.addEventListener("drop", function (event) {
    event.preventDefault();
    if (event.dataTransfer && event.dataTransfer.files) upload(event.dataTransfer.files);
  });

  // Status polling ----------------------------------------------------------------------

  function renderResume(r) {
    var li = el("li", "file-item");
    li.dataset.resumeId = r.id;
    var body = el("div", "file-item__name");
    body.appendChild(el("div", null, r.name || r.filename));
    var meta = [r.name ? r.filename : null, r.headline].filter(Boolean).join(" · ");
    if (meta) body.appendChild(el("div", "file-item__meta", meta));
    if (r.message) body.appendChild(el("div", "file-item__error", r.message));
    li.appendChild(body);
    var badge = el("span", "badge badge--" + (BADGES[r.status] || "plain") + (r.status === "processing" ? " badge--pulse" : ""), r.status_label);
    li.appendChild(badge);
    return li;
  }

  function render(data) {
    var p = data.progress;
    var set = function (key, value) {
      var node = root.querySelector('[data-stat="' + key + '"]');
      if (node) node.textContent = value;
    };
    set("total", p.total);
    set("done", p.done);
    set("queue", p.pending + p.processing);
    set("failed", p.parse_failed + p.analysis_failed);

    var fragment = document.createDocumentFragment();
    data.resumes.forEach(function (r) { fragment.appendChild(renderResume(r)); });
    resumeList.replaceChildren(fragment);
    emptyNote.hidden = data.resumes.length > 0;
    retryButton.hidden = !p.analysis_failed;

    var busy = data.running || p.pending > 0 || p.processing > 0;
    analysisState.hidden = !busy;
    if (wasBusy && !busy) {
      App.toast(ds.msgComplete);
      document.dispatchEvent(new CustomEvent("session:analyzed"));
    }
    wasBusy = busy;
    return busy;
  }

  function refresh() {
    clearTimeout(pollTimer);
    return App.request(ds.statusUrl)
      .then(function (data) {
        if (render(data)) pollTimer = setTimeout(refresh, 2000);
      })
      .catch(function () { pollTimer = setTimeout(refresh, 5000); });
  }

  retryButton.addEventListener("click", function () {
    retryButton.disabled = true;
    App.request(ds.analyzeUrl, { method: "POST" })
      .then(function () { App.toast(ds.msgQueued); wasBusy = true; refresh(); })
      .catch(function (error) { App.toast(error.message || ds.msgFailed, "danger"); })
      .finally(function () { retryButton.disabled = false; });
  });

  if (!analysisState.hidden) refresh();
})();
