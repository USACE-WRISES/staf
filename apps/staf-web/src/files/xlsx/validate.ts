// Checks an assembled workbook the way Excel would before calling it unreadable (libs/staf_workbook
// xlsx/validate.py): every part parses and has a content type, every relationship resolves, the sheet
// list is consistent, exactly one tab is selected, defined names are unique in their scope, each
// worksheet's elements come in the schema's order, and style and conditional-format indices are in
// range. ElementTree's parse is stood in for by a small well-formedness check (tags nested and closed,
// attributes quoted, entities known, prefixes bound), enough for what the writers produce.
import { Package } from './package'
import { WorkbookModel } from './workbook'

/** CT_Worksheet's sequence (ECMA-376 Part 1, 18.3.1.99); other children (mc:AlternateContent) are skipped */
const SHEET_ORDER = ['sheetPr', 'dimension', 'sheetViews', 'sheetFormatPr', 'cols', 'sheetData', 'sheetCalcPr',
  'sheetProtection', 'protectedRanges', 'scenarios', 'autoFilter', 'sortState', 'dataConsolidate',
  'customSheetViews', 'mergeCells', 'phoneticPr', 'conditionalFormatting', 'dataValidations',
  'hyperlinks', 'printOptions', 'pageMargins', 'pageSetup', 'headerFooter', 'rowBreaks', 'colBreaks',
  'customProperties', 'cellWatches', 'ignoredErrors', 'smartTags', 'drawing', 'legacyDrawing',
  'legacyDrawingHF', 'drawingHF', 'picture', 'oleObjects', 'controls', 'webPublishItems',
  'tableParts', 'extLst']
const RANK = new Map(SHEET_ORDER.map((tag, k) => [tag, k]))

function outOfOrder(children: string[]): boolean {
  const ranks = children.filter((c) => RANK.has(c)).map((c) => RANK.get(c)!)
  return ranks.some((b, k) => k > 0 && b < ranks[k - 1])
}

const NAME = /^[A-Za-z_À-￿][\w.\-·À-￿]*(?::[A-Za-z_À-￿][\w.\-·À-￿]*)?$/
const ENTITY = /&(?:lt|gt|amp|quot|apos|#\d+|#x[0-9A-Fa-f]+);/y

function checkText(text: string, where: number): void {
  for (let i = text.indexOf('&'); i >= 0; i = text.indexOf('&', i + 1)) {
    ENTITY.lastIndex = i
    if (!ENTITY.test(text)) throw new Error(`undefined entity: line 1, column ${where + i}`)
  }
  if (text.includes('<')) throw new Error(`not well-formed (invalid token): column ${where}`)
}

/** ET.fromstring's verdict: the root element's local name and its children's, or an error. */
export function parseXml(xml: string): { root: string, children: string[] } {
  let i = 0
  const n = xml.length
  if (xml.charCodeAt(0) === 0xfeff) i = 1
  const stack: { name: string, ns: Map<string, string> }[] = []
  let root: string | null = null
  const children: string[] = []
  const local = (q: string) => q.slice(q.indexOf(':') + 1)
  const bound = (prefix: string, scopes: Map<string, string>[]) => prefix === 'xml' || prefix === 'xmlns' || scopes.some((s) => s.has(prefix))
  while (i < n) {
    const lt = xml.indexOf('<', i)
    const text = xml.slice(i, lt < 0 ? n : lt)
    if (text.trim() && !stack.length) throw new Error(`junk outside the root element: column ${i}`)
    checkText(text, i)
    if (lt < 0) break
    if (xml.startsWith('<!--', lt)) {
      const end = xml.indexOf('-->', lt + 4)
      if (end < 0) throw new Error('unclosed comment')
      i = end + 3
      continue
    }
    if (xml.startsWith('<?', lt)) {
      const end = xml.indexOf('?>', lt + 2)
      if (end < 0) throw new Error('unclosed processing instruction')
      i = end + 2
      continue
    }
    if (xml.startsWith('<![CDATA[', lt)) {
      const end = xml.indexOf(']]>', lt + 9)
      if (end < 0 || !stack.length) throw new Error('bad CDATA section')
      i = end + 3
      continue
    }
    if (xml.startsWith('<!', lt)) {
      const end = xml.indexOf('>', lt + 2)
      if (end < 0 || stack.length || root !== null) throw new Error('bad declaration')
      i = end + 1
      continue
    }
    if (xml[lt + 1] === '/') {
      const end = xml.indexOf('>', lt + 2)
      if (end < 0) throw new Error('unclosed end tag')
      const name = xml.slice(lt + 2, end).trim()
      const top = stack.pop()
      if (!top || top.name !== name) throw new Error(`mismatched tag: column ${lt}`)
      i = end + 1
      continue
    }
    // a start tag: its name, then attributes up to > or />
    let j = lt + 1
    while (j < n && !/[\s/>]/.test(xml[j])) j++
    const name = xml.slice(lt + 1, j)
    if (!NAME.test(name)) throw new Error(`not well-formed (invalid token): column ${lt}`)
    const attrs = new Map<string, string>()
    let selfClose = false
    for (;;) {
      while (j < n && /\s/.test(xml[j])) j++
      if (j >= n) throw new Error('unclosed token')
      if (xml[j] === '>') {
        j++
        break
      }
      if (xml[j] === '/' && xml[j + 1] === '>') {
        selfClose = true
        j += 2
        break
      }
      const m = /^([^\s=/>]+)\s*=\s*("[^"<]*"|'[^'<]*')/.exec(xml.slice(j, j + 4096))
      if (!m || !NAME.test(m[1]) || attrs.has(m[1])) throw new Error(`not well-formed (invalid token): column ${j}`)
      checkText(m[2].slice(1, -1), j)
      attrs.set(m[1], m[2].slice(1, -1))
      j += m[0].length
    }
    const ns = new Map<string, string>()
    for (const [k, v] of attrs) if (k.startsWith('xmlns:')) ns.set(k.slice(6), v)
    const scopes = [...stack.map((s) => s.ns), ns]
    for (const q of [name, ...[...attrs.keys()].filter((k) => !k.startsWith('xmlns'))]) {
      const c = q.indexOf(':')
      if (c >= 0 && !bound(q.slice(0, c), scopes)) throw new Error(`unbound prefix: column ${lt}`)
    }
    if (!stack.length) {
      if (root !== null) throw new Error(`junk after document element: column ${lt}`)
      root = local(name)
    } else if (stack.length === 1) children.push(local(name))
    if (!selfClose) stack.push({ name, ns })
    i = j
  }
  if (root === null) throw new Error('no element found')
  if (stack.length) throw new Error('unclosed token')
  return { root, children }
}

/** validate_package: what is wrong with the workbook, as Excel would find it (empty when nothing). */
export async function validatePackage(data: Uint8Array): Promise<string[]> {
  const problems: string[] = []
  let pkg: Package
  try {
    pkg = await Package.open(data)
  } catch (err) {
    return [`not a zip file: ${err instanceof Error ? err.message : String(err)}`]
  }
  const children = new Map<string, string[]>()          // worksheet part to its child element names
  for (const [name, blob] of pkg.parts) {
    if (name.endsWith('.xml') || name.endsWith('.rels') || name.endsWith('.vml')) {
      try {
        const got = parseXml(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(blob))
        if (got.root === 'worksheet') children.set(name, got.children)
      } catch (err) {
        problems.push(`${name} does not parse: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    if (name !== '[Content_Types].xml' && pkg.contentType(name) === null) problems.push(`${name} has no content type`)
  }
  for (const name of [...pkg.parts.keys()]) {
    if (!name.endsWith('.rels')) continue
    const owner = name.replace(/_rels\/([^/]*)\.rels$/, '$1')
    for (const r of pkg.rels(owner)) {
      if (r.mode === 'External') continue
      if (!pkg.has(Package.resolve(owner, r.target!))) problems.push(`${name}: ${r.id} points at a missing part ${r.target}`)
    }
  }
  let wb: WorkbookModel
  try {
    wb = new WorkbookModel(pkg)
  } catch (err) {
    return [...problems, `workbook does not read: ${err instanceof Error ? err.message : String(err)}`]
  }
  const names = wb.sheets.map((s) => s.name.toLowerCase())
  if (names.length !== new Set(names).size) problems.push('duplicate sheet names')
  if (!wb.sheets.some((s) => s.state === 'visible')) problems.push('no visible sheet')
  const m = /<workbookView\b[^>]*\bactiveTab="(\d+)"/.exec(wb.xml)
  const active = m ? Number(m[1]) : 0
  if (active >= wb.sheets.length || wb.sheets[active].state !== 'visible') problems.push(`activeTab ${active} is not a visible sheet`)
  const selected = wb.sheets.filter((s) => /<sheetView\b[^>]*\btabSelected="(?:1|true)"/.test(pkg.text(s.part))).map((s) => s.name)
  if (selected.length !== 1) problems.push(`${selected.length} selected tabs (must be exactly one): ${JSON.stringify(selected)}`)
  else if (wb.sheets.length && selected[0] !== wb.sheets[active]?.name) problems.push(`the selected tab '${selected[0]}' is not the active tab`)
  const seen = new Set<string>()
  for (const nm of wb.names) {
    const key = JSON.stringify([nm.name.toLowerCase(), (nm.scope ?? '').toLowerCase()])
    if (seen.has(key)) problems.push(`defined name ${nm.name} repeated in one scope`)
    seen.add(key)
  }
  const sid = wb.sheets.map((s) => s.sheetId)
  if (sid.length !== new Set(sid).size) problems.push('duplicate sheetId')
  for (const s of wb.sheets) if (outOfOrder(children.get(s.part) ?? [])) problems.push(`${s.name}: worksheet elements out of schema order`)
  if (pkg.has('xl/styles.xml')) {
    const styles = pkg.text('xl/styles.xml')
    for (const [tag, child] of [['numFmts', 'numFmt'], ['fonts', 'font'], ['fills', 'fill'], ['borders', 'border'], ['cellXfs', 'xf'], ['dxfs', 'dxf']]) {
      const mm = new RegExp(`<${tag}\\b[^>]*\\bcount="(\\d+)"[^>]*>([\\s\\S]*?)</${tag}>`).exec(styles)
      if (mm && Number(mm[1]) !== [...mm[2].matchAll(new RegExp(`<${child}\\b`, 'g'))].length) problems.push(`styles: ${tag} count does not match its children`)
    }
    let mm = /<cellXfs\b[^>]*>([\s\S]*?)<\/cellXfs>/.exec(styles)
    const nXf = mm ? [...mm[1].matchAll(/<xf\b/g)].length : 0
    mm = /<dxfs\b[^>]*?(?<!\/)>([\s\S]*?)<\/dxfs>/.exec(styles)
    const nDxf = mm ? [...mm[1].matchAll(/<dxf\b/g)].length : 0
    for (const s of wb.sheets) {
      const xml = pkg.text(s.part)
      const used = [...xml.matchAll(/<c\b[^>]*\bs="(\d+)"/g)].map((x) => Number(x[1]))
      if (used.length && Math.max(...used) >= nXf) problems.push(`${s.name}: a cell uses style ${Math.max(...used)} of ${nXf}`)
      const rules = [...xml.matchAll(/<cfRule\b[^>]*\bdxfId="(\d+)"/g)].map((x) => Number(x[1]))
      if (rules.length && Math.max(...rules) >= nDxf) problems.push(`${s.name}: a conditional format uses format ${Math.max(...rules)} of ${nDxf}`)
    }
  }
  return problems
}
