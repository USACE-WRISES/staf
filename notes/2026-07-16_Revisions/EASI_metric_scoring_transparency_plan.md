# EASI Metric-Scoring Transparency & Simplification — Working Plan

**Status:** DRAFT for revision (not yet executed). Uncommitted work will land on `main`.
**Author context:** methodology review of how EASI's ~20 screening metrics compute Good/Fair/Poor.
**Goal:** make every metric's formula and its value→rating mapping transparent in the report, and
simplify the metrics that currently rate off invented multi-variable formulas down to distinct,
defensible, traceable indicators.

> This is a reference/working doc, kept deliberately detailed so it can be revised in isolation.
> Where a choice trades "most defensible" against "most distinct," both options are recorded under the
> metric and summarized in **Open Decisions**.

---

## 1. The problem (confirmed by code audit)

### 1a. Displayed criteria are decoupled from the computed rating
`MetricResult` (`apps/easi/easi/metrics/base.py:110-121`) carries `value, value_text, rating, confidence,
source, status, note, detail, is_override` — **no field for the thresholds/formula that produced the
rating.** The Good/Fair/Poor "scoring criteria" shown in the report come from a *separate* static file,
`apps/easi/data/easi-metrics.json` `criteria{}`, authored in STAF/SQT **field-method language** (pebble
counts, leaf packs, "outfalls per 100 m", "% channel wetted"). The actual rating is computed in adapter
code from **desktop landscape proxies** (StreamCat land cover, road density, soil K-factor, slope,
sinuosity, dam storage) via hard-coded `base.band(...)` thresholds that appear **nowhere** in the report.

Pipeline: adapter returns `MetricResult` → `assessment.assess()` (`assessment.py:103-150`) joins the
criteria via `config.criteria_bands(mid)` → `easi-metrics.json` → tooltip rendered by
`app._metric_tip_html` (`app.py:111-170`) via `_metric_table` (`app.py:411-517`). The `criteria` text and
the computed `rating` never meet except that the rating selects which criteria string to show.

### 1b. ~11 of 20 metrics rate off invented, uncited weighted formulas
Hand-picked weights, normalization caps, and Good/Fair/Poor cut points with no citation anywhere.

### 1c. Three metrics display an outright false calculation line
`substrate`, `habitat_complexity`, `biological_integrity` have no `METRIC_CALCULATIONS` entry, so the
tooltip's Calculation line falls back to **"Dataset value used directly (binned to a rating)."**
(`app.py:457-458`) — false; each is a multi-variable composite.

### 1d. Several criteria describe a different quantity than the code computes
Flow Alteration (criteria "% from baseline" vs code storage/DA ratio), Concentrated Runoff (outfalls/100 m
vs road density), Invasives (% presence vs taxon count), Low-flow (% wetted vs FCODE class), Detrital
(leaf packs vs riparian veg %).

---

## 2. Full audit table (all 20 metrics — current state)

Direction: HW = higher-is-worse, HB = higher-is-better. "Cite?" = citation present in code for the cut
points. File refs are the adapter functions.

| # | Metric (id fragment) | Adapter | Inputs (n) | Current formula / cut points | Cite? | Verdict |
|---|---|---|---|---|---|---|
| H1 | Catchment Hydrology / land-cover `catchment-hydrology-impervious-surface-cover` | hydrology.py:46 | 2 indep | imp: <10/<=25 (CWP ICM); ag: <25/<=50; worst governs | imp YES, ag prov. | GOOD (keep) |
| H2 | Surface Water Storage / wetlands `surface-water-storage-percent-wetlands-in-watershed` | hydrology.py:84 | 1 | wetland %: >5/>=1 (HB) | STAF | GOOD |
| H3 | Streamflow Regime / Flow Alteration `streamflow-regime-flow-alteration-regulation-water-use` | hydrology.py:111 | 2→ratio | `damnrmstorws/DA`; band 5/100 ac-ft/km2 (HW) | NO | criteria mismatch — FIX text |
| H4 | Concentrated Runoff `reach-inflow-concentrated-runoff-stormwater-inputs` | hydrology.py:130 | 1 | `rddensws`; band 1/3 km/km2 (HW) | NO | criteria mismatch (outfalls) |
| Y2 | Floodplain Access `floodplain-connectivity-floodplain-access-entrenchment` | hydraulics.py:92 | 1 | entrenchment ratio; >=2.2/>=1.4 (HB) | YES Rosgen | GOOD (keep) |
| Y1 | Floodplain Engagement `high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence` | hydraulics.py:50 | 1→model | `t=interp(bhr^(5/3))` vs `_GROWTH`; <=2/<=5 yr | partial | FLAG fabricated transform |
| Y3 | Low-flow Connectivity `low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity` | hydraulics.py:118 | 1 cat | FCODE 46006/46003/46007 | NHD | criteria mismatch (% wetted) |
| Y4 | Hyporheic Exchange `hyporheic-connectivity-hyporheic-exchange-indicators` | hydraulics.py:133 | 2 | `0.6*min(slope/0.01,1)+0.4*clamp((sin-1)/0.5)`; 0.6/0.3 (HB) | NO | FLAG composite |
| G1 | Channel Evolution `channel-evolution-channel-evolution-stage-and-trends` | geomorphology.py:31 | 1(+FCODE gate) | BHR <1.3/<1.7 (HW); ditch FCODE→Poor | no cut cite | keep; fix criteria (CEM stages) |
| G2 | Sediment Supply `sediment-continuity-sediment-supply-potential-watershed-banks` | geomorphology.py:54 | 3 | `0.5*min(ag/50,1)+0.3*min(kf/0.4,1)+0.2*min(rd/5,1)`; 0.33/0.66 (HW) | NO | FLAG composite |
| G3 | Substrate Condition `bed-composition-and-large-wood-substrate-condition-grain-size-embeddedness-fines-consolidation` | geomorphology.py:71 | 3 | `0.4*(1-min(slope/0.01,1))+0.4*min(ag/50,1)+0.2*min(kf/0.4,1)`; 0.4/0.7 (HW) | NO | FLAG + "used directly" lie |
| G4 | Bank Erosion & Armoring `channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition` | geomorphology.py:88 | 3 | `0.4*min(kf/0.4,1)+0.4*(1-min(rip_forest/100,1))+0.2*min(slope/0.02,1)`; 0.4/0.7 (HW) | NO | FLAG + forest-only bias |
| P1 | Regulatory Impairment `water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl` | physicochemistry.py:14 (surrogate :56) | 0 (ATTAINS) / 4 (surrogate) | surrogate `stress=0.45*min(imp/25,1)+0.35*min(ag/60,1)+0.20*min(rd/5,1)`, `risk=clamp(stress-0.15*min(rip/60,1))`; 0.25/0.5 | ATTAINS YES / surrogate NO | primary GOOD; FLAG surrogate |
| P2 | Detrital CPOM `carbon-processing-detrital-processing-cpom-retention-shredders` | physicochemistry.py:88 | 1 (sum of ≤7 rp100 classes) | natural riparian veg %; >50/>=20 (HB) | NO | FINISH (criteria still leaf-packs) |
| P3 | Nutrients (N & P) `nutrient-cycling-nitrogen-and-phosphorus-concentrations` | physicochemistry.py:110 | 2 indep obs | TN 0.5/1.5, TP 0.05/0.10 mg/L; worst governs (HW) | generic | keep; expose thresholds |
| P4 | Stream Temperature `light-and-thermal-regime-stream-temperature` | physicochemistry.py:141 (surrogate :165) | 1 obs / 2 surrogate | obs 20/25 C; surrogate `idx=tair-2.0*min(rip/60,1)`; 12/17 | NO | primary OK; FLAG surrogate |
| B1 | Invasive Species `community-dynamics-invasive-non-native-species-presence` | biology.py:22 | 1 count | 0 / <=2 / else | NAS data | criteria mismatch (% presence) |
| B2 | Fish Barriers `watershed-connectivity-fish-passage-and-barrier-effects-longitudinal-connectivity` | biology.py:39 | 1 count | 0 / 1 / >=2 dams within ~1 mi | NID | keep |
| B3 | Habitat Complexity `habitat-provision-in-stream-habitat-complexity-and-cover` | biology.py:52 | 2 (+1 unused slope) | `0.6*min(rip_forest/60,1)+0.4*min(so/4,1)`; 0.55/0.30 (HB) | NO | FLAG + "used directly" lie |
| B4 | Biological Integrity (IBI) `population-support-biological-integrity-ibi-community-condition` | biology.py:68 | 4 | `support=min(rip/60,1)`, `stress=0.45*min(imp/25,1)+0.35*min(ag/60,1)+0.20*min(rd/5,1)`, `score=clamp(0.5+0.5*support-0.6*stress)`; 0.66/0.4 (HB) | NO | FLAG + "used directly" lie |

**The 11 to simplify:** Flow Alteration (fix only), Concentrated Runoff (fix only), Hyporheic, Sediment
Supply, Substrate, Bank Erosion, Impairment surrogate, Detrital (finish), Temperature surrogate, Habitat
Complexity, Biological Integrity, Floodplain Engagement. (Flow Alteration and Concentrated Runoff are
single-value already; they need criteria/transparency fixes, not a math change.)

**The defensible exemplars to emulate:** H1 (CWP impervious / worst-governs), H2 (wetlands), Y2 (Rosgen
entrenchment), P1 primary (ATTAINS).

---

## 3. Owner design principles (locked)

1. **Defensible + traceable.** Every rating must be explainable to a skeptical reviewer. Thresholds are
   either cited or explicitly labeled "screening judgment" (a transparent HSI-style / qualitative
   rationale is acceptable — invented precision is not).
2. **More than one value per metric is allowed** *only* when each value is independently defensible and the
   combination is traceable. Realized **exclusively via worst-governs** (Liebig's law of the minimum:
   the worse of two independently-thresholded indicators governs). **No weighted sums, no normalization
   caps, no invented coefficients.**
3. **Minimize indicator reuse across metrics, especially within a discipline.** Target implemented here:
   **zero same-discipline reuse**; any recycled indicator appears in **at most two** metrics and always
   **across different disciplines**; the recycle is justified by a shared physical control and disclosed.
4. **Scope:** EASI adapters + SFARI evidence adapters + public metric library + docs. StreamCurves picks
   it up via re-vendor.

---

## 4. Part 1 — Transparency architecture (single source of truth; build first)

**Principle:** the `good_below`/`fair_below` passed in the adapter call must produce *both* the rating and
the displayed thresholds, so they cannot drift.

### 4a. New `MetricResult.scoring` field
`apps/easi/easi/metrics/base.py` (dataclass ~110-121). Add `scoring: Optional[dict] = None` (before
`is_override`). **Do not reuse `detail`** — `detail` is already occupied by two shapes: land-cover
`{"governing","impervious","agriculture"}` (`hydrology.py:74-78`) and detrital `{"kind":"riparian_veg",...}`
(`physicochemistry.py:100-107`). A metric must emit its breakdown **and** its scoring record; one `detail`
dict cannot hold both without breaking the consumers at `assessment.py:133-135`.

```python
@dataclass
class MetricResult:
    metric_id: str
    value: Any = None
    value_text: str = ""
    rating: Optional[str] = None
    confidence: str = "L"
    source: str = ""
    status: str = "ok"
    note: str = ""
    detail: Optional[dict] = None
    scoring: Optional[dict] = None     # NEW: the thresholds that produced `rating`
    is_override: bool = False
```

### 4b. Helpers in `base.py` (add after `band()` ~98-107)

```python
def _band_ranges(good_below, fair_below, higher_is_worse):
    """Human range strings that mirror band()'s exact cut points."""
    g, f = f"{good_below:g}", f"{fair_below:g}"
    if higher_is_worse:                       # band(): <good->Good, <fair->Fair, else Poor
        return [("Good", f"< {g}"), ("Fair", f"{g}–{f}"), ("Poor", f"> {f}")]
    return [("Good", f"> {g}"), ("Fair", f"{f}–{g}"), ("Poor", f"< {f}")]

def scoring_record(label, hit, bands, *, value=None, value_text="", unit="",
                   direction="", basis="screening judgment"):
    """Assemble a scoring payload from an explicit ordered (rating, range-text) list.
    Use for worst-of-two, ratios, categoricals, counts, and hand-coded inclusive boundaries."""
    return {"kind": "scoring", "label": label, "value": value, "valueText": value_text,
            "unit": unit, "direction": direction, "basis": basis, "hit": hit,
            "bands": [{"rating": r, "range": t} for r, t in bands]}

def scored(value, good_below, fair_below, *, higher_is_worse=True, label,
           unit="", value_text=None, basis="screening judgment"):
    """Rate `value` AND emit the record that scored it — one call, one truth.
    Drop-in ONLY for adapters currently calling base.band() (identical strict </> semantics)."""
    rating = band(value, good_below, fair_below, higher_is_worse)
    bands = _band_ranges(good_below, fair_below, higher_is_worse)
    vt = value_text if value_text is not None else f"{value:g}{(' ' + unit) if unit else ''}"
    return rating, scoring_record(label, rating, bands, value=value, value_text=vt, unit=unit,
        direction=("higher-worse" if higher_is_worse else "higher-better"), basis=basis)
```

**CRITICAL correctness rule:** `base.band()` uses strict `<`/`>`. Adapters that hand-code **inclusive**
boundaries must NOT be routed through `scored()` or ratings shift at exact cut points (would break
`test_adapters.py` boundary params). Use `scoring_record()` with explicit ranges for:
- `impervious`/`agriculture` (`<= 25`, `<= 50`, `hydrology.py:57-58`)
- `wetlands` (`>= 1`, `hydrology.py:105`)
- all custom raters: entrenchment (`>= 2.2/1.4`), BHR bins, FCODE, counts, worst-of-two.

Hoist custom cut points to module constants (e.g. `ER_GOOD=2.2, ER_FAIR=1.4`; `BHR_GOOD, BHR_FAIR`) so
each number is defined once and drives both the rating and the displayed range.

### 4c. Tooltip "How it's scored" block — `app._metric_tip_html` (app.py:111-170)
Add a `scoring` parameter. Render precedence: keep Definition/Source; **replace** the generic
Calculation+Scoring blocks (`app.py:131-133, 159-169`) with a uniform block when `scoring` is present;
**leave the land-cover block (142-158) and riparian block (134-141) intact** (those are already correct
scoring views). Compact layout:

```
HOW IT'S SCORED
Road density  0.50 km/km²  → Good
● Good   < 1.0            ← achieved band, bold + underlined
  Fair   1.0–3.0
  Poor   > 3.0
Basis: StreamCat road density (stormwater proxy)
```

Reuse existing `.easi-tip-crit` / `.easi-tip-dot` rows (band-colored swatches, `styles.css:457-464`) and
`.easi-tip-sub` for the basis line. For worst-of-two metrics, iterate two indicators each with its own
value+bands and bold the governing one (same grammar as the land-cover block). `_bieger_area_tip_html`
(`app.py:173-190`) already proves the tooltip can render a real equation.

In `_metric_table` (`app.py:449-461`): pass `scoring=r.get("scoring")` and change
`calc=config.METRIC_CALCULATIONS.get(mid) or "Dataset value used directly (binned to a rating)."` to
`calc=config.METRIC_CALCULATIONS.get(mid)` — **delete the false fallback.**

CSS (append near `styles.css:457`), then bump the cache-bust query string (guardrail #2, prior task did v25):
```css
.easi-tip-crit-head { font-size: 11px; color: #22304d; margin: 1px 0 2px; }
.easi-tip-val { font-weight: 600; }
.easi-tip-crit.is-hit { font-weight: 700; }
.easi-tip-crit.is-hit b { text-decoration: underline; }
```

### 4d. `assessment.py` wiring (assessment.py:133-150)
Carry the payload like `landCover`/`ripVeg`. **Name the local `scoring_rec`** — `scoring` is an imported
module in that file (`assessment.py:15`).
```python
scoring_rec = res.scoring if res else None
...
rows.append({ ... "landCover": land_cover, "ripVeg": rip_veg, "scoring": scoring_rec, ... })
```
Override path: `assess()` override branch touches rating/status/source/value_text only — leave `scoring`
(shows what the automation computed, which is the transparency a reviewer wants when overriding).
`rescore()` (`assessment.py:341-372`) does `row = dict(base)` so it copies `scoring` through — **no change
needed.** Optional: cross-section edits (`rate_metrics_from_stages`, `assessment.py:271-311`) could emit
fresh scoring records via the hoisted ER/BHR constants; not required.

### 4e. Criteria-string decision — DEMOTE, do not rewrite as source of truth
- The emitted `scoring` block **replaces** the JSON `criteria` as the report's scoring display.
- Keep `easi-metrics.json` `criteria{}` only as optional muted "STAF field method" context (or drop from
  the report entirely). **Do not** author scoring truth in `easi-metrics.json`: it is generated from
  `data/source/screening-metrics.tsv` by `scripts/build_easi_metrics.py`, and the hand-added
  `criteriaAgriculture` field is NOT in the TSV/script — a rebuild silently wipes it. Same footgun applies
  to any new criteria text.
- **Configure page** (`_cfg_row`, `app.py:1760-1789`) and `config.criteria_bands()` render thresholds
  *before* a run (no `scoring` yet). Phase 1: leave the imp/ag/wetland JSON strings feeding Configure
  (they match code) and add a **drift-lock test** asserting `config.criteria_bands(mid,"impervious"/
  "agriculture")` equals the adapter constants. Phase 2 (optional): hoist imp/ag cut points to constants
  and render Configure from them, making JSON `criteria` pure field context.

---

## 5. Part 2 — Per-metric simplification (distinct indicators)

Reworked so **each discipline's four metrics use four different indicators.** Recommendation first; where
distinctness trades against defensibility, the alternative is noted for revision.

### Discipline: GEOMORPHOLOGY → {K-factor, slope, riparian veg, BHR}

**G2 Sediment Supply Potential** — `sediment-continuity-...` (geomorphology.py:54)
- Current: `0.5*min(ag/50,1)+0.3*min(kf/0.4,1)+0.2*min(rd/5,1)`; 0.33/0.66 HW.
- **Recommended:** single = **soil erodibility K-factor** (`kffactws`). Inherent watershed erodibility is a
  direct, distinct sediment-supply-potential proxy (USLE/RUSLE K). HW.
- Thresholds (VERIFY against StreamCat `kffact` units/range; USLE K ~0.02–0.69): Good <0.20 / Fair
  0.20–0.35 / Poor >0.35. Basis: erodibility→supply direction is standard; cut points screening judgment.
- Dropped: ag (would recycle into a 3rd metric), roads (already Concentrated Runoff). Anthropogenic
  disturbance is captured by the land-cover metrics; bank supply by Bank Erosion / Channel Evolution —
  **state the scope split in the note.**
- **Alternative (see Decisions):** agricultural cover % (more directly "anthropogenic supply", Waters
  1995 / Allan 2004) — but recycles ag.
- Code sketch:
  ```python
  def sediment_supply(ctx):
      kf = base.sc(ctx).get("kffactws")
      if kf is None: return unavailable(SEDIMENT_ID, "no StreamCat K-factor", "M")
      rating, sc_rec = base.scored(kf, 0.20, 0.35, higher_is_worse=True,
          label="Soil erodibility (K-factor)", value_text=f"K-factor {kf:.2f}",
          basis="watershed soil erodibility as sediment-supply potential (USLE/RUSLE K); screening cut points")
      return MetricResult(SEDIMENT_ID, value=round(kf,2), value_text=f"soil erodibility K-factor {kf:.2f}",
          rating=rating, confidence="M", source="EPA StreamCat soil erodibility (kffact, watershed)",
          note="inherent watershed erodibility = sediment-supply potential; land disturbance is scored by "
               "the land-cover metrics, bank supply by bank-erosion/channel-evolution.", scoring=sc_rec)
  ```
- New criteria: Good "Low soil erodibility (K < 0.20) — low inherent sediment-supply potential." / Fair
  "Moderate erodibility (0.20–0.35)." / Poor "High erodibility (K > 0.35) — high supply potential."

**G3 Substrate Condition** — `bed-composition-...` (geomorphology.py:71)
- Current: `0.4*(1-min(slope/0.01,1))+0.4*min(ag/50,1)+0.2*min(kf/0.4,1)`; 0.4/0.7 HW.
- **Recommended:** single = **NHDPlus channel slope** (`ctx.slope`). Gradient is the first-order control on
  bed grain size / transport competence. HB (steeper→coarser, open framework).
- Thresholds: Good ≥0.01 / Fair 0.002–0.01 / Poor <0.002 m/m. Basis: Montgomery & Buffington 1997 (GSA
  Bull 109:596-611) process domains (sand <~0.001; pool-riffle ~0.001–0.02; step-pool ~0.03–0.065); the
  Good/Fair/Poor mapping is screening judgment.
- **Honest caveat (put in note + basis):** slope predicts *natural* bed texture, NOT anthropogenic
  embeddedness — a naturally sand-bedded lowland reach reads Poor. This inversion already exists in the
  current formula; slope-only makes it transparent and cited. True embeddedness needs field pebble counts.
  Makes the "used directly" tooltip honest.
- Code: `rating, sc = base.scored(slope, 0.01, 0.002, higher_is_worse=False, label="Channel slope",
  unit="m/m", basis="gradient->grain size (Montgomery & Buffington 1997 process domains); predicts natural
  coarseness, not embeddedness")`. Add a `METRIC_CALCULATIONS` entry (or let it correctly fall through).
- New criteria: Good "Steeper gradient (≥0.01 m/m) — coarse, well-flushed bed." / Fair "0.002–0.01 —
  gravel with some fines." / Poor "<0.002 — naturally fine/sand, depositional."

**G4 Bank Erosion & Armoring** — `channel-and-floodplain-dynamics-...` (geomorphology.py:88)
- Current: `0.4*min(kf/0.4,1)+0.4*(1-min(rip_forest/100,1))+0.2*min(slope/0.02,1)`; 0.4/0.7 HW.
- **Recommended:** single = **natural riparian vegetation %** (`base.riparian_natural_veg_pct`). Root
  reinforcement is the single most-cited bank-stability control; the **broad** natural-veg helper **fixes
  the forest-only arid-region bias** (shrub/grass banks are stable). HB.
- Thresholds: Good ≥50 / Fair 20–50 / Poor <20 %. Basis: Simon & Collison 2002 (ESPL 27:527-546; Pollen &
  Simon 2005 RipRoot) for direction; % cut points screening judgment (shared with Detrital).
- Emit `detail={"kind":"riparian_veg", **base.riparian_veg_breakdown(ctx)}` too (composition block).
- **Worst-of-two option (see Decisions):** worst-of(veg%, BHR) is the one scientifically clean 2-input case
  (root cohesion + incision are independent bank-failure drivers). Declined by default because BHR is
  already used by Channel Evolution (same discipline) — accepting it means BHR appears in 3 geomorph
  metrics.
- New criteria: Good "Predominantly natural, well-rooted buffer (≥50%)." / Fair "Partial (20–50%)." /
  Poor "Sparse natural cover (<20%) — bare/armored, unstable banks."

**G1 Channel Evolution** — `channel-evolution-...` (geomorphology.py:31) — **keep** single BHR (<1.3/<1.7
HW) + the canal/ditch FCODE→Poor gate. Only fix the criteria (today it references CEM Stage I–VI, which the
code does not classify — it bins one BHR). Basis: incision index (Rosgen; Schumm/Simon CEM). Route BHR
through `scoring_record` with hoisted `BHR_GOOD=1.3, BHR_FAIR=1.7`.

### Discipline: HYDRAULICS → {entrenchment ratio, BHR, FCODE, slope}

**Y1 Floodplain Engagement** — `high-flow-dynamics-...` (hydraulics.py:50)
- Current: `t_years = interp(bhr^(5/3))` against hardcoded `_GROWTH=[(1.5,1.0),(2.0,1.2),(5.0,1.9),
  (10.0,2.6),(25.0,3.6),(50.0,4.4)]`; Good ≤2 / Fair ≤5 yr. The BHR^(5/3)→recurrence conversion is a
  fabricated transform (Manning exponent + un-attributed growth curve).
- **Recommended:** stop deriving recurrence-years; **bin the measured BHR directly** (the real quantity;
  recurrence is fiction). BHR≈1 = floodplain-connected; >1.5 = incised/rare engagement.
- Thresholds: Good ≤1.2 / Fair 1.2–1.5 / Poor >1.5 (HW). Basis: BHR incision (Rosgen; Schumm/Simon).
  Cut points judgment. **Delete `_GROWTH` and `_recurrence_from_ratio`.** Keep the "no cross-section →
  Fair default" branch. The editable-cross-section recompute calls `rate_engagement`, so it stays
  consistent.
- **Alternative (Decisions):** align to Channel Evolution's 1.3/1.7 for internal consistency (both are BHR).
- Note: shares BHR with Channel Evolution (cross-discipline) — physically legitimate (incision governs both
  stage and overbank frequency); disclose.
- New criteria: Good "BHR ≈1 (≤1.2) — floodplain engaged near bankfull." / Fair "1.2–1.5 — moderately
  incised." / Poor ">1.5 — incised; floodplain rarely inundated." Update `METRIC_DEFINITIONS`/
  `METRIC_CALCULATIONS` (they currently promise a "recurrence interval") + the app.py engagement wording.

**Y4 Hyporheic Exchange** — `hyporheic-connectivity-...` (hydraulics.py:133)
- Current: `0.6*min(slope/0.01,1)+0.4*clamp((sin-1)/0.5)`; 0.6/0.3 HB.
- **Recommended:** single = **channel slope** (`ctx.slope`), the dominant first-order (vertical
  head-gradient) control. **Drop sinuosity** (frees it for Habitat Complexity). HB.
- Thresholds: Good ≥0.01 / Fair 0.002–0.01 / Poor <0.002 m/m — explicitly HSI-style screening judgment.
  Basis: Boano et al. 2014 (Rev. Geophysics 52:603-679) for direction.
- **Weakest case — honest caveats:** true first-order control is streambed Ksat + head gradient; Ksat is
  NOT fetched (registry names `sda_ksat` as planned-unbuilt). Slope also overlaps Substrate (both key on
  gradient). Worst-of-two is scientifically INAPPROPRIATE here (slope and sinuosity are additive/
  compensatory exchange pathways, not co-limiting stressors — a sinuous low-gradient reach can still have
  strong lateral exchange). **Alternative (Decisions):** mark Hyporheic low-confidence/qualitative until
  SSURGO Ksat is wired, to break the slope pair with Substrate.
- New criteria: Good "Steeper gradient (≥0.01 m/m) — strong head gradient." / Fair "0.002–0.01." / Poor
  "<0.002 — weak gradient, limited exchange." Update `METRIC_DEFINITIONS`/`METRIC_CALCULATIONS`
  (currently "slope and sinuosity").

**Y2 Floodplain Access** (keep, Rosgen ER; hoist `ER_GOOD/ER_FAIR`) and **Y3 Low-flow Connectivity**
(keep, FCODE categorical; emit a categorical `scoring_record`; fix criteria which says "% wetted").

### Discipline: PHYSICOCHEMISTRY → {ATTAINS, riparian veg, TN+TP, observed temp}

**P1 Regulatory Impairment** — `water-and-soil-quality-...` (physicochemistry.py:14, surrogate :56)
- Keep the **ATTAINS primary path** (cited, categorical) untouched.
- **Recommended for the surrogate:** when ATTAINS is silent, return **"not assessed"** (value None,
  rating None/Fair-default flagged, confidence L, overrideable) rather than the 4-var stress index. This is
  the honest answer for a regulatory-status metric and avoids reusing imp/ag a third time.
- **Coverage trade-off (Decisions):** most small reaches are unassessed → the metric is often blank.
- **Alternative:** replace the surrogate with `base.land_cover_pressure` (more-limiting imp/ag; NPS
  impairment is land-use driven) — but that makes imp/ag a 3rd recycled use. If chosen, emit `detail=lc`,
  `value_text="... NOT ATTAINS-assessed"`, add a `criteriaAgriculture` block, and a "surrogate — landscape
  model" badge when `source` starts with "Modeled".

**P4 Stream Temperature** — `light-and-thermal-regime-...` (physicochemistry.py:141, surrogate :165)
- Keep the **observed WQP primary path** (median binned 20/25 °C).
- **Recommended for the surrogate:** single = **mean annual air temperature** (`tmean8110ws`) as a distinct
  thermal-regime proxy (stream temp tracks air temp — Mohseni & Stefan 1999). Distinct — avoids reusing
  riparian in-discipline (Detrital already uses riparian).
- Thresholds: air-temp class (define, e.g. Good <12 / Fair 12–17 / Poor >17 °C — same as current surrogate
  index bounds but on air temp directly, screening judgment). **Caveat:** air temp is regional climate, not
  thermal *departure* — naturally warm climates read Poor; observed WQP is primary precisely because the
  surrogate is coarse. Disclose.
- **Alternatives (Decisions):** riparian-forest shade (defensible mechanism — Poole & Berman 2001 — but
  recycles riparian in-discipline) or "not assessed".
- Fix criteria to be quantitative (Good "<20 °C observed" etc.) + add `METRIC_CALCULATIONS`
  ("Observed WQP median; where absent, mean-air-temperature surrogate").

**P2 Detrital CPOM** — `carbon-processing-...` (physicochemistry.py:88) — **FINISH ONLY.** Already single =
natural riparian veg % (`riparian_veg_breakdown["total"]`), >50/≥20 HB. Rewrite the criteria (currently
field leaf-pack language) to riparian-veg %, add "screening judgment" basis. `METRIC_CALCULATIONS` already
correct (config.py:276). Emit `scoring` + keep the existing `detail` composition block.
- New criteria: Good ">50% natural riparian vegetation (forest/shrub/grass/wetland) in the 100 m buffer." /
  Fair "20–50%." / Poor "<20% (buffer largely developed, agricultural, or bare)."

**P3 Nutrients** — `nutrient-cycling-...` (physicochemistry.py:110) — keep TN/TP worst-governs (observed);
emit a two-indicator `scoring_record` (TN Good<0.5/Fair<1.5; TP Good<0.05/Fair<0.10 mg/L). This is a hidden
win: nutrients has no `detail` today so it currently shows the false "used directly."

### Discipline: BIOLOGY → {invasive count, barrier count, sinuosity, land-cover pressure}

**B3 Habitat Complexity** — `habitat-provision-...` (biology.py:52)
- Current: `0.6*min(rip_forest/60,1)+0.4*min(so/4,1)`; 0.55/0.30 HB; also fetches slope but never uses it.
- **Recommended:** single = **channel sinuosity** (`ctx.sinuosity`). Planform complexity → habitat-unit
  diversity (pools, point bars, cut banks). Distinct (freed by dropping sinuosity from Hyporheic). **Drop
  stream order and the unused slope.** HB.
- Thresholds: Good ≥1.3 / Fair 1.1–1.3 / Poor <1.1 (screening judgment). Basis: sinuosity→habitat
  heterogeneity (moderate support).
- **Alternative (Decisions):** riparian forest % (stronger LWD/cover basis — EPA RBP) but recycles riparian
  into a 3rd metric. If chosen, use forest-only (distinct from Detrital's all-natural-veg) and add a
  `METRIC_CALCULATIONS` entry to kill the "used directly" line.
- New criteria (sinuosity): Good "Sinuous channel (≥1.3) — diverse in-stream habitat units." / Fair
  "1.1–1.3." / Poor "<1.1 — straightened/uniform, low habitat diversity."

**B4 Biological Integrity (IBI)** — `population-support-...` (biology.py:68)
- Current: 4-var `score=clamp(0.5+0.5*support-0.6*stress)`; 0.66/0.4 HB.
- **Recommended:** **worst-of(impervious, agricultural)** via `base.land_cover_pressure`. Land use is the
  best-cited single IBI predictor (Allan 2004 Annu Rev Ecol Evol Syst 35:257-284; CWP; Booth & Jackson
  1997). Recycles land-cover once (with Catchment Hydrology, cross-discipline).
- Thresholds: imp 10/25 (CWP), ag 25/50; worst governs. Disclose "NOT a measured IBI", confidence L,
  overrideable. Emit `detail=lc` (drives both-indicator display) + `scoring`. Add `METRIC_CALCULATIONS`
  ("More-limiting of watershed impervious and agricultural cover.") to kill the "used directly" line.
- Rewrite the JSON `criteria` (currently field IBI observations) to impervious bands + add a
  `criteriaAgriculture` block.

**B1 Invasive Species** (keep count 0/≤2; emit categorical/count `scoring_record`; fix criteria which says
"% presence") and **B2 Fish Barriers** (keep count 0/1/≥2; emit record).

### Discipline: HYDROLOGY — unchanged indicators (4 distinct already)
- **H1 Catchment Hydrology:** keep worst-of(imp, ag) via the extracted `base.land_cover_pressure`.
- **H2 Wetlands:** keep (>5/≥1); emit inclusive-boundary `scoring_record`.
- **H3 Flow Alteration:** keep the single `damnrmstorws/DA` ratio (one derived value, defensible). **Fix
  criteria only** (today "% from baseline"; it computes ac-ft/km2 ≈ mm of impoundable depth; 1 ac-ft/km2 ≈
  1.23 mm, so 5/100 ≈ ~6/~123 mm). Update `METRIC_CALCULATIONS` (config.py:261) to "Upstream dam normal
  storage ÷ drainage area." VERIFY `damnrmstor` units.
- **H4 Concentrated Runoff:** keep road-density (1/3); **fix criteria** (says outfalls/100 m; computes
  `rddensws`).

### Shared helper
Extract `base.land_cover_pressure(ctx)` from the inline logic in `hydrology.impervious` (hydrology.py:55-78;
`_RANK`, `_impervious_pct`, `_agriculture_pct`), returning
`{"governing","rating","impervious":{pct,rating}|None,"agriculture":{pct,rating}|None}`. Refactor
`impervious` onto it (behavior-preserving). Used by **exactly two** metrics: Catchment Hydrology + Biological
Integrity.

```python
# base.py
_LC_RANK = {"Poor": 0, "Fair": 1, "Good": 2, None: 3}
def land_cover_pressure(ctx):
    imp = sc(ctx).get("pctimp2019ws") or (ctx.extras.get("landcover") or {}).get("impervious_pct")
    ag = ag_pct(ctx) or (ctx.extras.get("landcover") or {}).get("ag_pct")
    imp_r = ("Good" if imp < 10 else "Fair" if imp <= 25 else "Poor") if imp is not None else None
    ag_r  = ("Good" if ag < 25 else "Fair" if ag <= 50 else "Poor") if ag is not None else None
    if imp_r is None and ag_r is None: return None
    if imp_r is None: governing = "agriculture"
    elif ag_r is None or _LC_RANK[ag_r] >= _LC_RANK[imp_r]: governing = "impervious"
    else: governing = "agriculture"
    rating = ag_r if governing == "agriculture" else imp_r
    return {"governing": governing, "rating": rating,
            "impervious": None if imp is None else {"pct": round(float(imp),1), "rating": imp_r},
            "agriculture": None if ag is None else {"pct": round(float(ag),1), "rating": ag_r}}
```
(Careful with `or` on a legit 0.0 pct — use explicit `is None` checks in the real code.)

### Reuse map (final)
Zero same-discipline reuse. Recycled strong values, each in exactly two metrics, cross-discipline, sharing a
real physical control (disclosed in the report):

| Value | Metric A (discipline) | Metric B (discipline) | Shared control |
|---|---|---|---|
| impervious/ag | Catchment Hydrology (Hydrology) | Biological Integrity (Biology) | land-use intensity |
| bank-height ratio | Channel Evolution (Geomorph) | Floodplain Engagement (Hydraulics) | vertical incision |
| channel slope | Substrate (Geomorph) | Hyporheic (Hydraulics) | gradient |
| natural riparian veg | Bank Erosion (Geomorph) | Detrital CPOM (Physicochem) | riparian buffer condition |

Every other metric uses a unique indicator.

---

## 6. Part 3 — Clean-metric transparency + criteria fixes
Route all remaining metrics through the scoring payload and correct criteria text that names a different
quantity than the code computes: Concentrated Runoff (outfalls→road density), Invasive Species (%→count),
Low-flow (%wetted→FCODE), Nutrients (add TN/TP record). Wetlands, Entrenchment, Channel Evolution, Barriers
just get the uniform block. **Net result: no metric shows "Dataset value used directly" and every displayed
threshold is the one that scored the row.**

---

## 7. Part 4 — Propagation

- **SFARI** (`apps/sfari/sfari/likert.py` `BREAKS`, `apps/sfari/sfari/evidence.py`): SFARI has its own
  single-source `BREAKS` (metricId → dir + break list) on a 5-band Likert with deliberately different
  numbers (imp 5/10/20 vs 10/25; ag 15/25/50 vs 25/50; riparian 20/40/60). A literal share isn't possible
  across scales; where they encode the same physical breakpoint, keep them from drifting via a shared
  `staf_thresholds.py` (imported by both) or a cross-check test. Mirror the EASI *indicator choices* (e.g.
  if EASI drops sinuosity-for-hyporheic or uses K-for-sediment, reflect the same story in SFARI evidence).
  `ev_impervious` more-limiting logic (`evidence.py:109-118`) mirrors EASI `_RANK`. Lower stakes
  (advisory, show-don't-autofill).
- **Public metric library + docs:** per-metric JSON under `docs/assets/data/metric-library/metrics/*.json`
  is generated from `docs/assets/data/metric-library/Metric Library Complete 2026-02-10.csv` via
  `npm run build:metric-library` (`scripts/compileMetricLibraryFromCsv.ts`, `deriveScreeningCriteria`).
  It carries criteria/threshold text only (no formula field). To surface the simplified computed
  thresholds + a one-line method note **without a rebuild clobbering them**: add a CSV column (e.g.
  `Screening Computed Thresholds` / `Screening Method Note`), thread an optional field through
  `src/lib/metricLibrary/schemas.ts` + the compiler (there is an existing `formula` scoring type at
  ~schemas.ts:110 to consider), rebuild, and render on `docs/scoring/index.md` (+ optionally
  `docs/functions/index.md`). `rating-scales.json` = band labels/scores only, unchanged. Never hand-edit
  generated JSON (guardrail #1).
- **StreamCurves:** the vendored EASI copy (`apps/stream-curves/streamcurves/_vendor/easi`) is regenerated
  by `apps/stream-curves/scripts/vendor_easi_engine.py`. After the EASI changes, re-run it and commit
  `_vendor/easi/**`; the new `scoring` key rides through additively. No hand edits. NB: its drift-gate test
  `tests/test_easi_screening.py::test_vendor_in_sync_with_source` is already RED from prior uncommitted
  EASI work — this re-vendor clears it. See memory `streamcurves-vendored-easi-drift`.

---

## 8. Phasing (verify after each phase)
- **A. Backbone (no rating changes):** `MetricResult.scoring` + `base.scored`/`scoring_record`/`_band_ranges`
  + tooltip block + route the ~9 clean metrics + kill "used directly" + `assessment.py` wiring + drift-lock
  test. Rating-preserving → existing `test_adapters.py` passes unchanged (the regression proof).
- **B. Simplify the 11 composites** + `base.land_cover_pressure` + rewrite criteria/basis. Regenerate the
  parity golden (`EASI_WRITE_GOLDEN=1`) and review the diff (many values change — expected).
- **C. SFARI** alignment + tests.
- **D. Docs/library** rebuild + `docs/scoring` note; then **re-vendor** StreamCurves.

---

## 9. Verification
1. Per-app pytest from each app dir (repo rule — colliding module names): EASI, SFARI, DEEP. Phase A is
   rating-preserving; Phase B updates `test_adapters.py` composite cases. Rewrite `tests/test_report_tooltip.py`
   for the new block (currently asserts `Scoring` label + `default: Good` + band dots at :29-41, and
   criteria-escaping at :43-46).
2. **Anti-drift assertions** (`tests/test_adapters.py`): `result.scoring["hit"] == result.rating` across a
   value in each band (cannot disagree — one source); displayed range text contains the real
   `good_below`/`fair_below`; round-trip (parse shown ranges, re-bin the value, reproduce the rating);
   `"Dataset value used directly"` appears nowhere; **coverage gate** — every registered adapter returns a
   non-null `scoring` for a rated result (extend `config.validate_registry()` at config.py:286-301 or add a
   test).
3. Regenerate + review `tests/data/parity_golden.json` (`test_batch_parity.py`); `npm run
   build:metric-library` + `npm test` + `cd docs && bundle exec jekyll build` green.
4. Offline render checks: build a metric row through `app._metric_table`; assert the "How it's scored"
   block shows the indicator, thresholds, achieved band, and basis (unescape HTML before asserting).
5. Live EASI walkthrough (restart the running instance): delineate a site, open the report, confirm each
   tooltip shows its indicator(s) + thresholds + basis, no field-method mismatch, no "used directly".
6. Re-vendor StreamCurves; commit `_vendor/easi/**` (+ `apps/library`/DEEP if that re-bake is bundled).

---

## 10. Open Decisions (resolve on revision)
1. **Impairment surrogate when ATTAINS silent:** "not assessed" (recommended, honest; coverage drops) vs
   land-cover surrogate (makes imp/ag a 3rd recycle).
2. **Habitat Complexity:** sinuosity (recommended, distinct, moderate proxy) vs riparian forest % (stronger
   LWD/cover basis, recycles riparian a 3rd time).
3. **Stream Temperature surrogate:** mean air temperature (recommended, distinct; regional-climate caveat)
   vs riparian-forest shade (in-discipline riparian recycle) vs "not assessed".
4. **Sediment Supply:** soil K-factor (recommended, distinct inherent erodibility) vs agricultural cover %
   (more directly "anthropogenic supply", recycles ag).
5. **Bank Erosion:** single natural-veg % (recommended) vs worst-of(veg%, BHR) (clean 2-input, but BHR then
   appears in 3 geomorph metrics).
6. **Floodplain Engagement thresholds:** BHR 1.2/1.5 (recommended) vs align to Channel Evolution 1.3/1.7.
7. **Slope pair (Substrate + Hyporheic):** keep both disclosed (recommended) vs mark Hyporheic
   low-confidence/qualitative until SSURGO Ksat is wired.
8. **Threshold honesty:** confirm labeling most new cut points "screening judgment" (not citations) is
   acceptable — this is the transparent, defensible framing the review asked for.
9. **All new numeric thresholds** (K-factor 0.20/0.35, slope 0.01/0.002, sinuosity 1.3/1.1, air temp 12/17,
   flow-alteration 5/100 ac-ft/km2) need a calibration/units pass and SME sign-off before execution.

---

## 11. Critical files
- **EASI adapters:** `easi/metrics/base.py` (new `scoring` field + `scored`/`scoring_record`/`_band_ranges`
  + `land_cover_pressure`), `easi/metrics/{hydrology,hydraulics,geomorphology,physicochemistry,biology}.py`.
- **EASI wiring/render:** `easi/assessment.py` (row `scoring_rec`), `app.py` (`_metric_tip_html`,
  `_metric_table`, `_cfg_row`), `www/styles.css` (+cache-bust), `easi/config.py`
  (`METRIC_CALCULATIONS`/`METRIC_DEFINITIONS`/`METRIC_REGISTRY` datasource strings).
- **EASI data:** `data/easi-metrics.json` (criteria demotion / `criteriaAgriculture` for new land-cover
  surrogates) — remember the `build_easi_metrics.py`/TSV rebuild footgun.
- **EASI tests:** `tests/test_adapters.py`, `tests/test_report_tooltip.py`, `tests/data/parity_golden.json`
  (+ `test_batch_parity.py`).
- **SFARI:** `sfari/likert.py`, `sfari/evidence.py`, `tests/`.
- **Docs/library:** `docs/assets/data/metric-library/Metric Library Complete 2026-02-10.csv`,
  `src/lib/metricLibrary/schemas.ts` + `scripts/compileMetricLibraryFromCsv.ts`, `docs/scoring/index.md`.
- **StreamCurves:** re-run `apps/stream-curves/scripts/vendor_easi_engine.py`.

---

## 12. Appendix — key file:line anchors (as of 2026-07-16)
- `MetricResult`: `apps/easi/easi/metrics/base.py:110-121`; `band()`: `:98-107`; riparian/ag helpers:
  `riparian_forest_pct` `:42-48`, `riparian_natural_veg_pct`/`riparian_veg_breakdown` `:62-87`, `ag_pct`
  `:90-95`.
- Adapters: hydrology.py (impervious :46, wetlands :84, flow_alteration :111, reach_inflow :130);
  hydraulics.py (floodplain_engagement :50, floodplain_access :92, low_flow :118, hyporheic :133);
  geomorphology.py (channel_evolution :31, sediment_supply :54, substrate :71, bank_erosion :88);
  physicochemistry.py (impairment :14/:56, detrital_cpom :88, nutrients :110, stream_temperature :141/:165);
  biology.py (invasives :22, barriers :39, habitat_complexity :52, biological_integrity :68).
- Render: `_metric_tip_html` `app.py:111-170` (Calculation :131-133, riparian :134-141, land-cover :142-158,
  Scoring :159-169); `_metric_table` `app.py:411-517` (crit/calc lookup :449-461); `_cfg_row` :1760-1789;
  `_bieger_area_tip_html` :173-190 (equation-rendering precedent).
- Assembly: `assessment.assess()` `assessment.py:103-150` (detail classify :130-136, row :137-150);
  `rescore()` :341-372.
- Config: `criteria_bands` config.py:82-92; `METRIC_DEFINITIONS` :188-251; `METRIC_CALCULATIONS` :257-283;
  `METRIC_REGISTRY` :99-160; `validate_registry` :286-301.
- Data build: `easi-metrics.json` from `data/source/screening-metrics.tsv` via
  `scripts/build_easi_metrics.py` (criteriaAgriculture is a hand patch, not in TSV).
