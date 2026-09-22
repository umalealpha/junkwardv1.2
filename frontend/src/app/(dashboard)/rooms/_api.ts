// Alpha Rooms API client — thin wrappers over the shared apiFetch.
import { apiFetch, type PaginatedResponse } from '@/lib/api'

export interface Room {
  id: string
  name: string
  floor: string
  seats: number | null
  kit: string
  is_prominent: boolean
  is_active: boolean
}

export interface Booking {
  id: string
  room: string
  room_name: string
  day: string // YYYY-MM-DD
  start_min: number
  end_min: number
  title: string
  attendees: number
  booked_by: number | null
  booked_by_name: string
  is_mine: boolean
  created_at: string
}

export interface NewBooking {
  room: string
  day: string
  start_min: number
  end_min: number
  title: string
  attendees: number
}

export const getRooms = () => apiFetch<PaginatedResponse<Room>>('/boardroom-rooms/')

// page_size=100: a day board can hold well over the default 25 (6 rooms ×
// many slots) — without this, afternoon bookings silently fall off page 1.
export const getBookings = (day: string) =>
  apiFetch<PaginatedResponse<Booking>>(`/boardroom-bookings/?day=${encodeURIComponent(day)}&page_size=100`)

export const getMyBookings = () =>
  apiFetch<PaginatedResponse<Booking>>('/boardroom-bookings/?mine=1&page_size=100')

export const createBooking = (data: NewBooking) =>
  apiFetch<Booking>('/boardroom-bookings/', { method: 'POST', body: JSON.stringify(data) })

export const deleteBooking = (id: string) =>
  apiFetch<void>(`/boardroom-bookings/${id}/`, { method: 'DELETE' })
