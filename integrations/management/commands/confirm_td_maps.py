"""
integrations/management/commands/confirm_td_maps.py

Confirm Time Doctor ↔ Employee mappings so those people reliably appear in the
Time Doctor report, and optionally set an employee's department.

Why this exists (feature request be640a9d, Ikanyeng Sechele, 31 Jul 2026): five
Compliance staff were asked to be "included in the TD Omni report". Four of the
five had only an AUTO-suggested, UNCONFIRMED map row — and `TDMatcher` reads
`confirmed=True` rows ONLY (`_pass_confirmed_map`). An unconfirmed row therefore
buys nothing: the person still has to survive name matching every single run.

That is exactly where one of them was failing. Time Doctor spells her
"Galaletsang Dipitso"; her payroll row is "Galaletseng Dipitso" and a DUPLICATE
row exists as "Galeletsang Dipitso". `_pass_exact('nm')` needs an exact
normalised hit, and `_pass_token_subset` needs a token subset — "galaletsang"
matches neither spelling, so she matched no employee at all and silently dropped
out of the report.

Confirming a row ends that whole bug class for that person: the TD user_id is
stable, so the link no longer depends on how anyone spelled the name.

    python manage.py confirm_td_maps --td afsLBKeG6uY5k7wc            # dry run
    python manage.py confirm_td_maps --request be640a9d --commit      # the named batch
    python manage.py confirm_td_maps --request be640a9d --set-department --commit
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from hris.departments import fold_legacy
from integrations.models import TimeDoctorUserMap
from payroll.models import Employee

# The batch from feature request be640a9d. Keyed on the TD user_id, which is the
# stable identifier — deliberately NOT on the name, since a name is the very
# thing that was unreliable here.
REQUEST_BATCHES: dict[str, dict] = {
    'be640a9d': {
        'note': 'Sechele, 31 Jul 2026 — five Compliance staff into the TD report',
        'td_user_ids': [
            'afsLBKeG6uY5k7wc',   # Kakale Botana
            'adyreG3Tga6opAao',   # Oratile Ria Tlhomelang
            'addLRl5fmn3-3zEW',   # Galaletseng Dipitso  (TD spells it "Galaletsang")
            'addIaxW-kBQmDWUr',   # Opelo Rosemary Mokime
            'addGnUsqy_cfxdpq',   # Naomi Natasha Pheko  (already confirmed)
        ],
        # Department moves. The request itself only named Kakale explicitly and
        # listed the other three as "team members", so they were reported rather
        # than moved on an inference — a department drives leave dashboards and
        # headcount. CFO confirmed 31 Jul 2026 that all four belong in Compliance,
        # so the other three are now in the batch.
        #
        # Galaletseng (addLRl5fmn3-3zEW) is deliberately absent: she is already in
        # Compliance, and the command skips a no-op move anyway.
        'departments': {
            'afsLBKeG6uY5k7wc': 'Compliance',   # Kakale Botana — the original explicit ask
            'adyreG3Tga6opAao': 'Compliance',   # Oratile Ria Tlhomelang  (CFO confirmed)
            'addIaxW-kBQmDWUr': 'Compliance',   # Opelo Rosemary Mokime   (CFO confirmed)
            'addGnUsqy_cfxdpq': 'Compliance',   # Naomi Natasha Pheko     (CFO confirmed)
        },
    },
}


class Command(BaseCommand):
    help = 'Confirm Time Doctor ↔ Employee mappings (and optionally set a department).'

    def add_arguments(self, parser):
        parser.add_argument('--td', action='append', default=[],
                            help='A Time Doctor user id to confirm. Repeatable.')
        parser.add_argument('--request', default=None,
                            help=f'A named batch: {", ".join(REQUEST_BATCHES)}')
        parser.add_argument('--set-department', action='store_true',
                            help='Also apply the batch\'s department changes.')
        parser.add_argument('--commit', action='store_true',
                            help='Write. Without it, dry-run only.')

    def handle(self, *args, **opts):
        commit = opts['commit']
        batch = REQUEST_BATCHES.get(opts['request']) if opts['request'] else None
        if opts['request'] and batch is None:
            self.stderr.write(f'Unknown request batch "{opts["request"]}".')
            return

        td_ids = list(opts['td']) + list((batch or {}).get('td_user_ids', []))
        if not td_ids:
            self.stderr.write('Nothing to do — pass --td or --request.')
            return

        depts = (batch or {}).get('departments', {}) if opts['set_department'] else {}
        # Write the approved spelling, never a legacy one (L-DEPT).
        depts = {tid: fold_legacy(d) or d for tid, d in depts.items()}

        rows = {r.td_user_id: r for r in TimeDoctorUserMap.objects.filter(td_user_id__in=td_ids)}

        to_confirm, already, missing, unmatched = [], [], [], []
        for tid in td_ids:
            r = rows.get(tid)
            if r is None:
                missing.append(tid)
            elif r.employee_id is None:
                # Confirming a row with no employee would assert a link that does
                # not exist. That is the review queue's job, not this command's.
                unmatched.append(r)
            elif r.confirmed:
                already.append(r)
            else:
                to_confirm.append(r)

        self.stdout.write(f'Requested            : {len(td_ids)}')
        self.stdout.write(f'Already confirmed    : {len(already)}')
        for r in already:
            self.stdout.write(f'   = {r.td_name or r.td_user_id}  ->  {r.employee.full_name}')
        self.stdout.write(f'TO CONFIRM           : {len(to_confirm)}')
        for r in to_confirm:
            e = r.employee
            self.stdout.write(
                f'   + {r.td_name or r.td_user_id}  ->  {e.full_name}  '
                f'(dept={e.department or "-"}, status={e.status})'
            )
        if missing:
            self.stdout.write(self.style.WARNING(
                f'No TD map row exists for  : {missing} — these people have no Time Doctor '
                f'account on record, so nothing can be confirmed.'))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f'TD account with no employee: {[r.td_user_id for r in unmatched]} — '
                f'match them in the review queue first.'))

        if depts:
            self.stdout.write('\nDepartment changes:')
            for tid, dept in depts.items():
                r = rows.get(tid)
                if r is None or r.employee_id is None:
                    self.stdout.write(f'   ! {tid}: cannot set department — no employee linked')
                    continue
                cur = r.employee.department or '-'
                verb = 'already' if cur == dept else f'{cur}  ->  {dept}'
                self.stdout.write(f'   ~ {r.employee.full_name}: {verb}')

        if not commit:
            self.stdout.write(self.style.WARNING('\nDRY-RUN — nothing written. Re-run with --commit.'))
            return

        confirmed = moved = 0
        with transaction.atomic():
            for r in to_confirm:
                r.confirmed = True
                r.source = TimeDoctorUserMap.Source.MANUAL
                r.save(update_fields=['confirmed', 'source', 'updated_at'])
                confirmed += 1
            for tid, dept in depts.items():
                r = rows.get(tid)
                if r is None or r.employee_id is None:
                    continue
                e = r.employee
                if (e.department or '') != dept:
                    e.department = dept
                    e.save(update_fields=['department'])
                    moved += 1

        self.stdout.write(self.style.SUCCESS(
            f'Confirmed {confirmed} mapping(s); changed {moved} department(s).'))
        self.stdout.write(
            'A confirmed mapping is read first by TDMatcher, so these people no longer '
            'depend on how Time Doctor spells their name. They appear from the next pull.')
