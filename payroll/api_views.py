"""payroll/api_views.py"""
from django.shortcuts import get_object_or_404
from rest_framework import filters, status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response

from core.dual_auth import assert_ready_to_commit, record_approval, record_rejection
from core.mixins import CompanyScopedViewSetMixin
from core.models import Company, get_user_profile, allowed_company_ids, is_payroll_processor

from .amendment_views import user_can_view_payroll
from .importer import commit_payroll_import, parse_payroll_file, validate_payroll_rows
from .models import (
    Employee, PayrollImportBatch, PayrollPeriod, Payslip, PayslipComponent, TaxBracket,
)
from .serializers import (
    EmployeeSerializer, PayrollImportBatchSerializer, PayrollPeriodSerializer,
    PayslipComponentSerializer, PayslipSerializer, TaxBracketSerializer,
)
from django.utils import timezone


def _require_approver(request):
    """Bulk imports + tax-bracket edits restricted to approver titles."""
    profile = get_user_profile(request.user)
    if getattr(request.user, 'is_superuser', False):
        return
    if profile is None or not profile.can_approve_journal_entries:
        raise PermissionDenied(
            'Restricted to CFO / Finance Manager / Financial Controller.'
        )


class CanViewPayroll(BasePermission):
    """Payroll viewsets expose individual pay (gross/PAYE/net) and bank
    details, so they must be restricted to payroll-authorised users —
    superuser / administrator / approver titles / HR department. An ordinary
    employee viewing their OWN payslip uses the self-scoped
    /hris/api/my-payslips/ + /api/v1/payslips/<id>/pdf/ endpoints instead.
    CFO directive 2026-06-16 (payroll-exposure hardening).
    """
    message = 'Payroll data is restricted to HR / Finance / payroll administrators.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and user_can_view_payroll(request.user)
        )


class EmployeeViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Employee.objects.select_related('company').order_by('full_name')
    serializer_class   = EmployeeSerializer
    permission_classes = [IsAuthenticated, CanViewPayroll]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['full_name', 'employee_number', 'department', 'email', 'job_title']
    ordering_fields    = ['full_name', 'department', 'hire_date']

    # Birthday banner is an all-staff feature returning only month/day + name
    # (no year, no pay, no bank) — keep it open to any authenticated user.
    # Everything else (the full employee list incl. bank details) stays
    # payroll-restricted. CFO directive 2026-06-16.
    _OPEN_ACTIONS = {'birthdays_today', 'birthdays_upcoming'}

    def get_permissions(self):
        if self.action in self._OPEN_ACTIONS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), CanViewPayroll()]

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        dept = self.request.query_params.get('department')
        if dept:
            qs = qs.filter(department=dept)
        # Terminated Employee Archive (2026-08-13): archived staff are hidden
        # from the active list by default. `?archived=1` shows ONLY archived
        # records, but only for HR/Finance Managers (hris_role) — for anyone
        # else the param is ignored so archived staff never leak into the
        # ordinary employee picker (pay-run additions, amendments, etc.).
        from core.hris_access import hris_role
        want_archived = (self.request.query_params.get('archived') or '').lower() in {'1', 'true', 'yes'}
        if want_archived and hris_role(self.request.user) in {'hr', 'hris', 'admin', 'superadmin'}:
            qs = qs.filter(is_archived=True)
        else:
            qs = qs.filter(is_archived=False)
        # Company filter is applied by CompanyScopedViewSetMixin (UUID + code).
        return qs

    def _guard_terminate(self, request):
        """Asset Control gate (CFO 2026-09-02, "close both side doors"): flipping
        a staff member straight to 'terminated' here must run the SAME held-asset
        check as archiving — otherwise a laptop leaves with the person unrecorded.
        """
        obj = self.get_object()
        new_status = (request.data or {}).get('status')
        if new_status == Employee.Status.TERMINATED and obj.status != Employee.Status.TERMINATED:
            from assets.control_services import assets_blocking_offboarding
            held = list(assets_blocking_offboarding(obj))
            if held:
                tags = ', '.join(a.tag_number for a in held[:10])
                more = '' if len(held) <= 10 else f' (+{len(held) - 10} more)'
                raise ValidationError(
                    f'{obj.full_name} still holds {len(held)} asset(s): {tags}{more}. '
                    'Return or write off every asset before marking the person terminated.'
                )

    def update(self, request, *args, **kwargs):
        # Archived payroll fields are read-only (feature request 2026-08-13) —
        # unarchive first (the dedicated action, which is reason + audit
        # logged) before any field can be edited again.
        if self.get_object().is_archived:
            raise PermissionDenied('Employee is archived — unarchive before editing.')
        self._guard_terminate(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if self.get_object().is_archived:
            raise PermissionDenied('Employee is archived — unarchive before editing.')
        self._guard_terminate(request)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Archive means "retain, never delete" (feature request 2026-08-13) —
        # the whole point is to keep every field instead of a hard delete.
        if self.get_object().is_archived:
            raise PermissionDenied('Employee is archived — records are retained, never deleted.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='terminate')
    def terminate(self, request, pk=None):
        """Manual HR action — record an employee's exit (D. Ikgopoleng feature
        request, bug dfc0b768; CFO-authorised 2026-09-10). Requires a
        termination date + a reason. Restricted to HR / Finance Managers, and
        gated by the same Asset Control check as archiving. Never deletes: the
        profile and all history are retained. Pass archive=true to also hide
        the record from active lists."""
        from core.hris_access import hris_role
        if hris_role(request.user) not in {'hr', 'hris', 'admin', 'superadmin'}:
            raise PermissionDenied('Terminating staff is restricted to HR / Finance Managers.')
        from django.core.exceptions import ValidationError
        from django.utils.dateparse import parse_date
        from .archive_service import terminate_employee
        emp = get_object_or_404(Employee.objects.all(), pk=pk)
        raw_date = (request.data.get('termination_date') or '').strip()
        term_date = parse_date(raw_date) if raw_date else None
        if raw_date and term_date is None:
            return Response({'detail': 'termination_date must be YYYY-MM-DD.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            terminate_employee(
                emp, actor=request.user, termination_date=term_date,
                reason=(request.data.get('reason') or ''),
                notes=(request.data.get('notes') or '').strip(),
                archive=str(request.data.get('archive', '')).lower() in {'1', 'true', 'yes'},
            )
        except ValidationError as e:
            return Response({'detail': str(e.message if hasattr(e, "message") else e)},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(EmployeeSerializer(emp, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        """Manual HR action — archive a terminated employee. Requires a reason
        (and the employee must already have a termination date). Restricted to
        HR / Finance Managers. See payroll.archive_service."""
        from core.hris_access import hris_role
        if hris_role(request.user) not in {'hr', 'hris', 'admin', 'superadmin'}:
            raise PermissionDenied('Archiving is restricted to HR / Finance Managers.')
        from django.core.exceptions import ValidationError
        from .archive_service import archive_employee
        emp = get_object_or_404(Employee.objects.all(), pk=pk)
        reason = (request.data.get('reason') or '').strip()
        retention_years = request.data.get('retention_years')
        try:
            archive_employee(
                emp, actor=request.user, reason=reason,
                retention_years=int(retention_years) if retention_years else None,
            )
        except ValidationError as e:
            return Response({'detail': str(e.message if hasattr(e, "message") else e)},
                             status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(EmployeeSerializer(emp, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='unarchive')
    def unarchive(self, request, pk=None):
        """Manual HR action — unarchive (rehire, audit, legal hold). Requires
        a reason. Restricted to HR / Finance Managers."""
        from core.hris_access import hris_role
        if hris_role(request.user) not in {'hr', 'hris', 'admin', 'superadmin'}:
            raise PermissionDenied('Unarchiving is restricted to HR / Finance Managers.')
        from django.core.exceptions import ValidationError
        from .archive_service import unarchive_employee
        emp = get_object_or_404(Employee.objects.all(), pk=pk)
        reason = (request.data.get('reason') or '').strip()
        try:
            unarchive_employee(emp, actor=request.user, reason=reason)
        except ValidationError as e:
            return Response({'detail': str(e.message if hasattr(e, "message") else e)},
                             status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(EmployeeSerializer(emp, context={'request': request}).data)

    @action(detail=False, methods=['post'], url_path='bulk-archive')
    def bulk_archive(self, request):
        """Archive several terminated employees in one HR action (Unami Butale
        feature request — one reason, a list of ids). Each employee is put
        through the SAME single-record archive_service (own validation, own
        audit row), so a leaver without a termination date is skipped with a
        clear reason rather than silently dropped. Restricted to HR / Finance
        Managers. Reversible per record via /unarchive/."""
        from core.hris_access import hris_role
        if hris_role(request.user) not in {'hr', 'hris', 'admin', 'superadmin'}:
            raise PermissionDenied('Archiving is restricted to HR / Finance Managers.')
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response({'detail': 'ids must be a non-empty list of employee ids.'},
                             status=drf_status.HTTP_400_BAD_REQUEST)
        if len(ids) > 200:
            return Response({'detail': 'Archive at most 200 employees at a time.'},
                             status=drf_status.HTTP_400_BAD_REQUEST)
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'detail': 'Archive reason is required.'},
                             status=drf_status.HTTP_400_BAD_REQUEST)
        retention_years = request.data.get('retention_years')
        try:
            retention_years = int(retention_years) if retention_years else None
        except (TypeError, ValueError):
            retention_years = None

        from django.core.exceptions import ValidationError
        from .archive_service import archive_employee
        by_id = {str(e.pk): e for e in Employee.objects.filter(pk__in=ids)}
        results = []
        archived = skipped = 0
        # De-dupe while preserving the caller's order.
        for eid in list(dict.fromkeys(str(i) for i in ids)):
            emp = by_id.get(eid)
            if emp is None:
                results.append({'id': eid, 'name': None, 'ok': False,
                                'detail': 'Employee not found.'})
                skipped += 1
                continue
            try:
                archive_employee(emp, actor=request.user, reason=reason,
                                 retention_years=retention_years)
                results.append({'id': eid, 'name': emp.full_name, 'ok': True})
                archived += 1
            except ValidationError as e:
                results.append({'id': eid, 'name': emp.full_name, 'ok': False,
                                'detail': str(e.message if hasattr(e, 'message') else e)})
                skipped += 1
        return Response({'archived': archived, 'skipped': skipped, 'results': results})

    @action(detail=False, methods=['get'], url_path='archive-expiring')
    def archive_expiring(self, request):
        """Archived records whose retention window closes within `days`
        (default 90) — HR review queue. Never auto-purges. Restricted to
        HR / Finance Managers."""
        from core.hris_access import hris_role
        if hris_role(request.user) not in {'hr', 'hris', 'admin', 'superadmin'}:
            raise PermissionDenied('Restricted to HR / Finance Managers.')
        from .archive_service import records_nearing_retention_expiry
        try:
            days = int(request.query_params.get('days') or 90)
        except (TypeError, ValueError):
            days = 90
        rows = records_nearing_retention_expiry(within_days=days)
        return Response({
            'count': len(rows),
            'within_days': days,
            'employees': EmployeeSerializer(rows, many=True, context={'request': request}).data,
        })

    def retrieve(self, request, *args, **kwargs):
        # DPA L-5 (2026-07-19): record who opened an employee record. The bank
        # number is now MASKED here too (last-4) — the full number is disclosed
        # only via the explicit /reveal/ action below (CFO 2026-07-20).
        resp = super().retrieve(request, *args, **kwargs)
        from core.audit_reads import log_read
        log_read(request.user, 'payroll.Employee', kwargs.get('pk'),
                 description=f'Opened employee record (bank masked) {kwargs.get("pk")}',
                 request=request)
        return resp

    @action(detail=True, methods=['get'], url_path='reveal')
    def reveal(self, request, pk=None):
        """One-button reveal of the FULL (decrypted) bank account + national ID.

        The values are encrypted at rest (DPA S-5); this decrypts them on demand
        for an authorised viewer and records the reveal in the immutable audit
        trail. Authorisation is the viewset's CanViewPayroll gate, which admits
        HR, Unami, the C-suite (CEO/COO/CFO via is_hr_doc_admin), the Finance
        Manager and the Financial Controller (CFO directive 2026-07-20
        "encrypt, but reveal with one button"). Company scoping still applies
        through get_object()."""
        emp = self.get_object()
        from core.audit_reads import log_read
        log_read(request.user, 'payroll.Employee', str(emp.pk),
                 description=f'REVEALED bank account / national ID — {emp.full_name}',
                 request=request)
        return Response({
            'id': str(emp.pk),
            'bank_name': emp.bank_name or '',
            'bank_account_no': emp.bank_account_no or '',
            'bank_branch': emp.bank_branch or '',
            'national_id': emp.national_id or '',
        })

    # ─── Birthday endpoints (CFO directive 2026-06-09) ──────────────────────
    # Three behaviours:
    #   1. Banner on every page when ≥1 staff member has a birthday today.
    #   2. Special overlay when the logged-in user IS the birthday person.
    #   3. ARIA pings the CFO 2 days before via a daily local cron.
    #
    # `date_of_birth` lives on `hris.HRISProfile`. Payroll.Employee has the
    # OneToOne. The response NEVER includes year-of-birth (privacy) — only
    # month/day display + days_until_birthday + a thin set of HR fields a
    # birthday banner needs (full_name, department, email, image initials).

    @action(detail=False, methods=['get'], url_path='birthdays-today')
    def birthdays_today(self, request):
        """Active staff whose date_of_birth.month/day == today (any year)."""
        from datetime import date
        today = timezone.localdate()
        rows = self._birthday_rows(month=today.month, day=today.day)
        return Response({'date': today.isoformat(), 'count': len(rows), 'employees': rows})

    @action(detail=False, methods=['get'], url_path='birthdays-upcoming')
    def birthdays_upcoming(self, request):
        """Active staff with a birthday in the next N days (inclusive of today
        unless `include_today=false`). Default window: 7 days. ARIA hits this
        with `?in_days=2` to ping the CFO 2 days before."""
        from datetime import date, timedelta
        try:
            in_days = max(0, min(int(request.query_params.get('in_days') or 7), 60))
        except (TypeError, ValueError):
            in_days = 7
        include_today = (request.query_params.get('include_today') or 'true').lower() != 'false'
        today = timezone.localdate()
        # Build the set of (month, day) we want to match against, in order.
        days_offsets = range(0 if include_today else 1, in_days + 1)
        targets: list[tuple[int, int, int]] = []  # (offset, month, day)
        for off in days_offsets:
            d = today + timedelta(days=off)
            targets.append((off, d.month, d.day))
        if not targets:
            return Response({'window_days': in_days, 'count': 0, 'employees': []})
        out: list[dict] = []
        for off, m, d in targets:
            out.extend(self._birthday_rows(month=m, day=d, days_until=off))
        # Sort by days_until ascending, then by name
        out.sort(key=lambda r: (r['days_until'], r['full_name']))
        return Response({
            'window_days': in_days,
            'today': today.isoformat(),
            'count': len(out),
            'employees': out,
        })

    # --- internal helper -----------------------------------------------------
    def _birthday_rows(self, *, month: int, day: int, days_until: int = 0) -> list[dict]:
        """Return the slim per-employee dict the FE banner + ARIA pinger need.
        NEVER includes the year-of-birth — staff PII rule. Active employees only.
        Filters out the dummy 'system' / placeholder rows by requiring full_name."""
        from hris.models import HRISProfile
        qs = (HRISProfile.objects
              .select_related('employee', 'employee__company')
              .filter(employee__status=Employee.Status.ACTIVE,
                      date_of_birth__month=month,
                      date_of_birth__day=day)
              .exclude(employee__full_name__exact=''))
        # Entity scope (CFO 2026-06-16): a scoped user only sees birthdays in
        # their granted entities (raw query bypasses CompanyScopedViewSetMixin).
        from core.mixins import scoped_company_ids
        _ids = scoped_company_ids(self.request)
        if _ids is not None:
            qs = qs.filter(employee__company_id__in=_ids) if _ids else qs.none()
        rows = []
        for p in qs:
            emp = p.employee
            initials = (p.initials
                        or ''.join(seg[:1] for seg in (emp.full_name or '').split()[:2]).upper())
            rows.append({
                'employee_id': str(emp.pk),
                'full_name':   emp.full_name,
                'department':  emp.department or '',
                'job_title':   emp.job_title or '',
                'email':       emp.email or '',
                'company':     (emp.company.code if emp.company else ''),
                'initials':    initials,
                'dob_md':      f'{month:02d}-{day:02d}',  # month-day only — NO year
                'days_until':  days_until,
            })
        return rows


class TaxBracketViewSet(viewsets.ModelViewSet):
    """Editing tax brackets is approver-only — these drive PAYE."""
    queryset           = TaxBracket.objects.order_by('effective_from', 'lower_bound')
    serializer_class   = TaxBracketSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_update(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_destroy(self, instance):
        _require_approver(self.request)
        instance.delete()


class PayslipComponentViewSet(viewsets.ModelViewSet):
    """Component catalogue — read-open, write approver-only."""
    queryset           = PayslipComponent.objects.order_by('sort_order')
    serializer_class   = PayslipComponentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_update(self, serializer):
        _require_approver(self.request)
        serializer.save()


class CanManagePayrollPeriod(BasePermission):
    """Any authenticated user may LIST/READ payroll periods — the period
    dropdowns on Run Payroll, the amendment roll-forward screen and elsewhere
    need it. But CREATING / editing / deleting a period is restricted to
    approver titles (CFO / Finance Manager / Financial Controller), the same
    bar as running payroll. Previously the viewset was IsAuthenticated only,
    so any signed-in user could POST a new period.
    """
    message = ('Creating or changing a payroll period is restricted to '
               'CFO / Finance Manager / Financial Controller.')

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        if getattr(request.user, 'is_superuser', False):
            return True
        profile = get_user_profile(request.user)
        return bool(profile and profile.can_approve_journal_entries)


class PayrollPeriodViewSet(viewsets.ModelViewSet):
    queryset           = PayrollPeriod.objects.order_by('-start_date')
    serializer_class   = PayrollPeriodSerializer
    permission_classes = [IsAuthenticated, CanManagePayrollPeriod]

    @staticmethod
    def _is_finance_lead(user):
        prof = get_user_profile(user)
        title = (getattr(prof, 'title', '') or '').lower() if prof else ''
        return bool(getattr(user, 'is_superuser', False)
                    or title in ('cfo', 'financial_controller', 'finance_manager'))

    def _block_locked_status_patch(self, request):
        # A LOCKED period is frozen: it can only be re-opened through the audited
        # unlock action, never via a plain PATCH (which skips the finance-lead
        # gate + audit). OPEN->APPROVED etc. for the posting flow still works.
        obj = self.get_object()
        if obj.status == PayrollPeriod.Status.LOCKED and 'status' in (request.data or {}):
            raise PermissionDenied('This period is locked — use the unlock action to re-open it.')

    def update(self, request, *args, **kwargs):
        self._block_locked_status_patch(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._block_locked_status_patch(request)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='lock')
    def lock(self, request, pk=None):
        """Close a payroll month: OPEN -> LOCKED so no further amendment batch can
        be created or applied to it (CFO 2026-08-28). Finance-lead only, audited."""
        period = self.get_object()
        if not self._is_finance_lead(request.user):
            raise PermissionDenied('Only the CFO / Finance Manager / Financial Controller can lock a period.')
        if period.status != PayrollPeriod.Status.OPEN:
            return Response({'detail': f'Period is {period.get_status_display()}, not open — nothing to lock.'}, status=409)
        period.status = PayrollPeriod.Status.LOCKED
        period.save(update_fields=['status', 'updated_at'],
                    audit_user=request.user, audit_description=f'Payroll period {period.period_name} locked')
        return Response({'ok': True, 'period': period.period_name, 'status': period.status})

    @action(detail=True, methods=['post'], url_path='unlock')
    def unlock(self, request, pk=None):
        """Re-open a locked month (LOCKED -> OPEN) to correct it. Finance-lead
        only, audited. A POSTED/PAID period cannot be re-opened here."""
        period = self.get_object()
        if not self._is_finance_lead(request.user):
            raise PermissionDenied('Only the CFO / Finance Manager / Financial Controller can re-open a period.')
        if period.status != PayrollPeriod.Status.LOCKED:
            return Response({'detail': f'Period is {period.get_status_display()}, not locked — cannot re-open here.'}, status=409)
        period.status = PayrollPeriod.Status.OPEN
        period.save(update_fields=['status', 'updated_at'],
                    audit_user=request.user, audit_description=f'Payroll period {period.period_name} re-opened')
        return Response({'ok': True, 'period': period.period_name, 'status': period.status})

    @action(detail=True, methods=['post'], url_path='load-to-fnb')
    def load_to_fnb(self, request, pk=None):
        """Load this ADIC period's approved net pay into FNB (ISO 20022 bulk).

        POST body:
          dry_run       'true' (default) → preview only, no POST to FNB.
                        'false' → actually load (needs PAYROLL_FNB_LOAD_ENABLED).
          employee_ids  optional list → restrict to a subset (small live test).

        omni only LOADS the batch; the CFO approves it manually inside FNB.
        Finance-leadership / CFO only.
        """
        from django.core.exceptions import ValidationError
        from .fnb_disbursement import load_period_to_fnb
        period = self.get_object()
        prof = get_user_profile(request.user)
        title = (getattr(prof, 'title', '') or '').lower() if prof else ''
        if not (request.user.is_superuser
                or title in ('cfo', 'financial_controller', 'finance_manager')):
            return Response(
                {'error': 'Only Finance leadership or the CFO can load salaries to FNB.'},
                status=drf_status.HTTP_403_FORBIDDEN)
        dry = str(request.data.get('dry_run', 'true')).lower() != 'false'
        emp_ids = request.data.get('employee_ids') or None
        try:
            result = load_period_to_fnb(period, request.user,
                                        employee_ids=emp_ids, dry_run=dry)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        if dry:
            return Response({
                'dry_run': True, 'period': result.period_name,
                'ready_count': result.count, 'total': str(result.total),
                'included': result.included, 'excluded': result.excluded,
            })
        return Response({
            'dry_run': False, 'batch_id': str(result.pk),
            'fnb_reference': result.fnb_reference, 'status': result.status,
            'count': result.payment_count, 'total': str(result.total_amount_bwp),
        })


class PayslipViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Payslip.objects.select_related(
        'employee', 'period', 'company'
    ).prefetch_related('lines__component').order_by('-period__start_date', 'employee__full_name')
    serializer_class   = PayslipSerializer
    permission_classes = [IsAuthenticated, CanViewPayroll]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['employee__full_name', 'employee__department']

    def get_queryset(self):
        qs = super().get_queryset()
        period = self.request.query_params.get('period')
        if period:
            qs = qs.filter(period_id=period)
        period_name = self.request.query_params.get('period_name')
        if period_name:
            qs = qs.filter(period__period_name=period_name)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        # Terminated Employee Archive (Oprah Mogomotsi, 2026-08-24): a payslip
        # belonging to an archived leaver must not linger on the live register.
        # Roll-forward already skips archived staff, but a draft created BEFORE
        # the person was archived stays behind — so hide it here too. Mirrors the
        # EmployeeViewSet default (payroll.api_views.EmployeeViewSet.get_queryset):
        # archived hidden by default; `?archived=1` reveals them, but only for
        # HR / Finance Managers so a leaver's slips never leak into an ordinary
        # user's register. Historical archived payslips are retained, reachable
        # through the archived view — never deleted.
        from core.hris_access import hris_role
        want_archived = (self.request.query_params.get('archived') or '').lower() in {'1', 'true', 'yes'}
        if want_archived and hris_role(self.request.user) in {'hr', 'hris', 'admin', 'superadmin'}:
            qs = qs.filter(employee__is_archived=True)
        else:
            qs = qs.filter(employee__is_archived=False)
        return qs

    @action(detail=True, methods=['post'], url_path='send-email')
    def send_email(self, request, pk=None):
        """Email THIS payslip to its employee. Used by the per-row 'Send'
        button on the Payslips page (HR: Unami, Dorothy; Finance). Respects
        the CFO sign-off release gate — an unsigned 2026-07+ payroll is
        refused with a plain reason rather than pushed to staff.
        get_object() applies company scoping, so a scoped user cannot send a
        payslip for an entity they cannot access."""
        from .payslip_email import send_one_payslip
        payslip = self.get_object()
        result = send_one_payslip(payslip, user=request.user, request=request)
        # A business rejection ("not signed off yet", "no email on file") is an
        # outcome, not an HTTP error — return 200 so the UI shows the plain
        # reason as a warning rather than swallowing it as a failed request.
        if result['ok']:
            return Response({'sent': True, 'email': result['email']})
        return Response({'sent': False, 'reason': result['reason'],
                         'detail': result['detail']})

    @action(detail=False, methods=['post'], url_path='send-batch')
    def send_batch(self, request):
        """Email a chosen set of payslips (the ticked rows on the Payslips
        page). Body: {"payslip_ids": [<uuid>, ...]}. Each slip runs through
        the same gate as send-email; the response reports per-slip so HR sees
        exactly who got it and who was skipped (no email / not signed off)."""
        from .payslip_email import send_one_payslip
        ids = request.data.get('payslip_ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'error': 'Select at least one payslip to send.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if len(ids) > 500:
            return Response({'error': 'Too many payslips selected at once (max 500).'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        # filter_queryset(get_queryset()) keeps company scoping — a scoped user
        # can only send payslips for entities they are allowed to see.
        qs = self.filter_queryset(self.get_queryset()).filter(pk__in=ids)
        sent, failed = [], []
        for ps in qs:
            r = send_one_payslip(ps, user=request.user, request=request)
            entry = {'id': str(ps.pk), 'employee': ps.employee.full_name}
            if r['ok']:
                entry['email'] = r['email']
                sent.append(entry)
            else:
                entry['reason'] = r['reason']
                entry['detail'] = r['detail']
                failed.append(entry)
        return Response({
            'sent_count': len(sent), 'failed_count': len(failed),
            'sent': sent, 'failed': failed,
        })

    @action(detail=True, methods=['post'])
    def recompute(self, request, pk=None):
        payslip = self.get_object()
        payslip.recompute_totals()
        # Persist the freshly-derived PAYE onto the TAX line — otherwise the
        # header paye_amount updates but the line the GL posts from stays stale
        # (HRIS audit 2026-06-09). Mirrors the amendment applier.
        _rp = getattr(payslip, '_recomputed_paye', None)
        if _rp is not None:
            payslip.overwrite_paye_line(_rp, user=request.user)
            payslip.recompute_totals(recompute_paye=False)
        payslip.save(audit_user=request.user, audit_description='Manual recompute')
        return Response(PayslipSerializer(payslip).data)


class PayrollImportBatchViewSet(CompanyScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset           = PayrollImportBatch.objects.select_related(
        'period', 'company', 'created_by'
    ).order_by('-created_at')
    serializer_class   = PayrollImportBatchSerializer
    permission_classes = [IsAuthenticated, CanViewPayroll]

    @action(
        detail=False, methods=['post'], url_path='preview',
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def preview(self, request):
        """
        POST /api/v1/payroll-imports/preview/   multipart:
            file        — CSV / XLSX with the CFO's 28-column layout
            period      — UUID of an existing PayrollPeriod
            company     — optional UUID
        """
        # Upload/preview is open to Finance approvers (CFO/FM/FC) AND to a
        # designated entity payroll processor (e.g. Tshephang for Veritas —
        # CFO directive 2026-07-23). A processor is confined to their own
        # company and never approves their own upload (segregation below).
        prof = get_user_profile(request.user)
        is_appr = request.user.is_superuser or bool(prof and prof.can_approve_journal_entries)
        is_proc = is_payroll_processor(request.user)
        if not (is_appr or is_proc):
            raise PermissionDenied(
                'Uploading payroll is restricted to Finance managers or a '
                'designated entity payroll processor.')
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': 'file is required'}, status=drf_status.HTTP_400_BAD_REQUEST)
        period_id = request.data.get('period')
        if not period_id:
            return Response({'error': 'period is required'}, status=drf_status.HTTP_400_BAD_REQUEST)
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        company = None
        if request.data.get('company'):
            company = get_object_or_404(Company, pk=request.data['company'])
        # A processor (not also an approver) must name a company, and only
        # one they actually have access to.
        if is_proc and not is_appr:
            if company is None:
                return Response(
                    {'error': 'Select the company whose payroll you are uploading.'},
                    status=drf_status.HTTP_400_BAD_REQUEST)
            allowed = allowed_company_ids(request.user)
            if allowed != {'*'} and str(company.pk) not in {str(x) for x in allowed}:
                raise PermissionDenied('You can only upload payroll for your own entity.')

        file_name = getattr(upload, 'name', '') or ''
        rows, parse_errors = parse_payroll_file(upload, file_name=file_name)
        validation_errors  = validate_payroll_rows(rows)
        all_errors = parse_errors + validation_errors

        bad_rows = {e['row_index'] for e in validation_errors if e['row_index'] != -1}
        rows_invalid = len(bad_rows)
        rows_valid   = max(0, len(rows) - rows_invalid)

        batch = PayrollImportBatch.objects.create(
            source='odoo',
            file_name=file_name,
            period=period,
            rows_total=len(rows),
            rows_valid=rows_valid,
            rows_invalid=rows_invalid,
            parsed_rows=rows,
            validation_errors=all_errors,
            status=PayrollImportBatch.Status.DRAFT,
            company=company,
            created_by=request.user,
        )
        batch.save(audit_user=request.user, audit_description=f'Previewed {len(rows)} rows')
        return Response(PayrollImportBatchSerializer(batch).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Single-approval (CFO directive 2026-06-20): a payroll import needs
        ONE approval from an HR Manager / Finance Manager / CFO (down from dual).
        The uploader still cannot approve their own batch (basic segregation)."""
        batch = self.get_object()
        prof = get_user_profile(request.user)
        eligible = bool(request.user.is_superuser or (prof and prof.can_approve_payroll))
        if not eligible:
            return Response(
                {'error': 'Payroll approval requires HR Manager, Finance Manager or CFO.'},
                status=drf_status.HTTP_403_FORBIDDEN)
        if batch.status not in (PayrollImportBatch.Status.DRAFT,
                                PayrollImportBatch.Status.PARTIALLY_APPROVED):
            return Response(
                {'error': f'Batch is {batch.status} — only a draft batch can be approved.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        if batch.created_by_id == request.user.id:
            return Response(
                {'error': 'The person who uploaded the payroll cannot also approve it. '
                          'Ask another HR/Finance Manager or the CFO.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        from django.utils import timezone
        batch.first_approved_by = request.user
        batch.first_approved_at = timezone.now()
        batch.status = PayrollImportBatch.Status.APPROVED   # one approval = approved
        batch.save(audit_user=request.user,
                   audit_description='Payroll approved (single approver)')
        return Response(PayrollImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        batch = self.get_object()
        reason = request.data.get('reason', '')
        try:
            record_rejection(
                batch, request.user, reason,
                rejected_status=PayrollImportBatch.Status.REJECTED,
            )
            batch.save(audit_user=request.user, audit_description=f'Rejected: {reason}')
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(PayrollImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def discard(self, request, pk=None):
        """Remove a not-yet-approved payroll import so a wrong upload can be
        replaced. The person who UPLOADED it may discard their own draft (they
        are SoD-blocked from approving it anyway), or an approver may discard
        any. Never touches an APPROVED or COMMITTED batch — those carry an
        authorisation or have already created payslips.

        (Tshephang / Veritas 2026-07-24: uploaded the wrong file, spotted HR
        discrepancies, and had no way to remove it — approve is SoD-blocked and
        reject needs a CFO/FM/FC title, so an entity processor's draft got stuck.)
        """
        batch = self.get_object()
        prof = get_user_profile(request.user)
        is_appr = bool(request.user.is_superuser or (prof and prof.can_approve_payroll))
        is_owner = batch.created_by_id == request.user.id
        if not (is_appr or is_owner):
            return Response(
                {'error': 'You can only discard a draft you uploaded (or ask an approver).'},
                status=drf_status.HTTP_403_FORBIDDEN)
        # DRAFT only — never a batch that already carries an approval (an
        # approver's sign-off must not be undone from here) or a committed one.
        if batch.status != PayrollImportBatch.Status.DRAFT:
            return Response(
                {'error': f'Batch is {batch.get_status_display()} — only a draft that has '
                          f'not been approved or committed can be discarded.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        label = batch.file_name or str(batch.id)
        # Hard-delete the preview (no payslips exist until commit) but leave an
        # immutable AuditLog entry (who/what/when) for compliance.
        batch.delete(audit_user=request.user,
                     audit_description=f'Discarded draft payroll import: {label}')
        return Response({'discarded': True, 'file_name': label})

    @action(detail=True, methods=['post'])
    def commit(self, request, pk=None):
        # Single-approval gate (CFO directive 2026-06-20): committer must be an
        # HR/Finance Manager or CFO, and the batch must carry its one approval.
        prof = get_user_profile(request.user)
        if not (request.user.is_superuser or (prof and prof.can_approve_payroll)
                or is_payroll_processor(request.user)):
            raise PermissionDenied(
                'Payroll commit requires an approver (HR/Finance Manager or CFO) '
                'or the entity payroll processor.')
        batch = self.get_object()
        if batch.period_id and batch.period.status != PayrollPeriod.Status.OPEN:
            return Response({'error': f'{batch.period.period_name} is '
                             f'{batch.period.get_status_display()} (locked) — re-open the period '
                             f'before committing an import into it.'},
                            status=drf_status.HTTP_409_CONFLICT)
        if batch.status != PayrollImportBatch.Status.APPROVED:
            return Response(
                {'error': f'Batch is {batch.status}. It must be approved by an HR/Finance '
                          f'Manager or the CFO (one approval) before it can be committed.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        if batch.rows_invalid > 0:
            return Response({
                'error': f'{batch.rows_invalid} rows have validation errors. Re-preview to fix.',
            }, status=drf_status.HTTP_400_BAD_REQUEST)
        created, skipped, errors = commit_payroll_import(batch, request.user)
        return Response({
            'created':            created,
            'skipped_duplicates': skipped,
            'errors':             errors,
            'batch':              PayrollImportBatchSerializer(batch).data,
        })


# ── Staff-loan opening balances (CFO 2026-08-28) ─────────────────────────────
# The Financial Controller types each person's loan balance as at a month; the
# existing monthly engine (loan_service.apply_loan_repayments) deducts from then.
from rest_framework.decorators import api_view, permission_classes


def _is_finance_lead_user(user):
    prof = get_user_profile(user)
    title = (getattr(prof, 'title', '') or '').lower() if prof else ''
    return bool(getattr(user, 'is_superuser', False)
                or title in ('cfo', 'financial_controller', 'finance_manager'))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def staff_loans_list(request):
    """Active staff-loan balances + their monthly instalment (payroll authority)."""
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted.')
    from .contract_models import EmployeeLoan
    from .loan_service import _monthly_instalment
    rows = []
    for loan in (EmployeeLoan.objects.filter(status=EmployeeLoan.Status.ACTIVE)
                 .select_related('employee', 'start_period').order_by('employee__full_name')):
        rows.append({
            'id': str(loan.pk),
            'employee': loan.employee.full_name,
            'employee_number': loan.employee.employee_number or '',
            'principal': str(loan.principal),
            'outstanding': str(loan.outstanding),
            'monthly_instalment': str(_monthly_instalment(loan)),
            'term_months': loan.term_months,
            'start_period': loan.start_period.period_name if loan.start_period_id else None,
        })
    return Response({'count': len(rows), 'loans': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def staff_loans_opening_balance(request):
    """FC enters opening balances -> creates staff-loan accounts. Finance-lead only.
    Body: {start_period_id, rows:[{employee_id, net_balance, monthly_deduction}]}."""
    if not _is_finance_lead_user(request.user):
        raise PermissionDenied('Only the CFO / Finance Manager / Financial Controller can enter loan balances.')
    from .staff_loan_opening import create_opening_balances
    start_id = (request.data.get('start_period_id') or '').strip()
    start_period = None
    if start_id:
        try:
            start_period = PayrollPeriod.objects.filter(pk=start_id).first()
        except (ValueError, ValidationError):
            start_period = None
    if start_period is None:
        return Response({'detail': 'Pick the month deductions start from (start_period_id).'},
                        status=drf_status.HTTP_400_BAD_REQUEST)
    rows = request.data.get('rows') or []
    if not isinstance(rows, list) or not rows:
        return Response({'detail': 'Enter at least one employee balance.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)
    result = create_opening_balances(rows=rows, start_period=start_period, user=request.user)
    return Response(result)
