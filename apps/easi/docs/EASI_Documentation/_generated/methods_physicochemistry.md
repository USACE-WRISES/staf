| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **Stream Temperature** | Function | Light & thermal regime |
|  | Automated method | Thermal-regulation vulnerability |
|  | Inputs | Woody riparian cover (%); Watershed impervious cover (%) |
|  | Equation | Icombined = min(Iwoody, Iimpervious) |
|  | Scoring | *Woody riparian cover* — Good ≥75%; Fair 25–<75%; Poor <25%<br>*Watershed impervious cover* — Good <10%; Fair 10–25%; Poor >25% |
|  | Basis | published directional relationship; confidence L; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | This method estimates vulnerability to thermal loading and loss of shade; it does not estimate stream temperature.<br>Both inputs are required and the specific bands are provisional screening classes.<br>Valid WQP temperature observations may be displayed as context but are not scored without an applicable class, season, and exposure rule. |
| **Detrital Processing (CPOM retention / shredders)** | Function | Carbon processing |
|  | Automated method | Organic-matter supply potential proxy |
|  | Inputs | Riparian forest (%); Riparian shrub (%); Riparian grassland (%); Riparian wetland (%) |
|  | Equation | V = min(forest + shrub + grassland + wetland, 100) |
|  | Scoring | Good >50%; Fair >20–≤50%; Poor ≤20% |
|  | Basis | provisional STAF screening judgment; confidence M; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | The method estimates organic-matter supply potential, not CPOM retention or shredder condition.<br>All expected source classes must be present; missing classes are not treated as zero.<br>Breakpoints are provisional STAF screening judgments. |
| **Nitrogen & Phosphorus Concentrations** | Function | Nutrient cycling |
|  | Automated method | Nutrient condition |
|  | Inputs | Total nitrogen (mg/L); Total phosphorus (mg/L) |
|  | Equation | Icombined = min(ITN, ITP) |
|  | Scoring | Categorical; see the source hierarchy. |
|  | Basis | published threshold; confidence M/L |
|  | Source hierarchy | 1. Normalized WQP TN/TP observations — Use valid total-fraction observations from stations within five miles and the preceding ten years.<br>2. StreamCat CHEM integrity fallback — Use when no qualifying TN or TP observation can be normalized. |
|  | Known limitations | Nearby monitoring stations are not necessarily reach-specific.<br>Each station is reduced to a median before station medians are combined, preventing heavily sampled stations from dominating.<br>One analyte may produce a partial rating.<br>When observations are unavailable, the CHEM fallback represents landscape integrity rather than measured nutrient concentration. |
| **Regulatory Impairment Status (305b/303d/TMDL)** | Function | Water & soil quality |
|  | Automated method | Regulatory impairment |
|  | Inputs | ATTAINS integrated-report category |
|  | Equation | Rating = lookup(IR category) |
|  | Scoring | Good Category 1; Category 2; Fair Category 4a; Category 4b; Poor Category 4c; Category 5 |
|  | Basis | dataset reference; confidence H |
|  | Source hierarchy | 1. Conclusive ATTAINS category — Use an intersecting or qualifying nearby Category 1, 2, 4a, 4b, 4c, or 5 assessment.<br>2. StreamCat CHEM condition fallback — Use when ATTAINS is absent or Category 3 is inconclusive; the result is condition context, not a regulatory determination. |
|  | Known limitations | Category 3 remains unscored because evidence is insufficient.<br>A nearby unit within 2 km is explicitly labeled nearby and may not represent the selected reach.<br>When ATTAINS is absent or inconclusive, the CHEM fallback is labeled water-quality condition context and not a regulatory determination. |

: Physicochemistry metrics {#tbl-metrics-physicochemistry}
