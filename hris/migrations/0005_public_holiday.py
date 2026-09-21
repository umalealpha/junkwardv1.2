"""Public holidays for leave-day calculation (structural audit 2026-05-24).

Seeds Botswana 2025 + 2026 calendar so LeaveRequest.compute_days() can
skip weekends + holidays. Source: Botswana Public Holidays Act + cabinet
proclamations published in the Botswana Government Gazette.
"""

from __future__ import annotations

import uuid

from django.db import migrations, models


BW_HOLIDAYS_2025 = [
    ('2025-01-01', "New Year's Day"),
    ('2025-01-02', "Public Holiday"),
    ('2025-04-18', "Good Friday"),
    ('2025-04-19', "Holy Saturday"),
    ('2025-04-21', "Easter Monday"),
    ('2025-05-01', "Labour Day"),
    ('2025-05-29', "Ascension Day"),
    ('2025-07-01', "Sir Seretse Khama Day"),
    ('2025-07-21', "President's Day"),
    ('2025-07-22', "Public Holiday (President's Day)"),
    ('2025-09-30', "Botswana Day"),
    ('2025-10-01', "Public Holiday (Botswana Day)"),
    ('2025-12-25', "Christmas Day"),
    ('2025-12-26', "Boxing Day"),
]

BW_HOLIDAYS_2026 = [
    ('2026-01-01', "New Year's Day"),
    ('2026-01-02', "Public Holiday"),
    ('2026-04-03', "Good Friday"),
    ('2026-04-04', "Holy Saturday"),
    ('2026-04-06', "Easter Monday"),
    ('2026-05-01', "Labour Day"),
    ('2026-05-14', "Ascension Day"),
    ('2026-07-01', "Sir Seretse Khama Day"),
    ('2026-07-20', "President's Day"),
    ('2026-07-21', "Public Holiday (President's Day)"),
    ('2026-09-30', "Botswana Day"),
    ('2026-10-01', "Public Holiday (Botswana Day)"),
    ('2026-12-25', "Christmas Day"),
    ('2026-12-26', "Boxing Day"),
]


def seed_holidays(apps, schema_editor):
    PublicHoliday = apps.get_model('hris', 'PublicHoliday')
    from datetime import date
    rows = []
    for d, name in BW_HOLIDAYS_2025 + BW_HOLIDAYS_2026:
        y, m, dd = (int(x) for x in d.split('-'))
        rows.append(PublicHoliday(
            id=uuid.uuid4(),
            country_code='BW',
            holiday_date=date(y, m, dd),
            name=name,
            is_active=True,
        ))
    PublicHoliday.objects.bulk_create(rows, ignore_conflicts=True)


def unseed_holidays(apps, schema_editor):
    PublicHoliday = apps.get_model('hris', 'PublicHoliday')
    PublicHoliday.objects.filter(country_code='BW').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0004_onboarding'),
    ]

    operations = [
        migrations.CreateModel(
            name='PublicHoliday',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('country_code', models.CharField(db_index=True, default='BW',
                                                  max_length=2)),
                ('holiday_date', models.DateField(db_index=True)),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Public Holiday',
                'verbose_name_plural': 'Public Holidays',
                'ordering': ['holiday_date'],
                'unique_together': {('country_code', 'holiday_date')},
                'abstract': False,
            },
        ),
        migrations.RunPython(seed_holidays, unseed_holidays),
    ]
