"""The SFARI assessment file: the header's Save and Open.

Since 2026-10-05 SFARI writes the STAF assessment file, the structure EASI and DEEP write too
(``_vendor/staf_workbook/assessment_file.py``):

* ``delineation``: the whole ``delineate_only`` result. Since 2026-09 it may carry ``siteAnchor``
  (the reach classification), ``siteEngine`` (the geometry-stripped STAF site engine record) and
  ``watershedBasis``; earlier files lack them and every reader uses ``.get``.
* ``toolData``: what every scenario shares, the pulled desktop ``evidence`` and the
  ``cross_section`` geometry.
* ``scenarios``: each scenario's entries (``metric_scores``: Likert, note and photos per metric;
  ``function_scores``: the 0-15 score and note per function), Existing Conditions first.

Files written before (``schemaVersion`` 1: Existing Conditions' entries at the top level, the
alternatives under ``scenarios``) still open.
"""
from __future__ import annotations

from ._vendor.staf_workbook import assessment_file

TOOL = "SFARI"


def dump(delineation: dict, evidence: dict, cross_section, scenarios: dict) -> str:
    """The file. ``scenarios`` is ``assessment_file.scenarios_block``'s: every scenario's
    ``{"metric_scores", "function_scores"}``."""
    return assessment_file.dump(TOOL, delineation or {},
                                {"evidence": evidence or {}, "cross_section": cross_section}, scenarios)


def load(text: str) -> dict:
    """``{"delineation", "evidence", "cross_section", "scenarios"}`` of a saved file (missing
    blocks read empty). Raises ``assessment_file.AssessmentFileError`` for a file SFARI cannot
    open."""
    st = assessment_file.parse(text, TOOL, legacy=_legacy)
    data = st["toolData"]
    evidence = data.get("evidence")
    return {"delineation": st["delineation"],
            "evidence": evidence if isinstance(evidence, dict) else {},
            "cross_section": data.get("cross_section"),
            "scenarios": _without_function_na(st["scenarios"])}


def _legacy(d: dict) -> dict:
    """A file from before the shared format, in its shape."""
    entries = {"metric_scores": d.get("metric_scores") or {}, "function_scores": d.get("function_scores") or {}}
    return {"delineation": d.get("delineation", {}),
            "toolData": {"evidence": d.get("evidence") or {}, "cross_section": d.get("cross_section")},
            "scenarios": assessment_file.legacy_scenarios(d.get("scenarios"), entries)}


def _without_function_na(block):
    """Function-level N/A was removed (only metrics can be N/A): an old entry drops it."""
    for item in (block or {}).get("items") or []:
        state = item.get("state") if isinstance(item, dict) else None
        scores = state.get("function_scores") if isinstance(state, dict) else None
        for rec in (scores or {}).values() if isinstance(scores, dict) else ():
            if isinstance(rec, dict):
                rec.pop("na", None)
    return block
