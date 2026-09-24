"""Who StreamCurves records: initials, n/a by default, never the login (the owner's rule of
2026-09-23, decision 4 of the authoring round 2).

Every page that records a decision, an edit or a publish reads one helper,
``prefs.recorded_by``: STAF_LIBRARY_MAINTAINER when set, else the Prepared by initials, else
``n/a``. The Windows login is never read, and nothing is refused for a missing name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from streamcurves import prefs

APP = Path(__file__).resolve().parents[1]
LOGIN = re.compile(r"""environ(?:\.get)?\s*[\[(]\s*["'](?:USERNAME|USER|LOGNAME)["']|getpass|getlogin""")


@pytest.fixture
def no_name(tmp_path, monkeypatch):
    """A process with a login but no initials anywhere."""
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.delenv("STAF_LIBRARY_MAINTAINER", raising=False)
    monkeypatch.setenv("USERNAME", "the-login")
    monkeypatch.setenv("USER", "the-login")
    return monkeypatch


def test_no_code_reads_the_login():
    found = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith(("tests/", "streamcurves/_vendor/")) or "/_vendor/" in rel:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LOGIN.search(line):
                found.append(f"{rel}:{n}: {line.strip()}")
    assert found == []


def test_initials_come_from_the_environment_then_prepared_by_then_n_a(no_name):
    assert prefs.recorded_by() == "n/a"
    prefs.set(prefs.PREPARED_BY, "  XY ")
    assert prefs.recorded_by() == "XY"
    no_name.setenv("STAF_LIBRARY_MAINTAINER", "AB")
    assert prefs.recorded_by() == "AB"
    assert prefs.given_or_na("") == "n/a" and prefs.given_or_na(None) == "n/a"
    assert prefs.given_or_na("  QR ") == "QR"


def test_every_page_records_the_same_initials_and_never_the_login(no_name):
    from views import discipline_map, easi_page, publish, region_builder, source_panel, validate_page
    readers = (easi_page.person, source_panel.maintainer, validate_page._maintainer,
               region_builder._maintainer, publish._maintainer_name, discipline_map._default_actor)
    assert {f() for f in readers} == {"n/a"}
    no_name.setenv("STAF_LIBRARY_MAINTAINER", "AB")
    assert {f() for f in readers} == {"AB"}


def test_a_missing_name_never_refuses_a_publish(no_name, tmp_path):
    from streamcurves import library as lib
    no_name.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "library"))
    (tmp_path / "library").mkdir()
    no_name.setenv("STAF_LIBRARY_PUBLISH", "1")
    assert lib.publish_gate_reason("") is None and lib.publish_gate_reason(None) is None
    assert lib._maintainer_name("") == "n/a"
    src = (APP / "views" / "easi_page.py").read_text(encoding="utf-8")
    assert "Say who publishes" not in src and "Name not set" not in src
    assert '"publisher"' not in (APP / "streamcurves" / "library.py").read_text(encoding="utf-8")
