import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from '@/api/endpoints'
import type { ExceptionRow } from '@/api/types'
import { DiffView } from '@/features/audit/DiffView'
import { ExceptionsPage } from '@/features/exceptions/ExceptionsPage'
import { ReportDialog } from '@/features/reports/ReportDialog'
import { Money } from '@/components/Money'
import { renderApp } from '@/test/render'

let role = 'ANALYST'
vi.mock('@/auth/session', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useSession: () => ({
    me: { name: 'priya', role },
    can: (needed: string) =>
      ['VIEWER', 'ANALYST', 'ADMIN'].indexOf(role) >=
      ['VIEWER', 'ANALYST', 'ADMIN'].indexOf(needed),
  }),
}))
vi.mock('@/api/endpoints', () => ({
  api: {
    exceptions: vi.fn(),
    exception: vi.fn(),
    exceptionContext: vi.fn(),
    resolveException: vi.fn(),
    report: vi.fn(),
    reportCsv: vi.fn(),
  },
}))

const row = (overrides: Partial<ExceptionRow> = {}): ExceptionRow => ({
  id: '0147a622-9d7d-4db9-8c01-48c0b651a7ff',
  category: 'AMOUNT_MISMATCH',
  severity: 'CRITICAL',
  status: 'OPEN',
  assigned_tier: 2,
  assigned_to: 'queue:analyst',
  bank_code: 'HDFC',
  run_id: null,
  affected_records: {},
  financial_impact_amount: '4297883.7500',
  financial_impact_currency: 'INR',
  suggested_resolution: 'Compare the amounts.',
  rationale: 'Reference HDR0000029: amounts differ by 300 minor unit(s).',
  created_at: '2026-10-23T12:00:00Z',
  sla_deadline: '2026-10-23T14:00:00Z',
  sla_breached_at: null,
  resolved_at: null,
  resolved_by: null,
  resolution_details: null,
  ...overrides,
})

const txn = (source: 'INTERNAL' | 'EXTERNAL', amount: string) => ({
  id: crypto.randomUUID(),
  source,
  bank_code: 'HDFC',
  format_type: 'CSV',
  source_line_no: 210,
  txn_id: 'HDR0000029',
  bank_ref: null,
  normalised_reference: 'HDR0000029',
  amount,
  currency: 'INR',
  direction: 'CR',
  is_reversal: false,
  txn_date: '2026-10-08',
  settlement_date: null,
  txn_timestamp_utc: '2026-10-08T06:30:00Z',
  counterparty_name: 'PRIME TEXTILES LLP',
  counterparty_account_masked: null,
  narration: null,
  match_status: 'UNMATCHED',
  ingestion_file_id: crypto.randomUUID(),
})

beforeEach(() => {
  role = 'ANALYST'
  vi.mocked(api.exceptions).mockReset()
  vi.mocked(api.exception).mockResolvedValue({ ...row(), events: [] })
  vi.mocked(api.exceptionContext).mockResolvedValue({
    transactions: [txn('INTERNAL', '4297880.75'), txn('EXTERNAL', '4297883.75')],
    matches: [],
  })
  localStorage.clear()
})

describe('ExceptionsPage', () => {
  it('lists exceptions with their SLA and impact', async () => {
    vi.mocked(api.exceptions).mockResolvedValue({ items: [row()], total: 1, limit: 25, offset: 0 })
    renderApp(<ExceptionsPage />)
    const table = await screen.findByRole('table', { name: 'Exceptions' })
    expect(within(table).getByText('Amount mismatch')).toBeInTheDocument()
    expect(within(table).getByText('₹42,97,883.75')).toBeInTheDocument()
    expect(within(table).getByText('Critical')).toBeInTheDocument()
    expect(within(table).getByText('Analyst queue')).toBeInTheDocument()
  })

  it('asks the engine for exactly what the filters and address say', async () => {
    vi.mocked(api.exceptions).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
    renderApp(<ExceptionsPage />, {
      route: '/exceptions?category=FORMAT_ERROR&bank_code=HDFC&page=3',
    })
    await waitFor(() => expect(api.exceptions).toHaveBeenCalled())
    expect(vi.mocked(api.exceptions).mock.calls[0]![0]).toMatchObject({
      category: 'FORMAT_ERROR',
      bank_code: 'HDFC',
      limit: 25,
      offset: 50,
    })
    expect(await screen.findByText('Nothing matches those filters')).toBeInTheDocument()
  })

  it('shows the ledger and bank entry side by side and marks the field that differs', async () => {
    vi.mocked(api.exceptions).mockResolvedValue({ items: [row()], total: 1, limit: 25, offset: 0 })
    renderApp(<ExceptionsPage />)
    await userEvent.click(await screen.findByRole('button', { name: '0147a622' }))
    expect(await screen.findByRole('heading', { name: /^Ledger/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /^Bank/ })).toBeInTheDocument()
    const differing = document.querySelectorAll('[data-differs]')
    expect(differing).toHaveLength(2) // the same field, marked on both sides
    differing.forEach((el) => expect(el).toHaveTextContent('Amount'))
    expect(screen.getByText('These records are not matched to each other.')).toBeInTheDocument()
  })

  it('lets an analyst resolve, and sends what they wrote', async () => {
    vi.mocked(api.exceptions).mockResolvedValue({ items: [row()], total: 1, limit: 25, offset: 0 })
    vi.mocked(api.resolveException).mockResolvedValue(row({ status: 'RESOLVED' }))
    renderApp(<ExceptionsPage />)
    await userEvent.click(await screen.findByRole('button', { name: '0147a622' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    await userEvent.type(screen.getByLabelText(/What was done/), 'Corrected the ledger amount')
    await userEvent.click(screen.getByRole('button', { name: 'Save resolution' }))
    await waitFor(() =>
      expect(api.resolveException).toHaveBeenCalledWith('0147a622-9d7d-4db9-8c01-48c0b651a7ff', {
        resolution_details: 'Corrected the ledger amount',
        outcome: 'RESOLVED',
      }),
    )
  })

  it('offers a viewer no way to change anything', async () => {
    role = 'VIEWER'
    vi.mocked(api.exceptions).mockResolvedValue({ items: [row()], total: 1, limit: 25, offset: 0 })
    renderApp(<ExceptionsPage />)
    await userEvent.click(await screen.findByRole('button', { name: '0147a622' }))
    expect(await screen.findByText(/can read exceptions but not change them/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resolve' })).toBeNull()
  })

  it('warns an analyst that a senior-tier write-off needs an administrator', async () => {
    vi.mocked(api.exceptions).mockResolvedValue({
      items: [row({ assigned_tier: 4 })],
      total: 1,
      limit: 25,
      offset: 0,
    })
    renderApp(<ExceptionsPage />)
    await userEvent.click(await screen.findByRole('button', { name: '0147a622' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }))
    expect(screen.getByText(/needs an administrator/)).toBeInTheDocument()
  })

  it('explains a failed load and offers another try', async () => {
    vi.mocked(api.exceptions).mockRejectedValue(new Error('boom'))
    renderApp(<ExceptionsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('This could not be loaded')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('hide amounts', () => {
  it('replaces every money figure when the switch is on', () => {
    localStorage.setItem('recon.mask', '1')
    renderApp(<Money amount="4297883.75" />)
    expect(screen.queryByText(/42,97,883/)).toBeNull()
    expect(screen.getByText('••••••')).toBeInTheDocument()
  })
  it('shows them otherwise', () => {
    renderApp(<Money amount="4297883.75" />)
    expect(screen.getByText('₹42,97,883.75')).toBeInTheDocument()
  })
})

describe('DiffView', () => {
  it('shows leaves, not raw JSON, and collapses a long diff', async () => {
    const after = Object.fromEntries(
      Array.from({ length: 20 }, (_, i) => [`k${String(i).padStart(2, '0')}`, i]),
    )
    renderApp(<DiffView before={null} after={after} />)
    expect(screen.getAllByRole('row')).toHaveLength(13) // header + 12
    await userEvent.click(screen.getByRole('button', { name: 'Show all 20 changes' }))
    expect(screen.getAllByRole('row')).toHaveLength(21)
  })
  it('renders text that looks like markup as text', () => {
    renderApp(<DiffView before={{ note: 'a' }} after={{ note: '<img src=x onerror=alert(1)>' }} />)
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('ReportDialog', () => {
  beforeAll(() => {
    HTMLDialogElement.prototype.showModal ||= function showModal(this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
    HTMLDialogElement.prototype.close ||= function close(this: HTMLDialogElement) {
      this.removeAttribute('open')
    }
  })
  it('draws a report from JSON and never parses it as HTML', async () => {
    vi.mocked(api.report).mockResolvedValue({
      kind: 'audit-compliance',
      title: 'Audit <b>Compliance</b>',
      generated_at: '2026-10-25T12:00:00Z',
      parameters: {},
      summary: { transactions: 1087, match_rate: 0.8749 },
      tables: [{ name: 'Per bank', columns: ['Bank', 'Rate'], rows: [['HDFC', 0.8548]] }],
      verdict: 'FAIL',
      notes: [],
    })
    renderApp(<ReportDialog kind="audit-compliance" onClose={() => {}} />)
    expect(await screen.findByText('Audit <b>Compliance</b>')).toBeInTheDocument()
    expect(document.querySelector('.report b')).toBeNull()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'HDFC' })).toBeInTheDocument()
  })
})
