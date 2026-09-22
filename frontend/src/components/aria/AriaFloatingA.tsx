'use client'

/**
 * Aria — floating cartoon-rat assistant.
 *
 * CFO directive 2026-05-22: Office-Assistant-reborn. Sprite reshaped
 * 2026-05-22 from an orange "A" wedge to a fun grey rat (pink ears,
 * big eyes, pink tail, paws up) per CFO style call. Rat animates
 * continuously — breathing body, swishing tail, wiggling ears,
 * twitching whiskers, blinking, eye-tracking the cursor, plus an
 * excited paw wave when a toast fires.
 *
 * Phase 0 — sprite (eye-track + blink + bob), click-to-chat, voice TTS,
 *           per-user kill switch.
 * Phase 1 — backend compliance calendar + grounded tools.
 * Phase 2 — drag-drop ingest (this file).
 * Phase 3 — personality dial: formal / sharp-sharp / naughty.
 * Phase 5 — proactive toast alarms (urgent deadlines, TB unbalanced).
 */

import { useEffect, useRef, useState } from 'react'
import { usePathname } from 'next/navigation'
import { authedHrisFetch } from '@/app/(dashboard)/hris/_shared'
import {
  Send, X, Loader2, Volume2, VolumeX, Power,
  Smile, Zap, Sparkles, Upload, Bell, BellOff,
} from 'lucide-react'
import dynamic from 'next/dynamic'
import { useTheme } from '@/contexts/ThemeContext'
import { getMe } from '@/lib/api'

// The Aria character uses three.js (~600 KB). Load it lazily (client-only) so
// that heavy 3D library is NOT baked into every dashboard page's initial
// bundle — the page paints and is usable first, then the mascot streams in
// (CFO 2026-07-16, "make omni faster"). No behaviour change: the same orb and
// panel character render; only the moment they load shifts to after paint.
const AriaRatGLB = dynamic(
  () => import('./AriaRatGLB').then(m => m.AriaRatGLB),
  { ssr: false, loading: () => null },
)

interface PendingAction {
  action: 'approve' | 'reject'
  ref: string
  payee: string
  amount: string
  currency?: string
  status: string
  reason?: string
  notes?: string
  confirm_token: string
  expires_in_seconds: number
  state?: 'open' | 'busy' | 'done' | 'cancelled' | 'error'
  result?: string
}

interface Msg { role: 'user' | 'assistant'; content: string; action?: PendingAction }

// Casual proactive one-liners. {name} → user's first name (or 'there'
// when no real name is available, so we never read "Dumela System").
// CFO directive 2026-05-27 (revised 2026-05-28): cleaner, mostly-English
// copy with light Setswana flavour. Welcome-style + helpful.
const ARIA_CHATTER = [
  "Dumela {name}, welcome back to Aria. Anything I can crunch for you?",
  "Hey {name} 👋 — got a P&L, TB or invoice for me to chew through?",
  "{name}, I'm right here when you need numbers. Sharp sharp.",
  "Need a quick quote, {name}? Drop a brief into Health Care → Quick Quote.",
  "{name} — stuck on something? Ask me. I read xlsx, csv, pdf and docx.",
  "Hi {name}, want a bank rec, an aging report or a cash position? Say the word.",
  "{name}, fancy a one-line P&L summary? Ask away.",
  "Hello {name} — drag any file onto me and I'll route it to the right pipeline.",
  "{name}, deadlines coming up? I can list what's due before month-end.",
  "{name} — ask me anything: GL postings, payables, payments, payroll. I've got it.",
]

const STORAGE_KILLED      = 'aria_killed'
const STORAGE_VOICE       = 'aria_voice_on'
const STORAGE_OPEN        = 'aria_open'
const STORAGE_HISTORY     = 'aria_history'   // persist chat across reloads
const STORAGE_POS         = 'aria_pos'       // user-chosen {right,bottom} anchor

// Default anchor — bottom-right, above the Team Chat launcher (CFO 2026-06-10).
const DEFAULT_POS = { right: 18, bottom: 96 }

// Conversation starter chips — shown when the panel opens with an empty
// thread. Click → fills + sends. Keep tight; max 4. House style.
const ARIA_STARTERS: string[] = [
  "What's due this week?",
  'Show me FY26 GWP',
  'Any pending approvals?',
  'Bills over 90 days',
]
const STORAGE_MOOD        = 'aria_mood'     // 'formal' | 'sharp' | 'naughty'
const STORAGE_MUTE_ALARMS = 'aria_mute_alarms'
const STORAGE_LAST_ALARM  = 'aria_last_alarm_ts'
const STORAGE_LAST_NEWS_TS = 'aria_last_news_ts'
const STORAGE_LAST_NEWS_ID = 'aria_last_news_id'

const NEWS_COOLDOWN_MS = 3 * 60 * 60 * 1000   // 3 hours per CFO directive
const NEWS_POLL_MS     = 30 * 60 * 1000        // poll every 30 minutes

type Mood = 'formal' | 'sharp' | 'naughty'

async function ariaFetch(path: string, init?: RequestInit) {
  return authedHrisFetch(path, init)
}

interface UrgentDeadline {
  name: string
  authority: string
  due_date: string
  days_left: number
  category: string
}

interface NewsItem {
  title: string
  summary: string
  link: string
  source: string
  published: string
  severity: number    // 1–5
  major: boolean       // severity >= 4
  query: string
}

interface ToastPayload {
  kind: 'deadline' | 'news' | 'chatter'
  message: string
  link?: string
  major?: boolean
}

// Phase 2 — drag-drop routing table. Maps current pathname → upload
// endpoint + form-field name. Files dropped on the sprite go straight
// to the right pipeline. Multiple files OK; each routed separately.
const UPLOAD_ROUTES: Record<string, { url: string; field: string; needs?: string }> = {
  '/hris/payroll':           { url: '/api/v1/payroll/amendments/upload/', field: 'file', needs: 'target_period_id + baseline_period_id (use page form)' },
  '/reports/trial-balance':  { url: '/api/v1/smart-upload/preview/', field: 'file' },
  '/journal-entries':        { url: '/api/v1/smart-upload/preview/', field: 'file' },
  '/accounts':               { url: '/api/v1/smart-upload/preview/', field: 'file' },
  '/assets':                 { url: '/api/v1/asset-imports/upload-parse/', field: 'file' },
  '/contacts':               { url: '/api/v1/smart-upload/preview/', field: 'file' },
  '/reinsurance/treaties':   { url: '/api/v1/reinsurance/treaties/upload-parse/', field: 'file' },
}

function findUploadRoute(pathname: string): { url: string; field: string; needs?: string } | null {
  for (const [prefix, route] of Object.entries(UPLOAD_ROUTES)) {
    if (pathname === prefix || pathname.startsWith(prefix + '/')) return route
  }
  return null
}

// Keep Aria's anchor inside the viewport (she is ~126px; leave a margin so
// the hotspot can never be dragged fully off-screen and become unreachable).
// `w` is the on-screen width of the thing being positioned — 130 for the
// sprite, ~396 for the open chat panel. Bug 35ee3aeb: when the panel was open
// the anchor still clamped to the sprite width, so dragging left past the
// panel edge grew `right` invisibly and the next right-drag felt dead until it
// "caught up". Clamping to the actual width keeps the drag 1:1 with the cursor.
function clampPos(right: number, bottom: number, w = 130): { right: number; bottom: number } {
  if (typeof window === 'undefined') return { right, bottom }
  return {
    right:  Math.max(0, Math.min(right,  window.innerWidth  - w)),
    bottom: Math.max(0, Math.min(bottom, window.innerHeight - 130)),
  }
}

export default function AriaFloatingA() {
  const { themeKey } = useTheme()
  const pathname = usePathname() || '/'
  const [mounted, setMounted] = useState(false)
  const [killed, setKilled] = useState(false)
  const [voiceOn, setVoiceOn] = useState(false)
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<Msg[]>([])
  const [cursor, setCursor] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const [blink, setBlink] = useState(false)
  const [urgent, setUrgent] = useState<UrgentDeadline[]>([])
  const [mood, setMood] = useState<Mood>('sharp')
  const [moodOpen, setMoodOpen] = useState(false)
  const [alarmMuted, setAlarmMuted] = useState(false)
  const [toast, setToast] = useState<ToastPayload | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [pulse, setPulse] = useState(false)
  const [firstName, setFirstName] = useState('')
  // CFO directive 2026-06-10: users can drag Aria anywhere on screen.
  // Anchor is stored as {right,bottom} so the default corner behaviour is
  // unchanged until the user actually moves her. Position persists per user.
  const [pos, setPos] = useState<{ right: number; bottom: number }>(DEFAULT_POS)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({
    active: false, moved: false,
    startX: 0, startY: 0,
    baseRight: DEFAULT_POS.right, baseBottom: DEFAULT_POS.bottom,
    lastRight: DEFAULT_POS.right, lastBottom: DEFAULT_POS.bottom,
  })
  const spriteRef = useRef<HTMLDivElement | null>(null)
  const confirmingRef = useRef<Set<number>>(new Set())
  const panelEndRef = useRef<HTMLDivElement | null>(null)
  // Live mirror of `open` so the chatter timer can check it without
  // resetting the interval every time the panel toggles.
  const openRef = useRef(open)
  openRef.current = open

  // ── Mount + restore ───────────────────────────────────────────────
  useEffect(() => {
    setMounted(true)
    try {
      setKilled(localStorage.getItem(STORAGE_KILLED) === '1')
      setVoiceOn(localStorage.getItem(STORAGE_VOICE) === '1')
      // Bug 35ee3aeb: ALWAYS start closed. The panel used to be restored open
      // from STORAGE_OPEN, so after logout/login it reappeared open and felt
      // like it "couldn't be dismissed". The sprite is always there to reopen.
      setOpen(false)
      // Redesign pack 05 §3: proactive news/deadline toasts are MUTED BY
      // DEFAULT so the landing experience is calm. A user who has never
      // touched the bell (key absent) starts muted; power users who enabled
      // chatter (stored '0') keep it on. The mute toggle in the chat header
      // persists their choice either way.
      {
        const storedMute = localStorage.getItem(STORAGE_MUTE_ALARMS)
        setAlarmMuted(storedMute === null ? true : storedMute === '1')
      }
      const m = localStorage.getItem(STORAGE_MOOD) as Mood | null
      if (m === 'formal' || m === 'sharp' || m === 'naughty') setMood(m)
      // Restore last chat thread so the conversation persists across reloads.
      const raw = localStorage.getItem(STORAGE_HISTORY)
      if (raw) {
        const parsed = JSON.parse(raw)
        // A Confirm card never survives a reload: its token is not stored and it
        // would have expired anyway, so an unfinished card comes back as expired.
        if (Array.isArray(parsed)) setHistory((parsed as Msg[]).slice(-30).map(m => (
          m.action && (m.action.state === 'open' || m.action.state === 'busy')
            ? { ...m, action: { ...m.action, state: 'error' as const, result: 'Expired — ask again.' } }
            : m)))
      }
      // Restore the user's chosen Aria position (clamped — the viewport may
      // have shrunk since they dragged her there).
      const rawPos = localStorage.getItem(STORAGE_POS)
      if (rawPos) {
        const p = JSON.parse(rawPos)
        if (typeof p?.right === 'number' && typeof p?.bottom === 'number') {
          setPos(clampPos(p.right, p.bottom))
        }
      }
    } catch { /* ignore */ }
  }, [])

  // Persist chat thread to localStorage (capped at 30 turns to stay tiny).
  useEffect(() => {
    if (!mounted) return
    try {
      localStorage.setItem(STORAGE_HISTORY, JSON.stringify(history.slice(-30).map(m => (
        m.action ? { ...m, action: { ...m.action, confirm_token: '' } } : m))))
    } catch { /* quota etc — ignore */ }
  }, [history, mounted])

  // ── User first name (for proactive chatter) ──────────────────────
  // BUG (Oprah report 2026-06-11): ARIA greeted with the WRONG/stale name on
  // shared browsers because getMe() serves a 5-min cache and this effect read
  // it once on mount — so a new login saw the previous user's cached name.
  // Force a fresh /me fetch so the greeting always reflects the CURRENT
  // logged-in user, never a stale cross-user value.
  useEffect(() => {
    let cancelled = false
    getMe({ force: true }).then((me) => {
      if (cancelled || !me) return
      // Prefer first_name; fall back to the email local-part, then username.
      let fn = (me.first_name || '').trim()
      if (!fn) {
        const local = (me.email || me.username || '').split('@')[0]
        fn = local.replace(/[._-]/g, ' ').split(/\s+/)[0]
      }
      // Reject non-human placeholder names so chatter never reads
      // "Dumela System" / "Dumela Admin". Falls back to 'there'.
      const blacklist = new Set(['system', 'admin', 'administrator', 'omni',
                                  'aria', 'user', 'test'])
      if (fn && !blacklist.has(fn.toLowerCase())) {
        setFirstName(fn.charAt(0).toUpperCase() + fn.slice(1).toLowerCase())
      }
    }).catch(() => { /* ignore */ })
    return () => { cancelled = true }
  }, [])

  // ── Proactive chatter every 15 min (CFO directive 2026-05-27) ─────
  // Aria randomly greets the user by first name with a casual one-liner.
  // Skipped while the chat panel is open (don't interrupt), or killed.
  useEffect(() => {
    if (killed) return
    const fire = () => {
      if (openRef.current) return
      const line = ARIA_CHATTER[Math.floor(Math.random() * ARIA_CHATTER.length)]
        .replace('{name}', firstName || 'there')
      setToast({ kind: 'chatter', message: line })
      setPulse(true)
      setTimeout(() => setPulse(false), 4000)
      setTimeout(() => setToast((t) => (t && t.kind === 'chatter' ? null : t)), 11000)
    }
    const warm = setTimeout(fire, 30_000)             // first quip after 30s
    const tid  = setInterval(fire, 15 * 60 * 1000)    // then every 15 min
    return () => { clearTimeout(warm); clearInterval(tid) }
  }, [killed, firstName])

  // ── Urgent deadlines poll (Phase 1) ───────────────────────────────
  useEffect(() => {
    if (!mounted || killed) return
    let cancelled = false
    const fetchOnce = async () => {
      try {
        const r = await ariaFetch('/api/v1/aria/urgent-deadlines/')
        if (!r.ok || cancelled) return
        const data = await r.json()
        if (!cancelled) setUrgent(Array.isArray(data?.urgent) ? data.urgent : [])
      } catch { /* ignore */ }
    }
    fetchOnce()
    const tid = setInterval(fetchOnce, 15 * 60 * 1000)
    return () => { cancelled = true; clearInterval(tid) }
  }, [mounted, killed])

  // ── Phase 6 — news popups (CFO directive 2026-05-22) ─────────────
  // 3-hour cooldown for normal items; `major` items bypass.
  useEffect(() => {
    if (!mounted || killed || alarmMuted) return
    let cancelled = false

    const showNews = (item: NewsItem) => {
      // Dedupe — never show the same headline twice even after cooldown.
      let lastId = ''
      try { lastId = localStorage.getItem(STORAGE_LAST_NEWS_ID) || '' } catch { /* ignore */ }
      if (lastId === item.link) return

      let lastTs = 0
      try { lastTs = Number(localStorage.getItem(STORAGE_LAST_NEWS_TS) || '0') } catch { /* ignore */ }
      if (!item.major && (Date.now() - lastTs) < NEWS_COOLDOWN_MS) return

      const src = item.source ? ` — ${item.source}` : ''
      setToast({
        kind: 'news',
        message: item.title + src,
        link: item.link,
        major: item.major,
      })
      setPulse(true)
      try {
        localStorage.setItem(STORAGE_LAST_NEWS_TS, String(Date.now()))
        localStorage.setItem(STORAGE_LAST_NEWS_ID, item.link)
      } catch { /* ignore */ }
      setTimeout(() => setPulse(false), 6000)
      setTimeout(() => setToast(t => (t && t.kind === 'news' ? null : t)), item.major ? 25000 : 15000)
      speak(item.major
        ? `Important news. ${item.title}.`
        : `In the news. ${item.title}.`)
    }

    const fetchOnce = async () => {
      try {
        const r = await ariaFetch('/api/v1/aria/news/')
        if (!r.ok || cancelled) return
        const data = await r.json()
        const top: NewsItem | null = data?.top || null
        if (top) showNews(top)
      } catch { /* ignore */ }
    }
    fetchOnce()
    const tid = setInterval(fetchOnce, NEWS_POLL_MS)
    return () => { cancelled = true; clearInterval(tid) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted, killed, alarmMuted])

  // ── Phase 5 — proactive toast (max 1/hr, opt-out) ────────────────
  useEffect(() => {
    if (!mounted || killed || alarmMuted) return
    if (urgent.length === 0) return
    try {
      const last = Number(localStorage.getItem(STORAGE_LAST_ALARM) || '0')
      if (Date.now() - last < 60 * 60 * 1000) return   // cooldown
    } catch { /* ignore */ }

    const top = urgent[0]
    const msg = `${top.name} due in ${top.days_left} day${top.days_left === 1 ? '' : 's'} (${top.authority})`
    setToast({ kind: 'deadline', message: msg })
    setPulse(true)
    try { localStorage.setItem(STORAGE_LAST_ALARM, String(Date.now())) } catch { /* ignore */ }
    setTimeout(() => { setPulse(false) }, 6000)
    setTimeout(() => { setToast(null) }, 12000)
    speak(`Sharp sharp. ${msg}.`)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urgent, alarmMuted, killed, mounted])

  // ── Eye tracking ─────────────────────────────────────────────────
  useEffect(() => {
    if (!mounted || killed) return
    function onMove(e: MouseEvent) { setCursor({ x: e.clientX, y: e.clientY }) }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [mounted, killed])

  // ── Idle blink (no bob — vertical motion removed, CFO 2026-06-04) ──
  useEffect(() => {
    if (!mounted || killed) return
    const blinkTimer = setInterval(() => {
      setBlink(true); setTimeout(() => setBlink(false), 140)
    }, 4500 + Math.random() * 3000)
    return () => { clearInterval(blinkTimer) }
  }, [mounted, killed])

  // ── Auto-scroll chat ─────────────────────────────────────────────
  useEffect(() => {
    if (open) panelEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, open])

  function persist(key: string, value: string) {
    try { localStorage.setItem(key, value) } catch { /* ignore */ }
  }

  function toggleOpen() {
    const next = !open
    setOpen(next)
    persist(STORAGE_OPEN, next ? '1' : '0')
  }

  // ── Drag-to-move (CFO directive 2026-06-10) ───────────────────────
  // Pointer-event drag on the sprite hotspot AND the chat-panel header.
  // <5px of movement counts as a click (open/close keeps working); more
  // is a drag. Position is {right,bottom} so resizing keeps her corner-
  // relative, persisted in localStorage per user.
  function onDragStart(e: React.PointerEvent<HTMLElement>) {
    // Don't hijack header buttons (mood / mute / voice / kill / close).
    if ((e.target as HTMLElement).closest('button')) return
    const d = dragRef.current
    d.active = true; d.moved = false
    d.startX = e.clientX; d.startY = e.clientY
    d.baseRight = pos.right; d.baseBottom = pos.bottom
    d.lastRight = pos.right; d.lastBottom = pos.bottom
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function onDragMove(e: React.PointerEvent<HTMLElement>) {
    const d = dragRef.current
    if (!d.active) return
    const moveRight  = d.startX - e.clientX   // dragging left  → bigger right offset
    const moveBottom = d.startY - e.clientY   // dragging up    → bigger bottom offset
    if (!d.moved && Math.hypot(moveRight, moveBottom) < 5) return
    if (!d.moved) { d.moved = true; setDragging(true) }
    const next = clampPos(d.baseRight + moveRight, d.baseBottom + moveBottom, open ? 396 : 130)
    d.lastRight = next.right; d.lastBottom = next.bottom
    setPos(next)
  }

  function onDragEnd() {
    const d = dragRef.current
    if (!d.active) return
    d.active = false
    setDragging(false)
    if (d.moved) {
      persist(STORAGE_POS, JSON.stringify({ right: d.lastRight, bottom: d.lastBottom }))
    }
  }

  // Click handler that ignores the click which ends a drag.
  function onSpriteClick() {
    if (dragRef.current.moved) { dragRef.current.moved = false; return }
    toggleOpen()
  }

  function toggleVoice() {
    const next = !voiceOn
    setVoiceOn(next)
    persist(STORAGE_VOICE, next ? '1' : '0')
    if (!next && typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel()
    }
  }

  function toggleAlarms() {
    const next = !alarmMuted
    setAlarmMuted(next)
    persist(STORAGE_MUTE_ALARMS, next ? '1' : '0')
  }

  function pickMood(m: Mood) {
    setMood(m); persist(STORAGE_MOOD, m); setMoodOpen(false)
  }

  function killAria() {
    if (!confirm('Hide ARIA for this session and future visits? You can re-enable in Settings.')) return
    setKilled(true); setOpen(false)
    persist(STORAGE_KILLED, '1'); persist(STORAGE_OPEN, '0')
  }

  function speak(text: string) {
    if (!voiceOn || typeof window === 'undefined' || !window.speechSynthesis) return
    const clean = text.replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim()
    if (!clean) return
    const u = new SpeechSynthesisUtterance(clean.slice(0, 1000))
    u.rate = 1.05; u.pitch = 1.05; u.volume = 0.9
    const voices = window.speechSynthesis.getVoices()
    const prefer = voices.find(v => /en-(GB|ZA|BW)/i.test(v.lang)) || voices.find(v => v.lang.startsWith('en'))
    if (prefer) u.voice = prefer
    window.speechSynthesis.speak(u)
  }

  // ── Phase 2 — drag-drop ingest ───────────────────────────────────
  async function onFiles(files: FileList) {
    if (!files || files.length === 0) return
    const route = findUploadRoute(pathname)
    if (!open) {
      setOpen(true); persist(STORAGE_OPEN, '1')
    }
    if (!route) {
      setHistory(h => [...h, {
        role: 'assistant',
        content:
`Got ${files.length} file${files.length === 1 ? '' : 's'} but I don't know where to send it from this page.

Drop xlsx on one of these pages and I'll route it:
  • /reports/trial-balance  → smart upload (TB)
  • /journal-entries        → smart upload (GL)
  • /accounts               → smart upload (CoA)
  • /hris/payroll           → payroll amendments
  • /assets                 → asset import
  • /reinsurance/treaties   → treaty upload
  • /contacts               → smart upload (contacts)`
      }])
      return
    }
    if (route.needs) {
      setHistory(h => [...h, {
        role: 'assistant',
        content: `Drop received. This pipeline needs extra fields (${route.needs}). Use the page form instead — I'll learn this flow in a later phase.`,
      }])
      return
    }
    setBusy(true)
    for (const file of Array.from(files)) {
      const fd = new FormData()
      fd.append(route.field, file)
      try {
        const r = await ariaFetch(route.url, { method: 'POST', body: fd })
        const text = await r.text().catch(() => '')
        if (r.ok) {
          setHistory(h => [...h, {
            role: 'assistant',
            content: `✓ Uploaded ${file.name} (${(file.size / 1024).toFixed(0)} KB) to ${route.url}\n${text.slice(0, 400)}`,
          }])
        } else {
          setHistory(h => [...h, {
            role: 'assistant',
            content: `✗ ${file.name}: HTTP ${r.status} ${text.slice(0, 300)}`,
          }])
        }
      } catch (err) {
        setHistory(h => [...h, {
          role: 'assistant',
          content: `✗ ${file.name}: ${err instanceof Error ? err.message : 'network error'}`,
        }])
      }
    }
    setBusy(false)
  }

  // Global drag listeners — sprite glows when files cross the window.
  useEffect(() => {
    if (!mounted || killed) return
    function onWindowDragOver(e: DragEvent) {
      if (e.dataTransfer?.types?.includes('Files')) {
        e.preventDefault(); setDragOver(true)
      }
    }
    function onWindowDragLeave(e: DragEvent) {
      if (e.relatedTarget == null) setDragOver(false)
    }
    function onWindowDrop(e: DragEvent) {
      // Only swallow drops on the sprite/panel itself. Drops elsewhere
      // pass through to native handlers.
      const t = e.target as HTMLElement
      const onAria = t.closest?.('[data-aria-dropzone="1"]')
      if (onAria) {
        e.preventDefault()
        if (e.dataTransfer?.files) onFiles(e.dataTransfer.files)
      }
      setDragOver(false)
    }
    window.addEventListener('dragover',  onWindowDragOver)
    window.addEventListener('dragleave', onWindowDragLeave)
    window.addEventListener('drop',      onWindowDrop)
    return () => {
      window.removeEventListener('dragover',  onWindowDragOver)
      window.removeEventListener('dragleave', onWindowDragLeave)
      window.removeEventListener('drop',      onWindowDrop)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted, killed, pathname])

  async function onSend(textOverride?: string) {
    const text = (textOverride ?? input).trim()
    if (!text || busy) return
    const next: Msg[] = [...history, { role: 'user', content: text }]
    setHistory(next); setInput(''); setBusy(true)
    try {
      const ctx: Record<string, string> = { page: pathname }
      const company = typeof window !== 'undefined' ? localStorage.getItem('alpha_company_id') : null
      if (company) ctx.selected_company_id = company
      const r = await ariaFetch('/api/v1/ai/aria/chat/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Aria-Mood': mood,           // Phase 3 — backend swaps system prompt
        },
        body: JSON.stringify({
          message: text,
          context: ctx,
          history: next.slice(-12).map(m => ({ role: m.role, content: m.content })),
        }),
      })
      const data = await r.json().catch(() => ({}))
      const reply = data?.reply || data?.reason || 'Hmm, I drew a blank. Try again?'
      const pendingActions: PendingAction[] = Array.isArray(data?.pending_actions) ? data.pending_actions : []
      const assistantReplies: Msg[] = [
        { role: 'assistant', content: reply },
        ...pendingActions.map((item): Msg => ({
          role: 'assistant',
          content: '',
          action: { ...item, state: 'open' },
        })),
      ]
      setHistory(h => [...h, ...assistantReplies])
      speak(reply)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Network blip — try again.'
      setHistory(h => [...h, { role: 'assistant', content: msg }])
    } finally {
      setBusy(false)
    }
  }

  async function confirmAction(idx: number) {
    const current = history[idx]?.action
    // One tap = one request: a fast double-click must not send twice.
    if (!current || current.state !== 'open' || confirmingRef.current.has(idx)) return
    confirmingRef.current.add(idx)
    setHistory(h => h.map((m, i) => i === idx ? { ...m, action: { ...m.action!, state: 'busy' as const } } : m))
    try {
      const r = await ariaFetch('/api/v1/ai/aria/confirm-action/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: current.action,
          ref: current.ref,
          confirm_token: current.confirm_token,
          reason: current.reason ?? null,
          notes: current.notes ?? null,
        }),
      })
      const data = await r.json().catch(() => ({})) as {
        ok?: boolean
        new_status?: string
        error?: string
        detail?: string
      }
      if (r.ok && data.ok) {
        const patch = { state: 'done' as const, result: `${data.new_status}` }
        setHistory(h => h.map((m, i) => i === idx ? { ...m, action: { ...m.action!, ...patch } } : m))
      } else {
        const patch = { state: 'error' as const, result: data.error || data.detail || 'Could not complete — nothing was changed.' }
        setHistory(h => h.map((m, i) => i === idx ? { ...m, action: { ...m.action!, ...patch } } : m))
      }
    } catch {
      const patch = { state: 'error' as const, result: 'Network blip — nothing was changed.' }
      setHistory(h => h.map((m, i) => i === idx ? { ...m, action: { ...m.action!, ...patch } } : m))
    } finally {
      confirmingRef.current.delete(idx)
    }
  }

  function cancelAction(idx: number) {
    setHistory(h => h.map((m, i) => i === idx ? { ...m, action: { ...m.action!, state: 'cancelled' as const } } : m))
  }

  if (!mounted || killed) return null

  // Eye direction — limited offset so pupils stay inside their socket.
  const eyeOffset = (cx: number, cy: number) => {
    if (!spriteRef.current) return { dx: 0, dy: 0 }
    const r = spriteRef.current.getBoundingClientRect()
    const sx = r.left + r.width / 2
    const sy = r.top + r.height / 2
    const dx = Math.max(-3, Math.min(3, (cursor.x - sx) / 40))
    const dy = Math.max(-3, Math.min(3, (cursor.y - sy) / 40))
    return { dx, dy }
  }
  const { dx, dy } = eyeOffset(cursor.x, cursor.y)

  const moodLabel: Record<Mood, string> = {
    formal:  'Formal',
    sharp:   'Sharp-sharp',
    naughty: 'Naughty',
  }
  const moodIcon: Record<Mood, React.ReactNode> = {
    formal:  <Smile size={14} />,
    sharp:   <Zap size={14} />,
    naughty: <Sparkles size={14} />,
  }

  // CFO 2026-06-29: Aria the assistant is FUN-MODE ONLY. Hidden entirely in
  // Classic / Heavenly (no rat, no glowing face). Every hook above still runs;
  // we gate only the render, so hook order is preserved.
  if (themeKey !== 'fun') return null

  return (
    <>
      {/* Toast — speech bubble ABOVE the rat's head (CFO directive
          2026-05-27: stop covering the dashboard cards). Anchored bottom-
          right, sits clear above the ~252px sprite, with a tail pointing
          down at the rat. */}
      {toast && (
        <div
          style={{
            position: 'fixed',
            // Anchored relative to wherever the user has dragged Aria.
            right: pos.right + 6,
            bottom: pos.bottom + 196,
            zIndex: 9999,
            width: 150,
            minHeight: 140,
            maxWidth: 'calc(100vw - 48px)',
            background: '#0D1B2A',
            color: '#FFFFFF',
            padding: '14px 16px',
            borderRadius: 16,
            border: `2px solid ${toast.major ? '#DC2626' : '#F07F00'}`,
            boxShadow: toast.major
              ? '0 12px 34px rgba(220,38,38,0.45)'
              : '0 12px 34px rgba(13,27,42,0.40)',
            fontSize: 14,
            fontFamily: 'system-ui, sans-serif',
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
          }}
        >
          {/* Downward tail pointing at the rat's head */}
          <div style={{
            position: 'absolute',
            bottom: -9,
            right: 50,
            width: 16, height: 16,
            background: '#0D1B2A',
            borderRight: `2px solid ${toast.major ? '#DC2626' : '#F07F00'}`,
            borderBottom: `2px solid ${toast.major ? '#DC2626' : '#F07F00'}`,
            transform: 'rotate(45deg)',
          }} />
          <Bell size={14}
                style={{ color: toast.major ? '#DC2626' : '#F07F00', flexShrink: 0, marginTop: 2 }} />
          <div style={{ flex: 1, lineHeight: 1.4 }}>
            <div style={{
              fontSize: 10, opacity: 0.7, letterSpacing: 1, textTransform: 'uppercase',
              marginBottom: 2,
            }}>
              {toast.kind === 'news'
                ? (toast.major ? '⚠ Important news' : 'News')
                : toast.kind === 'chatter'
                  ? 'Aria'
                  : 'Deadline'}
            </div>
            {toast.link ? (
              <a href={toast.link} target="_blank" rel="noopener noreferrer"
                 style={{ color: '#FFFFFF', textDecoration: 'underline' }}>
                {toast.message}
              </a>
            ) : (
              <span>{toast.message}</span>
            )}
          </div>
          <button onClick={() => setToast(null)}
                  style={{ background: 'transparent', border: 'none', color: '#FFFFFF', cursor: 'pointer' }}>
            <X size={12} />
          </button>
        </div>
      )}

      {/* Sprite — cartoon rat (Aria).
          RECON-001 (Oprah re-test 2026-05-26): this 252x252 sprite sat at
          z-9998 and ate clicks on the Bank-Reconciliation Match button on
          bottom-of-table rows at narrow viewports. A z-index bump on the
          table cell could never win against 9998.
          Fix: the big sprite box is now `pointer-events: none` (decorative —
          clicks pass straight through to the table beneath), and the
          click-to-open / drop-zone behaviour is confined to a smaller
          centred hotspot. Net: the mascot stays big and visible, but only
          a ~108px core intercepts clicks, so the Action column at the right
          edge is reachable again. */}
      {!open && (
        // Docked copilot orb (redesign pack 05): instead of a loose floating
        // face, Aria lives in a frosted glass orb with a thin orange ring and a
        // small "Aria" label, so she reads as a designed part of the premium
        // shell. ALL existing behaviour is preserved — the orb itself is the
        // click / drag / drop hotspot (spriteRef + the same handlers); kill
        // switch, voice, upload, proactive toasts and drag-anchor are unchanged.
        <div
          style={{
            position: 'fixed',
            // CFO 2026-06-10: draggable — user-chosen anchor, default sits
            // above the Team Chat launcher (z9997) so both stay reachable.
            right: pos.right,
            bottom: pos.bottom,
            width: 76,
            // orb + label below it
            height: 96,
            pointerEvents: 'none',      // wrapper stays click-through
            zIndex: 9998,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 6,
            transform: `scale(${pulse ? 1.12 : 1})`,
            transition: dragging ? 'none' : 'transform 0.2s ease-out',
          }}
          aria-label="Aria"
        >
          {/* Non-clipping positioning context: the orb clips the face to the
              circle (overflow:hidden), but the urgent badge must bleed OUTSIDE
              the orb — so it lives here, as a sibling, not inside the orb. */}
          <div style={{ position: 'relative', width: 64, height: 64 }}>
            {/* The orb — the only interactive surface (compact, so it no longer
                overhangs table action columns: RECON-001 stays fixed). */}
            <div
              ref={spriteRef}
              data-aria-dropzone="1"
              onClick={onSpriteClick}
              onPointerDown={onDragStart}
              onPointerMove={onDragMove}
              onPointerUp={onDragEnd}
              onPointerCancel={onDragEnd}
              style={{
                position: 'absolute',
                inset: 0,
                borderRadius: '50%',
                background: 'radial-gradient(circle at 50% 38%, rgba(255,255,255,0.10), rgba(13,27,42,0.55) 78%)',
                border: `1px solid ${dragOver ? '#F07F00' : 'rgba(240,127,0,0.55)'}`,
                WebkitBackdropFilter: 'blur(10px) saturate(120%)',
                backdropFilter: 'blur(10px) saturate(120%)',
                boxShadow: dragOver
                  ? '0 0 0 3px rgba(240,127,0,0.45), 0 10px 26px -8px rgba(240,127,0,0.6)'
                  : pulse
                    ? '0 0 0 2px rgba(220,38,38,0.55), 0 10px 26px -10px rgba(220,38,38,0.6)'
                    : '0 0 0 1px rgba(240,127,0,0.25), 0 10px 26px -12px rgba(13,27,42,0.55)',
                cursor: dragging ? 'grabbing' : 'pointer',
                pointerEvents: 'auto',
                touchAction: 'none',
                overflow: 'hidden',
              }}
              title={dragOver ? 'Drop file here' : 'Ask Aria — click to chat, drag to move'}
              aria-label="Open Aria"
            >
              {/* Face fills the orb. Decorative (its own pointer-events:none) so
                  the orb div above owns the click/drag. */}
              <div style={{ position: 'absolute', inset: 4 }}>
                <AriaRatGLB blink={blink} dx={dx} dy={dy} excited={pulse || busy} />
              </div>
            </div>
            {/* Urgent badge — sibling of the orb so its -2px bleed is not
                clipped by the orb's overflow:hidden. */}
            {urgent.length > 0 && (
              <div style={{
                position: 'absolute', top: -2, right: -2,
                background: '#DC2626', color: '#FFFFFF',
                borderRadius: 999, minWidth: 20, height: 20,
                fontSize: 11, fontWeight: 700,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: '0 5px', border: '2px solid #FFFFFF',
                boxShadow: '0 2px 6px rgba(220,38,38,0.5)',
                zIndex: 2,
              }}
                   title={`${urgent.length} deadline${urgent.length === 1 ? '' : 's'} due within 7 days`}>
                {urgent.length}
              </div>
            )}
          </div>

          {/* "Aria" label pill — small, elegant, on-brand. */}
          <div style={{
            pointerEvents: 'none',
            background: 'rgba(13,27,42,0.72)',
            color: '#FFFFFF',
            border: '1px solid rgba(240,127,0,0.45)',
            borderRadius: 999,
            padding: '1px 9px',
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.08em',
            fontFamily: 'system-ui, sans-serif',
            WebkitBackdropFilter: 'blur(6px)',
            backdropFilter: 'blur(6px)',
          }}>
            Aria
          </div>
        </div>
      )}

      {/* Chat panel */}
      {open && (
        <div
          data-aria-dropzone="1"
          style={{
            // Panel follows Aria's drag anchor, clamped so the 380px panel
            // never opens off-screen even when she lives at a screen edge.
            position: 'fixed',
            right: typeof window === 'undefined' ? pos.right
              : Math.max(0, Math.min(pos.right + 4, window.innerWidth - 396)),
            bottom: typeof window === 'undefined' ? pos.bottom
              : Math.max(8, Math.min(pos.bottom - 4, window.innerHeight - 200)),
            width: 380,
            maxHeight: 'min(600px, 72vh)',
            background: '#FFFFFF', color: '#0D1B2A', borderRadius: 16,
            boxShadow: dragOver
              ? '0 0 0 4px rgba(240,127,0,0.5), 0 18px 50px -8px rgba(13,27,42,0.4)'
              : '0 18px 50px -8px rgba(13,27,42,0.4)',
            border: '1px solid rgba(13,27,42,0.08)',
            display: 'flex', flexDirection: 'column',
            zIndex: 9998, overflow: 'visible',
            fontFamily: 'system-ui, sans-serif',
            transition: 'box-shadow 0.15s ease-out',
          }}
        >
          {/* Header — also a drag handle (CFO 2026-06-10): grab anywhere
              that isn't a button to move the open panel around. */}
          <div
            onPointerDown={onDragStart}
            onPointerMove={onDragMove}
            onPointerUp={onDragEnd}
            onPointerCancel={onDragEnd}
            style={{
              background: 'linear-gradient(135deg, #F4A623, #F07F00)',
              color: '#0D1B2A', padding: '10px 14px',
              display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700,
              borderTopLeftRadius: 16, borderTopRightRadius: 16,
              cursor: dragging ? 'grabbing' : 'grab',
              touchAction: 'none',
            }}>
            <span style={{ width: 42, height: 42, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              <AriaRatGLB blink={blink} dx={dx} dy={dy} excited={busy} compact />
            </span>
            <span style={{ flex: 1 }}>Aria</span>

            {/* Mood selector — Phase 3 */}
            <div style={{ position: 'relative' }}>
              <button onClick={() => setMoodOpen(o => !o)}
                      title={`Mood: ${moodLabel[mood]}`}
                      style={{
                        background: 'rgba(13,27,42,0.12)',
                        border: 'none', cursor: 'pointer',
                        color: '#0D1B2A', borderRadius: 6,
                        padding: '4px 6px',
                        display: 'flex', alignItems: 'center', gap: 4,
                        fontSize: 11, fontWeight: 700,
                      }}>
                {moodIcon[mood]} <span>{moodLabel[mood]}</span>
              </button>
              {moodOpen && (
                <div style={{
                  position: 'absolute', top: '110%', right: 0,
                  background: '#FFFFFF', color: '#0D1B2A',
                  borderRadius: 8, boxShadow: '0 10px 30px rgba(13,27,42,0.3)',
                  border: '1px solid #E5E7EB',
                  minWidth: 160, padding: 4, zIndex: 9999,
                }}>
                  {(['formal', 'sharp', 'naughty'] as Mood[]).map(m => (
                    <button key={m} onClick={() => pickMood(m)}
                            style={{
                              width: '100%', textAlign: 'left',
                              padding: '6px 8px', borderRadius: 6,
                              border: 'none',
                              background: m === mood ? '#FFF7ED' : 'transparent',
                              color: '#0D1B2A',
                              cursor: 'pointer',
                              display: 'flex', alignItems: 'center', gap: 6,
                              fontSize: 12.5,
                            }}>
                      {moodIcon[m]}<span>{moodLabel[m]}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <button onClick={toggleAlarms}
                    title={alarmMuted ? 'Alarms muted — click to enable' : 'Alarms on — click to mute'}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#0D1B2A' }}>
              {alarmMuted ? <BellOff size={16} /> : <Bell size={16} />}
            </button>
            <button onClick={toggleVoice}
                    title={voiceOn ? 'Voice on' : 'Voice off'}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#0D1B2A' }}>
              {voiceOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
            </button>
            <button onClick={killAria}
                    title="Hide ARIA"
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#0D1B2A' }}>
              <Power size={16} />
            </button>
            <button onClick={toggleOpen}
                    title="Close chat"
                    aria-label="Close chat"
                    style={{ background: 'rgba(13,27,42,0.12)', border: 'none', cursor: 'pointer',
                             color: '#0D1B2A', borderRadius: 6, padding: '4px 6px',
                             display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 700 }}>
              <X size={16} /> Close
            </button>
          </div>

          {/* Messages */}
          <div style={{
            flex: 1, overflowY: 'auto', padding: 12,
            background: dragOver ? '#FFF7ED' : '#F8F9FA',
            display: 'flex', flexDirection: 'column', gap: 8,
            fontSize: 13.5, lineHeight: 1.45,
            transition: 'background 0.15s ease-out',
          }}>
            {dragOver && (
              <div style={{
                background: '#FFFFFF',
                border: '2px dashed #F07F00',
                color: '#7C2D12',
                padding: 18,
                borderRadius: 12,
                display: 'flex', alignItems: 'center', gap: 10,
                fontWeight: 600,
              }}>
                <Upload size={20} /> Drop to upload — I'll route by page.
              </div>
            )}
            {history.length === 0 && !dragOver && (
              <>
                <div style={{
                  background: '#FFF7ED', border: '1px solid #FFD7B5',
                  color: '#7C2D12', padding: 12, borderRadius: 12,
                  lineHeight: 1.5,
                }}>
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>
                    Dumela{firstName ? `, ${firstName}` : ''} — welcome to Aria.
                  </div>
                  How may I assist? Drop a file, type a question, or pick one below.
                  I can read xlsx · csv · pdf · docx, post nothing without permission,
                  and I cite my sources.
                  {voiceOn ? <span style={{ marginLeft: 6, opacity: 0.7 }}>Voice is on.</span> : null}
                </div>
                {/* Conversation starters */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {ARIA_STARTERS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => onSend(s)}
                      disabled={busy}
                      style={{
                        background: '#FFFFFF',
                        border: '1px solid #E5E7EB',
                        borderRadius: 999,
                        padding: '6px 12px',
                        fontSize: 12.5,
                        color: '#0D1B2A',
                        cursor: busy ? 'not-allowed' : 'pointer',
                        opacity: busy ? 0.5 : 1,
                        transition: 'background 0.15s, border-color 0.15s',
                      }}
                      onMouseEnter={(e) => {
                        ;(e.currentTarget as HTMLButtonElement).style.background = '#FFF7ED'
                        ;(e.currentTarget as HTMLButtonElement).style.borderColor = '#F4A623'
                      }}
                      onMouseLeave={(e) => {
                        ;(e.currentTarget as HTMLButtonElement).style.background = '#FFFFFF'
                        ;(e.currentTarget as HTMLButtonElement).style.borderColor = '#E5E7EB'
                      }}
                    >{s}</button>
                  ))}
                </div>
                {urgent.length > 0 && (
                  <div style={{
                    background: '#FEF2F2', border: '1px solid #FECACA',
                    color: '#7F1D1D', padding: 10, borderRadius: 10, fontSize: 12.5,
                  }}>
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>
                      ⚠ {urgent.length} deadline{urgent.length === 1 ? '' : 's'} within 7 days
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {urgent.slice(0, 5).map((d, i) => (
                        <li key={i}>
                          <strong>{d.name}</strong> — {d.due_date}
                          {' '}<span style={{ opacity: 0.7 }}>({d.days_left}d, {d.authority})</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
            {history.map((m, i) => {
              if (m.action) {
                const a = m.action
                const isOpen = a.state === 'open'
                const isDone = a.state === 'done'
                const isCancelled = a.state === 'cancelled'
                const isError = a.state === 'error'
                return (
                  <section
                    key={i}
                    role="group"
                    aria-label={`${a.action} ${a.ref}`}
                    style={{
                      alignSelf: 'flex-start',
                      maxWidth: '85%',
                      background: '#FFFFFF',
                      color: '#0D1B2A',
                      padding: '8px 12px',
                      borderRadius: 12,
                      border: '1px solid #E5E7EB',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    <div style={{ fontWeight: 700, marginBottom: 6 }}>
                      {a.action === 'approve' ? 'Approve payment?' : 'Reject payment?'}
                    </div>
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'max-content 1fr',
                      gap: '4px 8px',
                      fontSize: 12.5,
                    }}>
                      <span style={{ opacity: 0.6 }}>Reference</span>
                      <span>{a.ref}</span>
                      <span style={{ opacity: 0.6 }}>Payee</span>
                      <span>{a.payee}</span>
                      <span style={{ opacity: 0.6 }}>Amount</span>
                      <span>{a.currency || 'BWP'} {a.amount}</span>
                      <span style={{ opacity: 0.6 }}>Current stage</span>
                      <span>{a.status}</span>
                      {a.action === 'reject' && a.reason ? (
                        <>
                          <span style={{ opacity: 0.6 }}>Reason</span>
                          <span>{a.reason}</span>
                        </>
                      ) : null}
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                      <button
                        type="button"
                        onClick={() => { void confirmAction(i) }}
                        disabled={!isOpen}
                        style={{
                          background: '#F07F00',
                          color: '#FFFFFF',
                          border: 'none',
                          borderRadius: 8,
                          padding: '5px 10px',
                          cursor: isOpen ? 'pointer' : 'not-allowed',
                          opacity: isOpen ? 1 : 0.5,
                          fontWeight: 700,
                        }}
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => cancelAction(i)}
                        disabled={!isOpen}
                        style={{
                          background: '#FFFFFF',
                          color: '#0D1B2A',
                          border: '1px solid #D1D5DB',
                          borderRadius: 8,
                          padding: '5px 10px',
                          cursor: isOpen ? 'pointer' : 'not-allowed',
                          opacity: isOpen ? 1 : 0.5,
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                    <div aria-live="polite">
                      {isDone ? <div style={{ marginTop: 6, color: '#166534' }}>✓ Done — now {a.result}</div> : null}
                      {isCancelled ? <div style={{ marginTop: 6, color: '#6B7280' }}>Cancelled — nothing changed.</div> : null}
                      {isError ? <div style={{ marginTop: 6, color: '#991B1B' }}>✗ {a.result}</div> : null}
                    </div>
                  </section>
                )
              }
              return (
                <div key={i} style={{
                  alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                  maxWidth: '85%',
                  background: m.role === 'user' ? '#0D1B2A' : '#FFFFFF',
                  color: m.role === 'user' ? '#FFFFFF' : '#0D1B2A',
                  padding: '8px 12px',
                  borderRadius: 12,
                  border: m.role === 'assistant' ? '1px solid #E5E7EB' : 'none',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}>
                  {m.content}
                </div>
              )
            })}
            {busy && (
              <div style={{
                alignSelf: 'flex-start',
                background: '#FFFFFF', border: '1px solid #E5E7EB',
                padding: '8px 12px', borderRadius: 12,
                color: '#6B7280',
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <Loader2 size={14} className="animate-spin" /> thinking…
              </div>
            )}
            <div ref={panelEndRef} />
          </div>

          {/* Input */}
          <div style={{
            padding: 8, borderTop: '1px solid #E5E7EB',
            display: 'flex', gap: 6, background: '#FFFFFF',
            borderBottomLeftRadius: 16, borderBottomRightRadius: 16,
          }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
              placeholder="Ask ARIA…"
              disabled={busy}
              style={{
                flex: 1, padding: '8px 10px',
                border: '1px solid #D1D5DB', borderRadius: 8,
                outline: 'none', fontSize: 14,
              }}
            />
            <button
              onClick={() => { void onSend() }}
              disabled={busy || !input.trim()}
              style={{
                background: '#F07F00', color: '#FFFFFF',
                border: 'none', borderRadius: 8, padding: '8px 10px',
                cursor: busy || !input.trim() ? 'not-allowed' : 'pointer',
                opacity: busy || !input.trim() ? 0.5 : 1,
              }}
              aria-label="Send"
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      )}
    </>
  )
}

/** Re-enable helper for the eventual /settings/aria page. */
export function enableAria() {
  try { localStorage.removeItem('aria_killed') } catch { /* ignore */ }
}

/**
 * Cartoon-rat sprite for Aria.
 *
 *   blink   — whether eyes are closed this frame (driven by parent timer)
 *   dx, dy  — pupil offset toward the cursor (already clamped by parent)
 *   excited — bigger ear-wiggle + tail-swish + paw-wave when true
 *             (we use this for: pending toast pulse, "thinking" busy state)
 *   compact — render in 28×28 for the chat-panel header (no shadow)
 *
 * Animations are SMIL `<animate>` / `<animateTransform>` so we don't
 * need an extra CSS file or a re-render loop on every frame. Every
 * loop is `indefinite` so the rat is alive even when the parent
 * component is otherwise idle.
 */
function AriaRatSprite({
  blink, dx, dy, excited, compact,
}: {
  blink: boolean
  dx: number
  dy: number
  excited?: boolean
  compact?: boolean
}) {
  const size = compact ? 28 : 84
  // Excitement speed-ups (smaller = faster)
  const tailDur     = excited ? '0.55s' : '1.6s'
  const earDur      = excited ? '0.55s' : '2.2s'
  const breatheDur  = excited ? '0.9s'  : '2.4s'
  const whiskerDur  = excited ? '0.45s' : '1.4s'
  const pawDur      = excited ? '0.55s' : '3s'

  return (
    <svg viewBox="0 0 120 120" width={size} height={size}
         style={{ overflow: 'visible' }}>
      <defs>
        <radialGradient id="aria-body" cx="50%" cy="40%" r="65%">
          <stop offset="0%"  stopColor="#E5E7EB" />
          <stop offset="60%" stopColor="#C9CED6" />
          <stop offset="100%" stopColor="#9FA6B2" />
        </radialGradient>
        <radialGradient id="aria-tummy" cx="50%" cy="50%" r="55%">
          <stop offset="0%"  stopColor="#F4F6FA" />
          <stop offset="100%" stopColor="#D9DEE6" />
        </radialGradient>
        <radialGradient id="aria-ear" cx="50%" cy="40%" r="65%">
          <stop offset="0%"  stopColor="#FFB7BD" />
          <stop offset="100%" stopColor="#F38994" />
        </radialGradient>
      </defs>

      {/* Tail — wags side to side. Anchored at body base. */}
      <g transform="translate(80,86)">
        <g style={{ transformOrigin: '0 0' }}>
          <animateTransform attributeName="transform" type="rotate"
                            values="-10;14;-6;10;-10" keyTimes="0;0.25;0.5;0.75;1"
                            dur={tailDur} repeatCount="indefinite" />
          <path d="M 0,0 C 18,-2 30,8 32,22 C 33,32 26,36 22,30"
                stroke="#F38994" strokeWidth="6"
                strokeLinecap="round" fill="none" />
          <path d="M 0,0 C 18,-2 30,8 32,22 C 33,32 26,36 22,30"
                stroke="#FFB7BD" strokeWidth="2.4"
                strokeLinecap="round" fill="none" />
        </g>
      </g>

      {/* Body — breathing scale on Y */}
      <g style={{ transformOrigin: '60px 86px' }}>
        <animateTransform attributeName="transform" type="scale"
                          values="1 1; 1.025 0.985; 1 1"
                          dur={breatheDur} repeatCount="indefinite" />
        {/* Lower body / haunches */}
        <ellipse cx="60" cy="84" rx="28" ry="22" fill="url(#aria-body)"
                 stroke="#5C6470" strokeWidth="1.4" />
        {/* Tummy */}
        <ellipse cx="60" cy="90" rx="16" ry="11" fill="url(#aria-tummy)" />
        {/* Back feet */}
        <ellipse cx="44" cy="104" rx="7" ry="4" fill="#FFB7BD"
                 stroke="#5C6470" strokeWidth="1" />
        <ellipse cx="76" cy="104" rx="7" ry="4" fill="#FFB7BD"
                 stroke="#5C6470" strokeWidth="1" />

        {/* Right paw (held up) — gentle wave */}
        <g transform="translate(70,82)">
          <g style={{ transformOrigin: '0 0' }}>
            <animateTransform attributeName="transform" type="rotate"
                              values="-8;12;-8" dur={pawDur} repeatCount="indefinite" />
            <ellipse cx="0" cy="0" rx="6" ry="4.5" fill="url(#aria-body)"
                     stroke="#5C6470" strokeWidth="1.2" />
            <circle cx="-3" cy="-3" r="1.1" fill="#5C6470" />
            <circle cx="0"  cy="-4" r="1.1" fill="#5C6470" />
            <circle cx="3"  cy="-3" r="1.1" fill="#5C6470" />
          </g>
        </g>
        {/* Left paw */}
        <g transform="translate(50,82)">
          <g style={{ transformOrigin: '0 0' }}>
            <animateTransform attributeName="transform" type="rotate"
                              values="6;-10;6" dur={pawDur} repeatCount="indefinite" />
            <ellipse cx="0" cy="0" rx="6" ry="4.5" fill="url(#aria-body)"
                     stroke="#5C6470" strokeWidth="1.2" />
            <circle cx="-3" cy="-3" r="1.1" fill="#5C6470" />
            <circle cx="0"  cy="-4" r="1.1" fill="#5C6470" />
            <circle cx="3"  cy="-3" r="1.1" fill="#5C6470" />
          </g>
        </g>

        {/* Head */}
        <g>
          {/* Left ear — wiggles */}
          <g transform="translate(40,32)">
            <g style={{ transformOrigin: '0 6px' }}>
              <animateTransform attributeName="transform" type="rotate"
                                values="-6;8;-4;6;-6" keyTimes="0;0.25;0.5;0.75;1"
                                dur={earDur} repeatCount="indefinite" />
              <ellipse cx="0" cy="0" rx="13" ry="14" fill="url(#aria-body)"
                       stroke="#5C6470" strokeWidth="1.4" />
              <ellipse cx="0" cy="2" rx="8" ry="9" fill="url(#aria-ear)" />
            </g>
          </g>
          {/* Right ear — wiggles opposite */}
          <g transform="translate(80,32)">
            <g style={{ transformOrigin: '0 6px' }}>
              <animateTransform attributeName="transform" type="rotate"
                                values="6;-8;4;-6;6" keyTimes="0;0.25;0.5;0.75;1"
                                dur={earDur} repeatCount="indefinite" />
              <ellipse cx="0" cy="0" rx="13" ry="14" fill="url(#aria-body)"
                       stroke="#5C6470" strokeWidth="1.4" />
              <ellipse cx="0" cy="2" rx="8" ry="9" fill="url(#aria-ear)" />
            </g>
          </g>

          {/* Head shape */}
          <ellipse cx="60" cy="55" rx="28" ry="26" fill="url(#aria-body)"
                   stroke="#5C6470" strokeWidth="1.5" />

          {/* Whiskers — twitch */}
          <g stroke="#5C6470" strokeWidth="0.9" strokeLinecap="round" opacity="0.85">
            <g style={{ transformOrigin: '60px 66px' }}>
              <animateTransform attributeName="transform" type="rotate"
                                values="-3;3;-3" dur={whiskerDur} repeatCount="indefinite" />
              <line x1="48" y1="65" x2="32" y2="60" />
              <line x1="48" y1="68" x2="30" y2="68" />
              <line x1="48" y1="71" x2="32" y2="76" />
              <line x1="72" y1="65" x2="88" y2="60" />
              <line x1="72" y1="68" x2="90" y2="68" />
              <line x1="72" y1="71" x2="88" y2="76" />
            </g>
          </g>

          {/* Eyes — whites */}
          <ellipse cx="50" cy="52" rx="6.5" ry="7" fill="#FFFFFF"
                   stroke="#0D1B2A" strokeWidth="1.2" />
          <ellipse cx="70" cy="52" rx="6.5" ry="7" fill="#FFFFFF"
                   stroke="#0D1B2A" strokeWidth="1.2" />
          {/* Pupils (eye-tracking) — hidden when blink */}
          {!blink && (
            <>
              <ellipse cx={50 + dx} cy={53 + dy} rx="3.2" ry="3.8" fill="#2A1B0B" />
              <ellipse cx={70 + dx} cy={53 + dy} rx="3.2" ry="3.8" fill="#2A1B0B" />
              <circle  cx={50 + dx - 1} cy={51 + dy} r="1.1" fill="#FFFFFF" />
              <circle  cx={70 + dx - 1} cy={51 + dy} r="1.1" fill="#FFFFFF" />
            </>
          )}
          {blink && (
            <>
              <path d="M 44,52 Q 50,56 56,52" stroke="#0D1B2A"
                    strokeWidth="1.8" fill="none" strokeLinecap="round" />
              <path d="M 64,52 Q 70,56 76,52" stroke="#0D1B2A"
                    strokeWidth="1.8" fill="none" strokeLinecap="round" />
            </>
          )}

          {/* Nose */}
          <ellipse cx="60" cy="65" rx="4.5" ry="3.5" fill="#F38994"
                   stroke="#0D1B2A" strokeWidth="1" />
          <ellipse cx="58.5" cy="63.5" rx="1.2" ry="0.8" fill="#FFC4CA" />

          {/* Smile */}
          <path d="M 56,70 Q 60,73 64,70" stroke="#0D1B2A"
                strokeWidth="1.4" fill="none" strokeLinecap="round" />
          {/* Two front teeth peek */}
          <rect x="58.6" y="71.5" width="1.3" height="2" rx="0.3" fill="#FFFFFF" stroke="#0D1B2A" strokeWidth="0.4" />
          <rect x="60.1" y="71.5" width="1.3" height="2" rx="0.3" fill="#FFFFFF" stroke="#0D1B2A" strokeWidth="0.4" />
        </g>
      </g>
    </svg>
  )
}
