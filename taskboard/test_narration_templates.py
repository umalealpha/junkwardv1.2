"""taskboard/test_narration_templates.py — Finance's spec, pinned example by example.

Their reply of 2026-08-20 gave four worked examples. Each one is asserted here
verbatim, so a later "tidy up" of the templates fails loudly instead of quietly
changing what a supplier or a client sees on their statement.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open

from taskboard.narration_templates import (
    OUR_REF_MAX, PaymentNarrationType as T, build_defaults, default_type_for,
    initials_of,
)


def _b(**kw):
    return build_defaults(**kw)


class TheirWorkedExamplesTests(SimpleTestCase):
    """The four examples Finance wrote, character for character."""

    def test_claims_example_G2026004512_AOL(self):
        out = _b(payment_type=T.AOL,
                 line_items=[{'claim_number': 'G2026004512'}],
                 payee='Some Insured Person')
        self.assertEqual(out['our_reference'], 'G2026004512 AOL')
        self.assertEqual(out['narration'], 'ALPHA DIRECT G2026004512 AOL')

    def test_client_refund_example_REFUND_COMG2026004512_LN(self):
        out = _b(payment_type=T.CLIENT_REFUND,
                 policy_number='COMG2026004512',
                 account_name='Lorato Ntsima')
        self.assertEqual(out['our_reference'], 'REFUND COMG2026004512 LN')
        self.assertEqual(out['narration'], 'ALPHA DIRECT REFUND COMG2026004512')

    def test_internal_refund_example_REFUND_Starlink_PB(self):
        out = _b(payment_type=T.INTERNAL_REFUND,
                 refund_type='Starlink', account_name='Prathap Bhai')
        self.assertEqual(out['our_reference'], 'REFUND Starlink PB')
        self.assertEqual(out['narration'], 'ALPHA DIRECT REFUND PB')

    def test_supplier_example_MOTOVAC_INV45678(self):
        out = _b(payment_type=T.SUPPLIER_INVOICE,
                 line_items=[{'invoice_number': 'INV45678'}],
                 account_name='Motovac')
        self.assertEqual(out['our_reference'], 'MOTOVAC INV45678')
        self.assertEqual(out['narration'], 'ALPHA DIRECT INV45678')


class WhatThePayeeSeesTests(SimpleTestCase):
    """Rule 1 — the 140-character narration, per type."""

    def test_repair_carries_claim_and_invoice(self):
        out = _b(payment_type=T.REPAIR,
                 line_items=[{'claim_number': 'G2026004801',
                              'invoice_number': 'KA-40118'}])
        self.assertEqual(out['narration'], 'ALPHA DIRECT G2026004801 KA-40118')
        self.assertEqual(out['our_reference'], 'G2026004801 REPAIR')

    def test_cil_and_third_party_carry_the_type(self):
        cil = _b(payment_type=T.CIL, line_items=[{'claim_number': 'G1'}])
        self.assertEqual(cil['narration'], 'ALPHA DIRECT G1 CIL')
        tp = _b(payment_type=T.THIRD_PARTY, line_items=[{'claim_number': 'G2'}])
        self.assertEqual(tp['narration'], 'ALPHA DIRECT G2 THIRD PARTY')

    def test_ex_gratia_is_its_own_type(self):
        out = _b(payment_type=T.EX_GRATIA, line_items=[{'claim_number': 'G3'}])
        self.assertEqual(out['narration'], 'ALPHA DIRECT G3 EX-GRATIA')
        self.assertEqual(out['our_reference'], 'G3 EX-GRATIA')

    def test_several_invoices_all_appear_for_the_payee(self):
        out = _b(payment_type=T.SUPPLIER_INVOICE,
                 line_items=[{'invoice_number': 'INV1'},
                             {'invoice_number': 'INV2'}],
                 account_name='Motovac')
        self.assertEqual(out['narration'], 'ALPHA DIRECT INV1 INV2')
        # our reference keeps the FIRST invoice — 35 characters is not a list
        self.assertEqual(out['our_reference'], 'MOTOVAC INV1')


class TheThreeConstraintsTests(SimpleTestCase):
    """The rules Finance was explicit about, each one load-bearing."""

    def test_the_insureds_name_is_left_out_of_a_claim(self):
        """"The claim number already resolves to the insured in Omni, so the
        name adds nothing when matching. Leave it out." """
        out = _b(payment_type=T.AOL,
                 line_items=[{'claim_number': 'G2026004512'}],
                 payee='Kefilwe Mogomotsi', account_name='Kefilwe Mogomotsi')
        self.assertNotIn('KEFILWE', out['our_reference'].upper())
        self.assertNotIn('KEFILWE', out['narration'].upper())

    def test_the_supplier_name_truncates_and_the_invoice_survives(self):
        """"Truncate the name, never the number." """
        out = _b(payment_type=T.SUPPLIER_INVOICE,
                 line_items=[{'invoice_number': 'INV-2026-0000451'}],
                 account_name='Kalahari Automotive And Panel Services Botswana')
        self.assertLessEqual(len(out['our_reference']), OUR_REF_MAX)
        self.assertTrue(out['our_reference'].endswith('INV-2026-0000451'),
                        msg=out['our_reference'])

    def test_a_very_long_invoice_number_still_survives_whole(self):
        inv = 'INV-' + '9' * 30
        out = _b(payment_type=T.SUPPLIER_INVOICE,
                 line_items=[{'invoice_number': inv}],
                 account_name='Motovac')
        self.assertLessEqual(len(out['our_reference']), OUR_REF_MAX)
        self.assertIn('9999', out['our_reference'])

    def test_the_supplier_name_is_capped_at_twenty(self):
        out = _b(payment_type=T.SUPPLIER_INVOICE,
                 line_items=[{'invoice_number': 'I1'}],
                 account_name='A' * 40)
        name_part = out['our_reference'].split(' ')[0]
        self.assertEqual(len(name_part), 20)

    def test_no_omni_payment_number_anywhere(self):
        """"The Omni payment number should come off the bank narration." """
        for t in (T.SUPPLIER_INVOICE, T.REPAIR, T.AOL, T.CIL, T.THIRD_PARTY,
                  T.EX_GRATIA, T.CLIENT_REFUND, T.INTERNAL_REFUND):
            out = _b(payment_type=t,
                     line_items=[{'claim_number': 'G1', 'invoice_number': 'I1'}],
                     account_name='Someone', policy_number='POL1',
                     refund_type='Starlink')
            for field in ('narration', 'our_reference'):
                self.assertNotIn('PAY-OUT', out[field].upper(), msg=f'{t}/{field}')
                self.assertNotIn('PAY/', out[field].upper(), msg=f'{t}/{field}')


class LimitsTests(SimpleTestCase):
    def test_nothing_ever_exceeds_the_bank_limits(self):
        long_items = [{'claim_number': 'G' + '9' * 40,
                       'invoice_number': 'I' + '8' * 40}] * 6
        for t in (T.SUPPLIER_INVOICE, T.REPAIR, T.AOL, T.CLIENT_REFUND,
                  T.INTERNAL_REFUND):
            out = _b(payment_type=t, line_items=long_items,
                     account_name='X' * 80, policy_number='P' * 60,
                     refund_type='R' * 60)
            self.assertLessEqual(len(out['our_reference']), 35, msg=t)
            self.assertLessEqual(len(out['narration']), 140, msg=t)


class ManualTypesTests(SimpleTestCase):
    """The gap Finance named: "reinsurance, payroll and statutory payments
    (BURS, NBFIRA) are not covered above… which is why narration edits should be
    allowed and manually entered." """

    def test_other_returns_blank_so_the_person_types_it(self):
        out = _b(payment_type=T.OTHER, line_items=[{'invoice_number': 'I1'}],
                 account_name='BURS')
        self.assertEqual(out['narration'], '')
        self.assertEqual(out['our_reference'], '')
        self.assertEqual(out['missing'], [])

    def test_an_unknown_type_behaves_like_other_rather_than_guessing(self):
        out = _b(payment_type='reinsurance-treaty', account_name='Hannover Re')
        self.assertEqual(out['narration'], '')
        self.assertEqual(out['our_reference'], '')


class MissingPiecesTests(SimpleTestCase):
    """A template that cannot fill itself must say so, not emit half a
    reference that looks finished."""

    def test_a_supplier_payment_with_no_invoice_number_says_so(self):
        out = _b(payment_type=T.SUPPLIER_INVOICE, account_name='Motovac')
        self.assertIn('invoice number', out['missing'])

    def test_a_claim_with_no_claim_number_says_so(self):
        out = _b(payment_type=T.AOL, line_items=[])
        self.assertIn('claim number', out['missing'])

    def test_a_client_refund_with_no_policy_says_so(self):
        out = _b(payment_type=T.CLIENT_REFUND, account_name='Lorato Ntsima')
        self.assertIn('policy number', out['missing'])

    def test_a_complete_request_reports_nothing_missing(self):
        out = _b(payment_type=T.REPAIR,
                 line_items=[{'claim_number': 'G1', 'invoice_number': 'I1'}])
        self.assertEqual(out['missing'], [])


class SmallHelpersTests(SimpleTestCase):
    def test_initials(self):
        self.assertEqual(initials_of('Lorato Ntsima'), 'LN')
        self.assertEqual(initials_of('prathap bhai'), 'PB')
        self.assertEqual(initials_of('Mary-Jane  O Brien'), 'MJO')
        self.assertEqual(initials_of(''), '')

    def test_both_spellings_of_the_line_keys_are_read(self):
        a = _b(payment_type=T.AOL, line_items=[{'claim_number': 'G1'}])
        b = _b(payment_type=T.AOL, line_items=[{'claim_no': 'G1'}])
        self.assertEqual(a['our_reference'], b['our_reference'])

    def test_the_starting_guess_from_a_category(self):
        self.assertEqual(default_type_for('supplier'), T.SUPPLIER_INVOICE)
        self.assertEqual(default_type_for('premium_refund'), T.CLIENT_REFUND)
        self.assertEqual(default_type_for('claim', 'provider'), T.REPAIR)
        # A client claim could be AOL, CIL, third party or ex-gratia — the
        # category cannot tell, so it must not pretend to.
        self.assertEqual(default_type_for('claim', 'client'), T.OTHER)
        self.assertEqual(default_type_for('petty_cash'), T.OTHER)


class TheApproverSeesTheWordingOnTheScreenTests(SimpleTestCase):
    """Finance, 2026-08-20: "the final narration must display on the approval
    screen … The approver then releases exactly what the bank will receive."

    The drawer with the Approve button renders `formatted_html`. A block that
    lives only in the emailed plaintext is a promise shown on one screen and not
    the other — which is how someone approves wording they never saw.
    """

    def _pack(self, **over):
        from taskboard.payment_views import _render_html
        pr = dict(
            entity='Alpha Direct Insurance Company (Pty) Ltd', ref='PR-HTML-1',
            category='supplier', claim_payee_type='', currency='BWP',
            subject='Panel repair', payee='A Supplier',
            line_items=[{'description': 'Panel repair',
                         'amount': Decimal('250.00'),
                         'gl_code': '', 'ref': ''}],
            total=Decimal('250.00'), account_name='A Supplier (Pty) Ltd',
            account_number='1234567', bank_name='FNB Botswana',
            branch_code='293567', opening_balance=None, due_date='',
            payment_date=None, inputter='Someone', verifier='',
            bank_payment_type='supplier_invoice',
            bank_narration='ALPHA DIRECT INV45678',
            bank_our_reference='MOTOVAC INV45678',
        )
        pr.update(over)
        return _render_html(pr)

    def test_the_approval_screen_shows_what_the_bank_will_be_told(self):
        html = self._pack()
        self.assertIn('WHAT THE BANK WILL BE TOLD', html)
        self.assertIn('ALPHA DIRECT INV45678', html)
        self.assertIn('MOTOVAC INV45678', html)

    def test_no_wording_no_block(self):
        """An empty block would just be furniture on the pack."""
        html = self._pack(bank_narration='', bank_our_reference='',
                          bank_payment_type='')
        self.assertNotIn('WHAT THE BANK WILL BE TOLD', html)

    def test_the_wording_is_escaped_not_injected(self):
        html = self._pack(bank_narration='<script>alert(1)</script>')
        self.assertNotIn('<script>', html)


@window_always_open
class AHalfBuiltReferenceIsNotStoredTests(APITestCase):
    """`build_defaults` reports what the template wanted and did not get. If
    that is ignored, an AOL claim with no claim number on its lines stores an
    our-reference of just 'AOL' — identical on every AOL payment and matching
    nothing on a statement. Better to store nothing and let the payment number
    floor apply, which the pack states plainly.
    """

    def setUp(self):
        self.clerk = User.objects.create_user('lthebe2', email='lthebe2@alphadirect.co.bw')
        User.objects.create_user('pkago2', email='pkago@alphadirect.co.bw')
        self.url = reverse('v1-payment-requests')

    def _create(self, **over):
        body = {
            'subject': 'Claim settlement',
            'category': PaymentRequest.Category.OTHER,
            'bank_payment_type': T.AOL,
            'line_items': [{'description': 'AOL settlement', 'amount': '1000.00'}],
            'account_name': 'A Payee (Pty) Ltd',
            'account_number': '1234567', 'new_payee_confirmed': True,
            'bank_name': 'FNB Botswana',
            'branch_code': '293567',
        }
        body.update(over)
        self.client.force_authenticate(self.clerk)
        return self.client.post(self.url, body, format='json')

    def test_a_claim_type_with_no_claim_number_stores_no_reference(self):
        # Reachable in one click: someone picks AOL on an operational request.
        # The view wipes claim numbers off non-claim categories, so the template
        # has nothing to work with and 'AOL' alone is all it could emit.
        r = self._create()
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.bank_our_reference, '')
        self.assertEqual(pr.bank_narration, '')

    def test_a_complete_template_is_still_stored(self):
        """Finance's internal-refund format: "REFUND + REFUND TYPE + INITIALS".

        Not a claim type, because the create view wipes claim numbers off every
        category except 'claim' — so a claim wording chosen on an operational
        request can never complete, which is the case above.
        """
        r = self._create(bank_payment_type=T.INTERNAL_REFUND,
                         refund_type='Starlink',
                         account_name='Prathap Ganesharajah')
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.bank_our_reference, 'REFUND Starlink PG')
        self.assertEqual(pr.bank_narration, 'ALPHA DIRECT REFUND PG')

    def test_what_a_person_typed_is_never_dropped(self):
        """Only the TEMPLATE's guess is discarded. A human's words stand."""
        r = self._create(bank_our_reference='PAID PER CFO INSTRUCTION',
                         bank_narration='ALPHA DIRECT SETTLEMENT')
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.bank_our_reference, 'PAID PER CFO INSTRUCTION')
        self.assertEqual(pr.bank_narration, 'ALPHA DIRECT SETTLEMENT')


class TheDashOnThePackIsADashTests(SimpleTestCase):
    """Seen on the live approval pack, 2026-08-21: "This request becomes payable
    on &mdash;". The empty-value fallback was being HTML-escaped, so the code for
    a dash was printed instead of a dash. Escape the value, then fall back.
    """

    def _terms_html(self, **over):
        from taskboard.payment_views import _render_terms_block_html
        pr = dict(payment_date=None, line_items=[{'description': 'x',
                                                  'amount': Decimal('1.00')}],
                  early_payment_reason='', funds_already_moved=False)
        pr.update(over)
        return _render_terms_block_html(pr)

    def test_a_missing_due_date_prints_a_dash_not_its_html_code(self):
        html = self._terms_html()
        self.assertNotIn('&amp;mdash;', html)
        self.assertIn('&mdash;', html)
