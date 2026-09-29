"""Isolated, local EASI alternative studies; never a publication pipeline.

Study runner 1.2.0 (2026-09-27): a study takes built candidate packages (Round 4 of the
national campaign, the EASI addendum's families) as its candidates, beside the base the
manifest names (``bases.py``, ``base_id``). Its manifest carries a ``candidates`` block: the
base as ``alternative-1`` (the snapshot's app-data unchanged, labelled by the base registry
entry) and one entry per candidate package; every step enumerates ``study_candidates``.

A manifest without a ``candidates`` block is a 1.0.0 or 1.1.0 study whose steps enumerate
``ALTERNATIVES`` exactly as before, so the 2026-09-15 study, its receipts and its completion
record read exactly as before and are never rewritten. The constants below keep their values
for the same reason. ``protocol()`` records this version and the addendum's sha256 in every
new study.
"""
from .bases import (BASES, DEFAULT_BASE_ID, LEGACY_STUDY_ID, STUDY_ID_PATTERN,  # noqa: F401
                    Base, base, study_base, study_id_ok)

STUDY_VERSION = "1.2.0"
_DEFAULT_BASE = base(DEFAULT_BASE_ID)
BASE_METHOD = _DEFAULT_BASE.method_version
BASE_COMMIT = _DEFAULT_BASE.commit
BASE_REFERENCE = _DEFAULT_BASE.reference_sha256
REGIONAL_SETS = ("corridor-woody", "corridor-natural", "flow-variability")
ALTERNATIVES = [
    {"id": "alternative-1", "label": "Alternative 1: current regional method", "curve_count": 62, "changes": "None; preserved current method"},
    {"id": "alternative-2", "label": "Alternative 2: NARS-9 references", "curve_count": 34, "changes": "Only three regional curve families use NARS-9"},
    {"id": "alternative-3", "label": "Alternative 3: national references", "curve_count": 7, "changes": "Only three regional curve families use their existing national fallback"},
    {"id": "alternative-4", "label": "Alternative 4: woody 8.2 fallback", "curve_count": 61, "changes": "Only woody Level II 8.2 uses the existing national curve"},
]
#: the study's reference arm in every study version
REFERENCE_ID = "alternative-1"


def study_candidates(manifest, default=None) -> list:
    """The arms a study scores, in order: the manifest's ``candidates`` block (a 1.2.0 study:
    ``alternative-1`` is the base, then one entry per candidate package), or ``default``
    (``ALTERNATIVES``) when the block is absent (a 1.0.0 or 1.1.0 study). Every entry carries
    at least ``id``, ``label``, ``curve_count`` and ``changes``; a candidate package entry also
    carries the family, its ``candidate.json`` record and its identities."""
    block = (manifest or {}).get("candidates")
    if block:
        return [dict(row) for row in block]
    return list(default if default is not None else ALTERNATIVES)


def is_round4(manifest) -> bool:
    """True for a study created with a ``candidates`` block (runner 1.2.0)."""
    return bool((manifest or {}).get("candidates"))
