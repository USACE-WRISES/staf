// One workbook for an assessment and its scenarios, built from a tool's calculator template
// (libs/staf_workbook assemble.py). The template is never changed. The result opens on its first tab:
// the template's lead sheets when the tool names them (Instructions in all three tools, owner
// 2026-10-19), else Summary. Then come Summary, Existing Conditions (the template's score sheet,
// renamed), one tab per alternative (each a working copy of the calculator), ReferenceCurves, and the
// template's other sheets as they were. Each alternative's helper sheets are hidden. Excel recalculates
// on open.
import type { ScenarioScores } from '../../shared/scenarios'
import type { SummaryInfo } from '../../shared/summary'
import { clearTabSelected, ScenarioCloner, setTabSelected } from './clone'
import { Package } from './package'
import { addCurvesSheet, addSummarySheet, type Banner, type RefCurves, type SummaryCells } from './sheets'
import { StyleBook } from './styles'
import { validatePackage } from './validate'
import { WorkbookModel } from './workbook'

export const EC_NAME = 'Existing Conditions'

/** The assembled workbook failed its own checks (never served). */
export class AssemblyError extends Error {}

export interface AssemblySpec {
  /** "EASI", "SFARI", "DEEP" */
  appTitle: string
  /** the template's score sheet */
  scoreSheet: string
  blocks?: Record<string, string>
  hideHelpers?: boolean
  forceRecalc?: boolean
  /** template sheets before Summary, the first one open */
  lead?: string[]
}

export interface ScenarioInput { id: string, name: string, description: string, fill: (xml: string) => string }

export interface Built {
  pkg: Package
  wb: WorkbookModel
  ecSheet: string
  /** the score-sheet copies, in scenario order */
  altSheets: string[]
  /** alternative sheet to {template sheet: its copy} */
  helpers: Map<string, Map<string, string>>
  scoreTemplate: string
}

/** build: the baseline filled in place and renamed, and one calculator copy per alternative. */
export async function build(template: Uint8Array, spec: AssemblySpec, scenarios: ScenarioInput[]): Promise<Built> {
  if (!scenarios.length) throw new Error('at least the Existing Conditions scenario is needed')
  const pkg = await Package.open(template)
  const wb = new WorkbookModel(pkg)
  const cloner = new ScenarioCloner(pkg, wb, spec.scoreSheet, spec.blocks ?? {})
  cloner.capture()
  const scoreTemplate = cloner.score
  const part = wb.sheet(scoreTemplate).part
  pkg.setText(part, scenarios[0].fill(pkg.text(part)))
  const ecName = scenarios[0].name || EC_NAME
  if (ecName !== scoreTemplate) cloner.renameScore(ecName)
  const altSheets: string[] = []
  const helpers = new Map<string, Map<string, string>>()
  const others = cloner.analysis.perScenario.filter((s) => s !== cloner.score)
  scenarios.slice(1).forEach((alt, k) => {
    const created = cloner.addAlternative(k + 2, alt.name, alt.fill, alt.id, spec.hideHelpers ?? true)
    altSheets.push(created[0])
    helpers.set(created[0], new Map(others.map((o, j) => [o, created[1 + j]] as [string, string]).filter(([, c]) => c !== undefined)))
  })
  return { pkg, wb, ecSheet: ecName, altSheets, helpers, scoreTemplate }
}

/** sheet_for: the sheet that holds scenario ``index``'s copy of ``templateSheet`` (0: the baseline). */
export function sheetFor(built: Built, index: number, templateSheet: string): string {
  const isScore = templateSheet.toLowerCase() === built.scoreTemplate.toLowerCase()
  if (index === 0) return isScore ? built.ecSheet : templateSheet
  const alt = built.altSheets[index - 1]
  if (isScore) return alt
  for (const [orig, copy] of built.helpers.get(alt)!) if (orig.toLowerCase() === templateSheet.toLowerCase()) return copy
  return templateSheet                               // a shared sheet
}

export interface AssembleArgs {
  summary: SummaryInfo
  cells: SummaryCells
  scores: ScenarioScores[]
  curves: RefCurves | null
  /** the day written on the Summary (an ISO date) */
  generatedOn: string
  /** a line across the top of the Summary and ReferenceCurves tabs (DEEP's assessment status) */
  banner?: Banner | null
}

/** assemble: the whole workbook. */
export async function assemble(template: Uint8Array, spec: AssemblySpec, scenarios: ScenarioInput[], args: AssembleArgs): Promise<Uint8Array> {
  const built = await build(template, spec, scenarios)
  const styles = new StyleBook(built.pkg)
  const first = [addSummarySheet(built, spec, styles, args.summary, args.cells, args.scores, scenarios, args.generatedOn, args.banner ?? null)]
  const after = args.curves !== null && args.curves.blocks.length ? [addCurvesSheet(built, styles, args.curves, args.banner ?? null)] : []
  styles.save()
  return finish(built, spec, first, after)
}

/** finish: the tabs ordered (the template's lead sheets, then ``first`` before the scenarios, ``after``
 * right after them), the first selected, the calculation chain dropped, and the result checked. */
export async function finish(built: Built, spec: AssemblySpec, first: string[], after: string[]): Promise<Uint8Array> {
  const { pkg, wb } = built
  const lead = (spec.lead ?? []).flatMap((n) => wb.sheets.filter((s) => s.name.toLowerCase() === n.toLowerCase() && s.state === 'visible').map((s) => s.name))
  const head = [...lead, ...first, built.ecSheet, ...built.altSheets, ...after]
  const headLow = new Set(head.map((h) => h.toLowerCase()))
  const rest = wb.sheets.filter((s) => !headLow.has(s.name.toLowerCase()))
  wb.reorder([...head, ...rest.filter((s) => s.state === 'visible').map((s) => s.name), ...rest.filter((s) => s.state !== 'visible').map((s) => s.name)])
  const active = head[0]
  for (const s of wb.sheets) {
    const xml = pkg.text(s.part)
    const next = s.name === active ? setTabSelected(xml) : clearTabSelected(xml)
    if (next !== xml) pkg.setText(s.part, next)
  }
  wb.removeCalcChain()
  dropAppTitles(pkg)
  if (spec.forceRecalc) wb.forceFullCalc()
  wb.serialize(active)
  const data = pkg.write()
  const problems = await validatePackage(data)
  if (problems.length) throw new AssemblyError(problems.slice(0, 8).join('; '))
  return data
}

/** docProps/app.xml lists the sheet names; Excel rebuilds the list, so a stale one is dropped. */
function dropAppTitles(pkg: Package): void {
  if (!pkg.has('docProps/app.xml')) return
  const xml = pkg.text('docProps/app.xml')
  const next = xml.replace(/<(HeadingPairs|TitlesOfParts)\b[\s\S]*?<\/\1>/g, '')
  if (next !== xml) pkg.setText('docProps/app.xml', next)
}
