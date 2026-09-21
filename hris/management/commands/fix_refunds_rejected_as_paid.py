"""fix_refunds_rejected_as_paid — correct refunds that were PAID but recorded as
REJECTED (Bharath Balasubramanian's report, 2026-08-17).

`ExpenseClaim.Status.PAID` existed from the start but nothing ever set it, so a
refund paid outside the accountant + CFO route had nowhere to go. People closed
it with Reject and typed the reason "PAID". The record then reads "Returned to
requester" for money that actually left the bank, and the requester was emailed
that their refund "needs changes".

Found on prod 2026-08-17 — 3 rows, all Bharath Balasubramanian, all 30-Jul-2026:
    P1,422.19 + P500.00 + P549.99 = P2,472.18

This flips exactly those rows to PAID, keeping the original reason as an audit
breadcrumb in `paid_note`. It is deliberately NARROW:

  * only rows whose status is REJECTED,
  * only where the reject_reason, stripped and lowercased, is one of the
    PAID_MARKERS below — a reason like "Receipt is unreadable" is a REAL
    rejection and must never be touched,
  * never a row already PAID.

It does NOT move money and does NOT post a journal. It corrects a status field.

    python manage.py fix_refunds_rejected_as_paid --dry-run
    python manage.py fix_refunds_rejected_as_paid --reference "see FNB 30-Jul-2026"
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from hris.expense_claim_models import ExpenseClaim

# A reject reason that means "this was actually paid". Matched on the whole
# stripped, lowercased string — NOT a substring, so "not paid yet" can never hit.
PAID_MARKERS = {'paid', 'paid.', 'already paid', 'is paid', 'was paid', 'payed'}


class Command(BaseCommand):
    help = ('Correct refunds recorded as REJECTED with the reason "PAID" so they '
            'read as PAID. Narrow and reversible-by-inspection; dry-run first.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--reference', default='corrected 2026-08-17 — reject reason was "PAID"',
            help='Payment reference stamped on the corrected rows.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        reference = opts['reference'][:120]

        candidates = [
            c for c in (ExpenseClaim.objects
                        .filter(status=ExpenseClaim.Status.REJECTED)
                        .select_related('profile__user')
                        .order_by('expense_date'))
            if (c.reject_reason or '').strip().lower() in PAID_MARKERS
        ]

        if not candidates:
            self.stdout.write(self.style.SUCCESS(
                'Nothing to correct — no refund is rejected with a "paid" reason.'))
            return

        self.stdout.write(f'{len(candidates)} refund(s) rejected but actually paid:\n')
        total = 0
        for c in candidates:
            who = getattr(getattr(c.profile, 'user', None), 'username', '?')
            self.stdout.write(
                f'  ~ {who:24s} {c.currency} {c.amount:>10,.2f}  '
                f'{c.expense_date}  reason={c.reject_reason.strip()!r}  '
                f'-> PAID')
            total += c.amount

        with transaction.atomic():
            for c in candidates:
                if dry:
                    continue
                note = (f'[corrected {timezone.now():%Y-%m-%d}: was rejected with '
                        f'reason "{c.reject_reason.strip()}" because Omni had no '
                        f'way to record a refund as paid]')
                c.status         = ExpenseClaim.Status.PAID
                c.paid_at        = c.paid_at or timezone.now()
                c.paid_reference = reference
                c.paid_note      = (f'{c.paid_note} {note}'.strip()
                                    if c.paid_note else note)
                c.reject_reason  = ''
                c.save(update_fields=['status', 'paid_at', 'paid_reference',
                                      'paid_note', 'reject_reason', 'updated_at'])
            if dry:
                transaction.set_rollback(True)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"DRY RUN  " if dry else ""}Corrected {len(candidates)} refund(s), '
            f'total {total:,.2f}. No money moved and no journal posted — only the '
            f'status and the audit note changed.'))
        if dry:
            self.stdout.write(self.style.WARNING('No DB changes (--dry-run).'))
        else:
            self.stdout.write(
                'The requester is NOT emailed by this command — these are historic '
                'corrections, not new events.')
