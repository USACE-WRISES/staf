# 1D Cross-Section Hydraulics Calculator

Launch the web app from: https://usace-wrises.github.io/xs-calc/

A single-page web app that computes 1D open-channel hydraulics for a single cross-section using Manning’s equation. Open `index.html` in a modern browser, build or import a cross-section, and the app computes discharge and other metrics (or, if you specify discharge, it solves for stage/depth that conveys it).

**Key Features**
- **Units:** US (ft, s) or SI (m, s); `k=1.486` (US) or `k=1.0` (SI).
- **Specify Mode:** Solve for Q from depth D, or enable `Specify Discharge` to solve for stage from Q.
- **Cross-Section Editor:** Station–Elevation table with LB/RB tags, insert/delete, sort, keyboard nav, and type-to-edit.
- **Banks:** Mark exactly one Left Bank (LB) and one Right Bank (RB) to split into LOB, Channel, ROB.
- **HVn (Horizontal Variation of n):** Turn on to assign n at stations; carry-forward n between breaks; channel uses composite n when multiple n values exist and either bank is steeper than ~5H:1V.
- **n Picker:** Catalog with min/typ/max and search across categories.
- **HCS (Hydraulic Controls & Structures):** Ineffective Flow Areas, Obstructions, Levees (with levee-based effective width clipping).
- **Filter Tool:** Funnel icon lets you thin points (near/colinear filter or “minimize area change”) while preserving banks and endpoints.
- **Results:** XS average and per-zone (LOB/Channel/ROB) metrics; distribution by slices; conveyance-by-n when HVn is ON.
- **Plot:** Interactive XS with water fill, optional Velocity/Shear overlays (XS average or per-slice), n-segment strip, IFA/obstruction/levee graphics.
- **Quality of Life:** Reset example, harmonized button widths, tooltips, and Help modal.

**Three Common Workflows**
- **(1) Analyze Existing Data (e.g., from Excel):**
  - Open `index.html` and select units.
  - In the Cross-Section table, enter (or paste into inputs) Station and Elevation for each point from left to right; the grid adds rows automatically.
  - Mark exactly one LB and one RB row.
  - Set slope `S`, choose either depth `D` (to compute `Q`) or enable `Specify Discharge` and enter `Q` (to solve for stage).
  - Optionally set LOB/Channel/ROB n values, or turn on `Horizontal Variation of n` and enter n by station (first row must have an n; values carry forward until the next break).
  - Review Results, Conveyance, and Distribution; use the plot toggle for Velocity/Shear views.

- **(2) Import from USGS 3DEP:**
  - Click the `3DEP` button in the “Cross-Section Data” header.
  - In the 3DEP modal, choose a location/profile and import. Units convert automatically to your current unit system.
  - Back in the table, mark LB and RB (required), then proceed as above with slope, D or Q, and n options.

- **(3) Design a Cross-Section (Designer tab):**
  - Open the `Designer` tab and set Stage parameters (e.g., bankfull width/depth, side slopes), optional inner berm, and advanced controls.
  - Click `Apply` to synthesize a cross-section into the main table, or enable `Update XS Data` to auto-apply as you change inputs.
  - Mark (or confirm) LB/RB, then set hydraulics (S, D or Q, n/HVn) and analyze.

**UI Guide**
- **Settings:** Units, slope `S`, LOB/Channel/ROB `n`, depth `D` or `Specify Discharge` ? `Q`. Slice counts for LOB/Channel/ROB (total capped at 45).
- **Cross-Section:**
  - Table supports insert/delete/sort and keyboard navigation. A trailing blank row is kept automatically.
  - Use the header funnel icon to open the Filter dialog (Near/Colinear or Minimize Area Change). Banks/endpoints are preserved.
  - Bank tools in Results header let you step LB/RB to neighboring points or click the plot to set banks.
- **HCS:** Add IFAs, Obstructions, Levees. These adjust the effective bed and/or flow domain for computations.
- **HVn:** When ON, the n column is shown in the table and LOB/Channel/ROB n inputs are disabled. Enter n on the leftmost row; values carry forward until the next explicit n.
- **Results:**
  - Summary for XS/Channel/LOB/ROB: Q, n used, V, P, A, R, T, Davg/Dmax, t, unit stream power, Froude.
  - Distribution (by slices): Q, A, P, % conveyance, hydraulic depth, V, t, power for each slice.
  - Conveyance (HVn ON): per-n-segment A, P, R, K plus K_L/K_C/K_R totals.
- **Plot:** Water surface, bed, banks, n labels, IFA/Obstruction fills, levee markers; optional Velocity/Shear (XS-average or per-slice) on secondary axes.

**Notes**
- **Equations:** `K = (k/n) · A · R^(2/3)`, `Q = K · vS`. Unit weights/acceleration handled per unit system.
- **Slices:** Total slices across LOB/Channel/ROB are capped at 45 (for parity with common practice).
- **Help:** The `?` button in-app gives a concise overview and formulas.

**Files**
- `index.html`: App UI markup and styles; includes external scripts only.
- `app.core.js`: Core computational helpers (geometry, hydraulics) with no DOM access.
- `app.ui.js`: UI wiring and rendering (moved from inline script).
- `designer.js`: Parametric cross-section generator used by the Designer tab.
- `filter.html`, `filter_logic.js`: Embedded filter tool for thinning cross-section points.
- `usgs.html`, `app.js`: USGS 3DEP profile tool (standalone or embedded).

- `index.html`: App UI + core logic.
- `designer.js`: Parametric cross-section generator used by the Designer tab.
- `filter.html`, `filter_logic.js`: Embedded filter tool for thinning cross-section points.
