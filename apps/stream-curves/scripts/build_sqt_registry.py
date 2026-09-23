"""Build data/sqt/registry.json: published state SQT curves StreamCurves can offer as
candidate curve sources.

One record per state x metric x stratum curve, read from the RAW bins of the STAF metric
library CSV (not the clamped compiled curves) and cross-referenced with:

  - the adapted ``*-sqt-adapted`` assessments in apps/library (what DEEP scores today),
  - the compiled metric library (metric ids, citations),
  - original SQT files on disk: the two partial originals in data/templates, and whatever
    the owner adds under D:/Data/staf-authoring/sqt-originals/<STATE>/ with a
    ``sources.json`` naming each file's edition, citation and layout (data/sqt/README.md).

A record is ``verified`` only when its values were matched to an original file on disk.
Deterministic: sorted keys, records in key order, LF newlines, no timestamps.

    py -3.12 scripts/build_sqt_registry.py                   # write the registry
    py -3.12 scripts/build_sqt_registry.py --check           # exit 1 when it is stale
    py -3.12 scripts/build_sqt_registry.py --list-originals  # show the originals' tables
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Optional

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
sys.path.insert(0, str(APP_ROOT))

from streamcurves import sqt_registry as sr  # noqa: E402

METRIC_LIBRARY_DIR = REPO_ROOT / "docs" / "assets" / "data" / "metric-library"
ASSESSMENTS_DIR = REPO_ROOT / "apps" / "library" / "assessments"
TEMPLATES_DIR = APP_ROOT / "data" / "templates"
FUNCTIONS_PATH = APP_ROOT / "config" / "staf_functions.json"
REGISTRY_PATH = sr.REGISTRY_PATH
ORIGINALS_ENV = "STAF_SQT_ORIGINALS"
DEFAULT_ORIGINALS_ROOT = Path("D:/Data/staf-authoring/sqt-originals")
ORIGINALS_LABEL = "sqt-originals"

#: relative difference still read as drift (not a mismatch) against an original
DRIFT_TOLERANCE = 0.025
#: the index levels of the six SQT bins (the CSV and both original layouts use them)
DEFAULT_LEVELS = (0.0, 0.29, 0.30, 0.69, 0.70, 1.0)
#: the index levels an SQT row writes in its bins (template, not data)
SQT_INDEX_LEVELS = {0.0, 0.2, 0.29, 0.3, 0.5, 0.6, 0.69, 0.7, 1.0}
PLACEHOLDER_POINTS = [(0.0, 0.0), (1.0, 0.4), (2.0, 0.7), (3.0, 1.0)]

GE, LE = "\u2265", "\u2264"

STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL",
    "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
STATE_NAMES = {v: k for k, v in STATE_CODES.items()}
_SUFFIX = re.compile(r"\s+-\s+(" + "|".join(sorted(map(re.escape, STATE_CODES), key=len,
                                                     reverse=True)) + r") SQT\s*$")

#: constructs whose SQT curve rises to an optimum and falls again, with the evidence in
#: this repository for each. A record of one of these that holds a single limb cannot score
#: values past the optimum. ``types``: the stream types the two-sided curve applies to
#: (None: every stratum).
TWO_SIDED_CONSTRUCTS = {
    "habitat-provision-pool-spacing-ratio": {
        "name": "Pool spacing ratio for C and E stream types",
        "types": {"C", "Cb", "E", "E5", "Eb"},
        "evidence": ("the MN v2.0 list (row 18) and the WI curves sheet (rows 363 and 364) "
                     "give both limbs, and one NC stratum (CSV row 257) carries both"),
    },
    "habitat-provision-percent-riffle": {
        "name": "Percent riffle",
        "types": None,
        "evidence": ("the MN v2.0 list (rows 20 and 21) and the WI curves sheet (rows 436 "
                     "and 472) give both limbs, and the NC rows mark the end of the optimum"),
    },
    "channel-evolution-width-depth-ratio-state": {
        "name": "Width/depth ratio state",
        "types": None,
        "evidence": ("the WI curves sheet (rows 145 and 146) gives both limbs; the WY row "
                     "carries the rising limb and the SC and WI rows the falling limb"),
    },
}

#: words that make a stratum a sub-state region
_REGION_WORDS = re.compile(
    r"\b(Interior|Range|Mountains?|Piedmont|Coastal|Plains?|Basins?|Bioregion|Biotypes?|"
    r"Northern|Southern|Prairie|North|South|Central|Drift|River basins)\b")
_STREAM_TOKEN = r"(?:A|B|Ba|Bc|C|Cb|D|DA|E|Eb|E5|F|Fb|G|Gc)"
_STREAM_LIST = _STREAM_TOKEN + r"(?:\s*(?:,|&|and|or)\s*(?:and\s+|or\s+)?" + _STREAM_TOKEN + r")*"
_STREAMS_AFTER = re.compile(r"\b[Ss]tream [Tt]ypes?\s+(" + _STREAM_LIST + r")\b")
_STREAMS_BEFORE = re.compile(r"(?<![A-Za-z])(" + _STREAM_LIST + r")\s+[Ss]tream(?:s| [Tt]ypes?)?\b")
_EXTRA_BREAKPOINT = re.compile(
    r"Extra breakpoint:\s*(-?\d+(?:\.\d+)?)\s*->\s*(-?\d+(?:\.\d+)?)", re.I)
_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_OP_NUMBER = re.compile(r"^(>=|<=|>|<|" + GE + "|" + LE + r")\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$")
_RANGE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*[-\u2013]\s*([+-]?\d+(?:\.\d+)?)$")
_EDITION_HINT = re.compile(r"\b([A-Z]{2}) SQT(?: (\d{4}))?\b")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _rel(path: Path, originals_root: Optional[Path] = None) -> str:
    """A path for the record: repo-relative, or ``sqt-originals/...`` for owner files."""
    p = Path(path).resolve()
    if originals_root is not None:
        try:
            return ORIGINALS_LABEL + "/" + p.relative_to(Path(originals_root).resolve()).as_posix()
        except ValueError:
            pass
    try:
        return p.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return p.name


def _file_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slugify(text: str) -> str:
    """Lowercase words joined by hyphens; comparison signs and ``&`` kept as words so that
    ``W < 20ft`` and ``W > 20ft`` stay distinct."""
    s = str(text or "").lower()
    for sign, word in (("<=", " le "), (LE, " le "), (">=", " ge "), (GE, " ge "),
                       ("<", " lt "), (">", " gt "), ("&", " and "), ("%", " pct ")):
        s = s.replace(sign, word)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unnamed"


def _number(text: Any) -> Optional[float]:
    s = str(text if text is not None else "").strip()
    return float(s) if _PLAIN_NUMBER.match(s) else None


def _decimals(text: str) -> int:
    s = str(text).strip().lstrip("+-")
    return len(s.split(".", 1)[1]) if "." in s else 0


def _num_text(v: Any) -> str:
    """The shortest text of a number read from a workbook (68, 1.493, 0.9)."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v).strip()


def _half_up(value: str, dp: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)


def compare_values(record_text: str, original_text: str) -> str:
    """exact | consistent (the original rounds the record) | rounded (the record rounds the
    original) | drift (within DRIFT_TOLERANCE) | mismatch."""
    r, o = float(record_text), float(original_text)
    if r == o:
        return "exact"
    rd, od = _decimals(record_text), _decimals(original_text)
    if od < rd and _half_up(record_text, od) == Decimal(original_text):
        return "consistent"
    if rd < od and _half_up(original_text, rd) == Decimal(record_text):
        return "rounded"
    if abs(r - o) <= DRIFT_TOLERANCE * max(abs(r), abs(o)):
        return "drift"
    return "mismatch"


def _state_from_label(label: str) -> Optional[str]:
    m = _SUFFIX.search(str(label or ""))
    return STATE_CODES[m.group(1)] if m else None


def stratum_name(label: str) -> str:
    """A stratum label without its ``- <State> SQT`` suffix and ``Default -`` prefix."""
    s = _SUFFIX.sub("", str(label or "")).strip()
    s = re.sub(r"^Default\s*-\s*", "", s, flags=re.I).strip()
    return s or "Default"


def compiled_strat_label(label: str) -> str:
    """The stratum text the compiled library and the adapted bundles store."""
    s = _SUFFIX.sub("", str(label or "")).strip()
    s = re.sub(r"^default\s*-\s*", "", s, flags=re.I)
    s = re.sub(r"^default$", "", s, flags=re.I)
    return s.strip()


def parse_stream_types(label: str) -> Optional[dict]:
    """``{"types": [...]}`` or ``{"except": [...]}`` when a stratum names Rosgen stream types."""
    text = str(label or "")
    if re.search(r"\bAll other Streams\b", text, re.I):
        return {"types": [], "except": []}
    m = _STREAMS_AFTER.search(text) or _STREAMS_BEFORE.search(text)
    if not m:
        return None
    tokens = [t for t in re.split(r"\s*(?:,|&|\band\b|\bor\b)\s*", m.group(1)) if t]
    types = [t for t in tokens if re.fullmatch(_STREAM_TOKEN, t)]
    return {"types": types, "except": []} if types else None


# --------------------------------------------------------------------------- #
# the metric library CSV
# --------------------------------------------------------------------------- #
def find_metric_library_csv(folder: Path = METRIC_LIBRARY_DIR) -> Path:
    found = sorted(Path(folder).glob("Metric Library Complete *.csv"))
    if not found:
        raise FileNotFoundError(f"no 'Metric Library Complete *.csv' in {folder}")

    def dated(p: Path) -> tuple:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        return (m.group(1) if m else "", p.name)

    return max(found, key=dated)


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


ROW_FIELDS = ("Metric Name", "Mapped Function", "Source", "Discipline", "Recommended Tiers",
              "References")


class MetricLibrary:
    """The raw CSV: header positions, rows, and the stratification groups of each row."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.header, self.rows = read_csv(self.path)
        self.col = {h: i for i, h in enumerate(self.header)}
        self.groups = []
        n = 1
        while f"Stratification {n}" in self.col:
            self.groups.append(n)
            n += 1

    def cell(self, row: list[str], name: str) -> str:
        i = self.col.get(name)
        return row[i] if i is not None and i < len(row) else ""

    def group_cells(self, row: list[str], n: int) -> list[str]:
        start = self.col[f"Stratification {n}"]
        return list(row[start:start + 22])

    def row_identity(self, row: list[str]) -> dict:
        return {k: self.cell(row, k) for k in ROW_FIELDS}

    def csv_row(self, index: int) -> int:
        """The 1-based CSV row of a data row (header is row 1), as the compiled library."""
        return index + 2


def parse_bins(cells: list[str]) -> list[dict]:
    """The six bins of a stratification group, exactly as written plus parsed numbers."""
    bins = []
    for b in range(6):
        field, desc, index = (cells[4 + 3 * b], cells[5 + 3 * b], cells[6 + 3 * b])
        entry = {"bin": b + 1, "field": field, "index": index, "desc": desc,
                 "fieldNumber": _number(field), "indexNumber": _number(index)}
        m = _OP_NUMBER.match(field.strip())
        if m:
            entry["fieldOp"] = {"op": m.group(1).replace(GE, ">=").replace(LE, "<="),
                                "value": float(m.group(2))}
        bins.append(entry)
    return bins


def edition_hints(lib: MetricLibrary) -> dict[str, dict]:
    """``{code: {"hints": [...], "rows": [...]}}`` from every References cell."""
    out: dict[str, dict] = {}
    for i, row in enumerate(lib.rows):
        for code, year in _EDITION_HINT.findall(lib.cell(row, "References")):
            if code not in STATE_NAMES:
                continue
            e = out.setdefault(code, {"hints": set(), "rows": set()})
            if year:
                e["hints"].add(f"{code} SQT {year}")
            e["rows"].add(lib.csv_row(i))
    return {k: {"hints": sorted(v["hints"]), "rows": sorted(v["rows"])} for k, v in out.items()}


# --------------------------------------------------------------------------- #
# compiled library, functions, adapted bundles
# --------------------------------------------------------------------------- #
def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class CompiledLibrary:
    """CSV row -> compiled metric id, and the compiled detailed curve layers."""

    def __init__(self, folder: Path = METRIC_LIBRARY_DIR):
        self.folder = Path(folder)
        self.metric_by_row: dict[int, str] = {}
        self.detail: dict[str, dict] = {}
        for p in sorted((self.folder / "metrics").glob("*.json")):
            d = _load_json(p)
            mid = d.get("metricId")
            self.detail[mid] = d
            for r in (d.get("sourceMetadata") or {}).get("sourceRows") or []:
                self.metric_by_row[int(r)] = mid
        self._curves: dict[str, Optional[dict]] = {}

    def curve_layers(self, metric_id: str) -> list[dict]:
        if metric_id not in self._curves:
            p = self.folder / "curves" / f"curve-{metric_id}-detailed.json"
            self._curves[metric_id] = _load_json(p) if p.exists() else None
        doc = self._curves[metric_id]
        if not doc:
            return []
        return [L for c in doc.get("curves") or [] for L in c.get("layers") or []]

    def layer(self, metric_id: str, citation: str, strat_label: str) -> Optional[dict]:
        for L in self.curve_layers(metric_id):
            sm = L.get("sourceMetadata") or {}
            if sm.get("sourceCitation") == citation and (sm.get("stratification") or "") \
                    == strat_label:
                return L
        return None


class Functions:
    """Mapped Function text -> canonical STAF function id and name."""

    def __init__(self, path: Path = FUNCTIONS_PATH):
        self.by_name: dict[str, dict] = {}
        for f in _load_json(path).get("functions") or []:
            for name in [f["name"], *(f.get("aliases") or [])]:
                self.by_name[self._key(name)] = f

    @staticmethod
    def _key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower().replace("&", "and")).strip()

    def resolve(self, mapped: str) -> Optional[dict]:
        return self.by_name.get(self._key(mapped))


class AdaptedBundles:
    """Every version of every ``*-sqt-adapted`` assessment: layers by (state, metric, stratum)."""

    def __init__(self, folder: Path = ASSESSMENTS_DIR):
        self.folder = Path(folder)
        self.inputs: list[dict] = []
        self.layers: dict[tuple, list[dict]] = {}
        self.all_layers: list[dict] = []
        for adir in sorted(self.folder.glob("*-sqt-adapted")):
            for vdir in sorted(adir.glob("v*"), key=lambda p: int(p.name[1:] or 0)
                               if p.name[1:].isdigit() else -1):
                bundle_path = vdir / "assessment.deep.json"
                if not (vdir.name[1:].isdigit() and bundle_path.exists()):
                    continue
                version = int(vdir.name[1:])
                bundle = _load_json(bundle_path)
                self.inputs.append({"assessmentId": adir.name, "version": version,
                                    "path": _rel(bundle_path), "sha256": _file_sha(bundle_path)})
                state = str(bundle.get("stateCode") or "").upper()
                for fn in bundle.get("metricsByFunction") or []:
                    for m in fn.get("metrics") or []:
                        if m.get("curveLayers"):
                            layers = [(L.get("stratum") or "", L.get("points") or [])
                                      for L in m["curveLayers"]]
                        else:
                            c = m.get("curve") or {}
                            layers = [(c.get("stratification") or "", c.get("points") or [])]
                        for strat, pts in layers:
                            entry = {"assessmentId": adir.name, "version": version,
                                     "metricId": m.get("metricId"), "stratum": strat,
                                     "functionId": fn.get("functionId"),
                                     "points": [{"x": float(p["x"]), "y": float(p["y"])}
                                                for p in pts]}
                            self.layers.setdefault((state, m.get("metricId"), strat),
                                                   []).append(entry)
                            self.all_layers.append(entry)

    def find(self, state: str, metric_id: str, strat_label: str) -> list[dict]:
        return list(self.layers.get((state, metric_id, strat_label), []))


# --------------------------------------------------------------------------- #
# points, form, direction
# --------------------------------------------------------------------------- #
def detect_swap(bins: list[dict]) -> bool:
    """Field cells holding the SQT index levels while index cells hold field values."""
    fields = [b for b in bins if b["field"].strip()]
    if len(fields) < 2 or any(b["fieldNumber"] is None for b in fields):
        return False
    if not all(round(b["fieldNumber"], 6) in SQT_INDEX_LEVELS for b in fields):
        return False
    return any(b["indexNumber"] is not None and not 0.0 <= b["indexNumber"] <= 1.0
               for b in bins)


def source_points(bins: list[dict], swapped: bool) -> tuple[list[dict], list[dict], list[dict]]:
    """``(points, thresholds, extra)`` from the raw bins: numeric pairs (read the other way
    round when the columns are swapped), threshold bins, and breakpoints written in a bin
    description."""
    points, thresholds, extra = [], [], []
    for b in bins:
        x, y = (b["indexNumber"], b["fieldNumber"]) if swapped else (b["fieldNumber"],
                                                                     b["indexNumber"])
        if x is not None and y is not None:
            points.append({"x": x, "y": y, "bin": b["bin"]})
        elif b.get("fieldOp") and b["indexNumber"] is not None:
            thresholds.append({"bin": b["bin"], "op": b["fieldOp"]["op"],
                               "value": b["fieldOp"]["value"], "index": b["indexNumber"]})
        for bx, by in _EXTRA_BREAKPOINT.findall(b["desc"] or ""):
            extra.append({"x": float(bx), "y": float(by), "bin": b["bin"]})
    return points, thresholds, extra


def sort_points(points: list[dict]) -> list[dict]:
    """Ascending x (stable), duplicates of the same (x, y) kept once."""
    out, seen = [], set()
    for p in sorted(points, key=lambda p: p["x"]):
        k = (p["x"], p["y"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"x": float(p["x"]), "y": float(p["y"])})
    return out


def direction_changes(points: list[dict]) -> list[int]:
    """Signs of the non-flat steps in y along x."""
    signs = []
    for a, b in zip(points, points[1:]):
        d = b["y"] - a["y"]
        if d == 0:
            continue
        s = 1 if d > 0 else -1
        if not signs or signs[-1] != s:
            signs.append(s)
    return signs


def classify(bins: list[dict], points: list[dict], thresholds: list[dict]) -> tuple[str, str]:
    """``(form, direction)`` of a record."""
    texts = [b for b in bins if b["field"].strip() and b["fieldNumber"] is None
             and not b.get("fieldOp")]
    if texts:
        return "categorical", "unknown"
    signs = direction_changes(points)
    if len(points) < 2 or not signs:
        direction = "unknown"
    elif len(signs) == 1:
        direction = "increasing" if signs[0] > 0 else "decreasing"
    else:
        direction = "two-sided"
    if thresholds:
        return "threshold-table", direction
    if len(signs) >= 2:
        return "two-sided", direction
    return "piecewise", direction


def compile_like_adapted(bins: list[dict]) -> list[dict]:
    """What the metric library compiler makes of the raw bins (clamp, sort, placeholder):
    the adapted bundles should equal this."""
    pts = []
    for b in bins:
        x = _first_number(b["field"])
        y = _first_number(b["index"])
        if x is None or y is None:
            continue
        pts.append({"x": x, "y": max(0.0, min(1.0, y))})
    pts.sort(key=lambda p: p["x"])
    if len(pts) < 2:
        return [{"x": x, "y": y} for x, y in PLACEHOLDER_POINTS]
    return pts


def _first_number(text: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


# --------------------------------------------------------------------------- #
# originals: cells and layouts
# --------------------------------------------------------------------------- #
def parse_cell(value: Any) -> dict:
    """An original's value cell -> ``{"text", "tokens": [{"op", "value", "text"}],
    "range": bool, "categorical": bool}``."""
    if value is None:
        return {"text": "", "tokens": [], "range": False, "categorical": False}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        t = _num_text(value)
        return {"text": t, "tokens": [{"op": "=", "value": float(value), "text": t}],
                "range": False, "categorical": False}
    text = str(value)
    s = text.strip()
    if s.startswith("=") or s in ("", "-", "\u2013"):
        return {"text": s if not s.startswith("=") else "", "tokens": [], "range": False,
                "categorical": False}
    tokens, is_range, categorical = [], False, False
    for part in [p.strip() for p in s.splitlines() if p.strip()]:
        m = _RANGE.match(part)
        if m:
            is_range = True
            for g in (m.group(1), m.group(2)):
                tokens.append({"op": "=", "value": float(g), "text": g})
            continue
        m = _OP_NUMBER.match(part)
        if m:
            tokens.append({"op": m.group(1).replace(GE, ">=").replace(LE, "<="),
                           "value": float(m.group(2)), "text": m.group(2)})
            continue
        if _PLAIN_NUMBER.match(part):
            tokens.append({"op": "=", "value": float(part), "text": part})
            continue
        categorical = True
    return {"text": s, "tokens": tokens, "range": is_range, "categorical": categorical}


def _letter(col: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(col)


def _merged_origin(ws, row: int, col: int) -> tuple[Any, str]:
    """A cell's value, read from the top-left cell of its merged range, and that cell."""
    v = ws.cell(row=row, column=col).value
    if v is None:
        for rng in ws.merged_cells.ranges:
            if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
                return (ws.cell(row=rng.min_row, column=rng.min_col).value,
                        f"{_letter(rng.min_col)}{rng.min_row}")
    return v, f"{_letter(col)}{row}"


def _merged_value(ws, row: int, col: int):
    return _merged_origin(ws, row, col)[0]


def _formula_text(v: Any) -> str:
    text = getattr(v, "text", None)
    if text is not None:
        return str(text)
    return str(v) if isinstance(v, str) else ""


def build_limbs(levels: list[float], cells: list[dict]) -> dict:
    """Points of an original row from its level cells. Two values in one cell (or a range at
    the optimum) make it two-sided: the lower value belongs to the rising limb, the higher to
    the falling limb. Interior cells out of order with the row's ends are set aside."""
    two_sided = any(len(c["tokens"]) >= 2 for c in cells)
    rising, falling, single = [], [], []
    for lvl, c in zip(levels, cells):
        vals = sorted(t["value"] for t in c["tokens"])
        if not vals:
            continue
        if two_sided and len(vals) >= 2:
            rising.append((vals[0], lvl))
            falling.append((vals[-1], lvl))
        elif two_sided and lvl == max(levels):
            rising.append((vals[0], lvl))
            falling.append((vals[0], lvl))
        elif two_sided:
            rising.append((vals[0], lvl))
        else:
            single.append((vals[0], lvl))
    inconsistent = []

    def ordered(limb: list[tuple], rising_limb: bool) -> list[tuple]:
        xs = [x for x, _ in sorted(limb, key=lambda p: p[1])]
        ok = all((b >= a) if rising_limb else (b <= a) for a, b in zip(xs, xs[1:]))
        if ok or len(limb) <= 2:
            return limb
        by_level = sorted(limb, key=lambda p: p[1])
        inconsistent.extend(p[1] for p in by_level[1:-1])
        return [by_level[0], by_level[-1]]

    if two_sided:
        rising = ordered(rising, True)
        falling = ordered(falling, False)
        pts = rising + falling
    else:
        xs_by_level = sorted(single, key=lambda p: p[1])
        inc = all(b[0] >= a[0] for a, b in zip(xs_by_level, xs_by_level[1:]))
        dec = all(b[0] <= a[0] for a, b in zip(xs_by_level, xs_by_level[1:]))
        if not (inc or dec) and len(single) > 2:
            inconsistent.extend(p[1] for p in xs_by_level[1:-1])
            single = [xs_by_level[0], xs_by_level[-1]]
        pts = single
    points = sort_points([{"x": x, "y": y} for x, y in pts])
    return {"twoSided": two_sided, "points": points,
            "inconsistentLevels": sorted(set(inconsistent))}


def parse_performance_standards(ws, source: dict) -> list[dict]:
    """A 'list of metrics / performance standards' table: one row per metric and stratum,
    a value column per index level (header text such as ``Min (index = 0.00)``)."""
    level_cols: dict[int, float] = {}
    header_row = 0
    heads: dict[str, int] = {}
    for row in ws.iter_rows(min_row=1, max_row=8):
        for c in row:
            if not isinstance(c.value, str):
                continue
            text = " ".join(c.value.split())
            m = re.search(r"index\s*(?:=|" + GE + "|" + LE + r"|<=|>=|<|>)?\s*([01](?:\.\d+)?)",
                          text, re.I)
            if m:
                level_cols[c.column] = float(m.group(1))
                header_row = max(header_row, c.row)
            low = text.lower()
            if low.startswith("metric"):
                heads.setdefault("metric", c.column)
            elif low == "type":
                heads.setdefault("type", c.column)
            elif low == "description":
                heads.setdefault("desc", c.column)
            elif low.startswith("applicability"):
                heads.setdefault("limits", c.column)
            elif "notes" in low:
                heads.setdefault("notes", c.column)
    cols = sorted(level_cols)
    levels = [level_cols[c] for c in cols]
    tables = []
    for r in range(header_row + 1, ws.max_row + 1):
        cells = [parse_cell(ws.cell(row=r, column=c).value) for c in cols]
        if not any(c["tokens"] or c["categorical"] for c in cells):
            continue
        metric_text, title_cell = _merged_origin(ws, r, heads.get("metric", 3))
        metric_text = " ".join(str(metric_text or "").split())
        um = re.search(r"\(([^()]*)\)\s*$", metric_text)
        # a trailing parenthesis is the units unless it is an acronym such as (BHR)
        units = um.group(1).strip() if um and not re.fullmatch(r"[A-Z]{2,6}", um.group(1).strip()) \
            else None
        typ = _merged_value(ws, r, heads["type"]) if "type" in heads else None
        desc = ws.cell(row=r, column=heads["desc"]).value if "desc" in heads else None
        stratum = "Default"
        if typ is not None or desc is not None:
            stratum = f"{' '.join(str(typ or '').split())}: {' '.join(str(desc or '').split())}"
        limits = _merged_value(ws, r, heads["limits"]) if "limits" in heads else None
        notes = _merged_value(ws, r, heads["notes"]) if "notes" in heads else None
        limb = build_limbs(levels, cells)
        first, last = _letter(cols[0]), _letter(cols[-1])
        tables.append({
            "anchor": str(r), "row": r, "cells": f"{first}{r}:{last}{r}",
            "cellRefs": [f"{_letter(c)}{r}" for c in cols], "titleCell": title_cell,
            "title": metric_text, "stratum": stratum, "units": units,
            "levels": levels, "values": [c["text"] for c in cells], "parsed": cells,
            "categorical": any(c["categorical"] for c in cells),
            "twoSided": limb["twoSided"], "points": limb["points"],
            "inconsistentLevels": limb["inconsistentLevels"],
            "limits": " ".join(str(limits).split()) if limits else None,
            "notes": " ".join(str(notes).split()) if notes else None,
            "linearEvidence": bool(notes and "extrapolation" in str(notes).lower()),
            "logForm": False, "leastSquares": [],
        })
    return tables


def _slope_ranges(text: str) -> list[tuple[str, str]]:
    return re.findall(r"SLOPE\(\s*\$?([A-Z]+\$?\d+:\$?[A-Z]+\$?\d+)\s*,\s*"
                      r"\$?([A-Z]+\$?\d+:\$?[A-Z]+\$?\d+)\s*\)", text.replace("$", ""))


def parse_reference_curves(ws, source: dict) -> list[dict]:
    """A 'Reference Curves' sheet: blocks of a title, one or more field-value rows (an
    unlabeled row under a labeled one is a second limb) and an 'Index Value' row, with
    the segment equations below."""
    from openpyxl.utils import range_boundaries
    values: dict[tuple[int, int], Any] = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None:
                values[(c.row, c.column)] = c.value

    def numeric(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    tables = []
    for (r, c), v in sorted(values.items()):
        if not (isinstance(v, str) and " ".join(v.split()).lower() == "index value"):
            continue
        levels = [numeric(values.get((r, c + k))) for k in range(1, 7)]
        if sum(1 for x in levels if x is not None) < 2:
            continue
        rows = []
        rr = r - 1
        while rr >= 1:
            vals = [values.get((rr, c + k)) for k in range(1, 7)]
            if any(numeric(x) is not None for x in vals):
                rows.append(rr)
                rr -= 1
                continue
            break
        if not rows:
            continue
        title = values.get((rr, c))
        title = " ".join(str(title).split()) if isinstance(title, str) else ""
        rows.reverse()
        # coefficient labels and formulas under the index row
        below = [values.get((r2, c2)) for r2 in range(r + 1, r + 13) for c2 in range(c, c + 7)]
        texts = [_formula_text(x) for x in below]
        log_form = any("ln(" in t.lower() for t in texts)
        linear = any("SLOPE(" in t.upper() for t in texts) and not log_form
        slope_texts = [t for t in texts if "SLOPE(" in t.upper()]
        groups: list[list[int]] = []
        for rr2 in rows:
            label = values.get((rr2, c))
            if label is None and groups:
                groups[-1].append(rr2)
            else:
                groups.append([rr2])
        for g in groups:
            label = values.get((g[0], c))
            label = " ".join(str(label).split()) if isinstance(label, str) else ""
            stratum = "Default" if label.lower().replace(" ", "") == "fieldvalue" else label
            lvl = [x if x is not None else 0.0 for x in levels]
            limbs = []
            for rr2 in g:
                cells = [parse_cell(values.get((rr2, c + k))) for k in range(1, 7)]
                limbs.append(cells)
            if len(limbs) == 1:
                limb = build_limbs(lvl, limbs[0])
                pts, two = limb["points"], limb["twoSided"]
                inconsistent = limb["inconsistentLevels"]
            else:
                pts_all, inconsistent = [], []
                for cells in limbs:
                    one = build_limbs(lvl, cells)
                    pts_all.extend(one["points"])
                    inconsistent.extend(one["inconsistentLevels"])
                pts, two = sort_points(pts_all), True
            least = []
            for yr, xr in [p for t in slope_texts for p in _slope_ranges(t)]:
                try:
                    yc1, yr1, yc2, _ = range_boundaries(yr)
                    xc1, xr1, xc2, _ = range_boundaries(xr)
                except ValueError:
                    continue
                if not (g[0] <= xr1 <= g[-1]):
                    continue
                ys = [numeric(values.get((yr1, cc))) for cc in range(yc1, yc2 + 1)]
                xs = [numeric(values.get((xr1, cc))) for cc in range(xc1, xc2 + 1)]
                pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
                if len(pairs) > 2 and not _collinear(pairs):
                    least.append(f"{xr}")
            first_cell = f"{_letter(c + 1)}{g[0]}"
            tables.append({
                "anchor": first_cell, "row": g[0],
                "cells": ", ".join(f"{_letter(c + 1)}{x}:{_letter(c + 6)}{x}" for x in g),
                "cellRefs": [f"{_letter(c + k)}{g[0]}" for k in range(1, 7)],
                "title": title, "stratum": stratum, "units": None,
                "levels": lvl, "values": [parse_cell(values.get((g[0], c + k)))["text"]
                                          for k in range(1, 7)],
                "limbValues": [[parse_cell(values.get((rr2, c + k)))["text"]
                                for k in range(1, 7)] for rr2 in g],
                "parsed": [parse_cell(values.get((g[0], c + k))) for k in range(1, 7)],
                "categorical": False, "twoSided": two, "points": pts,
                "inconsistentLevels": sorted(set(inconsistent)),
                "limits": None, "notes": None, "linearEvidence": linear,
                "logForm": log_form, "leastSquares": least,
            })
    return tables


def _collinear(pairs: list[tuple[float, float]]) -> bool:
    (x0, y0), (x1, y1) = pairs[0], pairs[-1]
    if x1 == x0:
        return all(x == x0 for x, _ in pairs)
    slope = (y1 - y0) / (x1 - x0)
    return all(abs(y0 + slope * (x - x0) - y) <= 1e-9 for x, y in pairs)


def parse_manual(source: dict, file_display: str) -> list[dict]:
    """Owner transcriptions of a table in a PDF or report, with page and table."""
    tables = []
    for i, t in enumerate(source.get("tables") or []):
        levels = [float(x) for x in (t.get("levels") or DEFAULT_LEVELS)]
        cells = [parse_cell(v if v not in ("",) else None) for v in (t.get("values") or [])]
        cells += [parse_cell(None)] * (len(levels) - len(cells))
        limb = build_limbs(levels, cells[:len(levels)])
        where = ", ".join(x for x in (f"page {t['page']}" if t.get("page") else "",
                                      str(t.get("table") or "")) if x)
        tables.append({
            "anchor": f"table-{i + 1}", "row": None, "cells": where,
            "cellRefs": [where] * len(levels), "title": str(t.get("metric") or ""),
            "stratum": str(t.get("stratum") or "Default"), "units": t.get("units"),
            "levels": levels, "values": [c["text"] for c in cells[:len(levels)]],
            "parsed": cells[:len(levels)],
            "categorical": any(c["categorical"] for c in cells),
            "twoSided": limb["twoSided"], "points": limb["points"],
            "inconsistentLevels": limb["inconsistentLevels"],
            "limits": t.get("limits"), "notes": t.get("notes"),
            "linearEvidence": bool(t.get("linearSegments")), "logForm": False,
            "leastSquares": [], "page": t.get("page"), "table": t.get("table"),
            "match": {"metric": t.get("metric"), "stratum": t.get("stratum") or "Default"},
        })
    return tables


# --------------------------------------------------------------------------- #
# the originals on disk
# --------------------------------------------------------------------------- #
#: the two partial originals shipped with the app, described as a sources.json would.
#: ``match`` keys: sheet row (performance-standards) or anchor cell (reference-curves);
#: a null target marks a table that has no metric library record.
BUILTIN_SOURCES = [
    {
        "state": "MN",
        "id": "mn-sqt-v2.0-metric-list",
        "file": "MN-List-of-Metricsv2.0.xlsx",
        "tool": "Minnesota Stream Quantification Tool",
        "edition": "v2.0",
        "citation": "Minnesota SQT v2.0 list of metrics (MN-List-of-Metricsv2.0.xlsx)",
        "layout": "performance-standards",
        "sheet": "Performance Standards",
        "partial": True,
        "match": {
            "3": ["Land Use Coefficient", "Default"],
            "4": ["BMP MIDS Rv Coefficient", "Default"],
            "5": ["Concentrated Flow Points / 1,000 ft", "Default"],
            "6": ["Bank Height Ratio (BHR)", "Default"],
            "7": ["Entrenchment Ratio (ER)", "C Streams"],
            "8": ["Entrenchment Ratio (ER)", "B Streams"],
            "9": ["Entrenchment Ratio (ER)", "E Streams"],
            "10": ["LWD Index", "Default"],
            "11": ["LWD", "# Pieces / 100 meters"],
            "12": None,
            "13": ["Percent Streambank Erosion", "Default"],
            "14": ["Percent Armoring", "Default"],
            "15": None,
            "16": ["Pool Spacing Ratio", "A and B Stream Types"],
            "17": ["Pool Spacing Ratio", "Bc Stream Types"],
            "18": ["Pool Spacing Ratio", "C and E Streams"],
            "19": ["Pool Depth Ratio", "Default"],
            "20": ["Percent Riffle", "A and B Streams"],
            "21": ["Percent Riffle", "C and E Stream Types"],
            "22": ["Aggradation Ratio", "Default"],
            "23": ["Effective Vegetated Riparian Area", "Unconfined Alluvial Valleys"],
            "24": ["Effective Vegetated Riparian Area",
                   "Confined Alluvial or Colluvial/V-Shaped Valleys"],
            "25": ["Canopy Cover", "Woody vegetation is a natural component of riparian zone"],
            "26": ["Canopy Cover",
                   "Woody vegetation is not a natural component of riparian zone"],
            "27": ["Herbaceous Strata Vegetation Cover", "Default"],
            "28": ["Woody Stem Basal Area", "Default"],
            "29": ["Summer Average", "Default"],
            "30": ["DO", "2A"],
            "31": ["DO", "2B/2Bd"],
            "32": None,
            "33": ["TSS", "2A / -"],
            "34": ["TSS", "2B/2Bd - North"],
            "35": ["TSS", "2B/2Bd - Central"],
            "36": ["TSS", "2B/2Bd - South"],
            "37": ["Macroinvertebrate IBI", "Northern Forest Rivers - Northern"],
            "38": ["Macroinvertebrate IBI", "Northern Forest Streams Riffle-run - Northern"],
            "39": ["Macroinvertebrate IBI", "Northern Forest Streams Glide-pool - Northern"],
            "40": ["Macroinvertebrate IBI", "Northern Coldwater - Northern"],
            "41": ["Macroinvertebrate IBI", "Southern Forest Streams Riffle-run - Southern"],
            "42": ["Macroinvertebrate IBI", "Southern Forest Streams Glide-pool - Southern"],
            "43": ["Macroinvertebrate IBI", "Southern Coldwater - Southern"],
            "44": ["Macroinvertebrate IBI", "Prairie Forest Rivers - Prairie"],
            "45": ["Macroinvertebrate IBI", "Prairie Streams Glide-Pool - Prairie"],
            "46": ["Fish IBI", "Northern Rivers - Northern"],
            "47": ["Fish IBI", "Northern Streams - Northern"],
            "48": ["Fish IBI", "Northern Headwaters - Northern"],
            "49": ["Fish IBI", "Northern Coldwater - Northern"],
            "50": ["Fish IBI", "Southern River - Southern"],
            "51": ["Fish IBI", "Southern Streams - Southern"],
            "52": ["Fish IBI", "Southern Headwaters - Southern"],
            "53": ["Fish IBI", "Southern Coldwater - Southern"],
            "54": ["Fish IBI", "Low Gradient - Low Gradient"],
        },
    },
    {
        "state": "WI",
        "id": "wi-sqt-reference-curves",
        "file": "WISQT_Reference_Curves.xlsx",
        "tool": "Wisconsin Stream Quantification Tool",
        "edition": None,
        "citation": "Wisconsin SQT reference curves sheet (WISQT_Reference_Curves.xlsx)",
        "layout": "reference-curves",
        "sheet": "Reference_Curves",
        "partial": True,
        "match": {
            "C10": ["Land Use Coefficient", "Default"],
            "L10": ["Bank Height Ratio (BHR)", "Default"],
            "U10": ["LWD Index", "Default"],
            "AD10": ["Summer Mean Temperature", "Cold"],
            "AD11": ["Summer Mean Temperature", "Cold Transition"],
            "AD12": ["Summer Mean Temperature", "Warm Transition"],
            "AD13": ["Summer Mean Temperature", "Warm"],
            "AM10": ["mIBI", "Default"],
            "C44": ["Concentrated Flow Point Index", "Default"],
            "L44": ["Entrenchment Ratio (ER)", "C Stream Types"],
            "U44": ["LWD Frequency", "Default"],
            "AM45": ["fIBI", "Coldwater"],
            "AM46": ["fIBI", "Coolwater"],
            "AM47": ["fIBI", "Warmwater"],
            "AD46": ["Benthic Algal Biomass", "Field Value (Mean Score)"],
            "L77": ["Entrenchment Ratio (ER)", "E Stream Types"],
            "U79": ["Percent Streambank Erosion", "Default"],
            "AD79": ["Diatom Phosphorus Index (DPI)", "Default"],
            "AM85": ["Fish Abundance", "Adult Smallmouth Bass (Native)"],
            "AM86": ["Fish Abundance", "Adult and Yearling Brown Trout"],
            "AM87": ["Fish Abundance", "Adult and Yearling Brook Trout (Native)"],
            "AM88": ["Fish Abundance", "Lake Superior Trout YoY"],
            "AM89": ["Fish Abundance", "Lake Michigan Trout YoY"],
            "L112": ["Entrenchment Ratio (ER)", "B Stream Types"],
            "AD112": ["Hilsenhoff Biotic Index (HBI)", "Default"],
            "U114": ["Percent Streambank Armoring", "Default"],
            "L145": ["Width/Depth Ratio State", "Default"],
            "U149": ["Effective Vegetated Riparian Area", "Unconfined Alluvial"],
            "U150": ["Effective Vegetated Riparian Area", "Confined and Colluvial"],
            "U185": ["Canopy Cover", "Woody Reference Vegetation Cover"],
            "U186": ["Canopy Cover", "Herbaceous Reference Vegetation Cover"],
            "U222": ["Herbaceous Cover", "Default"],
            "U256": ["Woody Stem Basal Area", "Default"],
            "U291": ["Pool Spacing Ratio", "A and B Stream Types"],
            "U327": ["Pool Spacing Ratio", "Bc Stream Types"],
            "U363": ["Pool Spacing Ratio", "C and E Stream Types"],
            "U401": ["Pool Depth Ratio", "Default"],
            "U436": ["Percent Riffle", "A and B Stream Types"],
            "U472": ["Percent Riffle", "C and E Stream Types"],
            "U507": ["Percent Fines < 2mm", "Default"],
            "U542": ["Percent Fines <6.35mm", "Default"],
            "U577": ["Median Particle Size", "Default"],
        },
    },
]

LAYOUTS = ("performance-standards", "reference-curves", "manual")


def originals_root() -> Path:
    return Path(os.environ.get(ORIGINALS_ENV) or DEFAULT_ORIGINALS_ROOT)


def discover_sources(templates_dir: Path = TEMPLATES_DIR,
                     root: Optional[Path] = None) -> tuple[list[dict], list[dict]]:
    """``(sources, problems)``: the built-in partial originals, then each state folder's
    ``sources.json`` under the originals root (owner files are listed first so a full
    original outranks a partial one)."""
    sources, problems = [], []
    root = Path(root) if root is not None else originals_root()
    if root.exists():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            spec_path = folder / "sources.json"
            files = [p for p in folder.iterdir() if p.is_file() and p.name != "sources.json"]
            if not spec_path.exists():
                if files:
                    problems.append({"code": "original-without-sources-json",
                                     "state": folder.name.upper(),
                                     "detail": f"{len(files)} file(s) in {ORIGINALS_LABEL}/"
                                               f"{folder.name} but no sources.json naming "
                                               "their edition, citation and layout."})
                continue
            try:
                spec = _load_json(spec_path)
            except ValueError as e:
                problems.append({"code": "sources-json-unreadable", "state": folder.name.upper(),
                                 "detail": f"{ORIGINALS_LABEL}/{folder.name}/sources.json: {e}"})
                continue
            state = str(spec.get("state") or folder.name).upper()
            for s in spec.get("sources") or []:
                s = dict(s)
                s["state"] = state
                s["_path"] = folder / str(s.get("file") or "")
                s["_root"] = root
                s["origin"] = "owner"
                if s.get("layout") not in LAYOUTS:
                    problems.append({"code": "original-layout-unknown", "state": state,
                                     "detail": f"{s.get('file')}: layout {s.get('layout')!r} is "
                                               f"not one of {', '.join(LAYOUTS)}."})
                    continue
                if not s["_path"].is_file():
                    problems.append({"code": "original-missing", "state": state,
                                     "detail": f"sources.json names {s.get('file')}, which is "
                                               f"not in {ORIGINALS_LABEL}/{folder.name}."})
                    continue
                sources.append(s)
    for s in BUILTIN_SOURCES:
        s = dict(s)
        s["_path"] = Path(templates_dir) / s["file"]
        s["_root"] = None
        s["origin"] = "template"
        if s["_path"].is_file():
            sources.append(s)
        else:
            problems.append({"code": "original-missing", "state": s["state"],
                             "detail": f"{s['file']} is not in data/templates."})
    return sources, problems


#: categorical SQT inputs a workbook can list outside its curve tables (pull-down lists)
CATEGORICAL_INPUT_LABELS = ("dominant behi/nbs",)


def read_source_tables(source: dict) -> list[dict]:
    """The tables of one original. Also records, on ``source["_categoricalInputs"]``, any
    cell naming a categorical SQT input (such as a Dominant BEHI/NBS pull-down list)."""
    layout = source.get("layout")
    display = _rel(source["_path"], source.get("_root"))
    source["_categoricalInputs"] = []
    if layout == "manual":
        tables = parse_manual(source, display)
    else:
        import openpyxl
        # read-only skips the charts a reference-curves workbook carries; the performance
        # standards layout needs merged cells, which only a full load exposes
        read_only = layout == "reference-curves"
        wb = openpyxl.load_workbook(source["_path"], data_only=False, read_only=read_only)
        try:
            sheet = source.get("sheet")
            ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
            tables = (parse_performance_standards(ws, source)
                      if layout == "performance-standards" else parse_reference_curves(ws, source))
            for t in tables:
                t["sheet"] = ws.title
            # a curves sheet cannot show a categorical metric; its pull-down lists can
            for other in (wb.worksheets if layout == "reference-curves" else []):
                if other.title == ws.title:
                    continue
                for row in other.iter_rows():
                    for c in row:
                        v = getattr(c, "value", None)
                        if isinstance(v, str) and v.strip().rstrip(":").strip().lower() in \
                                CATEGORICAL_INPUT_LABELS:
                            source["_categoricalInputs"].append(
                                {"label": v.strip().rstrip(":").strip(),
                                 "cell": f"{other.title}!{c.coordinate}"})
        finally:
            if read_only:
                wb.close()
    for t in tables:
        t["file"] = display
        t["sourceId"] = source.get("id")
        t["state"] = source["state"]
        t["edition"] = source.get("edition")
        t["citation"] = source.get("citation")
        t["tool"] = source.get("tool")
        t["partialOriginal"] = bool(source.get("partial"))
        t["origin"] = source.get("origin")
    return tables


def _match_key(text: str) -> str:
    s = str(text or "").lower().replace("&", " and ")
    s = re.sub(r"\([^()]*\)", " ", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s)
             if w and w not in {"stream", "streams", "type", "types", "for", "the", "valley",
                                "valleys", "reference"}]
    return " ".join(words)


def auto_match(table: dict, candidates: list[dict]) -> Optional[dict]:
    """The one record of the table's state whose metric and stratum names agree with the
    table's title and stratum (the stratum's description after ``Type:`` also counts, and a
    record stratum may extend it, as ``Northern Rivers - Northern`` extends
    ``Northern Rivers``), or None when no record or more than one agrees."""
    title = _match_key(table.get("title"))
    raw = str(table.get("stratum") or "")
    wants = {_match_key(raw), _match_key(raw.rsplit(":", 1)[-1])} - {""}
    exact, extended = [], []
    for r in candidates:
        mk = _match_key(r["originalMetricName"])
        if not mk or not (title == mk or title.startswith(mk + " ")):
            continue
        rest = title[len(mk):].strip()
        options = {w for w in wants if w != "default"} or {rest or "default"}
        rk = _match_key(r["stratumName"])
        if rk in options:
            exact.append(r)
        elif any(rk.startswith(w + " ") for w in options):
            extended.append(r)
    if exact:
        return exact[0] if len(exact) == 1 else None
    return extended[0] if len(extended) == 1 else None


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def _tok_value(cell: dict, rising: Optional[bool] = None) -> Optional[dict]:
    toks = cell["tokens"]
    if not toks:
        return None
    if len(toks) == 1 or rising is None:
        return toks[0]
    return min(toks, key=lambda t: t["value"]) if rising else max(toks, key=lambda t: t["value"])


def compare_to_table(rec: dict, table: dict) -> dict:
    """Cell-by-cell and curve-level comparison of a record with one original table."""
    rows = []
    bins = rec["originalValues"]
    levels = table["levels"]
    rec_pts = rec["normalizedPoints"]
    rising = None
    if table["twoSided"] and rec["direction"] in ("increasing", "decreasing"):
        rising = rec["direction"] == "increasing"
    counts = {"exact": 0, "consistent": 0, "rounded": 0, "drift": 0, "mismatch": 0}
    for k, (lvl, cell) in enumerate(zip(levels, table["parsed"])):
        b = bins[k] if k < len(bins) else None
        ref = table["cellRefs"][k] if k < len(table["cellRefs"]) else ""
        skip = lvl in table.get("inconsistentLevels", [])
        tok = _tok_value(cell, rising)
        has_rec = b is not None and b["fieldNumber"] is not None and b["indexNumber"] is not None
        if tok is None and not has_rec:
            continue
        entry = {"index": lvl, "cell": ref, "original": " / ".join(cell["text"].splitlines()),
                 "record": b["field"] if b else ""}
        if tok is not None and len(cell["tokens"]) > 1:
            entry["originalValue"] = tok["text"]
        if skip:
            entry["result"] = "original-out-of-order"
        elif tok is not None and has_rec:
            res = compare_values(b["field"].strip(), tok["text"])
            if table["twoSided"] and rising is None and len(cell["tokens"]) > 1:
                res = min((compare_values(b["field"].strip(), t["text"]) for t in cell["tokens"]),
                          key=["exact", "consistent", "rounded", "drift", "mismatch"].index)
            entry["result"] = res
            counts[res] += 1
        elif tok is not None:
            x = tok["value"]
            ext = sr.evaluate(rec_pts, x, "unknown")
            if ext is None:
                entry["result"] = "outside-record"
            else:
                dy = abs(ext - lvl)
                entry["result"] = ("on-record-line" if dy <= sr.INDEX_TOLERANCE
                                   else "off-record-line")
                entry["recordIndexHere"] = round(ext, 4)
        else:
            y = (sr.evaluate(table["points"], b["fieldNumber"], "unknown")
                 if table["points"] else None)
            if y is not None and abs(y - b["indexNumber"]) <= sr.INDEX_TOLERANCE:
                entry["result"] = "on-original-line"
            else:
                entry["result"] = "not-in-original"
                counts["mismatch"] += 1
        rows.append(entry)
    return {"rows": rows, "counts": counts}


def _limb_words(points: list[dict]) -> str:
    return ", ".join(f"{sr._fmt(p['x'])} ({sr._fmt(p['y'])})" for p in points)


def verify(rec: dict, matched: list[dict], same_metric_tables: dict) -> dict:
    """The verification block and the issues it raises for one record."""
    issues: list[dict] = []
    if not matched:
        return {"verification": {"status": "unverified", "against": None,
                                 "reasons": ["STAF adaptation, not verified against the "
                                             "original SQT: no original on file for this "
                                             "curve."]},
                "issues": issues, "mislabelOf": None}
    table = matched[0]
    cmp = compare_to_table(rec, table)
    rows, counts = cmp["rows"], cmp["counts"]
    reasons: list[str] = []
    against = {
        "file": table["file"], "sourceId": table["sourceId"], "cells": table["cells"],
        "metricName": table["title"], "stratum": table["stratum"],
        "edition": table.get("edition"), "citation": table.get("citation"),
        "partialOriginal": table.get("partialOriginal", False),
    }
    if table.get("sheet"):
        against["sheet"] = table["sheet"]
    if table.get("page") is not None:
        against["page"] = table["page"]
    if table.get("table"):
        against["table"] = table["table"]
    status = "verified"
    defect = False

    mislabel_of = None
    # a mismatch that another stratum of the same metric explains is a mislabel
    if counts["mismatch"]:
        best = None
        for other in same_metric_tables.get((table["file"], table["title"]), []):
            if other is table:
                continue
            oc = compare_to_table(rec, other)
            agree = sum(oc["counts"][k] for k in ("exact", "consistent", "rounded", "drift"))
            total = agree + oc["counts"]["mismatch"]
            if total and agree / total >= 0.75 and (best is None or agree > best[1]):
                best = (other, agree, total, oc["rows"])
        mism = [r for r in rows if r.get("result") in ("mismatch", "not-in-original")]
        detail = "; ".join(f"{r['record'] or 'blank'} vs {r['original'] or 'blank'} at index "
                           f"{r['index']:g} ({r['cell']})" for r in mism)
        if best is not None:
            other, agree, total, orows = best
            odd = [r for r in orows if r.get("result") in ("drift", "mismatch")]
            odd_txt = ("; except " + "; ".join(f"{r['record']} vs {r['original']} ({r['cell']})"
                                               for r in odd)) if odd else ""
            wanted = ", ".join(r["original"] for r in rows if r.get("original"))
            issues.append({"code": "mislabelled-stratum", "severity": "defect",
                           "detail": f"Labelled {rec['stratumName']}, but its values are the "
                                     f"original's {other['stratum']} row ({other['cells']}: "
                                     f"{agree} of {total} agree{odd_txt}). The original's "
                                     f"{table['stratum']} values ({table['cells']}: {wanted}) "
                                     "are not in the metric library."})
            reasons.append(f"Values match {other['stratum']} ({other['cells']}), not "
                           f"{table['stratum']} ({table['cells']}).")
            mislabel_of = {"file": other["file"], "cells": other["cells"],
                           "stratum": other["stratum"]}
        else:
            issues.append({"code": "original-mismatch", "severity": "defect",
                           "detail": f"Differs from the original ({table['file']}, "
                                     f"{table['cells']}): {detail}."})
            reasons.append(f"Values differ from the original: {detail}.")
        defect = True

    if table["twoSided"] and rec["form"] != "two-sided":
        other_limb = [p for p in table["points"]
                      if sr.evaluate(rec["normalizedPoints"], p["x"], "unknown") is None]
        last = rec["normalizedPoints"][-1] if rec["direction"] == "increasing" \
            else rec["normalizedPoints"][0]
        side = "above" if rec["direction"] == "increasing" else "below"
        issues.append({"code": "two-sided-one-limb", "severity": "defect",
                       "detail": f"The original is two-sided: {_limb_words(table['points'])}. "
                                 f"This row holds one limb, so any value {side} "
                                 f"{sr._fmt(last['x'])} scores {sr._fmt(last['y'])}. Missing: "
                                 f"{_limb_words(other_limb) or 'the other limb'}."})
        reasons.append("The original is two-sided; the record holds one limb.")
        defect = True

    rounded = [r for r in rows if r.get("result") == "rounded"]
    drift = [r for r in rows if r.get("result") == "drift"]
    if rounded:
        issues.append({"code": "rounded-values", "severity": "warning",
                       "detail": "Values are the original's, rounded: " + "; ".join(
                           f"{r['record']} for {r['original']} ({r['cell']})" for r in rounded)
                                 + "."})
        reasons.append("Some values are the original's, rounded.")
        status = "partially-verified"
    if drift:
        note = ""
        if table.get("layout") == "performance-standards" or "list of metrics" in \
                str(table.get("citation") or "").lower():
            note = (" The list notes its values are calculated thresholds that may differ "
                    "slightly from the SQT's reference standards.")
        issues.append({"code": "value-drift", "severity": "warning",
                       "detail": "Values differ slightly from the original: " + "; ".join(
                           f"{r['record']} vs {r['original']} at index {r['index']:g} "
                           f"({r['cell']})" for r in drift) + "." + note})
        reasons.append("Some values differ slightly from the original.")
        status = "partially-verified"
    off = [r for r in rows if r.get("result") == "off-record-line"]
    outside = [r for r in rows if r.get("result") == "outside-record"]
    if off or (outside and not (table["twoSided"] and rec["form"] != "two-sided")):
        parts = [f"{r.get('originalValue', r['original'])} at index {r['index']:g} "
                 f"({r['cell']}), where the record's line gives {r['recordIndexHere']:g}"
                 for r in off]
        parts += [f"{r.get('originalValue', r['original'])} at index {r['index']:g} "
                  f"({r['cell']}), past the record's last point" for r in outside]
        issues.append({"code": "missing-breakpoints", "severity": "warning",
                       "detail": "The original has breakpoints this row lacks: "
                                 + "; ".join(parts) + "."})
        reasons.append("The original has breakpoints the record lacks.")
        status = "partially-verified"
    if table.get("inconsistentLevels"):
        bad = [r["cell"] for r in rows if r.get("result") == "original-out-of-order"]
        reasons.append("The original's cells " + ", ".join(bad) + " are out of order with "
                       "its end values (a copy error in the original); they were not compared.")
    if table.get("logForm"):
        issues.append({"code": "original-log-form", "severity": "warning",
                       "detail": "The original fits Y = a ln(X) + b through these points, so "
                                 "between and beyond them it is not the straight line drawn "
                                 "here."})
        reasons.append("The original is logarithmic between its points.")
        status = "partially-verified"
    if table.get("leastSquares"):
        issues.append({"code": "original-least-squares", "severity": "warning",
                       "detail": "The original fits a least-squares line through more than "
                                 "two points (" + ", ".join(table["leastSquares"]) + "), which "
                                 "need not pass through each point."})
        status = "partially-verified"
    consistent = [r for r in rows if r.get("result") == "consistent"]
    if consistent:
        reasons.append("Values match at the original's precision: " + "; ".join(
            f"{r['record']} for {r['original']} ({r['cell']})" for r in consistent) + ".")
    on_line = [r for r in rows if r.get("result") == "on-record-line"]
    if on_line:
        vals = ", ".join(f"{r.get('originalValue', r['original'])} ({r['cell']})"
                         for r in on_line)
        reasons.append(f"The original's {vals} {'lies' if len(on_line) == 1 else 'lie'} on "
                       "the record's line.")
    if defect:
        status = "defective"
    if status == "verified":
        reasons.insert(0, "Every value matches the original.")
    verification = {"status": status, "against": against, "reasons": reasons,
                    "comparison": rows,
                    "originalPoints": [dict(p) for p in table["points"]]}
    if len(matched) > 1:
        verification["alsoMatched"] = [{"file": t["file"], "cells": t["cells"]}
                                       for t in matched[1:]]
    return {"verification": verification, "issues": issues, "mislabelOf": mislabel_of}


def extrapolation_from(rec: dict, table: Optional[dict]) -> tuple[str, Optional[str]]:
    """``(extrapolation, basis)``: flat when the curve runs 0 to 1; linear when an original
    shows it; else unknown."""
    ends = sr.open_ends(rec)
    if len(rec["normalizedPoints"]) < 2:
        return "unknown", "The source gives fewer than two points."
    if not ends:
        return "flat", "The curve reaches 0 and 1; the index holds there."
    if table is None:
        return "unknown", ("The curve stops inside the index range and no original on file "
                           "says how the source scores past that point.")
    if table.get("logForm"):
        return "unknown", "The original is logarithmic past its points."
    pts = rec["normalizedPoints"]
    checked, agree = 0, 0
    for p in table["points"]:
        if pts[0]["x"] <= p["x"] <= pts[-1]["x"]:
            continue
        y = sr.evaluate(pts, p["x"], "linear")
        checked += 1
        if y is not None and abs(y - p["y"]) <= 0.02:
            agree += 1
        else:
            line_x = _linear_x_at(pts, p["y"], "low" if p["x"] < pts[0]["x"] else "high")
            if line_x is not None and abs(line_x - p["x"]) <= DRIFT_TOLERANCE * max(
                    abs(line_x), abs(p["x"])):
                agree += 1
    if checked:
        if agree == checked:
            return "linear", (f"The original's values past the record's ends lie on the linear "
                              f"extension of its end segments ({table['file']}).")
        return "unknown", "The original's values past the record's ends are not on a line."
    if table.get("linearEvidence"):
        return "linear", (f"The original scores with linear segment equations "
                          f"({table['file']}).")
    return "unknown", None


def _linear_x_at(pts: list[dict], y: float, end: str) -> Optional[float]:
    (ax, ay), (bx, by) = ((pts[0]["x"], pts[0]["y"]), (pts[1]["x"], pts[1]["y"])) \
        if end == "low" else ((pts[-2]["x"], pts[-2]["y"]), (pts[-1]["x"], pts[-1]["y"]))
    if by == ay:
        return None
    return ax + (y - ay) * (bx - ax) / (by - ay)


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #
def _issue(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail}


def _pts_words(points: list[dict]) -> str:
    return " ".join(f"({sr._fmt(p['x'])}, {sr._fmt(p['y'])})" for p in points)


def build_record(lib: MetricLibrary, row_index: int, group: int, compiled: CompiledLibrary,
                 functions: Functions, editions: dict, csv_display: str) -> dict:
    row = lib.rows[row_index]
    cells = lib.group_cells(row, group)
    label = cells[0]
    code = _state_from_label(label)
    state_name = STATE_NAMES[code]
    ident = lib.row_identity(row)
    csv_row = lib.csv_row(row_index)
    metric = ident["Metric Name"].strip()
    sname = stratum_name(label)
    bins = parse_bins(cells)
    issues: list[dict] = []

    swapped = detect_swap(bins)
    raw_points, thresholds, extra = source_points(bins, swapped)
    points = sort_points(raw_points + extra)
    form, direction = classify(bins, points, thresholds)

    metric_id = compiled.metric_by_row.get(csv_row)
    citation = f"{state_name} SQT"
    layer = compiled.layer(metric_id, citation, compiled_strat_label(label)) if metric_id else None
    if layer is not None:
        citation = (layer.get("sourceMetadata") or {}).get("sourceCitation") or citation
    fn = functions.resolve(ident["Mapped Function"])
    detail = compiled.detail.get(metric_id) or {}

    # edition
    hint = editions.get(code) or {}
    if len(hint.get("hints") or []) == 1:
        edition = hint["hints"][0]
        rows_txt = ", ".join(str(r) for r in hint["rows"][:6]) + \
            (", ..." if len(hint["rows"]) > 6 else "")
        edition_basis = (f"State hint in the metric library References column (rows "
                         f"{rows_txt}); the curve's own row names no edition.")
    else:
        edition, edition_basis = None, None
        found = hint.get("hints") or []
        issues.append(_issue("edition-unknown", "warning",
                             f"No edition is named for the {state_name} SQT in the metric "
                             "library" + (f" (hints disagree: {', '.join(found)})" if found
                                          else "") + "."))

    # geography and strata
    region, basis = None, None
    if code == "AK" and re.search(r"\bInterior\b", metric + " " + sname):
        region, basis = "Interior Alaska", "The metric or stratum names Interior Alaska."
    elif _REGION_WORDS.search(sname):
        region, basis = sname, "The stratum names a region."
    stream_types = parse_stream_types(sname)
    if stream_types == {"types": [], "except": []}:
        stream_types = None  # resolved against sibling strata after all records exist

    record = {
        "key": f"sqt:{code.lower()}:{slugify(metric)}:{slugify(sname)}",
        "state": code,
        "stateName": state_name,
        "tool": f"{state_name} Stream Quantification Tool",
        "edition": edition,
        "editionBasis": edition_basis,
        "citation": citation,
        "originalMetricName": metric,
        "stafMetricId": metric_id,
        "function": {
            "id": fn["id"] if fn else None,
            "name": fn["name"] if fn else None,
            "discipline": ident["Discipline"] or detail.get("discipline"),
            "mappedFunction": ident["Mapped Function"],
        },
        "protocol": None,
        "units": None,
        "unitsBasis": None,
        "stratum": label,
        "stratumName": sname,
        "streamTypes": stream_types,
        "geography": {"state": code, "region": region, "basis": basis},
        "form": form,
        "direction": direction,
        "source": {
            "file": csv_display, "csvRow": csv_row, "stratification": group,
            "sourceColumn": ident["Source"], "type": cells[1], "indexAsRange": cells[2],
            "unit": cells[3],
        },
        "originalValues": bins,
        "thresholds": thresholds,
        "normalizedPoints": points,
        "extrapolation": "unknown",
        "extrapolationBasis": None,
        "scoreScale": dict(sr.SQT_SCORE_SCALE, bands=dict(sr.SQT_SCORE_SCALE["bands"])),
        "sourceLimits": [],
        "fingerprints": {
            "sourceRow": sr.fingerprint({"row": ident, "stratification": cells}),
            "points": sr.points_fingerprint(points),
        },
        "adaptedIn": [],
    }

    # record-intrinsic checks
    if swapped:
        idx = ", ".join(b["index"] for b in bins if b["indexNumber"] is not None
                        and not 0 <= b["indexNumber"] <= 1)
        issues.append(_issue("swapped-columns", "defect",
                             "The field-value cells hold the SQT index levels and the index "
                             f"cells hold field values ({idx}). The points here read the "
                             "columns the other way round; confirm against the original."))
    elif any(b["indexNumber"] is not None and not 0.0 <= b["indexNumber"] <= 1.0 for b in bins
             if b["fieldNumber"] is not None):
        issues.append(_issue("index-out-of-range", "defect",
                             "An index value lies outside 0 to 1."))
    if len(points) < 2 and form != "categorical":
        issues.append(_issue("insufficient-points", "defect",
                             f"The row gives {len(points)} usable breakpoint(s) for this "
                             "stratum; a curve needs two. The other bins hold only the "
                             "index template."))
    elif len(points) >= 2 and len({p["y"] for p in points}) == 1:
        issues.append(_issue("constant-index", "defect",
                             f"Every point scores {sr._fmt(points[0]['y'])}."))
    if form == "two-sided" and len(direction_changes(points)) > 2:
        issues.append(_issue("non-monotone", "warning",
                             "The points change direction more than once."))
    for t in thresholds:
        issues.append(_issue("threshold-bin", "warning",
                             f"Bin {t['bin']} ('{bins[t['bin'] - 1]['field']}') is a threshold, "
                             f"not a breakpoint: values {t['op']} {sr._fmt(t['value'])} score "
                             f"{sr._fmt(t['index'])}."))
    for e in extra:
        issues.append(_issue("extra-breakpoint", "note",
                             f"Bin {e['bin']}'s description adds a breakpoint "
                             f"({sr._fmt(e['x'])}, {sr._fmt(e['y'])}); it is in the points."))
    covered = {t["index"] for t in thresholds}      # a threshold bin scores that end
    if len(points) >= 2:
        if min(p["y"] for p in points) > 0 and 0.0 not in covered:
            lo = min(points, key=lambda p: p["y"])
            issues.append(_issue("does-not-reach-0", "warning",
                                 f"The lowest index in the row is {sr._fmt(lo['y'])} (at "
                                 f"{sr._fmt(lo['x'])}); the source's scoring below it is not "
                                 "recorded here."))
        if max(p["y"] for p in points) < 1 and 1.0 not in covered:
            hi = max(points, key=lambda p: p["y"])
            issues.append(_issue("does-not-reach-1", "warning",
                                 f"The highest index in the row is {sr._fmt(hi['y'])} (at "
                                 f"{sr._fmt(hi['x'])}); the source's scoring above it is not "
                                 "recorded here."))
    if "?" in metric:
        issues.append(_issue("name-garbled", "note",
                             f"The metric name '{metric}' carries a '?', likely a character "
                             "lost in an earlier export."))
    if region == "Interior Alaska":
        issues.append(_issue("interior-alaska-labelled-statewide", "warning",
                             "This curve is for Interior Alaska, but the AK SQT Adapted "
                             "assessment applies the Alaska SQT to all 'Alaska streams'."))
    elif code == "AK" and region is None:
        issues.append(_issue("alaska-scope-unconfirmed", "note",
                             "The metric library gives no geographic scope for the Alaska "
                             "SQT. Its regional strata and its biotic index are all Interior "
                             "Alaska, so statewide use of this curve is unconfirmed."))
    elif region:
        issues.append(_issue("sub-state-region", "note",
                             f"The stratum names a region within {state_name}: {region}."))
    record["_issues"] = issues
    record["_swapped"] = swapped
    record["_extra"] = extra
    return record


def two_sided_check(rec: dict) -> Optional[dict]:
    spec = TWO_SIDED_CONSTRUCTS.get(rec.get("stafMetricId") or "")
    if not spec or rec["form"] == "two-sided" or len(rec["normalizedPoints"]) < 2:
        return None
    if spec["types"] is not None:
        st = rec.get("streamTypes") or {}
        types = set(st.get("types") or [])
        if not types or not (types & spec["types"]):
            return None
    last = rec["normalizedPoints"][-1] if rec["direction"] == "increasing" \
        else rec["normalizedPoints"][0]
    side = "above" if rec["direction"] == "increasing" else "below"
    plateau = " The row marks the end of the optimum but gives no falling limb." \
        if rec.get("_extra") else ""
    return _issue("two-sided-one-limb", "defect",
                  f"{spec['name']} rises to an optimum and falls again ({spec['evidence']}). "
                  f"This row holds one limb, so any value {side} {sr._fmt(last['x'])} scores "
                  f"{sr._fmt(last['y'])}.{plateau} Not checked against this state's original.")


def compare_adapted(rec: dict, bins: list[dict], adapted: AdaptedBundles) -> list[dict]:
    """The adapted bundle layers that carry this curve, and how they differ from the raw."""
    out = []
    strat = compiled_strat_label(rec["stratum"])
    expected = compile_like_adapted(bins)
    for layer in adapted.find(rec["state"], rec["stafMetricId"], strat):
        pts = layer["points"]
        diffs = []
        if pts == [{"x": x, "y": y} for x, y in PLACEHOLDER_POINTS] and \
                len(rec["normalizedPoints"]) < 2:
            diffs.append("placeholder")
        if pts != expected:
            diffs.append("differs-from-compile")
        clamped = [b for b in bins if _first_number(b["index"]) is not None
                   and _first_number(b["field"]) is not None
                   and not 0 <= _first_number(b["index"]) <= 1]
        if clamped:
            diffs.append("clamped")
        if rec.get("_swapped"):
            diffs.append("read-swapped-columns")
        for e in rec.get("_extra") or []:
            if not any(p["x"] == e["x"] and p["y"] == e["y"] for p in pts):
                diffs.append("dropped-extra-breakpoint")
                break
        if rec["thresholds"]:
            diffs.append("threshold-read-as-point")
        if pts and len(pts) >= 2 and (0 < pts[0]["y"] < 1 or 0 < pts[-1]["y"] < 1):
            diffs.append("held-flat-past-open-end")
        out.append({"assessmentId": layer["assessmentId"], "version": layer["version"],
                    "metricId": layer["metricId"], "stratum": layer["stratum"],
                    "points": pts, "pointsEqualRaw": pts == rec["normalizedPoints"],
                    "differences": sorted(set(diffs))})
    return out


def adapted_issues(rec: dict, bins: list[dict]) -> list[dict]:
    issues = []
    for a in rec["adaptedIn"]:
        where = f"{a['assessmentId']} v{a['version']}"
        d = set(a["differences"])
        if "placeholder" in d:
            issues.append(_issue("adapted-placeholder", "warning",
                                 f"{where} scores this stratum on a generic placeholder curve "
                                 f"{_pts_words(a['points'])}, not on the SQT."))
        if "clamped" in d:
            vals = ", ".join(b["index"] for b in bins if _first_number(b["index"]) is not None
                             and not 0 <= _first_number(b["index"]) <= 1)
            issues.append(_issue("adapted-clamped", "warning",
                                 f"{where} clamps index values {vals} to 1.0, so its curve "
                                 f"{_pts_words(a['points'])} is flat."))
        if "dropped-extra-breakpoint" in d:
            issues.append(_issue("adapted-drops-breakpoint", "warning",
                                 f"{where} drops the breakpoint written in the bin description."))
        if "threshold-read-as-point" in d:
            issues.append(_issue("adapted-threshold-as-point", "warning",
                                 f"{where} reads the threshold bin as a breakpoint "
                                 f"({_pts_words(a['points'])}); DEEP takes the later of two "
                                 "points at the same value, so the curve ends at "
                                 f"{sr._fmt(a['points'][-1]['y'])} instead of stepping to "
                                 f"{sr._fmt(rec['thresholds'][0]['index'])}."))
        if "held-flat-past-open-end" in d:
            ends = []
            if 0 < a["points"][0]["y"] < 1:
                ends.append(f"{sr._fmt(a['points'][0]['y'])} below {sr._fmt(a['points'][0]['x'])}")
            if 0 < a["points"][-1]["y"] < 1:
                ends.append(f"{sr._fmt(a['points'][-1]['y'])} above "
                            f"{sr._fmt(a['points'][-1]['x'])}")
            issues.append(_issue("adapted-held-flat", "warning",
                                 f"{where} stops inside the index range and DEEP holds the end "
                                 f"value past it ({'; '.join(ends)}), so it can never score "
                                 "the rest of the range there."))
        if "differs-from-compile" in d:
            issues.append(_issue("adapted-drift", "warning",
                                 f"{where} does not match what the metric library compiler "
                                 f"makes of this row today: {_pts_words(a['points'])}."))
    if not rec["adaptedIn"]:
        issues.append(_issue("not-in-adapted", "note",
                             "No adapted SQT assessment carries this curve."))
    return issues


# --------------------------------------------------------------------------- #
# the build
# --------------------------------------------------------------------------- #
def build(csv_path: Optional[Path] = None, *, library_dir: Path = METRIC_LIBRARY_DIR,
          assessments_dir: Path = ASSESSMENTS_DIR, templates_dir: Path = TEMPLATES_DIR,
          originals: Optional[Path] = None, functions_path: Path = FUNCTIONS_PATH) -> dict:
    csv_path = Path(csv_path) if csv_path else find_metric_library_csv(library_dir)
    lib = MetricLibrary(csv_path)
    compiled = CompiledLibrary(library_dir)
    functions = Functions(functions_path)
    adapted = AdaptedBundles(assessments_dir)
    editions = edition_hints(lib)
    csv_display = _rel(csv_path)
    root = Path(originals) if originals is not None else originals_root()

    recs = []
    for i, row in enumerate(lib.rows):
        for g in lib.groups:
            cells = lib.group_cells(row, g)
            if not any(c.strip() for c in cells) or not _state_from_label(cells[0]):
                continue
            recs.append(build_record(lib, i, g, compiled, functions, editions, csv_display))

    keys = [r["key"] for r in recs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"duplicate registry keys: {', '.join(dupes)}")

    # 'All other Streams': every stream type the sibling strata do not name
    for r in recs:
        if re.search(r"\bAll other Streams\b", r["stratumName"], re.I):
            siblings = [s for s in recs if s["state"] == r["state"] and
                        s["stafMetricId"] == r["stafMetricId"] and s is not r]
            named = sorted({t for s in siblings for t in (s.get("streamTypes") or {})
                            .get("types", [])})
            r["streamTypes"] = {"types": [], "except": named}

    # originals
    sources, problems = discover_sources(templates_dir, root)
    by_state: dict[str, list[dict]] = {}
    for r in recs:
        by_state.setdefault(r["state"], []).append(r)
    lookup = {(r["state"], r["originalMetricName"], r["stratumName"]): r for r in recs}
    matched: dict[str, list[dict]] = {}
    inputs_originals, unmatched_tables = [], []
    same_metric: dict[tuple, list[dict]] = {}
    for s in sources:
        try:
            tables = read_source_tables(s)
        except Exception as e:  # noqa: BLE001 - an unreadable owner file is a finding
            problems.append({"code": "original-unreadable", "state": s["state"],
                             "detail": f"{_rel(s['_path'], s.get('_root'))} could not be read "
                                       f"({type(e).__name__}: {e})."})
            continue
        inputs_originals.append({
            "state": s["state"], "id": s.get("id"), "file": _rel(s["_path"], s.get("_root")),
            "sha256": _file_sha(s["_path"]), "layout": s.get("layout"),
            "sheet": s.get("sheet"), "edition": s.get("edition"),
            "citation": s.get("citation"), "partial": bool(s.get("partial")),
            "origin": s.get("origin"), "tables": len(tables),
        })
        for t in tables:
            t["layout"] = s.get("layout")
            same_metric.setdefault((t["file"], t["title"]), []).append(t)
        mapping = s.get("match") or {}
        for t in tables:
            target = None
            if t.get("match"):
                m = t["match"]
                target = lookup.get((s["state"], m.get("metric"), m.get("stratum") or "Default"))
            elif t["anchor"] in mapping:
                m = mapping[t["anchor"]]
                if m is None:
                    unmatched_tables.append((s, t, "no metric library record"))
                    continue
                if isinstance(m, dict):
                    m = [m.get("metric"), m.get("stratum") or "Default"]
                target = lookup.get((s["state"], m[0], m[1]))
            else:
                target = auto_match(t, by_state.get(s["state"], []))
            if target is None:
                unmatched_tables.append((s, t, "no record matched"))
                continue
            matched.setdefault(target["key"], []).append(t)

    mislabels: dict[tuple, str] = {}
    for r in recs:
        tables = matched.get(r["key"], [])
        table = tables[0] if tables else None
        bins = r["originalValues"]
        v = verify(r, tables, same_metric)
        if v.get("mislabelOf"):
            mislabels[(v["mislabelOf"]["file"], v["mislabelOf"]["cells"])] = r["key"]
        issues = r.pop("_issues")
        issues.extend(v["issues"])
        if table is not None:
            if table.get("units"):
                r["units"] = table["units"]
                where = table.get("titleCell") or table["cells"]
                r["unitsBasis"] = f"{table['file']} ({where}: {table['title']})"
            if table.get("limits"):
                r["sourceLimits"] = [table["limits"]]
        if r["units"] is None:
            issues.append(_issue("units-not-stated", "warning",
                                 "The metric library leaves the units blank for this curve"
                                 + (" and the matched original states none." if table else ".")))
        ext, ext_basis = extrapolation_from(r, table)
        r["extrapolation"], r["extrapolationBasis"] = ext, ext_basis
        if not any(i["code"] == "two-sided-one-limb" for i in issues):
            ts = two_sided_check(r)
            if ts:
                issues.append(ts)
        if r["form"] == "two-sided" and r["stafMetricId"] not in TWO_SIDED_CONSTRUCTS \
                and table is None:
            issues.append(_issue("two-sided-unconfirmed", "note",
                                 "The row's points rise to an optimum and fall again "
                                 f"({_pts_words(r['normalizedPoints'])}); no original on file "
                                 "confirms that shape."))
        r["adaptedIn"] = compare_adapted(r, bins, adapted)
        issues.extend(adapted_issues(r, bins))
        r.pop("_swapped", None)
        r.pop("_extra", None)
        order = {"defect": 0, "warning": 1, "note": 2}
        issues.sort(key=lambda i: (order[i["severity"]], i["code"], i["detail"]))
        r["issues"] = issues
        r["eligible"] = not any(i["severity"] == "defect" for i in issues)
        vstat = v["verification"]
        if not r["eligible"]:
            vstat["status"] = "defective"
            if table is None:
                vstat["reasons"] = ["Defect in the metric library row: " + ", ".join(
                    sorted({i["code"] for i in issues if i["severity"] == "defect"})) + "."]
        r["verification"] = vstat

    recs.sort(key=lambda r: r["key"])
    findings = build_findings(lib, recs, adapted, sources, unmatched_tables, problems,
                              mislabels)
    doc = {
        "schemaVersion": sr.SCHEMA_VERSION,
        "generator": "apps/stream-curves/scripts/build_sqt_registry.py",
        "description": ("Published state SQT curves as candidate curve sources: one record per "
                        "state x metric x stratum, from the metric library's raw bins, checked "
                        "against the adapted SQT assessments and any originals on disk."),
        "scoreScales": {"sqt": sr.SQT_SCORE_SCALE, "staf": sr.STAF_SCORE_SCALE},
        "inputs": {
            "metricLibraryCsv": {"file": csv_display, "sha256": _file_sha(csv_path)},
            "adaptedBundles": adapted.inputs,
            "originals": sorted(inputs_originals, key=lambda o: (o["state"], o["file"])),
        },
        "summary": summarize(recs, adapted),
        "findings": findings,
        "records": recs,
    }
    return doc


def _table_kind(t: dict) -> str:
    """categorical, threshold-table (one value scored two ways: a step) or curve."""
    if t.get("categorical"):
        return "categorical"
    seen: dict[float, set] = {}
    for lvl, c in zip(t["levels"], t["parsed"]):
        for tok in c["tokens"]:
            seen.setdefault(tok["value"], set()).add(lvl)
    return "threshold-table" if any(len(v) > 1 for v in seen.values()) else "curve"


_STATE_SQT = re.compile(r"(" + "|".join(sorted(map(re.escape, STATE_CODES), key=len,
                                               reverse=True)) + r") SQT")


def build_findings(lib: MetricLibrary, recs: list[dict], adapted: AdaptedBundles,
                   sources: list[dict], unmatched: list, problems: list[dict],
                   mislabels: Optional[dict] = None) -> list[dict]:
    out = []
    mislabels = mislabels or {}
    # SQT-attributed rows the registry has no curve for (screening tier, categorical bins)
    for i, row in enumerate(lib.rows):
        src = lib.cell(row, "Source")
        if "SQT" not in src:
            continue
        if any(_state_from_label(lib.group_cells(row, g)[0]) for g in lib.groups):
            continue
        states = sorted({STATE_CODES[s] for s in _STATE_SQT.findall(src)})
        types = sorted({lib.group_cells(row, g)[1] for g in lib.groups
                        if lib.group_cells(row, g)[1].strip()})
        out.append({
            "code": "screening-categorical-not-carried",
            "states": states,
            "csvRow": lib.csv_row(i),
            "metric": lib.cell(row, "Metric Name"),
            "tier": lib.cell(row, "Recommended Tiers"),
            "detail": (f"CSV row {lib.csv_row(i)} ({lib.cell(row, 'Metric Name')}, "
                       f"{lib.cell(row, 'Recommended Tiers')} tier) cites {src}. It is scored "
                       f"on {', '.join(types).lower() or 'untyped'} bins with STAF screening "
                       "ranges (0 to 0.39, 0.40 to 0.69, 0.70 to 1.0), not on an SQT curve, and "
                       "no adapted SQT assessment carries it."),
        })
    # original tables with no record
    words = {"categorical": "a categorical metric", "threshold-table": "a threshold table",
             "curve": "a curve"}
    for s, t, why in unmatched:
        kind = _table_kind(t)
        code = "categorical-not-carried" if kind != "curve" else "original-table-without-record"
        key = mislabels.get((t["file"], t["cells"]))
        head = f"{t['file']} {t['cells']} ({t['title']}; {t['stratum']}) is {words[kind]}"
        if key:
            detail = (f"{head} with no record of its own; its values are in the metric "
                      f"library under another label ({key}).")
        elif why == "no metric library record":
            detail = f"{head} that the metric library does not carry, so the adaptation dropped it."
        else:
            detail = (f"{head} that matched no record by name; map it in the source's "
                      "sources.json if the metric library carries it.")
        out.append({
            "code": code, "states": [s["state"]], "file": t["file"], "cells": t["cells"],
            "metric": t["title"], "stratum": t["stratum"], "form": kind, "detail": detail,
        })
    # categorical inputs a workbook lists outside its curve tables (pull-down lists)
    for s in sources:
        for ci in s.get("_categoricalInputs") or []:
            file = _rel(s["_path"], s.get("_root"))
            out.append({
                "code": "categorical-not-carried", "states": [s["state"]], "file": file,
                "cells": ci["cell"], "metric": ci["label"], "form": "categorical",
                "detail": (f"{file} lists {ci['label']} choices ({ci['cell']}), a categorical "
                           "SQT input with no curve; the metric library has no record for it."),
            })
    # cells under columns without a header (past the last named stratification group)
    unnamed = [i for i, h in enumerate(lib.header) if not h.strip()]
    for i, row in enumerate(lib.rows):
        vals = [row[c] for c in unnamed if c < len(row) and row[c].strip()]
        if vals:
            out.append({
                "code": "unnamed-columns", "csvRow": lib.csv_row(i),
                "detail": (f"CSV row {lib.csv_row(i)} ({lib.cell(row, 'Metric Name')}) fills "
                           f"{len(vals)} of the {len(unnamed)} columns that have no header "
                           f"(after Stratification {lib.groups[-1]}) with "
                           f"{', '.join(repr(v) for v in sorted(set(vals)))}; they are not an "
                           "SQT stratum and neither the compiler nor this registry reads them."),
            })
    # states cited without curves
    cited = set()
    for row in lib.rows:
        for name in _STATE_SQT.findall(lib.cell(row, "Source")):
            cited.add(STATE_CODES[name])
    with_curves = {r["state"] for r in recs}
    for code in sorted(cited - with_curves):
        out.append({"code": "state-cited-without-curves", "states": [code],
                    "detail": (f"The {STATE_NAMES[code]} SQT is cited in the metric library's "
                               "Source column, but no stratum carries a curve from it.")})
    # adapted layers no record accounts for
    covered = {(a["assessmentId"], a["version"], a["metricId"], a["stratum"])
               for r in recs for a in r["adaptedIn"]}
    for L in adapted.all_layers:
        if (L["assessmentId"], L["version"], L["metricId"], L["stratum"]) not in covered:
            out.append({"code": "adapted-layer-without-record",
                        "assessmentId": L["assessmentId"], "version": L["version"],
                        "detail": f"{L['assessmentId']} v{L['version']} {L['metricId']} "
                                  f"{L['stratum'] or 'default'} matches no metric library row."})
    out.extend(problems)
    return out


def summarize(recs: list[dict], adapted: AdaptedBundles) -> dict:
    by_state: dict[str, dict] = {}
    by_status: dict[str, int] = {s: 0 for s in sr.VERIFICATION_STATUSES}
    issue_counts: dict[str, int] = {}
    for r in recs:
        s = by_state.setdefault(r["state"], {"records": 0, "eligible": 0,
                                             **{k: 0 for k in sr.VERIFICATION_STATUSES}})
        s["records"] += 1
        s["eligible"] += int(r["eligible"])
        s[r["verification"]["status"]] += 1
        by_status[r["verification"]["status"]] += 1
        for code in {i["code"] for i in r["issues"]}:
            issue_counts[code] = issue_counts.get(code, 0) + 1
    layers = adapted.all_layers
    return {
        "records": len(recs),
        "eligible": sum(int(r["eligible"]) for r in recs),
        "byState": by_state,
        "byVerification": by_status,
        "recordsWithIssue": dict(sorted(issue_counts.items())),
        "forms": _count(r["form"] for r in recs),
        "extrapolation": _count(r["extrapolation"] for r in recs),
        "adapted": {
            "layers": len(layers),
            "layersNeverReach0": sum(1 for L in layers if L["points"] and
                                     min(p["y"] for p in L["points"]) > 0),
            "layersNeverReach1": sum(1 for L in layers if L["points"] and
                                     max(p["y"] for p in L["points"]) < 1),
            "recordsCarried": sum(1 for r in recs if r["adaptedIn"]),
            "recordsEqualRaw": sum(1 for r in recs if r["adaptedIn"] and
                                   all(a["pointsEqualRaw"] for a in r["adaptedIn"])),
        },
    }


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def list_originals(templates_dir: Path = TEMPLATES_DIR, root: Optional[Path] = None) -> int:
    sources, problems = discover_sources(templates_dir, root)
    for s in sources:
        print(f"{s['state']} {_rel(s['_path'], s.get('_root'))} ({s.get('layout')}, "
              f"sheet {s.get('sheet')})")
        mapping = s.get("match") or {}
        try:
            tables = read_source_tables(s)
        except Exception as e:  # noqa: BLE001
            print(f"  ! could not read: {type(e).__name__}: {e}")
            continue
        for t in tables:
            m = t.get("match") or mapping.get(t["anchor"], "auto (by name)")
            vals = " | ".join(" / ".join(v.splitlines()) or "-" for v in t["values"])
            print(f"  {t['anchor']:>7}  {t['title'][:40]:40s} {t['stratum'][:34]:34s} "
                  f"[{vals}] -> {m}")
    for p in problems:
        print(f"  ! {p['detail']}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="compare the committed registry with a fresh build; write nothing")
    ap.add_argument("--list-originals", action="store_true",
                    help="print the tables read from each original and what they match")
    ap.add_argument("--originals", type=Path, default=None,
                    help=f"originals root (default: ${ORIGINALS_ENV} or "
                         f"{DEFAULT_ORIGINALS_ROOT.as_posix()})")
    ap.add_argument("--out", type=Path, default=REGISTRY_PATH, help="registry file to write "
                                                                     "or check")
    a = ap.parse_args(argv)
    if a.list_originals:
        return list_originals(TEMPLATES_DIR, a.originals)
    text = render(build(originals=a.originals))
    if a.check:
        current = a.out.read_bytes().decode("utf-8") if a.out.exists() else ""
        if current == text:
            print("sqt registry: ok")
            return 0
        fresh = {r["key"]: r for r in json.loads(text)["records"]}
        old = {r["key"]: r for r in json.loads(current)["records"]} if current else {}
        changed = sorted(k for k in set(fresh) | set(old) if fresh.get(k) != old.get(k))
        for k in changed[:20]:
            print(f"  - {k}")
        print(f"sqt registry: stale ({len(changed)} record(s) differ"
              + ("" if changed else "; header or findings differ") + "). Rebuild with "
              "scripts/build_sqt_registry.py.")
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    doc = json.loads(text)
    s = doc["summary"]
    print(f"wrote {_rel(a.out)}: {s['records']} records, {s['eligible']} eligible")
    for code, n in sorted(s["byState"].items()):
        print(f"  {code}: {n['records']} records, {n['eligible']} eligible, "
              + ", ".join(f"{k} {n[k]}" for k in sr.VERIFICATION_STATUSES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
