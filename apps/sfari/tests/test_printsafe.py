"""Print-safe glyph normalization (A1).

``report.to_print_safe`` maps the handful of non-ASCII glyphs that appear in
stored evidence text to print-safe ASCII, at render time only (it never mutates
stored evidence). Pure/offline.
"""
from sfari.report import to_print_safe


def test_maps_listed_tokens():
    assert to_print_safe("road density 1.23 km²") == "road density 1.23 km2"
    assert to_print_safe("impervious Δ +2.3 pts") == "impervious delta +2.3 pts"
    assert to_print_safe("bed shear τ 0.215 lb/ft²") == "bed shear tau 0.215 lb/ft2"
    assert to_print_safe("a → b") == "a -> b"


def test_maps_dashes_degree_micro():
    assert to_print_safe("2001–2019") == "2001-2019"        # en dash
    assert to_print_safe("range — wide") == "range - wide"  # em dash
    assert to_print_safe("20°C") == "20degC"                # degree
    assert to_print_safe("5 µg/L") == "5 ug/L"              # micro sign
    assert to_print_safe("5 μg/L") == "5 ug/L"              # greek mu


def test_result_is_pure_ascii_and_tolerates_none():
    out = to_print_safe("V 2.74 ft/s, τ 0.215 lb/ft² → mobilizes")
    assert out == "V 2.74 ft/s, tau 0.215 lb/ft2 -> mobilizes"
    assert out.isascii()
    assert to_print_safe(None) == ""
    assert to_print_safe("") == ""


def test_does_not_touch_plain_ascii():
    plain = "Impervious 12.3% (watershed)"
    assert to_print_safe(plain) == plain
