"""
procurement/test_claims_edge.py — adversarial edge / fuzz tests for the
claims-PO engine (claims_engine.py) and API (claims_api.py).

Fuzz ideas mined from a production ERP's PO validation layer (ERPNext,
read-only reference at work/external-repos/erp/erpnext):

  * accounts_controller.py::validate_qty_is_not_zero — zero-qty PO lines are
    REJECTED unless an explicit allow_zero_qty flag is set.
  * buying_controller.py::validate_negative_quantity — negative quantities are
    rejected outright (returns excepted).
  * buying/utils.py::validate_for_items — the same item entered twice on one
    PO is rejected unless "allow multiple items" is configured.
  * purchase_order/purchase_order.py::validate — integer-UOM qty check,
    minimum-order-qty check, supplier-scorecard prevent_pos block, and totals
    recalculated + rounded server-side.

Omni's claims path differs deliberately (assessment lines are what they are):
zero-qty is COERCED to 1 (mirroring claims_parser.py line ~240 `qty or 1.0`),
zero-price lines are allowed, duplicate descriptions on one PO are allowed
(two parts groups from the same supplier merge into one PO). These tests
RECORD that behaviour so a future change is a conscious one, and assert the
invariants that must never break: qty x unit base maths, the 2-PO split,
excess priority typed > assessment > max(pct x base, min), negative no-VAT
excess line, markup on outside parts only, vendors never auto-created,
DRAFT + BWP only.

Regression tests here cover one REAL bug found and fixed during this sweep:
build_allocation_plan billed only the FIRST labour group while claims_split
summed ALL of them — same report_json, different money (see
EngineLabourConsolidationTests).

Engine tests are pure -> SimpleTestCase; API tests mirror the DB fixture
style of test_claims_api.py.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from procurement.claims_engine import (
    VAT_RATE,
    apply_allocations,
    build_allocation_plan,
    claims_split,
    compute_excess,
)


# ---------------------------------------------------------------------------
# Shared fixture builders (shape identical to claims_parser.parse output)
# ---------------------------------------------------------------------------

def _pline(desc, qty, unit, **extra):
    line = {'description': desc, 'qty': qty, 'unit_price': unit}
    line.update(extra)
    return line


def _edge_report(repairer='Carfil Panel Beaters', parts=None, labour=8000.0,
                 summary=None):
    """parts = [(supplier_label, [line, ...], subtotal_excl), ...] — a list of
    tuples, so the SAME label can appear twice (two groups, one supplier)."""
    groups = []
    for label, lines, subtotal in (parts or []):
        groups.append({'kind': 'parts', 'supplier_label': label,
                       'subtotal_excl': subtotal, 'lines': lines})
    if labour is not None:
        groups.append({'kind': 'labour', 'supplier_label': repairer,
                       'subtotal_excl': labour,
                       'lines': [{'description': 'Panel beating', 'units': 1.0,
                                  'rate': labour, 'total': labour}]})
    return {'claim_no': 'EDGE-001', 'repairer': repairer, 'currency': 'BWP',
            'summary': {'Excess': 400.0} if summary is None else summary,
            'groups': groups}


# ===========================================================================
# ENGINE — pure money math (SimpleTestCase, no DB)
# ===========================================================================

class EngineZeroFractionalQtyTests(SimpleTestCase):
    """Fuzz cases 1 + 2 — zero-qty / zero-price / fractional-qty parts lines."""

    def test_zero_qty_and_zero_price_lines_do_not_crash(self):
        # ERPNext would REJECT the zero-qty row (validate_qty_is_not_zero).
        # Omni coerces qty 0/missing -> 1 by design: claims_parser.py already
        # emits `qty or 1.0`, and the engine mirrors it. RECORDED here — the
        # zero-qty line is billed at qty 1 x unit, the zero-price line at 0.
        report = _edge_report(
            parts=[('Motor Holdings',
                    [_pline('Clip', 0, 500.0),
                     _pline('Gasket (free)', 3, 0.0)],
                    500.0)],
            labour=1000.0, summary={},
        )
        s = claims_split(report)                       # must not raise
        self.assertEqual(s['motor_centre']['parts_total'], 500.0)  # 1x500 + 3x0

        plan = build_allocation_plan(report)           # must not raise
        parts_row = next(r for r in plan['rows']
                         if r['id'] == 'parts:Motor Holdings')
        self.assertEqual(parts_row['amount'], 500.0)
        # The coercion is visible on the line itself: qty 0 became 1.0.
        self.assertEqual(parts_row['lines'][0]['qty'], 1.0)
        self.assertEqual(parts_row['lines'][0]['unit_price'], 500.0)
        self.assertEqual(parts_row['lines'][1]['qty'], 3.0)
        self.assertEqual(parts_row['lines'][1]['unit_price'], 0.0)
        # No phantom markup: subtotal 500 == base 500.
        self.assertFalse(any(r['id'] == 'markup' for r in plan['rows']))

    def test_fractional_qty_base_maths_is_qty_times_unit_not_line_total(self):
        # qty 2.5 x unit 100 = 250. The line's own 'total' (999) LIES — the
        # difference is embedded markup and must land on the repairer PO as
        # the Markup line, never inflate the parts base.
        report = _edge_report(
            parts=[('Motor Holdings',
                    [_pline('Bolt kit', 2.5, 100.0, total=999.0)],
                    999.0)],
            labour=4000.0, summary={},
        )
        plan = build_allocation_plan(report)
        parts_row = next(r for r in plan['rows']
                         if r['id'] == 'parts:Motor Holdings')
        self.assertEqual(parts_row['amount'], 250.0)            # qty x unit
        self.assertEqual(parts_row['lines'][0]['qty'], 2.5)     # kept fractional
        self.assertEqual(parts_row['lines'][0]['unit_price'], 100.0)

        s = claims_split(report)
        self.assertEqual(s['motor_centre']['parts_total'], 250.0)
        # The 749 difference is the repairer's markup — counted exactly once.
        self.assertEqual(s['specialised']['markup'], 749.0)
        markup_row = next(r for r in plan['rows'] if r['id'] == 'markup')
        self.assertEqual(markup_row['amount'], 749.0)


class EngineMarkupModeTests(SimpleTestCase):
    """Fuzz case 7 — markup_pct = 0 vs None vs 25."""

    def _report(self):
        # Outside supplier parts 2000 + repairer's OWN parts 1000 + labour.
        return _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 2000.0)], 2000.0),
                   ('Carfil Panel Beaters', [_pline('Filler', 1, 1000.0)], 1000.0)],
            labour=4000.0, summary={},
        )

    def test_markup_pct_zero_produces_no_markup_row(self):
        plan = build_allocation_plan(self._report(), markup_pct=0.0)
        self.assertFalse(any(r['id'] == 'markup' for r in plan['rows']))
        s = claims_split(self._report(), markup_pct=0.0)
        self.assertEqual(s['specialised']['markup'], 0.0)

    def test_markup_pct_none_falls_back_to_embedded_markup(self):
        # No embedded markup in this fixture (subtotal == base) -> no row.
        plan = build_allocation_plan(self._report(), markup_pct=None)
        self.assertFalse(any(r['id'] == 'markup' for r in plan['rows']))
        s = claims_split(self._report())
        self.assertEqual(s['specialised']['markup'], 0.0)

    def test_markup_pct_25_applies_to_outside_parts_only(self):
        # 25% of the OUTSIDE 2000 = 500 — NEVER of the repairer's own 1000.
        plan = build_allocation_plan(self._report(), markup_pct=25.0)
        markup_row = next(r for r in plan['rows'] if r['id'] == 'markup')
        self.assertEqual(markup_row['amount'], 500.0)           # not 750
        self.assertEqual(markup_row['default_vendor'], 'Carfil Panel Beaters')
        s = claims_split(self._report(), markup_pct=25.0)
        self.assertEqual(s['specialised']['markup'], 500.0)


class EngineExcessPriorityTests(SimpleTestCase):
    """Fuzz cases 8 + 9 — typed excess wins even when SMALLER; min-floor
    applies only when excess_min was explicitly entered."""

    def _report(self, excess):
        return _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 2000.0)], 2000.0)],
            labour=4000.0, summary={'Excess': excess},
        )

    def _allocated(self, report):
        return apply_allocations(build_allocation_plan(report), None)

    def test_typed_excess_smaller_than_pct_and_min_still_wins_exactly(self):
        report = self._report(excess=0)
        allocated = self._allocated(report)
        # pct path would give max(5% x 4000, 5000) = 5000; typed 750 wins.
        ex = compute_excess(report, allocated, excess_pct=5.0,
                            excess_min=5000.0, excess_amount=750.0)
        self.assertEqual(ex['amount_incl'], 750.0)
        self.assertEqual(ex['line']['unit_price'], -750.0)
        self.assertTrue(ex['line']['no_vat'])
        s = claims_split(report, excess_pct=0.05, excess_min=5000.0,
                         excess_amount=750.0)
        self.assertEqual(s['specialised']['excess'], 750.0)

    def test_typed_excess_smaller_than_assessment_excess_wins(self):
        report = self._report(excess=3000.0)
        allocated = self._allocated(report)
        ex = compute_excess(report, allocated, None, None, excess_amount=750.0)
        self.assertEqual(ex['amount_incl'], 750.0)
        s = claims_split(report, excess_amount=750.0)
        self.assertEqual(s['specialised']['excess'], 750.0)

    def test_assessment_excess_below_min_is_floored_when_min_given(self):
        report = self._report(excess=900.0)
        allocated = self._allocated(report)
        ex = compute_excess(report, allocated, 5.0, 2000.0)
        self.assertEqual(ex['amount_incl'], 2000.0)             # floored up
        s = claims_split(report, excess_min=2000.0)
        self.assertEqual(s['specialised']['excess'], 2000.0)

    def test_assessment_excess_kept_when_min_absent_no_regression(self):
        report = self._report(excess=900.0)
        allocated = self._allocated(report)
        ex = compute_excess(report, allocated, 5.0, None)
        self.assertEqual(ex['amount_incl'], 900.0)              # NOT floored
        s = claims_split(report)
        self.assertEqual(s['specialised']['excess'], 900.0)


class EngineRepairerCaseVariantTests(SimpleTestCase):
    """Fuzz case 10 — 'ROLLING WHEELS' parts group vs repairer
    'Rolling Wheels (Gabs)': the _name_norm containment match must still
    classify the group as the repairer's own parts."""

    def _report(self):
        return {
            'repairer': 'Rolling Wheels (Gabs)',
            'summary': {},
            'groups': [
                {'kind': 'parts', 'supplier_label': 'ROLLING WHEELS',
                 'subtotal_excl': 3300.0,           # base 3000 + embedded 300
                 'lines': [_pline('Bonnet', 1, 3000.0)]},
                {'kind': 'parts', 'supplier_label': 'Motor Holdings',
                 'subtotal_excl': 2200.0,           # base 2000 + embedded 200
                 'lines': [_pline('Mirror', 1, 2000.0)]},
                {'kind': 'labour', 'supplier_label': 'Rolling Wheels (Gabs)',
                 'subtotal_excl': 5000.0, 'lines': []},
            ],
        }

    def test_split_repairer_parts_populated_and_markup_excludes_them(self):
        s = claims_split(self._report())
        spec = s['specialised']
        self.assertEqual(spec['repairer_parts'], 3000.0)     # own parts, base
        # Markup = the OUTSIDE group's embedded 200 only — the repairer
        # group's 300 is never earned.
        self.assertEqual(spec['markup'], 200.0)
        # Parts PO block = the outside supplier only.
        self.assertEqual(s['motor_centre']['parts_total'], 2000.0)
        # gross = own parts 3000 + labour 5000 + markup 200.
        self.assertEqual(spec['gross_excl'], 8200.0)

    def test_pct_markup_base_excludes_case_variant_repairer_parts(self):
        s = claims_split(self._report(), markup_pct=10.0)
        self.assertEqual(s['specialised']['markup'], 220.0)  # 10% of 2200 only
        plan = build_allocation_plan(self._report(), markup_pct=10.0)
        markup_row = next(r for r in plan['rows'] if r['id'] == 'markup')
        self.assertEqual(markup_row['amount'], 220.0)


class EngineLabourConsolidationTests(SimpleTestCase):
    """REGRESSION for the real bug found in this sweep: with TWO labour
    groups in report_json, claims_split summed both but
    build_allocation_plan billed only the FIRST — the split preview and the
    PO disagreed and the second group's money silently fell off the PO."""

    def _report(self):
        return {
            'repairer': 'Carfil Panel Beaters',
            'summary': {},
            'groups': [
                {'kind': 'labour', 'supplier_label': 'Carfil Panel Beaters',
                 'subtotal_excl': 5000.0, 'lines': []},
                {'kind': 'labour', 'supplier_label': 'Carfil Panel Beaters',
                 'subtotal_excl': 3000.0, 'lines': []},
            ],
        }

    def test_plan_labour_row_sums_all_labour_groups(self):
        plan = build_allocation_plan(self._report())
        labour_rows = [r for r in plan['rows'] if r['id'] == 'labour']
        self.assertEqual(len(labour_rows), 1)        # still ONE consolidated row
        self.assertEqual(labour_rows[0]['amount'], 8000.0)   # was 5000 pre-fix
        self.assertEqual(labour_rows[0]['lines'],
                         [{'description': 'Labour', 'qty': 1.0,
                           'unit_price': 8000.0}])

    def test_plan_and_split_agree_on_labour_money(self):
        s = claims_split(self._report())
        plan = build_allocation_plan(self._report())
        labour_row = next(r for r in plan['rows'] if r['id'] == 'labour')
        self.assertEqual(s['specialised']['labour'], labour_row['amount'])
        # Full parity: split gross == sum of everything routed to the repairer.
        rep_total = round(sum(
            r['amount'] for r in plan['rows']
            if r['default_vendor'] == 'Carfil Panel Beaters'
            and not r['id'].startswith('parts:')
        ), 2)
        self.assertEqual(s['specialised']['gross_excl'], rep_total)

    def test_single_labour_group_unchanged(self):
        # No regression on the normal parser shape (exactly one labour group).
        report = _edge_report(parts=[], labour=9692.0, summary={})
        plan = build_allocation_plan(report)
        labour_row = next(r for r in plan['rows'] if r['id'] == 'labour')
        self.assertEqual(labour_row['amount'], 9692.0)


# ===========================================================================
# API — full create-pos / plan path (TestCase, mirrors test_claims_api.py)
# ===========================================================================

from procurement.claims_api import ClaimsAssessmentViewSet          # noqa: E402
from procurement.claims_models import ClaimsAssessment              # noqa: E402
from procurement.models import PurchaseOrder                        # noqa: E402


class _ApiMixin:
    """Request plumbing shared by the API edge tests (fixture style mirrors
    test_claims_api.py — company/user/factory set in setUpTestData)."""

    def _make_assessment(self, report, **overrides):
        defaults = dict(
            company=self.company,
            created_by=self.user,
            status=ClaimsAssessment.Status.READY_FOR_REVIEW,
            report_json=report,
            claim_number='EDGE-001',
        )
        defaults.update(overrides)
        return ClaimsAssessment.objects.create(**defaults)

    def _create_pos(self, assessment):
        view = ClaimsAssessmentViewSet.as_view({'post': 'create_pos'})
        request = self.factory.post(
            f'/api/v1/claims-po/{assessment.pk}/create-pos/', {}, format='json',
        )
        force_authenticate(request, user=self.user)
        return view(request, pk=str(assessment.pk))

    def _get_plan(self, assessment):
        view = ClaimsAssessmentViewSet.as_view({'get': 'plan'})
        request = self.factory.get(f'/api/v1/claims-po/{assessment.pk}/plan/')
        force_authenticate(request, user=self.user)
        return view(request, pk=str(assessment.pk))


def _api_test_base(company_code, username, vendor_names):
    """Standard DB fixtures: BWP + Company + 14% VAT_STD + vendors + superuser."""
    from billing.models import Contact
    from core.models import Company, Currency, TaxRate

    Currency.objects.get_or_create(
        code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
    )
    company = Company.objects.create(code=company_code,
                                     name=f'{company_code} Co.')
    vat = TaxRate.objects.create(
        tax_code='VAT_STD', name='Standard Rate 14%',
        rate=Decimal('14.00'), is_active=True,
        effective_from=date(2026, 1, 1),
    )
    contacts = {
        name: Contact.objects.create(name=name, contact_type='vendor',
                                     company=company)
        for name in vendor_names
    }
    user = User.objects.create_user(username, password='x', is_superuser=True)
    return company, vat, contacts, user


class ApiUnicodeVendorTests(_ApiMixin, TestCase):
    """Fuzz case 3 — ampersand / apostrophe vendor labels through the FULL
    create-pos path: resolve cleanly, no exception, correct 2-PO split."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.vat, contacts, cls.user = _api_test_base(
            'EDGU', 'edgeunicode',
            ["Kgalagadi & Sons (Pty) Ltd", "Mokolodi's Panel & Paint"],
        )
        cls.parts_contact = contacts["Kgalagadi & Sons (Pty) Ltd"]
        cls.repairer_contact = contacts["Mokolodi's Panel & Paint"]
        cls.factory = APIRequestFactory()

    def test_unicode_labels_resolve_and_create_both_pos(self):
        report = _edge_report(
            repairer="Mokolodi's Panel & Paint",
            parts=[("Kgalagadi & Sons (Pty) Ltd",
                    [_pline('Windscreen — laminated', 1, 1000.0)], 1000.0)],
            labour=12000.0, summary={'Excess': 500.0},
        )
        assessment = self._make_assessment(report)
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])

        self.assertEqual(PurchaseOrder.objects.count(), 2)
        repairer_po = PurchaseOrder.objects.get(supplier=self.repairer_contact)
        parts_po = PurchaseOrder.objects.get(supplier=self.parts_contact)
        for po in (repairer_po, parts_po):
            self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
            self.assertEqual(po.currency_code_id, 'BWP')

        # Repairer: labour 12000 (VAT) - excess 500 (no VAT).
        self.assertEqual(repairer_po.subtotal, Decimal('11500.00'))
        self.assertEqual(repairer_po.tax_total, Decimal('1680.00'))
        excess_lines = [ln for ln in repairer_po.lines.all() if ln.unit_price < 0]
        self.assertEqual(len(excess_lines), 1)
        self.assertEqual(excess_lines[0].unit_price, Decimal('-500.00'))
        self.assertIsNone(excess_lines[0].tax_code)
        # Parts at BASE with VAT.
        self.assertEqual(parts_po.subtotal, Decimal('1000.00'))
        self.assertEqual(parts_po.total_amount, Decimal('1140.00'))


class ApiBucketingTests(_ApiMixin, TestCase):
    """Fuzz cases 1 (API leg), 4, 5 — line/bucket behaviour on create-pos."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.vat, contacts, cls.user = _api_test_base(
            'EDGB', 'edgebucket',
            ['Carfil Panel Beaters', 'Motor Holdings',
             'Gaborone Glass', 'Delta Spares'],
        )
        cls.repairer_contact = contacts['Carfil Panel Beaters']
        cls.mh = contacts['Motor Holdings']
        cls.glass = contacts['Gaborone Glass']
        cls.delta = contacts['Delta Spares']
        cls.factory = APIRequestFactory()

    def test_two_parts_groups_same_supplier_merge_into_one_po(self):
        # Two separate parts GROUPS under the same supplier label must bucket
        # into ONE parts PO carrying ALL the lines (never two POs — the
        # ERPNext analogue is its duplicate-item guard; ours is the merge).
        report = _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 600.0)], 600.0),
                   ('Motor Holdings', [_pline('Grille', 1, 400.0)], 400.0)],
            labour=10000.0, summary={'Excess': 1000.0},
        )
        assessment = self._make_assessment(report)
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])

        self.assertEqual(PurchaseOrder.objects.count(), 2)   # NOT three
        parts_po = PurchaseOrder.objects.get(supplier=self.mh)
        self.assertEqual(parts_po.lines.count(), 2)          # both groups' lines
        self.assertEqual(parts_po.subtotal, Decimal('1000.00'))
        self.assertEqual(parts_po.total_amount, Decimal('1140.00'))
        descriptions = {ln.description for ln in parts_po.lines.all()}
        self.assertEqual(descriptions, {'Bumper', 'Grille'})

    def test_three_outside_suppliers_make_n_plus_one_draft_pos(self):
        report = _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 1000.0)], 1000.0),
                   ('Gaborone Glass', [_pline('Windscreen', 1, 2000.0)], 2000.0),
                   ('Delta Spares', [_pline('Radiator', 1, 1500.0)], 1500.0)],
            labour=9000.0, summary={'Excess': 800.0},
        )
        assessment = self._make_assessment(report)
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])

        pos = PurchaseOrder.objects.all()
        self.assertEqual(pos.count(), 4)                     # 3 parts + repairer
        for po in pos:
            self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
            self.assertEqual(po.currency_code_id, 'BWP')
            self.assertIsNone(po.submitted_at)
        kinds = sorted(r['kind'] for r in response.data['results'])
        self.assertEqual(kinds, ['parts', 'parts', 'parts', 'repairer'])
        # Excess (negative line) ONLY on the repairer PO.
        for po in pos:
            negatives = [ln for ln in po.lines.all() if ln.unit_price < 0]
            if po.supplier_id == self.repairer_contact.pk:
                self.assertEqual(len(negatives), 1)
                self.assertEqual(negatives[0].unit_price, Decimal('-800.00'))
            else:
                self.assertEqual(negatives, [])
        # Each parts PO at BASE + VAT.
        self.assertEqual(PurchaseOrder.objects.get(supplier=self.mh)
                         .total_amount, Decimal('1140.00'))
        self.assertEqual(PurchaseOrder.objects.get(supplier=self.glass)
                         .total_amount, Decimal('2280.00'))
        self.assertEqual(PurchaseOrder.objects.get(supplier=self.delta)
                         .total_amount, Decimal('1710.00'))

    def test_zero_qty_and_zero_price_po_lines_recorded(self):
        # RECORDED behaviour (differs from ERPNext, which rejects zero qty):
        # a zero-qty assessment line reaches the PO as qty 1 (parser-rule
        # coercion), a zero-price line lands with line_total 0. Neither
        # crashes create-pos or blocks the PO.
        report = _edge_report(
            parts=[('Motor Holdings',
                    [_pline('Clip', 0, 500.0),
                     _pline('Gasket (free)', 3, 0.0)],
                    500.0)],
            labour=8000.0, summary={'Excess': 400.0},
        )
        assessment = self._make_assessment(report)
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)

        parts_po = PurchaseOrder.objects.get(supplier=self.mh)
        self.assertEqual(parts_po.lines.count(), 2)
        clip = parts_po.lines.get(description='Clip')
        self.assertEqual(clip.quantity, Decimal('1.0000'))    # coerced 0 -> 1
        self.assertEqual(clip.unit_price, Decimal('500.00'))
        self.assertEqual(clip.line_total, Decimal('500.00'))
        gasket = parts_po.lines.get(description='Gasket (free)')
        self.assertEqual(gasket.quantity, Decimal('3.0000'))
        self.assertEqual(gasket.unit_price, Decimal('0.00'))
        self.assertEqual(gasket.line_total, Decimal('0.00'))
        self.assertEqual(parts_po.subtotal, Decimal('500.00'))


class ApiSupplierVatTests(_ApiMixin, TestCase):
    """Fuzz case 6 — supplier_settings VAT off for the parts supplier."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.vat, contacts, cls.user = _api_test_base(
            'EDGV', 'edgevat', ['Carfil Panel Beaters', 'Motor Holdings'],
        )
        cls.repairer_contact = contacts['Carfil Panel Beaters']
        cls.mh = contacts['Motor Holdings']
        cls.factory = APIRequestFactory()

    def test_vat_off_supplier_gets_no_tax_and_po_stays_bwp(self):
        report = _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 1000.0)], 1000.0)],
            labour=8000.0, summary={'Excess': 400.0},
        )
        assessment = self._make_assessment(
            report,
            supplier_settings={'Motor Holdings':
                               {'vat': False, 'currency': 'ZAR'}},
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])

        parts_po = PurchaseOrder.objects.get(supplier=self.mh)
        for ln in parts_po.lines.all():
            self.assertIsNone(ln.tax_code)                    # VAT off
            self.assertEqual(ln.tax_amount, Decimal('0.00'))
        self.assertEqual(parts_po.tax_total, Decimal('0.00'))
        self.assertEqual(parts_po.total_amount, Decimal('1000.00'))
        # BWP-only rule: currency CHOICE recorded, PO itself never converted.
        self.assertEqual(parts_po.currency_code_id, 'BWP')
        parts_result = next(r for r in response.data['results']
                            if r['kind'] == 'parts')
        self.assertEqual(parts_result['supplier_currency_choice'], 'ZAR')
        self.assertEqual(parts_result['currency'], 'BWP')
        # The repairer keeps standard VAT — the setting is per supplier.
        repairer_po = PurchaseOrder.objects.get(supplier=self.repairer_contact)
        self.assertEqual(repairer_po.tax_total, Decimal('1120.00'))  # 14% x 8000


class ApiVendorIdEdgeTests(_ApiMixin, TestCase):
    """Fuzz case 11 (beyond the basics already covered in test_claims_api):
    a vendor_id pointing at an INACTIVE contact, or at a contact belonging to
    ANOTHER company, must fall back to label matching — never resolve to the
    out-of-scope record, never crash."""

    @classmethod
    def setUpTestData(cls):
        from billing.models import Contact
        from core.models import Company

        cls.company, cls.vat, contacts, cls.user = _api_test_base(
            'EDGI', 'edgevendorid', ['Carfil Panel Beaters', 'Motor Holdings'],
        )
        cls.repairer_contact = contacts['Carfil Panel Beaters']
        cls.mh_active = contacts['Motor Holdings']
        # Inactive vendor in the SAME company.
        cls.mh_inactive = Contact.objects.create(
            name='Motor Holdings Old', contact_type='vendor',
            company=cls.company, is_active=False,
        )
        # Active vendor in a DIFFERENT company — out of scope.
        cls.other_company = Company.objects.create(code='EDGX',
                                                   name='Other Co.')
        cls.cross_contact = Contact.objects.create(
            name='Cross Company Spares', contact_type='vendor',
            company=cls.other_company,
        )
        cls.factory = APIRequestFactory()

    def _report(self):
        return _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 1000.0)], 1000.0)],
            labour=8000.0, summary={'Excess': 400.0},
        )

    def test_vendor_id_of_inactive_contact_falls_back_to_label(self):
        assessment = self._make_assessment(
            self._report(),
            line_allocations=[{'id': 'parts:Motor Holdings',
                               'vendor': 'Motor Holdings',
                               'vendor_id': str(self.mh_inactive.pk)}],
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])
        # PO landed on the ACTIVE label match — never on the inactive record.
        self.assertEqual(PurchaseOrder.objects
                         .filter(supplier=self.mh_active).count(), 1)
        self.assertEqual(PurchaseOrder.objects
                         .filter(supplier=self.mh_inactive).count(), 0)

    def test_vendor_id_of_other_company_contact_falls_back_to_label(self):
        assessment = self._make_assessment(
            self._report(),
            line_allocations=[{'id': 'parts:Motor Holdings',
                               'vendor': 'Motor Holdings',
                               'vendor_id': str(self.cross_contact.pk)}],
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])
        # In-scope label match wins; the cross-company contact gets NOTHING.
        self.assertEqual(PurchaseOrder.objects
                         .filter(supplier=self.mh_active).count(), 1)
        self.assertEqual(PurchaseOrder.objects
                         .filter(supplier=self.cross_contact).count(), 0)


class ApiExcessParityTests(_ApiMixin, TestCase):
    """Fuzz case 12 — PARITY INVARIANT across all three excess paths: the
    excess shown on /plan/ (split.specialised.excess) must EQUAL the negative
    line create-pos writes on the repairer PO. Three fixtures: typed amount,
    assessment figure + min floor, and pure percentage."""

    @classmethod
    def setUpTestData(cls):
        cls.company, cls.vat, contacts, cls.user = _api_test_base(
            'EDGP', 'edgeparity', ['Carfil Panel Beaters', 'Motor Holdings'],
        )
        cls.repairer_contact = contacts['Carfil Panel Beaters']
        cls.mh = contacts['Motor Holdings']
        cls.factory = APIRequestFactory()

    def _report(self, excess):
        # parts 1000 + labour 8000 + paint 2000 (repairer base 10000).
        return _edge_report(
            parts=[('Motor Holdings', [_pline('Bumper', 1, 1000.0)], 1000.0)],
            labour=8000.0,
            summary={'Excess': excess, 'Paint': 2000.0},
        )

    def test_plan_excess_always_equals_created_po_excess_line(self):
        fixtures = [
            # (name, summary Excess, model overrides, expected excess)
            ('typed-amount-wins', 3000.0,
             {'excess_amount': Decimal('1234.56')}, 1234.56),
            ('assessment-plus-min-floor', 900.0,
             {'excess_min': Decimal('2000.00')}, 2000.0),
            ('pure-percent-path', 0,
             {'excess_pct': Decimal('7.50'), 'excess_min': Decimal('0.00')},
             750.0),   # 7.5% of labour 8000 + paint 2000
        ]
        for name, summary_excess, overrides, expected in fixtures:
            with self.subTest(fixture=name):
                assessment = self._make_assessment(
                    self._report(summary_excess), **overrides)

                plan_resp = self._get_plan(assessment)
                self.assertEqual(plan_resp.status_code, 200, plan_resp.data)
                seen = plan_resp.data['split']['specialised']['excess']
                self.assertEqual(seen, expected)

                response = self._create_pos(assessment)
                self.assertEqual(response.status_code, 201, response.data)
                repairer_result = next(r for r in response.data['results']
                                       if r['kind'] == 'repairer')
                self.assertTrue(repairer_result['excess_attached'])
                po = PurchaseOrder.objects.get(pk=repairer_result['id'])
                negatives = [ln for ln in po.lines.all() if ln.unit_price < 0]
                self.assertEqual(len(negatives), 1)
                # THE parity rule: what the user saw is what the PO carries.
                self.assertEqual(float(-negatives[0].unit_price), seen)
                self.assertIsNone(negatives[0].tax_code)      # never VATed
