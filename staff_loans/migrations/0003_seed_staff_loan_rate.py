"""Seed the opening staff-loan rate so new loans have a rate before the first
monthly cron run: Bank of Botswana MoPR 5.5% + spread 10% = 15.5% p.a."""

from decimal import Decimal
from datetime import date

from django.db import migrations


def seed_rate(apps, schema_editor):
    StaffLoanRate = apps.get_model('staff_loans', 'StaffLoanRate')
    if StaffLoanRate.objects.exists():
        return
    StaffLoanRate.objects.create(
        effective_from=date(2026, 7, 1),
        annual_rate_pct=Decimal('15.5'),
        reference_name='Bank of Botswana Monetary Policy Rate',
        reference_rate_pct=Decimal('5.5'),
        spread_pct=Decimal('10.0'),
        source='seed',
        source_url='https://www.bankofbotswana.bw/content/interest-rates',
        note='Opening rate: MoPR 5.5% + spread 10%. Cron refreshes monthly.',
    )


def unseed(apps, schema_editor):
    StaffLoanRate = apps.get_model('staff_loans', 'StaffLoanRate')
    StaffLoanRate.objects.filter(source='seed').delete()


class Migration(migrations.Migration):
    dependencies = [('staff_loans', '0002_staffloanrate')]
    operations = [migrations.RunPython(seed_rate, unseed)]
