import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { useInvalidate } from '@/api/queries'
import { Button } from '@/components/Button'
import { Dialog } from '@/components/Dialog'
import { SelectField, TextField } from '@/components/Fields'
import { useToast } from '@/components/Toast'

const BANKS = ['AXIS', 'HDFC', 'ICICI', 'INDB', 'KOTAK', 'SBI', 'YESB']

export function StartRunDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const invalidate = useInvalidate()
  const [bank, setBank] = useState('HDFC')
  const [mode, setMode] = useState<'FAST' | 'FULL'>('FULL')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const start = useMutation({
    // One key per submission: pressing the button twice cannot start two runs.
    mutationFn: (key: string) =>
      api.startRun(
        { bank_code: bank, mode, date_from: from || undefined, date_to: to || undefined },
        key,
      ),
    onSuccess: async (run) => {
      toast(`${run.bank_code} run ${run.status.toLowerCase()}.`)
      await invalidate('runs', 'summary', 'banks', 'exceptions', 'alerts', 'matches')
      onClose()
    },
    onError: (error) =>
      toast(error instanceof Error ? error.message : 'The run could not start.', 'danger'),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    start.mutate(crypto.randomUUID())
  }

  return (
    <Dialog open={open} title="Run a reconciliation" onClose={onClose}>
      <form className="dialog__body" style={{ padding: 0 }} onSubmit={submit}>
        <SelectField
          label="Bank"
          allLabel={null}
          value={bank}
          onChange={setBank}
          options={BANKS.map((b) => ({ value: b, label: b }))}
        />
        <SelectField
          label="How thorough"
          allLabel={null}
          value={mode}
          onChange={(v) => setMode(v as 'FAST' | 'FULL')}
          options={[
            { value: 'FULL', label: 'Full: exact, grouped, fuzzy and rule matching' },
            { value: 'FAST', label: 'Fast: exact matches only' },
          ]}
        />
        <TextField
          label="From (optional)"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
        />
        <TextField
          label="To (optional)"
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          hint="Leave both empty to reconcile everything not yet matched."
        />
        <div className="dialog__actions">
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={start.isPending}>
            Start the run
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
