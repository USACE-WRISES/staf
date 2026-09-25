"""The DEEP development evidence package: the data behind every curve of one region's build.

The analogue of EASI's ``tools/easi-national/builder/evidence_export.py``, written by a
region's stage (``run_region_batch.py stage``, after ``write_outputs``) and, for a scratch
interactive publish, from a session's fields. A package is ``evidence.json`` plus ``data/``
(AUTHORING.md, "Evidence"; ``evidence_store`` verifies, installs and fetches it):

- ``stations.csv``: the region's candidate panel and the stations the frame kept out, each
  with its screen outcome and fail reasons, the owner's exclusions and readmissions;
- ``values.csv``: station, metric, value and source cycle for the region's own stations and
  the metrics the build mapped (the DATA-11 ledger);
- ``pool_ledger.csv``: ``result["reference_pool_ledger"]``, every station judged for every
  pool option of every metric, with why it was in or out;
- ``pools.json``: per metric the reference-support record, the station ids of the pool the
  metric was admitted under, the options tried, the envelope, covariates and donor regions;
- ``curves.json``: every curve the version scores, points per stratum, direction,
  transformation, the metric's resampling seed and its review status;
- ``ladder.json``: the sources after the station pools, tried and refused;
- ``candidates.json``: the candidate register (``candidates.register_for_result``);
- ``ledger.json``: the per-metric rebuild ledger (``provenance.build_ledger``);
- ``decisions.json``: the owner's curve decisions, the reviewer answers, the standing
  decisions applied, the approvals, the documented gaps and the policy entries enabled.

Identity is content: ``dataDigest`` over the data files, ``packageDigest`` over the manifest
without its ``producer`` block; the archive's bytes are a function of the package alone
(sorted entries, a fixed time, deflate at one level: ``project_file._zip_bytes``), and
nothing in the package names a clock or an absolute path. A package built from a full stage
result is ``refittable``; one built from a session's fields alone has no pool ledger and is
``reviewable``, and says what is missing and how to get it. Nothing here enters the DEEP
bundle or the app payload: a package is referenced by digest and hosted on the rolling
``deep-evidence`` prerelease (``evidence_store.public_base("deep")``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from . import evidence_store as evs
from . import provenance as pv
from .paths import ROOT
from .project_file import _zip_bytes
from .version import APP_VERSION

SCHEMA = evs.SCHEMA
SCHEMA_VERSION = evs.SCHEMA_VERSION
KIND = "deep"
PACKAGE_PREFIX = "deep-dev-l3-"
REFITTABLE, REVIEWABLE = "refittable", "reviewable"
TOOL = "apps/stream-curves/streamcurves/deep_evidence.py"
ENGINE_PATH = "apps/stream-curves/streamcurves/curves.py"
NRSA_MANIFEST = "apps/stream-curves/data/nrsa/manifest.json"

STATION_COLUMNS = ("station_key", "lat", "lon", "comid", "in_frame", "frame_reason",
                   "screen_outcome", "screen_reason", "owner_exclusion", "readmitted_reason")
VALUE_COLUMNS = ("station_key", "metric", "value", "source_cycle")
LEDGER_COLUMNS = ("metric", "station_key", "level", "in_pool", "reason", "value", "source_cycle",
                  "l3", "l2", "l1", "option", "screen")

DICTIONARY = {
    "data/stations.csv": {
        "definition": "one row per station of the region's candidate panel (DATA-10 frame) and "
                      "per station the frame kept out",
        "columns": {"station_key": "the NRSA station key", "lat": "latitude", "lon": "longitude",
                    "comid": "the archive's NHDPlus V2 reach", "in_frame": "true for a panel station",
                    "frame_reason": "why a station is outside the frame",
                    "screen_outcome": "retained, excluded or not_evaluable under the fixed pressure screen",
                    "screen_reason": "the screen's own words for the outcome",
                    "owner_exclusion": "the owner's reason for dropping a retained station",
                    "readmitted_reason": "the owner's reason for keeping a station the frame left out"}},
    "data/values.csv": {
        "definition": "one row per station and metric with a value, for the region's own "
                      "stations and the metrics the build mapped (the DATA-11 value ledger)",
        "columns": {"station_key": "the NRSA station key", "metric": "the metric key",
                    "value": "the value the build read", "source_cycle": "the NRSA cycle it came from"}},
    "data/pool_ledger.csv": {
        "definition": "every station judged for every pool option of every metric "
                      "(reference_pool.build_pools): in the pool or not, and why",
        "columns": {c: c for c in LEDGER_COLUMNS}},
    "data/pools.json": {"definition": "per metric: the reference-support record, the station ids "
                                      "of the admitted pool, the options tried, envelope, "
                                      "covariates and donor regions"},
    "data/curves.json": {"definition": "every curve the version scores: points per stratum, "
                                       "direction, transformation, seed and review status"},
    "data/ladder.json": {"definition": "the sources after the station pools, tried and refused "
                                       "(REF-12 to REF-14), and their populations"},
    "data/candidates.json": {"definition": "the candidate register export (candidateRegister)"},
    "data/ledger.json": {"definition": "the per-metric rebuild ledger (rebuild-ledger/1)"},
    "data/decisions.json": {"definition": "the owner's curve decisions, reviewer answers, standing "
                                          "decisions, approvals, documented gaps and policy entries"},
}


class EvidenceExportError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def package_id(code: Any) -> str:
    """``deep-dev-l3-<code>``, a store folder name (``evidence_store.usable_package_id``)."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(code or "").strip().lower()).strip("-.")
    pid = PACKAGE_PREFIX + (slug or "unknown")
    if not evs.usable_package_id(pid):
        raise EvidenceExportError(f"no usable package id for region code {code!r}")
    return pid


def package_version(*, assessment_id: Optional[str] = None, version: Optional[int] = None,
                    inputs_digest: Optional[str] = None) -> str:
    """``<assessmentId>-v<N>`` for a published version, else the inputs digest's first twelve
    hex digits for a staged build, else ``unversioned``."""
    if assessment_id and version:
        text = f"{assessment_id}-v{int(version)}"
    elif inputs_digest:
        text = str(inputs_digest).split(":", 1)[-1][:12]
    else:
        text = "unversioned"
    if not evs.VERSION_RE.fullmatch(text):
        raise EvidenceExportError(f"no usable package version from {text!r}")
    return text


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: Any) -> bytes:
    return (json.dumps(pv.jsonable(obj), indent=1, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return "" if v != v else format(v, ".15g")
    if isinstance(v, bool):
        return "true" if v else "false"
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def _csv_bytes(rows: Iterable[Mapping], columns: Iterable[str]) -> bytes:
    columns = list(columns)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: _cell(r.get(c)) for c in columns})
    return buf.getvalue().encode("utf-8")


def _records(table: Any) -> list[dict]:
    if table is None:
        return []
    if isinstance(table, pd.DataFrame):
        return table.to_dict("records") if len(table) else []
    return [dict(r) for r in table if isinstance(r, Mapping)]


# --------------------------------------------------------------------------- #
# the data files
# --------------------------------------------------------------------------- #
def stations_rows(*, sites: Any, panel_ledger: Any = None, exclusions: Any = None,
                  overrides: Any = None) -> list[dict]:
    """The station rows: the screened panel (``easi_screening_sites`` rows), then the
    stations the frame kept out (``nrsa_panel_ledger``), with the owner's exclusions and
    readmissions joined by station key. Sorted by key, one row per station."""
    excluded = {str(e.get("site_id") or e.get("station_key")): str(e.get("reason") or "")
                for e in _records(exclusions)}
    readmitted = {str(o.get("station_key")): str(o.get("reason") or "")
                  for o in _records(overrides)}
    out: dict[str, dict] = {}
    for r in _records(sites):
        key = str(r.get("site_id") or r.get("station_key") or "")
        if not key:
            continue
        out[key] = {"station_key": key, "lat": r.get("lat"), "lon": r.get("lon"),
                    "comid": r.get("comid"), "in_frame": True, "frame_reason": "",
                    "screen_outcome": r.get("final_decision") or r.get("auto_decision") or "",
                    "screen_reason": r.get("reason") or r.get("issue") or "",
                    "owner_exclusion": excluded.get(key, ""),
                    "readmitted_reason": readmitted.get(key, "")}
    for r in _records(panel_ledger):
        key = str(r.get("station_key") or r.get("site_id") or "")
        if not key or key in out:
            continue
        out[key] = {"station_key": key, "lat": r.get("lat"), "lon": r.get("lon"),
                    "comid": r.get("comid"), "in_frame": False,
                    "frame_reason": r.get("reason") or "", "screen_outcome": "",
                    "screen_reason": "", "owner_exclusion": excluded.get(key, ""),
                    "readmitted_reason": readmitted.get(key, "")}
    return [out[k] for k in sorted(out)]


def values_rows(*, ledger: Any = None, data: Any = None, station_keys: Iterable[str] = (),
                metrics: Iterable[str] = ()) -> list[dict]:
    """The value rows: from the pool ledger (every option row of a station and metric holds
    the same value, so the pair is written once) restricted to ``station_keys``, else from
    the pooled data frame's metric columns. Sorted by station and metric."""
    keys = {str(k) for k in station_keys or ()}
    wanted = {str(m) for m in metrics or ()}
    out: dict[tuple, dict] = {}
    df = pv._ledger_frame(ledger)
    if df is not None and {"metric", "station_key", "value"} <= set(df.columns):
        sub = df[["metric", "station_key", "value"] + (["source_cycle"] if "source_cycle" in df.columns else [])]
        sub = sub[sub["value"].notna()]
        if keys:
            sub = sub[sub["station_key"].astype(str).isin(keys)]
        if wanted:
            sub = sub[sub["metric"].astype(str).isin(wanted)]
        for r in sub.to_dict("records"):
            k = (str(r["station_key"]), str(r["metric"]))
            if k not in out:
                out[k] = {"station_key": k[0], "metric": k[1], "value": float(r["value"]),
                          "source_cycle": r.get("source_cycle")}
    elif isinstance(data, pd.DataFrame) and len(data):
        key_col = "site_id" if "site_id" in data.columns else "station_key"
        if key_col in data.columns:
            cols = [c for c in data.columns if (not wanted or str(c) in wanted)
                    and c not in (key_col, "source_cycle")]
            cycles = data["source_cycle"] if "source_cycle" in data.columns else None
            for i, (key, row) in enumerate(zip(data[key_col].astype(str), data[cols].itertuples(index=False))):
                if keys and key not in keys:
                    continue
                for c, v in zip(cols, row):
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        continue
                    if f != f:
                        continue
                    out[(key, str(c))] = {"station_key": key, "metric": str(c), "value": f,
                                          "source_cycle": (cycles.iloc[i] if cycles is not None else None)}
    return [out[k] for k in sorted(out)]


def pool_ledger_bytes(ledger: Any) -> Optional[bytes]:
    df = pv._ledger_frame(ledger)
    if df is None:
        return None
    cols = [c for c in LEDGER_COLUMNS if c in df.columns] + [c for c in df.columns if c not in LEDGER_COLUMNS]
    return df[cols].to_csv(index=False, lineterminator="\n").encode("utf-8")


def pools_doc(support: Mapping, ledger: Any, code: Optional[str]) -> dict:
    """Per metric: the record, the admitted pool's station ids, options tried, envelope,
    covariates and donor regions (``provenance._pool_block`` reads the ledger)."""
    df = pv._ledger_frame(ledger)
    out = {}
    for mk in sorted(support):
        sup = dict(support[mk] or {})
        option = pv._pool_option(sup)
        block = pv._pool_block(str(mk), sup, option, df, code)
        out[str(mk)] = {"record": sup, "option": option, "stationIds": block["stationIds"],
                        "nStations": block["nStations"], "donorRegions": block["donorRegions"],
                        "cyclesUsed": block["cyclesUsed"], "optionsTried": sup.get("options_tried") or [],
                        "levelsTried": sup.get("levels_tried") or [],
                        "envelope": sup.get("envelope") or {}, "covariates": sup.get("covariates") or [],
                        "evidence": block["evidence"]}
    return out


def curves_doc(fields: Mapping, *, run_seed: Any = None, method_version: Optional[str] = None) -> dict:
    """Every curve the session scores, from the tiles the page would draw: points per
    stratum, direction and transformation, the metric's resampling seed, the review status
    and where the curve comes from."""
    from . import curve_tiles as ct
    from . import run_state
    fields = ct.fields_from_result(fields)
    mc = fields.get("metric_config") or {}
    review = fields.get("curve_review") or {}
    out = {}
    for t in ct.tiles_for_fields(fields):
        mk = str(t.get("metric") or "")
        cfg = mc.get(mk) or {}
        entry = review.get(mk) or {}
        fitted = not t.get("read_only")
        out[mk] = {
            "kind": "fitted" if fitted else str(t.get("source_kind") or "reference"),
            "displayName": t.get("display_name"), "units": t.get("units"),
            "strata": [{"label": s.get("label"), "points": [[float(x), float(y)] for x, y in s.get("points") or []],
                        "nReference": s.get("n_reference"), "curveStatus": s.get("curve_status"),
                        "curveSource": s.get("curve_source")} for s in t.get("strata") or []],
            "higherIsBetter": cfg.get("higher_is_better", t.get("higher_is_better")),
            "curveForm": cfg.get("curve_form") or cfg.get("expected_shape"),
            "transformation": cfg.get("transformation"),
            "domain": list(t.get("domain") or (None, None)),
            "referenceRange": list(t.get("reference_range") or (None, None)),
            "seed": pv._metric_seed(run_seed, mk) if fitted else None,
            "curveMethodVersion": (method_version or run_state.CURVE_METHOD_VERSION) if fitted else None,
            "status": {"reviewStatus": entry.get("status"), "decision": entry.get("decision"),
                       "inScope": t.get("in_scope"), "needsReview": bool(t.get("needs_review"))},
            "functions": [f for f in [t.get("function_id")] + [x.get("id") for x in t.get("also_function_refs") or []] if f],
            "basisDigest": _tile_digest(t, cfg),
        }
    return out


def _tile_digest(tile: Mapping, cfg: Mapping) -> Optional[str]:
    from . import candidates as C
    return C.tile_basis_digest(tile, cfg)


def ladder_doc(attempts: Any, populations: Any = None) -> dict:
    pops = {}
    for mk, pop in (populations or {}).items():
        if not isinstance(pop, Mapping):
            continue
        vals = pop.get("values")
        pops[str(mk)] = {"nTrain": pop.get("n_train"), "anchors": pop.get("anchors"),
                         "interval": pop.get("interval"),
                         "nValues": int(len(vals)) if vals is not None and hasattr(vals, "__len__") else None,
                         "stationIds": sorted(str(x) for x in pop.get("station_ids") or [])}
    return {"attempts": list(attempts or []), "populations": pops}


def decisions_doc(*, curve_decisions: Any = (), records: Any = (), review_queue: Any = None,
                  standing: Any = None, finalizations: Any = None, removals: Any = None,
                  approvals: Any = (), gaps: Any = (), enabled: Any = (), curve_review: Any = None,
                  register: Any = None) -> dict:
    """Every human and policy input a build applied, as its record shows them."""
    from . import owner_curves as oc
    answers = []
    for r in records or []:
        if r.get("reviewer_action"):
            answers.append({k: r.get(k) for k in ("rule_id", "subject", "reviewer", "reviewer_action",
                                                   "reviewer_rationale", "reviewed_at",
                                                   "reviewer_decision_class", "reviewer_rationale_origin")})
    resolved = [{k: i.get(k) for k in ("item_id", "status", "reviewer", "reviewer_action",
                                        "reviewer_rationale", "reviewed_at")}
                for i in ((review_queue or {}).get("items") or []) if i.get("status") != "open"]
    review_decisions = {mk: {k: e.get(k) for k in ("status", "decision", "decision_note", "decided_by",
                                                     "decided_at") if e.get(k) is not None}
                        for mk, e in (curve_review or {}).items() if isinstance(e, Mapping) and e.get("decision")}
    reg = register or {}
    return {"ownerDecisions": [oc.summary(d) for d in curve_decisions or [] if isinstance(d, Mapping)],
            "answers": answers, "reviewQueueResolved": resolved,
            "standingDecisions": standing, "finalizations": dict(finalizations or {}),
            "removals": dict(removals or {}), "approvals": list(approvals or []),
            "gaps": list(gaps or []), "enabledPolicyIds": list(enabled or []),
            "reviewDecisions": review_decisions,
            "registerDecisions": list(reg.get("decisions") or []) if isinstance(reg, Mapping) else [],
            "consideredCandidates": [c.get("candidateKey") for c in (reg.get("considered") or [])
                                     if isinstance(c, Mapping)] if isinstance(reg, Mapping) else []}


# --------------------------------------------------------------------------- #
# writing a package
# --------------------------------------------------------------------------- #
def _engine_block() -> dict:
    raw = (ROOT / "streamcurves" / "curves.py").read_bytes()
    return {"path": ENGINE_PATH, "sha256": _sha(raw), "sha256_lf": _sha(raw.replace(b"\r\n", b"\n"))}


def _nrsa_source(manifest_digest: Optional[str] = None) -> dict:
    p = ROOT / "data" / "nrsa" / "manifest.json"
    digest = manifest_digest
    dataset = None
    if p.is_file():
        raw = p.read_bytes()
        if not digest:
            digest = "sha256:" + _sha(raw)
        try:
            dataset = json.loads(raw.decode("utf-8")).get("datasetId")
        except ValueError:
            dataset = None
    return {"id": "nrsa-archive", "path": NRSA_MANIFEST, "sha256": digest, "datasetId": dataset,
            "citation": "USEPA National Rivers and Streams Assessment, the multi-cycle archive "
                        "StreamCurves ships (data/nrsa)"}


def _producer() -> dict:
    from . import code_identity
    return {"tool": TOOL, "appVersion": APP_VERSION, "gitCommit": code_identity.git_head(),
            "gitDirty": code_identity.git_dirty()}


def _write(out_dir, *, pid: str, version: str, data: dict[str, bytes], manifest: dict) -> dict:
    """Write the folder and the archive, return the reference (``evidence_store.reference``)."""
    out = Path(out_dir)
    folder = out / pid
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    files: dict[str, dict] = {}
    entries: list[tuple[str, bytes]] = []
    for name, body in sorted(data.items()):
        rel = f"data/{name}"
        evs._safe_rel(rel)
        (folder / rel).write_bytes(body)
        files[rel] = {"bytes": len(body), "sha256": _sha(body), "mediaType": _media_type(name)}
        entries.append((rel, body))
    doc = {"schema": SCHEMA, "schemaVersion": SCHEMA_VERSION, "packageId": pid, "version": version,
           "dataDigest": evs.data_digest(files), "files": files, **manifest}
    evs.check_manifest(dict(doc))
    body = (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")
    (folder / evs.MANIFEST).write_bytes(body)
    digest = evs.package_digest(doc)
    zip_bytes = _zip_bytes([(evs.MANIFEST, body)] + entries, deterministic=True)
    zname = f"{pid}-{version}-{digest.split(':', 1)[-1][:8]}.evidence.zip"
    for old in out.glob(f"{pid}-{version}-*.evidence.zip"):
        old.unlink()
    (out / zname).write_bytes(zip_bytes)
    archive = {"name": zname, "sha256": _sha(zip_bytes), "bytes": len(zip_bytes)}
    ref = evs.reference(doc, archive=archive, package_digest=digest)
    _update_index(out, ref)
    return ref


def _media_type(name: str) -> str:
    return {"csv": "text/csv", "json": "application/json"}.get(name.rsplit(".", 1)[-1].lower(),
                                                                "application/octet-stream")


def _update_index(out: Path, ref: dict) -> None:
    """``index.json`` beside the archives, the shape ``evidence_store.read_index`` reads."""
    p = out / evs.INDEX
    try:
        prior = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        prior = {}
    arch = ref.get("archive") or {}
    prior[ref["packageId"]] = {"packageId": ref["packageId"], "version": ref["version"],
                               "packageDigest": ref["packageDigest"], "dataDigest": ref["dataDigest"],
                               "zip": arch.get("name"), "zipSha256": arch.get("sha256"),
                               "zipBytes": arch.get("bytes"), "bytes": ref.get("bytes")}
    p.write_text(json.dumps(prior, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _manifest_common(*, region: Mapping, reproducibility: str, coverage: dict, recipe: dict,
                     checks: dict, sources: list, depends_on: list, limitations: list,
                     unavailable: list) -> dict:
    code, name = region.get("code"), region.get("name")
    return {
        "title": f"DEEP development evidence, Level III ecoregion {code}" + (f" ({name})" if name else ""),
        "description": ("The stations, values, pools, curves, candidates, rebuild ledger and "
                        "decisions behind one build of this ecoregion's DEEP reference assessment, "
                        "so every curve can be inspected and refit without the developer's drive."),
        "roles": ["development"], "reproducibility": reproducibility,
        "coverage": coverage, "dictionary": DICTIONARY, "sources": sources, "recipe": recipe,
        "checks": checks, "dependsOn": depends_on,
        "redistribution": {"status": "public-derived",
                           "notes": "Derived from public federal data (USEPA NRSA, NHDPlus V2, StreamCat)."},
        "limitations": limitations, "unavailable": unavailable, "producer": _producer(),
    }


def write_package(result: Mapping, doc: Optional[Mapping], out_dir, *,
                  version: Optional[str] = None, register: Optional[dict] = None) -> dict:
    """Write the package of a stage result under ``out_dir`` (``<out>/<packageId>/`` and the
    archive beside it) and return its reference (``evidence_store.reference`` with the
    archive named). ``doc`` is the build's provenance document (its manifest names the
    inputs digest, the NRSA archive, the run seed and the methodology); ``version`` names
    the package version (``package_version``: the inputs digest's prefix when absent);
    ``register`` the register already computed for the result. Refittable: the pool ledger
    is in it. Two writes of the same result give the same bytes."""
    from . import code_identity, methodology
    from . import candidates as C
    from . import curve_tiles as ct
    doc = dict(doc or {})
    manifest = dict(doc.get("manifest") or {})
    region = dict(result.get("region") or {})
    code = None if region.get("code") is None else str(region.get("code"))
    pid = package_id(code)
    inputs = manifest.get("inputs") or {}
    dataset = inputs.get("nrsa_dataset") or {}
    ver = version or package_version(inputs_digest=manifest.get("inputsDigest"))
    reg = register if register is not None else C.register_for_result(result)
    ledger = pv.build_ledger(result, manifest=manifest, register=reg)
    fields = ct.fields_from_result(result)
    screening = result.get("screening_tables") or {}
    sites = screening.get("easi_screening_sites")
    stations = stations_rows(sites=sites, panel_ledger=result.get("nrsa_panel_ledger"),
                             exclusions=result.get("owner_site_exclusions"),
                             overrides=result.get("nrsa_frame_overrides"))
    panel_keys = [r["station_key"] for r in stations if r["in_frame"]]
    support = {str(k): dict(v or {}) for k, v in (result.get("reference_support") or {}).items()}
    metrics = sorted(set(fields.get("metric_config") or {}) | set(support))
    pool_ledger = result.get("reference_pool_ledger")
    values = values_rows(ledger=pool_ledger, data=result.get("data"), station_keys=panel_keys,
                         metrics=metrics)
    approvals = list(result.get("portfolio_approvals") or (result.get("meta") or {}).get("portfolioApprovals") or [])
    standing = result.get("standing_decisions") or manifest.get("standingDecisions")
    data = {
        "stations.csv": _csv_bytes(stations, STATION_COLUMNS),
        "values.csv": _csv_bytes(values, VALUE_COLUMNS),
        "pools.json": _json_bytes(pools_doc(support, pool_ledger, code)),
        "curves.json": _json_bytes(curves_doc(fields, run_seed=result.get("run_seed"),
                                              method_version=(manifest.get("methodology") or {}).get("curveMethodVersion"))),
        "ladder.json": _json_bytes(ladder_doc(result.get("ladder_attempts"), result.get("ladder_populations"))),
        "candidates.json": _json_bytes(C.register_document(reg)),
        "ledger.json": _json_bytes(ledger),
        "decisions.json": _json_bytes(decisions_doc(
            curve_decisions=result.get("curve_decisions"), records=doc.get("records"),
            review_queue=doc.get("reviewQueue"), standing=standing,
            finalizations=result.get("finalized_metrics"), removals=result.get("removed_metrics"),
            approvals=approvals, gaps=result.get("coverage_exceptions"),
            enabled=(standing or {}).get("enabledIds") if isinstance(standing, Mapping) else (),
            curve_review=result.get("curve_review"), register=fields.get("candidate_register"))),
    }
    ledger_bytes = pool_ledger_bytes(pool_ledger)
    unavailable: list = []
    if ledger_bytes is not None:
        data["pool_ledger.csv"] = ledger_bytes
        reproducibility = REFITTABLE
    else:
        reproducibility = REVIEWABLE
        unavailable.append({"item": "the station-level pool ledger (pool_ledger.csv)",
                            "why": "the result carried no reference_pool_ledger",
                            "remedy": "stage the region again with run_region_batch.py stage"})
    if not stations:
        unavailable.append({"item": "the screened station panel (stations.csv is empty)",
                            "why": "the result carried no screening tables",
                            "remedy": "stage the region again with run_region_batch.py stage"})
    invariant = pv.ledger_rows_for_bundle(result.get("bundle"), ledger)
    with_ids = sum(1 for r in ledger.get("rows") or [] if r.get("disposition") == pv.REFITTED
                   and ((r.get("pool") or {}).get("stationIds") or (r.get("pool") or {}).get("donorRegions")))
    refitted = sum(1 for r in ledger.get("rows") or [] if r.get("disposition") == pv.REFITTED)
    screen = result.get("reference_screen") or {}
    station_screen = screen.get("stationScreen") or {}
    recipe = {"code": {"fingerprint": code_identity.fingerprint(),
                       "scope": [f"{s}/{','.join(p)}" for s, p in code_identity.SCOPE]},
              "methodology": manifest.get("methodology") or methodology.config_fingerprints(),
              "engine": _engine_block(), "runSeed": result.get("run_seed"),
              "inputsDigest": manifest.get("inputsDigest"), "digestSchema": manifest.get("digestSchema"),
              "referenceMethod": result.get("reference_method"),
              "valuePolicy": (result.get("value_selection") or {}).get("policy")}
    sources = [_nrsa_source(dataset.get("manifestDigest"))]
    if station_screen:
        sources.append({"id": "station-screen", "path": "apps/stream-curves/" + str(station_screen.get("path") or "data/nrsa/station_screen.parquet"),
                        "sha256": station_screen.get("sha256"),
                        "citation": "the committed station screen table (reference_screen.py, REF-04)"})
    manifest_extra = _manifest_common(
        region=region, reproducibility=reproducibility,
        coverage={"region": {"kind": region.get("kind"), "code": code, "name": region.get("name")},
                  "assessmentId": result.get("assessment_id"),
                  "stations": len(panel_keys), "stationsOutOfFrame": len(stations) - len(panel_keys),
                  "values": len(values), "metrics": len(metrics), "curves": len(fields.get("completed_metrics") or {}),
                  "ledgerRows": len(ledger.get("rows") or []), "poolLedgerRows": (int(len(pv._ledger_frame(pool_ledger))) if pv._ledger_frame(pool_ledger) is not None else 0),
                  "functionsScored": len({p[1] for p in invariant["bundle"]})},
        recipe=recipe,
        checks={"ledgerSelectedEqualsBundle": bool(invariant["equal"]),
                "refittedRowsWithStationEvidence": {"n": with_ids, "of": refitted}},
        sources=sources,
        depends_on=[{"packageId": "nrsa-archive", "version": dataset.get("datasetId") or _nrsa_source().get("datasetId"),
                     "dataDigest": dataset.get("manifestDigest") or _nrsa_source().get("sha256")}],
        limitations=["Values are the archive's per-station values under the value policy named "
                     "in the recipe, not raw survey records.",
                     "A carried curve's stations are those of the version that built it; its "
                     "row in ledger.json names that version."],
        unavailable=unavailable)
    return _write(out_dir, pid=pid, version=ver, data=data, manifest=manifest_extra)


def write_from_fields(fields: Mapping, out_dir, *, code: Any = None, version: Optional[str] = None,
                      provenance: Optional[Mapping] = None) -> dict:
    """The package of a session's fields alone (a scratch interactive publish, a reopened
    version): no pool ledger, so ``reviewable``, with the stations and values the session
    holds, every curve it scores, its register, its ledger and its decisions. ``provenance``:
    the session's origin document when it has one (its manifest names the digest and seed)."""
    from . import code_identity, methodology
    from . import candidates as C
    from . import curve_tiles as ct
    fields = ct.fields_from_result(dict(fields)) | {k: v for k, v in dict(fields).items()
                                                    if k not in ct.FIELD_KEYS}
    region = dict(fields.get("region_of_applicability") or {})
    code = region.get("code") if code is None else code
    pid = package_id(code)
    doc = dict(provenance or fields.get("source_provenance") or {})
    manifest = dict(doc.get("manifest") or {})
    ver = version or package_version(inputs_digest=manifest.get("inputsDigest") or None)
    reg = C.register_for_result(fields)
    ledger = pv.build_ledger(fields, manifest=manifest, register=reg)
    build = fields.get("reference_build") or {}
    support = pv._support_from_build(build)
    stations = stations_rows(sites=fields.get("easi_screening_sites"),
                             exclusions=fields.get("site_exclusions"))
    panel_keys = [r["station_key"] for r in stations if r["in_frame"]]
    metrics = sorted(set(fields.get("metric_config") or {}) | set(support))
    values = values_rows(data=fields.get("data"), station_keys=panel_keys, metrics=metrics)
    approvals = list(fields.get("portfolio_approvals") or [])
    withheld = build.get("insufficientReferenceSupport") or []
    attempts = [{"metric": w.get("metricKey"), "rung": a.get("rung"), "admitted": False,
                 "why": a.get("why"), "condition": a.get("condition")}
                for w in withheld for a in (w.get("rungsTried") or [])]
    data = {
        "stations.csv": _csv_bytes(stations, STATION_COLUMNS),
        "values.csv": _csv_bytes(values, VALUE_COLUMNS),
        "pools.json": _json_bytes(pools_doc(support, None, None if code is None else str(code))),
        "curves.json": _json_bytes(curves_doc(fields, run_seed=(manifest.get("diagnostics") or {}).get("runSeed"),
                                              method_version=(manifest.get("methodology") or {}).get("curveMethodVersion"))),
        "ladder.json": _json_bytes(ladder_doc(attempts)),
        "candidates.json": _json_bytes(C.register_document(reg)),
        "ledger.json": _json_bytes(ledger),
        "decisions.json": _json_bytes(decisions_doc(
            curve_decisions=fields.get("owner_curve_decisions"), records=doc.get("records"),
            review_queue=doc.get("reviewQueue"), standing=manifest.get("standingDecisions"),
            finalizations=(manifest.get("reviewerInputs") or {}).get("finalizedMetrics"),
            removals=(manifest.get("reviewerInputs") or {}).get("removedMetrics"),
            approvals=approvals, gaps=fields.get("function_coverage_exceptions"),
            enabled=fields.get("rule_selections") or (), curve_review=fields.get("curve_review"),
            register=fields.get("candidate_register"))),
    }
    unavailable = [{"item": "the station-level pool ledger (pool_ledger.csv)",
                    "why": "a session keeps the pooled values, not the station-by-station pool "
                           "decisions of its build",
                    "remedy": "stage the region with run_region_batch.py stage: its package is refittable"}]
    if not stations:
        unavailable.append({"item": "the screened station panel (stations.csv is empty)",
                            "why": "the session holds no screening table",
                            "remedy": "stage the region with run_region_batch.py stage"})
    invariant = pv.ledger_rows_for_bundle(fields.get("bundle"), ledger) if fields.get("bundle") else None
    recipe = {"code": {"fingerprint": code_identity.fingerprint(),
                       "scope": [f"{s}/{','.join(p)}" for s, p in code_identity.SCOPE]},
              "methodology": manifest.get("methodology") or methodology.config_fingerprints(),
              "engine": _engine_block(), "runSeed": (manifest.get("diagnostics") or {}).get("runSeed"),
              "inputsDigest": manifest.get("inputsDigest"), "digestSchema": manifest.get("digestSchema"),
              "referenceMethod": build.get("method")}
    dataset = (manifest.get("inputs") or {}).get("nrsa_dataset") or {}
    manifest_extra = _manifest_common(
        region=region, reproducibility=REVIEWABLE,
        coverage={"region": {"kind": region.get("kind"), "code": None if code is None else str(code),
                             "name": region.get("name")},
                  "stations": len(panel_keys), "values": len(values), "metrics": len(metrics),
                  "curves": len(fields.get("completed_metrics") or {}),
                  "ledgerRows": len(ledger.get("rows") or []), "poolLedgerRows": 0},
        recipe=recipe,
        checks={"ledgerSelectedEqualsBundle": (bool(invariant["equal"]) if invariant else None)},
        sources=[_nrsa_source(dataset.get("manifestDigest"))],
        depends_on=[{"packageId": "nrsa-archive", "version": dataset.get("datasetId") or _nrsa_source().get("datasetId"),
                     "dataDigest": dataset.get("manifestDigest") or _nrsa_source().get("sha256")}],
        limitations=["Built from a session's fields: the values are the pooled frame the session "
                     "holds and no pool ledger is in it, so the curves can be reviewed, not refit "
                     "from this package alone."],
        unavailable=unavailable)
    return _write(out_dir, pid=pid, version=ver, data=data, manifest=manifest_extra)


__all__ = ["SCHEMA", "SCHEMA_VERSION", "KIND", "PACKAGE_PREFIX", "REFITTABLE", "REVIEWABLE",
           "STATION_COLUMNS", "VALUE_COLUMNS", "LEDGER_COLUMNS", "DICTIONARY", "EvidenceExportError",
           "package_id", "package_version", "stations_rows", "values_rows", "pool_ledger_bytes",
           "pools_doc", "curves_doc", "ladder_doc", "decisions_doc", "write_package",
           "write_from_fields"]
