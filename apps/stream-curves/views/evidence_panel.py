"""The evidence packages a project or a version names: status, Download, View.

Factored from the EASI page's "Development data packages" block so one table, one viewer
and one download path serve both: the EASI method page (its project's ``evidence``
references) and the curve source panel of a DEEP version (the package behind one region's
build, referenced from ``reference_build["evidence"]``, the provenance's
``evidenceReferences`` or the version folder's ``evidence.json``). The packages themselves
live in the evidence store (``streamcurves.evidence_store``); a reference names one by
digest, and a page only ever shows, fetches and opens it.

Pure builders first (tables, the viewer's body, the stations behind a metric read from a
package's ``pool_ledger.csv``), then :func:`evidence_panel_server`, which registers the
Download and View handlers, the table preview and the CSV export under a caller's input
prefix, so the EASI page keeps its ``pkg_*`` ids and the source panel gets its own.
"""
from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from shiny import reactive, render, ui

from streamcurves import evidence_store as evs
from streamcurves.easi_method import evidence as ev
from views import state as st
from views.theme import fa
from views.uihelpers import guard

logger = logging.getLogger("streamcurves")

STATUS_WORDS = {"damaged": "Damaged: download or import it again",
                "other": "Another version is here", "missing": "Not on this computer"}
#: the pool ledger's columns a station row shows, in order (deep_evidence.LEDGER_COLUMNS)
STATION_COLUMNS = ("station_key", "level", "option", "screen", "value", "source_cycle", "l3")


# --------------------------------------------------------------------------- #
# small pure helpers (the EASI page imports them from here)
# --------------------------------------------------------------------------- #
def size_text(n) -> str:
    n = int(n or 0)
    return f"{n / 1e6:,.1f} MB" if n >= 100_000 else f"{n / 1e3:,.0f} KB"


def short(digest) -> str:
    d = str(digest or "")
    return d.split(":", 1)[1][:12] if ":" in d else d[:12]


def _sentence(text) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def unavailable_line(i: dict) -> str:
    """One item a package does not carry: what, why, and what to do."""
    head = str(i.get("item") or "").strip()
    head = head[:1].upper() + head[1:]
    return " ".join(x for x in (f"{head}:", _sentence(i.get("why")), _sentence(i.get("remedy")))
                    if x.strip(": "))


def covers(coverage: dict) -> str:
    """One line for what a package covers: EASI's member rows, reaches and fits, or a DEEP
    build's stations, values and curves."""
    c = coverage or {}
    parts = []
    for key, noun in (("memberRows", "member rows"), ("reaches", "reaches"), ("fits", "fits"),
                      ("operationalCurves", "operational curves"), ("stations", "stations"),
                      ("values", "values"), ("metrics", "metrics"), ("curves", "curves"),
                      ("functionsScored", "functions scored")):
        if isinstance(c.get(key), int) and not isinstance(c.get(key), bool):
            parts.append(f"{c[key]:,} {noun}")
    if isinstance(c.get("studyReceipts"), list):
        parts.append(f"{len(c['studyReceipts'])} study receipts")
    if c.get("build"):
        parts.append(f"build {str(c['build'])[:8]}")
    region = c.get("region") if isinstance(c.get("region"), dict) else None
    if region and (region.get("name") or region.get("code")):
        parts.insert(0, str(region.get("name") or f"region {region.get('code')}"))
    return ", ".join(parts)


def check_lines(checks: dict) -> list[str]:
    """A package's recorded checks in words (an unknown check stays as its JSON)."""
    import json
    out = []
    for key, c in (checks or {}).items():
        if key == "eromMonthsReproduceStoredCv" and isinstance(c, dict):
            out.append(f"The 12 monthly EROM flows reproduce the stored flow CV of "
                       f"{c.get('identicalAtStoredPrecision', 0):,} of {c.get('comparable', 0):,} "
                       f"members exactly at its stored precision ({c.get('storedType')}); "
                       f"missing values agree: {'yes' if c.get('nullsAgree') else 'no'}.")
        elif key == "panelsRegenerateMembers" and isinstance(c, dict):
            # packages exported before 2026-09-24 also recorded how long the check took
            took = f" ({c['seconds']} s)" if c.get("seconds") is not None else ""
            out.append(f"Drawing the panels again from this package gives "
                       f"{'the same' if c.get('identical') else 'different'} "
                       f"{c.get('memberRows', 0):,} member rows{took}.")
        elif key == "ledgerSelectedEqualsBundle":
            out.append("The rebuild ledger's selected curves are exactly the bundle's."
                       if c else "The rebuild ledger's selected curves and the bundle's disagree.")
        elif key == "refittedRowsWithStationEvidence" and isinstance(c, dict):
            out.append(f"{c.get('n', 0)} of {c.get('of', 0)} refitted curves name the stations "
                       "behind them.")
        else:
            out.append(f"{key}: {json.dumps(c, sort_keys=True)}")
    return out


def facts_dl(rows: Iterable[tuple]):
    return ui.tags.dl(*[t for k, v in rows if v not in (None, "")
                        for t in (ui.tags.dt(k), ui.tags.dd(v))], class_="easi-facts")


def evt(evt_id: str, **fields) -> str:
    extra = "".join(f"{k}: {int(v)}, " for k, v in fields.items())
    return (f"Shiny.setInputValue('{evt_id}', {{{extra}n: Date.now() + Math.random()}}, "
            "{priority: 'event'})")


def kind_of(ref: Mapping) -> str:
    """Which public host serves a package: ``deep`` for a DEEP build's package, else ``easi``."""
    pid = str((ref or {}).get("packageId") or "")
    return "deep" if pid.startswith("deep-") else "easi"


def fetchable(kind: str) -> bool:
    try:
        return bool(evs.public_base(kind))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# the evidence reference behind a DEEP version
# --------------------------------------------------------------------------- #
def _refs_of(value) -> list[dict]:
    if isinstance(value, Mapping) and value.get("packageId"):
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        return [dict(v) for v in value if isinstance(v, Mapping) and v.get("packageId")]
    return []


def evidence_references(build: Optional[Mapping], provenance: Optional[Mapping] = None,
                        origin: Optional[Mapping] = None) -> list[dict]:
    """Every package reference a DEEP session names, first the reference build's own
    (``reference_build["evidence"]``), then the provenance document's
    ``evidenceReferences``, then the ``evidence.json`` beside the version the session was
    opened from (a library version, a staged version or a run folder). Deduplicated by
    package id and digest, in that order."""
    from streamcurves import library as lib
    found: list[dict] = []
    found += _refs_of((build or {}).get("evidence"))
    found += _refs_of((provenance or {}).get("evidenceReferences"))
    o = origin or {}
    folder = None
    try:
        if o.get("kind") == "library" and o.get("library_id") and o.get("version"):
            folder = lib.version_dir(str(o["library_id"]), int(o["version"]))
        elif o.get("kind") == "staged" and o.get("staged_path"):
            folder = Path(str(o["staged_path"]))
        elif o.get("kind") == "run" and o.get("run_dir"):
            folder = Path(str(o["run_dir"]))
    except (TypeError, ValueError):
        folder = None
    if folder is not None and (folder / "evidence.json").is_file():
        try:
            import json
            found += _refs_of(json.loads((folder / "evidence.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    out: list[dict] = []
    seen: set = set()
    for ref in found:
        key = (ref.get("packageId"), ref.get("packageDigest") or ref.get("dataDigest"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def evidence_reference_for(build: Optional[Mapping], provenance: Optional[Mapping] = None,
                           origin: Optional[Mapping] = None) -> Optional[dict]:
    """The DEEP package reference a session names, or None (:func:`evidence_references`,
    the first DEEP one)."""
    refs = [r for r in evidence_references(build, provenance, origin) if kind_of(r) == "deep"]
    return refs[0] if refs else None


# --------------------------------------------------------------------------- #
# the stations behind a metric, from a package's pool ledger
# --------------------------------------------------------------------------- #
def _true(v) -> bool:
    return str(v or "").strip().lower() in ("true", "1", "yes", "t")


def stations_for_metric(folder, metric: str, *, limit: Optional[int] = None) -> dict:
    """The stations in the pool a metric's curve was fitted from, read from the installed
    package's ``data/pool_ledger.csv`` (``in_pool`` rows of that metric): ``{rows, total,
    judged, available}``. ``available`` is False when the package carries no pool ledger
    (a reviewable package written from a session's fields)."""
    path = Path(folder) / "data" / "pool_ledger.csv"
    if not path.is_file():
        return {"rows": [], "total": 0, "judged": 0, "available": False}
    rows: list[dict] = []
    judged = 0
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("metric") or "") != str(metric):
                continue
            judged += 1
            if _true(r.get("in_pool")):
                rows.append({c: r.get(c, "") for c in STATION_COLUMNS})
    rows.sort(key=lambda r: (str(r.get("level") or ""), str(r.get("station_key") or "")))
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    return {"rows": rows, "total": total, "judged": judged, "available": True}


def stations_table(got: Mapping, metric: str, *, limit: Optional[int] = None):
    """The table of the stations behind ``metric``, or the sentence that says why there is
    none."""
    if not got.get("available"):
        return ui.div("This package carries no station-level pool ledger, so the stations "
                      "behind the curve cannot be listed from it.", class_="easi-muted")
    rows = list(got.get("rows") or [])
    if not rows:
        return ui.div(f"The pool ledger holds no station in the pool for {metric}.",
                      class_="easi-muted")
    head = ("Station", "Level", "Pool option", "Screen", "Value", "Cycle", "L3")
    body = [ui.tags.tr(*[ui.tags.td(str(r.get(c) or "")) for c in STATION_COLUMNS]) for r in rows]
    total = int(got.get("total") or len(rows))
    words = (f"{total} stations in the pool behind {metric}, of {got.get('judged', total)} judged"
             + (f"; the first {len(rows)} shown" if len(rows) < total else ""))
    return ui.TagList(
        ui.div(words, class_="easi-muted mb-1"),
        ui.div(ui.tags.table(ui.tags.thead(ui.tags.tr(*[ui.tags.th(h) for h in head])),
                             ui.tags.tbody(*body), class_="table table-sm easi-table"),
               class_="easi-scroll"))


# --------------------------------------------------------------------------- #
# the packages table (status, Download, View)
# --------------------------------------------------------------------------- #
def status_cell(status: str, *, fetchable_: bool):
    if status == "installed":
        return ui.span(fa("circle-check"), " Verified", class_="easi-ok")
    words = STATUS_WORDS.get(status, status)
    return ui.div(ui.span(fa("triangle-exclamation"), " ", words, class_="easi-bad")
                  if status == "damaged" else ui.span(words, class_="easi-muted"),
                  None if fetchable_ else ui.div("Import its package file.", class_="easi-muted"))


def action_button(status: str, *, prefix: str, ns, i: int, fetchable_: bool):
    """View for an installed package, Download for one that can be fetched, else nothing."""
    if status == "installed":
        return ui.tags.button("View", type="button", class_="btn btn-outline-secondary btn-sm",
                              onclick=evt(ns(f"{prefix}_view"), i=i))
    if fetchable_:
        return ui.tags.button("Download", type="button", class_="btn btn-outline-primary btn-sm",
                              onclick=evt(ns(f"{prefix}_download"), i=i))
    return None


def package_row(ref: Mapping, status: str, *, prefix: str, ns, i: int, fetchable_: bool,
                extra=None):
    """One package's row: title and id, roles, reproducibility, coverage, size, status, the
    action (View or Download) and ``extra`` (a caller's own control, such as Remove)."""
    roles = ", ".join(ev.ROLE_LABELS.get(r, r) for r in ref.get("roles") or [])
    repro = ref.get("reproducibility") or ""
    arch = ref.get("archive") or {}
    return ui.tags.tr(
        ui.tags.td(ui.div(ref.get("title") or ref["packageId"], class_="easi-strong"),
                   ui.div(f"{ref['packageId']}, {ref.get('version')}", class_="easi-muted")),
        ui.tags.td(roles),
        ui.tags.td(ev.REPRODUCIBILITY_LABELS.get(repro, repro),
                   title=ev.REPRODUCIBILITY_HELP.get(repro, "")),
        ui.tags.td(covers(ref.get("coverage"))),
        ui.tags.td(size_text(ref.get("bytes")), class_="easi-right easi-nowrap",
                   title=(f"Unpacked. The download is {size_text(arch.get('bytes'))}."
                          if arch.get("bytes") else "Unpacked")),
        ui.tags.td(status_cell(status, fetchable_=fetchable_), class_="easi-nowrap"),
        ui.tags.td(action_button(status, prefix=prefix, ns=ns, i=i, fetchable_=fetchable_), extra,
                   class_="easi-right easi-nowrap"))


def packages_table(refs: Iterable[Mapping], installed: Iterable[Mapping], *, prefix: str, ns,
                   kind: Optional[str] = None, extra: Optional[Callable] = None,
                   empty_text: str = "No development data packages are named."):
    """The table over ``refs`` with each one's status on this computer (``installed``). ``kind``
    fixes the public host; when None each reference's own kind decides. ``extra(i, ref)``
    adds a caller's control to the last cell."""
    inst = list(installed or [])
    rows = []
    for i, ref in enumerate(refs or []):
        status = ev.status(ref, inst)
        can = fetchable(kind or kind_of(ref))
        rows.append(package_row(ref, status, prefix=prefix, ns=ns, i=i, fetchable_=can,
                                extra=extra(i, ref) if extra else None))
    if not rows:
        return ui.div(fa("box-open"), ui.span(" " + empty_text), class_="easi-empty")
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Package"), ui.tags.th("Role"),
                                 ui.tags.th("Reproducible"), ui.tags.th("Covers"),
                                 ui.tags.th("Size", class_="easi-right"),
                                 ui.tags.th("Here"), ui.tags.th(""))),
        ui.tags.tbody(*rows), class_="table table-sm easi-table")


def evidence_section(ref: Optional[Mapping], installed: Iterable[Mapping], *, prefix: str, ns,
                     metric: Optional[str] = None, folder=None, busy: Optional[str] = None,
                     stations_limit: int = 60):
    """The Evidence section of a DEEP curve's source panel: the package the build recorded,
    its status, Download or View, and, once the package is on this computer (``folder``),
    the stations behind ``metric`` from its pool ledger."""
    if not ref:
        return ui.div("This version records no evidence package. A build under the campaign "
                      "writes one; an earlier version has none to show.", class_="easi-muted")
    parts = [packages_table([ref], installed, prefix=prefix, ns=ns, kind="deep")]
    if busy:
        parts.append(ui.div(ui.span(busy, class_="easi-running"), class_="easi-tools"))
    if folder is not None and metric:
        parts.append(ui.div(ui.tags.strong(f"Stations behind {metric}"), class_="mt-2 mb-1"))
        parts.append(stations_table(stations_for_metric(folder, metric, limit=stations_limit),
                                    metric, limit=stations_limit))
    elif metric:
        parts.append(ui.div("Download the package to list the stations behind this curve.",
                            class_="easi-muted mt-1"))
    return ui.div(*parts, class_="evidence-section")


# --------------------------------------------------------------------------- #
# the package viewer
# --------------------------------------------------------------------------- #
def viewer_modal(doc: Mapping, *, prefix: str, ns, tables: list[str], preview_output=None):
    """The modal that shows an installed package: its description, facts, technical record,
    sources, checks, limitations, files and (for its tables) a preview with a CSV export.
    ``preview_output``: the caller's own preview output tag, else ``<prefix>_preview``."""
    files = ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("File"), ui.tags.th("Rows", class_="easi-right"),
                                 ui.tags.th("Columns", class_="easi-right"),
                                 ui.tags.th("Size", class_="easi-right"))),
        ui.tags.tbody(*[ui.tags.tr(ui.tags.td(ui.tags.code(rel.split("/", 1)[-1])),
                                   ui.tags.td(f"{rec['rows']:,}" if isinstance(rec.get("rows"), int) else "",
                                              class_="easi-right"),
                                   ui.tags.td(str(len(rec.get("columns") or [])) if rec.get("columns")
                                              else "", class_="easi-right"),
                                   ui.tags.td(size_text(rec["bytes"]), class_="easi-right"))
                        for rel, rec in sorted(doc["files"].items())]),
        class_="table table-sm easi-table")
    repro = doc.get("reproducibility") or ""
    facts = facts_dl([
        ("Version", doc.get("version")),
        ("Role", ", ".join(ev.ROLE_LABELS.get(r, r) for r in doc.get("roles") or [])),
        ("Reproducible", f"{ev.REPRODUCIBILITY_LABELS.get(repro, repro)}: "
                         f"{ev.REPRODUCIBILITY_HELP.get(repro, '')}" if repro else None),
        ("Covers", covers(doc.get("coverage"))),
        ("Shared as", (doc.get("redistribution") or {}).get("status")),
    ])
    sources = [s for s in doc.get("sources") or [] if isinstance(s, dict)]
    technical = ui.tags.details(ui.tags.summary("Technical record", class_="easi-muted"), facts_dl([
        ("Data digest", doc["dataDigest"]),
        ("Package digest", evs.package_digest(dict(doc))),
        ("Depends on", ", ".join(f"{d.get('packageId')} ({short(d.get('dataDigest'))})"
                                 for d in doc.get("dependsOn") or [])),
    ]))
    lists = []
    for key, title in (("limitations", "Limitations"), ("unavailable", "Not in this package")):
        items = doc.get(key) or []
        if items:
            lists.append(ui.div(title, class_="sc-sec"))
            lists.append(ui.tags.ul(*[ui.tags.li(i if isinstance(i, str) else unavailable_line(i))
                                      for i in items], class_="easi-list"))
    checks = doc.get("checks") or {}
    preview = None
    if tables:
        preview = ui.TagList(
            ui.div("Data", class_="sc-sec"),
            ui.div(ui.input_select(ns(f"{prefix}_table"), None,
                                   {t: t.split("/", 1)[-1] for t in tables}, width="320px"),
                   ui.download_button(ns(f"{prefix}_csv"), ui.TagList(fa("file-csv"), " Export as CSV"),
                                      class_="btn btn-outline-secondary btn-sm"),
                   class_="easi-tools"),
            preview_output if preview_output is not None else ui.output_ui(ns(f"{prefix}_preview")))
    return ui.modal(
        ui.p(doc.get("description") or "", class_="mb-2"), facts, technical,
        ui.div("Sources", class_="sc-sec") if sources else None,
        ui.tags.ul(*[ui.tags.li(" ".join(str(x) for x in (s.get("citation") or s.get("id"),
                                                         f"({s['path']})" if s.get("path") else "") if x))
                     for s in sources], class_="easi-list") if sources else None,
        ui.div("Checks", class_="sc-sec") if checks else None,
        ui.tags.ul(*[ui.tags.li(x) for x in check_lines(checks)], class_="easi-list")
        if checks else None,
        *lists, ui.div("Files", class_="sc-sec"), files, preview,
        title=doc.get("title") or doc["packageId"], size="xl", easy_close=True,
        footer=ui.modal_button("Close", class_="btn btn-outline-secondary"))


def preview_ui(path: Path):
    """The first rows of a package table (parquet or CSV)."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        cols = pf.schema_arrow.names
        head = pf.read_row_group(0, columns=cols[:14]).slice(0, 15).to_pylist()
        n = pf.metadata.num_rows
    else:
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            head = [dict(r) for _, r in zip(range(15), reader)]
        n = None
    shown = cols[:14]

    def cell(v):
        if isinstance(v, float):
            return "" if v != v else f"{v:.6g}"
        return "" if v is None else str(v)

    return ui.TagList(
        ui.div(f"First {len(head)} of {n:,} rows" if n is not None else f"First {len(head)} rows",
               (f", {len(shown)} of {len(cols)} columns" if len(cols) > len(shown) else ""),
               class_="easi-muted mb-1"),
        ui.div(ui.tags.table(ui.tags.thead(ui.tags.tr(*[ui.tags.th(c) for c in shown])),
                             ui.tags.tbody(*[ui.tags.tr(*[ui.tags.td(cell(r.get(c))) for c in shown])
                                             for r in head]),
                             class_="table table-sm easi-table easi-preview"),
               class_="easi-scroll"))


def csv_chunks(path: Path):
    """The bytes of a package table as CSV, a parquet file row group by row group."""
    if path.suffix == ".csv":
        yield path.read_bytes()
        return
    import io as _io
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    for i in range(pf.num_row_groups):
        buf = _io.BytesIO()
        pacsv.write_csv(pf.read_row_group(i), buf,
                        write_options=pacsv.WriteOptions(include_header=(i == 0)))
        yield buf.getvalue()


# --------------------------------------------------------------------------- #
# the server side: Download, View, the preview, the CSV export
# --------------------------------------------------------------------------- #
class EvidencePanel:
    """What :func:`evidence_panel_server` returns: the store tick to depend on, the busy
    text, the installed packages, the viewer's state, and the download and view entry
    points a caller's own handlers can await."""

    def __init__(self, *, tick, jobs: dict, viewing: dict, installed: Callable, download: Callable,
                 view: Callable, table_path: Callable, ready_folder: Callable):
        self.tick = tick
        self.jobs = jobs
        self.viewing = viewing
        self.installed = installed
        self.download = download
        self.view = view
        self.table_path = table_path
        self.ready_folder = ready_folder

    def busy(self) -> Optional[str]:
        return self.jobs.get("busy")


HANDLERS = ("download", "view", "preview", "csv")


def evidence_panel_server(input, output, session, state, *, prefix: str, refs: Callable[[], list],
                          kind: Optional[str] = None, tick=None, jobs: Optional[dict] = None,
                          handlers: Iterable[str] = HANDLERS) -> EvidencePanel:
    """Register the package handlers under ``prefix`` in the caller's namespace: the
    ``<prefix>_download`` and ``<prefix>_view`` events (each carries the row index ``i``
    into ``refs()``), the ``<prefix>_preview`` output and the ``<prefix>_csv`` download of
    the viewer. ``handlers`` names the ones to register; a page that keeps handlers of its
    own under the same ids (the EASI page) names fewer and calls the returned panel's
    ``download``, ``view`` and ``table_path`` from them. ``kind`` fixes the public host
    (``easi`` or ``deep``); None reads it off each reference. ``tick`` and ``jobs`` let a
    caller share its own store tick and busy record."""
    ns = session.ns
    wanted = set(handlers or ())
    store_tick = tick if tick is not None else reactive.value(0)
    _jobs: dict = jobs if jobs is not None else {"busy": None}
    _viewing: dict = {"folder": None, "files": [], "doc": None}
    _tasks: set = set()

    def _installed() -> list[dict]:
        store_tick()
        try:
            return evs.installed()
        except Exception:  # noqa: BLE001 - an unreadable store lists nothing
            return []

    def _ref_at(i) -> Optional[dict]:
        try:
            items = list(refs() or [])
            return items[int(i)] if 0 <= int(i) < len(items) else None
        except (TypeError, ValueError):
            return None

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    async def _download(ref: dict):
        """Fetch the package ``ref`` names from its public host into the store."""
        label = ref.get("title") or ref["packageId"]
        _jobs["busy"] = f" Downloading {label}..."
        store_tick.set(store_tick() + 1)
        await st.task_flush()
        try:
            with st.busy(state):
                base = evs.public_base(kind or kind_of(ref))
                await asyncio.to_thread(evs.fetch_reference, base, ref)
            ui.notification_show(f"{label} is on this computer and verified.", type="message", duration=5)
        except evs.EvidenceCancelled:
            ui.notification_show("Download cancelled. It continues from where it stopped next time.",
                                 type="message", duration=6)
        except evs.EvidenceError as exc:
            ui.notification_show(f"The package was not installed: {exc}", type="error", duration=12)
        except Exception as exc:  # noqa: BLE001 - never a silent failure
            logger.exception("evidence package download failed")
            ui.notification_show(f"The package could not be installed ({exc}).", type="error", duration=12)
        finally:
            _jobs["busy"] = None
            store_tick.set(store_tick() + 1)
            await st.task_flush()

    def _ready_folder(ref: Optional[Mapping]) -> Optional[Path]:
        """The verified installed folder of ``ref``, or None (no check on every paint:
        ``installed`` keeps the last full check's stamp)."""
        if not ref:
            return None
        for rec in _installed():
            if rec.get("verified") and evs.matches(rec, dict(ref)):
                return Path(rec["path"])
        return None

    async def _view(ref: Mapping, *, preview_output=None):
        """Open the viewer on the package ``ref`` names (verified now, every file hashed)."""
        try:
            folder = await asyncio.to_thread(evs.ready, dict(ref))
        except evs.EvidenceError as exc:
            store_tick.set(store_tick() + 1)
            ui.notification_show(str(exc), type="error", duration=10)
            return
        doc = evs.read_manifest(folder)
        tables = [rel for rel in doc["files"] if rel.endswith((".parquet", ".csv"))]
        _viewing.update(folder=folder, files=tables, doc=doc)
        ui.modal_show(viewer_modal(doc, prefix=prefix, ns=ns, tables=tables,
                                   preview_output=preview_output))

    def _table_path() -> Optional[Path]:
        try:
            rel = input[f"{prefix}_table"]()
        except Exception:  # noqa: BLE001 - no viewer open
            return None
        folder = _viewing.get("folder")
        if not folder or rel not in _viewing.get("files", []):
            return None
        return Path(folder) / rel

    if "download" in wanted:
        @reactive.effect
        @reactive.event(input[f"{prefix}_download"])
        @guard("download the package")
        def _on_download():
            ref = _ref_at((input[f"{prefix}_download"]() or {}).get("i", -1))
            if ref is None or _jobs.get("busy"):
                return
            _launch(_download(ref))

    if "view" in wanted:
        @reactive.effect
        @reactive.event(input[f"{prefix}_view"])
        @guard("open the package")
        async def _on_view():
            ref = _ref_at((input[f"{prefix}_view"]() or {}).get("i", -1))
            if ref is not None:
                await _view(ref)

    if "preview" in wanted:
        # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
        # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
        @output(id=f"{prefix}_preview", suspend_when_hidden=False)
        @render.ui
        def _preview():
            path = _table_path()
            if path is None:
                return None
            return preview_ui(path)

    if "csv" in wanted:
        @output(id=f"{prefix}_csv")
        @render.download(filename=lambda: (_table_path() or Path("table.csv")).stem + ".csv")
        def _csv():
            path = _table_path()
            if path is None:
                return
            rel = str(path.relative_to(Path(_viewing["folder"]))).replace("\\", "/")
            # the manifest that was verified when the viewer opened, never one read again from disk
            rec = ((_viewing.get("doc") or {}).get("files") or {}).get(rel) or {}
            if evs.sha_file(path) != rec.get("sha256"):
                why = f"{rel} no longer matches its package. Download or import the package again."
                ui.notification_show(why, type="error", duration=10)
                # raised, not returned: a download that ends without a byte would be saved as an
                # empty file, while an error mid-stream leaves the transfer unfinished and the
                # browser drops it
                raise evs.EvidenceError(why)
            yield from csv_chunks(path)

    return EvidencePanel(tick=store_tick, jobs=_jobs, viewing=_viewing, installed=_installed,
                         download=_download, view=_view, table_path=_table_path,
                         ready_folder=_ready_folder)


__all__ = ["STATUS_WORDS", "STATION_COLUMNS", "size_text", "short", "unavailable_line", "covers",
           "check_lines", "facts_dl", "evt", "kind_of", "fetchable", "evidence_references",
           "evidence_reference_for", "stations_for_metric", "stations_table", "status_cell",
           "action_button", "package_row", "packages_table", "evidence_section", "viewer_modal",
           "preview_ui", "csv_chunks", "EvidencePanel", "HANDLERS", "evidence_panel_server"]
