'use client'

/**
 * /cost-per-hour — Cost-per-Productive-Hour League (wow feature #9, 2026-07-22).
 * What each department COSTS (payroll gross) per Time Doctor productive hour —
 * cost and activity on one screen for the first time. SENSITIVE: server-gated
 * to CFO / EXCO / CEO / COO / HR. Scoped to the top-bar company.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { getCostPerHour, type CostPerHourResponse, type CostPerson } from '@/lib/api'
import { Loader2, Lock } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Book Antiqua, Palatino, Georgia, serif'

function money(s: string | null): string {
  if (s == null) return '—'
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function thisMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function CostPerHourPage() {
  const [month, setMonth] = useState(thisMonth())
  const [data, setData] = useState<CostPerHourResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPeople, setShowPeople] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setDenied(false)
    try {
      setData(await getCostPerHour(month))
    } catch (e: any) {
      if (e?.status === 403) setDenied(true)
      else setError(e?.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [month])

  useEffect(() => { load() }, [load])

  const people: CostPerson[] = data?.people || []

  return (
    <>
      <TopBar title="Cost-per-Hour League" />
      <div className="p-6 max-w-5xl mx-auto">
        <header className="mb-6">
          <h1 style={{ fontFamily: SERIF, color: NAVY }} className="text-2xl font-semibold tracking-tight">
            What does each hour cost us?
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Department payroll cost per productive hour tracked — cost and activity on one screen.
            <span className="inline-flex items-center gap-1 ml-2 text-amber-700"><Lock size={12} /> CFO / EXCO / CEO / COO / HR only</span>
          </p>
        </header>

        <div className="flex items-center gap-3 mb-6">
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm"
          />
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-slate-500 py-16 justify-center">
            <Loader2 className="animate-spin" size={18} /> Joining payroll to the clock…
          </div>
        )}
        {denied && !loading && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-amber-800 text-sm flex items-center gap-2">
            <Lock size={16} /> Restricted to CFO / EXCO / CEO / COO / HR.
          </div>
        )}
        {error && !loading && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-red-700 text-sm">{error}</div>
        )}

        {!loading && !denied && !error && data && (
          <>
            {data.error && (
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700 text-sm mb-4">{data.error}</div>
            )}
            {(!data.departments || data.departments.length === 0) ? (
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-slate-500 text-sm">
                No payroll + productivity data for {data.month} in this company.
              </div>
            ) : (
              <div className="rounded-2xl bg-white overflow-hidden mb-4" style={{ border: '1px solid #E2E8F0' }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr style={{ background: NAVY }} className="text-white text-left">
                      <th className="px-4 py-3 font-medium">Department</th>
                      <th className="px-4 py-3 font-medium text-right">Headcount</th>
                      <th className="px-4 py-3 font-medium text-right">Payroll cost</th>
                      <th className="px-4 py-3 font-medium text-right">Productive hrs</th>
                      <th className="px-4 py-3 font-medium text-right">Cost / hour</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.departments.map((d) => (
                      <tr key={d.department} className="border-t border-slate-100">
                        <td className="px-4 py-2.5 font-semibold" style={{ color: NAVY }}>{d.department}</td>
                        <td className="px-4 py-2.5 text-right text-slate-500">{d.headcount}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-700">P {money(d.total_cost_bwp)}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">{money(d.productive_hours)}</td>
                        <td className="px-4 py-2.5 text-right">
                          <span className="font-bold tabular-nums px-2 py-0.5 rounded" style={{ background: d.cost_per_productive_hour ? '#FEF3C7' : '#F1F5F9', color: d.cost_per_productive_hour ? '#92400E' : '#94A3B8' }}>
                            {d.cost_per_productive_hour ? `P ${money(d.cost_per_productive_hour)}` : 'no hours'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {people.length > 0 && (
              <div>
                <button onClick={() => setShowPeople((v) => !v)} className="text-sm font-medium mb-2" style={{ color: ORANGE }}>
                  {showPeople ? 'Hide' : 'Show'} per-person breakdown ({people.length})
                </button>
                {showPeople && (
                  <div className="rounded-2xl bg-white overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-slate-500 border-b border-slate-100">
                          <th className="px-4 py-2 font-medium">Name</th>
                          <th className="px-4 py-2 font-medium">Dept</th>
                          <th className="px-4 py-2 font-medium text-right">Cost</th>
                          <th className="px-4 py-2 font-medium text-right">Prod hrs</th>
                          <th className="px-4 py-2 font-medium text-right">Cost / hr</th>
                        </tr>
                      </thead>
                      <tbody>
                        {people.map((p, i) => (
                          <tr key={`${p.full_name}-${i}`} className="border-t border-slate-50">
                            <td className="px-4 py-2 text-slate-700">{p.full_name}</td>
                            <td className="px-4 py-2 text-slate-400">{p.department}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-slate-600">P {money(p.cost_bwp)}</td>
                            <td className="px-4 py-2 text-right tabular-nums text-slate-400">{money(p.productive_hours)}</td>
                            <td className="px-4 py-2 text-right tabular-nums font-semibold text-slate-800">{p.cost_per_hour ? `P ${money(p.cost_per_hour)}` : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
