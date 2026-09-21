"""
Load the Alpha Direct Leave Tracker (as-at June 2026) into omni and clear the
old leave loads. CFO directive 2026-07-10.

The tracker is an aggregate ledger (opening + monthly accrual + monthly taken +
running balance). omni's balance engine reads the LATEST hris.LeaveOpeningBalance
with as_at_date <= today per (profile, leave_type_code) as its baseline, so the
clean way to "update the leave" is to write each employee's as-at-2026-06-30
position as a LeaveOpeningBalance row — NOT to fabricate dated LeaveRequests
(LeaveRequest.save recomputes days from dates + rejects overlaps).

Input JSON (parsed off-server): [{name, start, company_sheet,
  types:{annual:{entitlement,opening,accrued}, sick|compassionate|study|special:
  {entitlement,opening}}}].

Matching: employees are matched by full_name against payroll.Employee (exact
normalised name; then first+last token when that is UNIQUE — omni stores full
middle names the tracker omits). An HRISProfile is created if missing (talent
data only — no access change). Genuine spelling differences stay unmatched and
are reported with the closest omni name; nothing is fuzzy-guessed.

"Remove all old leave records" is scoped to the SUPERSEDED loads only:
  * LeaveOpeningBalance with as_at_date < 2026-06-30 (old March load)
  * LeaveRequest whose reason starts "Annual leave taken" (2026-07-02 bulk import)
  Live ESS requests (other reasons) are KEPT. Full dumpdata backup taken first.

  python manage.py import_leave_tracker leave_load.json            # dry run
  python manage.py import_leave_tracker leave_load.json --commit
"""
from __future__ import annotations

import difflib
import json
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from payroll.models import Employee
from hris.models import HRISProfile, LeaveOpeningBalance, LeaveRequest

AS_AT = date(2026, 6, 30)
BATCH = 'leave-tracker-jun2026'
OLD_TAKEN_REASON = 'Annual leave taken'
TYPE_CODES = ['annual', 'sick', 'compassionate', 'study', 'special']
JUNK_NAMES = {'', 'totals', 'total', 'grand total', 'name'}


def _toks(n: str) -> list[str]:
    # drop single-letter middle initials (with/without dot)
    return [t for t in (n or '').split() if len(t.strip('.')) > 1]


def _norm(n: str) -> str:
    return ' '.join(_toks(n)).lower()


def _firstlast(n: str) -> str:
    t = _toks(n)
    return f'{t[0]} {t[-1]}'.lower() if len(t) >= 2 else (t[0].lower() if t else '')


def _dec(v):
    try:
        return Decimal(str(round(float(v), 2)))
    except (TypeError, ValueError):
        return Decimal('0')


class Command(BaseCommand):
    help = 'Load the June-2026 leave tracker as as-at LeaveOpeningBalances; clear old loads. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('json_path')
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        data = json.load(open(opts['json_path'], encoding='utf-8'))
        w = self.stdout.write

        # index active employees by exact-normalised name and by first+last
        emps = list(Employee.objects.filter(status=Employee.Status.ACTIVE))
        by_norm: dict[str, list] = {}
        by_fl: dict[str, list] = {}
        for emp in emps:
            by_norm.setdefault(_norm(emp.full_name), []).append(emp)
            by_fl.setdefault(_firstlast(emp.full_name), []).append(emp)

        def resolve(name):
            """-> (employee, None) | (None, 'unmatched'|'ambiguous')."""
            c = by_norm.get(_norm(name))
            if c:
                return (c[0], None) if len(c) == 1 else (None, 'ambiguous')
            c = by_fl.get(_firstlast(name))
            if c:
                return (c[0], None) if len(c) == 1 else (None, 'ambiguous')
            return (None, 'unmatched')

        planned = []            # (employee, code, entitlement, opening, accrued)
        unmatched, ambiguous, matched_names = [], [], set()
        for e in data:
            if (e.get('name') or '').strip().lower() in JUNK_NAMES:
                continue
            emp, err = resolve(e['name'])
            if err == 'ambiguous':
                ambiguous.append(e['name']); continue
            if err == 'unmatched':
                unmatched.append(e['name']); continue
            matched_names.add(e['name'])
            for code in TYPE_CODES:
                t = e['types'].get(code)
                if not t:
                    continue
                planned.append((emp, code, _dec(t.get('entitlement')),
                                _dec(t.get('opening')), _dec(t.get('accrued'))))

        old_ob = LeaveOpeningBalance.objects.filter(as_at_date__lt=AS_AT)
        old_taken = LeaveRequest.objects.filter(reason__istartswith=OLD_TAKEN_REASON)
        kept = LeaveRequest.objects.exclude(reason__istartswith=OLD_TAKEN_REASON)

        # report
        w(self.style.MIGRATE_HEADING('LEAVE TRACKER LOAD' + ('' if commit else ' (DRY RUN)')))
        w(f'  tracker rows:               {len(data)}')
        w(f'  matched employees:          {len(matched_names)}')
        w(f'  OB rows to write:           {len(planned)}')
        w(f'  ambiguous (>1 omni match):  {len(ambiguous)}  {ambiguous}')
        w(f'  unmatched (spelling diff):  {len(unmatched)}')
        keys = list(by_norm.keys())
        for nm in unmatched:
            hit = difflib.get_close_matches(_norm(nm), keys, n=1, cutoff=0.8)
            sug = f'  ~ omni: {by_norm[hit[0]][0].full_name}' if hit else '  (not in omni)'
            w(f'      {nm}{sug}')
        w(f'  OLD to delete — LeaveOpeningBalance(as_at<{AS_AT}): {old_ob.count()}')
        w(f'  OLD to delete — LeaveRequest reason~"{OLD_TAKEN_REASON}": {old_taken.count()}')
        w(f'  KEEP — other LeaveRequests (live ESS): {kept.count()}')

        if not commit:
            w(self.style.WARNING('DRY RUN — nothing changed. Re-run with --commit.'))
            return

        created_profiles = 0
        with transaction.atomic():
            del_ob = old_ob.delete()[0]
            del_lr = old_taken.delete()[0]
            LeaveOpeningBalance.objects.filter(as_at_date=AS_AT, batch=BATCH).delete()  # idempotent
            objs = []
            profile_of = {}
            for emp, code, ent, opening, accr in planned:
                p = profile_of.get(emp.id)
                if p is None:
                    p, made = HRISProfile.objects.get_or_create(employee=emp)
                    created_profiles += int(made)
                    profile_of[emp.id] = p
                objs.append(LeaveOpeningBalance(
                    profile=p, leave_type_code=code, as_at_date=AS_AT,
                    entitlement_days=ent, opening_balance_days=opening,
                    accrued_days=accr, batch=BATCH))
            LeaveOpeningBalance.objects.bulk_create(objs, batch_size=500)

        w(self.style.SUCCESS(
            f'COMMITTED: deleted old OB={del_ob} old taken-requests={del_lr}; '
            f'created {len(objs)} opening balances ({created_profiles} new HRIS profiles), '
            f'as-at {AS_AT}, batch {BATCH}.'))
