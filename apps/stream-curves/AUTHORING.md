# StreamCurves authoring contracts

StreamCurves develops, compares, selects, versions and publishes the STAF assessment methods.
Two applications apply them: **DEEP** runs detailed (field) assessments and **EASI** runs the
desktop screening assessment. This document is the contract between the authoring app, the
assessment library (`apps/library`, format in `apps/library/README.md`) and the two consumers.
Every section describes code that exists on the authoring-foundation branch.

## Producers and consumers

| Artifact | Producer (only writer) | Consumers |
|---|---|---|
| DEEP bundle `assessment.deep.json` | StreamCurves publish (`library.publish_version`) | DEEP (baked registry, local merge, remote `library` release) |
| DEEP calculator `calculator.xlsx` | `streamcurves/deep_calculator.py` at publish | DEEP (`www/calculators`), the `library` release |
| EASI method package | StreamCurves EASI exporter (`streamcurves/easi_method`) | EASI (`EASI_METHOD_PACKAGE`), the `library` release |
| EASI method files in `apps/easi/data` | today: `apps/easi/scripts/promote_alternative_2.py`, `build_easi_metrics.py` (from `data/source/screening-metrics.tsv`, generated from the metric-library CSV) and `fetch_nars_ecoregions.py`; after adoption: the EASI exporter only | EASI, StreamCurves' vendored copy (`_vendor/easi`), the EASI calculator generator |
| Library catalog and manifests | `library.publish_version` (DEEP) and `library.publish_easi_version` (EASI), maintainer checkout, `STAF_LIBRARY_PUBLISH=1` | StreamCurves, DEEP, `scripts/library_release.py` |
| Release feeds `library.json` / `library-v2.json` | `scripts/library_release.py` (CI) | StreamCurves gallery, DEEP remote library |
| Evidence packages | `tools/easi-national/builder/evidence_export.py` (EASI); the NRSA archive builder (DEEP, in-app) | StreamCurves evidence store (`streamcurves/evidence_store.py`), refit (`easi_method/refit.py`) and exploration |
| SQT source registry `data/sqt/registry.json` | `scripts/build_sqt_registry.py` (from the metric-library CSV, the adapted assessments and any originals) | the Select final curves picker, `candidates.sqt_candidate`, REF-15's `sqt` source |

A consumer never writes what it reads. After the owner adopts the authority flip, a producer that
feeds a method file (the metric-library TSV, the NARS-9 fetch) no longer writes `apps/easi/data`:
its output enters an authored draft through an explicit import and reaches EASI through the
exporter.

## Frozen during the foundation

EASI's operational identity `b2e3033116e3` covers the criteria set, the method files and these
evaluator sources, by name and raw bytes: `scoring.py`, `screening_methods.py`, `config.py`,
`watershed.py`, `assessment.py`, `bieger.py`, `geo.py`, `datasources/fabric.py`,
`national/records.py` and every file in `metrics/`. Any byte change to one of them, or a file
added to `metrics/`, starts a new method version, marks the published national dataset outdated
and changes the calculator's digest. None of them changes on this branch: new behavior lives in
`method_package.py`, `easi/__init__.py`, the report and app code. `tests/test_method_package.py`
pins `method_version() == b2e3033116e3`; StreamCurves' tests pin its vendored copy the same way.

## Assessment types

Every library assessment has a type. Absence means `deep`, so every existing manifest, catalog
entry, pack and bundle keeps its meaning.

| Type | Consumer | Version payload | Scope |
|---|---|---|---|
| `deep` | DEEP | `assessment.deep.json` (+ calculator) | one Level III ecoregion or one state |
| `easi` | EASI | `method.json` + `method/` files | one national method with internal NARS-9 and slope-class strata |

An `easi` version is never written under DEEP's file names, never listed in the schema-1 release
feed and never baked into DEEP. EASI is one versioned national method: its geography is stated as
national with the strata and fallbacks inside the method, never as 85 regional copies or a
placeholder region.

## Identities

Analytical identity is separate from packaging, review and lifecycle.

- **DEEP content digest** (unchanged): `library.content_digest` over `{metricsByFunction,
  regionCode}`. Lifecycle, validation, the polygon and bundle-level annotations are outside it.
- **EASI `method_version()`** (unchanged, the operational identity; see above). National results
  and calculators carry it.
- **EASI `packageDigest`**: SHA-256 of the canonical JSON `{file name: sha256}` of the eight method
  files. It changes exactly when method data changes.
- **EASI `evaluatorDigest`**: SHA-256 over the evaluator sources of `method_version` (name +
  bytes): the code that turns evidence into ratings. `method_version` is a function of (criteria
  set, evaluator sources, method files).
- **EASI `acquisitionDigest`**: SHA-256 over the code and bundled data that turn a site into
  evidence and sit outside `method_version`: the adapters (`datasources/`), `geomorph.py`,
  delineation and routing, the vendored site engine and the evaluator assets
  (`physio_divisions.geojson`, `ecoregions_l3.geojson`, `nrsa-2018-19-evidence.json.gz`). Live
  reports and exports carry it beside `method_version`, because a live result depends on both.
- **Evidence data digest**: SHA-256 over the canonical map of the package's data-file SHA-256s
  (`dataDigest`); the package digest is the canonical manifest without its producer block, so
  rewording a description never breaks a `dependsOn` reference and exporting the same data and
  description again gives the same package digest.
- **Candidate key and basis digest**: what a candidate is (its identity) and what it holds (its analytical content); see Candidates and final selection.
- **Project id**: a UUID per project file (`project_meta`), unrelated to any analytical identity.

Author notes, unselected alternatives, reviewer comments and lifecycle records never enter a
content digest, a `packageDigest` or a `method_version`.

Hashes are taken on the bytes a clean checkout holds. The root `.gitattributes` checks text out
with LF and pins the files whose recorded bytes are CRLF (`-text`), among them EASI's
`screening-methods.json` and `reference-curves.json`. An older working copy may hold CRLF copies of
LF files: the curve artifact's `curveEngineSha256` `a44a89f8...` is `streamcurves/curves.py` with
CRLF endings, and the same file hashes `413073bc...` with LF. Records that cite code carry the
LF-normalized hash too. Method files travel as raw bytes in every container (project, pack, release
asset, library folder); no text normalization applies to them.

## EASI method package

The consumer projection EASI loads (`apps/easi/easi/method_package.py`), and the payload of an
`easi` library version.

```
method.json                              envelope (below)
method/<file>                            the eight method files, byte for byte
calculator/EASI_Calculator_<ver>.xlsx    optional: generated from exactly these files
```

```json
{
  "schema": "staf-easi-method", "schemaVersion": 1,
  "methodId": "easi-screening", "version": 1, "status": "draft",
  "label": "Alternative 2: NARS-9 references", "criteriaSet": "regional",
  "identity": {"methodVersion": "b2e3033116e3", "packageDigest": "sha256:...",
               "evaluatorDigest": "sha256:...",
               "validatedUnder": [{"evaluatorDigest": "sha256:...", "methodVersion": "b2e3033116e3"}],
               "scoringIdentity": {"alternative_id": "alternative-2"}},
  "evaluator": {"app": "easi", "engineApi": 1,
                "requires": {"operators": ["threshold", "..."], "stratifiers": ["nars9", "slope_class"],
                             "behaviors": ["curve-index-bands-0.39-0.69", "..."]}},
  "files": {"screening-methods.json": {"bytes": 100718, "sha256": "78c1e292..."}}
}
```

Method files: `screening-methods.json`, `reference-curves.json`, `easi-metrics.json`,
`cwa-mapping.json`, `functions.json`, `ecoregion-crosswalk.json`, `scoring-identity.json`,
`nars-ecoregions-9.geojson.gz`. Evaluator assets stay with the app and its acquisition identity.

Validation, before anything is used:

- structure: schema, `schemaVersion`, `engineApi`, allowed entry names only (no code, no paths),
  sizes and SHA-256 of every file, the `packageDigest`;
- capabilities: every operator, stratifier and evaluator behavior the package requires is one this
  EASI provides (the behaviors name the scoring rules that live in code: the 0.39/0.69 index bands,
  anchors from `indexMidpoints`, `round(index x 15)`, the D/i weights, the ECI mean, the 0.70
  coverage rule, composites on anchor indices, the curve fallback order, twenty methods);
- cross-file consistency: `indexMidpoints` equal the catalog's `ratingIndex`, no anchor rounds on a
  tie, every CWA row is D/i/-, every curve set referenced exists with a national curve, strata are
  NARS-9 or slope-class codes, knots increase with index values in [0, 1], and
  `scoring-identity.json` names the hashes of the files it identifies;
- identity: `method_version` recomputed under this evaluator must equal the one recorded for this
  evaluator in `validatedUnder`; under an evaluator not in the list it is reported, not assumed.
  An unknown `schemaVersion` or `engineApi`, a missing capability, or a `criteriaSet` that
  disagrees with `EASI_CRITERIA_SET` is refused with a message; nothing is partly applied.

Activation and isolation:

- `EASI_METHOD_PACKAGE=<zip or folder>` activates a package at process start: `easi/__init__.py`
  materializes a verified, content-addressed data folder (the package's files plus the app's
  evaluator assets) and points `EASI_DATA_DIR` at it before `easi.config` is imported. The app
  verifies the catalog and identity at startup and refuses to start otherwise. Unset means the
  built-in `apps/easi/data` (rollback).
- One method per process. Switching is by process in production; `activate()` switches inside a
  process for tests and single-method workers, repointing every data-derived path and clearing
  every method cache (tested on the live region lookup both ways).
- Results carry the identity they were computed with, and callers check it.
- The calculator served is the active package's own, the committed one for the built-in method,
  or none: a workbook is only served for the files it was generated from.
- A vendored copy (StreamCurves) honors `EASI_METHOD_PACKAGE` only in a process marked
  `STREAMCURVES_EASI_WORKER=1`; StreamCurves' entry points also clear `EASI_*` switches at start.

Authority. The importer (`scripts/import_easi_method.py`) reads EASI's method files into an
authored project; the exporter writes packages from it. Neither runs implicitly or writes
`apps/easi/data`. Until the owner adopts the flip, a round-trip gate proves that import followed by
export reproduces `apps/easi/data` byte for byte, and a change to `apps/easi/data` is brought in by
importing it as a new authored version. After adoption, `apps/easi/data/source/active-method.json`
pins the active version (id, version, `packageDigest`), `apps/easi/data` must equal that version's
files, and only an explicit activation rewrites both. Publishing a new version never changes the
pin, so publishing does not activate.

Publishing, activating and recomputing are three separate operations. Publishing stages a version
in the library; activation points EASI at a version; recomputing national results is a builder
job. None triggers another. Stored national results bound to another method stay flagged as
outdated (`bundle.identity_error`, `require_current`).

## EASI inside DEEP authoring

StreamCurves' vendored EASI is an input to DEEP authoring: CURVE-11 fixed criteria
(`config/fixed_criteria.yaml`, generated from the vendored catalog and pinned by its sha), the
REF-04 reference screen (mirror-checked against `reference-curves.json`), the DEEP calculator's CWA
mapping, `basis_transfer` and `published_benchmark`, and every run manifest's `easiMethodVersion`.
Drafts never load in that copy. Re-vendoring a new EASI method into StreamCurves is a DEEP
methodology decision: its fingerprints and run manifests change, so it happens only through
re-vendoring with the drift gates, never through a package or an environment variable.

## StreamCurves project files

A project is a zip `<Name>.streamcurves`: `project.json`, `session.streamcurves.json` and
`origin/*` (`streamcurves/project_file.py`).

The app reads formats 1 and 2 and writes the lowest format a project needs. Format 2 adds
`assessment_type` and, for EASI, the parts `easi/package.json` (meta, lineage, candidate register,
decisions, evidence references, recipes, notes, history), `easi/method/<file>`,
`easi/calculator/<file>`, `easi/cases.json` and `easi/base/<file>` (the origin's bytes of each
method file a draft changed, so the version it came from is recoverable from the project alone and
checked against the origin's package digest); an EASI project's session is empty and its interface
state (the current stage) rides in `project.json`. A DEEP project is written as format 2 when its
session holds what an older app would drop on a re-save (curves added for comparison or reasons
recorded in the candidate register) or misread (an owner decision only the REF-15 extension
applies), and as format 1 otherwise (`project_file.required_format`). StreamCurves 1.0.0 refuses a
format-2 file with "update the app" instead of opening it and dropping what it cannot read. Packs
are named with the pack schema they hold (`-p1-`, `-p2-`). A DEEP pack stays format 1 unless its
session holds an extension decision; its candidate register alone keeps it format 1, because the
version's provenance holds that record and every installed app can still open the version.

## Library feeds

| Feed | Schema | Lists | Readers |
|---|---|---|---|
| `library.json` | 1 (frozen) | `deep` assessments only | StreamCurves 1.0.0, DEEP from ea32716 on |
| `library-v2.json` | 2 | every type, each entry typed, with typed assets | new StreamCurves |

The DEEP deployed on Posit (2026-09-09) predates the remote library and reads its baked registry
only; both it and the ea32716 reader are tested against the new build. `library.json` keeps listing
exactly the DEEP assessments, versions, statuses, digests and asset kinds; asset names and sizes
change only when their bytes do (packs embed the app version). `check`, `upload` and `prune`
work on the union of both catalogs, and both catalogs upload last. EASI assets: the pack
(`<id>-v<N>-p2-<sha8>.streamcurves`), the method package (`<id>-v<N>-<sha8>.easi-method.zip`) and
evidence manifests.

## EASI projects in StreamCurves

An EASI project opens in the same shell as a DEEP project, with its own five stages in the strip
and the Project panel (`streamcurves/easi_method/stages.py`, `views/easi_page.py`):

1. **Method**: the identity EASI reports (method version, package digest, evaluator digest),
   where the version came from, and the 20 functions with how each one scores.
2. **Development data**: the reference screen the curves record, and the development data
   packages the project carries.
3. **Curves and criteria**: the 34 reference curves by family and stratum, every fixed band
   and the regional nutrient edges.
4. **Final selection**: the method selected for each function and who decided it.
5. **Review and publish**: what changed from the origin, its consequences, the method package
   download and, in a maintainer's checkout, publishing the next version.

Rules:

- Only a draft revision is edited. An import or an opened library version stays exactly as it
  is; *Start vN* forks it (`io.fork`) into the next version, numbered after both the origin and
  the library's latest. A fork records its origin (version, method version, package digest).
- Supported edits (`easi_method.edit`): band edges and which side owns them, regional TN and TP
  edges, curve knots, and display text. Every edit needs a reason and becomes one history record
  and one undo step. A moved edge rewrites the two band labels and the plot annotation EASI shows
  at that edge; a count scale has no shared edge and is refused. A changed curve's 0.39 and 0.69
  crossings are recomputed and a curve that misses either break is refused. An edit returned to
  the origin's values restores the origin's bytes exactly (labels and identity included), so an
  undone experiment never leaves a changed method behind. No edit trail is written into a method
  file: history and provenance carry it. A text edit (title, label, limitations, rationale, basis
  class) scores nothing differently and raises no review flag, but it changes the catalog's bytes,
  so the package digest and the method version change with it: activated in EASI, it marks the
  stored nationwide results as from another method until they are recomputed. The page offers no
  text edit today (`edit.set_text` is a function call), and a basis class must be one EASI accepts.
- Operators, inputs, data routes, derivations, weights, anchors and the rollup are the
  evaluator's (its digest is shown) and change only in EASI itself.
- An analytical change flags its function in Final selection. A person confirms the selection
  with a reason (`register.confirm_selection`, `decidedBy: person`), and the library refuses a
  version with a flag still open.
- The consequences preview scores the draft and its origin on the project's preview cases in
  worker processes, never in the app's own EASI; results are cached by package, evaluator and
  case set. The preview cases are EASI's calculator test cases (every band edge, curve crossing
  and fallback route around one reach): they show which rules move, not how many real reaches
  would. The reviewed summary is kept in the published version's provenance.
- Publishing a revision needs a current preview in the app; publishing into the canonical
  library also needs the canonical gate (`library.publish_gate_reason`), checked in
  `io.publish` itself.

## Evidence

Three roles stay separate: **development** (fitting), **evaluation** (validation) and
**operational** (site inputs). A curve's evidence names its role, and `dependsOn` names upstream
packages by data digest, so a dependency such as EASI-screened reference sites under a DEEP curve
stays visible when agreement between the two is discussed.

```json
{
  "schema": "staf-evidence-package", "schemaVersion": 1,
  "packageId": "easi-dev-members", "version": "2026.09.15-baseline",
  "title": "...", "description": "...",
  "roles": ["development"], "reproducibility": "refittable",
  "dataDigest": "sha256:...",
  "files": {"data/member_values.parquet": {"bytes": 0, "sha256": "...", "rows": 0, "columns": ["..."]}},
  "coverage": {}, "dictionary": {"woody_wsrp100": {"definition": "...", "source": "landscape.woody_wsrp100"}},
  "sources": [{"id": "baseline-snapshot", "path": "...", "citation": "..."}],
  "recipe": {"engine": {"path": "apps/stream-curves/streamcurves/curves.py", "sha256_lf": "..."},
             "constants": {}},
  "checks": {}, "dependsOn": [{"packageId": "easi-dev-universe", "dataDigest": "sha256:..."}],
  "redistribution": {"status": "public-derived", "notes": "..."},
  "limitations": ["..."], "unavailable": [{"item": "...", "why": "...", "remedy": "..."}],
  "producer": {"tool": "...", "snapshot": {"baselineManifestSha256": "..."}}
}
```

Reproducibility levels: **reviewable** (the values behind a curve can be inspected),
**refittable** (the curve can be refit from preserved development evidence) and **regenerable**
(the evidence can be rebuilt from original sources). A package declares its level and itemizes
what is missing (`unavailable`, each with a remedy) and what was checked when it was made
(`checks`); every `dependsOn` resolves to a package of the same export, or the export stops.

Identity is content. `dataDigest` covers the data files; `packageDigest` the whole manifest but
its `producer` block. The producer block travels inside the archive but outside the package
digest, so no digest vouches for it: it says which tool and snapshot made the package, as the
exporter states it. An archive's bytes are a function of the package alone (sorted entries,
fixed times, every entry stored; when it was exported, from which commit, the exporter's own
SHA-256 and whether its code had uncommitted changes (`exporterDirty`) go to the export's
`index.json`, never into the package), and it is named by its package digest. A project pins a
package by its digests; the archive it names (file, SHA-256, size) only says where a copy was.
`evidence_store.fetch_reference` downloads that archive, or, when the host holds the same
package under another archive (a re-export), the one the host's `index.json` lists for it; either
is accepted only when the installed package's digests are the project's. The zip's SHA-256
checks the transfer: a download resumes a `.part` with an HTTP Range request, a finished `.part`
is decided at once, a host that cannot continue (HTTP 416) starts over, a dropped connection
keeps the `.part` and says so, and the archive is removed once installed.

The store (`<data root>/evidence/<packageId>/<package digest 12>/`, one folder per package digest,
a package id being a plain name) extracts into a staging folder with path checks (no absolute
paths, `..` or links; `evidence.json` and data file types under `data/` only; nothing the
manifest does not list; a damaged archive refused with a plain message) and moves it into place
only once every file's size and SHA-256 match. A folder keeps the size and time of every file
from its last full check (`.verified.json`); listing the store trusts that only while they hold,
and anything that reads the data (a refit, the viewer, an export) hashes every file first
(`evidence_store.ready`). A copy damaged or edited on disk reads "Damaged" with the way to fetch
it again, and a manifest changed after the install is damage too. One exception: a store folder
installed before 2026-09-23 is named by the package's data digest, and there an edited manifest
is caught only when its file table or data digest no longer matches the files; a reference that
records a package digest still reads such a copy as another version, never as the package.
Importing the package again installs it in a folder named by its package digest. Hosting is decided at adoption;
it uses content-named assets on an existing rolling prerelease (a release per package would push
the installer out of Velopack's 10-newest-releases window), and a rolling release URL is a
location, never an identity. Until then `STREAMCURVES_EVIDENCE_BASE_URL` (a folder or an https
base) names where archives are fetched.

EASI's development evidence (the 34 operational curves and the whole curve registry behind
them) ships as five packages exported from the frozen 2026-09-15 baseline:

| Package | Role | Level | Holds |
|---|---|---|---|
| `easi-dev-universe` | development | refittable | every NHDPlus V2 reach in the landscape table's order with the strata, frame and screen columns: the panels step regenerates every level's members from it |
| `easi-dev-members` | development | refittable | the panel members and panels verbatim, each member's fit input for every fitted quantity, the screen variables, the composite pressure and the 12 monthly EROM flows |
| `easi-dev-fits` | development | reviewable | the curve registry and knots verbatim, the recipe and the map of the 34 operational curves |
| `easi-eval-refs` | evaluation | reviewable | the alternatives study's receipts and field summary; the NRSA archive's manifest hash |
| `easi-operational-ref` | operational | reviewable | the national dataset's build and method identity (hashes only) |

The fit recipe is the builder's code, vendored verbatim into
`streamcurves/easi_method/fit_recipe.py` by `scripts/vendor_fit_recipe.py` (screens, panel
selection, quantities, fit wrapper, usability, rho, artifact rounding) with a drift gate;
`easi_method/refit.py` groups members into fits as the builder does (its grouping and the
pooled national entrenchment fallback are re-implemented, proven equal on the frozen registry,
with no drift gate against the builder's `curves.py`). A refit first compares the running curve
engine, its own code (the vendored fit recipe and `refit.py`, SHA-256 of their LF bytes, recorded
in the members and fits manifests' `recipe.code`) and the fit constants with the package's
recorded recipe (`refit.recipe_check`), and claims exactness only when they agree; a package that
records no engine or code hash is never taken to agree. Refitting from the installed packages with every developer path
blocked (Python's opens and pyarrow's) reproduces all 2,752 registry fits, every field the
refit and the registry both carry (22) and every knot, and the 34 operational curves exactly,
and the universe regenerates all 282,113 member rows
(`scripts/refit_easi_curves.py --panels --block-dev-paths`). An EASI project names the packages
it was developed from (`project.evidence`: package, version, data digest, roles,
reproducibility, coverage, archive), never their bytes; its Development data stage shows them,
downloads, imports and views them, and refits the curves from them. DEEP's development data,
the in-app NRSA archive, is described and verified the same way on the NRSA explorer page.

## Candidates and final selection

One vocabulary for every alternative considered, in DEEP and EASI (`streamcurves/candidates.py`):

```json
{
  "candidateKey": "cand-<sha12>",
  "identity": {"assessmentType": "deep", "subject": {"kind": "metric", "id": "..."},
               "sourceKind": "fitted", "sourceRef": {},
               "applicability": {"geography": {"kind": "ecoregion", "code": "50"}}},
  "basisDigest": "sha256:...", "purpose": "operational", "campaign": null,
  "buildStatus": "built", "supersededBy": null, "label": "...",
  "definition": {}, "limitations": [],
  "eligibility": {"status": "eligible", "reasons": [], "checks": []}
}
```

- `candidateKey` hashes the identity only (what the candidate is), so a refit keeps its key and its
  history; `basisDigest` hashes the analytical content (DEEP: every stratum's points at 12
  significant digits and the direction; EASI: `register.basis_digest`, the catalog entry's
  analytical fields, the curve sets it reads, anchors and CWA weights) and never captions, badges
  or caveat text. A DEEP curve can serve several functions, so its identity names none and each
  decision names its function; an EASI method entry serves one function, so its identity carries
  `functionId`.
- `sourceKind`: `fitted`, `carried`, `national`, `modeled`, `published_benchmark`, `fixed`, `sqt`,
  `owner_entered`, `borrowed`, `earlier_version`, `imported_alternative`. A fallback route inside
  one selected method (EASI's national curve) is part of that method, never a candidate.
- Status per candidate and function: `selected`, `eligible_not_selected`, `excluded` (or
  unsupported), `not_evaluated`, `failed`, `superseded`. Decisions carry `rule`, `reason`,
  `decidedBy` (`automated`, `imported`, `person`), `who`, `when` and the `basisDigest` decided on;
  one whose basis no longer matches reads "Look again" and stays in the history. A DEEP REF-15
  decision records the digest of the curve it was made on, computed as the register computes it
  (`views.curve_gallery.metric_basis`), so a refit that moves that curve asks for another look.

**DEEP** (`deep_register`) is a projection over records the session already keeps, read from the
gallery's own tiles so the two views never disagree: SELECT-04's `portfolioSelection` (selected,
or supported and not selected with the source and score), REF-06's withheld list (excluded, with
the statement), CURVE-07 (a curve held for review reads not evaluated, one taken out of scope
excluded), carried, ladder and fixed curves, and the owner's REF-15 decisions (who, when, why). It
writes nothing; REF-15 stays the only writer of what a version scores. On all seven latest
published versions the register's selected pairs equal the bundle's `metricsByFunction`. What the
register adds rides in the session field `candidate_register`: curves added for comparison (a
state SQT curve) and the reason a person gave for not selecting one. The Reference Curves page's
third section, **Select final curves**, lists the 20 functions with their selected curves, gaps
and unresolved items; each opens to the alternatives considered with status, reason and who
decided, "Use in this function" and "Undo" (REF-15's own form), "Record why not", and a compare
panel for up to three curves. An interactive publish writes the register's export
(`candidateRegister`) into the version's provenance, and says so when it cannot.

**EASI** keeps its register in the project (`easi/register.json`). `easi_method/alternatives.py`
imports the 2026-09-15 controlled study (verified by its completion record, sha256 97a24b44..., and
each candidate's catalog and curve hashes): a candidate only where an alternative's definition
differs from the method's. Alternative 2 differs nowhere; Alternatives 1, 3 and 4 differ in low
flow, light and thermal regime, carbon processing and habitat provision; the legacy criteria
(an evaluator asset, read from the vendored copy) in eight functions. Each carries its definition,
so the register is complete without the study folder, and its reason quotes the study (whose rule
recommended Alternative 1) and the owner's adoption of Alternative 2 on 2026-09-16 (commit 02f39a8);
history before the import is stated as missing. The Final selection stage (**Select final
methods**) compares up to three definitions curve family by curve family and, in a draft revision,
adopts one: every function reading a curve family it rewrites moves with it (the woody curves
serve light and thermal regime and habitat provision), the replaced method stays as eligible, not
selected, and adopting the method the draft started from restores its exact bytes. Published EASI
versions carry the register export in their provenance, never in the method package.

### Published state SQT curves

`data/sqt/registry.json` (built by `scripts/build_sqt_registry.py`, read by
`streamcurves/sqt_registry.py`; see `data/sqt/README.md`) holds one record per state, metric and
stratum from the metric library's raw bins, compared with the `*-sqt-adapted` library assessments
and with any original on disk: 386 records, 346 eligible; 53 verified and 30 partly verified
against the MN v2.0 list and the WI curves sheet in `data/templates`; defects (a placeholder, swapped
columns, a mislabelled stratum, two-sided curves cut to one limb) make a record ineligible and stay
as issues. Owner-supplied originals go in `D:\Data\staf-authoring\sqt-originals\<STATE>\` with a
`sources.json`; they are cited and fingerprinted, never redistributed.

A curve is added to a DEEP session from the section's picker (search by metric text; filter by
state, edition, verification, function and eligibility). Each record is checked against the
function's curves in this session (`candidates.sqt_context`): the ten registry checks (eligibility,
construct, protocol, units, direction, score scale, geography, stream type, source limits,
extrapolation) plus the adoption checks (the curve's ends, its form, a stratified metric). When the
STAF metric library's crosswalk (`config/staf_metric_library.json`, `app_metric_key`) says one of
those curves measures the same metric, its units, direction (`higher_is_better`) and the range of
the values the session scores on it enter the checks; otherwise the construct check names the
curves and units and values are not compared. The picker shows the worst status and its reason;
Compare lists every check that is not a pass. `candidates.sqt_candidate` freezes the record into the
candidate; a failed check excludes it with the reason. Selecting one checks it again against each
fitted curve it would take the place of and refuses on a failure. The published rule is written as
DEEP points (`candidates.sqt_adoption`): the original's points where the registry verified them,
an end the source extends linearly carried to the index limit it reaches, an end threshold bin as a
step; a form DEEP cannot write, or an end the source leaves open where the session's values run
past it, excludes the curve. The index itself is never rescaled: SQT bands (0.30, 0.70) differ from
DEEP's (0.39, 0.69), so a selected SQT curve keeps the published index, is labelled "State SQT" on
the published-criterion basis, and states the banding difference. For an EASI function an SQT curve
is a field measurement against EASI's desktop estimate, so `register.add_sqt_candidate` records it
excluded for that reason, never a silent substitution.

Known gaps, for the campaigns to close when they need them: a headless build
(`regional_agent`, `run_region_batch.py`) does not carry the session's `candidate_register`, so a
batch rebuild keeps the automated records but not the curves added for comparison or the reasons
people recorded, and only an interactive publish writes `candidateRegister` into provenance;
comparing two versions' registers exists as `candidates.diff` (tests, scripts), with no page in
either app; adding an SQT curve to an EASI project is a function call
(`register.add_sqt_candidate`), not yet a picker.

### The REF-15 extension (off until adopted)

Selecting a state SQT curve, or any chosen curve in place of one this build fitted, extends REF-15
behind `owner_decisions.alternatives_over_fitted`, **false** in the canonical configuration (the
methodology config and the REF-15 rule text say so). With it on, a `source` decision may be of kind
`sqt` and may name the fitted curves it `replaces` in the functions it names; each replaced curve
stays built and is recorded as supported, not selected, under the decision, and SELECT-04 and the
reference-source hierarchy keep their order. With it off, such a decision is refused when made and,
if a session already holds one, applies nothing and says so (`owner_curves.stale`); published
versions republish unchanged. The bundle carries the owner's decision summary (never `replaces`)
and the chosen curve; it never names a replaced or unselected candidate.

## Compatibility rules

- Absence semantics: a missing `assessmentType` is `deep`; a missing field in any record keeps its
  pre-change meaning.
- Newer schemas, formats and capabilities are refused whole, with a message, never partly applied.
- `library.json` (schema 1) never lists a non-DEEP entry.
- Historical library versions, frozen curve coordinates, study receipts and published artifacts
  are never rewritten.
- An installed copy stays a user copy: it downloads, revises and sends projects back; canonical
  publication is a maintainer checkout operation (`STAF_LIBRARY_PUBLISH=1`).
