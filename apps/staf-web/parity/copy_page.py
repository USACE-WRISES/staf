"""Copy what the static app's page serves as is into apps/staf-web/page/, and record where it came from.

The static app keeps its own copy of everything its page takes from elsewhere, so it keeps working when
the Shiny STAF app (Posit Connect Cloud) and its folders are retired (the owner, 2026-10-11). ``page/`` is
Vite's public folder: served at the app's root in development and copied into ``docs/app/`` as is.

- **From the Shiny apps** (the page's look and its shared scripts, while those apps exist): each tool's
  ``www/styles.css`` (and DEEP's ``deep.css``), the shared ``staf.css`` and ``metric-rows.css`` and page
  scripts (``libs/staf_workbook/assets``), DEEP's ``coverage.js``, the shell's ``staf-shell.css`` and
  favicon (``apps/staf/www/shell``); SFARI's field forms PDF and blank calculator (``forms/sfari/``, Get
  Forms' downloads); DEEP's state polygons (``data/us_states.geojson.gz``, kept with the data mirror's
  builder, ``tools/staf-web-data/data/``, which publishes them as ``engine/us_states.geojson``).
- **From the Python packages the Shiny app runs on** (the repo's .venv): py-shiny's Bootstrap build, its
  fonts cut to the latin and latin-ext faces (every character the apps show), htmltools' ``fill.css``,
  ``shinywidgets.css``; matplotlib's DejaVu Sans cut to what EASI's report cross-section image draws
  (``fonts/``, with its license) and, as JSON, how matplotlib sets that text (``xsplot-text.json``: the
  hinted advances, ink extents, kerning and ligatures at the image's sizes), so the static app draws the
  image as easi/xsplot.py does.
- **From node_modules:** Leaflet's stylesheet and control images (Leaflet 1.9.4, the app's own
  dependency). The stylesheet leaves out Leaflet's rules for Internet Explorer 8 and older (``.lvml``,
  ``.leaflet-oldie``): they match nothing in a browser the app runs in, and they carry IE's script hooks
  (``behavior: url(#default#VML)``, ``progid:DXImageTransform`` filters), which network filters treat as
  attacks.
- **Written here by hand** and never replaced: ``css/shiny/jupyter.css`` and ``jupyter-leaflet.css``,
  the Jupyter widget rules the Shiny maps take from ipyleaflet (see their headers).

The same goes for the method content the app's code imports (``content/...``, written to
``src/app/content/``, not ``page/``): SFARI's functions, metrics and outcome mapping (``apps/sfari/data``),
DEEP's functions and outcome mapping (``apps/deep/data``) and its concise method texts (the ``CONCISE``
table of ``apps/deep/deep/method_text.py``, written as JSON), EASI's adopted-method pin (its scoring method
itself is the pinned package of the library mirror, read at run time as the Shiny EASI reads it from the
library release) and EASI's tables kept in Python modules (``config.json``: the metric registry,
definitions and calculation notes of the regional criteria, the watershed layer's keys and labels).

``page-sources.json`` records each copy's source and the sha256 both had when copied. A copy that was
edited here since is never overwritten (``--force`` does). ``--check`` copies nothing and reports which
sources changed since (the Shiny page moved on: copy again to follow it), which copies were edited here,
and which sources are gone (once the Shiny apps are retired, all of them: then ``page/`` is the source,
edited directly).

    .venv\\Scripts\\python apps/staf-web/parity/copy_page.py [--check] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
PAGE = APP / "page"
RECORD = APP / "page-sources.json"

#: published path -> repo path
FROM_REPO = {
    "favicon.svg": "apps/staf/www/shell/favicon.svg",
    "css/easi/styles.css": "apps/easi/www/styles.css",
    "css/sfari/styles.css": "apps/sfari/www/styles.css",
    "css/deep/styles.css": "apps/deep/www/styles.css",
    "css/deep/deep.css": "apps/deep/www/deep.css",
    "css/staf/staf.css": "libs/staf_workbook/assets/staf.css",
    "css/staf/metric-rows.css": "libs/staf_workbook/assets/metric-rows.css",
    "css/shell/staf-shell.css": "apps/staf/www/shell/staf-shell.css",
    "js/staf/staf-ns.js": "libs/staf_workbook/assets/staf-ns.js",
    "js/staf/geocode-autocomplete.js": "libs/staf_workbook/assets/geocode-autocomplete.js",
    "js/staf/legend-dock.js": "libs/staf_workbook/assets/legend-dock.js",
    "js/staf/tooltip.js": "libs/staf_workbook/assets/tooltip.js",
    "js/staf/coord-entry.js": "libs/staf_workbook/assets/coord-entry.js",
    "js/deep/coverage.js": "apps/deep/www/coverage.js",
    "js/staf/metric-rows.js": "libs/staf_workbook/assets/metric-rows.js",
    "js/staf/scenarios.js": "libs/staf_workbook/assets/scenarios.js",
    "js/sfari/field-review.js": "apps/sfari/www/field-review.js",
    "content/sfari/sfari-functions.json": "apps/sfari/data/sfari-functions.json",
    "content/sfari/sfari-metrics.json": "apps/sfari/data/sfari-metrics.json",
    "content/sfari/sfari-outcome-mapping.json": "apps/sfari/data/sfari-outcome-mapping.json",
    "js/deep/measure.js": "apps/deep/www/measure.js",
    "content/deep/deep-functions.json": "apps/deep/data/deep-functions.json",
    "content/deep/deep-outcome-mapping.json": "apps/deep/data/deep-outcome-mapping.json",
    "content/easi/adopted-method.json": "apps/easi/data/source/adopted-method.json",
    "js/staf/report-ready.js": "libs/staf_workbook/assets/report-ready.js",
    "content/shared/ecoregions.json": "libs/staf_workbook/staf_workbook/data/ecoregions.json",
    "js/easi/report-controls.js": "apps/easi/www/report-controls.js",
    "js/easi/report-edit.js": "apps/easi/www/report-edit.js",
    "js/easi/worksheet.js": "apps/easi/www/worksheet.js",
    "js/staf/unsaved-guard.js": "libs/staf_workbook/assets/unsaved-guard.js",
    "forms/sfari/SFARI_Field_Form_v1.0.pdf": "apps/sfari/data/FieldForm/SFARI_Field_Form_v1.0.pdf",
    "forms/sfari/SFARI_Calculator_Draft_2026-06-29.xlsx": "apps/sfari/data/calculator/SFARI_Calculator_Draft_2026-06-29.xlsx",
    "data/us_states.geojson.gz": "apps/deep/data/us_states.geojson.gz",
}
#: EASI's tables that live in Python modules, kept as JSON (``content/easi/config.json``): the metric
#: registry, definitions and calculation notes of the active (regional) criteria, and the watershed
#: evidence layer's keys and labels
EASI_WATERSHED_TABLES = ("VALUE_KEYS", "ACRE_FT_PER_KM2_TO_M3_PER_KM2", "_STREAMCAT_COLUMNS", "_ENGINE_KEYS",
                         "STREAMCAT_INPUT_SOURCES", "STREAMCAT_RESULT_SOURCES", "_ENGINE_INPUT_DETAILS",
                         "_ENGINE_RESULT_DETAILS", "_ENGINE_NOTES", "GUIDANCE_UNAVAILABLE", "ENGINE_FAMILIES")
OWN = ("css/shiny/jupyter.css", "css/shiny/jupyter-leaflet.css")
KEEP_FONTS = ("latin", "latin-ext")
FACE = re.compile(r"/\* ([a-z-]+) \*/\s*(@font-face\s*\{[^}]*\})")
FONT_SRC = re.compile(r"url\((fonts/[^)]+)\)")


#: text files are kept with LF line endings, as the repo stores them (.gitattributes), so a copy's
#: sha256 is the same in every checkout (Leaflet's stylesheet ships with CRLF)
TEXT = (".css", ".js", ".svg", ".json", ".txt")


def dest(path: str) -> Path:
    """Where a copy lives: ``content/...`` beside the app's code, ``data/...`` with the data mirror's
    builder (tools/staf-web-data/data: files it publishes), everything else in ``page/``."""
    if path.startswith("content/"):
        return APP / "src" / "app" / path
    if path.startswith("data/"):
        return REPO / "tools" / "staf-web-data" / path
    return PAGE / path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normal(path: str, data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n") if path.endswith(TEXT) else data


#: a rule of Leaflet's for Internet Explorer 8 and older: its selectors all start .lvml (VML shapes) or
#: .leaflet-oldie (the class Leaflet gives the map in IE 8 and older)
OLD_IE = re.compile(r"(?m)^(?:\.(?:lvml|leaflet-oldie)\b[^,{}\n]*,\n)*\.(?:lvml|leaflet-oldie)\b[^,{}\n]*\{[^{}]*\}[ \t]*\n")
#: what an IE rule left in the stylesheet would carry
IE_HOOKS = ("behavior", "progid:", "-ms-filter", "expression(")


def _without_old_ie(css: bytes) -> bytes:
    """Leaflet's stylesheet without its rules for Internet Explorer 8 and older (see the module's notes)."""
    text = OLD_IE.sub("", css.decode("utf-8").replace("\r\n", "\n"))
    left = [hook for hook in IE_HOOKS if hook in text]
    if left:
        raise SystemExit(f"Leaflet's stylesheet still carries {', '.join(left)}: OLD_IE needs the new rule")
    return text.encode("utf-8")


def _module(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _easi_config() -> bytes | None:
    """EASI's config and watershed tables as JSON, or None once the modules are gone. Both modules
    import only the standard library at the top, so each loads on its own."""
    import os
    cfg_path = REPO / "apps" / "easi" / "easi" / "config.py"
    ws_path = REPO / "apps" / "easi" / "easi" / "watershed.py"
    if not (cfg_path.is_file() and ws_path.is_file()):
        return None
    os.environ["EASI_CRITERIA_SET"] = "regional"
    cfg = _module("_easi_config", cfg_path)
    ws = _module("_easi_watershed", ws_path)
    mids = list(cfg.METRIC_REGISTRY)
    missing = "\0"
    data = {
        "criteriaSet": cfg.criteria_set(),
        "registry": cfg.metric_registry(),
        "definitions": cfg.METRIC_DEFINITIONS,
        "regionalDefinitions": dict((m, d) for m in mids
                                    if (d := cfg.metric_definition(m, fallback=missing)) != missing),
        "calculations": dict((m, c) for m in mids if (c := cfg.metric_calculation(m)) is not None),
        "watershed": dict((k, getattr(ws, k)) for k in EASI_WATERSHED_TABLES),
    }
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _easi_acquisition() -> bytes | None:
    """EASI's acquisition digest (``method_package.acquisition_digest``: the identity of the code and
    assets that gather a site's evidence), which a saved screening and its exports record. It hashes the
    Python sources, so the static app keeps the digest of the EASI it was certified against. None once
    EASI is gone."""
    root = REPO / "apps" / "easi"
    if not (root / "easi" / "method_package.py").is_file():
        return None
    sys.path.insert(0, str(root))
    try:
        from easi import method_package
        digest = method_package.acquisition_digest()
    finally:
        sys.path.remove(str(root))
    data = {"acquisitionDigest": digest,
            "note": "apps/easi easi.method_package.acquisition_digest(): the EASI the static app's port is certified against"}
    return (json.dumps(data, indent=1) + "\n").encode("utf-8")


#: the sizes (points) EASI's report cross-section image sets its texts in (easi/xsplot.py), at the figure's
#: own 100 dpi (where tight_layout measures it) and the 130 dpi it is saved at
XSPLOT_SIZES = (6.5, 8, 9, 10)
XSPLOT_DPIS = (100, 130)
#: the characters it draws: printable ASCII and the minus sign of negative tick labels
XSPLOT_CHARS = "".join(chr(c) for c in range(0x20, 0x7F)) + "−"


#: EASI's report summary plot (easi/report.py _summary_plots_png): its texts' sizes per weight at the 100
#: dpi tight_layout measures at and the 150 dpi the figure is saved at, and the characters it draws
#: (printable ASCII and the em dash of a missing score)
SUMMARY_SIZES = {"normal": (7, 7.2, 8, 10), "bold": (7.2, 8, 9.5)}
SUMMARY_DPIS = (100, 150)
SUMMARY_CHARS = "".join(chr(c) for c in range(0x20, 0x7F)) + "—"


def _ft_metrics(path, pairs, chars) -> dict:
    """``{"<dpi>:<points>": {ascent, descent, glyphs, ligatures, kern}}``: the font at each resolution and
    size as matplotlib's FT2Font sets it (FreeType's default hinting), every pair's kerning as the shaper
    places it."""
    from matplotlib import ft2font
    from matplotlib.backends.backend_agg import get_hinting_flag
    flags = get_hinting_flag()
    sizes = {}
    for dpi, pt in pairs:
        font = ft2font.FT2Font(str(path))
        font.set_size(pt, dpi)
        os2 = font.get_sfnt_table("OS/2")
        scale = pt * dpi / 72 / font.get_sfnt_table("head")["unitsPerEm"]
        def metrics_of(g):
            x0, y0, x1, y1 = g.bbox
            empty = x0 == x1 or y0 == y1
            return [g.horiAdvance / 64, 0 if empty else y0 / 64, 0 if empty else y1 / 64]
        glyphs = dict((ch, metrics_of(font.load_char(ord(ch), flags=flags))) for ch in chars)
        kern, ligatures = {}, {}
        for a in chars:
            for b in chars:
                items = font._layout(a + b, flags=flags)
                if len(items) == 1:      # a ligature (DejaVu's fi, fl): one glyph for the pair
                    ligatures[a + b] = metrics_of(font.load_glyph(items[0].glyph_index, flags=flags))
                elif abs(items[1].x - glyphs[a][0]) > 1e-9:
                    kern[a + b] = items[1].x - glyphs[a][0]
        # the kerning on either side of a ligature (keyed by the two texts, the ligature's in full)
        for lig in ligatures:
            for c in chars:
                before = font._layout(c + lig, flags=flags)
                if len(before) == 2 and abs(before[1].x - glyphs[c][0]) > 1e-9:
                    kern[c + lig] = before[1].x - glyphs[c][0]
                after = font._layout(lig + c, flags=flags)
                if len(after) == 2 and abs(after[1].x - ligatures[lig][0]) > 1e-9:
                    kern[lig + c] = after[1].x - ligatures[lig][0]
        sizes[f"{dpi}:{pt:g}"] = {"ascent": os2["sTypoAscender"] * scale, "descent": -os2["sTypoDescender"] * scale,
                                  "glyphs": glyphs, "ligatures": ligatures, "kern": kern}
    return sizes


def _woff2(path, chars) -> bytes:
    """The font cut to ``chars``, as woff2 (its names, kerning and ligatures kept)."""
    import io

    from fontTools import subset
    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.layout_features = ["kern", "liga"]
    font = subset.load_font(str(path), options)
    sub = subset.Subsetter(options)
    sub.populate(unicodes=[ord(c) for c in chars])
    sub.subset(font)
    buf = io.BytesIO()
    subset.save_font(font, buf, options)
    return buf.getvalue()


def _summaryplot_text() -> tuple[dict, dict] | None:
    """EASI's report summary plot as matplotlib sets its text: DejaVu Sans and DejaVu Sans Bold metrics
    per ``"<weight>:<dpi>:<points>"`` (as _ft_metrics), and both faces cut to the plot's characters. None
    without matplotlib."""
    try:
        import matplotlib
        import fontTools  # noqa: F401
    except ImportError:
        return None
    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    faces = {"normal": ttf / "DejaVuSans.ttf", "bold": ttf / "DejaVuSans-Bold.ttf"}
    sizes = {}
    for weight, path in faces.items():
        pairs = [(d, p) for d in SUMMARY_DPIS for p in SUMMARY_SIZES[weight]]
        for key, m in _ft_metrics(path, pairs, SUMMARY_CHARS).items():
            sizes[f"{weight}:{key}"] = m
    metrics = {"source": f"matplotlib {matplotlib.__version__}: DejaVu Sans and DejaVu Sans Bold set with FreeType's default "
                         "hinting (per weight:dpi:points, each glyph's advance and ink bottom and top; pair kerning)",
               "sizes": sizes}
    return metrics, {"fonts/dejavu-sans-summary.woff2": _woff2(faces["normal"], SUMMARY_CHARS),
                     "fonts/dejavu-sans-bold.woff2": _woff2(faces["bold"], SUMMARY_CHARS)}


def _xsplot_text() -> tuple[dict, dict] | None:
    """EASI's report cross-section image as matplotlib sets its text (DejaVu Sans, FreeType's default
    hinting): per resolution and size (``"<dpi>:<points>"``) the font's typographic ascent and descent,
    each character's hinted advance and ink extent, the ligatures, and every pair's kerning as the shaper
    places it; and the font itself, cut to those characters (woff2) with its license. None without
    matplotlib."""
    try:
        import io

        import matplotlib
        from fontTools import subset
        from matplotlib import ft2font
        from matplotlib.backends.backend_agg import get_hinting_flag
    except ImportError:
        return None
    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    flags = get_hinting_flag()
    sizes = {}
    for dpi, pt in [(d, p) for d in XSPLOT_DPIS for p in XSPLOT_SIZES]:
        font = ft2font.FT2Font(str(path))
        font.set_size(pt, dpi)
        os2 = font.get_sfnt_table("OS/2")
        scale = pt * dpi / 72 / font.get_sfnt_table("head")["unitsPerEm"]
        def metrics_of(g):
            x0, y0, x1, y1 = g.bbox
            empty = x0 == x1 or y0 == y1
            return [g.horiAdvance / 64, 0 if empty else y0 / 64, 0 if empty else y1 / 64]
        glyphs = dict((ch, metrics_of(font.load_char(ord(ch), flags=flags))) for ch in XSPLOT_CHARS)
        kern, ligatures = {}, {}
        for a in XSPLOT_CHARS:
            for b in XSPLOT_CHARS:
                items = font._layout(a + b, flags=flags)
                if len(items) == 1:      # a ligature (DejaVu's fi, fl): one glyph for the pair
                    ligatures[a + b] = metrics_of(font.load_glyph(items[0].glyph_index, flags=flags))
                elif abs(items[1].x - glyphs[a][0]) > 1e-9:
                    kern[a + b] = items[1].x - glyphs[a][0]
        # the kerning on either side of a ligature (keyed by the two texts, the ligature's in full)
        for lig in ligatures:
            for c in XSPLOT_CHARS:
                before = font._layout(c + lig, flags=flags)
                if len(before) == 2 and abs(before[1].x - glyphs[c][0]) > 1e-9:
                    kern[c + lig] = before[1].x - glyphs[c][0]
                after = font._layout(lig + c, flags=flags)
                if len(after) == 2 and abs(after[1].x - ligatures[lig][0]) > 1e-9:
                    kern[lig + c] = after[1].x - ligatures[lig][0]
        sizes[f"{dpi}:{pt:g}"] = {"ascent": os2["sTypoAscender"] * scale, "descent": -os2["sTypoDescender"] * scale,
                                  "glyphs": glyphs, "ligatures": ligatures, "kern": kern}
    metrics = {"source": f"matplotlib {matplotlib.__version__}: DejaVu Sans set with FreeType's default hinting "
                         "(per dpi:points, each glyph's advance in whole pixels and ink bottom and top; pair kerning)",
               "sizes": sizes}
    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.layout_features = ["kern", "liga"]
    font = subset.load_font(str(path), options)
    sub = subset.Subsetter(options)
    sub.populate(unicodes=[ord(c) for c in XSPLOT_CHARS])
    sub.subset(font)
    buf = io.BytesIO()
    subset.save_font(font, buf, options)
    license_ = (path.parent / "LICENSE_DEJAVU").read_bytes()
    return metrics, {"fonts/dejavu-sans.woff2": buf.getvalue(), "fonts/dejavu-sans-license.txt": license_}


def _pdf_fonts() -> bytes | None:
    """reportlab's standard-14 font data and its single-byte codecs (``content/pdf/fonts.json``): each
    font's encoding, its 256 widths in that encoding, its face's ascent and descent and the fonts it
    falls back on; each codec as {code point: byte} over the Basic Multilingual Plane. The PDF writers
    measure and encode text with these, as reportlab's pure-Python paths do."""
    try:
        import codecs

        import reportlab
        from reportlab.pdfbase import _fontdata, pdfmetrics
    except ImportError:
        return None
    fonts = {}
    for name in _fontdata.standardFonts:
        f = pdfmetrics.getFont(name)
        fonts[name] = {"encoding": f.encName, "widths": list(f.widths), "ascent": f.face.ascent, "descent": f.face.descent,
                       "substitution": [s.fontName for s in f.substitutionFonts], "notdef": [f._notdefFont.fontName, f._notdefChar.decode("latin-1")]}
    encodings = {}
    for enc in ("WinAnsiEncoding", "SymbolEncoding", "ZapfDingbatsEncoding", "pdfdoc", "extpdfdoc"):
        codec = codecs.lookup(enc)
        table = {}
        for cp in range(0x10000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            try:
                b = codec.encode(chr(cp))[0]
            except UnicodeEncodeError:
                continue
            if len(b) == 1:
                table[str(cp)] = b[0]
        encodings[enc] = table
    doc = {"reportlab": reportlab.Version, "fonts": fonts, "encodings": encodings}
    return (json.dumps(doc, separators=(",", ":")) + "\n").encode("utf-8")


def sources() -> tuple[dict, dict]:
    """``({published path: (source label, bytes)}, versions)`` for every copy, from where it comes now;
    a source that is gone is left out."""
    out: dict = {}
    for path, rel in FROM_REPO.items():
        src = REPO / rel
        if src.is_file():
            out[path] = (rel, src.read_bytes())
    # DEEP's concise method texts live in a Python module: kept here as JSON
    concise = REPO / "apps" / "deep" / "deep" / "method_text.py"
    if concise.is_file():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_deep_method_text", concise)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out["content/deep/method-text.json"] = ("apps/deep/deep/method_text.py: CONCISE",
                                                (json.dumps(mod.CONCISE, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    easi = _easi_config()
    if easi is not None:
        out["content/easi/config.json"] = ("apps/easi/easi/config.py + watershed.py (regional criteria)", easi)
    acquisition = _easi_acquisition()
    if acquisition is not None:
        out["content/easi/acquisition.json"] = ("apps/easi/easi/method_package.py: acquisition_digest()", acquisition)
    try:
        # the template every plotly.py figure carries (EASI's cross-section plot is one)
        import plotly
        import plotly.graph_objects as go
        template = go.Figure().layout.template.to_plotly_json()
        out["content/easi/plotly-template.json"] = (f"plotly {plotly.__version__}: the default template",
                                                    (json.dumps(template, indent=1) + "\n").encode("utf-8"))
    except ImportError:
        print("plotly is not installed here: its template stays as it is")
    pdf_fonts = _pdf_fonts()
    if pdf_fonts is None:
        print("reportlab is not installed here: the PDF font tables stay as they are")
    else:
        import reportlab
        out["content/pdf/fonts.json"] = (f"reportlab {reportlab.Version}: the standard-14 fonts' widths and codecs", pdf_fonts)
    summary_text = _summaryplot_text()
    if summary_text is None:
        print("matplotlib or fontTools is not installed here: the summary plot's fonts stay as they are")
    else:
        import matplotlib
        metrics, files = summary_text
        out["content/easi/summaryplot-text.json"] = (f"matplotlib {matplotlib.__version__}: DejaVu Sans (normal, bold) text metrics "
                                                     "at 100 and 150 dpi",
                                                     (json.dumps(metrics, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        for rel, data in files.items():
            out[rel] = (f"matplotlib {matplotlib.__version__}: mpl-data/fonts/ttf ("
                        + ("DejaVu Sans Bold" if "bold" in rel else "DejaVu Sans") + ", cut to the summary plot's characters)", data)
    xsplot_text = _xsplot_text()
    if xsplot_text is None:
        print("matplotlib or fontTools is not installed here: the cross-section image's font stays as it is")
    else:
        import matplotlib
        metrics, files = xsplot_text
        out["content/easi/xsplot-text.json"] = (f"matplotlib {matplotlib.__version__}: DejaVu Sans text metrics at 100 and 130 dpi",
                                                (json.dumps(metrics, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        for rel, data in files.items():
            out[rel] = (f"matplotlib {matplotlib.__version__}: mpl-data/fonts/ttf (DejaVu Sans"
                        + (", cut to the image's characters)" if rel.endswith(".woff2") else ", LICENSE_DEJAVU)"), data)
    versions: dict = {}
    try:
        from importlib import metadata

        import shiny
        import shinywidgets
        shared = Path(shiny.__file__).parent / "www" / "shared"
        versions = {name: metadata.version(name) for name in ("shiny", "htmltools", "shinywidgets", "ipyleaflet")}
        versions["bootstrap"] = json.loads((shared / "bootstrap" / "_version.json").read_text())["bootstrap_version"]
        out["css/shiny/bootstrap.min.css"] = (f"shiny {versions['shiny']}: www/shared/bootstrap/bootstrap.min.css",
                                              (shared / "bootstrap" / "bootstrap.min.css").read_bytes())
        faces, files = [], []
        for subset, face in FACE.findall((shared / "bootstrap" / "font.css").read_text(encoding="utf-8")):
            if subset in KEEP_FONTS:
                faces.append(f"/* {subset} */\n{face}\n")
                files += [f for f in FONT_SRC.findall(face) if f not in files]
        out["css/shiny/font.css"] = (f"shiny {versions['shiny']}: www/shared/bootstrap/font.css (latin, latin-ext)",
                                     "".join(faces).encode("utf-8"))
        for rel in files:
            out[f"css/shiny/{rel}"] = (f"shiny {versions['shiny']}: www/shared/bootstrap/{rel}",
                                       (shared / "bootstrap" / rel).read_bytes())
        out["css/shiny/fill.css"] = (f"shiny {versions['shiny']}: www/shared/htmltools/fill/fill.css",
                                     (shared / "htmltools" / "fill" / "fill.css").read_bytes())
        out["css/shiny/shinywidgets.css"] = (f"shinywidgets {versions['shinywidgets']}: static/shinywidgets.css",
                                             (Path(shinywidgets.__file__).parent / "static" / "shinywidgets.css").read_bytes())
    except ImportError:
        print("shiny or shinywidgets is not installed here: the framework's copies stay as they are")
    leaflet = APP / "node_modules" / "leaflet"
    if (leaflet / "dist" / "leaflet.css").is_file():
        version = json.loads((leaflet / "package.json").read_text(encoding="utf-8"))["version"]
        versions["leaflet"] = version
        out["css/leaflet/leaflet.css"] = (f"leaflet {version}: dist/leaflet.css, without its rules for IE 8 and older",
                                          _without_old_ie((leaflet / "dist" / "leaflet.css").read_bytes()))
        for img in sorted((leaflet / "dist" / "images").iterdir()):
            out[f"css/leaflet/images/{img.name}"] = (f"leaflet {version}: dist/images/{img.name}", img.read_bytes())
    return {path: (label, _normal(path, data)) for path, (label, data) in out.items()}, versions


def main(check: bool, force: bool) -> int:
    record = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else {"files": {}}
    files = record.get("files", {})
    now, versions = sources()
    changed, edited, gone, copied = [], [], [], []
    for path in sorted(set(files) | set(now)):
        if path in OWN:
            continue
        dst = dest(path)
        raw = dst.read_bytes() if dst.is_file() else None
        mine = sha256(_normal(path, raw)) if raw is not None else None
        rec = files.get(path)
        if path not in now:
            gone.append(path)
            continue
        label, data = now[path]
        theirs = sha256(data)
        # edited here: neither its bytes nor their LF form are what was copied
        if rec and raw is not None and rec["sha256"] not in (mine, sha256(raw)):
            edited.append(path)
            if not force:
                continue
        if rec and theirs != rec["sha256"]:
            changed.append(path)
        if check or (mine == theirs and raw == data):
            if not check:
                files[path] = {"source": label, "sha256": theirs}
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        files[path] = {"source": label, "sha256": theirs}
        copied.append(path)
    for path in OWN:
        files[path] = {"source": "written here (see its header)", "sha256": sha256((PAGE / path).read_bytes())}
    if not check:
        record = {"note": "Where each file of page/ came from, with the sha256 both had when copied "
                          "(parity/copy_page.py). Once a source is gone, page/ is the source.",
                  "versions": versions or record.get("versions", {}), "files": dict(sorted(files.items()))}
        RECORD.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    for name, paths in (("copied", copied), ("source changed since the copy", changed),
                        ("edited here (kept; --force replaces)", edited), ("source gone", gone)):
        if paths:
            print(f"{name}: {', '.join(paths)}")
    if not (copied or changed or edited or gone):
        print(f"page/ matches its {len(now)} sources")
    return 1 if check and (changed or edited) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="copy nothing; report what differs")
    ap.add_argument("--force", action="store_true", help="also replace copies edited here")
    a = ap.parse_args()
    sys.exit(main(a.check, a.force))
