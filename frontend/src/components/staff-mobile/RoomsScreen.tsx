'use client'

/** /app/rooms · /m/staff/rooms — Alpha Rooms on the phone (CFO pick 2026-09-03).
 * Drives the SAME endpoints as the desktop /rooms board (boardroom/api_views.py):
 *   GET    /api/v1/boardroom-rooms/                          active rooms
 *   GET    /api/v1/boardroom-bookings/?day=YYYY-MM-DD        one day's board
 *   GET    /api/v1/boardroom-bookings/?mine=1                my bookings, today onward
 *   POST   /api/v1/boardroom-bookings/  { room, day, start_min, end_min, title, attendees }
 *   DELETE /api/v1/boardroom-bookings/<id>/                  own bookings only (server-enforced)
 * Clash / time-window errors are the server's words, shown verbatim. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CalendarDays, Users } from 'lucide-react'
import { reauthOn401, sfetch } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch,
} from './StaffFormKit'

interface Room { id: string; name: string; floor: string; seats: number | null; kit: string; is_prominent: boolean; is_active: boolean }
interface Booking {
  id: string; room: string; room_name: string; day: string; start_min: number; end_min: number
  title: string; attendees: number; booked_by_name: string; is_mine: boolean
}
type Page<T> = { results: T[] } | T[]
const rows = <T,>(p: Page<T>): T[] => Array.isArray(p) ? p : (p.results || [])

// Same window as boardroom/models.py (07:00–19:00, 30-minute slots).
const DAY_START_MIN = 7 * 60, DAY_END_MIN = 19 * 60, SLOT_MIN = 30
const SLOTS: number[] = []
for (let m = DAY_START_MIN; m <= DAY_END_MIN; m += SLOT_MIN) SLOTS.push(m)
const hhmm = (min: number) => `${`${Math.floor(min / 60)}`.padStart(2, '0')}:${`${min % 60}`.padStart(2, '0')}`
const dayKey = (d: Date) => `${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, '0')}-${`${d.getDate()}`.padStart(2, '0')}`
const addDays = (d: Date, n: number) => { const o = new Date(d); o.setDate(o.getDate() + n); return o }
const longDate = (k: string) => new Date(`${k}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })

export default function RoomsScreen() {
  const base = useStaffBase()
  const today = useMemo(() => dayKey(new Date()), [])
  const days = useMemo(() => { const t = new Date(); return Array.from({ length: 29 }, (_, i) => addDays(t, i - 14)) }, [])
  const [day, setDay] = useState(today)
  const [rooms, setRooms] = useState<Room[] | null>(null)
  const [bookings, setBookings] = useState<Booking[]>([])
  const [mine, setMine] = useState<Booking[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [sheet, setSheet] = useState<Room | null>(null)
  const [start, setStart] = useState(9 * 60)
  const [end, setEnd] = useState(10 * 60)
  const [title, setTitle] = useState('')
  const [attendees, setAttendees] = useState('1')
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  // The rail runs today −14 → +14; open it with today centred, not day −14.
  const rail = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = rail.current; const on = el?.querySelector<HTMLElement>('[aria-checked="true"]')
    if (el && on) el.scrollLeft = on.offsetLeft - el.clientWidth / 2 + on.offsetWidth / 2
  }, [])

  const load = useCallback(() => {
    setLoadErr(null)
    Promise.all([
      sfetch<Page<Room>>('/boardroom-rooms/'),
      sfetch<Page<Booking>>(`/boardroom-bookings/?day=${encodeURIComponent(day)}&page_size=100`),
      sfetch<Page<Booking>>('/boardroom-bookings/?mine=1&page_size=100'),
    ]).then(([r, b, m]) => {
      setRooms(rows(r).filter(x => x.is_active !== false)); setBookings(rows(b)); setMine(rows(m))
    }).catch(e => { if (!reauthOn401(e)) { setRooms(r => r ?? []); setLoadErr(errText(e, 'Could not load the rooms.')) } })
  }, [day])
  useEffect(() => { load() }, [load])

  function openSheet(room: Room) {
    setServerErr(null); setTitle(''); setAttendees('1'); setStart(9 * 60); setEnd(10 * 60); setSheet(room)
  }

  async function book() {
    if (!sheet) return
    setServerErr(null)
    if (!title.trim()) { show('Give the meeting a name.'); return }
    setBusy(true)
    try {
      // Same body the desktop board sends (rooms/_api.ts NewBooking).
      const r = await rawStaffFetch('/boardroom-bookings/', { method: 'POST', body: JSON.stringify({
        room: sheet.id, day, start_min: start, end_min: end, title: title.trim(), attendees: Number(attendees) || 1,
      }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      show(`Booked ${sheet.name} · ${hhmm(start)}–${hhmm(end)}. ✅`); setSheet(null); load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not book.')) }
    finally { setBusy(false) }
  }

  async function cancel(b: Booking) {
    if (!window.confirm(`Cancel "${b.title}" in ${b.room_name} on ${b.day}?`)) return
    setBusy(true)
    try {
      const r = await rawStaffFetch(`/boardroom-bookings/${b.id}/`, { method: 'DELETE' })
      if (!r.ok) { show(formatServerErrors(r.body, r.status)); return }
      show('Booking cancelled.'); load()
    } catch (e) { if (!reauthOn401(e)) show(errText(e, 'Could not cancel.')) }
    finally { setBusy(false) }
  }

  const forRoom = (id: string) => bookings.filter(b => b.room === id).sort((a, b) => a.start_min - b.start_min)
  const endOptions = SLOTS.filter(m => m > start)

  return (
    <ScreenFrame title="Rooms" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}

      {/* Day picker — today ±14, horizontal scroll */}
      <div>
        <p style={{ margin: '0 0 8px', fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Pick a day</p>
        <div ref={rail} role="radiogroup" aria-label="Day" style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4, scrollbarWidth: 'none', position: 'relative' }}>
          {days.map(d => {
            const k = dayKey(d); const on = k === day
            return (
              <button key={k} role="radio" aria-checked={on} aria-label={longDate(k)} onClick={() => setDay(k)}
                style={{ flex: '0 0 auto', minWidth: 52, minHeight: 56, borderRadius: 14, border: on ? 'none' : `1px solid ${C.line}`,
                  background: on ? C.navy : '#fff', color: on ? '#fff' : C.ink, cursor: 'pointer', display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center', gap: 2, padding: '6px 8px' }}>
                <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', opacity: 0.85 }}>{k === today ? 'Today' : d.toLocaleDateString(undefined, { weekday: 'short' })}</span>
                <span style={{ fontSize: 16, fontWeight: 800 }}>{d.getDate()}</span>
              </button>
            )
          })}
        </div>
        <p style={{ margin: '8px 0 0', fontSize: 13, color: C.ink, display: 'flex', alignItems: 'center', gap: 6 }}><CalendarDays size={15} aria-hidden="true" /> {longDate(day)}</p>
      </div>

      {rooms === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {rooms && rooms.length === 0 && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No rooms are set up yet.</p>}
      {rooms?.map(room => {
        const bs = forRoom(room.id)
        return (
          <Card key={room.id} style={{ padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
              <div>
                <b style={{ color: C.ink, fontSize: 15 }}>{room.name}</b>
                <p style={{ margin: '2px 0 0', fontSize: 12.5, color: C.inkSoft }}>
                  {[room.floor, room.seats != null ? `${room.seats} seats` : '', room.kit].filter(Boolean).join(' · ')}
                </p>
              </div>
              <button onClick={() => openSheet(room)} style={{ ...ghostBtn, background: C.orange, border: 'none', color: '#0D1B2A', flexShrink: 0 }}>Book</button>
            </div>
            <ul aria-label={`Bookings in ${room.name}`} style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {bs.length === 0 && <li style={{ fontSize: 12.5, color: '#065F46', background: '#ECFDF5', borderRadius: 10, padding: '7px 10px' }}>Free all day</li>}
              {bs.map(b => (
                <li key={b.id} style={{ fontSize: 12.5, color: C.ink, background: b.is_mine ? '#FFF7ED' : '#F6F7F9', borderRadius: 10, padding: '7px 10px', display: 'flex', gap: 8 }}>
                  <b style={{ whiteSpace: 'nowrap' }}>{hhmm(b.start_min)}–{hhmm(b.end_min)}</b>
                  <span style={{ flex: 1 }}>{b.title} <span style={{ color: C.inkSoft }}>· {b.is_mine ? 'you' : b.booked_by_name}</span></span>
                </li>
              ))}
            </ul>
          </Card>
        )
      })}

      <b style={{ color: C.ink, fontSize: 15 }}>My bookings</b>
      {rooms && mine.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>You have no upcoming bookings.</p>}
      {mine.map(b => (
        <Card key={b.id} style={{ padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
            <div>
              <b style={{ color: C.ink, fontSize: 14.5 }}>{b.title}</b>
              <p style={{ margin: '2px 0 0', fontSize: 12.5, color: C.inkSoft }}>{b.room_name} · {longDate(b.day)} · {hhmm(b.start_min)}–{hhmm(b.end_min)}</p>
            </div>
            {b.is_mine && <button onClick={() => cancel(b)} disabled={busy} style={{ ...ghostBtn, color: '#991B1B', flexShrink: 0 }}>Cancel</button>}
          </div>
        </Card>
      ))}

      {/* Book sheet */}
      {sheet && (
        <div role="dialog" aria-modal="true" aria-labelledby="book-title" style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 65 }} onClick={() => !busy && setSheet(null)}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px calc(24px + env(safe-area-inset-bottom, 0px))' }}>
            <h2 id="book-title" style={{ fontFamily: serif, fontSize: 18, color: C.ink, margin: 0 }}>Book {sheet.name}</h2>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>{longDate(day)}</p>
            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <label htmlFor="book-start" style={labelStyle}>Start</label>
                <select id="book-start" value={start} onChange={e => { const v = Number(e.target.value); setStart(v); if (end <= v) setEnd(Math.min(v + SLOT_MIN, DAY_END_MIN)) }} style={inputStyle}>
                  {SLOTS.filter(m => m < DAY_END_MIN).map(m => <option key={m} value={m}>{hhmm(m)}</option>)}
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label htmlFor="book-end" style={labelStyle}>End</label>
                <select id="book-end" value={end} onChange={e => setEnd(Number(e.target.value))} style={inputStyle}>
                  {endOptions.map(m => <option key={m} value={m}>{hhmm(m)}</option>)}
                </select>
              </div>
            </div>
            <label htmlFor="book-name" style={labelStyle}>Meeting name</label>
            <input id="book-name" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Claims weekly" style={inputStyle} maxLength={200} />
            <label htmlFor="book-people" style={labelStyle}>People</label>
            <input id="book-people" value={attendees} onChange={e => setAttendees(e.target.value.replace(/[^0-9]/g, ''))} inputMode="numeric" style={{ ...inputStyle, width: 120 }} />
            {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
            <button onClick={book} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 14, color: '#0D1B2A' }}>
              <Users size={16} aria-hidden="true" /> {busy ? 'Booking…' : `Book ${hhmm(start)}–${hhmm(end)}`}
            </button>
            <button onClick={() => setSheet(null)} disabled={busy} style={{ width: '100%', marginTop: 8, minHeight: 44, borderRadius: 999, border: 'none', background: 'none', color: C.inkSoft, fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>Cancel</button>
          </div>
        </div>
      )}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
