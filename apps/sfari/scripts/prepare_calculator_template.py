"""Prepare the SFARI calculator template from the owner's master copy.

The master (``notes/<date>_SFARI Calculator/``, gitignored) is the workbook as the owner issues it.
The shipped template (``data/calculator/SFARI_Calculator_Template_<date>.xlsx``) is the calculator the
apps assemble every workbook from: the master's Instructions, its score sheet and its hidden sheets,
with:

- the apps' own tabs left out: since 2026-10-19 the master carries the Summary the apps write (in the
  owner's layout, ``workbook.py``), so the apps write it afresh for each assessment;
- E72 corrected where the master still carries the bug (owner decision 2026-09-08): the Biology
  category label's second threshold reads ``H72:H87`` like the other four, not ``H56:H71``;
- Instructions step 1 reading "...top of each scenario worksheet." (owner, 2026-10-19): a blank names
  the score sheet "Scenario", a completed workbook names each tab after its scenario;
- the 104 input cells cleared where the master carries an example (20 function scores, 80 Likert
  values), with every cached value and chart recomputed by Excel for the empty calculator;
- the master's ``absPath`` (the folder it was last saved in) left out, since the file is published,
  and the master's window size and place kept (an Excel save records the saver's screen).

Everything else is the master's. The text edits are made in the XML, so both protected sheets keep
their protection exactly as issued (password ``sfari``); Excel then removes the apps' tabs, clears
the inputs (unlocked cells) and recalculates the two live sheets. Windows with Excel, and a Python
that has pywin32::

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

#: the score sheet's names so far: the issued draft, the 2026-10-15 blank, the 2026-10-19 master
SCORE_SHEETS = ("Scenario", "Existing Conditions", "SFARI Score")
INSTRUCTIONS = "Instructions"
#: the tabs the apps write into every workbook themselves
AUTHORED = ("Summary", "ReferenceCurves")
PASSWORD = "sfari"
#: the input cells: the 20 function scores on their merge anchors, the 80 Likert values
FUNCTION_CELLS = [f"H{row}" for row in range(8, 85, 4)]
METRIC_CELLS = [f"J{row}" for row in range(8, 88)]
INPUT_CELLS = set(FUNCTION_CELLS + METRIC_CELLS)
E72_BUG = "IF(AVERAGE(H56:H71)/15&lt;0.7"
E72_FIX = "IF(AVERAGE(H72:H87)/15&lt;0.7"
#: Instructions step 1 as issued, as the apps served it from 2026-10-19, and as it reads now
STEP1_WAS = (" cells at the top of the SFARI Score worksheet.", " cells at the top of the Existing Conditions worksheet.")
STEP1 = " cells at the top of each scenario worksheet."
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


def sheet_names(parts: dict) -> list:
    book = parts["xl/workbook.xml"].decode("utf-8")
    return [unescape(n) for n in re.findall(r'<sheet\b[^>]*name="([^"]*)"', book)]


def score_sheet(parts: dict) -> str:
    names = sheet_names(parts)
    for name in SCORE_SHEETS:
        if name in names:
            return name
    raise SystemExit(f"no score sheet among {names}")


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
    part = sheet_part(parts, score_sheet(parts))
    xml = parts[part].decode("utf-8")
    cell = re.search(r'<c r="E72"[^>]*>.*?</c>', xml, re.S)
    if cell is None:
        raise SystemExit("the score sheet has no E72; check the master by hand")
    if E72_BUG in cell.group(0):
        fixed = cell.group(0).replace(E72_BUG, E72_FIX)
        parts[part] = (xml[:cell.start()] + fixed + xml[cell.end():]).encode("utf-8")
    elif E72_FIX not in cell.group(0):
        raise SystemExit("E72 is neither the formula this script corrects nor the corrected one; check by hand")
    strings = parts["xl/sharedStrings.xml"].decode("utf-8")
    found = [w for w in STEP1_WAS if w in strings]
    if found:
        if sum(strings.count(w) for w in found) != 1:
            raise SystemExit("Instructions step 1 appears more than once; check the master by hand")
        strings = strings.replace(found[0], STEP1)
    elif strings.count(STEP1) != 1:
        raise SystemExit("Instructions step 1 is not a text this script knows; check the master by hand")
    parts["xl/sharedStrings.xml"] = strings.encode("utf-8")
    write(infos, parts, out)


def excel_step(path: Path, score: str) -> None:
    """In Excel (a separate, hidden instance): remove the apps' own tabs, clear the input cells and
    recalculate the two live sheets. Only those two are recalculated (marked dirty, then a normal
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
        names = [ws.Name for ws in wb.Worksheets]
        for name in AUTHORED:
            if name in names:
                wb.Worksheets(name).Delete()
        ws = wb.Worksheets(score)
        for ref in FUNCTION_CELLS:
            ws.Range(ref).MergeArea.ClearContents()
        ws.Range("J8:J87").ClearContents()
        for name in (score, INSTRUCTIONS):
            wb.Worksheets(name).UsedRange.Dirty()
        xl.Calculate()
        wb.Worksheets(INSTRUCTIONS).Activate()
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
    ms, os_ = _strings(mp), _strings(op)
    book = op["xl/workbook.xml"].decode("utf-8")
    if "absPath" in book:
        problems.append("workbook.xml still names the folder it was saved in (absPath)")
    if _window(book) != _window(mp["xl/workbook.xml"].decode("utf-8")):
        problems.append(f"the window's size and place differ from the master's: {_window(book)}")
    if ("<workbookProtection" in book) != ("<workbookProtection" in mp["xl/workbook.xml"].decode("utf-8")):
        problems.append("workbook protection differs from the master")
    names = sheet_names(op)
    want = [n for n in sheet_names(mp) if n not in AUTHORED]
    if names != want:
        problems.append(f"sheets {names}, expected {want}")
    gone = sorted(set(mp) - set(op))
    # Excel numbers the worksheet parts again once a tab goes (the sheets are compared by name below)
    new = sorted(p for p in set(op) - set(mp) if not p.startswith("xl/worksheets/"))
    if new:
        problems.append(f"parts the master does not have: {new}")
    if any(not (p.startswith(("xl/worksheets/", "xl/printerSettings/", "xl/drawings/", "xl/charts/"))) for p in gone):
        problems.append(f"parts gone beyond the apps' tabs: {gone}")
    print(f"  tabs left out: {[n for n in sheet_names(mp) if n in AUTHORED]}; parts left out: {gone}")
    score = score_sheet(op)
    for name in names:
        part = sheet_part(op, name)
        mpart = sheet_part(mp, name)
        mc, oc = _cells(mp, mpart, ms), _cells(op, part, os_)
        if set(mc) != set(oc):
            problems.append(f"{name}: cells added or removed: {sorted(set(mc) ^ set(oc))[:20]}")
        for ref in sorted(set(mc) & set(oc)):
            (mf, mv), (of, ov) = mc[ref], oc[ref]
            if name == score and ref == "E72":
                if of is None or E72_FIX not in of[1] or E72_BUG in of[1]:
                    problems.append("E72 is not corrected")
                continue
            if mf != of:
                problems.append(f"{name}!{ref}: formula changed: {mf} -> {of}")
            elif mf is None:                                   # a typed cell: only the inputs and step 1 move
                if name == score and ref in INPUT_CELLS:
                    if ov is not None:
                        problems.append(f"{name}!{ref} still holds {ov!r}")
                elif name == INSTRUCTIONS and mv is not None and any(w.strip() in mv for w in STEP1_WAS + (STEP1,)):
                    if not ov or STEP1.strip() not in ov:
                        problems.append(f"{name}!{ref}: step 1 reads {ov!r}")
                    else:
                        print(f"  {name}!{ref}: {ov}")
                elif mv != ov:
                    problems.append(f"{name}!{ref}: value changed: {mv!r} -> {ov!r}")
        mx, ox = mp[mpart].decode("utf-8"), op[part].decode("utf-8")
        mprot = re.search(r"<sheetProtection\b[^>]*/>", mx)
        oprot = re.search(r"<sheetProtection\b[^>]*/>", ox)
        if (mprot and mprot.group(0)) != (oprot and oprot.group(0)):
            problems.append(f"{name}: sheet protection differs from the master")
        elif oprot and not _password_ok(oprot.group(0)):
            problems.append(f"{name}: the password is not {PASSWORD!r}")
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
    cells = _cells(op, sheet_part(op, score), os_)
    if not (cells.get("P86") or (None, None))[0]:
        problems.append("P86 has no formula")
    print(f"  score sheet {score!r}; the empty calculator caches: "
          + ", ".join(f"{r}={cells[r][1]!r}" for r in ("A8", "C8", "C56", "C72", "E8", "E72", "G8")))
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
            excel_step(work, score_sheet(read(work)[1]))
            tidy_book(work, a.master, a.out)
        print(f"wrote {a.out} ({a.out.stat().st_size:,} bytes, sha256 {hashlib.sha256(a.out.read_bytes()).hexdigest()})")
    problems = verify(a.master, a.out)
    for p in problems:
        print("PROBLEM:", p)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
