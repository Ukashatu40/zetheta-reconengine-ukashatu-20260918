import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useFiles, useInvalidate } from '@/api/queries'
import type { FileFilters } from '@/api/types'
import { useSession } from '@/auth/session'
import { StatusChip } from '@/components/Badges'
import { Button } from '@/components/Button'
import { SelectField } from '@/components/Fields'
import { Hash } from '@/components/Hash'
import { Pager } from '@/components/Pager'
import { EmptyState, ErrorState, Loading } from '@/components/States'
import { useToast } from '@/components/Toast'
import { formatBytes, formatCount, formatDateTime, humanise } from '@/lib/format'
import { useUrlState } from '@/lib/useUrlState'
import { UploadDialog } from './UploadDialog'

const PAGE_SIZE = 25
const BANKS = ['AXIS', 'HDFC', 'ICICI', 'INDB', 'KOTAK', 'SBI', 'YESB']

export function FilesPage() {
  const url = useUrlState(PAGE_SIZE)
  const { can } = useSession()
  const toast = useToast()
  const invalidate = useInvalidate()
  const [uploading, setUploading] = useState(false)
  const filters: FileFilters = {
    bank_code: url.get('bank_code') || undefined,
    format_type: url.get('format_type') || undefined,
    source: url.get('source') || undefined,
    limit: PAGE_SIZE,
    offset: url.offset,
  }
  const query = useFiles(filters)
  const page = query.data

  const verify = useMutation({
    mutationFn: (id: string) => api.verifyFile(id),
    onSuccess: async (result) => {
      toast(
        result.status === 'OK'
          ? 'The stored file still matches its hash.'
          : 'The stored file no longer matches its hash.',
        result.status === 'OK' ? 'default' : 'danger',
      )
      await invalidate('files')
    },
    onError: (error) =>
      toast(error instanceof Error ? error.message : 'The check failed.', 'danger'),
  })

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Files</h1>
          <p className="page__lede">
            Every statement and ledger file the engine has read, with how many records it found and
            whether any could not be parsed.
          </p>
        </div>
        <div className="toolbar">
          <SelectField
            label="Bank"
            value={url.get('bank_code')}
            onChange={(v) => url.set({ bank_code: v })}
            options={BANKS.map((b) => ({ value: b, label: b }))}
          />
          <SelectField
            label="Format"
            value={url.get('format_type')}
            onChange={(v) => url.set({ format_type: v })}
            options={[
              { value: 'CSV', label: 'CSV' },
              { value: 'MT940', label: 'MT940' },
              { value: 'CAMT053', label: 'CAMT.053' },
            ]}
          />
          <SelectField
            label="Side"
            value={url.get('source')}
            onChange={(v) => url.set({ source: v })}
            options={[
              { value: 'EXTERNAL', label: 'Bank' },
              { value: 'INTERNAL', label: 'Ledger' },
            ]}
          />
          {can('ANALYST') && (
            <Button variant="primary" onClick={() => setUploading(true)}>
              Upload a file
            </Button>
          )}
        </div>
      </header>

      {query.isPending && <Loading rows={8} label="Loading files" />}
      {query.isError && <ErrorState error={query.error} retry={() => void query.refetch()} />}
      {page && page.items.length === 0 && (
        <EmptyState title="No files yet" hint="Upload a statement or ledger file to begin." />
      )}
      {page && page.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Ingested files</caption>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Bank</th>
                  <th scope="col">Format</th>
                  <th scope="col" className="right">
                    Records
                  </th>
                  <th scope="col" className="right">
                    Unreadable
                  </th>
                  <th scope="col">Status</th>
                  <th scope="col">SHA-256</th>
                  <th scope="col">Read at (IST)</th>
                  <th scope="col">Integrity</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((f) => (
                  <tr key={f.id}>
                    <td>
                      {f.original_filename}
                      <span className="panel__note num" style={{ display: 'block' }}>
                        {humanise(f.source)} side, {formatBytes(f.size_bytes)}
                      </span>
                    </td>
                    <td>{f.bank_code}</td>
                    <td>{f.format_type}</td>
                    <td className="right num">{formatCount(f.record_count)}</td>
                    <td
                      className="right num"
                      style={
                        f.parse_error_count > 0
                          ? { color: 'var(--danger)', fontWeight: 600 }
                          : undefined
                      }
                    >
                      {formatCount(f.parse_error_count)}
                    </td>
                    <td>
                      <StatusChip status={f.status} />
                    </td>
                    <td>
                      <Hash value={f.content_sha256} />
                    </td>
                    <td className="num">{formatDateTime(f.ingested_at)}</td>
                    <td>
                      <StatusChip status={f.integrity_status} />
                      {can('ANALYST') && (
                        <Button
                          size="small"
                          variant="quiet"
                          busy={verify.isPending && verify.variables === f.id}
                          onClick={() => verify.mutate(f.id)}
                        >
                          Check
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            total={page.total}
            limit={PAGE_SIZE}
            offset={url.offset}
            onChange={url.setOffset}
          />
        </>
      )}
      <UploadDialog open={uploading} onClose={() => setUploading(false)} />
    </div>
  )
}
