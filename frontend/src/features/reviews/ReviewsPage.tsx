import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useInvalidate, useMatches } from '@/api/queries'
import type { MatchRow } from '@/api/types'
import { useSession } from '@/auth/session'
import { Button } from '@/components/Button'
import { TextField } from '@/components/Fields'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import { useToast } from '@/components/Toast'
import { formatDate, humanise } from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'
import './reviews.css'

const PAGE_SIZE = 15
const FIELD_ORDER = ['reference', 'amount', 'date', 'counterparty', 'direction', 'currency']

function Scores({ scores }: { scores: Record<string, unknown> }) {
  return (
    <ul className="scores" aria-label="How closely each field agreed">
      {FIELD_ORDER.filter((f) => f in scores).map((field) => {
        const value = Number(scores[field])
        return (
          <li key={field} data-weak={value < 0.5}>
            <span>{humanise(field)}</span>
            <span className="scores__track" aria-hidden="true">
              <i style={{ width: `${Math.round(value * 100)}%` }} />
            </span>
            <span className="num">{Math.round(value * 100)}%</span>
          </li>
        )
      })}
    </ul>
  )
}

function ReviewItem({ match }: { match: MatchRow }) {
  const { can } = useSession()
  const toast = useToast()
  const invalidate = useInvalidate()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')

  const settled = async (message: string) => {
    toast(message)
    await invalidate('matches', 'summary', 'exceptions', 'exception-context')
  }
  const fail = (error: unknown) =>
    toast(error instanceof Error ? error.message : 'That did not work.', 'danger')
  const confirm = useMutation({
    mutationFn: () => api.confirmMatch(match.id),
    onSuccess: () => settled('Match confirmed'),
    onError: fail,
  })
  const reject = useMutation({
    mutationFn: () => api.rejectMatch(match.id, reason.trim()),
    onSuccess: () => settled('Match rejected'),
    onError: fail,
  })

  const reference = String(match.matched_fields.normalised_reference ?? '–')
  return (
    <li className="review">
      <div className="review__head">
        <div>
          <p className="review__ref num">{reference}</p>
          <p className="panel__note">
            {humanise(match.match_type)} match on {formatDate(match.matched_on_date)}
          </p>
        </div>
        <p className="review__conf num" aria-label={`Confidence ${match.confidence}`}>
          {Math.round(Number(match.confidence) * 100)}
          <span>% sure</span>
        </p>
      </div>
      <Scores scores={match.field_scores} />
      <p className="review__why">{match.rationale}</p>
      {can('ANALYST') && (
        <div className="review__actions">
          {!rejecting ? (
            <>
              <Button
                variant="primary"
                size="small"
                busy={confirm.isPending}
                onClick={() => confirm.mutate()}
              >
                These are the same payment
              </Button>
              <Button size="small" onClick={() => setRejecting(true)}>
                They are different
              </Button>
            </>
          ) : (
            <form
              className="review__reject"
              onSubmit={(event) => {
                event.preventDefault()
                reject.mutate()
              }}
            >
              <TextField
                label="Why are they different?"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                maxLength={500}
                hint="The pair is never proposed again."
              />
              <Button type="submit" variant="danger" size="small" busy={reject.isPending}>
                Reject the match
              </Button>
              <Button variant="quiet" size="small" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </form>
          )}
        </div>
      )}
    </li>
  )
}

export function ReviewsPage() {
  const url = useUrlState(PAGE_SIZE)
  const query = useMatches('PENDING_REVIEW', url.offset, PAGE_SIZE)
  const page = query.data
  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Match review</h1>
          <p className="page__lede">
            Pairs the engine thinks are the same payment but is not sure enough to settle alone.
            Weak fields are marked in red; the call is yours.
          </p>
        </div>
      </header>
      {query.isPending && <Loading rows={6} label="Loading pairs for review" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState title="Nothing waiting" hint="Every proposed match has been decided." />
      )}
      {page && page.items.length > 0 && (
        <>
          <ul className="reviews">
            {page.items.map((m) => (
              <ReviewItem key={m.id} match={m} />
            ))}
          </ul>
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
