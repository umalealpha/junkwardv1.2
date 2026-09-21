"""genric/services.py — ONE button: Generate GENRIC Pack.

The whole of B3 comes down to ``generate_pack(year, month, ...)``. It pulls the
bank lines for the month out of the existing statement register, classifies
them, rolls up the confirmed GWP, runs the six-step cession, derives the
invoice, builds the thirteen reports, and records a ``GenricPackRun`` with the
manifest of what is in the pack and what is not.

Today this is a full day of manual work: export the FNB statement, rebuild the
collections in Excel, tag each line by hand, roll up the GWP, calculate the
cession, raise the invoice, eyeball the policy book.

Hard stop: this posts NO journal and changes NO GL mapping. It reads and reports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from django.db import transaction

from . import collections as C
from . import constants as K
from . import invoice as INV
from . import policies as P
from . import reports as R
from .cession import compute_cession
from .config import (
    is_remit_to_configured, is_unpaid_days_configured, reinsurer_remit_to,
    UNPAID_DAYS_KEY, UNPAID_DAYS_QUESTION,
)
from .models import GenricPackRun

log = logging.getLogger(__name__)


@dataclass
class PackResult:
    run: GenricPackRun
    context: R.PackContext
    reports: list

    @property
    def blocked(self) -> list:
        return [r for r in self.reports if r.status in (R.BLOCKED, R.NIL_NO_SOURCE)]

    def manifest(self) -> dict:
        return {
            'period': {'year': self.context.year, 'month': self.context.month,
                       'label': self.context.period_label},
            'entity': f'{K.ENTITY_NAME} ({K.ENTITY_BOOK})',
            'part_a_regulatory_pack': [r.as_dict() for r in self.reports],
            'part_b_reinsurance_submission': self._part_b(),
            'part_c_cancellations': next(
                (r.as_dict() for r in self.reports if r.key == 'cancellations'), None),
            'produced': len([r for r in self.reports if r.status == R.OK]),
            'nil_returns': len([r for r in self.reports if r.status == R.NIL_NO_ACTIVITY]),
            'not_produced': len(self.blocked),
        }

    def _part_b(self) -> dict:
        ces = self.context.cession
        return {
            'master_reinsurance': {
                'period': self.context.period_label,
                'entity': f'{K.ENTITY_NAME} ({K.ENTITY_BOOK})',
                'treaty': {
                    'quota_share_ceded': str(K.QUOTA_SHARE_CEDED),
                    'retention': str(K.RETENTION),
                    'ceding_commission': str(K.CEDING_COMMISSION_RATE),
                    'ceding_commission_note': (
                        'The older MOU said 10% — the signed treaty wins.'),
                    'reinsurance_vat': 'Zero-rated (cross-border)',
                },
            },
            'gwp': {
                'confirmed_incl_vat': str(ces.confirmed_gwp_incl_vat),
                'excl_vat': str(ces.gwp_excl_vat),
                'collections': self.context.summary.net_collection_count,
            },
            'bank_recon': {
                'statement': self.context.bank_statement_label,
                'account': K.FNB_COLLECTION_ACCOUNT,
                'unclassified_lines': self.context.summary.unclassified_count,
            },
            'bordereaux': {
                'policy_count': len(self.context.current_policies),
                'basis': 'policy numbers only (DPA)',
            },
            'cession_steps': [
                {'step': s.number, 'label': s.label,
                 'amount': str(s.amount), 'basis': s.basis}
                for s in ces.steps
            ],
            'invoice': {
                'number': self.context.invoice_number,
                'needs_confirmation': self.context.invoice_needs_confirmation,
                'total': str(ces.net_reinsurance_premium_due),
                'vat': '0.00',
                'currency': K.CURRENCY,
                # Alpha Direct PAYS GENRIC — cedant to reinsurer, CFO 13 Sep 2026.
                'payment_direction': K.PAYMENT_DIRECTION,
                'remit_to': (reinsurer_remit_to() if is_remit_to_configured()
                             else ''),
                'remit_to_on_file': is_remit_to_configured(),
            },
        }


def _bank_lines(year: int, month: int, account_number: str = K.FNB_COLLECTION_ACCOUNT):
    """Statement lines for the month, from the EXISTING register.

    Filtered on the account NUMBER, not on a statement id: a month can arrive as
    more than one statement file (a mid-month pull plus a month-end pull), and
    keying on one statement would silently report half a month. The register's
    own dedupe_key guard means the overlap does not double-count.
    """
    from banking.models import BankStatementLine
    start = date(year, month, 1)
    end = P.month_end(year, month)
    return list(
        BankStatementLine.objects
        .select_related('statement__bank_account')
        .filter(transaction_date__gte=start, transaction_date__lte=end)
        .filter(statement__bank_account__account_number=account_number)
        .order_by('transaction_date', 'line_number')
    )


def _statement_label(lines: Sequence) -> str:
    # A month can arrive as more than one statement file, so this is a set.
    # getattr rather than ln.statement.statement_number: build_pack() accepts
    # any line-shaped object, and a caller supplying rows that are not attached
    # to a stored statement must get an empty provenance label — which the
    # Master report then prints as "(none)" — not an AttributeError.
    numbers = sorted({
        getattr(getattr(ln, 'statement', None), 'statement_number', '') or ''
        for ln in lines
    } - {''})
    if not numbers:
        return ''
    return ', '.join(numbers)


def build_pack(year: int, month: int, *,
               current_policies: Optional[Sequence] = None,
               prior_policies: Optional[Sequence] = None,
               policy_source: str = '',
               collection_charges=Decimal('0.00'),
               explicit_invoice_number: Optional[str] = None,
               bank_lines: Optional[Sequence] = None) -> tuple:
    """Assemble the context and build the reports. No database writes."""
    if month < 1 or month > 12:
        raise ValueError(f'month must be 1-12, got {month}')

    lines = list(bank_lines) if bank_lines is not None else _bank_lines(year, month)
    classified = C.classify_lines(lines)
    summary = C.summarise(classified)
    cession = compute_cession(summary.confirmed_gwp_incl_vat, collection_charges)

    current = list(current_policies or [])
    prior = list(prior_policies or [])
    book = P.position(current, summary.net_collection_count)

    try:
        number, needs_confirm = INV.next_invoice_number(explicit_invoice_number)
    except INV.InvoiceNumberUnconfirmed as exc:
        number, needs_confirm = '', True
        log.warning('GENRIC invoice number unconfirmed: %s', exc)

    ctx = R.PackContext(
        year=year, month=month,
        classified=classified, summary=summary, cession=cession,
        book=book, current_policies=current, prior_policies=prior,
        has_prior_export=bool(prior),
        invoice_number=number, invoice_needs_confirmation=needs_confirm,
        policy_source=policy_source,
        bank_statement_label=_statement_label(lines),
    )
    return ctx, R.build_all(ctx)


@transaction.atomic
def generate_pack(year: int, month: int, *, user=None, **kwargs) -> PackResult:
    """THE button. Builds the pack and records the run."""
    ctx, built = build_pack(year, month, **kwargs)

    blocked_notes = []
    if not is_unpaid_days_configured():
        blocked_notes.append({
            'report': 'Cancellations',
            'question': UNPAID_DAYS_QUESTION,
            'setting': UNPAID_DAYS_KEY,
        })
    for r in built:
        if r.status in (R.BLOCKED, R.NIL_NO_SOURCE) and r.key != 'cancellations':
            blocked_notes.append({'report': r.title,
                                  'question': r.notes[0] if r.notes else '',
                                  'setting': ''})

    stmt = None
    if ctx.bank_statement_label:
        from banking.models import BankStatement
        stmt = BankStatement.objects.filter(
            statement_number=ctx.bank_statement_label.split(', ')[0]
        ).first()

    run = GenricPackRun.objects.create(
        period_year=year, period_month=month,
        generated_by=user,
        collection_charges=kwargs.get('collection_charges') or Decimal('0.00'),
        status=(GenricPackRun.Status.PARTIAL if blocked_notes
                else GenricPackRun.Status.COMPLETE),
        bank_statement=stmt,
        policy_source=ctx.policy_source,
        confirmed_gwp_incl_vat=ctx.summary.confirmed_gwp_incl_vat,
        net_reinsurance_premium_due=ctx.cession.net_reinsurance_premium_due,
        invoice_number=ctx.invoice_number,
        blocked_notes=blocked_notes,
    )
    result = PackResult(run=run, context=ctx, reports=built)
    run.manifest = result.manifest()
    run.save(update_fields=['manifest'])
    return result
