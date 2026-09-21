import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('budgets', '0005_spend_actual_spent'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlanPack',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('entity', models.CharField(default='AD_INSURTECH',
                                            help_text='Matches the Omni entity filter.',
                                            max_length=40)),
                ('label', models.CharField(help_text='e.g. "FY2026–FY2030 · Base Case"',
                                           max_length=120)),
                ('scenario_slug', models.CharField(
                    choices=[('base', 'Base case'), ('conservative', 'Conservative'),
                             ('aggressive', 'Aggressive')],
                    default='base', max_length=20)),
                ('status', models.CharField(
                    choices=[('draft', 'Draft'), ('approved', 'Approved (board-adopted)'),
                             ('archived', 'Archived (superseded)')],
                    default='draft', max_length=12)),
                ('prepared_by', models.CharField(blank=True, default='', max_length=120)),
                ('prepared_date', models.DateField(blank=True, null=True)),
                ('department', models.CharField(blank=True, default='CFO Office', max_length=120)),
                ('base_figures', models.JSONField(blank=True, default=dict)),
                ('assumptions', models.JSONField(blank=True, default=list)),
                ('narrative', models.TextField(blank=True, default='', max_length=600)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='plan_packs_approved', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='plan_packs_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Strategic plan pack',
                'verbose_name_plural': 'Strategic plan packs',
                'ordering': ['status', 'scenario_slug'],
                'abstract': False,
                'unique_together': {('entity', 'label', 'scenario_slug')},
            },
        ),
        migrations.CreateModel(
            name='PlanPackFile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to='strategic-plan/%Y/')),
                ('kind', models.CharField(
                    choices=[('workbook', 'Financial model (xlsx)'),
                             ('spec', 'Specification / narrative'),
                             ('deck', 'Board deck'), ('app', 'Interactive page / app'),
                             ('other', 'Other')],
                    default='other', max_length=12)),
                ('label', models.CharField(blank=True, default='', max_length=200)),
                ('pack', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='files', to='budgets.planpack')),
            ],
            options={'ordering': ['kind', 'label'], 'abstract': False},
        ),
    ]
