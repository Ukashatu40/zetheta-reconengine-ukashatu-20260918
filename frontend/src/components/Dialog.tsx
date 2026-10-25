import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from './Button'

/** A modal built on the native <dialog>: focus is trapped and Escape closes it, for free. */
export function Dialog({
  open,
  title,
  onClose,
  children,
  wide = false,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      className={`dialog${wide ? ' dialog--wide' : ''}`}
      aria-label={title}
      onClose={onClose}
      onClick={(event) => event.target === ref.current && onClose()}
    >
      {open && (
        <div className="dialog__body">
          <header className="dialog__head">
            <h2 className="panel__title">{title}</h2>
            <Button variant="quiet" size="small" onClick={onClose} aria-label="Close">
              Close
            </Button>
          </header>
          {children}
        </div>
      )}
    </dialog>
  )
}
