"""bonu/legal.py — the in-house legal office's own screens.

The Claims Legal Office reviewed the BONU Legal screens with the CFO on
18 Aug 2026 and reported the plain truth: what the office does every day was
not on them. Omni held the panel firms' bills. It held
nothing about the matters Alpha Law handles itself, and nothing about the
external bills the office argues down before they are paid — the two things the
officer's performance is actually measured on. Both lived in a spreadsheet.

So this module is four registers, three rate tables, and the arithmetic between
them (`bonu/legal_calc.py`). It reads and writes nothing else in Omni.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
* **No general ledger.** Not a journal, not a posting, not a total that feeds
  one. The bonus figures are a calculation on a screen.
* **No payment.** The quarterly bonus is shown as payable; paying it stays a CFO
  decision and a payroll instruction, exactly as it is today.
* **No AI.** Client names sit in these tables (CFO, 11 Aug 2026 — member names
  may be stored and read inside BONU) and nothing here sends them anywhere.

Access is the standard BONU gate (`bonu/views._deny`): finance and management,
plus the named BONU team. The legal office and the BONU finance staff already
pass it, so no new grant is created here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import date, datetime

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import legal_calc as calc
from . import legal_reports as reports
from .models import (LegalAdvisoryEntry, LegalFeeNote, LegalInvoiceSaving,
                     LegalMonthlyBonus, LegalRateMapping, LegalSettings,
                     LegalTariffItem)
from .views import _deny
from django.utils import timezone

DEPARTMENTS = ['Finance', 'Claims', 'Compliance', 'Health Care', 'Uni Coin',
               'Underwriting', 'Admin & IT', 'Business Development',
               'Software Development']


# --------------------------------------------------------------------------
# Field parsing — ONE money parser and ONE date parser for the whole module.
#
# Two parsers is how a guard and a total come to disagree (Fable H55): a typo
# read as 0 by one and as money by the other. A bad amount is a 400 here, never
# a silent zero.
# --------------------------------------------------------------------------

class FieldError(ValueError):
    """A value the user typed that we will not guess at."""


def _dec(raw, field, *, default=Decimal('0')):
    s = str(raw if raw is not None else '').strip()
    if s == '':
        return default
    s = s.replace(',', '').replace('P', '').replace('\xa0', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return Decimal(s)
    except (InvalidOperation, ArithmeticError):
        raise FieldError(f'"{field}" must be an amount — got "{raw}".')


def _date(raw, field, *, required=True):
    s = str(raw if raw is not None else '').strip()
    if not s:
        if required:
            raise FieldError(f'"{field}" is required.')
        return None
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
        raise FieldError(f'"{field}" must be a date (YYYY-MM-DD) — got "{raw}".')


def _text(raw, field, *, required=False, max_length=240):
    s = str(raw if raw is not None else '').strip()
    if required and not s:
        raise FieldError(f'"{field}" is required.')
    return s[:max_length]


def _month(raw, field):
    s = str(raw if raw is not None else '').strip()
    try:
        datetime.strptime(s, '%Y-%m')
    except ValueError:
        raise FieldError(f'"{field}" must be a month (YYYY-MM) — got "{raw}".')
    return s


# --------------------------------------------------------------------------
# The registers, described once so create / edit / delete need one code path.
# --------------------------------------------------------------------------

def _apply_fee(obj, data):
    obj.date = _date(data.get('date'), 'Date')
    obj.client = _text(data.get('client'), 'Client / BONU member', required=True, max_length=200)
    obj.portfolio = _text(data.get('portfolio'), 'Portfolio', max_length=60) or 'BONU'
    obj.description = _text(data.get('description'), 'Service description', required=True)
    obj.unit = _text(data.get('unit'), 'Unit', max_length=60)
    obj.rate = _dec(data.get('rate'), 'Rate')
    obj.qty = _dec(data.get('qty'), 'Qty / hours')


def _apply_saving(obj, data):
    obj.date_received = _date(data.get('date_received'), 'Date received', required=False)
    obj.date_reviewed = _date(data.get('date_reviewed'), 'Date reviewed')
    obj.invoice_ref = _text(data.get('invoice_ref'), 'Invoice reference', required=True, max_length=120)
    obj.external_attorney = _text(data.get('external_attorney'), 'External attorney', max_length=200)
    obj.original_amount = _dec(data.get('original_amount'), 'Original amount')
    obj.agreed_amount = _dec(data.get('agreed_amount'), 'Agreed amount')
    obj.note = _text(data.get('note'), 'Note', max_length=2000)
    if obj.date_received and obj.date_reviewed and obj.date_reviewed < obj.date_received:
        raise FieldError('The bill cannot be reviewed before it was received.')


def _apply_advisory(obj, data):
    obj.date = _date(data.get('date'), 'Date')
    obj.department = _text(data.get('department'), 'Department', max_length=80)
    obj.client = _text(data.get('client'), 'Name of client', max_length=200)
    obj.matter_ref = _text(data.get('matter_ref'), 'Matter / reference', max_length=120)
    obj.description = _text(data.get('description'), 'Description', required=True)
    obj.type_of_work = _text(data.get('type_of_work'), 'Type of work', max_length=160)
    obj.hours = _dec(data.get('hours'), 'Hours spent')


def _apply_tariff(obj, data):
    scope = _text(data.get('scope'), 'Scope', required=True, max_length=10).lower()
    if scope not in ('internal', 'external'):
        raise FieldError('"Scope" must be internal or external.')
    obj.scope = scope
    obj.section = _text(data.get('section'), 'Section', max_length=160)
    obj.item = _text(data.get('item'), 'Service / item', required=True)
    obj.unit = _text(data.get('unit'), 'Unit', max_length=60)
    obj.rate = _dec(data.get('rate'), 'Rate')
    obj.position = int(_dec(data.get('position'), 'Position'))


def _apply_mapping(obj, data):
    obj.fee_description = _text(data.get('fee_description'), 'Fee note description', required=True)
    obj.internal_unit = _text(data.get('internal_unit'), 'Internal unit', max_length=60)
    obj.internal_rate = _dec(data.get('internal_rate'), 'Internal rate')
    obj.external_item = _text(data.get('external_item'), 'External item')
    obj.external_unit = _text(data.get('external_unit'), 'External unit', max_length=60)
    obj.external_rate = _dec(data.get('external_rate'), 'External rate')
    basis = _text(data.get('calc_basis'), 'Calculation basis', max_length=10).lower() or 'flat'
    if basis not in ('flat', 'per_hour'):
        raise FieldError('"Calculation basis" must be flat or per_hour.')
    obj.calc_basis = basis
    obj.is_disbursement = bool(data.get('is_disbursement'))
    obj.position = int(_dec(data.get('position'), 'Position'))


REGISTERS = {
    'fee-notes': (LegalFeeNote, _apply_fee),
    'invoice-savings': (LegalInvoiceSaving, _apply_saving),
    'advisory': (LegalAdvisoryEntry, _apply_advisory),
    'tariffs': (LegalTariffItem, _apply_tariff),
    'mappings': (LegalRateMapping, _apply_mapping),
}


# --------------------------------------------------------------------------
# Serialisers
# --------------------------------------------------------------------------

def _saving_row(r, sla_days):
    td = r.turnaround_days
    return {
        'id': str(r.id),
        'date_received': r.date_received.isoformat() if r.date_received else None,
        'date_reviewed': r.date_reviewed.isoformat() if r.date_reviewed else None,
        'invoice_ref': r.invoice_ref,
        'external_attorney': r.external_attorney,
        'original_amount': str(r.original_amount),
        'agreed_amount': str(r.agreed_amount),
        'saving': str(r.saving),
        'note': r.note,
        'turnaround_days': td,
        'sla_met': None if td is None else td <= sla_days,
        'updated_by': r.updated_by_email,
    }


def _advisory_row(r, settings):
    return {
        'id': str(r.id),
        'date': r.date.isoformat() if r.date else None,
        'department': r.department,
        'client': r.client,
        'matter_ref': r.matter_ref,
        'description': r.description,
        'type_of_work': r.type_of_work,
        'hours': str(r.hours),
        'value': str(calc.advisory_value(r.hours, settings)),
        'updated_by': r.updated_by_email,
    }


def _tariff_row(r):
    return {'id': str(r.id), 'scope': r.scope, 'section': r.section, 'item': r.item,
            'unit': r.unit, 'rate': str(r.rate), 'position': r.position}


def _mapping_row(r):
    return {'id': str(r.id), 'fee_description': r.fee_description,
            'internal_unit': r.internal_unit, 'internal_rate': str(r.internal_rate),
            'external_item': r.external_item, 'external_unit': r.external_unit,
            'external_rate': str(r.external_rate), 'calc_basis': r.calc_basis,
            'is_disbursement': r.is_disbursement, 'position': r.position}


def _settings_row(s):
    return {'quarterly_threshold': str(s.quarterly_threshold),
            'quarterly_bonus_pct': str(s.quarterly_bonus_pct),
            'monthly_bonus_cap': str(s.monthly_bonus_cap),
            'external_hourly_rate': str(s.external_hourly_rate),
            'sla_days': s.sla_days,
            # The per-client legal-spend ceiling (Kelvin Kimani, 9 Sep 2026).
            # Read AND written here, because the model help text and the
            # comments promised these were editable on screen and for a while
            # nothing served or saved them — a switch that could be flipped
            # nowhere (Fable, /fabe gate, 9 Sep 2026).
            'client_spend_cap': str(s.client_spend_cap),
            'client_spend_amber': str(s.client_spend_amber),
            'cap_blocks_capture': s.cap_blocks_capture}


def _row_for(slug, obj, settings):
    if slug == 'fee-notes':
        return calc.fee_line(obj, calc.mapping_index(LegalRateMapping.objects.all()))
    if slug == 'invoice-savings':
        return _saving_row(obj, settings.sla_days)
    if slug == 'advisory':
        return _advisory_row(obj, settings)
    if slug == 'tariffs':
        return _tariff_row(obj)
    return _mapping_row(obj)


def _valid_month(raw):
    """A month key we can work with, or None. A period typed into the address
    bar must fall back to the default, never take the screen down."""
    try:
        return _month(raw, 'month') if raw else None
    except FieldError:
        return None


def _valid_quarter(raw):
    s = str(raw or '').strip()
    if not s:
        return None
    try:
        calc.quarter_months(s)
    except (ValueError, IndexError):
        return None
    return s



# --------------------------------------------------------------------------
# GET /bonu/legal/ — everything the screen draws, in one read.
# --------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def legal_overview(request):
    denied = _deny(request)
    if denied is not None:
        return denied

    settings = LegalSettings.solo()
    fees = list(LegalFeeNote.objects.all())
    savings = list(LegalInvoiceSaving.objects.all())
    advisory = list(LegalAdvisoryEntry.objects.all())
    mappings = list(LegalRateMapping.objects.all())
    index = calc.mapping_index(mappings)

    with_work = sorted({calc.month_key(f.date) for f in fees if f.date}
                       | {calc.month_key(r.date_reviewed) for r in savings if r.date_reviewed}
                       | {calc.month_key(a.date) for a in advisory if a.date})
    months = sorted(set(with_work) | {calc.month_key(timezone.localdate())})
    quarters = sorted({calc.quarter_of_month(m) for m in months})

    # Open on the last month that actually HAS work in it, not on today. Work is
    # written up after the month it belongs to, so defaulting to the current
    # month greets the officer with a screen of zeros over a month they have
    # not written up yet — and a screen of zeros reads as "nothing was done".
    default_month = with_work[-1] if with_work else months[-1]
    month = _valid_month(request.GET.get('month')) or default_month
    if month not in months:
        months = sorted(set(months) | {month})
    quarter = _valid_quarter(request.GET.get('quarter')) or calc.quarter_of_month(month)
    if quarter not in quarters:
        quarters = sorted(set(quarters) | {quarter})

    qmonths = calc.quarter_months(quarter)
    month_fees = [f for f in fees if f.date and calc.month_key(f.date) == month]
    month_savings = [r for r in savings if r.date_reviewed and calc.month_key(r.date_reviewed) == month]
    month_advisory = [a for a in advisory if a.date and calc.month_key(a.date) == month]
    quarter_savings = [r for r in savings if r.date_reviewed and calc.month_key(r.date_reviewed) in qmonths]
    quarter_fees = [f for f in fees if f.date and calc.month_key(f.date) in qmonths]

    month_totals = calc.totals_for_fees(month_fees, index)
    all_totals = calc.totals_for_fees(fees, index)
    q_saving = calc.savings_total(quarter_savings)
    q_bonus = calc.quarterly_bonus(q_saving, settings)
    entered = LegalMonthlyBonus.objects.filter(month=month).first()
    adv_hours = sum((a.hours for a in month_advisory), calc.ZERO)

    invoice_saving_month = calc.savings_total(month_savings)
    # The demo's basis: mapped external cost less ALL matter billing for the
    # month (monthlyExternalTotal - monthlyFeeTotal). The Monthly Fee Note report
    # uses the same figure, so the screen and the downloaded document agree — a
    # screen number that disagreed with the document it downloads is exactly the
    # "a second calculation that drifts" the office reported (H74).
    in_house_month = month_totals['external'] - month_totals['internal']
    pct_saved_month = (in_house_month / month_totals['external']) if month_totals['external'] else None
    advisory_month = calc.advisory_value(adv_hours, settings)

    # Savings per client, over every mapped fee line (not just this month) —
    # the officer is asked "what has this member cost us" without a date.
    by_client: dict[str, dict] = {}
    for f in fees:
        ext = calc.external_equivalent(f, index)
        if ext is None:
            continue
        e = by_client.setdefault(f.client, {'internal': calc.ZERO, 'external': calc.ZERO})
        e['internal'] += calc.internal_amount(f)
        e['external'] += ext
    client_rows = []
    for client, v in sorted(by_client.items()):
        sav = v['external'] - v['internal']
        client_rows.append({'client': client, 'internal': str(v['internal']),
                            'external': str(v['external']), 'saving': str(sav),
                            'pct_saved': float(sav / v['external']) if v['external'] else None})

    return Response({
        'settings': _settings_row(settings),
        'departments': DEPARTMENTS,
        'months': months,
        'quarters': quarters,
        'month': month,
        'quarter': quarter,
        'fee_notes': [calc.fee_line(f, index) for f in fees],
        'invoice_savings': [_saving_row(r, settings.sla_days) for r in savings],
        'advisory': [_advisory_row(a, settings) for a in advisory],
        'tariffs': [_tariff_row(t) for t in LegalTariffItem.objects.all()],
        'mappings': [_mapping_row(m) for m in mappings],
        'client_savings': client_rows,
        'summary': {
            # 1 — matter billing (monthly performance bonus basis)
            'matter_billing': str(month_totals['internal']),
            'monthly_bonus_entered': None if entered is None else str(entered.amount),
            'monthly_bonus': str(calc.monthly_bonus(None if entered is None else entered.amount, settings)),
            'monthly_bonus_cap': str(settings.monthly_bonus_cap),
            # 2 — invoice review (quarterly 2% bonus basis)
            'invoice_saving_month': str(invoice_saving_month),
            'quarter_saving': str(q_bonus['saving']),
            'quarter_threshold': str(q_bonus['threshold']),
            'quarter_met': q_bonus['met'],
            'quarter_bonus': str(q_bonus['bonus']),
            'quarter_months': [{'month': m,
                                'saving': str(calc.savings_total(
                                    [r for r in savings if r.date_reviewed
                                     and calc.month_key(r.date_reviewed) == m]))}
                               for m in qmonths],
            'quarter_in_house_saving': str(calc.totals_for_fees(quarter_fees, index)['saving']),
            # 3 — savings vs external attorney (illustrative), the demo's basis:
            # "Internal amount billed" is ALL matter billing, external is the
            # mapped panel cost, and the saving is external − ALL billing. On the
            # (all-mapped) real data internal == mapped_internal; the difference
            # only shows when a line has no panel match, which the unmapped count
            # below explains. Screen and Monthly Fee Note now share one basis.
            'mapped_internal_month': str(month_totals['mapped_internal']),
            'external_equivalent': str(month_totals['external']),
            'in_house_saving': str(in_house_month),
            'pct_saved': (float(pct_saved_month) if pct_saved_month is not None else None),
            'unmapped_lines_month': month_totals['unmapped'],
            'unmapped_lines_total': all_totals['unmapped'],
            'lifetime_internal': str(all_totals['internal']),
            'lifetime_mapped_internal': str(all_totals['mapped_internal']),
            'lifetime_external': str(all_totals['external']),
            'lifetime_saving': str(all_totals['saving']),
            'lifetime_pct_saved': (float(all_totals['pct_saved'])
                                   if all_totals['pct_saved'] is not None else None),
            # 4 — internal advisory (illustrative)
            'advisory_hours': str(adv_hours),
            'advisory_value': str(advisory_month),
            # 5 — total value delivered
            'total_value': str(invoice_saving_month + in_house_month + advisory_month),
            # SLA on the invoice review
            'sla': {k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in calc.sla_summary(savings, settings.sla_days).items()},
        },
    })


# --------------------------------------------------------------------------
# Write endpoints
# --------------------------------------------------------------------------

def _who(request):
    return (getattr(request.user, 'email', '') or '')[:200]


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def legal_create(request, slug):
    denied = _deny(request)
    if denied is not None:
        return denied
    entry = REGISTERS.get(slug)
    if entry is None:
        return Response({'detail': 'Unknown register.'}, status=status.HTTP_404_NOT_FOUND)
    model, apply = entry
    obj = model()
    try:
        apply(obj, request.data or {})
    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if hasattr(obj, 'updated_by_email'):
        obj.updated_by_email = _who(request)
    try:
        with transaction.atomic():
            obj.save(audit_user=request.user)
    except IntegrityError:
        return Response({'detail': 'That entry already exists.'},
                        status=status.HTTP_409_CONFLICT)
    return Response(_row_for(slug, obj, LegalSettings.solo()),
                    status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def legal_detail(request, slug, row_id):
    denied = _deny(request)
    if denied is not None:
        return denied
    entry = REGISTERS.get(slug)
    if entry is None:
        return Response({'detail': 'Unknown register.'}, status=status.HTTP_404_NOT_FOUND)
    model, apply = entry
    obj = model.objects.filter(id=row_id).first()
    if obj is None:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        obj.delete(audit_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
    # A PATCH carries the whole row from the screen; merge over what is stored so
    # a field the form does not send is not blanked.
    data = {**_row_for(slug, obj, LegalSettings.solo()), **(request.data or {})}
    try:
        apply(obj, data)
    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if hasattr(obj, 'updated_by_email'):
        obj.updated_by_email = _who(request)
    try:
        with transaction.atomic():
            obj.save(audit_user=request.user)
    except IntegrityError:
        return Response({'detail': 'That entry already exists.'},
                        status=status.HTTP_409_CONFLICT)
    return Response(_row_for(slug, obj, LegalSettings.solo()))


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def legal_settings(request):
    denied = _deny(request)
    if denied is not None:
        return denied
    s = LegalSettings.solo()
    data = request.data or {}
    try:
        s.quarterly_threshold = _dec(data.get('quarterly_threshold', s.quarterly_threshold),
                                     'Quarterly minimum trigger')
        s.quarterly_bonus_pct = _dec(data.get('quarterly_bonus_pct', s.quarterly_bonus_pct),
                                     'Quarterly bonus %')
        s.monthly_bonus_cap = _dec(data.get('monthly_bonus_cap', s.monthly_bonus_cap),
                                   'Monthly bonus cap')
        new_rate = _dec(data.get('external_hourly_rate', s.external_hourly_rate),
                        'External attorney hourly rate')
        sla = int(_dec(data.get('sla_days', s.sla_days), 'Invoice review SLA (days)'))
        cap = _dec(data.get('client_spend_cap', s.client_spend_cap),
                   'Client legal-spend cap')
        amber = _dec(data.get('client_spend_amber', s.client_spend_amber),
                     'Approaching-cap warning level')
    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if sla < 1:
        return Response({'detail': 'The SLA target must be at least one day.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if cap <= 0:
        return Response({'detail': 'The client legal-spend cap must be more than nought.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # An amber level at or above the cap is not an early warning at all — the
    # client would go straight from clear to red with nothing in between.
    if amber >= cap:
        return Response({'detail': 'The approaching-cap warning must be BELOW the cap, '
                                   'otherwise there is no early warning.'},
                        status=status.HTTP_400_BAD_REQUEST)
    s.client_spend_cap = cap
    s.client_spend_amber = amber
    if 'cap_blocks_capture' in data:
        # NOT bool(): a JSON client sending the STRING "false" would switch the
        # hard ceiling ON, because every non-empty string is truthy. Switching
        # a control on when somebody asked to switch it off is the worst
        # possible direction to get wrong, so an unrecognised value is refused
        # rather than guessed. (OpenAI, /fabe panel, 9 Sep 2026.)
        raw = data.get('cap_blocks_capture')
        if isinstance(raw, bool):
            s.cap_blocks_capture = raw
        elif str(raw).strip().lower() in ('true', '1', 'yes', 'on'):
            s.cap_blocks_capture = True
        elif str(raw).strip().lower() in ('false', '0', 'no', 'off', ''):
            s.cap_blocks_capture = False
        else:
            return Response({'detail': 'The hard-ceiling switch must be yes or no.'},
                            status=status.HTTP_400_BAD_REQUEST)
    s.sla_days = sla
    rate_changed = new_rate != s.external_hourly_rate
    s.external_hourly_rate = new_rate
    s.save()

    # The standard hourly rate drives every hourly comparison, so the hourly
    # mappings move with it. Disbursements (email, telephone) are billed on
    # their own rates and are deliberately left alone.
    moved = 0
    if rate_changed:
        for m in LegalRateMapping.objects.filter(calc_basis='per_hour', is_disbursement=False):
            m.external_rate = new_rate
            m.save(audit_user=request.user)
            moved += 1
    return Response({**_settings_row(s), 'mappings_repriced': moved})


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def legal_monthly_bonus(request):
    """Record (or clear) the monthly performance bonus for one month."""
    denied = _deny(request)
    if denied is not None:
        return denied
    data = request.data or {}
    try:
        month = _month(data.get('month'), 'Month')
    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    raw = data.get('amount')
    if raw is None or str(raw).strip() == '':
        # Through the instance, not the queryset: a queryset delete skips
        # AuditableMixin, and clearing a bonus figure is exactly the change
        # somebody will later need to see in the audit log.
        existing = LegalMonthlyBonus.objects.filter(month=month).first()
        if existing is not None:
            existing.delete(audit_user=request.user)
        return Response({'month': month, 'amount': None, 'applied': '0'})
    try:
        amount = _dec(raw, 'Amount')
    except FieldError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if amount < 0:
        return Response({'detail': 'The monthly bonus cannot be negative.'},
                        status=status.HTTP_400_BAD_REQUEST)
    row = LegalMonthlyBonus.objects.filter(month=month).first() or LegalMonthlyBonus(month=month)
    row.amount = amount
    row.updated_by_email = _who(request)
    row.save(audit_user=request.user)
    s = LegalSettings.solo()
    return Response({'month': month, 'amount': str(amount),
                     'applied': str(calc.monthly_bonus(amount, s)),
                     'capped': amount > s.monthly_bonus_cap})


# --------------------------------------------------------------------------
# GET /bonu/legal/report/<kind>/<fmt>/ — the two reports the office generates
# from the captured registers, as Word or PDF. Built server-side from the same
# figures the screens read, so there is one source of numbers.
# --------------------------------------------------------------------------

_CONTENT_TYPES = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
}


def _default_month(fees, savings, advisory):
    """The last month with any captured work — the month the screen opens on, so
    a report generated straight away covers a written-up month, not empty today."""
    with_work = sorted({calc.month_key(f.date) for f in fees if f.date}
                       | {calc.month_key(r.date_reviewed) for r in savings if r.date_reviewed}
                       | {calc.month_key(a.date) for a in advisory if a.date})
    return with_work[-1] if with_work else calc.month_key(timezone.localdate())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def legal_report(request, kind, fmt):
    denied = _deny(request)
    if denied is not None:
        return denied
    if kind not in ('feenote', 'quarterly') or fmt not in ('docx', 'pdf'):
        return Response({'detail': 'Unknown report.'}, status=status.HTTP_404_NOT_FOUND)

    settings = LegalSettings.solo()
    fees = list(LegalFeeNote.objects.all())
    savings = list(LegalInvoiceSaving.objects.all())
    advisory = list(LegalAdvisoryEntry.objects.all())
    mappings = list(LegalRateMapping.objects.all())

    if kind == 'feenote':
        month = _valid_month(request.GET.get('month')) or _default_month(fees, savings, advisory)
        entered = LegalMonthlyBonus.objects.filter(month=month).first()
        model = reports.feenote_model(month, fees, savings, mappings,
                                      None if entered is None else entered.amount, settings)
        data = reports.feenote_docx(model) if fmt == 'docx' else reports.feenote_pdf(model)
    else:
        month = _default_month(fees, savings, advisory)
        quarter = _valid_quarter(request.GET.get('quarter')) or calc.quarter_of_month(month)
        model = reports.quarterly_model(quarter, fees, savings, advisory, mappings, settings)
        data = reports.quarterly_docx(model) if fmt == 'docx' else reports.quarterly_pdf(model)

    resp = HttpResponse(data, content_type=_CONTENT_TYPES[fmt])
    resp['Content-Disposition'] = f'attachment; filename="{model["filename"]}.{fmt}"'
    return resp
