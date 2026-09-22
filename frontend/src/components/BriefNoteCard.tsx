'use client'

// The morning-brief note composer (CFO 2026-09-10). One short note per person
// per morning, written here on the dashboard and carried into the CEO / CFO
// brief. The space itself says whether this person may write or read, so the
// card self-gates: no role check at the call site, and nothing renders at all
// for someone who has neither. Renders null while loading and on any error —
// a broken shell on the dashboard reads as an outage.

import { useEffect, useId, useState } from 'react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card'
import { Textarea } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getBriefNoteSpace,
  writeBriefNote,
  withdrawBriefNote,
  type BriefNoteSpace,
} from '@/lib/api'

type Audience = 'ceo' | 'cfo'

const COPY: Record<Audience, { title: string; description: string }> = {
  ceo: {
    title: 'Note to the CEO',
    description: "One short note per morning — it lands in Arun's 06:30 brief.",
  },
  cfo: {
    title: 'Note to the CFO',
    description: "One short note per morning — it lands in Prathap's morning brief.",
  },
}

// "Thursday, 11 September" — the morning this note will actually appear in.
// Parsed at midnight local so a plain yyyy-mm-dd never slides a day on TZ.
function briefDayLabel(ymd: string): string {
  const d = new Date(`${ymd}T00:00:00`)
  if (Number.isNaN(d.getTime())) return ymd
  const weekday = d.toLocaleDateString('en-GB', { weekday: 'long' })
  const month = d.toLocaleDateString('en-GB', { month: 'long' })
  return `${weekday}, ${d.getDate()} ${month}`
}

export function BriefNoteCard({ audience }: { audience: Audience }) {
  const { theme } = useTheme()
  const fieldId = useId()

  const [space, setSpace] = useState<BriefNoteSpace | null>(null)
  const [loading, setLoading] = useState(true)
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    getBriefNoteSpace(audience)
      .then((s) => {
        if (!alive) return
        setSpace(s)
        setBody(s.my_note?.body ?? '')
      })
      .catch(() => { if (alive) setSpace(null) /* silent — the card stays hidden */ })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [audience])

  async function refresh() {
    try {
      const s = await getBriefNoteSpace(audience)
      setSpace(s)
      setBody(s.my_note?.body ?? '')
    } catch {
      // Keep what is on screen — the write already succeeded.
    }
  }

  async function handleSend() {
    setBusy(true)
    setError('')
    try {
      await writeBriefNote(audience, body.trim())
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That note did not save.')
    } finally {
      setBusy(false)
    }
  }

  async function handleWithdraw() {
    const noteId = space?.my_note?.id
    if (!noteId) return
    setBusy(true)
    setError('')
    try {
      await withdrawBriefNote(noteId)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That note could not be withdrawn.')
    } finally {
      setBusy(false)
    }
  }

  if (loading || !space) return null

  const notes = space.notes ?? []
  // Nothing to write and nothing to read — show no shell at all.
  if (!space.can_post && notes.length === 0) return null

  const copy = COPY[audience]
  const mine = space.my_note
  const limit = space.word_limit
  // Counted at render, never stored: a word counter kept in state via an effect
  // lags one keystroke behind what the person can see.
  const words = body.trim() ? body.trim().split(/\s+/).length : 0
  const over = words > limit
  const showForm = space.can_post && !mine?.locked

  return (
    <Card>
      <CardHeader>
        <CardTitle>{copy.title}</CardTitle>
        <CardDescription>{copy.description}</CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        <p className="text-xs" style={{ color: theme.t3 }}>
          Goes into the brief on {briefDayLabel(space.for_date)}
        </p>

        {mine?.locked && (
          <div className="space-y-2">
            <blockquote
              className="text-sm whitespace-pre-wrap"
              style={{ color: theme.navy, borderLeft: `2px solid ${theme.orange}`, paddingLeft: 12 }}
            >
              {mine.body}
            </blockquote>
            <p className="text-xs" style={{ color: theme.t3 }}>
              This note has already gone out in this morning&apos;s brief, so it can no longer be changed.
            </p>
          </div>
        )}

        {showForm && (
          <div>
            <label
              htmlFor={fieldId}
              className="block text-xs font-medium mb-1.5"
              style={{ color: theme.t2 }}
            >
              {mine ? 'Your note for this morning' : 'Your note'}
            </label>
            <Textarea
              id={fieldId}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={busy}
              placeholder="One thing worth their attention."
              className="min-h-[88px]"
            />
            <div className="flex items-center justify-between mt-1.5">
              <span
                aria-live="polite"
                className="text-xs font-mono-nums"
                style={{ color: over ? theme.er : theme.t3 }}
              >
                {words} / {limit} words
              </span>
              {over && (
                <span className="text-xs" style={{ color: theme.er }}>
                  Trim it to {limit} words
                </span>
              )}
            </div>
          </div>
        )}

        {error && (
          <p className="text-sm" style={{ color: theme.er }} role="alert">
            {error}
          </p>
        )}

        {/* `shared` = the seven see each other (CEO space). `can_read_all` =
            you own this space, which is how the CFO sees staff messages. Keying
            on `shared` alone hid every note from him. */}
        {(space.shared || space.can_read_all) && notes.length > 0 && (
          <div className="space-y-3 pt-3" style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
            <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>
              In this morning&apos;s brief
            </p>
            {notes.map((note) => (
              <div key={note.id} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold" style={{ color: theme.navy }}>
                    {note.author}
                  </span>
                  {note.is_mine && <Badge variant="orange">You</Badge>}
                </div>
                <blockquote
                  className="text-sm whitespace-pre-wrap"
                  style={{
                    color: theme.t2,
                    borderLeft: `2px solid ${note.is_mine ? theme.orange : theme.cardBdr}`,
                    paddingLeft: 12,
                  }}
                >
                  {note.body}
                </blockquote>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      {showForm && (
        <CardFooter className="flex items-center justify-end gap-2">
          {mine && (
            <Button variant="ghost" size="sm" onClick={handleWithdraw} disabled={busy}>
              Withdraw
            </Button>
          )}
          <Button
            variant="accent"
            size="sm"
            onClick={handleSend}
            loading={busy}
            disabled={words === 0 || over}
          >
            {mine ? 'Update' : 'Send to the brief'}
          </Button>
        </CardFooter>
      )}
    </Card>
  )
}
