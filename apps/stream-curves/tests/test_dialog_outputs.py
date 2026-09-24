"""Outputs that live inside a dialog stay live while the dialog fades in.

A dialog's outputs bind while Bootstrap still hides the modal; a suspended output never
resumes, so a dialog's error line or preview would never show. Every such output is declared
``@output(suspend_when_hidden=False)`` (DEEP's field-forms dialog guards the same trap).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

VIEWS = Path(__file__).resolve().parents[1] / "views"
DIALOG_OUTPUTS = {"easi_page.py": ("modal_err", "pkg_preview"),
                  "final_selection.py": ("fs_sqt_results", "fs_select_checks")}


@pytest.mark.parametrize("module,name", [(m, n) for m, names in DIALOG_OUTPUTS.items() for n in names])
def test_a_dialog_output_is_never_suspended(module, name):
    text = (VIEWS / module).read_text(encoding="utf-8")
    assert f'ui.output_ui(ns("{name}"))' in text, f"{name} is no longer an output of {module}"
    assert re.search(r"@output\(suspend_when_hidden=False\)\s+@render\.ui\s+def " + name + r"\(", text), \
        f"{module}: {name} lives in a dialog and must not be suspended while hidden"
