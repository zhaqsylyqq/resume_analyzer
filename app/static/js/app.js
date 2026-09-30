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
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) event.preventDefault();
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

  window.App = { request: request, toast: toast, csrfToken: csrfToken };
})();
