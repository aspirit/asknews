// The Ask page: sends the question to /api/ask and renders the cited answer.
(function () {
  "use strict";
  var root = document.getElementById("ask-root");
  if (!root) return;
  var S = JSON.parse(document.getElementById("tpl-strings").textContent);
  var lang = root.dataset.lang;
  var PREFIX = document.documentElement.getAttribute("data-prefix") || "";
  var docId = root.dataset.doc;
  var turns = document.getElementById("turns");
  var rail = document.getElementById("rail");
  var railList = document.getElementById("rail-list");
  var railCount = document.getElementById("rail-count");
  var empty = document.getElementById("ask-empty");
  var thread = document.getElementById("ask-thread");
  var turnCount = 0;

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function fmtTime(seconds) {
    if (seconds == null) return "";
    var s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return h ? h + ":" + pad(m) + ":" + pad(x) : pad(m) + ":" + pad(x);
  }

  function fmtDate(iso) {
    var d = new Date(iso);
    if (lang === "ja") return d.getFullYear() + "\u5e74" + (d.getMonth() + 1) + "\u6708" + d.getDate() + "\u65e5";
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  }

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // "Rates fell [1]." -> text with citation chips; chips carry data-n for highlighting.
  function citedHtml(paragraph, citations) {
    return escapeHtml(paragraph).replace(/\[(\d+)\]/g, function (m, n) {
      var c = citations[Number(n) - 1];
      if (!c) return "";
      var time = c.start_sec != null ? "<em>" + fmtTime(c.start_sec) + "</em>" : "";
      return '<button type="button" class="c" data-n="' + n + '">' + n + time + "</button>";
    });
  }

  function sourceItem(c) {
    var meta = (S.inst[c.institution] || c.institution) + " \u00b7 " + (S.kinds[c.kind] || c.kind) + " \u00b7 " + fmtDate(c.published_at);
    var item = el("button", { type: "button", class: "src", "data-n": c.n }, [
      el("span", { class: "n", text: c.n }),
      el("span", { class: "o" }, [
        el("span", { text: meta }),
        el("span", { class: "lb" + (c.lang !== lang ? " x" : ""), text: c.lang.toUpperCase() }),
        c.start_sec != null ? el("span", { class: "tt", text: "\u25b6 " + fmtTime(c.start_sec) }) : null,
      ]),
      el("span", { class: "t", text: c.title }),
      el("span", { class: "x", text: c.text }),
    ]);
    item.addEventListener("click", function () { openDrawer(c); });
    return item;
  }

  function setHighlight(n, on) {
    document.querySelectorAll('[data-n="' + n + '"]').forEach(function (node) {
      node.classList.toggle("hl", on);
    });
  }

  function openDrawer(c) {
    closeDrawer();
    var scrim = el("div", { class: "scrim", id: "scrim" });
    scrim.addEventListener("click", closeDrawer);
    var who = c.speaker ? el("span", { text: c.speaker }) : null;
    var quote = el("div", { class: "quote", lang: c.lang }, [
      el("div", { class: "qmeta" }, [c.start_sec != null ? el("span", { class: "tt", text: "\u25b6 " + fmtTime(c.start_sec) }) : null, who]),
      document.createTextNode(c.text),
    ]);
    var open = el("a", { class: "btn", href: PREFIX + c.url, text: (c.start_sec != null ? S.open_at + " " + fmtTime(c.start_sec) : S.open_doc) + " \u2192" });
    var original = el("a", { class: "btn ghost", href: c.source_url, target: "_blank", rel: "noopener", text: c.source_url.split("/")[2] + " \u2197" });
    var drawer = el("div", { class: "drawer", id: "drawer", role: "dialog" }, [
      el("div", { class: "top" }, [el("span", { class: "kick", text: S.sources }), el("button", { type: "button", "aria-label": "Close", text: "\u00d7" })]),
      el("div", { class: "bd" }, [
        el("div", { class: "meta", style: "margin-bottom:0" }, [el("b", { text: S.inst[c.institution] || c.institution }), el("span", { text: S.kinds[c.kind] || c.kind }), el("span", { text: fmtDate(c.published_at) })]),
        el("h3", { text: c.title }),
        quote,
        el("div", { style: "display:flex;gap:10px;flex-wrap:wrap;margin-top:8px" }, [open, original]),
      ]),
    ]);
    drawer.querySelector(".top button").addEventListener("click", closeDrawer);
    document.body.appendChild(scrim);
    document.body.appendChild(drawer);
  }

  function closeDrawer() {
    ["scrim", "drawer"].forEach(function (id) { var n = document.getElementById(id); if (n) n.remove(); });
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });

  function renderRail(citations) {
    railList.innerHTML = "";
    citations.forEach(function (c) { railList.appendChild(sourceItem(c)); });
    railCount.textContent = citations.length;
    rail.hidden = citations.length === 0;
    thread.classList.toggle("solo", citations.length === 0);
  }

  function statusMessage(data) {
    if (data.status === "declined") {
      if (data.reason === "out_of_scope") return S.out_of_scope;
      if (data.reason === "budget") return S.budget;
      if (data.reason === "rate") return S.rate;
      return S.error;
    }
    if (data.status === "error") return S.error;
    return S.no_sources;
  }

  function renderTurn(container, question, data, seconds) {
    container.innerHTML = "";
    container.appendChild(el("div", { class: "kick", text: S.your_question }));
    container.appendChild(el("h2", { text: question }));
    if (data.status !== "answered") {
      var box = el("div", { class: "refuse" }, [
        el("div", { class: "kick", text: data.status === "declined" ? S.out_of_scope_kick : S.sources }),
        el("p", { text: data.status === "no_sources" && data.paragraphs && data.paragraphs.length ? data.paragraphs[0] : statusMessage(data) }),
      ]);
      var list = el("div", { class: "lst" });
      (S.questions || []).slice(0, 3).forEach(function (q) {
        var b = el("button", { type: "button", text: q });
        b.addEventListener("click", function () { askQuestion(q); });
        list.appendChild(b);
      });
      box.appendChild(el("div", { class: "fu", style: "margin-top:4px" }, [el("div", { class: "sec-h" }, [el("h3", { text: S.examples })]), list]));
      container.appendChild(box);
      renderRail([]);
      return;
    }
    container.appendChild(el("div", { class: "meta" }, [
      el("b", { text: "\u25cf " + data.citations.length + " " + S.sources_used }),
      el("span", { text: S.answered_in + " " + seconds + "s" }),
    ]));
    var answer = el("div", { class: "ans" });
    data.paragraphs.forEach(function (p, i) {
      var para = el("p", { html: citedHtml(p, data.citations) });
      para.style.animationDelay = i * 0.18 + "s";
      answer.appendChild(para);
    });
    container.appendChild(answer);
    container.appendChild(el("div", { class: "grnd", text: S.grounded }));
    var copy = el("button", { type: "button", text: S.copy });
    copy.addEventListener("click", function () {
      var text = question + "\n\n" + data.paragraphs.join("\n\n") + "\n\n" + data.citations.map(function (c) {
        return "[" + c.n + "] " + c.title + " \u2014 " + c.source_url;
      }).join("\n");
      if (navigator.clipboard) navigator.clipboard.writeText(text);
      copy.textContent = S.copied;
      setTimeout(function () { copy.textContent = S.copy; }, 1400);
    });
    container.appendChild(el("div", { class: "acts" }, [copy]));
    if (data.followups && data.followups.length) {
      var list2 = el("div", { class: "lst" });
      data.followups.forEach(function (q) {
        var b = el("button", { type: "button", text: q });
        b.addEventListener("click", function () { askQuestion(q); });
        list2.appendChild(b);
      });
      container.appendChild(el("div", { class: "fu" }, [el("div", { class: "sec-h" }, [el("h3", { text: S.followups })]), list2]));
    }
    container.querySelectorAll(".c").forEach(function (chip) {
      var n = chip.dataset.n;
      chip.addEventListener("mouseenter", function () { setHighlight(n, true); });
      chip.addEventListener("mouseleave", function () { setHighlight(n, false); });
      chip.addEventListener("click", function () { openDrawer(data.citations[Number(n) - 1]); });
    });
    renderRail(data.citations);
    railList.querySelectorAll(".src").forEach(function (item) {
      var n = item.dataset.n;
      item.addEventListener("mouseenter", function () { setHighlight(n, true); });
      item.addEventListener("mouseleave", function () { setHighlight(n, false); });
    });
  }

  function askQuestion(question) {
    if (!question) return;
    empty.hidden = true;
    thread.hidden = false;
    turnCount += 1;
    document.querySelectorAll("#turns .turn").forEach(function (t) { t.classList.add("prev"); });
    var container = el("div", { class: "turn" });
    var steps = el("div", { class: "steps" }, [S.searching, S.reading, S.writing].map(function (label) {
      return el("div", {}, [el("i"), document.createTextNode(label)]);
    }));
    container.appendChild(el("div", { class: "kick", text: S.your_question }));
    container.appendChild(el("h2", { text: question }));
    container.appendChild(steps);
    turns.appendChild(container);
    container.scrollIntoView({ behavior: "smooth", block: "start" });
    var stepNodes = steps.children;
    stepNodes[0].className = "cur";
    setTimeout(function () { stepNodes[0].className = "done"; stepNodes[1].className = "cur"; }, 450);
    setTimeout(function () { stepNodes[1].className = "done"; stepNodes[2].className = "cur"; }, 1000);
    var started = performance.now();
    var url = "/api/ask?q=" + encodeURIComponent(question) + (docId ? "&doc=" + docId : "");
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (data) { renderTurn(container, question, data, ((performance.now() - started) / 1000).toFixed(1)); })
      .catch(function () { renderTurn(container, question, { status: "error", paragraphs: [], citations: [] }, 0); });
    history.replaceState(null, "", PREFIX + "/ask?q=" + encodeURIComponent(question) + (docId ? "&doc=" + docId : ""));
  }

  document.querySelectorAll("[data-ask-form]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = form.querySelector("input[name=q]");
      var q = input.value.trim();
      input.value = "";
      askQuestion(q);
    });
  });

  if (root.dataset.question) askQuestion(root.dataset.question);
})();
