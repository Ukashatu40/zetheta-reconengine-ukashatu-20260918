import { NavLink, Outlet } from 'react-router-dom'
import { useSummary } from '@/api/queries'
import { useSession } from '@/auth/session'
import { formatCount } from '@/lib/format'
import { usePrefs, type Theme } from '@/lib/prefs'
import { Button } from './Button'
import { Logo } from './Logo'
import './shell.css'

const THEMES: { value: Theme; label: string }[] = [
  { value: 'system', label: 'Auto' },
  { value: 'light', label: 'Clearing house' },
  { value: 'dark', label: 'Night desk' },
]

export function Shell() {
  const { me, lock } = useSession()
  const { theme, setTheme, maskAmounts, setMaskAmounts } = usePrefs()
  const summary = useSummary()
  const s = summary.data

  const items: { to: string; label: string; count?: number }[] = [
    { to: '/', label: 'Overview' },
    { to: '/exceptions', label: 'Exceptions', count: s?.unresolved_count },
    { to: '/reviews', label: 'Match review', count: s?.pending_review_count },
    { to: '/alerts', label: 'Alerts', count: s?.open_alert_count },
    { to: '/audit', label: 'Audit trail' },
    { to: '/files', label: 'Files' },
    { to: '/runs', label: 'Runs' },
  ]

  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <aside className="spine">
        <div className="spine__brand">
          <Logo />
          <span>
            Reconciliation
            <br />
            desk
          </span>
        </div>
        <nav aria-label="Main">
          <ul className="spine__nav">
            {items.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} end={item.to === '/'}>
                  <span>{item.label}</span>
                  {item.count !== undefined && item.count > 0 && (
                    <span className="spine__count num">{formatCount(item.count)}</span>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="spine__foot">
          <label className="field">
            <span>Appearance</span>
            <select
              className="select"
              value={theme}
              onChange={(event) => setTheme(event.target.value as Theme)}
            >
              {THEMES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="spine__check">
            <input
              type="checkbox"
              checked={maskAmounts}
              onChange={(event) => setMaskAmounts(event.target.checked)}
            />
            Hide amounts
          </label>
          <div className="spine__who">
            <strong>{me?.name}</strong>
            <span>{me?.role?.toLowerCase()}</span>
          </div>
          <Button size="small" onClick={() => lock('signed-out')}>
            Lock
          </Button>
        </div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
