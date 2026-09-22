/**
 * Which menu row and module the CURRENT ROUTE belongs to (M-PEOPLE, CFO
 * 19-Sep-2026). Pure, so the sidebar's rule is tested without rendering it.
 */
export interface NavLink { href: string; label: string; header?: boolean }
export interface NavSectionLike { title: string; items: NavLink[] }
export interface NavModuleLike { key: string; sections: NavSectionLike[] }

const base = (href: string) => href.split('?')[0]

/** The single most specific menu href that matches `pathname` ('' if none). */
export function deepestHref(hrefs: string[], pathname: string): string {
  let best = ''
  for (const raw of hrefs) {
    if (!raw || raw === '#') continue
    const h = base(raw)
    const matches = h === '/' ? pathname === '/' : (pathname === h || pathname.startsWith(h + '/'))
    if (matches && h.length > best.length) best = h
  }
  return best
}

/** The module holding that href — never merely the first prefix match. */
export function routeModule(modules: NavModuleLike[], deepest: string): string | null {
  if (!deepest) return null
  for (const m of modules) {
    for (const s of m.sections) {
      if (s.items.some(it => it.href && it.href !== '#' && base(it.href) === deepest)) return m.key
    }
  }
  return null
}

/** One lit row even when a page is listed twice: the first, in panel order. */
export function activeRowKey(sections: NavSectionLike[], deepest: string): string | null {
  for (const sec of sections) {
    for (const it of sec.items) {
      if (it.href && it.href !== '#' && base(it.href) === deepest) return `${sec.title}|${it.href}|${it.label}`
    }
  }
  return null
}
