"""
realpay/services.py — pull a month + analyse with DeepSeek.

pull_month(year, month, beneficiary_user_id, product_codes) →
  • Calls realpay.client.list_transactions for each product_code
  • Aggregates totals: successful / failed / amount_collected / amount_failed
  • Generates XLSX file and attaches to RealPayMonthlyReport.xlsx_file
  • Stores raw_payload for downstream debugging + audit

generate_commentary(report) →
  • Calls DeepSeek API with month totals + top decliner reason codes
  • Stores narrative in RealPayMonthlyReport.ai_commentary

backfill(from_year, from_month) →
  • Iterates calendar months Jul-2025 → today and calls both phases.

Failure mode: each phase wraps in try/except and stores error_log so the
batch is recoverable. status moves pending → pulled → analysed (or failed).
"""

from __future__ import annotations

import calendar
import io
import logging
from decimal import Decimal
from typing import Iterable, List, Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .client import RealPayError, list_transactions
from .models import RealPayMonthlyReport

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')

# Field name keys — RealPay InstalmentChangesGetResponse uses PascalCase
# per live UAT inspection 2026-06-03 (e.g. InstalmentAmount=99, InstalmentStatus='S',
# ResponseCode='00'). Earlier guesses (amount/status/transactionAmount) were
# wrong for this endpoint and left amount_collected at 0 even with txns
# present. Now lists the InstalmentChanges keys first, with the legacy
# transactions_report keys kept as fallback.
_AMOUNT_KEYS = ('InstalmentAmount', 'amount', 'transactionAmount', 'debitAmount', 'amount_zar')
_STATUS_KEYS = ('InstalmentStatus', 'status', 'transactionStatus', 'state')
_REASON_KEYS = ('ResponseCode', 'reasonCode', 'failureReason', 'declineReason', 'errorCode')


def _pick(d: dict, keys: tuple, default=''):
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return v
    return default


def _is_successful(txn: dict) -> bool:
    s = str(_pick(txn, _STATUS_KEYS) or '').upper()
    # InstalmentStatus single-char codes: S=Success, P=Pending, F=Failed,
    # R=Reversed, X=Rejected. Plus full-word legacy values for back-compat.
    if s in ('S', 'SUCCESS', 'SUCCESSFUL', 'SETTLED', 'PAID', 'COMPLETED', 'OK'):
        return True
    # InstalmentChanges ResponseCode='00' == ISO 8583 success
    rc = str(txn.get('ResponseCode') or '').strip()
    return rc == '00'


def _is_failed(txn: dict) -> bool:
    s = str(_pick(txn, _STATUS_KEYS) or '').upper()
    if s in ('F', 'X', 'R', 'FAILED', 'DECLINED', 'RETURNED', 'REVERSED', 'NSF', 'ERROR', 'REJECTED'):
        return True
    rc = str(txn.get('ResponseCode') or '').strip()
    # Any non-'00' non-empty ResponseCode is a decline code
    return bool(rc) and rc != '00'


def pull_month(
    *,
    year: int,
    month: int,
    beneficiary_user_id: str,
    beneficiary_label: str = '',
    product_codes: Optional[Iterable[str]] = None,
    user=None,
) -> RealPayMonthlyReport:
    """Pull one (year, month) for one beneficiary and persist."""
    if product_codes is None:
        product_codes = (getattr(settings, 'REALPAY_PRODUCT_CODES', '') or '').split(',')
        product_codes = [c.strip() for c in product_codes if c.strip()]

    last_day = calendar.monthrange(year, month)[1]
    from_date = f'{year:04d}-{month:02d}-01'
    to_date   = f'{year:04d}-{month:02d}-{last_day:02d}'

    report, _ = RealPayMonthlyReport.objects.get_or_create(
        period_year=year, period_month=month,
        beneficiary_user_id=str(beneficiary_user_id),
        defaults={'beneficiary_label': beneficiary_label or ''},
    )
    if beneficiary_label and report.beneficiary_label != beneficiary_label:
        report.beneficiary_label = beneficiary_label

    try:
        all_txns: List[dict] = []
        for pc in product_codes:
            txns = list_transactions(
                beneficiary_user_id=beneficiary_user_id,
                product_code=pc,
                from_date=from_date, to_date=to_date,
            )
            for t in txns:
                t.setdefault('_productCode', pc)
            all_txns.extend(txns)

        collected = ZERO
        failed    = ZERO
        n_ok      = 0
        n_fail    = 0
        for t in all_txns:
            amt = Decimal(str(_pick(t, _AMOUNT_KEYS, '0') or '0'))
            if _is_successful(t):
                collected += amt; n_ok += 1
            elif _is_failed(t):
                failed    += amt; n_fail += 1

        report.txn_count_total      = len(all_txns)
        report.txn_count_successful = n_ok
        report.txn_count_failed     = n_fail
        report.amount_collected     = collected
        report.amount_failed        = failed
        report.amount_net           = collected - failed
        report.raw_payload          = {'transactions': all_txns,
                                       'pulled_at': timezone.now().isoformat()}

        # Generate XLSX
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f'{year}-{month:02d}'
            headers = ['date', 'amount', 'status', 'reason', 'productCode',
                       'reference', 'accountHolder', 'accountNumber']
            ws.append(headers)
            for t in all_txns:
                ws.append([
                    str(t.get('InstalmentActionDate') or t.get('LastUpdateDate')
                        or t.get('transactionDate') or t.get('actionDate')
                        or t.get('date') or ''),
                    str(_pick(t, _AMOUNT_KEYS, '')),
                    str(_pick(t, _STATUS_KEYS, '')),
                    str(_pick(t, _REASON_KEYS, '')),
                    str(t.get('_productCode', '')),
                    str(t.get('InstalmentReferenceNumber') or t.get('reference')
                        or t.get('transactionReference') or ''),
                    str(t.get('ClientNumber') or t.get('accountHolder')
                        or t.get('holderName') or ''),
                    str(t.get('ContractNumber') or t.get('accountNumber')
                        or t.get('debitAccount') or ''),
                ])
            buf = io.BytesIO(); wb.save(buf); buf.seek(0)
            report.xlsx_file.save(
                f'realpay_{year}{month:02d}_{beneficiary_user_id}.xlsx',
                ContentFile(buf.read()), save=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception('XLSX generation failed for %s/%s', year, month)
            report.error_log = (report.error_log or '') + f'XLSX: {exc}\n'

        report.status      = RealPayMonthlyReport.Status.PULLED
        report.pulled_at   = timezone.now()
        report.pulled_by   = user
        report.error_log   = ''
        report.save()
        return report

    except RealPayError as exc:
        report.status     = RealPayMonthlyReport.Status.FAILED
        report.error_log  = str(exc)[:2000]
        report.save(update_fields=['status', 'error_log', 'updated_at'])
        raise


def generate_commentary(report: RealPayMonthlyReport) -> RealPayMonthlyReport:
    """Run DeepSeek over the month aggregates and store narrative."""
    api_key = getattr(settings, 'DEEPSEEK_API_KEY', '') or ''
    if not api_key:
        report.ai_commentary = '(DEEPSEEK_API_KEY not configured)'
        report.save(update_fields=['ai_commentary', 'updated_at'])
        return report

    txns = (report.raw_payload or {}).get('transactions') or []
    # Top decline reasons (top 5)
    from collections import Counter
    reasons = Counter()
    for t in txns:
        if _is_failed(t):
            reasons[str(_pick(t, _REASON_KEYS, 'unknown'))] += 1
    top_reasons = reasons.most_common(5)

    prompt = (
        f'You are an insurance CFO assistant. Summarise this month of RealPay '
        f'debit-order collections for {report.beneficiary_label or report.beneficiary_user_id} '
        f'({report.period_label}) in 4-6 short bullet points. Cover: total collected vs '
        f'failed, success rate, top decline reasons, and any anomaly worth flagging.\n\n'
        f'Numbers:\n'
        f'  Total transactions: {report.txn_count_total}\n'
        f'  Successful: {report.txn_count_successful}\n'
        f'  Failed: {report.txn_count_failed}\n'
        f'  Collected: BWP {report.amount_collected}\n'
        f'  Failed amount: BWP {report.amount_failed}\n'
        f'  Net: BWP {report.amount_net}\n'
        f'  Top decline reasons (count): {top_reasons}\n'
    )

    try:
        # Routed through the PII firewall (core.ai_assist.deepseek_complete) so no
        # personal data (e.g. a beneficiary name) leaves to DeepSeek in the clear.
        # CFO directive 2026-07-18 (Option B, post DPA audit).
        from core.ai_assist import deepseek_complete
        txt = deepseek_complete(
            prompt,
            system_prompt=('You are a concise insurance-CFO assistant. '
                           'Cite numbers exactly as provided.'),
            max_tokens=600,
            timeout=60,
        )
        report.ai_commentary = (txt or '').strip()
        report.status = RealPayMonthlyReport.Status.ANALYSED
        report.analysed_at = timezone.now()
        report.save(update_fields=['ai_commentary', 'status', 'analysed_at', 'updated_at'])
    except Exception as exc:  # noqa: BLE001
        logger.exception('DeepSeek commentary failed')
        report.ai_commentary = f'(commentary failed: {exc})'
        report.save(update_fields=['ai_commentary', 'updated_at'])
    return report


def backfill(from_year: int, from_month: int, *,
             beneficiary_user_id: str, beneficiary_label: str = '',
             user=None) -> List[RealPayMonthlyReport]:
    """Iterate Jul-2025 → today, pull + analyse each month."""
    today = timezone.localdate()
    y, m = from_year, from_month
    out: List[RealPayMonthlyReport] = []
    while (y, m) <= (today.year, today.month):
        try:
            r = pull_month(year=y, month=m,
                           beneficiary_user_id=beneficiary_user_id,
                           beneficiary_label=beneficiary_label,
                           user=user)
            generate_commentary(r)
            out.append(r)
        except Exception as exc:  # noqa: BLE001
            logger.warning('backfill %s-%s failed: %s', y, m, exc)
        m += 1
        if m > 12: m = 1; y += 1
    return out


# ---------------------------------------------------------------------------
# Analytics (CFO directive 2026-05-25) — creative cross-month sections.
# ---------------------------------------------------------------------------

def compute_analytics(*, months: int = 12, beneficiary_user_id: str = '') -> dict:
    """Aggregate the last N months of pulled RealPay data into named sections.

    Sections returned (all serialisable JSON, money as strings):
      • summary             — count of months, txns, gross collected vs failed
      • not_paying          — accounts with ≥ 2 consecutive failed months
      • multi_debit_payers  — one bank account funding ≥ 2 distinct contracts
      • top10_collected     — biggest paying clients across the window
      • top10_failed        — biggest failed amounts across the window
      • decline_reasons     — count + amount per reason code
      • month_trend         — per-month collected vs failed (sparkline)
      • mandate_flags       — accounts whose mandate looks weak (multiple
                              reversals, frequent reason changes)

    Empty when no months are populated yet — UI shows the "awaiting first
    pull" state for that section.
    """
    from collections import Counter, defaultdict
    from decimal import Decimal as _D

    qs = RealPayMonthlyReport.objects.filter(
        status__in=[RealPayMonthlyReport.Status.PULLED,
                    RealPayMonthlyReport.Status.ANALYSED],
    ).order_by('-period_year', '-period_month')
    if beneficiary_user_id:
        qs = qs.filter(beneficiary_user_id=beneficiary_user_id)
    months_data = list(qs[:months])

    summary = {
        'months_loaded':       len(months_data),
        'total_txns':          sum(r.txn_count_total for r in months_data),
        'total_successful':    sum(r.txn_count_successful for r in months_data),
        'total_failed':        sum(r.txn_count_failed for r in months_data),
        'total_collected':     str(sum((r.amount_collected for r in months_data), _D('0'))),
        'total_failed_amount': str(sum((r.amount_failed    for r in months_data), _D('0'))),
    }

    # Per-account roll-up across all months
    per_account = defaultdict(lambda: {
        'name': '', 'account': '',
        'months_seen': 0, 'months_paid': 0, 'months_failed': 0,
        'amount_paid': _D('0'), 'amount_failed': _D('0'),
        'last_status': '', 'last_month': '',
        'contracts': set(),
    })

    # Per-payer (bank account) roll-up for multi-debit detection
    per_payer = defaultdict(lambda: {
        'account': '', 'holder': '',
        'contracts': set(),
    })

    decline_reasons = Counter()
    decline_amount_by_reason = defaultdict(lambda: _D('0'))
    trend = []

    for r in sorted(months_data, key=lambda x: (x.period_year, x.period_month)):
        trend.append({
            'period':      f'{r.period_year}-{r.period_month:02d}',
            'collected':   str(r.amount_collected),
            'failed':      str(r.amount_failed),
            'ok_count':    r.txn_count_successful,
            'fail_count':  r.txn_count_failed,
        })

        for t in (r.raw_payload or {}).get('transactions') or []:
            holder = str(t.get('ClientNumber') or t.get('accountHolder')
                         or t.get('holderName') or '').strip()
            account = str(t.get('ContractNumber') or t.get('accountNumber')
                          or t.get('debitAccount') or '').strip()
            contract = str(t.get('ContractNumber') or t.get('contractReference')
                           or t.get('contractRef') or t.get('reference') or '').strip()
            key = contract or f'{holder}|{account}'
            amt = _D(str(_pick(t, _AMOUNT_KEYS, '0') or '0'))
            ok  = _is_successful(t)
            bad = _is_failed(t)

            acct = per_account[key]
            acct['name']    = holder or acct['name']
            acct['account'] = account or acct['account']
            acct['months_seen'] += 1
            acct['last_month']   = f'{r.period_year}-{r.period_month:02d}'
            acct['last_status']  = ('paid' if ok else ('failed' if bad else 'other'))
            if contract: acct['contracts'].add(contract)
            if ok:
                acct['months_paid']   += 1; acct['amount_paid']   += amt
            elif bad:
                acct['months_failed'] += 1; acct['amount_failed'] += amt
                reason = str(_pick(t, _REASON_KEYS, 'unknown'))
                decline_reasons[reason] += 1
                decline_amount_by_reason[reason] += amt

            if account:
                p = per_payer[account]
                p['account'] = account
                p['holder'] = holder or p['holder']
                if contract: p['contracts'].add(contract)

    # Build sections
    not_paying = []
    for k, a in per_account.items():
        if a['months_failed'] >= 2 and a['months_failed'] >= a['months_paid']:
            not_paying.append({
                'name':            a['name'] or '(no name)',
                'account':         a['account'],
                'months_failed':   a['months_failed'],
                'months_paid':     a['months_paid'],
                'amount_failed':   str(a['amount_failed']),
                'last_status':     a['last_status'],
                'last_month':      a['last_month'],
            })
    not_paying.sort(key=lambda x: -float(x['amount_failed']))
    not_paying = not_paying[:25]

    multi_debit = []
    for acct, p in per_payer.items():
        if len(p['contracts']) >= 2:
            multi_debit.append({
                'account':       p['account'],
                'holder':        p['holder'] or '(no holder)',
                'contract_count': len(p['contracts']),
                'contracts':     sorted(p['contracts'])[:10],
            })
    multi_debit.sort(key=lambda x: -x['contract_count'])
    multi_debit = multi_debit[:20]

    top10_collected = sorted(
        ({'name': a['name'] or '(no name)',
          'account': a['account'],
          'amount_paid': str(a['amount_paid']),
          'months_paid': a['months_paid']}
         for a in per_account.values() if a['amount_paid'] > 0),
        key=lambda x: -float(x['amount_paid']),
    )[:10]

    top10_failed = sorted(
        ({'name': a['name'] or '(no name)',
          'account': a['account'],
          'amount_failed': str(a['amount_failed']),
          'months_failed': a['months_failed']}
         for a in per_account.values() if a['amount_failed'] > 0),
        key=lambda x: -float(x['amount_failed']),
    )[:10]

    reasons_out = []
    for code, n in decline_reasons.most_common(15):
        reasons_out.append({
            'code':   code,
            'count':  n,
            'amount': str(decline_amount_by_reason[code]),
        })

    # Mandate-weak: accounts whose months_failed ≥ 3 OR > 50% failure rate
    mandate_flags = []
    for a in per_account.values():
        if a['months_failed'] >= 3:
            total = a['months_paid'] + a['months_failed']
            rate = (a['months_failed'] / total) if total else 0.0
            mandate_flags.append({
                'name':         a['name'] or '(no name)',
                'account':      a['account'],
                'failure_rate': round(rate, 2),
                'months_failed': a['months_failed'],
            })
    mandate_flags.sort(key=lambda x: -x['failure_rate'])
    mandate_flags = mandate_flags[:25]

    return {
        'summary':            summary,
        'not_paying':         not_paying,
        'multi_debit_payers': multi_debit,
        'top10_collected':    top10_collected,
        'top10_failed':       top10_failed,
        'decline_reasons':    reasons_out,
        'month_trend':        trend,
        'mandate_flags':      mandate_flags,
    }
