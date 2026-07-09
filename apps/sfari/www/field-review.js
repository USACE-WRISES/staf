/* SFARI field-review worksheet interactions.
 *
 * All edits go through Shiny.setInputValue (event priority) so the server keeps a
 * shadow copy for the live rollup while the dynamic panel re-renders only on
 * function switch (never on a keystroke/drag) — the same non-binding technique
 * EASI uses for its report overrides. Visual state (rating dropdown, score
 * number/band) is updated client-side so there is no round-trip flicker.
 */
(function () {
  function send(id, payload) {
    if (window.Shiny && Shiny.setInputValue) {
      payload._t = Date.now();
      Shiny.setInputValue(id, payload, { priority: "event" });
    }
  }

  function bandOf(v) {
    if (v <= 5) return { label: "Non-Functioning", color: "#f5b5b5" };
    if (v <= 10) return { label: "Functioning-at-Risk", color: "#f5e7a6" };
    return { label: "Functioning", color: "#c8d9f2" };
  }

  // rating -> select tint class (mirrors _LIKERT_CLS in app.py)
  var LIKERT_CLS = { "Strongly Agree": "lk-good", "Agree": "lk-good", "Neutral": "lk-mid",
                     "Disagree": "lk-poor", "Strongly Disagree": "lk-poor", "Not Applicable": "lk-na" };

  document.addEventListener("click", function (e) {
    // Step navigator (data-step) — one event, so the two steppers never collide on ids.
    var step = e.target.closest("[data-step]");
    if (step) { send("step_nav", { key: step.dataset.step }); return; }
    // Reveal/hide an optional field (note / photo strip / function justification) — client-only.
    var tog = e.target.closest(".sfari-metric-toggle");
    if (tog) {
      var host = tog.closest(".sfari-metric, .sfari-scorecard");
      var kind = tog.dataset.toggle;
      if (host && kind) { host.classList.toggle("show-" + kind); tog.classList.toggle("on"); }
      return;
    }
    // "use this" suggested Likert chip -> set that rating in the metric's dropdown.
    var chip = e.target.closest(".sfari-suggest-chip");
    if (chip) {
      var lksel = document.querySelector('.sfari-likert-select[data-mid="' + cssEsc(chip.dataset.mid) + '"]');
      if (lksel) { lksel.value = chip.dataset.val; lksel.dispatchEvent(new Event("change", { bubbles: true })); }
      return;
    }
    // Desktop-metrics list popup.
    var dm = e.target.closest("[data-desktop-metrics]");
    if (dm) { send("desktop_metrics_evt", {}); return; }
    // Report: expand/collapse every discipline evidence expander at once.
    var rx = e.target.closest("[data-rep-expand]");
    if (rx) {
      var open = rx.dataset.repExpand === "1";
      document.querySelectorAll("#sfari-report details.sfari-rep-disc").forEach(function (dd) { dd.open = open; });
      return;
    }
    // Accept the suggested 0-15 function score -> move the slider + commit.
    var acc = e.target.closest(".sfari-accept");
    if (acc) {
      var fid = acc.dataset.fid, val = Math.round(parseFloat(acc.dataset.val));
      var sl = document.querySelector('.sfari-fscore[data-fid="' + cssEsc(fid) + '"]');
      if (sl) { sl.value = val; sl.dispatchEvent(new Event("input", { bubbles: true }));
                sl.dispatchEvent(new Event("change", { bubbles: true })); }
      return;
    }
    // Open the report modal.
    var rep = e.target.closest("[data-report]");
    if (rep) { send("open_report_evt", {}); return; }
    // Cross-section hydraulics popup.
    var xs = e.target.closest("[data-xs]");
    if (xs) { send("xs_open_evt", { fid: xs.dataset.xs }); return; }
    var xsa = e.target.closest("[data-xs-attach]");
    if (xsa) { send("xs_attach_evt", {}); return; }
    // Prev / Next / jump navigation.
    var nav = e.target.closest("[data-nav]");
    if (nav) { send("nav_move", { d: parseInt(nav.dataset.nav, 10) }); return; }
    var jump = e.target.closest(".sfari-nav-fn");
    if (jump && jump.dataset.idx !== undefined) { send("nav_jump", { i: parseInt(jump.dataset.idx, 10) }); return; }
    // Remove a metric photo.
    var prm = e.target.closest(".sfari-photo-rm");
    if (prm) {
      var pw = prm.closest(".sfari-thumb-wrap"); if (pw) pw.remove();
      send("metric_photo_remove", { mid: prm.dataset.mid, id: prm.dataset.id });
      return;
    }
  });

  document.addEventListener("input", function (e) {
    var sl = e.target.closest(".sfari-fscore");
    if (sl) {
      var card = sl.closest(".sfari-scorecard");
      if (card) {
        var num = card.querySelector(".sfari-fscore-num");
        var band = card.querySelector(".sfari-fscore-band");
        var v = parseInt(sl.value, 10), bd = bandOf(v);
        if (num) num.textContent = v;
        if (band) { band.textContent = bd.label; band.style.background = bd.color; }
        card.classList.remove("unset");
      }
      return;
    }
    var ta = e.target.closest(".sfari-metric-note");
    if (ta) { debounce(ta, function () { send("metric_note_set", { mid: ta.dataset.mid, note: ta.value }); }); return; }
    var fn = e.target.closest(".sfari-fn-note");
    if (fn) { debounce(fn, function () { send("fn_note_set", { fid: fn.dataset.fid, note: fn.value }); }); return; }
  });

  document.addEventListener("change", function (e) {
    // Likert rating dropdown — empty value clears the rating.
    var lk = e.target.closest(".sfari-likert-select");
    if (lk) {
      lk.classList.remove("lk-good", "lk-mid", "lk-poor", "lk-na");
      if (LIKERT_CLS[lk.value]) lk.classList.add(LIKERT_CLS[lk.value]);
      lk.classList.toggle("set", !!lk.value);
      send("likert_set", { mid: lk.dataset.mid, val: lk.value });
      updateRatedCount();
      return;
    }
    var sl = e.target.closest(".sfari-fscore");
    if (sl) { send("fnscore_set", { fid: sl.dataset.fid, score: parseInt(sl.value, 10) }); return; }
    // Metric photo(s) chosen -> downscale, add thumbnail client-side, persist to server.
    var photo = e.target.closest(".sfari-photo");
    if (photo) {
      var pmid = photo.dataset.mid;
      var metricEl = photo.closest(".sfari-metric");
      var strip = metricEl ? metricEl.querySelector(".sfari-photos") : null;
      var files = Array.prototype.slice.call(photo.files || []);
      var used = strip ? strip.querySelectorAll(".sfari-thumb-wrap").length : 0;
      files.forEach(function (file, i) {
        if (used + i >= 6 || !/^image\//.test(file.type)) return;
        downscale(file, 1024, function (uri) {
          if (!uri) return;
          var id = "p" + Date.now() + "-" + Math.round(Math.random() * 1e6);
          if (strip) strip.insertBefore(thumbEl(pmid, id, uri), strip.querySelector(".sfari-photo-btn"));
          send("metric_photo_add", { mid: pmid, id: id, uri: uri });
        });
      });
      photo.value = "";
      return;
    }
  });

  // Live "N of M rated" counter in the section label (the panel is isolated server-side, so
  // it does not re-render on each rating — update it client-side instead).
  function updateRatedCount() {
    var el = document.querySelector(".sfari-sec-count");
    if (!el) return;
    var groups = document.querySelectorAll(".sfari-fnpanel .sfari-likert-select");
    var rated = 0;
    groups.forEach(function (g) { if (g.value) rated++; });
    el.textContent = rated + " of " + groups.length + " rated";
  }

  function debounce(el, fn) { clearTimeout(el._deb); el._deb = setTimeout(fn, 350); }
  function cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/"/g, '\\"'); }

  // Downscale an image file to <= maxDim on its longest side, JPEG data-URI.
  function downscale(file, maxDim, cb) {
    var reader = new FileReader();
    reader.onload = function (e) {
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        var cw = Math.max(1, Math.round(img.width * scale)), ch = Math.max(1, Math.round(img.height * scale));
        var c = document.createElement("canvas"); c.width = cw; c.height = ch;
        c.getContext("2d").drawImage(img, 0, 0, cw, ch);
        try { cb(c.toDataURL("image/jpeg", 0.82)); } catch (err) { cb(null); }
      };
      img.onerror = function () { cb(null); };
      img.src = e.target.result;
    };
    reader.onerror = function () { cb(null); };
    reader.readAsDataURL(file);
  }

  function thumbEl(mid, id, uri) {
    var wrap = document.createElement("span");
    wrap.className = "sfari-thumb-wrap"; wrap.dataset.id = id; wrap.dataset.mid = mid;
    var img = document.createElement("img"); img.className = "sfari-thumb"; img.src = uri; wrap.appendChild(img);
    var rm = document.createElement("button");
    rm.className = "sfari-photo-rm"; rm.type = "button"; rm.textContent = "×";
    rm.dataset.id = id; rm.dataset.mid = mid; wrap.appendChild(rm);
    return wrap;
  }
})();
