"""The Excel calculator of the loaded assessment, blank and completed.

StreamCurves generates one workbook per published assessment version
(``streamcurves/deep_calculator.py``) and the bake step copies it to
``www/calculators/<id>@v<N>.xlsx`` with an ``index.json`` that records each
file's content digest. DEEP hands the blank out byte for byte, and fills a copy
with the values on the worksheet: the measured value of every metric, the curve
set in use, and the site block. The workbook recalculates on open, so a
completed copy shows the application's own indices, function scores and
Ecosystem Condition Index.

A version DEEP has from the remote library release (``deep/remote_library.py``)
and not from the bake gets the calculator published beside its bundle
(``<id>-v<N>-calculator-<sha8>.xlsx``), which the refresh downloads and checks
against the catalog's sha256.

A workbook is offered only when its recorded content digest equals the loaded
bundle's. A calculator built for another version of the curves is never handed
out as this one's.

openpyxl is not a DEEP dependency (adding it would change the desktop payload's
environment lock), and a load and save would drop the charts anyway. So, like
``apps/easi/easi/calculator.py``, this edits the worksheet XML inside the zip
and copies every other part through untouched. ``zipfile`` and ``re`` only.
"""
from __future__ import annotations

import copy
import datetime as _dt
import io
import json
import math
import numbers
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import reference_support

CALCULATOR_DIR = Path(__file__).resolve().parent.parent / "www" / "calculators"
#: Lets a local run read calculators baked somewhere else (a scratch library's),
#: the way STAF_LIBRARY_ROOT points DEEP at another library. Unset in deployments.
_ENV_DIR = "DEEP_CALCULATOR_DIR"
INDEX_NAME = "index.json"
SHEET_NAME = "DEEP Score"
ENTRY_PREFIXES = ("in_", "st_", "site_")
NOTES_NAME = "site_notes"
NOTES_LIMIT = 1500
POOLED_LABEL = reference_support.POOLED_LABEL

#: Excel's day zero for the 1900 date system (1899-12-30 absorbs the phantom
#: 1900-02-29 for every date from 1900-03-01 on).
_EXCEL_EPOCH = _dt.date(1899, 12, 30)


# --------------------------------------------------------------------------- #
# vocabulary shared with the generator (streamcurves/deep_calculator.py); the
# tests of both apps keep the two equal
# --------------------------------------------------------------------------- #
def metric_key(metric_id: str) -> str:
    """The defined-name stem of a metric: its id with every character outside
    ``A-Za-z0-9`` turned into an underscore."""
    return re.sub(r"[^A-Za-z0-9]", "_", str(metric_id))


def layer_label(stratum, metric: Optional[dict] = None) -> str:
    """What the workbook's curve-set list shows for a layer."""
    return reference_support.stratum_label(stratum, metric)


# --------------------------------------------------------------------------- #
# which workbook belongs to the loaded assessment
# --------------------------------------------------------------------------- #
def _raw(assessment) -> dict:
    raw = getattr(assessment, "raw", None)
    if isinstance(raw, dict) and raw:
        return raw
    return assessment if isinstance(assessment, dict) else {}


@lru_cache(maxsize=1)
def _index(directory: str) -> dict:
    path = Path(directory) / INDEX_NAME
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc.get("calculators") or {}


def clear_cache() -> None:
    _index.cache_clear()
    _read.cache_clear()


@lru_cache(maxsize=8)
def _read(path: str) -> bytes:
    """Bytes, not an open ``ZipFile``: handing a live archive's ``ZipInfo`` to
    ``writestr`` mutates it, which would corrupt a cached archive."""
    return Path(path).read_bytes()


def assessment_ref(assessment) -> Optional[str]:
    raw = _raw(assessment)
    if raw.get("assessmentRef"):
        return str(raw["assessmentRef"])
    aid = raw.get("assessmentId")
    version = raw.get("version") or (raw.get("library") or {}).get("version")
    return f"{aid}@v{int(version)}" if aid and version else None


def template_for(assessment, directory: Optional[Path] = None) -> Optional[bytes]:
    """The blank calculator of the loaded version, or ``None``.

    The baked workbook comes first. When none is shipped for the version, the
    index does not record it, or its recorded content digest differs from the
    loaded bundle's (the curves moved and the workbook did not), a version that
    arrived through the remote library gets the calculator published with it in
    the release, under the same rule (:func:`_remote_template`). ``None`` when
    neither has one."""
    directory = Path(directory or os.environ.get(_ENV_DIR) or CALCULATOR_DIR)
    ref = assessment_ref(assessment)
    if not ref:
        return None
    digest = _raw(assessment).get("contentDigest")
    if not digest:
        return None
    record = _index(str(directory)).get(ref)
    if isinstance(record, dict) and record.get("file") and record.get("contentDigest") == digest:
        path = directory / str(record["file"])
        if path.is_file():
            return _read(str(path))
    return _remote_template(ref, digest)


def _remote_template(ref: str, digest: str) -> Optional[bytes]:
    """The remote library's cached calculator for ``ref``, only when the release
    records it for the loaded bundle's content digest; else ``None``."""
    aid, sep, version = ref.rpartition("@v")
    if not sep or not aid:
        return None
    try:
        from . import remote_library  # local import: the release is optional

        path = remote_library.calculator_path(aid, int(version), digest)
    except Exception:  # noqa: BLE001 - a missing workbook never breaks the dialog
        return None
    if path is None:
        return None
    try:
        return _read(str(path))
    except OSError:
        return None


def blank_filename(assessment) -> str:
    ref = (assessment_ref(assessment) or "assessment").replace("@", "-")
    return f"deep-calculator-{ref}.xlsx"


def filled_filename(assessment, delineation=None) -> str:
    ref = (assessment_ref(assessment) or "assessment").replace("@", "-")
    dl = (delineation or {}).get("delineation") or {}
    site = ""
    if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
        site = f"-nhdplusid-{dl['nhdplus_id']}"
    elif dl.get("comid") not in (None, "", "None"):
        site = f"-comid-{dl['comid']}"
    return f"deep-calculator-{ref}{site}.xlsx"


# --------------------------------------------------------------------------- #
# entries from the worksheet state
# --------------------------------------------------------------------------- #
def entry_cells(template: bytes) -> dict[str, str]:
    """Entry name -> cell on the DEEP Score sheet, from the workbook's own
    defined names, so a regenerated workbook whose rows moved still fills."""
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        book = archive.read("xl/workbook.xml").decode("utf-8")
    cells: dict[str, str] = {}
    for name, target in re.findall(
            r'<definedName\b[^>]*\bname="([^"]+)"[^>]*>([^<]*)</definedName>', book):
        if not name.startswith(ENTRY_PREFIXES):
            continue
        sheet, _, addr = _xml_unescape(target).rpartition("!")
        if sheet.strip("'") != SHEET_NAME:
            continue
        cells[name] = addr.replace("$", "")
    if NOTES_NAME not in cells:
        raise ValueError("the DEEP calculator template has no entry cells")
    return cells


def entries_from_state(assessment, measured: Optional[dict], delineation=None,
                       ) -> tuple[dict, list[str]]:
    """Calculator entries for the worksheet's state, and what a reader should
    know about them: ``({entry name: value}, disclosure lines)``.

    A Not Applicable metric and a blank one stay blank (both drop out of the
    score, as in the application). A value the train and serve pairing rule
    keeps out of the score stays blank too, and is named in the notes, so the
    workbook never scores what the application withheld.
    """
    from . import curves, measure

    measured = measured or {}
    objects = measure.measured_from_state(measured)
    entries: dict = {}
    disclosures: list[str] = []
    withheld: list[str] = []
    seen: set = set()
    mbf = getattr(assessment, "metrics_by_function", None)
    if mbf is None:
        mbf = _raw(assessment).get("metricsByFunction") or []
    for fn in mbf:
        for m in fn.get("metrics", []):
            mid = m.get("metricId")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            rc = measured.get(mid) or {}
            key = metric_key(mid)
            layers = curves.curve_strata(m)
            if len(layers) > 1 and rc.get("stratum") is not None:
                entries[f"st_{key}"] = layer_label(rc.get("stratum"), m)
            value = rc.get("value")
            if rc.get("na") or value in (None, ""):
                continue
            mv = objects.get(mid)
            if mv is not None and curves.engine_pairing_advisory(mv, m) is not None:
                withheld.append(str(m.get("metricName") or mid))
                continue
            try:
                entries[f"in_{key}"] = float(value)
            except (TypeError, ValueError):
                continue
    if withheld:
        disclosures.append("Shown in the application as reference only and left blank here: "
                           + ", ".join(withheld) + ".")

    dl = (delineation or {}).get("delineation") or {}
    if dl:
        entries["site_name"] = dl.get("gnis_name") or "(unnamed stream)"
        if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
            entries["site_reach"] = f"NHDPlusID {dl['nhdplus_id']}"
        elif dl.get("comid") not in (None, "", "None"):
            entries["site_reach"] = f"COMID {dl['comid']}"
        try:
            entries["site_coords"] = (f"{float(dl.get('snapped_lat')):.5f}, "
                                      f"{float(dl.get('snapped_lon')):.5f}")
        except (TypeError, ValueError):
            pass
    return entries, disclosures


def notes_text(measured: Optional[dict], assessment, disclosures: list[str],
               today: _dt.date) -> str:
    lines = [f"Completed by the DEEP web application on {today.isoformat()}."]
    lines.extend(disclosures)
    names = {}
    for fn in (getattr(assessment, "metrics_by_function", None)
               or _raw(assessment).get("metricsByFunction") or []):
        for m in fn.get("metrics", []):
            names.setdefault(m.get("metricId"), m.get("metricName") or m.get("metricId"))
    notes = [f"{names.get(mid, mid)}: {' '.join(str(rc.get('note')).split())}"
             for mid, rc in (measured or {}).items()
             if isinstance(rc, dict) and str(rc.get("note") or "").strip()]
    if notes:
        lines.append("Assessor notes. " + " | ".join(notes))
    text = "\n".join(lines)
    return text if len(text) <= NOTES_LIMIT else text[:NOTES_LIMIT - 3].rstrip() + "..."


# --------------------------------------------------------------------------- #
# cell writing (the EASI filler's, unchanged in substance)
# --------------------------------------------------------------------------- #
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# An empty cell is <c .../> without lxml and <c ...></c> with it; both must fill.
_CELL_RE = re.compile(r'<c r="([A-Z]+[0-9]+)"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:/>|>(.*?)</c>)', re.S)
_TYPE_ATTR = re.compile(r'\s+t="[^"]*"')


def _xml_unescape(text: str) -> str:
    from xml.sax.saxutils import unescape
    return unescape(text, {"&apos;": "'", "&quot;": '"'})


def _xml_text(value) -> str:
    text = _ILLEGAL_XML.sub("", str(value))
    # Excel decodes _xHHHH_ on read, so a literal one in user text is escaped
    text = re.sub(r"_(x[0-9A-Fa-f]{4}_)", r"_x005F_\1", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _number_text(value) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def _fill_cells(sheet_xml: str, values: dict, *, replaceable: frozenset = frozenset()) -> str:
    """Replace each cell named in ``values`` with the same cell carrying it.

    The refs actually replaced are checked against the refs intended, so a
    workbook whose cells moved fails here and never comes back silently wrong.
    ``replaceable`` names the entry cells that ship with a value of their own
    (a curve-set cell holds its default), which the new value replaces; any
    other non-empty entry cell is an error.
    """
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        ref, attrs, content = m.group(1), m.group(2), m.group(3)
        if ref not in values:
            return m.group(0)
        if content and ref not in replaceable:
            raise ValueError(f"DEEP calculator entry cell {ref} is not empty in the template")
        seen.add(ref)
        value = values[ref]
        attrs = _TYPE_ATTR.sub("", attrs)
        number = _number_text(value)
        if number is not None:
            return f'<c r="{ref}"{attrs} t="n"><v>{number}</v></c>'
        return (f'<c r="{ref}"{attrs} t="inlineStr"><is><t xml:space="preserve">'
                f'{_xml_text(value)}</t></is></c>')

    out = _CELL_RE.sub(repl, sheet_xml)
    missing = set(values) - seen
    if missing:
        raise ValueError("the DEEP calculator template does not carry the expected entry "
                         "cells: " + ", ".join(sorted(missing)))
    return out


def _sheet_part(archive: zipfile.ZipFile, name: str) -> str:
    book = archive.read("xl/workbook.xml").decode("utf-8")
    rid = None
    for tag in re.findall(r"<sheet\b[^>]*/>", book):
        got = re.search(r'name="([^"]*)"', tag)
        if got and _xml_unescape(got.group(1)) == name:
            found = re.search(r'r:id="([^"]*)"', tag)
            rid = found.group(1) if found else None
            break
    if not rid:
        raise ValueError(f"the DEEP calculator template has no sheet named {name!r}")
    rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = None
    for tag in re.findall(r"<Relationship\b[^>]*/>", rels):
        if re.search(r'\bId="%s"' % re.escape(rid), tag):
            found = re.search(r'\bTarget="([^"]*)"', tag)
            target = found.group(1) if found else None
            break
    if not target:
        raise ValueError(f"the DEEP calculator template has no relationship {rid!r}")
    part = target.lstrip("/")
    return part if part.startswith("xl/") else f"xl/{part}"


def _repack(source: bytes, replacements: dict) -> bytes:
    """Rewrite the package with some parts replaced and the rest copied.
    ``copy.copy`` on each ``ZipInfo`` because ``writestr`` writes the CRC and
    sizes back into the object it is given."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as zin, \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(copy.copy(info), data)
    return buf.getvalue()


def build_filled(template: bytes, assessment, measured: Optional[dict], delineation=None, *,
                 today: Optional[_dt.date] = None) -> bytes:
    """The calculator with the worksheet's values typed into its entry cells.

    The workbook recalculates on open (``fullCalcOnLoad``) and carries no cached
    results, so only the DEEP Score part changes and every other part is copied
    byte for byte.
    """
    today = today or _dt.date.today()
    cells = entry_cells(template)
    entries, disclosures = entries_from_state(assessment, measured, delineation)
    entries["site_date"] = (today - _EXCEL_EPOCH).days
    entries[NOTES_NAME] = notes_text(measured, assessment, disclosures, today)
    values: dict = {}
    for name, value in entries.items():
        if name not in cells or value is None or isinstance(value, bool):
            continue
        if isinstance(value, numbers.Real):
            if _number_text(value) is None:
                continue
        else:
            value = str(value) if name == NOTES_NAME else str(value).strip()
            if not value:
                continue
        values[cells[name]] = value
    replaceable = frozenset(addr for name, addr in cells.items() if name.startswith("st_"))
    with zipfile.ZipFile(io.BytesIO(template)) as zin:
        part = _sheet_part(zin, SHEET_NAME)
        sheet_xml = zin.read(part).decode("utf-8")
    filled = _fill_cells(sheet_xml, values, replaceable=replaceable)
    return _repack(template, {part: filled.encode("utf-8")})
