"""EA1: the study's case set for ``campaigns.evaluation_campaign``.

``export_cases`` writes every evidence row of the study (the stored sample and the NRSA
stations, exactly the records the scorer scores) as the cases files StreamCurves'
``easi_method.campaigns.evaluation_campaign`` expects (``{"schema": ..., "cases": [{"id",
"record"}]}``), chunked because one file would hold well over a hundred thousand records,
with an index naming each chunk's sha256, rows and COMIDs. ``replay_base.py`` scores the base
package through the campaign on those files and compares rating, index and ECI per COMID with
the study's ``scores/alternative-1.parquet``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

SCHEMA = "staf-easi-evaluation-cases"
CHUNK = 2000


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def case_of(raw: dict) -> dict:
    """One evidence row as a campaign case: the record decoded exactly as the scorer decodes
    it (``records.from_row``), keyed by its COMID."""
    from easi.national import records
    record = records.from_row(raw)
    return {"id": str(int(raw["comid"])), "record": record}


def export_cases(study: Path, out_dir: Path | None = None, *, chunk: int = CHUNK) -> dict:
    """Write the study's case set under ``out_dir`` (default ``<study>/cases``): files
    ``cases-NNNN.json`` of at most ``chunk`` cases each, in evidence file order, and
    ``index.json`` naming every chunk (path, sha256, rows, first and last COMID) and the
    evidence files they came from. Returns the index."""
    study = Path(study)
    out_dir = Path(out_dir) if out_dir is not None else study / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted((study / "evidence").rglob("*.parquet"))
    if not sources:
        raise FileNotFoundError(f"{study} holds no evidence parquet files (run the evidence step first)")
    chunks, buffer, seen, evidence_files = [], [], set(), []
    number = 0

    def flush():
        nonlocal buffer, number
        if not buffer:
            return
        path = out_dir / f"cases-{number:04d}.json"
        doc = {"schema": SCHEMA, "schemaVersion": 1, "study": study.name, "chunk": number,
               "source": "study evidence (stored sample and NRSA stations)", "cases": buffer}
        path.write_text(json.dumps(doc, sort_keys=True, default=str), encoding="utf-8", newline="\n")
        chunks.append({"path": str(path), "name": path.name, "sha256": _sha(path), "rows": len(buffer),
                       "first_comid": int(buffer[0]["id"]), "last_comid": int(buffer[-1]["id"])})
        number += 1
        buffer = []

    for source in sources:
        table = pq.read_table(source)
        evidence_files.append({"path": str(source), "rows": table.num_rows, "sha256": _sha(source)})
        for raw in table.to_pylist():
            comid = int(raw["comid"])
            if comid in seen:
                raise ValueError(f"duplicate COMID in the study evidence: {comid}")
            seen.add(comid)
            buffer.append(case_of(raw))
            if len(buffer) >= chunk:
                flush()
    flush()
    index = {"schema": SCHEMA + "-index", "schemaVersion": 1, "study": study.name, "chunk_size": chunk,
             "cases": len(seen), "chunks": chunks, "evidence_files": evidence_files,
             "identity": "one case per COMID; the record is records.from_row of the evidence row; scored with cross_section=False"}
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return index


__all__ = ["SCHEMA", "CHUNK", "case_of", "export_cases"]
