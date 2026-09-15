"""Steps ``candidates``, ``erom`` and ``attains``: the data every candidate
metric of the stress test needs, none of it touching the pipeline's caches.

- ``analysis/streamcat_candidates.parquet``: the StreamCat variables the
  candidate metrics read, pulled by hydro-region like the national cache but
  into their own file, with a per-name area of interest (the predicted benthic
  condition ``prg_bmmi0809`` and the NRSA frame exist only under ``other``).
- ``analysis/erom.parquet``: the EROM flow estimates per reach from the
  seamless geodatabase (mean annual and monthly), plus the derived low-flow,
  variability and alteration ratios.
- ``analysis/attains_au_attributes.parquet``: every ATTAINS assessment unit
  with its per-use statuses and cause columns, and the pure aquatic-life-use
  rating rule the schemes simulate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from .. import config
from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common, local_gdb
from ..stages.streamcat_national import run_streamcat_national
from . import ANALYSIS_VERSION

WS_CAT = "ws,cat"
#: StreamCat names at the watershed and catchment scales: pressures, soils,
#: hydrology and inputs the candidate metrics of ``05_function_alternatives``
#: read; the years are the latest each series offers.
WS_CAT_NAMES = (
    "bfi", "canaldens", "rdcrs", "rdcrsslpwtd", "nabd_dens", "nabd_nrmstor", "damdens",
    "npdesdens", "wwtpalldens", "wwtpmajordens", "septic", "tridens", "superfunddens",
    "minedens", "coalminedens", "pcthydric", "wetindex", "hydrlcond", "pctalluvcoast",
    "pctagslphigh2019", "pctagslpmid2019", "agkffact", "pctagdrainage",
    "pctimpslphigh2019", "pctimpslpmid2019", "pctimp2001",
    "pctfrstloss2011", "pctfrstloss2012", "pctfrstloss2013",
    "pctburnarea2014", "pctburnarea2015", "pctburnarea2016", "pctburnarea2017", "pctburnarea2018",
    "n_tin_2017", "p_tin_2017", "nani", "nsurp", "wdrw_ld", "pctow2019",
    "elev", "precip8110", "tmean8110",
)
#: the NLCD classes already cached at ws/cat/wsrp100, now at the reach's own corridor
CATRP100_NAMES = ("pctconif2019", "pctdecid2019", "pctmxfst2019", "pctshrb2019", "pctwdwet2019",
                  "pcthbwet2019", "pctgrs2019", "pctcrop2019", "pcthay2019", "pctimp2019")
#: metrics with no watershed or catchment scale (the API answers them only under ``other``)
OTHER_NAMES = ("prg_bmmi0809", "nrsa_frame", "nars_region", "msst2014",
               "bankfullwidth", "bankfulldepth", "wettedwidth")


def candidate_aoi() -> dict[str, str]:
    """name -> comma-joined areas of interest for the candidate pull."""
    out = {name: WS_CAT for name in WS_CAT_NAMES}
    out.update({name: "catrp100" for name in CATRP100_NAMES})
    out.update({name: config.STREAMCAT_OTHER_AOI for name in OTHER_NAMES})
    return out


def candidates_path(root: DataRoot) -> Path:
    return root.analysis / "streamcat_candidates.parquet"


def erom_path(root: DataRoot) -> Path:
    return root.analysis / "erom.parquet"


def attains_attributes_path(root: DataRoot) -> Path:
    return root.analysis / "attains_au_attributes.parquet"


# ------------------------------------------------------------ StreamCat
#: three real COMIDs (the Rivanna, Virginia) the preflight probe asks for
PROBE_COMIDS = (8566387, 8566389, 8566391)


def probe_names(aoi_by_name: dict[str, str], post, comids=PROBE_COMIDS) -> tuple[dict[str, str], list[str]]:
    """Ask the API for each name at each of its areas of interest on a few
    COMIDs and keep only the (name, area) pairs that answer a column, so a
    name the API lacks at one scale cannot fail a whole region group later.
    Returns ``(kept map, dropped pairs)``."""
    kept: dict[str, str] = {}
    dropped: list[str] = []
    ids = ",".join(str(c) for c in comids)
    for name, aois in aoi_by_name.items():
        good = []
        for aoi in aois.split(","):
            try:
                items = post({"name": name, "aoi": aoi, "comid": ids})
            except Exception:  # noqa: BLE001 - a refused pair is dropped, the pull goes on
                items = []
            expected = name.lower() if aoi == config.STREAMCAT_OTHER_AOI else f"{name}{aoi}".lower()
            answered = any(str(k).lower() == expected and v is not None for item in items for k, v in item.items())
            (good if answered else dropped).append(aoi if answered else f"{name}@{aoi}")
        if good:
            kept[name] = ",".join(good)
    return kept, dropped


def inputs_streamcat(root: DataRoot, options: Optional[dict] = None) -> str:
    return digest("candidates", ANALYSIS_VERSION, sorted(candidate_aoi().items()), 1)


def run_streamcat(root: DataRoot, progress: Progress, control: Control,
                  options: Optional[dict] = None, *, post=None, region_list=None, probe: bool = True) -> Path:
    from ..stages.streamcat import _post
    poster = post or _post
    aoi = candidate_aoi()
    if probe:
        aoi, dropped = probe_names(aoi, poster)
        (root.analysis / "streamcat_candidates_probe.json").write_text(
            json.dumps({"kept": aoi, "dropped": dropped}, indent=1), encoding="utf-8")
        progress.say(f"candidates: probe kept {len(aoi)} names, dropped {len(dropped)} name/scale pairs"
                     + (f" ({', '.join(dropped[:8])}{'...' if len(dropped) > 8 else ''})" if dropped else ""))
    return run_streamcat_national(
        root, progress, control, names=list(aoi), aoi_by_name=aoi, post=poster,
        cache=candidates_path(root), ledger_name="streamcat-candidates",
        parts=root.analysis / "streamcat_candidates_parts", region_list=region_list)


# ------------------------------------------------------------ EROM
EROM_MONTHS = tuple(f"{m:02d}" for m in range(1, 13))
EROM_KEEP = ("qa_ma", "qe_ma", "qc_ma", "va_ma", "ve_ma", "totdasqkm")


def _ratio(numerator, denominator):
    numerator = np.asarray(numerator, dtype=float)
    denominator = np.asarray(denominator, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denominator > 0, numerator / np.where(denominator > 0, denominator, 1.0), np.nan)
    return out


def erom_metrics(frame):
    """The derived flow quantities per reach (pandas frame in, frame out with
    the raw columns kept as ``EROM_KEEP`` and the monthly series dropped):
    ``q_per_km2`` (cfs per km2), ``q_min_ratio`` (minimum monthly over mean
    annual, a low-flow fraction), ``q_max_ratio``, ``q_cv_monthly``,
    ``q_alteration`` (gage-adjusted over the unit-runoff estimate),
    ``q_alteration_c`` (gage-adjusted over the reference-gage estimate),
    ``q_seasonal_alteration`` (largest monthly departure of QE from QC). Every
    ratio is null where its denominator is not positive (ephemeral and
    isolated reaches). Flows are cubic feet per second."""
    import pandas as pd
    from easi.metrics.hydraulics import monthly_flow_cv
    from easi.datasources.fabric import EROM_PROPERTIES, erom_from_properties
    frame = frame.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    qe = np.column_stack([frame[f"qe_{m}"].to_numpy(dtype=float) for m in EROM_MONTHS])
    qc = np.column_stack([frame[f"qc_{m}"].to_numpy(dtype=float) if f"qc_{m}" in frame
                          else np.full(len(frame), np.nan) for m in EROM_MONTHS])
    qe_ma = frame["qe_ma"].to_numpy(dtype=float)
    out = pd.DataFrame({"comid": frame["comid"].astype("int64").to_numpy()})
    for name in EROM_KEEP:
        if name in frame.columns:
            out[name] = frame[name].to_numpy(dtype=float)
    out["q_per_km2"] = _ratio(qe_ma, frame["totdasqkm"].to_numpy(dtype=float)) if "totdasqkm" in frame else np.nan
    with np.errstate(invalid="ignore"):
        out["q_min_ratio"] = _ratio(np.nanmin(qe, axis=1), qe_ma)
        out["q_max_ratio"] = _ratio(np.nanmax(qe, axis=1), qe_ma)
        evidence = frame[list(EROM_PROPERTIES)].itertuples(index=False, name=None)
        out["q_cv_monthly"] = [monthly_flow_cv(erom_from_properties(dict(zip(EROM_PROPERTIES, row))))
                               for row in evidence]
        out["q_alteration"] = _ratio(qe_ma, frame["qa_ma"].to_numpy(dtype=float)) if "qa_ma" in frame else np.nan
        out["q_alteration_c"] = _ratio(qe_ma, frame["qc_ma"].to_numpy(dtype=float)) if "qc_ma" in frame else np.nan
        monthly = np.abs(_ratio(qe, qc) - 1.0)
        out["q_seasonal_alteration"] = np.where(np.isfinite(monthly).any(axis=1), np.nanmax(
            np.where(np.isfinite(monthly), monthly, -np.inf), axis=1), np.nan)
    out.loc[out["q_seasonal_alteration"] == -np.inf, "q_seasonal_alteration"] = np.nan
    return out.sort_values("comid").reset_index(drop=True)


def inputs_erom(root: DataRoot, options: Optional[dict] = None) -> str:
    from ..stages.score import erom_stamp
    return digest("erom", ANALYSIS_VERSION, erom_stamp(root), 2)


def run_erom(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pyarrow.parquet as pq
    if not root.erom.exists():
        raise RuntimeError("national/erom.parquet not found: run the national erom step first")
    control.check()
    frame = pq.read_table(root.erom).to_pandas()
    out = erom_metrics(frame)
    path = erom_path(root)
    # The runtime cache deliberately omits QA/QC and area. Retain those
    # historical analysis quantities from stored evidence when available.
    if path.exists():
        previous = pq.read_table(path).to_pandas().set_index("comid")
        refreshed = {"qe_ma", "q_min_ratio", "q_max_ratio", "q_cv_monthly"}
        for name in previous.columns:
            if name not in refreshed:
                out[name] = out["comid"].map(previous[name])
    path = common.write_parquet(out, path)
    progress.say(f"erom.parquet: {len(out):,} reaches from stored national flow evidence")
    return path


# ------------------------------------------------------------ ATTAINS uses
USE_COLUMNS = ("cultural_use", "drinkingwater_use", "ecological_use", "fishconsumption_use",
               "recreation_use", "other_use")
CAUSE_COLUMNS = (
    "algal_growth", "ammonia", "biotoxins", "cause_unknown", "cause_unknown_fish_kills",
    "cause_unknown_impaired_biota", "chlorine", "dioxins", "fish_consumption_advisory",
    "flow_alterations", "habitat_alterations", "hydrologic_alteration", "mercury",
    "metals_other_than_mercury", "noxious_aquatic_plants", "nuisance_exotic_species",
    "nuisance_native_species", "nutrients", "oil_and_grease", "oxygen_depletion", "other_cause",
    "pathogens", "pesticides", "pfas", "ph_acidity_caustic_conditions",
    "polychlorinated_biphenyls_pcbs", "radiation", "solids_chlorides_sulfates", "sediment",
    "taste_color_and_odor", "temperature", "total_toxics", "toxic_inorganics", "toxic_organics",
    "trash", "turbidity",
)
#: causes that do not bear on aquatic life (human-use listings)
NON_AQUATIC_CAUSES = ("mercury", "polychlorinated_biphenyls_pcbs", "fish_consumption_advisory",
                      "pathogens", "taste_color_and_odor", "dioxins", "radiation")
RATING_MODES = ("strict", "cause_screen")


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def _yes(value) -> bool:
    return _text(value).upper() in ("Y", "YES", "TRUE", "1")


def aquatic_life_rating(row: dict, *, mode: str = "strict") -> tuple[Optional[str], str]:
    """``(rating, reason)`` for one assessment unit from its aquatic-life
    (``ecological_use``) status. ``strict``: Fully Supporting is Good; Not
    Supporting is Fair with a restoration plan (category 4A or 4B, a TMDL, a
    4B or alternative plan) and Poor otherwise; anything else is unrated so
    the fallback decides. ``cause_screen`` adds two Good cases: an overall
    status of Fully Supporting, and units whose only causes are listings that
    do not bear on aquatic life (mercury, PCBs, fish-consumption advisories,
    pathogens, taste and odour, dioxins, radiation)."""
    if mode not in RATING_MODES:
        raise ValueError(f"unknown rating mode {mode!r}")
    use = _text(row.get("ecological_use"))
    category = _text(row.get("ircategory")).upper()
    has_plan = any(_yes(row.get(key)) for key in ("hastmdl", "has4bplan", "hasalternativeplan"))
    if use == "Fully Supporting":
        return "Good", "aquatic-life use fully supporting"
    if use == "Not Supporting":
        if category in ("4A", "4B") or has_plan:
            return "Fair", "aquatic-life use not supporting, restoration plan in place"
        return "Poor", "aquatic-life use not supporting"
    if mode == "cause_screen":
        if _text(row.get("overallstatus")) == "Fully Supporting":
            return "Good", "overall status fully supporting"
        causes = [name for name in CAUSE_COLUMNS if _text(row.get(name)) == "Cause"]
        if causes and all(name in NON_AQUATIC_CAUSES for name in causes):
            return "Good", "impaired only for causes that do not bear on aquatic life: " + ", ".join(causes)
    return None, f"aquatic-life use {use or 'not reported'}"


def cause_list(row: dict) -> list[str]:
    """The cause columns marked ``Cause`` on a unit."""
    return [name for name in CAUSE_COLUMNS if _text(row.get(name)) == "Cause"]


def inputs_attains(root: DataRoot, options: Optional[dict] = None) -> str:
    gdb = local_gdb.attains_gdb(root)
    return digest("attains_attributes", ANALYSIS_VERSION, gdb.name if gdb else "", 1)


def run_attains(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    frame = local_gdb.read_attains_attributes(root, progress)
    frame = frame.sort_values("assessmentunitidentifier").reset_index(drop=True)
    dupes = int(frame["assessmentunitidentifier"].duplicated().sum())
    ratings = {mode: [aquatic_life_rating(row, mode=mode)[0] for row in frame.to_dict("records")]
               for mode in RATING_MODES}
    for mode, values in ratings.items():
        frame[f"aquatic_life_{mode}"] = values
    frame["causes"] = [json.dumps(cause_list(row)) for row in frame.to_dict("records")]
    path = common.write_parquet(frame, attains_attributes_path(root))
    counts = {mode: {str(k): int(v) for k, v in
                     __import__("collections").Counter(values).items()} for mode, values in ratings.items()}
    progress.say(f"attains_au_attributes.parquet: {len(frame):,} assessment units"
                 + (f", {dupes:,} duplicate ids" if dupes else "") + f"; ratings {counts}")
    return path
