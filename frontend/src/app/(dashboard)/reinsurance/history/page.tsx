'use client'

/**
 * Reinsurance — 11-Year Treaty Performance (LOCKED board dashboard).
 *
 * CFO directive 2026-08-14. A permanent, locked record of the reinsurance
 * programme's 11-year performance (UWY 2014/15–2025/26, as at 31 Mar 2026),
 * cover by cover. Read-only for everyone; amending or deleting is impossible
 * from the UI without the caller's own password (the same login as HRIS), and
 * every amendment keeps a snapshot so the old numbers are never lost.
 *
 * The figures are served from a saved copy inside Omni (ReinsuranceHistory) —
 * never read live from the source Excel — so the dashboard survives file moves.
 */
import { useState, useMemo, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Lock, ShieldCheck, TrendingUp, TrendingDown, History, Pencil, X, AlertTriangle } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const GREEN = '#059669'
const RED = '#DC2626'

type Row = {
  key: string; name: string; status?: string; detail?: string; note?: string
  premium: number; commission?: number; incurred: number
  net: number; lr: number; net_ex_ll: number | null; lr_ex_ll: number | null
}
type Dataset = {
  as_at: string; currency: string; title: string; subtitle: string; large_loss_note: string
  proportional: Row[]; nonproportional: Row[]
  totals: { premium: number; commission: number; incurred: number; net: number; lr: number; net_ex_ll: number; lr_ex_ll: number }
  lr_series: Record<string, { uwy: string; lr: number }[]>
}
type Payload = {
  exists: boolean; locked: boolean; version: number; data: Dataset; source_note: string
  last_amended_by: string | null; last_amended_at: string | null; can_amend: boolean
}

const P = (n: number) => 'P ' + Math.round(n).toLocaleString('en-BW')
const PM = (n: number) => (n < 0 ? '-P ' : '+P ') + (Math.abs(n) / 1_000_000).toFixed(1) + 'm'
const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('en-BW', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

function LRBar({ series }: { series: { uwy: string; lr: number }[] }) {
  const max = Math.max(100, ...series.map(s => s.lr))
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 120, position: 'relative', marginTop: 8 }}>
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: `${(100 / max) * 104}px`, borderTop: `1px dashed ${RED}`, fontSize: 8, color: RED }}>100%</div>
      {series.map(h => (
        <div key={h.uwy} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end' }}>
          <div style={{ fontSize: 8, color: NAVY, fontWeight: 600 }}>{h.lr}</div>
          <div title={`${h.uwy}: LR ${h.lr}%`}
            style={{ width: '72%', height: `${(h.lr / max) * 104}px`, background: h.lr > 100 ? RED : h.lr > 70 ? ORANGE : GREEN, borderRadius: '3px 3px 0 0' }} />
          <div style={{ fontSize: 8, color: '#9AA3AF', marginTop: 2 }}>{h.uwy}</div>
        </div>
      ))}
    </div>
  )
}

export default function ReinsuranceHistoryPage() {
  const router = useRouter()
  useEffect(() => { if (!getToken()) router.replace('/login') }, [router])

  const [payload, setPayload] = useState<Payload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [exLL, setExLL] = useState(false)          // exclude the Nata Lodge large loss
  const [amendOpen, setAmendOpen] = useState(false)

  async function load() {
    setErr(null)
    try {
      const d = await apiFetch<Payload>('/reinsurance/history/')
      setPayload(d)
    } catch {
      setErr('Could not load the 11-year history. It may not be loaded on the server yet.')
    }
  }
  useEffect(() => { load() }, [])

  const ds = payload?.data
  const netOf = (r: { net: number; net_ex_ll: number | null }) => (exLL && r.net_ex_ll != null ? r.net_ex_ll : r.net)
  const lrOf = (r: { lr: number; lr_ex_ll: number | null }) => (exLL && r.lr_ex_ll != null ? r.lr_ex_ll : r.lr)

  if (err) return (
    <div className="flex flex-col h-full">
      <TopBar title="11-Year Performance" breadcrumbs={[{ label: 'Reinsurance' }, { label: '11-Year Performance' }]} />
      <div style={{ padding: 32, color: RED }}>{err}</div>
    </div>
  )
  if (!payload || !ds) return (
    <div className="flex flex-col h-full">
      <TopBar title="11-Year Performance" breadcrumbs={[{ label: 'Reinsurance' }, { label: '11-Year Performance' }]} />
      <div style={{ padding: 32, color: '#6B7480' }}>Loading…</div>
    </div>
  )

  return (
    <div className="flex flex-col h-full">
      <TopBar title="11-Year Performance" breadcrumbs={[{ label: 'Reinsurance' }, { label: '11-Year Performance' }]} />

      <div className="flex-1 overflow-auto" style={{ background: '#F7F8FB' }}>
        {/* Hero */}
        <div style={{ background: `linear-gradient(120deg, ${NAVY} 0%, #16314d 100%)`, color: '#fff', padding: '28px 32px' }}>
          <div style={{ fontSize: 11, letterSpacing: 3, textTransform: 'uppercase', opacity: 0.7 }}>{ds.subtitle}</div>
          <div style={{ fontSize: 40, fontWeight: 800, lineHeight: 1.05, fontFamily: 'var(--font-display, Georgia)' }}>
            11-Year <span style={{ color: ORANGE, fontStyle: 'italic' }}>Performance.</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginTop: 12 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: '#fff', background: 'rgba(255,255,255,0.14)', borderRadius: 6, padding: '5px 11px' }}>
              <Lock size={13} /> Locked · v{payload.version}
            </span>
            <span style={{ fontSize: 12, opacity: 0.85 }}>
              Last amended: {payload.last_amended_by || 'never (original figures)'} · {fmtDate(payload.last_amended_at)}
            </span>
            {payload.can_amend && (
              <button onClick={() => setAmendOpen(true)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: NAVY, background: ORANGE, border: 'none', borderRadius: 6, padding: '6px 12px', cursor: 'pointer' }}>
                <Pencil size={13} /> Amend (password)
              </button>
            )}
          </div>
        </div>

        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: NAVY, fontWeight: 600 }}>View:</span>
            {[{ v: false, l: 'Including large losses' }, { v: true, l: 'Excluding Nata Lodge' }].map(o => (
              <button key={o.l} onClick={() => setExLL(o.v)}
                style={{ fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 8, cursor: 'pointer',
                  border: `1px solid ${exLL === o.v ? ORANGE : '#D6DAE2'}`, background: exLL === o.v ? ORANGE : '#fff', color: NAVY }}>
                {o.l}
              </button>
            ))}
          </div>

          {/* Proportional summary table */}
          <Card>
            <CardContent>
              <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 16, marginBottom: 2 }}>Proportional treaties — whole-programme summary</h3>
              <p style={{ fontSize: 12, color: '#9AA3AF', marginBottom: 14 }}>Cumulative {ds.currency} · a positive net result is a profit to the reinsurer</p>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: NAVY, color: '#fff' }}>
                      {['Treaty', 'Premium', 'Commission', 'Incurred claims', 'Net result', 'LR'].map((h, i) => (
                        <th key={h} style={{ padding: '9px 10px', textAlign: i === 0 ? 'left' : 'right', fontWeight: 700 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ds.proportional.map(r => (
                      <tr key={r.key} style={{ borderBottom: '1px solid #EEF1F6' }}>
                        <td style={{ padding: '9px 10px' }}>
                          <div style={{ color: NAVY, fontWeight: 600 }}>{r.name}</div>
                          {r.status && <div style={{ fontSize: 11, color: ORANGE }}>{r.status}</div>}
                        </td>
                        <td style={{ padding: '9px 10px', textAlign: 'right' }}>{P(r.premium)}</td>
                        <td style={{ padding: '9px 10px', textAlign: 'right' }}>{r.commission != null ? P(r.commission) : '—'}</td>
                        <td style={{ padding: '9px 10px', textAlign: 'right' }}>{P(r.incurred)}</td>
                        <td style={{ padding: '9px 10px', textAlign: 'right', fontWeight: 700, color: netOf(r) >= 0 ? GREEN : RED }}>{PM(netOf(r))}</td>
                        <td style={{ padding: '9px 10px', textAlign: 'right' }}>{lrOf(r)}%</td>
                      </tr>
                    ))}
                    <tr style={{ background: '#EDEFF5', fontWeight: 700 }}>
                      <td style={{ padding: '10px' }}>TOTAL</td>
                      <td style={{ padding: '10px', textAlign: 'right' }}>{P(ds.totals.premium)}</td>
                      <td style={{ padding: '10px', textAlign: 'right' }}>{P(ds.totals.commission)}</td>
                      <td style={{ padding: '10px', textAlign: 'right' }}>{P(ds.totals.incurred)}</td>
                      <td style={{ padding: '10px', textAlign: 'right', color: netOf(ds.totals) >= 0 ? GREEN : RED }}>{PM(netOf(ds.totals))}</td>
                      <td style={{ padding: '10px', textAlign: 'right' }}>{lrOf(ds.totals)}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginTop: 12, fontSize: 12, color: '#6B7480' }}>
                <AlertTriangle size={15} style={{ color: ORANGE, flexShrink: 0, marginTop: 1 }} />
                <span>{ds.large_loss_note}</span>
              </div>
            </CardContent>
          </Card>

          {/* Non-proportional cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            {ds.nonproportional.map(r => (
              <Card key={r.key}>
                <CardContent>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 15 }}>{r.name}</h3>
                    <span style={{ color: netOf(r) >= 0 ? GREEN : RED }}>{netOf(r) >= 0 ? <TrendingUp size={18} /> : <TrendingDown size={18} />}</span>
                  </div>
                  {r.detail && <p style={{ fontSize: 12, color: '#9AA3AF', marginTop: 2 }}>{r.detail}</p>}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12 }}>
                    {[
                      { k: 'Premium paid', v: P(r.premium), c: NAVY },
                      { k: 'Losses recovered', v: P(r.incurred), c: NAVY },
                      { k: 'Net result', v: PM(netOf(r)), c: netOf(r) >= 0 ? GREEN : RED },
                      { k: 'Loss ratio', v: `${lrOf(r)}%`, c: NAVY },
                    ].map(o => (
                      <div key={o.k} style={{ background: '#F5F7FB', borderRadius: 8, padding: '9px 11px' }}>
                        <div style={{ fontSize: 11, color: '#6B7480' }}>{o.k}</div>
                        <div style={{ fontSize: 16, fontWeight: 700, color: o.c }}>{o.v}</div>
                      </div>
                    ))}
                  </div>
                  {r.note && <p style={{ fontSize: 12, color: '#6B7480', marginTop: 10 }}>{r.note}</p>}
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Per-cover 11-yr loss-ratio charts */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16 }}>
            {ds.proportional.filter(r => ds.lr_series[r.key]?.length).map(r => (
              <Card key={r.key}>
                <CardContent>
                  <h3 style={{ fontWeight: 700, color: NAVY, fontSize: 14 }}>{r.name} — loss ratio by year</h3>
                  <LRBar series={ds.lr_series[r.key]} />
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Lock note + history link */}
          <Card>
            <CardContent>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <ShieldCheck size={20} style={{ color: GREEN }} />
                <div style={{ fontSize: 13, color: NAVY }}>
                  This dataset is <strong>locked</strong>. It cannot be deleted, and changing any figure needs your password.
                  Every change keeps a dated snapshot of the old numbers.
                </div>
              </div>
              <a href="#" onClick={async (e) => { e.preventDefault(); const d = await apiFetch<{ snapshots: { version: number; amended_by: string | null; amended_at: string; note: string }[] }>('/reinsurance/history/snapshots/'); alert(d.snapshots.length ? d.snapshots.map(s => `v${s.version} · ${s.amended_by || '—'} · ${fmtDate(s.amended_at)}\n${s.note}`).join('\n\n') : 'No prior versions — these are the original figures.') }}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 12, fontSize: 12, color: NAVY, fontWeight: 600 }}>
                <History size={14} /> View change history
              </a>
            </CardContent>
          </Card>

          <p style={{ fontSize: 11, color: '#9AA3AF', textAlign: 'center', paddingBottom: 16 }}>
            Cumulative over underwriting years 2014/15–2025/26 · position as at {ds.as_at} · 2025/26 is part-year and will continue to develop.
          </p>
        </div>
      </div>

      {amendOpen && payload.can_amend && (
        <AmendModal payload={payload} onClose={() => setAmendOpen(false)} onSaved={() => { setAmendOpen(false); load() }} />
      )}
    </div>
  )
}

// ── Amend modal — password + editable headline figures ──────────────────────
function AmendModal({ payload, onClose, onSaved }: { payload: Payload; onClose: () => void; onSaved: () => void }) {
  const [draft, setDraft] = useState<Dataset>(JSON.parse(JSON.stringify(payload.data)))
  const [password, setPassword] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const editRow = (bucket: 'proportional' | 'nonproportional', i: number, field: string, val: string) => {
    const copy: Dataset = JSON.parse(JSON.stringify(draft))
    const num = val === '' ? null : Number(val)
    // @ts-expect-error dynamic field
    copy[bucket][i][field] = num
    setDraft(copy)
  }

  async function save() {
    if (!password) { setMsg('Enter your password.'); return }
    setBusy(true); setMsg(null)
    try {
      await apiFetch('/reinsurance/history/amend/', {
        method: 'POST',
        body: JSON.stringify({ password, note, data: draft }),
      })
      onSaved()
    } catch (e: unknown) {
      const m = e instanceof Error ? e.message : ''
      setMsg(m.includes('403') ? 'Password incorrect or you are not allowed. Nothing changed.' : 'Could not save. Nothing changed.')
    } finally {
      setBusy(false)
    }
  }

  const FIELDS = ['premium', 'commission', 'incurred', 'net', 'lr', 'net_ex_ll', 'lr_ex_ll']
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(13,27,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20 }}>
      <div style={{ background: '#fff', borderRadius: 14, maxWidth: 760, width: '100%', maxHeight: '90vh', overflow: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px', borderBottom: '1px solid #EEF1F6' }}>
          <h3 style={{ fontWeight: 800, color: NAVY, fontSize: 17 }}>Amend the 11-year history</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6B7480' }}><X size={20} /></button>
        </div>
        <div style={{ padding: 20 }}>
          <p style={{ fontSize: 13, color: '#6B7480', marginBottom: 14 }}>
            Change any headline figure below. The current numbers are snapshotted before saving, so nothing is lost. Requires your password.
          </p>
          {(['proportional', 'nonproportional'] as const).map(bucket => (
            <div key={bucket} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: NAVY, textTransform: 'capitalize', marginBottom: 6 }}>{bucket}</div>
              {draft[bucket].map((r, i) => (
                <div key={r.key} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, color: NAVY, fontWeight: 600, marginBottom: 3 }}>{r.name}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {FIELDS.map(f => (
                      <label key={f} style={{ fontSize: 10, color: '#6B7480', display: 'flex', flexDirection: 'column', width: 96 }}>
                        {f}
                        {/* @ts-expect-error dynamic field */}
                        <input value={r[f] ?? ''} onChange={e => editRow(bucket, i, f, e.target.value)}
                          style={{ border: '1px solid #D6DAE2', borderRadius: 6, padding: '4px 6px', fontSize: 12 }} />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ))}
          <label style={{ fontSize: 12, color: NAVY, fontWeight: 600, display: 'block', marginBottom: 4 }}>Note (what changed & why)</label>
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. corrected Fire incurred claims per Q4 broker statement"
            style={{ width: '100%', border: '1px solid #D6DAE2', borderRadius: 8, padding: '9px 11px', fontSize: 13, marginBottom: 12 }} />
          <label style={{ fontSize: 12, color: NAVY, fontWeight: 600, display: 'block', marginBottom: 4 }}>Your password (same as HRIS)</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password"
            style={{ width: '100%', border: '1px solid #D6DAE2', borderRadius: 8, padding: '9px 11px', fontSize: 13 }} />
          {msg && <div style={{ color: RED, fontSize: 12, marginTop: 8 }}>{msg}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
            <button onClick={onClose} style={{ fontSize: 13, color: '#6B7480', background: 'none', border: '1px solid #D6DAE2', borderRadius: 8, padding: '8px 16px', cursor: 'pointer' }}>Cancel</button>
            <button onClick={save} disabled={busy}
              style={{ fontSize: 13, fontWeight: 700, color: NAVY, background: ORANGE, border: 'none', borderRadius: 8, padding: '8px 18px', cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Saving…' : 'Save (locked)'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
