'use client'

import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { apiFetch } from '@/lib/api'

interface InsightResponse {
  ctx: string
  insight: string
  cached?: boolean
  fallback?: boolean
}

/**
 * Pill-shaped ARIA insight ribbon (Manus plan §5.1).
 *
 * ARIA is Alpha Direct's in-house AI assistant — branded externally
 * as "ARIA, produced locally". Underlying model providers are an
 * internal implementation detail and must not appear in user-facing
 * strings.
 *
 * One short, contextual nudge per context, cached server-side for
 * 6 hours per (ctx, day). Falls back to a static line on transient
 * errors so the ribbon never breaks the layout.
 */
export function AIInsightRibbon({ ctx }: { ctx: 'dashboard' | 'cfo' | 'hris' | 'po' }) {
  const [insight, setInsight] = useState<string>('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    apiFetch<InsightResponse>(`/ai/insight/?ctx=${ctx}`)
      .then(r => { if (!cancelled) setInsight(r.insight) })
      .catch(() => {
        if (!cancelled) setInsight('Sharp move — every minute on the dashboard is a minute closer to month-end.')
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [ctx])

  if (loading) {
    return (
      <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full"
           style={{
             background: 'rgba(0,201,183,0.08)',
             border: '1px solid rgba(0,201,183,0.25)',
           }}>
        <span className="w-3.5 h-3.5 rounded-full animate-pulse"
              style={{ background: 'rgba(0,201,183,0.5)' }} />
        <span className="text-xs italic" style={{ color: '#047C72' }}>thinking…</span>
      </div>
    )
  }

  return (
    <div
      className="inline-flex items-start gap-2.5 px-4 py-2 rounded-full max-w-3xl"
      style={{
        background: 'linear-gradient(135deg, rgba(0,201,183,0.10) 0%, rgba(0,201,183,0.04) 100%)',
        border: '1px solid rgba(0,201,183,0.30)',
        boxShadow: '0 0 0 4px rgba(0,201,183,0.04)',
      }}
    >
      <Sparkles
        className="w-4 h-4 flex-shrink-0 mt-0.5"
        style={{ color: '#00C9B7' }}
        strokeWidth={2}
      />
      <span
        className="text-xs sm:text-[13px] leading-snug"
        style={{ color: '#047C72', fontWeight: 500 }}
      >
        <span className="font-semibold uppercase tracking-[0.14em] text-[10px] mr-2"
              style={{ color: '#00897B' }}>ARIA</span>
        {insight}
      </span>
    </div>
  )
}
