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
  // Sending a question from the box on another page marks it as asked by the
  // visitor, so the Ask page runs it on arrival. Suggested questions (chips,
  // follow-up links) only fill the box there.
  if (!document.getElementById("ask-root")) {
    document.querySelectorAll("form[data-ask-form]").forEach(function (form) {
      form.addEventListener("submit", function () {
        try {
          sessionStorage.setItem("ask-intent", form.querySelector("input[name=q]").value.trim());
        } catch (e) { /* then it waits in the box */ }
      });
    });
  }
  // Sample-question buttons on the Ask page fill the box; the visitor sends.
  document.querySelectorAll("[data-ask]").forEach(function (button) {
    button.addEventListener("click", function () {
      var input = document.querySelector("#ask-empty form[data-ask-form] input[name=q]");
      if (!input) return;
      input.value = button.getAttribute("data-ask");
      input.focus();
    });
  });
})();
