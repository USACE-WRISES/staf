"""Evidence packages and EASI refits.

The fit recipe is the builder's code, vendored verbatim (a drift gate); a package is ready
only when every file matches its manifest; archives never write outside their package or
carry anything but data; downloads resume and verify; and refitting the members package
reproduces the curve registry and the 34 operational curves. The full refit runs when the
exported packages are present (``STAF_EVIDENCE_DIR``, a folder of package folders).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import numpy as np
import pytest

from streamcurves import evidence_store as es
from streamcurves.easi_method import fit_recipe as fr
from streamcurves.easi_method import refit

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent.parent
BUILDER = REPO / "tools" / "easi-national" / "builder" / "analysis"
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"


def test_the_fit_recipe_is_the_builders_code_verbatim():
    if not BUILDER.is_dir():
        pytest.skip("tools/easi-national is not in this copy")
    proc = subprocess.run([sys.executable, "-B", str(APP / "scripts" / "vendor_fit_recipe.py"), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# a small package, written the way the exporter writes one
# --------------------------------------------------------------------------- #
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_package(folder: Path, files: dict[str, bytes], **extra) -> dict:
    (folder / "data").mkdir(parents=True, exist_ok=True)
    table = {}
    for rel, blob in files.items():
        (folder / rel).write_bytes(blob)
        table[rel] = {"bytes": len(blob), "sha256": _sha(blob)}
    doc = {"schema": es.SCHEMA, "schemaVersion": 1, "packageId": "test-pkg", "version": "1",
           "dataDigest": es.data_digest(table), "files": table, "roles": ["development"], **extra}
    (folder / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def _zip(folder: Path, out: Path, *, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(out, "w") as z:
        z.write(folder / "evidence.json", "evidence.json")
        for p in sorted((folder / "data").rglob("*")):
            z.write(p, str(p.relative_to(folder)).replace("\\", "/"))
        for name, blob in (extra or {}).items():
            z.writestr(name, blob)
    return out


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "pkg"
    doc = _make_package(folder, {"data/a.json": b'{"x": 1}\n', "data/b.csv": b"k,v\n1,2\n"})
    return folder, doc


def test_a_package_installs_verified_and_is_reused(package, tmp_path):
    folder, doc = package
    root = tmp_path / "store"
    target = es.install_zip(_zip(folder, tmp_path / "p.evidence.zip"), root=root)
    assert es.verify_folder(target)["ok"] and target.parent.name == "test-pkg"
    assert es.install_zip(tmp_path / "p.evidence.zip", root=root) == target
    [rec] = es.installed(root)
    assert rec["dataDigest"] == doc["dataDigest"] and es.find("test-pkg", root=root) == target
    # a damaged installed copy is replaced by the next install
    (target / "data" / "a.json").write_bytes(b"{}")
    assert not es.verify_folder(target)["ok"]
    assert es.verify_folder(es.install_zip(tmp_path / "p.evidence.zip", root=root))["ok"]


def test_a_damaged_or_unsafe_archive_is_refused(package, tmp_path):
    folder, doc = package
    root = tmp_path / "store"
    for extra, match in (({"../evil.json": b"{}"}, "unsafe path"),
                         ({"data/tool.py": b"print(1)"}, "data files only"),
                         ({"data/c.json": b"{}"}, "does not list")):
        z = _zip(folder, tmp_path / "bad.evidence.zip", extra=extra)
        with pytest.raises(es.EvidenceError, match=match):
            es.install_zip(z, root=root)
    (folder / "data" / "a.json").write_bytes(b'{"x": 2}\n')          # content no longer matches
    with pytest.raises(es.EvidenceError, match="does not match its manifest"):
        es.install_zip(_zip(folder, tmp_path / "changed.evidence.zip"), root=root)
    good = _zip(package[0], tmp_path / "good.evidence.zip")
    (tmp_path / "trunc.evidence.zip").write_bytes(good.read_bytes()[:-40])
    with pytest.raises(es.EvidenceError):
        es.install_zip(tmp_path / "trunc.evidence.zip", root=root)
    assert not any(p.name.startswith(".staging") for p in root.iterdir())


def test_a_newer_package_format_is_refused_whole(package, tmp_path):
    folder, doc = package
    doc["schemaVersion"] = es.SCHEMA_VERSION + 1
    (folder / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(es.EvidenceError, match="newer than this app"):
        es.install_folder(folder, root=tmp_path / "store")


def test_downloads_resume_and_verify(package, tmp_path):
    folder, _ = package
    z = _zip(folder, tmp_path / "test-pkg-1-abcd1234.evidence.zip")
    blob = z.read_bytes()
    root = tmp_path / "store"
    # a folder base, resuming a .part that holds the first half
    (root / ".downloads").mkdir(parents=True)
    (root / ".downloads" / (z.name + ".part")).write_bytes(blob[: len(blob) // 2])
    got = es.download(str(tmp_path), z.name, sha256=_sha(blob), size=len(blob), root=root)
    assert got.read_bytes() == blob

    # an http base: the server honours the range; a cancel keeps the part
    class Resp:
        def __init__(self, data, status):
            self.data, self.status_code = data, status

        def iter_content(self, n):
            for i in range(0, len(self.data), 7):
                yield self.data[i:i + 7]

        def close(self):
            pass

    seen = []

    def fake_get(url, headers=None, stream=False, timeout=0):
        seen.append(dict(headers or {}))
        start = int((headers or {}).get("Range", "bytes=0-")[6:-1] or 0)
        return Resp(blob[start:], 206 if start else 200)

    root2 = tmp_path / "store2"
    cancel = threading.Event()
    calls = {"n": 0}

    def progress(got, total):
        calls["n"] += 1
        if calls["n"] == 3:
            cancel.set()

    old = es.http_get
    es.http_get = fake_get
    try:
        with pytest.raises(es.EvidenceCancelled):
            es.download("https://example.invalid/base", z.name, sha256=_sha(blob), size=len(blob),
                        root=root2, progress=progress, cancel=cancel)
        part = root2 / ".downloads" / (z.name + ".part")
        assert 0 < part.stat().st_size < len(blob)
        got = es.download("https://example.invalid/base", z.name, sha256=_sha(blob), size=len(blob),
                          root=root2)
        assert got.read_bytes() == blob and seen[-1].get("Range", "").startswith("bytes=")
        with pytest.raises(es.EvidenceError, match="did not match"):
            es.download("https://example.invalid/base", "other-1-00000000.evidence.zip",
                        sha256="0" * 64, size=len(blob), root=root2)
    finally:
        es.http_get = old


# --------------------------------------------------------------------------- #
# refitting
# --------------------------------------------------------------------------- #
def _synthetic():
    """Two NARS-9 strata and the national panel of one quantity, 60 members each."""
    import pandas as pd
    rng = np.random.default_rng(3)
    rows, vals = [], {}
    comid = 1
    for level, stratum in (("nars9", "nars9:CPL"), ("nars9", "nars9:NAP"), ("national", "national:national")):
        for _ in range(60):
            rows.append({"comid": comid, "huc12": f"h{comid}", "level": level, "stratum": stratum,
                         "slope_class": "0.5_to_2", "fcode_class": "stream", "screen": "strict",
                         "panel_tier": "exploratory"})
            vals[comid] = rng.uniform(40, 100)
            comid += 1
    members = pd.DataFrame(rows)
    values = pd.DataFrame({"comid": list(vals), "woody_wsrp100": list(vals.values()),
                           "composite_pressure": rng.uniform(0, 1, len(vals))})
    panels = pd.DataFrame([{"level": lv, "stratum": st, "panel_tier": "exploratory", "screen": "strict"}
                           for lv, st in {(r["level"], r["stratum"]) for r in rows}])
    return members, values, panels


def test_the_refit_groups_members_like_the_builder():
    members, values, panels = _synthetic()
    rows = refit.fit_registry(members, values, panels, quantities=["woody_wsrp100"])
    keys = sorted((r["level"], r["stratum"]) for r in rows)
    assert keys == [("nars9", "nars9:CPL"), ("nars9", "nars9:NAP"), ("national", "national:national")]
    for r in rows:
        assert r["n"] == 60 and r["status"] == "complete" and r["points"]
        assert r["x39"] is not None and r["x69"] is not None
    # the rounded curve has the method file's shape
    curve = fr._curve(rows[0])
    assert set(curve) >= {"points", "n", "nMembers", "q25", "q50", "q75", "x39", "x69", "status",
                          "panelTier", "screen"}


EVIDENCE_DIR = os.environ.get("STAF_EVIDENCE_DIR", "")


@pytest.mark.skipif(not EVIDENCE_DIR or not (Path(EVIDENCE_DIR) / "easi-dev-members").is_dir(),
                    reason="the exported evidence packages are not present (STAF_EVIDENCE_DIR)")
def test_refitting_the_members_package_reproduces_the_registry_and_the_34_curves():
    import pyarrow.parquet as pq
    base = Path(EVIDENCE_DIR)
    members, values, panels = refit.load_members(base / "easi-dev-members")
    rows = refit.fit_registry(members, values, panels)
    stored = pq.read_table(base / "easi-dev-fits" / "data" / "curve_registry.parquet").to_pylist()
    cmp_ = refit.compare_registry(rows, stored)
    assert cmp_["identical"], cmp_["differing"][:5]
    curves = refit.operational_curves(rows, members, values, panels)
    artifact = json.loads((VENDORED_DATA / "reference-curves.json").read_text(encoding="utf-8"))
    got = refit.compare_curves(curves, artifact)
    assert got["allIdentical"], got["differing"][:5]


# --------------------------------------------------------------------------- #
# a project names its development data
# --------------------------------------------------------------------------- #
def test_a_project_names_packages_without_changing_its_method(package, tmp_path):
    from streamcurves.easi_method import evidence as ev
    from streamcurves.easi_method import io as eio
    folder, doc = package
    project = eio.import_from_easi(VENDORED_DATA, imported_by="t", cases={"cases": []})
    ref = ev.reference(doc, archive={"name": "test-pkg-1-abcd1234.evidence.zip", "sha256": "0" * 64,
                                     "bytes": 10}, package_digest=es.package_digest(doc))
    named = ev.attach(project, ref, by="tester")
    assert named.package_digest == project.package_digest and named.files == project.files
    assert [e["packageId"] for e in named.evidence] == ["test-pkg"]
    assert named.history[-1]["action"] == "attach_evidence"
    assert ev.attach(named, ref, by="tester") is named                 # nothing new
    assert ev.status(ref, []) == "missing"
    installed = es.install_folder(folder, root=tmp_path / "store")
    assert ev.status(ref, es.installed(tmp_path / "store")) == "installed"
    assert not ev.detach(named, "test-pkg", by="tester").evidence
    # the reference survives a save and a reopen
    from streamcurves.easi_method.model import EasiProject
    assert EasiProject.from_parts(named.to_parts()).evidence == named.evidence
    assert installed.parent.name == "test-pkg"


def test_the_import_names_an_evidence_exports_packages(package, tmp_path):
    from streamcurves.easi_method import io as eio
    folder, doc = package
    export = tmp_path / "export"
    (export / "test-pkg").mkdir(parents=True)
    for p in folder.rglob("*"):
        if p.is_file():
            dest = export / "test-pkg" / p.relative_to(folder)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    (export / "index.json").write_text(json.dumps({"test-pkg": {
        "dataDigest": doc["dataDigest"], "zip": "test-pkg-1-abcd1234.evidence.zip",
        "zipSha256": "1" * 64, "zipBytes": 123}}), encoding="utf-8")
    [ref] = eio.evidence_references(export)
    assert ref["dataDigest"] == doc["dataDigest"] and ref["archive"]["bytes"] == 123
    bad = json.loads((export / "index.json").read_text(encoding="utf-8"))
    bad["test-pkg"]["dataDigest"] = "sha256:" + "0" * 64
    (export / "index.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        eio.evidence_references(export)


def test_recorded_checks_read_as_sentences():
    from views import easi_page as ep
    lines = ep.check_lines({"eromMonthsReproduceStoredCv": {"comparable": 5, "identicalAtStoredPrecision": 5,
                                                            "storedType": "float", "nullsAgree": True},
                            "panelsRegenerateMembers": {"identical": True, "memberRows": 7, "seconds": 1.0},
                            "other": {"x": 1}})
    assert lines[0].startswith("The 12 monthly EROM flows reproduce the stored flow CV of 5 of 5")
    assert lines[1].startswith("Drawing the panels again from this package gives the same 7")
    assert lines[2] == 'other: {"x": 1}'


def test_the_nrsa_archive_is_described_as_a_verified_package():
    rec = es.nrsa_archive_record()
    if rec is None:
        pytest.skip("the NRSA archive is not built in this copy")
    assert rec["verified"] and not rec["damaged"] and rec["coverage"]["files"] > 0
    assert rec["roles"] == ["development"] and rec["reproducibility"] == "regenerable"


@pytest.mark.skipif(not EVIDENCE_DIR or not (Path(EVIDENCE_DIR) / "easi-dev-universe").is_dir(),
                    reason="the exported universe package is not present (STAF_EVIDENCE_DIR)")
def test_the_universe_package_draws_the_same_panel_members():
    base = Path(EVIDENCE_DIR)
    members, _, _ = refit.load_members(base / "easi-dev-members")
    _, regenerated = refit.regenerate_members(base / "easi-dev-universe")
    assert refit.same_members(regenerated, members)["identical"]
