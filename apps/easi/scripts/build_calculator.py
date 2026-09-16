"""Generate the EASI Excel calculator from the application's own scoring definitions.

The workbook is never edited by hand. Every band, curve, regional threshold,
lookup, weight and anchor is read from the same files the application scores
with (``data/screening-methods.json``, ``data/reference-curves.json``,
``data/cwa-mapping.json``, ``data/easi-metrics.json``, ``data/scoring-identity.json``)
and written as worksheet formulas, so the calculator and the web application
implement one methodology. ``tests/test_calculator_parity.py`` proves that on a
retained case set scored by the application's own engine.

Sheets: Instructions, Inputs, Metrics, Results, Reference, Metadata. Formulas
use direct cell references and a small function vocabulary (IF AND OR NOT MIN
MAX ROUND INDEX MATCH IFERROR ISNUMBER COUNT SUMPRODUCT AVERAGE PRODUCT UPPER
TRIM SUBSTITUTE LEN) so Excel 2016 and later, LibreOffice and the Python
``formulas`` evaluator all agree. Defined names are added for every entry cell
and reference column for readers and for the fill code, but no formula depends
on them.

Route logic that lives in the adapters rather than the catalog (fallbacks and
observed overrides) is written out in ROUTES below and mirrors
``easi/metrics/*.py`` and ``easi/assessment.py``:

    nutrients          region known and at least one analyte, else min(CHEM) fallback
    impairment         conclusive ATTAINS category, else min(CHEM) fallback
    population support prg_bmmi0809 in [0, 1], else the ICI/IWI products (all twelve)
    channel evolution  observed class with a note > canal FCODE > worst of BHR and ER
    bank condition     observed erosion and armoring (both) > BHR proxy

Run:  .venv/Scripts/python.exe scripts/build_calculator.py [--out PATH] [--check]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.workbook.defined_name import DefinedName  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from easi import config, scoring  # noqa: E402
from easi import screening_methods as sm  # noqa: E402
from easi.national import method_version  # noqa: E402

TEMPLATE_VERSION = "1.0.0"
TEMPLATE_DATE = "2026-09-16"
OUT_DIR = os.path.join(ROOT, "www", "calculator")
OUT_NAME = f"EASI_Calculator_{TEMPLATE_VERSION}.xlsx"
PASSWORD = "easi"      # a guard rail against accidental edits, not a secret
FIXED_STAMP = _dt.datetime(2026, 9, 16, 0, 0, 0)

RATINGS = ("Good", "Fair", "Poor")
INDEX_EDGES = (0.39, 0.69)
COVERAGE_THRESHOLD = 0.70

# Keys that name the same quantity under two catalog names (the adapters read
# the same StreamCat field for both).
ALIASES = {"chemCat": "chemCatchment", "chemWs": "chemWatershed"}

# Colours
FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")      # required entry
FILL_OPTIONAL = PatternFill("solid", fgColor="DDEBF7")   # optional override or context
FILL_CALC = PatternFill("solid", fgColor="EDEDED")       # computed
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FONT_HEAD = Font(bold=True, color="FFFFFF")
FONT_BOLD = Font(bold=True)
FONT_NOTE = Font(italic=True, color="555555", size=9)
THIN = Side(style="thin", color="BBBBBB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
UNLOCKED = Protection(locked=False)

# ---------------------------------------------------------------------------
# Inputs: one entry per quantity. Order and grouping are the reading order of
# the Inputs sheet. ``keys`` are the catalog input keys the entry serves.
# ---------------------------------------------------------------------------
CONTEXT_INPUTS = [
    # name, label, kind, options/min/max, note
    ("ctx_region", "NARS-9 aggregate ecoregion of the site", "list", None,
     "Code of the EPA NARS nine-region ecoregion containing the site (CPL, NAP, NPL, SAP, SPL, TPL, UMW, WMT, XER). "
     "It selects the regional reference curves and the nutrient thresholds. Leave blank if unknown: the national "
     "curves apply and nutrients fall back to the CHEM index."),
    ("ctx_fcode", "NHD feature code (FCODE) of the reach", "whole", (0, 99999),
     "From NHDPlus V2. Codes 33600, 33601 and 33603 are canals and ditches, which rate Channel evolution Poor."),
    ("ctx_slope_override", "Slope class override", "list", None,
     "Normally derived from the channel slope below. Set only to force a class (lt_0.5, 0.5_to_2, ge_2 or national)."),
]

QUANTITY_INPUTS = [
    # group, key, label, units, kind, (min, max), source, note
    ("Watershed land cover (StreamCat, watershed area of interest)", "impervious",
     "Impervious cover of the watershed", "%", "decimal", (0, 100), "StreamCat pctimp2019ws",
     "Used by Catchment hydrology and Light and thermal regime."),
    ("Watershed land cover (StreamCat, watershed area of interest)", "agriculture",
     "Agricultural cover of the watershed (crop plus hay and pasture)", "%", "decimal", (0, 100),
     "StreamCat pctcrop2019ws + pcthay2019ws (the application rounds the sum to two decimals)",
     "Used by Catchment hydrology, Sediment continuity and Bed composition."),
    ("Watershed land cover (StreamCat, watershed area of interest)", "woodyWetland",
     "Woody wetland cover of the watershed", "%", "decimal", (0, 100), "StreamCat pctwdwet2019ws", ""),
    ("Watershed land cover (StreamCat, watershed area of interest)", "herbaceousWetland",
     "Herbaceous wetland cover of the watershed", "%", "decimal", (0, 100), "StreamCat pcthbwet2019ws", ""),
    ("Watershed land cover (StreamCat, watershed area of interest)", "roadDensity",
     "Road density of the watershed", "km/km2", "decimal", (0, 1e6), "StreamCat rddensws",
     "Used by Reach inflow and Sediment continuity."),
    ("Watershed land cover (StreamCat, watershed area of interest)", "kFactor",
     "Soil erodibility K factor of the watershed", "", "decimal", (0, 1), "StreamCat kffactws", ""),
    ("Flow regulation and flow variability", "storage",
     "Normalized dam storage upstream", "m3/km2", "decimal", (0, 1e12), "StreamCat damnrmstorws",
     "A true zero is valid evidence. Leave blank when unknown."),
    ("Flow regulation and flow variability", "runoff",
     "Mean annual runoff of the watershed", "mm", "decimal", (0, 1e6), "StreamCat runoffws",
     "Must be positive for the degree of regulation."),
    ("Flow regulation and flow variability", "flowCv",
     "Monthly flow variability (coefficient of variation of the twelve EROM mean monthly flows)", "ratio",
     "decimal", (0, 1e6), "NHDPlus V2 EROM QE_01 to QE_12: population standard deviation over the mean, rounded to six decimals",
     "Enter the value from the EASI report, or compute it below from the twelve monthly flows."),
    ("Riparian corridor (StreamCat, 100 m watershed corridor)", "woodyRiparian",
     "Woody riparian cover in the 100 m corridor (forest, shrub and woody wetland)", "%", "decimal", (0, 100),
     "StreamCat conifer + deciduous + mixed forest + shrub + woody wetland at wsrp100 (rounded to one decimal)",
     "Used by Light and thermal regime and Habitat provision."),
    ("Riparian corridor (StreamCat, 100 m watershed corridor)", "forest",
     "Forest cover in the corridor", "%", "decimal", (0, 100), "StreamCat pctconif + pctdecid + pctmxfst at wsrp100", ""),
    ("Riparian corridor (StreamCat, 100 m watershed corridor)", "shrub",
     "Shrub cover in the corridor", "%", "decimal", (0, 100), "StreamCat pctshrb2019wsrp100", ""),
    ("Riparian corridor (StreamCat, 100 m watershed corridor)", "grassland",
     "Grassland cover in the corridor", "%", "decimal", (0, 100), "StreamCat pctgrs2019wsrp100", ""),
    ("Riparian corridor (StreamCat, 100 m watershed corridor)", "wetland",
     "Wetland cover in the corridor (woody and herbaceous)", "%", "decimal", (0, 100),
     "StreamCat pctwdwet + pcthbwet at wsrp100", ""),
    ("Channel geometry (3DEP cross-sections and NHDPlus attributes)", "bhr",
     "Bank-height ratio (low-bank height over maximum bankfull depth), reach median", "ratio", "decimal", (0, 1e3),
     "EASI report cross-section block or a field survey", "Used by High flow dynamics, Channel evolution and Channel and floodplain dynamics."),
    ("Channel geometry (3DEP cross-sections and NHDPlus attributes)", "er",
     "Entrenchment ratio (flood-prone width over bankfull width), reach median", "ratio", "decimal", (0, 1e3),
     "EASI report cross-section block or a field survey", "Used by Floodplain connectivity and Channel evolution."),
    ("Channel geometry (3DEP cross-sections and NHDPlus attributes)", "slope",
     "Channel slope", "m/m", "decimal", (0, 10), "NHDPlus V2 VAA slope",
     "Used by Hyporheic connectivity and to pick the entrenchment slope class."),
    ("Channel geometry (3DEP cross-sections and NHDPlus attributes)", "sinuosity",
     "Reach sinuosity", "ratio", "decimal", (1, 100), "reach geometry (channel length over straight-line length)", ""),
    ("Water quality (Water Quality Portal and ATTAINS)", "tn",
     "Total nitrogen, median of station medians within 5 miles and 10 years", "mg/L", "decimal", (0, 1e4),
     "WQP total-fraction observations", "Leave blank when there are no qualifying observations."),
    ("Water quality (Water Quality Portal and ATTAINS)", "tp",
     "Total phosphorus, median of station medians within 5 miles and 10 years", "mg/L", "decimal", (0, 1e4),
     "WQP total-fraction observations", ""),
    ("Water quality (Water Quality Portal and ATTAINS)", "category",
     "ATTAINS integrated-report category of the assessment unit at the reach or within 2 km", "", "category", None,
     "EPA ATTAINS", "1, 2, 3, 4A, 4B, 4C or 5. Category 3 and a blank fall back to the CHEM index."),
    ("Landscape integrity indices (StreamCat)", "chemCatchment",
     "CHEM integrity index, catchment", "index", "decimal", (0, 1), "StreamCat chemcat",
     "Fallback for Nutrient cycling and Water and soil quality; a component of the ICI product."),
    ("Landscape integrity indices (StreamCat)", "chemWatershed",
     "CHEM integrity index, watershed", "index", "decimal", (0, 1), "StreamCat chemws",
     "Fallback for Nutrient cycling and Water and soil quality; a component of the IWI product."),
    ("Landscape integrity indices (StreamCat)", "prGBmmi",
     "Predicted probability of Good benthic condition (prg_bmmi0809)", "probability", "decimal", (0, 1),
     "StreamCat prg_bmmi0809 at the other area of interest", "Population support uses it directly; blank falls back to the ICI/IWI products."),
    ("Landscape integrity indices (StreamCat)", "hydCat", "HYD integrity, catchment", "index", "decimal", (0, 1), "StreamCat hydcat", ""),
    ("Landscape integrity indices (StreamCat)", "sedCat", "SED integrity, catchment", "index", "decimal", (0, 1), "StreamCat sedcat", ""),
    ("Landscape integrity indices (StreamCat)", "connCat", "CONN integrity, catchment", "index", "decimal", (0, 1), "StreamCat conncat", ""),
    ("Landscape integrity indices (StreamCat)", "tempCat", "TEMP integrity, catchment", "index", "decimal", (0, 1), "StreamCat tempcat", ""),
    ("Landscape integrity indices (StreamCat)", "habtCat", "HABT integrity, catchment", "index", "decimal", (0, 1), "StreamCat habtcat", ""),
    ("Landscape integrity indices (StreamCat)", "hydWs", "HYD integrity, watershed", "index", "decimal", (0, 1), "StreamCat hydws", ""),
    ("Landscape integrity indices (StreamCat)", "sedWs", "SED integrity, watershed", "index", "decimal", (0, 1), "StreamCat sedws", ""),
    ("Landscape integrity indices (StreamCat)", "connWs", "CONN integrity, watershed", "index", "decimal", (0, 1), "StreamCat connws", ""),
    ("Landscape integrity indices (StreamCat)", "tempWs", "TEMP integrity, watershed", "index", "decimal", (0, 1), "StreamCat tempws", ""),
    ("Landscape integrity indices (StreamCat)", "habtWs", "HABT integrity, watershed", "index", "decimal", (0, 1), "StreamCat habtws", ""),
    ("Counts (USGS NAS and USACE NID)", "taxaCount",
     "Established non-native taxa recorded in the HUC12", "count", "whole", (0, 100000), "USGS NAS",
     "A successful empty query is zero. Leave blank when the query was unavailable."),
    ("Counts (USGS NAS and USACE NID)", "damCount",
     "Mapped dams within one mile of the site", "count", "whole", (0, 100000), "USACE NID", ""),
]

OVERRIDE_INPUTS = [
    ("ov_stageClass", "Observed channel-evolution class", "list_rating", None,
     "Good, Fair or Poor from a documented field assessment. Applies only with the note below."),
    ("ov_indicators", "Observed channel-evolution indicators (note)", "text", None,
     "Required for the observed class to replace the automatic proxy."),
    ("ov_erodingBankPct", "Observed eroding bank", "decimal", (0, 100),
     "Percent of bank length eroding, from field or verified imagery. Both bank entries are required."),
    ("ov_armoredBankPct", "Observed armored bank", "decimal", (0, 100),
     "Percent of bank length armored."),
]

MONTH_INPUTS = [f"m{i:02d}" for i in range(1, 13)]


# ---------------------------------------------------------------------------
# Small formula helpers
# ---------------------------------------------------------------------------
def fnum(x) -> str:
    """A number literal Excel reads back exactly (repr keeps 17 digits)."""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return str(x)
    return repr(float(x))


def q(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


def band_predicate(b: dict, x: str) -> str:
    """The exact interval test of one catalog band (matches rating_for_value)."""
    parts = []
    if b.get("min") is not None:
        parts.append(f"{x}>={fnum(b['min'])}" if b.get("minInclusive") else f"{x}>{fnum(b['min'])}")
    if b.get("max") is not None:
        parts.append(f"{x}<={fnum(b['max'])}" if b.get("maxInclusive") else f"{x}<{fnum(b['max'])}")
    if not parts:
        return "TRUE"
    return parts[0] if len(parts) == 1 else "AND(" + ",".join(parts) + ")"


def bands_formula(bands: list[dict], x: str) -> str:
    """Rating text from bands, "" when unrated or not a number."""
    expr = '""'
    for b in reversed(bands):
        expr = f"IF({band_predicate(b, x)},{q(b['rating'])},{expr})"
    return f"IF(NOT(ISNUMBER({x})),\"\",{expr})"


def index_from_rating(r: str, anchors: dict[str, str]) -> str:
    """Index anchor from a rating cell (text), "" when blank."""
    return (f"IF({r}={q('Good')},{anchors['Good']},IF({r}={q('Fair')},{anchors['Fair']},"
            f"IF({r}={q('Poor')},{anchors['Poor']},\"\")))")


def rating_from_index_edges(idx: str) -> str:
    lo, hi = INDEX_EDGES
    return f"IF(NOT(ISNUMBER({idx})),\"\",IF({idx}>={fnum(hi)},\"Good\",IF({idx}>={fnum(lo)},\"Fair\",\"Poor\")))"


def rating_from_anchor(idx: str, anchors: dict[str, str]) -> str:
    """Rating whose anchor equals a combined worst/best index."""
    return (f"IF(NOT(ISNUMBER({idx})),\"\",IF({idx}={anchors['Good']},\"Good\","
            f"IF({idx}={anchors['Fair']},\"Fair\",IF({idx}={anchors['Poor']},\"Poor\",\"\"))))")


class Sheet:
    """Row-by-row writer with absolute references and defined names."""

    def __init__(self, wb: Workbook, ws, name: str):
        self.wb, self.ws, self.name = wb, ws, name
        self.row = 1

    def ref(self, col: int, row: int) -> str:
        return f"'{self.name}'!${get_column_letter(col)}${row}"

    def rng(self, col: int, r1: int, r2: int) -> str:
        c = get_column_letter(col)
        return f"'{self.name}'!${c}${r1}:${c}${r2}"

    def name_cell(self, name: str, col: int, row: int) -> str:
        ref = self.ref(col, row)
        self.wb.defined_names[name] = DefinedName(name, attr_text=ref)
        return ref

    def name_range(self, name: str, col: int, r1: int, r2: int) -> str:
        ref = self.rng(col, r1, r2)
        self.wb.defined_names[name] = DefinedName(name, attr_text=ref)
        return ref

    def cell(self, col: int, value=None, *, row: int | None = None, fill=None, font=None,
             fmt=None, unlocked=False, wrap=False, border=False):
        c = self.ws.cell(row=row or self.row, column=col)
        if value is not None:
            c.value = value
        if fill is not None:
            c.fill = fill
        if font is not None:
            c.font = font
        if fmt is not None:
            c.number_format = fmt
        if unlocked:
            c.protection = UNLOCKED
        if wrap:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        if border:
            c.border = BOX
        return c

    def header(self, *labels: str, widths: dict[int, int] | None = None):
        for i, label in enumerate(labels, 1):
            self.cell(i, label, fill=FILL_HEAD, font=FONT_HEAD)
        self.row += 1

    def title(self, text: str, size: int = 13):
        self.cell(1, text, font=Font(bold=True, size=size))
        self.row += 1

    def blank(self, n: int = 1):
        self.row += n


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------
class Builder:
    def __init__(self):
        self.catalog = sm.catalog()
        self.methods = {m["methodKey"]: m for m in self.catalog["methods"]}
        self.by_metric = {m["metricId"]: m for m in self.catalog["methods"]}
        self.curves = sm.curve_sets()
        self.metrics = config.easi_metrics()["metrics"]
        self.meta = {m["metricId"]: m for m in self.metrics}
        self.cwa = config.cwa_mapping()          # functionId -> {physical, chemical, biological}
        self.identity = config.scoring_identity()
        self.rating_index = self.catalog.get("ratingIndex") or config.RATING_INDEX
        self.wb = Workbook()
        self.wb.remove(self.wb.active)
        self.regions = sorted({k for s in self.curves.values() if s["stratifier"] == "nars9"
                               for k in s["curves"] if k != "national"})
        self.slope_classes = ["lt_0.5", "0.5_to_2", "ge_2"]
        self.problems = sm.validate_catalog()
        if self.problems:
            raise SystemExit("catalog problems: " + "; ".join(self.problems))
        for m in self.metrics:
            mids = m.get("indexMidpoints") or {}
            if mids and any(abs(float(mids[k]) - float(self.rating_index[k])) > 1e-12 for k in RATINGS):
                raise SystemExit(f"{m['metricId']}: indexMidpoints differ from the catalog ratingIndex")
        for k in RATINGS:
            if abs(float(self.rating_index[k]) * config.FUNCTION_SCORE_MAX % 1 - 0.5) < 1e-6:
                raise SystemExit("a rating anchor times 15 sits on a rounding tie")
        self.scores = {k: scoring.function_score(float(self.rating_index[k])) for k in RATINGS}
        # cell references filled while writing
        self.inputs: dict[str, str] = {}       # key -> absolute ref of the entry cell
        self.anchor_ref: dict[str, str] = {}   # rating -> absolute ref of its index anchor
        self.score_ref: dict[str, str] = {}
        self.curve_rows: dict[str, tuple[int, int]] = {}   # set -> (first row, last row)
        self.curve_cols: dict[str, int] = {}
        self.result_cells: dict[str, dict[str, str]] = {}  # metricId -> {rating, index, score, ...}

    # ---- Reference -------------------------------------------------------
    def build_reference(self):
        ws = self.wb.create_sheet("Reference")
        S = Sheet(self.wb, ws, "Reference")
        S.title("Reference tables generated from the EASI scoring definitions")
        S.cell(1, "Read only. These values are the application's own catalog, reference curves, regional "
                  "thresholds, lookups and weights. The Metrics sheet reads them by cell reference.",
               font=FONT_NOTE)
        S.row += 2

        # rating anchors
        S.title("Rating anchors and function scores", 11)
        S.header("Rating", "Index anchor", "Function score", "Score class")
        for r in RATINGS:
            S.cell(1, r)
            S.cell(2, float(self.rating_index[r]), fmt="0.000")
            S.cell(3, self.scores[r])
            S.cell(4, "Functioning" if self.scores[r] > 10 else "Functioning-at-Risk" if self.scores[r] > 5
                   else "Non-Functioning")
            self.anchor_ref[r] = S.name_cell(f"anchor_{r.lower()}", 2, S.row)
            self.score_ref[r] = S.name_cell(f"score_{r.lower()}", 3, S.row)
            S.row += 1
        S.blank()
        S.title("Condition classes", 11)
        S.header("Index at or below", "Class", "Function score at or below")
        for edge, label, fedge in ((0.39, "Non-Functioning", 5), (0.69, "Functioning-at-Risk", 10),
                                   (1.0, "Functioning", 15)):
            S.cell(1, edge); S.cell(2, label); S.cell(3, fedge); S.row += 1
        S.blank()

        # bands per input (informational)
        S.title("Fixed bands per input (informational; the Metrics formulas carry the same tests)", 11)
        S.header("Method", "Input", "Rating", "Min", "Min inclusive", "Max", "Max inclusive", "Label")
        for m in self.catalog["methods"]:
            for i in m.get("inputs", []):
                for b in i.get("bands") or []:
                    self._band_row(S, m["methodKey"], i["key"], b)
            for b in m.get("bands") or []:
                self._band_row(S, m["methodKey"], "(combined)", b)
            for v in m.get("variants") or []:
                for i in v.get("inputs", []):
                    for b in i.get("bands") or []:
                        self._band_row(S, v.get("methodKey"), i["key"], b)
                for b in v.get("bands") or []:
                    self._band_row(S, v.get("methodKey"), "(combined)", b)
        S.blank()

        # curves, long form, padded to five knots
        S.title("Reference curves (frozen), one row per curve, knots padded to five", 11)
        S.header("Key", "Set", "Stratum", "Higher is better", "n", "Members", "x1", "y1", "x2", "y2",
                 "x3", "y3", "x4", "y4", "x5", "y5", "x39", "x69", "q25", "q50", "q75", "Tier")
        first = S.row
        for set_id, s in self.curves.items():
            for key, c in s["curves"].items():
                pts = [(float(x), float(y)) for x, y in c["points"]]
                assert all(pts[i][0] < pts[i + 1][0] for i in range(len(pts) - 1)), (set_id, key)
                assert 2 <= len(pts) <= 5, (set_id, key, len(pts))
                while len(pts) < 5:
                    pts.append(pts[-1])
                S.cell(1, f"{set_id}|{key}"); S.cell(2, set_id); S.cell(3, key)
                S.cell(4, "TRUE" if s["higherIsBetter"] else "FALSE")
                S.cell(5, c.get("n")); S.cell(6, c.get("nMembers"))
                for k, (x, y) in enumerate(pts):
                    S.cell(7 + 2 * k, x); S.cell(8 + 2 * k, y)
                S.cell(17, c.get("x39")); S.cell(18, c.get("x69"))
                S.cell(19, c.get("q25")); S.cell(20, c.get("q50")); S.cell(21, c.get("q75"))
                S.cell(22, c.get("panelTier"))
                S.row += 1
        last = S.row - 1
        self.curve_rows["all"] = (first, last)
        self.curve_key_range = S.name_range("curve_keys", 1, first, last)
        self.curve_col_range = {}
        for k, col in (("x1", 7), ("y1", 8), ("x2", 9), ("y2", 10), ("x3", 11), ("y3", 12),
                       ("x4", 13), ("y4", 14), ("x5", 15), ("y5", 16), ("x39", 17), ("x69", 18)):
            self.curve_col_range[k] = S.name_range(f"curve_{k}", col, first, last)
        S.blank()

        # nutrient regional bands
        nut = self.methods["regional-nutrient-condition"]
        tn = next(i for i in nut["inputs"] if i["key"] == "tn")["regionalBands"]
        tp = next(i for i in nut["inputs"] if i["key"] == "tp")["regionalBands"]
        S.title("Nutrient regional thresholds by NARS-9 region (mg/L): Good at or below the first value, "
                "Poor at or above the second, Fair between", 11)
        S.header("Region", "TN good/fair", "TN fair/poor", "TP good/fair", "TP fair/poor")
        first = S.row
        for region in self.regions:
            S.cell(1, region); S.cell(2, float(tn[region][0])); S.cell(3, float(tn[region][1]))
            S.cell(4, float(tp[region][0])); S.cell(5, float(tp[region][1])); S.row += 1
        last = S.row - 1
        self.nut_regions = S.name_range("nutrient_regions", 1, first, last)
        self.nut_cols = {k: S.name_range(f"nutrient_{k}", col, first, last)
                         for k, col in (("tn_gf", 2), ("tn_fp", 3), ("tp_gf", 4), ("tp_fp", 5))}
        S.blank()

        # categorical lookups
        att = self.methods["attains-regulatory-category"]["formula"]["lookup"]
        S.title("ATTAINS category lookup (category 3 is inconclusive and falls back to the CHEM index)", 11)
        S.header("Category", "Rating", "Label")
        first = S.row
        for key, item in att.items():
            # "none" rather than an empty cell: INDEX of an empty cell reads as 0 in Excel
            S.cell(1, str(key)); S.cell(2, item.get("rating") or "none"); S.cell(3, item.get("label")); S.row += 1
        last = S.row - 1
        self.att_keys = S.name_range("attains_keys", 1, first, last)
        self.att_ratings = S.name_range("attains_ratings", 2, first, last)
        S.blank()
        canal = self.variant("channel-adjustment-susceptibility", "channelized-fcode")["formula"]["lookup"]
        self.canal_codes = sorted(int(k) for k in canal)
        S.title("Canal and ditch feature codes (Channel evolution rates Poor)", 11)
        S.header("FCODE", "Rating")
        for code in self.canal_codes:
            S.cell(1, code); S.cell(2, canal[str(code)]["rating"]); S.row += 1
        S.blank()

        # outcome weights
        S.title("Function to outcome mapping and weights (D direct, i indirect)", 11)
        S.header("Function", "Physical", "Chemical", "Biological", "Weight D", "Weight i")
        for m in self.metrics:
            row = self.cwa[m["functionId"]]
            S.cell(1, m["functionName"]); S.cell(2, row["physical"]); S.cell(3, row["chemical"])
            S.cell(4, row["biological"]); S.cell(5, config.WEIGHTS["D"]); S.cell(6, config.WEIGHTS["i"])
            S.row += 1
        S.blank()
        S.title("Slope classes for the entrenchment curves", 11)
        S.header("Class", "Channel slope")
        for cls, txt in (("lt_0.5", "below 0.005 m/m"), ("0.5_to_2", "0.005 to below 0.02 m/m"),
                         ("ge_2", "0.02 m/m and above"), ("national", "slope unknown or negative")):
            S.cell(1, cls); S.cell(2, txt); S.row += 1
        for col, width in ((1, 34), (2, 16), (3, 16), (4, 14), (5, 12), (6, 12)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = None
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    def _band_row(self, S: Sheet, method: str, key: str, b: dict):
        S.cell(1, method); S.cell(2, key); S.cell(3, b["rating"])
        S.cell(4, b.get("min")); S.cell(5, "TRUE" if b.get("minInclusive") else "FALSE")
        S.cell(6, b.get("max")); S.cell(7, "TRUE" if b.get("maxInclusive") else "FALSE")
        S.cell(8, b.get("label")); S.row += 1

    def variant(self, method_key: str, variant_key: str) -> dict:
        for v in self.methods[method_key].get("variants") or []:
            if v.get("methodKey") == variant_key:
                return v
        raise KeyError(variant_key)

    # ---- Inputs ------------------------------------------------------------
    def build_inputs(self):
        ws = self.wb.create_sheet("Inputs")
        S = Sheet(self.wb, ws, "Inputs")
        S.title("EASI calculator: inputs")
        S.cell(1, "Yellow cells are required entries, blue cells are optional context or overrides, grey cells "
                  "are computed. Enter each quantity once; the Metrics sheet reuses it wherever the method needs "
                  "it. Leave a cell blank when the evidence is unavailable. Never enter zero for unknown.",
               font=FONT_NOTE, wrap=True)
        ws.merge_cells(start_row=S.row, start_column=1, end_row=S.row, end_column=6)
        ws.row_dimensions[S.row].height = 42
        S.row += 2

        # site block
        S.title("Site", 11)
        for label, name in (("Site name", "site_name"), ("Assessor", "site_assessor"), ("Date", "site_date"),
                            ("NHDPlus V2 COMID", "site_comid"), ("Latitude, longitude", "site_coords"),
                            ("Notes", "site_notes")):
            S.cell(1, label); S.cell(3, None, fill=FILL_OPTIONAL, unlocked=True, border=True)
            S.name_cell(name, 3, S.row); S.row += 1
        S.blank()

        S.title("Context", 11)
        S.header("Setting", "Key", "Value", "", "", "Note")
        dv_region = DataValidation(type="list", formula1='"' + ",".join(self.regions) + '"', allow_blank=True)
        dv_slope = DataValidation(type="list", formula1='"' + ",".join(self.slope_classes + ["national"]) + '"',
                                  allow_blank=True)
        dv_rating = DataValidation(type="list", formula1='"Good,Fair,Poor"', allow_blank=True)
        dv_category = DataValidation(type="list", formula1='"1,2,3,4A,4B,4C,5"', allow_blank=True)
        for dv in (dv_region, dv_slope, dv_rating, dv_category):
            ws.add_data_validation(dv)
        for name, label, kind, bounds, note in CONTEXT_INPUTS:
            S.cell(1, label, wrap=True); S.cell(2, name, font=FONT_NOTE)
            c = S.cell(3, None, fill=FILL_OPTIONAL, unlocked=True, border=True)
            if kind == "list" and name == "ctx_region":
                dv_region.add(c)
            elif kind == "list":
                dv_slope.add(c)
            elif kind == "whole":
                dv = DataValidation(type="whole", operator="between", formula1=str(bounds[0]),
                                    formula2=str(bounds[1]), allow_blank=True)
                ws.add_data_validation(dv); dv.add(c)
            S.cell(6, note, font=FONT_NOTE, wrap=True)
            self.inputs[name] = S.name_cell(name, 3, S.row)
            S.row += 1
        # derived slope class
        S.cell(1, "Slope class used for the entrenchment curves (derived)")
        S.cell(2, "calc_slope_class", font=FONT_NOTE)
        self._slope_class_row = S.row
        S.cell(3, None, fill=FILL_CALC, border=True)
        self.inputs["ctx_slope_class"] = S.name_cell("calc_slope_class", 3, S.row)
        S.cell(6, "lt_0.5 below 0.005 m/m, 0.5_to_2 to below 0.02, ge_2 at or above 0.02, national when the slope "
                  "is blank or negative. The override above wins when set.", font=FONT_NOTE, wrap=True)
        S.row += 2

        S.title("Desktop quantities", 11)
        S.header("Quantity", "Key", "Value", "Units", "Source", "Note")
        group = None
        for grp, key, label, units, kind, bounds, source, note in QUANTITY_INPUTS:
            if grp != group:
                group = grp
                S.cell(1, grp, font=FONT_BOLD); S.row += 1
            S.cell(1, label, wrap=True); S.cell(2, key, font=FONT_NOTE)
            c = S.cell(3, None, fill=FILL_INPUT, unlocked=True, border=True)
            if kind == "decimal":
                dv = DataValidation(type="decimal", operator="between", formula1=fnum(bounds[0]),
                                    formula2=fnum(bounds[1]), allow_blank=True)
                ws.add_data_validation(dv); dv.add(c)
            elif kind == "whole":
                dv = DataValidation(type="whole", operator="between", formula1=str(bounds[0]),
                                    formula2=str(bounds[1]), allow_blank=True)
                ws.add_data_validation(dv); dv.add(c)
            elif kind == "category":
                dv_category.add(c)
            S.cell(4, units); S.cell(5, source, wrap=True); S.cell(6, note, font=FONT_NOTE, wrap=True)
            self.inputs[key] = S.name_cell(f"in_{key}", 3, S.row)
            S.row += 1
        for alias, target in ALIASES.items():
            self.inputs[alias] = self.inputs[target]
        # the slope class formula now that the slope cell exists
        slope = self.inputs["slope"]
        ov = self.inputs["ctx_slope_override"]
        ws.cell(row=self._slope_class_row, column=3).value = (
            f'=IF({ov}<>"",{ov},IF(NOT(ISNUMBER({slope})),"national",IF({slope}<0,"national",'
            f'IF({slope}<0.005,"lt_0.5",IF({slope}<0.02,"0.5_to_2","ge_2")))))')
        S.blank()

        S.title("Monthly flow helper (optional): the twelve EROM mean monthly flows, QE_01 to QE_12", 11)
        S.cell(1, "Enter all twelve flows (cfs) and copy the computed variability into the Monthly flow "
                  "variability cell above, or enter that value directly from the EASI report.",
               font=FONT_NOTE, wrap=True)
        ws.merge_cells(start_row=S.row, start_column=1, end_row=S.row, end_column=6)
        S.row += 1
        S.header("Month", "Key", "Flow (cfs)", "", "", "")
        first = S.row
        for i, key in enumerate(MONTH_INPUTS, 1):
            S.cell(1, f"Month {i:02d}"); S.cell(2, key, font=FONT_NOTE)
            S.cell(3, None, fill=FILL_OPTIONAL, unlocked=True, border=True)
            S.name_cell(f"in_{key}", 3, S.row); S.row += 1
        last = S.row - 1
        rng = S.rng(3, first, last)
        S.cell(1, "Computed monthly flow variability (all twelve required, mean must be positive)")
        S.cell(3, f'=IF(COUNT({rng})<12,"",IF(AVERAGE({rng})<=0,"",ROUND(STDEV.P({rng})/AVERAGE({rng}),6)))',
               fill=FILL_CALC, border=True, fmt="0.000000")
        S.name_cell("flow_cv_helper", 3, S.row)
        S.row += 2

        S.title("Observed evidence (optional overrides of the geometry proxies)", 11)
        S.header("Observation", "Key", "Value", "Units", "", "Note")
        for name, label, kind, bounds, note in OVERRIDE_INPUTS:
            S.cell(1, label, wrap=True); S.cell(2, name, font=FONT_NOTE)
            c = S.cell(3, None, fill=FILL_OPTIONAL, unlocked=True, border=True)
            if kind == "list_rating":
                dv_rating.add(c)
            elif kind == "decimal":
                dv = DataValidation(type="decimal", operator="between", formula1=fnum(bounds[0]),
                                    formula2=fnum(bounds[1]), allow_blank=True)
                ws.add_data_validation(dv); dv.add(c)
                S.cell(4, "%")
            S.cell(6, note, font=FONT_NOTE, wrap=True)
            self.inputs[name] = S.name_cell(name, 3, S.row)
            S.row += 1

        for col, width in ((1, 58), (2, 18), (3, 16), (4, 10), (5, 44), (6, 60)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = "A2"
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # ---- Metrics -----------------------------------------------------------
    def curve_lookup(self, set_id: str, stratum_expr: str) -> str:
        """Row number (within the curve table) for a set and a stratum expression."""
        keys = self.curve_key_range
        return (f"IFERROR(MATCH({q(set_id + '|')}&{stratum_expr},{keys},0),"
                f"MATCH({q(set_id + '|national')},{keys},0))")

    def interp(self, x: str, row_expr: str) -> str:
        """Piecewise-linear interpolation in the engine's operation order, clamped to 0..1."""
        cx = {k: f"INDEX({self.curve_col_range[f'x{k}']},{row_expr})" for k in range(1, 6)}
        cy = {k: f"INDEX({self.curve_col_range[f'y{k}']},{row_expr})" for k in range(1, 6)}

        def seg(a: int, b: int) -> str:
            return f"{cy[a]}+(({x}-{cx[a]})/({cx[b]}-{cx[a]}))*({cy[b]}-{cy[a]})"

        inner = (f"IF({x}<={cx[1]},{cy[1]},IF({x}>={cx[5]},{cy[5]},"
                 f"IF({x}<={cx[2]},{seg(1, 2)},IF({x}<={cx[3]},{seg(2, 3)},"
                 f"IF({x}<={cx[4]},{seg(3, 4)},{seg(4, 5)})))))")
        return f"IF(NOT(ISNUMBER({x})),\"\",MAX(0,MIN(1,{inner})))"

    def build_metrics(self):
        ws = self.wb.create_sheet("Metrics")
        S = Sheet(self.wb, ws, "Metrics")
        S.title("EASI calculator: metric scoring")
        S.cell(1, "Read only. One block per function: the inputs it reads, the route that applied, the rating, "
                  "the index anchor and the function score. Ratings follow the application's catalog exactly.",
               font=FONT_NOTE)
        S.row += 2
        A = {r: self.anchor_ref[r] for r in RATINGS}
        n = 0
        for m in self.metrics:
            n += 1
            method = self.by_metric[m["metricId"]]
            S.cell(1, f"{n}. {m['functionName']}", font=Font(bold=True, size=11))
            S.cell(2, f"{method['title']} ({method['methodKey']}, {method['operator']})", font=FONT_NOTE)
            S.row += 1
            S.header("Item", "Value", "Rating", "Index", "Note")
            cells = getattr(self, f"_m_{method['methodKey'].replace('-', '_')}", self._m_generic)(S, method, A)
            cells["functionId"] = m["functionId"]
            self.result_cells[m["metricId"]] = cells
            S.blank()
        for col, width in ((1, 52), (2, 18), (3, 12), (4, 12), (5, 70)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # -- shared block pieces --------------------------------------------------
    def _finish(self, S: Sheet, key: str, rating_formula: str, A: dict, route_formula: str,
                combined_formula: str | None = None, note: str = "", status_formula: str | None = None) -> dict:
        """Rows: route, combined, rating, index, score, status. Returns the cell refs."""
        cells = {}
        S.cell(1, "Route that applied"); S.cell(2, route_formula, fill=FILL_CALC)
        cells["route"] = S.name_cell(f"{key}_route", 2, S.row); S.row += 1
        if combined_formula is not None:
            S.cell(1, "Combined value"); S.cell(2, combined_formula, fill=FILL_CALC)
            cells["combined"] = S.name_cell(f"{key}_combined", 2, S.row); S.row += 1
        S.cell(1, "Function rating"); S.cell(3, rating_formula, fill=FILL_CALC, font=FONT_BOLD)
        cells["rating"] = S.name_cell(f"{key}_rating", 3, S.row)
        S.cell(4, "=" + index_from_rating(cells["rating"], A), fill=FILL_CALC, fmt="0.000")
        cells["index"] = S.name_cell(f"{key}_index", 4, S.row)
        if note:
            S.cell(5, note, font=FONT_NOTE, wrap=True)
        S.row += 1
        S.cell(1, "Function score (0 to 15)")
        r = cells["rating"]
        S.cell(2, f"=IF({r}={q('Good')},{self.score_ref['Good']},IF({r}={q('Fair')},{self.score_ref['Fair']},"
                  f"IF({r}={q('Poor')},{self.score_ref['Poor']},\"\")))", fill=FILL_CALC, font=FONT_BOLD)
        cells["score"] = S.name_cell(f"{key}_score", 2, S.row); S.row += 1
        S.cell(1, "Status")
        S.cell(2, status_formula or f'=IF({r}="","not rated","complete")', fill=FILL_CALC)
        cells["status"] = S.name_cell(f"{key}_status", 2, S.row); S.row += 1
        return cells

    def _input_row(self, S: Sheet, key: str, label: str, rule_formula: str | None, A: dict,
                   note: str = "", value_ref: str | None = None) -> tuple[str, str, str]:
        """An input row: value, rating (if rule), index. Returns (value, rating, index) refs.

        The value returned is the entry cell itself, never the mirror in column
        B: in Excel a formula that references an empty cell evaluates to 0, so a
        rule pointed at the mirror would rate a blank input as a number.
        """
        vref = value_ref or self.inputs[key]
        S.cell(1, label); S.cell(2, f'=IF({vref}="","",{vref})', fill=FILL_CALC)
        v = vref
        rat = idx = ""
        if rule_formula is not None:
            S.cell(3, "=" + rule_formula, fill=FILL_CALC)
            rat = S.ref(3, S.row)
            S.cell(4, "=" + index_from_rating(rat, A), fill=FILL_CALC, fmt="0.000")
            idx = S.ref(4, S.row)
        if note:
            S.cell(5, note, font=FONT_NOTE, wrap=True)
        S.row += 1
        return v, rat, idx

    def _rule_formula(self, S: Sheet, rule: dict, x: str, region_ref: str | None = None) -> str:
        """Rating formula for one catalog rule (bands or curve) on value expression x.

        A curve rule first writes two helper rows (the curve row in the
        Reference table and the interpolated index) so no formula repeats the
        lookup and every formula stays far below Excel's 8,192-character limit.
        """
        if rule.get("curve"):
            spec = rule["curve"]
            strat = region_ref if spec["stratifier"] == "nars9" else self.inputs["ctx_slope_class"]
            S.cell(1, f"Reference curve used ({spec['set']}, {spec['stratifier']}, national fallback)",
                   font=FONT_NOTE)
            S.cell(2, "=" + self.curve_lookup(spec["set"], strat), fill=FILL_CALC)
            row_ref = S.ref(2, S.row)
            S.cell(5, f'=INDEX({self.curve_key_range},{row_ref})', fill=FILL_CALC, font=FONT_NOTE)
            S.row += 1
            S.cell(1, "Curve index (interpolated, 0 to 1; Good at or above 0.69, Fair at or above 0.39)",
                   font=FONT_NOTE)
            S.cell(2, "=" + self.interp(x, row_ref), fill=FILL_CALC, fmt="0.0000")
            idx_ref = S.ref(2, S.row)
            S.row += 1
            return rating_from_index_edges(idx_ref)
        return bands_formula(rule["bands"], x)

    def _worst_best(self, S: Sheet, key: str, method: dict, A: dict, op: str, extra_note: str = "") -> dict:
        """worst_index / best_index over the method's required inputs (curve or bands per input)."""
        required = [i for i in method["inputs"] if i.get("required") and not i.get("contextOnly")]
        idx_refs, first_row = [], None
        for i in required:
            rule = sm.rule_for_input(method, i) if not i.get("regionalBands") else i
            x = self.inputs[i["key"]]
            formula = self._rule_formula(S, rule, x, self.inputs["ctx_region"])
            _, _, idx = self._input_row(S, i["key"], i["label"], formula, A)
            idx_refs.append(idx)
            first_row = first_row or S.row - 1
        last_row = S.row - 1
        allow = bool((method.get("formula") or {}).get("allowPartial"))
        idx_rng = S.rng(4, first_row, last_row)
        rated = f"COUNT({idx_rng})"
        pick = "MIN" if op == "worst_index" else "MAX"
        gate = f"{rated}=0" if allow else f"{rated}<{len(required)}"
        combined = f"=IF({gate},\"\",{pick}({idx_rng}))"
        S.cell(1, "Governing input"); S.cell(2, f'=IF({S.ref(2, S.row + 1)}="","",INDEX({S.rng(1, first_row, last_row)},MATCH({S.ref(2, S.row + 1)},{idx_rng},0)))', fill=FILL_CALC)
        gov = S.name_cell(f"{key}_governing", 2, S.row); S.row += 1
        S.cell(1, "Combined rating index (the more limiting input)" if op == "worst_index" else "Combined rating index (the better pathway)")
        S.cell(2, combined, fill=FILL_CALC, fmt="0.000")
        comb = S.name_cell(f"{key}_combined", 2, S.row); S.row += 1
        status = (f'=IF({rated}=0,"not rated",IF({rated}<{len(required)},"partial","complete"))' if allow
                  else f'=IF({rated}<{len(required)},"not rated","complete")')
        cells = self._finish(S, key, "=" + rating_from_anchor(comb, A), A, "=" + q("automatic method"),
                             None, extra_note, status)
        cells["governing"] = gov
        cells["combined"] = comb
        return cells

    # -- per-method blocks ----------------------------------------------------
    def _m_generic(self, S, method, A):
        raise SystemExit(f"no block writer for {method['methodKey']}")

    def _m_catchment_land_cover_pressure(self, S, method, A):
        return self._worst_best(S, "m01", method, A, "worst_index",
                                "Partial rating allowed when one input is missing.")

    def _m_watershed_wetland_extent(self, S, method, A):
        return self._sum_capped(S, "m02", method, A)

    def _sum_capped(self, S, key, method, A, curve=False):
        required = [i for i in method["inputs"] if i.get("required") and not i.get("contextOnly")]
        refs = []
        for i in required:
            v, _, _ = self._input_row(S, i["key"], i["label"], None, A)
            refs.append(v)
        cap = fnum(method["formula"]["cap"])
        all_num = "AND(" + ",".join(f"ISNUMBER({r})" for r in refs) + ")"
        combined = f'=IF({all_num},ROUND(MIN({"+".join(refs)},{cap}),12),"")'
        S.cell(1, f"Combined value (sum capped at {method['formula']['cap']})"); S.cell(2, combined, fill=FILL_CALC)
        comb = S.name_cell(f"{key}_combined", 2, S.row); S.row += 1
        rule = sm.rule_for_method(method)
        rating = "=" + self._rule_formula(S, rule, comb, self.inputs["ctx_region"])
        return self._finish(S, key, rating, A, "=" + q("automatic method"),
                            note="All classes are required. A missing class is unknown, not zero.")

    def _m_road_density_inflow_pressure(self, S, method, A):
        return self._threshold(S, "m03", method, A)

    def _threshold(self, S, key, method, A, note=""):
        inp = next(i for i in method["inputs"] if not i.get("contextOnly"))
        v, _, _ = self._input_row(S, inp["key"], inp["label"], None, A)
        for ctx in (i for i in method["inputs"] if i.get("contextOnly")):
            if ctx["key"] in self.inputs or ctx["key"] == "fcodeContext":
                ref = self.inputs.get(ctx["key"]) or self.inputs["ctx_fcode"]
                self._input_row(S, ctx["key"], ctx["label"] + " (context only)", None, A, value_ref=ref)
        rule = sm.rule_for_method(method)
        rating = "=" + self._rule_formula(S, rule, v, self.inputs["ctx_region"])
        return self._finish(S, key, rating, A, "=" + q("automatic method"), note=note)

    def _m_degree_of_regulation(self, S, method, A):
        f = method["formula"]
        num = next(i for i in method["inputs"] if i["key"] == f["numerator"])
        den = next(i for i in method["inputs"] if i["key"] == f["denominator"])
        nv, _, _ = self._input_row(S, num["key"], num["label"], None, A)
        dv, _, _ = self._input_row(S, den["key"], den["label"], None, A)
        positive = [i["key"] for i in method["inputs"] if i.get("positive")]
        guards = [f"ISNUMBER({nv})", f"ISNUMBER({dv})", f"{dv}<>0"]
        for k in positive:
            guards.append(f"{self.inputs[k]}>0")
        combined = (f'=IF(AND({",".join(guards)}),ROUND(({fnum(f.get("numeratorMultiplier", 1))}*{nv})/'
                    f'({fnum(f.get("denominatorMultiplier", 1))}*{dv}),12),"")')
        S.cell(1, "Degree of regulation (%)"); S.cell(2, combined, fill=FILL_CALC)
        comb = S.name_cell("m04_combined", 2, S.row); S.row += 1
        rating = "=" + bands_formula(method["bands"], comb)
        return self._finish(S, "m04", rating, A, "=" + q("automatic method"))

    def _m_erom_flow_variability(self, S, method, A):
        return self._threshold(S, "m05", method, A,
                               "Unvalidated screening proxy: monthly flow variability, lower is better, rated on the "
                               "regional curve with the national fallback.")

    def _m_bank_height_ratio(self, S, method, A):
        return self._threshold(S, "m06", method, A, "Values below 1.0 warrant a check of the cross-section geometry.")

    def _m_entrenchment_ratio(self, S, method, A):
        return self._threshold(S, "m07", method, A, "Rated on the slope-class curve, national fallback when the slope is unknown.")

    def _m_hyporheic_exchange_potential(self, S, method, A):
        return self._worst_best(S, "m08", method, A, "best_index", "One valid pathway gives a partial rating.")

    def _m_channel_adjustment_susceptibility(self, S, method, A):
        key = "m09"
        # inputs of the automatic proxy
        bhr = next(i for i in method["inputs"] if i["key"] == "bhr")
        er = next(i for i in method["inputs"] if i["key"] == "er")
        _, _, bhr_idx = self._input_row(S, "bhr", bhr["label"], bands_formula(bhr["bands"], self.inputs["bhr"]), A)
        first = S.row - 1
        er_rule = sm.rule_for_input(method, er)
        er_formula = self._rule_formula(S, er_rule, self.inputs["er"], None)
        _, _, er_idx = self._input_row(S, "er", er["label"], er_formula, A)
        idx_rng = S.rng(4, first, S.row - 1)
        fcode = self.inputs["ctx_fcode"]
        canal = "OR(" + ",".join(f"{fcode}={c}" for c in self.canal_codes) + ")"
        stage, note_ref = self.inputs["ov_stageClass"], self.inputs["ov_indicators"]
        observed = f'AND(OR({stage}="Good",{stage}="Fair",{stage}="Poor"),LEN(TRIM({note_ref}&""))>0)'
        proxy_idx = f'IF(COUNT({idx_rng})<2,"",MIN({idx_rng}))'
        S.cell(1, "Automatic proxy index (worse of BHR and ER, both required)")
        S.cell(2, "=" + proxy_idx, fill=FILL_CALC, fmt="0.000")
        proxy = S.name_cell(f"{key}_proxy_index", 2, S.row); S.row += 1
        route = (f'=IF({observed},"observed channel class",IF({canal},"canal or ditch (FCODE)",'
                 f'IF({proxy}="","not rated","automatic proxy")))')
        rating = (f'=IF({observed},{stage},IF({canal},"Poor",{rating_from_anchor(proxy, A)}))')
        return self._finish(S, key, rating, A, route, None,
                            "Observed class with a note replaces the proxy; canal and ditch feature codes rate Poor.")

    def _m_bhr_bank_instability_susceptibility(self, S, method, A):
        key = "m10"
        v, _, _ = self._input_row(S, "bhr", method["inputs"][0]["label"], None, A)
        var = self.variant("bhr-bank-instability-susceptibility", "observed-bank-condition")
        ero = next(i for i in var["inputs"] if i["key"] == "erodingBankPct")
        arm = next(i for i in var["inputs"] if i["key"] == "armoredBankPct")
        e_ref, a_ref = self.inputs["ov_erodingBankPct"], self.inputs["ov_armoredBankPct"]
        _, _, e_idx = self._input_row(S, "ov_erodingBankPct", "Observed eroding bank (%)",
                                      bands_formula(ero["bands"], e_ref), A, value_ref=e_ref)
        _, _, a_idx = self._input_row(S, "ov_armoredBankPct", "Observed armored bank (%)",
                                      bands_formula(arm["bands"], a_ref), A, value_ref=a_ref)
        observed = f"AND(ISNUMBER({e_ref}),ISNUMBER({a_ref}))"
        proxy_rating = bands_formula(method["bands"], v)
        obs_idx = f"MIN({e_idx},{a_idx})"
        route = f'=IF({observed},"observed bank condition",IF({proxy_rating}="","not rated","automatic proxy"))'
        rating = f"=IF({observed},{rating_from_anchor(obs_idx, A)},{proxy_rating})"
        return self._finish(S, key, rating, A, route, None,
                            "Both observed bank percentages replace the BHR proxy; the worse component governs.")

    def _m_sediment_supply_potential(self, S, method, A):
        return self._worst_best(S, "m11", method, A, "worst_index", "All three inputs are required.")

    def _m_watershed_agriculture_share(self, S, method, A):
        return self._threshold(S, "m12", method, A,
                               "Pressure proxy sharing the agriculture input of Catchment hydrology; not a bed measurement.")

    def _m_thermal_regulation_vulnerability(self, S, method, A):
        return self._worst_best(S, "m13", method, A, "worst_index", "Both inputs are required.")

    def _m_organic_matter_supply_potential(self, S, method, A):
        return self._sum_capped(S, "m14", method, A)

    def _m_regional_nutrient_condition(self, S, method, A):
        key = "m15"
        region = self.inputs["ctx_region"]
        rrow = f"MATCH({region},{self.nut_regions},0)"
        refs = {}
        for k, gf, fp in (("tn", "tn_gf", "tn_fp"), ("tp", "tp_gf", "tp_fp")):
            x = self.inputs[k]
            gfv = f"INDEX({self.nut_cols[gf]},{rrow})"
            fpv = f"INDEX({self.nut_cols[fp]},{rrow})"
            rating = (f'IF(NOT(ISNUMBER({x})),"",IFERROR(IF({x}<={gfv},"Good",IF({x}>={fpv},"Poor","Fair")),""))')
            label = next(i["label"] for i in method["inputs"] if i["key"] == k)
            _, rat, idx = self._input_row(S, k, label + " (mg/L)", rating, A)
            refs[k] = idx
        first = S.row - 2
        idx_rng = S.rng(4, first, S.row - 1)
        var = self.variant("regional-nutrient-condition", "streamcat-chem-integrity-nutrient")
        cat_ref, ws_ref = self.inputs["chemCatchment"], self.inputs["chemWatershed"]
        chem_val = f'IF(AND(ISNUMBER({cat_ref}),ISNUMBER({ws_ref})),ROUND(MIN({cat_ref},{ws_ref}),12),"")'
        S.cell(1, "CHEM fallback value (lower of catchment and watershed)"); S.cell(2, "=" + chem_val, fill=FILL_CALC)
        chem = S.name_cell(f"{key}_chem", 2, S.row); S.row += 1
        chem_rating = bands_formula(var["bands"], chem)
        primary = f"COUNT({idx_rng})>0"
        route = f'=IF({primary},"WQP nutrients with regional thresholds",IF({chem_rating}="","not rated","CHEM integrity fallback"))'
        rating = f"=IF({primary},{rating_from_anchor(f'MIN({idx_rng})', A)},{chem_rating})"
        status = f'=IF({primary},IF(COUNT({idx_rng})<2,"partial","complete"),IF({chem_rating}="","not rated","complete"))'
        return self._finish(S, key, rating, A, route, None,
                            "One rated analyte gives a partial rating; the worse analyte governs. Without a region or "
                            "observations the CHEM index decides.", status)

    def _m_attains_regulatory_category(self, S, method, A):
        key = "m16"
        cat = self.inputs["category"]
        keyx = f'SUBSTITUTE(UPPER(TRIM({cat}&""))," ","")'
        hit = f"INDEX({self.att_ratings},MATCH({keyx},{self.att_keys},0))"
        att = f'IF({cat}="","",IFERROR(IF({hit}="none","",{hit}),""))'
        S.cell(1, "ATTAINS category"); S.cell(2, f'=IF({cat}="","",{cat})', fill=FILL_CALC)
        S.cell(3, "=" + att, fill=FILL_CALC); att_ref = S.ref(3, S.row)
        S.cell(4, "=" + index_from_rating(att_ref, A), fill=FILL_CALC, fmt="0.000"); S.row += 1
        var = self.variant("attains-regulatory-category", "streamcat-chem-integrity-regulatory")
        cat_ref, ws_ref = self.inputs["chemCatchment"], self.inputs["chemWatershed"]
        chem_val = f'IF(AND(ISNUMBER({cat_ref}),ISNUMBER({ws_ref})),ROUND(MIN({cat_ref},{ws_ref}),12),"")'
        S.cell(1, "CHEM fallback value (lower of catchment and watershed)"); S.cell(2, "=" + chem_val, fill=FILL_CALC)
        chem = S.name_cell(f"{key}_chem", 2, S.row); S.row += 1
        chem_rating = bands_formula(var["bands"], chem)
        route = f'=IF({att_ref}<>"","ATTAINS category",IF({chem_rating}="","not rated","CHEM integrity fallback"))'
        rating = f'=IF({att_ref}<>"",{att_ref},{chem_rating})'
        return self._finish(S, key, rating, A, route, None,
                            "Categories 1 and 2 Good, 4A and 4B Fair, 4C and 5 Poor; category 3 and a blank fall back.")

    def _m_habitat_support_potential(self, S, method, A):
        return self._threshold(S, "m17", method, A, "Rated on the regional woody-corridor curve, national fallback.")

    def _m_streamcat_prg_bmmi(self, S, method, A):
        key = "m18"
        p = self.inputs["prGBmmi"]
        valid = f"AND(ISNUMBER({p}),{p}>=0,{p}<=1)"
        S.cell(1, method["inputs"][0]["label"]); S.cell(2, f'=IF({p}="","",{p})', fill=FILL_CALC)
        model_rating = bands_formula(method["bands"], p)
        S.cell(3, f"=IF({valid},{model_rating},\"\")", fill=FILL_CALC); mr = S.ref(3, S.row)
        S.cell(4, "=" + index_from_rating(mr, A), fill=FILL_CALC, fmt="0.000"); S.row += 1
        var = self.variant("streamcat-prg-bmmi", "streamcat-integrity-products")
        prods = var["formula"]["products"]
        prod_exprs = []
        for name, keys in prods.items():
            refs = [self.inputs[k] for k in keys]
            all_num = "AND(" + ",".join(f"ISNUMBER({r})" for r in refs) + ")"
            expr = f'IF({all_num},PRODUCT({",".join(refs)}),"")'
            S.cell(1, f"{name} (product of six {name[1:3].lower()} components)"); S.cell(2, "=" + expr, fill=FILL_CALC, fmt="0.0000")
            prod_exprs.append(S.name_cell(f"{key}_{name.lower()}", 2, S.row)); S.row += 1
        both = "AND(" + ",".join(f"ISNUMBER({r})" for r in prod_exprs) + ")"
        fb_val = f'IF({both},ROUND(MIN({",".join(prod_exprs)}),12),"")'
        S.cell(1, "Fallback value (lower of ICI and IWI)"); S.cell(2, "=" + fb_val, fill=FILL_CALC, fmt="0.0000")
        fb = S.name_cell(f"{key}_products", 2, S.row); S.row += 1
        fb_rating = bands_formula(var["bands"], fb)
        route = f'=IF({mr}<>"","published benthic model",IF({fb_rating}="","not rated","ICI/IWI integrity fallback"))'
        rating = f'=IF({mr}<>"",{mr},{fb_rating})'
        return self._finish(S, key, rating, A, route, None,
                            "The model probability governs when present; otherwise all twelve integrity components.")

    def _m_nas_established_taxa_count(self, S, method, A):
        return self._threshold(S, "m19", method, A, "Whole counts only; a fraction or a negative count is unrated.")

    def _m_nearby_dam_proximity(self, S, method, A):
        return self._threshold(S, "m20", method, A, "Whole counts only.")

    # ---- Results -----------------------------------------------------------
    def build_results(self):
        ws = self.wb.create_sheet("Results")
        S = Sheet(self.wb, ws, "Results")
        S.title("EASI calculator: results")
        S.cell(1, "Read only. Function scores, outcome sub-indices and the Ecosystem Condition Index, "
                  "computed with the STAF rollup: unrated functions leave both the numerator and the denominator.",
               font=FONT_NOTE)
        S.row += 2
        S.header("Function", "Discipline", "Metric", "Rating", "Score", "Class", "Physical", "Chemical",
                 "Biological", "Score or 0", "Rated", "Route")
        first = S.row
        for m in self.metrics:
            cells = self.result_cells[m["metricId"]]
            row = self.cwa[m["functionId"]]
            S.cell(1, m["functionName"]); S.cell(2, m["discipline"]); S.cell(3, m["name"])
            S.cell(4, f"={cells['rating']}", fill=FILL_CALC)
            S.cell(5, f"={cells['score']}", fill=FILL_CALC)
            sc = S.ref(5, S.row)
            S.cell(6, f'=IF(NOT(ISNUMBER({sc})),"Not assessed",IF({sc}<=5,"Non-Functioning",IF({sc}<=10,"Functioning-at-Risk","Functioning")))', fill=FILL_CALC)
            S.cell(7, config.WEIGHTS.get(row["physical"], 0.0)); S.cell(8, config.WEIGHTS.get(row["chemical"], 0.0))
            S.cell(9, config.WEIGHTS.get(row["biological"], 0.0))
            S.cell(10, f"=IF(ISNUMBER({sc}),{sc},0)", fill=FILL_CALC)
            S.cell(11, f"=IF(ISNUMBER({sc}),1,0)", fill=FILL_CALC)
            S.cell(12, f"={cells['route']}", fill=FILL_CALC)
            S.name_cell(f"result_score_{m['functionId'].replace('-', '_')}", 5, S.row)
            S.row += 1
        last = S.row - 1
        score0, mask = S.rng(10, first, last), S.rng(11, first, last)
        wcols = {"physical": S.rng(7, first, last), "chemical": S.rng(8, first, last), "biological": S.rng(9, first, last)}
        S.blank()
        S.header("Outcome", "Weighted score", "Weighted maximum", "Sub-index", "Displayed", "Class", "Coverage")
        sub_refs, cov_refs = [], []
        for outcome in config.OUTCOMES:
            w = wcols[outcome]
            S.cell(1, outcome.capitalize())
            S.cell(2, f"=SUMPRODUCT({score0},{w},{mask})", fill=FILL_CALC)
            S.cell(3, f"=SUMPRODUCT({w},{mask})*{config.FUNCTION_SCORE_MAX}", fill=FILL_CALC)
            wsum, wmax = S.ref(2, S.row), S.ref(3, S.row)
            S.cell(4, f'=IF({wmax}=0,"",{wsum}/{wmax})', fill=FILL_CALC, fmt="0.000000")
            sub = S.name_cell(f"sub_index_{outcome}", 4, S.row)
            S.cell(5, f'=IF({sub}="","",ROUND({sub},2))', fill=FILL_CALC, fmt="0.00")
            disp = S.name_cell(f"sub_index_{outcome}_display", 5, S.row)
            S.cell(6, f'=IF({disp}="","Not assessed",IF({disp}<=0.39,"Non-Functioning",IF({disp}<=0.69,"Functioning-at-Risk","Functioning")))', fill=FILL_CALC)
            S.cell(7, f"=IF(SUMPRODUCT({w})=0,\"\",SUMPRODUCT({w},{mask})/SUMPRODUCT({w}))", fill=FILL_CALC, fmt="0.00")
            cov_refs.append(S.name_cell(f"coverage_{outcome}", 7, S.row))
            sub_refs.append(sub)
            S.row += 1
        S.cell(1, "Ecosystem Condition Index", font=FONT_BOLD)
        eci = f"IF(COUNT({','.join(sub_refs)})=0,\"\",AVERAGE({','.join(sub_refs)}))"
        S.cell(4, "=" + eci, fill=FILL_CALC, fmt="0.000000", font=FONT_BOLD)
        eci_ref = S.name_cell("eci", 4, S.row)
        S.cell(5, f'=IF({eci_ref}="","",ROUND({eci_ref},2))', fill=FILL_CALC, fmt="0.00", font=FONT_BOLD)
        disp = S.name_cell("eci_display", 5, S.row)
        S.cell(6, f'=IF({disp}="","Not assessed",IF({disp}<=0.39,"Non-Functioning",IF({disp}<=0.69,"Functioning-at-Risk","Functioning")))', fill=FILL_CALC, font=FONT_BOLD)
        S.name_cell("eci_class", 6, S.row)
        S.row += 2
        S.header("Coverage", "Value", "", "", "", "", "")
        S.cell(1, "Functions rated"); S.cell(2, f"=SUM({mask})", fill=FILL_CALC)
        rated = S.name_cell("functions_rated", 2, S.row); S.row += 1
        S.cell(1, "Functions selected"); S.cell(2, len(self.metrics), fill=FILL_CALC)
        selected = S.name_cell("functions_selected", 2, S.row); S.row += 1
        S.cell(1, "Overall coverage"); S.cell(2, f"={rated}/{selected}", fill=FILL_CALC, fmt="0.00")
        overall = S.name_cell("coverage_overall", 2, S.row); S.row += 1
        covs = [overall] + cov_refs
        limited = "OR(" + ",".join(f"AND(ISNUMBER({c}),{c}<{fnum(COVERAGE_THRESHOLD)})" for c in covs) + ")"
        S.cell(1, "Provisional (coverage below 0.70 overall or for an outcome)")
        S.cell(2, f'=IF({limited},"yes","no")', fill=FILL_CALC)
        S.name_cell("provisional", 2, S.row); S.row += 1
        S.cell(1, "Status")
        S.cell(2, f'=IF({overall}=1,"Complete screening coverage","Partial screening coverage")', fill=FILL_CALC)
        S.name_cell("coverage_status", 2, S.row); S.row += 1
        for col, width in ((1, 34), (2, 14), (3, 44), (4, 12), (5, 10), (6, 20), (7, 10), (8, 10), (9, 10),
                           (10, 10), (11, 8), (12, 40)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = "A5"
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # ---- Instructions and Metadata ----------------------------------------
    def build_instructions(self):
        ws = self.wb.create_sheet("Instructions", 0)
        S = Sheet(self.wb, ws, "Instructions")
        S.title("EASI calculator", 16)
        lines = [
            ("Ecosystem Assessment Screening Index (EASI), Stream Tiered Assessment Framework (STAF)", True),
            (f"Calculator version {TEMPLATE_VERSION} ({TEMPLATE_DATE}). Scoring method digest {method_version()} "
             f"({self.identity.get('alternative_name')}). See the Metadata sheet.", False),
            ("", False),
            ("What it is", True),
            ("An offline implementation of the EASI screening methodology. The web application retrieves the "
             "desktop evidence for a stream reach and scores it; this workbook scores the same evidence when you "
             "enter it by hand. Same inputs give the same ratings, function scores, sub-indices and Ecosystem "
             "Condition Index, because every threshold, curve, lookup and weight here is generated from the "
             "application's own scoring definitions.", False),
            ("", False),
            ("How to use it", True),
            ("1. On the Inputs sheet, fill the site block, then the context: the NARS-9 ecoregion code of the site, "
             "the NHD feature code and, if needed, a slope class override.", False),
            ("2. Enter each desktop quantity once (yellow cells). The Source column names the StreamCat field or "
             "the EASI report item each value comes from. Leave a cell blank when the evidence is unavailable. "
             "Never enter zero for unknown: a zero is evidence.", False),
            ("3. Optional: enter the twelve EROM monthly flows to compute the monthly flow variability, and the "
             "observed channel class and bank percentages if you have field observations.", False),
            ("4. Read the Metrics sheet for each function's route, rating and score, and the Results sheet for "
             "the sub-indices, the ECI and the coverage.", False),
            ("", False),
            ("Colour key", True),
            ("Yellow: required entry. Blue: optional context or override. Grey: computed. All computed cells "
             "are locked; the sheets are protected without a secret password (it is 'easi') so that a mistaken "
             "edit is unlikely, not impossible.", False),
            ("", False),
            ("What the calculator does not do", True),
            ("It does not retrieve data, delineate a watershed or draw cross-sections. It does not implement the "
             "expert review of individual metrics that the application supports, other than the observed channel "
             "and bank overrides. Entries outside the documented ranges are flagged and left unrated.", False),
            ("", False),
            ("Rounding notes", True),
            ("Derived values from ratios, sums and products are rounded to twelve decimals before banding, as in "
             "the application. Displayed indices are rounded to two decimals; when a sub-index lands exactly on "
             "a half-hundredth, Excel rounds away from zero and the application rounds to the even digit, so the "
             "displayed value can differ by 0.01 while the class is the same. Composite inputs copied from an "
             "EASI report should be entered as the report shows them.", False),
            ("", False),
            ("Limitations", True),
            ("EASI is a screening-level desktop estimate, not a field-validated assessment. The reference curves "
             "express regional expectations derived from least-disturbed reaches and several proxies remain "
             "unvalidated; the technical report states each metric's limitations.", False),
        ]
        for text, bold in lines:
            c = S.cell(1, text, font=FONT_BOLD if bold else None, wrap=not bold)
            ws.merge_cells(start_row=S.row, start_column=1, end_row=S.row, end_column=8)
            if not bold and len(text) > 110:
                ws.row_dimensions[S.row].height = 15 * (len(text) // 110 + 1)
            S.row += 1
        ws.column_dimensions["A"].width = 120
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    def build_metadata(self, generator_sha: str):
        ws = self.wb.create_sheet("Metadata")
        S = Sheet(self.wb, ws, "Metadata")
        S.title("Metadata and version information")
        S.header("Item", "Value")
        rows = [
            ("Calculator version", TEMPLATE_VERSION),
            ("Calculator date", TEMPLATE_DATE),
            ("Scoring method digest", method_version()),
            ("Scoring identity", f"{self.identity.get('alternative_id')} ({self.identity.get('alternative_name')})"),
            ("Criteria set", config.criteria_set()),
            ("Reference curves", f"{self.identity.get('curve_count')} curves"),
            ("Catalog sha256", self.identity.get("catalog_sha256")),
            ("Curves sha256", self.identity.get("curves_sha256")),
            ("NARS-9 geography sha256", self.identity.get("nars_geography_sha256")),
            ("Rating anchors", f"Good {self.rating_index['Good']}, Fair {self.rating_index['Fair']}, Poor {self.rating_index['Poor']}"),
            ("Function scores", f"Good {self.scores['Good']}, Fair {self.scores['Fair']}, Poor {self.scores['Poor']}"),
            ("Outcome weights", f"direct {config.WEIGHTS['D']}, indirect {config.WEIGHTS['i']}"),
            ("Condition classes", "index at or below 0.39 Non-Functioning, at or below 0.69 Functioning-at-Risk, else Functioning"),
            ("Coverage threshold", str(COVERAGE_THRESHOLD)),
            ("Generator", "apps/easi/scripts/build_calculator.py"),
            ("Generator sha256", generator_sha),
            ("Generated from", "apps/easi/data/screening-methods.json, reference-curves.json, cwa-mapping.json, "
                               "easi-metrics.json, scoring-identity.json"),
            ("Application", "EASI (apps/easi), STAF, USACE-WRISES/staf"),
            ("Formula vocabulary", "IF AND OR NOT MIN MAX ROUND INDEX MATCH IFERROR ISNUMBER COUNT SUM SUMPRODUCT "
                                   "AVERAGE STDEV.P PRODUCT UPPER TRIM SUBSTITUTE LEN"),
        ]
        for k, v in rows:
            S.cell(1, k); S.cell(2, v); S.row += 1
            self.wb.defined_names[f"meta_{k.lower().replace(' ', '_').replace('-', '_')}"] = DefinedName(
                f"meta_{k.lower().replace(' ', '_').replace('-', '_')}", attr_text=S.ref(2, S.row - 1))
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 110
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # ---- assemble -----------------------------------------------------------
    def build(self, generator_sha: str) -> bytes:
        self.build_reference()
        self.build_inputs()
        self.build_metrics()
        self.build_results()
        self.build_instructions()
        self.build_metadata(generator_sha)
        order = ["Instructions", "Inputs", "Metrics", "Results", "Reference", "Metadata"]
        self.wb._sheets = [self.wb[name] for name in order]
        self.wb.active = 1
        self.wb.properties.creator = "EASI"
        self.wb.properties.lastModifiedBy = "EASI"
        self.wb.properties.created = FIXED_STAMP
        self.wb.properties.modified = FIXED_STAMP
        self.wb.calculation.fullCalcOnLoad = True
        buf = io.BytesIO()
        self.wb.save(buf)
        return repack(buf.getvalue())


def repack(source: bytes) -> bytes:
    """Rewrite the package with fixed entry timestamps so the bytes are reproducible.

    openpyxl stamps ``dcterms:modified`` with the save time regardless of the
    workbook properties, so that element is pinned here too.
    """
    import re
    stamp = FIXED_STAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                text = re.sub(r"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)", rf"\g<1>{stamp}\g<2>", text)
                text = re.sub(r"(<dcterms:created[^>]*>)[^<]*(</dcterms:created>)", rf"\g<1>{stamp}\g<2>", text)
                data = text.encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0
            zout.writestr(zi, data)
    return out.getvalue()


def generate() -> bytes:
    with open(os.path.abspath(__file__), "rb") as f:
        generator_sha = hashlib.sha256(f.read()).hexdigest()
    return Builder().build(generator_sha)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(OUT_DIR, OUT_NAME))
    ap.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = ap.parse_args()
    data = generate()
    if args.check:
        with open(args.out, "rb") as f:
            same = f.read() == data
        print("unchanged" if same else "DIFFERS")
        raise SystemExit(0 if same else 1)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"wrote {args.out} ({len(data):,} bytes, sha256 {hashlib.sha256(data).hexdigest()[:12]}, "
          f"method {method_version()})")


if __name__ == "__main__":
    main()
