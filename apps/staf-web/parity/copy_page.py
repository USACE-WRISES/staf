"""Copy what the static app's page serves as is into apps/staf-web/page/, and record where it came from.

The static app keeps its own copy of everything its page takes from elsewhere, so it keeps working when
the Shiny STAF app (Posit Connect Cloud) and its folders are retired (the owner, 2026-10-11). ``page/`` is
Vite's public folder: served at the app's root in development and copied into ``docs/app/`` as is.

- **From the Shiny apps** (the page's look and its shared scripts, while those apps exist): each tool's
  ``www/styles.css`` (and DEEP's ``deep.css``), the shared ``staf.css`` and ``metric-rows.css`` and page
  scripts (``libs/staf_workbook/assets``), DEEP's ``coverage.js``, the shell's ``staf-shell.css`` and
  favicon (``apps/staf/www/shell``).
- **From the Python packages the Shiny app runs on** (the repo's .venv): py-shiny's Bootstrap build, its
  fonts cut to the latin and latin-ext faces (every character the apps show), htmltools' ``fill.css``,
  ``shinywidgets.css``.
- **From node_modules:** Leaflet's stylesheet and control images (Leaflet 1.9.4, the app's own
  dependency).
- **Written here by hand** and never replaced: ``css/shiny/jupyter.css`` and ``jupyter-leaflet.css``,
  the Jupyter widget rules the Shiny maps take from ipyleaflet (see their headers).

The same goes for the method content the app's code imports (``content/...``, written to
``src/app/content/``, not ``page/``): SFARI's functions, metrics and outcome mapping (``apps/sfari/data``),
DEEP's functions and outcome mapping (``apps/deep/data``) and its concise method texts (the ``CONCISE``
table of ``apps/deep/deep/method_text.py``, written as JSON).

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
}
OWN = ("css/shiny/jupyter.css", "css/shiny/jupyter-leaflet.css")
KEEP_FONTS = ("latin", "latin-ext")
FACE = re.compile(r"/\* ([a-z-]+) \*/\s*(@font-face\s*\{[^}]*\})")
FONT_SRC = re.compile(r"url\((fonts/[^)]+)\)")


#: text files are kept with LF line endings, as the repo stores them (.gitattributes), so a copy's
#: sha256 is the same in every checkout (Leaflet's stylesheet ships with CRLF)
TEXT = (".css", ".js", ".svg", ".json")


def dest(path: str) -> Path:
    """Where a copy lives: ``content/...`` beside the app's code, everything else in ``page/``."""
    return APP / "src" / "app" / path if path.startswith("content/") else PAGE / path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normal(path: str, data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n") if path.endswith(TEXT) else data


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
        out["css/leaflet/leaflet.css"] = (f"leaflet {version}: dist/leaflet.css", (leaflet / "dist" / "leaflet.css").read_bytes())
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
