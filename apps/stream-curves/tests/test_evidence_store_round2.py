"""The evidence store after the second review of Phase 6 (review B, round 2).

One unreadable manifest never blanks the store and a new import repairs it; an edited manifest
reads as damage, never as another version; a reuse hashes every file; an archive of another
version under a pinned name is never installed; an unreachable host is said to be one; package
ids are plain folder names; ``pick`` takes the newest verified copy; and the page offers and
carries over only what is verified to be the package named. Every test works in its own store
(``root=``), never in the data root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

from streamcurves import evidence_store as es
from streamcurves.easi_method import evidence as ev

APP = Path(__file__).resolve().parents[1]


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


def _zip(folder: Path, out: Path) -> Path:
    with zipfile.ZipFile(out, "w") as z:
        z.write(folder / "evidence.json", "evidence.json")
        for p in sorted((folder / "data").rglob("*")):
            z.write(p, str(p.relative_to(folder)).replace("\\", "/"))
    return out


def _ref(doc, **extra):
    return ev.reference(doc, package_digest=es.package_digest(doc), **extra)


FILES = {"data/a.json": b'{"x": 1}\n', "data/b.csv": b"k,v\n1,2\n"}


@pytest.fixture
def two(tmp_path):
    """Two packages installed in one store: ``test-pkg`` and ``test-other``."""
    root = tmp_path / "store"
    a_doc = _make_package(tmp_path / "a", FILES)
    b_doc = _make_package(tmp_path / "b", FILES, packageId="test-other")
    a = es.install_folder(tmp_path / "a", root=root)
    b = es.install_folder(tmp_path / "b", root=root)
    return root, (tmp_path / "a", a_doc, a), (tmp_path / "b", b_doc, b)


# --------------------------------------------------------------------------- #
# N1: one unreadable manifest never hides the others, and a new import repairs it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spoil", ["nan", "overflow", "list-schema"])
def test_one_unreadable_manifest_never_blanks_the_store(two, spoil):
    root, (a_src, a_doc, a), (b_src, b_doc, b) = two
    text = json.dumps({**b_doc, "note": 0})
    if spoil == "nan":
        text = text.replace('"note": 0', '"note": NaN')
    elif spoil == "overflow":
        text = text.replace('"note": 0', '"note": 1e999')
    else:
        text = json.dumps({**b_doc, "schemaVersion": [1]})
    (b / "evidence.json").write_text(text, encoding="utf-8")

    recs = {r["packageId"]: r for r in es.installed(root)}
    assert set(recs) == {"test-pkg", "test-other"}
    assert recs["test-pkg"]["verified"] and recs["test-pkg"]["installedAs"] == a.name
    bad = recs["test-other"]
    assert not bad["verified"] and bad["damaged"] == ["evidence.json"] and bad["installedAs"] == b.name
    assert bad["version"] is None and bad["problem"]

    assert es.ready(_ref(a_doc), root=root) == a
    assert ev.status(_ref(b_doc), es.installed(root)) == "damaged"
    with pytest.raises(es.EvidenceError, match="damaged on this computer"):
        es.ready(_ref(b_doc), root=root)

    # a new import of the package replaces the unreadable copy
    assert es.install_folder(b_src, root=root) == b
    assert ev.status(_ref(b_doc), es.installed(root)) == "installed"
    assert es.ready(_ref(b_doc), root=root) == b


def test_a_manifest_refuses_values_that_are_not_finite_numbers():
    for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}', '{"a": -1e999}'):
        with pytest.raises(es.EvidenceError, match="not a finite number"):
            es.loads(text)
    assert es.loads('{"a": 1.5, "b": 10, "c": 1e300}') == {"a": 1.5, "b": 10, "c": 1e300}


# --------------------------------------------------------------------------- #
# N2: an edited manifest is damage, never another version
# --------------------------------------------------------------------------- #
def test_an_edited_manifest_reads_damaged_not_another_version(two):
    root, (a_src, a_doc, a), _ = two
    edited = {**a_doc, "reproducibility": "regenerable", "limitations": []}
    (a / "evidence.json").write_text(json.dumps(edited), encoding="utf-8")
    rec = next(r for r in es.installed(root) if r["packageId"] == "test-pkg")
    assert not rec["verified"] and rec["damaged"] == ["evidence.json"]
    assert rec["installedAs"] == a.name and rec["packageDigest"] == es.package_digest(edited)
    assert es.matches(rec, _ref(a_doc))
    assert ev.status(_ref(a_doc), es.installed(root)) == "damaged"
    with pytest.raises(es.EvidenceError, match="damaged on this computer"):
        es.ready(_ref(a_doc), root=root)


# --------------------------------------------------------------------------- #
# N3: a reuse hashes every file, whatever the size and time say
# --------------------------------------------------------------------------- #
def test_a_reuse_hashes_every_file_even_when_size_and_time_hold(tmp_path):
    folder = tmp_path / "pkg"
    _make_package(folder, FILES)
    z = _zip(folder, tmp_path / "p.evidence.zip")
    root = tmp_path / "store"
    target = es.install_zip(z, root=root)
    f = target / "data" / "a.json"
    st = f.stat()
    f.write_bytes(b'{"x": 9}\n')                        # same size
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))   # same time
    assert es.installed(root)[0]["verified"]           # the listing trusts the record ...
    assert es.install_zip(z, root=root) == target      # ... a reuse does not
    assert f.read_bytes() == FILES["data/a.json"] and es.verify_folder(target)["ok"]


# --------------------------------------------------------------------------- #
# N4: an archive of another version under the pinned name is never installed
# --------------------------------------------------------------------------- #
def test_another_version_under_the_pinned_name_is_not_installed(tmp_path):
    v1 = _make_package(tmp_path / "v1", FILES, description="As exported.")
    v2 = _make_package(tmp_path / "v2", FILES, description="Corrected later.")
    assert v1["dataDigest"] == v2["dataDigest"] and es.package_digest(v1) != es.package_digest(v2)
    host = tmp_path / "host"
    host.mkdir()
    name = "test-pkg-1-aaaaaaaa.evidence.zip"
    z2 = _zip(tmp_path / "v2", host / name)
    # the reference names v1 but pins the archive now on the host under that name (v2's bytes)
    ref = _ref(v1, archive={"name": name, "sha256": _sha(z2.read_bytes()), "bytes": z2.stat().st_size})
    root = tmp_path / "store"
    with pytest.raises(es.EvidenceError, match="does not hold this version"):
        es.fetch_reference(str(host), ref, root=root)
    assert not (root / ".downloads" / name).exists()
    assert es.installed(root) == []


def test_the_page_carries_an_archive_record_over_only_for_the_same_package():
    old = {"packageId": "test-pkg", "packageDigest": "sha256:" + "1" * 64,
           "archive": {"name": "x.evidence.zip", "sha256": "2" * 64, "bytes": 5}}
    assert ev.carried_archive(old, "sha256:" + "1" * 64) == old["archive"]
    assert ev.carried_archive(old, "sha256:" + "3" * 64) is None          # same id, other version
    assert ev.carried_archive({k: v for k, v in old.items() if k != "packageDigest"},
                              "sha256:" + "1" * 64) is None                # nothing proves it
    assert ev.carried_archive(None, "sha256:" + "1" * 64) is None
    assert ev.carried_archive({**old, "archive": None}, "sha256:" + "1" * 64) is None


# --------------------------------------------------------------------------- #
# N8: "Attach from this computer" offers verified copies only
# --------------------------------------------------------------------------- #
def test_only_verified_packages_are_offered_to_attach(tmp_path):
    def rec(pid, folder, *, verified=True, age=0):
        path = tmp_path / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / "evidence.json").write_text("{}", encoding="utf-8")
        os.utime(path / "evidence.json", (1_700_000_000 + age, 1_700_000_000 + age))
        return {"packageId": pid, "path": str(path), "verified": verified}

    inst = [rec("easi-dev-members", "m-old", age=0), rec("easi-dev-members", "m-new", age=50),
            rec("easi-dev-fits", "f", verified=False), rec("easi-eval-refs", "e"),
            rec("other-pkg", "o"), rec("easi-dev-universe", "u")]
    got = ev.spare_packages(inst, [{"packageId": "easi-dev-universe"}])
    assert [(r["packageId"], Path(r["path"]).name) for r in got] == [
        ("easi-dev-members", "m-new"), ("easi-eval-refs", "e")]
    assert ev.spare_packages([], []) == []


# --------------------------------------------------------------------------- #
# N10: an unreachable host is said to be one
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, data=b"", status=200):
        self.data, self.status_code = data, status

    @property
    def content(self):
        return self.data

    def close(self):
        pass


def test_an_unreachable_host_is_not_read_as_one_without_the_package(tmp_path, monkeypatch):
    doc = _make_package(tmp_path / "pkg", FILES)
    ref = _ref(doc)                                     # no archive pin: the index decides
    root = tmp_path / "store"

    def down(url, headers=None, stream=False, timeout=0):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(es, "http_get", down)
    assert es.read_index("https://unreachable.invalid/base") is None
    with pytest.raises(es.EvidenceError, match="could not be reached"):
        es.fetch_reference("https://unreachable.invalid/base", ref, root=root)

    monkeypatch.setattr(es, "http_get", lambda url, headers=None, stream=False, timeout=0: _Resp(b"", 404))
    assert es.read_index("https://example.invalid/base") == {}
    with pytest.raises(es.EvidenceError, match="does not hold this version"):
        es.fetch_reference("https://example.invalid/base", ref, root=root)

    assert es.read_index(str(tmp_path / "no-such-folder")) is None
    with pytest.raises(es.EvidenceError, match="could not be reached"):
        es.fetch_reference(str(tmp_path / "no-such-folder"), ref, root=root)
    empty = tmp_path / "empty-host"
    empty.mkdir()
    assert es.read_index(str(empty)) == {}


# --------------------------------------------------------------------------- #
# N12: a package id is a plain folder name
# --------------------------------------------------------------------------- #
def test_package_ids_are_plain_folder_names():
    for bad in ("con", "aux", "com1", "nul", "prn", "lpt9", "abc.", "con.x", "con\n", "easi\n",
                "Upper", "a" * 65, "", None, 7):
        assert not es.usable_package_id(bad), bad
    for good in ("easi-dev-members", "a.b", "com10", "console", "nul-data", "x_1"):
        assert es.usable_package_id(good), good


def test_a_manifest_with_a_reserved_id_or_a_ragged_version_is_refused(tmp_path):
    doc = _make_package(tmp_path / "pkg", FILES)
    for field, value in (("packageId", "nul"), ("packageId", "com1.data"), ("version", "1\n")):
        with pytest.raises(es.EvidenceError, match="unusable"):
            es.check_manifest({**doc, field: value})


# --------------------------------------------------------------------------- #
# pick: the verified copy, the newest when several verify
# --------------------------------------------------------------------------- #
def test_pick_takes_the_newest_verified_copy(tmp_path):
    root = tmp_path / "store"
    _make_package(tmp_path / "v1", FILES, description="As exported.")
    _make_package(tmp_path / "v2", FILES, description="Corrected later.")
    c1 = es.install_folder(tmp_path / "v1", root=root)
    c2 = es.install_folder(tmp_path / "v2", root=root)
    folder = root / "test-pkg"
    os.utime(c1 / "evidence.json", (1_800_000_000, 1_800_000_000))
    os.utime(c2 / "evidence.json", (1_700_000_000, 1_700_000_000))
    assert es.pick(folder) == c1
    os.utime(c2 / "evidence.json", (1_900_000_000, 1_900_000_000))
    assert es.pick(folder) == c2
    (c2 / "data" / "b.csv").write_bytes(b"k,v\n")      # the newest is damaged: never picked
    assert es.pick(folder) == c1
    (c1 / "data" / "b.csv").write_bytes(b"k,v\n")
    with pytest.raises(es.EvidenceError, match="no verified package"):
        es.pick(folder)
    assert es.pick(tmp_path / "v1") == tmp_path / "v1"  # a package folder is itself


# --------------------------------------------------------------------------- #
# N9: a refit is exact only when the engine, the refit's code and the constants are recorded
# and agree
# --------------------------------------------------------------------------- #
def _recipe_package(tmp_path, name, **recipe) -> Path:
    folder = tmp_path / name
    _make_package(folder, FILES, packageId="easi-dev-members", recipe=recipe)
    return folder


def test_the_recipe_check_needs_the_engine_and_the_refits_code_on_record(tmp_path):
    from streamcurves.easi_method import refit
    from streamcurves.paths import ROOT
    engine = {"sha256_lf": _sha((ROOT / "streamcurves" / "curves.py").read_bytes().replace(b"\r\n", b"\n"))}
    code = refit.recipe_code()
    same = refit.recipe_check(_recipe_package(tmp_path, "same", engine=engine, code=code))
    assert same["same"] and same["differences"] == [] and same["notRecorded"] == []
    assert refit.recipe_words(same) is None

    old = refit.recipe_check(_recipe_package(tmp_path, "old", engine=engine))      # before round 2
    assert not old["same"] and old["notRecorded"] == ["the fit recipe code", "the refit code"]
    assert refit.recipe_words(old) == ("The refit is not expected to match exactly: the package does not "
                                       "record the fit recipe code or the refit code.")

    bare = refit.recipe_check(_recipe_package(tmp_path, "bare"))
    assert not bare["same"] and bare["notRecorded"][0] == "the curve engine"

    moved = refit.recipe_check(_recipe_package(tmp_path, "moved", engine=engine,
                                               code={**code, "refit.py": "0" * 64}))
    assert not moved["same"] and moved["differences"] == ["refit code"]
    assert refit.recipe_words(moved) == ("The refit is not expected to match exactly: the refit code here "
                                         "differs from what the package records.")
    both = {"same": False, "differences": ["curve engine", "refit code"], "notRecorded": ["the fit recipe code"]}
    assert refit.recipe_words(both) == ("The refit is not expected to match exactly: the curve engine and "
                                        "refit code here differ from what the package records; the "
                                        "package does not record the fit recipe code.")


def test_a_recorded_check_reads_the_same_with_or_without_its_duration():
    from views import easi_page as ep
    now = ep.check_lines({"panelsRegenerateMembers": {"identical": True, "memberRows": 282113}})
    assert now == ["Drawing the panels again from this package gives the same 282,113 member rows."]
    before = ep.check_lines({"panelsRegenerateMembers": {"identical": True, "memberRows": 7, "seconds": 18.7}})
    assert before == ["Drawing the panels again from this package gives the same 7 member rows (18.7 s)."]


# --------------------------------------------------------------------------- #
# the CSV export (a Shiny download inside the page's server, so read as source)
# --------------------------------------------------------------------------- #
def test_the_csv_export_hashes_its_file_against_the_verified_manifest_and_fails_loudly():
    src = (APP / "views" / "easi_page.py").read_text(encoding="utf-8")
    body = src[src.index("def pkg_csv"):]
    body = body[:re.search(r"\n    (?:@|def |# )", body[1:]).start() + 1]
    assert "evs.sha_file(path)" in body
    assert '_viewing.get("doc")' in body and "read_manifest" not in body
    assert "raise evs.EvidenceError" in body
    # the check comes before the first byte is sent
    assert body.index("raise evs.EvidenceError") < body.index("yield")


def test_an_import_waits_for_a_running_package_job_as_a_download_does():
    src = (APP / "views" / "easi_page.py").read_text(encoding="utf-8")
    for handler in ("def _pkg_import_apply", "def _pkg_download"):
        body = src[src.index(handler):]
        body = body[:re.search(r"\n    (?:@|def |# )", body[1:]).start() + 1]
        assert '_jobs.get("busy")' in body, handler
        assert body.index('_jobs.get("busy")') < body.index("_launch(")
