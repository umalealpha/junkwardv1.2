'use client'

/**
 * /health/quotes — Group Health Quotations (CFO/Tlamelo 2026-06-17).
 *
 * Saved quotes (history) + a builder: employer details + member grid (typed in
 * or CSV-uploaded), rated by the backend OFFICE rate card, shown in one tab per
 * plan tier. Approve a quote, then generate the invoice. Download = print view.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Plus, Trash2, Upload, Save, Loader2, CheckCircle2, AlertTriangle,
  FileText, ChevronLeft, Lock, Receipt, Stethoscope, Download,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getHealthQuotes, getHealthQuote, createHealthQuote, updateHealthQuote,
  approveHealthQuote, invoiceHealthQuote, getHealthQuoteRateCard, getToken,
  submitHealthQuote, reviewHealthQuote, rejectHealthQuote, downloadHealthQuotePdf,
  downloadHealthQuoteXlsx, downloadHealthQuoteConsolidatedXlsx, parseHealthQuoteMembers,
  type HealthQuote, type HQMember, type HQRateCard,
} from '@/lib/api'

// No default plan (bug 03a2b875, found by rendering the page 2026-07-30). This row
// used to start on AD_ESSENTIAL, so a member typed straight into the grid was
// priced on AD Essential unless someone remembered to change the Plan column.
// The backend now refuses a missing plan — but it never saw one, because the UI
// always sent a valid tier. A wrong plan is worse than a zero: the quote looks
// right and the premium is not. The plan must be chosen deliberately.
const BLANK_MEMBER: HQMember = {
  full_name: '', member_type: 'main', gender: 'M', date_of_birth: '', tier: '',
}
function fmt(n: string | number | undefined) {
  // VAT + Incl-VAT + totals: keep the cents (Tlamelo 2026-06-22)
  const v = Number(n || 0)
  return 'P' + v.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtWhole(n: string | number | undefined) {
  // Excl-VAT only: whole Pula, no cents — matches the printed office rate table
  const v = Number(n || 0)
  return 'P' + v.toLocaleString('en-BW', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

export default function HealthQuotesPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [card, setCard] = useState<HQRateCard | null>(null)
  const [list, setList] = useState<HealthQuote[]>([])
  const [view, setView] = useState<'list' | 'builder'>('list')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // One plan for the whole group, applied on upload to members with no plan of
  // their own. Blank = use each row's own Tier column (bug 03a2b875).
  const [groupTier, setGroupTier] = useState('')

  // builder state
  const [current, setCurrent] = useState<HealthQuote | null>(null)   // saved quote (has tiers)
  const [form, setForm] = useState({
    client_name: '', client_address: '', contact_name: '', contact_email: '',
    contact_phone: '', vat_no: '', benefit_start: '', billing_period: '',
  })
  const [members, setMembers] = useState<HQMember[]>([{ ...BLANK_MEMBER }])
  // Per-tier discount % (Tlamelo 2026-06-25). Prefilled from the standard
  // defaults; editable per quote, saved with it.
  const [discounts, setDiscounts] = useState<Record<string, string>>({})
  // Editable only as a draft / sent-back quote; locked once in review/approved/invoiced.
  const editable = !current || current.status === 'draft' || current.status === 'rejected'
  const locked = !editable
  const st = current?.status
  const stage = current?.review_stage

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getHealthQuoteRateCard().then(setCard).catch(() => {})
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function refresh() {
    setLoading(true)
    getHealthQuotes().then(r => setList(r.quotes)).catch(() => {}).finally(() => setLoading(false))
  }

  function newQuote() {
    setCurrent(null)
    setForm({ client_name: '', client_address: '', contact_name: '', contact_email: '',
      contact_phone: '', vat_no: '', benefit_start: '', billing_period: '' })
    setMembers([{ ...BLANK_MEMBER }])
    setDiscounts({ ...(card?.tier_discount_defaults || {}) })   // prefill standard discounts
    setMsg(null); setErr(null); setView('builder')
  }

  async function openQuote(id: string) {
    setBusy(true); setErr(null)
    try {
      const q = await getHealthQuote(id)
      setCurrent(q)
      setForm({
        client_name: q.client_name, client_address: q.client_address, contact_name: q.contact_name,
        contact_email: q.contact_email, contact_phone: q.contact_phone, vat_no: q.vat_no,
        benefit_start: q.benefit_start || '', billing_period: q.billing_period,
      })
      setMembers((q.members && q.members.length ? q.members : [{ ...BLANK_MEMBER }]).map(m => ({
        ...m, date_of_birth: m.date_of_birth || '',
      })))
      // stored discounts win; fall back to the standard defaults for any tier
      // the quote doesn't carry yet (so editing an older draft prefills them).
      setDiscounts({ ...(card?.tier_discount_defaults || {}), ...(q.tier_discounts || {}) })
      setView('builder'); setMsg(null)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Load failed') }
    finally { setBusy(false) }
  }

  function setMember(i: number, patch: Partial<HQMember>) {
    setMembers(ms => ms.map((m, j) => j === i ? { ...m, ...patch } : m))
  }
  function addRow() { setMembers(ms => [...ms, { ...BLANK_MEMBER }]) }
  function delRow(i: number) { setMembers(ms => ms.filter((_, j) => j !== i)) }

  // CSV upload → member rows. Columns: full_name, member_type, gender, date_of_birth, tier
  // Upload an Excel/CSV member schedule → backend parses, validates (gender,
  // DOB/age, tier, duplicates) and rates each row off the office card.
  async function onUpload(file: File | null) {
    if (!file) return
    setBusy(true); setMsg(null); setErr(null)
    try {
      const r = await parseHealthQuoteMembers(file, groupTier)
      if (r.members.length) {
        setMembers(r.members.map(m => ({ ...m, date_of_birth: m.date_of_birth || '' })))
      }
      const notes: string[] = []
      // No member survived the upload — say so loudly and name the reason, rather
      // than leaving the grid as it was and letting the quote read zero (bug 03a2b875).
      if (!r.members.length) {
        notes.push('No member could be rated, so nothing was loaded.'
          + (groupTier ? '' : ' If the whole group is on one plan, pick it in "Plan for whole group" and upload again.'))
      }
      if (r.errors?.length) notes.push(`${r.errors.length} flagged (${r.errors.slice(0, 2).map(e => e.error).join('; ')})`)
      if (r.warnings?.length) notes.push(r.warnings.slice(0, 2).map(w => w.warning).join('; '))
      setMsg(r.message)
      if (notes.length) setErr(notes.join(' · '))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function save() {
    if (!form.client_name.trim()) { setErr('Client / employer name is required.'); return }
    setBusy(true); setMsg(null); setErr(null)
    const body = { ...form, members: members.filter(m => m.full_name.trim()), tier_discounts: discounts }
    try {
      const q = current ? await updateHealthQuote(current.id, body) : await createHealthQuote(body)
      setCurrent(q); setMsg(`Saved — ${q.ref}. ${q.lives} lives, ${fmt(q.total_incl)} incl. VAT.`)
      refresh()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(false) }
  }
  async function runAction(fn: (id: string) => Promise<HealthQuote>, okMsg: string, failMsg: string) {
    if (!current) return
    setBusy(true); setErr(null); setMsg(null)
    try { const q = await fn(current.id); setCurrent(q); setMsg(okMsg); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : failMsg) } finally { setBusy(false) }
  }
  const doSubmit  = () => runAction(submitHealthQuote, 'Submitted for review — Reviewer 1 (Ritah) notified.', 'Submit failed')
  const doReview  = () => runAction(reviewHealthQuote, 'Reviewed and forwarded to the next approver.', 'Review failed')
  const doApprove = () => runAction(approveHealthQuote, 'Quote approved — you can now generate the invoice.', 'Approve failed')
  function doReject() {
    if (!current) return
    const reason = window.prompt('Send back to the creator — reason?') ?? ''
    if (reason === null) return
    runAction((id) => rejectHealthQuote(id, reason), 'Sent back to the creator.', 'Send-back failed')
  }
  async function doInvoice() {
    if (!current) return
    setBusy(true); setErr(null)
    try { const q = await invoiceHealthQuote(current.id); setCurrent(q); setMsg(`Invoice ${q.invoice_no} generated.`); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Invoice failed') } finally { setBusy(false) }
  }
  function download() {
    if (!current) return
    const kind = current.status === 'invoiced' ? 'Invoice' : 'Quote'
    downloadHealthQuotePdf(current.id, `${kind}_${current.ref}.pdf`)
      .catch(e => setErr(e instanceof Error ? e.message : 'PDF download failed'))
  }
  function downloadXlsx() {
    if (!current) return
    const kind = current.status === 'invoiced' ? 'Invoice' : 'Quote'
    downloadHealthQuoteXlsx(current.id, `${kind}_${current.ref}.xlsx`)
      .catch(e => setErr(e instanceof Error ? e.message : 'Excel download failed'))
  }
  function downloadConsolidated() {
    if (!current) return
    downloadHealthQuoteConsolidatedXlsx(current.id, `Consolidated_${current.ref}.xlsx`)
      .catch(e => setErr(e instanceof Error ? e.message : 'Consolidated Excel download failed'))
  }

  const card_ = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }
  const tiers = current?.tiers || []

  // ── List view ──
  if (view === 'list') {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <TopBar title="Health Quotations"
                breadcrumbs={[{ label: 'Healthcare', href: '/health/quick-quote' }, { label: 'Quotations' }]} />
        <main className="flex-1 overflow-y-auto p-6 space-y-5">
          <div className="flex items-center gap-3">
            <Stethoscope className="w-5 h-5" style={{ color: theme.orange }} />
            <h2 className="text-lg font-semibold" style={{ color: theme.text }}>Group Health Quotations</h2>
            <div className="flex-1" />
            <button onClick={newQuote} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold"
                    style={{ background: theme.orange, color: '#fff' }}><Plus className="w-4 h-4" /> New quote</button>
          </div>
          {err && <Banner theme={theme} kind="err">{err}</Banner>}
          <div className="rounded-2xl overflow-hidden" style={card_}>
            <table className="w-full text-left text-sm">
              <thead><tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                {['Reference', 'Client', 'Lives', 'Total (incl VAT)', 'Status', 'Created'].map((h, i) => (
                  <th key={h} className={`px-3 py-2.5 text-[11px] uppercase tracking-wider font-semibold ${i === 2 || i === 3 ? 'text-right' : ''}`} style={{ color: theme.t2 }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {loading && <tr><td colSpan={6} className="px-3 py-8 text-center" style={{ color: theme.t2 }}><Loader2 className="w-4 h-4 inline animate-spin" /> Loading…</td></tr>}
                {!loading && !list.length && <tr><td colSpan={6} className="px-3 py-8 text-center" style={{ color: theme.t2 }}>No quotes yet — click “New quote”.</td></tr>}
                {!loading && list.map(q => (
                  <tr key={q.id} onClick={() => openQuote(q.id)} className="cursor-pointer"
                      style={{ borderTop: `1px solid ${theme.cardBdr}55` }}
                      onMouseEnter={e => e.currentTarget.style.background = theme.oL}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                    <td className="px-3 py-2 font-mono text-xs" style={{ color: theme.orange }}>{q.ref}</td>
                    <td className="px-3 py-2" style={{ color: theme.text }}>{q.client_name}</td>
                    <td className="px-3 py-2 text-right tabular-nums" style={{ color: theme.t2 }}>{q.lives}</td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold" style={{ color: theme.text }}>{fmt(q.total_incl)}</td>
                    <td className="px-3 py-2"><StatusPill theme={theme} status={q.status} label={q.status_label} /></td>
                    <td className="px-3 py-2 text-xs" style={{ color: theme.t2 }}>{q.created_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </main>
      </div>
    )
  }

  // ── Builder view ──
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title={current ? current.ref : 'New Quotation'}
              breadcrumbs={[{ label: 'Healthcare', href: '/health/quick-quote' }, { label: 'Quotations' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <button onClick={() => { setView('list'); refresh() }} className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> All quotes
        </button>

        {current && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm" style={{ color: theme.orange }}>{current.ref}</span>
            <StatusPill theme={theme} status={current.status} label={current.status_label} />
            {current.invoice_no && <span className="text-xs" style={{ color: theme.t2 }}>Invoice {current.invoice_no}</span>}
          </div>
        )}
        {msg && <Banner theme={theme} kind="ok">{msg}</Banner>}
        {err && <Banner theme={theme} kind="err">{err}</Banner>}

        {/* Employer details */}
        <div className="rounded-2xl p-4 space-y-3" style={card_}>
          <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Employer group</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {([['client_name', 'Client / employer *'], ['contact_name', 'Contact name'], ['contact_email', 'Contact email'],
               ['contact_phone', 'Contact phone'], ['vat_no', 'VAT no.'], ['billing_period', 'Billing period (e.g. June 2026)']] as const).map(([k, label]) => (
              <div key={k}>
                <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>{label}</label>
                <input value={(form as any)[k]} disabled={locked} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))}
                       className="w-full px-3 py-2 rounded-lg text-sm outline-none disabled:opacity-60" style={input} />
              </div>
            ))}
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Benefit start</label>
              <input type="date" value={form.benefit_start} disabled={locked} onChange={e => setForm(f => ({ ...f, benefit_start: e.target.value }))}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none disabled:opacity-60" style={input} />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Address</label>
              <input value={form.client_address} disabled={locked} onChange={e => setForm(f => ({ ...f, client_address: e.target.value }))}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none disabled:opacity-60" style={input} />
            </div>
          </div>
        </div>

        {/* Members */}
        <div className="rounded-2xl p-4 space-y-3" style={card_}>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Members</h3>
            <div className="flex-1" />
            {!locked && <>
              {/* One plan for the whole group (bug 03a2b875): most employer groups are
                  on a single plan. Without this, a census with no Tier column priced
                  every member out and the quote came to zero. */}
              <label className="text-xs" style={{ color: theme.t2 }} htmlFor="hq-group-tier">Plan for whole group</label>
              <select id="hq-group-tier" value={groupTier} onChange={e => setGroupTier(e.target.value)}
                      title="Applied to any uploaded member with no plan of their own"
                      className="px-2 py-1.5 rounded-lg text-xs outline-none" style={input}>
                <option value="">Use the sheet&apos;s Tier column</option>
                {(card?.tiers || []).map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
              <button onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold"
                      style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}><Upload className="w-3.5 h-3.5" /> Upload Excel/CSV</button>
              <input ref={fileRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden" onChange={e => onUpload(e.target.files?.[0] || null)} />
              <button onClick={addRow} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold"
                      style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}><Plus className="w-3.5 h-3.5" /> Add member</button>
            </>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                {['Full name', 'Status', 'Gender', 'Date of birth', 'Plan', ''].map(h => (
                  <th key={h} className="px-2 py-2 text-[11px] uppercase tracking-wide font-semibold" style={{ color: theme.t2 }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {members.map((m, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                    <td className="px-2 py-1"><input value={m.full_name} disabled={locked} onChange={e => setMember(i, { full_name: e.target.value })} className="w-44 px-2 py-1 rounded text-sm outline-none" style={input} /></td>
                    <td className="px-2 py-1">
                      <select value={m.member_type} disabled={locked} onChange={e => setMember(i, { member_type: e.target.value as HQMember['member_type'] })} className="px-2 py-1 rounded text-sm outline-none" style={input}>
                        {(card?.member_types || [{ value: 'main', label: 'Policy Holder' }, { value: 'adult_dep', label: 'Adult Dependant' }, { value: 'child_dep', label: 'Child Dependant' }]).map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </td>
                    <td className="px-2 py-1">
                      <select value={m.gender} disabled={locked} onChange={e => setMember(i, { gender: e.target.value })} className="px-2 py-1 rounded text-sm outline-none" style={input}><option>M</option><option>F</option></select>
                    </td>
                    <td className="px-2 py-1"><input type="date" value={m.date_of_birth || ''} disabled={locked} onChange={e => setMember(i, { date_of_birth: e.target.value })} className="px-2 py-1 rounded text-sm outline-none" style={input} /></td>
                    <td className="px-2 py-1">
                      <select value={m.tier || ''} disabled={locked} onChange={e => setMember(i, { tier: e.target.value })} className="px-2 py-1 rounded text-sm outline-none" style={input}>
                        {/* Placeholder so the plan is a deliberate choice, not a default. */}
                        <option value="">Select plan…</option>
                        {(card?.tiers || [{ value: 'AD_ESSENTIAL', label: 'AD Essential' }]).map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </td>
                    <td className="px-2 py-1">{!locked && members.length > 1 && <button onClick={() => delRow(i)} style={{ color: theme.er }}><Trash2 className="w-4 h-4" /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-2 pt-1 flex-wrap">
            {editable && (
              <button onClick={save} disabled={busy} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: theme.orange, color: '#fff' }}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save &amp; rate
              </button>
            )}
            {current && editable && (
              <button onClick={doSubmit} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: '#1D4ED8', color: '#fff' }}>
                <CheckCircle2 className="w-4 h-4" /> Submit for review
              </button>
            )}
            {st === 'submitted' && stage !== 'final' && (
              <button onClick={doReview} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: '#059669', color: '#fff' }}>
                <CheckCircle2 className="w-4 h-4" /> Review &amp; forward
              </button>
            )}
            {st === 'submitted' && stage === 'final' && (
              <button onClick={doApprove} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: '#059669', color: '#fff' }}>
                <CheckCircle2 className="w-4 h-4" /> Approve (final)
              </button>
            )}
            {st === 'submitted' && (
              <button onClick={doReject} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: theme.g100, color: theme.er, border: `1px solid ${theme.er}55` }}>
                Send back
              </button>
            )}
            {st === 'approved' && (
              <button onClick={doInvoice} disabled={busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold disabled:opacity-50" style={{ background: theme.text, color: theme.card }}><Receipt className="w-4 h-4" /> Generate invoice</button>
            )}
            {st === 'submitted' && (
              <span className="text-xs" style={{ color: theme.t2 }}>In review · {current?.review_stage_label}</span>
            )}
          </div>
          {current?.status === 'rejected' && current?.reject_reason && (
            <div className="text-xs rounded-lg px-3 py-2 mt-2" style={{ background: theme.erB, color: theme.er }}>
              Sent back: {current.reject_reason}
            </div>
          )}
        </div>

        {/* Discounts per plan tier (Tlamelo 2026-06-25) */}
        <div className="rounded-2xl p-4 space-y-3" style={card_}>
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Discounts per plan tier</h3>
            <span className="text-xs" style={{ color: theme.t2 }}>applied to the quoted premium · saved with this quote</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {(card?.tiers || []).map(tt => {
              const gate = card?.tier_discount_gates?.[tt.value]
              return (
                <div key={tt.value}>
                  <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                    {tt.label}{gate ? <span className="opacity-70"> · {gate}</span> : null}
                  </label>
                  <div className="flex items-center gap-1">
                    <input type="number" min={0} max={100} step="0.5" disabled={locked}
                           value={discounts[tt.value] ?? ''}
                           onChange={e => setDiscounts(d => ({ ...d, [tt.value]: e.target.value }))}
                           className="w-full px-2 py-1.5 rounded-lg text-sm outline-none disabled:opacity-60" style={input} />
                    <span className="text-sm" style={{ color: theme.t2 }}>%</span>
                  </div>
                </div>
              )
            })}
          </div>
          <p className="text-xs" style={{ color: theme.t2 }}>
            Premier &amp; Status are gated to under-35 groups — 0% by default. Save to apply; the discount shows on the quote &amp; PDF.
          </p>
        </div>

        {/* Rated result — one tab per tier */}
        {current && tiers.length > 0 && (
          <RatedTabs theme={theme} quote={current} tiers={tiers} onDownload={download} onDownloadXlsx={downloadXlsx} onDownloadConsolidated={downloadConsolidated} />
        )}

        {current?.company && (
          <div className="rounded-2xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 mb-2">
              <Receipt className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold text-sm" style={{ color: theme.text }}>
                Invoice {current.invoice_no} · banking details
              </h3>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-1 text-sm" style={{ color: theme.t2 }}>
              <div>Bank<br/><span style={{ color: theme.text }}>{current.company.bank}</span></div>
              <div>Account name<br/><span style={{ color: theme.text }}>{current.company.account_name}</span></div>
              <div>Account no.<br/><span style={{ color: theme.text }}>{current.company.account_no}</span></div>
              <div>Branch code<br/><span style={{ color: theme.text }}>{current.company.branch_code}</span></div>
              <div>SWIFT<br/><span style={{ color: theme.text }}>{current.company.swift}</span></div>
              <div>VAT reg.<br/><span style={{ color: theme.text }}>{current.company.vat_reg}</span></div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

function StatusPill({ theme, status, label }: any) {
  const c = status === 'invoiced' ? '#7c3aed' : status === 'approved' ? '#059669' : '#6B7280'
  return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide" style={{ background: c + '22', color: c }}>
    {status === 'invoiced' && <Lock className="w-3 h-3" />}{label}</span>
}
function Banner({ theme, kind, children }: any) {
  const ok = kind === 'ok'
  return <div className="flex items-start gap-1.5 text-sm rounded-lg px-3 py-2"
    style={ok ? { background: theme.g100, color: '#16a34a' } : { background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
    {ok ? <CheckCircle2 className="w-4 h-4 mt-0.5" /> : <AlertTriangle className="w-4 h-4 mt-0.5" />}{children}</div>
}

function RatedTabs({ theme, quote, tiers, onDownload, onDownloadXlsx, onDownloadConsolidated }: any) {
  const [active, setActive] = useState(0)
  const t = tiers[active]
  const card_ = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  return (
    <div className="rounded-2xl overflow-hidden" style={card_}>
      <div className="flex items-center gap-1 px-3 pt-3 flex-wrap" style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
        {tiers.map((tt: any, i: number) => (
          <button key={tt.tier} onClick={() => setActive(i)} className="px-3 py-2 rounded-t-lg text-sm font-semibold"
                  style={{ background: i === active ? theme.orange : 'transparent', color: i === active ? '#fff' : theme.t2 }}>
            {tt.tier_label} <span className="opacity-70">({tt.lives})</span>
          </button>
        ))}
        <div className="flex-1" />
        <button onClick={onDownloadConsolidated} className="inline-flex items-center gap-1.5 px-3 py-1.5 mb-1 rounded-lg text-xs font-semibold" style={{ background: theme.orange, color: '#fff' }}><Download className="w-3.5 h-3.5" /> All tiers (Excel)</button>
        <button onClick={onDownloadXlsx} className="inline-flex items-center gap-1.5 px-3 py-1.5 mb-1 ml-1 rounded-lg text-xs font-semibold" style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}><Download className="w-3.5 h-3.5" /> Excel</button>
        <button onClick={onDownload} className="inline-flex items-center gap-1.5 px-3 py-1.5 mb-1 ml-1 rounded-lg text-xs font-semibold" style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}><Download className="w-3.5 h-3.5" /> PDF / print</button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
            {['#', 'Full name', 'Status', 'Gen', 'Age', 'Band', 'Excl VAT', 'VAT 14%', 'Incl VAT'].map((h, i) => (
              <th key={h} className={`px-3 py-2 text-[11px] uppercase tracking-wide font-semibold ${i >= 6 ? 'text-right' : ''}`} style={{ color: theme.t2 }}>{h}</th>
            ))}
          </tr></thead>
          <tbody>
            {t.members.map((m: HQMember, i: number) => (
              <tr key={m.id || i} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                <td className="px-3 py-1.5" style={{ color: theme.t2 }}>{i + 1}</td>
                <td className="px-3 py-1.5" style={{ color: theme.text }}>{m.full_name}</td>
                <td className="px-3 py-1.5" style={{ color: theme.t2 }}>{m.member_type_label}</td>
                <td className="px-3 py-1.5" style={{ color: theme.t2 }}>{m.gender}</td>
                <td className="px-3 py-1.5" style={{ color: theme.t2 }}>{m.age}</td>
                <td className="px-3 py-1.5 text-xs" style={{ color: theme.t2 }}>{m.age_band}</td>
                <td className="px-3 py-1.5 text-right tabular-nums" style={{ color: theme.t2 }}>{fmtWhole(m.premium_excl)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums" style={{ color: theme.t2 }}>{fmt(m.vat)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums font-semibold" style={{ color: theme.text }}>{fmt(m.premium_incl)}</td>
              </tr>
            ))}
            <tr style={{ borderTop: `2px solid ${theme.orange}`, background: theme.oL }}>
              <td className="px-3 py-2 font-bold uppercase text-xs" colSpan={6} style={{ color: theme.orange }}>{t.tier_label} {Number(t.discount_pct) > 0 ? 'rack' : 'total'}</td>
              <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmtWhole(t.subtotal_excl)}</td>
              <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmt(t.vat)}</td>
              <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmt(t.total_incl)}</td>
            </tr>
            {Number(t.discount_pct) > 0 && (
              <>
                <tr style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                  <td className="px-3 py-1.5 text-xs" colSpan={6} style={{ color: theme.t2 }}>Less discount ({Number(t.discount_pct)}%)</td>
                  <td className="px-3 py-1.5 text-right tabular-nums" style={{ color: '#DC2626' }}>−{fmtWhole(t.discount_excl)}</td>
                  <td /><td />
                </tr>
                <tr style={{ background: theme.oL }}>
                  <td className="px-3 py-2 font-bold uppercase text-xs" colSpan={6} style={{ color: theme.orange }}>{t.tier_label} net</td>
                  <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmtWhole(t.net_excl)}</td>
                  <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmt(t.net_vat)}</td>
                  <td className="px-3 py-2 text-right font-bold tabular-nums" style={{ color: theme.text }}>{fmt(t.net_total_incl)}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-3 flex flex-col items-end gap-1 text-sm" style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
        {Number(quote.discount_excl) > 0 && (
          <>
            <div className="flex justify-end gap-6"><span style={{ color: theme.t2 }}>Subtotal (rack, excl VAT)</span><span className="tabular-nums" style={{ color: theme.t2 }}>{fmtWhole(quote.gross_excl)}</span></div>
            <div className="flex justify-end gap-6"><span style={{ color: theme.t2 }}>Total discount</span><span className="tabular-nums" style={{ color: '#DC2626' }}>−{fmtWhole(quote.discount_excl)}</span></div>
          </>
        )}
        <div className="flex justify-end gap-6 items-baseline">
          <span style={{ color: theme.t2 }}>All tiers — total incl. VAT</span>
          <span className="font-bold tabular-nums text-base" style={{ color: theme.orange }}>{fmt(quote.total_incl)}</span>
        </div>
      </div>
    </div>
  )
}
