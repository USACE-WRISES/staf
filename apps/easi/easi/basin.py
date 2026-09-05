"""Basin characteristics for the report (StreamStats-style).

Assembles a small, ordered set of basin/reach characteristics from data already
computed during delineation + the shared prefetch — no new network calls. Returns
only the rows that have data, so missing optional fields never blank the section.
"""
from __future__ import annotations


def fmt_km2(value) -> str:
    """``12.35 km²`` from a number, ``unknown`` from None or junk. Two
    decimals: the HR drainage area arrives with eight (0.98719999) and the
    engine area with four, and neither belongs on a card as served."""
    try:
        return f"{float(value):,.2f} km²"
    except (TypeError, ValueError):
        return "unknown"


def fmt_ft(value) -> str:
    """``1,000 ft`` from a number, ``unknown`` from None or junk."""
    try:
        return f"{float(value):,.0f} ft"
    except (TypeError, ValueError):
        return "unknown"


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
            # one area row only: the engine's polygon area and the HR drainage
            # area agree at two decimals and read as a duplicate (2026-09-04);
            # the CSV keeps "Exact watershed area (km2)" as the record
            rows.append(["Watershed engine", str(layer.get("label") or "STAF site engine")])
        elif provider is None:
            rows.append(["Watershed engine",
                         f"unavailable ({layer.get('unavailableReason') or 'not calculated'})"])
        else:
            rows.append(["Watershed engine",
                         "StreamCat lookup engine (nearest covered reach)"])

    da = getattr(ctx, "drainage_area_sqkm", None)
    if da is not None:
        rows.append(["Drainage area", fmt_km2(da)])
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
