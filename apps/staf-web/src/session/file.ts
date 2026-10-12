// The STAF assessment file: what the header's Save writes and Open reads, one JSON structure in EASI,
// SFARI and DEEP (libs/staf_workbook assessment_file.py, owner 2026-10-05).
//
//     {
//       "format": "staf-assessment",
//       "formatVersion": 1,
//       "tool": "SFARI",
//       "savedAt": "2026-10-05T17:20:31Z",
//       "delineation": {...},
//       "toolData": {...},
//       "scenarios": {"version": 1, "active": "existing", "next": 2, "items": [...]}
//     }
//
// One line per key, each value compact and in UTF-8, as dump writes it, so a file saved here opens in
// the Shiny tools and the reverse. A tool opens only its own files; files SFARI and DEEP wrote before
// this format are lifted into it by the tool's legacy reader.
import { isMarkedFloat, parseJson, pyClone, pyDumps } from '../app/pyjson'
import { fromUtf8, utf8Error } from '../files/bytes'
import { BASELINE_ID, ScenarioSet } from '../shared/scenarios'

export const FORMAT = 'staf-assessment'
export const FORMAT_VERSION = 1
export const TOOLS = ['EASI', 'SFARI', 'DEEP'] as const
export type ToolName = typeof TOOLS[number]
/** blocks of a saved delineation the tools read as objects */
const BLOCKS = ['delineation', 'ctx_inputs', 'siteAnchor', 'siteEngine']
const NOT_OURS = 'this is not a STAF assessment file.'

/** A file this tool cannot open. The text completes "Could not load assessment: ". */
export class AssessmentFileError extends Error {}

type Obj = Record<string, any>
const isObj = (v: unknown): v is Obj => v !== null && typeof v === 'object' && !Array.isArray(v)

/** _now: the UTC time to the second, ``2026-10-05T17:20:31Z``. */
export function nowStamp(d = new Date()): string {
  return d.toISOString().replace(/\.\d+Z$/, 'Z')
}

/** dump: the file ``tool`` saves (``scenarios``: scenariosBlock's). */
export function dump(tool: ToolName, delineation: unknown, toolData: unknown, scenarios: unknown, savedAt?: string): string {
  if (!TOOLS.includes(tool)) throw new Error(`unknown tool '${tool}'`)
  const truthy = (v: unknown) => v !== null && v !== undefined && !(isObj(v) && !Object.keys(v).length)
  const out: [string, unknown][] = [
    ['format', FORMAT], ['formatVersion', FORMAT_VERSION], ['tool', tool], ['savedAt', savedAt || nowStamp()],
    ['delineation', truthy(delineation) ? delineation : {}],
    ['toolData', truthy(toolData) ? toolData : {}],
    ['scenarios', truthy(scenarios) ? scenarios : new ScenarioSet().toJson(true)],
  ]
  const lines = out.map(([key, value]) => `  ${pyDumps(key)}: ${pyDumps(value, { ensureAscii: false, separators: [',', ':'] })}`)
  return '{\n' + lines.join(',\n') + '\n}\n'
}

/** The tool that wrote a file from before this format: its ``method``, else what its keys show. */
export function legacyTool(raw: Obj): ToolName | null {
  const method = raw.method
  if (typeof method === 'string' && TOOLS.includes(method.trim().toUpperCase() as ToolName)) return method.trim().toUpperCase() as ToolName
  if ('measured_values' in raw || 'assessment' in raw) return 'DEEP'
  if ('metric_scores' in raw || 'function_scores' in raw) return 'SFARI'
  return null
}

function sameTool(saved: unknown, tool: ToolName): void {
  if (saved !== null && saved !== undefined && saved !== tool) {
    const article = saved === 'EASI' ? 'an' : 'a'
    throw new AssessmentFileError(`this is ${article} ${saved} assessment. Open it in ${saved}.`)
  }
}

export interface Parsed { delineation: Obj, toolData: Obj, scenarios: Obj | null }

function checked(out: Obj): Parsed {
  let d = out.delineation
  d = d === null || d === undefined ? {} : d
  if (!isObj(d)) throw new AssessmentFileError('the saved delineation must be an object.')
  for (const key of BLOCKS) {
    if (d[key] !== null && d[key] !== undefined && !isObj(d[key])) throw new AssessmentFileError(`the saved ${key} must be an object.`)
  }
  let data = out.toolData
  data = data === null || data === undefined ? {} : data
  if (!isObj(data)) throw new AssessmentFileError('the saved tool data must be an object.')
  const scenarios = out.scenarios
  if (scenarios !== null && scenarios !== undefined && !isObj(scenarios)) throw new AssessmentFileError('the saved scenarios must be an object.')
  return { delineation: d, toolData: data, scenarios: scenarios ?? null }
}

/** parse: ``{delineation, toolData, scenarios}`` of a file ``tool`` saved, checked; ``legacy`` lifts a
 * file from before this format into that shape. Throws AssessmentFileError. */
export function parse(text: string, tool: ToolName, legacy?: (raw: Obj) => Obj): Parsed {
  let raw: unknown
  try {
    raw = parseJson(text)
  } catch {
    throw new AssessmentFileError(NOT_OURS)
  }
  if (!isObj(raw)) throw new AssessmentFileError(NOT_OURS)
  let out: Obj
  if ('format' in raw) {
    if (raw.format !== FORMAT || !TOOLS.includes(raw.tool)) throw new AssessmentFileError(NOT_OURS)
    const version = raw.formatVersion
    // an int, not a float (1.0) or a bool
    if (typeof version !== 'number' || !Number.isInteger(version) || isMarkedFloat(raw, 'formatVersion') || version < 1) {
      throw new AssessmentFileError('the file has no valid format version.')
    }
    if (version > FORMAT_VERSION) throw new AssessmentFileError('it was saved by a newer version of STAF. Update this app to open it.')
    sameTool(raw.tool, tool)
    out = { delineation: raw.delineation, toolData: raw.toolData, scenarios: raw.scenarios }
  } else {
    sameTool(legacyTool(raw), tool)
    if (!legacy) throw new AssessmentFileError(NOT_OURS)
    out = legacy(raw)
  }
  return checked(out)
}

/** scenarios_block: every scenario with its state, the one on screen with ``liveState``. */
export function scenariosBlock(sset: ScenarioSet, liveState: unknown): Obj {
  const data = sset.toJson(true)
  for (const item of data.items) if (item.id === sset.active) item.state = liveState
  return pyClone(data)
}

/** scenario_set: the scenarios a file carries (Existing Conditions alone without a block). */
export function scenarioSet(block: unknown): ScenarioSet {
  const b = pyClone(block) as Obj | null
  let base: unknown = null
  if (isObj(b) && Array.isArray(b.items)) {
    const item = b.items.find((x: unknown) => isObj(x) && x.id === BASELINE_ID)
    base = item ? item.state : null
  }
  return ScenarioSet.fromJson(b, base ?? null)
}

/** legacy_scenarios: a pre-format file's scenarios as this format's block. */
export function legacyScenarios(block: unknown, baselineState: unknown): Obj {
  return ScenarioSet.fromJson(pyClone(block), baselineState).toJson(true)
}

/** The file's text as Python's open(..., encoding="utf-8").read() gives it (universal newlines: CRLF
 * and CR read as LF); bytes that are not UTF-8 throw Python's UnicodeDecodeError text. */
export function readText(bytes: Uint8Array): string {
  const err = utf8Error(bytes)
  if (err) throw new Error(err)
  return fromUtf8(bytes).replace(/\r\n?/g, '\n')
}
