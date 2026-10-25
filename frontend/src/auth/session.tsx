import { useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, setApiKey, setUnauthorizedHandler } from '@/api/client'
import { api } from '@/api/endpoints'
import { atLeast, type Me, type Role } from '@/api/types'

/**
 * Where the API key lives, and for how long.
 *
 * The key is held in memory only. Reloading the tab asks for it again unless the person ticked
 * "keep for this tab", which uses sessionStorage (cleared when the tab closes, never shared
 * with other tabs, never localStorage). After a period of inactivity the key and every cached
 * response are discarded and the lock screen returns.
 */

export const IDLE_LOCK_MS = 15 * 60 * 1000
const TAB_KEY = 'recon.tab-key'

export type LockReason = 'idle' | 'rejected' | 'signed-out' | null

interface SessionValue {
  me: Me | null
  checking: boolean
  error: string | null
  lockReason: LockReason
  signIn: (key: string, keepForTab: boolean) => Promise<boolean>
  lock: (reason?: LockReason) => void
  can: (role: Role) => boolean
}

const SessionContext = createContext<SessionValue | null>(null)

function readTabKey(): string | null {
  try {
    return sessionStorage.getItem(TAB_KEY)
  } catch {
    return null
  }
}
function writeTabKey(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(TAB_KEY, key)
    else sessionStorage.removeItem(TAB_KEY)
  } catch {
    /* storage unavailable: the key simply stays in memory */
  }
}

export function SessionProvider({
  children,
  idleMs = IDLE_LOCK_MS,
}: {
  children: ReactNode
  idleMs?: number
}) {
  const queryClient = useQueryClient()
  const [me, setMe] = useState<Me | null>(null)
  const [checking, setChecking] = useState<boolean>(() => readTabKey() !== null)
  const [error, setError] = useState<string | null>(null)
  const [lockReason, setLockReason] = useState<LockReason>(null)
  const idleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const lock = useCallback(
    (reason: LockReason = 'signed-out') => {
      setApiKey(null)
      writeTabKey(null)
      queryClient.clear()
      setMe(null)
      setChecking(false)
      setLockReason(reason)
    },
    [queryClient],
  )

  const signIn = useCallback(async (key: string, keepForTab: boolean) => {
    const trimmed = key.trim()
    if (!trimmed) {
      setError('Enter an API key.')
      return false
    }
    setChecking(true)
    setError(null)
    setApiKey(trimmed)
    try {
      const identity = await api.me()
      if (keepForTab) writeTabKey(trimmed)
      setMe(identity)
      setLockReason(null)
      return true
    } catch (caught) {
      setApiKey(null)
      setError(
        caught instanceof ApiError && caught.status === 401
          ? 'That key was not recognised.'
          : caught instanceof ApiError
            ? caught.message
            : 'Sign-in failed.',
      )
      return false
    } finally {
      setChecking(false)
    }
  }, [])

  // A 401 on any request means the key was revoked or the user deactivated.
  useEffect(() => {
    setUnauthorizedHandler(() => lock('rejected'))
    return () => setUnauthorizedHandler(null)
  }, [lock])

  // Restore a key the person chose to keep for this tab.
  useEffect(() => {
    const stored = readTabKey()
    if (!stored) return
    let cancelled = false
    setApiKey(stored)
    api
      .me()
      .then((identity) => {
        if (!cancelled) setMe(identity)
      })
      .catch(() => {
        setApiKey(null)
        writeTabKey(null)
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Idle lock: any input restarts the countdown.
  useEffect(() => {
    if (!me) return
    const restart = () => {
      clearTimeout(idleTimer.current)
      idleTimer.current = setTimeout(() => lock('idle'), idleMs)
    }
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const
    events.forEach((name) => window.addEventListener(name, restart, { passive: true }))
    restart()
    return () => {
      clearTimeout(idleTimer.current)
      events.forEach((name) => window.removeEventListener(name, restart))
    }
  }, [me, idleMs, lock])

  const value = useMemo<SessionValue>(
    () => ({
      me,
      checking,
      error,
      lockReason,
      signIn,
      lock,
      can: (role) => atLeast(me?.role, role),
    }),
    [me, checking, error, lockReason, signIn, lock],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession needs a SessionProvider')
  return value
}
