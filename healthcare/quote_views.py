"""
healthcare/quote_views.py — Group Health Quotation API (CFO/Tlamelo 2026-06-17).

Persisted quotes (history + edit + download) + invoice-from-approved-quote,
rated off the OFFICE rate card in healthcare.health_rates (the customer-facing
premium per tier × age-band × gender × member type, +14% VAT).

    GET    /api/v1/health/quotes/                 list (history)
    POST   /api/v1/health/quotes/                 create (rates + saves)
    GET    /api/v1/health/quotes/<id>/            detail (grouped by tier — FE tabs)
    PATCH  /api/v1/health/quotes/<id>/            edit (re-rates)
    DELETE /api/v1/health/quotes/<id>/            delete a draft
    POST   /api/v1/health/quotes/<id>/approve/    mark approved (enables invoice)
    POST   /api/v1/health/quotes/<id>/invoice/    generate invoice (approved only)
    GET    /api/v1/health/quotes/rate-card/       tiers + age bands (for the FE)

Off-GL — no JE writes.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import UserRateThrottle

from .models import HealthQuote, HealthQuoteMember
from . import health_rates as HR

import os

Q2 = Decimal('0.01')

# Company banking / VAT block shown on the invoice (CFO/Tlamelo 2026-06-17).
COMPANY_INVOICE = {
    'bank': 'First National Bank Botswana (FNB)',
    'account_name': 'Alpha Direct Insurance Company (Pty) Ltd',
    'account_no': '63162367601',
    'branch_code': '282267',
    'swift': 'FIRNBWGX',
    'vat_reg': 'BW00000123907',
}

# Approval line (CFO directive 2026-06-17: "remove me from the approval line —
# Ritah, Medu or Tlamelo is enough"). Any ONE of these three may approve; the
# CFO is no longer in the line. Maker-checker still holds — the submitter can't
# approve their own quote (another approver must). Env-overridable.
REVIEW_CHAIN = [
    ('review1', os.environ.get('OMNI_HQ_REVIEW1', 'rtonkope')),    # Ritah Tonkope
    ('review2', os.environ.get('OMNI_HQ_REVIEW2', 'mtlagae')),     # Meduduetso Tlagae
]
def _final_approvers() -> set:
    env = (os.environ.get('OMNI_HQ_FINAL_APPROVERS') or '').strip()
    if env:
        return {x.strip().lower() for x in env.split(',') if x.strip()}
    return {'rtonkope', 'mtlagae', 'tchimidza'}   # Ritah, Meduduetso, Tlamelo (CFO removed)


def _escalation_approvers() -> set:
    """Escalation / senior approver — can approve if the three above don't, but
    is NOT routinely notified. CFO directive 2026-06-25: "too small for the CFO
    to get involved — Medu or Tlamelo can approve, or escalate to Kago." Kago
    replaces the CFO as the senior fallback. Env-overridable."""
    env = (os.environ.get('OMNI_HQ_ESCALATION') or '').strip()
    if env:
        return {x.strip().lower() for x in env.split(',') if x.strip()}
    return {'ktshutlhedi'}   # Kago Tshutlhedi


def _local(user) -> str:
    e = (getattr(user, 'email', '') or '')
    return (e.split('@', 1)[0] if '@' in e else e).strip().lower()


def _is_privileged(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    p = getattr(user, 'profile', None)
    return bool(p and (getattr(p, 'is_administrator', False)
                       or (getattr(p, 'title', '') or '').lower() in ('cfo', 'finance_manager', 'financial_controller')))


def _can_act(user, stage_local: str) -> bool:
    """The named person for this stage, a final-approver, or a privileged user."""
    lp = _local(user)
    if _is_privileged(user):
        return True
    if stage_local and (lp == stage_local or lp.startswith(stage_local)):
        return True
    return False


def _notify(to_local_or_email: str, subject: str, html: str):
    try:
        from core.notifications import send_html_with_cfo_cc
        to = to_local_or_email
        if '@' not in to:
            to = f'{to}@alphadirect.co.bw'
        # CFO directive 2026-06-25: health-quote approvals are too small for the
        # CFO — do NOT cc EXCO/CFO. Goes only to the approver(s).
        send_html_with_cfo_cc(subject=subject, html=html, to=[to], cc_cfo=False)
    except Exception:        # noqa: BLE001
        import logging
        logging.getLogger('healthquote').exception('quote notify failed')


def _stage_email(q, headline: str, action_for: str) -> str:
    return f"""\
<div style="font-family:'Book Antiqua',Georgia,serif;color:#111827;max-width:600px;">
  <div style="background:#0D1B2A;padding:14px 18px;border-radius:8px 8px 0 0;">
    <span style="color:#F4A623;font-size:17px;font-weight:700;">Omni — Health Quote {headline}</span></div>
  <div style="border:1px solid #e5e7eb;border-top:0;padding:16px 18px;border-radius:0 0 8px 8px;">
    <p style="margin-top:0;">{action_for}</p>
    <table style="font-size:13px;border-collapse:collapse;">
      <tr><td style="padding:3px 10px;color:#6B7280;">Quote</td><td style="padding:3px 10px;"><b>{q.ref}</b></td></tr>
      <tr><td style="padding:3px 10px;color:#6B7280;">Client</td><td style="padding:3px 10px;">{q.client_name}</td></tr>
      <tr><td style="padding:3px 10px;color:#6B7280;">Lives</td><td style="padding:3px 10px;">{q.members.count()}</td></tr>
      <tr><td style="padding:3px 10px;color:#6B7280;">Total incl. VAT</td><td style="padding:3px 10px;"><b>P{q.total_incl}</b></td></tr>
    </table>
    <p style="margin:12px 0;"><a href="https://omni.alphadirect.co.bw/health/quotes"
       style="background:#F4A623;color:#fff;text-decoration:none;font-weight:700;padding:7px 14px;border-radius:6px;">Open in omni →</a></p>
  </div></div>"""


def _money(x) -> Decimal:
    return Decimal(x).quantize(Q2, rounding=ROUND_HALF_UP)


WHOLE = Decimal('1')


def _whole(x) -> Decimal:
    """Round to whole Pula (no cents) — health quotes match the printed office
    rate table, which carries whole-Pula figures (Tlamelo 2026-06-22)."""
    return Decimal(x).quantize(WHOLE, rounding=ROUND_HALF_UP)


def _coerce_dob(raw):
    """Best-effort parse of a date-of-birth cell → datetime.date or None.

    Tlamelo 2026-06-18: the upload "did not capture the date of birth" and left
    members zero-rated. Root cause: the old _age only read ISO ``YYYY-MM-DD``
    strings, so an Excel date cell, an Excel serial number, or a day-first text
    date (``12/05/1990`` — the Botswana convention) failed silently → age 0 →
    no premium. This coercer handles all of those. Day-first is preferred for
    ambiguous slash/dot dates.
    """
    if raw is None or raw == '':
        return None
    # openpyxl returns native date/datetime for real Excel date cells
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    # Excel serial number (days since 1899-12-30)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(raw))
        except (ValueError, OverflowError):
            return None
    s = str(raw).strip()
    if not s:
        return None
    # numeric string that is really an Excel serial (e.g. "33015")
    head = s.split('.')[0]
    if head.isdigit() and 3 <= len(head) <= 6:
        try:
            return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(head))
        except (ValueError, OverflowError):
            pass
    if 'T' in s:                      # ISO datetime string → keep the date part
        s = s[:10]
    # battery of formats, day-first first (Botswana convention)
    for f in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y/%m/%d',
              '%m/%d/%Y', '%d %b %Y', '%d %B %Y', '%d-%b-%Y', '%d-%b-%y',
              '%d/%m/%y', '%m/%d/%y', '%b %d %Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return _dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _age(dob, ref: _dt.date) -> int:
    d = _coerce_dob(dob)
    if d is None:
        return 0
    return max(0, ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day)))


# DeepSeek date-repair fallback (CFO directive 2026-06-18: "deepseek should take
# over that point and fix the dates" / "no zeros in dates"). Only invoked when the
# deterministic _coerce_dob fails on a NON-empty value, so it almost never fires.
_AI_DOB_CACHE: dict = {}


def _coerce_dob_ai(raw):
    """Last-resort date-of-birth normaliser via DeepSeek.

    PII WALL (AD-POL-AI-GOV-001): sends ONLY the bare date token — never the
    member name, ID, policy number, or any other field — so nothing that could
    identify a person leaves the system. A lone date string carries no linkage.
    Returns a datetime.date or None. Results cached per distinct string so a
    repeated bad format in one file calls DeepSeek once.
    """
    import re as _re
    s = str(raw).strip()
    if not s or len(s) > 40 or not _re.search(r'\d', s):
        return None
    if s in _AI_DOB_CACHE:
        return _AI_DOB_CACHE[s]
    result = None
    try:
        from core.ai_assist import deepseek_complete, reasoning_complete
        prompt = (
            "Normalise ONE date of birth to strict ISO format.\n"
            "Output ONLY 'YYYY-MM-DD' (zero-padded) or the word NONE — nothing else.\n"
            "Use DAY-FIRST convention (Botswana): 03/04/1990 = 3 April 1990.\n"
            "If it is not a real calendar date, output NONE.\n"
            f"Value: {s}"
        )
        try:
            out = deepseek_complete(prompt)      # DeepSeek takes this point
        except Exception:                        # noqa: BLE001
            out = reasoning_complete(prompt)     # failover if DeepSeek key absent
        m = _re.search(r'(\d{4})-(\d{2})-(\d{2})', out or '')
        if m:
            try:
                d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if 1900 <= d.year <= 2100:
                    result = d
            except ValueError:
                result = None
    except Exception:                            # noqa: BLE001 — never block the upload
        result = None
    if len(_AI_DOB_CACHE) < 2000:
        _AI_DOB_CACHE[s] = result
    return result


def _gen_ref() -> str:
    today = timezone.localdate()
    prefix = f"ADH-GRP-{today:%Y%m%d}"
    n = HealthQuote.objects.filter(ref__startswith=prefix).count() + 1
    ref = f"{prefix}-{n:03d}"
    while HealthQuote.objects.filter(ref=ref).exists():
        n += 1
        ref = f"{prefix}-{n:03d}"
    return ref


def _bad_tier_detail(members) -> str | None:
    """Reject a member list where any row has no recognised plan/tier.

    Tlamelo bug 03a2b875 follow-up: _rate_member used to fall back to
    AD_ESSENTIAL for a missing/unknown tier, so a row typed straight into the
    grid was silently priced on a plan nobody chose — a WRONG premium, which is
    worse than a zero. The tier is now required at the API boundary; the upload
    path can supply one for the whole group instead (see group_tier).
    """
    bad = []
    for i, m in enumerate(members or [], start=1):
        if not (m.get('full_name') or '').strip():
            continue                                # blank rows are dropped, not rated
        if _norm_tier(m.get('tier')) is None:
            bad.append(f"row {i} ({(m.get('full_name') or '').strip()})")
    if not bad:
        return None
    shown = ', '.join(bad[:5]) + (f' and {len(bad) - 5} more' if len(bad) > 5 else '')
    return (
        f'{len(bad)} member(s) have no plan set: {shown}. '
        f'Every member needs a plan — AD Lite, AD Essential, AD Core, AD Premier or AD Status. '
        f'Set it on each row, or upload the schedule with one plan chosen for the whole group.'
    )


def _rate_member(m: dict, ref_date: _dt.date) -> dict:
    """Return the rated member dict (premium_excl/vat/incl + age/band).

    Raises ValueError when the plan/tier is missing or unknown. It used to fall
    back to AD_ESSENTIAL, which priced members on a plan nobody picked — a wrong
    premium, worse than a zero. Callers validate first (_bad_tier_detail at the
    API boundary, _norm_tier on the upload); this raise is the backstop so the
    silent fallback cannot creep back in.
    """
    tier = _norm_tier(m.get('tier'))
    if tier is None:
        raise ValueError(
            f"member {(m.get('full_name') or '').strip()!r} has no recognised plan "
            f"({m.get('tier')!r}) — refusing to guess a premium")
    mtype = (m.get('member_type') or 'main').lower()
    gender = (m.get('gender') or 'M').upper()[:1]
    dob = m.get('date_of_birth')
    age = int(m.get('age') or 0) or _age(dob, ref_date)
    # Excl-VAT rounds to whole Pula to match the printed office rate table
    # (280.55 → 281). VAT and Incl-VAT KEEP the cents — Tlamelo 2026-06-22:
    # "the rounded numbers we required were only on the Amounts Excl. VAT …
    # the VAT amounts and the Amounts Incl. VAT should include the cents."
    excl = _whole(HR.rate_for(tier, gender, mtype, age))
    vat = _money(excl * HR.VAT_RATE)          # 14% of the whole-Pula excl, to the cent
    return {
        'full_name': (m.get('full_name') or '').strip(),
        'member_type': mtype if mtype in HR.MEMBER_TYPES else 'main',
        'gender': gender, 'date_of_birth': dob, 'age': age,
        'age_band': HR.band_for(age), 'tier': tier,   # already validated above
        'premium_excl': excl, 'vat': vat, 'premium_incl': excl + vat,
    }


# Any integer — every invoice-number allocation takes this one advisory lock, so
# two concurrent invoices can never read the same "last number".
_INVOICE_LOCK_KEY = 8_301_774


def _next_invoice_no(client_name: str) -> tuple[str, _dt.date]:
    """Allocate the next invoice number. MUST be called inside a transaction.

    Format (Tlamelo 2026-06-17): first 3 letters of the client + '-' + year +
    3-digit sequence within the year, e.g. Mivani Security -> MIV-2026001.

    Two bugs fixed here (bug 03a2b875 follow-up, CFO 2026-07-30):
      * RACE — the sequence came from a COUNT with no lock, so two people
        invoicing at the same moment both read N and both wrote N+1, giving two
        invoices the same number. A Postgres advisory lock now serialises the
        allocation for the duration of the transaction.
      * REUSE — a COUNT also drops when a row is deleted, handing a number that
        was already issued to a second invoice. We now take MAX of the sequence
        actually used this year, so numbers only ever go up.
    """
    import re as _re
    from django.db import connection

    today = timezone.localdate()
    with connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s)', [_INVOICE_LOCK_KEY])

    prefix = (_re.sub(r'[^A-Za-z]', '', client_name)[:3] or 'INV').upper()
    year = str(today.year)
    highest = 0
    for existing in (HealthQuote.objects
                     .exclude(invoice_no='')
                     .filter(invoice_no__contains=f'-{year}')
                     .values_list('invoice_no', flat=True)):
        m = _re.search(rf'-{year}(\d+)$', existing or '')
        if m:
            highest = max(highest, int(m.group(1)))
    return f'{prefix}-{year}{highest + 1:03d}', today


def _zero_premium_detail(q) -> str | None:
    """Why this quote must not move forward because it prices to nothing.

    Tlamelo bug 03a2b875 (2026-07-28): a member schedule whose plan/tier column
    wasn't recognised left every member excluded, so the quote totalled zero —
    and zero sailed through submit, approve AND invoice because every gate only
    counted members, never the money. A BWP 0.00 health invoice was issuable.
    Returns None when the quote is fine.
    """
    members = list(q.members.all())
    if not members:
        return None                            # the empty-quote gates handle this
    if Decimal(q.total_incl or 0) > 0:
        return None
    zero = [m.full_name for m in members if Decimal(m.premium_excl or 0) == 0]
    who = ', '.join(zero[:4]) + (f' and {len(zero) - 4} more' if len(zero) > 4 else '')
    return (
        f'This quote totals 0.00 — no premium was worked out for '
        f'{len(zero)} of {len(members)} member(s): {who}. '
        f'The usual cause is the plan column on the member schedule: every row needs a '
        f'Tier column set to AD Lite / AD Essential / AD Core / AD Premier / AD Status. '
        f'Fix the schedule and upload it again — a quote worth nothing cannot be '
        f'submitted, approved or invoiced.'
    )


def _serialize_member(m: HealthQuoteMember) -> dict:
    return {
        'id': str(m.id), 'full_name': m.full_name,
        'member_type': m.member_type, 'member_type_label': HR.MEMBER_TYPES.get(m.member_type, m.member_type),
        'gender': m.gender,
        'date_of_birth': m.date_of_birth.isoformat() if m.date_of_birth else None,
        'age': m.age, 'age_band': m.age_band,
        'tier': m.tier, 'tier_label': HR.PLAN_LABELS.get(m.tier, m.tier),
        'premium_excl': str(m.premium_excl), 'vat': str(m.vat), 'premium_incl': str(m.premium_incl),
    }


def _serialize(q: HealthQuote, *, detail=False) -> dict:
    members = list(q.members.all())
    out = {
        'id': str(q.id), 'ref': q.ref, 'client_name': q.client_name,
        'client_address': q.client_address, 'contact_name': q.contact_name,
        'contact_email': q.contact_email, 'contact_phone': q.contact_phone,
        'vat_no': q.vat_no,
        'benefit_start': q.benefit_start.isoformat() if q.benefit_start else None,
        'billing_period': q.billing_period, 'underwriting': q.underwriting,
        'status': q.status, 'status_label': q.get_status_display(),
        # subtotal_excl / vat / total_incl are NET (after the per-tier discount).
        # gross_excl + discount_excl expose the rack figure + the discount line.
        'gross_excl': str(q.gross_excl or q.subtotal_excl), 'discount_excl': str(q.discount_excl),
        'subtotal_excl': str(q.subtotal_excl), 'vat': str(q.vat), 'total_incl': str(q.total_incl),
        'tier_discounts': q.tier_discounts or {},
        'lives': len(members),
        'invoice_no': q.invoice_no, 'invoice_date': q.invoice_date.isoformat() if q.invoice_date else None,
        'created_by': q.created_by.username if q.created_by_id else None,
        'approved_by': q.approved_by.username if q.approved_by_id else None,
        'approved_at': q.approved_at.isoformat() if q.approved_at else None,
        'created_at': q.created_at.isoformat(), 'updated_at': q.updated_at.isoformat(),
        # Review chain (CFO/Tlamelo 2026-06-17)
        'review_stage': q.review_stage, 'review_stage_label': q.get_review_stage_display() if q.review_stage else '',
        'submitted_by': q.submitted_by.username if q.submitted_by_id else None,
        'review1_by': q.review1_by.username if q.review1_by_id else None,
        'review1_at': q.review1_at.isoformat() if q.review1_at else None,
        'review2_by': q.review2_by.username if q.review2_by_id else None,
        'review2_at': q.review2_at.isoformat() if q.review2_at else None,
        'reject_reason': q.reject_reason,
    }
    if q.status in (HealthQuote.Status.APPROVED, HealthQuote.Status.INVOICED):
        out['company'] = COMPANY_INVOICE
    if detail:
        # group by tier so the FE renders one tab per plan tier.
        tiers = {}
        for m in members:
            t = tiers.setdefault(m.tier, {'tier': m.tier, 'tier_label': HR.PLAN_LABELS.get(m.tier, m.tier),
                                          'lives': 0, 'subtotal_excl': Decimal('0'), 'vat': Decimal('0'),
                                          'total_incl': Decimal('0'), 'members': []})
            t['lives'] += 1
            t['subtotal_excl'] += m.premium_excl
            t['vat'] += m.vat
            t['total_incl'] += m.premium_incl
            t['members'].append(_serialize_member(m))
        overrides = q.tier_discounts or {}
        for t in tiers.values():
            gross_excl = t['subtotal_excl']           # Decimal — rack sum for this tier
            pct = HR.discount_for(t['tier'], overrides)
            disc = _money(gross_excl * pct / Decimal('100'))
            net_excl = gross_excl - disc
            net_vat = _money(net_excl * HR.VAT_RATE)
            t['discount_pct'] = str(pct)
            t['discount_gate'] = HR.TIER_DISCOUNT_GATES.get(t['tier'], '')
            t['gross_excl'] = str(gross_excl)
            t['discount_excl'] = str(disc)
            t['net_excl'] = str(net_excl)
            t['net_vat'] = str(net_vat)
            t['net_total_incl'] = str(net_excl + net_vat)
            # legacy keys kept = rack (gross) so existing tab columns still render
            t['subtotal_excl'] = str(gross_excl); t['vat'] = str(t['vat']); t['total_incl'] = str(t['total_incl'])
        out['tiers'] = list(tiers.values())
        out['members'] = [_serialize_member(m) for m in members]
    return out


def _save_members(q: HealthQuote, members: list, ref_date: _dt.date):
    q.members.all().delete()
    for i, m in enumerate(members or []):
        if not (m.get('full_name') or '').strip():
            continue
        r = _rate_member(m, ref_date)
        HealthQuoteMember.objects.create(quote=q, position=i, **{
            'full_name': r['full_name'], 'member_type': r['member_type'], 'gender': r['gender'],
            'date_of_birth': r['date_of_birth'] or None, 'age': r['age'], 'age_band': r['age_band'],
            'tier': r['tier'], 'premium_excl': r['premium_excl'], 'vat': r['vat'], 'premium_incl': r['premium_incl'],
        })
    q.recompute()
    q.save(update_fields=['gross_excl', 'discount_excl', 'subtotal_excl', 'vat', 'total_incl', 'updated_at'])


def _clean_discounts(data) -> dict:
    """Sanitise the tier_discounts map from the request: keep only known tiers,
    clamp each percent to 0–100, store as a string. Missing tiers fall back to
    the standard defaults at compute time (HR.discount_for)."""
    raw = data.get('tier_discounts') or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for tier, val in raw.items():
        if tier not in HR.PLAN_LABELS or val in (None, ''):
            continue
        try:
            pct = Decimal(str(val))
        except (ArithmeticError, ValueError, TypeError):
            continue
        out[tier] = str(max(Decimal('0'), min(Decimal('100'), pct)))
    return out


def _ref_date(data) -> _dt.date:
    bs = data.get('benefit_start')
    if bs:
        try:
            return _dt.date.fromisoformat(str(bs)[:10])
        except ValueError:
            pass
    return timezone.localdate()


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def quotes(request):
    if request.method == 'GET':
        qs = HealthQuote.objects.all().prefetch_related('members')
        st = (request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(client_name__icontains=search) | qs.filter(ref__icontains=search)
        return Response({'count': qs.count(), 'quotes': [_serialize(q) for q in qs[:300]]})

    data = request.data or {}
    if not (data.get('client_name') or '').strip():
        return Response({'detail': 'client_name is required.'}, status=status.HTTP_400_BAD_REQUEST)
    bad_tier = _bad_tier_detail(data.get('members', []))
    if bad_tier:
        return Response({'detail': bad_tier}, status=status.HTTP_400_BAD_REQUEST)
    q = HealthQuote.objects.create(
        ref=_gen_ref(),
        client_name=data['client_name'].strip(),
        client_address=data.get('client_address', '') or '',
        contact_name=data.get('contact_name', '') or '',
        contact_email=data.get('contact_email', '') or '',
        contact_phone=data.get('contact_phone', '') or '',
        vat_no=data.get('vat_no', '') or '',
        benefit_start=_ref_date(data) if data.get('benefit_start') else None,
        billing_period=data.get('billing_period', '') or '',
        underwriting=data.get('underwriting', 'Standard + Exclusions') or 'Standard + Exclusions',
        notes=data.get('notes', '') or '',
        tier_discounts=_clean_discounts(data),
        created_by=request.user if request.user.is_authenticated else None,
    )
    _save_members(q, data.get('members', []), _ref_date(data))
    return Response(_serialize(q, detail=True), status=status.HTTP_201_CREATED)


class HealthQuoteEmailThrottle(UserRateThrottle):
    """6/min per user on the customer-facing health-quote email — mirrors the
    underwriting quote-email throttle (security review 2026-09-04) so a device
    token can't fire unlimited branded PDFs (with member names/ages) to any address."""
    scope = 'health-quote-email'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_price(request):
    """Stateless single-life OFFICE price for the mobile health quote (no DB write).

    Body: {tier, gender ('M'|'F'), member_type? ('main'|'adult_dep'|'child_dep'),
    age? | date_of_birth?}. Reuses the SAME rater the saved quotes use, so the
    price shown on the phone is exactly what the quote/invoice will carry.
    """
    data = request.data or {}
    age_raw = data.get('age')
    if age_raw not in (None, ''):
        try:
            _a = int(age_raw)
        except (ValueError, TypeError):
            return Response({'detail': 'Age must be a whole number.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not (0 <= _a <= 120):
            return Response({'detail': 'Age must be between 0 and 120.'},
                            status=status.HTTP_400_BAD_REQUEST)
    m = {
        'full_name': '',
        'member_type': (data.get('member_type') or 'main'),
        'gender': (data.get('gender') or 'M'),
        'date_of_birth': data.get('date_of_birth'),
        'age': data.get('age'),
        'tier': data.get('tier'),
    }
    try:
        rated = _rate_member(m, timezone.localdate())
    except (ValueError, TypeError, AttributeError) as exc:
        return Response({'detail': str(exc) or 'Could not price that member.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if rated['premium_excl'] <= 0:
        return Response(
            {'detail': 'No office rate for that age and plan — check the age and plan.'},
            status=status.HTTP_400_BAD_REQUEST)
    return Response({
        'tier': rated['tier'], 'gender': rated['gender'],
        'member_type': rated['member_type'], 'age': rated['age'],
        'age_band': rated['age_band'],
        'premium_excl': str(rated['premium_excl']),
        'vat': str(rated['vat']),
        'premium_incl': str(rated['premium_incl']),
        'currency': 'BWP',
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([HealthQuoteEmailThrottle])
def quote_email(request, pk):
    """Email a saved health quote's PDF to the client from the app.

    Reuses healthcare.quote_pdf.build_quote_pdf + the shared mailer
    (core.notifications.send_html_with_cfo_cc). Customer mail: no EXCO copy. The
    quote row is untouched if the send fails (mirrors underwriting's email flow).
    """
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.core.validators import validate_email

    from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html
    from .quote_pdf import build_quote_pdf

    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    to = str((request.data or {}).get('to') or q.contact_email or '').strip()
    try:
        validate_email(to)
    except DjangoValidationError:
        return Response({'detail': 'Enter a valid email address for the client.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # Don't email an empty / un-priced quote — gate on money, not rows alone.
    if not q.members.exists() or _zero_premium_detail(q):
        return Response({'detail': 'This quote has no priced members yet — add a '
                                   'member and a plan before sending it.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # A discounted quote must clear the review chain before it reaches a client.
    if (q.discount_excl or q.tier_discounts) and q.status not in (
            HealthQuote.Status.APPROVED, HealthQuote.Status.INVOICED):
        return Response({'detail': 'This quote carries a discount and must be approved '
                                   'before it can be sent to the client.'},
                        status=status.HTTP_409_CONFLICT)
    try:
        pdf = build_quote_pdf(q)
    except Exception:  # noqa: BLE001 — renderer down; the quote is untouched
        return Response({'detail': 'The quote PDF could not be produced just now. '
                                   'Try again in a moment.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    body = (
        'Dumela,\n\n'
        f'Thank you for considering Alpha Direct Insurance. Attached is your health '
        f'cover quotation {q.ref} for {q.client_name}.\n\n'
        'If you have any questions, please reply to this email.\n\n'
        'Regards,\nAlpha Direct Insurance'
    )
    try:
        n = send_html_with_cfo_cc(
            f'Health cover quotation {q.ref} — Alpha Direct Insurance',
            wrap_plain_as_html(body), to=[to], text_fallback=body,
            reply_to=([request.user.email] if getattr(request.user, 'email', '')
                      else ['health@alphadirect.co.bw']),
            attachments=[(f'Health-Quote-{q.ref}.pdf', bytes(pdf), 'application/pdf')],
            cc_cfo=False,
        )
        if not n:
            raise RuntimeError('mailer returned 0 (not sent)')
    except Exception:  # noqa: BLE001 — nothing changed on the quote
        return Response({'detail': 'The email could not be sent. Nothing was changed — '
                                   'try again, or forward the PDF yourself.'},
                        status=status.HTTP_502_BAD_GATEWAY)
    q.emailed_to = to[:254]
    q.emailed_at = timezone.now()
    q.save(update_fields=['emailed_to', 'emailed_at', 'updated_at'])
    return Response({'emailed': True, 'to': to, 'at': q.emailed_at})


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def quote_detail(request, pk):
    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response(_serialize(q, detail=True))

    if request.method == 'DELETE':
        if q.status != HealthQuote.Status.DRAFT:
            return Response({'detail': 'Only draft quotes can be deleted.'}, status=status.HTTP_400_BAD_REQUEST)
        q.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PATCH — edit. Only a draft / sent-back quote is editable; once it is
    # submitted for approval (or approved / invoiced) it is locked, so figures
    # can't change underneath an approver (maker-checker). Send it back to draft
    # (reject) to edit again.
    if q.status not in (HealthQuote.Status.DRAFT, HealthQuote.Status.REJECTED):
        return Response({'detail': f'A {q.get_status_display().lower()} quote is locked. '
                                   'Send it back to draft before editing.'},
                        status=status.HTTP_400_BAD_REQUEST)
    data = request.data or {}
    if 'members' in data:
        bad_tier = _bad_tier_detail(data.get('members', []))
        if bad_tier:
            return Response({'detail': bad_tier}, status=status.HTTP_400_BAD_REQUEST)
    for f in ('client_name', 'client_address', 'contact_name', 'contact_email',
              'contact_phone', 'vat_no', 'billing_period', 'underwriting', 'notes'):
        if f in data:
            setattr(q, f, data[f] or '')
    if 'benefit_start' in data:
        q.benefit_start = _ref_date(data) if data.get('benefit_start') else None
    if 'tier_discounts' in data:
        q.tier_discounts = _clean_discounts(data)
    q.save()
    if 'members' in data:
        _save_members(q, data.get('members', []), _ref_date(data) if data.get('benefit_start') else (q.benefit_start or timezone.localdate()))
    elif 'tier_discounts' in data:
        # discount changed without re-sending members → re-total off existing rows
        q.recompute()
        q.save(update_fields=['gross_excl', 'discount_excl', 'subtotal_excl', 'vat', 'total_incl', 'updated_at'])
    q.refresh_from_db()
    return Response(_serialize(q, detail=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_submit(request, pk):
    """Creator submits a quote for approval. CFO directive 2026-06-17: it goes
    straight to the approval gate — any one of Ritah / Meduduetso / Tlamelo may
    approve (the submitter can't approve their own)."""
    q = HealthQuote.objects.filter(pk=pk).first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not q.members.exists():
        return Response({'detail': 'Add members before submitting for approval.'}, status=status.HTTP_400_BAD_REQUEST)
    zero = _zero_premium_detail(q)
    if zero:
        return Response({'detail': zero}, status=status.HTTP_400_BAD_REQUEST)
    if q.status not in (HealthQuote.Status.DRAFT, HealthQuote.Status.REJECTED):
        return Response({'detail': f'Quote is {q.get_status_display()}, cannot submit.'}, status=status.HTTP_400_BAD_REQUEST)
    q.status = HealthQuote.Status.SUBMITTED
    q.review_stage = HealthQuote.ReviewStage.FINAL   # straight to approval; any one of the three
    q.submitted_by = request.user if request.user.is_authenticated else None
    q.submitted_at = timezone.now()
    q.reject_reason = ''
    q.save()
    sub = _local(request.user)
    for fa in sorted(_final_approvers()):
        if fa == sub:
            continue   # don't ask the submitter to approve their own
        _notify(fa, f'Health quote {q.ref} awaiting your approval',
                _stage_email(q, 'For approval', f'{q.client_name} — a quote is awaiting your approval (any one of Ritah / Meduduetso / Tlamelo can approve).'))
    return Response(_serialize(q, detail=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_review(request, pk):
    """Current reviewer signs off and forwards to the next person in the chain.
    review1 (Ritah) → review2 (Meduduetso) → final (CFO)."""
    q = HealthQuote.objects.filter(pk=pk).first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    if q.status != HealthQuote.Status.SUBMITTED:
        return Response({'detail': 'Quote is not in review.'}, status=status.HTTP_400_BAD_REQUEST)
    stage = q.review_stage
    chain = dict(REVIEW_CHAIN)
    if stage == HealthQuote.ReviewStage.REVIEW1:
        if not _can_act(request.user, chain['review1']):
            return Response({'detail': 'Only the assigned reviewer can sign off this step.'}, status=status.HTTP_403_FORBIDDEN)
        q.review1_by = request.user; q.review1_at = timezone.now()
        q.review_stage = HealthQuote.ReviewStage.REVIEW2
        q.save()
        _notify(chain['review2'], f'Health quote {q.ref} needs your review',
                _stage_email(q, 'For review', f'{q.client_name} — reviewed by step 1; needs your review (step 2 of 3).'))
    elif stage == HealthQuote.ReviewStage.REVIEW2:
        if not _can_act(request.user, chain['review2']):
            return Response({'detail': 'Only the assigned reviewer can sign off this step.'}, status=status.HTTP_403_FORBIDDEN)
        q.review2_by = request.user; q.review2_at = timezone.now()
        q.review_stage = HealthQuote.ReviewStage.FINAL
        q.save()
        for fa in sorted(_final_approvers()):
            _notify(fa, f'Health quote {q.ref} awaiting your final approval',
                    _stage_email(q, 'Final approval', f'{q.client_name} — reviewed by steps 1 & 2; awaiting your final approval.'))
    else:
        return Response({'detail': 'This quote is at final approval — use Approve.'}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(q, detail=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_reject(request, pk):
    """Any reviewer/approver sends the quote back to the creator as draft."""
    q = HealthQuote.objects.filter(pk=pk).first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    if q.status != HealthQuote.Status.SUBMITTED:
        return Response({'detail': 'Only a quote in review can be sent back.'}, status=status.HTTP_400_BAD_REQUEST)
    reason = (request.data.get('reason') or '').strip()[:300]
    q.status = HealthQuote.Status.DRAFT
    q.review_stage = HealthQuote.ReviewStage.NONE
    q.reject_reason = reason or 'Sent back for changes.'
    q.save()
    if q.created_by_id and q.created_by.email:
        _notify(q.created_by.email, f'Health quote {q.ref} sent back for changes',
                _stage_email(q, 'Sent back', f'{q.client_name} — sent back by {request.user.username}. Reason: {q.reject_reason}'))
    return Response(_serialize(q, detail=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_approve(request, pk):
    """Approve a submitted quote. Any one of Ritah / Meduduetso / Tlamelo (or a
    privileged admin override) — NOT the submitter (maker-checker)."""
    q = HealthQuote.objects.filter(pk=pk).first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not q.members.exists():
        return Response({'detail': 'Cannot approve an empty quote.'}, status=status.HTTP_400_BAD_REQUEST)
    zero = _zero_premium_detail(q)
    if zero:
        return Response({'detail': zero}, status=status.HTTP_400_BAD_REQUEST)
    if q.status != HealthQuote.Status.SUBMITTED:
        return Response({'detail': 'Only a submitted quote can be approved.'}, status=status.HTTP_400_BAD_REQUEST)
    lp = _local(request.user)
    # Ritah/Medu/Tlamelo are the routine approvers; Kago can approve as the
    # escalation (CFO directive 2026-06-25). CFO is out of the line entirely.
    approvers = _final_approvers() | _escalation_approvers()
    is_approver = (lp in approvers or any(lp.startswith(a) for a in approvers))
    if not (is_approver or _is_privileged(request.user)):
        return Response({'detail': 'Approval is limited to Ritah, Meduduetso, Tlamelo or (escalation) Kago.'},
                        status=status.HTTP_403_FORBIDDEN)
    # Maker-checker: the person who submitted it cannot also approve it.
    if q.submitted_by_id and q.submitted_by_id == getattr(request.user, 'id', None):
        return Response({'detail': 'You submitted this quote — another approver (Ritah, Meduduetso or Tlamelo) must approve it.'},
                        status=status.HTTP_403_FORBIDDEN)
    q.status = HealthQuote.Status.APPROVED
    q.review_stage = HealthQuote.ReviewStage.NONE
    q.approved_by = request.user if request.user.is_authenticated else None
    q.approved_at = timezone.now()
    q.save()
    if q.created_by_id and q.created_by.email:
        _notify(q.created_by.email, f'Health quote {q.ref} approved',
                _stage_email(q, 'Approved', f'{q.client_name} — approved. You can now generate the invoice.'))
    return Response(_serialize(q, detail=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_invoice(request, pk):
    """Generate an invoice from an APPROVED quote (Tlamelo ask #3)."""
    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    if q.status not in (HealthQuote.Status.APPROVED, HealthQuote.Status.INVOICED):
        return Response({'detail': 'Only an approved quote can be invoiced.'}, status=status.HTTP_400_BAD_REQUEST)
    zero = _zero_premium_detail(q)
    if zero:
        return Response({'detail': zero}, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        # Re-read the row UNDER the lock. Checking `invoice_no` on the copy fetched
        # above is check-then-act: a double-click fires two POSTs, both see it blank,
        # and the second overwrites the first's number — leaving a gap in the invoice
        # register and a number already printed on a PDF. Uniqueness alone cannot
        # catch that, because the overwriting number IS unique.
        q = HealthQuote.objects.select_for_update().get(pk=q.pk)
        if not q.invoice_no:
            q.invoice_no, q.invoice_date = _next_invoice_no(q.client_name)
        q.status = HealthQuote.Status.INVOICED
        if not q.billing_period:
            q.billing_period = f"{timezone.now():%B %Y}"
        q.save(update_fields=['invoice_no', 'invoice_date', 'status', 'billing_period', 'updated_at'])
    out = _serialize(q, detail=True)
    out['invoice'] = True
    return Response(out)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rate_card(request):
    """Tiers + age bands + member types for the FE quote builder."""
    return Response({
        'tiers': [{'value': k, 'label': v} for k, v in HR.PLAN_LABELS.items()],
        'member_types': [{'value': k, 'label': v} for k, v in HR.MEMBER_TYPES.items()],
        'age_bands': [b[2] for b in HR.AGE_BANDS],
        'vat_rate': str(HR.VAT_RATE),
        # Standardised per-tier quote discounts (Tlamelo 2026-06-25) — the FE
        # prefills these and lets the user adjust per quote.
        'tier_discount_defaults': {k: str(v) for k, v in HR.TIER_DISCOUNT_DEFAULTS.items()},
        'tier_discount_gates': HR.TIER_DISCOUNT_GATES,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quote_pdf(request, pk):
    """Branded PDF download of the quote (or invoice once invoiced)."""
    from django.http import HttpResponse
    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    from .quote_pdf import build_quote_pdf
    pdf = build_quote_pdf(q)
    kind = 'Invoice' if q.status == HealthQuote.Status.INVOICED else 'Quote'
    fname = f"{kind}_{q.ref}.pdf"
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quote_xlsx(request, pk):
    """Branded Excel (.xlsx) download of the quote — members + per-tier premiums
    + totals (Tlamelo 2026-06-18: "we want to also download in excel format")."""
    import io, os
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage

    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    invoiced = q.status == HealthQuote.Status.INVOICED
    kind = 'Invoice' if invoiced else 'Quotation'

    NAVY, ORANGE, LIGHT = '0D1B2A', 'F4A623', 'F3F4F6'
    navy_fill = PatternFill('solid', fgColor=NAVY)
    head_fill = PatternFill('solid', fgColor=NAVY)
    alt_fill = PatternFill('solid', fgColor=LIGHT)
    white_bold = Font(bold=True, color='FFFFFF', size=11)
    navy_bold = Font(bold=True, color=NAVY, size=10)
    thin = Side(style='thin', color='D1D5DB')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = 'P#,##0.00'

    wb = Workbook(); ws = wb.active; ws.title = kind
    # Logo (full-colour on the white sheet) + title band
    logo_path = os.path.join(os.path.dirname(__file__), 'ad-logo.png')
    try:
        if os.path.exists(logo_path):
            img = XLImage(logo_path)
            ratio = (img.width / img.height) if img.height else 2.0   # capture before resize
            img.height = 46; img.width = int(46 * ratio)
            ws.add_image(img, 'A1')
    except Exception:   # noqa: BLE001 — logo is decorative, never block the export
        pass
    ws.merge_cells('A1:I1'); ws.row_dimensions[1].height = 40
    ws['A1'] = f'Alpha Direct Health — Group Health {kind}'   # A1 = merge anchor (writable)
    ws['A1'].font = Font(bold=True, color=NAVY, size=14); ws['A1'].alignment = Alignment(horizontal='right', vertical='center')

    # Client / quote meta
    r = 3
    meta = [('Reference', q.ref), ('Client', q.client_name or ''),
            ('Contact', q.contact_name or ''), ('Benefit start', q.benefit_start.isoformat() if q.benefit_start else ''),
            ('Billing', q.billing_period or ''), ('Status', q.get_status_display())]
    if invoiced and q.invoice_no:
        meta.append(('Invoice no', q.invoice_no))
    for lbl, vlu in meta:
        ws.cell(r, 1, lbl).font = navy_bold
        ws.cell(r, 2, str(vlu))
        r += 1
    r += 1

    # Member table
    cols = ['Full Name', 'Type', 'Gender', 'Date of Birth', 'Age', 'Tier', 'Premium (excl)', 'VAT', 'Premium (incl)']
    for c, h in enumerate(cols, start=1):
        cell = ws.cell(r, c, h); cell.fill = head_fill; cell.font = white_bold
        cell.border = border; cell.alignment = Alignment(horizontal='center')
    r += 1
    start_rows = r
    # Tlamelo 2026-07-02: keep the UPLOAD ORDER (position) so each Policy Holder is
    # followed by ITS dependants, and print a per-holder subtotal after each group —
    # shows what each person (holder + their dependants) is invoiced. Was sorted by
    # (tier, name), which scattered dependants away from their holder.
    members = sorted(q.members.all(), key=lambda m: (m.position, m.full_name))

    def _subtotal_row(rr, holder, s_excl, s_vat, s_incl):
        ws.cell(rr, 6, f'Subtotal — {holder}').font = navy_bold
        for cc, val in ((7, s_excl), (8, s_vat), (9, s_incl)):
            cell = ws.cell(rr, cc, float(val)); cell.number_format = money
            cell.font = navy_bold; cell.fill = alt_fill; cell.border = border
        return rr + 1

    cur_holder = None
    gh_excl = gh_vat = gh_incl = Decimal('0')          # current holder group
    grand_excl = grand_vat = grand_incl = Decimal('0')  # rack grand total
    idx = 0
    for m in members:
        # a new Policy Holder closes the previous group with its subtotal
        if m.member_type == 'main' and cur_holder is not None:
            r = _subtotal_row(r, cur_holder, gh_excl, gh_vat, gh_incl)
            gh_excl = gh_vat = gh_incl = Decimal('0'); idx += 1
        if m.member_type == 'main':
            cur_holder = m.full_name
        vals = [m.full_name, HR.MEMBER_TYPES.get(m.member_type, m.member_type), m.gender,
                m.date_of_birth.isoformat() if m.date_of_birth else '—', m.age or '—',
                HR.PLAN_LABELS.get(m.tier, m.tier),
                float(m.premium_excl), float(m.vat), float(m.premium_incl)]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(r, c, v); cell.border = border
            if c >= 7:
                cell.number_format = money
            if idx % 2 == 1:
                cell.fill = alt_fill
        r += 1
        gh_excl += m.premium_excl or 0; gh_vat += m.vat or 0; gh_incl += m.premium_incl or 0
        grand_excl += m.premium_excl or 0; grand_vat += m.vat or 0; grand_incl += m.premium_incl or 0
    # close the final holder group
    if cur_holder is not None:
        r = _subtotal_row(r, cur_holder, gh_excl, gh_vat, gh_incl)
    # Rack grand subtotal (sum of the member rows — literals, so the per-holder
    # subtotal rows above are never double-counted).
    if members:
        ws.cell(r, 6, 'Subtotal (rack)').font = Font(bold=True, color='FFFFFF')
        for c, val in ((7, grand_excl), (8, grand_vat), (9, grand_incl)):
            cell = ws.cell(r, c, float(val)); cell.number_format = money
            cell.font = Font(bold=True, color='FFFFFF'); cell.fill = navy_fill; cell.border = border
        # Discount + NET (Tlamelo 2026-06-25). Discount is on the excl premium;
        # VAT is then charged on the net excl. Net incl VAT is what the client pays.
        if q.discount_excl and float(q.discount_excl) != 0:
            r += 1
            ws.cell(r, 6, 'Less discount (excl VAT)').font = navy_bold
            cell = ws.cell(r, 7, -float(q.discount_excl)); cell.number_format = money; cell.font = navy_bold
            r += 1
            ws.cell(r, 6, 'Net subtotal (excl VAT)').font = navy_bold
            cell = ws.cell(r, 7, float(q.subtotal_excl)); cell.number_format = money; cell.font = navy_bold
            cell = ws.cell(r, 8, float(q.vat)); cell.number_format = money; cell.font = navy_bold
            r += 1
            ws.cell(r, 6, 'TOTAL (incl VAT)').font = navy_bold
            cell = ws.cell(r, 9, float(q.total_incl)); cell.number_format = money
            cell.font = Font(bold=True, color='FFFFFF'); cell.fill = navy_fill; cell.border = border
    # Column widths
    widths = [26, 14, 8, 14, 6, 16, 15, 13, 15]
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.cell(r + 2, 1, 'All amounts in BWP (Pula). VAT at 14%. Generated by Omni — Alpha Direct Health Quotations.').font = Font(italic=True, color='6B7280', size=8)

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"{kind}_{q.ref}.xlsx"
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quote_consolidated_xlsx(request, pk):
    """Consolidated ALL-TIERS quotation (Tlamelo 2026-06-30). One workbook that
    prices every member under EVERY plan tier: a 'Terms & Notes' sheet + one
    schedule sheet per tier (AD Lite / Essential / Core / Premier / Status). The
    client compares all plans at once and picks the tier(s) at acceptance."""
    import io, os, datetime as _dt2
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage

    q = HealthQuote.objects.filter(pk=pk).prefetch_related('members').first()
    if q is None:
        return Response({'detail': 'Quote not found.'}, status=status.HTTP_404_NOT_FOUND)
    members = list(q.members.all())
    ref_date = q.benefit_start or timezone.localdate()

    NAVY, ORANGE, LIGHT = '0D1B2A', 'F4A623', 'F3F4F6'
    head_fill = PatternFill('solid', fgColor=NAVY)
    alt_fill = PatternFill('solid', fgColor=LIGHT)
    tot_fill = PatternFill('solid', fgColor=ORANGE)
    white_bold = Font(bold=True, color='FFFFFF', size=11)
    navy_bold = Font(bold=True, color=NAVY, size=10)
    thin = Side(style='thin', color='D1D5DB')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = 'P#,##0.00'
    logo_path = os.path.join(os.path.dirname(__file__), 'ad-logo.png')

    issue = timezone.localdate()
    valid_until = issue + _dt2.timedelta(days=30)
    plans = ', '.join(HR.PLAN_LABELS[t] for t in HR.PLAN_LABELS)

    wb = Workbook()
    tn = wb.active; tn.title = 'Terms & Notes'
    tn.merge_cells('B2:D2'); tn['B2'] = 'ALPHA DIRECT HEALTH — GROUP QUOTATION'
    tn['B2'].font = Font(bold=True, color=NAVY, size=14)
    terms = [
        ('Quote Reference', q.ref),
        ('Client', q.client_name or ''),
        ('Date of Issue', issue.isoformat()),
        ('Benefit Start Date', q.benefit_start.isoformat() if q.benefit_start else '—'),
        ('Quote Valid Until', f'{valid_until:%d %b %Y} (30 days from issue)'),
        ('Prepared By', (q.created_by.get_full_name() or q.created_by.username) if q.created_by_id else 'Alpha Direct Health'),
        ('Plans Quoted', plans),
        ('Currency', 'Botswana Pula (BWP).'),
        ('Currency & VAT', 'All premiums are shown in BWP. VAT at 14% (VAT Act Cap 50:01) is added where shown.'),
        ('Pricing & Underwriting', 'Premiums are risk-rated on age, gender and plan, per the Alpha Direct Health office rate card. Final premium is subject to underwriting.'),
        ('Eligibility', 'Policy holders and dependants are subject to each plan\'s eligibility and benefit rules.'),
        ('Waiting Periods', '30 days general; standard condition-specific waiting periods apply per the policy wording.'),
        ('Notes', 'This is a consolidated quotation across all plan tiers. The client selects the preferred tier(s) at acceptance; the invoice is then raised for the chosen tier(s).'),
    ]
    rr = 4
    for lbl, val in terms:
        tn.cell(rr, 2, lbl).font = navy_bold
        c = tn.cell(rr, 3, str(val)); c.alignment = Alignment(wrap_text=True, vertical='top')
        rr += 1
    tn.column_dimensions['B'].width = 22; tn.column_dimensions['C'].width = 82

    HEADERS = ['#', 'Full Name', 'Status', 'Gender', 'Date of Birth', 'Age', 'Age Band']
    for tier_key, tier_label in HR.PLAN_LABELS.items():
        ws = wb.create_sheet(f'{tier_label} Schedule'[:31])
        try:
            if os.path.exists(logo_path):
                img = XLImage(logo_path); ratio = (img.width / img.height) if img.height else 2.0
                img.height = 40; img.width = int(40 * ratio); ws.add_image(img, 'A1')
        except Exception:   # noqa: BLE001 — logo decorative
            pass
        ws.merge_cells('B1:K1'); ws['B1'] = f'ALPHA DIRECT HEALTH — {tier_label} SCHEDULE'
        ws['B1'].font = Font(bold=True, color=NAVY, size=13); ws['B1'].alignment = Alignment(vertical='center')
        ws.row_dimensions[1].height = 34
        ws.cell(3, 2, 'Quote Ref:').font = navy_bold; ws.cell(3, 4, q.ref)
        ws.cell(3, 7, 'Client:').font = navy_bold; ws.cell(3, 9, q.client_name or '')
        ws.cell(4, 2, 'Benefit Start:').font = navy_bold; ws.cell(4, 4, q.benefit_start.isoformat() if q.benefit_start else '—')
        ws.cell(4, 7, 'Valid Until:').font = navy_bold; ws.cell(4, 9, valid_until.isoformat())
        ws.cell(5, 2, 'Plan:').font = navy_bold; ws.cell(5, 4, tier_label)
        ws.cell(5, 7, 'VAT Rate:').font = navy_bold; ws.cell(5, 9, '14% (VAT Act Cap 50:01)')
        hdr = HEADERS + [f'{tier_label} Excl. VAT', 'VAT @ 14%', f'{tier_label} Incl. VAT']
        hr_row = 7
        for cidx, h in enumerate(hdr, start=2):
            cc = ws.cell(hr_row, cidx, h); cc.fill = head_fill; cc.font = white_bold
            cc.border = border; cc.alignment = Alignment(horizontal='center', wrap_text=True)
        r = hr_row + 1
        t_excl = t_vat = t_incl = Decimal('0')
        for i, m in enumerate(members, 1):
            age = m.age or _age(m.date_of_birth, ref_date)
            excl = _whole(HR.rate_for(tier_key, m.gender, m.member_type, int(age or 0)))
            vat = _money(excl * HR.VAT_RATE); incl = excl + vat
            t_excl += excl; t_vat += vat; t_incl += incl
            vals = [i, m.full_name, HR.MEMBER_TYPES.get(m.member_type, m.member_type), m.gender,
                    m.date_of_birth.isoformat() if m.date_of_birth else '—', age or '—',
                    m.age_band or HR.band_for(int(age or 0)), float(excl), float(vat), float(incl)]
            for cidx, v in enumerate(vals, start=2):
                cell = ws.cell(r, cidx, v); cell.border = border
                if cidx >= 9:
                    cell.number_format = money
                if i % 2 == 0:
                    cell.fill = alt_fill
            r += 1
        ws.cell(r, 2, f'TOTAL — {len(members)} lives').font = navy_bold
        for cidx, v in ((9, float(t_excl)), (10, float(t_vat)), (11, float(t_incl))):
            cell = ws.cell(r, cidx, v); cell.number_format = money; cell.fill = tot_fill
            cell.font = Font(bold=True, color='FFFFFF'); cell.border = border
        for cidx, w in {2: 6, 3: 26, 4: 14, 5: 8, 6: 14, 7: 6, 8: 12, 9: 16, 10: 13, 11: 16}.items():
            ws.column_dimensions[get_column_letter(cidx)].width = w
        ws.cell(r + 2, 2, 'All amounts in BWP. VAT at 14%. Generated by Omni — Alpha Direct Health.').font = Font(italic=True, color='6B7280', size=8)

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="Consolidated_{q.ref}.xlsx"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Smart member-file ingest + validation (CFO idea 2/4, 2026-06-17). Accepts a
# broker member schedule (xlsx or csv), maps the columns, validates each row,
# rates it off the office card, and returns the cleaned members + a per-row
# validation report — so a bad row can't silently produce a wrong premium.
# Columns (case-insensitive): full_name|name | status|member_type | gender |
# date_of_birth|dob | tier|plan
# ─────────────────────────────────────────────────────────────────────────────

def _norm_member_type(v: str) -> str:
    s = (v or '').strip().lower()
    if 'child' in s:
        return 'child_dep'
    if 'spouse' in s or 'adult' in s or 'depend' in s:
        return 'adult_dep'
    return 'main'


def _norm_tier(v: str) -> str | None:
    """Map a plan-tier cell to a tier key, or None if blank/unrecognised.

    Tlamelo 2026-06-19: members were silently rated at AD_ESSENTIAL because a
    blank or unmapped Tier cell fell through to Essential — producing the wrong
    premium (a different tier entirely) with no warning. We now return None for
    anything we can't confidently map; the caller flags + excludes the row so a
    quote can NEVER silently price a member on a defaulted tier. Valid names
    (Lite/Essential/Core/Premier/Status, with/without 'AD') still map exactly.
    """
    s = (v or '').upper().replace(' ', '').replace('_', '')
    if not s:
        return None
    for key in HR.PLAN_LABELS:
        if key.replace('_', '').replace('AD', '') and key.replace('_', '') in ('AD' + s, s, 'AD' + s.replace('AD', '')):
            return key
    if 'LITE' in s: return 'AD_LITE'
    if 'ESSENTIAL' in s or s == 'ESS': return 'AD_ESSENTIAL'
    if 'CORE' in s: return 'AD_CORE'
    if 'PREMIER' in s: return 'AD_PREMIER'
    if 'STATUS' in s: return 'AD_STATUS'
    return None


def _parse_member_file(f):
    """Return (headers, [raw row dicts]) from xlsx or csv."""
    import csv as _csv
    from io import BytesIO, StringIO
    raw = f.read()
    name = (getattr(f, 'name', '') or '').lower()
    is_xlsx = raw[:4] == b'PK\x03\x04' or name.endswith(('.xlsx', '.xlsm'))
    if is_xlsx:
        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        table = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        table = list(_csv.reader(StringIO(raw.decode('utf-8-sig', errors='replace'))))
    if not table:
        return [], []

    # Find the header row (Tlamelo 2026-06-18 conformity error): real HR member
    # schedules often carry a title / company / "as at" banner row above the
    # column headers, so assuming row 0 is the header read the banner and matched
    # nothing → "No member rows found". Scan the first 12 rows for the row that
    # has a NAME column AND at least one of type/gender/dob/tier.
    def _norm_row(row):
        return [('' if c is None else str(c)).strip().lower() for c in row]
    _NAME  = ('full_name', 'full name', 'name', 'member', 'member name', 'employee', 'principal')
    _OTHER = ('status', 'member_type', 'member type', 'type', 'relationship',
              'gender', 'sex', 'date_of_birth', 'date of birth', 'dob', 'd.o.b', 'd.o.b.',
              'dateofbirth', 'birth date', 'birthdate', 'born', 'birth',
              'tier', 'plan', 'plan tier', 'product', 'option')
    header_idx = 0
    headers = _norm_row(table[0])
    for idx in range(min(12, len(table))):
        h = _norm_row(table[idx])
        if any(n in h for n in _NAME) and any(o in h for o in _OTHER):
            header_idx, headers = idx, h
            break

    def col(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None
    ci = {'name': col('full_name', 'full name', 'name', 'member', 'member name', 'employee', 'principal'),
          'type': col('status', 'member_type', 'member type', 'type', 'relationship'),
          'gen': col('gender', 'sex'),
          'dob': col('date_of_birth', 'date of birth', 'dob', 'd.o.b', 'd.o.b.', 'dateofbirth',
                     'birth date', 'birthdate', 'born', 'birth'),
          'tier': col('tier', 'plan', 'plan tier', 'product', 'option')}
    rows = []
    for r in table[header_idx + 1:]:
        if r is None or all(c is None or str(c).strip() == '' for c in r):
            continue
        def g(k):
            i = ci[k]
            return r[i] if i is not None and i < len(r) and r[i] is not None else ''
        rows.append({'full_name': str(g('name')).strip(), 'member_type': str(g('type')),
                     'gender': str(g('gen')), 'date_of_birth': g('dob'), 'tier': str(g('tier'))})
    return headers, rows


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quote_members_parse(request):
    """POST a member file (xlsx/csv) → validated + rated members + a report."""
    from rest_framework.parsers import MultiPartParser, FormParser
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a .xlsx or .csv member file in the "file" field.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        headers, rows = _parse_member_file(f)
    except Exception as exc:    # noqa: BLE001
        return Response({'detail': f'Could not read the file: {exc}'}, status=status.HTTP_400_BAD_REQUEST)
    if not rows:
        return Response({'detail': 'No member rows found. Expected columns: full_name, status, gender, date_of_birth, tier.'},
                        status=status.HTTP_400_BAD_REQUEST)

    ref_date = _ref_date(request.data)
    # ONE PLAN FOR THE WHOLE GROUP (CFO 2026-07-30, from bug 03a2b875). Most
    # employer groups sit on a single plan, so requiring the tier on every row
    # made a normal census unquotable — every member was excluded and the quote
    # came to zero. When the caller picks a group plan, rows with no recognised
    # tier of their own use it; a row that DOES name a valid tier keeps its own,
    # so mixed-plan schedules still work.
    group_tier_raw = (request.data.get('group_tier') or '').strip()
    group_tier = _norm_tier(group_tier_raw) if group_tier_raw else None
    if group_tier_raw and group_tier is None:
        return Response({'detail': (f'{group_tier_raw!r} is not a plan. Choose one of '
                                    f'AD Lite / AD Essential / AD Core / AD Premier / AD Status.')},
                        status=status.HTTP_400_BAD_REQUEST)
    group_tier_applied = 0

    members, errors, warnings = [], [], []
    seen = {}
    for i, row in enumerate(rows, start=2):   # row 1 = header
        name = (row.get('full_name') or '').strip()
        if not name:
            errors.append({'row': i, 'error': 'Missing full name — row skipped.'}); continue
        mtype = _norm_member_type(row.get('member_type'))
        gen = (row.get('gender') or '').strip().upper()[:1]
        if gen not in ('M', 'F'):
            errors.append({'row': i, 'error': f'{name}: gender must be M or F (got {row.get("gender")!r}); defaulted to M.'})
            gen = 'M'
        dob_raw = row.get('date_of_birth')
        raw_present = bool(str(dob_raw).strip()) if dob_raw is not None else False
        dob = _coerce_dob(dob_raw)            # robust: ISO, Excel date/serial, dd/mm/yyyy, "12 May 1990"
        dob_ai_fixed = False
        if not dob and raw_present:
            dob = _coerce_dob_ai(dob_raw)     # DeepSeek repairs anything the parser can't read
            dob_ai_fixed = dob is not None
        age = _age(dob, ref_date)
        # NO ZERO-AGE LIVES (CFO 2026-06-18). A member whose DOB cannot be resolved
        # — even by DeepSeek — is reported and EXCLUDED from the rated members, never
        # carried as an age-0 / zero-premium row that quietly skews the quote.
        if not dob:
            if raw_present:
                errors.append({'row': i, 'error': (f"{name}: date of birth {dob_raw!r} could not be read "
                               f"(even by Aria). Use e.g. 1990-05-12 or 12/05/1990. Excluded until fixed.")})
            else:
                errors.append({'row': i, 'error': (f"{name}: no date of birth — required to rate. "
                               f"Add a 'Date of Birth' (e.g. 1990-05-12). Excluded until fixed.")})
            continue
        if age <= 0 or age > 120:
            errors.append({'row': i, 'error': f"{name}: date of birth gives an impossible age ({age}) — please check. Excluded until fixed."})
            continue
        tier = _norm_tier(row.get('tier'))
        # The group plan fills only GENUINELY BLANK cells. A row that names a plan
        # we cannot read ('AD Premeir') must stay an error — quietly moving it onto
        # the group plan would price it on a plan nobody chose for it, and a wrong
        # premium is worse than a zero.
        if tier is None and group_tier and not str(row.get('tier') or '').strip():
            tier = group_tier
            group_tier_applied += 1
        if tier is None:
            raw_tier = (str(row.get('tier') or '')).strip()
            why = 'missing' if not raw_tier else f"{raw_tier!r} not recognised"
            errors.append({'row': i, 'error': (
                f"{name}: plan tier {why} — set the Tier column to one of "
                f"AD Lite / AD Essential / AD Core / AD Premier / AD Status, "
                f"or choose one plan for the whole group above. "
                f"Member excluded until a plan is set (rates differ greatly by plan).")})
            continue
        key = (name.lower(), dob.isoformat())
        if key in seen:
            warnings.append({'row': i, 'warning': f'{name}: duplicate of row {seen[key]} — included once, please check.'})
            continue
        seen[key] = i
        m = _rate_member({'full_name': name, 'member_type': mtype, 'gender': gen,
                          'date_of_birth': dob.isoformat(), 'tier': tier}, ref_date)
        # Rate-card gap: a valid age that still prices to zero means no office
        # rate exists for that tier/type/age — surface it instead of a silent 0.
        if Decimal(m['premium_excl']) == 0:
            warnings.append({'row': i, 'warning': (f"{name}: no office rate for {HR.PLAN_LABELS.get(m['tier'], m['tier'])} / "
                             f"{HR.MEMBER_TYPES.get(m['member_type'], m['member_type'])} / age {m['age']} — premium 0, please verify.")})
        if dob_ai_fixed:
            warnings.append({'row': i, 'warning': f"{name}: date of birth '{dob_raw}' auto-corrected to {dob.isoformat()} by Aria — please confirm."})
        members.append({**m, 'premium_excl': str(m['premium_excl']), 'vat': str(m['vat']),
                        'premium_incl': str(m['premium_incl']),
                        'member_type_label': HR.MEMBER_TYPES.get(m['member_type']),
                        'tier_label': HR.PLAN_LABELS.get(m['tier']),
                        'dob_ai_fixed': dob_ai_fixed})
    if group_tier_applied:
        warnings.append({'row': 0, 'warning': (
            f'{group_tier_applied} member(s) had no plan of their own and were put on '
            f'{HR.PLAN_LABELS.get(group_tier, group_tier)} — the plan you chose for the group.')})
    return Response({
        'members': members, 'count': len(members),
        'errors': errors, 'warnings': warnings,
        'group_tier': group_tier or '',
        'group_tier_applied': group_tier_applied,
        'message': (f'{len(members)} member(s) read and rated'
                    + (f' — {group_tier_applied} on the group plan '
                       f'{HR.PLAN_LABELS.get(group_tier, group_tier)}' if group_tier_applied else '')
                    + (f', {len(errors)} flagged' if errors else '')
                    + (f', {len(warnings)} note(s)' if warnings else '') + '.'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quote_dashboard(request):
    """Health-book metrics for the management dashboard (CFO idea 3/4):
    quote funnel, active lives, GWP (annualised incl VAT) by tier, this-month
    invoiced, and renewals due (invoiced quotes from a prior billing period)."""
    from django.db.models import Count, Sum
    from collections import OrderedDict
    S = HealthQuote.Status
    funnel = {k: 0 for k in ('draft', 'submitted', 'approved', 'invoiced', 'rejected')}
    for row in HealthQuote.objects.values('status').annotate(n=Count('id')):
        funnel[row['status']] = row['n']

    active = HealthQuote.objects.filter(status__in=[S.APPROVED, S.INVOICED])
    active_ids = list(active.values_list('id', flat=True))
    lives = HealthQuoteMember.objects.filter(quote_id__in=active_ids).count()

    # GWP by tier — monthly premium incl VAT on active quotes, x12 = annualised.
    by_tier = OrderedDict()
    for tier in HR.PLAN_LABELS:
        by_tier[tier] = {'tier': tier, 'tier_label': HR.PLAN_LABELS[tier],
                         'lives': 0, 'monthly_incl': Decimal('0'), 'gwp_annual': Decimal('0')}
    agg = (HealthQuoteMember.objects.filter(quote_id__in=active_ids)
           .values('tier').annotate(lives=Count('id'), monthly=Sum('premium_incl')))
    for a in agg:
        t = by_tier.get(a['tier'])
        if t:
            t['lives'] = a['lives']
            t['monthly_incl'] = a['monthly'] or Decimal('0')
            t['gwp_annual'] = (a['monthly'] or Decimal('0')) * 12
    tiers = [{**t, 'monthly_incl': str(t['monthly_incl']), 'gwp_annual': str(t['gwp_annual'])}
             for t in by_tier.values() if t['lives']]

    month = f"{timezone.now():%B %Y}"
    invoiced_month = active.filter(status=S.INVOICED, billing_period=month).aggregate(
        n=Count('id'), total=Sum('total_incl'))
    renewals_due = active.filter(status=S.INVOICED).exclude(billing_period=month).count()
    total_monthly = sum((t['monthly_incl'] for t in by_tier.values()), Decimal('0'))

    return Response({
        'funnel': funnel,
        'active_quotes': len(active_ids),
        'active_lives': lives,
        'monthly_premium_incl': str(total_monthly),
        'gwp_annualised_incl': str(total_monthly * 12),
        'tiers': tiers,
        'this_month': {'label': month, 'invoiced_count': invoiced_month['n'] or 0,
                       'invoiced_total': str(invoiced_month['total'] or Decimal('0'))},
        'renewals_due': renewals_due,
    })
