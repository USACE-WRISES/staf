// A stand-in for the two Shiny calls the shared page scripts make.
//
// The Shiny apps' page scripts (libs/staf_workbook/assets/*.js, apps/deep/www/coverage.js) run
// unchanged in the static app (site.ts SCRIPTS). They post to the server with
// Shiny.setInputValue(id, value) and hear it through handlers they register with
// Shiny.addCustomMessageHandler(type, fn). Here both stay in the page: an input goes to the tool
// that owns the id ("deep-coverage_ready" -> DEEP, "coverage_ready"), and the tool answers through
// send(). No Shiny, no server, no Python: just these two calls and the session events the scripts
// wait for (shiny:connected, shiny:sessioninitialized).

type InputListener = (id: string, value: unknown) => void
type MessageHandler = (message: unknown) => void

const handlers = new Map<string, MessageHandler>()
const queued: [string, unknown][] = []
let listener: InputListener | null = null

interface ShinyStandIn {
  setInputValue(id: string, value: unknown, opts?: unknown): void
  addCustomMessageHandler(type: string, fn: MessageHandler): void
  bindAll(): void
  unbindAll(): void
}

export function install(): void {
  const w = window as unknown as { Shiny?: ShinyStandIn }
  if (w.Shiny) return
  w.Shiny = {
    setInputValue(id, value) {
      if (listener) listener(id, value)
      else queued.push([id, value])
    },
    addCustomMessageHandler(type, fn) {
      handlers.set(type, fn)
    },
    bindAll() {},
    unbindAll() {},
  }
}

/** Hears every input the page scripts post (those posted before this call too). */
export function onInput(fn: InputListener): void {
  listener = fn
  for (const [id, value] of queued.splice(0)) fn(id, value)
}

/** A custom message to the page scripts; false when none listens for its type. */
export function send(type: string, message: unknown): boolean {
  const fn = handlers.get(type)
  if (fn) fn(message)
  return !!fn
}

/** The session events the page scripts wait for before posting (after every script has run). */
export function connect(): void {
  document.dispatchEvent(new Event('shiny:connected'))
  document.dispatchEvent(new Event('shiny:sessioninitialized'))
}
