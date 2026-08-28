| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **Low-flow Wetted Connectivity** | Function | Low flow and baseflow dynamics |
|  | Automated method | Low-flow condition |
|  | Inputs | Wetted channel (%) · Expected NHD flow regime (context only) |
|  | Equation | Rating = bands(wetted) |
|  | Scoring | Good >75% · Fair 25-75% · Poor <25% |
|  | Basis | dataset reference, confidence M |
|  | Source hierarchy | 1. NRSA wetted-channel observation: Use an exact COMID first, then a NLDI-confirmed connected mainstem site within five miles and ten years.<br>2. StreamCat HYD integrity fallback: Use only when eligible connected NRSA evidence is unavailable. |
|  | Known limitations | A connected nearby NRSA visit is not necessarily representative of the selected reach or current season.<br>Natural intermittent and ephemeral reaches must be evaluated against their expected flow regime.<br>FCODE remains context only and never supplies the rating. |
| **Floodplain Engagement Frequency (bankfull recurrence)** | Function | High flow dynamics |
|  | Automated method | Floodplain engagement (bank-height ratio) |
|  | Inputs | Bank-height ratio (ratio) |
|  | Equation | Rating = bands(BHR) |
|  | Scoring | Good ≤1.3 · Fair >1.3-≤1.5 · Poor >1.5 |
|  | Basis | published threshold, confidence M |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | A BHR below 1.0 remains Good but requires geometry verification.<br>The DEM-derived cross section is a screening estimate and may require surveyed geometry. |
| **Floodplain Access / Entrenchment** | Function | Floodplain connectivity |
|  | Automated method | Floodplain access (entrenchment ratio) |
|  | Inputs | Entrenchment ratio (ratio) |
|  | Equation | Rating = bands(ER) |
|  | Scoring | Good ≥2.2 · Fair 1.4-<2.2 · Poor <1.4 |
|  | Basis | published threshold, confidence M |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | This is an unstratified confinement/access screen.<br>Naturally confined valley settings require field interpretation.<br>Published condition standards stratify by stream type. Alluvial C and E channels are functioning above 2.2 and not functioning below 2.0, while confined B channels are functioning above 1.4. This unstratified screen is therefore lenient for alluvial reaches and strict for naturally confined ones. |
| **Hyporheic Exchange Indicators** | Function | Hyporheic connectivity |
|  | Automated method | Hyporheic-exchange potential (channel gradient or sinuosity) |
|  | Inputs | Channel slope (m/m) · Reach sinuosity (ratio) |
|  | Equation | Icombined = max(Islope, Isinuosity) |
|  | Scoring | *Channel slope*: Good ≥0.006 m/m · Fair 0.003-<0.006 m/m · Poor <0.003 m/m<br>*Reach sinuosity*: Good ≥1.2 · Fair 1.05-<1.2 · Poor <1.05 |
|  | Basis | provisional STAF screening judgment, confidence L, provisional screening transitions |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | Slope screens vertical bedform-driven exchange and sinuosity screens lateral meander-driven exchange. Bed hydraulic conductivity is a co-dominant control that is unavailable, so steep reaches over bedrock or fine beds can overpredict exchange.<br>The better pathway governs because vertical and lateral exchange are alternative mechanisms. Either alone indicates exchange potential.<br>Sinuosity measured over generalized reach geometry understates planform sinuosity. Under the better-pathway rule that bias can only fail to lift a rating, never lower one. The earlier weighted slope and sinuosity composite was retired for exactly that downward bias.<br>A result computed from one pathway is labeled partial.<br>The slope and sinuosity bands are screening judgments, not published thresholds. |

: Hydraulics metrics {#tbl-metrics-hydraulics}
