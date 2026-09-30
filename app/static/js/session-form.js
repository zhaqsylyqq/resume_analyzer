// New-session form: add/remove vacancy blocks and keep their numbering in sync.
(function () {
  "use strict";

  var list = document.querySelector("[data-vacancy-list]");
  var addButton = document.querySelector("[data-add-vacancy]");
  if (!list || !addButton) return;
  var max = parseInt(list.getAttribute("data-max"), 10) || 10;

  function cards() { return list.querySelectorAll("[data-vacancy]"); }

  function renumber() {
    var all = cards();
    all.forEach(function (card, i) {
      var heading = card.querySelector("[data-vacancy-number]");
      if (heading) heading.textContent = heading.textContent.replace(/\d+$/, String(i + 1));
      var legend = card.querySelector("legend");
      if (legend) legend.textContent = legend.textContent.replace(/\d+$/, String(i + 1));
      card.querySelector("[data-remove-vacancy]").hidden = all.length === 1;
    });
    addButton.disabled = all.length >= max;
  }

  addButton.addEventListener("click", function () {
    if (cards().length >= max) return;
    var clone = cards()[0].cloneNode(true);
    clone.querySelectorAll("input, textarea").forEach(function (el) { el.value = ""; });
    clone.querySelectorAll(".field-error, [data-bias-result]").forEach(function (node) {
      if (node.hasAttribute("data-bias-result")) { node.hidden = true; node.textContent = ""; } else { node.remove(); }
    });
    clone.querySelectorAll("button").forEach(function (button) { button.disabled = false; });
    list.appendChild(clone);
    renumber();
    clone.querySelector("input").focus();
  });

  list.addEventListener("click", function (event) {
    var remove = event.target.closest("[data-remove-vacancy]");
    if (!remove || cards().length === 1) return;
    remove.closest("[data-vacancy]").remove();
    renumber();
  });

  // Bias check for a draft vacancy (B1) ------------------------------------------------

  var form = document.querySelector("[data-session-form]");
  var language = document.getElementById("language");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderReport(box, report) {
    box.replaceChildren();
    if (!report.findings.length) {
      box.appendChild(el("div", "alert alert--success", form.dataset.msgClean));
    } else {
      var list = el("ul", "stack findings");
      report.findings.forEach(function (f) {
        var item = el("li", "finding" + (f.severity === "high" ? " finding--high" : ""));
        var head = el("div", "row row--between");
        head.appendChild(el("span", "finding__phrase", "«" + f.phrase + "»"));
        head.appendChild(el("span", "badge badge--plain", f.label));
        item.appendChild(head);
        item.appendChild(el("p", null, f.explanation));
        var tip = el("p");
        tip.appendChild(el("strong", null, form.dataset.msgSuggestion + ": "));
        tip.appendChild(document.createTextNode(f.suggestion));
        item.appendChild(tip);
        list.appendChild(item);
      });
      box.appendChild(list);
    }
    if (!report.ai_checked) box.appendChild(el("p", "hint mt-2", form.dataset.msgRulesOnly));
    box.hidden = false;
  }

  list.addEventListener("click", function (event) {
    var button = event.target.closest("[data-bias-check]");
    if (!button) return;
    var card = button.closest("[data-vacancy]");
    var text = card.querySelector("textarea").value;
    var box = card.querySelector("[data-bias-result]");
    var spinner = button.querySelector("[data-busy-spinner]");
    button.disabled = true;
    if (spinner) spinner.hidden = false;
    App.request(form.dataset.biasUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, language: language ? language.value : null }),
    })
      .then(function (report) { renderReport(box, report); })
      .catch(function (error) { App.toast(error.message || form.dataset.msgError, "danger"); })
      .finally(function () {
        button.disabled = false;
        if (spinner) spinner.hidden = true;
      });
  });

  renumber();
})();
