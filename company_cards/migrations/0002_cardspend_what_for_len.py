from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen CardSpend.what_for 200 -> 1000 so a real 25-word explanation fits
    (CFO 2026-09-05). Additive, no data change."""

    dependencies = [
        ('company_cards', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cardspend',
            name='what_for',
            field=models.CharField(
                help_text='A few words — at least 25 — on what it was for. '
                          'Finance codes the account from this.',
                max_length=1000,
            ),
        ),
    ]
