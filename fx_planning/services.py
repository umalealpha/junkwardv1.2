"""FX Payment Planning — the engine.

Four jobs, all deterministic and explainable (the CFO must be able to defend
every predicted number to the board):

1. **Import** an FNB "Forex" history download (PDF / CSV / Excel) into
   ``ForexPaymentHistory`` — de-duplicated on the FNB reference.
2. **Detect** recurring payees from that history (``detect_recurring``): who
   pays whom, in which currency, how often, and roughly how much.
3. **Materialise** the forward calendar (``materialise_calendar``): project each
   active recurring payee forward, with a pula-cost estimate from the approved
   BoB rate table (control FX-001, reusing ``payments.resolve_fx_rate``).
4. **Raise** a planned line into the existing ``taskboard.PaymentRequest`` flow
   so it enters the finance → CFO → FNB authorisation chain unchanged.

No money moves here. This module observes, forecasts and plans.
"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from .models import (
    ForexPaymentHistory, ForexPaymentImport, PlannedForexPayment,
    RecurringForexPayee,
)
from django.utils import timezone

# Payment-type tokens seen on the FNB Forex screen; used to split the wrapped
# "reference - beneficiary" cell from the rest of a row.
_PAY_TYPES = r'Once-Off|Beneficiary|Recurring|Future[ -]?dated|Scheduled'

_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}

# Rate-risk buffer (control FX-002, CFO 2026-08-24). The pula cost of a foreign
# payment moves with the exchange rate between now and the value date. We show
# the planned pula at the current rate AND stressed by these percentages, so the
# team funds enough pula for a weaker pula. Overridable via settings.FX_STRESS_PCTS.
DEFAULT_FX_STRESS_PCTS = (Decimal('5'), Decimal('10'))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def normalise_key(name: str) -> str:
    """A stable match key for a beneficiary: upper-case, punctuation → space,
    whitespace collapsed. 'MAKSURE  Risk-Solutions' → 'MAKSURE RISK SOLUTIONS'."""
    if not name:
        return ''
    up = re.sub(r'[^A-Za-z0-9]+', ' ', name.upper())
    return re.sub(r'\s+', ' ', up).strip()


def _parse_eu_amount(raw: str) -> Decimal | None:
    """FNB prints amounts European-style: '40 545,45' / '250 000,00' / '220,03'.
    Spaces (incl. non-breaking/thin) are thousands separators; comma is decimal."""
    if not raw:
        return None
    cleaned = raw.replace(' ', '').replace(' ', '').replace(' ', '')
    cleaned = cleaned.replace(',', '.')
    try:
        return Decimal(cleaned)
    except Exception:  # noqa: BLE001
        return None


def _parse_date(raw: str):
    """'29 Apr 2026' → date(2026, 4, 29). Returns None on anything unexpected."""
    if not raw:
        return None
    m = re.match(r'\s*(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})\s*$', raw)
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2).title()), int(m.group(3))
    if not mon:
        return None
    try:
        return date(year, mon, day)
    except ValueError:
        return None


def _median_decimal(values) -> Decimal:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return Decimal('0')
    mid = n // 2
    if n % 2:
        return vals[mid]
    return ((vals[mid - 1] + vals[mid]) / 2).quantize(Decimal('0.01'), ROUND_HALF_UP)


def fx_stress_pcts() -> list[Decimal]:
    """The stress percentages for the worst-case pula view (settings-overridable).
    Always positive, sorted low→high; falls back to the default on bad config."""
    from django.conf import settings
    raw = getattr(settings, 'FX_STRESS_PCTS', None) or DEFAULT_FX_STRESS_PCTS
    out = []
    for v in raw:
        try:
            d = Decimal(str(v))
        except Exception:  # noqa: BLE001
            continue
        if d > 0:
            out.append(d)
    return sorted(out) or list(DEFAULT_FX_STRESS_PCTS)


def stress_bwp(bwp, pct) -> Decimal:
    """Pula cost if the rate worsened by ``pct`` percent (pula weakens)."""
    base = Decimal(bwp or 0)
    factor = Decimal('1') + (Decimal(pct) / Decimal('100'))
    return (base * factor).quantize(Decimal('0.01'), ROUND_HALF_UP)


def _regularity(gaps) -> float:
    """0..1 — how evenly spaced the payments are (1 = perfectly regular). Based on
    the coefficient of variation of the gaps; 'only one interval' is neutral."""
    if len(gaps) < 2:
        return 0.5
    mean = statistics.mean(gaps)
    if mean <= 0:
        return 0.0
    cv = statistics.pstdev(gaps) / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _confidence(occurrences: int, gaps, n_months: int) -> int:
    """0..100. Rewards MORE observations and MORE regular spacing — not just the
    number of calendar months. The old ``months * 25`` scored a mere 4-month payee
    at 100%; this weights 60% on how much data, 40% on how regular it is."""
    if n_months < 2:
        return 0
    occ_score = min(occurrences, 6) / 6.0        # 6+ sightings = full data score
    reg_score = _regularity(gaps)
    return int(round(100 * (0.6 * occ_score + 0.4 * reg_score)))


def _safe_date(year: int, month: int, day: int) -> date:
    """Clamp day to the month length (so 'day 31' in April → 30 Apr)."""
    while True:
        try:
            return date(year, month, min(day, 28) if day > 28 else day) \
                if day <= 28 else date(year, month, day)
        except ValueError:
            day -= 1


# ---------------------------------------------------------------------------
# 1. Parsing an FNB forex download
# ---------------------------------------------------------------------------

def parse_forex_text(text: str) -> list[dict]:
    """Parse the plain text of an FNB 'Forex' history page into row dicts.

    A record wraps across lines and the DATA line lands in the MIDDLE of the
    beneficiary name, e.g.::

        9753846 - ODOO KE
        Once-Off 21 May 2026 21 May 2026 60000003 USD 5 712,00 Complete
        LTD

    So the beneficiary is the text BEFORE the payment-type token ("ODOO KE")
    joined to the text AFTER the status word ("LTD"). We anchor each record on
    its 6–8 digit reference, find the payment-type token, read the data fields
    up to the status, and treat the leftover on both sides as the name.
    """
    rows: list[dict] = []
    # Split into records: from a "<ref> - " marker up to the next one (or EOF).
    record_re = re.compile(
        r'(\d{6,8})\s*-\s*(.*?)(?=(?:\d{6,8}\s*-\s*)|\Z)', re.S)
    remainder_re = re.compile(
        r'(?P<capture>\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+'
        r'(?P<value>\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+'
        r'(?P<account>\d{6,})\s+'
        r'(?P<currency>[A-Z]{3})\s+'
        r'(?P<amount>[\d   ]*\d[\d   ]*,\d{2})\s+'
        r'(?P<status>In\ Progress|In\ Process|[A-Za-z]+)', re.S)

    for rec in record_re.finditer(text):
        ref = rec.group(1).strip()
        body = rec.group(2)
        pt = re.search(r'\b(' + _PAY_TYPES + r')\b', body)
        if not pt:
            continue
        head = body[:pt.start()]                 # beneficiary text before the data line
        rest = body[pt.end():]
        m = remainder_re.search(rest)
        if not m:
            continue
        amount = _parse_eu_amount(m.group('amount'))
        if amount is None:
            continue
        tail = rest[m.end():]                     # beneficiary text after the status word
        beneficiary = re.sub(r'\s+', ' ', f'{head} {tail}').strip()
        rows.append({
            'reference':     ref,
            'beneficiary':   beneficiary,
            'payment_type':  re.sub(r'\s+', ' ', pt.group(1)).strip(),
            'capture_date':  _parse_date(m.group('capture')),
            'value_date':    _parse_date(m.group('value')),
            'source_account': m.group('account').strip(),
            'currency':      m.group('currency').strip(),
            'amount':        amount,
            'status':        m.group('status').strip(),
        })
    return rows


def parse_forex_pdf(data: bytes) -> list[dict]:
    """FNB forex download as PDF (born-digital). Reuses the pdfplumber path
    that taskboard/fnb_reconcile.py already relies on."""
    try:
        import pdfplumber
        out = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:60]:
                out.append(page.extract_text() or '')
        return parse_forex_text('\n'.join(out))
    except Exception:  # noqa: BLE001 — a bad upload must never 500
        return []


def parse_forex_tabular(data: bytes, filename: str) -> list[dict]:
    """CSV / XLSX export of the same screen. Best-effort header mapping."""
    name = (filename or '').lower()
    records: list[list] = []
    header: list[str] = []
    try:
        if name.endswith('.csv'):
            import csv
            txt = data.decode('utf-8-sig', errors='replace')
            reader = csv.reader(io.StringIO(txt))
            all_rows = list(reader)
        else:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.active
            all_rows = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    except Exception:  # noqa: BLE001
        return []
    if not all_rows:
        return []
    header = [str(c or '').strip().lower() for c in all_rows[0]]
    records = all_rows[1:]

    def col(*names):
        for i, h in enumerate(header):
            if any(n in h for n in names):
                return i
        return None

    ci = {
        'ref':   col('reference', 'ref'),
        'ben':   col('beneficiary', 'payee'),
        'ptype': col('payment type', 'type'),
        'cap':   col('capture'),
        'val':   col('value'),
        'acct':  col('account'),
        'ccy':   col('currency'),
        'amt':   col('amount'),
        'stat':  col('status'),
    }
    out = []
    for r in records:
        def g(key):
            i = ci[key]
            return '' if i is None or i >= len(r) or r[i] is None else str(r[i]).strip()
        ref = g('ref')
        # A combined "reference - beneficiary" cell (like the PDF) is supported.
        ben = g('ben')
        if not ref and ' - ' in ben:
            ref, ben = ben.split(' - ', 1)
            ref = ref.strip()
        amt = _parse_eu_amount(g('amt')) if g('amt') else None
        if amt is None:
            # amount may include the currency: "USD 5 712,00"
            am = re.search(r'([A-Z]{3})?\s*([\d  ]*\d[\d  ]*,\d{2}|\d+\.\d{2})', g('amt'))
            if am:
                amt = _parse_eu_amount(am.group(2)) or (
                    Decimal(am.group(2)) if am.group(2).replace('.', '').isdigit() else None)
        if not ref or amt is None:
            continue
        out.append({
            'reference':     ref,
            'beneficiary':   ben,
            'payment_type':  g('ptype'),
            'capture_date':  _parse_date(g('cap')),
            'value_date':    _parse_date(g('val')),
            'source_account': g('acct'),
            'currency':      (g('ccy') or '')[:3].upper(),
            'amount':        amt,
            'status':        g('stat'),
        })
    return out


def parse_upload(data: bytes, filename: str) -> list[dict]:
    name = (filename or '').lower()
    if name.endswith('.pdf'):
        return parse_forex_pdf(data)
    if name.endswith(('.csv', '.xlsx', '.xlsm')):
        return parse_forex_tabular(data, filename)
    # Unknown extension: try PDF then tabular.
    return parse_forex_pdf(data) or parse_forex_tabular(data, filename)


@transaction.atomic
def import_forex_file(data: bytes, filename: str, user=None) -> ForexPaymentImport:
    """Parse a download and store new history rows (de-duped on reference)."""
    parsed = parse_upload(data, filename)
    imp = ForexPaymentImport.objects.create(
        filename=filename or 'upload', uploaded_by=user, row_count=len(parsed))
    existing = set(ForexPaymentHistory.objects
                   .filter(reference__in=[p['reference'] for p in parsed])
                   .values_list('reference', flat=True))
    new_rows, dates = [], []
    for p in parsed:
        if p['reference'] in existing:
            continue
        existing.add(p['reference'])  # guard against dupes within one file
        new_rows.append(ForexPaymentHistory(
            reference=p['reference'],
            beneficiary=p['beneficiary'][:255],
            beneficiary_key=normalise_key(p['beneficiary'])[:255],
            payment_type=(p['payment_type'] or '')[:40],
            capture_date=p['capture_date'],
            value_date=p['value_date'],
            source_account=(p['source_account'] or '')[:40],
            currency=(p['currency'] or '')[:3],
            amount=p['amount'],
            status=(p['status'] or '')[:30],
            source_import=imp,
        ))
        if p['value_date']:
            dates.append(p['value_date'])
    ForexPaymentHistory.objects.bulk_create(new_rows)
    imp.imported_count = len(new_rows)
    if dates:
        imp.date_from, imp.date_to = min(dates), max(dates)
    imp.save(update_fields=['imported_count', 'date_from', 'date_to'])
    return imp


# ---------------------------------------------------------------------------
# 2. Recurring-payee detection
# ---------------------------------------------------------------------------

def _cadence_for(gap_days: float | None, months_active: int):
    C = RecurringForexPayee.Cadence
    if months_active < 2 or gap_days is None:
        return C.IRREGULAR
    if gap_days <= 45:
        return C.MONTHLY
    if gap_days <= 110:
        return C.QUARTERLY
    return C.IRREGULAR


def detect_recurring() -> int:
    """(Re)build ``RecurringForexPayee`` from history. Returns the row count.

    A payee seen in ≥2 distinct calendar months on a monthly/quarterly cadence is
    flagged ``active`` (it drives the forward calendar). Stats are always
    refreshed; a row a human has confirmed keeps its edited amount/day/active.
    """
    groups: dict[tuple, list[ForexPaymentHistory]] = {}
    for row in ForexPaymentHistory.objects.exclude(value_date=None):
        groups.setdefault((row.beneficiary_key, row.currency), []).append(row)

    count = 0
    for (key, ccy), rows in groups.items():
        if not key or not ccy:
            continue
        dates = sorted(r.value_date for r in rows)
        amounts = [r.amount for r in rows]
        months = {(d.year, d.month) for d in dates}
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = statistics.mean(gaps) if gaps else None
        cadence = _cadence_for(avg_gap, len(months))
        typical_amount = _median_decimal(amounts)
        amount_min, amount_max = min(amounts), max(amounts)
        typical_day = int(statistics.median([d.day for d in dates]))
        src_acct = Counter(r.source_account for r in rows if r.source_account).most_common(1)
        confidence = _confidence(len(rows), gaps, len(months))
        is_recurring = len(months) >= 2 and cadence in (
            RecurringForexPayee.Cadence.MONTHLY, RecurringForexPayee.Cadence.QUARTERLY)
        # Material but not clockwork → keep it VISIBLE as "watch / plan by hand"
        # rather than silently dropping it off the plan. A payee seen more than once
        # that we can't put on a cadence is exactly the lumpy payment worth flagging.
        watch = (not is_recurring) and len(rows) >= 2
        display = max((r.beneficiary for r in rows), key=len)  # fullest text seen

        payee, created = RecurringForexPayee.objects.get_or_create(
            beneficiary_key=key, currency=ccy,
            defaults=dict(
                display_name=display[:255], typical_amount=typical_amount,
                amount_min=amount_min, amount_max=amount_max,
                cadence=cadence, typical_day=typical_day,
                source_account=(src_acct[0][0] if src_acct else '')[:40],
                occurrences=len(rows), months_active=len(months),
                confidence=confidence, last_seen=max(dates),
                active=is_recurring, watch=watch,
            ),
        )
        # Always refresh the observed stats.
        payee.display_name = display[:255]
        payee.cadence = cadence
        payee.occurrences = len(rows)
        payee.months_active = len(months)
        payee.confidence = confidence
        payee.last_seen = max(dates)
        payee.amount_min = amount_min
        payee.amount_max = amount_max
        payee.source_account = (src_acct[0][0] if src_acct else '')[:40]
        # Amount / day / active / watch are learned only until a human confirms the row.
        if not payee.confirmed_by_id:
            payee.typical_amount = typical_amount
            payee.typical_day = typical_day
            payee.active = is_recurring
            payee.watch = watch
        payee.save()
        count += 1
    return count


# ---------------------------------------------------------------------------
# 3. Pula-cost estimate + forward calendar
# ---------------------------------------------------------------------------

def estimate_bwp(currency: str, amount: Decimal, on_date: date):
    """(rate, bwp_amount, is_estimate). Prefers the approved BoB rate (FX-001);
    falls back to the latest rate on/before the date, then the latest of all,
    flagging the result an estimate. (None, None, True) if no rate exists."""
    from payments.models import resolve_fx_rate
    rate = resolve_fx_rate(currency, on_date)   # approved only; 1 for BWP; None otherwise
    is_estimate = False
    if rate is None:
        from core.models import ExchangeRate
        row = (ExchangeRate.objects
               .filter(from_currency_id=currency, to_currency_id='BWP',
                       effective_date__lte=on_date)
               .order_by('-effective_date').first()
               or ExchangeRate.objects
               .filter(from_currency_id=currency, to_currency_id='BWP')
               .order_by('-effective_date').first())
        if row:
            rate, is_estimate = row.rate, True
    if rate is None:
        return None, None, True
    bwp = (amount * rate).quantize(Decimal('0.01'), ROUND_HALF_UP)
    return rate, bwp, is_estimate


def _future_dates(payee: RecurringForexPayee, start: date, horizon: date) -> list[date]:
    """Expected value dates for a payee within (start, horizon]."""
    C = RecurringForexPayee.Cadence
    step = 1 if payee.cadence == C.MONTHLY else 3 if payee.cadence == C.QUARTERLY else None
    if step is None:
        return []
    day = payee.typical_day or 28
    out, y, m = [], start.year, start.month
    for _ in range(24):  # safety cap
        d = _safe_date(y, m, day)
        if d > horizon:
            break
        if d > start:
            out.append(d)
        m += step
        while m > 12:
            m -= 12
            y += 1
    return out


@transaction.atomic
def materialise_calendar(weeks_ahead: int = 12, user=None, *, today: date | None = None) -> int:
    """Project every active recurring payee forward, creating/refreshing
    ``PlannedForexPayment`` rows. Returns the number of new rows created.

    Idempotent: a recurring payee has at most one line per value date
    (DB constraint), so re-running only fills gaps and refreshes estimates on
    lines still in PLANNED state."""
    today = today or timezone.localdate()
    horizon = today + timedelta(weeks=weeks_ahead)
    created = 0
    for payee in RecurringForexPayee.objects.filter(active=True):
        for d in _future_dates(payee, today, horizon):
            rate, bwp, est = estimate_bwp(payee.currency, payee.typical_amount, d)
            obj, was_created = PlannedForexPayment.objects.get_or_create(
                recurring_payee=payee, expected_value_date=d,
                driver=PlannedForexPayment.Driver.RECURRING,
                defaults=dict(
                    beneficiary=payee.display_name, beneficiary_key=payee.beneficiary_key,
                    currency=payee.currency, expected_amount=payee.typical_amount,
                    source_account=payee.source_account, estimated_rate=rate,
                    estimated_bwp=bwp, rate_is_estimate=est, created_by=user,
                ),
            )
            if was_created:
                created += 1
            elif obj.status == PlannedForexPayment.Status.PLANNED:
                obj.expected_amount = payee.typical_amount
                obj.currency = payee.currency
                obj.source_account = payee.source_account
                obj.estimated_rate, obj.estimated_bwp, obj.rate_is_estimate = rate, bwp, est
                obj.save(update_fields=[
                    'expected_amount', 'currency', 'source_account',
                    'estimated_rate', 'estimated_bwp', 'rate_is_estimate', 'updated_at'])
    return created


# ---------------------------------------------------------------------------
# 4. Raise a planned line into the existing PaymentRequest flow
# ---------------------------------------------------------------------------

@transaction.atomic
def raise_as_payment_request(planned: PlannedForexPayment, user):
    """Create a ``taskboard.PaymentRequest`` (category OTHER, pending finance
    sign-off) pre-filled from a planned line, and link it back. The money still
    moves only when finance signs off and the CFO authorises on FNB — this just
    stops the details being re-typed."""
    from taskboard.models import PaymentRequest
    from taskboard.payment_views import _next_ref

    if planned.payment_request_id:
        return planned.payment_request

    entity = 'Alpha Direct Insurance Company'
    who = (getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', '') or '')
    pr = PaymentRequest.objects.create(
        ref=_next_ref(entity),
        entity=entity,
        category=PaymentRequest.Category.OTHER,
        currency=planned.currency,
        subject=f'Foreign payment — {planned.beneficiary} ({planned.currency})',
        payee=planned.beneficiary[:200],
        line_items=[{
            'description': f'Planned {planned.currency} payment to {planned.beneficiary}',
            'gl_code': '', 'ref': '', 'amount': str(planned.expected_amount),
        }],
        total=planned.expected_amount,
        status=PaymentRequest.Status.PENDING_FINANCE,
        inputter=who[:160],
    )
    planned.payment_request = pr
    planned.status = PlannedForexPayment.Status.REQUESTED
    planned.save(update_fields=['payment_request', 'status', 'updated_at'])
    return pr


# ---------------------------------------------------------------------------
# 5. Accuracy back-test — did the forecast match reality?
# ---------------------------------------------------------------------------

def _months_back(today: date, n: int) -> list[tuple[int, int]]:
    """The (year, month) of the n full calendar months BEFORE today's month,
    oldest first."""
    y, m, out = today.year, today.month, []
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        out.append((y, m))
    return list(reversed(out))


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)   # first of next month
    return start, end


def backtest_accuracy(months_back: int = 3, *, today: date | None = None) -> dict:
    """Predicted vs actual for the last N full calendar months — read-only.

    For each month we compare what we PLANNED (``PlannedForexPayment`` with an
    expected value date that month, in pula) against what ACTUALLY happened
    (``ForexPaymentHistory`` with a value date that month, converted to pula at
    the rate on the day). Accuracy per month = 1 − |actual − predicted| / actual,
    floored at 0; the headline is the average across the months with activity.
    Nothing is written; this only reports so the CFO can trust the forecast.
    """
    today = today or timezone.localdate()
    periods, scores = [], []
    for (y, m) in _months_back(today, months_back):
        start, end = _month_bounds(y, m)
        planned = list(PlannedForexPayment.objects.filter(
            expected_value_date__gte=start, expected_value_date__lt=end))
        actual = list(ForexPaymentHistory.objects.filter(
            value_date__gte=start, value_date__lt=end))

        pred_bwp = sum((p.estimated_bwp or Decimal('0') for p in planned), Decimal('0'))
        act_bwp = Decimal('0')
        for a in actual:
            _, bwp, _ = estimate_bwp(a.currency, a.amount, a.value_date)
            act_bwp += bwp or Decimal('0')

        planned_keys = {p.beneficiary_key for p in planned if p.beneficiary_key}
        actual_keys = {a.beneficiary_key for a in actual if a.beneficiary_key}
        matched = planned_keys & actual_keys

        if act_bwp > 0:
            acc = max(Decimal('0'), Decimal('1') - abs(act_bwp - pred_bwp) / act_bwp)
        else:
            acc = Decimal('1') if pred_bwp == 0 else Decimal('0')
        has_activity = bool(planned or actual)
        if has_activity:
            scores.append(acc)

        periods.append({
            'year': y, 'month': m,
            'label': start.strftime('%b %Y'),
            'predicted_count': len(planned),
            'actual_count': len(actual),
            'predicted_bwp': str(pred_bwp.quantize(Decimal('0.01'), ROUND_HALF_UP)),
            'actual_bwp': str(act_bwp.quantize(Decimal('0.01'), ROUND_HALF_UP)),
            'matched_payees': len(matched),
            'accuracy_pct': int((acc * 100).quantize(Decimal('1'), ROUND_HALF_UP)),
        })

    overall = (int((sum(scores) / len(scores) * 100).quantize(Decimal('1'), ROUND_HALF_UP))
               if scores else 0)
    return {
        'today': today.isoformat(),
        'months_back': months_back,
        'overall_accuracy_pct': overall,
        'has_data': bool(scores),
        'periods': periods,
    }
