"""EASI's report cross-section image drawn by the static app beside matplotlib's, pixel by pixel.

Runs headless Edge against the static app's dev server (``npm run dev``, port 5180), draws the renders of
``tests/fixtures/xsimage_ref.json`` that carry matplotlib's PNG with ``src/tools/easi/xsimage.ts``, and writes
to ``parity/static/``: ``xsimage-<n>.png`` (matplotlib's above the static app's, then their difference,
darker where they differ) and prints how much each differs.

    .venv\\Scripts\\python apps/staf-web/parity/xsimage_compare.py [--url http://localhost:5180]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import websockets
from PIL import Image

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9343
HERE = Path(__file__).resolve().parent
REF = HERE.parent / "tests" / "fixtures" / "xsimage_ref.json"

DRAW_JS = """(async () => {
  const m = await import('/src/tools/easi/xsimage.ts');
  const ref = await (await fetch('/tests/fixtures/xsimage_ref.json')).json();
  const out = [];
  for (const r of ref.renders) {
    if (!r.png) continue;
    out.push(await m.xsImage({ stations: r.stations, elevs: r.elevs, thalweg: r.thalweg, bankfull_stage: r.bankfull_stage,
      floodplain_stage: r.floodplain_stage, unit: r.unit, source: r.source }));
  }
  return JSON.stringify(out);
})()"""


def decode(url_or_b64: str) -> np.ndarray:
    data = base64.b64decode(url_or_b64.split(",", 1)[-1])
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(int)


async def main(url: str) -> int:
    ref = json.loads(REF.read_text(encoding="utf-8"))
    pngs = [r["png"] for r in ref["renders"] if r.get("png")]
    prof = Path(tempfile.mkdtemp(prefix="staf-xsimage-"))
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             "--window-size=1200,800", "--no-first-run", "--no-default-browser-check", "about:blank"])
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
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result", {})
            await call("Page.enable")
            await call("Page.navigate", url=f"{url}/?tool=easi")
            await asyncio.sleep(4.0)
            r = await call("Runtime.evaluate", expression=DRAW_JS, returnByValue=True, awaitPromise=True)
            got = json.loads((r.get("result") or {}).get("value") or "[]")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(prof, ignore_errors=True)
    out = HERE / "static"
    out.mkdir(parents=True, exist_ok=True)
    for i, (mine, theirs) in enumerate(zip(got, pngs)):
        a, b = decode(theirs), decode(mine)
        d = np.abs(a - b).sum(axis=2)
        print(f"{i}: mean difference {d.mean():.2f}, pixels off by more than 96: {(d > 96).sum()} of {d.size}")
        diff = np.clip(255 - d, 0, 255)[:, :, None].repeat(3, axis=2)
        Image.fromarray(np.concatenate([a, b, diff]).astype(np.uint8)).save(out / f"xsimage-{i}.png")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:5180")
    sys.exit(asyncio.run(main(ap.parse_args().url.rstrip("/"))))
