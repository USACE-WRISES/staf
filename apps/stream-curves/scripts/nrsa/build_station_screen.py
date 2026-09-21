"""Build the static station screen table (rule REF-04, methodology 0.12).

One row per station of the pooled NRSA archive: where it sits (Level III, II
and I ecoregion, NARS-9, HUC12), what the reach is (stream order, NHDPlus slope
and drainage area with EASI's classes, the feature class), its natural setting
(elevation, precipitation, temperature, runoff, base flow index, hydraulic
conductivity, soil erodibility, surficial lithology), the seven variables of
EASI's ``least-disturbed-v1`` screen, and the strict and relaxed verdicts.

A regional build reads this table and nothing else to decide reference
membership, so a run needs no live service, costs nothing, and cannot drift with
the EASI condition index. The table is small and committed, like
``stream_order.csv``; rebuild it when the archive gains stations or EPA
republishes StreamCat.

    py -3.12 scripts/nrsa/build_station_screen.py --fast-path D:\\Data\\easi-national
    py -3.12 scripts/nrsa/build_station_screen.py                  # every source from the APIs
    py -3.12 scripts/nrsa/build_station_screen.py --parity D:\\Data\\easi-national
    py -3.12 scripts/nrsa/build_station_screen.py --verify         # offline, no network
    py -3.12 scripts/nrsa/build_station_screen.py --probe          # do the lithology names resolve
    py -3.12 scripts/nrsa/build_station_screen.py --limit 200      # a smoke test (writes nothing)

``--fast-path`` reads the local EASI national store for everything it holds
(StreamCat by COMID, the NHDPlus attributes, HUC12, the ECI) and fetches only
lithology, which that store lacks. The two paths share every derivation
(``streamcurves.reference_screen``), and ``--parity`` reports where they differ.
The builder refuses to write a table when a StreamCat chunk failed: a partial
fetch would read as a failed screen for the stations it missed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT))

from streamcurves import reference_screen as rscreen  # noqa: E402
from streamcurves.datasources.streamcat import streamcat_metrics  # noqa: E402

OUT_DIR = APP_ROOT / "data" / "nrsa"
TABLE_PATH = OUT_DIR / "station_screen.parquet"
META_PATH = OUT_DIR / "station_screen.meta.json"
CACHE_DIR = APP_ROOT / "scripts" / "out" / "station_screen_cache"
PARITY_PATH = APP_ROOT / "scripts" / "out" / "station_screen_parity.json"
CROSSWALK_PATH = (APP_ROOT / "streamcurves" / "_vendor" / "easi" / "data"
                  / "ecoregion-crosswalk.json")
RAW_DIR = APP_ROOT.parents[1] / "notes" / "DEEP_Working" / "nrsa_raw"
SITE_FILE_1314 = "1314/nrsa1314_siteinformation_wide_04292019.csv"

FABRIC_URL = ("https://api.water.usgs.gov/fabric/pygeoapi/collections/"
              "nhdflowline_network/items")
FABRIC_CHUNK = 40               # COMIDs per CQL `IN` filter (the API refuses long ones)
TIMEOUT = 120.0
RETRIES = 3

# StreamCat base names (the watershed column is the name plus "ws").
SCREEN_NAMES = ("pctimp2019", "pctcrop2019", "pcthay2019", "rddens", "damnrmstor",
                "runoff", "nabd_dens", "npdesdens", "minedens", "coalminedens")
SETTING_NAMES = ("elev", "precip8110", "tmean8110", "bfi", "hydrlcond", "kffact")
# Landscape EXPECTATION metrics (scored against reference, rule CURVE-11 keeps them off
# fixed criteria): wetland cover by class, summed below as StreamCurves and EASI both do.
EXPECTATION_NAMES = ("pctwdwet2019", "pcthbwet2019")
LITHOLOGY_NAMES = (
    "pctcarbresid", "pctnoncarbresid", "pctalkintruvol", "pctsilicic", "pctextruvol",
    "pctcolluvsed", "pctglactilclay", "pctglactilloam", "pctglactilcrs", "pctglaclakecrs",
    "pctglaclakefine", "pcthydric", "pcteolcrs", "pcteolfine", "pctsallake",
    "pctalluvcoast", "pctcoastcrs", "pctwater")
# Nine groups a reviewer can read, from StreamCat's eighteen surficial classes.
LITH_GROUPS: dict[str, tuple[str, ...]] = {
    "lith_carbonate": ("pctcarbresid",),
    "lith_noncarb_resid": ("pctnoncarbresid",),
    "lith_igneous_volcanic": ("pctalkintruvol", "pctsilicic", "pctextruvol"),
    "lith_colluvial": ("pctcolluvsed",),
    "lith_glacial_till": ("pctglactilclay", "pctglactilloam", "pctglactilcrs"),
    "lith_glacial_outwash_lake": ("pctglaclakecrs", "pctglaclakefine"),
    "lith_alluvium_coastal": ("pctalluvcoast", "pctcoastcrs"),
    "lith_eolian": ("pcteolcrs", "pcteolfine"),
    "lith_other": ("pcthydric", "pctsallake", "pctwater"),
}
LITH_DOMINANCE_MIN_PCT = 50.0   # below this the watershed's lithology is "mixed"
NAME_GROUP = 12                 # StreamCat names per request, to keep URLs short

# EASI's feature classes (tools/easi-national/builder/analysis/strata.py).
FCODE_CLASS = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
               33600: "canal", 33601: "canal", 33603: "canal",
               55800: "artificial_path", 42800: "pipeline", 56600: "coastline"}
WADEABLE_MAX_ORDER = 5

# CEC Level I ecological regions; the vendored crosswalk carries codes only.
L1_NAMES = {"5": "Northern Forests", "6": "Northwestern Forested Mountains",
            "7": "Marine West Coast Forest", "8": "Eastern Temperate Forests",
            "9": "Great Plains", "10": "North American Deserts",
            "11": "Mediterranean California", "12": "Southern Semiarid Highlands",
            "13": "Temperate Sierras", "15": "Tropical Wet Forests"}

COLUMNS = [
    "station_key", "comid", "comid_source",
    "l3", "l3_name", "l2", "l2_name", "l1", "l1_name", "nars9", "huc8", "huc12",
    "stream_order", "drainage_area_sqkm", "nhd_slope", "fcode", "fcode_class", "wadeable",
    "slope_class", "da_class",
    "elevws", "precip8110ws", "tmean8110ws", "runoffws", "bfiws", "hydrlcondws", "kffactws",
    "pctwdwet2019ws", "pcthbwet2019ws", "pctwet2019ws",
    *LITH_GROUPS, "lith_group", "lith_group_pct",
    "pctimp2019ws", "pctcrop2019ws", "pcthay2019ws", "agriculture_ws", "rddensws",
    "damnrmstorws", "dor", "nabd_densws", "npdesdensws", "minedensws", "coalminedensws",
    "mines_ws",
    "screen_evaluable", "pass_strict", "pass_relaxed", "fail_strict", "fail_relaxed",
    "rt_nrsa", "rt_nrsa_cycle", "eci_raw",
    "source", "fetched_at",
]
FLOAT_COLUMNS = [
    "drainage_area_sqkm", "nhd_slope", "elevws", "precip8110ws", "tmean8110ws", "runoffws",
    "bfiws", "hydrlcondws", "kffactws", "pctwdwet2019ws", "pcthbwet2019ws", "pctwet2019ws",
    "pctimp2019ws", "pctcrop2019ws", "pcthay2019ws",
    "agriculture_ws", "rddensws", "damnrmstorws", "dor", "nabd_densws", "npdesdensws",
    "minedensws", "coalminedensws", "mines_ws", "eci_raw"]
FLOAT32_COLUMNS = [*LITH_GROUPS, "lith_group_pct"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _l3_code(value) -> Optional[str]:
    text = str(value).strip() if value is not None and not pd.isna(value) else ""
    if text.endswith(".0"):
        text = text[:-2]
    return text or None


# --------------------------------------------------------------------------- #
# stations and geography
# --------------------------------------------------------------------------- #
def station_frame() -> pd.DataFrame:
    """The archive's stations with their Level III, II and I ecoregions."""
    stations = pd.read_parquet(OUT_DIR / "stations.parquet")
    cross = json.loads(CROSSWALK_PATH.read_text(encoding="utf-8"))
    l3_map, l2_map = cross.get("l3") or {}, cross.get("l2") or {}
    out = pd.DataFrame({"station_key": stations["station_key"].astype(str)})
    comid = pd.to_numeric(stations["comid"], errors="coerce")
    out["comid"] = comid.where(comid > 0).astype("Int64")
    out["comid_source"] = stations.get("comid_source")
    out["l3"] = [_l3_code(v) for v in stations["us_l3code"]]
    out["l3_name"] = stations["us_l3name"].astype(object)
    out["l2"] = [(l3_map.get(c) or {}).get("l2") if c else None for c in out["l3"]]
    out["l2_name"] = [((l2_map.get(c) or {}).get("name") or "").title() or None
                      if c else None for c in out["l2"]]
    out["l1"] = [(l3_map.get(c) or {}).get("l1") if c else None for c in out["l3"]]
    out["l1_name"] = [L1_NAMES.get(str(c)) if c else None for c in out["l1"]]
    out["nars9"] = stations["ag_eco9"].astype(object)
    out["huc8"] = (stations["huc8"].astype(str).str.replace("^H", "", regex=True)
                   .where(stations["huc8"].notna()))
    return out.sort_values("station_key").reset_index(drop=True)


def station_comids(frame: pd.DataFrame) -> list[int]:
    return sorted({int(c) for c in frame["comid"].dropna()})


# --------------------------------------------------------------------------- #
# the API path
# --------------------------------------------------------------------------- #
def fetch_flowline_attrs(comids: list[int], *, on_progress=None
                         ) -> tuple[pd.DataFrame, list[int]]:
    """NHDPlus V2 order, drainage area, slope and feature code from the USGS
    fabric API. Returns ``(frame, failed_chunk_starts)``."""
    rows: list[dict] = []
    failed: list[int] = []
    for i in range(0, len(comids), FABRIC_CHUNK):
        part = comids[i:i + FABRIC_CHUNK]
        params = {"filter": "comid IN (" + ",".join(str(c) for c in part) + ")",
                  "properties": "comid,streamorde,totdasqkm,slope,fcode",
                  "f": "json", "limit": len(part) + 5}
        got = None
        for attempt in range(RETRIES):
            try:
                r = requests.get(FABRIC_URL, params=params, timeout=TIMEOUT)
                r.raise_for_status()
                got = r.json().get("features") or []
                break
            except Exception as exc:  # noqa: BLE001 - a flaky service must not end the run
                print(f"  fabric chunk at {i}: attempt {attempt + 1} failed "
                      f"({type(exc).__name__})", flush=True)
                time.sleep(2.0 * (attempt + 1))
        if got is None:
            failed.append(i)
            continue
        for f in got:
            p = f.get("properties") or {}
            if p.get("comid") is None:
                continue
            rows.append({"comid": int(p["comid"]), "streamorde": p.get("streamorde"),
                         "totdasqkm": p.get("totdasqkm"), "slope": p.get("slope"),
                         "fcode": p.get("fcode")})
        if on_progress is not None:
            on_progress(min(i + FABRIC_CHUNK, len(comids)), len(comids))
    frame = pd.DataFrame(rows, columns=["comid", "streamorde", "totdasqkm", "slope", "fcode"])
    return frame.drop_duplicates("comid"), failed


def fetch_streamcat(comids: list[int], names, *, label: str) -> tuple[pd.DataFrame, list]:
    """StreamCat watershed values for ``names``, fetched in small name groups
    and cached per group so an interrupted build resumes. Returns
    ``(frame keyed by comid, failures)``."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    names = list(names)
    merged: Optional[pd.DataFrame] = None
    failures: list = []
    for g in range(0, len(names), NAME_GROUP):
        group = names[g:g + NAME_GROUP]
        cache = CACHE_DIR / f"{label}_{g // NAME_GROUP:02d}.parquet"
        frame = None
        if cache.exists():
            cached = pd.read_parquet(cache)
            if set(comids) <= set(cached["comid"].astype("int64")):
                frame = cached
        if frame is None:
            print(f"  streamcat {label} group {g // NAME_GROUP + 1}: "
                  f"{len(group)} names x {len(comids)} COMIDs", flush=True)
            got = streamcat_metrics(comids, group, area="watershed", batch=200)
            failed = list(got.attrs.get("failed_chunks") or [])
            if failed:
                failures.append({"names": group, "failed_chunks": failed})
            frame = got.rename(columns={"COMID": "comid"})
            frame.columns = [str(c).lower() for c in frame.columns]
            if len(frame) and not failed:
                frame.to_parquet(cache, index=False)
        if frame is None or not len(frame):
            continue
        frame = frame.drop_duplicates("comid")
        merged = frame if merged is None else merged.merge(frame, on="comid", how="outer")
    if merged is None:
        merged = pd.DataFrame({"comid": pd.Series([], dtype="int64")})
    return merged, failures


def probe_names(names, comid: int = 9327042) -> dict:
    """Which StreamCat names resolve on the live API, tested on one COMID."""
    found, missing = [], []
    for name in names:
        got = streamcat_metrics([comid], [name], area="watershed")
        (found if f"{name}ws" in {str(c).lower() for c in got.columns} else missing).append(name)
    return {"found": found, "missing": missing}


# --------------------------------------------------------------------------- #
# the fast path: the local EASI national store
# --------------------------------------------------------------------------- #
def read_fast_path(root: Path, comids: list[int]) -> dict[str, pd.DataFrame]:
    """StreamCat, NHDPlus attributes, HUC12 and the ECI from the local store."""
    import pyarrow.dataset as pads
    import pyarrow.compute as pc

    def _read(path: Path, columns: list[str]) -> pd.DataFrame:
        ds = pads.dataset(str(path), format="parquet")
        have = [c for c in columns if c in ds.schema.names]
        table = ds.to_table(columns=have, filter=pc.is_in(pc.field("comid"),
                                                          value_set=_pa_array(comids)))
        return table.to_pandas().drop_duplicates("comid")

    sc_cols = ["comid"] + [f"{n}ws" for n in (*SCREEN_NAMES, *SETTING_NAMES, *EXPECTATION_NAMES)]
    core = _read(root / "national" / "streamcat.parquet", sc_cols)
    cand = _read(root / "analysis" / "streamcat_candidates.parquet", sc_cols)
    streamcat = core.merge(cand[[c for c in cand.columns
                                 if c == "comid" or c not in core.columns]],
                           on="comid", how="outer")
    strata = _read(root / "analysis" / "strata.parquet",
                   ["comid", "huc12", "slope", "totdasqkm", "fcode", "streamorde"])
    strata = strata.rename(columns={"streamorde": "streamorde", "totdasqkm": "totdasqkm"})
    desktop = pd.read_parquet(root / "analysis" / "nrsa" / "nrsa_desktop.parquet",
                              columns=["station_key", "eci_raw"])
    targets = pd.read_parquet(root / "analysis" / "nrsa" / "nrsa_targets.parquet",
                              columns=["station_key", "rt_nrsa"])
    return {"streamcat": streamcat, "flowline": strata, "eci": desktop, "rt": targets}


def _pa_array(values):
    import pyarrow as pa
    return pa.array([int(v) for v in values], type=pa.int64())


def read_rt_nrsa(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """EPA's 2013-14 reference designation (R, In, Im) by station, read from the
    raw site file through the archive's visit table. Empty when the raw files
    are not on this machine (they are never committed)."""
    site_file = raw_dir / SITE_FILE_1314
    if not site_file.exists():
        return pd.DataFrame(columns=["station_key", "rt_nrsa"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from nrsa_io import read_epa_csv  # noqa: E402
    sites = read_epa_csv(site_file)
    cols = {str(c).upper(): c for c in sites.columns}
    if "SITE_ID" not in cols or "RT_NRSA" not in cols:
        return pd.DataFrame(columns=["station_key", "rt_nrsa"])
    rt = sites[[cols["SITE_ID"], cols["RT_NRSA"]]].copy()
    rt.columns = ["site_id", "rt_nrsa"]
    rt["rt_nrsa"] = rt["rt_nrsa"].astype(str).str.strip()
    rt = rt[rt["rt_nrsa"].isin(["R", "In", "Im"])].drop_duplicates("site_id")
    visits = pd.read_parquet(OUT_DIR / "site_visits.parquet",
                             columns=["cycle", "site_id", "station_key"])
    visits = visits[visits["cycle"].astype(str) == "1314"]
    out = visits.merge(rt, on="site_id", how="inner")[["station_key", "rt_nrsa"]]
    return out.drop_duplicates("station_key")


# --------------------------------------------------------------------------- #
# assembly (shared by both paths)
# --------------------------------------------------------------------------- #
def lithology_groups(streamcat: pd.DataFrame) -> pd.DataFrame:
    """The nine groups, the dominant one, and its share. Rows with no
    lithology value stay missing (never zero)."""
    out = pd.DataFrame(index=streamcat.index)
    have_any = pd.Series(False, index=streamcat.index)
    for group, parts in LITH_GROUPS.items():
        cols = [f"{p}ws" for p in parts if f"{p}ws" in streamcat.columns]
        if not cols:
            out[group] = np.nan
            continue
        vals = streamcat[cols].apply(pd.to_numeric, errors="coerce")
        have_any |= vals.notna().any(axis=1)
        out[group] = vals.sum(axis=1, min_count=1)
    groups = list(LITH_GROUPS)
    filled = out[groups].fillna(0.0)
    top = filled.idxmax(axis=1)
    top_pct = filled.max(axis=1)
    out["lith_group"] = np.where(~have_any, None,
                                 np.where(top_pct >= LITH_DOMINANCE_MIN_PCT,
                                          top.str.replace("lith_", "", regex=False), "mixed"))
    out["lith_group_pct"] = top_pct.where(have_any)
    out.loc[~have_any, groups] = np.nan
    return out


def assemble(frame: pd.DataFrame, flowline: pd.DataFrame, streamcat: pd.DataFrame, *,
             rt: Optional[pd.DataFrame] = None, eci: Optional[pd.DataFrame] = None,
             source: str, fetched_at: str) -> pd.DataFrame:
    """Join the pieces, derive the screen variables and the verdicts."""
    table = frame.copy()
    # ``huc12`` rides in on the flowline attributes when the source has it (the
    # national store does, the fabric API does not).
    flow = flowline.rename(columns={"streamorde": "fl_order", "totdasqkm": "fl_da",
                                    "slope": "fl_slope", "fcode": "fl_fcode"})
    flow = flow.drop_duplicates("comid")
    flow["comid"] = pd.to_numeric(flow["comid"], errors="coerce").astype("Int64")
    table = table.merge(flow, on="comid", how="left")
    # Stream order: the committed cache DATA-10 already frames on comes first, so
    # the table and the reference frame can never disagree about a station.
    order_cache = pd.read_csv(OUT_DIR / "stream_order.csv") \
        if (OUT_DIR / "stream_order.csv").exists() else pd.DataFrame(
            columns=["comid", "stream_order", "drainage_area_sqkm"])
    order_cache = order_cache.drop_duplicates("comid")[
        ["comid", "stream_order", "drainage_area_sqkm"]].rename(
        columns={"stream_order": "oc_order", "drainage_area_sqkm": "oc_da"})
    table = table.merge(order_cache, on="comid", how="left")
    order = pd.to_numeric(table["oc_order"], errors="coerce").fillna(
        pd.to_numeric(table.get("fl_order"), errors="coerce"))
    table["stream_order"] = order.round().astype("Int8")
    table["drainage_area_sqkm"] = pd.to_numeric(table["oc_da"], errors="coerce").fillna(
        pd.to_numeric(table.get("fl_da"), errors="coerce"))
    slope = pd.to_numeric(table.get("fl_slope"), errors="coerce")
    table["nhd_slope"] = slope.where(slope >= 0)            # -9998 is NHDPlus's "no value"
    fcode = pd.to_numeric(table.get("fl_fcode"), errors="coerce")
    table["fcode"] = fcode.round().astype("Int32")
    table["fcode_class"] = [FCODE_CLASS.get(int(c), "other") if pd.notna(c) else None
                            for c in fcode]
    table["wadeable"] = (order <= WADEABLE_MAX_ORDER).fillna(False).astype(bool)
    table["slope_class"] = [rscreen.slope_class(v) for v in table["nhd_slope"]]
    table["da_class"] = [rscreen.da_class(v) for v in table["drainage_area_sqkm"]]

    sc = streamcat.copy()
    sc.columns = [str(c).lower() for c in sc.columns]
    table = table.merge(sc.drop_duplicates("comid"), on="comid", how="left")
    lith = lithology_groups(table)
    for col in lith.columns:
        table[col] = lith[col]
    table = rscreen.derive_screen_variables(table)
    # Combined wetland cover, the sum both apps score: both classes required,
    # capped at 100 percent, two decimals (datasources/streamcat.py derive_metrics).
    wet = (pd.to_numeric(table.get("pctwdwet2019ws"), errors="coerce")
           + pd.to_numeric(table.get("pcthbwet2019ws"), errors="coerce"))
    table["pctwet2019ws"] = wet.clip(upper=100.0).round(2)
    strict_ok, strict_why = rscreen.evaluate(table, "strict")
    relaxed_ok, relaxed_why = rscreen.evaluate(table, "relaxed")
    table["pass_strict"], table["fail_strict"] = strict_ok, strict_why
    table["pass_relaxed"], table["fail_relaxed"] = relaxed_ok, relaxed_why
    # Evaluable means every strict variable has a value: a fail on a missing
    # value is a different statement from a fail on a measured one.
    table["screen_evaluable"] = ~table["fail_strict"].str.contains("missing:", na=False)

    table["rt_nrsa"] = None
    table["rt_nrsa_cycle"] = None
    if rt is not None and len(rt):
        by = rt.dropna(subset=["rt_nrsa"]).drop_duplicates("station_key").set_index(
            "station_key")["rt_nrsa"]
        table["rt_nrsa"] = table["station_key"].map(by)
        table.loc[table["rt_nrsa"].notna(), "rt_nrsa_cycle"] = "1314"
    table["eci_raw"] = np.nan
    if eci is not None and len(eci):
        by = eci.drop_duplicates("station_key").set_index("station_key")["eci_raw"]
        table["eci_raw"] = table["station_key"].map(by)
    if "huc12" not in table.columns:
        table["huc12"] = None
    table["source"] = source
    table["fetched_at"] = fetched_at

    for col in COLUMNS:
        if col not in table.columns:
            table[col] = np.nan
    table = table[COLUMNS].sort_values("station_key").reset_index(drop=True)
    for col in FLOAT_COLUMNS:
        table[col] = pd.to_numeric(table[col], errors="coerce").astype("float64")
    for col in FLOAT32_COLUMNS:
        table[col] = pd.to_numeric(table[col], errors="coerce").astype("float32")
    for col in ("screen_evaluable", "pass_strict", "pass_relaxed", "wadeable"):
        table[col] = table[col].fillna(False).astype(bool)
    return table


def summarize(table: pd.DataFrame) -> dict:
    frame = table["wadeable"] & (table["fcode_class"] != "canal")
    return {"rows": int(len(table)),
            "withComid": int(table["comid"].notna().sum()),
            "evaluable": int(table["screen_evaluable"].sum()),
            "inEasiFrame": int(frame.sum()),
            "strictInEasiFrame": int((frame & table["pass_strict"]).sum()),
            "relaxedInEasiFrame": int((frame & table["pass_relaxed"]).sum()),
            "withLithology": int(table["lith_group"].notna().sum()),
            "withHuc12": int(table["huc12"].notna().sum()),
            "withRtNrsa": int(table["rt_nrsa"].notna().sum())}


def write_table(table: pd.DataFrame, *, source: str, extra: Optional[dict] = None) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    table.to_parquet(TABLE_PATH, index=False, compression="zstd")
    try:
        from streamcurves._vendor.easi import national
        easi_method = national.method_version()
    except Exception:  # noqa: BLE001 - the digest is context, not a build input
        easi_method = None
    meta = {
        "schemaVersion": 1,
        "file": TABLE_PATH.name,
        "sha256": rscreen.file_sha256(TABLE_PATH),
        "rows": int(len(table)),
        "columns": {c: str(table[c].dtype) for c in table.columns},
        "screenId": rscreen.SCREEN_ID,
        "screen": {t: {k: [op, v] for k, (op, v) in rscreen.rules(t).items()}
                   for t in rscreen.TIERS},
        "easiMethodVersion": easi_method,
        "source": source,
        "builtAt": _now(),
        "summary": summarize(table),
        **(extra or {}),
    }
    META_PATH.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


# --------------------------------------------------------------------------- #
# verify and parity
# --------------------------------------------------------------------------- #
def cmd_verify() -> int:
    """Offline: the committed table still says what its inputs say."""
    problems: list[str] = list(rscreen.screen_drift())
    if not TABLE_PATH.exists() or not META_PATH.exists():
        print("station_screen.parquet or its meta file is missing")
        return 1
    table = pd.read_parquet(TABLE_PATH)
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    if rscreen.file_sha256(TABLE_PATH) != meta.get("sha256"):
        problems.append("the table's sha256 is not the one its meta file records")
    stations = pd.read_parquet(OUT_DIR / "stations.parquet", columns=["station_key"])
    if set(table["station_key"]) != set(stations["station_key"].astype(str)):
        problems.append("the table's stations are not the archive's stations")
    if list(table.columns) != COLUMNS:
        problems.append("the table's columns are not the contract's")
    again = rscreen.derive_screen_variables(
        table.drop(columns=["agriculture_ws", "dor", "mines_ws"]))
    for col in ("agriculture_ws", "dor", "mines_ws"):
        if not np.allclose(again[col], table[col], equal_nan=True):
            problems.append(f"{col} no longer recomputes from its stored parts")
    for tier in rscreen.TIERS:
        ok, why = rscreen.evaluate(table, tier)
        if not ok.equals(table[f"pass_{tier}"]):
            problems.append(f"pass_{tier} no longer recomputes from the stored variables")
        if not why.astype(object).equals(table[f"fail_{tier}"].astype(object)):
            problems.append(f"fail_{tier} no longer recomputes from the stored variables")
    rules_now = {t: {k: [op, v] for k, (op, v) in rscreen.rules(t).items()}
                 for t in rscreen.TIERS}
    if rules_now != meta.get("screen"):
        problems.append("the governed screen changed since the table was built")
    for p in problems:
        print(f"  - {p}")
    print("station screen: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def parity_report(api: pd.DataFrame, store: pd.DataFrame, *, tol: float = 1e-6) -> dict:
    """Where the API build and the store build disagree, column by column."""
    a = api.set_index("station_key")
    b = store.set_index("station_key").reindex(a.index)
    report: dict = {"rows": int(len(a)), "columns": {}}
    for col in COLUMNS:
        if col in ("station_key", "source", "fetched_at", "eci_raw", "huc12", "rt_nrsa",
                   "rt_nrsa_cycle"):
            continue
        x, y = a[col], b[col]
        if pd.api.types.is_float_dtype(x) or pd.api.types.is_float_dtype(y):
            xv = pd.to_numeric(x, errors="coerce")
            yv = pd.to_numeric(y, errors="coerce")
            both = xv.notna() & yv.notna()
            differ = both & ((xv - yv).abs() > tol * np.maximum(1.0, yv.abs()))
            only = int((xv.notna() ^ yv.notna()).sum())
            report["columns"][col] = {"differ": int(differ.sum()), "onlyOneSide": only,
                                      "maxAbs": float((xv - yv).abs()[both].max())
                                      if both.any() else None}
        else:
            differ = ~((x == y) | (x.isna() & y.isna()))
            report["columns"][col] = {"differ": int(differ.sum())}
    report["nonZero"] = {k: v for k, v in report["columns"].items()
                         if v.get("differ") or v.get("onlyOneSide")}
    return report


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build(*, fast_path: Optional[Path], limit: Optional[int], lithology: bool
          ) -> tuple[pd.DataFrame, list, str]:
    frame = station_frame()
    comids = station_comids(frame)
    if limit:
        comids = comids[:limit]
        frame = frame[frame["comid"].isin(comids)]
    failures: list = []
    if fast_path is not None:
        source = "easi-national-store"
        got = read_fast_path(fast_path, comids)
        flowline, streamcat, eci = got["flowline"], got["streamcat"], got["eci"]
        rt = got["rt"].dropna(subset=["rt_nrsa"])
    else:
        source = "streamcat-api"
        flowline, failed = fetch_flowline_attrs(
            comids, on_progress=lambda i, n: print(f"  fabric {i}/{n}", flush=True))
        if failed:
            failures.append({"fabric_failed_chunks": failed})
        streamcat, sc_fail = fetch_streamcat(
            comids, (*SCREEN_NAMES, *SETTING_NAMES, *EXPECTATION_NAMES), label="screen")
        failures += sc_fail
        eci, rt = None, read_rt_nrsa()
    if lithology:
        lith, lith_fail = fetch_streamcat(comids, LITHOLOGY_NAMES, label="lithology")
        failures += lith_fail
        streamcat = streamcat.merge(lith, on="comid", how="left")
    table = assemble(frame, flowline, streamcat, rt=rt, eci=eci, source=source,
                     fetched_at=_now())
    return table, failures, source


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast-path", default=None, metavar="ROOT",
                    help="the local EASI national store (D:\\Data\\easi-national)")
    ap.add_argument("--parity", default=None, metavar="ROOT",
                    help="build both ways and report the differences; writes no table")
    ap.add_argument("--verify", action="store_true", help="offline check of the committed table")
    ap.add_argument("--probe", action="store_true", help="test the StreamCat names on one COMID")
    ap.add_argument("--no-lithology", action="store_true",
                    help="skip the lithology fetch (the groups stay missing)")
    ap.add_argument("--limit", type=int, default=None, help="smoke test; writes nothing")
    a = ap.parse_args()

    if a.verify:
        return cmd_verify()
    if a.probe:
        got = probe_names((*SCREEN_NAMES, *SETTING_NAMES, *LITHOLOGY_NAMES))
        print(json.dumps(got, indent=1))
        return 1 if got["missing"] else 0
    drift = rscreen.screen_drift()
    if drift:
        print("the governed screen does not match the vendored EASI screen:")
        for p in drift:
            print(f"  - {p}")
        return 1
    if a.parity:
        store, _, _ = build(fast_path=Path(a.parity), limit=a.limit,
                            lithology=not a.no_lithology)
        api, failures, _ = build(fast_path=None, limit=a.limit, lithology=not a.no_lithology)
        report = parity_report(api, store)
        report["apiFailures"] = failures
        PARITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        PARITY_PATH.write_text(json.dumps(report, indent=1, default=str) + "\n",
                               encoding="utf-8")
        print(json.dumps(report["nonZero"], indent=1, default=str))
        print(f"wrote {PARITY_PATH}")
        return 0

    table, failures, source = build(
        fast_path=Path(a.fast_path) if a.fast_path else None, limit=a.limit,
        lithology=not a.no_lithology)
    print(json.dumps(summarize(table), indent=1))
    if failures:
        print("refusing to write the table: some requests failed, and a missing value reads "
              "as a failed screen")
        print(json.dumps(failures, indent=1, default=str))
        return 1
    if a.limit:
        print("--limit is a smoke test; nothing written")
        return 0
    meta = write_table(table, source=source)
    print(f"wrote {TABLE_PATH.relative_to(APP_ROOT)} ({meta['rows']} rows, "
          f"sha256 {meta['sha256'][:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
