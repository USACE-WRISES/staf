| Metric | Attribute | Detail |
|:--------|:------------------|:----------------------------------------------------|
| **Watershed Land-Cover Pressure** | Function | Catchment hydrology |
|  | Automated method | Catchment hydrology (land-cover pressure) |
|  | Inputs | Watershed impervious cover (%) · Watershed agricultural cover (%) |
|  | Equation | Icombined = min(Iimpervious, Iagriculture) |
|  | Scoring | *Watershed impervious cover*: Good <10% · Fair 10-25% · Poor >25%<br>*Watershed agricultural cover*: Good <30% · Fair 30-50% · Poor >50% |
|  | Basis | published directional relationship, confidence H, provisional screening transitions |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | The worse input governs because hydrologic alteration through one pathway is not offset by low pressure in the other.<br>Agricultural bands are national screening tiers, not ecological criteria.<br>A result computed from one input is labeled partial.<br>Sensitive macroinvertebrate taxa decline well below the 10 percent impervious Good boundary (community thresholds near 0.5 to 2 percent), so a Good rating is not a no-effect finding (King et al. 2011). |
| **Percent Wetlands in Watershed** | Function | Surface water storage |
|  | Automated method | Wetland extent |
|  | Inputs | Woody wetland cover (%) · Herbaceous wetland cover (%) |
|  | Equation | wetland % = min(woody wetland + herbaceous wetland, 100) |
|  | Scoring | Good >5% · Fair 1-5% · Poor <1% |
|  | Basis | provisional STAF screening judgment, confidence H, provisional screening transitions |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | Natural wetland abundance varies substantially among regions. |
| **Concentrated Runoff / Stormwater Inputs** | Function | Reach inflow |
|  | Automated method | Road-density proxy for concentrated inflow pressure |
|  | Inputs | Watershed road density (km/km²) |
|  | Equation | Rating = bands(road density) |
|  | Scoring | Good <1 km/km² · Fair 1-<3 km/km² · Poor ≥3 km/km² |
|  | Basis | published directional relationship, confidence L, provisional screening transitions |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | Road density does not count actual outfalls or establish whether runoff is treated.<br>The 1 km/km² breakpoint is a screening judgment.<br>USFS watershed-condition guidance classes road density more strictly (good under 1, impaired over 2.4 miles per square mile, about 0.62 and 1.49 km/km²), derived from salmonid watersheds. EASI keeps the NRSA-anchored national screen and discloses the stricter alternative. |
| **Flow Alteration (Regulation / Water Use)** | Function | Streamflow regime |
|  | Automated method | Flow alteration (degree of regulation) |
|  | Inputs | Upstream normalized storage (m³/km²) · Annual runoff (mm) |
|  | Equation | DOR (%) = 100 × DamNrmStorWs / (1000 × RunoffWs) |
|  | Scoring | Good <2% · Fair 2-15% · Poor >15% |
|  | Basis | published threshold, confidence M |
|  | Source hierarchy | Single source, no fallback. |
|  | Known limitations | Degree of regulation does not describe operating rules, diversions, seasonality, or dam location on the network.<br>Missing or non-positive runoff is not converted to zero and leaves the metric unscored. |

: Hydrology metrics {#tbl-metrics-hydrology}
