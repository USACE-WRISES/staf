/**
 * Phase 1 proof: what a click costs when the app reads STAF's data by HTTP range, and whether a
 * section pulled live from USGS 3DEP reproduces the archive. Results go to the page and to
 * `window.__proof` (read by tests driving the page).
 */
import { DataSource } from '../data/source'
import { gridded, linesInBox, rowGroupLines, type Flowline } from '../data/lines'
import { medianProfile, sectionDetails, sectionNumbers, sectionPulls, type XsPull } from '../xs/grid'
import { placeSections } from '../xs/place'
import { pullExact, pullOnGrids, pullSection, type PullSection, type SectionSource } from '../xs/sample'
import type { XsSection } from '../xs/grid'

interface Step {
  step: string
  result: string
  requests: number
  host: number // requests to the data host itself (Hugging Face rate-limits /resolve/)
  bytes: number
  ms: number
}

const params = new URLSearchParams(location.search)
const base = params.get('data') ?? `${location.origin}/data/`
const vpu = params.get('vpu') ?? '0710'
const rgIndex = Number(params.get('rg') ?? 3)
const src = new DataSource(base)
const steps: Step[] = []
const out: Record<string, unknown> = { base, vpu, steps }
;(window as any).__proof = out

async function measure<T>(name: string, fn: () => Promise<T>, describe: (v: T) => string): Promise<T> {
  const r0 = src.requests
  const h0 = src.hostRequests
  const b0 = src.bytes
  const t0 = performance.now()
  const value = await fn()
  const step = { step: name, result: describe(value), requests: src.requests - r0, host: src.hostRequests - h0, bytes: src.bytes - b0,
                 ms: Math.round(performance.now() - t0) }
  steps.push(step)
  const row = document.createElement('tr')
  row.innerHTML = `<td>${step.step}</td><td>${step.result}</td><td class="num">${step.requests}</td>` +
    `<td class="num">${step.host}</td>` +
    `<td class="num">${step.bytes.toLocaleString()}</td><td class="num">${step.ms}</td>`
  document.querySelector('#steps tbody')!.append(row)
  return value
}

/** Exact counts of the pulls (format 3: stored positions, checksum search). */
const exact = { pulls: 0, verified: 0, adjusted: 0, open: 0 }
out.exact = exact

/** A pull as the release allows: format 3 (stored position, checksum) resolves to the archive's samples
 * bit for bit; format 2 pulls on the recorded grids from the JS placement; older ones over the
 * section's own window. */
async function pullAs(sec: PullSection, src: SectionSource, d: XsSection, stored?: XsPull): Promise<Float32Array> {
  out.grids = d.reads.length > 0
  if (stored && d.reads.length) {
    const r = await pullExact({ ...sec, x: stored.x, y: stored.y, nx: stored.nx, ny: stored.ny }, src, d.reads, stored.zCrc)
    exact.pulls += 1
    exact.verified += r.verified ? 1 : 0
    exact.adjusted += r.adjusted
    exact.open += r.open
    return r.z
  }
  return d.reads.length ? pullOnGrids(sec, src, d.reads) : pullSection(sec, src)
}

async function pickFlowline(): Promise<Flowline> {
  const lines = (await rowGroupLines(src, vpu, rgIndex)).filter((l) => gridded(l.fcode) && l.x.length > 3)
  for (const line of lines.slice(0, 40)) {
    const m = await medianProfile(src, vpu, line.nhdplusid)
    if (m && m.res === 1) return line
  }
  throw new Error('no flowline with a 1 m median profile in that row group')
}

async function run(): Promise<void> {
  await measure('manifest', () => src.load(), (m) => `${Object.keys(m.files).length} files, regions ${m.regions.join(', ')}`)
  const line = await measure('pick a flowline (one row group of lines + medians)', pickFlowline,
    (l) => `${l.nhdplusid} ${l.gnisName ?? ''} (fcode ${l.fcode}, ${l.x.length} vertices)`)
  const lon = line.x[0] / 1e5
  const lat = line.y[0] / 1e5
  out.flowline = { nhdplusid: String(line.nhdplusid), lon, lat }
  await measure('streams in a zoom-14 view (0.12°)', () => linesInBox(src, vpu, [lon - 0.06, lat - 0.06, lon + 0.06, lat + 0.06]),
    (ls) => `${ls.length} flowlines`)
  const numbers = await measure('cross-section numbers of the flowline', () => sectionNumbers(src, vpu, line.nhdplusid),
    (ns) => `${ns.length} sections: ${ns.filter((n) => n.status === 'ok').length} scored, ER ${ns.map((n) => n.er ?? '–').join(' ')}`)
  const details = await measure('cross-section details of the flowline', () => sectionDetails(src, vpu, line.nhdplusid),
    (ds) => `${ds.length} sections, half-width ${ds[0]?.wide} m, ${ds[0]?.nPts} samples`)
  const stored = await measure('stored positions and checksums (format 3)', () => sectionPulls(src, vpu, line.nhdplusid),
    (ps) => (ps ? `${ps.length} sections` : 'none (an older release)'))
  const median = (await measure('median profile (stored)', () => medianProfile(src, vpu, line.nhdplusid),
    (m) => (m ? `section k=${m.k}, ${m.nPts} samples, thalweg ${m.thalweg?.toFixed(3)} m` : 'none')))!
  const placed = await measure('placement (sections.place in JS)', async () => placeSections(line),
    (ps) => `${ps.length} sections placed (grid has ${numbers.length})`)
  const sec = placed.find((p) => p.part === median.part && p.k === median.k)!
  const detail = details.find((d) => d.part === median.part && d.k === median.k)!
  const pulled = await measure('pull the median section from USGS 3DEP', () =>
    pullAs({ x: sec.x, y: sec.y, nx: sec.nx, ny: sec.ny, wide: detail.wide, nPts: detail.nPts },
           { res: numbers.find((n) => n.k === median.k)!.res, source: detail.source, tiles: detail.tiles }, detail,
           stored?.find((p) => p.part === median.part && p.k === median.k)),
  (z) => `${z.length} samples`)
  const a = new Uint32Array(pulled.buffer)
  const b = new Uint32Array(median.z.buffer)
  let differ = 0
  let worst = 0
  let nanMismatch = 0
  for (let i = 0; i < a.length; i++) {
    if (a[i] === b[i]) continue
    differ += 1
    if (Number.isNaN(pulled[i]) !== Number.isNaN(median.z[i])) nanMismatch += 1
    else worst = Math.max(worst, Math.abs(pulled[i] - median.z[i]))
  }
  out.compare = { samples: a.length, identical: a.length - differ, differ, nanMismatch, largestDifferenceM: worst }
  const other = placed.find((p) => p.k !== median.k && numbers.find((n) => n.k === p.k)?.status === 'ok')
  if (other) {
    const d = details.find((x) => x.k === other.k)!
    await measure(`pull another section (k=${other.k}, tiles cached)`, () =>
      pullAs({ x: other.x, y: other.y, nx: other.nx, ny: other.ny, wide: d.wide, nPts: d.nPts },
             { res: numbers.find((n) => n.k === other.k)!.res, source: d.source, tiles: d.tiles }, d,
             stored?.find((p) => p.part === other.part && p.k === other.k)),
    (z) => `${z.length} samples`)
  }
  const pulls = Number(params.get('pulls') ?? 0)
  if (pulls > 0) {
    const many = await measure(`pull ${pulls} more median sections and compare`, () => pullMany(line, pulls),
      (r) => `${r.identical} of ${r.sections} bit-identical; ${r.samplesDiffering} samples differ in all, largest ${r.largest.toExponential(2)} m`)
    out.many = many
  }
  const verdict = differ === 0 ? 'bit-identical to the archive'
    : `${differ} of ${a.length} samples differ (largest ${worst.toExponential(2)} m${nanMismatch ? `, ${nanMismatch} missing-value mismatches` : ''})`
  document.querySelector('#status')!.innerHTML =
    `Done. Live pull vs archive: <span class="${differ === 0 || (worst <= 3.1e-5 && !nanMismatch) ? 'ok' : 'bad'}">${verdict}</span>`
  out.done = true
  document.querySelector('#json')!.textContent = JSON.stringify(out, (_k, v) => (typeof v === 'bigint' ? String(v) : v), 1)
}

/** Pull the stored median section of up to `n` other flowlines (row groups `rg`, `rg`+1, ...) and
 * compare each with the archive's samples. */
async function pullMany(skip: Flowline, n: number) {
  const res = { sections: 0, identical: 0, samplesDiffering: 0, largest: 0, nanMismatch: 0, tiers: {} as Record<number, number> }
  for (let g = rgIndex; res.sections < n && g < rgIndex + 6; g++) {
    const lines = (await rowGroupLines(src, vpu, g)).filter((l) => gridded(l.fcode) && l.nhdplusid !== skip.nhdplusid)
    for (const line of lines) {
      if (res.sections >= n) break
      const m = await medianProfile(src, vpu, line.nhdplusid)
      if (!m || !m.res) continue
      const sec = placeSections(line).find((p) => p.part === m.part && p.k === m.k)
      const d = (await sectionDetails(src, vpu, line.nhdplusid)).find((x) => x.part === m.part && x.k === m.k)
      if (!sec || !d) continue
      const stored = (await sectionPulls(src, vpu, line.nhdplusid))?.find((p) => p.part === m.part && p.k === m.k)
      let z: Float32Array
      try {
        z = await pullAs({ x: sec.x, y: sec.y, nx: sec.nx, ny: sec.ny, wide: d.wide, nPts: d.nPts },
                         { res: m.res, source: d.source, tiles: d.tiles }, d, stored)
      } catch (err) {
        ;(res as any).errors = [...((res as any).errors ?? []), `${line.nhdplusid} k=${m.k}: ${String(err)}`]
        continue
      }
      const a = new Uint32Array(z.buffer)
      const b = new Uint32Array(m.z.buffer)
      let differ = 0
      for (let i = 0; i < a.length; i++) {
        if (a[i] === b[i]) continue
        differ += 1
        if (Number.isNaN(z[i]) !== Number.isNaN(m.z[i])) res.nanMismatch += 1
        else res.largest = Math.max(res.largest, Math.abs(z[i] - m.z[i]))
      }
      res.sections += 1
      res.tiers[m.res] = (res.tiers[m.res] ?? 0) + 1
      res.samplesDiffering += differ
      if (differ === 0) res.identical += 1
    }
  }
  return res
}

run().catch((err) => {
  out.error = String(err?.stack ?? err)
  document.querySelector('#status')!.innerHTML = `<span class="bad">Failed: ${String(err)}</span>`
  document.querySelector('#json')!.textContent = String(err?.stack ?? err)
})
