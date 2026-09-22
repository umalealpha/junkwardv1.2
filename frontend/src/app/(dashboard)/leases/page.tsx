'use client'

/**
 * /leases — IFRS 16 Lease cockpit (CFO / Kago 2026-07-15).
 *
 * Mirrors the Budget Library style: a register of leases with headline tiles,
 * and per-lease inputs you adjust (escalation, borrowing rate, term, payment)
 * that recompute the whole IFRS 16 picture live — right-of-use asset, lease
 * liability (current / non-current), the FY-by-FY summary and the full
 * amortisation schedule. Add a property or remove a lease in one click.
 * Numbers come from the server engine (leases/engine.py), verified to Kago's
 * "IFRS 16 ALPHA — Corrected FY26" workbook to the cent.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getLeases, createLease, updateLease, deleteLease, computeLease, getToken,
  type Lease, type LeaseCompute, type LeaseInputs,
} from '@/lib/api'
import { Building2, Plus, Trash2, ChevronDown, ChevronRight, RotateCcw, Save, Loader2 } from 'lucide-react'

const ORANGE = '#B04E00', NAVY = '#1D3270', GREEN = '#2E9E5B', RED = '#D14343'
const fmtP = (v: number | null | undefined) =>
  v == null ? '—' : 'P' + new Intl.NumberFormat('en-BW', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(Math.round(v))
const fmtP2 = (v: number) => 'P' + new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)

const toInputs = (l: Lease): LeaseInputs => ({
  monthly_payment: Number(l.monthly_payment), discount_rate_pct: Number(l.discount_rate_pct),
  term_months: l.term_months, escalation_pct: Number(l.escalation_pct),
  commencement_date: l.commencement_date, fye_month: l.fye_month,
  incentives: Number(l.incentives), payment_timing: l.payment_timing,
  initial_direct_costs: Number(l.initial_direct_costs), prepaid: Number(l.prepaid), dismantle: Number(l.dismantle),
})

export default function LeasesPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [leases, setLeases] = useState<Lease[]>([])
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  // silent=true → refresh after add/remove/save WITHOUT flipping the loading
  // state (which would unmount the open lease panel and flash "Loading…").
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try { setLeases(await getLeases()); setLoadErr(null) }
    catch (e) {
      const status = (e as { status?: number }).status
      if (status === 403) setDenied(true)
      else setLoadErr(e instanceof Error ? e.message : 'Could not load leases.')
    }
    finally { if (!silent) setLoading(false) }
  }, [])
  useEffect(() => {
    if (typeof window !== 'undefined' && !getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  // portfolio roll-up from each lease's summary
  const roll = useMemo(() => {
    const s = { rou: 0, current: 0, nonCurrent: 0, interest: 0, dep: 0 }
    for (const l of leases) {
      const u = l.summary
      if (!u) continue
      s.rou += u.nbv ?? 0
      s.current += u.current_portion ?? 0
      s.nonCurrent += u.non_current ?? 0
      s.interest += u.fy_interest ?? 0
      s.dep += u.fy_depreciation ?? 0
    }
    return s
  }, [leases])

  async function remove(l: Lease) {
    if (!confirm(`Remove the lease "${l.name}"? This deletes it from the register.`)) return
    setBusy(true)
    try { await deleteLease(l.id); if (openId === l.id) setOpenId(null); await load(true) }
    catch (e) { alert(e instanceof Error ? e.message : 'Could not remove') }
    finally { setBusy(false) }
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const tiles = [
    { l: 'Right-of-use asset (NBV)', v: roll.rou },
    { l: 'Lease liability — current', v: roll.current },
    { l: 'Lease liability — non-current', v: roll.nonCurrent },
    { l: 'This FY — interest + depreciation', v: roll.interest + roll.dep },
  ]

  if (denied) return <div><TopBar title="IFRS 16 Leases" /><div className="p-8 max-w-2xl mx-auto text-sm" style={{ color: theme.text }}>This section is available to Finance.</div></div>

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="IFRS 16 Leases" breadcrumbs={[{ label: 'Accounting' }, { label: 'IFRS 16 Leases' }]} />
      <section className="flex-1 overflow-y-auto p-5 space-y-5">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: ORANGE }}>Lease register</div>
            <h1 className="text-lg font-bold flex items-center gap-2" style={{ color: theme.text }}>
              <Building2 className="w-5 h-5" style={{ color: ORANGE }} /> Right-of-use assets &amp; lease liabilities
            </h1>
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>Add a property, remove a lease, or adjust the escalation / rate / term — the whole IFRS 16 picture recomputes live.</p>
          </div>
          <button onClick={() => setShowAdd(s => !s)} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold" style={{ background: ORANGE, color: '#fff' }}>
            <Plus className="w-4 h-4" /> Add property
          </button>
        </div>

        {/* portfolio tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {tiles.map(t => (
            <div key={t.l} className="rounded-2xl p-4" style={card}>
              <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{t.l}</div>
              <div className="text-2xl font-bold mt-1" style={{ color: theme.text }}>{fmtP(t.v)}</div>
            </div>
          ))}
        </div>

        {showAdd && <AddLease theme={theme} onClose={() => setShowAdd(false)} onSaved={async () => { setShowAdd(false); await load(true) }} />}

        {loadErr && <div className="rounded-2xl p-4 text-sm" style={{ ...card, color: RED }}>Couldn’t load the register: {loadErr}. Refresh to try again.</div>}

        {loading ? <p className="text-sm" style={{ color: theme.t2 }}>Loading…</p> :
          (leases.length === 0 && !loadErr) ? <div className="rounded-2xl p-6 text-sm" style={{ ...card, color: theme.t2 }}>No leases yet. Click “Add property”.</div> :
          <div className="space-y-3">
            {leases.map(l => (
              <div key={l.id} className="rounded-2xl overflow-hidden" style={card}>
                <div className="flex items-center gap-3 p-4 cursor-pointer" onClick={() => setOpenId(openId === l.id ? null : l.id)}>
                  {openId === l.id ? <ChevronDown className="w-4 h-4" style={{ color: theme.t2 }} /> : <ChevronRight className="w-4 h-4" style={{ color: theme.t2 }} />}
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold" style={{ color: theme.text }}>{l.name}</div>
                    <div className="text-[11px] truncate" style={{ color: theme.t2 }}>{l.property_ref || `${l.term_months}-mo · ${l.escalation_pct}% esc · ${l.discount_rate_pct}% rate`}</div>
                  </div>
                  <div className="hidden sm:flex gap-5 text-right">
                    <div><div className="text-[10px] uppercase" style={{ color: theme.t2 }}>ROU (NBV)</div><div className="font-mono-nums text-sm font-semibold" style={{ color: theme.text }}>{fmtP(l.summary?.nbv)}</div></div>
                    <div><div className="text-[10px] uppercase" style={{ color: theme.t2 }}>Liability</div><div className="font-mono-nums text-sm font-semibold" style={{ color: theme.text }}>{fmtP((l.summary?.current_portion ?? 0) + (l.summary?.non_current ?? 0))}</div></div>
                    <div><div className="text-[10px] uppercase" style={{ color: theme.t2 }}>FY dep+int</div><div className="font-mono-nums text-sm font-semibold" style={{ color: theme.text }}>{fmtP((l.summary?.fy_depreciation ?? 0) + (l.summary?.fy_interest ?? 0))}</div></div>
                  </div>
                  <button onClick={e => { e.stopPropagation(); remove(l) }} disabled={busy} title="Remove lease"
                    className="ml-2 p-2 rounded-lg" style={{ color: RED, border: `1px solid ${theme.cardBdr}` }}><Trash2 className="w-4 h-4" /></button>
                </div>
                {openId === l.id && <LeaseDetail lease={l} theme={theme} onSaved={() => load(true)} />}
              </div>
            ))}
          </div>}

        <p className="text-[11px] pb-4" style={{ color: theme.t2 }}>
          Engine verified to Kago’s “IFRS 16 ALPHA — Corrected FY26” workbook (BIH ICON: initial liability {fmtP2(4308084.59)}, ROU {fmtP2(374283.59)}, depreciation {fmtP2(6238.06)}/mo). Payments discounted in arrears; current portion = principal repaid in the next 12 months. This is a management tool — the posted journal stays a deliberate Finance step (Kago approves).
        </p>
      </section>
    </div>
  )
}

// ── per-lease detail: live-adjust inputs + FY summary + schedule ──
function LeaseDetail({ lease, theme, onSaved }: { lease: Lease; theme: any; onSaved: () => void }) {
  const [inp, setInp] = useState<LeaseInputs>(toInputs(lease))
  const [comp, setComp] = useState<LeaseCompute | null>(null)
  const [computing, setComputing] = useState(false)
  const [showSched, setShowSched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const seq = useRef(0)   // guards against out-of-order compute responses
  const dirty = JSON.stringify(inp) !== JSON.stringify(toInputs(lease))

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    setComputing(true)
    timer.current = setTimeout(async () => {
      const my = ++seq.current
      try { const r = await computeLease(inp); if (my === seq.current) setComp(r) }
      catch { /* keep last */ }
      finally { if (my === seq.current) setComputing(false) }
    }, 300)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [inp])

  const set = (k: keyof LeaseInputs, v: number) => { setInp(s => ({ ...s, [k]: v })); setSaved(false) }
  const card = { background: theme.g100, border: `1px solid ${theme.cardBdr}` }

  const SLIDERS: { key: keyof LeaseInputs; label: string; min: number; max: number; step: number; suffix?: string }[] = [
    { key: 'monthly_payment', label: 'Monthly payment', min: 0, max: 500000, step: 500 },
    { key: 'escalation_pct', label: 'Escalation % p.a.', min: 0, max: 15, step: 0.25, suffix: '%' },
    { key: 'discount_rate_pct', label: 'Borrowing rate % p.a.', min: 1, max: 20, step: 0.01, suffix: '%' },
    { key: 'term_months', label: 'Term (months)', min: 6, max: 240, step: 1, suffix: ' mo' },
  ]

  async function save() {
    setSaving(true)
    try {
      await updateLease(lease.id, {
        monthly_payment: String(inp.monthly_payment), escalation_pct: String(inp.escalation_pct),
        discount_rate_pct: String(inp.discount_rate_pct), term_months: inp.term_months,
      })
      setSaved(true); onSaved()
    } catch (e) { alert(e instanceof Error ? e.message : 'Could not save') }
    finally { setSaving(false) }
  }

  return (
    <div className="border-t p-4 space-y-4" style={{ borderColor: theme.cardBdr }}>
      {/* levers */}
      <div className="flex flex-wrap gap-x-6 gap-y-3">
        {SLIDERS.map(s => (
          <div key={s.key} className="flex flex-col gap-1" style={{ minWidth: 180 }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium" style={{ color: theme.t2 }}>{s.label}</span>
              <span className="text-[11px] font-bold font-mono-nums" style={{ color: ORANGE }}>
                {s.key === 'monthly_payment' ? fmtP(inp[s.key] as number) : `${inp[s.key]}${s.suffix || ''}`}
              </span>
            </div>
            <input type="range" min={s.min} max={Math.max(s.max, Number(inp[s.key]) || 0)} step={s.step} value={inp[s.key] as number}
              onChange={e => set(s.key, parseFloat(e.target.value))} style={{ accentColor: ORANGE }} />
          </div>
        ))}
        <div className="flex items-end gap-2">
          <button onClick={() => setInp(toInputs(lease))} disabled={!dirty} title="Reset"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-[11px] font-semibold" style={{ background: theme.g100, color: theme.t2, opacity: dirty ? 1 : 0.5 }}>
            <RotateCcw className="w-3.5 h-3.5" /> Reset
          </button>
          <button onClick={save} disabled={!dirty || saving}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-[11px] font-semibold" style={{ background: dirty ? ORANGE : theme.g100, color: dirty ? '#fff' : theme.t2 }}>
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} {saved && !dirty ? 'Saved' : 'Save changes'}
          </button>
        </div>
      </div>

      {/* headline recompute */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['Initial liability (PV)', comp?.initial_liability],
          ['ROU asset at cost', comp?.rou_cost],
          ['Depreciation / month', comp?.depreciation_per_month],
          ['Total interest (life)', comp?.total_interest],
        ].map(([l, v]) => (
          <div key={l as string} className="rounded-xl p-3" style={card}>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: theme.t2 }}>{l as string}</div>
            <div className="text-lg font-bold font-mono-nums mt-0.5" style={{ color: computing ? theme.t2 : theme.text }}>{v == null ? '…' : fmtP2(v as number)}</div>
          </div>
        ))}
      </div>

      {/* FY summary */}
      {comp && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 720 }}>
            <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
              {['FY (30 Jun)', 'Payments', 'Interest', 'Principal', 'Closing liability', 'Current', 'Non-current', 'Depreciation', 'ROU NBV'].map((h, i) =>
                <th key={h} className={i ? 'text-right pb-1 px-2' : 'text-left pb-1'}>{h}</th>)}
            </tr></thead>
            <tbody>
              {comp.fy_summary.map(f => (
                <tr key={f.fy} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                  <td className="py-1 font-semibold" style={{ color: theme.text }}>{f.fy}</td>
                  {[f.payments, f.interest, f.principal, f.closing_liability, f.current_portion, f.non_current, f.depreciation, f.nbv].map((v, i) =>
                    <td key={i} className="py-1 text-right px-2 font-mono-nums" style={{ color: theme.t2 }}>{fmtP(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* schedule (collapsible) */}
      {comp && (
        <div>
          <button onClick={() => setShowSched(s => !s)} className="text-[11px] font-semibold inline-flex items-center gap-1" style={{ color: ORANGE }}>
            {showSched ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />} {showSched ? 'Hide' : 'Show'} full monthly schedule ({comp.schedule.length} months)
          </button>
          {showSched && (
            <div className="overflow-x-auto mt-2 max-h-80 overflow-y-auto">
              <table className="w-full text-[11px]" style={{ minWidth: 640 }}>
                <thead><tr className="text-[10px] uppercase sticky top-0" style={{ color: theme.t2, background: theme.card }}>
                  {['#', 'Month', 'Payment', 'Interest', 'Principal', 'Closing', 'ROU carrying'].map((h, i) =>
                    <th key={h} className={i ? 'text-right pb-1 px-2' : 'text-left pb-1'}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {comp.schedule.map(r => (
                    <tr key={r.m} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                      <td className="py-0.5" style={{ color: theme.t2 }}>{r.m}</td>
                      <td className="py-0.5" style={{ color: theme.t2 }}>{r.date.slice(0, 7)}</td>
                      {[r.payment, r.interest, r.principal, r.closing, r.carrying].map((v, i) =>
                        <td key={i} className="py-0.5 text-right px-2 font-mono-nums" style={{ color: theme.t2 }}>{fmtP(v)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── add-property form ──
function AddLease({ theme, onClose, onSaved }: { theme: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    name: '', property_ref: '', commencement_date: '', term_months: '60',
    monthly_payment: '', escalation_pct: '0', discount_rate_pct: '7', incentives: '0',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const upd = (k: string, v: string) => setF(s => ({ ...s, [k]: v }))
  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }

  async function submit() {
    if (!f.name.trim() || !f.commencement_date || !f.monthly_payment) { setErr('Name, commencement date and monthly payment are required.'); return }
    if (!(Number(f.term_months) >= 1)) { setErr('Term must be at least 1 month.'); return }
    if (!(Number(f.monthly_payment) > 0)) { setErr('Monthly payment must be greater than zero.'); return }
    setBusy(true); setErr(null)
    try {
      await createLease({
        name: f.name.trim(), property_ref: f.property_ref.trim(), commencement_date: f.commencement_date,
        term_months: Number(f.term_months) as unknown as number,
        monthly_payment: f.monthly_payment, escalation_pct: f.escalation_pct,
        discount_rate_pct: f.discount_rate_pct, incentives: f.incentives, fye_month: 6 as unknown as number,
      } as never)
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Could not add') }
    finally { setBusy(false) }
  }

  const inputCl = 'rounded-lg px-3 py-2 text-sm outline-none'
  const inputSt = { background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }
  return (
    <div className="rounded-2xl p-5 space-y-3" style={card}>
      <div className="font-semibold" style={{ color: theme.text }}>New property lease</div>
      <div className="grid sm:grid-cols-3 gap-3">
        <input className={inputCl + ' sm:col-span-2'} style={inputSt} placeholder="Name (e.g. Warehouse — Broadhurst)" value={f.name} onChange={e => upd('name', e.target.value)} />
        <input className={inputCl} style={inputSt} placeholder="Lessor / ref (optional)" value={f.property_ref} onChange={e => upd('property_ref', e.target.value)} />
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Commencement<input type="date" className={inputCl} style={inputSt} value={f.commencement_date} onChange={e => upd('commencement_date', e.target.value)} /></label>
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Term (months)<input type="number" className={inputCl} style={inputSt} value={f.term_months} onChange={e => upd('term_months', e.target.value)} /></label>
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Monthly payment (excl VAT)<input inputMode="decimal" className={inputCl} style={inputSt} value={f.monthly_payment} onChange={e => upd('monthly_payment', e.target.value.replace(/[^0-9.]/g, ''))} /></label>
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Escalation % p.a.<input inputMode="decimal" className={inputCl} style={inputSt} value={f.escalation_pct} onChange={e => upd('escalation_pct', e.target.value.replace(/[^0-9.]/g, ''))} /></label>
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Borrowing rate % p.a.<input inputMode="decimal" className={inputCl} style={inputSt} value={f.discount_rate_pct} onChange={e => upd('discount_rate_pct', e.target.value.replace(/[^0-9.]/g, ''))} /></label>
        <label className="text-[11px] flex flex-col gap-1" style={{ color: theme.t2 }}>Lease incentives received<input inputMode="decimal" className={inputCl} style={inputSt} value={f.incentives} onChange={e => upd('incentives', e.target.value.replace(/[^0-9.]/g, ''))} /></label>
      </div>
      {err && <p className="text-xs" style={{ color: RED }}>{err}</p>}
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="px-3 py-2 rounded-lg text-sm" style={{ background: theme.g100, color: theme.text }}>Cancel</button>
        <button onClick={submit} disabled={busy} className="px-4 py-2 rounded-lg text-sm font-semibold" style={{ background: ORANGE, color: '#fff', opacity: busy ? 0.6 : 1 }}>Add lease</button>
      </div>
    </div>
  )
}
