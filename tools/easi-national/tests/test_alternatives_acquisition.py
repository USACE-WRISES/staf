import hashlib
import json
import struct
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import acquisition as a


def dbf_bytes(rows):
    fields = [("Gage_no", 15), ("COMID", 12), ("QC_final", 15)]
    header = 32 + 32 * len(fields) + 1
    length = 1 + sum(size for _, size in fields)
    data = bytearray(header)
    data[0] = 3
    struct.pack_into("<IHH", data, 4, len(rows), header, length)
    for index, (name, size) in enumerate(fields):
        offset = 32 + index * 32
        data[offset:offset + len(name)] = name.encode()
        data[offset + 11] = ord("C")
        data[offset + 16] = size
    data[-1] = 13
    for row in rows:
        data.extend(b" " + b"".join(str(row.get(name, "")).encode().ljust(size) for name, size in fields))
    return bytes(data)


def test_exact_matches_preserve_leading_zero_and_no_nearest_or_moved_substitution(tmp_path):
    p = tmp_path / "gages.dbf"
    p.write_bytes(dbf_bytes([{"Gage_no": "00123456", "COMID": "11", "QC_final": "reviewed"},
                            {"Gage_no": "00987654", "COMID": "12", "QC_final": "reviewed"}]))
    gages = a.dbf_rows(p)
    gages.append({"Gage_no": "00223344", "COMID": "88", "MovedCOMID": "13"})
    rows = a.exact_matches([{"station_key": "one", "comid": 11}, {"station_key": "two", "comid": 13},
                           {"station_key": "three", "comid": None}], gages)
    assert [(r["gage_no"], r["match_status"]) for r in rows] == [
        ("00123456", "exact_comid"), (None, "unmatched"), (None, "missing_comid")]
    assert rows[0]["gage_metadata"]["qc_final"] == "reviewed"


def test_cache_resume_and_corruption_are_content_bound(tmp_path):
    class Response:
        url = "https://www.epa.gov/test"
        status_code = 200
        headers = {"Content-Type": "text/plain"}
        def raise_for_status(self): pass
        def iter_content(self, size): return iter([b"source"])
        def close(self): pass
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()
    session = Session()
    cache = a.Cache(tmp_path, session)
    path = cache.fetch(Response.url, "source.txt")
    assert cache.fetch(Response.url, "source.txt") == path
    assert session.calls == 1
    assert cache.sources[0]["sha256"] == hashlib.sha256(b"source").hexdigest()
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="Cached source changed"):
        cache.fetch(Response.url, "source.txt")
    with pytest.raises(ValueError, match="official"):
        cache.fetch("https://example.com/data", "other")
    with pytest.raises(ValueError, match="escapes"):
        cache.fetch(Response.url, "../outside")


def test_bounded_failed_request_can_resume(tmp_path, monkeypatch):
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            raise OSError("temporary transport failure")
    session = Session()
    monkeypatch.setattr(a.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="3 attempts"):
        a.Cache(tmp_path, session).fetch(a.EPA_UPDATES, "page")
    assert session.calls == 3
    assert json.loads((tmp_path / "page.source.json").read_text())["status"] == "unavailable"


def test_rate_limit_stops_host_requests_but_cached_sources_remain_usable(tmp_path, monkeypatch):
    import requests
    class Response:
        status_code = 429
        url = a.DAILY
        headers = {"Retry-After": "3600", "X-RateLimit-Remaining": "0"}
        def raise_for_status(self): raise requests.HTTPError("429")
        def close(self): pass
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()
    waits = []
    monkeypatch.setattr(a.time, "sleep", waits.append)
    session = Session()
    cache = a.Cache(tmp_path, session)
    with pytest.raises(RuntimeError, match="1 attempts"):
        cache.fetch(a.DAILY, "one.json")
    with pytest.raises(RuntimeError, match="deferred"):
        cache.fetch(a.DAILY + "?other", "two.json")
    assert session.calls == 1 and waits == [1.]
    receipt = json.loads((tmp_path / "one.json.source.json").read_text())
    assert receipt["http_status"] == 429 and receipt["retry_after_seconds"] == 3600
    assert json.loads((tmp_path / "two.json.source.json").read_text())["attempts"] == 0
    (tmp_path / "cached.json").write_bytes(b"{}"); digest = hashlib.sha256(b"{}").hexdigest()
    (tmp_path / "cached.json.source.json").write_text(json.dumps({"url": a.DAILY, "sha256": digest}))
    assert cache.fetch(a.DAILY, "cached.json").read_bytes() == b"{}"
    assert session.calls == 1


def test_short_retry_after_is_honored_once_with_bounded_wait(tmp_path, monkeypatch):
    import requests
    class Response:
        status_code = 429
        url = a.DAILY
        headers = {"Retry-After": "5"}
        def raise_for_status(self): raise requests.HTTPError("429")
        def close(self): pass
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()
    waits = []
    monkeypatch.setattr(a.time, "sleep", waits.append)
    session = Session()
    with pytest.raises(RuntimeError, match="2 attempts"):
        a.Cache(tmp_path, session).fetch(a.DAILY, "one.json")
    assert session.calls == 2 and waits == [1., 5., 1.]


def test_corrected_comid_and_qc_notes_determine_primary_eligibility():
    assert a.gage_qc({"COMID": "11", "MovedCOMID": "12", "QC_final": "Move"})["primary_eligible"]
    assert a.gage_qc({"COMID": "11", "QC_final": "OK - Shift"})["primary_eligible"]
    for final, notes in [("OK", "Drop - powerplant no NHD flowline"), ("Move", "Revisit-uncertain"),
                         ("Move", "Not representative of what's going on there.")]:
        result = a.gage_qc({"comid": "11", "qc_final": final, "rev_notes2": notes})
        assert not result["primary_eligible"] and "unresolved_adverse_review_notes" in result["qc_exclusion_reasons"]
    assert not a.gage_qc({"comid": "-9999", "qc_final": "Drop"})["primary_eligible"]
    assert not a.gage_qc(None)["primary_eligible"]


def feature(site="USGS-00123456", date="2000-01-01", identity="first"):
    return {"id": identity, "properties": {"monitoring_location_id": site, "parameter_code": "00060",
            "statistic_id": "00003", "time": date, "value": "0.0", "unit_of_measure": "ft^3/s",
            "qualifier": ["e"], "approval_status": "Approved", "time_series_id": "series"}}


def test_daily_pagination_and_exact_scope_validation(tmp_path):
    class Cache:
        def __init__(self, pages): self.pages, self.urls = pages, []
        def fetch(self, url, relative):
            self.urls.append(url)
            path = tmp_path / f"page-{len(self.urls)}.json"
            path.write_text(json.dumps(self.pages[len(self.urls)-1]))
            return path
    second = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items?cursor=next"
    cache = Cache([{"type": "FeatureCollection", "features": [feature()], "links": [{"rel": "next", "href": second}]},
                   {"type": "FeatureCollection", "features": [feature(date="2024-12-31", identity="second")], "links": []}])
    rows = a.daily_values(cache, "00123456")
    assert len(rows) == 2 and rows[0]["value"] == "0.0" and rows[0]["qualifier"] == ["e"]
    assert "parameter_code=00060" in cache.urls[0] and "statistic_id=00003" in cache.urls[0]
    assert "time=2000-01-01%2F2024-12-31" in cache.urls[0]
    for wrong in [feature(site="USGS-00999999"), feature(date="2025-01-01")]:
        with pytest.raises(ValueError, match="out-of-scope"):
            a.daily_values(Cache([{"type": "FeatureCollection", "features": [wrong]}]), "00123456")


def test_survey_identifiers_do_not_become_confirmed_model_training_roster(tmp_path):
    sites, bent = tmp_path / "sites.csv", tmp_path / "bent.csv"
    sites.write_text("UID,SITE_ID,MASTER_SITEID,VISIT_NO,YEAR,INDEX_VISIT\n1,FW08A,OLD1,1,2008,YES\n2,LATER,LATER,1,2013,YES\n")
    bent.write_text("UID,SITE_ID,VISIT_NO,BENT_MMI_COND\n1,FW08A,1,Good\n")
    rows = a.training_identifiers(sites, bent)
    assert len(rows) == 1 and rows[0]["benthic_condition"] == "Good"
    assert rows[0]["confirmed_model_training_member"] is None
    assert rows[0]["identifier_basis"] == "published_0809_survey_site_not_confirmed_training_roster"


def test_acquire_fetches_daily_only_for_exact_evaluation_comids(tmp_path, monkeypatch):
    root, study = tmp_path, tmp_path / "review/alternative-studies/test"
    (study / "cohorts").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"station_key": "a", "comid": 11, "site_id": "FW08A"},
                                       {"station_key": "b", "comid": 33, "site_id": "OTHER"}]), study / "cohorts/observations.parquet")
    source = root / "analysis/untouched.json"
    source.parent.mkdir()
    source.write_text('{"current":true}')
    before = source.read_bytes()
    payloads = {
        "epa/sites_0809.csv": b"UID,SITE_ID,MASTER_SITEID,VISIT_NO,YEAR\n1,FW08A,OLD1,1,2008\n",
        "epa/benthos_0809.csv": b"UID,SITE_ID,VISIT_NO,BENT_MMI_COND\n1,FW08A,1,Good\n",
        "usgs/item.json": json.dumps({"facets": [{"files": [{"name": "SWIM_gage_loc.dbf", "url": "https://www.sciencebase.gov/test.dbf"}]}]}).encode(),
        "usgs/SWIM_gage_loc.dbf": dbf_bytes([{"Gage_no": "00123456", "COMID": "11"}, {"Gage_no": "00999999", "COMID": "99"}]),
    }
    def fetch(self, url, relative, **kwargs):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads.get(relative, b"official metadata"))
        return path
    calls = []
    def daily(cache, gage):
        calls.append(gage)
        return []
    monkeypatch.setattr(a.Cache, "fetch", fetch)
    monkeypatch.setattr(a, "daily_values", daily)
    result = a.acquire(root, study)
    assert calls == ["00123456"]
    assert result["matched_station_keys"] == 1 and result["unmatched_station_keys"] == 1
    assert result["model_training_roster"] == "unavailable" and result["model_independence"] == "unknown"
    assert source.read_bytes() == before
    overlap = json.loads((study / "acquisition/model-independence.json").read_text())["rows"]
    assert overlap[0]["training_era_identifier_match"] == ["FW08A"]
    assert all(r["independence"] == "unknown" for r in overlap)
    with pytest.raises(ValueError):
        a.acquire(root, root / "analysis")
