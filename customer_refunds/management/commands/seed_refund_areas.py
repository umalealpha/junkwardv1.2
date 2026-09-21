"""
seed_refund_areas — assign staff to the two refund AREAS.

MIS (UniCoin) and Domestic & Commercial are managed by different people
(CFO 2026-07-24). UniCoin staff must NOT see D&C refunds and vice versa.

  refund_area_mis : Phatsimo, Motlatsi, Bharath  (UniCoin)
  refund_area_dc  : Keetile, Tlamelo, Bontle, Kago, Pako  (Finance)

Idempotent. Matches users by username / email / first+last / profile name
(case-insensitive, substring). Reports who was matched and who was NOT found —
never guesses. Dry-run by default; pass --commit to write.
"""
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db.models import Q

# Reviewer groups (approve/reject/escalate) and inputter groups (raise/view only).
# CFO decision 2026-07-27: Motlatsi = MIS reviewer; Keetile = MIS + D&C reviewer
# (both books, NOT administrator); Phatsimo = MIS inputter; Bharath = MIS reviewer
# (escalation). The two Unicoin "Bakang" reviewers are PENDING CFO confirmation of
# the exact people + reporting lines — do NOT seed them here.
AREAS = {
    'refund_area_mis':  ['Motlatsi', 'Bharath', 'Keetile'],
    'refund_area_dc':   ['Keetile', 'Tlamelo', 'Bontle', 'Kago', 'Pako'],
    'refund_input_mis': ['Phatsimo'],
    'refund_input_dc':  [],
    # Money authority for the refund leg (send to FNB, >P50k approve, fraud
    # override, manual mark-paid) — CFO decision 2026-07-27. Scoped to refunds,
    # does NOT widen the general FNB gate. (Kago/Pako are ambiguous by name —
    # assign explicitly: ktshutlhedi, pkago.)
    'refund_money':     ['Keetile', 'Kago', 'Pako'],
}


def _find(hint: str):
    """Best-effort single-user match for a first-name hint."""
    qs = User.objects.filter(
        Q(username__icontains=hint) | Q(email__icontains=hint) |
        Q(first_name__icontains=hint) | Q(last_name__icontains=hint)
    ).distinct()
    return list(qs)


class Command(BaseCommand):
    help = 'Assign staff to refund_area_mis / refund_area_dc groups.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write (default is dry-run).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        for group_name, hints in AREAS.items():
            group, _ = Group.objects.get_or_create(name=group_name)
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n{group_name}:'))
            for hint in hints:
                matches = _find(hint)
                if not matches:
                    self.stdout.write(f'  ? {hint:10s} — NOT FOUND (skipped)')
                    continue
                if len(matches) > 1:
                    who = ', '.join(u.username for u in matches)
                    self.stdout.write(self.style.WARNING(
                        f'  ! {hint:10s} — {len(matches)} matches ({who}); '
                        'skipped, resolve manually'))
                    continue
                u = matches[0]
                if commit:
                    u.groups.add(group)
                self.stdout.write(self.style.SUCCESS(
                    f'  {"+" if commit else "·"} {hint:10s} → {u.username}'))
        if not commit:
            self.stdout.write('\nDry-run only. Re-run with --commit to apply.')
