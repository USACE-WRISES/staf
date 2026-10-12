"""Compare two PDFs the way the static app's goldens need: same objects, and each page's decoded content
stream line by line (the first differences shown with their neighbours).

    .venv\\Scripts\\python apps/staf-web/parity/pdf_compare.py expected.pdf actual.pdf [--lines 6]
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from pypdf import PdfReader


def mask(pdf: bytes) -> bytes:
    """The file with its creation and modification dates and its /ID blanked (they carry the time a
    non-invariant PDF was made)."""
    pdf = re.sub(rb"\(D:\d{14}[+-]\d\d'\d\d'\)", b"(D:DATE)", pdf)
    return re.sub(rb"/ID\s*\[<[0-9a-fA-F]+><[0-9a-fA-F]+>\]", b"/ID [ID]", pdf)


def page_streams(path: str) -> list[list[str]]:
    reader = PdfReader(path)
    out = []
    for page in reader.pages:
        data = page.get_contents().get_data() if page.get_contents() is not None else b""
        out.append(data.decode("latin-1").split("\n"))
    return out


def compare_pdfs(expected, actual) -> list[str]:
    """For compare_files.py: [] when the two PDFs are one file, or one but for the dates and the file ID
    (a PDF made without invariant=1); else what differs, page by page (the changed stream lines)."""
    exp = Path(expected).read_bytes()
    act = Path(actual).read_bytes()
    if exp == act or mask(exp) == mask(act):
        return []
    E = page_streams(str(expected))
    A = page_streams(str(actual))
    out = [f"{Path(expected).name}: {len(exp)} and {len(act)} bytes, {len(E)} and {len(A)} pages"]
    for i, (e, x) in enumerate(zip(E, A)):
        if e != x:
            lines = [ln for ln in difflib.unified_diff(e, x, lineterm="", n=0) if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
            out.append(f"  page {i + 1}: " + " | ".join(ln[:110] for ln in lines[:6]))
    if len(out) == 1:
        out.append("  the page streams are the same; the difference is elsewhere in the file")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("expected")
    ap.add_argument("actual")
    ap.add_argument("--lines", type=int, default=6)
    a = ap.parse_args()
    exp = open(a.expected, "rb").read()
    act = open(a.actual, "rb").read()
    if exp == act:
        print(f"identical ({len(exp)} bytes)")
        return 0
    if mask(exp) == mask(act):
        print(f"identical but for the dates and the file ID ({len(exp)} bytes; a PDF made without invariant=1)")
        return 0
    E = page_streams(a.expected)
    A = page_streams(a.actual)
    print(f"bytes: expected {len(exp)}, actual {len(act)}; pages: expected {len(E)}, actual {len(A)}")
    shown = 0
    for i, (e, x) in enumerate(zip(E, A)):
        if e == x:
            continue
        print(f"--- page {i + 1}: {len(e)} vs {len(x)} lines")
        for line in list(difflib.unified_diff(e, x, "expected", "actual", n=2, lineterm=""))[: a.lines * 6]:
            print(line[:220])
        shown += 1
        if shown >= 2:
            break
    if not shown:
        print("page streams identical; the difference is elsewhere in the file")
        for i, (p, q) in enumerate(zip(exp, act)):
            if p != q:
                print(f"first byte difference at {i}:")
                print("  expected:", exp[max(0, i - 120): i + 120])
                print("  actual:  ", act[max(0, i - 120): i + 120])
                break
    return 1


if __name__ == "__main__":
    sys.exit(main())
