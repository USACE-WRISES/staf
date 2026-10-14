// DEEP's Change dialog (app.py _change_assessment and _assess_cards_ui): one card per assessment whose
// region holds the site (at most 16), each with its default version's status badge and reference tier,
// a version chooser starting at the default, and its Select button ("✓ Selected" on the one in use).
import { h, type Tag } from './htm'
import type { Covering } from './deepAssess'
import { statusLabel, statusPill } from './deepStatus'
import { actionButton, inputSelect, modal, modalButton } from '../shared/shinyui'

export { statusLabel } from './deepStatus'
export const MAX_ASSESS_CARDS = 16
const TIER_LABELS: Record<string, string> = {
  minimally_disturbed: 'Minimally disturbed', least_disturbed: 'Least disturbed', best_available: 'Best available (fallback)',
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
      h.div(h.span(entry.assessmentName, { class: 'deep-card-name' }), statusPill(defaultLife), { class: 'deep-card-head' }),
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
