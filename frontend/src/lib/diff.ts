export type DiffKind = 'added' | 'removed' | 'changed'

export interface DiffEntry {
  path: string
  kind: DiffKind
  before?: unknown
  after?: unknown
}

type Json = unknown

function flatten(value: Json, prefix: string, out: Map<string, Json>): void {
  if (value !== null && typeof value === 'object') {
    const entries = Array.isArray(value)
      ? value.map((v, i) => [String(i), v] as const)
      : Object.entries(value as Record<string, Json>)
    if (entries.length === 0) {
      out.set(prefix, Array.isArray(value) ? '[]' : '{}')
      return
    }
    for (const [key, child] of entries) flatten(child, prefix ? `${prefix}.${key}` : key, out)
    return
  }
  out.set(prefix, value)
}

/** The leaf-by-leaf difference between two JSON documents, sorted by path. */
export function diffJson(before: Json, after: Json): DiffEntry[] {
  const a = new Map<string, Json>()
  const b = new Map<string, Json>()
  if (before != null) flatten(before, '', a)
  if (after != null) flatten(after, '', b)
  const paths = [...new Set([...a.keys(), ...b.keys()])].sort()
  const entries: DiffEntry[] = []
  for (const path of paths) {
    const inA = a.has(path)
    const inB = b.has(path)
    if (inA && !inB) entries.push({ path, kind: 'removed', before: a.get(path) })
    else if (!inA && inB) entries.push({ path, kind: 'added', after: b.get(path) })
    else if (!Object.is(a.get(path), b.get(path))) {
      entries.push({ path, kind: 'changed', before: a.get(path), after: b.get(path) })
    }
  }
  return entries
}

export function displayValue(value: unknown): string {
  if (value === null) return 'null'
  if (value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}
