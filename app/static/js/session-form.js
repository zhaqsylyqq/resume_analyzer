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
    clone.querySelectorAll(".field-error, [data-bias-result]").forEach(function (el) {
      if (el.hasAttribute("data-bias-result")) { el.hidden = true; el.textContent = ""; } else { el.remove(); }
    });
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

  renumber();
})();
