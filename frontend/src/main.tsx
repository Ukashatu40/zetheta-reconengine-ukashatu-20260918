import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { createQueryClient } from '@/api/queries'
import { SessionProvider } from '@/auth/session'
import { ToastProvider } from '@/components/Toast'
import { PrefsProvider } from '@/lib/prefs'
import { App } from './App'
import './styles/tokens.css'
import './styles/base.css'
import './styles/primitives.css'

const queryClient = createQueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <PrefsProvider>
        <ToastProvider>
          <SessionProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </SessionProvider>
        </ToastProvider>
      </PrefsProvider>
    </QueryClientProvider>
  </StrictMode>,
)
