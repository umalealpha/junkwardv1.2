'use client'

/**
 * QaReadOnlyBanner — the strip that says "this session cannot change anything".
 *
 * CFO 2026-07-29. Shows only in the read-only quality-check view opened from
 * /qa. Deliberately IN the page flow (like BirthdayBanner) rather than pinned
 * over it — a fixed overlay is exactly the bug class the eyes-on gate keeps
 * catching, and a QA tool must not itself hide the screen being checked.
 */

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Eye } from 'lucide-react'
import { removeToken } from '@/lib/api'
import { isQaReadOnly, clearQaReadOnly } from '@/lib/qaView'

export function QaReadOnlyBanner() {
  const router = useRouter()
  const [on, setOn] = useState(false)

  // localStorage is only readable after mount — checking during render would
  // mismatch the server-rendered markup.
  useEffect(() => { setOn(isQaReadOnly()) }, [])

  if (!on) return null

  function leave() {
    clearQaReadOnly()
    try { removeToken() } catch {}
    router.replace('/qa')
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      padding: '7px 16px', background: '#F4A623', color: '#0D1B2A',
      fontSize: 13, fontWeight: 600,
    }}>
      <Eye size={15} strokeWidth={2.5} />
      <span>Quality check — read only. Nothing you click can change data.</span>
      <button onClick={leave} style={{
        marginLeft: 'auto', padding: '3px 11px', borderRadius: 7,
        border: '1px solid #0D1B2A', background: 'transparent',
        color: '#0D1B2A', fontSize: 12, fontWeight: 700, cursor: 'pointer',
      }}>
        Leave
      </button>
    </div>
  )
}
