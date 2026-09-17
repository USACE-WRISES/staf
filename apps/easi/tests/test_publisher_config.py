"""The Posit Publisher bundle carries every file the app imports or serves.

Connect Cloud installs from ``requirements.txt`` and uploads only the paths in
``.posit/publish/easi-987U.toml``. A top-level module imported by ``app.py``
that is missing from that list fails the deploy with "No module named ..."
(seen 2026-09-17 with ``local_review``), so the list is checked here.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".posit" / "publish" / "easi-987U.toml"


def _files() -> list[str]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))["files"]


def _covered(path: str, files: list[str]) -> bool:
    return any(path == f or (f.endswith("/") and path.startswith(f)) for f in files)


def test_every_top_level_module_app_imports_is_in_the_bundle():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    names = set(re.findall(r"^(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", src, re.M))
    local = sorted(n for n in names if (ROOT / f"{n}.py").is_file() or (ROOT / n / "__init__.py").is_file())
    assert local, "app.py imports nothing local?"
    files = _files()
    missing = [n for n in local
               if not (_covered(f"/{n}.py", files) if (ROOT / f"{n}.py").is_file() else _covered(f"/{n}/", files))]
    assert not missing, f"add these to {CONFIG.name}: {missing}"


def test_the_bundle_carries_the_app_its_data_and_the_served_workbook():
    from easi import calculator

    files = _files()
    for required in ("/app.py", "/requirements.txt", "/easi/", "/data/", "/www/"):
        assert required in files, required
    workbook = "/" + calculator.TEMPLATE_PATH.relative_to(ROOT).as_posix()
    assert _covered(workbook, files), workbook
    assert calculator.TEMPLATE_PATH.is_file()
