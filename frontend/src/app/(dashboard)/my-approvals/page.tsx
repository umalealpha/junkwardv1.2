'use client'

/**
 * /my-approvals — unified approvals inbox (CFO 2026-07-13; bulk-approve 2026-07-22).
 * One place showing everything across Omni that awaits the signed-in user's
 * sign-off. Counts come from /api/v1/my-approvals/. The six clean single-signature
 * streams (journal entries, payments, incentives, staff loans, leave encashment,
 * petty cash) are itemised from /api/v1/my-approvals/items/ with per-item
 * checkboxes and a "Approve selected" button that posts to
 * /api/v1/my-approvals/bulk-approve/ — each item is signed through the SAME
 * server rule its own page uses (authority, SoD, quorum, audit unchanged). Every
 * other stream stays a count row that links out to its page to act.
 */
import { useCallback, useEffect, useMemo, useState, type ComponentType } from 'react'
import Link from 'next/link'
import { apiFetch, getMe } from '@/lib/api'
import {
  classifyApprovalClass,
  decisionButtonLabel,
  decisionPresetsFor,
  bulkButtonLabel,
  type ApprovalClass,
  type ApprovalPreset,
} from '@/lib/approvalProfiles'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Inbox, ArrowRight, CheckCircle2, FileCheck, CalendarDays, Receipt, Undo2,
  ShoppingCart, Coins, Banknote, BookOpenCheck, Landmark, HandCoins, Gift,
  Gavel, Wallet, Loader2, AlertTriangle, UserPlus, Percent,
} from 'lucide-react'

interface Stream { key: string; label: string; count: number; href: string }
interface Flag { level: 'warn' | 'info'; text: string }
interface Item { id: string; title: string; sub: string; amount: number | null; ccy: string; age_days: number; flags: Flag[] }
interface ItemStream { key: string; label: string; href: string; bulk_ok: boolean; items: Item[] }
interface BulkResult {
  approved: number
  approved_items: { stream: string; id: string }[]
  failed: { stream: string; id: string; error: string }[]
}
interface HistoryRow { kind: string; detail: string; at: string | null }

const STREAM_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  leave: CalendarDays,
  spend: Receipt,
  refunds: Undo2,
  po: ShoppingCart,
  leave_encash: Coins,
  leave_encash_pay: Banknote,
  journal_entries: BookOpenCheck,
  payments: Landmark,
  staff_loans: HandCoins,
  incentives: Gift,
  disciplinary: Gavel,
  petty_cash: Wallet,
  authority_to_recruit: UserPlus,
  commissions: Percent,
}

// A stable per-item key across the two streams (stream + id).
const rowKey = (streamKey: string, id: string) => `${streamKey}::${id}`


export default function MyApprovalsPage() {
  const [streams, setStreams] = useState<Stream[]>([])
  const [approvalClass, setApprovalClass] = useState<ApprovalClass>('general')
  const [itemStreams, setItemStreams] = useState<ItemStream[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [flash, setFlash] = useState<{ ok: string; errors: { label: string; error: string }[] } | null>(null)
  const [decideItem, setDecideItem] = useState<{ stream: string; id: string; title: string } | null>(null)
  const [deciding, setDeciding] = useState(false)
  const [history, setHistory] = useState<HistoryRow[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [brief, setBrief] = useState('')
  const [pushable, setPushable] = useState(false)
  const [pushOn, setPushOn] = useState(false)
  const openHistory = () => {
    setShowHistory(true)
    if (history === null)
      apiFetch<{ history: HistoryRow[] }>('/my-approvals/history/')
        .then(d => setHistory(d.history || [])).catch(() => setHistory([]))
  }

  useEffect(() => {
    getMe().then(profile => setApprovalClass(classifyApprovalClass(profile))).catch(() => setApprovalClass('general'))
  }, [])

  useEffect(() => {
    apiFetch<{ key: string }>('/my-approvals/push/vapid-key/')
      .then(d => setPushable(!!d.key)).catch(() => setPushable(false))
  }, [])

  // Decision-modal one-liner (wow-feature 3).
  useEffect(() => {
    setBrief('')
    if (!decideItem) return
    let live = true
    apiFetch<{ brief: string }>(`/my-approvals/brief/?stream=${encodeURIComponent(decideItem.stream)}&id=${encodeURIComponent(decideItem.id)}`)
      .then(d => { if (live) setBrief(d.brief || '') }).catch(() => {})
    return () => { live = false }
  }, [decideItem])

  const turnOnPush = async () => {
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) { setFlash({ ok: '', errors: [{ label: 'Alerts', error: 'This browser can\'t do push.' }] }); return }
      const { key } = await apiFetch<{ key: string }>('/my-approvals/push/vapid-key/')
      if (!key) return
      if (await Notification.requestPermission() !== 'granted') { setFlash({ ok: '', errors: [{ label: 'Alerts', error: 'Notifications blocked in the browser.' }] }); return }
      const reg = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      const pad = '='.repeat((4 - (key.length % 4)) % 4)
      const raw = atob((key + pad).replace(/-/g, '+').replace(/_/g, '/'))
      const arr = new Uint8Array(raw.length); for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i)
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: arr as BufferSource })
      await apiFetch('/my-approvals/push/subscribe/', { method: 'POST', body: JSON.stringify({ subscription: sub.toJSON() }) })
      setPushOn(true); setFlash({ ok: 'Alerts on — you\'ll be nudged when approvals arrive.', errors: [] })
    } catch { setFlash({ ok: '', errors: [{ label: 'Alerts', error: 'Could not turn on alerts.' }] }) }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [counts, items] = await Promise.all([
        apiFetch<{ streams: Stream[]; total: number }>('/my-approvals/'),
        apiFetch<{ streams: ItemStream[] }>('/my-approvals/items/').catch(() => ({ streams: [] })),
      ])
      setStreams(counts.streams || [])
      setTotal(counts.total || 0)
      setItemStreams(items.streams || [])
    } catch {
      /* leave empty */
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  // Keys that are itemised (rendered as checkbox cards) — so the count list
  // below shows only the streams that are NOT itemised, avoiding duplicates.
  const itemisedKeys = useMemo(
    () => new Set(itemStreams.map(s => s.key)),
    [itemStreams],
  )
  const countOnly = streams.filter(s => !itemisedKeys.has(s.key))

  const toggle = (k: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(k) ? next.delete(k) : next.add(k)
      return next
    })

  const toggleStream = (s: ItemStream) =>
    setSelected(prev => {
      const next = new Set(prev)
      const keys = s.items.map(i => rowKey(s.key, i.id))
      const allOn = keys.every(k => next.has(k))
      keys.forEach(k => (allOn ? next.delete(k) : next.add(k)))
      return next
    })

  const runDecision = async (p: ApprovalPreset) => {
    if (!decideItem) return
    const it = decideItem
    setDeciding(true)
    setFlash(null)
    try {
      await apiFetch('/my-approvals/decide/', {
        method: 'POST',
        body: JSON.stringify({ stream: it.stream, id: it.id, action: p.action, note: p.note || '' }),
      })
      setFlash({ ok: p.action === 'approve' ? `${p.label} saved.` : `${p.label}.`, errors: [] })
      setDecideItem(null)
      setSelected(prev => { const n = new Set(prev); n.delete(rowKey(it.stream, it.id)); return n })
      await load()
    } catch (e) {
      setFlash({ ok: '', errors: [{ label: it.title, error: e instanceof Error ? e.message : 'Could not action this.' }] })
    } finally { setDeciding(false) }
  }

  const approveSelected = async () => {
    // Map selected rowKeys back to {stream, id}.
    const payload: { stream: string; id: string }[] = []
    for (const s of itemStreams) {
      for (const it of s.items) {
        if (selected.has(rowKey(s.key, it.id))) payload.push({ stream: s.key, id: it.id })
      }
    }
    if (!payload.length) return
    setSubmitting(true)
    setFlash(null)
    try {
      const res = await apiFetch<BulkResult>('/my-approvals/bulk-approve/', {
        method: 'POST',
        body: JSON.stringify({ items: payload }),
      })
      // Build human labels for any failures.
      const titleFor = (streamKey: string, id: string) => {
        const s = itemStreams.find(x => x.key === streamKey)
        return s?.items.find(i => i.id === id)?.title || id
      }
      setFlash({
        ok: res.approved
          ? `Signed ${res.approved} item${res.approved === 1 ? '' : 's'}.`
          : 'Nothing was signed.',
        errors: (res.failed || []).map(f => ({ label: titleFor(f.stream, f.id), error: f.error })),
      })
      setSelected(new Set())
      await load()
    } catch {
      setFlash({ ok: '', errors: [{ label: 'Request', error: 'Could not reach the server. Nothing was signed.' }] })
    } finally {
      setSubmitting(false)
    }
  }

  const selectedCount = selected.size
  // Running cash total of ticked items (wow-feature 1) — BWP figure for a glance.
  const selectedTotal = useMemo(() => {
    let sum = 0
    for (const s of itemStreams) for (const it of s.items)
      if (selected.has(rowKey(s.key, it.id)) && it.amount) sum += it.amount
    return sum
  }, [itemStreams, selected])
  const fmtBWP = (n: number) => `BWP ${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-3xl mx-auto space-y-6 pb-28">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.11em] text-[#3F58CC]">
              <span className="w-[5px] h-[5px] rounded-full bg-[#4F6BED]" /> My work
            </div>
            <h1 className="mt-1.5 text-[25px] leading-tight font-bold tracking-[-0.03em] flex items-center gap-2.5">
              <Inbox className="h-6 w-6 text-[#F4A623]" /> My Approvals
              {total > 0 && <span className="text-xs font-bold text-white! bg-[#F4A623] rounded-full px-2 py-0.5">{total}</span>}
            </h1>
            <p className="text-sm text-muted-foreground mt-1.5">Everything across Omni waiting for your sign-off. Tick items and sign them all at once.</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {pushable && !pushOn && (
              <Button variant="outline" size="sm" onClick={turnOnPush}>🔔 Turn on alerts</Button>
            )}
            <Button variant="outline" size="sm" onClick={openHistory}>What I signed</Button>
          </div>
        </div>

        {flash && (
          <Card className="border-l-4 border-l-[#F4A623]">
            <CardContent className="p-4 space-y-2">
              {flash.ok && (
                <div className="flex items-center gap-2 text-sm font-medium text-emerald-700 dark:text-emerald-400">
                  <CheckCircle2 className="h-4 w-4" /> {flash.ok}
                </div>
              )}
              {flash.errors.length > 0 && (
                <div className="space-y-1">
                  <div className="flex items-center gap-2 text-sm font-medium text-[#B04E00]">
                    <AlertTriangle className="h-4 w-4" /> {flash.errors.length} couldn&apos;t be signed:
                  </div>
                  <ul className="text-xs text-muted-foreground pl-6 list-disc space-y-0.5">
                    {flash.errors.map((e, i) => <li key={i}><b>{e.label}</b> — {e.error}</li>)}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : total === 0 ? (
          <Card><CardContent className="p-8 text-center">
            <CheckCircle2 className="h-8 w-8 text-emerald-600 mx-auto mb-2" />
            <div className="font-medium">Nothing waiting for you.</div>
            <div className="text-sm text-muted-foreground">Inbox zero.</div>
          </CardContent></Card>
        ) : (
          <div className="space-y-4">
            {/* Itemised, bulk-approvable streams */}
            {itemStreams.map(s => {
              const Icon = STREAM_ICONS[s.key] ?? FileCheck
              const keys = s.items.map(i => rowKey(s.key, i.id))
              const allOn = keys.length > 0 && keys.every(k => selected.has(k))
              return (
                <Card key={s.key}>
                  <CardContent className="p-4 space-y-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#0D1B2A]/5 text-[#0D1B2A] dark:bg-white/10 dark:text-white">
                        <Icon className="h-5 w-5" />
                      </div>
                      <div className="flex-1 font-medium">{s.label}
                        <span className="ml-2 text-xs text-muted-foreground">({s.items.length})</span>
                      </div>
                      <button type="button" onClick={() => toggleStream(s)}
                        className="text-xs font-semibold text-[#0D1B2A] dark:text-[#F4A623] hover:underline">
                        {allOn ? 'Clear' : 'Select all'}
                      </button>
                      <Link href={s.href}><Button size="sm" variant="ghost">Open <ArrowRight className="h-4 w-4 ml-1" /></Button></Link>
                    </div>
                    <ul className="divide-y divide-border rounded-md border">
                      {s.items.map(it => {
                        const k = rowKey(s.key, it.id)
                        const on = selected.has(k)
                        return (
                          <li key={k} className="flex items-start gap-3 px-3 py-2 hover:bg-muted/40">
                            <input type="checkbox" checked={on} onChange={() => toggle(k)}
                              aria-label="Select for bulk approve"
                              className="h-4 w-4 accent-[#4F6BED] mt-1" />
                            <div className="flex-1 text-sm">
                              <div>
                                <span className="font-medium">{it.title}</span>
                                {it.sub && <span className="text-muted-foreground"> · {it.sub}</span>}
                              </div>
                              {it.flags.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 mt-1">
                                  {it.flags.map((f, i) => (
                                    <span key={i} className={`text-[10.5px] font-semibold px-1.5 py-0.5 rounded ${
                                      f.level === 'warn'
                                        ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300'
                                        : 'bg-muted text-muted-foreground'}`}>
                                      {f.level === 'warn' ? '⚠ ' : ''}{f.text}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                            {/* Approve opens the decision sheet (approve preset first);
                                a SEPARATE, visible Reject/ask control so the CFO can
                                send an item back without hunting — CFO 2026-08-31
                                "I don't have a reject button here". Both open the same
                                sheet (which carries reject / more-info / hold / duplicate);
                                the second button makes that capability visible. */}
                            <div className="flex items-center gap-1.5 shrink-0">
                              <Button size="sm" variant="outline"
                                onClick={() => setDecideItem({ stream: s.key, id: it.id, title: it.title })}>
                                {decisionButtonLabel(approvalClass, s.key)}
                              </Button>
                              {approvalClass !== 'executive_readonly' && approvalClass !== 'system' && (
                                <Button size="sm" variant="ghost"
                                  title="Reject, ask for more details, hold or flag a duplicate"
                                  onClick={() => setDecideItem({ stream: s.key, id: it.id, title: it.title })}
                                  className="text-[#B91C1C] hover:text-[#991B1B] hover:bg-red-50 dark:hover:bg-red-950/30">
                                  Reject / ask
                                </Button>
                              )}
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  </CardContent>
                </Card>
              )
            })}

            {/* Count-only streams (act on their own page) */}
            {countOnly.map(s => {
              const Icon = STREAM_ICONS[s.key] ?? FileCheck
              return (
                <Card key={s.key}><CardContent className="p-4 flex items-center gap-4">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#0D1B2A]/5 text-[#0D1B2A] dark:bg-white/10 dark:text-white">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="text-2xl font-bold tabular-nums w-10 text-center text-[#F4A623]">{s.count}</div>
                  <div className="flex-1 font-medium">{s.label}</div>
                  <Link href={s.href}><Button size="sm" variant="outline">Open <ArrowRight className="h-4 w-4 ml-1" /></Button></Link>
                </CardContent></Card>
              )
            })}
          </div>
        )}
      </div>

      {/* "What I signed" ledger (wow-feature 5) */}
      {showHistory && (
        <div className="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
          onClick={() => setShowHistory(false)}>
          <div onClick={e => e.stopPropagation()} className="bg-white dark:bg-[#0F172A] rounded-xl w-full max-w-md p-6 shadow-2xl max-h-[80vh] overflow-y-auto">
            <h3 className="text-base font-bold text-[#0D1B2A] dark:text-white">What I signed</h3>
            <p className="text-sm text-muted-foreground mb-3">Your recent approvals across Omni.</p>
            {history === null && <p className="text-sm text-muted-foreground">Loading…</p>}
            {history && history.length === 0 && <p className="text-sm text-muted-foreground">Nothing signed yet.</p>}
            {history && history.map((r, i) => (
              <div key={i} className={`py-2.5 ${i ? 'border-t' : ''}`}>
                <div className="flex justify-between gap-2">
                  <span className="text-xs font-bold text-[#B04E00]">{r.kind}</span>
                  {r.at && <span className="text-[11px] text-muted-foreground">{new Date(r.at).toLocaleDateString()}</span>}
                </div>
                <p className="text-sm mt-0.5">{r.detail}</p>
              </div>
            ))}
            <button onClick={() => setShowHistory(false)} className="w-full mt-3 py-2 text-sm font-medium text-muted-foreground">Close</button>
          </div>
        </div>
      )}

      {/* Five-button decision (CFO 2026-07-22) — one click, no typing */}
      {decideItem && (
        <div className="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
          onClick={() => !deciding && setDecideItem(null)}>
          <div onClick={e => e.stopPropagation()} className="bg-white dark:bg-[#0F172A] rounded-xl w-full max-w-md p-6 shadow-2xl">
            <h3 className="text-base font-bold text-[#0D1B2A] dark:text-white">
              {approvalClass === 'cfo' ? 'CFO authorisation' : `${approvalClass === 'general' ? 'Your' : approvalClass.replace('_', ' ')} decision`}
            </h3>
            <p className="text-sm text-muted-foreground mb-2">{decideItem.title}</p>
            {brief && <p className="text-[13px] mb-4 bg-muted rounded-md px-3 py-2">🤖 {brief}</p>}
            <div className="flex flex-col gap-2.5">
              {decisionPresetsFor(approvalClass, decideItem.stream).map(p => (
                <button key={p.key} onClick={() => runDecision(p)} disabled={deciding}
                  className="w-full text-left px-4 py-3 rounded-lg text-sm font-semibold disabled:opacity-60"
                  style={{
                    background: p.tone === 'yes' ? '#059669' : p.tone === 'no' ? '#B91C1C' : 'transparent',
                    color: p.tone === 'info' ? undefined : '#fff',
                    border: p.tone === 'info' ? '1px solid #D1D5DB' : 'none',
                  }}>
                  {p.label}
                </button>
              ))}
            </div>
            <button onClick={() => setDecideItem(null)} disabled={deciding}
              className="w-full mt-3 py-2 text-sm font-medium text-muted-foreground">Cancel</button>
          </div>
        </div>
      )}

      {/* Sticky action bar — appears once something is ticked */}
      {selectedCount > 0 && (
        <div className="fixed bottom-0 inset-x-0 border-t bg-background/95 backdrop-blur p-3 z-20">
          <div className="max-w-3xl mx-auto flex items-center gap-3">
            <span className="text-sm font-medium">{selectedCount} selected</span>
            {selectedTotal > 0 && <span className="text-sm text-muted-foreground">{fmtBWP(selectedTotal)}</span>}
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())} disabled={submitting}>Clear</Button>
            <div className="flex-1" />
            <Button onClick={approveSelected} disabled={submitting}
              className="bg-[#0D1B2A] text-white hover:bg-[#0D1B2A]/90">
              {submitting
                ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Signing…</>
                : <><CheckCircle2 className="h-4 w-4 mr-1" /> {bulkButtonLabel(approvalClass)} {selectedCount}{selectedTotal > 0 ? ` · ${fmtBWP(selectedTotal)}` : ''}</>}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
