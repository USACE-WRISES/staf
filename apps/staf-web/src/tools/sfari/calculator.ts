// SFARI's Excel calculator (sfari/calculator.py): the 2026-10-15 template (page/forms/sfari, the app's
// copy) with an assessment typed into the cells an assessor would type into, and every other part copied
// through untouched:
//   B2 reach id, B3 lat/long, B4 date, G2 reach length (ft)
//   H8, H12 ... H84: the 20 function scores, 0-15 (each a 4-row merge, so only the anchor row)
//   J8 .. J87: the 80 metric Likert values
// The template caches the empty calculator's error values, so fullCalcOnLoad makes Excel recalculate on open.
import { pageFile } from '../../files/download'
import { excelSerial, forceRecalc, sheetPart, xmlText } from '../../files/xlsx/calcfill'
import { readZip, repack } from '../../files/zip'
import { BLANK_FILENAME, coordsStr, reachIdStr, TEMPLATE_URL } from './forms'
import type { FunctionRecord, MetricRecord } from './markup'
import { LIKERT_NA, roundHalfEven } from './method'

export const SHEET_NAME = 'SFARI Score'
/** the workbook's Likert list ($R$1:$R$6): only the not-applicable label differs from the app's */
const NOT_APPLICABLE_LABEL = 'NA'
const WHO = 'SFARI calculator template'

// The cell map: explicit and committed (sfari/calculator.py FUNCTION_ROWS, METRIC_ROWS), never derived
// from list order or label matching.
export const FUNCTION_ROWS: [string, number][] = [
  ['catchment-hydrology', 8], ['surface-water-storage', 12], ['reach-inflow', 16], ['streamflow-regime', 20],
  ['low-flow-baseflow-dynamics', 24], ['high-flow-dynamics', 28], ['floodplain-connectivity', 32],
  ['hyporheic-connectivity', 36], ['channel-evolution', 40], ['channel-floodplain-dynamics', 44], ['sediment-continuity', 48],
  ['bed-composition-bedform-dynamics', 52], ['light-thermal-regime', 56], ['carbon-processing', 60], ['nutrient-cycling', 64],
  ['water-soil-quality', 68], ['habitat-provision', 72], ['population-support', 76], ['community-dynamics', 80],
  ['watershed-connectivity', 84],
]
export const METRIC_ROWS: [string, number][] = [
  ['catchment-hydrology-impervious-surface-area', 8], ['catchment-hydrology-road-density', 9],
  ['catchment-hydrology-land-use-change', 10], ['catchment-hydrology-impoundments', 11],
  ['surface-water-storage-wetland-coverage', 12], ['surface-water-storage-floodplain-water-retention', 13],
  ['surface-water-storage-in-channel-ponding-beaver', 14], ['surface-water-storage-off-channel-storage', 15],
  ['reach-inflow-concentrated-flow-inputs', 16], ['reach-inflow-tributary-condition-and-impact', 17],
  ['reach-inflow-local-runoff-diversions', 18], ['reach-inflow-road-highway-drainage', 19],
  ['streamflow-regime-flow-permanence', 20], ['streamflow-regime-flow-permanence-statistics', 21],
  ['streamflow-regime-channel-natural-flow-regime', 22], ['streamflow-regime-artificial-structures-and-inputs', 23],
  ['low-flow-baseflow-dynamics-low-flow-velocity', 24], ['low-flow-baseflow-dynamics-low-flow-depth', 25],
  ['low-flow-baseflow-dynamics-longitudinal-connectivity', 26], ['low-flow-baseflow-dynamics-lateral-connectivity', 27],
  ['high-flow-dynamics-overbank-flow-frequency', 28], ['high-flow-dynamics-peak-flow-capacity-morphological-check', 29],
  ['high-flow-dynamics-peak-flow-capacity-velocity-shear-stress', 30], ['high-flow-dynamics-bed-mobilization-frequency', 31],
  ['floodplain-connectivity-floodplain-complexity', 32], ['floodplain-connectivity-entrenchment-er', 33],
  ['floodplain-connectivity-channel-condition', 34], ['floodplain-connectivity-lateral-floodplain-inundation', 35],
  ['hyporheic-connectivity-channel-complexity-for-exchange', 36], ['hyporheic-connectivity-floodplain-permeability', 37],
  ['hyporheic-connectivity-bed-surface-grain-size', 38], ['hyporheic-connectivity-visible-hyporheic-indicators', 39],
  ['channel-evolution-channel-evolution-stage', 40], ['channel-evolution-incision-trend-headcuts', 41],
  ['channel-evolution-widening-trend', 42], ['channel-evolution-recovery-indicators', 43],
  ['channel-floodplain-dynamics-bank-erosion-potential', 44], ['channel-floodplain-dynamics-bank-migration-and-meander', 45],
  ['channel-floodplain-dynamics-sinuosity', 46], ['channel-floodplain-dynamics-channel-pattern', 47],
  ['sediment-continuity-sediment-deposition-patterns', 48], ['sediment-continuity-channel-degradation-incision', 49],
  ['sediment-continuity-fine-sediment-balance', 50], ['sediment-continuity-transport-capacity', 51],
  ['bed-composition-bedform-dynamics-large-wood-frequency-and-diversity', 52],
  ['bed-composition-bedform-dynamics-riparian-wood-recruitment', 53],
  ['bed-composition-bedform-dynamics-substrate-composition', 54], ['bed-composition-bedform-dynamics-bedform-diversity', 55],
  ['light-thermal-regime-riparian-canopy-cover', 56], ['light-thermal-regime-stream-temperature-rapid', 57],
  ['light-thermal-regime-algal-growth-light-limited', 58], ['light-thermal-regime-thermal-refugia', 59],
  ['carbon-processing-cpom-retention', 60], ['carbon-processing-detritus-decomposition-rate-shredder-detritivore-presence', 61],
  ['carbon-processing-riparian-corridor-width-and-quality', 62], ['carbon-processing-algal-and-primary-production', 63],
  ['nutrient-cycling-visible-algal-indications-n-p', 64], ['nutrient-cycling-n-p-concentrations', 65],
  ['nutrient-cycling-vegetated-riparian-corridor-width', 66], ['nutrient-cycling-relative-denitrification-potential', 67],
  ['water-soil-quality-water-clarity-turbidity', 68], ['water-soil-quality-dissolved-oxygen-rapid', 69],
  ['water-soil-quality-pollutants', 70], ['water-soil-quality-ph-and-specific-conductivity-rapid', 71],
  ['habitat-provision-in-stream-habitat-complexity', 72], ['habitat-provision-overhanging-vegetation', 73],
  ['habitat-provision-aquatic-invertebrate-habitat', 74], ['habitat-provision-lateral-and-off-channel-habitats', 75],
  ['population-support-fish-habitat', 76], ['population-support-aquatic-invertebrate-community', 77],
  ['population-support-fish-presence', 78], ['population-support-amphibian-and-crayfish-presence', 79],
  ['community-dynamics-native-and-non-native-species', 80],
  ['community-dynamics-species-richness-composition-and-abundance', 81], ['community-dynamics-riparian-communities', 82],
  ['community-dynamics-keystone-species', 83], ['watershed-connectivity-upstream-and-downstream-barriers', 84],
  ['watershed-connectivity-culvert-and-road-crossing-passability', 85],
  ['watershed-connectivity-lateral-connectivity-for-riparian-fauna', 86],
  ['watershed-connectivity-dewatered-or-intermittent-segments', 87],
]

export const FUNCTION_ROW = new Map(FUNCTION_ROWS)

/** template_bytes: the shipped template, byte for byte. */
export const templateBytes = (): Promise<Uint8Array> => pageFile(TEMPLATE_URL)

export type CellValue = string | number

/** _fill_cells: each empty cell named in ``values`` replaced with the same cell carrying it; a template
 * whose cells moved fails here rather than coming back silently blank. */
export function fillCells(sheetXml: string, values: Map<string, CellValue>): string {
  const seen = new Set<string>()
  const out = sheetXml.replace(/<c r="([A-Z]+[0-9]+)"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*\/>/g, (all: string, ref: string, attrs: string) => {
    if (!values.has(ref)) return all
    seen.add(ref)
    const value = values.get(ref)!
    if (typeof value === 'string') return `<c r="${ref}"${attrs} t="inlineStr"><is><t xml:space="preserve">${xmlText(value)}</t></is></c>`
    return `<c r="${ref}"${attrs}><v>${value}</v></c>`
  })
  const missing = [...values.keys()].filter((r) => !seen.has(r)).sort()
  if (missing.length) throw new Error('SFARI calculator template does not carry the expected empty cells: ' + missing.join(', '))
  return out
}

/** fill_sheet: the score sheet with ``values`` written into its empty input cells. */
export const fillSheet = (sheetXml: string, values: Map<string, CellValue>): string => fillCells(sheetXml, values)

/** input_values: the input cells of the score sheet for one assessment ({cell: value}). */
export function inputValues(delin: Record<string, any> | null, metricScores: Record<string, MetricRecord>, functionScores: Record<string, FunctionRecord>,
  today: string): Map<string, CellValue> {
  const dl = (delin ?? {}).delineation ?? {}
  const values = new Map<string, CellValue>()
  const reachId = reachIdStr(dl)
  if (reachId) values.set('B2', reachId)
  const coords = coordsStr(dl)
  if (coords) values.set('B3', coords)
  values.set('B4', excelSerial(today))
  const length = dl.reach_length_ft
  if (length !== null && length !== undefined) values.set('G2', roundHalfEven(Number(length)))
  for (const [fid, row] of FUNCTION_ROWS) {
    const score = ((functionScores ?? {})[fid] ?? {}).score
    if (score !== null && score !== undefined) values.set(`H${row}`, Math.trunc(Number(score)))
  }
  for (const [mid, row] of METRIC_ROWS) {
    const likert = ((metricScores ?? {})[mid] ?? {}).likert
    if (!likert) continue
    values.set(`J${row}`, likert === LIKERT_NA ? NOT_APPLICABLE_LABEL : String(likert))
  }
  return values
}

/** build_calculator: the single calculator with this assessment in its input cells (the fallback
 * when the scenario workbook cannot be assembled). */
export async function buildCalculator(delin: Record<string, any> | null, metricScores: Record<string, MetricRecord>,
  functionScores: Record<string, FunctionRecord>, today: string): Promise<Uint8Array> {
  const values = inputValues(delin, metricScores, functionScores, today)
  const source = await templateBytes()
  const zin = readZip(source)
  const part = await sheetPart(zin, SHEET_NAME, WHO)
  const sheetXml = new TextDecoder().decode(await zin.read(part))
  const bookXml = new TextDecoder().decode(await zin.read('xl/workbook.xml'))
  return repack(source, new Map([
    [part, new TextEncoder().encode(fillCells(sheetXml, values))],
    ['xl/workbook.xml', new TextEncoder().encode(forceRecalc(bookXml, WHO))],
  ]))
}

/** calculator_filename: the completed workbook's download name, the calculator's version. */
export const calculatorFilename = (): string => BLANK_FILENAME
