"""Cross-section hydraulics — validated against a hand-computed trapezoid.

Trapezoid: 4 ft bottom (x in [-2,2]), 2H:1V side slopes rising to z=6 at x=+/-14.
At stage 3 ft, slope 0.002, n 0.035 (US):
  A = 30 ft^2, P = 17.416 ft, T = 16 ft, R = 1.7225 ft
  Q = (1.49/0.035)*A*R^(2/3)*sqrt(S) = 82.06 cfs, V = 2.735 ft/s
  tau = 62.4*R*S = 0.215 lb/ft^2, Froude = V/sqrt(g*A/T) = 0.352
"""
from sfari import xscalc

TRAP = [(-14, 6), (-2, 0), (2, 0), (14, 6)]


def test_trapezoid_manning():
    r = xscalc.compute(TRAP, stage=3.0, slope=0.002, n_chan=0.035, units="US")
    assert abs(r["A"] - 30.0) < 0.1
    assert abs(r["P"] - 17.416) < 0.05
    assert abs(r["T"] - 16.0) < 0.05
    assert abs(r["Q"] - 82.06) < 1.0
    assert abs(r["V"] - 2.735) < 0.05
    assert abs(r["tau"] - 0.215) < 0.005
    assert abs(r["froude"] - 0.352) < 0.01


def test_solve_stage_inverts():
    st = xscalc.solve_stage(TRAP, target_q=82.06, slope=0.002, n_chan=0.035, units="US")
    assert abs(st - 3.0) < 0.03


def test_rating_monotonic():
    rt = xscalc.rating(TRAP, slope=0.002, n_chan=0.035, units="US", steps=10)
    qs = [r["Q"] for r in rt]
    assert all(b >= a for a, b in zip(qs, qs[1:]))
    assert qs[-1] > qs[0]


def test_dry_below_bed():
    r = xscalc.compute(TRAP, stage=-1.0, slope=0.002, n_chan=0.035)
    assert r["Q"] == 0.0


def test_zones_sum_and_roughness():
    zoned = xscalc.compute(TRAP, 3.0, 0.002, 0.035, n_lob=0.06, n_rob=0.06,
                           lb=-2, rb=2, units="US")
    single = xscalc.compute(TRAP, 3.0, 0.002, 0.035, units="US")
    assert abs(zoned["A"] - single["A"]) < 0.01     # zones partition the same wetted area
    assert zoned["Q"] < single["Q"]                  # rougher overbanks reduce conveyance
