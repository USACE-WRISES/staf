/* DEEP measure-worksheet interactions.
 *
 * Metric values are raw numeric inputs. Each edit updates that metric's index
 * chip and the computed function score CLIENT-SIDE (no round-trip flicker) using
 * the reference-curve points embedded on the metric block, and notifies the
 * server via Shiny.setInputValue so the live rollup + report recompute
 * authoritatively. Mirrors the non-binding shadow-copy pattern EASI/SFARI use.
 */
(function () {
  function send(id, payload) {
    if (window.Shiny && Shiny.setInputValue) {
      payload._t = Date.now();
      Shiny.setInputValue(id, payload, { priority: "event" });
    }
  }
  function clamp01(y) { return y < 0 ? 0 : (y > 1 ? 1 : y); }

  // Piecewise-linear interpolation — mirror of deep/curves.interp_curve.
  function interp(points, x) {
    if (!points || !points.length) return null;
    var p = points.slice().sort(function (a, b) { return a.x - b.x; });
    if (p.length === 1 || x <= p[0].x) return clamp01(p[0].y);
    if (x >= p[p.length - 1].x) return clamp01(p[p.length - 1].y);
    for (var i = 0; i < p.length - 1; i++) {
      if (p[i].x <= x && x <= p[i + 1].x) {
        var s = p[i + 1].x - p[i].x;
        if (s <= 0) return clamp01(p[i + 1].y);
        var t = (x - p[i].x) / s;
        return clamp01(p[i].y + t * (p[i + 1].y - p[i].y));
      }
    }
    return clamp01(p[p.length - 1].y);
  }
  // Endpoint-clamp advisory — mirror of deep/curves.domain_warning.
  function domainWarning(points, x) {
    if (!points || !points.length) return null;
    var xs = points
      .filter(function (p) { return p && p.x != null && p.y != null; })
      .map(function (p) { return +p.x; })
      .sort(function (a, b) { return a - b; });
    if (xs.length < 2) return null;
    var lo = xs[0], hi = xs[xs.length - 1];
    // Four decimals at most, so a seed edge such as 2.086666666666667 reads 2.0867 (mirrors deep/curves.py).
    var fmtB = function (v) { return String(Math.round(v * 1e4) / 1e4); };
    if (x < lo) return "value " + fmtB(x) + " is below the curve domain [" + fmtB(lo) + ", " + fmtB(hi) + "]; score clamped to the endpoint";
    if (x > hi) return "value " + fmtB(x) + " is above the curve domain [" + fmtB(lo) + ", " + fmtB(hi) + "]; score clamped to the endpoint";
    return null;
  }
  function idxColor(v) {
    if (v == null) return "#eef1f6";
    if (v <= 0.39) return "#f5b5b5";
    if (v <= 0.69) return "#f5e7a6";
    return "#c8d9f2";
  }
  function fnBand(v) {
    if (v <= 5) return { l: "Non-Functioning", c: "#f5b5b5" };
    if (v <= 10) return { l: "Functioning-at-Risk", c: "#f5e7a6" };
    return { l: "Functioning", c: "#c8d9f2" };
  }
  function pointsOf(metricEl) {
    try { return JSON.parse(metricEl.getAttribute("data-points") || "[]"); }
    catch (e) { return []; }
  }
  function metricIndex(metricEl) {
    var input = metricEl.querySelector(".deep-metric-input");
    var na = metricEl.querySelector(".deep-na");
    if (na && na.checked) return null;
    if (!input || input.value === "" || input.value == null) return null;
    var v = parseFloat(input.value);
    if (isNaN(v)) return null;
    return interp(pointsOf(metricEl), v);
  }
  function idxLabel(v) {
    if (v == null) return "—";
    if (v <= 0.39) return "Non-Functioning";
    if (v <= 0.69) return "Functioning-at-Risk";
    return "Functioning";
  }
  function rawValue(metricEl) {
    var input = metricEl.querySelector(".deep-metric-input");
    var na = metricEl.querySelector(".deep-na");
    if (na && na.checked) return null;
    if (!input || input.value === "" || input.value == null) return null;
    var v = parseFloat(input.value);
    return isNaN(v) ? null : v;
  }
  // Reposition the plot's site marker from the geometry embedded on the SVG.
  function updateMarker(metricEl, val, idx) {
    var svg = metricEl.querySelector(".deep-curve");
    if (!svg) return;
    var v = svg.querySelector(".deep-mk-v"), hh = svg.querySelector(".deep-mk-h"), dot = svg.querySelector(".deep-mk-dot");
    if (val == null || idx == null) {
      [v, hh, dot].forEach(function (el) { if (el) el.setAttribute("visibility", "hidden"); });
      return;
    }
    var x0 = parseFloat(svg.getAttribute("data-x0")), x1 = parseFloat(svg.getAttribute("data-x1"));
    var y0 = parseFloat(svg.getAttribute("data-y0")), y1 = parseFloat(svg.getAttribute("data-y1"));
    var xmin = parseFloat(svg.getAttribute("data-xmin")), xmax = parseFloat(svg.getAttribute("data-xmax"));
    var dx = (xmax - xmin) || 1;
    var vx = val < xmin ? xmin : (val > xmax ? xmax : val);
    var px = x0 + (vx - xmin) / dx * (x1 - x0);
    var py = y1 - clamp01(idx) * (y1 - y0);
    if (v) { v.setAttribute("x1", px.toFixed(1)); v.setAttribute("x2", px.toFixed(1));
             v.setAttribute("y1", y1.toFixed(1)); v.setAttribute("y2", py.toFixed(1)); v.removeAttribute("visibility"); }
    if (hh) { hh.setAttribute("x1", x0.toFixed(1)); hh.setAttribute("x2", px.toFixed(1));
              hh.setAttribute("y1", py.toFixed(1)); hh.setAttribute("y2", py.toFixed(1)); hh.removeAttribute("visibility"); }
    if (dot) { dot.setAttribute("cx", px.toFixed(1)); dot.setAttribute("cy", py.toFixed(1)); dot.removeAttribute("visibility"); }
  }
  // Toggle the metric's endpoint-clamp advisory (hidden when in-domain / unset).
  function updateWarn(metricEl, val) {
    var el = metricEl.querySelector(".deep-domain-warn");
    if (!el) return;
    var msg = (val == null) ? null : domainWarning(pointsOf(metricEl), val);
    if (msg) { el.textContent = msg; el.hidden = false; }
    else { el.textContent = ""; el.hidden = true; }
  }
  function updateMetric(metricEl) {
    var val = rawValue(metricEl);
    var idx = (val == null) ? null : interp(pointsOf(metricEl), val);
    var chip = metricEl.querySelector(".deep-metric-index");
    if (chip) { chip.textContent = (idx == null ? "—" : idx.toFixed(2) + " · " + idxLabel(idx)); chip.style.background = idxColor(idx); }
    updateMarker(metricEl, val, idx);
    updateWarn(metricEl, val);
  }
  function updateFunction() {
    var card = document.querySelector(".deep-scorecard");
    var panel = document.querySelector(".sfari-fnpanel-inner");
    if (!card || !panel) return;
    var vals = [];
    panel.querySelectorAll(".deep-metric").forEach(function (mEl) {
      var i = metricIndex(mEl); if (i != null) vals.push(i);
    });
    var num = card.querySelector(".deep-fscore-num");
    var band = card.querySelector(".deep-fscore-band");
    var knob = card.querySelector(".deep-fscore-knob");
    if (!vals.length) {
      card.classList.add("unset");
      if (num) num.textContent = "–";
      if (band) { band.textContent = "Not scored yet"; band.style.background = "#e7ebf1"; }
      updateEnteredCount();
      return;
    }
    card.classList.remove("unset");
    var score = (vals.reduce(function (a, b) { return a + b; }, 0) / vals.length) * 15;
    var b = fnBand(score);
    if (num) num.textContent = score.toFixed(1);
    if (band) { band.textContent = b.l; band.style.background = b.c; }
    if (knob) knob.style.left = (score / 15 * 100).toFixed(1) + "%";
    updateEnteredCount();
  }
  // Live "N/M entered" footer counter (the panel is isolated server-side, so it does not
  // re-render on each keystroke — update it client-side instead). A metric is entered when
  // it is marked N/A or holds a parseable value.
  function updateEnteredCount() {
    var panel = document.querySelector(".sfari-fnpanel-inner");
    if (!panel) return;
    var metrics = panel.querySelectorAll(".deep-metric"), entered = 0;
    metrics.forEach(function (mEl) {
      var na = mEl.querySelector(".deep-na");
      var input = mEl.querySelector(".deep-metric-input");
      if ((na && na.checked) || (input && input.value !== "" && !isNaN(parseFloat(input.value)))) entered++;
    });
    var foot = document.querySelector(".sfari-foot-rated");
    if (foot) foot.textContent = entered + "/" + metrics.length + " entered";
  }
  function scrollPanelTop() {
    var p = document.querySelector(".sfari-fnpanel");
    if (p) p.scrollTop = 0;
  }
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

  document.addEventListener("input", function (e) {
    var mi = e.target.closest(".deep-metric-input");
    if (mi) {
      var mEl = mi.closest(".deep-metric");
      if (mEl) { updateMetric(mEl); updateFunction(); send("measure_set", { mid: mEl.getAttribute("data-metric"), value: mi.value }); }
      return;
    }
    var note = e.target.closest(".sfari-metric-note");
    if (note) { debounce(note, function () { send("measure_note", { mid: note.dataset.midNote, note: note.value }); }); return; }
  });

  document.addEventListener("change", function (e) {
    var strat = e.target.closest(".deep-stratum-select");
    if (strat) { send("measure_stratum", { mid: strat.dataset.midStratum, stratum: strat.value }); return; }
    var na = e.target.closest(".deep-na");
    if (na) {
      var mEl = na.closest(".deep-metric");
      var input = mEl ? mEl.querySelector(".deep-metric-input") : null;
      if (input) input.disabled = na.checked;
      if (mEl) { updateMetric(mEl); updateFunction(); }
      send("measure_na", { mid: na.dataset.midNa, na: na.checked });
      return;
    }
    // Metric photo(s) chosen -> downscale, add a thumbnail client-side, persist to server.
    var photo = e.target.closest(".sfari-photo");
    if (photo) {
      var pmid = photo.dataset.mid;
      var metricEl = photo.closest(".deep-metric");
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

  // The step anchors carry no href, so the browser gives them no keyboard activation of
  // its own; they carry tabindex instead and this supplies Enter and Space.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    var step = e.target.closest && e.target.closest("[data-step]");
    if (!step) return;
    e.preventDefault();                     // Space would scroll the page otherwise
    send("step_nav", { key: step.dataset.step });
  });

  document.addEventListener("click", function (e) {
    // Step navigator. One event id for both steppers (left pane + worksheet rail), so
    // neither can register a Shiny input the other already owns.
    var step = e.target.closest("[data-step]");
    if (step) { send("step_nav", { key: step.dataset.step }); return; }

    var rep = e.target.closest("[data-report]");
    if (rep) { send("open_report_evt", {}); return; }
    // Prev / Next / jump. "Done" (Next on the last function) opens the report; every other
    // move also scrolls the panel back to the top of the new function.
    var nav = e.target.closest("[data-nav]");
    if (nav) {
      var d = parseInt(nav.dataset.nav, 10);
      var active = document.querySelector(".sfari-nav-fn.active");
      var lastIdx = document.querySelectorAll(".sfari-nav-fn").length - 1;
      if (d === 1 && active && parseInt(active.dataset.idx, 10) === lastIdx) {
        send("open_report_evt", {});
      } else {
        scrollPanelTop();
        send("nav_move", { d: d });
      }
      return;
    }
    var jump = e.target.closest(".sfari-nav-fn");
    if (jump && jump.dataset.idx !== undefined) {
      scrollPanelTop();
      send("nav_jump", { i: parseInt(jump.dataset.idx, 10) });
      return;
    }
    // Remove a metric photo.
    var prm = e.target.closest(".sfari-photo-rm");
    if (prm) {
      var pw = prm.closest(".sfari-thumb-wrap"); if (pw) pw.remove();
      send("metric_photo_remove", { mid: prm.dataset.mid, id: prm.dataset.id });
      return;
    }
  });

  function debounce(el, fn) { clearTimeout(el._deb); el._deb = setTimeout(fn, 350); }
})();
