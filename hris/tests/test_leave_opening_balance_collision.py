"""
Bug c2888ba7 — "OMNI accepts the report, but does not update the balances."

HR uploaded corrected leave balances as at 30 June 2026. The upload reported success. The
balances kept showing the old figures. Nothing was broken in the upload: the rows were written
and none were future-dated.

The fault was the ORDER of one query. `leave_balances()` picked the opening row with
`order_by('leave_type_code', '-as_at_date')` — no tie-break. When HR re-uploads a correction for
the SAME as-at date, two rows share that date, Postgres returns them in arbitrary order, and the
OLD figure can win. On production 63 (profile, type, date) combinations were colliding, and
one employee was demonstrably reading the 11 July row instead of the 5 August one.

Two fixes, both tested here: the query breaks the tie on `created_at` (newest upload wins), and
the upload replaces the row for the same key instead of adding a second one.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from hris.leave_balance import balances_for_profile
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import Employee

D = Decimal
AS_AT = dt.date(2026, 6, 30)
ENTITLEMENT = 18.0


def _plus_accrual(opening, as_at=AS_AT, entitlement=ENTITLEMENT):
    """The uploaded opening PLUS the annual leave earned since its as-at date.

    These tests are about WHICH uploaded row wins, and `available` is only how we
    observe the winner. They used to assert the uploaded figure verbatim, which
    also pinned the old behaviour where an uploaded balance never accrued again —
    the very thing HR reported as "no live monthly accrual engine"
    (ref AD/HR/IA/2026/001). Stating the accrual instead of hard-coding a number
    keeps the tie-break assertions exact while staying honest about the maths.
    """
    from django.utils import timezone

    from hris.leave_balance import accrued_to_date
    return round(opening + accrued_to_date(entitlement, timezone.now().date(),
                                           since=as_at), 1)


def _drop_the_constraint():
    """Recreate the pre-fix world so the legacy paths can be tested.

    Migration 0061 dedupes and then adds a UniqueConstraint, so a colliding pair can no longer
    be inserted at all — which is the point. The rows that already existed on production DID
    collide, though, and the code that copes with them still has to be proven. Dropping the
    constraint inside a TestCase is safe: the surrounding transaction rolls it back.
    """
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute('ALTER TABLE hris_leaveopeningbalance '
                    'DROP CONSTRAINT IF EXISTS hris_uniq_leave_opening_key')



class OpeningBalanceCollisionTests(TestCase):

    def setUp(self):
        self.emp = Employee.objects.create(full_name='one employee',
                                           email='test-one@example.co.bw')
        self.profile = HRISProfile.objects.create(employee=self.emp)

    def _row(self, opening, accrued='1.50', as_at=AS_AT, batch='b'):
        return LeaveOpeningBalance.objects.create(
            profile=self.profile, leave_type_code='annual', as_at_date=as_at,
            entitlement_days=D('18.00'), opening_balance_days=D(str(opening)),
            accrued_days=D(str(accrued)), batch=batch)

    def _annual(self):
        rows = [b for b in balances_for_profile(self.profile) if b['code'] == 'annual']
        self.assertEqual(len(rows), 1, 'annual leave should appear exactly once')
        return rows[0]

    def test_the_newest_upload_wins_on_the_same_as_at_date(self):
        # The live shape BEFORE the constraint: an old batch, then HR's correction, same
        # as-at date. The tie-break is what repairs rows already on file.
        _drop_the_constraint()
        self._row('1.50', accrued='10.50', batch='leave-tracker-jun2026')
        self._row('7.00', accrued='1.50', batch='leave-opening-upload')
        self.assertEqual(self._annual()['available'], _plus_accrual(7.0))

    def test_it_still_wins_when_the_rows_were_written_in_the_other_order(self):
        # Guards against a fix that only works because of insertion order.
        _drop_the_constraint()
        newest = self._row('7.00', batch='leave-opening-upload')
        older = self._row('1.50', batch='leave-tracker-jun2026')
        LeaveOpeningBalance.objects.filter(pk=older.pk).update(
            created_at=newest.created_at - dt.timedelta(days=1))
        self.assertEqual(self._annual()['available'], _plus_accrual(7.0))

    def test_a_later_as_at_date_still_beats_a_newer_upload_of_an_older_date(self):
        # as_at_date remains the primary key of truth: a June correction uploaded today must
        # not override a July position already on file.
        self._row('3.00', as_at=dt.date(2026, 7, 31), batch='july')
        self._row('9.00', as_at=AS_AT, batch='june-correction-uploaded-later')
        self.assertEqual(self._annual()['available'],
                         _plus_accrual(3.0, as_at=dt.date(2026, 7, 31)))

    def test_a_future_dated_row_is_still_ignored(self):
        self._row('5.00', batch='june')
        self._row('99.00', as_at=dt.date(2099, 1, 1), batch='typo')
        self.assertEqual(self._annual()['available'], _plus_accrual(5.0))

    def test_a_second_row_for_the_same_key_is_now_refused(self):
        # The real guarantee after migration 0061: the collision cannot be created at all.
        from django.db import IntegrityError, transaction
        self._row('4.50')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._row('9.00')

    def test_the_uploaded_figure_is_what_shows(self):
        self._row('4.50')
        b = self._annual()
        self.assertEqual(b['available'], _plus_accrual(4.5))
        self.assertEqual(b['source'], 'opening_balance')
        self.assertEqual(b['as_at'], AS_AT.isoformat())


class UploadReplacesRatherThanAccumulatesTests(TestCase):
    """The upload itself must not leave two rows for one key."""

    def setUp(self):
        User = get_user_model()
        self.hr = User.objects.create_user('hr-upload-test', password='x', is_superuser=True,
                                           is_staff=True)
        self.emp = Employee.objects.create(full_name='Test Employee One',
                                           email='test-one@example.co.bw')
        self.profile = HRISProfile.objects.create(employee=self.emp)

    def _upload(self, opening):
        """Drive the REAL view with a real CSV, the way HR does.

        Called directly through APIRequestFactory rather than over a URL: routed through the
        test client this POST came back 301 from middleware that does not apply in production
        (the same path returns 400 for a missing file on the live server). Calling the view is
        what actually exercises the upload logic, without a redirect standing in the way.
        """
        from rest_framework.test import APIRequestFactory, force_authenticate

        from django.core.files.uploadedfile import SimpleUploadedFile
        from hris.leave_upload_views import upload_leave_opening_balances

        csv = (b'employee,leave_type,as_at_date,entitlement,opening_balance,accrued\n'
               + f'Test Employee One,annual,2026-06-30,18,{opening},1.5\n'.encode())
        req = APIRequestFactory().post(
            '/hris/api/leave-opening-balances/upload/',
            {'file': SimpleUploadedFile('balances.csv', csv, content_type='text/csv')},
            format='multipart')
        force_authenticate(req, user=self.hr)
        return upload_leave_opening_balances(req)

    def test_re_uploading_a_correction_leaves_one_row_with_the_new_figure(self):
        first = self._upload('11.00')
        self.assertEqual(first.status_code, 200, getattr(first, 'data', None))
        second = self._upload('9.50')
        self.assertEqual(second.status_code, 200, getattr(second, 'data', None))

        rows = LeaveOpeningBalance.objects.filter(profile=self.profile, leave_type_code='annual',
                                                 as_at_date=AS_AT)
        self.assertEqual(rows.count(), 1, 'a correction must replace, not pile up')
        self.assertEqual(rows.first().opening_balance_days, D('9.50'))

    def test_the_reply_says_a_figure_was_corrected(self):
        self._upload('11.00')
        second = self._upload('9.50').data
        # HR needs to see that something was overwritten, not just "loaded".
        self.assertEqual(second['replaced'], 1)
        self.assertIn('corrected an earlier figure', second['message'])

    def test_the_balance_screen_shows_the_corrected_figure_afterwards(self):
        self._upload('11.00')
        self._upload('9.50')
        annual = [b for b in balances_for_profile(self.profile) if b['code'] == 'annual'][0]
        self.assertEqual(annual['available'], _plus_accrual(9.5))

    def test_a_first_upload_still_reports_it_as_created_not_corrected(self):
        out = self._upload('11.00').data
        self.assertEqual(out['created'], 1)
        self.assertEqual(out['replaced'], 0)
        self.assertNotIn('corrected an earlier figure', out['message'])


class PreExistingDuplicatesTests(TestCase):
    """The shape production was ALREADY in: two rows for one key, 63 times over.

    Fable review, 2026-08-05: `update_or_create` raises MultipleObjectsReturned on such a key,
    and the upload would have 500'd mid-file with some rows saved and some not — worse than the
    bug being fixed. The earlier tests all started from a clean table, so none of them saw it.
    """

    def setUp(self):
        User = get_user_model()
        self.hr = User.objects.create_user('hr-dupe-test', password='x', is_superuser=True,
                                           is_staff=True)
        self.emp = Employee.objects.create(full_name='Test Employee One',
                                           email='test-one-dupe@example.co.bw')
        self.profile = HRISProfile.objects.create(employee=self.emp)

    def _legacy_duplicates(self):
        """Two rows for one key, as prod held them before the constraint existed."""
        _drop_the_constraint()
        for opening, batch in (('11.00', 'leave-tracker-jun2026'), ('1.50', 'later-batch')):
            LeaveOpeningBalance.objects.create(
                profile=self.profile, leave_type_code='annual', as_at_date=AS_AT,
                entitlement_days=D('18.00'), opening_balance_days=D(opening),
                accrued_days=D('1.50'), batch=batch)

    def _upload(self, opening):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from django.core.files.uploadedfile import SimpleUploadedFile
        from hris.leave_upload_views import upload_leave_opening_balances
        csv = (b'employee,leave_type,as_at_date,entitlement,opening_balance,accrued\n'
               + f'Test Employee One,annual,2026-06-30,18,{opening},1.5\n'.encode())
        req = APIRequestFactory().post(
            '/hris/api/leave-opening-balances/upload/',
            {'file': SimpleUploadedFile('b.csv', csv, content_type='text/csv')},
            format='multipart')
        force_authenticate(req, user=self.hr)
        return upload_leave_opening_balances(req)

    def test_an_upload_over_legacy_duplicates_does_not_crash(self):
        self._legacy_duplicates()
        r = self._upload('9.50')
        self.assertEqual(r.status_code, 200, getattr(r, 'data', None))

    def test_it_collapses_them_to_one_corrected_row(self):
        self._legacy_duplicates()
        self._upload('9.50')
        rows = LeaveOpeningBalance.objects.filter(profile=self.profile,
                                                 leave_type_code='annual', as_at_date=AS_AT)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().opening_balance_days, D('9.50'))

    def test_and_the_balance_then_shows_the_corrected_figure(self):
        self._legacy_duplicates()
        self._upload('9.50')
        annual = [b for b in balances_for_profile(self.profile) if b['code'] == 'annual'][0]
        self.assertEqual(annual['available'], _plus_accrual(9.5))


class TheReportHRActuallyLooksAtTests(TestCase):
    """The screen the bug was filed against.

    Fable review, 2026-08-05: the bulk-upload tile sits ON the Team Leave Report page, and
    `leave_report()` never read LeaveOpeningBalance — so HR uploaded there, looked at the table
    there, and nothing changed. Fixing the balance query alone would have left every number on
    this screen exactly as it was, and HR would have been told "fixed" for a second time.
    """

    def setUp(self):
        User = get_user_model()
        self.hr = User.objects.create_user('hr-report-test', password='x', is_superuser=True,
                                           is_staff=True)
        self.emp = Employee.objects.create(full_name='Test Employee Two',
                                           email='test-two@example.co.bw')
        self.profile = HRISProfile.objects.create(employee=self.emp)

    def _report(self):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from hris.feature_views import leave_report
        req = APIRequestFactory().get('/hris/api/leave-report/',
                                      {'date_from': '2026-01-01', 'date_to': '2026-12-31',
                                       'leave_type': 'annual'})
        force_authenticate(req, user=self.hr)
        resp = leave_report(req)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        rows = [r for r in resp.data['rows']
                if r['employee_name'] == 'Test Employee Two' and r['leave_type_code'] == 'annual']
        self.assertEqual(len(rows), 1, resp.data['rows'])
        return rows[0]

    def test_without_an_upload_the_report_uses_the_cos_formula(self):
        row = self._report()
        self.assertEqual(row['source'], 'cos_formula')
        self.assertIsNone(row['as_at'])

    def test_an_uploaded_balance_shows_on_the_report(self):
        LeaveOpeningBalance.objects.create(
            profile=self.profile, leave_type_code='annual', as_at_date=AS_AT,
            entitlement_days=D('18.00'), opening_balance_days=D('7.50'),
            accrued_days=D('1.50'), batch='hr-upload')
        row = self._report()
        # THE assertion this whole bug turns on.
        self.assertEqual(row['opening_balance'], 7.5)
        self.assertEqual(row['source'], 'hr_upload')
        self.assertEqual(row['as_at'], AS_AT.isoformat())

    def test_a_correction_changes_what_the_report_shows(self):
        for opening in ('7.50', '4.00'):
            LeaveOpeningBalance.objects.update_or_create(
                profile=self.profile, leave_type_code='annual', as_at_date=AS_AT,
                defaults={'entitlement_days': D('18.00'),
                          'opening_balance_days': D(opening),
                          'accrued_days': D('1.50'), 'batch': 'hr-upload'})
        self.assertEqual(self._report()['opening_balance'], 4.0)
