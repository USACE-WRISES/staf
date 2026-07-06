"""Read and normalize ``docs/Verification Sites/SFARI Summary.xlsx``.

The workbook holds the SFARI (Rapid-tier, field) results for the verification
sites: per-function scores (0-15), Physical/Chemical/Biological sub-indices and
ECI (0-1), plus name/lat/long/state. EASI (Screening tier) shares the STAF
function taxonomy, so the SFARI function columns map almost 1:1 onto EASI
``functionId`` values (see ``SFARI_TO_EASI_FUNCTION``).

No third-party deps: the xlsx is read with the stdlib (zipfile + ElementTree)
because openpyxl is not installed in this environment.

Caveats handled here:
- Coordinates come in mixed formats ("95.264424 W", "44.814359<nbsp>W", plain
  decimals). All sites are CONUS, so latitude is forced positive (N) and
  longitude forced negative (W).
- Some rows duplicate an earlier row's full score vector exactly (a data-entry
  artifact). Those later rows are flagged ``sfari_duplicate=True`` and excluded
  from the quantitative comparison (the EASI run still happens, for coverage).
"""
from __future__ import annotations

import os
import re
import zipfile
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_XLSX = os.path.join(ROOT, "docs", "Verification Sites", "SFARI Summary.xlsx")

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# SFARI column header (normalized, lower-case) -> EASI functionId.
# 19 of 20 align by name. SFARI "Planform change" has no EASI function, and EASI
# "Bed composition and large wood" has no SFARI column -> both stay unmatched.
SFARI_TO_EASI_FUNCTION = {
    "catchment hydrology": "catchment-hydrology",
    "surface water storage": "surface-water-storage",
    "reach inflow": "reach-inflow",
    "streamflow regime": "streamflow-regime",
    "low flow and baseflow dyanimcs": "low-flow-baseflow-dynamics",  # sheet typo
    "low flow and baseflow dynamics": "low-flow-baseflow-dynamics",
    "high flow dynamics": "high-flow-dynamics",
    "floodplain connectivity": "floodplain-connectivity",
    "hyporheic connectivity": "hyporheic-connectivity",
    "channel evolution": "channel-evolution",
    "planform change": None,
    "sediment continuity": "sediment-continuity",
    "channel and floodplain dynamics": "channel-floodplain-dynamics",
    "light and thermal regime": "light-thermal-regime",
    "carbon processing": "carbon-processing",
    "nutrient cycling": "nutrient-cycling",
    "water and soil quality": "water-soil-quality",
    "habitat provision": "habitat-provision",
    "population support": "population-support",
    "community dynamics": "community-dynamics",
    "watershed connectivity": "watershed-connectivity",
}

# The one EASI function with no SFARI counterpart (excluded from function stats).
EASI_FUNCTION_NO_SFARI = "bed-composition-bedform-dynamics"

# Mink Brook (site id "MB"): expert override of three functions to Good, per the
# project lead. Keyed by EASI metricId (what pipeline overrides expects).
MINK_BROOK_OVERRIDES = {
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence": "Good",
    "floodplain-connectivity-floodplain-access-entrenchment": "Good",
    "channel-evolution-channel-evolution-stage-and-trends": "Good",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ").strip()).lower()


def _num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace("\xa0", " "))
    return float(m.group(0)) if m else None


def parse_lat(s):
    v = _num(s)
    if v is None:
        return None
    return -abs(v) if "S" in str(s).upper() else abs(v)


def parse_lon(s):
    v = _num(s)
    if v is None:
        return None
    return -abs(v)  # CONUS: always West -> negative


def _read_sheet_rows(path: str):
    """Return the first worksheet as a list of row lists (strings/None)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target")
                     for r in rels.findall(_PKG + "Relationship")}
    first = wb.find(_NS + "sheets").findall(_NS + "sheet")[0]
    tgt = rid_to_target[first.get(_RID + "id")]
    if not tgt.startswith("xl/"):
        tgt = "xl/" + tgt

    def col_idx(ref):
        letters = re.match(r"([A-Z]+)\d+", ref).group(1)
        idx = 0
        for ch in letters:
            idx = idx * 26 + (ord(ch) - 64)
        return idx - 1

    root = ET.fromstring(z.read(tgt))
    data = root.find(_NS + "sheetData")
    out = []
    for row in data.findall(_NS + "row"):
        cells, maxc = {}, -1
        for c in row.findall(_NS + "c"):
            ci = col_idx(c.get("r"))
            maxc = max(maxc, ci)
            t, v, isv = c.get("t"), c.find(_NS + "v"), c.find(_NS + "is")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr" and isv is not None:
                val = "".join(tt.text or "" for tt in isv.iter(_NS + "t"))
            elif v is not None:
                val = v.text
            else:
                val = None
            cells[ci] = val
        out.append([cells.get(i) for i in range(maxc + 1)])
    return out


def read_sites(path: str = DEFAULT_XLSX) -> list[dict]:
    """Parse the SFARI summary into a list of site dicts.

    Each site has: ``site_id, name, state, lat, lon, raw_lat, raw_lon``;
    ``sfari_functions`` ({functionId: score} for matched functions);
    ``sfari_planform_change`` (the unmatched SFARI function, kept for the record);
    ``sfari_sub`` ({physical, chemical, biological}); ``sfari_eci``;
    ``sfari_duplicate`` (bool) and ``duplicate_of`` (site_id) for exact-duplicate
    score rows.
    """
    rows = _read_sheet_rows(path)
    header = [_norm(h) for h in rows[0]]

    def find(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_id = find("site id")
    i_name = find("site name")
    i_lat = find("lat", "latitude")
    i_lon = find("long", "lon", "longitude")
    i_state = find("state")
    i_phys = find("physical sub-index", "physical subindex")
    i_chem = find("chemical sub-index", "chemical subindex")
    i_bio = find("biological sub-index", "biological subindex")
    i_eci = find("eci")

    # function columns: header -> (col index, functionId or None)
    func_cols = []
    for ci, h in enumerate(header):
        if h in SFARI_TO_EASI_FUNCTION:
            func_cols.append((ci, h, SFARI_TO_EASI_FUNCTION[h]))

    sites, seen = [], {}
    for r in rows[1:]:
        if not r or i_id is None or i_id >= len(r) or r[i_id] in (None, ""):
            continue

        def cell(idx):
            return r[idx] if (idx is not None and idx < len(r)) else None

        functions, planform = {}, None
        for ci, h, fid in func_cols:
            val = _num(cell(ci))
            if fid is None:
                planform = val
            elif val is not None:
                functions[fid] = val

        site = {
            "site_id": str(cell(i_id)).strip(),
            "name": str(cell(i_name) or "").strip(),
            "state": str(cell(i_state) or "").strip(),
            "raw_lat": cell(i_lat),
            "raw_lon": cell(i_lon),
            "lat": parse_lat(cell(i_lat)),
            "lon": parse_lon(cell(i_lon)),
            "sfari_functions": functions,
            "sfari_planform_change": planform,
            "sfari_sub": {
                "physical": _num(cell(i_phys)),
                "chemical": _num(cell(i_chem)),
                "biological": _num(cell(i_bio)),
            },
            "sfari_eci": _num(cell(i_eci)),
        }

        # exact-duplicate score vector (data-entry artifact) -> flag later rows
        key = (tuple(sorted(functions.items())),
               tuple(sorted((k, v) for k, v in site["sfari_sub"].items())),
               site["sfari_eci"])
        if key in seen:
            site["sfari_duplicate"] = True
            site["duplicate_of"] = seen[key]
        else:
            site["sfari_duplicate"] = False
            site["duplicate_of"] = None
            seen[key] = site["site_id"]
        sites.append(site)
    return sites


if __name__ == "__main__":  # quick inspection
    ss = read_sites()
    print(f"{len(ss)} sites; "
          f"{sum(1 for s in ss if not s['sfari_duplicate'])} unique-score, "
          f"{sum(1 for s in ss if s['sfari_duplicate'])} duplicate")
    for s in ss:
        flag = f"  DUP of {s['duplicate_of']}" if s["sfari_duplicate"] else ""
        print(f"  {s['site_id']:4} {s['name'][:26]:26} {s['state']:3} "
              f"({s['lat']:.4f}, {s['lon']:.4f})  ECI={s['sfari_eci']:.3f}"
              f"  nfunc={len(s['sfari_functions'])}{flag}")
