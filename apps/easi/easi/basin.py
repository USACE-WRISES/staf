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
    anchor = extras.get("siteAnchor") or {}
    routed = anchor.get("anchorKind") == "hrSurrogate"
    layer = extras.get("watershed") or {}
    # The engine answering the watershed metrics is a site characteristic on
    # routed sites (covered runs stay the StreamCat lookup engine and add no row).
    if routed:
        provider = layer.get("provider")
        if provider == "site-engine":
            meta = layer.get("meta") or {}
            rows.append(["Watershed engine", str(layer.get("label") or "STAF site engine")])
            if meta.get("areaSqkm") is not None:
                rows.append(["Exact watershed area", f"{round(float(meta['areaSqkm']), 2)} km²"])
        elif provider is None:
            rows.append(["Watershed engine",
                         f"unavailable ({layer.get('unavailableReason') or 'not calculated'})"])
        else:
            rows.append(["Watershed engine",
                         "StreamCat lookup engine (nearest covered reach)"])

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

    # EPA Level III ecoregion (bundled polygons, no network) — a location descriptor that helps
    # interpret land-cover metrics (e.g. the natural riparian buffer is non-forest in grassland
    # and arid ecoregions, so the detrital CPOM proxy counts grass/shrub there too).
    try:
        from . import geo
        eco = geo.level3_at(getattr(ctx, "lat", None), getattr(ctx, "lon", None))
    except Exception:  # noqa: BLE001 - resilience by design
        eco = None
    if eco and eco.get("name"):
        code = eco.get("code")
        rows.append(["EPA ecoregion (Level III)",
                     f"{eco['name']} ({code})" if code else eco["name"]])

    # (Bankfull width/depth, entrenchment ratio, and bank-height ratio live in the
    # report's editable cross-section geometry table, not here.)

    # climate normals (only shown when present in the StreamCat pull). Mean annual air
    # temp was dropped from the report as not needed for screening.
    suffix = " (nearest covered reach)" if routed else ""
    elev = sc.get("elevws")
    if elev is not None:
        rows.append(["Mean basin elevation" + suffix, f"{elev:.0f} m"])
    precip = sc.get("precip8110ws")
    if precip is not None:
        rows.append(["Mean annual precipitation" + suffix, f"{precip:.0f} mm"])

    return {"rows": rows}
