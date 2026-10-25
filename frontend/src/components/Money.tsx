import { formatMoney } from '@/lib/format'
import { usePrefs } from '@/lib/prefs'

/** A money figure that honours the "hide amounts" switch. */
export function Money({
  amount,
  currency = 'INR',
  decimals = true,
}: {
  amount: string | number | null | undefined
  currency?: string | null
  decimals?: boolean
}) {
  const { maskAmounts } = usePrefs()
  return (
    <span className="num">
      {formatMoney(amount, currency ?? 'INR', { masked: maskAmounts, decimals })}
    </span>
  )
}
