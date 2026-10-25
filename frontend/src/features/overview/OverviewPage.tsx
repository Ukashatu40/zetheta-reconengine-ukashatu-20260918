import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAlerts, useBanks, useSummary } from '@/api/queries'
import type { DashboardSummary, ReportKind } from '@/api/types'
import { Button } from '@/components/Button'
import { Panel } from '@/components/Panel'
import { ErrorState, Loading } from '@/components/States'
import { Severity } from '@/components/Badges'
import { CategoryBars } from '@/charts/CategoryBars'
import { Heatmap } from '@/charts/Heatmap'
import { Tally } from '@/charts/Tally'
import { TrendChart } from '@/charts/TrendChart'
import { formatCount, formatDate, formatDateTime, formatDuration, formatRate } from '@/lib/format'
import { ReportDialog } from '@/features/reports/ReportDialog'
import './overview.css'

function Figure({
  label,
  value,
  to,
  tone,
  hint,
}: {
  label: string
  value: string
  to?: string
  tone?: 'danger' | 'warn'
  hint?: string
}) {
  const body = (
    <>
      <span className="figure__label">{label}</span>
      <span className="figure__value num" data-tone={tone}>
        {value}
      </span>
      {hint && <span className="figure__hint">{hint}</span>}
    </>
  )
  return to ? (
    <Link to={to} className="figure">
      {body}
    </Link>
  ) : (
    <div className="figure">{body}</div>
  )
}

function Hero({ summary, banks }: { summary: DashboardSummary; banks: number }) {
  const unmatched = summary.total_transactions - summary.matched_count
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero__figure">
        <h2 id="hero-title" className="hero__label">
          Matched
        </h2>
        <p className="hero__rate num">{formatRate(summary.match_rate)}</p>
        <p className="hero__sentence">
          {formatCount(summary.matched_count)} of {formatCount(summary.total_transactions)}{' '}
          transactions across {banks} banks have a counterpart.{' '}
          {unmatched > 0 ? (
            <>
              <strong className="num">{formatCount(unmatched)}</strong> do not yet.
            </>
          ) : (
            'None are outstanding.'
          )}
        </p>
      </div>
      <Tally rate={summary.match_rate} />
    </section>
  )
}

export function OverviewPage() {
  const summary = useSummary()
  const banks = useBanks()
  const alerts = useAlerts({ status: 'OPEN', limit: 5 })
  const [report, setReport] = useState<ReportKind | null>(null)
  const s = summary.data

  const trend = (s?.daily_trend ?? []).map((p) => ({
    label: formatDate(p.day).slice(0, 6),
    rate: p.total_transactions > 0 ? p.match_rate : null,
    exceptions: p.exception_count,
  }))

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Overview</h1>
          <p className="page__lede">
            {s
              ? `Position as of ${formatDate(s.as_of)}. Figures refresh every 30 seconds.`
              : 'Where every bank stands against the ledger.'}
          </p>
        </div>
        <div className="toolbar">
          <Button onClick={() => setReport('daily')}>Daily report</Button>
          <Button onClick={() => setReport('exception-ageing')}>Exception ageing</Button>
          <Button onClick={() => setReport('audit-compliance')}>Audit compliance</Button>
        </div>
      </header>

      {summary.isPending && <Loading rows={8} label="Loading the overview" />}
      {summary.isError && <ErrorState error={summary.error} retry={() => void summary.refetch()} />}

      {s && (
        <>
          <Hero summary={s} banks={banks.data?.banks.length ?? 7} />

          <div className="figures">
            <Figure
              label="Open exceptions"
              value={formatCount(s.unresolved_count)}
              to="/exceptions"
              hint={`${formatCount(s.exception_count)} raised in total`}
            />
            <Figure
              label="Past their SLA"
              value={formatCount(s.sla_breached_open_count)}
              to="/exceptions"
              tone={s.sla_breached_open_count > 0 ? 'danger' : undefined}
            />
            <Figure
              label="Awaiting match review"
              value={formatCount(s.pending_review_count)}
              to="/reviews"
              tone={s.pending_review_count > 0 ? 'warn' : undefined}
            />
            <Figure
              label="Open alerts"
              value={formatCount(s.open_alert_count)}
              to="/alerts"
              tone={s.open_alert_count > 0 ? 'warn' : undefined}
            />
            <Figure
              label="Average run time"
              value={formatDuration(s.avg_processing_time_seconds)}
              to="/runs"
            />
          </div>

          <div className="split">
            <Panel title="Match rate by day" note="Days with no transactions are left blank.">
              <TrendChart
                kind="line"
                title="Match rate"
                caption="last 7 days"
                points={trend.map((t) => ({ label: t.label, value: t.rate }))}
                max={1}
                format={(v) => `${Math.round(v * 100)}%`}
              />
            </Panel>
            <Panel title="Exceptions raised" note="By the day they were created.">
              <TrendChart
                kind="bars"
                title="Exceptions"
                caption="last 7 days"
                points={trend.map((t) => ({ label: t.label, value: t.exceptions }))}
                format={(v) => formatCount(Math.round(v))}
              />
            </Panel>
          </div>

          <div className="split split--wide-left">
            <Panel
              title="Banks"
              note="Teal is healthy, red needs attention. Select a bank to see its exceptions."
            >
              {banks.isPending && <Loading rows={5} />}
              {banks.isError && (
                <ErrorState error={banks.error} retry={() => void banks.refetch()} />
              )}
              {banks.data && <Heatmap banks={banks.data.banks} />}
            </Panel>
            <Panel title="What is going wrong" note="Exceptions by category. Select one to filter.">
              <CategoryBars counts={s.exceptions_by_category} linkKey="category" />
            </Panel>
          </div>

          <Panel
            title="Alerts that need a look"
            actions={<Link to="/alerts">All alerts</Link>}
            note="Newest open alerts from the anomaly rules."
          >
            {alerts.isPending && <Loading rows={3} />}
            {alerts.data && alerts.data.items.length === 0 && (
              <p className="panel__note">No open alerts.</p>
            )}
            <ul className="alertlist">
              {alerts.data?.items.map((a) => (
                <li key={a.id}>
                  <Severity level={a.severity} />
                  <span>
                    {a.message}
                    <span className="alertlist__meta">
                      <strong>{a.bank_code ?? 'All banks'}</strong>
                      <span className="num">{formatDateTime(a.created_at)}</span>
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </>
      )}

      <ReportDialog kind={report} date={s?.as_of} onClose={() => setReport(null)} />
    </div>
  )
}
