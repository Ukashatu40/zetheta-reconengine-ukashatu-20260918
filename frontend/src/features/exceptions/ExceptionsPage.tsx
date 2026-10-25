import { Fragment, useState } from 'react'
import { useExceptions } from '@/api/queries'
import type {
  ExceptionCategory,
  ExceptionFilters,
  ExceptionSeverity,
  ExceptionStatus,
} from '@/api/types'
import { SlaClock, Severity, StatusChip, TierChip } from '@/components/Badges'
import { SelectField } from '@/components/Fields'
import { Money } from '@/components/Money'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import { formatAssignee, humanise } from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'
import { ExceptionDetail } from './ExceptionDetail'

const PAGE_SIZE = 25

const STATUSES: ExceptionStatus[] = [
  'OPEN',
  'IN_REVIEW',
  'ESCALATED',
  'REOPENED',
  'RESOLVED',
  'WRITTEN_OFF',
]
const SEVERITIES: ExceptionSeverity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
const CATEGORIES: ExceptionCategory[] = [
  'MISSING_INTERNAL',
  'MISSING_EXTERNAL',
  'AMOUNT_MISMATCH',
  'DUPLICATE_INTERNAL',
  'DUPLICATE_EXTERNAL',
  'DATE_MISMATCH',
  'CURRENCY_MISMATCH',
  'DIRECTION_REVERSAL',
  'PARTIAL_MATCH',
  'NETTED_SETTLEMENT',
  'FEE_DEDUCTION',
  'FX_VARIANCE',
  'STALE_TRANSACTION',
  'FORMAT_ERROR',
  'REFERENCE_TRUNCATED',
  'TIMEZONE_OFFSET',
  'REVERSAL_PENDING',
  'REGULATORY_HOLD',
  'SETTLEMENT_DELAY',
]
const BANKS = ['AXIS', 'HDFC', 'ICICI', 'INDB', 'KOTAK', 'SBI', 'YESB']
const options = (values: readonly string[]) => values.map((v) => ({ value: v, label: humanise(v) }))

export function ExceptionsPage() {
  const url = useUrlState(PAGE_SIZE)
  const [openId, setOpenId] = useState<string | null>(null)

  const filters: ExceptionFilters = {
    status: (url.get('status') || undefined) as ExceptionStatus | undefined,
    category: (url.get('category') || undefined) as ExceptionCategory | undefined,
    severity: (url.get('severity') || undefined) as ExceptionSeverity | undefined,
    bank_code: url.get('bank_code') || undefined,
    tier: url.get('tier') ? Number(url.get('tier')) : undefined,
    limit: PAGE_SIZE,
    offset: url.offset,
  }
  const query = useExceptions(filters)
  const page = query.data
  const filtered = Boolean(
    filters.status || filters.category || filters.severity || filters.bank_code || filters.tier,
  )

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Exceptions</h1>
          <p className="page__lede">
            Everything that did not reconcile on its own. Open a row to see the two records side by
            side and act on it.
          </p>
        </div>
        <div className="toolbar" role="search" aria-label="Filter exceptions">
          <SelectField
            label="Status"
            value={url.get('status')}
            onChange={(v) => url.set({ status: v })}
            options={options(STATUSES)}
          />
          <SelectField
            label="Category"
            value={url.get('category')}
            onChange={(v) => url.set({ category: v })}
            options={options(CATEGORIES)}
          />
          <SelectField
            label="Severity"
            value={url.get('severity')}
            onChange={(v) => url.set({ severity: v })}
            options={options(SEVERITIES)}
          />
          <SelectField
            label="Bank"
            value={url.get('bank_code')}
            onChange={(v) => url.set({ bank_code: v })}
            options={BANKS.map((b) => ({ value: b, label: b }))}
          />
          <SelectField
            label="Tier"
            value={url.get('tier')}
            onChange={(v) => url.set({ tier: v })}
            options={[1, 2, 3, 4].map((t) => ({ value: String(t), label: `Tier ${t}` }))}
          />
        </div>
      </header>

      {query.isPending && <Loading rows={10} label="Loading exceptions" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState
          title={filtered ? 'Nothing matches those filters' : 'No exceptions'}
          hint={filtered ? 'Clear a filter to widen the list.' : 'Every record reconciled.'}
        />
      )}
      {page && page.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Exceptions</caption>
              <thead>
                <tr>
                  <th scope="col">Exception</th>
                  <th scope="col">Category</th>
                  <th scope="col">Severity</th>
                  <th scope="col" className="right">
                    Impact
                  </th>
                  <th scope="col">Bank</th>
                  <th scope="col">SLA</th>
                  <th scope="col">Held by</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => {
                  const isOpen = openId === row.id
                  return (
                    <Fragment key={row.id}>
                      <tr data-open={isOpen}>
                        <td>
                          <button
                            type="button"
                            className="row-toggle mono"
                            aria-expanded={isOpen}
                            aria-controls={`detail-${row.id}`}
                            onClick={() => setOpenId(isOpen ? null : row.id)}
                          >
                            {row.id.slice(0, 8)}
                          </button>
                        </td>
                        <td>{humanise(row.category)}</td>
                        <td>
                          <Severity level={row.severity} />
                        </td>
                        <td className="right">
                          <Money
                            amount={row.financial_impact_amount}
                            currency={row.financial_impact_currency}
                          />
                        </td>
                        <td>{row.bank_code}</td>
                        <td>
                          <SlaClock
                            deadline={row.sla_deadline}
                            resolvedAt={row.resolved_at}
                            breachedAt={row.sla_breached_at}
                          />
                        </td>
                        <td>
                          <TierChip tier={row.assigned_tier} />{' '}
                          <span>{formatAssignee(row.assigned_to)}</span>
                        </td>
                        <td>
                          <StatusChip status={row.status} />
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="detail-row" id={`detail-${row.id}`}>
                          <td colSpan={8}>
                            <ExceptionDetail row={row} />
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
