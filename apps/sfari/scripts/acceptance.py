"""End-to-end acceptance harness.

Delineates + pulls desktop evidence for a set of diverse CONUS sites (including the
SFARI document's case-study regions: New Hampshire, Colorado, Texas) and prints a
sanity summary — a repeatable smoke test that the full pipeline works across
physiographic regions, not just one reach. Live network; run manually::

    D:/Code/Work/easi_claude/.venv/Scripts/python.exe scripts/acceptance.py

Coordinates are approximate on-network points; delineation snaps to the nearest
NHD COMID. A site that fails to snap (off-network / out-of-CONUS) is reported, not
fatal — that is the intended graceful degradation.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sfari import evidence, pipeline  # noqa: E402

SITES = [
    ("Scioto River @ Columbus OH — urban lowland (Interior Plains)", 39.95553, -83.00790),
    ("Mink Brook, Hanover NH — headwater (case study; Appalachian)", 43.68700, -72.25700),
    ("Cache la Poudre R., Fort Collins CO — montane (case study; Rockies)", 40.66140, -105.22470),
    ("Marys Creek nr Fort Worth TX — (case study region; Interior Plains)", 32.70500, -97.45200),
]


async def run_site(name: str, lat: float, lon: float) -> str:
    try:
        d = await pipeline.delineate_only(lat, lon)
    except Exception as exc:  # noqa: BLE001
        return f"{name}\n   DELINEATION ERROR: {exc}"
    if d.get("status") != "ok":
        return f"{name}\n   no NHD stream snapped ({d.get('message', '')[:60]})"
    dl = d["delineation"]
    try:
        ev = await evidence.pull(d["ctx_inputs"])
    except Exception as exc:  # noqa: BLE001
        return f"{name}\n   EVIDENCE ERROR: {exc}"
    ok = sum(1 for r in ev.values() if r.get("status") == "ok")
    sug = sum(1 for r in ev.values() if r.get("suggested_likert"))
    return (f"{name}\n   {dl.get('gnis_name')}  COMID {dl.get('comid')}  "
            f"DA {dl.get('drainage_area_sqkm')} km²  slope {dl.get('slope')}\n"
            f"   evidence: {ok}/{len(ev)} available, {sug} with a suggested Likert")


async def main():
    print("SFARI acceptance harness — delineate + evidence across regions\n" + "=" * 70)
    for name, lat, lon in SITES:
        print(await run_site(name, lat, lon))


if __name__ == "__main__":
    asyncio.run(main())
