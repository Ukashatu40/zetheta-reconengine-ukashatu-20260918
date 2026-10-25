import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { createQueryClient } from '@/api/queries'
import { SessionProvider } from '@/auth/session'
import { ToastProvider } from '@/components/Toast'
import { PrefsProvider } from '@/lib/prefs'

/** Renders with every provider the app has, over an in-memory router. */
export function renderApp(ui: ReactElement, options: { route?: string; idleMs?: number } = {}) {
  const client = createQueryClient()
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0 } })
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <PrefsProvider>
          <ToastProvider>
            <SessionProvider idleMs={options.idleMs}>
              <MemoryRouter initialEntries={[options.route ?? '/']}>{ui}</MemoryRouter>
            </SessionProvider>
          </ToastProvider>
        </PrefsProvider>
      </QueryClientProvider>,
    ),
  }
}
