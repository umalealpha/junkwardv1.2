"""
apply_hris_amendments_bulk — apply many HRIS field changes in one run instead
of one-by-one through the Amendments screen.

CFO directive 2026-07-14: Dorothy was applying amendments manually, one record
at a time (see the "HRIS amendment approved" emails). "Don't do it manually,
I can do this faster using Claude — send me data." This command is the fast
path: Dorothy (or HR) sends a plain list of changes; it's turned into JSON
off-server (no need to land a raw spreadsheet with DOB/national ID/etc. on
the box — same discipline as import_employee_titles_bands.py) and applied
here in one pass.

Every row goes through hris.amendment_service.submit_amendment — the EXACT
same call the Amendments screen makes. That means: the same field whitelist,
the same who-can-amend-whom scope checks (BUG 16d631ce), the same audit
trail, and the same self-apply rule (Dorothy/Unami self-apply immediately,
per CFO 2026-06-25 — this tool does NOT grant any new power, it just avoids
the CFO/Dorothy re-typing each change into the UI).

Input JSON:
  {"maker": "dikgopoleng", "rows": [
    {"employee": "Bontle Precious Tendani", "field": "phone", "value": "74251623"},
    {"employee": "Bontle Precious Tendani", "field": "contract_type", "value": "fixed_term"}
  ]}

`employee` matches by email, employee number, or full name (same tolerant
order as the leave-opening-balance uploader). `field` is looked up against
BOTH the Employee and HRISProfile amendment registries automatically — no
need to say which target it belongs to.

  python manage.py apply_hris_amendments_bulk batch.json            # dry run
  python manage.py apply_hris_amendments_bulk batch.json --commit
"""
from __future__ import annotations

import json
import re

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hris.amendment_models import HRISAmendment
from hris.amendment_service import _registry, submit_amendment
from payroll.models import Employee


def _norm_name(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def _first_last(s: str):
    parts = _norm_name(s).split(' ')
    return (parts[0], parts[-1]) if len(parts) >= 2 else None


def _build_index():
    by_norm, by_fl, by_email, by_num = {}, {}, {}, {}
    for e in Employee.objects.all().only('id', 'full_name', 'email', 'employee_number'):
        by_norm.setdefault(_norm_name(e.full_name), []).append(e)
        fl = _first_last(e.full_name)
        if fl:
            by_fl.setdefault(fl, []).append(e)
        if e.email:
            by_email[e.email.strip().lower()] = e
        if e.employee_number:
            by_num[str(e.employee_number).strip().lower()] = e
    return by_norm, by_fl, by_email, by_num


def _match_employee(ref, idx):
    by_norm, by_fl, by_email, by_num = idx
    s = str(ref or '').strip()
    if not s:
        return None, 'cell is blank'
    low = s.lower()
    if low in by_email:
        return by_email[low], None
    if low in by_num:
        return by_num[low], None
    cands = by_norm.get(_norm_name(s))
    if cands:
        if len(cands) == 1:
            return cands[0], None
        return None, f'{s!r} matches {len(cands)} employees — use email or employee number'
    fl = _first_last(s)
    if fl:
        cands = by_fl.get(fl)
        if cands and len(cands) == 1:
            return cands[0], None
        if cands:
            return None, f'{s!r} matches {len(cands)} employees — use email or employee number'
    return None, f'{s!r} not found — check the spelling against HRIS records'


def _resolve_field(field: str):
    """Find which amendment target (employee/profile) owns `field`. Returns
    (target_kind, target_id_for(employee)) resolver or (None, None) if unknown."""
    field = (field or '').strip()
    for target_kind, spec in _registry().items():
        if field in spec['fields']:
            return target_kind, field
    return None, None


class Command(BaseCommand):
    help = 'Apply a batch of HRIS amendments from JSON (dry-run unless --commit).'

    def add_arguments(self, parser):
        parser.add_argument('json_file')
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        w = self.stdout.write

        with open(opts['json_file']) as f:
            payload = json.load(f)

        maker_ident = (payload.get('maker') or '').strip()
        maker = (User.objects.filter(username__iexact=maker_ident).first()
                 or User.objects.filter(email__iexact=maker_ident).first())
        if maker is None:
            raise CommandError(f'maker {maker_ident!r} not found — expected a username or email.')

        rows = payload.get('rows') or []
        idx = _build_index()
        applied = errors = 0

        for i, row in enumerate(rows, start=1):
            emp_ref = row.get('employee')
            field = row.get('field')
            value = row.get('value')

            emp, err = _match_employee(emp_ref, idx)
            if emp is None:
                w(f'  ROW {i}: SKIP — {err}')
                errors += 1
                continue

            target_kind, resolved_field = _resolve_field(field)
            if target_kind is None:
                w(f'  ROW {i}: SKIP — unknown field {field!r} (not in the amendment registry)')
                errors += 1
                continue

            # PROFILE-target amendments key off the HRISProfile pk, not Employee.
            if target_kind == HRISAmendment.Target.PROFILE:
                profile = getattr(emp, 'hris_profile', None)
                if profile is None:
                    w(f'  ROW {i}: SKIP — {emp.full_name} has no HRISProfile yet')
                    errors += 1
                    continue
                target_id = str(profile.pk)
            else:
                target_id = str(emp.pk)

            label = f'{emp.full_name} · {resolved_field} -> {value}'
            if not commit:
                w(f'  ROW {i}: WOULD APPLY — {label}')
                applied += 1
                continue

            try:
                with transaction.atomic():
                    submit_amendment(
                        maker=maker, target_kind=target_kind, target_id=target_id,
                        proposed={resolved_field: value},
                        reason='Bulk-applied via apply_hris_amendments_bulk (CFO 2026-07-14).',
                    )
                w(f'  ROW {i}: APPLIED — {label}')
                applied += 1
            except ValidationError as exc:
                w(f'  ROW {i}: REJECTED — {label} ({exc})')
                errors += 1

        mode = 'APPLIED' if commit else 'DRY-RUN'
        w(f'\n=== apply_hris_amendments_bulk [{mode}] ===')
        w(f'rows applied : {applied}')
        w(f'rows skipped : {errors}')
        if not commit:
            w('(dry-run — re-run with --commit to perform)')
