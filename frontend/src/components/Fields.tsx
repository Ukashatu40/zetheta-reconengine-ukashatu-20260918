import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'

export function SelectField({
  label,
  options,
  value,
  onChange,
  allLabel = 'All',
  ...rest
}: {
  label: string
  options: readonly { value: string; label: string }[]
  value: string
  onChange: (value: string) => void
  allLabel?: string | null
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'onChange' | 'value'>) {
  const id = useId()
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      <select
        {...rest}
        id={id}
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {allLabel !== null && <option value="">{allLabel}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function TextField({
  label,
  hint,
  error,
  ...rest
}: {
  label: string
  hint?: ReactNode
  error?: string | null
} & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId()
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      <input {...rest} id={id} className="input" aria-invalid={error ? true : undefined} />
      {hint && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  )
}

export function TextArea({
  label,
  hint,
  value,
  onChange,
  required,
  maxLength,
}: {
  label: string
  hint?: ReactNode
  value: string
  onChange: (value: string) => void
  required?: boolean
  maxLength?: number
}) {
  const id = useId()
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      <textarea
        id={id}
        className="textarea"
        value={value}
        required={required}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}
