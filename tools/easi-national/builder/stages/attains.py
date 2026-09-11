"""EPA ATTAINS assessment units intersecting a chunk's bbox (the three
assessed layers the app queries), paged by offset with the ESRI JSON geometry
kept verbatim so the join can call the app's own distance code.

The service is moody about page size (a 1,000-feature page with geometry
answers a server-side error, and so do some 500-feature pages at higher
offsets), so each layer's count is read first, a failing page is retried at
half the size, and the stage refuses to finish short of the count.
"""
from __future__ import annotations

import json

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk
from . import common

STAGE = "attains"
SOURCE = "attains"
LAYERS = (0, 1, 2)
MIN_PAGE = 25


def _schema():
    import pyarrow as pa
    return pa.schema([
        ("layer", pa.int8()), ("assessment_unit", pa.string()),
        ("assessment_name", pa.string()), ("overallstatus", pa.string()),
        ("isimpaired", pa.string()), ("ircategory", pa.string()),
        ("geometry", pa.string()), ("minx", pa.float64()), ("miny", pa.float64()),
        ("maxx", pa.float64()), ("maxy", pa.float64())])


def _params(bbox: list[float]) -> dict:
    return {"where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in bbox),
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "f": "json"}


def fetch_count(layer: int, bbox: list[float]) -> int:
    data = http.get_json(f"{config.ATTAINS_BASE}/{layer}/query",
                         {**_params(bbox), "returnCountOnly": "true"})
    if data.get("error"):
        raise RuntimeError(f"ATTAINS layer {layer} count: {data['error']}")
    return int(data.get("count") or 0)


def fetch_page(layer: int, bbox: list[float], offset: int, count: int = config.ATTAINS_PAGE) -> dict:
    """One page; raises when the service answers an error document."""
    from easi.datasources import attains as app_attains
    params = {**_params(bbox), "outSR": "4326", "outFields": app_attains._FIELDS,
              "returnGeometry": "true", "resultOffset": offset, "resultRecordCount": count,
              "orderByFields": "OBJECTID"}
    data = http.get_json(f"{config.ATTAINS_BASE}/{layer}/query", params)
    if data.get("error"):
        raise RuntimeError(f"ATTAINS layer {layer} offset {offset} x{count}: {data['error']}")
    return data


def run_attains(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
                control: Control, *, force: bool = False, fetch=fetch_page,
                count=fetch_count) -> None:
    if not chunk.bbox:
        raise RuntimeError("the geometry stage must run first (no chunk bbox)")
    bbox = common.buffer_bbox(chunk.bbox, config.ATTAINS_BUFFER_M / 1609.344)
    inputs = common.chunk_inputs(STAGE, chunk, [round(v, 3) for v in bbox], LAYERS, 2)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        import pyarrow as pa
        from .local_gdb import attains_for
        local = attains_for(root, bbox)
        if local is not None:
            progress.begin(chunk.id, STAGE, total=1,
                           message="ATTAINS assessment units from the national geodatabase")
            seen_local: set = set()
            kept = []
            for row in local:
                ident = (row["layer"], row["assessment_unit"],
                         (row["minx"], row["miny"], row["maxx"], row["maxy"]))
                if ident in seen_local:
                    continue
                seen_local.add(ident)
                kept.append(row)
            common.write_parquet(pa.Table.from_pylist(kept, schema=_schema()), out)
            progress.tick(done=1)
            units_local = len({r["assessment_unit"] for r in kept})
            progress.say(f"attains.parquet: {len(kept):,} segments of {units_local:,} assessment units "
                         "from the national geodatabase")
            return
        parts = common.parts_dir(root, chunk.id, SOURCE)
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        # parts already on disk, keyed l<layer>-o<offset>, each recording how many
        # features it holds so a resume walks the same offsets
        have: dict[int, dict[int, int]] = {layer: {} for layer in LAYERS}
        for key, payload in common.read_parts(parts):
            try:
                layer, offset = int(key[1]), int(key.split("-o")[1])
            except (ValueError, IndexError):
                continue
            have[layer][offset] = len(payload.get("features") or [])
        totals = {layer: count(layer, bbox) for layer in LAYERS}
        progress.begin(chunk.id, STAGE, total=sum(totals.values()),
                       message="ATTAINS assessment units: " +
                       ", ".join(f"layer {l} {n:,}" for l, n in totals.items()))
        fetched = 0
        for layer in LAYERS:
            offset, page_size = 0, config.ATTAINS_PAGE
            while offset < totals[layer]:
                if offset in have[layer]:
                    n = have[layer][offset]
                    if n <= 0:
                        break
                    offset += n
                    fetched += n
                    progress.tick(done=fetched)
                    continue
                control.check()
                progress.tick(done=fetched, message=f"ATTAINS layer {layer}: fetching from "
                                                    f"{offset:,} of {totals[layer]:,}")
                try:
                    payload = fetch(layer, bbox, offset, page_size)
                except Exception as exc:  # noqa: BLE001 - shrink the page and retry
                    if page_size <= MIN_PAGE:
                        raise RuntimeError(f"ATTAINS layer {layer} keeps failing at offset "
                                           f"{offset}: {exc}") from exc
                    page_size //= 2
                    progress.say(f"ATTAINS layer {layer} offset {offset}: retrying with {page_size}")
                    continue
                n = len(payload.get("features") or [])
                if n == 0:
                    break
                key = f"l{layer}-o{offset:07d}"
                common.write_part(parts, key, payload)
                ledger.add(key, n=n)
                have[layer][offset] = n
                offset += n
                fetched += n
                progress.tick(done=fetched, message=f"ATTAINS layer {layer}: {offset:,} of {totals[layer]:,}")
        expected = sum(totals.values())
        if fetched < 0.98 * expected:
            raise RuntimeError(f"ATTAINS: fetched {fetched:,} of {expected:,} features; rerun")
        rows: list[dict] = []
        seen: set = set()
        for key, payload in common.read_parts(parts):
            layer = int(key[1])
            for feature in payload.get("features") or []:
                attrs = feature.get("attributes") or {}
                geometry = feature.get("geometry") or {}
                bounds = common.esri_bounds(geometry)
                if bounds is None:
                    continue
                # an assessment unit is many segments; keep every one (the join
                # measures the distance to the nearest), drop only exact repeats
                ident = (layer, attrs.get("assessmentunitidentifier"), bounds)
                if ident in seen:
                    continue
                seen.add(ident)
                rows.append({"layer": layer,
                             "assessment_unit": attrs.get("assessmentunitidentifier"),
                             "assessment_name": attrs.get("assessmentunitname"),
                             "overallstatus": attrs.get("overallstatus"),
                             "isimpaired": attrs.get("isimpaired"),
                             "ircategory": attrs.get("ircategory"),
                             "geometry": json.dumps(geometry, separators=(",", ":")),
                             "minx": bounds[0], "miny": bounds[1],
                             "maxx": bounds[2], "maxy": bounds[3]})
        table = pa.Table.from_pylist(rows, schema=_schema())
        common.write_parquet(table, out)
        units = len({r["assessment_unit"] for r in rows})
        progress.say(f"attains.parquet: {len(rows):,} segments of {units:,} assessment units "
                     f"({fetched:,} of {expected:,} features fetched)")
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
