'use client'

/**
 * /speak/[slug] — PUBLIC (no login) pre-session audience feedback.
 *
 * CFO 2026-08-03. The event organiser circulates this ONE link to their
 * members; each member rates ten AI pain statements 1-5 and answers three open
 * questions so the speaker can build the talk around real problems instead of
 * guessing. Identity is optional — the page says so, because an anonymous
 * honest answer is worth more than a named polite one.
 *
 * Answers are readable by the speaker alone (server-gated). The page states
 * that plainly above the form; that promise is what buys the honesty.
 */
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  getSpeakerFeedbackForm, submitSpeakerFeedback, type SpeakerFeedbackForm,
} from '@/lib/api'
import { Lock, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50">
      <div className="px-5 py-3 text-white" style={{ background: NAVY }}>
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <Lock className="h-5 w-5" style={{ color: ORANGE }} />
          <span className="font-semibold">Confidential pre-session feedback</span>
        </div>
      </div>
      {children}
    </div>
  )
}

export default function SpeakerFeedbackPage() {
  const params = useParams<{ slug: string }>()
  const slug = (params?.slug as string) || ''

  const [form, setForm] = useState<SpeakerFeedbackForm | null>(null)
  const [state, setState] = useState<'loading' | 'ok' | 'bad' | 'done'>('loading')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  const [ratings, setRatings] = useState<Record<string, number>>({})
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [who, setWho] = useState<Record<string, string>>({})
  const [mayQuote, setMayQuote] = useState(false)

  useEffect(() => {
    if (!slug) { setState('bad'); setMsg('This link is missing its code.'); return }
    getSpeakerFeedbackForm(slug)
      .then((f) => { setForm(f); setState('ok') })
      .catch((e) => {
        setState('bad')
        setMsg(e instanceof Error ? e.message : 'This feedback link is not valid.')
      })
  }, [slug])

  async function submit() {
    if (!form) return
    setMsg('')
    const required = form.open_questions.filter(q => q.required)
    const missing = required.find(q => !(answers[q.key] || '').trim())
    if (missing) { setMsg(`Please answer: ${missing.label}`); return }
    if (form.require_company && !(who.company_name || '').trim()) {
      setMsg('Please enter your company name.'); return
    }
    if (form.pains.length > 0 && Object.keys(ratings).length === 0) {
      setMsg('Please rate at least one of the ten statements.'); return
    }
    setSaving(true)
    try {
      await submitSpeakerFeedback(slug, {
        pain_ratings: ratings,
        biggest_question: answers.biggest_question || '',
        wish_ai_did: answers.wish_ai_did || '',
        tried_already: answers.tried_already || '',
        respondent_name: who.respondent_name || '',
        respondent_email: who.respondent_email || '',
        respondent_role: who.respondent_role || '',
        company_name: who.company_name || '',
        industry: who.industry || '',
        country: who.country || '',
        headcount_band: who.headcount_band || '',
        may_quote: mayQuote,
      })
      setState('done')
    } catch (e) {
      setMsg(e instanceof Error ? e.message : 'Could not send your answers. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  if (state === 'loading') {
    return (
      <Shell>
        <div className="mx-auto max-w-3xl px-4 py-20 text-center text-slate-500">
          <Loader2 className="mx-auto mb-3 h-6 w-6 animate-spin" />
          Opening the form…
        </div>
      </Shell>
    )
  }

  if (state === 'bad') {
    return (
      <Shell>
        <div className="mx-auto max-w-xl px-4 py-20 text-center">
          <AlertTriangle className="mx-auto mb-3 h-10 w-10" style={{ color: ORANGE }} />
          <h1 className="text-xl font-bold text-slate-900">This feedback link can’t be opened</h1>
          <p className="mt-2 text-sm text-slate-600">{msg}</p>
        </div>
      </Shell>
    )
  }

  if (state === 'done') {
    return (
      <Shell>
        <div className="mx-auto max-w-xl px-4 py-20 text-center">
          <CheckCircle2 className="mx-auto mb-3 h-12 w-12 text-emerald-500" />
          <h1 className="text-xl font-bold text-slate-900">Thank you — that’s in.</h1>
          <p className="mt-2 text-sm text-slate-600">
            Your answers go to the speaker only. They will shape the session on{' '}
            {form?.session_date}.
          </p>
        </div>
      </Shell>
    )
  }

  if (!form) return null

  if (form.closed) {
    return (
      <Shell>
        <div className="mx-auto max-w-xl px-4 py-20 text-center">
          <AlertTriangle className="mx-auto mb-3 h-10 w-10" style={{ color: ORANGE }} />
          <h1 className="text-xl font-bold text-slate-900">This form has closed</h1>
          <p className="mt-2 text-sm text-slate-600">
            Feedback for “{form.title}” is no longer being collected.
          </p>
        </div>
      </Shell>
    )
  }

  const scale = [1, 2, 3, 4, 5]
  const simple = form.pains.length === 0     // simplified form: company + one problem box
  const closingLabel = form.closes_on
    ? new Date(form.closes_on + 'T00:00:00Z').toLocaleDateString('en-GB',
        { day: 'numeric', month: 'long', year: 'numeric' })
    : ''

  return (
    <Shell>
      <div className="mx-auto max-w-3xl px-4 py-8">
        {/* Heading */}
        <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: ORANGE }}>
          {form.event}
        </p>
        <h1 className="mt-1 text-2xl font-bold text-slate-900">{form.title}</h1>
        <p className="mt-1 text-sm text-slate-600">
          {form.speaker} · {form.session_date}
        </p>
        {closingLabel && (
          <p className="mt-1 text-xs font-semibold" style={{ color: ORANGE }}>
            Closes {closingLabel}
          </p>
        )}
        <p className="mt-4 text-[15px] leading-relaxed text-slate-700">{form.intro}</p>

        {/* The confidentiality promise — this is what buys honest answers. */}
        <div className="mt-4 flex gap-3 rounded-lg border border-slate-200 bg-white p-4">
          <Lock className="mt-0.5 h-5 w-5 flex-shrink-0" style={{ color: NAVY }} />
          <p className="text-sm text-slate-700">{form.confidentiality}</p>
        </div>

        {/* Pain rating grid — only when the survey has pain statements */}
        {!simple && (
          <>
            <h2 className="mt-8 text-lg font-bold text-slate-900">
              1. How much is each of these a problem for you?
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              1 = {form.scale_low} · 5 = {form.scale_high}. Skip any that don’t apply.
            </p>
            <div className="mt-4 space-y-2">
              {form.pains.map((pain, i) => (
                <div key={pain.key} className="rounded-lg border border-slate-200 bg-white p-4">
                  <p className="text-[15px] text-slate-800">
                    <span className="mr-2 font-semibold text-slate-400">{i + 1}.</span>
                    {pain.statement}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {scale.map((n) => {
                      const on = ratings[pain.key] === n
                      return (
                        <button
                          key={n}
                          type="button"
                          aria-label={`${pain.statement} — rate ${n} out of 5`}
                          aria-pressed={on}
                          onClick={() => setRatings((r) => ({ ...r, [pain.key]: n }))}
                          className="h-10 w-10 rounded-lg border text-sm font-semibold transition-colors"
                          style={on
                            ? { background: NAVY, borderColor: NAVY, color: '#fff' }
                            : { background: '#fff', borderColor: '#cbd5e1', color: '#334155' }}
                        >
                          {n}
                        </button>
                      )
                    })}
                    {ratings[pain.key] !== undefined && (
                      <button
                        type="button"
                        onClick={() => setRatings((r) => {
                          const next = { ...r }; delete next[pain.key]; return next
                        })}
                        className="ml-1 text-xs text-slate-500 underline hover:text-slate-800"
                      >
                        clear
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Company name — only required when the survey demands it; optional otherwise */}
        {simple && (
          <div className="mt-6 rounded-lg border border-slate-200 bg-white p-4">
            <label htmlFor="w-company_name" className="block text-[15px] font-medium text-slate-800">
              Company name
              {form.require_company
                ? <span className="ml-1" style={{ color: ORANGE }}>*</span>
                : <span className="ml-2 text-xs font-normal text-slate-500">(optional)</span>}
            </label>
            <input
              id="w-company_name"
              type="text"
              value={who.company_name || ''}
              onChange={(e) => setWho((w) => ({ ...w, company_name: e.target.value }))}
              className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
              placeholder={form.require_company ? 'Your business' : 'Leave blank to stay anonymous'}
            />
          </div>
        )}

        {/* The problem — the one open box */}
        <div className={simple ? 'mt-4 space-y-4' : 'mt-8 space-y-4'}>
          {!simple && (
            <h2 className="text-lg font-bold text-slate-900">2. In your own words</h2>
          )}
          {form.open_questions.map((q) => (
            <div key={q.key} className="rounded-lg border border-slate-200 bg-white p-4">
              <label htmlFor={`q-${q.key}`} className="block text-[15px] font-medium text-slate-800">
                {q.label}
                {q.required && <span className="ml-1" style={{ color: ORANGE }}>*</span>}
              </label>
              <textarea
                id={`q-${q.key}`}
                rows={simple ? 5 : 3}
                value={answers[q.key] || ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.key]: e.target.value }))}
                className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                placeholder="Type here…"
              />
            </div>
          ))}
        </div>

        {/* Optional name (simplified mode) OR the full identity block (rated mode) */}
        {simple ? (
          <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
            <label htmlFor="w-respondent_name" className="block text-xs font-medium text-slate-600">
              Your name (only if you want a reply)
            </label>
            <input
              id="w-respondent_name"
              type="text"
              value={who.respondent_name || ''}
              onChange={(e) => setWho((w) => ({ ...w, respondent_name: e.target.value }))}
              className="mt-1 w-full rounded-lg border border-slate-300 p-2.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
              placeholder="Leave blank to stay anonymous"
            />
          </div>
        ) : (
          <>
            <h2 className="mt-8 text-lg font-bold text-slate-900">
              3. About you <span className="text-sm font-normal text-slate-500">(all optional)</span>
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Leave every field blank to answer anonymously.
            </p>
            <div className="mt-4 grid gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-2">
              {([
                ['respondent_name', 'Your name'],
                ['company_name', 'Company'],
                ['respondent_role', 'Your role'],
                ['industry', 'Industry'],
                ['country', 'Country'],
                ['respondent_email', 'Email (only if you want a reply)'],
              ] as const).map(([key, label]) => (
                <div key={key}>
                  <label htmlFor={`w-${key}`} className="block text-xs font-medium text-slate-600">
                    {label}
                  </label>
                  <input
                    id={`w-${key}`}
                    type={key === 'respondent_email' ? 'email' : 'text'}
                    value={who[key] || ''}
                    onChange={(e) => setWho((w) => ({ ...w, [key]: e.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 p-2.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                  />
                </div>
              ))}
              <div>
                <label htmlFor="w-headcount" className="block text-xs font-medium text-slate-600">
                  How many people work in the business?
                </label>
                <select
                  id="w-headcount"
                  value={who.headcount_band || ''}
                  onChange={(e) => setWho((w) => ({ ...w, headcount_band: e.target.value }))}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                >
                  <option value="">Prefer not to say</option>
                  {form.headcount_bands.map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
              </div>
            </div>

            <label className="mt-4 flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={mayQuote}
                onChange={(e) => setMayQuote(e.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                The speaker may quote my answer during the session, without naming me or my
                company.
              </span>
            </label>
          </>
        )}

        {msg && (
          <p className="mt-4 rounded-lg border border-orange-200 bg-orange-50 p-3 text-sm text-orange-800">
            {msg}
          </p>
        )}

        <button
          type="button"
          onClick={submit}
          disabled={saving}
          className="mt-6 w-full rounded-lg px-6 py-3.5 text-base font-semibold text-white disabled:opacity-60 sm:w-auto"
          style={{ background: ORANGE }}
        >
          {saving ? 'Sending…' : 'Send my answers'}
        </button>

        <p className="mt-8 border-t border-slate-200 pt-4 text-xs text-slate-400">
          Collected by Alpha Direct Insurance Company (Pty) Ltd on behalf of the speaker.
          Answers are used to prepare this session only.
        </p>
      </div>
    </Shell>
  )
}
