'use client'

/** Pool-car trip from the phone — the paper access book, digital. Drives the SAME
 * endpoints as the desktop /vehicle-register page (frontend/src/app/(dashboard)/vehicle-register/forms.tsx);
 * backend nexus/vehicle_register.py:
 *   GET  /nexus/vehicles/                            the register (status, odometer, open trip)
 *   GET  /nexus/vehicle-register/purposes/           approved purposes + fuel levels + the "Other" value
 *   POST /nexus/vehicle-register/checkout/           take a car out (odometer OUT, purpose, destination)
 *   POST /nexus/vehicle-register/trips/<id>/checkin/ bring it back (odometer IN)
 *   POST /nexus/vehicle-register/trips/<id>/photo/   optional condition photo (`image`, `kind`)
 *   GET  /nexus/vehicle-register/trips/?driver=      my recent trips (the register has no "mine" filter —
 *                                                    it is keyed by driver name, so the name you give is remembered)
 * The trip date is stamped by the server at checkout/check-in. No GPS. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, Car, Send, X } from 'lucide-react'
import { compressImage, reauthOn401, sfetch } from '@/app/(customer)/api'
import { C } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, StatusPill, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch,
} from './StaffFormKit'

interface Opt { value: string; label: string }
interface Trip {
  id: string; vehicle_id: string; registration: string; driver_name: string; purpose_label: string; destination: string
  checkout_at: string | null; checkin_at: string | null; odometer_out: number | null; odometer_in: number | null
  status: string; status_label: string; distance_km: number | null; flagged: boolean; flag_reason: string
}
interface Vehicle {
  id: string; registration: string; make: string; model: string; status: string; status_label: string
  odometer_km: number | null; open_trip: Trip | null
}
const NAME_KEY = 'omni_vehicle_driver_name'
const fmtWhen = (iso: string | null) => iso ? new Date(iso).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'

export default function VehicleTripScreen() {
  const base = useStaffBase()
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null)
  const [purposes, setPurposes] = useState<Opt[]>([])
  const [fuels, setFuels] = useState<Opt[]>([])
  const [otherValue, setOtherValue] = useState('other')
  const [trips, setTrips] = useState<Trip[] | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [tripsErr, setTripsErr] = useState<string | null>(null)

  const [driver, setDriver] = useState('')
  const [vehicleId, setVehicleId] = useState('')
  const [purpose, setPurpose] = useState('')
  const [purposeNotes, setPurposeNotes] = useState('')
  const [destination, setDestination] = useState('')
  const [odoOut, setOdoOut] = useState('')
  const [fuelOut, setFuelOut] = useState('')
  const [expected, setExpected] = useState('')
  const [confirmOut, setConfirmOut] = useState(false)
  // Check-in (odometer end) for the open trip on the chosen vehicle.
  const [odoIn, setOdoIn] = useState('')
  const [fuelIn, setFuelIn] = useState('')
  const [confirmIn, setConfirmIn] = useState(false)
  const [damage, setDamage] = useState(false)
  const [damageNotes, setDamageNotes] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)

  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  useEffect(() => { try { setDriver(localStorage.getItem(NAME_KEY) || '') } catch { /* private mode */ } }, [])

  const loadTrips = useCallback((name: string) => {
    setTripsErr(null)
    const q = name.trim() ? `?driver=${encodeURIComponent(name.trim())}` : ''
    sfetch<{ trips: Trip[] }>(`/nexus/vehicle-register/trips/${q}`)
      .then(r => setTrips((r.trips || []).slice(0, 10)))
      .catch(e => { if (!reauthOn401(e)) { setTrips(t => t ?? []); setTripsErr(errText(e, 'Could not load your trips.')) } })
  }, [])

  const load = useCallback(() => {
    setLoadErr(null)
    Promise.all([
      sfetch<{ vehicles: Vehicle[] }>('/nexus/vehicles/'),
      sfetch<{ purposes: Opt[]; fuel_levels: Opt[]; other_value: string }>('/nexus/vehicle-register/purposes/'),
    ]).then(([v, p]) => {
      setVehicles(v.vehicles || []); setPurposes(p.purposes || []); setFuels(p.fuel_levels || []); setOtherValue(p.other_value || 'other')
      setPurpose(cur => cur || (p.purposes?.[0]?.value ?? ''))
    }).catch(e => { if (!reauthOn401(e)) { setVehicles(vs => vs ?? []); setLoadErr(errText(e, 'Could not load the vehicle register.')) } })
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => { const t = setTimeout(() => loadTrips(driver), 400); return () => clearTimeout(t) }, [driver, loadTrips])

  const vehicle = vehicles?.find(v => v.id === vehicleId) || null
  const openTrip = vehicle?.open_trip || null
  const rememberName = () => { try { if (driver.trim()) localStorage.setItem(NAME_KEY, driver.trim()) } catch { /* ignore */ } }

  const onSnap = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]; e.target.value = ''
    if (!raw) return
    const f = await compressImage(raw)
    if (preview) URL.revokeObjectURL(preview)
    setPhoto(f); setPreview(URL.createObjectURL(f))
  }
  const clearPhoto = () => { if (preview) URL.revokeObjectURL(preview); setPhoto(null); setPreview(null) }

  async function uploadPhoto(tripId: string, kind: 'pre_trip' | 'return_damage') {
    if (!photo) return
    const fd = new FormData(); fd.append('image', photo); fd.append('kind', kind)
    try { await sfetch(`/nexus/vehicle-register/trips/${tripId}/photo/`, { method: 'POST', body: fd }, false) }
    catch (e) { if (!reauthOn401(e)) show(`Trip saved, but the photo failed: ${errText(e, 'upload error')}`) }
  }

  async function checkout() {
    setServerErr(null)
    if (!vehicleId) { show('Pick a vehicle.'); return }
    if (!destination.trim()) { show('Say where the car is going.'); return }
    if (!confirmOut) { show('Tick that you took the car and saw its condition.'); return }
    setBusy(true); rememberName()
    try {
      // Same body as the desktop CheckoutModal. driver_name blank = the server uses your own name.
      const r = await rawStaffFetch('/nexus/vehicle-register/checkout/', { method: 'POST', body: JSON.stringify({
        vehicle: vehicleId, driver_name: driver.trim(), purpose, purpose_notes: purposeNotes.trim(),
        destination: destination.trim(), odometer_out: odoOut === '' ? null : Number(odoOut),
        fuel_level_out: fuelOut, expected_return_at: expected || null, pre_trip_notes: '', driver_condition_confirm: confirmOut,
      }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      const trip = r.body as unknown as Trip
      await uploadPhoto(trip.id, 'pre_trip')
      show(`${trip.registration} checked out to ${trip.driver_name}. ✅`)
      if (!driver.trim() && trip.driver_name) { setDriver(trip.driver_name); try { localStorage.setItem(NAME_KEY, trip.driver_name) } catch { /* ignore */ } }
      setDestination(''); setOdoOut(''); setFuelOut(''); setExpected(''); setConfirmOut(false); setPurposeNotes(''); clearPhoto()
      load(); loadTrips(driver || trip.driver_name)
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not check the car out.')) }
    finally { setBusy(false) }
  }

  async function checkin() {
    if (!openTrip) return
    setServerErr(null)
    if (odoIn === '') { show('Enter the odometer reading on return.'); return }
    if (!confirmIn) { show('Tick that the car is returned.'); return }
    if (damage && !damageNotes.trim()) { show('Describe the damage.'); return }
    setBusy(true)
    try {
      const r = await rawStaffFetch(`/nexus/vehicle-register/trips/${openTrip.id}/checkin/`, { method: 'POST', body: JSON.stringify({
        odometer_in: Number(odoIn), fuel_level_in: fuelIn, driver_return_confirm: confirmIn,
        damage_on_return: damage, damage_notes: damageNotes.trim(),
      }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      await uploadPhoto(openTrip.id, damage ? 'return_damage' : 'pre_trip')
      const t = r.body as unknown as Trip
      show(`Returned${t.distance_km != null ? ` · ${t.distance_km} km` : ''}. Reception signs it off. ✅`)
      setOdoIn(''); setFuelIn(''); setConfirmIn(false); setDamage(false); setDamageNotes(''); clearPhoto()
      load(); loadTrips(driver)
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not check the car in.')) }
    finally { setBusy(false) }
  }

  const checkbox = (checked: boolean, onChange: (v: boolean) => void, text: string) => (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 12, fontSize: 13, color: C.ink, minHeight: 44, lineHeight: 1.5 }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
      <span>{text}</span>
    </label>
  )
  const fuelSelect = (v: string, set: (s: string) => void) => (
    <select value={v} onChange={e => set(e.target.value)} aria-label="Fuel level" style={inputStyle}>
      <option value="">Fuel level (optional)</option>
      {fuels.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
    </select>
  )
  const photoBlock = (label: string) => (
    <div style={{ marginTop: 12 }}>
      <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onSnap} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {preview && (
          <div style={{ position: 'relative' }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={preview} alt="condition photo" style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 12, border: `1px solid ${C.line}` }} />
            <button onClick={clearPhoto} aria-label="Remove photo" style={{ position: 'absolute', top: -6, right: -6, width: 22, height: 22, borderRadius: 999, border: 'none', background: C.navy, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><X size={12} /></button>
          </div>
        )}
        <button onClick={() => camRef.current?.click()} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', gap: 6, color: '#B45309' }}><Camera size={16} /> {photo ? 'Retake' : label}</button>
      </div>
    </div>
  )

  return (
    <ScreenFrame title="Vehicle trip" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Car size={20} style={{ color: C.orange }} /></div>
          <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>Sign the pool car out and back in. The time is stamped by Omni; reception signs off the return.</p>
        </div>
        <label htmlFor="trip-driver" style={labelStyle}>Your name (as the driver)</label>
        <input id="trip-driver" value={driver} onChange={e => setDriver(e.target.value)} onBlur={rememberName} placeholder="Full name — remembered on this phone" style={inputStyle} />
        <label htmlFor="trip-vehicle" style={labelStyle}>Vehicle</label>
        <select id="trip-vehicle" value={vehicleId} onChange={e => { setVehicleId(e.target.value); setServerErr(null) }} style={inputStyle} disabled={vehicles === null}>
          <option value="">{vehicles === null ? 'Loading…' : vehicles.length ? 'Choose a car…' : 'No vehicles on the register'}</option>
          {vehicles?.map(v => <option key={v.id} value={v.id}>{v.registration} · {[v.make, v.model].filter(Boolean).join(' ')} — {v.status_label}</option>)}
        </select>
        {vehicle && vehicle.odometer_km != null && <p style={{ margin: '6px 0 0', fontSize: 12, color: C.inkSoft }}>Last reading on file: {vehicle.odometer_km.toLocaleString()} km</p>}
      </Card>

      {vehicle && openTrip && (
        <Card>
          <b style={{ color: C.ink, fontSize: 15 }}>Bring {vehicle.registration} back</b>
          <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>Out since {fmtWhen(openTrip.checkout_at)} · {openTrip.driver_name}{openTrip.odometer_out != null ? ` · odo out ${openTrip.odometer_out.toLocaleString()} km` : ''}</p>
          <label htmlFor="trip-odo-in" style={labelStyle}>Odometer on return (km)</label>
          <input id="trip-odo-in" value={odoIn} onChange={e => setOdoIn(e.target.value.replace(/[^0-9]/g, ''))} inputMode="numeric" placeholder="e.g. 45210" style={inputStyle} />
          <label style={labelStyle}>Fuel</label>
          {fuelSelect(fuelIn, setFuelIn)}
          {checkbox(confirmIn, setConfirmIn, 'I am returning the car and I have checked its condition.')}
          {checkbox(damage, setDamage, 'There is damage on return (this blocks the car for maintenance and alerts fleet + CFO).')}
          {damage && <textarea value={damageNotes} onChange={e => setDamageNotes(e.target.value)} rows={2} placeholder="Describe the damage" aria-label="Describe the damage" style={{ ...inputStyle, marginTop: 8, resize: 'vertical' }} />}
          {photoBlock(damage ? 'Photo of the damage' : 'Photo (optional)')}
          {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
          <button onClick={checkin} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 14 }}><Send size={16} /> {busy ? 'Saving…' : 'Check the car in'}</button>
        </Card>
      )}

      {vehicle && !openTrip && (
        <Card>
          <b style={{ color: C.ink, fontSize: 15 }}>Take {vehicle.registration} out</b>
          {vehicle.status !== 'available' && <div style={{ marginTop: 8 }}><ServerMessage text={`${vehicle.registration} is ${vehicle.status_label} — not available for checkout.`} /></div>}
          <label htmlFor="trip-purpose" style={labelStyle}>Purpose of trip</label>
          <select id="trip-purpose" value={purpose} onChange={e => setPurpose(e.target.value)} style={inputStyle}>
            {purposes.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          {purpose === otherValue && <textarea value={purposeNotes} onChange={e => setPurposeNotes(e.target.value)} rows={2} placeholder='"Other" needs a note explaining the trip' aria-label="Note explaining the trip" style={{ ...inputStyle, marginTop: 8, resize: 'vertical' }} />}
          <label htmlFor="trip-destination" style={labelStyle}>Destination</label>
          <input id="trip-destination" value={destination} onChange={e => setDestination(e.target.value)} placeholder="Where is the car going?" style={inputStyle} />
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <label htmlFor="trip-odo-out" style={labelStyle}>Odometer out (km)</label>
              <input id="trip-odo-out" value={odoOut} onChange={e => setOdoOut(e.target.value.replace(/[^0-9]/g, ''))} inputMode="numeric" placeholder="e.g. 45100" style={inputStyle} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Fuel</label>
              {fuelSelect(fuelOut, setFuelOut)}
            </div>
          </div>
          <label htmlFor="trip-expected" style={labelStyle}>Expected back (optional)</label>
          <input id="trip-expected" type="datetime-local" value={expected} onChange={e => setExpected(e.target.value)} style={inputStyle} />
          {checkbox(confirmOut, setConfirmOut, 'I took the car and saw its condition.')}
          {photoBlock('Photo of the car (optional)')}
          {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
          <button onClick={checkout} disabled={busy || vehicle.status !== 'available'} style={{ ...primaryBtn(busy || vehicle.status !== 'available'), marginTop: 14 }}><Send size={16} /> {busy ? 'Saving…' : 'Check the car out'}</button>
        </Card>
      )}

      <b style={{ color: C.ink, fontSize: 15 }}>{driver.trim() ? `Recent trips — ${driver.trim()}` : 'Recent trips'}</b>
      {tripsErr && <RetryBanner message={tripsErr} onRetry={() => loadTrips(driver)} />}
      {trips === null && !tripsErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {trips && trips.length === 0 && !tripsErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>{driver.trim() ? 'No trips under this name yet.' : 'Enter your name above to see your trips.'}</p>}
      {trips?.map(t => (
        <Card key={t.id} style={{ padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <b style={{ color: C.ink, fontSize: 14.5 }}>{t.registration} · {t.purpose_label}</b>
            <StatusPill status={t.status} label={t.status_label} />
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>
            {t.destination} · out {fmtWhen(t.checkout_at)}{t.checkin_at ? ` · in ${fmtWhen(t.checkin_at)}` : ''}
            {t.distance_km != null ? ` · ${t.distance_km} km` : ''}{t.flagged ? ` · ⚠ ${t.flag_reason}` : ''}
          </p>
        </Card>
      ))}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
