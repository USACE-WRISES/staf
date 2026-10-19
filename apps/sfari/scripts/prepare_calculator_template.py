"""Prepare the SFARI calculator template from the owner's master copy.

The master (``notes/<date>_SFARI_Calculator/``, gitignored) is the workbook as its developers issue
it, with an example assessment typed in. The shipped template
(``data/calculator/SFARI_Calculator_Template_<date>.xlsx``) is that workbook with:

- E72 corrected (owner decision 2026-09-08): the Biology category label's second threshold reads
  ``H72:H87`` like the other four, not Physicochemistry's ``H56:H71``;
- Instructions step 1 naming the "Existing Conditions worksheet", the score sheet's name in every
  workbook the apps serve;
- the 104 input cells cleared (20 function scores, 80 Likert values), with every cached value and
  chart recomputed by Excel for the empty calculator;
- the master's ``absPath`` (the folder it was last saved in) left out, since the file is published,
  and the master's window size and place kept (an Excel save records the saver's screen).

Everything else is the master's. The two text edits are made in the XML, so both sheets keep their
protection exactly as issued (password ``sfari``); Excel then clears the inputs, which are unlocked
cells, and recalculates. Windows with Excel, and a Python that has pywin32::

    py -3.12 apps/sfari/scripts/prepare_calculator_template.py MASTER.xlsx OUT.xlsx

It ends by comparing the result with the master and exits 1 on anything unexpected; ``--verify``
runs only that comparison on an existing OUT. Afterwards run ``scripts/build_blank_calculator.py``
(the blank workbook is generated from the template) and update ``calculator.TEMPLATE_VERSION`` and
the digest in ``tests/test_calculator.py``.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import re
import struct
import sys
import tempfile
import zipfile
from html import unescape
from pathlib import Path

SCORE_SHEET = "SFARI Score"
INSTRUCTIONS = "Instructions"
PASSWORD = "sfari"
#: the input cells: the 20 function scores on their merge anchors, the 80 Likert values
FUNCTION_CELLS = [f"H{row}" for row in range(8, 85, 4)]
METRIC_CELLS = [f"J{row}" for row in range(8, 88)]
INPUT_CELLS = set(FUNCTION_CELLS + METRIC_CELLS)
E72_BUG = "IF(AVERAGE(H56:H71)/15&lt;0.7"
E72_FIX = "IF(AVERAGE(H72:H87)/15&lt;0.7"
STEP1_OLD = " cells at the top of the SFARI Score worksheet."
STEP1_NEW = " cells at the top of the Existing Conditions worksheet."
ABS_PATH = re.compile(r'<mc:AlternateContent\b[^>]*>\s*<mc:Choice Requires="x15">\s*<x15ac:absPath\b[^>]*/>'
                      r'\s*</mc:Choice>\s*</mc:AlternateContent>')
CELL = re.compile(r'<c r="([A-Z]+[0-9]+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)


# --- the package ----------------------------------------------------------------
def read(path: Path) -> tuple:
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        return infos, dict((i.filename, z.read(i.filename)) for i in infos)


def write(infos: list, parts: dict, out: Path) -> None:
    """Every part in its original order, compression and date (``copy.copy``: ``writestr`` writes
    the sizes back into the ``ZipInfo`` it is given)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in infos:
            z.writestr(copy.copy(info), parts[info.filename])
    out.write_bytes(buf.getvalue())


def sheet_part(parts: dict, name: str) -> str:
    book = parts["xl/workbook.xml"].decode("utf-8")
    rid = None
    for tag in re.findall(r"<sheet\b[^>]*/>", book):
        got = re.search(r'name="([^"]*)"', tag)
        if got and unescape(got.group(1)) == name:
            rid = re.search(r'r:id="([^"]*)"', tag).group(1)
    if rid is None:
        raise SystemExit(f"no sheet named {name!r}")
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    target = re.search(r'<Relationship[^>]*Id="%s"[^>]*Target="([^"]*)"' % rid, rels).group(1).lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


# --- the steps ------------------------------------------------------------------
def edit_text(master: Path, out: Path) -> None:
    """E72 and Instructions step 1, in the XML (both are locked cells on protected sheets)."""
    infos, parts = read(master)
    part = sheet_part(parts, SCORE_SHEET)
    xml = parts[part].decode("utf-8")
    cell = re.search(r'<c r="E72"[^>]*>.*?</c>', xml, re.S)
    if cell is None or cell.group(0).count(E72_BUG) != 1:
        raise SystemExit("E72 does not carry the formula this script corrects; check the master by hand")
    fixed = cell.group(0).replace(E72_BUG, E72_FIX)
    parts[part] = (xml[:cell.start()] + fixed + xml[cell.end():]).encode("utf-8")
    strings = parts["xl/sharedStrings.xml"].decode("utf-8")
    if strings.count(STEP1_OLD) != 1:
        raise SystemExit("Instructions step 1 is not the text this script rewords; check the master by hand")
    parts["xl/sharedStrings.xml"] = strings.replace(STEP1_OLD, STEP1_NEW).encode("utf-8")
    write(infos, parts, out)


def excel_clear(path: Path) -> None:
    """Clear the input cells and recalculate the two live sheets, in Excel (a separate, hidden
    instance). Only the score sheet and Instructions are recalculated (marked dirty, then a normal
    recalculation): a full one also recalculates the hidden legacy sheets, which nothing reads, and
    Excel then marks their spill formulas volatile."""
    import win32com.client                                  # Windows only; not needed by --verify

    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = False
    xl.DisplayAlerts = False
    try:
        wb = xl.Workbooks.Open(str(path.resolve()), 0, False)
        caption = wb.Windows(1).Caption
        if "Repaired" in caption:
            raise SystemExit(f"Excel repaired the workbook on open: {caption}")
        ws = wb.Worksheets(SCORE_SHEET)
        for ref in FUNCTION_CELLS:
            ws.Range(ref).MergeArea.ClearContents()
        ws.Range("J8:J87").ClearContents()
        for name in (SCORE_SHEET, INSTRUCTIONS):
            wb.Worksheets(name).UsedRange.Dirty()
        xl.Calculate()
        wb.Save()
        wb.Close(False)
    finally:
        xl.Quit()


def _window(book: str) -> dict:
    tag = re.search(r"<workbookView\b[^>]*>", book).group(0)
    return dict(re.findall(r'\b(xWindow|yWindow|windowWidth|windowHeight)="([^"]*)"', tag))


def tidy_book(path: Path, master: Path, out: Path) -> None:
    """workbook.xml without the folder it was saved in, and with the master's window size and
    place (Excel records the screen of whoever saved it last)."""
    infos, parts = read(path)
    book = parts["xl/workbook.xml"].decode("utf-8")
    book, n = ABS_PATH.subn("", book)
    if n > 1:
        raise SystemExit("workbook.xml has more than one absPath block")
    want = _window(read(master)[1]["xl/workbook.xml"].decode("utf-8"))
    tag = re.search(r"<workbookView\b[^>]*>", book).group(0)
    new = tag
    for key, value in want.items():
        new = re.sub(rf'\b{key}="[^"]*"', f'{key}="{value}"', new)
    parts["xl/workbook.xml"] = book.replace(tag, new, 1).encode("utf-8")
    write(infos, parts, out)


# --- the check ------------------------------------------------------------------
def _strings(parts: dict) -> list:
    xml = parts["xl/sharedStrings.xml"].decode("utf-8")
    return [unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))) for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]


def _cells(parts: dict, part: str, strings: list) -> dict:
    """``{ref: (formula or None, value or None)}`` with shared strings resolved. A formula's
    calculate-always flags (``ca``, ``aca``) are left out: Excel sets them as it recalculates."""
    out = {}
    for ref, attrs, body in CELL.findall(parts[part].decode("utf-8")):
        t = re.search(r'\bt="([^"]*)"', attrs)
        f = re.search(r"<f\b([^>]*)>(.*?)</f>", body or "", re.S)
        fe = re.search(r"<f\b([^>]*)/>", body or "")
        v = re.search(r"<v>(.*?)</v>", body or "", re.S)
        value = v.group(1) if v else None
        if t and t.group(1) == "s" and value is not None:
            value = strings[int(value)]
        formula = (f.group(1), f.group(2)) if f else ((fe.group(1), "") if fe else None)
        if formula is not None:
            formula = (re.sub(r'\s+a?ca="[^"]*"', "", formula[0]).strip(), formula[1])
            if formula == ("", ""):
                formula = None
        out[ref] = (formula, value)
    return out


def _password_ok(tag: str) -> bool:
    salt = base64.b64decode(re.search(r'saltValue="([^"]*)"', tag).group(1))
    want = re.search(r'hashValue="([^"]*)"', tag).group(1)
    spin = int(re.search(r'spinCount="([^"]*)"', tag).group(1))
    h = hashlib.sha512(salt + PASSWORD.encode("utf-16-le")).digest()
    for i in range(spin):
        h = hashlib.sha512(h + struct.pack("<I", i)).digest()
    return base64.b64encode(h).decode() == want


def verify(master: Path, out: Path) -> list:
    """What differs from the master that should not; prints what differs as intended."""
    problems = []
    _mi, mp = read(master)
    _oi, op = read(out)
    if set(mp) != set(op):
        problems.append(f"parts differ: only in the master {sorted(set(mp) - set(op))}, "
                        f"only in the template {sorted(set(op) - set(mp))}")
    ms, os_ = _strings(mp), _strings(op)
    book = op["xl/workbook.xml"].decode("utf-8")
    if "absPath" in book:
        problems.append("workbook.xml still names the folder it was saved in (absPath)")
    if _window(book) != _window(mp["xl/workbook.xml"].decode("utf-8")):
        problems.append(f"the window's size and place differ from the master's: {_window(book)}")
    if ("<workbookProtection" in book) != ("<workbookProtection" in mp["xl/workbook.xml"].decode("utf-8")):
        problems.append("workbook protection differs from the master")
    names = [unescape(n) for n in re.findall(r'<sheet\b[^>]*name="([^"]*)"', book)]
    if names != [unescape(n) for n in re.findall(r'<sheet\b[^>]*name="([^"]*)"', mp["xl/workbook.xml"].decode("utf-8"))]:
        problems.append(f"sheet names or order differ: {names}")
    for name in names:
        part = sheet_part(op, name)
        if part != sheet_part(mp, name):
            problems.append(f"{name}: part moved")
            continue
        mc, oc = _cells(mp, part, ms), _cells(op, part, os_)
        if set(mc) != set(oc):
            problems.append(f"{name}: cells added or removed: {sorted(set(mc) ^ set(oc))[:20]}")
        for ref in sorted(set(mc) & set(oc)):
            (mf, mv), (of, ov) = mc[ref], oc[ref]
            if name == SCORE_SHEET and ref == "E72":
                if of is None or E72_FIX not in of[1] or E72_BUG in of[1]:
                    problems.append("E72 is not corrected")
                continue
            if mf != of:
                problems.append(f"{name}!{ref}: formula changed: {mf} -> {of}")
            elif mf is None:                                   # a typed cell: only the inputs and step 1 move
                if name == SCORE_SHEET and ref in INPUT_CELLS:
                    if ov is not None:
                        problems.append(f"{name}!{ref} still holds {ov!r}")
                elif name == INSTRUCTIONS and mv is not None and STEP1_OLD.strip() in mv:
                    if ov != mv.replace(STEP1_OLD, STEP1_NEW):
                        problems.append(f"{name}!{ref}: step 1 reads {ov!r}")
                    else:
                        print(f"  {name}!{ref}: {ov}")
                elif mv != ov:
                    problems.append(f"{name}!{ref}: value changed: {mv!r} -> {ov!r}")
        mprot = re.search(r"<sheetProtection\b[^>]*/>", mp[part].decode("utf-8"))
        oprot = re.search(r"<sheetProtection\b[^>]*/>", op[part].decode("utf-8"))
        if (mprot and mprot.group(0)) != (oprot and oprot.group(0)):
            problems.append(f"{name}: sheet protection differs from the master")
        elif oprot and not _password_ok(oprot.group(0)):
            problems.append(f"{name}: the password is not {PASSWORD!r}")
        mx, ox = mp[part].decode("utf-8"), op[part].decode("utf-8")
        if set(re.findall(r'<mergeCell ref="([^"]*)"', mx)) != set(re.findall(r'<mergeCell ref="([^"]*)"', ox)):
            problems.append(f"{name}: merged cells differ from the master")      # their order is Excel's
        for block in ("dataValidations", "conditionalFormatting"):
            mb = re.findall(rf"<{block}\b.*?</{block}>", mx, re.S)
            ob = re.findall(rf"<{block}\b.*?</{block}>", ox, re.S)
            if [re.sub(r'\sxr:uid="[^"]*"', "", b) for b in mb] != [re.sub(r'\sxr:uid="[^"]*"', "", b) for b in ob]:
                problems.append(f"{name}: {block} differ from the master")
        flagged = len(re.findall(r'<f\b[^>]*\sa?ca="1"', ox)) - len(re.findall(r'<f\b[^>]*\sa?ca="1"', mx))
        if flagged:
            print(f"  {name}: Excel marked {flagged} formula(s) calculate-always")
    score = _cells(op, sheet_part(op, SCORE_SHEET), os_)
    if not (score.get("P86") or (None, None))[0]:
        problems.append("P86 has no formula")
    print("  the empty calculator caches: " + ", ".join(f"{r}={score[r][1]!r}" for r in ("A8", "C8", "C56", "C72", "E8", "E72", "G8")))
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("master", type=Path, help="the owner's master workbook")
    ap.add_argument("out", type=Path, help="the template to write")
    ap.add_argument("--verify", action="store_true", help="only compare an existing OUT with the master")
    a = ap.parse_args(argv)
    if not a.verify:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "calculator.xlsx"
            edit_text(a.master, work)
            excel_clear(work)
            tidy_book(work, a.master, a.out)
        print(f"wrote {a.out} ({a.out.stat().st_size:,} bytes, sha256 {hashlib.sha256(a.out.read_bytes()).hexdigest()})")
    problems = verify(a.master, a.out)
    for p in problems:
        print("PROBLEM:", p)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
