import { formatCount } from '@/lib/format'
import { Button } from './Button'

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}) {
  if (total === 0) return null
  const from = offset + 1
  const to = Math.min(offset + limit, total)
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="num">
        {formatCount(from)}–{formatCount(to)} of {formatCount(total)}
      </span>
      <span className="pager__buttons">
        <Button
          size="small"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button size="small" disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </span>
    </nav>
  )
}
