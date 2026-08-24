"""The profiler's role-suggestion lookup chain.

``_LIBRARY_LOOKUPS`` once named ``streamcurves.nrsa_metrics`` plus two
``streamcurves.datasources.*`` paths, none of which exist in this repo: they were
guesses at the R filename (``app/helpers/nrsa_metrics.R``), while the port landed
the module as ``streamcurves.nrsa``. ``suggest_roles_from_library`` swallows an
import error by design, so the NRSA catalog fallback documented in the comment
above the tuple simply never ran, and 762 of the 788 catalog codes (everything
``metric_map.yaml`` does not curate) got no role suggestion at all.

Nothing in the suite touched ``suggest_roles`` before this file, which is why the
drift survived. ``test_every_lookup_path_actually_imports`` is the one that
generalizes: a future rename fails loudly instead of degrading into silence.

NOTE: the primary ``role`` column stays structural, so a catalog metric like
``chem_ANC`` still reports ``role == "predictor"`` while its flags say metric.
That divergence is inert. ``classify_ui.classify_selected_role_set`` prefers the
``role_*`` flags whenever all three are present, and ``suggest_roles`` always
writes all three, so the ``role`` branch is unreachable from a
``profile_and_suggest`` frame.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest

from streamcurves import metric_map as mm
from streamcurves import nrsa
from streamcurves.profiler import (
    _LIBRARY_LOOKUPS,
    profile_and_suggest,
    suggest_roles_from_library,
)


# --------------------------------------------------------------------------- #
# the tuple itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mod_path,func_name", _LIBRARY_LOOKUPS)
def test_every_lookup_path_actually_imports(mod_path, func_name):
    """Outside the try/except on purpose: this is the assertion the bug needed."""
    mod = importlib.import_module(mod_path)
    assert callable(getattr(mod, func_name))


def test_the_curated_map_is_consulted_first():
    assert _LIBRARY_LOOKUPS[0][0] == "streamcurves.metric_map"


# --------------------------------------------------------------------------- #
# the fallback fires
# --------------------------------------------------------------------------- #

def test_the_nrsa_catalog_fallback_fires():
    """Both of these returned [] while the module paths were wrong."""
    assert suggest_roles_from_library("chem_ANC") == ["metric"]
    assert suggest_roles_from_library("land_BFIWS") == ["predictor"]


@pytest.mark.parametrize("code", [
    "chem_ANC",          # Water chemistry
    "phab_MEDBK_A",      # Physical habitat
    "bent_AMPHNTAX",     # Benthic macroinvertebrates
    "fish_ALIENNTAX",    # Fish
])
def test_a_measured_indicator_suggests_metric(code):
    """One fallback-only code per category, so a catalog reshuffle is visible.
    Each is absent from metric_map.yaml, so only the catalog can answer."""
    assert mm.metric_map_role_for(code) is None
    assert suggest_roles_from_library(code) == ["metric"]


@pytest.mark.parametrize("code", ["land_BFIWS", "land_ELEVWS"])
def test_a_landscape_code_suggests_predictor(code):
    assert mm.metric_map_role_for(code) is None
    assert suggest_roles_from_library(code) == ["predictor"]


def test_an_unknown_code_suggests_nothing():
    assert suggest_roles_from_library("__nope__") == []


# --------------------------------------------------------------------------- #
# precedence
# --------------------------------------------------------------------------- #

def test_a_curated_code_is_answered_by_metric_map_not_the_catalog(monkeypatch):
    """phab_XEMBED is in metric_map.yaml AND the catalog, and today they agree,
    so the roles alone cannot prove which one answered. Make them disagree."""
    assert mm.metric_map_role_for("phab_XEMBED") == "metric"
    assert nrsa.nrsa_catalog_role_for("phab_XEMBED") == "metric"

    monkeypatch.setattr(nrsa, "nrsa_catalog_role_for", lambda code: "stratifier")
    assert suggest_roles_from_library("phab_XEMBED") == ["metric"]
    # and the patch is live, so the assertion above is not vacuous
    assert suggest_roles_from_library("chem_ANC") == ["stratifier"]


def test_a_metric_map_only_code_keeps_its_dual_role():
    """bfi carries role: both and is not in the NRSA catalog at all."""
    assert nrsa.nrsa_catalog_role_for("bfi") is None
    assert suggest_roles_from_library("bfi") == ["metric", "predictor"]


# --------------------------------------------------------------------------- #
# what the wizard's Classify step actually sees
# --------------------------------------------------------------------------- #

def test_the_role_flags_flip_for_a_catalog_metric():
    """Before the fix chem_ANC fell through to the structural default and was
    offered as a Predictor. 459 measured indicators were in that position."""
    frame = pd.DataFrame({
        "site_id": ["a", "b", "c", "d"],
        "chem_ANC": [10.0, 20.0, 30.0, 40.0],
        "land_BFIWS": [55.0, 60.0, 65.0, 70.0],
        "phab_XEMBED": [5.0, 15.0, 25.0, 35.0],
    })
    prof = profile_and_suggest(frame).set_index("column")

    def flags(col):
        row = prof.loc[col]
        return (bool(row["role_metric"]), bool(row["role_predictor"]),
                bool(row["role_stratifier"]))

    assert flags("chem_ANC") == (True, False, False)
    assert flags("phab_XEMBED") == (True, False, False)
    assert flags("land_BFIWS") == (False, True, False)     # matches the old default
    assert flags("site_id") == (False, False, False)
    assert prof.loc["site_id", "role"] == "identifier"
