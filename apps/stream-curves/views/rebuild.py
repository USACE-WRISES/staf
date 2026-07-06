"""Shared dataset-rebuild path — port of app/helpers/workbook_build.R.

Given a ``tables`` dict (data + config sheets), runs the canonical pipeline
build_input_bundle_from_tables -> clean_data -> derive_variables ->
run_metric_precheck, then resets analysis state and repopulates AppState.
Used by the workbook grid (and later the setup wizard).
"""

from __future__ import annotations

import hashlib

import pandas as pd
from shiny import reactive, ui

from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.precheck import run_metric_precheck
from streamcurves.workbook import build_input_bundle_from_tables
from views import state as st
from views.state import AppState


def rebuild_app_from_tables(
    state: AppState,
    candidate_tables: dict,
    success_text: str = "Dataset updated.",
    error_prefix: str = "Update failed",
    status_cb=None,
    preserve_metric: bool = True,
) -> bool:
    with reactive.isolate():
        current_metric = state.current_metric()

    try:
        bundle = build_input_bundle_from_tables(candidate_tables)

        cleaned, qa_log = clean_data(
            bundle["raw_data"],
            bundle["metric_config"],
            bundle["strat_config"],
            bundle["factor_recode_config"],
        )
        derived = derive_variables(
            cleaned,
            bundle["factor_recode_config"],
            bundle["predictor_config"],
            bundle["strat_config"],
        )
        precheck = run_metric_precheck(derived, bundle["metric_config"])

        st.reset_all_analysis(state)

        state.metric_config.set(bundle["metric_config"])
        state.strat_config.set(bundle["strat_config"])
        state.predictor_config.set(bundle["predictor_config"])
        state.factor_recode_config.set(bundle["factor_recode_config"])
        state.input_metadata.set(bundle.get("metadata"))
        state.site_mask_config.set(bundle.get("site_mask_config"))
        state.data.set(derived)
        state.qa_log.set(qa_log)
        state.precheck_df.set(precheck)
        state.data_fingerprint.set(
            hashlib.md5(
                pd.util.hash_pandas_object(derived, index=True).values.tobytes()
            ).hexdigest()
        )
        metric_keys = list(bundle["metric_config"].keys())
        if preserve_metric and current_metric in bundle["metric_config"]:
            state.current_metric.set(current_metric)
        elif metric_keys:
            state.current_metric.set(metric_keys[0])
        with reactive.isolate():
            state.config_version.set((state.config_version() or 0) + 1)
        state.app_data_loaded.set(True)
        state.custom_groupings.set({})
        state.custom_grouping_counter.set({})

        if status_cb is not None:
            status_cb({"type": "success", "text": success_text})
        st.notify_workspace_refresh(state)
        return True
    except Exception as e:  # noqa: BLE001 — surfaced inline + as a notification, like R
        message_text = f"{error_prefix}: {e}"
        if status_cb is not None:
            status_cb({"type": "danger", "text": message_text})
        ui.notification_show(message_text, type="error", duration=8)
        return False
