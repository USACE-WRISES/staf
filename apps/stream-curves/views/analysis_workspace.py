"""Analysis workspace — port of app/modules/mod_analysis_workspace.R.

The tabbed container hosting all four phase workspaces inside the "analysis"
modal. Tab switches drive the preload status machinery (loading shell rows in
the modal + per-phase lazy preparation).
"""

from __future__ import annotations

from shiny import module, reactive, ui

from views import state as st
from views.phase2 import phase2_server, phase2_ui
from views.state import AppState


@module.ui
def analysis_workspace_ui():
    from views.phase1 import phase1_ui  # local import: registry fills as modules land
    from views.phase3 import phase3_ui
    from views.phase4 import phase4_ui

    return ui.navset_card_tab(
        ui.nav_panel("Exploratory", phase1_ui("phase1"), value="exploratory"),
        ui.nav_panel("Cross-Metric Analysis", phase2_ui("phase2"), value="cross_metric"),
        ui.nav_panel("Verification", phase3_ui("phase3"), value="verification"),
        ui.nav_panel("Reference Curves", phase4_ui("phase4"), value="reference_curves"),
        id="analysis_tabs",
        selected="reference_curves",
    )


@module.server
def analysis_workspace_server(input, output, session, state: AppState):
    from views.phase1 import phase1_server
    from views.phase3 import phase3_server
    from views.phase4 import phase4_server

    phase1_server("phase1", state, dialog_mode=True, workspace_scope="analysis")
    phase2_server("phase2", state, workspace_scope="analysis")
    phase3_server("phase3", state, dialog_mode=True, workspace_scope="analysis")
    phase4_server("phase4", state, dialog_mode=True, workspace_scope="analysis")

    @reactive.effect
    @reactive.event(input.analysis_tabs, ignore_init=False)
    def _tab_change():
        if not st.workspace_scope_is_active(state, "analysis", isolate_state=True):
            return
        with reactive.isolate():
            request_id = state.analysis_tab_request_id()
        selected_tab = input.analysis_tabs() or "reference_curves"
        if not st.analysis_tab_request_is_current(state, request_id):
            return
        if selected_tab not in st.analysis_tab_keys():
            return
        current = st.get_analysis_tab_status(state, selected_tab)
        if current in ("ready", "loading"):
            return
        st.set_analysis_tab_status(state, selected_tab, "loading", request_id)
        st.request_analysis_tab_preload(state, selected_tab, request_id)
