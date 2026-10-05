"""The STAF app imports EASI, SFARI and DEEP into one process, on one site engine."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from staf_shell import engine
from staf_shell.loader import ORDER

HERE = Path(__file__).resolve().parents[1]


def test_every_tool_loads_under_its_own_name(staf):
    assert list(staf.TOOLS) == list(ORDER)
    for key, tool in staf.TOOLS.items():
        assert tool.ok, tool.error
        assert tool.module.__name__ == f"staf_tool_{key}" and tool.module.TOOL_KEY == key


def test_the_tools_share_one_site_engine(staf):
    hr = sys.modules["easi._vendor.site_engine.hr"]
    for key in ("sfari", "deep"):
        assert engine.shared_with(key, "easi"), key
        assert sys.modules[f"{key}._vendor.site_engine.hr"] is hr
        assert sys.modules[f"{key}._vendor"].site_engine is sys.modules["easi._vendor.site_engine"]
    for name, module in list(sys.modules.items()):
        path = str(getattr(module, "__file__", "") or "")
        for key in ("sfari", "deep"):
            assert f"{os.sep}{key}{os.sep}_vendor{os.sep}site_engine" not in path, (name, path)


def test_the_shared_caches_live_in_one_runtime_folder(staf):
    run = Path(os.environ["STAF_RUNTIME_DIR"])
    for key in ("STAF_DATA_CACHE", "STAF_HR_CACHE_DIR", "HYRIVER_CACHE_NAME", "HYRIVER_CACHE_NAME_HTTP"):
        assert Path(os.environ[key]).parent == run, key


def test_a_tool_left_out_leaves_the_others_on_one_engine():
    code = ("import sys\n"
            "from staf_shell import engine, loader\n"
            "tools = loader.load_all()\n"
            "assert 'STAF_DISABLE_TOOLS' in tools['easi'].error\n"
            "assert 'easi' not in sys.modules\n"
            "assert engine.shared_with('deep', 'sfari')\n"
            "assert tools['sfari'].ok, tools['sfari'].error\n"
            "print('ok')\n")
    run = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=dict(os.environ, STAF_DISABLE_TOOLS="easi"),
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0 and "ok" in run.stdout, run.stdout + run.stderr
