# SFARI — Stream Functional Assessment Rapid Index (web app)

A Shiny-for-Python web application for applying **SFARI**, a rapid stream
functional assessment. From a single clicked map point it delineates the upstream
watershed and an assessment reach, pulls national **desktop GIS evidence** to
*support* the assessor's scoring (it does **not** auto-score), then walks the user
**function by function** to Likert-score ~82 metrics and assign each of 20 stream
functions a 0–15 score. Scores roll up to Physical / Chemical / Biological outcome
sub-indices and an overall **Ecosystem Condition Index**, and out to an EASI-style
screening report.

SFARI is a near-clone of the **EASI** app (`../easi_claude`) — same look, feel,
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

## Layout

```
sfari/            Python package (config, scoring, models, evidence, datasources, …)
data/             generated JSONs: sfari-functions, sfari-metrics (82), sfari-outcome-mapping
scripts/          build_sfari_data.py — regenerates data/ from docs/SFARI_Clean.docx
tests/            scoring + likert parity tests
www/              CSS/JS (mirrors EASI)
```

## Develop / test

The pinned stack matches EASI; during development you can reuse the EASI venv.

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
folders. No API keys are required at runtime; a free USGS NWIS key is optional
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
