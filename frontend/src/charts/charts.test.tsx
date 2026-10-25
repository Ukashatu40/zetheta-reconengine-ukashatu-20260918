import { render, screen } from '@testing-library/react'
import { matchedUnits, Tally } from './Tally'
import { TrendChart } from './TrendChart'

describe('Tally', () => {
  it('rounds the rate to whole units', () => {
    expect(matchedUnits(0.8743)).toBe(87)
    expect(matchedUnits(1.4)).toBe(100)
    expect(matchedUnits(-1)).toBe(0)
  })

  it('draws one hundred strokes, solid for matched and dashed for the rest', () => {
    const { container } = render(<Tally rate={0.87} />)
    const strokes = container.querySelectorAll('line.tally__stroke')
    expect(strokes).toHaveLength(100)
    expect(container.querySelectorAll('[data-matched="true"]')).toHaveLength(87)
    expect(container.querySelectorAll('[data-matched="false"]')).toHaveLength(13)
    expect(
      screen.getByRole('img', { name: '87 of every 100 transactions matched' }),
    ).toBeInTheDocument()
  })
})

describe('TrendChart', () => {
  const points = [
    { label: '1 Oct', value: 0.9 },
    { label: '2 Oct', value: null },
    { label: '3 Oct', value: 0.7 },
  ]
  it('offers every value as a table for screen readers, including gaps', () => {
    render(
      <TrendChart
        kind="line"
        title="Match rate"
        caption="3 days"
        points={points}
        max={1}
        format={(v) => `${v * 100}%`}
      />,
    )
    expect(screen.getByRole('row', { name: '2 Oct no data' })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: '3 Oct 70%' })).toBeInTheDocument()
  })
  it('does not join a line across a day with no data', () => {
    const { container } = render(
      <TrendChart kind="line" title="t" caption="c" points={points} max={1} format={String} />,
    )
    expect(container.querySelectorAll('path.trend__line')).toHaveLength(0) // two lone points, no segment
    expect(container.querySelectorAll('circle.trend__dot')).toHaveLength(2)
  })
})
