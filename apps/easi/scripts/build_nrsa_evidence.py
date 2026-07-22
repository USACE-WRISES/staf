"""Build the deterministic NRSA 2018-19 evidence index used by EASI.

The source CSVs remain EPA's canonical records.  This script retains only the
reach-matching fields and measurements EASI can interpret consistently:
wetted-channel percentage, embeddedness, and published benthic/fish MMI
condition classes.  The gzip header uses ``mtime=0`` so identical source data
produce byte-identical output.
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
DEFAULT_OUTPUT = ROOT / "data" / "nrsa-2018-19-evidence.json.gz"
SOURCES = {
    "site.csv": (
        "https://www.epa.gov/system/files/other-files/2023-01/"
        "NRSA_1819_SiteInfo.csv"),
    "physical.csv": (
        "https://www.epa.gov/sites/default/files/2021-04/"
        "nrsa_1819_physical_habitat_larger_set_of_metrics_-_data.csv"),
    "benthic.csv": (
        "https://www.epa.gov/system/files/other-files/2025-03/"
        "nrsa-1819-benthic-macroinvertebrate-mmi-data.csv"),
    "fish.csv": (
        "https://www.epa.gov/system/files/other-files/2025-03/"
        "nrsa-1819-fish-mmi-data.csv"),
}


def _read(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["UID"].strip(): row for row in csv.DictReader(handle)
                if row.get("UID") and row["UID"].strip()}


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


def build(source_dir: Path, output: Path) -> dict:
    sites = _read(source_dir / "site.csv")
    physical = _read(source_dir / "physical.csv")
    benthic = _read(source_dir / "benthic.csv")
    fish = _read(source_dir / "fish.csv")
    records = []
    for uid, site in sites.items():
        comid = _integer(site.get("COMID"))
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

    records.sort(key=lambda item: (item["comid"], item["date"], item["uid"]))
    payload = {
        "schemaVersion": 1,
        "survey": "NRSA 2018-19",
        "sources": [{"file": name, "url": url} for name, url in SOURCES.items()],
        "records": records,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as zipped:
            zipped.write(raw)
    return {"records": len(records), "bytes": output.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.source_dir:
        result = build(args.source_dir, args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="easi-nrsa-") as temp:
            source_dir = Path(temp)
            for name, url in SOURCES.items():
                _download(url, source_dir / name)
            result = build(source_dir, args.output)
    print(f"wrote {result['records']} records ({result['bytes']} bytes) to {args.output}")


if __name__ == "__main__":
    main()
