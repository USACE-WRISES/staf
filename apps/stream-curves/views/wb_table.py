"""Shared Discipline | Function | Metric table renderer (the ``wb-table``).

Extracted from ``views/discipline_map.py`` so the read-only coverage view on the
import wizard's Compile step renders with structure/classes identical to the
interactive workbench. The skeleton walk (thead, rowspanned discipline cell, one
tbody per discipline) lives here; each caller supplies the Function-column and
Metrics-column cell content via callbacks — the only two columns that differ
between the interactive workbench and the read-only coverage view.
"""

from __future__ import annotations

from collections.abc import Callable

from shiny import ui


def render_wb_table(
    by_discipline: dict[str, list[str]],
    *,
    fn_cell: Callable[[str], object],
    metrics_cell: Callable[[str], object],
):
    """Build the ``<table class="wb-table">`` skeleton.

    ``by_discipline`` maps each discipline to its ordered function names
    (``staf_functions_by_discipline()``). ``fn_cell(fn)`` returns the Function
    column ``<td>`` and ``metrics_cell(fn)`` the Metrics column ``<td>`` for each
    function.
    """
    disc_blocks = []
    for disc, fns in by_discipline.items():
        if not fns:
            continue
        disc_cls = f"discipline-{str(disc).lower()}"
        rows = []
        for j, fn in enumerate(fns):
            cells = []
            if j == 0:
                cells.append(
                    ui.tags.td(
                        ui.tags.span(disc, class_="wb-disc-label"),
                        class_=f"wb-disc {disc_cls}",
                        rowspan=str(len(fns)),
                    )
                )
            cells.extend([fn_cell(fn), metrics_cell(fn)])
            rows.append(ui.tags.tr(*cells))
        disc_blocks.append(ui.tags.tbody(*rows, class_="wb-disc-group"))

    return ui.tags.table(
        ui.tags.thead(
            ui.tags.tr(
                ui.tags.th("Discipline", class_="wb-th-disc"),
                ui.tags.th("Function", class_="wb-th-fn"),
                ui.tags.th("Metrics", class_="wb-th-metrics"),
            )
        ),
        *disc_blocks,
        class_="wb-table",
    )
