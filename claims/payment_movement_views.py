"""
claims/payment_movement_views.py — B6 Claims Payment Movement Report, the API.

Thin. It reads the period's claims payments out of the bank statement lines
CR-001 tagged, hands them to `claims/payment_movement.py` (which owns every
piece of arithmetic) and returns JSON, or an Excel workbook in the house style.

WHAT IT DOES NOT DO
-------------------
* It is NOT a bank reconciliation. The spec is explicit; the accounting
  reconciliation of the bank stays in Odoo.
* It does not build a statement register, an upload screen or a duplicate
  detector. All three already exist and are live — `banking.BankStatement`
  carries bank account, period, date loaded, loaded by, row count, opening and
  closing balance and status, which is exactly the list B6 asks for. Upload
  stays where it is, under Accounting > Banking.
* It posts nothing, maps nothing to the GL, and moves no money. It is a report.

THE ACCESS GATE
---------------
Finance only. The rows put PAYEE NAMES next to CLAIM AMOUNTS, so the gate is
the control that matters here — not the sidebar entry, which hides a screen
without protecting anything behind it. `permission_classes` sits on the view
itself, so a direct call to the address is refused the same way the screen is.
CanViewFinancials is the same gate every other financial report uses
(core/permissions.py, CFO directive 2026-06-20).

Refreshed by bank statement upload: there is nothing to schedule and nothing to
cache — each request reads whatever statement lines are loaded right now.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from banking.models import BankStatementLine
from core.permissions import CanViewFinancials

from .payment_movement import ClaimPaymentRow, build_payment_movement

REPORT_TITLE = 'Claims Payment Movement Report'


def _parse_date(raw, fallback):
    if not raw:
        return fallback, None
    try:
        return date.fromisoformat(str(raw)), None
    except ValueError:
        return None, f'Expected YYYY-MM-DD, got {raw!r}'


def _payee_of(line) -> str:
    """Who the money went to.

    The matched payment is the better answer when there is one — it is what
    Omni recorded, rather than what the bank's free text happened to say. The
    description is the fallback, because an unmatched line still has to appear
    on the report; leaving it blank would quietly drop payments from the
    analysis.
    """
    pay = line.matched_payment
    if pay is not None:
        for attr in ('bank_beneficiary_name', 'payee_name'):
            value = (getattr(pay, attr, '') or '').strip()
            if value:
                return value
    return (line.description or '').strip()


def collect_claim_payments(date_from: date, date_to: date, bank_account=None):
    """The period's claims payments, as ClaimPaymentRow objects.

    An outflow (amount < 0) that carries a claim reference IS a claims payment
    — that is what CR-001's `claim_reference` means. Inflows are excluded: a
    receipt against a claim is a recovery, not a payment, and adding it to a
    payment analysis would understate the total paid.

    `amount_gross` is handed over as a POSITIVE magnitude; the engine never
    sees the bank's sign convention.
    """
    qs = (BankStatementLine.objects
          .filter(transaction_date__gte=date_from,
                  transaction_date__lte=date_to,
                  amount__lt=0)
          .exclude(claim_reference='')
          .select_related('statement', 'matched_payment')
          .order_by('transaction_date', 'line_number'))
    if bank_account:
        qs = qs.filter(statement__bank_account_id=bank_account)

    return [
        ClaimPaymentRow(
            transaction_date=line.transaction_date,
            payee=_payee_of(line),
            claim_reference=line.claim_reference,
            invoice_reference=line.invoice_reference,
            payment_basis=line.payment_basis,
            amount_gross=abs(Decimal(line.amount)),
            statement_number=line.statement.statement_number,
            description=(line.description or ''),
        )
        for line in qs
    ]


def _bucket_json(b):
    return {
        'key': b.key,
        'label': b.label,
        'count': b.count,
        'amount_gross': str(b.amount_gross),
        'amount_excl_vat': str(b.amount_excl_vat),
        'vat_amount': str(b.vat_amount),
    }


def _row_json(r):
    s = r.source
    return {
        'transaction_date': s.transaction_date.isoformat(),
        'statement_number': s.statement_number,
        'payee': s.payee,
        'claim_reference': s.claim_reference,
        'invoice_reference': s.invoice_reference,
        'payment_basis': s.payment_basis,
        'payment_basis_label': r.basis_label,
        'settlement': s.settlement,
        'amount_gross': str(r.amount_gross),
        'amount_excl_vat': str(r.amount_excl_vat),
        'vat_amount': str(r.vat_amount),
        'was_degrossed': r.was_degrossed,
    }


class ClaimsPaymentMovementView(APIView):
    """GET /api/v1/reports/claims-payment-movement/?from=&to=&bank_account=

    Add ?format=xlsx for the branded workbook.
    """

    # Finance only — the report carries payee names against claim amounts.
    # This is the control. Do not soften it to a hidden menu entry.
    permission_classes = [IsAuthenticated, CanViewFinancials]

    def get(self, request):
        # Botswana time. timezone.localdate() is the date in settings.TIME_ZONE;
        # date.today() is the UTC date on a UTC box, which puts a payment made
        # after 22:00 Gaborone into the wrong period.
        today = timezone.localdate()
        default_from = today.replace(day=1)

        date_from, err = _parse_date(request.query_params.get('from'), default_from)
        if err:
            return Response({'error': f"Invalid 'from': {err}"}, status=400)
        date_to, err = _parse_date(request.query_params.get('to'), today)
        if err:
            return Response({'error': f"Invalid 'to': {err}"}, status=400)
        if date_from > date_to:
            return Response(
                {'error': "'from' is after 'to' — no period to report on."},
                status=400,
            )

        # A bank account id is a UUID. Passing anything else straight into the
        # filter raises inside the database driver and the caller gets a 500
        # where the honest answer is "you sent me nonsense".
        bank_account = request.query_params.get('bank_account') or None
        if bank_account:
            try:
                bank_account = str(uuid.UUID(str(bank_account)))
            except (ValueError, AttributeError, TypeError):
                return Response(
                    {'error': "'bank_account' must be a bank account id."},
                    status=400,
                )
        payments = collect_claim_payments(date_from, date_to, bank_account)
        # The rate is configuration, never a literal here. settings.RC_VAT_RATE
        # is the one VAT constant in this codebase (alpha_finance/settings.py);
        # a second one is how two parts of the system end up disagreeing.
        report = build_payment_movement(payments, settings.RC_VAT_RATE)

        if (request.query_params.get('format') or '').lower() == 'xlsx':
            return self._xlsx(report, date_from, date_to)

        return Response({
            'from': date_from.isoformat(),
            'to': date_to.isoformat(),
            'vat_rate': str(report.vat_rate),
            'total': _bucket_json(report.total),
            'by_type': [_bucket_json(b) for b in report.by_type],
            'by_settlement': [_bucket_json(b) for b in report.by_settlement],
            'degrossed_count': report.degrossed_count,
            'left_gross_count': report.left_gross_count,
            'rows': [_row_json(r) for r in report.rows],
            'notes': [
                'VAT is de-grossed ONLY where the payment basis is INVOICE. '
                'Every other basis is shown gross — repair, AOL, FOR, CIL, '
                'third party, ex-gratia, and any line whose basis could not '
                'be read off the bank narration.',
                'This is not a bank reconciliation. The accounting '
                'reconciliation of the bank stays in Odoo.',
            ],
        })

    def _xlsx(self, report, date_from, date_to):
        # Reuses the branded workbook builder (aware/reporting.py) so this
        # report looks like every other Excel Omni produces.
        from aware.reporting import _wb

        def f(d):
            return float(d)

        kpis = [
            ('Total paid (gross)', f'P {report.total.amount_gross:,.2f}'),
            ('Total excl. VAT', f'P {report.total.amount_excl_vat:,.2f}'),
            ('VAT de-grossed', f'P {report.total.vat_amount:,.2f}'),
            ('Payments de-grossed', f'{report.degrossed_count} of '
                                    f'{report.total.count}'),
            ('VAT rate applied', f'{report.vat_rate * 100:.0f}%'),
        ]
        cols = ['Date', 'Payee', 'Claim', 'Invoice', 'Payment type',
                'Settled against', 'Gross', 'Excl. VAT', 'VAT', 'De-grossed?']
        rows = [[
            r.source.transaction_date.isoformat(),
            r.source.payee,
            r.source.claim_reference,
            r.source.invoice_reference or '—',
            r.basis_label,
            'Supplier invoice' if r.source.invoice_reference else 'Individual claimant',
            f(r.amount_gross), f(r.amount_excl_vat), f(r.vat_amount),
            'Yes' if r.was_degrossed else 'No',
        ] for r in report.rows]

        notes = [
            'VAT de-grossed ONLY where the payment basis is INVOICE. Every '
            'other basis stays gross — repair, AOL, FOR, CIL, third party, '
            'ex-gratia, and any line whose basis could not be read off the '
            'bank narration.',
            'Not a bank reconciliation — the accounting reconciliation of the '
            'bank stays in Odoo.',
            'Contains payee names against claim amounts. Any wider '
            'distribution must be summarised or anonymised.',
        ]
        buf = _wb(
            REPORT_TITLE,
            f'{date_from.isoformat()} to {date_to.isoformat()}',
            kpis, cols, rows, notes, money_cols=(6, 7, 8),
            # Cents, not whole Pula. The excl-VAT and VAT columns have to add
            # back to the gross to the cent, on a report whose entire purpose
            # is that split; '#,##0' would show 87.72 as 88 and the workbook
            # would look like it did not balance.
            money_format='#,##0.00',
        )
        resp = HttpResponse(
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.'
                         'spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = (
            'attachment; filename="claims_payment_movement_'
            f'{date_from.isoformat()}_{date_to.isoformat()}.xlsx"'
        )
        return resp
