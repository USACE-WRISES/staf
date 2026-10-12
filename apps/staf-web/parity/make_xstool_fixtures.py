"""Reference outputs for SFARI's cross-section hydraulics tool (sfari/xscalc.py and app.py's _xs_svg,
_xs_modal, xs_view and _xs_attach).

Writes ``tests/fixtures/xstool_ref.json``: for a few bankfull geometries, the synthetic section, the
hydraulics at bankfull and at the stage a target discharge needs (with each zone), the dialog's markup,
and for a few sets of box values the view (drawing and table) and the four evidence entries Attach
writes. The app's server functions are pure string builders, read from app.py without starting it.
Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/make_xstool_fixtures.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "apps" / "sfari"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "xstool_ref.json"


def _app_functions(names):
    """Module-level functions of app.py, compiled on their own (app.py is not imported)."""
    tree = ast.parse((APP / "app.py").read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    mod = ast.Module(body=body, type_ignores=[])
    ns: dict = {}
    exec(compile(mod, "app.py", "exec"), ns)
    return ns


def _server_functions():
    """The server's _xs_modal, xs_view's body, _tau_c and _xs_attach's entries, as plain functions."""
    from htmltools import TagList
    from shiny import ui
    from shiny._namespaces import namespace_context

    from sfari import xscalc
    from sfari.pipeline import XS_MANNING_SOURCE

    fns = _app_functions({"_xs_svg", "_interp_series"})

    def html(tag):
        return TagList(tag).tagify().get_html_string()

    def tau_c(d50_mm):
        d50_ft = (d50_mm or 0) / 304.8
        return 0.047 * 1.65 * 62.4 * d50_ft if d50_ft > 0 else None

    def modal(geom):
        slope = geom.get("slope") or 0.001
        bf = xscalc.compute(geom["points"], geom["bankfull_stage"], slope, 0.035,
                            n_lob=0.06, n_rob=0.06, lb=geom["lb"], rb=geom["rb"])
        q_bf = int(round(bf["Q"])) if bf and bf.get("Q") else 100
        with namespace_context("sfari"):
            return html(ui.modal(
                ui.div(f"Synthetic bankfull section: {geom['division_name']} regional curve "
                       f"(Bieger 2015) at DA {geom['da']:.0f} km²: bankfull width "
                       f"{geom['width_m']:.1f} m, mean depth {geom['depth_m']:.2f} m. Adjust slope, "
                       f"roughness, and target discharge; results feed the hydraulics metrics.",
                       class_="easi-instr"),
                ui.row(
                    ui.column(4, ui.input_numeric("xs_slope", "Channel slope (m/m)",
                                                  value=round(float(slope), 5), min=0.00001, step=0.0005)),
                    ui.column(4, ui.input_numeric("xs_nchan", "Manning n (channel)", value=0.035, step=0.005)),
                    ui.column(4, ui.input_numeric("xs_nover", "Manning n (overbank)", value=0.06, step=0.005)),
                ),
                ui.row(
                    ui.column(6, ui.input_numeric("xs_targetq", "Target discharge Q (cfs)",
                                                  value=q_bf, min=0, step=1)),
                    ui.column(6, ui.input_numeric("xs_d50", "Bed D50 (mm)", value=30.0, min=0, step=1)),
                ),
                ui.output_ui("xs_view"),
                ui.div(ui.tags.button("Attach results to hydraulics metrics",
                                      {"data-xs-attach": "1", "type": "button"}, class_="sfari-btn primary"),
                       class_="easi-pane-actions"),
                title="Cross-section hydraulics", easy_close=True, size="l",
                footer=ui.modal_button("Close")))

    def calc(geom, boxes):
        try:
            slope = float(boxes["slope"]); nchan = float(boxes["nchan"])
            nover = float(boxes["nover"]); tq = float(boxes["targetq"])
        except Exception:
            return None
        if slope <= 0 or tq <= 0 or nchan <= 0:
            return None
        stage = xscalc.solve_stage(geom["points"], tq, slope, nchan, n_lob=nover, n_rob=nover, lb=geom["lb"], rb=geom["rb"])
        if stage is None:
            return None
        return xscalc.compute(geom["points"], stage, slope, nchan, n_lob=nover, n_rob=nover, lb=geom["lb"], rb=geom["rb"])

    def view(geom, boxes):
        res = calc(geom, boxes)
        stage = res["stage"] if res else geom["bankfull_stage"]
        img = ui.HTML(f'<div style="margin-bottom:8px;">{fns["_xs_svg"](geom["points"], stage, geom["lb"], geom["rb"])}</div>')
        if not res:
            return html(ui.div(img, ui.p("Enter a valid slope, roughness, and discharge.", class_="text-muted", style="font-size:12px;")))
        try:
            d50 = float(boxes["d50"] or 0)
        except Exception:
            d50 = 0.0
        tc = tau_c(d50)
        rows = [
            ("Discharge Q", f"{res['Q']:.0f} cfs"),
            ("Water-surface stage / max depth", f"{res['depth_max']:.2f} ft"),
            ("Mean velocity V", f"{res['V']:.2f} ft/s"),
            ("Top width T", f"{res['T']:.1f} ft"),
            ("Hydraulic radius R", f"{res['R']:.2f} ft"),
            ("Bed shear τ = γRS", f"{res['tau']:.3f} lb/ft²"),
            ("Unit stream power τV", f"{res['power']:.2f} lb/(ft·s)"),
            ("Froude number", f"{res['froude']:.2f} ({'super' if res['froude'] > 1 else 'sub'}critical)"),
        ]
        if tc:
            rows.append(("Critical shear τc (D50)", f"{tc:.3f} lb/ft² → {'mobilizes' if res['tau'] > tc else 'stable'}"))
        table = ui.tags.table(ui.tags.tbody(*[ui.tags.tr(ui.tags.td(a), ui.tags.td(ui.tags.b(b))) for a, b in rows]), class_="easi-tbl")
        return html(ui.div(img, table))

    def attach(geom, boxes):
        res = calc(geom, boxes)
        if not res:
            return None
        try:
            d50 = float(boxes["d50"] or 0)
        except Exception:
            d50 = 0.0
        tc = tau_c(d50)
        q = res["Q"]
        ev = {}

        def put(mid, vt, note, fvt=""):
            ev[mid] = {"metric_id": mid, "value_text": vt, "field_value_text": fvt, "suggested_likert": None, "confidence": "M",
                       "source": XS_MANNING_SOURCE, "source_url": "", "status": "ok", "note": note}
        put("low-flow-baseflow-dynamics-low-flow-depth", f"modeled max depth {res['depth_max']:.2f} ft at Q {q:.0f} cfs",
            "From the cross-section Manning solver.", f"Low-flow depth {res['depth_max']:.2f} ft")
        put("low-flow-baseflow-dynamics-low-flow-velocity", f"modeled velocity {res['V']:.2f} ft/s at Q {q:.0f} cfs",
            "From the cross-section Manning solver.", f"Low-flow velocity {res['V']:.2f} ft/s")
        put("high-flow-dynamics-peak-flow-capacity-velocity-shear-stress",
            f"V {res['V']:.2f} ft/s, τ {res['tau']:.3f} lb/ft², Fr {res['froude']:.2f}", "At the modeled stage.",
            f"Peak V {res['V']:.2f} ft/s, Fr {res['froude']:.2f}")
        mob = f" → {'mobilizes' if res['tau'] > tc else 'stable'}" if tc else ""
        put("high-flow-dynamics-bed-mobilization-frequency",
            (f"bed shear τ {res['tau']:.3f} vs critical τc {tc:.3f} lb/ft²{mob}" if tc else f"bed shear τ {res['tau']:.3f} lb/ft²"),
            "Shields comparison with the entered D50.", f"Bed shear tau {res['tau']:.3f} lb/ft2")
        return ev

    return xscalc, modal, view, attach, calc


def main() -> int:
    sys.path.insert(0, str(APP))
    xscalc, modal, view, attach, calc = _server_functions()
    cases = []
    for width, depth, slope, da, name in ((3.12, 0.312, 0.01, 1.0005, "New England"), (25.4, 1.42, 0.0004, 812.7, "Coastal Plain"),
                                          (0.1, 0.05, None, 0.02, "National curve"), (8.75, 0.66, 0.0123456, 46.2, "Appalachian Plateau")):
        pts, lb, rb, stage = xscalc.synthetic_section(width, depth)
        geom = {"points": pts, "lb": lb, "rb": rb, "bankfull_stage": stage, "slope": slope or 0.001, "da": da,
                "width_m": width, "depth_m": depth, "division_name": name}
        bf = xscalc.compute(pts, stage, geom["slope"], 0.035, n_lob=0.06, n_rob=0.06, lb=lb, rb=rb)
        views = []
        for boxes in ({"slope": geom["slope"], "nchan": 0.035, "nover": 0.06, "targetq": round(bf["Q"]), "d50": 30.0},
                      {"slope": 0.002, "nchan": 0.05, "nover": 0.1, "targetq": 5000, "d50": None},
                      {"slope": 0.002, "nchan": 0.03, "nover": 0.03, "targetq": 0.5, "d50": "abc"},
                      {"slope": None, "nchan": 0.035, "nover": 0.06, "targetq": 10, "d50": 2},
                      {"slope": 0.001, "nchan": 0.035, "nover": 0.06, "targetq": -3, "d50": 30.0},
                      {"slope": "x", "nchan": 0.035, "nover": 0.06, "targetq": 10, "d50": 30.0}):
            res = calc(geom, boxes)
            views.append({"boxes": boxes, "html": view(geom, boxes), "evidence": attach(geom, boxes),
                          "stage": None if res is None else res["stage"], "Q": None if res is None else res["Q"]})
        cases.append({"width": width, "depth": depth, "geom": geom, "bankfull": bf, "modal": modal(geom), "views": views})
    OUT.write_text(json.dumps({"cases": cases}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
