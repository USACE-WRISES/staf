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
clicks the map there (a pick) and waits until the pick has settled.

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
                          corner: wrap ? wrap.outerHTML : '' });
})()"""

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


def pretty(html: str) -> str:
    """One tag per line, so a diff points at the element that changed."""
    return html.replace("><", ">\n<")


async def main(app: str, url: str, width: int, height: int, view=None, name: str = "",
               coverage: bool = False, click=None) -> None:
    out = HERE / app
    out.mkdir(parents=True, exist_ok=True)
    prof = Path(tempfile.mkdtemp(prefix="staf-capture-"))
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             f"--window-size={width},{height}", "--no-first-run", "--no-default-browser-check",
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
            styles, dialogs = None, {}
            for tool in TOOLS:
                await call("Page.navigate", url=f"{url}/?tool={tool}")
                end = time.monotonic() + 90
                while time.monotonic() < end:
                    await asyncio.sleep(0.5)
                    if await js(IDLE_JS):
                        break
                await asyncio.sleep(3.0)                # the legend dock and the last tiles
                stem = f"{tool}-{name}" if view else tool
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
                got = json.loads(await js(CAPTURE_JS))
                styles = got["styles"]
                (out / f"{stem}.html").write_text(
                    f"<!-- {got['title']} : {url}/?tool={tool} at {width}x{height} -->\n"
                    + pretty(got["header"]) + "\n\n" + pretty(got["section"]) + "\n\n"
                    + "<!-- the map's top right corner -->\n" + pretty(got["corner"]) + "\n",
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
    a = ap.parse_args()
    v = tuple(float(x) for x in a.view.split(",")) if a.view else None
    c = tuple(float(x) for x in a.click.split(",")) if a.click else None
    asyncio.run(main(a.app, (a.url or URLS[a.app]).rstrip("/"), a.width, a.height, v, a.name, a.coverage, c))
