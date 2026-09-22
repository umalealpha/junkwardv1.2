'use client'

/**
 * RegisterManager — the editable cap-table / ESOP register (Finance & EXCO).
 * Add / edit / remove stakeholders, their share holdings and their option
 * grants, and load a vesting schedule per grant. Server enforces the same
 * EXCO/Finance gate the read views use, so a non-viewer never reaches this tab.
 */
import { useEffect, useState } from 'react'
import { Loader2, Plus, Trash2, ChevronDown, ChevronRight, CalendarClock, Save } from 'lucide-react'
import {
  listStakeholders, saveStakeholder, deleteStakeholder,
  saveHolding, deleteHolding, saveGrant, deleteGrant,
  previewVestingSchedule, applyVestingSchedule,
  type EquityStakeholder,
} from '@/lib/api'

const ORANGE = '#F4A623'

export function RegisterManager({ theme, card, onChanged }: any) {
  const [rows, setRows] = useState<EquityStakeholder[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [newName, setNewName] = useState('')
  const [newKind, setNewKind] = useState('individual')
  const [busy, setBusy] = useState(false)

  async function reload() {
    setLoading(true)
    try { setRows(await listStakeholders()); setErr(null) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load register') }
    finally { setLoading(false) }
  }
  useEffect(() => { reload() /* eslint-disable-next-line */ }, [])

  function changed() { reload(); onChanged?.() }

  async function addStakeholder() {
    if (!newName.trim()) return
    setBusy(true)
    try { await saveStakeholder({ name: newName.trim(), kind: newKind, is_current: true }); setNewName(''); changed() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Add failed') }
    finally { setBusy(false) }
  }
  async function removeStakeholder(id: number) {
    if (!confirm('Remove this stakeholder and all their holdings and grants?')) return
    try { await deleteStakeholder(id); changed() } catch (e) { setErr(e instanceof Error ? e.message : 'Delete failed') }
  }

  const inputSty = { background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }

  if (loading) return <div className="flex items-center gap-2 p-4" style={{ color: theme.t2 }}><Loader2 className="w-4 h-4 animate-spin" /> Loading register…</div>

  return (
    <div className="space-y-4">
      {err && <div className="rounded-lg p-3 text-sm" style={{ background: '#fef2f2', color: '#991b1b' }}>{err}</div>}

      {/* add stakeholder */}
      <div className="rounded-xl p-4" style={card}>
        <h2 className="text-sm font-bold mb-2" style={{ color: ORANGE }}>Add a stakeholder</h2>
        <div className="flex flex-wrap gap-2 items-end">
          <input placeholder="Name" value={newName} onChange={e => setNewName(e.target.value)}
            className="rounded-lg px-3 py-2 text-sm flex-1 min-w-[180px]" style={inputSty} />
          <select value={newKind} onChange={e => setNewKind(e.target.value)} className="rounded-lg px-3 py-2 text-sm" style={inputSty}>
            <option value="individual">Individual</option><option value="entity">Entity</option>
          </select>
          <button onClick={addStakeholder} disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold" style={{ background: ORANGE, color: '#0D1B2A', opacity: busy ? 0.6 : 1 }}>
            <Plus className="w-4 h-4" /> Add
          </button>
        </div>
      </div>

      {/* stakeholder list */}
      <div className="rounded-xl p-2" style={card}>
        {rows.map(s => (
          <div key={s.id} style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 px-2 py-2">
              <button onClick={() => setOpen(open === s.id ? null : s.id)} className="p-1">
                {open === s.id ? <ChevronDown className="w-4 h-4" style={{ color: theme.t2 }} /> : <ChevronRight className="w-4 h-4" style={{ color: theme.t2 }} />}
              </button>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold truncate" style={{ color: theme.text }}>{s.name}
                  {!s.is_current && <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded" style={{ background: theme.g100, color: theme.t2 }}>former</span>}
                </div>
                <div className="text-[11px]" style={{ color: theme.t2 }}>
                  {s.kind}{s.employee_name ? ` · linked: ${s.employee_name}` : (s.kind === 'individual' ? ' · not linked to staff' : '')}
                  {` · ${s.holdings?.length || 0} holding(s) · ${s.grants?.length || 0} grant(s)`}
                </div>
              </div>
              <button onClick={() => removeStakeholder(s.id)} className="p-1.5 rounded" title="Remove" style={{ color: '#dc2626' }}><Trash2 className="w-4 h-4" /></button>
            </div>
            {open === s.id && (
              <div className="px-3 pb-3 pl-9">
                <StakeholderEditor s={s} theme={theme} inputSty={inputSty} onChanged={changed} />
              </div>
            )}
          </div>
        ))}
        {rows.length === 0 && <div className="p-4 text-sm" style={{ color: theme.t2 }}>No stakeholders yet.</div>}
      </div>
    </div>
  )
}

function StakeholderEditor({ s, theme, inputSty, onChanged }: any) {
  const [current, setCurrent] = useState(s.is_current)
  const [email, setEmail] = useState(s.email || '')

  async function saveMeta() {
    await saveStakeholder({ is_current: current, email }, s.id); onChanged()
  }

  // holdings
  const [hKlass, setHKlass] = useState(''); const [hShares, setHShares] = useState(''); const [hUsd, setHUsd] = useState('')
  async function addHolding() {
    if (!hKlass.trim()) return
    await saveHolding({ stakeholder: s.id, klass: hKlass.trim(), shares: Number(hShares) || 0, usd_invested: Number(hUsd) || 0 })
    setHKlass(''); setHShares(''); setHUsd(''); onChanged()
  }

  // grants
  const [gUnits, setGUnits] = useState(''); const [gDate, setGDate] = useState('')
  async function addGrant() {
    if (!Number(gUnits)) return
    await saveGrant({ stakeholder: s.id, units: Number(gUnits), grant_date: gDate || null, status: 'active' })
    setGUnits(''); setGDate(''); onChanged()
  }

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex items-center gap-1 text-xs" style={{ color: theme.t2 }}>
          <input type="checkbox" checked={current} onChange={e => setCurrent(e.target.checked)} /> Current holder
        </label>
        <input placeholder="login email (for self-service match)" value={email} onChange={e => setEmail(e.target.value)}
          className="rounded-lg px-2 py-1 text-xs flex-1 min-w-[200px]" style={inputSty} />
        <button onClick={saveMeta} className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold" style={{ background: theme.g100, color: theme.text }}><Save className="w-3 h-3" /> Save</button>
      </div>

      {/* holdings */}
      <div>
        <div className="text-xs font-semibold mb-1" style={{ color: ORANGE }}>Share holdings</div>
        {(s.holdings || []).map((h: any) => (
          <div key={h.id} className="flex items-center gap-2 text-xs py-1" style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
            <span className="w-24" style={{ color: theme.text }}>{h.klass}</span>
            <span className="w-28" style={{ color: theme.t2 }}>{Number(h.shares).toLocaleString('en')} sh</span>
            <span className="w-28" style={{ color: theme.t2 }}>${Number(h.usd_invested).toLocaleString('en')}</span>
            <button onClick={async () => { await deleteHolding(h.id); onChanged() }} style={{ color: '#dc2626' }}><Trash2 className="w-3.5 h-3.5" /></button>
          </div>
        ))}
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          <input placeholder="Class (ORB)" value={hKlass} onChange={e => setHKlass(e.target.value)} className="rounded px-2 py-1 text-xs w-24" style={inputSty} />
          <input placeholder="Shares" value={hShares} onChange={e => setHShares(e.target.value)} className="rounded px-2 py-1 text-xs w-28" style={inputSty} />
          <input placeholder="USD invested" value={hUsd} onChange={e => setHUsd(e.target.value)} className="rounded px-2 py-1 text-xs w-28" style={inputSty} />
          <button onClick={addHolding} className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold" style={{ background: ORANGE, color: '#0D1B2A' }}><Plus className="w-3 h-3" /> Add</button>
        </div>
      </div>

      {/* grants */}
      <div>
        <div className="text-xs font-semibold mb-1" style={{ color: ORANGE }}>Option grants</div>
        {(s.grants || []).map((g: any) => (
          <GrantRow key={g.id} g={g} theme={theme} inputSty={inputSty} onChanged={onChanged} />
        ))}
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          <input placeholder="Units" value={gUnits} onChange={e => setGUnits(e.target.value)} className="rounded px-2 py-1 text-xs w-24" style={inputSty} />
          <input type="date" value={gDate} onChange={e => setGDate(e.target.value)} className="rounded px-2 py-1 text-xs" style={inputSty} />
          <button onClick={addGrant} className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold" style={{ background: ORANGE, color: '#0D1B2A' }}><Plus className="w-3 h-3" /> Add grant</button>
        </div>
      </div>
    </div>
  )
}

function GrantRow({ g, theme, inputSty, onChanged }: any) {
  const [units, setUnits] = useState(String(g.units))
  const [date, setDate] = useState(g.grant_date || '')
  const [status, setStatus] = useState(g.status)
  const [ref, setRef] = useState(g.letter_ref || '')
  const [showVest, setShowVest] = useState(false)
  const [tranches, setTranches] = useState<{ vest_date: string; units: number }[]>(
    (g.tranches || []).map((t: any) => ({ vest_date: t.vest_date, units: t.units })))
  const [vbusy, setVbusy] = useState(false)
  const [msg, setMsg] = useState('')

  async function save() {
    await saveGrant({ units: Number(units) || 0, grant_date: date || null, status, letter_ref: ref }, g.id); onChanged()
  }
  async function preview() {
    setVbusy(true); setMsg('')
    try { const r = await previewVestingSchedule(g.id); setTranches(r.tranches); if (!r.tranches.length) setMsg('Set a grant date and units first.') }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Preview failed') } finally { setVbusy(false) }
  }
  async function apply() {
    setVbusy(true); setMsg('')
    try { const r: any = await applyVestingSchedule(g.id, tranches); setMsg(`Saved ${r.saved} vesting step(s).`); onChanged() }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Save failed') } finally { setVbusy(false) }
  }

  return (
    <div className="py-1.5 text-xs" style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
      <div className="flex flex-wrap items-center gap-1.5">
        <input value={units} onChange={e => setUnits(e.target.value)} className="rounded px-2 py-1 w-20" style={inputSty} title="Units" />
        <input type="date" value={date} onChange={e => setDate(e.target.value)} className="rounded px-2 py-1" style={inputSty} title="Grant date" />
        <select value={status} onChange={e => setStatus(e.target.value)} className="rounded px-2 py-1" style={inputSty}>
          <option value="active">active</option><option value="lapsed">lapsed</option><option value="exercised">exercised</option><option value="cancelled">cancelled</option>
        </select>
        <input placeholder="Letter ref" value={ref} onChange={e => setRef(e.target.value)} className="rounded px-2 py-1 w-28" style={inputSty} />
        <button onClick={save} className="inline-flex items-center gap-1 px-2 py-1 rounded font-semibold" style={{ background: theme.g100, color: theme.text }}><Save className="w-3 h-3" /> Save</button>
        <button onClick={() => setShowVest(v => !v)} className="inline-flex items-center gap-1 px-2 py-1 rounded font-semibold" style={{ background: theme.g100, color: theme.text }}><CalendarClock className="w-3 h-3" /> Vesting ({(g.tranches || []).length})</button>
        <button onClick={async () => { await deleteGrant(g.id); onChanged() }} style={{ color: '#dc2626' }}><Trash2 className="w-3.5 h-3.5" /></button>
      </div>
      {showVest && (
        <div className="mt-2 pl-2 border-l-2" style={{ borderColor: ORANGE }}>
          <div className="flex items-center gap-2 mb-1">
            <button onClick={preview} disabled={vbusy} className="px-2 py-1 rounded font-semibold" style={{ background: theme.g100, color: theme.text }}>
              {vbusy ? 'Working…' : 'Preview standard 4yr / 1yr-cliff'}
            </button>
            <button onClick={apply} disabled={vbusy || !tranches.length} className="px-2 py-1 rounded font-semibold" style={{ background: ORANGE, color: '#0D1B2A', opacity: (!tranches.length) ? 0.5 : 1 }}>Save schedule</button>
            {msg && <span style={{ color: theme.t2 }}>{msg}</span>}
          </div>
          <div className="max-h-40 overflow-y-auto">
            {tranches.map((t, i) => (
              <div key={i} className="flex items-center gap-2 py-0.5">
                <input type="date" value={t.vest_date} onChange={e => setTranches(ts => ts.map((x, j) => j === i ? { ...x, vest_date: e.target.value } : x))} className="rounded px-1.5 py-0.5" style={inputSty} />
                <input value={t.units} onChange={e => setTranches(ts => ts.map((x, j) => j === i ? { ...x, units: Number(e.target.value) || 0 } : x))} className="rounded px-1.5 py-0.5 w-24" style={inputSty} />
                <button onClick={() => setTranches(ts => ts.filter((_, j) => j !== i))} style={{ color: '#dc2626' }}><Trash2 className="w-3 h-3" /></button>
              </div>
            ))}
            {!tranches.length && <div style={{ color: theme.t2 }} className="py-1">No schedule loaded. Preview a standard one or add steps.</div>}
          </div>
          <button onClick={() => setTranches(ts => [...ts, { vest_date: '', units: 0 }])} className="mt-1 inline-flex items-center gap-1 px-2 py-1 rounded" style={{ background: theme.g100, color: theme.text }}><Plus className="w-3 h-3" /> step</button>
        </div>
      )}
    </div>
  )
}
