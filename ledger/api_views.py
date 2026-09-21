"""ledger/api_views.py"""
import csv
import io
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import CompanyScopedViewSetMixin
from core.permissions import CanViewFinancials

from .models import (
    Account, FiscalPeriod, JournalEntry, JournalEntryAttachment, JournalEntryLine,
    RecurringJournalEntry,
)
from .serializers import (
    AccountSerializer,
    FiscalPeriodSerializer,
    JournalEntryAttachmentSerializer,
    JournalEntryDetailSerializer,
    JournalEntryListSerializer,
    RecurringJournalEntryDetailSerializer,
    RecurringJournalEntryListSerializer,
)
from django.utils import timezone

ZERO = Decimal('0.00')


def _compute_balances(company_id=None, as_of=None):
    """All account balances from posted JE lines.

    CFO directive 2026-05-25 (COA-003): the CoA flat-list view was
    showing all-company cumulative balances regardless of the topbar
    company selector. Now accepts:
      * company_id — restrict to JEs tagged with this company
      * as_of      — only JEs on or before this date

    None on either argument = no restriction (back-compat for callers
    that want the cross-company snapshot, e.g. audit pack).

    PERF (2026-07-17): this runs two GROUP-BY aggregations over the whole
    posted-lines table on EVERY /accounts/ request, and the frontend pages
    the CoA up to 50× per screen load — so the same aggregation fired dozens
    of times a second. Cache the result per (company, as_of) for 60s; the
    balance list tolerates being a minute stale.
    """
    from django.core.cache import cache
    _ckey = f'coa_balances:{company_id or "all"}:{as_of or "today"}'
    _cached = cache.get(_ckey)
    if _cached is not None:
        return _cached
    # BE-REPORT swarm 2026-06-08 #2 — completes GL-001 (P&L FY reset). The CoA
    # flat-list page (/api/v1/accounts/?as_of=...) used to sum revenue/expense
    # since inception, double-counting prior years. Split the aggregation into
    # BS (carries forward all prior activity) and P&L (only current-FY activity).
    base = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
    )
    if company_id:
        base = base.filter(journal_entry__company_id=company_id)
    if as_of:
        base = base.filter(journal_entry__entry_date__lte=as_of)

    bs_qs = base.exclude(account__account_type__in=('revenue', 'expense'))
    pl_qs = base.filter(account__account_type__in=('revenue', 'expense'))

    if as_of:
        from reporting.reports import _get_fiscal_year_start, _fy_end_month_for
        fy_start = _get_fiscal_year_start(as_of, _fy_end_month_for(company_id))
        pl_qs = pl_qs.filter(journal_entry__entry_date__gte=fy_start)

    out = {}
    for r in bs_qs.values('account_id').annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp')):
        out[r['account_id']] = (r['dr'] or ZERO, r['cr'] or ZERO)
    for r in pl_qs.values('account_id').annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp')):
        out[r['account_id']] = (r['dr'] or ZERO, r['cr'] or ZERO)
    cache.set(_ckey, out, 60)
    return out


class AccountViewSet(CompanyScopedViewSetMixin,
                     mixins.ListModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.CreateModelMixin,
                     mixins.UpdateModelMixin,
                     viewsets.GenericViewSet):
    # CFO structural audit 2026-05-19: company isolation via mixin.
    # Account uses owner_company FK, so override the lookup field.
    # SECURITY FIX (2026-07-14): this had no gate beyond IsAuthenticated, so
    # any employee scoped to a company could read (and create/update) its
    # full chart of accounts — the same class of data /cfo and the P&L/BS
    # reports already restrict to finance + management. Matches the FinancialReportView
    # pattern (see reporting/views.py).
    permission_classes = [IsAuthenticated, CanViewFinancials]
    company_lookup_field = 'owner_company_id'
    queryset         = Account.objects.select_related('parent', 'currency_code').order_by('code')
    serializer_class = AccountSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['code', 'name', 'sub_type', 'fs_line_item']
    ordering_fields  = ['code', 'name', 'account_type', 'statement_class']

    def get_queryset(self):
        qs = super().get_queryset()
        at = self.request.query_params.get('account_type')
        if at:
            qs = qs.filter(account_type=at)
        st = self.request.query_params.get('sub_type')
        if st:
            qs = qs.filter(sub_type=st)
        active = self.request.query_params.get('is_active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() == 'true')
        bank_only = self.request.query_params.get('bank_only')
        if bank_only and bank_only.lower() == 'true':
            qs = qs.filter(is_bank_account=True)
        # archived: hide by default; pass ?archived=true|only to include
        archived = (self.request.query_params.get('archived') or '').lower()
        if archived == 'only':
            qs = qs.filter(is_archived=True)
        elif archived != 'true':
            qs = qs.filter(is_archived=False)
        sclass = self.request.query_params.get('statement_class')
        if sclass:
            qs = qs.filter(statement_class=sclass.upper())
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        # CFO directive 2026-05-25 (COA-002 + COA-003): balances must
        # honour the topbar company + the as_of date the page passes
        # in. Falls back to all-company / today() when caller omits.
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(self.request)
        as_of_raw  = (self.request.query_params.get('as_of') or '').strip()
        as_of = None
        if as_of_raw:
            from datetime import date
            try:
                as_of = date.fromisoformat(as_of_raw)
            except ValueError:
                as_of = None
        ctx['balances'] = _compute_balances(company_id=company_id, as_of=as_of)
        return ctx

    @action(detail=False, methods=['get'], url_path='fs-line-options')
    def fs_line_options(self, request):
        """Return the canonical fs_line_item labels grouped by section.

        Used by the CoA inline-edit dropdown (CFO directive 2026-05-24)
        so the team picks from the same MA workbook labels the BS / P&L
        builders read — no free-text typos.

        Now company-aware (CFO directive 2026-06-09 Legakwa UX pass):
        pass ?company=<code> and we return the labels the BS engine
        actually reads for that company. ADIC / ADIL → ma_bs_spec.
        Everything else with a template → entity_bs_templates labels.
        Always includes the P&L lines (ma_pl_spec) and 'common' fallback.
        """
        from reporting.ma_bs_spec import SECTIONS as BS_SECTIONS
        from reporting.ma_pl_spec import MA_LINES, SECTIONS as PL_SECTIONS
        from reporting.entity_bs_templates import get_template as _bs_tpl

        # ?company= is the CODE when /accounts/<id> asks for it, but the plain
        # CoA table calls this with no argument and apiFetch then injects the
        # topbar selection as its UUID id. Resolving both ways is what stops a
        # templated entity (ADIPL, ADSA, …) silently getting ADIC's labels —
        # wrong FS mapping, no error. CFO standing rule 2026-07-28.
        from core.mixins import resolve_company
        raw = (request.query_params.get('company') or '').strip()
        co = resolve_company(raw) if raw else None
        co_code = (co.code if co else raw).upper()
        groups = []

        # Choose the BS label source per company
        use_adic_bs = (not co_code) or co_code in ('ADIC', 'ADIL')
        if not use_adic_bs:
            tpl = _bs_tpl(co_code)
            if tpl:
                for sec in tpl['sections']:
                    groups.append({
                        'group': sec['label'],
                        'side':  sec['side'],
                        'options': [ln['label'] for ln in sec['lines']],
                    })
            else:
                # No template → fall back to ADIC labels (better than nothing)
                use_adic_bs = True
        if use_adic_bs:
            for sec in BS_SECTIONS:
                groups.append({
                    'group': sec['label'],
                    'side':  sec['side'],
                    'options': list(sec['lines']),
                })

        # P&L lines apply to every company (ma_pl_spec is code-driven,
        # but the labels themselves are stable across entities).
        for sec in PL_SECTIONS:
            opts = []
            for key in sec['lines']:
                line = MA_LINES.get(key)
                if line and line.get('label'):
                    opts.append(line['label'])
            if opts:
                groups.append({
                    'group': sec['label'],
                    'side':  'pl',
                    'options': opts,
                })
        return Response({'groups': groups})

    @action(detail=True, methods=['get'], url_path='classify-suggest')
    def classify_suggest(self, request, pk=None):
        """Suggest the fs_line_item label for THIS account.

        Mirrors the management command `auto_map_fs_line_item._classify`
        — picks the same label the bulk auto-map would have applied,
        without writing. Used by the FE Account-Classification "Suggest"
        button (CFO directive 2026-06-09 Legakwa UX pass) so users don't
        have to remember the canonical MA-tree labels.

        Returns:
          {
            "suggestion": "Property, Plant & Equipment" | null,
            "rule":       "matched keyword: 'Motor Vehicle'" | null,
            "source":     "entity_bs_templates" | "ma_bs_spec" | null,
          }
        """
        acct = self.get_object()
        from ledger.management.commands.auto_map_fs_line_item import (
            _classify, _entity_keyword_table, _adic_keyword_table,
        )
        code = (acct.owner_company.code or '').upper()
        if code in ('ADIC', 'ADIL'):
            table = _adic_keyword_table(); src = 'ma_bs_spec'
        else:
            table = _entity_keyword_table(code); src = 'entity_bs_templates'
        # Find which keyword matched (for the rationale text)
        name = (acct.name or '').lower()
        match_kw = None
        for label, kws, excs in table:
            if any(e.lower() in name for e in excs):
                continue
            for k in kws:
                if k.lower() in name:
                    match_kw = k
                    break
            if match_kw:
                break
        suggestion = _classify(acct, table)
        return Response({
            'suggestion': suggestion,
            'rule': f"matched keyword: '{match_kw}'" if match_kw and suggestion else None,
            'source': src if suggestion else None,
        })

    @action(detail=True, methods=['get'], url_path='next-unmapped')
    def next_unmapped(self, request, pk=None):
        """Return the NEXT account (same company) whose fs_line_item is blank.

        Lets Legakwa walk through 128 unmapped accounts without going back
        to the list every time. Ordering: by Account.code ASC, looking for
        the first code strictly greater than the current account's.
        Wraps to the lowest unmapped code if none after. Returns null if
        there are no unmapped accounts left in this company.
        """
        from django.db.models import Q
        acct = self.get_object()
        qs = (Account.objects
              .filter(owner_company_id=acct.owner_company_id, is_archived=False)
              .filter(Q(fs_line_item__isnull=True) | Q(fs_line_item__exact='')))
        nxt = (qs.filter(code__gt=acct.code).order_by('code').first()
               or qs.exclude(id=acct.id).order_by('code').first())
        if not nxt:
            return Response({'next_id': None, 'next_code': None, 'remaining': 0})
        remaining = qs.exclude(id=acct.id).count()
        return Response({
            'next_id':   nxt.id,
            'next_code': nxt.code,
            'next_name': nxt.name,
            'remaining': remaining,
        })


class FiscalPeriodViewSet(mixins.ListModelMixin,
                          mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    queryset         = FiscalPeriod.objects.all().order_by('start_date')
    serializer_class = FiscalPeriodSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['period_name']
    ordering_fields  = ['start_date', 'status']

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        # CFO directive 2026-05-18: the periods page lists only FY25
        # onwards (Alpha Direct FY = 1 Jul – 30 Jun). Pre-FY25 periods
        # were imported for opening-balance purposes only and clutter
        # the close-period UI. Pass `start_date_gte=YYYY-MM-DD` to
        # widen the window, or `start_date_gte=` (empty) to disable.
        start_gte = self.request.query_params.get('start_date_gte', '2024-07-01')
        if start_gte:
            qs = qs.filter(start_date__gte=start_gte)
        # Period Management page (CFO directive 2026-05-28) — entity + FY filters.
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(self.request)
        if company_id:
            qs = qs.filter(company_id=company_id)
        fy_id = self.request.query_params.get('fiscal_year')
        if fy_id:
            qs = qs.filter(fiscal_year_id=fy_id)
        # Eager-load FK targets the serializer pulls (company, FY, sig users).
        qs = qs.select_related('company', 'fiscal_year',
                               'locked_by_cfo', 'locked_by_fm')
        return qs

    @action(detail=False, methods=['get'], url_path='audit-log')
    def audit_log(self, request):
        """GET /api/v1/fiscal-periods/audit-log/ — last N lock/unlock actions
        across all fiscal periods. CFO directive 2026-05-28 (Period
        Management page audit trail)."""
        from core.models import AuditLog
        try:
            limit = max(1, min(int(request.query_params.get('limit') or 20), 200))
        except (TypeError, ValueError):
            limit = 20
        rows = (AuditLog.objects
                .filter(table_name='FiscalPeriod')
                .select_related('user')
                .order_by('-created_at')[:limit])
        # Build a thin payload — join the period_name for display.
        period_ids = [r.record_id for r in rows]
        period_names = dict(FiscalPeriod.objects
                            .filter(pk__in=period_ids)
                            .values_list('pk', 'period_name'))
        period_companies = dict(FiscalPeriod.objects
                                .filter(pk__in=period_ids)
                                .values_list('pk', 'company__code'))
        return Response([{
            'id':           str(r.pk),
            'created_at':   r.created_at.isoformat(),
            'user':         (r.user.get_full_name() or r.user.username) if r.user_id else None,
            'period_id':    r.record_id,
            'period_name':  period_names.get(r.record_id) or period_names.get(str(r.record_id)),
            'company_code': period_companies.get(r.record_id) or period_companies.get(str(r.record_id)),
            'action':       r.action,
            'description':  r.description or '',
            'new_values':   r.new_values or {},
        } for r in rows])

    @action(detail=True, methods=['get'], url_path='close-checks')
    def close_checks(self, request, pk=None):
        """
        GET /api/v1/fiscal-periods/{id}/close-checks/

        Returns the pre-close checklist for this period — every item that
        would block close, grouped by check name. An empty dict means the
        period is ready to close.
        """
        from ledger.period_close import dry_run_close_checks
        period = self.get_object()
        issues = dry_run_close_checks(period)
        return Response({
            'period_name': period.period_name,
            'status':      period.status,
            'ready_to_close': len(issues) == 0,
            'issues':      issues,
        })

    @action(detail=True, methods=['post'], url_path='sign-lock')
    def sign_lock(self, request, pk=None):
        """
        POST /api/v1/fiscal-periods/{id}/sign-lock/
        Body: {"role": "cfo" | "fm", "reason": "optional"}

        Records one of the two required dual-protection signatures. The
        period flips to LOCKED only once BOTH cfo and fm signatures are
        present (CFO directive 2026-05-14).
        """
        from core.models import UserProfile, get_user_profile
        period = self.get_object()
        role = (request.data.get('role') or '').lower().strip()
        reason = (request.data.get('reason') or '').strip()

        profile = get_user_profile(request.user)
        is_cfo = bool(profile and profile.title == UserProfile.Title.CFO)
        is_fm  = bool(profile and (
            profile.is_administrator
            or profile.title in (UserProfile.Title.CFO, UserProfile.Title.FINANCE_MANAGER)
        )) or request.user.is_superuser

        if role == 'cfo' and not is_cfo and not request.user.is_superuser:
            return Response({'detail': 'CFO signature requires CFO title.'},
                            status=status.HTTP_403_FORBIDDEN)
        if role == 'fm' and not is_fm:
            return Response({'detail': 'FM signature requires Finance Manager or administrator role.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            period.sign_lock(request.user, role, reason=reason)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Audit trail (CFO directive 2026-05-28 — Period Management).
        from core.models import AuditLog as _AL
        _AL.objects.create(
            table_name='FiscalPeriod',
            record_id=str(period.pk),
            action=_AL.Action.UPDATE,
            new_values={
                'event':      f'sign_lock:{role}',
                'period':     period.period_name,
                'company':    period.company.code if period.company_id else None,
                'status':     period.status,
                'reason':     reason or None,
            },
            user=request.user,
            description=f"Signed {role.upper()} lock on {period.period_name}"
                        + (f" ({period.company.code})" if period.company_id else ""),
        )

        return Response({
            'period_name': period.period_name,
            'status':      period.status,
            'cfo_signed':  bool(period.locked_by_cfo_id),
            'cfo_signed_at': period.locked_by_cfo_at.isoformat() if period.locked_by_cfo_at else None,
            'fm_signed':   bool(period.locked_by_fm_id),
            'fm_signed_at':  period.locked_by_fm_at.isoformat() if period.locked_by_fm_at else None,
            'fully_locked': period.has_full_lock_signoff,
            'lock_reason': period.lock_reason,
        })

    @action(detail=True, methods=['post'], url_path='clear-lock')
    def clear_lock(self, request, pk=None):
        """
        POST /api/v1/fiscal-periods/{id}/clear-lock/
        Body: {"role": "cfo" | "fm"}

        Clears the caller's own signature. Both must be cleared independently
        to fully unlock a locked period.
        """
        period = self.get_object()
        role = (request.data.get('role') or '').lower().strip()
        try:
            period.clear_lock(request.user, role)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Audit trail (CFO directive 2026-05-28 — Period Management).
        from core.models import AuditLog as _AL
        _AL.objects.create(
            table_name='FiscalPeriod',
            record_id=str(period.pk),
            action=_AL.Action.UPDATE,
            new_values={
                'event':   f'clear_lock:{role}',
                'period':  period.period_name,
                'company': period.company.code if period.company_id else None,
                'status':  period.status,
            },
            user=request.user,
            description=f"Cleared {role.upper()} lock on {period.period_name}"
                        + (f" ({period.company.code})" if period.company_id else ""),
        )

        return Response({
            'period_name': period.period_name,
            'status':      period.status,
            'cfo_signed':  bool(period.locked_by_cfo_id),
            'fm_signed':   bool(period.locked_by_fm_id),
            'fully_locked': period.has_full_lock_signoff,
        })

    @action(detail=True, methods=['post'], url_path='close')
    def close_period(self, request, pk=None):
        """
        POST /api/v1/fiscal-periods/{id}/close/

        Body: {
          "reviewer_signoff": "I have reviewed all balances...",
          "override_password": "<OMNI_FINANCIAL_LOCK_OVERRIDE>"
        }

        Two gates:
          1. The user must be the CFO (or a superuser) — checked inside
             ledger.period_close.close_period().
          2. The user must re-confirm with the financial-lock password
             (CFO directive 2026-05-18 — same lock used by the TB and CoA
             upload endpoints). Without the password the close is rejected
             so a stale browser tab can't accidentally close a period.

        Runs the dry-run checks first; if anything blocks, returns 400.
        """
        from ledger.period_close import close_period as _close_period, dry_run_close_checks
        from ledger.cfo_upload import _check_override
        from django.core.exceptions import ValidationError as _DjangoValidation

        ok, err = _check_override(request)
        if not ok:
            return err

        period = self.get_object()
        issues = dry_run_close_checks(period)
        if issues:
            return Response(
                {'error': 'Period has open items — cannot close.',
                 'period_name': period.period_name,
                 'issues': issues},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _close_period(period, request.user)
        except _DjangoValidation as exc:
            return Response({'error': '; '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)
        period.refresh_from_db()
        return Response(FiscalPeriodSerializer(period).data)

    @action(detail=True, methods=['post'], url_path='reopen')
    def reopen_period(self, request, pk=None):
        """
        POST /api/v1/fiscal-periods/{id}/reopen/

        Body: {
          "reason":            "Why the close needs to be rolled back",
          "override_password": "<OMNI_FINANCIAL_LOCK_OVERRIDE>"
        }

        CFO directive 2026-05-18: CFO (or a superuser) can flip a closed
        period back to open without dropping to the Django admin. The
        financial-lock password is mandatory so the action is deliberate
        and audit-trail entries record the supplied reason.
        """
        from ledger.period_close import reopen_period as _reopen_period
        from ledger.cfo_upload import _check_override
        from django.core.exceptions import ValidationError as _DjangoValidation

        ok, err = _check_override(request)
        if not ok:
            return err

        reason = (request.data.get('reason') or '').strip()
        period = self.get_object()
        try:
            _reopen_period(period, request.user, reason=reason)
        except _DjangoValidation as exc:
            return Response({'error': '; '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)
        period.refresh_from_db()
        return Response(FiscalPeriodSerializer(period).data)


class JournalEntryViewSet(CompanyScopedViewSetMixin,
                           mixins.ListModelMixin,
                           mixins.RetrieveModelMixin,
                           mixins.CreateModelMixin,
                           viewsets.GenericViewSet):
    # SECURITY FIX (2026-07-14): this had no gate beyond IsAuthenticated — any
    # employee scoped to a company could read the full general ledger (every
    # posted journal entry). Matches the FinancialReportView pattern.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    queryset = JournalEntry.objects.select_related(
        'created_by', 'currency_code'
    ).prefetch_related('lines__account').order_by('-entry_date', '-created_at')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['entry_number', 'description']
    ordering_fields = ['entry_date', 'entry_number', 'status', 'journal_type']

    def get_serializer_class(self):
        if self.action == 'list':
            return JournalEntryListSerializer
        return JournalEntryDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        jt = self.request.query_params.get('journal_type')
        if jt:
            qs = qs.filter(journal_type=jt)
        # Company filter is applied by CompanyScopedViewSetMixin (handles
        # both UUID and Company.code values plus the ?company__code= alias).
        # Date window. `entry_date__gte` / `entry_date__lte` are accepted as
        # aliases because that is what a DRF caller reaches for first.
        #
        # Manus, 2026-08-09: `?entry_date__gte=2099-01-01` returned all 20,557
        # entries. There is no filter backend on this viewset, so the parameter
        # was accepted and thrown away — the caller believed it had filtered and
        # got the whole ledger back. An ignored filter is a wrong answer
        # delivered with a 200, which is worse than an error. Anything we do not
        # actually apply is now refused loudly below.
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.utils.dateparse import parse_date
        from rest_framework.exceptions import ValidationError

        def _window(*names):
            for n in names:
                raw = (self.request.query_params.get(n) or '').strip()
                if raw:
                    if parse_date(raw) is None:
                        raise ValidationError(
                            {n: f'Not a date: {raw!r}. Use YYYY-MM-DD.'})
                    return raw
            return None

        from_date = _window('from_date', 'entry_date__gte', 'entry_date__after')
        if from_date:
            qs = qs.filter(entry_date__gte=from_date)
        to_date = _window('to_date', 'entry_date__lte', 'entry_date__before')
        if to_date:
            qs = qs.filter(entry_date__lte=to_date)

        applied = {'status', 'journal_type', 'company', 'company__code', 'page',
                   'page_size', 'search', 'ordering', 'format', 'from_date',
                   'to_date', 'entry_date__gte', 'entry_date__lte',
                   'entry_date__after', 'entry_date__before'}
        # Manus, 2026-08-09: `?bogus_param=1` still returned all 20,557 because
        # only names that LOOKED like filters were checked. A typo of one missing
        # underscore — `entry_date_gte` — silently returned the whole ledger.
        # Anything we do not read is refused now, whatever it is called.
        ignored = sorted(k for k in self.request.query_params if k not in applied)
        if ignored:
            raise ValidationError({
                'detail': 'These filters are not supported here and were NOT '
                          'applied — refusing rather than returning unfiltered '
                          'rows that look filtered.',
                'unsupported': ignored,
                'supported_dates': ['from_date', 'to_date',
                                    'entry_date__gte', 'entry_date__lte'],
            })
        return qs

    @action(detail=True, methods=['post'], url_path='post')
    def post_entry(self, request, pk=None):
        """Direct post — only permitted for system / API users and superusers."""
        je = self.get_object()
        try:
            je.post(user=request.user)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='classify-related-party')
    def classify_related_party(self, request, pk=None):
        """
        POST /api/v1/journal-entries/{id}/classify-related-party/
            { "is_related_party": true|false }

        Maker's mandatory IAS 24 classification step. Only valid while the
        entry is still DRAFT (or REJECTED — to allow correction on re-submit).
        """
        je = self.get_object()
        if je.status not in (JournalEntry.Status.DRAFT, JournalEntry.Status.REJECTED):
            return Response(
                {'error': 'Related-party classification is locked once the entry leaves Draft.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        value = request.data.get('is_related_party')
        if value is None or value == '':
            return Response(
                {'error': 'is_related_party must be true or false (no third option).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if isinstance(value, str):
            value = value.lower() in ('true', '1', 'yes', 'y')
        je.is_related_party = bool(value)
        je.save(audit_user=request.user, audit_description=f'Classified RP={je.is_related_party}')
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit_entry(self, request, pk=None):
        """DRAFT -> PENDING_APPROVAL. Maker step."""
        je = self.get_object()
        try:
            je.submit_for_approval(user=request.user)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_entry(self, request, pk=None):
        """
        PENDING_APPROVAL -> POSTED.

        Checker step. Approver must hold an approval-eligible title (CFO,
        Finance Manager, Financial Controller) and cannot be the creator.
        """
        je = self.get_object()
        # BUG-001 (Oprah QA, 2026-06-08): secondary control against a test/QA
        # entry reaching the live ledger. In production, if the description looks
        # like a test entry, warn the approver and require an explicit confirm
        # before posting. Not a replacement for a real test environment — a
        # backstop on the human approver.
        import re as _re
        from django.conf import settings as _settings
        desc = je.description or ''
        looks_test = bool(_re.search(r'\b(test|qa|sandbox|smoke)\b', desc, _re.I))
        confirmed = str(request.data.get('confirm_test') or '').lower() in ('1', 'true', 'yes')
        if (not _settings.DEBUG) and looks_test and not confirmed:
            return Response({
                'requires_confirmation': True,
                'warning': (f'This entry looks like a TEST/QA entry — "{desc[:100]}". '
                            f'Approving posts it to the LIVE ledger ({je.entry_number}). '
                            'Confirm only if this is a genuine production entry.'),
                'entry_number': je.entry_number,
            }, status=status.HTTP_409_CONFLICT)
        try:
            je.approve(user=request.user)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject_entry(self, request, pk=None):
        """PENDING_APPROVAL -> REJECTED, with reason. Same authority as approve."""
        je = self.get_object()
        reason = request.data.get('reason', '')
        try:
            je.reject(user=request.user, reason=reason)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reopen')
    def reopen_entry(self, request, pk=None):
        """REJECTED -> DRAFT. Only the creator may reopen."""
        je = self.get_object()
        try:
            je.reopen_after_rejection(user=request.user)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='return')
    def return_entry(self, request, pk=None):
        """PENDING_APPROVAL -> RETURNED. Finance Manager bounces the entry
        back to the capturer for correction (CFO/Oprah directive 2026-05-28).
        Same SoD + authority as approve()/reject(); reason mandatory.
        """
        je = self.get_object()
        reason = request.data.get('reason', '')
        ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR')
        try:
            je.return_for_correction(user=request.user, reason=reason, audit_ip=ip)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(je, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='pending-my-approval')
    def pending_my_approval(self, request):
        """JEs awaiting approval that the current user is allowed to approve
        (PENDING_APPROVAL, created_by != current user). Drives the FM
        worklist on the Smart Entry approval queue."""
        from core.mixins import resolve_company_id_param
        qs = (JournalEntry.objects
              .filter(status=JournalEntry.Status.PENDING_APPROVAL)
              .exclude(created_by=request.user)
              .select_related('company', 'created_by', 'submitted_by', 'currency_code')
              .order_by('-submitted_at', '-entry_date'))
        company_id = resolve_company_id_param(request)
        if company_id:
            qs = qs.filter(company_id=company_id)
        serializer = JournalEntryListSerializer(qs[:200], many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reverse')
    def reverse_entry(self, request, pk=None):
        je = self.get_object()
        # SoD (Workstream A #1): a posted journal entry must NOT be reversed by
        # a single user through this direct endpoint — that bypassed the
        # maker-checker queue and let one Finance Manager unwind a posted JE
        # alone. All human reversals now go through Voucher Clearing
        # (/api/v1/je-clearings/): a MAKER (Financial Controller / Senior
        # Accountant) submits a clearing request and a DIFFERENT Finance
        # Manager approves it (submitter != approver enforced there). Only the
        # system/automation account may reverse directly. Not exempted for
        # superusers — a superuser Finance Manager must still use the queue.
        from core.models import get_user_profile
        prof = get_user_profile(request.user)
        is_system = prof is not None and prof.title == prof.Title.SYSTEM_API
        if not is_system:
            return Response(
                {'error': 'Direct reversal is disabled (segregation of duties). '
                          'Submit a clearing request via Voucher Clearing — a maker '
                          'submits and a different Finance Manager approves.'},
                status=status.HTTP_403_FORBIDDEN)
        reason = request.data.get('reason', 'API reversal')
        try:
            reversal = je.reverse(user=request.user, reason=reason)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JournalEntryDetailSerializer(reversal, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ----- Attachments ------------------------------------------------

    @action(
        detail=True, methods=['get', 'post'], url_path='attachments',
        parser_classes=[MultiPartParser],
    )
    def attachments(self, request, pk=None):
        """
        GET  /api/v1/journal-entries/{id}/attachments/
        POST /api/v1/journal-entries/{id}/attachments/   multipart: file, description?
        """
        je = self.get_object()
        if request.method == 'GET':
            qs = je.attachments.select_related('uploaded_by').all()
            return Response(
                JournalEntryAttachmentSerializer(qs, many=True, context={'request': request}).data
            )

        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)

        att = JournalEntryAttachment(
            journal_entry  = je,
            file           = upload,
            filename       = upload.name,
            file_size_bytes = upload.size or 0,
            content_type   = getattr(upload, 'content_type', '') or '',
            description    = (request.data.get('description') or '').strip(),
            uploaded_by    = request.user,
        )
        att.save(audit_user=request.user, audit_description=f'Attached {upload.name} to {je.entry_number}')
        return Response(
            JournalEntryAttachmentSerializer(att, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True, methods=['delete'],
        url_path=r'attachments/(?P<attachment_id>[0-9a-f-]{36})',
    )
    def delete_attachment(self, request, pk=None, attachment_id=None):
        je = self.get_object()
        try:
            att = je.attachments.get(pk=attachment_id)
        except JournalEntryAttachment.DoesNotExist:
            return Response({'error': 'Attachment not found.'}, status=status.HTTP_404_NOT_FOUND)
        att.file.delete(save=False)
        att.delete(audit_user=request.user, audit_description=f'Removed attachment {att.filename}')
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecurringJournalEntryViewSet(viewsets.ModelViewSet):
    """
    CRUD for recurring journal-entry templates.

    List / detail / create / update / delete:
        /api/v1/recurring-journal-entries/

    Custom action:
        POST /api/v1/recurring-journal-entries/run-due/?target_date=YYYY-MM-DD
            — generate any drafts whose schedule has reached target_date.
    """
    queryset = RecurringJournalEntry.objects.select_related(
        'company', 'currency_code', 'created_by'
    ).prefetch_related('lines__account').order_by('name')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['name', 'description']
    ordering_fields = ['name', 'frequency', 'last_generated_for']

    def get_serializer_class(self):
        if self.action == 'list':
            return RecurringJournalEntryListSerializer
        return RecurringJournalEntryDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        return qs

    @action(detail=False, methods=['post'], url_path='run-due')
    def run_due(self, request):
        from datetime import date as date_class, datetime
        from .recurring import generate_all_due

        target_raw = request.data.get('target_date') or request.query_params.get('target_date')
        if target_raw:
            try:
                target = datetime.strptime(target_raw, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': f'Invalid target_date: {target_raw}'},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            target = timezone.localdate()

        result = generate_all_due(target, request.user)
        return Response({
            'period_end':           result.period_end,
            'templates_considered': result.templates_considered,
            'entries_generated':    result.entries_generated,
            'entries_skipped':      result.entries_skipped,
            'errors':               result.errors,
        })


class BatchJournalUploadView(APIView):
    """
    POST /api/v1/journal-entries/batch-upload/

    Upload a CSV file to create journal entries in batch.

    CSV columns:
      entry_date,description,account_code,debit,credit

    Rows with the same entry_date+description are grouped into one JE.
    Each JE is validated (debits=credits) and posted automatically.
    """

    parser_classes = [MultiPartParser]

    @transaction.atomic
    def post(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response(
                {'error': 'No file uploaded. Send a CSV file as "file".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        auto_post = request.data.get('auto_post', 'true').lower() == 'true'

        try:
            content = file_obj.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return Response(
                {'error': 'File must be UTF-8 encoded CSV.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reader = csv.DictReader(io.StringIO(content))
        required_cols = {'entry_date', 'description', 'account_code', 'debit', 'credit'}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            return Response(
                {'error': f'CSV must contain columns: {", ".join(sorted(required_cols))}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Group rows by (entry_date, description) → one JE per group
        groups = {}
        errors = []
        for i, row in enumerate(reader, start=2):
            try:
                entry_date = date_type.fromisoformat(row['entry_date'].strip())
            except (ValueError, AttributeError):
                errors.append(f"Row {i}: invalid date '{row.get('entry_date')}'")
                continue

            code = row['account_code'].strip()
            try:
                acct = Account.objects.get(code=code)
            except Account.DoesNotExist:
                errors.append(f"Row {i}: account '{code}' not found")
                continue

            try:
                debit = Decimal(row['debit'].strip() or '0')
                credit = Decimal(row['credit'].strip() or '0')
            except (InvalidOperation, AttributeError):
                errors.append(f"Row {i}: invalid amount")
                continue

            if debit == 0 and credit == 0:
                continue

            key = (entry_date, row['description'].strip())
            groups.setdefault(key, []).append({
                'account': acct,
                'debit': debit,
                'credit': credit,
                'description': row.get('line_description', row['description']).strip(),
            })

        if errors:
            return Response(
                {'error': 'CSV validation errors', 'details': errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not groups:
            return Response(
                {'error': 'No valid journal entry rows found in CSV.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created_entries = []
        je_errors = []

        for (entry_date, description), lines in groups.items():
            total_dr = sum(ln['debit'] for ln in lines)
            total_cr = sum(ln['credit'] for ln in lines)
            if total_dr != total_cr:
                je_errors.append(
                    f"Entry '{description}' on {entry_date}: "
                    f"debits ({total_dr}) != credits ({total_cr})"
                )
                continue

            je = JournalEntry(
                entry_date=entry_date,
                description=description,
                journal_type='general',
                source_type='batch_upload',
                created_by=request.user,
            )
            je.save()

            for ln in lines:
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=ln['account'],
                    description=ln['description'],
                    debit_amount=ln['debit'],
                    credit_amount=ln['credit'],
                    debit_bwp=ln['debit'],
                    credit_bwp=ln['credit'],
                )

            if auto_post:
                try:
                    je.post(user=request.user)
                except Exception as e:
                    msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                    je_errors.append(f"Entry '{description}': post failed — {msg}")

            created_entries.append({
                'entry_number': je.entry_number,
                'entry_date': str(je.entry_date),
                'description': je.description,
                'status': je.status,
                'line_count': len(lines),
            })

        result = {
            'created': len(created_entries),
            'entries': created_entries,
        }
        if je_errors:
            result['warnings'] = je_errors

        return Response(result, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# JE Clearing Requests (maker-checker reversal of duplicate/wrong JEs)
# CFO directive 2026-05-26.
# ---------------------------------------------------------------------------
from django.contrib.auth.models import User
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers as drf_serializers

from .models import JEClearingRequest


def _is_in_group(user, group_name: str) -> bool:
    return bool(user and user.is_authenticated and
                (user.is_superuser or user.groups.filter(name=group_name).exists()))


class JEClearingRequestSerializer(drf_serializers.ModelSerializer):
    je_number     = drf_serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None,
    )
    je_description = drf_serializers.CharField(
        source='journal_entry.description', read_only=True, default=None,
    )
    je_date       = drf_serializers.DateField(
        source='journal_entry.entry_date', read_only=True, default=None,
    )
    je_amount     = drf_serializers.SerializerMethodField()
    submitted_by_name = drf_serializers.CharField(
        source='submitted_by.username', read_only=True,
    )
    decided_by_name   = drf_serializers.CharField(
        source='decided_by.username', read_only=True,
    )
    reversal_entry_number = drf_serializers.CharField(
        source='reversal_entry.entry_number', read_only=True, default=None,
    )
    is_bulk = drf_serializers.SerializerMethodField()

    class Meta:
        model = JEClearingRequest
        fields = [
            'id', 'status',
            'journal_entry', 'je_number', 'je_description', 'je_date', 'je_amount',
            'bulk_scope', 'is_bulk',
            'reason',
            'submitted_by', 'submitted_by_name', 'submitted_at',
            'decided_by', 'decided_by_name', 'decided_at', 'decision_note',
            'reversal_entry', 'reversal_entry_number',
            'deleted_count',
            # NOTE: deleted_je_snapshot intentionally NOT exposed in the list
            # payload — it can run to MBs. Pull it via /<id>/snapshot/.
        ]
        read_only_fields = [
            'id', 'status', 'submitted_by', 'submitted_at',
            'decided_by', 'decided_at', 'reversal_entry', 'deleted_count',
        ]
        extra_kwargs = {
            'journal_entry': {'required': False, 'allow_null': True},
        }

    def get_je_amount(self, obj):
        # Use sum of debits as the "size" of the JE
        if not obj.journal_entry_id:
            return None
        from django.db.models import Sum as _Sum
        agg = obj.journal_entry.lines.aggregate(total=_Sum('debit_amount'))
        return str(agg['total'] or 0)

    def get_is_bulk(self, obj):
        return obj.journal_entry_id is None and bool(obj.bulk_scope)


class JEClearingRequestViewSet(viewsets.ModelViewSet):
    """Maker-checker reversal queue.

      POST   /api/v1/je-clearings/                  — submit (maker)
      GET    /api/v1/je-clearings/                  — list (any auth user)
      POST   /api/v1/je-clearings/<id>/approve/     — approve (checker)
      POST   /api/v1/je-clearings/<id>/reject/      — reject (checker)
    """
    queryset = JEClearingRequest.objects.select_related(
        'journal_entry', 'submitted_by', 'decided_by', 'reversal_entry',
    ).order_by('-submitted_at')
    serializer_class   = JEClearingRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['journal_entry__entry_number',
                          'journal_entry__description', 'reason']
    ordering_fields    = ['status', 'submitted_at']

    MAKER_GROUP    = 'voucher_clearing_maker'
    APPROVER_GROUP = 'voucher_clearing_approver'

    # ── Single-source access predicates ────────────────────────────────────
    # All four clearing surfaces (submit / decide / roles / snapshot) share
    # these. Workstream A (2026-07-02) widened submit + decide to be
    # title-aware but left roles() and snapshot() on the group-only check —
    # so a title-only maker/checker could act on a request yet be told they
    # had no role and get 403 reading its snapshot. Factored out here to keep
    # the four sites in lock-step (2026-08-21 voucher-clearing access fix).
    @classmethod
    def _can_make(cls, user) -> bool:
        """MAKER: legacy ``voucher_clearing_maker`` group OR a maker title
        (``can_originate_controlled_txn`` — Financial Controller / Senior
        Accountant / Accountant per ``UserProfile.SOD_MAKER_TITLES``, plus
        system/API + superuser)."""
        from core.models import get_user_profile
        prof = get_user_profile(user)
        return _is_in_group(user, cls.MAKER_GROUP) or bool(
            prof and prof.can_originate_controlled_txn)

    @classmethod
    def _can_check(cls, user) -> bool:
        """CHECKER: legacy ``voucher_clearing_approver`` group OR a checker
        title (``can_check_controlled_txn`` — CEO / COO / Finance Manager /
        CFO per ``UserProfile.SOD_CHECKER_TITLES``, plus superuser)."""
        from core.models import get_user_profile
        prof = get_user_profile(user)
        return _is_in_group(user, cls.APPROVER_GROUP) or bool(
            prof and prof.can_check_controlled_txn)

    def get_queryset(self):
        qs = super().get_queryset()
        s = self.request.query_params.get('status')
        if s:
            qs = qs.filter(status=s)
        return qs

    def perform_create(self, serializer):
        u = self.request.user
        # SoD (Workstream A #1): a clearing request is the MAKER step. Allowed
        # for the legacy maker group OR a maker title (Financial Controller /
        # Senior Accountant / Accountant). A different Finance Manager approves
        # it below — submitter != approver is enforced in _decide.
        if not self._can_make(u):
            raise drf_serializers.ValidationError(
                {'detail': "You are not a voucher-clearing maker. Requires a maker "
                          "title (Financial Controller / Senior Accountant / Accountant) "
                          "or the 'voucher_clearing_maker' group."}
            )

        je    = serializer.validated_data.get('journal_entry')
        scope = serializer.validated_data.get('bulk_scope')

        # CFO directive 2026-05-26 — bulk-clear path. When journal_entry is
        # NULL the maker submits a wipe scope (company + date range). The
        # checker approval hard-deletes every POSTED JE that matches.
        if je is None and scope is None:
            raise drf_serializers.ValidationError(
                {'detail': 'Provide either journal_entry or bulk_scope.'}
            )
        if je is not None and scope is not None:
            raise drf_serializers.ValidationError(
                {'detail': 'Use journal_entry OR bulk_scope, not both.'}
            )

        if je is not None:
            if je.status != JournalEntry.Status.POSTED:
                raise drf_serializers.ValidationError(
                    {'detail': f'Only POSTED entries can be cleared. '
                              f'{je.entry_number} is {je.status}.'}
                )
            # Block duplicate pending requests for the same JE
            if JEClearingRequest.objects.filter(
                journal_entry=je, status=JEClearingRequest.Status.PENDING,
            ).exists():
                raise drf_serializers.ValidationError(
                    {'detail': f'A pending clearing request for '
                              f'{je.entry_number} already exists.'}
                )
        else:
            # bulk_scope shape: {company, from_date, to_date, source_type?}
            for k in ('company', 'from_date', 'to_date'):
                if not scope.get(k):
                    raise drf_serializers.ValidationError(
                        {'detail': f"bulk_scope missing required key '{k}'."}
                    )

        serializer.save(submitted_by=u)

    def _decide(self, request, pk, approve: bool):
        u = request.user
        # CHECKER step: legacy approver group OR a checker title (Finance
        # Manager / CFO). submitter != approver is enforced below.
        if not self._can_check(u):
            return Response(
                {'detail': "You are not a voucher-clearing approver. Requires a "
                          "Finance Manager or CFO title, or the "
                          "'voucher_clearing_approver' group."},
                status=status.HTTP_403_FORBIDDEN,
            )
        req = self.get_object()
        if req.status != JEClearingRequest.Status.PENDING:
            return Response(
                {'detail': f'Request is already {req.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if req.submitted_by_id == u.id:
            return Response(
                {'detail': 'You cannot approve a request you submitted. '
                          'Maker-checker separation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        note = (request.data or {}).get('decision_note', '')[:1000]
        from django.utils import timezone as _tz

        if approve:
            # Finance directive 2026-07-02 (Oprah Mogomotsi, EXCO-endorsed,
            # forwarded by the CFO) — REVERSES the 2026-05-26 hard-delete
            # escalation. On approval we now post an auditable REVERSING
            # journal entry for each target JE instead of hard-deleting it.
            # The original stays in the ledger marked REVERSED and linked to
            # its reversal, so every clearing leaves a permanent audit trail.
            try:
                with transaction.atomic():
                    snapshot, reversed_count, reversal = self._reverse_targets(
                        req, u,
                    )
                    req.status = JEClearingRequest.Status.APPROVED
                    req.decided_by = u
                    req.decided_at = _tz.now()
                    req.decision_note = note
                    # deleted_je_snapshot / deleted_count column names kept for
                    # back-compat; they now hold the pre-reversal snapshot and
                    # the count of JEs reversed.
                    req.deleted_je_snapshot = snapshot
                    req.deleted_count = reversed_count
                    if reversal is not None:
                        req.reversal_entry = reversal
                    req.save()
            except Exception as exc:                       # noqa: BLE001
                return Response(
                    {'detail': str(exc)[:500]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            req.status = JEClearingRequest.Status.REJECTED
            req.decided_by = u
            req.decided_at = _tz.now()
            req.decision_note = note
            req.save()

        return Response(self.get_serializer(req).data)

    # ── Hard-delete helpers (CFO directive 2026-05-26) ─────────────────────
    @staticmethod
    def _snapshot_je(je):
        """Freeze a JE header + lines as a plain JSON-safe dict."""
        return {
            'id':              str(je.id),
            'entry_number':    je.entry_number,
            'entry_date':      je.entry_date.isoformat() if je.entry_date else None,
            'description':     je.description,
            'company_id':      str(je.company_id) if je.company_id else None,
            'currency_code':   je.currency_code_id,
            'exchange_rate':   str(je.exchange_rate) if je.exchange_rate is not None else None,
            'source_type':     getattr(je, 'source_type', None),
            'status':          je.status,
            'posted_by_id':    str(je.posted_by_id) if getattr(je, 'posted_by_id', None) else None,
            'posted_at':       je.posted_at.isoformat() if getattr(je, 'posted_at', None) else None,
            'lines': [
                {
                    'account_code':   ln.account.code if ln.account_id else None,
                    'account_id':     str(ln.account_id) if ln.account_id else None,
                    'description':    ln.description,
                    'debit_amount':   str(ln.debit_amount),
                    'credit_amount':  str(ln.credit_amount),
                    'debit_bwp':      str(ln.debit_bwp),
                    'credit_bwp':     str(ln.credit_bwp),
                    'contact_id':     str(ln.contact_id) if getattr(ln, 'contact_id', None) else None,
                }
                for ln in je.lines.select_related('account').all()
            ],
        }

    def _reverse_targets(self, req, user):
        """Snapshot then post a REVERSING JE for each in-scope POSTED JE.

        Finance directive 2026-07-02 — replaces the 2026-05-26 hard-delete
        flow. Each target JE is reversed via ``JournalEntry.reverse()``: the
        original is kept and flipped to ``REVERSED``, an offsetting reversal
        JE is posted (Dr↔Cr swapped), and the two are linked. ``reverse()``
        writes its own ``AuditLog`` REVERSE row and takes a ``select_for_update``
        lock, so this must run (and does — see ``_decide``) inside a
        transaction.

        Returns ``(snapshot_json, reversed_count, single_reversal)`` where
        ``single_reversal`` is the reversal JE for a single-mode approval (to
        store on ``req.reversal_entry``), or ``None`` for bulk.
        """
        from ledger.models import JournalEntry

        # 1. Resolve the JE queryset (same scope logic as the old flow).
        if req.journal_entry_id:
            qs = JournalEntry.objects.filter(pk=req.journal_entry_id)
        else:
            scope = req.bulk_scope or {}
            qs = JournalEntry.objects.filter(
                company_id=scope['company'],
                entry_date__gte=scope['from_date'],
                entry_date__lte=scope['to_date'],
                status=JournalEntry.Status.POSTED,
            )
            if scope.get('source_type'):
                qs = qs.filter(source_type=scope['source_type'])

        targets = list(qs.prefetch_related('lines__account'))
        reason = (f'Voucher-clearing #{req.id}: {req.reason}').strip()[:500]

        entries = []
        single_reversal = None
        for je in targets:
            # Only POSTED entries can be reversed; skip anything already moved.
            if je.status != JournalEntry.Status.POSTED:
                continue
            snap = self._snapshot_je(je)
            reversal = je.reverse(user=user, reason=reason)
            snap['reversal_entry_number'] = reversal.entry_number
            snap['reversal_entry_id']     = str(reversal.pk)
            entries.append(snap)
            single_reversal = reversal

        reversed_count = len(entries)
        # Nothing valid to reverse — do NOT silently mark the request approved.
        # Raise so the approver sees why and the request stays PENDING. (The
        # target JE may already have been reversed, or the bulk range is empty.)
        if reversed_count == 0:
            raise ValueError(
                'No POSTED journal entries matched this clearing — nothing to '
                'reverse. The entry may already be reversed, or the date range '
                'holds no posted entries.'
            )
        # Only wire the single reversal_entry FK when exactly one JE was
        # reversed for a single-mode request; bulk keeps per-entry numbers in
        # the snapshot instead.
        if req.journal_entry_id and reversed_count == 1:
            return ({'entries': entries}, reversed_count, single_reversal)
        return ({'entries': entries}, reversed_count, None)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_request(self, request, pk=None):
        return self._decide(request, pk, approve=True)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject_request(self, request, pk=None):
        return self._decide(request, pk, approve=False)

    @action(detail=False, methods=['get'], url_path='roles')
    def roles(self, request):
        """Tell the frontend what the current user can do.

        Title-aware, matching the submit/decide gates: a maker/checker by
        title (not just by legacy group) sees the right buttons.
        """
        u = request.user
        return Response({
            'is_maker':    self._can_make(u),
            'is_approver': self._can_check(u),
        })

    @action(detail=True, methods=['get'], url_path='snapshot')
    def snapshot(self, request, pk=None):
        """Return the frozen JSON dump of the JE(s) deleted on approval.

        Excluded from the list payload because it can run to MBs for bulk
        wipes. Readable by the same maker/checker population that can submit
        and decide (group OR maker/checker title), plus Django staff; anyone
        else 403.
        """
        req = self.get_object()
        u = request.user
        if not (self._can_make(u) or self._can_check(u) or u.is_staff):
            return Response({'detail': 'Not authorised.'},
                            status=status.HTTP_403_FORBIDDEN)
        return Response({
            'status':        req.status,
            'deleted_count': req.deleted_count,
            'snapshot':      req.deleted_je_snapshot,
        })
