// Timings of the app's steps, as performance measures named "staf:<step>" (parity/capture.py --perf
// reads them with the page's resource timings, to see what a click costs and where it waits).

/** ``p``, with how long it took recorded as the measure "staf:<name>". */
export function timed<T>(name: string, p: Promise<T>): Promise<T> {
  const start = performance.now()
  const done = () => {
    try {
      performance.measure(`staf:${name}`, { start, end: performance.now() })
    } catch {
      // a runtime without user timing
    }
  }
  p.then(done, done)
  return p
}

/** ``f()`` run now, recorded as the measure "staf:<name>". */
export function timedSync<T>(name: string, f: () => T): T {
  const start = performance.now()
  try {
    return f()
  } finally {
    try {
      performance.measure(`staf:${name}`, { start, end: performance.now() })
    } catch {
      // a runtime without user timing
    }
  }
}
