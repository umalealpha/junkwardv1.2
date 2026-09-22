import { apiFetch } from '@/lib/api'

/**
 * Read EVERY page of a paginated list endpoint, not just the first.
 *
 * Written 2026-09-16 for the AML registers. The pages originally asked for
 * `?page_size=500`, which reads as "give me everything" and is not — the server
 * caps page_size at 100 (core/pagination.py). Nothing errors; the list just
 * stops at row 100 and the count beside it says 100 for ever. A compliance
 * register that quietly truncates is worse than one that is empty, because the
 * empty one is obviously wrong.
 *
 * Bounded at 50 pages so a paging bug cannot spin forever.
 */
export async function fetchAllPages<T>(path: string, pageSize = 100): Promise<T[]> {
  const sep = path.includes('?') ? '&' : '?'
  let url: string | null = `${path}${sep}page_size=${pageSize}`
  const rows: T[] = []

  for (let guard = 0; url && guard < 50; guard++) {
    const res: any = await apiFetch<any>(url)
    if (Array.isArray(res)) return res as T[]      // endpoint isn't paginated
    rows.push(...(res?.results ?? []))
    const nextUrl: string | null = res?.next ?? null
    // `next` comes back absolute; apiFetch wants the path it was given.
    url = nextUrl ? nextUrl.replace(/^.*\/api\/v1/, '') : null
  }
  return rows
}
