'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getMe } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import {
  BoardGrid,
  BoardToolbar,
  BookSheet,
  MineSheet,
  ROOMS_CSS,
} from './_board'
import {
  createBooking,
  deleteBooking,
  getBookings,
  getMyBookings,
  getRooms,
  type Booking,
  type Room,
} from './_api'
import { addDays, dayKey, minutesNow } from './_time'

export default function RoomsPage() {
  const router = useRouter()
  const [date, setDate] = useState(() => new Date())
  const [rooms, setRooms] = useState<Room[]>([])
  const [dayBookings, setDayBookings] = useState<Booking[]>([])
  const [myBookings, setMyBookings] = useState<Booking[]>([])
  const [draft, setDraft] = useState<{ roomId: string; start: number } | null>(null)
  const [showMine, setShowMine] = useState(false)
  const [bookError, setBookError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [booking, setBooking] = useState(false)
  const [nowMin, setNowMin] = useState(minutesNow)
  const [meName, setMeName] = useState('You')

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    getRooms()
      .then((r) => setRooms(r.results))
      .catch((e) => setLoadError(e instanceof Error ? e.message : 'Could not load rooms'))
    getMe()
      .then((p) => setMeName([p.first_name, p.last_name].filter(Boolean).join(' ') || 'You'))
      .catch(() => {})
    loadMine()
    const t = setInterval(() => setNowMin(minutesNow()), 60_000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const key = dayKey(date)
  useEffect(() => {
    getBookings(key)
      .then((r) => setDayBookings(r.results))
      .catch((e) => setLoadError(e instanceof Error ? e.message : 'Could not load bookings'))
  }, [key])

  const loadMine = () => {
    getMyBookings()
      .then((r) => setMyBookings(r.results))
      .catch(() => {})
  }

  const draftRoom = useMemo(
    () => (draft ? rooms.find((r) => r.id === draft.roomId) : undefined),
    [draft, rooms],
  )

  const openSlot = (roomId: string, start: number) => {
    setBookError(null)
    setDraft({ roomId, start })
  }

  const confirm = async (v: { title: string; attendees: number; duration: number }) => {
    if (!draft) return
    setBooking(true)
    setBookError(null)
    try {
      await createBooking({
        room: draft.roomId,
        day: key,
        start_min: draft.start,
        end_min: draft.start + v.duration,
        title: v.title,
        attendees: v.attendees,
      })
      setDraft(null)
      const r = await getBookings(key)
      setDayBookings(r.results)
      loadMine()
    } catch (e) {
      setBookError(e instanceof Error ? e.message : 'Could not book the room')
    } finally {
      setBooking(false)
    }
  }

  const cancel = async (id: string) => {
    try {
      await deleteBooking(id)
      const r = await getBookings(key)
      setDayBookings(r.results)
      loadMine()
    } catch {
      /* surfaced on next load */
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Alpha Rooms" breadcrumbs={[{ label: 'Operations' }, { label: 'Alpha Rooms' }]} />

      {/* Page hero — matches the rebuilt Omni screens (2026-09-02). */}
      <div style={{ padding: '20px 24px 4px', background: '#F7F8FA' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10.5, fontWeight: 650, letterSpacing: '0.11em', textTransform: 'uppercase', color: '#3F58CC' }}>
          <span style={{ width: 5, height: 5, borderRadius: 99, background: '#4F6BED' }} />
          Alpha Rooms
        </div>
        <h1 style={{ margin: '6px 0 0', fontSize: 25, lineHeight: 1.15, letterSpacing: '-0.03em', fontWeight: 680, color: '#1A1D21' }}>Book a room</h1>
        <p style={{ margin: '6px 0 0', fontSize: 14, color: '#6B7280' }}>Five rooms, one day at a time. Green means free right now.</p>
      </div>

      <div className="alpha-rooms">
        <style dangerouslySetInnerHTML={{ __html: ROOMS_CSS }} />

        <BoardToolbar
          date={date}
          onShift={(n) => setDate((d) => addDays(d, n))}
          onToday={() => setDate(new Date())}
          onOpenMine={() => {
            loadMine()
            setShowMine(true)
          }}
          mineCount={myBookings.length}
        />

        {loadError ? (
          <p className="ar-loading">{loadError}</p>
        ) : rooms.length === 0 ? (
          <p className="ar-loading">Loading rooms…</p>
        ) : (
          <BoardGrid
            rooms={rooms}
            date={date}
            bookings={dayBookings}
            nowMin={nowMin}
            onPickSlot={openSlot}
            onPickBooking={() => {
              loadMine()
              setShowMine(true)
            }}
          />
        )}

        {draft && draftRoom && (
          <BookSheet
            room={draftRoom}
            start={draft.start}
            date={date}
            meName={meName}
            error={bookError}
            busy={booking}
            onCancel={() => setDraft(null)}
            onConfirm={confirm}
          />
        )}

        {showMine && (
          <MineSheet
            bookings={myBookings}
            onCancelBooking={cancel}
            onClose={() => setShowMine(false)}
          />
        )}
      </div>
    </div>
  )
}
