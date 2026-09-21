"""
petty_cash/tests.py

Regression guardrails for the imprest petty-cash module. Added 2026-07-15
after a forensic audit found the module was LIVE-capable but completely
untested — every control ("SoD is enforced", "the float can't go negative",
"the JE carries the company") lived in exactly one place with nothing guarding
it. These tests are the CI gate: if a future change reopens one of these holes
the build fails before it reaches prod.

Covers the fixes made in the same audit:
  * voucher approval posts a balanced DR-expense / CR-petty-cash JE stamped
    with the location's company (was company=NULL — which bypassed the ADIC
    FY lock and never rolled up into a per-company report);
  * the ADIC historical FY lock now applies to backdated petty-cash vouchers;
  * SoD on approve AND reject (approver/rejecter != creator/submitter);
  * the negative-float guard;
  * expense-account classification re-checked at approval, not just create;
  * reimbursement sweeps, locks vouchers into REIMBURSED, and REIMBURSED is
    immutable; posted vouchers cannot be deleted;
  * API tenant isolation (preview / reimbursement-create scoped to the caller's
    companies) and the positive maker-title gate on voucher entry.
"""
from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from core.models import Company, Currency, UserCompanyAccess, UserProfile
from ledger.models import Account, FiscalPeriod, JournalEntry
from petty_cash import services
from petty_cash.models import PettyCashReimbursement, PettyCashVoucher

ZERO = Decimal('0.00')
TODAY = date.today()


def _user(username, title, superuser=False, email=''):
    u = User.objects.create_user(username=username, password='x', is_superuser=superuser, email=email)
    UserProfile.objects.update_or_create(
        user=u,
        defaults={'role': UserProfile.Role.ACCOUNTANT, 'title': title, 'is_active': True},
    )
    return u


class _World:
    """Shared fixture builder — accounts, companies, open periods, a tin."""

    @classmethod
    def build(cls, ns):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        ns.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        ns.oth = Company.objects.create(code='OTH', name='Other Entity')
        # Wide OPEN periods so both today and the FY26-9M lock window are
        # covered — in the lock test we want the LOCK to be the blocker, not a
        # missing period.
        for co in (ns.adic, ns.oth):
            FiscalPeriod.objects.create(
                company=co, period_name=f'{co.code}-wide',
                start_date=date(2024, 1, 1), end_date=date(2027, 12, 31),
                status=FiscalPeriod.Status.OPEN,
            )
        ns.exp = Account.objects.create(code='6100', name='Sundry expense',
                                        account_type='expense', sub_type='test', is_active=True)
        ns.exp2 = Account.objects.create(code='6200', name='Staff refreshments',
                                         account_type='expense', sub_type='test', is_active=True)
        ns.nonexp = Account.objects.create(code='2100', name='Trade payables',
                                           account_type='liability', sub_type='test', is_active=True)
        ns.petty = Account.objects.create(code='1160', name='Petty cash',
                                          account_type='asset', sub_type='bank', is_active=True)
        ns.bank = Account.objects.create(code='1110', name='FNB BWP',
                                         account_type='asset', sub_type='bank', is_active=True)
        from petty_cash.models import PettyCashLocation
        ns.loc = PettyCashLocation.objects.create(
            name='Test Tin', company=ns.adic, float_amount=Decimal('15000.00'),
            petty_cash_account=ns.petty, reimbursing_bank_account=ns.bank, is_active=True,
        )


class PettyCashServiceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        _World.build(cls)
        cls.maker = _user('pc_acc', UserProfile.Title.ACCOUNTANT)
        cls.maker2 = _user('pc_sacc', UserProfile.Title.SENIOR_ACCOUNTANT)
        # The designated petty-cash middle reviewer (Kago). Authority is by the
        # named allowlist now, not the FM title — so the email must match.
        cls.fm = _user('ktshutlhedi', UserProfile.Title.FINANCE_MANAGER,
                       email='ktshutlhedi@alphadirect.co.bw')
        # A finance_manager who is NOT on the petty-cash reviewer list.
        cls.fm_other = _user('pc_fm_other', UserProfile.Title.FINANCE_MANAGER,
                             email='lntabeni@alphadirect.co.bw')
        cls.cfo = _user('pc_cfo', UserProfile.Title.CFO)
        # CFO directive 2026-08-03: petty-cash vouchers are signed by Keetile /
        # Pako / Legakwa / Tlamelo — a NAMED set, no longer "any finance title".
        # The CFO is deliberately NOT a routine signer (superuser break-glass only).
        cls.signer1 = _user('keetile.mokhendo', UserProfile.Title.SENIOR_ACCOUNTANT,
                            email='kmokhendo@alphadirect.co.bw')
        cls.signer2 = _user('pkago', UserProfile.Title.FINANCIAL_CONTROLLER,
                            email='pkago@alphadirect.co.bw')

    def _draft(self, *, creator=None, amount='250.00', account=None, when=None):
        return services.create_voucher(
            location=self.loc,
            voucher_date=when or TODAY,
            payee='Kgomotso',
            amount=Decimal(amount),
            expense_account=account or self.exp,
            description='Taxi to BURS office',
            user=creator or self.maker,
        )

    def _post_voucher(self, v, first=None, second=None):
        """Dual sign-off (CFO 2026-07-16): TWO distinct eligible signatures post
        a voucher. Defaults to two of the four NAMED signers (CFO 2026-08-03) —
        both differ from the default maker/submitter."""
        services.approve_voucher(v, first or self.signer1)
        services.approve_voucher(v, second or self.signer2)
        return v

    # ---- posting + company stamp --------------------------------------

    def test_approve_posts_balanced_je_stamped_with_company(self):
        v = self._draft()
        services.submit_voucher(v, self.maker)
        self._post_voucher(v)   # two distinct signatures post the JE
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        je = v.journal_entry
        self.assertIsNotNone(je)
        # The whole point of the fix: the JE belongs to the location's entity.
        self.assertEqual(je.company_id, self.adic.id)
        self.assertEqual(je.status, JournalEntry.Status.POSTED)
        lines = {l.account.code: l for l in je.lines.all()}
        self.assertEqual(lines['6100'].debit_amount, Decimal('250.00'))
        self.assertEqual(lines['1160'].credit_amount, Decimal('250.00'))
        tot_d = sum(l.debit_amount for l in je.lines.all())
        tot_c = sum(l.credit_amount for l in je.lines.all())
        self.assertEqual(tot_d, tot_c)

    def test_backdated_adic_voucher_blocked_by_fy_lock(self):
        # A voucher dated inside FY26-9M (locked) must not post to ADIC without
        # the override — this only works because the JE now carries company=ADIC.
        v = self._draft(when=date(2026, 1, 15))
        services.submit_voucher(v, self.maker)
        # First signature only moves it to one-signature — no JE yet.
        services.approve_voucher(v, self.signer1)
        # The second signature is what posts the JE, so that is where the
        # ADIC FY lock bites.
        with self.assertRaises(ValidationError) as ctx:
            services.approve_voucher(v, self.signer2)
        self.assertIn('LOCKED', str(ctx.exception))
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.ONE_SIGNATURE)

    # ---- segregation of duties ----------------------------------------

    def test_approver_cannot_be_creator_or_submitter(self):
        v = self._draft(creator=self.maker)
        services.submit_voucher(v, self.maker)
        # maker created + submitted → cannot approve
        with self.assertRaises(ValidationError):
            services.approve_voucher(v, self.maker)
        # two eligible signers, neither the creator/submitter, post it
        self._post_voucher(v)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)

    def test_reject_requires_sod(self):
        v = self._draft(creator=self.maker)
        services.submit_voucher(v, self.maker)
        with self.assertRaises(ValidationError):
            services.reject_voucher(v, self.maker, 'mine')   # creator/submitter
        services.reject_voucher(v, self.signer1, 'no receipt')
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.REJECTED)

    def test_non_approver_cannot_approve(self):
        # CFO 2026-08-03: the signer pool is the four NAMED people, not a title.
        # Everyone below holds a finance-ish title yet must now be refused —
        # this is the regression guard for the 46-person over-grant (shared
        # mailboxes, service accounts and an outside address all carried
        # `accountant`/`cfo` titles and could sign a voucher).
        outsider = _user('pc_ops', UserProfile.Title.OPERATIONS)
        plain_accountant = _user('pc_any_acc', UserProfile.Title.ACCOUNTANT,
                                 email='health@alphadirect.co.bw')
        titled_cfo = _user('pc_titled_cfo', UserProfile.Title.CFO,
                           email='someone.else@alphadirect.co.bw')
        unnamed_fm = _user('pc_unnamed_fm', UserProfile.Title.FINANCE_MANAGER,
                           email='omogomotsi@alphadirect.co.bw')
        v = self._draft(creator=self.maker)
        services.submit_voucher(v, self.maker)
        for who in (outsider, plain_accountant, titled_cfo, unnamed_fm):
            with self.assertRaises(ValidationError):
                services.approve_voucher(v, who)

    def test_lefika_is_a_named_signer(self):
        # CFO approved 2026-08-07 on Keetile's request — Lefika takes the tin, and in
        # this module the custodian set IS the signer set.
        lefika = _user('lefika.basotli', UserProfile.Title.ACCOUNTANT,
                       email='lbasotli@alphadirect.co.bw')
        v = self._draft()
        services.submit_voucher(v, self.maker)
        services.approve_voucher(v, lefika)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.ONE_SIGNATURE)
        self.assertEqual(v.first_approved_by_id, lefika.id)

    def test_kago_can_sign_a_voucher(self):
        # CFO 2026-08-07: "give the approving powers to Pako, Kago, Keetile."
        # Kago was the reimbursement reviewer only and could not sign vouchers.
        kago = _user('ktshutlhedi2', UserProfile.Title.FINANCE_MANAGER,
                     email='ktshutlhedi@alphadirect.co.bw')
        v = self._draft()
        services.submit_voucher(v, self.maker)
        services.approve_voucher(v, kago)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.ONE_SIGNATURE)
        self.assertEqual(v.first_approved_by_id, kago.id)

    def test_cfo_gets_no_task_for_a_voucher(self):
        # The dashboard flood: the CFO was raised an FYI task for EVERY voucher.
        from core.models import OmniTask
        cfo = _user('pganesharajah', UserProfile.Title.CFO,
                    email='pganesharajah@alphadirect.co.bw')
        v = self._draft()
        services.submit_voucher(v, self.maker)
        self.assertEqual(
            OmniTask.objects.filter(assignee=cfo, title__icontains=v.voucher_number).count(),
            0,
            'the CFO must not be raised a task for an individual voucher')
        # the handlers still get theirs
        self.assertTrue(
            OmniTask.objects.filter(title__icontains=v.voucher_number).exists(),
            'the petty-cash handlers must still be tasked')

    def test_named_signers_are_the_pool(self):
        # The four the CFO named can sign; the routine-approver check (which
        # drives the My-Approvals inbox) is true for them and false for the CFO.
        from petty_cash.services import _can_approve, is_routine_approver
        for who in (self.signer1, self.signer2):
            self.assertTrue(_can_approve(who), who.username)
            self.assertTrue(is_routine_approver(who), who.username)
        cfo_titled = _user('pc_inbox_cfo', UserProfile.Title.CFO,
                           email='pganesharajah@alphadirect.co.bw')
        self.assertFalse(is_routine_approver(cfo_titled),
                         'petty cash must not appear on the CFO dashboard')

    def test_superuser_break_glass_still_signs(self):
        # He is out of the ROUTINE pool but must not be locked out entirely.
        su = _user('pc_su', UserProfile.Title.CFO, email='su@alphadirect.co.bw')
        su.is_superuser = True
        su.save(update_fields=['is_superuser'])
        from petty_cash.services import _can_approve, is_routine_approver
        self.assertTrue(_can_approve(su))
        self.assertFalse(is_routine_approver(su))

    # ---- money guards --------------------------------------------------

    def test_submit_blocks_when_over_float(self):
        v = self._draft(amount='20000.00')   # > P15,000 float
        with self.assertRaises(ValidationError):
            services.submit_voucher(v, self.maker)

    def test_non_expense_account_rejected_on_create(self):
        with self.assertRaises(ValidationError):
            self._draft(account=self.nonexp)

    def test_expense_account_revalidated_at_approval(self):
        v = self._draft(account=self.exp)
        services.submit_voucher(v, self.maker)
        # simulate a draft edited to point at a non-expense account after create
        v.expense_account = self.nonexp
        v.save(audit_user=self.maker)
        with self.assertRaises(ValidationError):
            services.approve_voucher(v, self.signer1)

    # ---- immutability --------------------------------------------------

    def test_posted_voucher_cannot_be_deleted(self):
        v = self._draft()
        services.submit_voucher(v, self.maker)
        self._post_voucher(v)
        v.refresh_from_db()
        with self.assertRaises(ValidationError):
            v.delete()

    # ---- correcting a POSTED voucher (CFO 2026-08-07) -------------------
    #
    # The window Keetile asked for: a voucher signed twice but not yet paid out
    # must still be fixable, and the GL must follow the fix rather than be
    # edited behind. It shuts the moment the replenishment pays it.

    def _posted(self, **kw):
        v = self._draft(**kw)
        services.submit_voucher(v, self.maker)
        self._post_voucher(v)
        v.refresh_from_db()
        return v

    def test_amend_posted_reverses_old_je_and_posts_corrected_one(self):
        v = self._posted(amount='250.00')
        old_je = v.journal_entry
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('180.00'), reason='Till slip says 180, not 250.')
        v.refresh_from_db()
        old_je.refresh_from_db()

        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        self.assertEqual(v.amount, Decimal('180.00'))
        self.assertEqual(v.original_amount, Decimal('250.00'))
        self.assertEqual(old_je.status, JournalEntry.Status.REVERSED)
        # A fresh entry carries the corrected figure — the voucher points at it,
        # not at the reversed one.
        self.assertNotEqual(v.journal_entry_id, old_je.id)
        self.assertEqual(v.journal_entry.status, JournalEntry.Status.POSTED)
        self.assertEqual(v.journal_entry.company_id, self.adic.id)
        lines = {l.account.code: l for l in v.journal_entry.lines.all()}
        self.assertEqual(lines['6100'].debit_amount, Decimal('180.00'))
        self.assertEqual(lines['1160'].credit_amount, Decimal('180.00'))
        # What the ledger actually shows for this expense once all three entries
        # are in: 250 out, 250 back, 180 out = 180. A reversal does not unpost the
        # original — its lines stay in the GL — so both are counted here.
        entries = JournalEntry.objects.filter(
            models.Q(source_type='petty_cash_voucher', source_id=v.pk)
            | models.Q(reversal_of=old_je)
        ).distinct()
        net = sum(
            (l.debit_amount - l.credit_amount
             for je in entries for l in je.lines.all() if l.account_id == self.exp.id),
            ZERO,
        )
        self.assertEqual(net, Decimal('180.00'))

    def test_amend_posted_carries_company_onto_the_reversal(self):
        # An entity-less entry drops out of every per-company report, and
        # JournalEntry.reverse() does not copy company across.
        v = self._posted(amount='250.00')
        old_je = v.journal_entry
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('100.00'), reason='Corrected against the slip.')
        old_je.refresh_from_db()
        self.assertEqual(old_je.reversed_by.company_id, self.adic.id)

    def test_reversal_lands_in_the_same_period_as_the_entry_it_reverses(self):
        # A correction made in a later month must not leave the expense showing in
        # full in one month and negative in the next — right in total, wrong in both
        # months' management accounts.
        # June 2026 — a month back, but clear of the ADIC FY26-9M historical lock
        # (Jul 2025 – Mar 2026), which now correctly bites on reversals too.
        v = self._posted(amount='250.00', when=date(2026, 6, 15))
        old_je = v.journal_entry
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('180.00'), reason='Corrected later.')
        old_je.refresh_from_db()
        self.assertEqual(old_je.reversed_by.entry_date, old_je.entry_date)

    def test_amend_posted_can_move_the_gl_account(self):
        v = self._posted(amount='250.00')
        services.amend_posted_voucher(
            v, self.signer1, expense_account=self.exp2, reason='Refreshments, not sundry.')
        v.refresh_from_db()
        self.assertEqual(v.expense_account_id, self.exp2.id)
        self.assertEqual(v.original_expense_account_id, self.exp.id)
        codes = {l.account.code for l in v.journal_entry.lines.all()}
        self.assertEqual(codes, {'6200', '1160'})

    def test_amend_posted_requires_a_reason(self):
        v = self._posted()
        with self.assertRaises(ValidationError):
            services.amend_posted_voucher(v, self.signer1, amount=Decimal('10.00'), reason='  ')

    def test_amend_posted_rejects_a_non_custodian(self):
        v = self._posted()
        outsider = _user('pc_outsider', UserProfile.Title.AUDITOR)
        with self.assertRaises(ValidationError):
            services.amend_posted_voucher(
                v, outsider, amount=Decimal('10.00'), reason='Trying it on.')

    def test_amend_posted_blocks_an_increase_the_tin_cannot_fund(self):
        v = self._posted(amount='250.00')
        with self.assertRaises(ValidationError):
            services.amend_posted_voucher(
                v, self.signer1, amount=Decimal('99999.00'), reason='Way over the float.')
        v.refresh_from_db()
        self.assertEqual(v.amount, Decimal('250.00'))

    def test_amend_posted_allows_an_increase_the_tin_can_fund(self):
        # The old figure is already out of the tin, so it must be added back
        # before the new one is tested — otherwise 50 -> 60 is judged as 110.
        v = self._posted(amount='250.00')
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('300.00'), reason='Slip was 300.')
        v.refresh_from_db()
        self.assertEqual(v.amount, Decimal('300.00'))

    def test_amend_posted_is_refused_once_paid(self):
        # The line the CFO drew: after the replenishment pays it, nobody amends it.
        v1, _ = self._post_two_vouchers()
        self._full_chain()
        v1.refresh_from_db()
        self.assertEqual(v1.status, PettyCashVoucher.Status.REIMBURSED)
        with self.assertRaises(ValidationError):
            services.amend_posted_voucher(
                v1, self.signer1, amount=Decimal('1.00'), reason='Too late.')
        v1.refresh_from_db()
        self.assertEqual(v1.amount, Decimal('100.00'))

    def test_amend_posted_retotals_a_replenishment_still_in_flight(self):
        # Otherwise the CFO approves a figure on screen that changes when posted.
        v1, _v2 = self._post_two_vouchers()          # 100 + 200 = 300
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        services.fm_review_reimbursement(reimb, self.fm)
        services.amend_posted_voucher(
            v1, self.signer1, amount=Decimal('50.00'), reason='Slip was 50.')
        reimb.refresh_from_db()
        self.assertEqual(reimb.total_amount, Decimal('250.00'))
        self.assertEqual(reimb.voucher_count, 2)
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.PENDING_CFO)

    def test_second_correction_reverses_the_correction_not_the_original(self):
        v = self._posted(amount='250.00')
        first_je = v.journal_entry
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('180.00'), reason='First fix.')
        v.refresh_from_db()
        second_je = v.journal_entry
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('160.00'), reason='Second fix.')
        v.refresh_from_db()
        second_je.refresh_from_db()
        first_je.refresh_from_db()
        self.assertEqual(first_je.status, JournalEntry.Status.REVERSED)
        self.assertEqual(second_je.status, JournalEntry.Status.REVERSED)
        self.assertEqual(v.journal_entry.status, JournalEntry.Status.POSTED)
        self.assertEqual(v.amount, Decimal('160.00'))
        # The FIRST figure is what the requester asked for — it must survive
        # every later correction.
        self.assertEqual(v.original_amount, Decimal('250.00'))

    def test_amend_posted_records_who_and_why(self):
        v = self._posted()
        services.amend_posted_voucher(
            v, self.signer1, amount=Decimal('11.00'), reason='Receipt reads 11.00.')
        v.refresh_from_db()
        self.assertEqual(v.amended_by_id, self.signer1.id)
        self.assertIsNotNone(v.amended_at)
        self.assertIn('11.00', v.amend_reason)

    # ---- returning a POSTED voucher to draft (CFO 2026-08-07) ----------

    def test_return_to_draft_reverses_the_je_and_clears_signatures(self):
        v = self._posted(amount='250.00')
        old_je = v.journal_entry
        services.unpost_voucher(v, self.signer1, reason='Rejected by Mr B — cash never given.')
        v.refresh_from_db()
        old_je.refresh_from_db()

        self.assertEqual(v.status, PettyCashVoucher.Status.DRAFT)
        self.assertIsNone(v.journal_entry_id)
        self.assertIsNone(v.first_approved_by_id)
        self.assertIsNone(v.approved_by_id)
        self.assertIsNone(v.submitted_by_id)
        self.assertEqual(old_je.status, JournalEntry.Status.REVERSED)
        self.assertEqual(old_je.reversed_by.company_id, self.adic.id)
        # Expense and tin both back where they started.
        net = sum(
            (l.debit_amount - l.credit_amount
             for je in (old_je, old_je.reversed_by) for l in je.lines.all()
             if l.account_id == self.exp.id),
            ZERO,
        )
        self.assertEqual(net, ZERO)

    def test_returned_voucher_can_be_edited_and_re_posted(self):
        # The whole point: back to draft means every field is editable again,
        # and two fresh signatures put it back in the GL.
        v = self._posted(amount='250.00')
        services.unpost_voucher(v, self.signer1, reason='Wrong payee and amount.')
        v.refresh_from_db()
        v.payee = 'Sechele'
        v.amount = Decimal('260.00')
        v.save(audit_user=self.maker)
        services.submit_voucher(v, self.maker)
        self._post_voucher(v)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        self.assertEqual(v.payee, 'Sechele')
        lines = {l.account.code: l for l in v.journal_entry.lines.all()}
        self.assertEqual(lines['6100'].debit_amount, Decimal('260.00'))

    def test_return_to_draft_requires_a_reason_and_a_custodian(self):
        v = self._posted()
        with self.assertRaises(ValidationError):
            services.unpost_voucher(v, self.signer1, reason='   ')
        outsider = _user('pc_outsider2', UserProfile.Title.AUDITOR)
        with self.assertRaises(ValidationError):
            services.unpost_voucher(v, outsider, reason='Not my job.')
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)

    def test_return_to_draft_is_refused_once_paid(self):
        v1, _ = self._post_two_vouchers()
        self._full_chain()
        v1.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.unpost_voucher(v1, self.signer1, reason='Too late.')
        v1.refresh_from_db()
        self.assertEqual(v1.status, PettyCashVoucher.Status.REIMBURSED)

    def test_return_to_draft_drops_the_voucher_out_of_an_open_replenishment(self):
        v1, _v2 = self._post_two_vouchers()          # 100 + 200 = 300
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        services.unpost_voucher(v1, self.signer1, reason='Cash was not given.')
        reimb.refresh_from_db()
        self.assertEqual(reimb.total_amount, Decimal('200.00'))
        self.assertEqual(reimb.voucher_count, 1)

    # ---- reimbursement -------------------------------------------------

    def _post_two_vouchers(self):
        v1 = self._draft(amount='100.00')
        services.submit_voucher(v1, self.maker)
        self._post_voucher(v1)
        v2 = self._draft(amount='200.00', account=self.exp2)
        services.submit_voucher(v2, self.maker)
        self._post_voucher(v2)
        return v1, v2

    def _full_chain(self, *, creator=None, submitter=None, reviewer=None, poster=None):
        """create -> submit -> FM review -> CFO post, with sensible defaults."""
        creator = creator or self.maker
        submitter = submitter or creator
        reviewer = reviewer or self.fm
        poster = poster or self.cfo
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=creator,
        )
        services.submit_reimbursement(reimb, submitter)
        services.fm_review_reimbursement(reimb, reviewer)
        services.post_reimbursement(reimb, poster)
        return reimb

    def test_reimbursement_chain_sweeps_locks_and_stamps_company(self):
        v1, v2 = self._post_two_vouchers()
        reimb = self._full_chain()
        reimb.refresh_from_db()
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.POSTED)
        self.assertEqual(reimb.total_amount, Decimal('300.00'))
        self.assertEqual(reimb.voucher_count, 2)
        self.assertEqual(reimb.submitted_by_id, self.maker.id)
        self.assertEqual(reimb.fm_reviewed_by_id, self.fm.id)
        self.assertEqual(reimb.posted_by_id, self.cfo.id)
        self.assertEqual(reimb.journal_entry.company_id, self.adic.id)
        lines = {l.account.code: l for l in reimb.journal_entry.lines.all()}
        self.assertEqual(lines['1160'].debit_amount, Decimal('300.00'))
        self.assertEqual(lines['1110'].credit_amount, Decimal('300.00'))
        for v in (v1, v2):
            v.refresh_from_db()
            self.assertEqual(v.status, PettyCashVoucher.Status.REIMBURSED)
            self.assertEqual(v.reimbursement_id, reimb.id)

    def test_cannot_post_before_fm_review(self):
        self._post_two_vouchers()
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        # straight from DRAFT
        with self.assertRaises(ValidationError):
            services.post_reimbursement(reimb, self.cfo)
        services.submit_reimbursement(reimb, self.maker)
        # from PENDING_FM (skipping FM review)
        with self.assertRaises(ValidationError):
            services.post_reimbursement(reimb, self.cfo)

    def test_fm_review_requires_fm_and_sod(self):
        self._post_two_vouchers()
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        # a maker/accountant cannot FM-review
        with self.assertRaises(ValidationError):
            services.fm_review_reimbursement(reimb, self.maker2)
        # an FM who created/submitted it cannot review it either
        own = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.fm,
        )
        services.submit_reimbursement(own, self.fm)
        with self.assertRaises(ValidationError):
            services.fm_review_reimbursement(own, self.fm)

    def test_fm_review_restricted_to_named_reviewers(self):
        self._post_two_vouchers()
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        # a finance_manager who is NOT Kago/Pako cannot review petty cash now
        with self.assertRaises(ValidationError):
            services.fm_review_reimbursement(reimb, self.fm_other)
        # the named reviewer (Kago) can
        services.fm_review_reimbursement(reimb, self.fm)
        reimb.refresh_from_db()
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.PENDING_CFO)

    def test_cfo_post_requires_cfo_and_full_sod(self):
        self._post_two_vouchers()
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        services.fm_review_reimbursement(reimb, self.fm)
        # the FM reviewer is not the CFO -> cannot post
        with self.assertRaises(ValidationError):
            services.post_reimbursement(reimb, self.fm)
        # the CFO posts it
        services.post_reimbursement(reimb, self.cfo)
        reimb.refresh_from_db()
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.POSTED)

    def test_reject_then_reopen(self):
        self._post_two_vouchers()
        reimb = services.create_reimbursement(
            location=self.loc, period_start=date(2020, 1, 1), period_end=TODAY,
            user=self.maker,
        )
        services.submit_reimbursement(reimb, self.maker)
        services.reject_reimbursement(reimb, self.fm, 'need receipts')
        reimb.refresh_from_db()
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.REJECTED)
        # non-creator cannot reopen
        with self.assertRaises(ValidationError):
            services.reopen_reimbursement(reimb, self.fm)
        services.reopen_reimbursement(reimb, self.maker)
        reimb.refresh_from_db()
        self.assertEqual(reimb.status, PettyCashReimbursement.Status.DRAFT)

    def test_reimbursed_voucher_is_immutable(self):
        v1, _ = self._post_two_vouchers()
        self._full_chain()
        v1.refresh_from_db()
        v1.payee = 'tampered'
        with self.assertRaises(ValidationError):
            v1.save(audit_user=self.maker)


class PettyCashApiIsolationTests(APITestCase):
    """Tenant isolation + maker-gate on the entry paths that resolve the
    location themselves (they bypass the mixin's scoped get_object)."""

    @classmethod
    def setUpTestData(cls):
        _World.build(cls)
        cls.accountant = _user('api_acc', UserProfile.Title.ACCOUNTANT)
        cls.auditor = _user('api_aud', UserProfile.Title.AUDITOR)
        cls.outsider = _user('api_out', UserProfile.Title.ACCOUNTANT)
        # accountant + auditor can act on ADIC; outsider only on OTH.
        for u in (cls.accountant, cls.auditor):
            UserCompanyAccess.objects.create(user=u, company=cls.adic, can_view=True, can_write=True)
        UserCompanyAccess.objects.create(user=cls.outsider, company=cls.oth, can_view=True, can_write=True)

    def _voucher_payload(self):
        return {
            'location': str(self.loc.id),
            'voucher_date': str(TODAY),
            'payee': 'Kgomotso',
            'amount': '120.00',
            'expense_account': str(self.exp.id),
            'description': 'Courier',
        }

    # ---- the correction endpoints (CFO 2026-08-07) ---------------------
    #
    # The services are covered above; these prove the ROUTING — that /amend/ on a
    # POSTED voucher reaches amend_posted_voucher rather than the pre-signature
    # path, and that /return-to-draft/ exists and holds the line at payment.

    def _api_posted_voucher(self, amount='250.00'):
        signer1 = _user('api_keetile', UserProfile.Title.SENIOR_ACCOUNTANT,
                        email='kmokhendo@alphadirect.co.bw')
        signer2 = _user('api_pako', UserProfile.Title.FINANCIAL_CONTROLLER,
                        email='pkago@alphadirect.co.bw')
        for u in (signer1, signer2):
            UserCompanyAccess.objects.create(user=u, company=self.adic,
                                             can_view=True, can_write=True)
        v = services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='Kgomotso',
            amount=Decimal(amount), expense_account=self.exp,
            description='Courier', user=self.accountant,
        )
        services.submit_voucher(v, self.accountant)
        services.approve_voucher(v, signer1)
        services.approve_voucher(v, signer2)
        v.refresh_from_db()
        return v, signer1

    def test_amend_endpoint_corrects_a_posted_voucher(self):
        v, signer = self._api_posted_voucher()
        old_je_id = v.journal_entry_id
        self.client.force_authenticate(signer)
        r = self.client.post(
            f'/api/v1/petty-cash-vouchers/{v.id}/amend/',
            {'amount': '180.00', 'reason': 'Till slip says 180.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        v.refresh_from_db()
        self.assertEqual(v.amount, Decimal('180.00'))
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        self.assertNotEqual(v.journal_entry_id, old_je_id)

    def test_return_to_draft_endpoint(self):
        v, signer = self._api_posted_voucher()
        self.client.force_authenticate(signer)
        r = self.client.post(
            f'/api/v1/petty-cash-vouchers/{v.id}/return-to-draft/',
            {'reason': 'Cash was never handed over.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.DRAFT)
        self.assertIsNone(v.journal_entry_id)

    def test_return_to_draft_endpoint_needs_a_reason(self):
        v, signer = self._api_posted_voucher()
        self.client.force_authenticate(signer)
        r = self.client.post(
            f'/api/v1/petty-cash-vouchers/{v.id}/return-to-draft/',
            {'reason': ''}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)

    def test_maker_gate_blocks_read_only_auditor(self):
        self.client.force_authenticate(self.auditor)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._voucher_payload(), format='json')
        self.assertEqual(r.status_code, 403)

    def test_maker_can_create_voucher(self):
        self.client.force_authenticate(self.accountant)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._voucher_payload(), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PettyCashVoucher.objects.count(), 1)

    def test_preview_is_tenant_scoped(self):
        # outsider (OTH only) must not preview the ADIC tin.
        self.client.force_authenticate(self.outsider)
        r = self.client.post('/api/v1/petty-cash-reimbursements/preview/',
                             {'location': str(self.loc.id),
                              'period_start': '2020-01-01', 'period_end': str(TODAY)},
                             format='json')
        self.assertEqual(r.status_code, 404)

    def test_reimbursement_create_is_tenant_scoped(self):
        self.client.force_authenticate(self.outsider)
        r = self.client.post('/api/v1/petty-cash-reimbursements/',
                             {'location': str(self.loc.id),
                              'period_start': '2020-01-01', 'period_end': str(TODAY)},
                             format='json')
        self.assertEqual(r.status_code, 404)

    def test_voucher_create_rejects_foreign_location(self):
        # outsider cannot plant a voucher in the ADIC tin.
        self.client.force_authenticate(self.outsider)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._voucher_payload(), format='json')
        self.assertEqual(r.status_code, 404)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_receipt_upload_and_list(self):
        self.client.force_authenticate(self.accountant)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._voucher_payload(), format='json')
        vid = r.json()['id']
        f = SimpleUploadedFile('slip.jpg', b'\xff\xd8\xff\xe0fakejpeg', content_type='image/jpeg')
        up = self.client.post(f'/api/v1/petty-cash-vouchers/{vid}/receipts/', {'file': f}, format='multipart')
        self.assertEqual(up.status_code, 201, up.content)
        lst = self.client.get(f'/api/v1/petty-cash-vouchers/{vid}/receipts/')
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.json()), 1)
        self.assertEqual(lst.json()[0]['filename'], 'slip.jpg')

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_receipt_rejects_non_image_type(self):
        self.client.force_authenticate(self.accountant)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._voucher_payload(), format='json')
        vid = r.json()['id']
        f = SimpleUploadedFile('bad.exe', b'MZmalware', content_type='application/x-msdownload')
        up = self.client.post(f'/api/v1/petty-cash-vouchers/{vid}/receipts/', {'file': f}, format='multipart')
        self.assertEqual(up.status_code, 400)


class PettyCashOptionalGlTests(TestCase):
    """The person who RAISES a voucher does not code the GL — the petty-cash
    finance team sets the expense account when they post it (CFO directive
    2026-08-10). These lock in: a voucher can be raised with NO GL, it cannot be
    POSTED until it is coded, coding-first needs no reason, and re-coding an
    already-coded voucher still does."""

    @classmethod
    def setUpTestData(cls):
        _World.build(cls)
        cls.maker = _user('og_maker', UserProfile.Title.ACCOUNTANT)
        cls.signer1 = _user('og_keetile', UserProfile.Title.SENIOR_ACCOUNTANT,
                            email='kmokhendo@alphadirect.co.bw')
        cls.signer2 = _user('og_pako', UserProfile.Title.FINANCIAL_CONTROLLER,
                            email='pkago@alphadirect.co.bw')

    def _uncoded_draft(self):
        return services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='Modiri',
            amount=Decimal('250.00'), expense_account=None,
            description='Birthday cake', user=self.maker,
        )

    def test_voucher_can_be_raised_without_a_gl_account(self):
        v = self._uncoded_draft()
        v.refresh_from_db()
        self.assertIsNone(v.expense_account_id)
        self.assertEqual(v.status, PettyCashVoucher.Status.DRAFT)

    def test_uncoded_voucher_cannot_be_signed(self):
        v = self._uncoded_draft()
        services.submit_voucher(v, self.maker)
        with self.assertRaises(ValidationError) as ctx:
            services.approve_voucher(v, self.signer1)
        self.assertIn('coded', str(ctx.exception).lower())
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.PENDING_APPROVAL)

    def test_finance_codes_it_first_then_it_posts(self):
        v = self._uncoded_draft()
        services.submit_voucher(v, self.maker)
        # Finance codes the blank GL — NO reason needed for first-time coding.
        services.amend_voucher(v, self.signer1, expense_account=self.exp, reason='')
        v.refresh_from_db()
        self.assertEqual(v.expense_account_id, self.exp.id)
        # Two distinct signers (neither the maker) post the JE.
        services.approve_voucher(v, self.signer1)
        services.approve_voucher(v, self.signer2)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        lines = {l.account.code: l for l in v.journal_entry.lines.all()}
        self.assertEqual(lines['6100'].debit_amount, Decimal('250.00'))
        self.assertEqual(lines['1160'].credit_amount, Decimal('250.00'))

    def test_recoding_an_already_coded_voucher_still_needs_a_reason(self):
        # Correcting a GL that was already set is a CHANGE — it must be explained.
        v = services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='X',
            amount=Decimal('50.00'), expense_account=self.exp,
            description='Tea', user=self.maker,
        )
        services.submit_voucher(v, self.maker)
        with self.assertRaises(ValidationError):
            services.amend_voucher(v, self.signer1, expense_account=self.exp2, reason='')


class PettyCashRaiseWithoutGlApiTests(APITestCase):
    """The maker-title gate now applies ONLY to a CODED voucher. Any staff
    member may raise a GL-LESS request; a non-finance user still cannot inject a
    coded entry."""

    @classmethod
    def setUpTestData(cls):
        _World.build(cls)
        cls.ea = _user('api_ea', UserProfile.Title.OPERATIONS, email='ceooffice@alphadirect.co.bw')
        UserCompanyAccess.objects.create(user=cls.ea, company=cls.adic, can_view=True, can_write=True)

    def _payload(self, with_gl=False):
        p = {
            'location': str(self.loc.id), 'voucher_date': str(TODAY),
            'payee': 'Modiri', 'amount': '600.00', 'description': 'Birthday cake for Bokani',
        }
        if with_gl:
            p['expense_account'] = str(self.exp.id)
        return p

    def test_non_finance_can_raise_a_gl_less_request(self):
        self.client.force_authenticate(self.ea)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._payload(with_gl=False), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        v = PettyCashVoucher.objects.get(pk=r.json()['id'])
        self.assertIsNone(v.expense_account_id)

    def test_non_finance_cannot_raise_a_coded_voucher(self):
        self.client.force_authenticate(self.ea)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._payload(with_gl=True), format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_requester_is_not_shown_the_coding_step_but_a_coder_is(self):
        """The bug that hit Modiri (CFO 2026-08-14): the requester who raises a
        voucher was shown 'Amend amount / GL' and a GL picker she could neither
        load (403 on the accounts list) nor was allowed to use. The UI now hides
        the code/sign controls from everyone but the petty-cash team, driven by
        the server-computed `viewer_can_code` flag. Coding is Keetile/Legakwa's
        job, not the requester's."""
        coder = _user('kmokhendo', UserProfile.Title.SENIOR_ACCOUNTANT,
                      email='kmokhendo@alphadirect.co.bw')
        UserCompanyAccess.objects.create(user=coder, company=self.adic, can_view=True, can_write=True)

        # The EA raises a GL-less request, then opens it.
        self.client.force_authenticate(self.ea)
        r = self.client.post('/api/v1/petty-cash-vouchers/', self._payload(with_gl=False), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        vid = r.json()['id']
        # Fresh in the create response …
        self.assertFalse(r.json()['viewer_can_code'])
        # … and on a subsequent detail read.
        detail = self.client.get(f'/api/v1/petty-cash-vouchers/{vid}/')
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertFalse(detail.json()['viewer_can_code'])

        # The petty-cash coder sees the same voucher WITH the coding controls.
        self.client.force_authenticate(coder)
        detail = self.client.get(f'/api/v1/petty-cash-vouchers/{vid}/')
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertTrue(detail.json()['viewer_can_code'])


class PettyCashUnicoinOverrideTests(TestCase):
    """Per-entity ring-fence for the Unicoin tin (CFO directive 2026-08-31).

    Unicoin petty cash is walled off from the group finance pool:
      • only Bakang Mhusiwa / Phatsimo Moseki may RAISE a Unicoin voucher;
      • only Keetile Mokhendo (1st) / Bharath Balasubramanian may APPROVE it;
      • the group signers (Pako, Legakwa, Tlamelo) may NOT touch the Unicoin
        tin, but keep their powers on every ordinary tin;
      • the tin carries a red theft-warning notice.
    """

    @classmethod
    def setUpTestData(cls):
        from petty_cash.models import PettyCashLocation
        _World.build(cls)
        cls.uni = Company.objects.create(code='UNI', name='Unicoin')
        FiscalPeriod.objects.create(
            company=cls.uni, period_name='UNI-wide',
            start_date=date(2024, 1, 1), end_date=date(2027, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )
        cls.uni_loc = PettyCashLocation.objects.create(
            name='Unicoin petty cash', company=cls.uni, float_amount=Decimal('15000.00'),
            petty_cash_account=cls.petty, reimbursing_bank_account=cls.bank, is_active=True,
        )
        # Named Unicoin people
        cls.bakang = _user('bmhusiwa', UserProfile.Title.SENIOR_ACCOUNTANT,
                           email='bmhusiwa@insurance.co.bw')
        cls.phatsimo = _user('pmoseki', UserProfile.Title.OPERATIONS,
                             email='pmoseki@insurance.co.bw')
        cls.keetile = _user('keetile.mokhendo', UserProfile.Title.SENIOR_ACCOUNTANT,
                            email='kmokhendo@alphadirect.co.bw')
        cls.bharath = _user('bbalasubramanian', UserProfile.Title.OPERATIONS,
                            email='bbalasubramanian@alphadirect.co.bw')
        # A group signer who must be BLOCKED on Unicoin
        cls.pako = _user('pkago', UserProfile.Title.FINANCIAL_CONTROLLER,
                         email='pkago@alphadirect.co.bw')
        # A random staffer with no petty-cash powers anywhere
        cls.random_staff = _user('some.clerk', UserProfile.Title.OPERATIONS,
                                 email='sclerk@alphadirect.co.bw')
        # A default group petty-cash handler (Tlamelo) — should NOT be tasked for
        # a Unicoin voucher, whose review routes to the ring-fenced approvers.
        cls.tlamelo = _user('tlamelo.chimidza', UserProfile.Title.SENIOR_ACCOUNTANT,
                            email='tchimidza@alphadirect.co.bw')

    def _uni_draft(self, creator):
        return services.create_voucher(
            location=self.uni_loc, voucher_date=TODAY, payee='Corner shop',
            amount=Decimal('250.00'), expense_account=self.exp,
            description='Airtime for the Unicoin desk', user=creator,
        )

    # ---- notice -------------------------------------------------------------

    def test_unicoin_tin_carries_theft_notice_adic_does_not(self):
        notice = services.access_notice_for_location(self.uni_loc)
        self.assertIsNotNone(notice)
        self.assertIn('THEFT', notice['text'])
        self.assertIn('Keetile', notice['approvers'])
        self.assertIn('Bharath', notice['approvers'])
        self.assertIsNone(services.access_notice_for_location(self.loc))

    # ---- input gate ---------------------------------------------------------

    def test_only_named_people_may_raise_a_unicoin_voucher(self):
        self.assertTrue(services.can_input_for_location(self.bakang, self.uni_loc))
        self.assertTrue(services.can_input_for_location(self.phatsimo, self.uni_loc))
        self.assertFalse(services.can_input_for_location(self.random_staff, self.uni_loc))
        self.assertFalse(services.can_input_for_location(self.pako, self.uni_loc))

    def test_input_gate_open_on_ordinary_tin(self):
        # No override on the ADIC tin — any staffer may raise there.
        self.assertTrue(services.can_input_for_location(self.random_staff, self.loc))

    # ---- approval gate ------------------------------------------------------

    def test_single_approval_by_bharath_posts_a_unicoin_voucher(self):
        # CFO 2026-08-31: Unicoin is single-signature — one approval posts it.
        v = self._uni_draft(self.bakang)
        services.submit_voucher(v, self.bakang)
        services.approve_voucher(v, self.bharath)   # ONE approval -> posts
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)
        self.assertEqual(v.approved_by_id, self.bharath.id)
        self.assertIsNotNone(v.journal_entry)

    def test_single_approval_by_keetile_also_posts(self):
        # Keetile is the fallback approver — one approval from her also posts.
        v = self._uni_draft(self.phatsimo)
        services.submit_voucher(v, self.phatsimo)
        services.approve_voucher(v, self.keetile)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)

    def test_unicoin_requires_only_one_signature(self):
        from petty_cash.services import _signatures_required
        v = self._uni_draft(self.bakang)
        self.assertEqual(_signatures_required(v), 1)
        # a group (ADIC) voucher still needs two
        va = services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='X',
            amount=Decimal('10.00'), expense_account=self.exp,
            description='y', user=self.random_staff)
        self.assertEqual(_signatures_required(va), 2)

    def test_group_signer_cannot_approve_a_unicoin_voucher(self):
        v = self._uni_draft(self.bakang)
        services.submit_voucher(v, self.bakang)
        with self.assertRaises(ValidationError) as ctx:
            services.approve_voucher(v, self.pako)
        self.assertIn('theft', str(ctx.exception).lower())

    def test_group_signers_still_work_on_ordinary_tin(self):
        # Regression: the ADIC tin keeps the group pool. Keetile + Pako sign.
        v = services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='Kgomotso',
            amount=Decimal('250.00'), expense_account=self.exp,
            description='Taxi', user=self.random_staff,
        )
        services.submit_voucher(v, self.random_staff)
        services.approve_voucher(v, self.keetile)
        services.approve_voucher(v, self.pako)
        v.refresh_from_db()
        self.assertEqual(v.status, PettyCashVoucher.Status.POSTED)

    def test_group_signer_cannot_reject_a_unicoin_voucher(self):
        v = self._uni_draft(self.bakang)
        services.submit_voucher(v, self.bakang)
        with self.assertRaises(ValidationError):
            services.reject_voucher(v, self.pako, 'no')

    def test_unicoin_review_task_routes_to_named_approvers_not_group(self):
        # The review task for a Unicoin voucher must land on Keetile / Bharath,
        # NOT the default group handlers (Tlamelo). (Fable 5 fix, 2026-08-31.)
        from core.models import OmniTask
        v = self._uni_draft(self.bakang)
        services.submit_voucher(v, self.bakang)   # fires notify_petty_cash_pending
        tasked = set(
            OmniTask.objects
            .filter(title__icontains=v.voucher_number)
            .values_list('assignee__username', flat=True)
        )
        self.assertIn('keetile.mokhendo', tasked)
        self.assertIn('bbalasubramanian', tasked)
        self.assertNotIn('tlamelo.chimidza', tasked,
                         'a group handler must not be tasked for a Unicoin voucher')


class PettyCashUnicoinInputBypassApiTests(APITestCase):
    """The input ring-fence must hold on the EDIT path too, not only create.

    A DRAFT voucher's `location` is writable (create-serializer), so without a
    guard on update/partial_update a staffer could raise a draft on an open tin
    and PATCH it onto the Unicoin tin — defeating "only Bakang/Phatsimo may
    raise". (Fable 5, checklist L20, 2026-08-31.)
    """

    @classmethod
    def setUpTestData(cls):
        from petty_cash.models import PettyCashLocation
        _World.build(cls)
        cls.uni = Company.objects.create(code='UNI', name='Unicoin')
        FiscalPeriod.objects.create(
            company=cls.uni, period_name='UNI-wide',
            start_date=date(2024, 1, 1), end_date=date(2027, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )
        cls.uni_loc = PettyCashLocation.objects.create(
            name='Unicoin petty cash', company=cls.uni, float_amount=Decimal('15000.00'),
            petty_cash_account=cls.petty, reimbursing_bank_account=cls.bank, is_active=True,
        )
        # A staffer with access to BOTH tins but NOT a named Unicoin inputter,
        # so the block is the INPUT gate (403), not the tenant scope (404).
        cls.clerk = _user('some.clerk', UserProfile.Title.OPERATIONS,
                          email='sclerk@alphadirect.co.bw')
        for co in (cls.adic, cls.uni):
            UserCompanyAccess.objects.create(user=cls.clerk, company=co,
                                             can_view=True, can_write=True)

    def test_draft_cannot_be_moved_onto_the_unicoin_tin_by_a_non_inputter(self):
        # Draft raised on the open ADIC tin (allowed there), then try to PATCH
        # its location onto the ring-fenced Unicoin tin.
        v = services.create_voucher(
            location=self.loc, voucher_date=TODAY, payee='Shop',
            amount=Decimal('120.00'), expense_account=self.exp,
            description='Sundry', user=self.clerk,
        )
        self.client.force_authenticate(self.clerk)
        resp = self.client.patch(f'/api/v1/petty-cash-vouchers/{v.id}/',
                                 {'location': str(self.uni_loc.id)}, format='json')
        self.assertEqual(resp.status_code, 403, resp.content)
        v.refresh_from_db()
        self.assertEqual(v.location_id, self.loc.id,
                         'the voucher must NOT have been moved onto the Unicoin tin')
