# EASI addendum to evaluation protocol V1, Round 4 of the national assessment campaign

Companion prose of `evaluation_protocol_v1_easi_addendum.yaml`, which is the machine-readable
authority. Both files are committed under `apps/stream-curves/config/methodology/` on the
candidate branch, and every EASI study or campaign manifest of Round 4 records the yaml's
sha256 beside the study runner's own protocol. Frozen before any candidate result is read;
after that a margin never moves. One re-specification per candidate family is allowed only
before that family's results are read, as a dated addendum at the end of this file.

## 1. What Round 4 decides

EASI screens a reach with 20 desktop methods, one per STAF function, on the method files the
StreamCurves exporter writes (method `b2e3033116e3`, Alternative 2 of the 2026-09-15 study:
34 reference curves, NARS-9 references for the three regional families, entrenchment by slope
class). Round 4 tests eight declared changes to those methods, one family at a time, against
the base scored through the same runner, and then scores the composition of the accepted
changes once on the retrospective cohort. The outcome is an adoption candidate (a library
version and a handoff to DEEP), never an activated method: activation, redeploy, canonical
EASI v2 and national rescoring stay the owner's (decision D4c, Round 5).

Owner decision D1 fixed the order: the shared audit first, DEEP's methodology frozen for
national generation (0.15-provisional, commit 616c04d, HANDOFF_DEEP-EASI_001), then EASI's own
rounds, one heavy campaign at a time on the machine.

## 2. Baseline

- EASI method `b2e3033116e3`, package sha256 `5b733a6b`, evaluator sha256 `594bd096`, method
  zip sha256 `3ae27405` (library `easi-screening` v1), the exporter as the sole writer of
  `apps/easi/data` (candidate workspace `easi-candidate` 8b673d0, parity 792 of 792 cases and
  540 of 540 reaches). The fixed bands (impervious cover, road density, wetland extent,
  degree of regulation, bank-height ratio, agriculture share, the benthic model, non-native
  taxa, dam proximity) are unchanged by this addendum except where a family names them.
- The rating index anchors 0.195 / 0.545 / 0.85; the ECI is the mean of the available
  discipline sub-indices, unrated functions omitted.
- DEEP's baseline for circularity accounting: methodology 0.15-provisional at 616c04d, the
  pressure screen `least-disturbed-v1` (station table sha `d9954342`), no shared file changed
  (HANDOFF_DEEP-EASI_001).
- Study runner 1.1.0 with base id `alternative-2-b2e3033116e3`; study ids
  `<ISO date>-<family>-alternatives`; the reference is the base scored through the runner,
  never a historical AUC.

## 3. Cohorts and partitions

The stored national sample (100,000 reaches, seed 17, state by Level II strata, weights
N_h over n_h; it describes the stored footprint, not CONUS) and every NRSA station COMID with
sufficient stored identity and evidence; latest eligible visit 1 per station and target;
development cohort 2013-19, retrospective cohort 2023-24. The unit of independence is the
station and its connected watershed, never observations repeated across functions.

Five spatial folds by the SHA256 rule of the 2026-09-15 study (`easi-field-fold-v1`, the
smallest connected HUC8). Every fitted reference family, the four shipped sets and any set a
candidate adds, is refitted per fold on the original strict panels excluding the fold's
watersheds under the same fit and usability rules; an unavailable fold stays unavailable and
no frozen curve fills it. The all-data fits are used for the stored-footprint effects only
and stay distinct from the fold fits. Paired comparisons use identical finite observations
and 1,000 deterministic HUC8 bootstrap draws; reference-panel stability uses 200 HUC12
resamples (seed 7). 2023-24 is retrospective, because it informed the 0.14 corrections and
the 2026-09-15 study. Reference selection, transforms, donor choices and thresholds are fitted
on development data only.

## 4. Targets and independence

T1: the ECI against the 2013-14 EPA reference and impaired designations (field cohort,
unweighted). T2: the ECI against the NRSA benthic MMI Good and Poor classes. T3: the fish MMI
classes and O/E where the station carries them (the campaign's O3 targets). Field targets for
the families that name them: the same-visit `XWIDTH / XBKF_W` low-flow ratio (percent reach
dry separate), NRSA `XFC_NAT` and `PCT_FAST` for habitat, NRSA `XBKF_H` and `BFWD_RAT` for
channel geometry at matched stations. A field observation flagged as a scoring input of a
function is excluded from that function's comparison.

Independence rules: T2 never decides for population support, because `prg_bmmi0809` was
trained on the NRSA benthic MMI (it is reported for that function as exploratory); no DEEP
output is a target and no EASI-derived screen or criterion validates DEEP (the shared evidence
contract in AUTHORING.md); agreement with previous EASI scores, with DEEP or with the fixed
criteria supports nothing; unknown model-training membership prevents an independence claim
for the model route.

## 5. Candidate families (one change each against the base)

| Id | Change | Hypothesis | Primary | Decision | Coverage effect |
|---|---|---|---|---|---|
| E1 | Low-flow rating withheld as a documented gap on NHDPlus FCODE intermittent (46003) and ephemeral (46007) reaches; perennial reaches keep the flow-variability rating | naturally intermittent reaches are rated Poor by monthly flow variability without impairment | P1 | simplification | low flow unrated on those reaches |
| E2 | The minimum-month over annual-mean EROM ratio (higher is better) with NARS-9 curves refitted on the strict panels replaces monthly flow variability | the ratio tracks low-flow condition with less climate confounding | P1 | accuracy change | none intended |
| E3 | The ICI and IWI landscape fallback of the benthic model is withheld where the model has no value; the model route is unchanged | the fallback reuses landscape evidence rated by other functions and adds no independent association | P1 on T1 | simplification | population support unrated on about 59 percent of reaches |
| E4 | Habitat provision reads bankfull width variability from the 3DEP cross sections (higher is better) with NARS-9 refits instead of corridor woody cover | in-stream habitat complexity is a channel-form construct; the corridor proxy is a shade and carbon quantity rated three times | P1 | accuracy change | reaches without a cross section lose the rating |
| E5 | BHR and ER ratings withheld where the cross-section record is flagged low quality or the ratio is outside 0 < BHR <= 2, ER >= 1 | uncertain DEM geometry rates channels at random | P1 | simplification | geometry functions unrated where withheld |
| E6 | Agriculture enters once: sediment supply rates K factor and road density only, and the agriculture-share method for Bed composition is withheld | the repeated input inflates cross-function agreement without adding association | P1 | simplification | Bed composition unrated everywhere |
| E7 | A composite's index is the mean of its inputs' indices instead of the worst or best of them | the extreme-of composite makes one input decisive and discontinuous | P1 | accuracy change | none |
| E8 | The ECI is reported with its completeness and as an interval (unrated as Poor for the lower bound, Good for the upper); comparisons paired on identical rated sets; the point ECI unchanged | reaches with few rated functions read more favorably than complete reaches | P5 | presentation | none |

Every mechanism is parity preserving when absent (the base package scores identically): an
applicability rule on a context input or on the evidence quality record that withholds a
rating with a documented-gap statement (K1, with the 3DEP provider's quality flags exposed,
K2); a refit of a registry quantity (`q_min_ratio`, `bankfull_width_cv`) into a curve set with
a method variant that reads it (K3); a `mean_index` operator (K4); rollup reporting fields
(K5). Each knob exists before its arm runs, as the Round 2 engine keys did.

## 6. Outcomes and margins

| Outcome | Measure | Margin | Basis |
|---|---|---|---|
| P1 independent association | paired AUC delta on T1 and T2, frozen and held-out designs, identical finite observations, 95 percent HUC8-bootstrap intervals from 1,000 draws; T3 reported | an accuracy change is adopted when the primary target's interval excludes 0 and the median delta is at least 0.01 with the other target's lower bound at least -0.01, in both designs; a simplification when both targets' lower bounds are at least -0.01 in both designs and rating availability is unchanged or changed by documented gaps only | the 2026-09-15 study rule and the campaign's O3 block; 0.01 as an accuracy margin is an uncalibrated operating choice |
| P2 function and regional review | per-function and per-NARS-9 paired AUC, signed Spearman and weighted-kappa deltas; supported when the sample floor holds and at least 800 of 1,000 draws are valid | a supported AUC delta interval wholly below -0.01, or a supported Spearman or kappa interval wholly below zero, retains the base | the study's review rule; no equivalence margin for correlation or kappa |
| P3 coverage and footprint | rating availability per function, changed share, paired mean ECI delta on the weighted stored sample | reported; never traded against P1 or P2; a withheld rating is a documented gap, never a favorable rating | |
| P4 stability | class-flip share over 200 HUC12 reference-panel resamples for a refitted family; threshold sensitivity of fixed bands | the flip share may not rise by more than 0.02 | operating choice |
| P5 completeness optimism | mean ECI of reaches with fewer than 15 rated functions minus complete reaches within the same Level II stratum, weighted | E8 adopted when the difference exceeds 0.02 | operating choice on the ACC-06 scale |
| P6 usability | inputs per function, distinct sources, runtime, failures, unavailable services | reported | |

Every number labelled "operating choice" is uncalibrated: stated before the results so it
cannot be chosen after them, and not a claim of ecological truth.

## 7. Multiplicity, stopping, claims

Eight families, one primary comparison each; Benjamini-Hochberg q <= 0.10 across the eight
primaries is reported as supporting evidence, and adoption rests on the margins. Function and
NARS-9 subgroups are exploratory, and P2 is the block. Stop after the declared families; a
family without a passing member is rejected and the base kept; the finalist composition is
checked once on the development cohort and scored once on the retrospective cohort. A round
is rejected when no candidate meets its margin, or meets it only by coverage accounting or by
lowering reference quality.

Parity cases, digests and serial and parallel equivalence support software verification. P1,
P2 and the field agreements support proxy meaning and association, never validation.

## 8. Sequence

Round EA: (EA1) replay the base through `evaluation_campaign` on the case set (the stored
sample and the NRSA stations) and confirm the D4c parity; (EA2) the base's P1 to P6 in both
designs. Round EB: E1 to E4. Round EC: E5 to E8. Finalist: compose the accepted changes into
one method package through the exporter (never into `apps/easi/data`), check the composition
once on the development cohort, score the retrospective cohort once, produce the final all-data
fits, and deliver the adoption candidate as a library version with a `HANDOFF_EASI-DEEP`
reply. Each arm scores an isolated package through `EASI_METHOD_PACKAGE`; workers never share
writable scoring artifacts; every run records code, configuration, data and dependency
fingerprints, partition identity, seeds, commands, logs, duration, status and output hashes.

## 9. Owner-reserved decisions taken under the standing instruction of 2026-09-25

The families, hypotheses, margins and the fold and cohort rules above are the campaign's,
listed for the owner; a change before any result is read is a dated re-specification below,
never a silent edit. Adoption of a finalist into the operational EASI is the owner's.

## Addenda

**Addendum 1 (2026-09-27, before any Round 4 result was read): E2 fits and rates perennial
reaches.** The K3 refit of `q_min_ratio` grouped by stratum only gave no usable curve for
SPL (q25 = 0), XER (q25 = q50 = 0) and the national fallback (q25 = 0): in those panels more
than a quarter of the reference members have a zero minimum month, and the fit rule refuses
a degenerate lower quartile. E2 as written cannot be built. E2 is re-specified as: the
minimum-month over annual-mean EROM ratio (higher is better) with NARS-9 curves and a
national fallback refitted on the strict panels' perennial members (the registry's own
`fcode_class` split of `q_min_ratio`), rating perennial reaches; naturally intermittent and
ephemeral reaches (NHDPlus FCODE 46003, 46007) are withheld with E1's documented-gap
statement. The paired comparison against the base therefore runs on perennial reaches, the
ratio against monthly flow variability; the withheld share is reported under P3. Owner-
reserved decision taken under the standing instruction of 2026-09-25, listed for the owner.
