"""What a published regional bundle says produced it.

Owner rule (2026-09-19): published text names the analysis in plain words and
never the software component. The strings ride on the DEEP field form, the
calculator's metadata sheet, and DEEP's curve-source column, so they read like
a citation. A version, once published, is never edited, so earlier versions
keep the wording they shipped with.

Kept apart from ``regional_agent`` so the light callers (``region_build``, the
CLIs' help text) do not import the analysis stack to spell a sentence.
"""
from __future__ import annotations

from typing import Optional

from . import methodology, nrsa_dataset

DEFAULT_AUTHOR = "StreamCurves regional analysis (STAF)"
PRODUCED_BY = "StreamCurves regional analysis"

_NRSA_SPAN = {nrsa_dataset.LEGACY_DATASET_ID: "2018-19",
              nrsa_dataset.MULTI_CYCLE_DATASET_ID: "2013-24"}


def default_source_citation(l3_code, dataset_id: Optional[str] = None) -> str:
    """The citation a regional build carries when the owner supplies none."""
    span = _NRSA_SPAN.get(str(dataset_id or ""), "")
    survey = f"USEPA NRSA {span}" if span else "USEPA NRSA"
    return (f"{survey} (L3 ecoregion {l3_code}), {PRODUCED_BY}, "
            f"methodology {methodology.methodology_version()}")
