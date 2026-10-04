"""One calculator per scenario: the template's per-scenario sheets copied with every reference
rewritten so each copy reads only itself and the shared sheets.

The per-scenario set is computed, not configured: the score sheet plus every sheet that depends on
it, through formulas, defined names, data validations, conditional formats or charts (EASI and
DEEP: Score, Metrics, Results, ChartData). A shared sheet that reads the score sheet in one block
only (SFARI's ``Instructions!L4:P27``, which feeds the score sheet's charts) is named in
``blocks``; each alternative gets a hidden copy of that block. Unsupported content in the set
(tables, pivots, controls, comments, external links) refuses the whole assembly.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional
from xml.sax.saxutils import escape, unescape

from . import formula as F
from .package import CT_CHART, CT_DRAWING, R_CHART, R_DRAWING, Package, Rel
from .workbook import DefinedName, WorkbookModel

_UNESC = {"&quot;": '"', "&apos;": "'"}
_UID_NS = uuid.UUID("6f1c2a52-4e7f-4a8e-9b1d-7d1d8f0a5c11")
_FORMULA_TAGS = ("f", "formula", "formula1", "formula2", "xm:f")
_REFUSE = (("<tableParts", "a table"), ("<oleObjects", "an embedded object"), ("<controls", "a form control"),
           ("<legacyDrawing", "comments"))
_REFUSE_RELS = (("/pivotTable", "a pivot table"), ("/table", "a table"), ("/oleObject", "an embedded object"),
                ("/ctrlProp", "a form control"), ("/comments", "comments"), ("/externalLink", "an external link"))
_CELL = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)


class CloneError(ValueError):
    """The template cannot be copied per scenario as it is."""


def _rewrite_tag(xml: str, tag: str, fn: Callable[[str], str]) -> str:
    pat = re.compile(rf"(<{re.escape(tag)}\b[^>]*?(?<!/)>)(.*?)(</{re.escape(tag)}>)", re.S)

    def sub(m):
        text = unescape(m.group(2), _UNESC)
        new = fn(text)
        return m.group(0) if new == text else m.group(1) + escape(new) + m.group(3)
    return pat.sub(sub, xml)


def rewrite_sheet_xml(xml: str, mapping: dict) -> str:
    """Every formula-bearing element and internal hyperlink of a sheet, sheets renamed."""
    if not mapping:
        return xml
    for tag in _FORMULA_TAGS:
        xml = _rewrite_tag(xml, tag, lambda t: F.rewrite(t, mapping))

    def link(m):
        loc = unescape(m.group(2), _UNESC)
        new = F.rewrite(loc, mapping)
        return m.group(0) if new == loc else m.group(1) + escape(new, {'"': "&quot;"}) + m.group(3)
    return re.sub(r'(<hyperlink\b[^>]*\slocation=")([^"]*)(")', link, xml)


def rewrite_chart_xml(xml: str, mapping: dict) -> str:
    return _rewrite_tag(xml, "c:f", lambda t: F.rewrite(t, mapping)) if mapping else xml


def sheet_formulas(xml: str) -> list:
    out = []
    for tag in _FORMULA_TAGS:
        out += [unescape(t, _UNESC) for t in re.findall(rf"<{re.escape(tag)}\b[^>]*?(?<!/)>(.*?)</{re.escape(tag)}>",
                                                        xml, re.S)]
    out += [unescape(t, _UNESC) for t in re.findall(r'<hyperlink\b[^>]*\slocation="([^"]*)"', xml)]
    return [t for t in out if t.strip()]


def chart_formulas(xml: str) -> list:
    return [unescape(t, _UNESC) for t in re.findall(r"<c:f\b[^>]*?(?<!/)>(.*?)</c:f>", xml, re.S) if t.strip()]


def _deterministic_uids(xml: str, seed: str) -> str:
    count = [0]

    def sub(m):
        count[0] += 1
        u = uuid.uuid5(_UID_NS, f"{seed}:{count[0]}")
        return f'{m.group(1)}="{{{str(u).upper()}}}"'
    xml = re.sub(r'((?:xr\d*:uid|a16:creationId\s+id|id)(?=="\{))="\{[0-9A-Fa-f-]{36}\}"', sub, xml)
    return xml


def clear_tab_selected(xml: str) -> str:
    return re.sub(r'\stabSelected="(?:1|true)"', "", xml)


def set_tab_selected(xml: str) -> str:
    m = re.search(r"<sheetView\b[^>]*?/?>", xml)
    if m:
        tag = m.group(0)
        new = re.sub(r'\stabSelected="[^"]*"', "", tag).replace("<sheetView", '<sheetView tabSelected="1"', 1)
        return xml.replace(tag, new, 1)
    return xml.replace("<sheetData", '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews><sheetData', 1)


def _cell_range(text: str):
    """``$L$4:$P$27`` or ``L4`` -> (col1, row1, col2, row2)."""
    def one(ref):
        m = re.match(r"\$?([A-Za-z]{1,3})\$?(\d+)$", ref)
        if not m:
            raise CloneError(f"not a cell range: {text}")
        col = 0
        for ch in m.group(1).upper():
            col = col * 26 + ord(ch) - 64
        return col, int(m.group(2))
    parts = text.split(":")
    a = one(parts[0])
    b = one(parts[-1])
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def _inside(inner, outer) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


@dataclass
class Analysis:
    graph: dict                      # sheet (lower) -> sheets it reads (lower)
    per_scenario: list               # sheet names, template order, score sheet first
    names_into_set: list             # DefinedName objects whose target is a per-scenario sheet
    originals: dict = field(default_factory=dict)        # part name -> original bytes


class ScenarioCloner:
    def __init__(self, pkg: Package, wb: WorkbookModel, score_sheet: str, *, blocks: Optional[dict] = None):
        self.pkg, self.wb = pkg, wb
        self.score = wb.sheet(score_sheet).name
        self.blocks = dict((k.lower(), v) for k, v in (blocks or {}).items())
        self.analysis = self._analyze()

    # ------------------------------------------------------------------ analysis
    def _drawing_charts(self, sheet_part: str) -> list:
        out = []
        for r in self.pkg.rels(sheet_part):
            if r.type == R_DRAWING:
                d = Package.resolve(sheet_part, r.target)
                out += [Package.resolve(d, c.target) for c in self.pkg.rels(d) if c.type == R_CHART]
        return out

    def _analyze(self) -> Analysis:
        wb, pkg = self.wb, self.pkg
        name_refs = dict((n.name.lower(), F.referenced_sheets(n.text)) for n in wb.names if n.scope is None)
        local_refs: dict = {}
        for n in wb.names:
            if n.scope is not None:
                local_refs.setdefault(n.scope.lower(), {})[n.name.lower()] = F.referenced_sheets(n.text)
        graph = {}
        for s in wb.sheets:
            xml = pkg.text(s.part)
            reads = set()
            texts = sheet_formulas(xml)
            for chart in self._drawing_charts(s.part):
                texts += chart_formulas(pkg.text(chart))
            for t in texts:
                reads |= F.referenced_sheets(t)
                for ident in F.identifiers(t):
                    reads |= local_refs.get(s.name.lower(), {}).get(ident, set()) or name_refs.get(ident, set())
            reads.discard(s.name.lower())
            graph[s.name.lower()] = reads
        members = {self.score.lower()}
        changed = True
        while changed:
            changed = False
            for s in wb.sheets:
                low = s.name.lower()
                if low in members or low in self.blocks:
                    continue
                if graph[low] & members:
                    members.add(low)
                    changed = True
        order = [s.name for s in wb.sheets if s.name.lower() in members]
        order.remove(self.score)
        per = [self.score] + order
        for name in per:                                   # refuse what cannot be copied
            part = wb.sheet(name).part
            xml = pkg.text(part)
            for marker, what in _REFUSE:
                if marker in xml:
                    raise CloneError(f"sheet {name!r} holds {what}; it cannot be copied per scenario")
            for r in pkg.rels(part):
                for marker, what in _REFUSE_RELS:
                    if r.type.endswith(marker):
                        raise CloneError(f"sheet {name!r} holds {what}; it cannot be copied per scenario")
        for block_sheet, rng in self.blocks.items():       # a block may be the only link into the set
            xml = pkg.text(wb.sheet(block_sheet).part)
            box = _cell_range(rng)
            for m in _CELL.finditer(xml):
                ref = re.search(r'\br="([A-Z]+\d+)"', m.group(1))
                fm = re.search(r"<f\b[^>]*?(?<!/)>(.*?)</f>", m.group(2) or "", re.S)
                if ref and fm and F.referenced_sheets(unescape(fm.group(1), _UNESC)) & members:
                    if not _inside(_cell_range(ref.group(1)), box):
                        raise CloneError(f"{block_sheet}!{ref.group(1)} reads the score sheet outside {rng}")
        into = []
        for n in wb.names:
            targets = F.referenced_sheets(n.text)
            scoped = n.scope is not None and n.scope.lower() in members
            if scoped or (targets & members and not targets - members - {""}):
                into.append(n)
        for s in per:                                      # no formula in the set may use such a name
            for t in sheet_formulas(pkg.text(wb.sheet(s).part)):
                used = F.identifiers(t)
                for n in into:
                    if n.scope is None and n.name.lower() in used:
                        raise CloneError(f"{s} uses the name {n.name}, which points into the per-scenario sheets")
        return Analysis(graph, per, into)

    # ------------------------------------------------------------------ originals
    def capture(self) -> None:
        """Remember the template parts the copies start from (before any rename or fill)."""
        keep = {}
        for name in self.analysis.per_scenario + [k for k in self.blocks]:
            part = self.wb.sheet(name).part
            keep[part] = self.pkg.get(part)
            for r in self.pkg.rels(part):
                if r.type == R_DRAWING:
                    d = Package.resolve(part, r.target)
                    keep[d] = self.pkg.get(d)
                    for c in self.pkg.rels(d):
                        if c.mode != "External":
                            target = Package.resolve(d, c.target)
                            if self.pkg.has(target):
                                keep[target] = self.pkg.get(target)
        self.analysis.originals = keep

    def original_text(self, sheet: str) -> str:
        return self.analysis.originals[self.wb.sheet(sheet).part].decode("utf-8-sig")

    # ------------------------------------------------------------------ the baseline, in place
    def rename_score(self, new_name: str) -> None:
        """Rename the score sheet everywhere (formulas, charts, names, hyperlinks)."""
        old = self.score
        mapping = {old.lower(): new_name}
        for s in self.wb.sheets:
            xml = self.pkg.text(s.part)
            new = rewrite_sheet_xml(xml, mapping)
            if new != xml:
                self.pkg.set_text(s.part, new)
            for chart in self._drawing_charts(s.part):
                cx = self.pkg.text(chart)
                nc = rewrite_chart_xml(cx, mapping)
                if nc != cx:
                    self.pkg.set_text(chart, nc)
        for n in self.wb.names:
            n.text = F.rewrite(n.text, mapping)
        self.wb.rename(old, new_name)
        self.analysis.per_scenario = [new_name if s == old else s for s in self.analysis.per_scenario]
        self.score_original = old
        self.score = new_name

    # ------------------------------------------------------------------ one alternative
    def add_alternative(self, k: int, name: str, fill: Callable[[str], str], *, seed: str,
                        hide_helpers: bool = True) -> list:
        """Copy the per-scenario set for alternative ``k`` (2, 3, ...); the score sheet's copy is
        named ``name``, the others ``S{k} <sheet>``. Returns the new sheet names, score first."""
        an = self.analysis
        original_score = getattr(self, "score_original", self.score)
        orig_names = [original_score] + [s for s in an.per_scenario if s != self.score]
        taken = set(s.name.lower() for s in self.wb.sheets)
        mapping = {original_score.lower(): name}
        for s in orig_names[1:]:
            mapping[s.lower()] = f"S{k} {s}"
        for new in mapping.values():
            if new.lower() in taken:
                raise CloneError(f"sheet name already used: {new}")
        block_names = {}
        for block_sheet in self.blocks:
            real = self.wb.sheet(block_sheet).name
            block_names[block_sheet] = f"S{k} {real} Block"
        created = []
        for idx, src in enumerate(orig_names):
            cur = self.score if src == original_score else src
            part = self.wb.sheet(cur).part
            xml = self.analysis.originals[part].decode("utf-8-sig")
            if idx == 0:
                xml = fill(xml)
            xml = rewrite_sheet_xml(xml, mapping)
            xml = clear_tab_selected(xml)
            xml = re.sub(r'\scodeName="[^"]*"', "", xml)
            xml = _deterministic_uids(xml, f"{seed}:{src}")
            new_name = mapping[src.lower()]
            state = "visible" if idx == 0 else ("hidden" if hide_helpers else "visible")
            sheet = self.wb.add_sheet(new_name, xml, state=state)
            self._copy_sheet_rels(part, sheet.part, dict(mapping, **dict(
                (b, block_names[b]) for b in block_names)), seed=f"{seed}:{src}")
            created.append(new_name)
        for block_sheet, rng in self.blocks.items():
            real = self.wb.sheet(block_sheet).name
            xml = self._block_sheet(real, rng, mapping)
            self.wb.add_sheet(block_names[block_sheet], xml, state="hidden")
            created.append(block_names[block_sheet])
        for n in an.names_into_set:                          # names copy as local names
            targets = F.referenced_sheets(n.text) or {(n.scope or "").lower()}
            scope_src = (n.scope or next(iter(sorted(targets)))).lower()
            if scope_src == self.score.lower():              # the baseline already carries its new name
                scope_src = original_score.lower()
            if scope_src not in mapping:
                continue
            text = F.rewrite(n.text, dict(mapping, **{self.score.lower(): name}))
            self.wb.names.append(DefinedName(n.name, text, mapping[scope_src], dict(n.extra)))
        return created

    def _copy_sheet_rels(self, src_part: str, dst_part: str, mapping: dict, *, seed: str) -> None:
        rels = self.pkg.rels(src_part)
        if not rels:
            return
        out = []
        for r in rels:
            if r.mode == "External":
                out.append(Rel(r.id, r.type, r.target, r.mode))
                continue
            target = Package.resolve(src_part, r.target)
            if r.type == R_DRAWING:
                new = self._copy_drawing(target, mapping, seed=seed)
            else:
                data = self.analysis.originals.get(target, self.pkg.get(target))
                stem, ext = target.rsplit(".", 1)
                base = re.sub(r"\d+$", "", stem)
                new = self.pkg.unique(base + "{}." + ext)
                self.pkg.add(new, data, self.pkg.content_type(target))
            out.append(Rel(r.id, r.type, Package.relative(dst_part, new)))
        self.pkg.write_rels(dst_part, out)

    def _copy_drawing(self, drawing: str, mapping: dict, *, seed: str) -> str:
        xml = self.analysis.originals.get(drawing, self.pkg.get(drawing)).decode("utf-8-sig")
        new_drawing = self.pkg.unique("xl/drawings/drawing{}.xml")
        self.pkg.add(new_drawing, _deterministic_uids(xml, seed + ":drawing").encode("utf-8"),
                     self.pkg.content_type(drawing) or CT_DRAWING)
        rels = []
        for r in self.pkg.rels(drawing):
            if r.mode == "External":
                rels.append(r)
                continue
            target = Package.resolve(drawing, r.target)
            if r.type == R_CHART:
                cx = self.analysis.originals.get(target, self.pkg.get(target)).decode("utf-8-sig")
                self._check_block_refs(cx)
                new_chart = self.pkg.unique("xl/charts/chart{}.xml")
                self.pkg.add(new_chart, rewrite_chart_xml(cx, mapping).encode("utf-8"),
                             self.pkg.content_type(target) or CT_CHART)
                crels = []
                for cr in self.pkg.rels(target):
                    ct = Package.resolve(target, cr.target)
                    if cr.mode == "External" or not re.search(r"/(style|colors)\d*\.xml$", ct):
                        crels.append(Rel(cr.id, cr.type, Package.relative(new_chart, ct) if cr.mode != "External"
                                         else cr.target, cr.mode))
                        continue
                    stem = re.sub(r"\d+\.xml$", "", ct)
                    copy = self.pkg.unique(stem + "{}.xml")
                    self.pkg.add(copy, self.pkg.get(ct), self.pkg.content_type(ct))
                    crels.append(Rel(cr.id, cr.type, Package.relative(new_chart, copy)))
                if crels:
                    self.pkg.write_rels(new_chart, crels)
                rels.append(Rel(r.id, r.type, Package.relative(new_drawing, new_chart)))
            else:                                            # images and other media are shared
                rels.append(Rel(r.id, r.type, Package.relative(new_drawing, target)))
        if rels:
            self.pkg.write_rels(new_drawing, rels)
        return new_drawing

    def _check_block_refs(self, chart_xml: str) -> None:
        """A copied chart may read a shared block sheet only inside the block it is given."""
        if not self.blocks:
            return
        for text in chart_formulas(chart_xml):
            for m in re.finditer(r"(?:'((?:[^']|'')+)'|([A-Za-z_][\w.]*))!(\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?)", text):
                sheet = (m.group(1) or m.group(2)).replace("''", "'").lower()
                if sheet in self.blocks and not _inside(_cell_range(m.group(3)), _cell_range(self.blocks[sheet])):
                    raise CloneError(f"a chart reads {sheet}!{m.group(3)}, outside the block {self.blocks[sheet]}")

    def _block_sheet(self, sheet: str, rng: str, mapping: dict) -> str:
        """A hidden sheet holding a copy of ``sheet``'s cells inside ``rng``, formulas rewritten."""
        xml = self.analysis.originals[self.wb.sheet(sheet).part].decode("utf-8-sig")
        box = _cell_range(rng)
        rows = []
        for rm in re.finditer(r"<row\b([^>]*)>(.*?)</row>", xml, re.S):
            r = int(re.search(r'\br="(\d+)"', rm.group(1)).group(1))
            if not box[1] <= r <= box[3]:
                continue
            cells = []
            for cm in re.finditer(r'<c\b[^>]*\br="([A-Z]+)(\d+)"[^>]*?(?:/>|>.*?</c>)', rm.group(2), re.S):
                col = _cell_range(cm.group(1) + cm.group(2))[0]
                if box[0] <= col <= box[2]:
                    cell = cm.group(0)
                    ref = re.search(r'<f\b[^>]*\bref="([^"]+)"', cell)
                    if ref and not _inside(_cell_range(ref.group(1)), box):
                        raise CloneError(f"a shared formula at {cm.group(1)}{cm.group(2)} reaches outside {rng}")
                    cells.append(rewrite_sheet_xml(cell, mapping))
            if cells:
                rows.append(f'<row r="{r}">' + "".join(cells) + "</row>")
        return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<sheetData>{''.join(rows)}</sheetData></worksheet>")
