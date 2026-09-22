'use client'

/**
 * /cfo/jobs — the scheduled-job switchboard (CFO 2026-09-09).
 *
 * Every job Omni runs on a timer, in plain English, with a switch the CFO owns.
 * The switch is a flag in Omni's database — it never touches the server's own
 * schedule files, so a page bug can't run anything on the box, and a deploy
 * can't reach in and undo his choice (the nine-day hours-reminders outage).
 *
 * Protected jobs — backups, watchdogs, anything whose silence hides a fault —
 * show a padlock and refuse. His-eyes-only, same lock as the build log.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { useTheme } from '@/contexts/ThemeContext'
import { API_BASE } from '@/lib/api'
import { AlertTriangle, Lock, Loader2, Power, RefreshCw } from 'lucide-react'

interface Job {
  name: string; what_it_does: string; who_it_affects: string
  if_switched_off: string; category: string
  is_enabled: boolean; is_protected: boolean
  off_until: string | null; off_reason: string
  host_present: boolean; host_disabled: boolean
  effective_on: boolean; why: string
  last_run: string | null; last_status: string | null; drift: boolean
}
interface Resp {
  jobs: Job[]
  counts: { total: number; running: number; switched_off: number; protected: number; drift: number }
}

function token(): string | null {
  try { const t = localStorage.getItem('alpha_token'); return t ? `Token ${t}` : null } catch { return null }
}

export default function JobsPage() {
  const { theme: T } = useTheme()
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [state, setState] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    const auth = token()
    if (!auth) { setLoading(false); setDenied(true); return }
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/cfo/jobs/${state ? `?state=${state}` : ''}`, { headers: { Authorization: auth } })
      if (r.status === 403 || r.status === 401) { setDenied(true); setData(null) }
      else if (r.ok) { setDenied(false); setData(await r.json()) }
    } catch { /* keep last */ }
    finally { setLoading(false) }
  }, [state])

  useEffect(() => { void load() }, [load])

  const toggle = useCallback(async (j: Job) => {
    if (j.is_protected) return
    const auth = token(); if (!auth) return
    const turningOff = j.is_enabled
    let reason = ''
    if (turningOff) {
      reason = window.prompt(`Switch OFF "${j.name}"?\n\n${j.if_switched_off}\n\nIt turns back on by itself in 7 days. A short reason (optional):`, '') ?? '__CANCEL__'
      if (reason === '__CANCEL__') return
    }
    setBusy(j.name)
    try {
      const r = await fetch(`${API_BASE}/cfo/jobs/toggle/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: auth },
        body: JSON.stringify({ name: j.name, on: !j.is_enabled, reason }),
      })
      if (r.ok) await load()
    } finally { setBusy('') }
  }, [load])

  if (denied) {
    return (
      <div>
        <TopBar title="Scheduled jobs" />
        <div style={{ padding: 40, textAlign: 'center', color: T.t2 }}>
          <AlertTriangle className="w-8 h-8" style={{ margin: '0 auto 12px', color: T.wr }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>This screen is the CFO&rsquo;s own.</div>
          <div style={{ fontSize: 13, marginTop: 8, maxWidth: 420, marginInline: 'auto', lineHeight: 1.6 }}>
            It opens only for <b>pganesharajah</b>. Sign in as yourself, not the shared excoboard account.
          </div>
        </div>
      </div>
    )
  }

  const fmt = (s: string | null) => s ? new Date(s).toLocaleString('en-GB',
    { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Gaborone' }) : 'never'

  return (
    <div>
      <TopBar title="Scheduled jobs" />
      <div style={{ padding: '18px 20px 40px', maxWidth: 1120, margin: '0 auto' }}>

        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
          <Stat T={T} label="Running" value={data?.counts.running ?? 0} c={T.ok} />
          <Stat T={T} label="Switched off" value={data?.counts.switched_off ?? 0} c={T.wr} />
          <Stat T={T} label="Protected" value={data?.counts.protected ?? 0} c={T.t2} />
          {!!data?.counts.drift && <Stat T={T} label="Not matching the server" value={data.counts.drift} c={T.er} />}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            {['', 'on', 'off', 'protected', 'drift'].map(s => (
              <button key={s || 'all'} onClick={() => setState(s)}
                      style={{
                        fontSize: 12, padding: '5px 11px', borderRadius: 999, cursor: 'pointer',
                        border: `1px solid ${state === s ? T.orange : T.cardBdr}`,
                        background: state === s ? T.oL : 'transparent',
                        color: state === s ? T.orangeText : T.t2, fontWeight: state === s ? 600 : 400,
                      }}>{s === '' ? 'All' : s === 'on' ? 'Running' : s === 'off' ? 'Off' : s === 'protected' ? 'Protected' : 'Mismatch'}</button>
            ))}
            <button onClick={load} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: T.t3 }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            </button>
          </div>
        </div>

        <Card style={{ overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead>
                <tr style={{ color: T.t3, textAlign: 'left' }}>
                  {['Job', 'What it does', 'Last run', 'Now', ''].map(h =>
                    <th key={h} style={{ padding: '9px 12px', borderBottom: `2px solid ${T.cardBdr}`, whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {(data?.jobs || []).map(j => (
                  <tr key={j.name} style={{ borderBottom: `1px solid ${T.cardBdr}`, background: j.drift ? T.erB : 'transparent' }}>
                    <td style={{ padding: '9px 12px', color: T.text, whiteSpace: 'nowrap', fontWeight: 600 }}>
                      {j.is_protected && <Lock className="w-3 h-3" style={{ display: 'inline', marginRight: 5, color: T.t3, verticalAlign: -1 }} />}
                      {j.name}
                    </td>
                    <td style={{ padding: '9px 12px', color: T.t2, maxWidth: 340, lineHeight: 1.5 }}>
                      {j.what_it_does}
                      {j.who_it_affects && <div style={{ fontSize: 11, color: T.t3, marginTop: 2 }}>{j.who_it_affects}</div>}
                    </td>
                    <td style={{ padding: '9px 12px', color: j.last_status === 'failed' ? T.er : T.t3, whiteSpace: 'nowrap' }}>
                      {fmt(j.last_run)}
                    </td>
                    <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                      <span style={{
                        fontSize: 11.5, padding: '2px 9px', borderRadius: 999, fontWeight: 600,
                        background: j.effective_on ? T.okB : T.wrB, color: j.effective_on ? T.ok : T.wr,
                      }}>{j.effective_on ? 'ON' : 'OFF'}</span>
                      {j.why !== 'running' && <div style={{ fontSize: 10.5, color: T.t3, marginTop: 3 }}>{j.why}</div>}
                    </td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                      {j.is_protected
                        ? <span title="Protected — cannot be switched off" style={{ color: T.t3, display: 'inline-flex' }}><Lock className="w-4 h-4" /></span>
                        : (
                          <button onClick={() => toggle(j)} disabled={busy === j.name}
                                  title={j.is_enabled ? 'Switch off' : 'Switch on'}
                                  style={{
                                    border: `1px solid ${j.is_enabled ? T.ok : T.cardBdr}`, cursor: 'pointer',
                                    background: j.is_enabled ? T.okB : 'transparent', borderRadius: 999,
                                    padding: '4px 10px', display: 'inline-flex', alignItems: 'center', gap: 5,
                                    color: j.is_enabled ? T.ok : T.t3, fontSize: 11.5, fontWeight: 600,
                                  }}>
                            {busy === j.name ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Power className="w-3.5 h-3.5" />}
                            {j.is_enabled ? 'On' : 'Off'}
                          </button>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div style={{ marginTop: 16, fontSize: 11.5, color: T.t3, lineHeight: 1.65 }}>
          The switch is a setting inside Omni — it never edits the server, so it takes effect at the
          next run and a software update can never undo it. A job you switch off turns itself back on
          after 7 days, so nothing stays off by accident. Padlocked jobs — backups, safety checks, and
          anything whose silence would hide a problem — cannot be switched off at all. A row in red is
          on here but missing on the server; that is the exact fault this screen exists to catch.
        </div>
      </div>
    </div>
  )
}

function Stat({ T, label, value, c }: { T: any; label: string; value: number; c: string }) {
  return (
    <Card style={{ padding: '10px 14px', minWidth: 110 }}>
      <div style={{ fontSize: 10.5, letterSpacing: .3, textTransform: 'uppercase', fontWeight: 700, color: T.t3, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1, color: c }}>{value}</div>
    </Card>
  )
}
