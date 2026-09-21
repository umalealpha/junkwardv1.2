"""Advance any leave-encashment stuck at the CFO leg where the applicant IS the
CFO onto the HR leg.

The CFO's own encashment cannot be signed at the CFO leg — the applicant may
never approve their own request (segregation of duties), and the only other
CFO-level login is a backup super-admin. If that backup is unavailable the
request freezes with nobody able to move it (the 2026-08-24 incident). From now
on `apply_encashment` skips the self-leg for CFO applicants; this one-off moves
the request(s) that were created before the fix onto the HR leg so they resume.

Data-only (RunPython). No schema change. The applicant stays fully excluded and
the two independent legs below (HR then Finance) still sign, so SoD holds.
"""
from django.db import migrations


def advance_cfo_self_leg(apps, schema_editor):
    LeaveEncashment = apps.get_model('hris', 'LeaveEncashment')
    UserProfile = apps.get_model('core', 'UserProfile')

    note = (
        "[CFO leg skipped (backfill 0073) — the applicant is the CFO and cannot "
        "approve their own request (segregation of duties). Advanced to the HR "
        "leg; HR then Finance sign it. CFO directive 2026-08-24.]")

    stuck = LeaveEncashment.objects.filter(status='pending_cfo')
    for enc in stuck:
        applicant = enc.applicant
        if applicant is None:
            continue
        is_cfo = bool(getattr(applicant, 'is_superuser', False))
        if not is_cfo:
            prof = UserProfile.objects.filter(user=applicant, is_active=True).first()
            # Title.CFO stored value is 'cfo' (see core UserProfile.Title).
            is_cfo = bool(prof and getattr(prof, 'title', '') == 'cfo')
        if not is_cfo:
            continue
        enc.status = 'pending_hr'
        enc.decision_notes = (f"{enc.decision_notes}\n{note}".strip()
                              if enc.decision_notes else note)
        enc.save(update_fields=['status', 'decision_notes', 'updated_at'])


def noop_reverse(apps, schema_editor):
    # Not reversible in a meaningful way — an advanced request may already have
    # been signed by HR. Leave it where it is.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0072_merge_entitlement_decimal'),
    ]

    operations = [
        migrations.RunPython(advance_cfo_self_leg, noop_reverse),
    ]
