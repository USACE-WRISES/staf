"""Build ``data/nrsa/metric_dictionary.csv``: readable names for every metric.

The bundled NRSA catalog's ``label`` column is the bare mnemonic (for all 788
rows ``label == name.split("_", 1)[1]``), so the app has been showing metric
codes: ``phab_XEMBED`` on a gallery tile, ``XEMBED`` in the summary table. EPA's
own metadata carries a real definition for almost every field, and this script
folds that together with the labels the repo already curates.

Precedence, highest first:

1. ``config/metric_names.yaml``      curated overrides and the handful EPA misses
2. ``config/metric_map.yaml``        the SFARI function crosswalk's picker labels
3. ``config/staf_metric_library.json`` SQT vocabulary for the mapped metrics
4. ``data/streamcat_metrics.csv``    StreamCat's own labels
5. EPA metadata, newest cycle first  (data/nrsa/reference/*.csv)
6. the mnemonic                      (last resort, recorded as such)

``description`` prefers the sentences already curated in
``config/nrsa_response_directions.yaml``, then EPA's definition.

Nothing here reaches the network. Run:

    py -3.12 scripts/nrsa/build_metric_dictionary.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

APP_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = APP_ROOT / "data"
CONFIG_DIR = APP_ROOT / "config"
REFERENCE_DIR = DATA_DIR / "nrsa" / "reference"
OUT_PATH = DATA_DIR / "nrsa" / "metric_dictionary.csv"

# newest first: a name is taken from the most recent cycle that defines it
CYCLES = ("2324", "1819", "1314")

# Tight spots (gallery tile headers, table cells) get ``short_name``, which is
# ``display_name`` truncated at a word boundary. ``display_name`` itself only has
# to stay sane after the mechanical cleanup below; anything past HARD_MAX means
# EPA's text is a paragraph and wants a curated name in config/metric_names.yaml.
SHORT_NAME_TARGET = 34
HARD_MAX_DISPLAY_NAME = 90

OUTPUT_COLUMNS = [
    "metric_key", "display_name", "short_name", "description", "units", "category",
    "epa_short_name", "source_cycle", "name_origin",
]


# --------------------------------------------------------------------------- #
# text cleanup
# --------------------------------------------------------------------------- #

# EPA's metadata is templated per sample type, leaving tails like
# "Acid Neutralizing Capacity - UEQ/L for NA" or "... for MICX"
_SAMPLE_TAIL = re.compile(r"\s+for\s+(NA|[A-Z]{2,8})\s*$")

# a trailing "(mean degrees)" / "(%)" / "(cm)" is a unit, not part of the name
_TRAILING_PAREN = re.compile(r"\s*\(([^()]{1,24})\)\s*$")

# "Acid Neutralizing Capacity - UEQ/L"
_TRAILING_DASH_UNIT = re.compile(r"\s+-\s+([A-Za-z%][A-Za-z0-9/%. ]{0,12})\s*$")

# things that read as units rather than as words
_UNIT_WORDS = {
    "%", "percent", "pct", "m", "cm", "mm", "km", "m2", "m3", "msq", "ha",
    "mg/l", "ug/l", "ueq/l", "mg n/l", "mg p/l", "us/cm", "ntu", "pcu",
    "deg c", "degrees", "mean degrees", "count", "n", "ratio", "std units",
    "cfu/100ml", "m3/100m", "number", "proportion",
    "km/sq km", "per sq km", "sq km", "frac", "fraction", "log mm", "log",
    "mean cm", "mean m", "mean %", "mean percent", "days", "years",
}


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""


# the wide chemistry files label every analyte column "Analyte value: <name>"
_TEMPLATE_PREFIX = re.compile(r"^(?:analyte value|value|result)\s*:\s*", re.I)


def strip_sample_tail(text: str) -> str:
    """Drop EPA's ' for <SAMPLE_TYPE>' tail and its 'Analyte value:' prefix.

    Only a name that lost the prefix gets re-capitalized, so a label that is
    already correct keeps its case. "pH" must not become "PH".
    """
    stripped = _TEMPLATE_PREFIX.sub("", text).strip()
    had_prefix = stripped != text.strip()
    out = _SAMPLE_TAIL.sub("", stripped).strip()
    if out and had_prefix:
        out = out[:1].upper() + out[1:]
    return out


def split_units(text: str) -> tuple[str, str]:
    """Lift a trailing unit out of a label into its own field.

    Only a trailing parenthetical or dash tail that actually reads as a unit is
    lifted, so 'Sinuosity of sample reach' and 'Log10(Dgm--Geometric Mean Bed
    Surface Particle Diameter--mm)' keep their text.
    """
    text = text.strip()
    m = _TRAILING_PAREN.search(text)
    if m and m.group(1).strip().lower() in _UNIT_WORDS:
        return text[: m.start()].strip(), m.group(1).strip()
    m = _TRAILING_DASH_UNIT.search(text)
    if m and m.group(1).strip().lower() in _UNIT_WORDS:
        return text[: m.start()].strip(), m.group(1).strip()
    return text, ""


# EPA concatenates several metadata records for one field with a pipe
_PIPE = re.compile(r"\s*\|\s*")

# a trailing literature citation: "(PRK 2008)", "(Kaufmann et al. 1999)"
_CITATION = re.compile(r"\s*\((?:[^()]*\b(?:19|20)\d{2})\)\s*$")

# a formula spelled out after a colon: "QR1=(QRVEG1*QRVEG2*QRDIST1)**(1/3)"
_FORMULA_TAIL = re.compile(r"\s*[:,]\s*[A-Za-z_][A-Za-z0-9_]*\s*=.*$")

# The landscape definitions are paragraphs that name the quantity first and then
# qualify it. Cutting the qualifier leaves the name and costs nothing, because
# the untrimmed text is always kept as ``description``.
_CLAUSE_TAILS = [
    re.compile(r"\s*,?\s+within the (?:upstream watershed|local catchment)\b.*$", re.I),
    re.compile(r"\s*,\s+(?:expressed|estimated|derived|calculated|adjusted|converted|based)\b.*$", re.I),
    re.compile(r"\s*,\s+(?:in|from|using|as)\s+.*$", re.I),
]

# a first sentence that already reads as a name
_SENTENCE = re.compile(r"^(.{15,}?[a-z0-9)\]%])\.\s+[A-Z0-9%]")

MIN_TRIMMED = 15


def shorten_label(text: str) -> str:
    """Mechanical trims that make EPA's definitions read as names.

    Each is conservative and never applied when it would leave a stub: only the
    first of several concatenated metadata records, a trailing literature
    citation, a spelled-out formula, the first sentence of a paragraph, and a
    trailing qualifying clause. The full text is kept as ``description``.
    """
    out = _PIPE.split(text)[0].strip()
    out = _FORMULA_TAIL.sub("", out).strip()
    prev = None
    while prev != out:  # a label can carry both a citation and a unit
        prev = out
        out = _CITATION.sub("", out).strip()

    m = _SENTENCE.match(out)
    if m:
        out = m.group(1).strip()

    for pattern in _CLAUSE_TAILS:
        trimmed = pattern.sub("", out).strip().rstrip(",")
        if len(trimmed) >= MIN_TRIMMED:
            out = trimmed
    return out


def clean_label(text) -> tuple[str, str]:
    """(display name, units) from one raw label. Returns ('', '') when unusable."""
    if _blank(text):
        return "", ""
    out = re.sub(r"\s+", " ", str(text)).strip()
    out = strip_sample_tail(out)
    out = shorten_label(out)
    out = out.rstrip(".").strip()
    if not out:
        return "", ""
    out, units = split_units(out)
    return out.strip(), units.strip()


def short_name_for(name: str, target: int = SHORT_NAME_TARGET) -> str:
    """``name`` truncated at a word boundary, for tile headers and table cells."""
    name = name.strip()
    if len(name) <= target:
        return name
    cut = name[: target + 1]
    space = cut.rfind(" ")
    if space >= target // 2:
        cut = cut[:space]
    else:
        cut = name[:target]
    return cut.rstrip(" ,;:-(/") + "..."


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #

def load_catalog() -> pd.DataFrame:
    cat = pd.read_csv(DATA_DIR / "nrsa_metric_catalog.csv")
    cat["metric_key"] = cat["name"].astype(str)
    return cat


def load_overrides(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v or {}) for k, v in (doc.get("metrics") or {}).items()}


def load_metric_map_labels() -> dict[str, str]:
    doc = yaml.safe_load((CONFIG_DIR / "metric_map.yaml").read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for fn in doc.get("functions") or []:
        for entry in fn.get("metrics") or []:
            code, label = entry.get("code"), entry.get("label")
            if code and label and code not in out:
                out[str(code)] = str(label)
    return out


def load_library_labels() -> dict[str, str]:
    path = CONFIG_DIR / "staf_metric_library.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in doc.get("metrics") or []:
        key, label = entry.get("app_metric_key"), entry.get("label")
        if key and label and key not in out:
            out[str(key)] = str(label)
    return out


def load_streamcat_labels() -> dict[str, str]:
    path = DATA_DIR / "streamcat_metrics.csv"
    if not path.exists():
        return {}
    cat = pd.read_csv(path)
    return {str(r["name"]): str(r["label"]) for _, r in cat.iterrows() if not _blank(r.get("label"))}


def load_direction_notes() -> dict[str, str]:
    """One-sentence descriptions already curated for the response directions."""
    out: dict[str, str] = {}
    for name in ("nrsa_response_directions.yaml", "landscape_response_directions.yaml"):
        path = CONFIG_DIR / name
        if not path.exists():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, entry in (doc.get("metrics") or {}).items():
            note = (entry or {}).get("note")
            if note and not _blank(note):
                out.setdefault(str(key), str(note).strip())
    return out


def load_epa_metadata() -> dict[str, dict[str, str]]:
    """upper-cased EPA short name -> {cycle: definition}, newest usable first."""
    by_name: dict[str, dict[str, str]] = {}
    for cycle in CYCLES:
        for stem in ("metadata_detail", "variable_lookup"):
            path = REFERENCE_DIR / f"{stem}_{cycle}.csv"
            if not path.exists():
                continue
            frame = pd.read_csv(path)
            label_col = next(
                (c for c in ("epa_label_description", "epa_definition_what_it_measures")
                 if c in frame.columns),
                None,
            )
            if label_col is None:
                continue
            for _, row in frame.iterrows():
                name = str(row.get("epa_short_name") or "").strip().upper()
                text = row.get(label_col)
                if not name or _blank(text):
                    continue
                by_name.setdefault(name, {}).setdefault(cycle, str(text).strip())
    return by_name


def epa_lookup(epa: dict[str, dict[str, str]], raw_name: str) -> tuple[str, str]:
    """(definition, cycle) for a catalog raw name, newest cycle first.

    Chemistry columns are ``ANC_RESULT`` in the wide files but the metadata also
    documents the bare analyte, so both spellings are tried.
    """
    upper = str(raw_name).strip().upper()
    candidates = [upper]
    if upper.endswith("_RESULT"):
        candidates.append(upper[: -len("_RESULT")])
    else:
        candidates.append(f"{upper}_RESULT")
    for key in candidates:
        found = epa.get(key)
        if not found:
            continue
        for cycle in CYCLES:
            if cycle in found:
                return found[cycle], cycle
    return "", ""


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def build(overrides_path: Path) -> tuple[pd.DataFrame, list[str]]:
    catalog = load_catalog()
    overrides = load_overrides(overrides_path)
    mm_labels = load_metric_map_labels()
    lib_labels = load_library_labels()
    sc_labels = load_streamcat_labels()
    notes = load_direction_notes()
    epa = load_epa_metadata()

    rows: list[dict] = []
    too_long: list[str] = []

    keys = list(dict.fromkeys(list(catalog["metric_key"]) + list(sc_labels) + list(overrides)))
    cat_by_key = catalog.set_index("metric_key")

    for key in keys:
        cat_row = cat_by_key.loc[key] if key in cat_by_key.index else None
        raw_name = str(cat_row["raw_name"]) if cat_row is not None else key
        category = str(cat_row["category"]) if cat_row is not None else (
            "Landscape" if key in sc_labels else ""
        )
        cat_units = "" if cat_row is None or _blank(cat_row.get("units")) else str(cat_row["units"])

        ov = overrides.get(key) or {}
        name = units = origin = ""
        source_cycle = ""
        epa_text, epa_cycle = epa_lookup(epa, raw_name)

        if not _blank(ov.get("display_name")):
            name, units = clean_label(ov["display_name"])
            origin = "curated"
        elif key in mm_labels:
            name, units = clean_label(mm_labels[key])
            origin = "metric_map"
        elif key in lib_labels:
            name, units = clean_label(lib_labels[key])
            origin = "staf_library"
        elif key in sc_labels:
            name, units = clean_label(sc_labels[key])
            origin = "streamcat"
        elif epa_text:
            name, units = clean_label(epa_text)
            origin = "epa_metadata"
            source_cycle = epa_cycle
        if not name:
            name = str(raw_name)
            origin = origin or "mnemonic"

        if not _blank(ov.get("units")):
            units = str(ov["units"]).strip()
        elif not units and cat_units:
            units = cat_units

        description = ""
        for candidate in (ov.get("description"), notes.get(key), epa_text):
            if not _blank(candidate):
                description = re.sub(r"\s+", " ", str(candidate)).strip()
                break
        if epa_text and not source_cycle:
            source_cycle = epa_cycle

        if len(name) > HARD_MAX_DISPLAY_NAME:
            too_long.append(f"{key}: {len(name)} chars: {name[:80]}...")

        rows.append({
            "metric_key": key,
            "display_name": name,
            "short_name": short_name_for(name),
            "description": description,
            "units": units,
            "category": category,
            "epa_short_name": raw_name,
            "source_cycle": source_cycle,
            "name_origin": origin,
        })

    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values("metric_key")
    return frame.reset_index(drop=True), too_long


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--overrides", type=Path, default=CONFIG_DIR / "metric_names.yaml")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--strict", action="store_true",
                    help="fail when any display name is still a paragraph")
    args = ap.parse_args(argv)

    frame, too_long = build(args.overrides)

    print(f"{len(frame)} metrics")
    print(frame["name_origin"].value_counts().to_string())
    readable = frame[frame.name_origin != "mnemonic"]
    print(f"\nwith a readable name: {len(readable)} / {len(frame)} "
          f"({len(readable) / max(len(frame), 1) * 100:.0f}%)")
    print(f"with units:           {(frame.units.astype(str).str.len() > 0).sum()}")
    print(f"with a description:   {(frame.description.astype(str).str.len() > 0).sum()}")
    lengths = frame.display_name.astype(str).str.len()
    print(f"display name length:  median {int(lengths.median())}, "
          f"p90 {int(lengths.quantile(0.9))}, max {int(lengths.max())}")

    if too_long:
        print(f"\n{len(too_long)} display names exceed {HARD_MAX_DISPLAY_NAME} characters:")
        for line in too_long[:40]:
            print(f"  {line}")
        if len(too_long) > 40:
            print(f"  ... and {len(too_long) - 40} more")
        print("\nThese are EPA definitions that read as paragraphs, almost all "
              "landscape variables the UI does not surface. short_name keeps every "
              "tile and cell readable; add a display_name in config/metric_names.yaml "
              "for any that matters.")
        if args.strict:
            return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8", lineterminator="\n")
    print(f"\nwrote {args.out.relative_to(APP_ROOT).as_posix()} "
          f"({args.out.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
