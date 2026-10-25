import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * Filters and page position live in the address bar, so a view can be bookmarked, shared with a
 * colleague, or reloaded without losing its place. Changing a filter returns to the first page.
 */
export function useUrlState(pageSize: number) {
  const [params, setParams] = useSearchParams()

  const get = useCallback((key: string) => params.get(key) ?? '', [params])
  const offset = useMemo(() => {
    const page = Number(params.get('page'))
    return Number.isInteger(page) && page > 0 ? (page - 1) * pageSize : 0
  }, [params, pageSize])

  const set = useCallback(
    (patch: Record<string, string>) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          for (const [key, value] of Object.entries(patch)) {
            if (value) next.set(key, value)
            else next.delete(key)
          }
          if (!('page' in patch)) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const setOffset = useCallback(
    (next: number) => set({ page: next > 0 ? String(Math.floor(next / pageSize) + 1) : '' }),
    [set, pageSize],
  )

  return { get, set, offset, setOffset }
}
