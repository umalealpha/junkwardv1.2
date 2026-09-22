'use client'

/**
 * Customer-app API client (route /m). Separate from the staff `@/lib/api`:
 * it uses the customer bearer token (email-OTP login), hits only
 * /api/v1/rewards/customer/*, and on 401 sends the user to the customer login
 * — never the staff /login. No member id is ever sent; the server resolves the
 * member from the token, so a customer only sees their own data.
 */
import type { ThriveScore } from '@/lib/api'
import { getAppToken, clearAppToken } from '@/app/(app)/api'

const BASE = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1/rewards/customer`
const TKEY = 'alpha_rewards_token'

export function getCustToken(): string | null {
  return typeof window !== 'undefined' ? localStorage.getItem(TKEY) : null
}
export function setCustToken(t: string) { localStorage.setItem(TKEY, t) }
export function clearCustToken() { localStorage.removeItem(TKEY) }

const REQUEST_TIMEOUT_MS = 20000
// Photo uploads over a phone uplink need much longer than a JSON call.
const UPLOAD_TIMEOUT_MS = 90000
const timeoutFor = (opts: RequestInit) =>
  (typeof FormData !== 'undefined' && opts.body instanceof FormData) ? UPLOAD_TIMEOUT_MS : REQUEST_TIMEOUT_MS

/** Error carrying the HTTP status so callers can tell 401 from a transient blip. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

/** A genuinely-staff screen (already past the /m/staff gate) hit a 401 — the
 * staff bridge token has expired or been rotated. Unlike a customer-endpoint
 * 401, sfetch does NOT auto-redirect: on the /m/staff gate a 401 legitimately
 * means "this token isn't a staff account" and must be caught, not redirected.
 * So on a real staff screen the caller passes its error here to recover the same
 * way cfetch does — drop the dead token and send the user to sign in again —
 * instead of a silently-empty form or a raw "Invalid token" message. Returns
 * true when it handled a 401 (the caller should then stop). */
export function reauthOn401(e: unknown): boolean {
  if (e instanceof ApiError && e.status === 401) {
    if (getAppToken()) { clearAppToken(); if (typeof window !== 'undefined') window.location.href = '/app/login' }
    else { clearCustToken(); if (typeof window !== 'undefined') window.location.href = '/m/login' }
    return true
  }
  return false
}

/** Which shell is calling the staff endpoints: the Omni app's 30-day device
 * token wins when present (the screens render under /app AND /m/staff); else
 * the Nexus customer token via the staff bridge. */
export function staffToken(): { token: string | null; app: boolean } {
  const a = getAppToken()
  if (a) return { token: a, app: true }
  return { token: getCustToken(), app: false }
}
/** Drop the dead token and go to the sign-in page of the shell that owns it. */
export function staffReauth(app: boolean) {
  if (app) { clearAppToken(); window.location.href = '/app/login' }
  else { clearCustToken(); window.location.href = '/m/login' }
}

function _errMsg(e: unknown, status: number): string {
  const o = (e || {}) as { detail?: string; error?: string; errors?: Record<string, unknown> }
  if (o.detail) return o.detail
  if (o.error) return o.error
  if (o.errors && typeof o.errors === 'object') {
    const first = Object.values(o.errors)[0]
    if (Array.isArray(first) && first.length) return String(first[0])
    if (first) return String(first)
  }
  return `Request failed (${status})`
}

/** Downscale + re-encode a camera photo so a 3–12 MB shot becomes ~a few hundred KB
 * before upload — otherwise mobile uploads time out and hit backend size caps. */
export async function compressImage(file: File, maxDim = 1600, quality = 0.7): Promise<File> {
  if (typeof document === 'undefined' || !file.type.startsWith('image/')) return file
  try {
    const dataUrl: string = await new Promise((res, rej) => {
      const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = rej
      r.readAsDataURL(file)
    })
    const img: HTMLImageElement = await new Promise((res, rej) => {
      const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = dataUrl
    })
    const scale = Math.min(1, maxDim / Math.max(img.width, img.height))
    if (scale >= 1 && file.size < 900_000) return file   // already small enough
    const cv = document.createElement('canvas')
    cv.width = Math.round(img.width * scale); cv.height = Math.round(img.height * scale)
    const ctx = cv.getContext('2d'); if (!ctx) return file
    ctx.drawImage(img, 0, 0, cv.width, cv.height)
    const blob: Blob | null = await new Promise(r => cv.toBlob(r, 'image/jpeg', quality))
    if (!blob) return file
    return new File([blob], file.name.replace(/\.(heic|heif|png|webp)$/i, '.jpg'), { type: 'image/jpeg' })
  } catch { return file }   // never block an upload because compression failed
}

async function cfetch<T>(path: string, opts: RequestInit = {}, noRedirect = false): Promise<T> {
  const t = getCustToken()
  // A phone on a flaky/edge connection can leave fetch() pending forever, which
  // freezes the screen with no error and no recovery. Abort after a timeout so
  // the caller's .catch() runs and the UI can show a retryable message.
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutFor(opts)) : null
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...opts,
      signal: ctrl ? ctrl.signal : undefined,
      headers: {
        'Content-Type': 'application/json',
        ...(t ? { Authorization: `Bearer ${t}` } : {}),
        ...(opts.headers || {}),
      },
    })
  } catch (err) {
    if (ctrl && (err as Error)?.name === 'AbortError') throw new ApiError('The network is slow right now. Please try again.', 0)
    throw new ApiError('No connection. Check your internet and try again.', 0)
  } finally {
    if (timer) clearTimeout(timer)
  }
  if (res.status === 401 && !noRedirect) {
    clearCustToken()
    if (typeof window !== 'undefined') window.location.href = '/m/login'
    throw new ApiError('Please sign in again.', 401)
  }
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new ApiError(_errMsg(e, res.status), res.status)
  }
  return res.json() as Promise<T>
}

export interface CustomerMember {
  id: string; name: string; tier: string; tierDisplay: string; points: number; hasEmail: boolean
}
export interface ThriveVitals {
  restingHr: number | null; hrvSdnn: number | null; hrvRmssd: number | null
  hrvPnn50: number | null; respirationRate: number | null; stressBand: string
  beats: number; scanConfidence: number
}
export interface ScanResult { vitals: ThriveVitals; alphaScore: ThriveScore; date: string }
export interface AlphaScoreResp { score: ThriveScore; tier: string; points: number; asOf: string | null; hasVitals: boolean }
export interface TrendResp { points: { date: string; score: number }[]; slope: number; direction: string }
export interface CoachResp { nudge: string; engine: string; scoreBand: string }
export interface RewardsResp {
  member: CustomerMember; discount: number; nextTier: string
  transactions: { kind: string; points: number; detail: string; program: string | null; occurredAt: string | null }[]
}
export interface DriveTrip {
  startedAt: string; score: number; distanceKm: number; durationMin: number
  harshEvents: number; idleMinutes: number; pointsAwarded: number
  // null when the phone's peak-speed reading was not supported by the distance
  // covered (a GPS lock-on spike) — the speed is unknown, not zero.
  maxSpeed: number | null
  counted: boolean          // false = kept for the record, never graded
  notCountedWhy: string
}
export interface DriveProfile {
  band: string; verdict: string; trips: number; totalKm: number; avgScore: number | null
  // null = too few events / too little distance for the rate to mean anything;
  // show the raw harshEvents count instead of a made-up rate.
  harshPer100km: number | null
  harshEvents: number
  idlePct: number; topSpeed: number; flags: string[]
  excludedTrips: number     // recordings too short to grade
}
export interface DriveResp {
  scores: { period: string; score: number; distanceKm: string; harshEvents: number; pointsAwarded: number; vehicleReg: string }[]
  trips: DriveTrip[]; profile: DriveProfile; avgScore: number | null; count: number
}
export interface DriveTripResult {
  trip: {
    score: number; band: string
    factors: { key: string; label: string; note: string; penalty: number }[]
    pointsAwarded: number; distanceKm: number; durationMin: number
    // null = the phone's peak reading was not supported by the distance
    // covered, so the speed is unknown (never shown as 0).
    harshEvents: number; idleMinutes: number; maxSpeed: number | null
  }
  totalPoints: number; tier: string
}
export const postDriveTrip = (p: { startedAt?: string; distanceKm: number; durationMin: number; idleMinutes: number; harshEvents: number; maxSpeed: number }) =>
  cfetch<DriveTripResult>('/drive/trip/', { method: 'POST', body: JSON.stringify(p) })

// --- auth (no redirect on 401 — we handle errors inline on the login page) ---
export const requestOtp = (email: string, referralCode?: string) =>
  cfetch<{ ok: boolean; message: string }>('/request-otp/', {
    method: 'POST',
    // A referral code is recorded at sign-up and NEVER paid there (the reward
    // lands only once the referred policy has stuck). A bad code is ignored
    // server-side and must never stop somebody joining.
    body: JSON.stringify(referralCode ? { email, referralCode } : { email }),
  }, true)
export const verifyOtp = (email: string, code: string) =>
  cfetch<{ token: string; member: CustomerMember }>('/verify-otp/', { method: 'POST', body: JSON.stringify({ email, code }) }, true)
export const logout = () => cfetch<{ ok: boolean }>('/logout/', { method: 'POST' }).catch(() => ({ ok: true }))
// Permanent in-app account deletion (App Store Guideline 5.1.1(v)).
export const deleteAccount = () =>
  cfetch<{ ok: boolean; detail: string }>('/delete-account/', { method: 'POST', body: JSON.stringify({ confirm: 'DELETE' }) })

// In-app feedback / short survey — shape Nexus around what members value.
// v10 step sync: mint a one-time pairing code (web session) that the native
// PairingActivity swaps for a steps-only device token. See rewards/device_auth.py.
export const pairStart = () =>
  cfetch<{ code: string; expiresInSeconds: number }>('/pair/start/', { method: 'POST' })

export const postFeedback = (p: { rating: number; area?: string; comment?: string; wants?: string }) =>
  cfetch<{ ok: boolean; detail: string }>('/feedback/', { method: 'POST', body: JSON.stringify(p) })

// --- scoped data ---
export const getMe = () => cfetch<CustomerMember>('/me/')
export const getRewards = () => cfetch<RewardsResp>('/rewards/')
export const getDrive = () => cfetch<DriveResp>('/drive/')
export const postScan = (rrIntervals: number[], respirationRate?: number) =>
  cfetch<ScanResult>('/thrive/scan/', { method: 'POST', body: JSON.stringify({ rrIntervals, respirationRate }) })
export const getAlphaScore = () => cfetch<AlphaScoreResp>('/thrive/alpha-score/')
export const getTrend = () => cfetch<TrendResp>('/thrive/risk-trend/')
export const postCoach = () => cfetch<CoachResp>('/thrive/coach/', { method: 'POST', body: JSON.stringify({}) })

export interface ActivityResult { kind: string; pointsAwarded: number; totalPoints: number; tier: string; alreadyToday: boolean; mealScore: number | null }
// Only the AI-verified meal photo may be posted here — typed steps and one-tap
// workouts are refused by the server (CFO 7-Sep-2026). Steps: pairStart + the app.
export const postActivity = (kind: 'healthy_eating', opts?: { detail?: string; imageData?: string }) =>
  cfetch<ActivityResult>('/activity/', { method: 'POST', body: JSON.stringify({ kind, ...opts }) })

// --- growth: quest / streak / weekly challenge / savings / leaderboard ---
export interface GrowthResp {
  quest: { steps: { key: string; label: string; done: boolean }[]; complete: boolean; bonus: number; bonusPaid: boolean; bonusPaidNow: boolean }
  streak: { days: number; activeToday: boolean }
  challenge: { label: string; progress: number; target: number; bonus: number; done: boolean; bonusPaid: boolean }
  savings: { discountPct: number; monthlyPremium: number | null; monthlySaving: number | null; yearlySaving: number | null; nextTier: string | null; nextTierPct: number | null; policyLinked: boolean }
  leaderboard: { top: { rank: number; name: string; isMe: boolean; nexusScore: number; trips: number }[]; me: { rank: number | null; total: number; nexusScore: number } }
  totalPoints: number
  tier: string
}
export const getGrowth = () => cfetch<GrowthResp>('/growth/')

export interface PolicyCardResp {
  linked: boolean; policyNumber?: string; monthlyPremium?: number | null
  claims?: { claimNumber: string; type: string; status: string; registered: string | null; paid: number }[]
}
// --- Teams, screening voucher, referrals (CFO 2026-09-08) -------------------
export interface TeamMate { id: string; name: string; km: number; points: number; trips: number }
export interface TeamResp {
  inTeam: boolean
  label?: string
  teamName?: string
  joinCode?: string
  maxSize: number
  size?: number
  full?: boolean
  spacesLeft?: number
  totalKm?: number
  totalPoints?: number
  totalTrips?: number
  members?: TeamMate[]
}
export const getTeam = () => cfetch<TeamResp>('/team/')
export const createTeam = (name: string) =>
  cfetch<TeamResp>('/team/', { method: 'POST', body: JSON.stringify({ name }) })
export const joinTeam = (code: string) =>
  cfetch<TeamResp>('/team/join/', { method: 'POST', body: JSON.stringify({ code }) })
export const leaveTeam = () => cfetch<TeamResp>('/team/leave/', { method: 'POST' })

export interface ScreeningResp {
  unlocked: boolean
  remaining: number
  nextEligible: string | null
  label: string
  scansDone: number
  required: number
  // Set once a voucher has been issued: checks only count from that date, so
  // the screening is earned again each year (CFO ruling 2026-09-09).
  countingSince: string | null
  voucher: { code: string; expiresOn: string } | null
}
export const getScreening = () => cfetch<ScreeningResp>('/screening/')
export const claimScreening = () => cfetch<ScreeningResp>('/screening/claim/', { method: 'POST' })

export interface ReferralResp {
  code: string
  friendsJoined: number
  friendsQualified: number
  maxPerYear: number
  // Both sides carry their OWN figure: equal today, and the sentence would
  // become a lie the day only one of them changes.
  pointsYou: number
  pointsFriend: number
  qualifyAfterDays: number
  label: string
  friends: { name: string; joined: string; qualified: boolean }[]
}
export const getReferral = () => cfetch<ReferralResp>('/referral/')

export const getPolicyCard = () => cfetch<PolicyCardResp>('/policy/')

// ===========================================================================
// Staff portal (CFO 2026-07-14). An EMPLOYEE signed in with their work email
// can call the normal omni APIs with the same customer token — the server's
// NexusStaffAuthentication maps the token to their staff account, so every
// existing permission rule applies unchanged. A plain customer gets 401 from
// these endpoints and the app simply never shows the staff portal.
// ===========================================================================
const API = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1`
const HRIS = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/hris/api`

async function bridged<T>(base: string, path: string, opts: RequestInit = {}, json = true, probe = false): Promise<T> {
  const { token: t, app } = staffToken()
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutFor(opts)) : null
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      ...opts,
      signal: ctrl ? ctrl.signal : undefined,
      headers: {
        ...(json ? { 'Content-Type': 'application/json' } : {}),
        ...(t ? { Authorization: `Bearer ${t}` } : {}),
        ...(opts.headers || {}),
      },
    })
  } catch (err) {
    if (ctrl && (err as Error)?.name === 'AbortError') throw new ApiError('The network is slow right now. Please try again.', 0)
    throw new ApiError('No connection. Check your internet and try again.', 0)
  } finally { if (timer) clearTimeout(timer) }
  // Expired / rotated staff-bridge token: recover the same way cfetch does for a
  // customer — drop the dead token and send the user to sign in again — instead
  // of every staff screen silently ending up empty or showing a raw "Invalid
  // token". This is central on purpose: every staff screen (today's and any
  // built later) gets recovery for FREE, so this whole class of bug can't come
  // back one screen at a time. The ONLY opt-out is a staff-detection PROBE
  // (probe=true), where a 401 legitimately means "this token isn't a staff
  // account" and the caller must catch it, not be redirected.
  if (res.status === 401 && !probe && typeof window !== 'undefined') {
    staffReauth(app)
    throw new ApiError('Please sign in again.', 401)
  }
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new ApiError(_errMsg(e, res.status), res.status)
  }
  return res.json() as Promise<T>
}
export const sfetch = <T,>(path: string, opts: RequestInit = {}, json = true, probe = false) => bridged<T>(API, path, opts, json, probe)
export const hfetch = <T,>(path: string, opts: RequestInit = {}, json = true, probe = false) => bridged<T>(HRIS, path, opts, json, probe)

/** Fetch a binary file (e.g. a payslip PDF) from the omni v1 API with the staff
 * bearer token, so a phone can download its own documents.
 * NOTE: throws a plain Error (not ApiError) — callers read .message only, never
 * .status. If a future caller needs the status, switch this to throw ApiError. */
export async function sfetchBlob(path: string): Promise<Blob> {
  const { token: t, app } = staffToken()
  const res = await fetch(`${API}${path}`, {
    headers: { ...(t ? { Authorization: `Bearer ${t}` } : {}) },
  })
  // Same expired-sign-in recovery as bridged(): a dead token on a download (e.g.
  // a payslip PDF) sends the user to sign in again, not a "Download failed (401)".
  if (res.status === 401 && typeof window !== 'undefined') {
    staffReauth(app)
    throw new Error('Please sign in again.')
  }
  if (!res.ok) throw new Error(`Download failed (${res.status})`)
  return res.blob()
}

export interface ApprovalStream { key: string; label: string; count: number; href: string }
export interface MyApprovals { streams: ApprovalStream[]; total: number }
/** Staff probe: resolves -> employee; throws (401) -> plain customer. Runs with
 * probe=true so a customer's 401 is NOT redirected to sign-in — the caller (the
 * /m and /m/staff gates) must catch it to decide whether to show the portal. */
export const getStaffApprovals = () => sfetch<MyApprovals>('/my-approvals/', {}, true, true)

// --- itemised bulk approvals (one dashboard for approvers, CFO 2026-07-22) ---
export interface ApprovalFlag { level: 'warn' | 'info'; text: string }
export interface ApprovalItem { id: string; title: string; sub: string; amount: number | null; ccy: string; age_days: number; flags: ApprovalFlag[] }
export interface ApprovalItemStream { key: string; label: string; href: string; bulk_ok: boolean; items: ApprovalItem[] }
export interface MyApprovalItems { streams: ApprovalItemStream[]; total: number }
export interface ApprovalHistoryRow { kind: string; detail: string; at: string | null }
export const getStaffApprovalHistory = () => sfetch<{ history: ApprovalHistoryRow[]; total: number }>('/my-approvals/history/')

// Decision-sheet one-liner (wow-feature 3) — computed on demand per item.
export const getApprovalBrief = (stream: string, id: string) =>
  sfetch<{ brief: string }>(`/my-approvals/brief/?stream=${encodeURIComponent(stream)}&id=${encodeURIComponent(id)}`)

// --- the full detail behind ONE approval (CFO 2026-09-20) -------------------
// "expecting a busy cfo to go inside the web version to check is unreasonable".
// The SAME pack core/approval_pack.py renders into the approval email and the
// no-login confirm page, so what you read in the mail and what you see here can
// never disagree. The server only answers for an item on your own list.
export interface ApprovalPack {
  stream: string; title: string; subtitle: string
  summary: { label: string; value: string }[]
  columns: string[]; rows: string[][]
  row_count: number; shown_count: number; row_total: string
  checks: { level: 'clean' | 'check'; items: string[] }
  note: string
}
export const getApprovalPack = (stream: string, id: string) =>
  sfetch<{ pack: ApprovalPack | null }>(`/my-approvals/pack/?stream=${encodeURIComponent(stream)}&id=${encodeURIComponent(id)}`)

// --- the pack behind a payment-authorisation task (CFO 2026-08-03) ----------
// The phone task card only ever showed a title and a total, so the approver
// could not see which suppliers, invoices and amounts made it up. Same endpoint
// the desktop Payment Requests screen uses — no new source of truth.
export interface PaymentLine {
  description?: string; gl_code?: string; ref?: string; amount?: string | number
  invoice_number?: string; invoice_date?: string; due_date?: string
  terms_basis?: string; terms_days?: string | number; claim_number?: string
}
export interface PaymentAttachment { id: string; name: string; uploaded_by: string; created_at: string }
export interface PaymentPack {
  id: string; ref: string; entity: string; category: string; category_label: string
  claim_payee_label: string; status: string; status_label: string
  currency: string; subject: string; payee: string
  line_items: PaymentLine[]; total: string
  account_name: string; account_number: string; bank_name: string
  due_date: string | null; inputter: string; verifier: string
  summary: string; created_at: string; created_by: string
  first_approver: string; decision_notes: string
  attachments: PaymentAttachment[]
}
export const getPaymentPack = (id: string) => sfetch<PaymentPack>(`/payment-requests/${id}/`)

// --- authorise every clean pack in one press (CFO 2026-08-04) ---------------
// The CFO asked to have his packs approved for him; that cannot be delegated,
// so this makes his own signature one press instead. `confirm_total` echoes
// back the figure he was shown — the server refuses the run if its own recount
// differs, so a pack that changed while he was reading can never ride along.
export interface BulkPack {
  id: string; ref: string; entity: string; subject: string
  currency: string; total: string; lines: number
  task_id: string  // the CFO authorisation task — drives per-payment approve/reassign
  signed_off_by: string; edited_after_signoff: boolean
  clashes?: number; clash_total?: string; clash_detail?: string[]
}
export interface BulkPreview {
  ready: BulkPack[]; blocked: BulkPack[]
  ready_total: string; ready_count: number; blocked_count: number
}
export interface BulkResult {
  authorised: BulkPack[]; authorised_count: number; authorised_total: string
  refused: (BulkPack & { reason?: string })[]; refused_count: number
}
export const getPaymentBulkPreview = () => sfetch<BulkPreview>('/payment-requests/bulk/preview/')
export const approvePaymentBulk = (confirmTotal: string) =>
  sfetch<BulkResult>('/payment-requests/bulk/approve/', {
    method: 'POST', body: JSON.stringify({ confirm_total: confirmTotal }),
  })

// --- Per-payment CFO actions on the mobile Payments screen (CFO 2026-08-29) ----
// These are the SAME endpoints the desktop Payment Requests screen uses — the
// mobile screen had lost them (view-only), so the CFO could no longer approve,
// decline, hand off, or ask for a change from his phone.

// Approve ONE payment = mark its task paid. Completing the payment task runs the
// same PAY-* duplicate controls as the desktop "Done — mark as paid".
export const authoriseOnePayment = (taskId: string) =>
  sfetch(`/taskboard/tasks/${taskId}/complete/`, {
    method: 'POST', body: JSON.stringify({ body: '', interaction_seconds: 0 }),
  })

// Decline = clear the request out of the queue WITHOUT paying it (a reason is
// required). Does NOT move money.
export const clearPaymentRequest = (reqId: string, notes: string) =>
  sfetch(`/payment-requests/${reqId}/clear/`, {
    method: 'POST', body: JSON.stringify({ notes }),
  })

// Assign to someone else (e.g. escalate above your limit). Reassigns the task.
export interface TaskAssignee { username: string; full_name: string; title: string; department: string }
export const getTaskAssignees = () =>
  sfetch<{ assignees: TaskAssignee[] }>('/tasks/assignees/').then(d => d.assignees || [])
// reassign_to is resolved by USERNAME server-side (core.api_views omni_task_detail).
export const reassignPaymentTask = (taskId: string, username: string, reason: string) =>
  sfetch(`/tasks/${taskId}/`, {
    method: 'PATCH',
    body: JSON.stringify({ reassign_to: username, ...(reason ? { reassign_reason: reason } : {}) }),
  })
export const paymentAttachmentBlob = (attId: string) =>
  sfetchBlob(`/payment-request-attachments/${attId}/file/`)

/** A PO attachment through its authenticated endpoint (CFO 2026-09-04: "PO
 * attachments should be able to open from the phone"). The list's raw `url`
 * is /media/…, which prod never serves; `file_url` from the same list is the
 * root-relative /api/v1/purchase-orders/<po>/attachments/<att>/file/ path —
 * sfetchBlob prepends /api/v1 itself, so that prefix is stripped here. */
export const fetchPoAttachmentBlob = (fileUrl: string) =>
  sfetchBlob(fileUrl.replace(/^\/api\/v1(?=\/)/, ''))

// --- Web Push (wow-feature 4) ------------------------------------------------
function urlB64ToUint8Array(b64: string): Uint8Array {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const base = (b64 + pad).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base)
  const arr = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i)
  return arr
}

/** Register the SW, subscribe to push with the server's VAPID key, and store
 * the subscription. Returns 'on' | 'unsupported' | 'denied' | 'unconfigured' |
 * 'error' so the UI can speak plainly. Safe to call again (idempotent). */
export async function enableApprovalPush(): Promise<string> {
  try {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator) || !('PushManager' in window))
      return 'unsupported'
    const { key } = await sfetch<{ key: string }>('/my-approvals/push/vapid-key/')
    if (!key) return 'unconfigured'
    const perm = await Notification.requestPermission()
    if (perm !== 'granted') return 'denied'
    const reg = await navigator.serviceWorker.register('/sw.js')
    await navigator.serviceWorker.ready
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(key) as BufferSource,
    })
    await sfetch('/my-approvals/push/subscribe/', {
      method: 'POST', body: JSON.stringify({ subscription: sub.toJSON() }),
    })
    return 'on'
  } catch {
    return 'error'
  }
}
export interface BulkApproveResult {
  approved: number
  approved_items: { stream: string; id: string }[]
  failed: { stream: string; id: string; error: string }[]
}
/** The six-plus sign-only streams the current user can approve with one tap. */
export const getStaffApprovalItems = () => sfetch<MyApprovalItems>('/my-approvals/items/')
/** Sign the selected items in one call — same server rules as each item's page. */
export const bulkApprove = (items: { stream: string; id: string }[]) =>
  sfetch<BulkApproveResult>('/my-approvals/bulk-approve/', { method: 'POST', body: JSON.stringify({ items }) })

/** One-tap decision on a single item: approve, or reject with a canned reason
 * (the "send back / more info / hold / duplicate" buttons all reject-with-reason). */
export const decideApproval = (stream: string, id: string, action: 'approve' | 'reject', note?: string) =>
  sfetch<{ ok: boolean }>('/my-approvals/decide/', { method: 'POST', body: JSON.stringify({ stream, id, action, note: note || '' }) })

/** The five preset buttons (CFO 2026-07-22), reused on Omni + Nexus. Reject
 * variants carry the canned reason sent back to the submitter. */
export const DECISION_PRESETS: { key: string; label: string; action: 'approve' | 'reject'; note?: string; tone: 'yes' | 'no' | 'info' }[] = [
  { key: 'approve', label: 'Yes — approve', action: 'approve', tone: 'yes' },
  { key: 'reject', label: 'No — reject', action: 'reject', tone: 'no', note: 'Rejected — this is not approved. See my note / speak to me before re-submitting.' },
  { key: 'more_info', label: 'Send back — need more info', action: 'reject', tone: 'info', note: 'I need more information before I can approve: payee, amount, purpose, due date and the supporting documents. Please add these and re-submit.' },
  { key: 'hold', label: 'Hold — next run', action: 'reject', tone: 'info', note: 'On hold for cash-flow timing — include this in the next payment run and re-submit.' },
  { key: 'duplicate', label: 'Possible duplicate — verify', action: 'reject', tone: 'info', note: 'This looks like a duplicate or an already-paid item — confirm with Finance, then re-submit if it is genuinely still owed.' },
]
