import { describe, expect, it } from 'vitest'
import { pyFixed } from '../src/app/pyfmt'

describe('pyFixed', () => {
  it('matches Python on plain values', () => {
    // python: f"{39.5:.4f}", f"{-98.35:.4f}", f"{0.1:.1f}", f"{2.675:.2f}"
    expect(pyFixed(39.5, 4)).toBe('39.5000')
    expect(pyFixed(-98.35, 4)).toBe('-98.3500')
    expect(pyFixed(0.1, 1)).toBe('0.1')
    expect(pyFixed(2.675, 2)).toBe('2.67')        // 2.67499999... in binary
  })
  it('rounds exact ties half to even, as Python does', () => {
    // python: f"{39.03125:.4f}" == '39.0312', f"{0.125:.2f}" == '0.12', f"{0.375:.2f}" == '0.38'
    expect(pyFixed(39.03125, 4)).toBe('39.0312')
    expect(pyFixed(-39.03125, 4)).toBe('-39.0312')
    expect(pyFixed(0.125, 2)).toBe('0.12')
    expect(pyFixed(0.375, 2)).toBe('0.38')
    expect(pyFixed(2.5, 0)).toBe('2')
    expect(pyFixed(3.5, 0)).toBe('4')
    expect(pyFixed(0.5, 0)).toBe('0')
  })
})
