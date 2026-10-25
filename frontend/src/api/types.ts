import type { components, paths } from './schema'

// Every shape the dashboard handles comes from the engine's OpenAPI schema (`npm run gen:api`),
// so a backend change that breaks the UI fails the type check instead of failing at runtime.
type S = components['schemas']

export type Me = S['MeOut']
export type ExceptionRow = S['ExceptionOut']
export type ExceptionDetail = S['ExceptionDetail']
export type ExceptionContext = S['ExceptionContext']
export type Transaction = S['TransactionOut']
export type MatchRow = S['MatchResultOut']
export type AuditEntry = S['AuditEntryOut']
export type AuditVerification = S['VerifyOut']
export type FileRow = S['FileOut']
export type FileVerification = S['FileVerificationOut']
export type RunRow = S['RunOut']
export type AlertRow = S['AlertOut']
export type DashboardSummary = S['DashboardSummary']
export type BankPerformance = S['BankDashboardRow']
export type TrendPoint = S['DailyTrendPoint']
export type UploadResult = S['UploadOut']
export type ExceptionStatus = S['ExceptionStatus']
export type ExceptionCategory = S['ExceptionCategory']
export type ExceptionSeverity = S['ExceptionSeverity']

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

type Query<P extends keyof paths> = paths[P] extends { get: { parameters: { query?: infer Q } } }
  ? NonNullable<Q>
  : never

export type ExceptionFilters = Query<'/api/v1/exceptions'>
export type AuditFilters = Query<'/api/v1/audit'>
export type FileFilters = Query<'/api/v1/files'>
export type RunFilters = Query<'/api/v1/reconciliation/runs'>
export type AlertFilters = Query<'/api/v1/anomalies'>

export type Role = 'VIEWER' | 'ANALYST' | 'ADMIN' | 'SYSTEM'
export const ROLE_RANK: Record<Role, number> = { VIEWER: 1, ANALYST: 2, ADMIN: 3, SYSTEM: 4 }

export function atLeast(role: string | undefined, needed: Role): boolean {
  return role !== undefined && (ROLE_RANK[role as Role] ?? 0) >= ROLE_RANK[needed]
}

export type ReportKind = 'daily' | 'exception-ageing' | 'audit-compliance'
export type ReportCell = string | number | boolean | null

export interface ReportDoc {
  kind: string
  title: string
  generated_at: string
  parameters: Record<string, unknown>
  summary: Record<string, ReportCell>
  tables: { name: string; columns: string[]; rows: ReportCell[][] }[]
  verdict: 'PASS' | 'FAIL' | null
  notes: string[]
}
