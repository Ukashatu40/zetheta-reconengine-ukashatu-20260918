import type { MatchRow, Transaction } from '@/api/types'
import { Money } from '@/components/Money'
import { formatDate, humanise } from '@/lib/format'
import './pair.css'

type Field = {
  label: string
  value: (t: Transaction) => string
  money?: boolean
}

const FIELDS: Field[] = [
  { label: 'Reference', value: (t) => t.normalised_reference ?? t.txn_id },
  { label: 'Amount', value: (t) => `${t.amount} ${t.currency}`, money: true },
  { label: 'Direction', value: (t) => t.direction },
  { label: 'Date', value: (t) => t.txn_date },
  { label: 'Counterparty', value: (t) => t.counterparty_name ?? '–' },
]

function Slip({
  side,
  txn,
  other,
}: {
  side: 'Ledger' | 'Bank'
  txn: Transaction | undefined
  other: Transaction | undefined
}) {
  if (!txn) {
    return (
      <div className="slip slip--empty">
        <h4>{side}</h4>
        <p>No record on this side.</p>
      </div>
    )
  }
  return (
    <div className="slip">
      <h4>
        {side}
        <span>
          {txn.format_type} line {txn.source_line_no ?? '–'}
        </span>
      </h4>
      <dl>
        {FIELDS.map((f) => {
          const differs = other !== undefined && f.value(txn) !== f.value(other)
          return (
            <div key={f.label} data-differs={differs || undefined}>
              <dt>{f.label}</dt>
              <dd className="num">
                {f.label === 'Amount' ? (
                  <Money amount={txn.amount} currency={txn.currency} />
                ) : f.label === 'Date' ? (
                  formatDate(txn.txn_date)
                ) : (
                  f.value(txn)
                )}
              </dd>
            </div>
          )
        })}
      </dl>
      {txn.narration && <p className="slip__narration">{txn.narration}</p>}
    </div>
  )
}

/** The ledger entry and the bank entry side by side, with a tally line between them. */
export function PairView({
  transactions,
  matches,
}: {
  transactions: Transaction[]
  matches: MatchRow[]
}) {
  const internal = transactions.find((t) => t.source === 'INTERNAL')
  const external = transactions.find((t) => t.source === 'EXTERNAL')
  const rest = transactions.filter((t) => t !== internal && t !== external)
  const link = matches.find((m) => ['AUTO_MATCHED', 'CONFIRMED'].includes(m.status))
  const pending = matches.find((m) => m.status === 'PENDING_REVIEW')
  const linked = Boolean(link)

  return (
    <div className="pair">
      <div className="pair__slips">
        <Slip side="Ledger" txn={internal} other={external} />
        <svg className="pair__line" viewBox="0 0 80 60" aria-hidden="true" data-linked={linked}>
          <line x1="2" y1="30" x2="78" y2="30" />
          {linked ? (
            <line x1="30" y1="42" x2="50" y2="18" className="pair__strike" />
          ) : (
            <line x1="34" y1="30" x2="46" y2="30" className="pair__gap" />
          )}
        </svg>
        <Slip side="Bank" txn={external} other={internal} />
      </div>
      <p className="pair__caption">
        {link
          ? `Matched (${humanise(link.match_type)}, confidence ${link.confidence}).`
          : pending
            ? `A ${humanise(pending.match_type)} match at confidence ${pending.confidence} is waiting for review.`
            : 'These records are not matched to each other.'}
      </p>
      {rest.length > 0 && (
        <details className="pair__rest">
          <summary>{rest.length} more related records</summary>
          <ul>
            {rest.map((t) => (
              <li key={t.id}>
                <span>{humanise(t.source)}</span>
                <span className="num">{t.normalised_reference ?? t.txn_id}</span>
                <Money amount={t.amount} currency={t.currency} />
                <span>{formatDate(t.txn_date)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
