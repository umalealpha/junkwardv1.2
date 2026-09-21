"""FrozenFigure — CFO-locked headline figures with drift detection.

Created 2026-05-18 per handover § P1.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0013_account_cfo_classification'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FrozenFigure',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period', models.CharField(
                    max_length=20,
                    help_text="Free-text period code, e.g. 'FY25_Jun2025', 'FY26_Mar2026'.",
                )),
                ('line_label', models.CharField(
                    max_length=50,
                    help_text="Free-text tile label, e.g. 'GWP', 'PAT', 'Total Assets', 'Cash & Bank'.",
                )),
                ('value_bwp', models.DecimalField(max_digits=18, decimal_places=2)),
                ('tolerance_pct', models.DecimalField(
                    max_digits=5, decimal_places=2,
                    default=Decimal('1.00'),
                    help_text='Allowed deviation as %; drift > tolerance triggers warning.',
                )),
                ('locked_at', models.DateTimeField(auto_now_add=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('is_active', models.BooleanField(default=True)),
                ('acknowledged_by_override', models.BooleanField(default=False)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('locked_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='frozen_figures_locked',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('acknowledged_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='frozen_figures_ack',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Frozen Figure',
                'verbose_name_plural': 'Frozen Figures',
                'ordering': ['period', 'line_label'],
            },
        ),
        migrations.AddConstraint(
            model_name='frozenfigure',
            constraint=models.UniqueConstraint(
                fields=('period', 'line_label'),
                name='uniq_frozen_figure_period_label',
            ),
        ),
    ]
