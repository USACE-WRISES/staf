"""WQP stream temperature for EASI's temperature context (bundle v2).

EASI shows nearby WQP water temperature beside M13 (station-balanced median within 5 miles over the
last ten years, never scored); the bundle answers it locally from every result, like TN and TP.

``pull`` fetches every month from 2016-01 (EASI's ten-year window with room to roll until the next
refresh) through the national builder's own month downloader (``wqp_national.download_month``: a
cut window is halved and retried, an incomplete answer is never kept) with the request switched to
the characteristic ``Temperature, water`` and the ``basicPhysChem`` profile (every field EASI reads,
the result identifier and the detection limits), into ``<sources>/wqp/temperature/monthly/``. The
monthly CSVs stay there as the reference copy. ``convert`` turns each month into two parquet parts:
``reference/`` keeps every column (text) for looking a result up by its identifier (``record``), and
``normalized/`` keeps what the bundle table needs.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Callable

from . import REPO_ROOT, sources

FOLDER = sources.SOURCES_DIR / "wqp" / "temperature"
START = "2016-01"
CHARACTERISTIC = "Temperature, water"
PROFILE = "basicPhysChem"
ID_COLUMN = "Result_MeasureIdentifier"


def months(start: str = START, end: str | None = None) -> list[str]:
    today = date.today()
    y, m = (int(v) for v in start.split("-"))
    ey, em = (int(v) for v in (end or f"{today.year}-{today.month:02d}").split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _builder():
    """The national builder's downloader with the request switched to stream temperature (this
    process only; the builder's own runs are untouched)."""
    path = str(REPO_ROOT / "tools" / "easi-national")
    if path not in sys.path:
        sys.path.insert(0, path)
    from builder.stages import wqp_national as wq

    def params_for_window(lo, hi):
        out = [("countrycode", "US"), ("siteType", "Stream"), ("characteristicName", CHARACTERISTIC)]
        out += [("startDateLo", wq._portal_date(lo)), ("startDateHi", wq._portal_date(hi)), ("mimeType", "csv"),
                ("dataProfile", PROFILE)]
        out += [("providers", p) for p in wq.PROVIDERS]
        return out
    wq.params_for_window = params_for_window
    return wq


def pull(month_list=None, workers: int = 3, log: Callable = print) -> dict:
    wq = _builder()
    folder = FOLDER / "monthly"
    folder.mkdir(parents=True, exist_ok=True)
    todo = month_list or months()
    results = {}

    def one(month):
        dest = folder / f"wqx3_{month}.csv"
        if dest.exists():
            return month, {"status": "done", "rows": None, "bytes": dest.stat().st_size}
        r = {"status": "failed"}
        for attempt, wait in enumerate((0, 60, 180, 600, 1200)):
            time.sleep(wait)
            r = wq.download_month(month, dest)
            if r["status"] == "done":
                return month, r
            log(f"wqp temperature {month}: attempt {attempt + 1} {r['status']} {r.get('error')}")
        return month, r

    total = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, m) for m in todo]):
            month, r = fut.result()
            results[month] = r
            total += int(r.get("bytes") or 0)
            log(f"wqp temperature {month}: {r['status']} {r.get('rows')} rows, {int(r.get('bytes') or 0) / 1e6:.0f} MB")
    failed = sorted(m for m, r in results.items() if r["status"] != "done")
    sources.record("wqp_temperature", {"months": todo, "failed": failed, "bytes": total, "profile": PROFILE,
                                       "characteristic": CHARACTERISTIC,
                                       "service": "WQP WQX3 Result/search (the national builder's downloader)"})
    return {"months": len(todo), "failed": failed, "bytes": total}


# ------------------------------------------------------------------------------------- convert
#: EASI's temperature rule (``easi.datasources.wqp.sample_summary``, the non-nutrient branch) keeps
#: Celsius results only. Results reported in Fahrenheit (about 6% of 2016's, nearly all from stations
#: that report nothing else; paired readings at the same station and time agree to 0.0 C at the
#: median) are kept converted, with their own reason, so an answer can use them where a station has
#: no Celsius result (``hrslim.points.wqp_easi``); a conversion outside -1 to 40 C is set apart.
CELSIUS = {"deg c", "c", "°c", "degrees celsius"}
FAHRENHEIT = {"deg f", "f", "°f", "degrees fahrenheit"}
PLAUSIBLE_C = (-1.0, 40.0)
REASONS = ("ok", "blank", "rejected", "censored", "non_total_fraction", "unsupported_unit", "nonnumeric",
           "fahrenheit", "fahrenheit_implausible", "celsius_implausible", "celsius_seasonal")

#: The owner's screen of Celsius results (2026-10-02). EASI's rule keeps every Celsius number, but
#: about 0.8% are not stream temperatures (Fahrenheit numbers labelled Celsius, sentinels, loggers
#: out of the water). ``screen`` sets apart a Celsius result outside -1 to 40 C
#: (``celsius_implausible``, the bounds the converted Fahrenheit results get) and one that does not
#: fit the season where it was taken (``celsius_seasonal``): read as Celsius it sits far above what
#: streams in the same area record in that calendar month while read as Fahrenheit it fits (winter
#: Fahrenheit readings labelled Celsius, which a fixed bound lets through), or it is at or below
#: freezing in an area and month where nearly every reading is well above it, or it sits far above
#: both the area's month and the station's own summer median (a deployment logged in Fahrenheit
#: outside winter, a logger left in the air). A hot spring reads warm in every month, so a station
#: that reads that warm in that month and stays within 10 C of it through the summer keeps its
#: readings. The area's month comes from the pull itself: 2 degree cells (6 degree where data is
#: thin), from station-month medians so loggers count like grab samples.
CLIMATE_CELLS_DEG = (2.0, 6.0)
CLIMATE_MIN_STATION_MONTHS, CLIMATE_MIN_STATIONS = 30, 5
FLIKE_Z_CELSIUS, FLIKE_Z_FAHRENHEIT, FLIKE_MARGIN_C, SPREAD_FLOOR_C = 5.0, 2.0, 12.0, 1.0
FREEZING_C, WARM_P05_C = 0.0, 8.0
ABOVE_OWN_SUMMER_C = 15.0
HOT_SPRING_NEAR_C, HOT_SPRING_SUMMER_C, HOT_SPRING_MIN_N = 5.0, 10.0, 3
SUMMER_MONTHS = (6, 7, 8)


def screen_parameters() -> dict:
    return {"bounds_c": list(PLAUSIBLE_C), "cells_deg": list(CLIMATE_CELLS_DEG),
            "min_station_months": CLIMATE_MIN_STATION_MONTHS, "min_stations": CLIMATE_MIN_STATIONS,
            "fahrenheit_like": {"z_celsius": FLIKE_Z_CELSIUS, "z_fahrenheit": FLIKE_Z_FAHRENHEIT,
                                "margin_c": FLIKE_MARGIN_C, "spread_floor_c": SPREAD_FLOOR_C},
            "freezing": {"at_or_below_c": FREEZING_C, "area_month_p05_above_c": WARM_P05_C},
            "above_own_summer": {"by_c": ABOVE_OWN_SUMMER_C, "and_fahrenheit_like_z_and_margin": True},
            "hot_spring": {"near_c": HOT_SPRING_NEAR_C, "summer_within_c": HOT_SPRING_SUMMER_C,
                           "min_results": HOT_SPRING_MIN_N, "summer_months": list(SUMMER_MONTHS)}}


def screen(t) -> tuple:
    """The reasons of ``t`` (columns station, lat, lon, date, value, reason; every month of the
    pull) with the Celsius results that are not realistic stream temperatures set apart, and the
    counts. Values are left as they are, so EASI's own rule can still be applied to them."""
    import numpy as np
    import pandas as pd
    ok, f_ok = REASONS.index("ok"), REASONS.index("fahrenheit")
    reason = t["reason"].to_numpy(dtype=np.int8, copy=True)
    v = t["value"].to_numpy(dtype=np.float64)
    when = pd.to_datetime(pd.Series(t["date"].to_numpy()))
    month = when.dt.month.to_numpy(dtype=np.int16)
    year = when.dt.year.to_numpy(dtype=np.int16)
    lat, lon = t["lat"].to_numpy(dtype=np.float64), t["lon"].to_numpy(dtype=np.float64)
    station = t["station"].to_numpy()
    celsius = reason == ok
    inside = (v >= PLAUSIBLE_C[0]) & (v <= PLAUSIBLE_C[1])
    pool = ((reason == ok) | (reason == f_ok)) & inside

    # the area's month: median, spread (scaled MAD) and 5th percentile of station-month medians
    m, s, p05 = (np.full(len(v), np.nan) for _ in range(3))
    for size in CLIMATE_CELLS_DEG:
        cell = np.floor(lat / size).astype(np.int64) * 10_000 + np.floor(lon / size).astype(np.int64)
        sm = (pd.DataFrame({"cell": cell[pool], "station": station[pool], "year": year[pool], "month": month[pool],
                            "v": v[pool]})
              .groupby(["cell", "station", "year", "month"], sort=False)["v"].median().reset_index())
        g = sm.groupby(["cell", "month"])["v"]
        clim = pd.DataFrame({"m": g.median(), "n": g.size(), "p05": g.quantile(0.05),
                             "stations": sm.groupby(["cell", "month"])["station"].nunique()})
        dev = (sm["v"] - g.transform("median")).abs()
        clim["s"] = dev.groupby([sm["cell"], sm["month"]]).median() * 1.4826
        clim = clim[(clim["n"] >= CLIMATE_MIN_STATION_MONTHS) & (clim["stations"] >= CLIMATE_MIN_STATIONS)]
        need = np.nonzero(np.isnan(m))[0]
        got = clim.reindex(pd.MultiIndex.from_arrays([cell[need], month[need]]))
        m[need], s[need], p05[need] = (got[c].to_numpy(dtype=np.float64) for c in ("m", "s", "p05"))

    has = ~np.isnan(m)
    spread = np.maximum(np.nan_to_num(s, nan=SPREAD_FLOOR_C), SPREAD_FLOOR_C)
    candidate = celsius & inside & has
    with np.errstate(invalid="ignore"):
        z_c = (v - m) / spread
        z_f = ((v - 32.0) * 5.0 / 9.0 - m) / spread
        far_above_area = candidate & (z_c > FLIKE_Z_CELSIUS) & (v - m >= FLIKE_MARGIN_C)
        flike = far_above_area & (v >= 32.0) & (np.abs(z_f) <= FLIKE_Z_FAHRENHEIT)
        freezing = candidate & (v <= FREEZING_C) & (p05 > WARM_P05_C)
    # far above the area's month and the station's own summers (at least three summer readings)
    in_summer = celsius & inside & np.isin(month, SUMMER_MONTHS)
    own_summer = (pd.DataFrame({"station": station[in_summer], "v": v[in_summer]})
                  .groupby("station")["v"].agg(["median", "size"]))
    own_summer = own_summer.loc[own_summer["size"] >= HOT_SPRING_MIN_N, "median"]
    summer_of = np.full(len(v), np.nan)
    far = np.nonzero(far_above_area)[0]
    summer_of[far] = own_summer.reindex(station[far]).to_numpy(dtype=np.float64)
    with np.errstate(invalid="ignore"):
        above_summer = far_above_area & (v - summer_of > ABOVE_OWN_SUMMER_C)
    springs = 0
    if flike.any():                      # a hot spring reads that warm in that month and in summer too
        allc = pd.DataFrame({"station": station[celsius], "month": month[celsius], "v": v[celsius]})
        own = allc.groupby(["station", "month"])["v"].agg(["median", "size"])
        summer = allc[allc["month"].isin(SUMMER_MONTHS)].groupby("station")["v"].agg(["median", "size"])
        idx = np.nonzero(flike)[0]
        o = own.reindex(pd.MultiIndex.from_arrays([station[idx], month[idx]]))
        w = summer.reindex(station[idx])
        o_med, o_n = o["median"].to_numpy(dtype=np.float64), o["size"].to_numpy(dtype=np.float64)
        w_med, w_n = w["median"].to_numpy(dtype=np.float64), w["size"].to_numpy(dtype=np.float64)
        with np.errstate(invalid="ignore"):
            spring = ((o_n >= HOT_SPRING_MIN_N) & (np.abs(v[idx] - o_med) <= HOT_SPRING_NEAR_C)
                      & (w_n >= HOT_SPRING_MIN_N) & (np.abs(w_med - o_med) <= HOT_SPRING_SUMMER_C))
        flike[idx[spring]] = False
        springs = int(spring.sum())
    implausible = celsius & ~inside
    out = reason.copy()
    out[implausible] = REASONS.index("celsius_implausible")
    out[flike | freezing | above_summer] = REASONS.index("celsius_seasonal")
    stats = {"celsius_outside_bounds": int(implausible.sum()), "fahrenheit_like": int(flike.sum()),
             "freezing_in_a_warm_month": int(freezing.sum()),
             "far_above_the_area_and_own_summer": int((above_summer & ~flike).sum()),
             "hot_spring_readings_kept": springs,
             "celsius_without_an_area_month": int((celsius & inside & ~has).sum())}
    return out, stats


def _easi():
    path = str(REPO_ROOT / "apps" / "easi")
    if path not in sys.path:
        sys.path.insert(0, path)
    from easi.datasources import wqp as app_wqp
    return app_wqp


def normalize(table) -> "pa.Table":
    """The bundle's columns for one month, by EASI's temperature rule: station (the monitoring
    location, else ``org|name``), name, org, latitude, longitude, date, value in C to two decimals
    (NaN unless kept or converted from Fahrenheit) and reason, plus the result identifier."""
    import math

    import numpy as np
    import pandas as pd
    import pyarrow as pa
    app_wqp = _easi()
    df = table.to_pandas()

    def col(name):
        return df[name].fillna("").astype(str) if name in df.columns else pd.Series([""] * len(df))

    raw = col("Result_Measure").str.strip()
    status = col("Result_MeasureStatusIdentifier")
    censor = col("Result_ResultDetectionCondition").str.strip()
    unit = col("Result_MeasureUnit").str.strip().str.lower()
    reason = np.full(len(df), REASONS.index("ok"), dtype=np.int8)
    blank = raw == ""
    rejected = ~blank & ~status.map(app_wqp._valid_status)
    censored = ~blank & ~rejected & (censor != "")
    is_f = unit.isin(FAHRENHEIT)
    bad_unit = ~blank & ~rejected & ~censored & ~unit.isin(CELSIUS) & ~is_f
    candidate = ~blank & ~rejected & ~censored & ~bad_unit
    value = pd.to_numeric(raw.where(candidate, ""), errors="coerce").to_numpy(dtype=np.float64, copy=True)
    # Python's float() also reads what to_numeric refuses (``1_000``); ask it where to_numeric failed
    for i in np.nonzero(candidate.to_numpy() & np.isnan(value))[0]:
        try:
            value[i] = float(raw.iat[i])
        except ValueError:
            value[i] = np.nan
    nonnumeric = candidate.to_numpy() & ~np.isfinite(value)
    fahrenheit = candidate.to_numpy() & is_f.to_numpy() & ~nonnumeric
    value = np.where(fahrenheit, (value - 32.0) * 5.0 / 9.0, value)
    implausible = fahrenheit & ((value < PLAUSIBLE_C[0]) | (value > PLAUSIBLE_C[1]))
    reason[blank.to_numpy()] = REASONS.index("blank")
    reason[rejected.to_numpy()] = REASONS.index("rejected")
    reason[censored.to_numpy()] = REASONS.index("censored")
    reason[bad_unit.to_numpy()] = REASONS.index("unsupported_unit")
    reason[nonnumeric] = REASONS.index("nonnumeric")
    reason[fahrenheit] = REASONS.index("fahrenheit")
    reason[implausible] = REASONS.index("fahrenheit_implausible")
    usable = (reason == 0) | (reason == REASONS.index("fahrenheit"))
    value = np.where(usable, np.round(value, 2), np.nan)        # hundredths of a degree
    station = col("Location_Identifier").str.strip()
    org = col("Org_Identifier").str.strip()
    name = col("Location_Name").str.strip()
    fallback = (org + "|" + name).str.strip("|")
    station = station.where(station != "", fallback.where(fallback != "", "unknown-station"))
    dates = pd.to_datetime(col("Activity_StartDate").str.slice(0, 10), errors="coerce", format="%Y-%m-%d")
    lat = pd.to_numeric(col("Location_Latitude"), errors="coerce")
    lon = pd.to_numeric(col("Location_Longitude"), errors="coerce")
    keep = dates.notna() & lat.notna() & lon.notna()
    return pa.table({
        "station": pa.array(station[keep].tolist()), "station_name": pa.array(name[keep].tolist()),
        "org": pa.array(org[keep].tolist()), "lat": pa.array(lat[keep].to_numpy()), "lon": pa.array(lon[keep].to_numpy()),
        "date": pa.array(dates[keep].dt.date.tolist(), type=pa.date32()),
        "value": pa.array(value[keep.to_numpy()]), "reason": pa.array(reason[keep.to_numpy()]),
        "result_id": pa.array(col(ID_COLUMN)[keep].tolist()),
    })


def convert(log: Callable = print) -> dict:
    """Each downloaded month into ``reference/`` (every column) and ``normalized/`` (bundle columns);
    a month already converted is kept."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq
    ref_dir, norm_dir = FOLDER / "reference", FOLDER / "normalized"
    ref_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)
    stats = {"months": 0, "rows": 0, "kept": 0}
    for csv_path in sorted((FOLDER / "monthly").glob("wqx3_*.csv")):
        month = csv_path.stem.split("_")[1]
        ref, norm = ref_dir / f"{month}.parquet", norm_dir / f"{month}.parquet"
        if not (ref.exists() and norm.exists()):
            # some comment cells hold quoted line breaks (2016-05 on), which the block chunker splits
            t = pcsv.read_csv(csv_path, convert_options=pcsv.ConvertOptions(column_types={}, strings_can_be_null=True,
                                                                              auto_dict_encode=False),
                              parse_options=pcsv.ParseOptions(newlines_in_values=True),
                              read_options=pcsv.ReadOptions(block_size=1 << 26))
            t = t.cast(pa.schema([pa.field(f.name, pa.string()) for f in t.schema]))
            if ID_COLUMN in t.column_names:          # stitched pieces never overlap; a repeat would be dropped
                firsts = (t.select([ID_COLUMN]).append_column("_i", pa.array(range(t.num_rows), pa.int64()))
                          .group_by(ID_COLUMN).aggregate([("_i", "min")]).column("_i_min"))
                if len(firsts) < t.num_rows:
                    t = t.take(pc.sort_indices(firsts))
            pq.write_table(t, ref, compression="zstd", compression_level=9)
            pq.write_table(normalize(t), norm, compression="zstd")
        n = pq.read_metadata(norm).num_rows
        stats["months"] += 1
        stats["rows"] += n
        log(f"wqp temperature {month}: {n:,} results")
    return stats


def record(result_id: str) -> dict | None:
    """The full WQP record (every column of the pull) of one result, from the reference parts."""
    import pyarrow.compute as pc
    import pyarrow.dataset as pds
    d = pds.dataset(FOLDER / "reference", format="parquet")
    t = d.to_table(filter=pc.field(ID_COLUMN) == result_id)
    return t.slice(0, 1).to_pylist()[0] if t.num_rows else None
