"""The method code the grid runs: the site engine's byte-synced copies of EASI's cross-section
modules (``libs/site_engine/site_engine/_extracted``), the copies SFARI and DEEP vendor."""
from __future__ import annotations

import sys

from . import config

_ENGINE = str(config.REPO / "libs" / "site_engine")
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from site_engine._extracted import bieger, dem_tiles, geomorph  # noqa: E402

__all__ = ["bieger", "dem_tiles", "geomorph", "bankfull"]


def bankfull(da_sqkm: float, abbr) -> tuple:
    """``bieger.bankfull_geometry`` for a division already looked up: ``(width_m, depth_m,
    area_m2, division_name, extrapolated)``, the same numbers by the same arithmetic."""
    da = max(float(da_sqkm or 0.0), 0.01)
    key = abbr if abbr in bieger.COEF else "USA"
    c = bieger.COEF[key]
    lo, hi = bieger.DA_FIT_RANGE_SQKM.get(key, bieger.DA_FIT_RANGE_SQKM["USA"])
    return (round(bieger._power(c["width"], da), 2), round(bieger._power(c["depth"], da), 3),
            round(bieger._power(c["area"], da), 2), bieger.DIV_NAME.get(key, "National curve"),
            not (lo <= da <= hi))
