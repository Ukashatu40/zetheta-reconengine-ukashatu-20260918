/** Run metrics arrive as an open object; these read the fields the dashboard shows, safely. */
type Metrics = Record<string, unknown> | null | undefined

export function metric(metrics: Metrics, key: string): number | null {
  const value = metrics?.[key]
  const number = typeof value === 'string' ? Number(value) : value
  return typeof number === 'number' && Number.isFinite(number) ? number : null
}

export function stageSeconds(metrics: Metrics): { stage: string; seconds: number }[] {
  const durations = (metrics?.duration_seconds ?? {}) as Record<string, unknown>
  return ['exact', 'fuzzy', 'rules']
    .map((stage) => ({ stage, seconds: Number(durations[stage]) }))
    .filter((s) => Number.isFinite(s.seconds))
}

export function recordsProcessed(metrics: Metrics): number | null {
  const internal = metric(metrics, 'internal_pool')
  const external = metric(metrics, 'external_pool')
  return internal === null && external === null ? null : (internal ?? 0) + (external ?? 0)
}
