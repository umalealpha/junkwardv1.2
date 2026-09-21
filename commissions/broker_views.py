"""commissions/broker_views.py — the Broker Commission screen's API.

Access (CFO 2026-09-08): the five named people plus any commissions reviewer or
superuser. Read AND write — the CFO asked explicitly that their access not be
limited. Customer names are returned to this auth-gated screen only and must
never be put in an AI prompt, a log line or a scheduled email
(AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import datetime as _dt
import logging
import re as _re
import os
import tempfile

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from commissions import brokers as svc
from commissions.models import Broker, BrokerPolicy
from django.utils import timezone

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 60 * 1024 * 1024


def _clean_period(raw):
    """'' or a valid YYYY-MM; None means the caller sent something else.

    The model's validator does NOT run on update_or_create, so a truncated
    '2026-8' would land in the unique key and then never match a month filter —
    an invisible row rather than an error.
    """
    p = str(raw or '').strip()
    if not p:
        return ''
    return p if _re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', p) else None


# Board item [C8]: the register answers to ONE named role and one permission,
# and everyone else has no access. Both are created by
# `manage.py seed_broker_commission_role`; membership is then maintained on the
# roles screen, never in code. The command imports these two names from here so
# the code and the seeder can never drift into two spellings — a mismatch would
# lock every member out and look like a permissions bug.
BROKER_COMMISSION_ROLE = 'BROKER_COMMISSION_FULL'
BROKER_COMMISSION_PERMISSION = 'broker-commission.full'


class CanUseBrokerModule(BasePermission):
    message = ('The broker commission register is restricted to the Broker '
               'Commission - Full Access role.')

    def has_permission(self, request, view) -> bool:
        u = getattr(request, 'user', None)
        if not (u and getattr(u, 'is_authenticated', False)):
            return False
        # Being a commission-stage reviewer is NOT enough. It used to be, and
        # that let a wider group than Finance named insert policies and edit
        # aliases on a register that feeds what brokers get paid.
        #
        # `user_has_permission` is the canonical check (core/models.py says so
        # in as many words). Going straight to UserRoleAssignment here missed
        # `expires_at`, so a time-boxed grant to an auditor or a contractor
        # would never have expired on this screen.
        from core.models import user_has_permission
        return user_has_permission(u, BROKER_COMMISSION_PERMISSION)


def _broker_json(b: Broker, counts: dict) -> dict:
    c = counts.get(b.id, {})
    return {
        'id': str(b.id), 'name': b.name, 'short_name': b.short_name,
        'is_active': b.is_active, 'notes': b.notes,
        'aliases': [a.graphite_agency_name for a in b.aliases.all()],
        'graphite_policies': c.get('policies', 0),
        'live_policies': c.get('live_policies', 0),
        'annual_premium': c.get('annual_premium', 0.0),
        'added_rows': b.added_rows,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_list(request):
    """Every broker, with their live commercial + domestic book from Graphite."""
    from realpay import graphite_feed
    book = graphite_feed.broker_book()
    by_agency = {r['agency']: r for r in book.get('rows', [])}
    from django.db.models import Count
    # annotate the count instead of b.policies.count() per broker — that loaded
    # every BrokerPolicy row (names included) just to count them.
    all_brokers = Broker.objects.prefetch_related('aliases').annotate(added_rows=Count('policies'))
    # C3 — an inactive broker is hidden wherever a person picks a broker.
    # ?include_inactive=1 is the deliberate way to see them.
    include_inactive = (request.query_params.get('include_inactive') or '').lower() in ('1', 'true', 'yes')
    qs = [b for b in all_brokers if include_inactive or b.is_active]
    counts: dict = {}
    for b in qs:
        agg = {'policies': 0, 'live_policies': 0, 'annual_premium': 0.0}
        for al in b.aliases.all():
            r = by_agency.get(al.graphite_agency_name)
            if r:
                agg['policies'] += r['policies']
                agg['live_policies'] += r['live_policies']
                agg['annual_premium'] += r['annual_premium']
        counts[b.id] = agg
    rows = sorted((_broker_json(b, counts) for b in qs),
                  key=lambda r: (-r['graphite_policies'], r['name']))

    # Agencies writing broker business that no Broker owns yet — this is exactly
    # the question Rose asked ("which brokers need tabs created?"), answered from
    # data instead of by eye.
    # .all() rides the prefetch; .values_list() would re-query per broker.
    # Claimed by ANY broker, active or not — an inactive broker's names are not
    # "unclaimed" and must not be offered for re-adding.
    claimed = {a.graphite_agency_name for b in all_brokers for a in b.aliases.all()}
    unclaimed = [r for r in book.get('rows', [])
                 if r['agency'] not in claimed and not svc.is_direct_channel(r['agency'])]
    return Response({
        'brokers': rows,
        'graphite_live': bool(book.get('configured')),
        'unclaimed_agencies': sorted(unclaimed, key=lambda r: -r['policies'])[:50],
        'possible_duplicates': svc.possible_duplicates(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_detail(request, pk):
    try:
        b = Broker.objects.prefetch_related('aliases', 'policies').get(pk=pk)
    except Broker.DoesNotExist:
        return Response({'detail': 'No such broker.'}, status=404)
    active_only = (request.query_params.get('active_only') or '').lower() in ('1', 'true', 'yes')
    # A broker sheet pays on what was collected in ONE month — that is what
    # Finance's 'Current Status' column means. Default to the current month so
    # the screen opens on a figure that can be reconciled, never an all-history
    # mix (the same lesson as the RealPay collections dashboard, 2026-09-07).
    period = (request.query_params.get('period') or '').strip()
    start = end = None
    if period:
        try:
            y, m = period.split('-')
            start = _dt.date(int(y), int(m), 1)
            end = (_dt.date(int(y) + (m == '12'), (int(m) % 12) + 1, 1) - _dt.timedelta(days=1))
        except (ValueError, TypeError):
            return Response({'detail': 'Month must look like 2026-08.'}, status=400)
    else:
        today = timezone.localdate()
        start, end = today.replace(day=1), today
        period = today.strftime('%Y-%m')
    data = svc.broker_clients(b, active_only=active_only, start=start, end=end)
    data['period'] = period
    return Response({
        'broker': {'id': str(b.id), 'name': b.name, 'short_name': b.short_name,
                   'notes': b.notes, 'is_active': b.is_active,
                   'aliases': [a.graphite_agency_name for a in b.aliases.all()]},
        **data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_sync(request):
    """Rebuild the register from Graphite. Idempotent; never deletes a broker."""
    return Response(svc.sync_brokers_from_graphite())



@api_view(['GET'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_policy_lookup(request):
    """C3 — look a policy up in Graphite before it is added.

    Three outcomes, kept apart on purpose:
      found=True                    -> prefill the form
      found=False, reachable=True   -> Graphite has no such policy; BLOCK the add
      reachable=False               -> Graphite is unreachable; do NOT claim the
                                       policy is missing, and do not block on a
                                       fact we could not establish.
    Also reports whether another broker already holds it, so the screen can warn
    before the user types the rest of the row.
    """
    from realpay import graphite_feed
    pol = str(request.GET.get('policy_number') or '').strip()
    if not pol:
        return Response({'detail': 'A policy number is required.'}, status=400)

    held = (BrokerPolicy.objects.filter(policy_number__iexact=pol)
            .select_related('broker').first())
    held_by = ({'id': str(held.broker_id), 'name': held.broker.name}
               if held else None)

    row = graphite_feed.policy_lookup(pol)
    if row is None:
        return Response({'reachable': False, 'found': False, 'policy': None,
                         'held_by': held_by,
                         'detail': 'Graphite could not be reached, so this policy '
                                   'could not be checked.'})
    if not row:
        return Response({'reachable': True, 'found': False, 'policy': None,
                         'held_by': held_by,
                         'detail': f'Graphite has no policy {pol}.'})
    return Response({'reachable': True, 'found': True, 'held_by': held_by,
                     'policy': {
                         'policy_number':  row.get('policy_number') or pol,
                         'insured_name':   row.get('insured_name') or '',
                         'agency':         row.get('agency') or '',
                         'premium':        str(row.get('premium') or '0'),
                         'annual_premium': str(row.get('annual_premium') or '0'),
                     }})


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_add_policy(request, pk):
    try:
        b = Broker.objects.get(pk=pk)
    except Broker.DoesNotExist:
        return Response({'detail': 'No such broker.'}, status=404)
    if not b.is_active:
        # C3 — an inactive broker is not offered in the picker, and the server
        # refuses it too so a stale screen or a hand-made request cannot.
        return Response({'detail': f'{b.name} is marked inactive, so policies cannot '
                                   f'be added to it.'}, status=400)
    pol = str(request.data.get('policy_number') or '').strip()
    if not pol:
        return Response({'detail': 'A policy number is required.'}, status=400)

    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    def money(k):
        try:
            # HALF UP, not Python's default HALF_EVEN — rounding is a tax
            # decision and VAT rounds half up (CFO standing order).
            return Decimal(str(request.data.get(k) or '0')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return Decimal('0.00')

    period = _clean_period(request.data.get('period_label'))
    if period is None:
        return Response({'detail': 'Month must look like 2026-08.'}, status=400)

    # C3 — a policy belongs to ONE broker. The table's uniqueness is
    # (broker, policy_number, period), so without this check the SAME policy can
    # sit under two brokers and be commissioned twice, with nothing to catch it.
    clash = (BrokerPolicy.objects.filter(policy_number__iexact=pol)
             .exclude(broker=b).select_related('broker').first())
    if clash:
        return Response({
            'detail': f'Policy {pol} is already on the register for '
                      f'{clash.broker.name}. A policy belongs to one broker — '
                      f'move it there first if this is wrong.',
            'held_by': {'id': str(clash.broker_id), 'name': clash.broker.name},
        }, status=409)

    # C3 — Graphite must know the policy before it joins a commission register.
    # An UNREACHABLE Graphite is not the same as a missing policy: blocking on a
    # fact we could not establish would stop all data entry during an outage, so
    # the row is allowed through instead.
    #
    # C3d — `graphite_verified` (and when) is STORED on the row, so the register
    # can later tell a checked row from one added during an outage.
    from realpay import graphite_feed
    gp = graphite_feed.policy_lookup(pol)
    if gp is not None and not gp:
        return Response({
            'detail': f'Graphite has no policy {pol}, so it cannot be added to a '
                      f'commission register. Check the number, or add it in '
                      f'Graphite first.',
        }, status=400)
    graphite_verified = bool(gp)

    obj, made = BrokerPolicy.objects.update_or_create(
        broker=b, policy_number=pol[:60], period_label=period,
        defaults={
            'insured_name': str(request.data.get('insured_name') or '')[:160],
            'premium': money('premium'), 'amount_received': money('amount_received'),
            'motor_premium': money('motor_premium'), 'motor_commission': money('motor_commission'),
            'non_motor_premium': money('non_motor_premium'),
            'non_motor_commission': money('non_motor_commission'),
            'commission_payable': money('commission_payable'), 'vat': money('vat'),
            'source': BrokerPolicy.Source.MANUAL, 'added_by': request.user,
            'graphite_verified': graphite_verified,
            'graphite_verified_at': timezone.now() if graphite_verified else None,
        })
    return Response({'id': str(obj.id), 'created': made,
                     'policy_number': obj.policy_number,
                     'graphite_verified': graphite_verified},
                    status=201 if made else 200)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_delete_policy(request, pk, policy_id):
    """Remove a row a person added. A row that only exists in Graphite cannot be
    deleted here — it is Graphite's record, and hiding it would make the sheet
    disagree with the book."""
    try:
        obj = BrokerPolicy.objects.get(pk=policy_id, broker_id=pk)
    except BrokerPolicy.DoesNotExist:
        return Response({'detail': 'That row is not one Omni added — it comes from '
                                   'Graphite and cannot be deleted here.'}, status=404)
    pol = obj.policy_number
    obj.delete()
    return Response({'deleted': True, 'policy_number': pol})


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_upload(request):
    """Import a multi-tab broker workbook. `preview=1` parses without writing."""
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'Attach the broker workbook.'}, status=400)
    if f.size > MAX_UPLOAD_BYTES:
        return Response({'detail': 'That file is larger than 60 MB.'}, status=400)
    name = (f.name or '').lower()
    if not name.endswith(('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods')):
        return Response({'detail': 'Upload a spreadsheet (.xlsx, .xlsm, .xlsb, .xls or .ods).'},
                        status=400)
    preview = str(request.data.get('preview') or '').lower() in ('1', 'true', 'yes')
    period = _clean_period(request.data.get('period_label'))
    if period is None:
        return Response({'detail': 'Month must look like 2026-08.'}, status=400)

    suffix = os.path.splitext(name)[1]
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in f.chunks():
            tmp.write(chunk)
        tmp.flush()
        tmp.close()
        out = svc.import_workbook(tmp.name, period_label=period,
                                  user=request.user, commit=not preview)
    except Exception as exc:  # noqa: BLE001 — a bad workbook is a 400, not a 500
        log.exception('broker workbook import failed')
        return Response({'detail': f'That workbook could not be read: {exc}'}, status=400)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_absorb(request, pk):
    """Fold another broker into this one — for the near-duplicates automatic
    merging cannot safely close (e.g. 'Minet Botswana' and 'Minet Francistown').
    A person confirms; the machine does not guess."""
    other_id = str(request.data.get('other_id') or '').strip()
    if not other_id:
        return Response({'detail': 'Say which broker to fold in.'}, status=400)
    try:
        keeper = Broker.objects.get(pk=pk)
        other = Broker.objects.get(pk=other_id)
    except (Broker.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'No such broker.'}, status=404)
    try:
        return Response(svc.absorb(keeper, other))
    except svc.PolicyHeldElsewhere as exc:
        # C3b — the same refusal Add Policy gives, naming the holding broker.
        return Response({'detail': str(exc),
                         'held_by': {'id': str(exc.broker.pk), 'name': exc.broker.name}},
                        status=409)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_commission_summary(request):
    """Per-broker commission for one month — READ ONLY, marked PREVIEW.

    ?period=YYYY-MM (defaults to the current Gaborone month). Nothing here
    writes, posts or pays; see commissions/broker_summary.py for the three
    cases in which it deliberately refuses to state a commission figure.
    """
    from commissions import broker_summary

    raw = request.query_params.get('period', '')
    period = _clean_period(raw)
    if period is None:
        return Response({'detail': 'period must be YYYY-MM'}, status=400)
    if not period:
        # Botswana time, not server UTC: on the 1st of a month between 00:00 and
        # 02:00 CAT a UTC clock still reads last month and would open the screen
        # on the wrong period.
        period = timezone.localtime().strftime('%Y-%m')
    return Response(broker_summary.build(period))
