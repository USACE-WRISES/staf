"""Shared-border encoding of exact grid polygons (hrslim.arcs)."""
import itertools

import numpy as np
import pytest
import shapely

from hrslim import arcs


def P(*rings):
    return shapely.Polygon(rings[0], rings[1:])


# A small coverage in whole cells:
#   A square with a redundant vertex on its bottom edge, B an L wrapped around A
#   (drawn clockwise), C a square with a hole, D the island filling that hole,
#   E two separate squares, F a bar whose straight bottom edge meets B and C at a
#   corner of theirs (a T-junction at (8, 8)).
COVERAGE = [
    shapely.MultiPolygon([P([(0, 0), (2, 0), (4, 0), (4, 4), (0, 4)])]),
    shapely.MultiPolygon([P([(4, 0), (4, 4), (0, 4), (0, 8), (8, 8), (8, 0)])]),
    shapely.MultiPolygon([P([(8, 0), (16, 0), (16, 8), (8, 8)], [(10, 2), (10, 6), (14, 6), (14, 2)])]),
    shapely.MultiPolygon([P([(10, 2), (14, 2), (14, 6), (10, 6)])]),
    shapely.MultiPolygon([P([(0, 8), (4, 8), (4, 12), (0, 12)]), P([(12, 8), (16, 8), (16, 12), (12, 12)])]),
    shapely.MultiPolygon([P([(4, 8), (12, 8), (12, 12), (4, 12)])]),
]


def encode(geoms):
    _, coords, (ring_off, poly_off, row_off) = shapely.to_ragged_array(np.asarray(geoms, dtype=object))
    return arcs.encode(coords[:, 0], coords[:, 1], ring_off, poly_off, row_off)


@pytest.fixture(scope="module")
def enc():
    return encode(COVERAGE)


def test_decodes_to_the_same_polygons(enc):
    out = arcs.rows_geometry(enc, np.arange(len(COVERAGE)))
    for got, want in zip(out, COVERAGE):
        assert shapely.equals(got, want)
    assert np.array_equal(enc.area2 / 2, [g.area for g in COVERAGE])
    assert enc.bbox[2].tolist() == [8, 0, 16, 8]


def test_structure(enc):
    s = enc.stats
    assert s["split_points"] >= 1                       # F's edge split at (8, 8)
    assert s["edges_claimed_twice_on_one_side"] == 0
    assert s["shared_edges"] > 0
    closed = [k for k in range(enc.n_arcs)
              if arcs.arc_vertices(enc, [k])[0][0] == arcs.arc_vertices(enc, [k])[0][-1]
              and arcs.arc_vertices(enc, [k])[1][0] == arcs.arc_vertices(enc, [k])[1][-1]]
    assert closed                                        # C's hole and D share a closed border
    # every arc separates two different sides
    assert np.all(enc.left != enc.right)


def test_owners_match_the_sides(enc):
    xs, ys, off = arcs.arc_vertices(enc)
    for k in range(enc.n_arcs):
        x0, y0, x1, y1 = xs[off[k]], ys[off[k]], xs[off[k] + 1], ys[off[k] + 1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = np.sign(x1 - x0), np.sign(y1 - y0)
        left_pt = shapely.Point(mx - dy * 0.25, my + dx * 0.25)
        right_pt = shapely.Point(mx + dy * 0.25, my - dx * 0.25)

        def owner(pt):
            hit = [i for i, g in enumerate(COVERAGE) if g.contains(pt)]
            return hit[0] if hit else -1
        assert (owner(left_pt), owner(right_pt)) == (enc.left[k], enc.right[k])


def test_outline_equals_union_for_every_subset(enc):
    n = len(COVERAGE)
    for k in range(1, n + 1):
        for subset in itertools.combinations(range(n), k):
            member = np.zeros(n, dtype=bool)
            member[list(subset)] = True
            got = arcs.outline(enc, member)
            want = shapely.union_all([COVERAGE[i] for i in subset])
            assert shapely.symmetric_difference(got, want).area == 0, subset
            assert arcs.area_cells(enc, member) == want.area


def test_outline_keeps_a_gap_between_parts_touching_at_corners():
    # M1 (bottom-left L) and M2 (top-right L) touch only at (2, 1) and (1, 2); the
    # one-cell gap G between them belongs to neither.
    cells = lambda *xy: shapely.union_all([shapely.box(x, y, x + 1, y + 1) for x, y in xy])
    m1, m2 = cells((0, 0), (1, 0), (0, 1)), cells((2, 1), (1, 2), (2, 2))
    others = [cells((1, 1)), cells((2, 0)), cells((0, 2))]
    geoms = [shapely.MultiPolygon([g]) for g in [m1, m2] + others]
    enc = encode(geoms)
    member = np.array([True, True, False, False, False])
    got = arcs.outline(enc, member)
    assert got.area == 6 and arcs.area_cells(enc, member) == 6
    assert shapely.symmetric_difference(got, shapely.union_all([m1, m2])).area == 0


def test_rows_can_be_decoded_alone(enc):
    one = arcs.rows_geometry(enc, [4])
    assert shapely.equals(one[0], COVERAGE[4]) and len(one[0].geoms) == 2


def test_a_diagonal_edge_is_refused():
    with pytest.raises(arcs.NotGridAligned):
        encode([shapely.MultiPolygon([P([(0, 0), (4, 0), (0, 4)])])])


def test_spikes_and_repeats_are_normalized():
    spiky = shapely.MultiPolygon([P([(0, 0), (4, 0), (4, 0), (4, 2), (6, 2), (4, 2), (4, 4), (0, 4)])])
    enc = encode([spiky])
    got = arcs.rows_geometry(enc, [0])[0]
    assert got.area == 16 and shapely.equals(got, shapely.box(0, 0, 4, 4))


def test_owners_and_areas_rebuild_from_the_references(enc):
    left, right = arcs.owners_from_refs(enc)
    assert np.array_equal(left, enc.left) and np.array_equal(right, enc.right)
    assert np.array_equal(arcs.area2_from_arcs(enc), enc.area2)


def test_steps_pack_round_trip():
    from hrslim import fmt2
    runs = np.array([1, -1, 2, -3, 127, -128, 300, -70000, 0, 5], dtype=np.int64)
    assert np.array_equal(fmt2.unpack_steps(fmt2.pack_steps(runs)), runs)


def test_outline_of_random_cell_sets_is_the_valid_union():
    # one catchment per cell of an 8 x 8 grid: random member sets give corner
    # pinches, holes touching their shell, and islands inside holes
    side = 8
    cells = [shapely.MultiPolygon([shapely.box(i, j, i + 1, j + 1)]) for j in range(side) for i in range(side)]
    enc = encode(cells)
    rng = np.random.default_rng(3)
    for trial in range(300):
        member = rng.random(side * side) < rng.uniform(0.2, 0.8)
        if not member.any():
            continue
        got = arcs.outline(enc, member)
        want = shapely.union_all([cells[k] for k in np.nonzero(member)[0]])
        assert shapely.is_valid(got), trial
        assert shapely.symmetric_difference(got, want).area == 0, trial
        assert got.area == member.sum()


def test_outline_nests_an_island_inside_a_hole():
    box = lambda x0, y0, x1, y1: shapely.box(x0, y0, x1, y1)
    ring = shapely.MultiPolygon([box(0, 0, 5, 5).difference(box(1, 1, 4, 4))])
    moat = shapely.MultiPolygon([box(1, 1, 4, 4).difference(box(2, 2, 3, 3))])
    island = shapely.MultiPolygon([box(2, 2, 3, 3)])
    far = shapely.MultiPolygon([box(10, 10, 11, 11)])
    enc = encode([ring, moat, island, far])
    got = arcs.outline(enc, np.array([True, False, True, True]))
    assert shapely.is_valid(got) and len(got.geoms) == 3 and got.area == 16 + 1 + 1
    assert sum(len(p.interiors) for p in got.geoms) == 1
