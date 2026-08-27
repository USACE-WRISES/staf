"""The Classify columns table.

The table listed only the raw column id, so choosing a role meant already
knowing what ``bfiws`` or ``pctwdwet2019ws`` measures. It now carries the
readable name beside the code, resolved through the same metric dictionary the
Review tab and the metric picker use, so one metric reads identically on all
three screens.
"""
from __future__ import annotations

import re

import pandas as pd

from streamcurves.profiler import profile_and_suggest
from views import classify_ui as cu


def _ns(name: str) -> str:
    return name


def _table(frame: pd.DataFrame) -> str:
    return str(cu.classify_table_html(_ns, profile_and_suggest(frame)))


def _cells(html: str) -> dict[str, str]:
    """{column code: name cell} for every body row."""
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S)[1:]:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        code = re.sub(r"<[^>]+>", "", tds[0]).strip()
        # the id hint badge renders inside the same cell
        code = code.replace("id?", "").strip()
        out[code] = re.sub(r"<[^>]+>", "", tds[1]).strip()
    return out


FRAME = pd.DataFrame({
    "site_id": ["a", "b", "c", "d"],
    "bfiws": [35.2, 28.3, 18.0, 28.4],
    "pctwdwet2019ws": [0.35, 0.76, 0.34, 0.19],
    "phab_XEMBED": [25.0, 35.0, 43.0, 12.0],
})


def test_the_name_column_sits_next_to_the_code():
    headers = re.findall(r"<th>(.*?)</th>", _table(FRAME))
    assert headers[:3] == ["Column", "Name", "Type"]


def test_a_metric_column_shows_what_it_measures():
    names = _cells(_table(FRAME))
    assert names["bfiws"] == "Base-flow index"
    assert names["pctwdwet2019ws"] == "Woody wetland"
    assert names["phab_XEMBED"] == "Embeddedness"


def test_an_identifier_column_shows_nothing_rather_than_its_code():
    assert _cells(_table(FRAME))["site_id"] == ""


def test_the_code_is_still_shown():
    """The name is added beside the code, not instead of it: the code is what
    the workbook and every export key on."""
    html = _table(FRAME)
    for column in FRAME.columns:
        assert f"<code>{column}</code>" in html


def test_a_long_name_is_not_truncated():
    """short_name_for clips at about 34 characters for tile headers, which would
    render "Road-stream crossings (StreamCat..." in a column with room for all
    of it."""
    frame = pd.DataFrame({"rdcrsws": [0.02, 0.016, 0.006, 0.012]})
    name = _cells(_table(frame))["rdcrsws"]
    assert name.startswith("Road-stream crossings")
    assert not name.endswith("...")
