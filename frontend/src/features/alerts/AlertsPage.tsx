import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useAlerts, useInvalidate } from '@/api/queries'
import type { AlertFilters } from '@/api/types'
import { useSession } from '@/auth/session'
import { Severity, StatusChip } from '@/components/Badges'
import { Button } from '@/components/Button'
import { SelectField } from '@/components/Fields'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import { useToast } from '@/components/Toast'
import { formatDateTime, humanise } from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'

const PAGE_SIZE = 20

export function AlertsPage() {
  const url = useUrlState(PAGE_SIZE)
  const { can } = useSession()
  const toast = useToast()
  const invalidate = useInvalidate()
  const filters: AlertFilters = {
    status: url.get('status') || 'OPEN',
    severity: url.get('severity') || undefined,
    route_to: url.get('route_to') || undefined,
    limit: PAGE_SIZE,
    offset: url.offset,
  }
  const query = useAlerts(filters)
  const page = query.data
  const acknowledge = useMutation({
    mutationFn: (id: string) => api.acknowledgeAlert(id),
    onSuccess: async () => {
      toast('Acknowledged')
      await invalidate('alerts', 'summary')
    },
    onError: (error) =>
      toast(error instanceof Error ? error.message : 'That did not work.', 'danger'),
  })

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Alerts</h1>
          <p className="page__lede">
            Unusual amounts, bursts, missing files and suspicious match rates, raised by the anomaly
            rules. Each is routed to operations or compliance.
          </p>
        </div>
        <div className="toolbar">
          <SelectField
            label="Status"
            allLabel={null}
            value={filters.status ?? 'OPEN'}
            onChange={(v) => url.set({ status: v })}
            options={[
              { value: 'OPEN', label: 'Open' },
              { value: 'ACKNOWLEDGED', label: 'Acknowledged' },
            ]}
          />
          <SelectField
            label="Severity"
            value={url.get('severity')}
            onChange={(v) => url.set({ severity: v })}
            options={['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => ({
              value: s,
              label: humanise(s),
            }))}
          />
          <SelectField
            label="Routed to"
            value={url.get('route_to')}
            onChange={(v) => url.set({ route_to: v })}
            options={[
              { value: 'operations', label: 'Operations' },
              { value: 'compliance', label: 'Compliance' },
            ]}
          />
        </div>
      </header>
      {query.isPending && <Loading rows={6} label="Loading alerts" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState title="No alerts" hint="Nothing unusual has been detected." />
      )}
      {page && page.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Alerts</caption>
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">Alert</th>
                  <th scope="col">Bank</th>
                  <th scope="col">Routed to</th>
                  <th scope="col">Raised (IST)</th>
                  <th scope="col">Status</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {page.items.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <Severity level={a.severity} />
                    </td>
                    <td>
                      <strong>{humanise(a.alert_type)}</strong>
                      <span style={{ display: 'block' }}>{a.message}</span>
                    </td>
                    <td>{a.bank_code ?? 'All'}</td>
                    <td>{humanise(a.route_to)}</td>
                    <td className="num">{formatDateTime(a.created_at)}</td>
                    <td>
                      <StatusChip status={a.status} />
                    </td>
                    <td>
                      {a.status === 'OPEN' && can('ANALYST') && (
                        <Button
                          size="small"
                          busy={acknowledge.isPending && acknowledge.variables === a.id}
                          onClick={() => acknowledge.mutate(a.id)}
                        >
                          Acknowledge
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
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
