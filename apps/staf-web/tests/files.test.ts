// The pieces every download is made of, against Python's own (files_ref.json, make_file_fixtures.py):
// the digests, json.dumps of what json.loads read, csv.writer rows, UnicodeDecodeError texts, the
// py-shiny controls the dialogs use, and staf_workbook's shared downloads, dialogs and summary rows.
import { describe, expect, it } from 'vitest'
import { TagList } from '../src/app/htm'
import { JsonError, parseJson, pyDumps } from '../src/app/pyjson'
import { base64, md5Hex, NAMESPACE_DNS, NAMESPACE_URL, sha1Hex, sha256Hex, utf8, utf8Error, uuid5 } from '../src/files/bytes'
import { csvRow, PyNum } from '../src/files/csv'
import { formsDownloads, reportFooter, scenarioSuffix } from '../src/shared/downloads'
import { newDialog } from '../src/session/dialogs'
import { ScenarioSet } from '../src/shared/scenarios'
import { column, inputNumeric, inputSelect, navsetPill, row } from '../src/shared/shinyui'
import { regionsForL3, summaryRows } from '../src/shared/summary'
import ref from './fixtures/files_ref.json'

const R = ref as any
const render = (t: any) => new TagList(t).render().replace(/(data-tabsetid="|#tab-|id="tab-)\d+/g, '$1TABSET')

describe('bytes', () => {
  it('digest as hashlib, uuid5 as uuid, base64 as base64', () => {
    for (const d of R.digests) {
      const data = utf8(d.text)
      expect(sha256Hex(data), d.text).toBe(d.sha256)
      expect(sha1Hex(data), d.text).toBe(d.sha1)
      expect(md5Hex(data), d.text).toBe(d.md5)
      expect(base64(data), d.text).toBe(d.b64)
      expect(uuid5(NAMESPACE_URL, d.text), d.text).toBe(d.uuid5_url)
      expect(uuid5(NAMESPACE_DNS, d.text), d.text).toBe(d.uuid5_dns)
    }
  })

  it('name bytes that are not UTF-8 as Python does', () => {
    for (const u of R.utf8) expect(utf8Error(new Uint8Array(u.bytes)), JSON.stringify(u.bytes)).toBe(u.message)
    expect(utf8Error(utf8('ok ✓ km² 😀'))).toBe(null)
  })
})

describe('json', () => {
  it('writes what json.loads read as json.dumps writes it', () => {
    for (const j of R.json) {
      const value = parseJson(j.source)
      for (const d of j.dumps) {
        const o = d.options
        expect(pyDumps(value, { indent: o.indent ?? null, separators: o.separators, ensureAscii: o.ensure_ascii, sortKeys: o.sort_keys }),
          `${j.source} ${JSON.stringify(o)}`).toBe(d.text)
      }
    }
  })

  it('refuses what json.loads refuses', () => {
    for (const t of R.jsonRefused) expect(() => parseJson(t), JSON.stringify(t)).toThrow(JsonError)
  })
})

describe('csv', () => {
  it('writes rows as csv.writer', () => {
    for (const c of R.csv) {
      const cells = c.row.map((v: any, i: number) => (c.kinds[i] === 'float' ? new PyNum(Number(v === 'nan' ? NaN : v === 'inf' ? Infinity : v)) : v))
      expect(csvRow(cells), JSON.stringify(c.row)).toBe(c.text)
    }
  })
})

describe('controls', () => {
  it('draw py-shiny controls as py-shiny', () => {
    for (const n of R.controls.numeric) {
      const [id, label, value, opts] = n.args
      expect(render(inputNumeric(id, label, value, opts)), id).toBe(n.html)
    }
    expect(render(row(column(4, '<div>a</div>' as any)))).not.toBe('')
    expect(render(inputSelect('sfari-assess_pick_0', null, [['a', 'A'], ['b', 'B & C']], 'b'))).toBe(R.controls.selectNull)
  })

  it('draw the shared downloads, footer and New dialog as staf_workbook', () => {
    const pills = navsetPill('sfari-ff_tabs', [{ title: 'Field forms', value: 'ff', content: '\n' as any }, { title: 'Desktop', content: 'y' }],
      formsDownloads(['sfari-dl_metrics', 'Desktop metrics PDF'], { fieldForms: 'sfari-dl_forms', workbooks: ['sfari-dl_wb', 'sfari-dl_blank'] }))
    // the panes' content is the tool's; the strip and the links are the shared part
    const strip = (html: string) => html.slice(0, html.indexOf('<div class="tab-content"'))
    const unbound = (html: string) => html.replace(/ shiny-download-link disabled/g, ' shiny-download-link')
      .replace(/ aria-disabled="true" tabindex="-1"/g, '').replace(/(<a class="btn btn-default shiny-download-link[^>]*?)>/g, (m, a) => `${a}>`)
    const want = unbound(strip(R.controls.pills))
    const got = strip(render(pills)).replace(/ aria-live="polite"/g, '')
    expect(got).toBe(want)
    const footer = render(reportFooter('sfari-dl_pdf', 'sfari-dl_csv', 'sfari-dl_geojson', 'sfari-dl_workbook')).replace(/ aria-live="polite"/g, '')
    expect(footer).toBe(unbound(R.controls.footer))
    expect(render(reportFooter('sfari-dl_pdf', 'sfari-dl_csv', 'sfari-dl_geojson')).replace(/ aria-live="polite"/g, ''))
      .toBe(unbound(R.controls.footerNoWorkbook))
    expect(render(newDialog('sfari', 'the delineation and every score, note and photo'))).toBe(R.controls.newDialog)
  })

  it('name report files and summarize sites as staf_workbook', () => {
    for (const s of R.scenarioSuffix) {
      const set = new ScenarioSet()
      if (s.name) set.add(s.name, '', { state: {} })
      expect(scenarioSuffix(set), String(s.name)).toBe(s.suffix)
    }
    for (const r of R.regions) expect(regionsForL3(r.l3), String(r.l3)).toEqual(r.regions)
    for (const s of R.summaries) {
      const i = s.info
      expect(summaryRows({ app: s.app, reachName: i.reachName, reachLengthFt: i.reachLengthFt, drainageAreaSqkm: i.nan ? NaN : i.drainageAreaSqkm,
        streamOrder: i.streamOrder, regions: i.regions }), s.app).toEqual(s.rows)
    }
  })
})
