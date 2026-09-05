"""Build the engine's base-flow index grid from the USGS download.

Downloads Wolock's (2003) "Base-flow index grid for the conterminous United
States" (``bfi48grd.zip``, an ArcInfo GRID, 1.8 MB), converts it to a tiled
deflate GeoTIFF in the engine's ``data/`` folder (1.3 MB, EPSG:5070, 1 km,
uint8 percent, nodata 255), and writes ``BFI_GRID_INFO.json`` beside it with
the source, citation, checksum, and the raster facts. Run once when the grid
is first adopted or if USGS ever republishes it:

    python libs/site_engine/scripts/build_bfi_grid.py

The GeoTIFF is committed and vendored with the engine, so the apps never
download at runtime.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

URL = "https://water.usgs.gov/GIS/dsdl/bfi48grd.zip"
CITATION = ("Wolock, D.M., 2003, Base-flow index grid for the conterminous United "
            "States: U.S. Geological Survey Open-File Report 03-263, "
            "https://doi.org/10.3133/ofr03263")
DATA_DIR = Path(__file__).resolve().parents[1] / "site_engine" / "data"
OUT_TIF = DATA_DIR / "bfi48grd.tif"
OUT_INFO = DATA_DIR / "BFI_GRID_INFO.json"


def main() -> int:
    import numpy as np
    import rasterio
    import requests

    print(f"downloading {URL}")
    raw = requests.get(URL, timeout=120).content
    sha = hashlib.sha256(raw).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(raw)).extractall(tmp)
        grid_dirs = [p.parent for p in Path(tmp).rglob("hdr.adf")]
        if not grid_dirs:
            print("no ArcInfo GRID (hdr.adf) in the download", file=sys.stderr)
            return 2
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with rasterio.open(grid_dirs[0]) as ds:
            arr = ds.read(1)
            nodata = int(ds.nodata) if ds.nodata is not None else 255
            valid = arr[arr != nodata]
            profile = ds.profile
            profile.update(driver="GTiff", compress="deflate", predictor=2, tiled=True,
                           blockxsize=256, blockysize=256, nodata=nodata)
            with rasterio.open(OUT_TIF, "w", **profile) as dst:
                dst.write(arr, 1)
            info = {
                "source": URL,
                "citation": CITATION,
                "downloaded": _dt.date.today().isoformat(),
                "zipSha256": sha,
                "geotiffSha256": hashlib.sha256(OUT_TIF.read_bytes()).hexdigest(),
                "crs": str(ds.crs),
                "resolutionM": [float(ds.res[0]), float(ds.res[1])],
                "shape": [int(ds.height), int(ds.width)],
                "dtype": str(ds.dtypes[0]),
                "nodata": nodata,
                "units": "percent of streamflow that is base flow",
                "valueRange": [int(valid.min()), int(valid.max())],
                "validCells": int(valid.size),
                "meanOfValidCells": round(float(np.mean(valid)), 2),
            }
    OUT_INFO.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_TIF} ({OUT_TIF.stat().st_size:,} bytes) and {OUT_INFO.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
