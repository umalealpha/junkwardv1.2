'use client'
/**
 * /vehicle-register — Pool-car checkout / check-in register (CFO/EXCO 2026-07-16).
 *
 * Replaces the paper access books. Live board of every company car's status
 * (in yard / out — who, purpose, since, expected back), overdue highlighting,
 * a reception sign-off queue, a damage register, and per-driver / purpose /
 * utilisation reports. Every trip maps to an approved business purpose; damage
 * on return flags the trip, alerts the fleet admin + CFO, and blocks the car.
 *
 * Extends the existing nexus fleet register; reads /api/v1/nexus/vehicle-*.
 * CarTrack-ready: odometer + GPS + return detection auto-populate once keys land.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { AddVehicleModal, CheckinModal, CheckoutModal, SignoffModal } from './forms'
import { ReportsPanel, TripsPanel } from './panels'
import {
  BoardResp, Btn, CANVAS, HAIR, MUT, NAVY, ORANGE_TXT, Opt, PurposesResp, RED,
  StatusPill, Trip, Vehicle, card, eyebrow, fmtWhen, jget, jpost, sinceText, GREEN,
} from './ui'

type Tab = 'board' | 'trips' | 'reports'
type ModalState =
  | { kind: 'none' }
  | { kind: 'checkout'; vehicle: Vehicle }
  | { kind: 'checkin'; trip: Trip }
  | { kind: 'signoff'; trip: Trip }
  | { kind: 'add' }

export default function VehicleRegisterPage() {
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('board')
  const [board, setBoard] = useState<BoardResp | null>(null)
  const [purposes, setPurposes] = useState<Opt[]>([])
  const [fuels, setFuels] = useState<Opt[]>([])
  const [otherValue, setOtherValue] = useState('other')
  const [modal, setModal] = useState<ModalState>({ kind: 'none' })
  const [err, setErr] = useState<string | null>(null)

  const loadBoard = useCallback(async () => {
    try { setBoard(await jget<BoardResp>('/nexus/vehicle-register/board/')) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load the board') }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    jget<PurposesResp>('/nexus/vehicle-register/purposes/').then(r => {
      setPurposes(r.purposes); setFuels(r.fuel_levels); setOtherValue(r.other_value)
    }).catch(() => {})
    loadBoard()
  }, [loadBoard, router])

  const done = () => { setModal({ kind: 'none' }); loadBoard() }
  const stats = board?.stats

  async function clearMaintenance(v: Vehicle) {
    try { await jpost(`/nexus/vehicles/${v.id}/clear-maintenance/`, {}); loadBoard() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed') }
  }

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Vehicle Register" breadcrumbs={[{ label: 'Assets' }, { label: 'Pool cars' }]} />
      <div className="flex-1 p-6 max-w-6xl mx-auto w-full space-y-4" style={{ color: NAVY }}>
        {err && <div className="rounded-2xl p-4 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: RED }}>{err}</div>}

        {/* stat tiles */}
        {stats && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 12 }}>
            <Stat label="Vehicles" value={stats.total} />
            <Stat label="In yard" value={stats.available} tone={GREEN} />
            <Stat label="Out now" value={stats.out} tone={ORANGE_TXT} />
            <Stat label="Overdue" value={stats.overdue} tone={stats.overdue ? RED : MUT} />
            <Stat label="Awaiting sign-off" value={stats.pending_signoff} tone={stats.pending_signoff ? ORANGE_TXT : MUT} />
            <Stat label="Maintenance" value={stats.maintenance} tone={stats.maintenance ? RED : MUT} />
          </div>
        )}

        {/* tabs + add */}
        <div className="flex items-center gap-2 flex-wrap">
          {(['board', 'trips', 'reports'] as Tab[]).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: '8px 16px', borderRadius: 999, fontSize: 13.5, fontWeight: 700, cursor: 'pointer',
              border: `1px solid ${tab === t ? NAVY : HAIR}`, textTransform: 'capitalize',
              background: tab === t ? NAVY : '#fff', color: tab === t ? '#fff' : NAVY,
            }}>{t === 'board' ? 'Live board' : t}</button>
          ))}
          {board?.can_manage && (
            <div style={{ marginLeft: 'auto' }}><Btn kind="ghost" onClick={() => setModal({ kind: 'add' })}>+ Add vehicle</Btn></div>
          )}
        </div>

        {tab === 'board' && board && (
          <BoardView board={board} canManage={!!board.can_manage}
            onCheckout={v => setModal({ kind: 'checkout', vehicle: v })}
            onCheckin={t => setModal({ kind: 'checkin', trip: t })}
            onSignoff={t => setModal({ kind: 'signoff', trip: t })}
            onClear={clearMaintenance} />
        )}
        {tab === 'trips' && <TripsPanel />}
        {tab === 'reports' && <ReportsPanel />}

        {!board && !err && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}
      </div>

      {modal.kind === 'checkout' && (
        <CheckoutModal vehicle={modal.vehicle} purposes={purposes} fuels={fuels} otherValue={otherValue}
          onClose={() => setModal({ kind: 'none' })} onDone={done} />)}
      {modal.kind === 'checkin' && (
        <CheckinModal trip={modal.trip} fuels={fuels} onClose={() => setModal({ kind: 'none' })} onDone={done} />)}
      {modal.kind === 'signoff' && (
        <SignoffModal trip={modal.trip} onClose={() => setModal({ kind: 'none' })} onDone={done} />)}
      {modal.kind === 'add' && (
        <AddVehicleModal onClose={() => setModal({ kind: 'none' })} onDone={done} />)}
    </div>
  )
}

function Stat({ label, value, tone = NAVY }: { label: string; value: number; tone?: string }) {
  return (
    <div style={{ ...card, padding: 16 }}>
      {/* Two lines' worth of room reserved: "Awaiting sign-off" wraps at narrow
          widths and without this its number drops a line, so the six figures
          stop sitting on a shared baseline. */}
      <div style={{ ...eyebrow, minHeight: 26, lineHeight: '13px' }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: tone, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

function BoardView({ board, canManage, onCheckout, onCheckin, onSignoff, onClear }: {
  board: BoardResp; canManage: boolean
  onCheckout: (v: Vehicle) => void; onCheckin: (t: Trip) => void
  onSignoff: (t: Trip) => void; onClear: (v: Vehicle) => void
}) {
  return (
    <div className="space-y-4">
      {board.overdue.length > 0 && (
        <div className="rounded-2xl p-4" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9' }}>
          <div style={{ fontWeight: 700, color: RED, marginBottom: 6 }}>⏰ {board.overdue.length} overdue — not returned by expected time</div>
          {board.overdue.map(t => (
            <div key={t.id} style={{ fontSize: 13.5, color: '#7A271A' }}>
              <b>{t.registration}</b> · {t.driver_name} · {t.destination} · due {fmtWhen(t.expected_return_at)}
            </div>
          ))}
        </div>
      )}

      {board.pending_signoff.length > 0 && (
        <div style={card}>
          <div style={{ padding: '14px 16px', fontWeight: 700, borderBottom: `1px solid ${HAIR}` }}>
            Awaiting reception sign-off ({board.pending_signoff.length})
          </div>
          {board.pending_signoff.map(t => (
            <div key={t.id} className="flex items-center gap-3 flex-wrap" style={{ padding: '12px 16px', borderTop: `1px solid ${HAIR}` }}>
              <b style={{ minWidth: 110 }}>{t.registration}</b>
              <span style={{ fontSize: 13, color: MUT }}>{t.driver_name} · returned {fmtWhen(t.checkin_at)}</span>
              {t.damage_on_return && <span style={{ fontSize: 12, fontWeight: 700, color: RED }}>⚠ damage</span>}
              <div style={{ marginLeft: 'auto' }}><Btn small onClick={() => onSignoff(t)}>Sign off condition</Btn></div>
            </div>
          ))}
        </div>
      )}

      {board.vehicles.length === 0 ? (
        <div style={{ ...card, padding: 32, textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>No vehicles yet</div>
          <p style={{ fontSize: 13, color: MUT, marginTop: 6 }}>
            {canManage
              ? <>Add the pool cars (registration + odometer) to start the register — use <b>+ Add vehicle</b> above.</>
              : <>The pool cars have not been loaded yet — ask Unami or Dorothy to add them.</>}
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(280px,1fr))', gap: 14 }}>
          {board.vehicles.map(v => <VehicleCard key={v.id} v={v} canManage={canManage}
            onCheckout={onCheckout} onCheckin={onCheckin} onClear={onClear} />)}
        </div>
      )}

      {board.damage_register.length > 0 && (
        <div style={card}>
          <div style={{ padding: '14px 16px', fontWeight: 700, borderBottom: `1px solid ${HAIR}` }}>Damage / incident register</div>
          {board.damage_register.map(t => (
            <div key={t.id} style={{ padding: '12px 16px', borderTop: `1px solid ${HAIR}`, fontSize: 13.5 }}>
              <b>{t.registration}</b> · {t.driver_name} · {fmtWhen(t.checkin_at)}
              <div style={{ color: RED, marginTop: 2 }}>{t.damage_notes || '(no notes)'}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function VehicleCard({ v, canManage, onCheckout, onCheckin, onClear }: {
  v: Vehicle; canManage: boolean
  onCheckout: (v: Vehicle) => void; onCheckin: (t: Trip) => void; onClear: (v: Vehicle) => void
}) {
  const t = v.open_trip
  return (
    <div style={{ ...card, padding: 16, display: 'flex', flexDirection: 'column', gap: 8,
                  borderColor: t?.is_overdue ? '#F6C9C9' : HAIR }}>
      <div className="flex items-center justify-between gap-2">
        <div style={{ fontSize: 17, fontWeight: 700 }}>{v.registration}</div>
        <StatusPill status={v.status} overdue={!!t?.is_overdue} />
      </div>
      <div style={{ fontSize: 12.5, color: MUT }}>
        {[v.make, v.model].filter(Boolean).join(' ') || 'Pool car'}
        {v.year ? ` · ${v.year}` : ''}{v.colour ? ` · ${v.colour}` : ''}
        {v.odometer_km != null && <> · {v.odometer_km.toLocaleString()} km</>}
      </div>
      {v.home_yard && (
        <div style={{ fontSize: 12, color: MUT }}>🔑 Keys held at <b style={{ color: NAVY }}>{v.home_yard}</b></div>
      )}
      {v.condition_notes && (
        <div style={{ fontSize: 12, color: NAVY, background: '#FFF9EE', border: `1px solid #F3E4C4`, borderRadius: 10, padding: '6px 10px' }}>
          {v.condition_notes}
        </div>
      )}

      {v.status === 'out' && t && (
        <div style={{ fontSize: 13, color: NAVY, background: '#F7F8FB', borderRadius: 12, padding: 10 }}>
          <div>👤 <b>{t.driver_name}</b></div>
          <div style={{ color: MUT }}>{t.purpose_label} → {t.destination}</div>
          <div style={{ color: MUT, marginTop: 2 }}>
            out {sinceText(t.checkout_at)}{t.expected_return_at && <> · due {fmtWhen(t.expected_return_at)}</>}
          </div>
        </div>
      )}
      {v.status === 'maintenance' && (
        <div style={{ fontSize: 12.5, color: RED }}>
          Blocked pending inspection.{!canManage && ' Unami or Dorothy releases it.'}
        </div>
      )}

      <div style={{ marginTop: 'auto', paddingTop: 6, display: 'flex', gap: 8 }}>
        {v.status === 'available' && <Btn small onClick={() => onCheckout(v)}>Check out</Btn>}
        {v.status === 'out' && t && <Btn small kind="ghost" onClick={() => onCheckin(t)}>Check in</Btn>}
        {v.status === 'maintenance' && canManage && (
          <Btn small kind="danger" onClick={() => onClear(v)}>Clear to service</Btn>)}
      </div>
    </div>
  )
}
