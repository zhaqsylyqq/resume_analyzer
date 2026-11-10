// Shared helpers: CSRF-aware fetch and toasts. Page scripts use window.App.
(function () {
  "use strict";

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

  function request(url, options) {
    options = options || {};
    var headers = new Headers(options.headers || {});
    headers.set("X-CSRF-Token", csrfToken);
    headers.set("Accept", "application/json");
    return fetch(url, Object.assign({}, options, { headers: headers, credentials: "same-origin" }))
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) {
            var error = new Error(data.message || data.error || response.statusText);
            error.status = response.status;
            error.data = data;
            throw error;
          }
          return data;
        });
      });
  }

  function toast(message, kind) {
    var region = document.querySelector("[data-toasts]");
    if (!region) return;
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " toast--" + kind : "");
    el.textContent = message;
    region.appendChild(el);
    setTimeout(function () { el.remove(); }, 5000);
  }

  // Forms with data-confirm ask before submitting (e.g. delete).
  // Forms with data-busy-form show a spinner and block double submits on slow AI calls.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) { event.preventDefault(); return; }
    if (form.hasAttribute("data-busy-form")) {
      var button = form.querySelector('button[type="submit"]');
      var spinner = form.querySelector("[data-busy-spinner]");
      if (spinner) spinner.hidden = false;
      if (button) setTimeout(function () { button.disabled = true; }, 0);
    }
  });

  // Buttons with data-copy="<selector>" copy that element's text.
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    var source = document.querySelector(button.getAttribute("data-copy"));
    if (!source || !navigator.clipboard) return;
    navigator.clipboard.writeText(source.textContent.trim()).then(function () {
      toast(button.getAttribute("data-copied") || "OK");
    });
  });

  // Progress bars rendered server-side carry their width in data-width (no inline styles under CSP).
  document.querySelectorAll("[data-width]").forEach(function (bar) {
    bar.style.width = Math.max(0, Math.min(100, parseFloat(bar.getAttribute("data-width")) || 0)) + "%";
  });

  // Show UTC timestamps in the viewer's local time.
  document.querySelectorAll("time[data-local-time]").forEach(function (node) {
    var date = new Date(node.getAttribute("datetime"));
    if (!isNaN(date)) {
      node.textContent = date.toLocaleString(document.documentElement.lang === "kk" ? "kk-KZ" : "ru-RU",
        { dateStyle: "medium", timeStyle: "short" });
    }
  });

  // CR-01 candidate status: save without reloading (the plain form still works without JS).
  // Changing the status saves at once; the note is saved with the button or Enter.
  function saveStatus(form) {
    var button = form.querySelector("[data-status-save]");
    var select = form.querySelector('select[name="status"]');
    if (button) button.disabled = true;
    request(form.action, { method: "POST", body: new FormData(form) })
      .then(function (data) {
        select.className = select.className.replace(/status-select--\w+/, "status-select--" + data.status);
        toast(data.message, "success");
      })
      .catch(function (error) { toast(error.message, "danger"); })
      .then(function () { if (button) button.disabled = false; });
  }
  document.addEventListener("change", function (event) {
    var form = event.target.closest("[data-status-form]");
    if (form && event.target.name === "status") saveStatus(form);
  });
  document.addEventListener("submit", function (event) {
    if (!event.target.matches("[data-status-form]")) return;
    event.preventDefault();
    saveStatus(event.target);
  });

  window.App = { request: request, toast: toast, csrfToken: csrfToken };
})();
