import { humanise } from '@/lib/format'
import { slaStatus } from '@/lib/sla'
import { useNow } from '@/lib/useNow'

const LEVEL: Record<string, number> = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 }

/** Severity as a four-step meter plus its word, so it never relies on colour alone. */
export function Severity({ level }: { level: string }) {
  return (
    <span className="sev" data-level={LEVEL[level] ?? 1}>
      <span className="sev__bars" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </span>
      {humanise(level)}
    </span>
  )
}

const STATUS_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'accent' | undefined> = {
  OPEN: 'accent',
  IN_REVIEW: 'accent',
  ESCALATED: 'warn',
  REOPENED: 'warn',
  RESOLVED: 'ok',
  WRITTEN_OFF: undefined,
  COMPLETED: 'ok',
  RUNNING: 'accent',
  PENDING: undefined,
  FAILED: 'danger',
  PARSED: 'ok',
  PARTIALLY_PARSED: 'warn',
  QUARANTINED: 'danger',
  ACKNOWLEDGED: 'ok',
  PENDING_REVIEW: 'warn',
  AUTO_MATCHED: 'ok',
  CONFIRMED: 'ok',
  REJECTED: 'danger',
  OK: 'ok',
  MISMATCH: 'danger',
}

export function StatusChip({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="chip">Not checked</span>
  return (
    <span className="chip" data-tone={STATUS_TONE[status]}>
      {humanise(status)}
    </span>
  )
}

export function TierChip({ tier }: { tier: number }) {
  return (
    <span className="chip" data-tone={tier >= 3 ? 'warn' : undefined}>
      Tier {tier}
    </span>
  )
}

/** Time left against the SLA, ticking once a minute. */
export function SlaClock({
  deadline,
  resolvedAt,
  breachedAt,
}: {
  deadline: string
  resolvedAt?: string | null
  breachedAt?: string | null
}) {
  const now = useNow(30_000)
  const status = slaStatus(deadline, now, { resolvedAt, breachedAt })
  return (
    <span className="sla" data-state={status.state} title={`Due ${deadline}`}>
      {status.label}
    </span>
  )
}
