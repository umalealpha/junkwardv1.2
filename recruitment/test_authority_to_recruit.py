"""Authority to Recruit — access, signatures, and the cost arithmetic.

CFO 2026-08-03: "create an Authority to recruit ... visible to Arun, arjun,
unami, Dorothy and cfo".

The arithmetic tests exist because HR's own grade workbooks for Bobby Mothibi
and Kago Tshutlhedi both understated the cost of employment: the "Total Package
/ CTC" figure quoted to the candidate deducted the EMPLOYEE's provident-fund
contribution from the company's cost, and left the leave-pay accrual out of the
monthly column while the annual column included it. Bobby was quoted 47,600 a
month against a real cost of 50,000; Kago 37,500 against 39,300. If that ever
gets "simplified" back to trusting the workbook total, these tests fail.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from recruitment.models import AuthorityToRecruit

# Bobby Mothibi's structure, verbatim from
# 2026_08_03-Bobby Mothibi_Grade Salary Structure.xlsx
BOBBY_LINES = [
    {'sn': 1,  'item': 'Base Salary (Pre-Tax)', 'monthly': '40000', 'annual': '480000', 'note': ''},
    {'sn': 2,  'item': 'Provident Fund', 'monthly': '2800', 'annual': '33600', 'note': 'Employer Contribution'},
    {'sn': 3,  'item': 'Provident Fund', 'monthly': '2400', 'annual': '28800', 'note': 'Employee Contribution'},
    {'sn': 4,  'item': 'Medical Aid', 'monthly': '1200', 'annual': '14400', 'note': 'Estimate based on 50% contribution; 0 dependants'},
    {'sn': 5,  'item': 'Cellphone Allowance', 'monthly': '500', 'annual': '6000', 'note': 'Orange Contract'},
    {'sn': 6,  'item': 'Car Allowance', 'monthly': '4000', 'annual': '48000', 'note': ''},
    {'sn': 7,  'item': 'Fuel', 'monthly': '1000', 'annual': '12000', 'note': ''},
    {'sn': 8,  'item': 'Internet', 'monthly': '500', 'annual': '6000', 'note': ''},
    {'sn': 9,  'item': 'Leave Pay (18 Days p.a.)', 'monthly': '1666.67', 'annual': '20000', 'note': ''},
    {'sn': 10, 'item': 'TOTAL', 'monthly': '47600', 'annual': '648800', 'note': ''},
]


def _atr(**kw):
    base = dict(
        kind=AuthorityToRecruit.Kind.RECRUIT,
        person_name='Bokang Bobby Mothibi',
        position='Team Lead',
        department='Health Insurance',
        salary_lines=BOBBY_LINES,
        quoted_ctc_monthly=Decimal('47600'),
        quoted_ctc_annual=Decimal('648800'),
    )
    base.update(kw)
    return AuthorityToRecruit.objects.create(**base)


class AuthorityCostTests(APITestCase):
    def test_true_cost_excludes_employee_contribution_and_leave_accrual(self):
        a = _atr()
        m, _ann = a.cost_to_company()
        # 40000 + 2800 employer PF + 1200 + 500 + 4000 + 1000 + 500 = 50,000.
        # The 2400 employee PF and the 1666.67 leave accrual are NOT company cost.
        self.assertEqual(m, Decimal('50000.00'))

    def test_the_quoted_package_is_flagged_as_understated(self):
        a = _atr()
        var_m, _ = a.variance_to_quote()
        self.assertEqual(var_m, Decimal('2400.00'))   # exactly the employee's own PF

    def test_the_total_row_is_never_double_counted(self):
        """The workbook carries a TOTAL row; adding it would double the cost."""
        a = _atr()
        m, _ = a.cost_to_company()
        self.assertLess(m, Decimal('90000'))
        self.assertEqual(m, Decimal('50000.00'))

    def test_kago_regrade_arithmetic(self):
        lines = [
            {'item': 'Base Salary (Pre-Tax)', 'monthly': '30000', 'annual': '360000', 'note': ''},
            {'item': 'Provident Fund', 'monthly': '2100', 'annual': '25200', 'note': 'Employer Contribution'},
            {'item': 'Provident Fund', 'monthly': '1800', 'annual': '21600', 'note': 'Employee Contribution'},
            {'item': 'Medical Aid', 'monthly': '1200', 'annual': '14400', 'note': ''},
            {'item': 'Cellphone Allowance', 'monthly': '500', 'annual': '6000', 'note': ''},
            {'item': 'Car Allowance', 'monthly': '4000', 'annual': '48000', 'note': ''},
            {'item': 'Fuel', 'monthly': '1000', 'annual': '12000', 'note': ''},
            {'item': 'Internet', 'monthly': '500', 'annual': '6000', 'note': ''},
            {'item': 'Leave Pay (18 Days p.a.)', 'monthly': '1250', 'annual': '15000', 'note': ''},
            {'item': 'TOTAL', 'monthly': '37500', 'annual': '508200', 'note': ''},
        ]
        a = _atr(kind=AuthorityToRecruit.Kind.REGRADE, person_name='Kago Tshutlhedi',
                 position='Manager', department='Finance & Planning', level='M-1',
                 salary_lines=lines, quoted_ctc_monthly=Decimal('37500'),
                 quoted_ctc_annual=Decimal('508200'))
        m, _ = a.cost_to_company()
        self.assertEqual(m, Decimal('39300.00'))
        self.assertEqual(a.variance_to_quote()[0], Decimal('1800.00'))

    def test_reference_prefix_distinguishes_a_regrade_from_a_hire(self):
        hire = _atr()
        regrade = _atr(kind=AuthorityToRecruit.Kind.REGRADE, person_name='Kago Tshutlhedi')
        self.assertTrue(hire.reference.startswith('ATR-'), hire.reference)
        self.assertTrue(regrade.reference.startswith('ARG-'), regrade.reference)


class AuthorityAccessTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.ceo = User.objects.create_user('aiyer', email='aiyer@alphadirect.co.bw')
        self.unami = User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw')
        self.outsider = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.a = _atr()
        self.list_url = reverse('v1-recruitment-authorities')
        self.detail_url = reverse('v1-recruitment-authority-detail', args=[self.a.id])
        self.sign_url = reverse('v1-recruitment-authority-sign', args=[self.a.id])
        self.doc_url = reverse('v1-recruitment-authority-doc', args=[self.a.id])

    def test_a_signatory_can_list(self):
        self.client.force_authenticate(self.ceo)
        r = self.client.get(self.list_url)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data['authorities']), 1)
        self.assertEqual(r.data['my_signature_slug'], 'ceo')

    def test_ordinary_staff_are_refused_everywhere(self):
        """403, not an empty list — 'nothing here' must not look like 'not for you'."""
        self.client.force_authenticate(self.outsider)
        for url in (self.list_url, self.detail_url, self.doc_url):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(self.sign_url, {'decision': 'approve'}).status_code, 403)

    def test_an_hr_person_not_on_the_list_is_still_refused(self):
        hr = User.objects.create_user('otherhr', email='otherhr@alphadirect.co.bw')
        from core.models import get_user_profile
        p = get_user_profile(hr)
        if p:
            p.title = 'hr_manager'
            p.save(update_fields=['title'])
        self.client.force_authenticate(hr)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(self.client.get(self.list_url).status_code, (401, 403))


class AuthoritySignatureTests(APITestCase):
    def setUp(self):
        self.people = {
            'ceo': User.objects.create_user('aiyer', email='aiyer@alphadirect.co.bw'),
            'coo': User.objects.create_user('arjuniyer', email='arjuniyer@alphadirect.co.bw'),
            'human_capital': User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw'),
            'hr_bp': User.objects.create_user('dikgopoleng', email='dikgopoleng@alphadirect.co.bw'),
            'cfo': User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw'),
        }
        self.a = _atr()
        self.sign_url = reverse('v1-recruitment-authority-sign', args=[self.a.id])

    def test_it_is_approved_only_when_all_five_have_signed(self):
        for i, (slug, user) in enumerate(self.people.items(), start=1):
            self.client.force_authenticate(user)
            r = self.client.post(self.sign_url, {'decision': 'approve'}, format='json')
            self.assertEqual(r.status_code, 200, r.content)
            self.a.refresh_from_db()
            if i < 5:
                self.assertEqual(self.a.status, AuthorityToRecruit.Status.PENDING,
                                 f'approved after only {i} signature(s)')
        self.assertEqual(self.a.status, AuthorityToRecruit.Status.APPROVED)
        self.assertEqual(self.a.outstanding_signatories(), [])

    def test_one_decline_stops_it(self):
        self.client.force_authenticate(self.people['cfo'])
        r = self.client.post(self.sign_url, {'decision': 'decline', 'notes': 'Not budgeted this year.'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, AuthorityToRecruit.Status.DECLINED)

    def test_declining_needs_a_reason(self):
        self.client.force_authenticate(self.people['cfo'])
        r = self.client.post(self.sign_url, {'decision': 'decline'}, format='json')
        self.assertEqual(r.status_code, 400, r.content)

    def test_nobody_signs_twice(self):
        self.client.force_authenticate(self.people['ceo'])
        self.assertEqual(self.client.post(self.sign_url, {'decision': 'approve'}, format='json').status_code, 200)
        again = self.client.post(self.sign_url, {'decision': 'approve'}, format='json')
        self.assertEqual(again.status_code, 403, again.content)


class AuthorityDocumentTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.a = _atr()

    def test_the_docx_downloads_and_is_a_real_word_file(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.get(reverse('v1-recruitment-authority-doc', args=[self.a.id]))
        self.assertEqual(r.status_code, 200, r.content[:200])
        body = b''.join(r.streaming_content) if r.streaming else r.content
        self.assertTrue(body.startswith(b'PK'), 'not a docx (zip) payload')
        self.assertIn('.docx', r['Content-Disposition'])

    def test_the_document_states_the_true_cost_not_just_the_quote(self):
        from docx import Document
        from io import BytesIO
        from recruitment import authority_doc
        doc = Document(BytesIO(authority_doc.build(self.a)))
        text = '\n'.join(p.text for p in doc.paragraphs)
        text += '\n'.join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        self.assertIn('50,000.00', text, 'true monthly cost missing from the document')
        self.assertIn('47,600.00', text, 'quoted package missing from the document')

    def test_a_regrade_document_says_it_is_not_a_recruitment(self):
        from docx import Document
        from io import BytesIO
        from recruitment import authority_doc
        a = _atr(kind=AuthorityToRecruit.Kind.REGRADE, person_name='Kago Tshutlhedi')
        doc = Document(BytesIO(authority_doc.build(a)))
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('AUTHORITY TO REGRADE', text)
        self.assertIn('not an external appointment', text)
