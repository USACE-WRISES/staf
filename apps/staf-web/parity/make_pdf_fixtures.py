"""Goldens for the static app's PDF writer (tests/pdf.test.ts), from reportlab 4.5.1 itself.

- ``fpStr``: fp_str on numbers around every rounding and exponent edge.
- ``widths``: stringWidth in the reports' fonts, characters outside WinAnsi included.
- ``canvas``: a two-page document drawn with the canvas calls the reports make (invariant), its bytes
  in ``pdf_canvas_ref.pdf``; the test draws the same and compares whole files.
- ``platypus``: a story (pdf_story.spec(): paragraphs of every kind, function blocks with spans,
  KeepTogether, a header-repeating table split over pages) laid out by reportlab, its bytes in
  ``pdf_platypus_ref.pdf``; the test lays out the same story with the port and compares whole files.

    .venv\\Scripts\\python apps/staf-web/parity/make_pdf_fixtures.py
"""
from __future__ import annotations

import io
import json
import random
import struct
import zlib
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.rl_accel import fp_str
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import pdf_story

HERE = Path(__file__).resolve().parent
FIX = HERE.parent / "tests" / "fixtures"


def png(width: int, height: int, ctype: int, rows: list[bytes], palette: bytes = b"", trns: bytes = b"") -> bytes:
    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
    if palette:
        out += chunk(b"PLTE", palette)
    if trns:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


RGBA = png(3, 2, 6, [bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0]), bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])])
PAL = png(2, 2, 3, [bytes([0, 1]), bytes([1, 0])], palette=bytes([200, 10, 10, 10, 200, 10]), trns=bytes([255, 0]))


def draw() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612.0, 792.0), invariant=1)
    c.setTitle("Canvas test")
    c.setAuthor("STAF")
    c.setFont("Times-Roman", 9)
    c.drawString(72, 700, "Plain text, (parens) and a back\\slash")
    c.drawCentredString(306, 680, "Accents: é è ü — ≥ ² “quotes” ✓")
    c.setFont("Helvetica-Bold", 11.5)
    c.drawRightString(540, 660, "Right aligned 12.5%")
    c.setStrokeColor(colors.HexColor("#c9d2de"))
    c.setLineWidth(0.5)
    c.line(72, 650, 540, 650)
    c.lines([(72, 640, 100, 640), (100, 640, 100, 600)])
    c.setFillColor("#667085")
    c.rect(72, 500, 100.25, 80.5, stroke=1, fill=1)
    c.setDash([2, 3], 0)
    c.rect(200, 500, 50, 50, stroke=1, fill=0)
    c.setDash([], 0)
    c.saveState()
    c.translate(300, 400)
    c.scale(2, 0.5)
    p = c.beginPath()
    p.moveTo(0, 0)
    p.lineTo(10.123456, 20.5)
    p.lineTo(30, 1e-8)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()
    c.drawImage(ImageReader(io.BytesIO(RGBA)), 72, 300, 90, 60, mask="auto")
    c.drawImage(ImageReader(io.BytesIO(PAL)), 200, 300, 40, 40, mask="auto")
    c.linkURL("https://usace-wrises.github.io/staf/", (72, 250, 300, 270), relative=1)
    c.showPage()
    c.setFont("Times-Bold", 14)
    c.setFillColor(colors.black)
    c.drawString(72, 720, "Page two")
    c.drawImage(ImageReader(io.BytesIO(RGBA)), 72, 600, 30, 20, mask="auto")
    c.showPage()
    return c.getpdfdata()


def main() -> None:
    rng = random.Random(7)
    nums = [0, 1, -1, 0.5, -0.5, 1e-7, 1.1e-7, -2e-7, 5e-7, 0.9999995, 1.0000005, 10, 100, 1000, 10000, 100000, 1e6, 1234567.891,
            999.9999995, 72.0, 612, 0.1, 0.2, 0.3, 14.399999999999999, 2.675, 1.005, 0.0000005, 123.4565, 0.788235294117647]
    nums += [rng.uniform(-1000, 1000) for _ in range(300)] + [rng.uniform(-2, 2) for _ in range(200)]
    nums += [round(rng.uniform(0, 800), 2) for _ in range(200)] + [10 ** k * (1 + d) for k in range(-3, 7) for d in (-1e-15, 0, 1e-15)]
    texts = ["Hello", "Times New Roman", "é è ü — ≥ ² “quotes” ✓", "∑∫ Ω", "日本", "", "a b", "Mink Brook (HR reach watershed)"]
    fonts = ["Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Times-Roman", "Times-Bold", "Times-Italic", "Courier"]
    ref = {
        "fpStr": [[n, fp_str(n)] for n in nums],
        "fpStrMany": fp_str(1.5, 2, -3.25, 0.000001, 1e-9),
        "widths": [[t, f, s, stringWidth(t, f, s)] for t in texts for f in fonts for s in (7.5, 9, 11.5)],
        "rgba": list(RGBA), "pal": list(PAL),
        "platypus": pdf_story.spec(),
    }
    (FIX / "pdf_ref.json").write_text(json.dumps(ref, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (FIX / "pdf_canvas_ref.pdf").write_bytes(draw())
    (FIX / "pdf_platypus_ref.pdf").write_bytes(pdf_story.build(ref["platypus"]))
    print("wrote pdf_ref.json, pdf_canvas_ref.pdf and pdf_platypus_ref.pdf")


if __name__ == "__main__":
    main()
