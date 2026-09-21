"""Database-level checks for the Subrogation recovery models.

These run against real Postgres (the pure-Python helpers are covered separately
under claims/recoveries/tests). The point here is the DB behaviour:
computed totals off receipts, the fallbacks to the legacy typed fields, the
panel register, and the RealPay double-count guard.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from claims.models import Subrogation, SubrogationPanel, SubrogationReceipt


class RecoveryModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='keetile')

    def _sub(self, **kw):
        base = dict(
            claim_reference='20241409',
            third_party_name='Test Third Party',   # fictional — no real PII in fixtures
            claim_paid_amount=Decimal('0'),
            created_by=self.user,
        )
        base.update(kw)
        return Subrogation.objects.create(**base)

    # --- Panel register ----------------------------------------------------

    def test_panel_member_created_with_each_kind(self):
        for kind in SubrogationPanel.Kind.values:
            SubrogationPanel.objects.create(
                name=f'Firm {kind}', kind=kind, created_by=self.user)
        self.assertEqual(SubrogationPanel.objects.count(), 4)

    def test_panel_name_is_unique(self):
        SubrogationPanel.objects.create(
            name='Salbany & Torto', kind=SubrogationPanel.Kind.EXTERNAL_LAWYER)
        with self.assertRaises(IntegrityError):
            SubrogationPanel.objects.create(
                name='Salbany & Torto', kind=SubrogationPanel.Kind.DEBT_COLLECTOR)

    def test_appointed_to_links_and_survives_panel_deactivation(self):
        firm = SubrogationPanel.objects.create(
            name='Minchin & Kelly', kind=SubrogationPanel.Kind.EXTERNAL_LAWYER)
        sub = self._sub(appointed_to=firm)
        firm.is_active = False
        firm.save()
        sub.refresh_from_db()
        self.assertEqual(sub.appointed_to_id, firm.id)  # history preserved

    # --- Total recoverable (cost build-up) ---------------------------------

    def test_total_recoverable_is_computed_from_components(self):
        # Real register row 20150018: 1260 + 35437.11 + 7369.07 = 44066.18
        sub = self._sub(
            assessor_fees=Decimal('1260.00'),
            repair_costs=Decimal('35437.11'),
            legal_fees=Decimal('7369.07'),
        )
        self.assertEqual(sub.total_recoverable, Decimal('44066.18'))

    def test_salvage_is_deducted_in_the_total(self):
        sub = self._sub(repair_costs=Decimal('10000'), salvage_amount=Decimal('2500'))
        self.assertEqual(sub.total_recoverable, Decimal('7500.00'))

    def test_total_recoverable_falls_back_to_legacy_expected_recovery(self):
        """A row with no cost components (legacy import / quick manual entry)
        still shows its typed figure."""
        sub = self._sub(expected_recovery=Decimal('5000.00'))
        self.assertEqual(sub.total_recoverable, Decimal('5000.00'))

    def test_components_win_over_legacy_field_when_both_present(self):
        sub = self._sub(expected_recovery=Decimal('999.99'), repair_costs=Decimal('100'))
        self.assertEqual(sub.total_recoverable, Decimal('100.00'))

    # --- Amount recovered (receipts) ---------------------------------------

    def test_amount_recovered_sums_receipts(self):
        sub = self._sub(repair_costs=Decimal('44066.18'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('20000.00'),
            received_date=date(2026, 1, 5), created_by=self.user)
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('16000.00'),
            received_date=date(2026, 3, 5), created_by=self.user)
        self.assertEqual(sub.amount_recovered, Decimal('36000.00'))
        self.assertEqual(sub.outstanding_balance, Decimal('8066.18'))

    def test_amount_recovered_falls_back_to_legacy_actual_recovery(self):
        sub = self._sub(expected_recovery=Decimal('100'), actual_recovery=Decimal('40'))
        self.assertEqual(sub.amount_recovered, Decimal('40.00'))
        self.assertEqual(sub.outstanding_balance, Decimal('60.00'))

    def test_receipts_override_the_legacy_typed_recovery(self):
        sub = self._sub(expected_recovery=Decimal('100'), actual_recovery=Decimal('40'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('55.00'),
            received_date=date(2026, 2, 1), created_by=self.user)
        self.assertEqual(sub.amount_recovered, Decimal('55.00'))

    def test_over_recovery_shows_a_negative_balance_not_zero(self):
        """The register has 24 over-recovered cases. The model must expose that,
        not clamp it — e.g. claim 20231059 (2,898.83 due, 163,502.74 in)."""
        sub = self._sub(repair_costs=Decimal('2898.83'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('163502.74'),
            received_date=date(2026, 1, 1), created_by=self.user)
        self.assertEqual(sub.outstanding_balance, Decimal('-160603.91'))

    def test_recovery_pct(self):
        sub = self._sub(repair_costs=Decimal('100'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('25'),
            received_date=date(2026, 1, 1), created_by=self.user)
        self.assertEqual(sub.recovery_pct, Decimal('25.0'))

    # --- RealPay double-count guard ----------------------------------------

    def test_duplicate_realpay_txn_is_rejected(self):
        sub = self._sub(repair_costs=Decimal('1000'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('500'), received_date=date(2026, 1, 1),
            method=SubrogationReceipt.Method.REALPAY, realpay_txn_id='RP-123',
            created_by=self.user)
        with self.assertRaises(IntegrityError):
            SubrogationReceipt.objects.create(
                subrogation=sub, amount=Decimal('500'), received_date=date(2026, 1, 2),
                method=SubrogationReceipt.Method.REALPAY, realpay_txn_id='RP-123',
                created_by=self.user)

    def test_blank_realpay_txn_ids_do_not_collide(self):
        """Manual cash/EFT/POS receipts carry no RealPay id — many blanks must
        be allowed (the unique guard is partial, on non-empty ids only)."""
        sub = self._sub(repair_costs=Decimal('1000'))
        for _ in range(3):
            SubrogationReceipt.objects.create(
                subrogation=sub, amount=Decimal('100'), received_date=date(2026, 1, 1),
                method=SubrogationReceipt.Method.CASH, created_by=self.user)
        self.assertEqual(sub.receipts.count(), 3)

    def test_deleting_a_subrogation_removes_its_receipts(self):
        sub = self._sub(repair_costs=Decimal('1000'))
        SubrogationReceipt.objects.create(
            subrogation=sub, amount=Decimal('100'), received_date=date(2026, 1, 1),
            created_by=self.user)
        sub.delete()
        self.assertEqual(SubrogationReceipt.objects.count(), 0)

    # --- Ageing & prescription delegate to the helpers ---------------------

    def test_age_bucket_and_days_follow_date_appointed(self):
        sub = self._sub(date_appointed=timezone.localdate() - timedelta(days=400))
        self.assertEqual(sub.age_bucket_label, '1-2 years')
        self.assertEqual(sub.age_days, 400)

    def test_no_date_appointed_is_reported_not_crashed(self):
        sub = self._sub(date_appointed=None)
        self.assertEqual(sub.age_bucket_label, 'No date appointed')
        self.assertIsNone(sub.age_days)

    def test_prescription_expired_for_an_old_loss(self):
        sub = self._sub(incident_date=date(2015, 7, 15))
        self.assertTrue(sub.prescription.expired)

    def test_prescription_unknown_when_no_dates(self):
        sub = self._sub(incident_date=None, date_appointed=None)
        self.assertTrue(sub.prescription.needs_attention)
        self.assertIsNone(sub.prescription.expires_on)

    def test_gl_config_is_a_singleton_and_starts_unset(self):
        from claims.models import SubrogationGLConfig
        a = SubrogationGLConfig.get_solo()
        b = SubrogationGLConfig.get_solo()
        self.assertEqual(a.pk, b.pk)                       # same row every time
        self.assertEqual(SubrogationGLConfig.objects.count(), 1)
        self.assertIsNone(a.recovery_income_account)       # unset → GL posting stays blocked
