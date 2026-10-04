"""Wetland subdivision for the NWI strips (hrbuild.extras.subdivide)."""
import numpy as np
import shapely

from hrbuild.extras import subdivide


def _wavy_ring(n=4000, r=1000.0):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rr = r * (1 + 0.2 * np.sin(37 * a))
    return np.column_stack([rr * np.cos(a), rr * np.sin(a)])


def test_pieces_cover_each_polygon_exactly():
    big = shapely.Polygon(_wavy_ring(), holes=[_wavy_ring(800, 300.0)])
    small = shapely.box(5000, 5000, 5100, 5100)
    pieces, parent = subdivide(np.array([small, big], dtype=object), max_vertices=64)
    assert parent[0] == 0 and (parent[1:] == 1).all() and len(pieces) > 50
    assert shapely.get_num_coordinates(pieces).max() <= 64
    assert abs(shapely.area(pieces[parent == 1]).sum() - big.area) < 1e-6 * big.area
    assert shapely.union_all(pieces[parent == 1]).symmetric_difference(big).area < 1e-6 * big.area
    # strips crossing the polygon meet the same area whole or in pieces
    strips = shapely.buffer(shapely.linestrings([[[-1500, y], [1500, y + 300]] for y in (-600, 0, 450)]), 150,
                            cap_style="flat")
    whole = shapely.area(shapely.intersection(strips, big))
    cut = np.array([shapely.area(shapely.intersection(s, pieces[parent == 1])).sum() for s in strips])
    assert np.allclose(whole, cut, rtol=1e-9)
