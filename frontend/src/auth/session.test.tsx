import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { setApiKey } from '@/api/client'
import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import { renderApp } from '@/test/render'
import { LockScreen } from './LockScreen'
import { useSession } from './session'

vi.mock('@/api/endpoints', () => ({ api: { me: vi.fn() } }))
const me = vi.mocked(api.me)

function Probe() {
  const { me: who, lockReason } = useSession()
  return who ? <p>signed in as {who.name}</p> : <LockScreen key={String(lockReason)} />
}

beforeEach(() => {
  me.mockReset()
  setApiKey(null)
  sessionStorage.clear()
  localStorage.clear()
})

describe('sign in', () => {
  it('opens the desk with a valid key and keeps the key out of storage by default', async () => {
    me.mockResolvedValue({ name: 'priya', role: 'ANALYST' })
    renderApp(<Probe />)
    await userEvent.type(screen.getByLabelText('API key'), 'rk_valid')
    await userEvent.click(screen.getByRole('button', { name: 'Open the desk' }))
    expect(await screen.findByText('signed in as priya')).toBeInTheDocument()
    expect(sessionStorage.length).toBe(0)
    expect(localStorage.length).toBe(0)
  })

  it('keeps the key in this tab only when asked', async () => {
    me.mockResolvedValue({ name: 'priya', role: 'ANALYST' })
    renderApp(<Probe />)
    await userEvent.type(screen.getByLabelText('API key'), 'rk_valid')
    await userEvent.click(screen.getByLabelText(/Keep the key for this tab/))
    await userEvent.click(screen.getByRole('button', { name: 'Open the desk' }))
    await screen.findByText('signed in as priya')
    expect(sessionStorage.getItem('recon.tab-key')).toBe('rk_valid')
    expect(localStorage.length).toBe(0)
  })

  it('says plainly when the key is wrong', async () => {
    me.mockRejectedValue(new ApiError('missing or invalid API key', 401))
    renderApp(<Probe />)
    await userEvent.type(screen.getByLabelText('API key'), 'rk_bad')
    await userEvent.click(screen.getByRole('button', { name: 'Open the desk' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That key was not recognised.')
    expect(screen.queryByText(/signed in/)).toBeNull()
  })

  it('refuses an empty key without calling the engine', async () => {
    renderApp(<Probe />)
    await userEvent.click(screen.getByRole('button', { name: 'Open the desk' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter an API key.')
    expect(me).not.toHaveBeenCalled()
  })

  it('restores a key kept for this tab', async () => {
    sessionStorage.setItem('recon.tab-key', 'rk_kept')
    me.mockResolvedValue({ name: 'sam', role: 'VIEWER' })
    renderApp(<Probe />)
    expect(await screen.findByText('signed in as sam')).toBeInTheDocument()
  })

  it('forgets a kept key the engine no longer accepts', async () => {
    sessionStorage.setItem('recon.tab-key', 'rk_stale')
    me.mockRejectedValue(new ApiError('nope', 401))
    renderApp(<Probe />)
    await waitFor(() => expect(sessionStorage.getItem('recon.tab-key')).toBeNull())
    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
  })
})

describe('idle lock', () => {
  it('drops the key and the cache after the idle period, and says why', async () => {
    me.mockResolvedValue({ name: 'priya', role: 'ANALYST' })
    const { client } = renderApp(<Probe />, { idleMs: 50 })
    client.setQueryData(['summary'], { total_transactions: 1 })
    await userEvent.type(screen.getByLabelText('API key'), 'rk_valid')
    await userEvent.click(screen.getByRole('button', { name: 'Open the desk' }))
    await screen.findByText('signed in as priya')

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 120))
    })

    expect(await screen.findByLabelText('API key')).toBeInTheDocument()
    expect(screen.getByText(/locked itself after 15 minutes/)).toBeInTheDocument()
    expect(client.getQueryData(['summary'])).toBeUndefined()
  })
})
