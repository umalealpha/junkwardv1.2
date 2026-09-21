"""nexus/vehicle_register.py — Pool-car Vehicle Register (CFO/EXCO 2026-07-16).

Replaces the paper access books for the cars parked at the office. A trip is a
checkout / check-in record with dual sign-off:

  * the driver signs on collection  (saw the car, took it),
  * the driver signs on return      (bringing it back in good condition),
  * the receptionist signs on return (confirms the condition).

Every trip must map to an approved business purpose. Damage on return
auto-flags the trip, alerts the fleet admin + CFO, and blocks the vehicle to
"Under maintenance" until it is cleared.

Endpoints (all under /api/v1/nexus/, IsAuthenticated):

    GET/POST  vehicles/                         list register / add a vehicle
    GET/PATCH vehicles/<id>/                    detail / edit (reg, odo, status)
    POST      vehicles/<id>/clear-maintenance/  free a maintenance vehicle
    GET       vehicle-register/board/           the live dashboard payload
    GET       vehicle-register/purposes/        approved purpose dropdown
    GET       vehicle-register/trips/           trip register (filters)
    POST      vehicle-register/checkout/        driver takes a car out
    POST      vehicle-register/trips/<id>/checkin/   driver returns it
    POST      vehicle-register/trips/<id>/signoff/   receptionist confirms
    POST      vehicle-register/trips/<id>/photo/      upload a condition photo
    GET       vehicle-register/photo/<id>/            stream a photo (gated)
"""
from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import escape
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import AuditLog
from .models import (
    FleetVehicle, FuelLevel, PhotoKind, TripPurpose, TripState,
    VehicleStatus, VehicleTrip, VehicleTripPhoto,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')) or None


def _actor_name(user) -> str:
    if not user or not user.is_authenticated:
        return 'Unknown'
    full = (user.get_full_name() or '').strip()
    return full or user.username


def _audit_vehicle(request, vehicle, description):
    """Explicit audit row for a FleetVehicle status change (the model itself is
    not AuditableMixin — trips are, but vehicle transitions ride alongside)."""
    AuditLog.objects.create(
        table_name='FleetVehicle', record_id=str(vehicle.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': vehicle.status, 'registration': vehicle.registration},
        user=(request.user if request.user.is_authenticated else None),
        ip_address=_client_ip(request), description=description,
    )


def _is_fleet_admin(user) -> bool:
    """Who may change the REGISTER itself — add/edit a vehicle, or release a
    damaged car back into service (CFO 2026-07-26: "unami and Dorothy admin").

    Deliberately narrow. Everyone still books cars out, brings them back and
    signs off returns — that is the daily flow and gating it would stop work.
    What is gated is the record the fuel-cash control reads (the odometer) and
    the maintenance block a damaged car sits behind.

    Superusers and omni administrators are always included, so the CFO can
    never be locked out of his own register.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    try:
        from core.models import get_user_profile
        p = get_user_profile(user)
        if p and getattr(p, 'is_administrator', False):
            return True
    except Exception:  # noqa: BLE001 — a profile lookup must never decide the gate
        logger.warning('fleet-admin check: profile lookup failed for %s',
                       getattr(user, 'username', '?'), exc_info=True)
    allowed = {e.strip().lower()
               for e in (getattr(settings, 'VEHICLE_FLEET_ADMIN_EMAILS', []) or [])
               if e.strip()}
    return bool(allowed) and (user.email or '').strip().lower() in allowed


# Shown to a non-admin who tries to change the register. Names the people to ask
# rather than dead-ending them on "forbidden".
_FLEET_ADMIN_ONLY = ('Only the fleet administrators may change the vehicle register — '
                     'ask Unami or Dorothy. Booking a car out and checking it back in '
                     'is unchanged.')


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _vehicle_or_none(pk):
    """Fetch a FleetVehicle by pk, tolerating a malformed UUID (→ None) rather
    than letting a bad request body raise a 500."""
    if not pk:
        return None
    try:
        return FleetVehicle.objects.filter(pk=pk).first()
    except (ValidationError, ValueError, TypeError):
        return None


# Condition photos are shown inline in the UI, so only real raster image types
# are accepted (SVG is excluded — it can carry script). Combined with a forced
# image content type + nosniff on serve, this closes the stored-XSS vector.
IMAGE_EXT_CONTENT_TYPE = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'webp': 'image/webp', 'heic': 'image/heic', 'heif': 'image/heif', 'gif': 'image/gif',
}


def _serialize_vehicle(v: FleetVehicle) -> dict:
    open_trip = next((t for t in getattr(v, '_open_trips', []) or []
                      if t.status == TripState.CHECKED_OUT), None)
    return {
        'id': str(v.id),
        'registration': v.registration,
        'make': v.make, 'model': v.model, 'year': v.year,
        'colour': v.colour, 'vin': v.vin, 'home_yard': v.home_yard,
        'condition_notes': v.condition_notes,
        'status': v.status, 'status_label': v.get_status_display(),
        'odometer_km': v.odometer_km,
        'open_trip': _serialize_trip(open_trip) if open_trip else None,
    }


def _serialize_trip(t: VehicleTrip, *, request=None) -> dict:
    return {
        'id': str(t.id),
        'vehicle_id': str(t.vehicle_id),
        'registration': t.vehicle.registration if t.vehicle_id else '',
        'driver_name': t.driver_name,
        'purpose': t.purpose, 'purpose_label': t.get_purpose_display(),
        'purpose_notes': t.purpose_notes,
        'destination': t.destination,
        'checkout_at': t.checkout_at.isoformat() if t.checkout_at else None,
        'odometer_out': t.odometer_out,
        'fuel_level_out': t.fuel_level_out,
        'pre_trip_notes': t.pre_trip_notes,
        'expected_return_at': t.expected_return_at.isoformat() if t.expected_return_at else None,
        'checkin_at': t.checkin_at.isoformat() if t.checkin_at else None,
        'odometer_in': t.odometer_in,
        'fuel_level_in': t.fuel_level_in,
        'driver_condition_confirm': t.driver_condition_confirm,
        'driver_return_confirm': t.driver_return_confirm,
        'damage_on_return': t.damage_on_return,
        'damage_notes': t.damage_notes,
        'receptionist_confirm': t.receptionist_confirm,
        'receptionist_signoff_at': t.receptionist_signoff_at.isoformat() if t.receptionist_signoff_at else None,
        'receptionist_notes': t.receptionist_notes,
        'status': t.status, 'status_label': t.get_status_display(),
        'is_overdue': t.is_overdue,
        'flagged': t.flagged, 'flag_reason': t.flag_reason,
        'distance_km': t.distance_km,
        'photos': [
            {
                'id': str(p.id), 'kind': p.kind, 'caption': p.caption,
                'url': (request.build_absolute_uri(f'/api/v1/nexus/vehicle-register/photo/{p.id}/')
                        if request else f'/api/v1/nexus/vehicle-register/photo/{p.id}/'),
            }
            for p in (t.photos.all() if t.pk else [])
        ],
        'created_at': t.created_at.isoformat() if t.created_at else None,
    }


def _notify_damage(trip: VehicleTrip, request):
    """Alert the fleet admin(s) + CFO/EXCO that a car came back damaged."""
    try:
        from core.notifications import send_html_with_cfo_cc
    except Exception:  # noqa: BLE001
        logger.warning('vehicle damage alert: notifications unavailable — '
                       'no alert sent for trip %s', trip.pk, exc_info=True)
        return
    to = list(getattr(settings, 'VEHICLE_FLEET_ADMIN_EMAILS', []) or [])
    v = trip.vehicle
    # Everything below is free text typed by a driver / receptionist and is
    # interpolated into an HTML email that lands with the fleet admin + CFO —
    # escape it so a stray "<" (or an injected tag) can't rewrite the alert.
    reg = escape(v.registration or '(unregistered)')
    veh = escape(' '.join(x for x in [v.make, v.model] if x))
    notes = escape((trip.damage_notes or '(no notes captured)').strip())
    driver = escape(trip.driver_name)
    purpose = escape(trip.get_purpose_display())
    destination = escape(trip.destination)
    when = timezone.localtime(trip.checkin_at or timezone.now()).strftime('%d %b %Y %H:%M')
    html = f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;color:#0D1B2A;max-width:560px">
  <div style="background:#0D1B2A;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#F4A623">
      Alpha Direct · Vehicle Register</div>
    <div style="font-size:20px;font-weight:700;margin-top:4px">⚠ Vehicle returned with damage</div>
  </div>
  <div style="border:1px solid #ECEEF2;border-top:none;padding:20px;border-radius:0 0 10px 10px">
    <p style="margin:0 0 12px">A pool car has been checked in with damage reported and has been
    automatically placed <b>Under maintenance</b> until it is cleared.</p>
    <table style="border-collapse:collapse;font-size:14px;width:100%">
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280">Vehicle</td><td><b>{reg}</b> — {veh or '—'}</td></tr>
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280">Driver</td><td>{driver}</td></tr>
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280">Purpose</td><td>{purpose}</td></tr>
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280">Destination</td><td>{destination}</td></tr>
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280">Returned</td><td>{when}</td></tr>
      <tr><td style="padding:4px 12px 4px 0;color:#6B7280;vertical-align:top">Damage</td>
          <td style="color:#B42318">{notes}</td></tr>
    </table>
    <p style="margin:16px 0 0;font-size:13px;color:#6B7280">
      Clear the vehicle back to service from the Vehicle Register once inspected.</p>
  </div>
</div>"""
    text = (f"Vehicle returned with damage: {reg} ({veh}). Driver {trip.driver_name}. "
            f"Damage: {notes}. Vehicle set to Under maintenance.")
    try:
        send_html_with_cfo_cc(
            f"⚠ Vehicle damage on return — {reg}", html, to,
            text_fallback=text,
        )
    except Exception:  # noqa: BLE001 — a failed alert must not fail the check-in,
        # but it must not disappear either: this is a control alert to the CFO.
        logger.warning('vehicle damage alert failed to send for trip %s (%s)',
                       trip.pk, reg, exc_info=True)


# ---------------------------------------------------------------------------
# Vehicles (register)
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def vehicles(request):
    """GET — the vehicle register; POST — add a vehicle (manual data-entry)."""
    if request.method == 'GET':
        qs = list(FleetVehicle.objects.all())
        open_map: dict[str, list] = {}
        for t in (VehicleTrip.objects.filter(status=TripState.CHECKED_OUT)
                  .select_related('vehicle').prefetch_related('photos')):
            open_map.setdefault(str(t.vehicle_id), []).append(t)
        for v in qs:
            v._open_trips = open_map.get(str(v.id), [])
        return Response({
            'count': len(qs),
            'vehicles': [_serialize_vehicle(v) for v in qs],
        })

    # POST — add a vehicle (fleet admins only)
    if not _is_fleet_admin(request.user):
        return Response({'detail': _FLEET_ADMIN_ONLY}, status=403)
    d = request.data if isinstance(request.data, dict) else {}
    reg = (d.get('registration') or '').strip()
    if not reg:
        return Response({'detail': 'registration is required.'}, status=400)
    if FleetVehicle.objects.filter(registration__iexact=reg).exists():
        return Response({'detail': f'A vehicle with registration {reg} already exists.'}, status=400)
    v = FleetVehicle.objects.create(
        registration=reg,
        make=(d.get('make') or '').strip(),
        model=(d.get('model') or '').strip(),
        year=_int_or_none(d.get('year')),
        colour=(d.get('colour') or '').strip(),
        vin=(d.get('vin') or '').strip(),
        home_yard=(d.get('home_yard') or 'Head Office').strip(),
        condition_notes=(d.get('condition_notes') or '').strip(),
        odometer_km=_int_or_none(d.get('odometer_km')),
        status=VehicleStatus.AVAILABLE,
    )
    _audit_vehicle(request, v, f'Added vehicle {reg} to the register')
    return Response(_serialize_vehicle(v), status=201)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def vehicle_detail(request, pk):
    v = FleetVehicle.objects.filter(pk=pk).first()
    if v is None:
        raise Http404('Vehicle not found.')
    if request.method == 'GET':
        v._open_trips = list(VehicleTrip.objects.filter(
            vehicle=v, status=TripState.CHECKED_OUT))
        return Response(_serialize_vehicle(v))

    # PATCH — editing the register (incl. the odometer the fuel-cash control
    # reads) is fleet-admin only.
    if not _is_fleet_admin(request.user):
        return Response({'detail': _FLEET_ADMIN_ONLY}, status=403)
    d = request.data if isinstance(request.data, dict) else {}
    for f in ('make', 'model', 'colour', 'vin', 'home_yard', 'condition_notes'):
        if f in d:
            setattr(v, f, (d.get(f) or '').strip())
    if 'registration' in d and (d.get('registration') or '').strip():
        new_reg = d['registration'].strip()
        if (FleetVehicle.objects.filter(registration__iexact=new_reg)
                .exclude(pk=v.pk).exists()):
            return Response({'detail': f'Registration {new_reg} already in use.'}, status=400)
        v.registration = new_reg
    if 'year' in d:
        v.year = _int_or_none(d.get('year'))
    if 'odometer_km' in d:
        new_odo = _int_or_none(d.get('odometer_km'))
        # The odometer is the basis of the km-per-vehicle report and of the
        # fuel-cash control (cash issued is reconciled against distance). A
        # manual edit must never rewind it — check-in already enforces this on
        # the trip path, so the edit form has to enforce it too or the control
        # is bypassable from the vehicle-detail screen.
        if new_odo is None:
            # A blank/garbage value must not NULL out a reading that exists:
            # clearing it first would make the rewind check below vacuous and
            # any low value would then be accepted. Vehicles that have no
            # reading yet stay as they are.
            if v.odometer_km is not None:
                return Response({'detail': 'Odometer must be a number — an existing '
                                           'reading cannot be cleared.'}, status=400)
        else:
            if new_odo < 0:
                return Response({'detail': 'Odometer reading cannot be negative.'}, status=400)
            if v.odometer_km is not None and new_odo < int(v.odometer_km):
                return Response({'detail': f'Odometer ({new_odo}) is below the current reading '
                                           f'({int(v.odometer_km)}) — the odometer cannot be '
                                           f'wound back.'}, status=400)
            v.odometer_km = new_odo
    # Status: only manual transitions are allowed. 'out' is system-driven by a
    # checkout and must never be set by hand; block it.
    if 'status' in d:
        new_status = (d.get('status') or '').strip()
        if new_status == VehicleStatus.OUT:
            return Response({'detail': "Can't set 'Out' manually — check a vehicle out instead."},
                            status=400)
        if new_status not in dict(VehicleStatus.choices):
            return Response({'detail': 'Invalid status.'}, status=400)
        # Never let a manual status change free a vehicle that still has a live
        # trip — checked out OR returned-but-not-signed-off. Otherwise the car
        # could be checked out a second time while a trip is still open/pending.
        if new_status != VehicleStatus.OUT and VehicleTrip.objects.filter(
                vehicle=v,
                status__in=[TripState.CHECKED_OUT, TripState.RETURNED_PENDING]).exists():
            return Response({'detail': 'Vehicle has a live trip (out or awaiting sign-off) — '
                                       'complete it before changing the status.'}, status=400)
        v.status = new_status
    v.save()
    _audit_vehicle(request, v, f'Edited vehicle {v.registration}')
    return Response(_serialize_vehicle(v))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clear_maintenance(request, pk):
    """Return a maintenance vehicle to service (fleet admin, after inspection).

    Gated: a damaged car is blocked automatically on check-in, so whoever can
    unblock it is the whole control. Before this, the driver who damaged the car
    could clear it themselves.
    """
    if not _is_fleet_admin(request.user):
        return Response({'detail': 'Only the fleet administrators may release a vehicle '
                                   'from maintenance — ask Unami or Dorothy once the '
                                   'car has been inspected.'}, status=403)
    v = FleetVehicle.objects.filter(pk=pk).first()
    if v is None:
        raise Http404('Vehicle not found.')
    if v.status != VehicleStatus.MAINTENANCE:
        return Response({'detail': 'Vehicle is not under maintenance.'}, status=400)
    v.status = VehicleStatus.AVAILABLE
    v.save()
    _audit_vehicle(request, v, f'Cleared {v.registration} from maintenance — back in service')
    return Response(_serialize_vehicle(v))


# ---------------------------------------------------------------------------
# Reference — approved purposes
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def purposes(request):
    return Response({
        'purposes': [{'value': c[0], 'label': c[1]} for c in TripPurpose.choices],
        'fuel_levels': [{'value': c[0], 'label': c[1]} for c in FuelLevel.choices],
        'other_value': TripPurpose.OTHER,
    })


# ---------------------------------------------------------------------------
# The live board (the headache-fix dashboard payload)
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def register_board(request):
    veh = list(FleetVehicle.objects.all())
    open_trips = list(VehicleTrip.objects.filter(status=TripState.CHECKED_OUT)
                      .select_related('vehicle').prefetch_related('photos'))
    open_by_veh = {str(t.vehicle_id): t for t in open_trips}
    for v in veh:
        v._open_trips = [open_by_veh[str(v.id)]] if str(v.id) in open_by_veh else []

    overdue = [t for t in open_trips if t.is_overdue]
    pending_signoff = list(VehicleTrip.objects.filter(status=TripState.RETURNED_PENDING)
                           .select_related('vehicle').prefetch_related('photos'))
    damage_register = list(VehicleTrip.objects.filter(damage_on_return=True)
                           .select_related('vehicle').prefetch_related('photos')[:50])

    counts = {s: 0 for s, _ in VehicleStatus.choices}
    for v in veh:
        counts[v.status] = counts.get(v.status, 0) + 1

    return Response({
        # Lets the UI hide "Add vehicle" / "Clear to service" for people who
        # aren't fleet admins, instead of showing a button that 403s.
        'can_manage': _is_fleet_admin(request.user),
        'stats': {
            'total': len(veh),
            'available': counts.get(VehicleStatus.AVAILABLE, 0),
            'out': counts.get(VehicleStatus.OUT, 0),
            'maintenance': counts.get(VehicleStatus.MAINTENANCE, 0),
            'retired': counts.get(VehicleStatus.RETIRED, 0),
            'overdue': len(overdue),
            'pending_signoff': len(pending_signoff),
        },
        'vehicles': [_serialize_vehicle(v) for v in veh],
        'overdue': [_serialize_trip(t, request=request) for t in overdue],
        'pending_signoff': [_serialize_trip(t, request=request) for t in pending_signoff],
        'damage_register': [_serialize_trip(t, request=request) for t in damage_register],
    })


# ---------------------------------------------------------------------------
# Trips register
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trips(request):
    qs = VehicleTrip.objects.select_related('vehicle').prefetch_related('photos')
    q = request.query_params
    if q.get('vehicle'):
        try:
            qs = qs.filter(vehicle_id=uuid.UUID(str(q['vehicle'])))
        except (ValueError, TypeError):
            qs = qs.none()   # unknown/malformed vehicle id → no rows (not a 500)
    if q.get('status'):
        qs = qs.filter(status=q['status'])
    if q.get('driver'):
        qs = qs.filter(driver_name__icontains=q['driver'])
    if q.get('flagged') in ('1', 'true', 'yes'):
        qs = qs.filter(flagged=True)
    rows = [_serialize_trip(t, request=request) for t in qs[:500]]
    return Response({'count': len(rows), 'trips': rows,
                     'can_correct_odometer': _may_correct_odometer(request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checkout(request):
    """Driver takes a car out. Blocks if the vehicle is not Available; every
    trip must carry an approved purpose (OTHER forces a note + flags)."""
    d = request.data if isinstance(request.data, dict) else {}
    v = _vehicle_or_none(d.get('vehicle'))
    if v is None:
        return Response({'detail': 'Select a valid vehicle.'}, status=400)
    if v.status != VehicleStatus.AVAILABLE:
        return Response({'detail': f'{v.registration} is {v.get_status_display()} — not available for checkout.'},
                        status=400)

    driver_name = (d.get('driver_name') or _actor_name(request.user)).strip()
    if not driver_name:
        return Response({'detail': 'driver_name is required.'}, status=400)

    purpose = (d.get('purpose') or '').strip()
    if purpose not in dict(TripPurpose.choices):
        return Response({'detail': 'Select an approved purpose for the trip.'}, status=400)
    purpose_notes = (d.get('purpose_notes') or '').strip()
    if purpose == TripPurpose.OTHER and not purpose_notes:
        return Response({'detail': 'Purpose "Other" requires a note explaining the trip.'}, status=400)

    destination = (d.get('destination') or '').strip()
    if not destination:
        return Response({'detail': 'destination is required.'}, status=400)

    if not bool(d.get('driver_condition_confirm')):
        return Response({'detail': 'The driver must confirm they took the car and saw its condition.'},
                        status=400)

    odo_out = _int_or_none(d.get('odometer_out'))
    if odo_out is not None and odo_out < 0:
        return Response({'detail': 'Odometer reading cannot be negative.'}, status=400)
    if odo_out is not None and v.odometer_km is not None and odo_out < int(v.odometer_km):
        return Response({'detail': f'Odometer out ({odo_out}) is below the vehicle\'s last '
                                   f'reading ({int(v.odometer_km)}).'}, status=400)

    fuel_out = (d.get('fuel_level_out') or '').strip()
    if fuel_out and fuel_out not in dict(FuelLevel.choices):
        return Response({'detail': 'Invalid fuel level.'}, status=400)

    expected_raw = d.get('expected_return_at') or None
    expected = None
    if expected_raw:
        expected = parse_datetime(str(expected_raw))
        if expected is None:
            return Response({'detail': 'Expected return is not a valid date/time.'}, status=400)

    flagged = purpose == TripPurpose.OTHER
    with transaction.atomic():
        # Re-check status inside the lock to avoid a double-checkout race.
        v = FleetVehicle.objects.select_for_update().get(pk=v.pk)
        if v.status != VehicleStatus.AVAILABLE:
            return Response({'detail': f'{v.registration} was just taken — no longer available.'},
                            status=409)
        trip = VehicleTrip(
            vehicle=v,
            driver_user=(request.user if request.user.is_authenticated else None),
            driver_name=driver_name[:120],
            purpose=purpose, purpose_notes=purpose_notes, destination=destination[:200],
            checkout_at=timezone.now(), odometer_out=odo_out, fuel_level_out=fuel_out,
            driver_condition_confirm=True,
            pre_trip_notes=(d.get('pre_trip_notes') or '').strip(),
            expected_return_at=expected,
            gps_checkout_lat=None, gps_checkout_lng=None,
            status=TripState.CHECKED_OUT,
            flagged=flagged, flag_reason=('Purpose: Other' if flagged else ''),
            created_by=(request.user if request.user.is_authenticated else None),
        )
        trip.save(audit_user=request.user if request.user.is_authenticated else None,
                  audit_ip=_client_ip(request),
                  audit_description=f'Checkout {v.registration} → {driver_name} ({trip.get_purpose_display()})')
        v.status = VehicleStatus.OUT
        v.save()
        _audit_vehicle(request, v, f'{v.registration} checked out by {driver_name}')
    return Response(_serialize_trip(trip, request=request), status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trip_checkin(request, pk):
    """Driver returns the car — the driver's return signature. Odometer in must
    be >= odometer out. Damage auto-flags + blocks the vehicle to maintenance."""
    trip = VehicleTrip.objects.select_related('vehicle').filter(pk=pk).first()
    if trip is None:
        raise Http404('Trip not found.')
    if trip.status != TripState.CHECKED_OUT:
        return Response({'detail': f'Trip is already {trip.get_status_display()}.'}, status=400)

    d = request.data if isinstance(request.data, dict) else {}
    if not bool(d.get('driver_return_confirm')):
        return Response({'detail': 'The driver must confirm the car is returned in good condition.'},
                        status=400)

    odo_in = _int_or_none(d.get('odometer_in'))
    if odo_in is None:
        return Response({'detail': 'odometer_in is required on return.'}, status=400)
    if odo_in < 0:
        return Response({'detail': 'Odometer reading cannot be negative.'}, status=400)
    # Floor the return reading. Prefer odometer_out; if it was left blank at
    # checkout, fall back to the vehicle's last known reading so a low value
    # can't rewind the register.
    floor = trip.odometer_out
    if floor is None and trip.vehicle.odometer_km is not None:
        floor = int(trip.vehicle.odometer_km)
    if floor is not None and odo_in < floor:
        return Response({'detail': f'Odometer in ({odo_in}) cannot be less than the last '
                                   f'reading ({floor}).'}, status=400)

    fuel_in = (d.get('fuel_level_in') or '').strip()
    if fuel_in and fuel_in not in dict(FuelLevel.choices):
        return Response({'detail': 'Invalid fuel level.'}, status=400)

    damage = bool(d.get('damage_on_return'))
    damage_notes = (d.get('damage_notes') or '').strip()
    if damage and not damage_notes:
        return Response({'detail': 'Describe the damage in damage_notes.'}, status=400)

    with transaction.atomic():
        trip = VehicleTrip.objects.select_for_update().select_related('vehicle').get(pk=trip.pk)
        if trip.status != TripState.CHECKED_OUT:
            return Response({'detail': 'Trip was just checked in elsewhere.'}, status=409)
        trip.checkin_at = timezone.now()
        trip.odometer_in = odo_in
        trip.fuel_level_in = fuel_in
        trip.driver_return_confirm = True
        trip.damage_on_return = damage
        trip.damage_notes = damage_notes
        trip.status = TripState.RETURNED_PENDING
        if damage:
            trip.flagged = True
            trip.flag_reason = (trip.flag_reason + '; ' if trip.flag_reason else '') + 'Damage on return'
        trip.save(audit_user=request.user if request.user.is_authenticated else None,
                  audit_ip=_client_ip(request),
                  audit_description=f'Check-in {trip.vehicle.registration} '
                                    f'({"DAMAGE" if damage else "no damage"})')

        v = trip.vehicle
        # Current odometer follows the return reading (manual until Cartrack),
        # but never moves backwards.
        existing = int(v.odometer_km) if v.odometer_km is not None else 0
        v.odometer_km = max(existing, odo_in)
        if damage:
            v.status = VehicleStatus.MAINTENANCE
            v.save()
            _audit_vehicle(request, v, f'{v.registration} → Under maintenance (damage on return)')
        else:
            v.save()   # stays OUT until the receptionist signs it off (then freed)

    if damage:
        _notify_damage(trip, request)
    return Response(_serialize_trip(trip, request=request))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trip_signoff(request, pk):
    """Receptionist (Wame) confirms condition on return. Only after the driver's
    return confirmation. Closes the trip; frees the vehicle when undamaged."""
    trip = VehicleTrip.objects.select_related('vehicle').filter(pk=pk).first()
    if trip is None:
        raise Http404('Trip not found.')
    if trip.status != TripState.RETURNED_PENDING:
        return Response({'detail': f'Trip is {trip.get_status_display()} — nothing to sign off.'},
                        status=400)
    if not trip.driver_return_confirm:
        return Response({'detail': 'Driver has not confirmed the return yet.'}, status=400)

    # Segregation of duties: the person confirming the condition on return must
    # not be the driver who took the car out — that is the whole point of the
    # dual sign-off. (Reception, or any other staff member, signs.)
    if (trip.driver_user_id and request.user.is_authenticated
            and request.user.id == trip.driver_user_id):
        return Response({'detail': 'The driver cannot sign off their own return — a '
                                   'different person must confirm the condition.'}, status=403)

    # Optional lock to named reception accounts (empty list = any staff, recorded).
    allowed = [e.lower() for e in getattr(settings, 'VEHICLE_RECEPTIONIST_EMAILS', []) or []]
    if allowed and (request.user.email or '').lower() not in allowed:
        return Response({'detail': 'Only reception may sign off the return.'}, status=403)

    if not bool(request.data.get('receptionist_confirm')):
        return Response({'detail': 'Confirm you have checked the vehicle condition.'}, status=400)

    with transaction.atomic():
        trip = VehicleTrip.objects.select_for_update().select_related('vehicle').get(pk=trip.pk)
        if trip.status != TripState.RETURNED_PENDING:
            return Response({'detail': 'Trip was just signed off elsewhere.'}, status=409)
        trip.receptionist_confirm = True
        trip.receptionist_user = request.user if request.user.is_authenticated else None
        trip.receptionist_signoff_at = timezone.now()
        trip.receptionist_notes = (request.data.get('receptionist_notes') or '').strip()
        trip.status = TripState.CLOSED
        trip.save(audit_user=request.user if request.user.is_authenticated else None,
                  audit_ip=_client_ip(request),
                  audit_description=f'Reception sign-off {trip.vehicle.registration} by '
                                    f'{_actor_name(request.user)}')
        v = trip.vehicle
        # Free the vehicle only when it wasn't damaged (damaged cars stay in
        # maintenance until explicitly cleared).
        if not trip.damage_on_return and v.status == VehicleStatus.OUT:
            v.status = VehicleStatus.AVAILABLE
            v.save()
            _audit_vehicle(request, v, f'{v.registration} back in service (signed off)')
    return Response(_serialize_trip(trip, request=request))


# ---------------------------------------------------------------------------
# Odometer correction (fat-finger fixes)
# ---------------------------------------------------------------------------
def _may_correct_odometer(user) -> bool:
    """Who may fix a mistyped odometer reading. A controlled action (it can move
    the vehicle's current reading DOWN), so it is NOT open to all staff. Anyone
    who may change the register (superuser / omni admin / fleet admin) can, PLUS
    the named corrector accounts — reception, e.g. Wame — so the person who spots
    the typo can fix it. Grant via VEHICLE_ODOMETER_CORRECTOR_EMAILS."""
    if _is_fleet_admin(user):
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    allowed = {e.strip().lower()
               for e in (getattr(settings, 'VEHICLE_ODOMETER_CORRECTOR_EMAILS', []) or [])
               if e.strip()}
    return bool(email) and email in allowed


def _rederive_vehicle_odometer(vehicle) -> int | None:
    """The vehicle's current reading = the highest genuine reading across all of
    its trips. Correcting a bogus high reading must be able to lower it, so this
    is recomputed from scratch (NOT the never-rewind max used on check-in)."""
    vals = [int(x)
            for tr in VehicleTrip.objects.filter(vehicle=vehicle)
            for x in (tr.odometer_out, tr.odometer_in)
            if x is not None]
    return max(vals) if vals else None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trip_correct_odometer(request, pk):
    """Fix a mistyped odometer reading on a trip (out and/or in), then re-derive
    the vehicle's current reading. Restricted to corrector accounts. The original
    value is preserved in the audit log (AuditableMixin captures old_values)."""
    if not _may_correct_odometer(request.user):
        return Response({'detail': 'You do not have access to correct odometer readings. '
                                   'Ask the fleet admin.'}, status=403)

    trip = VehicleTrip.objects.select_related('vehicle').filter(pk=pk).first()
    if trip is None:
        raise Http404('Trip not found.')

    d = request.data if isinstance(request.data, dict) else {}
    reason = (d.get('reason') or '').strip()
    if not reason:
        return Response({'detail': 'A short reason for the correction is required.'}, status=400)

    new_out = _int_or_none(d.get('odometer_out')) if 'odometer_out' in d else trip.odometer_out
    new_in = _int_or_none(d.get('odometer_in')) if 'odometer_in' in d else trip.odometer_in
    if 'odometer_out' not in d and 'odometer_in' not in d:
        return Response({'detail': 'Provide a corrected odometer_out and/or odometer_in.'},
                        status=400)
    for label, val in (('out', new_out), ('in', new_in)):
        if val is not None and val < 0:
            return Response({'detail': f'Odometer {label} cannot be negative.'}, status=400)
    if new_out is not None and new_in is not None and new_in < new_out:
        return Response({'detail': f'Odometer in ({new_in}) cannot be less than '
                                   f'odometer out ({new_out}).'}, status=400)

    old_out, old_in = trip.odometer_out, trip.odometer_in
    with transaction.atomic():
        trip.odometer_out = new_out
        trip.odometer_in = new_in
        trip.save(audit_user=request.user if request.user.is_authenticated else None,
                  audit_ip=_client_ip(request),
                  audit_description=(f'Odometer correction {trip.vehicle.registration} '
                                     f'(out {old_out}->{new_out}, in {old_in}->{new_in}) '
                                     f'by {_actor_name(request.user)} — {reason}'))
        v = trip.vehicle
        new_odo = _rederive_vehicle_odometer(v)
        if new_odo is not None and new_odo != v.odometer_km:
            old_odo = v.odometer_km
            v.odometer_km = new_odo
            v.save()
            _audit_vehicle(request, v, f'{v.registration} current odometer re-derived after '
                                       f'correction ({old_odo} -> {new_odo})')
    return Response(_serialize_trip(trip, request=request))


# ---------------------------------------------------------------------------
# Condition photos
# ---------------------------------------------------------------------------
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def trip_photo_upload(request, pk):
    trip = VehicleTrip.objects.filter(pk=pk).first()
    if trip is None:
        raise Http404('Trip not found.')
    f = request.FILES.get('image') or request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach an image in the "image" field.'}, status=400)
    # Only real raster image types (SVG excluded — it can carry script). This +
    # the forced content type / nosniff on serve closes the stored-XSS vector.
    ext = (f.name.rsplit('.', 1)[-1].lower() if f.name and '.' in f.name else '')
    if ext not in IMAGE_EXT_CONTENT_TYPE:
        return Response({'detail': 'Only image files are allowed (jpg, png, webp, heic, gif).'},
                        status=400)
    if f.size and f.size > 20 * 1024 * 1024:
        return Response({'detail': 'Image too large (max 20 MB).'}, status=400)
    kind = (request.data.get('kind') or PhotoKind.PRE_TRIP).strip()
    if kind not in dict(PhotoKind.choices):
        kind = PhotoKind.PRE_TRIP
    photo = VehicleTripPhoto.objects.create(
        trip=trip, kind=kind, image=f,
        caption=(request.data.get('caption') or '').strip()[:200],
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    return Response({
        'id': str(photo.id), 'kind': photo.kind, 'caption': photo.caption,
        'url': request.build_absolute_uri(f'/api/v1/nexus/vehicle-register/photo/{photo.id}/'),
    }, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trip_photo(request, pk):
    """Stream a condition photo (auth-gated; never a raw FileField.url)."""
    p = VehicleTripPhoto.objects.filter(pk=pk).first()
    if p is None or not p.image:
        raise Http404('Photo not found.')
    name = p.image.name.split('/')[-1] or 'photo'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    # Force a safe image content type (never text/html) + nosniff so a file
    # masquerading as an image can't be sniffed/executed as script inline.
    ctype = IMAGE_EXT_CONTENT_TYPE.get(ext, 'application/octet-stream')
    resp = FileResponse(p.image.open('rb'), content_type=ctype, filename=name)
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp
