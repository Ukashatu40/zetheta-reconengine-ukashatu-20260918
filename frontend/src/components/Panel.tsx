import type { ReactNode } from 'react'

export function Panel({
  title,
  note,
  actions,
  children,
  id,
}: {
  title: string
  note?: ReactNode
  actions?: ReactNode
  children: ReactNode
  id?: string
}) {
  const headingId = id ? `${id}-title` : undefined
  return (
    <section className="panel" aria-labelledby={headingId}>
      <header className="panel__head">
        <div>
          <h2 className="panel__title" id={headingId}>
            {title}
          </h2>
          {note && <p className="panel__note">{note}</p>}
        </div>
        {actions}
      </header>
      {children}
    </section>
  )
}
