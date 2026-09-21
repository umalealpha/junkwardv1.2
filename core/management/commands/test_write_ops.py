"""
test_write_ops — exercise write paths Manus's read-only test couldn't cover.

CFO directive 2026-05-21 (post-Manus full-system test follow-up).

What this does:
  1. Picks a real vendor + expense account for ADIC.
  2. Creates a small DRAFT PurchaseOrder (BWP 100, 1 line).
  3. Submits it for FM approval.
  4. FM-approves with a different user (segregation of duties).
  5. CFO-approves with the CFO user (third different user).
  6. Picks the current open FiscalPeriod and runs monthly depreciation
     for ADIC (dry-run by default, --apply to actually post).

Run order:
    python manage.py test_write_ops               # dry-run depreciation
    python manage.py test_write_ops --apply       # real depreciation

Output: one Markdown-friendly section per step with PASS/FAIL.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import Company, UserProfile
from billing.models import Contact
from ledger.models import Account, FiscalPeriod
from procurement.models import PurchaseOrder
from procurement import services as po_services


def _find_user_with_title(title_codes):
    """Return first User whose linked UserProfile has one of these title codes."""
    return (User.objects.filter(profile__title__in=title_codes, is_active=True)
            .exclude(profile__user_id=None).first())


class Command(BaseCommand):
    help = 'End-to-end write-path test: Create PO, approve through FM+CFO, run depreciation.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually post depreciation. Default = dry-run for depreciation only.')
        parser.add_argument('--cleanup', action='store_true',
                            help='At the end, cancel the test PO to avoid noise in queue.')

    def handle(self, *args, **opts):
        apply = opts['apply']

        # ── Pick actors ─────────────────────────────────────────────────────
        adic = Company.objects.filter(code='ADIC').first()
        if not adic:
            return self._fail('No ADIC company found.')

        # Creator: any operations_staff user (not Pako, not admin)
        creator = (User.objects.filter(
                       profile__role=UserProfile.Role.OPERATIONS_STAFF,
                       is_active=True,
                   ).exclude(username__in=['admin']).first())
        if not creator:
            return self._fail('No operations_staff user found for creator.')

        fm_user = _find_user_with_title([
            UserProfile.Title.FINANCIAL_CONTROLLER,
            UserProfile.Title.FINANCE_MANAGER,
        ])
        if not fm_user:
            return self._fail('No Finance Manager / Financial Controller user found.')

        cfo_user = (User.objects.filter(profile__title=UserProfile.Title.CFO,
                                        is_active=True).first()
                    or User.objects.filter(is_superuser=True, is_active=True).first())
        if not cfo_user:
            return self._fail('No CFO user found.')

        if len({creator.pk, fm_user.pk, cfo_user.pk}) != 3:
            return self._fail(
                f'Creator/FM/CFO must be 3 different users — got '
                f'creator={creator.username}, fm={fm_user.username}, cfo={cfo_user.username}'
            )

        vendor = Contact.objects.filter(
            contact_type='vendor', company=adic, is_active=True,
        ).first()
        if not vendor:
            return self._fail('No vendor contact for ADIC.')

        # Pick an expense account ADIC owns
        expense_acct = Account.objects.filter(
            owner_company=adic, account_type='expense',
            is_active=True, is_archived=False,
        ).first()
        if not expense_acct:
            return self._fail('No active expense account for ADIC.')

        self.stdout.write(self.style.SUCCESS('=== Test Write-Ops ==='))
        self.stdout.write(f'  ADIC          : {adic.code}')
        self.stdout.write(f'  Creator       : {creator.username} ({getattr(creator.profile, "title", "?")})')
        self.stdout.write(f'  FM approver   : {fm_user.username} ({getattr(fm_user.profile, "title", "?")})')
        self.stdout.write(f'  CFO approver  : {cfo_user.username} ({getattr(cfo_user.profile, "title", "?")})')
        self.stdout.write(f'  Vendor        : {vendor.name}')
        self.stdout.write(f'  Expense Acct  : {expense_acct.code} {expense_acct.name}')
        self.stdout.write('')

        # ── Step 1: Create PO ───────────────────────────────────────────────
        try:
            po = PurchaseOrder(
                department='admin',
                supplier=vendor,
                company=adic,
                issue_date=date.today(),
                expected_delivery_date=date.today() + timedelta(days=30),
                currency_code_id='BWP',
                justification='Automated write-path test (test_write_ops). Safe to cancel.',
                created_by=creator,
            )
            po.save(audit_user=creator,
                    audit_description='Created via test_write_ops')
            from procurement.models import PurchaseOrderLine
            PurchaseOrderLine.objects.create(
                purchase_order=po,
                description='Test write-path line — auto-generated',
                account=expense_acct,
                quantity=Decimal('1'),
                unit_price=Decimal('100.00'),
            )
            po.recalculate_totals()
            po.save(audit_user=creator)
            self._pass(f'1. CREATE PO  → {po.po_number} status={po.status} total={po.total_amount}')
        except Exception as exc:
            return self._fail(f'CREATE PO: {exc}')

        # ── Step 2: Submit for approval ────────────────────────────────────
        try:
            po_services.submit_for_approval(po, creator)
            po.refresh_from_db()
            self._pass(f'2. SUBMIT     → status={po.status}')
        except Exception as exc:
            return self._fail(f'SUBMIT: {exc}')

        # ── Step 3: FM approve ─────────────────────────────────────────────
        try:
            po_services.fm_approve(po, fm_user)
            po.refresh_from_db()
            self._pass(f'3. FM APPROVE → status={po.status} fm={fm_user.username}')
        except Exception as exc:
            return self._fail(f'FM APPROVE: {exc}')

        # ── Step 4: CFO approve ────────────────────────────────────────────
        try:
            po_services.cfo_approve(po, cfo_user)
            po.refresh_from_db()
            self._pass(f'4. CFO APPR.  → status={po.status} cfo={cfo_user.username}')
            if po.commitment_journal_entry_id:
                je = po.commitment_journal_entry
                self._pass(f'   commitment JE → {je.entry_number} status={je.status} lines={je.lines.count()}')
            else:
                self.stdout.write(self.style.WARNING(
                    '   NO commitment_journal_entry attached (issue #58?)'))
        except Exception as exc:
            return self._fail(f'CFO APPROVE: {exc}')

        # ── Step 5: Depreciation ───────────────────────────────────────────
        today = date.today()
        period = FiscalPeriod.objects.filter(
            start_date__lte=today, end_date__gte=today, status='open',
        ).first()
        if not period:
            self.stdout.write(self.style.WARNING(
                '   No open FiscalPeriod covers today — skipping depreciation step.'))
        else:
            try:
                from assets.services import run_monthly_depreciation
                result = run_monthly_depreciation(
                    period, cfo_user, company=adic, dry_run=(not apply),
                )
                self._pass(
                    f'5. DEPRECIATE → period={result.period} '
                    f'considered={result.assets_considered} '
                    f'depreciated={result.assets_depreciated} '
                    f'skipped={result.assets_skipped} '
                    f'fully={result.assets_fully_depreciated} '
                    f'amount=BWP {result.total_amount} '
                    f'{"(DRY)" if not apply else "(APPLIED)"}'
                )
                if result.errors:
                    self.stdout.write(self.style.WARNING(
                        f'   {len(result.errors)} asset errors — first 3:'))
                    for e in result.errors[:3]:
                        self.stdout.write(f'     {e}')
            except Exception as exc:
                return self._fail(f'DEPRECIATE: {exc}')

        if opts['cleanup']:
            try:
                po_services.cancel(po, cfo_user, 'Auto-cancelled by test_write_ops')
                self.stdout.write(self.style.WARNING(f'\nCleanup: cancelled {po.po_number}'))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Cleanup failed: {exc}'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('All write-path steps completed.'))

    def _pass(self, msg):
        self.stdout.write(self.style.SUCCESS(f'PASS  {msg}'))

    def _fail(self, msg):
        self.stdout.write(self.style.ERROR(f'FAIL  {msg}'))
