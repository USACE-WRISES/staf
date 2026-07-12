"""Basin characteristics for the report (StreamStats-style).

Assembles a small, ordered set of basin/reach characteristics from data already
computed during delineation + the shared prefetch — no new network calls. Returns
only the rows that have data, so missing optional fields never blank the section.
"""
from __future__ import annotations


def basin_characteristics(ctx) -> dict:
    """Ordered ``{"rows": [[label, value], ...]}`` from existing ``ctx`` data.

    Reads the AnalysisContext attributes (drainage area, slope, stream order,
    sinuosity) and ``ctx.extras`` (StreamCat climate normals). Bankfull/ER/BHR live
    in the report's cross-section table, not here. JSON-safe (values are strings).
    """
    extras = getattr(ctx, "extras", None) or {}
    sc = extras.get("streamcat") or {}
    rows: list[list[str]] = []

    da = getattr(ctx, "drainage_area_sqkm", None)
    if da is not None:
        rows.append(["Drainage area", f"{round(da, 2)} km²"])
    slope = getattr(ctx, "slope", None)
    if slope is not None:
        rows.append(["Channel slope", f"{slope:.4f} m/m ({slope * 100:.2f}%)"])
    so = getattr(ctx, "stream_order", None)
    if so is not None:
        rows.append(["Stream order", str(so)])
    sin = getattr(ctx, "sinuosity", None)
    if sin is not None:
        rows.append(["Sinuosity", f"{sin}"])

    # (Bankfull width/depth, entrenchment ratio, and bank-height ratio live in the
    # report's editable cross-section geometry table, not here.)

    # climate normals (only shown when present in the StreamCat pull). Mean annual air
    # temp was dropped from the report as not needed for screening.
    elev = sc.get("elevws")
    if elev is not None:
        rows.append(["Mean basin elevation", f"{elev:.0f} m"])
    precip = sc.get("precip8110ws")
    if precip is not None:
        rows.append(["Mean annual precipitation", f"{precip:.0f} mm"])

    return {"rows": rows}
