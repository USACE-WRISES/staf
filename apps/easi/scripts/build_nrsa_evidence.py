"""Build the deterministic NRSA reference-evidence index used by EASI.

The source CSVs remain EPA's canonical records.  This script retains only the
reach-matching fields and measurements EASI can interpret consistently:
wetted-channel percentage, embeddedness, and published benthic/fish MMI
condition classes.  The gzip header uses ``mtime=0`` so identical source data
produce byte-identical output.

Covers all three published survey cycles.  The screen matches on COMID rather
than on a site id, so a cycle EASI has no records for is simply a reach it cannot
evidence: before this covered 2013-14 and 2023-24, none of the 1,327 stations new
in 2023-24 had any evidence at all.

Two things worth knowing about the result:

* ``evidence_for_reach`` applies a ten-year age window, so 2013-14 records are
  currently filtered out at read time.  They are still written, because the window
  is the reader's policy and is a parameter, not a property of the data.
* 2023-24 publishes a COMID for only about a third of its sites.  ``--stations``
  fills the rest from the station table, which carries the COMID a station
  inherits from an earlier cycle.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import gzip
import io
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
# the name is historical: this index now covers all three cycles
DEFAULT_OUTPUT = ROOT / "data" / "nrsa-2018-19-evidence.json.gz"

CYCLE_LABELS = {"1314": "NRSA 2013-14", "1819": "NRSA 2018-19", "2324": "NRSA 2023-24"}

CYCLE_SOURCES = {
    "1314": {
        "site.csv": ("https://www.epa.gov/sites/default/files/2019-05/"
                     "nrsa1314_siteinformation_wide_04292019.csv"),
        "physical.csv": ("https://www.epa.gov/sites/default/files/2019-04/"
                         "nrsa1314_phabmed_04232019.csv"),
        "benthic.csv": ("https://www.epa.gov/sites/default/files/2019-04/"
                        "nrsa1314_bentmmi_04232019.csv"),
        "fish.csv": ("https://www.epa.gov/sites/default/files/2019-04/"
                     "nrsa1314_fishmmi_04232019.csv"),
    },
    "1819": {
        "site.csv": ("https://www.epa.gov/system/files/other-files/2023-01/"
                     "NRSA_1819_SiteInfo.csv"),
        "physical.csv": ("https://www.epa.gov/sites/default/files/2021-04/"
                         "nrsa_1819_physical_habitat_larger_set_of_metrics_-_data.csv"),
        "benthic.csv": ("https://www.epa.gov/system/files/other-files/2025-03/"
                        "nrsa-1819-benthic-macroinvertebrate-mmi-data.csv"),
        "fish.csv": ("https://www.epa.gov/system/files/other-files/2025-03/"
                     "nrsa-1819-fish-mmi-data.csv"),
    },
    "2324": {
        "site.csv": ("https://www.epa.gov/system/files/other-files/2026-06/"
                     "nrsa2324_siteinfo.csv"),
        "physical.csv": ("https://www.epa.gov/system/files/other-files/2026-06/"
                         "nrsa2324_physicalhabitat_metrics.csv"),
        "benthic.csv": ("https://www.epa.gov/system/files/other-files/2026-06/"
                        "nrsa2324_benthicmmi.csv"),
        "fish.csv": ("https://www.epa.gov/system/files/other-files/2026-06/"
                     "nrsa2324_fishmmi.csv"),
    },
}

# what the single-cycle build used before this covered all three
SOURCES = CYCLE_SOURCES["1819"]


def _read(path: Path) -> dict[str, dict[str, str]]:
    """Keyed by UID. EPA ships some of these as latin-1 rather than UTF-8, so a
    decode failure falls back rather than aborting the build."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return {row["UID"].strip(): row for row in csv.DictReader(handle)
                        if row.get("UID") and row["UID"].strip()}
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode {path}")


def _number(value: str | None, *, minimum: float | None = None,
            maximum: float | None = None) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return round(result, 10)


def _integer(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number > 0 else None


def _date(value: str | None) -> str | None:
    text = str(value or "").strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _condition(value: str | None) -> str | None:
    text = str(value or "").strip().title()
    return text if text in {"Good", "Fair", "Poor"} else None


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "STAF-EASI/NRSA-evidence-builder"})
    with urlopen(request, timeout=90) as response:  # noqa: S310 - fixed EPA URLs
        destination.write_bytes(response.read())


def build_records(source_dir: Path, *, cycle: str = "1819",
                  comid_by_site: dict[str, int] | None = None) -> list[dict]:
    """Records for one cycle. ``comid_by_site`` fills a site whose own COMID is
    blank, which is most of 2023-24."""
    sites = _read(source_dir / "site.csv")
    physical = _read(source_dir / "physical.csv")
    benthic = _read(source_dir / "benthic.csv")
    fish = _read(source_dir / "fish.csv")
    records = []
    comid_by_site = comid_by_site or {}
    for uid, site in sites.items():
        comid = _integer(site.get("COMID"))
        if comid is None:
            comid = comid_by_site.get(str(site.get("SITE_ID") or "").strip())
        lat = _number(site.get("LAT_DD83"), minimum=-90, maximum=90)
        lon = _number(site.get("LON_DD83"), minimum=-180, maximum=180)
        sample_date = _date(site.get("DATE_COL"))
        if comid is None or lat is None or lon is None or sample_date is None:
            continue

        habitat = physical.get(uid, {})
        protocol = str(habitat.get("PROTOCOL") or "").strip().upper()
        if protocol and protocol not in {"WADEABLE", "BOATABLE"}:
            continue
        pct_dry = _number(habitat.get("PCT_DR"), minimum=0, maximum=100)
        embeddedness = _number(habitat.get("XEMBED"), minimum=0, maximum=100)
        benthic_row, fish_row = benthic.get(uid, {}), fish.get(uid, {})
        benthic_class = _condition(benthic_row.get("BENT_MMI_COND"))
        fish_class = _condition(fish_row.get("FISH_MMI_COND"))
        if all(value is None for value in
               (pct_dry, embeddedness, benthic_class, fish_class)):
            continue
        records.append({
            "uid": uid,
            "cycle": cycle,
            "siteId": site.get("SITE_ID") or "",
            "date": sample_date,
            "visit": _integer(site.get("VISIT_NO")),
            "comid": comid,
            "lat": lat,
            "lon": lon,
            "huc8": str(site.get("HUC8") or "").removeprefix("H"),
            "name": site.get("NARS_NAME") or site.get("GNIS_NAME") or "",
            "protocol": protocol or None,
            "pctDry": pct_dry,
            "wettedPct": None if pct_dry is None else round(100.0 - pct_dry, 10),
            "embeddednessPct": embeddedness,
            "benthicClass": benthic_class,
            "benthicMmi": _number(benthic_row.get("MMI_BENT"), minimum=0),
            "fishClass": fish_class,
            "fishMmi": _number(fish_row.get("MMI_FISH"), minimum=0),
        })

    return records


def write_index(records: list[dict], output: Path, cycles: list[str]) -> dict:
    """One index over every cycle asked for, written byte-reproducibly."""
    records = sorted(records, key=lambda item: (item["comid"], item["date"],
                                                item.get("cycle", ""), item["uid"]))
    payload = {
        "schemaVersion": 2,
        "survey": ", ".join(CYCLE_LABELS[c] for c in cycles),
        "cycles": list(cycles),
        "sources": [{"cycle": c, "file": name, "url": url}
                    for c in cycles for name, url in CYCLE_SOURCES[c].items()],
        "records": records,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as zipped:
            zipped.write(raw)
    by_cycle: dict[str, int] = {}
    for record in records:
        key = str(record.get("cycle") or "?")
        by_cycle[key] = by_cycle.get(key, 0) + 1
    return {"records": len(records), "bytes": output.stat().st_size, "byCycle": by_cycle}


# dataset ids in the StreamCurves archive lock, per file this builder wants
ARCHIVE_DATASETS = {
    "site.csv": "SITE_INFO",
    "physical.csv": {"1314": "PHAB_LARGER", "1819": "PHAB", "2324": "PHAB"},
    "benthic.csv": "BENTHIC_MMI",
    "fish.csv": "FISH_MMI",
}


def _archive_sources(archive: Path, cycle: str) -> dict[str, Path]:
    """Map this builder's four file slots onto the already-downloaded archive."""
    lock = json.loads((archive / "lock" / "sources.lock.json").read_text(encoding="utf-8"))
    by_id = {(r["cycle"], r["dataset_id"]): r["file"]
             for r in lock["files"].values() if r["kind"] == "data"}
    out = {}
    for slot, dataset in ARCHIVE_DATASETS.items():
        dataset_id = dataset[cycle] if isinstance(dataset, dict) else dataset
        name = by_id.get((cycle, dataset_id))
        if name:
            out[slot] = archive / "raw" / cycle / name
    return out


def _station_comids(path: Path) -> dict[str, int]:
    """site_id -> the COMID its station carries, for filling a blank one."""
    import pandas as pd

    visits = pd.read_parquet(path / "site_visits.parquet")
    stations = pd.read_parquet(path / "stations.parquet").set_index("station_key")
    comid = visits["station_key"].map(stations["comid"])
    out: dict[str, int] = {}
    for site_id, value in zip(visits["site_id"].astype(str), comid):
        if value is not None and not pd.isna(value):
            out.setdefault(site_id.strip(), int(value))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path,
                        help="one cycle's four CSVs, for a single-cycle build")
    parser.add_argument("--archive", type=Path,
                        help="a StreamCurves NRSA archive: <archive>/raw/<cycle>/*.csv, "
                             "<archive>/lock/sources.lock.json, and optionally the "
                             "station tables beside them")
    parser.add_argument("--cycle", action="append", choices=sorted(CYCLE_SOURCES),
                        help="repeatable; defaults to every cycle")
    parser.add_argument("--stations", type=Path,
                        help="directory holding stations.parquet and site_visits.parquet, "
                             "used to fill a site whose own COMID is blank")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cycles = args.cycle or sorted(CYCLE_SOURCES)
    comid_by_site = _station_comids(args.stations) if args.stations else {}
    if comid_by_site:
        print(f"station COMIDs available for {len(comid_by_site):,} site ids")

    records: list[dict] = []
    for cycle in cycles:
        if args.archive:
            sources = _archive_sources(args.archive, cycle)
            missing = [k for k in ARCHIVE_DATASETS if k not in sources]
            if missing:
                raise SystemExit(f"{cycle}: archive is missing {missing}")
            with tempfile.TemporaryDirectory(prefix=f"easi-nrsa-{cycle}-") as temp:
                staged = Path(temp)
                for slot, path in sources.items():
                    (staged / slot).write_bytes(path.read_bytes())
                found = build_records(staged, cycle=cycle, comid_by_site=comid_by_site)
        elif args.source_dir:
            found = build_records(args.source_dir, cycle=cycle,
                                  comid_by_site=comid_by_site)
        else:
            with tempfile.TemporaryDirectory(prefix=f"easi-nrsa-{cycle}-") as temp:
                staged = Path(temp)
                for name, url in CYCLE_SOURCES[cycle].items():
                    _download(url, staged / name)
                found = build_records(staged, cycle=cycle, comid_by_site=comid_by_site)
        print(f"  {CYCLE_LABELS[cycle]}: {len(found):,} records")
        records.extend(found)

    result = write_index(records, args.output, cycles)
    print(f"wrote {result['records']:,} records ({result['bytes']:,} bytes) to {args.output}")
    print(f"  by cycle: {result['byCycle']}")
    print("  note: evidence_for_reach applies a ten-year age window, so 2013-14 "
          "records are filtered out at read time today")


if __name__ == "__main__":
    main()
