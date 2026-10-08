"""Find, import and check EASI, SFARI and DEEP for the STAF app.

Each tool is its own app under ``apps/<tool>`` (its package, ``www/``, ``data/`` and ``app.py``).
The STAF app imports the three into one process:

1. it points every tool's caches at one runtime folder (each tool's ``app.py`` only sets its own
   defaults, so the values set here win);
2. it imports the first tool's package (EASI's import checks its adopted method) and that tool's
   whole site engine, then points the other tools' copies at it before their packages are imported
   (``engine.py``);
3. it loads each tool's ``app.py`` under its own module name (all three are called ``app.py``) and
   checks it offers the module pieces the shell uses.

A tool that fails a step gets an "unavailable" section and the others carry on.
``STAF_DISABLE_TOOLS=easi,deep`` leaves tools out on purpose.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from . import engine

HERE = Path(__file__).resolve().parents[1]                     # apps/staf
ORDER = ("easi", "sfari", "deep")
NAMES = dict(easi="EASI", sfari="SFARI", deep="DEEP")
FULL_NAMES = dict(easi="Ecosystem Assessment Screening Index",
                  sfari="Stream Functions Assessment and Rapid Index",
                  deep="Detailed Evaluation of Ecosystem Processes")
#: each tool's STAF tier (docs/_data/apps.yml), in the order the tiers go
TIERS = dict(easi="Screening", sfari="Rapid", deep="Detailed")
#: the STAF site: the header's STAF link, and each tool's section of its Apply STAF page
SITE = "https://usace-wrises.github.io/staf/"
#: what the shell needs from a tool's app.py
CONTRACT = ("TOOL_KEY", "TOOL_NAME", "TOOL_FULL_NAME", "HEAD", "tool_nav_ui", "tool_center_ui",
            "tool_body_ui", "tool_server")


@dataclass
class Tool:
    key: str
    root: Path                    # apps/<tool>, or its assembled copy in _tools/<tool>
    module: object = None         # the tool's app.py, loaded as staf_tool_<key>
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.module is not None and self.error is None

    @property
    def name(self) -> str:
        return NAMES[self.key]

    @property
    def full_name(self) -> str:
        return getattr(self.module, "TOOL_FULL_NAME", None) or FULL_NAMES[self.key]

    @property
    def tier(self) -> str:
        return TIERS[self.key]

    @property
    def www(self) -> Path:
        return self.root / "www"

    @property
    def calculator_page(self) -> str:
        """The tool's section of the site's Apply STAF page, which offers its spreadsheet
        calculator. The standalone apps were retired on 2026-10-08, and the site's /<tool>/
        address now leads back into STAF."""
        return f"{SITE}tools/#{self.key}"


def tools_root() -> Path:
    """Where the tools are: ``STAF_TOOLS_DIR``; else the repo's ``apps/`` around this app
    (development); else the copy ``scripts/assemble_tools.py`` puts in ``_tools/`` for a deploy."""
    if os.environ.get("STAF_TOOLS_DIR"):
        return Path(os.environ["STAF_TOOLS_DIR"]).resolve()
    if all((HERE.parent / key / "app.py").is_file() for key in ORDER):
        return HERE.parent
    return HERE / "_tools"


def shared_caches() -> Path:
    """One runtime folder for every tool's caches: the data bundle, the HR answers and HyRiver's."""
    run = Path(os.environ.get("STAF_RUNTIME_DIR") or Path(tempfile.gettempdir()) / "staf")
    run.mkdir(parents=True, exist_ok=True)
    for key, value in (("STAF_DATA_SOURCE", "auto"),
                       ("STAF_DATA_CACHE", run / "data_bundle"),
                       ("STAF_HR_CACHE_DIR", run / "hr_cache"),
                       ("HYRIVER_CACHE_NAME", run / "hyriver.sqlite"),
                       ("HYRIVER_CACHE_NAME_HTTP", run / "hyriver_http.sqlite"),
                       ("HYRIVER_CACHE_EXPIRE", "604800")):
        os.environ.setdefault(key, str(value))
    return run


def _attempt(tool: Tool, step) -> bool:
    started = time.perf_counter()
    try:
        step()
        return True
    except Exception as exc:  # noqa: BLE001 - one tool failing never stops the others
        tool.error = f"{type(exc).__name__}: {exc}"
        tool.module = None
        traceback.print_exc()
        return False
    finally:
        tool.seconds += time.perf_counter() - started


def _load_app(tool: Tool) -> None:
    name = f"staf_tool_{tool.key}"
    spec = importlib.util.spec_from_file_location(name, tool.root / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    missing = [attr for attr in CONTRACT if not hasattr(module, attr)]
    if missing:
        raise RuntimeError("not a STAF module yet (no " + ", ".join(missing) + ")")
    tool.module = module


def load_all() -> dict:
    shared_caches()
    root = tools_root()
    print(f"[staf] tools from {root}", file=sys.stderr)
    off = {key.strip().lower() for key in os.environ.get("STAF_DISABLE_TOOLS", "").split(",") if key.strip()}
    tools = {key: Tool(key, root / key) for key in ORDER}
    canonical = None                      # the tool whose site engine every tool shares
    for tool in tools.values():
        if tool.key in off:
            tool.error = "turned off with STAF_DISABLE_TOOLS"
            continue
        if not (tool.root / "app.py").is_file():
            tool.error = f"not found in {root}"
            continue
        if str(tool.root) not in sys.path:
            sys.path.append(str(tool.root))
        sharing = canonical is not None and engine.same(root, tool.key, canonical)
        if sharing:
            engine.alias(tool.key, canonical)
        if not _attempt(tool, lambda: importlib.import_module(tool.key)):
            continue
        if canonical is None:
            if _attempt(tool, lambda: engine.load(tool.key)):
                canonical = tool.key
        elif sharing:
            _attempt(tool, lambda: engine.attach(tool.key, canonical))
    for tool in tools.values():
        if tool.error is None:
            _attempt(tool, lambda: _load_app(tool))
        state = "ready" if tool.ok else tool.error
        print(f"[staf] {tool.name}: {state} ({tool.seconds:.1f} s)", file=sys.stderr)
    return tools
