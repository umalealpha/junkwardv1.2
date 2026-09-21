"""The register showed only the date of loss.

Bokani Makosha, 2026-09-16: "you used Date of loss instead of REPORTED DATE …
when I filter the date in the system I want it to be reported date."

The from/to filter always used `registered_date` (Graphite's `registered_claim`,
i.e. the reported date) — but that date appeared nowhere on the register or in
the Excel, so the only date on screen was the date of loss. These pin both: the
filter is the reported date, and the reported date is now shown and exported.
"""
from datetime import date

from django.test import TestCase

from integrations.claims_views import _CLAIM_HEADERS, _claims_qs, _row
from integrations.models import GraphiteClaim


class ClaimsRegisterReportedDateTests(TestCase):
    def setUp(self):
        self.claim = GraphiteClaim.objects.create(
            graphite_id=90001, claim_number='CLM/TEST/1',
            registered_date=date(2026, 9, 10), date_of_loss=date(2026, 8, 1),
        )

    def test_row_carries_the_reported_date(self):
        row = _row(self.claim)
        self.assertEqual(row['registered_date'], '2026-09-10')
        self.assertEqual(row['date_of_loss'], '2026-08-01')

    def test_excel_has_a_reported_date_column_before_date_of_loss(self):
        self.assertIn('Reported Date', _CLAIM_HEADERS)
        self.assertLess(_CLAIM_HEADERS.index('Reported Date'),
                        _CLAIM_HEADERS.index('Date of Loss'))

    def test_the_date_filter_is_the_reported_date_not_the_date_of_loss(self):
        # A window around the REPORTED date keeps it; a window around the date
        # of loss must not.
        self.assertEqual(_claims_qs({'from': '2026-09-01', 'to': '2026-09-30'}).count(), 1)
        self.assertEqual(_claims_qs({'from': '2026-08-01', 'to': '2026-08-31'}).count(), 0)
