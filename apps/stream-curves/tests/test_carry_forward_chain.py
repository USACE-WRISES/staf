"""A rebuild carries the curves its published version itself carried forward
(methodology 0.14; review of 2026-09-22).

Interior Plateau v6 scores 22 curves it carried from v5, and Northeastern
Highlands v9 23 it carried from v8. A rebuild of either used to carry only the
curves the version had fitted, and walked the rest afresh, where the acceptance
evidence refuses most of the pools they rest on. A curve carried again keeps the
version that built it, and is judged there: its pool values are read from that
version's session, or, when that version cannot be read, its metric is judged as
a national or modeled curve's is.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from streamcurves import carry_forward as cf
from streamcurves import library as lib
from streamcurves import session_io as sio

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"


def _published(assessment_id: str, version: int) -> dict:
    vdir = LIBRARY / assessment_id / f"v{version}"
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{assessment_id}/v{version} is not in this checkout")
    return sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))


@pytest.mark.parametrize("code,assessment_id,version", [
    ("71", "interior-plateau", 6), ("58", "northeastern-highlands", 9)])
def test_a_rebuild_carries_what_its_version_itself_carried(code, assessment_id, version):
    fields = _published(assessment_id, version)
    prior = cf.prepare(code)
    if prior.get("fromVersion") != version:
        pytest.skip(f"{assessment_id} has moved past v{version}")
    recarried = set((fields.get("reference_build") or {}).get("carriedMetrics") or {})
    assert recarried and recarried <= set(prior["carried"]) | set(prior["rebuilt"])
    for mk in recarried & set(prior["carried"]):
        c = prior["carried"][mk]
        was = c["annotations"]["carriedForward"]["fromVersion"]
        # it still names the version that built it, and so does its record
        assert was < version and c["decision"]["carried_from"] == was
        assert c["config"] and c["functions"] and len(c["row"]["curve_points"]) >= 2


def test_a_recarried_pool_curve_is_judged_where_it_was_built(monkeypatch):
    fields = _published("interior-plateau", 6)
    target = sorted(fields["reference_build"]["carriedMetrics"])[0]
    real = cf._pool_defects

    def flag(metric, pool):
        got = real(metric, pool)
        return {**got, "n_pool": int(len(pool)), "n_defective": 1} if metric == target else got
    monkeypatch.setattr(cf, "_pool_defects", flag)
    prior = cf.prepare("71")
    if prior.get("fromVersion") != 6:
        pytest.skip("Interior Plateau has moved past v6")
    assert target not in prior["carried"]
    assert "built on in version 5" in prior["rebuilt"][target]["why"]


def test_a_version_that_cannot_be_read_judges_the_metric(tmp_path, monkeypatch):
    src = LIBRARY / "interior-plateau"
    fields = _published("interior-plateau", 6)
    root = tmp_path / "library"
    dest = root / "assessments" / "interior-plateau"
    (dest / "v6").mkdir(parents=True)
    shutil.copy(src / "manifest.json", dest / "manifest.json")
    for name in (lib.BUNDLE_FILE, lib.SESSION_FILE, lib.META_FILE):
        shutil.copy(src / "v6" / name, dest / "v6" / name)
    recarried = sorted(fields["reference_build"]["carriedMetrics"])
    target = recarried[0]
    monkeypatch.setattr(cf.nd, "is_corrected",
                        lambda mk, corrected=None: "a test correction" if mk == target else None)
    prior = cf.prepare("71", root=root)
    if prior.get("fromVersion") != 6:
        pytest.skip("Interior Plateau has moved past v6")
    # v5 is not in this library: the metric's archive values decide
    assert "carried from version 5" in prior["rebuilt"][target]["why"]
    assert set(recarried) - {target} <= set(prior["carried"])
