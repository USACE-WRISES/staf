"""Where each curve an assessment scores comes from, in words (pure).

The workspace draws every curve a version scores, and a reader has to be able to
tell what each one rests on: this build's own stations, a curve carried forward
from an earlier version, comparable stations from the national pool, a modeled
expectation, a published criterion, or a fixed criterion. This module is the one
vocabulary for that. The gallery's legend and tiles, the mapping chips and the
source panel read from it: a kind per curve with its label, icon and one
sentence, the facts that name the specific source, the trail of sources the
build tried before it, and the curve's breakpoints.

It reads a ``pressure_evidence.reference_rows`` entry and, when the opened
version carries one, its provenance document. No shiny. Every string here is
user-visible, so none carries an em dash.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional

from . import curve_basis, curve_svg, methodology, model_registry

#: one entry per kind of curve: the badge label, the Font Awesome icon (checked
#: against faicons by the tests) and the sentence the panel and legend print
KINDS: dict[str, dict] = {
    "built": {
        "label": "Built here", "icon": None,
        "sentence": ("Fitted in this build from least-disturbed stations of this ecoregion "
                     "or of a comparable wider region.")},
    "carried": {
        "label": "Carried forward", "icon": "clock-rotate-left",
        "sentence": ("Reused unchanged from an earlier version of this assessment. That "
                     "version decided its source.")},
    "national": {
        "label": curve_basis.label_for(curve_basis.NATIONAL), "icon": "globe",
        "sentence": curve_basis.statement_for(curve_basis.NATIONAL)},
    "modeled": {
        "label": curve_basis.label_for(curve_basis.MODELED), "icon": "chart-line",
        "sentence": curve_basis.statement_for(curve_basis.MODELED)},
    "published_benchmark": {
        "label": curve_basis.label_for(curve_basis.PUBLISHED), "icon": "book",
        "sentence": curve_basis.statement_for(curve_basis.PUBLISHED)},
    "fixed": {
        "label": "Fixed criterion", "icon": "ruler",
        "sentence": "Scored on cited thresholds that apply the same way in every region."},
    "owner_entered": {
        "label": curve_basis.label_for(curve_basis.OWNER), "icon": "user-pen",
        "sentence": ("Thresholds or breakpoints the owner entered, on a cited source or on "
                     "professional judgment.")},
    "borrowed": {
        "label": "From another assessment", "icon": "arrow-right-arrow-left",
        "sentence": ("Another STAF assessment's curve for the same metric, chosen by the owner "
                     "without a comparability check.")},
    "owner_exception": {
        "label": "Owner exception", "icon": "scale-balanced",
        "sentence": "A source the build refused, which the owner accepted with a rationale."},
    "not_selected": {
        "label": "Not selected here", "icon": "circle-minus",
        "sentence": ("Fitted in this build, but the two-per-function rule left it out of "
                     "this function.")},
}
KINDS["sqt"] = {
    "label": "State SQT", "icon": "landmark",
    "sentence": ("A curve published in a state Stream Quantification Tool, chosen by the owner "
                 "with its verification status and applicability checks, and scored on DEEP's "
                 "bands.")}
KIND_ORDER = ("built", "carried", "national", "modeled", "published_benchmark", "fixed",
              "owner_entered", "borrowed", "sqt", "owner_exception", "not_selected")

#: the rule under which the build chose each kind of curve
KIND_RULES = {"carried": "REF-05", "national": "REF-12", "modeled": "REF-13",
              "published_benchmark": "REF-14", "fixed": "CURVE-11"}

#: the sources after the station pools, in the order the build tries them
LADDER_RULES = ("REF-12", "REF-13", "REF-14")
RUNG_NAMES = {"REF-12": "National reference", "REF-13": "Modeled reference",
              "REF-14": "Published benchmark"}
NATIONAL_OPTIONS = {"3c_matched": "Matched donor stations",
                    "3a_envelope": "Stations inside the comparability envelope"}
CRITERION_KINDS = ("published_benchmark", "fixed")

USED, REFUSED, NOT_TRIED = "Used", "Refused", "Not tried"


def kind_meta(kind: Any) -> dict:
    """``{label, icon, sentence}`` of a kind; an unknown kind reads as its own name."""
    meta = KINDS.get(str(kind or ""))
    if meta:
        return dict(meta)
    text = str(kind or "").replace("_", " ").strip()
    return {"label": text[:1].upper() + text[1:], "icon": None, "sentence": ""}


def kind_label(kind: Any) -> str:
    return kind_meta(kind)["label"]


def kind_icon(kind: Any) -> Optional[str]:
    return kind_meta(kind)["icon"]


def kind_sentence(kind: Any) -> str:
    return kind_meta(kind)["sentence"]


# --------------------------------------------------------------------------- #
# small readers
# --------------------------------------------------------------------------- #
def _ann(entry: Mapping) -> dict:
    return dict((entry or {}).get("annotations") or {})


def _support(entry: Mapping) -> dict:
    sup = _ann(entry).get("referenceSupport")
    return dict(sup) if isinstance(sup, dict) else {}


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _count(v: Any) -> str:
    f = _num(v)
    return "" if f is None else f"{int(round(f)):,}"


def _records_for(metric: str, provenance: Optional[Mapping]) -> list[dict]:
    return [r for r in (provenance or {}).get("records") or []
            if str(r.get("subject")) == str(metric) and r.get("subject_kind", "metric") == "metric"]


def _record(metric: str, provenance: Optional[Mapping], rule_id: str) -> Optional[dict]:
    for r in _records_for(metric, provenance):
        if r.get("rule_id") == rule_id:
            return r
    return None


def _screen_detail(metric: str, provenance: Optional[Mapping], rule_id: str) -> dict:
    rec = _record(metric, provenance, rule_id) or {}
    detail = (rec.get("computed") or {}).get("screen_detail")
    return dict(detail) if isinstance(detail, dict) else {}


def _first_sentence(text: Any) -> str:
    t = " ".join(str(text or "").split())
    return t.split(". ")[0].rstrip(".") + "." if t else ""


def _approval_date(approval: Mapping) -> str:
    """The approval date. YAML 1.1 reads a bare ``on:`` key as the boolean True,
    so the registry's ``on`` arrives under either key."""
    for key, value in approval.items():
        if str(key).lower() in ("on", "true") and value not in (None, ""):
            return str(value)
    return ""


def _pool_words(sup: Mapping) -> str:
    """Where a station pool was drawn: this ecoregion, or a wider region by level."""
    status = str(sup.get("status") or "")
    if not status:
        return ""
    if status.startswith("local"):
        return "This ecoregion's own least-disturbed stations"
    level = str(sup.get("levelLabel") or "").strip()
    code = str(sup.get("regionCode") or "").strip()
    name = str(sup.get("regionName") or "").strip()
    where = " ".join(p for p in (level, code) if p)
    return (f"Borrowed from {where}" + (f" ({name})" if name else "")) if where else ""


def rule_name(rule_id: str) -> str:
    """The catalog's name of a rule, or an empty string for an unknown id."""
    try:
        return str(methodology.rule(rule_id).get("name") or "")
    except KeyError:
        return ""


def from_version_of(entry: Mapping, build: Optional[Mapping] = None) -> Any:
    """The version a carried curve comes from."""
    got = _ann(entry).get("carriedForward")
    if isinstance(got, dict) and got.get("fromVersion"):
        return got.get("fromVersion")
    return ((build or {}).get("carriedFrom") or {}).get("fromVersion")


def units_of(entry: Mapping) -> str:
    cfg = (entry or {}).get("config") or {}
    units = cfg.get("units")
    return "" if units is None or str(units).strip() in ("", "nan", "None") else str(units)


# --------------------------------------------------------------------------- #
# what the panel prints
# --------------------------------------------------------------------------- #
def source_title(metric: str, entry: Mapping, *, build: Optional[Mapping] = None) -> str:
    """The specific source in a few words: which version, which criterion, which
    pool or model."""
    kind = (entry or {}).get("kind")
    ann, sup = _ann(entry), _support(entry)
    owner = (entry or {}).get("owner")
    if owner and (owner.get("source") or {}).get("title"):
        return str(owner["source"]["title"])
    if kind == "carried":
        ver = from_version_of(entry, build)
        return f"Version {ver} of this assessment" if ver else "An earlier version of this assessment"
    if kind == "fixed":
        return str(ann.get("sourceCitation") or (ann.get("criteriaSource") or {}).get("title")
                   or kind_label(kind))
    if kind == "published_benchmark":
        src = ann.get("criteriaSource") or {}
        return str(src.get("title") or ann.get("sourceCitation") or kind_label(kind))
    if kind == "modeled":
        return "An approved model, fitted nationally with this ecoregion's own level"
    if kind == "national":
        n = _count(sup.get("nUsable"))
        return (f"{n} comparable stations from the national pool" if n
                else "Comparable stations from the national pool")
    return kind_label(kind)


def source_facts(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
                 build: Optional[Mapping] = None) -> list[tuple[str, str]]:
    """``[(label, value), ...]`` naming the specific source, most telling first.

    The facts come from the curve's own annotations (what the bundle states), the
    opened version's provenance (the catalog entry, the model's coverage, the
    national option) and the model registry (who approved the model, on what
    evidence)."""
    kind = (entry or {}).get("kind")
    ann, sup = _ann(entry), _support(entry)
    facts: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value not in (None, "", [], ()):
            facts.append((label, str(value)))

    owner = (entry or {}).get("owner")
    if owner:
        facts.extend(_owner_facts(metric, entry, owner))
        if kind not in ("published_benchmark", "carried"):
            return facts
    if kind in CRITERION_KINDS:
        src = ann.get("criteriaSource") or {}
        add("Criterion", source_title(metric, entry, build=build))
        add("Thresholds", "; ".join(f"{b.get('rating')} {b.get('label')}"
                                    for b in src.get("bands") or []
                                    if b.get("rating") and b.get("label")))
        if kind == "published_benchmark":
            pb = ann.get("publishedBenchmark") or {}
            add("Unit conversion", pb.get("unitConversion"))
            detail = _screen_detail(metric, provenance, "REF-14")
            if detail.get("catalogEntry"):
                add("Catalog entry", str(detail["catalogEntry"])
                    + (f", edition {detail['edition']}" if detail.get("edition") else ""))
        add("Citations", "; ".join(str(c.get("text")) for c in src.get("citations") or []
                                   if c.get("text")))
        if src.get("provisional"):
            add("Status", "Provisional: the source may revise these thresholds.")
    elif kind == "modeled":
        reg = model_registry.entry_for(metric)
        if reg:
            add("Model", _first_sentence(model_registry.procedure_of(reg).get("description")))
            approval = reg.get("approval") or {}
            if approval.get("by"):
                on = _approval_date(approval)
                add("Model approved", f"by {approval.get('by')}" + (f" on {on}" if on else ""))
            ev = reg.get("evidence") or {}
            if ev.get("verdict"):
                add("Evidence", (f"{str(ev['verdict']).capitalize()} in the recovery test, "
                                 f"{ev.get('n_cells')} test regions"))
        if _num(sup.get("nUsable")):
            local = _count(sup.get("nLocal"))
            add("Fitted on", f"{_count(sup.get('nUsable'))} stations nationally"
                + (f", {local} of them in this ecoregion" if local else ""))
        cov = _num(_screen_detail(metric, provenance, "REF-13").get("coverage"))
        if cov is not None:
            add("Natural setting covered", f"{round(cov * 100)} percent of this ecoregion's streams")
    elif kind == "national":
        if _num(sup.get("nUsable")):
            add("Donor stations", f"{_count(sup.get('nUsable'))} of the {_count(sup.get('nPool'))} "
                                  "in the national least-disturbed pool")
        detail = _screen_detail(metric, provenance, "REF-12")
        add("Option", NATIONAL_OPTIONS.get(str(detail.get("option") or "")))
        fauna = detail.get("fauna_groups") or []
        add("Faunal province", ", ".join(str(f).replace("_", " ") for f in fauna))
        risk = sup.get("transferRisk")
        add("Transfer risk", risk if risk not in (None, "", "none") else None)
    elif kind == "carried":
        ver = from_version_of(entry, build)
        add("From", f"Version {ver} of this assessment" if ver else None)
        basis = curve_basis.resolve(ann.get("basis"), criteria_basis=ann.get("criteriaBasis"))
        add("Original source", ann.get("basisLabel") or curve_basis.label_for(basis))
        if basis == curve_basis.MODELED and _num(sup.get("nUsable")):
            local = _count(sup.get("nLocal"))
            add("Fitted on", f"{_count(sup.get('nUsable'))} stations nationally"
                + (f", {local} of them in this ecoregion" if local else ""))
        elif basis == curve_basis.NATIONAL and _num(sup.get("nUsable")):
            add("Donor stations", f"{_count(sup.get('nUsable'))} from the national pool")
        elif basis == curve_basis.PUBLISHED:
            src = ann.get("criteriaSource") or {}
            add("Criterion", src.get("title") or ann.get("sourceCitation"))
        else:
            n = ann.get("referenceN")
            if n is None:
                n = sup.get("nUsable")
            add("Reference stations", _count(n))
            add("Pool", _pool_words(sup))
    label = ann.get("confidenceLabel")
    criterion = kind in CRITERION_KINDS or (
        kind == "carried" and curve_basis.resolve(ann.get("basis"), criteria_basis=ann.get(
            "criteriaBasis")) == curve_basis.PUBLISHED)
    if owner and not criterion:
        add("Confidence", "Not scored: the owner chose this curve")
    elif criterion:
        add("Confidence", "Not scored: a criterion rests on no reference sample")
    elif label:
        total = _num(ann.get("confidenceTotal"))
        add("Confidence", f"{label}" + (f" ({curve_svg.fmt_num(total)})" if total is not None else ""))
    rng = ann.get("referenceRange")
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and None not in rng:
        add("Reference range", f"{curve_svg.fmt_num(rng[0])} to {curve_svg.fmt_num(rng[1])}"
            + (f" {units_of(entry)}" if units_of(entry) else ""))
    return facts


def _date(text: Any) -> str:
    return str(text or "")[:10]


def _owner_facts(metric: str, entry: Mapping, owner: Mapping) -> list[tuple[str, str]]:
    """What the owner's choice states: the source, what the curve is and where it
    comes from. Why the owner chose it follows "Chosen by" in the panel."""
    src = owner.get("source") or {}
    kind, ref = str(src.get("kind") or ""), src.get("ref") or {}
    ann = _ann(entry)
    out: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value not in (None, "", [], ()):
            out.append((label, str(value)))

    if kind in ("entered", "refused_source"):
        add("Source", src.get("title"))
    if kind == "entered":
        add("Curve", "Two thresholds" if ref.get("method") == "thresholds"
            else "Entered point by point")
        bands = (ann.get("criteriaSource") or {}).get("bands") or []
        add("Thresholds", "; ".join(f"{b.get('rating')} {b.get('label')}" for b in bands
                                    if b.get("rating") and b.get("label")))
        add("Citation", src.get("citation") or "None. Entered on professional judgment.")
    elif kind == "other_assessment":
        bf = ann.get("borrowedFrom") or {}
        add("From", f"{bf.get('assessmentName') or ref.get('assessmentId')}, version "
            f"{bf.get('version') or ref.get('version')}")
        add("Original source", bf.get("basisLabel") or curve_basis.label_for(bf.get("basis")))
        n = _count(bf.get("referenceN"))
        add("Its reference stations", f"{n}, of {bf.get('regionName') or 'that region'}"
            if n else None)
    elif kind == "catalog":
        add("Catalog entry", str(ref.get("entry") or "")
            + (f", edition {ref['edition']}" if ref.get("edition") else ""))
    return out


def chosen_by(metric: str, entry: Mapping) -> str:
    """Who put the curve in this version, and under which rule."""
    owner = (entry or {}).get("owner")
    if owner:
        who = str(owner.get("recordedBy") or "The owner")
        on = _date(owner.get("recordedAt"))
        return f"{who}{(' on ' + on) if on else ''}, under REF-15 ({rule_name('REF-15') or 'Owner curve decision'})"
    rule = KIND_RULES.get(str((entry or {}).get("kind") or ""))
    if not rule:
        return ""
    name = rule_name(rule)
    return f"The build, under {rule}" + (f" ({name})" if name else "")


def _national_why(rec: Mapping) -> str:
    """What the national options came to: the refused ones with their reason,
    then the one used."""
    parts = []
    for opt in (rec.get("computed") or {}).get("options_tried") or []:
        name = NATIONAL_OPTIONS.get(str(opt.get("option") or ""), str(opt.get("option") or ""))
        if opt.get("accepted"):
            parts.append(f"{name}: used, {_count(opt.get('n'))} stations.")
        else:
            parts.append(f"{name}: refused. {str(opt.get('why') or '').strip()}".strip())
    return " ".join(p if p.endswith(".") else p + "." for p in parts)


def build_trail(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
                build: Optional[Mapping] = None) -> list[dict]:
    """The sources the build tried before settling on this curve, in order:
    ``[{step, verdict, why}]``. Empty when the opened version carries no
    provenance, or for a fixed criterion, which is not chosen from a list."""
    kind = str((entry or {}).get("kind") or "")
    if (entry or {}).get("owner"):
        return _owner_trail(metric, entry, build=build)
    if kind == "carried":
        ver = from_version_of(entry, build)
        return [{"step": f"Version {ver}" if ver else "Earlier version", "verdict": USED,
                 "why": ("Carried forward unchanged, because nothing it rests on changed. "
                         "That version decided its source.")}]
    if kind not in ("national", "modeled", "published_benchmark") or not provenance:
        return []
    trail = [{"step": "Station pools", "verdict": REFUSED,
              "why": ("No pool of least-disturbed stations, from this ecoregion or a wider "
                      "region, passed the acceptance checks.")}]
    for rule in LADDER_RULES:
        rec = _record(metric, provenance, rule)
        if rec is None:
            trail.append({"step": RUNG_NAMES[rule], "verdict": NOT_TRIED, "why": ""})
            continue
        computed = rec.get("computed") or {}
        if rec.get("verdict") == "pass":
            why = (_national_why(rec) if rule == "REF-12" else "") or str(rec.get("recommendation") or "")
            trail.append({"step": RUNG_NAMES[rule], "verdict": USED, "why": why})
            break
        trail.append({"step": RUNG_NAMES[rule], "verdict": REFUSED,
                      "why": str(computed.get("why") or rec.get("recommendation") or "")})
    # a rung the build never reached reads as not tried only when nothing was used
    if not any(s["verdict"] == USED for s in trail):
        return []
    return trail


REPLACED = "Replaced"


def _owner_trail(metric: str, entry: Mapping, *, build: Optional[Mapping]) -> list[dict]:
    """What the build did with the metric, then the owner's choice. ``build`` is the
    session's reference build as the build wrote it."""
    b = build or {}
    mk = str(metric)
    owner = entry.get("owner") or {}
    steps: list[dict] = []
    withheld = next((w for w in b.get("insufficientReferenceSupport") or []
                     if str(w.get("metricKey")) == mk), None)
    if withheld is not None:
        steps.append({"step": "Station pools", "verdict": REFUSED,
                      "why": ("No pool of least-disturbed stations, from this ecoregion or a "
                              "wider region, passed the acceptance checks.")})
        for r in withheld.get("rungsTried") or []:
            rule = str(r.get("rung") or "")
            steps.append({"step": RUNG_NAMES.get(rule, rule), "verdict": REFUSED,
                          "why": str(r.get("why") or "")})
    elif mk in (b.get("carriedMetrics") or {}):
        ver = (b.get("carriedFrom") or {}).get("fromVersion")
        steps.append({"step": f"Version {ver}" if ver else "Earlier version",
                      "verdict": REPLACED,
                      "why": "The build carried this version's curve forward. The owner chose "
                             "another source."})
    elif mk in (b.get("ladderMetrics") or {}):
        ann = (b.get("metricAnnotations") or {}).get(mk) or {}
        basis = curve_basis.resolve(ann.get("basis"), criteria_basis=ann.get("criteriaBasis"))
        rule = {curve_basis.NATIONAL: "REF-12", curve_basis.MODELED: "REF-13",
                curve_basis.PUBLISHED: "REF-14"}.get(basis)
        steps.append({"step": curve_basis.label_for(basis) or "Another source",
                      "verdict": REPLACED,
                      "why": ("The build's choice" + (f", under {rule}" if rule else "")
                              + ". The owner chose another source.")})
    from . import owner_sources
    source = owner.get("source") or {}
    steps.append({"step": "The owner", "verdict": USED,
                  "why": f"{owner_sources.label_for(source)}: {source.get('title') or ''}".strip()})
    return steps


def breakpoints(entry: Mapping) -> list[tuple[float, float]]:
    """The curve's points ``[(value, score), ...]``, sorted by value."""
    return curve_svg.points_from_curve_row((entry or {}).get("row"))


def limits(entry: Mapping) -> list[str]:
    """What the source does not claim: the basis limit and the curve's caveats,
    each sentence once."""
    ann = _ann(entry)
    out: list[str] = []
    for text in [ann.get("basisLimit")] + list(ann.get("curveCaveats") or []):
        t = str(text or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def kind_counts(tiles: Iterable[Mapping]) -> dict[str, int]:
    """``{kind: n}`` over gallery tiles: a read-only tile by its source, anything
    else built here. Each metric counts once, whatever it is cross-listed under."""
    seen: dict[str, str] = {}
    for t in tiles:
        mk = str(t.get("metric") or "")
        if not mk or mk in seen:
            continue
        seen[mk] = str(t.get("source_kind") or "") if t.get("read_only") else "built"
    out: dict[str, int] = {}
    for kind in seen.values():
        out[kind] = out.get(kind, 0) + 1
    return {k: out[k] for k in sorted(out, key=lambda k: (KIND_ORDER.index(k)
                                                         if k in KIND_ORDER else 99, k))}


__all__ = [
    "KINDS", "KIND_ORDER", "KIND_RULES", "LADDER_RULES", "RUNG_NAMES", "NATIONAL_OPTIONS",
    "USED", "REFUSED", "NOT_TRIED", "REPLACED", "kind_meta", "kind_label", "kind_icon",
    "kind_sentence",
    "rule_name", "from_version_of", "units_of", "source_title", "source_facts", "chosen_by",
    "build_trail", "breakpoints", "limits", "kind_counts",
]
