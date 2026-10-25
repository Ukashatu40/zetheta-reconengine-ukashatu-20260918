import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

/** Display preferences. None of this is sensitive, so it lives in localStorage. */

export type Theme = 'system' | 'light' | 'dark'

interface Prefs {
  theme: Theme
  setTheme: (theme: Theme) => void
  /** Hide every money figure, for screen sharing. */
  maskAmounts: boolean
  setMaskAmounts: (on: boolean) => void
}

const PrefsContext = createContext<Prefs | null>(null)

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const stored = read('recon.theme')
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  })
  const [maskAmounts, setMask] = useState<boolean>(() => read('recon.mask') === '1')

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') delete root.dataset.theme
    else root.dataset.theme = theme
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    write('recon.theme', next === 'system' ? null : next)
  }, [])
  const setMaskAmounts = useCallback((on: boolean) => {
    setMask(on)
    write('recon.mask', on ? '1' : null)
  }, [])

  const value = useMemo(
    () => ({ theme, setTheme, maskAmounts, setMaskAmounts }),
    [theme, setTheme, maskAmounts, setMaskAmounts],
  )
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>
}

export function usePrefs(): Prefs {
  const value = useContext(PrefsContext)
  if (!value) throw new Error('usePrefs needs a PrefsProvider')
  return value
}
