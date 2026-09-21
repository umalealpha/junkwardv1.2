"""Regression: Payment.confirm() JE-post authorisation is scoped, not blanket.

Fable audit 2026-08-19 — penny test PAY-OUT-2026-000008 (CFO Prathap).

Bug (fails without the fix on payments/models.py::Payment.confirm):
    Outbound dual-control payment. CFO signs FIRST (superuser). The FM/FC
    second-signer's approve_payment → confirm → je.post(user=FM) hit the
    ledger raw-draft guard because can_post_directly=False and superuser=False,
    stranding the payment in draft despite quorum being met. Order-dependent.

Guard (must NOT be bypassed by the fix):
    A non-superuser finance user confirming an unapproved RECEIVED draft
    via /confirm/ must still be blocked by the ledger raw-draft guard —
    otherwise any maker could post solo (fake-receipt fraud vector).

Test env:
    Runs in the CI/prod container (Postgres + full app deps). Not runnable
    on the Windows dev machine's sqlite harness.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from billing.models import Contact
from core.models import Company, Currency, UserProfile
from ledger.models import Account, FiscalPeriod
from payments.models import Payment, PaymentApproval
from procurement.models import VendorBankAccount


class PaymentConfirmAuthorisationTests(TestCase):
    """confirm()'s JE-post authorisation must be scoped to authorised branches."""

    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='CFA1', defaults={'name': 'Confirm Auth Test Co',
                                   'base_currency': cls.bwp})

        # Maker (Pako equivalent) — FC title, non-superuser.
        # update_or_create, not filter().update(): a User has no auto-created
        # profile, so filter().update() silently updated 0 rows and the title
        # never stuck — approve_payment then refused "title not authorised".
        cls.maker = User.objects.create_user('cfa_maker', password='x')
        UserProfile.objects.update_or_create(
            user=cls.maker,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER,
                      'is_active': True})

        # FM approver (Legakwa equivalent) — non-superuser, no can_post_directly
        cls.fm = User.objects.create_user('cfa_fm', password='x')
        UserProfile.objects.update_or_create(
            user=cls.fm,
            defaults={'title': UserProfile.Title.FINANCE_MANAGER,
                      'is_active': True})

        # CFO approver (Prathap equivalent) — superuser
        cls.cfo = User.objects.create_superuser(
            'cfa_cfo', 'cfo@example.com', 'x')
        UserProfile.objects.update_or_create(
            user=cls.cfo,
            defaults={'title': UserProfile.Title.CFO, 'is_active': True})

        # Vendor + bank
        cls.vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Test Vendor',
            currency_code=cls.bwp, company=cls.company)
        cls.bank, _ = Account.objects.get_or_create(
            code='CFA-BANK',
            defaults={'name': 'Confirm Auth Bank', 'account_type': 'asset',
                      'currency_code': cls.bwp, 'is_bank_account': True})

        # An OPEN fiscal period covering today's payment_date, or je.post fails
        # with "No open fiscal period covers ..." before the guard under test is
        # reached. Wide bounds so the date lands inside it across a year boundary.
        today = date.today()
        FiscalPeriod.objects.create(
            company=cls.company, period_name=today.strftime('%Y-%m'),
            start_date=date(today.year - 1, 1, 1),
            end_date=date(today.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )

        # The standard chart of accounts (je.post needs mapped accounts, e.g.
        # 1240) — otherwise confirm() dies with "Required account 1240 not found"
        # before reaching the guard under test.
        call_command('setup_chart_of_accounts')

        # An ACTIVE vendor bank account — a non-once-off outbound electronic
        # payment must have one before it can be confirmed (only ACTIVE is
        # payable). create() bypasses the immutability clean, which is fine here.
        cls.vba = VendorBankAccount.objects.create(
            contact=cls.vendor, bank_name='FNB',
            account_holder_name='Test Vendor', account_number='62012345678',
            currency_code=cls.bwp, is_default=True,
            status=VendorBankAccount.Status.ACTIVE,
            created_by=cls.maker, approved_by=cls.fm,
        )

    # ------------------------------------------------------------------
    # POSITIVE — the fix
    # ------------------------------------------------------------------
    def test_cfo_first_fm_second_posts_je(self):
        """Reproduces PAY-OUT-2026-000008. Must PASS after the fix, FAIL before it.

        CFO signs first (superuser — worked on old code). FM signs second —
        old code: ledger guard blocks je.post(user=FM). New code: quorum-met
        (approval_status=APPROVED before confirm()) so _allow_direct=True is
        passed and the JE posts cleanly.
        """
        p = Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=self.vendor, company=self.company, bank_account=self.bank,
            payment_date=date.today(), currency_code=self.bwp,
            amount=Decimal('10.00'),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.DRAFT, created_by=self.maker,
            vendor_bank_account=self.vba,
        )
        p.submit_for_approval(self.maker, notify=False)

        # CFO signs FIRST
        p.approve_payment(self.cfo, comment='CFO approves first')
        p.refresh_from_db()
        self.assertEqual(p.approval_status, Payment.ApprovalStatus.PENDING,
                         'CFO alone should not complete quorum')

        # FM signs SECOND — this is the failing click on old code
        p.approve_payment(self.fm, comment='FM approves second')
        p.refresh_from_db()

        self.assertEqual(p.status, Payment.Status.CONFIRMED,
                         'FM second-sign must not strand the payment in draft')
        self.assertEqual(p.approval_status, Payment.ApprovalStatus.APPROVED)
        self.assertIsNotNone(p.journal_entry_id, 'JE must be created')
        self.assertEqual(p.journal_entry.status, 'posted',
                         'JE must be POSTED, not stuck in DRAFT')

    def test_fm_first_cfo_second_still_posts_je(self):
        """The already-working order (FM first, CFO second) must keep working.

        Reproduces PAY-OUT-2026-000007's happy path. Guards against a
        regression that would flip the order dependence the other way.
        """
        p = Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=self.vendor, company=self.company, bank_account=self.bank,
            payment_date=date.today(), currency_code=self.bwp,
            amount=Decimal('10.00'),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.DRAFT, created_by=self.maker,
            vendor_bank_account=self.vba,
        )
        p.submit_for_approval(self.maker, notify=False)

        p.approve_payment(self.fm, comment='FM first')
        p.approve_payment(self.cfo, comment='CFO second')
        p.refresh_from_db()

        self.assertEqual(p.status, Payment.Status.CONFIRMED)
        self.assertEqual(p.journal_entry.status, 'posted')

    # ------------------------------------------------------------------
    # NEGATIVE — the door Fable flagged stays locked
    # ------------------------------------------------------------------
    def test_received_unapproved_draft_still_blocked(self):
        """A non-superuser finance user must NOT be able to post a RECEIVED
        draft's JE solo. approval_status defaults to NOT_REQUIRED for
        RECEIVED payments, so the fix's _quorum_or_system predicate must be
        False and the ledger raw-draft guard must fire.

        Locks the door: without this, any maker could confirm a fake customer
        receipt to clear a debtor balance (teeming-and-lading vector).
        """
        p = Payment.objects.create(
            payment_type=Payment.PaymentType.RECEIVED,
            contact=self.vendor, company=self.company, bank_account=self.bank,
            payment_date=date.today(), currency_code=self.bwp,
            amount=Decimal('10.00'),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.DRAFT, created_by=self.maker,
        )
        # Sanity: RECEIVED draft defaults to NOT_REQUIRED (not APPROVED),
        # so the fix predicate _quorum_or_system is False.
        self.assertNotEqual(p.approval_status, Payment.ApprovalStatus.APPROVED)

        # FM tries to confirm it solo — ledger raw-draft guard MUST fire.
        with self.assertRaises(ValidationError) as cm:
            p.confirm(self.fm)
        msg = str(cm.exception)
        self.assertIn('Direct posting is not permitted', msg,
                      'RECEIVED-draft solo confirm must remain blocked')
