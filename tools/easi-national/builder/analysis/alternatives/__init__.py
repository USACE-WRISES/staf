"""Isolated, local EASI alternative studies; never a publication pipeline."""

STUDY_VERSION = "1.0.0"
BASE_METHOD = "e9f472b31fe5"
BASE_COMMIT = "6cc77c2c3fbf94d7dc9daf71df9dd3a07b56aaa7"
BASE_REFERENCE = "ad100b39313af9259bd0628728d50ab87513e2c07955bf24d78e9d107863abdd"
REGIONAL_SETS = ("corridor-woody", "corridor-natural", "flow-variability")
ALTERNATIVES = [
    {"id": "alternative-1", "label": "Alternative 1: current regional method", "curve_count": 62, "changes": "None; preserved current method"},
    {"id": "alternative-2", "label": "Alternative 2: NARS-9 references", "curve_count": 34, "changes": "Only three regional curve families use NARS-9"},
    {"id": "alternative-3", "label": "Alternative 3: national references", "curve_count": 7, "changes": "Only three regional curve families use their existing national fallback"},
    {"id": "alternative-4", "label": "Alternative 4: woody 8.2 fallback", "curve_count": 61, "changes": "Only woody Level II 8.2 uses the existing national curve"},
]
