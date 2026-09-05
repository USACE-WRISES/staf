# SFARI — Stream Functional Assessment Rapid Index (web app)

A Shiny-for-Python web application for applying **SFARI**, a rapid stream
functional assessment. From a single clicked map point it delineates the upstream
watershed and an assessment reach, pulls national **desktop GIS evidence** to
*support* the assessor's scoring (it does **not** auto-score), then walks the user
**function by function** to Likert-score ~82 metrics and assign each of 20 stream
functions a 0–15 score. Scores roll up to Physical / Chemical / Biological outcome
sub-indices and an overall **Ecosystem Condition Index**, and out to an EASI-style
screening report.

SFARI is a near-clone of the **EASI** app (`../easi`) — same look, feel,
mapping, and report — with scoring authority moved from the system to the user.

## Method structure

- **5 functional categories × 4 functions = 20 functions**; ~82 metrics.
- **Metrics** are user-scored on a 5-point **Likert** scale (Strongly Agree … Strongly
  Disagree, + Not Applicable) as *lines of evidence*.
- **Functions** are user-scored **0–15** by professional judgment (11–15 Functioning,
  6–10 Functioning-at-Risk, 0–5 Non-Functioning), guided by (but not dictated by) the
  metrics. An *optional* auto-suggest averages the doc's Likert→numeric values
  (SA=14…SD=2); the user may accept or override.
- **Rollup:** normalize ÷15 → outcome sub-indices with Direct=1.0 / indirect=0.10
  weights → ECI = mean(Physical, Chemical, Biological).

## Desktop evidence sources

Evidence is pulled per metric from national services and shown with a source
label, a provenance badge, and a suggested Likert; the assessor always scores.
One watershed engine answers the watershed metrics (the definitions live in
`libs/README.md` and on the STAF site's Computation Engines page):

1. **STAF site engine** (`sfari/engine_prefill.py` over the vendored
   `sfari/_vendor/site_engine/`): the exact watershed at the clicked point on
   the full-resolution NHD (true point watershed + 100 m riparian buffer) for
   impervious cover, agriculture, wetlands, riparian vegetation, road density,
   impoundments (NID normal storage), soil erodibility (area-weighted K), the
   2001 to 2021 impervious change, and dam storage per km2. Entries carry
   `origin="engine"`, the engine version, and a value text ending in
   "(exact watershed)". The engine also supplies the watershed and the
   assessment reach themselves, at every site. While it runs a mapped row is
   `pending`; when it fails or refuses the row is unavailable and says why.
   Nothing is substituted from a neighboring NHDPlus V2 reach (2026-09-05).
2. **Direct services** (`origin="pull"`): NWIS gages, WQP nutrients, NWI
   wetlands, NID dams near the reach, and the NHDPlus HR attributes the
   engine reports (slope, flow permanence, sinuosity).

Sessions saved before 2026-09-05 may carry `origin="streamcat"` entries with
`anchor_label`, `fallback_reason`, or `upgrade_pending`; they still open and
display, and a new pull never produces them.

### Any NHD stream

The map draws the full high-resolution NHD from the engine's HR client, in one
color. Every click, and every typed point, snaps to it (`sfari/hr_site.py`, a
thin adapter over the vendored engine), the point lands at once, and
Delineate runs the STAF site engine for the exact watershed and the reach
(usually under a minute, up to about five minutes on a large basin, refused
past the interactive reach budget). If the engine fails the site stays on
Identify with the reason, so Delineate can retry; there is no covered-reach
basin to fall back on. Sessions carry `siteEngine` (geometry stripped) and
`watershedBasis` inside the delineation block; the schema version is unchanged.

## Layout

```
sfari/            Python package (config, scoring, models, evidence, engine_prefill, hr_site, datasources, …)
sfari/_vendor/    vendored STAF site engine (libs/site_engine), drift-gated
data/             generated JSONs: sfari-functions, sfari-metrics (82), sfari-outcome-mapping
scripts/          build_sfari_data.py (regenerates data/ from docs/SFARI_Clean.docx),
                  vendor_site_engine.py, build_fieldform_manifest.py, acceptance.py
tests/            scoring + likert parity + evidence + engine bridge + HR site tests
www/              CSS/JS (mirrors EASI)
```

## Develop / test

The pinned stack matches EASI; development uses the shared repo-root `.venv`
(see the monorepo README).

```
# regenerate data from the SFARI docx (one-time / when the doc changes)
python scripts/build_sfari_data.py

# run tests (golden parity: Physical 0.55 / Chemical 0.70 / Biological 0.30 / ECI 0.52)
python -m pytest
```

## Deploy (Posit Connect Cloud, via Posit Publisher)

This repo ships a Posit Publisher configuration (`.posit/publish/sfari.toml`) so it
can be deployed to **Posit Connect Cloud** straight from VS Code:

1. Install the **Posit Publisher** extension in VS Code and open this folder.
2. Open the Posit Publisher panel — it detects the `sfari` configuration
   (entrypoint `app.py`, Python 3.12, deps from `requirements.txt`).
3. Add / select your Connect Cloud credential, then click **Deploy**. Redeploys
   reuse the same content.

The bundle is `app.py`, `requirements.txt`, and the `sfari/`, `data/`, and `www/`
folders (the vendored site engine rides inside `sfari/`). The site engine needs
`requests`, `shapely`, and `geopandas` importable at runtime; if that stack is
absent the map draws no streams and the watershed evidence is unavailable; the
direct-service tiers still run.
No API keys are required at runtime; a free USGS NWIS key is optional
(higher rate limit on the shared egress IP), set as a Connect Cloud environment
variable. The HyRiver cache is directed to `/tmp` (ephemeral filesystem). Exports
(CSV / GeoJSON / PDF) and the cross-section plot are matplotlib-free (reportlab +
inline SVG), so there is no font-cache stall on first render. A field visit can be
saved to / resumed from a JSON file (Save / Resume).

Deployment records under `.posit/publish/deployments/` hold account-specific content
GUIDs/URLs and are git-ignored; Posit Publisher writes them locally on first deploy.

## Note on the outcome mapping

The SFARI document is internally inconsistent: **Table 1** (the reference framework)
differs from the mapping used in the document's own **worked example / calculator**.
Only the example mapping reproduces the published sub-indices, so the app adopts it
(`data/sfari-outcome-mapping.json`); Table 1 is retained as
`data/sfari-outcome-mapping-table1.json` for SME review. Differences: floodplain- and
hyporheic-connectivity Physical `D→i`, carbon-processing Chemical `D→i`.
