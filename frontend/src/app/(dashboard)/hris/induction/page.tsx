'use client'

/**
 * /hris/induction — the staff-facing induction course.
 *
 * CFO directive 2026-09-20: Dorothy was teaching the Conditions of Service to
 * every new joiner by hand. This is that talk, as something a person clicks
 * through inside Omni — slides, then the exam, then the certificate.
 *
 * This page owns auth and the API; the experience itself is the self-contained
 * app at /public/induction-app.html. Omni sends X-Frame-Options: DENY, so a
 * plain <iframe src> is refused — we fetch the app and mount it via srcDoc, the
 * same way the Development Dialogue cockpit does.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, GraduationCap } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch } from '../_shared'

const SLUG = 'induction-conditions-of-service-2026'
const API = `/hris/api/training/courses/${SLUG}/`

export default function InductionPage() {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [doc, setDoc] = useState('')
  const [status, setStatus] = useState('Loading…')

  useEffect(() => {
    let live = true
    fetch('/induction-app.html')
      .then((r) => r.text())
      .then((html) => { if (live) setDoc(html) })
      .catch(() => setStatus('Could not load the course — refresh to retry'))
    return () => { live = false }
  }, [])

  const post = useCallback((msg: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage({ target: 'induction', ...msg }, '*')
  }, [])

  const pushInit = useCallback(async () => {
    try {
      const r = await authedHrisFetch(API)
      if (r.status === 404) { setStatus('The induction course has not been set up yet.'); return }
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      setStatus('')
      post({
        type: 'init',
        course: d.course,
        slides: d.slides,
        slidesSeen: d.my_progress?.slides_seen ?? [],
        dwell: d.my_progress?.dwell_seconds ?? 0,
      })
    } catch {
      setStatus('Could not load the course — refresh to retry')
    }
  }, [post])

  useEffect(() => {
    async function onMessage(ev: MessageEvent) {
      // Only listen to OUR iframe. Every action here is self-scoped, so the
      // blast radius is small, but a page should not take instructions from
      // any window that can guess a message shape.
      if (ev.source !== iframeRef.current?.contentWindow) return
      const d = ev.data
      if (!d || d.source !== 'induction') return

      if (d.type === 'ready') {
        pushInit()
        return
      }

      if (d.type === 'heartbeat') {
        // Credit reading time. The server caps what one beat is worth, so this
        // is the honest record of time on the material.
        try {
          const r = await authedHrisFetch(`${API}progress/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ seconds: d.seconds, slides_seen: d.slidesSeen }),
          })
          if (r.ok) {
            const p = await r.json()
            post({
              type: 'progress',
              dwell: p.dwell_seconds,
              unlocked: p.exam_unlocked,
              reason: p.reason,
            })
          }
        } catch { /* a dropped heartbeat is not worth interrupting the reader */ }
        return
      }

      if (d.type === 'start-exam') {
        try {
          const r = await authedHrisFetch(`${API}start/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          })
          const p = await r.json()
          if (!r.ok) { post({ type: 'error', message: p.detail || 'Could not start the exam.' }); return }
          post({ type: 'paper', paper: p.paper, attemptId: p.attempt_id, paperNumber: p.paper_number })
        } catch {
          post({ type: 'error', message: 'Could not start the exam — please try again.' })
        }
        return
      }

      if (d.type === 'submit') {
        try {
          const r = await authedHrisFetch(`/hris/api/training/attempts/${d.attemptId}/submit/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers: d.answers }),
          })
          const p = await r.json()
          if (!r.ok) { post({ type: 'error', message: p.detail || 'Could not submit.' }); return }
          post({ type: 'result', result: p })
        } catch {
          post({ type: 'error', message: 'Could not submit your answers — please try again.' })
        }
        return
      }

      if (d.type === 'download-certificate' && d.certificateId) {
        // The PDF is rendered on demand and streamed through Omni (/media/ is
        // not served in production), so it needs the auth header — fetch it as
        // a blob rather than pointing the browser at the URL.
        try {
          const r = await authedHrisFetch(
            `/hris/api/training/certificates/${d.certificateId}/pdf/`)
          if (!r.ok) throw new Error('HTTP ' + r.status)
          const blob = await r.blob()
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = 'Alpha-Direct-Induction-Certificate.pdf'
          document.body.appendChild(a)
          a.click()
          a.remove()
          setTimeout(() => URL.revokeObjectURL(url), 30_000)
        } catch {
          post({ type: 'error', message: 'Could not download the certificate.' })
        }
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [post, pushInit])

  return (
    <div className="min-h-screen bg-[var(--t-bg)]">
      <TopBar />
      <div className="mx-auto max-w-6xl px-4 py-6">
        <div className="mb-4 flex items-center gap-3">
          <Link
            href="/hris"
            className="inline-flex items-center gap-1 text-sm text-[var(--t-muted)] hover:text-[var(--t-text)]"
          >
            <ChevronLeft className="h-4 w-4" />
            People
          </Link>
          <span className="text-[var(--t-muted)]">/</span>
          <span className="inline-flex items-center gap-2 text-sm font-medium">
            <GraduationCap className="h-4 w-4" />
            Induction
          </span>
        </div>

        {status && (
          <div className="rounded-xl border border-[var(--t-border)] bg-[var(--t-card)] p-8 text-center text-sm text-[var(--t-muted)]">
            {status}
          </div>
        )}

        {doc && (
          <iframe
            ref={iframeRef}
            srcDoc={doc}
            title="Alpha Direct induction course"
            className="w-full rounded-xl border border-[var(--t-border)] bg-white"
            style={{ height: 'calc(100vh - 150px)', minHeight: 620 }}
          />
        )}
      </div>
    </div>
  )
}
