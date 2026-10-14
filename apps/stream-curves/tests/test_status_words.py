"""The status words the calculator stamps are DEEP's words (owner, 2026-10-14).

StreamCurves stamps each DEEP calculator with its version's status: a banner, a print
header and the Metadata row say "Assessment status: Draft. For research ...". DEEP prints
the same sentence on its field forms, metrics and report, and shows the same labels, lines
and colors on screen. There is no shared package, so the literals live twice
(streamcurves/lifecycle_words.py and apps/deep/deep/session.py); this test reads DEEP's
file as source, so it needs nothing from DEEP's runtime.
"""
from __future__ import annotations

import ast
from pathlib import Path

from streamcurves import library as lib
from streamcurves import lifecycle_words as words

DEEP_SESSION = Path(__file__).resolve().parents[2] / "deep" / "deep" / "session.py"
NAMES = ("STATUS_LABELS", "STATUS_NOTES", "STATUS_COLORS", "STATUS_LINE")


def _deep_literals() -> dict:
    tree = ast.parse(DEEP_SESSION.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in NAMES:
                out[target.id] = ast.literal_eval(node.value)
    return out


def test_deep_holds_the_same_words():
    deep = _deep_literals()
    assert set(deep) == set(NAMES)
    for name in NAMES:
        assert deep[name] == getattr(words, name), name


def test_library_offers_the_same_labels():
    assert lib.STATUS_LABELS is words.STATUS_LABELS
    assert lib.status_label("certified") == "Final"


def test_every_status_deep_runs_has_a_line_and_colors():
    assert tuple(words.STATUS_NOTES) == lib.DEEP_STATUSES
    assert tuple(words.STATUS_COLORS) == lib.DEEP_STATUSES
    assert words.status_line("draft") == ("Assessment status: Draft. For research and regional "
                                          "assessment development only.")
    assert words.status_line("certified") == "Assessment status: Final. Cleared and approved for use."
    assert words.status_line("retired") == "Assessment status: Retired."
    assert words.status_colors("retired") == words.STATUS_COLORS["draft"]


def test_no_dash_in_the_words():
    for text in list(words.STATUS_NOTES.values()) + [words.STATUS_LINE]:
        assert "—" not in text and "–" not in text
