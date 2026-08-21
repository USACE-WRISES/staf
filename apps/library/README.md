# STAF Assessment Library

Canonical, version-controlled home for **completed detailed STAF assessments** — the
reference-curve sets built in **StreamCurves** and consumed by **DEEP**.

This folder is the *contract*, not shared code. StreamCurves and DEEP each carry their own
small reader/writer that follows the format below (the same "mirror the format, don't share the
file" pattern the repo already uses for `.deep.json` bundles). This is deliberate: the four
STAF apps deploy as isolated Posit Connect Cloud content items with no shared runtime
filesystem, so nothing here can be imported across apps at runtime.

## Who writes/reads what

| App | Access | When |
|---|---|---|
| StreamCurves (`streamcurves/library.py`) | read + **write** (publish) | Local/desktop only — the folder is reachable and writable there. On the cloud it degrades to saving a Draft session file to send to the publisher. |
| DEEP (`deep/library.py` + `scripts/build_deep_data.py`) | read | Dev/desktop: merges the live library over its baked registry. Cloud: reads only the baked snapshot in `apps/deep/data/deep-assessments.json`, produced by the bake step. |

A **builder** develops curves (anywhere) and saves a StreamCurves `*.streamcurves.json`
**session**. A **publisher** (local/desktop) promotes a session into a new library **version**.
DEEP always uses the **latest** version of each assessment; older versions stay here for
reference.

## Layout

```
apps/library/
  README.md                       # this contract
  catalog.json                    # index the apps read; points each assessment at its latest version
  assessments/
    <assessment-id>/
      manifest.json               # identity + region + full version history
      status.json                 # lifecycle history (preliminary, under_review, certified, revised, retired)
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
      "defaultVersion": 3,
      "contentDigest": "sha256:...",
      "validationState": "unvalidated",
      "validationSummary": null,
      "provenanceState": "present"
    }
  ]
}
```

`defaultVersion` is the latest certified version, else the latest preliminary one.
`validationState` and `validationSummary` come from the assessment's `validation.json`
(the last state record for the default version); `provenanceState` is `present` when the
default version folder carries `provenance.json`.

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

## Publishing (summary)

1. Builder saves a StreamCurves session (Save > Draft) and shares the file.
2. Publisher (local/desktop) opens StreamCurves, opens the session (header **Open**), goes to
   **Publish** (header **Save** or the stage banner), picks Preliminary or Final (Final =
   validated + certified; certification requires the Validated box), and **Publishes**.
   StreamCurves:
   - writes `assessments/<id>/vN/` (bundle + full session + meta),
   - writes `provenance.json` beside the bundle when the publisher supplies one (the agent
     always does; the interactive publish builds a leaner one),
   - updates `manifest.json` + `catalog.json` (+ `status.json` / `validation.json`),
   - re-bakes DEEP's registry (`apps/deep/scripts/build_deep_data.py`) so the cloud DEEP will
     ship the new latest version.
3. Publisher commits `apps/library/**` and `apps/deep/data/**` and pushes; redeploy DEEP.

Changing a published version's status later is republish-only: open the assessment, make any
updates, and publish again at the new level (a new version). `set_version_status` remains
available from Python for scripted actions such as retiring a version.
