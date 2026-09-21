import uuid
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0037_omnitask_performance_points'),
    ]

    operations = [
        migrations.CreateModel(
            name='Lease',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(help_text='e.g. "BIH ICON Building"', max_length=160)),
                ('property_ref', models.CharField(blank=True, default='', help_text='LOI clause / property code / lessor', max_length=160)),
                ('commencement_date', models.DateField()),
                ('term_months', models.PositiveIntegerField(default=60)),
                ('monthly_payment', models.DecimalField(decimal_places=2, help_text='Base monthly payment, excl. VAT', max_digits=14)),
                ('escalation_pct', models.DecimalField(decimal_places=3, default=Decimal('0'), help_text='Annual escalation %, compounded on the anniversary', max_digits=6)),
                ('discount_rate_pct', models.DecimalField(decimal_places=3, default=Decimal('7'), help_text='Incremental borrowing rate, % p.a.', max_digits=6)),
                ('fye_month', models.PositiveSmallIntegerField(default=6, help_text='Financial year-end month (6 = 30 June)')),
                ('payment_timing', models.CharField(choices=[('arrears', 'In arrears (month-end)'), ('advance', 'In advance (month-start)')], default='arrears', max_length=8)),
                ('incentives', models.DecimalField(decimal_places=2, default=Decimal('0'), help_text='Lease incentives received (reduces ROU)', max_digits=14)),
                ('initial_direct_costs', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('prepaid', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('dismantle', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('notes', models.TextField(blank=True, default='')),
                ('company', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leases', to='core.company')),
            ],
            options={
                'verbose_name': 'IFRS 16 Lease',
                'verbose_name_plural': 'IFRS 16 Leases',
                'ordering': ['name'],
                'abstract': False,
            },
        ),
    ]
