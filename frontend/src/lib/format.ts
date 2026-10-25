// Display formatting. Money is shown with Indian digit grouping (lakh, crore) because that is
// how the people reading it count; everything is rendered in IST because the banks settle in IST.

export const DISPLAY_TZ = 'Asia/Kolkata'
export const MASK = '••••••'

const count = new Intl.NumberFormat('en-IN')
const dateTime = new Intl.DateTimeFormat('en-IN', {
  timeZone: DISPLAY_TZ,
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const dateTimeFull = new Intl.DateTimeFormat('en-IN', {
  timeZone: DISPLAY_TZ,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export function formatCount(n: number | null | undefined): string {
  return n == null ? '–' : count.format(n)
}

/** A rate between 0 and 1 as a percentage. */
export function formatRate(rate: number | null | undefined, digits = 1): string {
  return rate == null ? '–' : `${(rate * 100).toFixed(digits)}%`
}

/** A value that is already a percentage (the API's `*_percent` fields). */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  return value == null ? '–' : `${value.toFixed(digits)}%`
}

export function formatMoney(
  amount: string | number | null | undefined,
  currency = 'INR',
  options: { masked?: boolean; decimals?: boolean } = {},
): string {
  if (amount == null || amount === '') return '–'
  if (options.masked) return MASK
  const value = typeof amount === 'string' ? Number(amount) : amount
  if (!Number.isFinite(value)) return '–'
  const decimals = options.decimals ?? true
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: decimals ? 2 : 0,
    maximumFractionDigits: decimals ? 2 : 0,
  }).format(value)
}

/** 12,50,000 becomes "12.5 L", 4,29,78,837 becomes "4.30 Cr". */
export function formatCompactInr(value: number, masked = false): string {
  if (masked) return MASK
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} L`
  return `${sign}₹${count.format(Math.round(abs))}`
}

export function formatDateTime(iso: string | null | undefined, opts: { full?: boolean } = {}) {
  if (!iso) return '–'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '–'
  return (opts.full ? dateTimeFull : dateTime).format(date)
}

/** A calendar date ("2026-10-22") shown without any time zone shift. */
export function formatDate(day: string | null | undefined): string {
  if (!day) return '–'
  const [y, m, d] = day.split('-').map(Number)
  if (!y || !m || !d) return day
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'UTC',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(new Date(Date.UTC(y, m - 1, d)))
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '–'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const m = Math.floor(seconds / 60)
  return `${m} min ${Math.round(seconds - m * 60)} s`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function shortHash(hash: string | null | undefined): string {
  if (!hash) return '–'
  return hash.length <= 16 ? hash : `${hash.slice(0, 8)}…${hash.slice(-6)}`
}

/** AMOUNT_MISMATCH becomes "Amount mismatch". */
export function humanise(value: string | null | undefined): string {
  if (!value) return '–'
  const text = value.replace(/[_-]+/g, ' ').toLowerCase()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** "queue:senior-ops" becomes "Senior ops queue"; a person's name is left alone. */
export function formatAssignee(value: string | null | undefined): string {
  if (!value) return 'Unassigned'
  return value.startsWith('queue:') ? `${humanise(value.slice(6))} queue` : value
}
