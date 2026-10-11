"""Reference values for the web app's unit tests, from the Python the TypeScript ports.

Writes ``apps/staf-web/tests/fixtures/xs_ref.json``:
- ``linspace``: ``numpy.linspace(-wide, wide, n)`` as float64 bit patterns (hex);
- ``codec``: real archive transects' int32 codes and their decoded float32 bit patterns;
- ``proj``: EPSG:5070 points and pyproj's EPSG:26915 / EPSG:4269 coordinates, and lon/lat points in EPSG:5070;
- ``place``: a flowline from the bundle and the archive's placement of its sections.

    python apps/staf-web/parity/make_fixtures.py
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "xs-grid"))
from xsgrid import codec, config, sections  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "xs_ref.json"


def hex64(values) -> list:
    return [struct.pack(">d", float(v)).hex() for v in values]


def main() -> int:
    from pyproj import Transformer
    ref: dict = {"linspace": [], "codec": [], "proj": {}, "place": []}
    for wide, n in ((250.0, 501), (319.76, 640), (800.0, 2001), (250.0, 51)):
        ref["linspace"].append({"wide": wide, "n": n, "hex": hex64(np.linspace(-wide, wide, n))})
    archive = pq.ParquetFile(config.ARCHIVE / "archive" / "xs_0710.parquet")
    rows = archive.read_row_group(0, columns=["nhdplusid", "k", "z"]).slice(0, 4).to_pylist()
    for r in rows:
        codes = np.asarray(r["z"], dtype=np.int32)
        z = codec.decode(codes)
        ref["codec"].append({"codes": codes.tolist(), "bits": z.view(np.uint32).tolist()})
    rng = np.random.default_rng(7)
    x = rng.uniform(-600_000, 200_000, 200)
    y = rng.uniform(1_800_000, 2_900_000, 200)
    u, v = Transformer.from_crs(5070, 26915, always_xy=True).transform(x, y)
    lon, lat = Transformer.from_crs(5070, 4269, always_xy=True).transform(x, y)
    glon = rng.uniform(-124, -67, 200)
    glat = rng.uniform(25, 49, 200)
    gx, gy = Transformer.from_crs(4326, 5070, always_xy=True).transform(glon, glat)
    ref["proj"] = {"x": x.tolist(), "y": y.tolist(), "utm15": [list(u), list(v)], "nad83": [list(lon), list(lat)],
                   "lonlat": [glon.tolist(), glat.tolist()], "albers": [list(gx), list(gy)]}
    lines = sections.flowlines("0710")
    placed = sections.place(lines.slice(0, 40))
    for i in range(3):
        line = lines.slice(i, 1).to_pylist()[0]
        nid = line["nhdplusid"]
        keep = placed["nhdplusid"] == nid
        ref["place"].append({"nhdplusid": str(nid), "x": line["x"], "y": line["y"], "parts": line["parts"],
                             "sections": [{"k": int(k), "s": float(s), "x": float(a), "y": float(b), "nx": float(c), "ny": float(d)}
                                          for k, s, a, b, c, d in zip(placed["k"][keep], placed["s_m"][keep], placed["x"][keep],
                                                                      placed["y"][keep], placed["nx"][keep], placed["ny"][keep])]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ref), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} KB)")
    lzw_fixture(OUT.parent)
    exact_fixture(OUT.parent)
    return 0


def exact_fixture(folder: Path) -> None:
    """``proj_ref.json``: zlib's CRC-32 of a profile's codes (the release's ``z_crc``) and of the standard
    check string, and pyproj's projections of 300 EPSG:5070 points to NAD83 and to their own UTM zone
    (projExact.ts lands within the pull's slack of them; parity/proj_fixtures.py makes the large set)."""
    import zlib

    from pyproj import Transformer
    z = (np.cumsum(np.random.default_rng(3).normal(0, 0.05, 501)) + 312.25).astype(np.float32)
    z[[7, 8]] = np.nan
    crc = zlib.crc32(np.ascontiguousarray(codec.encode(z), dtype="<i4").tobytes())
    rng = np.random.default_rng(11)
    x = rng.uniform(-2_300_000, 2_200_000, 300)
    y = rng.uniform(300_000, 3_100_000, 300)
    lon, lat = Transformer.from_crs(5070, 4269, always_xy=True).transform(x, y)
    zone = np.clip(np.floor((np.asarray(lon) + 180) / 6).astype(int) + 1, 10, 19)
    u, v = [], []
    for i in range(len(x)):
        a, b = Transformer.from_crs(5070, 26900 + int(zone[i]), always_xy=True).transform(x[i], y[i])
        u.append(a)
        v.append(b)
    out = dict(profile=[None if np.isnan(t) else float(t) for t in z], profile_crc=crc,
               check_123456789=zlib.crc32(b"123456789"), x=x.tolist(), y=y.tolist(), lon=list(lon), lat=list(lat),
               zone=zone.tolist(), u=u, v=v)
    path = folder / "proj_ref.json"
    with open(path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {path.name} ({path.stat().st_size / 1e3:.0f} KB)")


def lzw_fixture(folder: Path) -> None:
    """A small GeoTIFF the way 3DEP writes its tiles (LZW, floating-point predictor, tiled), with
    partial edge blocks and blocks that fill the LZW table, and GDAL's decoded pixels as a hash."""
    import rasterio
    from rasterio.transform import from_origin
    rng = np.random.default_rng(3)
    w, h = 300, 200
    z = (np.cumsum(rng.normal(0, 0.02, (h, w)), axis=1) + 250 + np.linspace(0, 3, h)[:, None]).astype(np.float32)
    z[5, 7] = -999999.0
    path = folder / "lzw_pred3.tif"
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:26915",
                       transform=from_origin(500000.0, 4600000.0, 1.0, 1.0), compress="lzw", predictor=3,
                       tiled=True, blockxsize=128, blockysize=128, nodata=-999999.0) as ds:
        ds.write(z, 1)
    with rasterio.open(path) as ds:
        back = ds.read(1).astype(np.float32)
    bits = back.ravel().view(np.uint32)
    hsh = 0
    for v in bits.tolist():
        hsh = (hsh * 31 + v) & 0xFFFFFFFF
    (folder / "lzw_ref.json").write_text(json.dumps({"width": w, "height": h, "hash": hsh,
                                                     "first": bits[:4].tolist(), "last": bits[-4:].tolist()}),
                                         encoding="utf-8", newline="\n")
    print(f"wrote {path.name} ({path.stat().st_size / 1e3:.0f} KB) and lzw_ref.json")


if __name__ == "__main__":
    sys.exit(main())
