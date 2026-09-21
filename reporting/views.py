"""
reporting/views.py

Thin API views — parse request parameters, call reports.py, return JSON.

Endpoints:
  GET /api/reports/trial-balance/      ?as_of=YYYY-MM-DD
  GET /api/reports/profit-loss/        ?from=YYYY-MM-DD&to=YYYY-MM-DD
  GET /api/reports/balance-sheet/      ?as_of=YYYY-MM-DD
  GET /api/reports/ar-aging/           ?as_of=YYYY-MM-DD
  GET /api/reports/ap-aging/           ?as_of=YYYY-MM-DD
  GET /api/reports/cash-position/
  GET /api/reports/general-ledger/     ?account=CODE&from=YYYY-MM-DD&to=YYYY-MM-DD
"""

from datetime import date

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .dashboard import build_cfo_dashboard
from .xlsx_export import (
    SUPPORTED_REPORTS as _XLSX_SUPPORTED,
    build_report_xlsx_response,
)
from .reports import (
    build_ar_aging,
    build_ap_aging,
    build_asset_movement,
    build_asset_register,
    build_balance_sheet,
    build_budget_vs_actual,
    build_cash_position,
    build_expense_analysis,
    build_general_ledger,
    build_management_pack,
    build_profit_loss,
    build_related_party_transactions,
    build_trial_balance,
    build_vat_return,
)

from rest_framework.permissions import BasePermission, IsAuthenticated
from core.permissions import CanViewFinancials


class FinancialReportView(APIView):
    """Base for every financial report endpoint. CFO directive 2026-06-20:
    financial data (P&L, BS, TB, GL, cash, packs, related-party, dashboard) is
    restricted to finance + management via CanViewFinancials. Subclasses that
    need a different gate may override permission_classes."""
    permission_classes = [IsAuthenticated, CanViewFinancials]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(request, param, default=None):
    """
    Parse a YYYY-MM-DD date from query params.
    Returns (date_obj, None) on success or (None, error_response) on failure.
    """
    raw = request.query_params.get(param)
    if not raw:
        if default is not None:
            return default, None
        return None, Response(
            {'error': f"Missing required parameter: '{param}'"},
            status=400,
        )
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, Response(
            {'error': f"Invalid date for '{param}': expected YYYY-MM-DD, got '{raw}'"},
            status=400,
        )


def _parse_company(request):
    """Pull the optional ?company= query param. Returns the company UUID as
    string, or None if absent.

    CFO directive 2026-05-19 (Manus audit follow-up): accepts either a UUID
    or a company code (e.g. ``ADIC``). Codes are resolved to the matching
    Company.id so callers never trigger a UUID-cast 500.
    Also honours ``?company__code=`` and ``?owner_company=`` aliases.
    """
    import uuid
    raw = None
    for q in ('company', 'company_id', 'company__code',
              'owner_company', 'owner_company__code'):
        v = (request.query_params.get(q) or '').strip()
        if v:
            raw = v
            break
    if not raw:
        return None
    # UUID? Pass through.
    try:
        uuid.UUID(raw)
        return raw
    except (ValueError, AttributeError):
        pass
    # Try as Company.code (case-insensitive)
    try:
        from core.models import Company
        comp = (Company.objects.filter(code__iexact=raw).first()
                or Company.objects.filter(code=raw).first())
        if comp:
            return str(comp.id)
    except Exception:    # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class TrialBalanceView(FinancialReportView):
    """
    GET /api/reports/trial-balance/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=<id|code>

    CFO directive 2026-05-20 (Manus TB-audit): the canonical query is a
    period range (`from` + `to`). Legacy callers using `?as_of=YYYY-MM-DD`
    are still accepted — `from` is derived from the fiscal-year start that
    contains the supplied date, using `Company.fy_end_month`.

    Response carries both the new fields (`from_date`, `to_date`) and the
    legacy aliases (`period_start`, `as_of`) so existing frontends keep
    rendering until they migrate.
    """

    def get(self, request):
        company_id = _parse_company(request)

        # Preferred: explicit from + to.
        from_raw = (request.query_params.get('from')
                    or request.query_params.get('from_date') or '').strip()
        to_raw = (request.query_params.get('to')
                  or request.query_params.get('to_date') or '').strip()

        # The period end defaults to BOTSWANA's today (settings.TIME_ZONE), not
        # the server's. date.today() is the UTC date on a UTC box, so a TB
        # pulled between 00:00 and 02:00 Gaborone silently EXCLUDED today's
        # postings — a wrong number with no error to warn anyone. Same midnight
        # window as the JE guards (f9ff6d56); this is the route the app calls,
        # ledger.views.TrialBalanceView only serves the unused /api/ledger/ one.
        today = timezone.localdate()

        from_date = None
        to_date = None
        if from_raw or to_raw:
            from_date, err = _parse_date(request, 'from', default=None) if from_raw else (None, None)
            if err:
                return err
            to_date, err = _parse_date(request, 'to', default=today)
            if err:
                return err
        else:
            # Legacy: ?as_of=YYYY-MM-DD only. from_date derives in builder.
            to_date, err = _parse_date(request, 'as_of', default=today)
            if err:
                return err

        if from_date and to_date and from_date > to_date:
            return Response(
                {'detail': f'from ({from_date}) must be <= to ({to_date}).'},
                status=400,
            )

        return Response(build_trial_balance(
            to_date, from_date=from_date, company_id=company_id,
        ))


class ProfitLossView(FinancialReportView):
    """
    GET /api/reports/profit-loss/?from=YYYY-MM-DD&to=YYYY-MM-DD
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        return Response(build_profit_loss(from_date, to_date, company_id=_parse_company(request)))


class PeerBenchmarkView(FinancialReportView):
    """
    GET /api/reports/peer-benchmark/

    Alpha Direct against the eight Botswana short-term insurers whose signed
    FY2025 statements we hold, on one comparable basis. Static reference data
    (see reporting/peer_benchmark.py) — no query parameters, no database read.
    Behind the same finance-and-management gate as every other report here.
    """

    def get(self, request):
        from reporting.peer_benchmark import build_benchmark
        from reporting.peer_benchmark_detail import build_detail
        payload = build_benchmark()
        # The line-level layer: cost base counted in both places peers report
        # it, Alpha Direct's own expense lines, class-of-business loss ratios
        # and the reinsurance treaty structure.
        payload['detail'] = build_detail()
        return Response(payload)


class MAProfitLossView(FinancialReportView):
    """
    GET /api/reports/ma-profit-loss/?from=YYYY-MM-DD&to=YYYY-MM-DD

    Returns the P&L in the exact section structure of the CFO's
    monthly Management Accounts workbook (see reporting/ma_pl_spec.py).
    The dashboard 'Revenue' tile reads totals.gross_written_premium
    from this endpoint — NOT the lumped 'revenue' total from the
    generic /profit-loss endpoint.
    """

    def get(self, request):
        from reporting.ma_pl import build_ma_pl
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        return Response(build_ma_pl(from_date, to_date, company_id=_parse_company(request)))


class EntityProfitLossView(FinancialReportView):
    """
    GET /api/v1/reports/entity-pl/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=<id|code>

    CFO directive 2026-05-21 (Group P&L Prompt PDF). Per-entity P&L
    using the templates in `reporting.entity_pl_templates`. ADIC is
    EXPLICITLY out of scope and routes to the MA P&L spec instead —
    its current insurance-format report is preserved untouched.

    Returns either:
      * MA P&L shape (build_ma_pl)            — when company is ADIC
      * Entity P&L shape (build_entity_pl)    — for QIH/VCM/RSA/ADSA/
                                                GCX/UNI/ADIPL/AIZ/
                                                ADIL/ADRG

    Frontend can branch on `format` field in the response.
    """

    def get(self, request):
        from reporting.entity_pl_engine import build_entity_pl
        from reporting.entity_pl_templates import get_template

        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)

        company_id = _parse_company(request)
        if not company_id:
            return Response(
                {'error': "?company= is required (UUID or code, e.g. 'QIH')."},
                status=400,
            )

        # Resolve the company so we can dispatch by code.
        try:
            from core.models import Company
            company = Company.objects.filter(id=company_id).first()
        except Exception:    # noqa: BLE001
            company = None
        if company is None:
            return Response({'error': f"Company {company_id} not found."}, status=404)

        # ADIC is FROZEN — defer to the MA P&L spec untouched.
        if company.code.upper() == 'ADIC':
            from reporting.ma_pl import build_ma_pl
            return Response({
                'format':       'ma_pl',
                'entity_code':  'ADIC',
                'entity_name':  company.name,
                **build_ma_pl(from_date, to_date, company_id=company.id),
            })

        template = get_template(company.code)
        if template is None:
            return Response({
                'error': f"No entity P&L template registered for {company.code}. "
                         f"Add one to reporting/entity_pl_templates.py.",
                'company_code': company.code,
            }, status=501)

        data = build_entity_pl(company.code, from_date, to_date)
        return Response({'format': 'entity_pl', **data})


class CashFlowView(FinancialReportView):
    """
    GET /api/reports/cash-flow/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=<id|code>

    CFO directive 2026-05-20. Indirect-method cash flow statement —
    starts from PAT, adds back non-cash items (depreciation, finance
    cost), walks working-capital deltas (changes in receivables /
    payables / UPR / IBNR / etc.), then splits into Operating /
    Investing / Financing sections.

    Working-capital deltas read from `Account.fs_line_item` (the MA
    label seeded by reporting/management/commands/seed_ma_classifications).
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        from .reports import build_cash_flow
        return Response(build_cash_flow(
            from_date, to_date, company_id=_parse_company(request),
        ))


class BalanceSheetView(FinancialReportView):
    """
    GET /api/reports/balance-sheet/?as_of=YYYY-MM-DD&company=<id|code>[&view=ma|legacy]

    Default (`view=ma`): groups by `Account.fs_line_item` per the MA
    workbook layout (reporting/ma_bs_spec.py). This is the CFO's
    canonical view.

    Legacy (`view=legacy`): preserved for callers that need the old
    account-type/sub-type breakdown (Asset / Liability / Equity).

    NB: parameter is named `view`, NOT `format`. `?format=` is reserved
    by DRF for content negotiation (json/api) — passing `format=ma`
    would 404 because DRF has no renderer for "ma".
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        # Accept both 'view' (preferred) and legacy 'format' for
        # backward compat with callers that already shipped — but only
        # treat non-DRF values; 'json'/'api' are renderer hints.
        v = (request.query_params.get('view') or '').strip().lower()
        if not v:
            f = (request.query_params.get('format') or '').strip().lower()
            v = f if f in ('ma', 'legacy') else 'ma'
        company_id = _parse_company(request)
        if v == 'legacy':
            return Response(build_balance_sheet(as_of, company_id=company_id))
        from .reports import build_ma_balance_sheet
        return Response(build_ma_balance_sheet(as_of, company_id=company_id))


class ARAgingView(FinancialReportView):
    """
    GET /api/reports/ar-aging/?as_of=YYYY-MM-DD

    Unpaid customer invoices aged by days past due.
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        return Response(build_ar_aging(as_of, company_id=_parse_company(request)))


class APAgingView(FinancialReportView):
    """
    GET /api/reports/ap-aging/?as_of=YYYY-MM-DD

    Unpaid vendor bills aged by days past due.
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        return Response(build_ap_aging(as_of, company_id=_parse_company(request)))


class PrudentialLimitsView(FinancialReportView):
    """
    GET /api/v1/compliance/prudential-limits/?as_of=YYYY-MM-DD&company=ID

    NBFIRA Insurance Industry Investment Regulations 2019 §5 — six headline
    limits computed live from the Investments + Banking + MA P&L modules.
    """

    def get(self, request):
        from .prudential import compute_prudential
        as_of_param = (request.query_params.get('as_of') or '').strip()
        as_of_date = None
        if as_of_param:
            try:
                as_of_date = date.fromisoformat(as_of_param)
            except (TypeError, ValueError):
                return Response(
                    {'error': 'as_of must be ISO date YYYY-MM-DD'},
                    status=400,
                )
        return Response(compute_prudential(
            company_id=_parse_company(request),
            as_of=as_of_date,
        ))


class CashPositionView(FinancialReportView):
    """
    GET /api/reports/cash-position/?as_of=YYYY-MM-DD&company=ID

    GL balance for every bank account at the close of business on as_of
    (defaults to today). When the dashboard supplies the period's
    `to_date`, the tile reflects the point-in-time closing balance —
    e.g. selecting FY2025 yields the 2025-06-30 cash position.
    """

    def get(self, request):
        as_of_param = (request.query_params.get('as_of') or '').strip()
        as_of_date = None
        if as_of_param:
            try:
                as_of_date = date.fromisoformat(as_of_param)
            except (TypeError, ValueError):
                return Response(
                    {'error': 'as_of must be ISO date YYYY-MM-DD'},
                    status=400,
                )
        return Response(build_cash_position(
            company_id=_parse_company(request),
            as_of=as_of_date,
        ))


class CoaMaTreeView(FinancialReportView):
    """
    GET /api/v1/reports/coa-ma-tree/?as_of=YYYY-MM-DD&company=ID

    Single tree for the CoA UI — top level MA sections (Current Assets,
    Non-Current Assets, Current/Non-current Liabilities, Equity, plus
    P&L sections), middle MA line labels, leaf GL accounts with running
    balances. Everything in one payload so the dashboard tiles can deep-
    link to a section and the CoA page can drill-down without further
    round-trips. CFO directive 2026-05-24: 'all the tables should
    talk the same'.
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        # `from_date` optional — builder defaults to FY start of as_of.
        from_raw = (request.query_params.get('from_date') or '').strip()
        from_date = None
        if from_raw:
            from_date, err = _parse_date(request, 'from_date',
                                          default=timezone.localdate())
            if err:
                return err
        from .reports import build_coa_ma_tree
        return Response(build_coa_ma_tree(
            company_id=_parse_company(request),
            as_of=as_of,
            from_date=from_date,
        ))


class ReceivablesSummaryView(FinancialReportView):
    """
    GET /api/reports/receivables-summary/?as_of=YYYY-MM-DD&company=ID

    Canonical "Total Receivables" figure used by the dashboard tile, BS
    "Receivables" line, AR aging summary, and CFO dashboard. Anything
    that needs a single AR number should read this — guarantees that
    all tables agree (CFO directive 2026-05-24).
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        from .reports import build_receivables_summary
        return Response(build_receivables_summary(
            company_id=_parse_company(request),
            as_of=as_of,
        ))


class GeneralLedgerView(FinancialReportView):
    """
    GET /api/reports/general-ledger/?account=CODE&from=YYYY-MM-DD&to=YYYY-MM-DD

    All posted JE lines for one account with a running balance.
    """

    def get(self, request):
        account_code = request.query_params.get('account')
        if not account_code:
            return Response(
                {'error': "Missing required parameter: 'account'"},
                status=400,
            )
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        result = build_general_ledger(account_code, from_date, to_date, company_id=_parse_company(request))
        if 'error' in result:
            return Response(result, status=404)
        return Response(result)


class GeneralLedgerExtractAllView(FinancialReportView):
    """
    GET /api/v1/reports/general-ledger/extract-all/?from=...&to=...&company=...

    CFO directive 2026-05-21: the GL page only allowed downloading one
    account at a time. Auditors and Finance need the full GL dump for
    a period. This streams ALL posted JE lines (across every account)
    in the window as CSV.

    Memory-friendly: uses .iterator() + StreamingHttpResponse so the
    response starts arriving immediately and ADIC's 100k+ line FY26 9M
    dump doesn't OOM the backend.
    """

    def get(self, request):
        from django.http import StreamingHttpResponse
        from ledger.models import JournalEntryLine
        import csv

        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )

        company_id = _parse_company(request)
        qs = (
            JournalEntryLine.objects
            .filter(
                journal_entry__entry_date__gte=from_date,
                journal_entry__entry_date__lte=to_date,
                journal_entry__status='posted',
            )
            .select_related('account', 'journal_entry',
                            'journal_entry__company', 'contact')
            .order_by('journal_entry__company__code',
                      'account__code',
                      'journal_entry__entry_date',
                      'journal_entry__id')
        )
        if company_id is not None:
            qs = qs.filter(journal_entry__company_id=company_id)

        # Pseudo-buffer for csv.writer — yields each row as a string
        # which StreamingHttpResponse pushes immediately.
        class _Echo:
            def write(self, value):
                return value

        writer = csv.writer(_Echo())

        header = [
            'company_code', 'account_code', 'account_name', 'account_type',
            'entry_date', 'entry_number', 'description', 'journal_type',
            'debit_amount', 'credit_amount', 'debit_bwp', 'credit_bwp',
            'currency_code', 'exchange_rate',
            'contact_code', 'contact_name', 'is_related_party',
            'line_description',
        ]

        def stream():
            yield writer.writerow(header)
            for ln in qs.iterator(chunk_size=500):
                je = ln.journal_entry
                yield writer.writerow([
                    je.company.code if je.company_id else '',
                    ln.account.code if ln.account_id else '',
                    ln.account.name if ln.account_id else '',
                    ln.account.account_type if ln.account_id else '',
                    je.entry_date.isoformat() if je.entry_date else '',
                    je.entry_number,
                    (je.description or '').replace('\n', ' ').replace('\r', ' '),
                    je.journal_type,
                    str(ln.debit_amount),
                    str(ln.credit_amount),
                    str(ln.debit_bwp),
                    str(ln.credit_bwp),
                    je.currency_code_id or '',
                    str(je.exchange_rate),
                    getattr(ln.contact, 'code', '') if ln.contact_id else '',
                    getattr(ln.contact, 'name', '') if ln.contact_id else '',
                    'Y' if getattr(ln, 'is_related_party', False) else '',
                    (ln.description or '').replace('\n', ' ').replace('\r', ' '),
                ])

        co_suffix = ''
        if company_id is not None:
            from core.models import Company
            c = Company.objects.filter(id=company_id).first()
            if c:
                co_suffix = f"_{c.code}"
        filename = f"general_ledger_all{co_suffix}_{from_date}_to_{to_date}.csv"

        resp = StreamingHttpResponse(stream(), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        resp['Cache-Control'] = 'no-cache'
        return resp


class BudgetVsActualView(FinancialReportView):
    """
    GET /api/reports/budget-vs-actual/?from=YYYY-MM-DD&to=YYYY-MM-DD&department=master

    Budget vs actual variance report.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        department = request.query_params.get('department', 'master')
        company_id = _parse_company(request)
        return Response(build_budget_vs_actual(from_date, to_date, department, company_id))


class VATReturnView(FinancialReportView):
    """
    GET /api/reports/vat-return/?from=YYYY-MM-DD&to=YYYY-MM-DD

    VAT return preparation showing output VAT, input VAT, and net position.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        company_id = _parse_company(request)
        return Response(build_vat_return(from_date, to_date, company_id))


class ExpenseAnalysisView(FinancialReportView):
    """
    GET /api/reports/expense-analysis/?from=YYYY-MM-DD&to=YYYY-MM-DD

    Detailed expense breakdown by category with period-over-period comparison.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        company_id = _parse_company(request)
        return Response(build_expense_analysis(from_date, to_date, company_id))


class ExpenseAnalysisDetailView(FinancialReportView):
    """
    GET /api/v1/reports/expense-analysis/detail/?from=&to=&company=

    CFO directive 2026-05-21. Per-MA-line drill: each P&L expense line
    explodes into per-account totals, per-supplier totals and the top 50
    JE lines so the user can click straight through to source documents.
    """

    def get(self, request):
        from reporting.reports import build_expense_analysis_detail
        from_date, err = _parse_date(request, 'from')
        if err: return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err: return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        return Response(build_expense_analysis_detail(
            from_date, to_date, company_id=_parse_company(request),
        ))


class ExpenseAnalysisXlsxView(FinancialReportView):
    """
    GET /api/v1/reports/expense-analysis/export-xlsx/?from=&to=&company=

    Multi-sheet Excel workbook for the period:
      - Summary           : MA-line totals + variance vs prior period
      - By Account        : every GL code, signed amount
      - By Supplier       : every contact, signed amount + line count
      - Line Detail       : every JE line that hit an opex account
    """

    def get(self, request):
        from django.http import HttpResponse
        from reporting.reports import build_expense_analysis_detail
        from io import BytesIO

        from_date, err = _parse_date(request, 'from')
        if err: return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err: return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        company_id = _parse_company(request)

        data = build_expense_analysis_detail(from_date, to_date, company_id=company_id)

        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            return Response(
                {'error': 'openpyxl not installed on the backend image.'},
                status=500,
            )

        wb = openpyxl.Workbook()
        bold = Font(bold=True)
        hdr_fill = PatternFill('solid', fgColor='0D1B2A')
        hdr_font = Font(bold=True, color='FFFFFF')

        # ── Summary ────────────────────────────────────────────────────
        ws = wb.active
        ws.title = 'Summary'
        ws.append(['Expense Analysis', '', f'{data["from_date"]} → {data["to_date"]}'])
        ws.append([])
        headers = ['Category', 'Current', 'Prior period', 'Change', 'Change %']
        ws.append(headers)
        for c in ws[ws.max_row]:
            c.font = hdr_font; c.fill = hdr_fill; c.alignment = Alignment(horizontal='center')
        for cat in data['categories']:
            ws.append([cat['label'], float(cat['amount']),
                       float(cat['prev_amount']), float(cat['change']),
                       float(cat['change_pct'])])
        ws.append([])
        ws.append(['TOTAL', float(data['totals']['current_total']),
                   float(data['totals']['previous_total']),
                   float(data['totals']['change']),
                   float(data['totals']['change_pct'])])
        for c in ws[ws.max_row]:
            c.font = bold
        for col in 'ABCDE':
            ws.column_dimensions[col].width = 22

        # ── By Account ─────────────────────────────────────────────────
        ws2 = wb.create_sheet('By Account')
        ws2.append(['Category', 'GL Code', 'Account name', 'Amount (BWP)'])
        for c in ws2[1]:
            c.font = hdr_font; c.fill = hdr_fill
        for cat in data['categories']:
            for row in cat['by_account']:
                ws2.append([cat['label'], row['code'], row['name'], float(row['amount'])])
        for col, width in zip('ABCD', (28, 12, 50, 18)):
            ws2.column_dimensions[col].width = width

        # ── By Supplier ────────────────────────────────────────────────
        ws3 = wb.create_sheet('By Supplier')
        ws3.append(['Category', 'Supplier', 'Lines', 'Amount (BWP)'])
        for c in ws3[1]:
            c.font = hdr_font; c.fill = hdr_fill
        for cat in data['categories']:
            for row in cat['by_supplier']:
                ws3.append([cat['label'], row['contact_name'],
                            row['line_count'], float(row['amount'])])
        for col, width in zip('ABCD', (28, 40, 8, 18)):
            ws3.column_dimensions[col].width = width

        # ── Line Detail ────────────────────────────────────────────────
        ws4 = wb.create_sheet('Line Detail')
        ws4.append(['Category', 'Date', 'JE #', 'Account', 'Description',
                    'Supplier', 'Debit', 'Credit', 'Net'])
        for c in ws4[1]:
            c.font = hdr_font; c.fill = hdr_fill
        for cat in data['categories']:
            for ln in cat['top_lines']:
                ws4.append([cat['label'], ln['date'], ln['entry_number'],
                            f"{ln['account_code']} {ln['account_name']}",
                            ln['description'], ln['contact_name'],
                            float(ln['debit']), float(ln['credit']),
                            float(ln['net'])])
        for col, width in zip('ABCDEFGHI', (28, 12, 18, 36, 50, 32, 14, 14, 14)):
            ws4.column_dimensions[col].width = width

        # ── Serialise ──────────────────────────────────────────────────
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = (
            f'attachment; filename="expense_analysis_{from_date}_to_{to_date}.xlsx"'
        )
        return resp


class ExpenseAnalysisAIView(FinancialReportView):
    """
    POST /api/v1/reports/expense-analysis/analyse/?from=&to=&company=

    Send the expense analysis to DeepSeek and return a short narrative:
      - Top three drivers of the period-on-period change
      - Two suspicious or out-of-band categories worth investigating
      - One recommendation for the next month

    Returns a degraded fallback if DEEPSEEK_API_KEY is unset.
    """

    def post(self, request):
        from reporting.reports import build_expense_analysis_detail
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
        import json

        from_date, err = _parse_date(request, 'from')
        if err: return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err: return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        company_id = _parse_company(request)

        data = build_expense_analysis_detail(from_date, to_date, company_id=company_id)

        # Compact data for the model — drop top_lines + by_account
        # (token-heavy) and keep only category-level summaries plus the
        # top-five suppliers per category.
        compact = {
            'from':           data['from_date'],
            'to':             data['to_date'],
            'previous_from':  data['previous_from'],
            'previous_to':    data['previous_to'],
            'totals':         data['totals'],
            'categories': [
                {
                    'label':       c['label'],
                    'amount':      c['amount'],
                    'prev_amount': c['prev_amount'],
                    'change':      c['change'],
                    'change_pct':  c['change_pct'],
                    'top_suppliers': c['by_supplier'][:5],
                }
                for c in data['categories']
            ],
        }

        prompt = (
            "You are the CFO's analyst at Alpha Direct Insurance (Botswana, "
            "BWP). Given the period-on-period expense data below, write a "
            "concise variance commentary (≤200 words) with:\n"
            "  1. Top three drivers of the period-on-period change "
            "(amounts + likely cause).\n"
            "  2. Two out-of-band categories worth investigating (why).\n"
            "  3. One recommendation for next month.\n\n"
            "Numbers are in BWP. Round to nearest thousand.\n\n"
            f"DATA:\n{json.dumps(compact, default=str, indent=2)}"
        )

        try:
            text = deepseek_complete(
                prompt,
                system_prompt='You are a precise financial analyst. No filler. Cite numbers.',
                timeout=20.0,
            )
        except DeepSeekUnavailable as e:
            return Response({
                'ai_available':   False,
                'fallback':       True,
                'reason':         str(e),
                'data_summary':   compact,
                'message':        'DeepSeek not configured — set DEEPSEEK_API_KEY in /etc/alpha-finance/.env and retry.',
            })

        return Response({
            'ai_available': True,
            'analysis':     text,
            'data_summary': compact,
        })


class ManagementPackView(FinancialReportView):
    """
    GET /api/reports/management-pack/?from=YYYY-MM-DD&to=YYYY-MM-DD

    Aggregated management accounts package with P&L, balance sheet,
    cash position, insurance KPIs, and budget comparison.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'"},
                status=400,
            )
        return Response(build_management_pack(from_date, to_date, company_id=_parse_company(request)))


class CFODashboardView(FinancialReportView):
    """
    GET /api/v1/dashboard/cfo/

    Aggregated landing-page payload — pending approvals, KPIs, related-party
    activity, audit trail (last 24h), asset sign-off queue.
    """
    def get(self, request):
        # BUG (Oprah 2026-06-11): the CFO dashboard ignored the topbar company
        # and always showed GROUP/consolidated KPIs — so Net Profit (FY-YTD)
        # read the all-entity figure (a loss) while /reports/profit-loss for the
        # selected entity (ADIC) showed a profit. Honour the company selector.
        return Response(build_cfo_dashboard(request.user, company_id=_parse_company(request)))


class RelatedPartyTransactionsView(FinancialReportView):
    """
    GET /api/reports/related-party-transactions/?from=YYYY-MM-DD&to=YYYY-MM-DD

    Returns related-party JE lines in the window plus fiscal-year-to-date.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        return Response(build_related_party_transactions(from_date, to_date))


class AssetRegisterView(FinancialReportView):
    """
    GET /api/reports/asset-register/?as_of=YYYY-MM-DD&category=&status=

    Fixed Assets register: every asset with cost, accumulated depreciation,
    NBV, location, and status as at a given date.
    """

    def get(self, request):
        as_of, err = _parse_date(request, 'as_of', default=timezone.localdate())
        if err:
            return err
        category = request.query_params.get('category')
        status_filter = request.query_params.get('status')
        company_id = _parse_company(request)
        return Response(build_asset_register(
            as_of, category=category, status_filter=status_filter, company_id=company_id,
        ))


class AssetMovementView(FinancialReportView):
    """
    GET /api/v1/reports/asset-movement/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=...

    IAS 16 Asset Movement Report — roll-forward of gross cost, accumulated
    depreciation and NBV between two dates, grouped by category. The format
    auditors expect for the Property, Plant & Equipment note in the AFS.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from', default=None)
        if err: return err
        if from_date is None:
            from_date, err = _parse_date(request, 'from_date', default=None)
            if err: return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err: return err
        # Allow `to_date` as an alias for `to`.
        alt_to = request.query_params.get('to_date')
        if alt_to:
            to_date, err = _parse_date(request, 'to_date', default=to_date)
            if err: return err
        if from_date is None:
            return Response({'error': "Missing required parameter: 'from'"}, status=400)
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        company_id = _parse_company(request)
        return Response(build_asset_movement(
            from_date, to_date, company_id=company_id,
        ))


# ---------------------------------------------------------------------------
# Audit Pack — auditor-ready bundle (TB + P&L + BS + posted JE listing)
# ---------------------------------------------------------------------------
# Note: upstream origin/main carries 501-stub versions of these two views from
# the early-evening hotfix wave. The real implementations below land via the
# day-sprint feature branch (reporting/audit_pack.py module) and supersede the
# stubs entirely. Merge resolution: keep the real implementations.

class AuditPackView(FinancialReportView):
    """
    GET /api/v1/reports/audit-pack/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=<uuid>
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        from .audit_pack import build_audit_pack
        return Response(build_audit_pack(from_date, to_date,
                                         company_id=_parse_company(request)))


class AuditPackPdfView(FinancialReportView):
    """
    GET /api/v1/reports/audit-pack/pdf/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=<uuid>

    Streams the audit pack as a PDF download.
    """

    def get(self, request):
        from_date, err = _parse_date(request, 'from')
        if err:
            return err
        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        if from_date > to_date:
            return Response({'error': "'from' must be on or before 'to'"}, status=400)
        from .audit_pack import build_audit_pack_pdf
        from django.http import HttpResponse
        try:
            pdf = build_audit_pack_pdf(from_date, to_date,
                                       company_id=_parse_company(request))
        except Exception as exc:  # noqa: BLE001
            return Response({'error': f'Audit pack PDF failed: {exc}'}, status=500)
        filename = f"audit_pack_{from_date}_to_{to_date}.pdf"
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp


# ───────────────────────────────────────────────────────────────────────
# Frozen-figure drift detection (CFO directive 2026-05-17, handover § P1)
# ───────────────────────────────────────────────────────────────────────

class FrozenDriftView(FinancialReportView):
    """
    GET /api/v1/reports/frozen-drift/?period=FY25_Jun2025[&company=<uuid>]

    Returns a list of dashboard tiles whose displayed value drifts from
    the CFO-locked FrozenFigure by more than the row's tolerance_pct.
    Frontend renders a red banner on /dashboard when `all_ok` is False.
    """

    def get(self, request):
        from .frozen_drift import build_frozen_drift
        period = request.query_params.get('period', '').strip()
        if not period:
            return Response(
                {'error': "'period' query param required, e.g. FY25_Jun2025"},
                status=400,
            )
        return Response(build_frozen_drift(
            period=period,
            company_id=_parse_company(request),
        ))


class FrozenDriftNarrativeView(FinancialReportView):
    """
    GET /api/v1/reports/frozen-drift/narrative/?period=FY25_Jun2025[&company=<uuid>][&ai=0]

    Same drift set as frozen-drift, plus a plain-English 'why' narrative per
    drifting tile: the largest posted journal entries booked to the tile's
    accounts SINCE the figure was locked, explained by the in-app AI. Read-only;
    posts nothing. Pass ai=0 to skip the AI call and return facts only.
    """

    def get(self, request):
        from .drift_narrator import build_drift_narratives
        period = request.query_params.get('period', '').strip()
        if not period:
            return Response(
                {'error': "'period' query param required, e.g. FY25_Jun2025"},
                status=400,
            )
        use_ai = request.query_params.get('ai', '1').strip().lower() not in ('0', 'false', 'no')
        return Response(build_drift_narratives(
            period=period,
            company_id=_parse_company(request),
            use_ai=use_ai,
            user=request.user,
        ))


class FrozenFigureAckView(FinancialReportView):
    """
    POST /api/v1/reports/frozen-drift/acknowledge/

    Body: { "period": "...", "line_label": "...", "override_password": "..." }

    Acknowledges a single drift, silencing the banner for this
    (period, line_label) pair until manually un-acknowledged. The
    override password must match the OMNI_FINANCIAL_LOCK_OVERRIDE env
    var (no hardcoded default — fail-closed if unset; 2026-07-17 audit).
    """

    permission_classes = []  # use DRF default; superuser check below

    def post(self, request):
        from django.utils.crypto import constant_time_compare
        from ledger.locks import _required_override
        from ledger.models import FrozenFigure

        user = request.user
        if not (user and user.is_authenticated and user.is_superuser):
            return Response({'error': 'Superuser required.'}, status=403)

        body = request.data or {}
        period      = (body.get('period') or '').strip()
        line_label  = (body.get('line_label') or '').strip()
        override_pw = body.get('override_password') or ''

        if not (period and line_label):
            return Response(
                {'error': 'period and line_label are required.'},
                status=400,
            )

        # CFO directive 2026-06-23 (Internal Audit, Oprah): the shared override
        # password is RETIRED for frozen-figure changes. Acknowledging a locked-
        # figure drift now requires an APPROVED CFO frozen-change request
        # (maker-checker), and the attempt is audited either way. Raises 403 with
        # the frozen message if there is no approved request. The unrelated
        # closed-period JE lock (ledger/locks.py) keeps its own override.
        from core.frozen_controls import assert_frozen_change_allowed
        from core.models import FrozenComponent
        assert_frozen_change_allowed(
            FrozenComponent.Key.ADIC_GWP_FIGURE, user, consume=True)

        try:
            ff = FrozenFigure.objects.get(period=period, line_label=line_label)
        except FrozenFigure.DoesNotExist:
            return Response({'error': 'Frozen figure not found.'}, status=404)

        from django.utils import timezone
        ff.acknowledged_by_override = True
        ff.acknowledged_by          = user
        ff.acknowledged_at          = timezone.now()
        ff.save(update_fields=[
            'acknowledged_by_override',
            'acknowledged_by',
            'acknowledged_at',
            'updated_at',
        ])
        return Response({'ok': True, 'period': period, 'line_label': line_label})


# ---------------------------------------------------------------------------
# XLSX export — generic dispatcher
# CFO directive 2026-05-20: every report page must offer Excel download.
# ---------------------------------------------------------------------------

class ReportXlsxExportView(FinancialReportView):
    """
    GET /api/v1/reports/export-xlsx/?report=<name>&<filters>

    Supported report names are listed in xlsx_export.SUPPORTED_REPORTS.
    Filters mirror the JSON endpoint of each report
    (e.g. ?report=general_ledger&account=100003&from=2025-07-01&to=2026-03-31).
    """

    def get(self, request):
        report_key = request.query_params.get('report')
        if not report_key:
            return Response(
                {
                    'error': "Missing required parameter: 'report'.",
                    'supported': list(_XLSX_SUPPORTED),
                },
                status=400,
            )
        return build_report_xlsx_response(report_key, request.query_params)


class GraphitePaymentsView(FinancialReportView):
    """
    GET /api/reports/graphite-payments/
        ?from=YYYY-MM-DD&to=YYYY-MM-DD
        &partner=DPO&status=success&policy_number=...
        &include_refunds=true&limit=1000&offset=0

    Omni-side view of the Graphite V2 payment-transaction feed (mirrored by the
    `pull_graphite_payments` command). Read-only; same data as the Graphite
    Reporting Portal transaction report.
    """

    def get(self, request):
        from datetime import timedelta

        from .graphite_payments import build_graphite_payments

        to_date, err = _parse_date(request, 'to', default=timezone.localdate())
        if err:
            return err
        from_date, err = _parse_date(
            request, 'from', default=to_date - timedelta(days=29),
        )
        if err:
            return err
        if from_date > to_date:
            return Response(
                {'error': "'from' must be on or before 'to'."},
                status=400,
            )

        def _int(param, default):
            try:
                return int(request.query_params.get(param, default))
            except (TypeError, ValueError):
                return default

        include_refunds = (
            request.query_params.get('include_refunds', 'true').lower()
            not in ('false', '0', 'no')
        )

        data = build_graphite_payments(
            date_from=from_date,
            date_to=to_date,
            partner=request.query_params.get('partner', '').strip(),
            status=request.query_params.get('status', '').strip(),
            policy_number=request.query_params.get('policy_number', '').strip(),
            include_refunds=include_refunds,
            limit=_int('limit', 1000),
            offset=_int('offset', 0),
        )
        return Response(data)


class GraphiteAgeAnalysisView(APIView):
    """
    GET /api/reports/graphite-age-analysis/?search=&status=&limit=

    Premium-debtors age analysis pulled READ-ONLY from the Graphite V2 replica
    (omni-only — no Graphite changes). Same shape as AR Aging so the page matches.
    """

    # Financial data — same finance/management gate as every other report
    # (CFO directive 2026-06-20). Without this the whole premium-debtor book was
    # readable by any authenticated user (DeepSeek flag, 2026-08-30).
    permission_classes = [IsAuthenticated, CanViewFinancials]

    def get(self, request):
        from integrations.graphite_age import GraphiteAgeNotConfigured
        from .graphite_age_analysis import build_graphite_age_analysis

        def _int(p, d):
            try:
                return int(request.query_params.get(p, d))
            except (TypeError, ValueError):
                return d

        try:
            data = build_graphite_age_analysis(
                search=request.query_params.get('search', '').strip(),
                status=request.query_params.get('status', '').strip(),
                limit=_int('limit', 20000),
            )
            return Response(data)
        except GraphiteAgeNotConfigured as e:
            return Response({'error': str(e), 'source_unconfigured': True}, status=503)
        except Exception as e:  # noqa: BLE001 — surface the read failure cleanly
            return Response(
                {'error': f'Graphite age-analysis read failed: {e}'}, status=502,
            )


# ───────────────────────────────────────────────────────────────────────
# Renewal report (Workstream B / B1) — policies renewing in a chosen month
# ───────────────────────────────────────────────────────────────────────

class RenewalReportView(APIView):
    """GET /api/reports/renewals/?month=9[&section=domestic|commercial][&export=xlsx|csv]

    Every in-force Graphite policy renewing in the chosen MONTH (any year), read
    read-only from the Graphite replica. Built to the Finance-settled rules — see
    reporting/renewal_report.py. Same finance/management gate as the premium book.
    """
    permission_classes = [IsAuthenticated, CanViewFinancials]

    def get(self, request):
        import calendar
        import csv as _csv

        from django.http import StreamingHttpResponse

        from reporting import renewal_report as rr
        from reporting.xlsx_export import Sheet, build_sheets_xlsx_response

        try:
            month = int(request.query_params.get('month', '0'))
        except (TypeError, ValueError):
            month = 0
        if not (1 <= month <= 12):
            return Response({'error': 'month is required (1-12)'}, status=400)

        section = (request.query_params.get('section', '') or '').strip().lower() or None
        export = (request.query_params.get('export', '') or '').strip().lower()

        try:
            rows = rr.fetch_renewals(month, section)
        except Exception as e:  # noqa: BLE001 — surface the read failure cleanly
            return Response({'error': f'Graphite renewal read failed: {e}'}, status=502)

        mlabel = calendar.month_name[month]
        seclabel = {'domestic': 'Domestic', 'commercial': 'Commercial'}.get(
            section or '', 'Domestic & Commercial')
        base = 'Renewals_%s_%s' % (mlabel, seclabel.replace(' & ', '_').replace(' ', '_'))
        meta = [
            f'Policies renewing in {mlabel} (any year) — {seclabel}',
            f'{len(rows)} policies. Renewal date = the policy anniversary, driven off '
            'the last anniversary / new-business invoice. First-year policies are flagged.',
        ]

        if export == 'xlsx':
            matrix = rr.to_matrix(rows)
            sheet = Sheet(title=f'Renewals — {mlabel}', headers=matrix[0],
                          rows=matrix[1:], meta=meta)
            return build_sheets_xlsx_response(f'{base}.xlsx', [sheet])

        if export == 'csv':
            matrix = rr.to_matrix(rows)

            class _Echo:
                def write(self, value):
                    return value

            writer = _csv.writer(_Echo())
            resp = StreamingHttpResponse(
                (writer.writerow(r) for r in matrix),
                content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = f'attachment; filename="{base}.csv"'
            resp['Cache-Control'] = 'no-cache'
            return resp

        return Response({
            'columns': rr.COLUMNS, 'rows': rows, 'count': len(rows),
            'month': month, 'month_label': mlabel, 'section': seclabel, 'meta': meta,
        })


# ───────────────────────────────────────────────────────────────────────
# Premium lapse early-warning (wow feature #6)
# ───────────────────────────────────────────────────────────────────────

class PremiumLapseView(FinancialReportView):
    """
    GET /api/v1/reports/premium-lapse/?months=6

    Policies whose most-recent debits are failing, tiered by the length of the
    trailing failure run, with the monthly premium at risk. Read-only.
    """

    def get(self, request):
        from .premium_lapse import compute_lapse_risk
        try:
            months = int(request.query_params.get('months', '6'))
        except (TypeError, ValueError):
            months = 6
        months = max(1, min(months, 24))
        return Response(compute_lapse_risk(months=months))
# Cost-per-Productive-Hour League (wow feature #9) — SENSITIVE (payroll)
# ───────────────────────────────────────────────────────────────────────

class IsCostLeagueViewer(BasePermission):
    """CFO / EXCO / CEO / COO / HR only (CFO directive 2026-07-22). Payroll cost
    + individual productivity — never visible to ordinary staff or finance."""
    message = 'The cost-per-hour league is restricted to CFO / EXCO / CEO / COO / HR.'
    _TITLES = {'cfo', 'executive', 'hr_manager'}

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True
        from core.models import get_user_profile
        prof = get_user_profile(user)
        return bool(prof and (getattr(prof, 'title', '') or '').lower() in self._TITLES)


class CostPerHourView(APIView):
    """
    GET /api/v1/reports/cost-per-hour/?month=YYYY-MM

    Department (and person) payroll cost per Time Doctor productive hour for the
    month, scoped to the top-bar company. Read-only. CFO/EXCO/CEO/COO/HR only.
    """
    permission_classes = [IsAuthenticated, IsCostLeagueViewer]

    def get(self, request):
        from .cost_per_hour import compute_cost_per_hour
        from django.utils import timezone
        month = (request.query_params.get('month') or '').strip()
        if not month:
            month = timezone.now().strftime('%Y-%m')
        return Response(compute_cost_per_hour(month, company_id=_parse_company(request)))
