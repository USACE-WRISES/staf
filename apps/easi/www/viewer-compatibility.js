/* Nationwide compatibility renderer: decoded MVT -> temporary canvas -> PNG img.
 * No live canvas is attached to the map. Stored scores are never recomputed. */
(function () {
  "use strict";
  var TILE_SIZE = 512, CACHE_ENTRIES = 128, CACHE_BYTES = 32 * 1024 * 1024, CONCURRENCY = 6;
  var EMPTY = "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";
  function abortError() { var error = new Error("Tile cancelled"); error.name = "AbortError"; return error; }
  function Cache(maxEntries, maxBytes) { this.entries = new Map(); this.bytes = 0; this.maxEntries = maxEntries; this.maxBytes = maxBytes; }
  Cache.prototype.get = function (key) {
    var item = this.entries.get(key); if (!item) return undefined;
    this.entries.delete(key); this.entries.set(key, item); return item.value;
  };
  Cache.prototype.set = function (key, value, bytes) {
    var old = this.entries.get(key); if (old) { this.bytes -= old.bytes; this.entries.delete(key); }
    if (bytes > this.maxBytes) return;
    this.entries.set(key, { value: value, bytes: bytes }); this.bytes += bytes;
    while (this.entries.size > this.maxEntries || this.bytes > this.maxBytes) {
      var first = this.entries.keys().next().value; this.bytes -= this.entries.get(first).bytes; this.entries.delete(first);
    }
  };
  Cache.prototype.clear = function () { this.entries.clear(); this.bytes = 0; };
  function Queue(limit) { this.limit = limit; this.active = 0; this.waiting = []; }
  Queue.prototype.run = function (job, signal) {
    var queue = this;
    return new Promise(function (resolve, reject) {
      if (signal.aborted) { reject(abortError()); return; }
      var item = { job: job, signal: signal, resolve: resolve, reject: reject, abort: null };
      item.abort = function () {
        var index = queue.waiting.indexOf(item);
        if (index >= 0) { queue.waiting.splice(index, 1); reject(abortError()); }
      };
      signal.addEventListener("abort", item.abort, { once: true });
      queue.waiting.push(item); queue.pump();
    });
  };
  Queue.prototype.pump = function () {
    var queue = this;
    while (queue.active < queue.limit && queue.waiting.length) {
      var item = queue.waiting.shift(); item.signal.removeEventListener("abort", item.abort);
      if (item.signal.aborted) { item.reject(abortError()); continue; }
      queue.active += 1;
      (function (task) {
        Promise.resolve().then(function () { if (task.signal.aborted) throw abortError(); return task.job(); })
          .then(task.resolve, task.reject).finally(function () { queue.active -= 1; queue.pump(); });
      })(item);
    }
  };
  // Sibling display tiles share one source download/decode. A tile unloading
  // releases only its subscription; the last subscriber cancels the source.
  function SourcePool(queue, cache, loader) { this.queue = queue; this.cache = cache; this.loader = loader; this.pending = new Map(); }
  SourcePool.prototype.acquire = function (key, signal) {
    var pool = this;
    if (signal.aborted) return Promise.reject(abortError());
    var cached = pool.cache.get(key); if (cached !== undefined) return Promise.resolve(cached);
    var source = pool.pending.get(key);
    if (!source || source.controller.signal.aborted) {
      source = { controller: new AbortController(), users: new Set(), settled: false };
      pool.pending.set(key, source);
      (function (item) {
        item.promise = pool.queue.run(function () { return pool.loader(key, item.controller.signal); }, item.controller.signal)
          .then(function (features) {
            if (item.controller.signal.aborted) throw abortError();
            pool.cache.set(key, features, featureSize(features)); return features;
          }).finally(function () {
            item.settled = true; if (pool.pending.get(key) === item) pool.pending.delete(key);
          });
      })(source);
    }
    return new Promise(function (resolve, reject) {
      var user = {}, finished = false;
      function finish(error, features) {
        if (finished) return; finished = true;
        signal.removeEventListener("abort", cancel); source.users.delete(user);
        if (!source.users.size && !source.settled) source.controller.abort();
        if (error) reject(error); else resolve(features);
      }
      function cancel() { finish(abortError()); }
      source.users.add(user); signal.addEventListener("abort", cancel, { once: true });
      source.promise.then(function (features) { finish(null, features); }, function (error) { finish(error); });
    });
  };
  SourcePool.prototype.clear = function () {
    this.pending.forEach(function (source) { source.controller.abort(); }); this.pending.clear(); this.cache.clear();
  };
  function sourceCoords(display, maxLogicalZoom) {
    var z = Math.min(display.z, maxLogicalZoom + 1), scale = Math.pow(2, display.z - z);
    return { x: Math.floor(display.x / scale), y: Math.floor(display.y / scale), z: z };
  }
  // Liang-Barsky clipping keeps only the visible child plus a stroke/hit halo.
  // Never allocate a canvas or all sibling geometry at the parent overzoom size.
  function clipSegment(a, b, low, high) {
    var dx = b[0] - a[0], dy = b[1] - a[1], start = 0, end = 1;
    var p = [-dx, dx, -dy, dy], q = [a[0] - low, high - a[0], a[1] - low, high - a[1]];
    for (var i = 0; i < 4; i++) {
      if (!p[i]) { if (q[i] < 0) return null; continue; }
      var t = q[i] / p[i];
      if (p[i] < 0) start = Math.max(start, t); else end = Math.min(end, t);
      if (start > end) return null;
    }
    return [[a[0] + start * dx, a[1] + start * dy], [a[0] + end * dx, a[1] + end * dy]];
  }
  function displayFeatures(features, source, display, pad) {
    if (source.z === display.z) return features;
    pad = pad == null ? 16 : pad;
    var scale = Math.pow(2, display.z - source.z), offsetX = display.x - source.x * scale, offsetY = display.y - source.y * scale;
    var result = [];
    features.forEach(function (f) {
      var unit = TILE_SIZE * scale / f.extent, ox = offsetX * TILE_SIZE, oy = offsetY * TILE_SIZE, bounds = f.bounds;
      if (bounds[2] * unit - ox < -pad || bounds[0] * unit - ox > TILE_SIZE + pad ||
          bounds[3] * unit - oy < -pad || bounds[1] * unit - oy > TILE_SIZE + pad) return;
      var lines = [];
      f.lines.forEach(function (line) {
        var path = null;
        for (var i = 1; i < line.length; i++) {
          var a = [line[i - 1][0] * unit - ox, line[i - 1][1] * unit - oy];
          var b = [line[i][0] * unit - ox, line[i][1] * unit - oy];
          var clipped = clipSegment(a, b, -pad, TILE_SIZE + pad);
          if (!clipped) { path = null; continue; }
          var last = path && path[path.length - 1];
          if (last && Math.abs(last[0] - clipped[0][0]) < 1e-8 && Math.abs(last[1] - clipped[0][1]) < 1e-8) path.push(clipped[1]);
          else { path = clipped; lines.push(path); }
        }
      });
      if (lines.length) result.push({ properties: f.properties, extent: TILE_SIZE, lines: lines });
    }); return prepare(result);
  }
  function boundsAt(coords) {
    var count = Math.pow(2, coords.z - 1);
    function latitude(y) { return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / count))) * 180 / Math.PI; }
    return [coords.x / count * 360 - 180, latitude(coords.y + 1), (coords.x + 1) / count * 360 - 180, latitude(coords.y)];
  }
  function selectedVpus(config, coords) {
    var tile = boundsAt(coords);
    return (config.vpus || []).filter(function (vpu) {
      var b = (config.vpuBounds || {})[vpu];
      return !Array.isArray(b) || b.length !== 4 || (b[0] <= tile[2] && b[2] >= tile[0] && b[1] <= tile[3] && b[3] >= tile[1]);
    });
  }
  function featureSize(features) {
    return features.reduce(function (size, f) {
      return size + 256 + JSON.stringify(f.properties).length * 2 + f.lines.reduce(function (n, line) { return n + line.length * 128; }, 0);
    }, 0);
  }
  function prepare(features) {
    features.forEach(function (f) {
      var b = [Infinity, Infinity, -Infinity, -Infinity];
      f.lines.forEach(function (line) { line.forEach(function (p) { b[0] = Math.min(b[0], p[0]); b[1] = Math.min(b[1], p[1]); b[2] = Math.max(b[2], p[0]); b[3] = Math.max(b[3], p[1]); }); });
      f.bounds = b;
    }); return features;
  }
  function nearestInTiles(entries, projectedAt, zoom, hit, distance) {
    var best = null, bestDistance = hit + 1e-8, seen = new Map();
    entries.forEach(function (entry) {
      if (!entry.loaded) return;
      var scale = Math.pow(2, zoom - entry.coords.z), world = projectedAt(entry.coords.z);
      var point = { x: world.x - entry.coords.x * TILE_SIZE, y: world.y - entry.coords.y * TILE_SIZE };
      if (point.x < -hit / scale || point.y < -hit / scale || point.x > TILE_SIZE + hit / scale || point.y > TILE_SIZE + hit / scale) return;
      entry.features.forEach(function (f) {
        var unit = TILE_SIZE / f.extent, pad = hit / scale / unit;
        var x = point.x / unit, y = point.y / unit, b = f.bounds;
        if (x < b[0] - pad || x > b[2] + pad || y < b[1] - pad || y > b[3] + pad) return;
        var d = Infinity;
        f.lines.forEach(function (line) { for (var i = 1; i < line.length; i++)
          d = Math.min(d, distance({ x: x, y: y }, { x: line[i - 1][0], y: line[i - 1][1] }, { x: line[i][0], y: line[i][1] }) * unit * scale); });
        var id = String(f.properties.comid);
        if (seen.has(id) && seen.get(id) <= d) return; seen.set(id, d);
        if (d < bestDistance) { bestDistance = d; best = { properties: f.properties, feature: f, entry: entry, distance: d }; }
      });
    }); return best;
  }
  window.EASIViewerCompatibility = { Cache: Cache, Queue: Queue, SourcePool: SourcePool, sourceCoords: sourceCoords,
    displayFeatures: displayFeatures, clipSegment: clipSegment, boundsAt: boundsAt, selectedVpus: selectedVpus,
    prepare: prepare, nearestInTiles: nearestInTiles, featureSize: featureSize, TILE_SIZE: TILE_SIZE,
    CACHE_ENTRIES: CACHE_ENTRIES, CACHE_BYTES: CACHE_BYTES, CONCURRENCY: CONCURRENCY };
  window.EASIViewerRenderers = window.EASIViewerRenderers || {};
  window.EASIViewerRenderers.compatibility = function (ctx, initial, camera) {
    var L = window.L, C = ctx.shared, state = ctx.state, config = initial;
    var alive = true, revision = 0, moving = false, frame = null, pending = null, hoverId = null;
    var grid = null, coverage = null, coverageRequest = null, popup = null, highlight = null, requests = 0;
    var entries = new Set(), cache = new Cache(CACHE_ENTRIES, CACHE_BYTES), queue = new Queue(CONCURRENCY);
    var sources = new SourcePool(queue, cache, function (url, signal) {
      var token = revision;
      return window.fetch(url, { signal: signal }).then(function (response) {
        if (response.status === 204) return null;
        if (!response.ok) throw new Error("Screening tile HTTP " + response.status);
        return response.arrayBuffer();
      }).then(function (bytes) {
        if (!current(token) || signal.aborted) throw abortError();
        return bytes ? prepare(window.EASIVectorTile.decode(new Uint8Array(bytes))) : [];
      });
    });
    var center = camera ? camera.center : (config.center || [-96, 38.5]);
    var container = document.getElementById("easi-viewer-map"), originalClasses = new Set(container.classList || []);
    var originalTabIndex = container.getAttribute ? container.getAttribute("tabindex") : null;
    var map = L.map(container, { preferCanvas: false, zoomControl: false,
      center: [center[1], center[0]], zoom: (camera ? camera.zoom : (config.zoom == null ? 4 : config.zoom)) + 1,
      minZoom: 4, maxZoom: 17, zoomAnimation: true, fadeAnimation: false, worldCopyJump: false });
    state.map = map;
    L.control.zoom({ position: "topright" }).addTo(map);
    // USGSTopo's published maximum scale is native level 16. Overzoom its
    // imagery instead of requesting nonexistent higher-resolution tiles.
    L.tileLayer(C.BASEMAP, { maxNativeZoom: 16, maxZoom: 20, attribution: "USGS The National Map", noWrap: true }).addTo(map);
    map.createPane("easiCoverage").style.zIndex = "250";
    map.createPane("easiLines").style.zIndex = "350";
    map.createPane("easiHover").style.zIndex = "450";
    map.getPane("easiHover").style.pointerEvents = "none";
    function current(token) { return alive && ctx.current() && (token == null || token === revision); }
    function begin() { requests += 1; ctx.loading(); }
    function end() { requests = Math.max(0, requests - 1); if (!requests && current()) ctx.idle(); }
    function coverageOpacity() {
      var z = map.getZoom() - 1;
      return z <= 4 ? .45 : z <= 9 ? .45 - (z - 4) * .06 : Math.max(0, .15 - (z - 9) * .075);
    }
    function coverageStyle(feature) { return { color: "#8a93a3", weight: .6, opacity: .8,
      fillColor: C.COVER_COLORS[(feature.properties || {}).status] || C.COVER_COLORS.not_started, fillOpacity: coverageOpacity() }; }
    function leave() {
      pending = null; hoverId = null; state.hover = null; map.getContainer().style.cursor = "";
      if (highlight) { map.removeLayer(highlight); highlight = null; }
      if (popup) { map.removeLayer(popup); popup = null; }
    }
    function release(entry) {
      if (entry.released) return; entry.released = true;
      entry.controller.abort(); entry.features = []; entries.delete(entry);
      entry.image.onload = entry.image.onerror = null;
      if (entry.url) { URL.revokeObjectURL(entry.url); entry.url = null; }
      if (!entry.ended) { entry.ended = true; end(); }
    }
    function fetchTile(vpu, coords, signal) {
      // GridLayer gates its target zoom with minZoom. During animation the
      // map still reports the previous zoom, so validate the requested tile.
      if (!current() || config.available === false ||
          coords.z - 1 < config.minzoom || coords.z - 1 > config.maxzoom) return Promise.resolve([]);
      var url = C.tileUrl(config, vpu).replace("{z}", coords.z - 1).replace("{x}", coords.x).replace("{y}", coords.y);
      return sources.acquire(url, signal);
    }
    function makeGrid() {
      var Layer = L.GridLayer.extend({ createTile: function (coords, done) {
        var token = revision, image = document.createElement("img"); image.alt = ""; image.setAttribute("role", "presentation");
        var entry = { coords: { x: coords.x, y: coords.y, z: coords.z }, image: image, features: [], loaded: false,
          controller: new AbortController(), released: false, ended: false, url: null };
        entry.sourceCoords = sourceCoords(coords, config.maxzoom);
        image._easiEntry = entry; entries.add(entry); begin();
        function valid() { return current(token) && !entry.released; }
        function finish(error) {
          if (!valid()) return;
          if (error) ctx.failed(); entry.loaded = !error;
          if (!entry.ended) { entry.ended = true; end(); }
          done(error || null, image);
        }
        Promise.allSettled(selectedVpus(config, coords).map(function (vpu) { return fetchTile(vpu, entry.sourceCoords, entry.controller.signal); }))
          .then(function (results) {
            if (!valid()) return;
            var unique = new Set();
            results.forEach(function (result) {
              if (result.status !== "fulfilled") { if (result.reason.name !== "AbortError") ctx.failed(); return; }
              var pad = result.value.reduce(function (n, f) { return Math.max(n, C.widthAt(coords.z - 1, f.properties.order) / 2 + 1); }, C.HIT_PX + 1);
              displayFeatures(result.value, entry.sourceCoords, coords, pad).forEach(function (f) {
                var key = f.properties.comid + "/" + f.extent + "/" + JSON.stringify(f.lines);
                if (!unique.has(key)) { unique.add(key); entry.features.push(f); }
              });
            });
            image.onload = function () { finish(null); };
            image.onerror = function () { finish(new Error("Screening image failed")); };
            if (!entry.features.length) { image.src = EMPTY; return; }
            // Detached, short-lived canvas. The Leaflet tile is always an image.
            var canvas = document.createElement("canvas"), dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = canvas.height = TILE_SIZE * dpr;
            var pen = canvas.getContext("2d"); if (!pen) throw new Error("Tile rasterization is unavailable");
            pen.scale(dpr, dpr); pen.lineCap = pen.lineJoin = "round"; pen.globalAlpha = .95;
            var logicalZoom = coords.z - 1;
            // Batch only equal styles, preserving source order where styles differ.
            var style = null;
            entry.features.forEach(function (f) {
              var color = C.BAND_COLORS[f.properties.band] || C.BAND_COLORS.pending;
              var width = C.widthAt(logicalZoom, f.properties.order), next = color + "/" + width;
              if (next !== style) { if (style !== null) pen.stroke(); pen.beginPath(); pen.strokeStyle = color; pen.lineWidth = width; style = next; }
              var unit = TILE_SIZE / f.extent;
              f.lines.forEach(function (line) { line.forEach(function (p, i) { if (!i) pen.moveTo(p[0] * unit, p[1] * unit); else pen.lineTo(p[0] * unit, p[1] * unit); }); });
            });
            if (style !== null) pen.stroke();
            canvas.toBlob(function (blob) {
              canvas.width = canvas.height = 0;
              if (!valid()) return;
              if (!blob) { finish(new Error("Screening image could not be created")); return; }
              entry.url = URL.createObjectURL(blob); image.src = entry.url;
            }, "image/png");
          }).catch(function (error) { if (valid() && error.name !== "AbortError") finish(error); });
        return image;
      } });
      var result = new Layer({ pane: "easiLines", tileSize: TILE_SIZE, noWrap: true, keepBuffer: 1,
        minZoom: config.minzoom + 1, maxZoom: 17, minNativeZoom: config.minzoom + 1,
        maxNativeZoom: 17, updateWhenIdle: true, updateWhenZooming: false, className: "easi-screening-tile" });
      result.on("tileunload", function (event) { if (event.tile._easiEntry) release(event.tile._easiEntry); });
      return result;
    }
    function nearest(point) {
      if (!current() || config.available === false || map.getZoom() - 1 < config.minzoom || !point) return null;
      var latlng = map.containerPointToLatLng(point), projected = new Map();
      return nearestInTiles(entries, function (z) { if (!projected.has(z)) projected.set(z, map.project(latlng, z)); return projected.get(z); }, map.getZoom(), C.HIT_PX, C.segmentDistance);
    }
    function hover(event) {
      if (!current() || moving) return;
      var found = nearest(event.containerPoint); if (!found) { leave(); return; }
      var id = String(found.properties.comid);
      if (id !== hoverId) {
        leave(); hoverId = id; state.hover = { comid: id };
        var paths = [];
        entries.forEach(function (entry) {
          if (!entry.loaded) return;
          entry.features.forEach(function (f) {
            if (String(f.properties.comid) !== id) return;
            f.lines.forEach(function (line) { paths.push(line.map(function (p) {
              return map.unproject(L.point((entry.coords.x + p[0] / f.extent) * TILE_SIZE, (entry.coords.y + p[1] / f.extent) * TILE_SIZE), entry.coords.z);
            })); });
          });
        });
        var width = C.widthAt(map.getZoom() - 1, found.properties.order) * 1.8;
        highlight = L.layerGroup().addTo(map);
        if (map.getZoom() - 1 >= 7) L.polyline(paths, { pane: "easiHover", interactive: false, color: "#fff", opacity: .9, weight: width + 8, className: "easi-reach-glow" }).addTo(highlight);
        L.polyline(paths, { pane: "easiHover", interactive: false, color: C.BAND_COLORS[found.properties.band] || C.BAND_COLORS.pending, opacity: .95, weight: width }).addTo(highlight);
        popup = L.popup({ closeButton: false, closeOnClick: false, autoPan: false, offset: [0, -10], className: "easi-viewer-hover" })
          .setContent("<div class='easi-viewer-popup'>" + C.describe(found.properties) + "</div>");
      }
      map.getContainer().style.cursor = "pointer"; popup.setLatLng(event.latlng).openOn(map);
    }
    map.on("mousemove", function (event) {
      if (moving) return; pending = event; if (frame != null) return;
      frame = window.requestAnimationFrame(function () { frame = null; if (pending) hover(pending); });
    });
    map.on("mouseout", leave);
    map.on("movestart", function () { moving = true; leave(); });
    map.on("moveend", function () { moving = false; });
    function syncZoom() { if (current()) ctx.zoom(map.getZoom() - 1); }
    map.on("zoom", syncZoom);
    map.on("zoomend", function () { syncZoom(); leave(); if (coverage) coverage.setStyle(coverageStyle); });
    map.on("click", function (event) { if (!current() || moving) return; var found = nearest(event.containerPoint); if (found) ctx.pick(found.properties); });
    function clear() {
      revision += 1; leave();
      if (coverageRequest) { coverageRequest.abort(); coverageRequest = null; }
      if (grid) { map.removeLayer(grid); grid = null; }
      entries.forEach(release);
      if (coverage) { map.removeLayer(coverage); coverage = null; }
      sources.clear(); requests = 0; ctx.idle();
    }
    function apply() {
      syncZoom();
      if (config.available === false) return;
      var token = revision, controller = new AbortController(); coverageRequest = controller; begin();
      window.fetch(C.metaUrl(config, "coverage"), { signal: controller.signal }).then(function (response) {
        if (!response.ok) throw new Error("Coverage HTTP " + response.status); return response.json();
      }).then(function (data) {
        if (current(token) && !controller.signal.aborted) coverage = L.geoJSON(data, { pane: "easiCoverage", interactive: false, style: coverageStyle }).addTo(map);
      }).catch(function (error) { if (current(token) && error.name !== "AbortError") ctx.failed(); })
        .finally(function () { if (current(token)) { coverageRequest = null; end(); } });
      grid = makeGrid().addTo(map);
    }
    var resize = window.ResizeObserver ? new window.ResizeObserver(function () { if (current()) map.invalidateSize({ pan: false }); }) : null;
    if (resize) resize.observe(map.getContainer());
    apply();
    return { nearest: nearest,
      visibleReaches: function (limit) {
        var found = [], seen = new Set(), size = map.getSize();
        entries.forEach(function (entry) {
          if (!entry.loaded) return;
          entry.features.forEach(function (f) {
            if (found.length >= (limit || 20) || seen.has(String(f.properties.comid))) return;
            for (var j = 0; j < f.lines.length; j++) {
              var line = f.lines[j]; if (line.length < 2) continue;
              var a = line[0], b = line[1];
              var latlng = map.unproject(L.point((entry.coords.x + (a[0] + b[0]) / 2 / f.extent) * TILE_SIZE,
                (entry.coords.y + (a[1] + b[1]) / 2 / f.extent) * TILE_SIZE), entry.coords.z);
              var point = map.latLngToContainerPoint(latlng);
              if (point.x >= 8 && point.y >= 8 && point.x < size.x - 8 && point.y < size.y - 8) {
                seen.add(String(f.properties.comid)); found.push({ properties: f.properties, point: { x: point.x, y: point.y } }); break;
              }
            }
          });
        }); return found;
      },
      camera: function () { var c = map.getCenter(); return { center: [c.lng, c.lat], zoom: map.getZoom() - 1 }; },
      refresh: function (next) { clear(); config = next; apply(); },
      destroy: function () { clear(); alive = false; if (frame != null) window.cancelAnimationFrame(frame);
        if (resize) resize.disconnect(); map.remove();
        // Leaflet.remove() leaves container classes behind. Do not carry its
        // touch/cursor rules into the Standard renderer on the same element.
        if (container.classList) Array.from(container.classList).forEach(function (name) {
          if (name.indexOf("leaflet-") === 0 && !originalClasses.has(name)) container.classList.remove(name);
        });
        if (container.setAttribute) {
          if (originalTabIndex === null) container.removeAttribute("tabindex");
          else container.setAttribute("tabindex", originalTabIndex);
        }
      },
      diagnostics: function () { return { active: queue.active, queued: queue.waiting.length, tiles: entries.size,
        cacheEntries: cache.entries.size, cacheBytes: cache.bytes, sourceRequests: sources.pending.size, revision: revision }; }
    };
  };
})();
