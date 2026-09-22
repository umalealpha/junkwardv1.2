'use client'
/** Checkout / check-in / sign-off / add-vehicle modals for the Vehicle Register. */
import React, { useState } from 'react'
import {
  Btn, ErrText, Field, Modal, Opt, Select, TextArea, TextInput, Toggle, Trip, Vehicle,
  jpost, MUT, NAVY, fmtWhen,
} from './ui'

function errMsg(e: unknown): string {
  if (e instanceof Error) {
    // apiFetch surfaces the DRF {detail} in the thrown message.
    return e.message
  }
  return 'Something went wrong.'
}

// ── Checkout ──────────────────────────────────────────────────────────────────
export function CheckoutModal({ vehicle, purposes, fuels, otherValue, onClose, onDone }: {
  vehicle: Vehicle; purposes: Opt[]; fuels: Opt[]; otherValue: string
  onClose: () => void; onDone: () => void
}) {
  const [driver, setDriver] = useState('')
  const [purpose, setPurpose] = useState(purposes[0]?.value ?? '')
  const [notes, setNotes] = useState('')
  const [destination, setDestination] = useState('')
  const [odo, setOdo] = useState(vehicle.odometer_km != null ? String(vehicle.odometer_km) : '')
  const [fuel, setFuel] = useState('')
  const [expected, setExpected] = useState('')
  const [preTrip, setPreTrip] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const isOther = purpose === otherValue

  async function submit() {
    setErr(null); setBusy(true)
    try {
      await jpost('/nexus/vehicle-register/checkout/', {
        vehicle: vehicle.id, driver_name: driver, purpose, purpose_notes: notes,
        destination, odometer_out: odo === '' ? null : Number(odo),
        fuel_level_out: fuel, expected_return_at: expected || null,
        pre_trip_notes: preTrip, driver_condition_confirm: confirm,
      })
      onDone()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Check out — ${vehicle.registration}`}
           subtitle={[vehicle.make, vehicle.model].filter(Boolean).join(' ') || 'Pool car'} onClose={onClose}>
      <ErrText>{err}</ErrText>
      <Field label="Driver" required><TextInput value={driver} placeholder="Full name"
        onChange={e => setDriver(e.target.value)} /></Field>
      <Field label="Purpose of trip" required>
        <Select options={purposes} value={purpose} onChange={e => setPurpose(e.target.value)} />
      </Field>
      {isOther && (
        <Field label="Explain (required for Other)" required>
          <TextArea value={notes} onChange={e => setNotes(e.target.value)}
            placeholder="This trip will be flagged for review." />
        </Field>
      )}
      <Field label="Destination" required><TextInput value={destination}
        placeholder="Where is the car going?" onChange={e => setDestination(e.target.value)} /></Field>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Odometer out (km)"><TextInput type="number" value={odo}
          onChange={e => setOdo(e.target.value)} /></Field>
        <Field label="Fuel level out">
          <Select options={[{ value: '', label: '—' }, ...fuels]} value={fuel}
            onChange={e => setFuel(e.target.value)} />
        </Field>
      </div>
      <Field label="Expected return"><TextInput type="datetime-local" value={expected}
        onChange={e => setExpected(e.target.value)} /></Field>
      <Field label="Pre-trip condition notes"><TextArea value={preTrip}
        placeholder="Any existing damage or issues seen at collection." onChange={e => setPreTrip(e.target.value)} /></Field>
      <Toggle checked={confirm} onChange={setConfirm}
        label="I confirm I have seen the car's condition and am taking it out." />
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn onClick={submit} disabled={busy || !confirm}>{busy ? 'Checking out…' : 'Check out'}</Btn>
      </div>
    </Modal>
  )
}

// ── Check-in ──────────────────────────────────────────────────────────────────
export function CheckinModal({ trip, fuels, onClose, onDone }: {
  trip: Trip; fuels: Opt[]; onClose: () => void; onDone: () => void
}) {
  const [odo, setOdo] = useState('')
  const [fuel, setFuel] = useState('')
  const [ret, setRet] = useState(false)
  const [damage, setDamage] = useState(false)
  const [damageNotes, setDamageNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setErr(null); setBusy(true)
    try {
      await jpost(`/nexus/vehicle-register/trips/${trip.id}/checkin/`, {
        odometer_in: odo === '' ? null : Number(odo), fuel_level_in: fuel,
        driver_return_confirm: ret, damage_on_return: damage, damage_notes: damageNotes,
      })
      onDone()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Check in — ${trip.registration}`}
           subtitle={`${trip.driver_name} · ${trip.purpose_label}`} onClose={onClose}>
      <ErrText>{err}</ErrText>
      <div style={{ fontSize: 13, color: MUT, marginBottom: 12 }}>
        Out since {fmtWhen(trip.checkout_at)}{trip.odometer_out != null && ` · odo out ${trip.odometer_out.toLocaleString()} km`}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Odometer in (km)" required><TextInput type="number" value={odo}
          onChange={e => setOdo(e.target.value)} /></Field>
        <Field label="Fuel level in">
          <Select options={[{ value: '', label: '—' }, ...fuels]} value={fuel}
            onChange={e => setFuel(e.target.value)} />
        </Field>
      </div>
      <Toggle checked={ret} onChange={setRet}
        label="I confirm the car is returned in good working condition." />
      <Toggle checked={damage} onChange={setDamage}
        label="Report damage / an incident on this trip." />
      {damage && (
        <Field label="Describe the damage" required>
          <TextArea value={damageNotes} onChange={e => setDamageNotes(e.target.value)}
            placeholder="The car will be flagged and blocked to maintenance; the fleet admin + CFO are notified." />
        </Field>
      )}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn onClick={submit} disabled={busy || !ret}>{busy ? 'Checking in…' : 'Check in'}</Btn>
      </div>
    </Modal>
  )
}

// ── Receptionist sign-off ───────────────────────────────────────────────────────
export function SignoffModal({ trip, onClose, onDone }: {
  trip: Trip; onClose: () => void; onDone: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setErr(null); setBusy(true)
    try {
      await jpost(`/nexus/vehicle-register/trips/${trip.id}/signoff/`, {
        receptionist_confirm: confirm, receptionist_notes: notes,
      })
      onDone()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Reception sign-off — ${trip.registration}`}
           subtitle={`Returned by ${trip.driver_name}`} onClose={onClose}>
      <ErrText>{err}</ErrText>
      <div style={{ fontSize: 13, color: MUT, marginBottom: 12 }}>
        Returned {fmtWhen(trip.checkin_at)}
        {trip.odometer_in != null && ` · odo in ${trip.odometer_in.toLocaleString()} km`}
        {trip.distance_km != null && ` · ${trip.distance_km.toLocaleString()} km driven`}
        {trip.damage_on_return && <span style={{ color: '#B42318', fontWeight: 700 }}> · damage reported</span>}
      </div>
      {trip.damage_on_return && trip.damage_notes && (
        <div className="rounded-xl p-3" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: '#B42318', fontSize: 13, marginBottom: 12 }}>
          {trip.damage_notes}
        </div>
      )}
      <Toggle checked={confirm} onChange={setConfirm}
        label="I have checked the vehicle and confirm its condition on return." />
      <Field label="Notes (optional)"><TextArea value={notes} onChange={e => setNotes(e.target.value)} /></Field>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn onClick={submit} disabled={busy || !confirm}>{busy ? 'Signing…' : 'Confirm & close trip'}</Btn>
      </div>
    </Modal>
  )
}

// ── Add vehicle ─────────────────────────────────────────────────────────────────
export function AddVehicleModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ registration: '', make: '', model: '', year: '', colour: '', vin: '', odometer_km: '', home_yard: '', condition_notes: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })

  async function submit() {
    setErr(null); setBusy(true)
    try {
      await jpost('/nexus/vehicles/', {
        ...f, year: f.year || null, odometer_km: f.odometer_km || null,
      })
      onDone()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <Modal title="Add vehicle to register" subtitle="Populate from the car list once received." onClose={onClose}>
      <ErrText>{err}</ErrText>
      <Field label="Registration (number plate)" required><TextInput value={f.registration}
        onChange={set('registration')} placeholder="B 123 ABC" /></Field>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Make"><TextInput value={f.make} onChange={set('make')} placeholder="Toyota" /></Field>
        <Field label="Model"><TextInput value={f.model} onChange={set('model')} placeholder="Hilux" /></Field>
        <Field label="Year"><TextInput type="number" value={f.year} onChange={set('year')} /></Field>
        <Field label="Colour"><TextInput value={f.colour} onChange={set('colour')} /></Field>
        <Field label="VIN"><TextInput value={f.vin} onChange={set('vin')} /></Field>
        <Field label="Odometer (km)"><TextInput type="number" value={f.odometer_km} onChange={set('odometer_km')} /></Field>
        <Field label="Keys held at"><TextInput value={f.home_yard} onChange={set('home_yard')} placeholder="Reception / Exco office / branch" /></Field>
        <Field label="Condition / existing damage"><TextInput value={f.condition_notes} onChange={set('condition_notes')} placeholder="e.g. scratches on front right" /></Field>
      </div>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4, color: NAVY }}>
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn onClick={submit} disabled={busy || !f.registration.trim()}>{busy ? 'Adding…' : 'Add vehicle'}</Btn>
      </div>
    </Modal>
  )
}

// ── Correct a mistyped odometer reading ───────────────────────────────────────
export function CorrectOdometerModal({ trip, onClose, onDone }: {
  trip: Trip; onClose: () => void; onDone: () => void
}) {
  const [out, setOut] = useState(trip.odometer_out != null ? String(trip.odometer_out) : '')
  const [odoIn, setOdoIn] = useState(trip.odometer_in != null ? String(trip.odometer_in) : '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setErr(null); setBusy(true)
    try {
      await jpost(`/nexus/vehicle-register/trips/${trip.id}/correct-odometer/`, {
        odometer_out: out === '' ? null : Number(out),
        odometer_in: odoIn === '' ? null : Number(odoIn),
        reason,
      })
      onDone()
    } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Correct odometer — ${trip.registration}`}
           subtitle={`${trip.driver_name} · ${trip.purpose_label}`} onClose={onClose}>
      <ErrText>{err}</ErrText>
      <div style={{ fontSize: 13, color: MUT, marginBottom: 12 }}>
        Fix a mistyped reading. The original value is kept in the audit trail, and
        the car&apos;s current odometer is recalculated automatically.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Odometer out (km)"><TextInput type="number" value={out}
          onChange={e => setOut(e.target.value)} /></Field>
        <Field label="Odometer in (km)"><TextInput type="number" value={odoIn}
          onChange={e => setOdoIn(e.target.value)} /></Field>
      </div>
      <Field label="Reason for the correction" required>
        <TextArea value={reason} onChange={e => setReason(e.target.value)}
          placeholder="e.g. typing error on check-in — extra digit." />
      </Field>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 4 }}>
        <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
        <Btn onClick={submit} disabled={busy || !reason.trim()}>{busy ? 'Saving…' : 'Save correction'}</Btn>
      </div>
    </Modal>
  )
}
