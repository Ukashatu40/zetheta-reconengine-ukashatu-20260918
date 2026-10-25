// The only module that touches the network. It owns the API key for the lifetime of a session,
// sends it as a header (never in a URL), and turns every failure into an ApiError that says what
// happened in words a person can act on.

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue>

export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | null
  readonly retryAfterSeconds: number | null

  constructor(
    message: string,
    status: number,
    extra: { requestId?: string | null; retryAfterSeconds?: number | null } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = extra.requestId ?? null
    this.retryAfterSeconds = extra.retryAfterSeconds ?? null
  }

  get isUnreachable(): boolean {
    return this.status === 0
  }
}

let apiKey: string | null = null
let unauthorizedHandler: (() => void) | null = null

export function setApiKey(key: string | null): void {
  apiKey = key
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export function buildUrl(path: string, query?: QueryParams): string {
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== '') params.set(name, String(value))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

interface ProblemBody {
  detail?: unknown
  title?: string
  request_id?: string
  errors?: { loc?: unknown[]; msg?: string }[]
}

function describe(status: number, body: ProblemBody | null): string {
  if (body?.errors?.length) {
    return body.errors
      .map(
        (e) => `${(e.loc ?? []).filter((p) => p !== 'body' && p !== 'query').join('.')}: ${e.msg}`,
      )
      .join('; ')
  }
  if (typeof body?.detail === 'string' && body.detail) return body.detail
  if (Array.isArray(body?.detail)) {
    return body.detail.map((d: { msg?: string }) => d.msg ?? 'invalid').join('; ')
  }
  switch (status) {
    case 401:
      return 'The key was rejected. Check it and try again.'
    case 403:
      return 'Your role does not allow this action.'
    case 404:
      return 'That record no longer exists.'
    case 429:
      return 'Too many requests. Wait a moment and retry.'
    default:
      return status >= 500
        ? 'The engine hit an error. Try again shortly.'
        : `Request failed (${status}).`
  }
}

export interface RequestOptions {
  query?: QueryParams
  json?: unknown
  form?: FormData
  headers?: Record<string, string>
  signal?: AbortSignal
  /** How to read a successful response. */
  as?: 'json' | 'text' | 'blob'
}

export async function request<T>(
  method: 'GET' | 'POST' | 'PUT',
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  if (apiKey) headers['X-API-Key'] = apiKey
  let body: BodyInit | undefined
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  } else if (options.form) {
    body = options.form // the browser sets the multipart boundary
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      signal: options.signal,
      credentials: 'omit',
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Cannot reach the engine. Check that it is running.', 0)
  }

  if (!response.ok) {
    const problem = await response
      .json()
      .then((body: unknown) => body as ProblemBody)
      .catch(() => null)
    const retry = Number(response.headers.get('Retry-After'))
    const error = new ApiError(describe(response.status, problem), response.status, {
      requestId: problem?.request_id ?? response.headers.get('X-Request-ID'),
      retryAfterSeconds: Number.isFinite(retry) && retry > 0 ? retry : null,
    })
    if (response.status === 401) unauthorizedHandler?.()
    throw error
  }

  if (options.as === 'text') return (await response.text()) as T
  if (options.as === 'blob') return (await response.blob()) as T
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const get = <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options)
export const post = <T>(path: string, options?: RequestOptions) => request<T>('POST', path, options)
export const put = <T>(path: string, options?: RequestOptions) => request<T>('PUT', path, options)
