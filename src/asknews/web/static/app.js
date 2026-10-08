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
  // Leaving for the Ask page with a question (the home box, a sample chip, a
  // follow-up link) marks it as asked by the visitor, so that page runs it.
  function intend(question) {
    try { sessionStorage.setItem("ask-intent", question); } catch (e) { /* then it waits in the box */ }
  }
  if (!document.getElementById("ask-root")) {
    document.querySelectorAll("form[data-ask-form]").forEach(function (form) {
      form.addEventListener("submit", function () {
        intend(form.querySelector("input[name=q]").value.trim());
      });
    });
    document.querySelectorAll("a[href*='/ask?q=']").forEach(function (link) {
      link.addEventListener("click", function () {
        var q = new URL(link.href, location.href).searchParams.get("q");
        if (q) intend(q);
      });
    });
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
