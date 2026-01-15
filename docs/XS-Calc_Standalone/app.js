// ---------- Config ----------
const USGS_BASE =
  'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer';
const USGS_GETSAMPLES = `${USGS_BASE}/getSamples`;
const USGS_QUERY = `${USGS_BASE}/query`;
const MAX_SAMPLES = 1000; // enforce client-side

// Detect if we are embedded in an iframe (modal)
const EMBEDDED = (function (){
  try { return window.self !== window.top || new URLSearchParams(location.search).has('embedded'); }
  catch { return true; }
})();

// If embedded, add a class for CSS to reveal embed-only buttons
if (EMBEDDED) document.documentElement.classList.add('is-embedded');

// ---------- Map UI ----------
const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
});

// Esri World Imagery (aerial)
const imageryOnly = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, attribution: 'Source: Esri, Maxar, Earthstar Geographics' }
);

// Hybrid = Imagery + Hydro reference overlay (streams) from Esri "Reference/World_Hydro_Reference_Overlay"
const imageryForHybrid = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19 }
);
const hydroOverlay = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Hydro_Reference_Overlay/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, opacity: 0.9, attribution: 'Esri Hydro Reference Overlay' }
);
const hybrid = L.layerGroup([imageryForHybrid, hydroOverlay]);

// Create map with zoomControl disabled so we can add geocoder first (above zoom)
const map = L.map('map', { layers: [osm], zoomControl: false }).setView([39.0, -96.5], 5);

// Search control (always open, top-left)
const geocoder = L.Control.geocoder({
  defaultMarkGeocode: false,
  collapsed: false,
  position: 'topleft',
  placeholder: 'Search places (e.g., rivers, cities)…'
})
.on('markgeocode', e => {
  const b = e.geocode.bbox || L.latLngBounds(e.geocode.center, e.geocode.center);
  map.fitBounds(b.pad(0.2));
})
.addTo(map);

// Now add zoom control after geocoder so it appears below it
L.control.zoom({ position: 'topleft' }).addTo(map);

L.control.layers(
  { 'Streets (OSM)': osm, 'Aerial (Esri World Imagery)': imageryOnly, 'Hybrid (Aerial + Hydro)': hybrid },
  null,
  { position: 'topright', collapsed: true }
).addTo(map);

L.control.scale().addTo(map);

// Draw control (only one polyline at a time)
const drawn = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    polygon: false, circle: false, rectangle: false, marker: false, circlemarker: false,
    polyline: { shapeOptions: { weight: 3 } }
  },
  edit: { featureGroup: drawn, edit: true, remove: true }
});
map.addControl(drawControl);

let currentLine = null;

// Disable double-click zoom while drawing (so double-click finishes the line)
map.on('draw:drawstart', () => map.doubleClickZoom.disable());
map.on('draw:drawstop',  () => map.doubleClickZoom.enable());

// Manage layers + results
map.on(L.Draw.Event.CREATED, e => {
  drawn.clearLayers();
  currentLine = e.layer;
  drawn.addLayer(currentLine);
  cancelActive(); // cancel any in-flight request
  clearResults('New cross‑section drawn. Click “Get profile”.');
});
map.on('draw:deleted', () => {
  currentLine = null;
  cancelActive();
  clearResults('Cross‑section deleted.');
});
map.on('draw:edited', () => {
  cancelActive();
  clearResults('Cross‑section edited. Click “Get profile” to refresh.');
});

// UI hooks
const btnGet    = document.getElementById('getProfile');
const btnCancel = document.getElementById('cancelBtn');
const btnCsv    = document.getElementById('downloadCsv');

// New: Import / Close buttons when embedded
const btnImport = document.getElementById('importBtn');
const btnClose  = document.getElementById('closeBtn');

document.getElementById('getProfile').addEventListener('click', onGetProfile);
document.getElementById('cancelBtn').addEventListener('click', cancelActive);
document.getElementById('downloadCsv').addEventListener('click', downloadCSV);
document.getElementById('units').addEventListener('change', reRenderInNewUnits);

if (btnImport) btnImport.addEventListener('click', onImportToParent);
if (btnClose)  btnClose .addEventListener('click', onCloseRequested);

// Hide embed-only buttons if standalone
if (!EMBEDDED) {
  if (btnImport) btnImport.style.display = 'none';
  if (btnClose)  btnClose .style.display = 'none';
}

const statusEl     = document.getElementById('status');
const resultTable  = document.getElementById('resultTable');
const sourceWrap   = document.getElementById('sourceWrap');
const resBreakdown = document.getElementById('resBreakdown');
const profileCanvas= document.getElementById('profileCanvas');

// ---------- State for re-render ----------
let latest = {
  stations_m: [],
  elevs_m: [],
  points: [],
  samples: [],
  catalog: []
};

// ---------- Cancellation plumbing ----------
let activeCancel = null;
function newCancelState() {
  return { canceled:false, abortControllers:[], jsonpCleanups:[] };
}
function registerController(cs, controller) {
  cs.abortControllers.push(controller);
}
function registerJsonpCleanup(cs, fn) {
  cs.jsonpCleanups.push(fn);
}
function cancelAll(cs) {
  if (!cs) return;
  cs.canceled = true;
  cs.abortControllers.forEach(c => { try { c.abort(); } catch {} });
  cs.jsonpCleanups.forEach(fn => { try { fn(); } catch {} });
  cs.abortControllers = [];
  cs.jsonpCleanups = [];
}
function cancelActive() {
  if (activeCancel) {
    cancelAll(activeCancel);
    setStatus('Canceled.');
    setDownloading(false);
  }
}

// ---------- Core flow ----------
async function onGetProfile() {
  try {
    setStatus('');
    sourceWrap.innerHTML = '';
    resBreakdown.innerHTML = '';
    resultTable.innerHTML = '';
    btnCsv.disabled = true;
    updateImportUi();

    if (!currentLine) { setStatus('Draw a cross‑section line first.', 'warn'); return; }

    setDownloading(true);
    activeCancel = newCancelState();

    const spacing = Number(document.getElementById('spacing').value || 5); // meters
    const outUnits = document.getElementById('units').value;

    const gj = currentLine.toGeoJSON();
    const lineCoords = gj.geometry.coordinates;
    if (!Array.isArray(lineCoords) || lineCoords.length < 2) {
      setStatus('The line needs at least two vertices.', 'warn'); return;
    }

    // Compute length and sample count; enforce hard cap (1000)
    const km = turf.length(turf.lineString(lineCoords), { units: 'kilometers' });
    const length_m = km * 1000;
    const idealCount = Math.ceil(length_m / spacing) + 1;
    const sampleCount = clamp(idealCount, 2, MAX_SAMPLES);

    const effSpacing_m = (sampleCount > 1) ? length_m / (sampleCount - 1) : length_m;
    const clamped = idealCount > MAX_SAMPLES;

    setStatus(`Requesting ${sampleCount} samples${clamped ? ` (clamped; effective spacing ≈ ${effSpacing_m.toFixed(2)} m)` : ''}…`);

    // Build esri polyline geometry
    const esriPolyline = {
      paths: [ lineCoords.map(([lon, lat]) => [lon, lat]) ],
      spatialReference: { wkid: 4326 }
    };

    // --- 1) 3DEP samples along the line (CORS first, JSONP fallback) ---
    const sp = new URLSearchParams({
      f: 'json',
      geometryType: 'esriGeometryPolyline',
      geometry: JSON.stringify(esriPolyline),
      sampleCount: String(sampleCount),
      returnFirstValueOnly: 'true',
      // keep default "best available" via mosaicRule (optional but explicit)
      mosaicRule: JSON.stringify({
        mosaicMethod: 'esriMosaicAttribute',
        sortField: 'Best',
        sortValue: 0
      })
    });
    const samplesUrl = `${USGS_GETSAMPLES}?${sp.toString()}`;
    const sampleJson = await fetchJsonWithCorsOrJsonp(samplesUrl, activeCancel, 20000);
    if (activeCancel.canceled) throw new Error('Canceled');
    if (!sampleJson || !sampleJson.samples || !sampleJson.samples.length) {
      throw new Error('No samples returned from USGS 3DEP.');
    }

    // Build arrays
    const pts = sampleJson.samples.map(s => ({
      lon: s.location?.x, lat: s.location?.y,
      elev_m: Number(s.value)
    }));

    // geodesic stationing along returned sample points (meters)
    const stations_m = [];
    let cum_m = 0;
    for (let i = 0; i < pts.length; i++) {
      if (i > 0) {
        const segKm = turf.length(turf.lineString([
          [pts[i-1].lon, pts[i-1].lat],
          [pts[i].lon, pts[i].lat]
        ]), { units: 'kilometers' });
        cum_m += segKm * 1000;
      }
      stations_m.push(cum_m);
    }

    // --- 2) Unique catalog items intersecting the line (Primary only if available) ---
    // If the service lacks Category, the where clause will simply return 0; so we try two passes.
    let catalog = [];
    try {
      catalog = await queryCatalog(esriPolyline, 'Category=1'); // Primary
      if (!catalog.length) catalog = await queryCatalog(esriPolyline, '1=1'); // Fallback
    } catch {
      catalog = await queryCatalog(esriPolyline, '1=1');
    }

    // Save raw (meters) for re-rendering in different units
    latest.stations_m = stations_m;
    latest.elevs_m = pts.map(p => p.elev_m);
    latest.points = pts.map(p => ({ lat: p.lat, lon: p.lon }));
    latest.catalog = catalog;

    // Render table + chart in chosen units
    renderAll(outUnits);

    btnCsv.disabled = false;
    updateImportUi();
    setStatus(`Got ${pts.length} points from USGS 3DEP.`);
  } catch (err) {
    if (err && (err.name === 'AbortError' || /Canceled/i.test(String(err)))) {
      setStatus('Canceled.');
    } else {
      console.error(err);
      setStatus(err.message || String(err), 'err');
    }
  } finally {
    setDownloading(false);
    activeCancel = null;
  }
}

// ---------- ImageServer catalog (unique sources intersecting the line) ----------
async function queryCatalog(esriPolyline, whereClause) {
  const q = new URLSearchParams({
    f: 'json',
    where: whereClause,
    geometryType: 'esriGeometryPolyline',
    geometry: JSON.stringify(esriPolyline),
    spatialRel: 'esriSpatialRelIntersects',
    returnGeometry: 'false',
    outFields: [
      'Dataset_ID','Name','ProductName','URL','Metadata',
      'LowPS','Resolution_X','Resolution_Y','Source','DEM_Type','Best'
    ].join(',')
  });
  const url = `${USGS_QUERY}?${q.toString()}`;
  const j = await fetchJsonWithCorsOrJsonp(url, activeCancel, 20000);
  if (activeCancel?.canceled) throw new Error('Canceled');
  return normalizeCatalog(j);
}

function normalizeCatalog(json) {
  const feats = (json && json.features) ? json.features : [];
  const seen = new Map();
  for (const f of feats) {
    const a = f.attributes || {};
    const key = a.Dataset_ID || a.Name || Math.random().toString(36).slice(2);
    if (!seen.has(key)) {
      seen.set(key, {
        id: key,
        name: a.Name || '(unnamed)',
        product: a.ProductName || '',
        url: a.URL || '',
        meta: a.Metadata || '',
        lowps: (a.LowPS != null ? Number(a.LowPS) : (a.Resolution_Y != null ? Number(a.Resolution_Y) : null)),
        resx: a.Resolution_X || null,
        resy: a.Resolution_Y || null,
        source: a.Source || '',
        demType: a.DEM_Type,
        best: a.Best
      });
    }
  }
  return Array.from(seen.values()).slice(0, 200);
}

// ---------- Fetch helpers with cancellation + JSONP fallback ----------
async function fetchJsonWithCorsOrJsonp(url, cancelState, timeoutMs = 15000) {
  // Try CORS fetch first with AbortController
  try {
    const controller = new AbortController();
    if (cancelState) registerController(cancelState, controller);
    const resp = await fetch(url, { method: 'GET', mode: 'cors', cache: 'no-store', signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    return await resp.json();
  } catch (e) {
    if (cancelState?.canceled) throw new Error('Canceled');
    // Fallback to JSONP
    return await jsonpCancelable(url, cancelState, timeoutMs);
  }
}

function jsonpCancelable(baseUrl, cancelState, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    const cbName = 'jsonp_cb_' + Math.random().toString(36).slice(2);
    const url = baseUrl + (baseUrl.includes('?') ? '&' : '?') + 'callback=' + cbName + (baseUrl.includes('f=') ? '' : '&f=json');

    const script = document.createElement('script');
    let timer;

    function cleanup() {
      try { delete window[cbName]; } catch {}
      if (script.parentNode) script.parentNode.removeChild(script);
      if (timer) clearTimeout(timer);
    }
    if (cancelState) registerJsonpCleanup(cancelState, cleanup);

    window[cbName] = (data) => { cleanup(); resolve(data); };

    script.src = url;
    script.onerror = () => { cleanup(); reject(new Error('JSONP network error')); };
    document.head.appendChild(script);
    timer = setTimeout(() => { cleanup(); reject(new Error('JSONP timeout')); }, timeoutMs);

    if (cancelState?.canceled) {
      cleanup();
      reject(new Error('Canceled'));
    }
  });
}

// ---------- Rendering ----------
function renderAll(outUnits) {
  const useFeet = outUnits === 'feet';
  const stations = latest.stations_m.map(m => useFeet ? mToFt(m) : m);
  const elevs = latest.elevs_m.map(m => useFeet ? mToFt(m) : m);
  const units = useFeet ? 'ft' : 'm';

  latest.samples = stations.map((s,i) => ({
    station: s, elevation: elevs[i],
    lat: latest.points[i].lat, lon: latest.points[i].lon
  }));

  renderTable(latest.samples, units);
  drawProfile('profileCanvas', stations, elevs, units);
  renderSourcesAndRes(latest.catalog);
  updateImportUi();
}

function renderTable(rows, units) {
  if (!rows.length) { resultTable.innerHTML = ''; return; }
  const head = `
    <thead><tr>
      <th>ID</th>
      <th>Station (${units})</th>
      <th>Elevation (${units})</th>
      <th>Lat</th>
      <th>Lon</th>
    </tr></thead>`;
  const body = rows.map((r, idx) => `
    <tr>
      <td>${idx + 1}</td>
      <td>${Number(r.station).toFixed(2)}</td>
      <td>${Number(r.elevation).toFixed(2)}</td>
      <td>${Number(r.lat).toFixed(6)}</td>
      <td>${Number(r.lon).toFixed(6)}</td>
    </tr>`).join('');
  resultTable.innerHTML = head + `<tbody>${body}</tbody>`;
}

/** Draws a responsive profile without stretching: canvas internal pixels match CSS box */
function drawProfile(canvasId, stations, elevs, units) {
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');

  // Match canvas internal size to its rendered (CSS) size for crisp, non‑stretched drawing
  fitCanvasToCssSize(c, ctx);
  const rect = c.getBoundingClientRect();
  const W = rect.width;
  const H = rect.height;

  ctx.clearRect(0,0,W,H);
  if (!stations || stations.length < 2) return;

  // Layout padding (CSS pixels)
  const pad = { l: 48, r: 12, t: 12, b: 32 };
  const w = Math.max(10, W - pad.l - pad.r);
  const h = Math.max(10, H - pad.t - pad.b);

  // Extents
  const minX = 0, maxX = Math.max(1, stations[stations.length-1]);
  const minY = Math.min(...elevs), maxY = Math.max(...elevs);
  const yPad = (maxY - minY) * 0.08 || 1;
  const y0 = minY - yPad, y1 = maxY + yPad;

  const xToPx = x => pad.l + ((x - minX) / (maxX - minX)) * w;
  const yToPx = y => pad.t + h - ((y - y0) / (y1 - y0)) * h;

  // Axes
  ctx.strokeStyle = '#888'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t+h); ctx.lineTo(pad.l+w, pad.t+h); ctx.stroke(); // x axis
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t);   ctx.lineTo(pad.l, pad.t+h); ctx.stroke();   // y axis

  // Grid + ticks
  ctx.fillStyle = '#444';
  ctx.font = '12px system-ui, -apple-system, Segoe UI, Roboto, Arial';
  const xTicks = 5, yTicks = 5;
  for (let i=0;i<=xTicks;i++){
    const x = minX + (i/xTicks)*(maxX-minX), px = xToPx(x);
    ctx.strokeStyle = '#eee'; ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, pad.t+h); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.textAlign = 'center';
    ctx.fillText(x.toFixed(0), px, pad.t+h+14);
  }
  for (let i=0;i<=yTicks;i++){
    const y = y0 + (i/yTicks)*(y1-y0), py = yToPx(y);
    ctx.strokeStyle = '#eee'; ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(pad.l+w, py); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.textAlign = 'right';
    ctx.fillText(y.toFixed(0), pad.l-6, py+4);
  }

  // Labels
  ctx.fillStyle = '#222';
  ctx.textAlign = 'center';
  ctx.fillText(`Station (${units})`, pad.l + w/2, H - 6);
  ctx.save();
  ctx.translate(12, pad.t + h/2);
  ctx.rotate(-Math.PI/2);
  ctx.fillText(`Elevation (${units})`, 0, 0);
  ctx.restore();

  // Profile line
  ctx.strokeStyle = '#1a73e8'; ctx.lineWidth = 2;
  ctx.beginPath();
  stations.forEach((s, i) => {
    const x = xToPx(s), y = yToPx(elevs[i]);
    if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  });
  ctx.stroke();
}

/** Resize canvas internal pixels to match its CSS size (and devicePixelRatio) */
function fitCanvasToCssSize(canvas, ctx) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  canvas.width  = Math.max(1, Math.floor(rect.width  * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.scale(dpr, dpr);
}

// Redraw on resize to keep things sharp and non‑stretched
window.addEventListener('resize', () => {
  if (!latest.stations_m.length) return;
  renderAll(document.getElementById('units').value);
});

function renderSourcesAndRes(catalog) {
  const counts = new Map();
  for (const item of catalog) {
    const label = classifyRes(item.lowps);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const parts = [];
  for (const [label, n] of Array.from(counts.entries()).sort((a,b)=>a[0].localeCompare(b[0]))) {
    parts.push(`${label} (${n})`);
  }
  resBreakdown.innerHTML = parts.length
    ? `<div><strong>Resolutions in use:</strong> ${parts.join(', ')}</div>`
    : `<div><strong>Resolutions in use:</strong> derived from 3DEP best‑available mosaics</div>`;

  sourceWrap.innerHTML = catalog.length ? catalog.map(it => {
    const res = it.lowps != null ? `${roundRes(it.lowps)} m` : (it.resy != null ? `${roundRes(it.resy)} m` : '');
    const urlLink = it.url ? `<a href="${it.url}" target="_blank" rel="noopener">download</a>` : '';
    const metaLink = it.meta ? `<a href="${it.meta}" target="_blank" rel="noopener">metadata</a>` : '';
    const links = [urlLink, metaLink].filter(Boolean).join(' · ');
    return `
      <div class="srcItem">
        <div><strong>${escapeHtml(it.name)}</strong>${it.product ? ` <small>(${escapeHtml(it.product)})</small>`:''}</div>
        <div><small>Resolution: ${res || 'n/a'} · Source: ${escapeHtml(it.source || '')}</small></div>
        ${links ? `<div><small>${links}</small></div>` : ''}
      </div>`;
  }).join('') : `<div class="srcItem"><small>No source items reported.</small></div>`;
}

// ---------- CSV ----------
function downloadCSV() {
  const rows = latest.samples || [];
  if (!rows.length) return;
  const head = ['station','elevation','lat','lon']; // keeping CSV schema unchanged
  const csv = [head.join(',')].concat(
    rows.map(r => [r.station, r.elevation, r.lat, r.lon].join(','))
  ).join('\n');

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'profile.csv';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------- Helpers ----------
function setStatus(msg, cls='') {
  statusEl.className = cls;
  statusEl.textContent = msg || '';
}
function setDownloading(on) {
  if (on) {
    btnGet.disabled = true;
    btnGet.textContent = 'Downloading…';
    btnCancel.disabled = false;
  } else {
    btnGet.disabled = false;
    btnGet.textContent = 'Get profile';
    btnCancel.disabled = true;
  }
}
function clearResults(reason) {
  latest = { stations_m:[], elevs_m:[], points:[], samples:[], catalog:[] };
  resultTable.innerHTML = '';
  resBreakdown.innerHTML = '';
  sourceWrap.innerHTML = '';
  const ctx = profileCanvas.getContext('2d');
  fitCanvasToCssSize(profileCanvas, ctx);
  ctx.clearRect(0,0,profileCanvas.getBoundingClientRect().width, profileCanvas.getBoundingClientRect().height);
  btnCsv.disabled = true;
  updateImportUi();
  setDownloading(false);
  if (reason) setStatus(reason);
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function mToFt(m) { return m * 3.280839895013123; }
function roundRes(v) { if (!isFinite(v)) return ''; return Math.round((v + Number.EPSILON) * 100) / 100; }
function classifyRes(v) {
  if (!isFinite(v)) return 'unknown';
  if (v < 1.5) return '1 m';
  if (v < 5) return '~3 m';
  if (v < 20) return '10 m';
  if (v < 45) return '30 m';
  return `~${roundRes(v)} m`;
}
function escapeHtml(s) { return (s||'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

// Re-render in new units
function reRenderInNewUnits() {
  if (!latest.stations_m.length) return;
  renderAll(document.getElementById('units').value);
}

// ---------- Embedded import controls ----------
function updateImportUi(){
  if (!btnImport) return;
  btnImport.disabled = !(latest.samples && latest.samples.length);
}
function onImportToParent(){
  if (!EMBEDDED) return; // nothing to do in standalone view
  const rows = latest.samples || [];
  if (!rows.length) return;
  const payload = {
    type: 'usgs-import',
    // Only station/elevation are imported into main.html
    samples: rows.map(r => ({ station: r.station, elevation: r.elevation })),
    // Report the current display units so the parent can convert if needed
    units: document.getElementById('units').value === 'feet' ? 'feet' : 'meters'
  };
  try { window.parent.postMessage(payload, '*'); } catch {}
  try { window.parent.postMessage({ type:'usgs-close-request' }, '*'); } catch {}
}
function onCloseRequested(){
  if (EMBEDDED) {
    try { window.parent.postMessage({ type:'usgs-close-request' }, '*'); } catch {}
  } else {
    // best-effort close in standalone (may be blocked)
    try { window.close(); } catch {}
  }
}
