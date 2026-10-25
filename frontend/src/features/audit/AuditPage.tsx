import { Fragment, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useAudit } from '@/api/queries'
import type { AuditFilters } from '@/api/types'
import { Button } from '@/components/Button'
import { Hash } from '@/components/Hash'
import { SelectField, TextField } from '@/components/Fields'
import { Panel } from '@/components/Panel'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import { StatusChip } from '@/components/Badges'
import { formatCount, formatDateTime, humanise } from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'
import { DiffView } from './DiffView'
import './audit.css'

const PAGE_SIZE = 25
const CHAINS = ['exceptions', 'ingestion', 'matching', 'runs', 'config', 'alerts', 'users']
const ACTIONS = [
  'INGEST',
  'NORMALISE',
  'MATCH',
  'UNMATCH',
  'RESOLVE',
  'ESCALATE',
  'OVERRIDE',
  'RUN_STATE_CHANGE',
  'CONFIG_CHANGE',
  'EXCEPTION_CREATE',
  'MATCH_CONFIRM',
  'MATCH_REJECT',
  'ASSIGN',
  'FILE_VERIFY',
] as const

function summarise(records: Record<string, unknown>): string {
  const entries = Object.entries(records)
  const [name, value] = entries.find(([, v]) => typeof v === 'string') ?? []
  if (!name || typeof value !== 'string') {
    return entries.length === 0
      ? '–'
      : `${entries.length} ${entries.length === 1 ? 'detail' : 'details'}`
  }
  const text = `${name.replace(/_/g, ' ')} ${value.slice(0, 8)}`
  return entries.length > 1 ? `${text} and ${entries.length - 1} more` : text
}

function VerifyPanel() {
  const verify = useMutation({ mutationFn: api.verifyAudit })
  const result = verify.data
  return (
    <Panel
      title="Is the trail intact?"
      note="Re-computes every hash in every chain and checks each link. It reads the whole trail, so it can take a moment."
      actions={
        <Button onClick={() => verify.mutate()} busy={verify.isPending}>
          Verify the trail
        </Button>
      }
    >
      {verify.isError && <ErrorState error={verify.error} />}
      {result && (
        <div className="verify">
          <p role="status">
            <strong>{result.ok ? 'Every chain verified.' : 'A chain failed verification.'}</strong>{' '}
            {result.ok
              ? 'No entry has been altered, removed or reordered.'
              : 'Treat this as an incident and keep the evidence.'}
          </p>
          <ul>
            {Object.entries(result.chains).map(([chain, c]) => (
              <li key={chain}>
                <span>
                  {humanise(chain)}{' '}
                  <span className="panel__note num">{formatCount(c.entries_checked)} entries</span>
                </span>
                <StatusChip status={c.ok ? 'OK' : 'MISMATCH'} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  )
}

export function AuditPage() {
  const url = useUrlState(PAGE_SIZE)
  const [openId, setOpenId] = useState<string | null>(null)
  const filters: AuditFilters = {
    chain_id: url.get('chain_id') || undefined,
    action_type: (url.get('action_type') || undefined) as AuditFilters['action_type'],
    actor_id: url.get('actor_id') || undefined,
    date_from: url.get('date_from') || undefined,
    date_to: url.get('date_to') || undefined,
    limit: PAGE_SIZE,
    offset: url.offset,
  }
  const query = useAudit(filters)
  const page = query.data

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Audit trail</h1>
          <p className="page__lede">
            Every change the engine and its users have made, in the order it happened. Entries are
            hash-chained and cannot be edited.
          </p>
        </div>
      </header>

      <VerifyPanel />

      <div className="toolbar" role="search" aria-label="Filter the audit trail">
        <SelectField
          label="Chain"
          value={url.get('chain_id')}
          onChange={(v) => url.set({ chain_id: v })}
          options={CHAINS.map((c) => ({ value: c, label: humanise(c) }))}
        />
        <SelectField
          label="Action"
          value={url.get('action_type')}
          onChange={(v) => url.set({ action_type: v })}
          options={ACTIONS.map((a) => ({ value: a, label: humanise(a) }))}
        />
        <TextField
          label="Actor"
          value={url.get('actor_id')}
          onChange={(e) => url.set({ actor_id: e.target.value })}
          placeholder="api-key:name"
          maxLength={100}
        />
        <TextField
          label="From"
          type="date"
          value={url.get('date_from')}
          onChange={(e) => url.set({ date_from: e.target.value })}
        />
        <TextField
          label="To"
          type="date"
          value={url.get('date_to')}
          onChange={(e) => url.set({ date_to: e.target.value })}
        />
      </div>

      {query.isPending && <Loading rows={10} label="Loading the audit trail" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState title="No entries match" hint="Widen the date range or clear a filter." />
      )}
      {page && page.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Audit entries</caption>
              <thead>
                <tr>
                  <th scope="col">When (IST)</th>
                  <th scope="col">Entry</th>
                  <th scope="col">Action</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Concerns</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((entry) => {
                  const isOpen = openId === entry.event_id
                  return (
                    <Fragment key={entry.event_id}>
                      <tr data-open={isOpen}>
                        <td>
                          <button
                            type="button"
                            className="row-toggle num"
                            aria-expanded={isOpen}
                            onClick={() => setOpenId(isOpen ? null : entry.event_id)}
                          >
                            {formatDateTime(entry.occurred_at)}
                          </button>
                        </td>
                        <td className="num">
                          {humanise(entry.chain_id)} #{entry.sequence_no}
                        </td>
                        <td>{humanise(entry.action_type)}</td>
                        <td>{entry.actor_id}</td>
                        <td>{summarise(entry.affected_records)}</td>
                      </tr>
                      {isOpen && (
                        <tr className="detail-row">
                          <td colSpan={5}>
                            <div className="audit-detail">
                              <p>{entry.rationale}</p>
                              <DiffView before={entry.before_state} after={entry.after_state} />
                              <div className="audit-detail__hashes">
                                <span>
                                  This entry <Hash value={entry.current_hash} />
                                </span>
                                <span>
                                  Previous entry <Hash value={entry.prev_hash} />
                                </span>
                                <span>
                                  Recorded {formatDateTime(entry.recorded_at, { full: true })} IST
                                </span>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          <Pager
            total={page.total}
            limit={PAGE_SIZE}
            offset={url.offset}
            onChange={url.setOffset}
          />
        </>
      )}
    </div>
  )
}
