# Local EASI alternatives study

This research runner preserves the current regional method as **Alternative 1**:
method `e9f472b31fe5`, 62 frozen curves, scores 13/8/3 and unchanged outcome
weights. The historical legacy baseline is a separate method.

All writes belong to `D:/Data/easi-national/review/alternative-studies/<study-id>/`.
The runner has no publication, queue, national tile or candidate staging step.
The EASI comparison page is `/local-review/alternatives/` and requires local-only
review access. It reads completed summaries; requests never run analyses.

## Run and resume

From `D:/Code/Work/staf_codex_2026-09-14/tools/easi-national`, PowerShell:

```powershell
$env:EASI_CRITERIA_SET = 'regional'
$env:OPENBLAS_NUM_THREADS = '1'
& 'D:/Code/Work/staf_codex_2026-09-14/.venv/Scripts/python.exe' -m builder.analysis.alternatives --study-id 2026-09-15-controlled-alternatives
```

The ordered stages are `snapshot`, `candidates`, `observations`, `evidence`,
`scores`, `acquisition`, `field`, `spatial`, `stability`, `report`. `--steps`
accepts a subset in that order. Dependencies must have current completed
receipts. `--workers` limits candidate subprocesses, up to four alternatives.
The study lock records its PID. If a process was killed, check that PID is gone
before removing only that study's stale lock. No automatic lock takeover occurs.

Each stage binds computation sources, input receipts and output hashes. Changed
upstream source evidence requires a new study. Changed code invalidates its
stage and dependents. Fine-grained evidence/scoring checkpoints are reusable
only when their data and computation signatures match. A partially completed
study has no completion receipt and its results are withheld by the page.

## Controlled scoring authorities

| Alternative | Sole change from Alternative 1 | Curves |
|---|---|---:|
| 1 | None | 62 |
| 2 | Three Level II families use original NARS-9 fits | 34 |
| 3 | Those three families use their existing national curves | 7 |
| 4 | Woody Level II 8.2 selects its existing national fallback | 61 |

The original registry is verified against the frozen artifact's provenance.
Unchanged curves are copied without refitting. Every scoring process gets an
isolated `EASI_DATA_DIR`; the live offline adapters preserve composite behavior,
missingness, interpolation and rating anchors. Replay checks include all scored
sample/study reaches and eligible NRSA stations against preserved analysis.
Candidate checks require identical available ratings and enforce function and
geographic scope. Broad historical diagnostic scenarios are never scoring
authorities for these alternatives.

## Cohorts and inference

- All NRSA station COMIDs with sufficient stored identity/evidence. Stored reach
  records are copied verbatim. Outside-footprint stations use the existing
  offline national-cache adapters, and those records are preserved in the study.
- Exactly 100,000 unique stored reaches, state by Level II strata, proportional
  Hamilton allocation after one per nonempty stratum. SHA256 of seed 17 and
  COMID determines selection independently of input row order. Each sampled
  reach has weight `N_h/n_h`. Estimates describe the stored footprint, not CONUS.
- All scored stored Level II 8.2 reaches for actual function/ECI transitions.
  Reference stability additionally uses the full stored landscape Level II 8.2
  population; its finite and missing counts are separately reported.
- A versioned long observation ledger retains source file hashes, rows and
  columns, station, COMID, cycle, visit, date, protocol and watershed. Identical
  targets collapse; unresolved conflicts and missing identities are counted
  and excluded. Target-specific latest visit-1, development 2013-19 and
  retrospective 2023-24 cohorts remain distinct.
- The experimental low-flow ratio is same-visit `XWIDTH / XBKF_W` with a positive
  denominator. Percent reach dry is separate. `XWD_RAT` is width/depth and is a
  secondary morphology diagnostic. Raw CV, minimum-month/annual ratio and BFI
  do not acquire new Good/Fair/Poor bands. Natural intermittence is not treated
  as proof of impairment.
- Paired comparisons use identical finite observations and 1,000 deterministic
  watershed bootstrap draws. Connected station/COMID/HUC8 identities stay in
  one of five folds. Held-out reference-method fits exclude evaluation
  watersheds and refit every reference family, including entrenchment, using
  original strict panels and the same fit/usability rules. These fits remain
  separate from frozen alternatives. Unavailable national fits make a fold
  unavailable; no frozen curve fills that gap.
- Regional and national woody curves use 200 reference HUC12 resamples each,
  evaluated on exactly the same stored 8.2 values with actual rounded-point
  interpolation. The two reference panels are resampled separately; the
  resulting stability summaries are not paired ecological-performance CIs.
- Field observations flagged as scoring inputs are excluded from their affected
  function comparison and from aggregate evaluation. Unknown biological-model
  training provenance still prevents an independence claim.

## Focused diagnostics and acquisition

Shared agriculture reports paired agreement, conditional field associations and
separately labeled removal of each observed weighted contribution. Official
weights and missing-evidence rules stay fixed. Population support reports model
and integrity routes separately. Probability-bin comparisons are exploratory
unless outcome harmonization and independence are established.

Only official EPA model/site metadata and official USGS exact-COMID gage metadata
and daily mean discharge (00060/00003, available 2000-2024) are acquired. Downloads
are cached with receipts under the study. No nearby-gage substitution is made.
QC limitations, failed requests, unmatched stations, partial daily coverage,
approval flags and qualifications remain visible. Survey-era identifiers are
not presented as a complete model-training roster.

## Recommendation and delivery

A positive simplification recommendation requires both aggregate target AUC
paired 95% lower bounds at least -0.01, unchanged rating availability and review
of supported functions/regions. The implementation also requires supported
noninferiority for both watershed-held-out aggregate endpoints; insufficient or
contradictory spatial evidence retains Alternative 1. Eligible simplifications
rank by fewer curves, then benthic AUC. A recommendation never changes scoring.

The function and regional review covers latest visit-1 observations in both
frozen and watershed-held-out comparisons. A supported AUC delta interval wholly
below -0.01 blocks simplification. A supported signed Spearman or weighted-kappa
delta interval wholly below zero is an unresolved review finding that also
retains Alternative 1; no correlation or kappa equivalence margin is assumed.
These findings require the sample support floor and at least 800 valid paired
draws out of 1,000. Small or invalid descriptive comparisons do not automatically
block a recommendation, and remain visible with their limitations.

Outputs include the protected snapshot, protocol, candidate artifacts, ledger,
sample and weights, evidence copies, scores and traces, target-level CSVs,
spatial folds/refits, woody stability, acquisition receipts, bounded summary,
source inventory and a study-specific completion receipt. The national
completion receipt is never overwritten. Local review checks completion hashes
and captured source stamps before displaying results.

Validation runs from each app/tool directory with the workspace interpreter:
EASI `-m pytest`; national builder `-m pytest`; StreamCurves `-m pytest -m 'not live'`.
Canonical EASI vendoring is `apps/stream-curves/scripts/vendor_easi_engine.py`.
Preserve SFARI, DEEP, deployment records and all published methods.
