"""taskboard/tests_supplier_terms.py — the supplier payment terms gate.

CFO directive 2026-07-28, after a supplier invoice raised in July was settled
in July at full value while the supplier book was unpaid. The authorisation pack
carried a reference and an amount only, so nobody could see the invoice was not
yet due.

These are the rules that must never regress:
  - a SUPPLIER payment request cannot be raised without the invoice number,
    the invoice date and the due date on every line;
  - the due date must respect the agreed credit term (you cannot shorten it);
  - the payment date cannot be before the earliest due date, unless a written
    early-settlement reason is given;
  - CLAIMS and operational payments (rent and the like) are NOT gated.

Run: manage.py test taskboard.tests_supplier_terms
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from taskboard.models import PaymentRequest
from taskboard.payment_views import _clean_lines, _validate_supplier_terms
from taskboard.test_helpers import seed_adic, window_always_open

def today():
    """Read the clock per use, never once at import: a run that straddles
    midnight Gaborone (22:00 UTC, where CI often sits) otherwise judges 'today'
    a day behind the view it is testing (CI 33922062679, 3 false FAILs)."""
    return timezone.localdate()


def month_end(d):
    import calendar
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def due_for(invoice_date, terms=30, basis='statement'):
    """The due date the gate will accept — terms run from the statement (the
    month-end the invoice lands on) unless invoice basis is agreed."""
    anchor = invoice_date if basis == 'invoice' else month_end(invoice_date)
    return anchor + datetime.timedelta(days=terms)


def line(**over):
    """A compliant supplier line: invoice old enough that the statement term has
    fully run, so it is genuinely payable today."""
    invoice_date = today() - datetime.timedelta(days=90)
    base = {
        'description': 'Korean Auto — parts',
        'amount': '211130.30',
        'invoice_number': 'KA-40118',
        'invoice_date': invoice_date.isoformat(),
        'terms_days': '30',
        'due_date': due_for(invoice_date).isoformat(),
        'discount_checked': True,
        # Ignored for supplier/vendor packs; required (and used) on claims lines.
        'claim_number': 'G2026004287',
    }
    base.update(over)
    return base


def validate(*raw, pay_date=None, override_reason=''):
    return _validate_supplier_terms(
        _clean_lines(list(raw)),
        pay_date=pay_date or today(),
        override_reason=override_reason,
    )


@window_always_open
class SupplierTermsGateTests(TestCase):
    def test_compliant_line_passes(self):
        err, meta = validate(line())
        self.assertIsNone(err)
        self.assertEqual(meta['days_early'], 0)

    def test_missing_invoice_number_blocks(self):
        err, _ = validate(line(invoice_number=''))
        self.assertIn('invoice number is required', err)

    def test_missing_invoice_date_blocks(self):
        err, _ = validate(line(invoice_date=''))
        self.assertIn('invoice date is required', err)

    def test_missing_due_date_blocks(self):
        err, _ = validate(line(due_date=''))
        self.assertIn('due date is required', err)

    def test_unparseable_invoice_date_blocks(self):
        err, _ = validate(line(invoice_date='28/07/2026'))
        self.assertIn('invoice date is required', err)

    def test_future_invoice_date_blocks(self):
        future = (today() + datetime.timedelta(days=1)).isoformat()
        err, _ = validate(line(invoice_date=future,
                               due_date=(today() + datetime.timedelta(days=31)).isoformat()))
        self.assertIn('is in the future', err)

    def test_due_date_shorter_than_agreed_terms_blocks(self):
        """The Korean Auto failure: a fresh invoice with a due date typed as
        'today' so it looks payable. The agreed term must win."""
        invoice_date = today() - datetime.timedelta(days=5)
        err, _ = validate(line(invoice_date=invoice_date.isoformat(),
                               terms_days='30',
                               due_date=today().isoformat()))
        self.assertIn('earlier than the agreed terms', err)
        self.assertIn(due_for(invoice_date).isoformat(), err)

    def test_paying_before_due_date_blocks(self):
        invoice_date = today() - datetime.timedelta(days=5)
        due = due_for(invoice_date)
        err, _ = validate(line(invoice_date=invoice_date.isoformat(),
                               due_date=due.isoformat()))
        self.assertIn('day(s) early', err)
        self.assertIn(due.isoformat(), err)

    def test_early_payment_allowed_with_written_reason(self):
        invoice_date = today() - datetime.timedelta(days=5)
        due = due_for(invoice_date)
        err, meta = validate(line(invoice_date=invoice_date.isoformat(),
                                  due_date=due.isoformat()),
                             override_reason='CFO approved — 5% settlement discount taken.')
        self.assertIsNone(err)
        self.assertEqual(meta['days_early'], (due - today()).days)
        self.assertEqual(meta['binding_due_date'], due.isoformat())

    def test_unchecked_discount_blocks(self):
        err, _ = validate(line(discount_checked=False))
        self.assertIn('discount', err)

    def test_a_not_yet_due_line_cannot_ride_alongside_a_due_one(self):
        """The smuggling case. Approving the pack pays every line on it, so the
        LATEST due date binds — a due invoice must not carry a fresh one out."""
        settled = today() - datetime.timedelta(days=90)     # term fully run
        fresh = today() - datetime.timedelta(days=10)        # not yet due
        not_yet_due = due_for(fresh)
        err, _ = validate(
            line(invoice_date=settled.isoformat(), due_date=due_for(settled).isoformat()),
            line(invoice_number='KA-40119', invoice_date=fresh.isoformat(),
                 due_date=not_yet_due.isoformat()),
        )
        self.assertIn(f'{(not_yet_due - today()).days} day(s) early', err)
        self.assertIn('line 2', err)
        self.assertIn(not_yet_due.isoformat(), err)

    def test_binding_due_date_is_the_latest(self):
        older = today() - datetime.timedelta(days=150)
        newer = today() - datetime.timedelta(days=90)
        err, meta = validate(
            line(invoice_date=older.isoformat(), due_date=due_for(older).isoformat()),
            line(invoice_number='KA-40119', invoice_date=newer.isoformat(),
                 due_date=due_for(newer).isoformat()),
        )
        self.assertIsNone(err)
        self.assertEqual(meta['binding_due_date'], due_for(newer).isoformat())


@window_always_open
class StatementBasisTests(TestCase):
    """Trade terms run from the STATEMENT, not the invoice. The Korean Auto
    invoice of 24 Jul 2026 on 30-day statement terms falls due 30 Aug 2026
    (31 Jul statement + 30 days) — NOT 23 Aug (invoice + 30). Invoice-basis
    would have released the payment a week early."""

    INVOICE = datetime.date(2026, 7, 24)
    STATEMENT_DUE = datetime.date(2026, 8, 30)
    INVOICE_DUE = datetime.date(2026, 8, 23)

    def korean_auto(self, **over):
        base = {'invoice_date': self.INVOICE.isoformat(), 'terms_days': '30',
                'due_date': self.STATEMENT_DUE.isoformat()}
        base.update(over)
        return line(**base)

    def test_statement_basis_is_the_default(self):
        err, meta = validate(self.korean_auto(), pay_date=self.STATEMENT_DUE)
        self.assertIsNone(err)
        self.assertEqual(meta['binding_due_date'], self.STATEMENT_DUE.isoformat())

    def test_invoice_plus_30_is_rejected_under_statement_terms(self):
        err, _ = validate(self.korean_auto(due_date=self.INVOICE_DUE.isoformat()),
                          pay_date=self.INVOICE_DUE)
        self.assertIn('earlier than the agreed terms', err)
        self.assertIn('2026-07-31 statement', err)
        self.assertIn(self.STATEMENT_DUE.isoformat(), err)

    def test_paying_on_23_august_is_still_seven_days_early(self):
        err, _ = validate(self.korean_auto(), pay_date=self.INVOICE_DUE)
        self.assertIn('7 day(s) early', err)

    def test_the_actual_28_july_payment_would_have_been_refused(self):
        err, _ = validate(self.korean_auto(), pay_date=datetime.date(2026, 7, 28))
        self.assertIn('33 day(s) early', err)
        self.assertIn(self.STATEMENT_DUE.isoformat(), err)

    def test_invoice_basis_is_available_when_agreed(self):
        err, meta = validate(
            self.korean_auto(terms_basis='invoice', due_date=self.INVOICE_DUE.isoformat()),
            pay_date=self.INVOICE_DUE)
        self.assertIsNone(err)
        self.assertEqual(meta['binding_due_date'], self.INVOICE_DUE.isoformat())

    def test_unknown_terms_basis_blocks(self):
        err, _ = validate(self.korean_auto(terms_basis='whenever'),
                          pay_date=self.STATEMENT_DUE)
        self.assertIn('terms basis must be', err)

    def test_february_month_end_is_handled(self):
        err, meta = validate(
            line(invoice_date='2026-02-10', terms_days='30', due_date='2026-03-30'),
            pay_date=datetime.date(2026, 3, 30))
        self.assertIsNone(err)   # 28 Feb 2026 statement + 30 days = 30 Mar
        self.assertEqual(meta['binding_due_date'], '2026-03-30')


@window_always_open
class SupplierTermsEndpointTests(TestCase):
    """The gate must refuse at the API — a non-compliant supplier request must
    never become a task, and a claims/operational request must be unaffected."""

    def setUp(self):
        self.me = User.objects.create_user('btendani', password='x')
        # A finance approver must exist or the create path 500s before the gate.
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'Korean Auto parts settlement',
            'category': PaymentRequest.Category.SUPPLIER,
            'line_items': [line()],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            # These tests are about supplier terms, not the bank-detail gate.
            'account_name': 'Korean Auto', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    def test_supplier_request_without_dates_goes_to_committee_not_refused(self):
        # CFO 2026-09-09: a supplier-terms problem no longer dead-ends the
        # raiser — it enters as a committee exception; the raiser is not blocked.
        r = self.post(line_items=[line(invoice_date='', due_date='')])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json().get('status'), 'exception')
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.exception_control, 'PAY-SUP-01')
        self.assertIn('Supplier terms', pr.exception_reason)

    def test_a_supplier_terms_exception_is_released_by_the_committee(self):
        # End-to-end (H39): the terms problem enters as an exception, then three
        # committee members sign it off and it moves to the normal finance path.
        User.objects.create_user('pako', email='pkago@alphadirect.co.bw', password='x')
        User.objects.create_user('oprah', email='omogomotsi@alphadirect.co.bw', password='x')
        r = self.post(line_items=[line(invoice_date='', due_date='')])
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.json()['id']

        def sign(email):
            self.client.force_login(User.objects.get(email=email))
            return self.client.post(
                reverse('v1-payment-exception-signoff', args=[pk]),
                {'decision': 'approve'}, content_type='application/json')

        self.assertEqual(sign('pkago@alphadirect.co.bw').status_code, 200)
        self.assertEqual(sign('ktshutlhedi@alphadirect.co.bw').status_code, 200)
        r3 = sign('omogomotsi@alphadirect.co.bw')
        self.assertEqual(r3.status_code, 200, r3.content)
        pr = PaymentRequest.objects.get(id=pk)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(pr.exception_decision, 'approve')

    def test_terms_plus_duplicate_does_not_bounce_forever(self):
        # Fable 5.1 audit 2026-09-09: a supplier-terms exception that ALSO carries
        # a hard duplicate must not bounce committee<->sign-off forever. Once the
        # committee approves, the sign-off re-check must respect their decision
        # even though the stored control is PAY-SUP-01, not the duplicate code.
        # Red without the [PAY-DUP-01] marker + skip fix (the pack re-bounces).
        User.objects.create_user('pako', email='pkago@alphadirect.co.bw', password='x')
        User.objects.create_user('oprah', email='omogomotsi@alphadirect.co.bw', password='x')
        # A finance approver who did NOT sign the committee, for the finance leg,
        # and the CFO the finance sign-off routes the approved payment on to.
        User.objects.create_user('lega', email='lntabeni@alphadirect.co.bw',
                                 password='x', first_name='Legakwa')
        User.objects.create_superuser('cfo', email='pganesharajah@alphadirect.co.bw',
                                      password='x')
        # a prior PAID request the new one duplicates (same invoice + amount)
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/01/9001', entity='Alpha Direct Insurance Company',
            status=PaymentRequest.Status.PAID, subject='prior', payee='Korean Auto',
            account_name='Korean Auto', account_number='62012345678', bank_name='FNB',
            currency='BWP', total='211130.30',
            line_items=[{'description': 'Korean Auto — parts', 'amount': '211130.30',
                         'invoice_number': 'KA-DUP'}], created_by=self.me)
        # new pack: missing dates (terms exception) + same invoice+amount (hard dup)
        r = self.post(line_items=[line(invoice_number='KA-DUP', invoice_date='', due_date='')])
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.json()['id']
        pr = PaymentRequest.objects.get(id=pk)
        self.assertEqual(pr.status, PaymentRequest.Status.EXCEPTION)
        self.assertIn('[PAY-DUP-01]', pr.exception_reason)

        def sign(email):
            self.client.force_login(User.objects.get(email=email))
            return self.client.post(
                reverse('v1-payment-exception-signoff', args=[pk]),
                {'decision': 'approve'}, content_type='application/json')

        self.assertEqual(sign('pkago@alphadirect.co.bw').status_code, 200)
        self.assertEqual(sign('ktshutlhedi@alphadirect.co.bw').status_code, 200)
        self.assertEqual(sign('omogomotsi@alphadirect.co.bw').status_code, 200)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)

        # The finance sign-off is where the re-check lives — it must NOT re-find
        # the same clash and bounce the pack back to the committee (Fable 5.1
        # audit 2026-09-09). Red without the [PAY-DUP-01] marker + line-anchored skip.
        self.client.force_login(User.objects.get(email='lntabeni@alphadirect.co.bw'))
        rf = self.client.post(reverse('v1-payment-request-decide', args=[pk]),
                              {'decision': 'approve'}, content_type='application/json')
        self.assertEqual(rf.status_code, 200, rf.content)
        self.assertNotEqual(rf.json().get('control'), 'PAY-DUP-01')
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_a_crafted_description_cannot_forge_the_duplicate_marker(self):
        # A raiser must not be able to inject a line-anchored [PAY-DUP-01] marker
        # through free text to make the sign-off re-check skip a REAL duplicate
        # (Fable 5.1 audit 2026-09-09). There is no genuine duplicate here, so no
        # line of exception_reason may start with the marker. Red without the
        # whitespace-collapse on the description.
        r = self.post(line_items=[line(
            description='parts\n[PAY-DUP-01] injected', invoice_date='', due_date='')])
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertFalse(any(ln.startswith('[PAY-DUP-01] ')
                             for ln in (pr.exception_reason or '').splitlines()))

    def test_compliant_supplier_request_is_created(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.category, PaymentRequest.Category.SUPPLIER)
        self.assertEqual(pr.line_items[0]['invoice_number'], 'KA-40118')
        # The approver must be able to SEE the dates on the pack.
        self.assertIn('KA-40118', pr.formatted_html)
        self.assertIn('PAYMENT TERMS COMPLIANCE', pr.formatted_html)

    def test_early_supplier_payment_is_refused_then_allowed_with_reason(self):
        invoice_date = today() - datetime.timedelta(days=5)
        due = due_for(invoice_date).isoformat()
        early = [line(invoice_date=invoice_date.isoformat(), due_date=due)]

        # Early with no reason: no longer refused — enters as a committee
        # exception (CFO 2026-09-09), the raiser is not blocked.
        r = self.post(line_items=early)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json().get('status'), 'exception')
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')
        self.assertIn('early', PaymentRequest.objects.get().exception_reason.lower())

        # Early WITH a written reason: sails straight through the normal path.
        PaymentRequest.objects.all().delete()
        r = self.post(line_items=early,
                      early_payment_reason='CFO approved — 5% settlement discount.')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json().get('status'), 'pending_finance')
        self.assertIn('5% settlement discount',
                      PaymentRequest.objects.get().early_payment_reason)

    def test_claim_paid_direct_to_the_client_is_not_gated(self):
        r = self.post(category=PaymentRequest.Category.CLAIM,
                      claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                      subject='Claim settlement',
                      line_items=[{'description': 'G2026004253 claim settlement',
                                   'amount': '211130.30',
                                   'claim_number': 'G2026004253'}])
        self.assertEqual(r.status_code, 201, r.content)
        # A non-gated pack must not be stamped with a payment date nobody
        # entered — "when the money leaves" would be an invented fact.
        self.assertIsNone(PaymentRequest.objects.get().payment_date)

    def test_supplier_payment_date_defaults_to_today(self):
        """Blank means "pay it now", so the gate judges today and stores it."""
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.get().payment_date, today())

    def test_operational_payment_is_not_gated(self):
        r = self.post(category=PaymentRequest.Category.OTHER,
                      subject='Office rent — August',
                      line_items=[{'description': 'Icon Building rent August',
                                   'amount': '85000.00'}])
        self.assertEqual(r.status_code, 201, r.content)

    def test_pack_never_claims_compliance_when_the_payment_date_is_early(self):
        """The pack derives the verdict from the payment date and the line due
        dates. A pack that reads 'on time' while the money leaves before the due
        date is worse than no control, so this must never regress."""
        from taskboard.payment_views import _render_html
        inv = datetime.date(2026, 7, 24)
        pack = _render_html({
            'entity': 'Alpha Direct Insurance Company', 'ref': 'PAY/ADIC/T/1',
            'category': PaymentRequest.Category.SUPPLIER, 'currency': 'BWP',
            'subject': 'Korean Auto', 'inputter': 'Bontle', 'verifier': 'Kago',
            'due_date': None, 'payment_date': datetime.date(2026, 7, 28),
            'account_name': '', 'account_number': '', 'bank_name': '',
            'opening_balance': None, 'total': 211130.30,
            'early_payment_reason': '', 'funds_already_moved': False,
            'line_items': [{
                'description': 'parts', 'gl_code': '', 'ref': '',
                'amount': 211130.30, 'invoice_number': 'KA-40118',
                'invoice_date': inv.isoformat(), 'terms_basis': 'statement',
                'terms_days': '30', 'due_date': '2026-08-30',
            }],
        })
        self.assertIn('33 DAY(S) EARLY', pack)
        self.assertNotIn('ON OR AFTER DUE DATE', pack)
        # The statement anchor must be visible so the approver can check it.
        self.assertIn('2026-07-31 statement', pack)

    def test_funds_moved_before_authority_is_flagged_on_the_pack(self):
        r = self.post(funds_already_moved=True)
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertTrue(pr.funds_already_moved)
        self.assertIn('BEFORE authorisation', pr.formatted_html)


@window_always_open
class ClaimsVsOperationsTests(TestCase):
    """CFO 2026-07-29 — "it's actually supplier payments for claims", and "it
    should ask whether its claims or operations payment so we don't confuse it".

    The first cut of PAY-SUP-01 gated category=supplier only. The packs it
    existed for were raised as CLAIM payments: a panel beater or parts supplier
    billing us for a repair, posted to claims payable (Carfil Services, five
    invoices, BWP 50,689.84 — no invoice date, no due date, the exact Korean
    Auto shape). Those must be gated. A claim settled straight to the
    policyholder has no third-party invoice and must not be.
    """

    def setUp(self):
        self.me = User.objects.create_user('btendani', password='x')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'Carfil repair invoices',
            'category': PaymentRequest.Category.CLAIM,
            'line_items': [line()],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            # These tests are about claim terms, not the bank-detail gate.
            'account_name': 'Carfil', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    # ── the claim number is required on every claims line (CFO 2026-07-29) ───
    def test_claim_without_a_claim_number_goes_to_committee(self):
        # CFO 2026-09-09 "no blocker": a missing claim number no longer refuses
        # the pack — it enters as a committee exception (the committee confirms it).
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                      line_items=[{'description': 'Settlement to policyholder',
                                   'amount': '9000.00'}])   # no claim_number
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-CLM-01')
        self.assertIn('PAY-CLM-01', PaymentRequest.objects.get().exception_reason)

    def test_one_missing_claim_number_among_several_is_named_for_the_committee(self):
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                      line_items=[{'description': 'A', 'amount': '10.00',
                                   'claim_number': 'G1'},
                                  {'description': 'B', 'amount': '20.00'},   # missing
                                  {'description': 'C', 'amount': '30.00',
                                   'claim_number': 'G3'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn('line 2', PaymentRequest.objects.get().exception_reason)

    def test_claim_number_missing_for_a_provider_goes_to_committee(self):
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.PROVIDER,
                      line_items=[line(claim_number='')])
        self.assertEqual(r.status_code, 201, r.content)
        # A provider line with no claim number carries BOTH the terms note and
        # the claim-number note; the first control wins as the stored code.
        self.assertIn('PAY-CLM-01', PaymentRequest.objects.get().exception_reason)

    def test_claim_number_is_shown_on_the_pack(self):
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                      line_items=[{'description': 'Settlement', 'amount': '9000.00',
                                   'claim_number': 'G2026004253'}])
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.line_items[0]['claim_number'], 'G2026004253')
        self.assertIn('G2026004253', pr.formatted_html)
        self.assertIn('Claim #', pr.formatted_html)

    def test_reloaded_claim_pack_keeps_the_claim_number(self):
        from taskboard.payment_views import _pr_public_dict, _render_html
        self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                  line_items=[{'description': 'Settlement', 'amount': '9000.00',
                               'claim_number': 'G2026004253'}])
        pr = PaymentRequest.objects.get()
        self.assertIn('G2026004253', _render_html(_pr_public_dict(pr)))

    def test_an_operations_payment_needs_no_claim_number(self):
        r = self.post(category=PaymentRequest.Category.PETTY_CASH,
                      subject='Float', claim_payee_type='',
                      line_items=[{'description': 'Float top-up', 'amount': '2000.00'}])
        self.assertEqual(r.status_code, 201, r.content)

    def test_a_stray_claim_number_is_dropped_from_a_non_claim_pack(self):
        """A user who typed a claim number then switched to operations must not
        leave one buried in the stored line JSON (Fable review 2026-07-29)."""
        r = self.post(category=PaymentRequest.Category.PETTY_CASH,
                      subject='Float', claim_payee_type='',
                      line_items=[{'description': 'Float top-up', 'amount': '2000.00',
                                   'claim_number': 'G2026009999'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.get().line_items[0]['claim_number'], '')

    # ── the hole that was open ───────────────────────────────────────────────
    def test_claim_paid_to_a_repairer_without_dates_goes_to_committee(self):
        """The Carfil case: a supplier invoice raised as a claim payment. Missing
        dates no longer refuse it — it enters as a committee exception (CFO
        2026-09-09); the raiser is not blocked."""
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.PROVIDER,
                      line_items=[{'description': 'G2026004287 CARFIL SERVICES',
                                   'amount': '5307.32',
                                   'claim_number': 'G2026004287'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')
        self.assertEqual(PaymentRequest.objects.get().exception_control, 'PAY-SUP-01')

    def test_claim_paid_to_a_repairer_with_dates_is_created_and_shows_them(self):
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.PROVIDER)
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.claim_payee_type,
                         PaymentRequest.ClaimPayeeType.PROVIDER)
        # The approver must SEE the invoice number, the due date and the verdict.
        self.assertIn('KA-40118', pr.formatted_html)
        self.assertIn('PAYMENT TERMS COMPLIANCE', pr.formatted_html)
        # Blank pay date means "pay it now" — the gate judged today and stored it.
        self.assertEqual(pr.payment_date, today())

    def test_claim_to_a_repairer_paid_before_due_goes_to_committee(self):
        # Paying before due no longer refuses the pack (CFO 2026-09-09) — it
        # enters as a committee exception; the raiser is not blocked.
        fresh = today() - datetime.timedelta(days=1)
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.PROVIDER,
                      line_items=[line(invoice_date=fresh.isoformat(),
                                       due_date=due_for(fresh).isoformat())])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')
        self.assertIn('not yet due', PaymentRequest.objects.get().exception_reason)

    # ── the question must actually be asked ──────────────────────────────────
    def test_claim_without_saying_who_is_paid_is_refused(self):
        r = self.post()
        self.assertEqual(r.status_code, 400)
        self.assertIn('who is being paid', r.json()['detail'].lower())
        self.assertFalse(PaymentRequest.objects.exists())

    def test_a_request_with_no_category_is_refused_not_guessed(self):
        """Category inference is gone. "Claims payable batch" for a panel beater
        used to auto-file as a CLAIM and so skip the gate entirely; omitting the
        field was also the easy bypass. Both are closed."""
        r = self.client.post(self.url, {
            'subject': 'Claims payable batch',
            'line_items': [{'description': 'Gaborone Panel Beaters',
                            'amount': '1000.00'}],
        }, content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('claims payment', r.json()['detail'])
        self.assertFalse(PaymentRequest.objects.exists())

    def test_claim_payee_type_must_be_a_real_choice(self):
        r = self.post(claim_payee_type='whoever')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PaymentRequest.objects.exists())

    def test_the_answer_is_printed_on_the_pack(self):
        r = self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT,
                      line_items=[{'description': 'G2026004253 settlement',
                                   'amount': '9000.00',
                                   'claim_number': 'G2026004253'}])
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertIn('PAID TO', pr.formatted_html)
        self.assertIn('policyholder', pr.formatted_html)

    def test_payee_type_is_not_stored_on_a_non_claim_pack(self):
        """It would read as a fact nobody was asked for."""
        r = self.post(category=PaymentRequest.Category.SUPPLIER,
                      claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.get().claim_payee_type, '')

    # ── claims are ADIC's alone ──────────────────────────────────────────────
    def test_claims_are_refused_for_a_non_adic_entity(self):
        r = self.post(entity='Alpha Direct SA',
                      claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT)
        self.assertEqual(r.status_code, 400)
        self.assertIn('ADIC', r.json()['detail'])
        self.assertFalse(PaymentRequest.objects.exists())

    def test_an_unresolvable_entity_cannot_raise_a_claim(self):
        """A control must never be satisfied by a fallback. _entity_code() ends
        in `return 'ADIC'`, so before this an unknown or misspelled entity name
        SATISFIED the claims-are-ADIC-only rule and got stamped PAY/ADIC/…
        (found by the Fable review, 2026-07-29)."""
        # 'Alpha' and 'Insurance' are FRAGMENTS of ADIC's own name: a control must
        # not key on a substring, or a bare fragment resolves to the ADIC row and
        # passes (Fable round 2). The drop-down always sends the exact name.
        for bogus in ('Nonsense Pty', 'Alpha Dirct Insurance', 'ADICC',
                      'Alpha', 'Insurance'):
            with self.subTest(entity=bogus):
                r = self.post(entity=bogus,
                              claim_payee_type=PaymentRequest.ClaimPayeeType.CLIENT)
                self.assertEqual(r.status_code, 400, f'{bogus} was accepted')
                self.assertIn('ADIC', r.json()['detail'])
        self.assertFalse(PaymentRequest.objects.exists())

    def test_a_non_adic_entity_may_still_raise_an_operations_payment(self):
        r = self.post(entity='Alpha Direct SA',
                      category=PaymentRequest.Category.SUPPLIER,
                      subject='Stationery')
        self.assertEqual(r.status_code, 201, r.content)

    # ── vendor packs escaped for the same reason ─────────────────────────────
    def test_vendor_payment_without_dates_goes_to_committee(self):
        r = self.post(category=PaymentRequest.Category.VENDOR,
                      subject='Vendor payments',
                      line_items=[{'description': 'Printing', 'amount': '19475.00'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')

    def test_petty_cash_stays_ungated(self):
        r = self.post(category=PaymentRequest.Category.PETTY_CASH,
                      subject='Petty cash float',
                      line_items=[{'description': 'Float top-up', 'amount': '2000.00'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(PaymentRequest.objects.get().payment_date)

    # ── a saved pack must not lose the gate when it is re-rendered ───────────
    def test_reloaded_claim_provider_pack_still_shows_the_terms(self):
        from taskboard.payment_views import _pr_public_dict, _render_html
        self.post(claim_payee_type=PaymentRequest.ClaimPayeeType.PROVIDER)
        pr = PaymentRequest.objects.get()
        pack = _render_html(_pr_public_dict(pr))
        self.assertIn('KA-40118', pack)
        self.assertIn('PAYMENT TERMS COMPLIANCE', pack)
