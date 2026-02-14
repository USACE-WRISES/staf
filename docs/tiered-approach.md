---
title: Stream Tiered Assessment Framework
nav_order: 2
description: "A practical, functions-based, three-tier framework for stream assessment that scales level of effort while preserving comparability."
---
{% include staf_page_chrome.html %}

**Working draft (for website use).**  
This guide explains the Stream Tiered Assessment Framework: what it is, why it's useful, what functions it uses, what the tiers are, how scoring works, and how to perform assessments in practice.

<div class="guide-layout">
  <nav class="guide-nav" markdown="1">
  ## Contents
  {: .no_toc }

  - TOC
  {:toc}
  </nav>
  <div class="guide-content" markdown="1">

---

## Purpose and intended users

This resource is designed for practitioners who need to assess stream condition to support:

- regulatory decision-making (e.g., impact assessment, mitigation, compliance, program reporting),
- restoration planning and design (e.g., site screening, alternatives comparison, final design),
- post-construction monitoring and adaptive management,
- watershed-scale prioritization and investment decisions.

The approach is meant to be **nationally applicable in concept**, while allowing **regional tailoring in metrics and scoring standards**.

---

## Why a tiered approach is needed

Stream assessment practice faces recurring challenges:

1. **Too many methods, scattered across sources.** Hundreds of assessment methods exist (many in ï¿½grey literatureï¿½), so locating and comparing approaches is inefficient.
2. **Inconsistent metric choices and terminology.** Different tools measure different things, often without a shared structure that makes gaps and overlaps visible.
3. **Wide variation in resources required.** Some assessments take minutes; others require weeks of field sampling and analysis. Level of effort is often not clearly tracked or used in selection.
4. **Limited comparability of results across methods and regions.** Different function coverage, different scoring structures, and different output formats make it hard to compare ï¿½like with like.ï¿½

The Tiered Approach addresses these issues by:

- using a **shared functions framework** (common vocabulary + comprehensive coverage),
- defining **explicit levels of effort** (Screening, Rapid, Detailed),
- mapping functions to **Clean Water Act outcome framing** (physical, chemical, biological integrity),
- providing **repeatable scoring and aggregation** so results remain comparable across tiers and over time.

---

## Big idea in one sentence

**Assess the same set of stream functions across all tiers, but scale the metrics and methods (and therefore cost/effort and uncertainty) to match the decision context.**

---

## Core concepts and definitions

These terms are used throughout this guide:

- **Structure:** The relatively static physical template observed at a moment in time (e.g., channel geometry, substrate, riparian cover).
- **Process:** Fundamental actions (physical/chemical/biological) that drive change (e.g., runoff generation, erosion, denitrification).
- **Function:** A discrete ecosystem result that emerges when processes interact at ecologically relevant scales (e.g., flood attenuation, nutrient processing).
- **Metric / indicator / variable:** A measurable proxy (field, desktop, modeled) representing a function (e.g., percent impervious cover as a proxy for catchment hydrology stress).
- **Assessment method / tool:** The procedure used to quantify metrics (from checklists to lab sampling to numerical models).
- **Level of effort (LoE):** The time, cost, expertise, and logistics required to apply an assessment.
- **Tier:** A standardized LoE class (Screening, Rapid, Detailed) used to match effort to decisions.
- **Outcome framing:** A roll-up structure aligned with the Clean Water Act: **physical**, **chemical**, and **biological** integrity.
- **Condition score:** A composite score summarizing outcomes for comparison across sites or over time.

---

## The stream functions framework

### Why functions (instead of only metrics)

Metrics differ by region, program, and available data. Functions provide the stable ï¿½spineï¿½ of assessment because they:

- keep assessment focused on ecological processes that matter (not only what is easy to measure),
- reveal gaps and overlaps among tools,
- create a shared vocabulary for communication,
- support comparability across projects and tiers.

### Functional categories used in this approach

This framework groups functions into five categories aligned with a widely used disciplinary structure:

1. **Hydrology** (watershed-scale water generation, storage, and delivery)
2. **Hydraulics** (reach-scale movement of water in channel/floodplain/subsurface pathways)
3. **Geomorphology** (sediment/wood transport and channel form dynamics)
4. **Physicochemistry** (light/heat/carbon/nutrients/contaminants that shape chemical template)
5. **Biology** (habitat, populations, communities, and connectivity)

### The 20 core stream functions

These 20 functions are intended as a **comprehensive starting point**. Regional tools may consolidate or tailor the list, but changes should be documented and traceable back to these functions to preserve comparability.

#### Hydrology (4)
1. **Catchment hydrology** ï¿½ Land uses alter water quantity and quality delivered to the stream.
2. **Surface water storage** ï¿½ Natural/artificial storage attenuates flows and increases residence time.
3. **Reach inflow** ï¿½ Localized inputs (tributaries, ditches, pipes) alter magnitude/timing/quality.
4. **Streamflow regime** ï¿½ The characteristic flow pattern that shapes habitat, transport, and water quality.

#### Hydraulics (4)
5. **Low flow and baseflow dynamics** ï¿½ Maintains wetted habitat and modulates temperature/chemistry during low water.
6. **High flow dynamics** ï¿½ Drives erosion and channel maintenance during storms and peaks.
7. **Floodplain connectivity** ï¿½ Lateral exchange supports nutrient cycling, habitat, and hydraulic relief.
8. **Hyporheic connectivity** ï¿½ Surfaceï¿½subsurface exchange supports temperature regulation, nutrients, and habitat.

#### Geomorphology (4)
9. **Channel evolution** ï¿½ Changes in channel dimension/slope reflecting legacy and ongoing adjustment.
10. **Channel and floodplain dynamics** ï¿½ Bank processes, planform dynamics, and curvature that affect complexity.
11. **Sediment continuity** ï¿½ Balanced sediment supply and transport that maintains form and habitat.
12. **Bed composition and bedform dynamics** ï¿½ Substrate and bedforms (and wood effects) supporting habitat and exchange.

#### Physicochemistry (4)
13. **Light & thermal regime** ï¿½ Regulates temperature and energy inputs shaping chemistry and niches.
14. **Carbon processing** ï¿½ Organic matter dynamics supporting food webs and system metabolism.
15. **Nutrient cycling** ï¿½ N and P transformations controlling productivity and water quality.
16. **Water & soil quality** ï¿½ Contaminants/constituents influencing chemical condition and biotic health.

#### Biology (4)
17. **Habitat provision** ï¿½ Physical habitat diversity supporting life stages and taxa.
18. **Population support** ï¿½ Survival, reproduction, and movement of key taxa.
19. **Community dynamics** ï¿½ Balanced assemblages of native taxa; invasive pressure; resilience.
20. **Watershed connectivity** ï¿½ Longitudinal/lateral connectivity enabling colonization and recovery.

---

## Linking functions to outcomes (Clean Water Act framing)

The Clean Water Act emphasizes **physical**, **chemical**, and **biological** integrity. The Tiered Approach uses this framing because it:

- aligns assessment reporting with common regulatory language,
- makes interdependencies explicit (many functions influence multiple outcomes),
- supports roll-up scoring for clear communication and comparability.

### Default mapping logic (direct vs indirect)

- **Direct (D):** the function is fundamentally associated with that outcome (primary pathway).
- **Indirect (i):** the function influences that outcome through intermediate processes or context.
- **None (ï¿½):** no meaningful linkage assumed for the generic national framework.

> This mapping is a **starting point**. Regional context may justify adjustments (document changes).

### Outcome mapping table (starter / national default)

| Function | Physical | Chemical | Biological |
|---|---:|---:|---:|
| Catchment hydrology | D | i | i |
| Surface water storage | D | i | i |
| Reach inflow | D | i | ï¿½ |
| Streamflow regime | D | i | i |
| Low flow & baseflow dynamics | D | i | i |
| High flow dynamics | D | i | i |
| Floodplain connectivity | i | i | i |
| Hyporheic connectivity | i | i | i |
| Channel evolution | D | i | i |
| Channel & floodplain dynamics | D | i | i |
| Sediment continuity | D | i | i |
| Bed composition & bedform dynamics | D | i | i |
| Light & thermal regime | ï¿½ | D | i |
| Carbon processing | ï¿½ | i | i |
| Nutrient cycling | i | D | i |
| Water & soil quality | i | D | i |
| Habitat provision | ï¿½ | ï¿½ | D |
| Population support | i | i | D |
| Community dynamics | i | i | D |
| Watershed connectivity | i | ï¿½ | D |

---

## The three tiers

### What the tiers do (and do not do)

Tiers **do not** change what you care about (functions).  
Tiers **do** change what you measure (metrics/methods), how long it takes, and how much confidence you can expect.

### Tier overview

#### Tier 1 ï¿½ Screening
**Goal:** rapid, low-cost, desktop-oriented snapshot for early planning and broad prioritization.  
**Typical effort:** minutes to hours per site; minimal field time (optional verification).  
**Primary data:** GIS / remotely sensed / existing datasets; imagery; basic reconnaissance.  
**Output:** function scores with higher uncertainty, useful for screening and prioritizing.

#### Tier 2 ï¿½ Rapid
**Goal:** moderate-effort field-based assessment for reach comparison and alternatives evaluation.  
**Typical effort:** hours to ~1 day per reach.  
**Primary data:** structured field observations + simple measurements; can incorporate Screening metrics.  
**Output:** more confident function scores supporting site ranking and design direction.

#### Tier 3 ï¿½ Detailed
**Goal:** high-effort assessment for final design, compliance, crediting, and monitoring.  
**Typical effort:** multiple days to weeks (sometimes longer across seasons).  
**Primary data:** intensive field measurements; lab sampling; monitoring; modeling; development/calibration of reference curves.  
**Output:** defensible, lower-uncertainty function scores and outcomes suitable for high-stakes decisions.

---

## Selecting a tier

Tier selection should be explicit and documented. Use criteria like:

- project phase (screening vs alternatives vs design vs monitoring),
- number of sites,
- decision risk (consequence of being wrong),
- acceptable uncertainty,
- budget/schedule constraints,
- availability of staff expertise and equipment,
- regulatory triggers requiring defensible evidence.

### Practical tier selection table (starter)

| Criterion | Screening | Rapid | Detailed |
|---|---|---|---|
| Typical use | watershed scoping, site screening | alternatives comparison, conceptual design support | final design, compliance, post-construction monitoring |
| Decision risk & tolerance for uncertainty | low risk; higher uncertainty acceptable | moderate risk; moderate uncertainty | high risk; low uncertainty required |
| Data sources | desktop/GIS | field + desktop | field + lab + models + reference calibration |
| Primary output | qualitative/semi-quantitative scores | semi-quantitative scores | quantitative, defensible scores + standards |
| Sites per phase | many (often >20) | some (ï¿½5ï¿½20) | few (=5) |
| Field time per site | none to <1 hr | 1 hrï¿½1 day | 1 dayï¿½1+ weeks |
| Reference curves | usually adopt published / coarse | adopt and possibly adapt | often develop/calibrate regionally |

---

## Workflow for building and applying a tiered assessment

This workflow is designed to be repeatable and transparent. It is compatible with structured assessment development guidance: select tier ? define functions ? select metrics ? define reference/scoring ? compute and report.

### Step 1 ï¿½ Define decision context
**Inputs:** objectives, stakeholders, regulatory context, project phase, risk tolerance, schedule/budget, stream types.  
**Outputs:** a clear problem statement and how assessment results will be used.

**Deliverable:** 1ï¿½2 page ï¿½Assessment Purpose & Decision Contextï¿½ memo.

### Step 2 ï¿½ Select tier(s)
**Inputs:** decision context + tier criteria table.  
**Decision:** pick Screening, Rapid, Detailed ï¿½ or a phased plan (e.g., Screening ? Rapid on top candidates ? Detailed on finalists).  
**Outputs:** tier selection rationale, resource needs, list of uncertainties.

**Deliverable:** completed Tier Selection Worksheet (short form) + assumptions list.

### Step 3 ï¿½ Define relevant stream functions
Start from the 20-function list.  
If tailoring, do so by **merging functions** (not deleting without justification), and document traceability.

**Deliverable:** Functions list for the project/region + short definitions + notes on any merges.

### Step 4 ï¿½ Select metrics and methods (tier-appropriate)
For each function, select 1+ feasible metrics at the chosen tier.

**Rules of thumb**
- Screening: usually 1 metric per function (proxies from national datasets).
- Rapid: 1ï¿½several rapid field metrics per function, plus Screening metrics as needed.
- Detailed: direct measures where possible; may include Rapid/Screening metrics for context.

**Deliverable:** ï¿½Metrics-to-Functions Tableï¿½ with:
- function name
- metric(s)
- method/data source
- minimum tier
- units
- sampling window
- QA/QC notes
- scoring standard availability (adopt / adapt / develop)

### Step 5 ï¿½ Establish reference framework and scoring standards
You need a way to convert raw metric values to comparable scores.

Each metric should specify:
- reference condition definition (least disturbed / best available),
- performance standard or scoring curve,
- stratification (stream type, region, drainage area, etc.),
- whether thresholds are adopted, adapted, or developed.

**Deliverable:** ï¿½Reference & Scoring Appendixï¿½ (even if short for Screening).

### Step 6 ï¿½ Collect data and compute metric values
Implement the tierï¿½s field/desktop plan.  
Ensure you capture metadata (date, reach ID, coordinates, method version, assessor).

**Deliverable:** complete dataset + metadata + QA/QC checklist.

### Step 7 ï¿½ Score metrics ? functions ? outcomes ? overall condition
This is the core scoring pipeline:

1. **Metric scores** (standardized scale)
2. **Function scores** (combine metric scores for each function)
3. **Outcome scores** (physical/chemical/biological roll-up)
4. **Overall condition score** (optional single score)

**Deliverable:** scoring workbook (or scripted calculation) + exported results table.

### Step 8 ï¿½ Report results and document decisions
Reporting should keep both:
- **roll-up scores** (easy to compare), and
- **component scores** (so you can see tradeoffs).

**Deliverable:** report template with:
- tier selection rationale
- functions evaluated
- metric list and data sources
- scoring rules
- results tables/figures
- uncertainties and limitations
- recommendations (e.g., whether to move up a tier)

---

## Scoring framework (recommended standard structure)

The goal is a scoring method that is:
- transparent,
- repeatable,
- comparable across tiers and sites,
- flexible enough for regional tailoring.

### Recommended scoring scale
Pick a consistent scale for function scores, such as:
- **0ï¿½1** (normalized), or
- **0ï¿½10** (more intuitive for many users).

Once selected, keep it consistent across tiers.

### Metric ? function scoring
A function may have multiple metrics. Common combination options include:

- **Arithmetic mean** (default): use when metrics represent similar importance and quality.
- **Weighted mean**: use when metrics have different reliability or relevance.
- **Minimum / limiting factor**: use when the weakest component controls function (use sparingly; document justification).

**Recommendation:** Start with mean or weighted mean; use limiting-factor logic only with strong justification.

### Function ? outcome (physical/chemical/biological) scoring

Define weights based on direct vs indirect mapping:

- weight = 1.0 for **Direct (D)**
- weight = 0.25 for **Indirect (i)** (starter default; adjust via sensitivity analysis)
- weight = 0 for **None (ï¿½)**

Compute outcome scores as weighted averages:

**Outcome score** = S(FunctionScore ï¿½ Weight) / S(Weight)

Do this separately for:
- Physical outcome index
- Chemical outcome index
- Biological outcome index

### Overall condition score

Compute overall condition score as an average of the three outcomes:

**Overall condition** = (Physical + Chemical + Biological) / 3

> Report outcome indices even if you report an overall score. The sub-indices show *why* a site scored the way it did.

---

## Worked scoring example (simple)

Assume function scores on a 0ï¿½10 scale.  
Assume the default mapping weights (Direct = 1.0; Indirect = 0.25; None = 0).

Example function scores (subset for illustration):

- Catchment hydrology = 6
- Streamflow regime = 7
- Water & soil quality = 4
- Nutrient cycling = 5
- Habitat provision = 8
- Community dynamics = 6

### Physical outcome
Catchment hydrology (D ? 1.0), Streamflow regime (D ? 1.0), Water & soil quality (i ? 0.25), Nutrient cycling (i ? 0.25), Habitat provision (ï¿½ ? 0)

Physical = (6ï¿½1 + 7ï¿½1 + 4ï¿½0.25 + 5ï¿½0.25) / (1 + 1 + 0.25 + 0.25)  
Physical = (6 + 7 + 1 + 1.25) / 2.5 = 15.25 / 2.5 = 6.1

### Chemical outcome
Water & soil quality (D ? 1.0), Nutrient cycling (D ? 1.0), Catchment hydrology (i ? 0.25), Streamflow regime (i ? 0.25), Community dynamics (i ? 0.25)

Chemical = (4ï¿½1 + 5ï¿½1 + 6ï¿½0.25 + 7ï¿½0.25 + 6ï¿½0.25) / (1 + 1 + 0.25 + 0.25 + 0.25)  
Chemical = (4 + 5 + 1.5 + 1.75 + 1.5) / 2.75 = 13.75 / 2.75 = 5.0

### Biological outcome
Habitat provision (D ? 1.0), Community dynamics (D ? 1.0), Catchment hydrology (i ? 0.25), Streamflow regime (i ? 0.25), Nutrient cycling (i ? 0.25), Water & soil quality (i ? 0.25)

Biological = (8ï¿½1 + 6ï¿½1 + 6ï¿½0.25 + 7ï¿½0.25 + 5ï¿½0.25 + 4ï¿½0.25) / (1 + 1 + 0.25 + 0.25 + 0.25 + 0.25)  
Biological = (8 + 6 + 1.5 + 1.75 + 1.25 + 1.0) / 3.0 = 19.5 / 3 = 6.5

### Overall condition
Overall = (6.1 + 5.0 + 6.5) / 3 = 5.9

This example shows how:
- indirect weights allow cross-domain influence without dominating the outcome,
- reporting outcome sub-indices preserves interpretability,
- a single overall score can still be computed for summary comparison.

---

## How to perform assessments by tier (practical guidance)

### Screening-tier assessment (desktop-first)

**When to use**
- watershed screening and prioritization
- early project planning where you may evaluate many sites
- low-risk decisions where higher uncertainty is acceptable

**Typical workflow**
1. Define reaches and watershed context.
2. Compute a small set of national, repeatable desktop metrics for each function.
3. Convert metric values to standardized scores using published thresholds or relative scoring (e.g., percentiles within a region/stratum).
4. Roll up to outcomes and an optional condition score.
5. Identify ï¿½high potential / low constraintï¿½ candidate reaches for Rapid tier.

**Recommended outputs**
- map of candidate reaches and scores
- table of function scores + outcome scores
- ranked list with notes on uncertainty and next steps

**Best practice goal**
- make this tier **repeatable and automatable** (scripted geoprocessing) so the main user burden is interpretation, not manual calculation.

### Rapid-tier assessment (field-based, standardized)

**When to use**
- comparing several candidate sites
- alternatives evaluation / conceptual design support
- validating Screening-tier findings with field evidence

**Typical workflow**
1. Use Screening results to focus the field effort.
2. Apply a structured rapid field protocol (standard form + clear scoring rules).
3. Use a scoring workbook/calculator to compute function/outcome/condition scores.
4. Compare sites and alternatives; decide whether any site requires Detailed tier for defensibility.

**Recommended outputs**
- completed field forms
- workbook outputs (function + outcomes)
- notes on constraints/opportunities observed in field

### Detailed-tier assessment (regional calibration + intensive data)

**When to use**
- high-risk decisions, compliance/permitting, mitigation crediting/debiting
- final design requiring precise understanding of processes
- post-construction monitoring and performance verification
- region-specific studies requiring tailored reference curves or models

**Typical workflow**
1. Define detailed questions and performance standards.
2. Build or adopt a reference network and stratification scheme.
3. Collect intensive field and lab data (e.g., water samples, biological sampling, sensors).
4. Develop or calibrate reference curves and thresholds.
5. Compute function and outcome scores with documented uncertainty.
6. Produce defensible documentation for review.

**Recommended outputs**
- dataset + QA/QC documentation
- calibrated scoring curves/standards
- results + uncertainty/sensitivity analysis
- recommendations for management actions and monitoring plan

---

## Maintaining comparability across tiers

Comparability does not require identical metrics across tiers. It requires:

1. **Consistent function definitions** (same functions, same meaning).
2. **Common score scale** (same scoring range).
3. **Transparent methods and versioning** (document which metrics, thresholds, and weights were used).
4. **Traceable substitutions** (if a function uses different metrics at different tiers, document the substitution and expected uncertainty).
5. **Reporting of sub-scores** (functions + outcomes, not only one final number).

---

## What to publish and keep updated (recommended artifacts)

To make this approach usable in practice, publish and maintain these ï¿½livingï¿½ artifacts:

- **Functions list** (the 20 functions + definitions + mapping to outcomes)
- **Tier selection worksheet** (short decision support tool)
- **Metric toolbox** (metrics that can represent each function at each tier)
- **Scoring workbook templates** (Screening, Rapid, Detailed variants if needed)
- **Reference framework documentation** (especially for Detailed tier)
- **Change log** (what changed, when, why)

---

## Suggested appendices (optional)

### Appendix A ï¿½ Tier Selection Worksheet (template)
- Project phase:
- Number of sites:
- Decision risk:
- Acceptable uncertainty:
- Budget/schedule constraints:
- Regulatory context:
- Selected tier and rationale:
- Functions emphasized:
- Data availability constraints:
- Next milestone to revisit tier decision:

### Appendix B ï¿½ Metrics-to-Functions Table (template columns)
- Function ID
- Function name
- Metric name
- Metric description
- Method/data source
- Tier
- Units
- Sampling window
- Scoring standard (adopt/adapt/develop)
- Notes/uncertainty

### Appendix C ï¿½ Scoring workbook core columns (recommended)
- Reach ID
- Date
- Function scores (20 columns)
- Physical outcome index
- Chemical outcome index
- Biological outcome index
- Overall condition score
- Notes / flags / assessor ID
- Version of scoring rules

</div>
</div>
