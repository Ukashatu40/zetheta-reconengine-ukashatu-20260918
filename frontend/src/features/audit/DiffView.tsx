import { useState } from 'react'
import { Button } from '@/components/Button'
import { diffJson, displayValue } from '@/lib/diff'
import './audit.css'

/** What an audit entry changed, leaf by leaf. Values are shown as text, never as markup. */
const COLLAPSED_ROWS = 12

export function DiffView({ before, after }: { before: unknown; after: unknown }) {
  const [all, setAll] = useState(false)
  const entries = diffJson(before, after)
  const shown = all ? entries : entries.slice(0, COLLAPSED_ROWS)
  if (entries.length === 0) {
    return <p className="panel__note">The entry records no before or after state.</p>
  }
  return (
    <>
      <table className="diff">
        <caption className="sr-only">Changes recorded by this entry</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Before</th>
            <th scope="col">After</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((entry) => (
            <tr key={entry.path} data-kind={entry.kind}>
              <th scope="row" className="mono">
                {entry.path}
              </th>
              <td className="mono">{entry.kind === 'added' ? '' : displayValue(entry.before)}</td>
              <td className="mono">{entry.kind === 'removed' ? '' : displayValue(entry.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {entries.length > COLLAPSED_ROWS && (
        <Button size="small" variant="quiet" onClick={() => setAll(!all)}>
          {all ? 'Show fewer changes' : `Show all ${entries.length} changes`}
        </Button>
      )}
    </>
  )
}
