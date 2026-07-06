"""One command to (re)build the EASI V&V documentation.

This is the single entry point for updating the report. It regenerates the figures
and validation tables, then renders the Quarto document straight to the app's
published file at ``www/documentation.html`` (no manual copy).

Run it with the project virtual environment so the data/figure steps have their
dependencies, for example:

    .venv/Scripts/python.exe scripts/build_docs.py            (Windows)
    .venv/bin/python scripts/build_docs.py                    (macOS / Linux)

Options:
    (no flag)      Rebuild figures + tables from the cached EASI runs, then render.
                   Use this for prose edits, figure restyles, and SFARI value edits
                   (SFARI Summary.xlsx is read fresh on every build). Fast, no network.
    --site <ID>    Re-run EASI for one site (e.g. MB, CC, MC) before rebuilding.
                   Use after changing a site's coordinates or its overrides. Network.
    --all          Re-run EASI for every site before rebuilding. Network, ~10 min.
    --no-render    Rebuild figures + tables only, skip the Quarto render.

Requires Quarto on PATH (https://quarto.org). See docs/EASI_Documentation/README.md.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = os.path.join(ROOT, "docs", "EASI_Documentation")
RUN_SITES = os.path.join(HERE, "run_sfari_sites.py")
BUILD_ASSETS = os.path.join(HERE, "build_doc_assets.py")
OUTPUT = os.path.join(ROOT, "www", "documentation.html")


def _py(step_args, desc):
    print(f"\n==> {desc}", flush=True)
    if subprocess.run([sys.executable, *step_args], cwd=ROOT).returncode != 0:
        sys.exit(f"FAILED: {desc}")


def main():
    ap = argparse.ArgumentParser(
        description="Rebuild and render the EASI V&V documentation.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--site", metavar="ID",
                   help="re-run EASI for one site id (e.g. MB) before building")
    g.add_argument("--all", action="store_true",
                   help="re-run EASI for all sites before building")
    ap.add_argument("--no-render", action="store_true",
                    help="rebuild figures and tables only, skip the Quarto render")
    args = ap.parse_args()

    # 1. Optionally refresh the (slow, networked) EASI runs.
    if args.all:
        _py([RUN_SITES, "--force"], "Re-running EASI at all SFARI sites (network)")
    elif args.site:
        _py([RUN_SITES, "--only", args.site, "--force"],
            f"Re-running EASI at site {args.site} (network)")

    # 2. Rebuild figures + validation tables (reads cached EASI + the xlsx).
    _py([BUILD_ASSETS], "Rebuilding figures and validation tables")

    # 3. Render the document to www/documentation.html (via _quarto.yml).
    if args.no_render:
        print("\nAssets rebuilt. Skipped render (--no-render).")
        return
    quarto = shutil.which("quarto")
    if not quarto:
        sys.exit("Quarto was not found on PATH. Install it from https://quarto.org, "
                 "then re-run, or render manually with `quarto render` inside "
                 "docs/EASI_Documentation/.")
    print("\n==> Rendering documentation with Quarto", flush=True)
    if subprocess.run([quarto, "render"], cwd=DOC).returncode != 0:
        sys.exit("FAILED: quarto render")

    print(f"\nDone. Wrote {OUTPUT}")
    print("Open the app and click Documentation, or open that file directly.")


if __name__ == "__main__":
    main()
