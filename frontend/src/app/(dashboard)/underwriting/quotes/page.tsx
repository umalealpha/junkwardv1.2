'use client'

/**
 * Quotations — one standard template, filled in from plain English.
 *
 * CFO/EXCO decision 2026-08-08: the loose Excel quotes stop. Ten real quotes
 * were reviewed and no two matched — two clients shared a file, one was a
 * competitor's schedule reused as our base, and VAT appeared three ways.
 *
 * The underwriter types what they have, the way they would say it on the phone.
 * Aria lays it out. They check it, correct anything, and issue. Everything the
 * system prints — the masthead, the licence line, the quote number, the VAT —
 * is not typeable.
 *
 * This sits beside the Document Generator (cover notes + WCA), because it is the
 * same job done by the same people in the same place.
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, draftQuoteFromText, createQuote, updateQuote,
  confirmQuotePremium, issueQuote, getQuotes, setQuoteOutcome, openQuotePdf,
  getQuoteRateFloors, loadQuoteFromPolicy, getQuoteTemplates, getLearnedExclusions,
  downloadQuoteFile, getQuote,
  type Quote, type QuoteSection, type QuoteDraftResult, type QuoteRateFloor,
  type QuotePolicyLookup, type QuoteTemplateOption, type LearnedExclusion,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Sparkles, FileText, AlertTriangle, CheckCircle2, RefreshCw, Send, Plus, Trash2,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const PLACEHOLDER =
  'Kalahari Freight, fleet of 14 — 6 heavy trucks 8.4m, 8 bakkies 3.12m, both retail value, ' +
  'excess 15k heavy and 7.5k light. Third party liability 5m any one event. Add passenger ' +
  'liability 250k and SADC transit. Broker Sesigo. Premium 418,600.'

function money(v: string | number | null | undefined): string {
  const n = Number(v || 0)
  if (!isFinite(n)) return '—'
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** '418,600' / '418.6k' / '8.4m' / 'P 15 000' -> a number. Mirrors
 *  underwriting/quote_parse.py::_to_decimal — keep the two in step. */
function parseMoney(raw: string | number): number {
  const s = String(raw ?? '').trim().toLowerCase().replace(/bwp/g, '').replace(/[, ]/g, '')
  const m = s.match(/^p?(-?[\d.]+)([km])?$/)
  if (!m) return Number(s.replace(/[^\d.]/g, '')) || 0
  const n = Number(m[1])
  if (!Number.isFinite(n)) return 0
  return m[2] === 'm' ? n * 1_000_000 : m[2] === 'k' ? n * 1_000 : n
}

export default function QuotesPage() {
  const router = useRouter()

  const [text, setText] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [draft, setDraft] = useState<QuoteDraftResult | null>(null)
  const [rows, setRows] = useState<QuoteSection[]>([])
  const [form, setForm] = useState({
    client_name: '', client_attn: '', class_of_business: '',
    period: '12 months', broker: '', agent: '', agent_email: '', premium: '',
    rate_pct: '', rate_incl_vat: true,
    // "What is not covered" — one per line; sent as a list. The document never
    // invents an exclusion, so if this is empty only the policy-wording pointer
    // prints (CFO 2026-08-10).
    exclusions_text: '',
    // Free-text notes — e.g. the Motor Excess Conditions block. Printed on the
    // quote line-for-line, exactly as typed (Gomolemo Sebudula, 14 Aug 2026).
    notes: '',
  })
  const [premiumSuggested, setPremiumSuggested] = useState(false)
  const [saved, setSaved] = useState<Quote | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [register, setRegister] = useState<Quote[]>([])
  const [loadingReg, setLoadingReg] = useState(false)
  const [floors, setFloors] = useState<QuoteRateFloor[]>([])
  const [templates, setTemplates] = useState<QuoteTemplateOption[]>([])
  const [learnedExcl, setLearnedExcl] = useState<LearnedExclusion[]>([])
  // Detailed is the default; simplified is the short version a client who only
  // wants the price asks for (CFO 2026-08-11).
  const [style, setStyle] = useState<'detailed' | 'simple'>('detailed')
  const [renewalPolicy, setRenewalPolicy] = useState('')
  const [renewalMsg, setRenewalMsg] = useState<string | null>(null)
  const [claimsInfo, setClaimsInfo] = useState<QuotePolicyLookup['claims'] | null>(null)

  const loadRegister = useCallback(() => {
    setLoadingReg(true)
    getQuotes().then((r) => setRegister(r.results || []))
      .catch(() => {}).finally(() => setLoadingReg(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    loadRegister()
    getQuoteRateFloors().then(setFloors).catch(() => {})
    getQuoteTemplates().then(setTemplates).catch(() => {})
  }, [router, loadRegister])

  // Suggestions are LEARNED from what underwriters have already typed for this
  // class — the wording converges instead of being retyped (CFO 2026-08-10).
  useEffect(() => {
    if (!getToken()) return
    getLearnedExclusions(form.class_of_business || '').then(setLearnedExcl).catch(() => {})
  }, [form.class_of_business])

  function applyTemplate(id: string) {
    const t = templates.find((x) => String(x.id) === id)
    if (!t) return
    setForm((f) => ({
      ...f,
      class_of_business: t.class_of_business || f.class_of_business,
      rate_pct: t.rate_pct && Number(t.rate_pct) > 0 ? String(t.rate_pct) : f.rate_pct,
      // Client quotes stay VAT-inclusive even if an older template was saved with
      // the add-on-top mode (CFO 2026-08-12).
      rate_incl_vat: true,
    }))
    if (t.sections?.length) setRows(t.sections.map((s) => ({ ...s })))
  }

  // VAT is shown live from the same rule the server uses, so the underwriter
  // never sees one number here and another on the PDF.
  // Reads k/m the same way the backend does. This module teaches k/m as the
  // house shorthand — the placeholder says "8.4m ... 15k" and the note reader
  // accepts it — but this box used to strip the letter and keep the digits, so
  // "418.6k" silently became 418.60 and a quotation for four hundred pula could
  // go to a broker. Two parsers, two answers, same string: exactly the "VAT three
  // different ways" disease this screen exists to end.
  // Premium can be TYPED or RATED. When a rate % is given, it drives the money:
  // rate % of the total sum insured. If the rate includes VAT (the usual case),
  // that figure IS the total and the net premium is backed out of it; otherwise
  // it is the net premium and VAT is added. Same rule the server recomputes on
  // save (quote_parse.price_from_rate), so the screen and the PDF never disagree.
  const rowRate = (r: QuoteSection) => parseFloat(String(r.rate || '').replace(/[^0-9.]/g, '')) || 0
  const totalSumInsured = rows.reduce((s, r) => s + (parseMoney(r.sum_insured || '') || 0), 0)
  const rateNum = parseFloat(String(form.rate_pct).replace(/[^0-9.]/g, '')) || 0
  // Per-product rating: if any cover row carries its own rate, the premium is
  // the sum of (row sum insured x row rate). This wins over a single quote-level
  // rate, which in turn wins over a typed premium — mirrors the server's recalc.
  const sectionRated = rows.some((r) => rowRate(r) > 0 && (parseMoney(r.sum_insured || '') || 0) > 0)
  const sectionFigure = rows.reduce((s, r) => {
    const si = parseMoney(r.sum_insured || '') || 0
    return s + (si > 0 && rowRate(r) > 0 ? (si * rowRate(r)) / 100 : 0)
  }, 0)
  const rated = sectionRated || rateNum > 0
  // Under per-product rating, a row with a sum insured but no rate is left OUT of
  // the premium — that product would be free. Flag those so Issue is blocked.
  const unratedCovered = sectionRated
    ? rows.filter((r) => (parseMoney(r.sum_insured || '') || 0) > 0 && !(rowRate(r) > 0)).map((r) => r.name || 'a cover row')
    : []
  // Under-pricing guard: lowest rate allowed for this class (exact class floor,
  // else the default). 0 = no floor set. The hard block is server-side at issue.
  const cls = (form.class_of_business || '').trim().toLowerCase()
  const floorRow = (cls && floors.find((f) => (f.class_of_business || '').trim() && (f.class_of_business || '').trim().toLowerCase() === cls))
    || floors.find((f) => !(f.class_of_business || '').trim())
  const floorPct = parseFloat(floorRow?.min_rate_pct || '0') || 0
  const belowFloor = floorPct > 0
    ? [
        ...(rateNum > 0 && rateNum < floorPct ? [`whole-quote ${rateNum}%`] : []),
        ...rows.filter((r) => rowRate(r) > 0 && rowRate(r) < floorPct).map((r) => `${r.name || 'a cover row'} ${rowRate(r)}%`),
      ]
    : []
  // CFO 2026-08-12: a RATE given to a client ALWAYS includes VAT. The rate % of
  // the sum insured IS the total the client pays; the net premium is backed out
  // (figure / 1.14) and VAT is the difference, so premium + VAT == total. The
  // add-VAT-on-top mode is gone from the client builder, so a rated quote can
  // never reach a client with VAT stacked on top again (Motlatsi 12 Aug). Same
  // one rule the server uses (_money_from_figure, incl_vat=True). A TYPED premium
  // is left unchanged: it is the net figure and VAT is added — a separate money
  // question flagged to the CFO, not changed silently here.
  let premiumNum: number, vatNum: number, totalNum: number
  if (rated) {
    const figure = Math.round((sectionRated ? sectionFigure : (totalSumInsured * rateNum) / 100) * 100) / 100
    totalNum = figure
    premiumNum = Math.round((figure / 1.14) * 100) / 100
    vatNum = Math.round((figure - premiumNum) * 100) / 100
  } else {
    premiumNum = parseMoney(form.premium)
    vatNum = Math.round(premiumNum * 0.14 * 100) / 100
    totalNum = Math.round((premiumNum + vatNum) * 100) / 100
  }

  async function runDraft() {
    if (!text.trim()) return
    setDrafting(true); setError(null)
    try {
      const r = await draftQuoteFromText(text)
      setDraft(r)
      setRows(r.draft.sections || [])
      setForm({
        client_name: r.draft.client_name || '',
        client_attn: r.draft.client_attn || '',
        class_of_business: r.draft.class_of_business || '',
        period: r.draft.period || '12 months',
        broker: r.draft.broker || '',
        agent: '', agent_email: '',
        premium: r.premium && Number(r.premium) > 0 ? r.premium : '',
        rate_pct: '', rate_incl_vat: true, exclusions_text: '', notes: '',
      })
      setPremiumSuggested(!!r.premium_is_suggested)
      setSaved(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that. Try rewording it.')
    } finally {
      setDrafting(false)
    }
  }

  // Manus QC 15-Aug-2026 + Gomolemo request: opening a quote from the register
  // must reveal the builder card (which holds Save changes / Issue / Open PDF /
  // Download PDF-Excel-Word) so drafts can be amended and issued quotes
  // downloaded. The builder is gated on `draft`; row-click also sets a synthetic
  // draft so the same card renders. No AI call, no server write.
  function hydrateBuilder(q: Quote) {
    setForm({
      client_name: q.client_name || '',
      client_attn: q.client_attn || '',
      class_of_business: q.class_of_business || '',
      period: q.period || '12 months',
      broker: q.broker || '',
      agent: q.agent || '', agent_email: q.agent_email || '',
      premium: q.premium || '',
      rate_pct: q.rate_pct || '', rate_incl_vat: !!q.rate_incl_vat,
      exclusions_text: '', notes: q.notes || '',
    })
    setRows((q.sections || []).map((s) => ({ ...s })))
    setDraft({
      ok: true, source: 'register',
      draft: {
        client_name: q.client_name || '', client_attn: q.client_attn || '',
        class_of_business: q.class_of_business || '', period: q.period || '',
        broker: q.broker || '', sections: q.sections || [],
      },
      premium: q.premium, vat: q.vat, vat_rate_pct: '', total: q.total,
      premium_is_suggested: !!q.premium_is_suggested, premium_basis: '', warnings: [],
    })
    setPremiumSuggested(!!q.premium_is_suggested)
    setSaved(q)
  }

  // Aria's warnings describe the note as it was TYPED. Once the underwriter fills
  // the gap they were pointing at, the warning has to go: an issued quotation was
  // still showing "No client name found" above the client's own name, which trains
  // people to ignore the amber box — including the one warning that matters, that
  // the premium is an estimate.
  const liveWarnings = (draft?.warnings ?? []).filter((w) => {
    if (w.startsWith('No client name found') && form.client_name.trim()) return false
    if (w.startsWith('No premium found') && premiumNum > 0) return false
    if (w.startsWith('No cover sections') && rows.length > 0) return false
    if (w.startsWith("This premium is Aria's estimate") && !premiumSuggested) return false
    return true
  })

  function setRow(i: number, key: keyof QuoteSection, value: string) {
    setRows((cur) => cur.map((r, idx) => (idx === i ? { ...r, [key]: value } : r)))
  }

  // Renewal: load a Graphite policy by number to pre-fill the client, class,
  // sum insured and last year's premium, and surface the client's claims.
  async function loadRenewal() {
    const p = renewalPolicy.trim()
    if (!p) return
    setRenewalMsg(null); setError(null)
    try {
      const r = await loadQuoteFromPolicy(p)
      if (!r.found) { setRenewalMsg(r.error || 'No policy with that number was found.'); return }
      setForm((f) => ({
        ...f,
        client_name: r.client_name || f.client_name,
        class_of_business: r.class_of_business || f.class_of_business,
      }))
      if (r.sum_insured && Number(r.sum_insured) > 0) {
        setRows((cur) => (cur.length ? cur : [{ group: '', name: r.class_of_business || 'Cover', sum_insured: r.sum_insured || '', basis: '', excess: '', rate: '' }]))
      }
      setClaimsInfo(r.claims || null)
      setSaved(null)
      setRenewalMsg(`Loaded ${r.policy_number}. Check the cover and rate it — last year's premium was P${Number(r.prior_annual_premium || 0).toLocaleString()}.`)
    } catch {
      setRenewalMsg('The renewal lookup is unavailable right now.')
    }
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const payload = {
        ...form, premium: String(premiumNum), sections: rows,
        // CFO 2026-08-12: client quotes are ALWAYS VAT-inclusive — never send the
        // add-VAT-on-top mode from this builder.
        rate_pct: String(rateNum || 0), rate_incl_vat: true,
        exclusions: form.exclusions_text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean),
        // A rated premium is deterministic, never a guess — clear the flag.
        premium_is_suggested: rated ? false : premiumSuggested,
        drafted_by_ai: !!draft, source_text: text,
      } as Partial<Quote>
      const q = saved ? await updateQuote(saved.id, payload) : await createQuote(payload)
      setSaved(q); setPremiumSuggested(q.premium_is_suggested); loadRegister()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.')
    } finally { setBusy(false) }
  }

  async function confirmPremium() {
    if (!saved) return
    setBusy(true); setError(null)
    try {
      const q = await confirmQuotePremium(saved.id, String(premiumNum))
      setSaved(q); setPremiumSuggested(false); loadRegister()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not confirm the premium.')
    } finally { setBusy(false) }
  }

  // One click on the wrong row used to be permanent — the outcome could not be
  // moved once set, and nothing reported a failure, so a mis-click quietly
  // corrupted the conversion rate the register exists to report. Ask first, and
  // say so when it does not save.
  async function markOutcome(q: Quote, outcome: 'won' | 'lost') {
    const label = outcome === 'won' ? 'won' : 'lost'
    if (!window.confirm(`Mark ${q.quote_number} — ${q.client_name} — as ${label}?`)) return
    setError(null)
    try {
      await setQuoteOutcome(q.id, outcome)
      loadRegister()
    } catch (e) {
      setError(e instanceof Error ? e.message : `Could not mark this quotation ${label}.`)
    }
  }

  async function issue() {
    if (!saved) return
    setBusy(true); setError(null)
    try {
      const q = await issueQuote(saved.id)
      setSaved(q); loadRegister()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not issue.')
    } finally { setBusy(false) }
  }

  const won = register.filter((q) => q.status === 'won').length
  const issued = register.filter((q) => q.status !== 'draft').length
  // Local date (YYYY-MM-DD) for the expired marker; valid_until is a plain date.
  const todayISO = localYmd(new Date())

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Quotations"
        breadcrumbs={[{ label: 'Underwriting', href: '/underwriting' }, { label: 'Quotations' }]}
      />

      <div className="flex-1 p-6 max-w-6xl space-y-4">

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* ── the ask box ─────────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="w-4 h-4" style={{ color: ORANGE }} />
              Describe the quote
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-[#6B7280] mb-2">
              Type it the way you would say it. Aria lays it out on the standard
              template — you check it before it goes anywhere.
            </p>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              placeholder={PLACEHOLDER}
              className="w-full bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm"
            />
            <div className="mt-3 flex items-center gap-3">
              <Button onClick={runDraft} disabled={drafting || !text.trim()}
                      leftIcon={<Sparkles className="w-4 h-4" />}>
                {drafting ? 'Reading…' : 'Draft the quotation'}
              </Button>
              {draft && (
                <span className="text-xs text-[#6B7280]">Laid out by {draft.source}</span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* ── start faster: a template, or load an existing policy ────────── */}
        <Card>
          <CardContent>
            {templates.length > 0 && (
              <div className="mb-3">
                <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">
                  Start from a template
                </label>
                <select
                  defaultValue=""
                  onChange={(e) => applyTemplate(e.target.value)}
                  className="w-64 bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm">
                  <option value="">— pick a product —</option>
                  {templates.map((t) => (
                    <option key={t.id} value={String(t.id)}>{t.name}</option>
                  ))}
                </select>
              </div>
            )}
            <p className="text-xs text-[#6B7280] mb-2">
              Renewing? Type the policy number to load the client, class, sum insured
              and last year&apos;s premium — and see the client&apos;s claims.
            </p>
            <div className="flex items-center gap-3 flex-wrap">
              <input
                value={renewalPolicy}
                onChange={(e) => setRenewalPolicy(e.target.value)}
                placeholder="e.g. COMG2025189299"
                className="w-64 bg-white border border-[#D1D5DB] rounded-md px-3 py-2 text-sm font-mono"
              />
              <Button variant="outline" onClick={loadRenewal} disabled={!renewalPolicy.trim()}>
                Load policy
              </Button>
              {renewalMsg && <span className="text-xs text-[#6B7280]">{renewalMsg}</span>}
            </div>
            {claimsInfo && (claimsInfo.count > 0 || Number(claimsInfo.paid) > 0) && (
              <div className="mt-3 rounded-lg p-3 flex items-start gap-2"
                   style={{ background: '#FFF8EC', border: `1px solid ${ORANGE}` }}>
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#9A6B00' }} />
                <div className="text-[13px]" style={{ color: '#7A5A16' }}>
                  <strong>This client has {claimsInfo.count} claim{claimsInfo.count === 1 ? '' : 's'} on file</strong>
                  {' '}— paid P{Number(claimsInfo.paid).toLocaleString()}, reserve P{Number(claimsInfo.reserve).toLocaleString()}. Price accordingly.
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── the draft, editable ─────────────────────────────────────────── */}
        {draft && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText className="w-4 h-4" style={{ color: NAVY }} />
                {saved ? saved.quote_number : 'Draft quotation'}
                {saved && (
                  <span className="text-xs font-normal px-2 py-0.5 rounded"
                        style={{ background: '#ECFDF5', color: '#047857' }}>
                    {saved.status_label}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>

              {liveWarnings.length > 0 && (
                <div className="mb-4 rounded-lg p-3" style={{ background: '#FFF8EC', border: '1px solid #F3E4C4' }}>
                  {liveWarnings.map((w, i) => (
                    <p key={i} className="text-[13px]" style={{ color: '#7A5A16' }}>• {w}</p>
                  ))}
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
                {([
                  ['client_name', 'Client *'], ['client_attn', 'Attention / address'],
                  ['class_of_business', 'Class of business'], ['period', 'Period'],
                  ['broker', 'Broker'],
                  // Agent — the producer who brought the business, printed next to
                  // the underwriter for accountability (CFO 2026-08-12).
                  ['agent', 'Agent'], ['agent_email', 'Agent email'],
                ] as const).map(([key, label]) => (
                  <div key={key}>
                    <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">{label}</label>
                    <input
                      value={(form as unknown as Record<string, string>)[key]}
                      onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                      list={key === 'broker' ? 'uw-brokers' : undefined}
                      className="w-full rounded px-2 py-1.5 text-sm border"
                      style={{ borderColor: key === 'client_name' && !form.client_name ? '#DC2626' : '#EAEEF3' }}
                    />
                  </div>
                ))}
              </div>
              <datalist id="uw-brokers">
                {Array.from(new Set(register.map((q) => q.broker).filter(Boolean))).map((b) => (
                  <option key={b} value={b} />
                ))}
              </datalist>

              {/* what is NOT covered — typed by the underwriter, never invented */}
              <div className="mb-4">
                <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">
                  What is not covered <span className="normal-case text-[10px]">(one per line)</span>
                </label>
                <textarea
                  value={form.exclusions_text}
                  onChange={(e) => setForm({ ...form, exclusions_text: e.target.value })}
                  rows={3}
                  placeholder={'Wear and tear, gradual deterioration and mechanical breakdown.\nStock left in the open.'}
                  className="w-full rounded px-2 py-1.5 text-sm border"
                  style={{ borderColor: '#EAEEF3' }}
                />
                {learnedExcl.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5 items-center">
                    <span className="text-[10px] uppercase tracking-wide text-[#6B7280]">Used before</span>
                    {learnedExcl.map((x) => (
                      <button
                        key={x.text}
                        type="button"
                        title={`Used on ${x.used} quotation${x.used === 1 ? '' : 's'}`}
                        onClick={() => setForm((f) => (
                          f.exclusions_text.includes(x.text) ? f : {
                            ...f,
                            exclusions_text: (f.exclusions_text.trim()
                              ? f.exclusions_text.replace(/\s*$/, '') + '\n' : '') + x.text,
                          }))}
                        className="text-[11px] rounded-full px-2 py-0.5 border"
                        style={{ borderColor: '#EAEEF3', color: NAVY, background: '#F5F7FB' }}>
                        + {x.text.length > 46 ? x.text.slice(0, 46) + '…' : x.text}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* notes — free text, printed on the quote line-for-line. e.g. the
                  Motor Excess Conditions block (Gomolemo Sebudula, 14 Aug 2026) */}
              <div className="mb-4">
                <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">
                  Notes <span className="normal-case text-[10px]">(printed on the quote, exactly as typed)</span>
                </label>
                <textarea
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  rows={4}
                  placeholder={'Motor Excess Conditions:\nBasic Excess: 10% of Each Claim, Minimum P3,500\nTheft/Hijacking Excess: 20% of Each Claim\nWindscreen Excess: 20% of Any Claim min P350'}
                  className="w-full rounded px-2 py-1.5 text-sm border"
                  style={{ borderColor: '#EAEEF3' }}
                />
              </div>

              {/* cover table — the underwriter corrects anything */}
              <div className="mb-4">
                <div className="flex items-center justify-between mb-1">
                  <label className="text-[11px] uppercase tracking-wide text-[#6B7280]">Cover</label>
                  <button
                    onClick={() => setRows([...rows, { group: '', name: '', sum_insured: '', basis: '', excess: '', rate: '' }])}
                    className="text-xs flex items-center gap-1" style={{ color: ORANGE }}>
                    <Plus className="w-3 h-3" /> add a row
                  </button>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[10px] uppercase tracking-wide text-[#6B7280]">
                        <th className="text-left pb-1 pr-2">Group</th>
                        <th className="text-left pb-1 pr-2">Section</th>
                        <th className="text-left pb-1 pr-2">Sum insured</th>
                        <th className="text-left pb-1 pr-2">Rate %</th>
                        <th className="text-left pb-1 pr-2">Basis</th>
                        <th className="text-left pb-1 pr-2">Excess</th>
                        <th className="text-left pb-1 pr-2">Origin</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <Fragment key={i}>
                        <tr>
                          {(['group', 'name', 'sum_insured', 'rate', 'basis', 'excess'] as const).map((k) => (
                            <td key={k} className="pr-2 pb-1">
                              <input
                                value={(r[k] as string) || ''}
                                onChange={(e) => setRow(i, k, e.target.value)}
                                className="w-full rounded px-2 py-1 text-sm border"
                                style={{ borderColor: '#EAEEF3' }}
                              />
                            </td>
                          ))}
                          {/* Motor: Imported / Local (CFO 2026-08-12, Motlatsi item 3) */}
                          <td className="pr-2 pb-1">
                            <select
                              value={r.origin || ''}
                              onChange={(e) => setRow(i, 'origin', e.target.value)}
                              className="w-full rounded px-1 py-1 text-sm border"
                              style={{ borderColor: '#EAEEF3' }}>
                              <option value="">—</option>
                              <option value="Local">Local</option>
                              <option value="Imported">Imported</option>
                            </select>
                          </td>
                          <td className="pb-1">
                            <button onClick={() => setRows(rows.filter((_, idx) => idx !== i))}
                                    className="text-[#B91C1C] p-1" title="Remove">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        </tr>
                        {/* Per-cover notes (Gomolemo Sebudula, 17 Aug 2026): one box per
                            cover, shown on the row where the cover's Group is named.
                            Printed directly under this cover on the quotation. */}
                        {!!(r.group || '').trim() && (
                        <tr>
                          <td colSpan={8} className="pr-2 pb-2">
                            <label className="text-[10px] uppercase tracking-wide text-[#6B7280]">
                              {`Notes for “${(r.group || '').trim()}” · prints on the quotation under this cover`}
                            </label>
                            <textarea
                              value={r.section_note || ''}
                              onChange={(e) => setRow(i, 'section_note', e.target.value)}
                              rows={2}
                              placeholder="Benefits, exclusions, extensions, conditions, warranties, excesses for this cover (separate from the general Notes below)."
                              className="mt-0.5 w-full rounded px-2 py-1 text-xs border"
                              style={{ borderColor: '#EAEEF3' }}
                            />
                          </td>
                        </tr>
                        )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* premium — RATED (a % of the sum insured) or typed. VAT and total never typed. */}
              <div className="flex flex-wrap gap-5 items-start">
                <div>
                  <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">
                    Premium rate % <span className="normal-case text-[10px]">(whole quote)</span>
                  </label>
                  <input
                    value={form.rate_pct}
                    onChange={(e) => { setForm({ ...form, rate_pct: e.target.value }); setPremiumSuggested(false) }}
                    placeholder="e.g. 3"
                    readOnly={sectionRated}
                    title={sectionRated ? 'Per-product rates are set in the cover table — clear those to rate the whole quote at one %' : undefined}
                    className="w-28 rounded px-2 py-1.5 text-sm border font-mono"
                    style={{ borderColor: '#EAEEF3', background: sectionRated ? '#F5F7FA' : '#fff' }}
                  />
                  <p className="mt-1.5 text-[11px] text-[#6B7280]">
                    Rate includes VAT — the figure is the client price; VAT is shown separately below.
                  </p>
                </div>
                <div>
                  <label className="block text-[11px] uppercase tracking-wide text-[#6B7280] mb-1">
                    Annual premium (BWP)
                  </label>
                  <input
                    value={rated ? money(premiumNum) : form.premium}
                    onChange={(e) => { setForm({ ...form, premium: e.target.value }); setPremiumSuggested(false) }}
                    readOnly={rated}
                    title={rated ? 'Worked out from the rate — clear the rate % to type a premium instead' : undefined}
                    className="w-44 rounded px-2 py-1.5 text-sm border font-mono"
                    style={{ borderColor: premiumSuggested ? ORANGE : '#EAEEF3',
                             background: rated ? '#F5F7FA' : '#fff' }}
                  />
                </div>
                <div className="text-sm pt-5">
                  <div className="text-[#6B7280]">VAT at 14% <span className="font-mono ml-2">{money(vatNum)}</span></div>
                  <div className="font-bold" style={{ color: NAVY }}>
                    Total payable <span className="font-mono ml-2">{money(totalNum)}</span>
                  </div>
                </div>
              </div>
              {rated && (
                <div className="mt-2 text-[12px]" style={{ color: '#6B7280' }}>
                  {sectionRated
                    ? <>Per-product rates (incl VAT) &rarr; </>
                    : <>{rateNum}% (incl VAT) of sum insured {money(totalSumInsured)} &rarr; </>}
                  premium {money(premiumNum)} + VAT {money(vatNum)} ={' '}
                  <span style={{ color: NAVY, fontWeight: 600 }}>{money(totalNum)}</span>
                </div>
              )}

              {premiumSuggested && (
                <div className="mt-3 rounded-lg p-3 flex items-start gap-2"
                     style={{ background: '#FFF8EC', border: `1px solid ${ORANGE}` }}>
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#9A6B00' }} />
                  <div className="text-[13px]" style={{ color: '#7A5A16' }}>
                    <strong>This premium is Aria&apos;s estimate, not your figure.</strong>
                    {draft.premium_basis ? ` ${draft.premium_basis}.` : ''} Check it, change it if
                    it is wrong, then confirm. The quotation will not issue until you do.
                    {saved && (
                      <div className="mt-2">
                        <Button size="sm" onClick={confirmPremium} disabled={busy}
                                leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />}>
                          Confirm this premium
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {unratedCovered.length > 0 && (
                <div className="mt-3 rounded-lg p-3 flex items-start gap-2"
                     style={{ background: '#FFF8EC', border: `1px solid ${ORANGE}` }}>
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#9A6B00' }} />
                  <div className="text-[13px]" style={{ color: '#7A5A16' }}>
                    <strong>These products have a sum insured but no rate,</strong> so they are not in the
                    premium: {unratedCovered.slice(0, 4).join(', ')}. Give them a rate, or clear their sum
                    insured, before issuing.
                  </div>
                </div>
              )}

              {belowFloor.length > 0 && (
                <div className="mt-3 rounded-lg p-3 flex items-start gap-2"
                     style={{ background: '#FEF2F2', border: '1px solid #DC2626' }}>
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#B91C1C' }} />
                  <div className="text-[13px]" style={{ color: '#7F1D1D' }}>
                    <strong>Below the minimum rate for this class ({floorPct}%):</strong>{' '}
                    {belowFloor.slice(0, 4).join(', ')}. Raise the rate before issuing.
                  </div>
                </div>
              )}

              <div className="mt-4 flex flex-wrap gap-3">
                <Button onClick={save} disabled={busy || !form.client_name.trim()}>
                  {busy ? 'Saving…' : saved ? 'Save changes' : 'Save draft'}
                </Button>
                {saved && saved.status === 'draft' && (
                  <Button onClick={issue} disabled={busy || premiumSuggested || unratedCovered.length > 0 || belowFloor.length > 0}
                          leftIcon={<Send className="w-4 h-4" />}>
                    Issue quotation
                  </Button>
                )}
                {saved && saved.status !== 'draft' && (
                  // Fetched with the auth header, not a bare link: the endpoint
                  // needs the token and a plain <a href> would just 401.
                  <Button variant="outline" disabled={busy}
                          leftIcon={<FileText className="w-4 h-4" />}
                          onClick={() => {
                            setError(null)
                            openQuotePdf(saved.id).catch((e) =>
                              setError(e instanceof Error ? e.message : 'Could not open the PDF.'))
                          }}>
                    Open the PDF
                  </Button>
                )}
              </div>

              {/* Download — detailed (default) or simplified, in any format. */}
              {saved && (
                <div className="mt-4 pt-3 border-t" style={{ borderColor: '#EAEEF3' }}>
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-[11px] uppercase tracking-wide text-[#6B7280]">Download</span>
                    <div className="inline-flex rounded-md overflow-hidden border" style={{ borderColor: '#EAEEF3' }}>
                      {([['detailed', 'Detailed'], ['simple', 'Simplified']] as const).map(([v, label]) => (
                        <button
                          key={v}
                          type="button"
                          onClick={() => setStyle(v)}
                          className="text-xs px-3 py-1.5"
                          style={style === v
                            ? { background: NAVY, color: '#fff' }
                            : { background: '#fff', color: '#6B7280' }}>
                          {label}
                        </button>
                      ))}
                    </div>
                    {([['pdf', 'PDF'], ['xlsx', 'Excel'], ['docx', 'Word']] as const).map(([fmt, label]) => (
                      <Button key={fmt} size="sm" variant="outline" disabled={busy}
                              onClick={() => {
                                setError(null)
                                downloadQuoteFile(saved.id, fmt, style, `${saved.quote_number}.${fmt}`)
                                  .catch((e) => setError(e instanceof Error ? e.message : 'Could not build that file.'))
                              }}>
                        {label}
                      </Button>
                    ))}
                  </div>
                  <p className="mt-1.5 text-[11px] text-[#6B7280]">
                    {style === 'detailed'
                      ? 'Every cover row, the exclusions and the conditions.'
                      : 'The premium table only — no row-by-row schedule or exclusions.'}
                    {' '}Excel carries live formulas, so changing a sum insured re-prices it.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* ── the register ────────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Quote register</CardTitle>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#6B7280]">
                  {issued} issued · {won} converted
                  {issued > 0 && ` · ${Math.round((won / issued) * 100)}% conversion`}
                </span>
                <button onClick={loadRegister} className="text-[#6B7280]" title="Refresh">
                  <RefreshCw className={`w-4 h-4 ${loadingReg ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {register.length === 0 ? (
              <p className="text-sm text-[#9CA3AF] italic">No quotations yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-wide text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="text-left py-2 pr-3">Quote</th>
                      <th className="text-left py-2 pr-3">Client</th>
                      <th className="text-left py-2 pr-3">Class</th>
                      <th className="text-left py-2 pr-3">Broker</th>
                      <th className="text-left py-2 pr-3">Underwriter</th>
                      <th className="text-right py-2 pr-3">Total</th>
                      <th className="text-left py-2 pr-3">Status</th>
                      <th className="text-left py-2">Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {register.map((q) => (
                      // Manus QC 14-Aug-2026: register rows had no download or
                      // preview action — the whole list was read-only. Row-click
                      // (and Enter/Space) now loads the quote back into the
                      // builder above so the existing PDF / Excel / Word buttons
                      // work against it. The "won/lost" outcome buttons keep
                      // their own click semantics via stopPropagation.
                      <tr
                        key={q.id}
                        role="button"
                        tabIndex={0}
                        onClick={async () => {
                          try {
                            const loaded = await getQuote(q.id, q.company)
                            hydrateBuilder(loaded)
                            window.scrollTo({ top: 0, behavior: 'smooth' })
                          } catch { /* toast handled by apiFetch */ }
                        }}
                        onKeyDown={async (e) => {
                          if (e.key !== 'Enter' && e.key !== ' ') return
                          e.preventDefault()
                          try {
                            const loaded = await getQuote(q.id, q.company)
                            hydrateBuilder(loaded)
                            window.scrollTo({ top: 0, behavior: 'smooth' })
                          } catch { /* handled */ }
                        }}
                        className="border-b border-[#F3F4F6] cursor-pointer hover:bg-[#FAFBFC] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F4A623]"
                      >
                        <td className="py-2 pr-3 font-mono text-xs" style={{ color: NAVY }}>{q.quote_number}</td>
                        <td className="py-2 pr-3">{q.client_name}</td>
                        <td className="py-2 pr-3 text-[#6B7280]">{q.class_of_business || '—'}</td>
                        <td className="py-2 pr-3 text-[#6B7280]">{q.broker || '—'}</td>
                        <td className="py-2 pr-3 text-[#6B7280]">{q.underwriter_name || '—'}</td>
                        <td className="py-2 pr-3 text-right font-mono">{money(q.total)}</td>
                        <td className="py-2 pr-3">
                          {q.status_label}
                          {/* An issued quote past its 30-day validity still read
                              "Issued" here — only the public verify page knew it
                              had expired. Display-only marker (valid_until is
                              already returned); nothing changes the stored status. */}
                          {q.status === 'issued' && q.valid_until && q.valid_until < todayISO && (
                            <span className="ml-1.5 text-[10px] font-semibold uppercase tracking-wide"
                                  style={{ color: '#B91C1C' }}>· expired</span>
                          )}
                        </td>
                        <td className="py-2" onClick={(e) => e.stopPropagation()}>
                          {['issued', 'lapsed', 'won', 'lost'].includes(q.status) ? (
                            <span className="flex gap-2 items-center">
                              <button onClick={() => markOutcome(q, 'won')}
                                      className="text-xs"
                                      style={{ color: q.status === 'won' ? '#047857' : '#9CA3AF',
                                               fontWeight: q.status === 'won' ? 600 : 400 }}>won</button>
                              <button onClick={() => markOutcome(q, 'lost')}
                                      className="text-xs"
                                      style={{ color: q.status === 'lost' ? '#B91C1C' : '#9CA3AF',
                                               fontWeight: q.status === 'lost' ? 600 : 400 }}>lost</button>
                              {q.converted_policy_number && (
                                <span className="text-xs text-[#6B7280]">{q.converted_policy_number}</span>
                              )}
                            </span>
                          ) : (
                            <span className="text-xs text-[#9CA3AF]">
                              {q.converted_policy_number || '—'}
                            </span>
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
