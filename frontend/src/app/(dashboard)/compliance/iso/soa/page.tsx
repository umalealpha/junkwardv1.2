'use client'

/**
 * /compliance/iso/soa — Statement of Applicability
 *
 * All 93 Annex A controls of ISO/IEC 27001:2022. Auditors expect:
 *   - every control listed
 *   - applicable Yes/No + justification
 *   - implementation status
 *   - owner + evidence reference
 *
 * Filter by domain / status / applicability; search by clause or title.
 * Inline edit on applicable / status / owner / justification / evidence_ref.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { ShieldCheck, Search, Download, RefreshCw } from 'lucide-react'
import { localYmd } from '@/lib/utils'

interface SoARow {
  id: number
  clause: string
  title: string
  domain: 'organisational' | 'people' | 'physical' | 'technological'
  domain_label: string
  control_type: string
  control_type_label: string
  description: string
  applicable: boolean
  justification: string
  status: 'implemented' | 'partial' | 'planned' | 'not_implemented' | 'excluded'
  status_label: string
  owner: string
  evidence_ref: string
  commandment_number: number | null
  last_reviewed_at: string | null
}

const STATUS_TONE: Record<SoARow['status'], string> = {
  implemented:     'bg-emerald-100 text-emerald-800 border-emerald-200',
  partial:         'bg-amber-100 text-amber-800 border-amber-200',
  planned:         'bg-sky-100 text-sky-800 border-sky-200',
  not_implemented: 'bg-red-100 text-red-800 border-red-200',
  excluded:        'bg-slate-200 text-slate-700 border-slate-300',
}

const DOMAIN_TONE: Record<SoARow['domain'], string> = {
  organisational: 'bg-blue-50 text-blue-700',
  people:         'bg-violet-50 text-violet-700',
  physical:       'bg-orange-50 text-orange-700',
  technological:  'bg-emerald-50 text-emerald-700',
}

export default function SoAPage() {
  const [rows, setRows] = useState<SoARow[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [domain, setDomain] = useState<'all' | SoARow['domain']>('all')
  const [status, setStatus] = useState<'all' | SoARow['status']>('all')
  const [seeding, setSeeding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const res = await apiFetch<any>('/iso/soa/?page_size=200')
      const list: SoARow[] = Array.isArray(res) ? res : (res?.results ?? [])
      setRows(list)
    } catch (e: any) {
      setErr(e?.message || 'Load failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const seed = async () => {
    setSeeding(true)
    try {
      await apiFetch('/iso/seed-soa/', { method: 'POST' })
      await load()
    } finally {
      setSeeding(false)
    }
  }

  const update = async (clause: string, patch: Partial<SoARow>) => {
    await apiFetch(`/iso/soa/${clause}/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    setRows((rs) => rs.map((r) => (r.clause === clause ? { ...r, ...patch } : r)))
  }

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return rows.filter((r) => {
      if (domain !== 'all' && r.domain !== domain) return false
      if (status !== 'all' && r.status !== status) return false
      if (!needle) return true
      return (
        r.clause.toLowerCase().includes(needle) ||
        r.title.toLowerCase().includes(needle) ||
        r.owner.toLowerCase().includes(needle)
      )
    })
  }, [rows, q, domain, status])

  const tally = useMemo(() => {
    const out = { implemented: 0, partial: 0, planned: 0, not_implemented: 0, excluded: 0 }
    rows.forEach((r) => { out[r.status] += 1 })
    return out
  }, [rows])

  const exportCsv = () => {
    const headers = ['clause', 'title', 'domain', 'control_type', 'applicable',
                     'justification', 'status', 'owner', 'evidence_ref', 'last_reviewed_at']
    const lines = [headers.join(',')]
    for (const r of filtered) {
      lines.push(headers.map((h) => {
        const v = (r as any)[h] ?? ''
        const s = typeof v === 'boolean' ? (v ? 'YES' : 'NO') : String(v)
        return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
      }).join(','))
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `soa-export-${localYmd(new Date())}.csv`
    a.click()
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <ShieldCheck size={28} style={{ color: '#0D1B2A' }} />
              <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                Statement of Applicability
              </h1>
            </div>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              All 93 controls from <strong>ISO/IEC 27001:2022 Annex A</strong>. Mark each control
              applicable or excluded, document the justification, and link the evidence the
              auditor will inspect.
            </p>
          </div>
          <div className="flex gap-2">
            <button onClick={exportCsv}
              className="flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
              <Download size={14} /> Export CSV
            </button>
            <button onClick={seed} disabled={seeding}
              className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-white"
              style={{ background: '#F4A623' }}>
              <RefreshCw size={14} className={seeding ? 'animate-spin' : ''} />
              {seeding ? 'Seeding…' : 'Seed / Refresh'}
            </button>
          </div>
        </div>

        <Card className="mb-4 border border-slate-200">
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <div className="relative flex-1 min-w-[260px]">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Search clause, title or owner"
                className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm" />
            </div>
            <select value={domain} onChange={(e) => setDomain(e.target.value as any)}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
              <option value="all">All domains</option>
              <option value="organisational">Organisational (A.5)</option>
              <option value="people">People (A.6)</option>
              <option value="physical">Physical (A.7)</option>
              <option value="technological">Technological (A.8)</option>
            </select>
            <select value={status} onChange={(e) => setStatus(e.target.value as any)}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">
              <option value="all">All statuses</option>
              <option value="implemented">Implemented</option>
              <option value="partial">Partial</option>
              <option value="planned">Planned</option>
              <option value="not_implemented">Not implemented</option>
              <option value="excluded">Excluded</option>
            </select>
            <div className="ml-auto flex gap-2 text-xs">
              <Tally label="Implemented" n={tally.implemented} tone="bg-emerald-500" />
              <Tally label="Partial"     n={tally.partial}     tone="bg-amber-500" />
              <Tally label="Planned"     n={tally.planned}     tone="bg-sky-500" />
              <Tally label="Not impl."   n={tally.not_implemented} tone="bg-red-500" />
              <Tally label="Excluded"    n={tally.excluded}    tone="bg-slate-500" />
            </div>
          </CardContent>
        </Card>

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2">Clause</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Domain</th>
                <th className="px-3 py-2">Applicable</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Owner</th>
                <th className="px-3 py-2">Justification / Evidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-500">
                  No controls — click <strong>Seed / Refresh</strong> to load all 93.
                </td></tr>
              ) : filtered.map((r) => (
                <tr key={r.clause} className="align-top hover:bg-slate-50">
                  <td className="px-3 py-2 font-mono text-xs">{r.clause}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium" style={{ color: '#0D1B2A' }}>{r.title}</div>
                    <div className="text-xs text-slate-500">{r.description}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] ${DOMAIN_TONE[r.domain]}`}>
                      {r.domain}
                    </span>
                    <div className="mt-1 text-[11px] text-slate-500">{r.control_type_label}</div>
                  </td>
                  <td className="px-3 py-2">
                    <select value={r.applicable ? 'yes' : 'no'}
                      onChange={(e) => update(r.clause, { applicable: e.target.value === 'yes' })}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-xs">
                      <option value="yes">Yes</option>
                      <option value="no">No (excluded)</option>
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <select value={r.status}
                      onChange={(e) => update(r.clause, { status: e.target.value as SoARow['status'] })}
                      className={`rounded border px-2 py-1 text-xs ${STATUS_TONE[r.status]}`}>
                      <option value="implemented">Implemented</option>
                      <option value="partial">Partial</option>
                      <option value="planned">Planned</option>
                      <option value="not_implemented">Not implemented</option>
                      <option value="excluded">Excluded</option>
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <input type="text" defaultValue={r.owner}
                      onBlur={(e) => e.target.value !== r.owner && update(r.clause, { owner: e.target.value })}
                      className="w-32 rounded border border-slate-200 bg-white px-2 py-1 text-xs" />
                  </td>
                  <td className="px-3 py-2 w-80">
                    <textarea defaultValue={r.justification}
                      onBlur={(e) => e.target.value !== r.justification && update(r.clause, { justification: e.target.value })}
                      placeholder={r.applicable ? 'Why applicable / how implemented' : 'Reason for exclusion'}
                      className="mb-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs"
                      rows={2} />
                    <input type="text" defaultValue={r.evidence_ref}
                      onBlur={(e) => e.target.value !== r.evidence_ref && update(r.clause, { evidence_ref: e.target.value })}
                      placeholder="Evidence reference / link"
                      className="w-full rounded border border-slate-200 bg-white px-2 py-1 text-[11px]" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  )
}

function Tally({ label, n, tone }: { label: string; n: number; tone: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1">
      <span className={`inline-block h-2 w-2 rounded-full ${tone}`} />
      <span className="text-slate-500">{label}</span>
      <span className="font-mono font-semibold" style={{ color: '#0D1B2A' }}>{n}</span>
    </div>
  )
}
