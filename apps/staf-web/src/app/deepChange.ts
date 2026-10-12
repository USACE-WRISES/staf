// DEEP's Change dialog (app.py _change_assessment and _assess_cards_ui): one card per assessment whose
// region holds the site (at most 16), each with its default version's status badge and reference tier,
// a version chooser starting at the default, and its Select button ("✓ Selected" on the one in use).
import { h, type Tag } from './htm'
import type { Covering } from './deepAssess'
import { actionButton, inputSelect, modal, modalButton } from '../shared/shinyui'

export const MAX_ASSESS_CARDS = 16
const LIFECYCLES = ['draft', 'preliminary', 'certified']
const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft', preliminary: 'Preliminary', under_review: 'Under review', certified: 'Final', revised: 'Revised', retired: 'Retired',
}
const BADGE_CLASS: Record<string, string> = { draft: 'deep-badge-draft', preliminary: 'deep-badge-prelim', certified: 'deep-badge-cert' }
const TIER_LABELS: Record<string, string> = {
  minimally_disturbed: 'Minimally disturbed', least_disturbed: 'Least disturbed', best_available: 'Best available (fallback)',
}

/** str.title() for a status DEEP does not know (status_label's fallback). */
const title = (s: string) => s.replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase())

/** session.status_label */
export function statusLabel(status: unknown): string {
  const s = String(status ?? '').trim().toLowerCase()
  return STATUS_LABELS[s] || (s ? title(s) : STATUS_LABELS.preliminary)
}

/** _badge_class: a status DEEP does not run reads as preliminary. */
function badgeClass(lifecycle: unknown): string {
  const s = lifecycle ? String(lifecycle).trim().toLowerCase() : ''
  return BADGE_CLASS[LIFECYCLES.includes(s) ? s : 'preliminary']
}

/** _tier_label */
export function tierLabel(tier: unknown): string {
  if (!tier) return ''
  return TIER_LABELS[String(tier)] ?? String(tier).replace(/_/g, ' ')
}

/** _assess_cards_ui */
export function assessCards(covering: Covering[], selected: string | null): Tag {
  const cards = covering.slice(0, MAX_ASSESS_CARDS).map((entry, i) => {
    const choices: [string, string][] = entry.refs.map((ref) =>
      [ref, `v${entry.versionByRef[ref] ?? 'None'} (${statusLabel(entry.lifecycleByRef[ref] ?? 'preliminary')})`])
    const defaultLife = entry.lifecycleByRef[entry.defaultRef] ?? 'preliminary'
    const defaultTier = tierLabel(entry.referenceTierByRef[entry.defaultRef])
    const isSel = selected !== null && entry.refs.includes(selected)
    return h.div(
      h.div(h.span(entry.assessmentName, { class: 'deep-card-name' }), h.span(statusLabel(defaultLife), { class: `deep-card-badge ${badgeClass(defaultLife)}` }),
        { class: 'deep-card-head' }),
      h.div(entry.regionName || '', { class: 'deep-card-region' }),
      defaultTier ? h.div(h.span('Reference tier', { class: 'deep-card-verlabel' }), h.span(' ' + defaultTier, { class: 'deep-card-tier' }),
        { class: 'deep-card-tierrow' }) : null,
      h.div(h.span('Version', { class: 'deep-card-verlabel' }), inputSelect(`deep-assess_ver_${i}`, null, choices, entry.defaultRef, '200px'),
        { class: 'deep-card-verrow' }),
      actionButton(`deep-assess_pick_${i}`, isSel ? '✓ Selected' : 'Select this assessment', isSel ? 'btn btn-sm btn-primary' : 'btn btn-sm btn-outline-primary'),
      { class: isSel ? 'deep-assess-card is-selected' : 'deep-assess-card' })
  })
  return h.div(...cards, { class: 'deep-assess-cards' })
}

/** _change_assessment's dialog. */
export function changeDialog(covering: Covering[], selected: string | null): Tag {
  const body = covering.length ? assessCards(covering, selected) : h.p('No assessment covers this point.')
  return modal(body, { title: 'Choose a detailed assessment', size: 'l', easyClose: true, footer: modalButton('Cancel') })
}
