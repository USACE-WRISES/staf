"""Isolated, local EASI alternative studies; never a publication pipeline.

Study runner 1.1.0 (2026-09-25): the base a study starts from is an entry of the registry
in ``bases.py``, named by the study manifest's ``base_id``. A manifest without one is a
1.0.0 study on the 2026-09-15 base, whose constants below keep their values, so the
2026-09-15 study, its receipts and its completion record read exactly as before and are
never rewritten. ``protocol()`` records this version in every new study.
"""
from .bases import (BASES, DEFAULT_BASE_ID, LEGACY_STUDY_ID, STUDY_ID_PATTERN,  # noqa: F401
                    Base, base, study_base, study_id_ok)

STUDY_VERSION = "1.1.0"
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
