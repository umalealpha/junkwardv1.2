'use client'

/**
 * Lightweight in-house toast — no extra dependency.
 *
 * CFO structural-audit directive 2026-05-19: the frontend was swallowing
 * API errors silently. Every 4xx / 5xx must surface to the user. This
 * Toaster mounts once in the root layout and listens for events emitted
 * by `pushToast()` (called from `apiFetch` on non-2xx responses + by any
 * component that wants to flash a message).
 *
 * Usage:
 *   import { pushToast } from '@/components/Toaster'
 *   pushToast({ message: 'Saved', type: 'success' })
 *
 * Mounted globally via app/layout.tsx so it's available app-wide.
 */

import { useEffect, useState } from 'react'

export type ToastType = 'info' | 'success' | 'warning' | 'error'

export interface ToastInput {
  message: string
  description?: string
  type?: ToastType
  /** Milliseconds before auto-dismiss. 0 = sticky. Default 6000. */
  duration?: number
}

interface ToastRecord extends ToastInput {
  id: number
  /** How many identical toasts have been coalesced into this one (>=1). */
  count?: number
}

let listeners: Array<(t: ToastRecord) => void> = []
let nextId = 1

/** Emit a toast from anywhere — module-level helper. */
export function pushToast(t: ToastInput) {
  const record: ToastRecord = { id: nextId++, type: 'info', duration: 6000, ...t }
  for (const fn of listeners) fn(record)
}

const COLORS: Record<ToastType, { bg: string; border: string; text: string }> = {
  info:    { bg: '#0D1B2A', border: '#0D1B2A', text: '#FFFFFF' },
  success: { bg: '#1F5132', border: '#B6E0C2', text: '#FFFFFF' },
  warning: { bg: '#F4A623', border: '#F4A623', text: '#0D1B2A' },
  error:   { bg: '#8E1F12', border: '#F5C2BE', text: '#FFFFFF' },
}

export function Toaster() {
  const [items, setItems] = useState<ToastRecord[]>([])

  useEffect(() => {
    const handler = (t: ToastRecord) => {
      let coalesced = false
      setItems((prev) => {
        // Coalesce identical toasts instead of stacking duplicates. A backend
        // blip (e.g. a deploy/restart window) makes every in-flight request
        // fail at once, firing many identical 5xx toasts — Oprah bug
        // 2712d5ad: "at least 7 HTTP 502 toasts stacking up". One alert with
        // a ×N counter is clearer for the user and avoids 7 repeated
        // screen-reader announcements on the aria-live region.
        const i = prev.findIndex(
          (x) => x.type === t.type && x.message === t.message && x.description === t.description,
        )
        if (i !== -1) {
          coalesced = true
          const next = prev.slice()
          next[i] = { ...next[i], count: (next[i].count || 1) + 1 }
          return next
        }
        return [...prev, t]
      })
      // Only the first occurrence schedules removal; coalesced duplicates ride
      // the original's timer (its id never entered `items`, so this is a no-op).
      if (!coalesced && t.duration && t.duration > 0) {
        setTimeout(() => {
          setItems((prev) => prev.filter((x) => x.id !== t.id))
        }, t.duration)
      }
    }
    listeners.push(handler)
    return () => {
      listeners = listeners.filter((fn) => fn !== handler)
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      style={{
        position: 'fixed', bottom: 20, right: 20, zIndex: 9999,
        display: 'flex', flexDirection: 'column', gap: 10,
        maxWidth: 'min(420px, calc(100vw - 40px))',
      }}
    >
      {items.map((t) => {
        const c = COLORS[t.type || 'info']
        return (
          <div
            key={t.id}
            role={t.type === 'error' ? 'alert' : 'status'}
            style={{
              background: c.bg,
              color: c.text,
              border: `1px solid ${c.border}`,
              borderRadius: 10,
              padding: '12px 14px',
              boxShadow: '0 8px 24px rgba(13,27,42,0.18)',
              fontSize: 14,
              lineHeight: 1.4,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <strong style={{ flex: 1 }}>
                {t.message}{t.count && t.count > 1 ? ` ×${t.count}` : ''}
              </strong>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}
                style={{
                  background: 'transparent', border: 'none', color: c.text,
                  fontSize: 18, lineHeight: 1, cursor: 'pointer', padding: 0,
                }}
              >×</button>
            </div>
            {t.description && (
              <div style={{ opacity: 0.85, marginTop: 4, fontSize: 13 }}>{t.description}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
