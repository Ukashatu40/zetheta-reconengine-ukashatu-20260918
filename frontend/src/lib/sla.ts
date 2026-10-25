export type SlaState = 'done' | 'ok' | 'urgent' | 'breached'

export interface SlaStatus {
  state: SlaState
  /** Milliseconds until the deadline; negative once it has passed. */
  remainingMs: number
  label: string
}

const URGENT_WITHIN_MS = 60 * 60 * 1000

function span(ms: number): string {
  const total = Math.floor(Math.abs(ms) / 60000)
  const days = Math.floor(total / 1440)
  const hours = Math.floor((total % 1440) / 60)
  const minutes = total % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  return `${minutes}m`
}

/**
 * Where an exception stands against its SLA. A resolved exception shows "Met" or "Missed",
 * whichever it was, instead of a clock that keeps running.
 */
export function slaStatus(
  deadlineIso: string,
  nowMs: number,
  options: { resolvedAt?: string | null; breachedAt?: string | null } = {},
): SlaStatus {
  const deadline = new Date(deadlineIso).getTime()
  if (options.resolvedAt) {
    const resolved = new Date(options.resolvedAt).getTime()
    const met = resolved <= deadline
    return { state: 'done', remainingMs: deadline - resolved, label: met ? 'Met' : 'Missed' }
  }
  const remainingMs = deadline - nowMs
  if (remainingMs < 0 || options.breachedAt) {
    return { state: 'breached', remainingMs, label: `${span(remainingMs)} over` }
  }
  return {
    state: remainingMs <= URGENT_WITHIN_MS ? 'urgent' : 'ok',
    remainingMs,
    label: `${span(remainingMs)} left`,
  }
}
