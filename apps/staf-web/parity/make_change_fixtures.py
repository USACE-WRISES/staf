"""Reference markup for DEEP's Change dialog (app.py _change_assessment and _assess_cards_ui).

Writes ``tests/fixtures/change_ref.json``: the dialog for a few covering lists (one assessment with one
version, several versions with statuses DEEP labels and one it does not, reference tiers present and
missing, the selected version's card, no covering assessment), rendered with py-shiny. The card logic
is app.py's, read from its source (the server's closures need a session): ``_assess_cards_ui``'s body
runs here on the same helpers. Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/make_change_fixtures.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "apps" / "deep"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "change_ref.json"


def _module_functions(names, extra_ns):
    tree = ast.parse((APP / "app.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign)) and (
        (isinstance(n, ast.FunctionDef) and n.name in names)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in names for t in n.targets)))]
    ns = dict(extra_ns)
    exec(compile(ast.Module(body=body, type_ignores=[]), "app.py", "exec"), ns)
    return ns


def _cards_function():
    """_assess_cards_ui from the server's source, as a function of (covering, selected)."""
    src = (APP / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    server = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "server")
    fn = next(n for n in ast.walk(server) if isinstance(n, ast.FunctionDef) and n.name == "_assess_cards_ui")
    fn.args.args.append(ast.arg(arg="sel_value"))
    # selected_ref() is the server's reactive value: the fixture passes it in
    text = ast.unparse(fn).replace("sel = selected_ref()", "sel = sel_value")
    return text


def main() -> int:
    sys.path.insert(0, str(APP))
    from htmltools import TagList
    from shiny import ui
    from shiny._namespaces import namespace_context

    from deep import session
    helpers = _module_functions({"_BADGE_CLASS", "_badge_class", "_TIER_LABELS", "_tier_label"}, {"session": session})
    ns = dict(helpers, ui=ui, session=session, _MAX_ASSESS_CARDS=16)
    exec(_cards_function(), ns)
    cards = ns["_assess_cards_ui"]

    def html(tag):
        return TagList(tag).tagify().get_html_string()

    def entry(aid, name, region, versions, default, tiers=None):
        refs = [f"{aid}@v{v}" for v, _ in versions]
        dref = f"{aid}@v{default}"
        refs = [dref] + [r for r in refs if r != dref]
        return {"assessmentId": aid, "assessmentName": name, "regionName": region, "defaultRef": dref, "refs": refs,
                "lifecycleByRef": dict((f"{aid}@v{v}", s) for v, s in versions),
                "versionByRef": dict((f"{aid}@v{v}", v) for v, _ in versions),
                "referenceTierByRef": dict((f"{aid}@v{v}", (tiers or {}).get(v)) for v, _ in versions),
                "hasCertified": any(s == "certified" for _, s in versions)}

    lists = {
        "one": [entry("neh", "Northeastern Highlands", "Northeastern Highlands", [(1, "preliminary")], 1, {1: "least_disturbed"})],
        "many": [entry("neh", "Northeastern Highlands & co", "Region <1>", [(3, "certified"), (2, "preliminary"), (1, "draft")], 3,
                       {3: "best_available", 2: "least_disturbed"}),
                 entry("ecbp", "Eastern Corn Belt Plains", "", [(1, "draft"), (2, "weird_status")], 2, {1: "minimally_disturbed", 2: "odd_tier"})],
        "none": [],
    }
    out = []
    with namespace_context("deep"):
        for name, covering in lists.items():
            for sel in (None, covering[0]["refs"][-1] if covering else None):
                body = cards(covering, sel) if covering else ui.p("No assessment covers this point.")
                dialog = ui.modal(body, title="Choose a detailed assessment", size="l", easy_close=True, footer=ui.modal_button("Cancel"))
                out.append({"name": name, "selected": sel, "covering": covering, "html": html(dialog)})
    OUT.write_text(json.dumps({"dialogs": out}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
