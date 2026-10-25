import { useState, type FormEvent } from 'react'
import { Button } from '@/components/Button'
import { Logo } from '@/components/Logo'
import { useSession, type LockReason } from './session'
import './lock.css'

const REASONS: Record<Exclude<LockReason, null>, string> = {
  idle: 'The desk locked itself after 15 minutes without activity.',
  rejected: 'The engine no longer accepts that key. Ask an administrator for a new one.',
  'signed-out': 'The desk is locked.',
}

export function LockScreen() {
  const { signIn, checking, error, lockReason } = useSession()
  const [key, setKey] = useState('')
  const [keep, setKeep] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const ok = await signIn(key, keep)
    if (ok) setKey('')
  }

  return (
    <main className="lock">
      <form className="lock__form" onSubmit={submit} autoComplete="off">
        <Logo size={44} />
        <h1>Reconciliation desk</h1>
        <p className="lock__lede">
          Enter the API key issued to you. It stays in this tab&rsquo;s memory and is dropped when
          the desk locks.
        </p>
        {lockReason && <p className="lock__reason">{REASONS[lockReason]}</p>}
        <label className="field">
          <span>API key</span>
          <input
            className="input"
            type="password"
            name="recon-api-key"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'lock-error' : undefined}
            autoFocus
          />
        </label>
        {error && (
          <p id="lock-error" className="field-error" role="alert">
            {error}
          </p>
        )}
        <label className="lock__keep">
          <input
            type="checkbox"
            checked={keep}
            onChange={(event) => setKeep(event.target.checked)}
          />
          Keep the key for this tab until it closes
        </label>
        <Button type="submit" variant="primary" busy={checking}>
          Open the desk
        </Button>
        <p className="lock__foot">
          Transaction data is confidential. Anything you do here is written to the audit trail under
          your key&rsquo;s name.
        </p>
      </form>
    </main>
  )
}
