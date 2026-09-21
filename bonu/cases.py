"""bonu/cases.py — call-centre claim intake + the legal case register (Phase 1).

A member phones in with a legal matter. A call-centre agent OPENS a case here and
ALLOCATES it to a panel firm (or the in-house office). Every later movement is a
CaseEvent. This is the forward-looking register the Panel league and Retainer
scorecard have been measuring against an empty table — until now a matter only
became visible in Omni once its first bill arrived (backward-looking).

No journal, and nothing here pays anybody. `member_ref` is a SCHEME reference,
never a name — the same discipline as the rest of BONU. Access is the narrow
intake gate (`bonu.access.can_capture_claim`): finance / BONU team, OR a named
call-centre agent who sees the register and nothing else.

TWO GATES ON THIS ONE SCREEN (Fable, /fabe gate, 9 Sep 2026)
------------------------------------------------------------
Kelvin Kimani's spec put a client's legal-spend total and their bills onto this
register. The intake gate is deliberately WIDER than the BONU financial gate,
so serving those to everyone who can open a claim would have quietly widened a
gate that was built narrow. The screen therefore has two gates:
`can_capture_claim` to see and open matters, and `_may_see_money`
(`can_view_bonu`) for the spend and the bills. The client's NAME is on the
wide side by the CFO's decision of 9 Sep 2026 — an agent is speaking to the
person, and a name reveals nothing about money.
"""
from __future__ import annotations

import datetime

from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# Sentinel so a *bad* date (garbage the user typed) is told apart from an *absent*
# one (a blank optional field). Absent → default; bad → 400.
_BAD_DATE = object()


def _deny_intake(request):
    from bonu.access import can_capture_claim
    if can_capture_claim(request.user):
        return None
    return Response(
        {'detail': 'Claim intake is restricted to the call-centre and BONU teams.'},
        status=status.HTTP_403_FORBIDDEN,
    )


def _date(val, default=None):
    """'' / None → default; a real ISO date → date; anything else → _BAD_DATE."""
    if val in (None, ''):
        return default
    try:
        return datetime.date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return _BAD_DATE


def _meta():
    from bonu.legal_rules import BOTSWANA_REGIONS
    from bonu.models import BonuInvoiceLine, CaseEvent, LegalCase
    from bonu.views import _firms
    return {
        'firms': [{'id': str(f.pk), 'name': f.name} for f in _firms()],
        'matter_types': [{'value': v, 'label': l} for v, l in BonuInvoiceLine.MatterType.choices],
        'statuses': [{'value': v, 'label': l} for v, l in LegalCase.Status.choices],
        'event_kinds': [{'value': v, 'label': l} for v, l in CaseEvent.Kind.choices],
        'firm_types': [{'value': v, 'label': l} for v, l in LegalCase.FirmType.choices],
        'regions': list(BOTSWANA_REGIONS),
    }


def _case_json(c, with_events=False, cap=None, show_money=False):
    """One case as JSON.

    `show_money` is the whole reason this function takes a flag. Intake sits
    behind `can_capture_claim`, which deliberately admits a call-centre agent
    who must see the register and no FINANCIALS — that separation is the point
    of the narrow gate. Kelvin Kimani's spec then put three things on this same
    register that had never been on it: the client's legal-spend total, the
    bills on the matter, and the client's name. Only the first two are gated —
    see below.

    Fable caught all three going to an intake-only agent at the /fabe gate on
    9 Sep 2026, with a live probe: an account admitted only via
    BONU_INTAKE_EMAILS was served cap.total = 70,000.00, the full bill list and
    the name. The CFO then ruled, as data controller, that the NAME may be
    shown to call-centre staff — they are talking to the person, so it helps
    them and tells them nothing about money — while the SPEND and the BILLS
    stay behind `can_view_bonu`. So the split here is money, not identity.
    """
    data = {
        'id': str(c.pk), 'case_ref': c.case_ref,
        # `firm` is now whoever is running the matter, in-house included, so
        # every existing column keeps working; `firm_id` is empty for in-house.
        'firm': c.handler,
        'firm_id': str(c.firm_id) if c.firm_id else '',
        'firm_type': c.firm_type, 'firm_type_label': c.get_firm_type_display(),
        'internal_officer': c.internal_officer,
        'is_in_house': c.is_in_house,
        'region': c.region,
        'member_ref': c.member_ref,
        # The name is shown to everyone who can open a claim (CFO decision,
        # 9 Sep 2026 — a call-centre agent is speaking to the person). It is
        # the MONEY below that is gated, not who the client is.
        'client_id': str(c.client_id) if c.client_id else '',
        'client_linked': bool(c.client_id),
        'client': c.client.full_name if c.client_id else '',
        'matter_type': c.matter_type, 'matter_label': c.get_matter_type_display(),
        'status': c.status, 'status_label': c.get_status_display(), 'is_open': c.is_open,
        'received_on': c.received_on.isoformat() if c.received_on else '',
        'date_of_loss': c.date_of_loss.isoformat() if c.date_of_loss else '',
        'matter_arose_on': c.matter_arose_on.isoformat() if c.matter_arose_on else '',
        'firm_contact_on': c.firm_contact_on.isoformat() if c.firm_contact_on else '',
        'instructed_on': c.instructed_on.isoformat() if c.instructed_on else '',
        'first_action_on': c.first_action_on.isoformat() if c.first_action_on else '',
        'last_activity_on': c.last_activity_on.isoformat() if c.last_activity_on else '',
        'next_action_due': c.next_action_due.isoformat() if c.next_action_due else '',
        'court_date': c.court_date.isoformat() if c.court_date else '',
        'closed_on': c.closed_on.isoformat() if c.closed_on else '',
        'days_quiet': c.days_quiet(),
        # None, not 0, when we were never told when the claim arrived.
        'days_to_process': c.days_to_process(),
        'outcome_note': c.outcome_note,
    }
    # What has been billed on THIS matter, so opening a claim shows its legal
    # spend and not only the matter detail — money, so gated.
    if show_money:
        allocs = list(c.bill_allocations.all())
        data['bills'] = [{
            'id': str(a.bill_id), 'reference': a.bill.reference,
            'biller': a.bill.biller, 'bill_date': a.bill.bill_date.isoformat(),
            'stage': a.bill.stage, 'stage_label': a.bill.get_stage_display(),
            'amount': str(a.amount),
        } for a in allocs]
        from decimal import Decimal as _D
        data['billed_total'] = str(sum((a.amount for a in allocs), _D('0')))
        if cap is not None:
            data['cap'] = cap
    if with_events:
        data['events'] = [{
            'id': str(e.pk), 'happened_on': e.happened_on.isoformat(),
            'kind': e.kind, 'kind_label': e.get_kind_display(),
            'detail': e.detail, 'reported_by': e.reported_by,
        } for e in c.events.all()]
    return data


def _looks_like_uuid(s):
    import uuid as _uuid
    try:
        _uuid.UUID(str(s))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _active_firm(fid):
    """Active LawFirm by id, tolerating a non-UUID id (→ None → a clean 400, not a 500)."""
    from django.core.exceptions import ValidationError
    from bonu.models import LawFirm
    if not fid:
        return None
    try:
        return LawFirm.objects.filter(pk=fid, is_active=True).first()
    except (ValidationError, ValueError):
        return None


#: The four key dates in the order they normally happen. Kelvin Kimani's spec
#: recommends a SOFT warning rather than a hard block, and that is right: a
#: reversed pair is usually a typo, but genuine exceptions exist and refusing
#: the claim over one would stop a real matter being opened at all.
_DATE_ORDER = (('date of loss', 0), ('date the matter arose', 1),
               ('law firm contact date', 2), ('law firm instruction date', 3))


def _order_warning(*dates):
    """A plain-English note when the four key dates run backwards, or ''."""
    named = [(label, dates[i]) for label, i in _DATE_ORDER if dates[i]]
    for (a_label, a), (b_label, b) in zip(named, named[1:]):
        if a > b:
            return (f'Check the dates: the {a_label} ({a}) is after the {b_label} ({b}). '
                    f'Saved as entered — correct it if it was a slip.')
    return ''


def _may_see_money(user) -> bool:
    """May this person see legal SPEND on the register?

    The intake gate (`can_capture_claim`) is deliberately wider than the BONU
    financial gate: it lets in a call-centre agent so a member's call can be
    turned into a matter. That agent must not be handed the client's legal
    spend or their bills just because those fields now live on the same screen.
    So MONEY answers to `can_view_bonu`, never to the intake gate.

    The client's NAME is deliberately NOT gated here — the CFO ruled on
    9 Sep 2026, as data controller, that an agent is speaking to the person and
    a name says nothing about money. The line this draws is money, not identity.
    """
    from bonu.access import can_view_bonu
    return can_view_bonu(user)


def _caps_for(cases):
    """{client_id: cap flag} for the clients on these cases, in one query."""
    from bonu.legal_bills import cap_state, client_spend
    from bonu.models import LegalSettings
    ids = {c.client_id for c in cases if c.client_id}
    if not ids:
        return {}
    settings = LegalSettings.solo()
    totals = client_spend(ids)
    import decimal
    return {i: cap_state(totals.get(i, decimal.Decimal('0')), settings) for i in ids}


class ClientRefused(Exception):
    """An explicitly named client that does not exist. Never a fallback."""


def _client_for(member_ref, explicit_id=None):
    """The structured client for a claim, or None.

    Two DIFFERENT cases, and the difference matters:

    * **A scheme reference that is not on the roll** → None, and the case still
      opens. The reference the agent types IS the union membership number, so
      the link normally comes for free; when it misses, refusing would push a
      real matter off the register entirely, which is worse than a case that
      shows up as needing a link. It is warned about in the response and
      counted in `unlinked_clients`, so it cannot go unnoticed.

    * **An explicitly named `client_id` that does not resolve** → refused.
      Somebody said exactly which client this is; silently ignoring that and
      opening the case unlinked is the fallback trap — worse, it also skipped
      the `member_ref` lookup that WOULD have linked it, and then blamed
      `member_ref` in the warning. `case_detail` already refused this; the
      create path did not. (Fable, /fabe gate, 9 Sep 2026.)
    """
    from django.core.exceptions import ValidationError
    from bonu.models import BonuMember
    if explicit_id:
        try:
            found = BonuMember.objects.filter(pk=explicit_id).first()
        except (ValidationError, ValueError):
            found = None
        if found is None:
            raise ClientRefused('That client is not on the membership register.')
        return found
    if not member_ref:
        return None
    return BonuMember.objects.filter(membership_no=member_ref).first()


def _next_case_ref(year):
    """An internal reference for a matter opened before the firm gives us its own.
    Sequential within the year; the model's uniqueness is (firm, case_ref)."""
    from bonu.models import LegalCase
    prefix = f'BL-{year}-'
    n = LegalCase.objects.filter(case_ref__startswith=prefix).count() + 1
    return f'{prefix}{n:05d}'


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def cases(request):
    """GET  /api/v1/bonu/cases/  — the case register (+ the form's dropdown options).
    POST /api/v1/bonu/cases/  — open a new claim and allocate it to a firm.
    """
    denied = _deny_intake(request)
    if denied is not None:
        return denied
    from bonu.models import BonuInvoiceLine, CaseEvent, LegalCase

    if request.method == 'GET':
        qs = (LegalCase.objects.select_related('firm', 'client')
              .prefetch_related('events', 'bill_allocations__bill'))
        st = (request.query_params.get('status') or '').strip()
        firm_id = (request.query_params.get('firm_id') or '').strip()
        member = (request.query_params.get('member_ref') or '').strip()
        region = (request.query_params.get('region') or '').strip()
        firm_type = (request.query_params.get('firm_type') or '').strip()
        if st:
            qs = qs.filter(status=st)
        if firm_id and _looks_like_uuid(firm_id):  # a garbage id filters nothing, never 500s
            qs = qs.filter(firm_id=firm_id)
        if member:
            qs = qs.filter(member_ref=member)
        if region:
            qs = qs.filter(region=region)
        if firm_type in {v for v, _ in LegalCase.FirmType.choices}:
            qs = qs.filter(firm_type=firm_type)
        if (request.query_params.get('open') or '').strip() == '1':
            qs = qs.filter(status__in=LegalCase.OPEN_STATUSES)
        cases = list(qs[:500])
        # The cap flag travels WITH the case, so a ceiling cannot be crossed on
        # one matter while another quietly pushed the same client over. It is
        # only computed, and only sent, for someone allowed to see the money.
        money = _may_see_money(request.user)
        caps = _caps_for(cases) if money else {}
        rows = [_case_json(c, cap=caps.get(c.client_id), show_money=money)
                for c in cases]
        return Response({
            'cases': rows, 'shown': len(rows),
            'open_count': LegalCase.objects.filter(status__in=LegalCase.OPEN_STATUSES).count(),
            'total': LegalCase.objects.count(),
            'unlinked_clients': LegalCase.objects.filter(client__isnull=True).count(),
            'meta': _meta(),
        })

    # ---- POST: open a claim ---------------------------------------------------
    data = request.data or {}

    # In-house or external. One or the other, and the pairing is checked
    # positively both ways: an external matter must name a firm, and an
    # in-house one must not carry one. A field silently ignored is how a matter
    # ends up recorded against the wrong handler.
    firm_type = (data.get('firm_type') or LegalCase.FirmType.EXTERNAL).strip()
    if firm_type not in {v for v, _ in LegalCase.FirmType.choices}:
        return Response({'detail': 'Say whether this matter is in-house or with an '
                                   'external law firm.'},
                        status=status.HTTP_400_BAD_REQUEST)

    firm = None
    if firm_type == LegalCase.FirmType.EXTERNAL:
        firm = _active_firm((data.get('firm_id') or '').strip())
        if firm is None:
            return Response({'detail': 'Choose an active panel firm to allocate this '
                                       'matter to.'},
                            status=status.HTTP_400_BAD_REQUEST)
    elif (data.get('firm_id') or '').strip():
        return Response({'detail': 'An in-house matter cannot also be allocated to an '
                                   'external law firm.'},
                        status=status.HTTP_400_BAD_REQUEST)

    from bonu.legal_rules import clean_region
    region = clean_region(data.get('region'))
    if region is None:
        # Refused, not defaulted. An unrecognised town must never be quietly
        # filed as "Other" or as Gaborone — that is the fallback trap.
        return Response({'detail': 'Choose the region from the list of Botswana cities '
                                   'and towns.'},
                        status=status.HTTP_400_BAD_REQUEST)

    member_ref = (data.get('member_ref') or '').strip()[:80]
    if not member_ref:
        return Response({'detail': 'Enter the member’s scheme reference (a reference, never a name).'},
                        status=status.HTTP_400_BAD_REQUEST)

    valid_matter = {v for v, _ in BonuInvoiceLine.MatterType.choices}
    matter_type = (data.get('matter_type') or '').strip()
    if matter_type not in valid_matter:
        matter_type = BonuInvoiceLine.MatterType.OTHER

    instructed_on = _date(data.get('instructed_on'), default=timezone.localdate())
    if instructed_on is _BAD_DATE:
        return Response({'detail': 'Instructed date is not a valid date.'},
                        status=status.HTTP_400_BAD_REQUEST)
    next_due = _date(data.get('next_action_due'))
    court_date = _date(data.get('court_date'))
    # The claim clock starts when the claim REACHED us. Defaulted to today at
    # intake rather than left empty, because a call-centre agent opening a claim
    # is the moment it arrived — but it stays editable for a back-dated one.
    received_on = _date(data.get('received_on'), default=timezone.localdate())
    date_of_loss = _date(data.get('date_of_loss'))
    matter_arose_on = _date(data.get('matter_arose_on'))
    firm_contact_on = _date(data.get('firm_contact_on'))
    if _BAD_DATE in (next_due, court_date, received_on, date_of_loss,
                     matter_arose_on, firm_contact_on):
        return Response({'detail': 'A date field is not a valid date.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # A claim cannot have reached us tomorrow. This one IS a hard refusal, not
    # a warning, because days-to-process counts from here: a future date makes
    # the clamp report nought days, which reads on screen as "dealt with the
    # same day" — the most flattering possible lie about a matter nobody has
    # touched. (Fable, /fabe gate, 9 Sep 2026.)
    #
    # `timezone.localdate()`, NEVER `date.today()`: the box runs UTC and
    # TIME_ZONE is Africa/Gaborone, so the server clock is YESTERDAY between
    # 00:00 and 02:00 local. With the server clock this guard would REFUSE a
    # claim correctly dated today for those two hours every night. That is the
    # third time this module family has hit it — see bonu/confirm.py and
    # bonu/test_confirm_timezone.py.
    if received_on and received_on is not _BAD_DATE and received_on > timezone.localdate():
        return Response({'detail': 'The claim cannot have been received in the future.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # A SOFT warning on the order, not a hard block: loss <= matter arose <=
    # contact <= instruction is normally right, and a reversed pair is usually a
    # typo — but genuine exceptions exist, and refusing the claim over one would
    # stop a real matter being opened.
    date_warning = _order_warning(date_of_loss, matter_arose_on, firm_contact_on,
                                  instructed_on)

    case_ref = (data.get('case_ref') or '').strip()[:80] or _next_case_ref(instructed_on.year)
    if LegalCase.objects.filter(firm=firm, case_ref=case_ref).exists():
        who = firm.name if firm else 'The in-house office'
        return Response({'detail': f'{who} already has a case {case_ref}.'},
                        status=status.HTTP_409_CONFLICT)

    try:
        client = _client_for(member_ref, (data.get('client_id') or '').strip())
    except ClientRefused as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    try:
        case = LegalCase.objects.create(
            firm_type=firm_type, firm=firm,
            internal_officer=(data.get('internal_officer') or '').strip()[:120]
            if firm_type == LegalCase.FirmType.IN_HOUSE else '',
            region=region,
            client=client,
            case_ref=case_ref, member_ref=member_ref, matter_type=matter_type,
            status=LegalCase.Status.INSTRUCTED, instructed_on=instructed_on,
            received_on=received_on or None, date_of_loss=date_of_loss or None,
            matter_arose_on=matter_arose_on or None, firm_contact_on=firm_contact_on or None,
            last_activity_on=instructed_on,
            next_action_due=next_due or None, court_date=court_date or None,
        )
    except IntegrityError:
        # Two intake requests raced to the same (firm, case_ref) — the .exists()
        # pre-check read False for both. The DB constraint is the real guard;
        # answer 409 rather than a 500 (auto-refs simply retry with a fresh count).
        who = firm.name if firm else 'The in-house office'
        return Response({'detail': f'{who} already has a case {case_ref}. Please try again.'},
                        status=status.HTTP_409_CONFLICT)
    # The opening is itself the first provable movement, so the register is never
    # a case with no events (which reads as "nothing happened").
    CaseEvent.objects.create(
        case=case, happened_on=instructed_on, kind=CaseEvent.Kind.INSTRUCTED,
        detail=(data.get('description') or '').strip()[:4000],
        reported_by=(data.get('reported_by') or 'Call centre').strip()[:120],
    )
    case = (LegalCase.objects.select_related('firm', 'client')
            .prefetch_related('events', 'bill_allocations__bill').get(pk=case.pk))
    money = _may_see_money(request.user)
    out = {'ok': True, 'case': _case_json(
        case, with_events=True, show_money=money,
        cap=_caps_for([case]).get(case.client_id) if money else None)}
    if date_warning:
        out['warning'] = date_warning
    if not case.client_id:
        out['warning_client'] = (
            f'Opened, but {member_ref} is not on the current membership list — so this '
            f'matter is not yet counted in any client legal-spend total. Link it to a '
            f'client to bring it into the cap.')
    return Response(out, status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def case_detail(request, case_id):
    """GET  — one case + its full event timeline.
    POST — update status / allocation / dates / outcome (all optional, only what changes).
    """
    denied = _deny_intake(request)
    if denied is not None:
        return denied
    from bonu.models import LegalCase

    c = (LegalCase.objects.select_related('firm', 'client')
         .prefetch_related('events', 'bill_allocations__bill')
         .filter(pk=case_id).first())
    if c is None:
        return Response({'detail': 'That case is not on file.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        money = _may_see_money(request.user)
        return Response({'case': _case_json(
            c, with_events=True, show_money=money,
            cap=_caps_for([c]).get(c.client_id) if money else None)})

    data = request.data or {}
    fields = []

    if 'status' in data:
        st = (data.get('status') or '').strip()
        if st not in {v for v, _ in LegalCase.Status.choices}:
            return Response({'detail': 'Unknown case status.'}, status=status.HTTP_400_BAD_REQUEST)
        c.status = st
        fields.append('status')
        if st not in LegalCase.OPEN_STATUSES and not c.closed_on:
            c.closed_on = timezone.localdate()
            fields.append('closed_on')
        elif st in LegalCase.OPEN_STATUSES and c.closed_on:
            c.closed_on = None            # reopened — clear the stale close date
            fields.append('closed_on')

    # Moving a matter between in-house and an external firm changes BOTH fields
    # together, always — setting one without the other is exactly what the
    # database constraint refuses, and it should be refused here in words
    # rather than surfacing as a 500 from the constraint.
    if 'firm_type' in data:
        ft = (data.get('firm_type') or '').strip()
        if ft not in {v for v, _ in LegalCase.FirmType.choices}:
            return Response({'detail': 'Say whether this matter is in-house or with an '
                                       'external law firm.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if ft == LegalCase.FirmType.IN_HOUSE:
            c.firm_type, c.firm = ft, None
            fields += ['firm_type', 'firm']
        else:
            firm = _active_firm((data.get('firm_id') or '').strip())
            if firm is None:
                return Response({'detail': 'Moving a matter to an external firm needs '
                                           'the firm it is going to.'},
                                status=status.HTTP_400_BAD_REQUEST)
            c.firm_type, c.firm = ft, firm
            fields += ['firm_type', 'firm']
    elif 'firm_id' in data:  # re-allocate between external firms
        if c.is_in_house:
            return Response({'detail': 'This matter is in-house. Change it to external '
                                       'first, naming the firm.'},
                            status=status.HTTP_400_BAD_REQUEST)
        firm = _active_firm((data.get('firm_id') or '').strip())
        if firm is None:
            return Response({'detail': 'Choose an active panel firm.'},
                            status=status.HTTP_400_BAD_REQUEST)
        c.firm = firm
        fields.append('firm')

    if 'internal_officer' in data:
        c.internal_officer = (data.get('internal_officer') or '').strip()[:120]
        fields.append('internal_officer')

    if 'region' in data:
        from bonu.legal_rules import clean_region
        region = clean_region(data.get('region'))
        if region is None:
            return Response({'detail': 'Choose the region from the list of Botswana '
                                       'cities and towns.'},
                            status=status.HTTP_400_BAD_REQUEST)
        c.region = region
        fields.append('region')

    if 'client_id' in data:
        try:
            c.client = _client_for('', (data.get('client_id') or '').strip())
        except ClientRefused as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        fields.append('client')

    for key in ('next_action_due', 'court_date', 'received_on', 'date_of_loss',
                'matter_arose_on', 'firm_contact_on'):
        if key in data:
            d = _date(data.get(key))
            if d is _BAD_DATE:
                return Response({'detail': f'{key} is not a valid date.'},
                                status=status.HTTP_400_BAD_REQUEST)
            setattr(c, key, d or None)
            fields.append(key)

    if 'outcome_note' in data:
        c.outcome_note = (data.get('outcome_note') or '').strip()[:4000]
        fields.append('outcome_note')

    if not fields:
        return Response({'detail': 'Nothing to update.'}, status=status.HTTP_400_BAD_REQUEST)

    # The same two impossibilities the create path refuses, checked again here
    # because an EDIT can create them just as easily: a receipt date in the
    # future, or one pushed past the closure date (which would make
    # days-to-process clamp to nought and read as same-day).
    if c.received_on and c.received_on > timezone.localdate():
        return Response({'detail': 'The claim cannot have been received in the future.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if c.received_on and c.closed_on and c.received_on > c.closed_on:
        return Response({'detail': 'The claim cannot have been received after it was '
                                   'closed. Correct one of the two dates.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        c.save(update_fields=list(dict.fromkeys(fields)) + ['updated_at'])
    except IntegrityError:
        # Re-allocated to a firm that already holds this file ref → graceful 409.
        return Response({'detail': f'{c.handler} already has a case {c.case_ref}.'},
                        status=status.HTTP_409_CONFLICT)
    # Whether a bill counts toward a client's cap depends on THIS row's client,
    # so moving the client link has to correct the bills that were captured
    # before it moved — otherwise the cap board shows a bill's money in a
    # client total AND lists it as belonging to nobody, at the same time.
    restated = 0
    cap_now = None
    if 'client' in fields:
        from bonu.legal_bills import cap_after_restating, restate_bills_for_case
        restated = restate_bills_for_case(c)
        # Linking a client can carry bills into their total by a route the hard
        # ceiling never sees, so say so rather than let it land quietly.
        cap_now = cap_after_restating(c)

    c = (LegalCase.objects.select_related('firm', 'client')
         .prefetch_related('events', 'bill_allocations__bill').get(pk=c.pk))
    money = _may_see_money(request.user)
    out = {'ok': True, 'case': _case_json(
        c, with_events=True, show_money=money,
        cap=_caps_for([c]).get(c.client_id) if money else None)}
    warning = _order_warning(c.date_of_loss, c.matter_arose_on, c.firm_contact_on,
                             c.instructed_on)
    if warning:
        out['warning'] = warning
    if restated:
        out['bills_restated'] = restated
    if cap_now:
        out['cap_breaches'] = [cap_now]
        out['warning_cap'] = (
            f'Linking this client brings their legal spend to {cap_now["total"]} '
            f'against a cap of {cap_now["cap"]} — {cap_now["label"].lower()}.')
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def case_event_add(request, case_id):
    """POST /api/v1/bonu/cases/<id>/events/ — log a movement on a case.

    An event is the smallest provable unit of activity and is what drives
    `last_activity_on`, and therefore every "gone quiet" measure downstream.
    """
    denied = _deny_intake(request)
    if denied is not None:
        return denied
    from bonu.models import CaseEvent, LegalCase

    c = LegalCase.objects.filter(pk=case_id).first()
    if c is None:
        return Response({'detail': 'That case is not on file.'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    kind = (data.get('kind') or '').strip()
    if kind not in {v for v, _ in CaseEvent.Kind.choices}:
        kind = CaseEvent.Kind.UPDATE
    happened_on = _date(data.get('happened_on'), default=timezone.localdate())
    if happened_on is _BAD_DATE:
        return Response({'detail': 'Event date is not a valid date.'},
                        status=status.HTTP_400_BAD_REQUEST)

    CaseEvent.objects.create(
        case=c, happened_on=happened_on, kind=kind,
        detail=(data.get('detail') or '').strip()[:4000],
        reported_by=(data.get('reported_by') or '').strip()[:120],
    )
    touched = ['updated_at']
    if not c.last_activity_on or happened_on > c.last_activity_on:
        c.last_activity_on = happened_on
        touched.append('last_activity_on')
    # First action = the EARLIEST real movement, not the first one keyed in — a
    # back-dated event must be able to pull it earlier.
    if kind != CaseEvent.Kind.INSTRUCTED:
        earliest = happened_on if not c.first_action_on else min(c.first_action_on, happened_on)
        if earliest != c.first_action_on:
            c.first_action_on = earliest
            touched.append('first_action_on')
    c.save(update_fields=list(dict.fromkeys(touched)))

    c = LegalCase.objects.select_related('firm').prefetch_related('events').get(pk=c.pk)
    return Response({'ok': True,
                     'case': _case_json(c, with_events=True,
                                        show_money=_may_see_money(request.user))},
                    status=status.HTTP_201_CREATED)
