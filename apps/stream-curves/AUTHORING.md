# StreamCurves authoring contracts

StreamCurves develops, compares, selects, versions and publishes the STAF assessment methods.
Two applications apply them: **DEEP** runs detailed (field) assessments and **EASI** runs the
desktop screening assessment. This document is the contract between the authoring app, the
assessment library (`apps/library`, format in `apps/library/README.md`) and the two consumers.
A section marked *planned* is the agreed design the authoring-foundation branch implements; the
others describe code that exists.

## Producers and consumers

| Artifact | Producer (only writer) | Consumers |
|---|---|---|
| DEEP bundle `assessment.deep.json` | StreamCurves publish (`library.publish_version`) | DEEP (baked registry, local merge, remote `library` release) |
| DEEP calculator `calculator.xlsx` | `streamcurves/deep_calculator.py` at publish | DEEP (`www/calculators`), the `library` release |
| EASI method package | StreamCurves EASI exporter (`streamcurves/easi_method`) | EASI (`EASI_METHOD_PACKAGE`), the `library` release (*planned*) |
| EASI method files in `apps/easi/data` | today: `apps/easi/scripts/promote_alternative_2.py`, `build_easi_metrics.py` (from `data/source/screening-metrics.tsv`, generated from the metric-library CSV) and `fetch_nars_ecoregions.py`; after adoption: the EASI exporter only | EASI, StreamCurves' vendored copy (`_vendor/easi`), the EASI calculator generator |
| Library catalog and manifests | `library.publish_version` (maintainer checkout, `STAF_LIBRARY_PUBLISH=1`) | StreamCurves, DEEP, `scripts/library_release.py` |
| Release feeds `library.json` / `library-v2.json` | `scripts/library_release.py` (CI) | StreamCurves gallery, DEEP remote library |
| Evidence packages (*planned*) | evidence producers (`tools/easi-national/builder/evidence_export.py`; the NRSA archive builder) | StreamCurves evidence store, refit and exploration |
| SQT source registry (*planned*) | `scripts/build_sqt_registry.py` | StreamCurves source dialog and candidate register |

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

## Assessment types (*planned*)

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
- **Evidence data digest** (*planned*): SHA-256 over the package's data files (name, bytes,
  sha256); descriptive metadata has its own manifest digest, so rewording a description never
  breaks a `dependsOn` reference.
- **Candidate key and basis digest** (*planned*, see Candidates).
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

*Planned:* the app reads formats 1 and 2 and writes the lowest format a project needs. Format 2
adds `assessment_type` and, for EASI, the parts `easi/package.json` (lineage, candidate register,
decisions, evidence references, recipes, notes), `easi/method/<file>`, `easi/calculator/<file>` and
`easi/cases.json`; the session then holds interface state only. A DEEP project stays format 1
unless it carries content an older app would drop (added SQT candidates, final-selection
decisions). StreamCurves 1.0.0 refuses a format-2 file with "update the app" instead of opening it
and dropping what it cannot read. Packs are named with the pack schema they hold (`-p1-`, `-p2-`),
and every DEEP pack stays format 1.

## Library feeds (*planned*)

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

## Evidence (*planned*)

Three roles stay separate: **development** (fitting), **evaluation** (validation) and
**operational** (site inputs). A curve's evidence names its role, and `dependsOn` names upstream
packages by data digest, so a dependency such as EASI-screened reference sites under a DEEP curve
stays visible when agreement between the two is discussed.

```json
{
  "schema": "staf-evidence-package", "schemaVersion": 1,
  "packageId": "easi-dev-members", "version": "2026.09.15-baseline",
  "roles": ["development"], "reproducibility": "refittable",
  "dataDigest": "sha256:...",
  "files": {"members.parquet": {"bytes": 0, "sha256": "...", "rows": 0, "columns": ["..."]}},
  "dictionary": {"woody_wsrp100": {"units": "%", "definition": "..."}},
  "sources": [{"id": "streamcat", "citation": "...", "vintage": "...", "url": "..."}],
  "recipe": {"engine": {"path": "streamcurves/curves.py", "sha256": "...", "sha256_lf": "..."}},
  "dependsOn": [{"packageId": "...", "dataDigest": "sha256:..."}],
  "redistribution": {"status": "public", "notes": "..."},
  "limitations": ["..."], "created": "2026-09-23T00:00:00Z"
}
```

Reproducibility levels: **reviewable** (the values behind a curve can be inspected),
**refittable** (the curve can be refit from preserved development evidence) and **regenerable**
(the evidence can be rebuilt from original sources). A package declares its level and itemizes
what is missing. The store downloads with resume into `.part` files, verifies size and SHA-256
before a package is ready, extracts with path checks, accepts data file types only, and reuses
verified packages offline. Hosting is decided at adoption; it uses content-named assets on an
existing rolling prerelease (a release per package would push the installer out of Velopack's
10-newest-releases window), and a rolling release URL is a location, never an identity.

## Candidates and final selection (*planned*)

One vocabulary for every alternative considered, in DEEP and EASI:

```json
{
  "candidateKey": "cand-<sha12>",
  "identity": {"assessmentType": "deep", "subject": {"kind": "metric", "id": "..."},
               "functionId": "...", "sourceKind": "fitted", "sourceRef": {},
               "applicability": {"geography": {"kind": "ecoregion", "code": "50"}, "strata": {}}},
  "basisDigest": "sha256:...", "methodVersion": "...", "dataFingerprint": "...",
  "purpose": "operational", "campaign": null,
  "buildStatus": "built", "supersededBy": null,
  "definition": {}, "evidence": {}, "limitations": [],
  "eligibility": {"status": "eligible", "reasons": [], "checks": []}
}
```

- `candidateKey` hashes the identity only (what the candidate is), so a refit keeps its key and its
  history; `basisDigest` hashes the analytical content (points, bands, operators, anchors, weights,
  never captions or caveat text) and changes with every refit or analytical edit.
- `sourceKind`: `fitted`, `carried`, `national`, `modeled`, `published_benchmark`, `fixed`, `sqt`,
  `owner_entered`, `borrowed`, `earlier_version`, `imported_alternative`. A candidate is an
  alternative definition for a function; a fallback route inside one selected method (EASI's
  national curve, a CHEM fallback) is part of that method, never a separate candidate.
- `buildStatus` (`built`, `failed`, `not_run`) and `supersededBy` record build failures and
  supersession; `purpose: evaluation` marks a fold fit that can never be selected.

Decisions are per candidate, function and applicability: `decision` (`selected`,
`not_selected`), `reason`, `rule`, `decidedBy` (`automated`, `imported`, `person`), who, when and
the `basisDigest` decided on. The displayed status is one of selected, eligible but not selected,
excluded or unsupported, not evaluated, failed to build, superseded. A decision whose
`basisDigest` no longer matches is flagged for re-review and kept in the history.

For DEEP the register is derived from existing records (SELECT-04 `portfolioSelection`,
basis-ladder refusals, CURVE-07 holds, carried metrics, REF-15 decisions) and REF-15 stays the only
writer; selecting an SQT curve over a curve fitted here extends REF-15 behind a methodology flag
that is off in the canonical configuration, and every published DEEP version must replay with an
unchanged content digest. For EASI the register and decisions live in the project package. The
register rides in projects, sessions and provenance, never in a DEEP bundle or an EASI method
package. Missing historical decisions are stated as missing; history starts at import.

## Compatibility rules

- Absence semantics: a missing `assessmentType` is `deep`; a missing field in any record keeps its
  pre-change meaning.
- Newer schemas, formats and capabilities are refused whole, with a message, never partly applied.
- `library.json` (schema 1) never lists a non-DEEP entry.
- Historical library versions, frozen curve coordinates, study receipts and published artifacts
  are never rewritten.
- An installed copy stays a user copy: it downloads, revises and sends projects back; canonical
  publication is a maintainer checkout operation (`STAF_LIBRARY_PUBLISH=1`).
