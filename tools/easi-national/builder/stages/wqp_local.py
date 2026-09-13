"""The chunk's WQP nutrient results from the national ten-year parquet
(``national/wqp/wqp_results.parquet``, the ``wqp_monthly`` step) instead of
the portal: the rows inside the chunk's buffered box and the app's date
window, renamed from the WQX3 columns to the legacy result columns and
normalized by the very same ``wqp.normalize_rows`` the portal path uses, so
the joins and the nutrient metrics see identical station-result files. Falls
back to the station pull when the national file is absent."""
from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Optional

from .. import config
from ..paths import DataRoot
from ..state import Control, Progress, UnitStates
from ..units import Chunk
from . import common
from . import wqp as wqp_stage

STAGE = wqp_stage.STAGE
PARAMS = wqp_stage.PARAMS

#: legacy portal column -> WQX3 column of the national parquet
COLUMN_MAP = (
    ("ResultIdentifier", "Result_MeasureIdentifier"),
    ("MonitoringLocationIdentifier", "Location_Identifier"),
    ("MonitoringLocationName", "Location_Name"),
    ("OrganizationIdentifier", "Org_Identifier"),
    ("ActivityStartDate", "Activity_StartDate"),
    ("CharacteristicName", "Result_Characteristic"),
    ("ResultMeasureValue", "Result_Measure"),
    ("ResultMeasure/MeasureUnitCode", "Result_MeasureUnit"),
    ("ResultSampleFractionText", "Result_SampleFraction"),
    ("ResultStatusIdentifier", "Result_MeasureStatusIdentifier"),
    ("ResultDetectionConditionText", "Result_ResultDetectionCondition"),
    ("LatitudeMeasure", "Location_Latitude"),
    ("LongitudeMeasure", "Location_Longitude"),
)


def national_path(root: DataRoot) -> Path:
    from . import wqp_national
    return wqp_national.combined_path(root)


def available(root: DataRoot) -> bool:
    return national_path(root).exists()


def _stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def start_iso(as_of: date, years: int = config.WQP_YEARS) -> str:
    """The app's window start as ISO (the parquet's dates are ISO text)."""
    try:
        start = as_of.replace(year=as_of.year - years)
    except ValueError:
        start = as_of.replace(year=as_of.year - years, day=28)
    return start.isoformat()


def select_rows(path: Path, bbox: list[float], start: str) -> list[dict]:
    """The parquet rows (legacy column names) inside ``bbox`` with a start
    date on or after ``start`` and a characteristic the app queries."""
    import pyarrow.parquet as pq
    wanted = [src for _legacy, src in COLUMN_MAP]
    schema = pq.read_schema(path).names
    present = [c for c in wanted if c in schema]
    table = pq.read_table(path, columns=present)
    frame = table.to_pandas()
    for column in wanted:
        if column not in frame.columns:
            frame[column] = None
    import pandas as pd
    lat = pd.to_numeric(frame["Location_Latitude"], errors="coerce")
    lon = pd.to_numeric(frame["Location_Longitude"], errors="coerce")
    west, south, east, north = bbox
    inside = (lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)
    dates = frame["Activity_StartDate"].fillna("").astype(str)
    recent = dates >= start
    names = frame["Result_Characteristic"].fillna("").astype(str)
    known = names.map(lambda n: wqp_stage.param_of(n) is not None)
    # pandas 3 reads Arrow string nulls as NaN: blank them, or a missing
    # detection condition would read as the text "nan" and censor the row
    picked = frame[inside & recent & known].fillna("")
    rows = []
    for record in picked.itertuples(index=False):
        values = dict(zip(picked.columns, record))
        rows.append({legacy: str(values.get(src) if values.get(src) is not None else "")
                     for legacy, src in COLUMN_MAP})
    return rows


def normalize(rows: list[dict]) -> list[dict]:
    """The portal path's normalization over legacy-named rows: the rows go
    through the same CSV reader and rules, so exclusions and unit factors
    cannot drift between the two sources."""
    if not rows:
        return []
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[legacy for legacy, _src in COLUMN_MAP], lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return wqp_stage.normalize_rows(None, buffer.getvalue())


def run_wqp(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
            control: Control, *, force: bool = False, as_of: date = config.NRSA_AS_OF,
            source: Optional[Path] = None) -> None:
    """The ``wqp`` stage from the national parquet; the station pull when it
    is not on disk."""
    path = source or national_path(root)
    if not path.exists():
        from . import wqp_stations
        progress.say("wqp: no national parquet, pulling from the portal by station")
        return wqp_stations.run_wqp(root, chunk, states, progress, control, force=force)
    if not chunk.bbox:
        raise RuntimeError("the geometry stage must run first (no chunk bbox)")
    start = start_iso(as_of)
    inputs = common.chunk_inputs(STAGE, chunk, [round(v, 3) for v in chunk.bbox], start, PARAMS,
                                 "national", _stamp(path), 1)

    def work():
        import pyarrow as pa
        progress.begin(chunk.id, STAGE, total=len(PARAMS) + 1,
                       message=f"WQP: reading the national parquet for the chunk box since {start}")
        control.check()
        picked = select_rows(path, chunk.bbox, start)
        progress.tick(done=1, message=f"WQP: {len(picked):,} national results in the box; normalizing")
        normalized = normalize(picked)
        schema = pa.schema([
            ("param", pa.string()), ("result_id", pa.string()), ("station", pa.string()),
            ("station_name", pa.string()), ("org", pa.string()), ("value", pa.float64()),
            ("reason", pa.string()), ("date", pa.string()), ("lat", pa.float64()), ("lon", pa.float64())])
        for i, param in enumerate(PARAMS):
            seen: set = set()
            unique: list[dict] = []
            for r in normalized:
                if r["param"] != param:
                    continue
                ident = r.get("result_id") or (r["station"], r.get("date"), r.get("value"), r["reason"])
                if ident in seen:
                    continue
                seen.add(ident)
                unique.append(r)
            common.write_parquet(pa.Table.from_pylist(unique, schema=schema), root.chunk_raw(chunk.id, f"wqp_{param}"))
            kept = sum(1 for r in unique if r["reason"] == "ok")
            progress.tick(done=i + 2, message=f"wqp_{param}.parquet: {len(unique):,} results, {kept:,} usable")
            progress.say(f"wqp_{param}.parquet: {len(unique):,} results from the national parquet, {kept:,} usable")
        stations: dict[str, dict] = {}
        for r in normalized:
            s = stations.setdefault(r["station"], {"station": r["station"], "station_name": r.get("station_name") or "",
                                                   "org": r.get("org") or "", "lat": r.get("lat"), "lon": r.get("lon")})
            if s["lat"] is None and r.get("lat") is not None:
                s["lat"], s["lon"] = r["lat"], r["lon"]
        common.write_parquet(pa.Table.from_pylist(list(stations.values()), schema=pa.schema([
            ("station", pa.string()), ("station_name", pa.string()), ("org", pa.string()),
            ("lat", pa.float64()), ("lon", pa.float64())])), root.chunk_raw(chunk.id, "wqp_stations"))
        progress.say(f"wqp_stations.parquet: {len(stations):,} stations with results in the box")

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
