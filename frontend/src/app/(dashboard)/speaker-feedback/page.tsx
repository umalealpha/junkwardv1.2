'use client'

/**
 * /speaker-feedback — CONFIDENTIAL. The speaker's own view of the audience
 * answers collected by the public /speak/<slug> form.
 *
 * CFO 2026-08-03. Restricted to the speaker alone by the server
 * (core.speaker_feedback_views.user_can_read_feedback — positive identity
 * match, no superuser or administrator bypass). This page probes that same
 * gate before rendering anything, so a non-reader never even sees the shape of
 * the data. The sidebar entry is hidden for everyone else too.
 */
import { useEffect, useState } from 'react'
import { ShieldAlert, Lock, Copy, Check, Users, MessageSquareQuote } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { getSpeakerFeedbackReport, type SpeakerFeedbackReport } from '@/lib/api'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const SLUG = 'ypo-ai-roadmap-2026'

function barColour(avg: number | null): string {
  if (avg === null) return '#cbd5e1'
  if (avg >= 4) return ORANGE
  if (avg >= 3) return '#f6c04d'
  return NAVY
}

export default function SpeakerFeedbackPage() {
  const [state, setState] = useState<'checking' | 'allowed' | 'denied'>('checking')
  const [report, setReport] = useState<SpeakerFeedbackReport | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    getSpeakerFeedbackReport(SLUG)
      .then((r) => { setReport(r); setState('allowed') })
      .catch(() => setState('denied'))
  }, [])

  function copyLink() {
    if (!report) return
    navigator.clipboard.writeText(report.public_link).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => { /* clipboard blocked — the link is on screen anyway */ })
  }

  return (
    <div>
      <TopBar
        title="Audience Feedback"
        breadcrumbs={[{ label: 'Confidential' }, { label: 'Audience Feedback' }]}
      />

      {state === 'checking' && (
        <div className="p-8 text-sm text-slate-400">Checking access…</div>
      )}

      {state === 'denied' && (
        <div className="mx-auto max-w-xl p-8">
          <div className="flex gap-3 rounded-xl border border-red-200 bg-red-50 p-6">
            <ShieldAlert className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-600" />
            <div>
              <p className="font-medium text-red-800">Access restricted</p>
              <p className="mt-1 text-sm text-red-700">
                These responses are confidential to the speaker.
              </p>
            </div>
          </div>
        </div>
      )}

      {state === 'allowed' && report && (
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          {/* Confidentiality banner */}
          <div className="flex items-start gap-3 rounded-xl border p-4"
               style={{ borderColor: '#c7d2e8', background: '#f4f7fd' }}>
            <Lock className="mt-0.5 h-5 w-5 flex-shrink-0" style={{ color: NAVY }} />
            <p className="text-sm text-slate-700">
              <span className="font-semibold">Confidential — your eyes only.</span>{' '}
              Nobody else in omni can open this page, including administrators. The
              respondents were told this, which is why they answered honestly.
            </p>
          </div>

          {/* Header + share link */}
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: ORANGE }}>
              {report.event}
            </p>
            <h2 className="mt-1 text-xl font-bold text-slate-900">{report.title}</h2>
            <p className="mt-1 text-sm text-slate-600">Session: {report.session_date}</p>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2">
                <Users className="h-4 w-4 text-slate-500" />
                <span className="text-sm font-semibold text-slate-800">
                  {report.response_count} {report.response_count === 1 ? 'response' : 'responses'}
                </span>
              </div>
            </div>

            <p className="mt-4 text-xs font-medium text-slate-500">
              Link to send the organiser (no login needed):
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <code className="rounded bg-slate-100 px-2 py-1.5 text-sm text-slate-800 break-all">
                {report.public_link}
              </code>
              <button
                type="button"
                onClick={copyLink}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-white"
                style={{ background: NAVY }}
              >
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>

          {/* Ranked pains */}
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="text-base font-bold text-slate-900">
              Where it hurts most — ranked
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Ranked by how many people called it a serious problem (scored 4 or 5),
              then by the average out of 5 ({report.scale_low} → {report.scale_high}).
              Always read the “answered” count — a 5.00 from one person is not a
              bigger problem than a 4.50 from twenty. Skipped statements are left out
              of the average, never counted as a middle score.
            </p>

            {report.response_count === 0 ? (
              <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-500">
                No answers yet. Send the link above to the organiser and this fills in
                as members reply.
              </p>
            ) : (
              <div className="mt-4 space-y-3">
                {report.summary.map((row, i) => (
                  <div key={row.key}>
                    <div className="flex items-baseline justify-between gap-3">
                      <p className="text-sm text-slate-800">
                        <span className="mr-2 font-semibold text-slate-400">{i + 1}.</span>
                        {row.statement}
                      </p>
                      <span className="flex-shrink-0 text-sm font-bold text-slate-900">
                        {row.average === null ? '—' : row.average.toFixed(2)}
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-3">
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${((row.average || 0) / 5) * 100}%`,
                            background: barColour(row.average),
                          }}
                        />
                      </div>
                      <span className="w-40 flex-shrink-0 text-right text-xs text-slate-500">
                        {row.top_box_pct === null
                          ? 'not answered'
                          : `${row.top_box_pct}% said 4 or 5 (${row.answered} answered)`}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Individual responses */}
          {report.responses.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-5">
              <h3 className="flex items-center gap-2 text-base font-bold text-slate-900">
                <MessageSquareQuote className="h-4 w-4" style={{ color: ORANGE }} />
                In their own words
              </h3>
              <div className="mt-4 space-y-4">
                {report.responses.map((r) => {
                  const who = [r.respondent_name, r.respondent_role, r.company_name]
                    .filter(Boolean).join(' · ')
                  const where = [r.industry, r.country, r.headcount_band && `${r.headcount_band} staff`]
                    .filter(Boolean).join(' · ')
                  return (
                    <div key={r.id} className="rounded-lg border border-slate-200 p-4">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <p className="text-sm font-semibold text-slate-900">
                          {who || 'Anonymous'}
                        </p>
                        <p className="text-xs text-slate-400">
                          {new Date(r.submitted_at).toLocaleDateString('en-GB', {
                            day: '2-digit', month: 'short', year: 'numeric',
                          })}
                        </p>
                      </div>
                      {where && <p className="text-xs text-slate-500">{where}</p>}
                      {r.respondent_email && (
                        <p className="text-xs text-slate-500">{r.respondent_email}</p>
                      )}

                      {r.biggest_question && (
                        <div className="mt-3">
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                            Wants answered
                          </p>
                          <p className="mt-0.5 text-sm text-slate-800">{r.biggest_question}</p>
                        </div>
                      )}
                      {r.wish_ai_did && (
                        <div className="mt-3">
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                            Wishes AI would take over
                          </p>
                          <p className="mt-0.5 text-sm text-slate-800">{r.wish_ai_did}</p>
                        </div>
                      )}
                      {r.tried_already && (
                        <div className="mt-3">
                          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                            Already tried
                          </p>
                          <p className="mt-0.5 text-sm text-slate-800">{r.tried_already}</p>
                        </div>
                      )}
                      <p className="mt-3 text-xs text-slate-400">
                        {r.may_quote
                          ? 'You may quote this anonymously on a slide.'
                          : 'Do NOT quote this — permission not given.'}
                      </p>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
