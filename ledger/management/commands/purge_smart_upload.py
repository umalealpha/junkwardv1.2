"""
ledger/management/commands/purge_smart_upload.py

CFO directive 2026-05-19 (extended) — clean up duplicate / bad smart-upload
records before `mode=replace` was wired into every committer.

USAGE
-----
Dry-run (default) — show what *would* be deleted:

    python manage.py purge_smart_upload --section tb --target dupes --keep newest

Commit:

    python manage.py purge_smart_upload --section tb --target dupes --keep newest --commit

Sections supported:
    tb         JournalEntry rows with source_type='smart_upload_tb'
               key = (company, entry_date)
    gl         JournalEntry rows with source_type='smart_upload_gl'
               key = (company, entry_date, entry_number)
    ppe        Asset rows where external_ref starts with 'smart_upload:ppe:'
               key = (company, tag_number)
    vendors    Contact rows where external_ref starts with 'smart_upload:vendor:'
               key = (company, external_ref)
    customers  Contact rows where external_ref starts with 'smart_upload:customer:'
               key = (company, external_ref)
    employees  Employee rows where external_ref starts with 'smart_upload:employees:'
               key = (company, employee_number)
    payroll    Payslip rows (per company per period)

Targets:
    --target dupes      keep one per natural key, delete the rest
    --target specific   take --pair COMPANY:KEY (repeatable) — only for tb/gl
                        where KEY = YYYY-MM-DD (entry_date).
    --target company    --pair COMPANY (no colon needed; date ignored)
                        nukes everything smart-upload created for that company
                        in this section.
    --target all        nukes every row this section knows about. Use with care.

--keep newest|oldest|none
    Only meaningful for dupes. Defaults to newest.

--commit
    Required to actually delete; otherwise dry-run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


SECTIONS = ('tb', 'gl', 'ppe', 'vendors', 'customers', 'employees', 'payroll')


def _parse_pair(s: str, allow_no_date: bool = False) -> tuple[str, date | None]:
    if ':' not in s:
        if not allow_no_date:
            raise CommandError(
                f'Bad --pair {s!r}; expected COMPANY:YYYY-MM-DD.'
            )
        return s.strip().upper(), None
    code, key = s.split(':', 1)
    code = code.strip().upper()
    key = key.strip()
    try:
        return code, date.fromisoformat(key)
    except ValueError:
        return code, key  # opaque key, section-specific


def _je_rows(source_type):
    from ledger.models import JournalEntry
    return JournalEntry.objects.filter(source_type=source_type).select_related('company')


class Command(BaseCommand):
    help = (
        'Purge smart-upload-created records across sections '
        '(CFO directive 2026-05-19).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--section', required=True, choices=SECTIONS,
                            help='Which section to clean.')
        parser.add_argument('--target', choices=('dupes', 'specific', 'company', 'all'),
                            default='dupes')
        parser.add_argument('--keep', choices=('newest', 'oldest', 'none'),
                            default='newest')
        parser.add_argument('--pair', action='append', default=[],
                            help='Repeatable. tb/gl: COMPANY:YYYY-MM-DD. '
                                 'others: COMPANY or COMPANY:KEY.')
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        section = opts['section']
        target  = opts['target']
        keep    = opts['keep']
        commit  = opts['commit']
        pairs   = opts['pair']

        if section == 'tb':
            return self._handle_je(opts, source_type='smart_upload_tb')
        if section == 'gl':
            return self._handle_je(opts, source_type='smart_upload_gl')
        if section == 'ppe':
            return self._handle_external_ref(
                opts, model_path='assets.models.Asset',
                ref_prefix='smart_upload:ppe:',
                key_attr='tag_number',
            )
        if section == 'vendors':
            return self._handle_external_ref(
                opts, model_path='billing.models.Contact',
                ref_prefix='smart_upload:vendor:',
                key_attr='external_ref',
            )
        if section == 'customers':
            return self._handle_external_ref(
                opts, model_path='billing.models.Contact',
                ref_prefix='smart_upload:customer:',
                key_attr='external_ref',
            )
        if section == 'employees':
            return self._handle_external_ref(
                opts, model_path='payroll.models.Employee',
                ref_prefix='smart_upload:employees:',
                key_attr='employee_number',
            )
        if section == 'payroll':
            return self._handle_payroll(opts)

    # ------------------------------------------------------------------
    # JE-backed sections (tb / gl)
    # ------------------------------------------------------------------
    def _handle_je(self, opts, *, source_type):
        target, keep, commit = opts['target'], opts['keep'], opts['commit']
        qs = _je_rows(source_type)

        buckets: dict[tuple[str, date], list] = defaultdict(list)
        for je in qs.order_by('id'):
            buckets[(je.company.code, je.entry_date)].append(je)

        if target == 'all':
            scope = list(buckets.items())
        elif target == 'dupes':
            scope = [(k, v) for k, v in buckets.items() if len(v) > 1]
        elif target == 'company':
            if not opts['pair']:
                raise CommandError('--target company requires --pair COMPANY.')
            wanted = {_parse_pair(p, allow_no_date=True)[0] for p in opts['pair']}
            scope = [(k, v) for k, v in buckets.items() if k[0] in wanted]
        elif target == 'specific':
            pairs = [_parse_pair(p) for p in opts['pair']]
            wanted = {(c, d) for c, d in pairs if isinstance(d, date)}
            scope = [(k, v) for k, v in buckets.items() if k in wanted]
        else:
            raise CommandError(f'Unknown --target {target!r}.')

        to_delete, to_keep, lines = [], [], []
        for (code, d), jes in sorted(scope):
            jes_sorted = sorted(jes, key=lambda j: j.id)
            if target in ('all', 'company') or keep == 'none':
                delete, kept = jes_sorted, []
            elif len(jes_sorted) == 1:
                delete, kept = [], jes_sorted
            elif keep == 'newest':
                delete, kept = jes_sorted[:-1], [jes_sorted[-1]]
            else:
                delete, kept = jes_sorted[1:], [jes_sorted[0]]
            for je in delete:
                to_delete.append(je.id)
                lines.append(f'  DEL  {code:8} {d}  id={str(je.id)}  '
                             f'number={je.entry_number}  lines={je.lines.count()}')
            for je in kept:
                to_keep.append(je.id)
                lines.append(f'  KEEP {code:8} {d}  id={str(je.id)}  '
                             f'number={je.entry_number}')

        self._report(opts, source_type, lines, to_delete, to_keep)

        if to_delete and commit:
            with transaction.atomic():
                deleted, breakdown = qs.filter(id__in=to_delete).delete()
                self.stdout.write(self.style.SUCCESS(
                    f'DELETED {deleted} object(s). breakdown={breakdown}'
                ))

    # ------------------------------------------------------------------
    # External-ref-backed sections (ppe / vendors / customers / employees)
    # ------------------------------------------------------------------
    def _handle_external_ref(self, opts, *, model_path, ref_prefix, key_attr):
        target, keep, commit = opts['target'], opts['keep'], opts['commit']
        mod_name, cls_name = model_path.rsplit('.', 1)
        Model = getattr(__import__(mod_name, fromlist=[cls_name]), cls_name)

        qs = Model.objects.filter(external_ref__startswith=ref_prefix)
        if hasattr(Model, 'company'):
            qs = qs.select_related('company')

        # bucket = (company.code, natural key)
        buckets: dict[tuple[str, str], list] = defaultdict(list)
        for obj in qs.order_by('id' if hasattr(Model, 'id') else 'pk'):
            comp = obj.company.code if getattr(obj, 'company', None) else 'NONE'
            buckets[(comp, getattr(obj, key_attr) or '')].append(obj)

        if target == 'all':
            scope = list(buckets.items())
        elif target == 'dupes':
            scope = [(k, v) for k, v in buckets.items() if len(v) > 1]
        elif target == 'company':
            if not opts['pair']:
                raise CommandError('--target company requires --pair COMPANY.')
            wanted = {_parse_pair(p, allow_no_date=True)[0] for p in opts['pair']}
            scope = [(k, v) for k, v in buckets.items() if k[0] in wanted]
        elif target == 'specific':
            wanted = set()
            for p in opts['pair']:
                code, key = _parse_pair(p, allow_no_date=True)
                wanted.add((code, key or ''))
            scope = [(k, v) for k, v in buckets.items() if k in wanted]
        else:
            raise CommandError(f'Unknown --target {target!r}.')

        to_delete, to_keep, lines = [], [], []
        for (code, key), objs in sorted(scope):
            objs_sorted = sorted(objs, key=lambda o: o.pk)
            if target in ('all', 'company') or keep == 'none':
                delete, kept = objs_sorted, []
            elif len(objs_sorted) == 1:
                delete, kept = [], objs_sorted
            elif keep == 'newest':
                delete, kept = objs_sorted[:-1], [objs_sorted[-1]]
            else:
                delete, kept = objs_sorted[1:], [objs_sorted[0]]
            for o in delete:
                to_delete.append(o.pk)
                lines.append(f'  DEL  {code:8} {key!s:30}  pk={str(o.pk)}')
            for o in kept:
                to_keep.append(o.pk)
                lines.append(f'  KEEP {code:8} {key!s:30}  pk={str(o.pk)}')

        self._report(opts, model_path, lines, to_delete, to_keep)
        if to_delete and commit:
            with transaction.atomic():
                deleted, breakdown = Model.objects.filter(pk__in=to_delete).delete()
                self.stdout.write(self.style.SUCCESS(
                    f'DELETED {deleted} object(s). breakdown={breakdown}'
                ))

    # ------------------------------------------------------------------
    # Payroll
    # ------------------------------------------------------------------
    def _handle_payroll(self, opts):
        from payroll.models import Payslip
        target, keep, commit = opts['target'], opts['keep'], opts['commit']
        qs = Payslip.objects.select_related('company', 'employee', 'period')

        buckets: dict[tuple[str, str, str], list] = defaultdict(list)
        for ps in qs.order_by('id'):
            comp = ps.company.code if ps.company else 'NONE'
            emp  = ps.employee.employee_number if ps.employee else 'NONE'
            per  = ps.period.period_name if ps.period else 'NONE'
            buckets[(comp, per, emp)].append(ps)

        if target == 'all':
            scope = list(buckets.items())
        elif target == 'dupes':
            scope = [(k, v) for k, v in buckets.items() if len(v) > 1]
        elif target == 'company':
            if not opts['pair']:
                raise CommandError('--target company requires --pair COMPANY[:PERIOD].')
            wanted = {_parse_pair(p, allow_no_date=True) for p in opts['pair']}
            scope = []
            for k, v in buckets.items():
                comp, per, emp = k
                for w_comp, w_per in wanted:
                    if comp == w_comp and (w_per in (None, '', per)):
                        scope.append((k, v))
                        break
        else:
            raise CommandError(f'--target {target!r} not supported for payroll.')

        to_delete, to_keep, lines = [], [], []
        for (comp, per, emp), pss in sorted(scope):
            pss_sorted = sorted(pss, key=lambda p: p.id)
            if target in ('all', 'company') or keep == 'none':
                delete, kept = pss_sorted, []
            elif len(pss_sorted) == 1:
                delete, kept = [], pss_sorted
            elif keep == 'newest':
                delete, kept = pss_sorted[:-1], [pss_sorted[-1]]
            else:
                delete, kept = pss_sorted[1:], [pss_sorted[0]]
            for ps in delete:
                to_delete.append(ps.id)
                lines.append(f'  DEL  {comp:8} {per:10} emp={emp}  id={str(ps.id)}')
            for ps in kept:
                to_keep.append(ps.id)
                lines.append(f'  KEEP {comp:8} {per:10} emp={emp}  id={str(ps.id)}')

        self._report(opts, 'payroll', lines, to_delete, to_keep)
        if to_delete and commit:
            with transaction.atomic():
                deleted, breakdown = Payslip.objects.filter(id__in=to_delete).delete()
                self.stdout.write(self.style.SUCCESS(
                    f'DELETED {deleted} object(s). breakdown={breakdown}'
                ))

    # ------------------------------------------------------------------
    # Report helper
    # ------------------------------------------------------------------
    def _report(self, opts, scope_label, lines, to_delete, to_keep):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'purge_smart_upload section={opts["section"]} scope={scope_label} '
            f'target={opts["target"]} keep={opts["keep"]} commit={opts["commit"]}'
        ))
        for line in lines:
            self.stdout.write(line)
        self.stdout.write(f'TOTAL_PURGED={len(to_delete)}')
        self.stdout.write(f'TOTAL_KEPT={len(to_keep)}')
        if to_delete and not opts['commit']:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — pass --commit to actually delete.'
            ))
        if not to_delete:
            self.stdout.write(self.style.SUCCESS('Nothing to delete.'))
