"""
Seed the Time Doctor Deduction leave type (Unami Butale, HR, 2026-08-25).

Staff whose tracked hours fall short of expected are deducted, and must apply
via Omni stating this as the reason — so the shortfall is a recorded, applied
absence rather than a silent payroll adjustment. This is its own leave category,
sitting beside annual and sick.

Runs on `migrate`, so it is live on deploy without a manual seed command.

The staff card + apply form come from the frontend LEAVE_TYPE_ORDER and the
backend DEFAULT_LEAVE_RULES (both changed in this batch) — NOT from this table, so
a future session adding a leave type must touch those too. This seeded DB row
exists so that apply_leave's get_or_create REUSES this unpaid row rather than
auto-creating a paid one (the model default is paid_pct=100); HR can later adjust
it through the DB-wins paid_pct override. update_or_create on the code; a re-run
re-applies the same values.
"""
from django.db import migrations


def seed(apps, schema_editor):
    LeaveType = apps.get_model('hris', 'LeaveType')
    LeaveType.objects.update_or_create(
        code='td_deduct',
        defaults={
            'name': 'Time Doctor Deduction (unpaid - tracked-hours shortfall)',
            'default_annual_days': 0,
            'is_paid': False,
            'is_active': True,
            'carry_over_cap_days': 0,
            'max_carry_over_years': 0,
            'probation_months': 0,
            'requires_medical_cert': False,
            'paid_pct': 0,
        },
    )


def unseed(apps, schema_editor):
    # Deactivate rather than delete — a LeaveApplication may point at it, and a
    # hard delete would break the chain of who was deducted and why.
    LeaveType = apps.get_model('hris', 'LeaveType')
    LeaveType.objects.filter(code='td_deduct').update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0074_amendment_reversal_of'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
