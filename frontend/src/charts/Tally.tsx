import './charts.css'

/**
 * The match rate drawn as a tally. One hundred units, grouped in fives: four ticks and a strike.
 * A matched unit is a solid ink stroke; an unmatched one is left as a dashed red stroke, so the
 * gaps in the ledger are visible at a glance. The strokes draw in once, in sequence, on load.
 */

const BUNDLES = 20
const BUNDLE_W = 52
const TICK_GAP = 9
const HEIGHT = 56

export function matchedUnits(rate: number): number {
  return Math.max(0, Math.min(100, Math.round(rate * 100)))
}

export function Tally({ rate, label }: { rate: number; label?: string }) {
  const matched = matchedUnits(rate)
  const rows = 2
  const perRow = BUNDLES / rows
  const width = perRow * BUNDLE_W
  const height = rows * (HEIGHT + 22)

  const strokes: React.ReactElement[] = []
  for (let bundle = 0; bundle < BUNDLES; bundle++) {
    const col = bundle % perRow
    const row = Math.floor(bundle / perRow)
    const x0 = col * BUNDLE_W + 8
    const y0 = row * (HEIGHT + 22) + 6
    for (let slot = 0; slot < 5; slot++) {
      const unit = bundle * 5 + slot
      const done = unit < matched
      const order = unit
      const style = { '--i': order } as React.CSSProperties
      if (slot < 4) {
        const x = x0 + slot * TICK_GAP
        strokes.push(
          <line
            key={unit}
            x1={x}
            x2={x}
            y1={y0}
            y2={y0 + HEIGHT}
            className="tally__stroke"
            data-matched={done}
            pathLength={1}
            style={style}
          />,
        )
      } else {
        strokes.push(
          <line
            key={unit}
            x1={x0 - 5}
            x2={x0 + 3 * TICK_GAP + 5}
            y1={y0 + HEIGHT - 8}
            y2={y0 + 8}
            className="tally__stroke"
            data-matched={done}
            pathLength={1}
            style={style}
          />,
        )
      }
    }
  }

  return (
    <svg
      className="tally"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label ?? `${matched} of every 100 transactions matched`}
    >
      {strokes}
    </svg>
  )
}
