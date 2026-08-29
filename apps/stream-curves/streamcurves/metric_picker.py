"""Unified metric-picker catalog for the wizard's "Choose metrics" step.

The step used to render a source-by-source accordion of bare mnemonic codes
("XBKA (nan)"). This module joins the three catalogs the wizard already loads —
the NRSA catalog (``nrsa.load_nrsa_catalog``), the StreamCat catalog, and the
SFARI crosswalk (``metric_map.metric_map_entries``) — into ONE tidy table so the
UI can show a readable name, units, source, and the STAF function(s)/discipline
each metric informs, and can compute live function coverage.

Names come from ``metric_map.yaml`` where the SFARI crosswalk curates one, and
otherwise from the metric dictionary (``streamcurves.metric_names``), which
carries EPA's own definition for essentially every NRSA field, so no row renders
as a bare code any more. ``named`` is a different question: it marks the curated
metrics the UI foregrounds, and the rest go to the "Advanced" list.

Pure/UI-free by design (domain layer): the coverage helpers return plain dicts so
the view renders them with ``render_wb_table`` and so tests need no Shiny session.
"""

from __future__ import annotations

import math
import re

import pandas as pd

from . import metric_names
from .metric_map import metric_map_entries, metric_map_functions_for
from .nrsa import load_nrsa_catalog
from .paths import DATA_DIR
from .staf_library import staf_canonical_function, staf_functions_by_discipline

PICKER_COLUMNS = [
    "code", "name", "source_key", "source", "category",
    "units", "functions", "disciplines", "recommended", "named",
]

# lowercase crosswalk source -> display label / the wizard reactive it feeds.
_SRC_DISPLAY = {
    "nrsa": "NRSA",
    "streamcat": "StreamCat",
    "streamstats": "StreamStats",
    "mmw": "MMW",
    "site_engine": "Site engine",
}
_STREAMCAT_CATALOG = DATA_DIR / "streamcat_metrics.csv"


def _load_streamcat_catalog() -> pd.DataFrame:
    try:
        return pd.read_csv(_STREAMCAT_CATALOG)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["name", "label", "domain", "default"])


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == ""


def _split_units(label: str) -> tuple[str, str]:
    """Split a trailing ``(units)`` off a label: "Bank angle (degrees)" ->
    ("Bank angle", "degrees"). No trailing parenthetical -> (label, "")."""
    if _blank(label):
        return "", ""
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", str(label).strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return str(label).strip(), ""


def build_metric_picker_table(
    *, nrsa=None, streamcat=None, streamstats=None, mmw=None,
    site_engine=None, entries=None
) -> pd.DataFrame:
    """One row per selectable metric across all catalog sources.

    Sources default to the real loaders; pass explicit frames/dicts in tests.
    ``streamstats`` is a ``{code: label}`` dict (``ss_core_bcs()``); ``mmw`` is a
    ``{key: {"label": ...}}`` dict (``mmw_core_metrics()``) — both optional and
    empty by default so the module is decoupled from those datasources.
    """
    entries = entries if entries is not None else metric_map_entries()
    mm_label: dict[str, str] = {}
    rec: set[str] = set()
    for _, r in entries.iterrows():
        code = r["code"]
        if code is None:
            continue
        code = str(code)
        if not _blank(r["label"]):
            mm_label.setdefault(code, str(r["label"]))
        if bool(r["default_selected"]):
            rec.add(code)

    rows: list[dict] = []

    def add_row(code, source_key: str, raw_label="", category="", cat_units=""):
        code = str(code)
        # metric_map's curated label wins; otherwise the metric dictionary's
        # readable name; the catalog's raw label is only the mnemonic
        dict_name = metric_names.display_name_for(code)
        base = mm_label.get(code) or dict_name or (raw_label if not _blank(raw_label) else code)
        name, units = _split_units(base)
        if _blank(units) and not _blank(cat_units):
            units = str(cat_units)
        if _blank(units):
            units = metric_names.units_for(code, "") or ""
        ffs = metric_map_functions_for(code)
        functions = list(dict.fromkeys(f["function_name"] for f in ffs))
        disciplines = list(dict.fromkeys(f["discipline"] for f in ffs))
        # "named" means curated-and-foregrounded, not "has a name": it gates
        # the main picker list, which deliberately shows the crosswalk's
        # metrics and tucks the rest of the NRSA catalog into Advanced.
        named = (code in mm_label) or (source_key in
                                       ("streamcat", "streamstats", "mmw",
                                        "site_engine"))
        rows.append({
            "code": code,
            "name": name or code,
            "source_key": source_key,
            "source": _SRC_DISPLAY.get(source_key, source_key),
            "category": "" if _blank(category) else str(category),
            "units": units or "",
            "functions": functions,
            "disciplines": disciplines,
            "recommended": code in rec,
            "named": bool(named),
        })

    # NRSA — the big catalog; most rows are unnamed (code only).
    ncat = nrsa if nrsa is not None else load_nrsa_catalog()
    if ncat is not None and len(ncat):
        for _, r in ncat.iterrows():
            add_row(r["name"], "nrsa", raw_label=r.get("label"),
                    category=r.get("category"), cat_units=r.get("units"))

    # StreamCat — all named, units baked into the label.
    scat = streamcat if streamcat is not None else _load_streamcat_catalog()
    if scat is not None and len(scat):
        for _, r in scat.iterrows():
            add_row(r["name"], "streamcat", raw_label=r.get("label"),
                    category=r.get("domain"))

    # StreamStats basin characteristics — {code: label}.
    for code, label in (streamstats or {}).items():
        add_row(code, "streamstats", raw_label=label, category="Basin characteristics")

    # Model My Watershed — {key: {"label": ...}}.
    for key, meta in (mmw or {}).items():
        label = meta.get("label") if isinstance(meta, dict) else meta
        add_row(key, "mmw", raw_label=label, category="Watershed")

    # Site computation engine — {key: {"label": ...}} (exact-watershed
    # predictors; rows appear only when the vendored engine is available).
    for key, meta in (site_engine or {}).items():
        label = meta.get("label") if isinstance(meta, dict) else meta
        add_row(key, "site_engine", raw_label=label, category="Exact watershed")

    if not rows:
        return pd.DataFrame(columns=PICKER_COLUMNS)
    return pd.DataFrame(rows, columns=PICKER_COLUMNS)


def default_selected_codes(table: pd.DataFrame) -> list[str]:
    """Codes checked when the user first lands (the recommended set), preserving
    the wizard's prior default selection."""
    if table is None or len(table) == 0:
        return []
    return list(table.loc[table["recommended"], "code"].astype(str))


def _candidate_columns(code: str, source_key: str) -> list[str]:
    """Column names a picker code could have been compiled to.

    NRSA/MMW compile to the bare code and StreamStats gains an ``ss_`` prefix,
    but StreamCat columns carry an area-of-interest suffix (``pctimp2019`` ->
    ``pctimp2019ws``), so a bare-code lookup misses every StreamCat metric. This
    maps forward rather than stripping suffixes off the columns, so a code can
    never be matched by an unrelated column that merely ends in ``ws``/``cat``.
    Suffix list mirrors ``datasources.streamcat._STREAMCAT_AOI_SUFFIX``.
    """
    if source_key == "streamstats":
        return [f"ss_{code}"]
    if source_key == "streamcat":
        return [code] + [f"{code}{sfx}" for sfx in ("ws", "cat", "wsrp100", "catrp100")]
    return [code]


def codes_for_columns(columns, table: pd.DataFrame) -> set[str]:
    """Picker codes whose pulled data column exists in ``columns``.

    The reverse of the compile step's column naming, used to seed the wizard's
    selection from a restored project's dataset.
    """
    if table is None or len(table) == 0:
        return set()
    # `columns` may be a pandas Index, whose truth value is ambiguous — never
    # use `or []` shortcuts on it.
    cols = set() if columns is None else {str(c).lower() for c in columns}
    out: set[str] = set()
    for _, r in table.iterrows():
        code = str(r["code"])
        cands = _candidate_columns(code, str(r["source_key"]))
        if any(c.lower() in cols for c in cands):
            out.add(code)
    return out


def split_selection_by_source(selected_codes, table: pd.DataFrame) -> dict[str, list[str]]:
    """Map a flat set of selected codes back to the per-source lists the compile
    step consumes (``metric_sel``/``nrsa_sel``/``ss_sel``/``mmw_sel``)."""
    out: dict[str, list[str]] = {"nrsa": [], "streamcat": [], "streamstats": [],
                                 "mmw": [], "site_engine": []}
    if table is None or len(table) == 0:
        return out
    src = dict(zip(table["code"].astype(str), table["source_key"]))
    seen: set[str] = set()
    for c in selected_codes or []:
        c = str(c)
        if c in seen:
            continue
        seen.add(c)
        key = src.get(c)
        if key in out:
            out[key].append(c)
    return out


# --------------------------------------------------------------------------- #
# Coverage: which of the 20 STAF functions the selection informs.             #
# --------------------------------------------------------------------------- #
def coverage_by_function(codes, label_of=None) -> dict[str, list[str]]:
    """{canonical function name -> [labels of selected metrics informing it]}.

    A metric informs a function per ``metric_map.yaml``; a metric serving several
    functions appears under each. ``label_of`` maps a code to its display label
    (defaults to the raw code)."""
    label_of = label_of or (lambda c: c)
    fn_metrics: dict[str, list[str]] = {}
    for c in codes or []:
        for ff in metric_map_functions_for(c):
            canon = staf_canonical_function(ff.get("function_name"))
            if canon is None:
                continue
            fn_metrics.setdefault(canon["name"], []).append(label_of(c))
    return fn_metrics


def coverage_summary(codes) -> dict:
    """Function-coverage rollup for a selection.

    Returns ``n_covered`` / ``total`` (out of 20) and ``per_discipline`` as
    ``{discipline: (covered, total)}`` over the fixed 5x4 STAF skeleton."""
    by_disc = staf_functions_by_discipline()
    covered = {f for f, mets in coverage_by_function(codes).items() if mets}
    per_discipline = {
        d: (sum(1 for f in fns if f in covered), len(fns))
        for d, fns in by_disc.items()
    }
    total = sum(len(fns) for fns in by_disc.values())
    n_covered = sum(c for c, _ in per_discipline.values())
    return {
        "covered_functions": covered,
        "n_covered": n_covered,
        "total": total,
        "per_discipline": per_discipline,
    }
