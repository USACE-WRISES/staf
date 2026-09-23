"""EASI method packages: the scoring method as a verifiable, portable file set.

A method package is what StreamCurves authors and publishes for EASI: the method
files EASI scores with, byte for byte, plus an envelope (``method.json``) that
names the method, its version and its identities::

    method.json
    method/screening-methods.json
    method/reference-curves.json
    ... (the eight METHOD_FILES)
    calculator/EASI_Calculator_<version>.xlsx   (optional, generated for this method)

Activation. ``EASI_METHOD_PACKAGE=<zip or folder>`` makes a process score with a
package: ``easi/__init__.py`` materializes it (verified, content addressed) and
points ``EASI_DATA_DIR`` at the result before ``easi.config`` reads it, so the
method digest (``easi.national.method_version``) is computed from the package's
bytes by the unchanged code. Unset, EASI uses its built-in ``data/`` folder,
which is the rollback. ``activate()`` switches the method inside a running
process for tests and single-method workers; one method is active per process.

Identities. ``method_version()`` stays the operational identity (criteria set +
evaluator sources + method files). ``package_digest`` covers the method files
only; ``evaluator_digest`` covers the evaluator sources only. A package records
the ``method_version`` it was validated under; activation recomputes it.

Nothing here imports ``easi.config`` at module import time: ``easi/__init__.py``
calls ``materialize_from_env`` before the config module resolves its data folder.
This module is outside the method digest by design (see ``easi.national``).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

SCHEMA = "staf-easi-method"
SCHEMA_VERSION = 1
#: The evaluator contract a package may rely on. Raised only when the meaning of a
#: package's data changes for the evaluator (a new operator, a new file).
ENGINE_API = 1
ENVELOPE = "method.json"
METHOD_DIR = "method"
CALCULATOR_DIR = "calculator"
ENV_PACKAGE = "EASI_METHOD_PACKAGE"
ENV_CACHE = "EASI_METHOD_CACHE"

#: The method files: the data files of ``method_version`` for the regional criteria.
METHOD_FILES = (
    "screening-methods.json",
    "reference-curves.json",
    "easi-metrics.json",
    "cwa-mapping.json",
    "functions.json",
    "ecoregion-crosswalk.json",
    "scoring-identity.json",
    "nars-ecoregions-9.geojson.gz",
)
#: Files the evaluator reads from its data folder that a package does not carry:
#: they belong to the app and its evaluator identity, not to the method.
EVALUATOR_ASSETS = (
    "physio_divisions.geojson",
    "ecoregions_l3.geojson",
    "nrsa-2018-19-evidence.json.gz",
    "screening-methods-legacy.json",
)
#: The files ``easi.national.method_version`` hashes, copied here so a package can be
#: resolved before ``easi.config`` is imported (``easi.national`` imports it).
#: ``tests/test_method_package.py`` keeps these equal to ``easi.national``'s.
DIGEST_SOURCES = ("scoring.py", "screening_methods.py", "config.py",
                  "watershed.py", "assessment.py", "bieger.py", "geo.py",
                  "datasources/fabric.py", "national/records.py")
DIGEST_DATA = ("easi-metrics.json",
               "cwa-mapping.json", "functions.json", "ecoregion-crosswalk.json",
               "reference-curves.json", "scoring-identity.json",
               "nars-ecoregions-9.geojson.gz")
CRITERIA_SETS = ("regional",)
MAX_FILE_BYTES = 64 * 1024 * 1024
_CALCULATOR_SUFFIX = ".xlsx"
#: A host app's copy of EASI (StreamCurves' ``_vendor/easi``) honors EASI_METHOD_PACKAGE
#: only in a process its host marked as an evaluation worker; the host's own process
#: stays on the built-in method it depends on.
WORKER_ENV = "STREAMCURVES_EASI_WORKER"

#: What this evaluator provides. A package lists what it requires (``requires`` in
#: method.json) and is refused when this evaluator lacks any of it.
#: ``tests/test_method_package.py`` keeps OPERATORS equal to ``screening_methods``'s.
OPERATORS = ("threshold", "ratio", "minimum", "minimum_of_products", "worst_index", "best_index",
             "weighted_capped_sum", "sum_capped", "categorical_lookup", "unscored")
STRATIFIERS = ("nars9", "slope_class")
SLOPE_CLASSES = ("lt_0.5", "0.5_to_2", "ge_2")
#: Scoring behavior that lives in the evaluator's code, not in the method files. A
#: package names the ones its ratings rely on; a later evaluator that changes one of
#: them changes this list, and a package relying on the old behavior is refused.
BEHAVIORS = (
    "curve-index-bands-0.39-0.69",      # Good >= 0.69, Fair >= 0.39 on the interpolated index
    "anchors-from-metric-midpoints",    # rating -> index from easi-metrics indexMidpoints
    "function-score-round-x15",         # function score = round(index x 15)
    "cwa-weights-D1-i0.1",              # outcome weights D = 1.0, i = 0.1, - = 0
    "eci-mean-of-available-subindices",
    "coverage-provisional-below-0.70",
    "composites-on-anchor-indices",     # min/max over anchors, first-in-order ties
    "curve-fallback-by-list",           # stratum, then the listed fallbacks (national)
    "twenty-methods",
)

#: Python files of the ``easi`` package that only present results (no effect on a rating
#: or its evidence), left out of the acquisition digest.
_PRESENTATION = ("__init__.py", "method_package.py", "methods.py", "method_plot.py", "report.py",
                 "reportmap.py", "snapcard.py", "viewport.py", "network_display.py", "notices.py",
                 "xsplot.py", "xsplotly.py", "batch_ui.py", "calculator.py")


class MethodPackageError(ValueError):
    """A package that cannot be used as it is. Never partly applied."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# the built-in method and the evaluator
# --------------------------------------------------------------------------- #
def package_root() -> Path:
    """The ``easi`` package folder (works in the repo and in a vendored copy)."""
    return Path(__file__).resolve().parent


def builtin_data_dir() -> Path:
    """The bundled data folder, resolved the way ``easi.config`` does without the
    environment override: in-package ``data/`` (vendored) then the sibling ``data/``."""
    in_pkg = package_root() / "data"
    if in_pkg.is_dir():
        return in_pkg
    return package_root().parent / "data"


def evaluator_sources() -> list[Path]:
    """The Python sources ``method_version`` hashes, in its order."""
    root = package_root()
    files = [root / name for name in DIGEST_SOURCES]
    files += sorted((root / "metrics").glob("*.py"))
    return files


def evaluator_digest() -> str:
    digest = hashlib.sha256()
    for path in evaluator_sources():
        digest.update(path.name.encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def package_digest(files: dict[str, str]) -> str:
    """Digest of the method files, ``{name: sha256}``; changes exactly when method data does."""
    return "sha256:" + _sha(canonical({"files": dict(sorted(files.items()))}))


def method_version_for(criteria_set: str, data: dict[str, bytes]) -> str:
    """``easi.national.method_version`` for a method file set under this evaluator,
    computed without activating it. Same algorithm, same order, same bytes."""
    digest = hashlib.sha256()
    digest.update(criteria_set.encode("utf-8") + b"\0")
    for path in evaluator_sources():
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    catalog = "screening-methods.json"
    for name in (catalog, *DIGEST_DATA):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        blob = data.get(name)
        digest.update(blob if blob is not None else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def acquisition_sources() -> list[Path]:
    """The code and bundled data that turn a site into evidence (adapters, geometry,
    delineation, routing, the vendored site engine) plus the evaluator assets: what a
    live result depends on besides ``method_version``."""
    root = package_root()
    digest_src = {(root / n).resolve() for n in DIGEST_SOURCES}
    metrics = {p.resolve() for p in (root / "metrics").glob("*.py")}
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel or p.resolve() in digest_src or p.resolve() in metrics:
            continue
        if "/" not in rel and rel in _PRESENTATION:
            continue
        if rel.startswith(("batch/", "national/dashboard", "national/tiles")):
            continue
        out.append(p)
    vend = root / "_vendor"
    if vend.is_dir():
        out += sorted(p for p in vend.rglob("*") if p.is_file() and p.suffix != ".pyc"
                      and "__pycache__" not in p.parts and p.suffix != ".py")
    data = builtin_data_dir()
    out += [data / n for n in EVALUATOR_ASSETS if (data / n).is_file()]
    return out


def acquisition_digest() -> str:
    """Identity of the evidence-acquisition code and assets (not in ``method_version``)."""
    root = package_root()
    digest = hashlib.sha256()
    for path in acquisition_sources():
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = "data/" + path.name
        digest.update(rel.encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _json(files: dict[str, bytes], name: str):
    return json.loads(files[name].decode("utf-8"))


def _nars9_codes(blob: bytes) -> set[str]:
    import gzip
    doc = json.loads(gzip.decompress(blob).decode("utf-8"))
    codes = set()
    for f in doc.get("features") or []:
        props = f.get("properties") or {}
        code = props.get("WSA_9") or props.get("wsa_9") or props.get("code")
        if code:
            codes.add(str(code))
    return codes


def requirements(files: dict[str, bytes]) -> dict:
    """What a method file set requires of its evaluator: the operators and stratifiers
    its catalog uses, and the evaluator behaviors every EASI method relies on."""
    cat = _json(files, "screening-methods.json")
    ops, strat = set(), set()

    def visit(rule):
        if not isinstance(rule, dict):
            return
        if rule.get("operator"):
            ops.add(rule["operator"])
        c = rule.get("curve")
        if isinstance(c, dict) and c.get("stratifier"):
            strat.add(c["stratifier"])
        for i in rule.get("inputs") or []:
            visit(i)
        for v in rule.get("variants") or []:
            visit(v)

    for m in cat.get("methods") or []:
        visit(m)
    return {"operators": sorted(ops), "stratifiers": sorted(strat), "behaviors": list(BEHAVIORS)}


def validate_files(files: dict[str, bytes]) -> list[str]:
    """Cross-file consistency of a method file set, without importing the evaluator.

    The catalog's own validator (``screening_methods.validate_catalog``) runs when the
    package is active (``verify_active``); these are the checks that span files."""
    problems: list[str] = []
    try:
        cat = _json(files, "screening-methods.json")
        curves = _json(files, "reference-curves.json")
        metrics = _json(files, "easi-metrics.json")
        cwa = _json(files, "cwa-mapping.json")
        ident = _json(files, "scoring-identity.json")
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        return [f"method file unreadable: {exc}"]
    methods = cat.get("methods") or []
    if len(methods) != 20:
        problems.append(f"expected 20 methods, found {len(methods)}")
    anchors = cat.get("ratingIndex") or {}
    for m in metrics.get("metrics") or []:
        mid = m.get("indexMidpoints") or {}
        if mid and any(abs(float(mid.get(k, -1)) - float(v)) > 1e-12 for k, v in anchors.items()):
            problems.append(f"{m.get('metricId')}: indexMidpoints differ from the catalog ratingIndex")
    for rating, a in anchors.items():
        frac = (float(a) * 15) % 1
        if abs(frac - 0.5) < 1e-9:
            problems.append(f"anchor {rating}={a} x 15 is a rounding tie")
    fids = {m.get("functionId") for m in metrics.get("metrics") or []}
    rows = {r.get("id"): r for r in cwa} if isinstance(cwa, list) else {}
    for fid in sorted(f for f in fids if f):
        row = rows.get(fid)
        if row is None:
            problems.append(f"cwa-mapping has no row for {fid}")
            continue
        for k in ("physical", "chemical", "biological"):
            if row.get(k) not in ("D", "i", "-"):
                problems.append(f"cwa-mapping {fid}.{k} must be D, i or -")
    sets = curves.get("sets") or {}
    try:
        nars = _nars9_codes(files["nars-ecoregions-9.geojson.gz"])
    except Exception as exc:  # noqa: BLE001
        nars = set()
        problems.append(f"NARS-9 geography unreadable: {exc}")
    if not nars:
        problems.append("NARS-9 geography names no region codes")
    allowed = {"nars9": nars, "slope_class": set(SLOPE_CLASSES)}
    for name, s in sets.items():
        keys = set((s.get("curves") or {}).keys())
        if "national" not in keys:
            problems.append(f"curve set {name} has no national curve")
        strat = s.get("stratifier")
        if strat not in allowed:
            problems.append(f"curve set {name}: unknown stratifier {strat!r}")
        elif allowed[strat] and not (keys - {"national"}) <= allowed[strat]:
            problems.append(f"curve set {name}: strata {sorted(keys - {'national'} - allowed[strat])} "
                            f"are not {strat} codes")
        for stratum, c in (s.get("curves") or {}).items():
            pts = (c or {}).get("points") or []
            if len(pts) < 2:
                problems.append(f"curve {name}/{stratum} has fewer than two knots")
            elif any(b[0] < a[0] for a, b in zip(pts, pts[1:])) or \
                    any(not 0.0 <= float(p[1]) <= 1.0 for p in pts):
                problems.append(f"curve {name}/{stratum} knots must increase in x with 0 <= y <= 1")

    def uses(rule):
        c = (rule or {}).get("curve")
        out = [c.get("set")] if isinstance(c, dict) and c.get("set") else []
        for i in (rule or {}).get("inputs") or []:
            out += uses(i)
        for v in (rule or {}).get("variants") or []:
            out += uses(v)
        return out

    for m in methods:
        for s in uses(m):
            if s not in sets:
                problems.append(f"{m.get('methodKey')}: curve set {s!r} is not in reference-curves.json")
    # scoring-identity.json names the exact files it identifies
    for key, name in (("catalog_sha256", "screening-methods.json"),
                      ("curves_sha256", "reference-curves.json"),
                      ("nars_geography_sha256", "nars-ecoregions-9.geojson.gz")):
        if ident.get(key) and ident[key] != _sha(files[name]):
            problems.append(f"scoring-identity.json {key} does not match {name}")
    n_curves = sum(len(s.get("curves") or {}) for s in sets.values())
    if ident.get("curve_count") is not None and ident["curve_count"] != n_curves:
        problems.append(f"scoring-identity.json curve_count {ident['curve_count']} != {n_curves}")
    return problems


def check_requirements(env: dict) -> None:
    req = (env.get("evaluator") or {}).get("requires") or {}
    missing_ops = sorted(set(req.get("operators") or []) - set(OPERATORS))
    missing_strat = sorted(set(req.get("stratifiers") or []) - set(STRATIFIERS))
    missing_beh = sorted(set(req.get("behaviors") or []) - set(BEHAVIORS))
    if missing_ops or missing_strat or missing_beh:
        raise MethodPackageError(
            "this method package needs evaluator capabilities this EASI does not provide: "
            + "; ".join(x for x in (f"operators {missing_ops}" if missing_ops else "",
                                    f"stratifiers {missing_strat}" if missing_strat else "",
                                    f"behaviors {missing_beh}" if missing_beh else "") if x))


# --------------------------------------------------------------------------- #
# the package
# --------------------------------------------------------------------------- #
@dataclass
class MethodPackage:
    envelope: dict
    files: dict[str, bytes]
    calculator: Optional[tuple[str, bytes]] = None
    source: str = ""

    @property
    def criteria_set(self) -> str:
        return str(self.envelope.get("criteriaSet") or "regional")

    @property
    def identity(self) -> dict:
        return dict(self.envelope.get("identity") or {})

    @property
    def digest(self) -> str:
        return package_digest({n: _sha(b) for n, b in self.files.items()})

    def file_records(self) -> dict[str, dict]:
        return {n: {"bytes": len(b), "sha256": _sha(b)} for n, b in sorted(self.files.items())}


def build_envelope(files: dict[str, bytes], *, method_id: str, version: int, label: str,
                   status: str = "draft", criteria_set: str = "regional",
                   calculator: Optional[tuple[str, bytes]] = None,
                   extra: Optional[dict] = None) -> dict:
    """The ``method.json`` for a file set, with identities computed here."""
    if criteria_set not in CRITERIA_SETS:
        raise MethodPackageError(f"criteria set {criteria_set!r} is not supported by packages")
    missing = [n for n in METHOD_FILES if n not in files]
    if missing:
        raise MethodPackageError(f"method files missing: {missing}")
    recs = {n: {"bytes": len(files[n]), "sha256": _sha(files[n])} for n in METHOD_FILES}
    scoring_identity = {}
    try:
        scoring_identity = json.loads(files["scoring-identity.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
    problems = validate_files(files)
    if problems:
        raise MethodPackageError("method files are not consistent: " + "; ".join(problems[:6]))
    mv, ev = method_version_for(criteria_set, files), evaluator_digest()
    env = {
        "schema": SCHEMA,
        "schemaVersion": SCHEMA_VERSION,
        "methodId": method_id,
        "version": int(version),
        "status": status,
        "label": label,
        "criteriaSet": criteria_set,
        "identity": {
            "methodVersion": mv,
            "packageDigest": package_digest({n: r["sha256"] for n, r in recs.items()}),
            "evaluatorDigest": ev,
            # every evaluator this file set was validated under, with the method version
            # it gives there (a later evaluator adds a row; none is ever rewritten)
            "validatedUnder": [{"evaluatorDigest": ev, "methodVersion": mv}],
            "scoringIdentity": {k: scoring_identity.get(k) for k in
                                ("alternative_id", "alternative_name", "curve_count")
                                if k in scoring_identity},
        },
        "evaluator": {"app": "easi", "engineApi": ENGINE_API, "requires": requirements(files)},
        "files": recs,
    }
    if calculator is not None:
        name, blob = calculator
        env["calculator"] = {"name": name, "bytes": len(blob), "sha256": _sha(blob)}
    if extra:
        env.update({k: v for k, v in extra.items() if k not in env})
    return env


def to_zip(pkg: MethodPackage) -> bytes:
    """Deterministic zip bytes: sorted names, fixed timestamps, stored JSON envelope."""
    buf = io.BytesIO()
    entries = [(ENVELOPE, json.dumps(pkg.envelope, indent=1, sort_keys=True).encode("utf-8") + b"\n")]
    entries += [(f"{METHOD_DIR}/{n}", b) for n, b in sorted(pkg.files.items())]
    if pkg.calculator is not None:
        entries.append((f"{CALCULATOR_DIR}/{pkg.calculator[0]}", pkg.calculator[1]))
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def _allowed_name(name: str) -> bool:
    if name == ENVELOPE:
        return True
    if "\\" in name or name.startswith("/") or ".." in name.split("/"):
        return False
    parts = name.split("/")
    if len(parts) != 2:
        return False
    folder, leaf = parts
    if folder == METHOD_DIR:
        return leaf in METHOD_FILES
    if folder == CALCULATOR_DIR:
        return leaf.startswith("EASI_Calculator_") and leaf.endswith(_CALCULATOR_SUFFIX)
    return False


def _read_entries(source: Union[str, Path, bytes]) -> tuple[dict[str, bytes], str]:
    if isinstance(source, (bytes, bytearray)):
        return _read_zip(bytes(source)), "<bytes>"
    path = Path(source)
    if path.is_dir():
        out: dict[str, bytes] = {}
        for p in sorted(path.rglob("*")):
            if p.is_file():
                rel = p.relative_to(path).as_posix()
                if not _allowed_name(rel):
                    raise MethodPackageError(f"unexpected file in method package: {rel}")
                if p.stat().st_size > MAX_FILE_BYTES:
                    raise MethodPackageError(f"file too large: {rel}")
                out[rel] = p.read_bytes()
        return out, str(path)
    if path.is_file():
        return _read_zip(path.read_bytes()), str(path)
    raise MethodPackageError(f"method package not found: {path}")


def _read_zip(blob: bytes) -> dict[str, bytes]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise MethodPackageError(f"not a method package (bad zip): {exc}") from exc
    out: dict[str, bytes] = {}
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not _allowed_name(info.filename):
                raise MethodPackageError(f"unexpected entry in method package: {info.filename!r}")
            if info.file_size > MAX_FILE_BYTES:
                raise MethodPackageError(f"entry too large: {info.filename}")
            try:
                out[info.filename] = zf.read(info)
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise MethodPackageError(f"corrupt entry {info.filename}: {exc}") from exc
    return out


def read_package(source: Union[str, Path, bytes]) -> MethodPackage:
    """Read and verify a package (zip bytes, zip file or folder). Raises on any problem."""
    entries, where = _read_entries(source)
    if ENVELOPE not in entries:
        raise MethodPackageError("method package has no method.json")
    try:
        env = json.loads(entries[ENVELOPE].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise MethodPackageError(f"method.json is not valid JSON: {exc}") from exc
    if env.get("schema") != SCHEMA:
        raise MethodPackageError(f"not an EASI method package (schema {env.get('schema')!r})")
    sv = env.get("schemaVersion")
    if not isinstance(sv, int) or sv < 1:
        raise MethodPackageError("method.json has no valid schemaVersion")
    if sv > SCHEMA_VERSION:
        raise MethodPackageError(f"this method package needs a newer EASI (package schema {sv}, "
                                 f"this EASI reads {SCHEMA_VERSION})")
    api = (env.get("evaluator") or {}).get("engineApi")
    if not isinstance(api, int) or api > ENGINE_API:
        raise MethodPackageError(f"this method package needs a newer EASI evaluator "
                                 f"(engine API {api}, this EASI provides {ENGINE_API})")
    if (env.get("evaluator") or {}).get("app") != "easi":
        raise MethodPackageError("method package is not for EASI")
    crit = env.get("criteriaSet") or "regional"
    if crit not in CRITERIA_SETS:
        raise MethodPackageError(f"criteria set {crit!r} is not supported by packages")
    declared = env.get("files") or {}
    if set(declared) != set(METHOD_FILES):
        raise MethodPackageError(f"method.json must list exactly the method files {list(METHOD_FILES)}")
    files: dict[str, bytes] = {}
    for name in METHOD_FILES:
        blob = entries.get(f"{METHOD_DIR}/{name}")
        rec = declared.get(name) or {}
        if blob is None:
            raise MethodPackageError(f"method file missing from the package: {name}")
        if len(blob) != rec.get("bytes") or _sha(blob) != rec.get("sha256"):
            raise MethodPackageError(f"method file does not match method.json (size or sha256): {name}")
        files[name] = blob
    calculator = None
    calc_entries = [(n, b) for n, b in entries.items() if n.startswith(CALCULATOR_DIR + "/")]
    if calc_entries:
        if len(calc_entries) > 1:
            raise MethodPackageError("a method package carries at most one calculator")
        rec = env.get("calculator") or {}
        name, blob = calc_entries[0]
        leaf = name.split("/", 1)[1]
        if rec.get("name") != leaf or rec.get("bytes") != len(blob) or rec.get("sha256") != _sha(blob):
            raise MethodPackageError("calculator does not match method.json")
        calculator = (leaf, blob)
    elif env.get("calculator"):
        raise MethodPackageError("method.json names a calculator the package does not carry")
    ident = env.get("identity") or {}
    pdig = package_digest({n: _sha(b) for n, b in files.items()})
    if ident.get("packageDigest") != pdig:
        raise MethodPackageError("method.json packageDigest does not match the method files")
    check_requirements(env)
    problems = validate_files(files)
    if problems:
        raise MethodPackageError("method files are not consistent: " + "; ".join(problems[:6]))
    return MethodPackage(envelope=env, files=files, calculator=calculator, source=where)


def check_identity(pkg: MethodPackage) -> dict:
    """Recompute ``method_version`` under this evaluator and compare with the envelope.

    Same evaluator digest and a different method version is a corrupt package
    (refused). A different evaluator digest is reported: the package was
    validated under another EASI evaluator, and its results here carry this
    evaluator's method version.
    """
    here = method_version_for(pkg.criteria_set, pkg.files)
    ident = pkg.identity
    ev = evaluator_digest()
    rows = list(ident.get("validatedUnder") or [])
    if ident.get("evaluatorDigest"):
        rows.append({"evaluatorDigest": ident["evaluatorDigest"],
                     "methodVersion": ident.get("methodVersion")})
    known = [r for r in rows if r.get("evaluatorDigest") == ev]
    if any(r.get("methodVersion") != here for r in known):
        raise MethodPackageError("method.json methodVersion does not match its files under the "
                                 "evaluator it names")
    return {"methodVersion": here, "recordedMethodVersion": ident.get("methodVersion"),
            "sameEvaluator": bool(known), "packageDigest": pkg.digest}


# --------------------------------------------------------------------------- #
# materialization and activation
# --------------------------------------------------------------------------- #
def cache_root() -> Path:
    env = os.environ.get(ENV_CACHE)
    return Path(env) if env else Path(tempfile.gettempdir()) / "easi-methods"


def _verify_dir(target: Path, pkg: MethodPackage, assets: dict[str, str]) -> bool:
    try:
        for name, blob in pkg.files.items():
            if (target / name).read_bytes() != blob:
                return False
        for name, sha in assets.items():
            if _sha((target / name).read_bytes()) != sha:
                return False
        return True
    except OSError:
        return False


def materialize(source: Union[str, Path, bytes, MethodPackage]) -> tuple[Path, MethodPackage]:
    """A verified data folder for a package: its method files plus this app's evaluator
    assets, content addressed under ``cache_root()``. Reused when already verified."""
    pkg = source if isinstance(source, MethodPackage) else read_package(source)
    check_identity(pkg)
    builtin = builtin_data_dir()
    assets = {}
    for name in EVALUATOR_ASSETS:
        p = builtin / name
        if p.is_file():
            assets[name] = _sha(p.read_bytes())
    key = _sha(canonical({"package": pkg.digest, "assets": assets}))[:24]
    final = cache_root() / key
    target = final / "data"
    if target.is_dir():
        if _verify_dir(target, pkg, assets):
            return target, pkg
        # A damaged copy is moved aside, never deleted in place under another reader.
        aside = final.with_name(f"{final.name}.damaged-{os.getpid()}")
        try:
            os.replace(final, aside)
            shutil.rmtree(aside, ignore_errors=True)
        except OSError:
            pass
    tmp = Path(tempfile.mkdtemp(prefix="easi-method-", dir=str(_ensure(cache_root()))))
    data = tmp / "data"
    data.mkdir()
    for name, blob in pkg.files.items():
        (data / name).write_bytes(blob)
    for name in assets:
        shutil.copyfile(builtin / name, data / name)
    if pkg.calculator is not None:
        (tmp / CALCULATOR_DIR).mkdir()
        (tmp / CALCULATOR_DIR / pkg.calculator[0]).write_bytes(pkg.calculator[1])
    (tmp / ENVELOPE).write_bytes(
        json.dumps(pkg.envelope, indent=1, sort_keys=True).encode("utf-8") + b"\n")
    try:
        os.replace(tmp, final)
    except OSError:
        # Another process materialized the same package first; use its copy.
        shutil.rmtree(tmp, ignore_errors=True)
    if not _verify_dir(target, pkg, assets):
        raise MethodPackageError("materialized method folder failed verification")
    return target, pkg


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


#: The package this process scores with, set by ``materialize_from_env`` or ``activate``.
_ACTIVE: dict = {}


def is_vendored() -> bool:
    """True in a host app's copy of EASI (for example ``streamcurves._vendor.easi``)."""
    return not __name__.startswith("easi.")


def materialize_from_env() -> None:
    """Called by ``easi/__init__.py`` before ``easi.config`` is imported.

    A vendored copy honors the package only in a process its host marked as an
    evaluation worker (``WORKER_ENV=1``): the host's own process keeps the built-in
    method its other work depends on (StreamCurves' DEEP builds read it)."""
    source = os.environ.get(ENV_PACKAGE)
    if not source:
        return
    if is_vendored() and os.environ.get(WORKER_ENV) != "1":
        return
    data_dir, pkg = materialize(source)
    existing = os.environ.get("EASI_DATA_DIR")
    if existing and Path(existing).resolve() != data_dir.resolve():
        raise MethodPackageError("EASI_METHOD_PACKAGE and EASI_DATA_DIR are both set; unset one")
    crit = os.environ.get("EASI_CRITERIA_SET")
    if crit and crit != pkg.criteria_set:
        raise MethodPackageError(f"EASI_CRITERIA_SET={crit!r} does not match the method package "
                                 f"({pkg.criteria_set!r})")
    os.environ["EASI_DATA_DIR"] = str(data_dir)
    os.environ["EASI_CRITERIA_SET"] = pkg.criteria_set
    _ACTIVE.clear()
    _ACTIVE.update(_active_record(pkg, data_dir))


def _active_record(pkg: MethodPackage, data_dir: Path) -> dict:
    env = pkg.envelope
    calc = (data_dir.parent / CALCULATOR_DIR / pkg.calculator[0]) if pkg.calculator else None
    return {"source": "package", "methodId": env.get("methodId"), "version": env.get("version"),
            "status": env.get("status"), "label": env.get("label"),
            "packageDigest": pkg.digest, "recordedMethodVersion": pkg.identity.get("methodVersion"),
            "recordedEvaluatorDigest": pkg.identity.get("evaluatorDigest"),
            "dataDir": str(data_dir), "calculator": str(calc) if calc else None}


def active() -> dict:
    """What this process scores with: the package record, or the built-in method."""
    return dict(_ACTIVE) if _ACTIVE else {"source": "built-in", "dataDir": str(builtin_data_dir())}


def active_identity() -> dict:
    """The active method's identities (imports config; call after startup)."""
    from . import config
    from .national import method_version
    rec = active()
    files = {}
    data_dir = Path(config.DATA_DIR)
    for name in METHOD_FILES:
        p = data_dir / name
        if p.is_file():
            files[name] = _sha(p.read_bytes())
    ident = config.scoring_identity() or {}
    return {**rec, "methodVersion": method_version(), "criteriaSet": config.criteria_set(),
            "packageDigest": package_digest(files), "evaluatorDigest": evaluator_digest(),
            "acquisitionDigest": _cached_acquisition_digest(),
            "alternativeId": ident.get("alternative_id"),
            "alternativeName": ident.get("alternative_name")}


_ACQ: dict = {}


def _cached_acquisition_digest() -> str:
    """The acquisition digest, computed once per process (it reads the whole package)."""
    if "v" not in _ACQ:
        _ACQ["v"] = acquisition_digest()
    return _ACQ["v"]


def verify_active() -> dict:
    """Startup check: the active catalog validates, and for a package the method
    version recomputed from the active files is the one the package records
    (when this is the evaluator it was validated under)."""
    from . import screening_methods as sm
    problems = sm.validate_catalog()
    if problems:
        raise MethodPackageError("method catalog failed validation: " + "; ".join(problems[:5]))
    ident = active_identity()
    rec = active()
    if rec.get("source") == "package":
        same_eval = rec.get("recordedEvaluatorDigest") == ident["evaluatorDigest"]
        ident["sameEvaluator"] = same_eval
        if same_eval and rec.get("recordedMethodVersion") != ident["methodVersion"]:
            raise MethodPackageError("the active method version differs from the package record")
        if ident["packageDigest"] != rec.get("packageDigest"):
            raise MethodPackageError("the active method files differ from the package")
    return ident


def calculator_path() -> Optional[Path]:
    """The calculator workbook for the active method: the package's own, the built-in
    workbook when the active files are the built-in method, else None (no workbook
    may be served for a method it was not generated from)."""
    rec = active()
    if rec.get("source") != "package":
        return None  # the caller keeps its built-in template
    if rec.get("calculator"):
        return Path(rec["calculator"])
    return None


def _reset_all_caches() -> None:
    from . import bieger, calculator, config, geo, methods
    from . import screening_methods as sm
    from .datasources import nrsa
    from .national import method_version
    config._load.cache_clear()
    methods._catalog_methods.cache_clear()
    methods._catalog_variants.cache_clear()
    sm.curve_sets.cache_clear()
    method_version.cache_clear()
    for fn in (getattr(geo, "_index", None), getattr(geo, "_nars9_names", None),
               getattr(bieger, "_divisions", None), getattr(nrsa, "_records", None),
               getattr(nrsa, "comid_by_site_id", None), getattr(calculator, "blank_bytes", None),
               getattr(calculator, "entry_cells", None)):
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


def _point_modules_at(data_dir: Path) -> None:
    from . import bieger, config, geo
    from .datasources import nrsa
    config.DATA_DIR = data_dir
    geo.DATA_DIR = data_dir
    geo.ECOREGIONS_PATH = data_dir / "ecoregions_l3.geojson"
    geo.NARS9_PATH = data_dir / "nars-ecoregions-9.geojson.gz"
    bieger._GEOJSON = data_dir / "physio_divisions.geojson"
    nrsa.DATA_PATH = data_dir / "nrsa-2018-19-evidence.json.gz"


def activate(source: Union[str, Path, bytes, MethodPackage, None]) -> dict:
    """Switch the method inside this process: for tests and for a worker that scores
    one method. Production switching is by process (``EASI_METHOD_PACKAGE`` at start).

    ``None`` returns to the built-in method. Every method cache is cleared, every
    module path that derives from the data folder is repointed, and the method
    version recomputed from the active files is returned with the identity.
    """
    if source is None:
        data_dir = builtin_data_dir()
        os.environ.pop("EASI_DATA_DIR", None)
        _ACTIVE.clear()
    else:
        data_dir, pkg = materialize(source)
        os.environ["EASI_DATA_DIR"] = str(data_dir)
        os.environ["EASI_CRITERIA_SET"] = pkg.criteria_set
        _ACTIVE.clear()
        _ACTIVE.update(_active_record(pkg, data_dir))
    _point_modules_at(data_dir)
    _reset_all_caches()
    ident = active_identity()
    if source is not None:
        rec = _ACTIVE
        if ident["packageDigest"] != rec["packageDigest"]:
            raise MethodPackageError("activation did not take effect (package digest differs)")
    return ident


def package_from_dir(data_dir: Union[str, Path], *, method_id: str = "easi-screening",
                     version: int = 1, label: str = "", status: str = "draft",
                     calculator: Optional[Union[str, Path]] = None) -> MethodPackage:
    """A package of the method files in a data folder, byte for byte (the importer's
    first step, and how tests build the built-in package)."""
    data_dir = Path(data_dir)
    files = {name: (data_dir / name).read_bytes() for name in METHOD_FILES}
    calc = None
    if calculator is not None:
        cpath = Path(calculator)
        calc = (cpath.name, cpath.read_bytes())
    ident = {}
    try:
        ident = json.loads(files["scoring-identity.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
    env = build_envelope(files, method_id=method_id, version=version,
                         label=label or ident.get("alternative_name") or "EASI screening method",
                         status=status, calculator=calc)
    return MethodPackage(envelope=env, files=files, calculator=calc, source=str(data_dir))
