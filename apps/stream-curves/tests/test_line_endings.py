"""Every file StreamCurves fingerprints over its raw bytes hashes the same in every copy.

A run manifest fingerprints files over their raw bytes (methodology.file_fingerprints and
config_fingerprints, the reference inputs, the vendored engines' identity) and folds most of them
into the run's inputsDigest; data/nrsa_provenance.json and data/nrsa/manifest.json record
byte-exact digests and sizes. Git stores text as LF and checks it out LF (.gitattributes,
`eol=lf`), but a file written on Windows with platform newlines is CRLF in that working copy
only, so that checkout silently hashes it differently from a clone, CI or the desktop payload.

So each such file is either LF in the working copy, or pinned in .gitattributes (`-text`) with
the bytes its record holds committed as they are (the legacy NRSA CSVs, two NRSA archive files
and the stratifier registry were recorded as CRLF).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from streamcurves import decisions, fixed_criteria, methodology, reference_pool, scale_analysis
from streamcurves import stratifiers
from streamcurves.paths import DATA_DIR

APP_DIR = Path(__file__).resolve().parents[1]
REPO = APP_DIR.parents[1]
PKG = APP_DIR / "streamcurves"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="reads .gitattributes through git; needs a STAF checkout")


def _pinned(paths: list[Path]) -> dict[Path, bool]:
    """{path: True when .gitattributes pins it `-text`}, from one `git check-attr` call."""
    rels = [p.relative_to(REPO).as_posix() for p in paths]
    out = subprocess.run(["git", "check-attr", "text", "--", *rels], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    pinned = {}
    for line in out.splitlines():
        rel, _attr, value = line.rsplit(": ", 2)
        pinned[(REPO / rel).resolve()] = value.strip() == "unset"
    return pinned


def _crlf_and_not_pinned(paths) -> list[str]:
    paths = sorted({Path(p).resolve() for p in paths if Path(p).is_file()})
    pinned = _pinned(paths)
    out = []
    for p in paths:
        data = p.read_bytes()
        if b"\0" in data[:8000]:
            continue                       # binary (parquet, xlsx): git never converts it
        if b"\r" in data and not pinned[p]:
            out.append(p.relative_to(REPO).as_posix())
    return out


def _provenance_paths(folder: str) -> set[Path]:
    """The files provenance.build_run_manifest fingerprints under app_root/<folder>."""
    src = (PKG / "provenance.py").read_text(encoding="utf-8")
    # a file directly under the folder: the segment is not followed by another `/ "..."`
    names = re.findall(r'app_root\s*/\s*"' + folder + r'"\s*/\s*"([^"]+)"(?!\s*/)', src)
    return {APP_DIR / folder / n for n in names}


def fingerprinted() -> set[Path]:
    """Every file a run manifest hashes over its raw bytes."""
    return (_provenance_paths("config") | _provenance_paths("data") | {
        methodology.CONFIG_PATH, methodology.RULE_CATALOG_PATH, decisions.POLICY_PATH,
        reference_pool.TRANSFER_CONFIG_PATH, scale_analysis.REGISTRY_PATH,
        fixed_criteria.FIXED_CRITERIA_PATH, fixed_criteria.VENDORED_CATALOG_PATH,
        stratifiers.REGISTRY_PATH,
        Path(DATA_DIR) / "nrsa" / "manifest.json",
        PKG / "_vendor" / "easi" / "VENDOR_INFO.json",
        PKG / "_vendor" / "site_engine" / "VENDOR_INFO.json",
    })


def recorded() -> set[Path]:
    """Every file data/nrsa_provenance.json, the NRSA archive manifest or the SQT registry
    records by its bytes (the registry pins each adapted SQT bundle it read)."""
    data = Path(DATA_DIR)
    prov = json.loads((data / "nrsa_provenance.json").read_text(encoding="utf-8"))
    out = {data / f["file"] for f in prov["files"]}
    manifest = json.loads((data / "nrsa" / "manifest.json").read_text(encoding="utf-8"))
    out |= {data / "nrsa" / rel for rel in manifest["files"]}
    registry = json.loads((data / "sqt" / "registry.json").read_text(encoding="utf-8"))
    return out | {REPO / b["path"] for b in registry["inputs"]["adaptedBundles"]}


def test_the_library_and_the_deep_bake_write_lf_on_every_platform(tmp_path):
    """A version the library writes on Windows is byte for byte what git stores, so the SQT
    registry's digests of it hold in every copy (2026-09-24: the SQT v2 publish left CRLF
    working copies that the registry then recorded)."""
    from streamcurves import library as lib
    lib._write_json(tmp_path / "a.json", {"lines": [1, 2]})
    data = (tmp_path / "a.json").read_bytes()
    assert b"\n" in data and b"\r" not in data
    src = (PKG / "library.py").read_text(encoding="utf-8")
    assert 'session_io.dumps_session(session_payload), encoding="utf-8", newline="\\n"' in src
    # the bake imports the DEEP package at import time, so its writer is read, not run
    bake = (REPO / "apps" / "deep" / "scripts" / "bake_library_into_deep.py").read_text(
        encoding="utf-8")
    body = bake[bake.index("def _write(path: Path, obj) -> None:"):]
    assert 'newline="\\n"' in body[:body.index("\ndef ")]


_HOW = ("is CRLF in this checkout but LF in git, so this checkout fingerprints it differently "
        "from every clone, CI run and desktop payload. Rewrite it LF (`git checkout -- <path>` "
        "when only the line endings differ; writers pass newline='\\n'), or, when a record "
        "already holds its CRLF bytes, pin it `-text` in .gitattributes.")


def test_the_fingerprinted_set_resolves():
    names = {p.name for p in fingerprinted()}
    assert {"metric_map.yaml", "national_stratifier_registry.yaml", "nrsa_sites.csv",
            "methodology_config.yaml", "standing_decisions.yaml", "manifest.json"} <= names
    assert all(p.is_file() for p in fingerprinted()), [
        str(p) for p in fingerprinted() if not p.is_file()]


def test_every_fingerprinted_file_is_lf_or_pinned():
    offenders = _crlf_and_not_pinned(fingerprinted())
    assert not offenders, f"{offenders} {_HOW}"


def test_every_recorded_text_file_is_lf_or_pinned():
    offenders = _crlf_and_not_pinned(recorded())
    assert not offenders, f"{offenders} {_HOW}"


def test_the_crlf_pins_are_the_files_recorded_as_crlf():
    """A pin is a deliberate record of CRLF bytes, never a way to silence the checks above.
    (Binary files such as the .gz stations layer are `-text` too, through `binary`.)"""
    text_files = [p.resolve() for p in fingerprinted() | recorded()
                  if p.is_file() and b"\0" not in p.read_bytes()[:8000]]
    pinned = sorted(p.relative_to(REPO).as_posix()
                    for p, is_pinned in _pinned(text_files).items() if is_pinned)
    assert pinned == [
        "apps/stream-curves/config/national_stratifier_registry.yaml",
        "apps/stream-curves/data/nrsa/sources.lock.json",
        "apps/stream-curves/data/nrsa/station_screen.meta.json",
        "apps/stream-curves/data/nrsa/verification_report.md",
        "apps/stream-curves/data/nrsa_metric_catalog.csv",
        "apps/stream-curves/data/nrsa_sites.csv",
        "apps/stream-curves/streamcurves/_vendor/easi/data/screening-methods.json",
    ]
