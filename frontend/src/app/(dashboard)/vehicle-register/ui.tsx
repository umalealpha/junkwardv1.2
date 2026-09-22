'use client'
/** Shared tokens, types, api helpers and small UI primitives for the
 *  Vehicle Register (pool-car checkout / check-in). Brand: navy #0D1B2A,
 *  orange accent #F4A623, accessible orange text #B04E00. */
import React from 'react'
import { apiFetch } from '@/lib/api'

export const NAVY = '#0D1B2A'
export const ORANGE = '#F4A623'
export const ORANGE_TXT = '#B04E00'
export const MUT = '#6B7280'
export const HAIR = '#ECEEF2'
export const CANVAS = '#F7F8FB'
export const GREEN = '#1B7A3D'
export const RED = '#B42318'
export const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'

export const card: React.CSSProperties = {
  background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}`,
}
export const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

// ── api wrappers ────────────────────────────────────────────────────────────
export const jget = <T,>(p: string) => apiFetch<T>(p)
export const jpost = <T,>(p: string, body: unknown) =>
  apiFetch<T>(p, { method: 'POST', body: JSON.stringify(body) })
export const jpatch = <T,>(p: string, body: unknown) =>
  apiFetch<T>(p, { method: 'PATCH', body: JSON.stringify(body) })

// ── types ────────────────────────────────────────────────────────────────────
export type VehStatus = 'available' | 'out' | 'maintenance' | 'retired'
export type TripStatus = 'checked_out' | 'returned_pending' | 'closed'

export interface Trip {
  id: string; vehicle_id: string; registration: string
  driver_name: string; purpose: string; purpose_label: string; purpose_notes: string
  destination: string; checkout_at: string | null; odometer_out: number | null
  fuel_level_out: string; pre_trip_notes: string; expected_return_at: string | null
  checkin_at: string | null; odometer_in: number | null; fuel_level_in: string
  driver_condition_confirm: boolean; driver_return_confirm: boolean
  damage_on_return: boolean; damage_notes: string
  receptionist_confirm: boolean; receptionist_signoff_at: string | null; receptionist_notes: string
  status: TripStatus; status_label: string; is_overdue: boolean
  flagged: boolean; flag_reason: string; distance_km: number | null
  photos: { id: string; kind: string; caption: string; url: string }[]
  created_at: string | null
}
export interface Vehicle {
  id: string; registration: string; make: string; model: string; year: number | null
  colour: string; vin: string; home_yard: string; condition_notes: string
  status: VehStatus; status_label: string; odometer_km: number | null
  open_trip: Trip | null
}
export interface BoardResp {
  /** True only for the fleet administrators (Unami / Dorothy) + omni admins.
      Hides "Add vehicle" and "Clear to service" for everyone else; the backend
      enforces it regardless — this just stops us offering a button that fails. */
  can_manage?: boolean
  stats: { total: number; available: number; out: number; maintenance: number
           retired: number; overdue: number; pending_signoff: number }
  vehicles: Vehicle[]; overdue: Trip[]; pending_signoff: Trip[]; damage_register: Trip[]
}
export interface Opt { value: string; label: string }
export interface PurposesResp { purposes: Opt[]; fuel_levels: Opt[]; other_value: string }

// ── formatting ────────────────────────────────────────────────────────────────
export function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}
export function sinceText(iso: string | null): string {
  if (!iso) return ''
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60), m = mins % 60
  if (h < 24) return `${h}h ${m}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  available:   { bg: '#E6F6EC', fg: GREEN,     label: 'In yard' },
  out:         { bg: '#FDEFD7', fg: ORANGE_TXT, label: 'Out' },
  maintenance: { bg: '#FCEAEA', fg: RED,       label: 'Maintenance' },
  retired:     { bg: '#EEF0F4', fg: MUT,       label: 'Retired' },
}
export function StatusPill({ status, overdue }: { status: string; overdue?: boolean }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.retired
  const bg = overdue ? '#FCEAEA' : s.bg
  const fg = overdue ? RED : s.fg
  return (
    <span className="px-2.5 py-1 rounded-full" style={{ fontSize: 12, fontWeight: 700, background: bg, color: fg }}>
      {overdue ? '⏰ Overdue' : s.label}
    </span>
  )
}

// ── primitives ────────────────────────────────────────────────────────────────
export function Btn({ children, onClick, kind = 'primary', disabled, small }: {
  children: React.ReactNode; onClick?: () => void
  kind?: 'primary' | 'ghost' | 'danger'; disabled?: boolean; small?: boolean
}) {
  const base: React.CSSProperties = {
    borderRadius: 12, fontWeight: 700, cursor: disabled ? 'not-allowed' : 'pointer',
    padding: small ? '7px 12px' : '10px 16px', fontSize: small ? 13 : 14,
    border: '1px solid transparent', opacity: disabled ? 0.5 : 1, transition: 'filter .15s',
  }
  const styles: Record<string, React.CSSProperties> = {
    primary: { ...base, background: NAVY, color: '#fff' },
    ghost:   { ...base, background: '#fff', color: NAVY, border: `1px solid ${HAIR}` },
    danger:  { ...base, background: '#fff', color: RED, border: '1px solid #F6C9C9' },
  }
  return <button onClick={onClick} disabled={disabled} style={styles[kind]}>{children}</button>
}

export function Modal({ title, subtitle, onClose, children }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode
}) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(13,27,42,.45)', zIndex: 60,
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: 20, overflowY: 'auto',
    }}>
      <div onClick={e => e.stopPropagation()} style={{ ...card, width: '100%', maxWidth: 520, marginTop: 40 }}>
        <div style={{ padding: '18px 22px', borderBottom: `1px solid ${HAIR}` }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: NAVY }}>{title}</div>
          {subtitle && <div style={{ fontSize: 13, color: MUT, marginTop: 2 }}>{subtitle}</div>}
        </div>
        <div style={{ padding: 22 }}>{children}</div>
      </div>
    </div>
  )
}

const labelStyle: React.CSSProperties = { fontSize: 12.5, fontWeight: 600, color: NAVY, marginBottom: 6, display: 'block' }
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '9px 12px', borderRadius: 10, border: `1px solid ${HAIR}`,
  fontSize: 14, color: NAVY, background: '#fff', outline: 'none',
}
export function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block', marginBottom: 14 }}>
      <span style={labelStyle}>{label}{required && <span style={{ color: RED }}> *</span>}</span>
      {children}
    </label>
  )
}
export function TextInput(p: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...p} style={{ ...inputStyle, ...(p.style || {}) }} />
}
export function TextArea(p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...p} style={{ ...inputStyle, minHeight: 72, resize: 'vertical', ...(p.style || {}) }} />
}
export function Select({ options, ...p }: { options: Opt[] } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...p} style={{ ...inputStyle, ...(p.style || {}) }}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}
export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 14, cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
             style={{ marginTop: 3, width: 16, height: 16, accentColor: NAVY }} />
      <span style={{ fontSize: 13.5, color: NAVY }}>{label}</span>
    </label>
  )
}
export function ErrText({ children }: { children: React.ReactNode }) {
  if (!children) return null
  return <div className="rounded-xl p-3 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: RED, marginBottom: 12 }}>{children}</div>
}
