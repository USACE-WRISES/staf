"""One stream network on the map, colored by the engine that answers a click.

Until 2026-09-03 the map drew the NHDPlus V2 network and the full NHDPlus HR
network as two layers at the same weight, so every covered stream showed as
two offset lines (V2 is 1:100k, HR is 1:24k). Now the HR geometry is drawn
once and split by the click rule: a stretch within the snap tolerance of a
V2 reach is scored by the StreamCat lookup engine and draws dark blue, the
rest is answered by the STAF site engine and draws cyan. V2 geometry farther
than the tolerance from any HR line still draws dark blue, so nothing
clickable disappears. The distance is the click rule's own (planar EPSG:5070
in feet, see ``flowlines.nearest_point_on_lines``). Pure functions, no
Shiny; ``fetch_streams`` takes injectable fetchers so the split is tested
offline.
"""
from __future__ import annotations

import functools
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import substring
from shapely.strtree import STRtree

from .datasources.flowlines import CRS_ALBERS, CRS_WGS84, FT_PER_M

#: Sample spacing along a line, one third of the snap tolerance: a color
#: boundary lands within half a step (25 ft) of the true crossing.
SAMPLE_FT = 50.0
#: Feature property carrying the classification: "v2" (HR stretch within the
#: tolerance of a V2 reach, or raw V2 when HR is unavailable), "hr" (answered
#: by the STAF site engine), "v2-orphan" (V2 geometry with no HR line nearby).
COVER_PROP = "cover"
#: Above this many HR samples the spacing doubles (still a 50 ft error).
MAX_SAMPLES = 150_000
_MIN_PIECE_M = 1.0

_TO_ALBERS = Transformer.from_crs(CRS_WGS84, CRS_ALBERS, always_xy=True)
_TO_WGS84 = Transformer.from_crs(CRS_ALBERS, CRS_WGS84, always_xy=True)

Fetcher = Callable[[float, float, float, float], Optional[dict]]


def _empty() -> dict:
    return {"type": "FeatureCollection", "features": []}


def _parts(fc: Optional[dict]) -> list[tuple[dict, list, LineString]]:
    """``(props, coordinates, LineString)`` per line part of ``fc``.

    Multipart lines are exploded, each part keeping the parent properties.
    The coordinates are the input values (exact, 2-D) so a part that needs no
    cut is emitted untouched."""
    out = []
    for feat in (fc or {}).get("features") or []:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        coords = geom.get("coordinates")
        gtype = geom.get("type")
        if not coords:
            continue
        if gtype == "LineString":
            parts = [coords]
        elif gtype == "MultiLineString":
            parts = coords
        else:
            continue
        for part in parts:
            if len(part) < 2:
                continue
            xy = [[float(p[0]), float(p[1])] for p in part]
            out.append((props, xy, LineString(xy)))
    return out


def _project(lines: list, transformer: Transformer) -> np.ndarray:
    if not len(lines):
        return np.array([], dtype=object)

    def fn(xy):
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])
    return shapely.transform(np.array(list(lines), dtype=object), fn)


def _sample_distances(line_m: LineString, spacing_m: float) -> np.ndarray:
    """Distances along ``line_m`` to sample: a regular grid plus every vertex,
    so both ends, every bend, and at least one interior point are covered."""
    length = line_m.length
    n = max(3, math.ceil(length / spacing_m) + 1)
    grid = np.linspace(0.0, length, n)
    coords = np.asarray(line_m.coords)
    seg = np.hypot(np.diff(coords[:, 0]), np.diff(coords[:, 1]))
    verts = np.concatenate([[0.0], np.cumsum(seg)])
    return np.unique(np.concatenate([grid, verts]))


def _samples(lines_m: np.ndarray, spacing_m: float):
    """``(points, owner, dists)`` over every line, doubling the spacing while
    the sample count exceeds ``MAX_SAMPLES``."""
    while True:
        dists = [_sample_distances(ln, spacing_m) for ln in lines_m]
        total = sum(len(d) for d in dists)
        if total <= MAX_SAMPLES or spacing_m >= 8 * SAMPLE_FT / FT_PER_M:
            break
        spacing_m *= 2.0
    pts = [shapely.line_interpolate_point(ln, d) for ln, d in zip(lines_m, dists)]
    points = np.concatenate(pts) if pts else np.array([], dtype=object)
    owner = (np.concatenate([np.full(len(d), i) for i, d in enumerate(dists)])
             if dists else np.array([], dtype=int))
    return points, owner, dists


def _within(points: np.ndarray, tree: Optional[STRtree], tol_m: float) -> np.ndarray:
    mask = np.zeros(len(points), dtype=bool)
    if tree is None or len(points) == 0:
        return mask
    pairs = tree.query(points, predicate="dwithin", distance=tol_m)
    if pairs.size:
        mask[np.unique(pairs[0])] = True
    return mask


def _runs(dists: np.ndarray, mask: np.ndarray) -> list[tuple[float, float, bool]]:
    """``(start, end, state)`` runs along the line; cuts sit midway between
    adjacent samples that differ."""
    runs = []
    start = 0.0
    state = bool(mask[0])
    for i in range(1, len(dists)):
        if bool(mask[i]) != state:
            cut = 0.5 * (float(dists[i - 1]) + float(dists[i]))
            runs.append((start, cut, state))
            start, state = cut, bool(mask[i])
    runs.append((start, float(dists[-1]), state))
    return runs


def _feature(props: dict, coords: list, cover: str) -> dict:
    return {"type": "Feature", "properties": {**props, COVER_PROP: cover},
            "geometry": {"type": "LineString", "coordinates": coords}}


def _split(parts, lines_m, tree, tol_m, spacing_m, keep_state: bool,
           cover_when_kept: str, cover_when_other: Optional[str]):
    """Classify ``parts`` against ``tree``. A part whose samples agree passes
    through with its input coordinates. A part with transitions is cut into
    pieces, which are back-projected in one call.

    ``keep_state`` is the mask value that maps to ``cover_when_kept``; the
    other state maps to ``cover_when_other`` or is dropped when that is None.
    Returns ``(kept_features, other_features)`` in input order."""
    kept: list = []
    other: list = []
    if not parts:
        return kept, other
    points, owner, dists = _samples(lines_m, spacing_m)
    mask = _within(points, tree, tol_m)
    pending: list[tuple[int, tuple, str]] = []      # (part index, (a, b), cover)
    slots: dict[int, list] = {}                      # part index -> its features, in order
    for i, (props, coords, _line) in enumerate(parts):
        m = mask[owner == i]
        d = dists[i]
        slots[i] = []
        if m.all() or not m.any():
            state = bool(m[0]) if len(m) else False
            cover = cover_when_kept if state == keep_state else cover_when_other
            if cover is not None:
                slots[i].append(_feature(props, coords, cover))
            continue
        for a, b, state in _runs(d, m):
            if b - a < _MIN_PIECE_M:
                continue
            cover = cover_when_kept if state == keep_state else cover_when_other
            if cover is not None:
                pending.append((i, (a, b), cover))
    if pending:
        pieces = [substring(lines_m[i], a, b) for i, (a, b), _c in pending]
        back = _project(pieces, _TO_WGS84)
        for (i, _ab, cover), geom in zip(pending, back):
            if geom.is_empty or geom.geom_type != "LineString":
                continue
            coords = [[round(float(x), 6), round(float(y), 6)] for x, y in geom.coords]
            slots[i].append(_feature(parts[i][0], coords, cover))
    for i in range(len(parts)):
        for feat in slots.get(i, []):
            (kept if feat["properties"][COVER_PROP] == cover_when_kept else other).append(feat)
    return kept, other


def split_by_coverage(hr_fc: Optional[dict], v2_fc: Optional[dict], tol_ft: float = 150.0,
                      spacing_ft: float = SAMPLE_FT) -> tuple[dict, dict]:
    """``(covered_fc, uncovered_fc)``: the HR geometry split by whether a
    stretch lies within ``tol_ft`` of a V2 reach (the click rule), plus the
    V2 stretches with no HR line within ``tol_ft`` (drawn covered so they stay
    clickable). Both are EPSG:4326 FeatureCollections, possibly empty."""
    hr = _parts(hr_fc)
    v2 = _parts(v2_fc)
    covered, uncovered = _empty(), _empty()
    if not hr and not v2:
        return covered, uncovered
    tol_m = tol_ft / FT_PER_M
    spacing_m = spacing_ft / FT_PER_M
    hr_m = _project([p[2] for p in hr], _TO_ALBERS)
    v2_m = _project([p[2] for p in v2], _TO_ALBERS)
    hr_tree = STRtree(hr_m) if len(hr_m) else None
    v2_tree = STRtree(v2_m) if len(v2_m) else None
    # HR pass: within tolerance of a V2 reach -> "v2", else "hr".
    hr_cov, hr_unc = _split(hr, hr_m, v2_tree, tol_m, spacing_m, True, "v2", "hr")
    # V2 pass: stretches with no HR line nearby are orphans; the rest is
    # already drawn by the covered HR pieces.
    orphans, _shadowed = _split(v2, v2_m, hr_tree, tol_m, spacing_m, False, "v2-orphan", None)
    covered["features"] = hr_cov + orphans
    uncovered["features"] = hr_unc
    return covered, uncovered


def _tagged(fc: dict, cover: str) -> dict:
    feats = []
    for feat in fc.get("features") or []:
        props = feat.get("properties") or {}
        feats.append({"type": "Feature", "properties": {**props, COVER_PROP: cover},
                      "geometry": feat.get("geometry")})
    return {"type": "FeatureCollection", "features": feats}


def build_display(v2_fc: Optional[dict], hr_fc: Optional[dict], tol_ft: float = 150.0) -> dict:
    """``{"mode", "covered", "uncovered"}`` for the two map layers.

    ``mode`` is ``segmented`` (both networks present), ``v2-only`` (the HR
    fetch failed or hit its record cap, the raw V2 lines draw covered),
    ``hr-only`` (no V2 lines, everything draws cyan) or ``empty``."""
    has_v2 = bool(v2_fc and v2_fc.get("features"))
    has_hr = bool(hr_fc and hr_fc.get("features"))
    if has_v2 and has_hr:
        covered, uncovered = split_by_coverage(hr_fc, v2_fc, tol_ft)
        return {"mode": "segmented", "covered": covered, "uncovered": uncovered}
    if has_v2:
        return {"mode": "v2-only", "covered": _tagged(v2_fc, "v2"), "uncovered": _empty()}
    if has_hr:
        return {"mode": "hr-only", "covered": _empty(), "uncovered": _tagged(hr_fc, "hr")}
    return {"mode": "empty", "covered": _empty(), "uncovered": _empty()}


def _fetch_pair(bbox: tuple, fetch_v2: Fetcher, fetch_hr: Fetcher):
    """Both network fetches side by side (they do not depend on each other)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        v2_future = pool.submit(fetch_v2, *bbox)
        hr_future = pool.submit(fetch_hr, *bbox)
        return v2_future.result(), hr_future.result()


def _assemble(bbox: tuple, v2_fc: Optional[dict], hr_fc: Optional[dict], tol_ft: float) -> dict:
    disp = build_display(v2_fc, hr_fc, tol_ft)
    return {"bbox": tuple(bbox),
            "v2": v2_fc if v2_fc and v2_fc.get("features") else None,
            "hr": hr_fc if hr_fc and hr_fc.get("features") else None, **disp}


@functools.lru_cache(maxsize=32)
def _default_display(west: float, south: float, east: float, north: float,
                     tol_ft: float) -> dict:
    from .datasources import flowlines, nhd_hr
    bbox = (west, south, east, north)
    v2_fc, hr_fc = _fetch_pair(bbox, flowlines.flowlines_in_bbox, nhd_hr.hr_flowlines_in_bbox)
    return _assemble(bbox, v2_fc, hr_fc, tol_ft)


def fetch_streams(bbox: tuple, *, tol_ft: float = 150.0, fetch_v2: Optional[Fetcher] = None,
                  fetch_hr: Optional[Fetcher] = None) -> dict:
    """The map's stream layers for ``bbox`` (west, south, east, north).

    Returns ``{"bbox", "v2", "hr", "mode", "covered", "uncovered"}`` where
    ``v2`` and ``hr`` are the raw FeatureCollections (or None) the click rule
    keeps using. With the default fetchers the result is cached on the bbox,
    so a pan back into a fetched box never re-splits; injected fetchers
    (tests) bypass the cache."""
    bbox = tuple(float(b) for b in bbox)
    if fetch_v2 is None and fetch_hr is None:
        return _default_display(*bbox, float(tol_ft))
    from .datasources import flowlines, nhd_hr
    v2_fc, hr_fc = _fetch_pair(bbox, fetch_v2 or flowlines.flowlines_in_bbox,
                               fetch_hr or nhd_hr.hr_flowlines_in_bbox)
    return _assemble(bbox, v2_fc, hr_fc, tol_ft)


def feature_by_id(fc: Optional[dict], prop: str, value) -> Optional[dict]:
    """The first feature of ``fc`` whose ``prop`` equals ``value`` (as int)."""
    if not fc or value is None:
        return None
    try:
        want = int(value)
    except (TypeError, ValueError):
        return None
    for feat in fc.get("features") or []:
        got = (feat.get("properties") or {}).get(prop)
        try:
            if got is not None and int(got) == want:
                return feat
        except (TypeError, ValueError):
            continue
    return None


@functools.lru_cache(maxsize=128)
def v2_reach_feature(comid: int) -> Optional[dict]:
    """The NHDPlus V2 reach's feature (attributes and geometry) from the USGS
    fabric API, for the scored-reach highlight when the reach lies outside the
    viewport's V2 fetch (a routed click). None when unknown or unanswered."""
    try:
        from .datasources import fabric
        feat = fabric.feature_by_comid(int(comid))
    except Exception:  # noqa: BLE001 - resilience by design
        return None
    return feat if feat and feat.get("geometry") else None
