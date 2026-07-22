| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **In-stream Habitat Complexity & Cover** | Function | Habitat provision |
|  | Automated method | Habitat-support potential |
|  | Inputs | Woody riparian cover (%); Reach sinuosity (ratio) |
|  | Equation | V = 0.6 × min(woodyRiparian/60, 1) + 0.4 × clamp((sinuosity−1)/0.5, 0, 1) |
|  | Scoring | Good ≥0.55; Fair 0.30–<0.55; Poor <0.30 |
|  | Basis | provisional STAF screening judgment; confidence L; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | This is a low-confidence habitat-support proxy, not a field inventory of pools, wood, cover, or bedforms.<br>Weights, caps, and bands are provisional STAF judgments. |
| **Biological Integrity (IBI / community condition)** | Function | Population support |
|  | Automated method | Biological integrity |
|  | Inputs | Governing measured condition class; Benthic MMI class (context only); Fish MMI class (context only) |
|  | Equation | Rating = lookup(condition) |
|  | Scoring | Good Measured Good; Fair Measured Fair; Poor Measured Poor |
|  | Basis | dataset reference; confidence M |
|  | Source hierarchy | 1. Measured NRSA/state condition class — Use compatible measured benthic and fish classes; the worse class governs and one community is partial.<br>2. Published BMMI probability — Use StreamCat prG_BMMI where the reach is inside the published prediction frame.<br>3. Published ICI/IWI landscape products — Outside the BMMI prediction frame, calculate the published catchment and watershed integrity products and use the lower value. |
|  | Known limitations | A connected nearby NRSA visit is not necessarily representative of the selected reach.<br>One measured community is partial evidence; when both are available, the worse class governs.<br>Predicted probabilities and integrity products remain modeled landscape condition and are never relabeled as measured IBI values. |
| **Invasive / Non-native Species Presence** | Function | Community dynamics |
|  | Automated method | Invasive-species pressure |
|  | Inputs | Established NAS taxa count (recorded taxa) |
|  | Equation | Rating = bands(taxa) |
|  | Scoring | Good 0 recorded; Fair 1–2 recorded; Poor ≥3 recorded |
|  | Basis | dataset reference; confidence M; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | A successful zero is worded 'no established taxa recorded,' not confirmed absence.<br>HUC8 fallback results carry lower confidence than HUC12 results.<br>Count bands are STAF screening judgments. |
| **Fish Passage & Barrier Effects (longitudinal connectivity)** | Function | Watershed connectivity |
|  | Automated method | Nearby dam-proximity proxy |
|  | Inputs | Mapped NID dams within one mile (mapped dams) |
|  | Equation | Rating = bands(dams) |
|  | Scoring | Good 0 mapped dams; Fair ≥1 mapped dam |
|  | Basis | dataset reference; confidence M; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | Dam count alone cannot establish passability or severity and therefore cannot generate Poor.<br>NID does not comprehensively represent culverts, smaller structures, or passability.<br>Query failure is unscored, not Good. |

: Biology metrics {#tbl-metrics-biology}
