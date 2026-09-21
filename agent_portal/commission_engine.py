"""
agent_portal/commission_engine.py — UNICOIN sales-agent commission engine.

Faithful Python port of the UNICOIN SOP §4 rules from the Senior Associate's
`commission_reports.html` tool (Motlatsi Molefe, 6 Jul 2026). One compute
function per stream; each takes a row (cells in the stream's column order,
exactly as the source tool indexes `c[i]`) and returns:

    {'agent', 'basis', 'pay' (bool), 'commission' (Decimal), 'reason'}

Rates + gate-checks match the tool line-for-line so omni reproduces its pay-run
arithmetic exactly. The SOP cross-checks (all-policy list, never-pay-twice,
paygate exports) and the CFO sign-off remain finance controls layered on top.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP

# ── Rates (UNICOIN SOP §4) ────────────────────────────────────────────────
COLL_RATE = Decimal('0.20')      # Collection: 20% of amount collected
CONV_RATE = Decimal('0.70')      # RealPay Conversion: 70% of premium
MOTOR_FLAT = Decimal('20')       # Motor Comprehensive: flat P20 / policy
KYC_FLAT = Decimal('20')         # KYC Claims: P20 / confirmed item
DEFAULT_BASE = Decimal('99')     # Hospital Cashback: default base premium

NAN = float('nan')


# ── helpers (mirror the tool's JS exactly) ────────────────────────────────
def norm(s) -> str:
    return str('' if s is None else s).strip()


def low(s) -> str:
    return norm(s).lower()


def num(s):
    """'BWP 1,234.50' -> 1234.5 ; empty/None -> NaN.

    JS-parity (Fable review #9): JS parseFloat parses the longest valid
    prefix ('1234.50-' -> 1234.5, '99.00.' -> 99), so extract the leading
    number instead of float()-ing the whole cleaned string.
    """
    if s is None:
        return NAN
    c = re.sub(r'[^0-9.\-]', '', str(s))
    m = re.match(r'^-?\d*\.?\d+', c)
    if not m:
        return NAN
    try:
        return float(m.group(0))
    except ValueError:
        return NAN


def is_cancelled(s) -> bool:
    return bool(re.search(r'deact|cancel|inactive|lapse', low(s)))


def kyc_ok(s) -> bool:
    l = low(s)
    return bool(re.search(r'appro|compl', l)) and not re.search(r'not|pend|non|reject', l)


def paygate_success(s) -> bool:
    l = low(s)
    return bool(re.search(r'succ|paid', l)) and not re.search(r'unpaid|fail', l)


def is_yes(s) -> bool:
    return bool(re.match(r'^(y|yes|true|1)', low(s)))


def money(n) -> str:
    try:
        return 'BWP ' + f'{float(n):,.2f}'
    except (TypeError, ValueError):
        return 'BWP 0.00'


def parse_dmy(s):
    """dd/mm/yyyy or yyyy-mm-dd -> datetime.date | None."""
    import datetime as _dt
    s = norm(s)
    if not s:
        return None
    if re.match(r'^\d{4}-\d{1,2}-\d{1,2}', s):
        try:
            return _dt.date(*[int(x) for x in s[:10].split('-')])
        except (ValueError, TypeError):
            return None
    m = re.match(r'^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})', s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return _dt.date(y, mo, d)
        except ValueError:
            return None
    # Generic fallback (Fable review #4): the JS tool ends with `new Date(s)`,
    # which parses e.g. "15 Jun 2026". Try the common textual formats so a
    # parseable date never silently skips the conversion 3-month gate.
    for fmt in ('%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%d-%b-%Y', '%d-%b-%y'):
        try:
            return _dt.datetime.strptime(s[:24], fmt).date()
        except ValueError:
            continue
    return None


def _q(x) -> Decimal:
    """Round to 2dp like the tool's .toFixed(2)."""
    if isinstance(x, float) and math.isnan(x):
        return Decimal('0.00')
    return Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _no(agent, basis, reason):
    return {'agent': agent, 'basis': _q(basis if not (isinstance(basis, float) and math.isnan(basis)) else 0),
            'pay': False, 'commission': Decimal('0.00'), 'reason': reason}


def _yes(agent, basis, commission, reason):
    return {'agent': agent, 'basis': _q(basis if not (isinstance(basis, float) and math.isnan(basis)) else 0),
            'pay': True, 'commission': _q(commission), 'reason': reason}


# ── per-stream compute (cells indexed exactly as the source tool) ─────────
def _new_sales(c, ctx=None):
    """MIS + Liberty: 1st month 100%; 2nd month 100% RealPay / 50% DPO.
    cols: agent,policy,holder,product,book,premium,kyc,status,created,inception,
          month,source,ref,pgs,paid,pdate"""
    a = norm(c[0])
    if not a:
        return None
    prem, kyc, st, month, src, pgs, paid = num(c[5]), c[6], c[7], norm(c[10]), low(c[11]), c[13], num(c[14])
    rate, lbl = Decimal('1'), '100% premium (1st month)'
    if re.search(r'2', month):
        if re.search(r'dpo', src):
            rate, lbl = Decimal('0.5'), '50% premium (2nd month, DPO)'
        else:
            rate, lbl = Decimal('1'), '100% premium (2nd month, RealPay)'
    if is_cancelled(st):
        return _no(a, prem, 'Not Activated (cancelled/deactivated)')
    if not kyc_ok(kyc):
        return _no(a, prem, 'KYC not approved')
    if not (paygate_success(pgs) and not math.isnan(paid) and paid >= prem):
        return _no(a, prem, 'No SUCCESS paygate with Paid ≥ premium')
    return _yes(a, prem, Decimal(str(prem)) * rate, lbl)


def _conversion(c, ctx=None):
    """70% of premium; inception must be on/before the 3-month cutoff.
    cols: agent,policy,holder,product,book,premium,kyc,status,inception,source,ref,pgs,paid,pdate"""
    ctx = ctx or {}
    a = norm(c[0])
    if not a:
        return None
    prem, kyc, st, inc, pgs, paid = num(c[5]), c[6], c[7], c[8], c[11], num(c[12])
    if is_cancelled(st):
        return _no(a, prem, 'Not Activated (cancelled/deactivated)')
    if not kyc_ok(kyc):
        return _no(a, prem, 'KYC not approved')
    cutoff = ctx.get('cutoff')
    if cutoff:
        d = parse_dmy(inc)
        if d and d > cutoff:
            return _no(a, prem, 'Started less than 3 months ago')
        # Fable review #4: a NON-empty inception we cannot parse must not
        # silently skip the 3-month gate (that would overpay 70% on a policy
        # the SOP rejects). Hold it with a clear reason for finance to fix.
        if norm(inc) and d is None:
            return _no(a, prem, 'Unreadable inception date — fix the date and re-ingest')
    if not (paygate_success(pgs) and not math.isnan(paid) and paid >= prem):
        return _no(a, prem, 'No SUCCESS paygate with Paid ≥ premium')
    return _yes(a, prem, Decimal(str(prem)) * CONV_RATE, '70% premium')


def _collection(c, ctx=None):
    """20% of amount collected; paygate collected must EQUAL claimed (±0.01).
    cols: agent,policy,holder,product,book,premium,kyc,status,billing,claimed,source,ref,pgs,pgcoll,pdate"""
    a = norm(c[0])
    if not a:
        return None
    kyc, st, claimed, pgs, pgcoll = c[6], c[7], num(c[9]), c[12], num(c[13])
    if is_cancelled(st):
        return _no(a, claimed, 'Not Activated (cancelled/deactivated)')
    if not kyc_ok(kyc):
        return _no(a, claimed, 'KYC not approved')
    if not paygate_success(pgs):
        return _no(a, claimed, 'No SUCCESS paygate')
    if math.isnan(pgcoll) or math.isnan(claimed) or abs(pgcoll - claimed) > 0.01:
        return _no(a, claimed, 'Paygate collected ≠ claimed amount')
    return _yes(a, claimed, Decimal(str(claimed)) * COLL_RATE, '20% of ' + money(claimed))


def _motor(c, ctx=None):
    """Flat P20; Activated + SUCCESS paygate.
    cols: agent,policy,holder,product,book,premium,kyc,status,source,ref,pgs,paid,pdate"""
    a = norm(c[0])
    if not a:
        return None
    st, pgs = c[7], c[10]
    if is_cancelled(st):
        return _no(a, MOTOR_FLAT, 'Not Activated (cancelled/deactivated)')
    if not paygate_success(pgs):
        return _no(a, MOTOR_FLAT, 'No SUCCESS paygate')
    return _yes(a, MOTOR_FLAT, MOTOR_FLAT, 'Flat P20')


def _kyc_claims(c, ctx=None):
    """P20 per confirmed, non-withheld item.
    cols: agent,policy,holder,product,book,premium,kyc,evidence,confirmed,withheld,date"""
    a = norm(c[0])
    if not a:
        return None
    ev, conf, wh = norm(c[7]), c[8], c[9]
    if not is_yes(conf):
        return _no(a, 1, 'Item not confirmed')
    if is_yes(wh):
        return _no(a, 1, 'Item withheld')
    return _yes(a, 1, KYC_FLAT, (ev or 'Confirmed item') + ' · P20')


def _hospital(c, ctx=None):
    """Commission = Expected Total − Base Premium; Activated + Reconciliation='Pay'.
    cols: agent,policy,holder,product,book,base,expected,payment,kyc,status,recon,source,ref,pgs,pdate"""
    a = norm(c[0])
    if not a:
        return None
    base = num(c[5])
    if math.isnan(base):
        base = float(DEFAULT_BASE)
    exp = num(c[6])
    pmt = num(c[7])
    st, rc = c[9], low(c[10])
    if math.isnan(pmt):
        # Fable review #7: if Expected Total is ALSO blank, exp-base is NaN —
        # the JS tool leaks a NaN commission; we reject it with a clear reason
        # instead of silently recording a P0.00 "payable" line.
        if math.isnan(exp):
            return _no(a, 0, 'Missing expected total')
        pmt = exp - base
    if math.isnan(pmt):
        return _no(a, exp, 'Missing expected total')
    pmt = float(_q(pmt))   # ROUND_HALF_UP, matching the module's money rounding (#16)
    if is_cancelled(st):
        return _no(a, exp, 'Not Activated (cancelled/deactivated)')
    if not re.match(r'^pay', rc) or re.search(r'not', rc):
        return _no(a, exp, "Reconciliation ≠ 'Pay'")
    if pmt <= 0:
        return _no(a, exp, 'No dependant premium added')
    return _yes(a, exp, pmt, 'Dependant premium (' + money(exp) + ' − ' + money(base) + ')')


def _incentives(c, ctx=None):
    """Approved memo amount; office agents, no policy/paygate check.
    cols: agent,operation,memo,amount,approved_by"""
    a = norm(c[0])
    if not a:
        return None
    op, amt = norm(c[1]), num(c[3])
    if math.isnan(amt) or amt <= 0:
        return _no(a, 0, 'No approved amount')
    return _yes(a, amt, amt, op or 'Incentive')


# stream key -> (display name, tag, compute, uses_cutoff, column_count)
# v31 alignment (Motlatsi's commission_reports_31.html, 2026-07-06): RealPay
# Conversion is split by book (MIS / Liberty, both 70%), and Bank Confirmation
# Collection is its own stream paying 20% of the amount collected (Collection
# math + gates). The legacy 'conversion' key stays accepted for old lines.
STREAMS = {
    'new_sales_mis':      ('New Sales MIS',       '100% / 50% of premium', _new_sales,  False, 16),
    'new_sales_liberty':  ('New Sales Liberty',   '100% / 50% of premium', _new_sales,  False, 16),
    'conversion_mis':     ('RealPay Conversion MIS',     '70% of premium', _conversion, True,  14),
    'conversion_liberty': ('RealPay Conversion Liberty', '70% of premium', _conversion, True,  14),
    'collection':         ('Collection',          '20% of amount collected', _collection, False, 15),
    'motor':              ('Motor Comprehensive', 'P20 flat per policy',   _motor,      False, 13),
    'kyc_claims':         ('KYC Claims',          'P20 per confirmed item', _kyc_claims, False, 11),
    'hospital':           ('Hospital Cashback',   'Expected total − base premium', _hospital, False, 15),
    'incentives':         ('Proposed Incentives', 'Approved memo amount',  _incentives,  False, 5),
    'bank_confirmation':  ('Bank Confirmation Collection', '20% of amount collected', _collection, False, 15),
}
# Legacy key (pre-v31 lines) — accepted by compute_row, hidden from the catalogue.
LEGACY_STREAMS = {
    'conversion': ('RealPay Conversion', '70% of premium', _conversion, True, 14),
}


def compute_row(stream_key: str, cells, ctx=None):
    """Compute one line for a stream. Returns the result dict or None (blank agent).

    Fable review #1: pad short rows to the stream's column count — the JS tool
    reads a missing trailing cell as undefined (-> ''/NaN) and keeps going;
    an unpadded Python row raised IndexError and 500'd the whole ingest.
    """
    spec = STREAMS.get(stream_key) or LEGACY_STREAMS.get(stream_key)
    if not spec:
        raise KeyError(f'Unknown commission stream: {stream_key}')
    width = spec[4]
    padded = list(cells) + [''] * max(0, width - len(cells))
    return spec[2](padded, ctx or {})


def compute_rows(stream_key: str, rows, ctx=None):
    """Compute a batch; returns list of result dicts (skips blank-agent rows)."""
    out = []
    for r in rows:
        res = compute_row(stream_key, r, ctx)
        if res is not None:
            out.append(res)
    return out
