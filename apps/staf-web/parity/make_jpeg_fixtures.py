"""Goldens for the static app's JPEG decoder (src/files/pdf/jpeg.ts, tests/jpeg.test.ts): JPEGs of the kinds
Pillow decodes and reportlab names a photo's XObject after, each with what ``Image.open(...).tobytes()``
gives (its MD5, mode and size; reportlab digests those bytes and b"auto").

- **Pillow's encoder** (libjpeg-turbo): 4:4:4, 4:2:2 and 4:2:0, baseline and progressive, optimized
  tables, restart markers, 16-bit quantization tables (an extended-sequential frame), gray, CMYK (Adobe)
  and RGB kept as RGB (Adobe transform 0), sizes from 1 x 1 up; photo, gradient, noise and sharp edges.
- **A small baseline encoder here**, for what Pillow never writes: other sampling factors (4:4:0, 4:1:1,
  3:1, 4:2, chroma finer than luma, gray with factors), one scan per component, restart intervals there,
  the colour space told by the component ids or an Adobe marker alone, YCCK; and data that ends early
  or is damaged (libjpeg's zero fill and its fake zero for a bad code).
- **Edge's canvas encoder**: the apps keep a photo as Chrome makes it (field-review.js and measure.js
  downscale: the photo drawn at most 1024 px, canvas.toDataURL("image/jpeg", 0.82)), run in headless Edge.

Writes ``tests/fixtures/jpeg/<name>.jpg`` and ``index.json``.

    .venv\\Scripts\\python apps/staf-web/parity/make_jpeg_fixtures.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE.parent / "tests" / "fixtures" / "jpeg"
PHOTO = REPO / "apps" / "easi" / "docs" / "EASI_Documentation" / "figures" / "photo_minkbrook.jpg"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7,
          14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46,
          53, 60, 61, 54, 47, 55, 62, 63]


# --- content -----------------------------------------------------------------------------------------

def gradient(w: int, h: int) -> Image.Image:
    x = np.linspace(0, 255, max(w, 1))[None, :].repeat(h, 0)
    y = np.linspace(0, 255, max(h, 1))[:, None].repeat(w, 1)
    return Image.fromarray(np.stack([x, y, (x + y) / 2], -1).astype(np.uint8), "RGB")


def noise(w: int, h: int, seed: int = 7) -> Image.Image:
    return Image.fromarray(np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8), "RGB")


def edges(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), (250, 250, 245))
    d = ImageDraw.Draw(im)
    for i in range(0, max(w, h), 6):
        d.line([(i, 0), (0, i)], fill=(i * 37 % 256, 20, 200 - i % 200), width=1)
    d.rectangle([w // 4, h // 4, 3 * w // 4, 3 * h // 4], outline=(0, 0, 0), fill=(255, 0, 0))
    if w > 6 and h > 6:
        d.ellipse([w // 3, h // 5, w - 2, h - 2], fill=(0, 120, 255))
    else:
        im.putpixel((0, 0), (0, 0, 0))
        im.putpixel((w - 1, h - 1), (255, 255, 0))
    return im


def photo(w: int, h: int) -> Image.Image:
    return Image.open(PHOTO).convert("RGB").resize((w, h), Image.LANCZOS)


CONTENT = {"gradient": gradient, "noise": noise, "edges": edges, "photo": photo}


# --- a small baseline encoder ------------------------------------------------------------------------

def segments(jpeg: bytes) -> list[tuple[int, bytes]]:
    """A JPEG's marker segments up to its first scan: [(marker, payload)]."""
    out, p = [], 2
    while p < len(jpeg):
        m = jpeg[p + 1]
        n = struct.unpack(">H", jpeg[p + 2:p + 4])[0]
        out.append((m, jpeg[p + 4:p + 2 + n]))
        if m == 0xDA:
            break
        p += 2 + n
    return out


def standard_tables() -> tuple[list[list[int]], list[tuple[int, int, list[int], list[int]]]]:
    """libjpeg's quantization tables at quality 80 and its standard Huffman tables (Annex K), taken from a
    file Pillow writes without optimized tables: [qt0, qt1] (zigzag order), [(class, id, counts, symbols)]."""
    buf = io.BytesIO()
    gradient(16, 16).save(buf, "JPEG", quality=80, subsampling=2)
    qts, hts = [], []
    for m, body in segments(buf.getvalue()):
        if m == 0xDB:
            p = 0
            while p < len(body):
                qts.append(list(body[p + 1:p + 65]))
                p += 65
        elif m == 0xC4:
            p = 0
            while p < len(body):
                counts = list(body[p + 1:p + 17])
                k = sum(counts)
                hts.append((body[p] >> 4, body[p] & 15, counts, list(body[p + 17:p + 17 + k])))
                p += 17 + k
    return qts, hts


def codes(counts: list[int], symbols: list[int]) -> dict[int, tuple[int, int]]:
    out, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            out[symbols[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return out


class Bits:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                if self.acc == 0xFF:
                    self.out.append(0)
                self.acc, self.n = 0, 0

    def flush(self) -> None:
        if self.n:
            self.put((1 << (8 - self.n)) - 1, 8 - self.n)


_C = np.array([[(np.sqrt(0.5) if u == 0 else 1.0) / 2 * np.cos((2 * x + 1) * u * np.pi / 16) for x in range(8)] for u in range(8)])


def encode(planes: list[np.ndarray], factors: list[tuple[int, int]], ids: list[int], *, jfif: bool = True,
           adobe: int | None = None, restart: int = 0, interleaved: bool = True, tables: list[int] | None = None) -> bytes:
    """A baseline JPEG of the given component planes (each at the image's full size; a component of factors
    (h, v) is box-averaged to its own size, as an encoder downsamples)."""
    qts, hts = standard_tables()
    huff = {(c, i): codes(counts, syms) for c, i, counts, syms in hts}
    H, W = planes[0].shape
    hmax = max(h for h, _ in factors)
    vmax = max(v for _, v in factors)
    tables = tables or [0] + [1] * (len(planes) - 1)
    mx, my = -(-W // (8 * hmax)), -(-H // (8 * vmax))
    comps = []
    for plane, (h, v) in zip(planes, factors):
        fx, fy = hmax // h, vmax // v
        dw, dh = -(-W * h // hmax), -(-H * v // vmax)
        full = np.pad(plane.astype(np.float64), ((0, dh * fy - H), (0, dw * fx - W)), mode="edge")
        small = full.reshape(dh, fy, dw, fx).mean(axis=(1, 3))
        bw, bh = (mx * h, my * v) if interleaved else (-(-dw // 8), -(-dh // 8))
        grid = np.pad(small, ((0, bh * 8 - dh), (0, bw * 8 - dw)), mode="edge") - 128
        comps.append((grid, h, v, dw, dh))
    head = bytearray(b"\xff\xd8")

    def seg(marker: int, body: bytes) -> None:
        head.extend(struct.pack(">BBH", 0xFF, marker, len(body) + 2) + body)

    if jfif:
        seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if adobe is not None:
        seg(0xEE, b"Adobe" + struct.pack(">HHHB", 100, 0, 0, adobe))
    seg(0xDB, bytes([0]) + bytes(qts[0]) + bytes([1]) + bytes(qts[1]))
    seg(0xC0, struct.pack(">BHHB", 8, H, W, len(planes)) + b"".join(
        bytes([i, (h << 4) | v, t]) for i, (h, v), t in zip(ids, factors, tables)))
    for c, i, counts, syms in hts:
        seg(0xC4, bytes([(c << 4) | i] + counts + syms))
    if restart:
        seg(0xDD, struct.pack(">H", restart))
    quant = [np.array([qts[t][ZIGZAG.index(k)] for k in range(64)], dtype=np.float64).reshape(8, 8) for t in (0, 1)]

    def block(ci: int, by: int, bx: int, bits: Bits, pred: list[int]) -> None:
        grid, *_ = comps[ci]
        t = tables[ci]
        coef = np.round((_C @ grid[by * 8:by * 8 + 8, bx * 8:bx * 8 + 8] @ _C.T) / quant[t]).astype(int).flatten()
        zz = [int(coef[k]) for k in ZIGZAG]
        dc, ac = huff[(0, t)], huff[(1, t)]
        diff = zz[0] - pred[ci]
        pred[ci] = zz[0]
        s = abs(diff).bit_length()
        bits.put(*dc[s])
        if s:
            bits.put(diff if diff >= 0 else diff + (1 << s) - 1, s)
        run = 0
        for k in range(1, 64):
            v = zz[k]
            if v == 0:
                run += 1
                continue
            while run > 15:
                bits.put(*ac[0xF0])
                run -= 16
            s = abs(v).bit_length()
            bits.put(*ac[(run << 4) | s])
            bits.put(v if v >= 0 else v + (1 << s) - 1, s)
            run = 0
        if run:
            bits.put(*ac[0x00])

    def scan(members: list[int]) -> bytes:
        body = bytes([len(members)]) + b"".join(bytes([ids[c], (tables[c] << 4) | tables[c]]) for c in members) + bytes([0, 63, 0])
        out = bytearray(struct.pack(">BBH", 0xFF, 0xDA, len(body) + 2) + body)
        bits, pred = Bits(), [0] * len(planes)
        if len(members) == 1:
            grid, h, v, dw, dh = comps[members[0]]
            units = [[(members[0], by, bx)] for by in range(-(-dh // 8)) for bx in range(-(-dw // 8))]
        else:
            units = [[(c, y * comps[c][2] + j, x * comps[c][1] + i) for c in members for j in range(comps[c][2]) for i in range(comps[c][1])]
                     for y in range(my) for x in range(mx)]
        for n, unit in enumerate(units):
            if restart and n and n % restart == 0:
                bits.flush()
                out.extend(bits.out)
                out.extend(bytes([0xFF, 0xD0 + (n // restart - 1) % 8]))
                bits, pred = Bits(), [0] * len(planes)
            for ci, by, bx in unit:
                block(ci, by, bx, bits, pred)
        bits.flush()
        out.extend(bits.out)
        return bytes(out)

    scans = [scan(list(range(len(planes))))] if interleaved else [scan([c]) for c in range(len(planes))]
    return bytes(head) + b"".join(scans) + b"\xff\xd9"


def ycc(im: Image.Image) -> list[np.ndarray]:
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return [0.299 * r + 0.587 * g + 0.114 * b, -0.168736 * r - 0.331264 * g + 0.5 * b + 128,
            0.5 * r - 0.418688 * g - 0.081312 * b + 128]


def rgb_planes(im: Image.Image) -> list[np.ndarray]:
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    return [a[..., 0], a[..., 1], a[..., 2]]


def damaged(jpeg: bytes, seed: int) -> bytes:
    """Flips bits inside the entropy data (never making or breaking an FF byte)."""
    sos = jpeg.index(b"\xff\xda")
    start = sos + 2 + struct.unpack(">H", jpeg[sos + 2:sos + 4])[0]
    data = bytearray(jpeg)
    rng = np.random.default_rng(seed)
    for p in rng.integers(start + 4, len(jpeg) - 8, 12):
        if data[p] not in (0xFF, 0x00) and data[p - 1] != 0xFF:
            v = data[p] ^ (1 << int(rng.integers(0, 8)))
            if v not in (0xFF, 0x00):
                data[p] = v
    return bytes(data)


def cut(jpeg: bytes, fraction: float) -> bytes:
    """The entropy data stopped early, EOI right after (libjpeg fills the rest with zeros)."""
    sos = jpeg.index(b"\xff\xda")
    end = sos + int((len(jpeg) - sos) * fraction)
    while jpeg[end - 1] == 0xFF:
        end -= 1
    return jpeg[:end] + b"\xff\xd9"


# --- Edge's canvas encoder -----------------------------------------------------------------------------

BROWSER_JS = """(async () => {
  const out = {};
  const img = new Image();
  img.src = 'data:image/jpeg;base64,PHOTO64';
  await img.decode();
  function scaled(maxDim) {   // field-review.js / measure.js downscale
    const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
    const cw = Math.max(1, Math.round(img.width * scale)), ch = Math.max(1, Math.round(img.height * scale));
    const c = document.createElement('canvas'); c.width = cw; c.height = ch;
    c.getContext('2d').drawImage(img, 0, 0, cw, ch);
    return c.toDataURL('image/jpeg', 0.82);
  }
  out['edge-photo-1024'] = scaled(1024);
  out['edge-photo-300'] = scaled(300);
  for (const [w, h] of [[17, 13], [640, 427]]) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0, 0, w, h);
    grad.addColorStop(0, '#1f4e8c'); grad.addColorStop(0.5, '#f5e7a6'); grad.addColorStop(1, '#c0392b');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
    g.fillStyle = '#000'; g.font = (h / 6) + 'px sans-serif'; g.fillText('STAF 0.82', w / 10, h / 2);
    g.strokeStyle = '#0a0'; g.lineWidth = 3; g.beginPath(); g.arc(w / 2, h / 2, h / 3, 0, 6.3); g.stroke();
    out['edge-drawn-' + w] = c.toDataURL('image/jpeg', 0.82);
  }
  return JSON.stringify(out);
})()"""


async def _edge_jpegs() -> dict[str, bytes]:
    import websockets
    prof = Path(tempfile.mkdtemp())
    proc = subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=9336", f"--user-data-dir={prof}",
                             "--no-first-run", "--no-default-browser-check", "about:blank"])
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:9336/json/version").read()
                break
            except Exception:
                time.sleep(0.2)
        time.sleep(1.0)
        page = [t for t in json.loads(urllib.request.urlopen("http://127.0.0.1:9336/json/list").read()) if t["type"] == "page"][0]
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=1 << 27) as ws:
            expr = BROWSER_JS.replace("PHOTO64", base64.b64encode(PHOTO.read_bytes()).decode("ascii"))
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    break
        urls = json.loads(msg["result"]["result"]["value"])
        return {k: base64.b64decode(v.split(",", 1)[1]) for k, v in urls.items()}
    finally:
        proc.terminate()
        time.sleep(1)
        shutil.rmtree(prof, ignore_errors=True)


# --- the corpus ----------------------------------------------------------------------------------------

def pil(im: Image.Image, **opts) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", **opts)
    return buf.getvalue()


def corpus() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    sizes = [(1, 1), (2, 2), (3, 5), (5, 3), (8, 8), (9, 9), (15, 17), (16, 16), (17, 33), (33, 17), (63, 65)]
    for w, h in sizes:
        for sub in (0, 1, 2):
            files[f"pil-edges-{w}x{h}-s{sub}"] = pil(edges(w, h), quality=85, subsampling=sub)
    for kind in CONTENT:
        im = CONTENT[kind](161, 97)
        for sub in (0, 1, 2):
            for q in (5, 50, 82, 100):
                files[f"pil-{kind}-q{q}-s{sub}"] = pil(im, quality=q, subsampling=sub)
            files[f"pil-{kind}-s{sub}-prog"] = pil(im, quality=82, subsampling=sub, progressive=True)
            files[f"pil-{kind}-s{sub}-prog-opt"] = pil(im, quality=95, subsampling=sub, progressive=True, optimize=True)
            files[f"pil-{kind}-s{sub}-opt"] = pil(im, quality=70, subsampling=sub, optimize=True)
            files[f"pil-{kind}-s{sub}-rst"] = pil(im, quality=82, subsampling=sub, restart_marker_blocks=3)
        files[f"pil-{kind}-prog-rst"] = pil(im, quality=82, subsampling=2, progressive=True, restart_marker_rows=1)
        files[f"pil-{kind}-gray"] = pil(im.convert("L"), quality=82)
        files[f"pil-{kind}-gray-prog"] = pil(im.convert("L"), quality=82, progressive=True)
        files[f"pil-{kind}-cmyk"] = pil(im.convert("CMYK"), quality=82)
        files[f"pil-{kind}-cmyk-prog"] = pil(im.convert("CMYK"), quality=82, progressive=True)
        files[f"pil-{kind}-keeprgb"] = pil(im, quality=82, keep_rgb=True)
    big = [[min(65535, 3 * (i + 1) ** 2) for i in range(64)], [min(65535, 400 + 7 * i) for i in range(64)]]
    files["pil-photo-qt16"] = pil(photo(161, 97), qtables=big, subsampling=2)

    im = photo(77, 45)
    y, cb, cr = ycc(im)
    for name, factors, interleaved in [
        ("h1v2", [(1, 2), (1, 1), (1, 1)], True),               # 4:4:0: h1v2 fancy
        ("h4v1", [(4, 1), (1, 1), (1, 1)], True),               # 4:1:1: int 4 x 1
        ("h3v1", [(3, 1), (1, 1), (1, 1)], True),               # int 3 x 1
        ("h4v2", [(4, 2), (1, 1), (1, 1)], True),               # int 4 x 2 (ten blocks an MCU)
        ("h2v2-c2x1", [(2, 2), (2, 1), (2, 1)], True),          # chroma h1v2 fancy
        ("h2v2-c1x2", [(2, 2), (1, 2), (1, 2)], True),          # chroma h2v1 fancy, two rows a group
        ("h1v1-c2x2", [(1, 1), (2, 2), (2, 2)], True),          # luma upsampled (h2v2 fancy)
        ("h2v2-mixed", [(2, 2), (1, 1), (2, 2)], True),         # one chroma full, one quarter
        ("h4v4-noninter", [(4, 4), (1, 1), (1, 1)], False),     # int 4 x 4, one scan a component
        ("h2v2-noninter", [(2, 2), (1, 1), (1, 1)], False),
        ("h2v1-noninter", [(2, 1), (1, 1), (1, 1)], False),
    ]:
        files[f"enc-{name}"] = encode([y, cb, cr], factors, [1, 2, 3], interleaved=interleaved)
        files[f"enc-{name}-rst"] = encode([y, cb, cr], factors, [1, 2, 3], interleaved=interleaved, restart=2)
    narrow = photo(3, 40)
    files["enc-narrow-h2v2"] = encode(ycc(narrow), [(2, 2), (1, 1), (1, 1)], [1, 2, 3])       # box upsampling (width <= 4)
    files["enc-narrow-h2v1"] = encode(ycc(narrow), [(2, 1), (1, 1), (1, 1)], [1, 2, 3])
    files["enc-gray-2x2"] = encode([y], [(2, 2)], [1])
    files["enc-rgb-ids"] = encode(rgb_planes(im), [(1, 1)] * 3, [82, 71, 66], jfif=False, tables=[0, 0, 0])
    files["enc-ycc-ids"] = encode([y, cb, cr], [(2, 2), (1, 1), (1, 1)], [0, 1, 2], jfif=False)
    files["enc-adobe1"] = encode([y, cb, cr], [(2, 1), (1, 1), (1, 1)], [7, 8, 9], jfif=False, adobe=1)
    files["enc-adobe0"] = encode(rgb_planes(im), [(1, 1)] * 3, [1, 2, 3], jfif=False, adobe=0, tables=[0, 0, 0])
    k = np.asarray(im.convert("L"), dtype=np.float64) * 0.4
    files["enc-ycck"] = encode([y, cb, cr, k], [(2, 2), (1, 1), (1, 1), (2, 2)], [1, 2, 3, 4], jfif=False, adobe=2,
                               tables=[0, 1, 1, 0])
    files["enc-cmyk-noadobe"] = encode([255 - p for p in rgb_planes(im)] + [k], [(1, 1)] * 4, [1, 2, 3, 4], jfif=False,
                                       tables=[0, 0, 0, 0])
    base = pil(photo(161, 97), quality=82, subsampling=2)
    files["cut-half"] = cut(base, 0.5)
    files["cut-rst"] = cut(pil(photo(161, 97), quality=82, subsampling=2, restart_marker_blocks=4), 0.6)
    files["cut-prog"] = cut(pil(photo(161, 97), quality=82, subsampling=2, progressive=True), 0.7)
    for seed in (1, 2, 3):
        files[f"damaged-{seed}"] = damaged(base, seed)
    files["damaged-prog"] = damaged(pil(photo(161, 97), quality=82, subsampling=2, progressive=True), 4)
    files.update(asyncio.run(_edge_jpegs()))
    return files


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cases = []
    for name, data in corpus().items():
        try:
            im = Image.open(io.BytesIO(data))
            raw = im.tobytes()
        except Exception as exc:  # noqa: BLE001  (Pillow refuses it: so does the Shiny report)
            print(f"{name}: Pillow refuses it ({exc}); left out")
            continue
        (OUT / f"{name}.jpg").write_bytes(data)
        cases.append({"name": name, "width": im.size[0], "height": im.size[1], "mode": im.mode,
                      "md5": hashlib.md5(raw).hexdigest(), "bytes": len(data)})
    (OUT / "index.json").write_text(json.dumps({"pillow": Image.__version__, "cases": cases}, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases, {sum(c['bytes'] for c in cases)} bytes")


if __name__ == "__main__":
    main()
