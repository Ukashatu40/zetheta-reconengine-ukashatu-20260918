import { Link } from 'react-router-dom'
import type { BankPerformance } from '@/api/types'
import { formatCount, formatPercent, formatRate } from '@/lib/format'
import './charts.css'

type Direction = 'high-is-good' | 'high-is-bad'

interface Column {
  key: string
  label: string
  direction: Direction
  /** Scales the value to 0..1 for colour. */
  scale: (bank: BankPerformance, all: BankPerformance[]) => number
  show: (bank: BankPerformance) => string
}

const COLUMNS: Column[] = [
  {
    key: 'match',
    label: 'Match rate',
    direction: 'high-is-good',
    scale: (b) => b.match_rate,
    show: (b) => formatRate(b.match_rate),
  },
  {
    key: 'sla',
    label: 'SLA met',
    direction: 'high-is-good',
    scale: (b) => b.sla_compliance_percent / 100,
    show: (b) => formatPercent(b.sla_compliance_percent, 0),
  },
  {
    key: 'rate',
    label: 'Exception rate',
    direction: 'high-is-bad',
    scale: (b) => Math.min(1, b.exception_rate / 0.2),
    show: (b) => formatRate(b.exception_rate),
  },
  {
    key: 'open',
    label: 'Open exceptions',
    direction: 'high-is-bad',
    scale: (b, all) =>
      b.open_exception_count / Math.max(1, ...all.map((x) => x.open_exception_count)),
    show: (b) => formatCount(b.open_exception_count),
  },
]

const MAX_TINT = 40

/** Teal where the bank is doing well, red where it is not, untinted in between. */
function tint(direction: Direction, level: number): string {
  const clamped = Math.max(0, Math.min(1, level))
  const goodness = direction === 'high-is-good' ? clamped : 1 - clamped
  const weight = Math.round(Math.abs(goodness - 0.5) * 2 * MAX_TINT)
  const colour = goodness >= 0.5 ? 'var(--ok)' : 'var(--danger)'
  return `color-mix(in srgb, ${colour} ${weight}%, var(--sheet))`
}

export function Heatmap({ banks }: { banks: BankPerformance[] }) {
  return (
    <div className="table-wrap">
      <table className="heat">
        <caption className="sr-only">Performance by bank</caption>
        <thead>
          <tr>
            <th scope="col">Bank</th>
            {COLUMNS.map((c) => (
              <th key={c.key} scope="col" className="right">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {banks.map((bank) => (
            <tr key={bank.bank_code}>
              <th scope="row">
                <Link to={`/exceptions?bank_code=${bank.bank_code}`}>{bank.bank_name}</Link>
                <span className="heat__sub num">
                  {formatCount(bank.total_transactions)} transactions
                </span>
              </th>
              {COLUMNS.map((c) => (
                <td
                  key={c.key}
                  className="right num"
                  style={{ background: tint(c.direction, c.scale(bank, banks)) }}
                >
                  {c.show(bank)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
