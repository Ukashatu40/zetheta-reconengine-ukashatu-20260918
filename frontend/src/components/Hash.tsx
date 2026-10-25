import { useState } from 'react'
import { shortHash } from '@/lib/format'

export function Hash({ value }: { value: string | null | undefined }) {
  const [copied, setCopied] = useState(false)
  if (!value) return <span>–</span>
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked: the full value is in the title */
    }
  }
  return (
    <span className="hash">
      <code title={value}>{shortHash(value)}</code>
      <button type="button" onClick={copy} aria-label={`Copy ${value}`}>
        {copied ? 'Copied' : 'Copy'}
      </button>
    </span>
  )
}
