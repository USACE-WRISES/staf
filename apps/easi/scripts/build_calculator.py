"""Generate the EASI Excel calculator from the application's own scoring definitions.

The workbook is never edited by hand. Every band, curve, regional threshold,
lookup, weight and anchor is read from the same files the application scores
with (``data/screening-methods.json``, ``data/reference-curves.json``,
``data/cwa-mapping.json``, ``data/easi-metrics.json``, ``data/scoring-identity.json``)
and written as worksheet formulas, so the calculator and the web application
implement one methodology. ``tests/test_calculator_parity.py`` proves that on a
retained case set scored by the application's own engine.

Sheets: Instructions, EASI Score (the worksheet the user fills, laid out like
the SFARI calculator: outcomes, functional categories, functions and metrics
side by side, orange entry cells, a scoring summary table, legends and two bar
charts), Metrics (every rule and route, read only), Results (the rollup, hidden
engine sheet), Reference, Metadata and ChartData (hidden chart series).
Formulas use direct cell references and a small function vocabulary (IF AND OR
NOT MIN MAX ROUND INDEX MATCH IFERROR ISNUMBER COUNT SUM SUMPRODUCT AVERAGE
STDEV.P PRODUCT UPPER TRIM SUBSTITUTE LEN NA) so Excel 2016 and later,
LibreOffice and the Python ``formulas`` evaluator all agree. Defined names are
added for every entry cell and reference column for readers and for the parity
harness, but no formula depends on them.

Route logic that lives in the adapters rather than the catalog (fallbacks and
observed overrides) is written out in ROUTES below and mirrors
``easi/metrics/*.py`` and ``easi/assessment.py``:

    nutrients          region known and at least one analyte, else min(CHEM) fallback
    impairment         conclusive ATTAINS category, else min(CHEM) fallback
    population support prg_bmmi0809 in [0, 1], else the ICI/IWI products (all twelve)
    channel evolution  observed class with a note > canal FCODE > worst of BHR and ER
    bank condition     observed erosion and armoring (both) > BHR proxy

Every function ends with one more entry, the Override Score: the rating select
the Assessment page shows on all 20 function cards (the registry's
``overrideable`` flag is not enforced there, so neither is it here). It replaces
the computed rating exactly as ``assessment.rescore`` does. On the two functions
with observed entries the order is the one the engine composes,
``apply_observed_evidence(rescore(...))``: complete observed entries, then the
override, then the automatic result. The row is set apart from the function's
metrics (a double rule, a band of its own, a taller row), because it is a
user-defined input and not one of the desktop quantities.

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
from openpyxl.chart import BarChart, Reference  # noqa: E402
from openpyxl.chart.data_source import NumFmt  # noqa: E402
from openpyxl.chart.label import DataLabelList  # noqa: E402
from openpyxl.chart.shapes import GraphicalProperties  # noqa: E402
from openpyxl.drawing.line import LineProperties  # noqa: E402
from openpyxl.formatting.rule import FormulaRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.workbook.defined_name import DefinedName  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from easi import calculator as served  # noqa: E402
from easi import config, scoring  # noqa: E402
from easi import screening_methods as sm  # noqa: E402
from easi.national import method_version  # noqa: E402

TEMPLATE_VERSION = served.TEMPLATE_VERSION   # one source: the module that serves the workbook
TEMPLATE_DATE = "2026-09-18"
OUT_DIR = os.path.join(ROOT, "www", "calculator")
OUT_NAME = f"EASI_Calculator_{TEMPLATE_VERSION}.xlsx"
PASSWORD = "easi"      # a guard rail against accidental edits, not a secret
FIXED_STAMP = _dt.datetime(2026, 9, 18, 0, 0, 0)
SCORE = "EASI Score"   # the worksheet the user fills

RATINGS = ("Good", "Fair", "Poor")
INDEX_EDGES = (0.39, 0.69)
COVERAGE_THRESHOLD = 0.70

# Keys that name the same quantity under two catalog names (the adapters read
# the same StreamCat field for both).
ALIASES = {"chemCat": "chemCatchment", "chemWs": "chemWatershed"}

# ---------------------------------------------------------------------------
# Palette: the SFARI calculator's worksheet look (Office theme greys for the
# bands, the accent-2 tint for entries, the same three class colours as its
# legends and charts, the same five functional-category tints).
# ---------------------------------------------------------------------------
C_INPUT = "F8CBAD"       # user entry
C_LINK = "F2F2F2"        # repeats an entry made elsewhere
C_BAND = "F2F2F2"        # band headers and the Physical block labels
C_CHEM = "D9D9D9"        # Chemical block labels
C_BIO = "BFBFBF"         # Biological block labels
C_GOOD = "8FAADC"        # Functioning
C_FAIR = "FFFF99"        # Functioning At-Risk
C_POOR = "FF6969"        # Non-Functioning
C_CALC = "EDEDED"        # computed cells on the detail sheets
CATEGORY_FILL = {"Hydrology": "D9E1F2", "Hydraulics": "B4C6E7", "Geomorphology": "FCE4D6",
                 "Physicochemistry": "FFF2CC", "Biology": "E2EFDA"}
OUTCOME_OF = {"Hydrology": "physical", "Hydraulics": "physical", "Geomorphology": "physical",
              "Physicochemistry": "chemical", "Biology": "biological"}
OUTCOME_FILL = {"physical": C_BAND, "chemical": C_CHEM, "biological": C_BIO}
OUTCOME_LABEL = {"physical": "Physical", "chemical": "Chemical", "biological": "Biological"}
CLASS_LABELS = {"Good": "Functioning", "Fair": "Functioning At-Risk", "Poor": "Non-Functioning"}


def solid(hex_: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_)


def cf_fill(hex_: str) -> PatternFill:
    """A fill for conditional formatting (Excel reads the dxf fill from start and end colours)."""
    return PatternFill(start_color=hex_, end_color=hex_, fill_type="solid")


FILL_INPUT = solid(C_INPUT)
FILL_LINK = solid(C_LINK)
FILL_BAND = solid(C_BAND)
FILL_CALC = solid(C_CALC)
FILL_HEAD = solid(C_BAND)
FONT_HEAD = Font(bold=True)
FONT_BOLD = Font(bold=True)
FONT_NOTE = Font(italic=True, color="555555", size=9)
THIN = Side(style="thin", color="000000")
MED = Side(style="medium", color="000000")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
UNLOCKED = Protection(locked=False)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
CENTER_NOWRAP = Alignment(horizontal="center", vertical="center")
LEFT_MID = Alignment(horizontal="left", vertical="center", wrap_text=True)
LEFT_TOP = Alignment(horizontal="left", vertical="top", wrap_text=True)
RIGHT_MID = Alignment(horizontal="right", vertical="center")

# ---------------------------------------------------------------------------
# Inputs: one entry per quantity. ``keys`` are the catalog input keys the entry
# serves. The worksheet places each entry under the first function that reads
# it (FRONT_ROWS) and repeats it as a linked cell under the others.
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
OBSERVED_SOURCE = "Field assessment or verified imagery"

# The Override Score: the last row of every function, the rating select of the
# Assessment page. It takes Good, Fair or Poor (the application overrides the
# rating, and the score follows: 13, 8 or 3), and it is drawn apart from the
# function's metrics because it is a user-defined input, not a desktop quantity.
SCORE_OVERRIDE_LABEL = "Override Score (Optional)"
SCORE_OVERRIDE_SOURCE = "User-defined override"     # one line, so the row keeps its own height
SCORE_OVERRIDE_NOTE = ("Optional. Pick Good, Fair or Poor to override this function's computed rating and score, as "
                       "a rating changed on the EASI Assessment page does. Leave blank to keep the computed rating.")
SCORE_OVERRIDE_NOTE_OBSERVED = ("Optional. Pick Good, Fair or Poor to override this function's computed rating and "
                                "score, as a rating changed on the EASI Assessment page does. Complete observed "
                                "entries above take precedence. Leave blank to keep the computed rating.")
SCORE_OVERRIDE_ROUTE = "override score"
C_OVERRIDE = "FDE9D9"    # the override row's band: a lighter tint of the entry orange
FILL_OVERRIDE = solid(C_OVERRIDE)
DOUBLE = Side(style="double", color="000000")
OVERRIDE_ROW_HEIGHT = 24.0


def score_override_name(mkey: str) -> str:
    """Defined name of a function's Override Score entry (``ov_m03_rating``: it holds a rating)."""
    return f"ov_{mkey}_rating"


MONTH_INPUTS = [f"m{i:02d}" for i in range(1, 13)]

# ---------------------------------------------------------------------------
# The EASI Score worksheet: which rows each function shows in the Metrics
# columns. ("in", key) is a quantity (an entry the first time it appears, a
# linked cell afterwards), ("ov", name) an observed-evidence entry, and
# ("calc", field, label, units) a computed quantity taken from the Metrics
# sheet (the value the catalog actually bands).
# ---------------------------------------------------------------------------
FRONT_ROWS = {
    "m01": [("in", "impervious"), ("in", "agriculture")],
    "m02": [("in", "woodyWetland"), ("in", "herbaceousWetland"),
            ("calc", "combined", "Wetland extent (sum, capped at 100)", "%")],
    "m03": [("in", "roadDensity")],
    "m04": [("in", "storage"), ("in", "runoff"), ("calc", "combined", "Degree of regulation", "%")],
    "m05": [("in", "flowCv")],
    "m06": [("in", "bhr")],
    "m07": [("in", "er"), ("in", "slope")],
    "m08": [("in", "slope"), ("in", "sinuosity")],
    "m09": [("in", "bhr"), ("in", "er"), ("ov", "ov_stageClass"), ("ov", "ov_indicators")],
    "m10": [("in", "bhr"), ("ov", "ov_erodingBankPct"), ("ov", "ov_armoredBankPct")],
    "m11": [("in", "agriculture"), ("in", "kFactor"), ("in", "roadDensity")],
    "m12": [("in", "agriculture")],
    "m13": [("in", "woodyRiparian"), ("in", "impervious")],
    "m14": [("in", "forest"), ("in", "shrub"), ("in", "grassland"), ("in", "wetland"),
            ("calc", "combined", "Natural corridor cover (sum, capped at 100)", "%")],
    "m15": [("in", "tn"), ("in", "tp"), ("in", "chemCatchment"), ("in", "chemWatershed")],
    "m16": [("in", "category"), ("in", "chemCatchment"), ("in", "chemWatershed")],
    "m17": [("in", "woodyRiparian")],
    "m18": [("in", "prGBmmi"), ("in", "hydCat"), ("in", "sedCat"), ("in", "connCat"), ("in", "tempCat"),
            ("in", "habtCat"), ("in", "chemCatchment"), ("in", "hydWs"), ("in", "sedWs"), ("in", "connWs"),
            ("in", "tempWs"), ("in", "habtWs"), ("in", "chemWatershed"),
            ("calc", "ici", "ICI (product of the six catchment indices)", "index"),
            ("calc", "iwi", "IWI (product of the six watershed indices)", "index")],
    "m19": [("in", "taxaCount")],
    "m20": [("in", "damCount")],
}

# Shorter labels and sources for the worksheet; the guidance note of each entry
# (shown when the cell is selected) carries the rest.
FRONT_LABELS = {
    "agriculture": "Agricultural cover of the watershed (crop, hay and pasture)",
    "kFactor": "Soil erodibility (K factor) of the watershed",
    "flowCv": "Monthly flow variability (CV of the twelve EROM monthly flows)",
    "woodyRiparian": "Woody riparian cover in the 100 m corridor",
    "forest": "Forest cover in the 100 m corridor",
    "shrub": "Shrub cover in the 100 m corridor",
    "grassland": "Grassland cover in the 100 m corridor",
    "wetland": "Wetland cover in the 100 m corridor (woody and herbaceous)",
    "bhr": "Bank-height ratio (reach median)",
    "er": "Entrenchment ratio (reach median)",
    "tn": "Total nitrogen (median of station medians)",
    "tp": "Total phosphorus (median of station medians)",
    "category": "ATTAINS integrated-report category",
    "prGBmmi": "Predicted probability of Good benthic condition",
    "hydCat": "HYD integrity index, catchment", "sedCat": "SED integrity index, catchment",
    "connCat": "CONN integrity index, catchment", "tempCat": "TEMP integrity index, catchment",
    "habtCat": "HABT integrity index, catchment", "hydWs": "HYD integrity index, watershed",
    "sedWs": "SED integrity index, watershed", "connWs": "CONN integrity index, watershed",
    "tempWs": "TEMP integrity index, watershed", "habtWs": "HABT integrity index, watershed",
    "taxaCount": "Established non-native taxa recorded in the HUC12",
    "damCount": "Mapped dams within one mile of the site",
}
FRONT_SOURCES = {
    "agriculture": "StreamCat pctcrop2019ws + pcthay2019ws",
    "flowCv": "NHDPlus V2 EROM QE_01 to QE_12, or the helper below",
    "woodyRiparian": "StreamCat forest + shrub + woody wetland at wsrp100",
    "forest": "StreamCat pctconif + pctdecid + pctmxfst at wsrp100",
    "wetland": "StreamCat pctwdwet + pcthbwet at wsrp100",
    "bhr": "EASI report cross-section block or a field survey",
    "er": "EASI report cross-section block or a field survey",
    "sinuosity": "Reach geometry (channel length over straight-line length)",
    "tn": "WQP total-fraction observations within 5 miles and 10 years",
    "tp": "WQP total-fraction observations within 5 miles and 10 years",
    "category": "EPA ATTAINS (at the reach or within 2 km)",
    "prGBmmi": "StreamCat prg_bmmi0809",
}
FRONT_NOTES = {
    "agriculture": "The application rounds the sum to two decimals. Used by Catchment hydrology, Sediment continuity "
                   "and Bed composition.",
    "woodyRiparian": "Conifer, deciduous and mixed forest, shrub and woody wetland at wsrp100, rounded to one decimal. "
                     "Used by Light and thermal regime and Habitat provision.",
    "flowCv": "Population standard deviation over the mean of the twelve EROM mean monthly flows, rounded to six "
              "decimals. Enter the value from the EASI report or compute it in the helper below the table.",
    "prGBmmi": "Population support uses the model probability directly. Leave it blank to fall back to the twelve "
               "integrity indices (ICI and IWI products).",
}

INSTRUCTIONS = [
    "Add the site information into the orange cells at the top of the EASI Score worksheet: reach ID, "
    "coordinates, date, assessor, NHDPlus V2 COMID, the NARS-9 ecoregion (drop-down) and the NHD feature code.",
    "Enter each desktop quantity once in the orange cells of the Metrics columns (column J). The Source column "
    "names the StreamCat field or the EASI report item each value comes from. Select a cell to read its guidance "
    "note.",
    "Grey cells repeat an entry made higher in the table and cannot be edited. White cells in column J are "
    "computed from the entries above them.",
    "Leave a cell blank when the evidence is unavailable. Never enter zero for unknown: a zero is evidence. A "
    "blank input leaves its function unrated, and unrated functions drop out of the sub-indices and the index.",
    "Optional: enter the twelve EROM mean monthly flows in the helper block below the table and copy the computed "
    "variability into the Low flow row, enter the observed channel class, indicators and bank percentages "
    "if you have field observations, and pick an Override Score (Good, Fair or Poor) in the last row of any "
    "function whose computed rating your review replaces.",
    "Ratings, function scores (0 to 15), the outcome sub-indices and the EASI index auto-populate (columns A to "
    "H). The scoring summary table, the legends and the charts to the right of the table also auto-populate.",
    "Record other observations, sources or assumptions in the grey box at the bottom of the worksheet.",
]
COMMENTS = [
    "The calculator implements the EASI scoring method of the web application exactly: the same bands, regional "
    "reference curves, lookups, weights and rounding, generated from the application's own scoring definitions. "
    "Same inputs give the same ratings, function scores, sub-indices and index. The Metadata sheet identifies the "
    "method.",
    "The Metrics sheet shows every input, rule and route that produced a rating. The Reference sheet lists the "
    "bands, the reference curves, the regional nutrient thresholds, the lookups and the outcome weights.",
    "Derived values (ratios, sums and products) are rounded to twelve decimals before banding, as in the "
    "application. Displayed indices are rounded to two decimals. When a sub-index lands exactly on a "
    "half-hundredth, Excel rounds away from zero and the application rounds to the even digit, so the displayed "
    "value can differ by 0.01 while the class is the same.",
    "The calculator does not retrieve data, delineate a watershed or draw cross-sections. The observed channel and "
    "bank entries and the optional Override Score of each function are the expert-review inputs it supports. An "
    "Override Score replaces the computed rating and score of its function, as a rating changed in the web "
    "application does, and the Metrics sheet keeps the computed rating beside it. Entries outside the documented "
    "ranges are refused.",
    "EASI is a screening-level desktop estimate, not a field-validated assessment. The reference curves express "
    "regional expectations derived from least-disturbed reaches, and several proxies remain unvalidated. The "
    "technical report states each metric's limitations.",
    "All computed cells are locked and the sheets are protected without a secret password (it is 'easi'), so a "
    "mistaken edit is unlikely, not impossible.",
]
DEVELOPERS = "Leanne M. Stepchinski, Gabrielle C. David, Samantha R. Wiest, and Garrett T. Menichino"
EMAILS = ("Leanne.M.Stepchinski@usace.army.mil, Gabrielle.C.David@usace.army.mil, "
          "Samantha.R.Wiest@erdc.dren.mil, Garrett.T.Menichino@usace.army.mil")


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


def link_formula(ref: str) -> str:
    """Show an entry made elsewhere, blank when it is blank."""
    return f'=IF({ref}="","",{ref})'


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
             fmt=None, unlocked=False, wrap=False, border=False, align=None):
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
        if align is not None:
            c.alignment = align
        if border is True:
            c.border = BOX
        elif border:
            c.border = border
        return c

    def header(self, *labels: str, widths: dict[int, int] | None = None):
        for i, label in enumerate(labels, 1):
            self.cell(i, label, fill=FILL_HEAD, font=FONT_HEAD, border=True)
        self.row += 1

    def title(self, text: str, size: int = 13):
        self.cell(1, text, font=Font(bold=True, size=size))
        self.row += 1

    def blank(self, n: int = 1):
        self.row += n

    def merge(self, r1: int, c1: int, r2: int, c2: int):
        if (r1, c1) != (r2, c2):
            self.ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)


def box(ws, r1: int, c1: int, r2: int, c2: int, outer: Side = MED, inner: Side | None = THIN):
    """Border a rectangle: ``outer`` on its edges, ``inner`` between its cells."""
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cell = ws.cell(row=r, column=c)
            old = cell.border
            cell.border = Border(
                left=outer if c == c1 else (inner or old.left),
                right=outer if c == c2 else (inner or old.right),
                top=outer if r == r1 else (inner or old.top),
                bottom=outer if r == r2 else (inner or old.bottom))


def side(ws, r1: int, c1: int, r2: int, c2: int, **sides: Side):
    """Override named sides (left, right, top, bottom) of every cell in a rectangle."""
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cell = ws.cell(row=r, column=c)
            old = cell.border
            cell.border = Border(left=sides.get("left", old.left), right=sides.get("right", old.right),
                                 top=sides.get("top", old.top), bottom=sides.get("bottom", old.bottom))


def lines_for(text: str, width_chars: int) -> int:
    return max(1, -(-len(text or "") // width_chars))


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
        self.qspec = {row[1]: row for row in QUANTITY_INPUTS}
        self.ovspec = {row[0]: row for row in OVERRIDE_INPUTS}
        self.ov_source = {row[0]: OBSERVED_SOURCE for row in OVERRIDE_INPUTS}
        self.ctxspec = {row[0]: row for row in CONTEXT_INPUTS}
        # the worksheet rows: FRONT_ROWS, then the Override Score as the last row of
        # every function (the Assessment page offers the rating select on all 20)
        self.front: dict[str, list[tuple]] = {}
        self.score_overrides: set[str] = set()
        for n, m in enumerate(self.metrics, 1):
            mkey = f"m{n:02d}"
            rows = list(FRONT_ROWS[mkey])
            name = score_override_name(mkey)
            observed = any(kind == "ov" for kind, *_ in rows)
            self.ovspec[name] = (name, SCORE_OVERRIDE_LABEL, "list_rating", None,
                                 SCORE_OVERRIDE_NOTE_OBSERVED if observed else SCORE_OVERRIDE_NOTE)
            self.ov_source[name] = SCORE_OVERRIDE_SOURCE
            self.score_overrides.add(name)
            rows.append(("ov", name))
            self.front[mkey] = rows
        self._check_front_rows()
        # cell references filled while writing
        self.inputs: dict[str, str] = {}       # key -> absolute ref of the entry cell
        self.anchor_ref: dict[str, str] = {}   # rating -> absolute ref of its index anchor
        self.score_ref: dict[str, str] = {}
        self.curve_rows: dict[str, tuple[int, int]] = {}
        self.curve_cols: dict[str, int] = {}
        self.result_cells: dict[str, dict[str, str]] = {}  # metricId -> {rating, index, score, ...}
        self.input_rating: dict[tuple[str, str], str] = {}  # (m01, key) -> rating cell of that input
        self.current_mkey = ""
        self.front_rows: list[tuple[str, str, str, int]] = []   # (mkey, kind, key/field, row)
        self.front_span: dict[str, tuple[int, int]] = {}        # mkey -> (first row, last row)
        self.rollup: dict[str, str] = {}                         # results refs used by the worksheet

    def _check_front_rows(self):
        """Every entry appears once, every catalog input of a method is shown under it."""
        shown = [k for rows in self.front.values() for kind, k, *_ in rows if kind == "in"]
        missing = [k for k in self.qspec if k not in shown]
        assert not missing, f"quantities without a worksheet row: {missing}"
        unknown = [k for k in shown if k not in self.qspec]
        assert not unknown, f"worksheet rows without a quantity: {unknown}"
        ov = [k for rows in self.front.values() for kind, k, *_ in rows if kind == "ov"]
        assert sorted(ov) == sorted(self.ovspec), ov
        for n, m in enumerate(self.metrics, 1):
            mkey = f"m{n:02d}"
            method = self.by_metric[m["metricId"]]
            keys = {ALIASES.get(k, k) for kind, k, *_ in FRONT_ROWS[mkey] if kind == "in"}
            for i in method.get("inputs", []):
                if i.get("required") and not i.get("contextOnly") and i["key"] in self.qspec:
                    assert i["key"] in keys, f"{mkey}: catalog input {i['key']} is not on the worksheet"
            for v in method.get("variants") or []:
                for name, comps in ((v.get("formula") or {}).get("products") or {}).items():
                    for k in comps:
                        assert ALIASES.get(k, k) in keys, f"{mkey}: product component {k} is not on the worksheet"

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
        ws.sheet_view.showGridLines = False
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

    # ---- EASI Score: layout and entry cells (first pass) --------------------
    def _validation(self, ws, kind: str, bounds, *, title: str = "", note: str = "",
                    error_title: str = "", error: str = "") -> DataValidation | None:
        """One validation per entry so each cell carries its own guidance note."""
        common = dict(allow_blank=True, showErrorMessage=True, errorStyle="stop",
                      errorTitle=error_title or "Entry refused", error=error)
        if note:
            common.update(showInputMessage=True, promptTitle=(title or "Guidance")[:32], prompt=note[:255])
        if kind == "decimal":
            dv = DataValidation(type="decimal", operator="between", formula1=fnum(bounds[0]),
                                formula2=fnum(bounds[1]), **common)
        elif kind == "whole":
            dv = DataValidation(type="whole", operator="between", formula1=str(bounds[0]),
                                formula2=str(bounds[1]), **common)
        elif kind == "list":
            dv = DataValidation(type="list", formula1='"' + ",".join(bounds) + '"', **common)
        else:
            if not note:
                return None
            dv = DataValidation(type="custom", formula1="TRUE", allow_blank=True, showErrorMessage=False,
                                showInputMessage=True, promptTitle=(title or "Guidance")[:32], prompt=note[:255])
        ws.add_data_validation(dv)
        return dv

    def _entry(self, S: Sheet, row: int, c1: int, c2: int, name: str, *, kind: str = "text", bounds=None,
               title: str = "", note: str = "", error: str = "", fmt: str | None = None) -> str:
        """An orange, unlocked entry cell (merged c1..c2) with its validation and guidance."""
        c = S.cell(c1, None, row=row, fill=FILL_INPUT, unlocked=True, align=CENTER_NOWRAP, fmt=fmt)
        S.merge(row, c1, row, c2)
        dv = self._validation(S.ws, kind, bounds, title=title, note=note, error=error)
        if dv is not None:
            dv.add(c)
        return S.name_cell(name, c1, row)

    def build_score_layout(self):
        ws = self.wb.create_sheet(SCORE)
        S = Sheet(self.wb, ws, SCORE)
        self.score_ws, self.score = ws, S
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = 80
        # title
        S.cell(1, "Ecosystem Assessment Screening Index (EASI) Worksheet", row=1, fill=FILL_BAND,
               font=Font(bold=True, size=16), align=CENTER_NOWRAP)
        S.merge(1, 1, 1, 13)
        ws.row_dimensions[1].height = 24
        # site block: label, entry (merged), label, entry, label, entry per row
        label_font = Font(bold=True, size=12)
        region_note = self.ctxspec["ctx_region"][4]
        fcode_note = self.ctxspec["ctx_fcode"][4]
        slope_note = self.ctxspec["ctx_slope_override"][4]
        site = [
            (2, 1, "Reach ID", 2, 5, "site_name", dict()),
            (2, 6, "Assessor(s)", 7, 9, "site_assessor", dict()),
            (2, 10, "COMID", 11, 13, "site_comid",
             dict(title="COMID", note="NHDPlus V2 reach identifier of the assessed reach (from the EASI report).")),
            (3, 1, "Lat/Long", 2, 5, "site_coords", dict()),
            (3, 6, "Date", 7, 9, "site_date", dict(fmt="yyyy-mm-dd")),
            (3, 10, "NARS-9 region", 11, 13, "ctx_region",
             dict(kind="list", bounds=self.regions, title="NARS-9 ecoregion", note=region_note,
                  error="Pick one of the nine NARS aggregate ecoregion codes, or leave the cell blank.")),
            (4, 1, "NHD FCODE", 2, 5, "ctx_fcode",
             dict(kind="whole", bounds=self.ctxspec["ctx_fcode"][3], title="NHD feature code", note=fcode_note,
                  error="Enter the whole-number NHDPlus V2 feature code of the reach, or leave the cell blank.")),
            (4, 6, "Slope class override", 7, 9, "ctx_slope_override",
             dict(kind="list", bounds=self.slope_classes + ["national"], title="Slope class override",
                  note=slope_note, error="Pick lt_0.5, 0.5_to_2, ge_2 or national, or leave the cell blank.")),
        ]
        for row, lc, label, c1, c2, name, opts in site:
            S.cell(lc, label, row=row, font=label_font, align=CENTER_NOWRAP)
            self.inputs[name] = self._entry(S, row, c1, c2, name, **opts)
        S.cell(10, "Slope class used", row=4, font=label_font, align=CENTER_NOWRAP)
        S.cell(11, None, row=4, align=CENTER_NOWRAP)
        S.merge(4, 11, 4, 13)
        self.inputs["ctx_slope_class"] = S.name_cell("calc_slope_class", 11, 4)
        for r in (2, 3, 4):
            ws.row_dimensions[r].height = 21
        box(ws, 2, 1, 4, 13, outer=MED, inner=THIN)
        # band headers
        for c1, c2, text in ((1, 1, "EASI"), (2, 3, "Outcomes"), (4, 5, "Functional Categories"),
                             (6, 8, "Functions"), (9, 13, "Metrics")):
            S.cell(c1, text, row=5, fill=FILL_BAND, font=label_font, align=CENTER)
            S.merge(5, c1, 5, c2)
        heads = ["Index Score", "Name", "Sub-Index Score", "Name", "Functions Rated", "Name", "Rating",
                 "Function Score", "Name", "Value", "Units", "Input Rating", "Source"]
        for c, text in enumerate(heads, 1):
            S.cell(c, text, row=6, font=label_font, align=CENTER)
            S.merge(6, c, 7, c)
        ws.row_dimensions[5].height = 18
        ws.row_dimensions[6].height = 18
        ws.row_dimensions[7].height = 18
        # body rows: the entries and linked cells (the ratings and scores come in the second pass)
        row = 8
        for n, m in enumerate(self.metrics, 1):
            mkey = f"m{n:02d}"
            first = row
            for spec in self.front[mkey]:
                kind, key = spec[0], spec[1]
                if kind == "in":
                    grp, _, label, units, qkind, bounds, source, note = self.qspec[key]
                    label = FRONT_LABELS.get(key, label)
                    S.cell(9, label, row=row, align=LEFT_MID)
                    S.cell(11, units, row=row, align=CENTER_NOWRAP)
                    S.cell(13, FRONT_SOURCES.get(key, source), row=row, align=LEFT_MID)
                    if key not in self.inputs:
                        text = FRONT_NOTES.get(key, note)
                        if qkind == "category":
                            self.inputs[key] = self._entry(
                                S, row, 10, 10, f"in_{key}", kind="list", bounds=["1", "2", "3", "4A", "4B", "4C", "5"],
                                title="ATTAINS category", note=text,
                                error="Pick 1, 2, 3, 4A, 4B, 4C or 5, or leave the cell blank.")
                        else:
                            lo, hi = bounds
                            rng_text = (f"a whole number from {lo} to {hi}" if qkind == "whole"
                                        else f"a number from {fnum(lo)} to {fnum(hi)}")
                            self.inputs[key] = self._entry(
                                S, row, 10, 10, f"in_{key}", kind=qkind, bounds=bounds, title=label[:32], note=text,
                                error=f"Enter {rng_text}, or leave the cell blank when the evidence is unavailable.")
                    else:
                        S.cell(10, link_formula(self.inputs[key]), row=row, fill=FILL_LINK, align=CENTER_NOWRAP)
                    self.front_rows.append((mkey, "in", key, row))
                elif kind == "ov":
                    name, label, okind, bounds, note = self.ovspec[key]
                    # the Override Score carries its own full label; its band, rule and
                    # height come in the second pass, after the grid borders are drawn
                    text = label if name in self.score_overrides else label + " (optional)"
                    S.cell(9, text, row=row, align=LEFT_MID)
                    S.cell(13, self.ov_source[name], row=row, align=LEFT_MID)
                    if okind == "list_rating":
                        self.inputs[name] = self._entry(S, row, 10, 10, name, kind="list", bounds=list(RATINGS),
                                                        title=label[:32], note=note,
                                                        error="Pick Good, Fair or Poor, or leave the cell blank.")
                    elif okind == "decimal":
                        S.cell(11, "%", row=row, align=CENTER_NOWRAP)
                        self.inputs[name] = self._entry(S, row, 10, 10, name, kind="decimal", bounds=bounds,
                                                        title=label[:32], note=note,
                                                        error="Enter a percentage from 0 to 100, or leave the cell blank.")
                    else:
                        self.inputs[name] = self._entry(S, row, 10, 10, name, kind="text", title=label[:32], note=note)
                    self.front_rows.append((mkey, "ov", name, row))
                else:
                    _, field, label, units = spec
                    S.cell(9, label, row=row, align=LEFT_MID)
                    S.cell(11, units, row=row, align=CENTER_NOWRAP)
                    S.cell(13, "Computed from the entries above", row=row, align=LEFT_MID, font=FONT_NOTE)
                    self.front_rows.append((mkey, "calc", field, row))
                row += 1
            self.front_span[mkey] = (first, row - 1)
        self.front_last = row - 1
        for alias, target in ALIASES.items():
            self.inputs[alias] = self.inputs[target]
        # the slope class formula now that the slope cell exists
        slope, ov = self.inputs["slope"], self.inputs["ctx_slope_override"]
        ws.cell(row=4, column=11).value = (
            f'=IF({ov}<>"",{ov},IF(NOT(ISNUMBER({slope})),"national",IF({slope}<0,"national",'
            f'IF({slope}<0.005,"lt_0.5",IF({slope}<0.02,"0.5_to_2","ge_2")))))')
        # monthly flow helper
        r = self.front_last + 2
        S.cell(1, "Monthly flow helper (optional): the twelve EROM mean monthly flows in cfs, QE_01 to QE_12. "
                  "Copy the computed variability into the Low flow row above.", row=r, font=FONT_BOLD, align=LEFT_MID)
        S.merge(r, 1, r, 13)
        S.cell(1, "Month", row=r + 1, font=FONT_BOLD, fill=FILL_BAND, align=CENTER_NOWRAP)
        S.cell(1, "Flow (cfs)", row=r + 2, font=FONT_BOLD, fill=FILL_BAND, align=CENTER_NOWRAP)
        for i, key in enumerate(MONTH_INPUTS, 1):
            col = 1 + i
            S.cell(col, f"QE_{i:02d}", row=r + 1, font=FONT_BOLD, fill=FILL_BAND, align=CENTER_NOWRAP)
            self._entry(S, r + 2, col, col, f"in_{key}", kind="decimal", bounds=(0, 1e9),
                        error="Enter the EROM mean monthly flow in cfs, or leave the cell blank.")
        rng = f"'{SCORE}'!$B${r + 2}:$M${r + 2}"
        S.cell(1, "Flow variability (CV)", row=r + 3, font=FONT_BOLD, align=CENTER)
        ws.row_dimensions[r + 3].height = 30
        S.cell(2, f'=IF(COUNT({rng})<12,"",IF(AVERAGE({rng})<=0,"",ROUND(STDEV.P({rng})/AVERAGE({rng}),6)))',
               row=r + 3, fmt="0.000000", align=CENTER_NOWRAP)
        S.name_cell("flow_cv_helper", 2, r + 3)
        S.cell(3, "All twelve flows are required and the mean must be positive. Enter the result in the Monthly "
                  "flow variability cell of Low flow and baseflow dynamics.", row=r + 3, font=FONT_NOTE, align=LEFT_MID)
        S.merge(r + 3, 3, r + 3, 13)
        box(ws, r + 1, 1, r + 3, 13, outer=MED, inner=THIN)
        self.helper_rows = (r, r + 3)
        # notes box
        n0 = r + 5
        c = S.cell(1, "Other Metrics/Notes:", row=n0, fill=FILL_BAND, unlocked=True, align=LEFT_TOP)
        S.merge(n0, 1, n0 + 9, 13)
        for rr in range(n0, n0 + 10):
            for cc in range(1, 14):
                ws.cell(row=rr, column=cc).fill = FILL_BAND
        box(ws, n0, 1, n0 + 9, 13, outer=MED, inner=None)
        self.inputs["site_notes"] = S.name_cell("site_notes", 1, n0)
        self.notes_rows = (n0, n0 + 9)

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
                  "the index anchor and the function score. Ratings follow the application's catalog exactly. "
                  "The entries live on the EASI Score sheet.",
               font=FONT_NOTE)
        S.row += 2
        A = {r: self.anchor_ref[r] for r in RATINGS}
        n = 0
        for m in self.metrics:
            n += 1
            method = self.by_metric[m["metricId"]]
            self.current_mkey = f"m{n:02d}"
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
        ws.sheet_view.showGridLines = False
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # -- shared block pieces --------------------------------------------------
    def _finish(self, S: Sheet, key: str, rating_formula: str, A: dict, route_formula: str,
                combined_formula: str | None = None, note: str = "", status_formula: str | None = None,
                blocked: str | None = None) -> dict:
        """Rows: route, combined, computed rating, override score, rating, index, score, status.

        Every function carries an Override Score entry. It replaces the computed
        rating the way ``assessment.rescore`` does, unless ``blocked`` (the complete
        observed entries of channel evolution and bank condition) holds, which is
        the engine's own order. ``computed`` in the returned refs is the automatic
        result either way, so the Input Rating column never shows an override as an
        input's rating.
        """
        cells = {}
        ov = self.inputs[score_override_name(key)]
        applies = f'OR({ov}="Good",{ov}="Fair",{ov}="Poor")'
        if blocked:
            applies = f"AND({applies},NOT({blocked}))"
        route_formula = f"=IF({applies},{q(SCORE_OVERRIDE_ROUTE)},{route_formula[1:]})"
        S.cell(1, "Route that applied"); S.cell(2, route_formula, fill=FILL_CALC)
        cells["route"] = S.name_cell(f"{key}_route", 2, S.row); S.row += 1
        if combined_formula is not None:
            S.cell(1, "Combined value"); S.cell(2, combined_formula, fill=FILL_CALC)
            cells["combined"] = S.name_cell(f"{key}_combined", 2, S.row); S.row += 1
        S.cell(1, "Computed rating (before the override score)"); S.cell(3, rating_formula, fill=FILL_CALC)
        cells["computed"] = S.name_cell(f"{key}_computed", 3, S.row); S.row += 1
        S.cell(1, "Override score (optional entry on the EASI Score sheet)")
        S.cell(2, link_formula(ov), fill=FILL_CALC)
        S.cell(5, "Replaces the computed rating, as a rating changed in the web application does."
                  + (" Complete observed entries take precedence." if blocked else ""),
               font=FONT_NOTE, wrap=True)
        S.row += 1
        S.cell(1, "Function rating")
        S.cell(3, f"=IF({applies},{ov},{cells['computed']})", fill=FILL_CALC, font=FONT_BOLD)
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
        status_formula = status_formula or f'=IF({r}="","not rated","complete")'
        # the automatic status describes the computed rating, which an override replaces
        status_formula = f"=IF({applies},{q(SCORE_OVERRIDE_ROUTE)},{status_formula[1:]})"
        S.cell(2, status_formula, fill=FILL_CALC)
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
            self.input_rating[(self.current_mkey, key)] = rat
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
        cells = self._finish(S, key, rating, A, "=" + q("automatic method"),
                             note="All classes are required. A missing class is unknown, not zero.")
        cells["combined"] = comb
        self.input_rating[(key, "combined")] = cells["computed"]
        return cells

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
        cells = self._finish(S, key, rating, A, "=" + q("automatic method"), note=note)
        self.input_rating[(key, inp["key"])] = cells["computed"]
        return cells

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
        cells = self._finish(S, "m04", rating, A, "=" + q("automatic method"))
        cells["combined"] = comb
        self.input_rating[("m04", "combined")] = cells["computed"]
        return cells

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
                            "Observed class with a note replaces the proxy; canal and ditch feature codes rate Poor.",
                            blocked=observed)

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
                            "Both observed bank percentages replace the BHR proxy; the worse component governs.",
                            blocked=observed)

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
        self.input_rating[(key, "category")] = att_ref
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
        self.input_rating[(key, "prGBmmi")] = mr
        var = self.variant("streamcat-prg-bmmi", "streamcat-integrity-products")
        prods = var["formula"]["products"]
        prod_exprs, prod_refs = [], {}
        for name, keys in prods.items():
            refs = [self.inputs[k] for k in keys]
            all_num = "AND(" + ",".join(f"ISNUMBER({r})" for r in refs) + ")"
            expr = f'IF({all_num},PRODUCT({",".join(refs)}),"")'
            S.cell(1, f"{name} (product of six {name[1:3].lower()} components)"); S.cell(2, "=" + expr, fill=FILL_CALC, fmt="0.0000")
            ref = S.name_cell(f"{key}_{name.lower()}", 2, S.row)
            prod_exprs.append(ref); prod_refs[name.lower()] = ref; S.row += 1
        both = "AND(" + ",".join(f"ISNUMBER({r})" for r in prod_exprs) + ")"
        fb_val = f'IF({both},ROUND(MIN({",".join(prod_exprs)}),12),"")'
        S.cell(1, "Fallback value (lower of ICI and IWI)"); S.cell(2, "=" + fb_val, fill=FILL_CALC, fmt="0.0000")
        fb = S.name_cell(f"{key}_products", 2, S.row); S.row += 1
        fb_rating = bands_formula(var["bands"], fb)
        route = f'=IF({mr}<>"","published benthic model",IF({fb_rating}="","not rated","ICI/IWI integrity fallback"))'
        rating = f'=IF({mr}<>"",{mr},{fb_rating})'
        cells = self._finish(S, key, rating, A, route, None,
                             "The model probability governs when present; otherwise all twelve integrity components.")
        cells.update(prod_refs)
        return cells

    def _m_nas_established_taxa_count(self, S, method, A):
        return self._threshold(S, "m19", method, A, "Whole counts only; a fraction or a negative count is unrated.")

    def _m_nearby_dam_proximity(self, S, method, A):
        return self._threshold(S, "m20", method, A, "Whole counts only.")

    # ---- Results (hidden engine sheet) --------------------------------------
    def build_results(self):
        ws = self.wb.create_sheet("Results")
        S = Sheet(self.wb, ws, "Results")
        S.title("EASI calculator: results")
        S.cell(1, "Engine sheet (hidden). Function scores, outcome sub-indices and the Ecosystem Condition Index, "
                  "computed with the STAF rollup: unrated functions leave both the numerator and the denominator. "
                  "The EASI Score sheet displays these cells.",
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
            self.rollup[f"sub_index_{outcome}_display"] = disp
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
        self.rollup["eci_display"] = disp
        S.cell(6, f'=IF({disp}="","Not assessed",IF({disp}<=0.39,"Non-Functioning",IF({disp}<=0.69,"Functioning-at-Risk","Functioning")))', fill=FILL_CALC, font=FONT_BOLD)
        self.rollup["eci_class"] = S.name_cell("eci_class", 6, S.row)
        S.row += 2
        S.header("Coverage", "Value", "", "", "", "", "")
        S.cell(1, "Functions rated"); S.cell(2, f"=SUM({mask})", fill=FILL_CALC)
        rated = S.name_cell("functions_rated", 2, S.row); S.row += 1
        self.rollup["functions_rated"] = rated
        S.cell(1, "Functions selected"); S.cell(2, len(self.metrics), fill=FILL_CALC)
        selected = S.name_cell("functions_selected", 2, S.row); S.row += 1
        S.cell(1, "Overall coverage"); S.cell(2, f"={rated}/{selected}", fill=FILL_CALC, fmt="0.00")
        overall = S.name_cell("coverage_overall", 2, S.row); S.row += 1
        self.rollup["coverage_overall"] = overall
        covs = [overall] + cov_refs
        limited = "OR(" + ",".join(f"AND(ISNUMBER({c}),{c}<{fnum(COVERAGE_THRESHOLD)})" for c in covs) + ")"
        S.cell(1, "Provisional (coverage below 0.70 overall or for an outcome)")
        S.cell(2, f'=IF({limited},"yes","no")', fill=FILL_CALC)
        self.rollup["provisional"] = S.name_cell("provisional", 2, S.row); S.row += 1
        S.cell(1, "Status")
        S.cell(2, f'=IF({overall}=1,"Complete screening coverage","Partial screening coverage")', fill=FILL_CALC)
        self.rollup["coverage_status"] = S.name_cell("coverage_status", 2, S.row); S.row += 1
        for col, width in ((1, 34), (2, 14), (3, 44), (4, 12), (5, 10), (6, 20), (7, 10), (8, 10), (9, 10),
                           (10, 10), (11, 8), (12, 40)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = "A5"
        ws.sheet_state = "hidden"
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # ---- EASI Score: ratings, scores, rollup, summary, legends, charts (second pass)
    def build_score_results(self):
        ws, S = self.score_ws, self.score
        first_body, last_body = 8, self.front_last
        score_font = Font(bold=True, size=12)
        # function columns and per-row ratings
        metric_rows = []   # (mkey, metric, first, last)
        for n, m in enumerate(self.metrics, 1):
            mkey = f"m{n:02d}"
            first, last = self.front_span[mkey]
            cells = self.result_cells[m["metricId"]]
            outcome = OUTCOME_OF[m["discipline"]]
            assert self.cwa[m["functionId"]][outcome] == "D", (m["functionId"], outcome)
            fill = solid(OUTCOME_FILL[outcome])
            S.cell(6, m["functionName"], row=first, fill=fill, align=CENTER, font=Font(size=12))
            S.merge(first, 6, last, 6)
            S.cell(7, f"={cells['rating']}", row=first, align=CENTER_NOWRAP, font=score_font)
            S.merge(first, 7, last, 7)
            S.cell(8, f"={cells['score']}", row=first, align=CENTER_NOWRAP, font=score_font, fmt="0")
            S.merge(first, 8, last, 8)
            metric_rows.append((mkey, m, first, last))
            for r in range(first, last + 1):
                ws.cell(row=r, column=9).fill = fill
        for mkey, kind, key, row in self.front_rows:
            if kind == "calc":
                cells = self.result_cells[next(m["metricId"] for n, m in enumerate(self.metrics, 1) if f"m{n:02d}" == mkey)]
                S.cell(10, link_formula(cells[key]), row=row, align=CENTER_NOWRAP,
                       fmt="0.0000" if key in ("ici", "iwi") else "0.00")
            rat = self.input_rating.get((mkey, key)) or (self.input_rating.get((mkey, "combined"))
                                                          if kind == "calc" and key == "combined" else None)
            if rat:
                S.cell(12, f"={rat}", row=row, align=CENTER_NOWRAP)
        # categories and outcomes: merged label cells with the SFARI greys
        cat_rows = []      # (discipline, first, last)
        for mkey, m, first, last in metric_rows:
            if cat_rows and cat_rows[-1][0] == m["discipline"]:
                cat_rows[-1] = (m["discipline"], cat_rows[-1][1], last)
            else:
                cat_rows.append((m["discipline"], first, last))
        out_rows = []      # (outcome, first, last)
        for disc, first, last in cat_rows:
            outcome = OUTCOME_OF[disc]
            if out_rows and out_rows[-1][0] == outcome:
                out_rows[-1] = (outcome, out_rows[-1][1], last)
            else:
                out_rows.append((outcome, first, last))
        for disc, first, last in cat_rows:
            fill = solid(OUTCOME_FILL[OUTCOME_OF[disc]])
            S.cell(4, disc, row=first, fill=fill, align=CENTER, font=Font(size=12))
            S.merge(first, 4, last, 4)
            n_funcs = sum(1 for _, m, *_ in metric_rows if m["discipline"] == disc)
            S.cell(5, f"=COUNT('{SCORE}'!$H${first}:$H${last})", row=first, align=CENTER_NOWRAP,
                   fmt=f'0" of {n_funcs}"', font=Font(size=12))
            S.merge(first, 5, last, 5)
        for outcome, first, last in out_rows:
            fill = solid(OUTCOME_FILL[outcome])
            S.cell(2, OUTCOME_LABEL[outcome], row=first, fill=fill, align=CENTER, font=Font(size=12))
            S.merge(first, 2, last, 2)
            S.cell(3, link_formula(self.rollup[f"sub_index_{outcome}_display"]), row=first, align=CENTER_NOWRAP,
                   fmt="0.00", font=score_font)
            S.merge(first, 3, last, 3)
        S.cell(1, link_formula(self.rollup["eci_display"]), row=first_body, align=CENTER_NOWRAP, fmt="0.00",
               font=Font(bold=True, size=14))
        S.merge(first_body, 1, last_body, 1)
        # borders: thin grid, medium around the groups of columns and the outcomes
        box(ws, 5, 1, 7, 13, outer=MED, inner=THIN)
        box(ws, first_body, 1, last_body, 13, outer=MED, inner=THIN)
        for col in (2, 4, 6, 9):
            side(ws, 5, col, last_body, col, left=MED)
        for _, first, last in out_rows:
            side(ws, last, 1, last, 13, bottom=MED)
        for _, first, last in cat_rows:
            side(ws, last, 4, last, 13, bottom=MED)
        for mkey, m, first, last in metric_rows:
            side(ws, last, 6, last, 13, bottom=MED)
        # the Override Score row, the last of every function: set apart from the function's
        # metrics as a user-defined input. A double rule above it, a band of its own across
        # the Metrics columns (the entry keeps the entry orange), a bold label and a taller
        # row. After the fills and borders above, which would otherwise paint over it.
        override_rows = {row for _mkey, kind, key, row in self.front_rows
                         if kind == "ov" and key in self.score_overrides}
        assert len(override_rows) == len(self.metrics), "one Override Score row per function"
        for row in override_rows:
            for col in (9, 11, 12, 13):
                ws.cell(row=row, column=col).fill = FILL_OVERRIDE
            label = ws.cell(row=row, column=9)
            label.font = Font(bold=True, italic=True)
            # flush against its entry cell, like a form prompt: the metric names above are
            # left-aligned descriptions, this is a field the user may fill
            label.alignment = Alignment(horizontal="right", vertical="center", indent=1)
            ws.cell(row=row, column=13).font = Font(italic=True)
            side(ws, row - 1, 9, row - 1, 13, bottom=DOUBLE)     # both edges: Excel draws the heavier one
            side(ws, row, 9, row, 13, top=DOUBLE)
        # row heights follow the longest wrapped text in the row
        for r in range(first_body, last_body + 1):
            lines = max(lines_for(str(ws.cell(row=r, column=9).value or ""), 46),
                        lines_for(str(ws.cell(row=r, column=13).value or ""), 40))
            ws.row_dimensions[r].height = max(15.75 * lines, OVERRIDE_ROW_HEIGHT if r in override_rows else 0)
        for mkey, m, first, last in metric_rows:
            need = lines_for(m["functionName"], 24)
            have = sum((ws.row_dimensions[r].height or 15.75) for r in range(first, last + 1))
            if have < 15.75 * need:
                ws.row_dimensions[last].height = (ws.row_dimensions[last].height or 15.75) + 15.75 * need - have
        # conditional formatting: the three classes, as in the legends and charts
        good, fair, poor = cf_fill(C_GOOD), cf_fill(C_FAIR), cf_fill(C_POOR)
        text_rules = lambda col: [  # noqa: E731
            FormulaRule(formula=[f'${col}8="Good"'], fill=good),
            FormulaRule(formula=[f'${col}8="Fair"'], fill=fair),
            FormulaRule(formula=[f'${col}8="Poor"'], fill=poor)]
        score_rules = lambda col, r: [  # noqa: E731
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}>10)'], fill=good),
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}>5,${col}{r}<=10)'], fill=fair),
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}<=5)'], fill=poor)]
        index_rules = lambda col, r: [  # noqa: E731
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}>0.69)'], fill=good),
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}>0.39,${col}{r}<=0.69)'], fill=fair),
            FormulaRule(formula=[f'AND(ISNUMBER(${col}{r}),${col}{r}<=0.39)'], fill=poor)]
        for col in ("G", "L"):
            for rule in text_rules(col):
                ws.conditional_formatting.add(f"{col}{first_body}:{col}{last_body}", rule)
        for rule in score_rules("H", first_body):
            ws.conditional_formatting.add(f"H{first_body}:H{last_body}", rule)
        for col in ("A", "C"):
            for rule in index_rules(col, first_body):
                ws.conditional_formatting.add(f"{col}{first_body}:{col}{last_body}", rule)
        # scoring summary table (columns O to T)
        self._summary_table(metric_rows, cat_rows, score_rules, index_rules)
        self._legends()
        self._charts(metric_rows)
        # widths, panes, print setup, protection
        for col, width in ((1, 15.6), (2, 13), (3, 13), (4, 17), (5, 13), (6, 27), (7, 10), (8, 12), (9, 46),
                           (10, 16), (11, 10), (12, 12), (13, 40), (14, 3), (15, 17), (16, 32), (17, 8),
                           (18, 10), (19, 10), (20, 11), (21, 3), (22, 8), (23, 20), (24, 3), (25, 8), (26, 20)):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.freeze_panes = "A8"
        ws.print_area = f"A1:Z{self.notes_rows[1]}"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    def _summary_table(self, metric_rows, cat_rows, score_rules, index_rules):
        ws, S = self.score_ws, self.score
        c0 = 15   # column O
        head_font = Font(bold=True, size=12)
        S.cell(c0 + 3, "Outcomes", row=5, font=head_font, align=CENTER_NOWRAP)
        S.merge(5, c0 + 3, 5, c0 + 5)
        S.cell(c0, "Scoring Summary", row=5, font=head_font, align=CENTER_NOWRAP)
        S.merge(5, c0, 5, c0 + 2)
        for i, text in enumerate(("Functional Categories", "Functions", "Score", "Physical", "Chemical", "Biological")):
            S.cell(c0 + i, text, row=6, font=head_font, align=CENTER)
            S.merge(6, c0 + i, 7, c0 + i)
        row = 8
        first = row
        for mkey, m, gfirst, glast in metric_rows:
            fill = solid(CATEGORY_FILL[m["discipline"]])
            S.cell(c0 + 1, m["functionName"], row=row, fill=fill, align=LEFT_MID, font=Font(size=10))
            S.cell(c0 + 2, f"=IF('{SCORE}'!$H${gfirst}=\"\",\"\",'{SCORE}'!$H${gfirst})", row=row,
                   align=CENTER_NOWRAP, font=Font(bold=True, size=10), fmt="0")
            weights = self.cwa[m["functionId"]]
            for j, outcome in enumerate(("physical", "chemical", "biological")):
                mark = weights[outcome]
                S.cell(c0 + 3 + j, "" if mark == "-" else mark, row=row, fill=fill, align=CENTER_NOWRAP,
                       font=Font(size=10))
            row += 1
        last = row - 1
        r = first
        for disc, *_ in cat_rows:
            n = sum(1 for _, m, *_ in metric_rows if m["discipline"] == disc)
            S.cell(c0, disc, row=r, fill=solid(CATEGORY_FILL[disc]), align=CENTER, font=Font(size=10))
            S.merge(r, c0, r + n - 1, c0)
            for rr in range(r, r + n):
                ws.cell(row=rr, column=c0).fill = solid(CATEGORY_FILL[disc])
            r += n
        box(ws, 5, c0, 7, c0 + 5, outer=THIN, inner=THIN)
        box(ws, first, c0, last, c0 + 5, outer=THIN, inner=THIN)
        for rule in score_rules(get_column_letter(c0 + 2), first):
            ws.conditional_formatting.add(f"{get_column_letter(c0 + 2)}{first}:{get_column_letter(c0 + 2)}{last}", rule)
        # sub-indices, index, coverage
        r = last + 1
        bold = Font(bold=True, size=11)
        S.cell(c0, "Sub-index Scores", row=r, font=bold, align=RIGHT_MID); S.merge(r, c0, r, c0 + 2)
        for j, outcome in enumerate(("physical", "chemical", "biological")):
            S.cell(c0 + 3 + j, link_formula(self.rollup[f"sub_index_{outcome}_display"]), row=r, font=bold,
                   align=CENTER_NOWRAP, fmt="0.00")
        S.cell(c0, "EASI Index", row=r + 1, font=bold, align=RIGHT_MID); S.merge(r + 1, c0, r + 1, c0 + 2)
        S.cell(c0 + 3, link_formula(self.rollup["eci_display"]), row=r + 1, font=Font(bold=True, size=12),
               align=CENTER_NOWRAP, fmt="0.00")
        S.merge(r + 1, c0 + 3, r + 1, c0 + 5)
        S.cell(c0, "Functions rated", row=r + 2, font=bold, align=RIGHT_MID); S.merge(r + 2, c0, r + 2, c0 + 2)
        S.cell(c0 + 3, f"={self.rollup['functions_rated']}", row=r + 2, align=CENTER_NOWRAP,
               fmt=f'0" of {len(self.metrics)}"')
        S.merge(r + 2, c0 + 3, r + 2, c0 + 5)
        S.cell(c0, "Coverage", row=r + 3, font=bold, align=RIGHT_MID); S.merge(r + 3, c0, r + 3, c0 + 2)
        S.cell(c0 + 3, f"={self.rollup['coverage_status']}", row=r + 3, align=CENTER_NOWRAP)
        S.merge(r + 3, c0 + 3, r + 3, c0 + 5)
        S.cell(c0, "Provisional result", row=r + 4, font=bold, align=RIGHT_MID); S.merge(r + 4, c0, r + 4, c0 + 2)
        S.cell(c0 + 3, f'=IF({self.rollup["provisional"]}="yes","yes (coverage below 0.70)","no")', row=r + 4,
               align=CENTER_NOWRAP)
        S.merge(r + 4, c0 + 3, r + 4, c0 + 5)
        box(ws, r, c0, r + 4, c0 + 5, outer=THIN, inner=THIN)
        col = get_column_letter(c0 + 3)
        for rule in index_rules(col, r):
            ws.conditional_formatting.add(f"{col}{r}:{get_column_letter(c0 + 5)}{r + 1}", rule)
        self.summary_rows = (5, r + 4)

    def _legends(self):
        """The SFARI legends drawn in cells: index scoring, function scoring, rating to score."""
        ws, S = self.score_ws, self.score
        v, w = 22, 23    # columns V, W
        y, z = 25, 26    # columns Y, Z
        head = Font(bold=True, size=11)
        S.cell(v, "EASI Index Scoring", row=5, font=head, align=CENTER_NOWRAP); S.merge(5, v, 5, w)
        S.cell(y, "Function Scoring", row=5, font=head, align=CENTER_NOWRAP); S.merge(5, y, 5, z)
        blocks = ((C_GOOD, "Functioning", "1.00", "0.70", "15", "11"),
                  (C_FAIR, "Functioning At-Risk", None, "0.40", "10", "6"),
                  (C_POOR, "Non-Functioning", None, "0.00", "5", "0"))
        r = 6
        for color, label, top, bottom, ftop, fbottom in blocks:
            for col, t, b in ((w, top, bottom), (z, ftop, fbottom)):
                S.cell(col, label, row=r, fill=solid(color), font=Font(bold=True, size=10), align=CENTER)
                S.merge(r, col, r + 2, col)
                for rr in range(r, r + 3):
                    ws.cell(row=rr, column=col).fill = solid(color)
                edge = col - 1
                if t is not None:
                    S.cell(edge, t, row=r, align=Alignment(horizontal="right", vertical="top"), font=Font(size=9))
                S.cell(edge, b, row=r + 2, align=Alignment(horizontal="right", vertical="bottom"), font=Font(size=9))
            r += 3
        box(ws, 6, w, 14, w, outer=THIN, inner=THIN)
        box(ws, 6, z, 14, z, outer=THIN, inner=THIN)
        # rating to function score
        r = 17
        S.cell(v, "Rating to Function Score", row=r, font=head, align=CENTER_NOWRAP); S.merge(r, v, r, z)
        S.cell(v, "Rating", row=r + 1, font=Font(bold=True, size=10), align=CENTER_NOWRAP); S.merge(r + 1, v, r + 1, w)
        S.cell(y, "Score", row=r + 1, font=Font(bold=True, size=10), align=CENTER_NOWRAP); S.merge(r + 1, y, r + 1, z)
        rr = r + 2
        for rating, color in (("Good", C_GOOD), ("Fair", C_FAIR), ("Poor", C_POOR)):
            S.cell(v, f"{rating} ({CLASS_LABELS[rating]})", row=rr, fill=solid(color), align=CENTER_NOWRAP,
                   font=Font(size=10))
            S.merge(rr, v, rr, w)
            ws.cell(row=rr, column=w).fill = solid(color)
            S.cell(y, f"={self.score_ref[rating]}", row=rr, fill=solid(color), align=CENTER_NOWRAP,
                   font=Font(bold=True, size=10), fmt="0")
            S.merge(rr, y, rr, z)
            ws.cell(row=rr, column=z).fill = solid(color)
            rr += 1
        S.cell(v, "Not rated (blank input)", row=rr, align=CENTER_NOWRAP, font=Font(size=10)); S.merge(rr, v, rr, w)
        S.cell(y, "excluded from the rollup", row=rr, align=CENTER_NOWRAP, font=Font(size=10)); S.merge(rr, y, rr, z)
        box(ws, r + 1, v, rr, z, outer=THIN, inner=THIN)

    def _charts(self, metric_rows):
        """ChartData (hidden) and the two bar charts of the SFARI worksheet."""
        cd = self.wb.create_sheet("ChartData")
        C = Sheet(self.wb, cd, "ChartData")
        C.cell(1, "Chart series (hidden). One column per class so each bar takes the class colour.", font=FONT_NOTE)
        heads = ["Function", "Score", "Non-Functioning (0-5)", "Functioning At-Risk (6-10)", "Functioning (11-15)"]
        for c, text in enumerate(heads, 1):
            C.cell(c, text, row=2, font=FONT_BOLD)
        r = 3
        for mkey, m, gfirst, glast in metric_rows:
            C.cell(1, m["functionName"], row=r)
            C.cell(2, link_formula(f"'{SCORE}'!$H${gfirst}"), row=r)
            s = C.ref(2, r)
            C.cell(3, f"=IF(ISNUMBER({s}),IF({s}<=5,{s},NA()),NA())", row=r)
            C.cell(4, f"=IF(ISNUMBER({s}),IF(AND({s}>5,{s}<=10),{s},NA()),NA())", row=r)
            C.cell(5, f"=IF(ISNUMBER({s}),IF({s}>10,{s},NA()),NA())", row=r)
            r += 1
        f_first, f_last = 3, r - 1
        r += 1
        heads = ["Outcome", "Score", "Non-Functioning (0.00-0.39)", "Functioning At-Risk (0.40-0.69)",
                 "Functioning (0.70-1.00)"]
        for c, text in enumerate(heads, 1):
            C.cell(c, text, row=r, font=FONT_BOLD)
        o_head = r
        r += 1
        for outcome in ("physical", "chemical", "biological"):
            C.cell(1, OUTCOME_LABEL[outcome], row=r)
            C.cell(2, link_formula(self.rollup[f"sub_index_{outcome}_display"]), row=r)
            s = C.ref(2, r)
            C.cell(3, f"=IF(ISNUMBER({s}),IF({s}<=0.39,{s},NA()),NA())", row=r)
            C.cell(4, f"=IF(ISNUMBER({s}),IF(AND({s}>0.39,{s}<=0.69),{s},NA()),NA())", row=r)
            C.cell(5, f"=IF(ISNUMBER({s}),IF({s}>0.69,{s},NA()),NA())", row=r)
            r += 1
        o_first, o_last = o_head + 1, r - 1
        cd.column_dimensions["A"].width = 34
        cd.sheet_state = "hidden"
        cd.protection.sheet = True
        cd.protection.password = PASSWORD

        def bar(title, head_row, first, last, vmax, unit, fmt, height):
            ch = BarChart()
            ch.type = "bar"
            ch.grouping = "clustered"
            ch.overlap = 100
            ch.gapWidth = 40
            ch.title = title
            ch.title.overlay = False
            ch.style = 2
            ch.add_data(Reference(cd, min_col=3, max_col=5, min_row=head_row, max_row=last), titles_from_data=True)
            ch.set_categories(Reference(cd, min_col=1, min_row=first, max_row=last))
            for s, color in zip(ch.series, (C_POOR, C_FAIR, C_GOOD)):
                s.graphicalProperties = GraphicalProperties(solidFill=color)
                s.graphicalProperties.line = LineProperties(solidFill="000000")
            ch.x_axis.scaling.orientation = "maxMin"
            ch.x_axis.delete = False
            ch.y_axis.delete = False
            ch.y_axis.scaling.min = 0
            ch.y_axis.scaling.max = vmax
            ch.y_axis.majorUnit = unit
            ch.y_axis.numFmt = NumFmt(formatCode=fmt, sourceLinked=False)
            ch.y_axis.majorGridlines = None
            ch.dataLabels = DataLabelList()
            ch.dataLabels.showVal = True
            ch.dataLabels.showSerName = False
            ch.dataLabels.showCatName = False
            ch.dataLabels.showLegendKey = False
            ch.legend.position = "b"
            ch.legend.overlay = False
            ch.width, ch.height = 24, height
            return ch

        chart_row = self.summary_rows[1] + 2
        self.score_ws.add_chart(bar("Function Score", 2, f_first, f_last, 15, 1, "0", 13), f"O{chart_row}")
        self.score_ws.add_chart(bar("Outcome Score", o_head, o_first, o_last, 1, 0.1, "0.00", 6.5),
                                f"O{chart_row + 27}")

    # ---- Instructions and Metadata ----------------------------------------
    def build_instructions(self):
        ws = self.wb.create_sheet("Instructions", 0)
        S = Sheet(self.wb, ws, "Instructions")
        ws.sheet_view.showGridLines = False
        label = Font(name="Arial", size=10, bold=True)
        body = Font(name="Arial", size=10)
        rows = [
            (1, "Model Name", "Ecosystem Assessment Screening Index (EASI)", label),
            (2, "Developers", DEVELOPERS, None),
            (3, "", "U.S. Army Engineer Research and Development Center, Environmental Laboratory", None),
            (4, "", "Vicksburg, MS", None),
            (5, "", EMAILS, None),
            (7, "Model Version", f"EASI calculator v{TEMPLATE_VERSION}, scoring method {method_version()} "
                                 f"({self.identity.get('alternative_name')})", body),
            (8, "Date of Last Update", _dt.datetime.strptime(TEMPLATE_DATE, "%Y-%m-%d"), None),
            (10, "Waiver", 'This model is provided for free as part of the USACE Technical Report "Ecosystem '
                           'Assessment Screening Index (EASI)" by ' + DEVELOPERS + ". It ships with the STAF web "
                           "application, whose EASI Assessment page offers the same download under Get Forms, blank "
                           "or completed with a site's values.", body),
            (11, "", "None of the authors nor the US Army Corps of Engineers accepts responsibility or liability "
                     "for the model's use by third parties.", body),
        ]
        for r, a, b, font in rows:
            S.cell(1, a or None, row=r, font=label, align=Alignment(vertical="center"))
            c = S.cell(2, b, row=r, font=font, align=LEFT_MID)
            if r == 8:
                c.number_format = "mmmm d, yyyy"
                c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[10].height = 45
        box(ws, 1, 1, 5, 2, outer=THIN, inner=THIN)
        box(ws, 7, 1, 8, 2, outer=THIN, inner=THIN)
        box(ws, 10, 1, 11, 2, outer=THIN, inner=THIN)
        r = 13
        S.cell(1, "Instructions", row=r, font=label, align=CENTER_NOWRAP); S.merge(r, 1, r, 2)
        first = r
        for i, text in enumerate(INSTRUCTIONS, 1):
            r += 1
            S.cell(1, f"{i})", row=r, align=Alignment(horizontal="center", vertical="center"))
            S.cell(2, text, row=r, align=LEFT_MID)
            ws.row_dimensions[r].height = 15 * lines_for(text, 88)
        box(ws, first, 1, r, 2, outer=THIN, inner=THIN)
        r += 2
        S.cell(1, "Comments/Assumptions", row=r, font=label, align=CENTER_NOWRAP); S.merge(r, 1, r, 2)
        first = r
        for i, text in enumerate(COMMENTS, 1):
            r += 1
            S.cell(1, f"{i})", row=r, align=Alignment(horizontal="center", vertical="center"))
            S.cell(2, text, row=r, align=LEFT_MID)
            ws.row_dimensions[r].height = 15 * lines_for(text, 88)
        box(ws, first, 1, r, 2, outer=THIN, inner=THIN)
        # colour coding key
        S.cell(4, "Color Coding Key:", row=1)
        for rr, text, fill in ((2, "User Input", FILL_INPUT), (3, "Calculated", None),
                               (4, "Linked to Another Cell", FILL_LINK),
                               (5, "Override Score Row", FILL_OVERRIDE)):
            S.cell(4, text, row=rr, fill=fill, align=CENTER_NOWRAP, border=True)
        S.cell(4, "Select an orange cell to read its guidance note.", row=6, font=FONT_NOTE)
        S.cell(4, "Sheets: EASI Score (entries and results), Metrics (every rule and route), "
                  "Reference (bands, curves, weights), Metadata (method identity).", row=7, font=FONT_NOTE)
        ws.column_dimensions["A"].width = 20
        ws.column_dimensions["B"].width = 90
        ws.column_dimensions["C"].width = 4
        ws.column_dimensions["D"].width = 28
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
            ("Worksheet layout", "EASI Score worksheet modelled on the SFARI calculator (entries, summary table, "
                                 "legends and charts on one sheet); Metrics and Results carry the formulas"),
            ("Generator", "apps/easi/scripts/build_calculator.py"),
            ("Generator sha256", generator_sha),
            ("Generated from", "apps/easi/data/screening-methods.json, reference-curves.json, cwa-mapping.json, "
                               "easi-metrics.json, scoring-identity.json"),
            ("Application", "EASI (apps/easi), STAF, USACE-WRISES/staf"),
            ("Formula vocabulary", "IF AND OR NOT MIN MAX ROUND INDEX MATCH IFERROR ISNUMBER COUNT SUM SUMPRODUCT "
                                   "AVERAGE STDEV.P PRODUCT UPPER TRIM SUBSTITUTE LEN NA"),
        ]
        for k, v in rows:
            S.cell(1, k, border=True); S.cell(2, v, border=True); S.row += 1
            self.wb.defined_names[f"meta_{k.lower().replace(' ', '_').replace('-', '_')}"] = DefinedName(
                f"meta_{k.lower().replace(' ', '_').replace('-', '_')}", attr_text=S.ref(2, S.row - 1))
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 110
        ws.sheet_view.showGridLines = False
        ws.protection.sheet = True
        ws.protection.password = PASSWORD

    # ---- assemble -----------------------------------------------------------
    def build(self, generator_sha: str) -> bytes:
        self.build_reference()
        self.build_score_layout()
        self.build_metrics()
        self.build_results()
        self.build_score_results()
        self.build_instructions()
        self.build_metadata(generator_sha)
        order = ["Instructions", SCORE, "Metrics", "Results", "Reference", "Metadata", "ChartData"]
        self.wb._sheets = [self.wb[name] for name in order]
        self.wb.active = 1
        for ws in self.wb.worksheets:
            ws.sheet_view.tabSelected = ws.title == SCORE
        self.wb.properties.creator = "EASI"
        self.wb.properties.lastModifiedBy = "EASI"
        self.wb.properties.title = "EASI calculator"
        self.wb.properties.created = FIXED_STAMP
        self.wb.properties.modified = FIXED_STAMP
        self.wb.calculation.fullCalcOnLoad = True
        buf = io.BytesIO()
        self.wb.save(buf)
        return repack(buf.getvalue())


#: Excel 2016+ "show #N/A as an empty cell" for a chart, the option the SFARI
#: charts carry. openpyxl does not write it, and without it every unrated bar
#: prints a "#N/A" data label.
NA_AS_BLANK = ('<extLst><ext uri="{56B9EC1D-385E-4148-901F-78D8002777C0}" '
               'xmlns:c16r3="http://schemas.microsoft.com/office/drawing/2017/03/chart">'
               '<c16r3:dataDisplayOptions16><c16r3:dispNaAsBlank val="1"/></c16r3:dataDisplayOptions16>'
               '</ext></extLst>')


def repack(source: bytes) -> bytes:
    """Rewrite the package with fixed entry timestamps so the bytes are reproducible.

    openpyxl stamps ``dcterms:modified`` with the save time regardless of the
    workbook properties, so that element is pinned here too. The chart parts
    gain the "#N/A as blank" option on the way through.
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
            elif info.filename.startswith("xl/charts/chart") and info.filename.endswith(".xml"):
                text = data.decode("utf-8")
                assert text.count("</chart>") == 1 and "dispNaAsBlank" not in text, info.filename
                data = text.replace("</chart>", NA_AS_BLANK + "</chart>").encode("utf-8")
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
