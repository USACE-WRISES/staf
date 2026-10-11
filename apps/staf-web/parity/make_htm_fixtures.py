"""Reference renderings of py-htmltools, for the static app's copy of its rules (src/app/htm.ts).

Each case is a tree written as JSON (``["div", {"class": "x"}, "text", ["span", {}, "a"]]``: a tag's
name, its attributes, then its children; ``{"html": "..."}`` is raw markup; a list of trees whose
first item is ``"taglist"`` is a TagList), rendered with htmltools as Shiny renders a ui output.
Writes ``tests/fixtures/htm_ref.json``.

    .venv\\Scripts\\python apps/staf-web/parity/make_htm_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

from htmltools import HTML, TagList

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "htm_ref.json"
ICON = ('<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 2h12"/></svg>')

CASES = [
    ["div", {"class": "a"}, "text"],
    ["div", {"class": "a"}],
    ["input", {"type": "range", "min": "0", "value": "8", "data-fid": "x", "class": "sfari-fscore"}],
    ["div", {"class": "staf-metric-ev"}, ["span", {"class": "staf-ev-tag"}, "Desktop"],
     ["b", {"class": "staf-ev-val"}, "1.4% <x> & \"y\""],
     ["span", {"onclick": "event.preventDefault();", "tabindex": "0", "data-tip": "it's \"q\" <b>", "class": "easi-info"}, "i"],
     ["span", {"class": "staf-suggest"}, "Suggested: ", ["b", {}, "Agree"],
      ["button", {"data-mid": "m", "type": "button", "class": "use"}, "Use"]]],
    ["div", {"class": "staf-metric-acts"},
     ["button", {"type": "button", "data-staf-panel": "note", "class": "staf-act"}, {"html": ICON},
      ["span", {"class": "staf-act-label"}, "Note"], ["span", {"class": "staf-act-dot"}]]],
    ["select", {"data-mid": "m", "class": "staf-rate"}, ["option", {"value": ""}, "Rate…"],
     ["option", {"value": "A", "selected": "selected"}, "A"], ["option", {"value": "B"}, "B"]],
    ["div", {"data-panel": "photo", "class": "p"},
     ["div", {"data-mid": "m", "class": "sfari-photos"},
      ["label", {"class": "sfari-photo-btn"}, {"html": ICON}, "Add photo",
       ["input", {"type": "file", "data-mid": "m", "class": "sfari-photo"}]]]],
    ["div", {"class": "outer"}, ["div", {"class": "inner"}, ["div", {"class": "deep"}, "x", ["span", {}, "y"], "z"]],
     ["p", {"class": "s"}, "statement"], ["textarea", {"data-fid": "f", "class": "n"}, ""]],
    ["taglist", ["div", {"class": "a"}, "one"], ["span", {}, "two"], ["span", {}, "three"], ["div", {}, "four"]],
    ["div", {"class": "x"}, "lead ", ["b", {}, "bold"], " tail", ["div", {}, "block"], "after"],
    ["div", {"class": "x"}, ["span", {"class": "y"}], ["div", {"class": "z"}]],
    ["span", {"class": "outer"}, ["div", {"class": "in"}, "a"], ["span", {}, "b"]],
]


def build(node):
    """The htmltools object a JSON tree describes: tags as ``htmltools.tags.<name>(*children, attrs)``,
    so each keeps its own default whitespace rule."""
    from htmltools import tags
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return HTML(node["html"])
    if node[0] == "taglist":
        return TagList(*[build(c) for c in node[1:]])
    name, attrs, *kids = node
    return getattr(tags, name)(*[build(c) for c in kids], attrs)


def main() -> int:
    out = [{"tree": c, "html": TagList(build(c)).get_html_string()} for c in CASES]
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(out)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
