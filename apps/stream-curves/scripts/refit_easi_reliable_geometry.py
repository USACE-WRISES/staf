"""Evaluate and refit EASI's geometry curves on the reliable least-disturbed members (E5b).

    python apps/stream-curves/scripts/refit_easi_reliable_geometry.py \\
        --members <easi-dev-members folder> --quality <xs_quality.parquet> \\
        --curves <reference-curves.json> --out <folder> [--shift 0.20] [--rounding 0.01]

Owner decision D15 (2026-09-28, WP-R6c). ``--quality`` is the K2b quality table the national
builder writes from the stored evidence (``builder.analysis.xs_quality``); ``--members`` the
verified ``easi-dev-members`` package (the strict reference panels of every level and the
value each fitted quantity read); ``--curves`` the shipped method file whose ``entrenchment``
set and bank-height bands are evaluated. The national panel's members are kept when their
cross sections are reliable for the quantity under K2b (``reliable.select_members``), the
shipped fit rules run on them (``reliable.fit_reliable``: national by slope class, the unsplit
national fallback, the package's own tail endpoints), and each shipped curve is compared with
its reliable-member fit under the stated materiality rule (``reliable.compare_entrenchment``:
a quartile beyond the ACC-04 shift of 0.20 IQR, or a class boundary moving by at least the
0.01 rounding). The bank-height ratio's fits are evaluated against the fixed bands
(``reliable.evaluate_bhr``).

The output folder receives ``reliable_members.json`` (the counts per stratum and per slope and
drainage-area class), ``refit_comparison.json`` (every curve's quartiles, boundaries, deltas and
verdict; the BHR evaluation), ``registry_rows.json`` (every fit) and ``curve-sets.json`` (the
refit ``entrenchment`` set in the method file's shape when the comparison is material, else an
empty set list with the reason), which ``build_easi_candidate_package.py --family E5b
--curve-sets`` reads. Nothing under ``apps/easi/data`` or the library is touched.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8", newline="\n")


def members_folder(source: Path):
    from streamcurves import evidence_store as es
    source = Path(source)
    last = None
    for c in (source, source / "easi-dev-members"):
        try:
            return es.pick(c)
        except es.EvidenceError as exc:
            last = exc
    raise SystemExit(f"{source}: no easi-dev-members package found ({last})")


def main(argv=None) -> int:
    from streamcurves import evidence_store as es
    from streamcurves.easi_method import refit, reliable
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--members", required=True, type=Path)
    ap.add_argument("--quality", required=True, type=Path, help="the K2b quality table (xs_quality.parquet)")
    ap.add_argument("--curves", type=Path, default=APP / "streamcurves" / "_vendor" / "easi" / "data" / "reference-curves.json",
                    help="the shipped method file whose geometry curves are evaluated")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--shift", type=float, default=reliable.ACC04_SHIFT_IQR, help="the ACC-04 quartile shift, in IQR")
    ap.add_argument("--rounding", type=float, default=reliable.RATIO_ROUNDING, help="the ratios' rounding")
    a = ap.parse_args(argv)
    folder = members_folder(a.members)
    got = es.verify_folder(folder)
    if not got["ok"]:
        raise SystemExit(f"{folder} does not verify: damaged {got.get('damaged')}, unlisted {got.get('unlisted')}")
    recipe = refit.recipe_check(folder)
    words = refit.recipe_words(recipe)
    if words:
        print(words)
    quality = reliable.load_quality(a.quality)
    quality_doc = {}
    sidecar = Path(a.quality).with_suffix(".json")
    if sidecar.is_file():
        quality_doc = json.loads(sidecar.read_text(encoding="utf-8"))
    members, values, panels = refit.load_members(folder)
    artifact = json.loads(Path(a.curves).read_text(encoding="utf-8"))
    shipped_set = (artifact.get("sets") or {}).get("entrenchment") or {}
    counts, reliable_members = {}, {}
    for quantity in reliable.GEOMETRY_QUANTITIES:
        reliable_members[quantity], counts[quantity] = reliable.select_members(members, quality, quantity)
    fit_er = reliable.fit_reliable(reliable_members["er_median"], values, panels, quantities=("er_median",))
    fit_bhr = reliable.fit_reliable(reliable_members["bhr_median"], values, panels, quantities=("bhr_median",))
    comparison = reliable.compare_entrenchment(shipped_set, fit_er, shift=a.shift, rounding=a.rounding)
    bhr = reliable.evaluate_bhr(fit_bhr)
    entrenchment = reliable.assemble_entrenchment(fit_er) if comparison["material"] else None
    if entrenchment is not None and "national" not in entrenchment["curves"]:
        print("the reliable-member national fallback is not usable; no set is written")
        entrenchment = None
    a.out.mkdir(parents=True, exist_ok=True)
    provenance = {"membersPackage": str(folder), "membersDigest": got["dataDigest"], "membersPackageDigest": got["packageDigest"],
                  "recipe": recipe, "quality": {"file": str(a.quality), "sha256": _sha(a.quality),
                                                "rules": quality_doc.get("quality"), "scan": quality_doc.get("writtenAt"),
                                                "code": quality_doc.get("code")},
                  "shippedCurves": {"file": str(a.curves), "sha256": _sha(a.curves)},
                  "reliability": "K2b: the sections carry the ratio, no low_quality flag, the ratio inside its physical range",
                  "fitRules": "the shipped fit rules (fit_registry: national by slope class; national_entrenchment: the "
                              "unsplit fallback) under the members package's tail endpoints, on the reliable members only",
                  "materiality": comparison["rule"], "material": comparison["material"],
                  "materialCurves": comparison["material_curves"],
                  "reliableMembers": {q: counts[q]["total"] for q in counts},
                  "builtAt": _now(), "builder": "apps/stream-curves/scripts/refit_easi_reliable_geometry.py"}
    reliable.write_curve_sets(a.out / "curve-sets.json", entrenchment=entrenchment, provenance=provenance)
    _write(a.out / "reliable_members.json", {"schema": "staf-easi-reliable-members", "schemaVersion": 1,
                                             "counts": counts, "provenance": provenance})
    _write(a.out / "refit_comparison.json", {"schema": "staf-easi-refit-comparison", "schemaVersion": 1,
                                             "entrenchment": comparison, "bhr": bhr,
                                             "refitEntrenchmentSet": entrenchment, "provenance": provenance})
    _write(a.out / "registry_rows.json", {"er_median": fit_er["rows"], "er_median_national_fallback": fit_er["national_entrenchment"],
                                          "bhr_median": fit_bhr["rows"]})
    for quantity in reliable.GEOMETRY_QUANTITIES:
        t = counts[quantity]["total"]
        print(f"{quantity}: national members {t['members']}, with the ratio {t['with_ratio']}, three-rule reliable "
              f"{t['three_rules']}, K2b reliable {t['k2b']}")
        for key, block in sorted(counts[quantity].get("by_slope_class", {}).items()):
            print(f"  slope {key}: {block}")
        for key, block in sorted(counts[quantity].get("by_da_class", {}).items()):
            print(f"  da {key}: {block}")
    for key, c in comparison["curves"].items():
        s, r = c["shipped"], c["refit"] or {}
        print(f"entrenchment/{key}: shipped n {s.get('n')} q25 {s.get('q25')} q50 {s.get('q50')} q75 {s.get('q75')} "
              f"x39 {s.get('x39')} x69 {s.get('x69')}; refit n {r.get('n')} q25 {r.get('q25')} q50 {r.get('q50')} "
              f"q75 {r.get('q75')} x39 {r.get('x39')} x69 {r.get('x69')} usable {r.get('usable')}; "
              f"material {c['material']} {c['reasons']}")
    print(f"bhr: {bhr['finding']}")
    for key, f in bhr["fits"].items():
        print(f"  bhr/{key}: n {f.get('n')} q25 {f.get('q25')} q50 {f.get('q50')} q75 {f.get('q75')} usable {f.get('usable')} "
              f"({f.get('reason')})")
    print(f"material: {comparison['material']} {comparison['material_curves']}; wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
