import { get, post, put } from './client'
import type {
  AlertFilters,
  AlertRow,
  AuditEntry,
  AuditFilters,
  AuditVerification,
  BankPerformance,
  DashboardSummary,
  ExceptionContext,
  ExceptionDetail,
  ExceptionFilters,
  ExceptionRow,
  FileFilters,
  FileRow,
  FileVerification,
  MatchRow,
  Me,
  Page,
  ReportDoc,
  ReportKind,
  RunFilters,
  RunRow,
  UploadResult,
} from './types'

const v1 = '/api/v1'

export const api = {
  me: () => get<Me>(`${v1}/me`),

  summary: () => get<DashboardSummary>(`${v1}/dashboard/summary`),
  banks: () => get<{ banks: BankPerformance[] }>(`${v1}/dashboard/banks`),

  exceptions: (filters: ExceptionFilters) =>
    get<Page<ExceptionRow>>(`${v1}/exceptions`, { query: filters }),
  exception: (id: string) => get<ExceptionDetail>(`${v1}/exceptions/${id}`),
  exceptionContext: (id: string) => get<ExceptionContext>(`${v1}/exceptions/${id}/context`),
  resolveException: (
    id: string,
    body: { resolution_details: string; outcome: 'RESOLVED' | 'WRITTEN_OFF' },
  ) => put<ExceptionRow>(`${v1}/exceptions/${id}/resolve`, { json: body }),
  assignException: (id: string, body: { assignee: string; note?: string }) =>
    put<ExceptionRow>(`${v1}/exceptions/${id}/assign`, { json: body }),
  reopenException: (id: string, reason: string) =>
    post<ExceptionRow>(`${v1}/exceptions/${id}/reopen`, { json: { reason } }),

  matches: (query: { status: string; limit?: number; offset?: number }) =>
    get<Page<MatchRow>>(`${v1}/matches`, { query }),
  confirmMatch: (id: string, note?: string) =>
    put<MatchRow>(`${v1}/matches/${id}/confirm`, { json: note ? { note } : {} }),
  rejectMatch: (id: string, reason: string) =>
    put<MatchRow>(`${v1}/matches/${id}/reject`, { json: { reason } }),

  audit: (filters: AuditFilters) => get<Page<AuditEntry>>(`${v1}/audit`, { query: filters }),
  verifyAudit: () => get<AuditVerification>(`${v1}/audit/verify`),

  files: (filters: FileFilters) => get<Page<FileRow>>(`${v1}/files`, { query: filters }),
  verifyFile: (id: string) => post<FileVerification>(`${v1}/files/${id}/verify`),
  uploadFile: (form: FormData) => post<UploadResult>(`${v1}/files/upload`, { form }),

  runs: (filters: RunFilters) => get<Page<RunRow>>(`${v1}/reconciliation/runs`, { query: filters }),
  startRun: (
    body: { bank_code: string; mode: 'FAST' | 'FULL'; date_from?: string; date_to?: string },
    idempotencyKey: string,
  ) =>
    post<RunRow>(`${v1}/reconciliation/run`, {
      json: body,
      headers: { 'Idempotency-Key': idempotencyKey },
    }),

  alerts: (filters: AlertFilters) => get<Page<AlertRow>>(`${v1}/anomalies`, { query: filters }),
  acknowledgeAlert: (id: string, note?: string) =>
    put<AlertRow>(`${v1}/anomalies/${id}/acknowledge`, { json: note ? { note } : {} }),

  report: (kind: ReportKind, date?: string) =>
    get<ReportDoc>(`${v1}/reports/${kind}`, {
      query: { format: 'json', date: kind === 'daily' ? date : undefined },
    }),
  reportCsv: (kind: ReportKind, date?: string) =>
    get<string>(`${v1}/reports/${kind}`, {
      query: { format: 'csv', date: kind === 'daily' ? date : undefined },
      as: 'text',
      headers: { Accept: 'text/csv' },
    }),
}
