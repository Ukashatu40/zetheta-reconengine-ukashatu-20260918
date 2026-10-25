import type { ReactNode } from 'react'
import { ApiError } from '@/api/client'
import { Button } from './Button'

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="state">
      <p className="state__title">{title}</p>
      {hint && <p className="state__hint">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const api = error instanceof ApiError ? error : null
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">
        {api?.isUnreachable ? 'The engine is not answering' : 'This could not be loaded'}
      </p>
      <p className="state__hint">
        {api?.message ?? 'Something went wrong. Try again.'}
        {api?.requestId && (
          <>
            {' '}
            Reference <code>{api.requestId}</code>.
          </>
        )}
      </p>
      {retry && (
        <Button size="small" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  )
}

export function Loading({ rows = 6, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${95 - ((i * 13) % 40)}%` }} />
      ))}
    </div>
  )
}
