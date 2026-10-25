import { diffJson } from './diff'

describe('diffJson', () => {
  it('reports changed, added and removed leaves with their paths', () => {
    const before = { status: 'RUNNING', metrics: { a: 1, gone: true } }
    const after = { status: 'COMPLETED', metrics: { a: 1, extra: [1, 2] } }
    expect(diffJson(before, after)).toEqual([
      { path: 'metrics.extra.0', kind: 'added', after: 1 },
      { path: 'metrics.extra.1', kind: 'added', after: 2 },
      { path: 'metrics.gone', kind: 'removed', before: true },
      { path: 'status', kind: 'changed', before: 'RUNNING', after: 'COMPLETED' },
    ])
  })
  it('treats a missing side as empty', () => {
    expect(diffJson(null, { a: 1 })).toEqual([{ path: 'a', kind: 'added', after: 1 }])
    expect(diffJson({ a: 1 }, null)).toEqual([{ path: 'a', kind: 'removed', before: 1 }])
    expect(diffJson(null, null)).toEqual([])
  })
  it('sees no difference between equal documents', () => {
    expect(diffJson({ a: [1, { b: null }] }, { a: [1, { b: null }] })).toEqual([])
  })
})
