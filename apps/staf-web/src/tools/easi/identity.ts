// The scoring method an EASI result was scored with (easi/report.py scoring_method, scoring_method_text):
// its version and alternative, the criteria set, the method package, and, for a live result, the
// evidence-acquisition code's digest. The digest hashes the Python sources, so the static app records
// the one of the EASI its port is certified against (content/easi/acquisition.json, copy_page.py).
import acquisition from '../../app/content/easi/acquisition.json'
import { APP, method, PIN, scoringIdentity } from './method'

type Obj = Record<string, any>

/** scoring_method: ``{}`` when the method is not loaded (an export never fails over its label). */
export function scoringMethod(result: Obj | null = null): Obj {
  let ident: Obj
  let envelope: Obj
  try {
    ident = scoringIdentity()
    envelope = method().envelope ?? {}
  } catch {
    return {}
  }
  const out: Obj = {
    method_version: PIN.methodVersion,
    alternative_id: ident.alternative_id ?? null,
    alternative_name: ident.alternative_name ?? null,
    criteria_set: APP.criteriaSet,
  }
  const live = !((result ?? {}).report ?? {}).precomputed
  if (result !== null && live && acquisition.acquisitionDigest) out.acquisition_digest = acquisition.acquisitionDigest
  out.package = { method_id: envelope.methodId ?? null, version: envelope.version ?? null, label: envelope.label ?? null,
    package_digest: PIN.packageDigest }
  return out
}

/** scoring_method_text: "Alternative 2 ... (method e91118cc821c), method package easi-screening v2". */
export function scoringMethodText(m: Obj | null = null): string {
  const s = m ?? scoringMethod()
  if (!s || !Object.keys(s).length) return ''
  const name = s.alternative_name || 'EASI screening method'
  let text = `${name} (method ${s.method_version})`
  const pkg = s.package
  if (pkg && Object.keys(pkg).length) text += `, method package ${pkg.method_id} v${pkg.version}`
  return text
}
