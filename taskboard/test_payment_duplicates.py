"""taskboard/test_payment_duplicates.py — duplicate payment control PAY-DUP-01.

CFO 2026-08-03: the authorisation queue held ten requests worth BWP 789,626.85
and BWP 219,600.10 of it was already paid or double-counted. These tests are
built from the ACTUAL duplicates found in that queue, so a regression reproduces
the real failure rather than a synthetic one:

  * PAY/ADIC/2026/07/29/0002 was a strict subset of 0003 (Carfil, five lines).
  * G2026004535 1,535.98 was paid on 07/28/0002 and re-raised on 07/28/0001.
  * G2025003601 CHOPPIES 42,380.04 sat on 07/23/0001 and 07/28/0001 at once.
  * G2026005105 appeared TWICE on 07/28/0001 (repair 25,684.20 + ex-gratia
    34,220.00) — legitimately, because the amounts differ.

Run: manage.py test taskboard.test_payment_duplicates
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest
from taskboard.payment_duplicates import (
    blocking_message, compare_lines, find_duplicates, hard_total, line_tokens,
)
from taskboard.test_helpers import window_always_open


def _L(description, amount, **kw):
    return {'description': description, 'amount': amount, **kw}


# ── the real 3-Aug-2026 queue, verbatim ──────────────────────────────────────
# Read out of omni on 3 Aug 2026. Kept as real data rather than fixtures so a
# regression reproduces the actual loss, and so the two totals below (the money
# that would have gone out twice) are asserted and not just described.
Q_0003 = ('PAY/ADIC/2026/07/29/0003', 'pending_cfo', [
    _L('G2026004287 CARFIL SERVICES', '5307.32'),
    _L('G2026004472 CARFIL SERVICES', '14553.86'),
    _L('G2026004524 CARFIL SERVICES', '8599.94'),
    _L('G2026004527 CARFIL SERVICES', '9467.40'),
    _L('G2026004597 CARFIL SERVICES', '1201.82'),
    _L('G2026004631 CARFIL SERVICES', '12602.54'),
    _L('G2026004641 CARFIL SERVICES', '12761.32'),
    _L('G2026004645 CARFIL SERVICES', '30663.53'),
    _L('G2026004779 CARFIL SERVICES', '8253.24'),
    _L('G2026004550 BB MOTORS.', '3745.65'),
    _L('G2026004763 BB MOTORS', '6394.31'),
])
Q_0002_LINES = [
    _L('G2026004287 CARFIL SERVICES', '5307.32'),
    _L('G2026004472 CARFIL SERVICES', '14553.86'),
    _L('G2026004524 CARFIL SERVICES', '8599.94'),
    _L('G2026004527 CARFIL SERVICES', '9467.40'),
    _L('G2026004641 CARFIL SERVICES', '12761.32'),
]
Q_0727 = ('PAY/ADIC/2026/07/27/0001', 'paid', [
    _L('G2026004546 CHRISTOPHER MOHWAS', '9000.00'),
    _L('G2026004605 COMMERCIAL AUTO G', '2576.00'),
    _L('G2026004619 COMMERCIAL AUTO GL', '2576.00'),
    _L('G2026004977 KAONE ESAU ARNOLD', '15000.00'),
    _L('G2026005105 KOMAROV EX-GRATIA', '34220.00'),
    _L('G2026005105 KOMAROV INVESTMEN', '25684.20'),
])
Q_0728_0002 = ('PAY/ADIC/2026/07/28/0002', 'paid',
               [_L('G2026004535 COMMERCIAL AUTO GL', '1535.98')])
Q_0803 = ('PAY/ADIC/2026/08/03/0001', 'paid',
          [_L('G2026004524 CARFIL SERVICE', '8599.94', invoice_number='9553')])
Q_0723 = ('PAY/ADIC/2026/07/23/0001', 'pending_cfo', [
    _L('G2026004620 RADICAL INVESTMENT', '48223.90'),
    _L('G2025003601 CHOPPIES DISTRIBUT', '42380.04'),
])
Q_0728_0001_LINES = [
    _L('20240956 ACHIEVABLE ENTERPRISE', '11215.00'),
    _L('G2026004535 COMMERCIAL AUTO GL', '1535.98'),
    _L('G2026004546 CHRISTOPHER MOHWAS', '9000.00'),
    _L('G2026004977 KAONE ESAU ARNOLD', '15000.00'),
    _L('G2026005105 KOMAROV INVESTMEN', '25684.20'),
    _L('G2025003601 CHOPPIES DISTRIBUT', '42380.04'),
    _L('G2026005105 KOMAROV EX-GRATIA', '34220.00'),
    _L('G2026004368 ARCON CRAFTS', '13662.67'),
]

# The other eight requests in the queue. None is a duplicate; every one of them
# must still be payable, or the control is a denial-of-service on Finance.
Q_CLEAN = {
    'ORANGE JULY 26': [_L('ORANGE JULY 26', '12730.23', ref='ORANGE JULY 26')],
    'SPRINT COURIERS': [_L('SPRINT COURIERS-91007-JUNE 26', '2682.05')],
    'PULA Medical Aid': [_L('PULA MED - JULY', '30247.25')],
    'BOMAID': [_L('BOMAID-JULY 26', '19475.00')],
    'E.G COURIERS': [
        _L('E.G COURIERS-IN102970-JUNE 26', '2277.02'),
        _L('E.G COURIERS-IN102971-JUNE 26', '1280.60'),
        _L('E.G COURIERS-IN102972-JUNE 26', '981.27'),
        _L('E.G COURIERS-IN102973-JUNE 26', '1955.96'),
        _L('E.G COURIERS-IN102974-JUNE 26', '6373.70'),
    ],
    'FAC Premium Cessions': [
        _L('P&C RE-JUN-JUL 2026', '43205.62'),
        _L('FMRE-MP MINING MAY', '43805.74'),
        _L('FMRE-MP MINING JUNE', '43805.74'),
        _L('FMRE- CHOPPES Q2', '19949.32'),
        _L('CO NTINENTAL RE-PST APR-MAY', '25573.00'),
        _L('CO NTINENTAL RE-PST JUNE-JUL', '25573.00'),
    ],
}


class RealQueueTests(SimpleTestCase):
    """The matching rules, with no database — compare_lines is pure on purpose.

    These are the cases that cost real money on 3 Aug 2026. They must hold even
    when a test database is unavailable, which is exactly when a rule like this
    otherwise goes unchecked.
    """

    REGISTER = [Q_0003, Q_0727, Q_0728_0002, Q_0803, Q_0723]

    def test_0002_is_a_strict_subset_of_0003(self):
        """PAY/ADIC/2026/07/29/0002, BWP 50,689.84, was lines 1-4 and 7 of 0003."""
        r = compare_lines(Q_0002_LINES, [Q_0003], currency='BWP')
        self.assertEqual(len(r['hard']), 5)
        self.assertEqual(hard_total(r['hard']), Decimal('50689.84'))

    def test_repair_batch_already_paid_lines(self):
        """Five lines of 07/28/0001 were settled on 07/27/0001 and 07/28/0002,
        and CHOPPIES was also on 07/23/0001 — BWP 127,820.22 in total."""
        r = compare_lines(Q_0728_0001_LINES,
                          [Q_0727, Q_0728_0002, Q_0723], currency='BWP')
        flagged = {h['label'].split()[0] for h in r['hard']}
        for claim in ('G2026004535', 'G2026004546', 'G2026004977',
                      'G2026005105', 'G2025003601'):
            self.assertIn(claim, flagged)
        self.assertEqual(hard_total(r['hard']), Decimal('127820.22'))

    def test_line_paid_the_same_morning(self):
        """G2026004524 8,599.94 was paid on 08/03/0001 and still sat on two
        pending requests."""
        r = compare_lines([_L('G2026004524 CARFIL SERVICES', '8599.94')],
                          [Q_0803], currency='BWP')
        self.assertEqual(len(r['hard']), 1)
        self.assertEqual(r['hard'][0]['clash_status'], 'paid')

    def test_every_clean_request_in_the_queue_still_passes(self):
        for name, lines in Q_CLEAN.items():
            with self.subTest(request=name):
                r = compare_lines(lines, self.REGISTER, currency='BWP')
                self.assertEqual(
                    r['hard'], [],
                    f'{name} wrongly blocked: '
                    + '; '.join(h['detail'] for h in r['hard'][:3]))

    def test_identical_fac_amounts_are_not_a_duplicate(self):
        """FMRE-MP MINING MAY and JUNE are both 43,805.74; Continental RE APR-MAY
        and JUNE-JUL are both 25,573.00. Different periods, real payments."""
        r = compare_lines(Q_CLEAN['FAC Premium Cessions'], [], currency='BWP')
        self.assertEqual(r['hard'], [])

    def test_message_is_actionable(self):
        r = compare_lines(Q_0002_LINES, [Q_0003], currency='BWP')
        msg = blocking_message(r['hard'], currency='BWP',
                              total_hard=hard_total(r['hard']))
        self.assertIn('Line 1', msg)
        self.assertIn('PAY/ADIC/2026/07/29/0003', msg)
        self.assertIn('50,689.84', msg)
        # The way out must be stated, and must match what the form actually
        # shows — a message telling the raiser to tick a box that does not
        # exist is worse than no instruction.
        self.assertIn('write why in the box below', msg)


class SameClaimMultipleInvoicesTests(SimpleTestCase):
    """One claim, several invoices (Pako Kago, 2026-09-02).

    A claim legitimately carries multiple invoices — G2026004567 (OMEGA
    AUTOWORLD) had four, referenced G2026004567-4546, -4550, … — and equal
    amounts across them are normal on a repair split. The control was blocking
    the second such invoice because both lines share the bare claim token.

    Tests 1-5 are red-first: they FAIL before the fix, pass after. Tests 6-9 pin
    the boundary — they pass before AND after, so the fix cannot be loosened into
    a double-payment hole.
    """

    # ── red-first: legitimate different invoices of one claim ────────────────
    def test_two_invoices_of_one_claim_in_one_pack_are_not_blocked(self):
        """Pako's exact shape: same claim, same amount, different -suffix."""
        lines = [_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546'),
                 _L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4550')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(r['hard'], [])
        self.assertEqual(len(r['soft']), 1)                 # never silent
        self.assertEqual(r['soft'][0]['clash_kind'], 'same_claim_other_invoice')

    def test_four_equal_invoices_of_one_claim_in_one_pack(self):
        """'1 claim can have 4 invoices' — verbatim from the report."""
        lines = [_L('OMEGA AUTOWORLD', '10000.00', ref=f'G2026004567-{s}')
                 for s in ('4546', '4550', '4551', '4552')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(r['hard'], [])

    def test_next_invoice_of_a_claim_already_paid_is_not_blocked(self):
        """Section 2: an earlier invoice of the claim is already paid; a NEW,
        different invoice at the same amount must not be blocked."""
        register = [('PAY/ADIC/2026/08/28/0001', 'paid',
                     [_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546')])]
        r = compare_lines([_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4550')],
                          register, currency='BWP')
        self.assertEqual(r['hard'], [])
        self.assertEqual(r['soft'][0]['clash_ref'], 'PAY/ADIC/2026/08/28/0001')

    def test_invoice_number_field_also_discriminates(self):
        """The distinguishing invoice id may live in invoice_number, not a
        suffix on the ref."""
        lines = [_L('OMEGA AUTOWORLD G2026004567', '10000.00', invoice_number='4546'),
                 _L('OMEGA AUTOWORLD G2026004567', '10000.00', invoice_number='4550')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(r['hard'], [])
        self.assertEqual(len(r['soft']), 1)

    def test_suffix_in_the_description_alone_is_recognised(self):
        """The real packs put the reference in the description (Edit 2)."""
        lines = [_L('G2026004567-4546 OMEGA AUTOWORLD', '10000.00'),
                 _L('G2026004567-4550 OMEGA AUTOWORLD', '10000.00')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(r['hard'], [])

    # ── boundary guards: these must stay HARD (pass before AND after) ────────
    def test_the_same_invoice_twice_is_still_hard(self):
        """Same claim, same amount, SAME invoice reference — a real duplicate."""
        lines = [_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546'),
                 _L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    def test_a_suffix_on_one_side_only_is_still_hard(self):
        """One line names its invoice, the other is bare — cannot prove they
        differ, so it must still block (the G2026004524 / invoice 9553 shape)."""
        register = [('PAY/ADIC/2026/08/28/0001', 'paid',
                     [_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546')])]
        r = compare_lines([_L('OMEGA AUTOWORLD G2026004567', '10000.00')],
                          register, currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    def test_a_shared_non_claim_reference_is_still_hard(self):
        """Two lines sharing a real invoice/voucher number (not a claim) at the
        same amount — still a duplicate."""
        lines = [_L('AUTOSCREEN one', '10000.00', ref='20240956'),
                 _L('AUTOSCREEN two', '10000.00', ref='20240956')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    def test_bare_claim_plus_amount_twice_is_still_hard(self):
        """The MOHWAS 3-Aug shape: bare claim, same amount, no invoice id — the
        exact pattern this fix must NOT reopen."""
        r = compare_lines([_L('G2026004546 CHRISTOPHER MOHWAS', '9000.00')],
                          [Q_0727], currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    # ── one claim, several SUPPLIERS (Leano Makwapa, 2026-09-02) ─────────────
    def test_same_claim_different_supplier_is_not_blocked(self):
        """A claim pays a windscreen supplier and, separately, an engine
        supplier — same claim number, same amount, DIFFERENT payee, no invoice
        id. Red-first: hard before the fix, soft after. Money never leaves Omni,
        so the approver still eyeballs the soft note."""
        register = [('PAY/ADIC/2026/08/28/0007', 'paid',
                     [_L('G2026005213 windscreen', '4500.00')],
                     '2026-08', 'AUTOGLASS BOTSWANA')]
        r = compare_lines([_L('G2026005213 engine repair', '4500.00')],
                          register, currency='BWP',
                          new_payee='ENGINE WORKS (PTY) LTD')
        self.assertEqual(r['hard'], [])
        self.assertEqual(len(r['soft']), 1)
        self.assertEqual(r['soft'][0]['clash_kind'], 'same_claim_other_payee')

    def test_same_claim_SAME_supplier_bare_is_still_hard(self):
        """Boundary: same claim, same amount, SAME payee, no invoice id — the
        P399k / 3-Aug double-pay shape. Must stay hard even with the payee fix."""
        register = [('PAY/ADIC/2026/08/28/0007', 'paid',
                     [_L('G2026005213 windscreen', '4500.00')],
                     '2026-08', 'AUTOGLASS BOTSWANA')]
        r = compare_lines([_L('G2026005213 windscreen', '4500.00')],
                          register, currency='BWP',
                          new_payee='AUTOGLASS BOTSWANA')
        self.assertEqual(len(r['hard']), 1)

    # ── holes Fable proved (2026-09-02): payee text is NOT an invoice id, and
    #    the same invoice in two formats is still the same invoice ────────────
    def test_payee_text_in_the_ref_is_not_an_invoice_id(self):
        """The literal 3-Aug CARFIL typo pair: same claim, same amount, only the
        payee text differs by one letter in the ref. That is NOT two invoices —
        it must still block. (Payee text in a ref must never demote a clash.)"""
        lines = [_L('one', '8599.94', ref='G2026004524 CARFIL SERVICE'),
                 _L('two', '8599.94', ref='G2026004524 CARFIL SERVICES')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    def test_the_same_invoice_in_two_formats_is_still_hard(self):
        """One line carries the invoice as a ref suffix 'G2026004567-4546', the
        other as claim + invoice_number '4546' — the SAME invoice, written two
        ways. Must still block."""
        lines = [_L('OMEGA AUTOWORLD', '10000.00', ref='G2026004567-4546'),
                 _L('OMEGA AUTOWORLD G2026004567', '10000.00', invoice_number='4546')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(len(r['hard']), 1)

    def test_leading_zero_variants_are_the_same_invoice(self):
        """Invoice '4546' and '04546' are one invoice — must still block."""
        lines = [_L('OMEGA AUTOWORLD G2026004567', '10000.00', invoice_number='4546'),
                 _L('OMEGA AUTOWORLD G2026004567', '10000.00', invoice_number='04546')]
        r = compare_lines(lines, [], currency='BWP')
        self.assertEqual(len(r['hard']), 1)


class DuplicateMatchingTests(APITestCase):
    """The matcher itself — no HTTP, no auth."""

    def _pr(self, ref, status, lines, currency='BWP'):
        return PaymentRequest.objects.create(
            ref=ref, status=status, currency=currency, subject=ref,
            line_items=lines, total=sum(Decimal(l['amount']) for l in lines))

    # ── token harvesting ────────────────────────────────────────────────────
    def test_claim_number_found_in_description_only(self):
        """The real packs put the claim number in the description and leave `ref`
        wrong — on PAY/ADIC/2026/07/24/0001 every `ref` was the same string. A
        matcher reading `ref` alone sees nothing."""
        toks = line_tokens({'description': 'G2026004287 CARFIL SERVICES',
                            'ref': '', 'claim_number': ''})
        self.assertIn('G2026004287', toks)

    def test_punctuation_and_case_collapse(self):
        a = line_tokens({'description': 'g2026004287-carfil  services'})
        b = line_tokens({'description': 'G2026004287 CARFIL SERVICES'})
        self.assertTrue(a & b)

    def test_short_tokens_ignored(self):
        """A two-character reference would match everything."""
        self.assertEqual(line_tokens({'description': 'RE', 'ref': 'AB'}), set())

    # ── the real duplicates ─────────────────────────────────────────────────
    def test_subset_request_is_caught(self):
        """0002's five Carfil lines are all inside 0003. Raising 0002 after 0003
        must be refused — this is the BWP 50,689.84 duplicate."""
        self._pr('PAY/ADIC/2026/07/29/0003', 'pending_cfo', [
            {'description': 'G2026004287 CARFIL SERVICES', 'amount': '5307.32'},
            {'description': 'G2026004472 CARFIL SERVICES', 'amount': '14553.86'},
            {'description': 'G2026004524 CARFIL SERVICES', 'amount': '8599.94'},
            {'description': 'G2026004527 CARFIL SERVICES', 'amount': '9467.40'},
            {'description': 'G2026004641 CARFIL SERVICES', 'amount': '12761.32'},
        ])
        dup = find_duplicates([
            {'description': 'G2026004287 CARFIL SERVICES', 'amount': '5307.32'},
            {'description': 'G2026004472 CARFIL SERVICES', 'amount': '14553.86'},
            {'description': 'G2026004524 CARFIL SERVICES', 'amount': '8599.94'},
            {'description': 'G2026004527 CARFIL SERVICES', 'amount': '9467.40'},
            {'description': 'G2026004641 CARFIL SERVICES', 'amount': '12761.32'},
        ], currency='BWP')
        self.assertEqual(len(dup['hard']), 5)
        self.assertTrue(all(h['clash_ref'] == 'PAY/ADIC/2026/07/29/0003'
                            for h in dup['hard']))

    def test_already_paid_line_is_caught(self):
        """G2026004535 1,535.98 was PAID on 07/28/0002 and re-raised."""
        self._pr('PAY/ADIC/2026/07/28/0002', 'paid', [
            {'description': 'G2026004535 COMMERCIAL AUTO GL', 'amount': '1535.98'},
        ])
        dup = find_duplicates(
            [{'description': 'G2026004535 COMMERCIAL AUTO GL', 'amount': '1535.98'}],
            currency='BWP')
        self.assertEqual(len(dup['hard']), 1)
        self.assertEqual(dup['hard'][0]['clash_status'], 'paid')

    def test_entity_spelling_does_not_hide_a_duplicate(self):
        """CHOPPIES 42,380.04 sat on 07/23/0001 ('Alpha Direct Insurance Company')
        and 07/28/0001 ('Alpha Direct Insurance') — the same company typed two
        ways. Keying on entity would let it through."""
        self._pr('PAY/ADIC/2026/07/23/0001', 'pending_cfo', [
            {'description': 'G2025003601 CHOPPIES DISTRIBUT', 'amount': '42380.04'},
        ])
        dup = find_duplicates(
            [{'description': 'G2025003601 CHOPPIES DISTRIBUT', 'amount': '42380.04'}],
            currency='BWP')
        self.assertEqual(len(dup['hard']), 1)

    def test_repeat_within_the_same_pack(self):
        dup = find_duplicates([
            {'description': 'G2026004287 CARFIL', 'amount': '5307.32'},
            {'description': 'G2026004287 CARFIL', 'amount': '5307.32'},
        ], currency='BWP')
        self.assertEqual(len(dup['hard']), 1)
        self.assertEqual(dup['hard'][0]['clash_kind'], 'same_request')

    # ── what must NOT be blocked ────────────────────────────────────────────
    def test_same_claim_different_amounts_allowed(self):
        """G2026005105 carries a repair (25,684.20) AND an ex-gratia (34,220.00).
        Both are real. Keying on the claim alone would block the ex-gratia and the
        control would be routed around."""
        self._pr('PAY/ADIC/2026/07/27/0001', 'paid', [
            {'description': 'G2026005105 KOMAROV INVESTMEN', 'amount': '25684.20'},
        ])
        dup = find_duplicates(
            [{'description': 'G2026005105 KOMAROV EX-GRATIA', 'amount': '34220.00'}],
            currency='BWP')
        self.assertEqual(dup['hard'], [])
        # …but the approver is still told about it.
        self.assertEqual(len(dup['soft']), 1)

    def test_same_amount_different_claims_allowed(self):
        """G2026004605 and G2026004619 are both 2,576.00 — different claims."""
        self._pr('PAY/ADIC/2026/07/27/0001', 'paid', [
            {'description': 'G2026004605 COMMERCIAL AUTO G', 'amount': '2576.00'},
        ])
        dup = find_duplicates(
            [{'description': 'G2026004619 COMMERCIAL AUTO GL', 'amount': '2576.00'}],
            currency='BWP')
        self.assertEqual(dup['hard'], [])

    def test_rejected_and_cancelled_are_ignored(self):
        """A request killed on purpose must be re-raisable."""
        self._pr('PAY/ADIC/2026/07/01/0001', 'rejected', [
            {'description': 'G2026009999 SOMEONE', 'amount': '100.00'}])
        self._pr('PAY/ADIC/2026/07/02/0001', 'cancelled', [
            {'description': 'G2026009998 SOMEONE', 'amount': '200.00'}])
        for claim, amt in (('G2026009999', '100.00'), ('G2026009998', '200.00')):
            dup = find_duplicates(
                [{'description': f'{claim} SOMEONE', 'amount': amt}],
                currency='BWP')
            self.assertEqual(dup['hard'], [], claim)

    def test_currency_is_part_of_the_key(self):
        self._pr('PAY/ADIC/2026/07/29/0007', 'paid', [
            {'description': 'G2026004111 SMG', 'amount': '1000.00'}], currency='ZAR')
        dup = find_duplicates([{'description': 'G2026004111 SMG', 'amount': '1000.00'}],
                              currency='BWP')
        self.assertEqual(dup['hard'], [])

    def test_exclude_pk_lets_a_request_be_rechecked(self):
        pr = self._pr('PAY/ADIC/2026/07/29/0003', 'pending_cfo', [
            {'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        dup = find_duplicates(pr.line_items, currency='BWP', exclude_pk=pr.pk)
        self.assertEqual(dup['hard'], [])


@window_always_open
class DuplicateGateTests(APITestCase):
    """The gate as a user meets it: create, sign off, pay."""

    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.pako = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        self.kago = User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        self.lega = User.objects.create_user('lntabeni', email='lntabeni@alphadirect.co.bw')
        self.clerk = User.objects.create_user('btendani', email='btendani@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _post(self, lines, **extra):
        self.client.force_authenticate(self.clerk)
        return self.client.post(self.list_url, {
            'subject': 'Repair invoices',
            'category': PaymentRequest.Category.OTHER,
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            # These tests are about the duplicate gate, not the bank-detail gate.
            'account_name': 'Carfil', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
            'line_items': lines, **extra}, format='json')

    def test_first_request_is_accepted(self):
        r = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        self.assertEqual(r.status_code, 201, r.content)

    def _committee_approve(self, pk):
        for who in (self.pako, self.kago, self.lega):
            self.client.force_authenticate(who)
            r = self.client.post(reverse('v1-payment-exception-signoff', args=[pk]),
                                 {'decision': 'approve'}, format='json')
            self.assertEqual(r.status_code, 200, r.content)

    def test_second_identical_request_goes_to_the_committee_not_refused(self):
        """CFO 2026-09-04: "even if there is a genuine duplicate the committee can
        decide to pay". Entered, flagged, decided by three of six — never a 400."""
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        r = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(body['exception']['control'], 'PAY-DUP-01')
        self.assertIn('duplicate', body['exception']['message'])
        self.assertIn('not blocked', body['exception']['message'])
        pr = PaymentRequest.objects.get(id=body['id'])
        self.assertEqual(pr.exception_control, 'PAY-DUP-01')
        # The committee must see the line and the clashing request, or they
        # cannot decide anything.
        self.assertIn('G2026004287', pr.exception_reason)
        self.assertIn('PAY/', pr.exception_reason)
        self.assertTrue(pr.duplicate_matches)

    def test_both_requests_exist_after_a_duplicate(self):
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        self.assertEqual(PaymentRequest.objects.count(), 2)

    def test_raiser_override_fields_change_nothing(self):
        """The raiser cannot talk a duplicate past the committee — a reason or a
        category typed on the form is ignored; the committee still decides."""
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        r = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}],
                       duplicate_override_reason=('FNB rejected the first attempt with RJCT '
                                                  'on 30 July, confirmed against the statement.'),
                       duplicate_override_category='bank_rejected')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-DUP-01')

    def test_committee_approved_duplicate_then_passes_finance_signoff(self):
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        pk = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}]).json()['id']
        self._committee_approve(pk)
        pr = PaymentRequest.objects.get(id=pk)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(pr.exception_decision, 'approve')
        # Finance sign-off must NOT re-find the same clash and send it back.
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-decide', args=[pk]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_committee_reject_ends_the_duplicate(self):
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        pk = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}]).json()['id']
        self.client.force_authenticate(self.pako)
        r = self.client.post(reverse('v1-payment-exception-signoff', args=[pk]),
                             {'decision': 'reject', 'note': 'already on the other request'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PaymentRequest.objects.get(id=pk).status, PaymentRequest.Status.REJECTED)

    def test_clean_request_stores_no_override(self):
        r = self._post([{'description': 'G2026004999 SOMEONE', 'amount': '10.00'}],
                       duplicate_override_reason='a reason nobody asked me for at all')
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.duplicate_override_reason, '')

    # ── the pre-existing queue: raised before the control existed ───────────
    def test_signoff_sends_a_duplicate_found_late_to_the_committee(self):
        """The ten requests in the queue on 3 Aug never passed a creation check,
        and two twins raised minutes apart only collide here. Sign-off must catch
        them — and since 2026-09-04 that means: to the committee, not a 409."""
        a = self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        # Simulate a request that predates the gate by writing it straight in.
        b = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/07/29/0002', status='pending_finance',
            currency='BWP', subject='Carfil repair invoices', created_by=self.clerk,
            line_items=[{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}],
            total=Decimal('5307.32'))
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-decide', args=[b.id]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-DUP-01')
        b.refresh_from_db()
        self.assertEqual(b.status, PaymentRequest.Status.EXCEPTION)
        self.assertEqual(b.exception_control, 'PAY-DUP-01')
        self.assertIn(a.json()['ref'], b.exception_reason)
        self.assertIsNotNone(b.exception_raised_at)

    def test_signoff_still_allows_a_clean_request(self):
        pr_id = self._post([{'description': 'G2026004777 CLEAN', 'amount': '99.00'}]).json()['id']
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_rejecting_a_duplicate_is_never_blocked(self):
        """The gate must not trap a duplicate in the queue — rejecting it is the
        whole point."""
        self._post([{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}])
        b = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/07/29/0002', status='pending_finance',
            currency='BWP', subject='dup', created_by=self.clerk,
            line_items=[{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}],
            total=Decimal('5307.32'))
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-decide', args=[b.id]),
                             {'decision': 'reject', 'notes': 'duplicate of 0003'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)


@window_always_open
class DuplicatePaymentGateTests(APITestCase):
    """The last gate: completing the CFO task IS the payment."""

    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.clerk = User.objects.create_user('btendani', email='btendani@alphadirect.co.bw')

    def _pr(self, ref, status, lines, task=None):
        return PaymentRequest.objects.create(
            ref=ref, status=status, currency='BWP', subject=ref, task=task,
            created_by=self.clerk, line_items=lines,
            total=sum(Decimal(l['amount']) for l in lines))

    def _cfo_task(self):
        from core.models import OmniTask
        return OmniTask.objects.create(
            assigner=self.clerk, assignee=self.cfo, title='Payment authorisation',
            body='x', status=OmniTask.Status.PENDING, source='payment_request')

    def test_paying_a_duplicate_is_refused(self):
        from django.core.exceptions import ValidationError

        from taskboard.services import complete_task
        self._pr('PAY/ADIC/2026/08/03/0001', 'paid',
                 [{'description': 'G2026004524 CARFIL', 'amount': '8599.94'}])
        task = self._cfo_task()
        self._pr('PAY/ADIC/2026/07/29/0003', 'pending_cfo',
                 [{'description': 'G2026004524 CARFIL', 'amount': '8599.94'}],
                 task=task)
        with self.assertRaises(ValidationError) as ctx:
            complete_task(task, self.cfo, 'Paid via FNB batch this morning.', 5)
        self.assertIn('G2026004524', str(ctx.exception))
        task.refresh_from_db()
        self.assertNotEqual(task.status, 'done')

    def test_paying_a_clean_request_works(self):
        from core.models import OmniTask
        from taskboard.services import complete_task
        task = self._cfo_task()
        pr = self._pr('PAY/ADIC/2026/07/29/0009', 'pending_cfo',
                      [{'description': 'ORANGE JULY 26', 'amount': '12730.23'}],
                      task=task)
        complete_task(task, self.cfo, 'Paid via FNB batch this morning.', 5)
        task.refresh_from_db()
        pr.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE)
        self.assertEqual(pr.status, PaymentRequest.Status.PAID)

    def test_free_text_alone_no_longer_carries_through_to_payment(self):
        """Written reason only — the payment must still be REFUSED.

        This test previously asserted the opposite, and that rule is what let
        claim G2026004368 (ARCON CRAFTS, BWP 13,662.67) be paid twice: correctly
        detected on PAY/ADIC/2026/08/07/0004 as already carried on
        PAY/ADIC/2026/07/28/0001, cleared by 25 characters typed by the same
        person who raised it, and both requests are marked paid. From
        2026-08-09 an override also needs a category, a document, and a second
        person (CFO decision).
        """
        from django.core.exceptions import ValidationError
        from taskboard.services import complete_task
        self._pr('PAY/ADIC/2026/08/03/0001', 'paid',
                 [{'description': 'G2026004524 CARFIL', 'amount': '8599.94'}])
        task = self._cfo_task()
        pr = self._pr('PAY/ADIC/2026/07/29/0003', 'pending_cfo',
                      [{'description': 'G2026004524 CARFIL', 'amount': '8599.94'}],
                      task=task)
        pr.duplicate_override_reason = ('FNB rejected the 3 Aug batch with RJCT; '
                                        'confirmed unpaid on the statement.')
        pr.save(update_fields=['duplicate_override_reason'])
        with self.assertRaises(ValidationError):
            complete_task(task, self.cfo, 'Re-paid after the bank rejection.', 5)

    def test_a_committee_approved_duplicate_is_paid(self):
        """CFO 2026-09-04: three of six said pay — the CFO's mark-as-paid must not
        re-find the clash and refuse."""
        from core.models import OmniTask
        from taskboard.services import complete_task
        self._pr('PAY/ADIC/2026/08/03/0003', 'paid',
                 [{'description': 'G2026004526 CARFIL', 'amount': '8599.94'}])
        task = self._cfo_task()
        pr = self._pr('PAY/ADIC/2026/07/29/0005', 'pending_cfo',
                      [{'description': 'G2026004526 CARFIL', 'amount': '8599.94'}],
                      task=task)
        pr.exception_control = 'PAY-DUP-01'
        pr.exception_decision = 'approve'
        pr.save(update_fields=['exception_control', 'exception_decision'])
        complete_task(task, self.cfo, 'Committee approved; paid via FNB.', 5)
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE)

    def test_a_complete_override_does_carry_through_to_payment(self):
        """Reason + category + document + a DIFFERENT person: payment proceeds."""
        from core.models import OmniTask
        from taskboard.services import complete_task
        from taskboard.models import PaymentRequestAttachment
        self._pr('PAY/ADIC/2026/08/03/0002', 'paid',
                 [{'description': 'G2026004525 CARFIL', 'amount': '8599.94'}])
        task = self._cfo_task()
        pr = self._pr('PAY/ADIC/2026/07/29/0004', 'pending_cfo',
                      [{'description': 'G2026004525 CARFIL', 'amount': '8599.94'}],
                      task=task)
        ev = PaymentRequestAttachment.objects.create(
            request=pr, file='evidence/fnb-rejection.pdf')
        pr.duplicate_override_reason = ('FNB rejected the 3 Aug batch with RJCT; '
                                        'confirmed unpaid on the statement.')
        pr.duplicate_override_category = 'bank_rejected'
        pr.duplicate_override_evidence = ev
        pr.duplicate_override_approved_by = self.cfo      # not the raiser
        pr.save()
        complete_task(task, self.cfo, 'Re-paid after the bank rejection.', 5)
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE)


class AuthoriseContextMessageTests(SimpleTestCase):
    """The refusal must describe the screen its reader is standing on.

    CFO, 3 Aug 2026: blocked on PAY/ADIC/2026/07/23/0001 (BWP 137,780.68) from a
    task in his inbox, he was told to "remove those lines" — someone else's pack,
    which he cannot edit — and to "write why in the box below", a box that only
    exists in the new-payment form. The only box on his screen was the completion
    note, which does not override anything. A control that refuses and then names
    two impossible actions reads as a broken screen rather than a finding, and the
    next move after that is to work around it.
    """

    # The actual clash: CHOPPIES 42,380.04 on 07/23/0001 and 07/28/0001 at once,
    # BOTH awaiting CFO authorisation — neither paid.
    TWIN = [{
        'line': 4, 'label': 'G2025003601 CHOPPIES DISTRIBUT',
        'amount': '42380.04', 'clash_kind': 'existing_request',
        'clash_ref': 'PAY/ADIC/2026/07/28/0001', 'clash_status': 'pending_cfo',
        'detail': ('BWP 42,380.04 against G2025003601 is already on '
                   'PAY/ADIC/2026/07/28/0001 (awaiting CFO authorisation)'),
    }]
    PAID = [{
        'line': 3, 'label': 'G2026004524 CARFIL SERVICES',
        'amount': '8599.94', 'clash_kind': 'existing_request',
        'clash_ref': 'PAY/ADIC/2026/08/03/0001', 'clash_status': 'paid',
        'detail': ('BWP 8,599.94 against G2026004524 is already on '
                   'PAY/ADIC/2026/08/03/0001 (already paid)'),
    }]

    def _msg(self, hard, context):
        return blocking_message(hard, currency='BWP',
                                total_hard=hard_total(hard), context=context)

    def test_authorise_context_never_names_the_raisers_actions(self):
        msg = self._msg(self.TWIN, 'authorise')
        self.assertNotIn('box below', msg)
        self.assertNotIn('Remove those lines', msg)

    def test_authorise_context_names_clear_from_queue_and_the_other_request(self):
        msg = self._msg(self.TWIN, 'authorise')
        self.assertIn('Clear from queue', msg)
        # It must say WHICH other request, or the CFO has to go hunting for it.
        self.assertIn('PAY/ADIC/2026/07/28/0001', msg)

    def test_two_live_requests_are_not_described_as_already_paid(self):
        """The deadlock case. Both twins are pending, so nothing has been paid —
        'already paid' sends the reader hunting a payment that was never made."""
        msg = self._msg(self.TWIN, 'authorise')
        self.assertIn('Nothing has been paid yet', msg)
        self.assertNotIn('already raised or paid', msg)
        self.assertIn('ANOTHER LIVE REQUEST', msg)

    def test_a_genuinely_paid_clash_still_says_paid(self):
        """The twin wording must not leak onto a real already-paid duplicate —
        that one IS money out of the door and has to keep reading that way."""
        msg = self._msg(self.PAID, 'authorise')
        self.assertIn('already raised or paid', msg)
        self.assertNotIn('Nothing has been paid yet', msg)

    def test_raise_context_is_unchanged(self):
        """The raiser owns the lines and has the override box, so their wording
        must not regress into the approver's."""
        msg = self._msg(self.PAID, 'raise')
        self.assertIn('Remove those lines', msg)
        self.assertIn('box below', msg)
        self.assertNotIn('Clear from queue', msg)

    def test_default_context_is_the_raiser(self):
        self.assertEqual(
            blocking_message(self.PAID, currency='BWP',
                             total_hard=hard_total(self.PAID)),
            self._msg(self.PAID, 'raise'))

    def test_a_pack_duplicating_its_own_line_is_not_a_twin(self):
        """'Delete the row' IS the fix for a self-duplicate, so it must not be
        rewritten as a two-request deadlock."""
        same = [{'line': 21, 'label': 'G2026005105 KOMAROV', 'amount': '34220.00',
                 'clash_kind': 'same_request', 'clash_ref': '', 'clash_status': '',
                 'detail': 'line 21 repeats line 20 on this same request'}]
        msg = self._msg(same, 'authorise')
        self.assertNotIn('Nothing has been paid yet', msg)


@window_always_open
class DuplicateRefusalIsLabelledForTheUITests(APITestCase):
    """The refusal must reach the browser as a control finding, not a generic 400.

    The global toast renders 240 characters, so the CFO's block arrived cut off
    mid-word ("Remove t") in a popup floating over the very box it referred to.
    The front end suppresses that toast and renders the finding in full — but only
    when the response is labelled, so the label is asserted here.
    """

    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.clerk = User.objects.create_user(
            'btendani', email='btendani@alphadirect.co.bw')

    def test_complete_task_returns_the_control_code(self):
        from core.models import OmniTask
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/07/28/0001', status='pending_cfo', currency='BWP',
            subject='twin', created_by=self.clerk,
            line_items=[_L('G2025003601 CHOPPIES DISTRIBUT', '42380.04')],
            total=Decimal('42380.04'))
        task = OmniTask.objects.create(
            assigner=self.clerk, assignee=self.cfo,
            title='Payment authorisation', body='x',
            status=OmniTask.Status.PENDING, source='payment_request')
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/07/23/0001', status='pending_cfo', currency='BWP',
            subject='twin', created_by=self.clerk, task=task,
            line_items=[_L('G2025003601 CHOPPIES DISTRIBUT', '42380.04')],
            total=Decimal('42380.04'))

        self.client.force_authenticate(self.cfo)
        r = self.client.post(reverse('v1-taskboard-complete', args=[task.id]),
                             {'body': 'Authorised and paid via FNB.',
                              'interaction_seconds': 5}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.data.get('control'), 'PAY-DUP-01')
        self.assertIn('PAY/ADIC/2026/07/28/0001', r.data['detail'])
        self.assertNotIn('box below', r.data['detail'])
        task.refresh_from_db()
        self.assertNotEqual(task.status, OmniTask.Status.DONE)

    def test_an_ordinary_completion_error_carries_no_control_code(self):
        """The label must mean 'payment control', not 'any 400'."""
        from core.models import OmniTask
        task = OmniTask.objects.create(
            assigner=self.clerk, assignee=self.cfo, title='Ordinary task',
            body='x', status=OmniTask.Status.DONE, source='manual')
        self.client.force_authenticate(self.cfo)
        r = self.client.post(reverse('v1-taskboard-complete', args=[task.id]),
                             {'body': 'Done already.', 'interaction_seconds': 5},
                             format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIsNone(r.data.get('control'))


class RecurringMonthlyPeriodTests(SimpleTestCase):
    """A fixed monthly payment must not read as a repeat of last month.

    Laone Thebe (SA operations, 25 Aug 2026) was refused today's ZAR batch as
    'already submitted'. Two lines carry the same Rand amount every month —
    Salary 5,557.91 and Omega Compliance 3,409.75 — and no invoice number, so the
    only signal left is amount + payee text. Last month's are marked paid, so the
    guard read this month's genuine payment as a duplicate. The month a request
    was raised is what tells one from the next; these tests pin that in the pure
    matcher, where no database is needed to prove the rule.
    """

    JULY = ('PAY/SA/2026/07/23/0002', 'paid',
            [_L('Salary July 2026', '5557.91')], '2026-07')

    def test_same_amount_and_wording_but_a_different_month_does_not_block(self):
        """The exact Laone case: stale wording ('Salary July 2026' reused) AND
        the same amount, raised in a later month. It must drop to a soft warning,
        not a hard block."""
        dup = compare_lines([_L('Salary July 2026', '5557.91')],
                            [self.JULY], currency='ZAR', new_period='2026-08')
        self.assertEqual(dup['hard'], [])
        self.assertEqual(len(dup['soft']), 1)
        self.assertEqual(dup['soft'][0]['clash_kind'], 'recurring_other_month')

    def test_same_month_still_blocks(self):
        """A genuine same-month double-pay (the CFAO MOBILITY shape, 07-28 and
        07-29) is untouched — same month, still a hard clash."""
        dup = compare_lines([_L('Salary July 2026', '5557.91')],
                            [self.JULY], currency='ZAR', new_period='2026-07')
        self.assertEqual(len(dup['hard']), 1)
        self.assertEqual(dup['soft'], [])

    def test_unknown_month_on_either_side_still_blocks(self):
        """Fail safe: if a period is missing, behave exactly as before — block."""
        no_period_existing = ('PAY/SA/2026/07/23/0002', 'paid',
                              [_L('Salary July 2026', '5557.91')])  # 3-tuple, no period
        dup = compare_lines([_L('Salary July 2026', '5557.91')],
                            [no_period_existing], currency='ZAR', new_period='2026-08')
        self.assertEqual(len(dup['hard']), 1)
        # And with no new_period at all (the default), also block.
        dup2 = compare_lines([_L('Salary July 2026', '5557.91')],
                             [self.JULY], currency='ZAR')
        self.assertEqual(len(dup2['hard']), 1)

    def test_a_reference_clash_ignores_the_month(self):
        """Period only rescues a NO-reference payee match. A shared claim number
        across two months is still a hard duplicate — a claim is paid once."""
        existing = ('PAY/ADIC/2026/07/29/0003', 'paid',
                    [_L('G2026004287 CARFIL', '5307.32')], '2026-07')
        dup = compare_lines([_L('G2026004287 CARFIL', '5307.32')],
                            [existing], currency='BWP', new_period='2026-08')
        self.assertEqual(len(dup['hard']), 1)


class RecurringMonthlyGateTests(APITestCase):
    """The same rule through the real register, anchored on created_at."""

    def _pr(self, ref, status, lines, *, currency='ZAR', created_at=None):
        pr = PaymentRequest.objects.create(
            ref=ref, status=status, currency=currency, subject=ref,
            line_items=lines, total=sum(Decimal(l['amount']) for l in lines))
        if created_at is not None:
            PaymentRequest.objects.filter(pk=pr.pk).update(created_at=created_at)
        return pr

    def test_last_months_salary_does_not_block_this_months(self):
        from datetime import timedelta

        from django.utils import timezone
        last_month = timezone.now() - timedelta(days=40)
        self._pr('PAY/SA/2026/07/23/0002', 'paid',
                 [_L('Salary July 2026', '5557.91')], created_at=last_month)
        # Same amount, even the same stale wording, raised now (a later month).
        dup = find_duplicates([_L('Salary July 2026', '5557.91')], currency='ZAR')
        self.assertEqual(dup['hard'], [])

    def test_this_months_own_duplicate_still_blocks(self):
        """Two of the same in the current month is still a double-pay."""
        self._pr('PAY/SA/2026/08/24/0001', 'pending_cfo',
                 [_L('Salary August 2026', '5557.91')])  # created now
        dup = find_duplicates([_L('Salary August 2026', '5557.91')], currency='ZAR')
        self.assertEqual(len(dup['hard']), 1)
