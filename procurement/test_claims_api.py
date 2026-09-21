"""
procurement/test_claims_api.py — Claims PO API (Phase 5) tests.

Covers the create-pos generation endpoint (procurement/claims_api.py):

  * two DRAFT POs from a two-vendor allocation (repairer + parts supplier)
  * negative, NO-VAT excess line lands on the repairer's PO
  * VAT (14% VAT_STD) applied on the repair work + parts, totals recalc'd
  * po_results populated + status -> POS_CREATED
  * nothing is ever submitted / approved (draft-only, no money moves)
  * an unresolved vendor label produces an error row, no PO for that bucket,
    and — when the unresolved vendor is the repairer — the excess is NOT
    silently dropped.

Fixtures are DB-real but minimal: BWP, one Company, the standard 14% VAT
TaxRate, two vendor Contacts, and a report_json shaped exactly like
claims_parser.parse() output.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Contact
from core.models import Company, Currency, TaxRate
from procurement.claims_api import ClaimsAssessmentViewSet
from procurement.claims_models import ClaimsAssessment
from procurement.models import PurchaseOrder


def _report(repairer='Carfil Panel Beaters'):
    """report_json fixture — parts from Motor Holdings, labour from the
    repairer, summary with Paint + Excess. Same shape as claims_parser."""
    return {
        'claim_no':       'G2026004607',
        'policy_no':      'COMG2026123456',
        'assessment_no':  'ASS-0042',
        'client_name':    'Thabo Client',
        'client_contact': '71234567',
        'vehicle':        'Toyota Hilux 2.8 GD-6',
        'vehicle_reg':    'B123ABC',
        'repairer':       repairer,
        'currency':       'BWP',
        'summary': {
            'Parts':  10000.0,
            'Labour': 5000.0,
            'Paint':  2000.0,
            'Excess': 3000.0,
        },
        'groups': [
            {
                'kind': 'parts',
                'supplier_label': 'Motor Holdings',
                'subtotal_excl': 10000.0,
                'lines': [
                    {'code': 'BMP01', 'description': 'Front bumper',
                     'supplier': 'Motor Holdings',
                     'qty': 1.0, 'unit_price': 6000.0, 'total': 6000.0,
                     'markup': 0.0},
                    {'code': 'GRL02', 'description': 'Radiator grille',
                     'supplier': 'Motor Holdings',
                     'qty': 1.0, 'unit_price': 4000.0, 'total': 4000.0,
                     'markup': 0.0},
                ],
            },
            {
                'kind': 'labour',
                'supplier_label': repairer,
                'subtotal_excl': 5000.0,
                'lines': [
                    {'code': 'L1', 'description': 'Panel beating',
                     'units': 10.0, 'rate': 500.0, 'total': 5000.0},
                ],
            },
        ],
    }


class ClaimsPOCreateTest(TestCase):
    """POST /api/v1/claims-po/{id}/create-pos/ — the two-PO generation."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TESTC', name='Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%',
            rate=Decimal('14.00'), is_active=True,
            effective_from=date(2026, 1, 1),
        )
        cls.repairer_contact = Contact.objects.create(
            name='Carfil Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=cls.company,
        )
        cls.parts_contact = Contact.objects.create(
            name='Motor Holdings', contact_type='vendor',
            company=cls.company,
        )
        # Superuser => unrestricted company bucket ('*') like existing PO tests.
        cls.user = User.objects.create_user(
            'claimsuser', password='x', is_superuser=True,
        )
        cls.factory = APIRequestFactory()

    def _make_assessment(self, report=None, **overrides):
        defaults = dict(
            company=self.company,
            created_by=self.user,
            status=ClaimsAssessment.Status.READY_FOR_REVIEW,
            report_json=report or _report(),
            claim_number='G2026004607',
            policy_number='COMG2026123456',
            assessment_number='ASS-0042',
            client_name='Thabo Client',
            registration='B123ABC',
            vehicle='Toyota Hilux 2.8 GD-6',
            contact_details='71234567',
            line_allocations=[
                {'id': 'labour',              'vendor': 'Carfil Panel Beaters'},
                {'id': 'parts:Motor Holdings', 'vendor': 'Motor Holdings'},
            ],
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

    # ------------------------------------------------------------------
    # Happy path — two draft POs
    # ------------------------------------------------------------------

    def test_creates_two_draft_pos_with_excess_and_vat(self):
        assessment = self._make_assessment()
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])
        self.assertEqual(len(response.data['results']), 2)

        # TWO POs, both DRAFT, department = claims, currency BWP.
        pos = PurchaseOrder.objects.all()
        self.assertEqual(pos.count(), 2)
        for po in pos:
            self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
            self.assertEqual(po.department, 'claims')
            self.assertEqual(po.currency_code_id, 'BWP')
            # NEVER submitted / approved — no money moves from this endpoint.
            self.assertIsNone(po.submitted_at)
            self.assertIsNone(po.fm_approved_at)
            self.assertIsNone(po.cfo_approved_at)
            # Claim metadata block on the PO.
            self.assertIn('G2026004607', po.justification)
            self.assertIn('COMG2026123456', po.justification)
            self.assertIn('B123ABC', po.justification)
            self.assertEqual(po.related_claim_reference, 'G2026004607')

        repairer_po = pos.get(supplier=self.repairer_contact)
        parts_po    = pos.get(supplier=self.parts_contact)

        # Repairer PO: Labour 5000 + Paint 2000 (both VAT) − excess 3000 (no VAT).
        excess_lines = [ln for ln in repairer_po.lines.all()
                        if ln.unit_price < 0]
        self.assertEqual(len(excess_lines), 1)
        excess_line = excess_lines[0]
        self.assertEqual(excess_line.unit_price, Decimal('-3000.00'))
        self.assertIsNone(excess_line.tax_code)
        self.assertEqual(excess_line.tax_amount, Decimal('0.00'))
        self.assertIn('excess', excess_line.description.lower())
        # Totals recalc'd: (5000+2000-3000) + 14% on 7000 only.
        self.assertEqual(repairer_po.subtotal,     Decimal('4000.00'))
        self.assertEqual(repairer_po.tax_total,    Decimal('980.00'))
        self.assertEqual(repairer_po.total_amount, Decimal('4980.00'))

        # Parts PO: parts at BASE price with VAT, no excess, no negative lines.
        self.assertEqual(parts_po.lines.count(), 2)
        for ln in parts_po.lines.all():
            self.assertGreater(ln.unit_price, 0)
            self.assertEqual(ln.tax_code_id, self.vat.pk)
            self.assertEqual(ln.tax_rate, Decimal('14.00'))
        self.assertEqual(parts_po.subtotal,     Decimal('10000.00'))
        self.assertEqual(parts_po.tax_total,    Decimal('1400.00'))
        self.assertEqual(parts_po.total_amount, Decimal('11400.00'))

        # Assessment outcome persisted.
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, ClaimsAssessment.Status.POS_CREATED)
        self.assertEqual(len(assessment.po_results), 2)
        kinds = {r['kind'] for r in assessment.po_results}
        self.assertEqual(kinds, {'repairer', 'parts'})
        for r in assessment.po_results:
            self.assertIn('po_number', r)
            self.assertEqual(r['status'], 'draft')
            self.assertEqual(r['currency'], 'BWP')

    # ------------------------------------------------------------------
    # Unresolved vendor — error row, no PO, excess never dropped silently
    # ------------------------------------------------------------------

    def test_unresolved_repairer_blocks_excess_but_not_parts_po(self):
        report = _report(repairer='Unknown Panel Shop')
        assessment = self._make_assessment(
            report=report,
            line_allocations=[
                {'id': 'labour',              'vendor': 'Unknown Panel Shop'},
                {'id': 'parts:Motor Holdings', 'vendor': 'Motor Holdings'},
            ],
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)

        # Only the parts PO was created — no PO for the unresolved repairer.
        pos = PurchaseOrder.objects.all()
        self.assertEqual(pos.count(), 1)
        self.assertEqual(pos.first().supplier, self.parts_contact)
        self.assertEqual(pos.first().status, PurchaseOrder.Status.DRAFT)

        errors = response.data['errors']
        self.assertTrue(errors)
        joined = ' '.join(e['error'] for e in errors)
        # The unresolved label is flagged...
        self.assertIn('Unknown Panel Shop', joined)
        # ...and the excess is NOT silently dropped: an explicit error names it.
        excess_errors = [e for e in errors if e.get('kind') == 'excess']
        self.assertEqual(len(excess_errors), 1)
        self.assertIn('excess', excess_errors[0]['error'].lower())
        self.assertIn('3,000.00', excess_errors[0]['error'])

        # Errors => the assessment stays READY_FOR_REVIEW for a fix + re-run.
        assessment.refresh_from_db()
        self.assertEqual(assessment.status,
                         ClaimsAssessment.Status.READY_FOR_REVIEW)
        # The outcome (1 PO + errors) is still recorded for the review screen.
        self.assertTrue(any('po_number' in r for r in assessment.po_results))
        self.assertTrue(any('error' in r for r in assessment.po_results))

    # ------------------------------------------------------------------
    # PARITY — the excess the user SEES on /plan/ must equal the excess
    # line /create-pos/ actually puts on the repairer PO (same inputs).
    # ------------------------------------------------------------------

    def _get_plan(self, assessment):
        view = ClaimsAssessmentViewSet.as_view({'get': 'plan'})
        request = self.factory.get(f'/api/v1/claims-po/{assessment.pk}/plan/')
        force_authenticate(request, user=self.user)
        return view(request, pk=str(assessment.pk))

    def test_plan_excess_equals_created_po_excess_pct_path(self):
        # %-path: no assessment excess, excess_pct/min typed on the claim.
        report = _report()
        report['summary']['Excess'] = 0
        assessment = self._make_assessment(
            report=report,
            excess_pct=Decimal('5.00'),      # stored as PERCENT
            excess_min=Decimal('5000.00'),
        )
        plan_resp = self._get_plan(assessment)
        seen = plan_resp.data['split']['specialised']['excess']
        self.assertEqual(seen, 5000.0)       # max(5% x 7000, 5000) = floor

        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        repairer_po = PurchaseOrder.objects.get(supplier=self.repairer_contact)
        excess_line = [ln for ln in repairer_po.lines.all() if ln.unit_price < 0][0]
        self.assertEqual(excess_line.unit_price, Decimal('-5000.00'))
        self.assertEqual(float(-excess_line.unit_price), seen)

    def test_plan_excess_equals_created_po_excess_after_reassignment(self):
        # Reassigning a row away from the repairer changes the %-base the PO
        # uses — the plan must show THAT number, not a stale formula.
        report = _report()
        report['summary']['Excess'] = 0
        assessment = self._make_assessment(
            report=report,
            excess_pct=Decimal('5.00'),
            excess_min=Decimal('0.00'),      # explicit zero floor -> pure %
            line_allocations=[
                {'id': 'labour', 'vendor': 'Carfil Panel Beaters'},
                {'id': 'paint',  'vendor': 'Motor Holdings'},   # moved away
            ],
        )
        plan_resp = self._get_plan(assessment)
        seen = plan_resp.data['split']['specialised']['excess']
        self.assertEqual(seen, 250.0)        # 5% of labour 5000 only

        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        repairer_po = PurchaseOrder.objects.get(supplier=self.repairer_contact)
        excess_line = [ln for ln in repairer_po.lines.all() if ln.unit_price < 0][0]
        self.assertEqual(float(-excess_line.unit_price), seen)

    # ------------------------------------------------------------------
    # Re-run after a partial failure must NOT duplicate the created PO
    # ------------------------------------------------------------------

    def test_rerun_after_partial_failure_does_not_duplicate_parts_po(self):
        report = _report(repairer='Unknown Panel Shop')
        assessment = self._make_assessment(
            report=report,
            line_allocations=[
                {'id': 'labour',               'vendor': 'Unknown Panel Shop'},
                {'id': 'parts:Motor Holdings', 'vendor': 'Motor Holdings'},
            ],
        )
        first = self._create_pos(assessment)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(PurchaseOrder.objects.count(), 1)   # parts PO only

        # User fixes the repairer (and paint) allocation, then re-runs.
        assessment.refresh_from_db()
        assessment.line_allocations = [
            {'id': 'labour',               'vendor': 'Carfil Panel Beaters'},
            {'id': 'paint',                'vendor': 'Carfil Panel Beaters'},
            {'id': 'parts:Motor Holdings', 'vendor': 'Motor Holdings'},
        ]
        assessment.save()
        second = self._create_pos(assessment)
        self.assertEqual(second.status_code, 201, second.data)

        # ONE parts PO (not two) + the new repairer PO.
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.parts_contact).count(), 1)
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.repairer_contact).count(), 1)

        # Outcome record keeps BOTH POs; status finally flips.
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, ClaimsAssessment.Status.POS_CREATED)
        created = [r for r in assessment.po_results if r.get('id')]
        self.assertEqual(len(created), 2)
        # The (second-run) repairer PO carries the excess deduction.
        repairer_po = PurchaseOrder.objects.get(supplier=self.repairer_contact)
        self.assertTrue(any(ln.unit_price < 0 for ln in repairer_po.lines.all()))

    def test_prior_repairer_po_without_excess_is_flagged_on_rerun(self):
        # If the repairer PO was created on an earlier run WITHOUT the excess
        # (e.g. the excess was typed afterwards), the dedupe skip must flag
        # the missing excess — never silently drop it.
        assessment = self._make_assessment(
            excess_amount=Decimal('3000.00'),
            po_results=[{'id': 'fake-prior-po', 'po_number': 'PO-PRIOR',
                         'supplier': 'Carfil Panel Beaters (Pty) Ltd',
                         'supplier_id': str(self.repairer_contact.pk),
                         'kind': 'repairer', 'excess_attached': False}],
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)
        # No duplicate repairer PO; parts PO still created.
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.repairer_contact).count(), 0)
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.parts_contact).count(), 1)
        excess_errors = [e for e in response.data['errors'] if e.get('kind') == 'excess']
        self.assertEqual(len(excess_errors), 1)
        self.assertIn('already exists', excess_errors[0]['error'])

    # ------------------------------------------------------------------
    # Parts-only assessment — excess is flagged, never silently dropped
    # ------------------------------------------------------------------

    def test_parts_only_assessment_flags_undeductible_excess(self):
        report = _report()
        report['groups'] = [g for g in report['groups'] if g['kind'] == 'parts']
        report['summary'] = {'Parts': 10000.0, 'Excess': 3000.0}
        assessment = self._make_assessment(
            report=report,
            line_allocations=[{'id': 'parts:Motor Holdings', 'vendor': 'Motor Holdings'}],
        )
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)

        # Parts PO still created…
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.first().supplier, self.parts_contact)
        # …but the undeductible excess is an explicit error row.
        excess_errors = [e for e in response.data['errors'] if e.get('kind') == 'excess']
        self.assertEqual(len(excess_errors), 1)
        self.assertIn('3,000.00', excess_errors[0]['error'])
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, ClaimsAssessment.Status.READY_FOR_REVIEW)

    # ------------------------------------------------------------------
    # Excess larger than the repairer PO — no negative draft PO
    # ------------------------------------------------------------------

    def test_excess_exceeding_repairer_po_value_is_blocked(self):
        assessment = self._make_assessment(excess_amount=Decimal('999999.00'))
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)

        # No repairer PO (would have been negative); parts PO unaffected.
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.first().supplier, self.parts_contact)
        excess_errors = [e for e in response.data['errors'] if e.get('kind') == 'excess']
        self.assertEqual(len(excess_errors), 1)
        self.assertIn('negative', excess_errors[0]['error'])
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, ClaimsAssessment.Status.READY_FOR_REVIEW)

    # ------------------------------------------------------------------
    # Guard — no duplicate generation
    # ------------------------------------------------------------------

    def test_create_pos_twice_is_blocked(self):
        assessment = self._make_assessment()
        first = self._create_pos(assessment)
        self.assertEqual(first.status_code, 201, first.data)
        second = self._create_pos(assessment)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(PurchaseOrder.objects.count(), 2)

    # ------------------------------------------------------------------
    # Review payload — GET /claims-po/{id}/plan/
    # ------------------------------------------------------------------

    def test_plan_endpoint_returns_plan_split_saved_and_meta(self):
        assessment = self._make_assessment()
        view = ClaimsAssessmentViewSet.as_view({'get': 'plan'})
        request = self.factory.get(f'/api/v1/claims-po/{assessment.pk}/plan/')
        force_authenticate(request, user=self.user)
        response = view(request, pk=str(assessment.pk))
        self.assertEqual(response.status_code, 200, response.data)

        plan = response.data['plan']
        row_ids = {r['id'] for r in plan['rows']}
        self.assertIn('labour', row_ids)
        self.assertIn('parts:Motor Holdings', row_ids)
        self.assertEqual(plan['repairer'], 'Carfil Panel Beaters')

        split = response.data['split']
        self.assertEqual(split['motor_centre']['parts_total'], 10000.0)
        self.assertEqual(split['specialised']['excess'], 3000.0)

        self.assertEqual(response.data['meta']['claim_number'], 'G2026004607')
        self.assertEqual(
            response.data['saved']['line_allocations'],
            assessment.line_allocations,
        )


def _b915bel_report():
    """The real B915BEL assessment shape (Kao end-to-end test 2026-07-06):
    the repairer 'Rolling Wheels' supplies 6 parts ITSELF (no markup),
    'MOTOR CENTRE' supplies 2 mirrors with embedded markup 670.96."""
    return {
        'claim_no':    'B915BEL',
        'repairer':    'Rolling Wheels',
        'currency':    'BWP',
        'summary': {'Paint': 17981.41, 'Sundries': 91.44, 'Excess': 3500.0},
        'groups': [
            {'kind': 'parts', 'supplier_label': 'ROLLING WHEELS',
             'subtotal_excl': 5788.84,          # repairer's own — NO markup
             'lines': [
                 {'description': 'Bonnet',       'qty': 1.0, 'unit_price': 1200.00},
                 {'description': 'Grille',       'qty': 1.0, 'unit_price': 950.50},
                 {'description': 'Headlamp LH',  'qty': 1.0, 'unit_price': 875.34},
                 {'description': 'Bumper front', 'qty': 1.0, 'unit_price': 1100.00},
                 {'description': 'Fender LH',    'qty': 1.0, 'unit_price': 823.00},
                 {'description': 'Bracket set',  'qty': 1.0, 'unit_price': 840.00},
             ]},
            {'kind': 'parts', 'supplier_label': 'MOTOR CENTRE',
             'subtotal_excl': 3354.76,          # base 2,683.80 + markup 670.96
             'lines': [
                 {'description': 'Mirror LH', 'qty': 1.0, 'unit_price': 1341.90},
                 {'description': 'Mirror RH', 'qty': 1.0, 'unit_price': 1341.90},
             ]},
            {'kind': 'labour', 'supplier_label': 'Rolling Wheels',
             'subtotal_excl': 9692.0,
             'lines': [{'description': 'Panel beating', 'units': 0.0,
                        'rate': 0.0, 'total': 9692.0}]},
        ],
    }


class ClaimsVendorPickTest(TestCase):
    """Fix 2 — the reviewer picks a REAL billing.Contact per row (vendor_id):
    an explicit vendor_id bypasses name matching (the two-'Rolling Wheels'
    ambiguity that blocked the B915BEL repairer PO), and /plan/ ships the
    vendor list + per-row auto-match the picker needs."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TESTV', name='Vendor Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%',
            rate=Decimal('14.00'), is_active=True,
            effective_from=date(2026, 1, 1),
        )
        # TWO genuinely DIFFERENT vendors that both substring-match the label
        # 'Rolling Wheels' — a real ambiguity. NB: same-name duplicates (e.g.
        # 'Rolling Wheels' vs 'Rolling Wheels (Pty) Ltd') are no longer
        # ambiguous — they collapse to one canonical contact (duplicate-vendor
        # collapse, 2026-07-08); that rule has its own test below.
        cls.rolling_a = Contact.objects.create(
            name='Rolling Wheels Gaborone', contact_type='vendor',
            company=cls.company,
        )
        cls.rolling_b = Contact.objects.create(
            name='Rolling Wheels Francistown', contact_type='vendor',
            company=cls.company,
        )
        cls.motor = Contact.objects.create(
            name='Motor Centre', contact_type='vendor', company=cls.company,
        )
        cls.user = User.objects.create_user(
            'claimsvendor', password='x', is_superuser=True,
        )
        cls.factory = APIRequestFactory()

    def _make_assessment(self, report, line_allocations):
        return ClaimsAssessment.objects.create(
            company=self.company,
            created_by=self.user,
            status=ClaimsAssessment.Status.READY_FOR_REVIEW,
            report_json=report,
            claim_number='B915BEL',
            line_allocations=line_allocations,
        )

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

    # ------------------------------------------------------------------
    # (a) ambiguous label + explicit vendor_id -> PO for THAT contact
    # ------------------------------------------------------------------

    def test_ambiguous_label_without_vendor_id_is_error(self):
        # Control: with NO vendor_id the two 'Rolling Wheels' records make
        # the label ambiguous — no repairer PO, explicit error rows.
        report = _b915bel_report()
        assessment = self._make_assessment(report, [])
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)
        # Only the (unambiguous) Motor Centre parts PO was created.
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.first().supplier, self.motor)
        joined = ' '.join(e['error'] for e in response.data['errors'])
        self.assertIn('Rolling Wheels', joined)
        self.assertTrue(any(e.get('kind') == 'excess'
                            for e in response.data['errors']))

    def test_ambiguous_label_with_vendor_id_creates_po_for_that_contact(self):
        # An explicit vendor_id picked on the review screen bypasses the
        # ambiguous name matching — the PO lands on EXACTLY that contact.
        report = _b915bel_report()
        vid = str(self.rolling_b.pk)
        assessment = self._make_assessment(report, [
            {'id': 'parts:ROLLING WHEELS', 'vendor': 'ROLLING WHEELS', 'vendor_id': vid},
            {'id': 'labour',   'vendor': 'Rolling Wheels', 'vendor_id': vid},
            {'id': 'markup',   'vendor': 'Rolling Wheels', 'vendor_id': vid},
            {'id': 'paint',    'vendor': 'Rolling Wheels', 'vendor_id': vid},
            {'id': 'sundries', 'vendor': 'Rolling Wheels', 'vendor_id': vid},
            # empty vendor_id -> falls back to name matching (still works)
            {'id': 'parts:MOTOR CENTRE', 'vendor': 'MOTOR CENTRE', 'vendor_id': ''},
        ])
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])
        # The repairer PO is on rolling_b — the picked record, not its twin.
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.rolling_b).count(), 1)
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.rolling_a).count(), 0)

    # ------------------------------------------------------------------
    # (b) B915BEL end-to-end: two POs, excess on the repairer, no dupes
    # ------------------------------------------------------------------

    def test_b915bel_vendor_ids_two_pos_excess_on_repairer(self):
        report = _b915bel_report()
        rep_id = str(self.rolling_a.pk)
        assessment = self._make_assessment(report, [
            {'id': 'parts:ROLLING WHEELS', 'vendor': 'ROLLING WHEELS', 'vendor_id': rep_id},
            {'id': 'labour',   'vendor': 'Rolling Wheels', 'vendor_id': rep_id},
            {'id': 'markup',   'vendor': 'Rolling Wheels', 'vendor_id': rep_id},
            {'id': 'paint',    'vendor': 'Rolling Wheels', 'vendor_id': rep_id},
            {'id': 'sundries', 'vendor': 'Rolling Wheels', 'vendor_id': rep_id},
            {'id': 'parts:MOTOR CENTRE', 'vendor': 'MOTOR CENTRE',
             'vendor_id': str(self.motor.pk)},
        ])
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['errors'], [])

        # EXACTLY two POs — the repairer rows merged into ONE bucket
        # (own parts + labour + markup + paint + sundries), nothing duplicated.
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        repairer_po = PurchaseOrder.objects.get(supplier=self.rolling_a)
        parts_po    = PurchaseOrder.objects.get(supplier=self.motor)

        # Repairer PO: 6 own parts + labour + markup + paint + sundries
        # + the negative excess line = 11 lines.
        self.assertEqual(repairer_po.lines.count(), 11)
        excess_lines = [ln for ln in repairer_po.lines.all() if ln.unit_price < 0]
        self.assertEqual(len(excess_lines), 1)
        self.assertEqual(excess_lines[0].unit_price, Decimal('-3500.00'))
        self.assertIsNone(excess_lines[0].tax_code)     # NO VAT on the excess
        # Kao's expected money: taxable 34,224.65 − excess 3,500 = 30,724.65;
        # 14% per-line VAT on the repair work only.
        self.assertEqual(repairer_po.subtotal,     Decimal('30724.65'))
        self.assertEqual(repairer_po.tax_total,    Decimal('4791.45'))
        self.assertEqual(repairer_po.total_amount, Decimal('35516.10'))

        # Parts PO: the two Motor Centre mirrors at BASE (1,341.90 each),
        # no markup folded in, no excess.
        self.assertEqual(parts_po.lines.count(), 2)
        for ln in parts_po.lines.all():
            self.assertEqual(ln.unit_price, Decimal('1341.90'))
            self.assertEqual(ln.tax_code_id, self.vat.pk)
        self.assertEqual(parts_po.subtotal,     Decimal('2683.80'))
        self.assertEqual(parts_po.tax_total,    Decimal('375.74'))
        self.assertEqual(parts_po.total_amount, Decimal('3059.54'))

        # Outcome recorded; excess attached to the repairer PO only.
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, ClaimsAssessment.Status.POS_CREATED)
        by_kind = {r['kind']: r for r in assessment.po_results}
        self.assertTrue(by_kind['repairer']['excess_attached'])
        self.assertFalse(by_kind['parts']['excess_attached'])

    # ------------------------------------------------------------------
    # /plan/ ships the vendor list + per-row auto-match for the picker
    # ------------------------------------------------------------------

    def test_plan_returns_vendor_contacts_and_auto_match(self):
        report = _b915bel_report()
        assessment = self._make_assessment(report, [])
        response = self._get_plan(assessment)
        self.assertEqual(response.status_code, 200, response.data)

        # Full in-scope vendor list, {id, name}, sorted by name.
        contacts = response.data['vendor_contacts']
        names = [c['name'] for c in contacts]
        self.assertEqual(names, sorted(names, key=str.lower))
        self.assertIn('Motor Centre', names)
        self.assertIn('Rolling Wheels Gaborone', names)
        self.assertIn('Rolling Wheels Francistown', names)
        for c in contacts:
            self.assertIsInstance(c['id'], str)

        rows = {r['id']: r for r in response.data['plan']['rows']}
        # 'MOTOR CENTRE' name-matches exactly one contact -> auto-matched.
        self.assertEqual(rows['parts:MOTOR CENTRE']['auto_status'], 'matched')
        self.assertEqual(rows['parts:MOTOR CENTRE']['auto_vendor_id'],
                         str(self.motor.pk))
        # 'Rolling Wheels' hits TWO records -> ambiguous, nothing pre-picked.
        for row_id in ('labour', 'paint', 'sundries', 'parts:ROLLING WHEELS'):
            self.assertEqual(rows[row_id]['auto_status'], 'ambiguous', row_id)
            self.assertIsNone(rows[row_id]['auto_vendor_id'], row_id)

    def test_bad_vendor_id_falls_back_to_name_matching(self):
        # A stale/malformed vendor_id must not 500 — it falls back to the
        # label, and an unambiguous label still resolves.
        report = _b915bel_report()
        assessment = self._make_assessment(report, [
            {'id': 'parts:MOTOR CENTRE', 'vendor': 'MOTOR CENTRE',
             'vendor_id': 'not-a-real-pk'},
        ])
        response = self._create_pos(assessment)
        self.assertEqual(response.status_code, 200, response.data)
        # Motor Centre PO still created via the label; ambiguous repairer
        # rows (no vendor_id) stay flagged.
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.motor).count(), 1)

    # ------------------------------------------------------------------
    # (c) duplicate-vendor collapse — same-name records are NOT ambiguous
    # ------------------------------------------------------------------

    def test_same_name_duplicates_collapse_to_matched(self):
        # 'Carfil Panel Beaters' vs 'Carfil Panel Beaters (Pty) Ltd'
        # normalise identically — duplicates of ONE vendor, so the label
        # auto-matches to a single canonical contact instead of being
        # flagged ambiguous (duplicate-vendor collapse, 2026-07-08).
        from procurement.claims_api import (_match_vendor_label,
                                            _vendor_contact_pool)
        dup_co = Company.objects.create(code='TESTD', name='Dup Test Co.')
        dup_a = Contact.objects.create(
            name='Carfil Panel Beaters', contact_type='vendor',
            company=dup_co)
        dup_b = Contact.objects.create(
            name='Carfil Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=dup_co)
        contact, match_status = _match_vendor_label(
            'Carfil Panel Beaters', _vendor_contact_pool(dup_co))
        self.assertEqual(match_status, 'matched')
        self.assertIn(contact, (dup_a, dup_b))


class ClaimsUploadTest(TestCase):
    """POST /api/v1/claims-po/ — upload (entity-guarded, async 202) + the
    background parse pipeline's failure modes (run synchronously here)."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TESTU', name='Upload Test Co.')
        cls.user = User.objects.create_user(
            'claimsupload', password='x', is_superuser=True,
        )
        cls.factory = APIRequestFactory()

    def _upload(self, with_company=True):
        from django.core.files.uploadedfile import SimpleUploadedFile
        view = ClaimsAssessmentViewSet.as_view({'post': 'create'})
        data = {'file': SimpleUploadedFile('a.pdf', b'%PDF-1.4 not-really',
                                           content_type='application/pdf')}
        if with_company:
            data['company'] = str(self.company.pk)
        request = self.factory.post('/api/v1/claims-po/', data,
                                    format='multipart')
        force_authenticate(request, user=self.user)
        return view(request)

    def _run_pipeline(self):
        """Create an assessment row and run the (normally background)
        pipeline synchronously — the upload view is async now (202 + thread),
        so parse outcomes are asserted on the pipeline itself, race-free.

        close_old_connections() is stubbed out: the pipeline calls it for
        real thread hygiene, but under TestCase it closes the test runner's
        transaction-wrapped connection (CONN_MAX_AGE-expired), which poisons
        every later test in the class with 'connection already closed'."""
        from unittest import mock
        from django.core.files.uploadedfile import SimpleUploadedFile
        from procurement.claims_api import _run_claims_pipeline
        assessment = ClaimsAssessment.objects.create(
            company=self.company, created_by=self.user,
            status=ClaimsAssessment.Status.PARSING,
            assessment_file=SimpleUploadedFile(
                'a.pdf', b'%PDF-1.4 not-really',
                content_type='application/pdf'),
        )
        with mock.patch('django.db.close_old_connections', lambda: None):
            _run_claims_pipeline(assessment.pk, self.user.pk)
        assessment.refresh_from_db()
        return assessment

    def test_upload_without_entity_is_rejected(self):
        # Fable audit 2026-07-08: a NULL-company assessment 404s the poll and
        # its POs are invisible to scoped approvers — the upload must insist
        # on the topbar entity.
        response = self._upload(with_company=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn('company/entity', str(response.data['detail']))
        self.assertEqual(ClaimsAssessment.objects.count(), 0)

    def test_upload_with_entity_is_accepted_async(self):
        # Upload replies immediately (202 + progress fields); the parse runs
        # in a background thread the review screen polls.
        response = self._upload()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertTrue(response.data['id'])
        self.assertEqual(response.data['progress_stage'],
                         ClaimsAssessment.Stage.UPLOADED)
        assessment = ClaimsAssessment.objects.get(pk=response.data['id'])
        self.assertEqual(assessment.company_id, self.company.pk)

    def test_report_with_no_groups_is_parse_failed_not_ready(self):
        # An empty/garbage PDF that "parses" to zero lines must land in
        # PARSE_FAILED — not a review screen with nothing to allocate.
        from unittest import mock
        with mock.patch('procurement.claims_parser.parse',
                        return_value={'groups': [], 'summary': {},
                                      'raw_text': ''}):
            assessment = self._run_pipeline()
        self.assertEqual(assessment.status,
                         ClaimsAssessment.Status.PARSE_FAILED)
        self.assertTrue(assessment.parse_error)
        self.assertEqual(assessment.progress_stage,
                         ClaimsAssessment.Stage.FAILED)

    def test_parser_exception_is_parse_failed(self):
        from unittest import mock
        with mock.patch('procurement.claims_parser.parse',
                        side_effect=ValueError('broken xref table')):
            assessment = self._run_pipeline()
        self.assertEqual(assessment.status,
                         ClaimsAssessment.Status.PARSE_FAILED)
        self.assertIn('broken xref table', assessment.parse_error)

    def test_overlong_parsed_meta_is_truncated_not_fatal(self):
        # A 200-char parsed policy number must not blow up the post-parse
        # save (CharField max_length=64) — it is truncated instead.
        from procurement.claims_api import _autofill_meta
        assessment = ClaimsAssessment()
        _autofill_meta(assessment, {
            'policy_no': 'P' * 200,
            'client_name': 'C' * 300,
        })
        self.assertEqual(len(assessment.policy_number), 64)
        self.assertEqual(len(assessment.client_name), 255)
