import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useException, useExceptionContext, useInvalidate } from '@/api/queries'
import type { ExceptionRow } from '@/api/types'
import { useSession } from '@/auth/session'
import { Button } from '@/components/Button'
import { TextArea, TextField } from '@/components/Fields'
import { ErrorState, Loading } from '@/components/States'
import { useToast } from '@/components/Toast'
import { Money } from '@/components/Money'
import { formatAssignee, formatDateTime, humanise } from '@/lib/format'
import { PairView } from './PairView'
import './detail.css'

const RESOLVABLE = ['OPEN', 'IN_REVIEW', 'ESCALATED', 'REOPENED']
const SENIOR_TIER = 3

function Actions({ row }: { row: ExceptionRow }) {
  const { can } = useSession()
  const toast = useToast()
  const invalidate = useInvalidate()
  const [mode, setMode] = useState<'resolve' | 'assign' | 'reopen' | null>(null)
  const [text, setText] = useState('')
  const [assignee, setAssignee] = useState('')
  const [outcome, setOutcome] = useState<'RESOLVED' | 'WRITTEN_OFF'>('RESOLVED')

  const open = RESOLVABLE.includes(row.status)
  const needsAdmin = row.assigned_tier >= SENIOR_TIER || outcome === 'WRITTEN_OFF'

  const done = async (message: string) => {
    toast(message)
    setMode(null)
    setText('')
    setAssignee('')
    await invalidate('exceptions', 'exception', 'exception-context', 'summary', 'banks')
  }
  const fail = (error: unknown) =>
    toast(error instanceof Error ? error.message : 'The action failed.', 'danger')

  const resolve = useMutation({
    mutationFn: () => api.resolveException(row.id, { resolution_details: text.trim(), outcome }),
    onSuccess: () => done(outcome === 'RESOLVED' ? 'Resolved' : 'Written off'),
    onError: fail,
  })
  const assign = useMutation({
    mutationFn: () =>
      api.assignException(row.id, { assignee: assignee.trim(), note: text.trim() || undefined }),
    onSuccess: () => done('Assigned'),
    onError: fail,
  })
  const reopen = useMutation({
    mutationFn: () => api.reopenException(row.id, text.trim()),
    onSuccess: () => done('Reopened'),
    onError: fail,
  })

  if (!can('ANALYST')) {
    return <p className="panel__note">Your key can read exceptions but not change them.</p>
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (mode === 'resolve') resolve.mutate()
    if (mode === 'assign') assign.mutate()
    if (mode === 'reopen') reopen.mutate()
  }

  return (
    <div className="actions">
      <div className="actions__buttons">
        {open && (
          <>
            <Button variant="primary" size="small" onClick={() => setMode('resolve')}>
              Resolve
            </Button>
            <Button size="small" onClick={() => setMode('assign')}>
              Assign
            </Button>
          </>
        )}
        {!open && (
          <Button size="small" onClick={() => setMode('reopen')}>
            Reopen
          </Button>
        )}
      </div>
      {mode && (
        <form className="actions__form" onSubmit={submit}>
          {mode === 'assign' && (
            <TextField
              label="Assign to"
              value={assignee}
              onChange={(event) => setAssignee(event.target.value)}
              list="assignees"
              required
              maxLength={100}
              hint="A person, or a queue such as queue:analyst."
            />
          )}
          {mode === 'resolve' && (
            <label className="field">
              <span>Outcome</span>
              <select
                className="select"
                value={outcome}
                onChange={(event) => setOutcome(event.target.value as typeof outcome)}
              >
                <option value="RESOLVED">Resolved</option>
                <option value="WRITTEN_OFF">Written off</option>
              </select>
            </label>
          )}
          <TextArea
            label={mode === 'resolve' ? 'What was done' : mode === 'reopen' ? 'Why reopen' : 'Note'}
            value={text}
            onChange={setText}
            required={mode !== 'assign'}
            maxLength={2000}
            hint={
              mode === 'resolve' && needsAdmin && !can('ADMIN')
                ? 'This needs an administrator. The engine will refuse it for your key.'
                : 'Recorded in the audit trail with your name.'
            }
          />
          <div className="actions__buttons">
            <Button
              type="submit"
              variant="primary"
              size="small"
              busy={resolve.isPending || assign.isPending || reopen.isPending}
            >
              {mode === 'resolve' ? 'Save resolution' : mode === 'assign' ? 'Assign' : 'Reopen'}
            </Button>
            <Button size="small" variant="quiet" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      <datalist id="assignees">
        <option value="queue:analyst" />
        <option value="queue:senior-ops" />
        <option value="queue:compliance" />
      </datalist>
    </div>
  )
}

export function ExceptionDetail({ row }: { row: ExceptionRow }) {
  const detail = useException(row.id, true)
  const context = useExceptionContext(row.id, true)

  return (
    <div className="detail">
      <div className="detail__main">
        {context.isPending && <Loading rows={4} label="Loading the records" />}
        {context.isError && (
          <ErrorState error={context.error} retry={() => void context.refetch()} />
        )}
        {context.data &&
          (context.data.transactions.length > 0 ? (
            <PairView transactions={context.data.transactions} matches={context.data.matches} />
          ) : (
            <p className="panel__note">
              This exception is not tied to a pair of records
              {Array.isArray((row.affected_records as { line_numbers?: number[] }).line_numbers)
                ? `: line ${(row.affected_records as { line_numbers: number[] }).line_numbers.join(', ')} of the source file could not be read`
                : ''}
              .
            </p>
          ))}
        <section>
          <h3 className="detail__h">Why it was raised</h3>
          <p>{row.rationale}</p>
          <h3 className="detail__h">Suggested next step</h3>
          <p>{row.suggested_resolution}</p>
          {row.resolution_details && (
            <>
              <h3 className="detail__h">Resolution</h3>
              <p>
                {row.resolution_details}{' '}
                <span className="panel__note">
                  {row.resolved_by} on {formatDateTime(row.resolved_at, { full: true })}
                </span>
              </p>
            </>
          )}
        </section>
      </div>
      <aside className="detail__side">
        <dl className="detail__facts">
          <div>
            <dt>Impact</dt>
            <dd>
              <Money
                amount={row.financial_impact_amount}
                currency={row.financial_impact_currency}
              />
            </dd>
          </div>
          <div>
            <dt>Held by</dt>
            <dd>{formatAssignee(row.assigned_to)}</dd>
          </div>
          <div>
            <dt>Raised</dt>
            <dd>{formatDateTime(row.created_at)}</dd>
          </div>
          <div>
            <dt>SLA due</dt>
            <dd>{formatDateTime(row.sla_deadline)}</dd>
          </div>
        </dl>
        <Actions row={row} />
        <section>
          <h3 className="detail__h">History</h3>
          {detail.isPending && <Loading rows={3} />}
          {detail.data && (
            <ol className="timeline">
              {[...detail.data.events].reverse().map((event, i) => (
                <li key={`${event.occurred_at}-${i}`}>
                  <strong>{humanise(event.event_type)}</strong>
                  <span>{event.detail}</span>
                  <span className="timeline__meta">
                    {event.actor}, {formatDateTime(event.occurred_at)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </aside>
    </div>
  )
}
