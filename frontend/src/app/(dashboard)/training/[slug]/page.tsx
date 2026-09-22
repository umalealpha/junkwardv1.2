'use client'

/**
 * Trainee page — take the training, get a certificate.
 *
 * The page reads /api/v1/training/modules/<slug>/ which returns everything
 * needed to render the lesson AND the quiz (no correct answers). The submit
 * button POSTs answers to /submit/ which returns the score. On pass, the
 * page shows a link to the PDF certificate.
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  ArrowLeft, AlertCircle, Award, CheckCircle2, Clock, ExternalLink, RotateCcw,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch, apiFetchRaw } from '@/lib/api'

interface Question {
  id:      string
  order:   number
  prompt:  string
  options: { letter: string; text: string }[]
}
interface Attempt {
  score_pct:           number
  passed:              boolean
  certificate_number:  string | null
  completed_at:        string
}
interface Module {
  id: string; slug: string; title: string; subtitle: string
  body_html: string; video_url: string; pdf_url: string | null
  pass_mark_pct: number
  open_from: string; open_until: string
  is_published: boolean
  questions: Question[]
  my_best_attempt: Attempt | null
}

interface SubmitResp {
  attempt_id: string
  score_pct: number
  pass_mark_pct: number
  passed: boolean
  questions_total: number
  questions_correct: number
  certificate_number: string | null
}

export default function TrainingPage() {
  const { theme } = useTheme()
  const params = useParams<{ slug: string }>()
  const slug = params?.slug
  const [module, setModule]   = useState<Module | null>(null)
  const [error, setError]     = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult]   = useState<SubmitResp | null>(null)

  useEffect(() => {
    if (!slug) return
    setLoading(true)
    apiFetch<Module>(`/training/modules/${slug}/`)
      .then(m => {
        setModule(m)
        if (m.my_best_attempt?.passed) {
          setResult({
            attempt_id: '', score_pct: m.my_best_attempt.score_pct,
            pass_mark_pct: m.pass_mark_pct, passed: true,
            questions_total: m.questions.length,
            questions_correct: Math.round(m.my_best_attempt.score_pct * m.questions.length / 100),
            certificate_number: m.my_best_attempt.certificate_number,
          })
        }
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [slug])

  async function submit() {
    if (!module) return
    const missing = module.questions.filter(q => !answers[q.id])
    if (missing.length) {
      setError(`Please answer all questions — ${missing.length} left.`); return
    }
    setError(null); setSubmitting(true)
    try {
      const r = await apiFetch<SubmitResp>(`/training/modules/${slug}/submit/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers }),
      })
      setResult(r)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Submit failed')
    } finally {
      setSubmitting(false)
    }
  }

  function retry() { setResult(null); setAnswers({}) }

  const embed = module?.video_url ? toEmbedUrl(module.video_url) : null

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Training" />

      <div className="p-4 lg:p-6 max-w-[900px] mx-auto space-y-5">
        <Link href="/my-omni"
              className="inline-flex items-center gap-1.5 text-xs font-semibold"
              style={{ color: theme.orange }}>
          <ArrowLeft className="w-3.5 h-3.5" /> Back
        </Link>

        {loading && (
          <div className="rounded-xl p-6 text-sm"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t3 }}>
            Loading…
          </div>
        )}

        {error && !result && (
          <div className="rounded-md p-3 flex items-start gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {module && (
          <>
            {/* Header */}
            <div className="rounded-2xl p-6 lg:p-8 relative overflow-hidden"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
              <div className="absolute -top-24 -right-20 w-80 h-80 rounded-full pointer-events-none"
                   style={{ background: `radial-gradient(circle at 30% 30%, rgba(244,166,35,0.10) 0%, transparent 60%)`, filter: 'blur(30px)' }} />
              <div className="relative">
                <h1 className="font-display-tight text-3xl lg:text-4xl font-bold"
                    style={{ color: theme.navy }}>{module.title}</h1>
                {module.subtitle && (
                  <p className="font-display text-base mt-2" style={{ color: theme.t2 }}>
                    {module.subtitle}
                  </p>
                )}
                <div className="mt-4 flex items-center gap-3 text-xs" style={{ color: theme.t2 }}>
                  <span className="inline-flex items-center gap-1"><Clock className="w-3.5 h-3.5" />
                    Open {new Date(module.open_from).toLocaleDateString('en-BW')} – {new Date(module.open_until).toLocaleDateString('en-BW')}
                  </span>
                  <span>·</span>
                  <span>Pass mark {module.pass_mark_pct}%</span>
                  <span>·</span>
                  <span>{module.questions.length} question{module.questions.length === 1 ? '' : 's'}</span>
                </div>
              </div>
            </div>

            {/* Result banner */}
            {result && (
              <div className="rounded-2xl p-6"
                   style={{
                     background: result.passed ? '#ECFDF5' : '#FEF2F2',
                     border: `1px solid ${result.passed ? '#A7F3D0' : '#FECACA'}`,
                   }}>
                <div className="flex items-start gap-3">
                  {result.passed
                    ? <Award className="w-7 h-7 shrink-0" style={{ color: '#059669' }} />
                    : <AlertCircle className="w-7 h-7 shrink-0" style={{ color: '#DC2626' }} />
                  }
                  <div className="flex-1">
                    <h2 className="font-display-tight text-xl font-bold"
                        style={{ color: result.passed ? '#065F46' : '#7F1D1D' }}>
                      {result.passed ? 'Passed. Well done.' : 'Not quite — please try again.'}
                    </h2>
                    <p className="text-sm mt-1" style={{ color: result.passed ? '#065F46' : '#7F1D1D' }}>
                      You scored <b>{result.score_pct}%</b> ({result.questions_correct}/{result.questions_total}).
                      Pass mark is {result.pass_mark_pct}%.
                    </p>
                    <div className="mt-3 flex items-center gap-3 flex-wrap">
                      {result.passed && result.certificate_number && (
                        <a href={`/api/v1/training/attempts/${result.attempt_id || module.my_best_attempt?.certificate_number}/certificate.pdf`}
                           target="_blank" rel="noopener"
                           onClick={async (e) => {
                             if (!result.attempt_id) return
                             e.preventDefault()
                             const r = await apiFetchRaw(`/training/attempts/${result.attempt_id}/certificate.pdf`)
                             const blob = await r.blob()
                             const url = URL.createObjectURL(blob)
                             window.open(url, '_blank', 'noopener')
                           }}
                           className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-md"
                           style={{ background: '#059669', color: '#fff' }}>
                          <Award className="w-3.5 h-3.5" /> Download certificate
                        </a>
                      )}
                      {!result.passed && (
                        <button onClick={retry}
                                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-md"
                                style={{ background: '#DC2626', color: '#fff' }}>
                          <RotateCcw className="w-3.5 h-3.5" /> Try again
                        </button>
                      )}
                      {result.certificate_number && (
                        <span className="text-[11px] font-mono" style={{ color: theme.t2 }}>
                          Cert {result.certificate_number}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Lesson body */}
            {!result?.passed && (
              <>
                {module.body_html && (
                  <div className="rounded-xl p-6 prose max-w-none"
                       style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh, color: theme.text }}
                       dangerouslySetInnerHTML={{ __html: module.body_html }} />
                )}

                {embed && (
                  <div className="rounded-xl overflow-hidden"
                       style={{ background: '#000', border: `1px solid ${theme.cardBdr}` }}>
                    <div className="relative" style={{ paddingBottom: '56.25%' }}>
                      <iframe src={embed} title={module.title}
                              className="absolute inset-0 w-full h-full"
                              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                              allowFullScreen />
                    </div>
                  </div>
                )}

                {module.pdf_url && (
                  <a href={module.pdf_url} target="_blank" rel="noopener"
                     className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-2 rounded-md"
                     style={{ background: theme.navy, color: '#fff' }}>
                    <ExternalLink className="w-4 h-4" /> Open the training PDF
                  </a>
                )}

                {/* Quiz */}
                <div className="rounded-xl p-6"
                     style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
                  <h3 className="font-display text-lg font-bold mb-4" style={{ color: theme.navy }}>
                    Quiz
                  </h3>
                  <div className="space-y-6">
                    {module.questions.map((q, i) => (
                      <div key={q.id}>
                        <p className="text-sm font-semibold mb-2" style={{ color: theme.text }}>
                          {i + 1}. {q.prompt}
                        </p>
                        <div className="space-y-1.5">
                          {q.options.map(opt => (
                            <label key={opt.letter}
                                   className="flex items-start gap-2 p-2 rounded cursor-pointer"
                                   style={{
                                     background: answers[q.id] === opt.letter ? theme.g100 : 'transparent',
                                     border: `1px solid ${answers[q.id] === opt.letter ? theme.orange : theme.cardBdr}`,
                                   }}>
                              <input type="radio" name={q.id} value={opt.letter}
                                     checked={answers[q.id] === opt.letter}
                                     onChange={() => setAnswers({ ...answers, [q.id]: opt.letter })}
                                     className="mt-0.5" />
                              <span className="text-sm" style={{ color: theme.text }}>
                                <b>{opt.letter}.</b> {opt.text}
                              </span>
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  <button onClick={submit} disabled={submitting}
                          className="mt-6 text-sm font-semibold px-4 py-2 rounded-md disabled:opacity-50"
                          style={{ background: theme.orange, color: '#fff' }}>
                    {submitting ? 'Scoring…' : 'Submit answers'}
                  </button>
                  {error && (
                    <p className="mt-2 text-xs" style={{ color: theme.er }}>{error}</p>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function toEmbedUrl(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.hostname.includes('youtube.com') || u.hostname === 'youtu.be') {
      const id = u.hostname === 'youtu.be'
        ? u.pathname.slice(1)
        : (u.searchParams.get('v') || u.pathname.split('/').pop() || '')
      return id ? `https://www.youtube.com/embed/${id}` : url
    }
    if (u.hostname.includes('vimeo.com')) {
      const id = u.pathname.split('/').pop()
      return id ? `https://player.vimeo.com/video/${id}` : url
    }
    return url
  } catch { return null }
}
