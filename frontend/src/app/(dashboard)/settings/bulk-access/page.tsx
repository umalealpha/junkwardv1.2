'use client'
import { useEffect, useState, type CSSProperties } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, getRBACRoles, parseAccessRequest, resolveBulkTargets, bulkAssignRole,
  type RBACRole, type BulkResolve, type BulkAssignResult,
} from '@/lib/api'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

function splitEmails(text: string): string[] {
  return Array.from(new Set(
    text.split(/[\s,;]+/).map(s => s.trim().toLowerCase()).filter(s => s.includes('@'))
  ))
}

export default function BulkAccessPage() {
  const router = useRouter()
  const [roles, setRoles] = useState<RBACRole[]>([])
  const [roleId, setRoleId] = useState('')
  const [nl, setNl] = useState('')
  const [emails, setEmails] = useState('')
  const [ariaBusy, setAriaBusy] = useState(false)
  const [ariaNote, setAriaNote] = useState('')
  const [preview, setPreview] = useState<BulkResolve | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<BulkAssignResult | null>(null)
  const [err, setErr] = useState('')

  if (typeof window !== 'undefined' && !getToken()) { router.replace('/login'); return null }

  useEffect(() => {
    getRBACRoles().then(r => setRoles((r.results || []).slice().sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => setErr('Could not load the list of roles — you may not have access to this page.'))
  }, [])

  async function askAria() {
    setErr(''); setAriaNote(''); setResult(null)
    if (!nl.trim()) { setAriaNote('Type what you want first, e.g. "give Leone the system tester role".'); return }
    setAriaBusy(true)
    try {
      const p = await parseAccessRequest(nl)
      if (p.role) setRoleId(p.role.id)
      const found = p.resolved.map(u => u.email).filter(Boolean)
      if (found.length) setEmails(prev => Array.from(new Set([...splitEmails(prev), ...found])).join('\n'))
      const bits: string[] = []
      bits.push(`Aria read it${p.role ? ` and chose the role "${p.role.name}"` : ' but could not pick a role — choose one below'}.`)
      if (found.length) bits.push(`Added ${found.length} matched ${found.length === 1 ? 'person' : 'people'}.`)
      if (p.ambiguous.length) bits.push(`Couldn't be sure who you meant by: ${p.ambiguous.map(a => a.name).join(', ')} — add them by email below.`)
      if (p.unmatched_names.length) bits.push(`Not found: ${p.unmatched_names.join(', ')}.`)
      setPreview(null)
      setAriaNote(bits.join(' '))
    } catch { setErr('Aria could not read that — please pick the role and paste emails manually.') }
    finally { setAriaBusy(false) }
  }

  async function check() {
    setErr(''); setResult(null)
    const list = splitEmails(emails)
    if (!roleId) { setErr('Pick a role first.'); return }
    if (!list.length) { setErr('Add at least one person (email).'); return }
    try { setPreview(await resolveBulkTargets(roleId, list)) }
    catch { setErr('Could not check — please try again.') }
  }

  async function apply() {
    setErr('')
    const list = splitEmails(emails)
    if (!roleId || !list.length) { setErr('Pick a role and add people first.'); return }
    const roleName = roles.find(r => r.id === roleId)?.name || 'this role'
    if (!confirm(`Grant "${roleName}" to ${list.length} ${list.length === 1 ? 'person' : 'people'}? This changes their access.`)) return
    setBusy(true)
    try { setResult(await bulkAssignRole(roleId, list)) }
    catch { setErr('Grant failed — please try again.') }
    finally { setBusy(false) }
  }

  const box: CSSProperties = { width: '100%', padding: 11, fontSize: 14, border: '1px solid #c9d2e3', borderRadius: 8, boxSizing: 'border-box', marginTop: 5, fontFamily: 'inherit' }
  const btn = (bg: string): CSSProperties => ({ padding: '10px 22px', fontSize: 14, fontWeight: 600, color: '#fff', background: bg, border: 'none', borderRadius: 8, cursor: 'pointer' })

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 24, fontFamily: 'Montserrat,"Segoe UI",sans-serif', color: NAVY }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, borderLeft: `5px solid ${ORANGE}`, paddingLeft: 12, margin: '0 0 4px' }}>Give access to many people at once</h1>
      <p style={{ color: '#5a6478', fontSize: 14 }}>Describe it in plain words and let <b>Aria</b> set it up, or pick a role and paste emails yourself. You confirm before anything changes.</p>

      {/* Aria */}
      <div style={{ background: '#FFF6E5', border: `1px solid #f3d9a8`, borderRadius: 10, padding: 16, marginTop: 16 }}>
        <label style={{ fontSize: 13, fontWeight: 700 }}>Ask Aria</label>
        <textarea value={nl} onChange={e => setNl(e.target.value)} rows={2} style={box}
          placeholder='e.g. "give Leone and Bonno the system tester role"' />
        <button onClick={askAria} disabled={ariaBusy} style={{ ...btn(ariaBusy ? '#9aa3b5' : ORANGE), marginTop: 10 }}>
          {ariaBusy ? 'Aria is reading…' : 'Ask Aria'}
        </button>
        {ariaNote && <p style={{ fontSize: 13, color: '#7a5b12', marginTop: 10, marginBottom: 0 }}>{ariaNote}</p>}
      </div>

      {/* Role */}
      <label style={{ display: 'block', fontSize: 13, fontWeight: 700, marginTop: 18 }}>Role to give</label>
      <select value={roleId} onChange={e => { setRoleId(e.target.value); setPreview(null) }} style={{ ...box, background: '#fff' }}>
        <option value="">— choose a role —</option>
        {roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
      </select>

      {/* People */}
      <label style={{ display: 'block', fontSize: 13, fontWeight: 700, marginTop: 16 }}>People (one email per line)</label>
      <textarea value={emails} onChange={e => { setEmails(e.target.value); setPreview(null) }} rows={6} style={box}
        placeholder={'name@alphadirect.co.bw\nname2@alphadirect.co.bw'} />

      <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
        <button onClick={check} style={btn(NAVY)}>Check who gets it</button>
        <button onClick={apply} disabled={busy} style={btn(busy ? '#9aa3b5' : '#2E7D46')}>{busy ? 'Granting…' : 'Apply (grant access)'}</button>
      </div>

      {err && <p style={{ color: '#8E1F12', marginTop: 14 }}>{err}</p>}

      {preview && (
        <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: '#eef2fb', fontSize: 14 }}>
          <b>{preview.count}</b> {preview.count === 1 ? 'person' : 'people'} will get this role.
          {preview.already_have_role.length > 0 && <> <b>{preview.already_have_role.length}</b> already have it (they’ll be skipped).</>}
          {preview.unmatched_emails.length > 0 && <div style={{ color: '#8E1F12', marginTop: 6 }}>Not found in omni: {preview.unmatched_emails.join(', ')}</div>}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 16, padding: '12px 16px', borderRadius: 8, background: '#e7f6ec', fontSize: 14 }}>
          <b style={{ color: '#1F5132' }}>Done.</b> Granted to <b>{result.totals.granted}</b>; skipped <b>{result.totals.skipped}</b> (already had it); failed <b>{result.totals.failed}</b>.
          {result.skipped.length > 0 && <div style={{ color: '#5a6478', marginTop: 6, fontSize: 13 }}>Skipped: {result.skipped.map(s => s.email).join(', ')}</div>}
          {result.failed.length > 0 && <div style={{ color: '#8E1F12', marginTop: 6, fontSize: 13 }}>Failed: {result.failed.map(s => `${s.email} (${s.reason})`).join('; ')}</div>}
          {result.unmatched_emails.length > 0 && <div style={{ color: '#8E1F12', marginTop: 6, fontSize: 13 }}>Not found: {result.unmatched_emails.join(', ')}</div>}
        </div>
      )}
    </div>
  )
}
