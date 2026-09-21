"""Setup voucher-clearing maker/approver groups + assign the three operators.

CFO directive 2026-05-26 — duplicate JE postings overstated cash by ~BWP 10M.
The fix is a maker-checker queue at /banking/voucher-clearing. Pako Kago and
Legakwa Ntabeni are the makers; Kago Tshutlhedi is the sole approver.

Idempotent: re-running this command only inserts what is missing.

Usage (from inside the backend container):

    python manage.py setup_voucher_clearing_roles
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction


MAKER_GROUP    = 'voucher_clearing_maker'
APPROVER_GROUP = 'voucher_clearing_approver'

# Matched against username, then against email local-part, then against
# first_name + last_name pairs. Whichever resolves first wins.
MAKERS = [
    {'label': 'Pako Kago',         'hints': ['pako.kago', 'pkago', 'pako', 'kago.p']},
    {'label': 'Legakwa Ntabeni',   'hints': ['legakwa.ntabeni', 'lntabeni', 'legakwa']},
]
APPROVERS = [
    {'label': 'Kago Tshutlhedi',   'hints': ['kago.tshutlhedi', 'ktshutlhedi', 'tshutlhedi']},
]


def resolve_user(hints, User):
    """Resolve a user by username, then email local-part, then first/last."""
    for h in hints:
        # Exact username
        u = User.objects.filter(username__iexact=h).first()
        if u:
            return u
        # Email local-part (anything@... where local matches)
        u = User.objects.filter(email__istartswith=f'{h}@').first()
        if u:
            return u
        # first.last → first / last
        if '.' in h:
            first, last = h.split('.', 1)
            u = (
                User.objects.filter(first_name__iexact=first, last_name__iexact=last).first()
                or User.objects.filter(username__icontains=last).first()
            )
            if u:
                return u
    return None


def grant_cfo_upload(user, stdout, style):
    """Idempotently grant the `cfo-upload` permission to ``user``.

    The CFO-upload endpoints check ``user_has_permission(user, 'cfo-upload')``
    via the RBAC role/permission chain. We do not have a stable role name to
    re-use, so the command creates a dedicated role —
    ``voucher_clearing_uploader`` — once, attaches the permission to it, and
    assigns the user to that role.

    "Active" semantics on ``UserRoleAssignment``: revoked_at IS NULL AND
    (expires_at IS NULL OR expires_at > now). There is no ``is_active``
    column — that field lives on ``Role`` itself.

    Safe to call repeatedly: ``get_or_create`` on every layer and the
    assignment lookup only inserts when no live row exists.
    """
    from django.db.models import Q
    from django.utils import timezone

    from core.models import Permission, Role, UserRoleAssignment

    perm, _ = Permission.objects.get_or_create(
        code='cfo-upload',
        defaults={
            'name': 'CFO-upload',
            'description': 'Write access to the bulk CFO upload endpoints '
                           '(TB / CoA / GL). Granted to voucher-clearing '
                           'makers so they can re-upload after a wipe.',
        },
    )
    role, role_created = Role.objects.get_or_create(
        name='voucher_clearing_uploader',
        defaults={
            'description': (
                'Auto-created by setup_voucher_clearing_roles. Grants the '
                'voucher-clearing makers permission to re-upload TB/GL after '
                'an approved wipe.'
            ),
            'level': 5,
        },
    )
    role.permissions.add(perm)
    if role_created:
        stdout.write(style.SUCCESS('  role voucher_clearing_uploader created'))

    now = timezone.now()
    existing = UserRoleAssignment.objects.filter(
        user=user, role=role, revoked_at__isnull=True,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
    ).exists()
    if existing:
        stdout.write(f'  · {user.username} already holds voucher_clearing_uploader')
        return False
    UserRoleAssignment.objects.create(
        user=user, role=role,
        justification=(
            'Auto-grant via setup_voucher_clearing_roles '
            '(CFO directive 2026-05-26 — voucher-clearing maker needs '
            'cfo-upload to re-upload TB after an approved wipe).'
        ),
    )
    stdout.write(style.SUCCESS(
        f'  + assigned {user.username} → voucher_clearing_uploader (grants cfo-upload)'
    ))
    return True


class Command(BaseCommand):
    help = 'Create voucher-clearing groups and assign Pako, Legakwa, Kago.'

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()

        maker_grp, m_created = Group.objects.get_or_create(name=MAKER_GROUP)
        appr_grp,  a_created = Group.objects.get_or_create(name=APPROVER_GROUP)
        self.stdout.write(
            f'  group {MAKER_GROUP}   {"created" if m_created else "exists"}'
        )
        self.stdout.write(
            f'  group {APPROVER_GROUP} {"created" if a_created else "exists"}'
        )

        added_makers = 0
        granted_uploaders = 0
        for spec in MAKERS:
            u = resolve_user(spec['hints'], User)
            if not u:
                self.stdout.write(self.style.WARNING(
                    f'  MAKER "{spec["label"]}" — no user matched (hints: {spec["hints"]})'
                ))
                continue
            if not u.groups.filter(name=MAKER_GROUP).exists():
                u.groups.add(maker_grp)
                added_makers += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  + maker: {u.username} ({u.email}) → {MAKER_GROUP}'
                ))
            else:
                self.stdout.write(f'  · maker {u.username} already in {MAKER_GROUP}')
            # CFO directive 2026-05-26 — makers also need the cfo-upload
            # permission so they can re-upload the TB / GL after a wipe.
            try:
                if grant_cfo_upload(u, self.stdout, self.style):
                    granted_uploaders += 1
            except Exception as exc:                       # noqa: BLE001
                self.stdout.write(self.style.WARNING(
                    f'  · cfo-upload grant skipped for {u.username}: {exc}'
                ))

        added_approvers = 0
        for spec in APPROVERS:
            u = resolve_user(spec['hints'], User)
            if not u:
                self.stdout.write(self.style.WARNING(
                    f'  APPROVER "{spec["label"]}" — no user matched (hints: {spec["hints"]})'
                ))
                continue
            if not u.groups.filter(name=APPROVER_GROUP).exists():
                u.groups.add(appr_grp)
                added_approvers += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  + approver: {u.username} ({u.email}) → {APPROVER_GROUP}'
                ))
            else:
                self.stdout.write(f'  · approver {u.username} already in {APPROVER_GROUP}')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {added_makers} new maker(s), {added_approvers} new approver(s), '
            f'{granted_uploaders} new uploader assignment(s).'
        ))
