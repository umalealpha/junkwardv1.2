"""
Seed the people who may change a statutory tax date.

CFO instruction 2026-09-11: "oprah should be able to change the dates also,
kago and legakwa as well."

This migration exists because `can_edit_dates` no longer accepts a job title as
a grant. It used to, and that hid a trap: all three of these people are Finance
Managers on prod, so they passed through the title gate while TaxCalendarEditor
sat empty — which meant removing someone's row would have taken nothing away,
silently. The list is now the only grant, so it has to actually contain them.

Matched on EMAIL, never on name. There is a second Pako in the directory (Ditso
Pako Motlhabane) and a Pako Mampane on another domain; a name match here would
be a coin flip on a permission.

Idempotent and tolerant: a missing account is skipped rather than raising, so
this cannot block a deploy if someone has been offboarded.
"""
from django.db import migrations

EDITORS = [
    ('omogomotsi@alphadirect.co.bw',  'Raised the requirement; follows the 2026 Acts'),
    ('ktshutlhedi@alphadirect.co.bw', 'Prepares PAYE and OWHT'),
    ('lntabeni@alphadirect.co.bw',    'Finance'),
]


def seed(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    TaxCalendarEditor = apps.get_model('regulatory', 'TaxCalendarEditor')

    for email, note in EDITORS:
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            continue
        TaxCalendarEditor.objects.get_or_create(
            user=user, defaults={'note': note},
        )


def unseed(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    TaxCalendarEditor = apps.get_model('regulatory', 'TaxCalendarEditor')
    ids = list(
        User.objects
        .filter(email__in=[e for e, _ in EDITORS])
        .values_list('id', flat=True)
    )
    TaxCalendarEditor.objects.filter(user_id__in=ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('regulatory', '0005_taxcompliancetask_date_change_reason_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
