'use client'

/**
 * /hris/talent-cockpit — Development Dialogue "Talent Cockpit".
 *
 * CFO directive 2026-07-17: omni is the performance-evaluation system of
 * record — no more lost Excel files. This page hosts the cockpit (the 9-box
 * grid + editable Development Dialogue form + targets) and bridges its
 * load/save to the HRIS API (/hris/api/talent/cockpit/), which is gated to
 * the HR/CFO whitelist. Reads: view_talent tier. Writes: HR tier.
 *
 * The cockpit UI is the vetted self-contained app at
 * /public/talent-cockpit-app.html; this page owns auth + persistence so the
 * data lives on the server, audited, and reachable from any device.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Target } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch } from '../_shared'

const API = '/hris/api/talent/cockpit/'

type Person = Record<string, unknown>

export default function TalentCockpitPage() {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const canManageRef = useRef(false)
  const [status, setStatus] = useState('Loading…')
  const [doc, setDoc] = useState('')

  // Access is enforced server-side by the scoped API (exec/HR = everyone,
  // manager = their team, otherwise 403). So we load the app for any signed-in
  // user and let the API decide — no client-side whitelist gate.
  // omni sends X-Frame-Options: DENY, so a plain <iframe src> is refused;
  // fetch the same-origin app and mount via srcDoc (XFO doesn't apply).
  useEffect(() => {
    let live = true
    fetch('/talent-cockpit-app.html')
      .then((r) => r.text())
      .then((html) => {
        if (live) setDoc(html)
      })
      .catch(() => setStatus('Could not load the cockpit — refresh to retry'))
    return () => {
      live = false
    }
  }, [])

  const pushInit = useCallback(async (focus?: string) => {
    try {
      const r = await authedHrisFetch(API)
      // Regular staff have no cockpit scope — send them to their OWN dialogue
      // instead of a dead end (the HRIS-home tile is labelled "Development
      // Dialogue", so this is where they land expecting to see theirs).
      if (r.status === 403) { window.location.replace('/hris/my-dialogue'); return }
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      canManageRef.current = !!d.can_manage
      const people: Person[] = Array.isArray(d.people) ? d.people : []
      iframeRef.current?.contentWindow?.postMessage({ type: 'cockpit-init', people, focus }, '*')
      setStatus(
        `${people.length} ${people.length === 1 ? 'dialogue' : 'dialogues'} loaded` +
          (d.can_manage ? '' : ' · read-only'),
      )
    } catch {
      setStatus('Could not load from omni — refresh to retry')
    }
  }, [])

  const newPeriod = useCallback(async (ref: string, period: string) => {
    if (!canManageRef.current) { setStatus('Read-only — only HR / CFO can start a period'); return }
    setStatus('Creating new period…')
    try {
      const r = await authedHrisFetch('/hris/api/talent/new-period/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, period }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      const focus = (d.person && (d.person._ref || d.person.id)) as string | undefined
      await pushInit(focus)
      setStatus('New period created ✓')
    } catch {
      setStatus('Could not create new period — retry')
    }
  }, [pushInit])

  // Board [DD] — the roster the "create a dialogue for an employee" picker
  // chooses from. Scoped server-side (exec/HR all, manager their chain), so the
  // frame never decides who may be listed.
  const loadEmployees = useCallback(async () => {
    try {
      const r = await authedHrisFetch('/hris/api/talent/employees/')
      // A 403/500 does NOT throw, so without this it fell through to an empty
      // list and rendered as "there are no staff" — the fake all-clear this
      // whole path exists to avoid.
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'cockpit-employees-result', employees: d.employees || [] }, '*')
    } catch {
      // Say nothing rather than an empty roster that reads as "no staff exist".
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'cockpit-employees-result', employees: null }, '*')
    }
  }, [])

  const loadHistory = useCallback(async (key: string) => {
    try {
      const r = await authedHrisFetch('/hris/api/talent/history/?key=' + encodeURIComponent(key))
      const d = r.ok ? await r.json() : { periods: [] }
      iframeRef.current?.contentWindow?.postMessage({ type: 'cockpit-history-result', periods: d.periods || [] }, '*')
    } catch {
      iframeRef.current?.contentWindow?.postMessage({ type: 'cockpit-history-result', periods: [] }, '*')
    }
  }, [])

  const newPeriodAll = useCallback(async (period: string) => {
    if (!canManageRef.current) { setStatus('Read-only — cannot start periods'); return }
    setStatus('Opening new period for everyone…')
    try {
      const r = await authedHrisFetch('/hris/api/talent/new-period-all/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ period }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const d = await r.json()
      await pushInit()
      setStatus(`New period opened for ${d.rolled ?? 0} ✓`)
    } catch {
      setStatus('Could not open new period for all — retry')
    }
  }, [pushInit])

  const doDelete = useCallback(async (ref: string) => {
    if (!canManageRef.current) { setStatus('Read-only — cannot remove people'); return }
    setStatus('Removing…')
    try {
      const r = await authedHrisFetch('/hris/api/talent/delete/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      await pushInit()
      setStatus('Removed ✓')
    } catch {
      setStatus('Could not remove — retry')
    }
  }, [pushInit])

  const doSign = useCallback(async (ref: string, role: string) => {
    setStatus('Signing…')
    try {
      const r = await authedHrisFetch('/hris/api/talent/sign/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, role }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      await pushInit(ref)
      setStatus('Signed ✓')
    } catch {
      setStatus('Could not sign — retry')
    }
  }, [pushInit])

  const saveAll = useCallback(async (people: Person[]) => {
    if (!canManageRef.current) {
      setStatus('Read-only — only HR / CFO can save changes')
      return
    }
    setStatus('Saving…')
    try {
      const r = await authedHrisFetch(API, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ people }),
      })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const t = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
      setStatus('Saved ✓ ' + t)
    } catch {
      setStatus('Save failed — check connection and retry')
    }
  }, [])

  useEffect(() => {
    function onMsg(ev: MessageEvent) {
      // srcDoc inherits our origin, so genuine frame messages match; drop the rest.
      if (ev.origin !== window.location.origin) return
      const m = (ev.data || {}) as { type?: string; people?: Person[]; ref?: string; period?: string; key?: string; role?: string }
      if (m.type === 'cockpit-ready') pushInit()
      else if (m.type === 'cockpit-save') saveAll(m.people || [])
      else if (m.type === 'cockpit-reset') pushInit()
      else if (m.type === 'cockpit-new-period' && m.ref && m.period) newPeriod(m.ref, m.period)
      else if (m.type === 'cockpit-new-period-all' && m.period) newPeriodAll(m.period)
      else if (m.type === 'cockpit-sign' && m.ref && m.role) doSign(m.ref, m.role)
      else if (m.type === 'cockpit-delete' && m.ref) doDelete(m.ref)
      else if (m.type === 'cockpit-history' && m.key) loadHistory(m.key)
      else if (m.type === 'cockpit-employees') loadEmployees()
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [pushInit, saveAll, newPeriod, newPeriodAll, doSign, doDelete, loadHistory, loadEmployees])

  return (
    <div className="flex min-h-screen flex-col bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <div className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-900">
        <Link
          href="/hris"
          className="text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
          aria-label="Back to HR"
        >
          <ChevronLeft size={20} />
        </Link>
        <Target size={18} className="text-[#F07F00]" />
        <div>
          <h1 className="text-[15px] font-bold leading-tight text-[#0B0B3B] dark:text-white">
            Development Dialogue (All Employees)
          </h1>
          <p className="text-[11px] text-gray-500">
            Performance evaluation · system of record · scoped to what you manage
          </p>
        </div>
        <span className="ml-auto text-xs font-semibold text-gray-500">{status}</span>
      </div>
      <iframe
        ref={iframeRef}
        srcDoc={doc}
        onLoad={() => pushInit()}
        title="Development Dialogue — Talent Cockpit"
        className="w-full flex-1 border-0"
        style={{ minHeight: 'calc(100vh - 112px)' }}
      />
    </div>
  )
}
