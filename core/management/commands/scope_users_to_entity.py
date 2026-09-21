"""
scope_users_to_entity — right-size every user's HRIS/payroll entity access to
their OWN entity, so an employee in one company can never see another company's
employees, leave or salaries.

CFO directive 2026-06-16: "I don't want my employee working in Veritas to see
the salaries of people in Alpha Direct or ADRisk." The enforcement already
exists (core.mixins.CompanyScopedViewSetMixin on the payroll viewsets +
core.mixins.scoped_company_ids / apply_company_scope across the HRIS function
views). It only bites a user whose UserCompanyAccess grant is a SUBSET of all
entities — but a bulk grant had given ~every user access to all 12 entities,
so isolation was effectively off. This command makes each user's grant exactly
their own entity (deleting cross-entity grants), except a configurable
GROUP-WIDE allowlist (CFO / EXCO / Finance / central HR / auditors / admins)
who legitimately work across the whole group.

Effect per user:
  * superuser / is_administrator / CFO title         → left untouched (they
                                                        bypass UserCompanyAccess
                                                        entirely — allowed='*').
  * group-wide allowlist (email local-part / title)  → left untouched (keep
                                                        their cross-entity grants).
  * no linked employee / no company on the employee  → left untouched (service
                                                        / unpaired accounts).
  * everyone else                                    → grant own entity, REVOKE
                                                        every other entity.

Safe + reversible: re-granting is one call to the admin grant API. Run with
--dry-run first (default) to see exactly who changes.

    python manage.py scope_users_to_entity                 # dry-run report
    python manage.py scope_users_to_entity --apply         # perform
    python manage.py scope_users_to_entity --apply --only-payroll
        # only narrow users who can currently VIEW payroll (the salary-leak
        # population) — leaves ordinary employees' grants alone.

Group-wide allowlist is overridable without a deploy:
    OMNI_GROUP_WIDE_LOCAL_PARTS="pganesharajah,aiyer,kago,pako,unami,finance,excoboard,internalauditors"
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

# Central team that legitimately works across the whole group. Matched on the
# lowercased email local-part (exact OR startswith, mirroring hris_access).
DEFAULT_GROUP_WIDE = (
    'pganesharajah',     # CFO
    'aiyer',             # CEO
    'kago', 'pako',      # Finance (alias forms)
    'ktshutlhedi',       # Kago Tshutlhedi (Finance) — actual email local-part
    'pkago',             # Pako Kago (Finance)
    'bbalasubramanian',  # Bharath (Finance)
    'unami', 'ubutale',  # Unami Butale (Senior management / HR)
    'dikgopoleng',       # Dorothy Ikgopoleng (central HR)
    'tmorapedi',         # Thapelo Morapedi (central HR)
    'lntabeni',          # Legakwa Tsala Ntabeni (all-payroll viewer)
    'omogomotsi',        # Oprah Mogomotsi (all-payroll viewer / internal audit)
    'finance',           # finance@ shared mailbox
    'excoboard',         # EXCO / board mailbox
    'internalauditors',  # internal audit
    'people', 'hc',      # central HR service mailboxes
    'manus',             # automation operator
)

GROUP_WIDE_TITLES = {'cfo', 'finance_manager', 'financial_controller'}


def _local_part(email: str | None) -> str:
    if not email:
        return ''
    return (email.split('@', 1)[0] if '@' in email else email).strip().lower()


def _group_wide_set() -> set[str]:
    env = (os.environ.get('OMNI_GROUP_WIDE_LOCAL_PARTS') or '').strip()
    if env:
        return {p.strip().lower() for p in env.split(',') if p.strip()}
    return {x.lower() for x in DEFAULT_GROUP_WIDE}


class Command(BaseCommand):
    help = "Scope each user's company access to their own entity (CFO 2026-06-16)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Perform the change. Without it, dry-run only.')
        parser.add_argument('--only-payroll', action='store_true',
                            help='Only narrow users who can currently view payroll.')
        parser.add_argument('--entity', default='',
                            help='Only process users whose OWN entity is this '
                                 'Company.code (e.g. VCM). Lets you isolate one '
                                 'subsidiary at a time, safely.')

    def handle(self, *args, **opts):
        from core.models import UserCompanyAccess, Company
        try:
            from payroll.amendment_views import user_can_view_payroll
        except Exception:                                  # noqa: BLE001
            user_can_view_payroll = lambda u: False        # noqa: E731

        apply = opts['apply']
        only_payroll = opts['only_payroll']
        only_entity = (opts.get('entity') or '').strip().upper()
        group_wide = _group_wide_set()
        code_by_id = {str(c.id): c.code for c in Company.objects.all()}
        only_entity_id = ''
        if only_entity:
            c = Company.objects.filter(code__iexact=only_entity).first()
            if c is None:
                self.stderr.write(f'Unknown --entity code {only_entity!r}.')
                return
            only_entity_id = str(c.id)

        scoped, pruned_rows, skipped_group, skipped_noemp, skipped_nonpay = 0, 0, 0, 0, 0
        changes = []   # (username, entity_code, removed_codes, can_payroll)

        for u in User.objects.filter(is_active=True).select_related('profile'):
            # 1. Unrestricted bucket — leave alone (they bypass UserCompanyAccess).
            if u.is_superuser:
                skipped_group += 1
                continue
            prof = getattr(u, 'profile', None)
            if prof is not None and getattr(prof, 'is_administrator', False):
                skipped_group += 1
                continue
            if prof is not None and (getattr(prof, 'title', '') or '').lower() in GROUP_WIDE_TITLES:
                skipped_group += 1
                continue
            # 2. Group-wide allowlist (central team) — leave alone.
            local = _local_part(getattr(u, 'email', ''))
            if local and (local in group_wide or any(local.startswith(a) for a in group_wide)):
                skipped_group += 1
                continue
            # 3. Must have a linked employee WITH a company to know "own entity".
            emp = getattr(u, 'employee_record', None)
            own = str(getattr(emp, 'company_id', '') or '') if emp is not None else ''
            if not own:
                skipped_noemp += 1
                continue
            # Optional single-entity rollout (e.g. isolate Veritas first).
            if only_entity_id and own != only_entity_id:
                continue
            # 4. Optionally only narrow the salary-leak population.
            can_pay = bool(user_can_view_payroll(u))
            if only_payroll and not can_pay:
                skipped_nonpay += 1
                continue

            existing = list(UserCompanyAccess.objects.filter(user=u))
            other = [g for g in existing if str(g.company_id) != own]
            has_own = any(str(g.company_id) == own and g.can_view for g in existing)
            if not other and has_own:
                continue   # already correctly scoped (e.g. Lakshmi → ADRG)

            removed = sorted({code_by_id.get(str(g.company_id), str(g.company_id)) for g in other})
            changes.append((u.username, code_by_id.get(own, own), removed, can_pay))
            if apply:
                for g in other:
                    g.delete()
                    pruned_rows += 1
                UserCompanyAccess.objects.update_or_create(
                    user=u, company_id=own,
                    defaults={'can_view': True},
                )
            else:
                pruned_rows += len(other)
            scoped += 1

        mode = 'APPLIED' if apply else 'DRY-RUN'
        self.stdout.write(f'\n=== scope_users_to_entity [{mode}]'
                          + (' (payroll-capable only)' if only_payroll else '') + ' ===')
        self.stdout.write(f'users scoped to own entity : {scoped}')
        self.stdout.write(f'cross-entity grants removed: {pruned_rows}')
        self.stdout.write(f'group-wide left untouched  : {skipped_group}')
        self.stdout.write(f'no employee/company        : {skipped_noemp}')
        if only_payroll:
            self.stdout.write(f'non-payroll skipped        : {skipped_nonpay}')
        # Surface the salary-relevant changes explicitly.
        pay_changes = [c for c in changes if c[3]]
        self.stdout.write(f'\n-- payroll-capable users being narrowed ({len(pay_changes)}) --')
        for un, ent, removed, _ in pay_changes[:60]:
            self.stdout.write(f'   {un:32s} -> {ent:6s}  (was also: {",".join(removed) or "—"})')
        if len(pay_changes) > 60:
            self.stdout.write(f'   … +{len(pay_changes) - 60} more')
        self.stdout.write(f'\n-- sample of all changes ({min(len(changes),20)} of {len(changes)}) --')
        for un, ent, removed, cp in changes[:20]:
            self.stdout.write(f'   {un:32s} -> {ent:6s}  payroll={cp}  -{len(removed)} others')
        if not apply:
            self.stdout.write('\n(dry-run — re-run with --apply to perform)')
