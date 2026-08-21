| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **Channel Evolution Stage & Trends** | Function | Channel evolution |
|  | Automated method | Channel evolution |
|  | Inputs | Bank-height ratio (ratio) · Entrenchment ratio (ratio) · NHD feature classification (context only) |
|  | Equation | Icombined = min(IBHR, IER) |
|  | Scoring | *Bank-height ratio*: Good ≤1.3 · Fair >1.3-≤1.5 · Poor >1.5<br>*Entrenchment ratio*: Good ≥2.2 · Fair 1.4-<2.2 · Poor <1.4 |
|  | Basis | published directional relationship, confidence L, provisional screening transitions |
|  | Source hierarchy | 1. Documented channel-stage assessment: A user-documented stable/recovered, moderately adjusting, or severely adjusting condition supersedes the proxy.<br>2. Canal/ditch classification: A canal/ditch FCODE is directly classified Poor.<br>3. BHR and ER susceptibility proxy: For other reaches, the worse BHR or ER index governs. |
|  | Known limitations | BHR and ER indicate susceptibility and provide channel-evolution clues. They do not establish a formal stage.<br>Formal interpretation also considers headcuts, bars, width/depth change, bank erosion, and recovery features.<br>BHR and ER are reused by other metrics and are therefore correlated evidence. |
| **Bank Erosion & Armoring Condition** | Function | Channel and floodplain dynamics |
|  | Automated method | Bank erosion and armoring |
|  | Inputs | Bank-height ratio (ratio) |
|  | Equation | Rating = bands(BHR) |
|  | Scoring | Good ≤1.3 · Fair >1.3-≤1.5 · Poor >1.5 |
|  | Basis | published directional relationship, confidence L, provisional screening transitions |
|  | Source hierarchy | 1. Observed erosion and armoring: Complete user observations of both components supersede the proxy.<br>2. BHR susceptibility fallback: Used automatically when complete bank observations are unavailable. |
|  | Known limitations | BHR does not detect existing armoring and does not directly measure erosion rate or eroding-bank extent.<br>The DEM-derived cross section is a susceptibility screen and may require surveyed geometry.<br>BHR is reused by floodplain engagement and channel adjustment and is therefore correlated evidence. |
| **Sediment Supply Potential (watershed/banks)** | Function | Sediment continuity |
|  | Automated method | Sediment-supply potential |
|  | Inputs | Agricultural cover (%) · Soil erodibility K-factor · Road density (km/km²) |
|  | Equation | Icombined = min(Iagriculture, IK, Iroads) |
|  | Scoring | *Agricultural cover*: Good <30% · Fair 30-50% · Poor >50%<br>*Soil erodibility K-factor*: Good <0.25 · Fair 0.25-0.40 · Poor >0.40<br>*Road density*: Good <1.24 km/km² · Fair 1.24-<1.86 km/km² · Poor ≥1.86 km/km² |
|  | Basis | published directional relationship, confidence M, provisional screening transitions |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | The worse input governs. Source indicators are screened separately and are not summed.<br>Intrinsic soil erodibility (K-factor) can lower the rating in undisturbed watersheds. It indicates susceptibility rather than an active source.<br>The road-density bands derive from salmonid watershed criteria applied here nationally.<br>Agricultural cover and road density are also rated by other metrics and are therefore correlated evidence.<br>All three inputs are required. Missing inputs are not converted to zero.<br>Bank-derived sediment supply is not represented. The method screens watershed sources only. |
| **Substrate Condition (grain size/embeddedness/fines/consolidation)** | Function | Bed composition and large wood |
|  | Automated method | Substrate condition |
|  | Inputs | Substrate embeddedness (%) |
|  | Equation | Rating = bands(embeddedness) |
|  | Scoring | Good <25% · Fair 25-75% · Poor >75% |
|  | Basis | dataset reference, confidence M |
|  | Source hierarchy | 1. NRSA embeddedness observation: Use an exact COMID first, then a NLDI-confirmed connected mainstem site within five miles and ten years.<br>2. StreamCat SED integrity fallback: Use only when eligible connected NRSA embeddedness is unavailable. |
|  | Known limitations | A connected nearby NRSA visit may not represent the selected reach's bed condition.<br>Embeddedness does not fully characterize grain size, fine-sediment depth, consolidation, or large wood.<br>The landscape fallback estimates sediment integrity rather than observed substrate condition.<br>Embeddedness applies to gravel and coarser beds. The RBP treats it as a high-gradient parameter, and the survey convention scores sand and finer beds as 100 percent embedded, so naturally fine-bedded streams (for example coastal plain channels) can rate Poor by nature. Interpret against the expected natural bed. |

: Geomorphology metrics {#tbl-metrics-geomorphology}
