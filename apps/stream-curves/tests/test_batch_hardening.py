"""Batch-runner hardening for new regions: a site screened once, the unresolved-
screen guard, the per-run StreamCat cache, and stage-many's summary."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import regional_agent as ra

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_region_batch.py"


def _batch_module():
    spec = importlib.util.spec_from_file_location("run_region_batch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── a site is screened once ──────────────────────────────────────────────────
def test_select_candidates_keeps_one_row_per_site_id(tmp_path):
    csv = tmp_path / "sites.csv"
    pd.DataFrame({
        "site_id": ["A", "A", "B", "C"], "site_name": ["a", "a", "b", "c"],
        "lat": [1.0, 1.0, 2.0, 3.0], "lon": [1.0, 1.0, 2.0, 3.0], "state": ["MN"] * 4,
        "us_l3code": ["49", "49", "49", "50"], "us_l3name": ["x"] * 4,
    }).to_csv(csv, index=False)
    sel = ra.select_candidates("49", sites_path=csv)
    assert sel["site_id"].tolist() == ["A", "B"]
    assert ra.select_candidates("50", sites_path=csv)["site_id"].tolist() == ["C"]


def test_bundled_nrsa_table_yields_unique_candidates():
    # the two duplicated Minnesota ids (L3 49 and 50) are collapsed
    for code in ("49", "50", "71"):
        assert ra.select_candidates(code)["site_id"].is_unique, code


def test_region_name_for_reads_the_site_table():
    assert ra.region_name_for("71") == "Interior Plateau"
    assert ra.region_name_for("55") == "Eastern Corn Belt Plains"
    assert ra.region_name_for("999") is None


# ── the unresolved-screen guard ──────────────────────────────────────────────
def test_unresolved_check_refuses_beyond_the_share_unless_allowed():
    mod = _batch_module()
    clean = {"n_screened": 25, "n_retained": 23, "n_excluded": 2, "n_unresolved": 0}
    assert mod.unresolved_check(clean, max_share=0.10, allow=False) == (None, None)
    outage = {"n_screened": 25, "n_retained": 10, "n_excluded": 2, "n_unresolved": 13}
    level, msg = mod.unresolved_check(outage, max_share=0.10, allow=False)
    assert level == "refuse" and "13 of 25" in msg and "52%" in msg
    level, msg = mod.unresolved_check(outage, max_share=0.10, allow=True)
    assert level == "warn" and msg
    # exactly at the limit passes; nothing screened (offline smoke) passes
    assert mod.unresolved_check({"n_screened": 20, "n_unresolved": 2}, max_share=0.10, allow=False) == (None, None)
    assert mod.unresolved_check({}, max_share=0.10, allow=False) == (None, None)
    assert mod.unresolved_share({"n_screened": 0}) is None


# ── the per-run StreamCat cache ──────────────────────────────────────────────
def test_enrich_streamcat_reads_its_own_cache_for_the_same_comids_and_codes(tmp_path):
    calls = []

    def fake_fetch(comids, codes, area="watershed"):
        calls.append((tuple(comids), tuple(codes)))
        return pd.DataFrame({"comid": list(comids), "bfiws": [40.0 + i for i in range(len(comids))],
                             "pctimp2019ws": [1.0] * len(comids)})

    directions = ra.load_landscape_directions()
    data = pd.DataFrame({"site_id": ["s1", "s2"], "comid": [101, 102]})
    cache = tmp_path / "streamcat_cache.json"

    out1, rep1 = ra.enrich_streamcat(data, directions, fetch=fake_fetch, cache_path=cache)
    assert rep1["status"] == "ok" and rep1["cache"] == {"path": str(cache), "from_cache": False}
    assert cache.is_file() and len(calls) == 1
    doc = json.loads(cache.read_text(encoding="utf-8"))
    assert doc["key"]["comids"] == ["101", "102"] and doc["rows"]

    out2, rep2 = ra.enrich_streamcat(data, directions, fetch=fake_fetch, cache_path=cache)
    assert len(calls) == 1, "the second call read the cache"
    assert rep2["status"] == "ok" and rep2["cache"]["from_cache"] is True
    assert rep2["n_columns"] == rep1["n_columns"]
    pd.testing.assert_frame_equal(out1.reset_index(drop=True), out2.reset_index(drop=True), check_dtype=False)

    other = pd.DataFrame({"site_id": ["s1", "s3"], "comid": [101, 103]})
    _, rep3 = ra.enrich_streamcat(other, directions, fetch=fake_fetch, cache_path=cache)
    assert len(calls) == 2 and rep3["cache"]["from_cache"] is False
    assert json.loads(cache.read_text(encoding="utf-8"))["key"]["comids"] == ["101", "103"]

    _, rep4 = ra.enrich_streamcat(data, directions, fetch=fake_fetch)
    assert "cache" not in rep4 and len(calls) == 3


# ── stage-many ───────────────────────────────────────────────────────────────
def test_write_batch_summary_has_one_row_per_region(tmp_path):
    mod = _batch_module()
    rows = [{"l3": "55", "name": "Eastern Corn Belt Plains", "exit": 0, "candidates": 18, "retained": 16,
             "tier": "best_available", "curves": 33, "decisions": 30, "open_items": 1, "hard_stops": 0,
             "staged_version": 1, "seconds": 61.2, "out": "x", "error": None},
            {"l3": "999", "name": None, "exit": 1, "out": "y", "error": "no NRSA candidate sites for L3 ecoregion 999"}]
    jp, mp = mod.write_batch_summary(rows, tmp_path)
    doc = json.loads(jp.read_text(encoding="utf-8"))
    assert [r["l3"] for r in doc["regions"]] == ["55", "999"]
    md = mp.read_text(encoding="utf-8")
    assert "2 region(s), 1 staged, 1 not staged" in md and "Nothing is promoted" in md
    assert md.count("\n| ") == 3  # header + two rows


@pytest.mark.skipif(not nd.multi_cycle_available(),
                    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)")
def test_stage_many_threads_the_dataset_choice_into_each_stage(tmp_path, monkeypatch):
    """stage-many builds cmd_stage's namespace by hand and once omitted the two
    dataset attributes, so a pooled sweep silently ran on the legacy data and
    recorded it as such. cmd_stage now reads the attributes directly (no getattr
    fallback), so the namespace must carry both."""
    mod = _batch_module()
    seen = []
    monkeypatch.setattr(mod, "cmd_stage", lambda ns: (seen.append(ns), 0)[1])
    rc = mod.main(["stage-many", "--l3", "55", "--out-root", str(tmp_path / "many"),
                   "--maintainer", "tester",
                   "--nrsa-dataset", nd.MULTI_CYCLE_DATASET_ID, "--nrsa-cycle", "2324"])
    assert rc == 0 and len(seen) == 1
    ns = seen[0]
    assert ns.nrsa_dataset == nd.MULTI_CYCLE_DATASET_ID
    assert ns.nrsa_cycles == ["2324"]


def test_stage_many_stages_each_region_and_records_failures(tmp_path):
    """Offline: the Eastern Corn Belt Plains stages, an unknown code fails, and
    both land in the summary. The pipeline's fixture cost (about a minute) is
    paid once here."""
    from conftest import documented_exclusions
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(json.dumps(documented_exclusions(
        reason="data-unavailable",
        justification="Offline test run without the StreamCat landscape join.")), encoding="utf-8")
    env = dict(os.environ)
    env.pop("STAF_LIBRARY_ROOT", None)
    out_root = tmp_path / "many"
    proc = subprocess.run(
        # Pinned to the legacy snapshot: the assertions below are calibrated on
        # the 18-candidate pilot; new builds default to the pooled archive.
        [sys.executable, str(SCRIPT), "stage-many", "--l3", "55", "--l3", "999",
         "--out-root", str(out_root), "--nrsa-dataset", "legacy-1819",
         "--no-screen", "--no-streamcat", "--n-boot", "20",
         "--maintainer", "tester", "--coverage-exceptions", str(exceptions),
         "--enable-policy", "curve07-thin-metric-finalized",
         "--enable-policy", "data03-thin-metric-finalized",
         "--enable-policy", "data06-insufficient-finalized"],
        capture_output=True, text=True, env=env, timeout=1200)
    assert proc.returncode == 1, proc.stdout[-3000:] + proc.stderr[-3000:]  # one region failed
    doc = json.loads((out_root / "batch_summary.json").read_text(encoding="utf-8"))
    rows = {r["l3"]: r for r in doc["regions"]}
    assert rows["55"]["exit"] == 0 and rows["55"]["staged_version"] == 1 and rows["55"]["curves"] > 0
    assert rows["999"]["exit"] == 1 and "no NRSA candidate sites" in rows["999"]["error"]
    assert (out_root / "l3-55-eastern-corn-belt-plains" / "review_packet.md").is_file()
    assert not (out_root / "l3-999-l3-999").exists()
    assert "[batch-many] summary" in proc.stdout
