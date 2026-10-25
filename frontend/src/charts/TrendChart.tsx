import { useId, useState } from 'react'
import './charts.css'

export interface TrendPoint {
  label: string
  /** null where there was nothing to measure that day; a line breaks there. */
  value: number | null
}

interface Props {
  points: TrendPoint[]
  kind: 'line' | 'bars'
  /** Domain maximum; defaults to the largest value. A rate chart passes 1. */
  max?: number
  min?: number
  format: (value: number) => string
  title: string
  caption: string
}

const W = 640
const H = 170
const PAD = { top: 12, right: 12, bottom: 26, left: 44 }

export function TrendChart({ points, kind, max, min = 0, format, title, caption }: Props) {
  const id = useId()
  const [active, setActive] = useState<number | null>(null)
  const values = points.map((p) => p.value).filter((v): v is number => v !== null)
  const top = max ?? Math.max(1, ...values)
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom
  const step = points.length > 1 ? innerW / (points.length - 1) : 0
  const x = (i: number) =>
    PAD.left + (kind === 'bars' ? (i + 0.5) * (innerW / points.length) : i * step)
  const y = (v: number) => PAD.top + innerH - ((v - min) / (top - min || 1)) * innerH

  // A line is drawn in runs, broken wherever a day has no value.
  const runs: { x: number; y: number }[][] = []
  points.forEach((p, i) => {
    if (p.value === null) {
      runs.push([])
      return
    }
    if (runs.length === 0) runs.push([])
    runs[runs.length - 1]!.push({ x: x(i), y: y(p.value) })
  })
  const gridValues = [min, min + (top - min) / 2, top]
  const slot = innerW / points.length

  const locate = (clientX: number, el: SVGSVGElement) => {
    const box = el.getBoundingClientRect()
    const px = ((clientX - box.left) / box.width) * W
    const index = Math.round(
      kind === 'bars' ? (px - PAD.left) / slot - 0.5 : (px - PAD.left) / (step || 1),
    )
    setActive(Math.max(0, Math.min(points.length - 1, index)))
  }
  const current = active === null ? null : points[active]

  return (
    <figure className="trend" aria-labelledby={`${id}-t`}>
      <figcaption id={`${id}-t`} className="trend__title">
        {title}
        <span>{caption}</span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="trend__svg"
        role="img"
        aria-label={`${title}. ${caption}`}
        onPointerMove={(e) => locate(e.clientX, e.currentTarget)}
        onPointerLeave={() => setActive(null)}
      >
        {gridValues.map((g) => (
          <g key={g}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(g)} y2={y(g)} className="trend__grid" />
            <text x={PAD.left - 8} y={y(g) + 4} textAnchor="end" className="trend__tick">
              {format(g)}
            </text>
          </g>
        ))}
        {points.map((p, i) => (
          <text key={p.label} x={x(i)} y={H - 6} textAnchor="middle" className="trend__tick">
            {p.label}
          </text>
        ))}
        {kind === 'bars' &&
          points.map((p, i) =>
            p.value === null || p.value === 0 ? null : (
              <rect
                key={p.label}
                x={x(i) - slot * 0.3}
                width={slot * 0.6}
                y={y(p.value)}
                height={y(min) - y(p.value)}
                className="trend__bar"
                data-active={active === i}
              />
            ),
          )}
        {kind === 'line' &&
          runs.map((run, i) =>
            run.length === 0 ? null : (
              <g key={i}>
                {run.length > 1 && (
                  <path
                    d={`M${run.map((p) => `${p.x},${p.y}`).join('L')}`}
                    className="trend__line"
                    fill="none"
                  />
                )}
                {run.map((p) => (
                  <circle key={p.x} cx={p.x} cy={p.y} r={3} className="trend__dot" />
                ))}
              </g>
            ),
          )}
        {active !== null && (
          <line
            x1={x(active)}
            x2={x(active)}
            y1={PAD.top}
            y2={PAD.top + innerH}
            className="trend__cursor"
          />
        )}
      </svg>
      <p className="trend__readout" aria-live="polite">
        {current
          ? `${current.label}: ${current.value === null ? 'no data' : format(current.value)}`
          : ' '}
      </p>
      <table className="sr-only">
        <caption>{title}</caption>
        <tbody>
          {points.map((p) => (
            <tr key={p.label}>
              <th scope="row">{p.label}</th>
              <td>{p.value === null ? 'no data' : format(p.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}
