"""The SFARI Excel calculator, blank and filled.

The calculator is the worksheet an assessment is handed over in. This module
serves the blank copy and writes a filled one from an assessment, so nobody has
to retype 20 function scores and 80 Likert selections.

The template (``data/calculator/``, draft 2026-06-29) is out for certification by
the Eco-PCX, so it must come back unaltered except for the cells an assessor
would type into. That rules out a spreadsheet library: openpyxl cannot round-trip
this workbook. Measured on 2026-09-08, a load-and-save drops 38 of its 64 parts,
including every embedded image, both chart style/color pairs, the VML drawing,
the printer settings, the threaded comments and the sensitivity label, and shrinks
the package by 31 percent.

So this edits the sheet XML inside the zip and copies every other part through
untouched. That also means no runtime dependency: zipfile and re are enough.

What gets written, all of them cells the workbook itself marks unlocked:
  B2  reach id            B3  lat/long        B4  date        G2  reach length (ft)
  H8, H12 ... H84         the 20 function scores, 0-15 (each is a 4-row merge, so
                          only the anchor row is written)
  J8 .. J87               the 80 metric Likert values

Everything else in the worksheet is a formula. The blank ships with cached error
values (321 formulas, 110 cells caching an error), so ``fullCalcOnLoad`` is set to
make Excel recalculate on open.

One correction to the shipped template (2026-09-08, owner decision). Cell E72, the
Biology functional-category label, tested Biology's own range for its first
threshold but Physicochemistry's (``H56:H71``) for its second, so a Biology average
of 0.5 was labelled Functioning whenever Physicochemistry happened to reach 0.7.
Its second ``AVERAGE`` now reads ``H72:H87`` like the other four categories. That
one formula is the only difference from the owner's master copy under ``notes/``,
which still carries the bug; the same fix has to reach whoever re-issues the draft.
"""
from __future__ import annotations

import copy
import datetime as _dt
import io
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from . import config

#: The draft this app ships. Part of the download filename so a returned
#: worksheet can be traced to the template it came from.
TEMPLATE_VERSION = "2026-06-29"

TEMPLATE_PATH = (Path(__file__).resolve().parent.parent
                 / "data" / "calculator" / f"SFARI_Calculator_Draft_{TEMPLATE_VERSION}.xlsx")

SHEET_NAME = "SFARI Score"

#: Excel's day zero for the 1900 date system. 1899-12-30 rather than 1899-12-31
#: because it absorbs the phantom 1900-02-29 for every date from 1900-03-01 on.
_EXCEL_EPOCH = _dt.date(1899, 12, 30)

#: The workbook's Likert list ($R$1:$R$6). The first five are identical to
#: config.LIKERT_ORDER; only the not-applicable label differs.
NOT_APPLICABLE_LABEL = "NA"

# --- The cell map -------------------------------------------------------------
# Explicit and committed, never derived from list order or from label matching:
# the worksheet's labels are abbreviations of the app's metric names in 17 of the
# 80 rows ("Flow Statistics" for "Flow permanence statistics", "Toxic Pollutants"
# for "Pollutants"), so a name match would be wrong and a positional match would
# break silently the first time a metric is inserted. The third element is the
# label the worksheet carries today; a test asserts it still does, so a revised
# template fails loudly instead of scoring the wrong row.

FUNCTION_ROWS = (
    ("catchment-hydrology", 8, "Catchment Hydrology"),
    ("surface-water-storage", 12, "Surface Water Storage"),
    ("reach-inflow", 16, "Reach Inflow"),
    ("streamflow-regime", 20, "Streamflow Regime"),
    ("low-flow-baseflow-dynamics", 24, "Low Flow and Baseflow Dynamics"),
    ("high-flow-dynamics", 28, "High Flow Dynamics"),
    ("floodplain-connectivity", 32, "Floodplain Connectivity"),
    ("hyporheic-connectivity", 36, "Hyporheic Connectivity"),
    ("channel-evolution", 40, "Channel Evolution"),
    ("channel-floodplain-dynamics", 44, "Channel and Floodplain Dynamics"),
    ("sediment-continuity", 48, "Sediment Continuity"),
    ("bed-composition-bedform-dynamics", 52, "Bed Composition and Bedform Dynamics"),
    ("light-thermal-regime", 56, "Light and Thermal Regime"),
    ("carbon-processing", 60, "Carbon Processing"),
    ("nutrient-cycling", 64, "Nutrient Cycling"),
    ("water-soil-quality", 68, "Water and Soil Quality"),
    ("habitat-provision", 72, "Habitat Provision"),
    ("population-support", 76, "Population Support"),
    ("community-dynamics", 80, "Community Dynamics"),
    ("watershed-connectivity", 84, "Watershed Connectivity"),
)

METRIC_ROWS = (
    ("catchment-hydrology-impervious-surface-area", 8, "Impervious surface area"),
    ("catchment-hydrology-road-density", 9, "Road density"),
    ("catchment-hydrology-land-use-change", 10, "Land Use Change"),
    ("catchment-hydrology-impoundments", 11, "Impoundments"),
    ("surface-water-storage-wetland-coverage", 12, "Wetland coverage"),
    ("surface-water-storage-floodplain-water-retention", 13, "Floodplain water retention"),
    ("surface-water-storage-in-channel-ponding-beaver", 14, "In-channel ponding/beaver"),
    ("surface-water-storage-off-channel-storage", 15, "Off-channel storage"),
    ("reach-inflow-concentrated-flow-inputs", 16, "Concentrated flow inputs"),
    ("reach-inflow-tributary-condition-and-impact", 17, "Tributary Condition and Impact"),
    ("reach-inflow-local-runoff-diversions", 18, "Local runoff diversions"),
    ("reach-inflow-road-highway-drainage", 19, "Road/highway drainage"),
    ("streamflow-regime-flow-permanence", 20, "Flow Permanence"),
    ("streamflow-regime-flow-permanence-statistics", 21, "Flow Statistics"),
    ("streamflow-regime-channel-natural-flow-regime", 22, "Channel natural flow regime"),
    ("streamflow-regime-artificial-structures-and-inputs", 23, "Artificial structures and inputs"),
    ("low-flow-baseflow-dynamics-low-flow-velocity", 24, "Low flow velocity"),
    ("low-flow-baseflow-dynamics-low-flow-depth", 25, "Low flow depth"),
    ("low-flow-baseflow-dynamics-longitudinal-connectivity", 26, "Longitudinal connectivity"),
    ("low-flow-baseflow-dynamics-lateral-connectivity", 27, "Lateral connectivity"),
    ("high-flow-dynamics-overbank-flow-frequency", 28, "Overbank Flow Frequency"),
    ("high-flow-dynamics-peak-flow-capacity-morphological-check", 29, "Peak Flow Capacity (Morphological)"),
    ("high-flow-dynamics-peak-flow-capacity-velocity-shear-stress", 30, "Peak Flow Capacity (Velocity/Shear)"),
    ("high-flow-dynamics-bed-mobilization-frequency", 31, "Bed Mobilization Frequency"),
    ("floodplain-connectivity-floodplain-complexity", 32, "Floodplain Complexity"),
    ("floodplain-connectivity-entrenchment-er", 33, "Entrenchment Ratio (ER)"),
    ("floodplain-connectivity-channel-condition", 34, "Channel Condition"),
    ("floodplain-connectivity-lateral-floodplain-inundation", 35, "Lateral Floodplain inundation"),
    ("hyporheic-connectivity-channel-complexity-for-exchange", 36, "Channel Complexity for Exchange"),
    ("hyporheic-connectivity-floodplain-permeability", 37, "Floodplain Permeability"),
    ("hyporheic-connectivity-bed-surface-grain-size", 38, "Bed Surface Grain Size"),
    ("hyporheic-connectivity-visible-hyporheic-indicators", 39, "Visible Hyporheic Metrics"),
    ("channel-evolution-channel-evolution-stage", 40, "Channel Evolution"),
    ("channel-evolution-incision-trend-headcuts", 41, "Incision Trend (headcuts)"),
    ("channel-evolution-widening-trend", 42, "Widening Trend"),
    ("channel-evolution-recovery-indicators", 43, "Recovery Indicators"),
    ("channel-floodplain-dynamics-bank-erosion-potential", 44, "Bank Erosion Potential"),
    ("channel-floodplain-dynamics-bank-migration-and-meander", 45, "Bank Migration and Meander"),
    ("channel-floodplain-dynamics-sinuosity", 46, "Sinuosity"),
    ("channel-floodplain-dynamics-channel-pattern", 47, "Channel Pattern"),
    ("sediment-continuity-sediment-deposition-patterns", 48, "Sediment Deposition Patterns"),
    ("sediment-continuity-channel-degradation-incision", 49, "Channel Degradation (Incision)"),
    ("sediment-continuity-fine-sediment-balance", 50, "Fine Sediment Balance"),
    ("sediment-continuity-transport-capacity", 51, "Transport Capacity"),
    ("bed-composition-bedform-dynamics-large-wood-frequency-and-diversity", 52, "Large wood frequency and diversity"),
    ("bed-composition-bedform-dynamics-riparian-wood-recruitment", 53, "Riparian Wood Recruitment"),
    ("bed-composition-bedform-dynamics-substrate-composition", 54, "Substrate composition"),
    ("bed-composition-bedform-dynamics-bedform-diversity", 55, "Bedform diversity"),
    ("light-thermal-regime-riparian-canopy-cover", 56, "Riparian Canopy Cover"),
    ("light-thermal-regime-stream-temperature-rapid", 57, "Stream Temperature"),
    ("light-thermal-regime-algal-growth-light-limited", 58, "Algal Growth (light-limited)"),
    ("light-thermal-regime-thermal-refugia", 59, "Thermal Refugia"),
    ("carbon-processing-cpom-retention", 60, "CPOM Retention"),
    ("carbon-processing-detritus-decomposition-rate-shredder-detritivore-presence", 61, "Detritus Decomposition, Shredders"),
    ("carbon-processing-riparian-corridor-width-and-quality", 62, "Riparian Corridor Width/Quality"),
    ("carbon-processing-algal-and-primary-production", 63, "Algal/Primary Production"),
    ("nutrient-cycling-visible-algal-indications-n-p", 64, "Visible Algal Indications (N/P)"),
    ("nutrient-cycling-n-p-concentrations", 65, "N/P Concentrations (surrogates)"),
    ("nutrient-cycling-vegetated-riparian-corridor-width", 66, "Vegetated Riparian Corridor Width"),
    ("nutrient-cycling-relative-denitrification-potential", 67, "Relative Denitrification Potential (visual)"),
    ("water-soil-quality-water-clarity-turbidity", 68, "Water Clarity/Turbidity"),
    ("water-soil-quality-dissolved-oxygen-rapid", 69, "Dissolved Oxygen"),
    ("water-soil-quality-pollutants", 70, "Toxic Pollutants"),
    ("water-soil-quality-ph-and-specific-conductivity-rapid", 71, "pH & Specific Conductivity"),
    ("habitat-provision-in-stream-habitat-complexity", 72, "In-Stream Habitat Complexity"),
    ("habitat-provision-overhanging-vegetation", 73, "Overhanging Vegetation"),
    ("habitat-provision-aquatic-invertebrate-habitat", 74, "Aquatic Invertebrate Habitat"),
    ("habitat-provision-lateral-and-off-channel-habitats", 75, "Lateral/Off-Channel Habitats"),
    ("population-support-fish-habitat", 76, "Fish habitat"),
    ("population-support-aquatic-invertebrate-community", 77, "Aquatic Invertebrate Community"),
    ("population-support-fish-presence", 78, "Fish Presence"),
    ("population-support-amphibian-and-crayfish-presence", 79, "Amphibian/ Crayfish Presence"),
    ("community-dynamics-native-and-non-native-species", 80, "Native and non-native species"),
    ("community-dynamics-species-richness-composition-and-abundance", 81, "Species richness, comp, and abundance"),
    ("community-dynamics-riparian-communities", 82, "Riparian communities"),
    ("community-dynamics-keystone-species", 83, "Keystone species"),
    ("watershed-connectivity-upstream-and-downstream-barriers", 84, "Upstream/downstream barriers"),
    ("watershed-connectivity-culvert-and-road-crossing-passability", 85, "Culvert/road-crossing passability"),
    ("watershed-connectivity-lateral-connectivity-for-riparian-fauna", 86, "Lateral connectivity for riparian fauna"),
    ("watershed-connectivity-dewatered-or-intermittent-segments", 87, "Dewatered/intermittent segments"),
)


# --- Reading the template -----------------------------------------------------
@lru_cache(maxsize=1)
def blank_bytes() -> bytes:
    """The shipped template, byte for byte.

    Bytes, not an open ``ZipFile``: handing a live archive's ``ZipInfo`` to
    ``writestr`` mutates it, so a cached archive would be corrupted by the first
    download and raise ``BadZipFile`` on every one after it.
    """
    return TEMPLATE_PATH.read_bytes()


# --- Cell writing -------------------------------------------------------------
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CELL_RE = re.compile(r'<c r="([A-Z]+[0-9]+)"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*/>')


def _xml_text(value) -> str:
    """Escape for an XML text node, including what Excel's own escape covers."""
    text = _ILLEGAL_XML.sub("", str(value))
    # Excel decodes _xHHHH_ on read, so a literal one in user text has to be
    # escaped or it comes back as a different character.
    text = re.sub(r"_(x[0-9A-Fa-f]{4}_)", r"_x005F_\1", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _excel_serial(day: _dt.date) -> int:
    return (day - _EXCEL_EPOCH).days


def _fill_cells(sheet_xml: str, values: dict) -> str:
    """Replace each empty cell named in ``values`` with the same cell carrying it.

    One pass, so the cost does not grow with the number of cells, and the set of
    refs actually replaced is checked against the set intended: a template whose
    cells moved fails here rather than producing a workbook that is silently
    blank where a score should be.
    """
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        ref, attrs = m.group(1), m.group(2)
        if ref not in values:
            return m.group(0)
        seen.add(ref)
        value = values[ref]
        if isinstance(value, str):
            return f'<c r="{ref}"{attrs} t="inlineStr"><is><t xml:space="preserve">' \
                   f'{_xml_text(value)}</t></is></c>'
        return f'<c r="{ref}"{attrs}><v>{value}</v></c>'

    out = _CELL_RE.sub(repl, sheet_xml)
    missing = set(values) - seen
    if missing:
        raise ValueError(
            "SFARI calculator template does not carry the expected empty cells: "
            + ", ".join(sorted(missing))
        )
    return out


def _sheet_part(archive: zipfile.ZipFile, name: str) -> str:
    """The worksheet part for a sheet name, resolved through the rels."""
    from xml.sax.saxutils import unescape

    book = archive.read("xl/workbook.xml").decode("utf-8")
    rid = None
    for tag in re.findall(r"<sheet\b[^>]*/>", book):
        got = re.search(r'name="([^"]*)"', tag)
        if got and unescape(got.group(1)) == name:
            rid = re.search(r'r:id="([^"]*)"', tag)
            rid = rid.group(1) if rid else None
            break
    if not rid:
        raise ValueError(f"SFARI calculator template has no sheet named {name!r}")
    rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = re.search(r'<Relationship[^>]*Id="%s"[^>]*Target="([^"]*)"' % rid, rels)
    if not target:
        raise ValueError(f"SFARI calculator template has no relationship {rid!r}")
    part = target.group(1).lstrip("/")
    return part if part.startswith("xl/") else f"xl/{part}"


def _force_recalc(book_xml: str) -> str:
    """Make Excel recalculate on open.

    The template caches an error in 110 cells, so without this the filled
    workbook opens showing #DIV/0! next to correct inputs. calcId is zeroed as
    well: Excel treats an unknown calc version as "computed elsewhere" and
    recalculates even where it would otherwise trust the cache.
    """
    def repl(m: re.Match) -> str:
        attrs = re.sub(r'\s*calcId="[^"]*"', "", m.group(1))
        return f'<calcPr{attrs} calcId="0" fullCalcOnLoad="1"/>'

    out, n = re.subn(r"<calcPr\b([^>]*?)/>", repl, book_xml, count=1)
    if n == 0:
        out, n = re.subn(r"<calcPr\b([^>]*?)>.*?</calcPr>", repl, book_xml,
                         count=1, flags=re.S)
    if n == 0:
        raise ValueError("SFARI calculator template has no calcPr to set recalculation on")
    return out


def _repack(source: bytes, replacements: dict) -> bytes:
    """Rewrite the package with some parts replaced and the rest copied.

    ``copy.copy`` on each ``ZipInfo`` because ``writestr`` writes the CRC and
    sizes back into the object it is given, which would corrupt the source
    archive's index. Copying also carries the per-entry compression (five PNGs
    are stored, not deflated), the 1980 timestamps and the MS-DOS create_system
    through, so the output is deterministic and identical on Windows and Linux.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as zin, \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(copy.copy(info), data)
    return buf.getvalue()


# --- The public builders ------------------------------------------------------
def build_calculator(delin, metric_scores, function_scores, *, today=None) -> bytes:
    """The calculator with this assessment written into its input cells.

    Arguments mirror the report builders. Unscored functions and unrated metrics
    are left blank, exactly as they would be if the assessor had not filled them
    in yet.
    """
    from . import report                      # local: avoids a cycle at import time

    dl = (delin or {}).get("delineation") or {}
    values: dict = {}

    reach_id = report._reach_id_str(dl)
    if reach_id:
        values["B2"] = reach_id
    coords = report._coords_str(dl)
    if coords:
        values["B3"] = coords
    values["B4"] = _excel_serial(today or _dt.date.today())
    length = dl.get("reach_length_ft")
    if length is not None:
        values["G2"] = int(round(float(length)))

    for fid, row, _label in FUNCTION_ROWS:
        score = ((function_scores or {}).get(fid) or {}).get("score")
        if score is not None:
            values[f"H{row}"] = int(score)

    for mid, row, _label in METRIC_ROWS:
        likert = ((metric_scores or {}).get(mid) or {}).get("likert")
        if not likert:
            continue
        values[f"J{row}"] = (NOT_APPLICABLE_LABEL if likert == config.LIKERT_NA
                             else str(likert))

    source = blank_bytes()
    with zipfile.ZipFile(io.BytesIO(source)) as zin:
        sheet_part = _sheet_part(zin, SHEET_NAME)
        sheet_xml = zin.read(sheet_part).decode("utf-8")
        book_xml = zin.read("xl/workbook.xml").decode("utf-8")

    return _repack(source, {
        sheet_part: _fill_cells(sheet_xml, values).encode("utf-8"),
        "xl/workbook.xml": _force_recalc(book_xml).encode("utf-8"),
    })


def blank_filename() -> str:
    """The blank does not depend on the site."""
    return f"SFARI_Calculator_Draft_{TEMPLATE_VERSION}.xlsx"


def calculator_filename(delin) -> str:
    """``sfari-calculator-nhdplusid-<id>.xlsx`` (or comid, or coordinates)."""
    from . import report

    slug = report._site_slug(delin)          # takes the whole result, not the inner dict
    return f"sfari-calculator-{slug}.xlsx" if slug else "sfari-calculator.xlsx"
