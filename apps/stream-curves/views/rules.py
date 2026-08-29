"""Rules page - the methodology rule reference as one filterable table, and
the one place the per-run standing-decision opt-ins are chosen.

Read-only over the three governing files (rule catalog, standing-decisions
policy, methodology config), joined by streamcurves/rules_view.py. One aligned
table, one tbody per family with a jump-bar chip per family; each rule row is
followed by a hidden detail row toggled purely client-side. Status badges mark
only DEPARTURES from the page baseline (36 of 42 rules are provisional and
implemented, so that pair says nothing); the full status always rides in the
detail row.

The only writable thing on the page is the four opt-in checkboxes, whose single
home is ``state.rule_selections``; the Region builder reads that list into its
``--enable-policy`` flags, and every run records what was enabled in its own
manifest regardless. Rule chips elsewhere deep-link here through
``uihelpers.RULES_GOTO_INPUT`` (consumed by app.py); this server clears the
filters, opens the target row (``rulesExpandRow`` in curves.js), and scrolls to
it (``scrollToElement``). The rule rows keep their ``rule-<id>`` anchors, so
every existing link keeps landing.

The render is split into three outputs on purpose: the shell (title, jump bar,
the two filter inputs) re-renders only when the page shows, so typing in the
search box never destroys the box; the table re-renders on the filters; the
enabled-line re-renders on the selection. Thresholds show the values resolved
through the same accessor the pipeline uses; the config loaders are lru-cached,
so an edited file shows up after a process restart, like everywhere else.
"""
from __future__ import annotations

from shiny import module, reactive, render, ui

from streamcurves import decisions as dec
from streamcurves import methodology
from streamcurves import region_build as rb
from streamcurves import rules_view
from views.state import AppState
from views.uihelpers import guard

#: Threshold-status exceptions render as small dots (text badges are what the
#: noise complaint was about); implementation exceptions keep short text
#: badges because they change what the rule means.
_STATUS_DOT = {"approved": "rules-dot-approved", "calibrated": "rules-dot-calibrated"}
_IMPL_BADGE = {"partial": "text-bg-warning",
               "not_yet_implemented": "text-bg-danger"}
_IMPL_LABEL = {"implemented": "implemented", "partial": "partial",
               "not_yet_implemented": "not implemented"}

#: Reviewer-facing labels for the opt-in checkboxes (id -> (label, detail)).
_OPTIONAL_COPY = {pid: (label, detail) for pid, label, detail in rb.OPTIONAL_POLICIES}

# ── client-side row expansion (no server round trip) ─────────────────────────
# The whole row is a click target, guarded so a click on or inside the Shiny
# checkbox (div.shiny-input-container > div.checkbox > label > input + span)
# never toggles the row; the chevron is a real button for keyboard users.
_TOGGLE_CORE = (
    "var r=this.closest('tr');var d=r?r.nextElementSibling:null;"
    "if(d&&d.classList.contains('rules-detail-row')){d.hidden=!d.hidden;"
    "r.classList.toggle('rules-open',!d.hidden);"
    "var b=r.querySelector('.rules-toggle');"
    "if(b)b.setAttribute('aria-expanded',d.hidden?'false':'true');}")

_ROW_CLICK = ("if(event.target.closest('input,label,a,button,"
              ".shiny-input-container'))return;" + _TOGGLE_CORE)
_BUTTON_CLICK = "event.stopPropagation();" + _TOGGLE_CORE


def _jump_onclick(family: str) -> str:
    """Scroll to a family heading row (the curve_gallery _scroll_to idiom)."""
    dom = rules_view.family_dom_id(family)
    return (f"var el=document.getElementById('{dom}');"
            "if(el)el.scrollIntoView({behavior:'smooth',block:'start'});")


def _checkbox_id(policy_id: str) -> str:
    """Shiny input ids cannot carry hyphens; policy ids do."""
    return "pol_" + str(policy_id).replace("-", "_")


def _value_line(entry: dict):
    """Catalog prose plus the live resolved values, one muted line."""
    parts = []
    if entry["threshold"]:
        parts.append(str(entry["threshold"]))
    for item in entry["resolved"]:
        parts.append(f"{item['path']} = {item['value']}")
    if not parts:
        return None
    return ui.div("Threshold: " + "; ".join(parts), class_="text-muted small")


def _name_marks(entry: dict, legend: dict):
    """The exception marks beside a rule's name; nothing for the baseline."""
    marks = []
    for kind, status in rules_view.status_exceptions(entry):
        if kind == "threshold":
            tip = (legend.get("threshold_status") or {}).get(status, "")
            marks.append(ui.tags.span(
                class_="rules-dot " + _STATUS_DOT.get(status, "rules-dot-approved"),
                title=f"{status}: {tip}".strip(": ")))
        else:
            tip = (legend.get("implementation_status") or {}).get(status, "")
            marks.append(ui.tags.span(
                _IMPL_LABEL.get(status, status),
                class_="badge rules-flag " + _IMPL_BADGE.get(status, "text-bg-secondary"),
                title=tip))
    return marks


def _adjust_cell(entry: dict, ns, selected: set):
    """The opt-in checkbox, or nothing. Deliberately nothing else: this column
    holds exactly what the app can change, so the eye can scan it for the four
    checkboxes. A rule's automatic (standing) decisions are stated in plain
    words in the detail row instead."""
    if entry["optional"]:
        pid = str(entry["optional"][0].get("id"))
        label, _detail = _OPTIONAL_COPY.get(pid, (pid, ""))
        return ui.input_checkbox(ns(_checkbox_id(pid)), label,
                                 value=pid in selected)
    return None


def _detail_cell(entry: dict, legend: dict):
    """Everything the old card showed inline, now behind the row toggle."""
    thr_tip = (legend.get("threshold_status") or {}).get(entry["threshold_status"], "")
    impl_tip = (legend.get("implementation_status") or {}).get(
        entry["implementation_status"], "")
    lines = [
        ui.div(entry["purpose"]) if entry["purpose"] else None,
        _value_line(entry),
        (ui.div(ui.tags.strong("Test: "), entry["test"])
         if entry["test"] else None),
        (ui.div(ui.tags.strong("Code: "), ui.tags.code(entry["maps_to"]))
         if entry["maps_to"] else None),
        (ui.div(ui.tags.strong("Notes: "), entry["note"])
         if entry["note"] else None),
        ui.div(
            ui.tags.strong("Status: "),
            ui.tags.span(f"{entry['threshold_status']} threshold", title=thr_tip),
            ", ",
            ui.tags.span(_IMPL_LABEL.get(entry["implementation_status"],
                                         entry["implementation_status"]),
                         title=impl_tip),
            f". Override: {entry['override_permission']}.",
        ),
    ]
    for pol in entry["standing"]:
        lines.append(ui.div(
            ui.tags.strong("Applied automatically on every build "),
            "(", ui.tags.code(pol.get("id")), ")",
            f": {pol.get('action')} when "
            + "; ".join(rules_view.describe_match(pol)) + ".",
        ))
    for pol in entry["optional"]:
        pid = str(pol.get("id"))
        label, detail = _OPTIONAL_COPY.get(pid, (pid, ""))
        lines.append(ui.div(
            ui.tags.strong(f"{label}: "),
            f"{detail} Applies {pol.get('action')} when "
            + "; ".join(rules_view.describe_match(pol))
            + ". Recorded on each run it is enabled for.",
        ))
    return ui.div(*lines, class_="small rules-detail")


def _rule_rows(entry: dict, ns, legend: dict, selected: set):
    """One rule as its (main row, hidden detail row) pair. The main row keeps
    the rule's deep-link anchor id; the detail row must stay its immediate
    next sibling (the toggle and the rulesExpandRow handler both rely on it)."""
    text, tip = rules_view.threshold_cell(entry)
    main = ui.tags.tr(
        ui.tags.td(
            ui.tags.button(
                ui.tags.span("›", class_="rules-caret"),
                type="button", class_="rules-toggle", onclick=_BUTTON_CLICK,
                aria_expanded="false", aria_label="Show details",
                title="Show details"),
            class_="rules-col-toggle"),
        ui.tags.td(ui.tags.code(entry["id"], class_="rules-id")),
        ui.tags.td(ui.tags.span(entry["name"], class_="rules-name",
                                title=entry["purpose"] or None),
                   *_name_marks(entry, legend)),
        ui.tags.td(text, class_="rules-threshold text-muted",
                   title=tip or None),
        ui.tags.td(_adjust_cell(entry, ns, selected), class_="rules-adjust"),
        id=rules_view.rule_dom_id(entry["id"]),
        class_="rules-row",
        onclick=_ROW_CLICK,
    )
    detail = ui.tags.tr(
        ui.tags.td(_detail_cell(entry, legend), colspan="5"),
        class_="rules-detail-row",
        hidden=True,
    )
    return main, detail


@module.ui
def rules_ui():
    return ui.output_ui("rules_page")


@module.server
def rules_server(input, output, session, state: AppState, active=None):
    ns = session.ns
    optional_ids = rules_view.optional_policy_ids()

    @render.ui
    def rules_page():
        # The shell: renders only when the page shows. The two filter inputs
        # live HERE, with static defaults, so typing never re-renders the box.
        # Filter state deliberately does NOT persist across page activations:
        # the deep-link flow (_scroll_to_rule) relies on a hidden page coming
        # back unfiltered so the target row exists to scroll to.
        if active is not None and not active():
            return None
        policy = dec.load_policy()
        by_family = rules_view.rules_by_family(rules_view.rule_entries(policy))
        chips = [
            ui.tags.button(
                fam_label := rules_view.FAMILY_LABELS.get(fam, fam),
                ui.tags.span(str(len(entries)), class_="rules-jump-count"),
                type="button", class_="rules-jump-chip",
                onclick=_jump_onclick(fam), title=f"Jump to {fam_label}")
            for fam, entries in by_family.items()
        ]
        return ui.div(
            ui.div(
                ui.h4("Rules", class_="mb-0"),
                ui.tags.span(
                    f"Methodology {methodology.methodology_version()}"
                    f", standing decisions policy {dec.policy_version(policy)}",
                    class_="text-muted small ms-2"),
                class_="d-flex align-items-baseline"),
            ui.output_ui("rules_enabled_line"),
            ui.div(
                *chips,
                ui.div(ui.input_checkbox(ns("rules_adjustable_only"),
                                         "Adjustable only", value=False),
                       class_="rules-filter",
                       title="Only the rules with a per-run opt-in"),
                ui.div(ui.input_text(ns("rules_search"), None,
                                     placeholder="Filter rules"),
                       class_="rules-search"),
                class_="rules-jumpbar"),
            ui.output_ui("rules_table"),
            class_="rules-page",
        )

    @render.ui
    def rules_enabled_line():
        selected = sorted(set(state.rule_selections() or []))
        return ui.div(
            "Enabled for builds started from this app: "
            + (", ".join(selected) if selected else "none")
            + ". Each run records what was enabled.",
            class_="text-muted small mb-2")

    @render.ui
    def rules_table():
        if active is not None and not active():
            return None
        try:
            query = str(input.rules_search() or "")
        except Exception:  # noqa: BLE001 - shell inputs not bound yet
            query = ""
        try:
            adjustable = bool(input.rules_adjustable_only())
        except Exception:  # noqa: BLE001
            adjustable = False
        policy = dec.load_policy()
        legend = rules_view.status_labels()
        # Selection is isolated: ticking a checkbox must not rebuild the table
        # (open detail rows would collapse). _sync_to_checkboxes keeps mounted
        # boxes honest when the selection changes elsewhere.
        with reactive.isolate():
            selected = set(state.rule_selections() or [])
        entries = rules_view.filter_entries(
            rules_view.rule_entries(policy),
            adjustable_only=adjustable, query=query)
        if not entries:
            return ui.div("No rules match. Clear the search or the filter above.",
                          class_="text-muted small py-3 rules-empty")
        bodies = []
        for fam, fam_entries in rules_view.rules_by_family(entries).items():
            rows = [ui.tags.tr(
                ui.tags.th(
                    rules_view.FAMILY_LABELS.get(fam, fam),
                    ui.tags.span(str(len(fam_entries)),
                                 class_="rules-jump-count ms-1"),
                    colspan="5"),
                id=rules_view.family_dom_id(fam),
                class_="rules-family-row")]
            for entry in fam_entries:
                main, detail = _rule_rows(entry, ns, legend, selected)
                rows.append(main)
                rows.append(detail)
            bodies.append(ui.tags.tbody(*rows))
        return ui.div(
            ui.tags.table(
                ui.tags.thead(ui.tags.tr(
                    ui.tags.th("", class_="rules-col-toggle"),
                    ui.tags.th("Rule", class_="rules-col-id"),
                    ui.tags.th("Name"),
                    ui.tags.th("Threshold", class_="rules-col-threshold"),
                    ui.tags.th("Adjust", class_="rules-col-adjust"),
                )),
                *bodies,
                class_="table table-sm rules-table"),
            class_="rules-table-wrap")

    # One guarded effect per opt-in checkbox, syncing into the single home.
    for _pid in optional_ids:

        def _mk(pid: str, input_id: str):
            @reactive.effect
            @guard("update the rule selection")
            def _sync_from_checkbox():
                checked = bool(input[input_id]())
                with reactive.isolate():
                    current = list(state.rule_selections() or [])
                if checked and pid not in current:
                    state.rule_selections.set(current + [pid])
                elif not checked and pid in current:
                    state.rule_selections.set([p for p in current if p != pid])

        _mk(_pid, _checkbox_id(_pid))

    # Keep the checkboxes honest when the selection changes elsewhere (restore,
    # the Region builder's REF-02 re-stage). Input reads are isolated so this
    # depends only on the selection; value-compare stops update loops. A box
    # the filter has hidden is unbound: the client drops the update, and the
    # remount renders from the selection anyway.
    @reactive.effect
    @guard("sync the rule selections")
    def _sync_to_checkboxes():
        selected = set(state.rule_selections() or [])
        for pid in optional_ids:
            input_id = _checkbox_id(pid)
            with reactive.isolate():
                try:
                    have = bool(input[input_id]())
                except Exception:  # noqa: BLE001 - not mounted yet
                    continue
            want = pid in selected
            if want != have:
                ui.update_checkbox(input_id, value=want)

    # A rule chip anywhere in the app: app.py switched the navset here and
    # bumped the nonce. A deep link must land on a visible, OPENED row: clear
    # both filters (harmlessly dropped when the page was hidden, because the
    # shell re-creates the inputs at these same defaults), open the detail row
    # (rulesExpandRow), then scroll (both handlers retry until layout).
    @reactive.effect
    @reactive.event(state.rules_anchor_nonce, ignore_init=True)
    @guard("scroll to the rule")
    async def _scroll_to_rule():
        with reactive.isolate():
            rule_id = state.rules_anchor_request()
        if not rule_id:
            return
        ui.update_checkbox("rules_adjustable_only", value=False)
        ui.update_text("rules_search", value="")
        dom = rules_view.rule_dom_id(rule_id)
        await session.send_custom_message("rulesExpandRow", {"id": dom})
        await session.send_custom_message("scrollToElement", {"id": dom})
