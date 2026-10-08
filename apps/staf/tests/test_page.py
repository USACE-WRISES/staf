"""The STAF page: one header, one section per tool, the shown tool chosen by the address."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from htmltools import HTMLDocument
from starlette.requests import Request

from staf_shell import shell_ui
from staf_shell.loader import ORDER


class _Tags(HTMLParser):
    """Every start tag of a page, with its attributes, and the page's visible text."""

    def __init__(self, html: str):
        super().__init__()
        self.tags, self.spans, self.text, self._open = [], [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict((k, v or "") for k, v in attrs)
        self.tags.append((tag, attrs))
        if tag == "span":
            self.spans.append([attrs, ""])
            self._open.append(self.spans[-1])

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict((k, v or "") for k, v in attrs)))

    def handle_endtag(self, tag):
        if tag == "span" and self._open:
            self._open.pop()

    def handle_data(self, data):
        self.text.append(data)
        for span in self._open:
            span[1] += data


def _page(staf, query: str = "") -> str:
    request = Request(dict(type="http", method="GET", path="/", query_string=query.encode(), headers=[]))
    return HTMLDocument(shell_ui.make_ui(staf.TOOLS)(request)).render()["html"]


def _shown(staf, key: str) -> str:
    return key if staf.TOOLS[key].ok else next(k for k in ORDER if staf.TOOLS[k].ok)


@pytest.mark.parametrize("query, key", [("", "easi"), ("tool=sfari", "sfari"), ("tool=DEEP", "deep"),
                                        ("assessment=reach-1%402", "deep"), ("tool=bogus", "easi"),
                                        ("tool=sfari&assessment=reach-1%402", "sfari")])
def test_the_address_picks_the_tool_shown(staf, query, key):
    shown = _shown(staf, key)
    html = _page(staf, query)
    tags = _Tags(html).tags
    assert f'setAttribute("data-staf-tool", "{shown}")' in html
    active = [a["data-tool"] for t, a in tags if t == "button" and "active" in a.get("class", "").split()]
    assert active == [shown]
    for tag, attrs in tags:
        if tag == "link" and "data-staf-css" in attrs:
            assert attrs["media"] == ("all" if attrs["data-staf-css"] == shown else "not all"), attrs
    assert f"<title>{staf.TOOLS[shown].name} · STAF</title>" in html


def test_the_bar_names_the_framework_and_lights_the_tier_while_full_names_stay_in_tooltips(staf):
    bar = _Tags(str(shell_ui.header(staf.TOOLS, "sfari")))
    tiers = [(a.get("data-staf-tool"), text) for a, text in bar.spans if "staf-tier" in a.get("class", "").split()]
    assert tiers == [("easi", "Screening"), ("sfari", "Rapid"), ("deep", "Detailed")]
    assert sum("staf-tier-sep" in a.get("class", "") for a, _ in bar.spans) == 2
    assert not any("staf-subtitle" in a.get("class", "") for _, a in bar.tags)
    visible = " ".join(bar.text)
    for key in ORDER:
        tool = staf.TOOLS[key]
        assert tool.full_name not in visible, tool.full_name           # the tooltips carry it
        button = next(a for t, a in bar.tags if t == "button" and a.get("data-tool") == key)
        assert tool.full_name in button["aria-label"] and f"{tool.tier} tier" in button["aria-label"]
        if tool.ok:
            assert button["title"] == tool.full_name
    assert staf.TOOLS["sfari"].full_name == "Stream Functions Assessment and Rapid Index"


def test_on_a_phone_the_bar_drops_the_tier_before_it_reaches_the_switch(staf):
    """Owner, 2026-10-05: at 375px "Detailed" ran into the switch. "Screening" needs about 412px
    beside the brand, so at 440px and below the tier goes, in every tool; the switch names it."""
    css = (staf.HERE / "www" / "shell" / "staf-shell.css").read_text(encoding="utf-8")
    assert re.search(r"@media \(max-width: 440px\) \{\s*\.staf-tiers \{ display: none; \}", css)
    assert 'href="shell/staf-shell.css?v=3"' in _page(staf, "tool=deep")


def test_every_tool_shows_the_same_header_actions(staf):
    """Owner, 2026-10-05: EASI, SFARI and DEEP share one set of actions on the right of the bar
    (New, Open, Save, About and Help), the same markup under each tool's own id prefix."""
    navs = dict((key, re.sub(rf'\b(id|for|name)="{key}-', r'\1="',
                             str(staf.TOOLS[key].module.tool_nav_ui(key, prefix=f"{key}/"))))
                for key in ORDER if staf.TOOLS[key].ok)
    assert len(navs) >= 2 and len(set(navs.values())) == 1, sorted(navs)
    text = re.sub(r"<[^>]+>", " ", next(iter(navs.values())))
    assert re.findall(r"\b(New|Open|Save|About|Help)\b", text) == ["New", "Open", "Save", "About", "Help"]


def test_no_id_appears_twice_and_each_tool_keeps_to_its_prefix(staf):
    tags = _Tags(_page(staf, "tool=sfari")).tags
    ids = [a["id"] for _, a in tags if a.get("id")]
    assert len(ids) == len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1)
    for key in ORDER:
        tool = staf.TOOLS[key]
        if not tool.ok:
            continue
        pieces = [shell_ui.section(tool), *shell_ui._slots(staf.TOOLS, "tool_nav_ui", "staf-nav"),
                  *shell_ui._slots(staf.TOOLS, "tool_center_ui", "staf-center")]
        for piece in pieces:
            html = str(piece)
            if f'data-staf-tool="{key}"' not in html:
                continue
            stray = [i for _, a in _Tags(html).tags if (i := a.get("id")) and not i.startswith(f"{key}-")]
            assert stray == [], (key, stray)


def test_each_script_loads_once_shared_namespacing_first(staf):
    tags = _Tags(_page(staf, "tool=sfari")).tags
    scripts = [a["src"] for t, a in tags if t == "script" and a.get("src") and not a["src"].startswith("lib/")]
    assert len(scripts) == len(set(scripts)), scripts
    assert scripts[0] == "staf/staf-ns.js?v=1"
    assert scripts[-1] == "shell/staf-shell.js?v=3"


def test_every_asset_the_page_names_is_served(staf):
    mounts = shell_ui.static_assets(staf.TOOLS, staf.HERE / "www")
    for tag, attrs in _Tags(_page(staf, "tool=sfari")).tags:
        url = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" else None
        if not url or url.startswith(("lib/", "http", "data:", "#")):
            continue
        prefix, rest = url.split("?", 1)[0].split("/", 1)
        assert f"/{prefix}" in mounts, url
        assert (Path(mounts[f"/{prefix}"]) / rest).is_file(), url


def test_the_mounts_never_cover_the_root(staf):
    mounts = shell_ui.static_assets(staf.TOOLS, staf.HERE / "www")
    assert set(mounts) == {"/shell", "/staf", "/easi", "/sfari", "/deep"}
    assert all(Path(folder).is_dir() and Path(folder).is_absolute() for folder in mounts.values())


def test_a_tool_that_cannot_run_here_points_to_its_calculator(staf):
    """The standalone apps are retired (2026-10-08): a tool STAF cannot load points to its
    spreadsheet calculator on the site's Apply STAF page, never back into STAF."""
    from staf_shell.loader import SITE, Tool
    for key in ORDER:
        tool = Tool(key, staf.TOOLS[key].root, error="RuntimeError: boom")     # no module: not ok
        html = str(shell_ui.section(tool))
        assert f"{tool.name} is not available here" in html and "RuntimeError: boom" in html
        assert tool.calculator_page == f"{SITE}tools/#{key}" and f'href="{tool.calculator_page}"' in html
        assert f'href="{SITE}{key}/"' not in html and "standalone" not in html
    for key in ORDER:
        if not staf.TOOLS[key].ok:
            assert re.search(rf'data-tool="{key}"[^>]*disabled|disabled[^>]*data-tool="{key}"', _page(staf))
