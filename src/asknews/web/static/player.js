// Document page: the publisher's own player, with transcript timestamps that seek.
(function () {
  "use strict";
  var doc = document.getElementById("doc");
  if (!doc) return;
  var provider = doc.dataset.provider;
  var mediaId = doc.dataset.mediaId;
  var start = doc.dataset.start ? Number(doc.dataset.start) : null;
  var segments = Array.prototype.slice.call(document.querySelectorAll(".seg[data-start]"));
  var seek = null;       // function(seconds)
  var current = null;    // function() -> seconds

  function highlight(seconds) {
    var active = null;
    segments.forEach(function (seg) {
      var s = Number(seg.dataset.start), e = seg.dataset.end ? Number(seg.dataset.end) : s + 30;
      var on = seconds >= s && seconds < e;
      seg.classList.toggle("on", on);
      if (on) active = seg;
    });
    return active;
  }

  function bindTimestamps() {
    document.querySelectorAll(".tm[data-seek]").forEach(function (button) {
      button.addEventListener("click", function () {
        var seconds = Number(button.dataset.seek);
        if (seek) seek(seconds);
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
    setInterval(function () { if (current) highlight(current()); }, 500);
  }

  function loadScript(src, onload) {
    var s = document.createElement("script");
    s.src = src; s.async = true; s.onload = onload;
    document.head.appendChild(s);
  }

  if (provider === "brightcove") {
    var account = document.getElementById("bc-player").dataset.account;
    loadScript("https://players.brightcove.net/" + account + "/default_default/index.min.js", function () {
      var player = window.videojs && window.videojs.getPlayer ? window.videojs.getPlayer("bc-player") : null;
      if (!player) return;
      seek = function (seconds) { player.currentTime(seconds); player.play(); };
      current = function () { return player.currentTime(); };
      player.ready(function () { if (start != null) seek(start); });
    });
  } else if (provider === "youtube") {
    window.onYouTubeIframeAPIReady = function () {
      var player = new window.YT.Player("yt-player", {
        videoId: mediaId,
        playerVars: { rel: 0, start: start || 0, autoplay: start != null ? 1 : 0 },
      });
      seek = function (seconds) { player.seekTo(seconds, true); player.playVideo(); };
      current = function () { return typeof player.getCurrentTime === "function" ? player.getCurrentTime() : 0; };
    };
    loadScript("https://www.youtube.com/iframe_api");
  }
  bindTimestamps();

  // Deep link to a segment anchor (#s12): highlight it.
  if (location.hash && /^#s\d+$/.test(location.hash)) {
    var target = document.getElementById(location.hash.slice(1));
    if (target) target.classList.add("on");
  }
})();
