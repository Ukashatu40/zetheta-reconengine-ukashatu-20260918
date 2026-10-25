import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

interface ToastItem {
  id: number
  text: string
  tone: 'default' | 'danger'
}

const ToastContext = createContext<(text: string, tone?: 'default' | 'danger') => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const push = useCallback((text: string, tone: 'default' | 'danger' = 'default') => {
    const id = Date.now() + Math.random()
    setItems((current) => [...current, { id, text, tone }])
    setTimeout(() => setItems((current) => current.filter((t) => t.id !== id)), 5000)
  }, [])
  const value = useMemo(() => push, [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="toast" data-tone={t.tone}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
