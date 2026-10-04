"""The riparian rule's join point (hrbuild.zonal.Paths)."""
import numpy as np

from hrbuild.zonal import Paths

#   rows:  0 outlet <- 1 <- 3 <- 5
#                    <- 2 <- 4
#          6 a separate outlet <- 7
DOWN = np.array([-1, 0, 0, 1, 2, 3, -1, 6])


def test_depth_counts_hops_to_the_outlet():
    assert Paths(DOWN).depth.tolist() == [0, 1, 1, 2, 2, 3, 0, 1]


def test_join_hops():
    p = Paths(DOWN)
    c = np.array([3, 3, 3, 3, 1, 5, 4, 3])
    l = np.array([3, 5, 1, 4, 0, 2, 3, 7])
    # own line 0; upstream line 0; its downstream line 1 hop; the cousin's path joins at the
    # outlet 2 hops down; line 1's downstream is the outlet; 5 meets 2 at the outlet 3 hops down;
    # 4 meets 3 at the outlet 2 hops down; another basin never meets
    assert p.join_hops(c, l).tolist() == [0, 0, 1, 2, 1, 3, 2, -1]
