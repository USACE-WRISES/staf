"""Opt-in (EASI_NATIONAL_NET_TESTS=1): on ten real Rivanna reaches the archive
path (offline reach line, best available 3DEP, sampling, derivation) equals
the live app's ``reach_geomorphology`` fed by the same elevation source."""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("EASI_NATIONAL_NET_TESTS") != "1",
                                reason="network test: set EASI_NATIONAL_NET_TESTS=1")
HUC8 = "02080204"
N = 10


def test_archive_path_equals_the_live_function_on_rivanna():
    import pyarrow.parquet as pq
    from builder import config, dem, reaches, state          # puts apps/easi on the path
    from easi.datasources import threedep
    from builder.network import NetworkIndex
    from builder.paths import DataRoot
    from builder.stages import xs_derive, xs_sample
    from builder.units import Chunk
    root = DataRoot(config.DEFAULT_ROOT)
    chunk = Chunk.load(root, "huc8-02080204")
    if chunk is None or not root.huc8_file(HUC8, "derived").exists() or not root.dem1m_catalog.exists():
        pytest.skip("the Rivanna chunk, its derive stage and the 3DEP catalogs are needed")
    derived = pq.read_table(root.huc8_file(HUC8, "derived"),
                            columns=["comid", "lat", "lon", "totdasqkm", "bankfull"]).to_pylist()
    derived.sort(key=lambda d: int(d["comid"]))
    sample = derived[:: max(1, len(derived) // N)][:N]
    network = NetworkIndex.load(root)
    geoms = dict(xs_sample.chunk_geometries(root, chunk))
    catalog, catalog19 = dem.Catalog.load(root), dem.Catalog19.load(root)
    budget = state.Bandwidth(root, budget_bytes=1e15)
    source = lambda buf: dem.best_available_dem(buf, catalog=catalog, catalog19=catalog19,  # noqa: E731
                                                accounting=lambda n: budget.add(n, reaches=0))
    compared = 0
    dem.install(root)
    try:
        for d in sample:
            comid = int(d["comid"])
            fc, _ft, _warnings, _chain = reaches.reach_line(comid, float(d["lat"]), float(d["lon"]), geoms, network)
            if fc is None:
                continue
            block = json.loads(d["bankfull"]) if isinstance(d["bankfull"], str) else (d["bankfull"] or {})
            da = float(d["totdasqkm"] or 0.0)
            wide = xs_sample.buffer_half_width(block.get("width_m"), da)
            rows, meta = xs_sample.sample_reach(fc, wide, source)
            offline = xs_derive.derive_reach(rows, meta["line_length_m"], da, block, meta["res"])
            width, depth = block.get("width_m"), block.get("depth_m")
            live = threedep.reach_geomorphology(fc, da, bankfull=(width, depth) if width and depth else None,
                                                bankfull_area_m2=block.get("area_m2"),
                                                division=block.get("division_name"))
            assert live and offline, comid
            assert offline["n_transects"] == live["n_transects"], comid
            assert offline["dem_resolution_m"] == live["dem_resolution_m"], comid
            assert offline["selected"] == live["selected"], comid
            for key in ("entrenchment_ratio", "bank_height_ratio"):
                assert offline[key] == pytest.approx(live[key], abs=0.011), (comid, key)
                assert offline["reach"][key]["median"] == pytest.approx(live["reach"][key]["median"], abs=0.011)
            compared += 1
    finally:
        dem.uninstall()
    assert compared >= N - 2, compared
