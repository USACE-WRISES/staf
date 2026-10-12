"""Captures of the Shiny STAF app and the static app, for the static app to match.

Runs headless Edge against a running app, opens each tool by its link (``?tool=easi|sfari|deep``)
at a desktop size, waits until it is idle, and writes to ``parity/<app>/``:

- ``<tool>.png``: a screenshot of the first screen;
- ``<tool>.html``: the header and the tool's section as rendered, with the map's inside left out
  (the maps are compared by the screenshots), one tag per line so a diff points at what changed;
- ``dialogs.json``: each tool's About and Help dialog (title and body), and ``<tool>-about.png``,
  ``<tool>-help.png``: screenshots with each open;
- ``styles.json``: the page's stylesheets in order, with their media.

``--view LAT,LON,ZOOM --name NAME`` moves each tool's map there first (the stream lines draw from zoom
14), waits until the streams have settled, and writes ``<tool>-<NAME>.png`` and ``.html`` only;
``--coverage`` also turns on StreamCat coverage in the Layers menu first; ``--click LAT,LON`` then
clicks the map there (a pick) and waits until the pick has settled; ``--delineate`` then presses
Delineate and waits for the Basin step (or an error); ``--assess`` then presses the Basin's next button
(Run screening, Continue) and waits until the Assessment worksheet has settled.

``--actions FILE`` then runs a script of user actions on the page (``parity/actions/*.js``: an async
function of the tool's key that clicks and types as a user would) and waits until the worksheet settles.

``--perf`` also writes ``<stem>.perf.json``: the static app's step timings (its ``staf:`` performance
measures) and every request the page made, with when and how many bytes (``parity/perf.py`` reads it).
``--throttle LATENCY_MS,MBPS`` slows the network to that, as a data host far away would be. The browser
profile is new each run, so every read is cold; ``--visits 2`` runs each tool's steps again in the same
profile (``<stem>-visit2``), as a user's next visit would, with what the first read kept in the browser.

``--app shiny`` reads the Shiny STAF app (``shiny run app.py --port 8040`` in apps/staf, or
.claude/launch.json "staf") and also writes its dialogs to ``src/app/content/dialogs.json``, which
the static app shows (About and Help are written in each tool's app.py). ``--app static`` reads the
static app's dev server (``npm run dev`` in apps/staf-web, port 5180). ``parity/compare.py``
compares the two.

    python apps/staf-web/parity/capture.py --app shiny|static [--url ...] [--width 1440 --height 900]
        [--view 43.6858,-72.2367,16 --name mink16]

The captures are gitignored (parity/shiny/, parity/static/); the dialogs are committed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9341
HERE = Path(__file__).resolve().parent
CONTENT = HERE.parent / "src" / "app" / "content" / "dialogs.json"
TOOLS = ("easi", "sfari", "deep")
URLS = {"shiny": "http://localhost:8040", "static": "http://localhost:5180"}

IDLE_JS = """(() => {
  const html = document.documentElement;
  if (html.classList.contains('shiny-busy')) return false;
  const tool = html.getAttribute('data-staf-tool');
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  if (!sec || !sec.querySelector('.easi-leftpane *')) return false;
  if (!sec.querySelector('.leaflet-container')) return false;
  const imgs = [...sec.querySelectorAll('.leaflet-tile-container img')];
  return imgs.length > 0 && imgs.every((i) => i.complete);
})()"""

CAPTURE_JS = """(() => {
  const tool = document.documentElement.getAttribute('data-staf-tool');
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]').cloneNode(true);
  sec.querySelectorAll('.easi-map-wrap').forEach((m) => { m.innerHTML = '<!-- the map -->'; });
  const header = document.querySelector('.staf-header').outerHTML;
  const styles = [...document.querySelectorAll('link[rel=stylesheet]')].map((l) => ({
    href: l.getAttribute('href'), media: l.media || '', tool: l.getAttribute('data-staf-css') || '' }));
  const wrap = document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .leaflet-top.leaflet-right');
  return JSON.stringify({ header, section: sec.outerHTML, styles, title: document.title,
                          corner: wrap ? wrap.outerHTML : '',
                          modal: (document.querySelector('.modal.show') || {}).outerHTML || '' });
})()"""

# the app's timings (performance measures named staf:<step>) and every request the page made, as
# [name or url, start ms, end ms, bytes]
PERF_JS = """JSON.stringify({
  measures: performance.getEntriesByType('measure').filter((m) => m.name.startsWith('staf:'))
    .map((m) => [m.name.slice(5), Math.round(m.startTime), Math.round(m.startTime + m.duration)]),
  resources: performance.getEntriesByType('resource')
    .map((r) => [r.name, Math.round(r.startTime), Math.round(r.responseEnd), r.encodedBodySize]),
  page: ((n) => n ? { domContentLoaded: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd) } : null)(
    performance.getEntriesByType('navigation')[0]),
  data: window.stafStats ? window.stafStats() : null,
})"""

# opens one of the shown tool's dialogs and returns {title, body} once it shows
OPEN_JS = """(async (which) => {
  const tool = document.documentElement.getAttribute('data-staf-tool');
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  document.getElementById(tool + '-nav_' + which).click();
  let m = null;
  for (let i = 0; i < 60; i++) {
    await wait(100);
    m = document.querySelector('#shiny-modal.show');
    if (m) break;
  }
  if (!m) return null;
  await wait(600);
  return JSON.stringify({ title: m.querySelector('.modal-title').textContent,
                          body: m.querySelector('.modal-body').innerHTML });
})"""

CLOSE_JS = """(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const m = document.querySelector('#shiny-modal.show');
  if (m) m.querySelector('[data-bs-dismiss="modal"]').click();
  for (let i = 0; i < 60; i++) {
    await wait(100);
    if (!document.querySelector('#shiny-modal.show') && !document.querySelector('.modal-backdrop')) break;
  }
  await wait(300);
  return true;
})()"""


# the shown tool's Leaflet map: neither page names it, so catch it firing an event (as DEEP's
# coverage.js does), then set its view
VIEW_JS = """((lat, lon, zoom) => {
  const tool = document.documentElement.getAttribute('data-staf-tool');
  const cont = document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .leaflet-container');
  const L = window.L;
  let map = null;
  const fire = L.Evented.prototype.fire;
  L.Evented.prototype.fire = function () { if (!map && this instanceof L.Map) map = this; return fire.apply(this, arguments); };
  const r = cont.getBoundingClientRect();
  cont.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
  L.Evented.prototype.fire = fire;
  if (!map) return false;
  map.setView([lat, lon], zoom, { animate: false });
  return true;
})"""

# the stream lines drawn, none loading, Shiny idle: the count of drawn paths, or -1 while busy
STREAMS_JS = """(() => {
  const html = document.documentElement;
  const tool = html.getAttribute('data-staf-tool');
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  if (html.classList.contains('shiny-busy') || sec.querySelector('.easi-flow-loading')) return -1;
  const imgs = [...sec.querySelectorAll('.leaflet-tile-container img')];
  if (!imgs.every((i) => i.complete)) return -1;
  return sec.querySelectorAll('.leaflet-staf-streams-pane path').length;
})()"""


# a click on the shown tool's map at a point (Leaflet's click event, as a mouse click fires it)
CLICK_JS = """((lat, lon) => {
  const tool = document.documentElement.getAttribute('data-staf-tool');
  const cont = document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .leaflet-container');
  const L = window.L;
  let map = null;
  const fire = L.Evented.prototype.fire;
  L.Evented.prototype.fire = function () { if (!map && this instanceof L.Map) map = this; return fire.apply(this, arguments); };
  const r = cont.getBoundingClientRect();
  cont.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }));
  L.Evented.prototype.fire = fire;
  if (!map) return false;
  const latlng = L.latLng(lat, lon);
  const layerPoint = map.latLngToLayerPoint(latlng);
  const containerPoint = map.latLngToContainerPoint(latlng);
  map.fire('click', { latlng, layerPoint, containerPoint, originalEvent: new MouseEvent('click') });
  return true;
})"""

# a pick has settled: no lookup spinner, Shiny idle, the snap status written; its text, or null
PICK_JS = """(() => {
  const html = document.documentElement;
  const tool = html.getAttribute('data-staf-tool');
  if (html.classList.contains('shiny-busy')) return null;
  if (document.querySelector('.easi-lookup-spinner')) return null;
  const snap = document.getElementById(tool + '-snap_status');
  return snap ? snap.textContent.replace(/\\s+/g, ' ').trim() : null;
})()"""


# Delineate pressed: the Basin step shown and idle, or an error said; the pane's head, or null
BASIN_JS = """(() => {
  const html = document.documentElement;
  const tool = html.getAttribute('data-staf-tool');
  if (html.classList.contains('shiny-busy')) return null;
  const err = document.querySelector('.shiny-notification-error');
  if (err) return 'error: ' + err.textContent.trim();
  const head = document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .easi-leftpane .easi-pane-head');
  const text = head ? head.textContent.trim() : '';
  return /Basin/.test(text) ? text : null;
})()"""


# the worksheet shown and settled: not busy, no progress toast, the worksheet output filled; its
# text, or null
ASSESS_JS = """(() => {
  const html = document.documentElement;
  const tool = html.getAttribute('data-staf-tool');
  if (html.classList.contains('shiny-busy')) return null;
  if (document.getElementById('shiny-notification-stage')) return null;
  const err = document.querySelector('.shiny-notification-error');
  if (err) return 'error: ' + err.textContent.trim();
  const ws = document.getElementById(tool + '-worksheet');
  const text = ws ? ws.innerText.trim() : '';
  return text ? text.length + ':' + text.slice(0, 120) : null;
})()"""
NEXT = {"easi": "easi-run_screening", "sfari": "sfari-to_review", "deep": "deep-to_measure"}


def pretty(html: str) -> str:
    """One tag per line, so a diff points at the element that changed."""
    return html.replace("><", ">\n<")


async def main(app: str, url: str, width: int, height: int, view=None, name: str = "",
               coverage: bool = False, click=None, delineate: bool = False, assess: bool = False,
               tools=TOOLS, actions: str = "", perf: bool = False, throttle=None, query: str = "", visits: int = 1) -> None:
    out = HERE / app
    out.mkdir(parents=True, exist_ok=True)
    prof = Path(tempfile.mkdtemp(prefix="staf-capture-"))
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             f"--window-size={width},{height}", "--no-first-run", "--no-default-browser-check",
                             # the HTTP/2 preview's localhost certificate is self-signed
                             "--ignore-certificate-errors",
                             "about:blank"])
    try:
        pages = []
        for _ in range(100):
            try:
                pages = [t for t in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read())
                         if t.get("type") == "page"]
                if pages:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=1 << 27) as ws:
            n = 0

            async def call(method, **params):
                nonlocal n
                n += 1
                mid = n
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})

            async def js(expr):
                r = await call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
                return (r.get("result") or {}).get("value")

            await call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                       mobile=False)
            await call("Page.enable")
            if perf:
                # every request kept (the browser keeps 250 by default)
                await call("Page.addScriptToEvaluateOnNewDocument", source="performance.setResourceTimingBufferSize(1000000)")
            if throttle:
                await call("Network.enable")
                await call("Network.emulateNetworkConditions", offline=False, latency=throttle[0],
                           downloadThroughput=throttle[1] * 125000, uploadThroughput=throttle[1] * 125000)
            styles, dialogs = None, {}
            # a later visit runs in the same browser profile: what the first read is in the browser's caches
            for visit, tool in [(k, tool) for k in range(1, visits + 1) for tool in tools]:
                await call("Page.navigate", url=f"{url}/?tool={tool}" + (f"&{query}" if query else ""))
                end = time.monotonic() + 90
                while time.monotonic() < end:
                    await asyncio.sleep(0.5)
                    if await js(IDLE_JS):
                        break
                await asyncio.sleep(3.0)                # the legend dock and the last tiles
                stem = (f"{tool}-{name}" if view else tool) + (f"-visit{visit}" if visit > 1 else "")
                if coverage:
                    await js("""(() => { const tool = document.documentElement.getAttribute('data-staf-tool');
                      const box = document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .staf-coverage-toggle');
                      if (box && !box.checked) box.click(); return !!box; })()""")
                    await asyncio.sleep(2.0)
                if view:
                    if not await js(f"{VIEW_JS}({view[0]}, {view[1]}, {view[2]})"):
                        raise RuntimeError(f"{tool}: could not find the map")
                    # settled: the same number of drawn paths, not loading, for 3 polls a second apart
                    last, same, end = None, 0, time.monotonic() + 120
                    while time.monotonic() < end and same < 3:
                        await asyncio.sleep(1.0)
                        count = await js(STREAMS_JS)
                        same = same + 1 if count == last and count >= 0 else 0
                        last = count
                    await asyncio.sleep(1.0)
                if click:
                    if not await js(f"{CLICK_JS}({click[0]}, {click[1]})"):
                        raise RuntimeError(f"{tool}: could not click the map")
                    last, same, end = None, 0, time.monotonic() + 120
                    await asyncio.sleep(1.0)
                    while time.monotonic() < end and same < 3:
                        await asyncio.sleep(1.0)
                        text = await js(PICK_JS)
                        same = same + 1 if text is not None and text == last else 0
                        last = text
                    print(f"{tool}: pick says {last!r}")
                    await asyncio.sleep(1.0)
                if delineate:
                    await js(f"document.getElementById('{tool}-delineate').click()")
                    head, end = None, time.monotonic() + 600
                    while time.monotonic() < end and head is None:
                        await asyncio.sleep(2.0)
                        head = await js(BASIN_JS)
                    print(f"{tool}: delineate says {head!r}")
                    await asyncio.sleep(4.0)                # the watershed drawn and fitted, tiles in
                if assess:
                    await js(f"document.getElementById('{NEXT[tool]}').click()")
                    last, same, end = None, 0, time.monotonic() + 900
                    while time.monotonic() < end and same < 3:
                        await asyncio.sleep(2.0)
                        text = await js(ASSESS_JS)
                        same = same + 1 if text is not None and text == last else 0
                        last = text
                    print(f"{tool}: assessment says {(last or '')[:100]!r}")
                    await asyncio.sleep(3.0)
                if actions:
                    # what a user does on the page (an async function over the page's elements), then
                    # the worksheet settled again
                    said = await js(f'({actions})({json.dumps(tool)})')
                    # what the script returns is kept beside the capture (<stem>.actions.txt), so a
                    # script can collect what it saw along the way for compare.py
                    (out / f"{stem}.actions.txt").write_text(str(said), encoding="utf-8", newline="\n")
                    print(f"{tool}: actions say {str(said)[:200]!r}")
                    last, same, end = None, 0, time.monotonic() + 300
                    while time.monotonic() < end and same < 3:
                        await asyncio.sleep(1.0)
                        text = await js(ASSESS_JS)
                        same = same + 1 if text is not None and text == last else 0
                        last = text
                    await asyncio.sleep(2.0)
                if perf:
                    (out / f"{stem}.perf.json").write_text(await js(PERF_JS), encoding="utf-8", newline="\n")
                got = json.loads(await js(CAPTURE_JS))
                styles = got["styles"]
                (out / f"{stem}.html").write_text(
                    f"<!-- {got['title']} : {url}/?tool={tool} at {width}x{height} -->\n"
                    + pretty(got["header"]) + "\n\n" + pretty(got["section"]) + "\n\n"
                    + "<!-- the map's top right corner -->\n" + pretty(got["corner"]) + "\n"
                    + ("\n<!-- the dialog shown -->\n" + pretty(got["modal"]) + "\n" if got.get("modal") else ""),
                    encoding="utf-8", newline="\n")
                shot = await call("Page.captureScreenshot", format="png")
                (out / f"{stem}.png").write_bytes(base64.b64decode(shot["data"]))
                if view:
                    print(f"{tool}: {got['title']} at {view}, {last} stream paths")
                    continue
                dialogs[tool] = {}
                for which in ("about", "help"):
                    text = await js(f"{OPEN_JS}({json.dumps(which)})")
                    dialogs[tool][which] = json.loads(text) if text else None
                    if text:
                        shot = await call("Page.captureScreenshot", format="png")
                        (out / f"{tool}-{which}.png").write_bytes(base64.b64decode(shot["data"]))
                    await js(CLOSE_JS)
                print(f"{tool}: {got['title']}")
            if view:
                return
            (out / "styles.json").write_text(json.dumps(styles, indent=1), encoding="utf-8", newline="\n")
            text = json.dumps(dialogs, indent=1, ensure_ascii=False) + "\n"
            (out / "dialogs.json").write_text(text, encoding="utf-8", newline="\n")
            if app == "shiny":
                CONTENT.parent.mkdir(parents=True, exist_ok=True)
                CONTENT.write_text(text, encoding="utf-8", newline="\n")
    finally:
        proc.terminate()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", choices=sorted(URLS), required=True)
    ap.add_argument("--url", help="default: the app's local address")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--view", help="LAT,LON,ZOOM to move each map to before the capture")
    ap.add_argument("--name", default="view", help="the captures' suffix with --view")
    ap.add_argument("--coverage", action="store_true", help="turn on StreamCat coverage first (with --view)")
    ap.add_argument("--click", help="LAT,LON to click on the map once the view settles (with --view)")
    ap.add_argument("--delineate", action="store_true", help="press Delineate after the pick (with --click)")
    ap.add_argument("--assess", action="store_true", help="go on to the Assessment step (with --delineate)")
    ap.add_argument("--tools", nargs="*", choices=TOOLS, default=list(TOOLS), help="only these tools (with --view)")
    ap.add_argument("--actions", help="a JS file: an async function (tool) => ... run on the page after --assess")
    ap.add_argument("--perf", action="store_true",
                    help="also write <stem>.perf.json: the page's staf: timings and every request it made")
    ap.add_argument("--throttle", help="LATENCY_MS,MBPS: the network made that slow (a data host far away)")
    ap.add_argument("--query", default="", help="more of the address's query, e.g. data=http://localhost:5181/data/")
    ap.add_argument("--visits", type=int, default=1,
                    help="run each tool's steps this many times in one browser profile (later runs: <stem>-visit2, ...)")
    a = ap.parse_args()
    v = tuple(float(x) for x in a.view.split(",")) if a.view else None
    c = tuple(float(x) for x in a.click.split(",")) if a.click else None
    asyncio.run(main(a.app, (a.url or URLS[a.app]).rstrip("/"), a.width, a.height, v, a.name, a.coverage, c,
                     a.delineate, a.assess, tuple(a.tools),
                     Path(a.actions).read_text(encoding="utf-8").strip() if a.actions else "", a.perf,
                     tuple(float(x) for x in a.throttle.split(",")) if a.throttle else None, a.query, a.visits))
