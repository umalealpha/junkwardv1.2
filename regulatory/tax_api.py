"""
regulatory/tax_api.py — the Tax Calendar's API.

Kept out of regulatory/api_views.py, which is the capital-adequacy module and
has nothing to do with this.

Permissions, in one place so they cannot drift:

  read            any authenticated user — the calendar is not sensitive
  mark complete   the task's owner, or an approver (CFO / FM / FC)
  verify + close  approvers, EXCEPT the person who prepared it. Both real
                  preparers are themselves approvers, so "approver only" was not
                  enough — see the separation-of-duties check in tax_task_verify.
                  The CFO (superuser) is exempt; nobody sits above him.
  close a breach  approvers only, and never without a written reason.
"""
from __future__ import annotations

from datetime import date, timedelta

from rest_framework import serializers, status as drf_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.contrib.auth.models import User

from core.models import get_user_profile
from core.permissions import CanViewFinancials

from .models import (TaxCalendarEditor, TaxComplianceSettings, TaxComplianceTask,
                     TaxObligationOwner)
from .tax_calendar import TaxType
from .vat_recon_service import build_vat_reconciliation, month_period
from .tax_workflow import (
    can_edit_dates,
    change_due_date,
    close_breach,
    generate_tasks,
    mark_preparer_complete,
    today_gabs,
    verify_and_close,
)


def _is_approver(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    return bool(profile and profile.can_approve_journal_entries)


def _person(user):
    if not user:
        return None
    return {
        'id':    user.id,
        'name':  user.get_full_name() or user.username,
        'email': user.email,
    }


class TaskSerializer(serializers.ModelSerializer):
    label          = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tax_type_label = serializers.SerializerMethodField()
    owner_detail   = serializers.SerializerMethodField()
    days_to_due    = serializers.SerializerMethodField()
    is_open        = serializers.SerializerMethodField()

    class Meta:
        model  = TaxComplianceTask
        fields = [
            'id', 'obligation_key', 'tax_type', 'tax_type_label', 'label',
            'period_label', 'period_start', 'period_end',
            'due_date', 'target_date', 'status', 'status_display',
            'owner_detail', 'completed_at', 'completion_note',
            'verified_at', 'verified_note', 'late_reason', 'breach_note',
            'reminder_count', 'days_to_due', 'is_open',
            'original_due_date', 'date_change_reason', 'date_changed_at',
            'date_changed_by_name',
        ]

    date_changed_by_name = serializers.SerializerMethodField()

    def get_date_changed_by_name(self, obj):
        if not obj.date_changed_by:
            return None
        return obj.date_changed_by.get_full_name() or obj.date_changed_by.username

    def get_tax_type_label(self, obj):
        return TaxType.LABELS.get(obj.tax_type, obj.tax_type)

    def get_owner_detail(self, obj):
        return _person(obj.owner)

    def get_days_to_due(self, obj):
        return obj.days_to_due(today_gabs())

    def get_is_open(self, obj):
        return obj.status in TaxComplianceTask.OPEN_STATUSES


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tax_calendar(request):
    """The board. Defaults to a 12-month forward window plus anything still open
    behind us — an overdue filing must never scroll off the top."""
    today  = today_gabs()
    months = int(request.query_params.get('months', 12))

    horizon = today + timedelta(days=int(months * 30.5))
    qs = (TaxComplianceTask.objects
          .select_related('owner', 'completed_by', 'verified_by')
          .filter(due_date__lte=horizon))
    # Past-dated rows are dropped ONLY when they are closed. An open one from
    # three months ago is exactly the row this page exists to surface.
    qs = qs.exclude(
        due_date__lt=today - timedelta(days=60),
        status__in=[TaxComplianceTask.Status.VERIFIED, TaxComplianceTask.Status.LATE],
    )

    tasks = list(qs.order_by('due_date', 'tax_type'))
    open_tasks = [t for t in tasks if t.status in TaxComplianceTask.OPEN_STATUSES]

    settings_row = TaxComplianceSettings.load()
    return Response({
        'today':       today,
        'can_verify':  _is_approver(request.user),
        'can_edit_dates': can_edit_dates(request.user),
        'vat_cycle':   settings_row.get_vat_cycle_display(),
        'summary': {
            'total':      len(tasks),
            'open':       len(open_tasks),
            'breach':     sum(1 for t in tasks if t.status == TaxComplianceTask.Status.BREACH),
            'reminding':  sum(1 for t in tasks if t.status == TaxComplianceTask.Status.REMINDING),
            'awaiting_cfo': sum(1 for t in tasks
                                if t.status == TaxComplianceTask.Status.PREPARER_COMPLETE),
            'unassigned': sum(1 for t in open_tasks if t.owner is None),
        },
        'tasks': TaskSerializer(tasks, many=True).data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def tax_task_complete(request, pk):
    """The preparer marks a filing done. Does NOT close it."""
    try:
        task = TaxComplianceTask.objects.select_related('owner').get(pk=pk)
    except TaxComplianceTask.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=drf_status.HTTP_404_NOT_FOUND)

    if not (_is_approver(request.user) or task.owner_id == request.user.id):
        return Response(
            {'detail': 'Only the owner of this filing, or Finance management, can mark it complete.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )
    if task.status == TaxComplianceTask.Status.BREACH:
        return Response(
            {'detail': 'The statutory date has passed. This is a breach and only the CFO can close it.'},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    task = mark_preparer_complete(task, request.user, (request.data.get('note') or '').strip())
    return Response(TaskSerializer(task).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def tax_task_verify(request, pk):
    """CFO verification — the only thing that closes a filing."""
    if not _is_approver(request.user):
        return Response(
            {'detail': 'Restricted to the CFO / Finance Manager / Financial Controller.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )
    try:
        task = TaxComplianceTask.objects.get(pk=pk)
    except TaxComplianceTask.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=drf_status.HTTP_404_NOT_FOUND)

    # SEPARATION OF DUTIES. Both real preparers (the Financial Controller and the
    # Assistant Finance Manager) are themselves approvers, so an approver-only
    # check is NOT enough — each of them could mark their own filing complete and
    # then verify it, and the CFO sign-off would be self-service. Found by
    # checking the live accounts, not by the tests: the test preparer happened
    # not to be an approver.
    #
    # The CFO (superuser) is exempt. He is the person the verification is FOR,
    # and if he prepares a filing himself there is nobody above him to check it.
    if not request.user.is_superuser and request.user.id in {task.completed_by_id, task.owner_id}:
        return Response(
            {'detail': 'You prepared this filing, so you cannot verify it. '
                       'Verification must come from someone else — that separation is the control.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )

    try:
        task = verify_and_close(
            task, request.user,
            note        = (request.data.get('note') or '').strip(),
            late_reason = (request.data.get('late_reason') or '').strip(),
        )
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST)
    return Response(TaskSerializer(task).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def tax_task_close_breach(request, pk):
    """Close a live breach. CFO only, written reason mandatory."""
    if not _is_approver(request.user):
        return Response(
            {'detail': 'Only the CFO can close a compliance breach.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )
    try:
        task = TaxComplianceTask.objects.get(pk=pk)
    except TaxComplianceTask.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=drf_status.HTTP_404_NOT_FOUND)

    try:
        task = close_breach(task, request.user, request.data.get('breach_note') or '')
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST)
    return Response(TaskSerializer(task).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def tax_owners(request):
    """Read or set who prepares each kind of filing.

    A change applies to FUTURE obligations and to every OPEN one, so handing over
    a portfolio does not leave live deadlines pointing at the person who left.
    Closed history keeps the owner it had.
    """
    if request.method == 'POST':
        if not _is_approver(request.user):
            return Response({'detail': 'Restricted to Finance management.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        tax_type = request.data.get('tax_type')
        owner_id = request.data.get('owner_id')
        if tax_type not in TaxType.LABELS:
            return Response({'detail': 'Unknown tax type.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        row, _ = TaxObligationOwner.objects.update_or_create(
            tax_type=tax_type,
            defaults={'owner_id': owner_id or None, 'updated_by': request.user},
        )
        TaxComplianceTask.objects.filter(
            tax_type=tax_type, status__in=TaxComplianceTask.OPEN_STATUSES,
        ).update(owner_id=owner_id or None)

    rows = {r.tax_type: r for r in TaxObligationOwner.objects.select_related('owner')}
    return Response([
        {
            'tax_type':  code,
            'label':     label,
            'owner':     _person(rows[code].owner) if code in rows else None,
        }
        for code, label in TaxType.CHOICES
    ])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def tax_regenerate(request):
    """Rebuild the forward schedule by hand. Idempotent — existing rows are never
    touched — so this is safe to press when a setting changes."""
    if not _is_approver(request.user):
        return Response({'detail': 'Restricted to Finance management.'},
                        status=drf_status.HTTP_403_FORBIDDEN)
    return Response(generate_tasks())


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def tax_task_change_date(request, pk):
    """Move a statutory date.

    CFO instruction 2026-09-11: open to Oprah Mogomotsi, Kago Tshutlhedi and
    Legakwa Ntabeni via the TaxCalendarEditor list (title alone does not grant
    it), because BURS shifts dates and routing every change through one person
    is how a deadline gets missed.

    Two of those three also prepare filings, so the safety is in the record, not
    in the permission: the original date is kept, a reason is mandatory, a breach
    can never be undone by moving a date, and the CFO is emailed every time.

    KNOWN AND ACCEPTED, so nobody rediscovers it and calls it a bug: this guard
    is DETECTIVE, not preventive. All three editors are approvers, so any of them
    can call `tax_owners` to reassign a filing, push the date, and reassign back
    — three requests. The guard below gates on `{owner, completed_by}`, and the
    weakest endpoint that can rewrite those fields is the real limit of it. The
    pre-existing verify guard has exactly the same property. What survives the
    bypass is the record: the date-change email names the mover and says the date
    went LATER, and the original date stays on the row. The CFO chose visible
    over forbidden; closing it properly means gating `tax_owners` too.
    """
    if not can_edit_dates(request.user):
        return Response(
            {'detail': 'You do not have rights to change a statutory date. '
                       'Ask the CFO to add you.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )
    try:
        task = TaxComplianceTask.objects.get(pk=pk)
    except TaxComplianceTask.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=drf_status.HTTP_404_NOT_FOUND)

    raw = (request.data.get('due_date') or '').strip()
    try:
        new_due = date.fromisoformat(raw)
    except ValueError:
        return Response({'detail': 'Give the new date as YYYY-MM-DD.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    try:
        task = change_due_date(task, request.user, new_due,
                              request.data.get('reason') or '')
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST)
    return Response(TaskSerializer(task).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def tax_date_editors(request):
    """Who may change statutory dates. Only the CFO changes this list — the
    right to hand out the right is not the same as the right."""
    if request.method == 'POST':
        if not getattr(request.user, 'is_superuser', False):
            return Response({'detail': 'Only the CFO can change who may edit dates.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        # Validate before writing. An unknown id used to hit the database as a
        # foreign key and 500; a wrong-but-real id would have silently granted
        # the right to somebody nobody chose. Neither is acceptable on a
        # permission endpoint.
        target = User.objects.filter(pk=request.data.get('user_id')).first()
        if target is None:
            return Response({'detail': 'No such user.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if request.data.get('remove'):
            TaxCalendarEditor.objects.filter(user=target).delete()
        else:
            TaxCalendarEditor.objects.update_or_create(
                user=target,
                defaults={'note': (request.data.get('note') or '').strip(),
                          'added_by': request.user},
            )

    return Response([
        {'user': _person(e.user), 'note': e.note}
        for e in TaxCalendarEditor.objects.select_related('user')
    ])


# ---------------------------------------------------------------------------
# VAT reconciliation (build spec B2)
#
# Read-only. It reports output VAT, input VAT, the net payable or refundable,
# and the tie-out to the general ledger. It posts NOTHING and changes no GL
# mapping — see regulatory/vat_recon.py for the hard stop.
#
# Gated on CanViewFinancials, not merely IsAuthenticated: the reconciliation
# lists customer and supplier names against amounts, which the rest of the Tax
# Calendar does not. The gate is on the endpoint itself, so hiding the menu
# item is not what protects it.
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def vat_reconciliation(request):
    """GET /api/v1/tax-compliance/vat-recon/?year=YYYY&month=M
    or  ?from=YYYY-MM-DD&to=YYYY-MM-DD [&company_id=N]

    Defaults to the month before the current Gaborone month — the period a
    preparer is actually working on, since this month's return is not yet due.
    """
    params = request.query_params
    raw_from, raw_to = params.get('from'), params.get('to')

    if raw_from or raw_to:
        if not (raw_from and raw_to):
            return Response({'detail': "Supply both 'from' and 'to', or neither."},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            period_start = date.fromisoformat(raw_from)
            period_end = date.fromisoformat(raw_to)
        except ValueError:
            return Response({'detail': 'Dates must be YYYY-MM-DD.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
    else:
        today = today_gabs()
        # The month just gone, by default.
        year = today.year if today.month > 1 else today.year - 1
        month = today.month - 1 if today.month > 1 else 12
        try:
            year = int(params.get('year', year))
            month = int(params.get('month', month))
        except (TypeError, ValueError):
            return Response({'detail': 'year and month must be whole numbers.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if not 1 <= month <= 12:
            return Response({'detail': 'month must be between 1 and 12.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        period_start, period_end = month_period(year, month)

    if period_start > period_end:
        return Response({'detail': "'from' must be on or before 'to'."},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    company_id = params.get('company_id') or None
    result = build_vat_reconciliation(period_start, period_end, company_id)

    payload = result.as_dict()
    payload['lines'] = [
        {
            'reference': line.reference,
            'party': line.party,
            'doc_date': str(line.doc_date),
            'net_amount': str(line.net_amount),
            'vat_amount': str(line.vat_amount),
            'side': line.side,
            'kind': line.kind,
        }
        for line in result.lines
    ]
    payload['posts_nothing'] = True
    return Response(payload)
