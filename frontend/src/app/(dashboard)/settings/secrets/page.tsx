'use client'

/**
 * /settings/secrets — CFO-only Secrets Vault (CFO directive 2026-06).
 *
 * Encrypted store for HRIS / portal / integration passwords + credentials.
 * Values are encrypted at rest (Fernet, server-side) — the list NEVER returns
 * plaintext. "Reveal" calls an audited endpoint that decrypts the one value
 * and logs who/when. CFO / administrator / superuser only (the /admin/vault/
 * endpoints enforce the same gate).
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, getMe,
  listVaultSecrets, createVaultSecret, updateVaultSecret, deleteVaultSecret, revealVaultSecret,
} from '@/lib/api'
import type { VaultSecretRow } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { ShieldCheck, Plus, Trash2, Copy, Eye, EyeOff, Lock, RefreshCw, KeyRound, AlertTriangle, ClipboardPaste, Wand2, Check, X } from 'lucide-react'

const CATEGORIES = [
  { id: 'hris',     label: 'HRIS' },
  { id: 'portal',   label: 'Portal / Paygate' },
  { id: 'api',      label: 'API / Integration' },
  { id: 'database', label: 'Database' },
  { id: 'email',    label: 'Email / M365' },
  { id: 'cloud',    label: 'Cloud / Infra' },
  { id: 'other',    label: 'Other' },
]

/* ─── Smart Paste — parse a credentials email ENTIRELY in the browser ──────────
 * The pasted email text NEVER leaves the browser. We only extract the fields the
 * user confirms, then save each through the normal encrypted create endpoint.
 * Recall-biased for RealPay-style handovers, but generic to any key:value email.
 */
type ParsedItem = {
  keep: boolean
  name: string       // friendly name, without the system prefix
  category: string
  value: string
  kind: string
  source: string     // original line — shown as a hint, NEVER stored
}

// email-header / greeting / sign-off labels that look like "key: value" but aren't secrets
const HEADER_DENY = new Set([
  'from', 'to', 'cc', 'bcc', 'sent', 'date', 'subject', 'reply-to', 'reply to',
  'importance', 'priority', 'received', 'return-path', 'message-id', 'sensitivity',
  'dear', 'hi', 'hello', 'regards', 'thanks', 'thank you', 'best', 'best regards',
  'kind regards', 'sincerely', 'sir', 'madam', 're', 'fw', 'fwd',
])

function classifyKind(label: string): string {
  const l = label.toLowerCase()
  if (/client[\s_-]?id|consumer key|\bapp id\b|\bapi id\b/.test(l)) return 'client_id'
  if (/client[\s_-]?secret|consumer secret|api secret|app secret/.test(l)) return 'client_secret'
  if (/beneficiar/.test(l)) return 'beneficiary'
  if (/product/.test(l)) return 'product_code'
  if (/url|endpoint|service address|service url|\bhost\b|wsdl|base ?url/.test(l)) return 'url'
  if (/password|passcode|\bpwd\b|\bpass\b/.test(l)) return 'client_secret'
  if (/user ?name|\buser\b|\blogin\b|\baccount\b/.test(l)) return 'username'
  if (/secret|token|\bkey\b|\bapi\b/.test(l)) return 'client_secret'
  return 'other'
}

function friendlyName(kind: string, label: string): string {
  switch (kind) {
    case 'client_id':     return 'Client ID'
    case 'client_secret': return 'Client Secret'
    case 'url':           return 'Service URL'
    case 'beneficiary':   return label.trim().replace(/[:=].*$/, '').trim() || 'Beneficiary'
    case 'product_code':  return 'Product code'
    case 'username':      return 'Username'
    default: {
      const t = label.trim().replace(/\s+/g, ' ')
      return t ? t.charAt(0).toUpperCase() + t.slice(1) : 'Value'
    }
  }
}

function cleanValue(raw: string): string {
  let v = (raw || '').trim()
  v = v.replace(/^["'`<(\[]+/, '').replace(/["'`>)\].,;]+$/, '').trim()
  return v
}

// a single, spaceless token that looks like a credential/code/number
function isTokenLike(v: string): boolean {
  return /^[^\s]{4,200}$/.test(v)
}

function parseCredentialsEmail(text: string): { systemLabel: string; items: ParsedItem[] } {
  const systemLabel = /real ?pay/i.test(text) ? 'RealPay LIVE' : ''
  const items: ParsedItem[] = []
  const seen = new Set<string>()
  const push = (label: string, value: string, source: string) => {
    const val = cleanValue(value)
    if (!val) return
    const kind = classifyKind(label)
    const known = kind !== 'other'
    // keep known-credential labels; otherwise only single-token / URL values
    if (!known && !isTokenLike(val)) return
    const dedup = `${kind}|${val.toLowerCase()}`
    if (seen.has(dedup)) return
    seen.add(dedup)
    const isApi = known || /real ?pay|\bapi\b|integration/i.test(text)
    items.push({
      keep: true,
      name: friendlyName(kind, label),
      category: isApi ? 'api' : 'other',
      value: val,
      kind,
      source: source.trim().slice(0, 160),
    })
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    const m = line.match(/^([A-Za-z][A-Za-z0-9 _\/.\-]{0,39}?)\s*[:=]\s*(.+)$/)
    if (m) {
      const label = m[1].trim()
      if (HEADER_DENY.has(label.toLowerCase())) continue
      push(label, m[2], line)
      continue
    }
    // a bare URL on its own line, no label
    const u = line.match(/https?:\/\/\S+/)
    if (u) push('URL', u[0], line)
  }
  return { systemLabel, items: items.slice(0, 40) }
}

export default function SecretsVaultPage() {
  const router = useRouter()
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [rows, setRows] = useState<VaultSecretRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // create form
  const [name, setName] = useState('')
  const [category, setCategory] = useState('hris')
  const [username, setUsername] = useState('')
  const [url, setUrl] = useState('')
  const [notes, setNotes] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)

  // revealed values, keyed by id (cleared on demand)
  const [revealed, setRevealed] = useState<Record<string, string>>({})
  const [copiedId, setCopiedId] = useState<string | null>(null)

  // SEC-DEL-01 (CFO 2026-08-15, Manus QC R2): a single window.confirm() was
  // one click from destroying live credentials like the RealPay LIVE client
  // secret. Delete now needs the exact name typed to arm the button.
  const [pendingDelete, setPendingDelete] = useState<VaultSecretRow | null>(null)
  const [deleteText, setDeleteText] = useState('')
  const [deleteBusy, setDeleteBusy] = useState(false)

  // Smart Paste — parse a credentials email in the browser
  const [smartText, setSmartText] = useState('')
  const [systemLabel, setSystemLabel] = useState('')
  const [parsed, setParsed] = useState<ParsedItem[] | null>(null)
  const [showValues, setShowValues] = useState(false)
  const [savingSmart, setSavingSmart] = useState(false)
  const [smartMsg, setSmartMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await listVaultSecrets()
      setRows(r.secrets)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load vault')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMe().then(me => {
      const m = me as any
      const title = (m?.title || '').toString().toLowerCase()
      const ok = !!(m && (m.is_superuser || m.is_administrator || title === 'cfo'))
      setAllowed(ok)
      if (ok) load()
      else setLoading(false)
    }).catch(() => { setAllowed(false); setLoading(false) })
  }, [load, router])

  async function doCreate() {
    if (!name.trim() || !secret) { setError('Name and secret are required.'); return }
    setBusy(true); setError(null)
    try {
      await createVaultSecret({
        name: name.trim(), category, username: username.trim(),
        url: url.trim(), notes: notes.trim(), secret,
      })
      setName(''); setUsername(''); setUrl(''); setNotes(''); setSecret(''); setCategory('hris')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    } finally { setBusy(false) }
  }

  function doReadEmail() {
    setSmartMsg(null); setError(null)
    const { systemLabel: sys, items } = parseCredentialsEmail(smartText)
    if (items.length === 0) {
      setParsed([])
      setSmartMsg("Couldn't spot any credentials in that text. You can still add them by hand below.")
      return
    }
    setSystemLabel(sys)
    setParsed(items)
  }

  function updateParsed(i: number, patch: Partial<ParsedItem>) {
    setParsed(p => p ? p.map((it, idx) => idx === i ? { ...it, ...patch } : it) : p)
  }

  function clearSmart() {
    setSmartText(''); setParsed(null); setSystemLabel(''); setSmartMsg(null); setShowValues(false)
  }

  async function saveSmart() {
    if (!parsed) return
    const chosen = parsed.filter(p => p.keep && p.value.trim() && p.name.trim())
    if (chosen.length === 0) { setSmartMsg('Nothing ticked to save.'); return }
    setSavingSmart(true); setSmartMsg(null); setError(null)
    let ok = 0
    const failed: string[] = []
    for (const it of chosen) {
      const fullName = systemLabel.trim() ? `${systemLabel.trim()} — ${it.name.trim()}` : it.name.trim()
      try {
        await createVaultSecret({
          name: fullName,
          category: it.category,
          notes: `Imported via Smart Paste (${it.kind})`,
          secret: it.value.trim(),
        })
        ok += 1
      } catch (e) {
        failed.push(`${fullName}: ${e instanceof Error ? e.message : 'failed'}`)
      }
    }
    setSavingSmart(false)
    await load()
    if (failed.length === 0) {
      setSmartMsg(`Saved ${ok} item${ok === 1 ? '' : 's'} to the vault.`)
      setSmartText(''); setParsed(null); setSystemLabel(''); setShowValues(false)
    } else {
      setSmartMsg(`Saved ${ok}. Skipped ${failed.length}: ${failed.join(' · ')}`)
      setParsed(p => p ? p.filter(it => failed.some(f => f.startsWith(`${systemLabel.trim() ? systemLabel.trim() + ' — ' : ''}${it.name.trim()}:`))) : p)
    }
  }

  async function doReveal(row: VaultSecretRow) {
    if (revealed[row.id] !== undefined) {
      setRevealed(r => { const n = { ...r }; delete n[row.id]; return n })  // hide
      return
    }
    try {
      const r = await revealVaultSecret(row.id)
      setRevealed(s => ({ ...s, [row.id]: r.secret }))
    } catch (e) { setError(e instanceof Error ? e.message : 'Reveal failed') }
  }

  async function doCopy(row: VaultSecretRow) {
    try {
      let val = revealed[row.id]
      if (val === undefined) { const r = await revealVaultSecret(row.id); val = r.secret }
      await navigator.clipboard.writeText(val)
      setCopiedId(row.id); setTimeout(() => setCopiedId(null), 1500)
    } catch (e) { setError(e instanceof Error ? e.message : 'Copy failed') }
  }

  async function doRotate(row: VaultSecretRow) {
    const next = window.prompt(`New value for "${row.name}":`)
    if (next == null || next === '') return
    try { await updateVaultSecret(row.id, { secret: next }); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Rotate failed') }
  }

  function doDelete(row: VaultSecretRow) {
    // Arm the typed-confirm modal — the actual delete fires from confirmDelete().
    setPendingDelete(row)
    setDeleteText('')
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    if (deleteText.trim() !== pendingDelete.name) return   // safety net; UI also disables
    setDeleteBusy(true)
    try {
      await deleteVaultSecret(pendingDelete.id)
      setPendingDelete(null)
      setDeleteText('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleteBusy(false)
    }
  }

  if (allowed === false) {
    return (
      <div className="min-h-screen bg-[#F8F9FB]"><TopBar />
        <div className="max-w-2xl mx-auto px-6 py-20 text-center">
          <Lock className="w-10 h-10 text-[#9CA3AF] mx-auto mb-3" />
          <h1 className="text-xl font-bold text-[#0D1B2A]">CFO / superuser only</h1>
          <p className="text-[#6B7280] mt-2">The Secrets Vault is restricted to the CFO and administrators.</p>
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
            <ShieldCheck className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">Secrets Vault</h1>
            <p className="text-sm text-[#6B7280]">CFO-only. Encrypted at rest. Every reveal is logged.</p>
          </div>
          <Button variant="outline" className="ml-auto" onClick={load} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>

        {error && (
          <div className="mt-3 mb-2 text-sm text-[#B91C1C] flex items-center gap-2 bg-[#FEF2F2] border border-[#FECACA] rounded px-3 py-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* Smart Paste — paste a whole credentials email, auto-fill the fields */}
        <Card className="mt-4 border-[#F4A623]/40">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2"><Wand2 className="w-4 h-4 text-[#F4A623]" /> Smart Paste — from an email</CardTitle>
            <p className="text-xs text-[#6B7280] mt-1">Paste the whole credentials email (e.g. the RealPay live keys). The email is read here in your browser and never sent anywhere — only the fields you tick get saved, encrypted.</p>
          </CardHeader>
          <CardContent>
            {parsed === null ? (
              <>
                <textarea
                  className="w-full min-h-[140px] px-3 py-2 border border-[#D1D5DB] rounded text-sm text-[#0D1B2A] bg-white font-mono"
                  placeholder={"Paste the email here…\n\ne.g.\nClient ID: 1a2b3c4d\nClient Secret: xxxxxxxxxxxx\nService URL: https://live.realpaycollect.com/rpi/rpws\nBeneficiary ADI: 19413\nProduct codes: RTFNBBW, FNBNDOBW"}
                  value={smartText}
                  onChange={e => setSmartText(e.target.value)}
                />
                <div className="mt-3 flex items-center gap-2">
                  <Button onClick={doReadEmail} disabled={!smartText.trim()}>
                    <ClipboardPaste className="w-4 h-4 mr-1" /> Read the email
                  </Button>
                  {smartText && <Button variant="outline" onClick={clearSmart}>Clear</Button>}
                </div>
              </>
            ) : parsed.length === 0 ? (
              <div className="text-sm text-[#6B7280]">
                <p>{smartMsg}</p>
                <Button variant="outline" className="mt-3" onClick={clearSmart}>Try again</Button>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-3 mb-3">
                  <div className="flex-1 min-w-[220px]">
                    <label className="block text-xs text-[#6B7280] mb-1">Group label (added in front of each name)</label>
                    <input className="h-10 w-full px-3 border border-[#D1D5DB] rounded text-sm text-[#0D1B2A] bg-white" placeholder="e.g. RealPay LIVE" value={systemLabel} onChange={e => setSystemLabel(e.target.value)} />
                  </div>
                  <Button variant="outline" onClick={() => setShowValues(v => !v)}>
                    {showValues ? <><EyeOff className="w-4 h-4 mr-1" /> Hide values</> : <><Eye className="w-4 h-4 mr-1" /> Show values</>}
                  </Button>
                </div>
                <p className="text-xs text-[#6B7280] mb-2">Found <b>{parsed.length}</b> item{parsed.length === 1 ? '' : 's'}. Untick anything you don&apos;t want, fix a name or value if needed, then save.</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                        <th className="py-2 pr-2 w-8">Keep</th>
                        <th className="py-2 pr-2">Name</th>
                        <th className="py-2 pr-2">Category</th>
                        <th className="py-2 pr-2">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {parsed.map((it, i) => (
                        <tr key={i} className={`border-b border-[#F3F4F6] ${it.keep ? '' : 'opacity-40'}`}>
                          <td className="py-1.5 pr-2">
                            <button onClick={() => updateParsed(i, { keep: !it.keep })} title={it.keep ? 'Keep' : 'Skipped'}
                              className={`w-6 h-6 rounded flex items-center justify-center ${it.keep ? 'bg-[#0D1B2A] text-white' : 'bg-[#F3F4F6] text-[#9CA3AF]'}`}>
                              {it.keep ? <Check className="w-4 h-4" /> : <X className="w-4 h-4" />}
                            </button>
                          </td>
                          <td className="py-1.5 pr-2">
                            <input className="h-9 w-full px-2 border border-[#D1D5DB] rounded text-sm text-[#0D1B2A] bg-white" value={it.name} onChange={e => updateParsed(i, { name: e.target.value })} />
                          </td>
                          <td className="py-1.5 pr-2">
                            <select className="h-9 px-2 border border-[#D1D5DB] rounded text-sm text-[#0D1B2A] bg-white" value={it.category} onChange={e => updateParsed(i, { category: e.target.value })}>
                              {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                            </select>
                          </td>
                          <td className="py-1.5 pr-2">
                            <input className="h-9 w-full px-2 border border-[#D1D5DB] rounded text-sm text-[#0D1B2A] bg-white font-mono" type={showValues ? 'text' : 'password'} value={it.value} onChange={e => updateParsed(i, { value: e.target.value })} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <Button onClick={saveSmart} disabled={savingSmart || !parsed.some(p => p.keep)}>
                    {savingSmart ? 'Saving…' : `Save ${parsed.filter(p => p.keep).length} to vault`}
                  </Button>
                  <Button variant="outline" onClick={clearSmart} disabled={savingSmart}>Cancel</Button>
                </div>
              </>
            )}
            {smartMsg && parsed !== null && parsed.length > 0 && (
              <p className="mt-2 text-sm text-[#0D1B2A]">{smartMsg}</p>
            )}
          </CardContent>
        </Card>

        {/* Add secret */}
        <Card className="mt-4">
          <CardHeader><CardTitle className="text-base flex items-center gap-2"><Plus className="w-4 h-4" /> Add a secret (by hand)</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input className="h-10 px-3 border border-[#D1D5DB] rounded text-sm" placeholder="Name (e.g. HRIS unlock password)" value={name} onChange={e => setName(e.target.value)} />
              <select className="h-10 px-3 border border-[#D1D5DB] rounded text-sm bg-white" value={category} onChange={e => setCategory(e.target.value)}>
                {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
              </select>
              <input className="h-10 px-3 border border-[#D1D5DB] rounded text-sm" placeholder="Username / account (optional)" value={username} onChange={e => setUsername(e.target.value)} />
              <input className="h-10 px-3 border border-[#D1D5DB] rounded text-sm" placeholder="URL (optional)" value={url} onChange={e => setUrl(e.target.value)} />
              <input className="h-10 px-3 border border-[#D1D5DB] rounded text-sm sm:col-span-2" type="password" autoComplete="new-password" placeholder="Secret value (password / key / token)" value={secret} onChange={e => setSecret(e.target.value)} />
              <input className="h-10 px-3 border border-[#D1D5DB] rounded text-sm sm:col-span-2" placeholder="Notes (optional)" value={notes} onChange={e => setNotes(e.target.value)} />
            </div>
            <div className="mt-3 flex justify-end">
              <Button onClick={doCreate} disabled={busy || !name.trim() || !secret}>
                {busy ? 'Saving…' : 'Save to vault'}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* List */}
        <Card className="mt-4">
          <CardHeader><CardTitle className="text-base flex items-center gap-2"><KeyRound className="w-4 h-4" /> Stored secrets ({rows.length})</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-[#6B7280]">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="text-sm text-[#6B7280]">No secrets yet. Add one above.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="py-2 pr-3">Name</th>
                      <th className="py-2 pr-3">Category</th>
                      <th className="py-2 pr-3">Username</th>
                      <th className="py-2 pr-3">Secret</th>
                      <th className="py-2 pr-3">Last revealed</th>
                      <th className="py-2 pr-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(row => (
                      <tr key={row.id} className="border-b border-[#F3F4F6]">
                        <td className="py-2 pr-3 font-medium text-[#0D1B2A]">{row.name}{row.url ? <a href={row.url} target="_blank" rel="noreferrer" className="ml-1 text-[#F4A623] text-xs">↗</a> : null}{row.notes ? <div className="text-xs text-[#9CA3AF]">{row.notes}</div> : null}</td>
                        <td className="py-2 pr-3"><span className="text-xs bg-[#F1F5F9] rounded px-2 py-0.5">{row.category_display}</span></td>
                        <td className="py-2 pr-3 text-[#374151]">{row.username || '—'}</td>
                        <td className="py-2 pr-3 font-mono text-[#374151]">{revealed[row.id] !== undefined ? revealed[row.id] : '••••••••'}</td>
                        <td className="py-2 pr-3 text-xs text-[#9CA3AF]">{row.last_revealed_at ? `${new Date(row.last_revealed_at).toLocaleString()} · ${row.last_revealed_by || ''}` : 'never'}</td>
                        <td className="py-2 pr-3">
                          <div className="flex items-center gap-1 justify-end">
                            <button title={revealed[row.id] !== undefined ? 'Hide' : 'Reveal'} onClick={() => doReveal(row)} className="p-1.5 rounded hover:bg-[#F3F4F6]">
                              {revealed[row.id] !== undefined ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                            <button title="Copy" onClick={() => doCopy(row)} className="p-1.5 rounded hover:bg-[#F3F4F6]">
                              <Copy className={`w-4 h-4 ${copiedId === row.id ? 'text-[#16A34A]' : ''}`} />
                            </button>
                            <button title="Rotate (set new value)" onClick={() => doRotate(row)} className="p-1.5 rounded hover:bg-[#F3F4F6]">
                              <RefreshCw className="w-4 h-4" />
                            </button>
                            <button title="Delete" onClick={() => doDelete(row)} className="p-1.5 rounded hover:bg-[#FEF2F2] text-[#B91C1C]">
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
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

      {/* SEC-DEL-01 — typed-confirm before wiping a live credential */}
      <Modal
        open={pendingDelete !== null}
        onOpenChange={(v) => { if (!v && !deleteBusy) { setPendingDelete(null); setDeleteText('') } }}
        title="Delete this secret?"
        description="This wipes the encrypted value. There is no undo."
        size="sm"
      >
        <ModalBody className="space-y-4">
          <div className="flex items-start gap-3 bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3">
            <AlertTriangle className="w-5 h-5 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <div className="text-sm text-[#7F1D1D]">
              <p className="font-semibold">{pendingDelete?.name}</p>
              <p className="mt-1">
                A live secret cannot be recovered from Omni once deleted. If this is an
                integration credential (e.g. a payment gateway client secret), losing it
                may cause an outage — rotate first, then delete.
              </p>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-[#374151] mb-1">
              Type the secret&rsquo;s name to enable delete:
              <span className="ml-1 font-mono text-[#0D1B2A]">{pendingDelete?.name}</span>
            </label>
            <input
              type="text"
              value={deleteText}
              onChange={(e) => setDeleteText(e.target.value)}
              placeholder={pendingDelete?.name || ''}
              autoComplete="off"
              className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#DC2626] focus:ring-[3px] focus:ring-[rgba(220,38,38,0.1)] transition-all font-mono"
            />
          </div>
        </ModalBody>
        <ModalFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setPendingDelete(null); setDeleteText('') }}
            disabled={deleteBusy}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            loading={deleteBusy}
            disabled={!pendingDelete || deleteText.trim() !== pendingDelete.name}
            onClick={confirmDelete}
          >
            Delete permanently
          </Button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
