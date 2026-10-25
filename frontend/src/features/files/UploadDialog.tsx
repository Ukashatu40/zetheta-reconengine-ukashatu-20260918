import { useRef, useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useInvalidate } from '@/api/queries'
import { Button } from '@/components/Button'
import { Dialog } from '@/components/Dialog'
import { SelectField } from '@/components/Fields'
import { useToast } from '@/components/Toast'
import { formatCount } from '@/lib/format'

const BANKS = ['AXIS', 'HDFC', 'ICICI', 'INDB', 'KOTAK', 'SBI', 'YESB']
const EXTENSIONS: Record<string, string> = {
  CSV: '.csv,.txt',
  MT940: '.sta,.txt,.mt940',
  CAMT053: '.xml',
}
const MAX_BYTES = 50 * 1024 * 1024

export function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const invalidate = useInvalidate()
  const input = useRef<HTMLInputElement>(null)
  const [bank, setBank] = useState('HDFC')
  const [format, setFormat] = useState('CSV')
  const [source, setSource] = useState('EXTERNAL')
  const [reconcile, setReconcile] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const upload = useMutation({
    mutationFn: (form: FormData) => api.uploadFile(form),
    onSuccess: async (result) => {
      toast(
        `${result.original_filename}: ${formatCount(result.record_count)} records read, ${formatCount(result.exceptions_created)} exceptions raised.`,
      )
      await invalidate('files', 'summary', 'banks', 'exceptions', 'alerts', 'runs')
      onClose()
    },
    onError: (error) => setProblem(error instanceof Error ? error.message : 'The upload failed.'),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const file = input.current?.files?.[0]
    if (!file) return setProblem('Choose a file first.')
    if (file.size > MAX_BYTES) return setProblem('Files over 50 MB are refused. Split the file.')
    setProblem(null)
    const form = new FormData()
    form.set('file', file)
    form.set('bank_code', bank)
    form.set('format_type', format)
    form.set('source', source)
    form.set('auto_reconcile', String(reconcile))
    upload.mutate(form)
  }

  return (
    <Dialog open={open} title="Upload a file" onClose={onClose}>
      <form className="dialog__body" style={{ padding: 0 }} onSubmit={submit}>
        <SelectField
          label="Bank"
          allLabel={null}
          value={bank}
          onChange={setBank}
          options={BANKS.map((b) => ({ value: b, label: b }))}
        />
        <SelectField
          label="Format"
          allLabel={null}
          value={format}
          onChange={setFormat}
          options={[
            { value: 'CSV', label: 'CSV' },
            { value: 'MT940', label: 'SWIFT MT940' },
            { value: 'CAMT053', label: 'ISO 20022 CAMT.053' },
          ]}
        />
        <SelectField
          label="Whose records"
          allLabel={null}
          value={source}
          onChange={setSource}
          options={[
            { value: 'EXTERNAL', label: 'Bank statement' },
            { value: 'INTERNAL', label: 'Internal ledger' },
          ]}
        />
        <label className="field">
          <span>File</span>
          <input ref={input} className="input" type="file" accept={EXTENSIONS[format]} required />
          <span className="field-hint">
            Up to 50 MB. Identical content is refused as a duplicate.
          </span>
        </label>
        <label className="spine__check">
          <input
            type="checkbox"
            checked={reconcile}
            onChange={(e) => setReconcile(e.target.checked)}
          />
          Reconcile this bank straight after the upload
        </label>
        {problem && (
          <p className="field-error" role="alert">
            {problem}
          </p>
        )}
        <div className="dialog__actions">
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={upload.isPending}>
            Upload
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
