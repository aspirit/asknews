// Small behaviours shared by every page. No framework; progressive enhancement only.
(function () {
  "use strict";
  // Expand a meeting card that is linked to by hash and scroll it into view.
  if (location.hash && location.hash.startsWith("#m-")) {
    var card = document.getElementById(location.hash.slice(1));
    if (card && card.tagName === "DETAILS") {
      card.open = true;
      card.scrollIntoView({ block: "start" });
    }
  }
  // Sample-question buttons on the Ask page submit the question.
  document.querySelectorAll("[data-ask]").forEach(function (button) {
    button.addEventListener("click", function () {
      var form = document.querySelector("[data-ask-form]");
      if (!form) return;
      form.querySelector("input[name=q]").value = button.getAttribute("data-ask");
      form.requestSubmit ? form.requestSubmit() : form.submit();
    });
  });
})();
