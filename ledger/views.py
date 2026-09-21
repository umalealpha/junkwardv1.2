"""
ledger/views.py

Trial Balance API endpoint.
GET /api/ledger/trial-balance/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
"""

import datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Account, JournalEntryLine


ZERO = Decimal('0.00')


def _build_trial_balance(start_date=None, end_date=None):
    """
    Return trial balance data for the given date range.
    Uses 2 aggregate queries regardless of account count.

    Opening balance = net of all posted lines BEFORE start_date.
    Period totals   = posted lines with entry_date in [start_date, end_date].
    Closing balance = opening + period_debits - period_credits.
    """
    # --- pre-period data (opening balances) ---
    pre_data = {}
    if start_date:
        qs = (
            JournalEntryLine.objects
            .filter(
                journal_entry__status='posted',
                journal_entry__entry_date__lt=start_date,
            )
            .values('account_id')
            .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
        )
        for row in qs:
            pre_data[row['account_id']] = (row['dr'] or ZERO, row['cr'] or ZERO)

    # --- in-period data ---
    period_qs = JournalEntryLine.objects.filter(journal_entry__status='posted')
    if start_date:
        period_qs = period_qs.filter(journal_entry__entry_date__gte=start_date)
    if end_date:
        period_qs = period_qs.filter(journal_entry__entry_date__lte=end_date)

    period_data = {}
    for row in period_qs.values('account_id').annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp')):
        period_data[row['account_id']] = (row['dr'] or ZERO, row['cr'] or ZERO)

    # --- combine ---
    all_ids  = set(pre_data) | set(period_data)
    accounts = (
        Account.objects
        .filter(pk__in=all_ids)
        .select_related('parent')
        .order_by('code')
    )

    rows         = []
    total_debits = ZERO
    total_credits = ZERO

    for acc in accounts:
        pre_dr,  pre_cr  = pre_data.get(acc.pk, (ZERO, ZERO))
        per_dr,  per_cr  = period_data.get(acc.pk, (ZERO, ZERO))
        opening  = pre_dr - pre_cr
        closing  = opening + per_dr - per_cr
        rows.append({
            'code':            acc.code,
            'name':            acc.name,
            'account_type':    acc.account_type,
            'opening_balance': str(opening),
            'total_debits':    str(per_dr),
            'total_credits':   str(per_cr),
            'closing_balance': str(closing),
        })
        total_debits  += per_dr
        total_credits += per_cr

    return {
        'period': {
            'start_date': str(start_date) if start_date else None,
            'end_date':   str(end_date)   if end_date   else None,
        },
        'accounts': rows,
        'totals': {
            'total_debits':  str(total_debits),
            'total_credits': str(total_credits),
            'balanced':      total_debits == total_credits,
        },
    }


class TrialBalanceView(APIView):
    """
    Returns the trial balance for a given date range.

    Query params:
      start_date  YYYY-MM-DD  (optional — omit for all-time)
      end_date    YYYY-MM-DD  (optional — defaults to today)
    """

    def get(self, request):
        raw_start = request.query_params.get('start_date')
        raw_end   = request.query_params.get('end_date')

        try:
            start_date = datetime.date.fromisoformat(raw_start) if raw_start else None
            # Default to BOTSWANA's today (settings.TIME_ZONE). date.today() is
            # the UTC date on a UTC box, so a TB pulled between 00:00 and 02:00
            # Gaborone silently excluded today's postings — a wrong number, with
            # no error to warn anyone. Same midnight window as f9ff6d56.
            end_date   = datetime.date.fromisoformat(raw_end)   if raw_end   else timezone.localdate()
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        data = _build_trial_balance(start_date, end_date)
        return Response(data)
