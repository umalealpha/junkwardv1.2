'use client'

/** /m — Alpha Nexus home (Stitch design): navy status hero, 3D feature cards,
 * Live Insights. Real data: tier/points (me), drive avg, wellness score. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronRight, LogOut, Sparkles, Apple, Footprints, BriefcaseBusiness, Flame, Trophy, ShieldCheck, CheckCircle2, Circle, PiggyBank, Users, UserPlus, Copy, Check } from 'lucide-react'
import { getMe, getDrive, getAlphaScore, getStaffApprovals, getGrowth, getPolicyCard, postActivity, pairStart, logout, deleteAccount, clearCustToken, compressImage, getTeam, getReferral, ApiError, type CustomerMember, type GrowthResp, type PolicyCardResp, type TeamResp, type ReferralResp } from '../api'
import { C, serif, sans, h, card, pill, headerPad, isInAppShell } from '../ui'
import { nextStepSyncAction, stepSyncIntentUrl, shouldReportNoHandoff, type PairCode } from './stepSync'

// CFO 7-Sep-2026: nothing a member can TYPE earns points any more. The old
// "Log steps" (type a number) and "Log a workout" (one tap) tiles are gone;
// steps count only when synced from the phone's real step data (Health
// Connect, via the native Alpha Nexus Android app). Meals stay — a photo is
// AI-verified server-side.
const ACTIVITIES = [
  { kind: 'healthy_eating' as const, label: 'Snap a meal', pts: 'photo · +15', icon: Apple },
]

const PLAY_URL = 'https://play.google.com/store/apps/details?id=com.alphadirect.rewardshealth'

const FEATURES = [
  { href: '/m/drive', img: '/brand/nexus/drive.png', title: 'Nexus Drive', desc: 'Optimize your daily commute with intelligent safety metrics.' },
  { href: '/m/rewards', img: '/brand/nexus/rewards.png', title: 'My Rewards', desc: 'Redeem points for premium wellness and lifestyle experiences.' },
  { href: '/m/thrive', img: '/brand/nexus/wellness.png', title: 'Wellness', desc: 'Personalized health tracking and a 30-second pulse check.' },
]

function tierName(t: string) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : 'Bronze' }

function Arc({ value, color }: { value: number | null; color: string }) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, value))
  const r = 34, circ = Math.PI * r, dash = (v / 100) * circ
  return (
    <svg viewBox="0 0 80 48" style={{ width: 80 }}>
      <path d="M 6 44 A 34 34 0 0 1 74 44" fill="none" stroke="#EEF0F3" strokeWidth="8" strokeLinecap="round" />
      <path d="M 6 44 A 34 34 0 0 1 74 44" fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
        strokeDasharray={`${dash} ${circ}`} />
    </svg>
  )
}

export default function CustomerHome() {
  const router = useRouter()
  const [me, setMe] = useState<CustomerMember | null>(null)
  const [drive, setDrive] = useState<number | null>(null)
  const [wellness, setWellness] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [logging, setLogging] = useState<string | null>(null)
  // Step-sync handshake: 'ready'/'needsApp' mean we hold a live pair code and
  // the next tap can navigate straight out (see startStepSync).
  const [stepStage, setStepStage] = useState<'idle' | 'preparing' | 'ready' | 'needsApp'>('idle')
  const [pairCode, setPairCode] = useState<PairCode | null>(null)
  const [loadErr, setLoadErr] = useState(false)
  const mealInputRef = useRef<HTMLInputElement | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const appOpenTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Every toast (success OR error) must auto-dismiss — the old error path left
  // it on screen permanently, so a transient failure looked like a stuck app.
  const showToast = useCallback((msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])


  // Staff bridge (CFO 2026-07-14): if this login belongs to an EMPLOYEE, the
  // probe resolves (server maps the customer token to their staff account) and
  // the Staff Portal card appears with the count of items awaiting them.
  // A plain customer's probe 401s and the card never shows.
  const [staffCount, setStaffCount] = useState<number | null>(null)
  // Growth cards (CFO 2026-08-11): quest / streak / challenge / savings /
  // leaderboard in one call; policy & claims card only for linked members.
  const [growth, setGrowth] = useState<GrowthResp | null>(null)
  const [policy, setPolicy] = useState<PolicyCardResp | null>(null)
  // Teams and referrals (CFO 2026-09-08, "6 is good" / "7 is good").
  const [team, setTeam] = useState<TeamResp | null>(null)
  const [referral, setReferral] = useState<ReferralResp | null>(null)
  const [codeCopied, setCodeCopied] = useState(false)

  const load = useCallback(() => {
    setLoadErr(false)
    // getMe drives the whole hero (name + points). If it fails we must NOT
    // render "0 NEXUS POINTS" as if it were real — show a retry banner instead.
    getMe().then(setMe).catch(() => setLoadErr(true))
    getDrive().then(d => setDrive(d.avgScore)).catch(() => {})
    getAlphaScore().then(s => setWellness(s.hasVitals ? s.score.score : null)).catch(() => {})
    getStaffApprovals().then(a => setStaffCount(a.total)).catch(() => setStaffCount(null))
    getGrowth().then(g => {
      setGrowth(g)
      // A quest/challenge bonus may have just paid — reflect it in the hero.
      setMe(m => (m ? { ...m, points: g.totalPoints, tier: g.tier } : m))
    }).catch(() => setGrowth(null))
    getPolicyCard().then(setPolicy).catch(() => setPolicy(null))
    // Both cards are additive — a failure here must never take the home screen
    // down with it, so each one just stays hidden.
    getTeam().then(setTeam).catch(() => setTeam(null))
    getReferral().then(setReferral).catch(() => setReferral(null))
  }, [])
  useEffect(() => { load() }, [load])

  // Step sync: the ONLY way steps earn points. Hands off to the native
  // PairingActivity inside the Alpha Nexus Android app (same package), which
  // reads today's total from Health Connect and posts it with a device-only
  // token. Points from synced steps are REWARDS ONLY - never premium.
  //
  // 🔴 Why this is a TWO-TAP flow (CFO 2026-09-08: "the sync steps is not
  // working"). Chrome silently refuses an external-protocol navigation
  // (intent://) once the tap's user activation has been consumed - and
  // awaiting the pair code from the server consumes it. Nothing threw, nothing
  // navigated, the catch never ran, so the tile looked completely dead.
  // Tap 1 fetches the code; tap 2 issues the intent INSIDE its own gesture.
  const openNexusApp = useCallback((code: string) => {
    const intent = stepSyncIntentUrl(code, PLAY_URL)
    // startStepSync refuses to reach here inside our own app shell, so this is
    // always a real browser hand-off.
    const inOwnShell = false
    // Browsers outside the Chrome family ignore intent:// without throwing, and
    // a phone without the app installed may not surface the Play fallback. Arm
    // the follow-up BEFORE navigating - if the assignment throws or is ignored,
    // a timer armed after it would never exist and the member would again be
    // left with a tile that did nothing. If this page is still in front when
    // the timer fires, the hand-off did not happen: say so and offer Play.
    if (appOpenTimer.current) clearTimeout(appOpenTimer.current)
    const armedAt = Date.now()
    // If the app takes over, the browser is suspended: cancel outright the
    // moment we go to the background, and treat a late-firing timer as proof
    // the hand-off worked (see shouldReportNoHandoff).
    const cancelOnHide = () => {
      if (inOwnShell) return          // cannot tell a real hand-off from our own app resurfacing
      if (!document.hidden) return
      if (appOpenTimer.current) {
        clearTimeout(appOpenTimer.current)
        appOpenTimer.current = null
      }
      // The app took over, so the code is spent (the server consumes it on
      // pairing). Leaving the tile on "Open Alpha Nexus - tap to finish" would
      // assert an unfinished sync that finished, and a re-tap would hand the
      // app a dead code.
      setPairCode(null)
      setStepStage('idle')
    }
    document.addEventListener('visibilitychange', cancelOnHide, { once: true })
    appOpenTimer.current = setTimeout(() => {
      document.removeEventListener('visibilitychange', cancelOnHide)
      if (!inOwnShell
          && !shouldReportNoHandoff({ hidden: document.hidden, elapsedMs: Date.now() - armedAt })) return
      setStepStage('needsApp')
      showToast(inOwnShell
        ? 'Your Alpha Nexus app cannot read steps yet — update it from Google Play, then tap again.'
        : 'The Alpha Nexus app did not open. Install or update it from Google Play, then tap again.')
    }, inOwnShell ? 3500 : 2500)
    try {
      window.location.href = intent
    } catch {
      // Some browsers throw on an unknown scheme instead of ignoring it; the
      // timer above already owns the message, so there is nothing to add here.
    }
  }, [showToast])

  const startStepSync = useCallback(async () => {
    const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent
    const isAndroid = /Android/i.test(ua)
    // iPhone app (Tauri shell, 1.1.0+): the native Apple Health plugin does the
    // pairing + reading + posting itself - the page only hands over the one-time
    // code and shows the server's result. The device token never reaches JS.
    const tauri = typeof window === 'undefined' ? undefined
      : (window as Window & { __TAURI__?: { core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> } } }).__TAURI__
    const isAppleShell = /iPhone|iPad|Macintosh/i.test(ua) && !!tauri?.core?.invoke
    const action = nextStepSyncAction({ isAndroid, isAppleShell, pairCode })
    if (action === 'apple') {
      if (stepStage === 'preparing') return
      setStepStage('preparing')
      try {
        const { code } = await pairStart()
        const r = await tauri!.core!.invoke!('plugin:nexus-health|sync', { payload: { pairCode: code } }) as
          { stepsToday: number; sessionsToday: number; pointsAdded: number; totalPoints: number }
        showToast(r.pointsAdded > 0
          ? `Synced from Apple Health: ${r.stepsToday.toLocaleString()} steps today, +${r.pointsAdded} points`
          : `Synced from Apple Health: ${r.stepsToday.toLocaleString()} steps today - no new points yet`)
        void load()
      } catch (e) {
        showToast(e instanceof ApiError ? e.message
          : typeof e === 'string' ? e
          : 'Apple Health sync did not complete - check Health permissions in Settings and try again.')
      } finally {
        setStepStage('idle')
      }
      return
    }
    if (action === 'unsupported') {
      // An Apple device inside our own shell but with no health plugin means
      // the app is older than 1.1.0 - say THAT, not "open this on your phone"
      // to someone who is already holding their phone.
      const appleDevice = /iPhone|iPad|Macintosh/i.test(ua)
      showToast(appleDevice && isInAppShell()
        ? 'Update the Alpha Nexus app to sync steps - this version cannot read Apple Health yet.'
        : 'Step and workout sync uses the Alpha Nexus phone app - open it on your phone to sync.')
      return
    }
    if (stepStage === 'preparing') return
    // 🔴 Inside our OWN installed app (CFO, 2026-09-09: "its app, when I click
    // it, it goes to Google Play store, the same issue i reported yesterday").
    // This build has no step-sync receiver, so Android answers the hand-off
    // with the Play fallback - and Play has nothing newer to offer, so the
    // member is bounced to a listing with no update button. That loop is worse
    // than a dead button. Say what is actually true and go nowhere.
    if (isInAppShell()) {
      setStepStage('needsApp')
      showToast('Step sync needs a newer Alpha Nexus. This version cannot read your steps yet — nothing to do here until the update lands.')
      return
    }
    // In a BROWSER the Play trip is correct: the member may simply not have the
    // app. Honour the label and go there inside this gesture, with no await, or
    // the navigation loses activation exactly as the intent:// hand-off did.
    if (stepStage === 'needsApp') {
      window.location.href = PLAY_URL
      return
    }
    // Tap 2+: the code is already in hand, so the intent goes out inside this
    // gesture and Chrome allows it. Codes expire server-side after 5 minutes.
    if (action === 'openApp' && pairCode) {
      openNexusApp(pairCode.code)
      return
    }
    setStepStage('preparing')
    try {
      const { code, expiresInSeconds } = await pairStart()
      setPairCode({ code, expiresAt: Date.now() + (expiresInSeconds - 15) * 1000 })
      setStepStage('ready')
      showToast('Ready - tap again to open the Alpha Nexus app and finish syncing.')
    } catch (e) {
      setStepStage('idle')
      // The real reason matters: a spent rate limit (5 codes an hour) used to
      // be reported as a connection problem, so tapping again never helped.
      showToast(e instanceof ApiError ? e.message
        : 'Could not start step sync - check your connection and try again.')
    }
  }, [showToast, load, openNexusApp, pairCode, stepStage])
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
    if (appOpenTimer.current) clearTimeout(appOpenTimer.current)
  }, [])
  // Coming back into the page re-reads the balance (the native sync may have
  // just awarded points) and clears a stale "Get the app" label.
  useEffect(() => {
    const onShow = () => {
      if (document.hidden) return
      setStepStage(s => (s === 'needsApp' ? 'idle' : s))
      void load()
    }
    document.addEventListener('visibilitychange', onShow)
    return () => document.removeEventListener('visibilitychange', onShow)
  }, [load])

  const signOut = async () => { await logout(); clearCustToken(); router.replace('/m/login') }

  // In-app account deletion — App Store Guideline 5.1.1(v). Two-step confirm,
  // then the server hard-deletes the member + all data and we clear the session.
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const confirmDelete = async () => {
    setDeleting(true)
    try {
      await deleteAccount()
      clearCustToken()
      router.replace('/m/login')
    } catch (e) {
      setDeleteOpen(false)
      showToast(e instanceof Error ? e.message : 'Could not delete your account — please try again.')
    } finally { setDeleting(false) }
  }

  // Ref-based in-flight guard: the buttons' disabled={logging===kind} relies on
  // a re-render, so two taps in the same frame both passed and double-posted
  // (two awards racing the server's one-per-day check). A ref flips instantly.
  const activityInFlight = useRef(false)
  const submitActivity = async (kind: 'healthy_eating', opts?: { imageData?: string }) => {
    if (activityInFlight.current) return
    activityInFlight.current = true
    setLogging(kind)
    try {
      const r = await postActivity(kind, opts)
      setMe(m => (m ? { ...m, points: r.totalPoints, tier: r.tier } : m))
      showToast(r.alreadyToday ? 'Already counted today — come back tomorrow to earn again.'
        : kind === 'healthy_eating' ? `Meal verified! +${r.pointsAwarded} points.`
        : `Nice! +${r.pointsAwarded} points.`)
    } catch (e) { showToast(e instanceof Error ? e.message : 'Could not log that') }
    finally { setLogging(null); activityInFlight.current = false }
  }

  const logActivity = (kind: 'healthy_eating') => {
    if (kind === 'healthy_eating') mealInputRef.current?.click()  // open camera
  }

  const onMealFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''  // allow re-selecting the same file
    if (!file) return
    setLogging('healthy_eating')
    try {
      // Shrink the shot first (same compressImage the staff screens use) — a
      // raw 3–12 MB camera photo as base64 JSON reliably timed out on real
      // Botswana mobile data; ~1280px JPEG lands in a few hundred KB.
      const small = await compressImage(file, 1280, 0.75)
      const imageData: string = await new Promise((res, rej) => {
        const r = new FileReader()
        r.onload = () => res(String(r.result)); r.onerror = rej
        r.readAsDataURL(small)
      })
      await submitActivity('healthy_eating', { imageData })
    } catch {
      setLogging(null)
      showToast('Could not read that photo.')
    }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
        <button onClick={signOut} aria-label="Sign out" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer' }}><LogOut size={20} /></button>
      </header>

      <main style={{ padding: 16 }}>
        {loadErr && (
          <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', fontSize: 13, padding: '10px 12px', borderRadius: 12, marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <span>Couldn&apos;t load your account. Check your connection.</span>
            <button onClick={load} style={{ background: '#B91C1C', color: '#fff', border: 'none', borderRadius: 999, padding: '6px 14px', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}>Retry</button>
          </div>
        )}
        {/* Status hero */}
        <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 24, padding: 24, color: '#fff', boxShadow: '0 18px 40px rgba(15,28,44,0.28)' }}>
          <span style={pill('rgba(244,166,35,0.18)', C.orange)}>★ {tierName(me?.tier || '')} Tier</span>
          <h1 style={{ ...h(30), color: '#fff', marginTop: 14 }}>Welcome back,<br />{me ? me.name.split(' ')[0] : '…'}</h1>
          <p style={{ color: 'rgba(255,255,255,0.72)', fontSize: 14, margin: '8px 0 18px' }}>Ready to climb the leaderboard? Take a drive and rack up points. 🏁</p>
          <p style={{ color: 'rgba(255,255,255,0.55)', fontSize: 11, letterSpacing: '0.12em', margin: 0 }}>NEXUS POINTS</p>
          <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 40, margin: '2px 0 16px' }}>{me ? me.points.toLocaleString() : '—'}</p>
          <Link href="/m/drive" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff', fontWeight: 700, fontSize: 15, textDecoration: 'none', padding: '13px 24px', borderRadius: 999, boxShadow: '0 10px 22px rgba(226,112,11,0.35)' }}>🚗 Take a drive — earn points</Link>
        </div>

        {/* Staff Portal — employees only (work-email login). The clear icon. */}
        {staffCount !== null && (
          <Link href="/m/staff" style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', gap: 16, textDecoration: 'none', marginTop: 18, border: `2px solid ${C.orange}` }}>
            <div style={{ width: 62, height: 62, borderRadius: 18, background: `linear-gradient(150deg, ${C.navy2}, ${C.navy})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <BriefcaseBusiness size={30} style={{ color: C.orange }} />
            </div>
            <div style={{ flex: 1 }}>
              <h2 style={{ ...h(20), marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                Staff Portal
                {staffCount > 0 && <span style={{ background: '#DC2626', color: '#fff', borderRadius: 999, fontSize: 11, fontWeight: 800, padding: '2px 8px', fontFamily: sans }}>{staffCount}</span>}
              </h2>
              <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.45 }}>Leave, approvals, receipts &amp; petty cash — Alpha Direct staff only.</p>
            </div>
            <ChevronRight size={20} style={{ color: '#C4C8CE' }} />
          </Link>
        )}

        {/* First-win quest — 3 starter steps, one-time bonus (hidden once paid) */}
        {growth && !(growth.quest.complete && growth.quest.bonusPaid && !growth.quest.bonusPaidNow) && (
          <div style={{ ...card, padding: 18, marginTop: 18, border: `2px solid ${growth.quest.complete ? C.teal : C.orange}` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <h2 style={{ ...h(20), display: 'flex', alignItems: 'center', gap: 8 }}>
                <Sparkles size={18} style={{ color: C.orange }} /> Your first win
              </h2>
              <span style={pill('#FFF7ED', C.orangeDeep)}>+{growth.quest.bonus} pts</span>
            </div>
            {growth.quest.bonusPaidNow ? (
              <p style={{ color: C.teal, fontWeight: 700, fontSize: 14, margin: 0 }}>🎉 Quest complete — +{growth.quest.bonus} bonus points are yours!</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {growth.quest.steps.map(s => (
                  <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {s.done
                      ? <CheckCircle2 size={18} style={{ color: C.teal, flexShrink: 0 }} />
                      : <Circle size={18} style={{ color: '#C4C8CE', flexShrink: 0 }} />}
                    <span style={{ fontSize: 14, color: s.done ? C.inkSoft : C.ink, fontWeight: s.done ? 400 : 600, textDecoration: s.done ? 'line-through' : 'none' }}>{s.label}</span>
                  </div>
                ))}
                <p style={{ fontSize: 12, color: C.inkSoft, margin: '4px 0 0' }}>Finish all three and {growth.quest.bonus} bonus points land instantly.</p>
              </div>
            )}
          </div>
        )}

        {/* Streak + weekly challenge */}
        {growth && (
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <div style={{ flex: 1, ...card, padding: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
              <Flame size={22} style={{ color: growth.streak.days > 0 ? C.orangeDeep : '#C4C8CE', flexShrink: 0 }} />
              <div>
                <p style={{ margin: 0, fontFamily: serif, fontWeight: 800, fontSize: 20, color: C.ink }}>{growth.streak.days}-day</p>
                <p style={{ margin: 0, fontSize: 11, color: C.inkSoft }}>{growth.streak.activeToday ? 'streak — going strong' : growth.streak.days > 0 ? 'streak — keep it alive today' : 'streak — start one today'}</p>
              </div>
            </div>
            <div style={{ flex: 1, ...card, padding: 14 }}>
              <p style={{ margin: 0, fontSize: 11, color: C.inkSoft }}>{growth.challenge.label}</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                <div style={{ flex: 1, height: 6, background: '#EEF0F3', borderRadius: 999, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${(growth.challenge.progress / growth.challenge.target) * 100}%`, background: growth.challenge.done ? C.teal : C.orange, borderRadius: 999 }} />
                </div>
                <span style={{ fontSize: 12, fontWeight: 700, color: growth.challenge.done ? C.teal : C.ink }}>{growth.challenge.progress}/{growth.challenge.target}</span>
              </div>
              <p style={{ margin: '6px 0 0', fontSize: 11, color: growth.challenge.done ? C.teal : C.inkSoft, fontWeight: growth.challenge.done ? 700 : 400 }}>
                {growth.challenge.done ? `Done — +${growth.challenge.bonus} pts banked` : `+${growth.challenge.bonus} pts when you finish`}
              </p>
            </div>
          </div>
        )}

        {/* Premium savings — the insurer hook */}
        {growth && (
          <div style={{ ...card, padding: 18, marginTop: 14, display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 46, height: 46, borderRadius: 14, background: '#EAF6F4', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <PiggyBank size={24} style={{ color: C.teal }} />
            </div>
            <div style={{ flex: 1 }}>
              {growth.savings.monthlySaving ? (
                <>
                  <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.ink }}>
                    You&apos;re saving P{growth.savings.monthlySaving.toFixed(2)} a month on your premium
                  </p>
                  <p style={{ margin: '2px 0 0', fontSize: 12, color: C.inkSoft }}>
                    {tierName(me?.tier || '')} discount {growth.savings.discountPct}% · about P{(growth.savings.yearlySaving || 0).toFixed(0)} a year
                    {growth.savings.nextTier ? ` — reach ${growth.savings.nextTier} for ${growth.savings.nextTierPct}%` : ''}
                  </p>
                </>
              ) : growth.savings.discountPct > 0 ? (
                <>
                  <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.ink }}>
                    Your {tierName(me?.tier || '')} tier earns {growth.savings.discountPct}% off your premium
                  </p>
                  <p style={{ margin: '2px 0 0', fontSize: 12, color: C.inkSoft }}>
                    {growth.savings.nextTier ? `Climb to ${growth.savings.nextTier} and the discount grows to ${growth.savings.nextTierPct}%.` : 'Top tier — maximum discount.'}
                  </p>
                </>
              ) : (
                <>
                  <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.ink }}>
                    Silver members get {growth.savings.nextTierPct ?? 5}% off their premium
                  </p>
                  <p style={{ margin: '2px 0 0', fontSize: 12, color: C.inkSoft }}>Earn points to climb — your discount grows with every tier.</p>
                </>
              )}
            </div>
          </div>
        )}

        {/* Safe-driving leaderboard — the promise the hero makes */}
        {growth && growth.leaderboard.top.length > 0 && (
          <div style={{ ...card, padding: 18, marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <h2 style={{ ...h(20), display: 'flex', alignItems: 'center', gap: 8 }}>
                <Trophy size={18} style={{ color: C.orange }} /> Leaderboard
              </h2>
              {growth.leaderboard.me.rank && (
                <span style={pill('#EAF6F4', C.teal)}>You&apos;re #{growth.leaderboard.me.rank} of {growth.leaderboard.me.total}</span>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {growth.leaderboard.top.map(r => (
                <div key={r.rank} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 6px', borderRadius: 10, background: r.isMe ? '#FFF7ED' : 'transparent' }}>
                  <span style={{ width: 22, fontWeight: 800, fontFamily: serif, color: r.rank === 1 ? C.orange : C.inkSoft, fontSize: 15 }}>{r.rank}</span>
                  <span style={{ flex: 1, fontSize: 14, fontWeight: r.isMe ? 700 : 500, color: C.ink }}>{r.name}{r.rank === 1 ? ' 🏆' : ''}</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: C.teal }}>{r.nexusScore.toLocaleString()}</span>
                </div>
              ))}
            </div>
            <p style={{ margin: '8px 0 0', fontSize: 11, color: C.inkSoft }}>Nexus Score = points + drives + wellness scans. Drive safe to climb.</p>
          </div>
        )}

        {/* My team — solo streaks only motivate the already-motivated */}
        {team && (
          <Link href="/m/team" style={{ textDecoration: 'none' }}>
            <div style={{ ...card, padding: 18, marginTop: 14, display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }}>
              <div style={{ width: 42, height: 42, borderRadius: 13, background: '#E6F4F2', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <Users size={20} style={{ color: C.teal }} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ margin: 0, fontFamily: serif, fontWeight: 700, fontSize: 17, color: C.ink }}>
                  {team.inTeam ? team.teamName : 'Start a team'}
                </p>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: C.inkSoft }}>
                  {team.inTeam
                    ? `${team.size} of ${team.maxSize} members · ${(team.totalPoints ?? 0).toLocaleString()} team points`
                    : `Drive better together — up to ${team.maxSize} family or friends`}
                </p>
              </div>
              <ChevronRight size={20} style={{ color: '#C4C8CE', flexShrink: 0 }} />
            </div>
          </Link>
        )}

        {/* Refer a friend. The reward is stated in POINTS and only lands once
            the referred policy has stuck — never at signup. No pula on screen. */}
        {referral?.code && (
          <div style={{ ...card, padding: 18, marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <UserPlus size={18} style={{ color: C.orange }} />
              <h2 style={{ ...h(20), margin: 0 }}>Refer a friend</h2>
            </div>
            <p style={{ margin: '0 0 12px', fontSize: 13, color: C.inkSoft, lineHeight: 1.5 }}>{referral.label}</p>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14, padding: '12px 14px' }}>
              <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, letterSpacing: '0.16em', color: C.ink }}>{referral.code}</span>
              <button onClick={async () => {
                // Never say "Copied" unless it copied - see m/team/page.tsx.
                try { await navigator.clipboard.writeText(referral.code) }
                catch { showToast('Could not copy — long-press the code to copy it yourself.'); return }
                setCodeCopied(true)
                setTimeout(() => setCodeCopied(false), 2000)
              }} aria-label="Copy referral code"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 14px', borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontWeight: 600, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>
                {codeCopied ? <><Check size={15} /> Copied</> : <><Copy size={15} /> Copy</>}
              </button>
            </div>
            {referral.friendsJoined > 0 && (
              <p style={{ margin: '10px 0 0', fontSize: 12, color: C.inkSoft }}>
                {referral.friendsJoined} friend{referral.friendsJoined === 1 ? '' : 's'} joined
                {referral.friendsQualified > 0 ? ` · ${referral.friendsQualified} qualified` : ' · none qualified yet'}
              </p>
            )}
          </div>
        )}

        {/* My policy & claims — every policyholder's reason to keep the app */}
        {policy?.linked && (
          <div style={{ ...card, padding: 18, marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <h2 style={{ ...h(20), display: 'flex', alignItems: 'center', gap: 8 }}>
                <ShieldCheck size={18} style={{ color: C.teal }} /> My policy
              </h2>
              <span style={pill('#EAF6F4', C.teal)}>{policy.policyNumber}</span>
            </div>
            {policy.monthlyPremium != null && (
              <p style={{ margin: '0 0 8px', fontSize: 13, color: C.inkSoft }}>Monthly premium <b style={{ color: C.ink }}>P{Number(policy.monthlyPremium).toFixed(2)}</b></p>
            )}
            {(policy.claims || []).length === 0 ? (
              <p style={{ margin: 0, fontSize: 13, color: C.inkSoft }}>No claims on record — long may it last. 🙌</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {(policy.claims || []).map(cl => (
                  <div key={cl.claimNumber} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#F6F7F9', borderRadius: 10, padding: '8px 12px' }}>
                    <div>
                      <p style={{ margin: 0, fontSize: 13, fontWeight: 700, color: C.ink }}>{cl.claimNumber}{cl.type ? ` · ${cl.type}` : ''}</p>
                      <p style={{ margin: 0, fontSize: 11, color: C.inkSoft }}>{cl.registered || ''}</p>
                    </div>
                    <span style={pill('#fff', C.ink)}>{cl.status || '—'}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Feature cards */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 18 }}>
          {FEATURES.map(f => (
            <Link key={f.href} href={f.href} style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', gap: 16, textDecoration: 'none' }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={f.img} alt="" width={62} height={62} style={{ flexShrink: 0 }} />
              <div style={{ flex: 1 }}>
                <h2 style={{ ...h(20), marginBottom: 4 }}>{f.title}</h2>
                <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.45 }}>{f.desc}</p>
              </div>
              <ChevronRight size={20} style={{ color: '#C4C8CE' }} />
            </Link>
          ))}
        </div>

        {/* Earn more — quick activity logging */}
        <input ref={mealInputRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onMealFile} />
        <h2 style={{ ...h(24), margin: '26px 0 12px' }}>Earn more points</h2>
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={startStepSync} disabled={stepStage === 'preparing'}
            aria-label="Sync steps from your phone"
            aria-busy={stepStage === 'preparing'}
            style={{ flex: 1, ...card, padding: '16px 6px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: stepStage === 'preparing' ? 'progress' : 'pointer', border: `1.5px solid ${C.teal}` }}>
            <div style={{ width: 40, height: 40, borderRadius: 12, background: '#E6F4F2', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Footprints size={20} style={{ color: C.teal }} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, color: C.ink, textAlign: 'center' }}>
              {stepStage === 'preparing' ? 'Preparing…'
                : stepStage === 'needsApp' ? (isInAppShell() ? 'Not yet available' : 'Get the app')
                : stepStage === 'ready' ? 'Open Alpha Nexus'
                : 'Sync steps'}
            </span>
            <span style={{ fontSize: 11, color: C.teal, fontWeight: 700 }}>
              {stepStage === 'ready' ? 'tap to finish · up to +15'
                : stepStage === 'needsApp' ? (isInAppShell() ? 'needs a newer app version' : 'install or update to sync')
                : 'from your phone · up to +15'}
            </span>
          </button>
          {ACTIVITIES.map(a => {
            const Icon = a.icon
            return (
              <button key={a.kind} onClick={() => logActivity(a.kind)} disabled={logging === a.kind}
                style={{ flex: 1, ...card, padding: '16px 6px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Icon size={20} style={{ color: C.orange }} />
                </div>
                <span style={{ fontSize: 12, fontWeight: 600, color: C.ink, textAlign: 'center' }}>{logging === a.kind ? '…' : a.label}</span>
                <span style={{ fontSize: 11, color: C.teal, fontWeight: 700 }}>{a.pts}</span>
              </button>
            )
          })}
        </div>

        {/* Live insights */}
        <h2 style={{ ...h(24), margin: '26px 0 14px' }}>Live Insights</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <p style={{ color: C.inkSoft, fontSize: 11, letterSpacing: '0.1em', margin: 0 }}>DRIVE SCORE</p>
              <p style={{ margin: '4px 0 0' }}><span style={{ fontFamily: serif, fontWeight: 800, fontSize: 30, color: C.ink }}>{drive ?? '—'}</span><span style={{ color: C.inkSoft, fontSize: 14 }}>/100</span></p>
            </div>
            <Arc value={drive} color={C.orange} />
          </div>
          <div style={{ ...card, padding: 18, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <p style={{ color: C.inkSoft, fontSize: 11, letterSpacing: '0.1em', margin: 0 }}>WELLNESS SCORE</p>
              <p style={{ margin: '4px 0 0' }}><span style={{ fontFamily: serif, fontWeight: 800, fontSize: 30, color: C.ink }}>{wellness ?? '—'}</span><span style={{ color: C.inkSoft, fontSize: 14 }}>/100</span></p>
              {wellness == null && <p style={{ color: C.teal, fontSize: 12, margin: '4px 0 0', display: 'flex', alignItems: 'center', gap: 4 }}><Sparkles size={12} />Do a scan to begin</p>}
            </div>
            <Arc value={wellness} color={C.teal} />
          </div>
        </div>
        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 11, margin: '22px 0 4px', opacity: 0.75 }}>
          The wellness scan is a quick check-in, not a medical diagnosis.
        </p>

        {/* Feedback entry — "shape Nexus around what members value". */}
        <Link href="/m/feedback" style={{ ...card, padding: 16, marginTop: 18, display: 'flex', alignItems: 'center', gap: 12, textDecoration: 'none' }}>
          <span style={{ fontSize: 22 }}>💬</span>
          <div style={{ flex: 1 }}>
            <p style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.ink }}>Tell us what you think</p>
            <p style={{ margin: '2px 0 0', fontSize: 12, color: C.inkSoft }}>Help shape what Nexus builds next — 30 seconds.</p>
          </div>
          <ChevronRight size={20} style={{ color: '#C4C8CE' }} />
        </Link>

        {/* Account management — deletion is required in-app (Apple 5.1.1(v)). */}
        <div style={{ textAlign: 'center', margin: '18px 0 6px' }}>
          <button onClick={() => setDeleteOpen(true)}
            style={{ background: 'none', border: 'none', color: '#B91C1C', fontSize: 13, fontWeight: 600, cursor: 'pointer', textDecoration: 'underline', fontFamily: sans }}>
            Delete my account
          </button>
        </div>
      </main>

      {deleteOpen && (
        <div role="dialog" aria-modal="true" aria-labelledby="del-title"
          onClick={() => !deleting && setDeleteOpen(false)}
          onKeyDown={e => { if (e.key === 'Escape' && !deleting) setDeleteOpen(false) }}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()} style={{ ...card, maxWidth: 360, width: '100%', padding: 24 }}>
            <h2 id="del-title" style={{ ...h(22), marginBottom: 8 }}>Delete your account?</h2>
            <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.55, margin: '0 0 6px' }}>
              This permanently deletes your account and <b style={{ color: C.ink }}>all your data</b> — points, drive history, wellness results and activity. It cannot be undone.
            </p>
            <button onClick={confirmDelete} disabled={deleting}
              style={{ marginTop: 14, width: '100%', padding: '13px', borderRadius: 999, border: 'none', background: '#B91C1C', color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans, opacity: deleting ? 0.6 : 1 }}>
              {deleting ? 'Deleting…' : 'Yes, delete everything'}
            </button>
            <button onClick={() => setDeleteOpen(false)} disabled={deleting} autoFocus
              style={{ marginTop: 8, width: '100%', padding: '13px', borderRadius: 999, border: `1px solid ${C.line}`, background: '#fff', color: C.ink, fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans }}>
              Keep my account
            </button>
          </div>
        </div>
      )}

      {toast && (
        <div style={{ position: 'fixed', bottom: 88, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center', boxShadow: '0 10px 30px rgba(15,28,44,0.35)' }}>
          {toast}
        </div>
      )}
    </div>
  )
}
