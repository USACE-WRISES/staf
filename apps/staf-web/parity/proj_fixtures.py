"""pyproj's answers for random EPSG:5070 points, as raw float64 files, for tests/projExact.test.ts's
large run (PROJ_FIXTURES=<out_dir>): the inverse Albers alone (radians), NAD83 degrees, and UTM in each
point's own zone and the next one east, plus the UTM step alone from pyproj's radians.

    python apps/staf-web/parity/proj_fixtures.py <out_dir> [n]
"""
import sys
from pathlib import Path

import numpy as np
import pyproj
from pyproj import Proj, Transformer

out = Path(sys.argv[1])
n = int(sys.argv[2]) if len(sys.argv) > 2 else 400_000
out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(20261011)
X = rng.uniform(-2_360_000.0, 2_260_000.0, n)
Y = rng.uniform(270_000.0, 3_170_000.0, n)
aea = Proj("+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +ellps=GRS80")
lam, phi = (np.asarray(a) for a in aea(X, Y, inverse=True, radians=True))
lon, lat = (np.asarray(a) for a in Transformer.from_crs(5070, 4269, always_xy=True).transform(X, Y))
zone = np.clip(np.floor((lon + 180.0) / 6.0).astype(np.int32) + 1, 10, 19)
zone2 = zone + 1
u = np.empty(n)
v = np.empty(n)
u2 = np.empty(n)
v2 = np.empty(n)
ub = np.empty(n)
vb = np.empty(n)
for z in np.unique(np.r_[zone, zone2]).tolist():
    t = Transformer.from_crs(5070, 26900 + z, always_xy=True)
    utm = Proj(f"+proj=utm +zone={z} +ellps=GRS80")
    m = zone == z
    if m.any():
        a, b = t.transform(X[m], Y[m])
        u[m], v[m] = a, b
        a, b = utm(lam[m], phi[m], radians=True)          # the UTM step alone, from pyproj's radians
        ub[m], vb[m] = a, b
    m2 = zone2 == z
    if m2.any():
        a, b = t.transform(X[m2], Y[m2])
        u2[m2], v2[m2] = a, b
for name, arr in dict(X=X, Y=Y, lam=lam, phi=phi, lon=lon, lat=lat, u=u, v=v, u2=u2, v2=v2, ub=ub, vb=vb).items():
    np.ascontiguousarray(arr, dtype="<f8").tofile(out / f"{name}.f64")
for name, arr in dict(zone=zone, zone2=zone2).items():
    np.ascontiguousarray(arr, dtype="<i4").tofile(out / f"{name}.i32")
print("pyproj", pyproj.__version__, "PROJ", pyproj.proj_version_str, "points", n, "zones", np.unique(zone).tolist())
