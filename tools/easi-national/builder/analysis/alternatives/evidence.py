"""Deterministic probability sample and isolated copies of offline evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from builder.paths import DataRoot
from builder.analysis import nrsa
from builder.stages import joins, nas_national, score, streamcat_national
from .io import fingerprint as source_fingerprint, info, read_json, sha, write_json, write_parquet


def stratified_sample(frame, n=100000, seed=17):
    """Fixed-size proportional allocation with every nonempty stratum represented.

    Hamilton allocation after one per stratum. Hash ordering makes selection
    independent of the input order and parallel execution. Inclusion weights
    are population size / allocated size, including certainty strata.
    """
    frame = frame.copy().sort_values("comid").reset_index(drop=True)
    if frame.comid.duplicated().any():
        raise ValueError("The reach population must contain unique COMIDs")
    n = min(n, len(frame))
    labels = frame[["state", "l2"]].fillna("unknown").astype(str)
    groups = list(labels.groupby(["state", "l2"], sort=True).groups.items())
    if n < len(groups):
        raise ValueError("Sample too small to represent every stratum")
    sizes = np.array([len(index) for _, index in groups])
    quota = np.ones(len(groups), dtype=int)
    remaining = n - len(groups)
    capacity = sizes - 1
    expected = capacity * remaining / max(1, capacity.sum())
    quota += np.floor(expected).astype(int)
    order = np.argsort(-(expected - np.floor(expected)), kind="stable")
    quota[order[:n - quota.sum()]] += 1
    frame["sample"] = False
    frame["sample_weight"] = 0.0
    summary = []
    for ((state, l2), index), size in zip(groups, quota):
        ordered = sorted(index, key=lambda i: hashlib.sha256(f"{seed}:{int(frame.at[i, 'comid'])}".encode()).digest())
        chosen = ordered[:size]
        frame.loc[chosen, "sample"] = True
        frame.loc[chosen, "sample_weight"] = len(index) / int(size)
        summary.append({"state": state, "l2": l2, "population_n": len(index), "sample_n": int(size),
                        "weight": len(index) / int(size)})
    return frame, summary


class _Progress:
    def say(self, message):
        print(message, flush=True)


def prepare(root: Path, study: Path):
    from easi.national import records
    data = DataRoot(root)
    path = study / "snapshot/analysis/values.parquet"
    population = pq.read_table(path, columns=["comid", "state", "l2", "nars9", "huc8", "huc12"]).to_pandas()
    population, strata = stratified_sample(population)
    station_ids = {r["comid"] for r in nrsa._stations()}
    population["station"] = population.comid.isin(station_ids)
    population["woody_82"] = population.l2.astype(str).eq("8.2")
    selected = population[population["sample"] | population.station | population.woody_82].copy()
    sources, collected = [], set()
    for huc8, group in selected.groupby("huc8", sort=True):
        source = data.huc8_file(str(huc8), "evidence")
        target = study / "evidence/stored" / f"{huc8}.parquet"
        stamp = info(source)
        receipt_path = target.with_suffix(".json")
        ids = sorted(int(c) for c in group.comid)
        identity = {"source": stamp, "comids": ids}
        receipt = read_json(receipt_path) if receipt_path.exists() else {}
        if receipt.get("identity") != identity or not target.exists() or sha(target) != receipt.get("output_sha256"):
            table = pq.read_table(source, filters=[("comid", "in", ids)])
            if set(table.column("comid").to_pylist()) != set(ids):
                raise RuntimeError(f"Incomplete stored evidence: {huc8}")
            write_parquet(target, table)
            write_json(receipt_path, {"identity": identity, "output_sha256": sha(target)})
        collected.update(ids)
        sources.append(stamp)
    print(f"Copied stored evidence for {len(collected):,} unique reaches", flush=True)
    outside = sorted(station_ids - collected)
    identity = nrsa._identity_from_vaa(data, outside) if outside else {}
    regional = nrsa._strata_rows(data, outside) if outside else {}
    # Source stamps bind resumed synthetic records to the existing offline caches.
    cache_paths = [data.vaa, data.erom, data.nid, data.huc4_vpu, data.analysis / "strata.parquet",
                   streamcat_national.cache_path(data), nas_national.cache_path(data)]
    from builder.stages import wqp_local, local_gdb
    cache_paths += [wqp_local.national_path(data), local_gdb.attains_path(data)]
    stamps = [info(p) for p in cache_paths if p.exists()]
    from .study import stage_sources
    fingerprint, _ = source_fingerprint(stage_sources("evidence"), {"caches": stamps})
    needed = []
    missing = []
    for comid in outside:
        meta = identity.get(comid)
        region = regional.get(comid) or {}
        if meta is None or region.get("lat") is None or region.get("lon") is None:
            missing.append(comid)
            continue
        target = study / "evidence/stations" / f"{comid}.parquet"
        receipt = read_json(target.with_suffix(".json")) if target.with_suffix(".json").exists() else {}
        if receipt.get("input_digest") != fingerprint or not target.exists() or sha(target) != receipt.get("output_sha256"):
            needed.append(comid)
    if needed:
        streamcat = streamcat_national.cached_rows(data, needed)
        erom = score.erom_rows(data, needed)
        wqp = nrsa._WqpNational(data, _Progress())
        attains = nrsa._AttainsCells(data)
        nid_index = joins._nid_index(data)
        taxa = nas_national.cached_taxa(data) or {}
        # Geographic ordering lets the existing one-degree ATTAINS cell cache work.
        needed.sort(key=lambda c: (int(regional[c]["lat"]), int(regional[c]["lon"]), c))
        for i, comid in enumerate(needed):
            region = regional[comid]
            lat, lon = float(region["lat"]), float(region["lon"])
            (x, y), = joins._project([lat], [lon])
            record = nrsa.synthetic_record(comid, identity[comid], region, streamcat.get(comid) or {},
                erom=erom.get(comid), attains=attains.lookup(lat, lon),
                wqp_tn=wqp.summary("tn", lat, lon, x, y), wqp_tp=wqp.summary("tp", lat, lon, x, y),
                nid_dams=joins.nid_lookup(nid_index, lat, lon, x, y),
                nas_taxa=taxa.get(str(region.get("huc12")), {}).get("taxa") if region.get("huc12") else None)
            target = study / "evidence/stations" / f"{comid}.parquet"
            write_parquet(target, pa.Table.from_pylist([records.to_row(record)]))
            write_json(target.with_suffix(".json"), {"input_digest": fingerprint, "output_sha256": sha(target)})
            if i % 100 == 0:
                print(f"Station evidence: {i + 1:,}/{len(needed):,}", flush=True)
    station_meta = pq.read_table(study / "snapshot/analysis/strata.parquet",
                                columns=["comid", "state", "l2", "nars9", "huc8", "huc12"],
                                filters=[("comid", "in", outside)]).to_pandas()
    station_meta["sample"] = False
    station_meta["sample_weight"] = 0.0
    station_meta["station"] = True
    station_meta["woody_82"] = False  # outside the stored-reach population
    selected = pd.concat([selected, station_meta], ignore_index=True).sort_values("comid")
    selected["evidence_available"] = ~selected.comid.isin(missing)
    write_parquet(study / "cohorts/reaches.parquet", selected)
    result = {"population_n": len(population), "sample_n": int(population["sample"].sum()),
              "woody_82_n": int(population.woody_82.sum()), "station_comids_n": len(station_ids),
              "station_missing_evidence": missing, "selected_n": len(selected), "strata": strata,
              "sources": sources, "synthetic_sources": stamps,
              "design": "State by Level II stratified simple random sample without replacement; SHA256 seed 17",
              "weights": "N_h/n_h; sampled footprint estimates, not full national totals"}
    write_json(study / "cohorts/sampling.json", result)
    return {k: v for k, v in result.items() if k not in ("sources", "synthetic_sources", "strata")}
