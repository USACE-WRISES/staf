"""Shared "Classify columns" UI + assignment logic — port of
app/helpers/classify_ui.R. Pure helpers parameterized by ``ns`` + ``input`` +
a profile DataFrame (from profile_and_suggest()); the calling module owns the
reactives. Inputs are named roles_<i> / family_<i> per row (1-based, like R).
"""

from __future__ import annotations

import pandas as pd
from shiny import ui

from streamcurves.profiler import metric_family_levels
from views.theme import fa

ROLE_CHOICES = {"metric": "Metric", "predictor": "Predictor", "stratifier": "Stratifier"}


def classify_suggested_role_set(role) -> list[str]:
    if isinstance(role, str) and role in ROLE_CHOICES:
        return [role]
    return []


def classify_selected_role_set(profile: pd.DataFrame, i: int) -> list[str]:
    """0-based row index; honors explicit membership seeds when present."""
    cols = {"role_metric", "role_predictor", "role_stratifier"}
    if cols.issubset(profile.columns):
        out = []
        if bool(profile["role_metric"].iat[i]):
            out.append("metric")
        if bool(profile["role_predictor"].iat[i]):
            out.append("predictor")
        if bool(profile["role_stratifier"].iat[i]):
            out.append("stratifier")
        return out
    return classify_suggested_role_set(profile["role"].iat[i])


def classify_role_select(ns, i: int, selected: list[str]):
    return ui.input_checkbox_group(
        ns(f"roles_{i}"),
        None,
        choices=ROLE_CHOICES,
        selected=selected,
        inline=True,
    )


def classify_family_select(ns, i: int, selected):
    return ui.input_select(
        ns(f"family_{i}"),
        None,
        choices=metric_family_levels(),
        selected=selected or "continuous",
        width="140px",
    )


def classify_assignments_from_input(input, profile: pd.DataFrame) -> pd.DataFrame:
    """Live multi-role assignments from the rendered classify inputs.
    Rows are 1-based input ids (roles_1..n) matching R."""
    cols = profile["column"].tolist()
    records = []
    for i, col in enumerate(cols, start=1):
        try:
            sel = list(input[f"roles_{i}"]() or [])
        except Exception:  # unbound (not yet in DOM)
            sel = []
        try:
            fam = input[f"family_{i}"]()
        except Exception:
            fam = None
        if not fam:
            fam = profile["suggested_family"].iat[i - 1] or "continuous"
        records.append(
            {
                "column": col,
                "is_metric": "metric" in sel,
                "is_predictor": "predictor" in sel,
                "is_stratifier": "stratifier" in sel,
                "family": fam,
            }
        )
    return pd.DataFrame(
        records, columns=["column", "is_metric", "is_predictor", "is_stratifier", "family"]
    )


def classify_table_html(ns, profile: pd.DataFrame):
    header = ui.tags.thead(
        ui.tags.tr(
            ui.tags.th("Column"),
            ui.tags.th("Type"),
            ui.tags.th("# Unique"),
            ui.tags.th("% Missing"),
            ui.tags.th("Examples"),
            ui.tags.th("Role"),
            ui.tags.th("Family (metrics)"),
        )
    )
    rows = []
    for i in range(len(profile)):
        id_hint = None
        if bool(profile["looks_like_id"].iat[i]):
            id_hint = ui.tags.span(
                "id?",
                class_="badge bg-light text-muted border ms-1",
                title="looks like an ID column",
            )
        rows.append(
            ui.tags.tr(
                ui.tags.td(
                    ui.tags.code(str(profile["column"].iat[i])), id_hint,
                    class_="wizard-col-name",
                ),
                ui.tags.td(str(profile["r_type"].iat[i])),
                ui.tags.td(str(profile["n_unique"].iat[i])),
                ui.tags.td(f"{profile['pct_missing'].iat[i]}%"),
                ui.tags.td(
                    str(profile["examples"].iat[i]),
                    class_="text-muted small wizard-col-examples",
                ),
                ui.tags.td(classify_role_select(ns, i + 1, classify_selected_role_set(profile, i))),
                ui.tags.td(classify_family_select(ns, i + 1, profile["suggested_family"].iat[i])),
            )
        )
    return ui.tags.table(
        header, ui.tags.tbody(*rows), class_="table table-sm align-middle wizard-classify-table"
    )


def classify_role_summary_html(assignments: pd.DataFrame):
    n_metric = int(assignments["is_metric"].sum())
    n_pred = int(assignments["is_predictor"].sum())
    n_strat = int(assignments["is_stratifier"].sum())
    n_unused = int(
        (~(assignments["is_metric"] | assignments["is_predictor"] | assignments["is_stratifier"])).sum()
    )

    def badge(label, value, cls):
        return ui.tags.span(f"{value} {label}", class_=f"badge {cls} me-1")

    warn = None
    if n_metric < 1:
        warn = ui.tags.span(
            fa("triangle-exclamation"),
            " Mark at least one column as Metric to build curves.",
            class_="text-danger ms-2 small",
        )
    return ui.div(
        badge("metric(s)", n_metric, "bg-primary"),
        badge("predictor(s)", n_pred, "bg-success"),
        badge("stratifier(s)", n_strat, "bg-info"),
        badge("not used", n_unused, "bg-light text-dark border"),
        warn,
        class_="mb-2",
    )
