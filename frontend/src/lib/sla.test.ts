import { slaStatus } from './sla'

const NOW = Date.parse('2026-10-25T12:00:00Z')
const at = (minutes: number) => new Date(NOW + minutes * 60000).toISOString()

describe('slaStatus', () => {
  it('counts down while there is time', () => {
    expect(slaStatus(at(190), NOW)).toMatchObject({ state: 'ok', label: '3h 10m left' })
  })
  it('turns urgent in the last hour', () => {
    expect(slaStatus(at(25), NOW)).toMatchObject({ state: 'urgent', label: '25m left' })
  })
  it('shows how long ago it was missed', () => {
    expect(slaStatus(at(-90), NOW)).toMatchObject({ state: 'breached', label: '1h 30m over' })
    expect(slaStatus(at(-60 * 50), NOW).label).toBe('2d 2h over')
  })
  it('trusts a recorded breach even if the clocks disagree', () => {
    expect(slaStatus(at(10), NOW, { breachedAt: at(-1) }).state).toBe('breached')
  })
  it('stops the clock once resolved', () => {
    expect(slaStatus(at(-60), NOW, { resolvedAt: at(-120) })).toMatchObject({
      state: 'done',
      label: 'Met',
    })
    expect(slaStatus(at(-60), NOW, { resolvedAt: at(-10) }).label).toBe('Missed')
  })
})
