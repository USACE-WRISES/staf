| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **In-stream Habitat Complexity & Cover** | Function | Habitat provision |
|  | Automated method | Habitat-support potential (woody riparian corridor) |
|  | Inputs | Woody riparian cover (%); Reach sinuosity (context only) |
|  | Equation | Rating = bands(woodyRiparian) |
|  | Scoring | Good >70%; Fair 50-70%; Poor <50% |
|  | Basis | published directional relationship; confidence L; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | Corridor woody cover is a habitat-support proxy, not a field inventory of pools, wood, cover, or bedforms.<br>The 50 and 70 percent bands are collapsed from RBP bank-vegetation categories that describe bank plots, applied here to the 100 m corridor.<br>Sinuosity is displayed as context and is not rated. Grass-dominated natural channels can provide habitat this proxy does not credit. |
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
|  | Known limitations | A successful zero is worded 'no established taxa recorded,' not confirmed absence.<br>HUC8 fallback results carry lower confidence than HUC12 results.<br>Count bands are STAF screening judgments.<br>Establishment status often cannot be confirmed from occurrence records, and uneven sampling effort inflates richness where effort is high (Mangiante et al. 2019). |
| **Fish Passage & Barrier Effects (longitudinal connectivity)** | Function | Watershed connectivity |
|  | Automated method | Nearby dam-proximity proxy |
|  | Inputs | Mapped NID dams within one mile (mapped dams) |
|  | Equation | Rating = bands(dams) |
|  | Scoring | Good 0 mapped dams; Fair 1 mapped dam; Poor ≥2 mapped dams |
|  | Basis | dataset reference; confidence M; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | A mapped dam count is a proximity screen and cannot confirm passability or the severity of a barrier.<br>NID carries about 91,000 regulated dams while the National Aquatic Barrier Inventory maps over 500,000 barriers of all sizes, so zero mapped dams is frequently a false negative for passage.<br>Query failure is unscored, not Good. |

: Biology metrics {#tbl-metrics-biology}
