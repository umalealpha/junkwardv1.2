"""
integrations/instant_insurance_views.py — the Instant Insurance book, live.

GET /api/v1/integrations/instant-insurance-summary/?asof=YYYY-MM-DD

Built to Build Brief 1 (Instant Insurance Summary Statistics, Data department,
2 September 2026), Stage D — the two book panels with their exception box on the
same screen. Every figure is read live from the Graphite read replica on each call.

WHAT THIS IS NOT. It is deliberately NOT the daily snapshot table the brief asks for
in Stage C. A snapshot has to wait for every paygate load to finish, record which
loads it saw, and never restate — and that design needs the receipts table signed off
first. So this reads live and says so on its face. Nobody should quote a figure from
here as a frozen month-end number.

THE RULES IT APPLIES, ALL SIGNED OFF
  * ACTIVE — status Active AND inception on or before the as-of date. Chosen by the
    CFO on 8 September 2026. He was shown that it counts policies whose cover has
    already expired (21,726 of 27,872 at 31 August) and chose it for continuity with
    what has been reported before. Those are therefore reported BESIDE the headline
    as a named exception, never netted out of it.
  * PAYING — at least one successful collection in the three calendar months ending
    on the as-of date, assessed only on policies that already pass the ACTIVE test.
    The rate is never returned without its denominator.
  * The two books are never summed into one client total. Instant Insurance is the
    five products; Motor Comprehensive stands alone.

TWO TRAPS THIS CODE RESPECTS (both proven on the live data 8-Sep-2026)
  * Products are matched on the product record, never on `products.type` — that
    column is NULL for Hospital Cashback (8,696 policies), so a type filter silently
    drops the whole product.
  * `payment_transactions` is a payment LEDGER carrying future-dated scheduled
    instalments out to 2058, and its success status has three spellings. Both are
    handled; see integrations.realpay_ledger_reconcile for the detail.

READ ONLY, and no customer information: counts and product names only. Every
statement goes through integrations.graphite_ro, which refuses a non-replica host and
refuses anything that is not a read.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from integrations import graphite_ro
from django.utils import timezone

log = logging.getLogger(__name__)

#: Build Brief 1's two reporting books, by the product name as actually stored.
#: "Mobile and electronic device insurance" is stored with a lower-case e — the brief
#: calls it "Mobile and Electronic", so a hand-typed filter would miss it.
INSTANT_PRODUCTS = (
    'Third Party Car Insurance',
    'Accidental Death Insurance',
    'Legal Insurance',
    'Hospital Cashback Insurance',
    'Mobile and electronic device insurance',
)
MOTOR_PRODUCT = 'Motor Comprehensive'

LEDGER_SUCCESS = ('SUCCESS', 'SUCCESSFUL', 'PAID')
PAYING_WINDOW_MONTHS = 3


def _month_floor(d: datetime.date) -> datetime.date:
    return d.replace(day=1)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    y, m = divmod((d.year * 12 + d.month - 1) + n, 12)
    return datetime.date(y, m + 1, 1)


def _ph(values) -> str:
    return ', '.join(['%s'] * len(values))


def _book_of(name: str) -> str:
    return 'INSTANT' if name in INSTANT_PRODUCTS else 'MOTOR'


def summary(asof: Optional[datetime.date] = None) -> Dict[str, Any]:
    """The live book: active, paying and the exception buckets, per product.

    Never raises — an unreachable replica returns {'available': False, 'reason': ...}
    so a screen degrades to "data unavailable" instead of an error page.
    """
    asof = asof or timezone.localdate()
    paying_from = _month_floor(_add_months(_month_floor(asof), -(PAYING_WINDOW_MONTHS - 1)))
    products = (*INSTANT_PRODUCTS, MOTOR_PRODUCT)

    # ACTIVE per the signed rule, plus PAYING and the exception buckets, in one pass.
    sql = (
        "SELECT p.name AS product, "
        "  COUNT(*) AS active, "
        "  SUM(CASE WHEN EXISTS ("
        "      SELECT 1 FROM payment_transactions pt "
        "      WHERE pt.policy_id = pol.id "
        f"       AND UPPER(TRIM(pt.status)) IN ({_ph(LEDGER_SUCCESS)}) "
        "        AND pt.new_payment_date >= %s AND pt.new_payment_date <= %s"
        "  ) THEN 1 ELSE 0 END) AS paying, "
        "  SUM(CASE WHEN pol.expiry_date IS NOT NULL AND pol.expiry_date < %s "
        "      THEN 1 ELSE 0 END) AS expired_but_active, "
        "  SUM(CASE WHEN pol.expiry_date IS NULL THEN 1 ELSE 0 END) AS no_expiry_date, "
        "  SUM(CASE WHEN COALESCE(pol.isPaymentCancel, 0) = 1 THEN 1 ELSE 0 END) "
        "      AS payment_cancelled "
        "FROM policies pol "
        "JOIN products p ON p.id = pol.product_id "
        f"WHERE p.name IN ({_ph(products)}) "
        "  AND pol.status = 1 "
        "  AND pol.policyActivatedDate IS NOT NULL "
        "  AND pol.policyActivatedDate <= %s "
        "GROUP BY p.name"
    )
    params = [*LEDGER_SUCCESS, paying_from, asof, asof, *products, asof]
    try:
        with graphite_ro.connection() as cx:
            with cx.cursor() as cur:
                cur.execute(sql, params)
                raw = list(cur.fetchall())
    except Exception as exc:                    # noqa: BLE001 — degrade, never 500
        log.warning('instant insurance summary unavailable: %s', exc.__class__.__name__)
        return {'available': False, 'reason': exc.__class__.__name__}

    books: Dict[str, Dict[str, Any]] = {
        'INSTANT': {'book': 'Instant Insurance', 'products': [],
                    'active': 0, 'paying': 0},
        'MOTOR': {'book': 'Motor Comprehensive', 'products': [],
                  'active': 0, 'paying': 0},
    }
    exceptions = {'expired_but_active': 0, 'no_expiry_date': 0, 'payment_cancelled': 0}
    for r in raw:
        name = r['product']
        active = int(r['active'] or 0)
        paying = int(r['paying'] or 0)
        b = books[_book_of(name)]
        b['products'].append({
            'product': name,
            'active': active,
            'paying': paying,
            'paying_rate_pct': round(paying / active * 100, 1) if active else None,
            'expired_but_active': int(r['expired_but_active'] or 0),
            'no_expiry_date': int(r['no_expiry_date'] or 0),
            'payment_cancelled': int(r['payment_cancelled'] or 0),
        })
        b['active'] += active
        b['paying'] += paying
        for k in exceptions:
            exceptions[k] += int(r[k] or 0)

    out: List[Dict[str, Any]] = []
    for key in ('INSTANT', 'MOTOR'):
        b = books[key]
        b['products'].sort(key=lambda x: -x['active'])
        b['paying_rate_pct'] = (round(b['paying'] / b['active'] * 100, 1)
                                if b['active'] else None)
        out.append(b)

    return {
        'available': True,
        'asof': asof.isoformat(),
        'paying_window': {'from': paying_from.isoformat(), 'to': asof.isoformat(),
                          'months': PAYING_WINDOW_MONTHS},
        'books': out,
        'exceptions': exceptions,
        'rules': {
            'active': ('Status Active and inception on or before the as-of date '
                       '(signed by the CFO, 8 September 2026).'),
            'paying': (f'At least one successful collection in the {PAYING_WINDOW_MONTHS} '
                       'calendar months ending on the as-of date, assessed only on '
                       'policies that already pass the Active test.'),
            'books': 'The two books are never summed into a single total.',
        },
        'caveats': [
            ('This reads live and is not a frozen month-end snapshot. The daily '
             'snapshot the brief asks for needs the receipts table signed off first.'),
            ('Paying is understated while RealPay collections are missing from the '
             'payment ledger — see the reconciliation panel on this screen.'),
        ],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def instant_insurance_summary(request):
    asof = None
    raw = (request.query_params.get('asof') or '').strip()
    if raw:
        try:
            asof = datetime.date.fromisoformat(raw[:10])
        except ValueError:
            asof = None
    return Response(summary(asof=asof))
