"""The STAF assessment file: what the header's Save writes and Open reads, one JSON structure in
EASI, SFARI and DEEP (owner, 2026-10-05).

    {
      "format": "staf-assessment",
      "formatVersion": 1,
      "tool": "SFARI",
      "savedAt": "2026-10-05T17:20:31Z",
      "delineation": {...},
      "toolData": {...},
      "scenarios": {"version": 1, "active": "existing", "next": 2,
                    "items": [{"id": "existing", "name": "Existing Conditions",
                               "description": "", "state": {...}}, ...]}
    }

``delineation`` is the site: the tool's delineate result, as its page holds it. ``toolData`` is
what the tool brought to the site, the same in every scenario (SFARI: the desktop evidence and the
cross section; DEEP: the assessment and its provenance; EASI: the screening, the notes and the
method). ``scenarios`` is what the assessor entered: every scenario with its own state, Existing
Conditions first, ``active`` the one on screen when the file was saved.

A tool opens only its own files. Files SFARI and DEEP wrote before this format (their own
``schemaVersion`` files, Existing Conditions' entries at the top level) still open: the tool's
``legacy`` reader lifts them into this shape. Standard library only.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json

from .model.scenarios import BASELINE_ID, ScenarioSet

FORMAT = "staf-assessment"
FORMAT_VERSION = 1
TOOLS = ("EASI", "SFARI", "DEEP")
#: blocks of a saved delineation the tools read as objects
_BLOCKS = ("delineation", "ctx_inputs", "siteAnchor", "siteEngine")
_NOT_OURS = "this is not a STAF assessment file."


class AssessmentFileError(ValueError):
    """A file this tool cannot open. The text completes "Could not load assessment: "."""


def dump(tool: str, delineation, tool_data, scenarios, *, saved_at: str | None = None) -> str:
    """The file ``tool`` saves. ``scenarios`` is :func:`scenarios_block`'s. One line per key: the
    header reads at a glance, and a watershed's coordinates stay compact (indenting them made a
    large basin's file four times its size)."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}")
    out = {"format": FORMAT, "formatVersion": FORMAT_VERSION, "tool": tool,
           "savedAt": saved_at or _now(),
           "delineation": delineation or {},
           "toolData": tool_data or {},
           "scenarios": scenarios or ScenarioSet().to_json(include_baseline_state=True)}
    lines = [f"  {json.dumps(key)}: "
             + json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_plain)
             for key, value in out.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def parse(text: str, tool: str, legacy=None) -> dict:
    """``{"delineation", "toolData", "scenarios"}`` of a file ``tool`` saved, checked. ``legacy(raw)``
    lifts a file the tool wrote before this format into that shape (without it such a file is
    refused). Raises :class:`AssessmentFileError`."""
    try:
        raw = json.loads(text)
    except ValueError:
        raise AssessmentFileError(_NOT_OURS) from None
    if not isinstance(raw, dict):
        raise AssessmentFileError(_NOT_OURS)
    if "format" in raw:
        if raw.get("format") != FORMAT or raw.get("tool") not in TOOLS:
            raise AssessmentFileError(_NOT_OURS)
        version = raw.get("formatVersion")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise AssessmentFileError("the file has no valid format version.")
        if version > FORMAT_VERSION:
            raise AssessmentFileError("it was saved by a newer version of STAF. Update this app to open it.")
        _same_tool(raw["tool"], tool)
        out = {"delineation": raw.get("delineation"), "toolData": raw.get("toolData"),
               "scenarios": raw.get("scenarios")}
    else:
        _same_tool(legacy_tool(raw), tool)
        if legacy is None:
            raise AssessmentFileError(_NOT_OURS)
        out = legacy(raw)
    return _checked(out)


def legacy_tool(raw: dict):
    """The tool that wrote a file from before this format: its ``method``, else what its keys show,
    else None (the tool's own legacy reader decides)."""
    method = raw.get("method")
    if isinstance(method, str) and method.strip().upper() in TOOLS:
        return method.strip().upper()
    if "measured_values" in raw or "assessment" in raw:
        return "DEEP"
    if "metric_scores" in raw or "function_scores" in raw:
        return "SFARI"
    return None


def _same_tool(saved, tool: str) -> None:
    if saved is not None and saved != tool:
        article = "an" if saved == "EASI" else "a"
        raise AssessmentFileError(f"this is {article} {saved} assessment. Open it in {saved}.")


def _checked(out: dict) -> dict:
    d = out.get("delineation")
    d = {} if d is None else d
    if not isinstance(d, dict):
        raise AssessmentFileError("the saved delineation must be an object.")
    for key in _BLOCKS:
        if d.get(key) is not None and not isinstance(d[key], dict):
            raise AssessmentFileError(f"the saved {key} must be an object.")
    data = out.get("toolData")
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise AssessmentFileError("the saved tool data must be an object.")
    scenarios = out.get("scenarios")
    if scenarios is not None and not isinstance(scenarios, dict):
        raise AssessmentFileError("the saved scenarios must be an object.")
    return {"delineation": d, "toolData": data, "scenarios": scenarios}


# --------------------------------------------------------------------------- scenarios
def scenarios_block(sset: ScenarioSet, live_state) -> dict:
    """The file's ``scenarios``: every scenario with its state, the one on screen (``sset.active``)
    with ``live_state``, what the page shows now."""
    data = sset.to_json(include_baseline_state=True)
    for item in data["items"]:
        if item["id"] == sset.active:
            item["state"] = live_state
    return copy.deepcopy(data)


def scenario_set(block) -> ScenarioSet:
    """The scenarios a file carries, each with its state (Existing Conditions' too), the one on
    screen when it was saved active. No block: Existing Conditions alone, with nothing entered."""
    block = copy.deepcopy(block)
    base = None
    if isinstance(block, dict) and isinstance(block.get("items"), list):
        base = next((item.get("state") for item in block["items"]
                     if isinstance(item, dict) and item.get("id") == BASELINE_ID), None)
    return ScenarioSet.from_json(block, baseline_state=base)


def legacy_scenarios(block, baseline_state) -> dict:
    """A pre-format file's scenarios as this format's block: those files kept Existing Conditions'
    entries at the top level (``baseline_state``) and only the alternatives under ``scenarios``."""
    return ScenarioSet.from_json(copy.deepcopy(block), baseline_state=baseline_state).to_json(
        include_baseline_state=True)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plain(value):
    """A value ``json`` cannot write as it is: a NumPy number as its Python value, a set as a list."""
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)
