'use client'

/**
 * NotificationCenter — bell with real count + dropdown + sticky banner.
 *
 * CFO directive 2026-05-21: every pending approval must surface visibly.
 * The bell that showed a permanent red dot was hiding messages. This
 * component:
 *
 *   - polls /api/v1/notifications/pending/ every 30 seconds
 *   - bell shows the real count (badge hidden when 0)
 *   - click bell -> dropdown panel listing pending items by type
 *     with one-click navigation to the underlying record
 *   - when count > 0 AND user hasn't dismissed this session, a
 *     sticky orange banner sits across the top of every page
 *
 * Mounts once inside the top bar; safe to render on every dashboard
 * page because polling only happens when the user is authenticated.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useRouter } from 'next/navigation'
import { Bell, X, ArrowRight, FileText, Receipt, Boxes, CalendarClock } from 'lucide-react'  // eslint-disable-line @typescript-eslint/no-unused-vars
import { apiFetch, getToken } from '@/lib/api'

interface PendingItem {
  type:     string   // 'purchase_order' | 'journal_entry' | 'leave' | 'asset_import' | ...
  id:       string
  title:    string
  subtitle: string
  amount?:  string | null
  currency?: string | null
  url:      string
  raised_by?: string
  raised_at?: string | null
}

interface PendingResponse {
  count:   number
  by_type: Record<string, number>
  items:   PendingItem[]
}

const POLL_MS = 30_000

/** DOM id of the header slot the "N tasks pending" pill renders into. */
export const PILL_SLOT_ID = 'omni-pending-pill-slot'

function typeIcon(t: string) {
  if (t === 'purchase_order')  return <Receipt    className="w-4 h-4" />
  if (t === 'journal_entry')   return <FileText   className="w-4 h-4" />
  if (t === 'leave')           return <CalendarClock className="w-4 h-4" />
  if (t === 'asset_import')    return <Boxes      className="w-4 h-4" />
  return <Bell className="w-4 h-4" />
}

function typeLabel(t: string) {
  return ({
    purchase_order: 'Purchase Order',
    journal_entry:  'Journal Entry',
    leave:          'Leave',
    asset_import:   'Asset Import',
  } as Record<string, string>)[t] || t
}

export default function NotificationCenter() {
  const router = useRouter()
  const [data, setData] = useState<PendingResponse | null>(null)
  const [open, setOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

  const load = useCallback(async () => {
    if (!getToken()) return
    try {
      const r = await apiFetch<PendingResponse>('/notifications/pending/')
      setData(r)
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    load()
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [load])

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (!dropdownRef.current) return
      if (dropdownRef.current.contains(e.target as Node)) return
      setOpen(false)
    }
    window.addEventListener('mousedown', handler)
    return () => window.removeEventListener('mousedown', handler)
  }, [open])

  const count = data?.count || 0

  // The pill lives in a slot TopBar renders next to the breadcrumb. It used to
  // be `fixed top-2 left-1/2` — centred on the VIEWPORT, so it ignored the
  // layout entirely: with the sidebar offsetting the header content, it landed
  // straight on top of the breadcrumb and clipped long page titles like
  // "Internal Audit — Dashboard" down to "Internal Aud…" (Oprah, QA
  // 2026-07-25). Portaling into a real flex child makes the browser keep the
  // title and the pill apart instead of painting one over the other.
  const [pillSlot, setPillSlot] = useState<HTMLElement | null>(null)
  useEffect(() => { setPillSlot(document.getElementById(PILL_SLOT_ID)) }, [])

  // CFO directive 2026-05-21 — compact growing pill at the top of the page.
  // Not a wide banner, not dismissible. Count rises as items accumulate,
  // click opens the dropdown.
  const pill = count > 0 ? (
    <button
      onClick={() => setOpen(o => !o)}
      className="flex flex-shrink-0 items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold shadow-md transition-transform hover:scale-105 whitespace-nowrap"
      style={{
        background: '#0D1B2A',
        color: '#FFFFFF',
        border: '1px solid #F4A623',
      }}
      aria-label={`${count} task${count === 1 ? '' : 's'} pending`}
    >
      <span
        className="flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[11px] font-bold"
        style={{ background: '#F4A623', color: '#0D1B2A' }}
      >
        {count > 99 ? '99+' : count}
      </span>
      <span>
        task{count === 1 ? '' : 's'} pending
      </span>
    </button>
  ) : null

  return (
    <>
      {/* Slot missing (no TopBar on this page) — fall back to rendering the
          pill inline beside the bell rather than dropping it silently. */}
      {pill && (pillSlot ? createPortal(pill, pillSlot) : pill)}

      {/* Bell */}
      <div ref={dropdownRef} className="relative">
        <button
          className="relative w-9 h-9 flex items-center justify-center rounded-lg transition-colors"
          style={{ color: 'inherit' }}
          onClick={() => setOpen(o => !o)}
          aria-label={`Notifications (${count})`}
          aria-expanded={open}
        >
          <Bell className="w-4 h-4" strokeWidth={1.5} />
          {count > 0 && (
            <span
              className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full text-[10px] font-bold text-white"
              style={{ background: '#DC2626' }}
            >
              {count > 99 ? '99+' : count}
            </span>
          )}
        </button>

        {/* Dropdown */}
        {open && (
          <div
            className="absolute right-0 mt-2 w-[380px] max-h-[480px] overflow-y-auto rounded-xl shadow-xl border bg-white z-50"
            style={{ borderColor: '#E5E7EB' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: '#E5E7EB' }}>
              <div className="font-semibold text-[#0D1B2A]">
                Pending approvals
                {count > 0 && (
                  <span className="ml-2 px-2 py-0.5 rounded-full text-xs font-bold text-white"
                        style={{ background: '#DC2626' }}>
                    {count}
                  </span>
                )}
              </div>
              <button onClick={() => setOpen(false)} aria-label="Close">
                <X className="w-4 h-4 text-[#6B7280]" />
              </button>
            </div>

            {count === 0 && (
              <div className="px-4 py-8 text-center text-sm text-[#6B7280]">
                Nothing waiting for you. <span className="opacity-60">Sharp.</span>
              </div>
            )}

            {data?.items.map(it => (
              <button
                key={`${it.type}-${it.id}`}
                onClick={() => { setOpen(false); router.push(it.url) }}
                className="w-full text-left px-4 py-3 flex items-start gap-3 border-b hover:bg-[#F9FAFB] transition-colors"
                style={{ borderColor: '#F3F4F6' }}
              >
                <div className="mt-0.5 flex-none w-7 h-7 flex items-center justify-center rounded-md"
                     style={{ background: '#FFFBEB', color: '#92400E' }}>
                  {typeIcon(it.type)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs uppercase tracking-wider text-[#6B7280]">
                    {typeLabel(it.type)}
                  </div>
                  <div className="text-sm font-medium text-[#0D1B2A] truncate">
                    {it.title}
                  </div>
                  <div className="text-xs text-[#6B7280] truncate">
                    {it.subtitle}
                  </div>
                </div>
                <ArrowRight className="w-4 h-4 text-[#9CA3AF] flex-none mt-1" />
              </button>
            ))}

            {count > 0 && (
              <button
                onClick={() => { setOpen(false); router.push('/approvals') }}
                className="w-full px-4 py-3 text-sm font-semibold text-center text-[#F4A623] hover:bg-[#FFFBEB]"
              >
                Open the approvals queue →
              </button>
            )}
          </div>
        )}
      </div>
    </>
  )
}
