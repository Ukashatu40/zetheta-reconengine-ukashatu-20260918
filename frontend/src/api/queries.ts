import {
  keepPreviousData,
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { ApiError } from './client'
import { api } from './endpoints'
import type { AlertFilters, AuditFilters, ExceptionFilters, FileFilters, RunFilters } from './types'

/** Reads are retried only for the failures that can pass; a 4xx is the answer, not a glitch. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (count, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
      },
    },
  })
}

export const keys = {
  summary: ['summary'] as const,
  banks: ['banks'] as const,
  exceptions: (f: ExceptionFilters) => ['exceptions', f] as const,
  exception: (id: string) => ['exception', id] as const,
  context: (id: string) => ['exception-context', id] as const,
  matches: (status: string, offset: number) => ['matches', status, offset] as const,
  audit: (f: AuditFilters) => ['audit', f] as const,
  auditVerify: ['audit-verify'] as const,
  files: (f: FileFilters) => ['files', f] as const,
  runs: (f: RunFilters) => ['runs', f] as const,
  alerts: (f: AlertFilters) => ['alerts', f] as const,
}

const LIVE_MS = 30_000

export const useSummary = () =>
  useQuery({ queryKey: keys.summary, queryFn: api.summary, refetchInterval: LIVE_MS })
export const useBanks = () =>
  useQuery({ queryKey: keys.banks, queryFn: api.banks, refetchInterval: LIVE_MS })

export const useExceptions = (filters: ExceptionFilters) =>
  useQuery({
    queryKey: keys.exceptions(filters),
    queryFn: () => api.exceptions(filters),
    placeholderData: keepPreviousData,
    refetchInterval: LIVE_MS,
  })
export const useException = (id: string, enabled: boolean) =>
  useQuery({ queryKey: keys.exception(id), queryFn: () => api.exception(id), enabled })
export const useExceptionContext = (id: string, enabled: boolean) =>
  useQuery({ queryKey: keys.context(id), queryFn: () => api.exceptionContext(id), enabled })

export const useMatches = (status: string, offset: number, limit: number) =>
  useQuery({
    queryKey: keys.matches(status, offset),
    queryFn: () => api.matches({ status, offset, limit }),
    placeholderData: keepPreviousData,
  })

export const useAudit = (filters: AuditFilters) =>
  useQuery({
    queryKey: keys.audit(filters),
    queryFn: () => api.audit(filters),
    placeholderData: keepPreviousData,
  })

export const useFiles = (filters: FileFilters) =>
  useQuery({
    queryKey: keys.files(filters),
    queryFn: () => api.files(filters),
    placeholderData: keepPreviousData,
  })
export const useRuns = (filters: RunFilters) =>
  useQuery({
    queryKey: keys.runs(filters),
    queryFn: () => api.runs(filters),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  })
export const useAlerts = (filters: AlertFilters) =>
  useQuery({
    queryKey: keys.alerts(filters),
    queryFn: () => api.alerts(filters),
    placeholderData: keepPreviousData,
  })

/** After a write, refetch everything that could show it. */
export function useInvalidate() {
  const client = useQueryClient()
  return (...roots: string[]) =>
    Promise.all(roots.map((root) => client.invalidateQueries({ queryKey: [root] })))
}

export { useMutation }
