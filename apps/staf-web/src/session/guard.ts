// The leave-page guard's state (staf_workbook unsaved-guard.js, web.state_fingerprint): each tool tells
// the page whether its work differs from the last Save or Open ({dirty, ns} on "staf-unsaved"), and the
// page warns before it is left while any tool is dirty. A tool's work is a fingerprint of the parts its
// app.py _work_fp names (null: nothing to lose); it is taken again shortly after anything changes.
import { pyDumps } from '../app/pyjson'
import * as shim from '../app/shim'
import { sha256Hex } from '../files/bytes'

/** state_fingerprint: a digest of JSON-able parts (keys sorted, compact, UTF-8). */
export function stateFingerprint(...parts: unknown[]): string {
  return sha256Hex(pyDumps(parts, { sortKeys: true, separators: [',', ':'], ensureAscii: false }))
}

export class UnsavedGuard {
  private saved: string | null = null
  private sent: boolean | null = null
  private timer: ReturnType<typeof setTimeout> | null = null

  /** ``work``: the tool's fingerprint now, or null when there is nothing to lose. */
  constructor(private ns: string, private work: () => string | null) {}

  /** Save or Open: the work as it is now is the saved work. */
  markSaved(): void {
    this.saved = this.work()
    this.check()
  }

  /** Something changed: the state is checked once things settle. */
  soon(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(() => this.check(), 150)
  }

  check(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    const fp = this.work()
    const dirty = fp !== null && fp !== this.saved
    if (dirty === this.sent) return
    this.sent = dirty
    shim.send('staf-unsaved', { dirty, ns: this.ns })
  }
}
