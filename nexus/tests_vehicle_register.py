"""Tests for the Vehicle Register (pool-car checkout / check-in, dual sign-off).

Run: DB_ENGINE=sqlite SECRET_KEY=devtest python manage.py test nexus.tests_vehicle_register
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from django.test import TestCase, override_settings

from core.models import AuditLog
from .models import (
    FleetVehicle, TripPurpose, TripState, VehicleStatus, VehicleTrip,
)


# Pin the fleet-admin list rather than leaning on the settings.py default, so a
# machine that sets VEHICLE_FLEET_ADMIN_EMAILS in its environment can't turn the
# gate tests green or red for the wrong reason.
@override_settings(VEHICLE_FLEET_ADMIN_EMAILS=['ubutale@alphadirect.co.bw'])
class VehicleRegisterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('driver1', 'driver1@alphadirect.co.bw', 'x',
                                              first_name='Kabout', last_name='Moremi')
        self.reception = User.objects.create_user('wame', 'wame@alphadirect.co.bw', 'x')
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        # A distinct actor for reception sign-off (segregation of duties).
        self.rc = APIClient()
        self.rc.force_authenticate(self.reception)
        # Fleet admin (Unami/Dorothy in prod) — the only role allowed to change
        # the register itself. Ordinary staff still book cars out and back in.
        self.fleet_admin = User.objects.create_user(
            'unami', 'ubutale@alphadirect.co.bw', 'x', first_name='Unami', last_name='Butale')
        self.fa = APIClient()
        self.fa.force_authenticate(self.fleet_admin)

    # ---- vehicle add / list ------------------------------------------------
    def _add_vehicle(self, reg='B 123 ABC', odo=10000):
        """Added as the FLEET ADMIN — adding a vehicle is a gated action."""
        r = self.fa.post('/api/v1/nexus/vehicles/', {
            'registration': reg, 'make': 'Toyota', 'model': 'Hilux',
            'year': 2022, 'colour': 'White', 'odometer_km': odo,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_add_and_list_vehicle(self):
        v = self._add_vehicle()
        self.assertEqual(v['status'], VehicleStatus.AVAILABLE)
        r = self.c.get('/api/v1/nexus/vehicles/')
        self.assertEqual(r.json()['count'], 1)

    def test_duplicate_registration_rejected(self):
        self._add_vehicle('B 1 AAA')
        r = self.fa.post('/api/v1/nexus/vehicles/', {'registration': 'b 1 aaa'}, format='json')
        self.assertEqual(r.status_code, 400)

    # ---- checkout gates ----------------------------------------------------
    def _checkout(self, vid, **over):
        body = {
            'vehicle': vid, 'driver_name': 'Kabo M', 'purpose': TripPurpose.CUSTOMER_VISITS,
            'destination': 'Choppies Riverwalk', 'driver_condition_confirm': True,
            'odometer_out': 10000,
        }
        body.update(over)
        return self.c.post('/api/v1/nexus/vehicle-register/checkout/', body, format='json')

    def test_checkout_success_sets_vehicle_out(self):
        v = self._add_vehicle()
        r = self._checkout(v['id'])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], TripState.CHECKED_OUT)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.OUT)

    def test_cannot_checkout_when_not_available(self):
        v = self._add_vehicle()
        self._checkout(v['id'])
        r = self._checkout(v['id'])   # already out
        self.assertEqual(r.status_code, 400)

    def test_other_purpose_requires_notes_and_flags(self):
        v = self._add_vehicle()
        r = self._checkout(v['id'], purpose=TripPurpose.OTHER, purpose_notes='')
        self.assertEqual(r.status_code, 400)
        r = self._checkout(v['id'], purpose=TripPurpose.OTHER, purpose_notes='Personal errand approved')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()['flagged'])

    def test_checkout_requires_driver_confirm(self):
        v = self._add_vehicle()
        r = self._checkout(v['id'], driver_condition_confirm=False)
        self.assertEqual(r.status_code, 400)

    def test_checkout_requires_purpose_and_destination(self):
        v = self._add_vehicle()
        self.assertEqual(self._checkout(v['id'], purpose='').status_code, 400)
        self.assertEqual(self._checkout(v['id'], destination='').status_code, 400)

    def test_odometer_out_below_last_reading_rejected(self):
        v = self._add_vehicle(odo=50000)
        r = self._checkout(v['id'], odometer_out=40000)
        self.assertEqual(r.status_code, 400)

    # ---- check-in ----------------------------------------------------------
    def _open_trip(self, odo=10000):
        v = self._add_vehicle(odo=odo)
        r = self._checkout(v['id'], odometer_out=odo)
        return v, r.json()

    def test_checkin_requires_return_confirm(self):
        _, t = self._open_trip()
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 10100, 'driver_return_confirm': False}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_checkin_odometer_in_below_out_rejected(self):
        _, t = self._open_trip(odo=10000)
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 9000, 'driver_return_confirm': True}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_checkin_no_damage_pending_signoff(self):
        v, t = self._open_trip(odo=10000)
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 10150, 'driver_return_confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], TripState.RETURNED_PENDING)
        self.assertEqual(r.json()['distance_km'], 150)
        veh = FleetVehicle.objects.get(pk=v['id'])
        self.assertEqual(veh.status, VehicleStatus.OUT)         # still out until signed off
        self.assertEqual(veh.odometer_km, 10150)                # current odo updated

    # ---- dual sign-off -----------------------------------------------------
    def test_full_lifecycle_closes_and_frees_vehicle(self):
        v, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10100, 'driver_return_confirm': True}, format='json')
        r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                         {'receptionist_confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], TripState.CLOSED)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.AVAILABLE)

    def test_signoff_blocked_before_checkin(self):
        _, t = self._open_trip()
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                        {'receptionist_confirm': True}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_signoff_requires_confirm(self):
        _, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10100, 'driver_return_confirm': True}, format='json')
        r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                         {'receptionist_confirm': False}, format='json')
        self.assertEqual(r.status_code, 400)

    # ---- damage ------------------------------------------------------------
    def test_damage_blocks_vehicle_and_flags(self):
        v, t = self._open_trip()
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 10100, 'driver_return_confirm': True,
                         'damage_on_return': True, 'damage_notes': 'Scratched rear bumper'},
                        format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['flagged'])
        veh = FleetVehicle.objects.get(pk=v['id'])
        self.assertEqual(veh.status, VehicleStatus.MAINTENANCE)
        # sign-off closes the trip but the vehicle stays in maintenance
        self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                     {'receptionist_confirm': True}, format='json')
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.MAINTENANCE)
        # clear it
        r = self.fa.post(f"/api/v1/nexus/vehicles/{v['id']}/clear-maintenance/", {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.AVAILABLE)

    def test_damage_requires_notes(self):
        _, t = self._open_trip()
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 10100, 'driver_return_confirm': True,
                         'damage_on_return': True, 'damage_notes': ''}, format='json')
        self.assertEqual(r.status_code, 400)

    # ---- board + overdue ---------------------------------------------------
    def test_board_reports_overdue(self):
        v, t = self._open_trip()
        trip = VehicleTrip.objects.get(pk=t['id'])
        trip.expected_return_at = timezone.now() - timedelta(hours=2)
        trip.save()
        r = self.c.get('/api/v1/nexus/vehicle-register/board/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['stats']['out'], 1)
        self.assertEqual(body['stats']['overdue'], 1)
        self.assertEqual(len(body['overdue']), 1)
        self.assertTrue(body['overdue'][0]['is_overdue'])

    def test_manual_status_cannot_be_out(self):
        v = self._add_vehicle()
        r = self.fa.patch(f"/api/v1/nexus/vehicles/{v['id']}/", {'status': 'out'}, format='json')
        self.assertEqual(r.status_code, 400)

    # ---- reference ---------------------------------------------------------
    def test_purposes_list(self):
        r = self.c.get('/api/v1/nexus/vehicle-register/purposes/')
        body = r.json()
        # 19 approved reasons + Other
        self.assertEqual(len(body['purposes']), 20)
        self.assertEqual(body['other_value'], TripPurpose.OTHER)

    # ---- audit -------------------------------------------------------------
    def test_checkout_writes_audit(self):
        v = self._add_vehicle()
        before = AuditLog.objects.count()
        self._checkout(v['id'])
        self.assertGreater(AuditLog.objects.count(), before)
        self.assertTrue(AuditLog.objects.filter(table_name='VehicleTrip').exists())

    # ---- reports + branded export -----------------------------------------
    def test_reports_and_xlsx_export(self):
        v, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10200, 'driver_return_confirm': True}, format='json')
        r = self.c.get('/api/v1/nexus/vehicle-register/reports/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['totals']['trips'], 1)
        self.assertEqual(body['totals']['km'], 200)
        self.assertTrue(any(p['trips'] == 1 for p in body['per_purpose']))
        x = self.c.get('/api/v1/nexus/vehicle-register/reports/export/')
        self.assertEqual(x.status_code, 200)
        self.assertIn('spreadsheetml', x['Content-Type'])

    def test_export_carries_the_logo_on_every_sheet(self):
        """The branded export must ship the real logo artwork, not just brand
        colours — a silently missing image is what went wrong on the payslip."""
        import io
        import openpyxl
        from .vehicle_reports import _logo_path

        self.assertIsNotNone(_logo_path(), 'logo artwork is missing from the repo')
        self._add_vehicle()
        x = self.c.get('/api/v1/nexus/vehicle-register/reports/export/')
        self.assertEqual(x.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(x.content))
        self.assertEqual(len(wb.sheetnames), 5)
        for name in wb.sheetnames:
            ws = wb[name]
            self.assertEqual(len(ws._images), 1, f'{name} has no logo image')
            img = ws._images[0]
            # Aspect ratio must survive the resize (reading .width after setting
            # .height is the classic stretch bug).
            self.assertAlmostEqual(img.width / img.height, 2062 / 630, delta=0.15)
        # Title/subtitle sit below the logo band so the artwork can't cover them.
        self.assertIn('Vehicle Register', str(wb['Summary']['A2'].value))

    def test_odometer_cannot_be_wound_back_by_edit(self):
        """The km report and the fuel-cash control both read this field, so a
        manual edit must not rewind it (check-in already refuses to)."""
        v = self._add_vehicle(odo=10000)
        r = self.fa.patch(f"/api/v1/nexus/vehicles/{v['id']}/",
                         {'odometer_km': 9000}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('wound back', r.json()['detail'])
        # A blank / null / garbage value must not NULL the reading either — if
        # it could, the rewind check above becomes vacuous and any low number
        # would then be accepted on the next PATCH.
        for blank in ('', None, 'abc'):
            r = self.fa.patch(f"/api/v1/nexus/vehicles/{v['id']}/",
                             {'odometer_km': blank}, format='json')
            self.assertEqual(r.status_code, 400, f'{blank!r} → {r.content}')
            self.assertIn('cannot be cleared', r.json()['detail'])
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).odometer_km, 10000)

        # Forward is fine.
        r = self.fa.patch(f"/api/v1/nexus/vehicles/{v['id']}/",
                         {'odometer_km': 10500}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['odometer_km'], 10500)

    def test_damage_alert_escapes_free_text(self):
        """Driver-typed damage notes land in an HTML email to the fleet admin +
        CFO — a stray tag must not be able to rewrite that alert."""
        from django.core import mail
        from django.test import override_settings
        from .vehicle_register import _notify_damage

        v, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10200, 'driver_return_confirm': True,
                     'damage_on_return': True,
                     'damage_notes': '<img src=x onerror=alert(1)>dented'},
                    format='json')
        trip = VehicleTrip.objects.get(pk=t['id'])
        mail.outbox = []
        with override_settings(VEHICLE_FLEET_ADMIN_EMAILS=['fleet@alphadirect.co.bw']):
            _notify_damage(trip, None)
        # Assert the mail actually went out — otherwise the escaping assertions
        # below silently pass on an empty outbox and test nothing.
        self.assertEqual(len(mail.outbox), 1, 'damage alert was not sent')
        msg = mail.outbox[0]
        body = msg.body + ' '.join(str(a[0]) for a in (msg.alternatives or []))
        self.assertNotIn('<img src=x', body)
        self.assertIn('&lt;img', body)
        self.assertIn('dented', body)

    # ---- photos ------------------------------------------------------------
    def test_photo_upload_and_gated_download(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        _, t = self._open_trip()
        img = SimpleUploadedFile('dent.jpg', b'\xff\xd8\xff\xe0jpegbytes', content_type='image/jpeg')
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/photo/",
                        {'image': img, 'kind': 'pre_trip', 'caption': 'front'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()['id']
        d = self.c.get(f"/api/v1/nexus/vehicle-register/photo/{pid}/")
        self.assertEqual(d.status_code, 200)

    # ---- Fable review regression fixes -------------------------------------
    def test_driver_cannot_sign_off_own_return(self):  # H2 — segregation of duties
        v, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10100, 'driver_return_confirm': True}, format='json')
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                        {'receptionist_confirm': True}, format='json')  # driver == self.c
        self.assertEqual(r.status_code, 403)
        r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                         {'receptionist_confirm': True}, format='json')  # different person
        self.assertEqual(r.status_code, 200, r.content)

    def test_pending_signoff_vehicle_cannot_be_freed_by_patch(self):  # H3
        v, t = self._open_trip()
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10100, 'driver_return_confirm': True}, format='json')
        r = self.fa.patch(f"/api/v1/nexus/vehicles/{v['id']}/", {'status': 'available'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.OUT)

    def test_bad_uuid_checkout_returns_400(self):  # M1
        r = self.c.post('/api/v1/nexus/vehicle-register/checkout/',
                        {'vehicle': 'not-a-uuid', 'driver_name': 'X', 'purpose': 'events',
                         'destination': 'y', 'driver_condition_confirm': True}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_bad_datetime_checkout_returns_400(self):  # M1
        v = self._add_vehicle()
        r = self._checkout(v['id'], expected_return_at='banana')
        self.assertEqual(r.status_code, 400)

    def test_trips_bad_vehicle_filter_no_500(self):  # M1
        r = self.c.get('/api/v1/nexus/vehicle-register/trips/?vehicle=abc')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 0)

    def test_checkin_without_out_odo_floors_against_vehicle(self):  # M2
        v = self._add_vehicle(odo=50000)
        tid = self._checkout(v['id'], odometer_out=None).json()['id']
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{tid}/checkin/",
                        {'odometer_in': 100, 'driver_return_confirm': True}, format='json')
        self.assertEqual(r.status_code, 400)   # would rewind the register
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{tid}/checkin/",
                        {'odometer_in': 50120, 'driver_return_confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).odometer_km, 50120)

    def test_photo_upload_rejects_non_image(self):  # H1 — stored XSS vector
        from django.core.files.uploadedfile import SimpleUploadedFile
        _, t = self._open_trip()
        evil = SimpleUploadedFile('evil.html', b'<script>alert(1)</script>', content_type='text/html')
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/photo/",
                        {'image': evil}, format='multipart')
        self.assertEqual(r.status_code, 400)

    def test_photo_served_with_nosniff(self):  # H1
        from django.core.files.uploadedfile import SimpleUploadedFile
        _, t = self._open_trip()
        img = SimpleUploadedFile('front.png', b'\x89PNG\r\n\x1a\n0000', content_type='image/png')
        pid = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/photo/",
                          {'image': img}, format='multipart').json()['id']
        d = self.c.get(f"/api/v1/nexus/vehicle-register/photo/{pid}/")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(d['Content-Type'], 'image/png')

    # ---- fleet-admin gate (CFO 2026-07-26: "unami and Dorothy admin") -------
    def test_ordinary_staff_cannot_change_the_register(self):
        """Changing the register — adding/editing a car, or releasing one from
        maintenance — is fleet-admin only. The odometer is the basis of the
        no-cash-for-fuel rule, so ordinary staff must not be able to move it."""
        v = self._add_vehicle(odo=10000)

        r = self.c.post('/api/v1/nexus/vehicles/', {'registration': 'B 999 XXX'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(FleetVehicle.objects.filter(registration='B 999 XXX').exists())

        r = self.c.patch(f"/api/v1/nexus/vehicles/{v['id']}/",
                         {'odometer_km': 999999}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).odometer_km, 10000)

        FleetVehicle.objects.filter(pk=v['id']).update(status=VehicleStatus.MAINTENANCE)
        r = self.c.post(f"/api/v1/nexus/vehicles/{v['id']}/clear-maintenance/", {}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status, VehicleStatus.MAINTENANCE)

    def test_driver_who_damaged_the_car_cannot_release_it(self):
        """The gap this closes: the damage block was self-serve before."""
        v, t = self._open_trip(odo=10000)
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 10100, 'driver_return_confirm': True,
                     'damage_on_return': True, 'damage_notes': 'kerbed the rim'},
                    format='json')
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status,
                         VehicleStatus.MAINTENANCE)
        # The driver tries to clear their own damage → refused.
        r = self.c.post(f"/api/v1/nexus/vehicles/{v['id']}/clear-maintenance/", {}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        # The fleet admin can, after inspection.
        r = self.fa.post(f"/api/v1/nexus/vehicles/{v['id']}/clear-maintenance/", {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status,
                         VehicleStatus.AVAILABLE)

    def test_daily_flow_still_open_to_everyone(self):
        """The gate must NOT stop ordinary staff using the cars — booking out,
        checking in and signing off are unchanged."""
        v, t = self._open_trip(odo=10000)          # checkout as ordinary staff
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                        {'odometer_in': 10100, 'driver_return_confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/signoff/",
                         {'receptionist_confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).status,
                         VehicleStatus.AVAILABLE)
        # And everyone can still SEE the board + the register.
        self.assertEqual(self.c.get('/api/v1/nexus/vehicle-register/board/').status_code, 200)
        self.assertEqual(self.c.get('/api/v1/nexus/vehicles/').status_code, 200)

    def test_board_tells_the_ui_who_may_manage(self):
        r = self.c.get('/api/v1/nexus/vehicle-register/board/')
        self.assertIs(r.json()['can_manage'], False)
        r = self.fa.get('/api/v1/nexus/vehicle-register/board/')
        self.assertIs(r.json()['can_manage'], True)

    def test_superuser_is_never_locked_out(self):
        """The CFO must always be able to fix his own register."""
        boss = User.objects.create_superuser('cfo', 'cfo@alphadirect.co.bw', 'x')
        sc = APIClient()
        sc.force_authenticate(boss)
        r = sc.post('/api/v1/nexus/vehicles/', {'registration': 'B 111 CFO'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIs(sc.get('/api/v1/nexus/vehicle-register/board/').json()['can_manage'], True)

    # ---- odometer correction ----------------------------------------------
    def _fat_finger_trip(self):
        """A closed trip whose odometer_in was mistyped (extra digit)."""
        v, t = self._open_trip(odo=12251)
        self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/checkin/",
                    {'odometer_in': 122776, 'driver_return_confirm': True}, format='json')
        return v, t

    def test_correction_denied_for_ordinary_staff(self):
        _, t = self._fat_finger_trip()
        r = self.c.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/correct-odometer/",
                        {'odometer_in': 12276, 'reason': 'typo'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_correction_by_named_corrector_fixes_trip_and_rederives_odo(self):
        from django.test import override_settings
        v, t = self._fat_finger_trip()
        # the vehicle odo was bumped to the bogus reading on check-in
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).odometer_km, 122776)
        with override_settings(VEHICLE_ODOMETER_CORRECTOR_EMAILS=['wame@alphadirect.co.bw']):
            r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/correct-odometer/",
                             {'odometer_in': 12276, 'reason': 'extra digit on check-in'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['odometer_in'], 12276)
        # current odometer recalculated DOWN from the bogus reading
        self.assertEqual(FleetVehicle.objects.get(pk=v['id']).odometer_km, 12276)
        # original value preserved in the audit trail
        self.assertTrue(AuditLog.objects.filter(
            table_name='VehicleTrip', record_id=str(t['id']),
            old_values__odometer_in=122776).exists())

    def test_correction_requires_reason(self):
        from django.test import override_settings
        _, t = self._fat_finger_trip()
        with override_settings(VEHICLE_ODOMETER_CORRECTOR_EMAILS=['wame@alphadirect.co.bw']):
            r = self.rc.post(f"/api/v1/nexus/vehicle-register/trips/{t['id']}/correct-odometer/",
                             {'odometer_in': 12276}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
