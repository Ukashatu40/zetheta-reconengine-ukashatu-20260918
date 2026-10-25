import { Link } from 'react-router-dom'
import { formatCount, humanise } from '@/lib/format'
import './charts.css'

export function CategoryBars({
  counts,
  linkKey,
}: {
  counts: Record<string, number>
  linkKey: 'category' | 'severity'
}) {
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1])
  const max = Math.max(1, ...rows.map(([, n]) => n))
  return (
    <ul className="bars">
      {rows.map(([name, n]) => (
        <li key={name}>
          <Link to={`/exceptions?${linkKey}=${name}`} className="bars__row">
            <span>{humanise(name)}</span>
            <span className="bars__track" aria-hidden="true">
              <span style={{ width: `${(n / max) * 100}%` }} />
            </span>
            <span className="num">{formatCount(n)}</span>
          </Link>
        </li>
      ))}
    </ul>
  )
}
