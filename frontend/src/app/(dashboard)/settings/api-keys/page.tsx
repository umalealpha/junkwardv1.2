'use client'

/**
 * /settings/api-keys — CFO-only API Keys console (CFO directive 2026-06-08).
 *
 * Lists every omni API key (label · scopes · prefix · last-used · active),
 * creates new scoped keys, and revokes them. The plaintext key is shown ONCE
 * on creation (copy to 1Password) — it is hashed server-side and can never be
 * displayed again. Superuser only (the admin/api-keys endpoints enforce this).
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, getMe, listApiKeys, createApiKey, revokeApiKey,
} from '@/lib/api'
import type { ApiKeyRow, ApiKeyCreated } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { KeyRound, Plus, Trash2, Copy, AlertTriangle, ShieldCheck, RefreshCw, Lock } from 'lucide-react'

const SCOPES = [
  { id: 'smart-upload', label: 'smart-upload', hint: 'GL / TB / CoA upload via /smart-upload/*' },
  { id: 'cfo-upload',   label: 'cfo-upload',   hint: 'CFO-upload family (TB/GL/CoA/treaty)' },
  { id: 'bulk-upload',  label: 'bulk-upload',  hint: 'Smart + CFO upload + read accounts/JEs' },
  { id: 'hr-extract',   label: 'hr-extract',   hint: 'READ-ONLY: HRIS, employees, payslips — no accounting data' },
  { id: 'graphite-events', label: 'graphite-events', hint: 'WRITE: Graphite pushes policy/claim events — raises invoices & bills' },
  { id: 'qc-manus', label: 'qc-manus', hint: 'Manus QC: read the bug-board pickup queue + post QC findings back — nothing else' },
]

export default function ApiKeysPage() {
  const router = useRouter()
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [keys, setKeys] = useState<ApiKeyRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // create form
  const [label, setLabel] = useState('')
  const [scopes, setScopes] = useState<string[]>(['smart-upload'])
  const [serviceUser, setServiceUser] = useState('')
  const [busy, setBusy] = useState(false)
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await listApiKeys()
      setKeys(r.keys)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load keys')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMe().then(me => {
      const m = me as any
      const title = (m?.title || '').toString().toLowerCase()
      // CFO authority — the CFO's SSO account isn't a Django superuser, so gate
      // on superuser OR administrator OR title=CFO (matches the backend).
      const ok = !!(m && (m.is_superuser || m.is_administrator || title === 'cfo'))
      setAllowed(ok)
      if (ok) load()
      else setLoading(false)
    }).catch(() => { setAllowed(false); setLoading(false) })
  }, [load, router])

  function toggleScope(id: string) {
    setScopes(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  }

  async function doCreate() {
    if (!label.trim() || scopes.length === 0) {
      setError('Label and at least one scope are required.'); return
    }
    setBusy(true); setError(null); setCreated(null); setCopied(false)
    try {
      const r = await createApiKey({
        label: label.trim(), allowed_scopes: scopes,
        service_user: serviceUser.trim() || undefined,
      })
      setCreated(r)
      setLabel(''); setServiceUser('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setBusy(false)
    }
  }

  async function doRevoke(k: ApiKeyRow) {
    if (!confirm(`Revoke key "${k.label}" (${k.key_prefix}…)? Any automation using it stops immediately.`)) return
    try { await revokeApiKey(k.id); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Revoke failed') }
  }

  if (allowed === false) {
    return (
      <div className="min-h-screen bg-[#F8F9FB]"><TopBar />
        <div className="max-w-2xl mx-auto px-6 py-20 text-center">
          <Lock className="w-10 h-10 text-[#9CA3AF] mx-auto mb-3" />
          <h1 className="text-xl font-bold text-[#0D1B2A]">CFO / superuser only</h1>
          <p className="text-[#6B7280] mt-2">API-key management is restricted. Ask the CFO if you need a key.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <KeyRound className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">API Keys</h1>
            <p className="text-sm text-[#6B7280]">Scoped service keys for uploads &amp; automation · CFO-only</p>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-md bg-[#FEE2E2] text-[#991B1B] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* One-time plaintext reveal */}
        {created && (
          <Card className="mt-5 border-2 border-[#F4A623]">
            <CardContent className="py-4">
              <div className="flex items-center gap-2 text-[#92400E] font-semibold mb-2">
                <ShieldCheck className="w-4 h-4" /> New key created — copy it now (shown only once)
              </div>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-[#0D1B2A] text-[#E7EBEF] rounded-md px-3 py-2 text-xs font-mono break-all">{created.key}</code>
                <Button variant="outline" onClick={() => { navigator.clipboard.writeText(created.key); setCopied(true) }}>
                  <Copy className="w-4 h-4 mr-1" /> {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
              <p className="text-xs text-[#6B7280] mt-2">
                {created.label} · {created.service_user} · scopes: {created.allowed_scopes.join(', ')}. Store in 1Password and deliver securely — it cannot be shown again.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Create */}
        <Card className="mt-5">
          <CardHeader><CardTitle className="text-[#0D1B2A] text-base flex items-center gap-2"><Plus className="w-4 h-4" /> Create a key</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">Label</label>
                <input value={label} onChange={e => setLabel(e.target.value)} placeholder="e.g. Finance GL/TB upload"
                  className="w-full border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">Service account email (optional — auto-created)</label>
                <input value={serviceUser} onChange={e => setServiceUser(e.target.value)} placeholder="finance@alphadirect.co.bw"
                  className="w-full border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
            </div>
            <div className="mt-3">
              <div className="text-xs font-medium text-[#6B7280] mb-1">Scopes</div>
              <div className="flex flex-wrap gap-2">
                {SCOPES.map(s => (
                  <button key={s.id} type="button" onClick={() => toggleScope(s.id)}
                    title={s.hint}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium border ${scopes.includes(s.id) ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]' : 'bg-white text-[#374151] border-[#D1D5DB]'}`}>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
            <Button onClick={doCreate} disabled={busy} className="mt-4 bg-[#0D1B2A] hover:bg-[#162a40] text-white">
              {busy ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Plus className="w-4 h-4 mr-1.5" />} Create key
            </Button>
          </CardContent>
        </Card>

        {/* List */}
        <Card className="mt-5">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-[#0D1B2A] text-base">Existing keys {keys.length ? `(${keys.length})` : ''}</CardTitle>
            <Button variant="outline" onClick={load} disabled={loading}><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /></Button>
          </CardHeader>
          <CardContent>
            {loading ? <div className="py-10 text-center text-[#6B7280]">Loading…</div>
              : keys.length === 0 ? <div className="py-10 text-center text-[#6B7280]">No keys yet.</div>
              : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                    <th className="py-2 pr-3 font-medium">Label</th>
                    <th className="py-2 pr-3 font-medium">Scopes</th>
                    <th className="py-2 pr-3 font-medium">Prefix</th>
                    <th className="py-2 pr-3 font-medium">Service account</th>
                    <th className="py-2 pr-3 font-medium">Last used</th>
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium"></th>
                  </tr></thead>
                  <tbody>
                    {keys.map(k => (
                      <tr key={k.id} className="border-b border-[#F3F4F6]">
                        <td className="py-2 pr-3 font-medium text-[#0D1B2A]">{k.label}</td>
                        <td className="py-2 pr-3">{k.allowed_scopes.map(s => <span key={s} className="inline-block bg-[#EEF1F5] text-[#374151] rounded px-1.5 py-0.5 text-xs mr-1">{s}</span>)}</td>
                        <td className="py-2 pr-3 font-mono text-xs">{k.key_prefix}…</td>
                        <td className="py-2 pr-3 text-xs text-[#6B7280]">{k.service_user__email || k.service_user__username || '—'}</td>
                        <td className="py-2 pr-3 text-xs text-[#6B7280]">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'never'}</td>
                        <td className="py-2 pr-3">
                          <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${k.is_active ? 'bg-[#D1FAE5] text-[#065F46]' : 'bg-[#FEE2E2] text-[#991B1B]'}`}>{k.is_active ? 'active' : 'revoked'}</span>
                        </td>
                        <td className="py-2 pr-3 text-right">
                          {k.is_active && (
                            <button onClick={() => doRevoke(k)} className="text-[#991B1B] hover:underline text-xs inline-flex items-center gap-1">
                              <Trash2 className="w-3.5 h-3.5" /> Revoke
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
