"""
billing/tests/test_reverse_charge.py

Tests for billing.ReverseChargeEntry — reverse-charge VAT on imported
remote services (VAT Amendment Act No.16 of 2025, effective 1 June 2026).

Closes the Fable-5 review's H-2 finding (zero tests) and locks in the C-1
fix (the full-recovery default going stale across an edit), plus the H-1
range-validation and M-3 effective-date guards added alongside it.

Sandbox note on how these actually run
--------------------------------------
The full project test DB cannot be built in every environment: core/ledger/
procurement/payments ship Postgres-only RunSQL migrations (raw PL/pgSQL
triggers, e.g. ledger/migrations/0026_posted_je_immutable_triggers.py) that
fail outright on SQLite, and billing's own models (Invoice.journal_entry,
Invoice.purchase_order) FK into both of those apps — so billing can't even
adopt commissions/test_settings.py's trick of dropping unrelated apps from
INSTALLED_APPS (see that file's docstring for the same documented
constraint). Where no Postgres is reachable at all (as in this sandbox),
`manage.py test` cannot create ANY test database, Postgres or SQLite.

Every test below is therefore a django.test.SimpleTestCase with
`databases = set()` — Django's test runner introspects that attribute and
skips database setup entirely for these tests ("Skipping setup of unused
database(s): default."), so they run with no DB of any kind, reachable or
not. They still reach the REAL ReverseChargeEntry.save() / .clean(), the
REAL serializer validate()/create()/update(), the REAL perform_destroy, and
the REAL build_vat_return() — nothing about the business logic is faked.
Only the DB I/O boundary is stubbed (see _no_db_writes below), the same
technique used to first catch C-1 without a live database.

Run:
  SECRET_KEY=dummy DB_ENGINE=django.db.backends.postgresql DB_NAME=x \\
    DB_USER=x DB_PASSWORD=x DB_HOST=localhost DB_PORT=5432 \\
    ALLOWED_HOSTS="*" DEBUG=False \\
    .venv-local/bin/python manage.py test billing.tests.test_reverse_charge -v2

On a machine/CI with a real reachable Postgres, the exact same command
works unchanged (SimpleTestCase's `databases = set()` is honoured either
way) — this is not a SQLite-only or offline-only test module.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import SimpleTestCase, override_settings

from billing.api_views import ReverseChargeEntryViewSet
from billing.models import Invoice, ReverseChargeEntry
from billing.serializers import ReverseChargeEntrySerializer

ZERO = Decimal('0.00')
RATE = Decimal('0.14')
EFFECTIVE = date(2026, 6, 1)


# ---------------------------------------------------------------------------
# In-process DB-boundary stub (no Postgres, no SQLite, no migrations)
# ---------------------------------------------------------------------------

@contextmanager
def _no_db_writes(model_cls=ReverseChargeEntry):
    """
    Let AuditableMixin.save() (core/models.py) — and therefore the real
    ReverseChargeEntry.save() above it — run end to end without a database.
    Stubs exactly the three points that would otherwise need one:
      - django.db.models.Model.save   the actual row INSERT/UPDATE
      - core.models.AuditLog.objects.create   the audit-trail INSERT
      - <model_cls>.objects.get   AuditableMixin's pre-update "old values"
        read, which we make raise DoesNotExist (there genuinely is no row)
    Nothing about ReverseChargeEntry's own business logic is touched.
    """
    with patch('django.db.models.Model.save', autospec=True) as mock_save, \
         patch('core.models.AuditLog.objects.create'), \
         patch.object(model_cls, 'objects') as mock_manager:
        mock_save.side_effect = lambda inst, *a, **kw: setattr(inst._state, 'adding', False)
        mock_manager.get.side_effect = model_cls.DoesNotExist
        yield


def _save(instance, **kwargs):
    """Convenience wrapper: run instance.save(**kwargs) inside _no_db_writes."""
    with _no_db_writes(type(instance)):
        instance.save(**kwargs)
    return instance


def _fake_user():
    # Unsaved User — constructing a model instance never touches the DB
    # (only .save()/.filter()/.get() do), and ReverseChargeEntry.save()
    # only ever reads audit_user.pk off it.
    return User(pk=1, username='tester')


def _entry(**overrides):
    fields = dict(
        vendor='Amazon Web Services', category=ReverseChargeEntry.Category.CLOUD,
        invoice_date=date(2026, 6, 15), foreign_currency='USD',
        foreign_amount=Decimal('100.00'), bwp_amount=Decimal('100.00'),
    )
    fields.update(overrides)
    return ReverseChargeEntry(**fields)


def _payload(**overrides):
    payload = dict(
        vendor='Amazon Web Services', category='cloud',
        invoice_date='2026-06-15', foreign_currency='USD',
        foreign_amount='100.00', bwp_amount='100.00', note='',
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# (a) output_vat = bwp * RC_VAT_RATE, ROUND_HALF_UP
# ---------------------------------------------------------------------------

@override_settings(RC_VAT_RATE=RATE)
class OutputVatComputationTest(SimpleTestCase):
    databases = set()

    def test_output_vat_uses_round_half_up_not_banker_rounding(self):
        # 0.75 * 0.14 = 0.1050 exactly -> halfway between 0.10 and 0.11.
        # ROUND_HALF_UP takes every tie away from zero -> 0.11.
        # ROUND_HALF_EVEN (banker's rounding) would instead land on 0.10
        # (0 is already even) -- chosen specifically so the two disagree,
        # proving save() really uses ROUND_HALF_UP and not the Decimal
        # module default (ROUND_HALF_EVEN) or float rounding.
        entry = _save(_entry(bwp_amount=Decimal('0.75')))
        self.assertEqual(entry.output_vat, Decimal('0.11'))

    def test_output_vat_is_bwp_times_rate_in_the_ordinary_case(self):
        entry = _save(_entry(bwp_amount=Decimal('1000.00')))
        self.assertEqual(entry.output_vat, Decimal('140.00'))


# ---------------------------------------------------------------------------
# (b) + (c) + (d) full-recovery default, C-1 regression, explicit override
# ---------------------------------------------------------------------------

@override_settings(RC_VAT_RATE=RATE)
class FullRecoveryAndOverrideTest(SimpleTestCase):
    databases = set()

    def test_net_zero_at_full_recovery_on_create(self):
        entry = _save(_entry(bwp_amount=Decimal('1000.00')))
        self.assertEqual(entry.output_vat, Decimal('140.00'))
        self.assertEqual(entry.input_vat_recoverable, Decimal('140.00'))
        self.assertEqual(entry.net_vat_cost, ZERO)
        self.assertFalse(entry.input_recovery_overridden)

    def test_net_stays_zero_after_bwp_amount_edit_when_not_overridden(self):
        """C-1 regression test: a later bwp_amount edit must not desync
        net_vat_cost from zero when the row was never overridden."""
        entry = _save(_entry(bwp_amount=Decimal('1000.00')))  # create
        self.assertEqual(entry.net_vat_cost, ZERO)

        entry.bwp_amount = Decimal('2500.00')
        _save(entry)  # edit — input_recovery_overridden is still False

        self.assertEqual(entry.output_vat, Decimal('350.00'))
        self.assertEqual(entry.input_vat_recoverable, Decimal('350.00'),
                          'input_vat_recoverable went stale — the C-1 bug is back')
        self.assertEqual(entry.net_vat_cost, ZERO)

    def test_explicit_input_recoverable_sticks_across_later_unrelated_save(self):
        entry = _save(_entry(
            bwp_amount=Decimal('1000.00'),
            input_vat_recoverable=Decimal('40.00'),
            input_recovery_overridden=True,
        ))
        self.assertEqual(entry.output_vat, Decimal('140.00'))
        self.assertEqual(entry.input_vat_recoverable, Decimal('40.00'))
        self.assertEqual(entry.net_vat_cost, Decimal('100.00'))

        entry.vendor = 'Amazon Web Services (renamed)'  # unrelated field edit
        _save(entry)

        self.assertEqual(entry.input_vat_recoverable, Decimal('40.00'),
                          'an explicit override must survive an unrelated save')
        self.assertEqual(entry.output_vat, Decimal('140.00'))
        self.assertEqual(entry.net_vat_cost, Decimal('100.00'))

    def test_explicit_zero_input_recoverable_sticks(self):
        """Explicit 0 must stick too — None-vs-a-value is the whole point of
        the nullable field; zero is a legitimate explicit choice (fully
        exempt spend), not "unset"."""
        entry = _save(_entry(
            bwp_amount=Decimal('500.00'),
            input_vat_recoverable=ZERO,
            input_recovery_overridden=True,
        ))
        self.assertEqual(entry.input_vat_recoverable, ZERO)
        self.assertEqual(entry.net_vat_cost, entry.output_vat)

        entry.bwp_amount = Decimal('600.00')
        _save(entry)

        self.assertEqual(entry.input_vat_recoverable, ZERO,
                          'explicit zero must not be treated as "unset"')
        self.assertEqual(entry.net_vat_cost, entry.output_vat)

    def test_future_rate_change_still_reconciles_to_net_zero(self):
        """Bonus: the C-1 fix's other promise — a future RC_VAT_RATE change
        must not desync an existing full-recovery row either."""
        entry = _save(_entry(bwp_amount=Decimal('1000.00')))
        self.assertEqual(entry.net_vat_cost, ZERO)

        with override_settings(RC_VAT_RATE=Decimal('0.15')):
            _save(entry)

        self.assertEqual(entry.output_vat, Decimal('150.00'))
        self.assertEqual(entry.input_vat_recoverable, Decimal('150.00'))
        self.assertEqual(entry.net_vat_cost, ZERO)


# ---------------------------------------------------------------------------
# (e) note required for "Other" — model.clean() AND serializer.validate()
# ---------------------------------------------------------------------------

class OtherCategoryNoteRequiredTest(SimpleTestCase):
    databases = set()

    def test_model_clean_rejects_other_without_note(self):
        entry = _entry(category=ReverseChargeEntry.Category.OTHER, note='')
        with self.assertRaises(DjangoValidationError) as ctx:
            entry.clean()
        self.assertIn('note', ctx.exception.message_dict)

    def test_model_clean_allows_other_with_note(self):
        entry = _entry(category=ReverseChargeEntry.Category.OTHER,
                        note='Zapier automation subscription')
        entry.clean()  # must not raise

    def test_serializer_rejects_other_without_note(self):
        serializer = ReverseChargeEntrySerializer(data=_payload(category='other', note=''))
        self.assertFalse(serializer.is_valid())
        self.assertIn('note', serializer.errors)

    def test_serializer_allows_other_with_note(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(category='other', note='Zapier automation subscription'))
        self.assertTrue(serializer.is_valid(), serializer.errors)


# ---------------------------------------------------------------------------
# H-1 — range validation (bonus coverage beyond the (a)-(g) list, since this
# is a HIGH-severity fix in the same review)
# ---------------------------------------------------------------------------

@override_settings(RC_VAT_RATE=RATE)
class RangeValidationTest(SimpleTestCase):
    databases = set()

    def test_model_clean_rejects_zero_and_negative_bwp_amount(self):
        for bad in (ZERO, Decimal('-5.00')):
            with self.assertRaises(DjangoValidationError):
                _entry(bwp_amount=bad).clean()

    def test_model_clean_rejects_zero_and_negative_foreign_amount(self):
        for bad in (ZERO, Decimal('-5.00')):
            with self.assertRaises(DjangoValidationError):
                _entry(foreign_amount=bad).clean()

    def test_model_clean_rejects_negative_input_recoverable(self):
        entry = _entry(bwp_amount=Decimal('100.00'), input_vat_recoverable=Decimal('-1.00'))
        with self.assertRaises(DjangoValidationError) as ctx:
            entry.clean()
        self.assertIn('input_vat_recoverable', ctx.exception.message_dict)

    def test_model_clean_rejects_input_recoverable_over_output_vat(self):
        # bwp=100 * 0.14 = output_vat bound of 14.00 -> 999 over-claims.
        entry = _entry(bwp_amount=Decimal('100.00'), input_vat_recoverable=Decimal('999.00'))
        with self.assertRaises(DjangoValidationError) as ctx:
            entry.clean()
        self.assertIn('input_vat_recoverable', ctx.exception.message_dict)

    def test_model_clean_allows_input_recoverable_exactly_at_bound(self):
        entry = _entry(bwp_amount=Decimal('100.00'), input_vat_recoverable=Decimal('14.00'))
        entry.clean()  # must not raise — the bound itself is inclusive

    def test_serializer_rejects_zero_and_negative_bwp_amount(self):
        for bad in ('0.00', '-5.00'):
            serializer = ReverseChargeEntrySerializer(data=_payload(bwp_amount=bad))
            self.assertFalse(serializer.is_valid(), f'{bad} should have been rejected')

    def test_serializer_rejects_input_recoverable_over_output_vat(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(bwp_amount='100.00', input_vat_recoverable='999.00'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('input_vat_recoverable', serializer.errors)

    def test_serializer_rejects_negative_input_recoverable(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(bwp_amount='100.00', input_vat_recoverable='-1.00'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('input_vat_recoverable', serializer.errors)

    def test_serializer_allows_valid_partial_recovery(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(bwp_amount='100.00', input_vat_recoverable='7.00'))
        self.assertTrue(serializer.is_valid(), serializer.errors)


# ---------------------------------------------------------------------------
# M-3 — effective-date guard (bonus coverage)
# ---------------------------------------------------------------------------

@override_settings(RC_VAT_EFFECTIVE_DATE=EFFECTIVE)
class EffectiveDateGuardTest(SimpleTestCase):
    databases = set()

    def test_model_clean_rejects_invoice_date_before_effective_date(self):
        entry = _entry(invoice_date=date(2026, 5, 31))
        with self.assertRaises(DjangoValidationError) as ctx:
            entry.clean()
        self.assertIn('invoice_date', ctx.exception.message_dict)

    def test_model_clean_allows_invoice_date_on_effective_date(self):
        entry = _entry(invoice_date=EFFECTIVE)
        entry.clean()  # must not raise — boundary is inclusive

    def test_serializer_rejects_invoice_date_before_effective_date(self):
        serializer = ReverseChargeEntrySerializer(data=_payload(invoice_date='2026-05-31'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('invoice_date', serializer.errors)

    def test_serializer_allows_invoice_date_on_effective_date(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(invoice_date=EFFECTIVE.isoformat()))
        self.assertTrue(serializer.is_valid(), serializer.errors)


# ---------------------------------------------------------------------------
# (f) output_vat / net_vat_cost / input_recovery_overridden are read-only
# ---------------------------------------------------------------------------

class ReadOnlyComputedFieldsTest(SimpleTestCase):
    databases = set()

    def test_client_supplied_output_vat_and_net_vat_cost_are_ignored(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(output_vat='999.99', net_vat_cost='999.99'))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('output_vat', serializer.validated_data)
        self.assertNotIn('net_vat_cost', serializer.validated_data)

    def test_client_cannot_set_input_recovery_overridden_directly(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(input_recovery_overridden=True))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('input_recovery_overridden', serializer.validated_data)


# ---------------------------------------------------------------------------
# C-1 mechanism at the serializer layer: create()/update() are the ONLY
# places input_recovery_overridden is set, and only from the presence of a
# real client-supplied input_vat_recoverable.
# ---------------------------------------------------------------------------

@override_settings(RC_VAT_RATE=RATE)
class SerializerOverrideFlagTest(SimpleTestCase):
    databases = set()

    def _context(self):
        return {'request': SimpleNamespace(user=_fake_user())}

    def test_create_without_input_vat_recoverable_is_not_overridden(self):
        serializer = ReverseChargeEntrySerializer(data=_payload(), context=self._context())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # create() falls back to Company.get_default() when no company is
        # supplied (not the case here) — that's a real DB lookup unrelated
        # to anything C-1 touches, so stub it out too rather than widening
        # _no_db_writes() for every test that doesn't need it.
        with _no_db_writes(), patch('core.models.Company.get_default', return_value=None):
            obj = serializer.save()
        self.assertFalse(obj.input_recovery_overridden)
        self.assertEqual(obj.net_vat_cost, ZERO)

    def test_create_with_explicit_input_vat_recoverable_is_overridden(self):
        serializer = ReverseChargeEntrySerializer(
            data=_payload(bwp_amount='100.00', input_vat_recoverable='5.00'),
            context=self._context(),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with _no_db_writes(), patch('core.models.Company.get_default', return_value=None):
            obj = serializer.save()
        self.assertTrue(obj.input_recovery_overridden)
        self.assertEqual(obj.input_vat_recoverable, Decimal('5.00'))
        self.assertEqual(obj.net_vat_cost, Decimal('9.00'))

    def test_update_touching_input_vat_recoverable_flips_override_on(self):
        entry = _save(_entry(bwp_amount=Decimal('1000.00')), audit_user=_fake_user())
        self.assertFalse(entry.input_recovery_overridden)

        serializer = ReverseChargeEntrySerializer(
            instance=entry, data={'input_vat_recoverable': '20.00'},
            partial=True, context=self._context(),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with _no_db_writes():
            obj = serializer.save()
        self.assertTrue(obj.input_recovery_overridden)
        self.assertEqual(obj.input_vat_recoverable, Decimal('20.00'))
        self.assertEqual(obj.net_vat_cost, Decimal('120.00'))

    def test_update_not_touching_input_vat_recoverable_leaves_override_alone(self):
        """The C-1 'sticks across a later unrelated save' guarantee, driven
        through the actual serializer.update() path this time."""
        entry = _save(_entry(
            bwp_amount=Decimal('1000.00'), input_vat_recoverable=Decimal('40.00'),
            input_recovery_overridden=True,
        ), audit_user=_fake_user())

        serializer = ReverseChargeEntrySerializer(
            instance=entry, data={'vendor': 'Amazon Web Services (renamed)'},
            partial=True, context=self._context(),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with _no_db_writes():
            obj = serializer.save()
        self.assertTrue(obj.input_recovery_overridden)
        self.assertEqual(obj.input_vat_recoverable, Decimal('40.00'))
        self.assertEqual(obj.net_vat_cost, Decimal('100.00'))


# ---------------------------------------------------------------------------
# M-2 — perform_destroy attributes the requesting user
# ---------------------------------------------------------------------------

class PerformDestroyAuditAttributionTest(SimpleTestCase):
    databases = set()

    def test_perform_destroy_attributes_the_requesting_user(self):
        viewset = ReverseChargeEntryViewSet()
        fake_user = _fake_user()
        viewset.request = SimpleNamespace(user=fake_user)
        instance = MagicMock()

        viewset.perform_destroy(instance)

        instance.delete.assert_called_once_with(audit_user=fake_user)


# ---------------------------------------------------------------------------
# (g) build_vat_return: reverse-charge lines are filtered + additive
# ---------------------------------------------------------------------------

class BuildVatReturnAggregationTest(SimpleTestCase):
    databases = set()

    def _empty_invoice_manager(self):
        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs
        mock_qs.__iter__.return_value = iter([])
        return mock_qs

    def test_reverse_charge_lines_are_filtered_and_additive(self):
        from reporting.reports import build_vat_return

        rc1 = _entry(vendor='AWS', bwp_amount=Decimal('1000.00'))
        rc1.output_vat = Decimal('140.00')
        rc1.input_vat_recoverable = Decimal('140.00')
        rc1.net_vat_cost = ZERO

        rc2 = _entry(vendor='Anthropic', category=ReverseChargeEntry.Category.AI_SAAS,
                     bwp_amount=Decimal('500.00'))
        rc2.output_vat = Decimal('70.00')
        rc2.input_vat_recoverable = Decimal('20.00')
        rc2.net_vat_cost = Decimal('50.00')

        mock_invoice_qs = self._empty_invoice_manager()
        mock_rc_qs = MagicMock()
        mock_rc_qs.filter.return_value = mock_rc_qs
        mock_rc_qs.order_by.return_value = [rc1, rc2]

        with patch.object(Invoice, 'objects', mock_invoice_qs), \
             patch.object(ReverseChargeEntry, 'objects', mock_rc_qs):
            report = build_vat_return(date(2026, 6, 1), date(2026, 6, 30))

        # Filtered on the right window + reverse_charge_applies=True.
        mock_rc_qs.filter.assert_called_once_with(
            invoice_date__gte=date(2026, 6, 1), invoice_date__lte=date(2026, 6, 30),
            reverse_charge_applies=True,
        )
        # Additive: domestic totals are untouched (all-zero here, since the
        # mocked Invoice queryset is empty) — reverse-charge amounts must
        # never leak into the existing VAT-return lines.
        self.assertEqual(report['summary']['output_vat'], '0.00')
        self.assertEqual(report['summary']['input_vat'], '0.00')
        self.assertEqual(report['summary']['net_vat'], '0.00')
        # Reverse-charge totals are the straight sum of the two entries,
        # surfaced as their own separate lines.
        self.assertEqual(report['reverse_charge_vat']['total_output_vat'], '210.00')
        self.assertEqual(report['reverse_charge_vat']['total_input_vat_recoverable'], '160.00')
        self.assertEqual(report['reverse_charge_vat']['total_net_vat_cost'], '50.00')
        self.assertEqual(report['summary']['reverse_charge_output_vat'], '210.00')
        self.assertEqual(report['summary']['reverse_charge_input_vat'], '160.00')
        self.assertEqual(report['summary']['reverse_charge_net_vat_cost'], '50.00')
        self.assertEqual(len(report['reverse_charge_vat']['entries']), 2)
        self.assertEqual(report['reverse_charge_vat']['entries'][0]['vendor'], 'AWS')

    def test_company_filter_is_applied_when_given(self):
        from reporting.reports import build_vat_return

        mock_invoice_qs = self._empty_invoice_manager()
        mock_rc_qs = MagicMock()
        mock_rc_qs.filter.return_value = mock_rc_qs
        mock_rc_qs.order_by.return_value = []

        company_id = 'c0ffee00-0000-0000-0000-000000000000'
        with patch.object(Invoice, 'objects', mock_invoice_qs), \
             patch.object(ReverseChargeEntry, 'objects', mock_rc_qs):
            build_vat_return(date(2026, 6, 1), date(2026, 6, 30), company_id=company_id)

        mock_rc_qs.filter.assert_any_call(company_id=company_id)

    def test_no_reverse_charge_entries_gives_zero_totals_not_an_error(self):
        from reporting.reports import build_vat_return

        mock_invoice_qs = self._empty_invoice_manager()
        mock_rc_qs = MagicMock()
        mock_rc_qs.filter.return_value = mock_rc_qs
        mock_rc_qs.order_by.return_value = []

        with patch.object(Invoice, 'objects', mock_invoice_qs), \
             patch.object(ReverseChargeEntry, 'objects', mock_rc_qs):
            report = build_vat_return(date(2026, 6, 1), date(2026, 6, 30))

        self.assertEqual(report['reverse_charge_vat']['total_output_vat'], '0.00')
        self.assertEqual(report['reverse_charge_vat']['entries'], [])
