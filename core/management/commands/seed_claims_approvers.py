"""
seed_claims_approvers — PIN the claims-PO approver titles so they stop drifting.

CFO directive 2026-07-11: "lock this instruction regarding PO approvals." Claims
POs are approved by the claims seniors (Claims Manager / Claims Team Leader /
Senior Claims Associate) — see procurement.services._can_approve_po. Those titles
live on core.UserProfile.title, which is set per-user by hand and DRIFTS:
  * SSO first-login provisions a new profile with the default title ACCOUNTANT
    (core.azure_auth) — a recreated/duplicate account is NOT an approver;
  * duplicate accounts (e.g. Bonang Lentswe) leave the claims title on one login
    while the person signs in with another;
  * nothing re-asserts the intended titles on deploy.

This command re-applies the intended titles every time it runs. The entrypoint
calls it on every deploy, so the approver set self-heals and can't silently
revert. Idempotent + SAFE: it only touches the listed accounts, only when the
account's name matches the expected person (guards against pinning a wrong
same-username account), and never downgrades anyone else.

  python manage.py seed_claims_approvers            # dry-run
  python manage.py seed_claims_approvers --commit

Edit ROSTER below to add/remove a claims approver (the single source of truth).
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.models import Company, UserCompanyAccess, UserProfile

# identifier (username OR email, case-insensitive) -> (expected name substring, Title)
ROSTER = [
    ('wangu.moses',                'moses',      UserProfile.Title.CLAIMS_MANAGER),
    ('segolame.masilo',            'masilo',     UserProfile.Title.CLAIMS_TEAM_LEADER),
    ('kelebogile.gaothobogwe',     'gaothobog',  UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE),
    ('blentswe@alphadirect.co.bw', 'lentswe',    UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE),
    ('bonang.lentswe',             'lentswe',    UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE),
]


class Command(BaseCommand):
    help = 'Pin claims-PO approver titles (self-heals every deploy). Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        w = self.stdout.write
        changed = matched = 0
        for ident, name_frag, title in ROSTER:
            users = list(User.objects.filter(
                Q(username__iexact=ident) | Q(email__iexact=ident)).distinct())
            if not users:
                w(f'  MISSING account: {ident}')
                continue
            for u in users:
                full = f'{u.first_name} {u.last_name} {u.username}'.lower()
                if name_frag.lower() not in full:
                    w(f'  SKIP {ident}: name guard failed (got {u.username}/{u.get_full_name()!r})')
                    continue
                matched += 1
                prof = getattr(u, 'profile', None) or UserProfile.objects.filter(user=u).first()
                if prof is None:
                    w(f'  {ident}: no UserProfile — skipped (login provisions it)')
                    continue
                if prof.title == title:
                    w(f'  ok    {u.username:26} already {title}')
                else:
                    w(f'  {"SET " if commit else "WOULD SET "}{u.username:26} {prof.title} -> {title}')
                    if commit:
                        prof.title = title
                        prof.save(update_fields=['title', 'updated_at'])
                        changed += 1
                # Company access pin (CFO 2026-07-13, after Bonang's active
                # blentswe@ login had ZERO UserCompanyAccess rows — the
                # CompanyScopedViewSetMixin then hides every PO, so the PO
                # tab "won't open" even though her approver title was fine).
                # An ACTIVE roster account with no access at all gets
                # view-only rows for every active company, mirroring the
                # rest of the claims team. Existing rows are never touched,
                # so an explicit narrower grant survives.
                if u.is_active and not UserCompanyAccess.objects.filter(user=u).exists():
                    cos = list(Company.objects.filter(is_active=True))
                    w(f'  {"GRANT " if commit else "WOULD GRANT "}{u.username:26} '
                      f'view access to {len(cos)} companies (had none)')
                    if commit:
                        for co in cos:
                            UserCompanyAccess.objects.get_or_create(
                                user=u, company=co,
                                defaults={'can_view': True, 'can_write': False,
                                          'notes': 'auto: seed_claims_approvers zero-access heal'},
                            )
                        changed += 1
        if commit:
            w(self.style.SUCCESS(f'COMMITTED: {changed} title(s) pinned ({matched} accounts checked).'))
        else:
            w(self.style.WARNING(f'DRY RUN — {matched} accounts checked. Re-run with --commit.'))
