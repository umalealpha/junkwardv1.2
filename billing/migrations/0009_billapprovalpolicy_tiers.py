"""
billing 0009 — PAY-001 three-tier approval thresholds on BillApprovalPolicy.

CFO directive 2026-05-27. Adds configurable tier1/tier2 ceilings (nullable
PLACEHOLDERS for the CFO to set in Settings) and the per-tier approver-role
labels. Legacy auto_approve_ceiling / manager_ceiling are left intact.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_payment_terms'),
    ]

    operations = [
        migrations.AddField(
            model_name='billapprovalpolicy',
            name='tier1_ceiling',
            field=models.DecimalField(
                max_digits=18, decimal_places=2, null=True, blank=True,
                help_text='PLACEHOLDER — CFO to set. Bills at/below this BWP '
                          'amount → Tier 1 (Finance Manager).',
            ),
        ),
        migrations.AddField(
            model_name='billapprovalpolicy',
            name='tier2_ceiling',
            field=models.DecimalField(
                max_digits=18, decimal_places=2, null=True, blank=True,
                help_text='PLACEHOLDER — CFO to set. Above tier1 and at/below '
                          'this → Tier 2 (Head of Finance). Above this → Tier 3.',
            ),
        ),
        migrations.AddField(
            model_name='billapprovalpolicy',
            name='tier1_role',
            field=models.CharField(
                max_length=40, default='finance_manager',
                help_text='Title that approves Tier 1 bills.',
            ),
        ),
        migrations.AddField(
            model_name='billapprovalpolicy',
            name='tier2_role',
            field=models.CharField(
                max_length=40, default='head_of_finance',
                help_text='Title that approves Tier 2 bills.',
            ),
        ),
        migrations.AddField(
            model_name='billapprovalpolicy',
            name='tier3_role',
            field=models.CharField(
                max_length=40, default='cfo',
                help_text='Title that approves Tier 3 bills (top tier).',
            ),
        ),
    ]
