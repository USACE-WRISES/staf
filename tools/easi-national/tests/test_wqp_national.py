"""The national monthly WQP pull: windows, the trailer and cut handling with
the piecewise split, the retry loop with its ledger, and the combine."""
from __future__ import annotations

import json
from datetime import date

import pyarrow.parquet as pq
import pytest
import requests

from builder import state
from builder.paths import DataRoot
from builder.stages import wqp_national as wn

HEADER = "Org_Identifier,Location_Identifier,Activity_StartDate,Result_Characteristic,Result_Measure,Result_MeasureIdentifier"


class FakeResponse:
    def __init__(self, status: int, body: bytes, cut: bool = False):
        self.status_code = status
        self._body = body
        self._cut = cut
        self.text = body.decode("utf-8", "replace")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), 7):
            yield self._body[i:i + 7]
        if self._cut:
            raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Answers per window ``"MM-DD-YYYY..MM-DD-YYYY"`` from a script of
    ``(status, body)``, ``("cut", body)`` or an exception; ``"*"`` is the
    fallback; a list is consumed one answer per call."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    def get(self, url, params=None, **kw):
        p = dict(params)
        key = f"{p['startDateLo']}..{p['startDateHi']}"
        self.calls.append(key)
        answers = self.script.get(key, self.script.get("*"))
        if answers is None or answers == []:
            raise AssertionError(f"no scripted answer for {key}")
        answer = answers.pop(0) if isinstance(answers, list) else answers
        if isinstance(answer, Exception):
            raise answer
        if answer[0] == "cut":
            return FakeResponse(200, answer[1], cut=True)
        return FakeResponse(*answer)


def _csv(rows):
    return (HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


GOOD_A = _csv(["A,S1,2016-09-02,Phosphorus,0.1,STORET-1", "A,S2,2016-09-03,Nitrogen,1.2,STORET-2"])
GOOD_B = _csv(["A,S3,2016-09-20,Phosphorus,0.3,STORET-5"])
CUT = ("cut", _csv(["A,S1,2016-09-02,Phosphorus,0.1,STORET-1"]))
TRAILER = (200, _csv(["A,S1,2016-09-02,Phosphorus,0.1,STORET-1",
                      "ERROR: INCOMPLETE DATA - THE RESULTS FOR THIS REQUEST ARE NOT COMPLETE AND MORE DATA IS "
                      "LIKELY AVAILABLE.  PLEASE RETRY THE REQUEST."]))
OVERLOADED = (500, b"Server Error: Unable to get the headers for this request. The system may be overloaded.")


def test_months_windows_and_params_are_calendar_months_inclusive():
    assert wn.months("2016-09", "2017-01") == ["2016-09", "2016-10", "2016-11", "2016-12", "2017-01"]
    assert wn.window("2016-09") == ("09-01-2016", "09-30-2016")
    assert wn.window("2024-02") == ("02-01-2024", "02-29-2024")
    assert wn.month_bounds("2016-09") == (date(2016, 9, 1), date(2016, 9, 30))
    p = dict(wn.params_for("2016-09"))
    assert p["dataProfile"] == "fullPhysChem" and p["mimeType"] == "csv" and p["siteType"] == "Stream"
    assert [v for k, v in wn.params_for("2016-09") if k == "characteristicName"] == list(wn.CHARACTERISTICS)
    assert dict(wn.params_for_window(date(2016, 9, 16), date(2016, 9, 30)))["startDateLo"] == "09-16-2016"


def test_a_cut_month_is_halved_and_stitched(tmp_path):
    session = FakeSession({"09-01-2016..09-30-2016": CUT, "09-01-2016..09-15-2016": (200, GOOD_A),
                           "09-16-2016..09-30-2016": (200, GOOD_B)})
    dest = tmp_path / "wqx3_2016-09.csv"
    result = wn.download_month("2016-09", dest, session=session)
    assert result["status"] == "done" and result["rows"] == 3 and result["pieces"] == 2
    assert result["bytes"] == len(CUT[1]) + len(GOOD_A) + len(GOOD_B)
    text = dest.read_text(encoding="utf-8")
    assert text.count(HEADER) == 1 and "STORET-5" in text and text.endswith("\n")
    assert not list(tmp_path.glob("*.piece_*")) and not list(tmp_path.glob("*.part"))
    assert session.calls == ["09-01-2016..09-30-2016", "09-01-2016..09-15-2016", "09-16-2016..09-30-2016"]


def test_the_trailer_splits_too_and_overloaded_answers_fail_the_month(tmp_path):
    session = FakeSession({"10-01-2016..10-31-2016": TRAILER, "*": (200, GOOD_A)})
    result = wn.download_month("2016-10", tmp_path / "wqx3_2016-10.csv", session=session)
    assert result["status"] == "done" and result["pieces"] == 2 and result["rows"] == 4
    session = FakeSession({"*": OVERLOADED})
    waited = []
    result = wn.download_month("2016-11", tmp_path / "wqx3_2016-11.csv", session=session, sleep=waited.append)
    assert result["status"] == "failed" and result["error"].startswith("2016-11-01 to 2016-11-30: HTTP 500")
    assert not (tmp_path / "wqx3_2016-11.csv").exists()
    assert len(session.calls) == 3 and sum(waited) == 60          # three tries, 20 s then 40 s apart
    session = FakeSession({"*": ConnectionError("Connection was reset")})
    result = wn.download_month("2016-12", tmp_path / "wqx3_2016-12.csv", session=session)
    assert result["status"] == "failed" and "Connection was reset" in result["error"]


def test_the_one_hour_cap_never_splits_a_window(tmp_path):
    session = FakeSession({"*": (200, GOOD_A)})
    result = wn.download_month("2016-09", tmp_path / "wqx3_2016-09.csv", session=session, max_s=-1)  # the cap is already past
    assert result["status"] == "slow" and "retried whole in a later pass" in result["error"]
    assert session.calls == ["09-01-2016..09-30-2016"]          # no halves requested
    assert not list(tmp_path.glob("*.split_*")) and not list(tmp_path.glob("*.piece_*"))
    assert not (tmp_path / "wqx3_2016-09.csv").exists()


def test_finished_pieces_survive_a_failed_attempt(tmp_path):
    dest = tmp_path / "wqx3_2016-09.csv"
    first = FakeSession({"09-01-2016..09-30-2016": CUT, "09-01-2016..09-15-2016": (200, GOOD_B),
                         "09-16-2016..09-30-2016": CUT, "09-16-2016..09-22-2016": CUT})
    result = wn.download_month("2016-09", dest, session=first, min_days=8)
    assert result["status"] == "cut" and result["error"].startswith("2016-09-16 to 2016-09-22")
    assert result["pieces"] == 1 and not dest.exists()
    assert (tmp_path / "wqx3_2016-09.piece_20160901_20160915.csv").exists()   # kept for the next attempt
    assert (tmp_path / "wqx3_2016-09.split_20160916_20160930").exists()      # and where the split got to
    second = FakeSession({"*": (200, GOOD_A)})
    result = wn.download_month("2016-09", dest, session=second, min_days=8)
    assert result["status"] == "done" and result["pieces"] == 3 and result["rows"] == 5
    assert second.calls == ["09-16-2016..09-22-2016", "09-23-2016..09-30-2016"]   # resumed below the split
    assert not list(tmp_path.glob("*.piece_*")) and not list(tmp_path.glob("*.split_*"))


def test_run_retries_until_every_month_is_done_then_combines(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    good2 = _csv(["A,S1,2016-10-02,Phosphorus,0.2,STORET-3", "A,S2,2016-09-03,Nitrogen,1.2,STORET-2"])   # a duplicate
    session = FakeSession({"09-01-2016..09-30-2016": (200, GOOD_A),
                           "10-01-2016..10-31-2016": [OVERLOADED, CUT],
                           "10-01-2016..10-15-2016": (200, good2), "10-16-2016..10-31-2016": (200, _csv([]))})
    waited = []
    progress = state.Progress(root, quiet=True)
    result = wn.run_wqp_monthly(root, progress, state.Control(root), workers=2, wanted=["2016-09", "2016-10"],
                                session_factory=lambda: session, sleep=lambda s: waited.append(s), max_rounds=5)
    assert result["done"] == 2 and result["total"] == 2 and result["combined"]
    ledger = wn.read_ledger(root)["months"]
    assert ledger["2016-09"]["status"] == "done" and ledger["2016-09"]["rows"] == 2 and ledger["2016-09"]["attempts"] == 1
    assert ledger["2016-10"]["status"] == "done" and ledger["2016-10"]["attempts"] == 1 and ledger["2016-10"]["pieces"] == 2
    assert session.calls.count("10-01-2016..10-31-2016") == 2
    assert sum(waited) == 20                              # the in-month wait after the overloaded answer
    table = pq.read_table(wn.combined_path(root))
    assert table.num_rows == 3 and table.column("source_month").to_pylist() == ["2016-09", "2016-09", "2016-10"]
    assert set(table.column("Result_MeasureIdentifier").to_pylist()) == {"STORET-1", "STORET-2", "STORET-3"}
    assert all(str(t) == "string" for t in table.schema.types)
    assert not wn.month_path(root, "2016-09").exists()   # the CSVs went once the parquet was verified
    combined = json.loads((wn.folder(root) / "combined.json").read_text(encoding="utf-8"))
    assert combined["rows_read"] == 4 and combined["rows_kept"] == 3
    s = wn.summary(root, ["2016-09", "2016-10"])
    assert s["done"] == 2 and s["rows"] == 4 and s["combined"]


def test_no_combine_keeps_the_csvs_and_a_pause_leaves_pending_months(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    session = FakeSession({"*": (200, GOOD_A)})
    result = wn.run_wqp_monthly(root, state.Progress(root, quiet=True), state.Control(root), workers=1,
                                wanted=["2016-09"], session_factory=lambda: session, combine_when_done=False,
                                max_rounds=3)
    assert result["done"] == 1 and not result["combined"] and wn.month_path(root, "2016-09").exists()
    session = FakeSession({"*": OVERLOADED})
    control = state.Control(root)

    def pausing_sleep(s):
        control.request("pause")
    with pytest.raises(state.PauseRequested):
        wn.run_wqp_monthly(root, state.Progress(root, quiet=True), control, workers=1, wanted=["2016-10"],
                           session_factory=lambda: session, sleep=pausing_sleep)
    entry = wn.read_ledger(root)["months"]["2016-10"]
    assert entry["status"] == "pending" and entry["attempts"] == 1
