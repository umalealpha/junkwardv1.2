'use client'

import { useEffect, useRef, useState } from 'react'
import { ModalPortal } from '@/components/ui/ModalPortal'
import type { Booking, Room } from './_api'
import {
  DAY_END_MIN,
  DAY_START_MIN,
  SLOT_COUNT,
  SLOT_MIN,
  durationLabel,
  hhmm,
  isToday,
  longDate,
  shortDate,
  slotStart,
} from './_time'

const SLOT_H = 46 // must stay in step with --slot-h in ROOMS_CSS

function offsetPx(min: number): number {
  return ((min - DAY_START_MIN) / SLOT_MIN) * SLOT_H
}

function roomMeta(room: Room): string {
  const bits: string[] = []
  if (room.seats) bits.push(`${room.seats} seats`)
  if (room.floor) bits.push(room.floor)
  return bits.join(' · ')
}

// ── Toolbar: day navigation + my bookings ─────────────────────────────────
export function BoardToolbar({
  date,
  onShift,
  onToday,
  onOpenMine,
  mineCount,
}: {
  date: Date
  onShift: (days: number) => void
  onToday: () => void
  onOpenMine: () => void
  mineCount: number
}) {
  return (
    <div className="ar-bar">
      <nav className="daynav" aria-label="Change day">
        <button className="daynav__btn" onClick={() => onShift(-1)} aria-label="Previous day">
          ‹
        </button>
        <div className="daynav__label" aria-live="polite">
          <b>{isToday(date) ? 'Today' : shortDate(date)}</b>
          <span>{longDate(date)}</span>
        </div>
        <button className="daynav__btn" onClick={() => onShift(1)} aria-label="Next day">
          ›
        </button>
      </nav>
      <div className="ar-bar__spacer" />
      {!isToday(date) && (
        <button className="btn btn--ghost" onClick={onToday}>
          Jump to today
        </button>
      )}
      <button className="btn btn--ghost" onClick={onOpenMine}>
        My bookings{mineCount > 0 ? ` · ${mineCount}` : ''}
      </button>
    </div>
  )
}

// ── The board grid ────────────────────────────────────────────────────────
export function BoardGrid({
  rooms,
  date,
  bookings,
  nowMin,
  onPickSlot,
  onPickBooking,
}: {
  rooms: Room[]
  date: Date
  bookings: Booking[]
  nowMin: number
  onPickSlot: (roomId: string, startMin: number) => void
  onPickBooking: (b: Booking) => void
}) {
  const today = isToday(date)
  const live = today ? nowMin : -1
  const boardRef = useRef<HTMLDivElement>(null)
  const scrolled = useRef(false)

  useEffect(() => {
    if (scrolled.current || !today || !boardRef.current) return
    scrolled.current = true
    boardRef.current.scrollTop = Math.max(0, offsetPx(nowMin - 60))
  }, [today, nowMin])

  return (
    <div className="board" ref={boardRef}>
      <div className="grid" style={{ ['--cols' as string]: String(rooms.length) }}>
        <div className="grid__corner" />
        {rooms.map((room) => {
          const current = bookings.find(
            (b) => b.room === room.id && b.start_min <= live && b.end_min > live,
          )
          return (
            <div className="grid__head" key={room.id}>
              <div className="roomhead">
                <div className="roomhead__name">{room.name}</div>
                <div className="roomhead__meta">
                  {today ? (
                    current ? (
                      <span className="pill pill--busy">
                        <span className="pill__dot" />
                        Busy till {hhmm(current.end_min)}
                      </span>
                    ) : (
                      <span className="pill pill--free">
                        <span className="pill__dot" />
                        Free now
                      </span>
                    )
                  ) : null}
                  {roomMeta(room) && <span>{roomMeta(room)}</span>}
                </div>
              </div>
            </div>
          )
        })}

        {Array.from({ length: SLOT_COUNT }, (_, i) => {
          const min = slotStart(i)
          const onHour = min % 60 === 0
          return (
            <div
              className={`timecell${onHour ? '' : ' timecell--half'}`}
              key={`t${i}`}
              style={{ gridRow: i + 2, gridColumn: 1 }}
            >
              {hhmm(min)}
            </div>
          )
        })}

        {rooms.map((room, col) => (
          <div
            className="slotcol"
            key={`c${room.id}`}
            style={{ gridColumn: col + 2, gridRow: `2 / span ${SLOT_COUNT}`, position: 'relative' }}
          >
            {Array.from({ length: SLOT_COUNT }, (_, i) => {
              const min = slotStart(i)
              const past = today && min + SLOT_MIN <= live
              return (
                <button
                  key={i}
                  className={`slot${min % 60 === 0 ? ' slot--hour' : ''}${past ? ' slot--past' : ''}`}
                  disabled={past}
                  onClick={() => onPickSlot(room.id, min)}
                  aria-label={`Book ${room.name} at ${hhmm(min)}`}
                />
              )
            })}

            {bookings
              .filter((b) => b.room === room.id)
              .map((b) => (
                <button
                  key={b.id}
                  className={`booking${b.is_mine ? ' booking--mine' : ''}`}
                  style={{ top: offsetPx(b.start_min), height: offsetPx(b.end_min) - offsetPx(b.start_min) - 4 }}
                  onClick={() => onPickBooking(b)}
                >
                  <span className="booking__title">{b.title}</span>
                  <span className="booking__meta">
                    {hhmm(b.start_min)}–{hhmm(b.end_min)} · {durationLabel(b.end_min - b.start_min)}
                  </span>
                  <span className="booking__meta">
                    {b.booked_by_name || 'Someone'} · {b.attendees} people
                  </span>
                </button>
              ))}

            {live >= DAY_START_MIN && live <= slotStart(SLOT_COUNT) && (
              <div className="nowline" style={{ top: offsetPx(live) }} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Book sheet ──────────────────────────────────────────────────────────────
const DURATIONS = [30, 60, 90, 120]

export function BookSheet({
  room,
  start,
  date,
  meName,
  error,
  busy,
  onCancel,
  onConfirm,
}: {
  room: Room
  start: number
  date: Date
  meName: string
  error: string | null
  busy: boolean
  onCancel: () => void
  onConfirm: (v: { title: string; attendees: number; duration: number }) => void
}) {
  const [title, setTitle] = useState('')
  const [attendees, setAttendees] = useState(4)
  const [duration, setDuration] = useState(60)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const end = start + duration
  const tooLong = end > DAY_END_MIN
  const ready = title.trim().length > 0 && !tooLong && !busy

  return (
    <ModalPortal>
      <div className="alpha-rooms" style={{ display: 'contents' }}>
      <div className="scrim" onClick={onCancel} />
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="ar-book-title">
        <div className="sheet__grip" />
        <h2 className="sheet__title" id="ar-book-title">
          {room.name}
        </h2>
        <p className="sheet__sub">
          {longDate(date)} · {hhmm(start)}–{hhmm(end)} · {durationLabel(duration)}
          {room.seats ? ` · ${room.seats} seats` : ''}
        </p>

        {error && <div className="alert">{error}</div>}
        {tooLong && (
          <div className="alert">
            {durationLabel(duration)} runs past {hhmm(DAY_END_MIN)}. Pick a shorter slot.
          </div>
        )}

        <label className="field">
          <span className="field__label">What is the meeting</span>
          <input
            className="field__input"
            value={title}
            autoFocus
            placeholder="e.g. Broker meeting — Hollard"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <div className="row">
          <div className="field">
            <span className="field__label">Booked by</span>
            <div className="field__static">{meName}</div>
          </div>
          <label className="field">
            <span className="field__label">People</span>
            <input
              className="field__input"
              type="number"
              min={1}
              value={attendees}
              onChange={(e) => setAttendees(Number(e.target.value) || 1)}
            />
          </label>
        </div>

        <div className="field">
          <span className="field__label">How long</span>
          <div className="chips">
            {DURATIONS.map((d) => (
              <button key={d} className="chip" aria-pressed={duration === d} onClick={() => setDuration(d)}>
                {durationLabel(d)}
              </button>
            ))}
          </div>
        </div>

        <div className="sheet__actions">
          <button
            className="btn btn--primary btn--block"
            disabled={!ready}
            onClick={() => onConfirm({ title: title.trim(), attendees, duration })}
          >
            {busy ? 'Booking…' : 'Book the room'}
          </button>
          <button className="btn btn--ghost btn--block" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </section>
      </div>
    </ModalPortal>
  )
}

// ── My bookings sheet ─────────────────────────────────────────────────────
export function MineSheet({
  bookings,
  onCancelBooking,
  onClose,
}: {
  bookings: Booking[]
  onCancelBooking: (id: string) => void
  onClose: () => void
}) {
  const mine = [...bookings].sort((a, b) =>
    `${a.day}${a.start_min}`.localeCompare(`${b.day}${b.start_min}`),
  )

  return (
    <ModalPortal>
      <div className="alpha-rooms" style={{ display: 'contents' }}>
      <div className="scrim" onClick={onClose} />
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="ar-mine-title">
        <div className="sheet__grip" />
        <h2 className="sheet__title" id="ar-mine-title">
          My bookings
        </h2>
        <p className="sheet__sub">Everything you booked, soonest first.</p>

        {mine.length === 0 ? (
          <p className="empty">Nothing booked under your name yet.</p>
        ) : (
          <div className="mine">
            {mine.map((b) => (
              <div className="mine__card" key={b.id}>
                <div className="mine__when">
                  {b.day.slice(5)}
                  <br />
                  {hhmm(b.start_min)}
                </div>
                <div className="mine__body">
                  <b>{b.title}</b>
                  <span>
                    {b.room_name} · {hhmm(b.start_min)}–{hhmm(b.end_min)} · {b.attendees} people
                  </span>
                </div>
                <button className="btn btn--danger" onClick={() => onCancelBooking(b.id)}>
                  Cancel
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="sheet__actions">
          <button className="btn btn--ghost btn--block" onClick={onClose}>
            Close
          </button>
        </div>
      </section>
      </div>
    </ModalPortal>
  )
}

// ── Scoped styles (Alpha Rooms look, ported from the standalone app) ────────
// Everything is scoped under .alpha-rooms so it cannot leak into Omni's own UI.
export const ROOMS_CSS = `
.alpha-rooms{
  --navy-900:#1A1D21;--navy-800:#2A2F36;--line:#ECEEF1;--orange:#4F6BED;--orange-soft:rgba(79,107,237,.14);
  --surface:#f7f9fc;--surface-raised:#ffffff;--surface-sunk:#eef2f7;
  --ink:#0d1b2a;--ink-dim:#44586d;--ink-faint:#7089a1;
  --free:#1E7A44;--free-soft:rgba(30,122,68,.12);--busy:#92400E;--busy-soft:rgba(146,64,14,.12);--mine:#4F6BED;--danger:#B4232D;
  --text-xs:.75rem;--text-sm:.875rem;--text-base:1rem;--text-lg:1.125rem;
  --text-xl:clamp(1.25rem,1rem + .6vw,1.5rem);--text-2xl:clamp(1.6rem,1.1rem + 1.4vw,2.25rem);
  --font-display:"Inter",-apple-system,"Segoe UI",Roboto,system-ui,sans-serif;
  --font-ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;
  --font-mono:ui-monospace,"SF Mono",Menlo,monospace;
  --sp-1:.25rem;--sp-2:.5rem;--sp-3:.75rem;--sp-4:1rem;--sp-5:1.5rem;--sp-6:2rem;--sp-7:3rem;
  --r-sm:8px;--r-md:14px;--r-lg:22px;--r-pill:999px;
  --shadow-lift:0 1px 2px rgba(13,27,42,.06),0 8px 24px rgba(13,27,42,.10);
  --shadow-sheet:0 -1px 0 rgba(13,27,42,.05),0 -24px 60px rgba(13,27,42,.18);
  --dur-fast:140ms;--dur:240ms;--dur-slow:420ms;--ease:cubic-bezier(.16,1,.3,1);
  --rail-w:168px;--slot-h:46px;
  display:flex;flex-direction:column;flex:1 1 auto;min-height:0;
  font-family:var(--font-ui);color:var(--ink);
  background:radial-gradient(120% 80% at 15% -10%,#eef2fc 0%,transparent 55%),radial-gradient(90% 70% at 100% 0%,#f2f5fd 0%,transparent 50%),var(--surface);
}
.alpha-rooms *,.alpha-rooms *::before,.alpha-rooms *::after{box-sizing:border-box;}
.alpha-rooms button{font:inherit;color:inherit;border:0;background:none;cursor:pointer;}
.alpha-rooms input{font:inherit;color:inherit;}
.alpha-rooms :focus-visible{outline:2px solid var(--orange);outline-offset:2px;border-radius:var(--r-sm);}
.alpha-rooms .ar-bar{display:flex;align-items:center;gap:var(--sp-4);padding:var(--sp-3) var(--sp-5);border-bottom:1px solid var(--line);}
.alpha-rooms .ar-bar__spacer{flex:1;}
.alpha-rooms .daynav{display:flex;align-items:center;gap:var(--sp-2);padding:var(--sp-1);background:var(--surface-raised);border:1px solid var(--line);border-radius:var(--r-pill);}
.alpha-rooms .daynav__btn{width:40px;height:40px;border-radius:var(--r-pill);display:grid;place-items:center;color:var(--ink-dim);transition:background var(--dur-fast) var(--ease),color var(--dur-fast);}
.alpha-rooms .daynav__btn:hover{background:rgba(13,27,42,.06);color:var(--ink);}
.alpha-rooms .daynav__btn:active{transform:scale(.94);}
.alpha-rooms .daynav__label{min-width:12ch;text-align:center;font-size:var(--text-sm);font-variant-numeric:tabular-nums;}
.alpha-rooms .daynav__label b{display:block;font-size:var(--text-base);font-weight:600;}
.alpha-rooms .daynav__label span{color:var(--ink-faint);font-size:var(--text-xs);}
.alpha-rooms .btn{display:inline-flex;align-items:center;gap:var(--sp-2);padding:var(--sp-3) var(--sp-5);border-radius:var(--r-pill);font-weight:600;font-size:var(--text-sm);transition:transform var(--dur-fast) var(--ease),background var(--dur-fast),box-shadow var(--dur-fast);}
.alpha-rooms .btn:active{transform:scale(.97);}
.alpha-rooms .btn--primary{background:var(--orange);color:#fff;box-shadow:0 6px 20px rgba(79,107,237,.28);}
.alpha-rooms .btn--primary:hover{background:#3F58CC;}
.alpha-rooms .btn--ghost{background:var(--surface-raised);color:var(--ink);border:1px solid var(--line);}
.alpha-rooms .btn--ghost:hover{background:rgba(13,27,42,.05);}
.alpha-rooms .btn--danger{background:rgba(248,113,113,.12);color:var(--danger);border:1px solid rgba(248,113,113,.35);}
.alpha-rooms .btn--block{width:100%;justify-content:center;padding:var(--sp-4);font-size:var(--text-base);}
.alpha-rooms .btn:disabled{opacity:.45;cursor:not-allowed;box-shadow:none;}
.alpha-rooms .board{overflow:auto;padding:0 var(--sp-5) var(--sp-7);flex:1;scrollbar-width:thin;}
.alpha-rooms .grid{display:grid;grid-template-columns:var(--rail-w) repeat(var(--cols),minmax(190px,1fr));min-width:min-content;}
.alpha-rooms .grid__corner,.alpha-rooms .grid__head{position:sticky;top:0;z-index:10;background:var(--surface-raised);padding:var(--sp-4) var(--sp-3) var(--sp-3);}
.alpha-rooms .grid__corner{z-index:12;left:0;}
.alpha-rooms .roomhead{display:flex;flex-direction:column;gap:var(--sp-2);border-bottom:1px solid var(--line);padding-bottom:var(--sp-3);}
.alpha-rooms .roomhead__name{font-family:var(--font-display);font-size:var(--text-lg);line-height:1.15;}
.alpha-rooms .roomhead__meta{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--text-xs);color:var(--ink-faint);}
.alpha-rooms .pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:var(--r-pill);font-size:var(--text-xs);font-weight:600;letter-spacing:.02em;white-space:nowrap;}
.alpha-rooms .pill--free{background:var(--free-soft);color:var(--free);}
.alpha-rooms .pill--busy{background:var(--busy-soft);color:var(--busy);}
.alpha-rooms .pill__dot{width:6px;height:6px;border-radius:50%;background:currentColor;}
.alpha-rooms .pill--free .pill__dot{animation:ar-pulse 2.4s var(--ease) infinite;}
@keyframes ar-pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.4;transform:scale(.7);}}
.alpha-rooms .timecell{position:sticky;left:0;z-index:8;height:var(--slot-h);padding-right:var(--sp-3);display:flex;align-items:flex-start;justify-content:flex-end;font-family:var(--font-mono);font-size:var(--text-xs);color:var(--ink-faint);background:var(--surface);transform:translateY(-.55em);}
.alpha-rooms .timecell--half{color:transparent;}
.alpha-rooms .slotcol{position:relative;border-left:1px solid var(--line);}
.alpha-rooms .slot{position:relative;display:block;width:100%;height:var(--slot-h);border-bottom:1px solid rgba(13,27,42,.08);transition:background var(--dur-fast);}
.alpha-rooms .slot--hour{border-bottom-color:var(--line);}
.alpha-rooms .slot:not(:disabled):hover::after{content:"+";position:absolute;inset:3px;display:grid;place-items:center;border-radius:var(--r-sm);background:var(--orange-soft);color:var(--orange);font-size:var(--text-lg);font-weight:600;}
.alpha-rooms .slot:disabled{cursor:default;}
.alpha-rooms .slot--past{background:repeating-linear-gradient(-45deg,transparent,transparent 6px,rgba(13,27,42,.035) 6px,rgba(13,27,42,.035) 12px);}
.alpha-rooms .booking{position:absolute;left:4px;right:4px;z-index:4;display:flex;flex-direction:column;gap:2px;padding:var(--sp-2) var(--sp-3);border-radius:var(--r-md);border-left:3px solid var(--busy);background:linear-gradient(135deg,rgba(180,83,9,.16),rgba(180,83,9,.05));text-align:left;overflow:hidden;box-shadow:var(--shadow-lift);transition:transform var(--dur-fast) var(--ease);}
.alpha-rooms .booking:hover{transform:translateY(-1px);}
.alpha-rooms .booking--mine{border-left-color:var(--mine);background:linear-gradient(135deg,rgba(96,165,250,.24),rgba(96,165,250,.08));}
.alpha-rooms .booking__title{font-weight:600;font-size:var(--text-sm);line-height:1.2;}
.alpha-rooms .booking__meta{font-size:var(--text-xs);color:var(--ink-dim);font-variant-numeric:tabular-nums;}
.alpha-rooms .nowline{position:absolute;left:0;right:0;height:0;border-top:2px solid var(--danger);z-index:6;pointer-events:none;}
.alpha-rooms .nowline::before{content:"";position:absolute;left:-5px;top:-5px;width:8px;height:8px;border-radius:50%;background:var(--danger);}
.alpha-rooms .scrim{position:fixed;inset:0;z-index:200;background:rgba(4,10,18,.62);backdrop-filter:blur(3px);animation:ar-fade var(--dur) var(--ease);}
@keyframes ar-fade{from{opacity:0;}}
.alpha-rooms .sheet{position:fixed;z-index:201;left:50%;bottom:0;width:min(560px,100%);transform:translateX(-50%);padding:var(--sp-5) var(--sp-5) max(var(--sp-5),env(safe-area-inset-bottom));border-radius:var(--r-lg) var(--r-lg) 0 0;border:1px solid var(--line);border-bottom:0;background:var(--surface-raised);box-shadow:var(--shadow-sheet);animation:ar-rise var(--dur-slow) var(--ease);max-height:92vh;overflow-y:auto;}
@keyframes ar-rise{from{transform:translate(-50%,18px);opacity:0;}}
.alpha-rooms .sheet__grip{width:44px;height:4px;border-radius:var(--r-pill);background:var(--line);margin:0 auto var(--sp-4);}
.alpha-rooms .sheet__title{font-family:var(--font-display);font-size:var(--text-2xl);margin:0 0 var(--sp-1);line-height:1.1;}
.alpha-rooms .sheet__sub{color:var(--ink-dim);font-size:var(--text-sm);margin:0 0 var(--sp-5);}
.alpha-rooms .field{display:block;margin-bottom:var(--sp-4);}
.alpha-rooms .field__label{display:block;font-size:var(--text-xs);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:var(--sp-2);}
.alpha-rooms .field__input{width:100%;padding:var(--sp-3) var(--sp-4);border-radius:var(--r-md);border:1px solid var(--line);background:var(--surface-sunk);transition:border-color var(--dur-fast),background var(--dur-fast);}
.alpha-rooms .field__input:focus{border-color:var(--orange);background:#ffffff;outline:none;}
.alpha-rooms .field__static{padding:var(--sp-3) var(--sp-4);border-radius:var(--r-md);border:1px solid var(--line);background:var(--surface-sunk);color:var(--ink-dim);}
.alpha-rooms .row{display:grid;grid-template-columns:1fr 1fr;gap:var(--sp-3);}
.alpha-rooms .chips{display:flex;flex-wrap:wrap;gap:var(--sp-2);}
.alpha-rooms .chip{padding:var(--sp-2) var(--sp-4);border-radius:var(--r-pill);border:1px solid var(--line);background:var(--surface-sunk);font-size:var(--text-sm);font-variant-numeric:tabular-nums;transition:all var(--dur-fast) var(--ease);}
.alpha-rooms .chip[aria-pressed="true"]{background:var(--orange);border-color:var(--orange);color:#fff;font-weight:600;}
.alpha-rooms .alert{padding:var(--sp-3) var(--sp-4);border-radius:var(--r-md);background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.32);color:#b42318;font-size:var(--text-sm);margin-bottom:var(--sp-4);}
.alpha-rooms .sheet__actions{display:grid;gap:var(--sp-3);margin-top:var(--sp-5);}
.alpha-rooms .mine{display:grid;gap:var(--sp-3);}
.alpha-rooms .mine__card{display:flex;align-items:center;gap:var(--sp-4);padding:var(--sp-4);border-radius:var(--r-md);border:1px solid var(--line);background:var(--surface-raised);}
.alpha-rooms .mine__when{font-family:var(--font-mono);font-size:var(--text-sm);color:var(--orange);white-space:nowrap;}
.alpha-rooms .mine__body{flex:1;min-width:0;}
.alpha-rooms .mine__body b{display:block;font-size:var(--text-sm);}
.alpha-rooms .mine__body span{font-size:var(--text-xs);color:var(--ink-faint);}
.alpha-rooms .empty{padding:var(--sp-6) var(--sp-4);text-align:center;color:var(--ink-faint);font-size:var(--text-sm);}
.alpha-rooms .ar-loading{padding:var(--sp-7);text-align:center;color:var(--ink-faint);}
@media (max-width:820px){
  .alpha-rooms{--rail-w:56px;}
  .alpha-rooms .board{padding-inline:var(--sp-3);}
}
`
