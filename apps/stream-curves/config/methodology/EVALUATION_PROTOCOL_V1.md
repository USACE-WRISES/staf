# Evaluation protocol V1 (Pre-registration V), Round 2 of the national assessment campaign

Companion prose of `evaluation_protocol_v1.yaml`, which is the machine-readable authority. Both
files are committed under `apps/stream-curves/config/methodology/` and every Round 2 harness
output records their sha256. Frozen at gate G2 before any candidate result is read; after that a
margin never moves. One re-specification per candidate family is allowed only before that
family's results are read, as a dated addendum at the end of this file.

## 1. What Round 2 decides

Methodology 0.14-provisional builds a DEEP assessment for a Level III ecoregion by walking one
source hierarchy per metric (local reference pool, regional pools, national donors, modeled
reference, published benchmark, fixed criterion, else withheld) and filling each of the 20 STAF
functions to two metrics. Round 2 tests ten declared changes to that workflow, one family at a
time, against a full-refit baseline of the current rules, on a development set of 14 regions,
and then scores the composition of the accepted changes once on 12 reserved regions. The
outcome is methodology 0.15-provisional and standing-decision policy 1.3, the frozen rule set
Round 3 applies to every CONUS region.

## 2. Baseline

- Methodology 0.14-provisional at main `30f57c5`; the campaign's Round 1 code at `6535837`
  (traceability and batch changes only; every published version replays, proven by
  `tests/test_replay_digests.py`).
- NRSA multi-cycle archive `multi-cycle-v1` (manifest 3a6dc372...), the DATA-10 wadeable frame
  (NHDPlus V2 stream order 1 to 5, canals out: 3,267 stations, 770 strict least-disturbed).
- Value policy: the seven published versions record `latest_non_null_index_visit` (v1). Every
  Round 2 build, the Round A refit included, reads the archive under `newest-nonnull-v2` (v1 plus
  non-finite and out-of-domain values read as missing, and 2013-14 residual pool depth derived
  from `phab_RP100`). The difference is a data correction, not a methodology candidate; A1
  (replay under v1) proves the published digests still reproduce, A2 (refit under v2) is the
  baseline every arm is compared with.
- Published versions: CBR v1, CGP v1, NLF v1, ECBP v8, NEH v9, SEP v2, IP v6.

## 3. Regions

Development set (14): the seven published regions (58, 71, 55, 65, 27, 50, 13) plus 43
Northwestern Great Plains (NPL), 17 Middle Rockies (WMT), 1 Coast Range (WMT), 59 Northeastern
Coastal Zone (NAP), 47 Western Corn Belt Plains (TPL), 75 Southern Coastal Plain (CPL) and 45
Piedmont (SAP). Every NARS-9 group is represented. Testable reference-recovery cells (at least
12 strict stations and 25 in frame): 58, 50, 13, 65, 43, 17, 1. TPL has no testable cell, so TPL
is evaluated through the Regime E emulation of Pre-registration II (35, 43, 65, 67) and through
coverage.

Reserved finalist cells (12), never touched during Rounds A, B and C and scored once by the
finalist: 4, 15, 16, 21, 78 (WMT), 18, 80 (XER), 35 (CPL), 62, 82 (NAP), 66, 67 (SAP). Rounds III
and IV of the basis validation already scored these cells, so the finalist evaluation is
labelled retrospective nested; the temporal check and O3 are the only evidence no selection step
has read.

## 4. Partitions and leakage

The unit is the station (`station_key`); visit 2 rows serve the precision estimate only.
Bootstraps resample HUC12 clusters (HUC8 where HUC12 is missing); the association folds (O3) are
HUC8, matching the EASI alternatives study. Reference recovery withholds one Level III region at
a time. A withheld region leaves the local pool, every regional pool, the national donor pool,
model training, the EPA agriculture-limit calibration (`reference_pool.epa_agriculture_limit`
reads the whole table today and is made fold-aware for Round 2) and the scale registry (a
per-fold registry, or the leave-one-region-out check that must reproduce at least 95 percent of
registry decisions). The target's own non-reference stations stay, as production would have
them. Seeds: the cell seed is the CRC32 of metric, region, basis, regime and seed (the
`basis_recovery` convention); the development and evaluation split is seed 11; 200 bootstrap
resamples.

Temporal sensitivity is retrospective, because 2023-24 informed the 0.14 data corrections: fit
on 2013-14 and 2018-19, score the 2023-24 reference stations; report the per-cycle quartile shift
in IQR units and the DATA-11 same-visit versus mixed-year class flips.

## 5. Candidate families (one change each against the Round A baseline)

| Id | Change | Hypothesis | Primary outcome | Decision type |
|---|---|---|---|---|
| B1 | Pool rule narrowest_adequate (n >= 20) instead of the first passing pool | adequate wider pools classify closer to reference than exploratory narrower ones | O1 | accuracy change |
| B2 | Regional screen relaxation off (strict screen in every pool) | relaxed pools (agriculture up to 47.8 percent in NPL, 77.4 in TPL) promote sites | O2 | accuracy change |
| B3 | One search order (l3, l2, l1, nars9) for every family | family-specific orders are no better than noise | O1 | simplification |
| B4 | DATA floors 15/25 unstratified and 8/20 per stratum | floors change class stability, not agreement | O5 | simplification |
| C1 | SELECT-04 fill to 1; a second metric only if abs Spearman < 0.65 and CURVE-12 AUC >= 0.55 | two-per-function adds redundancy, not accuracy | O3 | simplification |
| C2 | CURVE-12 gate: AUC < 0.55 or inverted withholds a new curve | gating removes curves without ecological signal at little coverage cost | O3 | accuracy change |
| C3a | Declared zero-inflated metrics (reference zero share > 0.25) take a two-part curve (a zero point scoring 0, the IQR seed on the positive part) instead of degenerate_q25; a pool whose positive part is too small for the seed is withheld | a fallback ramp on a zero-heavy pool misreads the median reference station | O5 | accuracy change |
| C3b | Tail endpoints 0.5 / 1.5 / 2.5 IQR instead of the engine's 0.3 / 4/3 / 7/3 IQR | endpoints move class boundaries more than sampling noise | O5 | simplification |
| C4 | Assignment changes from the evidence table's revise dispositions (bfi as covariate, chlorophyll in one function, DOC decided, nitrogen fraction on the benchmark's, dam pressure scored once, crops plus hay as the agricultural criterion, wetland zero handling, wood's function, residual pool depth's function, the fish proportion set) | construct-faithful assignments score functions once and drop covariate-like metrics | O3 | accuracy change |
| C5 | REF-15 extension on: verified state SQT curves compete with fitted curves (experimental config root only) | state curves add defensible coverage where verified | O4 | coverage only; an unresolved scoring or applicability question blocks adoption (D4a) |
| C6 | Carry-forward off (full refit) as the D3 reference arm | published carry-forward hides drift | O1 | reference arm |

## 6. Outcomes and margins

| Outcome | Measure | Margin | Basis |
|---|---|---|---|
| O1 reference recovery | class agreement with the withheld strict reference; A1 exceedance >= 0.10 per cell; paired delta on identical stations with a 90 percent HUC12-cluster bootstrap | an accuracy change is adopted when the interval excludes 0 and the median delta is >= 0.05; a simplification on noninferiority, lower bound >= -0.05 | 0.10 and the 2/3 cell rule are inherited from Pre-registration II; 0.05 is an uncalibrated operating choice (one site in twenty) |
| O2 directional bias | median net optimism (ACC-06) | <= 0.05, and no worsening beyond 0.02 | ACC-06, uncalibrated |
| O3 independent association | AUC and Spearman of the function score against NRSA benthic MMI, O/E and fish MMI at non-reference in-frame stations, HUC8 folds | a paired AUC delta whose lower 95 percent bound is below -0.01 blocks | the EASI alternatives study rule, shared so both tiers use one margin; operating choice |
| O4 coverage | functions supported of 20, curves by basis, withheld count | reported; never traded against O1 to O3 | |
| O5 stability | bootstrap class-flip rate at reference stations (200 HUC12 resamples) and the ACC-04 shift | the median flip rate may not rise by more than 0.02 | operating choice |
| O6 usability | field metrics per assessment, distinct procedures, runtime, failures | reported | |

Every number labelled "operating choice" is uncalibrated: it is stated before the results so
it cannot be chosen after them, and it is not a claim of ecological truth.

## 7. Multiplicity, stopping, claims

Ten families, one primary comparison each. The structural protection of Pre-registration IV
stands (at least four cells and the 2/3 rule); Benjamini-Hochberg q <= 0.10 across the ten
primaries is reported as supporting evidence, and adoption rests on the margins. Subgroups
(NARS-9 group, metric family) are uncorrected and exploratory, but any NARS-9 group losing more
than 0.10 on O1 blocks adoption of that candidate.

Stop after the declared families. A family without a passing member is rejected and the
baseline kept. The reserved cells are scored once. A round is rejected when no candidate meets
its margin, or a candidate meets it only by lowering reference quality (O2) or by coverage
accounting.

Digest replay and golden masters support software verification only. O1, O2 and O5 support
reference recovery. O3 supports association, not validation: MMI reference designation shares
landscape variables with the screen. Agreement with EASI or with the fixed criteria supports
nothing, because the reference screen is EASI's and the pressure metrics carry EASI's bands.

## 8. Sequence

Round A: (A1) replay the seven published versions with carry-forward and confirm the digests;
(A2) full refit of the 0.14 rules on the development set under value policy v2, with owner holds
and REF-15 decisions listed and not applied; (A3) baseline O1 to O6 on the development cells
through `run_hierarchy_test.py` and `run_model_test.py` restricted to those cells, and
`basis_validation.yaml` regenerated from development cells only for use during the rounds.

Round B: B1 to B4. Round C: C1 to C6. Each candidate is compared paired on identical eligible
stations; coverage change is reported separately (functions, metrics and regions gained or lost,
basis mix).

Finalist: compose the accepted changes, check the composition once on the development cells for
interaction, freeze (methodology 0.15-provisional, policy 1.3), score the reserved cells once,
then produce final all-data fits distinct from the fold fits.

## 9. Owner-reserved decisions taken under the standing instruction of 2026-09-25

The owner instructed the campaign to continue all phases without pausing at gates. Round 2
therefore takes these decisions itself and lists them for the owner: which candidates pass
(by the margins above, never by judgment), whether the six modeled specifications of
RECOMMENDATION_IV enter Round B (they do, as REF-13 candidates under their existing registry
limits; ECBP's out-of-limit curves stay refused), and the relaxation policy where NARS-9
subgroups disagree (the subgroup block above decides). The D3 differences between the
re-derived and the recorded owner decisions are decided in Round 3 with the default "keep the
recorded decision unless it can no longer apply", every item listed.

## Engine keys

The candidate knobs land on these configuration keys (defaults equal today's behavior; a knob
joins the inputs digest only when set away from its default): `reference_pool.ladder_rule`
(B1), `reference_hierarchy.regional_screen.enabled` (B2), `reference_transfer.yaml`
top-level `search_order` (B3), `data_rules.*` floors together with `acceptance.sample_adequacy.*`
(B4; the two blocks must move together), `metric_portfolio.fill_to`, `second_metric_rule`,
`second_metric_max_abs_spearman`, `second_metric_min_auc` (C1), `curve12.gate`, `curve12.min_auc`
(C2), `curve10.zero_inflated_share`, `curve10.zero_inflated_handling` (C3a),
`curve10.tail_offsets_iqr` (C3b), the generated metric-map variant (C4),
`owner_decisions.alternatives_over_fitted` (C5), the `--refit all` stage flag (C6).

## Addenda

- 2026-09-25, before any Round B or C result was read: C3b re-specified. The first draft named
  0.3 / 4/3 / 7/3 IQR as the alternative and 0.5 / 1.5 / 2.5 as the baseline; the engine's
  actual default is 0.3 / 4/3 / 7/3 (the golden masters pin it), so the candidate is now the
  wider 0.5 / 1.5 / 2.5 set. Hypothesis, outcome and decision type unchanged.
- 2026-09-25, after A2's first six regions were staged and before any outcome was read
  (operational, no hypothesis, outcome, margin or candidate changes): every experimental arm
  stages with the same blanket documented-gap file
  (`pilot\round2\coverage_exceptions.experimental.json`, all 20 STAF functions, reason
  `no-suitable-metric`, recorded by the rehearsal label), because a full refit that supports
  no metric for a function (A2, Eastern Corn Belt Plains: Habitat provision) is otherwise
  refused at the staged publish and drops out of the paired comparisons. The publish drops an
  exception that names a covered function, so curves, pools and choices are unchanged; O4
  counts a documented gap as unsupported; the file's sha rides in every arm's inputs digest,
  so A2's already-staged regions are staged again under the same flags. No arm's staged
  version can be promoted (experimental root, rehearsal label).
