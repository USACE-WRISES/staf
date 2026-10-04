"""Exact grid polygons stored as shared borders ("arcs").

Catchments are raster polygons (``hrslim.grid``): whole-cell vertices, edges along
the grid lines, and every border shared by two catchments. Here each border is
stored once, as an *arc*: its start corner, the axis of its first move, and one
signed run length (in cells) per corner. A catchment is a list of polygons, each a
list of rings, each a list of arc references (arc id and direction). Every arc
also records the catchment on its left and on its right (``-1`` for none), so the
outline of any set of catchments is assembled from the arcs with exactly one side
in the set, and its area is the sum of the members' cell counts.

Encoding normalizes rings (drops repeated and collinear vertices, zero-area
rings), splits every edge at the corners other rings place on it, and cuts rings
into arcs at nodes (vertices with other than two edges). Decoding returns the same
polygons, vertex for vertex up to that normalization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import shapely


class NotGridAligned(ValueError):
    """The polygons have a diagonal edge, so they are not raster polygons."""


@dataclass
class Encoded:
    """Arcs and the catchments built from them (all integers, grid cells)."""

    # arcs
    x0: np.ndarray
    y0: np.ndarray
    axis: np.ndarray            # 0: the first move is along x, 1: along y
    run_off: np.ndarray         # (n_arcs + 1) offsets into ``runs``
    runs: np.ndarray            # one signed run per move; closed arcs end where they start
    left: np.ndarray            # catchment row on the arc's left, -1 for none
    right: np.ndarray
    # catchment rows
    refs: np.ndarray            # arc * 2 + 1 when the ring follows the arc backwards
    ring_off: np.ndarray        # (n_rings + 1) offsets into ``refs``
    poly_off: np.ndarray        # (n_polys + 1) offsets into rings
    row_off: np.ndarray         # (n_rows + 1) offsets into polys
    area2: np.ndarray           # twice the area, in cells
    bbox: np.ndarray            # (n_rows, 4) xmin, ymin, xmax, ymax in cells
    stats: dict = field(default_factory=dict)

    @property
    def n_arcs(self) -> int:
        return len(self.x0)

    @property
    def n_rows(self) -> int:
        return len(self.row_off) - 1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _offsets(counts) -> np.ndarray:
    return np.concatenate([[0], np.cumsum(np.asarray(counts, dtype=np.int64))]).astype(np.int64)


def _ring_neighbours(ring_len: np.ndarray):
    """For vertices stored ring after ring: ring id, previous and next index."""
    off = _offsets(ring_len)
    n = int(off[-1])
    rid = np.repeat(np.arange(len(ring_len)), ring_len)
    pos = np.arange(n)
    nxt = pos + 1
    prv = pos - 1
    nonempty = ring_len > 0
    nxt[off[1:][nonempty] - 1] = off[:-1][nonempty]
    prv[off[:-1][nonempty]] = off[1:][nonempty] - 1
    return rid, prv, nxt, off


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenated ``arange(s, s + c)`` for every pair."""
    counts = np.asarray(counts, dtype=np.int64)
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    base = np.repeat(np.asarray(starts, dtype=np.int64) - _offsets(counts)[:-1], counts)
    return base + np.arange(total, dtype=np.int64)


def _normalize(x, y, ring_len):
    """Drop repeated and collinear vertices until none are left (spikes go too)."""
    x, y, ring_len = x.copy(), y.copy(), ring_len.copy()
    for _ in range(64):
        rid, prv, nxt, _ = _ring_neighbours(ring_len)
        dup = (x == x[prv]) & (y == y[prv])
        if dup.any():
            keep = ~dup
        else:
            col = ((x[prv] == x) & (x == x[nxt])) | ((y[prv] == y) & (y == y[nxt]))
            if not col.any():
                return x, y, ring_len
            keep = ~col
        x, y = x[keep], y[keep]
        ring_len = np.bincount(rid[keep], minlength=len(ring_len)).astype(np.int64)
    raise RuntimeError("ring normalization did not settle")


def _ring_area2(x, y, ring_len):
    """Twice the signed area of each ring (shoelace about the ring's first vertex,
    so the sums stay small and exact)."""
    rid, _, nxt, off = _ring_neighbours(ring_len)
    if not len(x):
        return np.zeros(len(ring_len), dtype=np.int64)
    first = off[:-1][rid]
    rx, ry = x - x[first], y - y[first]
    cross = rx * ry[nxt] - rx[nxt] * ry
    return np.bincount(rid, weights=cross, minlength=len(ring_len)).round().astype(np.int64)


# --------------------------------------------------------------------------- #
# encoding
# --------------------------------------------------------------------------- #
def encode(x, y, ring_off, poly_off, row_off) -> Encoded:
    """Encode polygons given as ragged arrays (``shapely.to_ragged_array`` layout:
    vertex offsets per ring with the closing vertex repeated, ring offsets per
    polygon, polygon offsets per row) in whole grid cells."""
    x = np.asarray(x, dtype=np.int64)
    y = np.asarray(y, dtype=np.int64)
    ring_off = np.asarray(ring_off, dtype=np.int64)
    poly_off = np.asarray(poly_off, dtype=np.int64)
    row_off = np.asarray(row_off, dtype=np.int64)
    n_rings = len(ring_off) - 1
    n_polys = len(poly_off) - 1
    n_rows = len(row_off) - 1
    stats: dict = {"rows": n_rows, "rings_in": n_rings, "vertices_in": int(len(x))}

    # ---- drop the closing vertex of every ring, then normalize
    ring_len = np.diff(ring_off)
    closing = np.zeros(len(x), dtype=bool)
    has = ring_len > 0
    closing[ring_off[1:][has] - 1] = True
    x, y = x[~closing], y[~closing]
    ring_len = np.maximum(ring_len - has.astype(np.int64), 0)
    x, y, ring_len = _normalize(x, y, ring_len)
    _, _, nxt0, _ = _ring_neighbours(ring_len)
    if np.any((x != x[nxt0]) & (y != y[nxt0])):
        raise NotGridAligned("diagonal edge found")

    # ---- drop zero-area rings; a polygon without its exterior ring goes, holes too
    area2_ring = _ring_area2(x, y, ring_len)
    ring_ok = (ring_len >= 4) & (area2_ring != 0)
    poly_of_ring = np.repeat(np.arange(n_polys), np.diff(poly_off))
    exterior = np.zeros(n_rings, dtype=bool)
    exterior[poly_off[:-1][np.diff(poly_off) > 0]] = True
    poly_ok = np.zeros(n_polys, dtype=bool)
    poly_ok[poly_of_ring[exterior & ring_ok]] = True
    ring_ok &= poly_ok[poly_of_ring]
    stats["rings_dropped"] = int((~ring_ok).sum())
    vkeep = np.repeat(ring_ok, ring_len)
    x, y = x[vkeep], y[vkeep]
    ring_len, area2_ring = ring_len[ring_ok], area2_ring[ring_ok]
    exterior = exterior[ring_ok]
    poly_of_ring = poly_of_ring[ring_ok]
    poly_ring_count = np.bincount(poly_of_ring, minlength=n_polys)
    row_of_poly = np.repeat(np.arange(n_rows), np.diff(row_off))
    row_poly_count = np.bincount(row_of_poly[poly_ok], minlength=n_rows)
    empty_rows = np.nonzero(row_poly_count == 0)[0]
    if empty_rows.size:
        raise ValueError(f"{empty_rows.size} rows have no non-degenerate polygon (first {empty_rows[:5]})")
    poly_ring_count = poly_ring_count[poly_ok]
    new_poly_off = _offsets(poly_ring_count)
    new_row_off = _offsets(row_poly_count)
    n_rings = len(ring_len)
    row_of_ring = np.repeat(np.repeat(np.arange(n_rows), row_poly_count), poly_ring_count)

    # ---- orient every ring with the polygon on its left (exteriors counter-
    # clockwise, holes clockwise): the side owners then follow from the references
    flip = np.where(exterior, area2_ring < 0, area2_ring > 0)
    if flip.any():
        rid, _, _, voff = _ring_neighbours(ring_len)
        local = np.arange(len(x)) - voff[:-1][rid]
        src = np.where(flip[rid], voff[:-1][rid] + ring_len[rid] - 1 - local, np.arange(len(x)))
        x, y = x[src], y[src]
        area2_ring = np.where(flip, -area2_ring, area2_ring)
    stats["rings_reoriented"] = int(flip.sum())

    # ---- every edge must run along the grid
    rid, prv, nxt, voff = _ring_neighbours(ring_len)
    if np.any((x != x[nxt]) & (y != y[nxt])):
        raise NotGridAligned("diagonal edge found")

    # ---- row areas and boxes (before splitting: splitting adds only collinear points)
    sign = np.where(exterior, 1, -1)
    area2_row = np.bincount(row_of_ring, weights=sign * np.abs(area2_ring), minlength=n_rows).astype(np.int64)
    vrow = row_of_ring[rid]
    bbox = np.empty((n_rows, 4), dtype=np.int64)
    order = np.argsort(vrow, kind="stable")
    starts = _offsets(np.bincount(vrow, minlength=n_rows))[:-1]
    bbox[:, 0] = np.minimum.reduceat(x[order], starts)
    bbox[:, 1] = np.minimum.reduceat(y[order], starts)
    bbox[:, 2] = np.maximum.reduceat(x[order], starts)
    bbox[:, 3] = np.maximum.reduceat(y[order], starts)

    # ---- split every edge at the corners lying on it (T-junctions)
    xmin, ymin = int(x.min()), int(y.min())
    sx, sy = x - xmin, y - ymin
    wx, wy = int(sx.max()) + 1, int(sy.max()) + 1
    key_h = np.unique(sy * wx + sx)            # vertices ordered by row, then x
    key_v = np.unique(sx * wy + sy)            # by column, then y
    nx_, ny_ = sx[nxt], sy[nxt]
    horiz = sy == ny_
    lo = np.where(horiz, sy * wx + np.minimum(sx, nx_), sx * wy + np.minimum(sy, ny_))
    hi = np.where(horiz, sy * wx + np.maximum(sx, nx_), sx * wy + np.maximum(sy, ny_))
    lo_pos = np.where(horiz, np.searchsorted(key_h, lo, "right"), np.searchsorted(key_v, lo, "right"))
    hi_pos = np.where(horiz, np.searchsorted(key_h, hi, "left"), np.searchsorted(key_v, hi, "left"))
    inner = np.maximum(hi_pos - lo_pos, 0)
    stats["split_points"] = int(inner.sum())
    out_count = 1 + inner
    out_off = _offsets(out_count)
    total = int(out_off[-1])
    ox = np.empty(total, dtype=np.int64)
    oy = np.empty(total, dtype=np.int64)
    ox[out_off[:-1]] = sx
    oy[out_off[:-1]] = sy
    if inner.any():
        seg = np.repeat(np.arange(len(sx)), inner)
        j = np.arange(int(inner.sum())) - np.repeat(_offsets(inner)[:-1], inner)
        forward = np.where(horiz[seg], nx_[seg] > sx[seg], ny_[seg] > sy[seg])
        pos = np.where(forward, lo_pos[seg] + j, hi_pos[seg] - 1 - j)
        hseg = horiz[seg]
        keys = np.where(hseg, key_h[np.minimum(pos, len(key_h) - 1)], key_v[np.minimum(pos, len(key_v) - 1)])
        px = np.where(hseg, keys % wx, keys // wy)
        py = np.where(hseg, keys // wx, keys % wy)
        slot = out_off[seg] + 1 + j
        ox[slot], oy[slot] = px, py
    ring_len2 = np.bincount(rid, weights=out_count, minlength=n_rings).astype(np.int64)
    rid2, _, nxt2, voff2 = _ring_neighbours(ring_len2)

    # ---- vertices and undirected edges
    vkey = ox * wy + oy
    ukeys, vid = np.unique(vkey, return_inverse=True)
    vid = vid.astype(np.int64)
    nv = len(ukeys)
    a, b = vid, vid[nxt2]
    lo_v, hi_v = np.minimum(a, b), np.maximum(a, b)
    ekeys, eid, ecount = np.unique(lo_v * nv + hi_v, return_inverse=True, return_counts=True)
    eid = eid.astype(np.int64)
    ne = len(ekeys)
    deg = np.bincount(np.concatenate([ekeys // nv, ekeys % nv]), minlength=nv)
    stats.update(vertices=nv, edges=ne, shared_edges=int((ecount == 2).sum()),
                 edges_used_3_or_more=int((ecount > 2).sum()))

    # ---- owners: the side of each edge a catchment lies on
    interior_left = np.where(exterior, area2_ring > 0, area2_ring < 0)
    canonical = a < b
    on_left = interior_left[rid2] == canonical
    rows = row_of_ring[rid2]
    left = np.full(ne, -1, dtype=np.int64)
    right = np.full(ne, -1, dtype=np.int64)
    left[eid[on_left]] = rows[on_left]
    right[eid[~on_left]] = rows[~on_left]
    clash = (np.bincount(eid[on_left], minlength=ne) > 1) | (np.bincount(eid[~on_left], minlength=ne) > 1)
    stats["edges_claimed_twice_on_one_side"] = int(clash.sum())

    # ---- cut rings into runs at nodes; rotate each ring to start at a node
    node = deg[a] != 2
    n_ring_nodes = np.bincount(rid2, weights=node, minlength=n_rings)
    local = np.arange(total) - voff2[:-1][rid2]
    big = np.iinfo(np.int64).max
    first_node = np.full(n_rings, big, dtype=np.int64)
    np.minimum.at(first_node, rid2[node], local[node])
    first_node[n_ring_nodes == 0] = 0
    rot = voff2[:-1][rid2] + (local - first_node[rid2]) % ring_len2[rid2]
    perm = np.empty(total, dtype=np.int64)
    perm[rot] = np.arange(total)               # perm[k] = position at rotated slot k
    r_a, r_e, r_canon, r_node, r_rid = a[perm], eid[perm], canonical[perm], node[perm], rid2[perm]
    r_local = np.arange(total) - voff2[:-1][r_rid]
    starts_run = r_node | (r_local == 0)
    run_id = np.cumsum(starts_run) - 1
    n_runs = int(run_id[-1]) + 1 if total else 0
    run_start = np.nonzero(starts_run)[0]
    run_len = np.diff(np.concatenate([run_start, [total]]))
    run_ring = r_rid[run_start]
    run_min_edge = np.minimum.reduceat(r_e, run_start)
    is_min = r_e == run_min_edge[run_id]
    min_pos = np.full(n_runs, -1, dtype=np.int64)
    min_pos[run_id[is_min][::-1]] = np.nonzero(is_min)[0][::-1]     # first occurrence per run
    run_canon = r_canon[min_pos]
    closed_run = n_ring_nodes[run_ring] == 0

    # ---- one arc per distinct run (a shared border appears in two rings)
    _, first_run, run_arc0 = np.unique(run_min_edge, return_index=True, return_inverse=True)
    arc_order = np.argsort(first_run, kind="stable")               # arcs in order of first use
    rank = np.empty_like(arc_order)
    rank[arc_order] = np.arange(len(arc_order))
    run_arc = rank[run_arc0]
    rep_run = first_run[arc_order]
    n_arcs = len(rep_run)
    reversed_ = run_canon != run_canon[rep_run[run_arc]]
    stats["arcs"] = n_arcs

    # ---- arc vertices from each representative run (+ the end vertex of open runs)
    rep_closed = closed_run[rep_run]
    rep_start, rep_len = run_start[rep_run], run_len[rep_run]
    idx = _ranges(rep_start, rep_len)
    arc_of_v = np.repeat(np.arange(n_arcs), rep_len)
    vv = r_a[idx]
    end_slot = rep_start + rep_len                                  # rotated slot after the run
    ring_of_rep = run_ring[rep_run]
    ring_end = voff2[1:][ring_of_rep]
    end_slot = np.where(end_slot >= ring_end, voff2[:-1][ring_of_rep], end_slot)
    end_v = np.where(rep_closed, r_a[rep_start], r_a[np.minimum(end_slot, total - 1)])
    av_count = rep_len + 1                                          # closed arcs repeat their start
    av_off = _offsets(av_count)
    arc_v = np.empty(int(av_off[-1]), dtype=np.int64)
    body_slots = av_off[arc_of_v] + (np.arange(len(idx)) - _offsets(rep_len)[:-1][arc_of_v])
    arc_v[body_slots] = vv
    arc_v[av_off[1:] - 1] = end_v
    vx_all, vy_all = ukeys // wy, ukeys % wy
    axv, ayv = vx_all[arc_v], vy_all[arc_v]

    # ---- drop collinear interior vertices (closed arcs: rotate to start at a corner first)
    arc_id_v = np.repeat(np.arange(n_arcs), av_count)
    pos_in = np.arange(len(arc_v)) - av_off[:-1][arc_id_v]
    last = av_count[arc_id_v] - 1
    if rep_closed.any():
        # vertices 0..last-1 form the cycle; find a corner and rotate there
        cyc_len = av_count - 1
        cprev = np.where(pos_in == 0, av_off[:-1][arc_id_v] + last - 1, np.arange(len(arc_v)) - 1)
        cnext = np.where(pos_in >= last - 1, av_off[:-1][arc_id_v], np.arange(len(arc_v)) + 1)
        corner = ~(((axv[cprev] == axv) & (axv == axv[cnext])) | ((ayv[cprev] == ayv) & (ayv == ayv[cnext])))
        corner &= pos_in < last
        first_corner = np.full(n_arcs, big, dtype=np.int64)
        np.minimum.at(first_corner, arc_id_v[corner], pos_in[corner])
        first_corner = np.where(rep_closed & (first_corner < big), first_corner, 0)
        shift = np.where(rep_closed[arc_id_v] & (pos_in < last),
                         (pos_in + first_corner[arc_id_v]) % np.maximum(cyc_len[arc_id_v], 1), pos_in)
        src = av_off[:-1][arc_id_v] + shift
        src = np.where(rep_closed[arc_id_v] & (pos_in == last), av_off[:-1][arc_id_v] + first_corner[arc_id_v], src)
        axv, ayv = axv[src], ayv[src]
    interior = (pos_in > 0) & (pos_in < last)
    pi = np.maximum(np.arange(len(arc_v)) - 1, 0)
    ni = np.minimum(np.arange(len(arc_v)) + 1, len(arc_v) - 1)
    col = interior & (((axv[pi] == axv) & (axv == axv[ni])) | ((ayv[pi] == ayv) & (ayv == ayv[ni])))
    keepv = ~col
    axv, ayv, arc_id_v = axv[keepv], ayv[keepv], arc_id_v[keepv]
    kcount = np.bincount(arc_id_v, minlength=n_arcs)
    koff = _offsets(kcount)

    # ---- moves: one signed run per move, alternating axes
    mv = np.ones(len(axv), dtype=bool)
    mv[koff[1:] - 1] = False                                         # no move after an arc's last vertex
    dxs = np.diff(axv, append=axv[-1:])
    dys = np.diff(ayv, append=ayv[-1:])
    dxs, dys = dxs[mv], dys[mv]
    if np.any((dxs != 0) & (dys != 0)) or np.any((dxs == 0) & (dys == 0)):
        raise NotGridAligned("an arc has a diagonal or empty move")
    runs = np.where(dxs != 0, dxs, dys)
    move_axis = (dxs == 0).astype(np.int8)                           # 0: x, 1: y
    run_count = kcount - 1
    run_off = _offsets(run_count)
    axis0 = move_axis[run_off[:-1]]
    j = np.arange(len(runs)) - np.repeat(run_off[:-1], run_count)
    if np.any(move_axis != ((np.repeat(axis0, run_count) + j) % 2)):
        raise NotGridAligned("arc moves do not alternate between the axes")
    x0 = axv[koff[:-1]] + xmin
    y0 = ayv[koff[:-1]] + ymin

    # ---- owners per arc, in the direction of its representative run
    rep_edge = run_min_edge[rep_run]
    rep_canon = run_canon[rep_run]
    arc_left = np.where(rep_canon, left[rep_edge], right[rep_edge])
    arc_right = np.where(rep_canon, right[rep_edge], left[rep_edge])

    refs = run_arc.astype(np.int64) * 2 + reversed_.astype(np.int64)
    ring_run_count = np.bincount(run_ring, minlength=n_rings)
    return Encoded(
        x0=x0, y0=y0, axis=axis0.astype(np.uint8), run_off=run_off, runs=runs.astype(np.int64),
        left=arc_left, right=arc_right, refs=refs, ring_off=_offsets(ring_run_count),
        poly_off=new_poly_off, row_off=new_row_off, area2=area2_row, bbox=bbox, stats=stats)


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #
def _all_arc_vertices(enc: Encoded):
    """``arc_vertices`` for every arc at once (no gathers: runs are already in arc order)."""
    n_runs = np.diff(enc.run_off)
    owner = np.repeat(np.arange(enc.n_arcs, dtype=np.int64), n_runs)
    j = np.arange(len(enc.runs), dtype=np.int64) - enc.run_off[:-1][owner]
    along_x = ((enc.axis[owner].astype(np.int64) + j) & 1) == 0
    dx = np.where(along_x, enc.runs, 0)
    dy = enc.runs - dx
    cx, cy = np.cumsum(dx), np.cumsum(dy)
    before = enc.run_off[:-1] - 1
    bx = np.where(before >= 0, cx[np.maximum(before, 0)], 0)
    by = np.where(before >= 0, cy[np.maximum(before, 0)], 0)
    off = enc.run_off + np.arange(enc.n_arcs + 1)       # each arc has one more vertex than runs
    xs = np.empty(int(off[-1]), dtype=np.int64)
    ys = np.empty(int(off[-1]), dtype=np.int64)
    xs[off[:-1]] = enc.x0
    ys[off[:-1]] = enc.y0
    slot = np.arange(len(enc.runs), dtype=np.int64) + owner + 1
    xs[slot] = enc.x0[owner] + cx - bx[owner]
    ys[slot] = enc.y0[owner] + cy - by[owner]
    return xs, ys, off


def arc_vertices(enc: Encoded, arcs: Optional[np.ndarray] = None):
    """Vertices (cells) of the given arcs: ``(x, y, offsets)``; closed arcs end
    with their start vertex."""
    if arcs is None:
        return _all_arc_vertices(enc)
    arcs = np.asarray(arcs, dtype=np.int64)
    n_runs = enc.run_off[arcs + 1] - enc.run_off[arcs]
    idx = _ranges(enc.run_off[arcs], n_runs)
    owner = np.repeat(np.arange(len(arcs)), n_runs)
    j = np.arange(len(idx)) - np.repeat(_offsets(n_runs)[:-1], n_runs)
    ax = (enc.axis[arcs][owner].astype(np.int64) + j) % 2
    r = enc.runs[idx]
    dx = np.where(ax == 0, r, 0)
    dy = np.where(ax == 1, r, 0)
    count = n_runs + 1
    off = _offsets(count)
    xs = np.empty(int(off[-1]), dtype=np.int64)
    ys = np.empty(int(off[-1]), dtype=np.int64)
    xs[off[:-1]] = enc.x0[arcs]
    ys[off[:-1]] = enc.y0[arcs]
    if len(idx):
        cx = np.cumsum(dx)
        cy = np.cumsum(dy)
        base = _offsets(n_runs)[:-1]
        has = n_runs > 0
        sub_x = np.zeros(len(arcs), dtype=np.int64)
        sub_y = np.zeros(len(arcs), dtype=np.int64)
        sub_x[has] = np.concatenate([[0], cx])[base[has]]
        sub_y[has] = np.concatenate([[0], cy])[base[has]]
        slot = off[owner] + 1 + j
        xs[slot] = enc.x0[arcs][owner] + cx - sub_x[owner]
        ys[slot] = enc.y0[arcs][owner] + cy - sub_y[owner]
    return xs, ys, off


def rows_ragged(enc: Encoded, rows: np.ndarray):
    """Ragged arrays (``shapely.from_ragged_array`` MULTIPOLYGON layout, closing
    vertex repeated) for the given catchment rows: ``(x, y, ring_off, poly_off, row_off)``."""
    rows = np.asarray(rows, dtype=np.int64)
    p0, p1 = enc.row_off[rows], enc.row_off[rows + 1]
    polys = _ranges(p0, p1 - p0)
    r0, r1 = enc.poly_off[polys], enc.poly_off[polys + 1]
    rings = _ranges(r0, r1 - r0)
    f0, f1 = enc.ring_off[rings], enc.ring_off[rings + 1]
    ref_idx = _ranges(f0, f1 - f0)
    refs = enc.refs[ref_idx]
    arc_ids = refs >> 1
    backwards = (refs & 1).astype(bool)
    ring_of_ref = np.repeat(np.arange(len(rings)), f1 - f0)
    first_in_ring = np.ones(len(refs), dtype=bool)
    first_in_ring[1:] = ring_of_ref[1:] != ring_of_ref[:-1]
    uniq, inv = np.unique(arc_ids, return_inverse=True)
    ax, ay, aoff = arc_vertices(enc, uniq)
    s, e = aoff[inv], aoff[inv + 1]
    take = (e - s) - (~first_in_ring).astype(np.int64)
    t = np.arange(int(take.sum())) - np.repeat(_offsets(take)[:-1], take)
    owner = np.repeat(np.arange(len(refs)), take)
    t = t + (~first_in_ring[owner]).astype(np.int64)
    src = np.where(backwards[owner], e[owner] - 1 - t, s[owner] + t)
    xs, ys = ax[src], ay[src]
    ring_counts = np.bincount(ring_of_ref, weights=take, minlength=len(rings)).astype(np.int64)
    return (xs, ys, _offsets(ring_counts), _offsets(r1 - r0), _offsets(p1 - p0))


def rows_geometry(enc: Encoded, rows) -> np.ndarray:
    """MultiPolygons (cells) of the given rows."""
    xs, ys, ring_off, poly_off, row_off = rows_ragged(enc, rows)
    coords = np.column_stack([xs, ys]).astype(np.float64)
    return shapely.from_ragged_array(shapely.GeometryType.MULTIPOLYGON, coords, (ring_off, poly_off, row_off))


def ref_rows(enc: Encoded) -> np.ndarray:
    """The catchment row of every reference."""
    ring_row = np.repeat(np.repeat(np.arange(enc.n_rows), np.diff(enc.row_off)), np.diff(enc.poly_off))
    return np.repeat(ring_row, np.diff(enc.ring_off))


def owners_from_refs(enc: Encoded) -> tuple[np.ndarray, np.ndarray]:
    """Left and right catchment rows of every arc, from the references: rings are
    stored with the polygon on their left, so a forward reference puts its row on
    the arc's left and a backward one on its right."""
    rows = ref_rows(enc)
    arc = enc.refs >> 1
    back = (enc.refs & 1).astype(bool)
    left = np.full(enc.n_arcs, -1, dtype=np.int64)
    right = np.full(enc.n_arcs, -1, dtype=np.int64)
    left[arc[~back]] = rows[~back]
    right[arc[back]] = rows[back]
    return left, right


def area2_from_arcs(enc: Encoded) -> np.ndarray:
    """Twice each row's area (cells), summed from per-arc shoelace terms (exact
    integers; rings carry the polygon on their left, so holes subtract)."""
    xs, ys, off = arc_vertices(enc)
    if not len(xs):
        return np.zeros(enc.n_rows, dtype=np.int64)
    xs = xs - xs.min()                                # one origin for every arc keeps terms small;
    ys = ys - ys.min()                                # the shift cancels around closed rings
    cross = np.zeros(len(xs), dtype=np.int64)
    cross[1:] = xs[:-1] * ys[1:] - xs[1:] * ys[:-1]
    cross[off[:-1]] = 0                               # no term across arc boundaries
    cs = np.cumsum(cross)
    term = cs[off[1:] - 1] - np.where(off[:-1] > 0, cs[np.maximum(off[:-1] - 1, 0)], 0)
    signed = np.where((enc.refs & 1).astype(bool), -term[enc.refs >> 1], term[enc.refs >> 1])
    row_ref_start = enc.ring_off[enc.poly_off[enc.row_off[:-1]]]
    sums = np.add.reduceat(signed, np.minimum(row_ref_start, max(len(signed) - 1, 0))) if len(signed) else signed
    counts = np.diff(np.append(row_ref_start, len(signed)))
    return np.where(counts > 0, sums, 0).astype(np.int64)


def boundary_arcs(enc: Encoded, member: np.ndarray) -> np.ndarray:
    """Arcs with exactly one side in ``member`` (a boolean mask over rows)."""
    member = np.asarray(member, dtype=bool)
    lf = (enc.left >= 0) & member[np.maximum(enc.left, 0)]
    rt = (enc.right >= 0) & member[np.maximum(enc.right, 0)]
    return np.nonzero(lf ^ rt)[0]


def _node_key(x, y):
    return (np.asarray(x, dtype=np.int64) + (1 << 30)) * (1 << 31) + (np.asarray(y, dtype=np.int64) + (1 << 30))


def _follow(succ: np.ndarray):
    """The cycles of a successor map: arcs in ring order, arcs per ring, ring of each arc."""
    n = len(succ)
    ring_of = np.full(n, -1, dtype=np.int64)
    seq, ring_arcs = [], []
    for i in range(n):
        if ring_of[i] >= 0:
            continue
        r, k, j = len(ring_arcs), 0, i
        while ring_of[j] < 0:
            ring_of[j] = r
            seq.append(j)
            k += 1
            j = int(succ[j])
        if j != i:
            raise RuntimeError("boundary arcs do not chain into rings")
        ring_arcs.append(k)
    return np.asarray(seq, dtype=np.int64), ring_arcs, ring_of


def outline(enc: Encoded, member: np.ndarray):
    """The polygon (cells) covered by the member rows, from their boundary arcs.

    Each boundary arc is turned so the member side is on its left, then the arcs
    are chained end to start into rings. Where two member corners touch (four
    boundary edges at one node) the chain first takes the left turn, which keeps
    parts touching at a corner apart; a ring that still passes such a node twice
    (a hole touching its shell, or two holes touching) swaps the pairing there,
    which splits it in two, so every ring is simple as valid polygons need. Rings
    running counter-clockwise are shells, clockwise ones holes; a hole belongs to
    the smallest shell around a point a quarter cell inside the member side of it.
    """
    member = np.asarray(member, dtype=bool)
    arcs = boundary_arcs(enc, member)
    if not arcs.size:
        return shapely.MultiPolygon()
    xs, ys, off = arc_vertices(enc, arcs)
    n = len(arcs)
    counts = np.diff(off)
    owner = np.repeat(np.arange(n), counts)
    pos = np.arange(len(xs)) - off[owner]
    fwd = (enc.left[arcs] >= 0) & member[np.maximum(enc.left[arcs], 0)]
    src = np.where(fwd[owner], off[owner] + pos, off[owner] + counts[owner] - 1 - pos)
    ox, oy = xs[src], ys[src]
    first, last = off[:-1], off[1:] - 1
    start_key, end_key = _node_key(ox[first], oy[first]), _node_key(ox[last], oy[last])
    in_dx, in_dy = np.sign(ox[last] - ox[last - 1]), np.sign(oy[last] - oy[last - 1])
    out_dx, out_dy = np.sign(ox[first + 1] - ox[first]), np.sign(oy[first + 1] - oy[first])

    # successor of every arc: the arc leaving its end node (the left turn at a pinch)
    order = np.argsort(start_key, kind="stable")
    sk = start_key[order]
    lo = np.searchsorted(sk, end_key, "left")
    hi = np.searchsorted(sk, end_key, "right")
    if np.any(hi - lo < 1) or np.any(hi - lo > 2):
        raise RuntimeError("boundary arcs do not chain into rings")
    succ = order[lo]
    pinch = np.nonzero(hi - lo == 2)[0]
    if pinch.size:
        a, b = order[lo[pinch]], order[lo[pinch] + 1]
        cross_a = in_dx[pinch] * out_dy[a] - in_dy[pinch] * out_dx[a]
        succ[pinch] = np.where(cross_a > 0, a, b)

    # rings: follow the successors; split any ring that passes a pinch node twice
    pairs = np.empty((0, 2), dtype=np.int64)
    if pinch.size:
        by_node = pinch[np.argsort(end_key[pinch], kind="stable")]
        if by_node.size % 2 or np.any(end_key[by_node[0::2]] != end_key[by_node[1::2]]):
            raise RuntimeError("boundary arcs do not chain into rings")
        pairs = by_node.reshape(-1, 2)
    for _ in range(256):
        seq, ring_arcs, ring_of = _follow(succ)
        same = pairs[ring_of[pairs[:, 0]] == ring_of[pairs[:, 1]]]
        if not same.size:
            break
        _, one = np.unique(ring_of[same[:, 0]], return_index=True)   # one node per ring at a time
        a, b = same[one, 0], same[one, 1]
        succ[a], succ[b] = succ[b].copy(), succ[a].copy()
    else:
        raise RuntimeError("rings did not settle")
    idx = _ranges(first[seq], counts[seq] - 1)               # each arc without its end vertex
    rx, ry = ox[idx], oy[idx]
    ring_len = np.bincount(np.repeat(np.arange(len(ring_arcs)), ring_arcs), weights=counts[seq] - 1,
                           minlength=len(ring_arcs)).astype(np.int64)
    rid, prv, nxt, _ = _ring_neighbours(ring_len)
    col = ((rx[prv] == rx) & (rx == rx[nxt])) | ((ry[prv] == ry) & (ry == ry[nxt]))
    if col.any():                                             # joints along a straight border
        rx, ry, rid = rx[~col], ry[~col], rid[~col]
        ring_len = np.bincount(rid, minlength=len(ring_len)).astype(np.int64)
    area2 = _ring_area2(rx, ry, ring_len)
    rings = shapely.linearrings(np.column_stack([rx, ry]).astype(np.float64), indices=rid)
    shells, holes = np.nonzero(area2 > 0)[0], np.nonzero(area2 < 0)[0]
    if len(shells) == 1:
        home = np.zeros(len(holes), dtype=np.int64)
    else:
        _, _, nxt2, roff = _ring_neighbours(ring_len)
        f = roff[:-1][holes]
        x0, y0, x1, y1 = rx[f], ry[f], rx[nxt2[f]], ry[nxt2[f]]
        dx, dy = np.sign(x1 - x0), np.sign(y1 - y0)
        probes = shapely.points((x0 + x1) / 2 - 0.25 * dy, (y0 + y1) / 2 + 0.25 * dx)
        shell_i, hole_i = shapely.STRtree(probes).query(shapely.polygons(rings[shells]), predicate="contains")
        by_size = np.lexsort((area2[shells][shell_i], hole_i))
        hole_i, shell_i = hole_i[by_size], shell_i[by_size]
        firsts = np.concatenate([[True], hole_i[1:] != hole_i[:-1]]) if hole_i.size else np.zeros(0, bool)
        home = np.full(len(holes), -1, dtype=np.int64)
        home[hole_i[firsts]] = shell_i[firsts]
        if np.any(home < 0):
            raise RuntimeError("a hole lies in no shell")
    ring_order = np.concatenate([shells, holes[np.argsort(home, kind="stable")]])
    poly_of = np.concatenate([np.arange(len(shells)), np.sort(home, kind="stable")])
    by_poly = np.argsort(poly_of, kind="stable")
    polys = shapely.polygons(rings[ring_order[by_poly]], indices=poly_of[by_poly])
    return shapely.multipolygons(polys)


def area_cells(enc: Encoded, member: np.ndarray) -> float:
    """The member rows' area in cells (exact)."""
    return float(enc.area2[np.asarray(member, dtype=bool)].sum()) / 2.0
