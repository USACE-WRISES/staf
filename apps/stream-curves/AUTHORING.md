# StreamCurves authoring contracts

StreamCurves develops, compares, selects, versions and publishes the STAF assessment methods.
Two applications apply them: **DEEP** runs detailed (field) assessments and **EASI** runs the
desktop screening assessment. This document is the contract between the authoring app, the
assessment library (`apps/library`, format in `apps/library/README.md`) and the two consumers.
Each section states what is implemented; a section marked *planned* is the agreed design that the
authoring-foundation branch implements next.

## Producers and consumers

| Artifact | Producer (only writer) | Consumers |
|---|---|---|
| DEEP bundle `assessment.deep.json` | StreamCurves publish (`library.publish_version`) | DEEP (baked registry, local merge, remote `library` release) |
| DEEP calculator `calculator.xlsx` | `streamcurves/deep_calculator.py` at publish | DEEP (`www/calculators`), the `library` release |
| EASI method package (*planned*) | StreamCurves EASI exporter | EASI (`EASI_METHOD_PACKAGE`), the `library` release |
| EASI method files in `apps/easi/data` | today: hand-promoted, digest-pinned; after adoption: the EASI exporter | EASI, StreamCurves' vendored copy (`_vendor/easi`), the EASI calculator generator |
| Library catalog and manifests | `library.publish_version` (maintainer checkout, `STAF_LIBRARY_PUBLISH=1`) | StreamCurves, DEEP, `scripts/library_release.py` |
| Release feeds `library.json` / `library-v2.json` | `scripts/library_release.py` (CI) | StreamCurves gallery, DEEP remote library |
| Evidence packages (*planned*) | evidence producers (`tools/easi-national/builder/evidence_export.py`; the NRSA archive builder) | StreamCurves evidence store, refit and exploration |
| SQT source registry (*planned*) | `scripts/build_sqt_registry.py` | StreamCurves source dialog and candidate register |

A consumer never writes what it reads. StreamCurves' in-process copy of EASI (`_vendor/easi`)
stays the operational method: DEEP builds read its pressure screen, its catalog
(`config/fixed_criteria.yaml`, CURVE-11) and its CWA mapping, so an EASI draft is evaluated only
in a worker process and never reaches those paths.

## Assessment types

Every library assessment has a type. Absence means `deep`, so every existing manifest, catalog
entry, pack and bundle keeps its meaning.

| Type | Consumer | Version payload | Scope |
|---|---|---|---|
| `deep` | DEEP | `assessment.deep.json` (+ calculator) | one Level III ecoregion or one state |
| `easi` (*planned*) | EASI | `method.json` + `method/` files | one national method with internal NARS-9 and slope-class strata |

An `easi` version is never written under DEEP's file names, never listed in the schema-1 release
feed and never baked into DEEP. EASI is one versioned national method: its geography is stated as
national with the strata and fallbacks inside the method, never as 85 regional copies or a
placeholder region.

## Identities

Analytical identity is separate from packaging, review and lifecycle.

- **DEEP content digest** (unchanged): `library.content_digest` over `{metricsByFunction,
  regionCode}`. Lifecycle, validation, annotations outside `metricsByFunction` and the polygon are
  outside it.
- **EASI `method_version()`** (unchanged, the operational identity): the first 12 hex of SHA-256
  over the criteria-set name, the evaluator sources (`scoring.py`, `screening_methods.py`,
  `config.py`, `watershed.py`, `assessment.py`, `bieger.py`, `geo.py`, `datasources/fabric.py`,
  `national/records.py`, `metrics/*.py`) and the method files, each as name + raw bytes. The
  release method is `b2e3033116e3`. National results and calculators carry it.
- **EASI `packageDigest`** (*planned*): SHA-256 of the canonical JSON `{file name: sha256}` of the
  method files. It changes exactly when method data changes.
- **EASI `evaluatorDigest`** (*planned*): SHA-256 over the evaluator sources above (name + bytes).
  `method_version` is a function of (criteria set, evaluator sources, method files), so a package
  records the `method_version` it was validated under and EASI recomputes it on activation.
- **Evidence package digest** (*planned*): SHA-256 of the canonical manifest without its creation
  time. Files are identified by SHA-256 and byte size, never by path or modification time.
- **Candidate id** (*planned*): SHA-256 of the canonical identity tuple (below).
- **Project id**: a UUID per project file (`project_meta`), unrelated to any analytical identity.

Author notes, unselected alternatives, reviewer comments and lifecycle records never enter a
content digest, a `packageDigest` or a `method_version`.

Hashes of text files are taken on the bytes a clean checkout holds. The root `.gitattributes`
checks text out with LF, and pins the few files whose recorded bytes are CRLF (`-text`). An older
working copy may still hold CRLF copies of LF files (the curve artifact's `curveEngineSha256`
`a44a89f8...` is `streamcurves/curves.py` with CRLF endings; the same file hashes `413073bc...`
with LF). Records that cite code therefore also carry the LF-normalized hash.

## EASI method package (*planned*)

The consumer projection EASI loads, and the payload of an `easi` library version.

```
method.json            envelope (below)
method/<file>          the method files, byte for byte
```

```json
{
  "schema": "staf-easi-method", "schemaVersion": 1,
  "methodId": "easi-screening", "version": 1, "status": "draft",
  "label": "Alternative 2: NARS-9 references", "criteriaSet": "regional",
  "identity": {"methodVersion": "b2e3033116e3", "packageDigest": "sha256:...",
               "evaluatorDigest": "sha256:...", "scoringIdentity": {"alternative_id": "alternative-2"}},
  "evaluator": {"app": "easi", "engineApi": 1, "operators": ["threshold", "..."],
                "capabilities": ["curve-bands-0.39-0.69", "anchors-13-8-3", "cwa-rollup-1"]},
  "files": {"screening-methods.json": {"bytes": 100718, "sha256": "78c1e292..."}}
}
```

Method files: `screening-methods.json`, `reference-curves.json`, `easi-metrics.json`,
`cwa-mapping.json`, `functions.json`, `ecoregion-crosswalk.json`, `scoring-identity.json`,
`nars-ecoregions-9.geojson.gz` (the data files of `method_version`). Evaluator assets stay with
the app and are not editable in a package: `physio_divisions.geojson` (Bieger regions),
`ecoregions_l3.geojson`, `nrsa-2018-19-evidence.json.gz` (legacy tier).

Rules:

- EASI validates before use: schema, allowed file names and types (data files only; no code),
  sizes, SHA-256, the catalog validator, `engineApi` compatibility. An unknown `schemaVersion` or
  `engineApi` is refused with a message; nothing is partly applied.
- `EASI_METHOD_PACKAGE=<zip or folder>` activates a package at process start (a verified,
  content-addressed data folder); unset means the built-in `apps/easi/data` (rollback).
- One active method per process. `activate()` (tests, workers) resets every method cache and
  asserts the recomputed `method_version`. Every result is stamped with its method identity.
- Unchanged files are copied verbatim, so the unedited baseline exports to `b2e3033116e3`. An
  edited file is re-serialized and gives a new `packageDigest` and `method_version`.

Authority: the importer (`scripts/import_easi_method.py`) reads `apps/easi/data` once to create
the authored package; the exporter (`scripts/export_easi_method.py`) writes packages from it. The
two never run implicitly. Until the owner adopts the flip, a drift gate keeps the library's EASI
version and `apps/easi/data` byte-identical; after adoption the exporter is the only writer of
those files.

Publishing, activating and recomputing are three separate operations. Publishing stages a version
in the library; activation points a running EASI at a version; recomputing national results is a
builder job. None triggers another. Stored national results bound to another method stay flagged
as outdated (`bundle.identity_error`, `require_current`).

## StreamCurves project files

A project is a zip `<Name>.streamcurves`: `project.json`, `session.streamcurves.json` and
`origin/*` (`streamcurves/project_file.py`). Format 1 is every existing DEEP project.

*Planned:* format 2 adds `assessment_type` and, for EASI, the parts `easi/package.json`
(lineage, candidate register, decisions, evidence manifest references, recipes, notes) and
`easi/method/<file>`; the session then holds interface state only. A DEEP project that carries
content an older app would drop (added SQT candidates, final-selection decisions) is also written
as format 2. StreamCurves 1.0.0 refuses a format-2 file with "update the app" instead of opening
it and dropping what it cannot read. Packs of format-2 projects are named `-p2-`.

## Library feeds (*planned*)

| Feed | Schema | Lists | Readers |
|---|---|---|---|
| `library.json` | 1 (frozen) | `deep` assessments only, exactly as today | StreamCurves 1.0.0, the deployed DEEP, new DEEP |
| `library-v2.json` | 2 | every type, each entry typed, with typed assets | new StreamCurves |

New StreamCurves reads `library-v2.json` and falls back to `library.json`. `library.json` stays
identical to the pre-change build apart from its build stamp (`generatedAt`, `source.commit`).
EASI assets: the pack (`<id>-v<N>-p2-<sha8>.streamcurves`), the method package
(`<id>-v<N>-<sha8>.easi-method.zip`) and evidence manifests. Asset names carry a content hash.

## Evidence (*planned*)

Three roles stay separate: **development** (fitting), **evaluation** (validation) and
**operational** (site inputs). A curve's evidence names its role, and a `dependsOn` list names
upstream packages, so a dependency such as EASI-screened reference sites under a DEEP curve stays
visible when agreement between the two is discussed.

```json
{
  "schema": "staf-evidence-package", "schemaVersion": 1,
  "packageId": "easi-dev-members", "version": "2026.09.15-baseline",
  "roles": ["development"], "reproducibility": "refittable",
  "files": {"members.parquet": {"bytes": 0, "sha256": "...", "rows": 0, "columns": ["..."]}},
  "dictionary": {"woody_wsrp100": {"units": "%", "definition": "..."}},
  "sources": [{"id": "streamcat", "citation": "...", "vintage": "...", "url": "..."}],
  "recipe": {"engine": {"path": "streamcurves/curves.py", "sha256": "...", "sha256_lf": "..."}},
  "dependsOn": [{"packageId": "...", "digest": "sha256:..."}],
  "redistribution": {"status": "public", "notes": "..."},
  "limitations": ["..."], "created": "2026-09-23T00:00:00Z"
}
```

Reproducibility levels: **reviewable** (the values behind a curve can be inspected),
**refittable** (the curve can be refit from preserved development evidence) and **regenerable**
(the evidence can be rebuilt from original sources). A package declares its level and itemizes
what is missing. The store downloads with resume into `.part` files, verifies size and SHA-256
before a package is ready, extracts with path checks, accepts data file types only, and reuses
verified packages offline. A rolling release URL is a location, not an identity.

## Candidates and final selection (*planned*)

One vocabulary for every alternative considered, in DEEP and EASI:

```json
{
  "candidateId": "cand-<sha12>",
  "identity": {"assessmentType": "deep", "subject": {"kind": "metric", "id": "..."},
               "sourceKind": "fitted", "sourceRef": {}, "variant": null,
               "applicability": {"geography": {"kind": "ecoregion", "code": "50"}, "strata": {}},
               "methodVersion": "...", "dataFingerprint": "..."},
  "purpose": "operational", "campaign": null,
  "definition": {}, "evidence": {}, "limitations": [],
  "eligibility": {"status": "eligible", "reasons": [], "checks": []}
}
```

Source kinds: `fitted`, `carried`, `national`, `modeled`, `published_benchmark`, `fixed`, `sqt`,
`owner_entered`, `borrowed`, `earlier_version`, `imported_alternative`. `purpose: evaluation` marks
a fold fit that can never be selected.

Decisions are per candidate, function and applicability, with `decision` (`selected` or
`not_selected`), `reason`, `rule`, `decidedBy` (`automated`, `imported`, `person`), who, when and
`basisDigest` (the candidate's analytical content when decided). The displayed status is one of:
selected, eligible but not selected, excluded or unsupported, not evaluated, failed to build,
superseded. A change to a candidate's analytical content flags its decisions for re-review and
keeps them in the history.

For DEEP the register is derived from the records that already exist (SELECT-04
`portfolioSelection`, basis-ladder refusals, CURVE-07 holds, carried metrics, REF-15 decisions);
REF-15 stays the only writer. For EASI the register and decisions live in the project package.
The register rides in projects, sessions and provenance, never in a DEEP bundle or an EASI method
package. Missing historical decisions are stated as missing; history starts at import.

## Compatibility rules

- Absence semantics: a missing `assessmentType` is `deep`; a missing field in any record keeps its
  pre-change meaning.
- Newer schemas and formats are refused whole, with a message, never partly applied.
- `library.json` (schema 1) never lists a non-DEEP entry; its bytes change only by build stamp.
- Historical library versions, frozen curve coordinates, study receipts and published artifacts
  are never rewritten.
- An installed copy stays a user copy: it downloads, revises and sends projects back; canonical
  publication is a maintainer checkout operation (`STAF_LIBRARY_PUBLISH=1`).
