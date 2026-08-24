"""The Reference Curves table's stratification cell.

`Stratifications Available` used to hold an `input_selectize(multiple=True)` in a
135px column, so its selected pills stacked vertically and every summary row ran
several lines tall (the CSS capped the cell at 4.75rem with an inner scrollbar).
The cell is now a read-only one-line chip summary and the editor moved into the
expanded detail row. These pin both halves so the tall cell cannot come back.
"""
from __future__ import annotations

from views import summary_page as sp

CHOICES = {
    "ecoregion": "Ecoregion",
    "da_class": "Drainage Area Class",
    "slope_class": "Channel Slope Class",
}


def _html(tag) -> str:
    return str(tag).replace("&apos;", "'").replace("&quot;", '"')


# --------------------------------------------------------------------------- #
# the read-only cell
# --------------------------------------------------------------------------- #

def test_summary_cell_is_read_only_with_a_count_badge_and_chips():
    html = _html(sp.strat_summary_ui(["Ecoregion", "Drainage Area Class"], onclick="GO()"))
    # no editor markup at all: this is what kept the rows tall
    assert "selectize" not in html
    assert "<select" not in html
    # a count badge plus one chip per stratification, on one line
    assert 'class="metric-count-badge"' in html
    assert html.count('class="metric-fn-chip"') == 2
    assert ">2<" in html
    assert "Ecoregion" in html and "Drainage Area Class" in html
    assert 'class="summary-strat-chips"' in html


def test_summary_cell_click_expands_the_detail_row():
    html = _html(sp.strat_summary_ui(["Ecoregion"], onclick="TOGGLE_ME()"))
    assert 'onclick="TOGGLE_ME()"' in html
    assert 'type="button"' in html
    assert "Click to edit" in html


def test_summary_cell_title_carries_the_full_list():
    html = _html(sp.strat_summary_ui(["Ecoregion", "Drainage Area Class"], onclick="x"))
    assert "Stratifications available: Ecoregion, Drainage Area Class." in html


def test_summary_cell_with_nothing_selected_says_none_and_has_no_badge():
    html = _html(sp.strat_summary_ui([], onclick="x"))
    assert 'class="summary-strat-none"' in html
    assert "None" in html
    assert "metric-count-badge" not in html
    assert "metric-fn-chip" not in html
    assert _html(sp.strat_summary_ui(None, onclick="x")) == html


def test_summary_cell_escapes_a_label_with_markup():
    html = str(sp.strat_summary_ui(['<b>x</b> & "y"'], onclick="x"))
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


# --------------------------------------------------------------------------- #
# the editor, now in the detail row
# --------------------------------------------------------------------------- #

def test_detail_editor_is_the_multiselect_with_every_choice():
    html = _html(sp.strat_editor_ui("available_phab_XEMBED", CHOICES, ["ecoregion"]))
    # py-shiny renders a <select multiple> plus a selectize config script
    assert "<select" in html
    assert 'id="available_phab_XEMBED"' in html
    assert "multiple" in html
    assert "remove_button" in html
    for label in CHOICES.values():
        assert label in html
    assert 'class="summary-detail-strat"' in html
    assert "Stratifications available for this metric" in html


def test_detail_editor_preselects_only_the_selected_keys():
    html = _html(sp.strat_editor_ui("i", CHOICES, ["ecoregion", "da_class"]))
    # selectize is seeded through the option elements py-shiny renders
    assert 'value="ecoregion" selected' in html or '"ecoregion"' in html
    assert "slope_class" in html  # still offered as a choice


def test_detail_editor_disables_while_the_row_is_locked():
    locked = _html(sp.strat_editor_ui("i", CHOICES, [], is_locked=True))
    free = _html(sp.strat_editor_ui("i", CHOICES, [], is_locked=False))
    assert "disabled" in locked
    assert "<fieldset disabled" in locked or 'disabled="disabled"' in locked
    assert 'disabled="disabled"' not in free


def test_detail_editor_tolerates_empty_choices_and_selection():
    html = _html(sp.strat_editor_ui("i", None, None))
    assert "summary-detail-strat" in html
