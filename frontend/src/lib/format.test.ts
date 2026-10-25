import {
  formatAssignee,
  formatCompactInr,
  formatDate,
  formatDuration,
  formatMoney,
  formatRate,
  humanise,
  MASK,
  shortHash,
} from './format'

describe('formatMoney', () => {
  it('groups digits the Indian way', () => {
    expect(formatMoney('4297883.75')).toBe('₹42,97,883.75')
    expect(formatMoney(1250000, 'INR', { decimals: false })).toBe('₹12,50,000')
  })
  it('hides the figure when masked, and says nothing when there is none', () => {
    expect(formatMoney('10.00', 'INR', { masked: true })).toBe(MASK)
    expect(formatMoney(null)).toBe('–')
    expect(formatMoney('not a number')).toBe('–')
  })
  it('keeps the currency of non-rupee amounts', () => {
    expect(formatMoney('1500.5', 'USD')).toContain('1,500.50')
  })
})

describe('formatCompactInr', () => {
  it('uses lakh and crore', () => {
    expect(formatCompactInr(4_297_883)).toBe('₹42.98 L')
    expect(formatCompactInr(123_456_789)).toBe('₹12.35 Cr')
    expect(formatCompactInr(9_999)).toBe('₹9,999')
    expect(formatCompactInr(5, true)).toBe(MASK)
  })
})

describe('other formatters', () => {
  it('formats rates, dates, durations and hashes', () => {
    expect(formatRate(0.8743)).toBe('87.4%')
    expect(formatRate(null)).toBe('–')
    expect(formatDate('2026-10-22')).toBe('22 Oct 2026')
    expect(formatDuration(0.238)).toBe('238 ms')
    expect(formatDuration(75)).toBe('1 min 15 s')
    expect(shortHash('a'.repeat(64))).toBe('aaaaaaaa…aaaaaa')
  })
  it('turns codes into words', () => {
    expect(humanise('AMOUNT_MISMATCH')).toBe('Amount mismatch')
    expect(formatAssignee('queue:senior-ops')).toBe('Senior ops queue')
    expect(formatAssignee(null)).toBe('Unassigned')
    expect(formatAssignee('priya')).toBe('priya')
  })
})
