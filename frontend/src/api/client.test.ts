import type { ApiError } from './client'
import { buildUrl, get, post, setApiKey, setUnauthorizedHandler } from './client'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  setApiKey(null)
  setUnauthorizedHandler(null)
})
afterEach(() => vi.unstubAllGlobals())

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

describe('buildUrl', () => {
  it('drops empty values and encodes the rest', () => {
    expect(buildUrl('/x', { a: 1, b: '', c: undefined, d: null, e: 'x y' })).toBe('/x?a=1&e=x+y')
    expect(buildUrl('/x')).toBe('/x')
  })
})

describe('request', () => {
  it('sends the key as a header and never in the URL', async () => {
    fetchMock.mockResolvedValue(json({ ok: true }))
    setApiKey('rk_secret')
    await get('/api/v1/me', { query: { limit: 5 } })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/v1/me?limit=5')
    expect(String(url)).not.toContain('rk_secret')
    expect(init.headers['X-API-Key']).toBe('rk_secret')
    expect(init).toMatchObject({
      credentials: 'omit',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
    })
  })

  it('sends nothing secret when there is no key', async () => {
    fetchMock.mockResolvedValue(json({}))
    await get('/health/live')
    expect(fetchMock.mock.calls[0]![1].headers['X-API-Key']).toBeUndefined()
  })

  it('posts JSON with a content type', async () => {
    fetchMock.mockResolvedValue(json({ id: 1 }))
    await post('/api/v1/x', { json: { a: 1 }, headers: { 'Idempotency-Key': 'k' } })
    const init = fetchMock.mock.calls[0]![1]
    expect(init.body).toBe('{"a":1}')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.headers['Idempotency-Key']).toBe('k')
  })

  it('lets the browser set the multipart boundary', async () => {
    fetchMock.mockResolvedValue(json({}))
    const form = new FormData()
    form.set('a', 'b')
    await post('/api/v1/files/upload', { form })
    const init = fetchMock.mock.calls[0]![1]
    expect(init.body).toBe(form)
    expect(init.headers['Content-Type']).toBeUndefined()
  })

  it('locks the session on a 401', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    fetchMock.mockResolvedValue(json({ detail: 'missing or invalid API key' }, { status: 401 }))
    await expect(get('/api/v1/me')).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledOnce()
  })

  it('reads FastAPI and problem+json errors into one sentence', async () => {
    fetchMock.mockResolvedValueOnce(
      json({ detail: 'exception is already RESOLVED' }, { status: 409 }),
    )
    await expect(get('/x')).rejects.toThrow('exception is already RESOLVED')

    fetchMock.mockResolvedValueOnce(
      json(
        {
          title: 'Unprocessable',
          request_id: 'abc',
          errors: [{ loc: ['body', 'username'], msg: 'Field required' }],
        },
        { status: 422 },
      ),
    )
    const error = (await get('/x').catch((e: unknown) => e)) as ApiError
    expect(error.message).toBe('username: Field required')
    expect(error.requestId).toBe('abc')
  })

  it('explains a rate limit and when to retry', async () => {
    fetchMock.mockResolvedValue(
      new Response('{}', { status: 429, headers: { 'Retry-After': '12' } }),
    )
    const error = (await get('/x').catch((e: unknown) => e)) as ApiError
    expect(error.retryAfterSeconds).toBe(12)
    expect(error.message).toMatch(/Too many requests/)
  })

  it('says so when the engine cannot be reached', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = (await get('/x').catch((e: unknown) => e)) as ApiError
    expect(error.isUnreachable).toBe(true)
    expect(error.message).toMatch(/Cannot reach the engine/)
  })

  it('does not hide a deliberate abort', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'))
    await expect(get('/x')).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('reads text responses for CSV', async () => {
    fetchMock.mockResolvedValue(new Response('a,b\n1,2\n'))
    expect(await get('/x', { as: 'text' })).toBe('a,b\n1,2\n')
  })
})
