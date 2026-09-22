/**
 * Pure search for the HRIS Amendments employee picker (CFO file 2 Medium,
 * 18-Sep-2026). Matches name, email or employee number. Never picks for the
 * user: a typed name only filters; the choice is always an explicit click or
 * Enter on one row, so two people with the same name cannot be confused.
 */
export type SearchableEmployee = { eid?: string; nm: string; email?: string; en?: string }

const norm = (s: string | undefined) => (s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').trim()

export function searchEmployees<T extends SearchableEmployee>(list: T[], query: string, limit = 50): T[] {
  const q = norm(query)
  const rows = list.filter(e => e.eid)
  if (!q) return rows.slice(0, limit)
  const terms = q.split(/\s+/)
  const scored: { e: T; score: number }[] = []
  for (const e of rows) {
    const name = norm(e.nm), email = norm(e.email), en = norm(e.en)
    const hay = `${name} ${email} ${en}`
    if (!terms.every(t => hay.includes(t))) continue
    let score = 3
    if (en && en === q) score = 0
    else if (email && email === q) score = 0
    else if (name === q) score = 1
    else if (name.startsWith(q) || email.startsWith(q) || en.startsWith(q)) score = 2
    scored.push({ e, score })
  }
  scored.sort((a, b) => a.score - b.score || a.e.nm.localeCompare(b.e.nm))
  return scored.slice(0, limit).map(s => s.e)
}

/** How many people in the list carry exactly this name (case/accents ignored). */
export function sameNameCount(list: SearchableEmployee[], name: string): number {
  const n = norm(name)
  return n ? list.filter(e => e.eid && norm(e.nm) === n).length : 0
}
