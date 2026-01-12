# STAF_Standalone

Standalone HTML/JavaScript port of the STAF page. Open `index.html` directly in a browser to run it.

## Contents
- `index.html`: Static markup for the STAF UI and dialogs.
- `staf.css`: Styling that mirrors the MudBlazor look-and-feel from the Blazor app.
- `staf.js`: Full client-side logic (data loading, rendering, scoring math, import/export).
- `data/StreamModelFunctions.csv`: Local copy of the functional variables database.
- `data/MetricToolbox.csv`: Local copy of the metric toolbox database.
- `vendor/exceljs.min.js`: Excel export library (used to match the original XLSX output).

## Notes
- CSV import/export uses the same column names as the Blazor version (selectedCategory/selectedVariable plus metric fields).
- Excel export reproduces the formulas, merges, and color bands from the C# ClosedXML export.
- The app first tries to load `data/` via fetch (for local servers). If that fails (for `file://`), it falls back to the embedded CSV in `index.html`.
