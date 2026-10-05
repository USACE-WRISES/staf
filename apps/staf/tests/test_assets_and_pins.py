"""What the STAF app takes from its tools: the shared assets (served once, so they must be the same
everywhere) and the pins (one environment, so it must be the union of the tools')."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from staf_shell.loader import ORDER

HERE = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s#]+)")


def _tree(root: Path) -> dict:
    return dict((p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(root.rglob("*")) if p.is_file())


def _pins(path: Path) -> dict:
    out = dict()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line.strip())
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def test_every_tool_ships_the_same_shared_assets(staf):
    trees = [_tree(staf.TOOLS[key].www / "staf") for key in ORDER]
    assert trees[0] and trees[0] == trees[1] == trees[2]
    assert "staf-ns.js" in trees[0]


def test_the_pins_are_the_union_of_the_tools(staf):
    union = dict()
    for key in ORDER:
        for name, version in _pins(staf.TOOLS[key].root / "requirements.txt").items():
            assert union.setdefault(name, version) == version, (key, name, version)
    assert _pins(HERE / "requirements.txt") == union
