'use client'

/**
 * /health/provider-dashboard — ADH Provider Network Readiness Dashboard.
 *
 * Interactive executive view built from the Stitch "Live Instrument Panel"
 * design (Navy #1D3270 / Orange #F47C20, Montserrat/Open Sans, traffic-light
 * readiness). Live data from the registry API (/health/service-providers/):
 * counts + every provider row, so all filtering/sorting is client-side — no new
 * backend. Readiness: Ready = ADH-ready; To chase = readiness mismatch; In
 * progress = everyone else.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  RefreshCw, Download, AlertTriangle, ChevronRight, Search, X, Loader2,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch, apiFetchBinary } from '@/lib/api'

// Brand + semantic tokens (from the Stitch DESIGN.md)
const NAVY = '#1D3270'
const NAVY_DEEP = '#001b5a'
const ORANGE = '#F47C20'
const GREEN = '#00b853'
const AMBER = '#f59e0b'
const RED = '#ba1a1a'
const INK = '#191c1d'
const MUTE = '#454650'
const LINE = '#c5c6d2'
const SURF = '#f8f9fa'
const FONT_H = "Montserrat,-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
const FONT_B = "'Open Sans',-apple-system,'Segoe UI',Roboto,Arial,sans-serif"

interface Provider {
  id: string; practice_number: string; name: string; discipline: string; town: string
  contract_status: string; afa_registered: string; qc_confirmed: boolean
  adh_ready: boolean; ready_mismatch: boolean; adh_acceptance: string
}
interface Counts {
  total: number; afa_registered: number; afa_pending: number; adh_ready: number
  afa_says_ready: number
  registered_not_qc: number; mismatches: number; pending_applications: number
  by_discipline: Record<string, number>
}
type Seg = 'all' | 'ready' | 'in_progress' | 'to_chase'

function readiness(p: Provider): Seg {
  if (p.adh_ready) return 'ready'
  if (p.ready_mismatch) return 'to_chase'
  return 'in_progress'
}
const CAP: React.CSSProperties = {
  fontFamily: FONT_B, fontSize: 11, fontWeight: 700, letterSpacing: '0.05em',
  textTransform: 'uppercase',
}

export default function ProviderDashboardPage() {
  const [counts, setCounts] = useState<Counts | null>(null)
  const [rows, setRows] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState('')
  const [q, setQ] = useState('')
  const [seg, setSeg] = useState<Seg>('all')
  const [disc, setDisc] = useState('')
  const [metricFilter, setMetricFilter] = useState<{ key: string; label: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await apiFetch<{ counts: Counts; results: Provider[] }>(
        '/health/service-providers/?active_only=1')
      setCounts(d.counts); setRows(d.results)
      setLastUpdated(new Date().toLocaleString('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }))
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const total = counts?.total ?? rows.length
  const ready = counts?.adh_ready ?? 0
  const toChase = counts?.mismatches ?? 0
  const inProgress = Math.max(0, total - ready - toChase)
  const pct = (n: number) => total ? Math.round((100 * n) / total) : 0

  // metric -> row predicate (for click-to-filter the drill-down)
  const METRICS: { key: string; label: string; count: number; pctText: string; pred: (p: Provider) => boolean; alert?: boolean }[] = counts ? [
    { key: 'total', label: 'Total providers', count: counts.total, pctText: '100%', pred: () => true },
    { key: 'afa_registered', label: 'AFA-registered', count: counts.afa_registered, pctText: `${pct(counts.afa_registered)}%`, pred: p => p.afa_registered === 'Yes' },
    { key: 'afa_pending', label: 'AFA registration pending', count: counts.afa_pending, pctText: `${pct(counts.afa_pending)}%`, pred: p => p.afa_registered === 'Pending' },
    // Two readiness numbers, deliberately side by side. The top one is AFA's
    // own claim off their column; the one below is our own check (contract
    // signed AND a person QC-confirmed). Blending them hid that the second was
    // fed by the first, so it could never disagree — see provider_registry.
    { key: 'afa_says_ready', label: 'AFA says ready (their list)', count: counts.afa_says_ready, pctText: `${pct(counts.afa_says_ready)}%`, pred: p => (p.adh_acceptance || '').trim().toUpperCase() === 'YES' },
    { key: 'adh_ready', label: 'ADH-ready, verified by us (signed + QC)', count: counts.adh_ready, pctText: `${pct(counts.adh_ready)}%`, pred: p => p.adh_ready },
    { key: 'registered_not_qc', label: "Registered but not yet QC'd", count: counts.registered_not_qc, pctText: `${pct(counts.registered_not_qc)}%`, pred: p => p.afa_registered === 'Yes' && !p.qc_confirmed },
    { key: 'mismatches', label: 'Readiness mismatches', count: counts.mismatches, pctText: `${pct(counts.mismatches)}%`, pred: p => p.ready_mismatch, alert: true },
    { key: 'pending_applications', label: 'New applications (pending review)', count: counts.pending_applications, pctText: '—', pred: () => false },
  ] : []

  const disciplines = useMemo(() =>
    Array.from(new Set(rows.map(r => r.discipline).filter(Boolean))).sort(), [rows])

  const chase = useMemo(() => rows.filter(r => r.ready_mismatch), [rows])

  const filtered = useMemo(() => {
    let out = rows
    if (metricFilter) {
      const m = METRICS.find(x => x.key === metricFilter.key)
      if (m) out = out.filter(m.pred)
    }
    if (seg !== 'all') out = out.filter(r => readiness(r) === seg)
    if (disc) out = out.filter(r => r.discipline === disc)
    const s = q.trim().toLowerCase()
    if (s) out = out.filter(r =>
      r.name.toLowerCase().includes(s) || r.practice_number.toLowerCase().includes(s)
      || (r.town || '').toLowerCase().includes(s))
    return out
  }, [rows, metricFilter, seg, disc, q]) // eslint-disable-line react-hooks/exhaustive-deps

  const exportXlsx = async () => {
    try {
      const r = await apiFetchBinary('/health/service-providers/export/?filter=all')
      if (!r.ok) return
      const url = URL.createObjectURL(await r.blob())
      const a = document.createElement('a'); a.href = url
      a.download = 'ADH_provider_network.xlsx'; document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch { /* ignore */ }
  }

  const dot = (color: string) => (
    <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 999, background: color }} />
  )
  const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`, borderRadius: 4 }

  return (
    <div style={{ minHeight: '100vh', background: SURF, fontFamily: FONT_B, color: INK }}>
      <TopBar />
      <div style={{ maxWidth: 1280, margin: '0 auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 28 }}>

        {/* HEADER */}
        <div style={{ borderBottom: `1px solid ${LINE}`, paddingBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <h1 style={{ fontFamily: FONT_H, fontSize: 24, fontWeight: 600, color: NAVY, letterSpacing: '-0.01em' }}>
                ADH Provider Network — Readiness Dashboard
              </h1>
              <p style={{ fontSize: 13, color: '#757681', marginTop: 4, fontWeight: 600, letterSpacing: '0.02em' }}>
                Last updated: {lastUpdated || '—'} CAT
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={load} title="Refresh"
                style={{ width: 40, height: 40, border: `1px solid ${LINE}`, borderRadius: 4, background: '#fff', color: MUTE, cursor: 'pointer' }}>
                <RefreshCw size={18} className={loading ? 'animate-spin' : ''} style={{ margin: '0 auto' }} />
              </button>
              <button onClick={exportXlsx} title="Download"
                style={{ width: 40, height: 40, border: `1px solid ${LINE}`, borderRadius: 4, background: '#fff', color: MUTE, cursor: 'pointer' }}>
                <Download size={18} style={{ margin: '0 auto' }} />
              </button>
            </div>
          </div>

          {/* HERO */}
          <div style={{ ...card, display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(0,1fr)', gap: 24, alignItems: 'center', padding: 24, marginTop: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <h2 style={{ fontFamily: FONT_H, fontSize: 18, fontWeight: 600 }}>
                {ready} of {total} providers ({pct(ready)}%) are ADH-ready today
              </h2>
              <div style={{ height: 16, width: '100%', borderRadius: 999, overflow: 'hidden', display: 'flex' }}>
                <div title={`${ready} Ready`} style={{ width: `${pct(ready)}%`, background: GREEN }} />
                <div title={`${inProgress} In progress`} style={{ width: `${pct(inProgress)}%`, background: AMBER }} />
                <div title={`${toChase} To chase`} style={{ width: `${pct(toChase)}%`, background: RED }} />
              </div>
              <div style={{ display: 'flex', gap: 16, ...CAP, fontSize: 11 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#00814a' }}>{dot(GREEN)} {ready} Ready</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: MUTE }}>{dot(AMBER)} {inProgress} In progress</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: RED }}>{dot(RED)} {toChase} To chase</span>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', borderLeft: `1px solid ${LINE}`, paddingLeft: 24 }}>
              <span style={{ ...CAP, color: MUTE }}>Mismatches</span>
              <span style={{ fontFamily: FONT_H, fontSize: 48, lineHeight: 1, fontWeight: 700, color: ORANGE }}>{toChase}</span>
            </div>
          </div>
        </div>

        {/* GRID: summary table (left) + action required (right) */}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(0,1fr)', gap: 28 }}>

          {/* NETWORK SUMMARY TABLE — the 2nd block */}
          <section style={{ ...card, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: `1px solid ${LINE}`, background: '#f3f4f5' }}>
              <h3 style={{ ...CAP, color: INK }}>Network Summary</h3>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', textAlign: 'left', fontSize: 14, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: SURF, borderBottom: `1px solid ${LINE}`, ...CAP, color: MUTE }}>
                    <th style={{ padding: '12px 20px' }}>Metric</th>
                    <th style={{ padding: '12px 20px', textAlign: 'right' }}>Count</th>
                    <th style={{ padding: '12px 20px', width: '38%' }}>% of network</th>
                  </tr>
                </thead>
                <tbody>
                  {METRICS.map(m => {
                    const active = metricFilter?.key === m.key
                    const clickable = m.key !== 'pending_applications'
                    return (
                      <tr key={m.key}
                        onClick={() => clickable && setMetricFilter(active ? null : { key: m.key, label: m.label })}
                        style={{
                          borderBottom: `1px solid #e7e8e9`,
                          cursor: clickable ? 'pointer' : 'default',
                          background: active ? '#eef1f8' : m.alert ? '#FFF6F5' : 'transparent',
                          borderLeft: m.alert ? `3px solid ${ORANGE}` : '3px solid transparent',
                        }}>
                        <td style={{ padding: '12px 20px', fontWeight: 500 }}>{m.label}</td>
                        <td style={{ padding: '12px 20px', textAlign: 'right', fontWeight: 600, letterSpacing: '0.02em' }}>{m.count}</td>
                        <td style={{ padding: '12px 20px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div style={{ width: '100%', maxWidth: 120, height: 6, background: '#e1e3e4', borderRadius: 999, overflow: 'hidden' }}>
                              <div style={{ width: `${m.key === 'total' ? 100 : pct(m.count)}%`, height: '100%', background: m.alert ? ORANGE : NAVY }} />
                            </div>
                            <span style={{ fontSize: 11, color: MUTE, width: 34, fontWeight: 600 }}>{m.pctText}</span>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* ACTION REQUIRED */}
          <section style={{ ...card, border: `1px solid ${ORANGE}`, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: `1px solid ${ORANGE}`, background: '#FFF3EB' }}>
              <h3 style={{ ...CAP, color: ORANGE, display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertTriangle size={16} /> Action Required ({chase.length})
              </h3>
            </div>
            <div style={{ maxHeight: 460, overflowY: 'auto' }}>
              {chase.length === 0 && (
                <div style={{ padding: 20, fontSize: 13, color: '#00814a' }}>Nothing to chase — every “ready” provider is fully AFA-registered.</div>
              )}
              {chase.map(p => (
                <div key={p.id} style={{ padding: 20, borderBottom: `1px solid #e7e8e9`, cursor: 'pointer' }}
                  onClick={() => { setQ(p.name); setMetricFilter(null); setSeg('all'); setDisc('') }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                    <span style={{ fontFamily: FONT_H, fontSize: 14, fontWeight: 600 }}>{p.name}</span>
                    <ChevronRight size={16} color={LINE} />
                  </div>
                  <div style={{ fontSize: 12, color: MUTE, marginBottom: 8 }}>{p.discipline || '—'} • {p.town || '—'}</div>
                  <span style={{ ...CAP, fontSize: 10, display: 'inline-flex', padding: '2px 8px', borderRadius: 4, background: '#ffdad6', color: '#93000a', border: '1px solid rgba(186,26,26,0.2)' }}>
                    {p.afa_registered === 'Yes' ? 'QC not confirmed' : 'AFA incomplete'}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* DRILL-DOWN */}
        <section style={{ ...card, overflow: 'hidden' }}>
          <div style={{ padding: '14px 20px', borderBottom: `1px solid ${LINE}`, background: '#f3f4f5', display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', justifyContent: 'space-between' }}>
            <h3 style={{ ...CAP, color: INK }}>Provider directory ({filtered.length})</h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              {metricFilter && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, background: '#eef1f8', border: `1px solid ${NAVY}`, color: NAVY, borderRadius: 4, padding: '4px 8px' }}>
                  {metricFilter.label}
                  <X size={13} style={{ cursor: 'pointer' }} onClick={() => setMetricFilter(null)} />
                </span>
              )}
              <div style={{ position: 'relative' }}>
                <Search size={14} style={{ position: 'absolute', left: 8, top: 9, color: '#757681' }} />
                <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, practice no., town"
                  style={{ width: 220, padding: '7px 8px 7px 28px', border: `1px solid ${LINE}`, borderRadius: 4, fontSize: 13 }} />
              </div>
              <select value={disc} onChange={e => setDisc(e.target.value)}
                style={{ padding: '7px 8px', border: `1px solid ${LINE}`, borderRadius: 4, fontSize: 13, background: '#fff' }}>
                <option value="">All disciplines</option>
                {disciplines.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
              <div style={{ display: 'flex', border: `1px solid ${LINE}`, borderRadius: 4, overflow: 'hidden' }}>
                {([['all', 'All'], ['ready', 'Ready'], ['in_progress', 'In progress'], ['to_chase', 'To chase']] as [Seg, string][]).map(([k, l]) => (
                  <button key={k} onClick={() => setSeg(k)}
                    style={{ padding: '7px 10px', fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
                             background: seg === k ? NAVY : '#fff', color: seg === k ? '#fff' : MUTE }}>{l}</button>
                ))}
              </div>
            </div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: SURF, borderBottom: `1px solid ${LINE}`, ...CAP, color: MUTE }}>
                  <th style={{ padding: '10px 16px' }}>Practice</th>
                  <th style={{ padding: '10px 16px' }}>Provider</th>
                  <th style={{ padding: '10px 16px' }}>Discipline</th>
                  <th style={{ padding: '10px 16px' }}>Town</th>
                  <th style={{ padding: '10px 16px' }}>Contract</th>
                  <th style={{ padding: '10px 16px', textAlign: 'center' }}>AFA</th>
                  <th style={{ padding: '10px 16px', textAlign: 'center' }}>QC</th>
                  <th style={{ padding: '10px 16px', textAlign: 'center' }}>Ready</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td colSpan={8} style={{ padding: 40, textAlign: 'center', color: '#757681' }}>
                    <Loader2 className="animate-spin" style={{ margin: '0 auto' }} /></td></tr>
                )}
                {!loading && filtered.slice(0, 300).map(p => {
                  const rd = readiness(p)
                  const rdColor = rd === 'ready' ? GREEN : rd === 'to_chase' ? RED : AMBER
                  return (
                    <tr key={p.id} style={{ borderBottom: `1px solid #eef0f2` }}>
                      <td style={{ padding: '9px 16px', fontFamily: 'monospace', fontSize: 12 }}>{p.practice_number}</td>
                      <td style={{ padding: '9px 16px', fontWeight: 600 }}>{p.name}</td>
                      <td style={{ padding: '9px 16px', color: MUTE }}>{p.discipline}</td>
                      <td style={{ padding: '9px 16px', color: MUTE }}>{p.town}</td>
                      <td style={{ padding: '9px 16px', color: MUTE }}>{p.contract_status || '—'}</td>
                      <td style={{ padding: '9px 16px', textAlign: 'center' }}>{dot(p.afa_registered === 'Yes' ? GREEN : p.afa_registered === 'Pending' ? AMBER : LINE)}</td>
                      <td style={{ padding: '9px 16px', textAlign: 'center' }}>{dot(p.qc_confirmed ? GREEN : LINE)}</td>
                      <td style={{ padding: '9px 16px', textAlign: 'center' }}>{dot(rdColor)}</td>
                    </tr>
                  )
                })}
                {!loading && filtered.length === 0 && (
                  <tr><td colSpan={8} style={{ padding: 30, textAlign: 'center', color: '#757681' }}>No providers match these filters.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  )
}
