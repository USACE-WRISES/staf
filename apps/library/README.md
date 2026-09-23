# STAF Assessment Library

Canonical, version-controlled home for **completed STAF assessment methods** built in
**StreamCurves**: the detailed assessments (reference-curve sets) **DEEP** runs and, typed
`easi`, the screening method **EASI** runs (see [Assessment types](#assessment-types)).

This folder is the *contract*, not shared code. StreamCurves and DEEP each carry their own
small reader/writer that follows the format below (the same "mirror the format, don't share the
file" pattern the repo already uses for `.deep.json` bundles). This is deliberate: DEEP
deploys as an isolated Posit Connect Cloud content item and StreamCurves runs on users'
machines (StreamCurves Desktop), with no shared runtime filesystem, so nothing here can be
imported across apps at runtime.

## Who writes/reads what

| App | Access | When |
|---|---|---|
| StreamCurves (`streamcurves/library.py`) | read + **write** (publish) | Writes only in a maintainer's STAF checkout with `STAF_LIBRARY_PUBLISH=1`. An installed StreamCurves Desktop never writes it: its Assessment library downloads versions from the `library` release, and a revision goes back to the maintainer as a `.streamcurves` project file. |
| DEEP (`deep/library.py`, `deep/remote_library.py`, `scripts/bake_library_into_deep.py`) | read | Every DEEP merges the `library` release's preliminary and final versions over the baked snapshot in `apps/deep/data/deep-assessments.json`. A checkout also merges this folder on top. |
| The `library` release (`apps/stream-curves/scripts/library_release.py`) | read | CI (`.github/workflows/library-release.yml`) rebuilds it on every push to `main` that touches this folder: `library.json` plus each version's pack, DEEP bundle and calculator. See `desktop/RELEASING.md`. |

A **builder** or reviewer develops curves in a StreamCurves project (a `.streamcurves` file)
and sends it to the **maintainer**, who publishes it from a checkout as a new library
**version**. DEEP defaults to the latest final version of each assessment, else the latest
preliminary one; older versions stay here for reference.

## Layout

```
apps/library/
  README.md                       # this contract
  catalog.json                    # index the apps read; points each assessment at its latest version
  assessments/
    <assessment-id>/
      manifest.json               # identity + region + full version history
      status.json                 # lifecycle history (draft, preliminary, under_review, certified, revised, retired)
      validation.json             # append-only validation records + state history (unvalidated | validated)
      v1/
        assessment.deep.json      # DEEP bundle (curves inlined) + embedded "library" block
        session.streamcurves.json # FULL editable StreamCurves session (round-trip): data,
                                  # screening tables, curves, decisions — reopen and revise
        meta.json                 # this version's metadata (convenience copy)
        provenance.json           # run manifest + decision records + review queue (agent and
                                  # interactive publishes since 2026-08; absent on older versions)
      v2/
        ...
```

`<assessment-id>` is a stable kebab-case slug (e.g. `eastern-corn-belt-plains`). It never
changes across versions; it is the identity DEEP keys on.

## Assessment types

Every assessment has a type in its `manifest.json` (and its `catalog.json` entry):
`assessmentType`. **Absent means `deep`**: every assessment published before typed entries is a
DEEP detailed assessment, and a DEEP entry never gains the key.

`easi` is EASI's screening method: one national method (region `{kind: "national", code:
"CONUS"}`) with its NARS-9 and slope-class strata inside it, never regional copies. Its versions
hold the method package EASI loads, not a DEEP bundle:

```
assessments/easi-screening/
  manifest.json          # assessmentType "easi"; versions carry contentDigest (the packageDigest) and methodVersion
  status.json            # the same lifecycle as DEEP
  v1/
    method.json          # the package envelope: identity (methodVersion, packageDigest, evaluatorDigest), requires
    method/<file>        # the eight method files, byte for byte (`-text` in .gitattributes)
    calculator/<file>    # the EASI calculator generated from exactly these files (when present)
    project.easi.json    # the authoring record: lineage, candidate register, decisions, notes, history
    cases.json           # the preview case set StreamCurves scores a draft on
    meta.json, provenance.json
```

Readers keep to their type: DEEP's readers and bake skip every entry whose type is not `deep`;
StreamCurves' DEEP paths (carry-forward, other-assessment sources, the DEEP publish page, the
calculator backfill, the rules scorecard) do too; `library.load_version_bundle` refuses an EASI
id. EASI versions are published by `library.publish_easi_version` (through
`streamcurves/easi_method/io.publish`), which checks the package the way EASI does before
anything is written; no DEEP gate, calculator or bake applies to them. Contracts:
`apps/stream-curves/AUTHORING.md`.

## `catalog.json`

Regenerated on every publish from the per-assessment manifests.

```json
{
  "schemaVersion": 2,
  "generatedAt": "2026-08-21T00:00:00Z",
  "assessments": [
    {
      "assessmentId": "eastern-corn-belt-plains",
      "assessmentName": "Eastern Corn Belt Plains",
      "region": { "kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains" },
      "stateCode": "",
      "stateName": "",
      "latestVersion": 3,
      "latestUpdatedAt": "2026-08-21T00:00:00Z",
      "latestPreliminary": 3,
      "latestCertified": 0,
      "latestDraft": 0,
      "defaultVersion": 3,
      "defaultStatus": "preliminary",
      "contentDigest": "sha256:...",
      "validationState": "unvalidated",
      "validationSummary": null,
      "provenanceState": "present"
    }
  ]
}
```

`defaultVersion` is the latest certified version, else the latest preliminary one, else
the numeric latest (which may be a draft: the StreamCurves picker needs an openable
default, and DEEP is protected by per-version eligibility, never by this pointer).
`defaultStatus` is that version's current lifecycle status; `latestDraft` is the newest
draft (0 if none). `validationState` and `validationSummary` come from the assessment's
`validation.json` (the last state record for the default version); `provenanceState` is
`present` when the default version folder carries `provenance.json`.

`latestVersion: 0` means no version has been published yet (a placeholder awaiting its first
publish). Such an assessment is **not** offered in DEEP until it has at least one version.

## `manifest.json` (per assessment)

Schema 2 adds `contentDigest` and `supersedesVersion` per version entry.

```json
{
  "schemaVersion": 2,
  "assessmentId": "eastern-corn-belt-plains",
  "assessmentName": "Eastern Corn Belt Plains",
  "region": { "kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains" },
  "stateCode": "",
  "stateName": "",
  "sourceCitation": "",
  "latestVersion": 0,
  "versions": [
    { "version": 1, "updatedAt": "...", "author": "...", "revisionNotes": "..." }
  ]
}
```

## `region` block

Uses StreamCurves' native region-of-applicability vocabulary (from the import wizard):

- `kind`: `"ecoregion"` (EPA Level III), `"state"`, or `"polygon"` (custom drawn area).
- `code`: `us_l3code` for an ecoregion (e.g. `"55"`), the state abbreviation for a state, or
  `"USER"` for a drawn polygon.
- `name`: `us_l3name` / state name / `"Custom area"`.
- `polygon` (optional): a GeoJSON geometry, present only for `kind: "polygon"`.

## Per-metric annotations on the bundle (2026-08-21)

Each metric entry in `metricsByFunction` may carry, beside its curve: `referenceTier`
(the tier the reference pool was drawn at, also stamped at the bundle's top level),
`referenceN` and `sampleDisposition` (the reference sample behind the curve and its
floor disposition), `metricRole` (`response` for a site-scale measurement,
`stressor_surrogate` for a landscape footprint metric), `curveCaveats` (short sentences a
scorer should read beside the number), `confidenceLabel` and `confidenceTotal` (the
builder's reviewer-priority heuristic, never a probability), and `referenceRange` (the
reference pool's observed span). They are inside `metricsByFunction`, so they are part of
`contentDigest`. DEEP shows them on the assessment card, in the metric information card,
and as scoring advisories.

## Reference support on the bundle (methodology 0.12)

A bundle built under the pressure-screen method also carries, per metric, `criteriaBasis`
(`reference` or `fixed`), `criteriaSource`, `referenceSupport` (status, level, source region,
usable n, local n, covariates, transfer risk and note), `localComparison`, `stratifier` (the
class variable, breaks and which classes have their own curve), `discrimination` and
`methodContext`, and at the top level `referenceMethod` and `insufficientReferenceSupport`
(withheld metrics, which have no curve and sit outside `metricsByFunction`). Older readers
ignore all of it.

## Artifacts

`assessments/<id>/artifacts.json` is an append-only record of files generated from a published
version. Today that is `vN/calculator.xlsx`, the Excel calculator built at publish, with its
sha256, the generator version and the bundle's `contentDigest`. `meta.json` of a published
version is never touched. The catalog reports `calculatorState` (present, stale or absent).

## Embedded `"library"` block on the bundle

Each `assessment.deep.json` carries a top-level `"library"` block so version + provenance
travel with the assessment. DEEP retains unknown top-level bundle fields
(`LoadedAssessment.raw`), so this needs no DEEP schema change — DEEP just surfaces it (the
assessment info button shows version + last-updated).

```json
"library": {
  "libraryId": "eastern-corn-belt-plains",
  "version": 3,
  "updatedAt": "2026-07-07T00:00:00Z",
  "author": "...",
  "revisionNotes": "...",
  "region": { "kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains" }
}
```

## Lifecycle

Stored status literals never change; people see display labels:

| Stored          | Displayed    | Meaning                                                        | Who sets it |
|-----------------|--------------|----------------------------------------------------------------|-------------|
| `draft`         | Draft        | Automation output, or a revision published for review; not approved yet | batch stage/promote, the headless agent, a maintainer's publish (the default) |
| `preliminary`   | Preliminary  | A human reviewed and stands behind it                          | a maintainer's publish as Preliminary, or Approve as Preliminary on a draft |
| `certified`     | Final        | Field-validated and certified                                  | Validate stage certify (gated on a validation record) |
| `under_review`, `revised`, `retired` | Under review / Revised / Retired | admin states | Python (`set_version_status`) |

Validation is a separate axis (`validation.json`: `unvalidated` | `validated`, displayed
Unvalidated / **Verified**): a version can be Preliminary and Verified before it is Final.
Only `preliminary` and `certified` are DEEP-eligible; drafts never bake.

## Publishing (summary)

1. **Automation path**: `run_region_batch.py stage` builds a region unattended and stages it
   (as a draft) under its run folder; `promote` confirms the standing decisions under the
   owner's name and publishes into `apps/library` as a **Draft** (pass `--status preliminary`
   only for a packet that was reviewed exhaustively). The Region builder inside the app
   drives the same commands.
2. **Review path**: open the version from the Assessment library on StreamCurves' start page
   (drafts are badged), review the stages, then either **Approve as Preliminary** on the
   Validate stage (records your review in place) or edit and publish a next version from
   **Publish** (Draft by default, or Preliminary; provenance carries the originating run plus
   your edits). A reviewer working in an installed copy sends the project file instead
   (Publish, **Save a copy for the maintainer**); the maintainer opens it with **Projects >
   Open project** and publishes from there.
3. **Verification path**: on the Validate stage, overlay field data, record the validation
   (the version reads **Verified**), then **Certify** (displayed **Final**).
   Publishing, approving and certifying each re-bake DEEP's registry
   (`apps/deep/scripts/bake_library_into_deep.py`).
4. The maintainer commits `apps/library/**`, `apps/deep/data/**` and
   `apps/deep/www/calculators/**` and pushes `main`. `library-release` refreshes the `library`
   release, which installed StreamCurves copies and DEEP read. Redeploy DEEP when its baked
   fallback should carry the version too.

Content never changes in place: edits are a new version. Status changes (`draft` to
`preliminary`, certification, retiring) append to `status.json` without re-minting the
version or its digest; `set_version_status` remains available from Python for scripted
actions such as retiring a version.

## The release feeds

`library-release` publishes two catalogs on the `library` prerelease:

| Feed | Schema | Lists | Read by |
|---|---|---|---|
| `library.json` | 1 (frozen) | DEEP assessments only, exactly as before typed entries | StreamCurves Desktop 1.0.0, DEEP |
| `library-v2.json` | 2 | every assessment, each entry typed (`"type": "deep"` or `"easi"`) | StreamCurves from the authoring foundation on |

A current StreamCurves reads `library-v2.json` and falls back to `library.json`. An EASI version
contributes a `-p2-` pack (a format-2 project that 1.0.0 refuses with "update the app") and its
method package (`<id>-v<N>-<sha8>.easi-method.zip`), never a DEEP bundle. `check`, `upload` and
`prune` work on the union of both catalogs, and both catalogs upload last, `library.json` at the
very end.
