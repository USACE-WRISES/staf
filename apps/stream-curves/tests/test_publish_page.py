"""Publish page contract: the redesigned layout keeps every input id the
publish handler reads, the gate note surfaces the actionable reason, the
name autofill effect exists, and the page copy stays em-dash free."""

from __future__ import annotations

from pathlib import Path

import views.publish as pub

SRC = Path(pub.__file__).read_text(encoding="utf-8")

#: Every id _publish reads (plus the buttons/downloads the page must emit).
HANDLER_IDS = (
    "save_level", "pub_assessment", "pub_new_id", "pub_name",
    "pub_citation", "pub_author", "pub_notes",
    "publish_btn", "draft_to_deep", "download_session", "download_workbook",
)


def test_every_handler_id_is_still_emitted():
    for input_id in HANDLER_IDS:
        assert f'"{input_id}"' in SRC, f"publish.py no longer emits {input_id!r}"


def test_level_choices_keep_the_values_and_titles():
    choices = pub._level_choices()
    assert set(choices) == {"file", "library"}
    assert "Save to file" in str(choices["file"])
    assert "Publish to library" in str(choices["library"])


def test_block_reason_surfaces_the_env_flag(monkeypatch):
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    reason = pub._publish_block_reason()
    assert reason is not None and "STAF_LIBRARY_PUBLISH=1" in reason


def test_block_reason_none_when_the_gate_is_open(monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    monkeypatch.setenv("STAF_LIBRARY_MAINTAINER", "tester")
    assert pub._publish_block_reason() is None


def test_block_reason_names_the_maintainer_env_when_only_the_name_is_missing(monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    for var in ("STAF_LIBRARY_MAINTAINER", "USERNAME", "USER"):
        monkeypatch.delenv(var, raising=False)
    reason = pub._publish_block_reason()
    assert reason is not None and "STAF_LIBRARY_MAINTAINER" in reason


def test_autofill_effect_exists_guarded_and_evented():
    assert "_autofill_pub_name" in SRC
    idx = SRC.index("def _autofill_pub_name")
    head = SRC[max(0, idx - 300):idx]
    assert "@reactive.event(input.pub_assessment" in head
    assert "@guard(" in head


def test_user_visible_publish_copy_carries_no_em_dash(monkeypatch):
    # The no-em-dash rule covers rendered copy (docstrings and code comments
    # are exempt), so check the strings that actually reach the page.
    for label in pub._level_choices().values():
        assert "—" not in str(label)
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    assert "—" not in (pub._publish_block_reason() or "")
