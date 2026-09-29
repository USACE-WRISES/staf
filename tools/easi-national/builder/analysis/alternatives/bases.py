"""The base method an alternatives study starts from: a registry, one entry per base.

A study compares candidate methods with a base, the operational EASI method its snapshot
preserves. The 2026-09-15 study started from Alternative 1 (method e9f472b31fe5, commit
6cc77c2, curves ad100b39); the owner then adopted its Alternative 2 (method b2e3033116e3,
commit 02f39a8, curves a824e2c2), so the next studies start there. A study manifest names
its base as ``base_id``; a manifest without one is a 1.0.0 study on the 2026-09-15 base,
whose constants (``BASE_METHOD``, ``BASE_COMMIT``, ``BASE_REFERENCE`` in the package) keep
their values, so that study's receipts and completion record read exactly as before.

Every value here was verified on 2026-09-25 against ``git log`` and the frozen identity
records the entry names; a value that could not be verified is None with a note, never a
guess.

Study ids: the 2026-09-15 study is ``2026-09-15-controlled-alternatives``. A new study on
the adopted base is named ``2026-10-<date>-<family>-alternatives`` (the campaign plan's
Round 4 step 4: one candidate family per study, ``<date>`` the day of October 2026 the
study is created, for example ``2026-10-03-low-flow-alternatives``), an immediate child of
``review/alternative-studies``. ``STUDY_ID_PATTERN`` is that form with any ISO date (the
plan named October as the first month). No study on the adopted base has been run.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Base:
    id: str
    label: str
    method_version: str            # easi.national.method_version() of the base
    commit: str                    # the commit whose apps/easi/data holds the base
    reference_sha256: str          # apps/easi/data/reference-curves.json at that commit
    catalog_sha256: Optional[str]  # apps/easi/data/screening-methods.json at that commit
    alternative_id: str            # the scoring identity the base carries
    curve_count: int
    package_digest: Optional[str]  # the EASI method package digest, where one exists
    library_version: Optional[str]  # the assessment library version holding it
    adopted: Optional[str]         # the owner's adoption date
    verified: tuple                # where each value was checked
    #: method versions of the same method files under evaluators the library validated
    #: later (a static record; the library's own validatedUnder rows are read beside it)
    validated_method_versions: tuple = ()

    def record(self) -> dict:
        """The base as a study manifest records it."""
        return asdict(self)


def library_validated_versions(base: Base, repo_root) -> tuple:
    """The method versions the assessment library records for the base's method files
    under every evaluator it validated them with: the ``identity.validatedUnder`` rows of
    the version ``base.library_version`` names (``<id> v<N>``), read only when that
    version's package digest is the base's. ``()`` for a base without a library version,
    without a repo root, or whose library version cannot be read."""
    if repo_root is None or not base.library_version or not base.package_digest:
        return ()
    try:
        assessment_id, version = str(base.library_version).split()
        path = (Path(repo_root) / "apps" / "library" / "assessments" / assessment_id / version
                / "method.json")
        identity = json.loads(path.read_text(encoding="utf-8")).get("identity") or {}
    except (OSError, ValueError, AttributeError):
        return ()
    if identity.get("packageDigest") != base.package_digest:
        return ()
    return tuple(dict.fromkeys(str(r["methodVersion"]) for r in identity.get("validatedUnder") or []
                               if isinstance(r, dict) and r.get("methodVersion")))


def accepted_method_versions(base: Base, repo_root=None) -> tuple:
    """Every method version that is this base: its own, the ones it records statically, and
    the ones the library validated its method files under (``library_validated_versions``)."""
    return tuple(dict.fromkeys((base.method_version, *base.validated_method_versions,
                                *library_validated_versions(base, repo_root))))


def accepts(base: Base, method_version, repo_root=None) -> bool:
    """True when ``method_version`` (the running evaluator's, or a completion's) is the
    base's method under an evaluator the library validated; a study on the base then
    records the evaluator it ran under beside the base."""
    return method_version is not None and str(method_version) in accepted_method_versions(base, repo_root)


LEGACY_STUDY_ID = "2026-09-15-controlled-alternatives"
DEFAULT_BASE_ID = "2026-09-15"
STUDY_ID_PATTERN = re.compile(r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])-[a-z0-9]+(?:-[a-z0-9]+)*-alternatives$")

BASES: dict[str, Base] = {
    DEFAULT_BASE_ID: Base(
        id=DEFAULT_BASE_ID,
        label="Alternative 1: the regional method the 2026-09-15 study started from",
        method_version="e9f472b31fe5",
        commit="6cc77c2c3fbf94d7dc9daf71df9dd3a07b56aaa7",
        reference_sha256="ad100b39313af9259bd0628728d50ab87513e2c07955bf24d78e9d107863abdd",
        catalog_sha256="ba169b20b7fc57c02a2b065298bd0b26df415094b5705470fc4fa3fab01ab28f",
        alternative_id="alternative-1",
        curve_count=62,
        package_digest=None,
        library_version=None,
        adopted=None,
        verified=(
            "method_version, commit and reference_sha256: the study manifest's alternative_1 "
            "and parent_binding blocks (2026-09-15-controlled-alternatives/manifest.json) and "
            "the package constants they were written from",
            "commit: git log 6cc77c2 (2026-09-15, 'easi: preserve composite classes in "
            "continuous diagnostics')",
            "reference_sha256 and catalog_sha256: sha256 of the study snapshot's app-data "
            "reference-curves.json and screening-methods.json; the same values are "
            "BASE_CURVES_SHA and BASE_CATALOG_SHA in apps/easi/scripts/promote_alternative_2.py",
            "package_digest and library_version: none; the method package format postdates "
            "this base and it was never published to the assessment library",
        ),
    ),
    "alternative-2-b2e3033116e3": Base(
        id="alternative-2-b2e3033116e3",
        label="Alternative 2: NARS-9 references, the operational method since 2026-09-16 "
              "(EASI 1.0.0; assessment library easi-screening v1)",
        method_version="b2e3033116e3",
        commit="02f39a8ccbe99de81d0aaa1a2da786b24669b555",
        reference_sha256="a824e2c254dea1c22af62d2a6f5fd3d0862ff0574190111655aa5b34dbce4887",
        catalog_sha256="78c1e2921198905ee6e53f18147e2aa33f9a6ffd87ff3e7a23844238b3fb73f3",
        alternative_id="alternative-2",
        curve_count=34,
        package_digest="sha256:5b733a6b7690f7893c6c15357178128a04646595e5d2cff7f8bb69fdbf40246d",
        library_version="easi-screening v1",
        adopted="2026-09-16",
        verified=(
            "method_version, package_digest: the campaign's frozen identities "
            "(D:/Data/staf-campaign-2026-09/baseline/identities_easi.json) and the library "
            "version's method.json (apps/library/assessments/easi-screening/v1)",
            "commit: git log 02f39a8 (2026-09-16, 'Adopt frozen NARS-9 scoring and add "
            "nationwide compatibility map'), the adoption recorded in "
            "streamcurves/easi_method/alternatives.py",
            "reference_sha256: sha256 of apps/easi/data/reference-curves.json, of the study's "
            "candidates/alternative-2/app-data/reference-curves.json (completion.json "
            "output_hashes) and of the library version's method file",
            "catalog_sha256: sha256 of apps/easi/data/screening-methods.json (the study's "
            "alternative-2 catalog 64c0a49d with its four 'Level II' prose strings promoted "
            "to 'NARS-9', apps/easi/data/source/alternative-2-promotion.json)",
        ),
    ),
    "alternative-2-e5b-e91118cc821c": Base(
        id="alternative-2-e5b-e91118cc821c",
        label="Alternative 2 with corrected cross-section quality checks (E5b), the operational "
              "method since 2026-09-29 (assessment library easi-screening v2)",
        method_version="e91118cc821c",
        commit="b4fe763ef083d1002b2713dc38658057308f759d",
        reference_sha256="a824e2c254dea1c22af62d2a6f5fd3d0862ff0574190111655aa5b34dbce4887",
        catalog_sha256="e754bf570291e0231d11e127e594a7fa375b3f227ee2f0d6591c45f9faa4ac49",
        alternative_id="alternative-2-e5b",
        curve_count=34,
        package_digest="sha256:7e44b19d4ad8415da0e0216f19c9d03a9e5bd021136439f8c43931e0fc44933d",
        library_version="easi-screening v2",
        adopted="2026-09-29",
        verified=(
            "method_version, package_digest: the library version's method.json "
            "(apps/library/assessments/easi-screening/v2) and data/source/adopted-method.json, "
            "the pin EASI loads by (owner decisions D15 and D20)",
            "commit: git log b4fe763 (2026-09-29, 'easi: activate easi-screening v2, pinned')",
            "reference_sha256: sha256 of apps/easi/data/reference-curves.json, unchanged from "
            "alternative-2-b2e3033116e3 (v2 keeps all 34 curves)",
            "catalog_sha256: sha256 of apps/easi/data/screening-methods.json: Alternative 2's "
            "catalog with the K2b applicability rule and its limitation on the four DEM geometry "
            "methods (study 2026-09-28-dem-geometry-quality-alternatives)",
        ),
    ),
}


def base(base_id: Optional[str] = None) -> Base:
    """The registry entry for ``base_id``; None is the default (2026-09-15) base."""
    key = base_id or DEFAULT_BASE_ID
    if key not in BASES:
        raise KeyError(f"unknown study base {key!r}; known bases: {sorted(BASES)}")
    return BASES[key]


def study_base(manifest: Optional[dict]) -> Base:
    """The base a study manifest names (``base_id``); a manifest without one is a 1.0.0
    study on the 2026-09-15 base. A manifest whose ``alternative_1`` block disagrees with
    the entry is refused: a study never changes its base."""
    manifest = manifest or {}
    found = base(manifest.get("base_id"))
    recorded = manifest.get("alternative_1") or {}
    if recorded and (recorded.get("method_version") != found.method_version
                     or recorded.get("frozen_sha") != found.reference_sha256):
        raise ValueError(f"the study's alternative_1 record is not base {found.id}")
    return found


def study_id_ok(study_id: str) -> bool:
    """True for the 2026-09-15 study's id or an id of the new pattern."""
    return study_id == LEGACY_STUDY_ID or bool(STUDY_ID_PATTERN.match(str(study_id)))
