"""
ledger/recurring.py

Generator for recurring journal entries.

Given a target period, walks every active RecurringJournalEntry whose
schedule has reached that period and creates a draft JournalEntry from each
template. Generated entries are DRAFT — they still flow through the
submit / approve workflow, so recurrence does NOT bypass any control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.db import transaction

from .models import (
    JournalEntry, JournalEntryLine, RecurringJournalEntry, RecurringJournalEntryLine,
)


ZERO = Decimal('0.00')


@dataclass
class GenerationResult:
    period_end: str
    templates_considered: int = 0
    entries_generated: int = 0
    entries_skipped:   int = 0
    errors:            list = field(default_factory=list)


def _months_step(frequency: str) -> int:
    return {
        RecurringJournalEntry.Frequency.MONTHLY:    1,
        RecurringJournalEntry.Frequency.QUARTERLY:  3,
        RecurringJournalEntry.Frequency.SEMIANNUAL: 6,
        RecurringJournalEntry.Frequency.ANNUAL:     12,
    }.get(frequency, 1)


def _candidate_dates(template: RecurringJournalEntry, target_date: date) -> list[date]:
    """
    Yield every scheduled date up to and including target_date that is at
    or after start_date and at or before end_date (if set), and that hasn't
    already been generated.
    """
    step = _months_step(template.frequency)
    cursor = template.start_date
    cursor = cursor.replace(day=min(template.day_of_period, _safe_eom(cursor)))

    last = template.last_generated_for or (template.start_date - relativedelta(days=1))

    out: list[date] = []
    while cursor <= target_date:
        if (template.end_date is None or cursor <= template.end_date) and cursor > last:
            out.append(cursor)
        cursor = cursor + relativedelta(months=step)
        cursor = cursor.replace(day=min(template.day_of_period, _safe_eom(cursor)))
    return out


def _safe_eom(d: date) -> int:
    """Last day of the month for date d. Returns 28-31."""
    next_month = d.replace(day=28) + relativedelta(days=4)
    return (next_month - relativedelta(days=next_month.day)).day


@transaction.atomic
def generate_for_template(
    template: RecurringJournalEntry,
    target_date: date,
    user: User,
) -> tuple[int, list[str]]:
    """
    Generate any missing JEs for *template* up to *target_date*.

    Returns (count_generated, list_of_entry_numbers).
    """
    if not template.is_active:
        return 0, []

    generated_numbers: list[str] = []
    last_generated = template.last_generated_for

    for run_date in _candidate_dates(template, target_date):
        je = JournalEntry.objects.create(
            entry_date    = run_date,
            description   = f"{template.name} — {run_date.isoformat()}",
            journal_type  = template.journal_type,
            status        = JournalEntry.Status.DRAFT,
            company       = template.company,
            currency_code = template.currency_code,
            exchange_rate = Decimal('1.00000000'),
            created_by    = user,
            source_type   = f'recurring:{template.id}',
            source_id     = template.id,
            notes         = f"Auto-generated from recurring template '{template.name}'.",
        )
        je.save(audit_user=user, audit_description=f'Generated from recurring template {template.id}')

        for line in template.lines.all():
            JournalEntryLine.objects.create(
                journal_entry = je,
                account       = line.account,
                debit_amount  = line.debit_amount or ZERO,
                credit_amount = line.credit_amount or ZERO,
                debit_bwp     = line.debit_amount or ZERO,
                credit_bwp    = line.credit_amount or ZERO,
                description   = line.description,
                contact       = line.contact,
            )

        generated_numbers.append(je.entry_number)
        last_generated = run_date

    if last_generated and last_generated != template.last_generated_for:
        template.last_generated_for = last_generated
        template.save(audit_user=user, audit_description='Recurring JE generation high-water mark')

    return len(generated_numbers), generated_numbers


def generate_all_due(target_date: date, user: User) -> GenerationResult:
    """Run for every active template whose schedule has reached target_date."""
    result = GenerationResult(period_end=target_date.isoformat())
    qs = RecurringJournalEntry.objects.filter(is_active=True).prefetch_related('lines')
    for template in qs:
        result.templates_considered += 1
        try:
            count, _numbers = generate_for_template(template, target_date, user)
            result.entries_generated += count
            if count == 0:
                result.entries_skipped += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append({'template': template.name, 'error': str(exc)})
    return result
