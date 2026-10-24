// The summary block every tool shows, on the report and on the workbook's Summary tab
// (libs/staf_workbook model/summary.py). Region names come from the shared ecoregion table
// (content/shared/ecoregions.json); EASI passes its own polygon answers and only takes the names.
import ecoregions from '../app/content/shared/ecoregions.json'
import { h, type Tag } from '../app/htm'
import { pyFixedComma } from '../app/pyfmt'

export const TIERS: Record<string, string> = { EASI: 'Screening', SFARI: 'Rapid', DEEP: 'Detailed' }
export const L1_NAMES: Record<string, string> = { 1: 'Arctic Cordillera', 2: 'Tundra', 3: 'Taiga', 4: 'Hudson Plain', 5: 'Northern Forests',
  6: 'Northwestern Forested Mountains', 7: 'Marine West Coast Forest', 8: 'Eastern Temperate Forests', 9: 'Great Plains',
  10: 'North American Deserts', 11: 'Mediterranean California', 12: 'Southern Semiarid Highlands', 13: 'Temperate Sierras',
  14: 'Tropical Dry Forests', 15: 'Tropical Wet Forests' }
export const NARS9_NAMES: Record<string, string> = { CPL: 'Coastal Plains', NAP: 'Northern Appalachians', NPL: 'Northern Plains',
  SAP: 'Southern Appalachians', SPL: 'Southern Plains', TPL: 'Temperate Plains', UMW: 'Upper Midwest', WMT: 'Western Mountains', XER: 'Xeric' }
export const NOT_AVAILABLE = 'Not available'
/** TIER: the one summary-block row the app sets for every site (never an entry) */
export const TIER = 'Assessment tier'
const TABLE = ((ecoregions as any).l3 ?? {}) as Record<string, any>

export interface Regions {
  l3: string | null, l3_name: string | null, l2: string | null, l2_name: string | null, l1: string | null, l1_name: string | null,
  nars9: string | null, nars9_name: string | null
}

/** str(x).strip() of a Python value as the region code helpers read it. */
function pyText(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(v)
  return String(v)
}

/** clean_code */
export function cleanCode(code: unknown): string | null {
  if (code === null || code === undefined) return null
  let text = pyText(code).trim()
  if (text.endsWith('.0')) text = text.slice(0, -2)
  return text || null
}

/** regions_for_l3 */
export function regionsForL3(l3: unknown): Regions {
  const code = cleanCode(l3)
  const entry = TABLE[code ?? ''] ?? {}
  const l1 = entry.l1 ?? null
  const nars = entry.nars9 ?? null
  return { l3: code, l3_name: entry.name ?? null, l2: entry.l2 ?? null, l2_name: entry.l2_name ?? null, l1, l1_name: L1_NAMES[l1 ?? ''] ?? null,
    nars9: nars, nars9_name: NARS9_NAMES[nars ?? ''] ?? null }
}

function region(code: unknown, name: unknown): string {
  if (!code && !name) return NOT_AVAILABLE
  if (code && name) return `${name} (${code})`
  return String(name || code)
}

/** _num: a number (or a numeric text), not a bool, not NaN. */
export function isNum(v: unknown): boolean {
  if (v === null || v === undefined || typeof v === 'boolean') return false
  if (typeof v === 'number') return !Number.isNaN(v)
  if (typeof v !== 'string') return false
  const t = v.trim()
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$|^[+-]?(inf|infinity)$/i.test(t.replace(/_/g, ''))) return false
  return !Number.isNaN(Number(t.replace(/_/g, '').replace(/inf(inity)?/i, 'Infinity')))
}

const toFloat = (v: unknown): number => (typeof v === 'number' ? v : Number(String(v).trim().replace(/inf(inity)?$/i, 'Infinity')))

export interface SummaryInfo {
  app: string
  reachName?: string | null
  reachLengthFt?: unknown
  drainageAreaSqkm?: unknown
  streamOrder?: unknown
  regions?: Partial<Regions>
}

/** f"{x:,.0f}" that writes inf and nan as Python does. */
function comma(x: number, digits: number): string {
  if (Number.isNaN(x)) return 'nan'
  if (!Number.isFinite(x)) return x > 0 ? 'inf' : '-inf'
  return pyFixedComma(x, digits)
}

/** web.summary_block: the summary every report opens with; ``scenario`` ([name, description]) names the
 * scenario the report is for. */
export function summaryBlock(rows: [string, string][], scenario: [string, string] | null): Tag {
  const all: [string, string][] = []
  if (scenario) {
    all.push(['Scenario', scenario[0]])
    if (scenario[1]) all.push(['Description', scenario[1]])
  }
  all.push(...rows)
  return h.table(h.tbody(...all.map(([label, value]) => h.tr(h.th(label), h.td(value)))), { class: 'staf-sum' })
}

/** web.report_scenario: the scenario a report names, or null for a lone undescribed Existing Conditions. */
export function reportScenario(sset: { hasAlternatives(): boolean, current: { name: string, description: string } }): [string, string] | null {
  const cur = sset.current
  if (!sset.hasAlternatives() && !cur.description) return null
  return [cur.name, cur.description || '']
}

/** The report's "Assessment summary" section (each tool's _summary_section). */
export function summarySection(rows: [string, string][], scenario: [string, string] | null): Tag {
  return h.div(h.div('Assessment summary', { class: 'easi-section-title' }), summaryBlock(rows, scenario), { class: 'staf-report-summary' })
}

/** SummaryInfo.rows() */
export function summaryRows(info: SummaryInfo): [string, string][] {
  const r = info.regions ?? {}
  const length = isNum(info.reachLengthFt) ? `${comma(toFloat(info.reachLengthFt), 0)} ft` : NOT_AVAILABLE
  const area = isNum(info.drainageAreaSqkm) ? `${comma(toFloat(info.drainageAreaSqkm), 2)} km²` : NOT_AVAILABLE
  const order = isNum(info.streamOrder) && toFloat(info.streamOrder) > 0 ? String(Math.trunc(toFloat(info.streamOrder))) : NOT_AVAILABLE
  return [['Reach name', (info.reachName ?? '').trim() || 'Unnamed'],
    [TIER, TIERS[info.app.toUpperCase()] ?? ''],
    ['Reach length', length],
    ['Drainage area', area],
    ['Stream order (Strahler)', order],
    ['NARS-9 region', region(r.nars9, r.nars9_name)],
    ['EPA Level I ecoregion', region(r.l1, r.l1_name)],
    ['EPA Level II ecoregion', region(r.l2, r.l2_name)],
    ['EPA Level III ecoregion', region(r.l3, r.l3_name)]]
}
