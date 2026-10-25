/** Four ticks struck through: the tally mark the whole interface is built around. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <g stroke="currentColor" strokeWidth="2.6" strokeLinecap="round">
        <path d="M8 7v18M13 7v18M18 7v18M23 7v18" />
      </g>
      <path d="M4.5 22 27.5 10" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  )
}
