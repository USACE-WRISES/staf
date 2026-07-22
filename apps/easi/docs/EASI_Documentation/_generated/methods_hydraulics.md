| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **Low-flow Wetted Connectivity** | Function | Low flow and baseflow dynamics |
|  | Automated method | Low-flow condition |
|  | Inputs | Wetted channel (%); Expected NHD flow regime (context only) |
|  | Equation | Rating = bands(wetted) |
|  | Scoring | Good >75%; Fair 25–75%; Poor <25% |
|  | Basis | dataset reference; confidence M |
|  | Source hierarchy | 1. NRSA wetted-channel observation — Use an exact COMID first, then a NLDI-confirmed connected mainstem site within five miles and ten years.<br>2. StreamCat HYD integrity fallback — Use only when eligible connected NRSA evidence is unavailable. |
|  | Known limitations | A connected nearby NRSA visit is not necessarily representative of the selected reach or current season.<br>Natural intermittent and ephemeral reaches must be evaluated against their expected flow regime.<br>FCODE remains context only and never supplies the rating. |
| **Floodplain Engagement Frequency (bankfull recurrence)** | Function | High flow dynamics |
|  | Automated method | Floodplain engagement — bank-height ratio |
|  | Inputs | Bank-height ratio (ratio) |
|  | Equation | Rating = bands(BHR) |
|  | Scoring | Good ≤1.3; Fair >1.3–≤1.5; Poor >1.5 |
|  | Basis | published threshold; confidence M |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | A BHR below 1.0 remains Good but requires geometry verification.<br>The DEM-derived cross section is a screening estimate and may require surveyed geometry. |
| **Floodplain Access / Entrenchment** | Function | Floodplain connectivity |
|  | Automated method | Floodplain access — entrenchment ratio |
|  | Inputs | Entrenchment ratio (ratio) |
|  | Equation | Rating = bands(ER) |
|  | Scoring | Good ≥2.2; Fair 1.4–<2.2; Poor <1.4 |
|  | Basis | published threshold; confidence M |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | This is an unstratified confinement/access screen.<br>Naturally confined valley settings require field interpretation. |
| **Hyporheic Exchange Indicators** | Function | Hyporheic connectivity |
|  | Automated method | Hyporheic-exchange potential |
|  | Inputs | Channel slope (m/m); Reach sinuosity (ratio) |
|  | Equation | V = 0.6 × min(slope/0.01, 1) + 0.4 × clamp((sinuosity−1)/0.5, 0, 1) |
|  | Scoring | Good ≥0.60; Fair 0.30–<0.60; Poor <0.30 |
|  | Basis | provisional STAF screening judgment; confidence L; provisional screening transitions |
|  | Source hierarchy | Single source; no fallback. |
|  | Known limitations | Bed permeability, sediment texture, and hydraulic head observations are unavailable.<br>Weights, caps, and bands are provisional STAF judgments rather than published ecological thresholds. |

: Hydraulics metrics {#tbl-metrics-hydraulics}
