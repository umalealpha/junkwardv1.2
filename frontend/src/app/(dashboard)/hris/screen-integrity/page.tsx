'use client'

/**
 * /hris/screen-integrity — the CFO's Screen-Integrity monitor (2026-09-06).
 *
 * Pulls the frozen-screen / weight-on-a-key Time Doctor exceptions (heavy
 * typing on a screen that never changes — physically impossible for real work)
 * from the stored daily sweep so the page is instant. A date + "Re-scan (live)"
 * button pulls one day straight from Time Doctor and re-persists it.
 *
 * Gated to HR / admin / CEO / superadmin (the CFO resolves to 'hr'); everyone
 * else is bounced. Privacy (AD-POL-AI-GOV-001): names + counts only.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ChevronLeft, ShieldAlert, RefreshCw, MousePointer2Off, Keyboard,
         AlertTriangle, Eye, CheckCircle2, CircleSlash } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const RED = '#B91C1C'
const AMBER = '#B45309'
const GREEN = '#0F7B3F'

interface Flag {
  name: string
  suspicion: 'watch' | 'suspicious'
  shots: number
  frozen_typing_pct: number
  frozen_typing_hours: number
  mouse_dead_pct: number
  identical_pct: number
  reasons: string[]
}
interface Scan {
  day: string
  people_checked: number
  suspicious: number
  watch: number
  status: 'ok' | 'no_data' | 'failed'
  note: string
  ran_at: string | null
  flags: Flag[]
}
interface ListResp {
  scans: Scan[]
  totals: { days: number; suspicious: number; watch: number }
}

function yesterdayISO(): string {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return localYmd(d)
}

const SUSP_TONE: Record<Flag['suspicion'], string> = { suspicious: RED, watch: AMBER }

export default function ScreenIntegrityPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [resp, setResp] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(14)
  const [rescanDate, setRescanDate] = useState(yesterdayISO())
  const [rescanning, setRescanning] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  const card = theme.card
  const ink = theme.text
  const mut = theme.t3
  const line = theme.cardBdr
  const pageBg = theme.bg
  const inputBg = theme.input

  const load = async (n = days) => {
    setLoading(true)
    try {
      const r = await apiFetch<ListResp>(`/hris/screen-integrity/?days=${n}`)
      setResp(r)
    } catch (e: any) {
      setMsg(e?.message || 'Could not load.')
    } finally {
      setLoading(false)
    }
  }

  const rescan = async () => {
    setRescanning(true)
    setMsg(null)
    try {
      const s = await apiFetch<Scan>('/hris/screen-integrity/rescan/', {
        method: 'POST',
        body: JSON.stringify({ date: rescanDate }),
      })
      setMsg(`Re-scanned ${s.day}: ${s.suspicious} suspicious, ${s.watch} to watch, of ${s.people_checked} checked.`)
      setOpen(o => ({ ...o, [s.day]: true }))
      await load()
    } catch (e: any) {
      setMsg(e?.message || 'Re-scan failed.')
    } finally {
      setRescanning(false)
    }
  }

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed === true) void load(days)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, days])

  const totals = resp?.totals
  const scans = useMemo(() => resp?.scans ?? [], [resp])

  if (allowed !== true) {
    return (
      <div style={{ minHeight: '100vh', background: pageBg }}>
        <TopBar />
        <div style={{ padding: 40, color: mut, fontFamily: 'Georgia, serif' }}>
          {allowed === false ? 'Redirecting…' : 'Checking access…'}
        </div>
      </div>
    )
  }

  return (
    <div style={{ minHeight: '100vh', background: pageBg }}>
      <TopBar />
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '24px 20px 64px',
                    fontFamily: 'Georgia, "Book Antiqua", serif' }}>
        <button onClick={() => router.back()}
                style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none',
                         border: 'none', color: mut, cursor: 'pointer', fontSize: 13, marginBottom: 12 }}>
          <ChevronLeft size={16} /> Back
        </button>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
          <div style={{ width: 38, height: 38, borderRadius: 10, background: NAVY,
                        display: 'grid', placeItems: 'center', flexShrink: 0 }}>
            <ShieldAlert size={20} color={ORANGE} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, color: ink, fontWeight: 700 }}>Screen-Integrity Monitor</h1>
            <p style={{ margin: '2px 0 0', fontSize: 13, color: mut }}>
              Heavy typing on a screen that never changes — the weight-on-a-key cheat. Flags for review; never docks pay.
            </p>
          </div>
        </div>

        {/* Controls */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10,
                      margin: '18px 0', padding: 14, background: card, border: `1px solid ${line}`,
                      borderRadius: 12 }}>
          <label style={{ fontSize: 13, color: mut }}>Window</label>
          <select value={days} onChange={e => setDays(Number(e.target.value))}
                  style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${line}`,
                           background: inputBg, color: ink, fontSize: 13 }}>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
            <option value={60}>Last 60 days</option>
          </select>
          <div style={{ flex: 1 }} />
          <label style={{ fontSize: 13, color: mut }}>Re-scan a day (live)</label>
          <input type="date" value={rescanDate} max={yesterdayISO()}
                 onChange={e => setRescanDate(e.target.value)}
                 style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${line}`,
                          background: inputBg, color: ink, fontSize: 13 }} />
          <button onClick={rescan} disabled={rescanning}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px',
                           borderRadius: 8, border: 'none', background: NAVY, color: '#fff',
                           fontSize: 13, cursor: rescanning ? 'wait' : 'pointer', opacity: rescanning ? 0.7 : 1 }}>
            <RefreshCw size={14} style={{ animation: rescanning ? 'spin 1s linear infinite' : 'none' }} />
            {rescanning ? 'Pulling…' : 'Re-scan'}
          </button>
        </div>

        {msg && (
          <div style={{ padding: '10px 14px', marginBottom: 14, borderRadius: 10, fontSize: 13,
                        background: theme.inB, color: ink, border: `1px solid ${line}` }}>
            {msg}
          </div>
        )}

        {/* Totals */}
        {totals && (
          <div style={{ display: 'flex', gap: 12, marginBottom: 18 }}>
            <Tile tone={RED} label="Suspicious" value={totals.suspicious} sub={`over ${totals.days} day(s)`} card={card} line={line} mut={mut} ink={ink} />
            <Tile tone={AMBER} label="To watch" value={totals.watch} sub="below full gate" card={card} line={line} mut={mut} ink={ink} />
            <Tile tone={GREEN} label="Days checked" value={totals.days} sub="silence ≠ unchecked" card={card} line={line} mut={mut} ink={ink} />
          </div>
        )}

        {loading && <p style={{ color: mut, fontSize: 13 }}>Loading…</p>}
        {!loading && scans.length === 0 && (
          <p style={{ color: mut, fontSize: 13 }}>No sweeps stored yet. Re-scan a day to pull one live.</p>
        )}

        {/* Per-day */}
        {scans.map(s => {
          const isOpen = open[s.day] ?? (s.suspicious > 0)
          const hasFlags = s.flags.length > 0
          return (
            <div key={s.day} style={{ background: card, border: `1px solid ${line}`, borderRadius: 12,
                                       marginBottom: 12, overflow: 'hidden' }}>
              <button onClick={() => setOpen(o => ({ ...o, [s.day]: !isOpen }))}
                      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12,
                               padding: '14px 16px', background: 'none', border: 'none',
                               cursor: hasFlags ? 'pointer' : 'default', textAlign: 'left' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: ink, minWidth: 108 }}>{s.day}</div>
                {s.status === 'failed' ? (
                  <span style={{ fontSize: 13, color: AMBER, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <AlertTriangle size={15} /> Pull failed — {s.note || 'will retry'}
                  </span>
                ) : s.status === 'no_data' ? (
                  <span style={{ fontSize: 13, color: mut, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <CircleSlash size={15} /> No screenshots this day
                  </span>
                ) : hasFlags ? (
                  <span style={{ fontSize: 13, color: ink, display: 'flex', gap: 10, alignItems: 'center' }}>
                    {s.suspicious > 0 && <Badge tone={RED} text={`${s.suspicious} suspicious`} />}
                    {s.watch > 0 && <Badge tone={AMBER} text={`${s.watch} to watch`} />}
                    <span style={{ color: mut }}>· {s.people_checked} checked</span>
                  </span>
                ) : (
                  <span style={{ fontSize: 13, color: GREEN, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <CheckCircle2 size={15} /> Checked {s.people_checked} — nothing flagged
                  </span>
                )}
                <div style={{ flex: 1 }} />
                {hasFlags && <Eye size={16} color={mut} />}
              </button>

              {isOpen && hasFlags && (
                <div style={{ borderTop: `1px solid ${line}`, padding: '4px 8px 8px' }}>
                  {s.flags.map((f, i) => (
                    <div key={i} style={{ padding: '12px 12px', borderTop: i ? `1px solid ${line}` : 'none' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 14, fontWeight: 700, color: ink }}>{f.name}</span>
                        <Badge tone={SUSP_TONE[f.suspicion]} text={f.suspicion} />
                      </div>
                      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 8, fontSize: 12.5, color: mut }}>
                        <Metric icon={<Keyboard size={13} />} label="frozen-typing" value={`${f.frozen_typing_pct}%`} strong tone={SUSP_TONE[f.suspicion]} />
                        <Metric label="credited" value={`${f.frozen_typing_hours.toFixed(1)}h`} />
                        <Metric icon={<MousePointer2Off size={13} />} label="mouse dead" value={`${f.mouse_dead_pct}%`} />
                        <Metric label="screens identical" value={`${f.identical_pct}%`} />
                        <Metric label="shots" value={String(f.shots)} />
                      </div>
                      {f.reasons?.length > 0 && (
                        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: ink, lineHeight: 1.5 }}>
                          {f.reasons.map((r, j) => <li key={j}>{r}</li>)}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}

        <p style={{ marginTop: 20, fontSize: 12, color: mut, lineHeight: 1.5 }}>
          The nightly sweep runs off Time Doctor screenshot metadata — keystroke, mouse and
          picture-fingerprint counts only. No screen contents, window titles or images ever leave the system.
          A flag is a signal to ask the person to explain their day, not proof and never an automatic deduction.
        </p>
      </div>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  )
}

function Tile({ tone, label, value, sub, card, line, mut, ink }: any) {
  return (
    <div style={{ flex: 1, background: card, border: `1px solid ${line}`, borderRadius: 12, padding: 14 }}>
      <div style={{ fontSize: 12, color: mut }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: tone, lineHeight: 1.1, margin: '2px 0' }}>{value}</div>
      <div style={{ fontSize: 11.5, color: mut }}>{sub}</div>
    </div>
  )
}

function Badge({ tone, text }: { tone: string; text: string }) {
  return (
    <span style={{ fontSize: 11.5, fontWeight: 700, color: '#fff', background: tone,
                   padding: '2px 8px', borderRadius: 999, textTransform: 'capitalize' }}>{text}</span>
  )
}

function Metric({ icon, label, value, strong, tone }: any) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      {icon}
      <span>{label}</span>
      <b style={{ color: strong ? tone : 'inherit', fontWeight: 700 }}>{value}</b>
    </span>
  )
}
