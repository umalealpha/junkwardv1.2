'use client'

/** /m/team — family & friend teams (CFO 2026-09-08, "6 is good").
 *
 * Solo streaks only motivate the already-motivated. A weekly target shared
 * with your spouse or a few friends is a different kind of pressure, and every
 * team recruits its own members.
 *
 * A member sees ONLY their own team: the server scopes every read to the
 * signed-in token, and there is deliberately no read-a-team-by-id path. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Users, Copy, Check, LogOut } from 'lucide-react'
import { getTeam, createTeam, joinTeam, leaveTeam, ApiError, type TeamResp } from '../../api'
import { C, serif, sans, h, card, pill, headerPad } from '../../ui'

export default function CustomerTeam() {
  const router = useRouter()
  const [data, setData] = useState<TeamResp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [copied, setCopied] = useState(false)
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    getTeam().then(d => { setData(d); setError(null) })
      .catch(e => setError(e instanceof ApiError ? e.message : 'Could not load your team.'))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current) }, [])

  // Every action reports its real reason on failure — a full team, a bad code
  // and a lost connection are three different things to the member.
  const run = async (fn: () => Promise<TeamResp>) => {
    if (busy) return
    setBusy(true); setError(null)
    try { setData(await fn()) }
    catch (e) { setError(e instanceof ApiError ? e.message : 'That did not work — please try again.') }
    finally { setBusy(false) }
  }

  const copyCode = async () => {
    if (!data?.joinCode) return
    // Only claim "Copied" when it actually copied. Clipboard access is refused
    // in plenty of real situations (no secure context, permission denied), and
    // saying Copied when nothing was is a small lie the member acts on - they
    // paste an empty message to their family. The code is on screen either way.
    try {
      await navigator.clipboard.writeText(data.joinCode)
    } catch {
      setError('Could not copy - long-press the code to copy it yourself.')
      return
    }
    setCopied(true)
    if (copyTimer.current) clearTimeout(copyTimer.current)
    copyTimer.current = setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 10, padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <button onClick={() => router.push('/m')} aria-label="Back" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, display: 'flex' }}>
          <ArrowLeft size={20} style={{ color: C.ink }} />
        </button>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>My Team</span>
      </header>

      <main style={{ padding: 16 }}>
        {error && (
          <div role="alert" style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', fontSize: 13, padding: '12px 14px', borderRadius: 12, marginBottom: 14 }}>{error}</div>
        )}
        {!data && !error && (
          <div style={{ ...card, padding: 24, textAlign: 'center', color: C.inkSoft, fontSize: 14 }}>Loading…</div>
        )}

        {data && !data.inTeam && (
          <>
            <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 24, padding: 24, color: '#fff' }}>
              <Users size={26} style={{ color: C.tealLight }} />
              <h1 style={{ ...h(28), color: '#fff', marginTop: 12 }}>Drive better,<br />together</h1>
              <p style={{ color: 'rgba(255,255,255,0.65)', fontSize: 14, lineHeight: 1.5, margin: '10px 0 0' }}>
                Start a team with your family or friends — up to {data.maxSize} people — and see your combined
                safe-driving progress in one place.
              </p>
            </div>

            <div style={{ ...card, padding: 18, marginTop: 16 }}>
              <h2 style={{ ...h(20), margin: '0 0 10px' }}>Start a team</h2>
              <input value={name} onChange={e => setName(e.target.value)} maxLength={40}
                placeholder="Team name (e.g. Team Kgosi)" aria-label="Team name"
                style={inputStyle} />
              <button onClick={() => run(() => createTeam(name))} disabled={busy || !name.trim()}
                style={{ ...primaryBtn, opacity: busy || !name.trim() ? 0.55 : 1 }}>
                {busy ? 'Working…' : 'Create team'}
              </button>
            </div>

            <div style={{ ...card, padding: 18, marginTop: 14 }}>
              <h2 style={{ ...h(20), margin: '0 0 10px' }}>Join a team</h2>
              <p style={{ color: C.inkSoft, fontSize: 13, margin: '0 0 10px' }}>
                Ask whoever started the team for their team code.
              </p>
              <input value={code} onChange={e => setCode(e.target.value.toUpperCase())} maxLength={12}
                placeholder="Team code" aria-label="Team code"
                style={{ ...inputStyle, letterSpacing: '0.18em', fontWeight: 700 }} />
              <button onClick={() => run(() => joinTeam(code))} disabled={busy || !code.trim()}
                style={{ ...secondaryBtn, opacity: busy || !code.trim() ? 0.55 : 1 }}>
                {busy ? 'Working…' : 'Join team'}
              </button>
            </div>
          </>
        )}

        {data?.inTeam && (
          <>
            <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 24, padding: 24, color: '#fff' }}>
              <span style={pill('rgba(45,212,191,0.18)', C.tealLight)}>
                ◈ {data.size} of {data.maxSize} members
              </span>
              <h1 style={{ ...h(30), color: '#fff', marginTop: 14 }}>{data.teamName}</h1>
              <div style={{ display: 'flex', gap: 30, marginTop: 16 }}>
                <div><p style={lblW}>TEAM POINTS</p><p style={statW}>{(data.totalPoints ?? 0).toLocaleString()}</p></div>
                <div><p style={lblW}>TOTAL KM</p><p style={statW}>{data.totalKm ?? 0}</p></div>
                <div><p style={lblW}>TRIPS</p><p style={statW}>{data.totalTrips ?? 0}</p></div>
              </div>
            </div>

            <div style={{ ...card, padding: 18, marginTop: 16 }}>
              <p style={{ color: C.inkSoft, fontSize: 12, margin: 0 }}>TEAM CODE — share it to invite</p>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
                <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 28, letterSpacing: '0.16em', color: C.ink }}>{data.joinCode}</span>
                <button onClick={copyCode} aria-label="Copy team code"
                  style={{ ...secondaryBtn, width: 'auto', margin: 0, padding: '10px 16px', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  {copied ? <><Check size={16} /> Copied</> : <><Copy size={16} /> Copy</>}
                </button>
              </div>
              {data.full && (
                <p style={{ color: C.orangeDeep, fontSize: 12, margin: '10px 0 0' }}>
                  This team is full — {data.maxSize} is the limit.
                </p>
              )}
            </div>

            <h2 style={{ ...h(22), margin: '22px 0 12px' }}>Members</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(data.members ?? []).map((m, i) => (
                <div key={m.id} style={{ ...card, padding: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ width: 26, height: 26, borderRadius: 999, background: i === 0 ? C.teal : '#EEF0F3', color: i === 0 ? '#fff' : C.inkSoft, fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
                    <span style={{ fontWeight: 600, fontSize: 15, color: C.ink }}>{m.name}</span>
                  </div>
                  <span style={{ fontSize: 13, color: C.inkSoft }}>
                    <b style={{ color: C.ink }}>{m.points.toLocaleString()}</b> pts · {m.km} km
                  </span>
                </div>
              ))}
            </div>

            <button onClick={() => run(leaveTeam)} disabled={busy}
              style={{ ...secondaryBtn, marginTop: 22, color: '#B91C1C', borderColor: '#FECACA', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <LogOut size={16} /> Leave this team
            </button>
          </>
        )}
      </main>
      <div style={{ height: 90 }} />
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '13px 14px', borderRadius: 14,
  border: `1px solid ${C.line}`, fontSize: 15, fontFamily: sans, color: C.ink,
  background: C.surface, marginBottom: 10,
}
const primaryBtn: React.CSSProperties = {
  width: '100%', padding: '13px 20px', borderRadius: 999, border: 'none',
  background: `linear-gradient(135deg, ${C.tealLight}, ${C.teal})`, color: C.navy,
  fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans,
}
const secondaryBtn: React.CSSProperties = {
  width: '100%', padding: '12px 20px', borderRadius: 999,
  border: `1px solid ${C.line}`, background: C.card, color: C.ink,
  fontWeight: 600, fontSize: 15, cursor: 'pointer', fontFamily: sans,
}
const lblW: React.CSSProperties = { color: 'rgba(255,255,255,0.55)', fontSize: 10, letterSpacing: '0.1em', margin: 0 }
const statW: React.CSSProperties = { fontFamily: serif, fontWeight: 800, fontSize: 22, color: '#fff', margin: '3px 0 0' }
