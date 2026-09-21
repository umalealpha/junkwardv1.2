"""Shared RealPay → subrogation import engine.

One implementation used by BOTH the management command
(`import_subrogation_realpay`) and the upload view, so the CLI and the on-screen
"RealPay import" space behave identically and are covered by the same tests.

See the command's module docstring for the rules. This function does the work;
callers decide dry-run vs commit and how to present the returned stats.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from claims.models import Subrogation, SubrogationReceipt
from claims.recoveries.claim_number import parse_claim_number
from django.utils import timezone

ZERO = Decimal('0.00')
DEFAULT_MERCHANT = 'Alpha Direct Third Parties'


def _money(raw) -> Decimal:
    s = str(raw or '').strip().replace(',', '').replace('P', '').replace(' ', '')
    if not s:
        return ZERO
    try:
        return Decimal(s)
    except InvalidOperation:
        return ZERO


def _date(raw):
    s = str(raw or '').strip()
    if not s:
        return None
    for fmt in ('%m/%d/%Y %H:%M', '%Y/%m/%d %H:%M', '%m/%d/%Y', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_realpay_rows(rows, *, merchant=DEFAULT_MERCHANT, creator, commit=False):
    """Post SUCCESSFUL RealPay collections for `merchant` as SubrogationReceipts.

    `rows` is an iterable of dicts (csv.DictReader rows). Returns
    (stats, posted_amount, unmatched_refs). Always runs inside a transaction;
    rolls back when commit is False.
    """
    rows = list(rows)
    merchant_l = merchant.strip().lower()
    stats = {'total': len(rows), 'other_merchant': 0, 'not_successful': 0,
             'matched': 0, 'unmatched': 0, 'duplicate': 0, 'posted': 0, 'zero_amount': 0}
    posted_amount = ZERO
    unmatched_refs = []
    seen_txn = set()

    with transaction.atomic():
        for r in rows:
            if (r.get('Merchant') or '').strip().lower() != merchant_l:
                stats['other_merchant'] += 1
                continue
            status = (r.get('Current Status') or r.get('Report Status') or '').strip().upper()
            if status != 'SUCCESSFUL':
                stats['not_successful'] += 1
                continue

            ref = parse_claim_number(r.get('ClientNumber')).normalised
            sub = Subrogation.objects.filter(claim_reference=ref).first() if ref else None
            if sub is None:
                stats['unmatched'] += 1
                if r.get('ClientNumber'):
                    unmatched_refs.append(str(r.get('ClientNumber')).strip())
                continue
            stats['matched'] += 1

            amount = _money(r.get('Collected Amount'))
            if amount <= ZERO:
                stats['zero_amount'] += 1
                continue

            txn_id = '|'.join([
                (r.get('ClientNumber') or '').strip(),
                (r.get('ContractNumber') or '').strip(),
                (r.get('ContractSequence') or '').strip(),
                (r.get('InstSeq') or '').strip(),
            ])
            if txn_id in seen_txn or SubrogationReceipt.objects.filter(realpay_txn_id=txn_id).exists():
                stats['duplicate'] += 1
                continue
            seen_txn.add(txn_id)

            try:
                with transaction.atomic():
                    SubrogationReceipt.objects.create(
                        subrogation=sub, amount=amount,
                        received_date=_date(r.get('Installment Date')) or timezone.localdate(),
                        method=SubrogationReceipt.Method.REALPAY,
                        reference=(r.get('ContractNumber') or '').strip(),
                        realpay_txn_id=txn_id, created_by=creator,
                        notes='Imported from RealPay collections.',
                    )
            except IntegrityError:
                stats['duplicate'] += 1
                continue
            stats['posted'] += 1
            posted_amount += amount

        if not commit:
            transaction.set_rollback(True)

    return stats, posted_amount, unmatched_refs
