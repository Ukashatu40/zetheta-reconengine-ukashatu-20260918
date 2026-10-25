import { Fragment, useState } from 'react'
import { useRuns } from '@/api/queries'
import type { RunFilters, RunRow } from '@/api/types'
import { useSession } from '@/auth/session'
import { StatusChip } from '@/components/Badges'
import { Button } from '@/components/Button'
import { SelectField } from '@/components/Fields'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import {
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
  formatRate,
  humanise,
} from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'
import { metric, recordsProcessed, stageSeconds } from './metrics'
import { StartRunDialog } from './StartRunDialog'
import './runs.css'

const PAGE_SIZE = 20
const BANKS = ['AXIS', 'HDFC', 'ICICI', 'INDB', 'KOTAK', 'SBI', 'YESB']
const COUNTS: [string, string][] = [
  ['exact_matched', 'Exact'],
  ['fuzzy_auto_matched', 'Fuzzy'],
  ['rule_matched', 'By rule'],
  ['split_groups', 'Split groups'],
  ['netted_groups', 'Netted groups'],
  ['cross_currency_matched', 'Cross-currency'],
  ['pending_review', 'Held for review'],
  ['exceptions_created', 'Exceptions raised'],
  ['exceptions_auto_resolved', 'Auto-resolved'],
]

function duration(run: RunRow): number | null {
  if (!run.started_at || !run.completed_at) return null
  return (Date.parse(run.completed_at) - Date.parse(run.started_at)) / 1000
}

function Stages({ run }: { run: RunRow }) {
  const stages = stageSeconds(run.metrics)
  const total = stages.reduce((sum, s) => sum + s.seconds, 0) || 1
  return (
    <div className="run-detail">
      {run.error_summary && (
        <p className="field-error" role="alert">
          {run.error_summary}
        </p>
      )}
      <div>
        <h3 className="detail__h">Where the time went</h3>
        <div
          className="stagebar"
          role="img"
          aria-label={stages.map((s) => `${s.stage} ${formatDuration(s.seconds)}`).join(', ')}
        >
          {stages.map((s) => (
            <span
              key={s.stage}
              style={{ flexGrow: Math.max(s.seconds / total, 0.02) }}
              data-stage={s.stage}
            >
              {humanise(s.stage)} {formatDuration(s.seconds)}
            </span>
          ))}
        </div>
      </div>
      <dl className="run-detail__counts">
        {COUNTS.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd className="num">{formatCount(metric(run.metrics, key))}</dd>
          </div>
        ))}
      </dl>
      <p className="panel__note">
        Requested by {run.requested_by}. Run {run.id}.
      </p>
    </div>
  )
}

export function RunsPage() {
  const url = useUrlState(PAGE_SIZE)
  const { can } = useSession()
  const [openId, setOpenId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const filters: RunFilters = {
    bank_code: url.get('bank_code') || undefined,
    status: url.get('status') || undefined,
    limit: PAGE_SIZE,
    offset: url.offset,
  }
  const query = useRuns(filters)
  const page = query.data

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Runs</h1>
          <p className="page__lede">
            Each reconciliation the engine has run, how long it took, and what it produced.
          </p>
        </div>
        <div className="toolbar">
          <SelectField
            label="Bank"
            value={url.get('bank_code')}
            onChange={(v) => url.set({ bank_code: v })}
            options={BANKS.map((b) => ({ value: b, label: b }))}
          />
          <SelectField
            label="Status"
            value={url.get('status')}
            onChange={(v) => url.set({ status: v })}
            options={['PENDING', 'RUNNING', 'COMPLETED', 'FAILED'].map((s) => ({
              value: s,
              label: humanise(s),
            }))}
          />
          {can('ANALYST') && (
            <Button variant="primary" onClick={() => setStarting(true)}>
              Run a reconciliation
            </Button>
          )}
        </div>
      </header>

      {query.isPending && <Loading rows={8} label="Loading runs" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState
          title="No runs yet"
          hint="Upload files, then run a reconciliation for a bank."
        />
      )}
      {page && page.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Reconciliation runs</caption>
              <thead>
                <tr>
                  <th scope="col">Started (IST)</th>
                  <th scope="col">Bank</th>
                  <th scope="col">Scope</th>
                  <th scope="col" className="right">
                    Records
                  </th>
                  <th scope="col" className="right">
                    Matched
                  </th>
                  <th scope="col" className="right">
                    Exceptions
                  </th>
                  <th scope="col" className="right">
                    Took
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((run) => {
                  const isOpen = openId === run.id
                  return (
                    <Fragment key={run.id}>
                      <tr data-open={isOpen}>
                        <td>
                          <button
                            type="button"
                            className="row-toggle num"
                            aria-expanded={isOpen}
                            onClick={() => setOpenId(isOpen ? null : run.id)}
                          >
                            {formatDateTime(run.started_at ?? run.created_at)}
                          </button>
                        </td>
                        <td>{run.bank_code}</td>
                        <td>
                          {humanise(run.mode)}
                          {run.date_from || run.date_to
                            ? `, ${formatDate(run.date_from)} to ${formatDate(run.date_to)}`
                            : ''}
                        </td>
                        <td className="right num">{formatCount(recordsProcessed(run.metrics))}</td>
                        <td className="right num">
                          {formatRate(metric(run.metrics, 'overall_match_rate_of_total'))}
                        </td>
                        <td className="right num">
                          {formatCount(metric(run.metrics, 'exceptions_created'))}
                        </td>
                        <td className="right num">{formatDuration(duration(run))}</td>
                        <td>
                          <StatusChip status={run.status} />
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="detail-row">
                          <td colSpan={8}>
                            <Stages run={run} />
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
      <StartRunDialog open={starting} onClose={() => setStarting(false)} />
    </div>
  )
}
