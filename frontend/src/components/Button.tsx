import type { ButtonHTMLAttributes } from 'react'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger' | 'quiet'
  size?: 'normal' | 'small'
  busy?: boolean
}

export function Button({
  variant = 'default',
  size = 'normal',
  busy = false,
  className = '',
  disabled,
  type = 'button',
  ...rest
}: Props) {
  const classes = [
    'button',
    variant !== 'default' && `button--${variant}`,
    size === 'small' && 'button--small',
    className,
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    />
  )
}
