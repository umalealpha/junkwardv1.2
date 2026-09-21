"""Carrying value + intake JE link on SalvageItem (CFO directive 2026-05-18)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salvage', '0002_salvage_portal_phase2'),
        ('ledger',  '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='salvageitem',
            name='cost_basis',
            field=models.DecimalField(
                max_digits=18, decimal_places=2, default=0,
                help_text='Carrying value on BS. Released on sale; gain/loss to P&L.',
            ),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='intake_posted_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='intake_journal_entry',
            field=models.ForeignKey(
                'ledger.JournalEntry', on_delete=models.SET_NULL,
                null=True, blank=True, related_name='+',
                help_text='Auto-posted JE when item was first taken into inventory.',
            ),
        ),
    ]
