'use client'

/**
 * /whatsapp — CFO-only WhatsApp reminder console (CFO directive 2026-07-14).
 *
 * One private place for the CFO to:
 *   1. see whether the WhatsApp API key is set (and where to paste it),
 *   2. keep a copy-paste phonebook of staff — especially managers — that is
 *      visible ONLY to him (API is CFO-gated),
 *   3. fire reminders at people who miss task deadlines: overdue tasks are
 *      pulled from the live board with a ready-to-send message, plus a free
 *      compose box. Every send also gives a wa.me link as a manual fallback.
 *
 * Light Finance theme (navy #0D1B2A / orange #F4A623). Numbers never leave the
 * CFO's screen — the backend blocks non-CFO users with 403.
 */

import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import {
  MessageCircle, Send, AlertTriangle, Plus, Trash2, KeyRound,
  ExternalLink, ShieldCheck, RefreshCw, Users, Check, X, FileText, Copy,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

interface Contact { id: string; name: string; phone: string; role: string; is_manager: boolean; active: boolean; notes: string }
interface OverduePerson { assignee_id: number; name: string; overdue: number; tasks: string[]; message: string; contact: Contact | null; phone: string; wa_link: string; template_name: string; template_params: string[] }
interface SendResult { name: string; phone: string; ok: boolean; error: string; wa_link: string }
interface Template { name: string; category: string; language: string; body: string; params: string[]; example: string[]; status: string }

const CARD = 'rounded-2xl border border-slate-200 bg-white shadow-sm'

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    APPROVED:      { label: 'Approved',       cls: 'bg-emerald-100 text-emerald-700' },
    PENDING:       { label: 'Pending Meta',   cls: 'bg-amber-100 text-amber-700' },
    IN_APPEAL:     { label: 'In appeal',      cls: 'bg-amber-100 text-amber-700' },
    REJECTED:      { label: 'Rejected',       cls: 'bg-red-100 text-red-700' },
    EXISTS:        { label: 'Submitted',      cls: 'bg-slate-100 text-slate-600' },
    NOT_SUBMITTED: { label: 'Not submitted',  cls: 'bg-slate-100 text-slate-500' },
  }
  const s = map[status] || { label: status, cls: 'bg-slate-100 text-slate-500' }
  return <span className={'text-[10px] px-1.5 py-0.5 rounded font-medium ' + s.cls}>{s.label}</span>
}

export default function WhatsAppPage() {
  const [denied, setDenied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [config, setConfig] = useState<{ configured: boolean; token_set: boolean; phone_id_set: boolean; waba_id_set: boolean } | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [overdue, setOverdue] = useState<OverduePerson[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [wabaConfigured, setWabaConfigured] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  // one-time Cloud API registration ("turn on sending")
  const [pin, setPin] = useState('')
  const [registering, setRegistering] = useState(false)
  const [registerMsg, setRegisterMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // compose
  const [message, setMessage] = useState('')
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const [sending, setSending] = useState(false)
  const [results, setResults] = useState<SendResult[]>([])

  // add / bulk
  const [paste, setPaste] = useState('')
  const [newName, setNewName] = useState('')
  const [newPhone, setNewPhone] = useState('')
  const [newRole, setNewRole] = useState('')
  const [newMgr, setNewMgr] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [cfg, cs, od, tp] = await Promise.all([
        apiFetch<{ configured: boolean; token_set: boolean; phone_id_set: boolean; waba_id_set: boolean }>('/whatsapp/config/'),
        apiFetch<{ contacts: Contact[] }>('/whatsapp/contacts/'),
        apiFetch<{ people: OverduePerson[] }>('/whatsapp/overdue/'),
        apiFetch<{ templates: Template[]; waba_configured: boolean }>('/whatsapp/templates/'),
      ])
      setConfig(cfg)
      setContacts(cs.contacts || [])
      setOverdue(od.people || [])
      setTemplates(tp.templates || [])
      setWabaConfigured(!!tp.waba_configured)
    } catch (e) {
      if (e instanceof Error && e.message.includes('403')) setDenied(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const pickedPhones = () => Object.entries(picked).filter(([, v]) => v).map(([id]) => id)

  async function send() {
    const ids = pickedPhones()
    if (!message.trim() || ids.length === 0) return
    setSending(true); setResults([])
    try {
      const r = await apiFetch<{ results: SendResult[] }>('/whatsapp/send/', {
        method: 'POST',
        body: JSON.stringify({ contact_ids: ids, message }),
      })
      setResults(r.results || [])
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Send failed')
    } finally {
      setSending(false)
    }
  }

  const templateApproved = (name: string) =>
    templates.some(t => t.name === name && t.status === 'APPROVED')

  async function sendOverdue(p: OverduePerson) {
    if (!p.contact) return
    // A reminder MUST go as an approved template — that is the only kind that
    // arrives outside the recipient's 24h reply window. Falling back to free
    // text would look like it worked and quietly deliver nothing, which is
    // exactly how Graphite's WhatsApp outage hid for months. Fail loudly instead.
    if (!templateApproved(p.template_name)) {
      alert('Cannot send this reminder yet: the template "' + p.template_name +
            '" is not approved by WhatsApp.\n\nA free-typed message would not ' +
            'reach ' + p.contact.name + ', because they have not messaged the ' +
            'Alpha Direct number in the last 24 hours.\n\nSubmit the templates ' +
            'to Meta above, or use the "Open in WhatsApp" link to send by hand.')
      return
    }
    setSending(true)
    try {
      const payload = {
        template: p.template_name,
        recipients: [{ contact_id: p.contact.id, name: p.contact.name, params: p.template_params }],
      }
      const r = await apiFetch<{ results: SendResult[] }>('/whatsapp/send/', {
        method: 'POST', body: JSON.stringify(payload),
      })
      setResults(r.results || [])
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Send failed')
    } finally { setSending(false) }
  }

  async function submitTemplates() {
    setSubmitting(true)
    try {
      const r = await apiFetch<{ ok: boolean; error?: string; results: { name: string; ok: boolean; status: string; error: string }[] }>(
        '/whatsapp/templates/submit/', { method: 'POST', body: JSON.stringify({}) })
      if (r.error) alert(r.error)
      else alert('Submitted to Meta:\n' + (r.results || []).map(x => `${x.name}: ${x.status}${x.error ? ' — ' + x.error : ''}`).join('\n'))
      await load()
    } catch (e) { alert(e instanceof Error ? e.message : 'Submit failed') }
    finally { setSubmitting(false) }
  }

  async function registerNumber() {
    const clean = pin.replace(/\D/g, '')
    if (clean.length !== 6) { setRegisterMsg({ ok: false, text: 'Enter the 6-digit PIN.' }); return }
    setRegistering(true); setRegisterMsg(null)
    try {
      const r = await apiFetch<{ ok: boolean; detail: string }>('/whatsapp/register/', {
        method: 'POST', body: JSON.stringify({ pin: clean }),
      })
      setRegisterMsg({ ok: !!r.ok, text: r.detail })
      if (r.ok) setPin('')
    } catch (e) {
      setRegisterMsg({ ok: false, text: e instanceof Error ? e.message : 'Registration failed' })
    } finally { setRegistering(false) }
  }

  function copyText(s: string) {
    navigator.clipboard?.writeText(s)
  }

  async function addOne() {
    if (!newName.trim() || !newPhone.trim()) return
    setBusy(true)
    try {
      await apiFetch('/whatsapp/contacts/', {
        method: 'POST',
        body: JSON.stringify({ name: newName, phone: newPhone, role: newRole, is_manager: newMgr }),
      })
      setNewName(''); setNewPhone(''); setNewRole(''); setNewMgr(false)
      await load()
    } catch (e) { alert(e instanceof Error ? e.message : 'Add failed') }
    finally { setBusy(false) }
  }

  async function addBulk() {
    if (!paste.trim()) return
    setBusy(true)
    try {
      const r = await apiFetch<{ created_count: number; skipped: number }>('/whatsapp/contacts/', {
        method: 'POST', body: JSON.stringify({ paste }),
      })
      setPaste('')
      alert(`Added ${r.created_count} contact(s). Skipped ${r.skipped} (blank / duplicate).`)
      await load()
    } catch (e) { alert(e instanceof Error ? e.message : 'Bulk add failed') }
    finally { setBusy(false) }
  }

  async function toggleMgr(c: Contact) {
    await apiFetch(`/whatsapp/contacts/${c.id}/`, { method: 'PATCH', body: JSON.stringify({ is_manager: !c.is_manager }) })
    load()
  }

  async function del(c: Contact) {
    if (!confirm(`Remove ${c.name} from your phonebook?`)) return
    await apiFetch(`/whatsapp/contacts/${c.id}/`, { method: 'DELETE' })
    load()
  }

  if (denied) {
    return (
      <div><TopBar />
        <div className="p-8 max-w-2xl mx-auto">
          <div className={CARD + ' p-6 flex gap-3 items-start'}>
            <ShieldCheck className="text-slate-400 mt-0.5" size={20} />
            <div className="text-sm text-slate-600">
              This page is private to the CFO. Staff phone numbers and the WhatsApp
              reminder console are only visible to you.
            </div>
          </div>
        </div>
      </div>
    )
  }

  const managers = contacts.filter(c => c.is_manager)

  return (
    <div><TopBar />
      <div className="p-6 max-w-5xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="rounded-xl p-2" style={{ background: NAVY }}>
            <MessageCircle size={22} color={ORANGE} />
          </div>
          <div>
            <h1 className="text-xl font-semibold" style={{ color: NAVY }}>WhatsApp Reminders</h1>
            <p className="text-sm text-slate-500">Private CFO console — remind staff who miss deadlines.</p>
          </div>
          <Button variant="outline" size="sm" className="ml-auto" onClick={load}>
            <RefreshCw size={14} className="mr-1" /> Refresh
          </Button>
        </div>

        {/* API key status */}
        <div className={CARD + ' p-4'}>
          <div className="flex items-center gap-3">
            <KeyRound size={18} className="text-slate-500" />
            <div className="flex-1 text-sm">
              {config?.configured ? (
                <span className="text-emerald-700 font-medium">WhatsApp API key is set — reminders send automatically.</span>
              ) : (
                <span className="text-slate-700">
                  <span className="font-medium" style={{ color: ORANGE }}>API key not set.</span>{' '}
                  Paste your key in Settings → Secrets, in the two slots{' '}
                  <code className="px-1 rounded bg-slate-100">WHATSAPP_TOKEN</code> and{' '}
                  <code className="px-1 rounded bg-slate-100">WHATSAPP_PHONE_ID</code>.
                  Until then, use the <strong>Open in WhatsApp</strong> links to send by hand.
                </span>
              )}
            </div>
            <a href="/settings/secrets" className="text-sm inline-flex items-center gap-1 font-medium" style={{ color: NAVY }}>
              Secrets <ExternalLink size={13} />
            </a>
          </div>
        </div>

        {/* Turn on sending — one-time Cloud API registration (#133010 fix) */}
        {config?.configured && (
          <div className={CARD + ' p-4'}>
            <div className="flex items-start gap-3">
              <ShieldCheck size={18} className="text-slate-500 mt-0.5" />
              <div className="flex-1">
                <div className="text-sm font-medium" style={{ color: NAVY }}>Turn on sending (one-time)</div>
                <p className="text-xs text-slate-500 mt-0.5">
                  WhatsApp blocks every message until the Alpha Direct number is registered on the
                  Cloud API (error <code className="px-1 rounded bg-slate-100">#133010</code>). Enter the
                  number&rsquo;s 6-digit two-step-verification PIN once to switch it on. The PIN is sent
                  straight to Meta and never stored.
                </p>
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <input value={pin} onChange={e => setPin(e.target.value)} inputMode="numeric" maxLength={6}
                    placeholder="6-digit PIN"
                    className="rounded-md border border-slate-200 px-2 py-1.5 text-sm w-32 tracking-widest tabular-nums focus:outline-none focus:ring-2"
                    style={{ ['--tw-ring-color' as string]: ORANGE }} />
                  <Button size="sm" disabled={registering || pin.replace(/\D/g, '').length !== 6}
                    onClick={registerNumber} style={{ background: NAVY }}>
                    <ShieldCheck size={14} className="mr-1" /> {registering ? 'Turning on…' : 'Turn on sending'}
                  </Button>
                  {registerMsg && (
                    <span className={'text-xs ' + (registerMsg.ok ? 'text-emerald-700' : 'text-red-600')}>
                      {registerMsg.text}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Message templates */}
        <div className={CARD + ' p-5'}>
          <div className="flex items-center gap-2 mb-1">
            <FileText size={18} style={{ color: NAVY }} />
            <h2 className="font-semibold" style={{ color: NAVY }}>Approved message templates</h2>
          </div>
          <p className="text-xs text-slate-500 mb-3">
            WhatsApp only lets you message staff freely within 24h of their last reply.
            For deadline reminders any time, Meta must <strong>approve</strong> a template.
            Two are prepared below. Submit them to Meta (one click) or paste the text into
            WhatsApp Manager by hand. Approval usually takes minutes to a few hours.
          </p>

          {!wabaConfigured && (
            <div className="mb-3 text-xs rounded-lg p-2.5" style={{ background: '#FFF7EC', color: NAVY }}>
              To submit automatically, also paste your <code className="px-1 rounded bg-white">WHATSAPP_WABA_ID</code>{' '}
              (WhatsApp Business Account id) in <a href="/settings/secrets" className="underline">Settings → Secrets</a>.
              Without it, copy each template below into WhatsApp Manager manually.
            </div>
          )}

          <div className="space-y-3">
            {templates.map(t => (
              <div key={t.name} className="rounded-lg border border-slate-100 p-3">
                <div className="flex items-center gap-2 mb-1">
                  <code className="text-sm font-medium" style={{ color: NAVY }}>{t.name}</code>
                  <span className="text-[10px] px-1 rounded bg-slate-100 text-slate-500">{t.category} · {t.language}</span>
                  <StatusBadge status={t.status} />
                  <button onClick={() => copyText(t.body)} className="ml-auto text-xs inline-flex items-center gap-1 text-slate-500 hover:text-slate-800">
                    <Copy size={12} /> Copy text
                  </button>
                </div>
                <p className="text-sm text-slate-700">{t.body}</p>
                <p className="text-[11px] text-slate-400 mt-1">Fills in: {t.params.join(', ')}</p>
              </div>
            ))}
          </div>

          <Button className="mt-3" disabled={submitting || !wabaConfigured} onClick={submitTemplates} style={{ background: NAVY }}>
            <FileText size={14} className="mr-1" /> {submitting ? 'Submitting…' : 'Submit templates to Meta'}
          </Button>
        </div>

        {/* Overdue — auto reminders */}
        <div className={CARD + ' p-5'}>
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={18} style={{ color: ORANGE }} />
            <h2 className="font-semibold" style={{ color: NAVY }}>People with overdue tasks</h2>
            <span className="text-xs text-slate-400 ml-auto">{overdue.length} behind</span>
          </div>
          {loading ? <p className="text-sm text-slate-400">Loading…</p> :
            overdue.length === 0 ? <p className="text-sm text-slate-500">Nobody is overdue right now. 🎉</p> : (
              <div className="divide-y divide-slate-100">
                {overdue.map(p => (
                  <div key={p.assignee_id} className="py-3 flex items-start gap-3">
                    <div className="flex-1">
                      <div className="font-medium text-slate-800">{p.name}
                        <span className="ml-2 text-xs font-normal text-white px-1.5 py-0.5 rounded" style={{ background: ORANGE }}>{p.overdue} late</span>
                        {!p.contact && <span className="ml-2 text-xs text-red-500">no phone on file</span>}
                      </div>
                      <div className="text-xs text-slate-500 mt-0.5 line-clamp-2">{p.tasks.slice(0, 3).join(' · ')}</div>
                    </div>
                    {p.contact && (
                      <div className="flex gap-2 shrink-0">
                        <Button size="sm" disabled={sending} onClick={() => sendOverdue(p)} style={{ background: NAVY }}>
                          <Send size={13} className="mr-1" /> Remind
                        </Button>
                        <a href={p.wa_link} target="_blank" rel="noreferrer"
                           className="text-xs inline-flex items-center px-2.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50">
                          Open in WhatsApp
                        </a>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
        </div>

        {/* Send results */}
        {results.length > 0 && (
          <div className={CARD + ' p-4'}>
            <h3 className="text-sm font-semibold mb-2" style={{ color: NAVY }}>Send results</h3>
            <div className="space-y-1">
              {results.map((r, i) => (
                <div key={i} className="text-sm flex items-center gap-2">
                  {r.ok ? <Check size={14} className="text-emerald-600" /> : <X size={14} className="text-red-500" />}
                  <span className="text-slate-700">{r.name || r.phone}</span>
                  {!r.ok && <>
                    <span className="text-xs text-red-500">{r.error}</span>
                    <a href={r.wa_link} target="_blank" rel="noreferrer" className="text-xs underline ml-auto" style={{ color: NAVY }}>send by hand</a>
                  </>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Compose */}
        <div className={CARD + ' p-5'}>
          <div className="flex items-center gap-2 mb-3">
            <Send size={18} style={{ color: NAVY }} />
            <h2 className="font-semibold" style={{ color: NAVY }}>Send a message</h2>
            {managers.length > 0 && (
              <button className="text-xs ml-auto underline text-slate-500"
                onClick={() => setPicked(Object.fromEntries(managers.map(m => [m.id, true])))}>
                Select all managers
              </button>
            )}
          </div>
          <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            <strong>A typed message only arrives if the person messaged this number in the last 24 hours.</strong>{' '}
            WhatsApp accepts everything else and then throws it away — it will show as
            &ldquo;Accepted&rdquo; below, never as delivered. To reach someone who has not
            replied recently, use an <strong>approved template</strong> (the overdue
            reminders above) or the <strong>Open in WhatsApp</strong> link to send it yourself.
          </div>
          <textarea value={message} onChange={e => setMessage(e.target.value)} rows={3}
            placeholder="Type your reminder…"
            className="w-full rounded-lg border border-slate-200 p-2.5 text-sm focus:outline-none focus:ring-2"
            style={{ ['--tw-ring-color' as string]: ORANGE }} />
          <div className="mt-3 max-h-48 overflow-auto rounded-lg border border-slate-100 divide-y divide-slate-50">
            {contacts.length === 0 && <p className="text-sm text-slate-400 p-3">Add contacts below first.</p>}
            {contacts.map(c => (
              <label key={c.id} className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-slate-50">
                <input type="checkbox" checked={!!picked[c.id]} onChange={e => setPicked({ ...picked, [c.id]: e.target.checked })} />
                <span className="text-slate-700">{c.name}</span>
                {c.is_manager && <span className="text-[10px] px-1 rounded bg-slate-100 text-slate-500">MGR</span>}
                <span className="text-xs text-slate-400 ml-auto">{c.phone}</span>
              </label>
            ))}
          </div>
          <Button className="mt-3" disabled={sending || !message.trim() || pickedPhones().length === 0}
            onClick={send} style={{ background: ORANGE, color: NAVY }}>
            <Send size={14} className="mr-1" /> Send to {pickedPhones().length || ''} selected
          </Button>
        </div>

        {/* Phonebook */}
        <div className={CARD + ' p-5'}>
          <div className="flex items-center gap-2 mb-3">
            <Users size={18} style={{ color: NAVY }} />
            <h2 className="font-semibold" style={{ color: NAVY }}>Your phonebook</h2>
            <span className="text-xs text-slate-400 ml-auto">{contacts.length} contacts · private to you</span>
          </div>

          {/* bulk paste */}
          <div className="mb-4">
            <p className="text-xs text-slate-500 mb-1">Paste a list — one per line, e.g. <code className="bg-slate-100 px-1 rounded">Kago Motlhaba  +26771234567</code></p>
            <textarea value={paste} onChange={e => setPaste(e.target.value)} rows={3}
              placeholder="Name  +267…&#10;Name  +267…"
              className="w-full rounded-lg border border-slate-200 p-2.5 text-sm font-mono focus:outline-none focus:ring-2"
              style={{ ['--tw-ring-color' as string]: ORANGE }} />
            <Button size="sm" variant="outline" className="mt-2" disabled={busy || !paste.trim()} onClick={addBulk}>
              <Plus size={13} className="mr-1" /> Add pasted list
            </Button>
          </div>

          {/* add one */}
          <div className="flex flex-wrap gap-2 items-center mb-4 p-3 rounded-lg bg-slate-50">
            <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Name"
              className="rounded-md border border-slate-200 px-2 py-1.5 text-sm flex-1 min-w-[120px]" />
            <input value={newPhone} onChange={e => setNewPhone(e.target.value)} placeholder="+267…"
              className="rounded-md border border-slate-200 px-2 py-1.5 text-sm w-36" />
            <input value={newRole} onChange={e => setNewRole(e.target.value)} placeholder="Role (optional)"
              className="rounded-md border border-slate-200 px-2 py-1.5 text-sm w-40" />
            <label className="text-xs text-slate-600 flex items-center gap-1">
              <input type="checkbox" checked={newMgr} onChange={e => setNewMgr(e.target.checked)} /> Manager
            </label>
            <Button size="sm" disabled={busy || !newName.trim() || !newPhone.trim()} onClick={addOne} style={{ background: NAVY }}>
              <Plus size={13} className="mr-1" /> Add
            </Button>
          </div>

          {/* table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                  <th className="py-2 font-medium">Name</th>
                  <th className="py-2 font-medium">Phone</th>
                  <th className="py-2 font-medium">Role</th>
                  <th className="py-2 font-medium">Manager</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {contacts.map(c => (
                  <tr key={c.id} className="border-b border-slate-50">
                    <td className="py-2 text-slate-800">{c.name}</td>
                    <td className="py-2 text-slate-600 tabular-nums">{c.phone}</td>
                    <td className="py-2 text-slate-500">{c.role || '—'}</td>
                    <td className="py-2">
                      <button onClick={() => toggleMgr(c)}
                        className={'text-xs px-2 py-0.5 rounded ' + (c.is_manager ? 'text-white' : 'text-slate-400 bg-slate-100')}
                        style={c.is_manager ? { background: ORANGE } : undefined}>
                        {c.is_manager ? 'Manager' : 'Staff'}
                      </button>
                    </td>
                    <td className="py-2 text-right">
                      <button onClick={() => del(c)} className="text-slate-300 hover:text-red-500"><Trash2 size={15} /></button>
                    </td>
                  </tr>
                ))}
                {contacts.length === 0 && <tr><td colSpan={5} className="py-4 text-center text-slate-400">No contacts yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  )
}
