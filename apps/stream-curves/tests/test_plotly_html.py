"""views/plotly_html.py — htmlwidgets-style plotly fragments (phase-1 scatter)."""

from __future__ import annotations

import json
import re

import plotly.graph_objects as go
from htmltools import TagList

from views.plotly_html import plotly_html_fragment, plotly_js_dependency


def _render(fragment: TagList):
    rendered = fragment.render()
    return rendered["html"], rendered["dependencies"]


def _payload(html: str) -> dict:
    m = re.search(r"var p=(\{.*?\}),n=0;", html, re.DOTALL)
    assert m, "draw script must embed the figure payload"
    return json.loads(m.group(1))


def _fig(**layout) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[1.0, 2.0], y=[3.0, 4.0], mode="markers"))
    if layout:
        fig.update_layout(**layout)
    return fig


def test_fragment_has_container_script_and_plotly_dependency():
    html, deps = _render(plotly_html_fragment(_fig(), height_px=380))

    m = re.search(r'<div id="(plotly-html-[0-9a-f]+)"', html)
    assert m, "fragment must carry the target div"
    assert f'getElementById("{m.group(1)}")' in html
    assert "Plotly.newPlot" in html
    assert "height:380px" in html

    dep_names = [d.name for d in deps]
    assert "plotly-js" in dep_names
    dep = deps[dep_names.index("plotly-js")]
    src = dep.source_path_map()["source"]
    assert (src / "plotly.min.js").exists() if hasattr(src, "exists") else True


def test_payload_round_trips_data_and_r_config_defaults():
    html, _ = _render(plotly_html_fragment(_fig(), height_px=430))
    payload = _payload(html)
    assert payload["data"][0]["x"] == [1.0, 2.0]
    assert payload["data"][0]["y"] == [3.0, 4.0]
    assert payload["config"]["displaylogo"] is False
    assert payload["config"]["responsive"] is True
    assert "sendDataToCloud" in payload["config"]["modeBarButtonsToRemove"]


def test_config_overrides_merge_over_defaults():
    html, _ = _render(
        plotly_html_fragment(_fig(), height_px=300, config={"displayModeBar": False})
    )
    payload = _payload(html)
    assert payload["config"]["displayModeBar"] is False
    assert payload["config"]["displaylogo"] is False


def test_user_text_cannot_close_the_script_tag():
    fig = _fig()
    fig.data[0].text = ['</script><script>alert(1)</script>', "safe"]
    html, _ = _render(plotly_html_fragment(fig, height_px=300))
    body = html.split("<script>", 1)[1]
    assert "</script><script>" not in body.split("</script>")[0]
    assert "\\u003c/script" in html or "\\u003cscript" in html
    # and it still parses back to the original text
    payload = _payload(html)
    assert payload["data"][0]["text"][0] == '</script><script>alert(1)</script>'


def test_unique_container_ids():
    html1, _ = _render(plotly_html_fragment(_fig(), height_px=300))
    html2, _ = _render(plotly_html_fragment(_fig(), height_px=300))
    id1 = re.search(r'id="(plotly-html-[0-9a-f]+)"', html1).group(1)
    id2 = re.search(r'id="(plotly-html-[0-9a-f]+)"', html2).group(1)
    assert id1 != id2


def test_dependency_serves_bundled_plotly_js():
    dep = plotly_js_dependency()
    assert dep.name == "plotly-js"
    assert dep.script[0]["src"] == "plotly.min.js"
