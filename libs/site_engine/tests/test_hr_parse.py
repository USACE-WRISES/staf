"""HR feature parsing: typed conversion, sentinel guards, and parity with the
EASI copy when the source tree is present."""
from __future__ import annotations

from pathlib import Path

import pytest

from site_engine import hr

_EASI = Path(__file__).resolve().parents[3] / "apps" / "easi"


def _feat(**props) -> dict:
    base = {
        "nhdplusid": 24000800021917.0, "gnis_name": "Rush Run",
        "reachcode": "05060001001737", "lengthkm": 1.2, "totdasqkm": 2.7176,
        "slope": 0.01767, "fcode": 46003, "ftype": 460, "streamorde": 1,
        "hydroseq": 24000800000444.0, "uphydroseq": 24000800000455.0,
        "dnhydroseq": 24000800000440.0, "vpuid": "0506", "innetwork": 1,
    }
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.02, 40.09], [-83.01, 40.10]]}}


def test_parse_types_and_sentinels():
    rec = hr.parse_feature(_feat())
    assert rec["nhdplusid"] == 24000800021917 and isinstance(rec["nhdplusid"], int)
    assert rec["totdasqkm"] == 2.7176 and rec["stream_order"] == 1
    assert hr.parse_feature(_feat(slope=-9998))["slope"] is None
    assert hr.parse_feature(_feat(totdasqkm=0))["totdasqkm"] is None
    assert hr.parse_feature(_feat(nhdplusid=None)) is None
    assert hr.parse_feature(None) is None


@pytest.mark.skipif(not _EASI.is_dir(), reason="EASI source not present")
def test_parse_parity_with_easi():
    # The engine's record is a superset (it adds EROM qama); every field the
    # EASI parser produces must match exactly.
    import sys
    sys.path.insert(0, str(_EASI))
    from easi.datasources import nhd_hr as easi_hr

    for f in (_feat(), _feat(slope=-9998), _feat(totdasqkm=0),
              _feat(gnis_name="  "), _feat(uphydroseq=0)):
        ours = hr.parse_feature(f)
        theirs = easi_hr.parse_feature(f)
        assert {k: ours[k] for k in theirs} == theirs
