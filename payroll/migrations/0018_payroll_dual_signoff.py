"""Dual payroll sign-off (CFO directive 2026-07-28).

A company's month closes when BOTH an HR signer (Unami/Dorothy) and a Finance
signer (Kago/Pako) have signed. Adds the two signature legs and relaxes the old
single submitted_by/at (a row is now opened by whoever signs first).
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0017_payroll_signoff'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollsignoff',
            name='hr_signed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payroll_sign_offs_hr',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='payrollsignoff',
            name='hr_signed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payrollsignoff',
            name='fin_signed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payroll_sign_offs_fin',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='payrollsignoff',
            name='fin_signed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='payrollsignoff',
            name='submitted_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payroll_sign_offs_submitted',
                to=settings.AUTH_USER_MODEL,
                help_text='Who first brought this month forward '
                          '(the earlier of the two signers).'),
        ),
        migrations.AlterField(
            model_name='payrollsignoff',
            name='submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
