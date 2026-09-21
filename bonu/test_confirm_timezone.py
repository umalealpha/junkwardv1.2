"""The midnight-window bug, BONU half: "today" must be Botswana's today.

Same defect as the ledger entry-date guards fixed in f9ff6d56 (PR #688).
``bonu/confirm.py`` defaults ``today`` to the SERVER clock's date in two
places, and on a UTC box that is YESTERDAY between 00:00 and 02:00
Africa/Gaborone:

  * ``confirm()`` — refuses "The invoice date cannot be in the future." for a
    bill dated today, so an accountant working out-of-hours cannot confirm it.
  * ``warnings_for_draft()`` — the same ``today`` feeds the pre-confirm checks.

Frozen at 23:30 UTC = 01:30 Gaborone the next day, the exact window.

RED-FIRST: ``confirm.py`` does ``import datetime as dt`` and resolves
``dt.date`` at call time, so patching ``datetime.date`` globally reaches it —
the same trick test_entry_date_timezone.py needed for the serializer.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from bonu.confirm import confirm, warnings_for_draft
from bonu.models import IngestedDocument, LawFirm

INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)


_REAL_DATE = dt.date


class _StillADate(type):
    """Keep ``isinstance(<a real date>, datetime.date)`` True while patched, so
    Django's DateField.to_python still recognises a date on its way to the DB.
    Without it the test dies inside the ORM instead of at the guard."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


class _FakeDate(dt.date, metaclass=_StillADate):
    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_window():
    return (mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC),
            mock.patch('datetime.date', _FakeDate))


class ConfirmInvoiceDateTimezoneTests(TestCase):

    def setUp(self):
        self.firm = LawFirm.objects.create(name='Test Chambers')
        self.doc = IngestedDocument.objects.create(filename='bill.pdf')

    def _payload(self, invoice_date, invoice_number='INV-TZ-1'):
        return {
            'firm_id': self.firm.pk,
            'header': {'invoice_number': invoice_number,
                       'invoice_date': invoice_date.isoformat(),
                       'subtotal': '100.00', 'vat': '14.00', 'total': '114.00'},
            'lines': [{'amount': '100.00', 'matter_type': 'advice'}],
        }

    def test_a_bill_dated_today_can_be_confirmed_inside_the_midnight_window(self):
        # 01:30 Gaborone: today's bill is not future-dated. RED on the old
        # default, which read the server's 17th and refused the 18th.
        now_p, date_p = _in_window()
        with now_p, date_p:
            self.assertEqual(timezone.localdate(), GABORONE_TODAY)  # window is real
            invoice = confirm(self.doc, self._payload(GABORONE_TODAY))
        self.assertEqual(invoice.invoice_date, GABORONE_TODAY)
        self.assertEqual(invoice.total, Decimal('114.00'))

    def test_a_genuinely_future_bill_is_still_refused(self):
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(ValueError) as ctx:
                confirm(self.doc, self._payload(GABORONE_TODAY + dt.timedelta(days=1)))
        self.assertIn('cannot be in the future', str(ctx.exception))

    def test_an_explicit_today_is_still_honoured(self):
        # Guard-only change: callers passing their own `today` are unaffected.
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(ValueError):
                confirm(self.doc, self._payload(GABORONE_TODAY),
                        today=GABORONE_TODAY - dt.timedelta(days=1))

    def test_draft_warnings_use_botswanas_today(self):
        # warnings_for_draft shares the same default; a bill dated today must
        # not be flagged as future-dated at 01:30 Gaborone.
        draft = {'header': {'invoice_number': 'INV-TZ-2',
                            'invoice_date': GABORONE_TODAY.isoformat()},
                 'lines': [{'amount': '100.00'}]}
        now_p, date_p = _in_window()
        with now_p, date_p:
            codes = {w['code'] for w in warnings_for_draft(draft, firm=self.firm)}
        self.assertNotIn('FUTURE_DATED', codes)

    def test_draft_warnings_still_flag_a_genuinely_future_bill(self):
        draft = {'header': {'invoice_number': 'INV-TZ-3',
                            'invoice_date': (GABORONE_TODAY
                                             + dt.timedelta(days=1)).isoformat()},
                 'lines': [{'amount': '100.00'}]}
        now_p, date_p = _in_window()
        with now_p, date_p:
            codes = {w['code'] for w in warnings_for_draft(draft, firm=self.firm)}
        self.assertIn('FUTURE_DATED', codes)
