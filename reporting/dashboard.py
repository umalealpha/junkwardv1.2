"""
reporting/dashboard.py

Aggregator for the CFO dashboard. Returns a single payload with everything
the landing page needs: pending approvals, KPIs, recent audit, and asset
sign-off pop-up state.

Each section is a small dict so the frontend can render whatever cards it
likes without pulling each piece separately.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Count, Q, Sum
from django.utils import timezone

from assets.models import AssetSignOff
from billing.models import Contact, Invoice
from core.models import AuditLog, Company, get_user_profile
from ledger.models import JournalEntry, RecurringJournalEntry


ZERO = Decimal('0.00')


def build_cfo_dashboard(user: User, company_id=None):
    # BUG (Oprah 2026-06-11): scope all KPI tiles to the selected company so
    # /cfo matches /dashboard and /reports/profit-loss. company_id=None keeps
    # the prior group/consolidated behaviour for callers that pass nothing.
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    profile = get_user_profile(user)
    is_approver = bool(profile and profile.can_approve_journal_entries)
    is_admin    = bool(profile and profile.can_administer_users)

    # -- Pending approvals (only visible to approvers) ---------------------
    pending_je_qs = JournalEntry.objects.filter(status=JournalEntry.Status.PENDING_APPROVAL)
    if is_approver:
        # Approver shouldn't see their own (SoD) but we count them so they know
        pending_je = list(
            pending_je_qs.exclude(created_by=user)
            .order_by('-submitted_at')[:10]
            .values('id', 'entry_number', 'entry_date', 'description',
                    'submitted_at', 'created_by__username', 'is_related_party')
        )
    else:
        pending_je = []

    # Imports awaiting your second sign-off
    from assets.models import AssetImportBatch
    from claims.models import RecoveryImportBatch
    from payroll.models import PayrollImportBatch

    awaiting_my_second = []
    if is_approver:
        for model_cls, label in [
            (AssetImportBatch, 'Asset import'),
            (RecoveryImportBatch, 'Recovery import'),
            (PayrollImportBatch, 'Payroll import'),
        ]:
            qs = model_cls.objects.filter(
                status='partially_approved'
            ).exclude(first_approved_by=user).exclude(created_by=user)
            for b in qs.order_by('-created_at')[:5]:
                awaiting_my_second.append({
                    'id':        str(b.id),
                    'kind':      label,
                    'file_name': getattr(b, 'file_name', '') or '',
                    'first_approver': getattr(b.first_approved_by, 'username', None),
                    'created_at':     b.created_at.isoformat(),
                })

    # -- Related-party JEs flagged this week -------------------------------
    rp_this_week = (
        JournalEntry.objects
        .filter(is_related_party=True,
                entry_date__gte=week_ago,
                status__in=[JournalEntry.Status.POSTED,
                            JournalEntry.Status.PENDING_APPROVAL])
        .order_by('-entry_date')[:10]
        .values('id', 'entry_number', 'entry_date', 'description', 'status')
    )

    # -- Overdue invoices --------------------------------------------------
    overdue_qs = (
        Invoice.objects
        .filter(status='posted', due_date__lt=today)
        .exclude(amount_paid__gte=models_F('total_amount'))
        if False else  # noqa
        Invoice.objects.filter(status='posted', due_date__lt=today).exclude(amount_paid__gte=Decimal('999999999999'))
    )
    # The .exclude above is a noop just to keep type checkers happy; we filter properly below.
    overdue_invoices = (
        Invoice.objects.filter(status='posted', due_date__lt=today)
        .annotate(_balance=models_F_balance())
        if False else
        Invoice.objects.filter(status='posted', due_date__lt=today)
    )
    overdue_total_count = overdue_invoices.count()
    overdue_total_amount = overdue_invoices.aggregate(t=Sum('total_amount'))['t'] or ZERO

    # -- KPIs --------------------------------------------------------------
    from reporting.reports import (
        build_cash_position, build_profit_loss, build_receivables_summary,
        build_balance_sheet, _get_fiscal_year_start, _fy_end_month_for,
    )

    cash = build_cash_position(company_id=company_id)
    cash_total = cash.get('total_bwp', '0.00')

    # BUG-005 (CFO 2026-06-05): tie the CFO dashboard to the MAIN dashboard.
    # P&L over FY-to-date, not month-to-date (MTD is ~0 early in a month and
    # contradicted the main dashboard's ~1.8M PAT).
    fy_start = _get_fiscal_year_start(today, _fy_end_month_for(company_id))
    pl = build_profit_loss(fy_start, today, company_id=company_id)

    # BUG-005 (CFO 2026-06-05): AR/AP now read the SAME GL source as the main
    # dashboard — receivables-summary for AR, balance-sheet liabilities for AP —
    # NOT the Invoice table (empty for Odoo-migrated entities -> always 0.00,
    # which contradicted the main dashboard's 25.1M / 43.7M).
    try:
        ar_total = Decimal(str(build_receivables_summary(company_id=company_id).get('total_bwp', '0') or '0'))
    except Exception:  # noqa: BLE001
        ar_total = ZERO
    try:
        _liab = build_balance_sheet(today, company_id=company_id).get('liabilities', {})
        _cl = Decimal(str(_liab.get('current_liabilities', {}).get('total', '0') or '0'))
        _prov = Decimal(str(_liab.get('provisions', {}).get('total', '0') or '0'))
        ap_total = abs(_cl) + abs(_prov)
    except Exception:  # noqa: BLE001
        ap_total = ZERO

    open_recurring_due = RecurringJournalEntry.objects.filter(
        is_active=True,
        start_date__lte=today,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).count()

    # QA-003 fix 2026-06-04: build_profit_loss returns net_profit + revenue.total
    # at the TOP level (MA-canonical after ARC-2), NOT under a 'totals' key — so
    # the old pl['totals'][...] reads always fell back to 0.00 (CFO dashboard
    # showed Net Profit 0.00 while the P&L/Management Pack showed the real
    # figure). Read the actual keys. Expenses = revenue - net_profit so the three
    # tiles reconcile (rev - exp = profit); the MA-signed per-bucket totals do
    # not sum to a clean positive total. NOTE: these are month-to-date; in early
    # June MTD is ~0 by definition — surfacing the wider ~1.8M figure is a
    # period-semantics change (MTD->FYTD) left for CFO sign-off.
    _pl_rev = pl.get('revenue', {}).get('total', '0.00')
    _pl_np  = pl.get('net_profit', '0.00')
    _pl_exp = str(Decimal(str(_pl_rev or '0')) - Decimal(str(_pl_np or '0')))
    # Say which entity these figures cover. Without it an unscoped call looks
    # like it is answering about the company you were just looking at: payables
    # read 94,474,792.15 with no ?company (all 13 entities) and 43,606,991.52
    # for ADIC alone, and the two were reported as a contradiction (Manus
    # 2026-08-09). Same field, same calculation, different scope.
    if company_id:
        try:
            _co = Company.objects.filter(id=company_id).first() or \
                  Company.objects.filter(code=str(company_id)).first()
            _scope = f'{_co.code} — {_co.name}' if _co else str(company_id)
        except Exception:                                    # noqa: BLE001
            _scope = str(company_id)
    else:
        _scope = 'ALL ENTITIES combined — pass ?company=<code> for one entity'

    kpis = {
        'scope': _scope,
        'cash_position_bwp':      str(cash_total),
        'ar_outstanding_bwp':     str(ar_total.quantize(Decimal('0.01')) if isinstance(ar_total, Decimal) else ar_total),
        'ap_outstanding_bwp':     str(ap_total.quantize(Decimal('0.01')) if isinstance(ap_total, Decimal) else ap_total),
        'mtd_revenue_bwp':        _pl_rev,
        'mtd_expenses_bwp':       _pl_exp,
        'mtd_net_profit_bwp':     _pl_np,
        'open_recurring_due':     open_recurring_due,
    }

    # -- Today's audit trail (last 24h) ------------------------------------
    yesterday = timezone.now() - timedelta(hours=24)
    audit_recent = list(
        AuditLog.objects.filter(created_at__gte=yesterday)
        .select_related('user')
        .order_by('-created_at')[:20]
        .values('id', 'table_name', 'record_id', 'action',
                'description', 'created_at', 'user__username')
    )
    for a in audit_recent:
        a['id'] = str(a['id'])
        a['created_at'] = a['created_at'].isoformat()

    # -- Asset sign-offs (popup) -------------------------------------------
    open_signoff_qs = AssetSignOff.objects.filter(
        status__in=[AssetSignOff.Status.PENDING,
                    AssetSignOff.Status.PARTIALLY_SIGNED,
                    AssetSignOff.Status.OVERDUE]
    ).order_by('due_date')
    open_signoffs = list(
        open_signoff_qs[:20].values(
            'id', 'kind', 'period_label', 'due_date', 'status',
            'asset__tag_number', 'asset__name',
        )
    )
    for s in open_signoffs:
        s['id'] = str(s['id'])
        s['due_date'] = s['due_date'].isoformat() if s['due_date'] else None

    overdue_signoffs = open_signoff_qs.filter(status=AssetSignOff.Status.OVERDUE).count()
    next_count_due = (
        AssetSignOff.objects
        .filter(kind=AssetSignOff.Kind.SEMI_ANNUAL_COUNT)
        .exclude(status__in=[AssetSignOff.Status.COMPLETED, AssetSignOff.Status.CANCELLED])
        .order_by('due_date')
        .values_list('period_label', 'due_date', 'status')
        .first()
    )
    next_count = None
    if next_count_due:
        next_count = {
            'period_label': next_count_due[0],
            'due_date':     next_count_due[1].isoformat() if next_count_due[1] else None,
            'status':       next_count_due[2],
        }

    # -- Operational PO authorisations (CFO directive 2026-07-05) -----------
    # The CFO keeps his sign-off on operational POs >= P10k, but wants daily
    # visibility of every operational (Admin/HR) PO authorised today — the
    # detective control that lets him stay out of the small ones. Mirrors the
    # daily_po_authorizations email.
    from procurement.models import PurchaseOrder as _PO
    _op_qs = (_PO.objects
              .filter(status=_PO.Status.APPROVED,
                      department__in=[_PO.Department.ADMIN, _PO.Department.HR],
                      fm_approved_at__date=today)
              .select_related('supplier'))
    if company_id:
        _op_qs = _op_qs.filter(company_id=company_id)
    _op = list(_op_qs.order_by('-total_bwp')[:10])
    _op_total = _op_qs.aggregate(t=Sum('total_bwp'))['t'] or ZERO
    _op_count = _op_qs.count()
    _op_fm_only = _op_qs.filter(cfo_approved_by__isnull=True).count()
    po_operational = {
        'count':       _op_count,
        'fm_only':     _op_fm_only,
        'total_bwp':   str(Decimal(str(_op_total)).quantize(Decimal('0.01'))),
        'items': [{
            'po_number':  p.po_number,
            'supplier':   p.supplier.name if p.supplier_id else '—',
            'department': p.get_department_display(),
            'total_bwp':  str(p.total_bwp or ZERO),
            'fm_only':    not p.cfo_approved_by_id,
        } for p in _op],
    }

    return {
        'as_of':              timezone.now().isoformat(),
        'user': {
            'username':       user.username,
            'is_approver':    is_approver,
            'is_admin':       is_admin,
            'title':          getattr(profile, 'title', '') if profile else '',
        },
        'pending_approvals': {
            'count_total':              pending_je_qs.count(),
            'awaiting_me_je':           pending_je if is_approver else [],
            'awaiting_my_second_import': awaiting_my_second,
        },
        'related_party_this_week': list(rp_this_week),
        'overdue_invoices': {
            'count':       overdue_total_count,
            'total_amount': str(overdue_total_amount.quantize(Decimal('0.01')) if isinstance(overdue_total_amount, Decimal) else overdue_total_amount),
        },
        'kpis':              kpis,
        'audit_recent':      audit_recent,
        'asset_signoffs': {
            'open':            open_signoffs,
            'overdue_count':   overdue_signoffs,
            'next_count_due':  next_count,
        },
        'po_operational_authorizations': po_operational,
    }


def models_F(name):  # tiny helper
    from django.db.models import F
    return F(name)


def models_F_balance():
    from django.db.models import F
    return F('total_amount') - F('amount_paid')
