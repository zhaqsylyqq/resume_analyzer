// Loaded synchronously in <head> so the saved theme applies before first paint.
(function () {
  var KEY = "theme";
  var root = document.documentElement;

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function apply(theme) {
    root.setAttribute("data-theme", theme);
  }

  apply(stored() || (systemDark() ? "dark" : "light"));

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* storage blocked */ }
  });
})();
