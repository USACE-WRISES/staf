// One calculator per scenario (libs/staf_workbook xlsx/clone.py): the template's per-scenario sheets
// copied with every reference rewritten so each copy reads only itself and the shared sheets.
//
// The per-scenario set is computed, not configured: the score sheet plus every sheet that depends on
// it, through formulas, defined names, data validations, conditional formats or charts. A shared
// sheet that reads the score sheet in one block only (SFARI's Instructions!L4:P27, which feeds the
// score sheet's charts) is named in ``blocks``; each alternative gets a hidden copy of that block.
// Unsupported content in the set (tables, pivots, controls, comments, external links) refuses the
// whole assembly.
import { uuid5 } from '../bytes'
import { reEscape, xmlEscape, xmlUnescape } from '../py'
import * as F from './formula'
import { CT_CHART, CT_DRAWING, Package, R_CHART, R_DRAWING, type Rel, rel } from './package'
import { type DefinedName, definedName, WorkbookModel } from './workbook'

const UNESC = { '&quot;': '"', '&apos;': "'" }
const UID_NS = '6f1c2a52-4e7f-4a8e-9b1d-7d1d8f0a5c11'
const FORMULA_TAGS = ['f', 'formula', 'formula1', 'formula2', 'xm:f']
const REFUSE: [string, string][] = [['<tableParts', 'a table'], ['<oleObjects', 'an embedded object'], ['<controls', 'a form control'],
  ['<legacyDrawing', 'comments']]
const REFUSE_RELS: [string, string][] = [['/pivotTable', 'a pivot table'], ['/table', 'a table'], ['/oleObject', 'an embedded object'],
  ['/ctrlProp', 'a form control'], ['/comments', 'comments'], ['/externalLink', 'an external link']]
const CELL = /<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g

/** The template cannot be copied per scenario as it is. */
export class CloneError extends Error {}

const tagPattern = (tag: string) => new RegExp(`(<${reEscape(tag)}\\b[^>]*?(?<!/)>)([\\s\\S]*?)(</${reEscape(tag)}>)`, 'g')

function rewriteTag(xml: string, tag: string, fn: (text: string) => string): string {
  return xml.replace(tagPattern(tag), (all: string, open: string, body: string, close: string) => {
    const text = xmlUnescape(body, UNESC)
    const next = fn(text)
    return next === text ? all : open + xmlEscape(next) + close
  })
}

/** rewrite_sheet_xml: every formula-bearing element and internal hyperlink of a sheet, sheets renamed. */
export function rewriteSheetXml(xml: string, mapping: Map<string, string>): string {
  if (!mapping.size) return xml
  for (const tag of FORMULA_TAGS) xml = rewriteTag(xml, tag, (t) => F.rewrite(t, mapping))
  return xml.replace(/(<hyperlink\b[^>]*\slocation=")([^"]*)(")/g, (all: string, open: string, value: string, close: string) => {
    const loc = xmlUnescape(value, UNESC)
    const next = F.rewrite(loc, mapping)
    return next === loc ? all : open + xmlEscape(next, { '"': '&quot;' }) + close
  })
}

export function rewriteChartXml(xml: string, mapping: Map<string, string>): string {
  return mapping.size ? rewriteTag(xml, 'c:f', (t) => F.rewrite(t, mapping)) : xml
}

const strip = (t: string) => t.trim() !== ''

export function sheetFormulas(xml: string): string[] {
  const out: string[] = []
  for (const tag of FORMULA_TAGS) {
    const re = new RegExp(`<${reEscape(tag)}\\b[^>]*?(?<!/)>([\\s\\S]*?)</${reEscape(tag)}>`, 'g')
    for (const m of xml.matchAll(re)) out.push(xmlUnescape(m[1], UNESC))
  }
  for (const m of xml.matchAll(/<hyperlink\b[^>]*\slocation="([^"]*)"/g)) out.push(xmlUnescape(m[1], UNESC))
  return out.filter(strip)
}

export function chartFormulas(xml: string): string[] {
  return [...xml.matchAll(/<c:f\b[^>]*?(?<!\/)>([\s\S]*?)<\/c:f>/g)].map((m) => m[1]).filter(strip).map((t) => xmlUnescape(t, UNESC))
}

function deterministicUids(xml: string, seed: string): string {
  let count = 0
  return xml.replace(/((?:xr\d*:uid|a16:creationId\s+id|id)(?=="\{))="\{[0-9A-Fa-f-]{36}\}"/g, (_all: string, name: string) => {
    count += 1
    return `${name}="{${uuid5(UID_NS, `${seed}:${count}`).toUpperCase()}}"`
  })
}

export function clearTabSelected(xml: string): string {
  return xml.replace(/\stabSelected="(?:1|true)"/g, '')
}

export function setTabSelected(xml: string): string {
  const m = /<sheetView\b[^>]*?\/?>/.exec(xml)
  if (m) {
    const tag = m[0]
    const next = tag.replace(/\stabSelected="[^"]*"/g, '').replace('<sheetView', () => '<sheetView tabSelected="1"')
    const i = xml.indexOf(tag)
    return xml.slice(0, i) + next + xml.slice(i + tag.length)
  }
  const i = xml.indexOf('<sheetData')
  return i < 0 ? xml : xml.slice(0, i) + '<sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews>' + xml.slice(i)
}

type Box = [number, number, number, number]

/** _cell_range: $L$4:$P$27 or L4 as (col1, row1, col2, row2). */
function cellRange(text: string): Box {
  const one = (ref: string): [number, number] => {
    const m = /^\$?([A-Za-z]{1,3})\$?(\d+)$/.exec(ref)
    if (!m) throw new CloneError(`not a cell range: ${text}`)
    let col = 0
    for (const ch of m[1].toUpperCase()) col = col * 26 + ch.charCodeAt(0) - 64
    return [col, Number(m[2])]
  }
  const parts = text.split(':')
  const a = one(parts[0])
  const b = one(parts[parts.length - 1])
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]
}

const inside = (inner: Box, outer: Box) => outer[0] <= inner[0] && outer[1] <= inner[1] && inner[2] <= outer[2] && inner[3] <= outer[3]

export interface Analysis {
  /** sheet (lower) to the sheets it reads (lower) */
  graph: Map<string, Set<string>>
  /** sheet names, template order, the score sheet first */
  perScenario: string[]
  /** defined names whose target is a per-scenario sheet */
  namesIntoSet: DefinedName[]
  /** part name to its original bytes */
  originals: Map<string, Uint8Array>
}

const decodeSig = (b: Uint8Array) => new TextDecoder('utf-8', { fatal: true }).decode(b)
const union = (a: Set<string>, b: Iterable<string>) => {
  for (const x of b) a.add(x)
}

export class ScenarioCloner {
  score: string
  scoreOriginal: string | null = null
  blocks: Map<string, string>
  analysis: Analysis

  constructor(private pkg: Package, private wb: WorkbookModel, scoreSheet: string, blocks: Record<string, string> = {}) {
    this.score = wb.sheet(scoreSheet).name
    this.blocks = new Map(Object.entries(blocks).map(([k, v]) => [k.toLowerCase(), v]))
    this.analysis = this.analyze()
  }

  // ---------------------------------------------------------------- analysis
  private drawingCharts(sheetPart: string): string[] {
    const out: string[] = []
    for (const r of this.pkg.rels(sheetPart)) {
      if (r.type === R_DRAWING) {
        const d = Package.resolve(sheetPart, r.target!)
        for (const c of this.pkg.rels(d)) if (c.type === R_CHART) out.push(Package.resolve(d, c.target!))
      }
    }
    return out
  }

  private analyze(): Analysis {
    const { wb, pkg } = this
    const nameRefs = new Map<string, Set<string>>()
    for (const n of wb.names) if (n.scope === null) nameRefs.set(n.name.toLowerCase(), F.referencedSheets(n.text))
    const localRefs = new Map<string, Map<string, Set<string>>>()
    for (const n of wb.names) {
      if (n.scope === null) continue
      const key = n.scope.toLowerCase()
      if (!localRefs.has(key)) localRefs.set(key, new Map())
      localRefs.get(key)!.set(n.name.toLowerCase(), F.referencedSheets(n.text))
    }
    const graph = new Map<string, Set<string>>()
    for (const s of wb.sheets) {
      const xml = pkg.text(s.part)
      const reads = new Set<string>()
      const texts = sheetFormulas(xml)
      for (const chart of this.drawingCharts(s.part)) texts.push(...chartFormulas(pkg.text(chart)))
      for (const t of texts) {
        union(reads, F.referencedSheets(t))
        for (const ident of F.identifiers(t)) {
          const local = localRefs.get(s.name.toLowerCase())?.get(ident) ?? new Set<string>()
          union(reads, local.size ? local : nameRefs.get(ident) ?? [])
        }
      }
      reads.delete(s.name.toLowerCase())
      graph.set(s.name.toLowerCase(), reads)
    }
    const members = new Set([this.score.toLowerCase()])
    let changed = true
    while (changed) {
      changed = false
      for (const s of wb.sheets) {
        const low = s.name.toLowerCase()
        if (members.has(low) || this.blocks.has(low)) continue
        if ([...graph.get(low)!].some((x) => members.has(x))) {
          members.add(low)
          changed = true
        }
      }
    }
    const order = wb.sheets.filter((s) => members.has(s.name.toLowerCase())).map((s) => s.name)
    order.splice(order.indexOf(this.score), 1)
    const per = [this.score, ...order]
    for (const name of per) {                        // refuse what cannot be copied
      const part = wb.sheet(name).part
      const xml = pkg.text(part)
      for (const [marker, what] of REFUSE) {
        if (xml.includes(marker)) throw new CloneError(`sheet '${name}' holds ${what}; it cannot be copied per scenario`)
      }
      for (const r of pkg.rels(part)) {
        for (const [marker, what] of REFUSE_RELS) {
          if ((r.type ?? '').endsWith(marker)) throw new CloneError(`sheet '${name}' holds ${what}; it cannot be copied per scenario`)
        }
      }
    }
    for (const [blockSheet, rng] of this.blocks) {   // a block may be the only link into the set
      const xml = pkg.text(wb.sheet(blockSheet).part)
      const box = cellRange(rng)
      for (const m of xml.matchAll(CELL)) {
        const ref = /\br="([A-Z]+\d+)"/.exec(m[1])
        const fm = /<f\b[^>]*?(?<!\/)>([\s\S]*?)<\/f>/.exec(m[2] ?? '')
        if (ref && fm && [...F.referencedSheets(xmlUnescape(fm[1], UNESC))].some((x) => members.has(x))) {
          if (!inside(cellRange(ref[1]), box)) throw new CloneError(`${blockSheet}!${ref[1]} reads the score sheet outside ${rng}`)
        }
      }
    }
    const into: DefinedName[] = []
    for (const n of wb.names) {
      const targets = F.referencedSheets(n.text)
      const scoped = n.scope !== null && members.has(n.scope.toLowerCase())
      const hits = [...targets].some((t) => members.has(t))
      const outside = [...targets].some((t) => !members.has(t) && t !== '')
      if (scoped || (hits && !outside)) into.push(n)
    }
    for (const s of per) {                           // no formula in the set may use such a name
      for (const t of sheetFormulas(pkg.text(wb.sheet(s).part))) {
        const used = F.identifiers(t)
        for (const n of into) {
          if (n.scope === null && used.has(n.name.toLowerCase())) {
            throw new CloneError(`${s} uses the name ${n.name}, which points into the per-scenario sheets`)
          }
        }
      }
    }
    return { graph, perScenario: per, namesIntoSet: into, originals: new Map() }
  }

  // ---------------------------------------------------------------- originals
  /** Remember the template parts the copies start from (before any rename or fill). */
  capture(): void {
    const keep = new Map<string, Uint8Array>()
    for (const name of [...this.analysis.perScenario, ...this.blocks.keys()]) {
      const part = this.wb.sheet(name).part
      keep.set(part, this.pkg.get(part))
      for (const r of this.pkg.rels(part)) {
        if (r.type !== R_DRAWING) continue
        const d = Package.resolve(part, r.target!)
        keep.set(d, this.pkg.get(d))
        for (const c of this.pkg.rels(d)) {
          if (c.mode === 'External') continue
          const target = Package.resolve(d, c.target!)
          if (this.pkg.has(target)) keep.set(target, this.pkg.get(target))
        }
      }
    }
    this.analysis.originals = keep
  }

  originalText(sheet: string): string {
    return decodeSig(this.analysis.originals.get(this.wb.sheet(sheet).part)!)
  }

  // ---------------------------------------------------------------- the baseline, in place
  /** Rename the score sheet everywhere (formulas, charts, names, hyperlinks). */
  renameScore(newName: string): void {
    const old = this.score
    const mapping = new Map([[old.toLowerCase(), newName]])
    for (const s of this.wb.sheets) {
      const xml = this.pkg.text(s.part)
      const next = rewriteSheetXml(xml, mapping)
      if (next !== xml) this.pkg.setText(s.part, next)
      for (const chart of this.drawingCharts(s.part)) {
        const cx = this.pkg.text(chart)
        const nc = rewriteChartXml(cx, mapping)
        if (nc !== cx) this.pkg.setText(chart, nc)
      }
    }
    for (const n of this.wb.names) n.text = F.rewrite(n.text, mapping)
    this.wb.rename(old, newName)
    this.analysis.perScenario = this.analysis.perScenario.map((s) => (s === old ? newName : s))
    this.scoreOriginal = old
    this.score = newName
  }

  // ---------------------------------------------------------------- one alternative
  /** Copy the per-scenario set for alternative ``k`` (2, 3, ...); the score sheet's copy is named
   * ``name``, the others ``S{k} <sheet>``. Returns the new sheet names, score first. */
  addAlternative(k: number, name: string, fill: (xml: string) => string, seed: string, hideHelpers = true): string[] {
    const an = this.analysis
    const originalScore = this.scoreOriginal ?? this.score
    const origNames = [originalScore, ...an.perScenario.filter((s) => s !== this.score)]
    const taken = new Set(this.wb.sheets.map((s) => s.name.toLowerCase()))
    const mapping = new Map([[originalScore.toLowerCase(), name]])
    for (const s of origNames.slice(1)) mapping.set(s.toLowerCase(), `S${k} ${s}`)
    for (const next of mapping.values()) if (taken.has(next.toLowerCase())) throw new CloneError(`sheet name already used: ${next}`)
    const blockNames = new Map<string, string>()
    for (const blockSheet of this.blocks.keys()) blockNames.set(blockSheet, `S${k} ${this.wb.sheet(blockSheet).name} Block`)
    const created: string[] = []
    for (const [idx, src] of origNames.entries()) {
      const cur = src === originalScore ? this.score : src
      const part = this.wb.sheet(cur).part
      let xml = decodeSig(this.analysis.originals.get(part)!)
      if (idx === 0) xml = fill(xml)
      xml = rewriteSheetXml(xml, mapping)
      xml = clearTabSelected(xml)
      xml = xml.replace(/\scodeName="[^"]*"/g, '')
      xml = deterministicUids(xml, `${seed}:${src}`)
      const newName = mapping.get(src.toLowerCase())!
      const state = idx === 0 ? 'visible' : hideHelpers ? 'hidden' : 'visible'
      const sheet = this.wb.addSheet(newName, xml, state)
      this.copySheetRels(part, sheet.part, new Map([...mapping, ...blockNames]), `${seed}:${src}`)
      created.push(newName)
    }
    for (const [blockSheet, rng] of this.blocks) {
      const real = this.wb.sheet(blockSheet).name
      this.wb.addSheet(blockNames.get(blockSheet)!, this.blockSheet(real, rng, mapping), 'hidden')
      created.push(blockNames.get(blockSheet)!)
    }
    for (const n of an.namesIntoSet) {               // names copy as local names
      let targets = F.referencedSheets(n.text)
      if (!targets.size) targets = new Set([(n.scope ?? '').toLowerCase()])
      let scopeSrc = (n.scope || [...targets].sort()[0]).toLowerCase()
      if (scopeSrc === this.score.toLowerCase()) scopeSrc = originalScore.toLowerCase()   // the baseline carries its new name
      if (!mapping.has(scopeSrc)) continue
      const text = F.rewrite(n.text, new Map([...mapping, [this.score.toLowerCase(), name]]))
      this.wb.names.push(definedName(n.name, text, mapping.get(scopeSrc)!, new Map(n.extra)))
    }
    return created
  }

  private copySheetRels(srcPart: string, dstPart: string, mapping: Map<string, string>, seed: string): void {
    const rels = this.pkg.rels(srcPart)
    if (!rels.length) return
    const out: Rel[] = []
    for (const r of rels) {
      if (r.mode === 'External') {
        out.push({ ...r })
        continue
      }
      const target = Package.resolve(srcPart, r.target!)
      let next: string
      if (r.type === R_DRAWING) next = this.copyDrawing(target, mapping, seed)
      else {
        const data = this.analysis.originals.get(target) ?? this.pkg.get(target)
        const dot = target.lastIndexOf('.')
        const stem = target.slice(0, dot)
        const ext = target.slice(dot + 1)
        next = this.pkg.unique(stem.replace(/\d+$/, '') + '{}.' + ext)
        this.pkg.add(next, data, this.pkg.contentType(target))
      }
      out.push(rel(r.id!, r.type!, Package.relative(dstPart, next)))
    }
    this.pkg.writeRels(dstPart, out)
  }

  private copyDrawing(drawing: string, mapping: Map<string, string>, seed: string): string {
    const xml = decodeSig(this.analysis.originals.get(drawing) ?? this.pkg.get(drawing))
    const newDrawing = this.pkg.unique('xl/drawings/drawing{}.xml')
    this.pkg.add(newDrawing, new TextEncoder().encode(deterministicUids(xml, seed + ':drawing')), this.pkg.contentType(drawing) ?? CT_DRAWING)
    const rels: Rel[] = []
    for (const r of this.pkg.rels(drawing)) {
      if (r.mode === 'External') {
        rels.push(r)
        continue
      }
      const target = Package.resolve(drawing, r.target!)
      if (r.type === R_CHART) {
        const cx = decodeSig(this.analysis.originals.get(target) ?? this.pkg.get(target))
        this.checkBlockRefs(cx)
        const newChart = this.pkg.unique('xl/charts/chart{}.xml')
        this.pkg.add(newChart, new TextEncoder().encode(rewriteChartXml(cx, mapping)), this.pkg.contentType(target) ?? CT_CHART)
        const crels: Rel[] = []
        for (const cr of this.pkg.rels(target)) {
          const ct = Package.resolve(target, cr.target!)
          if (cr.mode === 'External' || !/\/(style|colors)\d*\.xml$/.test(ct)) {
            crels.push({ id: cr.id, type: cr.type, target: cr.mode !== 'External' ? Package.relative(newChart, ct) : cr.target, mode: cr.mode })
            continue
          }
          const copy = this.pkg.unique(ct.replace(/\d+\.xml$/, '') + '{}.xml')
          this.pkg.add(copy, this.pkg.get(ct), this.pkg.contentType(ct))
          crels.push(rel(cr.id!, cr.type!, Package.relative(newChart, copy)))
        }
        if (crels.length) this.pkg.writeRels(newChart, crels)
        rels.push(rel(r.id!, r.type!, Package.relative(newDrawing, newChart)))
      } else {                                       // images and other media are shared
        rels.push(rel(r.id!, r.type!, Package.relative(newDrawing, target)))
      }
    }
    if (rels.length) this.pkg.writeRels(newDrawing, rels)
    return newDrawing
  }

  /** A copied chart may read a shared block sheet only inside the block it is given. */
  private checkBlockRefs(chartXml: string): void {
    if (!this.blocks.size) return
    for (const text of chartFormulas(chartXml)) {
      for (const m of text.matchAll(/(?:'((?:[^']|'')+)'|([A-Za-z_][\p{L}\p{N}_.]*))!(\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?)/gu)) {
        const sheet = (m[1] ?? m[2]).split("''").join("'").toLowerCase()
        const block = this.blocks.get(sheet)
        if (block !== undefined && !inside(cellRange(m[3]), cellRange(block))) {
          throw new CloneError(`a chart reads ${sheet}!${m[3]}, outside the block ${block}`)
        }
      }
    }
  }

  /** A hidden sheet holding a copy of ``sheet``'s cells inside ``rng``, formulas rewritten. */
  private blockSheet(sheet: string, rng: string, mapping: Map<string, string>): string {
    const xml = decodeSig(this.analysis.originals.get(this.wb.sheet(sheet).part)!)
    const box = cellRange(rng)
    const rows: string[] = []
    for (const rm of xml.matchAll(/<row\b([^>]*)>([\s\S]*?)<\/row>/g)) {
      const r = Number(/\br="(\d+)"/.exec(rm[1])![1])
      if (!(box[1] <= r && r <= box[3])) continue
      const cells: string[] = []
      for (const cm of rm[2].matchAll(/<c\b[^>]*\br="([A-Z]+)(\d+)"[^>]*?(?:\/>|>[\s\S]*?<\/c>)/g)) {
        const col = cellRange(cm[1] + cm[2])[0]
        if (box[0] <= col && col <= box[2]) {
          const cell = cm[0]
          const ref = /<f\b[^>]*\bref="([^"]+)"/.exec(cell)
          if (ref && !inside(cellRange(ref[1]), box)) throw new CloneError(`a shared formula at ${cm[1]}${cm[2]} reaches outside ${rng}`)
          cells.push(rewriteSheetXml(cell, mapping))
        }
      }
      if (cells.length) rows.push(`<row r="${r}">` + cells.join('') + '</row>')
    }
    return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      + 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
      + `<sheetData>${rows.join('')}</sheetData></worksheet>`
  }
}
