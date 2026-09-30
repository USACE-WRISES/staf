"""The HR client hardening (2026-09-30) changed acquisition code only: the
scoring method EASI v2 is pinned to (``data/source/adopted-method.json``) is
untouched, so its method version, the release pin and the Nationwide dataset's
identity stay valid."""
from __future__ import annotations

import json
from pathlib import Path

from easi import method_package as mp

PIN = Path(__file__).resolve().parents[1] / "data" / "source" / "adopted-method.json"


def test_the_evaluator_is_the_pinned_one():
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    assert mp.evaluator_digest() == pin["evaluatorDigest"]


def test_the_hr_client_is_acquisition_code_not_method_code():
    names = {p.relative_to(mp.package_root()).as_posix() for p in mp.evaluator_sources()}
    for acquisition in ("datasources/nhd_hr.py", "routing.py", "network_display.py",
                        "pipeline.py"):
        assert acquisition not in names
    assert not any(n.startswith("_vendor/") for n in names)
