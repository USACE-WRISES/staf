"""Optional local review page. Reads summaries and fits without changing evidence."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path

ENV_ROOT = "EASI_REVIEW_ROOT"
ENV_BASELINE = "EASI_REVIEW_BASELINE"
QUANTITY_LABELS = {
    "natural_wsrp100": "Natural land cover in the upstream riparian corridor (%)",
    "woody_wsrp100": "Woody land cover in the upstream riparian corridor (%)",
    "q_cv_monthly": "Monthly flow CV (dimensionless)",
    "er_median": "Entrenchment ratio (dimensionless)",
}
INDEX_LABELS = {"eci": "Ecosystem Condition Index", "physical": "Physical", "chemical": "Chemical", "biological": "Biological"}
TARGET_LABELS = {"bent_mmi": "Benthic macroinvertebrate MMI", "fish_mmi": "Fish MMI", "phab": "Physical habitat"}


def review_root() -> Path | None:
    value = os.environ.get(ENV_ROOT)
    if not value:
        return None
    root = Path(value).resolve()
    if not root.is_dir():
        raise ValueError(f"{ENV_ROOT} must name an existing local national data root")
    return root


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _rows(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))
    except (OSError, ValueError):
        return []


def _date(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return "Pending"


def _fresh_output(path: Path, meta_path: Path, meta: dict, method: str) -> bool:
    """A prior method or an output older than refreshed values remains stale."""
    try:
        return meta.get("method_version") == method and path.stat().st_mtime >= meta_path.stat().st_mtime
    except OSError:
        return False


def _number(value, digits=6) -> str:
    try:
        number = float(value)
        return f"{number:,.{digits}f}".rstrip("0").rstrip(".") if math.isfinite(number) else ""
    except (TypeError, ValueError):
        return ""


def _stat(value) -> str:
    try:
        number = float(value)
        return f"{number:,.3f}" if math.isfinite(number) else ""
    except (TypeError, ValueError):
        return ""


def _count(value) -> str:
    try:
        number = float(value)
        return f"{number:,.0f}" if math.isfinite(number) else ""
    except (TypeError, ValueError):
        return ""


def _label(value, names=None) -> str:
    value = str(value or "")
    return (names or {}).get(value, value.replace("_", " ").replace("-", " ").capitalize())


def _function_names(data_dir: Path) -> dict:
    try:
        rows = json.loads((data_dir / "functions.json").read_text(encoding="utf-8"))
        return {row["id"].replace("-", "_"): row["name"] for row in rows}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return '<p class="muted">Pending: no results available yet.</p>'
    head = "".join(f"<th scope='col'>{_e(label)}</th>" for _, label in columns)
    cell = lambda value: _e(f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else value)
    body = "".join("<tr>" + "".join(f"<td>{cell(row.get(key))}</td>" for key, _ in columns)
                   + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _pairs(points) -> list[tuple[float, float]]:
    out = []
    for point in points or []:
        try:
            x, y = (point.get("x"), point.get("y")) if isinstance(point, dict) else point[:2]
            x, y = float(x), float(y)
            if math.isfinite(x) and math.isfinite(y):
                out.append((x, y))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(out)


def curve_svg(points, title: str, quantiles: dict | None = None, axis_label="Physical value") -> str:
    """Plot the stored piecewise-linear points, without re-fitting or re-scoring."""
    pairs = _pairs(points)
    if len(pairs) < 2:
        return '<p class="muted">No fitted point series available.</p>'
    lo, hi = pairs[0][0], pairs[-1][0]
    if hi <= lo:
        return '<p class="muted">The fitted domain has no width.</p>'
    x = lambda value: 58 + 510 * (value - lo) / (hi - lo)
    y = lambda value: 224 - 184 * value
    parts = [f'<svg viewBox="0 0 600 280" role="img" aria-label="{_e(title)}">',
             f'<title>{_e(title)}. Stored physical value versus reference index.</title>']
    for bottom, top, color in ((0, .39, '#f8dada'), (.39, .69, '#fff0bc'), (.69, 1, '#dbe8f7')):
        parts.append(f'<rect x="58" y="{y(top):.2f}" width="510" height="{184 * (top-bottom):.2f}" fill="{color}"/>')
    for edge in (0, .39, .69, 1):
        parts.append(f'<line x1="58" x2="568" y1="{y(edge):.2f}" y2="{y(edge):.2f}" stroke="#7d8895" stroke-dasharray="3 3"/>'
                     f'<text x="49" y="{y(edge)+4:.2f}" text-anchor="end">{edge:g}</text>')
    line = " ".join(f"{x(px):.2f},{y(py):.2f}" for px, py in pairs)
    parts.append(f'<polyline points="{line}" fill="none" stroke="#1769aa" stroke-width="3"/>')
    for px, py in pairs:
        parts.append(f'<circle cx="{x(px):.2f}" cy="{y(py):.2f}" r="3" fill="#1769aa"><title>Fitted knot: {px:g}, {py:g}</title></circle>')
    for label, raw in (quantiles or {}).items():
        try:
            value = float(raw)
            if math.isfinite(value) and lo <= value <= hi:
                parts.append(f'<line x1="{x(value):.2f}" x2="{x(value):.2f}" y1="223" y2="233" stroke="#873e23" stroke-width="3">'
                             f'<title>Reference panel {_e(label)}: {value:g}</title></line>')
        except (TypeError, ValueError):
            pass
    for value, anchor in ((lo, "start"), ((lo+hi)/2, "middle"), (hi, "end")):
        parts.append(f'<text x="{x(value):.2f}" y="246" text-anchor="{anchor}">{_number(value, 4)}</text>')
    parts.append(f'<text x="313" y="271" text-anchor="middle">{_e(axis_label)}</text>'
                 '<text x="17" y="135" transform="rotate(-90 17 135)" text-anchor="middle">Reference index</text></svg>')
    return "".join(parts)


def _fit_card(title: str, curve: dict, *, diagnostic=False, axis_label="Physical value") -> str:
    if not curve:
        return f'<section><h3>{_e(title)}</h3><p class="muted">Pending or unavailable for this selection.</p></section>'
    values = {"n": _count(curve.get("n")), "members": _count(curve.get("n_members" if diagnostic else "nMembers")),
              "q25": _stat(curve.get("q25")), "q50": _stat(curve.get("q50")),
              "q75": _stat(curve.get("q75")), "x39": _number(curve.get("x39")),
              "x69": _number(curve.get("x69")), "status": curve.get("status"),
              "tier": curve.get("panel_tier" if diagnostic else "panelTier"), "screen": curve.get("screen")}
    points = curve.get("points")
    if diagnostic:
        try:
            points = json.loads(curve.get("points_json") or "[]")
        except ValueError:
            points = []
        values.update(usable=curve.get("usable"), reason=curve.get("reason"), rho=_stat(curve.get("rho_pressure")))
    columns = [("n", "Finite observations"), ("members", "Panel members"), ("q25", "Q25"),
               ("q50", "Median"), ("q75", "Q75"), ("x39", "Approx. x39"), ("x69", "Approx. x69"),
               ("status", "Status"), ("tier", "Panel tier"), ("screen", "Screen")]
    if diagnostic:
        columns += [("usable", "Usable"), ("reason", "Reason"), ("rho", "Pressure correlation")]
    knots = [{"x": _number(x), "y": _number(y)} for x, y in _pairs(points)]
    quantiles = {key: curve.get(key) for key in ("q25", "q50", "q75")}
    return (f'<section><h3>{_e(title)}</h3>{curve_svg(points, title, quantiles, axis_label)}'
            '<p class="muted">Blue points are fitted knots. Brown ticks show reference-panel quartiles where available.</p>'
            f'{_table([values], columns)}'
            '<details><summary>Stored fit points</summary>' + _table(knots, [("x", "Physical value"), ("y", "Reference index")])
            + '</details></section>')


def _diagnostic_fit(rows: list[dict], definition: dict, key: str) -> dict:
    slope = definition.get("stratifier") == "slope_class"
    level = "national" if slope or key == "national" else "l2"
    stratum = "national:national" if level == "national" else f"l2:{key}"
    split = key if slope and key != "national" else ""
    return next((row for row in rows if row.get("quantity") == definition.get("quantity")
                 and row.get("level") == level and row.get("stratum") == stratum
                 and (row.get("split") or "") == split), {})


def _comparison_table(rows, columns) -> str:
    formatted = []
    for row in rows or []:
        item = {}
        for key, _ in columns:
            value = row.get(key)
            if value is not None and key.endswith("share"):
                try:
                    value = f"{100 * float(value):.1f}%"
                except (TypeError, ValueError):
                    value = ""
            elif value is not None and ("mean" in key):
                value = _stat(value)
            elif key == "name" and value and "_" in str(value):
                value = _label(value)
            item[key] = value
        formatted.append(item)
    return _table(formatted, columns)


def _summary_rows(rows) -> list[dict]:
    formatted = []
    for row in rows:
        item = dict(row)
        for key in ("legacy", "current", "change"):
            value = row.get(key)
            if value is not None and row.get("unit") == "share":
                try:
                    item[key] = f"{100 * float(value):.1f}%"
                except (TypeError, ValueError):
                    item[key] = ""
            elif row.get("unit") == "eci":
                item[key] = _stat(value)
            elif row.get("unit") == "count":
                item[key] = _count(value)
        formatted.append(item)
    return formatted


def render_page(root: Path, data_dir: Path, criteria: str, method: str, selected="") -> str:
    """Read only small local JSON/CSV outputs. Missing rebuild outputs stay pending."""
    analysis = root / "analysis"
    function_names = _function_names(data_dir)
    manifest = _json(root / "staging/manifest.json")
    stats = _json(root / "staging/stats.json")
    meta_path = analysis / "values_meta.json"
    meta = _json(meta_path)
    registry = analysis / "curves/curve_registry.csv"
    validation_path = analysis / "validation/nrsa_validation.csv"
    report_path = analysis / "report/index.html"
    fresh = {"Diagnostic fits": _fresh_output(registry, meta_path, meta, method),
             "Field validation": _fresh_output(validation_path, meta_path, meta, method),
             "Analysis report": _fresh_output(report_path, meta_path, meta, method)}
    baseline = Path(os.environ[ENV_BASELINE]).resolve() if os.environ.get(ENV_BASELINE) else None
    baseline_manifest = _json(baseline / "staging/manifest.json") if baseline else {}
    baseline_stats = _json(baseline / "staging/stats.json") if baseline else {}
    artifact_path = data_dir / "reference-curves.json"
    artifact = _json(artifact_path)
    try:
        artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError:
        artifact_sha = "Pending"
    completion = _json(analysis / "local-review/completion.json")
    verified = (completion.get("status") == "complete" and completion.get("criteria_set") == criteria
                and completion.get("method_version") == method and bool(manifest.get("updated"))
                and manifest.get("method_version") == method and manifest.get("criteria_set") == criteria
                and completion.get("staging_updated") == manifest.get("updated")
                and completion.get("frozen_artifact_sha256") == artifact_sha
                and all((completion.get("checks") or {}).get(key) == "passed"
                        for key in ("analysis_audit", "comparison", "landscape_parity")))
    definitions = artifact.get("sets") or {}
    choices = {f"{sid}|{key}": (sid, key) for sid, definition in sorted(definitions.items())
               for key in sorted((definition.get("curves") or {}), key=lambda k: (k != "national", k))}
    selected = selected if selected in choices else next(iter(choices), "")
    current = manifest.get("method_version") == method and manifest.get("criteria_set") == criteria
    rows = [{"item": "Local rebuild checks", "value": f"Verified complete at {completion.get('completed_at', '')}" if verified
              else "Checks pending; outputs below may still be rebuilding"},
            {"item": "Active app", "value": f"{criteria} criteria; method {method}"},
            {"item": "Local staging", "value": f"{manifest.get('criteria_set', 'pending')} criteria; method {manifest.get('method_version', 'pending')}"},
            {"item": "Staging matches app", "value": "Yes" if current else "No, rebuild pending or a different method"},
            {"item": "Dataset vintage / build", "value": f"{manifest.get('vintage', 'pending')} / {manifest.get('updated', 'pending')}"},
            {"item": "Statistics built", "value": stats.get("generated", "Pending")},
            {"item": "Saved baseline", "value": f"method {baseline_manifest.get('method_version', 'pending')}; build {baseline_manifest.get('updated', 'pending')}"},
            {"item": "Analysis values", "value": f"method {meta.get('method_version', 'pending')}; built {meta.get('built_at', 'pending')}"}]
    rows += [{"item": name, "value": "Current method; output follows refreshed values" if ready
              else "Stale or unavailable; refreshed output pending"} for name, ready in fresh.items()]
    sections = ['<h1>Local EASI review</h1><p><a href="../">Open EASI</a>. Use <b>Nationwide screening</b> and '
                '<b>Dashboard</b> for the local map, reach reports, state comparisons and CSV export. '
                'This page is read-only. Refresh it as rebuild outputs become available.</p>',
                '<p><a href="alternatives/">Compare archived alternative studies</a>. The owner adopted Alternative 2 for the application. '
                'The historical analysis and Alternative 1 staging below remain preserved; they do not describe the active nationwide bundle. '
                'Use Nationwide screening and Dashboard for current Alternative 2 results.</p>',
                '<section><h2>Build provenance</h2>' + _table(rows, [("item", "Item"), ("value", "Value")]) + '</section>']
    indices = ((stats.get("groups") or {}).get("US") or {}).get("indices") or {}
    summary = []
    for name, values in indices.items():
        bands = values.get("bands") or [0, 0, 0]
        total = sum(bands)
        summary.append({"index": _label(name, INDEX_LABELS), "n": values.get("n"), "median": _stat(values.get("p50")),
                        "sd": _stat(values.get("sd")), **{key: f"{100*bands[i]/total:.1f}%" if total else ""
                        for i, key in enumerate(("nf", "ar", "f"))}})
    sections.append('<section><h2>Local staged results</h2>' + _table(summary, [("index", "Index"), ("n", "Reaches"),
        ("median", "Median"), ("sd", "SD"), ("nf", "Non-Functioning"), ("ar", "At-Risk"), ("f", "Functioning")]) + '</section>')
    comparison = _json(analysis / "local-review/comparison.json")
    comparison_rows = comparison.get("summary_rows") or comparison.get("summary") or []
    if isinstance(comparison_rows, dict):
        comparison_rows = [{"measure": key, "current": value} for key, value in comparison_rows.items()
                           if not isinstance(value, (dict, list))]
    sections.append('<section><h2>Saved baseline and rebuilt results</h2><p>Matched-reach comparisons show changes in criteria '
                    'and input evidence, with coverage changes reported separately. More score spread alone does not establish field accuracy.</p>'
                    '<p>The saved baseline and both current criteria sets use 13/8/3 scores for Good / Fair / Poor. '
                    'The legacy switch retains the previous criteria.</p>'
                    + _table(_summary_rows(comparison_rows), [("measure", "Measure"), ("legacy", "Baseline"),
                        ("current", "Rebuilt"), ("change", "Change")])
                    + ('<details><summary>Comparison provenance</summary><pre>' + _e(json.dumps(comparison.get("provenance") or {}, indent=2))
                       + '</pre></details><details><summary>Comparison validation</summary><pre>'
                       + _e(json.dumps(comparison.get("validation") or {}, indent=2)) + '</pre></details>' if comparison else ''))
    if baseline_stats and not comparison:
        sections.append('<p>The saved baseline is available. The matched-reach comparison is pending.</p>')
    sections.append('<details><summary>State comparisons</summary>' + _comparison_table(comparison.get("states"), [
        ("name", "State"), ("reaches", "Matched reaches"), ("legacy_eci_mean", "Baseline mean ECI"),
        ("current_eci_mean", "Rebuilt mean ECI"), ("eci_mean_change", "Mean change"),
        ("legacy_functioning_share", "Baseline Functioning"), ("current_functioning_share", "Rebuilt Functioning"),
        ("class_changed_share", "Class changed")]) + '</details>')
    sections.append('<details><summary>Function comparisons</summary>' + _comparison_table(comparison.get("functions"), [
        ("name", "Function"), ("reaches", "Matched reaches"), ("legacy_good_share", "Baseline Good"),
        ("current_good_share", "Rebuilt Good"), ("legacy_missing_share", "Baseline not rated"),
        ("current_missing_share", "Rebuilt not rated"), ("rating_changed_share", "Rating changed"),
        ("score_changed_share", "Score changed")]) + '</details></section>')
    options = "".join(f'<option value="{_e(value)}"{" selected" if value == selected else ""}>{_e(sid)} / {_e(key)}</option>'
                      for value, (sid, key) in choices.items())
    sections.append('<section><h2>Reference fits</h2><form method="get"><label for="curve">Quantity and reference region</label> '
                    f'<select id="curve" name="curve">{options}</select> <button type="submit">Show fit</button></form>'
                    '<p>The frozen regional artifact supplies scoring curves. Its interpolated reference index is banded at '
                    '0.39 and 0.69, then Good / Fair / Poor maps to 0.85 / 0.545 / 0.195 and rounds to 13/8/3 scores. Displayed physical crossings are approximate. '
                    'Diagnostic fits are regenerated analysis outputs and do not change the scoring artifact.</p>'
                    + ('<p class="status">Legacy criteria are active; the regional fits below are not used by this app session.</p>'
                       if criteria != "regional" else ''))
    if not fresh["Diagnostic fits"]:
        sections.append('<p class="status">Refreshed diagnostic fits are pending. Any diagnostic fit shown below is a '
                        'stored result that has not been confirmed for the current values method and build.</p>')
    if selected:
        sid, key = choices[selected]
        definition = definitions[sid]
        axis_label = QUANTITY_LABELS.get(definition.get("quantity"), definition.get("quantity") or "Physical value")
        diagnostic = _diagnostic_fit(_rows(registry), definition, key)
        location = ("National fallback, used when regional identity or a usable regional fit is unavailable."
                    if key == "national" else f"Reference stratum: {key}. A missing or unusable selection falls back nationally.")
        sections.append(f'<p>{_e(location)}</p>')
        sections.append('<div class="grid">' + _fit_card("Frozen scoring fit", definition["curves"][key], axis_label=axis_label)
                        + _fit_card("Regenerated diagnostic fit", diagnostic, diagnostic=True, axis_label=axis_label) + '</div>')
    sections.append(f'<p>Frozen artifact SHA-256: <code>{artifact_sha}</code>. Diagnostic registry modified: {_e(_date(registry))}.</p>'
                    '<details><summary>Frozen fit provenance</summary><pre>' + _e(json.dumps(artifact.get("provenance") or {}, indent=2))
                    + '</pre></details></section>')
    agreement = [r for r in _rows(validation_path)
                 if r.get("run") == "S0" and r.get("region") == "US" and not r.get("subject", "").startswith("cand__")]
    agreement = [{**row, "subject": _label(row.get("subject"), function_names),
                  "target": _label(row.get("target"), TARGET_LABELS), "n": _count(row.get("n")),
                  "n_class": _count(row.get("n_class")),
                  **{key: _stat(row.get(key)) for key in ("auc_poor", "auc_poor_lo", "auc_poor_hi", "rho", "kappa")}}
                 for row in agreement]
    sections.append('<section><h2>Field agreement and diagnostic report</h2><p>These analysis rows describe the values method '
                    'and build shown above. Check that method before treating them as rebuilt results. Low-flow CV remains an '
                    'unvalidated proxy; agriculture-based functions share correlated evidence.</p>'
                    '<p>Poor-class AUC measures ranking: 0.5 is chance and 1 is perfect ranking in the matched sample. '
                    'Rank correlation ranges from -1 to 1; values near zero show weak monotonic association. '
                    'Class agreement is chance-adjusted kappa. These diagnostics do not establish causal effects or '
                    'accuracy outside the validation sample. Desktop sites have a finite desktop index; AUC and kappa '
                    'use class-matched sites. Paired counts may differ for each continuous field target used in correlation.</p>'
                    + ('<p class="status">Refreshed field validation is pending. The rows below, if present, are stale '
                       'or unconfirmed for this values build.</p>' if not fresh["Field validation"] else '')
                    + _table(agreement, [("subject", "Function"), ("target", "Field target"), ("n", "Desktop sites"),
                        ("n_class", "Class-matched sites"),
                        ("auc_poor", "Poor-class AUC"), ("auc_poor_lo", "AUC lower bound"), ("auc_poor_hi", "AUC upper bound"),
                        ("rho", "Rank correlation"), ("kappa", "Class agreement"), ("verdict", "Validation status")]))
    if report_path.is_file():
        sections.append(f'<p><a href="report/index.html" target="_blank" rel="noopener">Open analysis report and scorecards</a>. '
                        f'Report modified: {_e(_date(report_path))}. The report includes scenario comparisons; '
                        'the active criteria remain the approved regional criteria.</p>')
        if not fresh["Analysis report"]:
            sections.append('<p class="status">The linked report is stale or unconfirmed for this values build. '
                            'The refreshed report is pending.</p>')
    else:
        sections.append('<p class="muted">The refreshed analysis report is pending.</p>')
    sections.append('</section>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Local EASI review</title><style>' + STYLE + '</style></head><body><main>' + ''.join(sections) + '</main></body></html>')


def routes(root: Path | None):
    """Mount only when explicitly enabled; report assets cannot escape their folder."""
    if root is None:
        return []
    from starlette.responses import FileResponse, HTMLResponse, Response
    from starlette.routing import Route

    async def page(request):
        if not _loopback(request):
            return Response(status_code=403)
        import anyio
        from easi import config
        from easi.national import method_version
        content = await anyio.to_thread.run_sync(render_page, root, config.DATA_DIR, config.criteria_set(),
                                                method_version(), request.query_params.get("curve", ""))
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

    async def report_asset(request):
        if not _loopback(request):
            return Response(status_code=403)
        folder = (root / "analysis/report").resolve()
        path = (folder / request.path_params["path"]).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() not in {".html", ".png", ".svg", ".css"}:
            return Response(status_code=404)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    from alternative_review import routes as alternative_routes
    return [Route("/local-review/", page), Route("/local-review/report/{path:path}", report_asset), *alternative_routes(root)]


def _loopback(request) -> bool:
    import ipaddress
    try:
        return request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


STYLE = """
body{margin:0;background:#f4f6f9;color:#26324a;font:15px/1.5 system-ui,sans-serif}
main{max-width:1400px;margin:auto;padding:24px}h1{margin-top:0}h2{font-size:1.35rem}h3{font-size:1.1rem}
section{background:white;border:1px solid #d9e0e9;border-radius:8px;margin:18px 0;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,450px),1fr));gap:16px}.grid section{min-width:0}
svg{width:100%;max-height:330px}svg text{font:12px system-ui,sans-serif}.table-wrap{overflow:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px;border-bottom:1px solid #e0e5ec;text-align:left}
th{background:#f3f6fa;white-space:nowrap}a{color:#1769aa}select,button{font:inherit;padding:8px;border:1px solid #8290a2;border-radius:4px}
button{background:#1769aa;color:white;cursor:pointer}select{max-width:100%}label{font-weight:600}summary{cursor:pointer;padding:8px 0}
pre{overflow:auto;white-space:pre-wrap;word-break:break-word}code{overflow-wrap:anywhere}.muted{color:#5d6878}.status{background:#fff0bc;padding:12px}
:focus-visible{outline:3px solid #1769aa;outline-offset:3px}
"""
