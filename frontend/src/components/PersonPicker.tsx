'use client'

/**
 * PersonPicker — searchable, disambiguating "assign to" control for the Tasks
 * area (CFO 2026-07-15 "task issues").
 *
 * Replaces the plain <select> that dumped every account (incl. System Admin)
 * into one long alphabetical list with no search. This:
 *   • lists ONLY people on the payroll (source: GET /tasks/assignees/),
 *   • filters as you type by name, job title or department,
 *   • shows title + department under each name so near-duplicates are obvious
 *     (e.g. Kago Tshutlhedi, Manager – Finance vs Pako Kago, Senior Associate),
 *   • is fully keyboard-driven (↑/↓ to move, Enter to pick, Esc to close).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { getTaskAssignees, type TaskAssignee } from '@/lib/api'
import { Search, X, Check } from 'lucide-react'

// One shared fetch per page — three pickers can mount at once, no need to hit
// the endpoint three times. Reset on failure so a later mount can retry.
let _cache: Promise<TaskAssignee[]> | null = null

// Retry with backoff: the SSO bearer can be briefly unavailable on first paint
// (MSAL token race), which 401s a single fetch. A few spaced retries let the
// token warm up so the picker self-heals instead of hanging on "Loading…".
async function fetchWithRetry(tries = 4): Promise<TaskAssignee[]> {
  let lastErr: unknown
  for (let i = 0; i < tries; i++) {
    try { return await getTaskAssignees() }
    catch (e) {
      lastErr = e
      if (i < tries - 1) await new Promise(r => setTimeout(r, 500 * (i + 1)))
    }
  }
  throw lastErr
}
function loadAssignees(): Promise<TaskAssignee[]> {
  if (!_cache) {
    _cache = fetchWithRetry().catch(e => { _cache = null; throw e })
  }
  return _cache
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

// Deterministic colour per department so the same team always reads the same
// tint — fast to scan, no photos needed. Falls back to a neutral grey.
function deptColour(dept: string): { bg: string; fg: string } {
  const d = (dept || '').trim()
  if (!d) return { bg: 'rgba(148,163,184,0.18)', fg: '#475569' }
  let h = 0
  for (let i = 0; i < d.length; i++) h = (h * 31 + d.charCodeAt(i)) % 360
  return { bg: `hsl(${h} 70% 92%)`, fg: `hsl(${h} 55% 32%)` }
}

// Bold the part of the name the viewer just typed, so the match is obvious.
function highlight(name: string, q: string): React.ReactNode {
  const needle = q.trim()
  if (!needle) return name
  const i = name.toLowerCase().indexOf(needle.toLowerCase())
  if (i < 0) return name
  return (
    <>
      {name.slice(0, i)}
      <strong className="font-semibold text-[#0D1B2A] dark:text-white">
        {name.slice(i, i + needle.length)}
      </strong>
      {name.slice(i + needle.length)}
    </>
  )
}

const FREQUENT_MAX = 4   // pin the viewer's most-assigned people, capped

type Section = { key: string; label: string; people: TaskAssignee[] }

// Group the (already filtered) people into the CFO's relevance order:
// Frequent → Your team · <dept> → Executives → Managers → Everyone else.
// Empty groups vanish. A frequent person is pinned once, not repeated below.
function buildSections(people: TaskAssignee[]): Section[] {
  const frequent = people
    .filter(p => (p.recent_count || 0) > 0)
    .sort((a, b) => (b.recent_count || 0) - (a.recent_count || 0))
    .slice(0, FREQUENT_MAX)
  const pinned = new Set(frequent.map(p => p.username))
  const rest = people.filter(p => !pinned.has(p.username))

  // The "Your team" label uses the department shared by the tier-1 people.
  const myTeamDept = rest.find(p => p.tier === 1)?.department || ''
  const byTier = (t: number) => rest.filter(p => (p.tier ?? 4) === t)

  const out: Section[] = []
  if (frequent.length) out.push({ key: 'freq', label: 'Frequent', people: frequent })
  const team = byTier(1)
  if (team.length) out.push({ key: 'team', label: myTeamDept ? `Your team · ${myTeamDept}` : 'Your team', people: team })
  const execs = byTier(2)
  if (execs.length) out.push({ key: 'exec', label: 'Executives', people: execs })
  const mgrs = byTier(3)
  if (mgrs.length) out.push({ key: 'mgr', label: 'Managers', people: mgrs })
  const rest4 = byTier(4)
  if (rest4.length) out.push({ key: 'rest', label: 'Everyone else', people: rest4 })
  return out
}

export function PersonPicker({
  value, onChange, placeholder = 'Search a name…', autoFocus = false,
}: {
  value: string
  onChange: (username: string) => void
  placeholder?: string
  autoFocus?: boolean
}) {
  const [people, setPeople]   = useState<TaskAssignee[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState(false)
  const [open, setOpen]       = useState(false)
  const [query, setQuery]     = useState('')
  const [active, setActive]   = useState(0)
  const boxRef   = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef  = useRef<HTMLDivElement>(null)

  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  function load() {
    setLoading(true); setLoadErr(false)
    loadAssignees()
      .then(p => { if (mounted.current) { setPeople(p); setLoading(false) } })
      .catch(() => { if (mounted.current) { setLoadErr(true); setLoading(false) } })
  }
  useEffect(() => { load() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  // Close when clicking away.
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const selected = useMemo(
    () => people.find(p => p.username === value) || null,
    [people, value],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return people
    return people.filter(p =>
      p.full_name.toLowerCase().includes(q) ||
      p.title.toLowerCase().includes(q) ||
      p.department.toLowerCase().includes(q) ||
      p.username.toLowerCase().includes(q),
    )
  }, [people, query])

  // Group into the relevance order (Frequent → Your team → Executives →
  // Managers → Everyone else), then flatten to the exact on-screen order so
  // keyboard nav and Enter land on the right person across group headers.
  const sections = useMemo(() => buildSections(filtered), [filtered])
  const ordered  = useMemo(() => sections.flatMap(s => s.people), [sections])

  useEffect(() => { setActive(0) }, [query, open])

  // Keep the highlighted row in view during keyboard nav.
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.querySelector<HTMLElement>(`[data-idx="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  function pick(p: TaskAssignee) {
    onChange(p.username)
    setOpen(false)
    setQuery('')
  }
  function clearSelection() {
    onChange('')
    setQuery('')
    setOpen(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }
  function onKey(e: React.KeyboardEvent) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return }
    if (e.key === 'ArrowDown')      { e.preventDefault(); setActive(a => Math.min(a + 1, ordered.length - 1)) }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter')     { e.preventDefault(); if (ordered[active]) pick(ordered[active]) }
    else if (e.key === 'Escape')    { e.preventDefault(); setOpen(false) }
  }

  // ── Selected, collapsed: show the person as a chip with a clear button ──
  if (selected && !open) {
    return (
      <div ref={boxRef} className="relative">
        <div className="w-full flex items-center gap-2 border rounded px-2.5 py-1.5 text-sm bg-background">
          <span className="w-6 h-6 rounded-full bg-[#F4A623]/15 text-[#B45309] grid place-items-center text-[10px] font-bold shrink-0">
            {initials(selected.full_name)}
          </span>
          <button
            type="button"
            onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 0) }}
            className="flex-1 min-w-0 text-left"
            title="Change person"
          >
            <span className="block truncate font-medium">{selected.full_name}</span>
            {(selected.title || selected.department) && (
              <span className="block truncate text-xs text-[#6B7280]">
                {[selected.title, selected.department].filter(Boolean).join(' · ')}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={clearSelection}
            className="text-[#9CA3AF] hover:text-[#374151] shrink-0"
            title="Clear"
            aria-label="Clear selected person"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    )
  }

  // ── Search + dropdown ──
  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <Search className="w-4 h-4 text-[#9CA3AF] absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          ref={inputRef}
          autoFocus={autoFocus}
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          placeholder={loading ? 'Loading people…' : (loadErr ? 'Couldn’t load — tap to retry' : placeholder)}
          onClick={() => { if (loadErr) load() }}
          className="w-full border rounded pl-8 pr-3 py-1.5 text-sm bg-background"
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
        />
      </div>
      {open && (
        <div
          ref={listRef}
          className="absolute z-[60] mt-1 w-full max-h-64 overflow-y-auto border rounded bg-white dark:bg-[#0F172A] shadow-lg"
        >
          {loading ? (
            <div className="px-3 py-2 text-xs text-[#9CA3AF]">Loading…</div>
          ) : loadErr ? (
            <button type="button" onClick={load}
                    className="w-full text-left px-3 py-2 text-xs text-[#B45309] hover:bg-[#FFF7ED]">
              Couldn’t load the people list. Tap to retry.
            </button>
          ) : ordered.length === 0 ? (
            <div className="px-3 py-2 text-xs text-[#9CA3AF]">No matching people.</div>
          ) : (() => {
            // One continuous index across every group so the highlighted row
            // (keyboard / mouse) always matches `ordered[active]`.
            let idx = -1
            return sections.map(sec => (
              <div key={sec.key}>
                <div className="sticky top-0 z-10 bg-[#F9FAFB] dark:bg-[#111827] px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-[#6B7280] dark:text-[#9CA3AF]">
                  {sec.label}
                </div>
                {sec.people.map(p => {
                  idx += 1
                  const i = idx
                  const c = deptColour(p.department)
                  return (
                    <button
                      type="button"
                      key={p.username}
                      data-idx={i}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => pick(p)}
                      className={`w-full flex items-center gap-2 px-3 py-1.5 text-left ${
                        i === active ? 'bg-[#FFF7ED]' : 'hover:bg-[#F9FAFB] dark:hover:bg-[#1E293B]'
                      }`}
                    >
                      <span
                        className="w-6 h-6 rounded-full grid place-items-center text-[10px] font-bold shrink-0"
                        style={{ backgroundColor: c.bg, color: c.fg }}
                      >
                        {initials(p.full_name)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {highlight(p.full_name, query)}
                        </span>
                        {(p.title || p.department) && (
                          <span className="block truncate text-xs text-[#6B7280]">
                            {[p.title, p.department].filter(Boolean).join(' · ')}
                          </span>
                        )}
                      </span>
                      {value === p.username && <Check className="w-4 h-4 text-[#F4A623] shrink-0" />}
                    </button>
                  )
                })}
              </div>
            ))
          })()}
        </div>
      )}
    </div>
  )
}
