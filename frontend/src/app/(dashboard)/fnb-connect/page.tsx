'use client'
import { useEffect, useState, type CSSProperties } from 'react'
import { apiFetch } from '@/lib/api'

interface CredStatus { client_id: string; secret_set: boolean; configured: boolean; api_base: string }

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

export default function FnbConnectPage() {
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [status, setStatus] = useState<CredStatus | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  async function refresh() {
    try {
      const s = await apiFetch<CredStatus>('/fnb/credentials/')
      setStatus(s)
      setClientId(s.client_id || '')
    } catch {
      setErr('Could not load the current status. You may not have access to this page.')
    }
  }
  useEffect(() => { refresh() }, [])

  async function save() {
    setErr(''); setMsg('')
    if (!clientId.trim() || !secret.trim()) {
      setErr('Please fill in both boxes from the FNB screen before saving.')
      return
    }
    setSaving(true)
    try {
      await apiFetch('/fnb/credentials/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId.trim(), client_secret: secret.trim() }),
      })
      setSecret('')
      setMsg('Saved. The bank login is now stored securely in omni.')
      await refresh()
    } catch {
      setErr('Save failed — please try again.')
    } finally {
      setSaving(false)
    }
  }

  const inputStyle: CSSProperties = {
    width: '100%', padding: 11, fontSize: 14, border: '1px solid #c9d2e3',
    borderRadius: 8, boxSizing: 'border-box', marginTop: 5,
  }

  return (
    <div style={{ maxWidth: 660, margin: '0 auto', padding: 24, fontFamily: 'Montserrat, "Segoe UI", sans-serif', color: NAVY }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, borderLeft: `5px solid ${ORANGE}`, paddingLeft: 12, margin: '0 0 6px' }}>
        FNB Bank Connection
      </h1>
      <p style={{ color: '#5a6478', fontSize: 14, lineHeight: 1.55 }}>
        Paste the two details from your FNB screen below, then press <b>Save</b>. That connects omni to the bank.
        Your secret is stored encrypted and is never shown again on this page.
      </p>

      <div style={{
        margin: '16px 0', padding: '10px 14px', borderRadius: 8, fontSize: 14,
        background: status?.configured ? '#e7f6ec' : '#fdecea',
        color: status?.configured ? '#1F5132' : '#8E1F12',
      }}>
        {status
          ? (status.configured ? '● Connected — a bank login is saved.' : '● Not connected yet — paste the details below.')
          : 'Checking the current status…'}
        {status?.secret_set ? ' (a secret is currently on file)' : ''}
      </div>

      <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginTop: 14 }}>
        Client ID
        <input value={clientId} onChange={e => setClientId(e.target.value)} placeholder="e.g. R1G66R" style={inputStyle} />
      </label>

      <label style={{ display: 'block', fontSize: 13, fontWeight: 600, marginTop: 16 }}>
        Client secret
        <input value={secret} onChange={e => setSecret(e.target.value)} type="password"
               placeholder="paste the secret from the FNB screen" style={inputStyle} />
      </label>

      <button onClick={save} disabled={saving} style={{
        marginTop: 20, padding: '11px 26px', fontSize: 15, fontWeight: 600, color: '#fff',
        background: saving ? '#9aa3b5' : NAVY, border: 'none', borderRadius: 8,
        cursor: saving ? 'default' : 'pointer',
      }}>
        {saving ? 'Saving…' : 'Save'}
      </button>

      {msg && <p style={{ color: '#1F5132', marginTop: 16, fontSize: 14 }}>{msg}</p>}
      {err && <p style={{ color: '#8E1F12', marginTop: 16, fontSize: 14 }}>{err}</p>}
    </div>
  )
}
