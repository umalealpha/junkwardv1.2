"""
Put a panel firm and its retainer on file. Nothing else.

A control screen with no agreement behind it shows zeros, and zeros teach nobody anything.
This records what the CFO actually told us, so the retainer scorecard has something real to
measure.

    python manage.py bonu_seed_panel --firm "JEREMIAH TLADI & COMPANY" \
        --fee 40000 --cases 40 --start 2025-07-01 --label South

**Use --label when a firm has more than one agreement.** One firm can hold several SLAs (CFO
2026-08-03: 40,000 for the south, around 85,000 for the north). Without a label the second run
UPDATES the first agreement instead of adding one — which is exactly what happened on
production and silently replaced the northern fee with the southern one.

DELIBERATELY NARROW. It creates a firm and a retainer, and it will not invent a single
invoice, case or event — a control that measures made-up work is worse than no control.
It is idempotent: run it twice and the second run updates rather than duplicates.

The committed case count is recorded WITH a note saying where it came from, because
"40 cases" is a contractual term and until the signed agreement is on file the scorecard
must not present it as one.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Record a BONU panel firm and its monthly retainer.'

    def add_arguments(self, parser):
        parser.add_argument('--firm', required=True)
        parser.add_argument('--fee', required=True, type=str, help='Monthly fee in BWP, e.g. 85000')
        parser.add_argument('--cases', required=True, type=int, help='Committed caseload, e.g. 40')
        parser.add_argument('--start', required=True, help='Agreement start date, YYYY-MM-DD')
        parser.add_argument('--rate', default='', help='Agreed hourly rate, if there is one on file.')
        parser.add_argument('--email', default='', help='Where their invoices come from.')
        parser.add_argument('--quiet-days', type=int, default=30)
        parser.add_argument('--first-action-days', type=int, default=5)
        parser.add_argument('--source', default='CFO instruction, 3 August 2026 — pending the '
                                               'signed retainer terms.')
        # One firm can hold SEVERAL SLAs — CFO 2026-08-03: "the same legal firm has two SLAs.
        # One is for south for 40,000 BWP and somewhere around 85,000 to manage north." Without
        # a label the second run UPDATED the first agreement instead of adding one, which is
        # exactly what happened on prod and silently lost the northern fee.
        parser.add_argument('--label', default='',
                            help='Distinguishes several agreements with one firm, e.g. South.')

    def handle(self, *args, **o):
        from bonu.models import LawFirm, RetainerAgreement

        try:
            start = dt.date.fromisoformat(o['start'])
        except ValueError:
            raise CommandError('--start must be a date like 2026-01-01') from None
        try:
            fee = Decimal(str(o['fee']).replace(',', ''))
        except Exception:                     # noqa: BLE001
            raise CommandError('--fee must be a number like 85000') from None
        if o['cases'] < 1:
            raise CommandError('--cases must be at least 1')

        firm, made = LawFirm.objects.get_or_create(name=o['firm'].strip())
        changed = []
        if o['email'] and firm.contact_email != o['email']:
            firm.contact_email = o['email']
            changed.append('contact_email')
        if o['rate']:
            firm.agreed_hourly_rate = Decimal(str(o['rate']).replace(',', ''))
            changed.append('agreed_hourly_rate')
        if changed:
            firm.save(update_fields=changed + ['updated_at'])
        self.stdout.write(f"firm  : {firm.name} ({'created' if made else 'already on file'})"
                          + (f" · agreed rate {firm.agreed_hourly_rate}" if firm.agreed_hourly_rate
                             else ' · NO agreed rate on file, so rate checks cannot run'))

        label = (o.get('label') or '').strip()
        name = f'{firm.name} — BONU panel' + (f' ({label})' if label else '')
        retainer, r_made = RetainerAgreement.objects.update_or_create(
            firm=firm, name=name,
            defaults={
                'monthly_fee': fee,
                'committed_cases': o['cases'],
                'max_days_no_activity': o['quiet_days'],
                'max_days_to_first_action': o['first_action_days'],
                'start_date': start,
                'is_active': True,
                'scope_note': (f"Committed caseload of {o['cases']} recorded from: {o['source']} "
                               f"Until the signed agreement is on file, treat the shortfall figure "
                               f"as a measurement, not a claim."),
            })
        self.stdout.write(
            f"retainer: {retainer.monthly_fee} a month for {retainer.committed_cases} cases "
            f"= {retainer.fee_per_committed_case} per case "
            f"({'created' if r_made else 'updated'})")
        self.stdout.write(self.style.SUCCESS(
            'Done. No invoice, case or event was created — those only come from real documents.'))
