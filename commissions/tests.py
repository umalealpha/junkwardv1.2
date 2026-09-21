"""commissions/tests.py — money math + submission flow.

Run: DB_ENGINE=sqlite SECRET_KEY=… python manage.py test commissions
"""
import os
from decimal import Decimal

# stage-2 is email-pinned (no name fallback) — give the tests a known address.
os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.deadline_test_utils import submission_window_open
from . import service
from .api_views import CommissionSubmissionViewSet
from .models import (
    CommissionAgent,
    CommissionBankAccount,
    CommissionGroup,
    CommissionSubmission,
    CommissionSubmissionLine,
)

User = get_user_model()


# Roster-matching reviewer users (see access.py): 1st = Bokani/Tlamelo,
# 2nd = Pako/Kago (token match), final = CFO (by known email).
def stage1_user(u='bokani'):
    return User.objects.create_user(u, f'{u}@x.co', 'pw', first_name='Bokani', last_name='Makosha')
def stage2_user(u='pako'):
    # matches stage-2 by EMAIL (COMMISSIONS_STAGE2_EMAILS), unique username per call
    return User.objects.create_user(u, 'pako@x.co', 'pw', first_name='Pako', last_name='K')
def final_user(u='cfo'):
    return User.objects.create_user(u, 'pganesharajah@alphadirect.co.bw', 'pw',
                                    first_name='Prathap', last_name='Ganesharajah')


def approve_through_chain(sub, submitter):
    """Drive a submission all the way to APPROVED through the 3 stages."""
    service.submit(sub, submitter)
    service.review(sub, stage1_user(f's1{str(sub.id)[:8]}'), approve=True)
    service.review(sub, stage2_user(f's2{str(sub.id)[:8]}'), approve=True)
    service.review(sub, final_user(f'cfo{str(sub.id)[:8]}'), approve=True)
    return sub


class FinalApproveTests(TestCase):
    """CFO direct approve / send-back from any stage (CFO 2026-08-18)."""

    def setUp(self):
        self.inhouse = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(name='Direct Agent', group=self.inhouse)
        self.submitter = User.objects.create_user('subm', 'subm@x.co', 'pw')

    def _submitted(self, period='2026-05'):
        sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.inhouse, period_label=period)
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('1000'))
        service.submit(sub, self.submitter)
        return sub

    def test_cfo_approves_directly_from_submitted(self):
        sub = self._submitted()
        self.assertEqual(sub.status, CommissionSubmission.Status.SUBMITTED)
        service.final_approve(sub, final_user('cfoA'), approve=True)
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.APPROVED)
        self.assertIsNotNone(sub.final_by_id)

    def test_non_final_reviewer_cannot_direct_approve(self):
        sub = self._submitted()
        with self.assertRaises(ValueError):
            service.final_approve(sub, stage1_user('bok2'), approve=True)
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.SUBMITTED)

    def test_cfo_cannot_direct_approve_own_submission(self):
        sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.inhouse, period_label='2026-06')
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('500'))
        cfo = final_user('cfoSelf')
        service.submit(sub, cfo)
        with self.assertRaises(ValueError):
            service.final_approve(sub, cfo, approve=True)


class ComputeTotalsTests(TestCase):
    """The pure withholding math — no DB needed."""

    def test_ten_percent_withholding(self):
        t = service.compute_totals([Decimal('600.00'), Decimal('400.00')], Decimal('0.1000'))
        self.assertEqual(t['gross'], Decimal('1000.00'))
        self.assertEqual(t['withholding'], Decimal('100.00'))
        self.assertEqual(t['net'], Decimal('900.00'))

    def test_no_withholding_in_house(self):
        t = service.compute_totals([Decimal('1250.50')], Decimal('0.0000'))
        self.assertEqual(t['gross'], Decimal('1250.50'))
        self.assertEqual(t['withholding'], Decimal('0.00'))
        self.assertEqual(t['net'], Decimal('1250.50'))

    def test_rounding_half_up(self):
        # 333.33 * 10% = 33.333 -> 33.33
        t = service.compute_totals([Decimal('333.33')], Decimal('0.1000'))
        self.assertEqual(t['withholding'], Decimal('33.33'))
        self.assertEqual(t['net'], Decimal('300.00'))

    def test_empty_is_zero(self):
        t = service.compute_totals([], Decimal('0.1000'))
        self.assertEqual((t['gross'], t['withholding'], t['net']),
                         (Decimal('0.00'), Decimal('0.00'), Decimal('0.00')))


class SeedGroupsTests(TestCase):
    def test_three_groups_seeded(self):
        # 0002_seed_groups runs in the test DB build.
        self.assertEqual(CommissionGroup.objects.count(), 3)
        indep = CommissionGroup.objects.get(key='independent')
        self.assertEqual(indep.withholding_rate, Decimal('0.1000'))
        inhouse = CommissionGroup.objects.get(key='in_house')
        self.assertEqual(inhouse.withholding_rate, Decimal('0.0000'))
        self.assertEqual(inhouse.pays_via, 'payroll')
        # BDU is taxed through payroll — no 10% withholding of this kind.
        bdu = CommissionGroup.objects.get(key='bdu')
        self.assertEqual(bdu.withholding_rate, Decimal('0.0000'))
        self.assertEqual(bdu.pays_via, 'payroll')


class SubmissionFlowTests(TestCase):
    def setUp(self):
        self.indep = CommissionGroup.objects.get(key='independent')
        self.inhouse = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(name='Test Agent', group=self.indep)
        self.agent_user = User.objects.create_user('agent1', 'agent1@x.co', 'pw')

    def _sub_with_lines(self, group=None, amounts=(Decimal('600'), Decimal('400'))):
        group = group or self.indep
        agent = CommissionAgent.objects.create(name=f'A-{group.key}-{len(amounts)}', group=group)
        sub = CommissionSubmission.objects.create(agent=agent, group=group, period_label='2026-05')
        for a in amounts:
            CommissionSubmissionLine.objects.create(submission=sub, commission_amount=a)
        return sub

    def test_recompute_applies_group_rate(self):
        sub = self._sub_with_lines(self.indep)
        service.recompute_submission(sub)
        self.assertEqual(sub.gross_commission, Decimal('1000.00'))
        self.assertEqual(sub.withholding_amount, Decimal('100.00'))
        self.assertEqual(sub.net_payable, Decimal('900.00'))

    def test_recompute_in_house_no_withholding(self):
        sub = self._sub_with_lines(self.inhouse)
        service.recompute_submission(sub)
        self.assertEqual(sub.withholding_amount, Decimal('0.00'))
        self.assertEqual(sub.net_payable, sub.gross_commission)

    def test_independent_via_company_has_no_withholding(self):
        # Independent agent working THROUGH a company → the 10% does NOT apply.
        agent = CommissionAgent.objects.create(name='Via Co', group=self.indep,
                                               works_via_company=True)
        sub = CommissionSubmission.objects.create(agent=agent, group=self.indep, period_label='2026-05')
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('1000'))
        service.recompute_submission(sub)
        self.assertEqual(sub.withholding_rate, Decimal('0.0000'))
        self.assertEqual(sub.withholding_amount, Decimal('0.00'))
        self.assertEqual(sub.net_payable, Decimal('1000.00'))

    def test_independent_direct_still_withholds(self):
        agent = CommissionAgent.objects.create(name='Direct', group=self.indep,
                                               works_via_company=False)
        sub = CommissionSubmission.objects.create(agent=agent, group=self.indep, period_label='2026-05')
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('1000'))
        service.recompute_submission(sub)
        self.assertEqual(sub.withholding_amount, Decimal('100.00'))
        self.assertEqual(sub.net_payable, Decimal('900.00'))

    def test_submit_snapshots_and_locks_state(self):
        sub = self._sub_with_lines(self.indep)
        service.submit(sub, self.agent_user)
        self.assertEqual(sub.status, CommissionSubmission.Status.SUBMITTED)
        self.assertEqual(sub.withholding_rate, Decimal('0.1000'))
        self.assertIsNotNone(sub.submitted_at)
        # can't submit again from submitted
        with self.assertRaises(ValueError):
            service.submit(sub, self.agent_user)

    def test_three_stage_chain_to_approved(self):
        S = CommissionSubmission.Status
        sub = self._sub_with_lines(self.indep)
        service.submit(sub, self.agent_user)
        self.assertEqual(sub.status, S.SUBMITTED)
        # a 2nd-stage reviewer cannot act while it's still at 1st review
        with self.assertRaises(ValueError):
            service.review(sub, stage2_user(), approve=True)
        service.review(sub, stage1_user(), approve=True)      # 1st review
        self.assertEqual(sub.status, S.SECOND_REVIEW)
        self.assertIsNotNone(sub.first_reviewed_by)
        service.review(sub, stage2_user('pako2'), approve=True)   # 2nd review
        self.assertEqual(sub.status, S.FINAL_REVIEW)
        service.review(sub, final_user(), approve=True)       # CFO final
        self.assertEqual(sub.status, S.APPROVED)
        self.assertIsNotNone(sub.final_by)

    def test_first_reviewer_cannot_be_the_submitter(self):
        sub = self._sub_with_lines(self.indep)
        s1 = stage1_user()
        service.submit(sub, s1)                                # submitter is also a 1st reviewer
        with self.assertRaises(ValueError):                    # SoD: can't review own
            service.review(sub, s1, approve=True)

    def test_reject_then_resubmit(self):
        S = CommissionSubmission.Status
        sub = self._sub_with_lines(self.indep)
        service.submit(sub, self.agent_user)
        service.review(sub, stage1_user(), approve=False, note='fix policy 3')
        self.assertEqual(sub.status, S.REJECTED)
        service.submit(sub, self.agent_user)                   # rejected is resubmittable
        self.assertEqual(sub.status, S.SUBMITTED)


class PayoutExportTests(TestCase):
    def setUp(self):
        self.indep = CommissionGroup.objects.get(key='independent')

    def _approved(self, name, amounts, with_bank=True):
        agent = CommissionAgent.objects.create(name=name, group=self.indep)
        if with_bank:
            CommissionBankAccount.objects.create(agent=agent, bank_name='FNB',
                                                 account_number='620123456', branch_code='282667')
        sub = CommissionSubmission.objects.create(agent=agent, group=self.indep, period_label='2026-05')
        for a in amounts:
            CommissionSubmissionLine.objects.create(submission=sub, commission_amount=a)
        submitter = User.objects.create_user(f'u{name}'.replace(' ', ''), f'{name}@x.co'.replace(' ', ''), 'pw')
        approve_through_chain(sub, submitter)   # submit → 1st → 2nd → final
        return sub

    def test_ready_and_held_split(self):
        self._approved('Paid Agent', [Decimal('1000')], with_bank=True)
        self._approved('No Bank Agent', [Decimal('500')], with_bank=False)
        ready, held = service.payout_rows(self.indep, '2026-05')
        self.assertEqual(len(ready), 1)
        self.assertEqual(len(held), 1)
        self.assertEqual(ready[0]['agent'], 'Paid Agent')
        self.assertEqual(ready[0]['net'], '900.00')  # 1000 - 10%
        self.assertEqual(held[0]['agent'], 'No Bank Agent')

    def test_export_csv_has_header_and_ready_row_only(self):
        self._approved('Paid Agent', [Decimal('1000')], with_bank=True)
        self._approved('No Bank Agent', [Decimal('500')], with_bank=False)
        csv_text, meta = service.export_payout_csv(self.indep, '2026-05')
        self.assertIn('Agent,Agent code,Period', csv_text)
        self.assertIn('Paid Agent', csv_text)
        self.assertNotIn('No Bank Agent', csv_text)  # held, not exported
        self.assertEqual(meta['ready_count'], 1)
        self.assertEqual(meta['held_count'], 1)
        self.assertEqual(meta['total_net'], '900.00')


@submission_window_open   # 16th submission deadline (2026-08-28)
class ApiTests(TestCase):
    """Exercise the DRF viewset directly (no urlconf) — ownership + gating."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.indep = CommissionGroup.objects.get(key='independent')
        # agent linked to a user by email
        self.agent = CommissionAgent.objects.create(name='Api Agent', group=self.indep,
                                                     email='apiagent@x.co')
        self.agent_user = User.objects.create_user('apiagent', 'apiagent@x.co', 'pw')
        self.other = CommissionAgent.objects.create(name='Other Agent', group=self.indep)
        self.manager = User.objects.create_user('mgr', 'mgr@x.co', 'pw', is_superuser=True)

    def _line(self, amt):
        return {'policy_number': 'P1', 'client_name': 'C', 'transaction_type': 'new_business',
                'amount_collected': '1000.00', 'annualised_premium': '1000.00',
                'commission_rate': '15.0000', 'is_policy_closed': False, 'commission_amount': amt}

    def test_agent_creates_own_submission(self):
        view = CommissionSubmissionViewSet.as_view({'post': 'create'})
        req = self.factory.post('/x', {'agent': str(self.agent.id), 'period_label': '2026-06',
                                       'lines': [self._line('600.00'), self._line('400.00')]},
                                format='json')
        force_authenticate(req, user=self.agent_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 201, resp.data)
        sub = CommissionSubmission.objects.get(agent=self.agent, period_label='2026-06')
        self.assertEqual(sub.gross_commission, Decimal('1000.00'))
        self.assertEqual(sub.net_payable, Decimal('900.00'))  # totals computed on create

    def test_agent_cannot_create_for_another_agent(self):
        view = CommissionSubmissionViewSet.as_view({'post': 'create'})
        req = self.factory.post('/x', {'agent': str(self.other.id), 'period_label': '2026-06',
                                       'lines': []}, format='json')
        force_authenticate(req, user=self.agent_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

    def test_access_reports_reviewer_stages(self):
        view = CommissionSubmissionViewSet.as_view({'get': 'access'})
        req = self.factory.get('/x')
        force_authenticate(req, user=stage1_user())
        resp = view(req)
        self.assertTrue(resp.data['is_reviewer'])
        self.assertEqual(resp.data['stages'], ['stage1'])

    def test_non_reviewer_cannot_review(self):
        sub = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-06')
        service.submit(sub, self.agent_user)
        view = CommissionSubmissionViewSet.as_view({'post': 'review'})
        req = self.factory.post('/x', {'approve': True}, format='json')
        force_authenticate(req, user=self.agent_user)  # a plain agent, not a reviewer
        resp = view(req, pk=str(sub.id))
        self.assertEqual(resp.status_code, 403)

    def test_first_stage_reviewer_advances_one_stage(self):
        sub = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-06')
        service.submit(sub, self.agent_user)
        view = CommissionSubmissionViewSet.as_view({'post': 'review'})
        req = self.factory.post('/x', {'approve': True, 'note': 'ok'}, format='json')
        force_authenticate(req, user=stage1_user())
        resp = view(req, pk=str(sub.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.SECOND_REVIEW)  # one stage, not approved


class ImporterTests(TestCase):
    """Parse the 'Data' tab layout (synthetic fixture — no real customer data)."""

    def _make_wb(self, path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Data'
        ws.append(['DIMPHO COMMISSION JUNE 2026'])   # title row
        ws.append([])                                # spacer
        ws.append(['Policy Number', 'Policy Name',
                   'New Business / Renewal / Endorsement/ Previous Month', 'Annual / Monthly',
                   'Amount Collected during this period (P)', 'Annualised Premium',
                   'Commission Rate', 'Collection Date',
                   'If not collected, in the date stated in "H" what was the collection date?',
                   'Is the Policy closed in the system', 'Amount Applicable for Commission (P)',
                   'Commissions  (P)', 'Reason for not claiming the commision on time'])
        ws.append(['POL-1', 'Acme Ltd', 'New Business', 'Annual', 1000, 1200, 15,
                   '2026-06-10', '', 'Yes', 1000, 150, ''])
        ws.append(['POL-2', 'Beta Pty', 'Renewal', 'Monthly', 500, 600, 10,
                   '2026-06-12', '', 'No', 500, 50, ''])
        # totals rows — must be skipped: one with a blank policy cell, one with a
        # 'Grand Total' label parked in the policy-number column.
        ws.append([None, 'TOTAL', None, None, 1500, None, None, None, None, None, 1500, 200, None])
        ws.append(['Grand Total', None, None, None, 3000, None, None, None, None, None, 3000, 400, None])
        wb.save(path)

    def _path(self):
        import os
        import tempfile
        return os.path.join(tempfile.mkdtemp(), 'DIMPHO COMMISSION - JUNE 2026 verified.xlsx')

    def test_parse_maps_columns_and_skips_totals(self):
        from .importer import parse_workbook
        p = self._path()
        self._make_wb(p)
        lines = parse_workbook(p)
        self.assertEqual(len(lines), 2)               # totals row skipped
        self.assertEqual(lines[0]['policy_number'], 'POL-1')
        self.assertEqual(lines[0]['client_name'], 'Acme Ltd')
        self.assertEqual(lines[0]['transaction_type'], 'new_business')
        self.assertEqual(lines[0]['frequency'], 'Annual')
        self.assertEqual(lines[0]['amount_applicable'], Decimal('1000'))
        self.assertEqual(lines[0]['commission_amount'], Decimal('150'))
        self.assertTrue(lines[0]['is_policy_closed'])
        self.assertEqual(lines[1]['transaction_type'], 'renewal')
        self.assertFalse(lines[1]['is_policy_closed'])

    def _make_broker_wb(self, path):
        """Independent broker sheet layout: short "Policy #" header, a plain
        "Amount", "Deposit Date", and the misspelt "COMMISION" column —
        synthetic rows, no real customer data."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Sheet1'
        ws.append(['Deposit Date', 'Amount', 'Client Name', 'Policy #', 'Transaction date',
                   'Payment status', 'COMMISION', 'MOTOR', 'MOTOR COMMISSION',
                   'NON MOTOR', 'NON MOTOR COMMISSION'])
        ws.append(['2026-08-05', 1000, 'Client One', 'PN-100', '2026-08-01',
                   'Paid', 200, 1, 200, 0, 0])
        ws.append(['2026-08-06', 2000, 'Client Two', 'PN-200', '2026-08-02',
                   'Paid', 300, 0, 0, 1, 300])
        wb.save(path)

    def _broker_path(self):
        import os
        import tempfile
        return os.path.join(tempfile.mkdtemp(), 'JOHAN BURGER AUGUST 2026.xlsx')

    def test_parse_broker_layout_policy_hash_and_misspelt_commission(self):
        from .importer import parse_workbook
        p = self._broker_path()
        self._make_broker_wb(p)
        lines = parse_workbook(p)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['policy_number'], 'PN-100')        # "Policy #"
        self.assertEqual(lines[0]['client_name'], 'Client One')
        self.assertEqual(lines[0]['amount_collected'], Decimal('1000'))  # plain "Amount"
        self.assertEqual(lines[0]['commission_amount'], Decimal('200'))  # "COMMISION"
        self.assertEqual(str(lines[0]['collection_date']), '2026-08-05')  # "Deposit Date"

    def test_import_broker_independent_direct_withholds_ten_percent(self):
        from .importer import import_workbook
        p = self._broker_path()
        self._make_broker_wb(p)
        agent = CommissionAgent.objects.create(
            name='Test Broker', group=CommissionGroup.objects.get(key='independent'),
            works_via_company=False)
        r = import_workbook(p, 'independent', '2026-08', agent_name=agent.name, commit=True)
        self.assertEqual(r['lines'], 2)
        self.assertEqual(r['gross'], '500.00')       # 200 + 300
        self.assertEqual(r['net'], '450.00')         # less 10% direct-independent withholding

    def test_import_creates_bdu_submission_zero_withholding(self):
        from .importer import import_workbook
        p = self._path()
        self._make_wb(p)
        r = import_workbook(p, 'bdu', '2026-06', commit=True)
        self.assertEqual(r['agent'], 'DIMPHO')        # from the file name
        self.assertEqual(r['lines'], 2)               # both totals rows skipped
        # BDU is payroll / 0% withholding → net == gross == 150 + 50
        self.assertEqual(r['gross'], '200.00')
        self.assertEqual(r['net'], '200.00')

    def test_importer_refuses_to_overwrite_approved(self):
        from .importer import import_workbook
        p = self._path()
        self._make_wb(p)
        import_workbook(p, 'bdu', '2026-06', agent_name='DIMPHO', commit=True)
        sub = CommissionSubmission.objects.get(agent__name='DIMPHO', period_label='2026-06')
        approve_through_chain(sub, User.objects.create_user('dimpho', 'dimpho@x.co', 'pw'))
        self.assertEqual(sub.status, CommissionSubmission.Status.APPROVED)
        with self.assertRaises(ValueError):           # approved → refuse re-import
            import_workbook(p, 'bdu', '2026-06', agent_name='DIMPHO', commit=True)

    def test_dec_handles_accounting_negatives_and_spaces(self):
        from .importer import _dec
        self.assertEqual(_dec('(1,234.56)'), Decimal('-1234.56'))
        self.assertEqual(_dec('1 234.56'), Decimal('1234.56'))
        self.assertEqual(_dec('N/A'), Decimal('0.00'))


@submission_window_open   # 16th submission deadline (2026-08-28)
class FixesTests(TestCase):
    """Coverage for the Fable-review fixes."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.indep = CommissionGroup.objects.get(key='independent')
        self.agent = CommissionAgent.objects.create(name='Fix Agent', group=self.indep,
                                                    email='fix@x.co')
        self.user = User.objects.create_user('fix', 'fix@x.co', 'pw')
        self.mgr = User.objects.create_user('m', 'm@x.co', 'pw', is_superuser=True)

    def test_create_without_agent_binds_self(self):
        view = CommissionSubmissionViewSet.as_view({'post': 'create'})
        req = self.factory.post('/x', {'period_label': '2026-06', 'lines': []}, format='json')
        force_authenticate(req, user=self.user)          # no 'agent' in the body
        resp = view(req)
        self.assertEqual(resp.status_code, 201, resp.data)
        sub = CommissionSubmission.objects.get(period_label='2026-06')
        self.assertEqual(sub.agent, self.agent)

    def test_duplicate_create_returns_400(self):
        view = CommissionSubmissionViewSet.as_view({'post': 'create'})

        def mk():
            req = self.factory.post('/x', {'period_label': '2026-08', 'lines': []}, format='json')
            force_authenticate(req, user=self.user)
            return view(req)
        self.assertEqual(mk().status_code, 201)
        self.assertEqual(mk().status_code, 400)   # unique (agent, period) → clean 400

    def test_cannot_delete_approved_but_can_delete_draft(self):
        draft = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-06')
        view = CommissionSubmissionViewSet.as_view({'delete': 'destroy'})
        req = self.factory.delete('/x'); force_authenticate(req, user=self.user)
        self.assertEqual(view(req, pk=str(draft.id)).status_code, 204)
        appr = CommissionSubmission.objects.create(agent=self.agent, group=self.indep,
                                                   period_label='2026-07',
                                                   status=CommissionSubmission.Status.APPROVED)
        req2 = self.factory.delete('/x'); force_authenticate(req2, user=self.user)
        self.assertEqual(view(req2, pk=str(appr.id)).status_code, 403)
        self.assertTrue(CommissionSubmission.objects.filter(id=appr.id).exists())

    def test_form_encoded_false_does_not_approve(self):
        sub = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-06')
        service.submit(sub, self.user)
        view = CommissionSubmissionViewSet.as_view({'post': 'review'})
        req = self.factory.post('/x', {'approve': 'false', 'note': 'no'})  # form-encoded string
        force_authenticate(req, user=stage1_user())   # a 1st-stage reviewer
        resp = view(req, pk=str(sub.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.REJECTED)

    def test_amendment_recorded_on_edit(self):
        """Editing a draft line writes ONE CommissionAmendment (old→new + note)."""
        from .models import CommissionAmendment, CommissionSubmissionLine
        draft = CommissionSubmission.objects.create(agent=self.agent, group=self.indep,
                                                    period_label='2026-09')
        CommissionSubmissionLine.objects.create(submission=draft, policy_number='P1',
            client_name='ACME', commission_amount=Decimal('100.00'))
        service.recompute_submission(draft)
        view = CommissionSubmissionViewSet.as_view({'patch': 'partial_update'})
        body = {'lines': [{'policy_number': 'P1', 'client_name': 'ACME',
                           'transaction_type': 'new_business', 'amount_collected': '2000',
                           'commission_rate': '5', 'commission_amount': '150.00'}]}
        req = self.factory.patch('/x', body, format='json')
        force_authenticate(req, user=self.user)
        resp = view(req, pk=str(draft.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        amds = CommissionAmendment.objects.filter(submission=draft)
        self.assertEqual(amds.count(), 1)
        a = amds.first()
        self.assertEqual(a.old_gross, Decimal('100.00'))
        self.assertEqual(a.new_gross, Decimal('150.00'))
        self.assertEqual(a.actor, self.user)
        self.assertTrue(a.note)            # deterministic fallback note present
        # A no-op save must NOT create a second amendment.
        req2 = self.factory.patch('/x', body, format='json')
        force_authenticate(req2, user=self.user)
        self.assertEqual(view(req2, pk=str(draft.id)).status_code, 200)
        self.assertEqual(CommissionAmendment.objects.filter(submission=draft).count(), 1)

    def test_cannot_reassign_agent_on_update(self):
        other = CommissionAgent.objects.create(name='Other', group=self.indep)
        draft = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-06')
        view = CommissionSubmissionViewSet.as_view({'patch': 'partial_update'})
        req = self.factory.patch('/x', {'agent': str(other.id)}, format='json')
        force_authenticate(req, user=self.user)
        view(req, pk=str(draft.id))
        draft.refresh_from_db()
        self.assertEqual(draft.agent, self.agent)   # agent unchanged

    def test_period_label_validated(self):
        from .serializers import CommissionSubmissionSerializer
        s = CommissionSubmissionSerializer(data={'period_label': '2026-6', 'lines': []})
        self.assertFalse(s.is_valid())
        self.assertIn('period_label', s.errors)

    def test_negative_net_goes_to_held(self):
        agent = CommissionAgent.objects.create(name='Clawback', group=self.indep)
        CommissionBankAccount.objects.create(agent=agent, account_number='123')
        sub = CommissionSubmission.objects.create(agent=agent, group=self.indep,
                                                  period_label='2026-06',
                                                  status=CommissionSubmission.Status.APPROVED)
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('-500'))
        service.recompute_submission(sub)
        ready, held = service.payout_rows(self.indep, '2026-06')
        self.assertEqual(len(ready), 0)
        self.assertEqual(len(held), 1)
        self.assertIn('negative', held[0]['hold_reason'].lower())

    def test_csv_formula_injection_neutralised(self):
        agent = CommissionAgent.objects.create(name='=cmd|calc', group=self.indep)
        CommissionBankAccount.objects.create(agent=agent, account_number='999')
        sub = CommissionSubmission.objects.create(agent=agent, group=self.indep,
                                                  period_label='2026-06',
                                                  status=CommissionSubmission.Status.APPROVED)
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('100'))
        service.recompute_submission(sub)
        csv_text, _ = service.export_payout_csv(self.indep, '2026-06')
        self.assertNotIn('\n=cmd', csv_text)      # not a raw leading-= cell
        self.assertIn("'=cmd|calc", csv_text)     # neutralised with a leading quote


class PayrollStageTests(TestCase):
    """Payroll processing after final approval + the notify-out step."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.indep = CommissionGroup.objects.get(key='independent')
        self.agent = CommissionAgent.objects.create(name='Payroll Agent', group=self.indep)
        self.payroll = User.objects.create_user('pay', 'pay@x.co', 'pw', is_superuser=True)

    def _approved(self, period='2026-06'):
        sub = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label=period)
        CommissionSubmissionLine.objects.create(submission=sub, commission_amount=Decimal('1000'))
        approve_through_chain(sub, User.objects.create_user(f'sub{period}', f'sub{period}@x.co', 'pw'))
        return sub

    def test_mark_processed_after_approval(self):
        sub = self._approved()
        self.assertEqual(sub.status, CommissionSubmission.Status.APPROVED)
        service.mark_processed(sub, self.payroll)
        self.assertEqual(sub.status, CommissionSubmission.Status.PAID)
        self.assertEqual(sub.paid_by, self.payroll)

    def test_cannot_process_before_approval(self):
        sub = CommissionSubmission.objects.create(agent=self.agent, group=self.indep, period_label='2026-07')
        service.submit(sub, User.objects.create_user('s7', 's7@x.co', 'pw'))
        with self.assertRaises(ValueError):
            service.mark_processed(sub, self.payroll)

    def test_notify_sends_to_the_full_access_roster_without_env_config(self):
        """CFO 2026-09-08: the 5 full-access people are stage-1 recipients
        regardless of COMMISSIONS_STAGE1_EMAILS — nothing else is set here
        and it still sends."""
        from .access import _FULL_ACCESS_EMAILS
        from .notify import _body_html, notify_payroll_done
        sub = self._approved()
        result = notify_payroll_done(sub)
        self.assertEqual(result['sent'], 1)
        self.assertEqual(set(result['to']), _FULL_ACCESS_EMAILS)
        body = _body_html(sub)
        self.assertIn('ALPHA DIRECT', body)
        self.assertIn('Payroll Agent', body)

    def test_api_mark_processed_is_payroll_gated(self):
        sub = self._approved()
        view = CommissionSubmissionViewSet.as_view({'post': 'mark_processed'})
        req = self.factory.post('/x')
        force_authenticate(req, user=User.objects.create_user('nope', 'nope@x.co', 'pw'))
        self.assertEqual(view(req, pk=str(sub.id)).status_code, 403)   # not payroll
        req2 = self.factory.post('/x')
        force_authenticate(req2, user=self.payroll)
        self.assertEqual(view(req2, pk=str(sub.id)).status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, CommissionSubmission.Status.PAID)


@submission_window_open   # 16th submission deadline (2026-08-28)
class UploadApiTests(TestCase):
    """The in-app workbook upload (seamless load, no retyping)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.indep = CommissionGroup.objects.get(key='independent')
        self.agent = CommissionAgent.objects.create(name='Upload Agent', group=self.indep, email='up@x.co')
        self.user = User.objects.create_user('up', 'up@x.co', 'pw')

    def _wb_bytes(self):
        import io
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Data'
        ws.append(['Policy Number', 'Policy Name',
                   'New Business / Renewal / Endorsement/ Previous Month', 'Annual / Monthly',
                   'Amount Collected during this period (P)', 'Annualised Premium', 'Commission Rate',
                   'Collection Date', 'x', 'Is the Policy closed in the system',
                   'Amount Applicable for Commission (P)', 'Commissions  (P)', 'Reason'])
        ws.append(['P1', 'C', 'New Business', 'Annual', 1000, 1200, 15, '2026-06-10', '', 'Yes', 1000, 150, ''])
        b = io.BytesIO(); wb.save(b); return b.getvalue()

    def _upload(self, user, fname='Upload Agent COMMISSION - JUNE 2026.xlsx', body=None, period='2026-06'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        up = SimpleUploadedFile(fname, body if body is not None else self._wb_bytes(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        view = CommissionSubmissionViewSet.as_view({'post': 'upload'})
        req = self.factory.post('/x', {'file': up, 'period': period}, format='multipart')
        force_authenticate(req, user=user)
        return view(req)

    def test_agent_uploads_own_sheet(self):
        resp = self._upload(self.user)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        sub = CommissionSubmission.objects.get(agent=self.agent, period_label='2026-06')
        self.assertEqual(sub.lines.count(), 1)
        self.assertEqual(sub.gross_commission, Decimal('150.00'))
        self.assertEqual(sub.net_payable, Decimal('135.00'))   # independent 10%

    def test_upload_rejects_non_excel(self):
        self.assertEqual(self._upload(self.user, fname='x.txt', body=b'nope').status_code, 400)

    def test_upload_rejects_old_xls_with_hint(self):
        """.xls has no installed reader — must 400 with a save-as-.xlsx hint,
        never fall through to openpyxl and 500 (Bokani, 2026-07-18)."""
        resp = self._upload(self.user, fname='JULY COMMISSION.xls', body=b'\xd0\xcf\x11\xe0old')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('.xlsx', resp.data['detail'])

    def test_upload_corrupt_xlsx_is_400_not_500(self):
        """A file that crashes the parser must return a friendly 400, not a
        blank server error."""
        resp = self._upload(self.user, fname='broken.xlsx', body=b'not a zip at all')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('detail', resp.data)

    def test_upload_blocked_when_no_linked_agent(self):
        stranger = User.objects.create_user('noag', 'noag@x.co', 'pw')
        self.assertEqual(self._upload(stranger).status_code, 403)

    def test_preview_reads_rows_without_writing(self):
        """preview=1 → parse + return the rows, create NOTHING."""
        before = CommissionSubmission.objects.count()
        resp = self._upload(self.user, body=None)  # not preview yet — build the request by hand
        # rebuild as a preview request
        from django.core.files.uploadedfile import SimpleUploadedFile
        up = SimpleUploadedFile('Upload Agent COMMISSION - JUNE 2026.xlsx', self._wb_bytes(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        view = CommissionSubmissionViewSet.as_view({'post': 'upload'})
        req = self.factory.post('/x', {'file': up, 'period': '2026-07', 'preview': '1'}, format='multipart')
        force_authenticate(req, user=self.user)
        r = view(req)
        self.assertEqual(r.status_code, 200, getattr(r, 'data', None))
        self.assertEqual(r.data['mode'], 'policy')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['rows'][0]['client_name'], 'C')
        # the real upload above (2026-06) wrote one; the preview (2026-07) wrote nothing
        self.assertFalse(CommissionSubmission.objects.filter(period_label='2026-07').exists())
        self.assertEqual(CommissionSubmission.objects.count(), before + 1)

    def test_reviewer_loads_inhouse_summary_into_review(self):
        """The 'Load agent commission sheets' panel: a reviewer uploads the in-house
        summary (inhouse=1) and the agents drop straight into the queue (submit=1)."""
        import io
        import openpyxl
        from django.core.files.uploadedfile import SimpleUploadedFile
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'JUNE SUMMARY'
        ws.append(['AGENT NAME', 'GROSS COMMISSION JUNE'])
        ws.append(['House One', 400]); ws.append(['House Two', 600])
        b = io.BytesIO(); wb.save(b)
        reviewer = User.objects.create_user('cfo', 'pganesharajah@alphadirect.co.bw', 'pw')  # FINAL roster
        up = SimpleUploadedFile('inhouse.xlsx', b.getvalue(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        view = CommissionSubmissionViewSet.as_view({'post': 'upload'})
        req = self.factory.post('/x', {'file': up, 'period': '2026-06', 'inhouse': '1', 'submit': '1'},
                                format='multipart')
        force_authenticate(req, user=reviewer)
        resp = view(req)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertEqual(resp.data['agents'], 2)
        subs = CommissionSubmission.objects.filter(period_label='2026-06', group__key='in_house')
        self.assertEqual(subs.count(), 2)
        # submit=1 → into the review chain, not left as a draft
        self.assertTrue(all(s.status == CommissionSubmission.Status.SUBMITTED for s in subs))
        self.assertEqual(subs.get(agent__name='House One').gross_commission, Decimal('400.00'))


class InhouseImportTests(TestCase):
    """In-house summary import — one submission per agent = their month's gross."""

    def _wb(self, path):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'JUNE SUMMARY'
        ws.append(['AGENT NAME', 'GROSS COMMISSION MAY', 'GROSS COMMISSION JUNE', 'Change'])
        ws.append(['Agent One', 100, 250, 150])
        ws.append(['Agent Two', 50, 75, 25])
        ws.append(['TOTAL', 150, 325, 175])
        wb.save(path)

    def test_inhouse_loads_period_month_gross_per_agent(self):
        import os
        import tempfile
        from commissions.importer import import_inhouse
        p = os.path.join(tempfile.mkdtemp(), 'IN-HOUSE.xlsx')
        self._wb(p)
        r = import_inhouse(p, '2026-06', commit=True)
        self.assertEqual(r['agents'], 2)               # TOTAL row skipped
        s = CommissionSubmission.objects.get(agent__name='Agent One', period_label='2026-06')
        self.assertEqual(s.gross_commission, Decimal('250.00'))   # JUNE column, not MAY (100)
        self.assertEqual(s.group.key, 'in_house')
        self.assertEqual(s.lines.count(), 1)
