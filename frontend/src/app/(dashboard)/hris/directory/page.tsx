'use client'

/**
 * /hris/directory — native People Directory.
 *
 * Replaces the Graphiter iframe "Open my profile" link with an Omni
 * surface: search, filter by department/company, table of every
 * employee with role + tenure. Data source: GET /hris/api/employees/
 * (which already enforces the 5-person whitelist server-side).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Search, Users as UsersIcon, Building2, Pencil, ArrowLeftRight } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { fetchHrisEmployees, type HrisEmployee } from '../_shared'

function yearsBetween(iso: string, now = new Date()): number {
  if (!iso) return 0
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 0
  return (now.getTime() - d.getTime()) / (365.25 * 86400000)
}

export default function HrisDirectoryPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [dept, setDept] = useState<string>('All')

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    const load = () => {
      setLoading(true)
      fetchHrisEmployees()
        .then(r => setEmployees(r.employees || []))
        .finally(() => setLoading(false))
    }
    load()
    // Bug 07c1c74a: refetch when the entity is switched in the topbar so the
    // People list updates automatically — no manual page reload needed.
    window.addEventListener('alpha-company-changed', load)
    return () => window.removeEventListener('alpha-company-changed', load)
  }, [allowed])

  const departments = useMemo(() => {
    const s = new Set<string>()
    employees.forEach(e => { if (e.dp) s.add(e.dp) })
    return ['All', ...Array.from(s).sort()]
  }, [employees])

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase()
    return employees.filter(e => {
      if (dept !== 'All' && e.dp !== dept) return false
      if (!term) return true
      return (
        e.nm.toLowerCase().includes(term) ||
        e.ps.toLowerCase().includes(term) ||
        (e.dp || '').toLowerCase().includes(term) ||
        (e.company || '').toLowerCase().includes(term)
      )
    })
  }, [employees, q, dept])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="People Directory" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Directory' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <div className="flex items-center justify-between gap-4">
          <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
            <ChevronLeft className="w-4 h-4" /> Back to HRIS
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/hris/transfers"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold"
              style={{ background: theme.oL, color: theme.orange }}
              title="Move an employee between entities (dual-approved)">
              <ArrowLeftRight className="w-4 h-4" /> Transfers
            </Link>
            <div className="text-sm" style={{ color: theme.t2 }}>
              {loading ? 'Loading…' : `${filtered.length} of ${employees.length}`}
            </div>
          </div>
        </div>

        <div className="rounded-2xl p-4 space-y-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: theme.t2 }} />
              <input
                type="text"
                value={q}
                onChange={e => setQ(e.target.value)}
                aria-label="Search people"
                placeholder="Search by name, role, department, company"
                className="w-full pl-10 pr-4 py-2.5 rounded-lg text-sm outline-none"
                style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
              />
            </div>
            <select
              value={dept}
              onChange={e => setDept(e.target.value)}
              aria-label="Department"
              className="px-3 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
            >
              {departments.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Name</th>
                  <th className="py-2.5 pr-4 font-semibold">Role</th>
                  <th className="py-2.5 pr-4 font-semibold">Department</th>
                  <th className="py-2.5 pr-4 font-semibold">Company</th>
                  <th className="py-2.5 pr-4 font-semibold">Reports to</th>
                  <th className="py-2.5 pr-4 font-semibold">Location</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Tenure</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Edit</th>
                </tr>
              </thead>
              <tbody>
                {loading && [0, 1, 2, 3, 4, 5].map(i => (
                  <tr key={i} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    {[0, 1, 2, 3, 4, 5, 6, 7].map(j => (
                      <td key={j} className="py-3 pr-4">
                        <div className="h-3 w-24 rounded animate-pulse" style={{ background: theme.g100 }} />
                      </td>
                    ))}
                  </tr>
                ))}
                {!loading && filtered.length === 0 && (
                  <tr><td colSpan={8} className="py-8 text-center" style={{ color: theme.t2 }}>No matches.</td></tr>
                )}
                {!loading && filtered.map(e => {
                  const yrs = yearsBetween(e.hired)
                  return (
                    <tr key={e.id} className="border-t hover:bg-black/[0.02] transition-colors" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                               style={{ background: theme.oL, color: theme.orange }}>
                            {e.img || e.nm.split(' ').map(p => p[0]).slice(0, 2).join('').toUpperCase()}
                          </div>
                          <span className="font-medium" style={{ color: theme.text }}>{e.nm}</span>
                        </div>
                      </td>
                      <td className="py-3 pr-4" style={{ color: theme.text }}>{e.ps}</td>
                      <td className="py-3 pr-4" style={{ color: theme.t2 }}>{e.dp || '—'}</td>
                      <td className="py-3 pr-4">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium"
                              style={{ background: theme.oL, color: theme.orange }}>
                          <Building2 className="w-3 h-3" /> {e.company || 'ADIC'}
                        </span>
                      </td>
                      <td className="py-3 pr-4" style={{ color: theme.t2 }}>{e.mg || '—'}</td>
                      <td className="py-3 pr-4" style={{ color: theme.t2 }}>{e.loc || '—'}</td>
                      <td className="py-3 pr-4 text-right tabular-nums" style={{ color: theme.text }}>
                        {yrs > 0 ? `${yrs.toFixed(1)}y` : '—'}
                      </td>
                      <td className="py-3 pr-4 text-right">
                        {/* CFO 2026-06-11 (Oprah report): HR can now edit from the
                            directory. Routes to the dual-approved amendment flow
                            (maker-checker) — never a raw write. */}
                        {e.eid ? (
                          <Link href={`/hris/amendments?eid=${e.eid}`}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-semibold"
                            style={{ background: theme.oL, color: theme.orange }}
                            title="Request a change (dual-approved)">
                            <Pencil className="w-3 h-3" /> Edit
                          </Link>
                        ) : <span style={{ color: theme.t2 }}>—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-xs" style={{ color: theme.t2 }}>
          <UsersIcon className="w-3.5 h-3.5 inline mr-1" />
          Data source: HRISProfile (Django) — falls back to the 79-employee seed when HR has not yet onboarded a profile.
        </p>
      </main>
    </div>
  )
}
