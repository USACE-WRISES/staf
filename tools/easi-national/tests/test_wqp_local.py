"""The WQP stage from the national ten-year parquet: the box and date
selection, the WQX3 to legacy column mapping, the shared normalization,
the three output files, the digest, and the fallback to the portal."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from builder import state
from builder.paths import DataRoot
from builder.stages import wqp_local
from builder.units import Chunk

WQX3 = ["Result_MeasureIdentifier", "Location_Identifier", "Location_Name", "Org_Identifier",
        "Activity_StartDate", "Result_Characteristic", "Result_Measure", "Result_MeasureUnit",
        "Result_SampleFraction", "Result_MeasureStatusIdentifier", "Result_ResultDetectionCondition",
        "Location_Latitude", "Location_Longitude", "source_month"]


def _row(rid, station="USGS-1", lat="38.1", lon="-78.5", date="2020-06-01", name="Phosphorus",
         value="0.12", unit="mg/l", fraction="Total", status="Accepted", detection=None, org="USGS"):
    # detection is null by default, as in the real file: a null must read as
    # "no condition", never as the text "nan" (pandas 3 reads Arrow nulls as NaN)
    return {"Result_MeasureIdentifier": rid, "Location_Identifier": station, "Location_Name": f"Site {station}",
            "Org_Identifier": org, "Activity_StartDate": date, "Result_Characteristic": name,
            "Result_Measure": value, "Result_MeasureUnit": unit, "Result_SampleFraction": fraction,
            "Result_MeasureStatusIdentifier": status, "Result_ResultDetectionCondition": detection,
            "Location_Latitude": lat, "Location_Longitude": lon, "source_month": date[:7]}


def _parquet(path, rows):
    table = pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in WQX3]))
    pq.write_table(table, path)
    return path


def _chunk():
    return Chunk(id="huc8-TEST", kind="huc8", label="test", huc8s=["02080204"], n_comids=3,
                 bbox=[-79.0, 37.5, -78.0, 38.5])


def test_selection_mapping_normalization_and_outputs(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    rows = [
        _row("r1"),                                                          # TP, usable
        _row("r2", name="Total Nitrogen, mixed forms", value="1.5"),         # TN, usable
        _row("r3", value="", ),                                              # blank
        _row("r4", detection="Not Detected"),                                # censored
        _row("r5", fraction="Dissolved"),                                    # non-total fraction
        _row("r6", unit="ug/l", value="250"),                                # unit factor -> 0.25 mg/L
        _row("r7", lat="40.0"),                                              # outside the box
        _row("r8", date="2015-01-01"),                                       # before the window
        _row("r9", name="Chloride"),                                         # not a nutrient the app asks for
        _row("r1"),                                                          # duplicate result id
        _row("r10", station="", org="ORG", lat="", lon=""),                  # no coordinates: never inside a box
    ]
    source = _parquet(root.national / "wqp_results.parquet", rows)
    chunk = _chunk()
    states = state.UnitStates(root)
    progress = state.Progress(root, quiet=True)
    import datetime as dt
    wqp_local.run_wqp(root, chunk, states, progress, state.Control(root), source=source,
                      as_of=dt.date(2026, 9, 10))
    tp = {r["result_id"]: r for r in pq.read_table(root.chunk_raw(chunk.id, "wqp_tp")).to_pylist()}
    tn = pq.read_table(root.chunk_raw(chunk.id, "wqp_tn")).to_pylist()
    assert set(tp) == {"r1", "r3", "r4", "r5", "r6"} and len(tn) == 1 and tn[0]["result_id"] == "r2"
    assert tp["r1"]["reason"] == "ok" and tp["r1"]["value"] == 0.12 and tp["r1"]["date"] == "2020-06-01"
    assert tp["r1"]["station"] == "USGS-1" and tp["r1"]["station_name"] == "Site USGS-1" and tp["r1"]["lat"] == 38.1
    assert tp["r3"]["reason"] == "blank" and tp["r4"]["reason"] == "censored"
    assert tp["r5"]["reason"] == "non_total_fraction" and tp["r6"]["value"] == 0.25
    assert tn[0]["value"] == 1.5 and tn[0]["param"] == "tn"
    stations = pq.read_table(root.chunk_raw(chunk.id, "wqp_stations")).to_pylist()
    assert [s["station"] for s in stations] == ["USGS-1"] and stations[0]["lat"] == 38.1
    assert states.unit(chunk.id)["wqp"]["status"] == "done"
    # the digest follows the national file: a refreshed parquet re-runs the stage
    first = states.unit(chunk.id)["wqp"]["inputs"]
    _parquet(source, rows + [_row("r11")])
    import os, time
    os.utime(source, (time.time() + 5, time.time() + 5))
    wqp_local.run_wqp(root, chunk, states, progress, state.Control(root), source=source,
                      as_of=dt.date(2026, 9, 10))
    assert states.unit(chunk.id)["wqp"]["inputs"] != first
    assert "r11" in {r["result_id"] for r in pq.read_table(root.chunk_raw(chunk.id, "wqp_tp")).to_pylist()}


def test_falls_back_to_the_station_pull_without_the_national_file(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    called = {}
    from builder.stages import wqp_stations
    monkeypatch.setattr(wqp_stations, "run_wqp", lambda *a, **k: called.setdefault("args", (a, k)))
    wqp_local.run_wqp(root, _chunk(), state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root))
    assert "args" in called and called["args"][1] == {"force": False}
    assert not wqp_local.available(root)
    assert wqp_local.start_iso(__import__("datetime").date(2026, 9, 10)) == "2016-09-10"
