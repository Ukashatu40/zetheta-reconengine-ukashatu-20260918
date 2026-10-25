import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import type { ReportCell, ReportKind } from '@/api/types'
import { Button } from '@/components/Button'
import { Dialog } from '@/components/Dialog'
import { ErrorState, Loading } from '@/components/States'
import { formatDateTime, humanise } from '@/lib/format'
import { useToast } from '@/components/Toast'
import './reports.css'

const TITLES: Record<ReportKind, string> = {
  daily: 'Daily reconciliation report',
  'exception-ageing': 'Exception ageing report',
  'audit-compliance': 'Audit compliance report',
}

function cell(value: ReportCell): string {
  if (value === null) return '–'
  if (typeof value === 'number')
    return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, '')
  return String(value)
}

/** A report from the engine, drawn as plain text and tables. Nothing from it is ever parsed as HTML. */
export function ReportDialog({
  kind,
  date,
  onClose,
}: {
  kind: ReportKind | null
  date?: string
  onClose: () => void
}) {
  const toast = useToast()
  const query = useQuery({
    queryKey: ['report', kind, date],
    queryFn: () => api.report(kind as ReportKind, date),
    enabled: kind !== null,
  })
  const report = query.data

  const download = async () => {
    if (!kind) return
    try {
      const text = await api.reportCsv(kind, date)
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `${kind}${date ? `-${date}` : ''}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      toast(error instanceof Error ? error.message : 'The download failed.', 'danger')
    }
  }

  return (
    <Dialog open={kind !== null} title={kind ? TITLES[kind] : 'Report'} onClose={onClose} wide>
      {query.isPending && <Loading rows={6} label="Building the report" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {report && (
        <div className="report">
          <header className="report__head">
            <div>
              <h3>{report.title}</h3>
              <p className="panel__note">
                Generated {formatDateTime(report.generated_at, { full: true })} IST
              </p>
            </div>
            {report.verdict && (
              <span className="report__verdict" data-verdict={report.verdict}>
                {report.verdict === 'PASS' ? 'Passed' : 'Failed'}
              </span>
            )}
          </header>
          <dl className="report__summary">
            {Object.entries(report.summary).map(([key, value]) => (
              <div key={key}>
                <dt>{humanise(key)}</dt>
                <dd className="num">{cell(value)}</dd>
              </div>
            ))}
          </dl>
          {report.tables.map((table) => (
            <section key={table.name}>
              <h4>{table.name}</h4>
              {table.rows.length === 0 ? (
                <p className="panel__note">Nothing to show.</p>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        {table.columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {table.rows.map((row, i) => (
                        <tr key={i}>
                          {row.map((value, j) => (
                            <td
                              key={j}
                              className={typeof value === 'number' ? 'right num' : undefined}
                            >
                              {cell(value)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          ))}
          {report.notes.map((note) => (
            <p key={note} className="panel__note">
              {note}
            </p>
          ))}
          <div className="dialog__actions">
            <Button onClick={download}>Download CSV</Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}
