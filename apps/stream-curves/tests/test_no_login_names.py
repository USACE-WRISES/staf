"""Who StreamCurves records: initials, n/a by default, never the login (the owner's rule of
2026-09-23, decision 4 of the authoring round 2).

Every page that records a decision, an edit or a publish reads one helper,
``views.state.recorded_by`` (``prefs.recorded_by`` with the open project's metadata):
STAF_LIBRARY_MAINTAINER when set, else the open project's Prepared by, else ``n/a``. The
computer-wide preference of the same name only offers initials to a new project. The Windows
login is never read, and nothing is refused for a missing name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from shiny import reactive

from streamcurves import prefs

APP = Path(__file__).resolve().parents[1]
#: the usual ways code reads the login: an environment name as a string (however it is then
#: read, a variable key included; plain USER only as read from the environment, since "USER" is
#: also the import map's code for a drawn region), the modules that ask the system, the home
#: folder's name
LOGIN = re.compile(
    r"""["'](?:USERNAME|LOGNAME|USERPROFILE|HOMEPATH)["']"""
    r"""|(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*["']USER["']"""
    r"""|\bgetpass\b|\bgetlogin\b|\bgetpwuid\b"""
    r"""|home\(\)\s*\.\s*(?:name|stem|parts)\b"""
    r"""|expanduser\(\s*["']~["']\s*\)\s*\)?\s*\.\s*(?:name|stem|parts|split)\b"""
    r"""|basename\(\s*(?:os\.path\.)?expanduser""")


@pytest.fixture
def no_name(tmp_path, monkeypatch):
    """A process with a login but no initials anywhere."""
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data-root"))
    monkeypatch.delenv("STAF_LIBRARY_MAINTAINER", raising=False)
    monkeypatch.setenv("USERNAME", "the-login")
    monkeypatch.setenv("USER", "the-login")
    return monkeypatch


def test_the_scan_knows_the_usual_ways_of_reading_the_login():
    for line in ('os.environ.get("USERNAME")', "os.environ['USERNAME']", 'os.getenv("USERNAME")',
                 'os.environ.get("USERPROFILE")', 'key = "LOGNAME"', 'os.path.basename(os.path.expanduser("~"))',
                 'Path(os.path.expanduser("~")).name', "Path.home().name", "Path.home().parts[-1]",
                 "getpass.getuser()", "os.getlogin()", "pwd.getpwuid(os.getuid())",
                 'os.getenv("USER")', "os.environ['USER']"):
        assert LOGIN.search(line), line
    for line in ('Path.home() / "Documents"', 'Path.home() / ".streamcurves"', "prefs.recorded_by(meta)",
                 'region_code.set("USER")'):
        assert not LOGIN.search(line), line


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


def test_initials_come_from_the_environment_then_the_projects_prepared_by_then_n_a(no_name):
    assert prefs.recorded_by() == "n/a" and prefs.recorded_by({"prepared_by": None}) == "n/a"
    assert prefs.recorded_by({"prepared_by": "  XY "}) == "XY"
    # the computer-wide preference only offers initials to a new project; it is never recorded
    prefs.set(prefs.PREPARED_BY, "PQ")
    assert prefs.recorded_by() == "n/a" and prefs.recorded_by({}) == "n/a"
    no_name.setenv("STAF_LIBRARY_MAINTAINER", "AB")
    assert prefs.recorded_by({"prepared_by": "XY"}) == "AB"
    assert prefs.given_or_na("") == "n/a" and prefs.given_or_na(None) == "n/a"
    assert prefs.given_or_na("  QR ") == "QR"


def test_every_page_records_the_open_projects_initials_and_never_the_login(no_name):
    from views import discipline_map, easi_page, publish, region_builder, source_panel, validate_page
    from views.state import AppState
    readers = (easi_page.person, source_panel.maintainer, validate_page._maintainer,
               region_builder._maintainer, publish._maintainer_name, discipline_map._default_actor)
    state = AppState.fresh()
    prefs.set(prefs.PREPARED_BY, "PQ")                 # another project's, typed on this computer
    assert {f(state) for f in readers} == {"n/a"} and {f() for f in readers} == {"n/a"}
    with reactive.isolate():
        state.project_meta.set({"project_name": "p", "prepared_by": "ZZ"})
    assert {f(state) for f in readers} == {"ZZ"}
    no_name.setenv("STAF_LIBRARY_MAINTAINER", "AB")
    assert {f(state) for f in readers} == {"AB"}


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
