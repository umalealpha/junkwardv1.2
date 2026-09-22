'use client'

/** /app/roster-flag · /m/staff/roster-flag — a line manager says "this person on
 * my list isn't right" (CFO pick 2026-09-03). A FLAG, not an edit: HR decides.
 * Same endpoints the desktop monthly-return roster uses:
 *   GET  /hris/api/manager-return/   → { team:[{employee_id, name, job_title, flag}], no_team? }
 *                                      (403 when the login has no employee record)
 *   GET  /hris/api/roster-flags/     → { flags:[...] }  the flags I raised
 *   POST /hris/api/roster-flags/     { employee_id, kind, note }
 *        kinds: not_mine · resigned · long_leave · wrong_info · other  (hris/roster_flag_models.FlagKind)
 * The person STAYS on the list until HR decides (CFO correction 2026-07-26). */
import { useCallback, useEffect, useState } from 'react'
import { Flag } from 'lucide-react'
import { hfetch, reauthOn401 } from '@/app/(customer)/api'
import { C, pill, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  inputStyle, labelStyle, primaryBtn, rawHrisFetch,
} from './StaffFormKit'

interface Person { employee_id: string; name: string; job_title: string; flag?: { kind?: string; kind_label?: string } | null }
interface RosterFlag {
  id: string; employee: string; employee_id: string; job_title: string; kind: string; kind_label: string
  note: string; status: string; status_label: string; raised_at: string | null; hr_note: string
}
const KINDS: { v: string; label: string }[] = [
  { v: 'not_mine', label: 'Not reporting to me' },
  { v: 'resigned', label: 'Resigned / left' },
  { v: 'long_leave', label: 'On long leave' },
  { v: 'wrong_info', label: 'Wrong title or details' },
  { v: 'other', label: 'Something else' },
]
const NOT_MANAGER = 'Only line managers can flag.'

export default function RosterFlagScreen() {
  const base = useStaffBase()
  const [team, setTeam] = useState<Person[] | null>(null)
  const [blocked, setBlocked] = useState<string | null>(null)   // 403 / no team
  const [flags, setFlags] = useState<RosterFlag[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [pick, setPick] = useState<Person | null>(null)
  const [kind, setKind] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [sent, setSent] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null); setBlocked(null)
    Promise.all([
      rawHrisFetch('/manager-return/'),
      hfetch<{ flags: RosterFlag[] }>('/roster-flags/').catch(() => ({ flags: [] as RosterFlag[] })),
    ]).then(([r, f]) => {
      setFlags(f.flags || [])
      if (r.status === 403 || r.body.no_team) { setBlocked(NOT_MANAGER); setTeam([]); return }
      if (!r.ok) { setTeam([]); setLoadErr(formatServerErrors(r.body, r.status)); return }
      const rows = Array.isArray(r.body.team) ? (r.body.team as Person[]) : []
      setTeam(rows)
    }).catch(e => { if (!reauthOn401(e)) { setTeam(t => t ?? []); setLoadErr(errText(e, 'Could not load your team.')) } })
  }, [])
  useEffect(() => { load() }, [load])

  function open(p: Person) { setPick(p); setKind(''); setNote(''); setServerErr(null); setSent(null) }

  async function submit() {
    if (!pick) return
    setServerErr(null)
    if (!kind) { show('Choose a reason.'); return }
    setBusy(true)
    try {
      // Server reads data.get('employee_id'), data.get('kind'), data.get('note').
      const r = await rawHrisFetch('/roster-flags/', { method: 'POST', body: JSON.stringify({ employee_id: pick.employee_id, kind, note: note.trim() }) })
      if (r.status === 403) { setServerErr(`${NOT_MANAGER}${r.body.detail ? `\n${String(r.body.detail)}` : ''}`); return }
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      setSent(pick.name); setPick(null); load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not send the flag.')) }
    finally { setBusy(false) }
  }

  const openFlags = flags.filter(f => f.status === 'open')

  return (
    <ScreenFrame title="Flag my roster" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>
        Someone on your list who shouldn’t be — left, moved teams, on long leave, or the details are wrong? Tap them and tell HR. They stay on your list until HR decides.
      </p>
      {sent && <ServerMessage tone="ok" text={`Sent to HR about ${sent}. They stay on your list until HR decides.`} />}
      {blocked && <ServerMessage text={blocked} />}

      {team === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {team && team.length > 0 && (
        <Card style={{ padding: 8 }}>
          <ul aria-label="My team" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {team.map((p, i) => (
              <li key={p.employee_id} style={{ borderTop: i ? `1px solid ${C.line}` : 'none' }}>
                <button onClick={() => open(p)} style={{ width: '100%', minHeight: 56, display: 'flex', alignItems: 'center', gap: 12, padding: '10px 10px', background: 'none', border: 'none', textAlign: 'left', cursor: 'pointer' }}>
                  <span style={{ flex: 1 }}>
                    <b style={{ color: C.ink, fontSize: 14.5, display: 'block' }}>{p.name}</b>
                    <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{p.job_title || '—'}</span>
                    {p.flag && <span style={{ ...pill('#FEF3C7', '#92400E'), marginLeft: 8 }}>Waiting on HR</span>}
                  </span>
                  <Flag size={18} style={{ color: C.head, flexShrink: 0 }} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {openFlags.length > 0 && (
        <>
          <b style={{ color: C.ink, fontSize: 15 }}>Waiting on HR ({openFlags.length})</b>
          {openFlags.map(f => (
            <Card key={f.id} style={{ padding: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <b style={{ color: C.ink, fontSize: 14.5 }}>{f.employee}</b>
                <span style={pill('#FEF3C7', '#92400E')}>{f.status_label}</span>
              </div>
              <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>{f.kind_label}{f.note ? ` · “${f.note}”` : ''}{f.raised_at ? ` · ${new Date(f.raised_at).toLocaleDateString()}` : ''}</p>
            </Card>
          ))}
        </>
      )}

      {pick && (
        <div role="dialog" aria-modal="true" aria-labelledby="flag-title" style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 65 }} onClick={() => !busy && setPick(null)}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px calc(24px + env(safe-area-inset-bottom, 0px))' }}>
            <h2 id="flag-title" style={{ fontFamily: serif, fontSize: 18, color: C.ink, margin: 0 }}>Flag {pick.name}</h2>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 12px' }}>{pick.job_title || 'No title on record'} · goes to HR, who decide. Nothing changes until they do.</p>
            <div role="radiogroup" aria-label="Reason" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {KINDS.map(k => {
                const on = kind === k.v
                return (
                  <button key={k.v} role="radio" aria-checked={on} onClick={() => setKind(k.v)}
                    style={{ minHeight: 44, padding: '10px 14px', borderRadius: 999, cursor: 'pointer', fontWeight: 700, fontSize: 13,
                      border: on ? 'none' : `1px solid ${C.line}`, background: on ? C.navy : '#fff', color: on ? '#fff' : C.ink }}>
                    {k.label}
                  </button>
                )
              })}
            </div>
            <label htmlFor="flag-note" style={labelStyle}>Note for HR (optional)</label>
            <textarea id="flag-note" value={note} onChange={e => setNote(e.target.value)} rows={3}
              placeholder="e.g. Moved to Claims in July" style={{ ...inputStyle, resize: 'vertical' }} />
            {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
            <button onClick={submit} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 14, color: '#0D1B2A' }}>
              <Flag size={16} aria-hidden="true" /> {busy ? 'Sending…' : 'Send to HR'}
            </button>
            <button onClick={() => setPick(null)} disabled={busy} style={{ width: '100%', marginTop: 8, minHeight: 44, borderRadius: 999, border: 'none', background: 'none', color: C.inkSoft, fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>Cancel</button>
          </div>
        </div>
      )}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
