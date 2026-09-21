"""
payments/bulk_upload.py — bulk payment upload for the Record Payment surface.

The finance team pastes / uploads a sheet of vendor payments (any column
layout). DeepSeek maps the HEADER ROW to our canonical fields (header names
only — no amounts or vendor data are sent to the model); a heuristic fallback
covers the case where DeepSeek is unavailable. Each row becomes a DRAFT Payment
that then runs through the normal approval quorum (1 FM/FC + 1 CFO/CEO). Nothing is
paid or posted here — this only creates drafts.

CFO directive 2026-07-07.
"""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

# Canonical fields we try to fill from the uploaded sheet.
CANONICAL = ['vendor', 'amount', 'currency', 'method', 'reference', 'description']

# The reference template we hand the team (download button on the page).
TEMPLATE_HEADER = ['Vendor Name', 'Amount', 'Currency', 'Payment Method', 'Reference', 'Description']
TEMPLATE_SAMPLE = [
    ['Redhill Risk Solutions', '4000.00', 'BWP', 'EFT', 'Redhill — July invoice', 'Consulting fee'],
    ['Veritas', '25000.00', 'BWP', 'EFT', 'Assessor fees', 'Claims assessment'],
]

_METHODS = {
    'eft': 'bank_transfer', 'bank transfer': 'bank_transfer', 'bank_transfer': 'bank_transfer',
    'transfer': 'bank_transfer', 'rtgs': 'bank_transfer', 'card': 'bank_transfer',
    'debit order': 'debit_order',
    'debit_order': 'debit_order', 'mobile money': 'mobile_money', 'mobile_money': 'mobile_money',
    'cash': 'cash', 'cheque': 'cheque', 'check': 'cheque', 'gateway': 'gateway',
}


def template_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_HEADER)
    for r in TEMPLATE_SAMPLE:
        w.writerow(r)
    return buf.getvalue()


def _rows(text: str):
    sniff = '\t' if any('\t' in ln for ln in text.splitlines()) else ','
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=sniff) if any((c or '').strip() for c in r)]
    return rows


def _norm(s):
    return str('' if s is None else s).strip()


def _num(s):
    raw = str(s or '').strip()
    neg = raw.startswith('(') and raw.endswith(')')   # accounting negative
    c = re.sub(r'[^0-9.\-]', '', raw)
    m = re.match(r'^-?\d*\.?\d+', c)
    if not m:
        return None
    try:
        val = Decimal(m.group(0))
    except InvalidOperation:
        return None
    return -val if neg and val > 0 else val


def _heuristic_map(header):
    """Best-effort header-name → canonical field, by keyword."""
    idx = {}
    pats = {
        'vendor': r'vendor|payee|supplier|beneficiary|name|creditor',
        'amount': r'amount|value|total|pula|bwp|pay',
        'currency': r'currency|ccy',
        'method': r'method|type|channel',
        'reference': r'ref|narrative|description|detail',
        'description': r'description|memo|note|comment',
    }
    low = [_norm(h).lower() for h in header]
    for field, pat in pats.items():
        for i, h in enumerate(low):
            if h and re.search(pat, h) and i not in idx.values():
                idx[field] = i
                break
    return idx


def _deepseek_map(header):
    """Ask DeepSeek to map header names → canonical field indexes (header only —
    no data values sent). Returns {} on any failure (caller falls back)."""
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    except Exception:   # noqa: BLE001
        return {}
    sys = (
        'You map spreadsheet column headers to a fixed set of payment fields. '
        'Return ONLY JSON: {"vendor":i,"amount":i,"currency":i,"method":i,'
        '"reference":i,"description":i} where i is the 0-based column index in the '
        'given header list, or -1 if that field is not present. Fields: vendor = '
        'the payee/supplier/beneficiary name; amount = the money value; currency = '
        'currency code; method = payment method/channel; reference = payment '
        'reference/narrative; description = free-text note.'
    )
    user = 'Header columns (0-based): ' + json.dumps([_norm(h) for h in header])
    try:
        out = deepseek_complete(user, system_prompt=sys, response_format='json_object', timeout=20)
        data = json.loads(out)
        idx = {}
        for f in CANONICAL:
            v = data.get(f)
            if isinstance(v, int) and 0 <= v < len(header):
                idx[f] = v
        # Duplicate column indices = a broken mapping (e.g. vendor=amount=0).
        # Reject entirely so the heuristic fallback takes over.
        if len(set(idx.values())) != len(idx):
            return {}
        return idx
    except (DeepSeekUnavailable, ValueError, TypeError, KeyError):
        return {}
    except Exception:   # noqa: BLE001
        return {}


def _looks_like_header(row):
    """A row is a header if it has no positive numeric cell AND at least two
    word-like cells (so a data row with a blank amount isn't swallowed)."""
    if any(_num(c) is not None and _num(c) > 0 for c in row):
        return False
    words = sum(1 for c in row if _norm(c) and _num(c) is None)
    return words >= 2


def analyse(text: str) -> dict:
    """Map columns (DeepSeek → heuristic) and return the field→index mapping +
    whether the first row is a header. No DB writes."""
    rows = _rows(text)
    if not rows:
        return {'mapping': {}, 'has_header': False, 'via': 'none', 'row_count': 0}
    header = rows[0]
    has_header = _looks_like_header(header)
    via = 'deepseek'
    mapping = _deepseek_map(header) if has_header else {}
    if not mapping.get('vendor') and mapping.get('vendor') != 0:
        mapping = _heuristic_map(header) if has_header else {}
        via = 'heuristic'
    # Positional fallback when there is no usable header at all.
    if 'vendor' not in mapping:
        mapping = {'vendor': 0, 'amount': 1, 'currency': 2, 'method': 3, 'reference': 4, 'description': 5}
        via = 'positional'
    data_rows = rows[1:] if has_header else rows
    return {'mapping': mapping, 'has_header': has_header, 'via': via, 'row_count': len(data_rows)}


def _resolve_vendor(name, company_id=None):
    """Vendor Contact by name — exact first, then unique icontains. Scoped to
    the entity when company_id is given (vendor lists are per-entity — an ADIC
    vendor must not satisfy an ADSA upload). Returns (contact|None, error|None)."""
    from billing.models import Contact
    name = _norm(name)
    if not name:
        return None, 'blank vendor name'
    qs = Contact.objects.filter(contact_type='vendor', is_active=True)
    if company_id:
        qs = qs.filter(company_id=company_id)
    exact = list(qs.filter(name__iexact=name)[:2])
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return sorted(exact, key=lambda c: str(c.created_at))[0], None   # oldest copy, deterministic
    part = list(qs.filter(name__icontains=name)[:2])
    if len(part) == 1:
        return part[0], None
    if len(part) > 1:
        return None, f'"{name}" matches several vendors — enter it exactly'
    return None, f'no vendor named "{name}" — add it first'


def build_preview(text: str, company_id=None) -> dict:
    """Map + parse rows into a preview (vendor resolved, amount parsed, errors)
    WITHOUT creating anything."""
    from core.models import Currency
    valid_ccy = set(Currency.objects.filter(is_active=True).values_list('code', flat=True))
    info = analyse(text)
    mapping = info['mapping']
    rows = _rows(text)
    data_rows = rows[1:] if info['has_header'] else rows

    def cell(row, field):
        i = mapping.get(field)
        return row[i] if isinstance(i, int) and 0 <= i < len(row) else ''

    out_rows = []
    for r in data_rows:
        vname = _norm(cell(r, 'vendor'))
        amt = _num(cell(r, 'amount'))
        ccy = (_norm(cell(r, 'currency')) or 'BWP').upper()[:3]
        method = _METHODS.get(_norm(cell(r, 'method')).lower(), 'bank_transfer')
        ref = _norm(cell(r, 'reference'))[:200]
        desc = _norm(cell(r, 'description'))[:1000]
        contact, verr = _resolve_vendor(vname, company_id=company_id)
        err = verr
        if amt is None or amt <= 0:
            err = err or 'amount missing / not a positive number'
        if ccy not in valid_ccy:
            err = err or f'unknown currency "{ccy}"'
        out_rows.append({
            'vendor': vname, 'vendor_id': str(contact.id) if contact else None,
            'amount': str(amt) if amt is not None else None,
            'currency': ccy, 'method': method, 'reference': ref, 'description': desc,
            'ok': err is None, 'error': err,
        })
    return {**info, 'rows': out_rows,
            'ok_count': sum(1 for r in out_rows if r['ok']),
            'error_count': sum(1 for r in out_rows if not r['ok'])}


def create_payments(text: str, *, user, company_id, bank_account_id, submit=True) -> dict:
    """Create a DRAFT Payment per valid row (payment_type=sent), optionally
    submit each for approval so they land in the approval queue. Bad rows are
    skipped with a reason; the good ones still go through.

    Each row commits in its OWN savepoint — one bad row must not silently roll
    back the rows already reported as created (Fable audit 2026-07-07: outer
    @transaction.atomic + swallowed row errors fabricated success)."""
    from payments.models import Payment, resolve_paying_bank, resolve_fx_rate
    prev = build_preview(text, company_id=company_id)
    # Accepts a ledger.Account id OR a banking.BankAccount id — the FE picker
    # sends the latter (see resolve_paying_bank).
    bank = resolve_paying_bank(bank_account_id)
    if not bank:
        raise ValueError('Pick a valid paying bank account.')
    # Entity consistency (mirror of the once-off guard): the paying bank must
    # belong to the entity these payments are booked under.
    if bank.owner_company_id and str(bank.owner_company_id) != str(company_id):
        raise ValueError('That paying bank belongs to a different entity than '
                         'the one selected top-right — pick a bank in this entity.')
    today = timezone.localdate()
    created, errors = [], []
    for r in prev['rows']:
        if not r['ok']:
            errors.append({'vendor': r['vendor'], 'error': r['error']})
            continue
        try:
            rate = resolve_fx_rate(r['currency'] or 'BWP', today)
            if rate is None:
                errors.append({'vendor': r['vendor'],
                               'error': f"no approved {r['currency']}→BWP exchange "
                                        "rate loaded — load one on /fx first"})
                continue
            with transaction.atomic():   # per-row savepoint
                p = Payment(
                    payment_type=Payment.PaymentType.SENT,
                    contact_id=r['vendor_id'], bank_account=bank,
                    company_id=company_id,
                    payment_date=today,
                    currency_code_id=r['currency'] or 'BWP',
                    exchange_rate=rate,
                    amount=Decimal(r['amount']),
                    payment_method=r['method'],
                    reference=r['reference'] or f"Bulk upload {today}",
                    description=r.get('description') or '',
                    created_by=user,
                )
                p.save(audit_user=user)
                if submit:
                    # notify=False: don't raise one approver task PER row — fire
                    # a single summary task for the whole batch below (Fable audit).
                    p.submit_for_approval(user=user, notify=False)
            created.append({'vendor': r['vendor'], 'amount': r['amount'],
                            'payment_number': p.payment_number, 'id': str(p.id)})
        except Exception as e:   # noqa: BLE001
            errors.append({'vendor': r['vendor'], 'error': str(e)[:160]})
    if submit and created:
        try:
            from core.notifications import notify_payment_batch_submitted
            notify_payment_batch_submitted(
                count=len(created), company_id=company_id, submitter=user)
        except Exception:   # noqa: BLE001 — never block the upload on a task
            pass
    return {'created': created, 'errors': errors,
            'created_count': len(created), 'error_count': len(errors),
            'mapping_via': prev['via']}
