"""The midnight window, claim-intake half: "today" must be Botswana's today.

The third time this module family has hit the same defect. `bonu/confirm.py`
was fixed for it (see `bonu/test_confirm_timezone.py`), the ledger entry-date
guards were fixed for it (PR #688), and the new claim-intake date guards
introduced on 9 Sep 2026 reintroduced it: they compared against
`datetime.date.today()`, the SERVER clock, while `settings.TIME_ZONE` is
`Africa/Gaborone` and the prod box runs UTC.

Between 00:00 and 02:00 Gaborone the server clock still reads yesterday, so:

  * opening a claim dated today was REFUSED — "cannot have been received in
    the future" — for two hours every night;
  * and worse, EDITING ANY FIELD on a case received today was refused too,
    because the edit path re-checks `received_on` whatever was actually
    changed. A status change, a note, a next-action date: all blocked.

Caught by Fable at the /fabe gate before deploy, 9 Sep 2026.

Frozen at 23:30 UTC = 01:30 Gaborone the next day — the exact window.

RED-FIRST: `cases.py` does `import datetime` and resolves `datetime.date` at
call time, so patching `datetime.date` globally reaches it — the same trick
`test_confirm_timezone.py` needed.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.cases import case_detail, cases
from bonu.models import LawFirm, LegalCase
from core.models import Company, UserProfile

INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)

_REAL_DATE = dt.date


class _StillADate(type):
    """Keep `isinstance(<a real date>, datetime.date)` True while patched, so
    Django's DateField.to_python still recognises a date on its way to the DB.
    Without it the test dies inside the ORM instead of at the guard."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


class _ServerClockDate(_REAL_DATE, metaclass=_StillADate):
    """A `date` whose `.today()` is the SERVER's date, not Botswana's."""

    @classmethod
    def today(cls):
        return SERVER_TODAY


class MidnightWindowTests(TestCase):
    """Inside the 00:00-02:00 window, intake must still work."""

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.firm = LawFirm.objects.create(name='Midnight & Co', is_active=True)
        self.rf = APIRequestFactory()
        u = User.objects.create_user('fin2', email='fin2@alphadirect.co.bw', password='x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})
        self.user = User.objects.get(pk=u.pk)

    def _post(self, view, body, **kw):
        req = self.rf.post('/api/v1/bonu/cases/', body, format='json')
        force_authenticate(req, user=self.user)
        return view(req, **kw)

    def test_a_claim_dated_botswana_today_opens_inside_the_window(self):
        # The screen sends Gaborone's today (it uses localYmd). On the server
        # clock that is TOMORROW, so the future-date guard would refuse it.
        with mock.patch('django.utils.timezone.now', return_value=INSIDE_WINDOW_UTC), \
             mock.patch('datetime.date', _ServerClockDate):
            r = self._post(cases, {'firm_id': str(self.firm.pk),
                                   'member_ref': 'BONU-MID-1',
                                   'received_on': GABORONE_TODAY.isoformat()})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().received_on, GABORONE_TODAY)

    def test_a_case_received_today_can_still_be_edited_inside_the_window(self):
        # The nastier half: the edit path re-checks `received_on` no matter what
        # was actually changed, so on the server clock EVERY edit to a case
        # received today was refused — a status change, a note, anything.
        c = LegalCase.objects.create(
            case_ref='MID-1', firm_type=LegalCase.FirmType.EXTERNAL, firm=self.firm,
            member_ref='BONU-MID-2', instructed_on=GABORONE_TODAY,
            received_on=GABORONE_TODAY)
        with mock.patch('django.utils.timezone.now', return_value=INSIDE_WINDOW_UTC), \
             mock.patch('datetime.date', _ServerClockDate):
            r = self._post(case_detail, {'outcome_note': 'spoke to the member'},
                           case_id=c.pk)
        self.assertEqual(r.status_code, 200, r.data)
        c.refresh_from_db()
        self.assertEqual(c.outcome_note, 'spoke to the member')

    def test_the_default_received_date_is_botswanas_today_not_the_servers(self):
        # A claim opened at 00:30 with no date typed must be dated TODAY in
        # Gaborone, not yesterday — otherwise days-to-process starts at 1.
        with mock.patch('django.utils.timezone.now', return_value=INSIDE_WINDOW_UTC), \
             mock.patch('datetime.date', _ServerClockDate):
            r = self._post(cases, {'firm_id': str(self.firm.pk),
                                   'member_ref': 'BONU-MID-3'})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(LegalCase.objects.get().received_on, GABORONE_TODAY)

    def test_a_genuinely_future_date_is_still_refused_inside_the_window(self):
        # The guard must not be softened into uselessness by the fix: tomorrow
        # in Gaborone is still tomorrow.
        with mock.patch('django.utils.timezone.now', return_value=INSIDE_WINDOW_UTC), \
             mock.patch('datetime.date', _ServerClockDate):
            r = self._post(cases, {
                'firm_id': str(self.firm.pk), 'member_ref': 'BONU-MID-4',
                'received_on': (GABORONE_TODAY + dt.timedelta(days=1)).isoformat()})
        self.assertEqual(r.status_code, 400)
