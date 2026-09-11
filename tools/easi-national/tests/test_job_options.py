"""Job options: the opt-in DEM window archive rides from the CLI and the queue
to the sampling stage only, and the national job takes a step filter."""
from __future__ import annotations

import numpy as np
import xarray as xr

from builder import pipeline, state, worker
from builder.paths import DataRoot
from builder.stages import national, xs_sample


def test_keep_window_writes_a_geotiff_and_only_the_sampling_stage_takes_the_flag(tmp_path):
    import rasterio
    import rioxarray  # noqa: F401 - the rio accessor
    z = np.arange(16, dtype="float32").reshape(4, 4)
    da = xr.DataArray(z, dims=("y", "x"), coords={"y": [3.5, 2.5, 1.5, 0.5], "x": [0.5, 1.5, 2.5, 3.5]})
    path = tmp_path / "dem_windows" / "1.tif"
    xs_sample._keep_window(da, path)                     # no CRS on the array: written as EPSG:5070
    with rasterio.open(path) as ds:
        assert ds.count == 1 and ds.width == 4 and ds.crs.to_epsg() == 5070
        assert float(ds.read(1)[0, 0]) == 0.0
    xs_sample._keep_window(None, tmp_path / "none.tif")
    assert not (tmp_path / "none.tif").exists()
    assert pipeline._stage_kwargs("xs_sample", True) == {"keep_dem_windows": True}
    assert pipeline._stage_kwargs("xs_sample", False) == {}
    assert pipeline._stage_kwargs("derive", True) == {}


def test_worker_cli_carries_the_flag_into_the_job(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(worker, "run_job", lambda root, job, states, progress, control: seen.update(job))
    rc = worker.main(["--root", str(tmp_path / "root"), "chunk", "--kind", "huc8", "--value", "02080204",
                      "--stages", "xs_sample", "xs_derive", "--keep-dem-windows"])
    assert rc == 0
    assert seen["keep_dem_windows"] is True and seen["stages"] == ["xs_sample", "xs_derive"]
    seen.clear()
    assert worker.main(["--root", str(tmp_path / "root"), "chunk", "--kind", "state", "--value", "VA"]) == 0
    assert seen["keep_dem_windows"] is False


def test_run_national_honours_the_step_filter(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "root").ensure()
    calls: list[str] = []
    for name in ("fetch_vaa", "fetch_enhd", "build_slim", "build_index", "fetch_huc4_polygons"):
        monkeypatch.setattr(national, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr(national, "fetch_nid", lambda *a, **k: calls.append("fetch_nid"))
    monkeypatch.setattr(national, "_streamcat_cache", lambda *a, **k: calls.append("streamcat"))
    monkeypatch.setattr(national, "_nas_cache", lambda *a, **k: calls.append("nas"))
    monkeypatch.setattr(national, "_dem_catalog", lambda *a, **k: calls.append("dem1m"))
    monkeypatch.setattr(national, "_dem_catalog19", lambda *a, **k: calls.append("dem19"))
    monkeypatch.setattr(national, "_wqp_monthly", lambda *a, **k: calls.append("wqp_monthly"))
    monkeypatch.setattr(national, "_gdb_steps", lambda *a, **k: ())
    monkeypatch.setattr(national, "_inputs_for", lambda stage, root_: "x")
    states = state.UnitStates(root)
    progress = state.Progress(root, quiet=True)
    national.run_national(root, states, progress, state.Control(root), steps=["nid", "dem19_index"])
    assert calls == ["fetch_nid", "dem19"]
    assert states.unit("national")["nid"]["status"] == "done"
    assert "vaa" not in states.unit("national")
    calls.clear()
    national.run_national(root, states, progress, state.Control(root))
    assert calls[:4] == ["fetch_vaa", "fetch_enhd", "build_slim", "build_index"] and "fetch_nid" not in calls
