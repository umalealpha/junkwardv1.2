'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationAi } from '@/lib/api'
import { Sparkles, Gavel, AlertTriangle } from 'lucide-react'

interface JudgeVerdict {
  pick?: string
  why?: string
  verdict?: string
  challenge?: string
}

/** The judge field is a JSON string and may be empty or malformed — parse
 *  defensively, never let a bad string crash the section. */
function parseJudge(raw: string | undefined | null): JudgeVerdict | null {
  if (!raw || !raw.trim()) return null
  try {
    const obj = JSON.parse(raw)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj as JudgeVerdict
  } catch {
    /* malformed — treated as no verdict, not an error */
  }
  return null
}

export function MorningNote({ theme, ai }: { theme: Theme; ai: TransformationAi }) {
  const judge = parseJudge(ai.judge)
  const hasNarrative = !!ai.narrative?.trim()

  if (!hasNarrative && !judge && !ai.note) {
    return (
      <p className="text-sm" style={{ color: theme.t3 }}>
        No morning note yet — it appears after the first pulse run.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {hasNarrative && (
        <div className="flex gap-3">
          <Sparkles className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: theme.orange }} strokeWidth={1.7} />
          <div>
            <p className="text-sm leading-relaxed" style={{ color: theme.text }}>{ai.narrative}</p>
            {ai.source && (
              <p className="text-xs mt-1.5" style={{ color: theme.t3 }}>Source: {ai.source}</p>
            )}
          </div>
        </div>
      )}

      {judge && (judge.verdict || judge.why || judge.pick || judge.challenge) && (
        <div className="rounded-lg p-4" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-2 mb-2">
            <Gavel className="w-4 h-4" style={{ color: theme.navy }} strokeWidth={1.7} />
            <span className="text-sm font-semibold" style={{ color: theme.navy }}>Judge&apos;s verdict</span>
            {judge.verdict && (
              <span
                className="ml-auto text-xs font-medium px-2 py-0.5 rounded-full"
                style={{ background: theme.oL, color: theme.orangeText }}
              >
                {judge.verdict}
              </span>
            )}
          </div>
          {judge.pick && (
            <p className="text-sm mb-1" style={{ color: theme.text }}>
              <span className="font-medium">Pick: </span>{judge.pick}
            </p>
          )}
          {judge.why && (
            <p className="text-sm" style={{ color: theme.t2 }}>{judge.why}</p>
          )}
          {judge.challenge && (
            <p className="text-sm mt-2 italic" style={{ color: theme.t2 }}>
              Challenge: {judge.challenge}
            </p>
          )}
          {ai.judge_source && (
            <p className="text-xs mt-2" style={{ color: theme.t3 }}>Judge source: {ai.judge_source}</p>
          )}
        </div>
      )}

      {ai.note && (
        <div className="flex items-start gap-2 text-xs" style={{ color: theme.wr }}>
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" strokeWidth={1.7} />
          <span>{ai.note}</span>
        </div>
      )}
    </div>
  )
}
