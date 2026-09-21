"""
seed_pinned_company_access — PIN named individuals' cross-entity access so it
self-heals every deploy and can't be silently pruned by a future entity sweep.

CFO directive 2026-07-14: Lemogang Machola (Unicoin, Junior Associate — Parts &
Assessment) does the assessment work behind ADIC's Claims PO / Assessments
module, but scope_users_to_entity (his home-entity fix, same day) correctly
narrowed him to Unicoin only — so he lost the ADIC access his actual job
needs. "He is a key person and must always have access for purchase orders."

This is the same self-healing idiom as seed_claims_approvers.py (run on every
deploy via entrypoint.sh) but for raw UserCompanyAccess grants rather than the
approver title — it does NOT grant approval authority, only the ability to
see/use the named companies' records. Additive only: never removes a grant a
person already has, and never touches anyone not explicitly listed here.

  python manage.py seed_pinned_company_access            # dry-run
  python manage.py seed_pinned_company_access --commit

Edit ROSTER below to add/remove a pinned grant (the single source of truth).
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Company, UserCompanyAccess

# identifier (username OR email, case-insensitive) -> (expected name substring,
# [company codes to always keep], can_write)
ROSTER = [
    ('lemogang.machola', 'machola', ['ADIC'], True),
]


class Command(BaseCommand):
    help = 'Pin named individuals\' cross-entity access (self-heals every deploy). Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        w = self.stdout.write
        granted = matched = 0
        for ident, name_frag, codes, can_write in ROSTER:
            users = list(User.objects.filter(
                Q(username__iexact=ident) | Q(email__iexact=ident)).distinct())
            if not users:
                w(f'  MISSING account: {ident}')
                continue
            for u in users:
                full = f'{u.first_name} {u.last_name} {u.username}'.lower()
                if name_frag.lower() not in full:
                    w(f'  SKIP {u.username} — name does not contain {name_frag!r} (wrong account?)')
                    continue
                matched += 1
                for code in codes:
                    company = Company.objects.filter(code=code).first()
                    if company is None:
                        w(f'  UNKNOWN company code: {code}')
                        continue
                    existing = UserCompanyAccess.objects.filter(user=u, company=company).first()
                    if existing and existing.can_view and (existing.can_write or not can_write):
                        continue   # already has at least what we'd pin
                    granted += 1
                    w(f'  {"WOULD GRANT" if not commit else "GRANTED"} {u.username} -> {code} '
                      f'(view=True write={can_write})')
                    if commit:
                        UserCompanyAccess.objects.update_or_create(
                            user=u, company=company,
                            defaults={'can_view': True,
                                      'can_write': can_write or (existing.can_write if existing else False),
                                      'notes': 'auto: pinned access (seed_pinned_company_access)'},
                        )

        mode = 'APPLIED' if commit else 'DRY-RUN'
        w(f'\n=== seed_pinned_company_access [{mode}] ===')
        w(f'roster entries matched : {matched}')
        w(f'grants added           : {granted}')
        if not commit:
            w('(dry-run — re-run with --commit to perform)')
