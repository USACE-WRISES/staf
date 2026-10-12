/**
 * The data host's signed addresses, remembered between visits.
 *
 * Hugging Face answers a file's `/resolve/` request (3,000 a 5-minute window for each IP address) with a
 * redirect to a signed CDN address. That address works for an hour from any network: its CloudFront
 * policy holds only an expiry (checked 2026-10-12). Reads at the address aren't counted. So a page
 * reads a file through its address from the second read on, and a later visit within the hour (a
 * reload, a second tab) starts there too: it looks up only the files it opens for the first time.
 * Kept in localStorage; where storage is missing or refuses, files are looked up again as before. An
 * address the CDN refuses is forgotten and the file looked up again (`DataSource`).
 */

/** The storage key (a new format takes a new key). */
const KEY = 'staf-data-signed-1'
/** Seconds of validity an address must still have to be used. */
const MARGIN = 120
/** The most addresses kept (a session opens about 30 files). */
const MAX = 1000

/** When a signed address stops working (seconds since 1970): CloudFront's `Expires`, or S3's
 * `X-Amz-Date` with `X-Amz-Expires`; null when it says neither. */
export function expiresAt(url: string): number | null {
  let q: URLSearchParams
  try {
    q = new URL(url).searchParams
  } catch {
    return null
  }
  const exp = Number(q.get('Expires'))
  if (Number.isFinite(exp) && exp > 0) return exp
  const date = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(q.get('X-Amz-Date') ?? '')
  const life = Number(q.get('X-Amz-Expires'))
  if (date && Number.isFinite(life) && life > 0) {
    return Date.UTC(+date[1], +date[2] - 1, +date[3], +date[4], +date[5], +date[6]) / 1000 + life
  }
  return null
}

export class SignedAddresses {
  private map = new Map<string, [string, number]>()
  private saving = false

  constructor(private readonly enabled = true, private readonly now = () => Date.now() / 1000) {
    if (!enabled) return
    try {
      const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, unknown>
      for (const [path, v] of Object.entries(raw)) {
        if (Array.isArray(v) && typeof v[0] === 'string' && typeof v[1] === 'number' && v[1] - MARGIN > this.now()) {
          this.map.set(path, [v[0], v[1]])
        }
      }
    } catch {
      // no storage, or an entry from elsewhere: look files up as before
    }
  }

  /** The remembered address of a stored file, while it has more than MARGIN seconds left; else null. */
  get(path: string): string | null {
    const v = this.map.get(path)
    if (!v) return null
    if (v[1] - MARGIN > this.now()) return v[0]
    this.map.delete(path)
    return null
  }

  /** Keeps the address a stored file redirected to, when it says how long it works. */
  remember(path: string, url: string): void {
    if (!this.enabled) return
    const exp = expiresAt(url)
    if (exp === null || exp - MARGIN <= this.now()) return
    this.map.set(path, [url, exp])
    this.save()
  }

  /** Drops an address the CDN refused. */
  forget(path: string): void {
    if (this.map.delete(path)) this.save()
  }

  /** One write for a burst of changes, of the addresses still good, the latest first. */
  private save(): void {
    if (this.saving) return
    this.saving = true
    setTimeout(() => {
      this.saving = false
      try {
        const t = this.now()
        const live = [...this.map].filter(([, v]) => v[1] - MARGIN > t).sort((a, b) => b[1][1] - a[1][1]).slice(0, MAX)
        localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(live)))
      } catch {
        // storage full or refused: this visit still uses them
      }
    }, 0)
  }
}
