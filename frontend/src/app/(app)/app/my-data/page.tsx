'use client'
import { useState } from 'react'
import { LogOut, Trash2, ShieldCheck } from 'lucide-react'
import { afetch, clearAppToken } from '../../api'
import { C, serif, card } from '../../ui'

const HOLDS: [string, string][] = [
  ['Your name and work email', 'So you can sign in and your work can find you'],
  ['This phone’s sign-in', 'Kept for 30 days, or until you sign the phone out'],
  ['Your approvals, tasks and requests', 'To run the business and keep an audit trail'],
  ['Your payslips, leave and expenses', 'So you can see your own'],
  ['Which screens you open', 'To see which parts are used. Deleted automatically after 180 days'],
  ['Your alert subscription', 'To send the morning summary. Gone when you turn alerts off'],
]

export default function MyData() {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [asking, setAsking] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [note, setNote] = useState('')

  async function signOutEverywhere() {
    if (busy) return
    setBusy('out'); setMsg('')
    try {
      await afetch('/auth/devices/sign-out-everywhere/', { method: 'POST' })
      clearAppToken()
      window.location.href = '/app/login'
    } catch {
      setBusy(''); setMsg('That did not go through. Try again, or ask IT.')
    }
  }

  async function sendRequest() {
    if (busy) return
    setBusy('req'); setMsg('')
    try {
      const r = await afetch<{ received: boolean; contact: string }>('/app/data-request/', {
        method: 'POST', body: JSON.stringify({ confirm: confirm.trim().toUpperCase(), note }),
      })
      setAsking(false); setConfirm(''); setNote('')
      setMsg(`Your request has gone to ${r.contact}. They will come back to you directly.`)
    } catch {
      setMsg('Type DELETE in the box to confirm, then try again.')
    } finally { setBusy('') }
  }

  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <h1 style={{ fontFamily: serif, fontSize: 26, fontWeight: 700, color: C.head, margin: 0 }}>Your data</h1>
        <p style={{ fontSize: 14, color: C.inkSoft, margin: '6px 0 0' }}>
          What Omni holds about you, and what you can do about it.
        </p>
      </div>

      <div style={{ ...card, padding: 0, overflow: 'hidden' }}>
        {HOLDS.map(([what, why], i) => (
          <div key={what} style={{ padding: '13px 16px', borderTop: i ? `1px solid ${C.line}` : undefined }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.head }}>{what}</div>
            <div style={{ fontSize: 13, color: C.inkSoft, marginTop: 2 }}>{why}</div>
          </div>
        ))}
      </div>

      <div style={{ ...card, padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ShieldCheck size={20} color={C.head} aria-hidden="true" />
          <div style={{ fontFamily: serif, fontSize: 18, fontWeight: 700, color: C.head }}>Sign out everywhere</div>
        </div>
        <p style={{ fontSize: 14, color: C.inkSoft, margin: 0 }}>
          Signs Omni out on every phone you are signed in on, including this one. Use this if a phone
          is lost or stolen — then tell IT the same day.
        </p>
        <button onClick={signOutEverywhere} disabled={!!busy} className="oa-press"
          style={{ minHeight: 48, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card,
                   color: C.head, fontWeight: 600, fontSize: 15, display: 'flex', alignItems: 'center',
                   justifyContent: 'center', gap: 8, cursor: 'pointer' }}>
          <LogOut size={18} aria-hidden="true" />{busy === 'out' ? 'Signing out…' : 'Sign out on every phone'}
        </button>
      </div>

      <div style={{ ...card, padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Trash2 size={20} color={C.red} aria-hidden="true" />
          <div style={{ fontFamily: serif, fontSize: 18, fontWeight: 700, color: C.head }}>Ask for your data to be deleted</div>
        </div>
        <p style={{ fontSize: 14, color: C.inkSoft, margin: 0 }}>
          Some of what Omni holds is an employment record Alpha Direct is required by law to keep, and
          your approvals are part of the audit trail behind real payments — so this is a request to a
          person, not a button that wipes everything. IT and HR will tell you exactly what can and
          cannot be removed.
        </p>

        {!asking ? (
          <button onClick={() => setAsking(true)} className="oa-press"
            style={{ minHeight: 48, borderRadius: 12, border: `1px solid ${C.red}`, background: C.card,
                     color: C.red, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
            Request deletion of my data
          </button>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label htmlFor="md-note" style={{ fontSize: 13, fontWeight: 600, color: C.head }}>
              Anything you want them to know (optional)
            </label>
            <textarea id="md-note" value={note} onChange={e => setNote(e.target.value)} rows={3}
              style={{ borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.ink,
                       padding: 12, fontSize: 15, fontFamily: 'inherit' }} />
            <label htmlFor="md-confirm" style={{ fontSize: 13, fontWeight: 600, color: C.head }}>
              Type DELETE to confirm
            </label>
            <input id="md-confirm" value={confirm} onChange={e => setConfirm(e.target.value)}
              autoCapitalize="characters" placeholder="DELETE"
              style={{ minHeight: 48, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card,
                       color: C.ink, padding: '0 12px', fontSize: 15 }} />
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={sendRequest} disabled={!!busy} className="oa-press"
                style={{ flex: 1, minHeight: 48, borderRadius: 12, border: 'none', background: C.red,
                         color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer' }}>
                {busy === 'req' ? 'Sending…' : 'Send request'}
              </button>
              <button onClick={() => { setAsking(false); setConfirm(''); setNote('') }} className="oa-press"
                style={{ flex: 1, minHeight: 48, borderRadius: 12, border: `1px solid ${C.line}`,
                         background: C.card, color: C.head, fontWeight: 600, fontSize: 15, cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {msg && (
        <div role="status" style={{ ...card, padding: 14, fontSize: 14, color: C.head }}>{msg}</div>
      )}
    </div>
  )
}
