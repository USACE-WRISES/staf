"""The Apply STAF page lists Draft calculators only when asked (owner, 2026-10-14), as DEEP's map
does: the Draft group joins the ecoregion list when "Show Draft assessments" is ticked, the map
dialog draws a hidden Draft neutral and lets its key switch the Drafts, and without JavaScript the
Drafts sit in their own closed list. These read the site's files as text."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INCLUDE = REPO / "docs" / "_includes" / "apply_staf.html"
SCRIPT = REPO / "docs" / "assets" / "js" / "apply-staf.js"
CSS = REPO / "docs" / "assets" / "css" / "custom.css"


def test_the_page_marks_the_draft_group_and_offers_the_box():
    html = INCLUDE.read_text(encoding="utf-8")
    assert '{% if status == "Draft" %} data-calc-draft{% endif %}' in html
    assert ('<label class="apply-picker-drafts" data-calc-controls hidden><input type="checkbox" '
            'data-calc-drafts autocomplete="off"> Show Draft assessments</label>') in html
    assert '{% unless c.status == "Draft" %}' in html, "the no-script list leaves the Drafts out"
    assert "<summary>Draft assessments ({{ drafts.size }})</summary>" in html


def test_the_script_hides_the_drafts_until_asked():
    js = SCRIPT.read_text(encoding="utf-8")
    assert "select.querySelector('optgroup[data-calc-draft]')" in js
    assert "route.querySelector('[data-calc-drafts]')" in js
    assert "select.removeChild(draftGroup)" in js and "select.appendChild(draftGroup)" in js
    assert "path.classList.add('is-hidden')" in js and "hiddenDraft(option)" in js
    assert "' Draft assessments'" in js, "the key's Draft item says what it does"


def test_the_styles_and_the_cache_busts():
    css = CSS.read_text(encoding="utf-8")
    assert ".apply-picker-drafts {" in css and ".apply-picker-drafts[hidden] {" in css
    assert css.index(".eco-map-regions path:hover {") < css.index(".eco-map-regions path.is-hidden {")
    assert ".eco-map-key-toggle {" in css and ".eco-map-swatch.is-off {" in css
    assert "apply-staf.js?v=20261014-drafts-hidden" in (REPO / "docs" / "tools" / "index.md").read_text(
        encoding="utf-8")
    assert "custom.css?v=20261014-drafts-hidden" in (REPO / "docs" / "_includes" / "head_custom.html"
                                                    ).read_text(encoding="utf-8")


def test_no_dash_in_the_new_copy():
    for path in (INCLUDE, SCRIPT):
        text = path.read_text(encoding="utf-8")
        assert "Show Draft assessments" in text or "Draft assessments" in text
    assert "—" not in INCLUDE.read_text(encoding="utf-8")
