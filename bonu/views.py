"""
bonu/views.py — the BONU dashboard API. READ ONLY.

CFO 2026-08-03: "we will make a nice dashboard with these info in omni, for now let us
wire to GL later". So this reads the ledger and the invoice detail and returns numbers.
Nothing here posts a journal, changes an account or moves a reported figure.

Access: the same HRIS/finance gate used by the rest of the finance surfaces — this shows
supplier-level spend and a revenue line, so it is not for general staff.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

BONU_ACCOUNTS = {
    'revenue': '100004',        # Written Premium - BONU
    'claims': '103014',         # BONU Claims
    'commission': '107006',     # BONU Commission
    'admin': '111041',          # BONU Admin Expenses
    'acquisition': '118007',    # BONU Acqusition  (sic — the account name in the CoA)
    'receivable': '290001',     # BONU Accounts Receivable
}


def _deny(request):
    """Finance + management (plus admins / superusers).

    CFO directive 2026-08-11: ALSO the named BONU team (bonu/access.py). The BONU
    team lead's title is `operations`, so the financials gate alone shut her out of
    the section she runs — and the only way in through it was to make her finance,
    which would have handed her the GL as well.

    CFO directive 2026-08-04: this was wired to the HRIS gate
    (user_can_access_hris), which admits only finance LEADERSHIP (CFO /
    Finance Manager / Financial Controller) + HR — so ordinary accountants
    were wrongly blocked from a finance screen. Align it with the standard
    financials gate (UserProfile.can_view_financials / CanViewFinancials),
    the same one guarding the GL, reports and dashboards, which includes
    accountants and the rest of finance + management.
    """
    from bonu.access import can_view_bonu
    if can_view_bonu(request.user):
        return None
    return Response({'detail': 'BONU analysis is restricted to finance, management and '
                               'the BONU team.'},
                    status=status.HTTP_403_FORBIDDEN)


def _gl_monthly():
    """Monthly movement on every BONU account, straight from the ledger."""
    from ledger.models import Account, JournalEntryLine as JL
    out = defaultdict(lambda: defaultdict(float))
    balances = {}
    for key, code in BONU_ACCOUNTS.items():
        acc = Account.objects.filter(code=code).first()
        if acc is None:
            balances[key] = None
            continue
        net = 0.0
        for l in (JL.objects.filter(account=acc)
                  .select_related('journal_entry')
                  .only('debit_bwp', 'credit_bwp', 'journal_entry__entry_date')):
            d = getattr(l.journal_entry, 'entry_date', None)
            if d is None:
                continue
            dr, cr = float(l.debit_bwp or 0), float(l.credit_bwp or 0)
            # Revenue and the receivable read naturally in opposite directions; present
            # each as the figure a reader expects to see positive.
            amt = (cr - dr) if key == 'revenue' else (dr - cr)
            out[key][d.strftime('%Y-%m')] += amt
            net += amt
        balances[key] = round(net, 2)
    months = sorted({m for series in out.values() for m in series})
    return months, {k: {m: round(v.get(m, 0.0), 2) for m in months} for k, v in out.items()}, balances


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard(request):
    """GET /api/v1/bonu/dashboard/ — the headline picture."""
    denied = _deny(request)
    if denied is not None:
        return denied
    months, series, balances = _gl_monthly()

    rev = series.get('revenue', {})
    posted_months = [m for m, v in rev.items() if v]
    last_posted = posted_months[-1] if posted_months else None
    recent = [v for m, v in sorted(rev.items()) if v][-6:]
    run_rate = round(sum(recent) / len(recent), 2) if recent else 0.0

    # Months that should carry revenue but do not — the finding that started this.
    gap_months, gap_value = [], 0.0
    if last_posted:
        y, mo = int(last_posted[:4]), int(last_posted[5:])
        cur = datetime.date(y, mo, 1)
        today = timezone.localdate().replace(day=1)
        while cur < today:
            cur = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            if cur >= today:
                break
            gap_months.append(cur.strftime('%Y-%m'))
        gap_value = round(run_rate * len(gap_months), 2)

    claims = balances.get('claims') or 0.0
    revenue = balances.get('revenue') or 0.0
    costs = sum((balances.get(k) or 0.0) for k in ('claims', 'commission', 'admin', 'acquisition'))
    return Response({
        'accounts': BONU_ACCOUNTS,
        'months': months,
        'series': series,
        'balances': balances,
        'loss_ratio_pct': round(100 * claims / revenue, 1) if revenue else None,
        'result_on_ledger': round(revenue - costs, 2),
        'revenue_run_rate': run_rate,
        'last_revenue_month': last_posted,
        'missing_revenue_months': gap_months,
        'missing_revenue_estimate': gap_value,
        'caveat': ('Revenue stops after the last posted month above. Costs are posted to date, so '
                   'the result shown is understated until the missing months are invoiced and '
                   'recognised. GL posting from this screen is deliberately not wired.'),
    })


#: The forensics screen aggregates in Python, so it reads a bounded number of rows.
#: The line-level detail behind it aggregates in SQL and is NOT bounded by this.
FORENSICS_LINE_CAP = 5000


def filtered_lines(p):
    """The BONU invoice lines matching the caller's filters.

    ONE definition, used by both the forensics aggregates and the line-level detail
    behind them. Kutlo Keitumele's request (11 Aug 2026) is to reconcile the
    consolidated billed figure against the underlying transactions -- which only
    works if "the underlying transactions" means exactly the same rows the
    consolidated figure was built from. Two hand-copied filter blocks would drift,
    and the reconciliation would then report a difference that was really just a
    mismatch between two queries.

    Filters: law firm, individual lawyer, case type ("show me every divorce"),
    scheme member reference, and service-date range.
    """
    from bonu.models import BonuInvoiceLine
    # `id` last, and it is load-bearing, not tidiness. Two lines on different
    # invoices can share an invoice_date AND a line_no, and with only those two
    # columns their relative order is undefined — Postgres may return them one
    # way for page 1 and the other way for page 2. The page is then served with
    # a row repeated and a row missing, silently, on a screen whose whole
    # promise is that the lines add up to the figure above them. A unique final
    # key makes the ordering total, so paging can neither drop nor duplicate.
    qs = (BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
          .order_by('invoice__invoice_date', 'line_no', 'id'))
    if p.get('firm'):
        qs = qs.filter(invoice__firm__name__icontains=p['firm'])
    if p.get('lawyer'):
        qs = qs.filter(fee_earner__icontains=p['lawyer'])
    if p.get('matter_type'):
        qs = qs.filter(matter_type=p['matter_type'])
    if p.get('member_ref'):
        qs = qs.filter(member_ref__iexact=p['member_ref'])
    if p.get('date_from'):
        qs = qs.filter(service_date__gte=p['date_from'])
    if p.get('date_to'):
        qs = qs.filter(service_date__lte=p['date_to'])
    return qs


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def forensics(request):
    """GET /api/v1/bonu/forensics/ — invoice-level over-billing checks."""
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.forensics import run_rules, summarise
    from bonu.models import BonuInvoiceLine, BonuInvoice, LawFirm

    p = request.query_params
    qs = filtered_lines(p)
    lines = list(qs[:FORENSICS_LINE_CAP])
    # These aggregates are built in Python over a bounded read. Past the cap the
    # figures below understate, and they would then sit above a link to a detail
    # total (aggregated in SQL over everything) that contradicts them. Say so.
    lines_total = qs.count()
    lines_truncated = len(lines) >= FORENSICS_LINE_CAP

    holidays = set()
    try:
        from hris.workforce_brief import holiday_off_dates
        holidays = set(holiday_off_dates())
    except BaseException:      # noqa: BLE001
        pass

    # The agreed monthly SLA fee per firm, so a fee we contracted for is not reported as an
    # over-charge (CFO 2026-08-03: the 40,000 monthly is an SLA for 40 clients).
    from bonu.models import RetainerAgreement
    # A LIST per firm — one firm can hold several SLAs (CFO: 40,000 south, ~85,000 north).
    retainer_fees = defaultdict(list)
    for r in RetainerAgreement.objects.filter(is_active=True):
        retainer_fees[str(r.firm_id)].append(r.monthly_fee)
    findings = run_rules(lines, holidays=holidays, retainer_fees=retainer_fees)
    by_firm = defaultdict(lambda: {'lines': 0, 'billed': 0.0, 'at_risk': 0.0, 'findings': 0})
    for l in lines:
        b = by_firm[l.invoice.firm.name]
        b['lines'] += 1
        b['billed'] += float(l.amount or 0)
    for f in findings:
        if f.get('firm') is not None:
            b = by_firm[f['firm'].name]
            b['at_risk'] += float(f['amount_at_risk'])
            b['findings'] += 1

    # Spend by case type and by individual lawyer — both are answers the CFO asked
    # for directly, and both are cheap once the category exists.
    by_type = defaultdict(lambda: {'lines': 0, 'billed': 0.0, 'matters': set()})
    by_lawyer = defaultdict(lambda: {'lines': 0, 'billed': 0.0, 'firm': '', 'hours': 0.0})
    labels = dict(BonuInvoiceLine.MatterType.choices)
    for l in lines:
        t = by_type[l.matter_type or 'other']
        t['lines'] += 1
        t['billed'] += float(l.amount or 0)
        if l.matter_ref:
            t['matters'].add(l.matter_ref.strip().lower())
        if (l.fee_earner or '').strip():
            w = by_lawyer[l.fee_earner.strip()]
            w['lines'] += 1
            w['billed'] += float(l.amount or 0)
            w['firm'] = l.invoice.firm.name
            if l.basis == 'hourly' and l.units:
                w['hours'] += float(l.units)

    return Response({
        'filters_applied': {k: p.get(k) for k in
                            ('firm', 'lawyer', 'matter_type', 'member_ref', 'date_from', 'date_to')
                            if p.get(k)},
        'matter_types': [{'value': v, 'label': lbl} for v, lbl in
                         BonuInvoiceLine.MatterType.choices],
        'lines_total': lines_total,
        'lines_truncated': lines_truncated,
        'line_cap': FORENSICS_LINE_CAP,
        'by_matter_type': [{'type': k, 'label': labels.get(k, k), 'lines': v['lines'],
                            'billed': round(v['billed'], 2), 'matters': len(v['matters']),
                            'avg_per_matter': round(v['billed'] / len(v['matters']), 2)
                            if v['matters'] else 0.0}
                           for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]['billed'])],
        'by_lawyer': [{'lawyer': k, 'firm': v['firm'], 'lines': v['lines'],
                       'billed': round(v['billed'], 2), 'hours': round(v['hours'], 2),
                       'effective_rate': round(v['billed'] / v['hours'], 2) if v['hours'] else None}
                      for k, v in sorted(by_lawyer.items(), key=lambda kv: -kv[1]['billed'])],
        'invoices': BonuInvoice.objects.count(),
        'lines_checked': len(lines),
        'firms': LawFirm.objects.count(),
        'summary': summarise(findings),
        # Members: the same person claiming again, often through a different firm. Identified
        # by a one-way token — no names are stored or shown.
        'members': _floats(_member_block(lines)),
        'by_firm': [{'firm': k, **v} for k, v in
                    sorted(by_firm.items(), key=lambda kv: -kv[1]['at_risk'])],
        'findings': [{
            'code': f['code'], 'severity': f['severity'], 'title': f['title'],
            'detail': f['detail'], 'amount_at_risk': float(f['amount_at_risk']),
            'question_for_firm': f['question_for_firm'],
            'firm': getattr(f.get('firm'), 'name', ''),
            'invoice': getattr(f.get('invoice'), 'invoice_number', ''),
        } for f in findings[:300]],
        'no_data': not lines,
        # Was a dead end: it quoted a HARDCODED "890 claim lines worth P6.34M" (a
        # figure that cannot drift with the data, so it stops being true silently) and
        # told staff to load an invoice pack. Since 13-Aug-2026 BONU's detail lives in
        # the Schedule tab, so send them there.
        'no_data_note': ('These twelve checks read confirmed bills, and none are loaded, so there '
                         'is nothing for them to test yet. BONU\'s fee-note detail lives on the '
                         'Schedule tab — start there. Bills arrive here once they are confirmed '
                         'under "Bills to check".'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def retainers(request):
    """GET /api/v1/bonu/retainers/ — did the flat fee buy what it was meant to?

    The CFO's Jeremiah-and-Taldi problem: P85,000 a month for ~40 cases, and no invoice
    can tell you whether 40 cases were actually carried. This reads the case register.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.retainer import double_dipping, scorecard
    from bonu.models import BonuInvoiceLine, LegalCase, RetainerAgreement

    out = []
    for r in RetainerAgreement.objects.filter(is_active=True).select_related('firm'):
        cases = list(LegalCase.objects.filter(retainer=r))
        card = scorecard(r, cases)
        card['double_dipping'] = double_dipping(
            r, cases,
            BonuInvoiceLine.objects.select_related('invoice').filter(invoice__firm=r.firm))
        out.append(card)
    return Response({
        'retainers': out,
        'no_data': not out,
        'no_data_note': ('No retainer is recorded yet. Add the agreement (firm, monthly fee, '
                         'committed caseload, SLA days) and the case register, and this page '
                         'answers what the fee actually bought.'),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_invoice(request):
    """POST /api/v1/bonu/upload-invoice/ — read an invoice, fill the figures in.

    Returns a DRAFT only. Nothing is saved: the accountant confirms on screen beside the
    document. Excel and text-layer PDFs are read exactly with no AI; a scan is reported as
    needing the vision path rather than guessed at.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.ingest import parse_invoice

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a file as "file".'},
                        status=status.HTTP_400_BAD_REQUEST)
    if f.size > 25 * 1024 * 1024:
        return Response({'detail': 'That file is larger than 25 MB.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # The parse is kept as a document in the waiting room rather than handed back and
    # forgotten: it gives the accountant the duplicate warnings up front, and it leaves an
    # audit trail of exactly what the machine proposed before anyone corrected it.
    from bonu.mailbox import ingest_blob
    doc = ingest_blob(f.name, f.read(), arrived_by='upload')
    return Response({
        'document_id': str(doc.pk),
        'status': doc.status,
        'firm_guess': {'id': str(doc.firm_id), 'name': doc.firm.name} if doc.firm_id else None,
        'warnings': doc.warnings,
        **doc.draft,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def documents(request):
    """GET /api/v1/bonu/documents/ — the waiting room: bills read, not yet confirmed."""
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.models import IngestedDocument

    qs = IngestedDocument.objects.select_related('firm', 'invoice')
    state = (request.query_params.get('status') or '').strip()
    if state:
        qs = qs.filter(status=state)
    elif not request.query_params.get('all'):
        qs = qs.exclude(status__in=[IngestedDocument.Status.CONFIRMED,
                                    IngestedDocument.Status.DISCARDED])

    rows = []
    for d in qs[:120]:
        draft = d.draft or {}
        hdr = draft.get('header') or {}
        rows.append({
            'id': str(d.pk), 'filename': d.filename, 'arrived_by': d.arrived_by,
            'from_address': d.from_address, 'firm': getattr(d.firm, 'name', ''),
            'firm_id': str(d.firm_id) if d.firm_id else None,
            'status': d.status, 'status_label': d.get_status_display(),
            'extraction_method': d.extraction_method, 'extraction_error': d.extraction_error,
            'invoice_number': hdr.get('invoice_number') or '',
            'invoice_date': hdr.get('invoice_date') or '',
            'header_total': hdr.get('total'),
            'lines': len(draft.get('lines') or []),
            'lines_total': draft.get('lines_total'),
            'warnings': d.warnings or [],
            'received': d.created_at.date().isoformat() if d.created_at else '',
        })
    return Response({
        'documents': rows,
        'waiting': sum(1 for r in rows if r['status'] == 'parsed'),
        'needs_ocr': sum(1 for r in rows if r['status'] == 'needs_ocr'),
        'failed': sum(1 for r in rows if r['status'] == 'failed'),
        'firms': [{'id': str(f.pk), 'name': f.name} for f in _firms()],
        'no_data_note': ('Nothing is waiting. Upload a bill, or ask the firms to email them in '
                         'once mailbox reading is switched on.'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def document_detail(request, doc_id):
    """GET /api/v1/bonu/documents/<id>/ — one bill, the way the confirm screen needs it."""
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.models import BonuInvoiceLine, IngestedDocument

    d = IngestedDocument.objects.select_related('firm').filter(pk=doc_id).first()
    if d is None:
        return Response({'detail': 'That document is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)
    return Response({
        'id': str(d.pk), 'filename': d.filename, 'status': d.status,
        'arrived_by': d.arrived_by, 'from_address': d.from_address,
        'firm_id': str(d.firm_id) if d.firm_id else None,
        'firm': getattr(d.firm, 'name', ''),
        'extraction_method': d.extraction_method, 'extraction_error': d.extraction_error,
        'text_preview': d.text_preview, 'warnings': d.warnings or [],
        'draft': d.draft or {},
        'matter_types': [{'value': v, 'label': lbl}
                         for v, lbl in BonuInvoiceLine.MatterType.choices],
        'bases': [{'value': v, 'label': lbl} for v, lbl in BonuInvoiceLine.Basis.choices],
        'firms': [{'id': str(f.pk), 'name': f.name} for f in _firms()],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_document(request, doc_id):
    """POST /api/v1/bonu/documents/<id>/confirm/ — the human agrees, the invoice is created.

    This is the ONLY route that turns a read document into a payable, and it takes what the
    accountant agreed rather than what the machine read.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.confirm import confirm
    from bonu.models import IngestedDocument

    d = IngestedDocument.objects.filter(pk=doc_id).first()
    if d is None:
        return Response({'detail': 'That document is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        invoice = confirm(d, request.data or {}, user=request.user)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({'ok': True, 'invoice_id': str(invoice.pk),
                     'invoice_number': invoice.invoice_number,
                     'firm': invoice.firm.name, 'total': float(invoice.total),
                     'lines': invoice.lines.count(),
                     'message': f'Invoice {invoice.invoice_number} saved with '
                                f'{invoice.lines.count()} line(s).'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def discard_document(request, doc_id):
    """POST /api/v1/bonu/documents/<id>/discard/ — not a bill, or a duplicate. Kept, not deleted."""
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.models import IngestedDocument

    d = IngestedDocument.objects.filter(pk=doc_id).first()
    if d is None:
        return Response({'detail': 'That document is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)
    if d.status == IngestedDocument.Status.CONFIRMED:
        return Response({'detail': 'That one is already an invoice. Query the invoice instead.'},
                        status=status.HTTP_400_BAD_REQUEST)
    d.status = IngestedDocument.Status.DISCARDED
    d.save(update_fields=['status', 'updated_at'])
    return Response({'ok': True, 'message': f'{d.filename} set aside. Nothing was deleted.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def panel(request):
    """GET /api/v1/bonu/panel/ — the league table, the hot cases, and what is still to come."""
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu import panel as P
    from bonu.models import (ApprovalThreshold, BonuInvoiceLine, LawFirm, LegalCase,
                             QueryLetter)

    lines = list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
    firms = list(LawFirm.objects.all())
    cases = list(LegalCase.objects.all())
    queries = list(QueryLetter.objects.select_related('firm'))
    thresholds = list(ApprovalThreshold.objects.all())

    warnings = P.case_early_warnings(lines, thresholds)
    typical = P.typical_cost_by_matter_type(lines, thresholds)

    return Response({
        'league': _floats(P.league_table(firms, lines, cases, queries)),
        'early_warnings': _floats([w for w in warnings if w.get('message')][:60]),
        'hot_cases': sum(1 for w in warnings if w.get('running_hot') or w.get('over_approval')),
        'typical_cost': _floats([{'matter_type': k, **v} for k, v in typical.items()]),
        'unbilled': _floats(P.unbilled_estimate(cases, lines, thresholds)),
        'classification': _floats(P.unconfirmed_classification(lines)),
        'thresholds': [{'matter_type': t.matter_type, 'approval_above': float(t.approval_above),
                        'typical_cost': float(t.typical_cost) if t.typical_cost is not None else None,
                        'warn_multiple': float(t.warn_multiple), 'note': t.note}
                       for t in thresholds],
        'no_data': not lines,
        'no_data_note': ('No invoice detail loaded yet, so there is nothing to compare firms on. '
                         'The league table fills itself as bills are confirmed.'),
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def queries(request):
    """GET  /api/v1/bonu/queries/  — every letter, what it claimed, what came back.
    POST /api/v1/bonu/queries/  — draft ONE letter per firm from its open findings.

    The draft is saved unsent. Sending is a separate, deliberate act.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu import queries as Q
    from bonu.models import BonuFinding, LawFirm, QueryLetter

    if request.method == 'GET':
        qs = list(QueryLetter.objects.select_related('firm').prefetch_related('findings'))
        return Response({
            'queries': [{
                'id': str(q.pk), 'reference': q.reference, 'firm': q.firm.name,
                'subject': q.subject, 'status': q.status,
                'status_label': q.get_status_display(),
                'amount_queried': float(q.amount_queried or 0),
                'amount_conceded': float(q.amount_conceded or 0),
                'sent_on': q.sent_on.isoformat() if q.sent_on else '',
                'reply_due_on': q.reply_due_on.isoformat() if q.reply_due_on else '',
                'replied_on': q.replied_on.isoformat() if q.replied_on else '',
                'overdue': q.is_overdue(), 'chased_count': q.chased_count,
                'findings': q.findings.count(), 'body': q.body,
            } for q in qs],
            'chase_today': _floats(Q.chase_list(qs)),
            'recovery': _floats(Q.recovery_summary(qs)),
            'unqueried_findings': BonuFinding.objects.filter(resolved=False, queries=None).count(),
        })

    firm_id = (request.data or {}).get('firm_id')
    made = []
    firms = LawFirm.objects.filter(pk=firm_id) if firm_id else LawFirm.objects.all()
    for firm in firms:
        open_findings = list(BonuFinding.objects.filter(firm=firm, resolved=False, queries=None))
        if not open_findings:
            continue
        draft = Q.draft_for_firm(firm, open_findings,
                                existing_count=QueryLetter.objects.filter(firm=firm).count())
        letter = QueryLetter.objects.create(
            firm=firm, reference=draft['reference'], subject=draft['subject'],
            body=draft['body'], amount_queried=draft['amount_queried'],
            reply_due_on=draft['reply_due_on'])
        letter.findings.set(draft['findings'])
        made.append({'id': str(letter.pk), 'reference': letter.reference, 'firm': firm.name,
                     'amount_queried': float(letter.amount_queried),
                     'items': len(draft['findings']), 'left_out': draft['left_out']})

    if not made:
        return Response({'ok': False,
                         'detail': 'Nothing to query — no open findings without a letter already.'})
    return Response({'ok': True, 'drafted': made,
                     'message': f'{len(made)} letter(s) drafted. Nothing has been sent yet.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def query_update(request, query_id):
    """POST /api/v1/bonu/queries/<id>/ — record that it was sent, chased, or answered.

    Sending an email to a law firm is an outward act, so this records the fact rather than
    performing it: the letter text is there to copy, and what came back is captured here so
    the recovery figure is real.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.models import QueryLetter

    q = QueryLetter.objects.filter(pk=query_id).first()
    if q is None:
        return Response({'detail': 'That query is not on file.'},
                        status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    action = (data.get('action') or '').strip()
    today = timezone.localdate()
    fields = []

    if action == 'sent':
        q.status = QueryLetter.Status.SENT
        q.sent_on = q.sent_on or today
        fields += ['status', 'sent_on']
    elif action == 'chased':
        q.chased_count = (q.chased_count or 0) + 1
        q.last_chased_on = today
        fields += ['chased_count', 'last_chased_on']
    elif action in ('conceded', 'rejected', 'replied', 'withdrawn'):
        q.status = action
        q.replied_on = q.replied_on or today
        if data.get('amount_conceded') is not None:
            q.amount_conceded = Decimal(str(data['amount_conceded']))
            fields.append('amount_conceded')
        if data.get('note'):
            q.reply_note = str(data['note'])[:4000]
            fields.append('reply_note')
        fields += ['status', 'replied_on']
    else:
        return Response({'detail': 'Use one of: sent, chased, replied, conceded, rejected, '
                                   'withdrawn.'}, status=status.HTTP_400_BAD_REQUEST)

    q.save(update_fields=list(dict.fromkeys(fields)) + ['updated_at'])
    return Response({'ok': True, 'reference': q.reference, 'status': q.status,
                     'amount_conceded': float(q.amount_conceded or 0)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def member_case(request):
    """GET /api/v1/bonu/member-case/?member_ref=… — "has anything happened on my case?"

    Built for the member-facing app: status, dates and the last movement, and NOTHING else.
    No fees, no narrative, no other member's data. The point is that a firm's silence
    becomes visible to its own client, which costs us nothing and moves cases.

    The customer app still has to be wired to it — today this answers for staff only, so a
    member cannot yet reach it.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu.models import LegalCase

    ref = (request.query_params.get('member_ref') or '').strip()
    if not ref:
        return Response({'detail': 'Give a member reference.'},
                        status=status.HTTP_400_BAD_REQUEST)

    out = []
    for c in LegalCase.objects.select_related('firm').filter(member_ref=ref):
        last = c.events.order_by('-happened_on').first()
        out.append({
            # `handler` rather than `firm.name` — an in-house matter has no firm
            # and reading `.name` off None would 500 this whole screen.
            'case_ref': c.case_ref, 'firm': c.handler,
            'matter_type': c.matter_type, 'status': c.get_status_display(),
            'instructed_on': c.instructed_on.isoformat(),
            'last_activity_on': c.last_activity_on.isoformat() if c.last_activity_on else '',
            'days_since_movement': c.days_quiet(),
            'court_date': c.court_date.isoformat() if c.court_date else '',
            'last_step': last.get_kind_display() if last else '',
            'is_open': c.is_open,
        })
    return Response({'member_ref': ref, 'cases': out,
                     'note': 'Case progress only. No fees and no case detail are shown here.'})


def _member_block(lines):
    """Member-pattern findings plus how much spend can be tied to a member at all."""
    from bonu.member_rules import member_summary, run_member_rules
    findings = run_member_rules(lines)
    block = member_summary(lines)
    block['findings'] = [{
        'code': f['code'], 'severity': f['severity'], 'title': f['title'],
        'detail': f['detail'], 'amount_at_risk': float(f['amount_at_risk'] or 0),
        'question_for_firm': f['question_for_firm'],
        'firm': getattr(f.get('firm'), 'name', ''),
    } for f in findings[:80]]
    return block


def _firms():
    from bonu.models import LawFirm
    return LawFirm.objects.filter(is_active=True).order_by('name')


def _floats(obj):
    """Decimals and dates → JSON-safe, recursively. DRF will not serialise a Decimal here."""
    import datetime as _dt
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_floats(v) for v in obj]
    return obj


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ai_review(request):
    """POST /api/v1/bonu/ai-review/ — DeepSeek over what the rules cannot describe.

    Firm names are anonymised and member data never leaves. Read-only unless
    `persist: true` is passed, which saves the findings for follow-up.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from bonu import ai_review as air
    from bonu.forensics import run_rules
    from bonu.models import BonuInvoiceLine

    qs = (BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
          .order_by('-invoice__invoice_date'))
    firm = (request.data or {}).get('firm')
    if firm:
        qs = qs.filter(invoice__firm__name__icontains=firm)
    lines = list(qs[:air.MAX_LINES_PER_CALL])
    if not lines:
        return Response({'ok': False,
                         'detail': 'No invoice lines loaded yet — nothing for the AI to review.'})

    rules = run_rules(lines)
    data, firm_map, err = air.review(lines, rules)
    saved = 0
    if (request.data or {}).get('persist') and data.get('findings'):
        saved = air.persist(data, {l.pk: l for l in lines})
    return Response({'ok': err is None, 'error': err, 'lines_reviewed': len(lines),
                     'result': data, 'saved_findings': saved})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def invoice_lines(request):
    """GET /api/v1/bonu/lines/ — the transactions BEHIND a consolidated billed figure.

    Kutlo Keitumele, 11 August 2026: *"it would be beneficial to have a feature that
    provides a detailed breakdown of the consolidated billed amount, rather than only
    displaying the consolidated figure. This functionality would assist the Finance
    team with reconciliation, verification, and identifying any discrepancies between
    the underlying transactions and the consolidated billed amount."*

    Until now every BONU screen showed totals only: P4.8m billed, P1.1m for one firm,
    620 matters. Correct figures with nothing underneath them, so Finance could not
    verify a single pula of it or explain a difference to the union.

    Two things make this a control rather than just another table:

      * the rows come from `filtered_lines()` — the SAME query the consolidated
        figures are built from, so a difference here is a real difference and not two
        queries disagreeing;
      * the totals are aggregated in SQL over EVERY matching row, then compared with
        the total of the rows actually returned. When the page is truncated the
        response says so out loud. A reconciliation screen that silently drops rows
        is worse than no reconciliation screen.

    Member references only, never a member name or Omang — the same rule the rest of
    BONU follows.
    """
    denied = _deny(request)
    if denied is not None:
        return denied
    from django.db.models import Count, Sum
    from bonu.models import BonuInvoiceLine

    qs = filtered_lines(request.query_params)

    # Over EVERY matching row, in SQL. This is the figure the consolidated view
    # claims, and the number Finance reconciles to.
    totals = qs.aggregate(n=Count('id'), billed=Sum('amount'))
    total_lines = totals['n'] or 0
    total_billed = totals['billed'] or Decimal('0')

    try:
        limit = min(max(int(request.query_params.get('limit', 500)), 1), 2000)
    except (TypeError, ValueError):
        limit = 500
    try:
        offset = max(int(request.query_params.get('offset', 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    rows = list(qs[offset:offset + limit])
    labels = dict(BonuInvoiceLine.MatterType.choices)
    sources = dict(BonuInvoiceLine.ClassifiedBy.choices)
    shown_billed = sum((r.amount or Decimal('0')) for r in rows)

    out = []
    for r in rows:
        inv = r.invoice
        out.append({
            'id': str(r.id),
            'invoice_number': getattr(inv, 'invoice_number', '') or '',
            'invoice_date': inv.invoice_date.isoformat() if inv and inv.invoice_date else None,
            'firm': getattr(getattr(inv, 'firm', None), 'name', '') or '',
            'line_no': r.line_no,
            'service_date': r.service_date.isoformat() if r.service_date else None,
            'matter_ref': r.matter_ref,
            'member_ref': r.member_ref,
            'fee_earner': r.fee_earner or '',
            'matter_type': r.matter_type,
            'matter_type_label': labels.get(r.matter_type, r.matter_type),
            # An AI guess must never read as a fact (CFO 2026-08-03), so the
            # reconciliation says how the case type was decided.
            'matter_type_source': r.matter_type_source,
            'matter_type_source_label': sources.get(r.matter_type_source, r.matter_type_source),
            'classification_confirmed': r.matter_type_source in ('firm', 'manual'),
            'description': r.matter_description or '',
            'basis': r.basis or '',
            # `units`, not `hours` — the model has no `hours` field. A getattr with
            # a default turned a wrong field name into a silently empty column, while
            # the overview's by-lawyer table reads l.units and showed real figures.
            # Two screens, same data, different answers.
            'hours': str(r.units or ''),
            'rate': str(r.rate or ''),
            'amount': str(r.amount or Decimal('0')),
        })

    # Over EVERY matching row, like the other figures in the strip. Summing this
    # over the current page made it shrink as you paged — a silent partial on the
    # one screen built to forbid silent partials.
    unconfirmed = (qs.exclude(matter_type_source__in=('firm', 'manual'))
                   .aggregate(t=Sum('amount'))['t'] or Decimal('0'))

    return Response({
        # From the model, so a new case type appears here without a second edit.
        'matter_types': [{'value': v, 'label': lbl}
                         for v, lbl in BonuInvoiceLine.MatterType.choices],
        'filters': {k: v for k, v in request.query_params.items()
                    if k in ('firm', 'lawyer', 'matter_type', 'member_ref',
                             'date_from', 'date_to')},
        'reconciliation': {
            # What the consolidated figure should be, over every matching row.
            'total_lines': total_lines,
            'total_billed': str(total_billed),
            # What this page actually shows.
            'shown_lines': len(rows),
            'shown_billed': str(shown_billed),
            'truncated': (offset + len(rows)) < total_lines,
            'balances': len(rows) == total_lines and shown_billed == total_billed,
            # How much of the total rests on a case type nobody confirmed.
            'unconfirmed_classification_billed': str(unconfirmed),
        },
        'limit': limit,
        'offset': offset,
        'lines': out,
    })
