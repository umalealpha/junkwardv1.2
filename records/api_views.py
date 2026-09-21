"""
records/api_views.py — the records register API.

Access (CFO directive 6 Aug 2026): Admin and Human Capital, plus the C-suite and
Finance Manager who see everything else. Everyone else gets 403.

Restricted records are handled separately from access to the register itself.
A record marked `restricted` is personal data — an HR file, anything carrying an
Omang. Those rows stay visible to Admin/HR/C-suite only; other permitted users see
the register without them. That split exists because "who may open the register"
and "who may see the HR files inside it" are not the same question, and answering
them with one flag is how personal data ends up on the wrong screen.
"""

from django.db.models import Q
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from core.models import UserProfile

from .models import RecordCategory, RecordItem, RecordMovement
from .serializers import (
    RecordCategorySerializer, RecordItemSerializer, RecordMovementSerializer,
)
from .services import RecordMovementError, move_record
from django.utils import timezone

T = UserProfile.Title

# May open the register at all.
REGISTER_TITLES = {
    T.CEO, T.COO, T.CFO, T.FINANCE_MANAGER, T.FINANCIAL_CONTROLLER,
    T.OPERATIONS_MANAGER, T.HR_MANAGER, T.ACCOUNTANT, T.SENIOR_ACCOUNTANT,
}
# May additionally see records marked restricted (personal data).
RESTRICTED_TITLES = {T.CEO, T.COO, T.CFO, T.HR_MANAGER, T.OPERATIONS_MANAGER}


def _title(user) -> str:
    p = UserProfile.objects.filter(user=user).only('title').first()
    return (getattr(p, 'title', '') or '').strip().lower()


def _may_open(user) -> bool:
    """May this person open the register at all?

    Three ways in, mirroring `_may_see_restricted` below: superuser, a job title
    that carries it by right, or an EXPLICIT grant of
    `records.view_records_register` (per-user or via a group).

    The explicit grant was added 2026-08-25 for a real case: a second Records
    Officer needed access to cover for the first, and her title is `operations`.
    Before this, the only routes were to change her job title — which in Omni also
    changes her approval rights on payments and purchase orders — or to admit the
    whole `operations` title to the register. A register that can only be opened by
    promoting someone is a register that gets opened by promoting someone.
    """
    return bool(user
                and (user.is_superuser
                     or _title(user) in REGISTER_TITLES
                     or user.has_perm('records.view_records_register')))


def _may_see_restricted(user) -> bool:
    """Cleared for records marked restricted (personal data).

    Three ways in, deliberately: superuser, a title that carries it by right, or
    an EXPLICIT grant of `records.view_restricted_records`. The grant exists
    because the Records officer holds the physical personnel files but her title
    is `accountant` (CFO approved her access, 11 Aug 2026) — and adding
    `accountant` to RESTRICTED_TITLES would have cleared every accountant in the
    group for every employee's personal file.

    Note `has_perm` honours a GROUP grant as well as a per-user one. That is
    intended, not an oversight: a "Records officers" group is a legitimate way to
    hold this, and either way it is a deliberate, audited admin action against a
    named subject — which is the property that matters. What it is NOT is
    automatic by job title. Revoked in the admin, never by a code change.
    """
    if not user:
        return False
    return bool(user.is_superuser
                or _title(user) in RESTRICTED_TITLES
                or user.has_perm('records.view_restricted_records'))


class _GatedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    # No DELETE. RecordMovement cascades off RecordItem, so one DELETE would wipe
    # the whole chain of custody — via the bulk-cascade path, which also skips the
    # AuditableMixin audit rows. A register whose history can be erased by any of
    # nine titles is not a register. Records are archived or destroyed by RECORDING
    # a movement, which is exactly what the destroy/archive kinds are for.
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _may_open(request.user):
            self.permission_denied(
                request,
                message='The records register is for Admin and Human Capital.')


class RecordCategoryViewSet(_GatedViewSet):
    queryset = RecordCategory.objects.all()
    serializer_class = RecordCategorySerializer


class RecordsRegisterPagination(PageNumberPagination):
    """The register must show the WHOLE list. It sat on the default 25-per-page
    while the screen has no next-page control, so a Records officer could not see
    records past the first page and read the register as missing entries she had
    logged (Tlotlo Maswabi, 2026-08-13). A physical-records register is a few
    hundred rows at most — return them all; a client may still page with
    ?page_size= if it ever grows."""
    page_size = 500
    page_size_query_param = 'page_size'
    max_page_size = 2000


class RecordItemViewSet(_GatedViewSet):
    serializer_class = RecordItemSerializer
    pagination_class = RecordsRegisterPagination

    def get_queryset(self):
        qs = (RecordItem.objects
              .select_related('category', 'current_holder')
              .all())
        if not _may_see_restricted(self.request.user):
            qs = qs.exclude(confidentiality=RecordItem.Confidentiality.RESTRICTED)

        p = self.request.query_params
        if p.get('status'):
            qs = qs.filter(status=p['status'])
        if p.get('category'):
            qs = qs.filter(category_id=p['category'])
        if p.get('q'):
            term = p['q'].strip()
            qs = qs.filter(Q(reference__icontains=term) |
                           Q(title__icontains=term) |
                           Q(current_custodian__icontains=term))
        if p.get('out') == 'true':
            qs = qs.filter(status=RecordItem.Status.ISSUED)
        return qs

    def list(self, request, *args, **kwargs):
        """The register, plus a count of any rows withheld from this caller.

        Bug 49c9d8d3: the Records officer reported he could not see the log —
        "it only shows the last two". He could see exactly two, and that was
        correct: his title grants the register but not restricted personal data,
        and 53 of the 55 records are personnel files marked restricted. The
        filter was right; the SILENCE was the defect. A register that hides
        fifty-three of fifty-five rows and says nothing reads as broken data,
        which is the worst thing a register can look like — and it cost a bug
        report, an investigation, and his confidence in the register.

        This changes NOTHING about who may see what. It only tells the caller
        that rows exist which they are not cleared for, so the answer is "ask
        for access" rather than "the log is empty".
        """
        response = super().list(request, *args, **kwargs)
        if _may_see_restricted(request.user):
            return response
        # DELIBERATELY the WHOLE-REGISTER count, NOT the count matching the
        # caller's current filters. Scoping it to the search would turn this
        # notice into an oracle: `?q=<a surname>` returning "1 record hidden"
        # CONFIRMS that a restricted personnel file exists for that name, which
        # is precisely the personal data the role is not cleared for. A constant
        # total tells the caller nothing about any individual — it is the same
        # number whatever they type — so the unfiltered count is the private
        # choice here, not the lazy one. Do not "tighten" this to the filtered
        # queryset.
        hidden = (RecordItem.objects
                  .filter(confidentiality=RecordItem.Confidentiality.RESTRICTED)
                  .count())
        if hidden and isinstance(response.data, dict):
            response.data['restricted_hidden'] = hidden
            response.data['restricted_notice'] = (
                f'{hidden} record(s) are marked restricted (personal data) and '
                f'are not shown to your role. Nothing is missing from the '
                f'register — ask Human Capital if your work needs access.')
        return response

    @action(detail=True, methods=['get'])
    def movements(self, request, pk=None):
        """The full chain of custody for one record, newest first."""
        rec = self.get_object()
        data = RecordMovementSerializer(
            rec.movements.select_related('from_employee', 'to_employee',
                                         'recorded_by'), many=True).data
        return Response({'record': rec.reference, 'movements': data})

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """Issue, return, transfer, archive or destroy — one entry point."""
        rec = self.get_object()
        body = request.data
        kind = (body.get('kind') or '').strip()
        if kind not in RecordMovement.Kind.values:
            return Response(
                {'error': f'kind must be one of: '
                          f'{", ".join(RecordMovement.Kind.values)}.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        if not body.get('moved_at'):
            return Response({'error': 'moved_at is required.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            mv = move_record(
                rec, kind=kind, moved_at=body['moved_at'],
                recorded_by=request.user,
                to_employee=_employee(body.get('to_employee')),
                to_custodian=(body.get('to_custodian') or '').strip(),
                to_location=(body.get('to_location') or '').strip(),
                reason=(body.get('reason') or '').strip(),
                due_back_on=body.get('due_back_on') or None,
            )
        except RecordMovementError as e:
            return Response({'error': str(e)},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(RecordMovementSerializer(mv).data,
                        status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def hold(self, request, pk=None):
        """Put a record under legal hold, or release it.

        legal_hold was made read-only on the serializer so it could not be
        switched off with a PATCH and the record then destroyed. That closed the
        hole but left no way to APPLY a hold either — the feature was dead. This
        is the one way in, and it is restricted to the titles that may see
        personal data, not everyone who may open the register.
        """
        if not _may_see_restricted(request.user):
            return Response(
                {'error': 'Legal hold is set by HR or the C-suite.'},
                status=drf_status.HTTP_403_FORBIDDEN)
        rec = self.get_object()
        on = bool(request.data.get('legal_hold'))
        note = (request.data.get('legal_hold_note') or '').strip()
        if on and not note:
            return Response({'error': 'Say why it is being held.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        rec.legal_hold = on
        rec.legal_hold_note = note if on else ''
        rec.save(update_fields=['legal_hold', 'legal_hold_note', 'updated_at'])
        return Response(RecordItemSerializer(rec).data)

    @action(detail=False, methods=['get'])
    def overdue(self, request):
        """Records past their due-back date — the list Admin actually chases."""
        from datetime import date
        qs = (self.get_queryset()
              .filter(status=RecordItem.Status.ISSUED,
                      due_back_on__lt=timezone.localdate()))
        return Response({'count': qs.count(),
                         'results': RecordItemSerializer(qs, many=True).data})

    @action(detail=False, methods=['get'])
    def due_for_destruction(self, request):
        """Retention expired and not under legal hold. A worklist, not a purge."""
        from datetime import date
        qs = (self.get_queryset()
              .filter(retention_until__lte=timezone.localdate(), legal_hold=False)
              .exclude(status=RecordItem.Status.DESTROYED))
        return Response({'count': qs.count(),
                         'results': RecordItemSerializer(qs, many=True).data})


def _employee(emp_id):
    if not emp_id:
        return None
    from payroll.models import Employee
    return Employee.objects.filter(pk=emp_id).first()
