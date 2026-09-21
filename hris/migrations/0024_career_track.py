"""Career Tracks — promotion-readiness records (CFO directive 2026-07-13).

Creates CareerTrack + CareerMilestone and seeds the first record:
M. Tlagae (Health BU secondment) → Projects Manager, gated on the three
Healthcare milestones set by the CFO on 2026-07-13. The seed is a graceful
no-op if her employee record (matched by work email, then by surname) is
absent, so test databases and fresh installs migrate cleanly.
"""
from __future__ import annotations

import uuid

import django.db.models.deletion
from django.db import migrations, models


TLAGAE_EMAIL = 'mtlagae@alphadirect.co.bw'
CREATED_BY = 'Prathap Ganesharajah (CFO)'

CONTEXT = (
    'Secondment to the Health Business Unit; currently Senior Associate '
    'leading the Health operations team, Projects Manager position vacant. '
    'Recorded contributions (Employer Group Progress Dashboard / ExCo '
    'Dashboard 02-Jul-2026): employer groups grown from 1 to 6 fully '
    'onboarded plus 12 signed MOUs (operations team executed 9 of the '
    'onboardings); provider network at 791 in onboarding (365 practices, '
    '244 fully registered); Service Provider Portal live off Graphite; '
    'Pharmacy EDI established; tariff pricing and quotation engine set up; '
    'benefit-utilisation SMS confirmations live; Health marketing delivered '
    '(website, LinkedIn campaign, billboards). Pursuing a Master\'s degree. '
    'CFO directive 2026-07-13: promotion review for the Projects Manager '
    'role is tied to the milestones below.'
)

MILESTONES = [
    'Achieve the Healthcare budgets.',
    'Bring on board all the large hospitals into the Health provider '
    'network.',
    'Implement the BWP100 Continental Re mass-population healthcare '
    'product.',
]


def seed_tlagae(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    CareerTrack = apps.get_model('hris', 'CareerTrack')
    CareerMilestone = apps.get_model('hris', 'CareerMilestone')

    emp = Employee.objects.filter(email__iexact=TLAGAE_EMAIL).first()
    if emp is None:
        emp = (Employee.objects
               .filter(full_name__icontains='tlagae')
               .order_by('employee_number')
               .first())
    if emp is None:
        return  # no matching employee on this database — nothing to seed
    if CareerTrack.objects.filter(
            employee=emp, target_role='Projects Manager').exists():
        return  # idempotent

    track = CareerTrack.objects.create(
        id=uuid.uuid4(),
        employee=emp,
        target_role='Projects Manager',
        status='active',
        context=CONTEXT,
        created_by_name=CREATED_BY,
    )
    for i, desc in enumerate(MILESTONES, start=1):
        CareerMilestone.objects.create(
            id=uuid.uuid4(),
            track=track,
            order=i,
            description=desc,
            status='pending',
        )


def unseed_tlagae(apps, schema_editor):
    CareerTrack = apps.get_model('hris', 'CareerTrack')
    CareerTrack.objects.filter(
        target_role='Projects Manager', created_by_name=CREATED_BY).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0023_expenseclaim_gl_account'),
        ('payroll', '0010_employmentcontract_type_probation'),
    ]

    operations = [
        migrations.CreateModel(
            name='CareerTrack',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('target_role', models.CharField(max_length=120)),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('achieved', 'Achieved'),
                             ('on_hold', 'On hold')],
                    default='active', max_length=12)),
                ('context', models.TextField(
                    blank=True, default='',
                    help_text='Background: current role, achievements to '
                              'date, and why this path was opened.')),
                ('created_by_name', models.CharField(
                    blank=True, default='', max_length=120)),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='career_tracks', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Career Track',
                'verbose_name_plural': 'Career Tracks',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CareerMilestone',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveSmallIntegerField(default=1)),
                ('description', models.TextField()),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'),
                             ('in_progress', 'In progress'),
                             ('done', 'Done')],
                    default='pending', max_length=12)),
                ('evidence', models.TextField(blank=True, default='')),
                ('track', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='milestones', to='hris.careertrack')),
            ],
            options={
                'ordering': ['order', 'created_at'],
                'abstract': False,
            },
        ),
        migrations.RunPython(seed_tlagae, unseed_tlagae),
    ]
