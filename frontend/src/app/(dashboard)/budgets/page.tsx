'use client'

/**
 * /budgets — Budget Library (CFO directive 2026-06-27).
 * A home for each year's budget pack: headline figures (GWP / EBITDA / PAT) plus
 * the source files (the linked Excel model, the highlights deck, the reinsurance
 * calculator). Finance can add a pack + attach files; everyone can view/download.
 * Backend: /api/v1/budgets/packs/ (budgets/views.py).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getBudgetPacks, createBudgetPack, uploadBudgetPackFile, downloadBudgetPackFile,
  getToken, type BudgetPack, type BudgetPackList,
} from '@/lib/api'
import { Wallet, Download, Upload, Plus, Loader2, AlertTriangle, CheckCircle2, Sparkles } from 'lucide-react'

const FILE_KINDS = [
  { value: 'model', label: 'Financial model (xlsx)' },
  { value: 'highlights', label: 'Highlights (deck / doc)' },
  { value: 'calculator', label: 'Calculator / tool' },
  { value: 'other', label: 'Other' },
]
const mn = (v: number | null) => (v === null || v === undefined ? '—' : `P${v.toFixed(1)}m`)
const fmtSize = (n: number) => (!n ? '' : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

const BLANK = {
  fy_label: '', company: '', period_start: '', period_end: '', status: 'draft',
  scenario: '', gwp_target: '', ebitda: '', pat: '', notes: '', source: '',
}

export default function BudgetsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [data, setData] = useState<BudgetPackList | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ ...BLANK })
  const upRef = useRef<Record<string, HTMLInputElement | null>>({})
  const kindRef = useRef<Record<string, string>>({})

  const refresh = useCallback(() => {
    setLoading(true); setErr(null)
    getBudgetPacks().then(setData)
      .catch(e => setErr(e instanceof Error ? e.message : 'Could not load budgets'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    refresh()
  }, [refresh, router])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }
  const packs = data?.packs || []

  async function createPack() {
    if (!form.fy_label.trim()) { setErr('Enter a fiscal-year label, e.g. FY2026/27.'); return }
    setBusy(true); setMsg(null); setErr(null)
    try {
      const p = await createBudgetPack(form)
      setMsg(`Created budget pack ${p.fy_label}.`)
      setForm({ ...BLANK }); setShowForm(false); refresh()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not create pack') }
    finally { setBusy(false) }
  }

  async function uploadTo(packId: string) {
    const f = upRef.current[packId]?.files?.[0]
    if (!f) { setErr('Choose a file first.'); return }
    setBusy(true); setMsg(null); setErr(null)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('kind', kindRef.current[packId] || 'other')
      fd.append('label', f.name)
      await uploadBudgetPackFile(packId, fd)
      setMsg(`Attached ${f.name}.`)
      if (upRef.current[packId]) upRef.current[packId]!.value = ''
      refresh()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  const statusColor = (s: string) => s === 'approved' ? '#1F5132' : s === 'superseded' ? '#6B7280' : theme.orange

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Budgets" breadcrumbs={[{ label: 'Accounting' }, { label: 'Budget Library' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <p className="text-sm" style={{ color: theme.t2 }}>
            One home for each year&rsquo;s budget pack — headline figures + the source files.
          </p>
          <div className="flex items-center gap-2">
            <a href="/budgets/simulator"
               className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold"
               style={{ border: `1px solid ${theme.orange}`, color: theme.orange }}>
              <Sparkles className="w-4 h-4" /> FY27 Cockpit
            </a>
            <button onClick={() => setShowForm(s => !s)}
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold"
                    style={{ background: theme.orange, color: '#fff' }}>
              <Plus className="w-4 h-4" /> New budget pack
            </button>
          </div>
        </div>

        {msg && <div className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg" style={{ background: '#ECFDF5', color: '#1F5132' }}><CheckCircle2 className="w-4 h-4" />{msg}</div>}
        {err && <div className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg" style={{ background: '#FEF2F2', color: '#8E1F12' }}><AlertTriangle className="w-4 h-4" />{err}</div>}

        {showForm && (
          <div className="rounded-xl p-4 space-y-3" style={card}>
            <p className="font-semibold" style={{ color: theme.text }}>New budget pack</p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <input placeholder="FY label e.g. FY2026/27" value={form.fy_label} onChange={e => setForm(f => ({ ...f, fy_label: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
              <input placeholder="Entity code (blank = Group)" value={form.company} onChange={e => setForm(f => ({ ...f, company: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
              <select value={form.status} onChange={e => setForm(f => ({ ...f, status: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input}>
                <option value="draft">Draft</option><option value="approved">Approved (adopted plan)</option><option value="superseded">Superseded</option>
              </select>
              <label className="text-xs flex flex-col gap-1" style={{ color: theme.t2 }}>Period start
                <input type="date" value={form.period_start} onChange={e => setForm(f => ({ ...f, period_start: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} /></label>
              <label className="text-xs flex flex-col gap-1" style={{ color: theme.t2 }}>Period end
                <input type="date" value={form.period_end} onChange={e => setForm(f => ({ ...f, period_end: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} /></label>
              <input placeholder="Scenario e.g. Base case" value={form.scenario} onChange={e => setForm(f => ({ ...f, scenario: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
              <input placeholder="GWP target (P Mn)" value={form.gwp_target} onChange={e => setForm(f => ({ ...f, gwp_target: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
              <input placeholder="EBITDA (P Mn)" value={form.ebitda} onChange={e => setForm(f => ({ ...f, ebitda: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
              <input placeholder="PAT (P Mn)" value={form.pat} onChange={e => setForm(f => ({ ...f, pat: e.target.value }))} className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
            </div>
            <input placeholder="Source e.g. Kago Tshutlhedi" value={form.source} onChange={e => setForm(f => ({ ...f, source: e.target.value }))} className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={input} />
            <textarea placeholder="Headline summary / key assumptions" value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} rows={2} className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={input} />
            <button onClick={createPack} disabled={busy} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold" style={{ background: theme.orange, color: '#fff', opacity: busy ? 0.6 : 1 }}>
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Save pack
            </button>
          </div>
        )}

        {loading && <div className="flex items-center gap-2 text-sm" style={{ color: theme.t2 }}><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>}
        {!loading && packs.length === 0 && <p className="text-sm" style={{ color: theme.t2 }}>No budgets kept yet. Click &ldquo;New budget pack&rdquo; to add the first.</p>}

        {packs.map(p => (
          <div key={p.id} className="rounded-xl p-4 space-y-3" style={card}>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <Wallet className="w-5 h-5" style={{ color: theme.orange }} />
                <span className="font-bold text-base" style={{ color: theme.text }}>{p.fy_label}</span>
                <span className="text-sm" style={{ color: theme.t2 }}>· {p.company_name}{p.scenario ? ` · ${p.scenario}` : ''}</span>
              </div>
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded uppercase tracking-wide" style={{ background: `${statusColor(p.status)}22`, color: statusColor(p.status) }}>{p.status_label}</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              {[['GWP', p.gwp_target], ['EBITDA', p.ebitda], ['PAT', p.pat]].map(([lbl, v]) => (
                <div key={lbl as string} className="rounded-lg px-3 py-2" style={{ background: theme.g100 }}>
                  <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{lbl as string}</div>
                  <div className="text-lg font-bold font-mono-nums" style={{ color: theme.text }}>{mn(v as number | null)}</div>
                </div>
              ))}
            </div>
            {p.notes && <p className="text-sm whitespace-pre-line" style={{ color: theme.t2 }}>{p.notes}</p>}
            {(p.period_start || p.source) && <p className="text-xs" style={{ color: theme.t2 }}>{p.period_start} → {p.period_end}{p.source ? ` · prepared by ${p.source}` : ''}</p>}

            <div className="space-y-1">
              {p.files.map(f => (
                <button key={f.id} onClick={() => downloadBudgetPackFile(f.id, f.filename || f.label).catch(e => setErr(e instanceof Error ? e.message : 'Download failed'))}
                        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left hover:opacity-80" style={input}>
                  <Download className="w-4 h-4 flex-shrink-0" style={{ color: theme.orange }} />
                  <span className="truncate" style={{ color: theme.text }}>{f.label || f.filename}</span>
                  <span className="ml-auto text-[11px]" style={{ color: theme.t2 }}>{f.kind_label} · {fmtSize(f.size)}</span>
                </button>
              ))}
              {p.files.length === 0 && <p className="text-xs" style={{ color: theme.t2 }}>No files attached yet.</p>}
            </div>

            <div className="flex items-center gap-2 flex-wrap pt-1">
              <input type="file" ref={el => { upRef.current[p.id] = el }} className="text-xs" style={{ color: theme.t2 }} />
              <select defaultValue="other" onChange={e => { kindRef.current[p.id] = e.target.value }} className="px-2 py-1.5 rounded-lg text-xs outline-none" style={input}>
                {FILE_KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select>
              <button onClick={() => uploadTo(p.id)} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold" style={{ background: theme.orange, color: '#fff', opacity: busy ? 0.6 : 1 }}>
                <Upload className="w-3.5 h-3.5" /> Attach file
              </button>
            </div>
          </div>
        ))}
      </main>
    </div>
  )
}
